"""Independent TUN-off drill. No browsers or Claude/Anthropic probes."""
import argparse,ctypes,json,os,re,socket,subprocess,sys,time,uuid
from checks import require
from client import LOCAL,root_path,write
from verification_reports import save_report
from verify import query

def api(root,method,path,body=None):
 # A stuck pipe must not trap both the drill and its independent recovery worker.
 code='import json,sys;from pathlib import Path;from client import api;print(json.dumps(api(Path(sys.argv[1]),sys.argv[2],sys.argv[3],json.loads(sys.argv[4]))))'
 result=subprocess.run([sys.executable,'-c',code,str(root),method,path,json.dumps(body)],capture_output=True,timeout=10,
  creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
 require(result.returncode==0,'Local controller unavailable')
 return json.loads(result.stdout)

def system_state():
 command="@{cores=@(Get-CimInstance Win32_Process -Filter \"Name='verge-mihomo.exe'\" -ErrorAction Stop | ForEach-Object {@{pid=$_.ProcessId;path=$_.ExecutablePath}});tun_up=@(Get-NetAdapter -IncludeHidden -ErrorAction Stop | Where-Object {$_.Name -ceq 'Meta' -and $_.Status -eq 'Up'}).Count} | ConvertTo-Json -Compress -Depth 4"
 result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',command],capture_output=True,timeout=12)
 require(result.returncode==0,'Cannot read core and TUN state')
 value=json.loads(result.stdout.decode('utf-8-sig'));cores=value['cores']
 require(len(cores)==1 and cores[0].get('path'),'Cannot identify a unique running core')
 return value

def tcp_reachable(address):
 try:
  with socket.create_connection((address,443),timeout=4):return True
 except OSError:return False

def restore_tun(root,timeout=25):
 deadline=time.monotonic()+timeout
 while True:
  try:
   api(root,'PATCH','/configs',{'tun':{'enable':True}})
   require(api(root,'GET','/configs').get('tun',{}).get('enable') is True,'TUN recovery not loaded')
   require(system_state()['tun_up']==1,'TUN adapter not recovered')
   return
  except Exception:
   if time.monotonic()>=deadline:raise
   time.sleep(1)

def start_guard(root):
 token=uuid.uuid4().hex;folder=LOCAL/'tun-drills'/token;folder.mkdir(parents=True)
 write(folder/'guard.json',{'root':str(root),'parent':os.getpid(),'timeout':60})
 with (folder/'watchdog.log').open('ab') as output:
  worker=subprocess.Popen([sys.executable,str(__file__),'--watch',token],stdout=output,stderr=output,
   creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
 deadline=time.monotonic()+8
 while not (folder/'ready').exists():
  require(worker.poll() is None and time.monotonic()<deadline,'TUN recovery watchdog unavailable')
  time.sleep(.1)
 return folder

def watch(token):
 require(bool(re.fullmatch('[a-f0-9]{32}',token)),'Invalid guard token')
 folder=LOCAL/'tun-drills'/token;record=json.loads((folder/'guard.json').read_text(encoding='utf-8'))
 kernel=ctypes.WinDLL('kernel32',use_last_error=True)
 kernel.OpenProcess.restype=ctypes.c_void_p
 kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_uint32]
 kernel.CloseHandle.argtypes=[ctypes.c_void_p]
 handle=kernel.OpenProcess(0x100000,False,record['parent'])
 # Stop before the drill if the parent's lifetime cannot be monitored.
 if not handle:return
 (folder/'ready').touch();deadline=time.monotonic()+record['timeout']
 try:
  while not (folder/'done').exists():
   if kernel.WaitForSingleObject(handle,0)!=258 or time.monotonic()>=deadline:
    try:
     restore_tun(root_path(record['root']));write(folder/'recovery.json',{'success':True});(folder/'done').touch()
    except Exception as exc:write(folder/'recovery.json',{'success':False,'error':type(exc).__name__})
    return
   time.sleep(.25)
 finally:kernel.CloseHandle(handle)

def verify_tun():
 report={'success':False,'scope':'主机新建直连 TCP 与 DNS 请求；不代表浏览器、虚拟机或显式本机代理路径。'}
 root=None;guard=None;touched=False
 try:
  plan=json.loads((LOCAL/'plan.json').read_text(encoding='utf-8'));root=root_path(plan['root'])
  require(api(root,'GET','/configs').get('tun',{}).get('enable') is True,'Expected enabled TUN before drill')
  before=system_state();require(before['tun_up']==1,'Expected active Meta TUN adapter')
  for label,address in [('foreign','1.1.1.1'),('domestic','223.5.5.5')]:
   require(tcp_reachable(address),'Positive TCP control unavailable')
  report['positive_controls']=True
  guard=start_guard(root);touched=True
  try:
   api(root,'PATCH','/configs',{'tun':{'enable':False}})
   require(api(root,'GET','/configs').get('tun',{}).get('enable') is False,'TUN did not disable')
   disabled=system_state()
   require(disabled['cores']==before['cores'],'Core changed during TUN-off drill')
   require(disabled['tun_up']==0,'TUN adapter remained active')
   report['tun_disabled']=True;report['same_core_running']=True
   for label,address in [('foreign','1.1.1.1'),('domestic','223.5.5.5')]:
    report[label+'_tcp_blocked']=not tcp_reachable(address)
    require(report[label+'_tcp_blocked'],'Public TCP remained reachable with TUN disabled')
   for tcp in (False,True):
    blocked=False
    try:query('8.8.8.8',53,1,tcp,host='example.com')
    except OSError:blocked=True
    report['dns_'+('tcp' if tcp else 'udp')+'_blocked']=blocked
    require(blocked,'Public DNS responded with TUN disabled')
   require(api(root,'GET','/configs').get('tun',{}).get('enable') is False,'TUN re-enabled during drill; evidence inconclusive')
   after=system_state()
   require(after['cores']==before['cores'] and after['tun_up']==0,'Core or TUN changed during drill')
   report['success']=True
  finally:
   if touched:
    try:
     restore_tun(root);report['tun_restored']=True;(guard/'done').touch()
    except Exception:
     report['success']=False;report['restore_failed']=True;raise
 except Exception as exc:
  report['success']=False;report['error']=type(exc).__name__
 finally:
  report=save_report(report,'tun-off')
 return report

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--watch');args=parser.parse_args()
 if args.watch:watch(args.watch)
 else:
  result=verify_tun();print(json.dumps(result,ensure_ascii=False));raise SystemExit(0 if result['success'] else 1)
