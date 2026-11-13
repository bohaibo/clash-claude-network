"""Reversible WebRTC policies; installation inspection is strictly read-only."""
import argparse
import copy
import hashlib
import json
import os
import re
from pathlib import Path
import webrtc_bundle

LOCAL_EXTENSION_ID = 'emjiefookpomoonibkiaighloejfambf'

RECORD = 'webrtc-policies.json'
VALUE = 'disable_non_proxied_udp'
TARGETS = [
    ('Chrome', r'SOFTWARE\Policies\Google\Chrome', 'WebRtcIPHandling'),
    ('Chrome', r'SOFTWARE\Policies\Google\Chrome', 'WebRtcIPHandlingUrl'),
    ('Edge', r'SOFTWARE\Policies\Microsoft\Edge', 'WebRtcLocalhostIpHandling'),
    ('Edge', r'SOFTWARE\Policies\Microsoft\Edge', 'WebRtcIPHandlingUrl'),
    ('Firefox', r'SOFTWARE\Policies\Mozilla\Firefox', 'Preferences'),
]
PREF = 'media.peerconnection.enabled'
LOCK = {'Value': False, 'Status': 'locked'}
EXTENSION_RECORD = 'webrtc-extensions.json'
AUTO_EXTENSION_RECORD = 'webrtc-auto-extensions.json'
EXTENSIONS = [
    ('Chrome', r'SOFTWARE\Policies\Google\Chrome\ExtensionInstallForcelist',
     'fjkmabmdepjfammlpliljpnbhleegehm;https://clients2.google.com/service/update2/crx'),
    ('Edge', r'SOFTWARE\Policies\Microsoft\Edge\ExtensionInstallForcelist',
     'eepeadgljpkkjpbfecfkijnnliikglpl;https://edge.microsoft.com/extensionwebstorebase/v1/crx'),
]

class Registry:
    def __init__(self):
        import winreg
        self.w = winreg
    def read(self, path, name):
        w = self.w
        try:
            with w.OpenKey(w.HKEY_LOCAL_MACHINE, path, 0, w.KEY_READ | w.KEY_WOW64_64KEY) as key:
                value, kind = w.QueryValueEx(key, name)
        except FileNotFoundError:
            return {'exists': False}
        if kind not in (w.REG_SZ, w.REG_EXPAND_SZ, w.REG_DWORD, w.REG_MULTI_SZ):
            raise ValueError('Unsupported existing WebRTC policy type')
        return {'exists': True, 'kind': kind, 'value': value}
    def preflight(self):
        # Firefox does not support mixing the old Preferences subkey with JSON.
        w = self.w
        try:
            with w.OpenKey(w.HKEY_LOCAL_MACHINE, r'SOFTWARE\Policies\Mozilla\Firefox\Preferences', 0, w.KEY_READ | w.KEY_WOW64_64KEY) as key:
                subkeys, values, _ = w.QueryInfoKey(key)
                if subkeys or values:
                    raise ValueError('Firefox has legacy Preferences policies; migrate them before applying WebRTC protection')
        except FileNotFoundError:
            pass

    def list_values(self, path):
        w = self.w
        try:
            with w.OpenKey(w.HKEY_LOCAL_MACHINE, path, 0, w.KEY_READ | w.KEY_WOW64_64KEY) as key:
                return {w.EnumValue(key, i)[0]: self.read(path, w.EnumValue(key, i)[0])
                        for i in range(w.QueryInfoKey(key)[1])}
        except FileNotFoundError:
            return {}

    def write(self, path, name, entry):
        w = self.w
        if entry['exists']:
            with w.CreateKeyEx(w.HKEY_LOCAL_MACHINE, path, 0, w.KEY_WRITE | w.KEY_WOW64_64KEY) as key:
                w.SetValueEx(key, name, 0, entry['kind'], entry['value'])
        else:
            try:
                with w.OpenKey(w.HKEY_LOCAL_MACHINE, path, 0, w.KEY_SET_VALUE | w.KEY_WOW64_64KEY) as key:
                    try: w.DeleteValue(key, name)
                    except FileNotFoundError: pass
            except FileNotFoundError: pass
        if self.read(path, name) != entry:
            raise RuntimeError('WebRTC policy write verification failed')


def preferences(entry):
    if not entry['exists']: return {}
    if entry.get('kind') != 7 or not isinstance(entry.get('value'), list):
        raise ValueError('Firefox Preferences policy must be REG_MULTI_SZ JSON')
    result = json.loads('\n'.join(entry['value']))
    if not isinstance(result, dict): raise ValueError('Invalid Firefox policy JSON')
    return result


