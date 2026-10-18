param([ValidateSet('install','remove')][string]$Action,[string]$Python)
$project=Split-Path -Parent $PSScriptRoot
$digest=[Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($project))
$suffix=([BitConverter]::ToString($digest)).Replace('-','').Substring(0,12)
$name='ClaudeNetwork-Update-'+$suffix
$existing=Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if($Action -eq 'remove'){if($existing){Unregister-ScheduledTask -TaskName $name -Confirm:$false};return}
$argument='"'+(Join-Path $PSScriptRoot 'update.py')+'"'
if($existing){if(@($existing.Actions | Where-Object {$_.Arguments -ne $argument}).Count){throw 'Existing updater task differs; review required'};return}
$taskAction=New-ScheduledTaskAction -Execute $Python -Argument $argument -WorkingDirectory $project
$trigger=New-ScheduledTaskTrigger -Daily -At '13:00'
$principal=New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 4) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $name -Action $taskAction -Trigger $trigger -Principal $principal -Settings $settings | Out-Null
