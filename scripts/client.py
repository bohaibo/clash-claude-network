"""Clash Verge Rev Windows adapter. Secrets and backups stay under .local/."""
from pathlib import Path
import argparse,copy,datetime,hashlib,ipaddress,json,os,re,shutil,socket,subprocess,sys,urllib.request,uuid
import yaml

PROJECT=Path(__file__).resolve().parent.parent
LOCAL=PROJECT/'.local';LOCAL.mkdir(exist_ok=True)
GROUP='Claude-Overseas-Pinned'
SOURCES={'domains':'https://raw.githubusercontent.com/pluwen/china-domain-allowlist/main/allow-list.sorl',
 'ips':'https://raw.githubusercontent.com/Loyalsoldier/clash-rules/release/cncidr.txt',
 'privateDomains':'https://raw.githubusercontent.com/Loyalsoldier/clash-rules/release/private.txt',
 'lanCidrs':'https://raw.githubusercontent.com/Loyalsoldier/clash-rules/release/lancidr.txt'}
FAKE=ipaddress.ip_network('198.18.0.0/15')

def load(path):return yaml.safe_load(path.read_text(encoding='utf-8-sig'))
def write(path,data):
 path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_name(path.name+'.new')
 temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)
def root_path(value=None):
 assert os.name=='nt','Bundled system adapter supports Windows only. Read references/platforms.md for other systems.'
 root=Path(value) if value else Path(os.environ['APPDATA'])/'io.github.clash-verge-rev.clash-verge-rev'
 assert (root/'profiles.yaml').is_file() and (root/'clash-verge.yaml').is_file(),'Clash Verge Rev data directory not found'
 return root.resolve()
def safe_file(root,relative):
 path=(root/relative).resolve();assert path.is_relative_to(root.resolve()),'Path escapes client directory';return path
def executables():
 node=shutil.which('node');assert node,'Install Node.js from its official source first'
 candidates=[Path(os.environ.get('PROGRAMFILES',r'C:\Program Files'))/'Clash Verge/verge-mihomo.exe',
             Path(os.environ.get('PROGRAMDATA',r'C:\ProgramData'))/'clash-verge-service/cores/verge-mihomo.exe']
 core=next((p for p in candidates if p.is_file()),None);assert core,'Mihomo executable not found; agent must inspect the actual installation'
 return node,str(core)
def discover(root):
 profiles=load(root/'profiles.yaml');rows=[]
 for item in profiles['items']:
  if item.get('type')!='remote' or not item.get('file'):continue
  source=safe_file(root,'profiles/'+item['file'])
  if not source.is_file():continue
  value=load(source) or {}
  rows.append({'uid':item['uid'],'name':item.get('name'),'nodes':[n['name'] for n in value.get('proxies',[])]})
 return {'client':'Clash Verge Rev','root':str(root),'active_uid':profiles['current'],'subscriptions':rows}
def select(root,node,subscription=None):
 matches=[r for r in discover(root)['subscriptions'] if node in r['nodes'] and
          (subscription is None or subscription in (r['name'],r['uid']))]
 assert len(matches)==1,'Node missing or ambiguous. Supply an exact subscription name/UID; do not guess.'
 return next(x for x in load(root/'profiles.yaml')['items'] if x['uid']==matches[0]['uid'])
def download_rules():
 data={};metadata={};opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 for name,url in SOURCES.items():
  with opener.open(url,timeout=30) as response:raw=response.read(4_000_001)
  assert len(raw)<=4_000_000,'Oversized rule source'
  if name=='domains':
   values=[]
   for line in raw.decode('utf-8-sig').splitlines():
    v=line.strip()
    if not v or v.startswith((';','[')) or re.fullmatch(r'[0-9.*]+',v):continue
    assert re.fullmatch(r'[a-zA-Z0-9.*_-]+',v),'Unrecognized domain-list syntax'
    v='+.'+v[2:] if v.startswith('*.') else v
    if v not in ('+.douban.*','+.jianshu.*'):
     name=v.removeprefix('+.');assert '*' not in name
     assert '.' in name or name in ('cn','xn--fiqs8s'),'Overbroad public suffix requires review'
     assert all(re.fullmatch(r'[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?',label) for label in name.split('.'))
    values.append(v)
  else:
   values=yaml.safe_load(raw)['payload'];assert isinstance(values,list)
   if name=='lanCidrs':values=[v for v in values if v!='198.18.0.0/15']
   if name in ('ips','lanCidrs'):
    for value in values:
     network=ipaddress.ip_network(value);assert network.prefixlen>0
     if network.version==4:assert not network.overlaps(FAKE),'Direct rule overlaps fake-IP pool'
  data[name]=list(dict.fromkeys(values));metadata[name]={'url':url,'sha256':hashlib.sha256(raw).hexdigest(),'count':len(data[name])}
 assert len(data['domains'])>1000 and len(data['ips'])>8000 and len(data['privateDomains'])>30
 write(LOCAL/'sources.json',metadata);return data
