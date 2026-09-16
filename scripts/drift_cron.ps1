# PowerShell wrapper for daily drift cron — invoked by Windows Task Scheduler.
#
# Activates the project venv (if present), loads .env, runs scripts/drift_cron.py,
# logs output to logs/drift_cron_<YYYYMMDD>.log, and sets a non-zero exit code
# if drift was detected (Task Scheduler logs failures for visibility).
#
# Designed for: Trigger=daily at 08:00 ET, weekdays only.
# Action: powershell.exe -ExecutionPolicy Bypass -File scripts\drift_cron.ps1
# Working dir: %REPO_ROOT%
#
# To install (run as user, NOT elevated):
#   .\scripts\install_drift_cron_task.ps1

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $repo

# Try venv activation if exists
$venvActivate = Join-Path $repo ".venv\Scripts\Activate.ps1"
if (Test-Path $venvActivate) {
    . $venvActivate
    Write-Host "[drift_cron.ps1] Activated venv: $venvActivate"
}

# Run with python (uses system python if no venv)
$logDir = Join-Path $repo "logs"
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir | Out-Null }
$today = Get-Date -Format "yyyyMMdd"
$logFile = Join-Path $logDir "drift_cron_${today}.log"

Write-Host "[drift_cron.ps1] $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') Starting drift detector"
$python = (Get-Command python).Source
# Avoid the merge-and-tee pipe-deadlock pattern (PS 5.1 wraps stderr in
# NativeCommandError records when merging streams into a pipeline, which can
# deadlock). Redirect stderr directly to the log file; stdout still tees to
# console + log.
& $python "scripts\drift_cron.py" 2>> $logFile | Tee-Object -FilePath $logFile -Append
$rc = $LASTEXITCODE

Write-Host "[drift_cron.ps1] $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') Exit=$rc (0=clean, 1=drift detected)"
exit $rc
