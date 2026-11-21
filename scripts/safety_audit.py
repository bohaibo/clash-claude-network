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
from gui_job_console import append_event

STAGES = (
 '复核生效防火墙',
 '检查连通性并演练节点故障',
 '演练关闭 TUN',
 '演练内核退出',
 '复查恢复后的 IPv6 绑定',
 '复测恢复后的网络',
 '复核恢复后的防火墙',
)

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
 events=os.environ.get('CCN_EVENTS_FILE')
 completed=0
 def stage(index, operation):
  nonlocal completed
  title=STAGES[index-1]
  def progress(status):
   if events:
    try:
     append_event(events,{'type':'audit-step','index':index,'total':len(STAGES),
                          'completed':completed,'title':title,'status':status})
    except OSError:
     pass
  print(f'[{index}/{len(STAGES)}] {title}…',flush=True)
  progress('running')
  try:
   operation()
  except Exception:
   print(f'[{index}/{len(STAGES)}] {title}未通过。',flush=True)
   progress('failed')
   raise
  completed+=1
  print(f'[{index}/{len(STAGES)}] {title}已完成。',flush=True)
  progress('passed')
 try:
  check_pending_tun(LOCAL)
  # The first firewall report records the revision after the TUN is ready.
  # Subsequent reports verify their revision against that evidence.
  stage(1,lambda:require(verify_firewall('firewall-before')['success'],'Full firewall preflight not verified; drills stopped'))
  try:
   stage(2,lambda:require(verify(outage=True)['success'],'Node outage verification did not pass'))
   stage(3,lambda:require(verify_tun()['success'],'TUN-off protection or recovery failed'))
   stage(4,lambda:require(verify_core_exit()['success'],'Core-exit protection or recovery failed'))
  finally:
   try:stage(5,recheck_ipv6)
   except Exception:
    save_report({'success':False,'failed_check':'ipv6_bindings'},'post-recovery');raise
  stage(6,lambda:require(verify(stage='post-recovery')['success'],'Post-recovery verification did not pass'))
  stage(7,lambda:require(verify_firewall('firewall-after')['success'],'Final firewall audit not verified'))
  result['success']=True
 except Exception as exc:result['error']=type(exc).__name__
 write(LOCAL/'safety-audit-result.json',result)
 print(json.dumps(result,ensure_ascii=False));return result

if __name__=='__main__':raise SystemExit(0 if run_audit()['success'] else 1)
