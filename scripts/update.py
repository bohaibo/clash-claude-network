"""Refresh only approved domestic/private sources; never auto-union Claude lists."""
from checks import require
import datetime,json,re,sys,urllib.request,hashlib,ipaddress
from client import LOCAL,PROJECT,root_path,load,select,build,download_rules,api,safe_file,write,parse_project_wrapper
from operation_lock import operation_lock

def update():
 plan=json.loads((LOCAL/'plan.json').read_text(encoding='utf-8'));root=root_path(plan['root'])
 profiles=load(root/'profiles.yaml')
 selected=next((x for x in profiles.get('items',[]) if x.get('uid')==plan['subscription_uid']),None)
 if selected is None or selected.get('option',{}).get('script')!=plan['script_uid']:
  return {'success':True,'skipped':True,'reason':'The project script is no longer associated with this subscription'}
 entry=next((x for x in profiles.get('items',[]) if x.get('uid')==plan['script_uid']),None)
 if entry is None or entry.get('file')!=plan['script_file']:
  return {'success':True,'skipped':True,'reason':'The user changed or removed the script file association'}
 script=safe_file(root,'profiles/'+plan['script_file']);old=script.read_text(encoding='utf-8')
 _,_,previous,_=parse_project_wrapper(old)
 fresh=download_rules()
 for key in ('privateDomains','lanCidrs'):
  reviewed=previous.get('lanSourceCidrs',previous[key]) if key=='lanCidrs' else previous[key]
  require(set(fresh[key])==set(reviewed),'Private/LAN source changed; review before publishing')
 for key in ('domains','ips'):
  require(len(set(fresh[key])^set(previous[key]))<=max(100,int(len(previous[key])*.1)),'Large source delta requires manual review')
 for value in set(fresh['ips'])-set(previous['ips']):
  require(ipaddress.ip_network(value).prefixlen>=16,'New broad direct CIDR requires manual review')
 policy=json.loads((PROJECT/'assets/claude-policy.json').read_text(encoding='utf-8'))
 for key,value in policy.items():require(value==previous[key],'Reviewed Claude policy changed; explicit reconfiguration required')
 item=select(root,plan['node'],plan['subscription_uid'])
 wrapper,text,_,_=build(root,item,plan['node'],fresh)
 active=profiles['current']==plan['subscription_uid']
 runtime=(root/'clash-verge.yaml').read_text(encoding='utf-8')
 try:
  if active:api(root,'PUT','/configs?force=true',{'payload':text})
  script.write_text(wrapper,encoding='utf-8')
  if active:(root/'clash-verge.yaml').write_text(text,encoding='utf-8')
 except Exception:
  script.write_text(old,encoding='utf-8')
  if active:api(root,'PUT','/configs?force=true',{'payload':runtime});(root/'clash-verge.yaml').write_text(runtime,encoding='utf-8')
  raise
 review={}
 for name,url in {'xiaolai':'https://raw.githubusercontent.com/xiaolai/anthropic-claude-surge-rules-set/main/domains.yaml',
                  'vpsdance':'https://raw.githubusercontent.com/VPSDance/ai-proxy-rules/main/rules/clash/anthropic.yaml'}.items():
  try:
   with urllib.request.urlopen(url,timeout=15) as response:raw=response.read(1_000_001)
   require(len(raw)<1_000_001,'配置检查未通过，请查看本地记录。')
   target=LOCAL/('claude-candidate-'+name+'.yaml');target.write_bytes(raw)
   review[name]={'sha256':hashlib.sha256(raw).hexdigest(),'automatic_merge':False,'review_required':True}
  except Exception as exc:review[name]={'downloaded':False,'error':type(exc).__name__}
 write(LOCAL/'upstream-review.json',review)
 return {'success':True,'active_profile_reloaded':active,'claude_automatic_merge':False}
def failure_result(exc):
 # Only fixed messages are safe for the GUI; parser/controller errors can
 # include subscription contents. Keep detailed diagnostics local.
 import traceback
 write_path=LOCAL/'update-error.log'
 write_path.write_text(traceback.format_exc(),encoding='utf-8')
 reasons={
  'Private/LAN source changed; review before publishing':'私有域名或局域网规则来源发生变化，需要审核后才能发布。',
  'Large source delta requires manual review':'国内规则变化超过自动更新阈值，需要人工审核。',
  'New broad direct CIDR requires manual review':'新增的国内直连 IP 网段范围过大，需要人工审核。',
  'Reviewed Claude policy changed; explicit reconfiguration required':'项目策略已变化，需要重新配置后才能更新规则。',
 }
 reason=reasons.get(str(exc))
 if reason is None:
  reason='规则下载、校验或写入未完成，具体错误见 .local/update-error.log。'
 return {'success':False,'error':type(exc).__name__,'reason':reason,
         'note':'请查看 .local/update-error.log；本次更新未完成。'}

if __name__=='__main__':
 try:
  with operation_lock(LOCAL):result=update()
 except Exception as exc:result=failure_result(exc)
 result['time']=datetime.datetime.now().astimezone().isoformat();write(LOCAL/'update-result.json',result)
 print(json.dumps(result,ensure_ascii=False))
 if not result['success']:raise SystemExit(1)
