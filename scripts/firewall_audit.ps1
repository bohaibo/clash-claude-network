param([string]$PlanFile)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'firewall_rules.ps1')
$result=[ordered]@{success=$false;full_verified=$false;status='unverified';administrator=$false}
try {
 $result.administrator=([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
 if(!$result.administrator){throw 'Administrator required'}
 $plan=Get-Content -LiteralPath $PlanFile -Raw -Encoding UTF8 | ConvertFrom-Json
 Assert-StrictFirewall -coreProgram ([string]$plan.core_program)
 $result.success=$true;$result.full_verified=$true;$result.status='passed'
 $result.stores=@('PersistentStore','ActiveStore')
 $result.roles=@('Core','TUN','LAN','DHCP','SharedAccess-Block')
} catch {
 $result.error_type=$_.Exception.GetType().Name
 $result.status=if(!$result.administrator -or $_.Exception -is [UnauthorizedAccessException] -or $_.CategoryInfo.Category -eq 'PermissionDenied'){'unverified'}else{'failed'}
 $_ | Out-String | Set-Content -LiteralPath (Join-Path (Split-Path -Parent $PSScriptRoot) '.local/firewall-audit-error.log') -Encoding UTF8
}
$result | ConvertTo-Json -Compress -Depth 4
