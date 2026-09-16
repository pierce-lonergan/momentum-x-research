<#
.SYNOPSIS
    Momentum-X Daily Paper Trading Launcher
    Designed for Windows Task Scheduler to run every trading day.

.DESCRIPTION
    This script:
    1. Checks if today is a weekday (skips weekends)
    2. Kills any stale Momentum-X process from previous runs
    3. Loads secrets from external file -> .env
    4. Validates Python environment and critical imports
    5. Starts the paper trading process with logging
    6. Logs output to timestamped log file for debugging
    7. Sends a Windows toast notification on start/failure

    Schedule with Task Scheduler to run at 3:30 AM ET daily.
    The trading system handles its own phase timing:
      Phase 0: 3:30-4:00 AM ET - Pre-market research (news, SEC, technicals)
      Phase 1: 4:00-9:20 AM ET - Pre-market scanning
      Phase 1.5: 9:20 AM ET - Fast-path 3-agent scoring
      Phase 2: 9:30 AM ET - Market open, fast-path fire + parallel eval
      Phase 3: 10:00-3:45 PM ET - Position management + exit intelligence
      Phase 4: 3:45-4:00 PM ET - Close positions, session report

.PARAMETER SecretsFile
    Path to secrets .env file. Default: $HOME\momentum-x-secrets.env
#>

param(
    [string]$SecretsFile = "$env:USERPROFILE\momentum-x-secrets.env"
)

# D98: Transcript BEFORE ErrorActionPreference to capture ALL crash output.
# Without this, Task Scheduler failures leave zero diagnostics.
$_d98_transcript = Join-Path (Join-Path (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)) "logs") "transcript_$(Get-Date -Format 'yyyy-MM-dd').log"
try { Start-Transcript -Path $_d98_transcript -Append -Force } catch {}

$ErrorActionPreference = "Stop"

# ── Configuration ──
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

# D217: Path assertion - refuse to run from worktrees or unexpected locations.
# After the April 9 incident where Task Scheduler ran from a stale worktree
# for two weeks without anyone noticing, this is a hard check.
$ExpectedRoot = Join-Path $env:USERPROFILE "Documents\GitHub\momentum-x"
if ($ProjectRoot -ne $ExpectedRoot) {
    $msg = "D217 FATAL: Running from '$ProjectRoot', expected '$ExpectedRoot'. " +
           "Worktrees contain stale code. Update Task Scheduler to point to the main repo."
    Write-Error $msg
    try { Add-Content -Path (Join-Path $ProjectRoot "logs\launcher_error.log") -Value "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg" } catch {}
    exit 1
}
$EnvTarget = Join-Path $ProjectRoot ".env"
$LogDir = Join-Path $ProjectRoot "logs"
$Today = Get-Date -Format "yyyy-MM-dd"
$LogFile = Join-Path $LogDir "paper_${Today}.log"
$LockFile = Join-Path $LogDir "momentum-x.lock"

# ── Create log directory ──
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

# ── Logging helper ──
function Log {
    param([string]$Message, [string]$Level = "INFO")
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] [$Level] $Message"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line
}

# ── Skip weekends ──
$dayOfWeek = (Get-Date).DayOfWeek
if ($dayOfWeek -eq "Saturday" -or $dayOfWeek -eq "Sunday") {
    Log "Skipping - today is $dayOfWeek (market closed)" "SKIP"
    exit 0
}

Log "==================================================="
Log "MOMENTUM-X Daily Paper Trading - $Today"
Log "==================================================="

# ── D90: Kill stale Momentum-X processes ──────────────────────────────
# Prevents duplicate instances that corrupt state and create ghost positions.
# The scheduled task has MultipleInstances=IgnoreNew, but manual runs
# or zombie processes from Task Scheduler retries can overlap.
Log "Checking for stale Momentum-X processes..."
$staleProcs = Get-Process -Name "python" -ErrorAction SilentlyContinue |
    Where-Object {
        try {
            $cmdline = (Get-CimInstance Win32_Process -Filter "ProcessId=$($_.Id)" -ErrorAction SilentlyContinue).CommandLine
            $cmdline -match "main\s+paper" -or $cmdline -match "momentum"
        } catch { $false }
    }
