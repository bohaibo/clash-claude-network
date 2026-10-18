"""Network verification only: never logs in or invokes a Claude conversation."""
from pathlib import Path
import argparse,datetime,ipaddress,json,os,socket,struct,subprocess,sys,time
from client import PROJECT,LOCAL,api,root_path,GROUP,load

def query(server,port,kind,tcp=False,source=None,host='cloudflare.com'):
 token=os.urandom(2);packet=token+struct.pack('!HHHHH',0x100,1,0,0,0)+b''.join(bytes([len(s)])+s.encode() for s in host.split('.'))+b'\0'+struct.pack('!HH',kind,1)
 with socket.socket(socket.AF_INET,socket.SOCK_STREAM if tcp else socket.SOCK_DGRAM) as sock:
  sock.settimeout(5)
  if source:sock.bind((source,0))
  def receive(n):
   result=b''
   while len(result)<n:
    part=sock.recv(n-len(result))
    if not part:raise RuntimeError('Truncated DNS response')
    result+=part
   return result
  if tcp:
   sock.connect((server,port));sock.sendall(struct.pack('!H',len(packet))+packet);data=receive(struct.unpack('!H',receive(2))[0])
  else:sock.sendto(packet,(server,port));data=sock.recv(4096)
 assert data[:2]==token,'Mismatched DNS response'
 _,flags,_,answers,_,_=struct.unpack('!HHHHHH',data[:12]);rcode=flags&15
 return {'transport':'TCP' if tcp else 'UDP','qtype':kind,'rcode':rcode,'answer_count':answers,
         'passed':rcode==0 and (answers==0 if kind in (64,65) else answers>0)}
def curl(url,head=False):
 env={k:v for k,v in os.environ.items() if k.lower() not in ('http_proxy','https_proxy','all_proxy','no_proxy')}
 cmd=['curl.exe','--noproxy','*','--connect-timeout','4','--max-time','10','-sS','-w','\n__CHECK__%{http_code}']
 if head:cmd+=['-I']
 p=subprocess.run(cmd+[url],capture_output=True,text=True,encoding='utf-8',errors='replace',env=env,timeout=13)
 body,_,status=p.stdout.rpartition('\n__CHECK__')
 return {'exit':p.returncode,'http':int(status or 0),'body':body}
def verify(outage=False):
 plan=json.loads((LOCAL/'plan.json').read_text(encoding='utf-8'));root=root_path(plan['root']);node=plan['node']
 report={'success':False,'account_tested':False,'application_usability_verified':False}
 try:
  group=api(root,'GET','/proxies')['proxies'][GROUP];assert group['all']==[node] and group['now']==node
  cn=curl('https://www.baidu.com',True);assert cn['exit']==0 and cn['http']==200
  report['domestic_http']=cn['http']
  egress=curl('https://ipinfo.io/json');assert egress['exit']==0
  address=json.loads(egress['body']);assert address.get('country')==plan['region'],'Exit country differs from required region'
  report['egress']={k:address.get(k) for k in ('ip','country','city')}
  claude=curl('https://claude.ai',True);report['claude_http']=claude['http'];assert claude['exit']==0 and claude['http']>0
  report['dns']=[query('8.8.8.8',53,t,tcp) for t in (1,64,65) for tcp in (False,True)]
  assert all(x['passed'] for x in report['dns'])
  assert curl('https://[2606:4700:4700::1111]/cdn-cgi/trace')['exit']!=0
  report['ipv6_public_blocked']=True
  if outage:
   current=(root/'clash-verge.yaml').read_text(encoding='utf-8');broken=__import__('yaml').safe_load(current)
   for value in broken['proxies']:
    if value['name']==node:value['port']=1
   try:
    api(root,'PUT','/configs?force=true',{'payload':__import__('yaml').safe_dump(broken,allow_unicode=True,sort_keys=False)})
    for connection in api(root,'GET','/connections').get('connections',[]):
     if node in connection.get('chains',[]):
      try:api(root,'DELETE','/connections/'+connection['id'])
      except Exception:pass
    assert curl('https://claude.ai')['exit']!=0
    assert curl('https://www.baidu.com',True)['http']==200
    report['node_failure_blocks_foreign_preserves_domestic']=True
   finally:api(root,'PUT','/configs?force=true',{'payload':current})
   recovered=curl('https://ipinfo.io/json');assert recovered['exit']==0 and json.loads(recovered['body']).get('country')==plan['region']
   report['recovered']=True
  report['success']=True;return report
 except Exception as exc:report['error']=str(exc);raise
 finally:
  report['time']=datetime.datetime.now().astimezone().isoformat()
  (LOCAL/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
  print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--node-outage',action='store_true');args=p.parse_args();verify(args.node_outage)
