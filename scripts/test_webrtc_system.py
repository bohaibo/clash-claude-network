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

    def sealed_protection(self):
        self.snapshot()
        rtc.snapshot_extensions(self.path,self.registry)
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
        self.assertEqual(self.registry.read(path,'1'),unrelated)
        later={'exists':True,'kind':1,'value':'b'*32}
        self.registry.write(path,'3',later)
        rtc.restore_extensions(state,self.registry)
        rtc.restore(rtc.load_sealed(self.path),self.registry)
        self.assertEqual(self.registry.values,{**before,(path,'3'):later})

    def test_extension_failure_rolls_back_core_policies_and_extensions(self):
        self.sealed_protection();before=copy.deepcopy(self.registry.values)
        self.registry.fail_once='1'
        with self.assertRaises(OSError):rtc.apply_protection(self.path,self.registry)
        self.assertEqual(self.registry.values,before)

    def test_changed_slot_stops_apply_before_writes_and_restore_does_not_clobber(self):
        state=self.sealed_protection();entry=state['entries'][0]
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
