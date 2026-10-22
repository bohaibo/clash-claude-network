param([ValidateSet('snapshot','apply','supplement','restore','rollback-firewall','repair-bindings')][string]$Action,[string]$Python)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'firewall_rules.ps1')
$project=Split-Path -Parent $PSScriptRoot
$localDir=Join-Path $project '.local'
# CCN_SNAPSHOT pins the snapshot for this run; otherwise the shared default-snapshot policy is used by the entry point.
$stateDir=$env:CCN_SNAPSHOT
if(!$stateDir){
  $snapshotRoot=Join-Path $localDir 'snapshots'
  $latest=@(Get-ChildItem -LiteralPath $snapshotRoot -Directory -ErrorAction SilentlyContinue | Where-Object {Test-Path -LiteralPath (Join-Path $_.FullName 'manifest.json')} | Sort-Object Name -Descending)
  $automatic=@($latest | Where-Object {
    $manifest=Get-Content -LiteralPath (Join-Path $_.FullName 'manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $kind=if($null -ne $manifest.kind){[string]$manifest.kind}else{'auto'}
    $kind -eq 'auto'
  })
  $choices=if($automatic.Count){$automatic}else{$latest}
  $stateDir=if(@($choices).Count){@($choices)[0].FullName}else{Join-Path $snapshotRoot 'uninitialized'}
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
 return @(Get-NetFirewallRule -PolicyStore $PolicyStore -ErrorAction Stop | Where-Object {$_.Name -like ($prefix+'*') -or $_.DisplayName -like ($displayPrefix+'*')})
}
# Adapter identity helpers. Never resolve an adapter by Name through a cmdlet that
# accepts wildcards: Windows names virtual adapters "Local Area Connection* 1",
# so -Name matching silently hits the wrong adapter or nothing at all.
function Get-AdapterIpv6Binding($adapter) {
  # Name is only compared literally after enumerating ALL bindings. Never pass
  # a virtual adapter name containing '*' to a wildcard-taking Name parameter.
  if($adapter.InterfaceGuid){
    $current=@(Get-NetAdapter -IncludeHidden -ErrorAction Stop | Where-Object {[string]$_.InterfaceGuid -eq [string]$adapter.InterfaceGuid})
    if($current.Count -ne 1){return $null};$adapter=$current[0]
  }
  try{$all=@(Get-NetAdapterBinding -IncludeHidden -AllBindings -ComponentID ms_tcpip6 -ErrorAction Stop)}
  catch{if("$($_.FullyQualifiedErrorId)" -like 'CmdletizationQuery_NotFound*'){return $null};throw}
  $binding=@($all | Where-Object {[string]$_.Name -ceq [string]$adapter.Name})
  if($binding.Count -gt 1){throw '同一网卡存在多条 IPv6 绑定，无法安全处理。'}
  if($binding.Count){return $binding[0]};return $null
}
function Get-AdapterIpv6Enabled($adapter) {
  $binding=Get-AdapterIpv6Binding $adapter
  if($null -ne $binding){return [bool]$binding.Enabled}
  return $null
}
function Set-AdapterIpv6Binding($adapter,[bool]$enabled) {
  $binding=Get-AdapterIpv6Binding $adapter
  if($null -eq $binding){throw '该网卡的 IPv6 绑定不存在，无法更改。'}
  # Mutate the binding instance, not a wildcard adapter name.
  if($enabled){$binding | Enable-NetAdapterBinding -Confirm:$false -ErrorAction Stop}
  else{$binding | Disable-NetAdapterBinding -Confirm:$false -ErrorAction Stop}
}
function Find-Adapter([string]$Guid,[string]$Name,[string]$Description) {
  $all=@(Get-NetAdapter -IncludeHidden -ErrorAction Stop)
  if($Guid){$hit=@($all | Where-Object {[string]$_.InterfaceGuid -eq $Guid});if($hit.Count -eq 1){return $hit[0]};return $null}
  if($Description){$hit=@($all | Where-Object {[string]$_.InterfaceDescription -ceq $Description});if($hit.Count -eq 1){return $hit[0]}}
  if($Name){$hit=@($all | Where-Object {[string]$_.Name -ceq $Name});if($hit.Count -eq 1){return $hit[0]}}
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
  $all=@(Get-NetFirewallRule -PolicyStore PersistentStore -ErrorAction Stop)
  $currentProject=@($all | Where-Object {$_.Name -like 'CCN-Portable-*' -or $_.DisplayName -like 'Claude network *'})
  $original=@($saved.projectFirewallRules | Where-Object {$null -ne $_})
  if(!$saved.PSObject.Properties['projectFirewallRules']){
    # Legacy manual snapshots did not save fingerprints; preserve recorded IDs.
    $original=@($saved.enabledOutboundRules | Where-Object {$_ -like 'CCN-Portable-*' -or $_ -like 'Claude network *'} | ForEach-Object {[pscustomobject]@{name=[string]$_;enabled='True'}})
  }
  foreach($entry in $original){
    $rule=@($currentProject | Where-Object {$_.Name -eq $entry.name})
    if($rule.Count -ne 1){throw '该快照原有的项目防火墙规则已缺失，不能把删除后的状态当作恢复成功。'}
    if($entry.fingerprint -and (Get-ProjectFirewallState $rule[0]).fingerprint -ine $entry.fingerprint){throw '项目防火墙规则内容与该快照不一致，请检查后重试恢复。'}
  }
  foreach($firewallProfile in $saved.firewallProfiles){Set-NetFirewallProfile -Name $firewallProfile.Name -DefaultOutboundAction ([int]$firewallProfile.DefaultOutboundAction) -ErrorAction Stop}
  $remove=@($currentProject | Where-Object {$_.Name -notin @($original.name)})
  if($remove.Count){$remove | Remove-NetFirewallRule -ErrorAction Stop}
  $restore=@($all | Where-Object {$_.Name -in $saved.enabledOutboundRules -and $_.Name -notin $remove.Name -and $_.Direction.ToString() -eq 'Outbound' -and $_.Action.ToString() -eq 'Allow'})
  if($restore.Count){$restore | Enable-NetFirewallRule -ErrorAction Stop}
  foreach($entry in $original){
    $rule=@($currentProject | Where-Object {$_.Name -eq $entry.name})
    if($entry.enabled -eq 'True'){$rule | Enable-NetFirewallRule -ErrorAction Stop}else{$rule | Disable-NetFirewallRule -ErrorAction Stop}
  }
  Restore-AdditionalOutboundRules $stateDir
}
function Disable-CurrentIpv6Bindings([object[]]$Targets) {
  if(!$PSBoundParameters.ContainsKey('Targets')){$Targets=@(Get-NetAdapter -IncludeHidden -ErrorAction Stop | Where-Object {(Get-AdapterIpv6Enabled $_) -eq $true})}
  $journal=Join-Path $stateDir 'disabled-by-apply.json'
  $records=@()
  if(Test-Path -LiteralPath $journal){$records=Get-Content -LiteralPath $journal -Raw -Encoding UTF8 | ConvertFrom-Json}
  $records=@(ConvertTo-AdapterChangeList $records)
  foreach($adapter in $targets){
    $guid=[string]$adapter.InterfaceGuid
    $known=@($records | Where-Object {if($guid){$_.guid -eq $guid}else{!$_.guid -and $_.name -ceq [string]$adapter.Name -and $_.description -ceq [string]$adapter.InterfaceDescription}})
    if(!$known.Count){
      $records+=@{name=[string]$adapter.Name;guid=[string]$adapter.InterfaceGuid;description=[string]$adapter.InterfaceDescription}
    }
  }
  # Write ahead and accumulate; repeated runs must retain all recovery records.
  ConvertTo-Json -InputObject @($records) -Depth 3 | Set-Content -LiteralPath ($journal+'.tmp') -Encoding UTF8
  Move-Item -LiteralPath ($journal+'.tmp') -Destination $journal -Force
  foreach($adapter in $targets){Set-AdapterIpv6Binding $adapter $false}
  $checked=@(Get-NetAdapter -IncludeHidden -ErrorAction Stop)
  $remaining=@($checked | Where-Object {(Get-AdapterIpv6Enabled $_) -eq $true})
  if($remaining.Count){throw '仍有网卡的 IPv6 绑定启用（可能在扫描期间新增），配置未完成；请重试配置或补充配置。'}
  Write-Host ('  已复查 '+$checked.Count+' 块适配器，本次关闭 '+$targets.Count+' 块的 IPv6；其余原本关闭或没有 IPv6 绑定。')
}
function Restore-RecordedIpv6($saved,[string]$directory) {
  $key='HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip6\Parameters'
  # Restore exactly what was recorded; repair is a separate explicit action.
  if($saved.ipv6.exists){New-ItemProperty -LiteralPath $key -Name DisabledComponents -PropertyType DWord -Value ([int]$saved.ipv6.value) -Force | Out-Null}
  else{Remove-ItemProperty -LiteralPath $key -Name DisabledComponents -ErrorAction SilentlyContinue}
  $failed=New-Object System.Collections.Generic.List[string]
  $records=@();$journal=Join-Path $directory 'disabled-by-apply.json'
  if(Test-Path -LiteralPath $journal){$records=Get-Content -LiteralPath $journal -Raw -Encoding UTF8 | ConvertFrom-Json}
  $records=@(ConvertTo-AdapterChangeList $records)
  foreach($record in $records){
    if(!$record){continue}
    $adapter=Find-Adapter ([string]$record.guid) ([string]$record.name) ([string]$record.description)
    if(!$adapter){Write-Warning ('记录中的网卡已不存在：'+$record.name);continue}
    try{Set-AdapterIpv6Binding $adapter $true;if((Get-AdapterIpv6Enabled $adapter) -ne $true){throw 'Binding unchanged'}}catch{$failed.Add([string]$record.name)}
  }
  # Baseline values take precedence, including intentional Disabled states.
  foreach($binding in @($saved.bindings)){
    if(!$binding -or $null -eq $binding.enabled){continue}
    $adapter=Find-Adapter ([string]$binding.guid) ([string]$binding.name) ([string]$binding.description)
    if(!$adapter){Write-Warning ('快照中的网卡已不存在：'+$binding.name);continue}
    try{Set-AdapterIpv6Binding $adapter ([bool]$binding.enabled);if((Get-AdapterIpv6Enabled $adapter) -ne [bool]$binding.enabled){throw 'Binding unchanged'}}catch{$failed.Add([string]$binding.name)}
  }
  if($failed.Count){throw ('部分网卡的 IPv6 还原失败：'+($failed -join '、')+'。恢复未完成，请检查后重试。')}
  Write-Host '  IPv6 已按快照及补充记录逐块处理；原本关闭的绑定保持关闭。'
}
if($Action -eq 'snapshot') {
  if(Test-Path -LiteralPath $file){Write-Output '已有系统状态快照，保留原文件不覆盖。';return}
  # A manual snapshot may legitimately capture an already-configured machine, so the
  # "no project firewall rules without a baseline" guard only applies to automatic ones.
  $manifestFile=Join-Path $stateDir 'manifest.json'
  $kind='auto'
  if(Test-Path -LiteralPath $manifestFile){$kind=[string](Get-Content -LiteralPath $manifestFile -Raw -Encoding UTF8 | ConvertFrom-Json).kind}
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
    bindings=@(Get-NetAdapter -IncludeHidden -ErrorAction Stop | ForEach-Object {
      $adapter=$_
      [ordered]@{name=[string]$adapter.Name;guid=[string]$adapter.InterfaceGuid;description=[string]$adapter.InterfaceDescription;enabled=(Get-AdapterIpv6Enabled $adapter)}});
    firewallProfiles=@(Get-NetFirewallProfile | Select-Object Name,DefaultOutboundAction);
    enabledOutboundRules=@(Get-EnabledOutboundAllowRules | Select-Object -ExpandProperty Name);
    projectFirewallRules=@(Get-ProjectFirewallRules | ForEach-Object {Get-ProjectFirewallState $_});
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
  foreach($adapter in @(Get-NetAdapter -IncludeHidden -ErrorAction Stop)){
    if((Get-AdapterIpv6Enabled $adapter) -eq $false){
      try{Set-AdapterIpv6Binding $adapter $true}catch{$failed.Add("$($adapter.Name): $($_.Exception.Message)")}
    }
  }
  foreach($entry in $failed){Write-Warning "以下网卡无法重新启用 IPv6：$entry"}
  if($failed.Count){throw '仍有网卡的 IPv6 处于禁用状态，可能其 TCP/IPv6 组件已被移除；可能需要执行 netcfg -d 后重启。'}
  Write-Host '已重新启用所有网卡的 IPv6，重启 Windows 后生效。'
  return
}
$saved=Get-Content -LiteralPath $file -Raw -Encoding UTF8 | ConvertFrom-Json
if($Action -eq 'supplement'){
  # Require an existing deployment; never capture a replacement baseline.
  $plan=Get-Content -LiteralPath (Join-Path $localDir 'plan.json') -Raw -Encoding UTF8 | ConvertFrom-Json
  Assert-StrictFirewall -coreProgram ([string]$plan.core_program) -AllowAdditionalLocalRules
  $inbound=@(Get-NetFirewallRule -PolicyStore PersistentStore -ErrorAction Stop | Where-Object {$_.Direction.ToString() -eq 'Inbound'})
  Write-Host ('  已扫描 '+$inbound.Count+' 条本地入站规则；按现有策略保留，不批量禁用。')
  # Local rollback only undoes this run, leaving previous protection in place.
  $outbound=@(Get-EnabledOutboundAllowRules | Where-Object {$_.Name -notlike 'CCN-Portable-*' -and $_.DisplayName -notlike 'Claude network *'})
  $adapters=@(Get-NetAdapter -IncludeHidden -ErrorAction Stop | Where-Object {(Get-AdapterIpv6Enabled $_) -eq $true})
  try{
    Disable-NonProjectOutboundRules $saved $stateDir -Targets $outbound
    Disable-CurrentIpv6Bindings -Targets $adapters
    Assert-StrictFirewall -coreProgram ([string]$plan.core_program)
  }catch{
    $failure=$_
    try{if($outbound.Count){$outbound | Enable-NetFirewallRule -ErrorAction Stop}}catch{Write-Warning '本次出站规则回滚未完成，请使用菜单 3 恢复。'}
    foreach($adapter in $adapters){
      try{Set-AdapterIpv6Binding $adapter $true}catch{Write-Warning '本次网卡回滚未完成，请使用菜单 3 恢复。'}
    }
    throw $failure
  }
  return
}
if($Action -eq 'rollback-firewall'){Restore-Firewall $saved;return}
if($Action -eq 'restore') {
  $languageRestoreFailed=$false
  Restore-Firewall $saved
  Set-Service -Name clash_verge_service -StartupType $saved.serviceStartupType
  Restore-RecordedIpv6 $saved $stateDir
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
  }catch{$languageRestoreFailed=$true;Write-Warning '语言列表还原失败，其余设置会继续还原；本次恢复将报告未完成。'}
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
  $appRestore=& $Python (Join-Path $PSScriptRoot 'app_proxies.py')
  if($LASTEXITCODE -ne 0){throw '应用代理设置仅部分恢复；后加的账号和其他配置已保留，详情见 .local/app-proxy-restore.json。'}
  if($languageRestoreFailed){throw '语言列表尚未还原，其余系统设置已继续处理。恢复未完成，请检查后重试。'}
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
Disable-CurrentIpv6Bindings
foreach($scope in @('User','Machine','Process')){foreach($name in @('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY')){[Environment]::SetEnvironmentVariable($name,$null,$scope)}}
$key='HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings';Set-ItemProperty -LiteralPath $key -Name ProxyEnable -Value 0
Remove-ItemProperty -LiteralPath $key -Name ProxyServer,AutoConfigURL -ErrorAction SilentlyContinue
& netsh.exe winhttp reset proxy | Out-Null
if($LASTEXITCODE -ne 0){throw 'WinHTTP 代理重置失败。'}
if(Get-Command git -ErrorAction SilentlyContinue){
  $keys=& git config --global --name-only --get-regexp '^https?(\..*)?\.proxy$'
  foreach($name in $keys){& git config --global --unset-all $name}
}
if(Get-Command npm.cmd -ErrorAction SilentlyContinue){& npm.cmd config delete proxy --location=user;& npm.cmd config delete https-proxy --location=user}
$docker=Join-Path ([Environment]::GetFolderPath('UserProfile')) '.docker\config.json'
if(Test-Path -LiteralPath $docker){
  & $Python -c "import json,pathlib; p=pathlib.Path.home()/'.docker/config.json'; d=json.loads(p.read_text(encoding='utf-8-sig')); d.pop('proxies',None); p.write_text(json.dumps(d,indent=2),encoding='utf-8')"
  if($LASTEXITCODE -ne 0){throw 'Docker 客户端代理清理失败。'}
}
$plan=Get-Content -LiteralPath (Join-Path $project '.local\plan.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$tun=Get-NetAdapter | Where-Object {$_.Name -eq 'Meta'}
if(@($tun).Count -ne 1){throw '找不到 Meta TUN 网卡，或者存在多个同名网卡。改防火墙之前必须先确认实际网卡。'}
Disable-NonProjectOutboundRules $saved $stateDir
Get-ProjectFirewallRules | Remove-NetFirewallRule
New-NetFirewallRule -Name ($prefix+'Core') -DisplayName 'Claude network core outbound' -Direction Outbound -Action Allow -Program $plan.core_program -Profile Any | Out-Null
New-NetFirewallRule -Name ($prefix+'TUN') -DisplayName 'Claude network TUN outbound' -Direction Outbound -Action Allow -InterfaceAlias $tun.Name -Profile Any | Out-Null
New-NetFirewallRule -Name ($prefix+'LAN') -DisplayName 'Claude network LAN outbound' -Direction Outbound -Action Allow -RemoteAddress @('10.0.0.0/8','100.64.0.0/10','127.0.0.0/8','169.254.0.0/16','172.16.0.0/12','192.168.0.0/16','224.0.0.0/4') -Profile Any | Out-Null
New-NetFirewallRule -Name ($prefix+'DHCP') -DisplayName 'Claude network DHCP outbound' -Direction Outbound -Action Allow -Program "$env:SystemRoot\System32\svchost.exe" -Service Dhcp -Protocol UDP -LocalPort 68 -RemotePort 67 -Profile Any | Out-Null
Set-NetFirewallProfile -Profile Domain,Private,Public -DefaultOutboundAction Block
