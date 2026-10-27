"""Clash Verge Rev Windows adapter. Secrets and backups stay under .local/."""
from checks import require
from pathlib import Path
import argparse,copy,datetime,hashlib,ipaddress,json,os,re,shutil,socket,subprocess,sys,urllib.request,uuid
import yaml
from baseline import is_runtime_snapshot_file,verify_incremental_files,default_snapshot,pin_baseline
from routing_checks import audit_ingress,audit_lan_provider,lan_domain,local_network,normalize_lan_cidrs

PROJECT=Path(__file__).resolve().parent.parent
LOCAL=PROJECT/'.local';LOCAL.mkdir(exist_ok=True)
SNAPSHOTS=LOCAL/'snapshots'
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
def snapshot_roots():
 return sorted([p for p in SNAPSHOTS.glob('*') if (p/'manifest.json').is_file()],key=lambda p:p.name,reverse=True)
def migrate_legacy_baseline():
 # A single pre-snapshot baseline from an older version becomes the first snapshot.
 legacy=LOCAL/'baseline'
 if not (legacy/'manifest.json').is_file():return
 SNAPSHOTS.mkdir(exist_ok=True)
 if any(SNAPSHOTS.iterdir()):return
 state=json.loads((legacy/'manifest.json').read_text(encoding='utf-8'))
 digits=''.join(c for c in str(state.get('time','')) if c.isdigit())
 stamp=digits[:8]+'-'+digits[8:14] if len(digits)>=14 else datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
 target=SNAPSHOTS/stamp
 shutil.move(str(legacy),str(target))
 state.setdefault('kind','auto')
 write(target/'manifest.json',state)
def snapshot_kind(path):
 try:return str(json.loads((path/'manifest.json').read_text(encoding='utf-8')).get('kind','auto'))
 except Exception:return 'auto'
def snapshot_dir(create=False):
 # CCN_SNAPSHOT pins the snapshot for the current run; otherwise the automatic
 # pre-first-apply snapshot wins, so a later manual snapshot never becomes the
 # rollback point for a subsequent apply.
 value=os.environ.get('CCN_SNAPSHOT')
 if value:
  path=Path(value)
  if create:path.mkdir(parents=True,exist_ok=True)
  return path
 migrate_legacy_baseline()
 selected=default_snapshot(LOCAL)
 if selected:return selected
 require(create,'No snapshot captured yet; run apply once to create the first snapshot')
 stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
 path=SNAPSHOTS/stamp
 while path.exists():path=SNAPSHOTS/(stamp+'-'+uuid.uuid4().hex[:4])
 path.mkdir(parents=True)
 return path
def new_snapshot():
 migrate_legacy_baseline()
 stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
 path=SNAPSHOTS/stamp
 while path.exists():path=SNAPSHOTS/(stamp+'-'+uuid.uuid4().hex[:4])
 path.mkdir(parents=True)
 return path
def root_path(value=None):
 require(os.name=='nt','Bundled system adapter supports Windows only. Read references/windows.md for the supported platform.')
 root=Path(value) if value else Path(os.environ['APPDATA'])/'io.github.clash-verge-rev.clash-verge-rev'
 require((root/'profiles.yaml').is_file() and (root/'clash-verge.yaml').is_file(),'找不到 Clash Verge Rev 的数据目录。')
 return root.resolve()
def safe_file(root,relative):
 path=(root/relative).resolve();require(path.is_relative_to(root.resolve()),'Path escapes client directory');return path
def executables():
 node=shutil.which('node');require(node,'Install Node.js from its official source first')
 return node,actual_core()
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
 require(len(matches)==1,'节点不存在或有重名。请指定订阅名称或 UID；不要猜测。')
 return next(x for x in load(root/'profiles.yaml')['items'] if x['uid']==matches[0]['uid'])
