"""Offline policy backup/apply/restore tests; no real registry writes."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
import client
import webrtc_system as rtc

class FakeRegistry:
    def __init__(self): self.values = {}; self.fail_once = None
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
if __name__=='__main__':unittest.main()
