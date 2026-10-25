"""Network verification only: never logs in or invokes a Claude conversation."""
from checks import require
from pathlib import Path
import argparse,contextlib,datetime,ipaddress,json,os,shutil,socket,struct,subprocess,sys,time,uuid
from client import PROJECT,LOCAL,api,root_path,GROUP,load
from verification_reports import save_report

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
 require(data[:2]==token,'Mismatched DNS response')
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
def stun(host='stun.l.google.com',port=19302,route_probe=None):
 """Native UDP STUN binding request, bypassing any HTTP proxy."""
 transaction=os.urandom(12);cookie=0x2112A442
 request=struct.pack('!HHI',0x0001,0,cookie)+transaction
 try:
  with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
   target=(socket.gethostbyname(host),port)
   sock.settimeout(5);sock.sendto(request,target);data,sender=sock.recvfrom(2048)
   route=None
   if route_probe:
    try:route=route_probe({'host':host,'destination_ip':target[0],'destination_port':port,'source_port':sock.getsockname()[1]})
    except Exception as exc:route={'verified':False,'error':type(exc).__name__}
 except OSError as exc:
  return {'ok':False,'error':type(exc).__name__}
 if len(data)<20 or sender!=target:return {'ok':False,'error':'unexpected STUN response'}
 message,length,response_cookie=struct.unpack('!HHI',data[:8])
 if message!=0x0101 or response_cookie!=cookie or data[8:20]!=transaction or length%4 or len(data)!=20+length:
  return {'ok':False,'error':'invalid STUN header or transaction'}
 offset=20;mapped=None
 while offset+4<=len(data):
  atype,alen=struct.unpack('!HH',data[offset:offset+4]);end=offset+4+alen;next_offset=end+((4-alen%4)%4)
  if end>len(data) or next_offset>len(data):return {'ok':False,'error':'truncated STUN attribute'}
  value=data[offset+4:end]
  if atype in (0x0001,0x0020) and len(value)==8 and value[0]==0 and value[1]==0x01:
   address=value[4:8];port_value=struct.unpack('!H',value[2:4])[0]
   if atype==0x0020:
    port_value^=0x2112;address=bytes([address[0]^0x21,address[1]^0x12,address[2]^0xA4,address[3]^0x42])
   if port_value and ipaddress.ip_address(address).is_global:
    mapped='%d.%d.%d.%d:%d'%(*address,port_value)
  offset=next_offset
 if offset!=len(data) or mapped is None:return {'ok':False,'error':'missing valid public STUN mapping'}
 result={'ok':True,'mapped':mapped}
 if route_probe:result['route']=route
 return result

def udp_route(root,node,probe):
 matches=[]
 for connection in api(root,'GET','/connections').get('connections',[]):
  metadata=connection.get('metadata',{})
  if (str(metadata.get('network','')).lower()=='udp' and
      str(metadata.get('sourcePort'))==str(probe['source_port']) and
      str(metadata.get('destinationPort'))==str(probe['destination_port']) and
      (metadata.get('host')==probe['host'] or metadata.get('destinationIP')==probe['destination_ip'])):
   matches.append(connection)
 chains=matches[0].get('chains',[]) if len(matches)==1 else []
 return {'verified':len(matches)==1 and node in chains and GROUP in chains and 'DIRECT' not in chains,
         'matched_connections':len(matches),'selected_node_present':node in chains,
         'pinned_group_present':GROUP in chains,'direct_present':'DIRECT' in chains}

