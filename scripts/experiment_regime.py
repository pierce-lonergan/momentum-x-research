#!/usr/bin/env python
"""D200-E3: Regime-Conditional Strategy Experiment.

Partitions 196 labeled scenarios by market regime (VIX, day-of-week, gap density,
gap size) and identifies regimes with negative expected value.

Usage:
    python scripts/experiment_regime.py                  # Full analysis
    python scripts/experiment_regime.py --vix-only       # Just VIX partitioning
    python scripts/experiment_regime.py --no-trade-days   # Only show negative EV
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.arena.regime_analyzer import RegimeAnalyzer


def main() -> None:
    parser = argparse.ArgumentParser(description="D200-E3: Regime Analysis")
    parser.add_argument("--vix-only", action="store_true")
    parser.add_argument("--no-trade-days", action="store_true",
                        help="Only show negative EV regimes")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    analyzer = RegimeAnalyzer()
    n_scenarios = analyzer.load_scenarios()
    if n_scenarios == 0:
        print("ERROR: No scenarios found. Check data/scenarios/gap_scenarios.json")
        sys.exit(1)

    n_vix = analyzer.load_vix()
    report = analyzer.analyze()

    if args.no_trade_days:
        print("=" * 70)
        print("D200-E3: NEGATIVE EV REGIMES — SIT THESE OUT")
        print("=" * 70)
        if report.negative_ev_regimes:
            for regime in report.negative_ev_regimes:
                print(f"  ✗ {regime}")
        else:
            print("  No negative EV regimes found with n≥5.")
        print("=" * 70)
    else:
        print(analyzer.format_report(report))


if __name__ == "__main__":
    main()
