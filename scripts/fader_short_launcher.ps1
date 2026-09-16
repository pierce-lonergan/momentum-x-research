<#
.SYNOPSIS
    Fader Short Runner launcher (chronic-fader short strategy, doc 101).

.DESCRIPTION
    Standalone launcher for scripts/fader_short_runner.py.
    Designed for Windows Task Scheduler, runs at 15:50 ET on weekdays
    (10 minutes before close, before-close entry).

    Coexists with daily_paper_trade.ps1 + lottery_paper_trade.ps1:
    - main bot at 04:30 ET (currently halted)
    - lottery at 09:00 ET (long lottery)
    - fader-short at 15:50 ET (this script)

    Three independent processes; shared Alpaca paper account.

.PARAMETER SecretsFile
    Path to secrets .env file. Default: $HOME\momentum-x-secrets.env

.PARAMETER DryRun
    If $true, sets FADER_SHORT_DRY_RUN=1.

.PARAMETER Variant
    S1, S2, or S3. Default S3 (most restrictive, +5.55%/trade).
#>

param(
    [string]$SecretsFile = "$env:USERPROFILE\momentum-x-secrets.env",
    [switch]$DryRun,
    [string]$Variant = "S3"
)

$_transcript = Join-Path (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)) "logs\fader_short_transcript_$(Get-Date -Format 'yyyy-MM-dd').log"
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
$LogFile = Join-Path $LogDir "fader_short_launcher_${Today}.log"

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

Log "=== FADER SHORT LAUNCHER START ==="
Log "ProjectRoot: $ProjectRoot"
Log "Variant: $Variant"
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

# Halt switch (separate from lottery + main bot)
$faderHalt = [Environment]::GetEnvironmentVariable("MOMENTUM_FADER_SHORT_HALT", "User")
if ($faderHalt -eq "1") {
    Log "MOMENTUM_FADER_SHORT_HALT=1 (User scope) - observation only" "WARN"
    [Environment]::SetEnvironmentVariable("MOMENTUM_FADER_SHORT_HALT", "1", "Process")
}

if ($DryRun) {
    Log "DRY RUN mode" "WARN"
    [Environment]::SetEnvironmentVariable("FADER_SHORT_DRY_RUN", "1", "Process")
}

# Set conservative defaults if not already set (User scope can override)
$defaults = @{
    "FADER_SHORT_NOTIONAL_USD" = "250"
    "FADER_SHORT_MAX_TICKERS"  = "5"
    "FADER_SHORT_TARGET_PCT"   = "10"
    "FADER_SHORT_STOP_PCT"     = "10"
    "FADER_SHORT_HOLD_DAYS"    = "5"
    "FADER_SHORT_MIN_DVOL"     = "5000000"
}
foreach ($k in $defaults.Keys) {
    if (-not [Environment]::GetEnvironmentVariable($k, "Process")) {
        [Environment]::SetEnvironmentVariable($k, $defaults[$k], "Process")
    }
    Log "$k = $([Environment]::GetEnvironmentVariable($k, 'Process'))"
}

# Verify python + httpx
$pythonPath = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $pythonPath) { Log "python not on PATH" "ERROR" ; exit 1 }
Log "Python: $pythonPath"
& python -c "import httpx; import duckdb" | Out-Null
if ($LASTEXITCODE -ne 0) { Log "httpx or duckdb import failed" "ERROR" ; exit 1 }
Log "Imports OK"

# Run the runner
$runner = Join-Path $ProjectRoot "scripts\fader_short_runner.py"
Log "Starting: python -u $runner --variant $Variant"
Set-Location $ProjectRoot
try {
    & python -u $runner --variant $Variant | ForEach-Object { Add-Content -Path $LogFile -Value $_ ; Write-Host $_ }
    $exitCode = $LASTEXITCODE
    Log "Fader short runner exited with code $exitCode"
} catch {
    Log "Fader short runner threw: $_" "ERROR"
    exit 1
}

Log "=== FADER SHORT LAUNCHER END ==="
try { Stop-Transcript } catch {}
exit $exitCode