def udp_egress(result,tcp,region):
 report={'verified':False,'route_verified':result.get('route',{}).get('verified') is True}
 try:
  address=str(ipaddress.IPv4Address(result['mapped'].rsplit(':',1)[0]))
  require(ipaddress.ip_address(address).is_global,'Non-public UDP mapping')
  report['ip']=address;report['same_as_tcp']=address==tcp.get('ip')
  if report['same_as_tcp']:country=tcp.get('country')
  else:
   lookup=curl('https://ipinfo.io/'+address+'/json')
   require(lookup['exit']==0 and lookup['http']==200,'UDP country lookup failed')
   geo=json.loads(lookup['body'])
   require(geo.get('ip')==address,'UDP lookup returned a different address')
   country=geo.get('country')
  report['country']=country;report['country_matches']=country==region
  report['verified']=report['route_verified'] and report['country_matches']
 except (AssertionError,ValueError,KeyError,OSError,subprocess.SubprocessError) as exc:
  report['error']=type(exc).__name__
 return report
def run_tool_attempt(command,timeout,env,log_dir=None):
 # Do not capture pipes: on Windows a Git helper can keep inherited pipes open
 # even after subprocess.run kills the parent, defeating its timeout.
 with contextlib.ExitStack() as files:
  output=errors=subprocess.DEVNULL
  if log_dir is not None:
   log_dir.mkdir(parents=True,exist_ok=True)
   output=files.enter_context((log_dir/'stdout.log').open('wb'))
   errors=files.enter_context((log_dir/'stderr.log').open('wb'))
  process=subprocess.Popen(command,stdout=output,stderr=errors,env=env)
  try:
   code=process.wait(timeout=timeout)
   return {'exit':code,'ok':code==0}
  except subprocess.TimeoutExpired:
   tree_stopped=False
   if os.name=='nt':
    try:
     cleanup=subprocess.run(['taskkill.exe','/PID',str(process.pid),'/T','/F'],
                            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=5)
     tree_stopped=cleanup.returncode==0
    except (OSError,subprocess.TimeoutExpired):pass
   try:process.kill()
   except OSError:pass
   try:process.wait(timeout=3)
   except subprocess.TimeoutExpired:pass
   return {'ok':False,'error':'timeout','timeout_seconds':timeout,'process_tree_stopped':tree_stopped}

def tool_egress(command,timeout,env,attempts=2):
 history=[]
 folder=LOCAL/'tool-probes'/uuid.uuid4().hex
 for _ in range(attempts):
  started=time.monotonic()
  logs=folder/str(len(history)+1)
  try:
   attempt=run_tool_attempt(command,timeout,env,logs)
  except OSError:
   attempt={'ok':False,'error':'process-start-failed'}
  attempt['elapsed_seconds']=round(time.monotonic()-started,2)
  attempt['logs']=str(logs.relative_to(LOCAL));history.append(attempt)
  if (attempt['ok'] or attempt.get('error')=='process-start-failed'
      or (attempt.get('error')=='timeout' and not attempt.get('process_tree_stopped'))):break
 return {'tested':True,**history[-1],'attempts':history}

def git_egress():
 if not shutil.which('git'):return {'tested':False,'reason':'git not installed'}
 env=clean_env();env.update(GIT_TERMINAL_PROMPT='0',GCM_INTERACTIVE='Never',GIT_ASKPASS='',GIT_TRACE='1',GIT_CURL_VERBOSE='1')
 return tool_egress(['git','-c','http.proxy=','-c','https.proxy=','-c','credential.helper=',
                     '-c','core.askPass=','-c','http.lowSpeedLimit=1','-c','http.lowSpeedTime=10',
                     'ls-remote','https://github.com/git/git','HEAD'],20,env)
def npm_egress():
 npm=shutil.which('npm') or shutil.which('npm.cmd')
 if not npm:return {'tested':False,'reason':'npm not installed'}
 return tool_egress([npm,'ping','--registry','https://registry.npmjs.org',
                     '--fetch-retries=0','--fetch-timeout=12000','--update-notifier=false'],20,clean_env())
def workload_scope():
 return {'docker_installed':bool(shutil.which('docker')),'wsl_installed':bool(shutil.which('wsl')),
         'note':'Container and WSL egress must be measured inside each workload; host curl is not proof.'}
