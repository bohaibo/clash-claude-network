"""Isolated browser profile for Claude work.

This module never reads or writes the machine's default Chrome/Edge profile,
registry policies or language settings. A dedicated `--user-data-dir` under
`.local/` is created instead, so the user's normal browser stays untouched and
the sandbox can be reset independently.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from checks import require
import region_profile

try:
    import winreg
except ImportError:  # non-Windows hosts (tests still import this module)
    winreg = None

PROJECT = Path(__file__).resolve().parent.parent
LOCAL = PROJECT / '.local'
BROWSER_ROOT = LOCAL / 'browser'
PROFILE = BROWSER_ROOT / 'profile'
SETTINGS = BROWSER_ROOT / 'settings.json'
LAST_LAUNCH = BROWSER_ROOT / 'last-launch.json'
ENGINE_LOG = BROWSER_ROOT / 'engine.log'
GUI_PROFILE = LOCAL / 'gui' / 'browser-profile'

LANGUAGE_RE = re.compile(r'^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})+$')
CHECK_URLS = ('https://ip.net.coffee/claude/', 'https://browserleaks.com/webrtc')

LANGUAGES = tuple(sorted(set(region_profile.all_browser_languages()) | {
    'zh-SG', 'en-SG', 'zh-CN', 'en-US', 'en-GB', 'ja-JP',
}))
ENGINES = ('auto', 'chrome', 'edge')
DEFAULT_SETTINGS = {'engine': 'auto', 'region': 'SG', 'language': 'zh-SG', 'start_url': 'about:blank'}


def valid_language(value):
    return isinstance(value, str) and bool(LANGUAGE_RE.fullmatch(value))
LIMITATIONS = [
    '独立资料目录只隔离浏览器数据；它不是操作系统级沙箱，也不是指纹伪装。',
    'WebRTC、DoH 和语言设置由浏览器启动参数与资料首选项控制，实际效果需在沙箱内自检页观察。',
    '网络仍由 Windows TUN 接管；未验证容器、WSL、其他浏览器和浏览器扩展的行为。',
    '不要在本软件中保存密码或订阅凭据；沙箱资料目录包含登录 Cookie，请按敏感数据保护。',
]
RESTRICTED_MESSAGE = (
    '当前界面进程运行在受限环境（低完整性）中：Windows 不允许它写入 HKCU 注册表，'
    'Chrome 也会在显示窗口前退出。请关闭本界面，再从资源管理器双击「一键网络.cmd」重新打开后重试。'
)
ENGINE_LABELS = {'chrome': 'Chrome', 'edge': 'Edge'}
INTEGRITY_NAMES = {
    'S-1-16-0': 'untrusted',
    'S-1-16-4096': 'low',
    'S-1-16-8192': 'medium',
    'S-1-16-8448': 'medium-plus',
    'S-1-16-12288': 'high',
    'S-1-16-16384': 'system',
    'S-1-16-20480': 'protected',
}
_CACHE_LOCK = threading.Lock()
_PID_CACHE = {}
_SIZE_CACHE = {}


def _root(local=None):
    return Path(local) if local else LOCAL


def browser_root(local=None):
    return _root(local) / 'browser'


def profile_path(local=None):
    return browser_root(local) / 'profile'


def settings_path(local=None):
    return browser_root(local) / 'settings.json'


def gui_profile_path(local=None):
    return _root(local) / 'gui' / 'browser-profile'


def _inside(path, parent):
    try:
        return Path(path).resolve().is_relative_to(Path(parent).resolve())
    except (OSError, ValueError):
        return False


def _integrity_sid():
    """This process's Windows integrity SID ('' when it cannot be read)."""
    if os.name != 'nt':
        return ''
    try:
        import ctypes
        from ctypes import wintypes
        advapi32 = ctypes.WinDLL('advapi32', use_last_error=True)
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        advapi32.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                              ctypes.POINTER(wintypes.HANDLE)]
        advapi32.OpenProcessToken.restype = wintypes.BOOL
        advapi32.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                 wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        advapi32.GetTokenInformation.restype = wintypes.BOOL
        advapi32.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_wchar_p)]
        advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)):
            return ''
        try:
            size = wintypes.DWORD()
            advapi32.GetTokenInformation(token, 25, None, 0, ctypes.byref(size))
            if not size.value:
                return ''
            buffer = ctypes.create_string_buffer(size.value)
            if not advapi32.GetTokenInformation(token, 25, buffer, size, ctypes.byref(size)):
                return ''
            sid = int.from_bytes(buffer.raw[:ctypes.sizeof(ctypes.c_void_p)], 'little')
            text = ctypes.c_wchar_p()
            if not advapi32.ConvertSidToStringSidW(ctypes.c_void_p(sid), ctypes.byref(text)):
                return ''
            try:
                return text.value or ''
            finally:
                kernel32.LocalFree(ctypes.cast(text, ctypes.c_void_p))
        finally:
            kernel32.CloseHandle(token)
    except (AttributeError, OSError, ValueError):
        return ''


