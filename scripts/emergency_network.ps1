param([Parameter(Mandatory=$true)][string]$Python)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'operation_guard.ps1')
$local=Join-Path (Split-Path -Parent $PSScriptRoot) '.local'
$stream=[IO.File]::Open((Join-Path $local 'operation.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::ReadWrite)
try{
 $stream.Lock(0,1)
 Push-Location $PSScriptRoot
 try{& $Python -c 'from baseline import verify;verify()'}finally{Pop-Location}
 if($LASTEXITCODE -ne 0){throw 'Emergency baseline verification failed'}
 if(Test-Path -LiteralPath (Join-Path $local 'pending-firewall.json')){
  Resolve-PendingFirewall -LocalDir $local -Python $Python -Force
 }else{
  & (Join-Path $PSScriptRoot 'system.ps1') -Action rollback-firewall -Python $Python
 }
}finally{$stream.Dispose()}
