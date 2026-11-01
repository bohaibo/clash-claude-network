param([ValidateSet('install','remove','status')][string]$Action,[string]$Python,[string]$At='13:00',[ValidateRange(1,365)][int]$DaysInterval=1)
$ErrorActionPreference='Stop'
$project=Split-Path -Parent $PSScriptRoot
$digest=[Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($project))
$suffix=([BitConverter]::ToString($digest)).Replace('-','').Substring(0,12)
$name='ClaudeNetwork-Update-'+$suffix
$expectedAction='"'+(Join-Path $PSScriptRoot 'update.py')+'"'
$existing=Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue

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

if($Action -eq 'remove'){if($existing){Unregister-ScheduledTask -TaskName $name -Confirm:$false};return}

if($At -notmatch '^(?:[01]\d|2[0-3]):[0-5]\d$'){throw '定时更新时间须为 HH:MM（00:00–23:59）。'}
if($DaysInterval -lt 1 -or $DaysInterval -gt 365){throw '更新间隔必须是 1–365 天。'}
$trigger=New-ScheduledTaskTrigger -Daily -At $At -DaysInterval $DaysInterval
if($existing){
 if(@($existing.Actions | Where-Object {$_.Arguments -ne $expectedAction}).Count){throw '已存在一个参数不同的更新任务，请先人工确认。'}
 if((Get-TriggerTime $existing) -ne $At -or (Get-TriggerInterval $existing) -ne $DaysInterval){Set-ScheduledTask -TaskName $name -Trigger $trigger | Out-Null}
 return
}
$taskAction=New-ScheduledTaskAction -Execute $Python -Argument $expectedAction -WorkingDirectory $project
$principal=New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 4) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $name -Action $taskAction -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
