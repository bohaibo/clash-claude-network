"""Snapshot regressions; fixtures and logs stay under .local/, never touch live settings."""
import ctypes,json,os,tempfile,unittest,subprocess,sys
from pathlib import Path
from unittest.mock import patch
import baseline,client,browser_languages
from operation_lock import operation_lock

class Snapshots(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory(dir=client.LOCAL,prefix='snapshot-test-')
  self.addCleanup(self.temp.cleanup)
  self.local=Path(self.temp.name);self.root=self.local/'fake-client';self.root.mkdir()
  for name in ('profiles.yaml','clash-verge.yaml'):(self.root/name).write_text('{}',encoding='utf-8')
  self.enterContext(patch.object(client,'LOCAL',self.local))
  self.enterContext(patch.object(client,'SNAPSHOTS',self.local/'snapshots'))
  self.enterContext(patch.object(baseline,'FINAL',self.local))
  self.enterContext(patch.object(browser_languages,'FINAL',self.local))
  self.enterContext(patch.dict(os.environ))
  os.environ.pop('CCN_SNAPSHOT',None)
 def exclusive_lock(self,path):
  path.write_text('runtime',encoding='utf-8')
  create=ctypes.windll.kernel32.CreateFileW
  create.argtypes=[ctypes.c_wchar_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_uint32,ctypes.c_void_p]
  create.restype=ctypes.c_void_p
  handle=create(str(path),0x80000000,0,None,3,0,None)
  self.assertNotEqual(handle,ctypes.c_void_p(-1).value)
  close=ctypes.windll.kernel32.CloseHandle;close.argtypes=[ctypes.c_void_p]
  self.addCleanup(close,handle)
 def test_running_client_lock_and_restore_without_plan(self):
  self.exclusive_lock(self.root/'singleton-instance.lock')
  (self.root/'logs').mkdir();(self.root/'logs'/'log.txt').write_text('volatile')
  snapshot=Path(client.create_snapshot(self.root)['snapshot'])
  self.assertFalse((snapshot/'client'/'singleton-instance.lock').exists())
  self.assertFalse((snapshot/'client'/'logs').exists())
  os.environ['CCN_SNAPSHOT']=str(snapshot)
  (snapshot/'system.json').write_text('{}')
  client.seal();baseline.verify()
  (self.root/'profiles.yaml').write_text('{items: [], user-update: preserved}')
  with patch.object(client,'api',return_value={}) as api:
   self.assertTrue(client.restore_client()['client_restored'])
   api.assert_called_once()
  self.assertEqual(client.load(self.root/'profiles.yaml'),{'items':[],'user-update':'preserved'})
  (snapshot/'system.json').write_text('tampered')
  with self.assertRaises(AssertionError):baseline.verify()
 def test_transient_recovery_files_are_not_sealed(self):
  snapshot=Path(client.create_snapshot(self.root)['snapshot']);os.environ['CCN_SNAPSHOT']=str(snapshot)
  temporary=snapshot/'recovery.json.new';temporary.write_text('temporary')
  (snapshot/'system.json').write_text('{}')
  client.seal();temporary.unlink();baseline.verify()
  self.assertNotIn(temporary.name,json.loads((snapshot/'manifest.json').read_text())['extra_sha256'])
 def test_locked_configuration_is_not_silently_skipped(self):
  self.exclusive_lock(self.root/'profiles.yaml')
  with self.assertRaises(OSError):client.create_snapshot(self.root)
  self.assertEqual(client.snapshot_roots(),[])
 def test_manual_snapshot_preserves_first_automatic_backup(self):
  original=client.snapshot_dir(create=True);client.backup(self.root,original)
  manifest=(original/'manifest.json').read_bytes()
  (self.root/'profiles.yaml').write_text('changed')
  manual=Path(client.create_snapshot(self.root)['snapshot'])
  self.assertNotEqual(manual,original)
  self.assertEqual(client.snapshot_dir(),original)
  self.assertEqual(baseline.snapshot(),original)
  self.assertEqual((original/'manifest.json').read_bytes(),manifest)
  self.assertEqual((original/'client'/'profiles.yaml').read_text(),'{}')

 def test_browser_increment_preserves_sealed_original_and_restores_new_profile(self):
  snapshot=Path(client.create_snapshot(self.root)['snapshot']);os.environ['CCN_SNAPSHOT']=str(snapshot)
  browser=self.local/'browser';self.enterContext(patch.object(browser_languages,'ROOTS',{'Chrome':browser}))
  def profile(name,language):
   path=browser/name/'Preferences';path.parent.mkdir(parents=True)
   path.write_text(json.dumps({'intl':{'accept_languages':language},'other':'preserved'}),encoding='utf-8')
   return path
  first=profile('Default','original-first')
  browser_languages.capture();client.seal()
  original=(snapshot/'browser-languages-before.json').read_bytes()
  before=json.loads((snapshot/'manifest.json').read_text(encoding='utf-8'))
  second=profile('Profile 1','original-second')
  browser_languages.capture();client.seal();baseline.verify()
  self.assertEqual((snapshot/'browser-languages-before.json').read_bytes(),original)
  after=json.loads((snapshot/'manifest.json').read_text(encoding='utf-8'))
  self.assertEqual(before['sha256'],after['sha256']);self.assertEqual(before['extra_sha256'],after['extra_sha256'])
  self.assertEqual(len(after['incremental_sha256']),1)
  with patch.object(browser_languages,'running',return_value=False):
   browser_languages.update()
   unrecorded=profile('Profile 2','leave-alone')
   manifest=(snapshot/'manifest.json').read_bytes()
   browser_languages.update(restore=True)
  self.assertEqual((snapshot/'manifest.json').read_bytes(),manifest)
  self.assertEqual(json.loads(first.read_text())['intl'],{'accept_languages':'original-first'})
  self.assertEqual(json.loads(second.read_text())['intl'],{'accept_languages':'original-second'})
  self.assertEqual(json.loads(unrecorded.read_text())['intl']['accept_languages'],'leave-alone')
  increment=snapshot/next(iter(after['incremental_sha256']))
  increment.write_text('{}')
  with self.assertRaises(ValueError):baseline.verify()

 def test_partial_restore_does_not_reload_and_reports_failed_file(self):
  snapshot=Path(client.create_snapshot(self.root)['snapshot']);os.environ['CCN_SNAPSHOT']=str(snapshot)
  replace=Path.replace
  def locked_replace(source,destination):
   if Path(destination).name=='profiles.yaml':raise PermissionError('fixture lock')
   return replace(source,destination)
  with patch.object(Path,'replace',locked_replace),patch.object(client,'api') as api:
   result=client.restore_client()
  self.assertFalse(result['client_restored']);self.assertEqual(result['status'],'partial')
  self.assertEqual(result['failed_items'][0]['file'],'profiles.yaml');api.assert_not_called()
  self.assertTrue((self.local/'client-restore-result.json').exists())

 def test_reload_failure_is_not_success(self):
  snapshot=Path(client.create_snapshot(self.root)['snapshot']);os.environ['CCN_SNAPSHOT']=str(snapshot)
  with patch.object(client,'api',side_effect=RuntimeError('fixture')):
   result=client.restore_client()
  self.assertFalse(result['client_restored']);self.assertFalse(result['runtime_reloaded'])
  self.assertEqual(result['failed_items'][0]['operation'],'reload')

 def test_restore_preflight_fails_before_writing(self):
  snapshot=Path(client.create_snapshot(self.root)['snapshot']);os.environ['CCN_SNAPSHOT']=str(snapshot)
  baseline.preflight_restore()
  (snapshot/'client'/'profiles.yaml').write_text('tampered')
  original=(self.root/'profiles.yaml').read_bytes()
  with self.assertRaises(AssertionError):baseline.preflight_restore()
  self.assertEqual((self.root/'profiles.yaml').read_bytes(),original)

 def test_unsealed_system_snapshot_is_rejected(self):
  snapshot=Path(client.create_snapshot(self.root)['snapshot']);os.environ['CCN_SNAPSHOT']=str(snapshot)
  (snapshot/'system.json').write_text('{}')
  manifest=(snapshot/'manifest.json').read_bytes()
  with self.assertRaises(ValueError):baseline.preflight_restore()
  self.assertEqual((snapshot/'manifest.json').read_bytes(),manifest)
  client.seal();baseline.preflight_restore()

 def test_powershell_and_python_share_lock_and_release_on_exit(self):
  probe=self.local/'probe.ps1'
  probe.write_text('''param([string]$LockFile)
$stream=[IO.File]::Open($LockFile,[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::ReadWrite)
try {try {$stream.Lock(0,1)} catch {exit 23}} finally {$stream.Dispose()}
''',encoding='utf-8-sig')
  command=['powershell.exe','-NoProfile','-File',str(probe),str(self.local/'operation.lock')]
  with operation_lock(self.local):
   self.assertEqual(subprocess.run(command,capture_output=True).returncode,23)
  self.assertEqual(subprocess.run(command,capture_output=True).returncode,0)
  with operation_lock(self.local):pass

 def test_manual_only_snapshot_policy_matches(self):
  manual=Path(client.create_snapshot(self.root)['snapshot'])
  self.assertEqual(client.snapshot_dir(),manual);self.assertEqual(baseline.snapshot(),manual)

 def test_operation_lock_released_after_process_is_killed(self):
  code="from pathlib import Path; from operation_lock import operation_lock; import sys;\nwith operation_lock(Path(sys.argv[1])):\n print('ready',flush=True);sys.stdin.read(1)\n"
  process=subprocess.Popen([sys.executable,'-c',code,str(self.local)],cwd=Path(__file__).parent,
                           stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  try:
   self.assertEqual(process.stdout.readline().strip(),b'ready')
   with self.assertRaises(RuntimeError):
    with operation_lock(self.local):pass
  finally:
   process.kill();process.communicate(timeout=5)
  with operation_lock(self.local):pass

 def test_entry_preflight_failure_never_stops_or_restarts_apps(self):
  # Run an isolated copy of the real entry flow with fake process commands and
  # a deliberately failing baseline. No registry/firewall/client code is invoked.
  project=self.local/'entry-fixture';scripts=project/'scripts';scripts.mkdir(parents=True)
  source=(client.PROJECT/'scripts'/'run.ps1').read_text(encoding='utf-8-sig')
  start=source.index('$identity=');end=source.index('# The same byte-range lock',start)
  source=source[:start]+source[end:] # Fixture bypasses elevation; it never requests UAC.
  sentinel=project/'unexpected-process-action'
  mocks='''
function Get-Process { [pscustomobject]@{ProcessName='fixture';Path='fixture.exe'} }
function Stop-Process { 'stopped' | Set-Content -LiteralPath (Join-Path $PSScriptRoot '../unexpected-process-action') }
function Get-ScheduledTask { 'queried' | Set-Content -LiteralPath (Join-Path $PSScriptRoot '../unexpected-process-action') }
function Start-Process { throw 'Unexpected process start' }
function Start-ScheduledTask { throw 'Unexpected task start' }
'''
  first,rest=source.split('\n',1);(scripts/'run.ps1').write_text(first+'\n'+mocks+rest,encoding='utf-8-sig')
  (scripts/'firewall_rules.ps1').write_text('# No live helpers in fixture')
  (scripts/'operation_guard.ps1').write_text((client.PROJECT/'scripts'/'operation_guard.ps1').read_text(encoding='utf-8-sig'),encoding='utf-8-sig')
  (scripts/'baseline.py').write_text("def preflight_restore():\n raise ValueError('fixture integrity failure')\n")
  run=subprocess.run(['powershell.exe','-NoProfile','-File',str(scripts/'run.ps1'),'-Action','restore',
                      '-Snapshot','fixture','-Acknowledged','-NoPause'],capture_output=True)
  self.assertEqual(run.returncode,1);self.assertFalse(sentinel.exists())
  result=json.loads((project/'.local'/'last-run.json').read_text(encoding='utf-8-sig'))
  self.assertFalse(result['success']);self.assertEqual(result['failedStep'],1)
  with operation_lock(project/'.local'):pass
  for name in ('earlier','later'):
   folder=project/'.local'/'snapshots'/name;folder.mkdir(parents=True)
   (folder/'manifest.json').write_text(json.dumps({'time':'fixture','kind':'manual','computer':'fixture','user_sid':'fixture','client_root':'fixture','sha256':{}}))
  (project/'.local'/'plan.json').write_text(json.dumps({'snapshot':str(project/'.local'/'snapshots'/'earlier')}))
  listed=subprocess.run(['powershell.exe','-NoProfile','-File',str(scripts/'run.ps1'),'-Action','list-snapshots'],
                        capture_output=True,encoding='utf-8')
  self.assertEqual(listed.returncode,0)
  line=next(line for line in listed.stdout.splitlines() if 'earlier' in line)
  self.assertIn('<配置及补充改动关联>',line)
  # A successful -SkipBrowser restore must neither invoke browser helpers nor
  # include a browser step in the progress count. All helpers remain fixtures.
  (scripts/'baseline.py').write_text('def preflight_restore(): return {}\n')
  (scripts/'client.py').write_text("import json;print(json.dumps({'client_restored':True,'failed_items':[],'runtime_rebuild_required':False}))\n")
  (scripts/'update_task.ps1').write_text('param($Action,$Python)\n')
  (scripts/'system.ps1').write_text('param($Action,$Python)\n')
  fixture_snapshot=project/'.local'/'snapshots'/'fixture';fixture_snapshot.mkdir(exist_ok=True)
  (fixture_snapshot/'system.json').write_text('{}')
  for name in ('browser_settings.py','browser_languages.py'):
   (scripts/name).write_text("from pathlib import Path\nPath(__file__).parent.parent.joinpath('browser-touched').write_text('unexpected')\n")
  skipped=subprocess.run(['powershell.exe','-NoProfile','-File',str(scripts/'run.ps1'),'-Action','restore',
                          '-Snapshot','fixture','-Acknowledged','-NoPause','-SkipBrowser'],capture_output=True)
  self.assertEqual(skipped.returncode,0,skipped.stderr.decode('utf-8',errors='replace'))
  self.assertFalse((project/'browser-touched').exists())
  sentinel.unlink(missing_ok=True)
  runner=(scripts/'run.ps1').read_text(encoding='utf-8-sig')
  runner=runner.replace("function Get-Process {", "function Get-Service { throw 'Fixture preflight failure' }\nfunction Get-Process {",1)
  (scripts/'run.ps1').write_text(runner,encoding='utf-8-sig')
  stopped=subprocess.run(['powershell.exe','-NoProfile','-File',str(scripts/'run.ps1'),'-Action','apply',
                          '-Node','fixture','-Acknowledged','-NoPause','-SkipBrowser'],capture_output=True)
  self.assertEqual(stopped.returncode,1);self.assertFalse(sentinel.exists())

if __name__=='__main__':unittest.main()
