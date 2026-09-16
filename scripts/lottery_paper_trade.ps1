<#
.SYNOPSIS
    Lottery Runner - paper deployment of the freshness-tilted lottery (doc 90).

.DESCRIPTION
    Standalone launcher for scripts/lottery_runner.py.
    Designed for Windows Task Scheduler, runs at ~09:00 ET on weekdays.
    The script self-paces and waits for 09:25 ET, then 09:30 ET (open).

    Coexists with the main bot launcher (daily_paper_trade.ps1) - different
    log files, different state directories, both safe.

    Schedule with Task Scheduler:
      Trigger:   Daily at 9:00 AM ET (Mon-Fri)
      Action:    powershell.exe -NoProfile -ExecutionPolicy Bypass -File
                 "%REPO_ROOT%\scripts\lottery_paper_trade.ps1"
      Run as:    the operator (NOT elevated - same as daily_paper_trade.ps1)

.PARAMETER SecretsFile
    Path to secrets .env file. Default: $HOME\momentum-x-secrets.env

.PARAMETER DryRun
    If $true, sets LOTTERY_DRY_RUN=1 (logs intended orders without submitting).
#>

param(
    [string]$SecretsFile = "$env:USERPROFILE\momentum-x-secrets.env",
    [switch]$DryRun
)

$_transcript = Join-Path (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)) "logs\lottery_transcript_$(Get-Date -Format 'yyyy-MM-dd').log"
try { Start-Transcript -Path $_transcript -Append -Force } catch {}

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ExpectedRoot = Join-Path $env:USERPROFILE "Documents\GitHub\momentum-x"
if ($ProjectRoot -ne $ExpectedRoot) {
    Write-Error "FATAL: Running from '$ProjectRoot', expected '$ExpectedRoot'."
    exit 1
}

$LogDir = Join-Path $ProjectRoot "logs"
$Today = Get-Date -Format "yyyy-MM-dd"
$LogFile = Join-Path $LogDir "lottery_launcher_${Today}.log"

if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

function Log {
    param([string]$Message, [string]$Level = "INFO")
    $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $line = "[$ts] [$Level] $Message"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line
}

# Skip weekends
$dayOfWeek = (Get-Date).DayOfWeek
if ($dayOfWeek -eq "Saturday" -or $dayOfWeek -eq "Sunday") {
    Log "Skipping - $dayOfWeek (market closed)" "SKIP"
    exit 0
}

Log "=== LOTTERY LAUNCHER START ==="
Log "ProjectRoot: $ProjectRoot"
Log "DryRun: $DryRun"

# Load secrets
if (-not (Test-Path $SecretsFile)) {
    Log "Secrets file not found: $SecretsFile" "ERROR"
    exit 1
}
Log "Loading secrets from $SecretsFile"
Get-Content $SecretsFile | ForEach-Object {
    if ($_ -match "^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$") {
        $key = $matches[1]
        $val = $matches[2].Trim('"').Trim("'")
        [Environment]::SetEnvironmentVariable($key, $val, "Process")
    }
}

# Halt-switch check (separate from main bot's MOMENTUM_HALT_NEW_ENTRIES)
$lotteryHalt = [Environment]::GetEnvironmentVariable("MOMENTUM_LOTTERY_HALT", "User")
if ($lotteryHalt -eq "1") {
    Log "MOMENTUM_LOTTERY_HALT=1 (User scope) - lottery will run in observation mode" "WARN"
    [Environment]::SetEnvironmentVariable("MOMENTUM_LOTTERY_HALT", "1", "Process")
}

if ($DryRun) {
    Log "DRY RUN mode - orders will be logged but NOT submitted" "WARN"
    [Environment]::SetEnvironmentVariable("LOTTERY_DRY_RUN", "1", "Process")
}

