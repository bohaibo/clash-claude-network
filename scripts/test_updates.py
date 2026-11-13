"""Offline fixtures only: source parsing, migration, publishing and recovery."""
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import client,update,gui_server

class RuleSources(unittest.TestCase):
 def encoded(self,items):return client.yaml.safe_dump({'payload':items}).encode()
 def test_domain_semantics_and_deduplication(self):
  values=client.parse_rule_source('domains',self.encoded(['WWW.Example.cn','+.example.cn','+.cn','WWW.Example.cn']))
  self.assertEqual(values,['www.example.cn','+.example.cn','+.cn'])
 def test_invalid_or_overbroad_domain_input_is_rejected(self):
  for value in ['*','+.com','+.net','*.example.cn','+.douban.*','example.cn,DIRECT','https://example.cn','a..cn',True,None,{},' a.cn']:
   with self.subTest(value=value),self.assertRaises((AssertionError,ValueError,TypeError)):
    client.parse_rule_source('domains',self.encoded([value]))
  for raw in [b'<html>error</html>',b'payload: null',b'payload: []\nrules: []',b'[SwitchyOmega Conditions]']:
   with self.subTest(raw=raw),self.assertRaises(Exception):client.parse_rule_source('domains',raw)
 def test_cidr_fail_closed_and_fake_ip_filter(self):
  self.assertEqual(client.parse_rule_source('lanCidrs',self.encoded(['198.18.0.0/15','10.0.0.0/8'])),['10.0.0.0/8'])
  for cidr in ['0.0.0.0/0','198.18.0.0/16','1.0.0.1/24','example.cn']:
   with self.subTest(cidr=cidr),self.assertRaises((AssertionError,ValueError)):
    client.parse_rule_source('ips',self.encoded([cidr]))
 def test_failed_download_never_overwrites_applied_sources(self):
  with tempfile.TemporaryDirectory(dir=client.LOCAL) as folder:
   path=Path(folder);client.write(path/'sources.json',{'applied':'fixture'})
   with patch.object(client,'LOCAL',path),patch.object(client,'rule_source_bytes',return_value=self.encoded(['+.cn'])):
    with self.assertRaises((AssertionError,ValueError)):client.download_rules()
   self.assertEqual(client.load(path/'sources.json'),{'applied':'fixture'})
   self.assertFalse((path/'sources-candidate.json').exists())

