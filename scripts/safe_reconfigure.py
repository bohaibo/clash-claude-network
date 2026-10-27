"""Detached, bounded restore/apply supervision. All job data stays under .local/."""
import argparse,ctypes,json,os,subprocess,sys,time,uuid
from pathlib import Path
import baseline,client

def save(folder,name,value):client.write(folder/name,value)

def process_birth(pid):
 if os.name!='nt':raise RuntimeError('Windows only')
 from ctypes import wintypes
 kernel=ctypes.WinDLL('kernel32',use_last_error=True)
 kernel.OpenProcess.restype=wintypes.HANDLE
 kernel.GetProcessTimes.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
 kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
 kernel.CloseHandle.argtypes=[wintypes.HANDLE]
 handle=kernel.OpenProcess(0x1000,False,int(pid))
 if not handle:return None
 try:
  code=wintypes.DWORD()
  if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)) or code.value!=259:return None
  times=[wintypes.FILETIME() for _ in range(4)]
  if not kernel.GetProcessTimes(handle,*(ctypes.byref(t) for t in times)):return None
  return (times[0].dwHighDateTime<<32)|times[0].dwLowDateTime
 finally:kernel.CloseHandle(handle)

def stop_worker(folder):
 record=folder/'worker.json'
 if not record.exists():return
 worker=json.loads(record.read_text(encoding='utf-8'))
 if process_birth(worker['pid'])!=worker['birth']:return
 result=subprocess.run(['taskkill.exe','/PID',str(worker['pid']),'/T','/F'],capture_output=True,timeout=15)
 if result.returncode and process_birth(worker['pid'])==worker['birth']:
  raise RuntimeError('Cannot stop owned worker; recovery must not race system writes')

def load_job(folder):
 folder=Path(folder).resolve()
 if folder.parent!=(client.LOCAL/'reconfigure-runs').resolve():raise ValueError('Job outside .local')
 job=json.loads((folder/'job.json').read_text(encoding='utf-8'))
 if job['id']!=folder.name or len(job['id'])!=32:raise ValueError('Invalid job ID')
 if Path(job['snapshot']).resolve().parent!=(client.LOCAL/'snapshots').resolve():raise ValueError('Invalid snapshot')
 return folder,job

def command(job,action,skip_browser=False):
 args=['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(client.PROJECT/'scripts/run.ps1'),
       '-Action',action,'-Acknowledged','-NoPause']
 if action=='restore':args+=['-Snapshot',Path(job['snapshot']).name]
 else:args+=['-Node',job['node'],'-Subscription',job['subscription_uid'],'-Region',job['region']]
 if skip_browser or job.get('skip_browser'):args+=['-SkipBrowser']
 return args

def run_stage(folder,job,action,timeout):
 # The child waits for its PID/birth record before it can modify the machine.
 gate=folder/(action+'.go');gate.unlink(missing_ok=True)
 env=dict(os.environ,CCN_RECONFIGURE_JOB=job['id'])
 with (folder/(action+'.log')).open('wb') as output:
  child=subprocess.Popen([sys.executable,__file__,'--worker',str(folder),'--action',action],
                         stdout=output,stderr=subprocess.STDOUT,env=env)
  birth=process_birth(child.pid)
  if birth is None:raise RuntimeError('Worker could not start')
  save(folder,'worker.json',{'pid':child.pid,'birth':birth,'action':action})
  gate.write_text('ready',encoding='utf-8')
  try:code=child.wait(timeout=timeout)
  except subprocess.TimeoutExpired:
   stop_worker(folder);child.wait(timeout=15)
   raise TimeoutError(action+' exceeded its local deadline') from None
  if code:raise RuntimeError(action+' failed; see '+action+'.log')

def network_ready():
 try:
  probe=subprocess.run(['curl.exe','--noproxy','*','--connect-timeout','4','--max-time','12','-sS','-o','NUL',
                        '-w','%{http_code}','https://www.cloudflare.com/cdn-cgi/trace'],
                       capture_output=True,timeout=15)
  return probe.returncode==0 and probe.stdout.strip()==b'200'
 except (OSError,subprocess.TimeoutExpired):return False

