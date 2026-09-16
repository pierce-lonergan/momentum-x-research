#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
# Momentum-X Phase 3 Runner
#
# Loads secrets from external file, runs Momentum-X commands.
#
# Usage:
#   ./scripts/run_phase3.sh build-scenarios
#   ./scripts/run_phase3.sh record-scenarios --max-scenarios 10
#   ./scripts/run_phase3.sh backtest
#   SECRETS_FILE=/path/to/secrets.env ./scripts/run_phase3.sh build-scenarios
# ══════════════════════════════════════════════════════════════

set -euo pipefail

# ── Config ──────────────────────────────────────────────────────────
SECRETS_FILE="${SECRETS_FILE:-$HOME/momentum-x-secrets.env}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
ENV_TARGET="$PROJECT_ROOT/.env"

# ── Banner ──────────────────────────────────────────────────────────
echo ""
echo "  ╔══════════════════════════════════════════════════╗"
echo "  ║         MOMENTUM-X  Phase 3 Runner              ║"
echo "  ╚══════════════════════════════════════════════════╝"
echo ""

# ── Check secrets file ──────────────────────────────────────────────
if [ ! -f "$SECRETS_FILE" ]; then
    echo "  [!] Secrets file not found: $SECRETS_FILE"
    echo ""
    echo "  Creating template..."
    cat > "$SECRETS_FILE" << 'TEMPLATE'
# ══════════════════════════════════════════════════════════════
# MOMENTUM-X SECRETS FILE
# Keep this file OUTSIDE your git repo. Never commit API keys.
# ══════════════════════════════════════════════════════════════

# ── Alpaca Paper Trading ──────────────────────────────────────
# Get these from: https://app.alpaca.markets -> Paper Trading -> API Keys
ALPACA_API_KEY=PKXXXXXXXXXXXXXXXXXX
ALPACA_SECRET_KEY=XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
ALPACA_BASE_URL=https://paper-api.alpaca.markets
ALPACA_DATA_URL=https://data.alpaca.markets
ALPACA_FEED=iex

# ── Together AI (LLM Provider) ───────────────────────────────
# Get this from: https://api.together.ai -> API Keys
# Sign up for $25 free credits.
TOGETHER_AI_API_KEY=XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX
LLM_TIER1_MODEL=moonshotai/Kimi-K2-Thinking
LLM_TIER1_PROVIDER=together_ai
LLM_TIER2_MODEL=moonshotai/Kimi-K2.5
LLM_TIER2_PROVIDER=together_ai

# ── Optional: Finnhub News ───────────────────────────────────
# Get this from: https://finnhub.io (free tier: 60 req/min)
# FINNHUB_API_KEY=XXXXXXXXXXXXXXXXXXXXXXXX
TEMPLATE

    echo "  [OK] Template created at: $SECRETS_FILE"
    echo ""
    echo "  Next steps:"
    echo "    1. Edit $SECRETS_FILE"
    echo "    2. Replace XXXX placeholders with real API keys"
    echo "    3. Re-run this script"
    echo ""
    exit 0
fi

echo "  Secrets file: $SECRETS_FILE"

# ── Validate required keys ──────────────────────────────────────────
if grep -q 'ALPACA_API_KEY=PKXXXX' "$SECRETS_FILE"; then
    echo "  [!] ALPACA_API_KEY still has placeholder value." >&2
    echo "  Edit $SECRETS_FILE and replace with your real key." >&2
    exit 1
fi

if grep -q 'ALPACA_SECRET_KEY=XXXX' "$SECRETS_FILE"; then
    echo "  [!] ALPACA_SECRET_KEY still has placeholder value." >&2
    echo "  Edit $SECRETS_FILE and replace with your real key." >&2
    exit 1
fi

# ── Parse command ───────────────────────────────────────────────────
if [ $# -eq 0 ]; then
    echo "  [!] No command specified."
    echo ""
    echo "  Usage:"
    echo "    ./scripts/run_phase3.sh build-scenarios"
    echo "    ./scripts/run_phase3.sh record-scenarios --max-scenarios 10"
    echo "    ./scripts/run_phase3.sh backtest"
    echo "    ./scripts/run_phase3.sh paper"
    echo ""
    echo "  Available commands:"
    echo "    build-scenarios     Fetch historical gap scenarios from Alpaca (~2-5 min)"
    echo "    record-scenarios    Run scenarios through agent pipeline (~30-90 min)"
    echo "    backtest            Run CPCV backtest on recorded data (~10 sec)"
    echo "    paper               Start paper trading (after backtest ACCEPTED)"
    echo "    scan                Run pre-market scanner only"
    echo "    analyze             Post-session Elo analysis"
    echo ""
    exit 1
fi

COMMAND="$1"
shift
EXTRA_ARGS="$*"

echo "  Command:      $COMMAND $EXTRA_ARGS"

# ── Copy secrets to .env ────────────────────────────────────────────
echo "  Copying secrets to .env (gitignored)..."
cp "$SECRETS_FILE" "$ENV_TARGET"
echo "  [OK] .env loaded"
echo ""

# ── Run the command ─────────────────────────────────────────────────
echo "  ────────────────────────────────────────────────────"
echo ""
echo "  > python -m main $COMMAND $EXTRA_ARGS"
echo ""

cd "$PROJECT_ROOT"
# shellcheck disable=SC2086
python -m main "$COMMAND" $EXTRA_ARGS
EXIT_CODE=$?

# ── Report results ──────────────────────────────────────────────────
echo ""
echo "  ────────────────────────────────────────────────────"

if [ $EXIT_CODE -eq 0 ]; then
    echo "  [OK] Command completed successfully."
else
    echo "  [!] Command exited with code $EXIT_CODE"
fi

# Show output files
for f in data/scenarios/gap_scenarios.json data/scenarios/recording_report.json \
         data/scenarios/backtest_data.json data/backtest_report.json; do
    if [ -f "$PROJECT_ROOT/$f" ]; then
        SIZE=$(du -h "$PROJECT_ROOT/$f" | cut -f1)
        echo "  Output: $f ($SIZE)"
    fi
done

echo ""
exit $EXIT_CODE
