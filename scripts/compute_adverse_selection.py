"""Block D: Adverse-selection analysis on 60min post-fill NBBO windows.

For each non-data_quality_outlier prod fill in prod_qty_truth.parquet:
  - Load NBBO ticks for the 60 minutes following entry_ts
  - At each 1-minute increment T (T+1, T+2, ..., T+60), compute the
    NBBO mid in effect at that timestamp
  - adverse_drift_bps_at_T = (nbbo_mid_at_T - prod_entry_px) / prod_entry_px * 1e4
  - Negative drift = adverse for the LONG position (price moved against us)

Output:
  data/calibration/adverse_selection_curves.parquet
  Columns: ticker, session_date, prod_entry_px, T_minutes, mid_at_T,
           adverse_drift_bps, has_quote

Aggregate analysis (per-T summary across the cohort):
  - median, p25, p75 adverse drift at T+5, T+15, T+30, T+60
  - cross-tab by entry-time-of-day (q1 vs others) and price tier (sub_3 vs above)

Compare to A.5 synthetic model:
  - A.5 used "50 bps/min adverse drift after T+5min"
  - At T+30 this implies 25min × 50bps = 1250 bps cumulative
  - Print real cohort median at T+30 vs that 1250-bps stress
"""
from __future__ import annotations

from pathlib import Path
from datetime import datetime, timezone, timedelta

import pandas as pd
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
TRUTH_PATH = REPO_ROOT / "data" / "replay" / "prod_qty_truth.parquet"
TICK_ROOT = REPO_ROOT / "data" / "polygon_backfill" / "tick_data"
OUT = REPO_ROOT / "data" / "calibration" / "adverse_selection_curves.parquet"
OUT.parent.mkdir(parents=True, exist_ok=True)


def _mid_at(quotes_df: pd.DataFrame, target_ns: int) -> tuple[float | None, int | None]:
    """Return (midpoint, lag_ms) for the NBBO snapshot in effect at target_ns,
    or (None, None) if no quote ≤ target_ns."""
    if quotes_df.empty:
        return None, None
    idx = quotes_df["sip_ts_ns"].searchsorted(target_ns, side="right") - 1
    if idx < 0:
        return None, None
    row = quotes_df.iloc[idx]
    bid = float(row["bid_price"]); ask = float(row["ask_price"])
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0, int((target_ns - int(row["sip_ts_ns"])) / 1e6)
    return None, None


