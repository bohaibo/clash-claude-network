param([switch]$NoPause)
$ErrorActionPreference='Stop'
$env:PYTHONNOUSERSITE='1'
$project=Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $project
try{[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;[Console]::ForegroundColor=[ConsoleColor]::White}catch{}

function Show-Failure([string]$message){
 Write-Host ''
 Write-Host $message -ForegroundColor Red
 $log=Join-Path $project '.local\gui\server.log'
 if(Test-Path -LiteralPath $log){
  Write-Host '最近的界面服务记录：' -ForegroundColor Yellow
  Get-Content -LiteralPath $log -Tail 20 -Encoding UTF8 | ForEach-Object { Write-Host ('  '+$_) }
 }
 if(!$NoPause){Read-Host '按回车键关闭窗口'}
 exit 1
}

try {
$bootstrap=Join-Path $PSScriptRoot 'bootstrap.ps1'
$envFile=Join-Path $project '.local\env.json'
# bootstrap.ps1 owns the single environment-check banner.
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $bootstrap
if($LASTEXITCODE -ne 0){Show-Failure '项目内环境未就绪。安装过程不会修改系统 PATH；请查看上方错误后重试。'}
if(!(Test-Path -LiteralPath $envFile)){Show-Failure '缺少 .local\env.json。请重新运行「一键网络.cmd」。'}
$envState=Get-Content -LiteralPath $envFile -Raw -Encoding UTF8 | ConvertFrom-Json
$python=[string]$envState.python
$pythonw=[string]$envState.pythonw
if(!$python -or !(Test-Path -LiteralPath $python) -or !$pythonw -or !(Test-Path -LiteralPath $pythonw)){Show-Failure '项目内 Python 3.13 不可用。请删除 .runtime 后重新运行「一键网络.cmd」。'}
$guiServer=Join-Path $PSScriptRoot 'gui_server.py'
if(!(Test-Path -LiteralPath $guiServer)){Show-Failure '缺少 scripts\gui_server.py，界面无法启动。'}
$local=Join-Path $project '.local'
$session=Join-Path $local 'gui\session.json'
New-Item -Path (Join-Path $local 'gui') -ItemType Directory -Force | Out-Null

# A stale session (e.g. after a crash) must not make this launcher exit before
# the new server can bind. A live port is left in place: gui_server.py will
# reuse it and only open another GUI window.
if(Test-Path -LiteralPath $session){
 try{
  $oldSession=Get-Content -LiteralPath $session -Raw -Encoding UTF8 | ConvertFrom-Json
  $probe=New-Object System.Net.Sockets.TcpClient
  $probe.Connect('127.0.0.1',[int]$oldSession.port)
  $probe.Close()
 }catch{
  Remove-Item -LiteralPath $session -Force -ErrorAction SilentlyContinue
 }
}

$arguments=@(('"'+$guiServer+'"'),'--open')
$startupOut=Join-Path $local 'gui\startup.stdout.log'
$startupErr=Join-Path $local 'gui\startup.stderr.log'
try{
 $process=Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $project -WindowStyle Hidden -PassThru -RedirectStandardOutput $startupOut -RedirectStandardError $startupErr
}catch{
 Show-Failure ('无法启动界面服务：'+$_.Exception.Message)
}

# A session is written before browser startup. Verify both before closing.
$probeCode=@'
import sys
import json
import urllib.request
from gui_server import App
app = App(open_browser=False)
ready = False
try:
    state = json.loads(app.session_file.read_text(encoding='utf-8-sig'))
    request = urllib.request.Request('http://127.0.0.1:%s/api/ping' % state['port'], headers={'X-CCN-Token': state['token']})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=2) as response:
        ready = json.load(response).get('page_ready') is True
except Exception:
    pass
sys.exit(0 if ready else 1)
'@
$deadline=[DateTime]::UtcNow.AddSeconds(60)
while([DateTime]::UtcNow -lt $deadline){
 if(Test-Path -LiteralPath $session){
  & $python -c $probeCode
  if($LASTEXITCODE -eq 0){
   Write-Host '界面已在独立浏览器中打开。' -ForegroundColor Green
   exit 0
  }
 }
 $process.Refresh()
 if($process.HasExited -and $process.ExitCode -ne 0){Show-Failure '界面启动进程异常退出。详情见 .local\gui\startup.stderr.log。'}
 Start-Sleep -Milliseconds 400
}
Show-Failure '界面或独立浏览器在 60 秒内没有就绪。请查看 .local\gui\server.log 和 startup.stderr.log。'
}catch{
 Show-Failure ('界面启动失败：'+$_.Exception.Message)
}
