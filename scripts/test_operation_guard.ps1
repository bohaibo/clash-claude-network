# Offline: only fixture files are read; no system recovery is invoked.
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'operation_guard.ps1')
$fixture=Join-Path (Split-Path -Parent $PSScriptRoot) ('.local/operation-guard-test-'+[Guid]::NewGuid().ToString('N'))
$folder=Join-Path $fixture ('gui/settings-transactions/'+('a'*32))
New-Item -ItemType Directory -Path $folder -Force | Out-Null
$journal=Join-Path $folder 'transaction.json'
foreach($data in @('{"phase":"applying"}','{"phase":"recovery_failed"}','{}','[]','{broken')){
 Set-Content -LiteralPath $journal -Value $data -Encoding UTF8
 $blocked=$false
 try{Assert-NoPendingSettings -LocalDir $fixture}catch{$blocked=$true}
 if(!$blocked){throw 'Pending or malformed journal was accepted'}
}
foreach($phase in @('committed','rolled_back')){
 @{phase=$phase}|ConvertTo-Json|Set-Content -LiteralPath $journal -Encoding UTF8
 Assert-NoPendingSettings -LocalDir $fixture
}
Remove-Item -LiteralPath $journal
Assert-NoPendingSettings -LocalDir $fixture
Write-Output 'Offline settings guard passed: pending, malformed, terminal, legacy'