def download_rules():
 data={};metadata={};opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
 for name,url in SOURCES.items():
  with opener.open(url,timeout=30) as response:raw=response.read(4_000_001)
  require(len(raw)<=4_000_000,'Oversized rule source')
  if name=='domains':
   values=[]
   for line in raw.decode('utf-8-sig').splitlines():
    v=line.strip()
    if not v or v.startswith((';','[')) or re.fullmatch(r'[0-9.*]+',v):continue
    require(re.fullmatch(r'[a-zA-Z0-9.*_-]+',v),'Unrecognized domain-list syntax')
    v='+.'+v[2:] if v.startswith('*.') else v
    if v not in ('+.douban.*','+.jianshu.*'):
     # Never reuse the outer loop variable here: clobbering `name` would key
     # this source under the last domain instead of 'domains'.
     bare=v.removeprefix('+.');require('*' not in bare,'配置检查未通过，请查看本地记录。')
     require('.' in bare or bare in ('cn','xn--fiqs8s'),'Overbroad public suffix requires review')
     require(all(re.fullmatch(r'[A-Za-z0-9_](?:[A-Za-z0-9_-]{0,61}[A-Za-z0-9_])?',label) for label in bare.split('.')),'配置检查未通过，请查看本地记录。')
    values.append(v)
  else:
   values=yaml.safe_load(raw)['payload'];require(isinstance(values,list),'配置检查未通过，请查看本地记录。')
   if name=='lanCidrs':values=[v for v in values if v!='198.18.0.0/15']
   if name in ('ips','lanCidrs'):
    for value in values:
     network=ipaddress.ip_network(value);require(network.prefixlen>0,'配置检查未通过，请查看本地记录。')
     if network.version==4:require(not network.overlaps(FAKE),'Direct rule overlaps fake-IP pool')
  data[name]=list(dict.fromkeys(values));metadata[name]={'url':url,'sha256':hashlib.sha256(raw).hexdigest(),'count':len(data[name])}
 require(len(data['domains'])>1000 and len(data['ips'])>8000 and len(data['privateDomains'])>30,'配置检查未通过，请查看本地记录。')
 write(LOCAL/'sources.json',metadata);return data
def api(root,method,path,body=None):
 config=load(root/'clash-verge.yaml');pipes=[n for n in os.listdir('//./pipe/') if n.startswith('verge-mihomo-')]
 require(len(pipes)==1,'Cannot identify a unique Mihomo control pipe')
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
 require(header.split(b'\r\n')[0].split()[1].startswith(b'2'),'Mihomo API request failed; response withheld to protect credentials')
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
 lan_audit=[]
 data['lanSourceCidrs']=list(data['lanCidrs'])
 data['lanCidrs']=normalize_lan_cidrs(data['lanCidrs'],lan_audit)
 for domain in data['privateDomains']:lan_domain(domain,data)
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
  require(payload is not None,'Original LAN provider cache missing; inspect it before proceeding')
  require(provider.get('behavior') in ('classical','domain','ipcidr'),'Unsupported LAN provider format')
  cleaned=[]
  for value in payload:
   parts=value.split(',');cidr=parts[1] if parts[0]=='IP-CIDR' else value if provider['behavior']=='ipcidr' and ':' not in value else None
   if cidr:
    network=ipaddress.ip_network(cidr,strict=False)
    if network.overlaps(FAKE):continue
   cleaned.append(value)
  data['lanProviderOverrides'][name]=audit_lan_provider({'type':'inline','behavior':provider['behavior'],'payload':cleaned},data,lan_audit)
  data['lan'].append(rule)
 data['lan']=list(dict.fromkeys(data['lan']))
 write(LOCAL/'lan-audit.json',{'source_profiles_changed':False,'changes':lan_audit})
 scriptid=item.get('option',{}).get('script')
 entry=next((x for x in load(root/'profiles.yaml')['items'] if x['uid']==scriptid),None)
 previous=safe_file(root,'profiles/'+entry['file']).read_text(encoding='utf-8') if entry else 'function main(config) { return config; }'
 # Do not nest this project's wrapper on repeated applications.
 if '// PORTABLE-CLAUDE-NETWORK-V1' in previous:previous,_=strip_project_wrapper(previous)
 secure=(PROJECT/'assets/secure-main.js').read_text(encoding='utf-8')
 secure=secure.replace("  delete config['prepend-rules'];", "  Object.assign(config['rule-providers'],data.lanProviderOverrides);\n  delete config['prepend-rules'];")
 wrapper=previous+'\n// PORTABLE-CLAUDE-NETWORK-V1\n'+secure+'\n;(()=>{ const before=main; const data='+json.dumps(data,ensure_ascii=False,separators=(',',':'))+'; main=function(config){return secureMain(before(config),data,'+json.dumps(node)+','+json.dumps(GROUP)+');};})();\n'
 path=LOCAL/'candidate.js';path.write_text(wrapper,encoding='utf-8');nodebin,core=executables()
 runner="const fs=require('fs'),vm=require('vm');vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'));let s='';process.stdin.on('data',d=>s+=d);process.stdin.on('end',()=>process.stdout.write(JSON.stringify(main(JSON.parse(s)))));"
 result=subprocess.run([nodebin,'-e',runner,str(path)],input=json.dumps(config),capture_output=True,text=True,encoding='utf-8',timeout=30)
 require(result.returncode==0,'Existing or generated enhancement script failed; no changes published')
 candidate=json.loads(result.stdout);validate(candidate,original,node)
 text=yaml.safe_dump(candidate,allow_unicode=True,sort_keys=False);check=LOCAL/'candidate.yaml';check.write_text(text,encoding='utf-8')
 result=subprocess.run([core,'-t','-d',str(root),'-f',str(check)],capture_output=True,timeout=45)
 require(result.returncode==0,'Mihomo validation failed; candidate retained locally for agent review')
 return wrapper,text,candidate,entry
