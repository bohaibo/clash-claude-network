# Internal helpers; callers supply the active snapshot. No live changes on import.
function ConvertTo-RuleNameList($value) {
  foreach($entry in $value){
    if($null -eq $entry){continue}
    if($entry -is [System.Array]){ConvertTo-RuleNameList $entry}
    elseif($entry -is [string]){if($entry){Write-Output $entry}}
    else{throw '防火墙恢复清单包含无效记录。'}
  }
}
function ConvertTo-AdapterChangeList($value) {
  foreach($entry in $value){
    if($null -eq $entry){continue}
    if($entry -is [System.Array]){ConvertTo-AdapterChangeList $entry}
    elseif(($entry.guid -is [string] -and $entry.guid) -or ($entry.name -is [string] -and $entry.name)){Write-Output $entry}
    else{throw '网卡恢复清单包含无效记录。'}
  }
}
function Assert-FirewallValues($actual,$expected,[string]$label) {
  $left=@($actual | ForEach-Object {[string]$_} | Sort-Object -Unique)
  $right=@($expected | ForEach-Object {[string]$_} | Sort-Object -Unique)
  if(!$left.Count -or @(Compare-Object -ReferenceObject $right -DifferenceObject $left).Count){
    throw ('防火墙规则内容不符：'+$label+'。请先使用完整配置。')
  }
}
function ConvertTo-FirewallProgram([string]$program) {
  if($program -eq 'Any'){return 'Any'}
  if([string]::IsNullOrWhiteSpace($program)){throw '防火墙程序路径为空。'}
  return [IO.Path]::GetFullPath([Environment]::ExpandEnvironmentVariables($program))
}
function Assert-FirewallAvailable {
  $services=@(Get-Service -Name BFE,MpsSvc -ErrorAction Stop)
  if($services.Count -ne 2 -or @($services | Where-Object {$_.Status.ToString() -ne 'Running'}).Count){throw 'Windows 防火墙相关服务未运行。'}
  $profiles=@(Get-NetFirewallProfile -PolicyStore ActiveStore -ErrorAction Stop)
  Assert-FirewallValues @($profiles.Name) @('Domain','Private','Public') '配置文件'
  if(@($profiles | Where-Object {$_.Enabled.ToString() -ne 'True'}).Count){throw 'Windows 防火墙未全部启用，请先检查三个网络配置文件的状态。'}
  return $profiles
}
function Assert-StrictFirewall([string]$coreProgram,[switch]$AllowAdditionalLocalRules) {
  # Read effective policy as well as persistent rules; names alone prove nothing.
  $profiles=@(Assert-FirewallAvailable)
  if(@($profiles | Where-Object {$_.DefaultOutboundAction.ToString() -ne 'Block'}).Count){
    throw '实际默认出站动作不是 Block，无法确认严格防火墙生效。'
  }
  $core=ConvertTo-FirewallProgram $coreProgram
  if(!(Test-Path -LiteralPath $core -PathType Leaf)){throw '配置记录中的核心程序不存在。'}
  $running=@(Get-CimInstance -ClassName Win32_Process -Filter "Name = 'verge-mihomo.exe'" -ErrorAction Stop | Select-Object -ExpandProperty ExecutablePath -Unique)
  if($running.Count -ne 1 -or [string]::IsNullOrWhiteSpace([string]$running[0]) -or (ConvertTo-FirewallProgram ([string]$running[0])) -ine $core){
    throw '运行中的核心路径与配置记录不一致或无法唯一确认。'
  }
  $tun=@(Get-NetAdapter -ErrorAction Stop | Where-Object {$_.Name -ceq 'Meta'})
  if($tun.Count -ne 1 -or $tun[0].Status.ToString() -ne 'Up'){throw 'Meta TUN 网卡不存在、不唯一或未启用。'}
  $lan=@('10.0.0.0/8','100.64.0.0/10','127.0.0.0/8','169.254.0.0/16','172.16.0.0/12','192.168.0.0/16','224.0.0.0/4')
  foreach($store in @('PersistentStore','ActiveStore')){
    $all=@(Get-NetFirewallRule -PolicyStore $store -ErrorAction Stop)
    $project=@($all | Where-Object {$_.Name -like 'CCN-Portable-*' -or $_.DisplayName -like 'Claude network *'})
    if($project.Count -ne 4){throw ('项目规则不完整或有多余规则：'+$store)}
    foreach($role in @('Core','TUN','LAN','DHCP')){
      $rules=@($project | Where-Object {$_.Name -eq ('CCN-Portable-'+$role) -or $_.DisplayName -eq ('Claude network '+$role+' outbound')})
      if($rules.Count -ne 1){throw ('项目规则无法唯一确认：'+$role)}
      $rule=$rules[0]
      if($rule.Enabled.ToString() -ne 'True' -or $rule.Direction.ToString() -ne 'Outbound' -or $rule.Action.ToString() -ne 'Allow' -or $rule.Profile.ToString() -ne 'Any'){
        throw ('项目规则状态或适用范围不符：'+$role)
      }
      $application=@($rule | Get-NetFirewallApplicationFilter -ErrorAction Stop)
      $address=@($rule | Get-NetFirewallAddressFilter -ErrorAction Stop)
      $port=@($rule | Get-NetFirewallPortFilter -ErrorAction Stop)
      $service=@($rule | Get-NetFirewallServiceFilter -ErrorAction Stop)
      $interface=@($rule | Get-NetFirewallInterfaceFilter -ErrorAction Stop)
      foreach($filter in @($application,$address,$port,$service,$interface)){
        if(@($filter).Count -ne 1){throw ('规则筛选条件不唯一：'+$role)}
      }
      $program='Any';$remote=@('Any');$alias=@('Any');$svc='Any';$localPort='Any';$remotePort='Any'
      if($role -eq 'Core'){$program=$core}
      if($role -eq 'TUN'){$alias=@([string]$tun[0].Name)}
      if($role -eq 'LAN'){$remote=$lan}
      if($role -eq 'DHCP'){$program=ConvertTo-FirewallProgram (Join-Path $env:SystemRoot 'System32\svchost.exe');$svc='Dhcp';$localPort='68';$remotePort='67'}
      Assert-FirewallValues @((ConvertTo-FirewallProgram ([string]$application[0].Program))) @($program) ($role+' 程序')
      Assert-FirewallValues @($address[0].LocalAddress) @('Any') ($role+' 本地地址')
      Assert-FirewallValues @($address[0].RemoteAddress) $remote ($role+' 远程地址')
      Assert-FirewallValues @($interface[0].InterfaceAlias) $alias ($role+' 网卡')
      Assert-FirewallValues @($service[0].Service) @($svc) ($role+' 服务')
      Assert-FirewallValues @($port[0].LocalPort) @($localPort) ($role+' 本地端口')
      Assert-FirewallValues @($port[0].RemotePort) @($remotePort) ($role+' 远程端口')
      $protocol=[string]$port[0].Protocol
      if(($role -eq 'DHCP' -and $protocol -notin @('UDP','17')) -or ($role -ne 'DHCP' -and $protocol -notin @('Any','256'))){throw ('规则协议不符：'+$role)}
    }
    if($store -eq 'ActiveStore' -and !$AllowAdditionalLocalRules){
      $extra=@($all | Where-Object {$_.Enabled.ToString() -eq 'True' -and $_.Direction.ToString() -eq 'Outbound' -and $_.Action.ToString() -eq 'Allow' -and $_.Name -notin $project.Name})
      if($extra.Count){throw '实际生效策略仍有非项目出站放行规则，补充配置未完成。'}
    }
  }
}
# NetSecurity 的 cmdlet 化封装：带过滤条件却查不到任何对象时，Get-NetFirewallRule
# 不返回空集，而是抛出 CmdletizationQuery_NotFound（CimJobException）。应用防火
# 墙的过程中这个空状态是正常中间态——禁用完最后一非项目放行规则、项目自己的 4
# 条规则还没创建出来之前，"仍启用的出站放行规则"就该一条不剩。这里把"查询没有
# 匹配对象"映射成空结果，真正的 CIM 故障依旧抛出，不会被静默吞掉。
function Get-EnabledOutboundAllowRules {
 param([string]$PolicyStore='PersistentStore')
 try{
  return @(Get-NetFirewallRule -PolicyStore $PolicyStore -Direction Outbound -Action Allow -Enabled True -ErrorAction Stop)
 }catch{
  if("$($_.FullyQualifiedErrorId)" -like 'CmdletizationQuery_NotFound*'){return @()}
  throw
 }
}
function Disable-NonProjectOutboundRules($saved,[string]$stateDir,[object[]]$Targets) {
  $rules=if($PSBoundParameters.ContainsKey('Targets')){@($Targets)}else{@(Get-EnabledOutboundAllowRules | Where-Object {$_.Name -notlike 'CCN-Portable-*' -and $_.DisplayName -notlike 'Claude network *'})}
  $rules=@($rules | Where-Object {$null -ne $_})
  $newRules=@($rules | Where-Object {$_.Name -notin $saved.enabledOutboundRules})
  $journal=Join-Path $stateDir 'firewall-disabled-by-apply.json'
  $names=@()
  if(Test-Path -LiteralPath $journal){$names=Get-Content -LiteralPath $journal -Raw -Encoding UTF8 | ConvertFrom-Json}
  $names=@(ConvertTo-RuleNameList $names)
  $names=@(@($names)+@($newRules | Select-Object -ExpandProperty Name) | Where-Object {$null -ne $_} | Sort-Object -Unique)
  # Persist before disabling; keep the sealed first baseline unchanged. Accumulate
  # across retries so a later apply cannot lose the names needed for restoration.
  if($names.Count){
    $temporary=$journal+'.tmp'
    ConvertTo-Json -InputObject @($names) | Set-Content -LiteralPath $temporary -Encoding UTF8
    Move-Item -LiteralPath $temporary -Destination $journal -Force
  }
  Write-Host ('  自动禁用 '+$rules.Count+' 条非项目出站放行规则（其中新增 '+$newRules.Count+' 条）。')
  if($rules.Count){$rules | Disable-NetFirewallRule -ErrorAction Stop}
  # 项目自己的 4 条放行规则此时还没创建，查询结果为空是预期状态，不能当故障。
  $remaining=@(Get-EnabledOutboundAllowRules | Where-Object {$_.Name -notlike 'CCN-Portable-*' -and $_.DisplayName -notlike 'Claude network *'})
  if($remaining.Count){throw '仍有非项目出站放行规则启用，严格防火墙配置失败。'}
}

