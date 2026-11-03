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
# Resolve current compatible releases only when installation is needed. Historical
# pins in assets/runtime-provenance.json remain a reference, not an update lock.
$downloadHashes=@{}
$resolvedFiles=@{}
function Get-OfficialBytes([string]$url){
 $uri=[uri]$url
 if($uri.Scheme -ne 'https' -or $uri.Host -notin @('www.python.org','nodejs.org','bootstrap.pypa.io') -or $uri.Port -ne 443 -or $uri.UserInfo){Fail '运行环境来源必须是允许的官方 HTTPS 地址。'}
 # No redirects: never follow metadata or executable code to another host.
 $response=Invoke-WebRequest -UseBasicParsing -Uri $uri -MaximumRedirection 0 -TimeoutSec 60
 if($response.StatusCode -ne 200){Fail '官方来源响应异常。'}
 $bytes=$response.RawContentStream.ToArray()
 if(!$bytes.Length -or $bytes.Length -gt 8MB){Fail '官方来源内容为空或过大。'}
 return ,$bytes
}
function Get-OfficialText([string]$url){
 return [Text.Encoding]::UTF8.GetString((Get-OfficialBytes $url))
}
function Register-Download([string]$name,[string]$url,[string]$source,[string]$digest,[string]$method){
 if($name -notmatch '^[A-Za-z0-9._-]+$' -or $digest -notmatch '^[a-fA-F0-9]{64}$'){Fail '官方文件名或 SHA256 无效。'}
 $downloadHashes[$name]=$digest
 $resolvedFiles[$name]=[ordered]@{name=$name;download_url=$url;source_url=$source;sha256=$digest.ToLowerInvariant();method=$method;resolved_at=(Get-Date).ToUniversalTime().ToString('o')}
 # Machine-local record of actual selections; never rewrite the shipped reference.
 @($resolvedFiles.Values) | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $local 'runtime-downloads.json') -Encoding UTF8
}
function Select-PythonVersion([string]$listing){
 $versions=@([regex]::Matches($listing,'href="(3\.13\.\d+)/"') | ForEach-Object {$_.Groups[1].Value} | Sort-Object {[version]$_} -Descending -Unique)
 if(!$versions.Count){Fail '官方目录中没有 Python 3.13 稳定版。'}
 return $versions[0]
}
function Select-NodeVersion($releases){
 $versions=@($releases | Where-Object {$_.version -cmatch '^v\d+\.\d+\.\d+$' -and $_.lts -is [string] -and $_.lts -and $_.files -contains 'win-x64-zip'} | Sort-Object {[version]$_.version.Substring(1)} -Descending)
 if(!$versions.Count){Fail '官方目录中没有 Windows x64 Node.js LTS 稳定版。'}
 return [string]$versions[0].version
}
function Resolve-PythonDownload {
 $listing=Get-OfficialText 'https://www.python.org/ftp/python/'
 $null=Select-PythonVersion $listing
 $versions=@([regex]::Matches($listing,'href="(3\.13\.\d+)/"') | ForEach-Object {$_.Groups[1].Value} | Sort-Object {[version]$_} -Descending -Unique)
 $version=$null
 foreach($candidate in $versions){
  $candidateName='python-'+$candidate+'-embed-amd64.zip'
  $base='https://www.python.org/ftp/python/'+$candidate+'/'
  $files=Get-OfficialText $base
  # Security-only Python releases can contain source archives without Windows
  # binaries. Select the newest release actually providing our embedded package.
  if($files.Contains('href="'+$candidateName+'"') -and $files.Contains('href="'+$candidateName+'.sigstore"')){
   $version=$candidate;$name=$candidateName;$url=$base+$name;break
  }
 }
 if(!$version){Fail '官方目录中没有可校验的 Python 3.13 Windows x64 嵌入包。'}
 $source=$url+'.sigstore'
 $record=(Get-OfficialText $source | ConvertFrom-Json).messageSignature.messageDigest
 if($record.algorithm -ne 'SHA2_256'){Fail 'Python 官方摘要算法无效。'}
 $bytes=[Convert]::FromBase64String($record.digest)
 if($bytes.Length -ne 32){Fail 'Python 官方摘要长度无效。'}
 $digest=([BitConverter]::ToString($bytes)).Replace('-','').ToLowerInvariant()
 Register-Download $name $url $source $digest 'sigstore-published-digest'
 return [pscustomobject]@{version=$version;name=$name;url=$url}
}
function Resolve-NodeDownload {
 $version=Select-NodeVersion (Get-OfficialText 'https://nodejs.org/dist/index.json' | ConvertFrom-Json)
 $name='node-'+$version+'-win-x64.zip'
 $base='https://nodejs.org/dist/'+$version+'/'
 $source=$base+'SHASUMS256.txt'
 $pattern='(?m)^([a-fA-F0-9]{64})\s+'+[regex]::Escape($name)+'\s*$'
 $matches=[regex]::Matches((Get-OfficialText $source),$pattern)
 if($matches.Count -ne 1){Fail 'Node.js 官方摘要缺失或重复。'}
 Register-Download $name ($base+$name) $source $matches[0].Groups[1].Value 'shasums256'
 return [pscustomobject]@{version=$version;name=$name;url=($base+$name)}
}
function Save-LatestGetPip([string]$target){
 $url='https://bootstrap.pypa.io/get-pip.py'
 $bytes=Get-OfficialBytes $url
 $sha=[Security.Cryptography.SHA256]::Create()
 try{$digest=([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-','').ToLowerInvariant()}finally{$sha.Dispose()}
 # PyPA publishes this script over HTTPS without a separate checksum. Record
 # that trust explicitly, then verify the saved bytes before execution.
 Register-Download 'get-pip.py' $url $url $digest 'official-https-content-sha256'
 [IO.File]::WriteAllBytes($target,$bytes)
 Assert-DownloadHash $target
}
function Assert-DownloadHash([string]$target){
 $name=Split-Path -Leaf $target
 $expected=$downloadHashes[$name]
 if(!$expected){Fail ('下载文件没有固定的 SHA256：'+$name)}
 if((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $expected){
  Fail ('下载文件 SHA256 不匹配，已停止安装：'+$name)
 }
}
function Download([string]$url,[string]$target){
 if(Test-Path -LiteralPath $target){
  try{Assert-DownloadHash $target;return}catch{Remove-Item -LiteralPath $target -Force}
 }
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
 Assert-DownloadHash $target
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
 $release=Resolve-PythonDownload
 $zip=Join-Path $downloads $release.name
 Download $release.url $zip
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

function Test-Pip {
 try{& $python -m pip --version 2>$null | Out-Null;return ($LASTEXITCODE -eq 0)}catch{return $false}
}

function Ensure-Pip {
 # Python can survive an interrupted first install without pip. Repair independently
 # after configuring its import paths, using the current official bootstrap.
 if(Test-Pip){return}
 if($NoInstall){Fail '缺少项目内 pip。请运行 一键网络.cmd 自动修复 .runtime。'}
 Say '  安装/修复项目内 pip…' 'Yellow'
 $getpip=Join-Path $downloads 'get-pip.py'
 Save-LatestGetPip $getpip
 & $python $getpip --no-user --no-warn-script-location --disable-pip-version-check
 if($LASTEXITCODE -ne 0 -or !(Test-Pip)){Fail '项目内 pip 安装/修复失败；请检查上方输出及 .runtime 目录权限后重试。'}
}

function Ensure-Yaml {
 if(Test-Yaml){Say '  PyYAML 已就绪' 'Green';return}
 if($NoInstall){Fail '缺少项目内 PyYAML。请运行 一键网络.cmd 自动安装。'}
 Ensure-Pip
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
 $release=Resolve-NodeDownload
 $zip=Join-Path $downloads $release.name
 Download $release.url $zip
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
