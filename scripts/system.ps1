param([ValidateSet('snapshot','apply','restore','rollback-firewall','repair-bindings')][string]$Action,[string]$Python)
$ErrorActionPreference='Stop'
$project=Split-Path -Parent $PSScriptRoot
$localDir=Join-Path $project '.local'
# CCN_SNAPSHOT pins the snapshot for this run; otherwise the newest captured snapshot wins.
$stateDir=$env:CCN_SNAPSHOT
if(!$stateDir){
  $snapshotRoot=Join-Path $localDir 'snapshots'
  $latest=@(Get-ChildItem -LiteralPath $snapshotRoot -Directory -ErrorAction SilentlyContinue | Where-Object {Test-Path -LiteralPath (Join-Path $_.FullName 'manifest.json')} | Sort-Object Name -Descending)
  $stateDir=if($latest.Count){$latest[0].FullName}else{Join-Path $snapshotRoot 'uninitialized'}
}
$file=Join-Path $stateDir 'system.json'
$prefix='CCN-Portable-'
$displayPrefix='Claude network '
# 本项目的 4 条出站放行规则用 Name 前缀 CCN-Portable-* 或 DisplayName 前缀
# "Claude network " 识别。2026-10-18 实测有外部因素把规则的 Name 改写成了
# DisplayName 文本，只认 CCN-Portable-* 的旧逻辑会把自己的规则判成"未知新
# 规则"而中止配置，恢复时也删不掉，看门狗回滚同样受影响。
function Get-ProjectFirewallRules {
 param([string]$PolicyStore='PersistentStore')
 return @(Get-NetFirewallRule -PolicyStore $PolicyStore -ErrorAction SilentlyContinue | Where-Object {$_.Name -like ($prefix+'*') -or $_.DisplayName -like ($displayPrefix+'*')})
}
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
  Get-ProjectFirewallRules | Remove-NetFirewallRule
  foreach($name in $saved.enabledOutboundRules){Get-NetFirewallRule -PolicyStore PersistentStore -Name $name -ErrorAction SilentlyContinue | Enable-NetFirewallRule}
}
if($Action -eq 'snapshot') {
  if(Test-Path -LiteralPath $file){Write-Output '已有系统状态快照，保留原文件不覆盖。';return}
  # A manual snapshot may legitimately capture an already-configured machine, so the
  # "no project firewall rules without a baseline" guard only applies to automatic ones.
  $manifestFile=Join-Path $stateDir 'manifest.json'
  $kind='auto'
  if(Test-Path -LiteralPath $manifestFile){$kind=[string](Get-Content -LiteralPath $manifestFile -Raw | ConvertFrom-Json).kind}
  if($kind -ne 'manual' -and @(Get-ProjectFirewallRules).Count){throw '发现本项目残留的防火墙规则，但没有原始基线。请改为「保存当前快照」，不要新建自动快照。'}
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
  if($LASTEXITCODE -ne 0){throw '防火墙导出失败，未改动任何系统设置。'}
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
  if($serviceFixed){Write-Warning 'Tcpip6 驱动被设为手动/禁用，已改回随系统启动。需要重启 Windows 后 IPv6 才会生效。'}
  Remove-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip6\Parameters' -Name DisabledComponents -ErrorAction SilentlyContinue
  $failed=New-Object System.Collections.Generic.List[string]
  foreach($adapter in @(Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue)){
    if((Get-AdapterIpv6Enabled $adapter) -eq $false){
      try{Set-AdapterIpv6Binding $adapter $true}catch{$failed.Add("$($adapter.Name): $($_.Exception.Message)")}
    }
  }
  foreach($entry in $failed){Write-Warning "以下网卡无法重新启用 IPv6：$entry"}
  if($failed.Count){throw '仍有网卡的 IPv6 处于禁用状态，可能其 TCP/IPv6 组件已被移除；可能需要执行 netcfg -d 后重启。'}
  Write-Host '已重新启用所有网卡的 IPv6，重启 Windows 后生效。'
  return
}
$saved=Get-Content -LiteralPath $file -Raw | ConvertFrom-Json
if($Action -eq 'rollback-firewall'){Restore-Firewall $saved;return}
if($Action -eq 'restore') {
  Restore-Firewall $saved
  Set-Service -Name clash_verge_service -StartupType $saved.serviceStartupType
  $serviceFixed=Repair-Tcpip6Service
  if($serviceFixed){Write-Warning 'Tcpip6 驱动被设为手动/禁用，已改回随系统启动。需要重启 Windows 后 IPv6 才会生效。'}
  # A baseline captured after an earlier run already disabled IPv6 would faithfully
  # restore the broken state. Detect that case and treat IPv6 as "must be usable".
  $snapshotBindings=@($saved.bindings | Where-Object {$null -ne $_})
  $snapshotEnabledCount=@($snapshotBindings | Where-Object {$_.enabled -eq $true}).Count
  $baselineDisabled=($snapshotBindings.Count -gt 0 -and $snapshotEnabledCount -eq 0) -or ($saved.ipv6.exists -and [int]$saved.ipv6.value -eq 255)
  if($baselineDisabled){
    Remove-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip6\Parameters' -Name DisabledComponents -ErrorAction SilentlyContinue
    Write-Warning '原始基线是在 IPv6 已被禁用时采集的，因此这里恢复成「可用」，而不是回放那个禁用状态。'
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
      if(!$adapter){$bindingWarnings.Add("配置时被禁用的网卡已不存在：$($record.name)");continue}
      try{Set-AdapterIpv6Binding $adapter $true}catch{$bindingWarnings.Add("无法重新启用网卡 $($adapter.Name) 的 IPv6：$($_.Exception.Message)")}
    }
  }
  foreach($binding in $snapshotBindings){
    if($binding.guid){[void]$knownGuids.Add([string]$binding.guid)}
    if($binding.Name){[void]$knownNames.Add([string]$binding.Name)}
    if($baselineDisabled){$targetState=$true}
    elseif($null -eq $binding.enabled){continue}
    else{$targetState=[bool]$binding.enabled}
    $adapter=Find-Adapter ([string]$binding.guid) ([string]$binding.Name) ([string]$binding.description)
    if(!$adapter){$bindingWarnings.Add("原始快照里的网卡已不存在：$($binding.Name)");continue}
    try{Set-AdapterIpv6Binding $adapter $targetState}catch{$bindingWarnings.Add("无法还原网卡 $($adapter.Name) 的 IPv6 绑定：$($_.Exception.Message)")}
  }
  foreach($adapter in @(Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue)){
    if($knownGuids -contains [string]$adapter.InterfaceGuid){continue}
    if($knownNames -contains [string]$adapter.Name){continue}
    if((Get-AdapterIpv6Enabled $adapter) -ne $false){continue}
    try{Set-AdapterIpv6Binding $adapter $true;$bindingWarnings.Add("已为两份记录中都不存在的网卡重新启用 IPv6：$($adapter.Name)")}catch{$bindingWarnings.Add("无法为未记录的网卡 $($adapter.Name) 重新启用 IPv6：$($_.Exception.Message)")}
  }
  $stillDisabled=@(Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue | Where-Object {(Get-AdapterIpv6Enabled $_) -eq $false})
  foreach($adapter in $stillDisabled){$bindingWarnings.Add("还原后 IPv6 仍处于禁用状态：$($adapter.Name)")}
  foreach($message in $bindingWarnings){Write-Warning $message}
  Set-TimeZone -Id $saved.timezone;Set-Culture -CultureInfo $saved.culture
  # @(Get-WinUserLanguageList) is stored one level too deep in the snapshot
  # ([ [ {..}, {..} ] ]), so .LanguageTag used to return an array and
  # New-WinUserLanguageList -Language rejected it, aborting the whole restore.
  # Flatten it, and never let a cosmetic language step block the rest.
  try{
    $savedLanguages=@($saved.languages)
    if($savedLanguages.Count -eq 1 -and $savedLanguages[0] -is [System.Array]){$savedLanguages=@($savedLanguages[0])}
    $savedLanguages=@($savedLanguages | Where-Object {$null -ne $_ -and $_ -isnot [System.Array]})
    if($savedLanguages.Count){
      $languages=New-WinUserLanguageList ([string]($savedLanguages[0].LanguageTag));$languages.Clear()
      foreach($entry in $savedLanguages){$item=(New-WinUserLanguageList ([string]($entry.LanguageTag)))[0];$item.InputMethodTips.Clear();foreach($tip in @($entry.InputMethodTips)){$item.InputMethodTips.Add([string]$tip)};$item.Spellchecking=[bool]$entry.Spellchecking;$item.Handwriting=[bool]$entry.Handwriting;$languages.Add($item)}
      Set-WinUserLanguageList -LanguageList $languages -Force
    }
  }catch{Write-Warning ('语言列表还原失败，其余设置继续还原：'+$_.Exception.Message)}
  foreach($scope in @('User','Machine')){foreach($property in $saved.environment.$scope.PSObject.Properties){[Environment]::SetEnvironmentVariable($property.Name,$property.Value,$scope)}}
  $key='HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
  foreach($property in $saved.proxy.PSObject.Properties){
    if($property.Value.exists){$kind=if($property.Name -eq 'ProxyEnable'){'DWord'}else{'String'};New-ItemProperty -LiteralPath $key -Name $property.Name -Value $property.Value.value -PropertyType $kind -Force | Out-Null}
    else {Remove-ItemProperty -LiteralPath $key -Name $property.Name -ErrorAction SilentlyContinue}
  }
  # Set-Content -Encoding UTF8 writes a BOM, and netsh cannot parse "\ufeff" as
  # the first command, so exec failed on a dump this project itself produced.
  # Re-write a BOM-free copy before replaying it.
  $dumpFile=Join-Path $stateDir 'winhttp.txt'
  $execFile=Join-Path $stateDir 'winhttp.exec.txt'
  $dumpText=Get-Content -LiteralPath $dumpFile -Raw -Encoding UTF8
  [IO.File]::WriteAllText($execFile,$dumpText,(New-Object System.Text.UTF8Encoding($false)))
  & netsh.exe exec $execFile | Out-Null
  if($LASTEXITCODE -ne 0){throw 'WinHTTP 还原失败，请查看保留的导出文件。'}
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
  try{Set-AdapterIpv6Binding $adapter $false}catch{Write-Warning "无法禁用网卡 $($adapter.Name) 的 IPv6：$($_.Exception.Message)"}
}
$applyRecords=@($ipv6Targets | ForEach-Object {[ordered]@{name=[string]$_.Name;guid=[string]$_.InterfaceGuid;description=[string]$_.InterfaceDescription}})
if($applyRecords.Count){$applyRecords | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $stateDir 'disabled-by-apply.json') -Encoding UTF8}
else {'[]' | Set-Content -LiteralPath (Join-Path $stateDir 'disabled-by-apply.json') -Encoding UTF8}
foreach($scope in @('User','Machine','Process')){foreach($name in @('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY')){[Environment]::SetEnvironmentVariable($name,$null,$scope)}}
$key='HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings';Set-ItemProperty -LiteralPath $key -Name ProxyEnable -Value 0
Remove-ItemProperty -LiteralPath $key -Name ProxyServer,AutoConfigURL -ErrorAction SilentlyContinue
& netsh.exe winhttp reset proxy | Out-Null
if($LASTEXITCODE -ne 0){throw 'WinHTTP 代理重置失败。'}
if(Get-Command git -ErrorAction SilentlyContinue){
  $keys=& git config --global --name-only --get-regexp '(^http.*\.proxy$|^https.*\.proxy$)'
  foreach($name in $keys){& git config --global --unset-all $name}
}
if(Get-Command npm.cmd -ErrorAction SilentlyContinue){& npm.cmd config delete proxy --location=user;& npm.cmd config delete https-proxy --location=user}
$docker=Join-Path ([Environment]::GetFolderPath('UserProfile')) '.docker\config.json'
if(Test-Path -LiteralPath $docker){
  & $Python -c "import json,pathlib; p=pathlib.Path.home()/'.docker/config.json'; d=json.loads(p.read_text(encoding='utf-8-sig')); d.pop('proxies',None); p.write_text(json.dumps(d,indent=2),encoding='utf-8')"
  if($LASTEXITCODE -ne 0){throw 'Docker 客户端代理清理失败。'}
}
$plan=Get-Content -LiteralPath (Join-Path $project '.local\plan.json') -Raw | ConvertFrom-Json
$tun=Get-NetAdapter | Where-Object {$_.Name -eq 'Meta'}
if(@($tun).Count -ne 1){throw '找不到 Meta TUN 网卡，或者存在多个同名网卡。改防火墙之前必须先确认实际网卡。'}
$unknown=@(Get-NetFirewallRule -PolicyStore PersistentStore -Direction Outbound -Action Allow -Enabled True | Where-Object {$_.Name -notlike ($prefix+'*') -and $_.DisplayName -notlike ($displayPrefix+'*') -and $_.Name -notin $saved.enabledOutboundRules})
if($unknown.Count){throw '自初始快照以来出现了新的出站允许规则，请先确认再继续。'}
Get-NetFirewallRule -PolicyStore PersistentStore -Direction Outbound -Action Allow -Enabled True | Where-Object {$_.Name -notlike ($prefix+'*') -and $_.DisplayName -notlike ($displayPrefix+'*')} | Disable-NetFirewallRule
Get-ProjectFirewallRules | Remove-NetFirewallRule
New-NetFirewallRule -Name ($prefix+'Core') -DisplayName 'Claude network core outbound' -Direction Outbound -Action Allow -Program $plan.core_program -Profile Any | Out-Null
New-NetFirewallRule -Name ($prefix+'TUN') -DisplayName 'Claude network TUN outbound' -Direction Outbound -Action Allow -InterfaceAlias $tun.Name -Profile Any | Out-Null
New-NetFirewallRule -Name ($prefix+'LAN') -DisplayName 'Claude network LAN outbound' -Direction Outbound -Action Allow -RemoteAddress @('10.0.0.0/8','100.64.0.0/10','127.0.0.0/8','169.254.0.0/16','172.16.0.0/12','192.168.0.0/16','224.0.0.0/4') -Profile Any | Out-Null
New-NetFirewallRule -Name ($prefix+'DHCP') -DisplayName 'Claude network DHCP outbound' -Direction Outbound -Action Allow -Program "$env:SystemRoot\System32\svchost.exe" -Service Dhcp -Protocol UDP -LocalPort 68 -RemotePort 67 -Profile Any | Out-Null
Set-NetFirewallProfile -Profile Domain,Private,Public -DefaultOutboundAction Block
