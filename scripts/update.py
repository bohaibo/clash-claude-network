"""Transactional updates of fixed Loyalsoldier sources. No service probes."""
from checks import require
import argparse,datetime,hashlib,json,ipaddress,os,re,sys,uuid
from client import LOCAL,PROJECT,SOURCES,root_path,load,select,build,download_rules,api,safe_file,write,parse_project_wrapper
from atomic_files import replace_file
from config_health import source_digest,assert_live
from operation_lock import operation_lock
from performance import timed
import chain_profile
import managed_chain
from safe_yaml import dumps as yaml_dumps

KEYS=('domains','ips','privateDomains','lanCidrs')

def atomic_bytes(path,value):
 temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.new')
 try:
  temp.write_bytes(value);replace_file(temp,path)
 finally:temp.unlink(missing_ok=True)

def digest(value):return hashlib.sha256(value).hexdigest() if value is not None else None

def transaction_targets(record):
 root=root_path(record['root'])
 targets={'script':safe_file(root,'profiles/'+record['script_file']),
          'plan':LOCAL/'plan.json','sources':LOCAL/'sources.json'}
 if record.get('chain_file'):
  targets['chain']=chain_profile.profile_file(root,record['chain_file'])
  targets['chain_registry']=LOCAL/chain_profile.REGISTRY
 if record.get('managed_file'):
  targets['managed_source']=chain_profile.profile_file(root,record['managed_file'])
  targets['managed_registry']=LOCAL/managed_chain.REGISTRY
 for key,filename in record.get('companion_files',{}).items():
  require(bool(re.fullmatch(r'companion-[0-9]+-(?:script|source)',key)),'Invalid companion update target')
  targets[key]=chain_profile.profile_file(root,filename)
 if record.get('companion_registry'):targets['managed_registry']=LOCAL/managed_chain.REGISTRY
 if record['active']:targets['runtime']=root/'clash-verge.yaml'
 return root,targets

def recover_pending():
 pending=LOCAL/'rule-update-pending.json'
 if not pending.exists():return False
 reference=json.loads(pending.read_text(encoding='utf-8'))
 token=reference.get('id','');require(bool(re.fullmatch(r'[a-f0-9]{32}',token)),'Invalid rule update recovery record')
 folder=LOCAL/'rule-updates'/token;record=load(folder/'transaction.json')
 root,targets=transaction_targets(record)
 require(load(root/'profiles.yaml').get('current')==record['current_uid'],'Rule update recovery needs review')
 # A crash must not allow a later user edit to be silently overwritten.
 for name,path in targets.items():
  entry=record['files'][name];current=path.read_bytes() if path.exists() else None
  require(digest(current) in (entry['before'],entry['after']),'Rule update recovery needs review')
  backup=(folder/(name+'.before')).read_bytes() if entry['before'] is not None else None
  require(digest(backup)==entry['before'],'Invalid rule update recovery backup')
 errors=[]
 for name,path in targets.items():
  try:
   if record['files'][name]['before'] is None:path.unlink(missing_ok=True)
   else:atomic_bytes(path,(folder/(name+'.before')).read_bytes())
  except Exception as exc:errors.append(type(exc).__name__)
 if record['active']:
  try:api(root,'PUT','/configs?force=true',{'payload':(folder/'runtime.before').read_text(encoding='utf-8-sig')})
  except Exception as exc:errors.append(type(exc).__name__)
 record['state']='rollback-incomplete' if errors else 'rolled-back';write(folder/'transaction.json',record)
 require(not errors,'Rule update rollback incomplete')
 pending.unlink();return True