def integrity_level():
    """Named Windows integrity level of this process ('' when unknown)."""
    return INTEGRITY_NAMES.get(_integrity_sid(), '')


def restricted_environment():
    """True when Windows denies this process HKCU writes and Chrome startup.

    A low-integrity process cannot write HKCU (reported as "access denied") and
    its Chrome child aborts with STATUS_BREAKPOINT before showing a window.
    Windows derives the integrity level of a process from its image, so a low
    integrity label on the project folder (for example one left by a sandbox
    tool) makes every browser launch and registration fail this way.
    """
    return integrity_level() in ('untrusted', 'low')


def environment_status():
    restricted = restricted_environment()
    return {
        'integrity': integrity_level(),
        'restricted': restricted,
        'message': RESTRICTED_MESSAGE if restricted else '',
    }


def load_settings(local=None):
    path = settings_path(local)
    result = dict(DEFAULT_SETTINGS)
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise ValueError('沙箱浏览器设置文件损坏；未使用默认值覆盖。') from exc
        if not isinstance(stored, dict):
            raise ValueError('沙箱浏览器设置文件格式无效。')
        result.update(stored)
    if result.get('start_url') == 'https://claude.ai/':
        result['start_url'] = 'about:blank'
    result.setdefault('region', 'SG')
    if result.get('engine') not in ENGINES:
        raise ValueError('沙箱浏览器引擎设置无效。')
    if not re.fullmatch(r'[A-Za-z]{2}', str(result.get('region') or '')):
        raise ValueError('沙箱浏览器地区设置无效。')
    result['region'] = str(result['region']).upper()
    if not valid_language(result.get('language')):
        raise ValueError('沙箱浏览器语言设置无效。')
    if not isinstance(result.get('start_url'), str) or not (result['start_url'] == 'about:blank' or result['start_url'].startswith(('http://', 'https://'))):
        raise ValueError('沙箱浏览器起始地址无效。')
    return result


def save_settings(values, local=None):
    current = load_settings(local)
    for key in ('engine', 'region', 'language', 'start_url'):
        if key in values:
            current[key] = values[key]
    if current['engine'] not in ENGINES:
        raise ValueError('沙箱浏览器引擎设置无效。')
    if not re.fullmatch(r'[A-Za-z]{2}', str(current.get('region') or '')):
        raise ValueError('沙箱浏览器地区设置无效。')
    current['region'] = str(current['region']).upper()
    if not valid_language(current.get('language')):
        raise ValueError('沙箱浏览器语言设置无效。')
    if not isinstance(current['start_url'], str) or not (current['start_url'] == 'about:blank' or current['start_url'].startswith(('http://', 'https://'))):
        raise ValueError('沙箱浏览器起始地址无效。')
    root = browser_root(local)
    root.mkdir(parents=True, exist_ok=True)
    target = settings_path(local)
    temporary = target.with_name(target.name + '.new')
    temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(target)
    return current