def validate(config,original,node):
 require(config['proxies']==original['proxies'],'Node definitions changed')
 require(config.get('mode')=='rule','Only rule mode is allowed; direct/global mode rejected')
 audit_ingress(config,GROUP)
 require(set(config.get('proxy-providers',{}))==set(original.get('proxy-providers',{})),'Subscription provider removed')
 for name,provider in original.get('proxy-providers',{}).items():
  require({k:v for k,v in provider.items() if k!='proxy'}=={k:v for k,v in config['proxy-providers'][name].items() if k!='proxy'},'Subscription provider changed unexpectedly')
 group=next(g for g in config['proxy-groups'] if g['name']==GROUP)
 require(group['proxies']==[node] and not group.get('use') and not group.get('include-all') and group['type']=='select','配置检查未通过，请查看本地记录。')
 require(config['rules'][-1]=='MATCH,'+GROUP,'配置检查未通过，请查看本地记录。')
 require(sum(r.startswith('MATCH,') for r in config['rules'])==1,'配置检查未通过，请查看本地记录。')
 require(config['rules'].index('DOMAIN-SUFFIX,claude.ai,'+GROUP)<config['rules'].index('RULE-SET,strict-cn-domain,DIRECT'),'配置检查未通过，请查看本地记录。')
 require(config['tun']['enable'] and config['tun']['strict-route'] and not config['ipv6'] and not config['dns']['ipv6'],'配置检查未通过，请查看本地记录。')
 require(config['tun'].get('auto-route') and config['tun'].get('auto-detect-interface'),'TUN auto-route/auto-detect-interface disabled')
 require(config['dns']['enhanced-mode']=='fake-ip' and not config['dns'].get('fallback'),'配置检查未通过，请查看本地记录。')
 require(set(config['tun']['dns-hijack'])=={'any:53','tcp://any:53'},'配置检查未通过，请查看本地记录。')
 for host in ('anthropic.com','claude.ai','claude.com','clau.de','claudeusercontent.com','claudemcpclient.com','claudemcpcontent.com'):
  require(config['rules'].index('DOMAIN-SUFFIX,'+host+','+GROUP)<config['rules'].index('RULE-SET,strict-cn-domain,DIRECT'),'配置检查未通过，请查看本地记录。')
 policy=json.loads((PROJECT/'assets/claude-policy.json').read_text(encoding='utf-8'))
 protected=['DOMAIN-SUFFIX,'+host+','+GROUP for host in policy['claude']+policy['extraSuffix']]
 protected+=['DOMAIN,'+host+','+GROUP for host in policy['extraExact']]
 end=max(config['rules'].index(rule) for rule in protected)
 selected=next(p for p in original['proxies'] if p['name']==node)
 try:server=ipaddress.ip_address(selected['server'])
 except ValueError:server=None
 for rule in config['rules'][:end+1]:
  parts=rule.split(',')
  if len(parts)<3 or parts[2]!='DIRECT':continue
  require(len(parts) in (3,4) and (len(parts)==3 or parts[3]=='no-resolve'),'Unknown early DIRECT rule')
  if parts[0]=='RULE-SET':
   provider=config['rule-providers'][parts[1]]
   require(audit_lan_provider(provider,policy)==provider,'LAN provider contains non-LAN ranges; normalize before publishing')
  elif parts[0] in ('DOMAIN','DOMAIN-SUFFIX'):lan_domain(parts[1],policy)
  elif parts[0] in ('IP-CIDR','IP-CIDR6'):
   network=ipaddress.ip_network(parts[1],strict=False)
   node_exception=bool(server and server.version==4 and network==ipaddress.ip_network(str(server)+'/32'))
   require(local_network(network) or node_exception,'Public address in early LAN DIRECT rules')
  else:raise AssertionError('Unknown early DIRECT rule; review required')
 for server in config['dns']['nameserver']:
  require('#'+GROUP in server,'Foreign DNS bypasses pinned group')
 direct_providers={r.split(',')[1] for r in config['rules'] if r.startswith('RULE-SET,') and r.split(',')[2]=='DIRECT'}
 for name in direct_providers:
  provider=config['rule-providers'][name]
  for value in provider.get('payload',[]):
   parts=value.split(',');cidr=parts[1] if parts[0]=='IP-CIDR' else value if provider.get('behavior')=='ipcidr' and ':' not in value else None
   if cidr:require(not ipaddress.ip_network(cidr,strict=False).overlaps(FAKE),'DIRECT provider overlaps fake-IP pool')
 def filtered(server):
  # Special resolvers ("system", "dhcp://...") cannot carry query flags; the type
  # filter only applies to real resolver URLs.
  if not re.match(r'(?i)^(?:https?|tls|quic|h3)://',server):return True
  return 'disable-qtype-64=true' in server and 'disable-qtype-65=true' in server
 for key in ('nameserver','default-nameserver','direct-nameserver','proxy-server-nameserver'):
  require(all(filtered(v) for v in config['dns'].get(key,[])),'DNS server path bypasses type filter')
 for value in config['dns'].get('nameserver-policy',{}).values():
  values=value if isinstance(value,list) else [value]
  require(all(filtered(v) for v in values),'DNS policy path bypasses type filter')
