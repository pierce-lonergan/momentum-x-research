"""Watchlist mover analysis — per-(date, ticker) intraday metrics over all
recorded watchlists.

USER QUESTION: "There is almost one stock in every watchlist day by day that
moves extraordinary amounts of percentage. I want to see if we can find some
way to identify just the high movers within the watchlist."

This script:
  1. Iterates every (date, ticker) JSON in data/bar_recordings/
  2. Computes intraday metrics:
       - rth_open, rth_high, rth_low, rth_close
       - max_return_from_open  (peak intraday upside)
       - close_return          (close vs open)
       - intraday_range_pct    (high-low / open)
       - max_drawup_min        (min from RTH open to peak high)
       - first_5min_return, first_30min_return, first_60min_return
       - first_5min_volume, first_30min_volume, total_rth_volume
       - has_premarket, premarket_high_vs_rth_open  (when premarket bars exist)
  3. Per-day ranks tickers by max_return_from_open
  4. Outputs `data/audits/watchlist_movers_metrics.parquet` (one row per
     (date, ticker)) and a summary at `data/audits/watchlist_movers_summary.json`.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
BAR_DIR = REPO / "data" / "bar_recordings"
OUT_PARQUET = REPO / "data" / "audits" / "watchlist_movers_metrics.parquet"
OUT_SUMMARY = REPO / "data" / "audits" / "watchlist_movers_summary.json"
OUT_PARQUET.parent.mkdir(parents=True, exist_ok=True)

ET = ZoneInfo("America/New_York")


def parse_ts(s: str) -> datetime:
    # bars use ...Z; treat as UTC
    if s.endswith("Z"):
        return datetime.fromisoformat(s[:-1]).replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(s)


def is_rth(ts_et: datetime) -> bool:
    """09:30 ET <= t < 16:00 ET (RTH cash session)."""
    h, m = ts_et.hour, ts_et.minute
    if h < 9 or h > 16:
        return False
    if h == 9 and m < 30:
        return False
    if h == 16:
        return False  # 16:00 boundary excluded
    return True


def is_premarket(ts_et: datetime) -> bool:
    """04:00 ET <= t < 09:30 ET."""
    h, m = ts_et.hour, ts_et.minute
    if h < 4 or h > 9:
        return False
    if h == 9 and m >= 30:
        return False
    return True


def metrics_for_one(date_str: str, ticker: str, bars: list[dict]) -> dict | None:
    if not bars:
        return None

    rth_bars = []
    pm_bars = []
    for b in bars:
        try:
            ts = parse_ts(b["timestamp"]).astimezone(ET)
        except Exception as e:
            print(f"WARN: metrics_for_one: bad bar timestamp for {ticker}: {e}")
            continue
        if is_rth(ts):
            rth_bars.append((ts, b))
        elif is_premarket(ts):
            pm_bars.append((ts, b))

    if not rth_bars:
        return None

    rth_bars.sort(key=lambda x: x[0])
    rth_open = float(rth_bars[0][1]["open"])
    rth_close = float(rth_bars[-1][1]["close"])
    rth_high = max(float(b["high"]) for _, b in rth_bars)
    rth_low = min(float(b["low"]) for _, b in rth_bars)
    rth_volume = sum(int(b["volume"]) for _, b in rth_bars)

    if rth_open <= 0:
        return None

    max_return = (rth_high - rth_open) / rth_open
    min_return = (rth_low - rth_open) / rth_open
    close_return = (rth_close - rth_open) / rth_open
    intraday_range_pct = (rth_high - rth_low) / rth_open

    # Time-to-peak (minutes from RTH open to bar containing the high)
    peak_idx = max(range(len(rth_bars)), key=lambda i: float(rth_bars[i][1]["high"]))
    peak_ts = rth_bars[peak_idx][0]
    open_ts = rth_bars[0][0]
    time_to_peak_min = int((peak_ts - open_ts).total_seconds() // 60)

    # First-N-minute slices
    def slice_first(n_min: int):
        end_ts = open_ts + pd.Timedelta(minutes=n_min)
        sl = [b for ts, b in rth_bars if ts < end_ts]
        if not sl:
            return None, None
        sl_high = max(float(b["high"]) for b in sl)
        sl_vol = sum(int(b["volume"]) for b in sl)
        sl_close = float(sl[-1]["close"])
        # return from open to end of slice
        return (sl_close - rth_open) / rth_open, sl_vol, (sl_high - rth_open) / rth_open

    first_5_close_ret, first_5_vol, first_5_max_ret = slice_first(5)
    first_30_close_ret, first_30_vol, first_30_max_ret = slice_first(30)
    first_60_close_ret, first_60_vol, first_60_max_ret = slice_first(60)

    # Premarket
    has_pm = bool(pm_bars)
    pm_high = max((float(b["high"]) for _, b in pm_bars), default=None)
    pm_low = min((float(b["low"]) for _, b in pm_bars), default=None)
    pm_volume = sum(int(b["volume"]) for _, b in pm_bars) if pm_bars else 0
    pm_high_vs_rth_open = (pm_high - rth_open) / rth_open if pm_high else None
    pm_range_pct = ((pm_high - pm_low) / pm_low) if (pm_high and pm_low and pm_low > 0) else None
    # premarket gap: last premarket close vs rth_open (if pm bars exist)
    if pm_bars:
        pm_last_close = float(pm_bars[-1][1]["close"])
        pm_to_open_gap = (rth_open - pm_last_close) / pm_last_close if pm_last_close > 0 else None
    else:
        pm_to_open_gap = None

    return {
        "date": date_str,
        "ticker": ticker,
        "rth_open": rth_open,
        "rth_close": rth_close,
        "rth_high": rth_high,
        "rth_low": rth_low,
        "rth_volume": int(rth_volume),
        "max_return_from_open": float(max_return),
        "min_return_from_open": float(min_return),
        "close_return": float(close_return),
        "intraday_range_pct": float(intraday_range_pct),
        "time_to_peak_min": int(time_to_peak_min),
        "first_5min_close_return": first_5_close_ret,
        "first_5min_max_return": first_5_max_ret,
        "first_5min_volume": first_5_vol,
        "first_30min_close_return": first_30_close_ret,
        "first_30min_max_return": first_30_max_ret,
        "first_30min_volume": first_30_vol,
        "first_60min_close_return": first_60_close_ret,
        "first_60min_max_return": first_60_max_ret,
        "first_60min_volume": first_60_vol,
        "has_premarket": has_pm,
        "pm_high_vs_rth_open": pm_high_vs_rth_open,
        "pm_range_pct": pm_range_pct,
        "pm_to_open_gap": pm_to_open_gap,
        "pm_volume": int(pm_volume),
        "n_rth_bars": len(rth_bars),
    }


def main() -> None:
    rows: list[dict] = []
    skipped: list[tuple[str, str, str]] = []

    date_dirs = sorted(p for p in BAR_DIR.iterdir() if p.is_dir())
    print(f"Scanning {len(date_dirs)} date directories ...")

    for d in date_dirs:
        date_str = d.name
        for f in d.glob("*.json"):
            ticker = f.stem
            try:
                obj = json.loads(f.read_text())
            except Exception as e:
                skipped.append((date_str, ticker, f"json_parse:{e}"))
                continue
            bars = obj.get("bars") or []
            row = metrics_for_one(date_str, ticker, bars)
            if row is None:
                skipped.append((date_str, ticker, "no_rth_bars"))
                continue
            rows.append(row)

    df = pd.DataFrame(rows)
    print(f"\nWrote {len(df)} (date, ticker) rows; skipped {len(skipped)}.")

    # Per-date rank by max_return_from_open
    df["per_date_rank_by_max_ret"] = df.groupby("date")["max_return_from_open"].rank(
        method="dense", ascending=False
    ).astype(int)
    df["per_date_n"] = df.groupby("date")["ticker"].transform("count")

    df = df.sort_values(["date", "per_date_rank_by_max_ret"]).reset_index(drop=True)
    df.to_parquet(OUT_PARQUET, index=False)
    print(f"Wrote {OUT_PARQUET.relative_to(REPO)}")

    # ---------- Summary ----------
    summary: dict = {}
    summary["n_rows"] = len(df)
    summary["n_dates"] = int(df["date"].nunique())
    summary["n_unique_tickers"] = int(df["ticker"].nunique())
    summary["n_skipped"] = len(skipped)

    # Distribution of per-day max-mover max_return_from_open
    top_per_day = df[df["per_date_rank_by_max_ret"] == 1]
    summary["top_mover_per_day"] = {
        "mean_max_return": float(top_per_day["max_return_from_open"].mean()),
        "median_max_return": float(top_per_day["max_return_from_open"].median()),
        "p25": float(top_per_day["max_return_from_open"].quantile(0.25)),
        "p75": float(top_per_day["max_return_from_open"].quantile(0.75)),
        "min": float(top_per_day["max_return_from_open"].min()),
        "max": float(top_per_day["max_return_from_open"].max()),
    }
    # How many days have a mover above thresholds
    thresholds = [0.10, 0.20, 0.30, 0.50, 1.00, 2.00]
    summary["days_with_mover_above"] = {}
    for t in thresholds:
        days_with = top_per_day[top_per_day["max_return_from_open"] >= t].shape[0]
        summary["days_with_mover_above"][f"{int(t*100)}pct"] = {
            "n_days": int(days_with),
            "pct_of_dates": float(days_with / summary["n_dates"]),
        }

    # Population distribution (all rows)
    summary["all_rows_max_return_dist"] = {
        "mean": float(df["max_return_from_open"].mean()),
        "median": float(df["max_return_from_open"].median()),
        "p90": float(df["max_return_from_open"].quantile(0.90)),
        "p95": float(df["max_return_from_open"].quantile(0.95)),
        "p99": float(df["max_return_from_open"].quantile(0.99)),
        "max": float(df["max_return_from_open"].max()),
    }

    # Predictive power of first-30-min max return for being top mover
    df["is_top_mover"] = (df["per_date_rank_by_max_ret"] == 1).astype(int)
    # Per-date ranking by first_30min_max_return
    df["first_30_rank"] = df.groupby("date")["first_30min_max_return"].rank(
        method="dense", ascending=False
    )
    # How often does the actual top-mover also rank #1 by first_30_max?
    top_movers = df[df["is_top_mover"] == 1]
    n_top = len(top_movers)
    # Coverage at top-K of the first-30 ranking
    summary["first_30min_max_ret_predictive"] = {}
    for k in [1, 2, 3, 5]:
        n_in_top_k = int((top_movers["first_30_rank"] <= k).sum())
        summary["first_30min_max_ret_predictive"][f"top_{k}"] = {
            "n_top_movers_in_top_k": n_in_top_k,
            "share": float(n_in_top_k / n_top) if n_top else None,
        }

    # Premarket-volume signal
    df_pm = df[df["has_premarket"]].copy()
    if len(df_pm):
        df_pm["pm_vol_rank"] = df_pm.groupby("date")["pm_volume"].rank(
            method="dense", ascending=False
        )
        # of those days that have premarket data, how often is top-mover top pm-volume?
        top_pm = df_pm[df_pm["is_top_mover"] == 1]
        n_top_pm = len(top_pm)
        summary["pm_volume_predictive"] = {
            "n_dates_with_pm": int(df_pm["date"].nunique()),
            "n_top_movers_with_pm": n_top_pm,
        }
        for k in [1, 3, 5]:
            n_in = int((top_pm["pm_vol_rank"] <= k).sum())
            summary["pm_volume_predictive"][f"top_{k}_pm_vol"] = {
                "n_top_movers": n_in,
                "share": float(n_in / n_top_pm) if n_top_pm else None,
            }

    # Top 30 (date, ticker) movers across full sample
    biggest = df.sort_values("max_return_from_open", ascending=False).head(30)[
        ["date", "ticker", "rth_open", "rth_high", "rth_close",
         "max_return_from_open", "close_return", "time_to_peak_min",
         "first_30min_max_return", "rth_volume"]
    ]
    summary["top_30_movers_all_time"] = biggest.to_dict(orient="records")

    OUT_SUMMARY.write_text(json.dumps(summary, indent=2, default=str))
    print(f"Wrote {OUT_SUMMARY.relative_to(REPO)}")

    # Console preview
    print("\n=== Top mover per day, summary ===")
    print(json.dumps(summary["top_mover_per_day"], indent=2))
    print("\n=== Days with mover above threshold ===")
    print(json.dumps(summary["days_with_mover_above"], indent=2))
    print("\n=== first_30min_max_return predictive of top mover? ===")
    print(json.dumps(summary["first_30min_max_ret_predictive"], indent=2))
    if "pm_volume_predictive" in summary:
        print("\n=== pm_volume predictive of top mover? ===")
        print(json.dumps(summary["pm_volume_predictive"], indent=2))
    print("\n=== Top 10 movers all-time ===")
    print(biggest.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
