[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
# Read-only GUI inventory. Never return proxy addresses or credentials.
$ErrorActionPreference='Stop'
function Read-Value($key,$name) {
 try { $v=Get-ItemPropertyValue -LiteralPath $key -Name $name -ErrorAction Stop; return [string]$v } catch { return '未设置或不可读取' }
}
function Service-State($name) {
 try { $s=Get-Service $name -ErrorAction Stop; return "$($s.StartType) / $($s.Status)" } catch { return '未能读取（未验证）' }
}
$result=[ordered]@{}
$result.timezone=(Get-TimeZone).Id
$result.culture=(Get-Culture).Name
$result.country=[string](Get-WinHomeLocation).GeoId
$result.location=Read-Value 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\LocationAndSensors' 'DisableLocation'
$result.auto_timezone=Service-State 'tzautoupdate'
$result.auto_time=(Service-State 'W32Time')+' / '+(Read-Value 'HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\Parameters' 'Type')
$result.ipv6=Read-Value 'HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip6\Parameters' 'DisabledComponents'
try { $b=@(Get-NetAdapterBinding -ComponentID ms_tcpip6 -IncludeHidden -AllBindings -ErrorAction Stop); $result.bindings="IPv6 已启用 $(@($b|Where-Object Enabled).Count) / $($b.Count) 个绑定" } catch { $result.bindings='未能读取（未验证）' }
try { $result.firewall=(@(Get-NetFirewallProfile -ErrorAction Stop | ForEach-Object { "$($_.Name): $($_.DefaultOutboundAction)" }) -join '；') } catch {$result.firewall='未能读取（未验证）'}
$result.proxy='ProxyEnable='+(Read-Value 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' 'ProxyEnable')
$proxyNames=@('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY')
$result.environment=(@('User','Machine') | ForEach-Object { $scope=$_; $count=@($proxyNames|Where-Object {[Environment]::GetEnvironmentVariable($_,$scope)}).Count; "$scope : $count 个代理变量" }) -join '；'
try { $key=Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Internet Settings\Connections' -Name WinHttpSettings -ErrorAction Stop; $result.winhttp="已读取 WinHTTP 设置（$($key.WinHttpSettings.Length) 字节；地址隐藏）" } catch { $result.winhttp='无 WinHTTP 注册设置或无法读取（未验证）' }
$result | ConvertTo-Json -Compress