if ($staleProcs) {
    Log "D90: Found $($staleProcs.Count) stale python process(es) - killing..." "WARN"
    foreach ($proc in $staleProcs) {
        try {
            Stop-Process -Id $proc.Id -Force
            Log "  Killed PID $($proc.Id)" "WARN"
        } catch {
            Log "  Failed to kill PID $($proc.Id): $_" "ERROR"
        }
    }
    Start-Sleep -Seconds 2  # Let ports/files release
}

# ── D90: Clean up stale lock file ──
if (Test-Path $LockFile) {
    $lockContent = Get-Content $LockFile -ErrorAction SilentlyContinue
    $lockPid = $lockContent | Select-Object -First 1
    $lockProc = Get-Process -Id $lockPid -ErrorAction SilentlyContinue
    if (-not $lockProc) {
        Log "D90: Removing stale lock file (PID $lockPid no longer running)" "WARN"
        Remove-Item $LockFile -Force
    } else {
        Log "D90: Another instance is running (PID $lockPid) - exiting" "WARN"
        exit 0
    }
}

# ── Write lock file ──
$PID | Out-File -FilePath $LockFile -NoNewline

# ── Load secrets ──
if (Test-Path $SecretsFile) {
    Copy-Item -Path $SecretsFile -Destination $EnvTarget -Force
    Log "Secrets loaded from $SecretsFile"
} elseif (Test-Path $EnvTarget) {
    Log "Secrets file not found ($SecretsFile) - using existing .env" "WARN"
} else {
    Log "No secrets file and no .env found - cannot start" "ERROR"
    Remove-Item $LockFile -Force -ErrorAction SilentlyContinue
    exit 1
}

# ── D90: Pre-flight validation ──────────────────────────────────────
# Validate Python, dependencies, and API connectivity BEFORE starting
# the 13-hour trading session. Fail fast with clear error messages.

Log "Running pre-flight checks..."

# Check Python exists
$pythonPath = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $pythonPath) {
    Log "FATAL: Python not found in PATH" "ERROR"
    Remove-Item $LockFile -Force -ErrorAction SilentlyContinue
    exit 1
}
Log "  Python: $pythonPath"

# Check critical imports
Push-Location $ProjectRoot
$importCheck = & python -c "
import sys
errors = []
try:
    import config.settings
except Exception as e:
    errors.append(f'config.settings: {e}')
try:
    import litellm
except Exception as e:
    errors.append(f'litellm: {e}')
try:
    import httpx
except Exception as e:
    errors.append(f'httpx: {e}')
try:
    import alpaca_trade_api
except ImportError:
    pass  # Not required, we use httpx directly
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception as e:
    errors.append(f'dotenv: {e}')
try:
    import os
    api_key = os.getenv('ALPACA_API_KEY', '')
    if not api_key:
        errors.append('ALPACA_API_KEY not set in .env')
    together_key = os.getenv('TOGETHER_AI_API_KEY', '')
    if not together_key:
        errors.append('TOGETHER_AI_API_KEY not set in .env')
except Exception as e:
    errors.append(f'env check: {e}')
if errors:
    print('PREFLIGHT_FAIL:' + '|'.join(errors))
    sys.exit(1)
else:
    print('PREFLIGHT_OK')
    sys.exit(0)
" 2>&1
Pop-Location

if ($LASTEXITCODE -ne 0 -or "$importCheck" -match "PREFLIGHT_FAIL") {
    Log "FATAL: Pre-flight validation failed: $importCheck" "ERROR"
    Remove-Item $LockFile -Force -ErrorAction SilentlyContinue
    exit 1
}
Log "  Pre-flight checks passed"
Log "  D97: Post-preflight checkpoint -- proceeding to session state cleanup"

