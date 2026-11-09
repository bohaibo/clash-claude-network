"""Loopback-only GUI server for the Windows Claude network project.

The server is intentionally not elevated. Actions that need administrator rights
are delegated to `run.ps1`, which keeps its existing UAC flow and writes JSONL
progress events that this server reads back. The GUI itself and the isolated
browser profiles run at the normal user integrity level.
"""
from __future__ import annotations

import argparse
import ctypes
import datetime
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from contextlib import contextmanager
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import gui_settings
import node_region
import region_profile
import sandbox_browser
from operation_lock import operation_lock, pending_settings
from atomic_files import replace_file

PROJECT = Path(__file__).resolve().parent.parent
LOCAL = PROJECT / '.local'
GUI_DIR = PROJECT / 'gui'
JOBS_DIR = LOCAL / 'gui' / 'jobs'
SESSION_FILE = LOCAL / 'gui' / 'session.json'
SERVER_LOG = LOCAL / 'gui' / 'server.log'
VERSION = '2.0'
COOKIE = 'ccn_session'
JOB_ID_RE = re.compile(r'^[a-f0-9]{32}$')
SNAPSHOT_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$')
REGION_RE = re.compile(r'^[A-Z]{2}$')
TIME_RE = re.compile(r'^(?:[01]\d|2[0-3]):[0-5]\d$')
CONFIRM = {
    'apply': 'APPLY',
    'supplement': 'SUPPLEMENT',
    'snapshot': 'SNAPSHOT',
    'reconfigure': 'RECONFIGURE',
    'update': 'UPDATE',
    'verify': 'VERIFY',
}
ADMIN_ACTIONS = {'apply', 'supplement', 'snapshot', 'restore', 'reconfigure'}
RUN_ACTIONS = {'apply', 'supplement', 'snapshot', 'restore', 'reconfigure'}
LOCAL_ACTIONS = {
    'browser-open': 'open', 'browser-check': 'check', 'browser-close': 'close',
    'browser-reset': 'reset', 'browser-register': 'register-default', 'settings-recover': None,
}
PUBLIC_FILES = {'sandbox-check.html', 'sandbox-check.js', 'sandbox-check.css', 'favicon.svg'}
MIME = {
    '.html': 'text/html; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.js': 'text/javascript; charset=utf-8',
    '.svg': 'image/svg+xml',
    '.png': 'image/png',
    '.ico': 'image/x-icon',
}


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def now_iso():
    return datetime.datetime.now().astimezone().isoformat(timespec='seconds')


def read_json(path, default=None):
    path = Path(path)
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return default


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.new')
    with temporary.open('w', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2))
        stream.flush()
        os.fsync(stream.fileno())
    replace_file(temporary, path)


def append_event(path, event):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({'time': now_iso(), **event}, ensure_ascii=False)
    with path.open('a', encoding='utf-8') as stream:
        stream.write(line + '\n')


def read_events(path, limit=400):
    path = Path(path)
    if not path.is_file():
        return []
    try:
        text = tail_text(path, limit=800000, lines=max(limit * 4, 1600))
    except OSError:
        return []
    events = []
    for line in text.splitlines():
        line = line.strip().lstrip('\ufeff')
        if not line:
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events[-limit:]


def tail_text(path, limit=24000, lines=140):
    path = Path(path)
    if not path.is_file():
        return ''
    try:
        with path.open('rb') as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - (limit * 4 + 4)))
            data = stream.read(limit * 4 + 4).decode('utf-8', errors='replace')
    except OSError:
        return ''
    values = data.splitlines()[-lines:]
    text = '\n'.join(values)
    return text[-limit:]


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def mask_ip(value):
    if not isinstance(value, str):
        return ''
    if re.fullmatch(r'\d{1,3}(?:\.\d{1,3}){3}', value):
        parts = value.split('.')
        return parts[0] + '.' + parts[1] + '.***.***'
    return '***'


def parse_time(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.datetime.fromisoformat(value)
    except ValueError:
        return None


def python_console():
    executable = Path(sys.executable)
    if executable.name.lower() == 'pythonw.exe':
        candidate = executable.with_name('python.exe')
        if candidate.is_file():
            return str(candidate)
    return str(executable)


def _no_window():
    return getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def _run_powershell(script, arguments, timeout=25):
    command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(script)]
    return subprocess.run(
        command + list(arguments),
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        timeout=timeout, creationflags=_no_window(),
    )