def engine_paths(environment=None):
    env = os.environ if environment is None else environment
    program_files = env.get('ProgramFiles', r'C:\Program Files')
    program_files_x86 = env.get('ProgramFiles(x86)', r'C:\Program Files (x86)')
    local_app_data = env.get('LOCALAPPDATA', '')
    rows = []
    for root in filter(None, (program_files, program_files_x86, local_app_data)):
        rows.append(('chrome', Path(root) / 'Google' / 'Chrome' / 'Application' / 'chrome.exe'))
    for root in filter(None, (program_files, program_files_x86, local_app_data)):
        rows.append(('edge', Path(root) / 'Microsoft' / 'Edge' / 'Application' / 'msedge.exe'))
    seen = set()
    unique = []
    for name, path in rows:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append((name, path))
    return unique


def available_engines(environment=None):
    result = {}
    for name, path in engine_paths(environment):
        if path.is_file() and name not in result:
            result[name] = str(path)
    return result


def find_engine(preference='auto', environment=None):
    found = available_engines(environment)
    if preference in ('chrome', 'edge'):
        if preference in found:
            return {'name': preference, 'path': found[preference]}
        return None
    for name in ('chrome', 'edge'):
        if name in found:
            return {'name': name, 'path': found[name]}
    return None


def _read_preferences(profile):
    path = Path(profile) / 'Default' / 'Preferences'
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError('沙箱浏览器资料首选项损坏；未覆盖，请重置沙箱资料后重试。') from exc
    if not isinstance(value, dict):
        raise ValueError('沙箱浏览器资料首选项格式无效；未覆盖。')
    return value


