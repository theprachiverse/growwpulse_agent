# Weekly runner script for the Groww Review Pulse Agent (Phase 7).
# Runs unattended every Monday at 09:00 IST (03:30 UTC).

param(
    [string]$Week = 'auto',
    [string]$FromStep = '',
    [switch]$Send = $false,
    [switch]$DryRun = $false
)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir

Set-Location $ProjectRoot

# Ensure logs directory exists
$LogDir = Join-Path $ProjectRoot 'data\logs'
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

$Timestamp = Get-Date -Format 'yyyyMMddTHHmmssZ'
$LogFile = Join-Path $LogDir ('weekly_run_' + $Timestamp + '.log')

Write-Host '=================================================='
Write-Host ' Groww Weekly Review Pulse - Scheduled Run'
Write-Host (' Time: ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss K'))
Write-Host (' Root: ' + $ProjectRoot)
Write-Host (' Log:  ' + $LogFile)
Write-Host '=================================================='

# Check Python virtual environment
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $VenvPython)) {
    Write-Error ('Virtual environment not found at ' + $VenvPython)
    exit 1
}

# Load .env into process environment if present
$EnvFile = Join-Path $ProjectRoot '.env'
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith('#') -and ($line -match '^([^=]+)=(.*)$')) {
            $name = $matches[1].Trim()
            $val = $matches[2].Trim()
            [Environment]::SetEnvironmentVariable($name, $val, 'Process')
        }
    }
}

# Build CLI arguments
$ArgsList = @('run', '--week', $Week)
if ($FromStep) {
    $ArgsList += @('--from-step', $FromStep)
}
if ($DryRun) {
    $ArgsList += '--dry-run'
}
if ($Send) {
    $ArgsList += '--send'
    $ArgsList += '--yes'
}

$PulseExe = Join-Path $ProjectRoot '.venv\Scripts\pulse.exe'
Write-Host ('Executing: ' + $PulseExe + ' ' + ($ArgsList -join ' '))

# Run pulse with logging
& $PulseExe @ArgsList 2>&1 | Tee-Object -FilePath $LogFile
$ExitCode = $LASTEXITCODE

if ($ExitCode -eq 0) {
    Write-Host ''
    Write-Host 'Weekly pulse run completed successfully.' -ForegroundColor Green
} else {
    Write-Host ''
    Write-Host ('Weekly pulse run failed with exit code ' + $ExitCode) -ForegroundColor Red
}

exit $ExitCode
