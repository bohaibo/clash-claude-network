param([ValidateSet('apply','supplement','restore','snapshot','list-snapshots')][string]$Action,[string]$Node,[string]$Subscription,[string]$Region='SG',[string]$Snapshot,[switch]$Acknowledged,[switch]$NoPause,[switch]$SkipBrowser)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'firewall_rules.ps1')
. (Join-Path $PSScriptRoot 'operation_guard.ps1')
# 提权后的新控制台默认是 936 代码页，中文和表情会在 PowerShell 与 Python 之间
# 互相解码错误（甚至让 print 直接抛 UnicodeEncodeError）。两端统一成 UTF-8。
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
try{[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;[Console]::ForegroundColor=[ConsoleColor]::White}catch{}
$project=Split-Path -Parent $PSScriptRoot
$local=Join-Path $project '.local';New-Item -Path $local -ItemType Directory -Force | Out-Null
$python=(Get-Command python.exe -ErrorAction Stop).Source
if($python -like '*\Microsoft\WindowsApps\*'){throw '检测到的是 Windows 应用商店的 Python 占位程序。请使用正式安装的 Python，脚本不会自动安装任何软件。'}
& $python -c 'import yaml,sys;sys.exit(0 if sys.version_info >= (3,10) else 1)'
if($LASTEXITCODE -ne 0){throw '需要 Python 3.10 及以上版本，并且已安装 PyYAML。请先阅读 README.md，不要只装一半依赖。'}

function Get-Snapshots {
 $root=Join-Path $local 'snapshots'
 @(Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue | Where-Object {Test-Path -LiteralPath (Join-Path $_.FullName 'manifest.json')} | Sort-Object Name -Descending)
}
function Show-Snapshots {
 $valid=@();$states=@{}
 foreach($candidate in @(Get-Snapshots)){
  try{
   $state=Get-Content -LiteralPath (Join-Path $candidate.FullName 'manifest.json') -Raw -Encoding UTF8 | ConvertFrom-Json
   if(!$state.computer -or !$state.user_sid -or !$state.client_root -or $null -eq $state.sha256){throw 'Incomplete manifest'}
   $valid+=$candidate;$states[$candidate.FullName]=$state
  }catch{Write-Warning ('快照 '+$candidate.Name+' 的清单不完整或无法读取，已跳过。')}
 }
 $list=@($valid)
 if(!$list.Count){Write-Host '还没有可用的快照清单。请先执行菜单 4「保存当前快照」或菜单 1/2「配置」；保存失败时请查看本地错误记录。';return @()}
 $associated=$null;$planFile=Join-Path $local 'plan.json'
 if(Test-Path -LiteralPath $planFile){try{$associated=[IO.Path]::GetFullPath([string](Get-Content -LiteralPath $planFile -Raw -Encoding UTF8 | ConvertFrom-Json).snapshot)}catch{$associated=$null}}
 Write-Host '快照列表（新的在上面；优先辨认配置及补充改动的关联恢复点）：'
 for($i=0;$i -lt $list.Count;$i++){
  $state=$states[$list[$i].FullName]
  $kind=if($state.kind -eq 'manual'){'手动快照'}else{'自动基线'}
  $sealed=($state.extra_sha256 -and $state.extra_sha256.PSObject.Properties['system.json']) -or $state.sha256.PSObject.Properties['system.json']
  $status=if($sealed -and (Test-Path -LiteralPath (Join-Path $list[$i].FullName 'system.json'))){'含已封存系统记录'}else{'系统备份不完整'}
  $tag=if($associated -and $list[$i].FullName -ieq $associated){'  <配置及补充改动关联>'}else{''}
  Write-Host ('  {0}. {1}  {2}  [{3} / {4}]{5}' -f ($i+1),$list[$i].Name,$state.time,$kind,$status,$tag)
 }
 return $list
}

# ---- 进度显示 ----------------------------------------------------------
# 每一步开始前打印一次：进度条 + 第几步/共几步 + 这一步在做什么。
# 步骤标题统一放在 $stepTitles 里，Write-Step 只接收「这一步的说明」，
# 序号自动递增并从标题表取标题，避免标题表和调用点写歪、对不上号。
$stepTitles=@()
$stepNo=0
function Set-StepPlan([string[]]$titles){$script:stepTitles=@($titles);$script:stepNo=0}
function Write-Step([string]$note){
 $script:stepNo++
 $n=$script:stepNo
 $list=@($script:stepTitles)
 $total=$list.Count
 if($total -lt 1){$total=1}
 $title=if($n -le $list.Count){[string]$list[$n-1]}else{'处理中'}
 $filled=[int]((20*($n-1))/$total)
 $bar=('='*$filled).PadRight(20,'.')
 Write-Host ''
 Write-Host ('  ['+$bar+']  第 '+$n+'/'+$total+' 步  '+$title) -ForegroundColor Cyan
 if($note){Write-Host ('             '+$note)}
}
function Write-Done([string]$text){
 Write-Host ''
 Write-Host ('  ['+('='*20)+']  '+$text) -ForegroundColor Green
}
function Get-VerificationFailureMessage([string]$fallback){
 $details=@()
 try{
  $report=Get-Content -LiteralPath (Join-Path $local 'verification.json') -Raw -Encoding UTF8 | ConvertFrom-Json
  if(!$env:CCN_VERIFY_RUN -or $report.run_id -ne $env:CCN_VERIFY_RUN){return $fallback}
  foreach($name in @('git','npm','stun')){
   $item=$report.$name
   if($item -and $item.status -eq 'failed'){
    $title=switch($name){'git'{'GitHub/Git'};'npm'{'npm'};'stun'{'UDP STUN'}}
    if($item.error -eq 'timeout'){$details+=($title+' 检测超时（'+@($item.attempts).Count+' 次尝试）')}
    else{$details+=($title+' 检测失败')}
   }
  }
  if($report.udp_egress -and !$report.udp_egress.verified){$details+='UDP 出口地区或指定节点路由未确认'}
 }catch{return $fallback}
 if($details.Count){return ($fallback+' '+($details -join '；')+'。详情见 .local/verification.json。')}
 return ($fallback+' 详情见 .local/verification.json。')
}
# 配置前的清单：一项一行，下面缩进写这一项可能造成的影响。
function Write-PlanItem([int]$index,[string]$item,[string]$impact){
 Write-Host ('  {0}) {1}' -f $index,$item)
 if($impact){Write-Host ('     影响：'+$impact)}
}

if($Action -eq 'list-snapshots'){Show-Snapshots | Out-Null;return}

# Pick the restore target before elevation so the interactive prompt stays visible.
if($Action -eq 'restore' -and !$Snapshot){
 $list=@(Show-Snapshots)
 if(!$list.Count){throw '没有可恢复的快照。'}
 if($list.Count -eq 1){$Snapshot=$list[0].Name;Write-Host ('只有一份快照，直接使用：'+$Snapshot)}
 else{
  $pick=Read-Host ('请输入要恢复的快照编号 [1-'+$list.Count+']')
  $index=0
  if(![int]::TryParse($pick,[ref]$index) -or $index -lt 1 -or $index -gt $list.Count){throw '快照编号无效。'}
  $Snapshot=$list[$index-1].Name
 }
}

$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
if(!([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  $arguments=@('-NoProfile','-ExecutionPolicy','Bypass','-File',('"'+$PSCommandPath+'"'),'-Action',$Action,'-Region',$Region)
  if($Node){$arguments+=@('-Node',('"'+$Node+'"'))};if($Subscription){$arguments+=@('-Subscription',('"'+$Subscription+'"'))}
  if($Snapshot){$arguments+=@('-Snapshot',('"'+$Snapshot+'"'))}
  if($Acknowledged){$arguments+='-Acknowledged'};if($NoPause){$arguments+='-NoPause'};if($SkipBrowser){$arguments+='-SkipBrowser'}
  $process=Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $arguments -Wait -PassThru
  exit $process.ExitCode
}

# The same byte-range lock is used by the Python scheduled updater.
$operationStream=[IO.File]::Open((Join-Path $local 'operation.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::ReadWrite)
try{$operationStream.Lock(0,1)}catch{$operationStream.Dispose();throw '本项目另一个操作正在执行，请稍后重试。'}
try {
Resolve-PendingFirewall -LocalDir $local -Python $python
$env:CCN_SNAPSHOT=$null
if($Snapshot){
 if([IO.Path]::GetFileName($Snapshot) -ne $Snapshot -or $Snapshot -in @('.','..')){throw '快照只能使用 .local/snapshots 内的目录名。'}
 $env:CCN_SNAPSHOT=Join-Path (Join-Path $local 'snapshots') $Snapshot
}

if($Action -eq 'snapshot'){
 $snapshotError=Join-Path $local 'snapshot-error.log'
 try{
  $output=& $python (Join-Path $PSScriptRoot 'client.py') create-snapshot 2> $snapshotError
  if($LASTEXITCODE -ne 0){throw 'client.py failed'}
 }catch{throw ('快照采集失败，未做任何改动。详细错误保存在：'+$snapshotError)}
 $created=($output | Select-Object -Last 1) | ConvertFrom-Json
 $env:CCN_SNAPSHOT=$created.snapshot
 & (Join-Path $PSScriptRoot 'system.ps1') -Action snapshot -Python $python
 if(!$SkipBrowser){
  & $python (Join-Path $PSScriptRoot 'browser_settings.py') capture;if($LASTEXITCODE -ne 0){throw '浏览器策略备份失败。'}
  & $python (Join-Path $PSScriptRoot 'browser_languages.py') capture;if($LASTEXITCODE -ne 0){throw '浏览器语言备份失败。'}
 }
 & $python (Join-Path $PSScriptRoot 'client.py') seal;if($LASTEXITCODE -ne 0){throw '快照封存校验失败，不能视为保存成功。'}
 Write-Host ('快照已保存：'+$created.snapshot)
 return
}

# Supplement has its own narrow path: no browser restart, client rewrite or network tests.
if($Action -eq 'supplement'){
 $result=[ordered]@{success=$false;action=$Action}
 try{
  Set-StepPlan @('检查现有配置与恢复记录','扫描并补充规则')
  Write-Step '沿用上次配置的快照，不新建或覆盖原始备份'
  $planFile=Join-Path $local 'plan.json'
  if(!(Test-Path -LiteralPath $planFile)){throw '没有已有配置记录，请先执行菜单 1 或 2。'}
  $previous=Get-Content -LiteralPath $planFile -Raw -Encoding UTF8 | ConvertFrom-Json
  if(!$previous.snapshot){throw '已有配置记录缺少恢复快照，请先处理原始备份。'}
  $snapshotPath=[IO.Path]::GetFullPath([string]$previous.snapshot)
  $snapshotRoot=[IO.Path]::GetFullPath((Join-Path $local 'snapshots'))
  if((Split-Path -Parent $snapshotPath) -ine $snapshotRoot){throw '恢复快照必须位于本项目 .local/snapshots 内。'}
  if($Snapshot -and [IO.Path]::GetFullPath($env:CCN_SNAPSHOT) -ine $snapshotPath){throw '补充配置只能使用上次配置关联的快照。'}
  $env:CCN_SNAPSHOT=$snapshotPath
  Push-Location $PSScriptRoot
  try{& $python -c 'from baseline import verify;verify()'}finally{Pop-Location}
  if($LASTEXITCODE -ne 0){throw '原始快照校验失败，未补充任何配置。'}
  if(!(Test-Path -LiteralPath (Join-Path $snapshotPath 'system.json'))){throw '缺少系统恢复记录，无法补充配置。'}
  if(!$Acknowledged){
   Write-Host '补充配置会禁用非项目的本地出站放行规则、关闭当前启用的网卡 IPv6，并累积记录供菜单 3 恢复。' -ForegroundColor Yellow
   Write-Host '入站规则扫描后保留。可能影响新软件、VPN、Docker/WSL、远程控制；不运行网络或断网测试。'
   $answer=Read-Host '确认执行请输入 1（直接回车 = 取消）'
   if($answer -ne '1' -and $answer -cne '是' -and $answer -ine 'YES'){Write-Host '已取消，没有做任何改动。';return}
  }
  Write-Step '扫描当前规则和网卡；先保存恢复记录，再补充改动'
  & (Join-Path $PSScriptRoot 'system.ps1') -Action supplement -Python $python
  $result.success=$true
  $result.snapshot=$snapshotPath
  Write-Done ('补充配置已完成。恢复时请在菜单 3 选择快照：'+(Split-Path -Leaf $snapshotPath))
  Write-Host '未运行网络或断网测试；浏览器、虚拟网络和账号行为未验证。'
 }catch{
  $result.error=$_.Exception.Message
  $_ | Format-List * -Force | Out-String | Set-Content -LiteralPath (Join-Path $local 'last-error.log') -Encoding UTF8
  Write-Host ('补充配置未完成：'+$result.error) -ForegroundColor Red
  Write-Host '错误保存在 .local/last-error.log；如有未恢复的改动，可使用菜单 3 恢复到关联快照。'
 }finally{
  $result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $local 'last-run.json') -Encoding UTF8
 }
 $operationStream.Dispose()
 if(!$NoPause){Read-Host '按回车键关闭窗口'}
 if(!$result.success){exit 1}
 return
}

if(!$Acknowledged) {
 if($Action -eq 'apply'){
  Write-Host ''
  Write-Host '本次会改动下面 6 项，每项后面是它可能造成的影响：' -ForegroundColor Yellow
  Write-PlanItem 1 'Clash 客户端：为所选订阅关联独立的项目增强脚本与固定节点组' '保留原自定义脚本；恢复时撤销项目关联，保留后加订阅和节点更新'
  Write-PlanItem 2 '严格出站防火墙：保留四条放行，阻断 SharedAccess 服务出站' 'Mihomo 一停，国内网站也一起断；连接共享、移动热点、VPN、Docker、WSL、视频通话、远程控制可能失效'
  Write-PlanItem 3 '系统 IPv6：全局关闭' '依赖 IPv6 的内网或校园网资源不可用，要重启 Windows 才完全生效'
  Write-PlanItem 4 '区域与语言：设置新加坡时区，首选 zh-SG / en-SG' '部分应用的区域和语言显示可能变化；需要重启 Windows 才完全生效'
  Write-PlanItem 5 '代理环境变量：清空 HTTP_PROXY / HTTPS_PROXY 等' '依赖这些变量的命令行工具将不再走代理'
  if($SkipBrowser){
   Write-PlanItem 6 'Chrome / Edge：本次完全不碰' '不改 WebRTC 和语言，也不关闭浏览器；浏览器相关步骤整段跳过'
  } else {
   Write-PlanItem 6 'Chrome / Edge：强制关闭后修改 WebRTC 与语言' '未保存的网页内容会丢失，请先保存；结束后不会自动打开浏览器'
  }
  Write-Host ''
  Write-Host '执行时会逐条显示进度。中途失败可以运行菜单 3「恢复」还原本项目记录的改动；后加订阅和正常更新会保留。'
 }
 else{
  Write-Host ''
  Write-Host ('即将把本机还原到这份快照：'+$Snapshot) -ForegroundColor Yellow
  Write-PlanItem 1 'Clash 客户端：保留当前订阅及更新，撤销项目脚本和网络设置' 'Clash Verge 会被关闭后重新启动，按当前订阅生成运行配置'
  Write-PlanItem 2 '系统状态：防火墙、IPv6、区域/语言、代理环境变量' 'IPv6 与语言改动要重启 Windows 才完全生效'
  if($SkipBrowser){Write-PlanItem 3 '浏览器：本次跳过' '不修改浏览器设置，也不关闭浏览器'}
  else{Write-PlanItem 3 '浏览器：还原 WebRTC 策略与语言偏好' '正在运行的 Chrome/Edge 会被强制关闭'}
  Write-PlanItem 4 '计划任务：移除本项目注册的自动更新任务'
 }
 Write-Host ''
 $answer=Read-Host '确认执行请输入 1（直接回车 = 取消）'
 if($answer -ne '1' -and $answer -cne '是' -and $answer -ine 'YES'){Write-Host '已取消，没有做任何改动。';return}
}
# 需要改浏览器设置时强制关闭 Chrome/Edge；-SkipBrowser 时一律不碰。
$opened=@()
if(!$SkipBrowser){$opened=@(Get-Process chrome,msedge -ErrorAction SilentlyContinue | Select-Object -ExpandProperty ProcessName -Unique)}
$clashProgram=@(Get-Process clash-verge -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Path -Unique)
$result=[ordered]@{success=$false;action=$Action}
# 先按本次实际会跑的分支确定步骤总数，进度条才能显示「第几步/共几步」。
if($Action -eq 'apply'){
 $plan=@('检查运行环境','选择节点','生成候选配置与备份','备份系统状态')
 if(!$SkipBrowser){$plan+='备份浏览器设置'}
 $plan+=@('封存快照','写入 Clash 客户端配置','启动防火墙看门狗','应用系统设置')
 if(!$SkipBrowser){$plan+='写入浏览器设置'}
 $plan+=@('网络验证','注册自动更新任务')
} else {
 $plan=@('校验快照','移除自动更新任务','还原系统设置')
 if(!$SkipBrowser){$plan+='还原浏览器策略与语言'}
 $plan+='还原 Clash 客户端'
}
Set-StepPlan $plan
$planCount=@($plan).Count
$planWord=if($Action -eq 'apply'){'配置'}else{'恢复'}
Write-Host ''
Write-Host ('开始'+$planWord+'，共 '+$planCount+' 步，每一步都会打印进度。') -ForegroundColor Yellow
try {
 $clientSessionTouched=$false
 if($Action -eq 'apply') {
  Write-Step '确认 Clash Verge 后台服务在运行，并自检内核的 DNS 类型过滤'
  Get-Service -Name clash_verge_service -ErrorAction Stop | Out-Null
  Assert-FirewallAvailable | Out-Null
  if(!$SkipBrowser){
   & $python (Join-Path $PSScriptRoot 'browser_settings.py') preflight
   if($LASTEXITCODE -ne 0){throw '浏览器已有冲突策略，未关闭应用或写入配置。请先审核原策略。'}
  }
  & $python (Join-Path $PSScriptRoot 'test_dns_filter.py');if($LASTEXITCODE -ne 0){throw '内核 DNS 类型过滤自检未通过，未改动任何系统设置。'}
  $nodeNote=if($Node){'使用命令行指定的节点：'+$Node}else{'优先沿用上次用过的节点，也可以按编号重新选'}
  Write-Step $nodeNote
  if(!$Node){
   # 优先沿用上次的节点，避免每次手打完整节点名；没有历史时按编号选择。
   $lastNode=$null;$lastSubscription=$null
   $planFile=Join-Path $local 'plan.json'
   if(Test-Path -LiteralPath $planFile){try{$previousPlan=Get-Content -LiteralPath $planFile -Raw -Encoding UTF8 | ConvertFrom-Json;$lastNode=[string]$previousPlan.node;$lastSubscription=[string]$previousPlan.subscription_uid}catch{$lastNode=$null}}
   if($lastNode){
    Write-Host ('上次使用的节点：'+$lastNode)
    $answerNode=Read-Host '直接回车沿用它；要换节点就输入新的完整节点名'
    if([string]::IsNullOrWhiteSpace($answerNode)){$Node=$lastNode;if(!$Subscription){$Subscription=$lastSubscription}}else{$Node=$answerNode.Trim()}
   } else {
    $subs=@()
    try{
     $discovered=& $python (Join-Path $PSScriptRoot 'client.py') discover 2>$null | Select-Object -Last 1 | ConvertFrom-Json
     $subs=@($discovered.subscriptions)
    }catch{$subs=@()}
    if($subs.Count){
     Write-Host '可用订阅：'
     for($i=0;$i -lt $subs.Count;$i++){Write-Host ('  {0}. {1}（{2} 个节点）' -f ($i+1),$subs[$i].name,@($subs[$i].nodes).Count)}
     $pick=Read-Host ('请输入订阅编号 [1-'+$subs.Count+']，也可以直接输入完整节点名')
     $index=0
     if([int]::TryParse($pick,[ref]$index) -and $index -ge 1 -and $index -le $subs.Count){
      $chosen=$subs[$index-1];$nodes=@($chosen.nodes)
      Write-Host ('订阅「'+$chosen.name+'」的节点：')
      for($j=0;$j -lt $nodes.Count;$j++){Write-Host ('  {0}. {1}' -f ($j+1),$nodes[$j])}
      $nodePick=Read-Host ('请输入节点编号 [1-'+$nodes.Count+']')
      $nodeIndex=0
      if(![int]::TryParse($nodePick,[ref]$nodeIndex) -or $nodeIndex -lt 1 -or $nodeIndex -gt $nodes.Count){throw '节点编号无效。'}
      $Node=[string]$nodes[$nodeIndex-1]
      if(!$Subscription){$Subscription=[string]$chosen.name}
     } else {$Node=$pick.Trim()}
    } else {
     $Node=Read-Host '请输入订阅里已存在的完整节点名（不会自动替换成别的节点）'
    }
   }
   Write-Host ('将使用节点：'+$Node)
  }
  Write-Step '关闭 Clash Verge，生成候选配置并用内核自带的检查验证能加载'
  $clientSessionTouched=$true
  Get-Process clash-verge -ErrorAction SilentlyContinue | Stop-Process -Force
  $prepareArguments=@((Join-Path $PSScriptRoot 'client.py'),'prepare','--node',$Node,'--region',$Region)
  if($Subscription){$prepareArguments+=@('--subscription',$Subscription)}
  $prepared=& $python @prepareArguments;if($LASTEXITCODE -ne 0){throw '候选配置/备份生成失败，未改动任何系统设置。'}
  $planState=($prepared | Select-Object -Last 1) | ConvertFrom-Json
  $env:CCN_SNAPSHOT=$planState.snapshot
  Write-Step ('把当前系统状态存进快照 '+$planState.snapshot+'，这是本次的回滚点')
  & (Join-Path $PSScriptRoot 'system.ps1') -Action snapshot -Python $python
  if(!$SkipBrowser){
   Get-Process chrome,msedge -ErrorAction SilentlyContinue | Stop-Process -Force
   Write-Step '备份 Chrome/Edge 的策略与语言偏好'
   & $python (Join-Path $PSScriptRoot 'browser_settings.py') capture;if($LASTEXITCODE -ne 0){throw '浏览器策略备份失败。'}
   & $python (Join-Path $PSScriptRoot 'browser_languages.py') capture;if($LASTEXITCODE -ne 0){throw '浏览器语言备份失败。'}
  }
  Write-Step '封存快照，确认备份完整可恢复'
  & $python (Join-Path $PSScriptRoot 'client.py') seal;if($LASTEXITCODE -ne 0){throw '系统备份封存失败。'}
  Write-Step '写入 Clash 客户端配置：增强脚本、固定节点组、分流规则'
  & $python (Join-Path $PSScriptRoot 'client.py') apply-client;if($LASTEXITCODE -ne 0){throw '客户端配置写入失败。'}
  Write-Step '启动防火墙保护：180 秒未确认成功则进入回滚，正在写入的步骤会先完成'
  $watchToken='guard-'+[Guid]::NewGuid().ToString('N')
  [ordered]@{token=$watchToken;snapshot=$env:CCN_SNAPSHOT} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $local 'pending-firewall.json.new') -Encoding UTF8
  Move-Item -LiteralPath (Join-Path $local 'pending-firewall.json.new') -Destination (Join-Path $local 'pending-firewall.json') -Force
  $watchArgs=@('-NoProfile','-ExecutionPolicy','Bypass','-File',('"'+(Join-Path $PSScriptRoot 'watchdog.ps1')+'"'),'-Token',$watchToken,'-Python',('"'+$python+'"'))
  Start-Process -FilePath 'powershell.exe' -WindowStyle Hidden -ArgumentList $watchArgs | Out-Null
  Write-Step '应用系统设置：严格防火墙、关闭 IPv6、区域/语言、代理环境变量'
  $applyMutex=New-Object Threading.Mutex($false,('Global\CCN-'+$watchToken))
  try{[void]$applyMutex.WaitOne()}catch [Threading.AbandonedMutexException]{}
  try{
   if(Test-Path -LiteralPath (Join-Path $local ($watchToken+'.rollback'))){throw '后台回滚已开始，停止配置。'}
   & (Join-Path $PSScriptRoot 'system.ps1') -Action apply -Python $python
   $firewallPlan=Get-Content -LiteralPath (Join-Path $local 'plan.json') -Raw -Encoding UTF8 | ConvertFrom-Json
   Assert-StrictFirewall -coreProgram ([string]$firewallPlan.core_program)
  }finally{$applyMutex.ReleaseMutex();$applyMutex.Dispose()}
  if(!$SkipBrowser){
   Write-Step '写入浏览器设置：WebRTC 策略与语言偏好'
   & $python (Join-Path $PSScriptRoot 'browser_settings.py') apply;if($LASTEXITCODE -ne 0){throw '浏览器策略写入失败。'}
   & $python (Join-Path $PSScriptRoot 'browser_languages.py') apply;if($LASTEXITCODE -ne 0){throw '浏览器语言写入失败。'}
  }
  Write-Step '网络验证：节点中断、内核退出后能否自动恢复（需要 1-2 分钟）'
  $env:CCN_VERIFY_RUN=[Guid]::NewGuid().ToString('N')
  & $python (Join-Path $PSScriptRoot 'verify.py') --node-outage;if($LASTEXITCODE -ne 0){throw (Get-VerificationFailureMessage '网络验证未通过。')}
  & $python (Join-Path $PSScriptRoot 'verify_core_exit.py');if($LASTEXITCODE -ne 0){throw '内核退出保护/重启验证未通过。'}
  & $python (Join-Path $PSScriptRoot 'verify.py') --stage post-recovery;if($LASTEXITCODE -ne 0){throw (Get-VerificationFailureMessage '内核退出测试之后网络没有恢复。')}
  $guardMutex=New-Object Threading.Mutex($false,('Global\CCN-'+$watchToken))
  try{[void]$guardMutex.WaitOne()}catch [Threading.AbandonedMutexException]{}
  try {
   if(Test-Path -LiteralPath (Join-Path $local ($watchToken+'.rollback'))){throw '防火墙看门狗已回滚，本次部署不算成功。'}
   $firewallPlan=Get-Content -LiteralPath (Join-Path $local 'plan.json') -Raw -Encoding UTF8 | ConvertFrom-Json
   Assert-StrictFirewall -coreProgram ([string]$firewallPlan.core_program)
   Set-Content -LiteralPath (Join-Path $local ($watchToken+'.commit')) -Value 'Verified'
  } finally {$guardMutex.ReleaseMutex();$guardMutex.Dispose()}
  Write-Step '注册自动更新任务，每天更新已审核的国内规则来源，不更新内核'
  & (Join-Path $PSScriptRoot 'update_task.ps1') -Action install -Python $python
 } else {
  Write-Step '校验快照与本机是否匹配，不匹配就不动任何设置'
  # `python -c` only puts the current directory on sys.path, and the menu entry
  # starts this script from the project root, so baseline.py was never importable.
  # Do not build a quoted path into the -c string: PowerShell 5.1 strips embedded
  # double quotes when calling a native program. Switch directory instead.
  Push-Location $PSScriptRoot
  try{& $python -c 'from baseline import preflight_restore;preflight_restore()'}finally{Pop-Location}
  if($LASTEXITCODE -ne 0){throw '快照校验失败，未恢复任何设置，也未关闭浏览器。'}
  $clientSessionTouched=$true
  if($opened.Count){Stop-Process -Name $opened -Force}
  Write-Step '移除本项目的自动更新任务'
  & (Join-Path $PSScriptRoot 'update_task.ps1') -Action remove -Python $python
  Write-Step '还原系统设置：防火墙、IPv6、区域/语言、代理环境变量'
  # Legacy/incomplete snapshots may lack system records. Restore available data,
  # but never claim the missing system settings were restored.
  if(Test-Path -LiteralPath (Join-Path $env:CCN_SNAPSHOT 'system.json')){
   & (Join-Path $PSScriptRoot 'system.ps1') -Action restore -Python $python
  } else {
   $result.status='partial';$result.systemRestore='not-recorded'
   Write-Warning '这份快照没有系统恢复记录（system.json），无法据此还原防火墙、IPv6、区域和语言；仅继续恢复已有记录。'
  }
  if(!$SkipBrowser){
  Write-Step '还原浏览器策略与语言偏好'
  & $python (Join-Path $PSScriptRoot 'browser_settings.py') restore;if($LASTEXITCODE -ne 0){throw '浏览器策略还原失败。'}
  & $python (Join-Path $PSScriptRoot 'browser_languages.py') restore;if($LASTEXITCODE -ne 0){throw '浏览器语言还原失败。'}
  }
  Write-Step '保留订阅及节点更新，撤销项目脚本并重新生成运行配置'
  Get-Process clash-verge -ErrorAction SilentlyContinue | Stop-Process -Force
  $clientRestore=$null
  $restoreOutput=& $python (Join-Path $PSScriptRoot 'client.py') restore-client
  $restoreExit=$LASTEXITCODE
  if($restoreOutput){
   $clientRestore=($restoreOutput | Select-Object -Last 1) | ConvertFrom-Json
   $result.clientRestore=$clientRestore
   foreach($item in @($clientRestore.failed_items)){
    Write-Host ('  未完成：'+$item.file+' ['+$item.operation+' / '+$item.reason+']') -ForegroundColor Red
   }
  }
  if($restoreExit -ne 0 -or !$clientRestore -or !$clientRestore.client_restored){
   $result.status='partial'
   throw '恢复仅部分完成。未完成项目见 .local/client-restore-result.json；请解除文件占用或权限问题后重试恢复。'
  }
  if($clientRestore.runtime_rebuild_required){
   Write-Host '  正在启动 Clash，等待它按保留后的当前订阅重新生成配置。'
   if(Get-ScheduledTask -TaskName 'Clash Verge' -ErrorAction SilentlyContinue){Start-ScheduledTask -TaskName 'Clash Verge'}
   elseif($clashProgram.Count -eq 1 -and (Test-Path -LiteralPath $clashProgram[0])){Start-Process -FilePath $clashProgram[0]}
   else{$result.status='partial';throw '订阅已保留，但无法启动 Clash 生成运行配置；请手动打开 Clash 后重试恢复。'}
   $clientSessionTouched=$false
   $finishOutput=& $python (Join-Path $PSScriptRoot 'client.py') finish-restore
   $finishExit=$LASTEXITCODE
   if($finishOutput){$result.clientRestore=($finishOutput | Select-Object -Last 1) | ConvertFrom-Json}
   if($finishExit -ne 0 -or !$result.clientRestore.runtime_reloaded){
    $result.status='partial';throw '订阅文件已保留，但当前订阅的运行配置尚未确认生成；恢复未完成，请检查 Clash 后重试。'
   }
  }
 }
 if($Action -eq 'restore' -and $result.status -eq 'partial'){throw '该快照缺少系统恢复记录，已处理现有客户端记录，但无法完成系统恢复。'}
 $result.success=$true
 if($Action -eq 'apply'){
  Write-Done ('配置已完成并生效（共 '+$planCount+' 步）。本次的回滚点（快照）：'+$env:CCN_SNAPSHOT)
  Write-Host '  固定节点组 Claude-Overseas-Pinned 只包含你指定的节点。要撤销请运行菜单 3「恢复」。'
 } else {
  Write-Done ('恢复已完成（共 '+$planCount+' 步）。使用的快照：'+$env:CCN_SNAPSHOT)
 }
 Write-Host '  IPv6 和语言改动要重启 Windows 才完全生效。网络测试不能证明 Claude 账号本身可用。'
} catch {
 $runError=$_
 $result.error=$_.Exception.Message
 $result.failedStep=$script:stepNo
 $result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $local 'last-run.json') -Encoding UTF8
 $runError | Format-List * -Force | Out-String | Set-Content -LiteralPath (Join-Path $local 'last-error.log') -Encoding UTF8
 $brokenStep=@($script:stepTitles)
 if($script:stepNo -gt 0 -and $script:stepNo -le $brokenStep.Count){
  Write-Host ''
  Write-Host ('  中断在第 '+$script:stepNo+'/'+$brokenStep.Count+' 步：'+[string]$brokenStep[$script:stepNo-1]) -ForegroundColor Red
 }
 Write-Host ('  原因：'+$runError.Exception.Message) -ForegroundColor Red
 Write-Host '  错误已保存到 .local/last-error.log。'
 if($Action -eq 'apply' -and $watchToken -and $env:CCN_SNAPSHOT -and (Test-Path -LiteralPath (Join-Path $env:CCN_SNAPSHOT 'system.json'))) {
  Write-Host '  正在回滚防火墙，原有规则较多时可能需要等待。' -ForegroundColor Yellow
  try{if($watchToken){Resolve-PendingFirewall -LocalDir $local -Python $python -ExpectedToken $watchToken -Force}else{& (Join-Path $PSScriptRoot 'system.ps1') -Action rollback-firewall -Python $python};$result.firewallRollback=$true}catch{$result.firewallRollback=$false}
 }
 Write-Error $runError -ErrorAction Continue
 if($Action -eq 'apply'){
  Write-Host '配置没有完成，可能只改了一部分：看门狗只回滚防火墙规则，时区、语言、IPv6 和代理设置不会自动还原。重试之前请先执行「恢复」。'
 } else {
  Write-Host '恢复没有完成，可能只还原了一部分。可以直接再执行一次「恢复」。'
 }
} finally {
 $result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $local 'last-run.json') -Encoding UTF8
 if($clientSessionTouched -and (Get-ScheduledTask -TaskName 'Clash Verge' -ErrorAction SilentlyContinue)){Start-ScheduledTask -TaskName 'Clash Verge'}
 elseif($clientSessionTouched -and $clashProgram.Count -eq 1 -and (Test-Path -LiteralPath $clashProgram[0])){Start-Process -FilePath $clashProgram[0]}
 # 浏览器不再自动拉起：配置/恢复结束后由你自己打开，避免多开窗口。
 # 需要恢复上次标签页时，Chrome/Edge 启动后会用自带的"恢复"功能。
}
$operationStream.Dispose()
if(!$NoPause){Read-Host '按回车键关闭窗口'}
if(!$result.success){exit 1}

} finally {$operationStream.Dispose()}
