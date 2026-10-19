<#
.SYNOPSIS
    Registers or unregisters the Groww Weekly Review Pulse scheduled task in Windows Task Scheduler.
.PARAMETER Action
    Register (default) or Unregister.
#>

param(
    [ValidateSet("Register", "Unregister")]
    [string]$Action = "Register",
    [string]$TaskName = "GrowwWeeklyReviewPulse"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$XmlPath = Join-Path $ScriptDir "scheduled_task.xml"

if ($Action -eq "Register") {
    Write-Host "Registering scheduled task '$TaskName'..."
    if (-not (Test-Path $XmlPath)) {
        Write-Error "Task definition XML not found at $XmlPath"
        exit 1
    }
    # Replace path in XML if repo moved
    $ProjectRoot = Split-Path -Parent $ScriptDir
    $RunScript = Join-Path $ProjectRoot "scripts\run_weekly.ps1"

    $xmlContent = Get-Content $XmlPath -Raw
    $xmlContent = $xmlContent -replace 'WorkingDirectory>.*?</WorkingDirectory>', "WorkingDirectory>$ProjectRoot</WorkingDirectory>"
    $xmlContent = $xmlContent -replace 'Arguments>-NoProfile -ExecutionPolicy Bypass -File ".*?"</Arguments>', "Arguments>-NoProfile -ExecutionPolicy Bypass -File `"$RunScript`"</Arguments>"

    $tempXml = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tempXml, $xmlContent, [System.Text.Encoding]::Unicode)

    try {
        schtasks.exe /Create /TN $TaskName /XML $tempXml /F
        Write-Host "Task '$TaskName' registered successfully to run every Monday at 09:00 IST." -ForegroundColor Green
    } finally {
        if (Test-Path $tempXml) { Remove-Item $tempXml -Force }
    }
} elseif ($Action -eq "Unregister") {
    Write-Host "Unregistering scheduled task '$TaskName'..."
    schtasks.exe /Delete /TN $TaskName /F
    Write-Host "Task '$TaskName' removed successfully." -ForegroundColor Green
}