def verify(outage=False,stage=None):
 stage=stage or ('node-outage' if outage else 'connectivity')
 plan=json.loads((LOCAL/'plan.json').read_text(encoding='utf-8'));root=root_path(plan['root']);node=plan['node']
 report={'success':False,'account_tested':False,'application_usability_verified':False,
         'not_tested':['browser_webrtc','browser_fingerprint','claude_http']}
 try:
  runtime=api(root,'GET','/configs')
  require(runtime.get('mode')=='rule','Live core is not in rule mode; network probes stopped')
  report['runtime_mode']='rule'
  group=api(root,'GET','/proxies')['proxies'][GROUP];require(group['all']==[node] and group['now']==node,'配置检查未通过，请查看本地记录。')
  cn=curl('https://www.baidu.com',True);require(cn['exit']==0 and cn['http']==200,'配置检查未通过，请查看本地记录。')
  taobao=curl('https://www.taobao.com',True);require(taobao['exit']==0 and taobao['http'] in (200,301,302),'配置检查未通过，请查看本地记录。')
  report['domestic']={'baidu':cn['http'],'taobao':taobao['http']}
  egress=curl('https://ipinfo.io/json');require(egress['exit']==0,'配置检查未通过，请查看本地记录。')
  address=json.loads(egress['body']);require(address.get('country')==plan['region'],'Exit country differs from required region')
  report['egress']={k:address.get(k) for k in ('ip','country','city')}
  report['dns']=[query('8.8.8.8',53,t,tcp) for t in (1,64,65) for tcp in (False,True)]
  require(all(x['passed'] for x in report['dns']),'配置检查未通过，请查看本地记录。')
  fake=query('8.8.8.8',53,1,False,host='claude.ai')
  require(any(ipaddress.ip_address(ip) in FAKE for ip in fake['ips']),'fake-IP not observed; TUN DNS hijack may be bypassed')
  report['fake_ip_hijack']=fake['ips']
  require(curl('https://[2606:4700:4700::1111]/cdn-cgi/trace')['exit']!=0,'配置检查未通过，请查看本地记录。')
  report['ipv6_public_blocked']=True
  report['stun']=stun(route_probe=lambda probe:udp_route(root,node,probe))
  report['git']=git_egress()
  report['npm']=npm_egress()
  failed=[]
  for name in ('stun','git','npm'):
   value=report[name]
   if value.get('tested',True) is False:
    report['not_tested'].append(name);value['status']='not-tested'
   else:
    value['status']='passed' if value.get('ok') is True else 'failed'
    if value['status']=='failed':failed.append(name)
  require(not failed,'Required egress checks failed: '+', '.join(failed))
  report['udp_egress']=udp_egress(report['stun'],report['egress'],plan['region'])
  require(report['udp_egress']['verified'],'UDP exit country or pinned route could not be verified')
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
    require(curl('https://ipinfo.io/json')['exit']!=0,'Foreign traffic remained reachable during node outage')
    require(curl('https://www.baidu.com',True)['http']==200,'配置检查未通过，请查看本地记录。')
    report['node_failure_blocks_foreign_preserves_domestic']=True
   finally:api(root,'PUT','/configs?force=true',{'payload':current})
   recovered=curl('https://ipinfo.io/json');require(recovered['exit']==0 and json.loads(recovered['body']).get('country')==plan['region'],'配置检查未通过，请查看本地记录。')
   report['recovered']=True
  report['success']=True
 except Exception as exc:report['error']=str(exc);raise
 finally:
  report=save_report(report,stage)
  print(json.dumps(report,ensure_ascii=False))
 return report
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--node-outage',action='store_true')
 p.add_argument('--stage',choices=('connectivity','node-outage','post-recovery'));args=p.parse_args()
 if args.stage and (args.stage=='node-outage')!=args.node_outage:p.error('Stage must match --node-outage')
 verify(args.node_outage,args.stage)
