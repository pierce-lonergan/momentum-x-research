#!/usr/bin/env python
"""D200: Decision Quality Arena CLI.

Analyzes the full pipeline decision quality — not just scanner filters.
Measures per-agent accuracy, MFCS calibration, catalyst breakdown, and debate impact.

Usage:
    python scripts/run_decision_arena.py                    # Full report
    python scripts/run_decision_arena.py --agent news       # Single agent focus
    python scripts/run_decision_arena.py --calibration      # MFCS calibration only
    python scripts/run_decision_arena.py --save results.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.arena.decision_quality import DecisionQualityArena


def main() -> None:
    parser = argparse.ArgumentParser(description="D200: Decision Quality Arena")
    parser.add_argument("--agent", help="Focus on a single agent (e.g., 'news_agent')")
    parser.add_argument("--calibration", action="store_true", help="Show MFCS calibration only")
    parser.add_argument("--save", help="Save results to JSON file")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    arena = DecisionQualityArena()
    n_loaded = arena.load_journals()
    n_joined = arena.load_outcomes()

    if n_loaded == 0:
        print("ERROR: No journal entries found. Check data/journals/ directory.")
        sys.exit(1)

    report = arena.analyze()
    print(arena.format_report(report))

    # Additional detail for specific agent
    if args.agent:
        aa = report.agent_accuracy.get(args.agent)
        if aa:
            print(f"\n─── DETAIL: {args.agent} ───")
            print(f"  Total signals: {aa.total_signals}")
            print(f"  Bullish: {aa.bullish_signals} ({aa.bullish_accuracy:.1%} accurate)")
            print(f"  Bearish: {aa.bearish_signals} ({aa.bearish_accuracy:.1%} accurate)")
            print(f"  Neutral: {aa.neutral_signals}")
        else:
            print(f"\nAgent '{args.agent}' not found. Available: {', '.join(report.agent_accuracy.keys())}")

    if args.save:
        output = {
            "total_decisions": report.total_decisions,
            "decisions_with_outcomes": report.decisions_with_outcomes,
            "buy_win_rate": report.buy_win_rate,
            "avg_mfcs_winners": report.avg_mfcs_winners,
            "avg_mfcs_losers": report.avg_mfcs_losers,
            "agent_accuracy": {
                k: {
                    "bullish_accuracy": v.bullish_accuracy,
                    "bearish_accuracy": v.bearish_accuracy,
                    "overall_accuracy": v.overall_accuracy,
                    "total_signals": v.total_signals,
                }
                for k, v in report.agent_accuracy.items()
            },
            "mfcs_calibration": [
                {
                    "range": f"[{b.mfcs_min:.1f}, {b.mfcs_max:.1f})",
                    "n": b.total,
                    "win_rate": b.win_rate,
                    "avg_pnl": b.avg_pnl,
                }
                for b in report.mfcs_calibration
            ],
            "catalyst_breakdown": {
                k: {"win_rate": v.win_rate, "n": v.total, "buy_count": v.buy_count}
                for k, v in report.catalyst_breakdown.items()
            },
        }
        with open(args.save, "w") as f:
            json.dump(output, f, indent=2)
        print(f"\nResults saved to {args.save}")


if __name__ == "__main__":
    main()
