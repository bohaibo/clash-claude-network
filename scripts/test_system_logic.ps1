# Offline tests: import only function ASTs, then mock every system operation.
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'firewall_rules.ps1')
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'system.ps1'),[ref]$tokens,[ref]$errors)
foreach($name in @('Find-Adapter','Restore-RecordedIpv6','Restore-Firewall','Disable-CurrentIpv6Bindings')){
 $function=$ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst]},$true) | Where-Object {$_.Name -eq $name}
 Invoke-Expression $function.Extent.Text
}
$fixture=Join-Path (Split-Path -Parent $PSScriptRoot) ('.local/system-test-'+[Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $fixture | Out-Null
$stateDir=$fixture;$script:cases=0;$script:registry=$null;$script:fail=$false
function Get-NetAdapter { [CmdletBinding()]param([switch]$IncludeHidden) if(!$IncludeHidden){throw 'Hidden adapters must be included'};$script:adapters }
function Set-AdapterIpv6Binding($adapter,[bool]$enabled) {if($script:fail){throw 'Fixture failure'};$adapter.Enabled=$enabled}
function Get-AdapterIpv6Enabled($adapter) {$adapter.Enabled}
function New-ItemProperty {param($LiteralPath,$Name,$PropertyType,$Value,[switch]$Force) $script:registry=$Value}
function Remove-ItemProperty {[CmdletBinding()]param($LiteralPath,$Name) $script:registry=$null}
function Assert-Case($condition,$label){if(!$condition){throw ('Failed: '+$label)};$script:cases++}
$script:adapters=@([pscustomobject]@{InterfaceGuid='original';Name='same-name';InterfaceDescription='same-description';Enabled=$true},
 [pscustomobject]@{InterfaceGuid='later';Name='new';InterfaceDescription='new';Enabled=$false},
 [pscustomobject]@{InterfaceGuid='unrecorded';Name='other';InterfaceDescription='other';Enabled=$false})
Assert-Case ($null -eq (Find-Adapter 'missing' 'same-name' 'same-description')) 'GUID does not fall back to a replacement adapter'
Assert-Case ((Find-Adapter 'original' 'old-name' '').InterfaceGuid -eq 'original') 'GUID survives renaming'
@(@{guid='original';name='same-name'},@{guid='later';name='new'}) | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $fixture 'disabled-by-apply.json') -Encoding UTF8
$saved=[pscustomobject]@{ipv6=[pscustomobject]@{exists=$true;value=255};bindings=@([pscustomobject]@{guid='original';name='same-name';enabled=$false})}
Restore-RecordedIpv6 $saved $fixture
Assert-Case ($script:registry -eq 255) 'intentionally disabled global IPv6 stays disabled'
Assert-Case (!$script:adapters[0].Enabled) 'baseline binding wins over the change journal'
Assert-Case ($script:adapters[1].Enabled) 'later recorded adapter restored'
Assert-Case (!$script:adapters[2].Enabled) 'unrecorded adapter untouched'
$oldJournal=Join-Path $fixture 'disabled-by-apply.json'
('['+(Get-Content -LiteralPath $oldJournal -Raw -Encoding UTF8)+']') | Set-Content -LiteralPath $oldJournal -Encoding UTF8
$script:adapters[1].Enabled=$false;Restore-RecordedIpv6 $saved $fixture
Assert-Case ($script:adapters[1].Enabled) 'legacy nested adapter journal restored'
$script:adapters[0].Enabled=$true;$script:adapters[2].Enabled=$true
Disable-CurrentIpv6Bindings
Assert-Case (!@($script:adapters | Where-Object {$_.Enabled}).Count) 'all adapters including hidden and virtual are disabled'
$recorded=Get-Content -LiteralPath (Join-Path $fixture 'disabled-by-apply.json') -Raw -Encoding UTF8 | ConvertFrom-Json
Assert-Case (@($recorded).Count -eq 3) 'every changed adapter is journaled without dropping earlier records'
Restore-RecordedIpv6 $saved $fixture
Assert-Case (!$script:adapters[0].Enabled -and $script:adapters[1].Enabled -and $script:adapters[2].Enabled) 'restore covers baseline and supplemental adapters'
$script:fail=$true;$rejected=$false
try{Restore-RecordedIpv6 $saved $fixture}catch{$rejected=$true}
Assert-Case $rejected 'binding failure is reported';$script:fail=$false
$script:enabled=@();$script:removed=@();$script:profileChanges=0
function Get-NetFirewallRule {[CmdletBinding()]param($PolicyStore) $script:rules}
function Get-ProjectFirewallState($rule) {[pscustomobject]@{fingerprint=$rule.Fingerprint}}
function Set-NetFirewallProfile {[CmdletBinding()]param($Name,$DefaultOutboundAction) $script:profileChanges++}
function Enable-NetFirewallRule {[CmdletBinding()]param([Parameter(ValueFromPipeline=$true)]$Rule) process{$script:enabled+=@($Rule.Name)}}
function Disable-NetFirewallRule {[CmdletBinding()]param([Parameter(ValueFromPipeline=$true)]$Rule) process{}}
function Remove-NetFirewallRule {[CmdletBinding()]param([Parameter(ValueFromPipeline=$true)]$Rule) process{$script:removed+=@($Rule.Name)}}
function Restore-AdditionalOutboundRules($directory) {}
$script:rules=@([pscustomobject]@{Name='app*literal';DisplayName='App';Direction='Outbound';Action='Allow'},
 [pscustomobject]@{Name='appXliteral';DisplayName='Other';Direction='Outbound';Action='Allow'},
 [pscustomobject]@{Name='CCN-Portable-Core';DisplayName='Claude network core outbound';Direction='Outbound';Action='Allow';Fingerprint='original'})
$original=[pscustomobject]@{enabledOutboundRules=@('app*literal');firewallProfiles=@();projectFirewallRules=@()}
Restore-Firewall $original
Assert-Case ('app*literal' -in $script:enabled -and 'appXliteral' -notin $script:enabled) 'exact rule IDs instead of wildcard matching'
Assert-Case ('CCN-Portable-Core' -in $script:removed) 'new project rule removed for original baseline'
$script:removed=@();$original.projectFirewallRules=@([pscustomobject]@{name='CCN-Portable-Core';enabled='True';fingerprint='original'})
Restore-Firewall $original
Assert-Case (!$script:removed.Count) 'manual snapshot retains its own project rule'
$original.projectFirewallRules[0].fingerprint='changed';$rejected=$false
try{Restore-Firewall $original}catch{$rejected=$true}
Assert-Case $rejected 'manual rule drift stops restoration'
$script:rules=@();$rejected=$false
try{Restore-Firewall $original}catch{$rejected=$true}
Assert-Case $rejected 'missing manual snapshot project rule is not silently lost'
$firewallAst=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'firewall_rules.ps1'),[ref]$tokens,[ref]$errors)
foreach($name in @('Restore-AdditionalOutboundRules','Disable-NonProjectOutboundRules')){
 $function=$firewallAst.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst]},$true) | Where-Object {$_.Name -eq $name}
 Invoke-Expression $function.Extent.Text
}
$script:rules=@('later-one','later-two','later-three') | ForEach-Object {[pscustomobject]@{Name=$_;DisplayName='Fixture app';Direction='Outbound';Action='Allow';Enabled=$false}}
ConvertTo-Json -InputObject @('later-one','later-two') | Set-Content -LiteralPath (Join-Path $fixture 'firewall-disabled-by-apply.json') -Encoding UTF8
$script:enabled=@();Restore-AdditionalOutboundRules $fixture
Assert-Case ($script:enabled.Count -eq 2 -and 'later-one' -in $script:enabled -and 'later-two' -in $script:enabled) 'multiple supplemental firewall records restored'
$oldJournal=Join-Path $fixture 'firewall-disabled-by-apply.json'
'[["later-one","later-two"]]' | Set-Content -LiteralPath $oldJournal -Encoding UTF8
$script:enabled=@();Restore-AdditionalOutboundRules $fixture
Assert-Case ($script:enabled.Count -eq 2) 'legacy nested firewall journal restored'
function Get-EnabledOutboundAllowRules { @($script:rules | Where-Object {$_.Enabled -eq $true}) }
function Disable-NetFirewallRule {[CmdletBinding()]param([Parameter(ValueFromPipeline=$true)]$Rule) process{$Rule.Enabled=$false}}
$script:rules[2].Enabled=$true
Disable-NonProjectOutboundRules ([pscustomobject]@{enabledOutboundRules=@()}) $fixture
$journal=Get-Content -LiteralPath (Join-Path $fixture 'firewall-disabled-by-apply.json') -Raw -Encoding UTF8 | ConvertFrom-Json
Assert-Case ($journal.Count -eq 3 -and 'later-one' -in $journal -and 'later-three' -in $journal) 'supplemental records accumulate without nested arrays'
$script:adapters=@([pscustomobject]@{Name='no-guid-one';InterfaceGuid='';InterfaceDescription='one';Enabled=$true},
 [pscustomobject]@{Name='no-guid-two';InterfaceGuid='';InterfaceDescription='two';Enabled=$true})