def already_configured(root):
 for candidate in (root/'profiles').glob('*'):
  if candidate.suffix not in ('.js','.yaml','.yml'):continue
  try:
   if 'PORTABLE-CLAUDE-NETWORK-V1' in candidate.read_text(encoding='utf-8',errors='ignore'):return True
  except OSError:pass
 return False
def backup(root,path=None,allow_configured=False):
 path=path or snapshot_dir(create=True);manifest=path/'manifest.json'
 if manifest.exists():
  state=json.loads(manifest.read_text(encoding='utf-8'));require(state['computer']==socket.gethostname(),'Backup belongs to another computer')
  require(state.get('user_sid')==user_sid(),'Backup belongs to another Windows user')
  require(Path(state['client_root']).resolve()==root.resolve(),'Existing baseline is for a different client data directory')
  for name,digest in state['sha256'].items():require(hashlib.sha256(((path/'client'/name) if (path/'client'/name).exists() else path/name).read_bytes()).hexdigest()==digest,'Backup integrity failure')
  for name,digest in state.get('extra_sha256',{}).items():
   if is_runtime_snapshot_file(name):continue
   require(hashlib.sha256((path/name).read_bytes()).hexdigest()==digest,'System backup integrity failure')
  verify_incremental_files(path,state)
  return
 if not allow_configured:
  require(not already_configured(root),'Client already carries this project enhancement. Capture the baseline before the first apply instead of snapshotting an already-configured state.')
 # A running Clash holds its instance lock open, including for administrators.
 # Exclude the same runtime-only files that restore deliberately never replays.
 def ignore_runtime(directory,names):
  relative=Path(directory).relative_to(root)
  return [name for name in names if is_volatile(relative/name)]
 path.mkdir(parents=True,exist_ok=True);shutil.copytree(root,path/'client',dirs_exist_ok=True,ignore=ignore_runtime)
 write(manifest,{'computer':socket.gethostname(),'user_sid':user_sid(),'client_root':str(root),'time':datetime.datetime.now().astimezone().isoformat(),
  'kind':'auto','coverage':{},'sha256':{str(p.relative_to(path/'client')):hashlib.sha256(p.read_bytes()).hexdigest() for p in (path/'client').rglob('*') if p.is_file()}})
def user_sid():
 value=subprocess.check_output(['whoami','/user','/fo','csv','/nh'],text=True)
 return re.search(r'S-1-\d+(?:-\d+)+',value).group(0)
def seal():
 path=snapshot_dir();manifest=path/'manifest.json';state=json.loads(manifest.read_text(encoding='utf-8'))
 backup(Path(state['client_root']),path);extras=state.setdefault('extra_sha256',{})
 for file in path.iterdir():
  if not file.is_file() or file==manifest:continue
  if is_runtime_snapshot_file(file.name):
   extras.pop(file.name,None);continue
  digest=hashlib.sha256(file.read_bytes()).hexdigest()
  require(file.name not in extras or extras[file.name]==digest,'Previously sealed system snapshot changed')
  extras[file.name]=digest
 write(manifest,state);return {'system_backup_sealed':True}
def prepare(root,node,subscription,region):
 item=select(root,node,subscription)
 path=snapshot_dir(create=True);backup(root,path);wrapper,text,candidate,entry=build(root,item,node)
 pin_baseline(LOCAL,default_snapshot(LOCAL) or path)
 # Never overwrite an existing user script. Reuse only our own generated wrapper.
 owned=entry and str(entry['uid']).startswith('PortableClaude') and 'PORTABLE-CLAUDE-NETWORK-V1' in safe_file(root,'profiles/'+entry['file']).read_text(encoding='utf-8')
 uid=entry['uid'] if owned else 'PortableClaude'+uuid.uuid4().hex[:12]
 file=entry['file'] if owned else uid+'.js'
 plan={'root':str(root),'node':node,'subscription_uid':item['uid'],'script_uid':uid,'script_file':file,
  'script_existed':bool(owned),'region':region,'core_program':actual_core(),'snapshot':str(path),'generated_files':['profiles/'+file]}
 if (LOCAL/'plan.json').exists():
  earlier=json.loads((LOCAL/'plan.json').read_text(encoding='utf-8'))
  require(Path(earlier['root']).resolve()==root.resolve(),'Previous plan targets another client directory')
  plan['generated_files']=list(dict.fromkeys(earlier.get('generated_files',[])+plan['generated_files']))
 write(LOCAL/'plan.json',plan);write(LOCAL/'settings.json',{'node':node,'subscription':item['uid'],'region':region})
 return {'prepared':True,'node':node,'snapshot':str(path),'rule_count':len(candidate['rules'])}
