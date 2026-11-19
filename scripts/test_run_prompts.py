"""Exercise the real entry in isolated .local fixtures; never touch live settings."""
import base64,json,subprocess,tempfile,time,unittest,sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import client
from gui_job_console import capture


class RunPrompts(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory(dir=client.LOCAL,prefix='prompt-test-');self.addCleanup(temp.cleanup)
  self.project=Path(temp.name);self.scripts=self.project/'scripts';self.scripts.mkdir()
  self.local=self.project/'.local';self.local.mkdir()
  (self.local/'env.json').write_text(json.dumps({'python':sys.executable}))
  source=(client.PROJECT/'scripts/run.ps1').read_text(encoding='utf-8-sig')
  start=source.index('$identity=');end=source.index('$activeJob=',start)
  source=source[:start]+source[end:]
  mocks="""
function Get-Process { return @() }
function Stop-Process { throw 'Unexpected process mutation' }
function Get-ScheduledTask { throw 'Unexpected task access' }
function Start-Process { throw 'Unexpected process start' }
function Get-Service { return $null }
function Assert-FirewallAvailable { return $null }
function Resolve-PendingFirewall { return $null }
function Assert-NoPendingSettings { return $null }
function Assert-NoPendingRules { return $null }
$script:answers=New-Object 'System.Collections.Generic.Queue[string]'
$entries=Get-Content -LiteralPath (Join-Path $PSScriptRoot '../answers.json') -Raw | ConvertFrom-Json
foreach($entry in $entries){$script:answers.Enqueue([string]$entry)}
function Read-Host {param($Prompt) if(!$script:answers.Count){throw 'Unexpected input prompt'};$script:answers.Dequeue()}
"""
  first,rest=source.split('\n',1)
  (self.scripts/'run.ps1').write_text(first+'\n'+mocks+rest,encoding='utf-8-sig')
  for name in ('firewall_rules.ps1','operation_guard.ps1'):(self.scripts/name).write_text('# fixture')
  (self.scripts/'test_dns_filter.py').write_text('# isolated self-test fixture')
  (self.scripts/'operation_lock.py').write_bytes((client.PROJECT/'scripts/operation_lock.py').read_bytes())
  self.discover([{'uid':'first','name':'Same','nodes':['Chosen']}])
 def discover(self,rows):
  data=json.dumps({'subscriptions':rows},ensure_ascii=True)
  (self.scripts/'client.py').write_text('import sys,json\nif sys.argv[1] == "prefetch-rules": print(json.dumps({"rules_bundle":"a"*32}));sys.exit(0)\nif sys.argv[1] != "discover": raise RuntimeError("Unexpected mutation")\nprint('+repr(data)+')\n')
 def run_entry(self,action='apply',answers=(),*extra):
  (self.project/'answers.json').write_text(json.dumps(answers))
  command=['powershell.exe','-NoProfile','-File',str(self.scripts/'run.ps1'),'-Action',action,'-NoPause']
  result=subprocess.run(command+list(extra),capture_output=True,encoding='utf-8',timeout=25)
  return result,result.stdout+result.stderr
 def test_pending_settings_blocks_real_console_guard_before_configuration(self):
  script=self.scripts/'run.ps1'
  script.write_text(script.read_text(encoding='utf-8-sig').replace('function Assert-NoPendingSettings { return $null }',''),encoding='utf-8-sig')
  (self.scripts/'operation_guard.ps1').write_text((client.PROJECT/'scripts/operation_guard.ps1').read_text(encoding='utf-8-sig'),encoding='utf-8-sig')
  folder=self.local/'gui/settings-transactions'/('a'*32);folder.mkdir(parents=True)
  (folder/'transaction.json').write_text('{"phase":"applying"}',encoding='utf-8')
  result,text=self.run_entry('apply',(),'-Acknowledged','-Node','Missing')
  self.assertNotEqual(result.returncode,0)
  self.assertIn('未完成的设置保存',text)
  self.assertNotIn('Unexpected process',text)
 def test_missing_node_stops_before_closing_client_without_restore_advice(self):
  result,text=self.run_entry('apply',(),'-Acknowledged','-Node','Missing')
  self.assertEqual(result.returncode,1,text);self.assertIn('没有这个精确节点',text)
  self.assertIn('无需为此执行恢复',text);self.assertNotIn('Unexpected process',text)
  self.assertNotIn('重试之前请先执行',text)

 def test_pending_rule_recovery_blocks_real_console_entry(self):
  script=self.scripts/'run.ps1'
  script.write_text(script.read_text(encoding='utf-8-sig').replace('function Assert-NoPendingRules { return $null }',''),encoding='utf-8-sig')
  (self.scripts/'operation_guard.ps1').write_text((client.PROJECT/'scripts/operation_guard.ps1').read_text(encoding='utf-8-sig'),encoding='utf-8-sig')
  (self.local/'rule-update-pending.json').write_text('{broken',encoding='utf-8')
  for action in ('apply','supplement','restore','snapshot','audit'):
   with self.subTest(action=action):
    result,text=self.run_entry(action,(),'-Acknowledged','-Snapshot','fixture')
    self.assertNotEqual(result.returncode,0);self.assertIn('规则更新恢复尚未完成',text)
    self.assertNotIn('Unexpected',text)
 def test_events_record_progress_and_failure_without_browser_steps(self):
  events=self.local/'events.jsonl'
  result,text=self.run_entry('apply',(),'-Acknowledged','-Node','Missing','-EventsFile',str(events))
  self.assertEqual(result.returncode,1,text)
  rows=[json.loads(line) for line in events.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
  types=[row.get('type') for row in rows]
  self.assertIn('start',types);self.assertIn('step',types);self.assertIn('error',types)
  first_step=next(row for row in rows if row.get('type')=='step')
  self.assertEqual(first_step.get('total'),12)
  self.assertTrue(all('浏览器' not in str(row.get('title','')) for row in rows))
 def test_duplicate_node_stops_without_substituting(self):
  self.discover([{'uid':uid,'name':'Same','nodes':['Chosen']} for uid in ('first','second')])
  result,text=self.run_entry('apply',(),'-Acknowledged','-Node','Chosen')
  self.assertEqual(result.returncode,1,text);self.assertIn('多个订阅',text)
  self.assertNotIn('Unexpected process',text)
 def test_new_single_static_preflights_without_subscription_discovery(self):
  spec=self.local/'static.json';spec.write_text('{}')
  seen=self.local/'preflight.json'
  (self.scripts/'client.py').write_text(
   'import pathlib,sys,json\nif sys.argv[1] == "chain-preflight":\n'
   f' pathlib.Path({str(seen)!r}).write_text(json.dumps(sys.argv[2:]));sys.exit(0)\n'
   'sys.exit(1)\n')
  result,text=self.run_entry('apply',(),'-Acknowledged','-Node','Static test','-ChainSpecPath',str(spec))
  self.assertEqual(result.returncode,1,text)
  self.assertEqual(json.loads(seen.read_text()),['--chain-spec',str(spec),'--node','Static test'])
  self.assertIn('规则准备失败',text)
  self.assertNotIn('Unexpected process',text)
 def test_download_failure_keeps_client_open_and_records_failed_step(self):
  (self.scripts/'client.py').write_text('import sys,json\nif sys.argv[1] == "prefetch-rules": sys.exit(1)\nprint(json.dumps({"subscriptions":[{"uid":"first","name":"Same","nodes":["Chosen"]}]}))\n')
  result,text=self.run_entry('apply',(),'-Acknowledged','-Node','Chosen')
  self.assertEqual(result.returncode,1,text)
  self.assertIn('未关闭 Clash',text)
  self.assertNotIn('Unexpected process',text)
  rows=[json.loads(line) for path in (self.local/'performance').glob('*.steps.jsonl') for line in path.read_text(encoding='utf-8').splitlines()]
  self.assertEqual([(row['stage'],row['status']) for row in rows],[('step.1','passed'),('step.2','passed'),('step.3','failed')])
 def test_missing_supplement_plan_records_failed_timing_without_system_writes(self):
  result,text=self.run_entry('supplement',(),'-Acknowledged')
  self.assertEqual(result.returncode,1,text)
  self.assertNotIn('Unexpected process',text)
  rows=[json.loads(line) for path in (self.local/'performance').glob('*.steps.jsonl') for line in path.read_text(encoding='utf-8').splitlines()]
  self.assertEqual([(row['stage'],row['status']) for row in rows],[('step.1','failed')])
 def test_node_name_is_case_sensitive(self):
  result,text=self.run_entry('apply',(),'-Acknowledged','-Node','chosen')
  self.assertEqual(result.returncode,1);self.assertIn('没有这个精确节点',text)
 def test_empty_node_cancels_without_mutation(self):
  result,text=self.run_entry('apply',[''],'-Acknowledged')
  self.assertEqual(result.returncode,0,text);self.assertIn('已取消配置',text)
  self.assertNotIn('Unexpected process',text)
  report=json.loads((self.local/'last-run.json').read_text(encoding='utf-8-sig'))
  self.assertEqual(report['status'],'cancelled');self.assertFalse(report['success'])
 def test_invalid_node_number_reprompts_then_cancels(self):
  result,text=self.run_entry('apply',['1','99',''],'-Acknowledged')
  self.assertEqual(result.returncode,0,text);self.assertIn('请输入 1 到 1',text)
  self.assertIn('已取消配置',text);self.assertNotIn('Unexpected process',text)
 def test_invalid_subscription_number_reprompts_then_cancels(self):
  result,text=self.run_entry('apply',['99',''],'-Acknowledged')
  self.assertEqual(result.returncode,0,text);self.assertIn('订阅编号应为',text)
  self.assertIn('已取消配置',text);self.assertNotIn('Unexpected process',text)
 def test_subscription_selection_passes_unique_uid(self):
  self.discover([{'uid':uid,'name':'Same','nodes':['Chosen']} for uid in ('first','second')])
  # Stop at the first attempted process mutation. Reaching it proves that the
  # duplicate display name was resolved to the selected UID during preflight.
  result,text=self.run_entry('apply',['2','1'],'-Acknowledged')
  self.assertEqual(result.returncode,1,text);self.assertIn('Unexpected process mutation',text)
  self.assertNotIn('此节点在多个订阅',text)
 def test_restore_number_reprompts_and_can_cancel(self):
  for name in ('earlier','later'):
   folder=self.local/'snapshots'/name;folder.mkdir(parents=True)
   (folder/'manifest.json').write_text(json.dumps({'computer':'fixture','user_sid':'fixture','client_root':'fixture','sha256':{}}))
  result,text=self.run_entry('restore',['bad','3',''])
  self.assertEqual(result.returncode,0,text);self.assertIn('已取消恢复',text)
  self.assertNotIn('Unexpected',text)
 def test_snapshot_failure_has_local_log_and_never_claims_success(self):
  (self.scripts/'client.py').write_text('raise SystemExit(1)\n')
  result,text=self.run_entry('snapshot')
  self.assertEqual(result.returncode,1,text);self.assertIn('快照未保存完整',text)
  self.assertNotIn('快照已保存',text)
  self.assertTrue((self.local/'last-error.log').exists())
  self.assertFalse(json.loads((self.local/'last-run.json').read_text(encoding='utf-8-sig'))['success'])
 def test_missing_python_has_readable_message(self):
  (self.local/'env.json').unlink()
  path=self.scripts/'run.ps1';source=path.read_text(encoding='utf-8-sig')
  source=source.replace("$pythonCommand=Get-Command python.exe -ErrorAction SilentlyContinue",'$pythonCommand=$null')
  path.write_text(source,encoding='utf-8-sig')
  result,text=self.run_entry()
  self.assertEqual(result.returncode,1,text);self.assertIn('未找到 Python',text)
  self.assertTrue((self.local/'last-error.log').exists())
 def test_cancelled_elevation_is_not_reported_as_configuration_failure(self):
  path=self.scripts/'run.ps1';source=path.read_text(encoding='utf-8-sig')
  original=(client.PROJECT/'scripts/run.ps1').read_text(encoding='utf-8-sig')
  start=original.index('$identity=');end=original.index('$activeJob=',start)
  block=original[start:end];block='if($true){\n'+block[block.index('  $arguments='):]
  source=source.replace('$activeJob=',block+'$activeJob=',1)
  source=source.replace("throw 'Unexpected process start'",'throw (New-Object ComponentModel.Win32Exception(1223))')
  path.write_text(source,encoding='utf-8-sig')
  result,text=self.run_entry('snapshot')
  self.assertEqual(result.returncode,0,text);self.assertIn('已取消管理员授权',text)
  self.assertNotIn('操作未完成',text)
 def test_elevated_console_capture_preserves_output_streams_and_exit_code(self):
  script=self.scripts/'output fixture.ps1'
  script.write_text(
   "param([string]$Node,[string]$Release)\n"
   "[Console]::OutputEncoding=[Text.Encoding]::UTF8\n"
   "Write-Host ('实际输出：'+$Node)\n"
   "$deadline=[DateTime]::UtcNow.AddSeconds(8)\n"
   "while(!(Test-Path -LiteralPath $Release)){\n"
   " if([DateTime]::UtcNow -gt $deadline){exit 9}\n"
   " Start-Sleep -Milliseconds 30\n"
   "}\n"
   "Write-Error '错误流输出' -ErrorAction Continue\n"
   "exit 7\n",encoding='utf-8-sig')
  events=self.local/'events.jsonl'
  release=self.local/'output.release'
  log=self.local/'elevated.log'
  payload={'events':str(events),'arguments':['-Node','测试 节点','-Release',str(release)]}
  with ThreadPoolExecutor(max_workers=1) as executor:
   job=executor.submit(capture,payload,script)
   try:
    deadline=time.monotonic()+5
    while time.monotonic()<deadline and not job.done():
     if log.exists() and '实际输出：测试 节点' in log.read_text(encoding='utf-8',errors='replace'):
      break
     time.sleep(.03)
    else:self.fail('Expected console output before task completion')
   finally:release.touch()
   self.assertEqual(job.result(timeout=5),7)
  output=log.read_text(encoding='utf-8')
  self.assertIn('实际输出：测试 节点',output)
  self.assertIn('错误流输出',output)
 def test_gui_elevation_routes_to_console_capture(self):
  path=self.scripts/'run.ps1';source=path.read_text(encoding='utf-8-sig')
  original=(client.PROJECT/'scripts/run.ps1').read_text(encoding='utf-8-sig')
  start=original.index('$identity=');end=original.index('$activeJob=',start)
  block=original[start:end]
  source=source.replace('$activeJob=','if($true){\n'+block[block.index('  $arguments='):]+'$activeJob=',1)
  source=source.replace("function Start-Process { throw 'Unexpected process start' }", """
function Start-Process {
 param($FilePath,$Verb,$ArgumentList,$WindowStyle,[switch]$PassThru)
 @{program=$FilePath;verb=$Verb;arguments=$ArgumentList} | ConvertTo-Json -Compress |
  Set-Content -LiteralPath (Join-Path $PSScriptRoot '../launch.json') -Encoding UTF8
 $process=[pscustomobject]@{Handle=1;ExitCode=0}
 $process | Add-Member ScriptMethod WaitForExit {}
 return $process
}
""")
  path.write_text(source,encoding='utf-8-sig')
  events=self.local/'events.jsonl'
  result,text=self.run_entry('apply',(),'-Acknowledged','-Node','测试 节点','-EventsFile',str(events))
  self.assertEqual(result.returncode,0,text)
  launch=json.loads((self.project/'launch.json').read_text(encoding='utf-8-sig'))
  self.assertEqual(launch['verb'],'RunAs')
  self.assertEqual(Path(launch['program']),Path(sys.executable))
  self.assertTrue(launch['arguments'][0].endswith('gui_job_console.py"'))
  payload=json.loads(base64.b64decode(launch['arguments'][1]))
  self.assertEqual(Path(payload['events']),events)
  self.assertIn('测试 节点',payload['arguments'])
  self.assertIn('-Acknowledged',payload['arguments'])
 def test_firewall_rollback_outcome_is_explicit(self):
  snapshot=self.local/'snapshots'/'fixture';snapshot.mkdir(parents=True)
  (snapshot/'system.json').write_text('{}')
  plan={'snapshot':str(snapshot),'core_program':'fixture.exe'}
  (self.local/'plan.json').write_text(json.dumps(plan))
  discover=json.dumps({'subscriptions':[{'uid':'first','name':'Same','nodes':['Chosen']}]})
  (self.scripts/'client.py').write_text('import json,sys\nif sys.argv[1] == "prefetch-rules": print(json.dumps({"rules_bundle":"a"*32}));sys.exit(0)\nprint('+repr(discover)+' if sys.argv[1] == "discover" else '+repr(json.dumps(plan))+')\n')
  (self.scripts/'system.ps1').write_text('param($Action,$Python,$TimeZoneId,$Culture,$LanguageTag,$FallbackLanguageTag)\n')
  (self.scripts/'verify.py').write_text('raise SystemExit(1)\n')
  path=self.scripts/'run.ps1';source=path.read_text(encoding='utf-8-sig')
  source=source.replace("throw 'Unexpected process mutation'",'return')
  source=source.replace("throw 'Unexpected process start'",'return')
  source=source.replace('function Assert-FirewallAvailable', 'function Assert-StrictFirewall { return }\nfunction Assert-FirewallAvailable')
  for fails in (False,True):
   runner=source
   if fails:
    runner=runner.replace('function Resolve-PendingFirewall { return $null }',"function Resolve-PendingFirewall {param($LocalDir,$Python,$ExpectedToken,[switch]$Force) if($Force){throw 'fixture rollback failure'} }")
   path.write_text(runner,encoding='utf-8-sig')
   result,text=self.run_entry('apply',(),'-Acknowledged','-Node','Chosen')
   self.assertEqual(result.returncode,1,text)
   report=json.loads((self.local/'last-run.json').read_text(encoding='utf-8-sig'))
   self.assertEqual(report['firewallRollback'],not fails,text)
   self.assertIn('防火墙回滚失败' if fails else '防火墙回滚已完成',text)
   self.assertIn('重试之前请先在',text)

 def test_apply_runs_audit_after_committing_and_keeps_configuration_on_audit_failure(self):
  snapshot=self.local/'snapshots'/'fixture';snapshot.mkdir(parents=True)
  plan={'snapshot':str(snapshot),'core_program':'fixture.exe','node':'Chosen','root':'fixture'}
  (self.local/'plan.json').write_text(json.dumps(plan))
  discover={'subscriptions':[{'uid':'first','name':'Same','nodes':['Chosen']}]}
  (self.scripts/'client.py').write_text(f"""import json, sys
from pathlib import Path
LOCAL = Path(__file__).resolve().parents[1] / '.local'
GROUP = 'fixture'
load = lambda path: json.loads(path.read_text(encoding='utf-8-sig'))
root_path = lambda root: root
def api(root, method, path):
    return {{'proxies': {{GROUP: {{'all': ['Chosen'], 'now': 'Chosen'}}}}}}
if __name__ == '__main__':
    command = sys.argv[1]
    if command == 'discover': print(json.dumps({discover!r}))
    elif command == 'prefetch-rules': print(json.dumps({{'rules_bundle': 'a'*32}}))
    elif command == 'prepare': print(json.dumps({plan!r}))
    else: assert command in ('seal', 'apply-client'), command
""",encoding='utf-8')
  (self.scripts/'config_health.py').write_text('def assert_live(*args): pass\n')
  (self.scripts/'system.ps1').write_text('param($Action,$Python,$TimeZoneId,$RegionCode,$CultureName)\n')
  (self.scripts/'update_task.ps1').write_text('param($Action,$Python,$At,$DaysInterval)\n')
  (self.scripts/'webrtc_system.py').write_text('print(\'{"installation_pending": [], "manual_fallback_available": false}\')\n')
  path=self.scripts/'run.ps1';source=path.read_text(encoding='utf-8-sig')
  source=source.replace("throw 'Unexpected process mutation'",'return')
  source=source.replace("throw 'Unexpected process start'",'return')
  bypass="if($Force){throw 'Unexpected firewall rollback'}"
  source=source.replace('function Resolve-PendingFirewall { return $null }',
                        "function Resolve-PendingFirewall {param($LocalDir,$Python,$ExpectedToken,[switch]$Force) "+bypass+"}")
  source=source.replace('function Assert-FirewallAvailable', 'function Assert-StrictFirewall { return }\nfunction Assert-FirewallAvailable')
  path.write_text(source,encoding='utf-8-sig')
  for exit_code in (0,1):
   with self.subTest(audit_exit=exit_code):
    seen=self.local/'audit-ran.txt';seen.unlink(missing_ok=True)
    (self.scripts/'safety_audit.py').write_text(f"""from client import LOCAL, load
configuration = load(LOCAL / 'configuration-applied.json')
result = load(LOCAL / 'last-run.json')
assert result['success'] is True and result['network_audit'] == 'pending'
assert result['configuration_id'] == configuration['configuration_id']
assert any(LOCAL.glob('guard-*.commit'))
(LOCAL / 'audit-ran.txt').write_text('ran')
raise SystemExit({exit_code})
""")
    events=self.local/'audit-events.jsonl';events.unlink(missing_ok=True)
    result,text=self.run_entry('apply',(),'-Acknowledged','-Node','Chosen','-EventsFile',str(events))
    self.assertEqual(result.returncode,exit_code,text)
    self.assertTrue(seen.exists(),text)
    report=json.loads((self.local/'last-run.json').read_text(encoding='utf-8-sig'))
    self.assertTrue(report['success'])
    self.assertEqual(report['network_audit'],'failed' if exit_code else 'passed')
    self.assertEqual(report['network_tested'],not exit_code)
    rows=[json.loads(line) for line in events.read_text(encoding='utf-8-sig').splitlines()]
    self.assertEqual([row['title'] for row in rows if row['type']=='step'][-1],'完整网络验收')
    self.assertEqual(rows[-1]['type'],'error' if exit_code else 'done')


if __name__=='__main__':unittest.main()
