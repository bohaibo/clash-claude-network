"""Full firewall evidence; missing permission never counts as a pass."""
import json,subprocess
from client import LOCAL,PROJECT
from verification_reports import save_report

def verify_firewall(stage='firewall-before'):
 report={'success':False,'full_verified':False,'status':'unverified','evidence_kind':'firewall-audit'}
 try:
  completed=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',
   str(PROJECT/'scripts/firewall_audit.ps1'),'-PlanFile',str(LOCAL/'plan.json')],capture_output=True,timeout=90)
  parsed=json.loads(completed.stdout.decode('utf-8-sig').strip().splitlines()[-1])
  report.update(parsed)
  report['success']=completed.returncode==0 and parsed.get('success') is True and parsed.get('full_verified') is True and parsed.get('administrator') is True
  report['full_verified']=report['success']
  if not report['success'] and report.get('status')=='passed':report['status']='unverified'
 except Exception as exc:report['error_type']=type(exc).__name__
 return save_report(report,stage)