def api(root,method,path,body=None):
 config=load(root/'clash-verge.yaml');pipes=[n for n in os.listdir('//./pipe/') if n.startswith('verge-mihomo-')]
 assert len(pipes)==1,'Cannot identify a unique Mihomo control pipe'
 payload=b'' if body is None else json.dumps(body).encode()
 request=(f'{method} {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\nAuthorization: Bearer {config.get("secret","")}\r\nContent-Type: application/json\r\nContent-Length: {len(payload)}\r\n\r\n').encode()+payload
 with open('\\\\.\\pipe\\'+pipes[0],'r+b',buffering=0) as stream:
  stream.write(request);response=b''
  while True:
   try:part=stream.read(65536)
   except OSError:break
   if not part:break
   response+=part
 header,_,value=response.partition(b'\r\n\r\n')
 assert header.split(b'\r\n')[0].split()[1].startswith(b'2'),'Mihomo API request failed; response withheld to protect credentials'
 if b'chunked' in header.lower():
  decoded=b''
  while value:
   size,_,value=value.partition(b'\r\n');size=int(size,16)
   if not size:break
   decoded+=value[:size];value=value[size+2:]
  value=decoded
 return json.loads(value) if value else None
def build(root,item,node,data=None):
 original=load(safe_file(root,'profiles/'+item['file']));config=copy.deepcopy(original)
 effective=load(root/'clash-verge.yaml')
 for key in ('mixed-port','port','socks-port','allow-lan','log-level','external-controller','external-controller-pipe','secret','find-process-mode','profile'):
  if key in effective:config[key]=copy.deepcopy(effective[key])
 data=copy.deepcopy(data or download_rules());data.update(json.loads((PROJECT/'assets/claude-policy.json').read_text(encoding='utf-8')))
 data['lan']=['IP-CIDR,127.0.0.0/8,DIRECT,no-resolve','IP-CIDR,10.0.0.0/8,DIRECT,no-resolve',
  'IP-CIDR,172.16.0.0/12,DIRECT,no-resolve','IP-CIDR,192.168.0.0/16,DIRECT,no-resolve',
  'IP-CIDR,169.254.0.0/16,DIRECT,no-resolve','IP-CIDR,100.64.0.0/10,DIRECT,no-resolve']
 # Keep original explicit LAN rules; remove only benchmark/fake-IP destination ranges.
 for rule in original.get('rules',[]):
  fields=rule.split(',')
  if len(fields)<3 or fields[2]!='DIRECT':continue
  if fields[0] in ('IP-CIDR','IP-CIDR6'):
   network=ipaddress.ip_network(fields[1],strict=False)
   if network.version==4 and network.overlaps(FAKE):continue
   if network.is_private or network.is_link_local or network.is_loopback:data['lan'].append(rule)
  elif fields[0] in ('DOMAIN','DOMAIN-SUFFIX') and (fields[1] in ('localhost','lan','local','internal') or fields[1].endswith(('.lan','.local','.internal'))):data['lan'].append(rule)
 selected=next(n for n in original.get('proxies',[]) if n['name']==node)
 try:
  address=ipaddress.ip_address(selected['server'])
  if address.version==4:data['lan'].insert(0,f'IP-CIDR,{address}/32,DIRECT,no-resolve')
 except ValueError:pass
 # Preserve the selected subscription's cached classical LAN provider rules, not another subscription's.
 data['lanProviderOverrides']={}
 for rule in original.get('rules',[]):
  fields=rule.split(',')
  if len(fields)<3 or fields[0]!='RULE-SET' or fields[2]!='DIRECT' or not re.search('private|localarea|lancidr|lan$',fields[1],re.I):continue
  name=fields[1];provider=original.get('rule-providers',{}).get(name,{})
  payload=provider.get('payload')
  if payload is None and provider.get('path'):
   cache=safe_file(root,provider['path'])
   if cache.exists():payload=(load(cache) or {}).get('payload')
  assert payload is not None,'Original LAN provider cache missing; inspect it before proceeding'
  assert provider.get('behavior') in ('classical','domain','ipcidr'),'Unsupported LAN provider format'
  cleaned=[]
  for value in payload:
   parts=value.split(',');cidr=parts[1] if parts[0]=='IP-CIDR' else value if provider['behavior']=='ipcidr' and ':' not in value else None
   if cidr:
    network=ipaddress.ip_network(cidr,strict=False)
    if network.overlaps(FAKE):continue
   cleaned.append(value)
  data['lanProviderOverrides'][name]={'type':'inline','behavior':provider['behavior'],'payload':cleaned}
  data['lan'].append(rule)
 data['lan']=list(dict.fromkeys(data['lan']))
 scriptid=item.get('option',{}).get('script')
 entry=next((x for x in load(root/'profiles.yaml')['items'] if x['uid']==scriptid),None)
 previous=safe_file(root,'profiles/'+entry['file']).read_text(encoding='utf-8') if entry else 'function main(config) { return config; }'
 # Do not nest this project's wrapper on repeated applications.
 previous=previous.split('// PORTABLE-CLAUDE-NETWORK-V1')[0]
 secure=(PROJECT/'assets/secure-main.js').read_text(encoding='utf-8')
 secure=secure.replace("  delete config['prepend-rules'];", "  Object.assign(config['rule-providers'],data.lanProviderOverrides);\n  delete config['prepend-rules'];")
 wrapper=previous+'\n// PORTABLE-CLAUDE-NETWORK-V1\n'+secure+'\n;(()=>{ const before=main; const data='+json.dumps(data,ensure_ascii=False,separators=(',',':'))+'; main=function(config){return secureMain(before(config),data,'+json.dumps(node)+','+json.dumps(GROUP)+');};})();\n'
 path=LOCAL/'candidate.js';path.write_text(wrapper,encoding='utf-8');nodebin,core=executables()
 runner="const fs=require('fs'),vm=require('vm');vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'));let s='';process.stdin.on('data',d=>s+=d);process.stdin.on('end',()=>process.stdout.write(JSON.stringify(main(JSON.parse(s)))));"
 result=subprocess.run([nodebin,'-e',runner,str(path)],input=json.dumps(config),capture_output=True,text=True,encoding='utf-8',timeout=30)
 assert result.returncode==0,'Existing or generated enhancement script failed; no changes published'
 candidate=json.loads(result.stdout);validate(candidate,original,node)
 text=yaml.safe_dump(candidate,allow_unicode=True,sort_keys=False);check=LOCAL/'candidate.yaml';check.write_text(text,encoding='utf-8')
 result=subprocess.run([core,'-t','-d',str(root),'-f',str(check)],capture_output=True,timeout=45)
 assert result.returncode==0,'Mihomo validation failed; candidate retained locally for agent review'
 return wrapper,text,candidate,entry
