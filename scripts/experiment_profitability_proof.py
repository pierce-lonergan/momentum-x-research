#!/usr/bin/env python
"""D213: Enhanced Profitability Proof — slippage, serial gapper gate, walk-forward.

Proves the gate stack produces positive expected value on labeled scenarios
with realistic transaction costs, D212 enrichment gates, and out-of-sample
walk-forward validation.

Usage:
    python scripts/experiment_profitability_proof.py              # Full run with slippage
    python scripts/experiment_profitability_proof.py --walk-forward  # Train/test split
    python scripts/experiment_profitability_proof.py --sweep-gaps    # Optimal gap threshold
    python scripts/experiment_profitability_proof.py --no-slippage   # Compare without costs
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_DATA = Path(__file__).resolve().parent.parent / "data"


@dataclass
class BacktestConfig:
    """All tunable parameters for a single backtest run."""
    # Gates
    vix_block: float = 20.0
    vix_reduce: float = 15.0
    allowed_days: set = field(default_factory=lambda: {0, 1, 2, 3, 4})  # D211: all weekdays
    gap_min: float = 0.05
    gap_max: float = 0.50
    rvol_min: float = 2.0
    serial_gap_threshold: int = 3     # D212: block if >= N prior gaps
    block_day2_runners: bool = True   # D212: block day-2 without catalyst
    # Execution
    stop_pct: float = 0.35
    target_pct: float = 0.10
    # Slippage
    entry_slippage: float = 0.005     # 0.5% adverse entry fill
    stop_slippage: float = 0.010      # 1.0% gap-through on stop fills
    eod_slippage: float = 0.003       # 0.3% EOD close slippage
    label: str = ""


@dataclass
class BacktestResult:
    """Results from a single backtest run."""
    trades: list
    n: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    avg_pnl: float = 0.0
    total_pnl: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    final_equity: float = 0.0
    blocked_reasons: dict = field(default_factory=dict)
    gate_attribution: dict = field(default_factory=dict)


def load_scenarios() -> list[dict]:
    """Load enriched gap scenarios."""
    path = _DATA / "scenarios" / "gap_scenarios.json"
    with open(path) as f:
        data = json.load(f)
    return data if isinstance(data, list) else data.get("scenarios", [])


def load_vix() -> dict[str, float]:
    """Load VIX history keyed by date string."""
    vix = {}
    path = _DATA / "regime" / "vix_history.csv"
    if path.exists():
        with open(path) as f:
            for row in csv.DictReader(f):
                vix[row["Date"][:10]] = float(row["Close"])
    return vix


def run_backtest(scenarios: list[dict], config: BacktestConfig, vix: dict) -> BacktestResult:
    """Run a full backtest on scenarios with the given config."""
    trades = []
    blocked_reasons: dict[str, int] = defaultdict(int)
    gate_attribution: dict[str, dict] = defaultdict(lambda: {"blocked": 0, "would_win": 0, "would_lose": 0, "phantom_pnl": 0.0})

    for s in scenarios:
        date = s["date"]
        gap = abs(s.get("gap_pct", 0))
        rvol = s.get("rvol", 0)
        v = vix.get(date)

        try:
            dow = datetime.strptime(date, "%Y-%m-%d").weekday()
        except ValueError:
            dow = None

        # Gate checks — track which gate would block and what the phantom P&L would be
        blocked_by = None
        if v is not None and v >= config.vix_block:
            blocked_by = "VIX_BLOCK"
        elif dow is not None and dow not in config.allowed_days:
            blocked_by = "DAY_OF_WEEK"
        elif gap < config.gap_min:
            blocked_by = "GAP_TOO_SMALL"
        elif gap > config.gap_max:
            blocked_by = "GAP_TOO_LARGE"
        elif rvol < config.rvol_min:
            blocked_by = "RVOL_LOW"

        # D212 gates
        prior_gaps = s.get("prior_gap_count")
        if blocked_by is None and prior_gaps is not None and prior_gaps >= config.serial_gap_threshold:
            blocked_by = "SERIAL_GAPPER"
        if blocked_by is None and config.block_day2_runners and s.get("is_day2_runner", False):
            blocked_by = "DAY2_RUNNER"

        # Compute phantom P&L for gate attribution (even if blocked)
        intraday_ret = s.get("intraday_return", 0)
        high_from_open = s.get("high_from_open_pct", 0)
        low_from_open = s.get("low_from_open_pct", 0)

        # Simulate with slippage
        effective_stop = -(config.stop_pct + config.stop_slippage)
        effective_target = config.target_pct - config.entry_slippage

        if low_from_open <= effective_stop:
            pnl_pct = effective_stop
            exit_reason = "STOP"
        elif high_from_open >= effective_target:
            pnl_pct = effective_target
            exit_reason = "TARGET"
        else:
            pnl_pct = intraday_ret - config.eod_slippage
            exit_reason = "EOD"

        # VIX sizing
        size_mult = 1.0
        if v is not None and v >= config.vix_reduce:
            size_mult = 0.5

        sized_pnl = pnl_pct * size_mult

        if blocked_by:
            blocked_reasons[blocked_by] += 1
            attr = gate_attribution[blocked_by]
            attr["blocked"] += 1
            attr["phantom_pnl"] += sized_pnl
            if s["outcome"] == "WIN":
                attr["would_win"] += 1
            else:
                attr["would_lose"] += 1
            continue

        trades.append({
            "ticker": s["ticker"],
            "date": date,
            "gap_pct": gap,
            "rvol": rvol,
            "vix": v,
            "outcome": s["outcome"],
            "pnl_pct": pnl_pct,
            "exit_reason": exit_reason,
            "size_mult": size_mult,
            "sized_pnl_pct": sized_pnl,
            "prior_gap_count": prior_gaps,
            "is_day2_runner": s.get("is_day2_runner", False),
        })

    # Compute metrics
    n = len(trades)
    if n == 0:
        return BacktestResult(trades=[], blocked_reasons=dict(blocked_reasons), gate_attribution=dict(gate_attribution))

    wins = sum(1 for t in trades if t["pnl_pct"] > 0)
    losses = n - wins
    total_pnl = sum(t["sized_pnl_pct"] for t in trades)
    win_sum = sum(t["sized_pnl_pct"] for t in trades if t["pnl_pct"] > 0)
    loss_sum = sum(t["sized_pnl_pct"] for t in trades if t["pnl_pct"] <= 0)
    pf = abs(win_sum / loss_sum) if loss_sum != 0 else float("inf")

    # Equity curve
    equity = 10_000
    peak = equity
    max_dd = 0.0
    for t in sorted(trades, key=lambda x: x["date"]):
        equity += 1000 * t["sized_pnl_pct"]
        peak = max(peak, equity)
        dd = (peak - equity) / peak if peak > 0 else 0
        max_dd = max(max_dd, dd)

    return BacktestResult(
        trades=trades,
        n=n, wins=wins, losses=losses,
        win_rate=wins / n,
        avg_pnl=total_pnl / n,
        total_pnl=total_pnl,
        profit_factor=pf,
        max_drawdown=max_dd,
        final_equity=equity,
        blocked_reasons=dict(blocked_reasons),
        gate_attribution=dict(gate_attribution),
    )


def print_results(result: BacktestResult, config: BacktestConfig, total_scenarios: int, label: str = ""):
    """Print formatted backtest results."""
    header = f"  {label}" if label else "  RESULTS"
    print(f"\n  {'='*60}")
    print(f"{header} ({result.n} trades from {total_scenarios} scenarios)")
    print(f"  {'='*60}")

    if result.n == 0:
        print("  No trades passed all gates.")
        return

    print(f"  Win Rate:      {result.win_rate:.0%} ({result.wins}W / {result.losses}L)")
    print(f"  Avg P&L/trade: {result.avg_pnl:+.2%}")
    print(f"  Total P&L:     {result.total_pnl:+.1%}")
    avg_win = sum(t["sized_pnl_pct"] for t in result.trades if t["pnl_pct"] > 0) / max(result.wins, 1)
    avg_loss = sum(t["sized_pnl_pct"] for t in result.trades if t["pnl_pct"] <= 0) / max(result.losses, 1)
    print(f"  Avg Winner:    {avg_win:+.2%}")
    print(f"  Avg Loser:     {avg_loss:+.2%}")
    print(f"  Profit Factor: {result.profit_factor:.2f}x")
    print(f"  Max Drawdown:  {result.max_drawdown:.1%}")
    print(f"  Final Equity:  ${result.final_equity:,.0f} (from $10K)")

    if config.entry_slippage > 0:
        print(f"  Slippage:      entry={config.entry_slippage:.1%} stop={config.stop_slippage:.1%} EOD={config.eod_slippage:.1%}")

    # Exit reasons
    print(f"\n  Exit Reasons:")
    for reason in ["TARGET", "EOD", "STOP"]:
        subset = [t for t in result.trades if t["exit_reason"] == reason]
        if subset:
            avg = sum(t["sized_pnl_pct"] for t in subset) / len(subset)
            wr = sum(1 for t in subset if t["pnl_pct"] > 0) / len(subset)
            print(f"    {reason:8s}: {len(subset):3d} trades, WR={wr:.0%}, avg P&L {avg:+.2%}")

    # Gate attribution (what each gate saved/cost)
    if result.gate_attribution:
        print(f"\n  Gate Attribution (what blocked trades would have done):")
        print(f"  {'Gate':<20s} {'Blocked':>7s} {'WR':>5s} {'Phantom P&L':>12s} {'Verdict':>8s}")
        print(f"  {'-'*55}")
        for gate, attr in sorted(result.gate_attribution.items(), key=lambda x: x[1]["phantom_pnl"]):
            n_blocked = attr["blocked"]
            wr = attr["would_win"] / n_blocked * 100 if n_blocked > 0 else 0
            pnl = attr["phantom_pnl"]
            verdict = "KEEP" if pnl < 0 else "RELAX"
            symbol = "+" if pnl < 0 else "-"
            print(f"  {gate:<20s} {n_blocked:>7d} {wr:>4.0f}% ${pnl*1000:>+10.0f}  {verdict:>6s} {symbol}")


def main() -> None:
    parser = argparse.ArgumentParser(description="D213: Enhanced Profitability Proof")
    parser.add_argument("--walk-forward", action="store_true", help="Walk-forward train/test split")
    parser.add_argument("--sweep-gaps", action="store_true", help="Sweep serial gapper thresholds 1-6")
    parser.add_argument("--no-slippage", action="store_true", help="Disable slippage for comparison")
    parser.add_argument("--no-d212", action="store_true", help="Disable D212 gates (serial gapper, day-2)")
    args = parser.parse_args()

    print("=" * 70)
    print("  D213: ENHANCED PROFITABILITY PROOF")
    print("  Slippage model + D212 serial gapper gate + walk-forward validation")
    print("=" * 70)

    scenarios = load_scenarios()
    vix = load_vix()

    config = BacktestConfig(
        label="D213 Full Stack",
        serial_gap_threshold=3 if not args.no_d212 else 999,
        block_day2_runners=not args.no_d212,
    )
    if args.no_slippage:
        config.entry_slippage = 0.0
        config.stop_slippage = 0.0
        config.eod_slippage = 0.0

    # ── MODE 1: Sweep serial gapper thresholds ──
    if args.sweep_gaps:
        print(f"\n  SERIAL GAPPER THRESHOLD SWEEP")
        print(f"  {'Threshold':>10s} {'Trades':>7s} {'WR':>6s} {'Avg P&L':>9s} {'Total':>9s} {'PF':>6s} {'Blocked':>8s} {'Block WR':>9s}")
        print(f"  {'-'*70}")

        for threshold in [1, 2, 3, 4, 5, 6, 99]:
            cfg = BacktestConfig(
                serial_gap_threshold=threshold,
                block_day2_runners=threshold < 99,
                entry_slippage=config.entry_slippage,
                stop_slippage=config.stop_slippage,
                eod_slippage=config.eod_slippage,
            )
            result = run_backtest(scenarios, cfg, vix)
            serial_blocked = result.gate_attribution.get("SERIAL_GAPPER", {})
            sb_n = serial_blocked.get("blocked", 0)
            sb_wr = serial_blocked.get("would_win", 0) / max(sb_n, 1) * 100

            label = f">={threshold}" if threshold < 99 else "OFF"
            print(
                f"  {label:>10s} {result.n:>7d} {result.win_rate:>5.0%} "
                f"{result.avg_pnl:>+8.2%} {result.total_pnl:>+8.1%} "
                f"{result.profit_factor:>5.2f}x {sb_n:>8d} {sb_wr:>8.0f}%"
            )

        print(f"\n  Interpretation: Pick threshold where blocking has lowest WR (worst trades removed).")
        print("=" * 70)
        return

    # ── MODE 2: Walk-forward validation ──
    if args.walk_forward:
        scenarios_sorted = sorted(scenarios, key=lambda s: s["date"])
        split_idx = int(len(scenarios_sorted) * 0.66)  # 2/3 train, 1/3 test
        train = scenarios_sorted[:split_idx]
        test = scenarios_sorted[split_idx:]

        print(f"\n  Walk-Forward Split:")
        print(f"    Train: {len(train)} scenarios ({train[0]['date']} to {train[-1]['date']})")
        print(f"    Test:  {len(test)} scenarios ({test[0]['date']} to {test[-1]['date']})")

        train_result = run_backtest(train, config, vix)
        test_result = run_backtest(test, config, vix)

        print_results(train_result, config, len(train), "TRAIN SET")
        print_results(test_result, config, len(test), "TEST SET (out-of-sample)")

        if test_result.n > 0 and test_result.win_rate > 0:
            overfit = train_result.win_rate / test_result.win_rate if test_result.win_rate > 0 else 99
            print(f"\n  Overfit Detection:")
            print(f"    Train WR: {train_result.win_rate:.0%}  |  Test WR: {test_result.win_rate:.0%}")
            print(f"    Overfit ratio: {overfit:.2f}x {'(OK)' if overfit <= 1.3 else '(WARNING: possible overfit)' if overfit <= 1.5 else '(OVERFIT DETECTED)'}")
            print(f"    Train PF: {train_result.profit_factor:.2f}x  |  Test PF: {test_result.profit_factor:.2f}x")

        # Also run without D212 for comparison
        config_no_d212 = BacktestConfig(
            serial_gap_threshold=999, block_day2_runners=False,
            entry_slippage=config.entry_slippage,
            stop_slippage=config.stop_slippage,
            eod_slippage=config.eod_slippage,
        )
        test_no_d212 = run_backtest(test, config_no_d212, vix)
        if test_no_d212.n > 0:
            print(f"\n  D212 Gate Impact (test set only):")
            print(f"    Without D212: {test_no_d212.n} trades, WR={test_no_d212.win_rate:.0%}, P&L={test_no_d212.avg_pnl:+.2%}")
            print(f"    With D212:    {test_result.n} trades, WR={test_result.win_rate:.0%}, P&L={test_result.avg_pnl:+.2%}")
            delta_wr = (test_result.win_rate - test_no_d212.win_rate) * 100
            print(f"    D212 added: {delta_wr:+.0f}pp win rate, {test_result.n - test_no_d212.n:+d} trades")

        print("=" * 70)
        return

    # ── MODE 3: Standard full run ──
    result = run_backtest(scenarios, config, vix)
    print_results(result, config, len(scenarios), "D213 FULL STACK")

    # Compare with/without slippage
    if not args.no_slippage:
        config_no_slip = BacktestConfig(
            entry_slippage=0, stop_slippage=0, eod_slippage=0,
            serial_gap_threshold=config.serial_gap_threshold,
            block_day2_runners=config.block_day2_runners,
        )
        result_no_slip = run_backtest(scenarios, config_no_slip, vix)
        print(f"\n  Slippage Impact:")
        print(f"    Without slippage: WR={result_no_slip.win_rate:.0%}, Avg={result_no_slip.avg_pnl:+.2%}, PF={result_no_slip.profit_factor:.2f}x")
        print(f"    With slippage:    WR={result.win_rate:.0%}, Avg={result.avg_pnl:+.2%}, PF={result.profit_factor:.2f}x")
        drag = (result_no_slip.avg_pnl - result.avg_pnl) * 100
        print(f"    Slippage drag: {drag:.1f}pp per trade")

    # Compare with/without D212
    config_no_d212 = BacktestConfig(
        serial_gap_threshold=999, block_day2_runners=False,
        entry_slippage=config.entry_slippage,
        stop_slippage=config.stop_slippage,
        eod_slippage=config.eod_slippage,
    )
    result_no_d212 = run_backtest(scenarios, config_no_d212, vix)
    print(f"\n  D212 Gate Impact:")
    print(f"    Without D212: {result_no_d212.n} trades, WR={result_no_d212.win_rate:.0%}, Avg={result_no_d212.avg_pnl:+.2%}")
    print(f"    With D212:    {result.n} trades, WR={result.win_rate:.0%}, Avg={result.avg_pnl:+.2%}")

    # Best/worst
    sorted_trades = sorted(result.trades, key=lambda x: x["sized_pnl_pct"], reverse=True)
    print(f"\n  Top 5 Winners:")
    for t in sorted_trades[:5]:
        gaps = t.get("prior_gap_count", "?")
        print(f"    {t['ticker']:6s} ({t['date']}): {t['exit_reason']:6s} P&L={t['sized_pnl_pct']:+.2%} gap={t['gap_pct']:.0%} gaps={gaps}")
    print(f"\n  Top 5 Losers:")
    for t in sorted_trades[-5:]:
        gaps = t.get("prior_gap_count", "?")
        print(f"    {t['ticker']:6s} ({t['date']}): {t['exit_reason']:6s} P&L={t['sized_pnl_pct']:+.2%} gap={t['gap_pct']:.0%} gaps={gaps}")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
