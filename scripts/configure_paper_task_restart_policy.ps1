# Tue 2026-04-21 Fix 3: Task Scheduler auto-restart-on-failure for MomentumX-PaperTrading.
#
# Bug: today's 10:18 ET watchdog SIGKILL of Python left the system dead
# from 10:18 -> 16:00 (5h 42m), with ELSE held overnight on a DAY-TIF
# stop that subsequently expired at close. The watchdog ASSUMED Task
# Scheduler would auto-restart the killed task. Inspection found
# `Get-ScheduledTask` reported RestartCount=3/Interval=PT5M, but the
# exported task XML had an EMPTY <RestartOnFailure/> element — i.e.
# the policy was reported but not enforced. No restart fired.
#
# This script writes the policy properly so it actually persists in
# the task XML. After running, `verify_paper_task_settings.ps1` should
# print "PASS" and Export-ScheduledTask should show the populated
# <RestartOnFailure><Interval>PT5M</Interval><Count>6</Count></RestartOnFailure>.
#
# Per user spec: "On failure: retry every 5 minutes, max 6 retries."
#   Coverage envelope: 6 × 5min = 30 minutes after a kill, vs today's
#   5h 42m dark window. Combined with Fix 2's heartbeat keeper +
#   raised threshold, the joint behaviour becomes:
#     1. Watchdog only fires on genuine event-loop deadlock (180/240s)
#     2. If it does fire, Task Scheduler restarts within 5 min
#     3. Worst-case dark window: ~5-30 minutes (vs 5h 42m today)

[CmdletBinding()]
param(
    [string]$TaskName = "MomentumX-PaperTrading",
    [int]$RestartCount = 6,
    [int]$RestartIntervalMinutes = 5
)

Write-Host "=== Configuring restart-on-failure for $TaskName ==="

# Pre-condition: task exists
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop

# Build a fresh settings object preserving the existing important fields
# and adding the restart-on-failure values that today's bug surfaced.
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 14) `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -RestartCount $RestartCount `
    -RestartInterval (New-TimeSpan -Minutes $RestartIntervalMinutes)

# Apply
Set-ScheduledTask -TaskName $TaskName -Settings $settings | Out-Null

Write-Host "Applied: RestartCount=$RestartCount, RestartInterval=$($RestartIntervalMinutes)min"
Write-Host ""

# Verify via Export-ScheduledTask (the XML is the source of truth)
Write-Host "=== Post-apply verification ==="
$xml = Export-ScheduledTask -TaskName $TaskName
$restartBlock = ($xml -split "`n" | Select-String -Pattern "RestartOnFailure" -Context 2,2)
$restartBlock | ForEach-Object { Write-Host $_.ToString() }

# Confirm both child elements present
$ok_interval = $xml -match "<Interval>PT$($RestartIntervalMinutes)M</Interval>"
$ok_count    = $xml -match "<Count>$RestartCount</Count>"

if ($ok_interval -and $ok_count) {
    Write-Host ""
    Write-Host "PASS: <RestartOnFailure> populated with Interval=PT$($RestartIntervalMinutes)M and Count=$RestartCount" -ForegroundColor Green
    exit 0
} else {
    Write-Host ""
    Write-Host "FAIL: restart policy did not persist as expected." -ForegroundColor Red
    Write-Host "      ok_interval=$ok_interval ok_count=$ok_count"
    Write-Host "      Inspect: Export-ScheduledTask -TaskName $TaskName"
    exit 1
}
