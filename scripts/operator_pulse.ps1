<#
.SYNOPSIS
    doc 208 — MOMENTUM-X Operator pulse. Fired by Task Scheduler at the runbook times.

.DESCRIPTION
    One pulse of the AI Operator (doc 204-207). It:
      1. Maps the current time to a scheduled session label (boot-health, post-open, ...).
      2. Skips weekends.
      3. Runs the RELIABLE observe-only session (operator_observe.py) -- assembles the ORIENT
         brief, triages (classify only), posts Discord, logs, scores. ALWAYS works.
      4. OPTIONALLY invokes the richer Opus-4.8-MAX operator via `claude -p` (best-effort,
         gated by OPS_OPERATOR_USE_CLAUDE_CLI -- off until Pierce confirms the CLI runs
         headless in the scheduled context).

    OBSERVE-ONLY until T1 is armed (OPS_OPERATOR_T1_ENABLED). Never blocks trading.
#>
param([string]$Label = "")

$ErrorActionPreference = "Continue"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$LogDir = Join-Path $ProjectRoot "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
$Today = Get-Date -Format "yyyy-MM-dd"
$PulseLog = Join-Path $LogDir "operator_pulse_${Today}.log"

function PLog {
    param([string]$m)
    $ts = Get-Date -Format "HH:mm:ss"
    Add-Content -Path $PulseLog -Value "[$ts] $m"
    Write-Host "[$ts] $m"
}

# Skip weekends
$dow = (Get-Date).DayOfWeek
if ($dow -eq "Saturday" -or $dow -eq "Sunday") { PLog "Skip - $dow"; exit 0 }

# Map current time -> session label (the runbook schedule, local=ET)
if (-not $Label) {
    $schedule = @{
        "03:45" = "boot-health"; "06:30" = "pre-open-1"; "08:30" = "premarket";
        "09:25" = "pre-open-2"; "09:40" = "post-open"; "10:30" = "early";
        "11:45" = "late-am"; "13:00" = "midday"; "14:15" = "afternoon";
        "15:50" = "pre-close"; "16:10" = "close-out"; "17:30" = "deep-work"
    }
    $nowMin = (Get-Date).Hour * 60 + (Get-Date).Minute
    $best = "manual"
    $bestDelta = 999
    foreach ($k in $schedule.Keys) {
        $parts = $k.Split(":")
        $kmin = [int]$parts[0] * 60 + [int]$parts[1]
        $d = [math]::Abs($nowMin - $kmin)
        if ($d -lt $bestDelta) { $bestDelta = $d; $best = $schedule[$k] }
    }
    if ($bestDelta -le 8) { $Label = $best } else { $Label = "manual" }
}
# doc 224: master Operator kill-switch (OFF for the Tuesday 6/2 deploy). When disabled the
# pulse exits before spawning anything. Re-enable: set OPS_OPERATOR_ENABLED=1 (machine env).
if ($env:OPS_OPERATOR_ENABLED -eq "0" -or $env:OPS_OPERATOR_ENABLED -eq "false") {
    PLog "Operator DISABLED via OPS_OPERATOR_ENABLED - skipping pulse (session=$Label)"
    exit 0
}
PLog "Operator pulse - session=$Label (dow=$dow)"

Push-Location $ProjectRoot
try {
    # 1) RELIABLE observe-only session (always runs)
    PLog "Running observe session..."
    & python scripts/operator_observe.py --label $Label 2>> $PulseLog
    PLog "Observe session exit=$LASTEXITCODE"

    # 2) OPTIONAL richer Opus-4.8-MAX operator (best-effort; off until Pierce enables)
    if ($env:OPS_OPERATOR_USE_CLAUDE_CLI -eq "1") {
        $claude = Get-Command claude -ErrorAction SilentlyContinue
        if ($null -ne $claude) {
            PLog "Invoking claude -p (Opus 4.8 MAX) operator..."
            if ($env:OPS_OPERATOR_T1_ENABLED -eq "false") {
                $t1status = "DISARMED (observe-only - do not take actions)"
            } else {
                $t1status = "armed"
            }
            $prompt = "You are the MOMENTUM-X Operator. Follow docs/research-log/operator_runbook.md EXACTLY for session '$Label'. ORIENT first (run: python -m src.ops.operator_session), then triage and act WITHIN TIER. T1 is $t1status. Post findings to Discord and update the scorecard. Be restrained - a correct no-op is the high score."
            if ([string]::IsNullOrWhiteSpace($env:OPS_OPERATOR_MODEL)) {
                & claude -p $prompt 2>> $PulseLog
            } else {
                & claude -p $prompt --model $env:OPS_OPERATOR_MODEL 2>> $PulseLog
            }
            PLog "claude -p exit=$LASTEXITCODE"
        } else {
            PLog "claude CLI not found - observe session already ran (sufficient for observe mode)"
        }
    }
} catch {
    PLog "ERROR: $_"
    # doc 272 A6: propagate failure — was a hard `exit 0`, so Task Scheduler could never
    # show red (the doc-269 census exit-mask class, same fix as ShadowGrader).
    $script:PulseFailed = $true
} finally {
    Pop-Location
}
if ($script:PulseFailed) { exit 1 }
exit 0
