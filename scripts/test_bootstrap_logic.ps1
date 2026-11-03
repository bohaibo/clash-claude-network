# Offline: extract functions only; no real interpreter, downloads or bootstrap entry.
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'bootstrap.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Bootstrap parse failed'}
foreach($name in @('Fail','Assert-DownloadHash','Ensure-Python','Test-Pip','Ensure-Pip','Test-Yaml','Ensure-Yaml','Select-PythonVersion','Select-NodeVersion','Resolve-PythonDownload','Resolve-NodeDownload','Get-OfficialBytes','Get-OfficialText','Register-Download','Save-LatestGetPip')){
 $fn=@($ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst]},$true) | Where-Object Name -eq $name)
 if($fn.Count -ne 1){throw ('Missing or duplicate bootstrap function: '+$name)}
 Invoke-Expression $fn[0].Extent.Text
}
function Assert([bool]$condition,[string]$message){if(!$condition){throw $message}}
$downloadHashes=@{'fixture.zip'='A'*64}
function Get-FileHash {param($LiteralPath,$Algorithm) [pscustomobject]@{Hash=$script:digest}}
$script:digest='A'*64
Assert-DownloadHash 'fixture.zip'
$script:digest='B'*64
$rejected=$false
try{Assert-DownloadHash 'fixture.zip'}catch{$rejected=$true}
Assert $rejected 'Corrupted archive accepted'
$rejected=$false
try{Assert-DownloadHash 'unknown.zip'}catch{$rejected=$true}
Assert $rejected 'Unpinned input accepted'

Remove-Item Function:Get-FileHash
function Assert-Rejected([scriptblock]$action,[string]$message){
 $rejected=$false
 try{& $action | Out-Null}catch{$rejected=$true}
 Assert $rejected $message
}

# Synthetic release metadata intentionally arrives out of order.
Assert ((Select-PythonVersion '<a href="3.13.9/"><a href="3.13.21/"><a href="3.14.99/"><a href="3.13.22rc1/">') -eq '3.13.21') 'Python selection must use numeric compatible stable versions'
Assert-Rejected {Select-PythonVersion '<a href="3.14.1/"><a href="3.13.1rc1/">'} 'Python empty candidates accepted'
$releases=@(
 [pscustomobject]@{version='v24.9.0';lts='Fixture';files=@('win-x64-zip')}
 [pscustomobject]@{version='v24.12.0';lts='Fixture';files=@('win-x64-zip')}
 [pscustomobject]@{version='v30.0.0';lts=$false;files=@('win-x64-zip')}
 [pscustomobject]@{version='v28.0.0';lts='Fixture';files=@('win-arm64-zip')}
 [pscustomobject]@{version='v26.0.0-rc1';lts='Fixture';files=@('win-x64-zip')}
)
Assert ((Select-NodeVersion $releases) -eq 'v24.12.0') 'Node selection ignored LTS, platform, stable version or numeric ordering'
Assert-Rejected {Select-NodeVersion @()} 'Node empty candidates accepted'

