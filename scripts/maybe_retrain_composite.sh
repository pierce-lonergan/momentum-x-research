#!/usr/bin/env bash
# D221 Phase D — manual retrain trigger for the composite score.
#
# Decides whether to retrain based on:
#   - Current row count in features_labeled.jsonl + the labels_shards/ shards
#   - Last training's row count (from latest models/composite_*_metadata.json)
#   - Days since last train
#
# Triggers retrain if EITHER:
#   (a) current_rows > last_train_rows * 1.5   (data has grown 50%+)
#   (b) last train > 7 days ago
#
# By design this is a SHELL SCRIPT, not a cron job.
# The manual gate is the safety mechanism while the system is still maturing.
# Run it AFTER each backfill milestone (~every 1000 new rows).
#
# Usage:
#   ./scripts/maybe_retrain_composite.sh                # check + maybe-train
#   ./scripts/maybe_retrain_composite.sh --check-only   # report decision, do not train
#   ./scripts/maybe_retrain_composite.sh --force        # train regardless of triggers
#
# Always logs to docs/research-log/composite_retrain_log.md (append-only).

set -e

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

LABELS_FILE="data/backfill/features_labeled.jsonl"
SHARDS_DIR="data/backfill/labels_shards"
MODELS_DIR="models"
# Overridable so tests (and dry runs) do not append to the committed research
# log. Without this, the fast suite mutates a tracked document every run.
LOG_FILE="${COMPOSITE_RETRAIN_LOG:-docs/research-log/composite_retrain_log.md}"

CHECK_ONLY=0
FORCE=0
for arg in "$@"; do
    case "$arg" in
        --check-only) CHECK_ONLY=1 ;;
        --force)      FORCE=1 ;;
        --help|-h)
            sed -n '2,/^$/p' "$0" | sed 's/^# \?//'
            exit 0 ;;
        *)
            echo "unknown arg: $arg" >&2
            exit 2 ;;
    esac
done

# ── Count current rows: features_labeled.jsonl + all shards ──

count_lines() {
    if [ -f "$1" ]; then
        wc -l < "$1" | tr -d ' '
    else
        echo 0
    fi
}

base_rows=$(count_lines "$LABELS_FILE")
shard_rows=0
if [ -d "$SHARDS_DIR" ]; then
    for f in "$SHARDS_DIR"/labels_*.jsonl; do
        [ -f "$f" ] || continue
        n=$(count_lines "$f")
        shard_rows=$((shard_rows + n))
    done
fi
current_rows=$((base_rows + shard_rows))

# ── Find latest metadata ──

latest_meta=""
latest_mtime=0
if [ -d "$MODELS_DIR" ]; then
    for f in "$MODELS_DIR"/composite_v*_metadata.json; do
        [ -f "$f" ] || continue
        mtime=$(stat -c %Y "$f" 2>/dev/null || stat -f %m "$f" 2>/dev/null || echo 0)
        if [ "$mtime" -gt "$latest_mtime" ]; then
            latest_mtime=$mtime
            latest_meta="$f"
        fi
    done
fi

# ── Decide ──

trigger_reason=""
last_rows=0
last_train_days=999

if [ -z "$latest_meta" ]; then
    trigger_reason="no prior model — initial training"
else
    last_rows=$(python -c "import json; print(json.load(open('$latest_meta')).get('n_train_rows', 0))" 2>/dev/null || echo 0)
    now=$(date +%s)
    age_secs=$((now - latest_mtime))
    last_train_days=$((age_secs / 86400))
    growth_threshold=$((last_rows * 3 / 2))

    if [ "$current_rows" -gt "$growth_threshold" ] && [ "$last_rows" -gt 0 ]; then
        trigger_reason="row count grew >50% (last: $last_rows, current: $current_rows)"
    elif [ "$last_train_days" -ge 7 ]; then
        trigger_reason="last train was $last_train_days days ago (>= 7 day threshold)"
    fi
fi

if [ "$FORCE" -eq 1 ]; then
    trigger_reason="--force flag"
fi

# ── Log + maybe-execute ──

mkdir -p "$(dirname "$LOG_FILE")"

ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
{
    echo ""
    echo "## $ts"
    echo "- current_rows: $current_rows (base=$base_rows + shards=$shard_rows)"
    echo "- last_train_rows: $last_rows"
    echo "- last_train_age_days: $last_train_days"
    echo "- latest_meta: ${latest_meta:-none}"
    if [ -n "$trigger_reason" ]; then
        echo "- decision: **RETRAIN** ($trigger_reason)"
    else
        echo "- decision: skip (no triggers met; ${last_train_days}d old, ${current_rows}/${last_rows}*1.5 rows)"
    fi
} >> "$LOG_FILE"

echo "current_rows: $current_rows"
echo "last_train_rows: $last_rows"
echo "last_train_age_days: $last_train_days"

if [ -z "$trigger_reason" ]; then
    echo "decision: SKIP (no triggers met)"
    exit 0
fi

echo "decision: RETRAIN — $trigger_reason"

if [ "$CHECK_ONLY" -eq 1 ]; then
    echo "(--check-only set; not executing retrain)"
    exit 0
fi

echo ""
echo "→ python -m src.composite.train --feature-stability-check"
python -m src.composite.train --feature-stability-check
exit_code=$?

echo "" >> "$LOG_FILE"
echo "- training exit code: $exit_code" >> "$LOG_FILE"

exit $exit_code
