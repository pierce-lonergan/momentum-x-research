#!/usr/bin/env python3
"""
Single-day replay — run the arena standalone or with the bot.

Usage:
    # Standalone (no bot, just drive exchange with data)
    python mx-arena/scripts/run_replay.py --date 2026-03-25 --symbols MKDW,FEED,VSA

    # With synthetic scenario
    python mx-arena/scripts/run_replay.py --scenario gap_and_go --symbols FAKE

    # HTTP mode (start server, then launch bot separately)
    python mx-arena/scripts/run_replay.py --date 2026-03-25 --http --port 8080
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arena.clock import ClockMode
from arena.harness import ArenaConfig, ArenaInstance
from arena.scenario import ScenarioConfig, ScenarioGenerator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def load_prev_daily_from_journals(date: str, journals_dir: str) -> dict:
    """Try to load previous daily bars from journal data."""
    # This is a placeholder — in practice, prev daily bars come from
    # the downloaded daily data
    return {}


async def run_historical_replay(args):
    """Replay a historical day using Parquet data."""
    symbols = [s.strip().upper() for s in args.symbols.split(",")]

    config = ArenaConfig(
        date=args.date,
        symbols=symbols,
        data_dir=args.data_dir,
        daily_dir=args.daily_dir,
        initial_cash=args.cash,
        seed=args.seed,
        clock_mode=ClockMode.REPLAY,
        fill_model=args.fill_model,
        rest_port=args.port,
    )

    instance = ArenaInstance(config)

    if args.http:
        logger.info("Starting HTTP mode on port %d...", args.port)
        result = await instance.run_http_mode()
    else:
        logger.info("Running standalone simulation...")
        result = await instance.run_standalone()

    # Print results
    print("\n" + "=" * 60)
    print(f"REPLAY RESULTS: {args.date}")
    print("=" * 60)
    print(f"  P&L:          ${result.pnl:+.2f}")
    print(f"  Trades:       {len(result.trades)}")
    print(f"  Win Rate:     {result.win_rate:.1%}")
    print(f"  Profit Factor: {result.profit_factor:.2f}")
    print(f"  Orders Total: {result.orders_total}")
    print(f"  Positions EOD: {len(result.positions_eod)}")

    if result.trades:
        print(f"\n  Trade Details:")
        for t in result.trades:
            pnl = t.get("pnl", "")
            pnl_str = f" P&L=${pnl:+.2f}" if pnl != "" else ""
            print(
                f"    {t['timestamp'][:19]} {t['side'].upper():4s} "
                f"{t['symbol']:6s} {t['qty']}x @ ${t['price']:.2f}{pnl_str}"
            )

    print("=" * 60)

    # Save results
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"replay_{args.date}.json"
    with open(out_file, "w") as f:
        json.dump(result.to_dict(), f, indent=2, default=str)
    print(f"\nResults saved to {out_file}")

    return result


async def run_scenario_replay(args):
    """Run a synthetic scenario."""
    symbols = [s.strip().upper() for s in args.symbols.split(",")]
    symbol = symbols[0] if symbols else "SCENARIO"

    scenario_config = ScenarioConfig(
        name=args.scenario,
        base_pattern=args.scenario,
        entry_price=args.entry_price,
        gap_pct=args.gap_pct,
        seed=args.seed,
    )

    generator = ScenarioGenerator()
    bars, prev_daily = generator.generate(scenario_config)

    logger.info(
        "Generated %d bars for %s scenario (entry=$%.2f, gap=%.0f%%)",
        len(bars), args.scenario, args.entry_price, args.gap_pct * 100,
    )

    # Create arena with synthetic data
    config = ArenaConfig(
        date="2026-03-25",
        symbols=[symbol],
        data_dir="",  # Not used — we inject bars directly
        initial_cash=args.cash,
        seed=args.seed,
        prev_daily_bars={symbol: prev_daily},
    )

    instance = ArenaInstance(config)

    # Inject bars directly into data engine
    from arena.fill_model import Bar
    bar_dict = {}
    for i, bar in enumerate(bars):
        bar_dict[i] = bar
    instance.data_engine._minute_bars[symbol] = bar_dict
    instance.data_engine._watchlist = [symbol]
    instance.data_engine.set_prev_daily(symbol, prev_daily)

    result = await instance.run_standalone()

    print(f"\nScenario '{args.scenario}' complete:")
    print(f"  Bars generated: {len(bars)}")
    print(f"  Entry price: ${args.entry_price:.2f}")
    print(f"  Gap: {args.gap_pct:.0%}")
    print(f"  Final price: ${bars[-1].close:.2f}")
    print(f"  Day return: {(bars[-1].close - bars[0].open) / bars[0].open:.2%}")

    return result


async def main():
    parser = argparse.ArgumentParser(description="mx-arena single-day replay")
    parser.add_argument("--date", type=str, default="2026-03-25", help="Replay date")
    parser.add_argument("--symbols", type=str, default="MKDW,FEED", help="Comma-separated symbols")
    parser.add_argument("--data-dir", type=str, default="mx-arena/data/historical")
    parser.add_argument("--daily-dir", type=str, default=None)
    parser.add_argument("--output", type=str, default="mx-arena/data/results")
    parser.add_argument("--cash", type=float, default=100_000.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--fill-model", type=str, default="alpaca", choices=["alpaca", "realistic"])
    parser.add_argument("--http", action="store_true", help="Run in HTTP mode (start FastAPI server)")
    parser.add_argument("--port", type=int, default=8080)

    # Scenario mode
    parser.add_argument("--scenario", type=str, help="Synthetic scenario: gap_and_go, gap_and_fade")
    parser.add_argument("--entry-price", type=float, default=5.0)
    parser.add_argument("--gap-pct", type=float, default=0.20)

    args = parser.parse_args()

    if args.scenario:
        await run_scenario_replay(args)
    else:
        await run_historical_replay(args)


if __name__ == "__main__":
    asyncio.run(main())