def is_file_safe(path):
 try:return path.is_file()
 except OSError:return False
def actual_core():
 command="Get-CimInstance Win32_Process | Where-Object {$_.Name -eq 'verge-mihomo.exe'} | Select-Object -ExpandProperty ExecutablePath"
 try:
  result=subprocess.check_output(['powershell.exe','-NoProfile','-Command',command],text=True,stderr=subprocess.DEVNULL).strip().splitlines()
 except OSError:
  result=[]
 result=sorted(set(line.strip() for line in result if line.strip()))
 if len(result)==1 and Path(result[0]).is_file():return result[0]
 # Fallback for an unelevated or sandboxed caller: the core runs as SYSTEM, so
 # its image path cannot be queried. Accept only a single unambiguous install.
 # The elevated apply re-runs this function and overwrites the unverified value.
 candidates=[p for p in (Path(os.environ.get('PROGRAMFILES',r'C:\Program Files'))/'Clash Verge/verge-mihomo.exe',
             Path(os.environ.get('PROGRAMDATA',r'C:\ProgramData'))/'clash-verge-service/cores/verge-mihomo.exe') if is_file_safe(p)]
 require(len(candidates)==1,'Cannot identify unique actual core executable for firewall rule')
 print('core path not verifiable without elevation; using single install: '+str(candidates[0]),file=sys.stderr)
 return str(candidates[0])
def apply_client():
 plan=json.loads((LOCAL/'plan.json').read_text(encoding='utf-8'));root=root_path(plan['root']);backup(root,Path(plan['snapshot']) if plan.get('snapshot') else None)
 text=(LOCAL/'candidate.yaml').read_text(encoding='utf-8');current=(root/'clash-verge.yaml').read_text(encoding='utf-8')
 profiles=load(root/'profiles.yaml');item=next(x for x in profiles['items'] if x['uid']==plan['subscription_uid'])
 journal_path=(Path(plan['snapshot']) if plan.get('snapshot') else snapshot_dir())/'client-changes.json'
 journal=json.loads(journal_path.read_text(encoding='utf-8-sig')) if journal_path.exists() else {'subscriptions':{}}
 record=journal['subscriptions'].setdefault(item['uid'],{'had_option':'option' in item,'had_script':'script' in item.get('option',{}),
                 'script':item.get('option',{}).get('script'),'applied_scripts':[]})
 if plan['script_uid'] not in record['applied_scripts']:record['applied_scripts'].append(plan['script_uid'])
 write(journal_path,journal) # Commit recovery metadata before changing any association.
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
VOLATILE_NAMES=('singleton-instance.lock','singleton-instance.json','cache.db')
VOLATILE_DIRS=('logs','clash-verge-rev-backup')
def is_volatile(relative):
 # Runtime state only: locks, caches and logs are held open by the running client
 # and replaying them cannot change behaviour, but they abort a whole restore.
 return relative.name in VOLATILE_NAMES or relative.suffix=='.lock' or (relative.parts and relative.parts[0] in VOLATILE_DIRS)
def restore_yaml(file):
 try:return load(file)
 except (OSError,UnicodeError,yaml.YAMLError):
  raise ValueError('Client restore input is unreadable or invalid; contents withheld') from None

def parse_project_wrapper(text):
 prefix,tail=text.split('// PORTABLE-CLAUDE-NETWORK-V1',1)
 bootstrap=';(()=>{ const before=main; const data='
 start=tail.find(bootstrap)
 if start<0:raise ValueError('Project script was modified; cannot safely unwrap it')
 decoder=json.JSONDecoder()
 try:
  data,end=decoder.raw_decode(tail,start+len(bootstrap))
  if not isinstance(data,dict):raise ValueError()
  call='; main=function(config){return secureMain(before(config),data,'
  if not tail.startswith(call,end):raise ValueError()
  node,end=decoder.raw_decode(tail,end+len(call))
  if not isinstance(node,str) or tail[end:end+1]!=',':raise ValueError()
  group,end=decoder.raw_decode(tail,end+1)
  closing=');};})();'
  if group!=GROUP or not tail.startswith(closing,end):raise ValueError()
 except (ValueError,TypeError):
  raise ValueError('Project script was modified; cannot safely unwrap it') from None
 suffix=tail[end+len(closing):]
 return prefix,suffix,data,node

