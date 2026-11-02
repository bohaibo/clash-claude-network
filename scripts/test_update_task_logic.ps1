# Offline: all scheduler/COM operations are mocked, and recovery files stay in .local.
$ErrorActionPreference='Stop'
$projectRoot=Split-Path -Parent $PSScriptRoot
$fixture=Join-Path $projectRoot ('.local/task-transaction-test-'+[Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $fixture | Out-Null
$taskScript=Join-Path $PSScriptRoot 'update_task.ps1'
$fixturePython=Join-Path $projectRoot '.runtime/python313/python.exe'
$fixtureArguments='"'+(Join-Path $PSScriptRoot 'update.py')+'"'
$script:fixtureTask=[pscustomobject]@{
 Actions=@([pscustomobject]@{Arguments=$fixtureArguments;Execute=$fixturePython})
 Principal=[pscustomobject]@{RunLevel=0;UserId=[Security.Principal.WindowsIdentity]::GetCurrent().Name}
}
$script:fixtureXml='<Task><Triggers><CalendarTrigger><DaysInterval>7</DaysInterval></CalendarTrigger></Triggers><Settings><Enabled>false</Enabled></Settings></Task>'
$script:fixtureSddl='D:(A;;FA;;;SY)'
$script:registered=0;$script:removed=0
function Get-ScheduledTask {param($TaskName,$ErrorAction) return $script:fixtureTask}
function Export-ScheduledTask {param($TaskName,$ErrorAction) return $script:fixtureXml}
function Register-ScheduledTask {param($TaskName,$Xml,[switch]$Force,$ErrorAction)
 $script:registered++;$script:fixtureXml=$Xml
}
function Unregister-ScheduledTask {param($TaskName,$Confirm,$ErrorAction)
 $script:removed++;$script:fixtureTask=$null
}
$script:registration=New-Object psobject
$script:registration | Add-Member ScriptMethod GetSecurityDescriptor {param($flags) return $script:fixtureSddl}
$script:registration | Add-Member ScriptMethod SetSecurityDescriptor {param($value,$flags) $script:fixtureSddl=$value}
$script:scheduler=New-Object psobject
$script:scheduler | Add-Member ScriptMethod Connect {}
$script:scheduler | Add-Member ScriptMethod GetFolder {param($path) return $script:scheduler}
$script:scheduler | Add-Member ScriptMethod GetTask {param($taskName) return $script:registration}
function New-Object {param($ComObject) if($ComObject -ne 'Schedule.Service'){throw 'Unexpected COM access'};return $script:scheduler}
$saved=Join-Path $fixture 'original-task.json'
$originalXml=$script:fixtureXml;$originalSddl=$script:fixtureSddl
. $taskScript -Action snapshot -Python $fixturePython -StateFile $saved
if(!(Test-Path -LiteralPath $saved)){throw 'Missing task recovery file'}
$script:fixtureXml='<Task><Triggers/><Settings><Enabled>true</Enabled></Settings></Task>'
$script:fixtureSddl='changed'
. $taskScript -Action rollback -Python $fixturePython -StateFile $saved
if($script:fixtureXml -ne $originalXml -or $script:fixtureSddl -ne $originalSddl -or $script:registered -ne 1){throw 'Original task XML/access not restored'}
$script:fixtureTask=$null
$absent=Join-Path $fixture 'absent-task.json'
. $taskScript -Action snapshot -Python $fixturePython -StateFile $absent
$script:fixtureTask=[pscustomobject]@{
 Actions=@([pscustomobject]@{Arguments=$fixtureArguments;Execute=$fixturePython})
 Principal=[pscustomobject]@{RunLevel=0;UserId=[Security.Principal.WindowsIdentity]::GetCurrent().Name}
}
. $taskScript -Action rollback -Python $fixturePython -StateFile $absent
if($script:fixtureTask -or $script:removed -ne 1){throw 'Originally absent task not restored'}
$denied=$false
try{. $taskScript -Action snapshot -Python $fixturePython -StateFile (Join-Path $projectRoot 'outside-task-test.json')}catch{$denied=$true}
if(!$denied){throw 'Recovery outside .local accepted'}
Write-Output 'Task transaction offline checks passed: XML, access, disabled state, absence, path boundary'
