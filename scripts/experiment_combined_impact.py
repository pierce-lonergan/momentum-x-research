#!/usr/bin/env python
"""D203: Combined Impact Assessment — what would D199-D203 changes have done?

Replays 229 journal entries with outcomes through the new production gates:
1. D200-E4: Catalyst gate (require_catalyst=True)
2. D203: Day-of-week gate (Mon/Thu only)
3. D203: Technical agent de-weighted (0.05 instead of 0.25-0.35)
4. D201: MFCS threshold raised to 0.25
5. D200-E3: VIX gate (block >20, reduce >15)

Measures: how many trades would have been taken, and what's the projected outcome.

Usage:
    python scripts/experiment_combined_impact.py
"""

from __future__ import annotations

import json
import glob
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_DATA = Path(__file__).resolve().parent.parent / "data"


def load_vix_cache() -> dict[str, float]:
    """Load VIX data from cache."""
    vix = {}
    cache_path = _DATA / "regime" / "vix_history.csv"
    if cache_path.exists():
        import csv
        with open(cache_path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                date_str = row.get("Date", "")[:10]
                try:
                    vix[date_str] = float(row.get("Close", 0))
                except (ValueError, TypeError):
                    pass
    return vix


def main() -> None:
    print("=" * 70)
    print("D203: COMBINED IMPACT ASSESSMENT")
    print("  What would D199-D203 production changes have done?")
    print("=" * 70)

    # Load outcomes
    live_outcomes = {}
    with open(_DATA / "trade_results.jsonl") as f:
        for line in f:
            tr = json.loads(line)
            live_outcomes[(tr["ticker"], tr["session_date"])] = tr

    sc_outcomes = {}
    with open(_DATA / "scenarios" / "gap_scenarios.json") as f:
        data = json.load(f)
    for s in (data if isinstance(data, list) else data.get("scenarios", [])):
        sc_outcomes[(s["ticker"], s["date"])] = {
            "is_win": s["outcome"] == "WIN",
            "intraday_return": s.get("intraday_return", 0),
        }

    vix_data = load_vix_cache()

    # Gate definitions
    ALLOWED_DAYS = {0, 3}  # Mon, Thu
    VIX_BLOCK = 20.0
    MFCS_THRESHOLD = 0.25

    # Replay all BUY journal entries
    original = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}
    after_catalyst = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0, "blocked": 0}
    after_dow = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0, "blocked": 0}
    after_vix = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0, "blocked": 0}
    after_all = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0, "blocked": 0}

    for jf in sorted(glob.glob(str(_DATA / "journals" / "journal_*.jsonl"))):
        with open(jf) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if entry.get("action") not in ("BUY", "STRONG_BUY"):
                    continue

                ticker = entry.get("ticker", "")
                date = entry.get("session_date", "")
                mfcs = entry.get("mfcs", 0.0)

                outcome = live_outcomes.get((ticker, date)) or sc_outcomes.get((ticker, date))
                if not outcome:
                    continue

                is_win = outcome.get("is_win", False)
                pnl = outcome.get("pnl", 0.0) or 0.0

                # Extract catalyst
                catalyst = None
                for sig in (entry.get("agent_signals") or []):
                    if sig.get("agent_id") == "news_agent":
                        ct = sig.get("catalyst_type")
                        if ct and ct not in ("NONE", "None", "null", None):
                            catalyst = ct

                # Get day of week
                try:
                    dow = datetime.strptime(date, "%Y-%m-%d").weekday()
                except ValueError:
                    dow = None

                # Get VIX
                vix = vix_data.get(date)

                # Original (no gates)
                original["trades"] += 1
                if is_win:
                    original["wins"] += 1
                else:
                    original["losses"] += 1
                original["pnl"] += pnl

                # Gate 1: Catalyst only
                has_catalyst = catalyst is not None
                if has_catalyst:
                    after_catalyst["trades"] += 1
                    if is_win: after_catalyst["wins"] += 1
                    else: after_catalyst["losses"] += 1
                    after_catalyst["pnl"] += pnl
                else:
                    after_catalyst["blocked"] += 1

                # Gate 2: DOW only
                dow_ok = dow is not None and dow in ALLOWED_DAYS
                if dow_ok:
                    after_dow["trades"] += 1
                    if is_win: after_dow["wins"] += 1
                    else: after_dow["losses"] += 1
                    after_dow["pnl"] += pnl
                else:
                    after_dow["blocked"] += 1

                # Gate 3: VIX only
                vix_ok = vix is None or vix < VIX_BLOCK
                if vix_ok:
                    after_vix["trades"] += 1
                    if is_win: after_vix["wins"] += 1
                    else: after_vix["losses"] += 1
                    after_vix["pnl"] += pnl
                else:
                    after_vix["blocked"] += 1

                # All gates combined
                if has_catalyst and dow_ok and vix_ok and mfcs >= MFCS_THRESHOLD:
                    after_all["trades"] += 1
                    if is_win: after_all["wins"] += 1
                    else: after_all["losses"] += 1
                    after_all["pnl"] += pnl
                else:
                    after_all["blocked"] += 1

    # Print results
    print()
    print(f"  {'Configuration':<30s} | {'Trades':>7s} | {'Win%':>6s} | {'W':>4s}/{' L':>4s} | {'P&L':>12s} | {'Blocked':>8s}")
    print("  " + "-" * 85)

    for label, data in [
        ("Original (no gates)", original),
        ("+ Catalyst gate", after_catalyst),
        ("+ Day-of-week gate", after_dow),
        ("+ VIX gate (<20)", after_vix),
        ("ALL GATES COMBINED", after_all),
    ]:
        trades = data["trades"]
        wr = data["wins"] / trades if trades > 0 else 0
        blocked = data.get("blocked", 0)
        pnl_str = f"${data['pnl']:+,.0f}" if data["pnl"] != 0 else "N/A"
        print(
            f"  {label:<30s} | {trades:7d} | {wr:5.0%} | "
            f"{data['wins']:4d}/{data['losses']:4d} | {pnl_str:>12s} | {blocked:8d}"
        )

    print()
    saved = original["pnl"] - after_all["pnl"]
    print(f"  Losses prevented: ${saved:+,.0f}")
    print(f"  Trades eliminated: {original['trades'] - after_all['trades']}")
    print(f"  Remaining trade quality: {after_all['wins']}/{after_all['trades']} = {after_all['wins']/after_all['trades']:.0%} WR" if after_all["trades"] > 0 else "  No trades pass all gates")

    print()
    print("  NOTE: These results use HISTORICAL agent signals (pre-D202 ensemble).")
    print("  With ensemble (D202), signal quality should further improve.")
    print("=" * 70)


if __name__ == "__main__":
    main()