# Keep real registration and filesystem hash verification; replace only transport.
& {
 $local=Join-Path (Split-Path -Parent $PSScriptRoot) ('.local/bootstrap-fixture-'+[guid]::NewGuid().ToString('N'))
 New-Item -ItemType Directory -Path $local -Force | Out-Null
 $downloadHashes=@{};$resolvedFiles=@{}
 $script:officialSources=@{}
 function Get-OfficialText([string]$url){
  if(!$script:officialSources.ContainsKey($url)){throw 'Unexpected official metadata URL'}
  return $script:officialSources[$url]
 }
 $pythonBase='https://www.python.org/ftp/python/'
 $script:officialSources[$pythonBase]='<a href="3.13.22/"><a href="3.13.21/">'
 $script:officialSources[$pythonBase+'3.13.22/']='<a href="Python-3.13.22.tar.xz">'
 $script:officialSources[$pythonBase+'3.13.21/']='<a href="python-3.13.21-embed-amd64.zip"><a href="python-3.13.21-embed-amd64.zip.sigstore">'
 $pythonSource=$pythonBase+'3.13.21/python-3.13.21-embed-amd64.zip.sigstore'
 $digestBytes=New-Object byte[] 32
 $digestBytes[0]=1
 $encoded=[Convert]::ToBase64String($digestBytes)
 $script:officialSources[$pythonSource]='{"messageSignature":{"messageDigest":{"algorithm":"SHA2_256","digest":"'+$encoded+'"}}}'
 $release=Resolve-PythonDownload
 Assert ($release.version -eq '3.13.21' -and $downloadHashes[$release.name] -eq ('01'+('00'*31))) 'Python official digest not registered'
 $downloadHashes.Clear();$resolvedFiles.Clear()
 $script:officialSources[$pythonBase+'3.13.21/']='<a href="Python-3.13.21.tar.xz">'
 Assert-Rejected {Resolve-PythonDownload} 'Source-only Python release accepted as embedded runtime'
 Assert ($resolvedFiles.Count -eq 0) 'Missing embedded Python package registered'
 $script:officialSources[$pythonBase+'3.13.21/']='<a href="python-3.13.21-embed-amd64.zip"><a href="python-3.13.21-embed-amd64.zip.sigstore">'
 foreach($invalid in @(
  '{"messageSignature":{"messageDigest":{"algorithm":"SHA2_512","digest":"'+$encoded+'"}}}',
  '{"messageSignature":{"messageDigest":{"algorithm":"SHA2_256","digest":"AA=="}}}'
 )){
  $downloadHashes.Clear();$resolvedFiles.Clear()
  $script:officialSources[$pythonSource]=$invalid
  Assert-Rejected {Resolve-PythonDownload} 'Invalid Python digest accepted'
  Assert ($resolvedFiles.Count -eq 0) 'Invalid Python digest registered'
 }
 $script:officialSources['https://nodejs.org/dist/index.json']='[{"version":"v24.12.0","lts":"Fixture","files":["win-x64-zip"]}]'
 $nodeSource='https://nodejs.org/dist/v24.12.0/SHASUMS256.txt'
 $row=('b'*64)+'  node-v24.12.0-win-x64.zip'
 foreach($invalid in @('',('c'*64)+'  other.zip',($row+"`n"+$row))){
  $downloadHashes.Clear();$resolvedFiles.Clear()
  $script:officialSources[$nodeSource]=$invalid
  Assert-Rejected {Resolve-NodeDownload} 'Missing or ambiguous Node checksum accepted'
  Assert ($resolvedFiles.Count -eq 0) 'Invalid Node checksum registered'
 }
 $script:officialSources[$nodeSource]=$row
 $release=Resolve-NodeDownload
 Assert ($release.version -eq 'v24.12.0' -and $downloadHashes[$release.name] -eq ('b'*64)) 'Node published checksum not registered'
 function Get-OfficialBytes([string]$url){
  Assert ($url -eq 'https://bootstrap.pypa.io/get-pip.py') 'Unexpected script source'
  return ,[Text.Encoding]::UTF8.GetBytes('new official script fixture, never executed')
 }
 $downloadHashes['get-pip.py']='a'*64
 $target=Join-Path $local 'get-pip.py'
 Save-LatestGetPip $target
 Assert ([IO.File]::ReadAllText($target) -eq 'new official script fixture, never executed') 'Latest script not saved'
 Assert ($downloadHashes['get-pip.py'] -ne ('a'*64)) 'Historical get-pip pin blocked fresh content'
 Assert-DownloadHash $target
 [IO.File]::WriteAllText($target,'tampered fixture')
 Assert-Rejected {Assert-DownloadHash $target} 'Saved script tampering accepted'
 $record=Get-Content (Join-Path $local 'runtime-downloads.json') -Raw | ConvertFrom-Json
 Assert (@($record | Where-Object name -eq 'get-pip.py').Count -eq 1) 'Resolved script provenance not persisted'
}