def desired(name, current):
    if name == 'Preferences':
        value = preferences(current)
        value[PREF] = copy.deepcopy(LOCK)
        return {'exists': True, 'kind': 7, 'value': [json.dumps(value, ensure_ascii=False)]}
    value = json.dumps([{'url': '*', 'handling': VALUE}]) if name.endswith('Url') else VALUE
    return {'exists': True, 'kind': 1, 'value': value}


def validate(state):
    if state.get('version') != 1 or len(state.get('entries', [])) != len(TARGETS):
        raise ValueError('Invalid WebRTC recovery record')
    for saved, (browser, path, name) in zip(state['entries'], TARGETS):
        if saved.get('path') != path or saved.get('name') != name or saved.get('browser') != browser:
            raise ValueError('Unexpected WebRTC recovery target')
        entry = saved['before']
        if type(entry.get('exists')) is not bool: raise ValueError('Invalid policy state')
        if entry['exists']:
            kind, value = entry.get('kind'), entry.get('value')
            valid = (kind in (1, 2) and isinstance(value, str) or kind == 4 and type(value) is int
                     or kind == 7 and isinstance(value, list) and all(isinstance(v, str) for v in value))
            if not valid: raise ValueError('Invalid policy value')
        if name == 'Preferences': preferences(entry)
    return state


def snapshot(directory, registry):
    path = Path(directory) / RECORD
    if path.exists(): return validate(json.loads(path.read_text(encoding='utf-8')))
    manifest = Path(directory) / 'manifest.json'
    if manifest.exists() and RECORD in json.loads(manifest.read_text(encoding='utf-8')).get('extra_sha256', {}):
        raise ValueError('Sealed WebRTC recovery record is missing; do not recapture current policies')
    state = {'version': 1, 'entries': [{'browser': b, 'path': p, 'name': n, 'before': registry.read(p, n)} for b, p, n in TARGETS]}
    validate(state)
    # Refuse to overwrite an earlier recovery record.
    with path.open('x', encoding='utf-8') as stream: json.dump(state, stream, ensure_ascii=False, indent=2)
    return state


def load_sealed(directory):
    directory = Path(directory)
    path = directory / RECORD
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    expected = manifest.get('extra_sha256', {}).get(RECORD)
    if not expected or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError('WebRTC recovery record is not sealed or was changed')
    return validate(json.loads(path.read_text(encoding='utf-8')))


def validate_extensions(state):
    if state.get('version') not in (1, 2, 3) or len(state.get('entries', [])) != len(EXTENSIONS):
        raise ValueError('Invalid WebRTC extension recovery record')
    if state['version'] == 2 and state.get('read_only') is not True:
        raise ValueError('Offline extension record must be read-only')
    for entry, (browser, path, value) in zip(state['entries'], EXTENSIONS):
        if (entry.get('browser') != browser or entry.get('path') != path or entry.get('managed') != value
                or not re.fullmatch(r'[1-9][0-9]{0,4}', entry.get('name', ''))):
            raise ValueError('Invalid managed extension target')
        before = entry.get('before', {})
        if type(before.get('exists')) is not bool:
            raise ValueError('Invalid extension backup')
        if before['exists'] and (before.get('kind') != 1 or not isinstance(before.get('value'), str)
                                or before['value'].split(';')[0] != value.split(';')[0]):
            raise ValueError('Extension backup would overwrite another add-on')
    return state


def snapshot_extensions(directory, registry, record=EXTENSION_RECORD):
    if record not in (EXTENSION_RECORD, AUTO_EXTENSION_RECORD):
        raise ValueError('Unexpected extension recovery filename')
    file = Path(directory) / record
    if file.exists():
        return validate_extensions(json.loads(file.read_text(encoding='utf-8')))
    manifest = Path(directory) / 'manifest.json'
    if manifest.exists() and record in json.loads(manifest.read_text(encoding='utf-8')).get('extra_sha256', {}):
        raise ValueError('Sealed extension recovery record is missing; do not recapture current policies')
    entries = []
    for browser, path, value in EXTENSIONS:
        values = registry.list_values(path)
        matches = [name for name, entry in values.items()
                   if str(entry.get('value', '')).split(';')[0] == value.split(';')[0]]
        if len(matches) > 1:
            raise ValueError('Duplicate WebRTC extension policies require review')
        name = matches[0] if matches else next(str(i) for i in range(1, 10000) if str(i) not in values)
        entries.append(dict(browser=browser, path=path, name=name, managed=value,
                            before=registry.read(path, name)))
    state = validate_extensions({'version': 3, 'entries': entries})
    with file.open('x', encoding='utf-8') as stream:
        json.dump(state, stream, ensure_ascii=False, indent=2)
    return state


