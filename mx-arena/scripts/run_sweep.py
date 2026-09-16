#!/usr/bin/env python3
"""
Parameter sweep — run many simulations with different parameter values.

Replays journal BUY signals against real bar data with different stop levels,
position sizes, and other parameters. Finds optimal configurations.

Usage:
    # Sweep stop_loss_pct across Mar 26
    python mx-arena/scripts/run_sweep.py \
        --dates 2026-03-26 \
        --sweep stop_loss_pct=0.02,0.03,0.04,0.05,0.06,0.08

    # Multi-date sweep
    python mx-arena/scripts/run_sweep.py \
        --dates 2026-03-26,2026-03-27 \
        --sweep stop_loss_pct=0.03,0.04,0.05 \
        --sweep risk_per_trade_pct=0.01,0.02,0.03

    # Full matrix with parallel workers
    python mx-arena/scripts/run_sweep.py \
        --dates 2026-03-26,2026-03-27 \
        --sweep stop_loss_pct=0.02,0.04,0.06 \
        --workers 8
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arena.runner import SweepConfig, SweepRunner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
ARENA_DATA = PROJECT_ROOT / "mx-arena" / "data"


def load_date_config(date: str) -> tuple[list[str], dict]:
    """Load symbols and prev_daily from replay config or journals."""
    config_file = ARENA_DATA / "configs" / f"replay_{date}.json"
    if config_file.exists():
        with open(config_file) as f:
            cfg = json.load(f)
        return cfg.get("tickers", []), cfg.get("prev_daily", {})

    # Fallback: extract from journals
    tickers = set()
    for f in glob.glob(str(DATA_DIR / "journals" / f"journal_{date}_*.jsonl")):
        with open(f) as fh:
            for line in fh:
                try:
                    entry = json.loads(line.strip())
                    t = entry.get("ticker")
                    if t:
                        tickers.add(t)
                except:
                    pass
    return sorted(tickers), {}


def parse_sweep_args(sweep_strs: list[str]) -> dict[str, list]:
    """Parse --sweep param=val1,val2,val3 into param_grid."""
    grid = {}
    for s in sweep_strs:
        name, values = s.split("=", 1)
        # Try float, fall back to string
        parsed = []
        for v in values.split(","):
            try:
                parsed.append(float(v))
            except ValueError:
                parsed.append(v)
        grid[name] = parsed
    return grid


def main():
    parser = argparse.ArgumentParser(description="mx-arena parameter sweep")
    parser.add_argument("--dates", type=str, required=True, help="Comma-separated dates")
    parser.add_argument("--sweep", action="append", default=[], help="param=v1,v2,v3")
    parser.add_argument("--workers", type=int, default=4, help="Parallel workers")
    parser.add_argument("--fill-model", type=str, default="alpaca", choices=["alpaca", "realistic"])
    parser.add_argument("--output", type=str, default=str(ARENA_DATA / "results"))
    parser.add_argument(
        "--decision-replay", action="store_true",
        help="D132: Re-evaluate candidates with sweep params (changes WHICH trades are taken)",
    )
    parser.add_argument(
        "--walk-forward", action="store_true",
        help="D135: Walk-forward cross-validation (train on first 60%%, test on last 40%%)",
    )
    args = parser.parse_args()

    dates = [d.strip() for d in args.dates.split(",")]
    param_grid = parse_sweep_args(args.sweep)

    # Load per-date configs
    symbols_per_date = {}
    prev_daily_per_date = {}
    for date in dates:
        symbols, prev_daily = load_date_config(date)
        symbols_per_date[date] = symbols
        prev_daily_per_date[date] = prev_daily

    total_combos = 1
    for vals in param_grid.values():
        total_combos *= len(vals)

    print(f"\n{'='*70}")
    print(f"PARAMETER SWEEP")
    print(f"{'='*70}")
    print(f"  Dates:       {', '.join(dates)}")
    print(f"  Parameters:  {len(param_grid)} params")
    for name, vals in param_grid.items():
        print(f"    {name}: {vals}")
    print(f"  Combinations: {total_combos} x {len(dates)} dates = {total_combos * len(dates)} sims")
    print(f"  Workers:     {args.workers}")
    print(f"  Fill model:  {args.fill_model}")
    print(f"  Decision replay: {args.decision_replay}")
    if args.decision_replay:
        print(f"  Mode: RE-EVALUATING candidates (changes which trades are taken)")
    else:
        print(f"  Mode: Journal replay (fixed entries, sweep execution params)")
    print()

    # Build sweep config
    sweep_config = SweepConfig(
        dates=dates,
        symbols_per_date=symbols_per_date,
        prev_daily_per_date=prev_daily_per_date,
        param_grid=param_grid,
        data_dir=str(ARENA_DATA / "historical"),
        json_bars_dir=str(DATA_DIR / "bars"),
        daily_dir=str(ARENA_DATA / "daily"),
        initial_cash=100_000.0,
        fill_model=args.fill_model,
        max_workers=args.workers,
        decision_replay=args.decision_replay,
        journals_dir=str(DATA_DIR / "journals"),
    )

    # Walk-forward mode
    if args.walk_forward:
        from arena.walk_forward import WalkForwardConfig, run_walk_forward, format_walk_forward_results
        wf_config = WalkForwardConfig(
            all_dates=dates,
            param_grid=param_grid,
            data_dir=str(ARENA_DATA / "historical"),
            json_bars_dir=str(DATA_DIR / "bars"),
            journals_dir=str(DATA_DIR / "journals"),
            daily_dir=str(ARENA_DATA / "daily"),
            max_workers=args.workers,
            decision_replay=args.decision_replay,
        )
        t0 = time.perf_counter()
        wf_results = run_walk_forward(wf_config)
        elapsed = time.perf_counter() - t0
        print(format_walk_forward_results(wf_results))
        print(f"\nCompleted in {elapsed:.1f}s")

        out_dir = Path(args.output)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"walkforward_{'_'.join(dates[:2])}_{dates[-1]}.json"
        with open(out_file, "w") as f:
            json.dump([{
                "fold": r.fold, "train_dates": r.train_dates, "test_dates": r.test_dates,
                "best_params": r.best_params, "train_pf": r.train_pf, "test_pf": r.test_pf,
                "overfit_ratio": r.overfit_ratio, "train_trades": r.train_trades,
                "test_trades": r.test_trades,
            } for r in wf_results], f, indent=2, default=str)
        print(f"Results saved to {out_file}")
        return

    # Run sweep
    runner = SweepRunner(sweep_config)
    t0 = time.perf_counter()
    results = runner.run()
    elapsed = time.perf_counter() - t0

    # Display results
    print(f"\n{'='*70}")
    print(f"RESULTS ({len(results)} sims in {elapsed:.1f}s)")
    print(f"{'='*70}\n")
    print(runner.results_to_table(results))

    # Summary statistics
    print(f"\n{'='*70}")
    print("SUMMARY BY PARAMETER VALUE")
    print(f"{'='*70}")

    for param_name in param_grid:
        print(f"\n  {param_name}:")
        param_results = {}
        for r in results:
            val = r.get("params", {}).get(param_name, "default")
            if val not in param_results:
                param_results[val] = {"pnl": [], "win_rates": [], "trades": []}
            param_results[val]["pnl"].append(r.get("sim_pnl", 0))
            param_results[val]["win_rates"].append(r.get("sim_win_rate", 0))
            param_results[val]["trades"].append(len(r.get("sim_trades", [])))

        for val in sorted(param_results.keys()):
            data = param_results[val]
            avg_pnl = sum(data["pnl"]) / max(len(data["pnl"]), 1)
            avg_wr = sum(data["win_rates"]) / max(len(data["win_rates"]), 1)
            total_trades = sum(data["trades"])
            print(f"    {param_name}={val}: avg_pnl=${avg_pnl:+.2f}, "
                  f"win_rate={avg_wr:.0%}, trades={total_trades}")

    # Find best config
    best = max(results, key=lambda r: r.get("sim_pnl", float("-inf")))
    print(f"\n  BEST: {best.get('config_id', '?')}")
    print(f"    P&L: ${best.get('sim_pnl', 0):+.2f}")
    print(f"    Params: {best.get('params', {})}")

    # Save results
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"sweep_{'_'.join(dates)}.json"
    with open(out_file, "w") as f:
        json.dump({
            "dates": dates,
            "param_grid": param_grid,
            "total_sims": len(results),
            "elapsed_seconds": round(elapsed, 2),
            "results": results,
            "best": best,
        }, f, indent=2, default=str)

    print(f"\nResults saved to {out_file}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
