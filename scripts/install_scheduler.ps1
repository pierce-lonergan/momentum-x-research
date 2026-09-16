<#
.SYNOPSIS
    Install Momentum-X as a Windows Scheduled Task.
    Run this ONCE (elevated) to set up automatic daily trading.

.DESCRIPTION
    Creates a Windows Task Scheduler task that:
    - Runs daily at 3:30 AM ET (before Phase 0 pre-market research)
    - Skips weekends automatically (handled by daily_paper_trade.ps1)
    - Wakes the computer from sleep if needed
    - Runs whether the user is logged in or not (optional)
    - Restarts on failure (up to 3 retries)
    - Logs to logs/paper_YYYY-MM-DD.log

.PARAMETER Uninstall
    Remove the scheduled task instead of installing.
#>

param(
    [switch]$Uninstall
)

$TaskName = "MomentumX-PaperTrading"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$LauncherScript = Join-Path $ScriptDir "daily_paper_trade.ps1"

# ── Uninstall ──
if ($Uninstall) {
    Write-Host ""
    Write-Host "Removing scheduled task: $TaskName" -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "[OK] Task removed." -ForegroundColor Green
    Write-Host ""
    exit 0
}

# ── Check elevation ──
$currentPrincipal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host ""
    Write-Host "[!] This script requires Administrator privileges." -ForegroundColor Red
    Write-Host "    Right-click PowerShell → 'Run as Administrator' and try again." -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

Write-Host ""
Write-Host "═══════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  Momentum-X Scheduler Installation" -ForegroundColor Cyan
Write-Host "═══════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host ""

# ── Verify launcher exists ──
if (-not (Test-Path $LauncherScript)) {
    Write-Host "[!] Launcher not found: $LauncherScript" -ForegroundColor Red
    exit 1
}
Write-Host "  Launcher:  $LauncherScript" -ForegroundColor Gray
Write-Host "  Project:   $ProjectRoot" -ForegroundColor Gray

# ── Remove existing task if present ──
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Write-Host "  Removing existing task..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# ── Create scheduled task ──
# Trigger: Daily at 3:30 AM (local time — set your Windows timezone to ET)
$trigger = New-ScheduledTaskTrigger -Daily -At "3:30AM"

# Action: Run PowerShell with the launcher script
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$LauncherScript`"" `
    -WorkingDirectory "$ProjectRoot"

# Settings: wake from sleep, retry on failure, don't stop if running
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -WakeToRun `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 5) `
    -ExecutionTimeLimit (New-TimeSpan -Hours 14) `
    -MultipleInstances IgnoreNew

# Register the task (runs as the current user)
Register-ScheduledTask `
    -TaskName $TaskName `
    -Trigger $trigger `
    -Action $action `
    -Settings $settings `
    -Description "Momentum-X daily paper trading. Runs Phase 0-4 (3:30 AM - 4:00 PM ET)." `
    -RunLevel Highest

Write-Host ""
Write-Host "  [OK] Scheduled task created: $TaskName" -ForegroundColor Green
Write-Host ""
Write-Host "  Schedule:   Daily at 3:30 AM (your local time)" -ForegroundColor White
Write-Host "  Wake:       Yes (wakes PC from sleep)" -ForegroundColor White
Write-Host "  Retries:    3 (5 min apart on failure)" -ForegroundColor White
Write-Host "  Time limit: 14 hours (kills at 5:30 PM)" -ForegroundColor White
Write-Host ""
Write-Host "  IMPORTANT: Set Windows timezone to Eastern Time!" -ForegroundColor Yellow
Write-Host "    Settings → Time & Language → Time zone → (UTC-05:00) Eastern" -ForegroundColor Yellow
Write-Host ""
Write-Host "  To verify:    Get-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Gray
Write-Host "  To run now:   Start-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Gray
Write-Host "  To remove:    .\scripts\install_scheduler.ps1 -Uninstall" -ForegroundColor Gray
Write-Host "  Logs at:      $ProjectRoot\logs\paper_YYYY-MM-DD.log" -ForegroundColor Gray
Write-Host ""