function Restore-AdditionalOutboundRules([string]$stateDir) {
  $journal=Join-Path $stateDir 'firewall-disabled-by-apply.json'
  if(!(Test-Path -LiteralPath $journal)){return}
  $names=Get-Content -LiteralPath $journal -Raw -Encoding UTF8 | ConvertFrom-Json
  $names=@(ConvertTo-RuleNameList $names)
  # Exact membership avoids wildcard interpretation of application rule names.
  Get-NetFirewallRule -PolicyStore PersistentStore -ErrorAction Stop | Where-Object {$_.Name -in $names -and $_.Direction.ToString() -eq 'Outbound' -and $_.Action.ToString() -eq 'Allow' -and $_.Name -notlike 'CCN-Portable-*' -and $_.DisplayName -notlike 'Claude network *'} | Enable-NetFirewallRule -ErrorAction Stop
}

function Get-ProjectFirewallState($rule) {
  $app=@($rule | Get-NetFirewallApplicationFilter -ErrorAction Stop)
  $address=@($rule | Get-NetFirewallAddressFilter -ErrorAction Stop)
  $port=@($rule | Get-NetFirewallPortFilter -ErrorAction Stop)
  $service=@($rule | Get-NetFirewallServiceFilter -ErrorAction Stop)
  $interface=@($rule | Get-NetFirewallInterfaceFilter -ErrorAction Stop)
  $type=@($rule | Get-NetFirewallInterfaceTypeFilter -ErrorAction Stop)
  $security=@($rule | Get-NetFirewallSecurityFilter -ErrorAction Stop)
  $spec=[ordered]@{direction=[string]$rule.Direction;action=[string]$rule.Action;profile=[string]$rule.Profile;
    program=[string]$app[0].Program;package=[string]$app[0].Package;
    localAddress=@($address[0].LocalAddress | Sort-Object);remoteAddress=@($address[0].RemoteAddress | Sort-Object);
    protocol=[string]$port[0].Protocol;localPort=@($port[0].LocalPort | Sort-Object);remotePort=@($port[0].RemotePort | Sort-Object);
    service=[string]$service[0].Service;interfaces=@($interface[0].InterfaceAlias | Sort-Object);interfaceType=[string]$type[0].InterfaceType;
    authentication=[string]$security[0].Authentication;encryption=[string]$security[0].Encryption;
    localUser=[string]$security[0].LocalUser;remoteUser=[string]$security[0].RemoteUser;remoteMachine=[string]$security[0].RemoteMachine}
  return [pscustomobject]@{name=[string]$rule.Name;enabled=[string]$rule.Enabled;fingerprint=($spec | ConvertTo-Json -Depth 5 -Compress)}
}
