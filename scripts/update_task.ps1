param([ValidateSet('install','remove','status','snapshot','rollback')][string]$Action,[string]$Python,[string]$At='13:00',[ValidateRange(1,365)][int]$DaysInterval=1,[string]$StateFile)
$ErrorActionPreference='Stop'
$project=Split-Path -Parent $PSScriptRoot
$digest=[Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($project))
$suffix=([BitConverter]::ToString($digest)).Replace('-','').Substring(0,12)
$name='ClaudeNetwork-Update-'+$suffix
$expectedAction='"'+(Join-Path $PSScriptRoot 'update.py')+'"'
$existing=Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue

function Grant-UpdateTaskUserAccess {
 $task=Get-ScheduledTask -TaskName $name -ErrorAction Stop
 $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
 if(@($task.Actions).Count -ne 1 -or $task.Actions[0].Arguments -ne $expectedAction -or $task.Actions[0].Execute -ne $Python -or [int]$task.Principal.RunLevel -ne 0){throw 'Unexpected update task definition; permissions unchanged.'}
 $taskSid=([Security.Principal.NTAccount]::new($task.Principal.UserId)).Translate([Security.Principal.SecurityIdentifier])
 if($taskSid.Value -ne $identity.User.Value){throw 'Update task belongs to another user; permissions unchanged.'}
 $scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect()
 $registered=$scheduler.GetFolder('\').GetTask($name)
 $descriptor=[Security.AccessControl.RawSecurityDescriptor]::new($registered.GetSecurityDescriptor(4))
 $mask=0x1f01ff
 foreach($ace in $descriptor.DiscretionaryAcl){
  if($ace -is [Security.AccessControl.CommonAce] -and $ace.AceQualifier -eq [Security.AccessControl.AceQualifier]::AccessAllowed -and $ace.SecurityIdentifier -eq $identity.User -and ($ace.AccessMask -band $mask) -eq $mask){return}
 }
 $ace=[Security.AccessControl.CommonAce]::new([Security.AccessControl.AceFlags]::None,[Security.AccessControl.AceQualifier]::AccessAllowed,$mask,$identity.User,$false,$null)
 $descriptor.DiscretionaryAcl.InsertAce(0,$ace)
 $registered.SetSecurityDescriptor($descriptor.GetSddlForm([Security.AccessControl.AccessControlSections]::Access),0)
}

function Get-TriggerTime($task){
 $trigger=@($task.Triggers | Select-Object -First 1)
 if(!$trigger.Count -or !$trigger[0].StartBoundary){return ''}
 try{return ([datetime]$trigger[0].StartBoundary).ToString('HH:mm')}catch{return ''}
}

function Get-TriggerInterval($task){
 $trigger=@($task.Triggers | Select-Object -First 1)
 if(!$trigger.Count){return 1}
 try{$value=[int]$trigger[0].DaysInterval;if($value -ge 1){return $value}}catch{}
 return 1
}

if($Action -eq 'status'){
 $payload=[ordered]@{installed=[bool]$existing}
 if($existing){
  $payload.state=[string]$existing.State
  $payload.time=Get-TriggerTime $existing
  $payload.days=Get-TriggerInterval $existing
  $payload.action_matches=(@($existing.Actions | Where-Object {$_.Arguments -ne $expectedAction}).Count -eq 0)
  try{$payload.next_run=[string](Get-ScheduledTaskInfo -TaskName $name -ErrorAction Stop).NextRunTime}catch{$payload.next_run=''}
  try{$payload.last_run=[string](Get-ScheduledTaskInfo -TaskName $name -ErrorAction Stop).LastRunTime}catch{$payload.last_run=''}
  try{$payload.last_result=(Get-ScheduledTaskInfo -TaskName $name -ErrorAction Stop).LastTaskResult}catch{$payload.last_result=$null}
 }
 $payload | ConvertTo-Json -Compress
 return
}

function Assert-OwnedUpdateTask($task){
 if(!$task){return}
 if(@($task.Actions).Count -ne 1 -or $task.Actions[0].Arguments -ne $expectedAction -or $task.Actions[0].Execute -ne $Python -or [int]$task.Principal.RunLevel -ne 0){throw 'Unexpected update task definition; task unchanged.'}
 $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
 $sid=([Security.Principal.NTAccount]::new($task.Principal.UserId)).Translate([Security.Principal.SecurityIdentifier])
 if($sid.Value -ne $identity.User.Value){throw 'Update task belongs to another user; task unchanged.'}
}

function Get-UpdateTaskRegistration {
 $scheduler=New-Object -ComObject 'Schedule.Service';$scheduler.Connect()
 return $scheduler.GetFolder('\').GetTask($name)
}

if($Action -in @('snapshot','rollback')){
 $target=[IO.Path]::GetFullPath($StateFile)
 $allowed=[IO.Path]::GetFullPath((Join-Path $project '.local')).TrimEnd('\')+'\'
 if(!$target.StartsWith($allowed,[StringComparison]::OrdinalIgnoreCase)){throw 'Task recovery records must stay under project .local.'}
 Assert-OwnedUpdateTask $existing
 if($Action -eq 'snapshot'){
  if(Test-Path -LiteralPath $target){throw 'Task recovery point already exists; refusing to overwrite.'}
  $record=[ordered]@{version=1;name=$name;installed=[bool]$existing;xml='';sddl=''}
  if($existing){
   $record.xml=Export-ScheduledTask -TaskName $name -ErrorAction Stop
   $record.sddl=(Get-UpdateTaskRegistration).GetSecurityDescriptor(4)
  }
  $record | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $target -Encoding UTF8
  return
 }
 $record=Get-Content -LiteralPath $target -Raw -Encoding UTF8 | ConvertFrom-Json
 if($record.version -ne 1 -or $record.name -ne $name -or $record.installed -isnot [bool]){throw 'Invalid task recovery record.'}
 if($record.installed){
  if(!$record.xml -or !$record.sddl){throw 'Incomplete task recovery record.'}
  Register-ScheduledTask -TaskName $name -Xml $record.xml -Force -ErrorAction Stop | Out-Null
  (Get-UpdateTaskRegistration).SetSecurityDescriptor($record.sddl,0)
  if((Get-UpdateTaskRegistration).GetSecurityDescriptor(4) -ne $record.sddl){throw 'Task access recovery verification failed.'}
  $actual=Export-ScheduledTask -TaskName $name -ErrorAction Stop
  $before=[xml]$record.xml;$after=[xml]$actual
  if($before.Task.Triggers.OuterXml -ne $after.Task.Triggers.OuterXml -or $before.Task.Settings.Enabled -ne $after.Task.Settings.Enabled){throw 'Task recovery verification failed.'}
 } elseif($existing){Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction Stop}
 return
}

if($Action -eq 'remove'){Assert-OwnedUpdateTask $existing;if($existing){Unregister-ScheduledTask -TaskName $name -Confirm:$false};return}

if($At -notmatch '^(?:[01]\d|2[0-3]):[0-5]\d$'){throw '定时更新时间须为 HH:MM（00:00–23:59）。'}
if($DaysInterval -lt 1 -or $DaysInterval -gt 365){throw '更新间隔必须是 1–365 天。'}
$trigger=New-ScheduledTaskTrigger -Daily -At $At -DaysInterval $DaysInterval
if($existing){
 if(@($existing.Actions | Where-Object {$_.Arguments -ne $expectedAction}).Count){throw '已存在一个参数不同的更新任务，请先人工确认。'}
 $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
 if(([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){Grant-UpdateTaskUserAccess}
 if((Get-TriggerTime $existing) -ne $At -or (Get-TriggerInterval $existing) -ne $DaysInterval){Set-ScheduledTask -TaskName $name -Trigger $trigger | Out-Null}
 return
}
$taskAction=New-ScheduledTaskAction -Execute $Python -Argument $expectedAction -WorkingDirectory $project
$principal=New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 4) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $name -Action $taskAction -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
Grant-UpdateTaskUserAccess
