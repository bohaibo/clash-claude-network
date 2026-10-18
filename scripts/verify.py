"""Network verification only: never logs in or invokes a Claude conversation."""
from pathlib import Path
import argparse,datetime,ipaddress,json,os,shutil,socket,struct,subprocess,sys,time
from client import PROJECT,LOCAL,api,root_path,GROUP,load

FAKE=ipaddress.ip_network('198.18.0.0/16')

def clean_env():
 return {k:v for k,v in os.environ.items() if k.lower() not in ('http_proxy','https_proxy','all_proxy','no_proxy')}

def skip_name(data,offset):
 while True:
  length=data[offset]
  if length==0:return offset+1
  if length&0xC0==0xC0:return offset+2
  offset+=1+length

def answer_ips(data):
 ips=[]
 try:
  _,_,qd,an,_,_=struct.unpack('!HHHHHH',data[:12]);offset=12
  for _ in range(qd):offset=skip_name(data,offset)+4
  for _ in range(an):
   offset=skip_name(data,offset)
   rtype,_,_,rdlen=struct.unpack('!HHIH',data[offset:offset+10]);offset+=10
   rdata=data[offset:offset+rdlen];offset+=rdlen
   if rtype==1 and rdlen==4:ips.append('.'.join(str(b) for b in rdata))
 except Exception:pass
 return ips

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
 return {'transport':'TCP' if tcp else 'UDP','qtype':kind,'host':host,'rcode':rcode,'answer_count':answers,
         'ips':answer_ips(data) if kind==1 else [],
         'passed':rcode==0 and (answers==0 if kind in (64,65) else answers>0)}
def curl(url,head=False):
 cmd=['curl.exe','--noproxy','*','--connect-timeout','4','--max-time','10','-sS','-w','\n__CHECK__%{http_code}']
 if head:cmd+=['-I']
 p=subprocess.run(cmd+[url],capture_output=True,text=True,encoding='utf-8',errors='replace',env=clean_env(),timeout=13)
 body,_,status=p.stdout.rpartition('\n__CHECK__')
 return {'exit':p.returncode,'http':int(status or 0),'body':body}
def stun(host='stun.l.google.com',port=19302):
 """Native UDP STUN binding request, bypassing any HTTP proxy."""
 transaction=os.urandom(12);request=struct.pack('!HHI',0x0001,0,len(transaction))+transaction
 try:
  with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
   sock.settimeout(5);sock.sendto(request,(socket.gethostbyname(host),port));data,_=sock.recvfrom(2048)
 except OSError as exc:
  return {'ok':False,'error':type(exc).__name__}
 if len(data)<20 or data[:2]!=b'\x01\x01':return {'ok':False,'error':'unexpected STUN response'}
 offset=20;mapped=None
 while offset+4<=len(data):
  atype,alen=struct.unpack('!HH',data[offset:offset+4]);value=data[offset+4:offset+4+alen]
  if atype in (0x0001,0x0020) and len(value)>=8 and value[1]==0x01:
   address=value[4:8];port_value=struct.unpack('!H',value[2:4])[0]
   if atype==0x0020:
    port_value^=0x2112;address=bytes([address[0]^0x21,address[1]^0x12,address[2]^0xA4,address[3]^0x42])
   mapped='%d.%d.%d.%d:%d'%(*address,port_value)
  offset+=4+alen+((4-alen%4)%4)
 return {'ok':True,'mapped':mapped}
def git_egress():
 if not shutil.which('git'):return {'tested':False,'reason':'git not installed'}
 p=subprocess.run(['git','-c','http.proxy=','-c','https.proxy=','ls-remote','https://github.com/git/git','HEAD'],
                  capture_output=True,text=True,env=clean_env(),timeout=30)
 return {'tested':True,'exit':p.returncode,'ok':p.returncode==0}
def npm_egress():
 npm=shutil.which('npm') or shutil.which('npm.cmd')
 if not npm:return {'tested':False,'reason':'npm not installed'}
 p=subprocess.run([npm,'ping','--registry','https://registry.npmjs.org'],capture_output=True,text=True,env=clean_env(),timeout=35)
 return {'tested':True,'exit':p.returncode,'ok':p.returncode==0}
def workload_scope():
 return {'docker_installed':bool(shutil.which('docker')),'wsl_installed':bool(shutil.which('wsl')),
         'note':'Container and WSL egress must be measured inside each workload; host curl is not proof.'}
def verify(outage=False):
 plan=json.loads((LOCAL/'plan.json').read_text(encoding='utf-8'));root=root_path(plan['root']);node=plan['node']
 report={'success':False,'account_tested':False,'application_usability_verified':False,
         'not_tested':['browser_webrtc','browser_fingerprint']}
 try:
  group=api(root,'GET','/proxies')['proxies'][GROUP];assert group['all']==[node] and group['now']==node
  cn=curl('https://www.baidu.com',True);assert cn['exit']==0 and cn['http']==200
  taobao=curl('https://www.taobao.com',True);assert taobao['exit']==0 and taobao['http'] in (200,301,302)
  report['domestic']={'baidu':cn['http'],'taobao':taobao['http']}
  egress=curl('https://ipinfo.io/json');assert egress['exit']==0
  address=json.loads(egress['body']);assert address.get('country')==plan['region'],'Exit country differs from required region'
  report['egress']={k:address.get(k) for k in ('ip','country','city')}
  claude=curl('https://claude.ai',True);report['claude_http']=claude['http'];assert claude['exit']==0 and claude['http']>0
  report['dns']=[query('8.8.8.8',53,t,tcp) for t in (1,64,65) for tcp in (False,True)]
  assert all(x['passed'] for x in report['dns'])
  fake=query('8.8.8.8',53,1,False,host='claude.ai')
  assert any(ipaddress.ip_address(ip) in FAKE for ip in fake['ips']),'fake-IP not observed; TUN DNS hijack may be bypassed'
  report['fake_ip_hijack']=fake['ips']
  assert curl('https://[2606:4700:4700::1111]/cdn-cgi/trace')['exit']!=0
  report['ipv6_public_blocked']=True
  report['stun']=stun()
  report['git']=git_egress()
  report['npm']=npm_egress()
  scope=workload_scope();report['workload_scope']=scope
  if scope['docker_installed']:report['not_tested'].append('docker_container_egress')
  if scope['wsl_installed']:report['not_tested'].append('wsl_egress')
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