def snapshot_extension_installation(directory, registry):
    state = snapshot_extensions(directory, registry)
    # An offline-only baseline owns no slots. Capture a separate first record
    # for the new automatic-install changes without replacing that baseline.
    if state['version'] == 2:
        snapshot_extensions(directory, registry, AUTO_EXTENSION_RECORD)


def load_extension_sealed(directory, record=EXTENSION_RECORD):
    if record not in (EXTENSION_RECORD, AUTO_EXTENSION_RECORD):
        raise ValueError('Unexpected extension recovery filename')
    directory = Path(directory)
    file = directory / record
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    expected = manifest.get('extra_sha256', {}).get(record)
    if not file.exists():
        if expected:
            raise ValueError('Sealed extension recovery record is missing')
        return None
    if not expected or hashlib.sha256(file.read_bytes()).hexdigest() != expected:
        raise ValueError('WebRTC extension recovery record is not sealed or was changed')
    return validate_extensions(json.loads(file.read_text(encoding='utf-8')))


def load_installation_sealed(directory):
    original = load_extension_sealed(directory)
    automatic = load_extension_sealed(directory, AUTO_EXTENSION_RECORD)
    if automatic is not None:
        if original is None or original['version'] != 2 or automatic['version'] != 3:
            raise ValueError('Unexpected automatic extension migration record')
        return automatic
    return original


def apply_protection(directory, registry):
    state = load_sealed(directory)
    extensions = load_installation_sealed(directory)
    if extensions is None or extensions['version'] == 2:
        raise ValueError('Missing extension recovery point; capture and seal before applying')
    registry.preflight()
    # Retain the offline fallback, and let each official browser store install
    # its signed extension through the supported force-install policy.
    webrtc_bundle.prepare()
    changes = [(p, n, desired(n, registry.read(p, n))) for _, p, n in TARGETS]
    for entry in extensions['entries']:
        current = registry.read(entry['path'], entry['name'])
        after = {'exists': True, 'kind': 1, 'value': entry['managed']}
        if current not in (entry['before'], after):
            raise ValueError('Extension policy slot changed; refusing to replace another add-on')
        changes.append((entry['path'], entry['name'], after))
    before = [(p, n, registry.read(p, n)) for p, n, _ in changes]
    try:
        for p, n, value in changes:
            registry.write(p, n, value)
    except Exception:
        failures = []
        for p, n, value in reversed(before):
            try: registry.write(p, n, value)
            except Exception: failures.append(n)
        if failures: raise RuntimeError('WebRTC rollback incomplete; use snapshot restore') from None
        raise


def restore_extensions(state, registry):
    validate_extensions(state)
    # New offline configurations never own a force-install registry slot.
    if state['version'] == 2:
        return
    failures = []
    for entry in state['entries']:
        try:
            current = registry.read(entry['path'], entry['name'])
            managed = {'exists': True, 'kind': 1, 'value': entry['managed']}
            if current not in (entry['before'], managed):
                raise ValueError('Managed extension slot has later user changes')
            registry.write(entry['path'], entry['name'], entry['before'])
        except Exception: failures.append(entry['browser'])
    if failures:
        raise RuntimeError('WebRTC extension restoration incomplete: ' + ', '.join(failures))