class Updates(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory(dir=client.LOCAL,prefix='update-test-');self.addCleanup(temp.cleanup)
  self.local=Path(temp.name);self.root=self.local/'client';(self.root/'profiles').mkdir(parents=True)
  for module in (client,update):self.enterContext(patch.object(module,'LOCAL',self.local))
  self.metadata={k:{'url':v,'sha256':'a'*64,'count':1} for k,v in client.SOURCES.items()}
  self.data=dict(json.loads((client.PROJECT/'assets/claude-policy.json').read_text()),
   domains=['+.cn'],ips=['1.0.1.0/24'],privateDomains=['+.lan'],lanCidrs=['10.0.0.0/8'],ruleSources=self.metadata)
  self.script=self.root/'profiles/project.js';self.script.write_text(self.wrapper(self.data),encoding='utf-8')
  self.plan={'root':str(self.root),'subscription_uid':'a','script_uid':'project','script_file':'project.js','node':'Chosen','source_digest':update.source_digest(),'snapshot':'original-preserved'}
  self.profiles={'current':'a','items':[{'uid':'a','option':{'script':'project'}},{'uid':'project','file':'project.js'}]}
  client.write(self.root/'profiles.yaml',self.profiles);client.write(self.local/'plan.json',self.plan)
  self.config={'mode':'rule','find-process-mode':'strict','rule-providers':{
   name:{'payload':['fixture']} for name in ('strict-cn-domain','strict-cncidr','strict-private-domain','strict-lancidr')}}
  client.write(self.root/'clash-verge.yaml',self.config);client.write(self.local/'sources.json',self.metadata)
  self.fresh={key:copy.deepcopy(self.data[key]) for key in (*update.KEYS,'ruleSources')}
  self.download=self.enterContext(patch.object(update,'download_rules',side_effect=lambda:copy.deepcopy(self.fresh)))
  self.enterContext(patch.object(update,'select',return_value={}))
  self.build=self.enterContext(patch.object(update,'build',side_effect=lambda *args:(self.wrapper(args[-1]),json.dumps(self.config),{},None)))
  self.calls=[];self.api=self.enterContext(patch.object(update,'api',side_effect=self.controller))
 def wrapper(self,data):
  return 'function main(c){return c;}\n// PORTABLE-CLAUDE-NETWORK-V1\n;(()=>{ const before=main; const data='+json.dumps(data)+'; main=function(config){return secureMain(before(config),data,"Chosen","Claude-Overseas-Pinned");};})();\n'
 def controller(self,root,method,path,body=None):
  self.calls.append((method,path))
  if path=='/configs':return self.config
  if path=='/rules':return {'rules':[{'type':'GeoSite','payload':'geolocation-!cn','proxy':client.GROUP},{'type':'RuleSet','payload':'strict-cn-domain','proxy':'DIRECT'}]}
  if path=='/providers/rules':return {'providers':{k:{'ruleCount':len(v['payload'])} for k,v in self.config['rule-providers'].items()}}
  return {}
 def mutate_domains(self):self.fresh['domains'].append('+.example.cn')
 def snapshot_files(self):
  return {p:p.read_bytes() for p in (self.script,self.root/'clash-verge.yaml',self.local/'plan.json',self.local/'sources.json')}
 def test_no_change_does_not_reload_or_build(self):
  result=update.update();self.assertTrue(result['unchanged']);self.api.assert_not_called();self.build.assert_not_called()
 def test_scheduled_source_migration_stops_before_download(self):
  legacy=copy.deepcopy(self.data);legacy.pop('ruleSources');self.script.write_text(self.wrapper(legacy))
  with self.assertRaisesRegex(AssertionError,'migration requires manual'):update.update()
  self.download.assert_not_called();self.api.assert_not_called()
 def test_manual_migration_only_waives_domain_delta(self):
  legacy=copy.deepcopy(self.data);legacy.pop('ruleSources');self.script.write_text(self.wrapper(legacy))
  self.fresh['domains']+=['+.d'+str(i)+'.cn' for i in range(150)]
  result=update.update(migrate_source=True)
  self.assertTrue(result['migrated']);self.assertEqual(result['changes']['domains']['added'],150)
  self.assertEqual(client.parse_project_wrapper(self.script.read_text())[2]['domains'],self.fresh['domains'])
  self.assertEqual(client.load(self.local/'plan.json')['snapshot'],'original-preserved')
  self.assertFalse((self.local/'rule-update-pending.json').exists())
 def test_manual_migration_does_not_waive_ip_delta(self):
  legacy=copy.deepcopy(self.data);legacy.pop('ruleSources');self.script.write_text(self.wrapper(legacy))
  self.fresh['ips']=['10.0.'+str(i)+'.0/24' for i in range(150)]
  with self.assertRaisesRegex(AssertionError,'Large source delta'):update.update(True)
  self.build.assert_not_called()
 def test_even_manual_update_retains_delta_protection_after_migration(self):
  self.fresh['domains']+=['+.d'+str(i)+'.cn' for i in range(150)]
  with self.assertRaisesRegex(AssertionError,'Large source delta'):update.update(True)
  self.api.assert_not_called()
 def test_lan_changes_are_never_silently_published(self):
  self.fresh['privateDomains'].append('+.test')
  with self.assertRaisesRegex(AssertionError,'Private/LAN source'):update.update(True)
  self.api.assert_not_called()
 def test_native_validation_failure_preserves_all_applied_records(self):
  self.mutate_domains();before=self.snapshot_files();self.build.side_effect=RuntimeError('native fixture failure')
  with self.assertRaises(RuntimeError):update.update()
  self.assertEqual(self.snapshot_files(),before);self.api.assert_not_called()
 def test_partial_reload_failure_restores_all_records_and_runtime(self):
  self.mutate_domains();before=self.snapshot_files();self.api.side_effect=[RuntimeError('partial reload'),{}]
  with self.assertRaisesRegex(RuntimeError,'partial reload'):update.update()
  self.assertEqual(self.snapshot_files(),before);self.assertEqual(self.api.call_count,2)
  self.assertFalse((self.local/'rule-update-pending.json').exists())
 def test_disk_publish_failure_rolls_back(self):
  self.mutate_domains();before=self.snapshot_files();original=update.atomic_bytes;failed=[]
  def write(path,value):
   if path.name=='sources.json' and not failed:failed.append(True);raise PermissionError('fixture')
   return original(path,value)
  with patch.object(update,'atomic_bytes',side_effect=write),self.assertRaises(PermissionError):update.update()
  self.assertEqual(self.snapshot_files(),before)
  self.assertFalse((self.local/'rule-update-pending.json').exists())
 def test_provider_load_mismatch_rolls_back(self):
  self.mutate_domains();before=self.snapshot_files()
  def controller(root,method,path,body=None):
   if path=='/providers/rules':return {'providers':{}}
   return self.controller(root,method,path,body)
  self.api.side_effect=controller
  with self.assertRaisesRegex(AssertionError,'provider count mismatch'):update.update()
  self.assertEqual(self.snapshot_files(),before)
 def test_incomplete_rollback_is_reported_and_recovered_before_next_update(self):
  self.mutate_domains();before=self.snapshot_files();self.api.side_effect=RuntimeError('offline fixture')
  with self.assertRaisesRegex(AssertionError,'rollback incomplete') as error:update.update()
  self.assertTrue(update.failure_result(error.exception)['recovery_required'])
  self.api.side_effect=self.controller
  self.assertTrue(update.recover_pending());self.assertEqual(self.snapshot_files(),before)
  self.assertFalse((self.local/'rule-update-pending.json').exists())
 def test_recovery_refuses_later_user_edit(self):
  self.mutate_domains();self.api.side_effect=RuntimeError('fixture')
  with self.assertRaises(AssertionError):update.update()
  self.script.write_text('user change')
  self.api.reset_mock()
  with self.assertRaisesRegex(AssertionError,'needs review'):update.recover_pending()
  self.assertEqual(self.script.read_text(),'user change');self.api.assert_not_called()
 def test_skipped_association_never_downloads(self):
  self.profiles['items'][0]['option']['script']='user';client.write(self.root/'profiles.yaml',self.profiles)
  result=update.update();self.assertTrue(result['skipped']);self.download.assert_not_called()
 def test_gui_reports_skipped_results_and_only_safe_counts(self):
  app=gui_server.App(local=self.local,open_browser=False,idle_exit=False)
  with patch.object(app,'task_status',return_value={'installed':False}):
   client.write(self.local/'update-result.json',{'success':True,'skipped':True,
    'time':__import__('datetime').datetime.now().astimezone().isoformat(),
    'changes':{'domains':{'after':110189,'added':1,'removed':0,'secret':'withheld'},'unapproved':{}}})
   summary=app.update_summary()
  self.assertTrue(summary['stale']);self.assertTrue(summary['last']['skipped'])
  self.assertEqual(summary['last']['changes'],{'domains':{'after':110189,'added':1,'removed':0}})
  command,_=app._job_command('update',{},self.local)
  self.assertIn('--migrate-source',command)

if __name__=='__main__':unittest.main()
