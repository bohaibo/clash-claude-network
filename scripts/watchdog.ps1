param([Parameter(Mandatory=$true)][string]$Token,[Parameter(Mandatory=$true)][string]$Python)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'operation_guard.ps1')
$project=Split-Path -Parent $PSScriptRoot
$local=Join-Path $project '.local'
$commit=Join-Path $local ($Token+'.commit')
$deadline=(Get-Date).AddSeconds(180)
while((Get-Date) -lt $deadline){if(Test-Path -LiteralPath $commit){exit};Start-Sleep -Seconds 2}
try{
 Resolve-PendingFirewall -LocalDir $local -Python $Python -ExpectedToken $Token
 # Firewall rollback alone cannot recover a core left stopped by an interrupted
 # outage test. Starting the existing service needs no network or model call.
 $pendingFile=Join-Path $local 'pending-firewall.json'
 if(Test-Path -LiteralPath $pendingFile){
  $current=Get-Content -LiteralPath $pendingFile -Raw -Encoding UTF8 | ConvertFrom-Json
  if($current.token -ceq $Token -and (Test-Path -LiteralPath (Join-Path $local ($Token+'.rollback')))){
   Start-Service -Name clash_verge_service -ErrorAction Stop
  }
 }
}
catch{ $_.Exception.Message | Set-Content -LiteralPath (Join-Path $local 'watchdog-error.txt') -Encoding UTF8;exit 1 }
