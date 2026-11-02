# Offline: extract hash validation only; never execute bootstrap or downloads.
$ErrorActionPreference='Stop'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Join-Path $PSScriptRoot 'bootstrap.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'Bootstrap parse failed'}
foreach($name in @('Fail','Assert-DownloadHash')){
 $fn=$ast.FindAll({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst]},$true) | Where-Object Name -eq $name
 Invoke-Expression $fn.Extent.Text
}
$downloadHashes=@{'fixture.zip'='A'*64}
function Get-FileHash {param($LiteralPath,$Algorithm) [pscustomobject]@{Hash=$script:digest}}
$script:digest='A'*64
Assert-DownloadHash 'fixture.zip'
$script:digest='B'*64
$rejected=$false
try{Assert-DownloadHash 'fixture.zip'}catch{$rejected=$true}
if(!$rejected){throw 'Corrupted archive accepted'}
$rejected=$false
try{Assert-DownloadHash 'unknown.zip'}catch{$rejected=$true}
if(!$rejected){throw 'Unpinned input accepted'}
Write-Output 'Bootstrap offline checks passed: valid, corrupt, unpinned'
