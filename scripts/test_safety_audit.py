"""Offline fault fixtures: never touches live adapters, services or network."""
import contextlib,io,json,os,socket,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
import client,verify_tun as tun,firewall_audit as firewall,safety_audit as audit,verification_reports as reports,gui_server
from operation_lock import check_pending_tun

class SafetyAudit(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory(dir=client.LOCAL,prefix='safety-test-');self.addCleanup(temp.cleanup)
  self.local=Path(temp.name);self.guard=self.local/'guard';self.guard.mkdir()
  self.enterContext(patch.dict(os.environ));os.environ['CCN_VERIFY_RUN']='f'*32
  for module in (tun,firewall,audit,reports):self.enterContext(patch.object(module,'LOCAL',self.local))
  client.write(self.local/'plan.json',{'root':str(self.local)})
  self.enterContext(patch.object(tun,'root_path',return_value=self.local))
  self.enterContext(contextlib.redirect_stdout(io.StringIO()))
  self.enabled=True
  self.api=self.enterContext(patch.object(tun,'api',side_effect=self.controller))
  self.state=self.enterContext(patch.object(tun,'system_state',side_effect=self.system))
  self.guard_start=self.enterContext(patch.object(tun,'start_guard',return_value=self.guard))
  self.tcp=self.enterContext(patch.object(tun,'tcp_reachable',side_effect=lambda address:self.enabled))
  self.dns=self.enterContext(patch.object(tun,'query',side_effect=socket.timeout('blocked fixture')))
 def controller(self,root,method,path,body=None):
  if method=='PATCH':self.enabled=body['tun']['enable'];return {}
  return {'tun':{'enable':self.enabled}}
 def system(self):return {'cores':[{'pid':42,'path':'fixture.exe'}],'tun_up':int(self.enabled)}
 def test_tun_drill_proves_core_alive_and_blocks_direct_tcp_and_dns(self):
  result=tun.verify_tun()
  self.assertTrue(result['success']);self.assertTrue(result['same_core_running']);self.assertTrue(result['tun_disabled'])
  for key in ('foreign_tcp_blocked','domestic_tcp_blocked','dns_tcp_blocked','dns_udp_blocked','tun_restored'):self.assertTrue(result[key])
  self.assertTrue(self.enabled);self.assertTrue((self.guard/'done').exists())
  self.assertEqual(self.dns.call_count,2)
  for call in self.dns.call_args_list:self.assertEqual(call.kwargs['host'],'example.com')
 def test_leak_still_restores_tun_and_fails(self):
  self.tcp.side_effect=lambda address:True
  result=tun.verify_tun();self.assertFalse(result['success']);self.assertTrue(result['tun_restored']);self.assertTrue(self.enabled)
 def test_dns_response_is_a_failure_even_when_empty(self):
  self.dns.side_effect=None;self.dns.return_value={'rcode':5,'answer_count':0}
  result=tun.verify_tun();self.assertFalse(result['success']);self.assertTrue(result['tun_restored'])
 def test_core_replacement_cannot_pass(self):
  before=self.system();after={'cores':[{'pid':43,'path':'fixture.exe'}],'tun_up':0}
  self.state.side_effect=[before,after,before]
  result=tun.verify_tun();self.assertFalse(result['success']);self.assertTrue(result['tun_restored'])
 def test_still_active_adapter_cannot_pass(self):
  before=self.system();self.state.side_effect=[before,before,before]
  result=tun.verify_tun();self.assertFalse(result['success']);self.assertTrue(self.enabled)
 def test_failed_patch_still_attempts_restore(self):
  def controller(root,method,path,body=None):
   if method=='PATCH' and not body['tun']['enable']:
    self.enabled=False;raise OSError('partially applied fixture')
   return self.controller(root,method,path,body)
  self.api.side_effect=controller
  result=tun.verify_tun();self.assertFalse(result['success']);self.assertTrue(result['tun_restored'])
 def test_restore_failure_keeps_guard_pending_and_report_failed(self):
  with patch.object(tun,'restore_tun',side_effect=OSError('fixture')):
   result=tun.verify_tun()
  self.assertFalse(result['success']);self.assertTrue(result['restore_failed']);self.assertFalse((self.guard/'done').exists())
 def test_no_positive_control_never_disables_tun(self):
  self.tcp.side_effect=None;self.tcp.return_value=False
  self.assertFalse(tun.verify_tun()['success']);self.guard_start.assert_not_called()
  self.assertFalse(any(call.args[1]=='PATCH' for call in self.api.call_args_list))
 def test_no_watchdog_never_disables_tun(self):
  self.guard_start.side_effect=OSError('fixture')
  self.assertFalse(tun.verify_tun()['success']);self.assertTrue(self.enabled)
  self.assertFalse(any(call.args[1]=='PATCH' for call in self.api.call_args_list))
 def test_watchdog_restores_after_parent_exit(self):
  token='b'*32;folder=self.local/'tun-drills'/token;folder.mkdir(parents=True)
  client.write(folder/'guard.json',{'root':str(self.local),'parent':123,'timeout':60})
  kernel=Mock();kernel.OpenProcess.return_value=123;kernel.WaitForSingleObject.return_value=0
  with patch.object(tun.ctypes,'WinDLL',return_value=kernel),patch.object(tun,'restore_tun') as restore:
   tun.watch(token)
  restore.assert_called_once_with(self.local);self.assertTrue((folder/'done').exists())
  self.assertTrue(client.load(folder/'recovery.json')['success']);kernel.CloseHandle.assert_called_once()
 def test_watchdog_failure_retains_pending_recovery(self):
  token='c'*32;folder=self.local/'tun-drills'/token;folder.mkdir(parents=True)
  client.write(folder/'guard.json',{'root':str(self.local),'parent':123,'timeout':60})
  kernel=Mock();kernel.OpenProcess.return_value=123;kernel.WaitForSingleObject.return_value=0
  with patch.object(tun.ctypes,'WinDLL',return_value=kernel),patch.object(tun,'restore_tun',side_effect=OSError('fixture')):
   tun.watch(token)
  self.assertFalse((folder/'done').exists());self.assertFalse(client.load(folder/'recovery.json')['success'])
  with self.assertRaises(RuntimeError):check_pending_tun(self.local)
 def test_already_disabled_tun_is_not_claimed_as_tested(self):
  self.enabled=False;result=tun.verify_tun();self.assertFalse(result['success']);self.guard_start.assert_not_called()
 def test_incomplete_guard_blocks_other_operations(self):
  folder=self.local/'tun-drills'/('a'*32);folder.mkdir(parents=True);(folder/'ready').touch()
  with self.assertRaisesRegex(RuntimeError,'TUN drill recovery'):check_pending_tun(self.local)
  (folder/'done').touch();check_pending_tun(self.local)
 def test_firewall_permission_denied_is_unverified(self):
  completed=Mock(returncode=0,stdout=json.dumps({'success':False,'full_verified':False,'status':'unverified','administrator':False}).encode())
  with patch.object(firewall.subprocess,'run',return_value=completed):result=firewall.verify_firewall()
  self.assertFalse(result['success']);self.assertFalse(result['full_verified']);self.assertEqual(result['status'],'unverified')
 def test_firewall_missing_full_assertion_or_elevation_cannot_pass(self):
  for full,admin in ((False,True),(True,False)):
   completed=Mock(returncode=0,stdout=json.dumps({'success':True,'full_verified':full,'status':'passed','administrator':admin}).encode())
   with patch.object(firewall.subprocess,'run',return_value=completed):result=firewall.verify_firewall()
   self.assertFalse(result['success']);self.assertEqual(result['status'],'unverified')
 def test_firewall_preflight_failure_stops_all_drills(self):
  with patch.object(audit,'verify_firewall',return_value={'success':False}),patch.object(audit,'verify') as network,patch.object(audit,'verify_tun') as drill,patch.object(audit,'verify_core_exit') as core:
   self.assertFalse(audit.run_audit()['success'])
  network.assert_not_called();drill.assert_not_called();core.assert_not_called()
 def test_failed_tun_drill_still_rechecks_ipv6_bindings(self):
  with patch.object(audit,'verify_firewall',return_value={'success':True}),patch.object(audit,'verify'),patch.object(audit,'verify_tun',return_value={'success':False}),patch.object(audit,'verify_core_exit') as core,patch.object(audit,'recheck_ipv6') as recovery:
   self.assertFalse(audit.run_audit()['success'])
  recovery.assert_called_once();core.assert_not_called()
 def test_ipv6_recovery_failure_marks_current_verification_failed(self):
  with patch.object(audit,'verify_firewall',return_value={'success':True}),patch.object(audit,'verify'),patch.object(audit,'verify_tun',return_value={'success':True}),patch.object(audit,'verify_core_exit',return_value={'success':True}),patch.object(audit,'recheck_ipv6',side_effect=OSError('fixture')):
   self.assertFalse(audit.run_audit()['success'])
  report=client.load(self.local/'verification.json')
  self.assertFalse(report['success']);self.assertEqual(report['failed_check'],'ipv6_bindings')
 def test_old_three_stage_report_is_partial(self):
  stages={key:{'success':True} for key in ('node-outage','core-exit','post-recovery')}
  self.assertEqual(reports.coverage_for(stages),'partial')
  stages.update({key:{'success':True} for key in ('tun-off','firewall-before','firewall-after')})
  self.assertEqual(reports.coverage_for(stages),'full-network')
 def test_final_firewall_stage_preserves_recovery_network_details(self):
  reports.save_report({'success':True,'egress':{'country':'SG'},'runtime_mode':'rule'},'post-recovery')
  reports.save_report({'success':False,'status':'unverified'},'firewall-after')
  result=client.load(self.local/'verification.json')
  self.assertEqual(result['egress']['country'],'SG');self.assertFalse(result['success'])
 def test_gui_downgrades_legacy_coverage_and_shows_unverified_firewall(self):
  app=gui_server.App(local=self.local,open_browser=False,idle_exit=False)
  client.write(self.local/'verification.json',{'success':True,'coverage':'full-network','stages':{
   'node-outage':{'success':True},'core-exit':{'success':True},'post-recovery':{'success':True},
   'firewall-before':{'success':False,'status':'unverified'}}})
  result=app.verification_summary();self.assertEqual(result['coverage'],'partial')
  rows={row['id']:row for row in result['stages']};self.assertEqual(rows['firewall-before']['status'],'unverified')
  self.assertFalse(rows['tun-off']['tested'])
  command,_=app._job_command('audit',{},self.local)
  self.assertIn('-Acknowledged',command);self.assertIn('audit',command)
 def test_watchdog_has_separate_bounded_preparation_and_drill_windows(self):
  import subprocess,shutil
  project=self.local/'watchdog';scripts=project/'scripts';scripts.mkdir(parents=True)
  local=project/'.local';local.mkdir()
  shutil.copy2(client.PROJECT/'scripts/watchdog.ps1',scripts/'watchdog.ps1')
  (scripts/'operation_guard.ps1').write_text("function Resolve-PendingFirewall {param($LocalDir,$Python,$ExpectedToken) Write-Output (($global:clockTime-$global:clockStart).TotalSeconds)}")
  wrapper=scripts/'clock.ps1'
  wrapper.write_text('''param($Armed,$Window)
$global:clockStart=[datetime]'2000-01-01';$global:clockTime=$global:clockStart
function Get-Date {return $global:clockTime}
function Start-Sleep {param($Seconds) $global:clockTime=$global:clockTime.AddSeconds($Seconds)}
$marker=Join-Path $PSScriptRoot '../.local/fixture.armed'
if($Armed -eq 'yes'){Set-Content -LiteralPath $marker 'armed'}elseif(Test-Path $marker){Remove-Item -LiteralPath $marker}
& (Join-Path $PSScriptRoot 'watchdog.ps1') -Token fixture -Python unused -WaitForFirewall -FirewallTimeoutSeconds $Window
''',encoding='utf-8-sig')
  for armed,window,expected in [('no',360,180),('yes',180,180),('yes',360,360)]:
   run=subprocess.run(['powershell.exe','-NoProfile','-File',str(wrapper),armed,str(window)],capture_output=True,timeout=12)
   self.assertEqual(run.returncode,0,run.stderr);self.assertEqual(int(run.stdout.strip()),expected)

class ControllerDeadline(unittest.TestCase):
 def test_controller_has_a_finite_deadline(self):
  import subprocess
  with patch.object(tun.subprocess,'run',side_effect=subprocess.TimeoutExpired('fixture',10)) as run:
   with self.assertRaises(subprocess.TimeoutExpired):tun.api(Path('fixture'),'GET','/configs')
  self.assertEqual(run.call_args.kwargs['timeout'],10)

if __name__=='__main__':unittest.main()