# Set conservative defaults if not already set
if (-not [Environment]::GetEnvironmentVariable("LOTTERY_NOTIONAL_USD", "Process")) {
    [Environment]::SetEnvironmentVariable("LOTTERY_NOTIONAL_USD", "250", "Process")
}
if (-not [Environment]::GetEnvironmentVariable("LOTTERY_MAX_TICKERS", "Process")) {
    [Environment]::SetEnvironmentVariable("LOTTERY_MAX_TICKERS", "10", "Process")
}
if (-not [Environment]::GetEnvironmentVariable("LOTTERY_TRAIL_PCT", "Process")) {
    [Environment]::SetEnvironmentVariable("LOTTERY_TRAIL_PCT", "15.0", "Process")
}

# Session-115 / 116 defaults: meta-scorer + aggressive Kelly + intraday refresh.
# These can be overridden in the secrets file or env. Default is FULL-SEND
# (paper trading only). Set LOTTERY_USE_META_SCORER=0 to disable.
if (-not [Environment]::GetEnvironmentVariable("LOTTERY_USE_META_SCORER", "Process")) {
    [Environment]::SetEnvironmentVariable("LOTTERY_USE_META_SCORER", "1", "Process")
}
if (-not [Environment]::GetEnvironmentVariable("LOTTERY_META_BANKROLL_USD", "Process")) {
    [Environment]::SetEnvironmentVariable("LOTTERY_META_BANKROLL_USD", "10000", "Process")
}
if (-not [Environment]::GetEnvironmentVariable("LOTTERY_AGGRESSIVE_KELLY", "Process")) {
    [Environment]::SetEnvironmentVariable("LOTTERY_AGGRESSIVE_KELLY", "1", "Process")
}
# D280 (2026-05-05): use 100% of broker equity for Kelly sizing instead of
# the legacy $10k fixed bankroll. Pre-D280: ELITE pick was 50% × $10k = $5k
# = 3.5% of a $140k paper account. With LOTTERY_BANKROLL_PCT=1.0 ELITE
# now = 50% × $140k = $70k. Set to 0.5 for half-account ramp-up; set to 0
# to opt back into legacy fixed sizing (LOTTERY_META_BANKROLL_USD).
if (-not [Environment]::GetEnvironmentVariable("LOTTERY_BANKROLL_PCT", "Process")) {
    [Environment]::SetEnvironmentVariable("LOTTERY_BANKROLL_PCT", "1.0", "Process")
}
# D285 (2026-05-07, doc 142 Bouchaud capacity finding): cap per-pick
# notional at LOTTERY_MAX_PARTICIPATION_PCT × dvol_d0. The Bouchaud
# square-root impact law (Toth-Eisler-Bouchaud PRX 2011) says position
# impact scales as Y · √(participation) · σ_daily.
#
# Doc 142 capacity analysis with Y=1.5 (microcap-realistic) and current
# Aggressive Kelly (50/35/20/10):
#   At $140K AUM (current paper):  ELITE position $70K, median dvol $8.6M
#                                    -> 0.8% participation, ~3% impact, alpha intact
#   At $1M AUM (next scaling):      ELITE position $500K
#                                    -> 6% participation, ~14% impact, +5.7% net (barely)
#   At $5M AUM (M.md reference):    ELITE position $2.5M
#                                    -> 30% participation, ~21% impact, -0.9% net (alpha dead)
#
# With LOTTERY_MAX_PARTICIPATION_PCT=0.05 cap:
#   At $5M AUM:  ELITE position capped at 5% × $8.6M = $430K
#                -> 5% participation, ~7% impact, +12.6% net (alpha intact)
#
# At current $140K paper account this guard rarely binds (most positions
# already <0.5% of dvol_d0). It's defense for future scaling. Set to 1.0
# to disable (NOT RECOMMENDED above $1M AUM).
if (-not [Environment]::GetEnvironmentVariable("LOTTERY_MAX_PARTICIPATION_PCT", "Process")) {
    [Environment]::SetEnvironmentVariable("LOTTERY_MAX_PARTICIPATION_PCT", "0.05", "Process")
}
# D290 (2026-05-11, doc 149 adaptive Kelly): per-tier Kelly caps that
# shrink as AUM grows. Doc 142 + doc 147 found that fixed Aggressive
# Kelly (50/35/20/10) produces NEGATIVE expected $-PNL above ~$500K AUM
# because per-pick impact dominates per-pick mean return for tiers
# with smaller mean (BROAD especially).
#
# Doc 149 capacity-aware optimization (maximize per-pick $-PNL =
# position × E[mean_ret - 2·Y·sqrt(participation)·sigma_d]) on recent
# OOS data:
#   AUM       ELITE    HIGH    VETOED   BROAD    Daily $-PNL lift vs fixed
#   $100K     50.0%    35.0%   20.0%    10.0%    +0% (no change at paper)
#   $250K     50.0%    35.0%   20.0%     4.0%    +9.9%
#   $500K     50.0%    35.0%   20.0%     2.0%    +60.4%
#   $1M       27.5%    35.0%   11.0%     1.0%    fixed-Kelly goes NEGATIVE
#   $2M       14.0%    35.0%    5.5%     0.5%    fixed-Kelly $-32K/day loss
#   $5M        5.5%    19.5%    2.0%     0.5%    fixed-Kelly $-177K/day loss
#   $10M       3.0%    10.0%    1.0%     0.5%    fixed-Kelly $-522K/day loss
#
# At current paper account ($140K), behavior is IDENTICAL to fixed
# Aggressive Kelly. As AUM grows (live deployment), automatically
# reduces tier caps to keep per-pick $-PNL positive.
#
# Pre-commit verdict: ship if $-PNL improves >=10% at $1M+ AUM AND
# no degradation at $140K. Both met.
#
# To opt out (NOT RECOMMENDED above $500K AUM): LOTTERY_USE_ADAPTIVE_KELLY=0
if (-not [Environment]::GetEnvironmentVariable("LOTTERY_USE_ADAPTIVE_KELLY", "Process")) {
    [Environment]::SetEnvironmentVariable("LOTTERY_USE_ADAPTIVE_KELLY", "1", "Process")
}
# D286 (2026-05-11, doc 144 four-experiments synthesis): TabPFN shadow-mode
# logging. Per Exp #4 pre-commit (TabPFN top-decile Sharpe = 1.39x v3 cascade
# Sharpe, exceeds the 1.3x threshold), skip the Phase 1 paper-trade
# observation step and go straight to shadow-mode logging in production.
#
# Shadow mode runs ALONGSIDE production (separate script tabpfn_shadow_runner.py):
#   - After daily picks fire, fits TabPFN on the most-recent 120-day v3
#     training window, predicts on today's candidates
#   - Writes data/.../tabpfn_shadow/<YYYY-MM-DD>.parquet with TabPFN
#     predictions per candidate + per-day quintile rank
#   - Does NOT modify v3's trading decisions; pure observational
#
# After 2 weeks of shadow data, doc 144 Exp #3's defensive-overlay finding
# (skip v3 picks where TabPFN below top quintile -> +5.87pp lift per pick)
# can be empirically validated on live data. If confirmed, enable
# LOTTERY_TABPFN_DEFENSIVE_OVERLAY=1 in a future launcher commit.
#
# Requires: TABPFN_TOKEN secret in $SecretsFile (priorlabs.ai API key,
# loaded via the Get-Content secrets parser earlier in this script).
#
# To disable: LOTTERY_TABPFN_SHADOW=0
if (-not [Environment]::GetEnvironmentVariable("LOTTERY_TABPFN_SHADOW", "Process")) {
    [Environment]::SetEnvironmentVariable("LOTTERY_TABPFN_SHADOW", "1", "Process")
}
# D286 default: defensive overlay OFF until 2 weeks of shadow data
# validates the OOS pattern. Doc 144 Exp #3 measured +5.87pp lift on
# OOS data; need live shadow data to confirm before flipping.
if (-not [Environment]::GetEnvironmentVariable("LOTTERY_TABPFN_DEFENSIVE_OVERLAY", "Process")) {
    [Environment]::SetEnvironmentVariable("LOTTERY_TABPFN_DEFENSIVE_OVERLAY", "0", "Process")
}
# Suppress TabPFN's interactive browser auth (script context only)
if (-not [Environment]::GetEnvironmentVariable("TABPFN_NO_BROWSER", "Process")) {
    [Environment]::SetEnvironmentVariable("TABPFN_NO_BROWSER", "1", "Process")
}
# Session-122 update: VETOED rule D is TCN-free (s115 ablation: +13.37%/trade
# vs production rule E's +9.07%, +47% lift). Switch to D + disable TCN-based
# intraday refresh.
if (-not [Environment]::GetEnvironmentVariable("MX_VETOED_RULE", "Process")) {
    [Environment]::SetEnvironmentVariable("MX_VETOED_RULE", "D", "Process")
}
# Intraday refresh OFF: rule D doesn't need TCN, so 10:00 ET re-evaluation
# adds compute cost without P&L lift. Set to 1 only if testing rule E.
if (-not [Environment]::GetEnvironmentVariable("LOTTERY_USE_INTRADAY_REFRESH", "Process")) {
    [Environment]::SetEnvironmentVariable("LOTTERY_USE_INTRADAY_REFRESH", "0", "Process")
}
if (-not [Environment]::GetEnvironmentVariable("LOTTERY_INTRADAY_REFRESH_HOUR", "Process")) {
    [Environment]::SetEnvironmentVariable("LOTTERY_INTRADAY_REFRESH_HOUR", "10", "Process")
}
if (-not [Environment]::GetEnvironmentVariable("LOTTERY_INTRADAY_REFRESH_MIN", "Process")) {
    [Environment]::SetEnvironmentVariable("LOTTERY_INTRADAY_REFRESH_MIN", "0", "Process")
}
# Suppress legacy gates when meta-scorer is on (it absorbs both)
if (-not [Environment]::GetEnvironmentVariable("LOTTERY_USE_ML_MODEL", "Process")) {
    [Environment]::SetEnvironmentVariable("LOTTERY_USE_ML_MODEL", "0", "Process")
}
if (-not [Environment]::GetEnvironmentVariable("LOTTERY_USE_ISING_GATE", "Process")) {
    [Environment]::SetEnvironmentVariable("LOTTERY_USE_ISING_GATE", "0", "Process")
}
# D279 (2026-05-05): Switch to SIP feed for prev-day-bar fetch in
# get_prev_day_bar. The default IEX feed returns sparse data for microcaps
# pre-market — Tuesday 2026-05-05 09:25 ET produced "0/10 tickers" because
# IEX had no completed bars for the candidate cohort. SIP returns full
# coverage (~120x richer volume on the same tickers) and the account is
# entitled. Override only if SIP entitlement is revoked.
if (-not [Environment]::GetEnvironmentVariable("ALPACA_DATA_FEED", "Process")) {
    [Environment]::SetEnvironmentVariable("ALPACA_DATA_FEED", "sip", "Process")
}
# D281 (2026-05-05): s125 hybrid ELITE router (v3-default override path).
# CONSERVATIVE: DISABLED post-D284 (doc 138) ELITE DSR finding.
#
# Doc 138 (ml_v6_phase1_executive_decisions.py Item 2) ran standalone
# DSR on the v3 ELITE specialist's top-3/day picks across 320 OOS days:
#   Per-day Sharpe: +0.105   Annualized: +1.67   T = 320 days
#   DSR @ N=10:  0.645   (passes 0.5 if literally 10 trials)
#   DSR @ N=50:  0.371   (FAILS 0.5 gate)         <-- decision threshold
#   DSR @ N=100: 0.279   (clearly fails)
# At conservative N=50 (D281 grid + s125 + v4 + v5 ELITE configs we've
# tried this year), the apparent ELITE edge is statistically
# indistinguishable from the maximum lift you'd expect from grid-
# searching ~50 random strategies. M.md §3 third-option threshold for
# "honest acceptance via DSR that ELITE is statistical noise" triggered.
#
# Top-5/day and top-10/day ELITE selections were *negative* Sharpe
# (-0.114 and -1.059 annualized) — only the most aggressive top-3
# selection had even an apparent positive Sharpe, and that didn't
# survive multiple-testing correction.
#
# This flag flip removes the v3-default override path (which would
# upgrade borderline candidates to ELITE). The ELITE tier itself
# still exists in assign_tier() — that deeper refactor (route ELITE
# -> HIGH-tier sizing) is filed for doc 139.
#
# To re-enable for an A/B paper-trading test only: MX_HYBRID_ELITE=1.
if (-not [Environment]::GetEnvironmentVariable("MX_HYBRID_ELITE", "Process")) {
    [Environment]::SetEnvironmentVariable("MX_HYBRID_ELITE", "0", "Process")
}
# D281 (2026-05-05): cohort-specialized cascade with 4 XGBoost tier
# specialists. CONSERVATIVE: DISABLED post-D283 (v6 Phase 0) finding.
#
# D281's thresholds (ELITE 0.40, HIGH 0.40, VETOED 0.40, BROAD 0.50)
# were selected via the SAME 600-combo grid search the v4 MoMTrans
# cascade used. Phase 0 DSR with N=600 effective trials shows that
# kind of grid-search inflation puts the apparent +$1,104 lift in the
# H0 noise band.
#
# Until v6 hygiene (CPCV-validated thresholds, no in-fold optimization)
# is in place, D281 stays DISABLED. The 4 specialist .pkl files remain
# on disk; flipping this back to "1" re-enables the cascade.
if (-not [Environment]::GetEnvironmentVariable("MX_TIERED_LEARNING", "Process")) {
    [Environment]::SetEnvironmentVariable("MX_TIERED_LEARNING", "0", "Process")
}
# D282 (2026-05-06): MoMTrans v4 multi-task tabular transformer cascade.
# CONSERVATIVE: DISABLED post-D283 (v6 Phase 0) validation finding.
#
# Phase 0 validation (docs/research-log/132 → 133) ran the López de Prado
# framework on v4's predictions and found:
#   - DSR = 0.0471 with N=600 effective trials (the threshold grid)
#     → the apparent +$46k WF lift is in the H0 noise band
#   - PBO = 0.278 (threshold selection IS predictive — partial defense)
#   - 100%-neutralized Spearman = 0.091 (down from 0.136 raw, -33%)
#     → most of the cascade's edge was sector/cap exposure, not novel signal
# Production v3 survived all 3 gates (DSR=1.0, neut-ρ=0.076).
#
# Until v6 Phase 1 (microstructure features) is built and validated under
# proper CPCV + DSR + Numerai-neutralization hygiene, MoMTrans v4 stays
# DISABLED. The .pt files remain on disk; flipping this back to "1" plus
# any threshold env vars re-enables the cascade.
#
# Default OFF. To force re-enable for an A/B paper-test, set
# MX_USE_MOMTRANS=1 before launch.
if (-not [Environment]::GetEnvironmentVariable("MX_USE_MOMTRANS", "Process")) {
    [Environment]::SetEnvironmentVariable("MX_USE_MOMTRANS", "0", "Process")
}

