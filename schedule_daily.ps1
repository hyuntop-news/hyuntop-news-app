param(
    [string]$Time = "07:00",
    [string]$TaskName = "Morning News Mailer"
)

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Runner = Join-Path $ProjectDir "run_morning_news.bat"

if (-not (Test-Path $Runner)) {
    throw "run_morning_news.bat 파일을 찾지 못했습니다."
}

$Action = New-ScheduledTaskAction -Execute $Runner -WorkingDirectory $ProjectDir
$Trigger = New-ScheduledTaskTrigger -Daily -At $Time
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "Send top morning news to Gmail" `
    -Force

Write-Host "등록 완료: 매일 $Time 에 '$TaskName' 작업이 실행됩니다."

