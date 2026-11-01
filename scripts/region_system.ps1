# Internal system helpers. Dot-sourcing this file performs no system writes.
function Get-RegionRegistryValue([string]$Path,[string]$Name) {
  $key=Get-Item -LiteralPath $Path -ErrorAction Stop
  $exists=$Name -in @($key.GetValueNames())
  return [pscustomobject]@{exists=$exists;kind=if($exists){$key.GetValueKind($Name).ToString()}else{$null};value=if($exists){$key.GetValue($Name)}else{$null}}
}
function Set-RegionRegistryValue([string]$Path,[string]$Name,$Entry) {
  if($Entry.exists){
    New-ItemProperty -LiteralPath $Path -Name $Name -PropertyType $Entry.kind -Value $Entry.value -Force -ErrorAction Stop | Out-Null
  }else{
    $current=Get-RegionRegistryValue $Path $Name
    if($current.exists){Remove-ItemProperty -LiteralPath $Path -Name $Name -ErrorAction Stop}
  }
  $current=Get-RegionRegistryValue $Path $Name
  if($current.exists -ne $Entry.exists -or ($Entry.exists -and ($current.kind -ne $Entry.kind -or $current.value -ne $Entry.value))){throw "区域/时间注册表设置验证失败：$Name"}
}
function Get-RegionServiceState([string]$Name) {
  $service=Get-Service -Name $Name -ErrorAction Stop
  $key='HKLM:\SYSTEM\CurrentControlSet\Services\'+$Name
  $start=Get-RegionRegistryValue $key 'Start'
  if(!$start.exists -or $start.kind -ne 'DWord' -or $start.value -notin @(2,3,4)){throw "无法记录服务启动方式：$Name"}
  if($service.Status.ToString() -notin @('Running','Stopped')){throw "服务正在切换状态，请稍后重试：$Name"}
  return [pscustomobject]@{start=[int]$start.value;running=($service.Status.ToString() -eq 'Running');delayed=(Get-RegionRegistryValue $key 'DelayedAutoStart')}
}
function Assert-RegionTimeState($State) {
  if($State.version -ne 1 -or $State.homeGeoId -isnot [int] -or $State.homeGeoId -lt 0 -or !$State.timezone -or !$State.culture){throw '区域/时间恢复记录无效。'}
  $null=[Globalization.CultureInfo]::GetCultureInfo([string]$State.culture)
  $null=Get-TimeZone -Id ([string]$State.timezone) -ErrorAction Stop
  foreach($name in @('tzautoupdate','W32Time')){
    $entry=$State.services.$name
    if($null -eq $entry -or $entry.start -notin @(2,3,4) -or $entry.running -isnot [bool] -or $entry.delayed.exists -isnot [bool]){throw '时间服务恢复记录无效。'}
    if($entry.delayed.exists -and ($entry.delayed.kind -ne 'DWord' -or $entry.delayed.value -notin @(0,1))){throw '延迟启动恢复记录无效。'}
  }
  if($State.syncType.exists -isnot [bool] -or ($State.syncType.exists -and ($State.syncType.kind -ne 'String' -or $State.syncType.value -isnot [string]))){throw '自动校时恢复记录无效。'}
}
function Save-RegionTimeState([string]$Directory) {
  $record=Join-Path $Directory 'region-time.json'
  if(Test-Path -LiteralPath $record){
    Assert-RegionTimeState (Get-Content -LiteralPath $record -Raw -Encoding UTF8 | ConvertFrom-Json)
    return
  }
  $state=[ordered]@{version=1;homeGeoId=[int](Get-WinHomeLocation).GeoId;timezone=(Get-TimeZone).Id;culture=(Get-Culture).Name;
    services=@{tzautoupdate=(Get-RegionServiceState 'tzautoupdate');W32Time=(Get-RegionServiceState 'W32Time')};
    syncType=(Get-RegionRegistryValue 'HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\Parameters' 'Type')}
  # Upgrades add only the newly managed fields. Existing timezone/culture baseline wins.
  $systemFile=Join-Path $Directory 'system.json'
  if(Test-Path -LiteralPath $systemFile){
    $original=Get-Content -LiteralPath $systemFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $state.timezone=$original.timezone;$state.culture=$original.culture
  }
  Assert-RegionTimeState ([pscustomobject]$state)
  $temporary=$record+'.'+[Guid]::NewGuid().ToString('N')+'.tmp'
  $bytes=[Text.UTF8Encoding]::new($false).GetBytes(($state | ConvertTo-Json -Depth 8))
  $stream=[IO.File]::Open($temporary,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write,[IO.FileShare]::None)
  try{$stream.Write($bytes,0,$bytes.Length);$stream.Flush($true)}finally{$stream.Dispose()}
  # File.Move refuses to replace a first recovery point.
  [IO.File]::Move($temporary,$record)
}
function Disable-RegionTimeService([string]$Name) {
  Set-Service -Name $Name -StartupType Disabled -ErrorAction Stop
  Stop-Service -Name $Name -ErrorAction Stop
  $state=Get-RegionServiceState $Name
  if($state.start -ne 4 -or $state.running){throw "未能关闭自动时间服务：$Name"}
}
function Set-SystemRegionTime([string]$Directory,[string]$RegionCode,[string]$CultureName,[string]$TimeZoneId) {
  $region=[Globalization.RegionInfo]::new($RegionCode)
  $null=[Globalization.CultureInfo]::GetCultureInfo($CultureName)
  $null=Get-TimeZone -Id $TimeZoneId -ErrorAction Stop
  Save-RegionTimeState $Directory
  Disable-RegionTimeService 'tzautoupdate'
  Disable-RegionTimeService 'W32Time'
  Set-RegionRegistryValue 'HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\Parameters' 'Type' ([pscustomobject]@{exists=$true;kind='String';value='NoSync'})
  Set-WinHomeLocation -GeoId $region.GeoId -ErrorAction Stop
  Set-Culture -CultureInfo $CultureName -ErrorAction Stop
  Set-TimeZone -Id $TimeZoneId -ErrorAction Stop
  # Read persisted culture because Get-Culture can be cached in this PowerShell session.
  $locale=(Get-ItemProperty -LiteralPath 'HKCU:\Control Panel\International' -Name LocaleName -ErrorAction Stop).LocaleName
  if((Get-WinHomeLocation).GeoId -ne $region.GeoId -or $locale -ine $CultureName -or (Get-TimeZone).Id -ne $TimeZoneId){throw '国家/区域格式/时区设置验证失败。'}
}
function Restore-RegionTimeService([string]$Name,$Entry) {
  $startup=@{2='Automatic';3='Manual';4='Disabled'}[[int]$Entry.start]
  # A disabled service may have been running when the snapshot was captured.
  if($Entry.running){
    Set-Service -Name $Name -StartupType Manual -ErrorAction Stop
    Start-Service -Name $Name -ErrorAction Stop
  }else{Stop-Service -Name $Name -ErrorAction Stop}
  Set-Service -Name $Name -StartupType $startup -ErrorAction Stop
  Set-RegionRegistryValue ('HKLM:\SYSTEM\CurrentControlSet\Services\'+$Name) 'DelayedAutoStart' $Entry.delayed
  $current=Get-RegionServiceState $Name
  if($current.start -ne $Entry.start -or $current.running -ne $Entry.running){throw "时间服务恢复验证失败：$Name"}
}
function Restore-SystemRegionTime([string]$Directory) {
  $record=Join-Path $Directory 'region-time.json'
  if(!(Test-Path -LiteralPath $record)){
    Write-Warning '旧恢复点没有国家/自动时间记录，这些项目保持不变；原有时区和区域格式仍按 system.json 恢复。'
    return
  }
  $state=Get-Content -LiteralPath $record -Raw -Encoding UTF8 | ConvertFrom-Json
  Assert-RegionTimeState $state
  # Attempt every recorded field even if an individual restore step fails.
  $failures=New-Object System.Collections.Generic.List[string]
  foreach($name in @('tzautoupdate','W32Time')){
    try{Disable-RegionTimeService $name}catch{$failures.Add($name+': '+$_.Exception.Message)}
  }
  try{Set-RegionRegistryValue 'HKLM:\SYSTEM\CurrentControlSet\Services\W32Time\Parameters' 'Type' $state.syncType}catch{$failures.Add('校时模式: '+$_.Exception.Message)}
  try{Set-WinHomeLocation -GeoId $state.homeGeoId -ErrorAction Stop}catch{$failures.Add('国家/地区: '+$_.Exception.Message)}
  try{Set-Culture -CultureInfo $state.culture -ErrorAction Stop}catch{$failures.Add('区域格式: '+$_.Exception.Message)}
  try{Set-TimeZone -Id $state.timezone -ErrorAction Stop}catch{$failures.Add('时区: '+$_.Exception.Message)}
  try{
    if((Get-WinHomeLocation).GeoId -ne $state.homeGeoId -or (Get-TimeZone).Id -ne $state.timezone -or
      (Get-ItemProperty -LiteralPath 'HKCU:\Control Panel\International' -Name LocaleName -ErrorAction Stop).LocaleName -ine $state.culture){throw '国家/区域格式/时区恢复验证失败。'}
  }catch{$failures.Add($_.Exception.Message)}
  foreach($name in @('W32Time','tzautoupdate')){
    try{Restore-RegionTimeService $name $state.services.$name}catch{$failures.Add($name+': '+$_.Exception.Message)}
  }
  if($failures.Count){throw ('区域/时间恢复未完成：'+($failures -join '；'))}
}