def companion_contexts(root,profiles,plan,fresh,migrate_source):
 """Keep other project-owned normal/chain profiles on the same reviewed rule version."""
 contexts=[];needed=False;registry=managed_chain.records(root,LOCAL)
 policy=json.loads((PROJECT/'assets/claude-policy.json').read_text(encoding='utf-8'))
 for item in profiles['items']:
  if item.get('uid')==plan['subscription_uid'] or item.get('type') not in ('remote','local'):continue
  record=registry.get(item['uid']) if item.get('type')=='local' else None
  if item.get('type')=='local' and (not record or record.get('legacy')):continue
  sid=item.get('option',{}).get('script')
  if not str(sid).startswith('PortableClaude'):continue
  script=next((s for s in profiles['items'] if s.get('uid')==sid and s.get('type')=='script'),None)
  require(script is not None,'Configured companion script missing')
  before=safe_file(root,'profiles/'+script['file']).read_bytes()
  _,_,previous,node=parse_project_wrapper(before.decode('utf-8-sig'))
  for key,value in policy.items():require(value==previous[key],'Reviewed Claude policy changed; explicit reconfiguration required')
  migration=any(previous.get('ruleSources',{}).get(k,{}).get('url')!=url for k,url in SOURCES.items())
  require(not migration or migrate_source,'Rule source migration requires manual update')
  different=False
  for key in KEYS:
   a=set(previous.get('lanSourceCidrs',previous[key]) if key=='lanCidrs' else previous[key]);b=set(fresh[key])
   if key in ('privateDomains','lanCidrs'):require(a==b,'Private/LAN source changed; review before publishing')
   elif not(migration and key=='domains'):require(len(a^b)<=max(100,int(len(a)*.1)),'Large source delta requires manual review')
   different=different or a!=b
  if record:
   require(node==record['node'],'Managed chain script node changed')
   managed_chain.check_source(root,record)
   different=different or record.get('source_fingerprint')!=chain_profile.source_fingerprint(root,profiles,record)
  contexts.append({'item':item,'record':record,'node':node,'script_file':script['file'],'script_before':before})
  needed=needed or different or migration
 return contexts,needed


def build_companions(root,profiles,contexts,fresh):
 from client import preparation_inputs,assert_preparation_inputs
 results=[]
 for context in contexts:
  item=context['item'];record=context['record'];original=None
  inputs=preparation_inputs(root,item,profiles)
  if record:
   original=managed_chain.source(root,profiles,record)
   for name in managed_chain.input_files(record):inputs[name]=digest(safe_file(root,name).read_bytes())
  else:select(root,context['node'],item['uid'],profiles)
  if record:wrapper,text,_,_=build(root,item,context['node'],fresh,original=original)
  else:wrapper,text,_,_=build(root,item,context['node'],fresh)
  assert_preparation_inputs(root,inputs)
  entry={'uid':item['uid'],'node':context['node'],'script_file':context['script_file'],'script':wrapper,
         'runtime':text,'before':{context['script_file']:context['script_before']}}
  if record:
   source_text=yaml_dumps(original,allow_unicode=True,sort_keys=False)
   entry.update(source_file=record['file'],source=source_text,record=managed_chain.refreshed(record,source_text,root,profiles))
   entry['before'][record['file']]=safe_file(root,'profiles/'+record['file']).read_bytes()
  results.append(entry)
 return results

@timed('rules.publish')
def publish(plan,profiles,wrapper,text,metadata,chain=None,managed=None,companions=None):
 companions=companions or []
 root=root_path(plan['root']);active=profiles['current']==plan['subscription_uid'] or bool(chain and chain['active']) or any(c['uid']==profiles['current'] for c in companions)
 new_plan=dict(plan,source_digest=source_digest())
 if managed:new_plan['chain']=managed['record']
 values={'script':wrapper.encode('utf-8'),
         'plan':json.dumps(new_plan,ensure_ascii=False,indent=2).encode('utf-8'),
         'sources':json.dumps(metadata,ensure_ascii=False,indent=2).encode('utf-8')}
 if chain:
  values['chain']=chain['text'].encode('utf-8')
  values['chain_registry']=json.dumps(chain['record'],ensure_ascii=False,indent=2).encode('utf-8')
 if managed:
  values['managed_source']=managed['text'].encode('utf-8')
 if managed or any(c.get('record') for c in companions):
  registry=managed_chain.records(root,LOCAL)
  if managed:registry[plan['subscription_uid']]=managed['record']
  for companion in companions:
   if companion.get('record'):registry[companion['uid']]=companion['record']
  values['managed_registry']=json.dumps(registry,ensure_ascii=False,indent=2).encode('utf-8')
 runtime_text=chain['runtime_text'] if chain and chain['active'] else text
 runtime_plan=new_plan
 companion_files={};expected={}
 for index,companion in enumerate(companions):
  for field,filename in [('script',companion['script_file']),('source',companion.get('source_file'))]:
   if not filename:continue
   key='companion-'+str(index)+'-'+field
   companion_files[key]=filename;values[key]=companion[field].encode('utf-8');expected[key]=companion['before'][filename]
  if profiles['current']==companion['uid']:
   runtime_text=companion['runtime'];runtime_plan={'node':companion['node'],'chain':companion.get('record')}
 if active:values['runtime']=runtime_text.encode('utf-8')
 record={'root':str(root),'script_file':plan['script_file'],'active':active,
         'current_uid':profiles['current'],'state':'prepared','files':{}}
 if chain:record['chain_file']=chain['record']['file']
 if managed:record['managed_file']=managed['record']['file']
 if companions:record['companion_files']=companion_files;record['companion_registry']='managed_registry' in values
 _,targets=transaction_targets(record)
 require(len(set(targets.values()))==len(targets),'Shared project script associations require review')
 token=uuid.uuid4().hex;folder=LOCAL/'rule-updates'/token;folder.mkdir(parents=True)
 for name,path in targets.items():
  before=path.read_bytes() if path.exists() else None
  if chain and name in ('chain','chain_registry'):
   require(before==chain['before' if name=='chain' else 'registry_before'],'Client configuration changed during update')
  if managed and name=='managed_source':require(before==managed['before'],'Client configuration changed during update')
  if name in expected:require(before==expected[name],'Client configuration changed during update')
  if before is not None:(folder/(name+'.before')).write_bytes(before)
  record['files'][name]={'before':digest(before),'after':digest(values[name])}
 write(folder/'transaction.json',record)
 write(LOCAL/'rule-update-pending.json',{'id':token})
 try:
  record['state']='publishing';write(folder/'transaction.json',record)
  if active:api(root,'PUT','/configs?force=true',{'payload':values['runtime'].decode('utf-8')})
  for name,path in targets.items():atomic_bytes(path,values[name])
  if active:
   assert_live(api(root,'GET','/configs'),api(root,'GET','/rules'))
   managed_chain.assert_live(runtime_plan,api(root,'GET','/proxies')) if runtime_plan.get('chain') else None
   providers=api(root,'GET','/providers/rules')['providers']
   expected=load(root/'clash-verge.yaml')['rule-providers']
   for name in ('strict-cn-domain','strict-cncidr','strict-private-domain','strict-lancidr'):
    require(providers.get(name,{}).get('ruleCount')==len(expected[name]['payload']),'Loaded rule provider count mismatch')
  record['state']='committed';write(folder/'transaction.json',record)
  (LOCAL/'rule-update-pending.json').unlink()
 except Exception:
  recover_pending();raise
 return active

