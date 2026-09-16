<#
.SYNOPSIS
    Momentum-X Daily Data Ingestion Pipeline
    Refreshes polygon_warehouse + derived catalogs after market close.

.DESCRIPTION
    Resolves doc 157 Bug 2: NO scheduled task backfills polygon data,
    so day_aggs / aftermath_strat go stale (11+ days as of 2026-05-12).

    This script:
    1. Pulls recent day_aggs + minute_aggs from Polygon API (resumable)
    2. Rebuilds high_movers_catalog (incremental via --start)
    3. Rebuilds aftermath_catalog + aftermath_strat (full rebuild from
       cached minute_aggs; ~3-5 min)
    4. Rebuilds intraday_paths + microstructure features
    5. Logs to logs/data_ingest_YYYY-MM-DD.log

    Schedule with Task Scheduler:
      MomentumX-DataIngest, runs daily at 17:30 ET (after market close
      and after EOD reconciliation; before next morning's 04:30 ET bot start).

    Failures here do NOT affect production trading (the bot uses live
    Polygon/Alpaca API, not the local lake). Only shadow runner +
    research analysis depend on the lake being fresh.

.PARAMETER SecretsFile
    Path to secrets .env file. Default: $HOME\momentum-x-secrets.env

.PARAMETER ProjectRoot
    Repo root path. Default: script's parent directory.

.PARAMETER LookbackDays
    How many days of data to ensure are present (default: 30).
    Catalog scripts re-process from scratch but the polygon API pulls
    are incremental (skip already-pulled (ticker, date, endpoint) tuples).

.PARAMETER DryRun
    If $true, only log what would run; don't actually pull or rebuild.
#>

param(
    [string]$SecretsFile = "$env:USERPROFILE\momentum-x-secrets.env",
    [string]$ProjectRoot = (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)),
    [int]$LookbackDays = 30,
    [switch]$DryRun
)

# 2026-05-12 fix: was "Stop" which made Python stderr INFO logs trigger
# PowerShell exceptions in the Invoke-Step try/catch (running every step
# as failure even when Python exited 0). Use "Continue" -- step success
# is determined by $LASTEXITCODE explicitly.
$ErrorActionPreference = "Continue"
$Today = (Get-Date).ToString("yyyy-MM-dd")
$LogFile = Join-Path $ProjectRoot "logs\data_ingest_$Today.log"

function Log {
    param([string]$Message, [string]$Level = "INFO")
    $ts = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    $line = "[$ts] [$Level] $Message"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line
}

# ── Skip on weekends (no new market data to pull) ──────────────────
$dow = (Get-Date).DayOfWeek
if ($dow -eq "Saturday" -or $dow -eq "Sunday") {
    Log "Weekend ($dow) - no market data to pull. Exiting."
    exit 0
}

Log "==================================================="
Log "MOMENTUM-X Daily Data Ingestion - $Today"
Log "==================================================="
Log "ProjectRoot: $ProjectRoot"
Log "LookbackDays: $LookbackDays"
Log "DryRun: $DryRun"

# ── Load secrets so POLYGON_API_KEY is in env ──────────────────────
if (-not (Test-Path $SecretsFile)) {
    Log "Secrets file not found: $SecretsFile" "ERROR"
    exit 1
}
Log "Loading secrets from $SecretsFile"
Get-Content $SecretsFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
        $idx = $line.IndexOf("=")
        $key = $line.Substring(0, $idx).Trim()
        $val = $line.Substring($idx + 1).Trim()
        [Environment]::SetEnvironmentVariable($key, $val, "Process")
    }
}

if (-not $env:POLYGON_API_KEY) {
    Log "POLYGON_API_KEY not loaded from secrets" "ERROR"
    exit 1
}
Log "POLYGON_API_KEY loaded (length=$($env:POLYGON_API_KEY.Length))"

# ── Verify Python ──────────────────────────────────────────────────
$pythonPath = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $pythonPath) {
    Log "python not found in PATH" "ERROR"
    exit 1
}
Log "Python: $pythonPath"

Set-Location $ProjectRoot

