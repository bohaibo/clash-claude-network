param([switch]$NoInstall,[switch]$Quiet)
$ErrorActionPreference='Stop'
$env:PYTHONNOUSERSITE='1'
$project=Split-Path -Parent $PSScriptRoot
$runtime=Join-Path $project '.runtime'
$local=Join-Path $project '.local'
$downloads=Join-Path $runtime 'downloads'
$pythonRoot=Join-Path $runtime 'python313'
$python=Join-Path $pythonRoot 'python.exe'
$pythonw=Join-Path $pythonRoot 'pythonw.exe'
$nodeRoot=Join-Path $runtime 'node'
$node=Join-Path $nodeRoot 'node.exe'
$envFile=Join-Path $local 'env.json'
New-Item -ItemType Directory -Path $runtime,$local,$downloads -Force | Out-Null
try{[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12}catch{}

function Say([string]$text,[string]$color='White'){if(!$Quiet){Write-Host $text -ForegroundColor $color}}
function Fail([string]$text){throw $text}
function Download([string]$url,[string]$target){
 Say ('  下载 '+$url) 'DarkGray'
 $curl=Join-Path $env:SystemRoot 'System32\curl.exe'
 if(Test-Path -LiteralPath $curl){
  & $curl -4 -L --fail --silent --show-error --retry 3 --retry-delay 2 --max-time 1200 -C - --output $target $url
  if($LASTEXITCODE -ne 0){Fail ('下载失败：'+$url)}
 } else {
  $old=$ProgressPreference;$ProgressPreference='SilentlyContinue'
  try{Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $target -TimeoutSec 600}finally{$ProgressPreference=$old}
 }
 if(!(Test-Path -LiteralPath $target) -or (Get-Item -LiteralPath $target).Length -lt 1024){Fail ('下载失败：'+$url)}
}

function Test-Python313 {
 if(!(Test-Path -LiteralPath $python)){return $false}
 try{$value=& $python -c 'import sys;print(''%d.%d''%sys.version_info[:2])' 2>$null}catch{return $false}
 return ($value -eq '3.13')
}

function Ensure-Python {
 if(Test-Python313){Say ('  Python 3.13 已就绪：'+$python) 'Green';return}
 if($NoInstall){Fail '缺少项目内 Python 3.13。请运行 一键网络.cmd 自动安装到 .runtime。'}
 Say '  安装项目内 Python 3.13（不修改系统 PATH，不安装到系统目录）…' 'Yellow'
 $page=$null
 try{$page=(Invoke-WebRequest -UseBasicParsing -Uri 'https://www.python.org/ftp/python/' -TimeoutSec 60).Content}catch{}
 $version='3.13.17'
 if($page){
  $matches=[regex]::Matches([string]$page,'href="(3\.13\.\d+)/"')
  $candidates=@($matches | ForEach-Object {$_.Groups[1].Value} | Sort-Object {[version]$_} -Descending)
  foreach($candidate in $candidates){
   $probe='https://www.python.org/ftp/python/'+$candidate+'/python-'+$candidate+'-embed-amd64.zip'
   try{Invoke-WebRequest -UseBasicParsing -Method Head -Uri $probe -TimeoutSec 30 | Out-Null;$version=$candidate;break}catch{}
  }
 }
 $zip=Join-Path $downloads ('python-'+$version+'-embed-amd64.zip')
 Download ('https://www.python.org/ftp/python/'+$version+'/python-'+$version+'-embed-amd64.zip') $zip
 $temp=Join-Path $runtime 'python-temp'
 Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
 Expand-Archive -LiteralPath $zip -DestinationPath $temp -Force
 if(Test-Path -LiteralPath $pythonRoot){Remove-Item -LiteralPath $pythonRoot -Recurse -Force}
 Move-Item -LiteralPath $temp -Destination $pythonRoot
 # Embedded Python needs site-packages enabled; keep everything project-local.
 $pth=Get-ChildItem -LiteralPath $pythonRoot -Filter '*._pth' | Select-Object -First 1
 if($pth){
  @('python313.zip','.','Lib\site-packages','..\..\scripts','import site') | Set-Content -LiteralPath $pth.FullName -Encoding ASCII
 }
 $getpip=Join-Path $downloads 'get-pip.py'
 Download 'https://bootstrap.pypa.io/get-pip.py' $getpip
 & $python $getpip --no-warn-script-location --disable-pip-version-check
 if($LASTEXITCODE -ne 0){Fail '项目内 Python 安装 pip 失败。'}
}

function Ensure-Pth {
 $pth=Get-ChildItem -LiteralPath $pythonRoot -Filter '*._pth' -ErrorAction SilentlyContinue | Select-Object -First 1
 if(!$pth){return}
 $wanted=@('python313.zip','.','Lib\site-packages','..\..\scripts','import site')
 $current=@(Get-Content -LiteralPath $pth.FullName -Encoding ASCII)
 if(($current -join '|') -ne ($wanted -join '|')){
  $wanted | Set-Content -LiteralPath $pth.FullName -Encoding ASCII
 }
}

function Ensure-Sitecustomize {
 $site=Join-Path $pythonRoot 'Lib\site-packages'
 New-Item -ItemType Directory -Path $site -Force | Out-Null
 $code=@(
  '"""Emulate normal Python script-directory sys.path entry."""'
  'import os'
  'import sys'
  ''
  'try:'
  '    script = os.path.abspath(sys.argv[0]) if getattr(sys, "argv", None) else ""'
  '    folder = os.path.dirname(script)'
  '    if folder and folder not in sys.path:'
  '        sys.path.insert(0, folder)'
  'except Exception:'
  '    pass'
 )
 Set-Content -LiteralPath (Join-Path $site 'sitecustomize.py') -Value $code -Encoding UTF8
}

function Test-Yaml {
 try{& $python -c 'import yaml' 2>$null;return ($LASTEXITCODE -eq 0)}catch{return $false}
}

function Ensure-Yaml {
 if(Test-Yaml){Say '  PyYAML 已就绪' 'Green';return}
 if($NoInstall){Fail '缺少项目内 PyYAML。请运行 一键网络.cmd 自动安装。'}
 Say '  安装 PyYAML 到项目内 Python…' 'Yellow'
 & $python -m pip install --no-warn-script-location --disable-pip-version-check --no-user -r (Join-Path $project 'requirements.txt')
 if($LASTEXITCODE -ne 0 -or !(Test-Yaml)){Fail 'PyYAML 安装失败；请检查网络或 .runtime 目录权限。'}
}

function Test-Node {
 if(!(Test-Path -LiteralPath $node)){return $false}
 try{$value=& $node --version 2>$null}catch{return $false}
 return ($value -match '^v\d+\.')
}

function Ensure-Node {
 if(Test-Node){Say ('  Node.js 已就绪：'+$node) 'Green';return}
 if($NoInstall){Fail '缺少项目内 Node.js。请运行 一键网络.cmd 自动安装到 .runtime。'}
 Say '  安装项目内 Node.js（不修改系统 PATH）…' 'Yellow'
 $index=$null
 try{$index=Invoke-RestMethod -Uri 'https://nodejs.org/dist/index.json' -TimeoutSec 60}catch{}
 $version='v26.13.0'
 if($index){
  $lts=@($index | Where-Object {$_.lts -ne $false} | Select-Object -First 1)
  if($lts.Count -and $lts[0].version){$version=[string]$lts[0].version}
 }
 $name='node-'+$version+'-win-x64'
 $zip=Join-Path $downloads ($name+'.zip')
 Download ('https://nodejs.org/dist/'+$version+'/'+$name+'.zip') $zip
 $temp=Join-Path $runtime 'node-temp'
 Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
 Expand-Archive -LiteralPath $zip -DestinationPath $temp -Force
 if(Test-Path -LiteralPath $nodeRoot){Remove-Item -LiteralPath $nodeRoot -Recurse -Force}
 $inner=Get-ChildItem -LiteralPath $temp -Directory | Select-Object -First 1
 if(!$inner){Fail 'Node.js 压缩包结构异常。'}
 Move-Item -LiteralPath $inner.FullName -Destination $nodeRoot
 Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
 if(!(Test-Node)){Fail '项目内 Node.js 安装后仍不可用。'}
}

function Test-Clash {
 $service=Get-Service -Name clash_verge_service -ErrorAction SilentlyContinue
 $data=Join-Path $env:APPDATA 'io.github.clash-verge-rev.clash-verge-rev'
 $profiles=Join-Path $data 'profiles.yaml'
 if(!$service -or !(Test-Path -LiteralPath $profiles)){
  Fail ('未检测到 Clash Verge Rev 服务或数据目录。本项目不会安装 Clash Verge 或其内核，请先安装并启动 Clash Verge Rev。' + "`n" +
        '  Clash Verge Rev：https://github.com/clash-verge-rev/clash-verge-rev/releases' + "`n" +
        '  安装后确认服务 clash_verge_service 为 Running，并且至少登录一次客户端。')
 }
 return $service
}

Say '检查项目内运行环境（不会安装到系统目录，不修改 PATH）…' 'Cyan'
$clash=Test-Clash
Ensure-Python
Ensure-Pth
Ensure-Sitecustomize
Ensure-Yaml
Ensure-Node
$pythonVersion=(& $python -c 'import sys;print(''%d.%d.%d''%sys.version_info[:3])').Trim()
$nodeVersion=(& $node --version).Trim()
$state=[ordered]@{
 python=$python;pythonw=$pythonw;node=$node;
 python_version=$pythonVersion;node_version=$nodeVersion;
 clash_service=[string]$clash.Status;checked=(Get-Date).ToString('o');
 scope='project-local .runtime; no system PATH or registry changes'
}
$state | ConvertTo-Json | Set-Content -LiteralPath $envFile -Encoding UTF8
Say ''
Say ('环境就绪：Python '+$pythonVersion+' · Node '+$nodeVersion+' · Clash '+$clash.Status) 'Green'
Say ('项目内路径：'+$runtime) 'Gray'
exit 0
