<#
.SYNOPSIS
    doc 208 — register the MOMENTUM-X Operator schedule (run ONCE, elevated).

.DESCRIPTION
    Creates/updates the 'MomentumX-Operator' scheduled task that fires scripts/operator_pulse.ps1
    at the 12 runbook session times (weekdays). Mirrors the trading launcher's task style
    (RunLevel Highest, MultipleInstances IgnoreNew). OBSERVE-ONLY until T1 is armed.

    Run in an ELEVATED PowerShell:  .\scripts\register_operator_schedule.ps1
    Remove with:                    Unregister-ScheduledTask -TaskName 'MomentumX-Operator' -Confirm:$false
#>
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Pulse = Join-Path $ProjectRoot "scripts\operator_pulse.ps1"
if (-not (Test-Path $Pulse)) { throw "operator_pulse.ps1 not found at $Pulse" }

$TaskName = "MomentumX-Operator"
$times = @("03:45","06:30","08:30","09:25","09:40","10:30","11:45","13:00","14:15","15:50","16:10","17:30")

# Build the action argument with single-quote concatenation (no backtick-quote escaping).
$argStr = '-NoProfile -ExecutionPolicy Bypass -File "' + $Pulse + '"'
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argStr -WorkingDirectory $ProjectRoot

$triggers = @()
foreach ($t in $times) { $triggers += (New-ScheduledTaskTrigger -Daily -At $t) }

# NOTE: the cmdlet is New-ScheduledTaskSettingsSet (NOT New-ScheduledTaskSettings) — mirrors
# the proven scripts/install_scheduler.ps1 pattern.
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -WakeToRun `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed existing '$TaskName'"
}

$desc = "MOMENTUM-X AI Operator (doc 204-208) - observe-only until T1 armed. Fires operator_pulse.ps1 at 12 runbook times, weekdays."
# RunLevel Highest is passed directly to Register-ScheduledTask (runs as the current user,
# same as install_scheduler.ps1) -- avoids a New-ScheduledTaskPrincipal domain/user mismatch.
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers -Settings $settings -Description $desc -RunLevel Highest | Out-Null

Write-Host "Registered '$TaskName' with $($times.Count) daily triggers: $($times -join ', ')"
Write-Host "OBSERVE-ONLY (T1 disarmed). To arm T1 later: set OPS_OPERATOR_T1_ENABLED=true (machine env)."
Write-Host "To enable the richer claude -p operator: set OPS_OPERATOR_USE_CLAUDE_CLI=1 and OPS_OPERATOR_MODEL once the CLI runs headless."
