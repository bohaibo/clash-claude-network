"""Internal audit coordinator; caller owns the operation lock and elevation."""
import json,os,uuid,subprocess,sys
from pathlib import Path
from checks import require
from client import LOCAL,PROJECT,write
from firewall_audit import verify_firewall
from verify import verify
from verify_tun import verify_tun
from verify_core_exit import verify_core_exit
from operation_lock import check_pending_tun
from verification_reports import save_report

def recheck_ipv6():
 plan=json.loads((LOCAL/'plan.json').read_text(encoding='utf-8'))
 snapshot=Path(plan['snapshot']).resolve()
 require(snapshot.parent==(LOCAL/'snapshots').resolve(),'Invalid recovery snapshot')
 env=dict(os.environ,CCN_SNAPSHOT=str(snapshot))
 result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',
  str(PROJECT/'scripts/system.ps1'),'-Action','recheck-ipv6','-Python',sys.executable],capture_output=True,timeout=60,env=env)
 (LOCAL/'audit-ipv6-recheck.stdout.log').write_bytes(result.stdout)
 (LOCAL/'audit-ipv6-recheck.stderr.log').write_bytes(result.stderr)
 require(result.returncode==0,'IPv6 bindings did not recover')
 write(LOCAL/'audit-ipv6-recheck.json',{'success':True})

def run_audit():
 os.environ['CCN_VERIFY_RUN']=uuid.uuid4().hex
 result={'success':False,'run_id':os.environ['CCN_VERIFY_RUN']}
 try:
  check_pending_tun(LOCAL)
  require(verify_firewall('firewall-before')['success'],'Full firewall preflight not verified; drills stopped')
  try:
   verify(outage=True)
   require(verify_tun()['success'],'TUN-off protection or recovery failed')
   require(verify_core_exit()['success'],'Core-exit protection or recovery failed')
  finally:
   try:recheck_ipv6()
   except Exception:
    save_report({'success':False,'failed_check':'ipv6_bindings'},'post-recovery');raise
  verify(stage='post-recovery')
  require(verify_firewall('firewall-after')['success'],'Final firewall audit not verified')
  result['success']=True
 except Exception as exc:result['error']=type(exc).__name__
 write(LOCAL/'safety-audit-result.json',result)
 print(json.dumps(result,ensure_ascii=False));return result

if __name__=='__main__':raise SystemExit(0 if run_audit()['success'] else 1)
