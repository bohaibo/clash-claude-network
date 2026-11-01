# Offline location tests: only extracted helpers run; registry operations are mocked.
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'system.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'PowerShell parse failed'}
foreach($name in @('Save-LocationState','Disable-SystemLocation','Restore-SystemLocation')){
 $fn=$ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst]},$true) | Where-Object Name -eq $name
 Invoke-Expression $fn.Extent.Text
}
$stateDir=Join-Path (Split-Path -Parent $PSScriptRoot) ('.local/location-test-'+[Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stateDir | Out-Null
function Get-Item {param($LiteralPath,$ErrorAction) if($LiteralPath -like 'HKLM:*'){return $script:key}; Microsoft.PowerShell.Management\Get-Item -LiteralPath $LiteralPath}
function New-Item {param($Path,[switch]$Force) if($Path -notlike 'HKLM:*'){throw 'unexpected path'}}
function New-ItemProperty {param($LiteralPath,$Name,$PropertyType,$Value,[switch]$Force) $script:value=$Value}
function Get-ItemProperty {param($LiteralPath,$Name,$ErrorAction) if($null -ne $script:value){[pscustomobject]@{DisableLocation=$script:value}}}
function Remove-ItemProperty {param($LiteralPath,$Name,$ErrorAction) $script:value=$null}
function Test-Path {param($LiteralPath) if($LiteralPath -like 'HKLM:*'){return $true};Microsoft.PowerShell.Management\Test-Path -LiteralPath $LiteralPath}
foreach($original in @($null,0,1)){
 $script:value=$original
 $script:key=$null
 if($null -ne $original){
  $script:key=[pscustomobject]@{original=$original}
  $script:key | Add-Member ScriptMethod GetValueNames {return @('DisableLocation')}
  $script:key | Add-Member ScriptMethod GetValueKind {param($n) return 'DWord'}
  $script:key | Add-Member ScriptMethod GetValue {param($n) return $this.original}
 }
 $record=Join-Path $stateDir 'location.json'
 if(Test-Path -LiteralPath $record){Remove-Item -LiteralPath $record}
 Disable-SystemLocation
 if($script:value -ne 1){throw 'disable failed'}
 $before=Get-Content -LiteralPath $record -Raw
 Disable-SystemLocation
 if((Get-Content -LiteralPath $record -Raw) -cne $before){throw 'baseline overwritten'}
 Restore-SystemLocation
 if($script:value -ne $original){throw 'original state not restored'}
}
Remove-Item -LiteralPath $record
$script:value=1
Restore-SystemLocation
if($script:value -ne 1){throw 'legacy snapshot altered location'}
Write-Output 'Offline location cases passed: absent, enabled, disabled, repeated apply, legacy restore.'
