"""Offline subscription-preserving restore tests. Fixtures stay under .local/."""
import copy,json,os,shutil,subprocess,unittest
from pathlib import Path
from unittest.mock import patch
import yaml,client,baseline,test_snapshots

class SubscriptionRestore(unittest.TestCase):
 def setUp(self):
  test_snapshots.Snapshots.setUp(self)
  (self.root/'profiles').mkdir()
  self.user_script='function main(config) { config.user=true; return config; }\n'
  self.put('profiles/user.js',self.user_script)
  self.put('profiles/a.yaml',{'proxies':[{'name':'Chosen','type':'socks5','server':'example.invalid','port':1234}],'rules':['MATCH,DIRECT']})
  self.index={'current':'a','items':[{'uid':'a','type':'remote','name':'First','file':'a.yaml','url':'https://example.invalid/first','option':{'script':'user','interval':60}},
                                {'uid':'user','type':'script','file':'user.js'}]}
  self.put('profiles.yaml',self.index)
  self.put('verge.yaml',{'enable_tun_mode':False,'theme':'original'})
  self.put('config.yaml',{'mode':'rule','ipv6':True})
  self.snapshot=client.snapshot_dir(create=True);client.backup(self.root,self.snapshot)
  self.before=(self.snapshot/'client'/'profiles.yaml').read_bytes()
 def put(self,name,value):
  file=self.root/name;file.parent.mkdir(parents=True,exist_ok=True)
  file.write_text(value if isinstance(value,str) else yaml.safe_dump(value,allow_unicode=True,sort_keys=False),encoding='utf-8')
 def apply(self,uid='a'):
  index=client.load(self.root/'profiles.yaml');item=next(x for x in index['items'] if x['uid']==uid)
  node=client.load(self.root/'profiles'/item['file'])['proxies'][0]['name']
  entry=next((x for x in index['items'] if x['uid']==item.get('option',{}).get('script')),None)
  def build(*args):
   previous=(self.root/'profiles'/entry['file']).read_text(encoding='utf-8') if entry else 'function main(config) { return config; }'
   previous=previous.split('// PORTABLE-CLAUDE-NETWORK-V1')[0]
   wrapper=previous+'\n// PORTABLE-CLAUDE-NETWORK-V1\nfunction secureMain(config) { return config; }\n;(()=>{ const before=main; const data={}; main=function(config){return secureMain(before(config),data,'+json.dumps(node)+',"Claude-Overseas-Pinned");};})();\n'
   candidate={'proxies':client.load(self.root/'profiles'/item['file'])['proxies'],'proxy-groups':[{'name':client.GROUP}],'tun':{'enable':True},'rules':['MATCH,'+client.GROUP]}
   text=yaml.safe_dump(candidate)
   (self.local/'candidate.js').write_text(wrapper,encoding='utf-8');(self.local/'candidate.yaml').write_text(text,encoding='utf-8')
   return wrapper,text,candidate,entry
  with patch.object(client,'build',side_effect=build),patch.object(client,'actual_core',return_value='fixture.exe'):
   client.prepare(self.root,node,uid,'SG')
  with patch.object(client,'api',return_value={}):client.apply_client()
  return json.loads((self.local/'plan.json').read_text(encoding='utf-8'))
 def add_subscription(self):
  index=client.load(self.root/'profiles.yaml')
  index['items'].append({'uid':'b','name':'Later','type':'remote','file':'b.yaml','url':'https://example.invalid/later','option':{'interval':90}})
  index['current']='b';self.put('profiles.yaml',index)
  self.put('profiles/b.yaml',{'proxies':[{'name':'New node','type':'socks5','server':'later.invalid','port':4321}],'rules':['MATCH,DIRECT']})
 def restore(self):
  with patch.object(client,'api') as api:
   result=client.restore_client();api.assert_not_called()
  return result
 def test_new_subscription_updated_nodes_and_selection_survive(self):
  plan=self.apply();self.add_subscription()
  self.assertNotEqual(plan['script_uid'],'user')
  self.assertEqual((self.root/'profiles/user.js').read_text(),self.user_script)
  source={'proxies':[{'name':'Updated node','type':'socks5','server':'updated.invalid','port':2345}]}
  self.put('profiles/a.yaml',source)
  index=client.load(self.root/'profiles.yaml');index['items'][0]['url']='https://example.invalid/updated';index['items'][0]['updated']=99
  self.put('profiles.yaml',index)
  a=(self.root/'profiles/a.yaml').read_bytes();b=(self.root/'profiles/b.yaml').read_bytes()
  result=self.restore();self.assertTrue(result['runtime_rebuild_required'])
  restored=client.load(self.root/'profiles.yaml')
  self.assertEqual(restored['current'],'b');self.assertEqual([x['uid'] for x in restored['items']],['a','user','b'])
  self.assertEqual(restored['items'][0]['option']['script'],'user')
  self.assertEqual(restored['items'][0]['url'],'https://example.invalid/updated');self.assertEqual(restored['items'][0]['updated'],99)
  self.assertEqual((self.root/'profiles/a.yaml').read_bytes(),a);self.assertEqual((self.root/'profiles/b.yaml').read_bytes(),b)
  self.assertEqual((self.root/'profiles/user.js').read_text(),self.user_script)
  self.assertFalse((self.root/'profiles'/plan['script_file']).exists())
  self.assertEqual((self.snapshot/'client'/'profiles.yaml').read_bytes(),self.before)
  self.put('clash-verge.yaml',client.load(self.root/'profiles/b.yaml'))
  with patch.object(client,'api',return_value={}) as api:
   done=client.finish_restore(timeout=0)
  self.assertTrue(done['client_restored']);self.assertTrue(done['runtime_reloaded'])
  self.assertIn('New node',api.call_args.args[3]['payload'])
 def test_stale_runtime_never_reloaded(self):
  self.apply();self.add_subscription();self.restore()
  with patch.object(client,'api') as api:result=client.finish_restore(timeout=0)
  self.assertFalse(result['client_restored']);api.assert_not_called()
 def test_later_subscription_can_be_configured_and_restored(self):
  first=self.apply();self.add_subscription();second=self.apply('b');self.restore()
  index=client.load(self.root/'profiles.yaml')
  a=next(x for x in index['items'] if x['uid']=='a');b=next(x for x in index['items'] if x['uid']=='b')
  self.assertEqual(a['option']['script'],'user');self.assertNotIn('script',b['option'])
  for plan in (first,second):self.assertFalse((self.root/'profiles'/plan['script_file']).exists())
 def test_deleted_subscription_is_not_resurrected(self):
  self.apply();self.add_subscription()
  index=client.load(self.root/'profiles.yaml');index['items']=[x for x in index['items'] if x['uid']!='a'];self.put('profiles.yaml',index)
  self.restore();self.assertNotIn('a',[x['uid'] for x in client.load(self.root/'profiles.yaml')['items']])
 def test_user_changed_script_association_is_preserved(self):
  self.apply();self.add_subscription()
  self.put('profiles/new-script.js','function main(c) { return c; }')
  index=client.load(self.root/'profiles.yaml');index['items'].append({'uid':'new-user','type':'script','file':'new-script.js'})
  index['items'][0]['option']['script']='new-user';self.put('profiles.yaml',index)
  self.restore();self.assertEqual(client.load(self.root/'profiles.yaml')['items'][0]['option']['script'],'new-user')
 def test_legacy_overwritten_script_is_unwrapped(self):
  plan=self.apply()
  wrapper=(self.root/'profiles'/plan['script_file']).read_text(encoding='utf-8')
  index=client.load(self.root/'profiles.yaml');index['items']=[x for x in index['items'] if x['uid']!=plan['script_uid']]
  index['items'][0]['option']['script']='user';self.put('profiles.yaml',index);self.put('profiles/user.js',wrapper+'\n// later user note\n')
  self.add_subscription();self.restore()
  text=(self.root/'profiles/user.js').read_text()
  self.assertNotIn('PORTABLE-CLAUDE-NETWORK-V1',text);self.assertIn('config.user=true',text);self.assertIn('later user note',text)
 def test_invalid_subscription_index_stops_preflight(self):
  self.apply();self.put('profiles.yaml','malformed index')
  before=(self.root/'verge.yaml').read_bytes()
  with self.assertRaises(ValueError):baseline.preflight_restore()
  self.assertEqual((self.root/'verge.yaml').read_bytes(),before)
 def test_repeated_apply_keeps_one_wrapper_and_original_association(self):
  first=self.apply();second=self.apply()
  self.assertEqual(first['script_uid'],second['script_uid'])
  self.restore()
  index=client.load(self.root/'profiles.yaml')
  self.assertEqual(index['items'][0]['option']['script'],'user')
  self.assertEqual(len(index['items']),2)
 def test_legacy_plan_without_journal_preserves_later_subscription(self):
  self.apply();self.add_subscription();(self.snapshot/'client-changes.json').unlink()
  self.restore();index=client.load(self.root/'profiles.yaml')
  self.assertEqual(index['current'],'b');self.assertEqual(index['items'][0]['option']['script'],'user')
 def test_repeat_restore_accepts_current_identical_runtime(self):
  self.apply();self.add_subscription();self.restore()
  self.put('clash-verge.yaml',client.load(self.root/'profiles/b.yaml'))
  with patch.object(client,'api',return_value={}):self.assertTrue(client.finish_restore(timeout=0)['client_restored'])
  self.restore()
  with patch.object(client,'api',return_value={}):self.assertTrue(client.finish_restore(timeout=0)['client_restored'])
 def test_only_project_network_keys_are_reverted(self):
  self.apply();self.put('verge.yaml',{'enable_tun_mode':True,'theme':'later-user-choice','extra':7})
  self.restore();settings=client.load(self.root/'verge.yaml')
  self.assertFalse(settings['enable_tun_mode']);self.assertEqual(settings['theme'],'later-user-choice');self.assertEqual(settings['extra'],7)
 def test_wrapper_parser_handles_punctuation_inside_json(self):
  prefix=self.user_script+'\n'
  data={'example':'fake );};})(); and ; main=function(config) strings'}
  node='name ");};})(); with punctuation'
  wrapper=prefix+'// PORTABLE-CLAUDE-NETWORK-V1\n'+(client.PROJECT/'assets/secure-main.js').read_text(encoding='utf-8')
  wrapper+='\n;(()=>{ const before=main; const data='+json.dumps(data)+'; main=function(config){return secureMain(before(config),data,'+json.dumps(node)+','+json.dumps(client.GROUP)+');};})();\n// user suffix'
  cleaned,has_suffix=client.strip_project_wrapper(wrapper)
  self.assertTrue(has_suffix);self.assertEqual(cleaned,prefix+'\n// user suffix')
 def test_wrong_provider_update_is_not_loaded(self):
  self.apply();self.add_subscription()
  self.put('profiles/b.yaml',{'proxies':[],'proxy-providers':{'current':{'type':'http','url':'https://example.invalid/current'}}})
  self.restore()
  self.put('clash-verge.yaml',{'proxies':[],'proxy-providers':{'current':{'type':'http','url':'https://example.invalid/stale'}}})
  with patch.object(client,'api') as api:result=client.finish_restore(timeout=0)
  self.assertFalse(result['client_restored']);api.assert_not_called()
 def test_invalid_active_source_stops_before_any_restore_writes(self):
  self.apply();self.add_subscription();self.put('profiles/b.yaml','not a subscription mapping')
  before=(self.root/'verge.yaml').read_bytes()
  with self.assertRaises(ValueError):baseline.preflight_restore()
  self.assertEqual((self.root/'verge.yaml').read_bytes(),before)

 def configured_manual_snapshot(self):
  plan=self.apply();manual=Path(client.create_snapshot(self.root)['snapshot'])
  os.environ['CCN_SNAPSHOT']=str(manual)
  return plan,manual
 def generated_current_runtime(self):
  # Simulate native regeneration from today's nodes with the real policy renderer.
  source=client.load(self.root/'profiles/a.yaml')
  data=json.loads((client.PROJECT/'assets/claude-policy.json').read_text())
  data.update(domains=['+.cn'],ips=['1.0.1.0/24'],privateDomains=['+.lan'],lanCidrs=['10.0.0.0/8'],lan=[])
  script=(client.PROJECT/'assets/secure-main.js').read_text()
  code=script+'\nprocess.stdout.write(JSON.stringify(secureMain('+json.dumps(source)+','+json.dumps(data)+',"Chosen",'+json.dumps(client.GROUP)+')));'
  result=subprocess.run([shutil.which('node') or 'node','-e',code],capture_output=True,text=True,encoding='utf-8',check=True)
  return json.loads(result.stdout)
 def test_configured_manual_snapshot_keeps_project_and_current_nodes(self):
  plan,manual=self.configured_manual_snapshot()
  original=(manual/'client'/'profiles.yaml').read_bytes()
  source=client.load(self.root/'profiles/a.yaml');source['proxies'][0]['port']=2345
  self.put('profiles/a.yaml',source)
  script=self.root/'profiles'/plan['script_file'];text=script.read_text()+'\n// later rule/user update\n';script.write_text(text)
  result=self.restore();self.assertTrue(result['client_restored']);self.assertEqual(result['project_node'],'Chosen')
  self.assertEqual(client.load(self.root/'profiles.yaml')['items'][0]['option']['script'],plan['script_uid'])
  self.assertEqual(script.read_text(),text);self.assertTrue(client.load(self.root/'verge.yaml')['enable_tun_mode'])
  self.assertEqual(client.load(self.root/'profiles/a.yaml'),source)
  self.assertEqual((manual/'client'/'profiles.yaml').read_bytes(),original)
  # A stale runtime with old node definitions must still be rejected.
  with patch.object(client,'api') as api:failed=client.finish_restore(timeout=0)
  self.assertFalse(failed['client_restored']);api.assert_not_called()
  self.restore();self.put('clash-verge.yaml',self.generated_current_runtime())
  with patch.object(client,'api',return_value={}) as api:done=client.finish_restore(timeout=0)
  self.assertTrue(done['client_restored']);self.assertIn('2345',api.call_args.args[3]['payload'])
  # Identical but validated regeneration is accepted on a second restore.
  self.restore()
  with patch.object(client,'api',return_value={}):self.assertTrue(client.finish_restore(timeout=0)['client_restored'])
 def test_manual_snapshot_missing_project_script_stops_preflight(self):
  plan,_=self.configured_manual_snapshot();(self.root/'profiles'/plan['script_file']).unlink()
  before=(self.root/'profiles.yaml').read_bytes()
  with self.assertRaisesRegex(ValueError,'missing or changed'):baseline.preflight_restore()
  self.assertEqual((self.root/'profiles.yaml').read_bytes(),before)
 def test_manual_snapshot_missing_selected_node_never_substitutes(self):
  self.configured_manual_snapshot()
  source=client.load(self.root/'profiles/a.yaml');source['proxies'][0]['name']='Different node';self.put('profiles/a.yaml',source)
  with self.assertRaisesRegex(ValueError,'no replacement selected'):baseline.preflight_restore()
 def test_manual_snapshot_runtime_without_pinned_group_is_not_loaded(self):
  self.configured_manual_snapshot();self.restore()
  self.put('clash-verge.yaml',client.load(self.root/'profiles/a.yaml'))
  with patch.object(client,'api') as api:result=client.finish_restore(timeout=0)
  self.assertFalse(result['client_restored']);api.assert_not_called()
 def test_manual_snapshot_removes_only_later_project_script(self):
  first,manual=self.configured_manual_snapshot();self.add_subscription();second=self.apply('b')
  self.restore();index=client.load(self.root/'profiles.yaml')
  self.assertEqual(index['current'],'b')
  self.assertEqual(index['items'][0]['option']['script'],first['script_uid'])
  self.assertNotIn('script',next(x for x in index['items'] if x['uid']=='b')['option'])
  self.assertTrue((self.root/'profiles'/first['script_file']).exists())
  self.assertFalse((self.root/'profiles'/second['script_file']).exists())
 def test_manual_snapshot_respects_later_user_script_selection(self):
  self.configured_manual_snapshot()
  index=client.load(self.root/'profiles.yaml');index['items'][0]['option']['script']='user';self.put('profiles.yaml',index)
  result=self.restore();self.assertIsNone(result['project_node'])
  self.assertEqual(client.load(self.root/'profiles.yaml')['items'][0]['option']['script'],'user')

if __name__=='__main__':unittest.main()
