"""Administrator-only test for the Windows service deployment; always restarts service."""
import json,socket,subprocess,time
from client import LOCAL,root_path,api

plan=json.loads((LOCAL/'plan.json').read_text());root=root_path(plan['root'])
def command(text):
 result=subprocess.run(['powershell.exe','-NoProfile','-Command',text],capture_output=True,text=True)
 assert result.returncode==0,'Core service control failed'
 return result.stdout.strip()
command("$s=Get-Service -Name clash_verge_service -ErrorAction Stop; if($s.Status -ne 'Running'){throw 'Expected running Clash Verge service'}")
report={'success':False}
current=(root/'clash-verge.yaml').read_text(encoding='utf-8')
try:
 command('Stop-Service clash_verge_service -Force -ErrorAction Stop')
 assert command('@(Get-Process verge-mihomo -ErrorAction SilentlyContinue).Count')=='0','Core remained alive; protection test is inconclusive'
 report['core_stopped']=True
 for label,address in [('foreign','1.1.1.1'),('domestic','223.5.5.5')]:
  try:
   with socket.create_connection((address,443),timeout=4):pass
  except OSError:report[label+'_public_blocked']=True
  else:raise AssertionError('Public network remained reachable while core was stopped')
 report['success']=True
finally:
 command('Start-Service clash_verge_service -ErrorAction Stop')
 deadline=time.monotonic()+35
 while True:
  try:api(root,'PUT','/configs?force=true',{'payload':current});break
  except Exception:
   if time.monotonic()>deadline:report['success']=False;report['restart_failed']=True;break
   time.sleep(1)
 (LOCAL/'core-exit-test.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
 print(json.dumps(report))
 if not report['success']:raise SystemExit(1)
