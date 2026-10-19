# Internal helpers; callers supply the active snapshot. No live changes on import.
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
function Disable-NonProjectOutboundRules($saved,[string]$stateDir) {
  $rules=@(Get-EnabledOutboundAllowRules | Where-Object {$_.Name -notlike 'CCN-Portable-*' -and $_.DisplayName -notlike 'Claude network *'})
  $newRules=@($rules | Where-Object {$_.Name -notin $saved.enabledOutboundRules})
  $journal=Join-Path $stateDir 'firewall-disabled-by-apply.json'
  $names=@()
  if(Test-Path -LiteralPath $journal){$names=@(Get-Content -LiteralPath $journal -Raw -Encoding UTF8 | ConvertFrom-Json)}
  $names=@(@($names)+@($newRules | Select-Object -ExpandProperty Name) | Sort-Object -Unique)
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
  $names=@(Get-Content -LiteralPath $journal -Raw -Encoding UTF8 | ConvertFrom-Json)
  # Exact membership avoids wildcard interpretation of application rule names.
  Get-NetFirewallRule -PolicyStore PersistentStore -ErrorAction Stop | Where-Object {$_.Name -in $names -and $_.Direction.ToString() -eq 'Outbound' -and $_.Action.ToString() -eq 'Allow' -and $_.Name -notlike 'CCN-Portable-*' -and $_.DisplayName -notlike 'Claude network *'} | Enable-NetFirewallRule -ErrorAction Stop
}
