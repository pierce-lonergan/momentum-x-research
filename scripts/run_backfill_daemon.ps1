# D221 Phase E — Backfill daemon launcher.
#
# Runs `python -m src.backfill_agent --mode continuous --workers 3` as a
# long-running background process. Designed to be invoked by Windows Task
# Scheduler (see docs/runbooks/backfill_daemon.md).
#
# Behavior:
#   - Strict mode ($ErrorActionPreference = "Stop") so a venv-activation
#     failure does NOT silently fall through to a system-Python launch.
#   - Logs stdout+stderr to data/backfill_agent/logs/<DAEMON_START_DATE>.log
#     where DAEMON_START_DATE is captured at process start, so a daemon that
#     crosses midnight does not split its log at an arbitrary point.
#   - On exit: appends exit code + timestamp to data/backfill_agent/exit.log
#     (APPEND mode — preserves crash history).
#
# Pause / resume / status: see docs/runbooks/backfill_daemon.md.
# Do NOT add launcher logic for those — runtime control belongs to the agent.

$ErrorActionPreference = "Stop"

# ── Resolve repo root ──────────────────────────────────────────────────
# The script lives at <repo>/scripts/run_backfill_daemon.ps1
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$RepoRoot  = Split-Path -Parent $ScriptDir
Set-Location -Path $RepoRoot

# ── Paths ──────────────────────────────────────────────────────────────
$DaemonStartDate = (Get-Date).ToString("yyyy-MM-dd")
$DaemonStartIso  = (Get-Date -Format "yyyy-MM-ddTHH:mm:ssK")
$LogDir   = Join-Path $RepoRoot "data\backfill_agent\logs"
$LogFile  = Join-Path $LogDir   "$DaemonStartDate.log"
$ExitLog  = Join-Path $RepoRoot "data\backfill_agent\exit.log"

# Ensure log dir exists
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

# ── Locate Python interpreter ──────────────────────────────────────────
# Prefer a project venv if present, else fall back to system python.
# The same logic as scripts/daily_paper_trade.ps1 uses for production.
$PythonExe = $null
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
    $PythonExe = $VenvPython
} else {
    $SystemPython = (Get-Command python -ErrorAction SilentlyContinue).Source
    if (-not $SystemPython) {
        # No Python found — append to exit.log and bail
        $msg = "$DaemonStartIso | EXIT 99 | no Python interpreter found"
        Add-Content -Path $ExitLog -Value $msg -Encoding utf8
        throw "no Python interpreter on PATH and no .venv found"
    }
    $PythonExe = $SystemPython
}

# ── Header ─────────────────────────────────────────────────────────────
$Header = @(
    "================================================================"
    "D221 Backfill Daemon — start"
    "  start_iso:        $DaemonStartIso"
    "  python:           $PythonExe"
    "  repo_root:        $RepoRoot"
    "  log_file:         $LogFile"
    "  exit_log:         $ExitLog"
    "  pause sentinel:   data\backfill_agent\PAUSED (touch to pause)"
    "================================================================"
) -join "`n"
Add-Content -Path $LogFile -Value $Header -Encoding utf8

# ── Run the agent ──────────────────────────────────────────────────────
# Stream stdout+stderr to the log file via *>>&1 redirection. The agent
# itself uses Python logging which writes to stderr.
$exitCode = 99
try {
    & $PythonExe -m src.backfill_agent --mode continuous --workers 3 *>> $LogFile
    $exitCode = $LASTEXITCODE
} catch {
    $exitCode = 99
    Add-Content -Path $LogFile -Value "FATAL: $($_.Exception.Message)" -Encoding utf8
} finally {
    $endIso = (Get-Date -Format "yyyy-MM-ddTHH:mm:ssK")
    $exitLine = "$endIso | exit=$exitCode | started_at=$DaemonStartIso | log=$DaemonStartDate.log"
    Add-Content -Path $ExitLog -Value $exitLine -Encoding utf8
    Add-Content -Path $LogFile -Value "================================================================`nDaemon exit at $endIso (code $exitCode)" -Encoding utf8
}

exit $exitCode
