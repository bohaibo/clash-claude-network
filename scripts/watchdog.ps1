param([Parameter(Mandatory=$true)][string]$Token,[Parameter(Mandatory=$true)][string]$Python)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'operation_guard.ps1')
$project=Split-Path -Parent $PSScriptRoot
$local=Join-Path $project '.local'
$commit=Join-Path $local ($Token+'.commit')
$deadline=(Get-Date).AddSeconds(180)
while((Get-Date) -lt $deadline){if(Test-Path -LiteralPath $commit){exit};Start-Sleep -Seconds 2}
try{Resolve-PendingFirewall -LocalDir $local -Python $Python -ExpectedToken $Token}
catch{ $_.Exception.Message | Set-Content -LiteralPath (Join-Path $local 'watchdog-error.txt') -Encoding UTF8;exit 1 }