# Exercise source restrictions without any HTTP request leaving the process.
& {
 $script:transportCalls=0
 function Invoke-WebRequest {
  param([switch]$UseBasicParsing,$Uri,$MaximumRedirection,$TimeoutSec)
  $script:transportCalls++
  Assert ($MaximumRedirection -eq 0) 'Official metadata followed redirects'
  return [pscustomobject]@{StatusCode=200;RawContentStream=(New-Object IO.MemoryStream(,[byte[]]@(1,2,3)))}
 }
 foreach($url in @('http://nodejs.org/', 'https://nodejs.org.evil.invalid/', 'https://user:pass@nodejs.org/', 'https://www.python.org:8443/')){
  Assert-Rejected {Get-OfficialBytes $url} 'Unofficial source accepted'
 }
 Assert ($transportCalls -eq 0) 'Invalid source reached transport'
 $bytes=Get-OfficialBytes 'https://nodejs.org/dist/index.json'
 Assert ($bytes.Length -eq 3 -and $transportCalls -eq 1) 'Official response not returned'
}
# A fake executable models interrupted installs while capturing every invocation.
$project=Split-Path -Parent $PSScriptRoot
$downloads=Join-Path $project '.runtime/downloads'
$python='Invoke-MockPython'
function Say {param($text,$color)}
function Test-Python313 {return $true}
function Save-LatestGetPip {
 param($target)
 Assert ($target -eq (Join-Path $downloads 'get-pip.py')) 'Unexpected download destination'
 $script:downloadCount++
}
function Invoke-MockPython {
 $script:calls.Add(($args -join '|'))
 $global:LASTEXITCODE=0
 if($args[0] -eq '-c' -and $args[1] -eq 'import yaml'){
  if(!$script:yamlReady){$global:LASTEXITCODE=1};return
 }
 if($args[0] -eq '-m' -and $args[1] -eq 'pip' -and $args[2] -eq '--version'){
  if($script:pipReady){Write-Output 'pip fixture'}else{$global:LASTEXITCODE=1};return
 }
 if($args[0] -eq (Join-Path $downloads 'get-pip.py')){
  Assert ($args -contains '--no-user') 'get-pip must disallow user install'
  if($script:pipInstallFails){$global:LASTEXITCODE=1}else{$script:pipReady=$true};return
 }
 if($args[0] -eq '-m' -and $args[1] -eq 'pip' -and $args[2] -eq 'install'){
  Assert $script:pipReady 'PyYAML installation attempted without pip'
  Assert ($args -contains '--no-user') 'PyYAML must disallow user install'
  $script:yamlReady=$true;return
 }
 throw ('Unexpected interpreter invocation: '+($args -join '|'))
}
function Reset-Fixture {
 $script:pipReady=$false;$script:yamlReady=$false;$script:pipInstallFails=$false
 $script:downloadCount=0;$script:NoInstall=$false
 $script:calls=New-Object 'System.Collections.Generic.List[string]'
}
Reset-Fixture
Ensure-Python
Ensure-Yaml
Assert ($pipReady -and $yamlReady -and $downloadCount -eq 1) 'Existing Python missing pip did not recover'
Assert (@($calls | Where-Object {$_ -like '-m|pip|--version*'}).Count -ge 2) 'pip installation was not verified'
Reset-Fixture
$pipReady=$true
Ensure-Yaml
Assert ($yamlReady -and $downloadCount -eq 0) 'Existing pip was downloaded again'
Reset-Fixture
$NoInstall=$true
$rejected=$false
try{Ensure-Pip}catch{$rejected=$true}
Assert ($rejected -and $downloadCount -eq 0) 'NoInstall allowed pip download'
$rejected=$false
try{Ensure-Yaml}catch{$rejected=$true}
Assert ($rejected -and $downloadCount -eq 0) 'NoInstall allowed dependency installation'
Reset-Fixture
$pipInstallFails=$true
$rejected=$false
try{Ensure-Yaml}catch{$rejected=$true}
Assert ($rejected -and !$yamlReady) 'Failed pip installation did not stop PyYAML'
Assert (@($calls | Where-Object {$_ -like '-m|pip|install*'}).Count -eq 0) 'PyYAML attempted after pip failure'
Reset-Fixture
$yamlReady=$true
Ensure-Yaml
Assert ($downloadCount -eq 0 -and $calls.Count -eq 1) 'Working PyYAML unnecessarily required pip'

# Only top-level commands determine startup order; function bodies are excluded.
$startup=@($ast.EndBlock.Statements | Where-Object {$_ -is [Management.Automation.Language.PipelineAst]} | ForEach-Object {
 foreach($element in $_.PipelineElements){if($element -is [Management.Automation.Language.CommandAst]){$element.GetCommandName()}}
})
$pthIndex=[Array]::IndexOf($startup,'Ensure-Pth')
$siteIndex=[Array]::IndexOf($startup,'Ensure-Sitecustomize')
$yamlIndex=[Array]::IndexOf($startup,'Ensure-Yaml')
Assert ($pthIndex -ge 0 -and $siteIndex -gt $pthIndex -and $yamlIndex -gt $siteIndex) 'Python module paths must be configured before dependency repair'
Write-Output 'Bootstrap offline checks passed: official release selection, source-only Python skip, checksum validation, latest get-pip, source restrictions, interrupted install recovery, NoInstall, startup order'