# ── D90 + Wed 2026-04-22 Bug B fix: clean stale session state ────
# Remove YESTERDAY's session state file to force a clean start; KEEP
# today's so D64 can recover positions, stop_order_ids, target_prices,
# and tranche state across an intraday restart.
#
# Bug history: prior code read $stateJson.date but the JSON field is
# actually $stateJson.session_date. So the comparison was always
# $null -ne $Today = TRUE, meaning the file got deleted EVERY morning
# regardless of date. D64 then logged "No session state file found
# fresh start" every day, lost all per-position state, and the system
# came up reconciling only from the broker -- losing stop_order_ids,
# target_prices, tranche records, opened_at timestamps, etc.
#
# Caught Wed 2026-04-22 morning when this morning's startup wiped my
# Tue-evening GTC stop reference (along with everything else). See
# tests/unit/test_session_state_recovery.py for the regression
# test set guaranteeing this doesn't drift again.
$stateFile = Join-Path (Join-Path $ProjectRoot "data") "session_state.json"
if (Test-Path $stateFile) {
    try {
        $stateJson = Get-Content $stateFile -Raw | ConvertFrom-Json
        # NOTE: field is "session_date", NOT "date". Do NOT change without
        # also updating src/execution/session_state.py SessionState schema.
        $stateDate = $stateJson.session_date
        if (-not $stateDate) {
            Log "  Session state file missing 'session_date' field -- treating as corrupt" "WARN"
            Remove-Item $stateFile -Force -ErrorAction SilentlyContinue
        } elseif ($stateDate -ne $Today) {
            Log "  Removing stale session state from $stateDate (today is $Today)"
            Remove-Item $stateFile -Force
        } else {
            Log "  Session state from $stateDate is current -- preserving for D64 recovery"
        }
    } catch {
        Log "  Removing corrupt session state file: $_" "WARN"
        Remove-Item $stateFile -Force -ErrorAction SilentlyContinue
    }
}

# ── D218: Pre-flight health check ──
# Runs 4 critical checks before starting the trading system.
# If any fail, abort immediately. Better to not start than start broken.
Log "Running pre-flight health check..."
Push-Location $ProjectRoot
$_prevEAP_preflight = $ErrorActionPreference
$ErrorActionPreference = "Continue"
# 2026-05-12: pass OPS_ALERT_WEBHOOK_URL to preflight so HALT-switch
# detection (and any check failure) reaches Discord at 04:30 ET startup,
# not just the transcript. The HALT switch was the bug that cost 2 days
# of trading (doc 156-158) -- this surface is the earliest possible
# operator visibility.
$_ops_webhook = [Environment]::GetEnvironmentVariable("OPS_ALERT_WEBHOOK_URL", "Process")
if ($_ops_webhook) {
    $preflightResult = & python scripts/preflight_check.py --webhook $_ops_webhook 2>&1
} else {
    $preflightResult = & python scripts/preflight_check.py 2>&1
}
$preflightExit = $LASTEXITCODE
$ErrorActionPreference = $_prevEAP_preflight
Pop-Location

foreach ($line in $preflightResult) {
    Log "  $line"
}

if ($preflightExit -ne 0) {
    Log "FATAL: Pre-flight check failed — system will NOT start" "ERROR"
    Remove-Item $LockFile -Force -ErrorAction SilentlyContinue
    exit 1
}
Log "Pre-flight check passed"

# ── Toast notification (non-blocking) ──
# D97: Wrapped in a separate scope with SilentlyContinue to prevent
# WinRT type-loading errors from crashing the script under Task Scheduler.
# ErrorActionPreference=Stop makes even WinRT parser errors terminating,
# and type-loading errors can bypass try/catch in some PowerShell versions.
$_prevEAP = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
try {
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
    $textNodes = $template.GetElementsByTagName("text")
    $textNodes.Item(0).AppendChild($template.CreateTextNode("Momentum-X Starting")) | Out-Null
    $textNodes.Item(1).AppendChild($template.CreateTextNode("Paper trading session $Today")) | Out-Null
    $toast = [Windows.UI.Notifications.ToastNotification]::new($template)
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Momentum-X").Show($toast)
} catch {
    Log "Toast notification not available (OK)" "WARN"
}
$ErrorActionPreference = $_prevEAP

