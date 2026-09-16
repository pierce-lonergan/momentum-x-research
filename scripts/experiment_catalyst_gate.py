#!/usr/bin/env python
"""D200-E4: Catalyst Confirmation Gate Experiment.

All 14 live losses had catalyst_type="unknown". This experiment measures
win rate WITH vs WITHOUT confirmed catalyst across the 196 labeled scenarios
and 4548 journal entries.

Usage:
    python scripts/experiment_catalyst_gate.py
"""

from __future__ import annotations

import json
import glob
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_DATA = Path(__file__).resolve().parent.parent / "data"


def load_scenario_outcomes() -> dict[str, dict]:
    """Load scenario outcomes keyed by ticker."""
    outcomes: dict[str, dict] = {}
    sc_path = _DATA / "scenarios" / "gap_scenarios.json"
    if sc_path.exists():
        with open(sc_path) as f:
            data = json.load(f)
        scenarios = data if isinstance(data, list) else data.get("scenarios", [])
        for s in scenarios:
            key = f"{s['ticker']}_{s['date']}"
            outcomes[key] = s
    return outcomes


def extract_catalyst_from_journal(entry: dict) -> str:
    """Extract catalyst_type from news_agent signal in a journal entry."""
    for sig in (entry.get("agent_signals") or []):
        if sig.get("agent_id") == "news_agent":
            ct = sig.get("catalyst_type")
            if ct and ct not in ("NONE", "None", "null", None):
                return ct
            kd = sig.get("key_data", {})
            if isinstance(kd, dict):
                ct2 = kd.get("catalyst_type")
                if ct2 and ct2 not in ("NONE", "None", "null", None):
                    return ct2
    return "unknown"