def restart_current_runtime(job):
 # Reload only today's native generated file, never a snapshot's node YAML.
 result=subprocess.run(['powershell.exe','-NoProfile','-Command',
                        'Start-Service clash_verge_service -ErrorAction Stop'],capture_output=True,timeout=30)
 if result.returncode:raise RuntimeError('Core restart failed')
 root=client.root_path(job['root'])
 config=client.load(root/'clash-verge.yaml')
 if config.get('mode')!='rule':raise ValueError('Current runtime is not in rule mode')
 if any(g.get('name')==client.GROUP for g in config.get('proxy-groups',[])):
  item=client.select(root,job['node'],job['subscription_uid'])
  client.validate(config,client.load(client.safe_file(root,'profiles/'+item['file'])),job['node'])
 deadline=time.monotonic()+35
 while True:
  try:
   client.api(root,'PUT','/configs?force=true',{'payload':(root/'clash-verge.yaml').read_text(encoding='utf-8')})
   return
  except Exception:
   if time.monotonic()>=deadline:raise RuntimeError('Current runtime recovery failed') from None
   time.sleep(1)

def bounded_restart(folder,job):
 # Named-pipe I/O can block even when its caller has a Python deadline. Keep
 # it in an owned process whose entire tree the guardian can actually stop.
 with (folder/'runtime-recovery.log').open('ab') as output:
  child=subprocess.Popen([sys.executable,__file__,'--restart',str(folder)],stdout=output,stderr=subprocess.STDOUT)
  save(folder,'worker.json',{'pid':child.pid,'birth':process_birth(child.pid),'action':'runtime-recovery'})
  try:code=child.wait(timeout=80)
  except subprocess.TimeoutExpired:
   stop_worker(folder);child.wait(timeout=15)
   raise RuntimeError('Runtime recovery exceeded its independent deadline') from None
  if code:raise RuntimeError('Current runtime recovery failed; inspect runtime-recovery.log')

def recover(folder,job):
 stop_worker(folder)
 env=dict(os.environ,CCN_SNAPSHOT=job['snapshot'],CCN_RECONFIGURE_JOB=job['id'])
 restored=False
 with (folder/'recovery.log').open('ab') as output:
  # Reuse the normal, subscription-preserving restore. A second independent
  # timeout bounds this recovery worker as well.
  child=subprocess.Popen(command(job,'restore',True),stdout=output,stderr=subprocess.STDOUT,env=env)
  save(folder,'worker.json',{'pid':child.pid,'birth':process_birth(child.pid),'action':'recovery'})
  try:restored=child.wait(timeout=300)==0
  except subprocess.TimeoutExpired:
   birth=process_birth(child.pid)
   if birth:
    save(folder,'worker.json',{'pid':child.pid,'birth':birth,'action':'recovery'})
    stop_worker(folder);child.wait(timeout=15)
  # A cosmetic/client error must not prevent emergency firewall/service repair.
  if not restored:
   emergency=subprocess.Popen(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',
                               str(client.PROJECT/'scripts/emergency_network.ps1'),'-Python',sys.executable],
                              env=env,stdout=output,stderr=subprocess.STDOUT)
   save(folder,'worker.json',{'pid':emergency.pid,'birth':process_birth(emergency.pid),'action':'emergency'})
   try:code=emergency.wait(timeout=120)
   except subprocess.TimeoutExpired:
    stop_worker(folder);emergency.wait(timeout=15);raise RuntimeError('Emergency firewall restore timed out') from None
   if code:raise RuntimeError('Emergency firewall restore failed')
 bounded_restart(folder,job)
 return {'network_ready':network_ready(),'full_restore':restored,'browser_restored':False}

def guard(folder):
 folder,job=load_job(folder)
 os.environ['CCN_SNAPSHOT']=job['snapshot'];baseline.verify()
 save(folder,'guard-ready.json',{'ready':True,'pid':os.getpid()})
 deadline=time.monotonic()+1800
 while not (folder/'done.json').exists():
  if ((folder/'recover.request').exists() or time.monotonic()>=deadline
      or process_birth(job['parent_pid'])!=job['parent_birth']):
   try:
    result=recover(folder,job);result['success']=result['network_ready']
   except Exception as exc:
    import traceback
    (folder/'recovery-error.log').write_text(traceback.format_exc(),encoding='utf-8')
    result={'success':False,'error':type(exc).__name__}
   save(folder,'recovery-result.json',result)
   if result['success']:
    save(folder,'done.json',{'success':False,'recovered':True})
    (client.LOCAL/'reconfigure-active.json').unlink(missing_ok=True)
   return 0 if result['success'] else 1
  time.sleep(1)
 return 0

def worker(folder,action):
 folder,job=load_job(folder);deadline=time.monotonic()+20
 while not (folder/(action+'.go')).exists():
  if time.monotonic()>=deadline:return 1
  time.sleep(.1)
 return subprocess.call(command(job,action))

