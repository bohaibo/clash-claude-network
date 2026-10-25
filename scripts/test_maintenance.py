"""Offline maintenance regressions; no live network, registry or app changes."""
import json,os,shutil,subprocess,sys,unittest,ast
from pathlib import Path
from unittest.mock import patch
import app_proxies,client,update,test_snapshots
from operation_lock import operation_lock

class Maintenance(unittest.TestCase):
 def setUp(self):test_snapshots.Snapshots.setUp(self)
 def test_runtime_checks_survive_python_optimization(self):
  code="from client import validate\ntry: validate({'proxies':[]},{'proxies':[{}]},'fixture')\nexcept AssertionError: print('rejected')\nelse: raise SystemExit(2)"
  result=subprocess.run([sys.executable,'-O','-c',code],cwd=Path(__file__).parent,capture_output=True)
  self.assertEqual(result.returncode,0);self.assertIn(b'rejected',result.stdout)
  for name in ('client.py','baseline.py','update.py','browser_settings.py','browser_languages.py','verify.py','verify_core_exit.py','routing_checks.py'):
   tree=ast.parse((client.PROJECT/'scripts'/name).read_text(encoding='utf-8'))
   self.assertFalse(any(isinstance(node,ast.Assert) for node in ast.walk(tree)),name)
 def test_pending_recovery_blocks_updates_and_releases_lock(self):
  token='guard-'+'a'*32
  (self.local/'pending-firewall.json').write_text(json.dumps({'token':token,'snapshot':str(self.local/'snapshots'/'original')}))
  with self.assertRaises(RuntimeError):
   with operation_lock(self.local):self.fail('must not enter updater')
  (self.local/(token+'.commit')).write_text('completed')
  with operation_lock(self.local):pass
 def test_pending_recovery_rejects_path_like_tokens(self):
  (self.local/'pending-firewall.json').write_text(json.dumps({'token':'../../outside'}))
  with self.assertRaises(RuntimeError):
   with operation_lock(self.local):pass
 def guard_fixture(self,fail=False,force=False):
  project=self.local/'guard-fixture';scripts=project/'scripts';scripts.mkdir(parents=True)
  local=project/'.local';local.mkdir();first=local/'snapshots'/'first';first.mkdir(parents=True)
  (local/'snapshots'/'latest').mkdir()
  token='guard-'+'b'*32
  (local/'pending-firewall.json').write_text(json.dumps({'token':token,'snapshot':str(first)}))
  shutil.copy2(client.PROJECT/'scripts'/'operation_guard.ps1',scripts/'operation_guard.ps1')
  (scripts/'baseline.py').write_text("def verify():\n "+("raise ValueError('fixture integrity failure')" if fail else 'return {}')+'\n')
  (scripts/'system.ps1').write_text("param($Action,$Python)\n$env:CCN_SNAPSHOT | Add-Content -LiteralPath (Join-Path $PSScriptRoot '../captured-snapshot') -Encoding UTF8\n")
  (scripts/'probe.ps1').write_text("param($LocalDir,$Python)\n$ErrorActionPreference='Stop'\n. (Join-Path $PSScriptRoot 'operation_guard.ps1')\nResolve-PendingFirewall -LocalDir $LocalDir -Python $Python\nResolve-PendingFirewall -LocalDir $LocalDir -Python $Python\n",encoding='utf-8-sig')
  if force:
   with (scripts/'probe.ps1').open('a',encoding='utf-8') as output:output.write('Resolve-PendingFirewall -LocalDir $LocalDir -Python $Python -Force\n')
  result=subprocess.run(['powershell.exe','-NoProfile','-File',str(scripts/'probe.ps1'),str(local),sys.executable],capture_output=True)
  return result,project,local,first,token
 def test_guard_uses_original_snapshot_and_rolls_back_once(self):
  result,project,local,first,token=self.guard_fixture()
  self.assertEqual(result.returncode,0,result.stderr.decode('utf-8',errors='replace'))
  lines=(project/'captured-snapshot').read_text(encoding='utf-8-sig').splitlines()
  self.assertEqual(lines,[str(first)]);self.assertTrue((local/(token+'.commit')).exists())
 def test_bad_recovery_snapshot_cannot_change_firewall(self):
  result,project,local,first,token=self.guard_fixture(fail=True)
  self.assertNotEqual(result.returncode,0);self.assertFalse((project/'captured-snapshot').exists())
  self.assertFalse((local/(token+'.commit')).exists())
 def test_late_apply_failure_can_rollback_after_commit(self):
  result,project,local,first,token=self.guard_fixture(force=True)
  self.assertEqual(result.returncode,0)
  self.assertEqual((project/'captured-snapshot').read_text(encoding='utf-8-sig').splitlines(),[str(first),str(first)])
 def proxy_fixture(self,original_exists=True):
  self.saved=self.local/'before';self.saved.mkdir()
  self.apps=self.local/'应用设置';self.apps.mkdir()
  originals={'.gitconfig':'[user]\n name = Original user\n[http]\n proxy = http://original.invalid:8080\n',
             '.npmrc':'registry=https://original.invalid/\n//original.invalid/:_authToken=old-fixture\nproxy=http://original.invalid:8080\n',
             '.docker/config.json':json.dumps({'auths':{'old.invalid':{'auth':'old-fixture'}},'proxies':{'default':{'httpProxy':'http://original.invalid:8080'}}})}
  currents={'.gitconfig':'[user]\n name = New user\n[http]\n proxy = http://later.invalid:8080\n',
            '.npmrc':'registry=https://later.invalid/\n//later.invalid/:_authToken=new-fixture\nhttps-proxy=http://later.invalid:8080\n',
            '.docker/config.json':json.dumps({'auths':{'new.invalid':{'auth':'new-fixture'}},'proxies':{'default':{'httpProxy':'http://later.invalid:8080'}}})}
  entries={}
  for i,(name,value) in enumerate(originals.items()):
   current=self.apps/name;current.parent.mkdir(parents=True,exist_ok=True);current.write_text(currents[name],encoding='utf-8')
   copy=str(i)+'.original'
   if original_exists:(self.saved/copy).write_text(value,encoding='utf-8')
   entries[name]={'path':str(current),'copy':copy,'exists':original_exists}
  (self.saved/'system.json').write_text(json.dumps({'appFiles':entries}),encoding='utf-8')
  self.enterContext(patch.object(app_proxies,'FINAL',self.local))
  self.enterContext(patch.object(app_proxies,'snapshot',return_value=self.saved))
  self.enterContext(patch.object(app_proxies,'verify',return_value={}))
 def test_proxy_restore_preserves_later_account_and_other_fields(self):
  self.proxy_fixture();report=app_proxies.restore_apps();self.assertTrue(report['success'],report)
  git=shutil.which('git');current=self.apps/'.gitconfig'
  value=subprocess.run([git,'config','--file',str(current),'user.name'],capture_output=True,text=True)
  self.assertEqual(value.stdout.strip(),'New user')
  self.assertEqual(app_proxies.git_proxy_values(git,current),[('http.proxy','http://original.invalid:8080')])
  npm=(self.apps/'.npmrc').read_text();self.assertIn('registry=https://later.invalid/',npm);self.assertIn('_authToken=new-fixture',npm);self.assertIn('proxy=http://original.invalid:8080',npm)
  docker=json.loads((self.apps/'.docker/config.json').read_text());self.assertIn('new.invalid',docker['auths']);self.assertEqual(docker['proxies']['default']['httpProxy'],'http://original.invalid:8080')
  again=app_proxies.restore_apps();self.assertTrue(again['success']);self.assertEqual(again['changed'],[])
 def test_later_app_settings_survive_when_no_original_file_existed(self):
  self.proxy_fixture(original_exists=False);report=app_proxies.restore_apps();self.assertTrue(report['success'],report)
  self.assertEqual(app_proxies.git_proxy_values(shutil.which('git'),self.apps/'.gitconfig'),[])
  self.assertIn('new-fixture',(self.apps/'.npmrc').read_text())
  self.assertNotIn('https-proxy=',(self.apps/'.npmrc').read_text())
  docker=json.loads((self.apps/'.docker/config.json').read_text());self.assertIn('new.invalid',docker['auths']);self.assertNotIn('proxies',docker)
 def test_updater_skips_user_changed_script_before_downloading(self):
  plan={'root':str(self.root),'subscription_uid':'a','script_uid':'project','node':'中文节点'}
  (self.local/'plan.json').write_text(json.dumps(plan,ensure_ascii=False),encoding='utf-8')
  (self.root/'profiles.yaml').write_text('current: a\nitems:\n- uid: a\n  option: {script: user-choice}\n',encoding='utf-8')
  with patch.object(update,'LOCAL',self.local),patch.object(update,'download_rules') as download:
   report=update.update()
  self.assertTrue(report['skipped']);download.assert_not_called()
 def updater_lan_fixture(self):
  folder=self.root/'profiles';folder.mkdir()
  policy=json.loads((client.PROJECT/'assets/claude-policy.json').read_text())
  data=dict(policy,domains=['+.cn'],ips=['1.0.1.0/24'],privateDomains=['+.lan'],lanCidrs=['10.0.0.0/8'],lanSourceCidrs=['10.0.0.0/8','192.0.2.0/24'])
  text='function main(c){return c;}\n// PORTABLE-CLAUDE-NETWORK-V1\n;(()=>{ const before=main; const data='+json.dumps(data)+'; main=function(config){return secureMain(before(config),data,"Chosen","Claude-Overseas-Pinned");};})();\n'
  (folder/'project.js').write_text(text)
  (self.root/'profiles.yaml').write_text('current: a\nitems:\n- {uid: a, option: {script: project}}\n- {uid: project, file: project.js}\n')
  plan={'root':str(self.root),'subscription_uid':'a','script_uid':'project','script_file':'project.js','node':'Chosen'}
  (self.local/'plan.json').write_text(json.dumps(plan))
  fresh={key:data[key] for key in ('domains','ips','privateDomains')};fresh['lanCidrs']=list(data['lanSourceCidrs'])
  self.enterContext(patch.object(update,'LOCAL',self.local));return fresh
 def test_updater_reviews_raw_lan_changes_even_if_normalization_drops_them(self):
  fresh=self.updater_lan_fixture();fresh['lanCidrs'].append('203.0.113.0/24')
  with patch.object(update,'download_rules',return_value=fresh),patch.object(update,'build') as build:
   with self.assertRaisesRegex(AssertionError,'Private/LAN source changed'):update.update()
  build.assert_not_called()
 def test_updater_accepts_unchanged_raw_source_after_lan_normalization(self):
  fresh=self.updater_lan_fixture()
  with patch.object(update,'download_rules',return_value=fresh),patch.object(update,'select',return_value={}),patch.object(update,'build',side_effect=RuntimeError('fixture reached build')) as build:
   with self.assertRaisesRegex(RuntimeError,'fixture reached build'):update.update()
  build.assert_called_once()
 def test_invalid_docker_file_is_preserved_and_reported(self):
  self.proxy_fixture();docker=self.apps/'.docker/config.json';docker.write_text('{invalid fixture',encoding='utf-8')
  before=docker.read_bytes();report=app_proxies.restore_apps()
  self.assertFalse(report['success']);self.assertEqual(docker.read_bytes(),before)
  self.assertEqual(report['failed_items'][0]['file'],'.docker/config.json')
 def test_cli_does_not_print_subscription_contents_on_parse_failure(self):
  project=self.local/'cli-fixture';scripts=project/'scripts';scripts.mkdir(parents=True)
  for name in ('client.py','baseline.py','checks.py','routing_checks.py'):shutil.copy2(client.PROJECT/'scripts'/name,scripts/name)
  (self.root/'profiles').mkdir()
  (self.root/'profiles.yaml').write_text('current: a\nitems:\n- {uid: a, type: remote, file: source.yaml}\n')
  (self.root/'profiles'/'source.yaml').write_text('proxies: [DO-NOT-ECHO-FIXTURE-CREDENTIAL',encoding='utf-8')
  result=subprocess.run([sys.executable,str(scripts/'client.py'),'discover','--root',str(self.root)],capture_output=True)
  self.assertEqual(result.returncode,1);self.assertNotIn(b'DO-NOT-ECHO',result.stdout+result.stderr)
  self.assertTrue((project/'.local'/'client-error.log').exists())

if __name__=='__main__':unittest.main()
