param([ValidateSet('apply','restore','snapshot','list-snapshots')][string]$Action,[string]$Node,[string]$Subscription,[string]$Region='SG',[string]$Snapshot,[switch]$Acknowledged,[switch]$NoPause,[switch]$SkipBrowser)
$ErrorActionPreference='Stop'
$project=Split-Path -Parent $PSScriptRoot
$local=Join-Path $project '.local';New-Item -Path $local -ItemType Directory -Force | Out-Null
$python=(Get-Command python.exe -ErrorAction Stop).Source
if($python -like '*\Microsoft\WindowsApps\*'){throw 'Windows Store Python alias detected. Use an installed Python runtime; no software will be installed automatically.'}
& $python -c 'import yaml,sys;assert sys.version_info >= (3,10)'
if($LASTEXITCODE -ne 0){throw 'Python 3.10+ and PyYAML are required. Read README.md; do not run a partial installation.'}

function Get-Snapshots {
 $root=Join-Path $local 'snapshots'
 @(Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue | Where-Object {Test-Path -LiteralPath (Join-Path $_.FullName 'manifest.json')} | Sort-Object Name -Descending)
}
function Show-Snapshots {
 $list=Get-Snapshots
 if(!$list.Count){Write-Host 'No snapshot captured yet. Run apply once to create the first one.';return @()}
 Write-Host 'Snapshots (newest first):'
 for($i=0;$i -lt $list.Count;$i++){
  $state=Get-Content -LiteralPath (Join-Path $list[$i].FullName 'manifest.json') -Raw | ConvertFrom-Json
  $kind=if($state.kind){[string]$state.kind}else{'auto'}
  Write-Host ('  {0}. {1}  {2}  [{3}]' -f ($i+1),$list[$i].Name,$state.time,$kind)
 }
 return $list
}

if($Action -eq 'list-snapshots'){Show-Snapshots | Out-Null;return}

# Pick the restore target before elevation so the interactive prompt stays visible.
if($Action -eq 'restore' -and !$Snapshot){
 $list=Show-Snapshots
 if(!$list.Count){throw 'No snapshot available to restore.'}
 if($list.Count -eq 1){$Snapshot=$list[0].Name;Write-Host ('Using the only snapshot: '+$Snapshot)}
 else{
  $pick=Read-Host ('Select snapshot [1-'+$list.Count+']')
  $index=0
  if(![int]::TryParse($pick,[ref]$index) -or $index -lt 1 -or $index -gt $list.Count){throw 'Invalid snapshot selection'}
  $Snapshot=$list[$index-1].Name
 }
}

