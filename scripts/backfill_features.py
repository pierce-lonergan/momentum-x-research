"""
D219: Feature Computation and Outcome Labeling

Reads candidates + minute bars, computes point-in-time features
and multi-horizon outcome labels. This is the training dataset.

Usage:
    python scripts/backfill_features.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("backfill_features")


def compute_features_and_outcomes(candidate: dict, bars: list[dict]) -> dict | None:
    """Compute point-in-time features and multi-horizon outcome labels."""
    if not bars or len(bars) < 30:
        return None

    ticker = candidate["ticker"]
    date = candidate["date"]
    gap_pct = candidate["gap_pct"]
    dollar_volume = candidate.get("dollar_volume", 0)

    # Find the 9:30 AM bar (market open)
    open_idx = None
    for i, bar in enumerate(bars):
        ts = bar.get("timestamp", "")
        # 9:30 ET = 13:30 UTC during EDT (Mar-Nov), 14:30 UTC during EST (Nov-Mar).
        # D221 fix: was missing T14:30 — silently dropped 95 EST candidates.
        if "T13:30" in ts or "T14:30" in ts:
            open_idx = i
            break

    if open_idx is None or open_idx >= len(bars) - 5:
        return None

    # Entry price = 9:31 open (first full minute after open)
    entry_idx = open_idx + 1
    if entry_idx >= len(bars):
        return None
    entry_price = bars[entry_idx]["open"]
    if entry_price <= 0:
        return None

    # Pre-market features (bars before 9:30)
    pre_market_bars = bars[:open_idx]
    pre_market_volume = sum(b.get("volume", 0) for b in pre_market_bars)
    pre_market_high = max((b.get("high", 0) for b in pre_market_bars), default=0)

    # Opening Range (first 5 minutes after open)
    orb_bars = bars[open_idx:open_idx + 5]
    orb_high = max((b.get("high", 0) for b in orb_bars), default=0)
    orb_low = min((b.get("low", float("inf")) for b in orb_bars), default=0)

    # Post-entry bars (from entry_idx onward)
    post_entry = bars[entry_idx:]

    # Multi-horizon outcome labels
    outcomes = {}
    for horizon_min in [1, 5, 15, 30, 60, 120]:
        idx = horizon_min  # Each bar is ~1 minute
        if idx < len(post_entry):
            exit_price = post_entry[idx]["close"]
            outcomes[f"t{horizon_min}"] = round((exit_price - entry_price) / entry_price, 4)
        else:
            outcomes[f"t{horizon_min}"] = None

    # Close return (last bar)
    close_price = post_entry[-1]["close"] if post_entry else entry_price
    outcomes["close"] = round((close_price - entry_price) / entry_price, 4)

    # MFE/MAE (max favorable/adverse excursion)
    post_entry_highs = [b["high"] for b in post_entry if b.get("high", 0) > 0]
    post_entry_lows = [b["low"] for b in post_entry if b.get("low", 0) > 0]

    mfe = max(post_entry_highs) if post_entry_highs else entry_price
    mae = min(post_entry_lows) if post_entry_lows else entry_price

    mfe_pct = round((mfe - entry_price) / entry_price, 4)
    mae_pct = round((mae - entry_price) / entry_price, 4)

    # Time to MFE
    time_to_mfe = 0
    for i, b in enumerate(post_entry):
        if b.get("high", 0) == mfe:
            time_to_mfe = i
            break

    # Did the ORB high get broken?
    orb_broken = any(b["high"] > orb_high for b in post_entry[5:]) if len(post_entry) > 5 else False

    # Win/loss classification at various horizons
    t60_win = outcomes.get("t60", 0) is not None and outcomes.get("t60", 0) > 0
    close_win = outcomes.get("close", 0) is not None and outcomes.get("close", 0) > 0

    return {
        "date": date,
        "ticker": ticker,
        # Features (point-in-time, available before 9:30)
        "gap_pct": round(gap_pct, 4),
        "prior_close": candidate["prior_close"],
        "open": candidate["open"],
        "dollar_volume": dollar_volume,
        "pre_market_volume": pre_market_volume,
        "pre_market_high": round(pre_market_high, 4),
        "day_high": candidate["high"],
        "day_low": candidate["low"],
        "day_close": candidate["close"],
        "day_volume": candidate["volume"],
        # Opening range
        "orb_high": round(orb_high, 4),
        "orb_low": round(orb_low, 4),
        "orb_range_pct": round((orb_high - orb_low) / entry_price, 4) if entry_price > 0 else 0,
        "orb_broken": orb_broken,
        # Entry
        "entry_price": round(entry_price, 4),
        # Outcomes
        **outcomes,
        "mfe_pct": mfe_pct,
        "mae_pct": mae_pct,
        "time_to_mfe_min": time_to_mfe,
        "t60_win": t60_win,
        "close_win": close_win,
    }


def main():
    candidates_file = Path("data/backfill/candidates.jsonl")
    output_file = Path("data/backfill/features_labeled.jsonl")

    if not candidates_file.exists():
        logger.error("Run backfill_harvester.py first")
        return

    with open(candidates_file, encoding="utf-8") as f:
        candidates = [json.loads(line) for line in f if line.strip()]

    logger.info("Processing %d candidates", len(candidates))

    results = []
    missing_bars = 0
    processed = 0

    for cand in candidates:
        ticker = cand["ticker"]
        date = cand["date"]

        bar_file = Path(f"data/bar_recordings/{date}/{ticker}.json")
        if not bar_file.exists():
            missing_bars += 1
            continue

        with open(bar_file, encoding="utf-8") as f:
            bar_data = json.load(f)
        bars = bar_data.get("bars", [])

        result = compute_features_and_outcomes(cand, bars)
        if result:
            results.append(result)
            processed += 1

    # Write output
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")

    logger.info(
        "COMPLETE: %d labeled rows from %d candidates (%d missing bars) → %s",
        processed, len(candidates), missing_bars, output_file,
    )

    # Print summary stats
    if results:
        t60_wins = sum(1 for r in results if r.get("t60_win"))
        close_wins = sum(1 for r in results if r.get("close_win"))
        avg_mfe = sum(r.get("mfe_pct", 0) for r in results) / len(results) * 100
        avg_mae = sum(r.get("mae_pct", 0) for r in results) / len(results) * 100
        avg_ttmfe = sum(r.get("time_to_mfe_min", 0) for r in results) / len(results)
        avg_close = sum(r.get("close", 0) or 0 for r in results) / len(results) * 100

        print(f"\n{'='*60}")
        print(f"  BACKFILL SUMMARY — {len(results)} labeled candidates")
        print(f"{'='*60}")
        print(f"  T+60s win rate:    {t60_wins}/{len(results)} ({t60_wins/len(results)*100:.0f}%)")
        print(f"  Close win rate:    {close_wins}/{len(results)} ({close_wins/len(results)*100:.0f}%)")
        print(f"  Avg MFE:           {avg_mfe:+.1f}%")
        print(f"  Avg MAE:           {avg_mae:+.1f}%")
        print(f"  Avg time to MFE:   {avg_ttmfe:.0f} min")
        print(f"  Avg close return:  {avg_close:+.1f}%")

        # Gap bucket analysis
        print(f"\n  Gap Bucket Analysis:")
        for lo, hi, label in [(0.05, 0.10, "5-10%"), (0.10, 0.20, "10-20%"), (0.20, 0.50, "20-50%"), (0.50, 1.0, "50-100%"), (1.0, 10.0, "100%+")]:
            bucket = [r for r in results if lo <= r.get("gap_pct", 0) < hi]
            if bucket:
                bwr = sum(1 for r in bucket if r.get("close_win")) / len(bucket) * 100
                bavg = sum(r.get("close", 0) or 0 for r in bucket) / len(bucket) * 100
                bmfe = sum(r.get("mfe_pct", 0) for r in bucket) / len(bucket) * 100
                print(f"    {label:>8s}: n={len(bucket):3d} | WR={bwr:.0f}% | avg_close={bavg:+.1f}% | avg_MFE={bmfe:+.1f}%")

        # ORB analysis
        orb_broken = [r for r in results if r.get("orb_broken")]
        orb_held = [r for r in results if not r.get("orb_broken")]
        if orb_broken:
            orb_wr = sum(1 for r in orb_broken if r.get("close_win")) / len(orb_broken) * 100
            print(f"\n  ORB broken (n={len(orb_broken)}): close WR={orb_wr:.0f}%")
        if orb_held:
            held_wr = sum(1 for r in orb_held if r.get("close_win")) / len(orb_held) * 100
            print(f"  ORB held   (n={len(orb_held)}): close WR={held_wr:.0f}%")

        # Time-to-MFE distribution
        ttmfe_vals = [r.get("time_to_mfe_min", 0) for r in results]
        ttmfe_vals.sort()
        if ttmfe_vals:
            p25 = ttmfe_vals[len(ttmfe_vals)//4]
            p50 = ttmfe_vals[len(ttmfe_vals)//2]
            p75 = ttmfe_vals[3*len(ttmfe_vals)//4]
            print(f"\n  Time-to-MFE: p25={p25}min p50={p50}min p75={p75}min")
            if p50 > 1:
                pct_after_1min = sum(1 for t in ttmfe_vals if t > 1) / len(ttmfe_vals) * 100
                print(f"  MFE after T+1min: {pct_after_1min:.0f}% of stocks peak AFTER the first minute")


if __name__ == "__main__":
    main()
