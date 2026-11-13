# Offline round-trip tests. Registry, services, clock and region cmdlets are all mocked.
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'region_system.ps1')
$root=Join-Path (Split-Path -Parent $PSScriptRoot) ('.local/region-time-test-'+[Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $root | Out-Null
$script:cases=0
function Assert($condition,$message){if(!$condition){throw $message};$script:cases++}
function Get-RegionRegistryValue([string]$Path,[string]$Name){
 $id=$Path+'|'+$Name
 if($Name -eq 'Locale' -and $script:staleLocaleReads -gt 0){
  $script:staleLocaleReads--;return [pscustomobject]@{exists=$true;kind='String';value='00000804'}
 }
 if($script:registry.ContainsKey($id)){return [pscustomobject]@{exists=$true;kind=$script:registry[$id].kind;value=$script:registry[$id].value}}
 return [pscustomobject]@{exists=$false;kind=$null;value=$null}
}
# Exercise the real verified registry writer against fake provider cmdlets.
function New-ItemProperty {param($LiteralPath,$Name,$PropertyType,$Value,[switch]$Force,$ErrorAction)
 if($Name -eq 'Locale'){
  $script:localeWrites++
  $named=$script:registry[$LiteralPath+'|LocaleName']
  if($script:blockLocale -or ($script:requireLocaleName -and (!$named -or $named.value -ne $script:culture))){$Value='00000804'}
 }
 $script:writes++;$script:registry[$LiteralPath+'|'+$Name]=@{kind=$PropertyType;value=$Value}
}
function Remove-ItemProperty {param($LiteralPath,$Name,$ErrorAction) $script:writes++;$script:registry.Remove($LiteralPath+'|'+$Name)}
function Get-Service {param($Name,$ErrorAction) [pscustomobject]@{Status=if($script:running[$Name]){'Running'}else{'Stopped'}}}
function Set-Service {param($Name,$StartupType,$ErrorAction)
 $script:writes++;$script:registry['HKLM:\SYSTEM\CurrentControlSet\Services\'+$Name+'|Start']=@{kind='DWord';value=@{Automatic=2;Manual=3;Disabled=4}[$StartupType]}
}
function Stop-Service {param($Name,$ErrorAction) $script:writes++;$script:running[$Name]=$false}
function Start-Service {param($Name,$ErrorAction) $script:writes++;$script:running[$Name]=$true}
function Get-WinHomeLocation {[pscustomobject]@{GeoId=$script:geo}}
function Set-WinHomeLocation {param($GeoId,$ErrorAction) $script:writes++;$script:geo=[int]$GeoId}
function Get-Culture {[pscustomobject]@{Name=$script:culture}}
function Set-Culture {param($CultureInfo,$ErrorAction) $script:writes++;$script:culture=$CultureInfo}
function Get-TimeZone {param($Id,$ErrorAction) if($Id){return [TimeZoneInfo]::FindSystemTimeZoneById($Id)};[pscustomobject]@{Id=$script:zone}}
function Set-TimeZone {param($Id,$ErrorAction) $script:writes++;if($script:fail){throw 'injected write failure'};$script:zone=$Id}
function Get-ItemProperty {param($LiteralPath,$Name,$ErrorAction) [pscustomobject]@{LocaleName=$script:culture;Locale=('{0:x8}' -f ([Globalization.CultureInfo]::GetCultureInfo($script:culture)).LCID)}}
function Reset-Fixture([int]$Start,[bool]$Running,$Sync,$Delayed){
 $script:writes=0;$script:fail=$false;$script:registry=@{};$script:running=@{tzautoupdate=$Running;W32Time=$Running}
 $script:staleLocaleReads=0;$script:blockLocale=$false;$script:requireLocaleName=$false;$script:localeWrites=0
 foreach($name in @('tzautoupdate','W32Time')){
  $path='HKLM:\SYSTEM\CurrentControlSet\Services\'+$name
  $script:registry[$path+'|Start']=@{kind='DWord';value=$Start}
  if($null -ne $Delayed){$script:registry[$path+'|DelayedAutoStart']=@{kind='DWord';value=$Delayed}}
 }
 if($null -ne $Sync){$script:registry['HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\Parameters|Type']=@{kind='String';value=$Sync}}
 $script:geo=45;$script:culture='zh-CN';$script:zone='China Standard Time'
}
foreach($entry in @(@{start=2;running=$true;sync='NTP';delayed=1},@{start=3;running=$false;sync='NT5DS';delayed=$null},@{start=4;running=$false;sync='NoSync';delayed=0},@{start=4;running=$true;sync=$null;delayed=$null})){
 Reset-Fixture $entry.start $entry.running $entry.sync $entry.delayed
 $dir=Join-Path $root ([Guid]::NewGuid().ToString('N'));[IO.Directory]::CreateDirectory($dir) | Out-Null
 $beforeRegistry=$script:registry | ConvertTo-Json -Depth 5 -Compress
 Set-SystemRegionTime $dir 'US' 'en-US' 'Eastern Standard Time'
 Assert ($script:geo -eq 244 -and $script:zone -eq 'Eastern Standard Time' -and $script:culture -eq 'en-US') 'US region was not applied'
 Assert ((Get-RegionRegistryValue 'HKCU:\Control Panel\International' 'Locale').value -eq '00000409') 'regional format locale ID was not applied'
 Assert ((Get-RegionRegistryValue 'HKCU:\Control Panel\International' 'LocaleName').value -eq 'en-US') 'regional format locale name was not applied'
 Assert (!$script:running.W32Time -and !$script:running.tzautoupdate) 'automatic services were not stopped'
 Assert ((Get-RegionRegistryValue 'HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\Parameters' 'Type').value -eq 'NoSync') 'auto clock was not disabled'
 $record=Join-Path $dir 'region-time.json';$original=[IO.File]::ReadAllText($record)
 Set-SystemRegionTime $dir 'JP' 'ja-JP' 'Tokyo Standard Time'
 Assert ([IO.File]::ReadAllText($record) -ceq $original) 'repeat apply overwrote baseline'
 Restore-SystemRegionTime $dir
 Assert ($script:geo -eq 45 -and $script:culture -eq 'zh-CN' -and $script:zone -eq 'China Standard Time') 'original region was not restored'
 Assert ((Get-RegionRegistryValue 'HKCU:\Control Panel\International' 'Locale').value -eq '00000804') 'regional format locale ID was not restored'
 Assert ((Get-RegionRegistryValue 'HKCU:\Control Panel\International' 'LocaleName').value -eq 'zh-CN') 'regional format locale name was not restored'
 foreach($name in @('W32Time','tzautoupdate')){
  $current=Get-RegionServiceState $name
  Assert ($current.start -eq $entry.start -and $current.running -eq $entry.running) 'original service state was not restored'
  Assert ($current.delayed.exists -eq ($null -ne $entry.delayed)) 'delayed flag existence was not restored'
  if($null -ne $entry.delayed){Assert ($current.delayed.value -eq $entry.delayed) 'delayed flag value was not restored'}
 }
 $sync=Get-RegionRegistryValue 'HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\Parameters' 'Type'
 Assert ($sync.exists -eq ($null -ne $entry.sync) -and $sync.value -eq $entry.sync) 'original sync type was not restored'
 Restore-SystemRegionTime $dir
 Assert ($script:geo -eq 45) 'repeated restore failed'
}
Reset-Fixture 3 $false 'NTP' $null
$legacy=Join-Path $root 'legacy';[IO.Directory]::CreateDirectory($legacy) | Out-Null
Restore-SystemRegionTime $legacy
Assert ($script:writes -eq 0) 'old snapshot without new fields wrote system settings'
@{timezone='Singapore Standard Time';culture='zh-SG'} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $legacy 'system.json') -Encoding UTF8
Save-RegionTimeState $legacy
$state=Get-Content -LiteralPath (Join-Path $legacy 'region-time.json') -Raw | ConvertFrom-Json
Assert ($state.timezone -eq 'Singapore Standard Time' -and $state.culture -eq 'zh-SG' -and $state.homeGeoId -eq 45) 'upgrade replaced original baseline fields'
Reset-Fixture 2 $true 'NTP' 1
$partial=Join-Path $root 'partial';[IO.Directory]::CreateDirectory($partial) | Out-Null
$script:fail=$true;$failed=$false
try{Set-SystemRegionTime $partial 'US' 'en-US' 'Eastern Standard Time'}catch{$failed=$true}
Assert $failed 'injected failure was not reported'
$script:fail=$false;Restore-SystemRegionTime $partial
Assert ($script:culture -eq 'zh-CN' -and $script:running.W32Time) 'partial apply could not be restored'
# A failed region restore must still attempt to recover both automatic services.
Set-SystemRegionTime $partial 'US' 'en-US' 'Eastern Standard Time'
$script:fail=$true;$failed=$false
try{Restore-SystemRegionTime $partial}catch{$failed=$true}
Assert ($failed -and $script:running.W32Time -and $script:running.tzautoupdate) 'restore failure prevented service recovery or was hidden'
$script:fail=$false;Restore-SystemRegionTime $partial
$script:writes=0
'{"version":1}' | Set-Content -LiteralPath (Join-Path $partial 'region-time.json')
$failed=$false;try{Restore-SystemRegionTime $partial}catch{$failed=$true}
Assert ($failed -and $script:writes -eq 0) 'corrupt recovery record mutated settings'
$failed=$false;try{Set-SystemRegionTime $partial 'US' 'en-US' 'Eastern Standard Time'}catch{$failed=$true}
Assert ($failed -and $script:writes -eq 0) 'corrupt baseline was overwritten during apply'
# Model a write that requires the name/LCID pair to be coherent.
Reset-Fixture 3 $false 'NTP' $null
$script:requireLocaleName=$true
$paired=Join-Path $root 'paired';[IO.Directory]::CreateDirectory($paired) | Out-Null
Set-SystemRegionTime $paired 'US' 'en-US' 'Eastern Standard Time'
Assert ((Get-RegionRegistryValue 'HKCU:\Control Panel\International' 'Locale').value -eq '00000409') 'locale pair was verified before its name was written'
Assert ($script:localeWrites -eq 1) 'stable regional format performed unnecessary retries'
Restore-SystemRegionTime $paired
Assert ($script:culture -eq 'zh-CN') 'paired culture update broke restoration'
Reset-Fixture 3 $false 'NTP' $null
$singapore=Join-Path $root 'singapore';[IO.Directory]::CreateDirectory($singapore) | Out-Null
Set-SystemRegionTime $singapore 'SG' 'zh-SG' 'Singapore Standard Time'
Assert ((Get-RegionRegistryValue 'HKCU:\Control Panel\International' 'Locale').value -eq '00001004') 'Singapore locale ID was not persisted'
Assert ((Get-RegionRegistryValue 'HKCU:\Control Panel\International' 'LocaleName').value -eq 'zh-SG') 'Singapore locale name was not persisted'
Restore-SystemRegionTime $singapore
Assert ($script:culture -eq 'zh-CN') 'Singapore configuration did not restore original culture'
# A stale first read must be retried; no real registry or network is used.
Reset-Fixture 3 $false 'NTP' $null
$script:staleLocaleReads=1
$delayed=Join-Path $root 'delayed';[IO.Directory]::CreateDirectory($delayed) | Out-Null
Set-SystemRegionTime $delayed 'US' 'en-US' 'Eastern Standard Time'
Assert ($script:localeWrites -eq 2) 'transient locale readback was not retried'
Assert ($script:zone -eq 'Eastern Standard Time') 'transient readback stopped configuration'
# Permanent mismatches must still fail, with usable target/current diagnostics.
Reset-Fixture 2 $true 'NTP' 1
$script:blockLocale=$true
$blocked=Join-Path $root 'blocked';[IO.Directory]::CreateDirectory($blocked) | Out-Null
$message=''
try{Set-SystemRegionTime $blocked 'US' 'en-US' 'Eastern Standard Time'}catch{$message=$_.Exception.Message}
Assert ($message -match 'Locale' -and $message -match '00000409' -and $message -match '00000804') 'permanent locale failure omitted target/current values'
Assert ($script:localeWrites -eq 4) 'locale retry budget was not bounded'
$original=[IO.File]::ReadAllText((Join-Path $blocked 'region-time.json'))
$script:blockLocale=$false
Restore-SystemRegionTime $blocked
Assert ($script:culture -eq 'zh-CN' -and $script:running.W32Time) 'locale failure was not recoverable'
Assert ([IO.File]::ReadAllText((Join-Path $blocked 'region-time.json')) -ceq $original) 'retry changed the first recovery point'
Write-Output ('Offline region/time cases passed: '+$script:cases)