def validate(config,original,node):
 assert config['proxies']==original['proxies'],'Node definitions changed'
 assert set(config.get('proxy-providers',{}))==set(original.get('proxy-providers',{})),'Subscription provider removed'
 for name,provider in original.get('proxy-providers',{}).items():
  assert {k:v for k,v in provider.items() if k!='proxy'}=={k:v for k,v in config['proxy-providers'][name].items() if k!='proxy'},'Subscription provider changed unexpectedly'
 group=next(g for g in config['proxy-groups'] if g['name']==GROUP)
 assert group['proxies']==[node] and not group.get('use') and not group.get('include-all') and group['type']=='select'
 assert config['rules'][-1]=='MATCH,'+GROUP
 assert sum(r.startswith('MATCH,') for r in config['rules'])==1
 assert config['rules'].index('DOMAIN-SUFFIX,claude.ai,'+GROUP)<config['rules'].index('RULE-SET,strict-cn-domain,DIRECT')
 assert config['tun']['enable'] and config['tun']['strict-route'] and not config['ipv6'] and not config['dns']['ipv6']
 assert config['dns']['enhanced-mode']=='fake-ip' and not config['dns'].get('fallback')
 assert set(config['tun']['dns-hijack'])=={'any:53','tcp://any:53'}
 for host in ('anthropic.com','claude.ai','claude.com','claudemcpclient.com','claudemcpcontent.com'):
  assert config['rules'].index('DOMAIN-SUFFIX,'+host+','+GROUP)<config['rules'].index('RULE-SET,strict-cn-domain,DIRECT')
 for server in config['dns']['nameserver']:
  assert '#'+GROUP in server,'Foreign DNS bypasses pinned group'
 direct_providers={r.split(',')[1] for r in config['rules'] if r.startswith('RULE-SET,') and r.split(',')[2]=='DIRECT'}
 for name in direct_providers:
  provider=config['rule-providers'][name]
  for value in provider.get('payload',[]):
   parts=value.split(',');cidr=parts[1] if parts[0]=='IP-CIDR' else value if provider.get('behavior')=='ipcidr' and ':' not in value else None
   if cidr:assert not ipaddress.ip_network(cidr,strict=False).overlaps(FAKE),'DIRECT provider overlaps fake-IP pool'
 for key in ('nameserver','default-nameserver','direct-nameserver','proxy-server-nameserver'):
  assert all('disable-qtype-64=true' in v and 'disable-qtype-65=true' in v for v in config['dns'][key])
 for value in config['dns'].get('nameserver-policy',{}).values():
  values=value if isinstance(value,list) else [value]
  assert all('disable-qtype-64=true' in v and 'disable-qtype-65=true' in v for v in values),'DNS policy path bypasses type filter'