if ($DryRun) {
    Log "DryRun=True. Would run the following pipeline:"
    Log "  1. python scripts/polygon_backfill_all.py --corpus tick_validation"
    Log "  2. python scripts/polygon_high_mover_catalog.py"
    Log "  3. python scripts/polygon_aftermath_catalog.py"
    Log "  4. python scripts/build_intraday_paths.py"
    Log "  4a. python scripts/tabpfn_shadow_runner.py --mode live  (defaults to max(d0) in catalog)"
    Log "  4b. python scripts/tabpfn_shadow_backfill_returns.py"
    Log "  4c. python scripts/stop_widening_replay.py  (doc 169 D308/D309 T1 shadow analysis)"
    Log "  5. python scripts/build_microstructure_features_v2.py"
    Log "DryRun complete. Exiting without changes."
    exit 0
}

# Helper: run a step, log status, fail-fast on critical errors
function Invoke-Step {
    param(
        [string]$Name,
        [scriptblock]$Action,
        [bool]$Critical = $true
    )
    Log "--- STEP: $Name ---"
    $stepStart = Get-Date
    # 2026-05-12 fix: removed try/catch wrapper. With $ErrorActionPreference
    # = "Continue" and explicit $LASTEXITCODE check, the only way a step
    # "fails" is non-zero exit code. Python's stderr-INFO no longer triggers
    # PowerShell exceptions. The step output goes to the log file via the
    # 2>&1 redirect.
    & $Action *>&1 | ForEach-Object {
        $line = $_.ToString()
        Add-Content -Path $LogFile -Value "  [${Name}] $line"
    }
    $exitCode = $LASTEXITCODE
    $duration = ((Get-Date) - $stepStart).TotalSeconds
    if ($exitCode -eq 0 -or $null -eq $exitCode) {
        Log "STEP $Name OK ($([int]$duration)s)"
    } elseif ($Critical) {
        Log "STEP $Name FAILED (exit=$exitCode, $([int]$duration)s) - aborting pipeline" "ERROR"
        # doc 269 census defect #3b: every failure path was a bare exit — success-only alerting.
        # Post the failure to Discord before dying so a dead lake pages someone the same evening.
        $wh = [Environment]::GetEnvironmentVariable("OPS_ALERT_WEBHOOK_URL", "Process")
        if ($wh) {
            try {
                $body = @{ content = ":rotating_light: **DataIngest FAILED** at step ``$Name`` (exit=$exitCode). The lake is STALE for tomorrow's shadows. See logs/data_ingest_$Today.log" } | ConvertTo-Json
                Invoke-RestMethod -Uri $wh -Method Post -ContentType "application/json" -Body $body -TimeoutSec 15 | Out-Null
            } catch {
                Log "Failure-alert post failed: $_" "WARN"
            }
        }
        exit $exitCode
    } else {
        Log "STEP $Name FAILED (exit=$exitCode, $([int]$duration)s) - non-critical, continuing" "WARN"
    }
}

# ── Pipeline wall-clock start ──────────────────────────────────────
# doc 209 Bug 6: this timestamp was previously captured at the END of the
# run (just before the report) and the duration was computed against the
# log file's LastAccessTime -- so the Discord "runtime" field always read
# ~0s. Capture it HERE, before STEP 1a, so the reported duration reflects
# the full ingest pipeline.
$_ingest_start_ts = Get-Date

# ── STEP 1a: Pull recent day_aggs from Polygon S3 flat files ───────
# 2026-05-12 fix: previous version called polygon_backfill_all.py with
# wrong args (--tickers-from logs --max 200 don't exist; the script
# only takes --corpus, --years, --max). It also writes to a different
# directory (data/polygon_backfill/) than the warehouse the catalog
# reads from (data/polygon_warehouse/). Switched to polygon_flatfile_pull
# which pulls from Polygon's S3 flat-file API into the right location.
# --days 7 covers a typical 5-day trading week + 2-day weekend buffer
# so re-running mid-week always catches the most recent N days.
Invoke-Step "polygon_flatfile_day_aggs" {
    python scripts/polygon_flatfile_pull.py --dataset day_aggs_v1 --days 7 --workers 4
} -Critical $false

