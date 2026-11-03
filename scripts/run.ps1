param([ValidateSet('apply','supplement','restore','snapshot','list-snapshots','reconfigure')][string]$Action,[string]$Node,[string]$Subscription,[string]$Region='SG',[string]$Snapshot,[switch]$Acknowledged,[switch]$NoPause,[string]$EventsFile,[string]$Python)
$ErrorActionPreference='Stop'
$env:PYTHONNOUSERSITE='1'
. (Join-Path $PSScriptRoot 'firewall_rules.ps1')
. (Join-Path $PSScriptRoot 'operation_guard.ps1')
# 提权后的新控制台默认是 936 代码页，中文和表情会在 PowerShell 与 Python 之间
# 互相解码错误（甚至让 print 直接抛 UnicodeEncodeError）。两端统一成 UTF-8。
$env:PYTHONUTF8='1'
$env:PYTHONIOENCODING='utf-8'
try{[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;[Console]::ForegroundColor=[ConsoleColor]::White}catch{}
$project=Split-Path -Parent $PSScriptRoot
$local=Join-Path $project '.local';New-Item -Path $local -ItemType Directory -Force | Out-Null
# ---- GUI 事件流 ---------------------------------------------------------
# 提权后的进程无法把标准输出交回非管理员的界面进程，所以界面通过这个
# JSONL 文件接收进度。事件只包含动作、步骤和错误，不含订阅内容或凭据。
$eventsPath=$null
if($EventsFile){
 $eventsPath=[IO.Path]::GetFullPath($EventsFile)
 $localRoot=[IO.Path]::GetFullPath($local)
 $eventPrefix=$localRoot.TrimEnd('\')+'\'
 if(!$eventsPath.StartsWith($eventPrefix,[StringComparison]::OrdinalIgnoreCase)){throw '事件记录必须保存在本项目 .local 内。'}
 $eventDir=Split-Path -Parent $eventsPath
 if($eventDir -and !(Test-Path -LiteralPath $eventDir)){New-Item -Path $eventDir -ItemType Directory -Force | Out-Null}
}
$script:eventLock=New-Object object
function Write-GuiEvent([string]$Type,[System.Collections.IDictionary]$Data){
 if(!$eventsPath){return}
 try{
  $payload=[ordered]@{type=$Type;time=(Get-Date).ToString('o')}
  if($Data){foreach($key in @($Data.Keys)){$payload[$key]=$Data[$key]}}
  $line=($payload | ConvertTo-Json -Compress -Depth 4)
  [Threading.Monitor]::Enter($script:eventLock)
  try{[IO.File]::AppendAllText($eventsPath,($line+"`n"),[Text.UTF8Encoding]::new($false))}
  finally{[Threading.Monitor]::Exit($script:eventLock)}
 }catch{}
}
try {
$envState=$null
$envFile=Join-Path $local 'env.json'
if(Test-Path -LiteralPath $envFile){try{$envState=Get-Content -LiteralPath $envFile -Raw -Encoding UTF8 | ConvertFrom-Json}catch{$envState=$null}}
if(!$Python -and $envState -and $envState.python){$Python=[string]$envState.python}
if($envState -and $envState.node){$env:CCN_NODE=[string]$envState.node}
$require313=$false
if($PSBoundParameters.ContainsKey('Python') -and $Python){$require313=$true}
if($envState -and $envState.python -and $Python -ieq [string]$envState.python){$require313=$true}
if($Python){
 if(!(Test-Path -LiteralPath $Python)){throw '项目内 Python 路径不存在。请重新运行「一键网络.cmd」修复 .runtime。'}
} else {
 $pythonCommand=Get-Command python.exe -ErrorAction SilentlyContinue
 if(!$pythonCommand){throw '未找到 Python（项目内 3.13）。请运行「一键网络.cmd」自动安装到 .runtime，不会修改系统 PATH。'}
 $Python=$pythonCommand.Source
}
$python=$Python
if($python -like '*\Microsoft\WindowsApps\*'){throw '检测到的是 Windows 应用商店的 Python 占位程序。请运行「一键网络.cmd」使用项目内 Python 3.13。'}
if($require313){
 & $python -c 'import sys,importlib.util;sys.exit(10 if sys.version_info[:2]!=(3,13) else 11 if importlib.util.find_spec(''yaml'') is None else 0)' 2> (Join-Path $local 'dependency-error.log')
 if($LASTEXITCODE -eq 10){throw '项目内 Python 不是 3.13。请重新运行「一键网络.cmd」修复 .runtime。'}
 if($LASTEXITCODE -eq 11){throw '项目内 Python 缺少 PyYAML。请重新运行「一键网络.cmd」修复依赖。'}
} else {
 & $python -c 'import sys,importlib.util;sys.exit(10 if sys.version_info < (3,10) else 11 if importlib.util.find_spec(''yaml'') is None else 0)' 2> (Join-Path $local 'dependency-error.log')
 if($LASTEXITCODE -eq 10){throw 'Python 版本太旧。需要项目内 Python 3.13，请运行「一键网络.cmd」。'}
 if($LASTEXITCODE -eq 11){throw '当前 Python 缺少 PyYAML。请重新运行「一键网络.cmd」修复依赖。'}
}
if($LASTEXITCODE -ne 0){throw 'Python 依赖检查失败，详情见 .local/dependency-error.log。请重新运行「一键网络.cmd」。'}

$regionProfile=[pscustomobject]@{
 region='SG';label='新加坡';culture='zh-SG';language_tag='zh-Hans-SG';fallback_language_tag='en-SG';
 browser_language='zh-SG';timezone_id='Singapore Standard Time';iana='Asia/Singapore'
}
if($Action -eq 'apply'){
 if($Region -notmatch '^[A-Za-z]{2}$'){throw '出口地区须为两位国家代码，例如 SG。'}
 $Region=$Region.ToUpperInvariant()
 $regionProfilePath=Join-Path $PSScriptRoot 'region_profile.py'
 if(Test-Path -LiteralPath $regionProfilePath){
  $regionJson=& $python $regionProfilePath --region $Region
  if($LASTEXITCODE -ne 0){throw '读取地区语言/时区配置失败。'}
  try{$regionProfile=($regionJson | ConvertFrom-Json)}catch{throw '地区语言/时区配置格式无效。'}
  if($null -ne $regionProfile.known -and !$regionProfile.known){throw ('暂无 '+$Region+' 的语言/时区配置；请手动选择支持的地区。')}
 }
}

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
 if(!$list.Count){Write-Host '还没有可用的快照清单。请先在界面「快照与恢复」保存当前快照，或完成一次配置；保存失败时请查看本地错误记录。';return @()}
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
 Write-GuiEvent 'step' ([ordered]@{index=$n;total=$total;title=$title;note=$note})
}
function Write-Done([string]$text){
 Write-Host ''
 Write-Host ('  ['+('='*20)+']  '+$text) -ForegroundColor Green
 Write-GuiEvent 'done' ([ordered]@{message=$text})
}
function Read-Selection([string]$prompt,[int]$maximum){
 while($true){
  $value=Read-Host ($prompt+'（回车取消）')
  if([string]::IsNullOrWhiteSpace($value)){return 0}
  $number=0
  if([int]::TryParse($value,[ref]$number) -and $number -ge 1 -and $number -le $maximum){return $number}
  Write-Host ('请输入 1 到 '+$maximum+' 之间的编号，或直接回车取消。') -ForegroundColor Yellow
 }
}
function Stop-ClashClient {
 $stopping=@(Get-Process clash-verge -ErrorAction SilentlyContinue)
 try{
  # 固定原进程句柄，等待该进程的退出信号；PID 查询仍返回对象不代表进程存活。
  # 保留句柄直到等待结束，也避免把复用的 PID 当成原进程。
  foreach($process in $stopping){$null=$process.Handle}
  $stopping | Stop-Process -Force
  foreach($process in $stopping){
   if(!$process.WaitForExit(15000)){throw 'Clash 旧进程尚未退出，未继续写入；请检查客户端后重试。'}
  }
  # 原进程退出后，新的客户端同样可能写配置；无法确认其退出时必须停止。
  foreach($process in @(Get-Process clash-verge -ErrorAction SilentlyContinue)){
   try{
    if(!$process.HasExited){throw 'Clash 客户端在关闭后仍有运行进程或被重新启动，未继续写入；请退出客户端及其自动启动程序后重试。'}
   }finally{$process.Dispose()}
  }
 }finally{
  foreach($process in $stopping){$process.Dispose()}
 }
}
function Start-ClashClient {
 # A scheduled task may still report Running after its old process was killed,
 # and IgnoreNew silently discards the new trigger. Prefer the observed exe.
 if($clashProgram.Count -eq 1 -and (Test-Path -LiteralPath $clashProgram[0])){
  Start-Process -FilePath $clashProgram[0] -WindowStyle Hidden
 }elseif(Get-ScheduledTask -TaskName 'Clash Verge' -ErrorAction SilentlyContinue){Start-ScheduledTask -TaskName 'Clash Verge'}
 else{
  # Restore also runs when the client is already closed, so the observed
  # process is unavailable. Fall back to the standard installation only.
  $installed=@()
  foreach($root in @($env:ProgramFiles,${env:ProgramFiles(x86)})){
   if(!$root){continue}
   $candidate=Join-Path $root 'Clash Verge\clash-verge.exe'
   if(Test-Path -LiteralPath $candidate){$installed+=$candidate}
  }
  if($installed.Count -eq 1){Start-Process -FilePath $installed[0] -WindowStyle Hidden}
  else{throw '无法确认 Clash 启动路径，请手动打开 Clash 后重试恢复。'}
 }
}
function Get-VerificationFailureMessage([string]$fallback){
 $details=@()
 try{
  $report=Get-Content -LiteralPath (Join-Path $local 'verification.json') -Raw -Encoding UTF8 | ConvertFrom-Json
  if(!$env:CCN_VERIFY_RUN -or $report.run_id -ne $env:CCN_VERIFY_RUN){return $fallback}
  $checkTitles=@{runtime='内核运行模式或固定节点组';domestic='国内网站连通性';egress='海外出口及要求地区';dns='DNS A/64/65 过滤';fake_ip='TUN DNS 接管';ipv6='IPv6 公网阻断';tools='Git/npm/UDP STUN';udp_egress='UDP 出口地区及指定节点路由';node_outage='节点故障保护';recovery='节点恢复后出口'}
  foreach($stage in @('node-outage','core-exit','post-recovery','connectivity')){
   $evidence=$report.stages.$stage
   if($evidence -and $evidence.success -eq $false){
    if($stage -eq 'core-exit'){
     if($evidence.restart_failed){$details+='停核测试后核心重启失败，请检查 Clash 后恢复'}else{$details+='核心退出保护检测失败'}
    }elseif($evidence.failed_check -and $checkTitles.ContainsKey([string]$evidence.failed_check)){$details+=($checkTitles[[string]$evidence.failed_check]+'检测失败')}
   }
  }
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
  $index=Read-Selection '请输入要恢复的快照编号' $list.Count
  if(!$index){Write-Host '已取消恢复，未执行还原步骤。';return}
  $Snapshot=$list[$index-1].Name
 }
}

$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
if(!([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  $arguments=@('-NoProfile','-ExecutionPolicy','Bypass','-File',('"'+$PSCommandPath+'"'),'-Action',$Action,'-Region',$Region)
  if($Node){$arguments+=@('-Node',('"'+$Node+'"'))};if($Subscription){$arguments+=@('-Subscription',('"'+$Subscription+'"'))}
  if($Snapshot){$arguments+=@('-Snapshot',('"'+$Snapshot+'"'))}
  if($Acknowledged){$arguments+='-Acknowledged'};if($NoPause){$arguments+='-NoPause'}
  if($Python){$arguments+=@('-Python',('"'+$Python+'"'))}
  if($eventsPath){$arguments+=@('-EventsFile',('"'+$eventsPath+'"'))}
  Write-GuiEvent 'elevation' ([ordered]@{message='等待 Windows 管理员授权'})
  Write-Host '即将请求管理员权限；请在 Windows 授权窗口中确认。取消授权不会执行所选操作。' -ForegroundColor Yellow
  # Start-Process -Wait also waits for Clash descendants, which stay open.
  # Wait only for the elevated operation process itself.
  try{$process=Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $arguments -PassThru;$null=$process.Handle;$process.WaitForExit()}
  catch{
   $native=$_.Exception
   while($native.InnerException){$native=$native.InnerException}
   if($native.NativeErrorCode -eq 1223){Write-GuiEvent 'cancelled' ([ordered]@{message='已取消管理员授权，未执行所选操作。'});Write-Host '已取消管理员授权，未执行所选操作。';if(!$NoPause){Read-Host '按回车键关闭窗口'};return}
   Write-GuiEvent 'error' ([ordered]@{message='无法打开管理员窗口，请检查 Windows 权限限制。'})
   throw '无法打开管理员窗口。请重新打开「一键网络.cmd」重试，并检查 Windows 权限限制。'
  }
  Write-GuiEvent 'exit' ([ordered]@{code=$process.ExitCode})
  exit $process.ExitCode
}

# This coordinator delegates each step to the normal locked entry; it must not
# hold operation.lock itself, or its own workers would deadlock.
if($Action -eq 'reconfigure'){
 if(!$Acknowledged){
  Write-Host '将先恢复固定的最早恢复点，再沿用原精确节点完整配置，并短暂中断公网。' -ForegroundColor Yellow
  Write-Host '独立本机看护会在失败、进程退出或超时后尝试恢复网络；记录保存在 .local/reconfigure-runs。'
  Write-Host '浏览器不会被关闭；正在使用的网页可能因网络短暂中断而失去连接。'
  $answer=Read-Host '确认执行请输入 1（回车取消）'
  if($answer -ne '1'){Write-Host '已取消。';return}
 }
 Write-GuiEvent 'start' ([ordered]@{action='reconfigure'})
 $env:CCN_PYTHON=$python
 $safeArguments=@((Join-Path $PSScriptRoot 'safe_reconfigure.py'))
 if($eventsPath){$env:CCN_EVENTS_FILE=$eventsPath}
 & $python @safeArguments
 if($LASTEXITCODE -ne 0){throw '恢复并重新配置未全部完成，请检查本次本地记录；独立看护会处理网络恢复。'}
 Write-GuiEvent 'done' ([ordered]@{message='恢复并重新配置已完成；请查看本次验收记录。'})
 if(!$NoPause){Read-Host '按回车键关闭窗口'}
 return
}
$activeJob=Join-Path $local 'reconfigure-active.json'
if((Test-Path -LiteralPath $activeJob) -and $Action -ne 'restore'){
 $owner=(Get-Content -LiteralPath $activeJob -Raw -Encoding UTF8 | ConvertFrom-Json).id
 if(!$env:CCN_RECONFIGURE_JOB -or $env:CCN_RECONFIGURE_JOB -cne $owner){throw '安全重新配置或网络恢复仍在执行，请等待本机看护完成，不要同时开始其他操作。'}
}
# The same byte-range lock is used by the Python scheduled updater.
$operationStream=[IO.File]::Open((Join-Path $local 'operation.lock'),[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::ReadWrite)
try{$operationStream.Lock(0,1)}catch{$operationStream.Dispose();throw '本项目另一个操作正在执行，请稍后重试。'}
try {
Resolve-PendingFirewall -LocalDir $local -Python $python
Assert-NoPendingSettings -LocalDir $local
$env:CCN_SNAPSHOT=$null
if($Snapshot){
 if([IO.Path]::GetFileName($Snapshot) -ne $Snapshot -or $Snapshot -in @('.','..')){throw '快照只能使用 .local/snapshots 内的目录名。'}
 $env:CCN_SNAPSHOT=Join-Path (Join-Path $local 'snapshots') $Snapshot
}
Write-GuiEvent 'start' ([ordered]@{action=$Action})

if($Action -eq 'snapshot'){
 Set-StepPlan @('采集客户端记录','保存系统记录','封存并校验快照')
 Write-Step '另存当前状态，不覆盖首次基线；不修改网络或浏览器设置'
 $snapshotError=Join-Path $local 'snapshot-error.log'
 try{
  $output=& $python (Join-Path $PSScriptRoot 'client.py') create-snapshot 2> $snapshotError
  if($LASTEXITCODE -ne 0){throw 'client.py failed'}
 }catch{throw ('快照采集失败，未做任何改动。详细错误保存在：'+$snapshotError)}
 $created=($output | Select-Object -Last 1) | ConvertFrom-Json
 $env:CCN_SNAPSHOT=$created.snapshot
 Write-Step '保存系统记录，文件较多时请等待'
 & (Join-Path $PSScriptRoot 'system.ps1') -Action snapshot -Python $python
 Write-Step '检查恢复记录的完整性；只有封存通过才算保存成功'
 & $python (Join-Path $PSScriptRoot 'client.py') seal;if($LASTEXITCODE -ne 0){throw '快照封存校验失败，不能视为保存成功。'}
 [ordered]@{success=$true;action=$Action;snapshot=$created.snapshot} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $local 'last-run.json') -Encoding UTF8
 Write-Done ('快照已保存：'+$created.snapshot)
 Write-Host '可在界面「快照与恢复」中选择此快照；它不会替换已有自动基线。'
 if(!$NoPause){Read-Host '按回车键关闭窗口'}
 return
}

# Supplement has its own narrow path: no browser restart, client rewrite or network tests.
if($Action -eq 'supplement'){
 $result=[ordered]@{success=$false;action=$Action}
 try{
  Set-StepPlan @('检查现有配置与恢复记录','扫描并补充规则')
  Write-Step '沿用上次配置的快照，不新建或覆盖原始备份'
  $planFile=Join-Path $local 'plan.json'
  if(!(Test-Path -LiteralPath $planFile)){throw '没有已有配置记录，请先在界面「一键配置」中完成配置。'}
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
   Write-Host '补充配置会禁用非项目的本地出站放行规则、关闭当前启用的网卡 IPv6、系统定位、自动时区和自动校时，按节点地区设置国家/区域格式/时区，并配置 Chrome、Edge、Firefox 的 WebRTC 策略（网页通话可能受影响），累积记录供「快照与恢复」撤销。' -ForegroundColor Yellow
   Write-Host '入站规则扫描后保留。可能影响新软件、VPN、Docker/WSL、远程控制；不运行网络或断网测试。'
   $answer=Read-Host '确认执行请输入 1（直接回车 = 取消）'
   if($answer -ne '1' -and $answer -cne '是' -and $answer -ine 'YES'){Write-Host '已取消，没有做任何改动。';return}
  }
  Write-Step '扫描当前规则和网卡；先保存恢复记录，再补充改动'
  $regionJson=& $python (Join-Path $PSScriptRoot 'region_profile.py') --region ([string]$previous.region)
  if($LASTEXITCODE -ne 0){throw '无法读取现有配置的地区；未补充设置。'}
  $supplementRegion=$regionJson | ConvertFrom-Json
  if(!$supplementRegion.known){throw '现有配置的地区未受支持；请重新选择地区配置。'}
  & (Join-Path $PSScriptRoot 'system.ps1') -Action snapshot -Python $python
  & $python (Join-Path $PSScriptRoot 'client.py') seal
  if($LASTEXITCODE -ne 0){throw '补充恢复记录封存失败，未补充设置。'}
  & (Join-Path $PSScriptRoot 'system.ps1') -Action supplement -Python $python -TimeZoneId $supplementRegion.timezone_id -RegionCode $supplementRegion.region -CultureName $supplementRegion.culture
  $result.success=$true
  $result.snapshot=$snapshotPath
  Write-Done ('补充配置已完成。恢复时请在「快照与恢复」选择快照：'+(Split-Path -Leaf $snapshotPath))
  Write-Host '未运行网络或断网测试；沙箱浏览器、虚拟网络和账号行为未验证。'
 }catch{
  $result.error=$_.Exception.Message
  $_ | Format-List * -Force | Out-String | Set-Content -LiteralPath (Join-Path $local 'last-error.log') -Encoding UTF8
  Write-Host ('补充配置未完成：'+$result.error) -ForegroundColor Red
  Write-Host '错误保存在 .local/last-error.log；如有未恢复的改动，可在「快照与恢复」中恢复到关联快照。'
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
  Write-Host '本次会改动下面 5 项，每项后面是它可能造成的影响：' -ForegroundColor Yellow
  Write-PlanItem 1 'Clash 客户端：为所选订阅关联独立的项目增强脚本与固定节点组' '保留原自定义脚本；恢复时撤销项目关联，保留后加订阅和节点更新'
  Write-PlanItem 2 '严格出站防火墙：保留四条放行，阻断 SharedAccess 服务出站' 'Mihomo 一停，国内网站也一起断；连接共享、移动热点、VPN、Docker、WSL、视频通话、远程控制可能失效'
  Write-PlanItem 3 '系统 IPv6：全局关闭' '依赖 IPv6 的内网或校园网资源不可用，要重启 Windows 才完全生效'
  Write-Host '系统自动定位：关闭 Windows 定位策略；恢复时还原原值，地图/天气等定位功能会受影响。'
  Write-PlanItem 4 ('区域与时区：系统时区切换为 '+$regionProfile.label+'（'+$regionProfile.timezone_id+'），国家/地区和区域格式同步；关闭自动时区及自动校时；沙箱浏览器按节点地区设置语言/时区') '国家、日期/数字格式和时区会变化；自动校时关闭后可能产生时钟误差；应用需重新打开'
  Write-PlanItem 5 '清理系统、WinHTTP、环境变量与 Git/npm/Docker 的代理覆盖' '普通本机流量交给 TUN 分流；已打开的终端需重新启动，容器内部行为未验证'
  Write-Host ''
  Write-Host '执行时会逐条显示进度。中途失败可以在「快照与恢复」中还原本项目记录的改动；后加订阅和正常更新会保留。' -ForegroundColor White
  Write-Host '本项目的 WebRTC 保护会写入 Chrome、Edge、Firefox 的机器级策略，需重启浏览器，网页语音和视频可能受影响；不会自动关闭浏览器。' -ForegroundColor Cyan
 }
 else{
  Write-Host ''
  Write-Host ('即将把本机还原到这份快照：'+$Snapshot) -ForegroundColor Yellow
  Write-PlanItem 1 'Clash 客户端：保留当前订阅及更新，撤销项目脚本和网络设置' 'Clash Verge 会被关闭后重新启动，按当前订阅生成运行配置'
  Write-PlanItem 2 '系统状态：防火墙、IPv6、区域/语言、代理环境变量' 'IPv6 与语言改动要重启 Windows 才完全生效'
  Write-PlanItem 3 '计划任务：移除本项目注册的规则更新任务'
  Write-Host '默认 Chrome / Edge 与沙箱资料目录不会被删除；沙箱登录状态会保留。'
 }
 Write-Host ''
 $answer=Read-Host '确认执行请输入 1（直接回车 = 取消）'
 if($answer -ne '1' -and $answer -cne '是' -and $answer -ine 'YES'){Write-Host '已取消，没有做任何改动。';return}
}
$clashProgram=@(Get-Process clash-verge -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Path -Unique)
$result=[ordered]@{success=$false;action=$Action}
# 先按本次实际会跑的分支确定步骤总数，进度条才能显示「第几步/共几步」。
if($Action -eq 'apply'){
 $plan=@('检查运行环境','选择节点','生成候选配置与备份','备份系统状态','封存快照','写入 Clash 客户端配置','启动防火墙看门狗','应用系统设置','网络验证','配置规则更新方式')
} else {
 $plan=@('校验快照','移除规则更新任务','还原系统设置','还原 Clash 客户端')
}
Set-StepPlan $plan
$planCount=@($plan).Count
$planWord=if($Action -eq 'apply'){'配置'}else{'恢复'}
Write-Host ''
Write-Host ('开始'+$planWord+'，共 '+$planCount+' 步，每一步都会打印进度。') -ForegroundColor Yellow
try {
 $clientSessionTouched=$false
 $settingsTouched=$false
 if($Action -eq 'apply') {
  Write-Step '确认 Clash Verge 后台服务在运行，并自检内核的 DNS 类型过滤'
  Get-Service -Name clash_verge_service -ErrorAction Stop | Out-Null
  Assert-FirewallAvailable | Out-Null
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
     if($LASTEXITCODE -ne 0){throw 'discovery failed'}
    }catch{throw '读取当前订阅失败，未关闭 Clash。请检查客户端与订阅文件；详情见 .local/client-error.log。'}
    if($subs.Count){
     Write-Host '可用订阅：'
     for($i=0;$i -lt $subs.Count;$i++){Write-Host ('  {0}. {1}（{2} 个节点）' -f ($i+1),$subs[$i].name,@($subs[$i].nodes).Count)}
     while($true){
      $pick=Read-Host ('请输入订阅编号 [1-'+$subs.Count+']，也可以直接输入完整节点名（回车取消）')
      $index=0
      if(![int]::TryParse($pick,[ref]$index) -or ($index -ge 1 -and $index -le $subs.Count)){break}
      Write-Host ('订阅编号应为 1 到 '+$subs.Count+'，请重新输入；数字节点名请用 -Node 明确指定。') -ForegroundColor Yellow
     }
     if([int]::TryParse($pick,[ref]$index) -and $index -ge 1 -and $index -le $subs.Count){
      $chosen=$subs[$index-1];$nodes=@($chosen.nodes)
      Write-Host ('订阅「'+$chosen.name+'」的节点：')
      for($j=0;$j -lt $nodes.Count;$j++){Write-Host ('  {0}. {1}' -f ($j+1),$nodes[$j])}
      if(!$nodes.Count){throw '此订阅没有可直接选择的节点。请先在 Clash 更新订阅；仅 provider 类型需另行适配。'}
      $nodeIndex=Read-Selection '请输入节点编号' $nodes.Count
      if(!$nodeIndex){$result.status='cancelled';Write-Host '已取消配置，未关闭应用或写入配置。';return}
      $Node=[string]$nodes[$nodeIndex-1]
      if(!$Subscription){$Subscription=[string]$chosen.uid}
     } else {$Node=$pick.Trim()}
    } else {
     $Node=Read-Host '请输入订阅里已存在的完整节点名（不会自动替换成别的节点）'
    }
   }
   Write-Host ('将使用节点：'+$Node)
  }
  if([string]::IsNullOrWhiteSpace($Node)){$result.status='cancelled';Write-Host '未选择节点，已取消配置；未关闭应用或写入配置。';return}
  # Validate the exact selection before closing the client; never guess a replacement.
  $discoveryOutput=& $python (Join-Path $PSScriptRoot 'client.py') discover
  if($LASTEXITCODE -ne 0){throw '读取订阅失败，未关闭 Clash。详情见 .local/client-error.log。'}
  $available=($discoveryOutput | Select-Object -Last 1) | ConvertFrom-Json
  $matches=@($available.subscriptions | Where-Object {(@($_.nodes) -ccontains $Node) -and (!$Subscription -or $_.uid -ceq $Subscription -or $_.name -ceq $Subscription)})
  if(!$matches.Count){throw '所选订阅中没有这个精确节点。请在 Clash 更新订阅并核对完整名称；未关闭应用或写入配置。'}
  if($matches.Count -gt 1){throw '此节点在多个订阅中存在。请按订阅编号选择，或使用 -Subscription 指定唯一 UID；未关闭应用或写入配置。'}
  $Subscription=[string]$matches[0].uid
  if(!$env:CCN_NODE -or !(Test-Path -LiteralPath $env:CCN_NODE)){
 $localNode=Join-Path $project '.runtime\node\node.exe'
 if(Test-Path -LiteralPath $localNode){$env:CCN_NODE=$localNode}
 elseif(Get-Command node.exe -ErrorAction SilentlyContinue){$env:CCN_NODE=(Get-Command node.exe).Source}
 else{throw '未找到项目内 Node.js。请重新运行「一键网络.cmd」修复 .runtime；未关闭应用或写入配置。'}
}
  Write-Step '关闭 Clash Verge，生成候选配置并用内核自带的检查验证能加载'
  $clientSessionTouched=$true
  Stop-ClashClient
  $prepareArguments=@((Join-Path $PSScriptRoot 'client.py'),'prepare','--node',$Node,'--region',$Region)
  if($Subscription){$prepareArguments+=@('--subscription',$Subscription)}
  $prepared=& $python @prepareArguments;if($LASTEXITCODE -ne 0){throw '候选配置/备份生成失败，未改动任何系统设置。'}
  $planState=($prepared | Select-Object -Last 1) | ConvertFrom-Json
  $env:CCN_SNAPSHOT=$planState.snapshot
  Write-Step ('首次保存系统原值；已有记录则沿用，不覆盖。关联快照：'+$planState.snapshot)
  & (Join-Path $PSScriptRoot 'system.ps1') -Action snapshot -Python $python
  Write-Step '封存快照，确认备份完整可恢复'
  & $python (Join-Path $PSScriptRoot 'client.py') seal;if($LASTEXITCODE -ne 0){throw '系统备份封存失败。'}
  Write-Step '写入 Clash 客户端配置：增强脚本、固定节点组、分流规则'
  $settingsTouched=$true
  & $python (Join-Path $PSScriptRoot 'client.py') apply-client;if($LASTEXITCODE -ne 0){throw '客户端配置写入失败。'}
  Write-Step '启动防火墙保护：180 秒未确认成功则进入回滚，正在写入的步骤会先完成'
  $watchToken='guard-'+[Guid]::NewGuid().ToString('N')
  [ordered]@{token=$watchToken;snapshot=$env:CCN_SNAPSHOT} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $local 'pending-firewall.json.new') -Encoding UTF8
  Move-Item -LiteralPath (Join-Path $local 'pending-firewall.json.new') -Destination (Join-Path $local 'pending-firewall.json') -Force
  $watchArgs=@('-NoProfile','-ExecutionPolicy','Bypass','-File',('"'+(Join-Path $PSScriptRoot 'watchdog.ps1')+'"'),'-Token',$watchToken,'-Python',('"'+$python+'"'))
  Start-Process -FilePath 'powershell.exe' -WindowStyle Hidden -ArgumentList $watchArgs | Out-Null
  Write-Step '应用系统设置：严格防火墙、关闭 IPv6/定位/自动时间、国家/区域格式/时区、代理环境变量'
  $applyMutex=New-Object Threading.Mutex($false,('Global\CCN-'+$watchToken))
  try{[void]$applyMutex.WaitOne()}catch [Threading.AbandonedMutexException]{}
  try{
   if(Test-Path -LiteralPath (Join-Path $local ($watchToken+'.rollback'))){throw '后台回滚已开始，停止配置。'}
   & (Join-Path $PSScriptRoot 'system.ps1') -Action apply -Python $python -TimeZoneId $regionProfile.timezone_id -RegionCode $regionProfile.region -CultureName $regionProfile.culture
   $firewallPlan=Get-Content -LiteralPath (Join-Path $local 'plan.json') -Raw -Encoding UTF8 | ConvertFrom-Json
   Assert-StrictFirewall -coreProgram ([string]$firewallPlan.core_program)
  }finally{$applyMutex.ReleaseMutex();$applyMutex.Dispose()}
  Write-Step '即将短暂中断代理及全部公网，分别验证节点故障、停核保护和恢复；多项检测可能需要数分钟'
  $env:CCN_VERIFY_RUN=[Guid]::NewGuid().ToString('N')
  # This coordinator already owns operation.lock; call the internal verifier.
  & $python -c 'import sys;sys.path.insert(0,sys.argv[1]);from verify import verify;verify(outage=True)' $PSScriptRoot;if($LASTEXITCODE -ne 0){throw (Get-VerificationFailureMessage '网络验证未通过。')}
  Write-Host '  节点故障阶段已通过；开始停核测试，国内公网也会暂时断开。' -ForegroundColor Cyan
  & $python (Join-Path $PSScriptRoot 'verify_core_exit.py');if($LASTEXITCODE -ne 0){throw (Get-VerificationFailureMessage '内核退出保护/重启验证未通过。')}
  Write-Host '  停核测试已通过；开始恢复后的连通性复测。' -ForegroundColor Cyan
  & $python -c 'import sys;sys.path.insert(0,sys.argv[1]);from verify import verify;verify(stage=''post-recovery'')' $PSScriptRoot;if($LASTEXITCODE -ne 0){throw (Get-VerificationFailureMessage '内核退出测试之后网络没有恢复。')}
  $guardMutex=New-Object Threading.Mutex($false,('Global\CCN-'+$watchToken))
  try{[void]$guardMutex.WaitOne()}catch [Threading.AbandonedMutexException]{}
  try {
   if(Test-Path -LiteralPath (Join-Path $local ($watchToken+'.rollback'))){throw '防火墙看门狗已回滚，本次部署不算成功。'}
   $firewallPlan=Get-Content -LiteralPath (Join-Path $local 'plan.json') -Raw -Encoding UTF8 | ConvertFrom-Json
   Assert-StrictFirewall -coreProgram ([string]$firewallPlan.core_program)
   Set-Content -LiteralPath (Join-Path $local ($watchToken+'.commit')) -Value 'Verified'
  } finally {$guardMutex.ReleaseMutex();$guardMutex.Dispose()}
  Write-Host ('沙箱浏览器：设置 '+$regionProfile.label+' 语言；系统时区已切换，Claude 桌面/浏览器重启后读取新时区')
  $browserScript=Join-Path $PSScriptRoot 'sandbox_browser.py'
  if(Test-Path -LiteralPath $browserScript){
   $browserRegionJson=& $python $browserScript --configure-region $Region
   if($LASTEXITCODE -ne 0){Write-Warning '沙箱浏览器地区配置未完成；网络配置仍已生效。'}
   else{
    try{$browserRegion=$browserRegionJson | ConvertFrom-Json;if($browserRegion.running){Write-Host '沙箱浏览器正在运行：语言在关闭并重新打开后生效；系统时区变化也需重启浏览器/Claude'} }catch{}
   }
  }

  $updateMode='scheduled';$updateTime='13:00';$updateDays=1
  try{
   $uiSettings=Get-Content -LiteralPath (Join-Path $local 'ui-settings.json') -Raw -Encoding UTF8 | ConvertFrom-Json
   if([string]$uiSettings.update.mode -in @('manual','scheduled')){$updateMode=[string]$uiSettings.update.mode}
   if([string]$uiSettings.update.time -match '^(?:[01]\d|2[0-3]):[0-5]\d$'){$updateTime=[string]$uiSettings.update.time}
   if([string]$uiSettings.update.days -match '^\d+$'){$candidateDays=[int]$uiSettings.update.days;if($candidateDays -ge 1 -and $candidateDays -le 365){$updateDays=$candidateDays}}
  }catch{}
  if($updateMode -eq 'manual'){
   & (Join-Path $PSScriptRoot 'update_task.ps1') -Action remove -Python $python
   if($LASTEXITCODE -ne 0){throw '移除规则更新任务失败。'}
   Write-Step '规则更新方式：手动。不注册后台任务；打开界面后可在「规则更新」中立即检查'
  } else {
   & (Join-Path $PSScriptRoot 'update_task.ps1') -Action install -Python $python -At $updateTime -DaysInterval $updateDays
   if($LASTEXITCODE -ne 0){throw '注册规则更新任务失败。'}
   $intervalText=if($updateDays -eq 1){'每天'}else{'每 '+$updateDays+' 天'}
   Write-Step ('规则更新方式：'+$intervalText+' '+$updateTime+' 自动更新已审核的国内规则来源，不更新内核')
  }
 } else {
  Write-Step '校验快照与本机是否匹配，不匹配就不动任何设置'
  # `python -c` only puts the current directory on sys.path, and the menu entry
  # starts this script from the project root, so baseline.py was never importable.
  # Do not build a quoted path into the -c string: PowerShell 5.1 strips embedded
  # double quotes when calling a native program. Switch directory instead.
  Push-Location $PSScriptRoot
  try{& $python -c 'from baseline import preflight_restore;preflight_restore()'}finally{Pop-Location}
  if($LASTEXITCODE -ne 0){throw '快照校验失败，未恢复任何设置。'}
  $clientSessionTouched=$true
  $settingsTouched=$true
  Write-Step '移除本项目的规则更新任务'
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
  Write-Step '保留订阅及节点更新，撤销项目脚本并重新生成运行配置'
  Stop-ClashClient
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
   Write-Host '  正在启动 Clash，等待它按保留后的当前订阅重新生成配置（最多 90 秒）。'
   Start-ClashClient
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
  Write-Host '  固定节点组 Claude-Overseas-Pinned 只包含你指定的节点。要撤销请在「快照与恢复」中恢复。'
  Write-Host '  已完成自动网络验收；沙箱浏览器自检、虚拟网络、Claude 与账号可用性未验证。'
  Write-Host '  默认 Chrome / Edge 未被修改；需要时请从界面「浏览器沙箱」打开独立资料目录。'
  try{
   $verification=Get-Content -LiteralPath (Join-Path $local 'verification.json') -Raw -Encoding UTF8 | ConvertFrom-Json
   foreach($tool in @('git','npm')){
    if($verification.$tool.status -eq 'not-tested'){Write-Host ('  '+$tool+' 未测试：未安装此工具；不能视为该项已通过。') -ForegroundColor Yellow}
   }
  }catch{Write-Host '  无法读取验收摘要，请检查 .local/verification-runs 中本次记录。' -ForegroundColor Yellow}
 } else {
  Write-Done ('恢复已完成（共 '+$planCount+' 步）。使用的快照：'+$env:CCN_SNAPSHOT)
  Write-Host '  已还原记录中的设置；恢复流程未运行网络验收，请自行确认联网。'
 }
 Write-Host '  IPv6 和语言改动要重启 Windows 才完全生效。网络测试不能证明 Claude 账号本身可用。'
} catch {
 $runError=$_
 $result.error=$_.Exception.Message
 $result.failedStep=$script:stepNo
 Write-GuiEvent 'error' ([ordered]@{message=$runError.Exception.Message;step=$script:stepNo;total=@($script:stepTitles).Count})
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
  try{
   Resolve-PendingFirewall -LocalDir $local -Python $python -ExpectedToken $watchToken -Force
   $result.firewallRollback=$true
   Write-Host '  防火墙回滚已完成；这不代表其他设置已恢复。' -ForegroundColor Yellow
  }catch{
   $result.firewallRollback=$false
   $_ | Out-String | Set-Content -LiteralPath (Join-Path $local 'rollback-error.log') -Encoding UTF8
   Write-Host '  防火墙回滚失败，公网可能仍被阻断。请保留 .local，从界面「快照与恢复」重试；详情见 .local/rollback-error.log。' -ForegroundColor Red
  }
 }
 Write-Error $runError -ErrorAction Continue
 if(!$settingsTouched){
  Write-Host '所选操作在写入配置前停止，没有应用或还原网络设置。请处理上述原因后重试，无需为此执行恢复。'
 }elseif($Action -eq 'apply'){
  Write-Host '配置没有完成，可能只改了一部分：看门狗只回滚防火墙规则，时区、语言、IPv6 和代理设置不会自动还原。重试之前请先在「快照与恢复」中恢复。'
 } else {
  Write-Host '恢复没有完成，可能只还原了一部分。可以直接再执行一次「恢复」。'
 }
} finally {
 $result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $local 'last-run.json') -Encoding UTF8
 try{
  if($clientSessionTouched){Start-ClashClient}
 }catch{Write-Host '无法自动打开 Clash，请手动启动客户端；此提示不改变上面的配置/恢复结果。' -ForegroundColor Yellow}
  # 配置/恢复不会关闭或修改默认浏览器；沙箱浏览器由用户从界面显式打开。

}
$operationStream.Dispose()
if(!$NoPause){Read-Host '按回车键关闭窗口'}
if(!$result.success){exit 1}

} finally {$operationStream.Dispose()}
} catch {
 $startupError=$_
 Write-GuiEvent 'error' ([ordered]@{message=$startupError.Exception.Message})
 [ordered]@{success=$false;action=$Action;status='failed';error=$startupError.Exception.Message} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $local 'last-run.json') -Encoding UTF8
 $_ | Format-List * -Force | Out-String | Set-Content -LiteralPath (Join-Path $local 'last-error.log') -Encoding UTF8
 Write-Host ('操作未完成：'+$_.Exception.Message) -ForegroundColor Red
 Write-Host '详情见 .local/last-error.log。请保留 .local 中的快照和恢复记录；不要删除记录或用当前状态覆盖首次基线。'
 if($Action -eq 'snapshot'){Write-Host '本次快照未保存完整，不能用作已验证恢复点；请排除错误后重新保存。'}
 if(!$NoPause){Read-Host '按回车键关闭窗口'}
 exit 1
}
