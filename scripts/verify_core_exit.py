"""Administrator-only test for the Windows service deployment; always restarts service."""
from checks import require
import json,socket,subprocess,time
from client import LOCAL,root_path,write
from drill_recovery import DrillRecoveryError,guarded_outage
from verify_tun import api
from verification_reports import save_report

def command(text):
 result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',text],
  stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=20)
 require(result.returncode==0,'Core service control failed')
 return result.stdout.strip()
def restore_core(root,current,recovery_timeout=35):
 deadline=time.monotonic()+recovery_timeout
 while True:
  try:
   command('Start-Service clash_verge_service -ErrorAction Stop')
   api(root,'PUT','/configs?force=true',{'payload':current})
   break
  except Exception:
   if time.monotonic()>=deadline:raise TimeoutError('Core configuration recovery timed out') from None
   time.sleep(1)
 # Windows may retain fake-IP answers from the stopped core.
 command('Clear-DnsClientCache -ErrorAction Stop')

def verify_core_exit():
 report={'success':False}
 try:
  plan=json.loads((LOCAL/'plan.json').read_text(encoding='utf-8'));root=root_path(plan['root'])
  command("$s=Get-Service -Name clash_verge_service -ErrorAction Stop; if($s.Status -ne 'Running'){throw 'Expected running Clash Verge service'}")
  current=(root/'clash-verge.yaml').read_text(encoding='utf-8')
  with guarded_outage(root,'core-exit',current):
   require(command('@(Get-Process verge-mihomo -ErrorAction SilentlyContinue).Count')=='0','Core remained alive; protection test is inconclusive')
   report['core_stopped']=True
   for label,address in [('foreign','1.1.1.1'),('domestic','223.5.5.5')]:
    try:
     with socket.create_connection((address,443),timeout=4):pass
    except OSError:report[label+'_public_blocked']=True
    else:raise AssertionError('Public network remained reachable while core was stopped')
   report['domestic_also_blocked']=bool(report.get('domestic_public_blocked'))
   report['scope']='Strict firewall blocks all public destinations while the core is stopped, domestic included. Container, WSL and browser behavior remain unverified.'
   report['success']=True
  report['dns_cache_cleared']=True
 except DrillRecoveryError as exc:
  report['success']=False;report['restart_failed']=True;report['error']=type(exc).__name__
 except Exception as exc:
  report['success']=False;report['error']=type(exc).__name__
 finally:
  write(LOCAL/'core-exit-test.json',report)
  report=save_report(report,'core-exit')
  print(json.dumps(report))
 return report

if __name__=='__main__':
 from operation_lock import operation_lock
 with operation_lock(LOCAL):
  raise SystemExit(0 if verify_core_exit()['success'] else 1)
