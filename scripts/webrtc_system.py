"""Machine-wide, reversible WebRTC policies. Never reads browser profiles."""
import argparse
import copy
import hashlib
import json
import os
import re
from pathlib import Path

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
    if state.get('version') != 1 or len(state.get('entries', [])) != len(EXTENSIONS):
        raise ValueError('Invalid WebRTC extension recovery record')
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


def snapshot_extensions(directory, registry):
    file = Path(directory) / EXTENSION_RECORD
    if file.exists():
        return validate_extensions(json.loads(file.read_text(encoding='utf-8')))
    manifest = Path(directory) / 'manifest.json'
    if manifest.exists() and EXTENSION_RECORD in json.loads(manifest.read_text(encoding='utf-8')).get('extra_sha256', {}):
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
    state = validate_extensions({'version': 1, 'entries': entries})
    with file.open('x', encoding='utf-8') as stream:
        json.dump(state, stream, ensure_ascii=False, indent=2)
    return state


def load_extension_sealed(directory):
    directory = Path(directory)
    file = directory / EXTENSION_RECORD
    if not file.exists():
        return None
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    expected = manifest.get('extra_sha256', {}).get(EXTENSION_RECORD)
    if not expected or hashlib.sha256(file.read_bytes()).hexdigest() != expected:
        raise ValueError('WebRTC extension recovery record is not sealed or was changed')
    return validate_extensions(json.loads(file.read_text(encoding='utf-8')))


def apply_protection(directory, registry):
    state = load_sealed(directory)
    extensions = load_extension_sealed(directory)
    if extensions is None:
        raise ValueError('Missing extension recovery point; capture and seal before applying')
    registry.preflight()
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
        rows.append(dict(browser=browser, policy_configured=policy, extension_requested=requested,
                         runtime_verified=False,
                         state=('已写入禁用策略；实际行为未验证' if policy else '禁用策略未完整配置') if browser == 'Firefox'
                         else '限制与扩展安装策略已配置；安装及阻断未验证' if policy and requested
                         else '限制或扩展安装策略未完整配置'))
    return {'browsers': rows, 'runtime_verified': False,
            'scope': '仅管理标准 Chrome、Edge、Firefox 策略；外部指纹浏览器不在配置范围内',
            'note': 'Chrome/Edge 扩展不能视为内核完全禁用，隐私窗口不自动覆盖。扩展实际安装、开关、权限和阻断效果需人工确认。'}


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
    if (directory / EXTENSION_RECORD).exists() and not (directory / RECORD).exists():
        raise ValueError('Missing WebRTC policy recovery record')
    if args.action == 'snapshot':
        snapshot(directory, registry)
        snapshot_extensions(directory, registry)
    elif args.action == 'apply': apply_protection(directory, registry)
    elif args.action == 'verify':
        if (directory / RECORD).exists(): load_sealed(directory)
        load_extension_sealed(directory)
    elif not (directory / RECORD).exists():
        if (directory / EXTENSION_RECORD).exists(): raise ValueError('Missing WebRTC policy recovery record')
        return {'success': True, 'skipped': '旧快照没有全浏览器 WebRTC 记录，保留当前设置'}
    else:
        state = load_sealed(directory)
        extensions = load_extension_sealed(directory)
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