# ── STEP 1b: Pull recent minute_aggs from Polygon S3 flat files ────
# Larger payload (~50-80MB/day) but still fast (~10s for 7 days at 8 workers).
Invoke-Step "polygon_flatfile_minute_aggs" {
    python scripts/polygon_flatfile_pull.py --dataset minute_aggs_v1 --days 7 --workers 8
} -Critical $false

# ── STEP 1c: Convert recent CSV.gz partitions to parquet ───────────
# polygon_parquet_warehouse.py SKIPs partitions that already have
# data.parquet output. To force re-conversion of the current month
# (which now contains the new days' CSVs), delete the current-month
# parquet first. The previous (older) months stay untouched.
$currentYear = (Get-Date).Year
$currentMonth = (Get-Date).Month.ToString("00")
$dayParquet = "data/polygon_warehouse/day_aggs/year=$currentYear/data.parquet"
$minParquet = "data/polygon_warehouse/minute_aggs/year=$currentYear/month=$currentMonth/data.parquet"
if (Test-Path $dayParquet) {
    Log "Removing current-year day_aggs parquet to force rebuild: $dayParquet"
    Remove-Item $dayParquet -Force
}
if (Test-Path $minParquet) {
    Log "Removing current-month minute_aggs parquet to force rebuild: $minParquet"
    Remove-Item $minParquet -Force
}

Invoke-Step "polygon_parquet_warehouse_day" {
    python scripts/polygon_parquet_warehouse.py --dataset day_aggs_v1
} -Critical $true

Invoke-Step "polygon_parquet_warehouse_minute" {
    python scripts/polygon_parquet_warehouse.py --dataset minute_aggs_v1 --year $currentYear
} -Critical $true

# ── STEP 2: Rebuild high_movers_catalog ───────────────────────────
# CRITICAL: must use --source minute (not the default --source day).
# --source day uses close-vs-open from day_aggs and produces ~3,800 rows.
# --source minute uses intraday MFE (high-vs-open) from minute_aggs
# and produces the full ~21,000-row historical catalog the bot was
# trained on. Doc 159 verified this is the right invocation.
Invoke-Step "polygon_high_mover_catalog" {
    python scripts/polygon_high_mover_catalog.py --source minute
} -Critical $true

# ── STEP 3: Rebuild aftermath_catalog + aftermath_strat ────────────
# 2026-05-12 SAFETY HARDENING: previous version overwrote aftermath_strat
# unconditionally, which destroyed the 20K-row file when polygon_backfill
# silently failed (rebuild from stale minute_aggs only produced 116 rows).
#
# New flow:
#   1. Snapshot current aftermath_strat row count BEFORE rebuild
#   2. Run rebuild (writes to aftermath_strat.parquet)
#   3. Compare new row count to snapshot
#   4. If new < 0.95 * old: REVERT (restore from .preingest backup)
#   5. Else: keep (atomic-ish since duckdb writes the file at COPY time)
$strat_path = "data/polygon_warehouse/derived/aftermath_strat.parquet"
$strat_backup = "$strat_path.preingest_$(Get-Date -Format 'yyyyMMddTHHmmss')"

if (Test-Path $strat_path) {
    Copy-Item $strat_path $strat_backup
    $oldRowCount = [int](python scripts/_aftermath_strat_rowcount.py 2>$null)
    Log "Pre-ingest aftermath_strat: $oldRowCount rows (backup at $strat_backup)"
} else {
    $oldRowCount = 0
    Log "No existing aftermath_strat to back up"
}

Invoke-Step "polygon_aftermath_catalog" {
    python scripts/polygon_aftermath_catalog.py
} -Critical $true