def strip_project_wrapper(text):
 prefix,suffix,_,_=parse_project_wrapper(text)
 return prefix+suffix,bool(suffix.strip())

def client_restore_targets(root,original,plan,journal):
 # Merge only project-owned script associations into today's profile index.
 profiles=restore_yaml(root/'profiles.yaml')
 if not isinstance(profiles,dict) or not isinstance(profiles.get('items',[]),list):
  raise ValueError('Current subscription index is invalid; it will not be overwritten')
 profiles=copy.deepcopy(profiles);items=profiles.get('items',[])
 if profiles.get('current'):
  active=next((x for x in items if x.get('uid')==profiles['current']),None)
  if not active or not active.get('file'):raise ValueError('Active subscription source is missing')
  if not isinstance(restore_yaml(safe_file(root,'profiles/'+active['file'])),dict):
   raise ValueError('Active subscription source is invalid')
 before=restore_yaml(original/'profiles.yaml') or {}
 originals={entry['uid']:entry for entry in before.get('items',[])}
 targets={};remove=[];owned={};preserved=set()
 generated=set(plan.get('generated_files',[]))
 for entry in items:
  if entry.get('type')!='script' or not entry.get('file'):continue
  relative='profiles/'+entry['file'];file=safe_file(root,relative)
  if not file.is_file():continue
  text=file.read_text(encoding='utf-8')
  marker='// PORTABLE-CLAUDE-NETWORK-V1'
  if marker not in text:continue
  baseline_entry=originals.get(entry['uid'],{})
  baseline_file=safe_file(original,'profiles/'+baseline_entry['file']) if baseline_entry.get('file') else None
  if (baseline_entry.get('type')=='script' and baseline_entry.get('file')==entry['file'] and
      baseline_file and baseline_file.is_file() and marker in baseline_file.read_text(encoding='utf-8')):
   # This script already belonged to the selected snapshot. Keep its current
   # content (including later rule updates), rather than undoing the snapshot.
   parse_project_wrapper(text);preserved.add(entry['uid']);continue
  # Parse JSON boundaries so punctuation inside a node name cannot truncate JS.
  cleaned,has_user_suffix=strip_project_wrapper(text)
  created=relative in generated or str(entry['uid']).startswith('PortableClaude')
  if created and not has_user_suffix:
   owned[entry['uid']]=entry;remove.append(file)
  else:
   # Legacy versions overwrote user scripts; remove only the project suffix.
   targets[file]=cleaned
 for entry in items:
  uid=entry['uid'];option=entry.get('option',{})
  current=option.get('script')
  saved=journal.get('subscriptions',{}).get(uid)
  if saved and current in saved.get('applied_scripts',[]) and current in owned:
   if saved['had_script']:
    previous=saved.get('script')
    if previous is not None and previous not in {x['uid'] for x in items}:
     raise ValueError('Previous user script no longer exists; restore stopped')
    option['script']=previous
   else:option.pop('script',None)
   if not option and not saved.get('had_option'):entry.pop('option',None)
  elif current in owned:
   old=originals.get(uid,{}).get('option',{})
   if 'script' in old and old['script'] not in owned:
    if old['script'] and old['script'] not in {x['uid'] for x in items}:raise ValueError('Previous user script no longer exists')
    option['script']=old['script']
   else:option.pop('script',None)
 # Never resurrect removed subscriptions, change the active subscription, or
 # overwrite source files, URLs, ordering, timestamps, or node updates.
 referenced={x.get('option',{}).get('script') for x in items}
 if any(uid in referenced for uid in owned):raise ValueError('Project script is still referenced')
 for entry in items:
  uid=entry.get('option',{}).get('script')
  if uid not in preserved:
   old=originals.get(uid,{})
   if old.get('file'):
    old_file=safe_file(original,'profiles/'+old['file'])
    if old_file.is_file() and '// PORTABLE-CLAUDE-NETWORK-V1' in old_file.read_text(encoding='utf-8'):
     raise ValueError('Snapshot project script is missing or changed; restore stopped')
   continue
  script=next(x for x in items if x['uid']==uid)
  _,_,_,node=parse_project_wrapper(safe_file(root,'profiles/'+script['file']).read_text(encoding='utf-8'))
  source=restore_yaml(safe_file(root,'profiles/'+entry['file'])) or {}
  if not isinstance(source,dict):raise ValueError('Current subscription source is invalid')
  if not any(p.get('name')==node for p in source.get('proxies',[])):
   raise ValueError('Snapshot project node is absent from the current subscription; no replacement selected')
 if 'items' in profiles:profiles['items']=[x for x in items if x['uid'] not in owned]
 targets[root/'profiles.yaml']=yaml.safe_dump(profiles,allow_unicode=True,sort_keys=False)
 for name,keys in [('verge.yaml',('enable_tun_mode','enable_system_proxy','enable_dns_settings')),
                   ('config.yaml',('mode','ipv6','tun'))]:
  if not (original/name).is_file():continue
  saved=restore_yaml(original/name) or {};current=restore_yaml(root/name) or {}
  if not isinstance(saved,dict) or not isinstance(current,dict):raise ValueError('Invalid client settings')
  for key in keys:
   if key in saved:current[key]=copy.deepcopy(saved[key])
   else:current.pop(key,None)
  targets[root/name]=yaml.safe_dump(current,allow_unicode=True,sort_keys=False)
 return targets,remove,profiles

