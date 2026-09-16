"""Block C: tick-level slippage calibration v3.

For each prod fill in `data/replay/prod_qty_truth.parquet`:
- Load the NBBO snapshot at the exact fill timestamp (millisecond precision)
- Compute slippage_bps = (prod_fill_px - nbbo_midpoint_at_fill_ts) / nbbo_mid * 1e4
- Side convention: positive = paid worse than mid
  - For BUY: positive when prod_fill_px > mid (paid up)
  - For SELL: positive when prod_fill_px < mid (got hit)

Output:
- data/calibration/tick_level_slippage_residuals.parquet
- mx-arena/arena/calibration/spread_v3.json (if cells stable)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]

TRUTH_PATH = REPO_ROOT / "data" / "replay" / "prod_qty_truth.parquet"
TICK_ROOT = REPO_ROOT / "data" / "polygon_backfill" / "tick_data"
OUT_RESIDUALS = REPO_ROOT / "data" / "calibration" / "tick_level_slippage_residuals.parquet"
OUT_SPREAD_V3 = REPO_ROOT / "mx-arena" / "arena" / "calibration" / "spread_v3.json"


def _quote_at_ns(quotes_df: pd.DataFrame, target_ns: int) -> dict | None:
    """Find the LAST quote whose sip_ts_ns ≤ target_ns. This is the
    NBBO snapshot in effect at the fill time — exactly the value the
    bot 'should have seen'."""
    if quotes_df.empty:
        return None
    # binary search via searchsorted (quotes are sorted asc)
    idx = quotes_df["sip_ts_ns"].searchsorted(target_ns, side="right") - 1
    if idx < 0:
        return None
    row = quotes_df.iloc[idx]
    return {
        "sip_ts_ns": int(row["sip_ts_ns"]),
        "bid_price": float(row["bid_price"]),
        "ask_price": float(row["ask_price"]),
        "bid_size": int(row["bid_size"]),
        "ask_size": int(row["ask_size"]),
        "midpoint": float(row["midpoint"]),
        "lag_ms": (target_ns - int(row["sip_ts_ns"])) / 1e6,
    }


def main() -> None:
    if not TRUTH_PATH.exists():
        raise SystemExit(f"truth corpus missing: {TRUTH_PATH}")

    truth = pd.read_parquet(TRUTH_PATH)
    print(f"Truth corpus: {len(truth)} prod fills\n")

    rows = []
    skipped_outliers = 0
    for _, t in truth.iterrows():
        ticker = str(t["ticker"]).upper()
        sess = str(t["session_date"])
        # Bug AS defense (2026-04-29): honor the corpus-level
        # data_quality_outlier flag set by extract_prod_qty_truth.py.
        # These rows have prod_entry_avg_px outside the day's actual
        # price range (most likely Bug AT-1 limit-as-fill recording).
        if bool(t.get("data_quality_outlier", False)):
            print(f"  SKIP {ticker} {sess}: data_quality_outlier=True (Bug AS)")
            skipped_outliers += 1
            continue
        entry_ts = pd.Timestamp(t["entry_ts"]).tz_convert("UTC")
        entry_ns = int(entry_ts.timestamp() * 1e9)
        prod_px = float(t["prod_entry_avg_px"])
        prod_qty = int(t["prod_qty"])

        quotes_path = TICK_ROOT / sess / f"{ticker}_quotes.parquet"
        trades_path = TICK_ROOT / sess / f"{ticker}_trades.parquet"
        if not quotes_path.exists():
            print(f"  MISS quotes: {ticker} {sess}")
            continue

        quotes_df = pd.read_parquet(quotes_path)
        # Polygon quotes_df is already sorted asc by sip_ts_ns from the orchestrator.
        if not quotes_df["sip_ts_ns"].is_monotonic_increasing:
            quotes_df = quotes_df.sort_values("sip_ts_ns").reset_index(drop=True)

        snap = _quote_at_ns(quotes_df, entry_ns)
        if snap is None:
            print(f"  NO QUOTE at entry: {ticker} {sess}")
            continue

        # Side inference: all rows in prod_qty_truth are BUYs (entries).
        side = "buy"
        mid = snap["midpoint"]
        if mid <= 0:
            print(f"  ZERO MID: {ticker} {sess} (bid={snap['bid_price']} ask={snap['ask_price']})")
            slippage_bps = float("nan")
        else:
            # buy: paid worse than mid = positive
            slippage_bps = (prod_px - mid) / mid * 1e4

        # Optional: also check the nearest TRADE tick at fill time as a
        # second reference. Many prod fills route through wholesalers
        # which may print AT the bid/ask for liquidity-taking but
        # internalize a fraction inside the spread.
        trade_ref_px = None
        if trades_path.exists():
            trades_df = pd.read_parquet(trades_path)
            if not trades_df.empty:
                if not trades_df["sip_ts_ns"].is_monotonic_increasing:
                    trades_df = trades_df.sort_values("sip_ts_ns").reset_index(drop=True)
                # Closest trade within 5 seconds either side.
                window_ns = 5 * 10**9
                nearby = trades_df[
                    (trades_df["sip_ts_ns"] >= entry_ns - window_ns) &
                    (trades_df["sip_ts_ns"] <= entry_ns + window_ns)
                ]
                if not nearby.empty:
                    # Trade-volume-weighted average within the window.
                    trade_ref_px = float((nearby["price"] * nearby["size"]).sum() / nearby["size"].sum())

        # Price tier
        if prod_px < 3:
            tier = "sub_3"
        elif prod_px < 10:
            tier = "3_to_10"
        else:
            tier = "above_10"

        # Time-of-day quintile (within 09:30-16:00 ET = 13:30-20:00 UTC)
        et_minute = (entry_ts.hour * 60 + entry_ts.minute) - (13 * 60 + 30)
        if et_minute < 0 or et_minute > 390:
            tod_quintile = "outside_session"
        else:
            q = min(4, et_minute // (390 // 5))
            tod_quintile = f"q{q+1}"

        # Structural outlier flag: |slippage| > 500 bps is suspect
        struct_outlier = abs(slippage_bps) > 500 if not np.isnan(slippage_bps) else False

        rows.append({
            "ticker": ticker,
            "session_date": sess,
            "side": side,
            "prod_qty": prod_qty,
            "prod_fill_px": prod_px,
            "nbbo_bid": snap["bid_price"],
            "nbbo_ask": snap["ask_price"],
            "nbbo_mid": mid,
            "nbbo_lag_ms": snap["lag_ms"],
            "trade_ref_px_5s_vwap": trade_ref_px,
            "slippage_bps_vs_mid": slippage_bps,
            "slippage_bps_vs_trade_vwap": (
                (prod_px - trade_ref_px) / trade_ref_px * 1e4
                if trade_ref_px else float("nan")
            ),
            "price_tier": tier,
            "tod_quintile": tod_quintile,
            "structural_outlier_flag": struct_outlier,
        })

    df = pd.DataFrame(rows)
    OUT_RESIDUALS.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT_RESIDUALS, index=False)

    print(f"\n=== Tick-level residuals ({len(df)} fills) ===")
    print(df[[
        "ticker", "session_date", "prod_fill_px", "nbbo_mid",
        "slippage_bps_vs_mid", "slippage_bps_vs_trade_vwap",
        "price_tier", "tod_quintile",
    ]].to_string(index=False))

    if df.empty:
        print("\nNo residuals computed; nothing to write.")
        return

    print("\n=== Summary stats ===")
    sl = df["slippage_bps_vs_mid"].dropna()
    print(f"  count: {len(sl)}")
    print(f"  mean:  {sl.mean():+.1f} bps")
    print(f"  median: {sl.median():+.1f} bps")
    print(f"  p25 / p75: {sl.quantile(0.25):+.1f} / {sl.quantile(0.75):+.1f} bps")
    print(f"  min / max: {sl.min():+.1f} / {sl.max():+.1f} bps")
    print(f"  abs mean: {sl.abs().mean():.1f} bps")

    print("\n=== By price tier ===")
    print(df.groupby("price_tier")["slippage_bps_vs_mid"].describe()[
        ["count", "mean", "50%", "min", "max"]
    ])

    # Calibration v3 fit: per-tier multiplier (winsorized median).
    # Cap at 3x. If sample n<3 within a cell, ship 1.0 (identity).
    print("\n=== Calibration v3 fit ===")
    cal: dict[str, dict] = {"meta": {
        "version": 3,
        "fit_date": datetime.now(timezone.utc).isoformat(),
        "method": "median absolute slippage vs NBBO mid, capped at 3x",
        "input": str(OUT_RESIDUALS.relative_to(REPO_ROOT)),
        "n_total": len(df),
    }, "per_tier": {}}
    for tier, g in df.groupby("price_tier"):
        sl_t = g["slippage_bps_vs_mid"].dropna()
        if len(sl_t) < 3:
            mult = 1.0
            note = f"n={len(sl_t)} < 3, ship identity"
        else:
            # Winsorize top/bottom 5%
            lo, hi = sl_t.quantile(0.05), sl_t.quantile(0.95)
            wins = sl_t.clip(lo, hi)
            median_abs = wins.abs().median()
            # Multiplier interpretation: ratio of observed median |slippage|
            # to a reference 5 bps "ideal" mid execution. Cap at 3x.
            mult = min(3.0, max(1.0, median_abs / 5.0))
            note = f"n={len(sl_t)}, winsorized median |slip|={median_abs:.1f} bps"
        cal["per_tier"][tier] = {
            "multiplier": round(mult, 4),
            "n_samples": int(len(sl_t)),
            "note": note,
        }
        print(f"  {tier}: multiplier={mult:.2f}  ({note})")

    # Side-only (all together) too, for fallback
    sl_all = df["slippage_bps_vs_mid"].dropna()
    if len(sl_all) >= 3:
        lo, hi = sl_all.quantile(0.05), sl_all.quantile(0.95)
        wins = sl_all.clip(lo, hi)
        median_abs = wins.abs().median()
        side_mult = min(3.0, max(1.0, median_abs / 5.0))
        cal["side_fallback"] = {
            "buy": {"multiplier": round(side_mult, 4), "n_samples": int(len(sl_all))},
        }
        print(f"  side(buy)_fallback: multiplier={side_mult:.2f} (n={len(sl_all)})")

    OUT_SPREAD_V3.parent.mkdir(parents=True, exist_ok=True)
    OUT_SPREAD_V3.write_text(json.dumps(cal, indent=2), encoding="utf-8")
    print(f"\nv3 calibration written to {OUT_SPREAD_V3.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
