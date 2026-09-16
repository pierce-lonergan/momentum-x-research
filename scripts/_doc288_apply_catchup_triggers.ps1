<#
.SYNOPSIS
    doc 288 (operator, RUN AS ADMINISTRATOR): add 05:15 + 06:00 ET catch-up triggers to
    \MomentumX-PaperTrading so a 04:30 preflight abort no longer costs the whole day.

.DESCRIPTION
    Why: on 7/8 and 7/10 the 04:30 launch aborted on a transient Alpaca timeout and NOTHING relaunched
    it. RestartOnFailure=3/PT5M is configured but does not fire for a script that runs to completion and
    returns a non-zero exit code (Task Scheduler records that as a completed run, not an engine failure).
    The launcher (scripts\daily_paper_trade.ps1) is idempotent -- D90 lock-file + MultipleInstancesPolicy
    =IgnoreNew mean a re-run while the bot is already up just exits 0 -- so two extra daily triggers give
    two FREE retries with zero risk of a double-launch. The doc-287 preflight degraded-start already
    prevents the specific transient-timeout abort; these triggers are defense-in-depth for any other
    early-death cause.

    This script REQUIRES ELEVATION (the task runs at HighestAvailable). It backs the task up first, adds
    the two triggers (idempotent -- running it twice will not duplicate them), and verifies the result.

.NOTES
    Run:  right-click -> Run with PowerShell (as Administrator), or from an elevated prompt:
          powershell -NoProfile -ExecutionPolicy Bypass -File scripts\_doc288_apply_catchup_triggers.ps1
#>
$ErrorActionPreference = 'Stop'
$name = 'MomentumX-PaperTrading'

# ── elevation guard ──
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "ERROR: this must run ELEVATED (Run as Administrator). The task runs at HighestAvailable." -ForegroundColor Red
    exit 1
}

# ── backup ──
$bakDir = Join-Path $PSScriptRoot '..\data\research\doc288'
if (-not (Test-Path $bakDir)) { New-Item -ItemType Directory -Force -Path $bakDir | Out-Null }
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$bak = Join-Path $bakDir "MomentumX-PaperTrading.$stamp.bak.xml"
schtasks /query /tn "\$name" /xml | Out-File -FilePath $bak -Encoding utf8
Write-Host "backup written: $bak"

# ── build the desired trigger set: keep every existing trigger, add 05:15 + 06:00 daily if absent ──
$task = Get-ScheduledTask -TaskName $name
$existing = @($task.Triggers)
function Has-DailyAt([object[]]$trigs, [string]$hhmm) {
    foreach ($t in $trigs) {
        if ($t.StartBoundary -and ([datetime]$t.StartBoundary).ToString('HH:mm') -eq $hhmm) { return $true }
    }
    return $false
}
$toAdd = @()
if (-not (Has-DailyAt $existing '05:15')) { $toAdd += (New-ScheduledTaskTrigger -Daily -At '5:15AM') }
if (-not (Has-DailyAt $existing '06:00')) { $toAdd += (New-ScheduledTaskTrigger -Daily -At '6:00AM') }

if ($toAdd.Count -eq 0) {
    Write-Host "Both catch-up triggers already present -- nothing to do." -ForegroundColor Green
} else {
    Set-ScheduledTask -TaskName $name -Trigger ($existing + $toAdd) | Out-Null
    Write-Host "Added $($toAdd.Count) catch-up trigger(s)." -ForegroundColor Green
}

# ── verify ──
$after = @((Get-ScheduledTask -TaskName $name).Triggers)
Write-Host "`nTriggers now on \$name ($($after.Count)):"
$after | ForEach-Object { Write-Host ("  - " + ([datetime]$_.StartBoundary).ToString('HH:mm') + " daily") }
if ($after.Count -lt 3) {
    Write-Host "WARNING: expected >= 3 triggers (04:30 + 05:15 + 06:00). Review manually." -ForegroundColor Yellow
    exit 1
}
Write-Host "`nOK: 04:30 primary + 05:15/06:00 catch-ups are live. A morning abort now gets two free retries." -ForegroundColor Green
