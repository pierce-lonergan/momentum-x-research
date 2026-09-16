<#
.SYNOPSIS
    Momentum-X Phase 3 Runner - loads secrets from external file, runs commands.

.DESCRIPTION
    This script:
    1. Loads API keys from an external secrets file (outside the git repo)
    2. Copies them into the project as .env (gitignored)
    3. Runs the requested Momentum-X command
    4. Keeps your secrets safe and out of version control

.PARAMETER SecretsFile
    Path to your secrets .env file. Default: $HOME\momentum-x-secrets.env

.EXAMPLE
    .\scripts\run_phase3.ps1 build-scenarios
    .\scripts\run_phase3.ps1 record-scenarios --max-scenarios 10
    .\scripts\run_phase3.ps1 backtest
    .\scripts\run_phase3.ps1 -SecretsFile "D:\keys\my-secrets.env" build-scenarios
#>

param(
    [string]$SecretsFile = "$env:USERPROFILE\momentum-x-secrets.env",
    [Parameter(Position=0, ValueFromRemainingArguments=$true)]
    [string[]]$Arguments
)

$ErrorActionPreference = "Stop"

# ── Banner ──
Write-Host ""
Write-Host "  ======================================================" -ForegroundColor Cyan
Write-Host "           MOMENTUM-X  Phase 3 Runner                   " -ForegroundColor Cyan
Write-Host "  ======================================================" -ForegroundColor Cyan
Write-Host ""

# ── Resolve project root ──
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$EnvTarget = Join-Path $ProjectRoot ".env"
$TemplatePath = Join-Path $ProjectRoot ".env.example"

# ── Check if secrets file exists ──
if (-not (Test-Path $SecretsFile)) {
    Write-Host "  [!] Secrets file not found: $SecretsFile" -ForegroundColor Yellow
    Write-Host ""

    # Copy from .env.example as template
    if (Test-Path $TemplatePath) {
        Copy-Item -Path $TemplatePath -Destination $SecretsFile
        Write-Host "  Created template from .env.example" -ForegroundColor Green
    } else {
        # Write template line by line to avoid heredoc quoting issues
        $lines = @(
            "# MOMENTUM-X SECRETS FILE",
            "# Keep this file OUTSIDE your git repo. Never commit API keys.",
            "",
            "# -- Alpaca Paper Trading --",
            "# Get these from: https://app.alpaca.markets -> Paper Trading -> API Keys",
            "ALPACA_API_KEY=PKXXXXXXXXXXXXXXXXXX",
            "ALPACA_SECRET_KEY=XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
            "ALPACA_BASE_URL=https://paper-api.alpaca.markets",
            "ALPACA_DATA_URL=https://data.alpaca.markets",
            "ALPACA_FEED=iex",
            "",
            "# -- Together AI (LLM Provider) --",
            "# Get this from: https://api.together.ai -> API Keys",
            "TOGETHER_AI_API_KEY=XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX",
            "LLM_TIER1_MODEL=moonshotai/Kimi-K2-Thinking",
            "LLM_TIER1_PROVIDER=together_ai",
            "LLM_TIER2_MODEL=moonshotai/Kimi-K2.5",
            "LLM_TIER2_PROVIDER=together_ai",
            "",
            "# -- Optional: Finnhub News --",
            "# FINNHUB_API_KEY=XXXXXXXXXXXXXXXXXXXXXXXX"
        )
        Set-Content -Path $SecretsFile -Value ($lines -join "`n") -Encoding UTF8
        Write-Host "  Created template from scratch" -ForegroundColor Green
    }

    Write-Host "  Template saved to: $SecretsFile" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Next steps:" -ForegroundColor White
    Write-Host "    1. Open $SecretsFile in a text editor" -ForegroundColor White
    Write-Host "    2. Replace the XXXX placeholders with your real API keys" -ForegroundColor White
    Write-Host "    3. Re-run this script" -ForegroundColor White
    Write-Host ""
    exit 0
}

Write-Host "  Secrets file: $SecretsFile" -ForegroundColor Gray

# ── Validate secrets file has required keys ──
$content = Get-Content $SecretsFile -Raw
$missingKeys = @()

if ($content -notmatch "ALPACA_API_KEY=PK") {
    $missingKeys += "ALPACA_API_KEY (still has placeholder)"
}
if ($content -match "ALPACA_SECRET_KEY=X{10}") {
    $missingKeys += "ALPACA_SECRET_KEY (still has placeholder)"
}