def _write_preferences(profile, preferences):
    folder = Path(profile) / 'Default'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'Preferences'
    temporary = path.with_name(path.name + '.ccn-new')
    temporary.write_text(json.dumps(preferences, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    temporary.replace(path)


def apply_preferences(profile, language):
    """Only managed keys are changed; all unrelated browser data is preserved."""
    require(valid_language(language), '沙箱浏览器语言设置无效。')
    preferences = _read_preferences(profile)
    preferences.setdefault('dns_over_https', {})
    if not isinstance(preferences['dns_over_https'], dict):
        raise ValueError('沙箱浏览器 DoH 首选项格式无效；未覆盖。')
    preferences['dns_over_https'].update({'mode': 'off', 'templates': ''})
    preferences.setdefault('webrtc', {})
    if not isinstance(preferences['webrtc'], dict):
        raise ValueError('沙箱浏览器 WebRTC 首选项格式无效；未覆盖。')
    preferences['webrtc'].update({
        'ip_handling_policy': 'disable_non_proxied_udp',
        'multiple_routes_enabled': False,
        'nonproxied_udp_enabled': False,
    })
    preferences.setdefault('intl', {})
    if not isinstance(preferences['intl'], dict):
        raise ValueError('沙箱浏览器语言首选项格式无效；未覆盖。')
    preferences['intl'].update({
        'accept_languages': language + ',' + language.split('-')[0],
        'selected_languages': language + ',' + language.split('-')[0],
    })
    preferences.setdefault('browser', {})
    if not isinstance(preferences['browser'], dict):
        raise ValueError('沙箱浏览器首选项格式无效；未覆盖。')
    preferences['browser'].update({'has_seen_welcome_page': True, 'check_default_browser': False})
    preferences.setdefault('signin', {})
    if not isinstance(preferences['signin'], dict):
        raise ValueError('沙箱浏览器登录首选项格式无效；未覆盖。')
    preferences['signin']['allowed'] = False
    preferences.setdefault('sync', {})
    if not isinstance(preferences['sync'], dict):
        raise ValueError('沙箱浏览器同步首选项格式无效；未覆盖。')
    preferences['sync']['requested'] = False
    _write_preferences(profile, preferences)
    return preferences


def build_arguments(exe, profile, language, url, app=False, gui=False):
    require(valid_language(language), '沙箱浏览器语言设置无效。')
    args = [
        '--user-data-dir=' + str(profile),
        '--profile-directory=Default',
        '--no-first-run',
        '--no-default-browser-check',
        '--disable-session-crashed-bubble',
        '--disable-sync',
        '--disable-background-mode',
        '--no-service-autorun',
        '--disable-background-networking',
    ]
    # Edge signs the isolated profile into the Windows Microsoft account and
    # offers to sync it; that is never wanted for a throwaway sandbox profile.
    signin = 'msEdgeIdentity,msEdgeSync,msImplicitSignin,SigninInterception'
    if gui:
        # The panel must look like a desktop tool, not a browser tab: app mode
        # has no tab strip, address bar or bookmarks. RendererAppContainer is
        # disabled for the same reason as before: some Windows security products
        # block that sandbox layer, which leaves the window blank.
        args += ['--app=' + url, '--window-size=1280,880', '--disable-extensions',
                 '--disable-features=' + signin + ',RendererAppContainer']
        return args
    args += [
        '--lang=' + language,
        '--accept-lang=' + language + ',' + language.split('-')[0] + ',zh-CN,en',
        '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
        '--disable-non-proxied-udp',
        '--disable-quic',
        '--disable-features=' + signin + ',Translate',
    ]
    if app:
        args.append('--app=' + url)
    else:
        args += ['--new-window', url]
    return args


def _pid_script():
    return (
        "$ErrorActionPreference='Stop';"
        "if(!$env:CCN_BROWSER_PROFILE){exit 0};"
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe' OR Name='msedge.exe'\" "
        "-ErrorAction SilentlyContinue | "
        "Where-Object { $_.CommandLine -and "
        "$_.CommandLine.IndexOf($env:CCN_BROWSER_PROFILE,[StringComparison]::OrdinalIgnoreCase) -ge 0 } | "
        "ForEach-Object { [int]$_.ProcessId }"
    )


def running_pids(profile, use_cache=True, timeout=8):
    profile = Path(profile)
    key = str(profile).lower()
    now = time.monotonic()
    if use_cache:
        with _CACHE_LOCK:
            cached = _PID_CACHE.get(key)
        if cached and now - cached[0] < 2.5:
            return list(cached[1])
    environment = dict(os.environ, CCN_BROWSER_PROFILE=str(profile))
    pids = []
    try:
        result = subprocess.run(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', _pid_script()],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=timeout, env=environment,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.isdigit():
                    pids.append(int(line))
    except (OSError, subprocess.SubprocessError):
        pids = []
    pids = sorted(set(pids))
    with _CACHE_LOCK:
        _PID_CACHE[key] = (now, pids)
    return pids


def _clear_cache(profile):
    with _CACHE_LOCK:
        _PID_CACHE.pop(str(profile).lower(), None)


def _directory_size(path):
    key = str(path).lower()
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _SIZE_CACHE.get(key)
    if cached and now - cached[0] < 30:
        return cached[1]
    total = 0
    try:
        for item in Path(path).rglob('*'):
            try:
                if item.is_file():
                    total += item.stat().st_size
            except OSError:
                continue
    except OSError:
        total = 0
    with _CACHE_LOCK:
        _SIZE_CACHE[key] = (now, total)
    return total


def _log_tail(path, limit=800, start=0):
    # Only bytes appended by this launch may be reported as the failure cause;
    # reusing the whole log reported an old, unrelated error as the diagnosis.
    try:
        with open(path, 'rb') as stream:
            stream.seek(max(0, start))
            data = stream.read()
    except OSError:
        return ''
    return data[-limit:].decode('utf-8', errors='replace')


def _launch(exe, args, log_path):
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    stream = log_path.open('ab')
    try:
        # The GUI server is hidden by its launcher. Do not let the browser
        # inherit SW_HIDE: a running browser process need not have a window.
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 1  # SW_SHOWNORMAL
        process = subprocess.Popen(
            [exe] + args,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=stream,
            close_fds=True,
            startupinfo=startupinfo,
            creationflags=getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0),
        )
    finally:
        stream.close()
    return process


def _shell_launch(exe, args):
    # Let the desktop shell start the browser. A direct child of the hidden
    # service can start its browser process but fail to start its renderers.
    # Explorer supplies the normal interactive desktop launch environment.
    try:
        command = subprocess.list2cmdline([str(value) for value in args])
        environment = dict(os.environ, CCN_BROWSER_EXE=str(exe),
                           CCN_BROWSER_ARGS=command, CCN_BROWSER_CWD=str(Path(exe).parent))
        result = subprocess.run(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
             "$ErrorActionPreference='Stop';"
             "$shell=New-Object -ComObject Shell.Application;"
             "$shell.ShellExecute($env:CCN_BROWSER_EXE,$env:CCN_BROWSER_ARGS,$env:CCN_BROWSER_CWD,'open',1)"],
            env=environment, capture_output=True, timeout=15,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        return 33 if result.returncode == 0 else 0
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0


def _record_launch(profile, found, pid, target, already, app, gui, requested=None):
    result = {
        'started': True,
        'already_running': bool(already),
        'pid': pid,
        'engine': found['name'],
        'requested_engine': requested or found['name'],
        'profile': str(profile),
        'url': target,
        'app': bool(app or gui),
    }
    try:
        LAST_LAUNCH.parent.mkdir(parents=True, exist_ok=True)
        LAST_LAUNCH.write_text(json.dumps({**result, 'time': time.strftime('%Y-%m-%dT%H:%M:%S%z')}, ensure_ascii=False, indent=2), encoding='utf-8')
    except OSError:
        pass
    return result


def visible_window_count(pids):
    """Count visible top-level windows belonging to this isolated profile."""
    if not pids:
        return 0
    if os.name != 'nt':
        return len(pids)
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    matches = []
    def visit(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd):
            matches.append(hwnd)
        return True
    callback = callback_type(visit)
    if not user32.EnumWindows(callback, 0):
        raise OSError('无法核实浏览器窗口是否可见。')
    return len(matches)


def _engine_attempt(found, profile, language, target, app, gui, wait, already, log_path, requested=None):
    previous_windows = visible_window_count(already) if already and not (gui or app) else 0
    def live_pids():
        pids = running_pids(profile, use_cache=False)
        if pids and not already:
            # A crashing engine can briefly expose a PID before exiting.
            # Let that failure reach the alternate-engine path.
            time.sleep(1)
            pids = running_pids(profile, use_cache=False)
        return pids if visible_window_count(pids) > previous_windows else []

    args = build_arguments(found['path'], profile, language, target, app=app, gui=gui)
    start = log_path.stat().st_size if log_path.exists() else 0
    if os.name == 'nt' and _shell_launch(found['path'], args) > 32:
        deadline = time.monotonic() + max(0, wait)
        while time.monotonic() < deadline:
            pids = live_pids()
            if pids:
                _clear_cache(profile)
                return _record_launch(profile, found, pids[0], target, already, app, gui, requested), ''
            time.sleep(0.25)
    process = _launch(found['path'], args, log_path)
    deadline = time.monotonic() + max(0, wait)
    while time.monotonic() < deadline:
        if live_pids():
            _clear_cache(profile)
            return _record_launch(profile, found, process.pid, target, already, app, gui, requested), ''
        if process.poll() is not None and not already:
            break
        time.sleep(0.25)
    if not already and not running_pids(profile, use_cache=False) and _shell_launch(found['path'], args) > 32:
        deadline = time.monotonic() + max(0, wait)
        while time.monotonic() < deadline:
            pids = live_pids()
            if pids:
                _clear_cache(profile)
                return _record_launch(profile, found, pids[0], target, False, app, gui, requested), ''
            time.sleep(0.25)
    if live_pids():
        _clear_cache(profile)
        return _record_launch(profile, found, process.pid, target, True, app, gui, requested), ''
    code = process.poll()
    if code not in (None, 0) and not already:
        # STATUS_BREAKPOINT from a browser process means it aborted before it
        # could open a window; report the code instead of a mute timeout.
        return None, '{} 在创建窗口前退出（退出代码 0x{:08X}）；后台进程不代表打开成功。'.format(
            ENGINE_LABELS.get(found['name'], found['name']), code & 0xFFFFFFFF)
    return None, _log_tail(log_path, start=start) or '浏览器未创建可见的新窗口；后台进程不代表打开成功。'


def launch(url=None, local=None, app=False, gui=False, engine=None, language=None, wait=8):
    settings = load_settings(local)
    preference = engine or settings['engine']
    found = find_engine(preference)
    # GUI availability must not depend on the Claude browser preference.
    # Prefer the Windows-provided engine for the local app window.
    if gui and engine is None:
        found = find_engine('edge') or find_engine('chrome')
    if not found:
        raise RuntimeError('未找到可用的 Chrome 或 Edge。请先安装其中一个；本软件不会自动下载浏览器。')
    selected_language = language or settings['language']
    profile = gui_profile_path(local) if gui else profile_path(local)
    profile.parent.mkdir(parents=True, exist_ok=True)
    target = url or ('http://127.0.0.1/' if gui else settings['start_url'])
    if not isinstance(target, str) or not (target == 'about:blank' or target.startswith(('http://', 'https://', 'file://'))):
        raise ValueError('浏览器地址无效。')
    already = running_pids(profile, use_cache=False)
    if not already and not gui:
        apply_preferences(profile, selected_language)
    log_path = _root(local) / ('gui' if gui else 'browser') / 'engine.log'
    candidates = [found]
    # A pending vendor update can leave one installed engine unable to start a
    # fresh profile. The profile is engine independent, so try the other one
    # once instead of failing the page the user asked for.
    other = find_engine('edge' if found['name'] == 'chrome' else 'chrome')
    if other and str(other.get('path', '')).lower() != str(found.get('path', '')).lower():
        candidates.append(other)
    details = []
    for candidate in candidates:
        result, detail = _engine_attempt(candidate, profile, selected_language, target, app, gui,
                                         wait, already, log_path, preference)
        if result:
            return result
        if detail:
            details.append(detail.splitlines()[-1])
    detail = details[-1] if details else ''
    hint = ' ' + RESTRICTED_MESSAGE if restricted_environment() else ''
    raise RuntimeError('浏览器启动失败。' + ((' 诊断：' + detail) if detail else ' 请检查浏览器完整性。') + hint)


def close(local=None, profile=None):
    root = _root(local)
    target = Path(profile) if profile else profile_path(local)
    require(_inside(target, root), '只能关闭本项目资料目录内的浏览器。')
    pids = running_pids(target, use_cache=False)
    if not pids:
        return {'closed': 0, 'running': False}
    for pid in pids:
        try:
            subprocess.run(['taskkill.exe', '/PID', str(pid), '/T', '/F'], capture_output=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            continue
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not running_pids(target, use_cache=False):
            _clear_cache(target)
            return {'closed': len(pids), 'running': False}
        time.sleep(0.3)
    _clear_cache(target)
    remaining = running_pids(target, use_cache=False)
    if remaining:
        raise RuntimeError('沙箱浏览器仍在运行；未强制结束失败的进程，请手动关闭后重试。')
    return {'closed': len(pids), 'running': False}


def reset(local=None, confirm=''):
    require(confirm == 'DELETE', '重置沙箱资料需要明确确认。')
    target = profile_path(local)
    require(_inside(target, _root(local)), '资料目录不在本项目内，拒绝删除。')
    if running_pids(target, use_cache=False):
        raise RuntimeError('请先关闭沙箱浏览器，再重置资料。')
    if not target.exists():
        return {'reset': True, 'profile_existed': False}
    trash = browser_root(local) / ('trash-' + time.strftime('%Y%m%d-%H%M%S'))
    if trash.exists():
        trash = browser_root(local) / ('trash-' + time.strftime('%Y%m%d-%H%M%S') + '-' + str(os.getpid()))
    target.replace(trash)
    _clear_cache(target)
    try:
        shutil.rmtree(trash)
    except OSError as exc:
        raise RuntimeError('资料已移到 ' + str(trash) + '，但删除未完成；请手动删除该目录。') from exc
    return {'reset': True, 'profile_existed': True}


def configure_region(region, local=None):
    profile = region_profile.get(region)
    current = load_settings(local)
    language = profile['browser_language']
    saved = save_settings({'region': profile['region'], 'language': language}, local)
    launcher = launcher_path(local)
    if launcher.exists():
        try:
            write_launcher(local)
        except (OSError, RuntimeError, ValueError):
            pass
    running = bool(running_pids(profile_path(local), use_cache=False))
    applied = False
    target = profile_path(local)
    if not running and target.exists():
        apply_preferences(target, language)
        applied = True
    return {
        'region': profile['region'],
        'known': profile['known'],
        'label': profile['label'],
        'language': language,
        'timezone': profile['iana'],
        'timezone_id': profile['timezone_id'],
        'culture': profile['culture'],
        'running': running,
        'applied_to_profile': applied,
        'settings': saved,
    }


def status(local=None):
    settings = load_settings(local)
    found = find_engine(settings['engine'])
    available = available_engines()
    profile = profile_path(local)
    pids = running_pids(profile)
    return {
        'settings': settings,
        'region': settings['region'],
        'region_profile': region_profile.get(settings['region']),
        'timezone': region_profile.get(settings['region'])['iana'],
        'engine': {'name': found['name'] if found else None, 'path': found['path'] if found else None},
        'available': available,
        'profile': {
            'path': str(profile.relative_to(_root(local))),
            'exists': profile.exists(),
            'bytes': _directory_size(profile) if profile.exists() else 0,
        },
        'running': bool(pids),
        'pids': pids,
        'languages': list(LANGUAGES),
        'limitations': LIMITATIONS,
        'environment': environment_status(),
        'touches_default_browser': False,
    }


def open_environment_checks(local=None):
    opened = []
    engine = None
    for url in CHECK_URLS:
        try:
            result = launch(url, local=local, engine=engine)
        except (OSError, RuntimeError, ValueError) as exc:
            if opened:
                raise RuntimeError('第一个检测网站已打开，第二个打开失败，请重试。') from exc
            raise
        engine = result.get('engine') or engine
        opened.append(url)
    return {'opened': opened, 'engine': engine}


def open_check_page(base_url, local=None, region=None, language=None, timezone=None):
    require(isinstance(base_url, str) and base_url.startswith('http://127.0.0.1:'), '自检页地址无效。')
    from urllib.parse import urlencode
    query = {}
    if isinstance(region, str) and region:
        query['region'] = region
    if isinstance(language, str) and language:
        query['lang'] = language
    if isinstance(timezone, str) and timezone:
        query['tz'] = timezone
    url = base_url.rstrip('/') + '/sandbox-check.html'
    if query:
        url += '?' + urlencode(query)
    return launch(url, local=local, app=False)


# ---- optional Windows default-browser registration -----------------------

DEFAULT_APP = 'CCN-SandboxBrowser'
DEFAULT_NAME = 'Claude 沙箱浏览器'
DEFAULT_SETTINGS_URI = 'ms-settings:defaultapps?registeredAppUser=' + DEFAULT_APP


def launcher_path(local=None):
    return browser_root(local) / 'launch-sandbox.vbs'


def _vbs_quote(value):
    return 'Chr(34) & "' + str(value).replace('"', '""') + '" & Chr(34)'


def write_launcher(local=None):
    settings = load_settings(local)
    found = find_engine(settings['engine'])
    if not found:
        raise RuntimeError('未找到 Chrome 或 Edge，无法创建默认浏览器启动器。')
    profile = profile_path(local)
    profile.parent.mkdir(parents=True, exist_ok=True)
    target = launcher_path(local)
    language = settings['language']
    command = (
        'Dim shell, url, command\r\n'
        'Set shell = CreateObject("WScript.Shell")\r\n'
        'url = "' + settings['start_url'] + '"\r\n'
        'If WScript.Arguments.Count > 0 Then url = WScript.Arguments(0)\r\n'
        'command = ' + _vbs_quote(found['path']) +
        ' & " --user-data-dir=" & ' + _vbs_quote(profile) +
        ' & " --profile-directory=Default --no-first-run --no-default-browser-check --disable-session-crashed-bubble --disable-sync --disable-background-mode --disable-features=SigninInterception,Translate --lang=' + language +
        ' --accept-lang=' + language + ',' + language.split('-')[0] + ',zh-CN,en --force-webrtc-ip-handling-policy=disable_non_proxied_udp --disable-non-proxied-udp --disable-quic --no-service-autorun " & Chr(34) & url & Chr(34)\r\n'
        'shell.Run command, 1, False\r\n'
    )
    target.write_bytes(b'\xff\xfe' + command.encode('utf-16-le'))
    return {'launcher': str(target), 'engine': found['name'], 'profile': str(profile)}


def _default_command(local=None):
    launcher = launcher_path(local)
    wscript = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'wscript.exe'
    return '"' + str(wscript) + '" //B "' + str(launcher) + '" "%1"'


def default_status(local=None):
    if winreg is None:
        return {'supported': False, 'registered': False, 'is_default': False, 'current': '',
                'can_register': False, 'note': '仅 Windows 支持。'}
    current = ''
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r'Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice') as key:
            current = str(winreg.QueryValueEx(key, 'ProgId')[0])
    except OSError:
        current = ''
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\RegisteredApplications') as key:
            winreg.QueryValueEx(key, DEFAULT_APP)
        registered = True
    except OSError:
        registered = False
    restricted = restricted_environment()
    return {
        'supported': True,
        'registered': registered,
        'is_default': current == DEFAULT_APP,
        'current': current,
        'can_register': not restricted,
        'restricted_reason': RESTRICTED_MESSAGE if restricted else '',
        'settings_uri': DEFAULT_SETTINGS_URI,
        'note': 'Windows 要求在“设置 → 默认应用”中手动确认；注册只写入当前用户，不修改系统级关联。',
    }


def register_default(local=None):
    if winreg is None:
        raise RuntimeError('仅 Windows 支持设为默认浏览器。')
    if restricted_environment():
        raise RuntimeError('无法注册默认浏览器：' + RESTRICTED_MESSAGE)
    info = write_launcher(local)
    command = _default_command(local)
    found = find_engine(load_settings(local)['engine'])
    icon = (found['path'] + ',0') if found else ''
    base = 'Software\\Clients\\StartMenuInternet\\' + DEFAULT_APP
    classes = 'Software\\Classes\\' + DEFAULT_APP
    capabilities = 'Software\\' + DEFAULT_APP + '\\Capabilities'
    steps = [
        (base, '应用名称', '', DEFAULT_NAME),
        (base + '\\shell\\open\\command', '启动命令', '', command),
        (base + '\\DefaultIcon', '应用图标', '', icon),
        (classes + '\\shell\\open\\command', 'ProgID 启动命令', '', command),
        (classes + '\\Application', 'ProgID 应用名称', 'ApplicationName', DEFAULT_NAME),
        (classes + '\\Application', 'ProgID 应用图标', 'ApplicationIcon', icon),
        (capabilities, '应用能力名称', 'ApplicationName', DEFAULT_NAME),
        (capabilities, '应用能力描述', 'ApplicationDescription', 'Claude 独立资料目录浏览器'),
        (capabilities, '应用能力图标', 'ApplicationIcon', icon),
        (capabilities + '\\URLAssociations', 'http 关联', 'http', DEFAULT_APP),
        (capabilities + '\\URLAssociations', 'https 关联', 'https', DEFAULT_APP),
        ('Software\\RegisteredApplications', '已注册应用', DEFAULT_APP, capabilities),
    ]
    for path, label, name, value in steps:
        try:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        except OSError as exc:
            # Name the exact key: Windows reports only "access denied" for
            # policy-protected keys, which is not actionable on its own.
            raise RuntimeError(
                '无法写入当前用户的“{}”注册项（{}）：{}。注册表写入可能被安全软件或组策略限制。'.format(
                    label, path, exc)) from exc
    try:
        os.startfile(DEFAULT_SETTINGS_URI)
        opened = True
    except OSError:
        opened = False
    status = default_status(local)
    status.update({'settings_opened': opened, 'launcher': info['launcher']})
    return status


def open_default_settings():
    try:
        os.startfile(DEFAULT_SETTINGS_URI)
        return {'opened': True, 'settings_uri': DEFAULT_SETTINGS_URI}
    except OSError as exc:
        raise RuntimeError('无法打开 Windows 默认应用设置。') from exc


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--configure-region')
    args = parser.parse_args()
    if not args.configure_region:
        parser.error('--configure-region is required')
    print(json.dumps(configure_region(args.configure_region), ensure_ascii=False))
