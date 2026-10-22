# A pending rollback belongs to its original snapshot, never the newest one.
function Resolve-PendingFirewall([string]$LocalDir,[string]$Python,[string]$ExpectedToken,[switch]$Force) {
 $file=Join-Path $LocalDir 'pending-firewall.json'
 if(!(Test-Path -LiteralPath $file)){return}
 $pending=Get-Content -LiteralPath $file -Raw -Encoding UTF8 | ConvertFrom-Json
 $token=[string]$pending.token
 if($token -notmatch '^guard-[a-f0-9]{32}$'){throw '待处理的防火墙回滚记录无效，请检查 .local/pending-firewall.json。'}
 if($ExpectedToken -and $token -ne $ExpectedToken){return}
 $commit=Join-Path $LocalDir ($token+'.commit')
 if(!$Force -and (Test-Path -LiteralPath $commit)){return}
 $snapshot=[IO.Path]::GetFullPath([string]$pending.snapshot)
 if((Split-Path -Parent $snapshot) -ine [IO.Path]::GetFullPath((Join-Path $LocalDir 'snapshots'))){throw '回滚快照不在本项目目录内。'}
 $mutex=New-Object Threading.Mutex($false,('Global\CCN-'+$token))
 $held=$false;$previous=$env:CCN_SNAPSHOT
 try{
  try{$held=$mutex.WaitOne()}catch [Threading.AbandonedMutexException]{$held=$true}
  if(!$Force -and (Test-Path -LiteralPath $commit)){return}
  if($Force -and (Test-Path -LiteralPath $commit)){Remove-Item -LiteralPath $commit -Force}
  $env:CCN_SNAPSHOT=$snapshot
  Push-Location $PSScriptRoot
  try{& $Python -c 'from baseline import verify;verify()'}finally{Pop-Location}
  if($LASTEXITCODE -ne 0){throw '待处理的回滚快照校验失败，未改动防火墙。'}
  Write-Host '正在完成上次操作留下的防火墙回滚，完成前不会开始新的配置。'
  Set-Content -LiteralPath (Join-Path $LocalDir ($token+'.rollback')) -Value 'Rollback started'
  & (Join-Path $PSScriptRoot 'system.ps1') -Action rollback-firewall -Python $Python
  Set-Content -LiteralPath $commit -Value 'Rollback completed'
 }finally{
  $env:CCN_SNAPSHOT=$previous
  if($held){$mutex.ReleaseMutex()};$mutex.Dispose()
 }
}
