"""Offline regression and publish hygiene checks; never modifies live client/system."""
from checks import require
from pathlib import Path
import copy,json,os,re,shutil,subprocess,sys,py_compile
from client import PROJECT,LOCAL,GROUP,validate

def tracked_files():
 if (PROJECT/'.git').exists():
  result=subprocess.check_output(['git','ls-files','--cached','--others','--exclude-standard','-z'],cwd=PROJECT)
  return [PROJECT/os.fsdecode(p) for p in result.split(b'\0') if p]
 return [p for p in PROJECT.rglob('*') if p.is_file() and not any(v in p.relative_to(PROJECT).parts for v in ('.local','.git','__pycache__'))]

def check():
 files=tracked_files();require(files,'No project files found')
 private_list=LOCAL/'publish-forbidden-values.json'
 banned=json.loads(private_list.read_text(encoding='utf-8')) if private_list.exists() else []
 for path in files:
  relative=path.relative_to(PROJECT)
  require('.local' not in relative.parts,'Local machine data is tracked')
  if path.suffix=='.py':py_compile.compile(str(path),doraise=True)
  text=path.read_text(encoding='utf-8-sig')
  require(not re.search(r'(?:gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{35,}|-----BEGIN (?:RSA |OPENSSH )?PRIVATE KEY-----)',text),'Credential-like material found in '+str(relative))
  require(not any(value in text for value in banned),'Original machine data found in '+str(relative))
  require(not re.search(r'S-1-5-21-\d+-\d+-\d+-\d+',text),'Machine SID found')
 require({p.name for p in PROJECT.glob('*.cmd')}=={'一键网络.cmd'},'Expected exactly one public entry: 一键网络.cmd')
 policy=json.loads((PROJECT/'assets/claude-policy.json').read_text(encoding='utf-8'))
 data={**policy,'domains':['+.cn'],'ips':['1.0.1.0/24'],'privateDomains':['+.lan'],
       'lanCidrs':['10.0.0.0/8'],'lan':['IP-CIDR,127.0.0.0/8,DIRECT,no-resolve']}
 base={'proxies':[{'name':'Chosen','type':'socks5','server':'example.invalid','port':1234},
                  {'name':'Keep-Unknown','type':'socks5','server':'unrelated.invalid','port':4321}],
       'proxy-groups':[{'name':'Old','type':'select','proxies':['Chosen','DIRECT']}],
       'proxy-providers':{},'rule-providers':{},'rules':['MATCH,DIRECT']}
 script=(PROJECT/'assets/secure-main.js').read_text(encoding='utf-8')
 code=script+'\nprocess.stdout.write(JSON.stringify(secureMain('+json.dumps(base)+','+json.dumps(data)+',"Chosen",'+json.dumps(GROUP)+')));'
 result=subprocess.run([shutil.which('node') or 'node','-e',code],capture_output=True,text=True,encoding='utf-8',check=True)
 candidate=json.loads(result.stdout);validate(candidate,base,'Chosen')
 require(candidate['dns']['nameserver-policy']['+.local']==['system'],'system resolver must stay unmodified')
 require(candidate['dns']['nameserver-policy']['+.lan']==['system'],'system resolver must stay unmodified')
 cases=[]
 def reject(name,change):
  value=copy.deepcopy(candidate);change(value)
  try:validate(value,base,'Chosen')
  except (AssertionError,KeyError,ValueError):cases.append(name)
  else:raise AssertionError('Unsafe mutation accepted: '+name)
 reject('DIRECT fallback',lambda c:c['rules'].__setitem__(-1,'MATCH,DIRECT'))
 reject('DIRECT in pinned group',lambda c:c['proxy-groups'][-1]['proxies'].append('DIRECT'))
 reject('Node removed',lambda c:c['proxies'].pop())
 reject('TUN disabled',lambda c:c['tun'].__setitem__('enable',False))
 reject('IPv6 enabled',lambda c:c.__setitem__('ipv6',True))
 reject('DNS filter removed',lambda c:c['dns']['nameserver'].__setitem__(0,'https://1.1.1.1/dns-query#'+GROUP))
 reject('Private DNS bypasses filter',lambda c:c['dns']['nameserver-policy'].__setitem__('+.local',['https://223.5.5.5/dns-query#DIRECT']))
 reject('TUN auto-route disabled',lambda c:c['tun'].__setitem__('auto-route',False))
 reject('Claude domain removed',lambda c:c['rules'].remove('DOMAIN-SUFFIX,clau.de,'+GROUP))
 reject('Fake-IP marked direct',lambda c:c['rule-providers']['strict-lancidr']['payload'].append('198.18.0.0/16'))
 reject('Domestic DNS for foreign',lambda c:c['dns'].__setitem__('nameserver',['https://223.5.5.5/dns-query#DIRECT&disable-qtype-64=true&disable-qtype-65=true']))
 report={'success':True,'published_files_checked':len(files),'unsafe_mutations_rejected':cases,
         'live_client_or_system_changed':False}
 (LOCAL/'offline-checks.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__':check()
