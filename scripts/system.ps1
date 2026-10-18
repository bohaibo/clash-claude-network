param([ValidateSet('snapshot','apply','restore','rollback-firewall','repair-bindings')][string]$Action,[string]$Python)
$ErrorActionPreference='Stop'
$project=Split-Path -Parent $PSScriptRoot
$stateDir=Join-Path $project '.local\baseline'
$file=Join-Path $stateDir 'system.json'
$prefix='CCN-Portable-'
# Adapter identity helpers. Never resolve an adapter by Name through a cmdlet that
# accepts wildcards: Windows names virtual adapters "Local Area Connection* 1",
# so -Name matching silently hits the wrong adapter or nothing at all.
function Get-AdapterIpv6Enabled($adapter) {
  $binding=@($adapter | Get-NetAdapterBinding -ComponentID ms_tcpip6 -ErrorAction SilentlyContinue)
  if($binding.Count){return [bool]$binding[0].Enabled}
  return $null
}
function Set-AdapterIpv6Binding($adapter,[bool]$enabled) {
  if($enabled){$adapter | Enable-NetAdapterBinding -ComponentID ms_tcpip6 -Confirm:$false -ErrorAction Stop}
  else{$adapter | Disable-NetAdapterBinding -ComponentID ms_tcpip6 -Confirm:$false -ErrorAction Stop}
}
function Find-Adapter([string]$Guid,[string]$Name,[string]$Description) {
  $all=@(Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue)
  if($Guid){$hit=@($all | Where-Object {[string]$_.InterfaceGuid -eq $Guid});if($hit.Count){return $hit[0]}}
  if($Description){$hit=@($all | Where-Object {[string]$_.InterfaceDescription -ceq $Description});if($hit.Count -eq 1){return $hit[0]}}
  if($Name){$hit=@($all | Where-Object {[string]$_.Name -ceq $Name});if($hit.Count){return $hit[0]}}
  return $null
}
function Repair-Tcpip6Service {
  # If the Tcpip6 driver is set to Manual/Disabled, no IPv6 interface exists at all,
  # so per-adapter binding changes have no visible effect. Restore system start (1).
  $key='HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip6'
  $current=(Get-ItemProperty -LiteralPath $key -Name Start -ErrorAction SilentlyContinue).Start
  if($null -eq $current){return $false}
  if([int]$current -le 2){return $false}
  New-ItemProperty -LiteralPath $key -Name Start -PropertyType DWord -Value 1 -Force | Out-Null
  return $true
}
function Restore-Firewall($saved) {
  foreach($firewallProfile in $saved.firewallProfiles){Set-NetFirewallProfile -Name $firewallProfile.Name -DefaultOutboundAction ([int]$firewallProfile.DefaultOutboundAction)}
  Get-NetFirewallRule -PolicyStore PersistentStore -Name ($prefix+'*') -ErrorAction SilentlyContinue | Remove-NetFirewallRule
  foreach($name in $saved.enabledOutboundRules){Get-NetFirewallRule -PolicyStore PersistentStore -Name $name -ErrorAction SilentlyContinue | Enable-NetFirewallRule}
}
if($Action -eq 'snapshot') {
  if(Test-Path -LiteralPath $file){Write-Output 'Existing first-change system snapshot retained.';return}
  if(@(Get-NetFirewallRule -PolicyStore PersistentStore -Name ($prefix+'*') -ErrorAction SilentlyContinue).Count){throw 'Existing project firewall rules found without the original baseline. Locate that baseline instead of creating another one.'}
  $ipKey='HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip6\Parameters'
  $ipValue=Get-ItemProperty -LiteralPath $ipKey -Name DisabledComponents -ErrorAction SilentlyContinue
  $proxyKey='HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
  $proxy=[ordered]@{}
  foreach($name in @('ProxyEnable','ProxyServer','ProxyOverride','AutoConfigURL')) {
    $value=Get-ItemProperty -LiteralPath $proxyKey -Name $name -ErrorAction SilentlyContinue
    $proxy[$name]=@{exists=($null -ne $value);value=if($value){$value.$name}else{$null}}
  }
  $environment=[ordered]@{}
  foreach($scope in @('User','Machine')){
    $values=[ordered]@{};foreach($name in @('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY')){$values[$name]=[Environment]::GetEnvironmentVariable($name,$scope)};$environment[$scope]=$values
  }
  $appFiles=[ordered]@{}
  foreach($relative in @('.gitconfig','.npmrc','.docker\config.json')){
    $path=Join-Path ([Environment]::GetFolderPath('UserProfile')) $relative
    $entry=@{path=$path;exists=(Test-Path -LiteralPath $path);copy=('app-'+($appFiles.Count)+'.original')}
    if($entry.exists){Copy-Item -LiteralPath $path -Destination (Join-Path $stateDir $entry.copy)};$appFiles[$relative]=$entry
  }
  & netsh.exe winhttp dump | Set-Content -LiteralPath (Join-Path $stateDir 'winhttp.txt') -Encoding UTF8
  & netsh.exe advfirewall export (Join-Path $stateDir 'firewall.wfw') | Out-Null
  if($LASTEXITCODE -ne 0){throw 'Firewall export failed; no system settings changed.'}
  [ordered]@{timezone=(Get-TimeZone).Id;culture=(Get-Culture).Name;languages=@(Get-WinUserLanguageList);serviceStartupType=(Get-Service clash_verge_service).StartType.ToString();
    ipv6=@{exists=($null -ne $ipValue);value=if($ipValue){$ipValue.DisabledComponents}else{$null}};
    bindings=@(Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue | ForEach-Object {
      $adapter=$_
      [ordered]@{name=[string]$adapter.Name;guid=[string]$adapter.InterfaceGuid;description=[string]$adapter.InterfaceDescription;enabled=(Get-AdapterIpv6Enabled $adapter)}});
    firewallProfiles=@(Get-NetFirewallProfile | Select-Object Name,DefaultOutboundAction);
    enabledOutboundRules=@(Get-NetFirewallRule -PolicyStore PersistentStore -Direction Outbound -Action Allow -Enabled True | Select-Object -ExpandProperty Name);
    proxy=$proxy;environment=$environment;appFiles=$appFiles} | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $file -Encoding UTF8
  return
}
if($Action -eq 'repair-bindings') {
  # Standalone repair for machines restored by an older version of this script:
  # re-enable ms_tcpip6 on every adapter, drop the global IPv6 kill switch, and
  # make sure the Tcpip6 driver itself is allowed to load at boot.
  $serviceFixed=Repair-Tcpip6Service
  if($serviceFixed){Write-Warning 'Tcpip6 driver startup type was Manual/Disabled; set to system start. A reboot is required before IPv6 works.'}
  Remove-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip6\Parameters' -Name DisabledComponents -ErrorAction SilentlyContinue
  $failed=New-Object System.Collections.Generic.List[string]
  foreach($adapter in @(Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue)){
    if((Get-AdapterIpv6Enabled $adapter) -eq $false){
      try{Set-AdapterIpv6Binding $adapter $true}catch{$failed.Add("$($adapter.Name): $($_.Exception.Message)")}
    }
  }
  foreach($entry in $failed){Write-Warning "IPv6 binding could not be enabled on $entry"}
  if($failed.Count){throw 'Some adapters still have IPv6 disabled. Their TCP/IPv6 component may have been removed; netcfg -d followed by a reboot may be required.'}
  Write-Host 'IPv6 binding re-enabled on every adapter. Restart Windows for it to take effect.'
  return
}
$saved=Get-Content -LiteralPath $file -Raw | ConvertFrom-Json
if($Action -eq 'rollback-firewall'){Restore-Firewall $saved;return}
if($Action -eq 'restore') {
  Restore-Firewall $saved
  Set-Service -Name clash_verge_service -StartupType $saved.serviceStartupType
  $serviceFixed=Repair-Tcpip6Service
  if($serviceFixed){Write-Warning 'Tcpip6 driver startup type was Manual/Disabled; set to system start. A reboot is required before IPv6 works.'}
  # A baseline captured after an earlier run already disabled IPv6 would faithfully
  # restore the broken state. Detect that case and treat IPv6 as "must be usable".
  $snapshotBindings=@($saved.bindings | Where-Object {$null -ne $_})
  $snapshotEnabledCount=@($snapshotBindings | Where-Object {$_.enabled -eq $true}).Count
  $baselineDisabled=($snapshotBindings.Count -gt 0 -and $snapshotEnabledCount -eq 0) -or ($saved.ipv6.exists -and [int]$saved.ipv6.value -eq 255)
  if($baselineDisabled){
    Remove-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip6\Parameters' -Name DisabledComponents -ErrorAction SilentlyContinue
    Write-Warning 'The original baseline was captured while IPv6 was already disabled. Restoring IPv6 as usable instead of replaying that state.'
  }
  elseif($saved.ipv6.exists){New-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip6\Parameters' -Name DisabledComponents -PropertyType DWord -Value ([int]$saved.ipv6.value) -Force | Out-Null}
  else {Remove-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip6\Parameters' -Name DisabledComponents -ErrorAction SilentlyContinue}
  # IPv6 binding restoration runs in three passes. apply disables ms_tcpip6 on every
  # adapter (including hidden ones), while restore used to replay a name-keyed snapshot
  # only, so any adapter created after the snapshot stayed disabled forever.
  $bindingWarnings=New-Object System.Collections.Generic.List[string]
  $knownGuids=New-Object System.Collections.Generic.List[string]
  $knownNames=New-Object System.Collections.Generic.List[string]
  $applyFile=Join-Path $stateDir 'disabled-by-apply.json'
  if(Test-Path -LiteralPath $applyFile){
    foreach($record in @(Get-Content -LiteralPath $applyFile -Raw | ConvertFrom-Json)){
      if(!$record){continue}
      if($record.guid){[void]$knownGuids.Add([string]$record.guid)}
      if($record.name){[void]$knownNames.Add([string]$record.name)}
      $adapter=Find-Adapter ([string]$record.guid) ([string]$record.name) ([string]$record.description)
      if(!$adapter){$bindingWarnings.Add("adapter disabled during apply no longer exists: $($record.name)");continue}
      try{Set-AdapterIpv6Binding $adapter $true}catch{$bindingWarnings.Add("could not re-enable IPv6 on $($adapter.Name): $($_.Exception.Message)")}
    }
  }
  foreach($binding in $snapshotBindings){
    if($binding.guid){[void]$knownGuids.Add([string]$binding.guid)}
    if($binding.Name){[void]$knownNames.Add([string]$binding.Name)}
    if($baselineDisabled){$targetState=$true}
    elseif($null -eq $binding.enabled){continue}
    else{$targetState=[bool]$binding.enabled}
    $adapter=Find-Adapter ([string]$binding.guid) ([string]$binding.Name) ([string]$binding.description)
    if(!$adapter){$bindingWarnings.Add("adapter from the original snapshot is absent: $($binding.Name)");continue}
    try{Set-AdapterIpv6Binding $adapter $targetState}catch{$bindingWarnings.Add("could not restore IPv6 binding on $($adapter.Name): $($_.Exception.Message)")}
  }
  foreach($adapter in @(Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue)){
    if($knownGuids -contains [string]$adapter.InterfaceGuid){continue}
    if($knownNames -contains [string]$adapter.Name){continue}
    if((Get-AdapterIpv6Enabled $adapter) -ne $false){continue}
    try{Set-AdapterIpv6Binding $adapter $true;$bindingWarnings.Add("re-enabled IPv6 on adapter missing from both records: $($adapter.Name)")}catch{$bindingWarnings.Add("could not re-enable IPv6 on unrecorded adapter $($adapter.Name): $($_.Exception.Message)")}
  }
  $stillDisabled=@(Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue | Where-Object {(Get-AdapterIpv6Enabled $_) -eq $false})
  foreach($adapter in $stillDisabled){$bindingWarnings.Add("IPv6 binding still disabled after restore: $($adapter.Name)")}
  foreach($message in $bindingWarnings){Write-Warning $message}
  Set-TimeZone -Id $saved.timezone;Set-Culture -CultureInfo $saved.culture
  $languages=New-WinUserLanguageList $saved.languages[0].LanguageTag;$languages.Clear()
  foreach($entry in $saved.languages){$item=(New-WinUserLanguageList $entry.LanguageTag)[0];$item.InputMethodTips.Clear();foreach($tip in $entry.InputMethodTips){$item.InputMethodTips.Add([string]$tip)};$item.Spellchecking=[bool]$entry.Spellchecking;$item.Handwriting=[bool]$entry.Handwriting;$languages.Add($item)}
  Set-WinUserLanguageList -LanguageList $languages -Force
  foreach($scope in @('User','Machine')){foreach($property in $saved.environment.$scope.PSObject.Properties){[Environment]::SetEnvironmentVariable($property.Name,$property.Value,$scope)}}
  $key='HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
  foreach($property in $saved.proxy.PSObject.Properties){
    if($property.Value.exists){$kind=if($property.Name -eq 'ProxyEnable'){'DWord'}else{'String'};New-ItemProperty -LiteralPath $key -Name $property.Name -Value $property.Value.value -PropertyType $kind -Force | Out-Null}
    else {Remove-ItemProperty -LiteralPath $key -Name $property.Name -ErrorAction SilentlyContinue}
  }
  & netsh.exe exec (Join-Path $stateDir 'winhttp.txt') | Out-Null
  if($LASTEXITCODE -ne 0){throw 'WinHTTP restoration failed; inspect the retained dump.'}
  foreach($entry in $saved.appFiles.PSObject.Properties.Value){
    if($entry.exists){Copy-Item -LiteralPath (Join-Path $stateDir $entry.copy) -Destination $entry.path -Force}
    elseif(Test-Path -LiteralPath $entry.path){Remove-Item -LiteralPath $entry.path}
  }
  return
}
# Applying does not create any browser/account identity or install Claude.
Set-Service -Name clash_verge_service -StartupType Automatic
Set-TimeZone -Id 'Singapore Standard Time';Set-Culture -CultureInfo 'zh-SG'
$langs=New-WinUserLanguageList 'zh-Hans-SG'
$existing=Get-WinUserLanguageList
$tips=@($existing | ForEach-Object {$_.InputMethodTips} | Where-Object {$_ -like '0804:*'})
if($tips.Count){$langs[0].InputMethodTips.Clear();foreach($tip in $tips){$langs[0].InputMethodTips.Add([string]$tip)}}
$langs.Add((New-WinUserLanguageList 'en-SG')[0]);Set-WinUserLanguageList -LanguageList $langs -Force
New-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip6\Parameters' -Name DisabledComponents -PropertyType DWord -Value 255 -Force | Out-Null
# Record exactly which adapters this run disabled, so restore can re-enable them even
# when they are later renamed, rebuilt or re-plugged and no longer match the snapshot.
$ipv6Targets=@(Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue | Where-Object {(Get-AdapterIpv6Enabled $_) -eq $true})
foreach($adapter in $ipv6Targets){
  try{Set-AdapterIpv6Binding $adapter $false}catch{Write-Warning "could not disable IPv6 on $($adapter.Name): $($_.Exception.Message)"}
}
$applyRecords=@($ipv6Targets | ForEach-Object {[ordered]@{name=[string]$_.Name;guid=[string]$_.InterfaceGuid;description=[string]$_.InterfaceDescription}})
if($applyRecords.Count){$applyRecords | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $stateDir 'disabled-by-apply.json') -Encoding UTF8}
else {'[]' | Set-Content -LiteralPath (Join-Path $stateDir 'disabled-by-apply.json') -Encoding UTF8}
foreach($scope in @('User','Machine','Process')){foreach($name in @('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY')){[Environment]::SetEnvironmentVariable($name,$null,$scope)}}
$key='HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings';Set-ItemProperty -LiteralPath $key -Name ProxyEnable -Value 0
Remove-ItemProperty -LiteralPath $key -Name ProxyServer,AutoConfigURL -ErrorAction SilentlyContinue
& netsh.exe winhttp reset proxy | Out-Null
if($LASTEXITCODE -ne 0){throw 'WinHTTP proxy reset failed'}
if(Get-Command git -ErrorAction SilentlyContinue){
  $keys=& git config --global --name-only --get-regexp '(^http.*\.proxy$|^https.*\.proxy$)'
  foreach($name in $keys){& git config --global --unset-all $name}
}
if(Get-Command npm.cmd -ErrorAction SilentlyContinue){& npm.cmd config delete proxy --location=user;& npm.cmd config delete https-proxy --location=user}
$docker=Join-Path ([Environment]::GetFolderPath('UserProfile')) '.docker\config.json'
if(Test-Path -LiteralPath $docker){
  & $Python -c "import json,pathlib; p=pathlib.Path.home()/'.docker/config.json'; d=json.loads(p.read_text(encoding='utf-8-sig')); d.pop('proxies',None); p.write_text(json.dumps(d,indent=2),encoding='utf-8')"
  if($LASTEXITCODE -ne 0){throw 'Docker client proxy cleanup failed'}
}
$plan=Get-Content -LiteralPath (Join-Path $project '.local\plan.json') -Raw | ConvertFrom-Json
$tun=Get-NetAdapter | Where-Object {$_.Name -eq 'Meta'}
if(@($tun).Count -ne 1){throw 'Meta TUN adapter is missing/ambiguous. Agent must inspect actual adapter before firewall changes.'}
$unknown=@(Get-NetFirewallRule -PolicyStore PersistentStore -Direction Outbound -Action Allow -Enabled True | Where-Object {$_.Name -notlike ($prefix+'*') -and $_.Name -notin $saved.enabledOutboundRules})
if($unknown.Count){throw 'New outbound allow rules detected since initial snapshot. Review before proceeding.'}
Get-NetFirewallRule -PolicyStore PersistentStore -Direction Outbound -Action Allow -Enabled True | Where-Object {$_.Name -notlike ($prefix+'*')} | Disable-NetFirewallRule
Get-NetFirewallRule -Name ($prefix+'*') -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -Name ($prefix+'Core') -DisplayName 'Claude network core outbound' -Direction Outbound -Action Allow -Program $plan.core_program -Profile Any | Out-Null
New-NetFirewallRule -Name ($prefix+'TUN') -DisplayName 'Claude network TUN outbound' -Direction Outbound -Action Allow -InterfaceAlias $tun.Name -Profile Any | Out-Null
New-NetFirewallRule -Name ($prefix+'LAN') -DisplayName 'Claude network LAN outbound' -Direction Outbound -Action Allow -RemoteAddress @('10.0.0.0/8','100.64.0.0/10','127.0.0.0/8','169.254.0.0/16','172.16.0.0/12','192.168.0.0/16','224.0.0.0/4') -Profile Any | Out-Null
New-NetFirewallRule -Name ($prefix+'DHCP') -DisplayName 'Claude network DHCP outbound' -Direction Outbound -Action Allow -Program "$env:SystemRoot\System32\svchost.exe" -Service Dhcp -Protocol UDP -LocalPort 68 -RemotePort 67 -Profile Any | Out-Null
Set-NetFirewallProfile -Profile Domain,Private,Public -DefaultOutboundAction Block