def _last_json_line(text):
    for line in reversed((text or '').splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


class App:
    def __init__(self, *, local=None, gui_dir=None, project=None, open_browser=True, idle_exit=True):
        self.local = Path(local) if local else LOCAL
        self.gui_dir = Path(gui_dir) if gui_dir else GUI_DIR
        self.project = Path(project) if project else PROJECT
        self.scripts = self.project / 'scripts'
        self.jobs_dir = self.local / 'gui' / 'jobs'
        self.session_file = self.local / 'gui' / 'session.json'
        self.server_log = self.local / 'gui' / 'server.log'
        self.open_browser = open_browser
        self.idle_exit = idle_exit
        self.token = secrets.token_urlsafe(32)
        self.session_id = secrets.token_urlsafe(24)
        self.httpd = None
        self.base_url = ''
        self.started = time.monotonic()
        self.last_heartbeat = time.monotonic()
        self.last_browser_seen = 0.0
        self.browser_opened = False
        self.frontend_ready = False
        self.browser_error = ''
        self.browser_fallback = False
        self.stopping = False
        self.active_job = None
        self.jobs = {}
        self._lock = threading.RLock()
        self._background = {}
        self._settings_busy = False
        self._shutdown_requested = False
        self._task_cache = (0.0, None)
        self._service_cache = (0.0, None)
        self._nodes_cache = (0.0, None)
        self.env_state = read_json(self.local / 'env.json', {})
        if not isinstance(self.env_state, dict):
            self.env_state = {}

    def node_path(self):
        value = self.env_state.get('node') if isinstance(self.env_state, dict) else None
        if isinstance(value, str) and Path(value).is_file():
            return value
        local = self.project / '.runtime' / 'node' / 'node.exe'
        if local.is_file():
            return str(local)
        return shutil.which('node')

    # ---- logging -------------------------------------------------------
    def log(self, message):
        try:
            self.server_log.parent.mkdir(parents=True, exist_ok=True)
            if self.server_log.exists() and self.server_log.stat().st_size > 1_000_000:
                self.server_log.write_text('', encoding='utf-8')
            with self.server_log.open('a', encoding='utf-8') as stream:
                stream.write(time.strftime('[%Y-%m-%d %H:%M:%S] ') + str(message).replace('\n', ' ') + '\n')
        except OSError:
            pass

    # ---- small reads ---------------------------------------------------
    def plan(self):
        value = read_json(self.local / 'plan.json', {})
        return value if isinstance(value, dict) else {}

    def snapshots(self):
        root = self.local / 'snapshots'
        plan = self.plan()
        associated = ''
        try:
            associated = str(Path(str(plan.get('snapshot', ''))).resolve())
        except (OSError, ValueError):
            associated = ''
        rows = []
        if not root.is_dir():
            return rows
        for folder in sorted(root.iterdir(), key=lambda p: p.name, reverse=True):
            if not folder.is_dir():
                continue
            manifest = read_json(folder / 'manifest.json')
            if not isinstance(manifest, dict):
                continue
            sha = manifest.get('sha256') if isinstance(manifest.get('sha256'), dict) else {}
            extra = manifest.get('extra_sha256') if isinstance(manifest.get('extra_sha256'), dict) else {}
            system_file = folder / 'system.json'
            committed = 'system.json' in sha or 'system.json' in extra
            kind = manifest.get('kind') or 'auto'
            rows.append({
                'name': folder.name,
                'time': str(manifest.get('time') or ''),
                'kind': 'manual' if kind == 'manual' else 'auto',
                'sealed': bool(system_file.is_file() and committed),
                'system': system_file.is_file(),
                'associated': bool(associated and str(folder.resolve()) == associated),
                'node': str(manifest.get('node') or ''),
            })
        return rows

    def pending_firewall(self):
        file = self.local / 'pending-firewall.json'
        if not file.is_file():
            return False
        value = read_json(file, {})
        token = value.get('token') if isinstance(value, dict) else None
        if not isinstance(token, str) or not re.fullmatch(r'guard-[a-f0-9]{32}', token):
            return True
        return not (self.local / (token + '.commit')).is_file()

    def baseline_name(self):
        value = read_json(self.local / 'baseline-selection.json', {})
        if isinstance(value, dict) and isinstance(value.get('snapshot'), str):
            target = self.local / 'snapshots' / value['snapshot']
            if (target / 'manifest.json').is_file():
                return value['snapshot']
        return ''

    def configured(self):
        root = self.plan().get('root')
        if not isinstance(root, str) or not root:
            return False, ''
        folder = Path(root) / 'profiles'
        if not folder.is_dir():
            return False, '找不到 Clash Verge Rev 的 profiles 目录。'
        try:
            for candidate in folder.iterdir():
                if candidate.suffix.lower() not in ('.js', '.yaml', '.yml') or not candidate.is_file():
                    continue
                try:
                    if candidate.stat().st_size > 4_000_000:
                        continue
                    if 'PORTABLE-CLAUDE-NETWORK-V1' in candidate.read_text(encoding='utf-8', errors='ignore'):
                        return True, ''
                except OSError:
                    continue
        except OSError as exc:
            return False, '读取 Clash 配置目录失败：' + type(exc).__name__
        return False, ''

    def task_status(self):
        script = self.scripts / 'update_task.ps1'
        result = {'installed': None, 'error': ''}
        try:
            completed = _run_powershell(script, ['-Action', 'status', '-Python', python_console()], timeout=20)
            parsed = _last_json_line(completed.stdout)
            if parsed is None:
                result['error'] = '无法读取规则更新任务状态。'
            else:
                result = parsed
        except (OSError, subprocess.SubprocessError) as exc:
            result['error'] = '读取规则更新任务失败：' + type(exc).__name__
        return result

    def service_status(self):
        command = (
            "$ErrorActionPreference='SilentlyContinue';"
            "$s=Get-Service -Name clash_verge_service;"
            "$p=@(Get-Process -Name clash-verge -ErrorAction SilentlyContinue).Count;"
            "[ordered]@{service=if($s){$s.Status.ToString()}else{'NotInstalled'};app_running=($p -gt 0)} | ConvertTo-Json -Compress"
        )
        result = {'service': 'Unknown', 'app_running': False}
        try:
            completed = subprocess.run(
                ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                timeout=15, creationflags=_no_window(),
            )
            parsed = _last_json_line(completed.stdout)
            if parsed:
                result = parsed
        except (OSError, subprocess.SubprocessError):
            pass
        return result

    def update_summary(self):
        try:
            settings = gui_settings.load(self.local)
        except (ValueError, OSError) as exc:
            settings = {'version': 1, 'update': {'mode': 'scheduled', 'time': '13:00'}}
            settings['error'] = str(exc)
        task = self.task_status()
        last = read_json(self.local / 'update-result.json', {})
        if not isinstance(last, dict):
            last = {}
        if last.get('note') == 'Review .local reports; invalid source changes are not published.' and not last.get('reason'):
            last['reason'] = '上次规则更新失败；旧版记录未保存具体失败原因，请重新更新以获取详细报告。'
        clean_last = {
            'success': bool(last.get('success')),
            'skipped': bool(last.get('skipped')),
            'reason': str(last.get('reason') or last.get('note') or ''),
            'error': str(last.get('error') or ''),
            'time': str(last.get('time') or ''),
        }
        stale = True
        moment = parse_time(clean_last.get('time'))
        if clean_last.get('success') and moment is not None:
            stale = (datetime.datetime.now().astimezone() - moment).total_seconds() > 24 * 3600
        elif not clean_last.get('time'):
            stale = True
        else:
            stale = True
        return {
            'settings': settings,
            'task': task,
            'last': clean_last,
            'stale': stale,
            'mode': settings.get('update', {}).get('mode', 'scheduled'),
            'time': settings.get('update', {}).get('time', '13:00'),
            'days': settings.get('update', {}).get('days', 1),
        }

    def verification_summary(self):
        report = read_json(self.local / 'verification.json', {})
        if not isinstance(report, dict) or not report:
            return {'available': False}
        stages = report.get('stages') if isinstance(report.get('stages'), dict) else {}
        stage_rows = []
        for name, title in (('node-outage', '节点故障保护'), ('core-exit', '停核保护'), ('post-recovery', '恢复后复测')):
            entry = stages.get(name) if isinstance(stages.get(name), dict) else {}
            stage_rows.append({'id': name, 'title': title, 'tested': bool(entry), 'success': bool(entry.get('success'))})
        egress = report.get('egress') if isinstance(report.get('egress'), dict) else {}
        checks = []
        checks.append({'title': '内核运行模式', 'status': 'passed' if report.get('runtime_mode') == 'rule' else 'unknown'})
        checks.append({'title': '国内网站', 'status': 'passed' if report.get('domestic') else 'unknown'})
        checks.append({'title': '海外出口地区', 'status': 'passed' if egress.get('country') else 'unknown'})
        dns = report.get('dns') if isinstance(report.get('dns'), list) else []
        dns_status = 'passed' if dns and all(x.get('passed') for x in dns) else 'failed' if dns else 'unknown'
        checks.append({'title': 'DNS A/64/65', 'status': dns_status})
        checks.append({'title': 'TUN DNS 接管', 'status': 'passed' if report.get('fake_ip_hijack') else 'unknown'})
        checks.append({'title': 'IPv6 公网阻断', 'status': 'passed' if report.get('ipv6_public_blocked') else 'unknown'})
        for key, title in (('git', 'Git/GitHub'), ('npm', 'npm'), ('stun', 'UDP STUN')):
            value = report.get(key) if isinstance(report.get(key), dict) else {}
            status = value.get('status')
            if status == 'not-tested':
                status = 'untested'
            checks.append({'title': title, 'status': status or 'unknown'})
        udp = report.get('udp_egress') if isinstance(report.get('udp_egress'), dict) else {}
        checks.append({'title': 'UDP 出口与节点路由', 'status': 'passed' if udp.get('verified') else 'failed' if udp else 'unknown'})
        return {
            'available': True,
            'success': bool(report.get('success')),
            'time': str(report.get('time') or ''),
            'run_id': str(report.get('run_id') or ''),
            'egress': {'country': str(egress.get('country') or ''), 'city': str(egress.get('city') or '')},
            'dns_failed': dns_status == 'failed',
            'dns_status': dns_status,
            'stages': stage_rows,
            'checks': checks,
            'not_tested': [str(x) for x in report.get('not_tested', []) if isinstance(x, str)],
            'limitations': [
                '未测试浏览器 WebRTC、浏览器指纹、Claude 网页与账号可用性。',
                '未测试 Docker 容器、WSL 和虚拟机内部出口。',
                '通过检测只代表当时观测到的结果，不保证未来所有应用或线路。',
            ],
        }

    def reconfigure_runs(self):
        root = self.local / 'reconfigure-runs'
        rows = []
        if not root.is_dir():
            return rows
        for folder in sorted(root.iterdir(), key=lambda p: p.name, reverse=True)[:8]:
            if not folder.is_dir():
                continue
            status = read_json(folder / 'status.json', {})
            if not isinstance(status, dict):
                continue
            rows.append({
                'id': folder.name[:8],
                'stage': str(status.get('stage') or ''),
                'success': bool(status.get('success')),
                'error': str(status.get('error') or ''),
            'record_warning': status.get('record_warning') or (job.get('monitor_warning') if status.get('state') == 'running' else ''),
                'time': datetime.datetime.fromtimestamp(folder.stat().st_mtime).astimezone().isoformat(timespec='seconds'),
                'recovered': (folder / 'recovery-result.json').is_file(),
            })
        return rows

    def configuration_preview(self):
        cached = getattr(self, '_preview_cache', None)
        if cached and time.monotonic() - cached[0] < 30:
            return cached[1]
        current = {}
        try:
            result = subprocess.run(
                ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                 str(self.scripts / 'preview_state.ps1')], capture_output=True,
                timeout=20, creationflags=_no_window())
            if result.returncode == 0:
                current = json.loads(result.stdout.decode('utf-8-sig', errors='replace'))
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        import preview_config
        try:
            current['winhttp'] = preview_config.winhttp()
        except (OSError, AttributeError):
            current['winhttp'] = '读取失败（未验证）'
        profile = sandbox_browser.profile_path(self.local)
        preferences = read_json(profile / 'Default' / 'Preferences', {})
        current['browser_language'] = (preferences.get('intl', {}).get('accept_languages') or '独立资料尚未设置语言（设置页默认值不代表浏览器当前值）')
        try:
            import webrtc_system
            rtc = webrtc_system.status()
            current['webrtc_protection'] = rtc['state']
            current['webrtc_browsers'] = rtc['browsers']
        except (OSError, ValueError):
            current['webrtc_protection'] = '未能确认'

        # Read only summaries; never serialize node definitions or proxy URLs.
        try:
            import client
            root = client.root_path()
            config = client.load(root / 'clash-verge.yaml') or {}
            import preview_config
            current.update(preview_config.network(config))
        except (OSError, ValueError, RuntimeError, TypeError):
            pass
        for name, relative in [('git', '.gitconfig'), ('npm', '.npmrc'), ('docker', '.docker/config.json')]:
            try:
                import preview_config
                current[name] = preview_config.app_proxy(name, Path.home() / relative)
            except (OSError, ValueError, TypeError):
                current[name] = '未能读取（未验证）'
        value = {'current': current, 'regions': region_profile.PROFILES,
                 'read_at': now_iso()}
        self._preview_cache = (time.monotonic(), value)
        return value

    def background_value(self, name, loader, default, ttl=30):
        with self._lock:
            entry = self._background.setdefault(name, {'value': default, 'at': 0, 'running': False,
                'generation': 0, 'read_at': '', 'error': ''})
            if not entry['running'] and time.monotonic() - entry['at'] >= ttl:
                entry['running'] = True
                generation = entry['generation']
                def collect():
                    try:
                        value = loader()
                        issue = self.state_issue(name, value)
                    except Exception as exc:
                        self.log('state collection failed: ' + name + ': ' + type(exc).__name__)
                        with self._lock:
                            if entry['generation'] == generation:
                                entry['error'] = '读取失败（' + type(exc).__name__ + '），显示上次结果，未验证当前状态。'
                    else:
                        with self._lock:
                            if entry['generation'] == generation:
                                if issue:
                                    entry['error'] = issue + '显示上次结果，未验证当前状态。'
                                else:
                                    entry['value'] = value
                                    entry['read_at'] = now_iso()
                                    entry['error'] = ''
                    finally:
                        with self._lock:
                            if entry['generation'] == generation:
                                entry['at'] = time.monotonic()
                            elif name == 'preview':
                                self._preview_cache = None
                            elif name == 'update':
                                self._task_cache = (0.0, None)
                            elif name == 'service':
                                self._service_cache = (0.0, None)
                            entry['running'] = False
                threading.Thread(target=collect, daemon=True, name='ccn-state-' + name).start()
            return entry['value']

    def invalidate_background(self, *names):
        with self._lock:
            for name in names or tuple(self._background):
                entry = self._background.get(name)
                if entry is not None:
                    entry['generation'] += 1
                    entry['at'] = 0

    def publish_background(self, name, value):
        with self._lock:
            entry = self._background.setdefault(name, {'running': False, 'generation': 0})
            issue = self.state_issue(name, value)
            entry.update(value=value, generation=entry['generation'] + 1,
                         at=time.monotonic(), read_at=entry.get('read_at', '') if issue else now_iso(), error=issue)

    @staticmethod
    def state_issue(name, value):
        if not isinstance(value, dict):
            return '状态读取失败，当前值未验证。'
        if name == 'service' and value.get('service') == 'Unknown':
            return '无法读取 Clash 服务状态。'
        if name == 'update' and value.get('task', {}).get('error'):
            return '计划任务状态读取失败。'
        if name == 'update' and value.get('settings', {}).get('error'):
            return '更新设置读取失败。'
        return '状态读取失败，当前值未验证。' if value.get('error') else ''

    def collection_status(self):
        with self._lock:
            return {name: {'read_at': entry['read_at'], 'refreshing': entry['running'],
                           'error': entry['error'],
                           'stale': not entry['read_at'] or bool(entry['error']) or
                                    time.monotonic() - entry['at'] >= 30}
                    for name, entry in self._background.items()}

    def browser_summary(self):
        browser = sandbox_browser.status(self.local)
        browser['default'] = sandbox_browser.default_status(self.local)
        return browser

    def app_state(self):
        plan = self.plan()
        configured, configured_error = self.configured()
        root = plan.get('root') if isinstance(plan.get('root'), str) else ''
        snapshot_name = ''
        snapshot_ok = False
        if plan.get('snapshot'):
            try:
                target = Path(str(plan['snapshot']))
                snapshot_name = target.name
                snapshot_ok = (target / 'manifest.json').is_file()
            except (OSError, ValueError):
                snapshot_name = ''
        plan_view = {
            'exists': bool(plan),
            'node': str(plan.get('node') or ''),
            'subscription': str(plan.get('subscription_uid') or ''),
            'region': str(plan.get('region') or 'SG'),
            'snapshot': snapshot_name,
            'snapshot_ok': snapshot_ok,
            'core_program': str(plan.get('core_program') or ''),
        }
        browser = self.background_value('browser', self.browser_summary,
            {'running': False, 'settings': {}, 'engine': {}, 'profile': {}, 'default': {},
             'error': '正在读取浏览器状态（未验证）'}, ttl=15)
        active = self.active_job_view()
        last_run = read_json(self.local / 'last-run.json', {})
        if not isinstance(last_run, dict):
            last_run = {}
        return {
            'preview': self.background_value('preview', self.configuration_preview,
                {'current': {}, 'regions': region_profile.PROFILES, 'read_at': ''}),
            'version': VERSION,
            'admin': is_admin(),
            'configured': configured,
            'configured_error': configured_error,
            'plan': plan_view,
            'baseline': self.baseline_name(),
            'snapshots': self.snapshots(),
            'verification': self.verification_summary(),
            'update': self.background_value('update', self.update_summary,
                {'mode': 'unknown', 'task': {'installed': None}, 'settings': {}, 'last': {}}, ttl=12),
            'browser': browser,
            'service': self.background_value('service', self.service_status,
                {'service': 'Unknown', 'app_running': False}, ttl=15),
            'app': {
                'python': sys.version.split()[0],
                'node': bool(self.node_path()),
                'windows': os.name == 'nt',
                'gui_browser_fallback': self.browser_fallback,
                'project_name': self.project.name,
                'path': str(self.local.relative_to(self.project)) if self.local.is_relative_to(self.project) else '',
            },
            'operation': {
                'settings_busy': self._settings_busy,
                'settings_pending': self.settings_transactions(),
                'job': active,
                'reconfigure_active': (self.local / 'reconfigure-active.json').is_file(),
                'pending_firewall': self.pending_firewall(),
            },
            'last_run': {
                'success': bool(last_run.get('success')),
                'action': str(last_run.get('action') or ''),
                'status': str(last_run.get('status') or ''),
                'error': str(last_run.get('error') or ''),
            },
            'reconfigure_runs': self.reconfigure_runs(),
            'log_files': self.log_files(),
            'limits': {
                'browser': '沙箱资料目录仅隔离浏览器数据；不是操作系统级沙箱，也不修改默认 Chrome/Edge。',
                'network': '未测试浏览器 WebRTC、Claude 网页、账号可用性、Docker/WSL 和虚拟机。',
            },
            'collection': self.collection_status(),
        }

    def log_files(self):
        rows = []
        for name in ('last-error.log', 'client-error.log', 'snapshot-error.log', 'rollback-error.log',
                     'dependency-error.log', 'update-result.json'):
            path = self.local / name
            if path.is_file():
                rows.append({'name': name, 'bytes': path.stat().st_size,
                             'time': datetime.datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec='seconds')})
        server = self.local / 'gui' / 'server.log'
        if server.is_file():
            rows.append({'name': 'gui/server.log', 'bytes': server.stat().st_size,
                         'time': datetime.datetime.fromtimestamp(server.stat().st_mtime).astimezone().isoformat(timespec='seconds')})
        return rows

    def log_text(self, name):
        if name == 'gui/server.log':
            path = self.local / 'gui' / 'server.log'
        elif name in ('last-error.log', 'client-error.log', 'snapshot-error.log', 'rollback-error.log',
                      'dependency-error.log', 'update-result.json'):
            path = self.local / name
        else:
            match = re.fullmatch(r'gui/jobs/([a-f0-9]{32})/console\.log', str(name))
            if not match:
                raise ApiError(404, '日志名称无效。')
            path = self.jobs_dir / match.group(1) / 'console.log'
        if not path.is_file():
            raise ApiError(404, '日志不存在。')
        return {'name': name, 'text': tail_text(path, limit=200000, lines=20000)}

    # ---- discovery -----------------------------------------------------
    def nodes(self, refresh=False):
        now = time.monotonic()
        if not refresh and self._nodes_cache[1] is not None and now - self._nodes_cache[0] < 5:
            return self._nodes_cache[1]
        script = self.scripts / 'client.py'
        env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONNOUSERSITE='1')
        if self.node_path():
            env['CCN_NODE'] = self.node_path()
        try:
            completed = subprocess.run(
                [python_console(), str(script), 'discover'],
                cwd=str(self.scripts), capture_output=True, text=True, encoding='utf-8',
                errors='replace', timeout=60, env=env, creationflags=_no_window(),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ApiError(500, '读取当前订阅失败：' + type(exc).__name__ + '。详情见日志。') from exc
        if completed.returncode != 0:
            raise ApiError(500, '读取当前订阅失败。请确认 Clash Verge Rev 已安装并至少有一个远程订阅；详情见日志。')
        parsed = _last_json_line(completed.stdout)
        if not isinstance(parsed, dict):
            raise ApiError(500, '订阅输出格式无效。详情见日志。')
        subscriptions = []
        regions = {}
        for row in parsed.get('subscriptions', []):
            if not isinstance(row, dict):
                continue
            uid = str(row.get('uid') or '')
            nodes = [str(x) for x in row.get('nodes', []) if isinstance(x, str)]
            name = str(row.get('name') or '')
            subscriptions.append({'uid': uid, 'name': name, 'nodes': nodes})
            regions[uid] = {}
            subscription_hint = node_region.infer(name)
            for node in nodes:
                hint = node_region.infer(node) or subscription_hint
                if hint:
                    regions[uid][node] = hint
        plan = self.plan()
        result = {
            'subscriptions': subscriptions,
            'active_uid': str(parsed.get('active_uid') or ''),
            'plan_node': str(plan.get('node') or ''),
            'plan_subscription': str(plan.get('subscription_uid') or ''),
            'plan_region': str(plan.get('region') or 'SG'),
            'regions': regions,
        }
        self._nodes_cache = (now, result)
        return result

    # ---- jobs ----------------------------------------------------------
    def _cleanup_jobs(self):
        try:
            self.jobs_dir.mkdir(parents=True, exist_ok=True)
            folders = []
            for folder in self.jobs_dir.iterdir():
                try:
                    if (JOB_ID_RE.fullmatch(folder.name) and folder.is_dir()
                            and not folder.is_symlink() and not folder.is_junction()
                            and folder.resolve().parent == self.jobs_dir.resolve()):
                        folders.append((folder.stat().st_mtime, folder))
                except OSError:
                    continue
        except OSError:
            return
        completed = 0
        for _, folder in sorted(folders, reverse=True):
            status = read_json(folder / 'status.json', {})
            if not isinstance(status, dict):
                continue
            if status.get('state') not in ('success', 'failed', 'cancelled', 'interrupted'):
                continue
            completed += 1
            if completed > 30 and folder.resolve().parent == self.jobs_dir.resolve():
                shutil.rmtree(folder, ignore_errors=True)

    def _process_info(self, pid):
        command = ("$ErrorActionPreference='Stop';$p=Get-CimInstance Win32_Process -Filter \"ProcessId=" + str(int(pid)) + "\";"
                   "if($p){[ordered]@{alive=$true;command=[string]$p.CommandLine;name=[string]$p.Name}|ConvertTo-Json -Compress}"
                   "else{[ordered]@{alive=$false;command='';name=''}|ConvertTo-Json -Compress}")
        try:
            result = subprocess.run(
                ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                timeout=8, creationflags=_no_window(),
            )
            value = _last_json_line(result.stdout)
            if result.returncode == 0 and isinstance(value, dict) and type(value.get('alive')) is bool:
                return value
            return {'alive': None}
        except (OSError, subprocess.SubprocessError):
            return {'alive': None}

    def _process_has_children(self, pid):
        command = ("$p=Get-CimInstance Win32_Process -Filter \"ParentProcessId=" + str(int(pid)) + "\";"
                   "if($p){'1'}else{'0'}")
        try:
            result = subprocess.run(
                ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                capture_output=True, text=True, timeout=8, creationflags=_no_window(),
            )
            return result.stdout.strip() == '1'
        except (OSError, subprocess.SubprocessError):
            return True

    def cancel_job(self, job_id):
        # Reconcile a completed operation before offering to terminate its wrapper.
        current = self.job_view(job_id)
        if current.get('state') != 'running':
            return current
        if current.get('action') in LOCAL_ACTIONS:
            raise ApiError(409, '本地操作仍在进行，不能中断；请等待结果。')
        if not isinstance(job_id, str) or not JOB_ID_RE.fullmatch(job_id):
            raise ApiError(404, '操作记录无效。')
        folder = self.jobs_dir / job_id
        status = read_json(folder / 'status.json', {})
        tracked = self.jobs.get(job_id)
        if tracked and tracked.get('runtime'):
            status = tracked['runtime']
        if not isinstance(status, dict) or status.get('state') != 'running':
            return self.job_view(job_id)
        if (self.local / 'reconfigure-active.json').is_file():
            raise ApiError(409, '本机安全重新配置或网络恢复仍在执行；请等待独立看护完成。')
        status = dict(status)
        pid = status.get('pid')
        if type(pid) is not int or pid <= 0:
            raise ApiError(409, '无法确认任务进程，未结束任务或解除保护。请稍后重试。')
        info = self._process_info(pid)
        if info.get('alive') is None:
            raise ApiError(409, '无法确认任务进程状态，仍保留忙碌保护。请稍后重试。')
        if info.get('alive') is False and tracked and tracked.get('process'):
            try:
                confirmed_exit = tracked['process'].poll() is not None
            except Exception:
                confirmed_exit = False
            if not confirmed_exit:
                raise ApiError(409, '进程信息尚不一致，仍保留忙碌保护。请稍后重试。')
        if info.get('alive') is True:
            command = str(info.get('command') or '')
            allowed = [str(self.scripts / 'run.ps1'), str(self.scripts / 'update.py'), str(self.scripts / 'verify.py')]
            if not any(value.lower() in command.lower() for value in allowed):
                raise ApiError(409, '该进程不是本项目的操作进程，未结束。')
            try:
                result = subprocess.run(
                    ['taskkill.exe', '/PID', str(pid), '/T', '/F'],
                    capture_output=True, text=True, timeout=15,
                    creationflags=_no_window(),
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise ApiError(409, '无法结束卡住的任务进程；请在任务管理器中结束它。') from exc
            if result.returncode != 0:
                raise ApiError(409, '无法结束卡住的任务进程；它可能已提权，请在任务管理器中结束。')
            try:
                if tracked and tracked.get('process'):
                    tracked['process'].wait(timeout=5)
                elif self._process_info(pid).get('alive') is not False:
                    raise RuntimeError('exit not confirmed')
            except Exception as exc:
                raise ApiError(409, '已请求结束任务，但尚未确认退出，仍保留忙碌保护。') from exc
        label = {'apply': '配置', 'restore': '恢复', 'supplement': '补充配置', 'snapshot': '快照保存',
                 'reconfigure': '重新配置', 'update': '规则更新', 'verify': '连通性检查'}.get(status.get('action'), '操作')
        message = '任务已手动结束；' + label + '可能未完成，请检查快照/日志后再决定是否重试。'
        status.update({'state': 'interrupted', 'finished': now_iso(), 'error': message})
        atomic_json(folder / 'status.json', status)
        append_event(folder / 'events.jsonl', {'type': 'cancelled', 'message': message})
        with self._lock:
            job = self.jobs.get(job_id)
            if job:
                job['forced'] = True
                job['state'] = 'interrupted'
                job['completion'] = status
            if self.active_job and self.active_job.get('id') == job_id:
                self.active_job = None
        return self.job_view(job_id)

    def _validate_job(self, payload):
        action = str(payload.get('action') or '')
        if action in LOCAL_ACTIONS:
            if action == 'settings-recover':
                transaction = str(payload.get('transaction') or '')
                self.transaction_folder(transaction)
                if payload.get('confirm') != transaction:
                    raise ApiError(400, '设置恢复确认不匹配。')
                if transaction not in {row['id'] for row in self.settings_transactions()}:
                    raise ApiError(400, '没有待恢复的设置记录。')
                return action, {'transaction': transaction}
            if payload.get('confirm') != ('DELETE' if action == 'browser-reset' else action.upper()):
                raise ApiError(400, '浏览器操作确认不匹配。')
            return action, {}
        if action not in RUN_ACTIONS | {'update', 'verify'}:
            raise ApiError(400, '不支持的操作。')
        confirm = str(payload.get('confirm') or '')
        params = {}
        plan = self.plan()
        configured, _ = self.configured()
        if action == 'restore':
            snapshot = str(payload.get('snapshot') or '')
            if not SNAPSHOT_RE.fullmatch(snapshot):
                raise ApiError(400, '快照名称无效。')
            rows = {row['name']: row for row in self.snapshots()}
            if snapshot not in rows:
                raise ApiError(400, '所选快照不存在或清单不完整。')
            if not rows[snapshot]['sealed']:
                raise ApiError(400, '该快照没有完整的已封存系统记录，不能作为可靠恢复点。')
            if confirm != snapshot:
                raise ApiError(400, '恢复确认与所选快照不一致。')
            params['snapshot'] = snapshot
        else:
            if confirm != CONFIRM[action]:
                raise ApiError(400, '确认信息不匹配；未执行操作。')
        if action == 'apply':
            node = str(payload.get('node') or '').strip()
            if not node or len(node) > 200 or any(ord(ch) < 32 for ch in node):
                raise ApiError(400, '请选择订阅中已存在的完整节点名。')
            subscription = str(payload.get('subscription') or '').strip()
            if len(subscription) > 160:
                raise ApiError(400, '订阅标识过长。')
            region = str(payload.get('region') or 'SG').strip().upper()
            if not REGION_RE.fullmatch(region):
                raise ApiError(400, '出口地区须为两位国家代码，例如 SG。')
            params.update(node=node, subscription=subscription, region=region)
        if action in ('supplement', 'reconfigure', 'update', 'verify') and not plan:
            raise ApiError(400, '还没有配置记录；请先完成一次配置。')
        if action in ('supplement', 'reconfigure') and not self.baseline_name():
            raise ApiError(400, '没有可用的首次恢复点；请先完成一次配置并保存原始快照。')
        if action == 'reconfigure':
            if (self.local / 'reconfigure-active.json').is_file():
                raise ApiError(409, '安全重新配置或网络恢复仍在执行，请等待本机看护完成。')
        if action in ('update', 'verify') and not configured:
            raise ApiError(400, '当前未检测到本项目的分流配置；请先配置。')
        return action, params

    def start_job(self, payload):
        with self._lock:
            if self.active_job or self._settings_busy or self.stopping or self._shutdown_requested:
                raise ApiError(409, '另一个操作正在进行，请等待完成。')
            if payload.get('action') != 'settings-recover' and self.settings_transactions():
                raise ApiError(409, '请先在快照与恢复页面恢复未完成的设置保存。')
            action, params = self._validate_job(payload)
            job_id = secrets.token_hex(16)
            self.jobs_dir.mkdir(parents=True, exist_ok=True)
            folder = self.jobs_dir / job_id
            folder.mkdir(parents=True, exist_ok=False)
            job = {
                'id': job_id,
                'action': action,
                'params': params,
                'created': now_iso(),
                'started_epoch': time.time(),
                'state': 'running',
                'folder': str(folder),
            }
            try:
                atomic_json(folder / 'job.json', {k: job[k] for k in ('id', 'action', 'params', 'created', 'state')})
                atomic_json(folder / 'status.json', {'state': 'running', 'action': action, 'started': job['created'], 'exit_code': None})
                thread = threading.Thread(target=self._run_job, args=(job,), daemon=True, name='ccn-job-' + job_id[:8])
                job['thread'] = thread
                self.jobs[job_id] = job
                self.active_job = job
                thread.start()
            except Exception as exc:
                self.jobs.pop(job_id, None)
                if self.active_job is job:
                    self.active_job = None
                try:
                    atomic_json(folder / 'status.json', {'state': 'failed', 'action': action,
                        'error': '任务创建失败，未开始执行。', 'exit_code': None, 'finished': now_iso()})
                except OSError:
                    pass
                raise ApiError(500, '任务创建失败，未开始执行。请检查本地目录权限和剩余空间。') from exc
            return self.job_view(job_id)

    def _job_command(self, action, params, folder):
        events = folder / 'events.jsonl'
        events.touch(exist_ok=True)
        if action in RUN_ACTIONS:
            command = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
                       str(self.scripts / 'run.ps1'), '-Action', action, '-NoPause', '-EventsFile', str(events),
                       '-Python', python_console()]
            if action == 'apply':
                command += ['-Node', params['node'], '-Region', params['region']]
                if params.get('subscription'):
                    command += ['-Subscription', params['subscription']]
            elif action == 'restore':
                command += ['-Snapshot', params['snapshot']]
            if action in ('apply', 'supplement', 'restore', 'reconfigure'):
                command.append('-Acknowledged')
            return command, events
        if action == 'update':
            return [python_console(), str(self.scripts / 'update.py')], events
        return [python_console(), str(self.scripts / 'verify.py')], events

    def _complete_job(self, job, status):
        try:
            atomic_json(Path(job['folder']) / 'status.json', status)
        except OSError:
            status['record_warning'] = '任务已结束，但状态记录写入失败；本次结果仅保留在当前界面会话中。'
        with self._lock:
            job['completion'] = status
            job['state'] = status['state']
            if self.active_job is job or (self.active_job and self.active_job['id'] == job['id']):
                self.active_job = None

    def _run_job(self, job):
        if job['action'] in LOCAL_ACTIONS:
            self._run_local_job(job)
            return
        folder = Path(job['folder'])
        action = job['action']
        events = folder / 'events.jsonl'
        process = None
        exit_code = None
        env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONNOUSERSITE='1', CCN_EVENTS_FILE=str(events))
        if self.node_path():
            env['CCN_NODE'] = self.node_path()
        try:
            command, events = self._job_command(action, job['params'], folder)
            if action in ('update', 'verify'):
                append_event(events, {'type': 'start', 'action': action})
            with (folder / 'console.log').open('ab') as output:
                process = subprocess.Popen(command, cwd=str(self.project), env=env, stdin=subprocess.DEVNULL,
                    stdout=output, stderr=subprocess.STDOUT, creationflags=_no_window())
                # Track the child before any fallible disk writes or stream cleanup.
                job['process'] = process
                job['runtime'] = {'state': 'running', 'action': action, 'pid': process.pid,
                                  'started': job['created'], 'exit_code': None}
                try:
                    atomic_json(folder / 'status.json', job['runtime'])
                except OSError:
                    job['monitor_warning'] = '运行记录写入失败，仍在跟踪进程；操作结束前请勿重复执行。'
            while exit_code is None:
                try:
                    exit_code = process.wait()
                except Exception:
                    job['monitor_warning'] = '暂时无法读取进程退出状态，仍在跟踪；请勿重复执行。'
                    try:
                        exit_code = process.poll()
                    except Exception:
                        pass
                    time.sleep(.5)
            self._finish_job(job, exit_code)
        except Exception as exc:
            # Even an error closing the output stream must not orphan a started child.
            if process is not None and exit_code is None:
                job['monitor_warning'] = '任务记录发生错误，仍在等待已启动的进程结束。'
                while exit_code is None:
                    try:
                        exit_code = process.wait()
                    except Exception:
                        try:
                            exit_code = process.poll()
                        except Exception:
                            pass
                        time.sleep(.5)
            self.log('job ' + job['id'] + ' failed: ' + type(exc).__name__)
            try:
                append_event(events, {'type': 'error', 'message': '任务记录或执行失败：' + type(exc).__name__})
            except OSError:
                pass
            self._complete_job(job, {'state': 'failed', 'action': action, 'exit_code': exit_code,
                'started': job['created'], 'finished': now_iso(),
                'error': '任务未完成或结果记录失败，请检查本地日志及当前设置。'})

    def _run_local_job(self, job):
        folder = Path(job['folder'])
        events = folder / 'events.jsonl'
        result = None
        error = ''
        try:
            append_event(events, {'type': 'step', 'index': 1, 'total': 1,
                                  'title': '正在恢复原设置…' if job['action'] == 'settings-recover' else '正在处理浏览器操作…'})
            with operation_lock(self.local, settings_recovery=job['action'] == 'settings-recover'):
                if job['action'] == 'settings-recover':
                    result = self.restore_settings_transaction(job['params']['transaction'])
                else:
                    self.invalidate_background('browser')
                    result = self._browser_action({'action': LOCAL_ACTIONS[job['action']], 'confirm': 'DELETE'})
        except Exception as exc:
            error = str(exc) if isinstance(exc, ApiError) else '操作未完成（' + type(exc).__name__ + '）。'
        finally:
            self.invalidate_background('browser', 'update')
        status = {'state': 'failed' if error else 'success', 'action': job['action'],
            'started': job['created'], 'finished': now_iso(), 'exit_code': 1 if error else 0,
            'error': error, 'result': result}
        try:
            append_event(events, {'type': 'error' if error else 'done', 'message': error or '操作已完成。'})
            atomic_json(folder / 'status.json', status)
            (folder / 'console.log').write_text(error or '操作已完成。', encoding='utf-8')
        except OSError:
            status.update(state='failed', exit_code=1,
                          error='操作已结束，但本地记录写入失败。请检查当前状态和目录权限。')
        finally:
            with self._lock:
                job['completion'] = status
                job['state'] = status['state']
                if self.active_job and self.active_job['id'] == job['id']:
                    self.active_job = None

    def _finish_job(self, job, code):
        if job.get('forced'):
            with self._lock:
                if self.active_job and self.active_job.get('id') == job['id']:
                    self.active_job = None
            return
        folder = Path(job['folder'])
        action = job['action']
        events = read_events(folder / 'events.jsonl')
        console_text = tail_text(folder / 'console.log', limit=40000, lines=300)
        done = next((e for e in reversed(events) if e.get('type') == 'done'), None)
        cancelled = next((e for e in reversed(events) if e.get('type') == 'cancelled'), None)
        error_event = next((e for e in reversed(events) if e.get('type') == 'error'), None)
        result = None
        if action in ('update', 'verify'):
            result = _last_json_line(console_text)
            success = code == 0 and isinstance(result, dict) and bool(result.get('success'))
            if not success and isinstance(result, dict):
                error = str(result.get('error') or result.get('note') or '操作未通过。')
            else:
                error = '' if success else '操作未通过。'
            append_event(folder / 'events.jsonl', {
                'type': 'done' if success else 'error',
                'message': ('规则更新已跳过（订阅或脚本关联变化）。' if success and action == 'update' and isinstance(result, dict) and result.get('skipped')
                            else '规则更新完成。' if success and action == 'update'
                            else '连通性检查完成。' if success else (error or '操作未通过。')),
            })
        else:
            last_run_path = self.local / 'last-run.json'
            if last_run_path.is_file():
                try:
                    if last_run_path.stat().st_mtime >= job['started_epoch'] - 5:
                        candidate = read_json(last_run_path, None)
                        if isinstance(candidate, dict) and candidate.get('action') == action:
                            result = candidate
                except OSError:
                    result = None
            success = code == 0 and done is not None and error_event is None and cancelled is None
            if success:
                error = ''
            elif cancelled is not None:
                error = str(cancelled.get('message') or '已取消管理员授权。')
            elif error_event is not None:
                error = str(error_event.get('message') or '操作未完成。')
            elif isinstance(result, dict) and result.get('error'):
                error = str(result.get('error'))
            else:
                error = '操作未完成（退出码 ' + str(code) + '）。'
        state = 'success' if success else 'cancelled' if cancelled is not None and code == 0 and error_event is None else 'failed'
        status = {
            'state': state,
            'action': action,
            'exit_code': code,
            'started': job.get('created'),
            'finished': now_iso(),
            'error': error,
            'result': result if isinstance(result, dict) else None,
            'params': job.get('params', {}),
        }
        self._complete_job(job, status)

    def job_view(self, job_id):
        if not isinstance(job_id, str) or not JOB_ID_RE.fullmatch(job_id):
            raise ApiError(404, '操作记录无效。')
        with self._lock:
            job = self.jobs.get(job_id)
        folder = self.jobs_dir / job_id
        if job is None:
            stored = read_json(folder / 'job.json', None)
            if not isinstance(stored, dict):
                raise ApiError(404, '找不到该操作记录。')
            job = {'id': job_id, **stored, 'started_epoch': 0}
        status = job.get('completion') or read_json(folder / 'status.json', {})
        if not job.get('completion') and job.get('runtime') and (not isinstance(status, dict) or status.get('state') in (None, 'running')):
            status = job['runtime']
        if not isinstance(status, dict) or not status:
            status = {'state': job.get('state', 'running'), 'action': job.get('action'), 'exit_code': None}
        events = read_events(folder / 'events.jsonl')
        elapsed = 0
        if (status.get('state') == 'running' and not job.get('process')
                and job.get('action') in ('apply', 'restore', 'supplement', 'snapshot', 'reconfigure')):
            terminal_events = [e for e in events if e.get('type') in ('done', 'error', 'cancelled')]
            result_path = self.local / 'last-run.json'
            result = read_json(result_path, {})
            started = parse_time(status.get('started') or job.get('created'))
            completed = parse_time(terminal_events[-1].get('time')) if terminal_events else None
            if (terminal_events and terminal_events[-1].get('type') == 'done'
                    and not any(e.get('type') in ('error', 'cancelled') for e in terminal_events)
                    and isinstance(result, dict) and result.get('success') is True
                    and result.get('action') == job.get('action') and started is not None and completed is not None
                    and started.timestamp() - 5 <= result_path.stat().st_mtime <= completed.timestamp() + 30):
                self._finish_job(job, 0)
                status = read_json(folder / 'status.json', {})
        if job.get('started_epoch'):
            elapsed = int(max(0, time.time() - job['started_epoch']))
        else:
            moment = parse_time(status.get('started') or job.get('created'))
            if moment is not None:
                elapsed = int(max(0, (datetime.datetime.now().astimezone() - moment).total_seconds()))
        terminal = any(isinstance(event, dict) and event.get('type') in ('done', 'error', 'cancelled') for event in events)
        stale = status.get('state') == 'running' and terminal and elapsed >= 15
        return {
            'id': job_id,
            'action': job.get('action') or status.get('action') or '',
            'params': job.get('params') or status.get('params') or {},
            'created': job.get('created') or status.get('started') or '',
            'state': status.get('state') or 'running',
            'exit_code': status.get('exit_code'),
            'error': str(status.get('error') or ''),
            'record_warning': status.get('record_warning') or (job.get('monitor_warning') if status.get('state') == 'running' else ''),
            'result': status.get('result'),
            'elapsed': elapsed,
            'stale': stale,
            'events': events,
            'log': tail_text(folder / 'console.log', limit=30000, lines=200),
        }

    def active_job_view(self):
        with self._lock:
            job = self.active_job
        if not job:
            return None
        try:
            return self.job_view(job['id'])
        except ApiError:
            return None

    # ---- mutations -----------------------------------------------------
    def _update_task(self, action, at=None, days=None, *, state_file=None):
        command = ['-Action', action, '-Python', python_console()]
        if state_file:
            command += ['-StateFile', str(state_file)]
        if at:
            command += ['-At', at]
        if days:
            command += ['-DaysInterval', str(days)]
        try:
            completed = _run_powershell(self.scripts / 'update_task.ps1', command, timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ApiError(500, '规则更新任务操作失败：' + type(exc).__name__ + '。') from exc
        if completed.returncode != 0:
            self.log('update_task failed: ' + (completed.stderr or completed.stdout)[-800:])
            raise ApiError(500, '规则更新任务操作失败；详情见 .local/gui/server.log。')
        return completed

    def settings_transactions(self):
        return pending_settings(self.local)

    def transaction_folder(self, transaction):
        if not JOB_ID_RE.fullmatch(str(transaction)):
            raise ApiError(400, '设置恢复记录名称无效。')
        root = (self.local / 'gui' / 'settings-transactions').resolve()
        folder = root / transaction
        if not folder.resolve().is_relative_to(root) or folder.is_symlink():
            raise ApiError(400, '设置恢复路径不在本项目内。')
        return folder

    def restore_settings_transaction(self, transaction):
        folder = self.transaction_folder(transaction)
        journal = folder / 'transaction.json'
        record = read_json(journal, {})
        if (not isinstance(record, dict) or record.get('version') != 1
                or type(record.get('previous_exists')) is not bool
                or type(record.get('mutations_started')) is not bool):
            raise ApiError(400, '设置恢复记录不完整，未修改当前设置。')
        if record.get('phase') in ('committed', 'rolled_back'):
            return {'success': True, 'already_finished': True}
        if not record['mutations_started']:
            atomic_json(journal, {**record, 'phase': 'rolled_back'})
            return {'success': True}
        state_file = folder / 'task.json'
        original = folder / 'ui-settings.original'
        for path, key in [(state_file, 'task_sha256')] + ([(original, 'settings_sha256')] if record['previous_exists'] else []):
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != record.get(key):
                raise ApiError(400, '设置恢复备份缺失或校验失败，未修改当前设置。')
        atomic_json(journal, {**record, 'phase': 'recovering'})
        failures = []
        try:
            self._update_task('rollback', state_file=state_file)
        except Exception:
            failures.append('计划任务')
        try:
            settings_path = gui_settings.settings_path(self.local)
            if record['previous_exists']:
                temporary = folder / 'ui-settings.restore'
                with temporary.open('wb') as stream:
                    stream.write(original.read_bytes())
                    stream.flush()
                    os.fsync(stream.fileno())
                replace_file(temporary, settings_path)
            else:
                settings_path.unlink(missing_ok=True)
        except Exception:
            failures.append('界面设置')
        self.invalidate_background('update')
        atomic_json(journal, {**record, 'phase': 'recovery_failed' if failures else 'rolled_back'})
        if failures:
            raise ApiError(500, '、'.join(failures) + '恢复未完成，可在快照与恢复页面重试。')
        return {'success': True}

    @contextmanager
    def settings_operation(self):
        with self._lock:
            if self.active_job or self._settings_busy or self.stopping or self._shutdown_requested:
                raise ApiError(409, '另一个操作正在进行，请等待完成。')
            if self.settings_transactions():
                raise ApiError(409, '存在未完成的设置保存，请先在快照与恢复页面恢复原设置。')
            self._settings_busy = True
        try:
            with operation_lock(self.local):
                yield
        except RuntimeError as exc:
            if isinstance(exc, ApiError):
                raise
            raise ApiError(409, '另一个操作或恢复正在进行，请稍后重试。') from exc
        finally:
            with self._lock:
                self._settings_busy = False

    def save_update_settings(self, payload):
        mode = str(payload.get('mode') or '')
        time_value = str(payload.get('time') or '')
        days = payload.get('days', 1)
        try:
            gui_settings.validate(mode, time_value, days)
        except ValueError as exc:
            raise ApiError(400, str(exc)) from exc
        with self.settings_operation():
            # Validate before taking the exact file/task recovery point.
            gui_settings.load(self.local)
            settings_path = gui_settings.settings_path(self.local)
            previous = settings_path.read_bytes() if settings_path.exists() else None
            folder = self.local / 'gui' / 'settings-transactions' / secrets.token_hex(16)
            folder.mkdir(parents=True)
            state_file = folder / 'task.json'
            journal = folder / 'transaction.json'
            record = {'version': 1, 'phase': 'preparing', 'previous_exists': previous is not None,
                      'mutations_started': False, 'created': now_iso()}
            if previous is not None:
                with (folder / 'ui-settings.original').open('wb') as stream:
                    stream.write(previous)
                    stream.flush()
                    os.fsync(stream.fileno())
                record['settings_sha256'] = hashlib.sha256(previous).hexdigest()
            atomic_json(journal, record)
            try:
                self._update_task('snapshot', state_file=state_file)
                record.update(task_sha256=hashlib.sha256(state_file.read_bytes()).hexdigest(),
                              phase='applying', mutations_started=True)
                atomic_json(journal, record)
                if mode == 'manual':
                    self._update_task('remove')
                elif self.configured()[0]:
                    self._update_task('install', time_value, int(days))
                gui_settings.save(mode, time_value, int(days), self.local)
                atomic_json(journal, {**record, 'phase': 'committed'})
            except Exception as exc:
                try:
                    self.restore_settings_transaction(folder.name)
                except Exception as recovery:
                    raise ApiError(500, '保存失败，恢复未完成。请在快照与恢复页面重试；记录位于 '
                                   '.local/gui/settings-transactions/' + folder.name + '。') from recovery
                raise ApiError(500, '保存失败，原界面设置和计划任务已恢复。') from exc
            self._task_cache = (0.0, None)
            summary = self.update_summary()
            self.publish_background('update', summary)
            return summary

    def browser_action(self, payload):
        asynchronous = {value: key for key, value in LOCAL_ACTIONS.items() if value is not None}
        action = str(payload.get('action') or '')
        if action in asynchronous:
            job_action = asynchronous[action]
            if action == 'reset' and payload.get('confirm') != 'DELETE':
                raise ApiError(400, '重置资料需要明确确认。')
            return self.start_job({'action': job_action, 'confirm': 'DELETE' if action == 'reset' else job_action.upper()})
        with self.settings_operation():
            self.invalidate_background('browser')
            try:
                result = self._browser_action(payload)
                if str(payload.get('action')) == 'save-settings':
                    result['default'] = sandbox_browser.default_status(self.local)
                    self.publish_background('browser', result)
                return result
            finally:
                if str(payload.get('action')) != 'save-settings':
                    self.invalidate_background('browser')

    def _browser_action(self, payload):
        action = str(payload.get('action') or '')
        try:
            if action == 'open':
                return sandbox_browser.launch(local=self.local)
            if action == 'check':
                return sandbox_browser.open_environment_checks(local=self.local)
            if action == 'close':
                return sandbox_browser.close(local=self.local)
            if action == 'reset':
                return sandbox_browser.reset(local=self.local, confirm=str(payload.get('confirm') or ''))
            if action == 'default-status':
                return sandbox_browser.default_status(self.local)
            if action == 'register-default':
                return sandbox_browser.register_default(self.local)
            if action == 'open-default-settings':
                return sandbox_browser.open_default_settings()
            if action == 'save-settings':
                running = sandbox_browser.running_pids(sandbox_browser.profile_path(self.local))
                if running:
                    raise ApiError(409, '请先关闭沙箱浏览器，再修改引擎或语言。')
                values = {
                    'engine': str(payload.get('engine') or ''),
                    'language': str(payload.get('language') or ''),
                }
                sandbox_browser.save_settings(values, self.local)
                return sandbox_browser.status(self.local)
            raise ApiError(400, '不支持的浏览器操作。')
        except ApiError:
            raise
        except (ValueError, OSError, RuntimeError) as exc:
            raise ApiError(400, str(exc)) from exc

    def reveal_local(self):
        try:
            subprocess.Popen(['explorer.exe', str(self.local.resolve())], creationflags=_no_window())
            return {'opened': True, 'path': str(self.local.relative_to(self.project)) if self.local.is_relative_to(self.project) else '.local'}
        except OSError as exc:
            raise ApiError(500, '无法打开资源管理器。') from exc

    # ---- server lifecycle ---------------------------------------------
    def session_url(self):
        return self.base_url + '/?t=' + self.token

    def existing_session(self):
        value = read_json(self.session_file, {})
        if not isinstance(value, dict):
            return None
        port = value.get('port')
        token = value.get('token')
        url = value.get('url')
        if not isinstance(port, int) or not isinstance(token, str) or not isinstance(url, str):
            return None
        try:
            request = urllib.request.Request(
                f'http://127.0.0.1:{port}/api/ping',
                headers={'X-CCN-Token': token, 'Cache-Control': 'no-store'},
            )
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=2) as response:
                data = json.loads(response.read().decode('utf-8'))
            if data.get('ok') is True:
                return url
        except Exception:
            return None
        return None

    def run(self):
        if os.name != 'nt':
            print('本界面仅支持 Windows。', file=sys.stderr)
            return 2
        self.local.joinpath('gui').mkdir(parents=True, exist_ok=True)
        existing = self.existing_session()
        if existing:
            try:
                sandbox_browser.launch(existing, local=self.local, gui=True, app=True)
            except Exception as exc:
                # Same rule as a fresh start: reuse the app window, never the
                # user's default browser.
                self.log('existing session browser open failed: ' + repr(exc))
            return 0
        try:
            self.session_file.unlink()
        except OSError:
            pass
        self._cleanup_jobs()
        handler = make_handler(self)
        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), handler)
        self.httpd.daemon_threads = True
        port = self.httpd.server_address[1]
        self.base_url = f'http://127.0.0.1:{port}'
        atomic_json(self.session_file, {
            'pid': os.getpid(),
            'port': port,
            'token': self.token,
            'url': self.session_url(),
            'version': VERSION,
            'started': now_iso(),
        })
        self.log('GUI listening on ' + self.base_url)
        if self.open_browser:
            self.open_gui_browser()
        threading.Thread(target=self._idle_monitor, daemon=True, name='ccn-idle').start()
        try:
            self.httpd.serve_forever(poll_interval=0.5)
        finally:
            self._cleanup_session()
        return 0

    def open_gui_browser(self):
        try:
            sandbox_browser.launch(self.session_url(), local=self.local, gui=True, app=True)
            self.browser_opened = True
            self.last_browser_seen = time.monotonic()
            self.log('GUI browser opened in isolated app window')
        except Exception as exc:
            self.browser_error = str(exc)
            self.log('GUI browser launch failed: ' + repr(exc))
            # Never open the user's default browser for the panel: a stray tab
            # in the everyday browser is what the app window exists to avoid.
            # Tell the user visibly, then keep the loopback server available.
            self.log('GUI stays on ' + self.base_url + ' (no default-browser fallback)')
            print(self.session_url(), flush=True)
            try:
                import ctypes
                import threading
                threading.Thread(
                    target=ctypes.windll.user32.MessageBoxW,
                    args=(None, '界面窗口无法打开。请查看 .local\\gui\\server.log 后重新运行「一键网络.cmd」。',
                          'Claude 网络环境', 0x10),
                    daemon=True).start()
            except Exception:
                pass

    def _idle_monitor(self):
        while True:
            time.sleep(5)
            if self.stopping or not self.idle_exit:
                continue
            with self._lock:
                active = self.active_job is not None or self._settings_busy
            if active:
                continue
            now = time.monotonic()
            if self.browser_opened:
                try:
                    pids = sandbox_browser.running_pids(sandbox_browser.gui_profile_path(self.local))
                except Exception:
                    pids = []
                if pids:
                    self.last_browser_seen = now
                elif (self.last_browser_seen and now - self.last_browser_seen > 25
                      and now - self.last_heartbeat > 40):
                    self.log('GUI browser closed; stopping server')
                    if self.stop() is False:
                        continue
                    return
            if now - self.last_heartbeat > 600:
                self.log('GUI heartbeat expired; stopping server')
                if self.stop() is False:
                    continue
                return

    def _cleanup_session(self):
        value = read_json(self.session_file, {})
        if isinstance(value, dict) and value.get('token') == self.token:
            try:
                self.session_file.unlink()
            except OSError:
                pass
        try:
            sandbox_browser.close(local=self.local, profile=sandbox_browser.gui_profile_path(self.local))
        except Exception as exc:
            self.log('GUI browser close failed: ' + repr(exc))

    def stop(self):
        with self._lock:
            if self.stopping:
                return
            if self.active_job or self._settings_busy:
                self.log('shutdown deferred until current operation completes')
                return False
            self.stopping = True
        try:
            if self.httpd:
                self.httpd.shutdown()
        except Exception:
            pass


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        server_version = 'CCN-GUI/' + VERSION
        sys_version = ''

        def log_message(self, fmt, *args):
            app.log('%s - %s' % (self.address_string(), fmt % args))

        def log_request(self, code='-', size='-'):
            # Never write ?t=... to the local log; the request line contains the token.
            try:
                path = urlsplit(self.path).path
            except Exception:
                path = '-'
            app.log('%s %s %s %s' % (self.address_string(), self.command, path, code))

        def _security_headers(self, content_type, length):
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(length))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Permissions-Policy', 'geolocation=(), microphone=(), camera=()')
            self.send_header('Content-Security-Policy',
                             "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                             "img-src 'self' data:; font-src 'self'; connect-src 'self' https://ipapi.co https://ipinfo.io; "
                             "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
            self.send_header('Connection', 'close')

        def _send(self, status, body=b'', content_type='application/json; charset=utf-8', headers=None):
            if isinstance(body, str):
                body = body.encode('utf-8')
            self.send_response(status)
            self._security_headers(content_type, len(body))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
            self.close_connection = True

        def _json(self, value, status=200):
            self._send(status, json.dumps(value, ensure_ascii=False), 'application/json; charset=utf-8')

        def _error(self, status, message):
            self._json({'error': message}, status)

        def _host_ok(self):
            port = app.httpd.server_address[1] if app.httpd else None
            return self.headers.get('Host', '') in (f'127.0.0.1:{port}', f'localhost:{port}')

        def _origin_ok(self):
            origin = self.headers.get('Origin')
            if origin is None:
                return True
            port = app.httpd.server_address[1] if app.httpd else None
            return origin in (f'http://127.0.0.1:{port}', f'http://localhost:{port}')

        def _session_ok(self):
            raw = self.headers.get('Cookie', '')
            if not raw:
                return False
            try:
                cookie = SimpleCookie()
                cookie.load(raw)
                value = cookie.get(COOKIE)
                return bool(value and value.value == app.session_id)
            except Exception:
                return False

        def _token_ok(self):
            value = self.headers.get('X-CCN-Token', '')
            return bool(value) and secrets.compare_digest(value, app.token)

        def _read_json(self):
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                raise ApiError(400, '请求长度无效。')
            if length < 0 or length > 1_000_000:
                raise ApiError(413, '请求过大。')
            raw = self.rfile.read(length) if length else b''
            if not raw:
                return {}
            try:
                value = json.loads(raw.decode('utf-8'))
            except (ValueError, UnicodeDecodeError) as exc:
                raise ApiError(400, '请求不是有效的 JSON。') from exc
            if not isinstance(value, dict):
                raise ApiError(400, '请求格式无效。')
            return value

        def _static_path(self, url_path):
            if url_path == '/':
                return app.gui_dir / 'index.html'
            relative = url_path.lstrip('/')
            if not relative or '..' in relative or '\\' in relative or relative.startswith('.'):
                return None
            candidate = (app.gui_dir / relative).resolve()
            if not candidate.is_relative_to(app.gui_dir.resolve()):
                return None
            if candidate.is_file():
                return candidate
            return None

        def _serve_file(self, path, public=False):
            try:
                data = path.read_bytes()
            except OSError:
                self._error(404, '文件不存在。')
                return
            content_type = MIME.get(path.suffix.lower(), 'application/octet-stream')
            self._send(200, data, content_type)

        def do_GET(self):
            if not self._host_ok():
                self._error(421, '主机地址不允许。')
                return
            try:
                parsed = urlsplit(self.path)
                path = parsed.path
                if path == '/api/ping':
                    if self._token_ok():
                        self._json({'ok': True, 'version': VERSION, 'page_ready': app.frontend_ready})
                    else:
                        self._error(403, '未授权。')
                    return
                if path in ('/' + name for name in PUBLIC_FILES):
                    target = self._static_path(path)
                    if target is None:
                        self._error(404, '文件不存在。')
                    else:
                        self._serve_file(target, public=True)
                    return
                if not self._session_ok():
                    if path == '/':
                        token = parse_qs(parsed.query).get('t', [''])[0]
                        if token and secrets.compare_digest(token, app.token):
                            cookie = f'{COOKIE}={app.session_id}; HttpOnly; SameSite=Strict; Path=/'
                            self._send(303, b'', 'text/plain; charset=utf-8', {'Location': '/', 'Set-Cookie': cookie})
                        else:
                            self._error(403, '请从「一键网络.cmd」重新打开界面。')
                    else:
                        self._error(403, '会话无效。')
                    return
                if path == '/':
                    self._serve_file(app.gui_dir / 'index.html')
                    return
                if path == '/api/session':
                    self._json({'token': app.token, 'version': VERSION, 'admin': is_admin()})
                    return
                if path == '/api/state':
                    if parse_qs(parsed.query).get('refresh', ['0'])[0] == '1':
                        with app._lock:
                            app._preview_cache = None
                            app.invalidate_background()
                    app.last_heartbeat = time.monotonic()
                    self._json(app.app_state())
                    return
                if path == '/api/nodes':
                    refresh = parse_qs(parsed.query).get('refresh', ['0'])[0] == '1'
                    self._json(app.nodes(refresh=refresh))
                    return
                if path.startswith('/api/job/'):
                    self._json(app.job_view(path.rsplit('/', 1)[-1]))
                    return
                if path == '/api/log':
                    name = parse_qs(parsed.query).get('name', [''])[0]
                    self._json(app.log_text(name))
                    return
                target = self._static_path(path)
                if target is None:
                    self._error(404, '文件不存在。')
                else:
                    self._serve_file(target)
            except ApiError as exc:
                self._error(exc.status, exc.message)
            except Exception:
                app.log('GET failure: ' + traceback.format_exc())
                self._error(500, '界面服务未完成请求；详情见 .local/gui/server.log。')

        def do_POST(self):
            if not self._host_ok():
                self._error(421, '主机地址不允许。')
                return
            if not self._origin_ok():
                self._error(403, '来源不允许。')
                return
            if not self._session_ok() or not self._token_ok():
                self._error(403, '会话或令牌无效。')
                return
            try:
                parsed = urlsplit(self.path)
                payload = self._read_json()
                if parsed.path == '/api/heartbeat':
                    app.last_heartbeat = time.monotonic()
                    app.frontend_ready = True
                    self._json({'ok': True})
                elif parsed.path == '/api/job':
                    self._json(app.start_job(payload), 202)
                elif parsed.path == '/api/job-cancel':
                    self._json(app.cancel_job(str(payload.get('id') or '')))
                elif parsed.path == '/api/update-settings':
                    self._json(app.save_update_settings(payload))
                elif parsed.path == '/api/browser':
                    self._json(app.browser_action(payload))
                elif parsed.path == '/api/reveal':
                    self._json(app.reveal_local())
                elif parsed.path == '/api/shutdown':
                    with app._lock:
                        active = app.active_job is not None or app._settings_busy
                    if active:
                        raise ApiError(409, '操作仍在进行，完成后再退出界面。')
                    with app._lock:
                        if app.active_job or app._settings_busy:
                            raise ApiError(409, '操作仍在进行，完成后再退出界面。')
                        app._shutdown_requested = True
                    self._json({'ok': True})
                    threading.Timer(0.4, app.stop).start()
                else:
                    self._error(404, '接口不存在。')
            except ApiError as exc:
                self._error(exc.status, exc.message)
            except Exception:
                app.log('POST failure: ' + traceback.format_exc())
                self._error(500, '操作未完成；详情见 .local/gui/server.log。')

        def do_OPTIONS(self):
            self._error(405, '不支持预检请求。')

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description='Claude network GUI (loopback only)')
    parser.add_argument('--open', action='store_true', help='open the GUI in the isolated browser profile')
    parser.add_argument('--no-idle-exit', action='store_true', help='keep serving after the GUI closes (diagnostics)')
    parser.add_argument('--local', help='override .local directory (tests only)')
    parser.add_argument('--gui-dir', help='override gui directory (tests only)')
    args = parser.parse_args(argv)
    app = App(local=args.local, gui_dir=args.gui_dir, open_browser=args.open, idle_exit=not args.no_idle_exit)
    return app.run()


if __name__ == '__main__':
    sys.exit(main())
