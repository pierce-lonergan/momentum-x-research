"""
D219: Backfill Analytics Report (Block 6)

Generates comprehensive summary statistics from the labeled feature dataset,
validating D219 threshold changes with historical evidence.

Usage:
    python scripts/backfill_analytics.py
"""

from __future__ import annotations

import json
import logging
import statistics
from collections import defaultdict
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("analytics")


def load_data() -> list[dict]:
    features_file = Path("data/backfill/features_labeled.jsonl")
    if not features_file.exists():
        logger.error("Run backfill_features.py first")
        return []
    with open(features_file, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def section_header(title: str):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


def pct(val):
    return f"{val*100:+.1f}%" if val is not None else "N/A"


def main():
    rows = load_data()
    if not rows:
        return

    section_header(f"D219 BACKFILL ANALYTICS — {len(rows)} labeled candidates")

    # ----------------------------------------------------------------
    # 1. Overall statistics
    # ----------------------------------------------------------------
    section_header("1. OVERALL STATISTICS")

    dates = set(r["date"] for r in rows)
    tickers = set(r["ticker"] for r in rows)
    close_rets = [r["close"] for r in rows if r.get("close") is not None]
    mfes = [r["mfe_pct"] for r in rows if r.get("mfe_pct") is not None]
    maes = [r["mae_pct"] for r in rows if r.get("mae_pct") is not None]
    ttmfes = [r["time_to_mfe_min"] for r in rows if r.get("time_to_mfe_min") is not None]

    wins = sum(1 for r in close_rets if r > 0)
    print(f"  Trading days:      {len(dates)}")
    print(f"  Unique tickers:    {len(tickers)}")
    print(f"  Avg candidates/day: {len(rows)/len(dates):.1f}")
    print(f"  Close win rate:    {wins}/{len(close_rets)} ({wins/len(close_rets)*100:.1f}%)")
    print(f"  Avg close return:  {statistics.mean(close_rets)*100:+.2f}%")
    print(f"  Median close ret:  {statistics.median(close_rets)*100:+.2f}%")
    print(f"  Stdev close ret:   {statistics.stdev(close_rets)*100:.2f}%")
    print(f"  Avg MFE:           {statistics.mean(mfes)*100:+.2f}%")
    print(f"  Avg MAE:           {statistics.mean(maes)*100:+.2f}%")
    print(f"  Avg time to MFE:   {statistics.mean(ttmfes):.1f} min")
    print(f"  Median time MFE:   {statistics.median(ttmfes):.0f} min")

    # ----------------------------------------------------------------
    # 2. Multi-horizon return profile
    # ----------------------------------------------------------------
    section_header("2. MULTI-HORIZON RETURN PROFILE")

    for horizon in ["t1", "t5", "t15", "t30", "t60", "t120", "close"]:
        vals = [r[horizon] for r in rows if r.get(horizon) is not None]
        if vals:
            w = sum(1 for v in vals if v > 0)
            print(f"  {horizon:>6s}: n={len(vals):4d} | WR={w/len(vals)*100:4.0f}% | "
                  f"avg={statistics.mean(vals)*100:+5.2f}% | "
                  f"med={statistics.median(vals)*100:+5.2f}% | "
                  f"std={statistics.stdev(vals)*100:5.2f}%")

    # ----------------------------------------------------------------
    # 3. Gap bucket analysis (validates D219 gap cap change)
    # ----------------------------------------------------------------
    section_header("3. GAP BUCKET ANALYSIS (validates gap_pct_max: 0.50 -> 1.00)")

    buckets = [
        (0.05, 0.10, "5-10%"),
        (0.10, 0.20, "10-20%"),
        (0.20, 0.30, "20-30%"),
        (0.30, 0.50, "30-50%"),
        (0.50, 0.75, "50-75%"),
        (0.75, 1.00, "75-100%"),
        (1.00, 10.0, "100%+"),
    ]

    print(f"  {'Bucket':>10s} | {'n':>4s} | {'WR':>5s} | {'avg_close':>10s} | {'avg_MFE':>8s} | {'avg_MAE':>8s} | {'med_ttMFE':>9s}")
    print(f"  {'-'*10}-+-{'-'*4}-+-{'-'*5}-+-{'-'*10}-+-{'-'*8}-+-{'-'*8}-+-{'-'*9}")

    for lo, hi, label in buckets:
        b = [r for r in rows if lo <= r.get("gap_pct", 0) < hi]
        if not b:
            continue
        w = sum(1 for r in b if (r.get("close") or 0) > 0)
        avg_c = statistics.mean([(r.get("close") or 0) for r in b]) * 100
        avg_m = statistics.mean([r.get("mfe_pct", 0) for r in b]) * 100
        avg_a = statistics.mean([r.get("mae_pct", 0) for r in b]) * 100
        med_t = statistics.median([r.get("time_to_mfe_min", 0) for r in b])
        print(f"  {label:>10s} | {len(b):4d} | {w/len(b)*100:4.0f}% | {avg_c:+9.2f}% | {avg_m:+7.2f}% | {avg_a:+7.2f}% | {med_t:8.0f}m")

    # Key insight: are 50-100% gaps profitable?
    above_50 = [r for r in rows if r.get("gap_pct", 0) >= 0.50]
    below_50 = [r for r in rows if 0.05 <= r.get("gap_pct", 0) < 0.50]
    if above_50 and below_50:
        a50_wr = sum(1 for r in above_50 if (r.get("close") or 0) > 0) / len(above_50)
        b50_wr = sum(1 for r in below_50 if (r.get("close") or 0) > 0) / len(below_50)
        a50_mfe = statistics.mean([r.get("mfe_pct", 0) for r in above_50])
        b50_mfe = statistics.mean([r.get("mfe_pct", 0) for r in below_50])
        print(f"\n  KEY FINDING - Gap >=50% vs <50%:")
        print(f"    <50%:  n={len(below_50):4d} | WR={b50_wr*100:.0f}% | avg_MFE={b50_mfe*100:+.1f}%")
        print(f"    >=50%: n={len(above_50):4d} | WR={a50_wr*100:.0f}% | avg_MFE={a50_mfe*100:+.1f}%")
        print(f"    -> D219 gap cap raise {'VALIDATED' if a50_mfe > 0 else 'QUESTIONABLE'}")

    # ----------------------------------------------------------------
    # 4. Price tier analysis (validates price_min: $1.50 → $2.00)
    # ----------------------------------------------------------------
    section_header("4. PRICE TIER ANALYSIS (validates price_min: $1.50 -> $2.00)")

    price_tiers = [
        (1.50, 2.00, "$1.50-$2"),
        (2.00, 5.00, "$2-$5"),
        (5.00, 10.00, "$5-$10"),
        (10.00, 20.00, "$10-$20"),
        (20.00, 50.00, "$20-$50"),
        (50.00, 100.00, "$50-$100"),
    ]

    for lo, hi, label in price_tiers:
        b = [r for r in rows if lo <= r.get("entry_price", r.get("open", 0)) < hi]
        if not b:
            continue
        w = sum(1 for r in b if (r.get("close") or 0) > 0)
        avg_c = statistics.mean([(r.get("close") or 0) for r in b]) * 100
        avg_m = statistics.mean([r.get("mfe_pct", 0) for r in b]) * 100
        print(f"    {label:>10s}: n={len(b):3d} | WR={w/len(b)*100:4.0f}% | avg_close={avg_c:+5.1f}% | MFE={avg_m:+5.1f}%")

    # ----------------------------------------------------------------
    # 5. ORB analysis (validates Phase 5 ORB confirmation)
    # ----------------------------------------------------------------
    section_header("5. ORB ANALYSIS (validates ORB confirmation gate)")

    orb_broken_rows = [r for r in rows if r.get("orb_broken")]
    orb_held_rows = [r for r in rows if not r.get("orb_broken")]

    if orb_broken_rows:
        orb_wr = sum(1 for r in orb_broken_rows if (r.get("close") or 0) > 0) / len(orb_broken_rows)
        orb_mfe = statistics.mean([r.get("mfe_pct", 0) for r in orb_broken_rows])
        orb_ttm = statistics.median([r.get("time_to_mfe_min", 0) for r in orb_broken_rows])
        print(f"  ORB Broken: n={len(orb_broken_rows):4d} | WR={orb_wr*100:.0f}% | MFE={orb_mfe*100:+.1f}% | med_ttMFE={orb_ttm:.0f}m")

    if orb_held_rows:
        held_wr = sum(1 for r in orb_held_rows if (r.get("close") or 0) > 0) / len(orb_held_rows)
        held_mfe = statistics.mean([r.get("mfe_pct", 0) for r in orb_held_rows])
        held_ttm = statistics.median([r.get("time_to_mfe_min", 0) for r in orb_held_rows])
        print(f"  ORB Held:   n={len(orb_held_rows):4d} | WR={held_wr*100:.0f}% | MFE={held_mfe*100:+.1f}% | med_ttMFE={held_ttm:.0f}m")

    if orb_broken_rows and orb_held_rows:
        orb_edge = (sum(1 for r in orb_broken_rows if (r.get("close") or 0) > 0) / len(orb_broken_rows) -
                    sum(1 for r in orb_held_rows if (r.get("close") or 0) > 0) / len(orb_held_rows))
        print(f"  ORB breakout edge: {orb_edge*100:+.1f}pp win rate advantage")

    # ----------------------------------------------------------------
    # 6. Time-to-MFE analysis (informs exit timing)
    # ----------------------------------------------------------------
    section_header("6. TIME-TO-MFE DISTRIBUTION (informs exit timing)")

    if ttmfes:
        sorted_t = sorted(ttmfes)
        n = len(sorted_t)
        print(f"  p10={sorted_t[n//10]}min  p25={sorted_t[n//4]}min  p50={sorted_t[n//2]}min  "
              f"p75={sorted_t[3*n//4]}min  p90={sorted_t[9*n//10]}min")

        # What % peak in first 5, 15, 30, 60 minutes
        for cutoff in [5, 15, 30, 60]:
            pct_before = sum(1 for t in ttmfes if t <= cutoff) / len(ttmfes) * 100
            print(f"  MFE within {cutoff:3d}min: {pct_before:.0f}% of stocks")

    # ----------------------------------------------------------------
    # 7. Daily P&L simulation (equal-weight all passers)
    # ----------------------------------------------------------------
    section_header("7. DAILY P&L SIMULATION (equal-weight all passers)")

    daily = defaultdict(list)
    for r in rows:
        if r.get("close") is not None:
            daily[r["date"]].append(r["close"])

    daily_returns = []
    for date in sorted(daily.keys()):
        rets = daily[date]
        avg_ret = statistics.mean(rets)
        daily_returns.append(avg_ret)
        wins = sum(1 for r in rets if r > 0)
        print(f"  {date}: {len(rets):3d} trades | WR={wins/len(rets)*100:4.0f}% | "
              f"avg={avg_ret*100:+5.2f}%")

    if daily_returns:
        total_days = len(daily_returns)
        winning_days = sum(1 for d in daily_returns if d > 0)
        cumulative = sum(daily_returns) * 100
        print(f"\n  {winning_days}/{total_days} winning days ({winning_days/total_days*100:.0f}%)")
        print(f"  Cumulative: {cumulative:+.1f}% (equal-weight, no position sizing)")
        if len(daily_returns) > 1:
            sharpe_approx = statistics.mean(daily_returns) / statistics.stdev(daily_returns) * (252 ** 0.5)
            print(f"  Annualized Sharpe (approx): {sharpe_approx:.2f}")

    # ----------------------------------------------------------------
    # 8. D219 validation summary
    # ----------------------------------------------------------------
    section_header("8. D219 VALIDATION SUMMARY")

    checks = []

    # Gap cap
    above_50_wins = [r for r in rows if r.get("gap_pct", 0) >= 0.50 and (r.get("close") or 0) > 0]
    above_50_all = [r for r in rows if r.get("gap_pct", 0) >= 0.50]
    if above_50_all:
        gap_verdict = "[VALIDATED]" if len(above_50_wins) / len(above_50_all) > 0.40 else "[QUESTIONABLE]"
        checks.append(f"  Gap cap 50%->100%: {gap_verdict} "
                      f"(WR={len(above_50_wins)}/{len(above_50_all)} = {len(above_50_wins)/len(above_50_all)*100:.0f}%)")

    # Price floor
    below_2 = [r for r in rows if 1.50 <= r.get("open", 0) < 2.00]
    if below_2:
        b2_wr = sum(1 for r in below_2 if (r.get("close") or 0) > 0) / len(below_2)
        above_2_wr = sum(1 for r in rows if r.get("open", 0) >= 2.00 and (r.get("close") or 0) > 0) / max(1, sum(1 for r in rows if r.get("open", 0) >= 2.00))
        price_verdict = "[VALIDATED]" if b2_wr < above_2_wr else "[QUESTIONABLE]"
        checks.append(f"  Price floor $1.50->$2: {price_verdict} "
                      f"($1.50-$2 WR={b2_wr*100:.0f}% vs >=$2 WR={above_2_wr*100:.0f}%)")
    else:
        checks.append(f"  Price floor $1.50->$2: [NO DATA] (0 candidates in $1.50-$2 range)")

    # ORB
    if orb_broken_rows and orb_held_rows:
        orb_verdict = "[VALIDATED]" if orb_edge > 0.05 else "[MARGINAL]" if orb_edge > 0 else "[QUESTIONABLE]"
        checks.append(f"  ORB confirmation: {orb_verdict} ({orb_edge*100:+.1f}pp edge)")

    for check in checks:
        print(check)


if __name__ == "__main__":
    main()
