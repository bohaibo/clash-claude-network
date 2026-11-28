param([string]$PlanFile,[switch]$WaitForTun)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'firewall_rules.ps1')
$result=[ordered]@{success=$false;full_verified=$false;status='unverified';administrator=$false}
$errorLog=Join-Path (Split-Path -Parent $PSScriptRoot) '.local/firewall-audit-error.log'
try {
 $result.administrator=([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
 if(!$result.administrator){throw 'Administrator required'}
 $plan=Get-Content -LiteralPath $PlanFile -Raw -Encoding UTF8 | ConvertFrom-Json
 Assert-StrictFirewall -coreProgram ([string]$plan.core_program) -WaitForTun:$WaitForTun
 $result.success=$true;$result.full_verified=$true;$result.status='passed'
 $result.stores=@('PersistentStore','ActiveStore')
 $result.roles=@('Core','TUN','LAN','DHCP','SharedAccess-Block')
 Remove-Item -LiteralPath $errorLog -Force -ErrorAction SilentlyContinue
} catch {
 $result.error_type=$_.Exception.GetType().Name
 if($_.Exception.Data['ccn_failed_check']){$result.failed_check=[string]$_.Exception.Data['ccn_failed_check']}
 $msg=([string]$_.Exception.Message -split "(`r`n|`n|`r)",2)[0].Trim()
 if($msg.Length -gt 260){$msg=$msg.Substring(0,260)+'…'}
 if($msg){$result.error_message=$msg}
 $result.status=if(!$result.administrator -or $_.Exception -is [UnauthorizedAccessException] -or $_.CategoryInfo.Category -eq 'PermissionDenied'){'unverified'}else{'failed'}
 $_ | Out-String | Set-Content -LiteralPath $errorLog -Encoding UTF8
}
$result | ConvertTo-Json -Compress -Depth 4