'[]' | Set-Content -LiteralPath (Join-Path $fixture 'disabled-by-apply.json') -Encoding UTF8
Disable-CurrentIpv6Bindings
$recorded=Get-Content -LiteralPath (Join-Path $fixture 'disabled-by-apply.json') -Raw -Encoding UTF8 | ConvertFrom-Json
Assert-Case ($recorded.Count -eq 2) 'adapters without GUIDs still get distinct records'
Restore-RecordedIpv6 ([pscustomobject]@{ipv6=[pscustomobject]@{exists=$false};bindings=@()}) $fixture
Assert-Case (@($script:adapters | Where-Object {$_.Enabled}).Count -eq 2) 'recorded adapters without GUIDs are restored by unique identity'
foreach($name in @('Get-AdapterIpv6Binding','Get-AdapterIpv6Enabled','Set-AdapterIpv6Binding')){
 $function=$ast.FindAll({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst]},$true) | Where-Object {$_.Name -eq $name}
 Invoke-Expression $function.Extent.Text
}
$script:adapters=@([pscustomobject]@{InterfaceGuid='wildcard';Name='Local* 1'},[pscustomobject]@{InterfaceGuid='other';Name='LocalX 1'})
$script:bindings=@([pscustomobject]@{Name='Local* 1';Enabled=$true},[pscustomobject]@{Name='LocalX 1';Enabled=$true})
function Get-NetAdapterBinding {
 [CmdletBinding()]param([switch]$IncludeHidden,[switch]$AllBindings,$ComponentID)
 if(!$IncludeHidden -or !$AllBindings -or $ComponentID -ne 'ms_tcpip6'){throw 'All IPv6 bindings must be enumerated'}
 $script:bindings
}
function Disable-NetAdapterBinding {[CmdletBinding(SupportsShouldProcess=$true)]param([Parameter(ValueFromPipeline=$true)]$InputObject) process{$InputObject.Enabled=$false}}
function Enable-NetAdapterBinding {[CmdletBinding(SupportsShouldProcess=$true)]param([Parameter(ValueFromPipeline=$true)]$InputObject) process{$InputObject.Enabled=$true}}
Set-AdapterIpv6Binding $script:adapters[0] $false
Assert-Case (!(Get-AdapterIpv6Enabled $script:adapters[0]) -and $script:bindings[1].Enabled) 'wildcard-like adapter names never affect another adapter'
Set-AdapterIpv6Binding $script:adapters[0] $true
Assert-Case (Get-AdapterIpv6Enabled $script:adapters[0]) 'hidden binding instance can be restored'
Write-Output ('Offline system cases passed: '+$script:cases)