if ($oldRowCount -gt 0) {
    $newRowCount = [int](python scripts/_aftermath_strat_rowcount.py 2>$null)
    Log "Post-ingest aftermath_strat: $newRowCount rows (was $oldRowCount)"
    $minAcceptable = [int]($oldRowCount * 0.95)
    if ($newRowCount -lt $minAcceptable) {
        Log "REGRESSION DETECTED: $newRowCount < $minAcceptable (95% of $oldRowCount). Reverting from backup." "ERROR"
        Copy-Item $strat_backup $strat_path -Force
        Log "Reverted aftermath_strat from $strat_backup"
        Log "Pipeline aborted to prevent data loss. Investigate why rebuild produced fewer rows." "ERROR"
        exit 1
    } else {
        Log "Row count OK ($newRowCount >= $minAcceptable). Removing backup."
        Remove-Item $strat_backup -Force
    }
}

# ── STEP 4: Rebuild intraday paths ─────────────────────────────────
Invoke-Step "build_intraday_paths" {
    python scripts/build_intraday_paths.py
} -Critical $false

# ── STEP 4a: TabPFN shadow runner (LIVE mode) — doc 163 Gate 4 ─────
# Score TabPFN against today's d0 candidates (NULL ret_t5 at scoring
# time -- realized returns filled in by step 4b on a future run, ~5
# trading days later).
#
# Requires TABPFN_TOKEN in env (loaded from secrets at top of script).
# TABPFN_NO_BROWSER=1 prevents the client from trying to open a browser
# for auth on a headless task-scheduler run.
#
# Non-critical: shadow data is observational only (does not touch the
# trading hot path). Failure here delays D293.8 ensemble re-test by a
# day but does not break tomorrow's bot.
$env:TABPFN_NO_BROWSER = "1"
if ($env:TABPFN_TOKEN) {
    # 2026-05-13 followup fix: drop --date $Today. At 17:30 ET the
    # catalog's max(d0) = yesterday because Polygon's day_aggs flat file
    # for today isn't published until ~04:00 ET tomorrow. The runner
    # in --mode live now defaults to "score max(d0) in catalog", which
    # gives us the freshest scoreable date (typically T-1).
    Invoke-Step "tabpfn_shadow_runner_live" {
        python scripts/tabpfn_shadow_runner.py --mode live
    } -Critical $false
} else {
    Log "STEP tabpfn_shadow_runner_live SKIPPED (TABPFN_TOKEN missing from secrets)" "WARN"
}

# ── STEP 4b: TabPFN shadow backfill returns — doc 163 Gate 4 ───────
# Walk every shadow parquet; fill realized_ret_t5 in stale rows whose
# (ticker, d0) is now labeled in aftermath_strat. Idempotent. Strictly
# advances NaN -> finite; never overwrites an existing value.
#
# Non-critical: failure delays propagation of new labels into shadow
# files by one day. Next run picks them up.
Invoke-Step "tabpfn_shadow_backfill_returns" {
    python scripts/tabpfn_shadow_backfill_returns.py
} -Critical $false

# ── STEP 4c: Stop-widening replayer — doc 169 D308/D309 T1 ─────────
# Walk every stop_decisions_*.jsonl in data/shadow_stops/, replay each
# stop-decision event against minute_aggs to compute hypothetical
# wide-ATR-stop P&L vs the actual phase1-tight-stop P&L. Output:
# data/shadow_stops/replay_results.parquet (overwritten each night
# with the latest cumulative result -- fast to recompute).
#
# This is T1 of the stop-widening validation plan: zero production
# behavior change, just observational data collection. After ~3 trading
# days of clean shadow data we decide whether to flip to T2 A/B split.
#
# Non-critical: failure delays one day of shadow analysis. Next run
# picks up everything since live emission started.
Invoke-Step "stop_widening_replayer" {
    python scripts/stop_widening_replay.py
} -Critical $false

