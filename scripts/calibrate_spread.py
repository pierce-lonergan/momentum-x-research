"""Fit per-tier spread multipliers from arena replay output.

Block 2.1 of the arena↔prod parity program.

Method:
    1. Read data/replay/arena_session_<since>_<until>.parquet
    2. Filter to status=ok rows (we have arena+prod fill prices)
    3. Per price tier (sub_1, sub_3, sub_10, sub_50, above_50), compute
       the median |fill_delta_bps| across BOTH legs (entry + exit).
       Median chosen over mean for robustness to ATER-class outliers
       in a small sample.
    4. The current arena half-spread implies an expected |delta_bps|
       of ~half the spread. The calibration multiplier is:
            multiplier = max(1.0, prod_median_delta / arena_baseline_half_bps)
       Floor at 1.0 because we never want to MAKE arena tighter than
       its base model.
    5. Write calibration JSON keyed by tier.
    6. Print before/after summary.

DATA SPARSITY CAVEAT (loud):
    With only 4 replayable trades, single-tier estimates are point
    samples, not distributions. The output is a v0.1 calibration. As
    bar coverage gaps close (Block 2 finding) and more sessions
    replay, this script re-runs and produces a richer fit. The
    finding doc 59 documents this explicitly.

Usage:
    python scripts/calibrate_spread.py \
        --since 2026-04-22 --until 2026-04-28 \
        --output mx-arena/arena/calibration/spread_v0.1.json

    # Print only (no write)
    python scripts/calibrate_spread.py --since ... --print-only
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger("calibrate_spread")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPLAY_DIR = REPO_ROOT / "data" / "replay"
DEFAULT_OUTPUT = REPO_ROOT / "mx-arena" / "arena" / "calibration" / "spread_v0.1.json"


# Arena baseline base-bps per tier (from spread_model.py)
ARENA_BASE_BPS = {
    "sub_1": 80.0,
    "sub_3": 30.0,
    "sub_10": 12.0,
    "sub_50": 5.0,
    "above_50": 2.0,
}


def _tier_key(price: float) -> str:
    if price < 1.0:
        return "sub_1"
    if price < 3.0:
        return "sub_3"
    if price < 10.0:
        return "sub_10"
    if price < 50.0:
        return "sub_50"
    return "above_50"


def fit_calibration(df) -> dict:
    """Compute per-tier multipliers from a replay parquet."""
    import pandas as pd
    ok = df[df["replay_status"] == "ok"].copy()
    if ok.empty:
        logger.warning("no ok rows in replay parquet — calibration trivial")
        return {}

    # Build long-form: one row per (trade, leg) with (price, delta_bps)
    rows = []
    for _, r in ok.iterrows():
        entry_px = float(r["prod_entry_px"])
        exit_px = float(r["prod_exit_px"])
        if entry_px > 0:
            rows.append({"price": entry_px, "abs_delta_bps": abs(float(r["fill_delta_entry_bps"])), "leg": "entry", "ticker": r["ticker"]})
        if exit_px > 0:
            rows.append({"price": exit_px, "abs_delta_bps": abs(float(r["fill_delta_exit_bps"])), "leg": "exit", "ticker": r["ticker"]})
    long_df = pd.DataFrame(rows)
    long_df["tier"] = long_df["price"].apply(_tier_key)

    # Per-tier median absolute delta (bps)
    per_tier = long_df.groupby("tier")["abs_delta_bps"].agg(["median", "count"]).to_dict("index")

    calibration: dict = {}
    for tier, stats in per_tier.items():
        median_delta = float(stats["median"])
        n = int(stats["count"])
        baseline_half_bps = ARENA_BASE_BPS.get(tier, 5.0) / 2.0  # half-spread
        # The multiplier brings arena's expected fill delta in line with
        # prod's observed median. Floor at 1.0 (never tighten beyond base).
        if baseline_half_bps <= 0:
            multiplier = 1.0
        else:
            multiplier = max(1.0, median_delta / baseline_half_bps)
        calibration[tier] = round(multiplier, 3)
        logger.info(
            "tier=%s n=%d median_|delta|=%.1fbps  baseline_half=%.1fbps  → multiplier=%.3f",
            tier, n, median_delta, baseline_half_bps, multiplier,
        )

    # Tiers with no observations get default 1.0 (preserves arena base)
    for tier in ARENA_BASE_BPS:
        if tier not in calibration:
            calibration[tier] = 1.0
            logger.debug("tier=%s NO data — default multiplier=1.0", tier)

    return calibration


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--since", default="2026-04-22")
    p.add_argument("--until", default="2026-04-28")
    p.add_argument("--replay-dir", type=Path, default=DEFAULT_REPLAY_DIR)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--print-only", action="store_true",
                   help="Print calibration to stdout; do NOT write file.")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    try:
        import pandas as pd
    except ImportError:
        logger.error("pandas required")
        return 1

    replay_path = args.replay_dir / f"arena_session_{args.since}_{args.until}.parquet"
    if not replay_path.exists():
        logger.error("replay file not found: %s", replay_path)
        return 1
    df = pd.read_parquet(replay_path)
    logger.info("loaded %d rows from %s", len(df), replay_path)

    calibration = fit_calibration(df)
    payload = {
        "version": "v0.1",
        "fit_window": {"since": args.since, "until": args.until},
        "method": "median absolute fill_delta_bps per price tier; multiplier = max(1.0, median / baseline_half_bps)",
        "data_sparsity_caveat": "Single-tier point estimates with n<10 each. Re-fit when more replay data arrives.",
        "tiers": calibration,
    }

    if args.print_only:
        print(json.dumps(payload, indent=2))
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        logger.info("wrote %s", args.output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