@timed('rules.update')
def update(migrate_source=False):
 recovered=recover_pending()
 plan=json.loads((LOCAL/'plan.json').read_text(encoding='utf-8'));root=root_path(plan['root'])
 profiles=load(root/'profiles.yaml')
 selected=next((x for x in profiles.get('items',[]) if x.get('uid')==plan['subscription_uid']),None)
 if selected is None or selected.get('option',{}).get('script')!=plan['script_uid']:
  return {'success':True,'skipped':True,'reason':'订阅已移除或脚本关联已改变；请重新配置。'}
 entry=next((x for x in profiles.get('items',[]) if x.get('uid')==plan['script_uid']),None)
 if entry is None or entry.get('file')!=plan['script_file']:
  return {'success':True,'skipped':True,'reason':'配置脚本已移除或关联文件已改变；请重新配置。'}
 script=safe_file(root,'profiles/'+plan['script_file']);old=script.read_text(encoding='utf-8')
 old_runtime=(root/'clash-verge.yaml').read_bytes()
 _,_,previous,wrapped_node=parse_project_wrapper(old)
 require(wrapped_node==plan['node'],'Project script node changed; reconfiguration required')
 old_sources=previous.get('ruleSources',{})
 migration=any(old_sources.get(key,{}).get('url')!=url for key,url in SOURCES.items())
 require(not migration or migrate_source,'Rule source migration requires manual update')
 fresh=download_rules();metadata=fresh['ruleSources']
 changes={}
 for key in KEYS:
  before=set(previous.get('lanSourceCidrs',previous[key]) if key=='lanCidrs' else previous[key]);after=set(fresh[key])
  changes[key]={'before':len(before),'after':len(after),'added':len(after-before),'removed':len(before-after)}
  if key in ('privateDomains','lanCidrs'):
   require(before==after,'Private/LAN source changed; review before publishing')
  elif not (migration and key=='domains'):
   require(len(after^before)<=max(100,int(len(before)*.1)),'Large source delta requires manual review')
 for value in set(fresh['ips'])-set(previous['ips']):
  require(ipaddress.ip_network(value).prefixlen>=16,'New broad direct CIDR requires manual review')
 policy=json.loads((PROJECT/'assets/claude-policy.json').read_text(encoding='utf-8'))
 for key,value in policy.items():require(value==previous[key],'Reviewed Claude policy changed; explicit reconfiguration required')
 result={'success':True,'source':'Loyalsoldier/clash-rules','sources':metadata,'changes':changes,
         'migrated':migration,'recovered_previous_update':recovered,'claude_automatic_merge':False}
 unchanged=not any(v['added'] or v['removed'] for v in changes.values())
 managed=None;record=None
 if plan.get('chain'):
  record=managed_chain.registered(root,profiles,plan['subscription_uid'],LOCAL)
  require(record is not None,'Managed chain registration missing')
  managed_chain.check_source(root,record)
 refresh=(record.get('source_fingerprint')!=chain_profile.source_fingerprint(root,profiles,record)) if record else chain_profile.refresh_required(LOCAL,root,profiles,plan)
 contexts,companions_changed=companion_contexts(root,profiles,plan,fresh,migrate_source)
 if unchanged and not migration and plan.get('source_digest')==source_digest() and not refresh and not companions_changed:
  return dict(result,unchanged=True,active_profile_reloaded=False)
 item=select(root,plan['node'],plan['subscription_uid'])
 if record:
  original=managed_chain.source(root,profiles,record)
  wrapper,text,candidate,_=build(root,item,plan['node'],fresh,original=original)
  source_text=yaml_dumps(original,allow_unicode=True,sort_keys=False)
  managed={'record':managed_chain.refreshed(record,source_text,root,profiles),'text':source_text,
           'before':safe_file(root,'profiles/'+record['file']).read_bytes()}
 else:wrapper,text,candidate,_=build(root,item,plan['node'],fresh)
 chain=None if record else chain_profile.prepare_update(LOCAL,root,profiles,plan,candidate)
 if chain:
  from client import actual_core
  chain_profile.validate_native(chain['config'],actual_core(),root,LOCAL/'chain-validation')
 companions=build_companions(root,profiles,contexts,fresh)
 # Native client edits are outside our operation lock. Refuse stale publication.
 require(load(root/'profiles.yaml')==profiles and script.read_text(encoding='utf-8')==old and (root/'clash-verge.yaml').read_bytes()==old_runtime,'Client configuration changed during update')
 active=publish(plan,profiles,wrapper,text,metadata,chain,managed,companions)
 return dict(result,unchanged=False,active_profile_reloaded=active,chain_profile_updated=bool(chain or managed or any(c.get('record') for c in companions)))