def restore_client():
 from baseline import verify
 state=verify();path=snapshot_dir();root=root_path(state['client_root']);backup(root,path)
 plan_path=LOCAL/'plan.json'
 plan=json.loads(plan_path.read_text(encoding='utf-8')) if plan_path.exists() else {}
 if plan:require(Path(plan['root']).resolve()==root,'Plan belongs to a different client data directory')
 journal_file=path/'client-changes.json'
 journal=json.loads(journal_file.read_text(encoding='utf-8-sig')) if journal_file.exists() else {}
 targets,remove,profiles=client_restore_targets(root,path/'client',plan,journal)
 failed=[];restored=[]
 runtime=root/'clash-verge.yaml'
 old_runtime=hashlib.sha256(runtime.read_bytes()).hexdigest()
 for file,text in targets.items():
  try:
   # Atomic replacement avoids publishing half a subscription index.
   temp=file.with_name(file.name+'.ccn-restore-new');temp.write_text(text,encoding='utf-8');temp.replace(file)
   restored.append(str(file.relative_to(root)))
  except OSError as exc:failed.append({'file':str(file.relative_to(root)),'operation':'write','reason':type(exc).__name__})
 # Leave wrappers in place if any write failed; a retry can finish detaching them.
 if not failed:
  for file in remove:
   try:file.unlink(missing_ok=True)
   except OSError as exc:failed.append({'file':str(file.relative_to(root)),'operation':'remove-generated','reason':type(exc).__name__})
 rebuild=bool(profiles.get('items'))
 reloaded=False
 if not failed and not rebuild:
  try:api(root,'PUT','/configs?force=true',{'payload':runtime.read_text(encoding='utf-8')});reloaded=True
  except Exception as exc:failed.append({'file':'clash-verge.yaml','operation':'reload','reason':type(exc).__name__})
 active=next((x for x in profiles.get('items',[]) if x.get('uid')==profiles.get('current')),None)
 script=next((x for x in profiles.get('items',[]) if active and x.get('uid')==active.get('option',{}).get('script')),None)
 project_node=None
 if script and script.get('file') and not failed:
  text=safe_file(root,'profiles/'+script['file']).read_text(encoding='utf-8')
  if '// PORTABLE-CLAUDE-NETWORK-V1' in text:project_node=parse_project_wrapper(text)[3]
 report={'client_restored':not failed,'status':'partial' if failed else 'pending-runtime' if rebuild else 'complete',
         'root':str(root),'snapshot':str(path),'active_uid':profiles.get('current'),'previous_runtime_sha256':old_runtime,
         'subscriptions_preserved':True,'runtime_rebuild_required':rebuild,'runtime_reloaded':reloaded,'project_node':project_node,
         'restored_files':restored,'failed_items':failed,
         'locked_not_restored':[x['file'] for x in failed if x['reason']=='PermissionError']}
 write(LOCAL/'client-restore-result.json',report)
 return report

