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
 foreach($role in @('Core','TUN','LAN','DHCP')){
  [pscustomobject]@{Name=('CCN-Portable-'+$role);DisplayName=('Claude network '+$role+' outbound');Role=$role;Store=$PolicyStore;Enabled='True';Direction='Outbound';Action='Allow';Profile='Any'}
 }
 if($script:change -eq 'extra' -and $PolicyStore -eq 'ActiveStore'){
  [pscustomobject]@{Name='extra';DisplayName='fixture';Enabled='True';Direction='Outbound';Action='Allow'}
 }
}
function Get-NetFirewallApplicationFilter {
 param([Parameter(ValueFromPipeline=$true)]$Rule)
 process {
  $program='Any'
  if($Rule.Role -eq 'Core' -and $script:change -ne 'core'){$program=$core}
  if($Rule.Role -eq 'DHCP'){$program=Join-Path $env:SystemRoot 'System32\svchost.exe'}
  [pscustomobject]@{Program=$program}
 }
}
function Get-NetFirewallAddressFilter {
 param([Parameter(ValueFromPipeline=$true)]$Rule)
 process {
  $remote=@('Any')
  if($Rule.Role -eq 'LAN' -and $script:change -ne 'lan'){$remote=@('10.0.0.0/8','100.64.0.0/10','127.0.0.0/8','169.254.0.0/16','172.16.0.0/12','192.168.0.0/16','224.0.0.0/4')}
  [pscustomobject]@{LocalAddress='Any';RemoteAddress=$remote}
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
 process {$svc=if($Rule.Role -eq 'DHCP'){'Dhcp'}else{'Any'};[pscustomobject]@{Service=$svc}}
}
function Get-NetFirewallInterfaceFilter {
 param([Parameter(ValueFromPipeline=$true)]$Rule)
 process {$alias=if($Rule.Role -eq 'TUN' -and $script:change -ne 'tun'){'Meta'}else{'Any'};[pscustomobject]@{InterfaceAlias=$alias}}
}
Assert-StrictFirewall -coreProgram $core
$script:cases++
foreach($case in @('disabled','core','lan','dhcp','tun','extra')){
 $script:change=$case
 $rejected=$false
 try{Assert-StrictFirewall -coreProgram $core}catch{$rejected=$true}
 if(!$rejected){throw ('Unsafe fixture accepted: '+$case)}
 $script:cases++
}
$script:change='extra'
Assert-StrictFirewall -coreProgram $core -AllowAdditionalLocalRules
$script:cases++
Write-Output ('Offline firewall cases passed: '+$script:cases)