def backup(root):
 path=LOCAL/'baseline';manifest=path/'manifest.json'
 if manifest.exists():
  state=json.loads(manifest.read_text());assert state['computer']==socket.gethostname(),'Backup belongs to another computer'
  assert state.get('user_sid')==user_sid(),'Backup belongs to another Windows user'
  assert Path(state['client_root']).resolve()==root.resolve(),'Existing baseline is for a different client data directory'
  for name,digest in state['sha256'].items():assert hashlib.sha256(((path/'client'/name) if (path/'client'/name).exists() else path/name).read_bytes()).hexdigest()==digest,'Backup integrity failure'
  for name,digest in state.get('extra_sha256',{}).items():assert hashlib.sha256((path/name).read_bytes()).hexdigest()==digest,'System backup integrity failure'
  return
 assert not path.exists(),'Incomplete first backup; inspect before retrying'
 path.mkdir();shutil.copytree(root,path/'client')
 write(manifest,{'computer':socket.gethostname(),'user_sid':user_sid(),'client_root':str(root),'time':datetime.datetime.now().astimezone().isoformat(),
  'coverage':{},'sha256':{str(p.relative_to(path/'client')):hashlib.sha256(p.read_bytes()).hexdigest() for p in (path/'client').rglob('*') if p.is_file()}})
def user_sid():
 value=subprocess.check_output(['whoami','/user','/fo','csv','/nh'],text=True)
 return re.search(r'S-1-\d+(?:-\d+)+',value).group(0)
def seal():
 path=LOCAL/'baseline';manifest=path/'manifest.json';state=json.loads(manifest.read_text(encoding='utf-8'))
 backup(Path(state['client_root']));extras=state.setdefault('extra_sha256',{})
 for file in path.iterdir():
  if not file.is_file() or file==manifest:continue
  digest=hashlib.sha256(file.read_bytes()).hexdigest()
  assert file.name not in extras or extras[file.name]==digest,'Previously sealed system snapshot changed'
  extras[file.name]=digest
 write(manifest,state);return {'system_backup_sealed':True}
