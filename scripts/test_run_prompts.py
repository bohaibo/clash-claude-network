"""Exercise the real entry in isolated .local fixtures; never touch live settings."""
import json,subprocess,tempfile,unittest
from pathlib import Path
import client


class RunPrompts(unittest.TestCase):
 def setUp(self):
  temp=tempfile.TemporaryDirectory(dir=client.LOCAL,prefix='prompt-test-');self.addCleanup(temp.cleanup)
  self.project=Path(temp.name);self.scripts=self.project/'scripts';self.scripts.mkdir()
  self.local=self.project/'.local';self.local.mkdir()
  source=(client.PROJECT/'scripts/run.ps1').read_text(encoding='utf-8-sig')
  start=source.index('$identity=');end=source.index('# The same byte-range lock',start)
  source=source[:start]+source[end:]
  mocks="""
function Get-Process { return @() }
function Stop-Process { throw 'Unexpected process mutation' }
function Get-ScheduledTask { throw 'Unexpected task access' }
function Start-Process { throw 'Unexpected process start' }
function Get-Service { return $null }
function Assert-FirewallAvailable { return $null }
function Resolve-PendingFirewall { return $null }
$script:answers=New-Object 'System.Collections.Generic.Queue[string]'
$entries=Get-Content -LiteralPath (Join-Path $PSScriptRoot '../answers.json') -Raw | ConvertFrom-Json
foreach($entry in $entries){$script:answers.Enqueue([string]$entry)}
function Read-Host {param($Prompt) if(!$script:answers.Count){throw 'Unexpected input prompt'};$script:answers.Dequeue()}
"""
  first,rest=source.split('\n',1)
  (self.scripts/'run.ps1').write_text(first+'\n'+mocks+rest,encoding='utf-8-sig')
  for name in ('firewall_rules.ps1','operation_guard.ps1'):(self.scripts/name).write_text('# fixture')
  (self.scripts/'test_dns_filter.py').write_text('# isolated self-test fixture')
  self.discover([{'uid':'first','name':'Same','nodes':['Chosen']}])
 def discover(self,rows):
  data=json.dumps({'subscriptions':rows},ensure_ascii=True)
  (self.scripts/'client.py').write_text('import sys\nif sys.argv[1] != "discover": raise RuntimeError("Unexpected mutation")\nprint('+repr(data)+')\n')
 def run_entry(self,action='apply',answers=(),*extra):
  (self.project/'answers.json').write_text(json.dumps(answers))
  command=['powershell.exe','-NoProfile','-File',str(self.scripts/'run.ps1'),'-Action',action,'-NoPause']
  result=subprocess.run(command+list(extra),capture_output=True,encoding='utf-8',timeout=25)
  return result,result.stdout+result.stderr
 def test_missing_node_stops_before_closing_client_without_restore_advice(self):
  result,text=self.run_entry('apply',(),'-Acknowledged','-SkipBrowser','-Node','Missing')
  self.assertEqual(result.returncode,1,text);self.assertIn('没有这个精确节点',text)
  self.assertIn('无需为此执行恢复',text);self.assertNotIn('Unexpected process',text)
  self.assertNotIn('重试之前请先执行',text)
 def test_duplicate_node_stops_without_substituting(self):
  self.discover([{'uid':uid,'name':'Same','nodes':['Chosen']} for uid in ('first','second')])
  result,text=self.run_entry('apply',(),'-Acknowledged','-SkipBrowser','-Node','Chosen')
  self.assertEqual(result.returncode,1,text);self.assertIn('多个订阅',text)
  self.assertNotIn('Unexpected process',text)
 def test_node_name_is_case_sensitive(self):
  result,text=self.run_entry('apply',(),'-Acknowledged','-SkipBrowser','-Node','chosen')
  self.assertEqual(result.returncode,1);self.assertIn('没有这个精确节点',text)
 def test_empty_node_cancels_without_mutation(self):
  result,text=self.run_entry('apply',[''],'-Acknowledged','-SkipBrowser')
  self.assertEqual(result.returncode,0,text);self.assertIn('已取消配置',text)
  self.assertNotIn('Unexpected process',text)
  report=json.loads((self.local/'last-run.json').read_text(encoding='utf-8-sig'))
  self.assertEqual(report['status'],'cancelled');self.assertFalse(report['success'])
 def test_invalid_node_number_reprompts_then_cancels(self):
  result,text=self.run_entry('apply',['1','99',''],'-Acknowledged','-SkipBrowser')
  self.assertEqual(result.returncode,0,text);self.assertIn('请输入 1 到 1',text)
  self.assertIn('已取消配置',text);self.assertNotIn('Unexpected process',text)
 def test_invalid_subscription_number_reprompts_then_cancels(self):
  result,text=self.run_entry('apply',['99',''],'-Acknowledged','-SkipBrowser')
  self.assertEqual(result.returncode,0,text);self.assertIn('订阅编号应为',text)
  self.assertIn('已取消配置',text);self.assertNotIn('Unexpected process',text)
 def test_subscription_selection_passes_unique_uid(self):
  self.discover([{'uid':uid,'name':'Same','nodes':['Chosen']} for uid in ('first','second')])
  # Stop at the first attempted process mutation. Reaching it proves that the
  # duplicate display name was resolved to the selected UID during preflight.
  result,text=self.run_entry('apply',['2','1'],'-Acknowledged','-SkipBrowser')
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
  path=self.scripts/'run.ps1';source=path.read_text(encoding='utf-8-sig')
  source=source.replace("$pythonCommand=Get-Command python.exe -ErrorAction SilentlyContinue",'$pythonCommand=$null')
  path.write_text(source,encoding='utf-8-sig')
  result,text=self.run_entry()
  self.assertEqual(result.returncode,1,text);self.assertIn('未找到 Python',text)
  self.assertTrue((self.local/'last-error.log').exists())
 def test_cancelled_elevation_is_not_reported_as_configuration_failure(self):
  path=self.scripts/'run.ps1';source=path.read_text(encoding='utf-8-sig')
  original=(client.PROJECT/'scripts/run.ps1').read_text(encoding='utf-8-sig')
  start=original.index('$identity=');end=original.index('# The same byte-range lock',start)
  block=original[start:end];block='if($true){\n'+block[block.index('  $arguments='):]
  source=source.replace('# The same byte-range lock',block+'# The same byte-range lock',1)
  source=source.replace("throw 'Unexpected process start'",'throw (New-Object ComponentModel.Win32Exception(1223))')
  path.write_text(source,encoding='utf-8-sig')
  result,text=self.run_entry('snapshot')
  self.assertEqual(result.returncode,0,text);self.assertIn('已取消管理员授权',text)
  self.assertNotIn('操作未完成',text)
 def test_firewall_rollback_outcome_is_explicit(self):
  snapshot=self.local/'snapshots'/'fixture';snapshot.mkdir(parents=True)
  (snapshot/'system.json').write_text('{}')
  plan={'snapshot':str(snapshot),'core_program':'fixture.exe'}
  (self.local/'plan.json').write_text(json.dumps(plan))
  discover=json.dumps({'subscriptions':[{'uid':'first','name':'Same','nodes':['Chosen']}]})
  (self.scripts/'client.py').write_text('import json,sys\nprint('+repr(discover)+' if sys.argv[1] == "discover" else '+repr(json.dumps(plan))+')\n')
  (self.scripts/'system.ps1').write_text('param($Action,$Python)\n')
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
   result,text=self.run_entry('apply',(),'-Acknowledged','-SkipBrowser','-Node','Chosen')
   self.assertEqual(result.returncode,1,text)
   report=json.loads((self.local/'last-run.json').read_text(encoding='utf-8-sig'))
   self.assertEqual(report['firewallRollback'],not fails,text)
   self.assertIn('防火墙回滚失败' if fails else '防火墙回滚已完成',text)
   self.assertIn('重试之前请先执行',text)


if __name__=='__main__':unittest.main()