def main() -> None:
    truth = pd.read_parquet(TRUTH_PATH)
    print(f"Truth corpus: {len(truth)} rows")
    if "data_quality_outlier" in truth.columns:
        clean = truth[~truth["data_quality_outlier"]]
        print(f"After excluding data_quality_outlier: {len(clean)}")
    else:
        clean = truth

    rows: list[dict] = []
    for _, t in clean.iterrows():
        ticker = str(t["ticker"]).upper()
        sess = str(t["session_date"])
        entry_ts = pd.Timestamp(t["entry_ts"]).tz_convert("UTC")
        entry_ns = int(entry_ts.timestamp() * 1e9)
        prod_px = float(t["prod_entry_avg_px"])
        if prod_px <= 0:
            continue

        quotes_p = TICK_ROOT / sess / f"{ticker}_quotes.parquet"
        if not quotes_p.exists():
            print(f"  MISS quotes: {ticker} {sess}")
            continue

        q = pd.read_parquet(quotes_p)
        if not q["sip_ts_ns"].is_monotonic_increasing:
            q = q.sort_values("sip_ts_ns").reset_index(drop=True)

        # T+1..T+60 minute increments
        for T in range(1, 61):
            target_ns = entry_ns + T * 60 * 10**9
            mid, lag_ms = _mid_at(q, target_ns)
            row = {
                "ticker": ticker,
                "session_date": sess,
                "prod_entry_px": prod_px,
                "T_minutes": T,
                "target_ts": pd.Timestamp(target_ns, unit="ns", tz="UTC").isoformat(),
                "mid_at_T": mid,
                "lag_ms": lag_ms,
                "adverse_drift_bps": ((mid - prod_px) / prod_px * 1e4) if mid else None,
                "has_quote": mid is not None,
            }
            # Tier metadata for cross-tab
            row["price_tier"] = (
                "sub_3" if prod_px < 3 else
                "3_to_10" if prod_px < 10 else "above_10"
            )
            et_minute = (entry_ts.hour * 60 + entry_ts.minute) - (13 * 60 + 30)
            row["tod_q"] = (
                "outside" if (et_minute < 0 or et_minute > 390)
                else f"q{min(4, et_minute // (390 // 5)) + 1}"
            )
            rows.append(row)

    if not rows:
        print("No rows computed; nothing to write.")
        return

    df = pd.DataFrame(rows)
    df.to_parquet(OUT, index=False)
    print(f"\nWrote {OUT.relative_to(REPO_ROOT)} ({len(df)} rows for {df['ticker'].nunique()} fills × 60 increments)")

    # Per-T summary across cohort
    print("\n=== Adverse-drift quantiles by T (across cohort) ===")
    print(f"  T   |  n  | p25 bps | p50 bps | p75 bps | mean bps")
    print("  -----+-----+---------+---------+---------+---------")
    for T in [1, 5, 10, 15, 30, 45, 60]:
        sub = df[(df["T_minutes"] == T) & df["has_quote"]]
        if sub.empty:
            continue
        ad = sub["adverse_drift_bps"]
        print(f"  T+{T:<2} | {len(ad):3d} | {ad.quantile(0.25):+7.1f} | "
              f"{ad.median():+7.1f} | {ad.quantile(0.75):+7.1f} | {ad.mean():+7.1f}")

    # Per-ticker drift trajectory at key checkpoints
    print("\n=== Per-fill adverse drift at T+5, T+15, T+30, T+60 ===")
    print("  ticker | date       | entry $  | T+5     | T+15    | T+30    | T+60")
    print("  -------+------------+----------+---------+---------+---------+--------")
    for (ticker, sess, prod_px), g in df.groupby(["ticker", "session_date", "prod_entry_px"]):
        line = f"  {ticker:<6} | {sess} | {prod_px:>8.4f} |"
        for T in [5, 15, 30, 60]:
            row = g[g["T_minutes"] == T]
            if not row.empty and row.iloc[0]["has_quote"]:
                line += f" {row.iloc[0]['adverse_drift_bps']:+7.1f} |"
            else:
                line += f"  N/A    |"
        print(line)

    # A.5 comparison
    print("\n=== Comparison vs A.5 synthetic model (50 bps/min adverse after T+5) ===")
    for T in [10, 15, 30, 60]:
        a5_implied = -(T - 5) * 50  # negative because adverse for long
        sub = df[(df["T_minutes"] == T) & df["has_quote"]]
        if sub.empty:
            continue
        real_median = sub["adverse_drift_bps"].median()
        ratio = real_median / a5_implied if a5_implied != 0 else float("nan")
        marker = "<<<" if abs(real_median) < abs(a5_implied) * 0.5 else (">>>" if abs(real_median) > abs(a5_implied) * 1.5 else "~~")
        print(f"  T+{T:<2} | A.5 implied: {a5_implied:+7.0f} bps | real median: {real_median:+7.1f} bps | ratio: {ratio:.2f} {marker}")

    print("\n  Legend: '<<<' real drift much smaller than A.5; '>>>' larger; '~~' similar")

    # Per-tier
    print("\n=== Adverse drift by price tier at T+15 ===")
    sub = df[(df["T_minutes"] == 15) & df["has_quote"]]
    print(sub.groupby("price_tier")["adverse_drift_bps"].describe()[["count","mean","50%","min","max"]])


if __name__ == "__main__":
    main()
