"""
D219: Arena Scenario Converter (Block 5)

Converts backfill features_labeled.jsonl into selection arena MoverRecord format
with archetype tags for pattern analysis.

Usage:
    python scripts/backfill_arena_convert.py
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("arena_convert")


def classify_archetype(row: dict) -> str:
    """Classify candidate into a trading archetype."""
    gap = row.get("gap_pct", 0)
    mfe = row.get("mfe_pct", 0)
    mae = row.get("mae_pct", 0)
    close_ret = row.get("close", 0) or 0
    orb_broken = row.get("orb_broken", False)
    ttmfe = row.get("time_to_mfe_min", 0)

    # Classify by behavior pattern
    if gap >= 0.50 and close_ret > 0.10:
        return "MEGA_GAP_RUNNER"
    if gap >= 0.50 and close_ret < -0.10:
        return "MEGA_GAP_FADER"
    if orb_broken and close_ret > 0.05:
        return "ORB_BREAKOUT"
    if not orb_broken and close_ret < -0.05:
        return "FAILED_BREAKOUT"
    if mfe > 0.10 and close_ret < 0:
        return "SPIKE_AND_FADE"
    if ttmfe <= 5 and mfe > 0.05:
        return "EARLY_SPIKE"
    if ttmfe > 60 and mfe > 0.05:
        return "LATE_RUNNER"
    if abs(close_ret) < 0.02:
        return "FLAT"
    if close_ret > 0:
        return "WINNER"
    return "LOSER"


def to_mover_record(row: dict) -> dict:
    """Convert a features_labeled row to arena MoverRecord format."""
    gap = row.get("gap_pct", 0)
    entry = row.get("entry_price", row.get("open", 0))
    prior = row.get("prior_close", 0)
    mfe = row.get("mfe_pct", 0)
    dolvol = row.get("dollar_volume", 0)
    pre_vol = row.get("pre_market_volume", 0)
    day_vol = row.get("day_volume", row.get("volume", 0))

    # Estimate RVOL (pre_market relative to day — rough proxy)
    # We don't have prior day's volume breakdown, so use dolvol as proxy
    rvol_estimate = pre_vol / max(1, day_vol / 390) if day_vol > 0 else 0

    return {
        "ticker": row["ticker"],
        "date": row["date"],
        "open_price": entry,
        "previous_close": prior,
        "gap_pct": round(gap, 4),
        "rvol_at_open": round(rvol_estimate, 2),
        "premarket_volume": pre_vol,
        "prev_volume": day_vol,  # Best available proxy
        "dollar_volume": dolvol,
        "has_news": True,  # All gap-ups likely have news
        "float_shares": None,  # Not available in backfill
        "market_cap": None,
        "high": row.get("day_high", 0),
        "low": row.get("day_low", 0),
        "close": row.get("day_close", 0),
        "max_gain_from_open": round(mfe, 4),
        "scanner_found": True,
        "journal_action": None,
        "journal_mfcs": None,
        "data_confidence": "confirmed_partial",
        "notes": f"backfill|archetype={classify_archetype(row)}",
        # Extra fields for analysis (not in standard MoverRecord)
        "_archetype": classify_archetype(row),
        "_mfe_pct": row.get("mfe_pct", 0),
        "_mae_pct": row.get("mae_pct", 0),
        "_close_return": row.get("close", 0),
        "_time_to_mfe": row.get("time_to_mfe_min", 0),
        "_orb_broken": row.get("orb_broken", False),
        "_orb_range_pct": row.get("orb_range_pct", 0),
        "_outcomes": {
            h: row.get(h) for h in ["t1", "t5", "t15", "t30", "t60", "t120", "close"]
        },
    }


def main():
    features_file = Path("data/backfill/features_labeled.jsonl")
    output_file = Path("data/backfill/arena_scenarios.jsonl")

    if not features_file.exists():
        logger.error("Run backfill_features.py first")
        return

    with open(features_file, encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]

    logger.info("Converting %d rows to arena format", len(rows))

    records = [to_mover_record(r) for r in rows]

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    # Archetype distribution
    from collections import Counter
    archetypes = Counter(r["_archetype"] for r in records)
    print(f"\n{'='*50}")
    print(f"  ARCHETYPE DISTRIBUTION — {len(records)} scenarios")
    print(f"{'='*50}")
    for arch, count in archetypes.most_common():
        pct = count / len(records) * 100
        # Get avg close return for this archetype
        arch_rets = [r["_close_return"] for r in records if r["_archetype"] == arch and r["_close_return"] is not None]
        avg_ret = sum(arch_rets) / len(arch_rets) * 100 if arch_rets else 0
        print(f"  {arch:>20s}: {count:4d} ({pct:4.1f}%) | avg_close={avg_ret:+5.1f}%")

    logger.info("Wrote %d arena scenarios → %s", len(records), output_file)


if __name__ == "__main__":
    main()