def reconfigure(skip_browser=False):
 if os.name!='nt' or not ctypes.windll.shell32.IsUserAnAdmin():raise RuntimeError('Administrator required')
 active=client.LOCAL/'reconfigure-active.json'
 if active.exists():raise RuntimeError('Another reconfigure/recovery is unresolved; inspect its local job before retrying')
 plan=json.loads((client.LOCAL/'plan.json').read_text(encoding='utf-8'))
 path=baseline.default_snapshot(client.LOCAL)
 if path is None:raise RuntimeError('No original baseline; run initial configuration first')
 os.environ['CCN_SNAPSHOT']=str(path)
 baseline.preflight_restore();baseline.pin_baseline(client.LOCAL,path)
 client.select(client.root_path(plan['root']),plan['node'],plan['subscription_uid'])
 if not network_ready():raise RuntimeError('Pre-operation network probe failed; no settings changed')
 folder=client.LOCAL/'reconfigure-runs'/uuid.uuid4().hex;folder.mkdir(parents=True)
 job={k:plan[k] for k in ('node','subscription_uid','region','root')}
 job.update(id=folder.name,snapshot=str(path.resolve()),parent_pid=os.getpid(),parent_birth=process_birth(os.getpid()),skip_browser=bool(skip_browser))
 previous=client.LOCAL/'verification.json'
 previous_run=json.loads(previous.read_text(encoding='utf-8')).get('run_id') if previous.exists() else None
 save(folder,'job.json',job)
 with active.open('x',encoding='utf-8') as output:json.dump({'id':folder.name},output)
 try:
  with (folder/'guard.log').open('wb') as output:
   watcher=subprocess.Popen([sys.executable,__file__,'--guard',str(folder)],stdout=output,stderr=subprocess.STDOUT,
                            creationflags=subprocess.DETACHED_PROCESS|subprocess.CREATE_NEW_PROCESS_GROUP)
  ready=time.monotonic()+20
  while not (folder/'guard-ready.json').exists():
   if watcher.poll() is not None or time.monotonic()>=ready:raise RuntimeError('Independent recovery guard did not start; no system changes allowed')
   time.sleep(.2)
  print('独立网络看护已就绪；即使会话断开，本机仍会继续执行并在失败或超时后恢复网络。记录：'+str(folder),flush=True)
  save(folder,'status.json',{'stage':'restore','success':False})
  run_stage(folder,job,'restore',600)
  if not network_ready():raise RuntimeError('Original baseline connectivity was not confirmed')
  save(folder,'baseline-restored.json',{'success':True,'network_ready':True})
  print('已恢复最早记录并确认联网，开始重新配置。',flush=True)
  save(folder,'status.json',{'stage':'apply','success':False})
  run_stage(folder,job,'apply',900)
  report=json.loads((client.LOCAL/'verification.json').read_text(encoding='utf-8'))
  if (report.get('success') is not True or report.get('run_id')==previous_run
      or not {'node-outage','core-exit','post-recovery'}<=set(report.get('stages',{}))):
   raise RuntimeError('Fresh complete verification evidence is missing')
  baseline.verify()
  save(folder,'status.json',{'stage':'complete','success':True,'verification_run':report['run_id']})
  save(folder,'done.json',{'success':True});active.unlink(missing_ok=True)
  print('恢复、重新配置及断网恢复验收已完成。记录：'+str(folder),flush=True)
  return 0
 except Exception as exc:
  save(folder,'status.json',{'stage':'failed','success':False,'error':str(exc)})
  if not (folder/'guard-ready.json').exists():
   save(folder,'done.json',{'success':False,'system_changed':False});active.unlink(missing_ok=True)
   raise
  (folder/'recover.request').write_text('recover',encoding='utf-8')
  print('本次流程未完成，独立看护正在恢复网络；请保留本地记录。',flush=True)
  deadline=time.monotonic()+600
  while not (folder/'recovery-result.json').exists() and time.monotonic()<deadline:time.sleep(1)
  if (folder/'recovery-result.json').exists():
   result=json.loads((folder/'recovery-result.json').read_text(encoding='utf-8'))
   print('网络已恢复，可继续排查。' if result.get('success') else '自动恢复未确认成功，请从根菜单恢复原始快照。',flush=True)
  return 1

if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--guard');parser.add_argument('--worker');parser.add_argument('--restart');parser.add_argument('--skip-browser',action='store_true');parser.add_argument('--action',choices=('restore','apply'));args=parser.parse_args()
 try:
  code=guard(args.guard) if args.guard else worker(args.worker,args.action) if args.worker else restart_current_runtime(load_job(args.restart)[1]) if args.restart else reconfigure(args.skip_browser)
 except Exception as exc:
  import traceback
  (client.LOCAL/'reconfigure-error.log').write_text(traceback.format_exc(),encoding='utf-8')
  print('安全重新配置未完成；详情见 .local/reconfigure-error.log。',file=sys.stderr);code=1
 sys.exit(code)
