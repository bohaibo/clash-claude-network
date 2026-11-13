"""Transactional updates of fixed Loyalsoldier sources. No service probes."""
from checks import require
import argparse,datetime,hashlib,json,ipaddress,re,sys,uuid
from client import LOCAL,PROJECT,SOURCES,root_path,load,select,build,download_rules,api,safe_file,write,parse_project_wrapper
from atomic_files import replace_file
from config_health import source_digest,assert_live
from operation_lock import operation_lock

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

def publish(plan,profiles,wrapper,text,metadata):
 root=root_path(plan['root']);active=profiles['current']==plan['subscription_uid']
 new_plan=dict(plan,source_digest=source_digest())
 values={'script':wrapper.encode('utf-8'),
         'plan':json.dumps(new_plan,ensure_ascii=False,indent=2).encode('utf-8'),
         'sources':json.dumps(metadata,ensure_ascii=False,indent=2).encode('utf-8')}
 if active:values['runtime']=text.encode('utf-8')
 record={'root':str(root),'script_file':plan['script_file'],'active':active,
         'current_uid':profiles['current'],'state':'prepared','files':{}}
 _,targets=transaction_targets(record)
 token=uuid.uuid4().hex;folder=LOCAL/'rule-updates'/token;folder.mkdir(parents=True)
 for name,path in targets.items():
  before=path.read_bytes() if path.exists() else None
  if before is not None:(folder/(name+'.before')).write_bytes(before)
  record['files'][name]={'before':digest(before),'after':digest(values[name])}
 write(folder/'transaction.json',record)
 write(LOCAL/'rule-update-pending.json',{'id':token})
 try:
  record['state']='publishing';write(folder/'transaction.json',record)
  if active:api(root,'PUT','/configs?force=true',{'payload':text})
  for name,path in targets.items():atomic_bytes(path,values[name])
  if active:
   assert_live(api(root,'GET','/configs'),api(root,'GET','/rules'))
   providers=api(root,'GET','/providers/rules')['providers']
   expected=load(root/'clash-verge.yaml')['rule-providers']
   for name in ('strict-cn-domain','strict-cncidr','strict-private-domain','strict-lancidr'):
    require(providers.get(name,{}).get('ruleCount')==len(expected[name]['payload']),'Loaded rule provider count mismatch')
  record['state']='committed';write(folder/'transaction.json',record)
  (LOCAL/'rule-update-pending.json').unlink()
 except Exception:
  recover_pending();raise
 return active

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
 if unchanged and not migration and plan.get('source_digest')==source_digest():
  return dict(result,unchanged=True,active_profile_reloaded=False)
 item=select(root,plan['node'],plan['subscription_uid'])
 wrapper,text,_,_=build(root,item,plan['node'],fresh)
 # Native client edits are outside our operation lock. Refuse stale publication.
 require(load(root/'profiles.yaml')==profiles and script.read_text(encoding='utf-8')==old and (root/'clash-verge.yaml').read_bytes()==old_runtime,'Client configuration changed during update')
 active=publish(plan,profiles,wrapper,text,metadata)
 return dict(result,unchanged=False,active_profile_reloaded=active)

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
  'Loaded rule provider count mismatch':'内核加载的规则数量不符，已尝试恢复更新前配置。',
 }
 pending=(LOCAL/'rule-update-pending.json').exists()
 reason='上次更新恢复未完成，请查看本地日志并恢复配置后重试。' if pending else reasons.get(str(exc),'规则下载、校验或发布未完成，请查看本地错误记录。')
 return {'success':False,'error':type(exc).__name__,'reason':reason,'recovery_required':pending,
         'note':'详细记录保存在 .local/update-error.log。'}

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--migrate-source',action='store_true');args=parser.parse_args()
 try:
  with operation_lock(LOCAL):result=update(migrate_source=args.migrate_source)
 except Exception as exc:result=failure_result(exc)
 result['time']=datetime.datetime.now().astimezone().isoformat();write(LOCAL/'update-result.json',result)
 print(json.dumps(result,ensure_ascii=False))
 if not result['success']:raise SystemExit(1)
