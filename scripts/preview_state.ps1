[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
# Read-only GUI inventory. Never return proxy addresses or credentials.
$ErrorActionPreference='Stop'
function Read-Value($key,$name) {
 try {
  if(!(Test-Path -LiteralPath $key -ErrorAction Stop)){return '未设置'}
  $item=Get-Item -LiteralPath $key -ErrorAction Stop
  if($name -notin $item.GetValueNames()){return '未设置'}
  return [string]$item.GetValue($name)
 } catch { return '读取失败（未验证）' }
}
function Service-State($name) {
 try { $s=Get-Service $name -ErrorAction Stop; return "$($s.StartType) / $($s.Status)" } catch { return '未能读取（未验证）' }
}
$result=[ordered]@{}
$result.timezone=(Get-TimeZone).Id
$result.culture=(Get-ItemProperty -LiteralPath 'HKCU:\Control Panel\International' -Name LocaleName -ErrorAction Stop).LocaleName
$result.culture_runtime=(Get-Culture).Name
$result.culture_restart_required=($result.culture -ine $result.culture_runtime)
$geo=(Get-WinHomeLocation).GeoId
$region=[Globalization.CultureInfo]::GetCultures('SpecificCultures') | ForEach-Object {try{[Globalization.RegionInfo]::new($_.Name)}catch{}} | Where-Object {$_.GeoId -eq $geo} | Select-Object -First 1
$result.country=if($region){$region.DisplayName+'（'+$region.TwoLetterISORegionName+'；GeoID '+$geo+'）'}else{'GeoID '+$geo}
$result.location=Read-Value 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\LocationAndSensors' 'DisableLocation'
if($result.location -eq '未设置'){$result.location='未配置禁用定位策略（不等同于应用已获定位权限）'}
elseif($result.location -eq '1'){$result.location='已通过策略关闭定位'}
elseif($result.location -eq '0'){$result.location='策略允许定位（应用权限另行决定）'}
$result.auto_timezone=Service-State 'tzautoupdate'
$result.auto_time=(Service-State 'W32Time')+' / '+(Read-Value 'HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\Parameters' 'Type')
$result.ipv6=Read-Value 'HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip6\Parameters' 'DisabledComponents'
if($result.ipv6 -eq '未设置'){$result.ipv6='未设置禁用项（Windows 默认启用 IPv6）'}
try { $b=@(Get-NetAdapterBinding -ComponentID ms_tcpip6 -IncludeHidden -AllBindings -ErrorAction Stop); $result.bindings="IPv6 已启用 $(@($b|Where-Object Enabled).Count) / $($b.Count) 个绑定" } catch { $result.bindings='未能读取（未验证）' }
try { $result.firewall=(@(Get-NetFirewallProfile -PolicyStore ActiveStore -ErrorAction Stop | ForEach-Object { $action=if($_.DefaultOutboundAction -eq 'NotConfigured'){'未显式设置（Windows 默认允许）'}else{[string]$_.DefaultOutboundAction}; "$($_.Name)：出站 $action；防火墙启用=$($_.Enabled)" }) -join '；') } catch {$result.firewall='未能读取（未验证）'}
$result.proxy='ProxyEnable='+(Read-Value 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' 'ProxyEnable')
$key='HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
foreach($name in @('ProxyServer','ProxyOverride','AutoConfigURL')){
 $value=Read-Value $key $name
 $result.proxy+='；'+$name+'='+$(if($value -eq '未设置' -or !$value){'未设置'}elseif($value -eq '读取失败（未验证）'){$value}else{'已设置（值隐藏）'})
}
$proxyNames=@('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY')
$result.environment=(@('User','Machine') | ForEach-Object { $scope=$_; $fields=@($proxyNames | ForEach-Object {$_+'='+$(if([Environment]::GetEnvironmentVariable($_,$scope)){'已设置（值隐藏）'}else{'未设置'})}); "$scope : $($fields -join '，')" }) -join '；'
try { $key=Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Internet Settings\Connections' -Name WinHttpSettings -ErrorAction Stop; $result.winhttp="已读取 WinHTTP 设置（$($key.WinHttpSettings.Length) 字节；地址隐藏）" } catch { $result.winhttp='无 WinHTTP 注册设置或无法读取（未验证）' }
$result.country_code=if($region){$region.TwoLetterISORegionName}else{''}
$result.timezone_label=(Get-TimeZone).DisplayName
$result.location_state='未能确认'
$policy=Read-Value 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\LocationAndSensors' 'DisableLocation'
$consent=Read-Value 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\location' 'Value'
if($policy -eq '1' -or $consent -eq 'Deny'){$result.location_state='关闭'}
elseif($consent -eq 'Allow'){$result.location_state='开启'}
try {
 $tz=Get-Service tzautoupdate -ErrorAction Stop
 $result.auto_timezone_state=if($tz.StartType -eq 'Automatic'){'开启'}else{'关闭'}
} catch {$result.auto_timezone_state='未能确认'}
try {
 $time=Get-Service W32Time -ErrorAction Stop
 $type=Read-Value 'HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\Parameters' 'Type'
 $result.auto_time_state=if($time.StartType -eq 'Disabled' -or $type -eq 'NoSync'){'关闭'}elseif($type -in @('NTP','NT5DS','AllSync')){'开启'}else{'未能确认'}
} catch {$result.auto_time_state='未能确认'}
$v=Read-Value 'HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip6\Parameters' 'DisabledComponents'
$result.ipv6_state=if($v -eq '255'){'关闭'}elseif($v -eq '未设置' -or $v -eq '0'){'开启'}else{'部分关闭或未能确认'}
$result.proxy_state=if((Read-Value 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' 'ProxyEnable') -eq '1'){'开启'}else{'关闭'}
try {
 $profiles=@(Get-NetFirewallProfile -PolicyStore ActiveStore -ErrorAction Stop)
 $blocked=@($profiles | Where-Object {$_.Enabled -eq 'True' -and $_.DefaultOutboundAction -eq 'Block'}).Count
 $result.firewall_state=if($blocked -eq $profiles.Count){'开启'}elseif($blocked -eq 0){'关闭'}else{'部分开启'}
} catch {$result.firewall_state='未能确认'}
try {
 $allBindings=@(Get-NetAdapterBinding -ComponentID ms_tcpip6 -IncludeHidden -AllBindings -ErrorAction Stop)
 $result.ipv6_enabled_bindings=@($allBindings | Where-Object Enabled).Count
 if($result.ipv6_enabled_bindings -gt 0){$result.ipv6_state='未完整关闭'}
} catch {$result.ipv6_enabled_bindings=$null;$result.ipv6_state='未能确认'}
$result | ConvertTo-Json -Compress