$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
if(!([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  $arguments=@('-NoProfile','-ExecutionPolicy','Bypass','-File',('"'+$PSCommandPath+'"'),'-Action',$Action,'-Region',$Region)
  if($Node){$arguments+=@('-Node',('"'+$Node+'"'))};if($Subscription){$arguments+=@('-Subscription',('"'+$Subscription+'"'))}
  if($Snapshot){$arguments+=@('-Snapshot',('"'+$Snapshot+'"'))}
  if($Acknowledged){$arguments+='-Acknowledged'};if($NoPause){$arguments+='-NoPause'};if($SkipBrowser){$arguments+='-SkipBrowser'}
  $process=Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $arguments -Wait -PassThru
  exit $process.ExitCode
}

if($Snapshot){$env:CCN_SNAPSHOT=Join-Path (Join-Path $local 'snapshots') $Snapshot}

if($Action -eq 'snapshot'){
 $output=& $python (Join-Path $PSScriptRoot 'client.py') create-snapshot
 if($LASTEXITCODE -ne 0){throw 'Snapshot capture failed; nothing changed'}
 $created=($output | Select-Object -Last 1) | ConvertFrom-Json
 $env:CCN_SNAPSHOT=$created.snapshot
 & (Join-Path $PSScriptRoot 'system.ps1') -Action snapshot -Python $python
 if(!$SkipBrowser){
  & $python (Join-Path $PSScriptRoot 'browser_settings.py') capture;if($LASTEXITCODE -ne 0){throw 'Browser policy capture failed'}
  & $python (Join-Path $PSScriptRoot 'browser_languages.py') capture;if($LASTEXITCODE -ne 0){throw 'Browser language capture failed'}
 }
 Write-Host ('Snapshot saved: '+$created.snapshot)
 return
}

if(!$Acknowledged) {
 if($Action -eq 'apply'){
  Write-Host 'This applies strict outbound blocking, disables IPv6, changes Singapore region/languages, clears proxy overrides, and restarts Chrome/Edge.'
  Write-Host 'If Mihomo stops, domestic public Internet is blocked too. VPN, Docker, WSL, video calls and remote access can be affected. Save browser pages first.'
  if($SkipBrowser){Write-Host 'Browser policies and languages will be left untouched (-SkipBrowser).'}
 }
 else{Write-Host ('This restores the snapshot: '+$Snapshot)}
 if((Read-Host 'Type YES to confirm') -cne 'YES'){return}
}
$opened=@(Get-Process chrome,msedge -ErrorAction SilentlyContinue | Select-Object -ExpandProperty ProcessName -Unique)
$clashProgram=@(Get-Process clash-verge -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Path -Unique)
$result=[ordered]@{success=$false;action=$Action}
try {
 if($opened.Count){Stop-Process -Name $opened -Force}
 if($Action -eq 'apply') {
  Get-Service -Name clash_verge_service -ErrorAction Stop | Out-Null
  & $python (Join-Path $PSScriptRoot 'test_dns_filter.py');if($LASTEXITCODE -ne 0){throw 'Installed core DNS type-filter control test failed; no system settings changed.'}
  if(!$Node){$Node=Read-Host 'Exact existing node name (no automatic node replacement)'}
  Get-Process clash-verge -ErrorAction SilentlyContinue | Stop-Process -Force
  $prepareArguments=@((Join-Path $PSScriptRoot 'client.py'),'prepare','--node',$Node,'--region',$Region)
  if($Subscription){$prepareArguments+=@('--subscription',$Subscription)}
  $prepared=& $python @prepareArguments;if($LASTEXITCODE -ne 0){throw 'Candidate/backup preparation failed; no system settings changed.'}
  $planState=($prepared | Select-Object -Last 1) | ConvertFrom-Json
  $env:CCN_SNAPSHOT=$planState.snapshot
  Write-Host ('Snapshot in use: '+$planState.snapshot)
  & (Join-Path $PSScriptRoot 'system.ps1') -Action snapshot -Python $python
  if(!$SkipBrowser){
   & $python (Join-Path $PSScriptRoot 'browser_settings.py') capture;if($LASTEXITCODE -ne 0){throw 'Browser policy backup failed'}
   & $python (Join-Path $PSScriptRoot 'browser_languages.py') capture;if($LASTEXITCODE -ne 0){throw 'Browser language backup failed'}
  }
  & $python (Join-Path $PSScriptRoot 'client.py') seal;if($LASTEXITCODE -ne 0){throw 'System backup sealing failed'}
  & $python (Join-Path $PSScriptRoot 'client.py') apply-client;if($LASTEXITCODE -ne 0){throw 'Client application failed'}
  $watchToken='guard-'+[Guid]::NewGuid().ToString('N')
  $watchArgs=@('-NoProfile','-ExecutionPolicy','Bypass','-File',('"'+(Join-Path $PSScriptRoot 'watchdog.ps1')+'"'),'-Token',$watchToken)
  Start-Process -FilePath 'powershell.exe' -WindowStyle Hidden -ArgumentList $watchArgs | Out-Null
  & (Join-Path $PSScriptRoot 'system.ps1') -Action apply -Python $python
  if(!$SkipBrowser){
   & $python (Join-Path $PSScriptRoot 'browser_settings.py') apply;if($LASTEXITCODE -ne 0){throw 'Browser policies failed'}
   & $python (Join-Path $PSScriptRoot 'browser_languages.py') apply;if($LASTEXITCODE -ne 0){throw 'Browser languages failed'}
  }
  & $python (Join-Path $PSScriptRoot 'verify.py') --node-outage;if($LASTEXITCODE -ne 0){throw 'Network verification failed'}
  & $python (Join-Path $PSScriptRoot 'verify_core_exit.py');if($LASTEXITCODE -ne 0){throw 'Core-exit protection/restart verification failed'}
  & $python (Join-Path $PSScriptRoot 'verify.py');if($LASTEXITCODE -ne 0){throw 'Network did not recover after core-exit check'}
  $guardMutex=New-Object Threading.Mutex($false,('Local\CCN-'+$watchToken))
  [void]$guardMutex.WaitOne()
  try {
   if(Test-Path -LiteralPath (Join-Path $local ($watchToken+'.rollback'))){throw 'Firewall watchdog rolled back before completion; not a successful deployment.'}
   if(@(Get-NetFirewallProfile | Where-Object {$_.DefaultOutboundAction.ToString() -ne 'Block'}).Count){throw 'Strict firewall is no longer active.'}
   if(@(Get-NetFirewallRule -PolicyStore PersistentStore -Name 'CCN-Portable-*' -ErrorAction SilentlyContinue).Count -ne 4){throw 'Strict firewall rules are incomplete.'}
   Set-Content -LiteralPath (Join-Path $local ($watchToken+'.commit')) -Value 'Verified'
  } finally {$guardMutex.ReleaseMutex();$guardMutex.Dispose()}
  & (Join-Path $PSScriptRoot 'update_task.ps1') -Action install -Python $python
 } else {
  & $python -c 'from baseline import verify;verify()'
  if($LASTEXITCODE -ne 0){throw 'Snapshot verification failed; no settings restored'}
  & (Join-Path $PSScriptRoot 'update_task.ps1') -Action remove -Python $python
  & (Join-Path $PSScriptRoot 'system.ps1') -Action restore -Python $python
  & $python (Join-Path $PSScriptRoot 'browser_settings.py') restore;if($LASTEXITCODE -ne 0){throw 'Browser policy restoration failed'}
  & $python (Join-Path $PSScriptRoot 'browser_languages.py') restore;if($LASTEXITCODE -ne 0){throw 'Browser language restoration failed'}
  Get-Process clash-verge -ErrorAction SilentlyContinue | Stop-Process -Force
  & $python (Join-Path $PSScriptRoot 'client.py') restore-client;if($LASTEXITCODE -ne 0){throw 'Client restoration failed'}
 }
 $result.success=$true
 Write-Host ('Completed. Restored from: '+$env:CCN_SNAPSHOT)
 Write-Host 'Restart Windows for IPv6/language changes. Network checks do not prove Claude account usability.'
} catch {
 $result.error=$_.Exception.Message
 if($Action -eq 'apply' -and $env:CCN_SNAPSHOT -and (Test-Path -LiteralPath (Join-Path $env:CCN_SNAPSHOT 'system.json'))) {
  try{& (Join-Path $PSScriptRoot 'system.ps1') -Action rollback-firewall -Python $python;$result.firewallRollback=$true;if($watchToken){Set-Content -LiteralPath (Join-Path $local ($watchToken+'.commit')) -Value 'Rolled back'}}catch{$result.firewallRollback=$false}
 }
 Write-Error $_ -ErrorAction Continue
 Write-Host 'Configuration is incomplete and may be partially applied: the firewall watchdog rolls back firewall rules only, not timezone, language, IPv6 or proxy changes. Run restore before retrying.'
} finally {
 $result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $local 'last-run.json') -Encoding UTF8
 if(Get-ScheduledTask -TaskName 'Clash Verge' -ErrorAction SilentlyContinue){Start-ScheduledTask -TaskName 'Clash Verge'}
 elseif($clashProgram.Count -eq 1 -and (Test-Path -LiteralPath $clashProgram[0])){Start-Process -FilePath $clashProgram[0]}
 if($opened -contains 'chrome'){Start-Process -FilePath "$env:ProgramFiles\Google\Chrome\Application\chrome.exe" -ArgumentList '--restore-last-session'}
 if($opened -contains 'msedge'){Start-Process -FilePath "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe" -ArgumentList '--restore-last-session'}
}
if(!$NoPause){Read-Host 'Press Enter to close'}
if(!$result.success){exit 1}