# ── Run paper trading ──
Log "Starting: python -m main paper"
Log "Log file: $LogFile"

Push-Location $ProjectRoot
# D217/D218 FIX: Do NOT pipe Python's stdout/stderr through ForEach-Object.
# The pipe has a ~64KB buffer. When the buffer fills (Python writes faster
# than ForEach-Object drains), stderr.write() blocks, which blocks the
# QueueListener background thread, which fills the QueueHandler queue,
# which blocks logging.emit(), which blocks the asyncio event loop.
# This was the root cause of EVERY production hang (April 9, 10, 13).
#
# Python handles its own logging via RotatingFileHandler -> logs/momentum_YYYY-MM-DD.log.
# The launcher pipe is redundant and actively harmful.
#
# Instead: redirect stderr to the launcher log file directly (no pipe),
# and let Python's RotatingFileHandler be the primary log path.
$_prevEAP_run = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    # Start Python WITHOUT piping. Stderr goes to launcher log file.
    # Python's RotatingFileHandler writes to logs/momentum_YYYY-MM-DD.log independently.
    & python -u -m main paper 2>> $LogFile
    $exitCode = $LASTEXITCODE
} catch {
    $exitCode = 1
    Log "EXCEPTION: $_" "ERROR"
} finally {
    $ErrorActionPreference = $_prevEAP_run
    Pop-Location
    # ── Clean up lock file ──
    Remove-Item $LockFile -Force -ErrorAction SilentlyContinue
}

# ── Report ──
if ($exitCode -eq 0) {
    Log "Paper trading session completed successfully" "OK"
} else {
    Log "Paper trading exited with code $exitCode" "ERROR"

    # Error notification (D97: wrapped with SilentlyContinue like startup toast)
    $_prevEAP2 = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    try {
        $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
        $textNodes = $template.GetElementsByTagName("text")
        $textNodes.Item(0).AppendChild($template.CreateTextNode("Momentum-X FAILED")) | Out-Null
        $textNodes.Item(1).AppendChild($template.CreateTextNode("Exit code $exitCode - check $LogFile")) | Out-Null
        $toast = [Windows.UI.Notifications.ToastNotification]::new($template)
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Momentum-X").Show($toast)
    } catch {}
    $ErrorActionPreference = $_prevEAP2
}

# ── doc 193: post-close measurement scorecard (best-effort, decoupled) ──
# Quantify the session we just traded: SELECTION win% / forward return + the marketable
# fill edge + gate-correct% -> data/reports/measurement_trend.jsonl (doc 192: SELECTION
# dominates fills ~30x; selWin% is the headline metric to move). Runs AFTER the bot has
# exited, so it NEVER touches the trading hot path and CANNOT affect the session exit
# code. Scores only $Today (one day's bars, ~130 API calls). Disable with
# $env:MX_POSTCLOSE_SCORECARD = "0".
if ($env:MX_POSTCLOSE_SCORECARD -ne "0") {
    Log "Running post-close measurement scorecard for $Today (best-effort)..."
    $_prevEAP_sc = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        Push-Location $ProjectRoot
        & python scripts/post_close_scorecard.py $Today 2>> $LogFile
        Pop-Location
        Log "Post-close scorecard complete -> data/reports/measurement_trend.jsonl"
    } catch {
        Log "Post-close scorecard failed (non-fatal): $_" "WARN"
    }
    $ErrorActionPreference = $_prevEAP_sc
}

try { Stop-Transcript } catch {}
exit $exitCode
