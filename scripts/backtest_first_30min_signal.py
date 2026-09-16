"""Backtest: buy top-K watchlist tickers ranked by first-30-min max return.

Rule:
  - At 09:30 ET, observe each watchlist ticker's bars.
  - At 10:00 ET (or first bar at/after 10:00 ET), rank by first_30min_max_return.
  - Buy top-K at the 10:00 ET bar's open price.
  - Exit:
      * Target +20% from entry (intrabar high crossing)
      * Stop  -10% from entry (intrabar low crossing)
      * Time stop: 15:55 ET (use that bar's close)
  - Per-day P&L = avg of K trades' return.

Also produces "high_movers_catalog.parquet" — every (date, ticker) where
max_return_from_open >= 0.30 (the user's "huge mover" set).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
BAR_DIR = REPO / "data" / "bar_recordings"
OUT_BACKTEST = REPO / "data" / "audits" / "first_30min_backtest.parquet"
OUT_CATALOG = REPO / "data" / "audits" / "high_movers_catalog.parquet"
OUT_SUMMARY = REPO / "data" / "audits" / "first_30min_backtest_summary.json"
OUT_BACKTEST.parent.mkdir(parents=True, exist_ok=True)

ET = ZoneInfo("America/New_York")

TARGET_PCT = 0.20
STOP_PCT = -0.10
HIGH_MOVER_THRESHOLD = 0.30
TOP_K_VALUES = [1, 2, 3, 5]


def parse_ts(s: str) -> datetime:
    if s.endswith("Z"):
        return datetime.fromisoformat(s[:-1]).replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(s)


def is_rth(ts_et: datetime) -> bool:
    h, m = ts_et.hour, ts_et.minute
    if h < 9 or h > 16:
        return False
    if h == 9 and m < 30:
        return False
    if h == 16:
        return False
    return True


def load_rth_bars(path: Path) -> list[tuple[datetime, dict]]:
    obj = json.loads(path.read_text())
    bars = obj.get("bars") or []
    out = []
    for b in bars:
        try:
            ts = parse_ts(b["timestamp"]).astimezone(ET)
        except Exception as e:
            print(f"WARN: load_rth_bars: bad timestamp in {path}: {e}")
            continue
        if is_rth(ts):
            out.append((ts, b))
    out.sort(key=lambda x: x[0])
    return out


def first_30min_metrics(rth_bars: list) -> tuple[float, float, float]:
    """Return (first_30min_max_return, entry_price_at_10am, rth_open)."""
    if not rth_bars:
        return None, None, None
    open_ts = rth_bars[0][0]
    rth_open = float(rth_bars[0][1]["open"])
    end_ts = open_ts + pd.Timedelta(minutes=30)
    sl = [(ts, b) for ts, b in rth_bars if ts < end_ts]
    if not sl:
        return None, None, None
    sl_high = max(float(b["high"]) for _, b in sl)
    first_30_max_ret = (sl_high - rth_open) / rth_open if rth_open > 0 else None
    # Find the bar at/after 10:00 ET — that's our entry
    entry_bar = None
    for ts, b in rth_bars:
        if ts >= end_ts:
            entry_bar = b
            break
    if entry_bar is None:
        return first_30_max_ret, None, rth_open
    entry_price = float(entry_bar["open"])
    return first_30_max_ret, entry_price, rth_open


def simulate_trade(rth_bars: list, entry_ts_after: datetime, entry_price: float) -> dict:
    """Walk forward from entry_ts_after; check target/stop/time-exit."""
    if entry_price <= 0:
        return {"exit_reason": "bad_entry", "exit_return": 0.0, "exit_min": 0}
    target = entry_price * (1 + TARGET_PCT)
    stop = entry_price * (1 + STOP_PCT)
    time_stop_ts = entry_ts_after.replace(hour=15, minute=55)
    last_close = entry_price
    last_min = 0
    open_ts = rth_bars[0][0]
    for ts, b in rth_bars:
        if ts < entry_ts_after:
            continue
        hi = float(b["high"])
        lo = float(b["low"])
        cl = float(b["close"])
        last_close = cl
        last_min = int((ts - open_ts).total_seconds() // 60)
        # If both target and stop hit in same bar, assume stop fills first (conservative)
        if lo <= stop:
            return {"exit_reason": "stop", "exit_return": STOP_PCT, "exit_min": last_min}
        if hi >= target:
            return {"exit_reason": "target", "exit_return": TARGET_PCT, "exit_min": last_min}
        if ts >= time_stop_ts:
            return {"exit_reason": "time_stop", "exit_return": (cl - entry_price) / entry_price, "exit_min": last_min}
    return {"exit_reason": "eod", "exit_return": (last_close - entry_price) / entry_price, "exit_min": last_min}


def main() -> None:
    candidates: list[dict] = []

    date_dirs = sorted(p for p in BAR_DIR.iterdir() if p.is_dir())
    print(f"Scanning {len(date_dirs)} dates ...")

    for d in date_dirs:
        date_str = d.name
        for f in d.glob("*.json"):
            ticker = f.stem
            try:
                rth = load_rth_bars(f)
            except Exception as e:
                print(f"WARN: load_rth_bars failed for {ticker} on {date_str}: {e}")
                continue
            if not rth:
                continue

            f30_max, entry_px, rth_open = first_30min_metrics(rth)
            if f30_max is None or entry_px is None:
                continue

            entry_ts_after = rth[0][0] + pd.Timedelta(minutes=30)
            sim = simulate_trade(rth, entry_ts_after, entry_px)

            # Also track what would have happened with full intraday max
            full_high = max(float(b["high"]) for _, b in rth)
            close_px = float(rth[-1][1]["close"])
            max_return_from_open = (full_high - rth_open) / rth_open if rth_open > 0 else 0.0
            close_return_from_open = (close_px - rth_open) / rth_open if rth_open > 0 else 0.0

            candidates.append({
                "date": date_str,
                "ticker": ticker,
                "rth_open": rth_open,
                "entry_px": entry_px,
                "first_30min_max_return": f30_max,
                "max_return_from_open": max_return_from_open,
                "close_return_from_open": close_return_from_open,
                "exit_reason": sim["exit_reason"],
                "exit_return": sim["exit_return"],
                "exit_min": sim["exit_min"],
                "rth_volume": sum(int(b["volume"]) for _, b in rth),
            })

    df = pd.DataFrame(candidates)
    print(f"Loaded {len(df)} candidates.")
    df["per_date_rank_30min"] = df.groupby("date")["first_30min_max_return"].rank(
        method="dense", ascending=False
    ).astype(int)
    df["per_date_rank_full"] = df.groupby("date")["max_return_from_open"].rank(
        method="dense", ascending=False
    ).astype(int)
    df = df.sort_values(["date", "per_date_rank_30min"]).reset_index(drop=True)
    df.to_parquet(OUT_BACKTEST, index=False)
    print(f"Wrote {OUT_BACKTEST.relative_to(REPO)}")

    # High movers catalog
    catalog = df[df["max_return_from_open"] >= HIGH_MOVER_THRESHOLD].copy()
    catalog = catalog.sort_values(["date", "max_return_from_open"], ascending=[True, False])
    catalog.to_parquet(OUT_CATALOG, index=False)
    print(f"Wrote {OUT_CATALOG.relative_to(REPO)}  ({len(catalog)} rows >= {HIGH_MOVER_THRESHOLD:.0%})")

    # ---------- Summary ----------
    summary: dict = {
        "config": {
            "target_pct": TARGET_PCT,
            "stop_pct": STOP_PCT,
            "high_mover_threshold": HIGH_MOVER_THRESHOLD,
            "entry_time": "10:00 ET (open of 31st RTH minute)",
            "exit_time": "first of: target, stop, 15:55 ET",
        },
        "n_dates": int(df["date"].nunique()),
        "n_candidates": int(len(df)),
        "n_high_movers": int(len(catalog)),
        "n_high_movers_per_date": {
            "mean": float(catalog.groupby("date").size().mean()),
            "median": float(catalog.groupby("date").size().median()),
            "max": int(catalog.groupby("date").size().max()),
        },
    }

    # Backtest by top-K
    summary["backtest_by_top_k"] = {}
    for k in TOP_K_VALUES:
        picks = df[df["per_date_rank_30min"] <= k].copy()
        n_trades = len(picks)
        n_dates = picks["date"].nunique()
        avg_return = float(picks["exit_return"].mean()) if n_trades else None
        win_rate = float((picks["exit_return"] > 0).mean()) if n_trades else None
        target_rate = float((picks["exit_reason"] == "target").mean()) if n_trades else None
        stop_rate = float((picks["exit_reason"] == "stop").mean()) if n_trades else None
        # daily portfolio return (equal-weight K)
        per_date = picks.groupby("date")["exit_return"].mean()
        cum_return = float(((1 + per_date).prod()) - 1) if len(per_date) else None
        sharpe_daily = float(per_date.mean() / per_date.std()) if per_date.std() else None
        summary["backtest_by_top_k"][f"k={k}"] = {
            "n_trades": n_trades,
            "n_dates_traded": int(n_dates),
            "avg_per_trade_return": avg_return,
            "win_rate": win_rate,
            "target_hit_rate": target_rate,
            "stop_hit_rate": stop_rate,
            "compounded_daily_return": cum_return,
            "daily_mean_return": float(per_date.mean()) if len(per_date) else None,
            "daily_std": float(per_date.std()) if len(per_date) else None,
            "daily_sharpe_unannualized": sharpe_daily,
        }

    # What share of true top-movers were captured by top-K of first_30min ranking?
    summary["true_top_mover_capture"] = {}
    true_tops = df[df["per_date_rank_full"] == 1]
    for k in TOP_K_VALUES:
        captured = int((true_tops["per_date_rank_30min"] <= k).sum())
        summary["true_top_mover_capture"][f"top_{k}"] = {
            "n_captured": captured,
            "share": float(captured / len(true_tops)) if len(true_tops) else None,
        }

    # Catalog summary
    summary["catalog_top_30"] = catalog.sort_values(
        "max_return_from_open", ascending=False
    ).head(30)[["date", "ticker", "rth_open", "max_return_from_open", "close_return_from_open",
                "first_30min_max_return", "per_date_rank_30min", "exit_return", "exit_reason"]].to_dict(orient="records")

    OUT_SUMMARY.write_text(json.dumps(summary, indent=2, default=str))
    print(f"Wrote {OUT_SUMMARY.relative_to(REPO)}\n")

    # Console preview
    print("=== Backtest by top-K ===")
    for k_label, stats in summary["backtest_by_top_k"].items():
        print(f"\n  {k_label}: n_trades={stats['n_trades']}  "
              f"avg/trade={stats['avg_per_trade_return']:+.2%}  "
              f"win={stats['win_rate']:.1%}  target={stats['target_hit_rate']:.1%}  "
              f"stop={stats['stop_hit_rate']:.1%}  "
              f"compound={stats['compounded_daily_return']:+.2%}")

    print("\n=== True top mover capture by first_30min top-K ===")
    print(json.dumps(summary["true_top_mover_capture"], indent=2))

    print("\n=== Top 10 high movers (by max intraday) ===")
    print(catalog.sort_values("max_return_from_open", ascending=False).head(10)[
        ["date", "ticker", "rth_open", "max_return_from_open",
         "first_30min_max_return", "per_date_rank_30min", "exit_return", "exit_reason"]
    ].to_string(index=False))


if __name__ == "__main__":
    main()
