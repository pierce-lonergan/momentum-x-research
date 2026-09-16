#!/usr/bin/env python3
"""
Daily Retrospective Comparison — measure arena prediction accuracy.

The real path to 10.0: each morning, replay yesterday in the arena,
compare predicted P&L to actual production P&L. After 20 days, compute
correlation. If >0.7, arena predictions are trustworthy.

Usage:
    python mx-arena/scripts/daily_retrospective.py --date 2026-03-26
    python mx-arena/scripts/daily_retrospective.py --date 2026-03-26 --actual-pnl 150.00

    # After 20+ days, compute accumulated accuracy:
    python mx-arena/scripts/daily_retrospective.py --analyze
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arena.decision_replay import load_candidates_from_journals, replay_decisions
from arena.runner import _simulate_journal_trades
from arena.harness import ArenaConfig, ArenaInstance
from arena.stats import bootstrap_profit_factor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
ARENA_DATA = PROJECT_ROOT / "mx-arena" / "data"
RETRO_FILE = ARENA_DATA / "results" / "retrospective_log.jsonl"


def run_retrospective(date: str, actual_pnl: float | None = None) -> dict:
    """Replay a date in the arena and log predicted vs actual P&L."""
    t0 = time.perf_counter()

    candidates = load_candidates_from_journals(date, str(DATA_DIR / "journals"))
    if not candidates:
        print(f"  No journal data for {date}.")
        return {}

    buys = replay_decisions(candidates, {"mfcs_buy_threshold": 0.15})

    config = ArenaConfig(
        date=date,
        symbols=[b["ticker"] for b in buys] or ["SPY"],
        data_dir=str(ARENA_DATA / "historical"),
    )
    instance = ArenaInstance(config)
    instance.data_engine.json_bars_dir = DATA_DIR / "bars"
    instance.load_data()

    trades = _simulate_journal_trades(instance, buys, {
        "mfcs_buy_threshold": 0.15,
        "max_positions": 8,
    })

    predicted_pnl = sum(t.get("pnl", 0) for t in trades)
    n_trades = len(trades)
    pf, ci_lo, ci_hi = bootstrap_profit_factor(trades)
    elapsed = time.perf_counter() - t0

    entry = {
        "date": date,
        "predicted_pnl": round(predicted_pnl, 4),
        "actual_pnl": actual_pnl,
        "delta": round(predicted_pnl - actual_pnl, 4) if actual_pnl is not None else None,
        "n_trades": n_trades,
        "profit_factor": pf,
        "tickers": [t["ticker"] for t in trades],
        "elapsed_s": round(elapsed, 1),
    }

    # Append to log
    RETRO_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(RETRO_FILE, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")

    print(f"\n  DAILY RETROSPECTIVE: {date}")
    print(f"  {'='*50}")
    print(f"  Arena predicted P&L: ${predicted_pnl:+.4f}")
    if actual_pnl is not None:
        print(f"  Actual production P&L: ${actual_pnl:+.2f}")
        print(f"  Delta: ${entry['delta']:+.4f}")
    else:
        print(f"  Actual P&L: not provided (use --actual-pnl)")
    print(f"  Trades: {n_trades} | PF: {pf:.2f} [{ci_lo:.2f}, {ci_hi:.2f}]")
    print(f"  Completed in {elapsed:.1f}s")
    print(f"  Logged to {RETRO_FILE}")

    return entry


def analyze_retrospectives():
    """Compute prediction accuracy from accumulated retrospective log."""
    if not RETRO_FILE.exists():
        print("No retrospective log found. Run daily retrospectives first.")
        return

    entries = []
    with open(RETRO_FILE) as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))

    # Filter entries with both predicted and actual
    paired = [e for e in entries if e.get("actual_pnl") is not None]

    print(f"\n  RETROSPECTIVE ANALYSIS")
    print(f"  {'='*50}")
    print(f"  Total entries: {len(entries)}")
    print(f"  With actual P&L: {len(paired)}")

    if len(paired) < 3:
        print(f"  Need at least 3 paired entries. Have {len(paired)}.")
        print(f"  Run more retrospectives with --actual-pnl to accumulate data.")
        return

    predicted = [e["predicted_pnl"] for e in paired]
    actual = [e["actual_pnl"] for e in paired]
    deltas = [e["delta"] for e in paired]

    # Correlation
    n = len(paired)
    mean_p = sum(predicted) / n
    mean_a = sum(actual) / n
    cov = sum((p - mean_p) * (a - mean_a) for p, a in zip(predicted, actual)) / n
    std_p = (sum((p - mean_p) ** 2 for p in predicted) / n) ** 0.5
    std_a = (sum((a - mean_a) ** 2 for a in actual) / n) ** 0.5
    corr = cov / (std_p * std_a) if std_p > 0 and std_a > 0 else 0

    mean_delta = sum(deltas) / n
    abs_delta = sum(abs(d) for d in deltas) / n

    print(f"\n  Prediction Accuracy:")
    print(f"    Correlation: {corr:.3f}")
    print(f"    Mean delta: ${mean_delta:+.4f}")
    print(f"    Mean |delta|: ${abs_delta:.4f}")
    print(f"    Entries: {n}")

    if corr > 0.7:
        print(f"\n  VERDICT: Arena predictions TRUSTWORTHY (corr={corr:.2f} > 0.7)")
        print(f"  Parameter recommendations from arena can be deployed.")
    elif corr > 0.4:
        print(f"\n  VERDICT: Arena predictions DIRECTIONALLY USEFUL (corr={corr:.2f})")
        print(f"  Use for relative ranking, not absolute P&L prediction.")
    else:
        print(f"\n  VERDICT: Arena predictions UNRELIABLE (corr={corr:.2f})")
        print(f"  Investigate fidelity gap before trusting arena output.")

    # Per-entry detail
    print(f"\n  Per-Day Detail:")
    for e in paired:
        print(
            f"    {e['date']}: predicted=${e['predicted_pnl']:+.4f} "
            f"actual=${e['actual_pnl']:+.2f} delta=${e['delta']:+.4f}"
        )


def main():
    parser = argparse.ArgumentParser(description="Daily retrospective comparison")
    parser.add_argument("--date", type=str, help="Date to replay")
    parser.add_argument("--actual-pnl", type=float, help="Actual production P&L for comparison")
    parser.add_argument("--analyze", action="store_true", help="Analyze accumulated retrospectives")
    args = parser.parse_args()

    if args.analyze:
        analyze_retrospectives()
    elif args.date:
        run_retrospective(args.date, args.actual_pnl)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
