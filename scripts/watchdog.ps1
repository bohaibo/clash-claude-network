param([Parameter(Mandatory=$true)][string]$Token)
$project=Split-Path -Parent $PSScriptRoot
$commit=Join-Path $project ('.local\'+$Token+'.commit')
$deadline=(Get-Date).AddSeconds(180)
while((Get-Date) -lt $deadline){if(Test-Path -LiteralPath $commit){exit};Start-Sleep -Seconds 2}
$mutex=New-Object Threading.Mutex($false,('Local\CCN-'+$Token))
[void]$mutex.WaitOne()
try {
 if(Test-Path -LiteralPath $commit){return}
 Set-Content -LiteralPath (Join-Path $project ('.local\'+$Token+'.rollback')) -Value 'Deadline expired'
 & (Join-Path $PSScriptRoot 'system.ps1') -Action rollback-firewall
} catch { $_.Exception.Message | Set-Content -LiteralPath (Join-Path $project '.local\watchdog-error.txt') }
finally {$mutex.ReleaseMutex();$mutex.Dispose()}