# ── STEP 5: SKIPPED by default — microstructure features ──────────
# 2026-05-12: build_microstructure_features_v2.py scans the 451GB
# trades_v1 lake. On the test box it took 20+ minutes and consumed
# ~32GB RAM (two python processes at 16GB each via DuckDB), with no
# visible progress logging. The output is non-critical for daily
# aftermath_strat freshness -- the bot doesn't use microstructure
# features in its hot trading path; only research scripts do.
#
# Run separately when needed (weekly is plenty):
#   python scripts/build_microstructure_features_v2.py
#
# To re-enable in this launcher, uncomment the block below + ensure
# the system has sufficient idle RAM (>=32GB free) and time budget.
#
# Invoke-Step "build_microstructure_features_v2" {
#     python scripts/build_microstructure_features_v2.py
# } -Critical $false
Log "STEP build_microstructure_features_v2 SKIPPED (run separately as needed)"

# ── Final: report aftermath_strat freshness + Discord notify ───────
# doc 209 Bug 6: max_d0 is a pandas Timestamp -- it renders as
# "2026-05-28 00:00:00". That embedded space broke the old `(\S+),` regex
# (it stopped at the space and never reached the comma), so EVERY embed
# showed "max d0 ?" / "rows ?". Slice to YYYY-MM-DD so the value is a
# single whitespace-free token the regex can capture.
# doc 269 census defect #3: the f-string + nested-quote escaping threw SyntaxError on EVERY scheduled run
# since 5/13 (the embed carried PARSE_FAILED for ~4 weeks). Rewritten with .format and single-quote-free SQL.
$report = python -c "
import duckdb
con = duckdb.connect()
q = 'SELECT MAX(d0) as max_d0, COUNT(*) as n FROM read_parquet(' + chr(39) + 'data/polygon_warehouse/derived/aftermath_strat.parquet' + chr(39) + ')'
df = con.execute(q).fetchdf()
print('aftermath_strat max_d0={}, total_rows={}'.format(str(df['max_d0'].iloc[0])[:10], int(df['n'].iloc[0])))
" 2>&1
$_report_exit = $LASTEXITCODE
# Flatten any multi-line / array output (2>&1 can interleave duckdb stderr)
# into a single scalar string. -match only populates $matches for a SCALAR
# LHS; against an array it acts as a filter and leaves $matches stale --
# another way the captures silently came back empty.
$_report_text = ($report | Out-String).Trim()
Log "Post-ingest report: $_report_text"

# 2026-05-12: Discord notification on success. Operator sees confirmation
# of fresh data lake before next morning's bot start.
$_ops_webhook = [Environment]::GetEnvironmentVariable("OPS_ALERT_WEBHOOK_URL", "Process")
if ($_ops_webhook) {
    # doc 209 Bug 6: real wall-clock duration from the pipeline-start
    # timestamp captured before STEP 1a. Integer seconds avoids any locale
    # decimal-separator surprise when interpolated into float('...') below.
    $_ingest_duration = [int](((Get-Date) - $_ingest_start_ts).TotalSeconds)
    # Parse the report line to extract max_d0 and rows. On query failure
    # (non-zero exit) or a format mismatch, pass an explicit sentinel --
    # NOT a silent empty/None -- so the operator can distinguish "lake
    # refreshed but the freshness report didn't parse" from a genuine "?".
    $_max_d0 = ""
    $_rows = ""
    if ($_report_exit -eq 0 -and $_report_text -match "max_d0=(\S+),\s*total_rows=(\d+)") {
        $_max_d0 = $matches[1]
        $_rows = $matches[2]
    } else {
        $_max_d0 = "PARSE_FAILED"
        Log "Could not parse aftermath_strat report (python exit=$_report_exit, text='$_report_text')" "WARN"
    }
    Push-Location $ProjectRoot
    & python -c "
import asyncio, sys
sys.path.insert(0, '.')
from src.monitoring.alerts import alert_data_ingest_result
asyncio.run(alert_data_ingest_result(
    success=True,
    aftermath_strat_max_d0='$_max_d0',
    rows=int('$_rows') if '$_rows' else None,
    duration_sec=float('$_ingest_duration'),
    webhook_url='$_ops_webhook',
))
print('Discord ingest-success alert sent')
" 2>> $LogFile | ForEach-Object { Log "  [discord] $_" }
    Pop-Location
}

Log "==================================================="
Log "DAILY DATA INGEST COMPLETE - $Today"
Log "==================================================="
exit 0
