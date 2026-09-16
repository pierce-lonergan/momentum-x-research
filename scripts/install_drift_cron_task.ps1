# Install the drift_cron Windows Task Scheduler task.
#
# Schedule: weekdays at 08:00 local time (~ET; assumes machine TZ is ET).
# Runs as the current user (no UAC, paper-trading-safe).
# Action: powershell.exe -ExecutionPolicy Bypass -File scripts\drift_cron.ps1
#
# Run from project root:
#   .\scripts\install_drift_cron_task.ps1
#
# To remove: Unregister-ScheduledTask -TaskName "MX Drift Cron" -Confirm:$false

[CmdletBinding()]
param(
    [string]$TaskName = "MX Drift Cron",
    [string]$RunTime = "08:00"
)

$ErrorActionPreference = "Stop"

# Resolve repo path
$repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ps1 = Join-Path $repo "scripts\drift_cron.ps1"

if (-not (Test-Path $ps1)) {
    Write-Error "Cannot find $ps1"
    exit 1
}

Write-Host "Installing scheduled task '$TaskName'"
Write-Host "  Working dir:   $repo"
Write-Host "  Action script: $ps1"
Write-Host "  Trigger:       Weekdays at $RunTime local time"

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$ps1`"" `
    -WorkingDirectory $repo

$trigger = New-ScheduledTaskTrigger `
    -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At $RunTime

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

# Run as current user, no elevation needed
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive

# Unregister if exists
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "  Removing existing task..."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask -TaskName $TaskName `
    -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "MOMENTUM-X daily drift detector (PSI / KS / Page-Hinkley) with Discord alerts" | Out-Null

Write-Host ""
Write-Host "Installed. Verify with:"
Write-Host "  Get-ScheduledTask -TaskName '$TaskName'"
Write-Host ""
Write-Host "Manual test run:"
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host ""
Write-Host "Remove:"
Write-Host "  Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