def main() -> None:
    print("=" * 70)
    print("D200-E4: CATALYST CONFIRMATION GATE EXPERIMENT")
    print("=" * 70)

    # Load scenarios for outcome mapping
    outcomes = load_scenario_outcomes()
    print(f"Loaded {len(outcomes)} scenario outcomes")

    # Load trade results for live outcome mapping
    live_outcomes: dict[str, dict] = {}
    tr_path = _DATA / "trade_results.jsonl"
    if tr_path.exists():
        with open(tr_path) as f:
            for line in f:
                tr = json.loads(line)
                key = f"{tr['ticker']}_{tr['session_date']}"
                live_outcomes[key] = tr

    # Analyze journal entries
    catalyst_stats: dict[str, dict] = defaultdict(lambda: {
        "total": 0, "buy_verdicts": 0, "wins": 0, "losses": 0,
        "total_pnl": 0.0, "tickers": [],
    })

    total_entries = 0
    entries_with_outcome = 0

    for jf in sorted(glob.glob(str(_DATA / "journals" / "journal_*.jsonl"))):
        with open(jf) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                total_entries += 1
                ticker = entry.get("ticker", "")
                date = entry.get("session_date", "")
                action = entry.get("action", "")
                catalyst = extract_catalyst_from_journal(entry)

                stats = catalyst_stats[catalyst]
                stats["total"] += 1

                if action in ("BUY", "STRONG_BUY"):
                    stats["buy_verdicts"] += 1

                # Check for outcome
                key = f"{ticker}_{date}"
                outcome = live_outcomes.get(key) or outcomes.get(key)
                if outcome:
                    entries_with_outcome += 1
                    is_win = outcome.get("is_win", outcome.get("outcome") == "WIN")
                    pnl = outcome.get("pnl", 0.0)
                    if is_win:
                        stats["wins"] += 1
                    else:
                        stats["losses"] += 1
                    if pnl:
                        stats["total_pnl"] += pnl
                    stats["tickers"].append(ticker)

    print(f"\nTotal journal entries: {total_entries}")
    print(f"Entries with outcomes: {entries_with_outcome}")
    print()

    # ── Results ─────────────────────────────────────────────────────
    print("─── CATALYST TYPE → WIN RATE ───")
    print(f"  {'Catalyst':<25s} | {'n':>5s} | {'BUY':>5s} | {'Win%':>6s} | {'W':>3s}/{' L':>3s} | {'P&L':>10s}")
    print("  " + "-" * 75)

    for ct in sorted(catalyst_stats.keys(), key=lambda x: catalyst_stats[x]["wins"] + catalyst_stats[x]["losses"], reverse=True):
        s = catalyst_stats[ct]
        outcome_total = s["wins"] + s["losses"]
        win_rate = s["wins"] / outcome_total if outcome_total > 0 else 0.0
        pnl_str = f"${s['total_pnl']:+,.0f}" if s["total_pnl"] != 0 else "N/A"
        print(
            f"  {ct:<25s} | {s['total']:5d} | {s['buy_verdicts']:5d} | "
            f"{win_rate:5.0%} | {s['wins']:3d}/{s['losses']:3d} | {pnl_str:>10s}"
        )

    # ── Impact Analysis ─────────────────────────────────────────────
    print()
    print("─── IMPACT: WHAT IF WE REQUIRED CATALYST? ───")

    unknown = catalyst_stats.get("unknown", {})
    none_ct = catalyst_stats.get("NONE", {})
    no_catalyst_buys = unknown.get("buy_verdicts", 0) + none_ct.get("buy_verdicts", 0)
    no_catalyst_losses = unknown.get("losses", 0) + none_ct.get("losses", 0)
    no_catalyst_wins = unknown.get("wins", 0) + none_ct.get("wins", 0)
    no_catalyst_pnl = unknown.get("total_pnl", 0) + none_ct.get("total_pnl", 0)

    total_buys = sum(s["buy_verdicts"] for s in catalyst_stats.values())
    total_wins = sum(s["wins"] for s in catalyst_stats.values())
    total_losses = sum(s["losses"] for s in catalyst_stats.values())
    total_pnl = sum(s["total_pnl"] for s in catalyst_stats.values())

    with_catalyst_wins = total_wins - no_catalyst_wins
    with_catalyst_losses = total_losses - no_catalyst_losses
    with_catalyst_total = with_catalyst_wins + with_catalyst_losses
    with_catalyst_wr = with_catalyst_wins / with_catalyst_total if with_catalyst_total > 0 else 0

    print(f"  BUY verdicts without catalyst: {no_catalyst_buys}/{total_buys} ({no_catalyst_buys/total_buys:.0%} of all BUYs)" if total_buys > 0 else "  No BUY verdicts found")
    print(f"  Losses from no-catalyst trades: {no_catalyst_losses}")
    print(f"  P&L from no-catalyst trades: ${no_catalyst_pnl:+,.0f}")
    print()
    print(f"  WITH catalyst win rate:    {with_catalyst_wr:.0%} ({with_catalyst_wins}/{with_catalyst_total})")
    print(f"  WITHOUT catalyst win rate: {(no_catalyst_wins/(no_catalyst_wins+no_catalyst_losses)):.0%} ({no_catalyst_wins}/{no_catalyst_wins+no_catalyst_losses})" if (no_catalyst_wins + no_catalyst_losses) > 0 else "  WITHOUT catalyst: no outcomes")
    print()
    print(f"  Estimated P&L saved by requiring catalyst: ${-no_catalyst_pnl:+,.0f}")

    print()
    print("RECOMMENDATION:")
    if no_catalyst_losses > no_catalyst_wins and (no_catalyst_wins + no_catalyst_losses) >= 5:
        print("  ✓ ENABLE catalyst gate — would eliminate net-losing no-catalyst trades")
        print("  Set UNIVERSE_REQUIRE_CATALYST=true in .env")
    else:
        print("  △ Insufficient data to make recommendation. Need more labeled outcomes.")

    print("=" * 70)


if __name__ == "__main__":
    main()