# Check for at least one LLM provider
$hasTogetherKey = $content -match "TOGETHER_AI_API_KEY=" -and $content -notmatch "TOGETHER_AI_API_KEY=X{10}"
$hasOpenRouterKey = $content -match "OPENROUTER_API_KEY=" -and $content -notmatch "OPENROUTER_API_KEY=X{10}"
$hasFireworksKey = $content -match "FIREWORKS_AI_API_KEY=" -and $content -notmatch "FIREWORKS_AI_API_KEY=X{10}"
$hasOpenAIKey = $content -match "OPENAI_API_KEY=" -and $content -notmatch "OPENAI_API_KEY=X{10}"
$hasLLM = $hasTogetherKey -or $hasOpenRouterKey -or $hasFireworksKey -or $hasOpenAIKey

if ($missingKeys.Count -gt 0) {
    Write-Host ""
    Write-Host "  [!] Secrets file has placeholder values:" -ForegroundColor Red
    foreach ($key in $missingKeys) {
        Write-Host "      - $key" -ForegroundColor Red
    }
    Write-Host ""
    Write-Host "  Edit $SecretsFile and replace XXXX with real keys." -ForegroundColor Yellow
    Write-Host ""
    exit 1
}

if (-not $hasLLM) {
    Write-Host ""
    Write-Host "  [WARN] No LLM provider key found." -ForegroundColor Yellow
    Write-Host "      OK for build-scenarios (only needs Alpaca)," -ForegroundColor Yellow
    Write-Host "      but record-scenarios requires an LLM key." -ForegroundColor Yellow
    Write-Host ""
}

# ── Parse command from arguments ──
if ($Arguments.Count -eq 0) {
    Write-Host "  [!] No command specified." -ForegroundColor Red
    Write-Host ""
    Write-Host "  Usage:" -ForegroundColor White
    Write-Host "    .\scripts\run_phase3.ps1 build-scenarios" -ForegroundColor Gray
    Write-Host "    .\scripts\run_phase3.ps1 record-scenarios --max-scenarios 10" -ForegroundColor Gray
    Write-Host "    .\scripts\run_phase3.ps1 backtest" -ForegroundColor Gray
    Write-Host "    .\scripts\run_phase3.ps1 paper" -ForegroundColor Gray
    Write-Host ""
    Write-Host "  Commands:" -ForegroundColor White
    Write-Host "    build-scenarios     Fetch historical gap scenarios from Alpaca (~2-5 min)" -ForegroundColor Gray
    Write-Host "    record-scenarios    Run scenarios through agent pipeline (~30-90 min)" -ForegroundColor Gray
    Write-Host "    backtest            Run CPCV backtest on recorded data (~10 sec)" -ForegroundColor Gray
    Write-Host "    paper               Start paper trading (after backtest passes)" -ForegroundColor Gray
    Write-Host "    scan                Run pre-market scanner only" -ForegroundColor Gray
    Write-Host "    analyze             Post-session Elo analysis" -ForegroundColor Gray
    Write-Host ""
    exit 1
}

$Command = $Arguments[0]
$ExtraArgs = if ($Arguments.Count -gt 1) { $Arguments[1..($Arguments.Count-1)] -join " " } else { "" }

Write-Host "  Command:      $Command $ExtraArgs" -ForegroundColor Gray

# ── Copy secrets to .env ──
Write-Host "  Copying secrets to .env (gitignored)..." -ForegroundColor Gray
Copy-Item -Path $SecretsFile -Destination $EnvTarget -Force
Write-Host "  [OK] .env loaded" -ForegroundColor Green
Write-Host ""

# ── Run the command ──
Write-Host "  ------------------------------------------------" -ForegroundColor DarkGray
Write-Host ""

$pythonCmd = "python -m main $Command $ExtraArgs"
Write-Host "  > $pythonCmd" -ForegroundColor Cyan
Write-Host ""

Push-Location $ProjectRoot
try {
    if ($ExtraArgs) {
        $allArgs = @("-m", "main", $Command) + ($ExtraArgs -split " ")
        & python @allArgs
    } else {
        & python -m main $Command
    }
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

# ── Report results ──
Write-Host ""
Write-Host "  ------------------------------------------------" -ForegroundColor DarkGray

if ($exitCode -eq 0) {
    Write-Host "  [OK] Command completed successfully." -ForegroundColor Green
} else {
    Write-Host "  [!] Command exited with code $exitCode" -ForegroundColor Red
}

# Show output files if they exist
$dataDir = Join-Path $ProjectRoot "data"
$outputFiles = @(
    "scenarios\gap_scenarios.json",
    "scenarios\recording_report.json",
    "scenarios\backtest_data.json",
    "backtest_report.json"
)
foreach ($f in $outputFiles) {
    $fullPath = Join-Path $dataDir $f
    if (Test-Path $fullPath) {
        $size = (Get-Item $fullPath).Length / 1KB
        Write-Host "  Output: data/$f ($([math]::Round($size, 1)) KB)" -ForegroundColor Gray
    }
}

Write-Host ""
exit $exitCode
