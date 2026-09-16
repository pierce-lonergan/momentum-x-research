"""
D211 Phantom Portfolio Replay — Gate Attribution via Counterfactual Trading

For every BUY verdict captured by PhantomJournal, replay the position
through actual minute bars (collected by D210) and attribute P&L to
the gate that blocked it.

Usage:
    python scripts/phantom_replay.py --date 2026-04-08
    python scripts/phantom_replay.py  # defaults to today

Output: Gate attribution report showing which gates saved/cost money.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))


def load_phantom_entries(date: str) -> list[dict]:
    """Load phantom verdicts for the given date."""
    from src.data.phantom_journal import PhantomJournal
    return PhantomJournal.load(date)


def load_minute_bars(date: str, ticker: str) -> list[dict] | None:
    """Load minute bars from session collector or bar recorder."""
    # Try session collector first
    session_path = Path(f"data/historical_collection/{date}/{ticker}.json")
    if session_path.exists():
        try:
            data = json.loads(session_path.read_text())
            bars = data.get("minute_bars", [])
            if bars:
                return bars
        except Exception:
            pass

    # Try bar recorder
    bar_path = Path(f"data/bar_recordings/{date}/{ticker}.json")
    if bar_path.exists():
        try:
            data = json.loads(bar_path.read_text())
            bars = data.get("bars", [])
            if bars:
                return bars
        except Exception:
            pass

    return None


def simulate_position(
    entry_price: float,
    stop_loss: float,
    target_prices: list[float],
    bars: list[dict],
    direction: str = "long",
) -> dict:
    """
    Simulate a position through minute bars.

    Returns:
        dict with exit_price, exit_reason, pnl_pct, exit_minute, max_gain, max_drawdown
    """
    if not bars or entry_price <= 0:
        return {
            "exit_price": entry_price,
            "exit_reason": "NO_BARS",
            "pnl_pct": 0.0,
            "exit_minute": 0,
            "max_gain_pct": 0.0,
            "max_drawdown_pct": 0.0,
        }

    is_long = direction == "long"
    max_gain = 0.0
    max_drawdown = 0.0
    targets_hit = []
    exit_price = entry_price
    exit_reason = "EOD_CLOSE"
    exit_minute = len(bars)

    for i, bar in enumerate(bars):
        high = bar.get("high", bar.get("h", entry_price))
        low = bar.get("low", bar.get("l", entry_price))
        close = bar.get("close", bar.get("c", entry_price))

        if is_long:
            # Check stop loss (uses low of bar)
            if low <= stop_loss:
                exit_price = stop_loss
                exit_reason = "STOP_LOSS"
                exit_minute = i
                break

            # Check targets (uses high of bar)
            for t_idx, target in enumerate(target_prices):
                if t_idx not in [th[0] for th in targets_hit] and high >= target:
                    targets_hit.append((t_idx, target, i))

            # Track max gain/drawdown
            gain_pct = (high - entry_price) / entry_price
            dd_pct = (entry_price - low) / entry_price
            max_gain = max(max_gain, gain_pct)
            max_drawdown = max(max_drawdown, dd_pct)

            # Exit at last bar close
            exit_price = close
        else:
            # Short position
            if high >= stop_loss:
                exit_price = stop_loss
                exit_reason = "STOP_LOSS"
                exit_minute = i
                break

            for t_idx, target in enumerate(target_prices):
                if t_idx not in [th[0] for th in targets_hit] and low <= target:
                    targets_hit.append((t_idx, target, i))

            gain_pct = (entry_price - low) / entry_price
            dd_pct = (high - entry_price) / entry_price
            max_gain = max(max_gain, gain_pct)
            max_drawdown = max(max_drawdown, dd_pct)

            exit_price = close

    # If targets were hit, use weighted exit
    if targets_hit and exit_reason == "EOD_CLOSE":
        # Assume equal tranche sizes
        tranche_pct = 1.0 / (len(target_prices) + 1)  # +1 for remainder
        weighted_exit = 0.0
        remaining_pct = 1.0
        for _, target, _ in sorted(targets_hit):
            weighted_exit += target * tranche_pct
            remaining_pct -= tranche_pct
        weighted_exit += exit_price * remaining_pct
        exit_price = weighted_exit
        exit_reason = f"TRANCHE_{len(targets_hit)}_OF_{len(target_prices)}"

    if is_long:
        pnl_pct = (exit_price - entry_price) / entry_price
    else:
        pnl_pct = (entry_price - exit_price) / entry_price

    return {
        "exit_price": round(exit_price, 4),
        "exit_reason": exit_reason,
        "pnl_pct": round(pnl_pct, 4),
        "exit_minute": exit_minute,
        "max_gain_pct": round(max_gain, 4),
        "max_drawdown_pct": round(max_drawdown, 4),
        "targets_hit": len(targets_hit),
    }


def run_replay(date: str, equity: float = 142_000.0) -> None:
    """Run the full phantom replay and print attribution report."""
    entries = load_phantom_entries(date)
    if not entries:
        print(f"\nNo phantom entries found for {date}")
        print(f"  Expected file: data/phantom/phantom_{date}.jsonl")
        return

    # Deduplicate: keep only the LAST entry per ticker (most final gate status)
    ticker_entries: dict[str, dict] = {}
    for e in entries:
        ticker_entries[e["ticker"]] = e
    entries = list(ticker_entries.values())

    print(f"\n{'=' * 60}")
    print(f"  PHANTOM PORTFOLIO — Gate Attribution  {date}")
    print(f"{'=' * 60}")
    print(f"\n  Phantom verdicts: {len(entries)}")

    # Simulate each position
    results = []
    for entry in entries:
        bars = load_minute_bars(date, entry["ticker"])
        if bars is None:
            sim = {
                "exit_price": entry["entry_price"],
                "exit_reason": "NO_BARS",
                "pnl_pct": 0.0,
                "exit_minute": 0,
                "max_gain_pct": 0.0,
                "max_drawdown_pct": 0.0,
                "targets_hit": 0,
            }
        else:
            sim = simulate_position(
                entry_price=entry["entry_price"],
                stop_loss=entry["stop_loss"],
                target_prices=entry.get("target_prices", []),
                bars=bars,
                direction=entry.get("direction", "long"),
            )

        position_pct = entry.get("position_size_pct", 0.01)
        notional = equity * position_pct
        pnl_dollars = notional * sim["pnl_pct"]

        result = {
            **entry,
            **sim,
            "notional": round(notional, 2),
            "pnl_dollars": round(pnl_dollars, 2),
        }
        results.append(result)

    # Separate executed vs blocked
    executed = [r for r in results if r["blocked_by"] in ("EXECUTED", "PENDING")]
    blocked = [r for r in results if r["blocked_by"] not in ("EXECUTED", "PENDING")]

    # Overall stats
    all_wins = sum(1 for r in results if r["pnl_pct"] > 0)
    all_pnl = sum(r["pnl_dollars"] for r in results)
    exec_wins = sum(1 for r in executed if r["pnl_pct"] > 0)
    exec_pnl = sum(r["pnl_dollars"] for r in executed)
    blocked_wins = sum(1 for r in blocked if r["pnl_pct"] > 0)
    blocked_pnl = sum(r["pnl_dollars"] for r in blocked)

    print(f"\n  {'Portfolio':<25s} {'Trades':>6s} {'WR':>6s} {'P&L':>10s}")
    print(f"  {'-'*50}")
    if executed:
        print(f"  {'ACTUAL (executed)':<25s} {len(executed):>6d} {exec_wins/len(executed)*100:>5.0f}% ${exec_pnl:>+9.0f}")
    if blocked:
        print(f"  {'BLOCKED (phantom)':<25s} {len(blocked):>6d} {blocked_wins/max(len(blocked),1)*100:>5.0f}% ${blocked_pnl:>+9.0f}")
    print(f"  {'ALL (if no gates)':<25s} {len(results):>6d} {all_wins/max(len(results),1)*100:>5.0f}% ${all_pnl:>+9.0f}")

    # Gate attribution
    gate_stats: dict[str, dict] = defaultdict(lambda: {
        "count": 0, "wins": 0, "pnl": 0.0, "tickers": [],
    })
    for r in blocked:
        gate = r["blocked_by"]
        gate_stats[gate]["count"] += 1
        gate_stats[gate]["pnl"] += r["pnl_dollars"]
        if r["pnl_pct"] > 0:
            gate_stats[gate]["wins"] += 1
        gate_stats[gate]["tickers"].append(
            f"{r['ticker']}({r['pnl_pct']*100:+.1f}%)"
        )

    if gate_stats:
        print(f"\n  GATE ATTRIBUTION (P&L saved/lost by each gate):")
        print(f"  {'Gate':<28s} {'Blocked':>7s} {'WR':>5s} {'Phantom P&L':>12s} {'Verdict':>10s}")
        print(f"  {'-'*65}")
        for gate, stats in sorted(gate_stats.items(), key=lambda x: x[1]["pnl"]):
            wr = stats["wins"] / max(stats["count"], 1) * 100
            verdict = "KEEP" if stats["pnl"] < 0 else "RELAX!"
            symbol = "✓" if stats["pnl"] < 0 else "⚠"
            print(f"  {gate:<28s} {stats['count']:>7d} {wr:>4.0f}% ${stats['pnl']:>+10.0f}  {verdict:>6s} {symbol}")

        print(f"\n  BLOCKED TRADE DETAILS:")
        for r in sorted(results, key=lambda x: x["pnl_dollars"], reverse=True):
            if r["blocked_by"] not in ("EXECUTED", "PENDING"):
                emoji = "✅" if r["pnl_pct"] > 0 else "❌"
                print(
                    f"    {emoji} {r['ticker']:6s} | {r['blocked_by']:<25s} | "
                    f"MFCS={r['mfcs']:.3f} | Entry=${r['entry_price']:.2f} → "
                    f"${r['exit_price']:.2f} ({r['pnl_pct']*100:+.1f}%) | "
                    f"${r['pnl_dollars']:+.0f} | {r['exit_reason']}"
                )

    # Top missed trades (positive phantom P&L that was blocked)
    missed = [r for r in blocked if r["pnl_pct"] > 0]
    if missed:
        missed.sort(key=lambda x: x["pnl_dollars"], reverse=True)
        print(f"\n  TOP MISSED TRADES (blocked winners):")
        for r in missed[:5]:
            print(
                f"    {r['ticker']:6s}: blocked by {r['blocked_by']}. "
                f"Would have: {r['pnl_pct']*100:+.1f}% (${r['pnl_dollars']:+.0f}) "
                f"Max gain: {r['max_gain_pct']*100:.1f}%"
            )

    # Saves (blocked losers)
    saves = [r for r in blocked if r["pnl_pct"] < 0]
    if saves:
        saves.sort(key=lambda x: x["pnl_dollars"])
        print(f"\n  TOP SAVES (blocked losers):")
        for r in saves[:5]:
            print(
                f"    {r['ticker']:6s}: blocked by {r['blocked_by']}. "
                f"Avoided: {r['pnl_pct']*100:+.1f}% (${r['pnl_dollars']:+.0f}) "
                f"Max DD: {r['max_drawdown_pct']*100:.1f}%"
            )

    print(f"\n{'=' * 60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="D211 Phantom Portfolio Replay")
    parser.add_argument(
        "--date",
        default=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        help="Session date (YYYY-MM-DD). Default: today.",
    )
    parser.add_argument(
        "--equity",
        type=float,
        default=142_000.0,
        help="Account equity for P&L sizing. Default: $142,000.",
    )
    args = parser.parse_args()
    run_replay(args.date, args.equity)
