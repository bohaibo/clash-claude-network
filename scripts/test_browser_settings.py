"""Browser policy preflight with mocked registry; no live browser/registry writes."""
import json,unittest
from unittest.mock import patch
import browser_settings as browser

class BrowserPolicies(unittest.TestCase):
 def setUp(self):
  self.values={};self.machine_languages={}
  self.enterContext(patch.object(browser,'read',side_effect=lambda path,name,hive=browser.winreg.HKEY_CURRENT_USER:
      self.values.get((hive,path,name),{'exists':False})))
  self.enterContext(patch.object(browser,'list_values',side_effect=lambda path,hive=browser.winreg.HKEY_CURRENT_USER:
      self.machine_languages if hive==browser.winreg.HKEY_LOCAL_MACHINE else {}))
  self.capture=self.enterContext(patch.object(browser,'capture'))
  self.put=self.enterContext(patch.object(browser,'put'))
  self.enterContext(patch.object(browser,'remove'))
 def policy(self,label,name,value,hive=None):
  self.values[(hive or browser.winreg.HKEY_CURRENT_USER,browser.ROOTS[label],name)]={
      'exists':True,'value':value,'kind':browser.winreg.REG_SZ}
 def assert_stopped_before_writes(self):
  with self.assertRaises(AssertionError):browser.apply()
  self.capture.assert_not_called();self.put.assert_not_called()
 def test_combined_proxy_conflict_in_either_hive_stops(self):
  for label in browser.ROOTS:
   for hive in (browser.winreg.HKEY_CURRENT_USER,browser.winreg.HKEY_LOCAL_MACHINE):
    with self.subTest(browser=label,hive=hive):
     self.values.clear();self.policy(label,'ProxySettings',json.dumps({'ProxyMode':'fixed_servers','ProxyServer':'127.0.0.1:9'}),hive)
     self.assert_stopped_before_writes()
 def test_url_webrtc_override_in_either_hive_stops(self):
  for label in browser.ROOTS:
   for hive in (browser.winreg.HKEY_CURRENT_USER,browser.winreg.HKEY_LOCAL_MACHINE):
    with self.subTest(browser=label,hive=hive):
     self.values.clear();self.policy(label,'WebRtcIPHandlingUrl',json.dumps([{'url':'*','handling':'default'}]),hive)
     self.assert_stopped_before_writes()
 def test_compatible_overrides_are_preserved(self):
  for label in browser.ROOTS:
   self.policy(label,'ProxySettings',json.dumps({'ProxyMode':'direct'}))
   self.policy(label,'WebRtcIPHandlingUrl',json.dumps([{'url':'*','handling':'disable_non_proxied_udp'}]))
  self.assertTrue(browser.preflight()['preflight_passed']);self.put.assert_not_called()
 def test_invalid_json_is_rejected_without_printing_contents(self):
  self.policy('Chrome','ProxySettings','DO-NOT-ECHO-FIXTURE')
  with self.assertRaises(AssertionError) as error:browser.apply()
  self.assertNotIn('DO-NOT-ECHO',str(error.exception));self.put.assert_not_called()
 def test_machine_language_conflict_stops(self):
  self.machine_languages={'1':{'value':'en-US','kind':browser.winreg.REG_SZ}}
  self.assert_stopped_before_writes()
 def test_matching_machine_languages_allowed(self):
  self.machine_languages={str(i):{'value':value,'kind':browser.winreg.REG_SZ} for i,value in enumerate(('zh-SG','en-SG','zh-CN'),1)}
  self.assertTrue(browser.preflight()['preflight_passed'])
 def test_same_name_machine_conflict_stops(self):
  self.policy('Edge','DnsOverHttpsMode','secure',browser.winreg.HKEY_LOCAL_MACHINE)
  self.assert_stopped_before_writes()
 def test_policy_values_are_not_reported_as_effective_behavior(self):
  report=browser.apply();self.assertFalse(report['effective_browser_behavior_verified'])
  self.assertTrue(report['effective_policy_ui_verification_required']);self.assertTrue(report['limitations'])

if __name__=='__main__':unittest.main()
