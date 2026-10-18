"""Refresh only approved domestic/private sources; never auto-union Claude lists."""
import datetime,json,re,sys,urllib.request,hashlib,ipaddress
from client import LOCAL,PROJECT,root_path,load,select,build,download_rules,api,safe_file,write

def update():
 plan=json.loads((LOCAL/'plan.json').read_text());root=root_path(plan['root'])
 script=safe_file(root,'profiles/'+plan['script_file']);old=script.read_text(encoding='utf-8')
 match=re.search(r'const data=(.*?); main=function',old,re.S)
 assert match,'Installed project wrapper missing; manual review required'
 previous=json.loads(match.group(1));fresh=download_rules()
 for key in ('privateDomains','lanCidrs'):
  assert set(fresh[key])==set(previous[key]),'Private/LAN source changed; review before publishing'
 for key in ('domains','ips'):
  assert len(set(fresh[key])^set(previous[key]))<=max(100,int(len(previous[key])*.1)),'Large source delta requires manual review'
 for value in set(fresh['ips'])-set(previous['ips']):
  assert ipaddress.ip_network(value).prefixlen>=16,'New broad direct CIDR requires manual review'
 policy=json.loads((PROJECT/'assets/claude-policy.json').read_text(encoding='utf-8'))
 for key,value in policy.items():assert value==previous[key],'Reviewed Claude policy changed; explicit reconfiguration required'
 item=select(root,plan['node'],plan['subscription_uid'])
 wrapper,text,_,_=build(root,item,plan['node'],fresh)
 active=load(root/'profiles.yaml')['current']==plan['subscription_uid']
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
   assert len(raw)<1_000_001
   target=LOCAL/('claude-candidate-'+name+'.yaml');target.write_bytes(raw)
   review[name]={'sha256':hashlib.sha256(raw).hexdigest(),'automatic_merge':False,'review_required':True}
  except Exception as exc:review[name]={'downloaded':False,'error':type(exc).__name__}
 write(LOCAL/'upstream-review.json',review)
 return {'success':True,'active_profile_reloaded':active,'claude_automatic_merge':False}
if __name__=='__main__':
 import msvcrt
 lock=open(LOCAL/'update.lock','a+b');lock.write(b'0');lock.flush();lock.seek(0)
 try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1);result=update()
 except Exception as exc:result={'success':False,'error':str(exc),'note':'Review .local reports; invalid source changes are not published.'}
 finally:lock.close()
 result['time']=datetime.datetime.now().astimezone().isoformat();write(LOCAL/'update-result.json',result)
 print(json.dumps(result,ensure_ascii=False))
 if not result['success']:raise SystemExit(1)