def restored_runtime_ready(root,report):
 current=restore_yaml(root/'profiles.yaml') or {}
 raw=(root/'clash-verge.yaml').read_bytes();text=raw.decode('utf-8-sig');effective=yaml.safe_load(text) or {}
 if not isinstance(current,dict) or not isinstance(effective,dict):raise ValueError('Invalid generated configuration')
 if current.get('current')!=report['active_uid']:return None
 project_node=report.get('project_node')
 if not project_node and GROUP in [x.get('name') for x in effective.get('proxy-groups',[])]:return None
 item=next((x for x in current.get('items',[]) if x.get('uid')==report['active_uid']),None)
 if not item or not item.get('file'):return None
 source=restore_yaml(safe_file(root,'profiles/'+item['file'])) or {}
 if not isinstance(source,dict):raise ValueError('Invalid subscription source')
 if project_node:
  script=next((x for x in current.get('items',[]) if x.get('uid')==item.get('option',{}).get('script')),None)
  if not script or not script.get('file'):return None
  if parse_project_wrapper(safe_file(root,'profiles/'+script['file']).read_text(encoding='utf-8'))[3]!=project_node:return None
  # A configured manual snapshot legitimately retains the pinned group. Check
  # it against today's source nodes instead of loading a saved runtime YAML.
  try:validate(effective,source,project_node)
  except (AssertionError,KeyError,TypeError,StopIteration):return None
 expected=source.get('proxies',[]);actual=effective.get('proxies',[])
 providers=effective.get('proxy-providers',{})
 providers_match=all(name in providers and all(providers[name].get(k)==v for k,v in definition.items() if k!='proxy')
                     for name,definition in source.get('proxy-providers',{}).items())
 fresh=hashlib.sha256(raw).hexdigest()!=report['previous_runtime_sha256']
 unchanged_source=(actual==expected and effective.get('rules',[])==source.get('rules',[])
                   and providers==source.get('proxy-providers',{}))
 if project_node:unchanged_source=True # validate() checked current nodes and routing above.
 tun=(restore_yaml(root/'verge.yaml') or {}).get('enable_tun_mode') if (root/'verge.yaml').exists() else None
 tun_matches=tun is None or bool(effective.get('tun',{}).get('enable',False))==bool(tun)
 if (fresh or unchanged_source) and tun_matches and providers_match and all(x in actual for x in expected):return text
 return None

def finish_restore(timeout=90):
 # Never publish old nodes as a fallback. Tolerate transient files/pipe errors
 # while the native client is regenerating and restarting its controller.
 import time
 report=json.loads((LOCAL/'client-restore-result.json').read_text(encoding='utf-8'))
 root=root_path(report['root']);deadline=time.monotonic()+timeout
 try:
  if report['failed_items']:raise ValueError('File restoration is incomplete')
  while True:
   try:
    text=restored_runtime_ready(root,report)
    if text is not None:
     api(root,'PUT','/configs?force=true',{'payload':text})
     report.update(client_restored=True,status='complete',runtime_reloaded=True,runtime_rebuild_required=False)
     break
   except (OSError,UnicodeError,ValueError,yaml.YAMLError,AssertionError):
    pass # Retry partially written files and a temporarily unavailable controller.
   if time.monotonic()>=deadline:raise TimeoutError('Native client has not regenerated the current subscription')
   time.sleep(.5)
 except Exception as exc:
  report.update(client_restored=False,status='partial')
  report['failed_items'].append({'file':'clash-verge.yaml','operation':'native-regeneration','reason':type(exc).__name__})
 write(LOCAL/'client-restore-result.json',report)
 return report

def create_snapshot(root=None,node=None):
 # Manual snapshot: capture whatever the machine looks like now, configured or not.
 root=root_path(root);path=new_snapshot();backup(root,path,allow_configured=True)
 state=json.loads((path/'manifest.json').read_text(encoding='utf-8'))
 state['kind']='manual'
 if node:state['node']=node
 write(path/'manifest.json',state)
 return {'snapshot':str(path),'time':state['time'],'kind':'manual'}

if __name__=='__main__':
 try:
  parser=argparse.ArgumentParser();parser.add_argument('action',choices=['discover','prepare','seal','apply-client','restore-client','finish-restore','create-snapshot'])
  parser.add_argument('--root');parser.add_argument('--node');parser.add_argument('--subscription');parser.add_argument('--region',default='SG');args=parser.parse_args()
  if args.action=='discover':result=discover(root_path(args.root))
  elif args.action=='prepare':
   require(args.node,'Exact node name is required');result=prepare(root_path(args.root),args.node,args.subscription,args.region)
  elif args.action=='seal':result=seal()
  elif args.action=='create-snapshot':result=create_snapshot(args.root,args.node)
  elif args.action=='apply-client':result=apply_client()
  elif args.action=='finish-restore':result=finish_restore()
  else:result=restore_client()
  # ASCII-only stdout: the caller is Windows PowerShell, whose pipe decoding
  # follows the console code page (936 in an elevated window) and cannot carry
  # emoji node names. \uXXXX escapes survive any code page and ConvertFrom-Json
  # turns them back into the original characters.
  print(json.dumps(result,ensure_ascii=True))
  if args.action in ('restore-client','finish-restore') and not result['client_restored']:raise SystemExit(1)
 except Exception as exc:
  import traceback
  (LOCAL/'client-error.log').write_text(traceback.format_exc(),encoding='utf-8')
  print('客户端操作未完成（'+type(exc).__name__+'）。详细错误保存在 .local/client-error.log；未输出订阅内容。',file=sys.stderr)
  raise SystemExit(1)
