"""Local management window only. No browsing, profile settings or default-app registration."""
from __future__ import annotations
import json, os, re, subprocess, threading, time
from pathlib import Path
from checks import require
try:
    import winreg
except ImportError:
    winreg = None
LOCAL = Path(__file__).resolve().parent.parent / '.local'
_CACHE_LOCK = threading.Lock()
_PID_CACHE = {}
ENGINE_LABELS = {'chrome':'Chrome','edge':'Edge'}
RESTRICTED_MESSAGE = (
    '当前界面进程运行在受限环境（低完整性）中：Windows 不允许它写入 HKCU 注册表，'
    'Chrome 也会在显示窗口前退出。请关闭本界面，再从资源管理器双击「一键网络.cmd」重新打开后重试。'
)
INTEGRITY_NAMES = {
    'S-1-16-0': 'untrusted',
    'S-1-16-4096': 'low',
    'S-1-16-8192': 'medium',
    'S-1-16-8448': 'medium-plus',
    'S-1-16-12288': 'high',
    'S-1-16-16384': 'system',
    'S-1-16-20480': 'protected',
}


def _root(local=None):
    return Path(local) if local else LOCAL

def gui_profile_path(local=None):
    return _root(local) / 'gui' / 'browser-profile'

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

def build_arguments(exe, profile, language, url, app=True, gui=True):
    return ['--user-data-dir=' + str(profile), '--profile-directory=Default',
            '--no-first-run', '--no-default-browser-check', '--disable-sync',
            '--disable-background-mode', '--disable-background-networking',
            '--disable-extensions', '--app=' + url, '--window-size=1280,880',
            '--disable-features=msEdgeIdentity,msEdgeSync,msImplicitSignin,SigninInterception,RendererAppContainer']


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
        (profile.parent / "last-launch.json").parent.mkdir(parents=True, exist_ok=True)
        (profile.parent / "last-launch.json").write_text(json.dumps({**result, 'time': time.strftime('%Y-%m-%dT%H:%M:%S%z')}, ensure_ascii=False, indent=2), encoding='utf-8')
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

def launch(url, local=None, app=True, gui=True, wait=8):
    from urllib.parse import urlsplit
    parsed = urlsplit(url)
    require(parsed.scheme == 'http' and parsed.hostname == '127.0.0.1' and parsed.port is not None
            and not parsed.username and not parsed.password, '管理窗口只允许本机界面地址。')
    profile = gui_profile_path(local)
    profile.parent.mkdir(parents=True, exist_ok=True)
    already = running_pids(profile, use_cache=False)
    candidates = [e for e in (find_engine('edge'), find_engine('chrome')) if e]
    require(candidates, '管理窗口需要本机已有的 Edge 或 Chrome。')
    details = []
    for found in candidates:
        result, detail = _engine_attempt(found, profile, '', url, True, True, wait, already,
                                        _root(local) / 'gui' / 'engine.log', 'auto')
        if result:
            return result
        details.append(detail)
    raise RuntimeError('管理窗口无法打开：' + (details[-1] if details else '没有可用引擎'))


def close(local=None, profile=None):
    root = _root(local)
    target = Path(profile) if profile else gui_profile_path(local)
    require(target.resolve() == gui_profile_path(local).resolve(), '只能关闭本项目资料目录内的浏览器。')
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
        raise RuntimeError('管理窗口仍在运行；未强制结束失败的进程，请手动关闭后重试。')
    return {'closed': len(pids), 'running': False}