def extension_installation(browser, root=None):
    """Inspect only the managed add-on's metadata, never modify a profile.

    Disk/registration evidence is not proof that an extension ran on a page.
    Do not return profile names, unrelated settings or any browsing data.
    """
    vendor = {'Chrome': 'Google/Chrome', 'Edge': 'Microsoft/Edge'}[browser]
    if root is None:
        local = os.environ.get('LOCALAPPDATA')
        if not local:
            return {'state': 'unknown', 'label': '安装记录未能读取'}
        root = Path(local) / vendor / 'User Data'
    root = Path(root)
    store_id = next(value.split(';')[0] for name, _, value in EXTENSIONS if name == browser)
    ids = (store_id, LOCAL_EXTENSION_ID)
    total = installed = disabled = errors = 0
    local_installed = 0
    try:
        profiles = sorted(root.iterdir()) if root.exists() else []
        for profile in profiles:
            if not (profile.name == 'Default' or re.fullmatch(r'Profile [0-9]+', profile.name)):
                continue
            if profile.is_symlink() or not profile.is_dir():
                continue
            if not (profile / 'Preferences').exists():
                continue
            total += 1
            try:
                registrations = {}
                for name in ('Preferences', 'Secure Preferences'):
                    path = profile / name
                    if not path.exists():
                        continue
                    if path.stat().st_size > 32 * 1024 * 1024:
                        raise ValueError('Metadata too large')
                    data = json.loads(path.read_text(encoding='utf-8'))
                    settings = data.get('extensions', {}).get('settings', {})
                    for eid in ids:
                        entry = settings.get(eid)
                        if isinstance(entry, dict) and entry:
                            registrations[eid] = entry
                found = []
                for eid, registered in registrations.items():
                    if eid == LOCAL_EXTENSION_ID:
                        expected = (webrtc_bundle.PROJECT / '.local' / 'webrtc-addon' / '1.0.0').resolve()
                        recorded = registered.get('path', '')
                        local_valid = False
                        if isinstance(recorded, str) and Path(recorded).is_absolute() and Path(recorded).resolve() == expected:
                            raw = webrtc_bundle.payload()
                            local_valid = all((expected / n).is_file() and (expected / n).read_bytes() == v for n, v in raw.items())
                        if not local_valid:
                            continue
                        local_installed += 1
                    else:
                        manifests = (profile / 'Extensions' / eid).glob('*/manifest.json')
                        if not any(isinstance(json.loads(p.read_text(encoding='utf-8')).get('version'), str) for p in manifests):
                            continue
                    found.append(not (registered.get('disable_reasons') or registered.get('state') == 0))
                if found:
                    installed += 1
                    if not any(found):
                        disabled += 1
            except (OSError, ValueError, TypeError, AttributeError):
                errors += 1
    except OSError:
        errors += 1
    if errors:
        state, label = 'unknown', '安装记录未完整读取'
    elif not total:
        state, label = 'no_profiles', '尚无可检查的标准浏览器资料'
    elif disabled:
        state, label = 'disabled', '发现已安装但被禁用的扩展记录'
    elif installed == total:
        state, label = 'installed', '已安装'
    elif installed:
        state, label = 'partial', f'仅 {installed}/{total} 份标准资料有安装记录'
    else:
        state, label = 'missing', '尚未安装'
    return dict(state=state, label=label, profiles=total, installed_profiles=installed,
                local_installed_profiles=local_installed, disabled_profiles=disabled, runtime_verified=False)


def protection_status(registry=None):
    registry = registry or Registry()
    core = status(registry)['browsers']
    rows = []
    for browser in ('Chrome', 'Edge', 'Firefox'):
        policy = core[browser] == '开启'
        requested = None
        if browser != 'Firefox':
            try:
                _, path, value = next(e for e in EXTENSIONS if e[0] == browser)
                requested = any(e.get('kind') == 1 and e.get('value') == value for e in registry.list_values(path).values())
            except (OSError, ValueError): requested = False
        installation = extension_installation(browser) if browser != 'Firefox' else None
        if installation and requested and installation['state'] in ('missing', 'partial', 'no_profiles'):
            installation['label'] = '已安排自动安装' if installation['state'] == 'missing' else installation['label'] + '，待浏览器自动安装'
        complete = policy and (browser == 'Firefox' or installation['state'] == 'installed')
        rows.append(dict(browser=browser, policy_configured=policy, extension_requested=requested,
                         installation=installation,
                         configuration_complete=complete,
                         configuration_state='配置完成' if complete else '待应用策略' if not policy else '待完成扩展配置',
                         runtime_verified=False,
                         state=('WebRTC 已锁定关闭' if policy else '禁用策略未完整配置') if browser == 'Firefox'
                         else '路径限制策略已配置' if policy
                         else '路径限制策略未完整配置'))
    pending = [r['browser'] for r in rows if r['installation'] and
               r['installation']['state'] != 'no_profiles' and not r['configuration_complete']]
    installation_help = '默认静默安装 Chrome、Edge 官方商店扩展，已有扩展复用；浏览器关闭时将在下次正常启动后安装。项目自带离线包仅作手动备用。'
    if pending:
        installation_help += '如果正常重启浏览器后仍未安装或已被禁用，请在 chrome://extensions 或 edge://extensions 启用开发者模式，选择“加载已解压的扩展”，加载下方项目离线目录，每份资料一次。离线文件就绪不代表安装成功。'
    return {'browsers': rows, 'runtime_verified': False,
            'installation_pending': pending,
            'manual_fallback_available': webrtc_bundle.status().get('prepared') is True,
            'scope': '仅管理标准 Chrome、Edge、Firefox 策略；外部指纹浏览器不在配置范围内',
            'bundle': webrtc_bundle.status(),
            'note': 'Chrome/Edge 使用策略与扩展两层保护；Firefox 使用原生禁用策略。',
            'installation_help': installation_help}


