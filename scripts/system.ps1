param([ValidateSet('snapshot','apply','restore','rollback-firewall')][string]$Action,[string]$Python)
$ErrorActionPreference='Stop'
$project=Split-Path -Parent $PSScriptRoot
$stateDir=Join-Path $project '.local\baseline'
$file=Join-Path $stateDir 'system.json'
$prefix='CCN-Portable-'
function Restore-Firewall($saved) {
  foreach($profile in $saved.firewallProfiles){Set-NetFirewallProfile -Name $profile.Name -DefaultOutboundAction ([int]$profile.DefaultOutboundAction)}
  Get-NetFirewallRule -PolicyStore PersistentStore -Name ($prefix+'*') -ErrorAction SilentlyContinue | Remove-NetFirewallRule
  foreach($name in $saved.enabledOutboundRules){Get-NetFirewallRule -PolicyStore PersistentStore -Name $name -ErrorAction SilentlyContinue | Enable-NetFirewallRule}
}
if($Action -eq 'snapshot') {
  if(Test-Path -LiteralPath $file){Write-Output 'Existing first-change system snapshot retained.';return}
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
    bindings=@(Get-NetAdapterBinding -ComponentID ms_tcpip6 -IncludeHidden | Select-Object Name,Enabled);
    firewallProfiles=@(Get-NetFirewallProfile | Select-Object Name,DefaultOutboundAction);
    enabledOutboundRules=@(Get-NetFirewallRule -PolicyStore PersistentStore -Direction Outbound -Action Allow -Enabled True | Select-Object -ExpandProperty Name);
    proxy=$proxy;environment=$environment;appFiles=$appFiles} | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $file -Encoding UTF8
  return
}
$saved=Get-Content -LiteralPath $file -Raw | ConvertFrom-Json
if($Action -eq 'rollback-firewall'){Restore-Firewall $saved;return}
if($Action -eq 'restore') {
  Restore-Firewall $saved
  Set-Service -Name clash_verge_service -StartupType $saved.serviceStartupType
  if($saved.ipv6.exists){New-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip6\Parameters' -Name DisabledComponents -PropertyType DWord -Value ([int]$saved.ipv6.value) -Force | Out-Null}
  else {Remove-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip6\Parameters' -Name DisabledComponents -ErrorAction SilentlyContinue}
  foreach($binding in $saved.bindings){
    $present=Get-NetAdapterBinding -Name $binding.Name -ComponentID ms_tcpip6 -IncludeHidden -ErrorAction SilentlyContinue
    if(!$present){continue}
    if($binding.Enabled){Enable-NetAdapterBinding -Name $binding.Name -ComponentID ms_tcpip6 -IncludeHidden -Confirm:$false | Out-Null}
    else {Disable-NetAdapterBinding -Name $binding.Name -ComponentID ms_tcpip6 -IncludeHidden -Confirm:$false | Out-Null}
  }
  Set-TimeZone -Id $saved.timezone;Set-Culture -CultureInfo $saved.culture
  $languages=New-WinUserLanguageList $saved.languages[0].LanguageTag;$languages.Clear()
  foreach($entry in $saved.languages){$item=(New-WinUserLanguageList $entry.LanguageTag)[0];$item.InputMethodTips.Clear();foreach($tip in $entry.InputMethodTips){$item.InputMethodTips.Add([string]$tip)};$languages.Add($item)}
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
Get-NetAdapterBinding -ComponentID ms_tcpip6 -IncludeHidden | Where-Object Enabled | ForEach-Object {Disable-NetAdapterBinding -Name $_.Name -ComponentID ms_tcpip6 -IncludeHidden -Confirm:$false | Out-Null}
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
