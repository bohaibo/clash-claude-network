# Offline fixtures only. Every Windows network/service query below is mocked.
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'firewall_rules.ps1')
$fixture=Join-Path (Split-Path -Parent $PSScriptRoot) ('.local/firewall-test-'+[Guid]::NewGuid().ToString('N'))
New-Item -Path $fixture -ItemType Directory -Force | Out-Null
$core=Join-Path $fixture 'fixture-core.exe'
Set-Content -LiteralPath $core -Value 'fixture, not executable'
$script:change='';$script:cases=0
function Get-Service { @('BFE','MpsSvc') | ForEach-Object {[pscustomobject]@{Name=$_;Status='Running'}} }
function Get-NetFirewallProfile {
 @('Domain','Private','Public') | ForEach-Object {[pscustomobject]@{Name=$_;Enabled=($script:change -ne 'disabled');DefaultOutboundAction='Block'}}
}
function Get-CimInstance { [pscustomobject]@{ExecutablePath=$core} }
function Get-NetAdapter { [pscustomobject]@{Name='Meta';Status='Up'} }
function Get-NetFirewallRule {
 param($PolicyStore)
 foreach($role in @('Core','TUN','LAN','DHCP','SharedAccess-Block')){
  if($role -eq 'SharedAccess-Block' -and $script:change -eq 'missing-block'){continue}
  $action=if($role -eq 'SharedAccess-Block'){'Block'}else{'Allow'}
  if($role -eq 'SharedAccess-Block' -and $script:change -eq 'block-allow'){$action='Allow'}
  [pscustomobject]@{Name=('CCN-Portable-'+$role);DisplayName=('Claude network '+$role+' outbound');Role=$role;Store=$PolicyStore;Enabled='True';Direction='Outbound';Action=$action;Profile='Any'}
 }
 if($script:change -in @('extra','policy-extra') -and $PolicyStore -eq 'ActiveStore'){
  $source=if($script:change -eq 'policy-extra'){'GroupPolicy'}else{'Local'}
  [pscustomobject]@{Name='extra';DisplayName='fixture';Enabled='True';Direction='Outbound';Action='Allow';PolicyStoreSourceType=$source}
 }
 if($script:change -like 'dynamic-*' -and $PolicyStore -eq 'ActiveStore'){
  [pscustomobject]@{Name='fixture-dynamic';DisplayName='Internet connection sharing';Role='Dynamic';Enabled='True';Direction='Outbound';Action='Allow';PolicyStoreSourceType='Dynamic'}
 }
}
function Get-NetFirewallApplicationFilter {
 param([Parameter(ValueFromPipeline=$true)]$Rule)
 process {
  $program='Any'
  if($Rule.Role -eq 'Core' -and $script:change -ne 'core'){$program=$core}
  if($Rule.Role -eq 'DHCP'){$program=Join-Path $env:SystemRoot 'System32\svchost.exe'}
  if($Rule.Role -eq 'Dynamic'){$program=Join-Path $env:SystemRoot 'System32\svchost.exe';if($script:change -eq 'dynamic-program'){$program=$core}}
  if($Rule.Role -eq 'SharedAccess-Block' -and $script:change -eq 'block-program'){$program=$core}
  $package=if($Rule.Role -eq 'SharedAccess-Block' -and $script:change -eq 'block-package'){'fixture-package'}else{$null}
  [pscustomobject]@{Program=$program;Package=$package}
 }
}
function Get-NetFirewallAddressFilter {
 param([Parameter(ValueFromPipeline=$true)]$Rule)
 process {
  $remote=@('Any')
  if($Rule.Role -eq 'LAN' -and $script:change -ne 'lan'){$remote=@('10.0.0.0/8','100.64.0.0/10','127.0.0.0/8','169.254.0.0/16','172.16.0.0/12','192.168.0.0/16','224.0.0.0/4')}
  if($Rule.Role -eq 'LAN' -and $script:change -eq 'windows-mask'){
   $remote=@('10.0.0.0/255.0.0.0','100.64.0.0/255.192.0.0','127.0.0.0/255.0.0.0','169.254.0.0/255.255.0.0','172.16.0.0/255.240.0.0','192.168.0.0/255.255.0.0','224.0.0.0/240.0.0.0')
  }
  if($Rule.Role -eq 'LAN' -and $script:change -eq 'windows-range'){$remote[0]='10.0.0.0-10.255.255.255'}
  if($Rule.Role -eq 'LAN' -and $script:change -eq 'split'){$remote=@($remote | Select-Object -Skip 1)+@('10.0.0.0/9','10.128.0.0/9')}
  if($Rule.Role -eq 'LAN' -and $script:change -eq 'widen-mask'){$remote[0]='10.0.0.0/254.0.0.0'}
  if($Rule.Role -eq 'LAN' -and $script:change -eq 'narrow'){$remote[0]='10.0.0.0/9'}
  if($Rule.Role -eq 'LAN' -and $script:change -eq 'missing'){$remote=@($remote | Select-Object -Skip 1)}
  if($Rule.Role -eq 'LAN' -and $script:change -eq 'public-active' -and $Rule.Store -eq 'ActiveStore'){$remote+=@('8.8.8.8')}
  if($Rule.Role -eq 'LAN' -and $script:change -eq 'public-persistent' -and $Rule.Store -eq 'PersistentStore'){$remote+=@('8.8.8.8')}
  if($Rule.Role -eq 'Core' -and $script:change -eq 'local-restricted'){$local='127.0.0.1'}else{$local='Any'}
  if($Rule.Role -eq 'SharedAccess-Block' -and $script:change -eq 'block-range'){$remote=@('10.0.0.0/8')}
  [pscustomobject]@{LocalAddress=$local;RemoteAddress=$remote}
 }
}
function Get-NetFirewallPortFilter {
 param([Parameter(ValueFromPipeline=$true)]$Rule)
 process {
  if($Rule.Role -eq 'DHCP'){
   $port=if($script:change -eq 'dhcp'){'Any'}else{'67'}
   [pscustomobject]@{Protocol='UDP';LocalPort='68';RemotePort=$port}
  }else{[pscustomobject]@{Protocol='Any';LocalPort='Any';RemotePort='Any'}}
 }
}
function Get-NetFirewallServiceFilter {
 param([Parameter(ValueFromPipeline=$true)]$Rule)
 process {
  $svc=if($Rule.Role -eq 'DHCP'){'Dhcp'}elseif($Rule.Role -in @('SharedAccess-Block','Dynamic')){'SharedAccess'}else{'Any'}
  if($Rule.Role -eq 'SharedAccess-Block' -and $script:change -eq 'block-service'){$svc='Any'}
  if($Rule.Role -eq 'Dynamic' -and $script:change -eq 'dynamic-service'){$svc='Any'}
  [pscustomobject]@{Service=$svc}
 }
}
function Get-NetFirewallSecurityFilter {
 param([Parameter(ValueFromPipeline=$true)]$Rule)
 process {
  $bypass=if(($Rule.Role -eq 'Dynamic' -and $script:change -eq 'dynamic-bypass') -or ($Rule.Role -eq 'TUN' -and $script:change -eq 'project-bypass')){'True'}else{'False'}
  $auth=if(($Rule.Role -eq 'Dynamic' -and $script:change -eq 'dynamic-auth') -or ($Rule.Role -eq 'SharedAccess-Block' -and $script:change -eq 'block-auth')){'Required'}else{'NotRequired'}
  $user=if($Rule.Role -eq 'SharedAccess-Block' -and $script:change -eq 'block-user'){'fixture-user'}else{'Any'}
  [pscustomobject]@{OverrideBlockRules=$bypass;Authentication=$auth;Encryption='NotRequired';LocalUser=$user;RemoteUser='Any';RemoteMachine='Any'}
 }
}
function Get-NetFirewallInterfaceFilter {
 param([Parameter(ValueFromPipeline=$true)]$Rule)
 process {$alias=if($Rule.Role -eq 'TUN' -and $script:change -ne 'tun'){'Meta'}else{'Any'};[pscustomobject]@{InterfaceAlias=$alias}}
}
function Get-NetFirewallInterfaceTypeFilter {
 param([Parameter(ValueFromPipeline=$true)]$Rule)
 process {$type=if($script:change -eq 'block-interface-type'){'Wireless'}else{'Any'};[pscustomobject]@{InterfaceType=$type}}
}
Assert-StrictFirewall -coreProgram $core
$script:cases++
foreach($case in @('windows-mask','windows-range','split','dynamic-covered')){
 $script:change=$case
 Assert-StrictFirewall -coreProgram $core
 $script:cases++
}
foreach($case in @('disabled','core','lan','dhcp','tun','extra','policy-extra','widen-mask','narrow','missing','public-active','public-persistent','local-restricted','missing-block','block-allow','block-service','block-program','block-range','block-package','block-auth','block-user','block-interface-type','project-bypass','dynamic-bypass','dynamic-service','dynamic-program','dynamic-auth')){
 $script:change=$case
 $rejected=$false
 try{Assert-StrictFirewall -coreProgram $core}catch{$rejected=$true}
 if(!$rejected){throw ('Unsafe fixture accepted: '+$case)}
 $script:cases++
}
$script:change='extra'
Assert-StrictFirewall -coreProgram $core -AllowAdditionalLocalRules
$script:cases++
foreach($case in @('dynamic-bypass','policy-extra')){
 $script:change=$case
 $rejected=$false
 try{Assert-StrictFirewall -coreProgram $core -AllowAdditionalLocalRules}catch{$rejected=$true}
 if(!$rejected){throw 'Supplement accepted an uncovered dynamic or policy allow'}
 $script:cases++
}
# Independent expected intervals exercise unsigned boundaries and set merging.
$normalizations=@(
 @{Value=@('0.0.0.0/0');Expected=@('ipv4:0-4294967295')},
 @{Value=@('0.0.0.0/0.0.0.0');Expected=@('ipv4:0-4294967295')},
 @{Value=@('255.255.255.255/32');Expected=@('ipv4:4294967295-4294967295')},
 @{Value=@('255.255.255.255/255.255.255.255');Expected=@('ipv4:4294967295-4294967295')},
 @{Value=@('255.255.255.254/31');Expected=@('ipv4:4294967294-4294967295')},
 @{Value=@('128.0.0.0/1');Expected=@('ipv4:2147483648-4294967295')},
 @{Value=@('10.5.6.7/255.0.0.0');Expected=@('ipv4:167772160-184549375')},
 @{Value=@('10.128.0.0/9','10.0.0.0/9','10.2.3.4','10.128.0.0/9');Expected=@('ipv4:167772160-184549375')},
 @{Value=@('0.0.0.1','255.255.255.255','0.0.0.0');Expected=@('ipv4:0-1','ipv4:4294967295-4294967295')},
 @{Value=@(' any ');Expected=@('keyword:Any')}
)
foreach($test in $normalizations){
 $observed=@(ConvertTo-FirewallAddressSet $test.Value)
 if(@(Compare-Object $test.Expected $observed).Count){throw 'Address normalization fixture failed'}
 $script:cases++
}
foreach($invalid in @('','256.0.0.0','10.0.0.1/33','10.0.0.1/-1','10.0.0.1/255.0.255.0','10.0.0.1/255.255.255.1','10.0.0.1/8/8','10.0.0.2-10.0.0.1','10.0.0.1-10.0.0.2-10.0.0.3','127.1','010.0.0.1','0x0a000001','LocalSubnet','::/0')){
 $rejected=$false
 try{Assert-FirewallAddresses @($invalid) @('10.0.0.0/8') 'fixture'}catch{$rejected=$true}
 if(!$rejected){throw 'Invalid address fixture accepted'}
 $script:cases++
}
foreach($empty in @(@(),@($null),@(123))){
 $rejected=$false
 try{Assert-FirewallAddresses $empty @('Any') 'fixture'}catch{$rejected=$true}
 if(!$rejected){throw 'Empty or non-string address fixture accepted'}
 $script:cases++
}
# Any also includes IPv6, so an IPv4 /0 must never substitute for it.
$rejected=$false
try{Assert-FirewallAddresses @('0.0.0.0/0') @('Any') 'fixture'}catch{$rejected=$true}
if(!$rejected){throw 'IPv4-only range accepted in place of Any'}
$script:cases++
Write-Output ('Offline firewall cases passed: '+$script:cases)