Log "LOTTERY_NOTIONAL_USD:        $([Environment]::GetEnvironmentVariable('LOTTERY_NOTIONAL_USD', 'Process'))"
Log "LOTTERY_MAX_TICKERS:         $([Environment]::GetEnvironmentVariable('LOTTERY_MAX_TICKERS', 'Process'))"
Log "LOTTERY_TRAIL_PCT:           $([Environment]::GetEnvironmentVariable('LOTTERY_TRAIL_PCT', 'Process'))"
Log "LOTTERY_DRY_RUN:             $([Environment]::GetEnvironmentVariable('LOTTERY_DRY_RUN', 'Process'))"
Log "LOTTERY_USE_META_SCORER:     $([Environment]::GetEnvironmentVariable('LOTTERY_USE_META_SCORER', 'Process'))"
Log "LOTTERY_META_BANKROLL_USD:   $([Environment]::GetEnvironmentVariable('LOTTERY_META_BANKROLL_USD', 'Process'))"
Log "LOTTERY_BANKROLL_PCT:        $([Environment]::GetEnvironmentVariable('LOTTERY_BANKROLL_PCT', 'Process'))"
Log "LOTTERY_AGGRESSIVE_KELLY:    $([Environment]::GetEnvironmentVariable('LOTTERY_AGGRESSIVE_KELLY', 'Process'))"
Log "LOTTERY_USE_INTRADAY_REFRESH: $([Environment]::GetEnvironmentVariable('LOTTERY_USE_INTRADAY_REFRESH', 'Process'))"
Log "MX_VETOED_RULE:              $([Environment]::GetEnvironmentVariable('MX_VETOED_RULE', 'Process'))"
Log "ALPACA_DATA_FEED:            $([Environment]::GetEnvironmentVariable('ALPACA_DATA_FEED', 'Process'))"
Log "MX_HYBRID_ELITE:             $([Environment]::GetEnvironmentVariable('MX_HYBRID_ELITE', 'Process'))"
Log "MX_TIERED_LEARNING:          $([Environment]::GetEnvironmentVariable('MX_TIERED_LEARNING', 'Process'))"
Log "MX_USE_MOMTRANS:             $([Environment]::GetEnvironmentVariable('MX_USE_MOMTRANS', 'Process'))"