def failure_result(exc):
 import traceback
 (LOCAL/'update-error.log').write_text(traceback.format_exc(),encoding='utf-8')
 reasons={
  'Private/LAN source changed; review before publishing':'私有域名或局域网规则发生变化，需要审核；未发布新规则。',
  'Large source delta requires manual review':'国内规则变化超过自动更新阈值，需要人工审核。',
  'New broad direct CIDR requires manual review':'新增国内直连 IP 网段范围过大，需要人工审核。',
  'Reviewed Claude policy changed; explicit reconfiguration required':'项目策略已变化，需要重新配置后才能更新规则。',
  'Rule source migration requires manual update':'需要首次切换到 Loyalsoldier，请在规则更新页点击立即更新；定时任务不会自动切换来源。',
  'Overbroad public suffix requires review':'上游新增了未经审核的顶级域直连规则，已停止更新。',
  'Client configuration changed during update':'下载期间 Clash 配置发生变化，请重试。',
  'Chain profile changed; review required':'链式配置已被手动修改，已停止更新；请先检查改动。',
  'Chain profile enhancement changed; review required':'链式配置的增强脚本或覆写已改变，已停止更新。',
  'Chain rule source changed; review required':'链式配置关联的原规则配置已改变，需要检查后再更新。',
  'Exact chain source node missing or duplicated':'链式代理的指定源节点缺失或重名，已停止更新，不自动替换。',
  'Loaded rule provider count mismatch':'内核加载的规则数量不符，已尝试恢复更新前配置。',
 }
 pending=(LOCAL/'rule-update-pending.json').exists()
 reason='上次更新恢复未完成，请查看本地日志并恢复配置后重试。' if pending else reasons.get(str(exc),'规则下载、校验或发布未完成，请查看本地错误记录。')
 return {'success':False,'error':type(exc).__name__,'reason':reason,'recovery_required':pending,
         'note':'详细记录保存在 .local/update-error.log。'}

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--migrate-source',action='store_true');args=parser.parse_args()
 os.environ['CCN_OPERATION_ID']=uuid.uuid4().hex;os.environ['CCN_OPERATION_ACTION']='update'
 try:
  with operation_lock(LOCAL):result=update(migrate_source=args.migrate_source)
 except Exception as exc:result=failure_result(exc)
 result['time']=datetime.datetime.now().astimezone().isoformat();write(LOCAL/'update-result.json',result)
 print(json.dumps(result,ensure_ascii=False))
 if not result['success']:raise SystemExit(1)