def prepare(root,node,subscription,region):
 item=select(root,node,subscription);backup(root);wrapper,text,candidate,entry=build(root,item,node)
 uid=entry['uid'] if entry else 'PortableClaude'+uuid.uuid4().hex[:12]
 file=entry['file'] if entry else uid+'.js'
 plan={'root':str(root),'node':node,'subscription_uid':item['uid'],'script_uid':uid,'script_file':file,
  'script_existed':entry is not None,'region':region,'core_program':actual_core(), 'generated_files':[] if entry else ['profiles/'+file]}
 if (LOCAL/'plan.json').exists():
  earlier=json.loads((LOCAL/'plan.json').read_text(encoding='utf-8'))
  assert Path(earlier['root']).resolve()==root.resolve(),'Previous plan targets another client directory'
  plan['generated_files']=list(dict.fromkeys(earlier.get('generated_files',[])+plan['generated_files']))
 write(LOCAL/'plan.json',plan);write(LOCAL/'settings.json',{'node':node,'subscription':item['uid'],'region':region})
 return {'prepared':True,'node':node,'backup':str(LOCAL/'baseline'),'rule_count':len(candidate['rules'])}
def actual_core():
 command="Get-CimInstance Win32_Process | Where-Object {$_.Name -eq 'verge-mihomo.exe'} | Select-Object -ExpandProperty ExecutablePath"
 result=subprocess.check_output(['powershell.exe','-NoProfile','-Command',command],text=True).strip().splitlines()
 assert len(result)==1 and Path(result[0]).is_file(),'Cannot identify unique actual core executable for firewall rule'
 return result[0]
def apply_client():
 plan=json.loads((LOCAL/'plan.json').read_text());root=root_path(plan['root']);backup(root)
 text=(LOCAL/'candidate.yaml').read_text(encoding='utf-8');current=(root/'clash-verge.yaml').read_text(encoding='utf-8')
 profiles=load(root/'profiles.yaml');item=next(x for x in profiles['items'] if x['uid']==plan['subscription_uid'])
 if not plan['script_existed']:profiles['items'].append({'uid':plan['script_uid'],'type':'script','file':plan['script_file'],'desc':'Portable Claude network enhancement','updated':int(__import__('time').time())})
 item.setdefault('option',{})['script']=plan['script_uid'];profiles['current']=item['uid']
 targets={root/'profiles.yaml':yaml.safe_dump(profiles,allow_unicode=True,sort_keys=False),
  safe_file(root,'profiles/'+plan['script_file']):(LOCAL/'candidate.js').read_text(encoding='utf-8'),root/'clash-verge.yaml':text}
 for filename,updates in [('verge.yaml',{'enable_tun_mode':True,'enable_system_proxy':False,'enable_dns_settings':False}),('config.yaml',{'mode':'rule','ipv6':False,'tun':yaml.safe_load(text)['tun']})]:
  value=load(root/filename) or {};value.update(updates);targets[root/filename]=yaml.safe_dump(value,allow_unicode=True,sort_keys=False)
 old={p:p.read_bytes() if p.exists() else None for p in targets}
 try:
  api(root,'PUT','/configs?force=true',{'payload':text})
  for path,value in targets.items():path.write_text(value,encoding='utf-8')
 except Exception:
  for path,value in old.items():
   if value is not None:path.write_bytes(value)
   elif path.exists():path.unlink()
  api(root,'PUT','/configs?force=true',{'payload':current});raise
 return {'client_applied':True,'node':plan['node']}
def restore_client():
 plan=json.loads((LOCAL/'plan.json').read_text());root=root_path(plan['root']);backup(root)
 original=LOCAL/'baseline/client'
 for p in original.rglob('*'):
  if p.is_file():dest=safe_file(root,str(p.relative_to(original)));dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
 for relative in plan['generated_files']:
  p=safe_file(root,relative)
  if p.exists() and not (original/relative).exists():p.unlink()
 api(root,'PUT','/configs?force=true',{'payload':(root/'clash-verge.yaml').read_text(encoding='utf-8')})
 return {'client_restored':True}

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('action',choices=['discover','prepare','seal','apply-client','restore-client'])
 parser.add_argument('--root');parser.add_argument('--node');parser.add_argument('--subscription');parser.add_argument('--region',default='SG');args=parser.parse_args()
 if args.action=='discover':result=discover(root_path(args.root))
 elif args.action=='prepare':
  assert args.node,'Exact node name is required';result=prepare(root_path(args.root),args.node,args.subscription,args.region)
 elif args.action=='seal':result=seal()
 elif args.action=='apply-client':result=apply_client()
 else:result=restore_client()
 print(json.dumps(result,ensure_ascii=False))
