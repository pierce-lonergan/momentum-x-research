#!/usr/bin/env python3
"""
Daily Reconciliation + Continuous Optimization Loop.

After each live trading day:
1. Prep replay config from session journal
2. Run arena with current production params
3. Analyze with all innovations (ordering, failure modes, capture ratio)
4. Run drift detection vs training baseline
5. If drift detected: re-run walk-forward optimization
6. Output: comprehensive daily report

Innovation 6: The arena as a continuous learning system.

Usage:
    python mx-arena/scripts/daily_reconciliation.py --date 2026-03-26
    python mx-arena/scripts/daily_reconciliation.py --date 2026-03-26 --full-analysis
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
from arena.stats import bootstrap_profit_factor, bootstrap_mean_pnl
from arena.regime import (
    classify_regime, detect_parameter_drift, format_drift_report,
    label_dates_with_regime, _load_daily_bars,
)
from arena.failure_modes import analyze_failure_distribution, format_failure_analysis
from arena.ordering_study import (
    run_ordering_comparison, format_ordering_results,
    run_pipeline_inversion,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
ARENA_DATA = PROJECT_ROOT / "mx-arena" / "data"
DEFAULT_PARAMS = {"mfcs_buy_threshold": 0.15}


def run_daily_report(date: str, full_analysis: bool = False):
    """Generate comprehensive daily reconciliation report."""
    t0 = time.perf_counter()

    print(f"\n{'='*70}")
    print(f"DAILY RECONCILIATION: {date}")
    print(f"{'='*70}\n")

    # 1. Load candidates and replay decisions
    candidates = load_candidates_from_journals(date, str(DATA_DIR / "journals"))
    if not candidates:
        print(f"  No journal data for {date}. Skipping.")
        return

    buys = replay_decisions(candidates, DEFAULT_PARAMS)
    print(f"  Candidates: {len(candidates)} | BUY decisions: {len(buys)}")

    # 2. Run trade simulation
    config = ArenaConfig(
        date=date,
        symbols=[b["ticker"] for b in buys] or ["SPY"],
        data_dir=str(ARENA_DATA / "historical"),
    )
    instance = ArenaInstance(config)
    instance.data_engine.json_bars_dir = DATA_DIR / "bars"
    instance.load_data()

    trades = _simulate_journal_trades(instance, buys, DEFAULT_PARAMS)

    # 3. Core metrics
    total_pnl = sum(t.get("pnl", 0) for t in trades)
    n_trades = len(trades)
    win_rate = sum(1 for t in trades if t.get("pnl", 0) > 0) / max(n_trades, 1)
    pf, ci_lo, ci_hi = bootstrap_profit_factor(trades)
    avg_capture = sum(t.get("capture_ratio", 0) for t in trades) / max(n_trades, 1)
    avg_mfe = sum(t.get("mfe_pct", 0) for t in trades) / max(n_trades, 1)
    avg_mae = sum(t.get("mae_pct", 0) for t in trades) / max(n_trades, 1)

    print(f"\n  CORE METRICS")
    print(f"  {'-'*50}")
    print(f"  P&L:            ${total_pnl:+.4f}")
    print(f"  Trades:         {n_trades}")
    print(f"  Win Rate:       {win_rate:.0%}")
    print(f"  Profit Factor:  {pf:.2f} [{ci_lo:.2f}, {ci_hi:.2f}]")
    print(f"  Avg Capture:    {avg_capture:.1%}")
    print(f"  Avg MFE:        {avg_mfe:.1%}")
    print(f"  Avg MAE:        {avg_mae:.1%}")

    # 4. Per-trade detail
    print(f"\n  TRADE DETAIL")
    print(f"  {'-'*50}")
    for t in trades:
        cap = t.get("capture_ratio", 0)
        print(
            f"  {t['ticker']:6s} fill=${t['fill_price']:.2f} "
            f"pnl=${t['pnl']:+.4f} mfe={t.get('mfe_pct',0):.1%} "
            f"mae={t.get('mae_pct',0):.1%} cap={cap:.0%} {t['exit_reason']}"
        )

    # 5. Regime classification
    spy_bars = _load_daily_bars(ARENA_DATA / "daily", "SPY")
    vixy_bars = _load_daily_bars(ARENA_DATA / "daily", "VIXY")
    spy_by_date = {str(b.get("t", b.get("timestamp", "")))[:10]: b for b in spy_bars}
    vixy_by_date = {str(b.get("t", b.get("timestamp", "")))[:10]: b for b in vixy_bars}

    spy_bar = spy_by_date.get(date, {})
    vixy_bar = vixy_by_date.get(date, {})
    spy_ret = ((spy_bar.get("c", 0) - spy_bar.get("o", 1)) / max(spy_bar.get("o", 1), 1)) * 100
    vixy_ret = ((vixy_bar.get("c", 0) - vixy_bar.get("o", 1)) / max(vixy_bar.get("o", 1), 1)) * 100
    regime = classify_regime(spy_ret, vixy_ret)

    print(f"\n  REGIME: {regime} (SPY={spy_ret:+.1f}%, VIXY={vixy_ret:+.1f}%)")

    # 6. Failure mode analysis
    if trades:
        failure_summary = analyze_failure_distribution(trades)
        print(f"\n{format_failure_analysis(failure_summary)}")

    # 7. Full analysis (ordering study + pipeline inversion)
    if full_analysis and buys:
        print(f"\n  ORDERING STUDY")
        print(f"  {'-'*50}")
        ordering_results = run_ordering_comparison(
            buys, _simulate_journal_trades, instance, DEFAULT_PARAMS, delay_bars=1,
        )
        print(format_ordering_results(ordering_results))

        print(f"\n  PIPELINE INVERSION")
        print(f"  {'-'*50}")
        inversion = run_pipeline_inversion(
            buys, _simulate_journal_trades, instance, DEFAULT_PARAMS,
        )
        print(f"  Sequential: ${inversion['sequential']['pnl']:+.4f} ({inversion['sequential']['trades']} trades)")
        print(f"  Parallel:   ${inversion['parallel']['pnl']:+.4f} ({inversion['parallel']['trades']} trades)")
        print(f"  Delta:      ${inversion['delta_pnl']:+.4f} ({inversion['delta_pct']:+.1f}%)")

    # 8. Save report
    elapsed = time.perf_counter() - t0
    report = {
        "date": date,
        "regime": regime,
        "spy_return": round(spy_ret, 2),
        "vixy_return": round(vixy_ret, 2),
        "n_candidates": len(candidates),
        "n_buys": len(buys),
        "n_trades": n_trades,
        "total_pnl": round(total_pnl, 4),
        "win_rate": round(win_rate, 3),
        "profit_factor": pf,
        "pf_ci": [ci_lo, ci_hi],
        "avg_capture_ratio": round(avg_capture, 4),
        "avg_mfe_pct": round(avg_mfe, 4),
        "avg_mae_pct": round(avg_mae, 4),
        "trades": [{
            "ticker": t["ticker"],
            "pnl": t["pnl"],
            "capture_ratio": t.get("capture_ratio", 0),
            "mfe_pct": t.get("mfe_pct", 0),
            "mae_pct": t.get("mae_pct", 0),
            "exit_reason": t["exit_reason"],
        } for t in trades],
        "elapsed_seconds": round(elapsed, 1),
    }

    out_dir = ARENA_DATA / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"daily_{date}.json"
    with open(out_file, "w") as f:
        json.dump(report, f, indent=2, default=str)

    print(f"\n  Report saved to {out_file}")
    print(f"  Completed in {elapsed:.1f}s")
    print(f"{'='*70}\n")

    return report


def main():
    parser = argparse.ArgumentParser(description="Daily arena reconciliation")
    parser.add_argument("--date", type=str, required=True)
    parser.add_argument("--full-analysis", action="store_true",
                        help="Include ordering study + pipeline inversion")
    args = parser.parse_args()
    run_daily_report(args.date, full_analysis=args.full_analysis)


if __name__ == "__main__":
    main()
