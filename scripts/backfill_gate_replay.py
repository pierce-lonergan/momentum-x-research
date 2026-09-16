"""
D219: Gate Replay — Counterfactual Analysis

Replays the EMC filter gates with both OLD (pre-D219) and NEW (D219) thresholds
against the historical candidates. Shows how many candidates each gate kills/passes
and which profitable trades were missed under the old config.

Usage:
    python scripts/backfill_gate_replay.py
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("gate_replay")

# Old (pre-D219) thresholds
OLD_CONFIG = {
    "gap_pct_min": 0.05,
    "gap_pct_max": 0.50,
    "price_min": 1.50,
    "price_max": 100.00,
    "rvol_premarket_min": 2.0,
    "dolvol_min": 2_000_000,
    "float_ideal_shares": 10_000_000,
    "vix_block_threshold": 20.0,
}

# New (D219) thresholds
NEW_CONFIG = {
    "gap_pct_min": 0.05,
    "gap_pct_max": 1.00,
    "price_min": 2.00,
    "price_max": 100.00,
    "rvol_premarket_min": 3.0,
    "dolvol_min": 2_000_000,
    "float_ideal_shares": 5_000_000,
    "vix_block_threshold": 30.0,
}


def apply_emc_filter(candidate: dict, config: dict) -> tuple[bool, str]:
    """Apply EMC filter gates. Returns (passed, rejection_reason)."""
    gap = candidate.get("gap_pct", 0)
    price = candidate.get("open", 0)
    dolvol = candidate.get("dollar_volume", 0)

    if gap < config["gap_pct_min"]:
        return False, "gap_too_low"
    if gap > config["gap_pct_max"]:
        return False, "gap_too_high"
    if price < config["price_min"]:
        return False, "price_too_low"
    if price > config["price_max"]:
        return False, "price_too_high"
    if dolvol < config["dolvol_min"]:
        return False, "dolvol_too_low"

    return True, "PASS"


def main():
    features_file = Path("data/backfill/features_labeled.jsonl")
    candidates_file = Path("data/backfill/candidates.jsonl")

    # Use features_labeled if available (has outcome data), fall back to candidates
    if features_file.exists():
        with open(features_file, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        has_outcomes = True
        logger.info("Using features_labeled.jsonl (%d rows with outcomes)", len(rows))
    elif candidates_file.exists():
        with open(candidates_file, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
        has_outcomes = False
        logger.info("Using candidates.jsonl (%d rows, no outcomes yet)", len(rows))
    else:
        logger.error("No data files found. Run harvester + features first.")
        return

    # Replay gates
    old_pass = []
    old_reject = {}
    new_pass = []
    new_reject = {}
    diff_gained = []  # Pass NEW but not OLD
    diff_lost = []    # Pass OLD but not NEW

    for row in rows:
        old_ok, old_reason = apply_emc_filter(row, OLD_CONFIG)
        new_ok, new_reason = apply_emc_filter(row, NEW_CONFIG)

        if old_ok:
            old_pass.append(row)
        else:
            old_reject[old_reason] = old_reject.get(old_reason, 0) + 1

        if new_ok:
            new_pass.append(row)
        else:
            new_reject[new_reason] = new_reject.get(new_reason, 0) + 1

        if new_ok and not old_ok:
            diff_gained.append((row, old_reason))
        if old_ok and not new_ok:
            diff_lost.append((row, new_reason))

    # Report
    print(f"\n{'='*70}")
    print(f"  GATE REPLAY — {len(rows)} candidates")
    print(f"{'='*70}")
    print(f"\n  OLD config: {len(old_pass)} pass / {len(rows) - len(old_pass)} reject")
    for reason, count in sorted(old_reject.items(), key=lambda x: -x[1]):
        print(f"    {reason:>20s}: {count:4d} rejected")

    print(f"\n  NEW config: {len(new_pass)} pass / {len(rows) - len(new_pass)} reject")
    for reason, count in sorted(new_reject.items(), key=lambda x: -x[1]):
        print(f"    {reason:>20s}: {count:4d} rejected")

    print(f"\n  DIFF: +{len(diff_gained)} gained, -{len(diff_lost)} lost")

    # Analyze what we gained
    if diff_gained and has_outcomes:
        print(f"\n  GAINED CANDIDATES (would have been missed by old config):")
        gained_wins = 0
        gained_returns = []
        for row, old_reason in diff_gained:
            close_ret = row.get("close")
            mfe = row.get("mfe_pct", 0)
            if close_ret is not None:
                gained_returns.append(close_ret)
                if close_ret > 0:
                    gained_wins += 1
            ticker = row.get("ticker", "?")
            date = row.get("date", "?")
            gap = row.get("gap_pct", 0)
            print(f"    {date} {ticker:>6s} gap={gap*100:5.1f}% close={close_ret*100 if close_ret else 0:+5.1f}% "
                  f"MFE={mfe*100:+5.1f}% (was rejected: {old_reason})")

        if gained_returns:
            avg_ret = sum(gained_returns) / len(gained_returns) * 100
            wr = gained_wins / len(gained_returns) * 100
            print(f"\n    Gained summary: n={len(gained_returns)} | WR={wr:.0f}% | avg_close={avg_ret:+.1f}%")

    # Analyze what we lost
    if diff_lost and has_outcomes:
        print(f"\n  LOST CANDIDATES (pass old, fail new):")
        lost_wins = 0
        lost_returns = []
        for row, new_reason in diff_lost:
            close_ret = row.get("close")
            if close_ret is not None:
                lost_returns.append(close_ret)
                if close_ret > 0:
                    lost_wins += 1
            ticker = row.get("ticker", "?")
            date = row.get("date", "?")
            gap = row.get("gap_pct", 0)
            print(f"    {date} {ticker:>6s} gap={gap*100:5.1f}% close={close_ret*100 if close_ret else 0:+5.1f}% "
                  f"(now rejected: {new_reason})")

        if lost_returns:
            avg_ret = sum(lost_returns) / len(lost_returns) * 100
            wr = lost_wins / len(lost_returns) * 100
            print(f"\n    Lost summary: n={len(lost_returns)} | WR={wr:.0f}% | avg_close={avg_ret:+.1f}%")

    # Outcome analysis by gap bucket (if we have outcomes)
    if has_outcomes:
        print(f"\n{'='*70}")
        print(f"  OUTCOME ANALYSIS (NEW config passers only)")
        print(f"{'='*70}")

        for lo, hi, label in [
            (0.05, 0.10, "5-10%"),
            (0.10, 0.20, "10-20%"),
            (0.20, 0.50, "20-50%"),
            (0.50, 1.00, "50-100%"),
            (1.00, 10.0, "100%+"),
        ]:
            bucket = [r for r in new_pass if lo <= r.get("gap_pct", 0) < hi]
            if not bucket:
                continue
            wins = sum(1 for r in bucket if (r.get("close") or 0) > 0)
            wr = wins / len(bucket) * 100
            avg_close = sum((r.get("close") or 0) for r in bucket) / len(bucket) * 100
            avg_mfe = sum(r.get("mfe_pct", 0) for r in bucket) / len(bucket) * 100
            avg_mae = sum(r.get("mae_pct", 0) for r in bucket) / len(bucket) * 100
            print(f"    {label:>8s}: n={len(bucket):3d} | WR={wr:4.0f}% | "
                  f"avg_close={avg_close:+5.1f}% | MFE={avg_mfe:+5.1f}% | MAE={avg_mae:+5.1f}%")

        # Top winners and losers
        if new_pass:
            sorted_by_close = sorted(
                [r for r in new_pass if r.get("close") is not None],
                key=lambda r: r.get("close", 0),
                reverse=True,
            )
            print(f"\n  TOP 10 WINNERS:")
            for r in sorted_by_close[:10]:
                print(f"    {r['date']} {r['ticker']:>6s} gap={r['gap_pct']*100:5.1f}% "
                      f"close={r['close']*100:+6.1f}% MFE={r.get('mfe_pct',0)*100:+5.1f}%")

            print(f"\n  TOP 10 LOSERS:")
            for r in sorted_by_close[-10:]:
                print(f"    {r['date']} {r['ticker']:>6s} gap={r['gap_pct']*100:5.1f}% "
                      f"close={r['close']*100:+6.1f}% MAE={r.get('mae_pct',0)*100:+5.1f}%")


if __name__ == "__main__":
    main()
