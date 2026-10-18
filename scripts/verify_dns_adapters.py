"""Bind DNS probes to each usable IPv4 interface; retain results locally."""
import concurrent.futures,datetime,json,subprocess
from verify import query
from client import LOCAL,write

command="Get-NetAdapter -IncludeHidden | ForEach-Object { [pscustomobject]@{name=$_.Name;status=$_.Status.ToString();index=$_.ifIndex;addresses=@(Get-NetIPAddress -InterfaceIndex $_.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty IPAddress)} } | ConvertTo-Json -Compress"
adapters=json.loads(subprocess.check_output(['powershell.exe','-NoProfile','-Command',command],text=True,encoding='utf-8',errors='replace'))
if isinstance(adapters,dict):adapters=[adapters]
jobs=[]
def probe(adapter,address,tcp,kind):
 try:
  result=query('8.8.8.8',53,kind,tcp,source=address)
  return {'adapter':adapter['name'],'source':address,**result}
 except Exception as exc:
  return {'adapter':adapter['name'],'source':address,'qtype':kind,'transport':'TCP' if tcp else 'UDP','passed':False,'error':type(exc).__name__}
with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
 for adapter in adapters:
  if adapter['status']!='Up':continue
  for address in adapter['addresses']:
   if address.startswith(('169.254.','127.')):continue
   for tcp in (False,True):
    for kind in (1,64,65):jobs.append(pool.submit(probe,adapter,address,tcp,kind))
 results=[job.result() for job in jobs]
report={'time':datetime.datetime.now().astimezone().isoformat(),'interfaces':adapters,'probes':results,
        'all_testable_paths_passed':bool(results) and all(r['passed'] for r in results),
        'scope':'Host Windows active IPv4 paths entering TUN; disconnected/absent interfaces and arbitrary application DoH are not proven'}
write(LOCAL/'dns-adapter-verification.json',report)
print(json.dumps({'active_ipv4_paths_tested':len({(r['adapter'],r['source']) for r in results}),
                  'probes':len(results),'passed':sum(r['passed'] for r in results),
                  'all_testable_paths_passed':report['all_testable_paths_passed']},ensure_ascii=False))
