<#
.SYNOPSIS
    Install the D217 Watchdog Monitor as a Windows Scheduled Task.

.DESCRIPTION
    Creates "MomentumX-Watchdog" task that runs every 2 minutes
    from 4:00 AM to 5:00 PM ET (covers full trading day + pre-market).

    Run this script once (elevated) to install. The watchdog will then
    run independently of the trading system.

.PARAMETER WebhookUrl
    Optional Discord/Slack webhook URL for alerts.
#>

param(
    [string]$WebhookUrl = ""
)

$ErrorActionPreference = "Stop"

$TaskName = "MomentumX-Watchdog"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ScriptPath = Join-Path $ProjectRoot "scripts\watchdog_monitor.ps1"

# Build the action
$argString = "-NoProfile -ExecutionPolicy Bypass -File `"$ScriptPath`" -ProjectRoot `"$ProjectRoot`""
if ($WebhookUrl) {
    $argString += " -WebhookUrl `"$WebhookUrl`""
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument $argString `
    -WorkingDirectory $ProjectRoot

# D217 FIX: Use -Daily trigger with CIM repetition pattern.
# -Once with a past StartBoundary never fires (April 10 bug: watchdog never ran).
# -Daily fires every day at 04:00 AM, with 2-minute repetition for 13 hours.
$trigger = New-ScheduledTaskTrigger -Daily -At "04:00" -DaysInterval 1
$repetition = New-CimInstance -CimClass (
    Get-CimClass -Namespace "Root/Microsoft/Windows/TaskScheduler" -ClassName "MSFT_TaskRepetitionPattern"
) -Property @{ Interval = "PT2M"; Duration = "PT13H"; StopAtDurationEnd = $true } -ClientOnly
$trigger.Repetition = $repetition

# Settings
$taskSettings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

# Skip weekends
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType S4U -RunLevel Limited

# Check if task exists
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "Updating existing task '$TaskName'..."
    Set-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $taskSettings | Out-Null
} else {
    Write-Host "Creating new task '$TaskName'..."
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $taskSettings `
        -Principal $principal `
        -Description "D217: Monitors Momentum-X heartbeat file. Kills hung processes after 2 min staleness during market hours. Circuit breaker after 3 restarts/hour." | Out-Null
}

Write-Host ""
Write-Host "Watchdog installed successfully!"
Write-Host "  Task:      $TaskName"
Write-Host "  Script:    $ScriptPath"
Write-Host "  Interval:  Every 2 minutes"
Write-Host "  Window:    4:00 AM - 5:00 PM ET"
Write-Host "  Webhook:   $(if ($WebhookUrl) { 'Configured' } else { 'None (set -WebhookUrl to enable)' })"
Write-Host ""
Write-Host "To test: schtasks /Run /TN '$TaskName'"
Write-Host "To remove: Unregister-ScheduledTask -TaskName '$TaskName'"
