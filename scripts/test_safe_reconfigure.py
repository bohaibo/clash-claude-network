"""Offline guard/backup tests; no live service, firewall or network mutations."""
import json,os,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch,MagicMock
import baseline,client,safe_reconfigure as safe

class SafeReconfigure(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory(dir=client.LOCAL,prefix='safe-test-');self.addCleanup(temp.cleanup)
  self.local=Path(temp.name);self.enterContext(patch.object(client,'LOCAL',self.local))
  self.enterContext(patch.dict(os.environ));os.environ.pop('CCN_SNAPSHOT',None)
 def snapshot(self,name,kind='auto'):
  p=self.local/'snapshots'/name;p.mkdir(parents=True)
  (p/'manifest.json').write_text(json.dumps({'kind':kind}))
  return p
 def job(self):
  folder=self.local/'reconfigure-runs'/('a'*32);folder.mkdir(parents=True)
  snap=self.snapshot('first')
  job={'id':folder.name,'snapshot':str(snap),'parent_pid':42,'parent_birth':123,'root':str(self.local),'node':'Chosen','subscription_uid':'first','region':'SG'}
  client.write(folder/'job.json',job);return folder,job
 def test_oldest_automatic_wins_and_pin_cannot_be_replaced(self):
  first=self.snapshot('20260101');second=self.snapshot('20260102');self.snapshot('20260103','manual')
  self.assertEqual(baseline.default_snapshot(self.local),first)
  original=(first/'manifest.json').read_bytes();baseline.pin_baseline(self.local,first)
  self.assertEqual(baseline.default_snapshot(self.local),first)
  with self.assertRaises(ValueError):baseline.pin_baseline(self.local,second)
  self.assertEqual((first/'manifest.json').read_bytes(),original)
 def test_existing_plan_keeps_original_manual_recovery_point(self):
  first=self.snapshot('first','manual');self.snapshot('later','auto')
  client.write(self.local/'plan.json',{'snapshot':str(first)})
  self.assertEqual(baseline.default_snapshot(self.local),first)
 def test_missing_plan_baseline_stops_instead_of_replacing(self):
  self.snapshot('other');client.write(self.local/'plan.json',{'snapshot':str(self.local/'missing')})
  with self.assertRaises(ValueError):baseline.default_snapshot(self.local)
 def test_skip_browser_applies_to_both_restore_and_apply(self):
  folder,job=self.job();job['skip_browser']=True
  for action in ('restore','apply'):
   self.assertIn('-SkipBrowser',safe.command(job,action))
  job['skip_browser']=False
  self.assertNotIn('-SkipBrowser',safe.command(job,'apply'))
  self.assertIn('-SkipBrowser',safe.command(job,'restore',True))
 def test_missing_or_escaping_pin_stops(self):
  self.snapshot('fallback')
  for name in ('missing','../outside'):
   client.write(self.local/'baseline-selection.json',{'snapshot':name})
   with self.assertRaises(ValueError):baseline.default_snapshot(self.local)
 def test_successful_guard_does_not_recover(self):
  folder,job=self.job();client.write(folder/'done.json',{'success':True})
  with patch.object(baseline,'verify'),patch.object(safe,'recover') as recover:
   self.assertEqual(safe.guard(folder),0)
  recover.assert_not_called();self.assertTrue((folder/'guard-ready.json').exists())
 def test_parent_exit_recovers_without_model_or_network_contact(self):
  folder,job=self.job();client.write(self.local/'reconfigure-active.json',{'id':folder.name})
  with patch.object(baseline,'verify'),patch.object(safe,'process_birth',return_value=None),patch.object(safe,'recover',return_value={'network_ready':True,'full_restore':True}) as recover:
   self.assertEqual(safe.guard(folder),0)
  recover.assert_called_once();self.assertFalse((self.local/'reconfigure-active.json').exists())
  self.assertTrue(json.loads((folder/'done.json').read_text())['recovered'])
 def test_recovery_failure_remains_visible_and_blocks_updates(self):
  folder,job=self.job();(folder/'recover.request').touch()
  client.write(self.local/'reconfigure-active.json',{'id':folder.name})
  with patch.object(baseline,'verify'),patch.object(safe,'recover',side_effect=RuntimeError('DO-NOT-ECHO')):
   self.assertEqual(safe.guard(folder),1)
  result=json.loads((folder/'recovery-result.json').read_text())
  self.assertFalse(result['success']);self.assertNotIn('DO-NOT-ECHO',json.dumps(result))
  self.assertTrue((self.local/'reconfigure-active.json').exists())
 def test_pid_reuse_never_kills_an_unrelated_process(self):
  folder,job=self.job();client.write(folder/'worker.json',{'pid':42,'birth':123})
  with patch.object(safe,'process_birth',return_value=999),patch.object(safe.subprocess,'run') as run:
   safe.stop_worker(folder)
  run.assert_not_called()
 def test_stage_timeout_stops_owned_worker(self):
  folder,job=self.job();child=MagicMock(pid=42);child.wait.side_effect=[subprocess.TimeoutExpired('fixture',1),0]
  with patch.object(safe.subprocess,'Popen',return_value=child),patch.object(safe,'process_birth',return_value=123),patch.object(safe,'stop_worker') as stop:
   with self.assertRaises(TimeoutError):safe.run_stage(folder,job,'apply',1)
  stop.assert_called_once_with(folder);self.assertTrue((folder/'apply.go').exists())
 def test_blocked_named_pipe_cannot_block_the_guardian_indefinitely(self):
  folder,job=self.job();child=MagicMock(pid=42);child.wait.side_effect=[subprocess.TimeoutExpired('fixture',80),0]
  with patch.object(safe.subprocess,'Popen',return_value=child),patch.object(safe,'process_birth',return_value=123),patch.object(safe,'stop_worker') as stop:
   with self.assertRaisesRegex(RuntimeError,'independent deadline'):safe.bounded_restart(folder,job)
  stop.assert_called_once_with(folder)
 def test_guard_deadline_requests_recovery(self):
  folder,job=self.job()
  with patch.object(baseline,'verify'),patch.object(safe.time,'monotonic',side_effect=[0,1801]),patch.object(safe,'recover',return_value={'network_ready':True}) as recover:
   self.assertEqual(safe.guard(folder),0)
  recover.assert_called_once()
 def test_restart_uses_current_native_file_and_no_backup_yaml(self):
  folder,job=self.job();native=self.local/'clash-verge.yaml';native.write_text('mode: rule\n')
  with patch.object(safe.subprocess,'run',return_value=MagicMock(returncode=0)),patch.object(client,'root_path',return_value=self.local),patch.object(client,'api') as api:
   safe.restart_current_runtime(job)
  self.assertEqual(api.call_args.args[-1],{'payload':'mode: rule\n'})
 def test_real_windows_process_identity_is_stable(self):
  self.assertIsNotNone(safe.process_birth(os.getpid()))
  child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])
  try:
   birth=safe.process_birth(child.pid);self.assertIsNotNone(birth)
   self.assertEqual(safe.process_birth(child.pid),birth)
  finally:child.kill();child.wait(timeout=5)
  self.assertIsNone(safe.process_birth(child.pid))
 def test_detached_guard_really_survives_parent_exit(self):
  # Run the actual guardian loop/process identity checks in a separate process.
  # Only its recovery side effects are replaced with a file-only simulation.
  scripts=self.local/'scripts';scripts.mkdir();local=self.local/'.local';local.mkdir()
  source=Path(safe.__file__).read_text(encoding='utf-8')
  start=source.index('def recover(');end=source.index('def guard(',start)
  source=source[:start]+"def recover(folder,job):\n save(folder,'simulated-recovery.json',{'parent_exited':True})\n return {'network_ready':True,'simulation':True}\n\n"+source[end:]
  (scripts/'safe_reconfigure.py').write_text(source,encoding='utf-8')
  (scripts/'client.py').write_text("from pathlib import Path\nimport json\nLOCAL=Path(__file__).resolve().parent.parent/'.local'\ndef write(path,data):\n path.write_text(json.dumps(data),encoding='utf-8')\n")
  (scripts/'baseline.py').write_text('def verify(): return {}\n')
  job_id='b'*32;folder=local/'reconfigure-runs'/job_id;folder.mkdir(parents=True)
  snapshot=local/'snapshots'/'original';snapshot.mkdir(parents=True)
  parent=scripts/'parent.py'
  parent.write_text("import os,subprocess,sys,time\nfrom pathlib import Path\nimport safe_reconfigure as s\nf=Path(sys.argv[1]);s.save(f,'job.json',{'id':f.name,'snapshot':sys.argv[2],'parent_pid':os.getpid(),'parent_birth':s.process_birth(os.getpid())})\nwith (f/'guard.log').open('wb') as out:\n subprocess.Popen([sys.executable,s.__file__,'--guard',str(f)],stdout=out,stderr=subprocess.STDOUT,creationflags=subprocess.DETACHED_PROCESS|subprocess.CREATE_NEW_PROCESS_GROUP)\nlimit=time.monotonic()+10\nwhile not (f/'guard-ready.json').exists():\n if time.monotonic()>limit:raise SystemExit(2)\n time.sleep(.1)\nos._exit(0)\n")
  result=subprocess.run([sys.executable,str(parent),str(folder),str(snapshot)],capture_output=True,timeout=15)
  self.assertEqual(result.returncode,0,result.stderr)
  deadline=time.monotonic()+10
  while not (folder/'done.json').exists() and time.monotonic()<deadline:time.sleep(.1)
  self.assertTrue((folder/'simulated-recovery.json').exists())
  self.assertTrue(json.loads((folder/'recovery-result.json').read_text())['success'])

if __name__=='__main__':unittest.main()