def apply(state, registry):
    validate(state)
    if hasattr(registry, 'preflight'): registry.preflight()
    # Parse all current policies before any write; preserve unrelated Firefox prefs.
    changes = [(p, n, desired(n, registry.read(p, n))) for _, p, n in TARGETS]
    before = [(p, n, registry.read(p, n)) for p, n, _ in changes]
    try:
        for p, n, entry in changes: registry.write(p, n, entry)
    except Exception:
        errors = []
        for p, n, entry in reversed(before):
            try: registry.write(p, n, entry)
            except Exception: errors.append(n)
        if errors: raise RuntimeError('WebRTC policy rollback incomplete; use snapshot restore') from None
        raise


def restore(state, registry):
    validate(state)
    errors = []
    for saved in state['entries']:
        p, n, before = saved['path'], saved['name'], saved['before']
        try:
            if n == 'Preferences':
                current = registry.read(p, n)
                value, original = preferences(current), preferences(before)
                if PREF in original: value[PREF] = original[PREF]
                else: value.pop(PREF, None)
                # Restore exact original representation when no unrelated fields changed.
                entry = before if value == original else {'exists': True, 'kind': 7, 'value': [json.dumps(value, ensure_ascii=False)]}
            else: entry = before
            registry.write(p, n, entry)
        except Exception: errors.append(saved['browser'])
    if errors: raise RuntimeError('WebRTC policy restoration incomplete: ' + ', '.join(sorted(set(errors))))


def status(registry=None):
    registry = registry or Registry()
    states = []
    for browser in ('Chrome', 'Edge', 'Firefox'):
        try:
            ready = True
            for b, p, n in TARGETS:
                if b != browser: continue
                entry = registry.read(p, n)
                if n == 'Preferences': ok = preferences(entry).get(PREF) == LOCK
                elif n.endswith('Url'):
                    ok = entry.get('kind') == 1 and json.loads(entry.get('value', 'null')) == [{'url': '*', 'handling': VALUE}]
                else: ok = entry.get('value') == VALUE and entry.get('kind') == 1
                ready = ready and ok
            states.append((browser, '开启' if ready else '未完整开启'))
        except (OSError, ValueError, TypeError): states.append((browser, '未能确认'))
    return {'state': '开启' if all(v == '开启' for _, v in states) else '未完整开启', 'browsers': dict(states)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['snapshot', 'apply', 'restore', 'status', 'verify'])
    args = parser.parse_args()
    registry = Registry()
    if args.action == 'status': return protection_status(registry)
    directory = Path(os.environ['CCN_SNAPSHOT'])
    if any((directory / n).exists() for n in (EXTENSION_RECORD, AUTO_EXTENSION_RECORD)) and not (directory / RECORD).exists():
        raise ValueError('Missing WebRTC policy recovery record')
    if args.action == 'snapshot':
        snapshot(directory, registry)
        snapshot_extension_installation(directory, registry)
    elif args.action == 'apply': apply_protection(directory, registry)
    elif args.action == 'verify':
        if (directory / RECORD).exists(): load_sealed(directory)
        load_installation_sealed(directory)
    elif not (directory / RECORD).exists():
        if any((directory / n).exists() for n in (EXTENSION_RECORD, AUTO_EXTENSION_RECORD)): raise ValueError('Missing WebRTC policy recovery record')
        return {'success': True, 'skipped': '旧快照没有全浏览器 WebRTC 记录，保留当前设置'}
    else:
        state = load_sealed(directory)
        extensions = load_installation_sealed(directory)
        failures = []
        try: restore(state, registry)
        except Exception: failures.append('browser policies')
        if extensions:
            try: restore_extensions(extensions, registry)
            except Exception: failures.append('extension policies')
        if failures: raise RuntimeError('WebRTC restoration incomplete: ' + ', '.join(failures))
    return {'success': True, 'scope': ['Chrome', 'Edge', 'Firefox'], 'restart_required': True, 'browser_behavior': 'unverified'}

if __name__ == '__main__':
    print(json.dumps(main(), ensure_ascii=True))
