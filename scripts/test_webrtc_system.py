"""Offline policy backup/apply/restore tests; no real registry writes."""
import copy
import hashlib
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
import client
import webrtc_system as rtc

class FakeRegistry:
    def __init__(self): self.values = {}; self.fail_once = None
    def preflight(self): pass
    def list_values(self, path): return {n:copy.deepcopy(v) for (p,n),v in self.values.items() if p==path}
    def read(self, p, n): return copy.deepcopy(self.values.get((p, n), {'exists': False}))
    def write(self, p, n, value):
        if n == self.fail_once:
            self.fail_once = None
            raise OSError('simulated failure')
        if value['exists']: self.values[p, n] = copy.deepcopy(value)
        else: self.values.pop((p, n), None)

class WebRtcPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=client.LOCAL, prefix='webrtc-test-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.registry = FakeRegistry()
        prepare = rtc.webrtc_bundle.prepare
        local_prepare = patch.object(rtc.webrtc_bundle, 'prepare', side_effect=lambda: prepare(self.path / 'addon'))
        local_prepare.start()
        self.addCleanup(local_prepare.stop)
    def snapshot(self): return rtc.snapshot(self.path, self.registry)
    def test_apply_covers_every_supported_browser_and_restore_removes_new_values(self):
        state = self.snapshot()
        rtc.apply(state, self.registry)
        self.assertEqual(rtc.status(self.registry)['state'], '开启')
        self.assertEqual(set(rtc.status(self.registry)['browsers']), {'Chrome','Edge','Firefox'})
        rtc.restore(state, self.registry)
        self.assertEqual(self.registry.values, {})
    def test_snapshot_never_overwrites_first_values(self):
        _, p, n = rtc.TARGETS[0]
        before = {'exists':True,'kind':1,'value':'default'}
        self.registry.write(p,n,before)
        state = self.snapshot()
        raw = (self.path/rtc.RECORD).read_bytes()
        rtc.apply(state,self.registry)
        self.snapshot()
        self.assertEqual(raw,(self.path/rtc.RECORD).read_bytes())
        rtc.restore(state,self.registry)
        self.assertEqual(self.registry.read(p,n),before)
    def test_firefox_preserves_other_policies_and_later_changes(self):
        _,p,n=rtc.TARGETS[-1]
        original={'browser.tabs.warnOnClose':{'Value':True,'Status':'locked'},rtc.PREF:{'Value':True,'Status':'locked'}}
        self.registry.write(p,n,{'exists':True,'kind':7,'value':[json.dumps(original)]})
        state=self.snapshot();rtc.apply(state,self.registry)
        current=rtc.preferences(self.registry.read(p,n));current['browser.startup.page']={'Value':0,'Status':'locked'}
        self.registry.write(p,n,{'exists':True,'kind':7,'value':[json.dumps(current)]})
        rtc.restore(state,self.registry)
        restored=rtc.preferences(self.registry.read(p,n))
        self.assertEqual(restored[rtc.PREF],original[rtc.PREF])
        self.assertEqual(restored['browser.startup.page']['Value'],0)
        self.assertEqual(restored['browser.tabs.warnOnClose'],original['browser.tabs.warnOnClose'])
    def test_failure_rolls_back_all_policy_values(self):
        state=self.snapshot();before=copy.deepcopy(self.registry.values)
        self.registry.fail_once='WebRtcLocalhostIpHandling'
        with self.assertRaises(OSError):rtc.apply(state,self.registry)
        self.assertEqual(before,self.registry.values)
    def test_malformed_firefox_policy_stops_before_any_write(self):
        _,p,n=rtc.TARGETS[-1]
        state=self.snapshot()
        self.registry.write(p,n,{'exists':True,'kind':7,'value':['invalid JSON']})
        before=copy.deepcopy(self.registry.values)
        with self.assertRaises(ValueError):rtc.apply(state,self.registry)
        self.assertEqual(before,self.registry.values)
    def test_changed_or_unsealed_recovery_record_is_rejected(self):
        self.snapshot()
        file=self.path/rtc.RECORD
        (self.path/'manifest.json').write_text(json.dumps({'extra_sha256':{rtc.RECORD:hashlib.sha256(file.read_bytes()).hexdigest()}}))
        rtc.load_sealed(self.path)
        file.write_text(file.read_text()+' ')
        with self.assertRaises(ValueError):rtc.load_sealed(self.path)
    def test_recovery_cannot_target_unrelated_registry_values(self):
        state=self.snapshot();state['entries'][0]['name']='Unrelated'
        with self.assertRaises(ValueError):rtc.restore(state,self.registry)
        self.assertEqual(self.registry.values,{})

    def sealed_protection(self, legacy=False, offline=False):
        self.snapshot()
        extensions = rtc.snapshot_extensions(self.path,self.registry)
        if legacy:
            extensions['version'] = 1
            extensions.pop('read_only', None)
            (self.path / rtc.EXTENSION_RECORD).write_text(json.dumps(extensions), encoding='utf-8')
        if offline:
            extensions['version'] = 2
            extensions['read_only'] = True
            (self.path / rtc.EXTENSION_RECORD).write_text(json.dumps(extensions), encoding='utf-8')
        hashes={name:hashlib.sha256((self.path/name).read_bytes()).hexdigest()
                for name in (rtc.RECORD,rtc.EXTENSION_RECORD)}
        (self.path/'manifest.json').write_text(json.dumps({'extra_sha256':hashes}))
        return rtc.load_extension_sealed(self.path)

    def test_extension_install_preserves_other_addons_and_restores_only_managed_slots(self):
        _,path,_=rtc.EXTENSIONS[0]
        unrelated={'exists':True,'kind':1,'value':'a'*32+';https://example.invalid/update'}
        self.registry.write(path,'1',unrelated)
        state=self.sealed_protection()
        self.assertEqual(state['entries'][0]['name'],'2')
        before=copy.deepcopy(self.registry.values)
        rtc.apply_protection(self.path,self.registry)
        self.assertTrue(rtc.protection_status(self.registry)['browsers'][0]['extension_requested'])
        self.assertEqual(self.registry.read(path,'2')['value'],rtc.EXTENSIONS[0][2])
        self.assertEqual(self.registry.read(path,'1'),unrelated)
        later={'exists':True,'kind':1,'value':'b'*32}
        self.registry.write(path,'3',later)
        rtc.restore_extensions(state,self.registry)
        rtc.restore(rtc.load_sealed(self.path),self.registry)
        self.assertEqual(self.registry.values,{**before,(path,'3'):later})

    def test_extension_failure_rolls_back_core_policies_and_extensions(self):
        self.sealed_protection();before=copy.deepcopy(self.registry.values)
        self.registry.fail_once='WebRtcLocalhostIpHandling'
        with self.assertRaises(OSError):rtc.apply_protection(self.path,self.registry)
        self.assertEqual(self.registry.values,before)

    def test_changed_slot_stops_apply_before_writes_and_restore_does_not_clobber(self):
        state=self.sealed_protection(legacy=True);entry=state['entries'][0]
        self.registry.write(entry['path'],entry['name'],{'exists':True,'kind':1,'value':'c'*32})
        before=copy.deepcopy(self.registry.values)
        with self.assertRaises(ValueError):rtc.apply_protection(self.path,self.registry)
        self.assertEqual(self.registry.values,before)
        with self.assertRaises(RuntimeError):rtc.restore_extensions(state,self.registry)
        self.assertEqual(self.registry.values,before)

    def test_preexisting_requested_extension_remains_after_restore(self):
        _,path,value=rtc.EXTENSIONS[0]
        existing={'exists':True,'kind':1,'value':value}
        self.registry.write(path,'7',existing)
        state=self.sealed_protection();rtc.apply_protection(self.path,self.registry)
        rtc.snapshot_extensions(self.path,self.registry)
        rtc.restore_extensions(state,self.registry)
        self.assertEqual(self.registry.read(path,'7'),existing)

    def test_extension_seal_and_targets_are_validated(self):
        state=self.sealed_protection();file=self.path/rtc.EXTENSION_RECORD
        state['entries'][0]['path']='Unrelated'
        with self.assertRaises(ValueError):rtc.restore_extensions(state,self.registry)
        file.write_text(file.read_text()+' ')
        with self.assertRaises(ValueError):rtc.apply_protection(self.path,self.registry)
        self.assertEqual(self.registry.values,{})

    def test_policy_status_never_claims_browser_runtime_or_extension_verified(self):
        self.sealed_protection();rtc.apply_protection(self.path,self.registry)
        status=rtc.protection_status(self.registry)
        self.assertFalse(status['runtime_verified'])
        self.assertTrue(all(row['policy_configured'] for row in status['browsers']))
        self.assertTrue(all(row['extension_requested'] for row in status['browsers'][:2]))
        self.assertTrue(all(not row['runtime_verified'] for row in status['browsers']))

    def test_installation_requires_registration_and_files_in_each_profile(self):
        root = self.path / 'user-data'
        eid = rtc.EXTENSIONS[0][2].split(';')[0]
        profile = root / 'Default'
        profile.mkdir(parents=True)
        (profile / 'Preferences').write_text('{}')
        addon = profile / 'Extensions' / eid / '0.3.5_0'
        addon.mkdir(parents=True)
        (addon / 'manifest.json').write_text('{"version":"0.3.5"}')
        self.assertEqual(rtc.extension_installation('Chrome', root)['state'], 'missing')
        record = {'extensions': {'settings': {eid: {'location': 7, 'disable_reasons': []}}}}
        secure = profile / 'Secure Preferences'
        secure.write_text(json.dumps(record))
        before = secure.read_bytes()
        result = rtc.extension_installation('Chrome', root)
        self.assertEqual(result['state'], 'installed')
        self.assertFalse(result['runtime_verified'])
        self.assertEqual(before, secure.read_bytes())
        other = root / 'Profile 1'
        other.mkdir()
        (other / 'Preferences').write_text('{}')
        self.assertEqual(rtc.extension_installation('Chrome', root)['state'], 'partial')
        record['extensions']['settings'][eid]['disable_reasons'] = [1]
        secure.write_text(json.dumps(record))
        self.assertEqual(rtc.extension_installation('Chrome', root)['state'], 'disabled')
        secure.write_text('broken')
        self.assertEqual(rtc.extension_installation('Chrome', root)['state'], 'unknown')

    def test_missing_browser_profiles_are_not_reported_installed(self):
        result = rtc.extension_installation('Edge', self.path / 'absent')
        self.assertEqual(result['state'], 'no_profiles')
        self.assertFalse(result['runtime_verified'])

    def test_bundle_integrity_failure_stops_before_policy_changes(self):
        self.sealed_protection()
        before = copy.deepcopy(self.registry.values)
        with patch.object(rtc.webrtc_bundle, 'prepare', side_effect=ValueError('corrupted local add-on')):
            with self.assertRaises(ValueError):
                rtc.apply_protection(self.path, self.registry)
        self.assertEqual(before, self.registry.values)

    def test_offline_restore_never_owns_unused_force_install_slot(self):
        state = self.sealed_protection(offline=True)
        self.assertEqual(state['version'], 2)
        entry = state['entries'][0]
        later = {'exists': True, 'kind': 1, 'value': 'd' * 32}
        self.registry.write(entry['path'], entry['name'], later)
        rtc.restore_extensions(state, self.registry)
        self.assertEqual(self.registry.read(entry['path'], entry['name']), later)

    def test_offline_baseline_migration_preserves_original_and_later_addons(self):
        original = self.sealed_protection(offline=True)
        before = (self.path / rtc.EXTENSION_RECORD).read_bytes()
        entry = original['entries'][0]
        later = {'exists': True, 'kind': 1, 'value': 'e' * 32}
        self.registry.write(entry['path'], entry['name'], later)
        rtc.snapshot_extension_installation(self.path, self.registry)
        manifest = json.loads((self.path/'manifest.json').read_text())
        manifest['extra_sha256'][rtc.AUTO_EXTENSION_RECORD] = hashlib.sha256((self.path/rtc.AUTO_EXTENSION_RECORD).read_bytes()).hexdigest()
        (self.path/'manifest.json').write_text(json.dumps(manifest))
        self.assertEqual((self.path/rtc.EXTENSION_RECORD).read_bytes(), before)
        auto = rtc.load_installation_sealed(self.path)
        self.assertEqual(auto['entries'][0]['name'], '2')
        rtc.apply_protection(self.path, self.registry)
        self.assertTrue(all(row['extension_requested'] for row in rtc.protection_status(self.registry)['browsers'][:2]))
        rtc.restore_extensions(auto, self.registry)
        self.assertEqual(self.registry.read(entry['path'],entry['name']),later)
        self.assertFalse(self.registry.read(entry['path'],'2')['exists'])
        self.assertEqual((self.path/rtc.EXTENSION_RECORD).read_bytes(),before)

    def test_unmigrated_offline_baseline_stops_before_any_policy_write(self):
        self.sealed_protection(offline=True)
        with self.assertRaises(ValueError):
            rtc.apply_protection(self.path, self.registry)
        self.assertEqual(self.registry.values,{})

    def test_missing_sealed_automatic_migration_record_is_rejected(self):
        self.sealed_protection(offline=True)
        rtc.snapshot_extension_installation(self.path,self.registry)
        manifest=json.loads((self.path/'manifest.json').read_text())
        manifest['extra_sha256'][rtc.AUTO_EXTENSION_RECORD]=hashlib.sha256((self.path/rtc.AUTO_EXTENSION_RECORD).read_bytes()).hexdigest()
        (self.path/'manifest.json').write_text(json.dumps(manifest))
        (self.path/rtc.AUTO_EXTENSION_RECORD).unlink()
        with self.assertRaises(ValueError):rtc.load_installation_sealed(self.path)
        with self.assertRaises(ValueError):rtc.snapshot_extension_installation(self.path,self.registry)

    def test_configuration_complete_requires_both_layers_for_chromium(self):
        self.sealed_protection()
        rtc.apply_protection(self.path, self.registry)
        with patch.object(rtc, 'extension_installation', return_value={'state': 'missing', 'label': '待加载本地扩展'}):
            rows = rtc.protection_status(self.registry)['browsers']
            self.assertFalse(rows[0]['configuration_complete'])
            self.assertTrue(rows[2]['configuration_complete'])
        with patch.object(rtc, 'extension_installation', return_value={'state': 'installed', 'label': '已安装'}):
            rows = rtc.protection_status(self.registry)['browsers']
            self.assertTrue(all(row['configuration_complete'] for row in rows))
            self.assertTrue(all(row['configuration_state'] == '配置完成' for row in rows))

    def test_local_install_requires_expected_directory_and_verified_files(self):
        project = self.path / 'fixture-project'
        addon = project / '.local/webrtc-addon/1.0.0'
        addon.mkdir(parents=True)
        for name, raw in rtc.webrtc_bundle.payload().items():
            (addon / name).write_bytes(raw)
        root = self.path / 'profiles'
        profile = root / 'Default'
        profile.mkdir(parents=True)
        record = {'extensions': {'settings': {rtc.LOCAL_EXTENSION_ID: {
            'path': str(addon.resolve()), 'location': 4, 'disable_reasons': []}}}}
        (profile / 'Preferences').write_text(json.dumps(record))
        with patch.object(rtc.webrtc_bundle, 'PROJECT', project):
            status = rtc.extension_installation('Chrome', root)
            self.assertEqual(status['state'], 'installed')
            self.assertEqual(status['local_installed_profiles'], 1)
            (addon / 'block.js').write_text('changed')
            self.assertEqual(rtc.extension_installation('Chrome', root)['state'], 'missing')

    def test_missing_sealed_backups_are_not_recaptured(self):
        self.sealed_protection()
        rtc.apply_protection(self.path,self.registry)
        (self.path/rtc.EXTENSION_RECORD).unlink()
        with self.assertRaises(ValueError):rtc.snapshot_extensions(self.path,self.registry)
        (self.path/rtc.RECORD).unlink()
        with self.assertRaises(ValueError):self.snapshot()

    def test_cli_capture_seal_apply_verify_restore_roundtrip(self):
        with patch.object(rtc,'Registry',return_value=self.registry), patch.dict(rtc.os.environ,{'CCN_SNAPSHOT':str(self.path)}):
            with patch('sys.argv',['webrtc_system.py','snapshot']):rtc.main()
            self.sealed_protection()
            with patch('sys.argv',['webrtc_system.py','apply']):rtc.main()
            self.assertTrue(all(row['policy_configured'] for row in rtc.protection_status(self.registry)['browsers']))
            with patch('sys.argv',['webrtc_system.py','verify']):rtc.main()
            with patch('sys.argv',['webrtc_system.py','restore']):rtc.main()
            self.assertEqual(self.registry.values,{})
if __name__=='__main__':unittest.main()
