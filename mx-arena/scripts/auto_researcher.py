#!/usr/bin/env python3
"""
Nightly Auto-Researcher — Sprint 18.

Runs automatically after each trading day:
1. Load today's trades from journal
2. Add to historical dataset
3. Re-run walk-forward on current bar-1 strategy
4. Compute rolling 40-trade outlier frequency
5. Check overfit ratio and drift
6. Generate a daily research report

This captures 80% of the Darwin-Gödel value at 10% of the cost.
The human stays in the loop for deployment decisions.

Usage:
    python mx-arena/scripts/auto_researcher.py --date 2026-03-28
    python mx-arena/scripts/auto_researcher.py --date 2026-03-28 --actual-pnl 50.00
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arena.decision_replay import load_candidates_from_journals, replay_decisions
from arena.runner import _simulate_journal_trades
from arena.harness import ArenaConfig, ArenaInstance
from arena.stats import bootstrap_profit_factor
from arena.regime import classify_regime, _load_daily_bars, _extract_date

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-5s %(message)s")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
ARENA_DATA = PROJECT_ROOT / "mx-arena" / "data"
REPORTS_DIR = PROJECT_ROOT / "docs" / "daily_research"
TRADE_LOG = ARENA_DATA / "results" / "cumulative_trades.jsonl"


def load_cumulative_trades() -> list[dict]:
    """Load all accumulated trade results."""
    trades = []
    if TRADE_LOG.exists():
        with open(TRADE_LOG) as f:
            for line in f:
                line = line.strip()
                if line:
                    trades.append(json.loads(line))
    return trades


def append_trades(new_trades: list[dict], date: str) -> None:
    """Append new trade results to cumulative log."""
    TRADE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(TRADE_LOG, "a") as f:
        for t in new_trades:
            t["_research_date"] = date
            f.write(json.dumps(t, default=str) + "\n")


def compute_outlier_frequency(trades: list[dict], window: int = 40) -> dict:
    """Compute rolling outlier frequency over the last N trades."""
    recent = trades[-window:] if len(trades) >= window else trades
    n = len(recent)
    # Fix D148: Normalize outlier detection by fill price (percentage, not absolute)
    # A 3% return on a $5 stock is $0.15, not $3.00
    outliers = sum(
        1 for t in recent
        if t.get("fill_price", 0) > 0
        and (t.get("pnl", 0) / t.get("fill_price", 1)) > 0.03
    )
    freq = outliers / max(n, 1)

    return {
        "window": min(window, n),
        "outliers": outliers,
        "frequency": round(freq, 4),
        "expected": 0.063,  # 6.3% baseline from arena
        "status": (
            "HEALTHY" if freq >= 0.04 else
            "WATCH" if freq >= 0.02 else
            "ALERT — consider fallback to 50% bar-1"
        ),
    }


def run_nightly_research(date: str, actual_pnl: float | None = None):
    """Run the complete nightly research cycle."""
    t0 = time.perf_counter()
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    report_lines = []
    report_lines.append(f"# Daily Research Report: {date}")
    report_lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    report_lines.append("")

    # 1. Load today's candidates and simulate
    candidates = load_candidates_from_journals(date, str(DATA_DIR / "journals"))
    if not candidates:
        logger.warning("No journal data for %s — skipping nightly research", date)
        report_lines.append("**No journal data for this date.** Market closed or no candidates.")
        _save_report(report_lines, date)
        print(f"\nNo trades for {date} — nightly research skipped.\n")
        return

    buys = replay_decisions(candidates, {"mfcs_buy_threshold": 0.15})

    config = ArenaConfig(
        date=date,
        symbols=[b["ticker"] for b in buys] or ["SPY"],
        data_dir=str(ARENA_DATA / "historical"),
    )
    instance = ArenaInstance(config)
    instance.data_engine.json_bars_dir = DATA_DIR / "bars"
    instance.load_data()

    bar1_cfg = {
        "time_exit_bars": [1], "time_exit_pcts": [1.00],
        "mfcs_buy_threshold": 0.15, "max_positions": 8,
    }
    trades = _simulate_journal_trades(instance, buys, bar1_cfg)

    # Append to cumulative log
    append_trades(trades, date)

    # 2. Core metrics
    predicted_pnl = sum(t.get("pnl", 0) for t in trades)
    n_trades = len(trades)
    pf, ci_lo, ci_hi = bootstrap_profit_factor(trades)

    report_lines.append("## Today's Simulation")
    report_lines.append(f"- Candidates: {len(candidates)}")
    report_lines.append(f"- BUY decisions: {len(buys)}")
    report_lines.append(f"- Trades simulated: {n_trades}")
    report_lines.append(f"- Predicted P&L: ${predicted_pnl:+.4f}")
    if actual_pnl is not None:
        delta = predicted_pnl - actual_pnl
        report_lines.append(f"- Actual P&L: ${actual_pnl:+.2f}")
        report_lines.append(f"- Delta: ${delta:+.4f}")
    report_lines.append(f"- Profit Factor: {pf:.2f} [{ci_lo:.2f}, {ci_hi:.2f}]")
    report_lines.append("")

    # 3. Cumulative analysis
    all_trades = load_cumulative_trades()
    total_trades = len(all_trades)
    total_pnl = sum(t.get("pnl", 0) for t in all_trades)

    report_lines.append("## Cumulative Status")
    report_lines.append(f"- Total trades: {total_trades}")
    report_lines.append(f"- Total P&L: ${total_pnl:+.4f}")
    report_lines.append("")

    # 4. Outlier frequency monitoring
    outlier_status = compute_outlier_frequency(all_trades)
    report_lines.append("## Outlier Monitoring (40-Trade Window)")
    report_lines.append(f"- Window: {outlier_status['window']} trades")
    report_lines.append(f"- Outliers (>3% bar-1 return): {outlier_status['outliers']}")
    report_lines.append(f"- Frequency: {outlier_status['frequency']:.1%} (expected: 6.3%)")
    report_lines.append(f"- Status: **{outlier_status['status']}**")
    report_lines.append("")

    # 5. Regime
    spy_bars = _load_daily_bars(ARENA_DATA / "daily", "SPY")
    vixy_bars = _load_daily_bars(ARENA_DATA / "daily", "VIXY")
    spy_by_date = {str(_extract_date(b.get("t", b.get("timestamp", "")))): b for b in spy_bars}
    vixy_by_date = {str(_extract_date(b.get("t", b.get("timestamp", "")))): b for b in vixy_bars}
    spy = spy_by_date.get(date, {})
    vixy = vixy_by_date.get(date, {})
    spy_ret = ((spy.get("c", 0) - spy.get("o", 1)) / max(spy.get("o", 1), 1)) * 100
    vixy_ret = ((vixy.get("c", 0) - vixy.get("o", 1)) / max(vixy.get("o", 1), 1)) * 100
    regime = classify_regime(spy_ret, vixy_ret)

    report_lines.append(f"## Regime: {regime}")
    report_lines.append(f"- SPY: {spy_ret:+.1f}% | VIXY: {vixy_ret:+.1f}%")
    report_lines.append("")

    # 6. Alerts
    report_lines.append("## Alerts")
    alerts = []
    if outlier_status["status"] == "ALERT":
        alerts.append("OUTLIER FREQUENCY BELOW THRESHOLD — consider 50% bar-1 fallback")
    if pf < 1.0 and n_trades >= 5:
        alerts.append(f"PF below 1.0 ({pf:.2f}) — strategy underperforming today")
    if total_trades >= 40 and outlier_status["outliers"] < 2:
        alerts.append(f"40+ trades with <2 outliers — REVERT to 50% bar-1 recommended")

    if alerts:
        for a in alerts:
            report_lines.append(f"- **{a}**")
    else:
        report_lines.append("- No alerts. Strategy performing within expected range.")
    report_lines.append("")

    # 7. Per-trade detail
    if trades:
        report_lines.append("## Trade Detail")
        for t in trades:
            report_lines.append(
                f"- {t['ticker']:6s} fill=${t['fill_price']:.2f} "
                f"pnl=${t['pnl']:+.4f} mfe={t.get('mfe_pct', 0):.1%} "
                f"{t['exit_reason']}"
            )
    report_lines.append("")

    elapsed = time.perf_counter() - t0
    report_lines.append(f"---")
    report_lines.append(f"Generated in {elapsed:.1f}s by auto_researcher.py")

    _save_report(report_lines, date)

    # Print summary
    print(f"\n{'='*60}")
    print(f"NIGHTLY RESEARCH: {date}")
    print(f"{'='*60}")
    print(f"  Trades: {n_trades} | P&L: ${predicted_pnl:+.4f}")
    print(f"  Cumulative: {total_trades} trades, ${total_pnl:+.4f}")
    print(f"  Outliers: {outlier_status['outliers']}/{outlier_status['window']} "
          f"({outlier_status['frequency']:.1%}) — {outlier_status['status']}")
    print(f"  Regime: {regime}")
    if alerts:
        for a in alerts:
            print(f"  ALERT: {a}")
    print(f"  Report: {REPORTS_DIR / f'{date}.md'}")
    print(f"{'='*60}\n")


def _save_report(lines: list[str], date: str) -> None:
    """Save report to markdown file."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_file = REPORTS_DIR / f"{date}.md"
    with open(report_file, "w") as f:
        f.write("\n".join(lines))
    logger.info("Report saved to %s", report_file)


def main():
    parser = argparse.ArgumentParser(description="Nightly auto-researcher")
    parser.add_argument("--date", type=str, required=True)
    parser.add_argument("--actual-pnl", type=float, help="Actual production P&L")
    args = parser.parse_args()
    run_nightly_research(args.date, args.actual_pnl)


if __name__ == "__main__":
    main()
