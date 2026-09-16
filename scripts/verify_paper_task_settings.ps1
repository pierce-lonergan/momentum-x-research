# Tue 2026-04-21 Fix 3: Verification script — confirms the live
# MomentumX-PaperTrading task XML has the expected restart-on-failure
# policy populated, not just default-reported by Get-ScheduledTask.
#
# Today's bug surfaced because Get-ScheduledTask returned a phantom
# RestartCount=3/Interval=PT5M while the underlying XML had an empty
# <RestartOnFailure/> element. This script trusts ONLY the XML.
#
# Run any time:
#     powershell -ExecutionPolicy Bypass -File scripts/verify_paper_task_settings.ps1
#
# Returns 0 on PASS, 1 on FAIL. Suitable for CI / scheduled health
# probe.

[CmdletBinding()]
param(
    [string]$TaskName = "MomentumX-PaperTrading",
    [int]$ExpectedRestartCount = 6,
    [int]$ExpectedRestartIntervalMinutes = 5,
    [int]$ExpectedExecutionTimeLimitHours = 14
)

$ErrorActionPreference = "Stop"

# Tue 2026-04-21 note. As of commit time the live task XML had
# RestartCount=3 (not the spec'd 6). Elevating to 6 requires
# `Set-ScheduledTask` or `schtasks /create` to run as admin. Run
# `configure_paper_task_restart_policy.ps1` from an elevated shell to
# apply Count=6. Until then this verify script will report "FAIL"
# pointing to the gap. The functional behavior at Count=3 is still a
# 3x improvement over today's "no restart fired at all" outcome.
#
# Today's bug was that even the existing Count=3 policy didn't fire
# after the watchdog SIGKILL'd Python at 10:18 ET. Whether Task
# Scheduler interpreted the launcher's exit code (-1 / 0xFFFFFFFF)
# as "failure" or "abandoned" is a separate diagnostic flagged for
# the Tue architecture call.

Write-Host "=== Verifying $TaskName task XML ==="

# Pull the source-of-truth XML
try {
    $xml = Export-ScheduledTask -TaskName $TaskName
} catch {
    Write-Host "FAIL: cannot read task '$TaskName' -- $_" -ForegroundColor Red
    exit 1
}

$failures = @()

# Check 1: RestartOnFailure populated, not empty
if ($xml -match "<RestartOnFailure\s*/>") {
    $failures += "RestartOnFailure element is empty (today's bug). Run configure_paper_task_restart_policy.ps1."
}

# Check 2: explicit Interval value
$expectedInterval = "PT$($ExpectedRestartIntervalMinutes)M"
if ($xml -notmatch "<Interval>$expectedInterval</Interval>") {
    $failures += "Restart Interval missing or != $expectedInterval"
}

# Check 3: explicit Count value
if ($xml -notmatch "<Count>$ExpectedRestartCount</Count>") {
    $failures += "Restart Count missing or != $ExpectedRestartCount"
}

# Check 4: ExecutionTimeLimit (covers a full session)
$expectedExec = "PT$($ExpectedExecutionTimeLimitHours)H"
if ($xml -notmatch "<ExecutionTimeLimit>$expectedExec</ExecutionTimeLimit>") {
    $failures += "ExecutionTimeLimit missing or != $expectedExec"
}

# Check 5: StartWhenAvailable (recover from missed triggers if machine was off)
if ($xml -notmatch "<StartWhenAvailable>true</StartWhenAvailable>") {
    $failures += "StartWhenAvailable should be true (handle reboots / missed triggers)"
}

# Report
Write-Host ""
if ($failures.Count -eq 0) {
    Write-Host "PASS: all checks green" -ForegroundColor Green
    Write-Host "  RestartOnFailure: <Interval>$expectedInterval</Interval> <Count>$ExpectedRestartCount</Count>"
    Write-Host "  ExecutionTimeLimit: $expectedExec"
    Write-Host "  StartWhenAvailable: true"
    exit 0
} else {
    Write-Host "FAIL: $($failures.Count) check(s) failed" -ForegroundColor Red
    foreach ($f in $failures) { Write-Host "  - $f" -ForegroundColor Red }
    Write-Host ""
    Write-Host "Remediation: powershell -ExecutionPolicy Bypass -File scripts\configure_paper_task_restart_policy.ps1"
    exit 1
}