# Verify python
$pythonPath = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $pythonPath) {
    Log "python not on PATH" "ERROR"
    exit 1
}
Log "Python: $pythonPath"

# Verify httpx import (avoid 2>&1 — PS 5.1 wraps stderr lines as ErrorRecords)
& python -c "import httpx" | Out-Null
if ($LASTEXITCODE -ne 0) {
    Log "httpx import failed (LASTEXITCODE=$LASTEXITCODE)" "ERROR"
    exit 1
}
Log "httpx import: OK"

# Run the lottery runner
$runner = Join-Path $ProjectRoot "scripts\lottery_runner.py"
Log "Starting: python -u $runner"

Set-Location $ProjectRoot
try {
    # Avoid 2>&1 (PS 5.1 wraps stderr as ErrorRecord). The runner's own logger
    # writes to logs/lottery_YYYY-MM-DD.log; this launcher captures stdout only.
    & python -u $runner | ForEach-Object { Add-Content -Path $LogFile -Value $_ ; Write-Host $_ }
    $exitCode = $LASTEXITCODE
    Log "Lottery runner exited with code $exitCode"
} catch {
    Log "Lottery runner threw: $_" "ERROR"
    exit 1
}

# D286: TabPFN shadow-mode runner — separate process, runs AFTER main lottery
# completes. Failures here do NOT affect production (shadow data is observational
# only). Requires TABPFN_TOKEN to be set (loaded from secrets file above).
$tabpfnShadow = [Environment]::GetEnvironmentVariable("LOTTERY_TABPFN_SHADOW", "Process")
$tabpfnToken = [Environment]::GetEnvironmentVariable("TABPFN_TOKEN", "Process")
if ($tabpfnShadow -eq "1" -and $tabpfnToken) {
    $shadowRunner = Join-Path $ProjectRoot "scripts\tabpfn_shadow_runner.py"
    if (Test-Path $shadowRunner) {
        Log "Starting TabPFN shadow runner (D286)..."
        try {
            & python -u $shadowRunner --date $Today | ForEach-Object { Add-Content -Path $LogFile -Value "  [shadow] $_" ; Write-Host "  [shadow] $_" }
            $shadowExit = $LASTEXITCODE
            Log "TabPFN shadow runner exited with code $shadowExit"
        } catch {
            Log "TabPFN shadow runner failed: $_ (non-fatal, production unaffected)" "WARN"
        }
    } else {
        Log "TabPFN shadow runner script missing: $shadowRunner" "WARN"
    }
} elseif ($tabpfnShadow -eq "1") {
    Log "LOTTERY_TABPFN_SHADOW=1 but TABPFN_TOKEN not set in secrets; skipping shadow run" "WARN"
} else {
    Log "TabPFN shadow disabled (LOTTERY_TABPFN_SHADOW != 1)"
}

Log "=== LOTTERY LAUNCHER END ==="
try { Stop-Transcript } catch {}
exit $exitCode
