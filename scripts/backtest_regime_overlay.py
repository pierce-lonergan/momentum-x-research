"""Regime-detection overlay for H3 (short the ripper).

Hypothesis: H3 worked in Jan-Feb 2026 because the universe was
pump-heavy. When pumps dried up (Mar-Apr), H3 flattened. If we can
detect "pump regime" intraday — using only information available at
10:00 ET — we can selectively enter H3 only on pump-rich days.

Daily regime features (all observable BY 10:00 ET):
  F1: pump_breadth = share of watchlist with first_30 ≥ 20%
  F2: pump_intensity = mean(first_30) of top-5 watchlist
  F3: peak_concentration = top-1 first_30 / mean(top-5 first_30)
  F4: reversal_count = (computed end-of-day, NOT useable for entry)
       — used only for diagnostic
  F5: avg_dollar_volume_first_30 = sum(close*vol) for first 30 min,
                                   averaged across watchlist top-5

Test plan:
  1. Compute features per date.
  2. Run H3 separately on dates where each feature is in top-third vs
     bottom-third.
  3. Combine multiple features into a simple regime score.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from backtest_5_hypotheses import (  # type: ignore
    load_all_dates, first_30_max_return, simulate_short_trade,
    simulate_long_trade, OUT_DIR,
)


def first_30_dollar_volume(rth: list) -> float:
    """sum of close*volume for first 30 RTH bars."""
    if not rth: return 0.0
    open_ts = rth[0][0]
    end_ts = open_ts + pd.Timedelta(minutes=30)
    sl = [b for ts, b in rth if ts < end_ts]
    return sum(float(b["close"]) * int(b["volume"]) for b in sl)


def daily_features(by_date: dict) -> pd.DataFrame:
    rows = []
    for date_str, tickers in by_date.items():
        f30s = []
        dvols = []
        n = 0
        for t in tickers:
            f30, _, _ = first_30_max_return(t["rth"])
            if f30 is None: continue
            n += 1
            f30s.append(f30)
            dvols.append(first_30_dollar_volume(t["rth"]))
        if not f30s: continue
        f30s_sorted = sorted(f30s, reverse=True)
        top5 = f30s_sorted[:5]
        # corresponding dollar vols for top-5 by f30
        paired = sorted(zip(f30s, dvols), key=lambda x: -x[0])[:5]
        top5_dvol = [d for _, d in paired]
        rows.append({
            "date": date_str,
            "univ_size": n,
            "F1_pump_breadth": sum(1 for x in f30s if x >= 0.20) / n,
            "F2_pump_intensity": sum(top5) / len(top5),
            "F3_peak_concentration": top5[0] / (sum(top5) / len(top5)) if top5[0] > 0 else 0,
            "F5_avg_dvol_top5": sum(top5_dvol) / len(top5_dvol) if top5_dvol else 0,
            "max_first_30": top5[0] if top5 else 0,
        })
    return pd.DataFrame(rows)


def run_h3_with_filter(by_date: dict, eligible_dates: set,
                        k=1, min_pct=0.20, tgt=0.30, stp=0.15) -> list[dict]:
    out = []
    for date_str, tickers in by_date.items():
        if date_str not in eligible_dates: continue
        scored = []
        for t in tickers:
            f30, end_ts, _ = first_30_max_return(t["rth"])
            if f30 is None or f30 < min_pct: continue
            scored.append((t, f30, end_ts))
        scored.sort(key=lambda x: -x[1])
        for t, f30, end_ts in scored[:k]:
            rth = t["rth"]
            entry_bar = next((b for ts, b in rth if ts >= end_ts), None)
            if entry_bar is None: continue
            ats = next(ts for ts, b in rth if ts >= end_ts)
            entry_px = float(entry_bar["open"])
            sim = simulate_short_trade(rth, ats, entry_px, tgt, stp)
            out.append({"date": date_str, "ticker": t["ticker"],
                        "first_30": f30,
                        "exit_reason": sim["exit_reason"], "pnl_pct": sim["exit_pct"]})
    return out


def summary(trades: list[dict], label: str) -> dict:
    if not trades: return {"label": label, "n": 0}
    df = pd.DataFrame(trades)
    return {
        "label": label, "n": len(df),
        "n_dates": int(df["date"].nunique()),
        "avg_pnl": float(df["pnl_pct"].mean()),
        "median_pnl": float(df["pnl_pct"].median()),
        "win_rate": float((df["pnl_pct"] > 0).mean()),
        "compound": float((1 + df["pnl_pct"]).prod() - 1),
        "target_rate": float((df["exit_reason"] == "target").mean()),
        "stop_rate": float((df["exit_reason"] == "stop").mean()),
    }


def main():
    by_date = load_all_dates()
    feat = daily_features(by_date)
    feat = feat.sort_values("date").reset_index(drop=True)
    feat.to_parquet(OUT_DIR / "regime_features.parquet", index=False)
    print(f"Features computed for {len(feat)} dates.")
    print(feat.describe().to_string())
    print()

    # For each feature, split top-third / bottom-third, run H3
    print("=== H3 performance conditional on regime feature (terciles) ===")
    feature_cols = ["F1_pump_breadth", "F2_pump_intensity", "F3_peak_concentration",
                    "F5_avg_dvol_top5", "univ_size"]
    out: dict = {"feature_terciles": {}}

    for fcol in feature_cols:
        # tercile by feature
        feat["bin"] = pd.qcut(feat[fcol], q=3, labels=["lo", "mid", "hi"], duplicates="drop")
        per_bin = {}
        for label in ["lo", "mid", "hi"]:
            dates = set(feat.loc[feat["bin"] == label, "date"])
            trades = run_h3_with_filter(by_date, dates)
            per_bin[label] = summary(trades, f"{fcol}={label}")
        out["feature_terciles"][fcol] = per_bin
        print(f"\n  {fcol}:")
        for label in ["lo", "mid", "hi"]:
            s = per_bin[label]
            print(f"    {label}: n={s.get('n', 0):3d}  "
                  f"avg={s.get('avg_pnl', 0)*100:+6.2f}%  "
                  f"win={s.get('win_rate', 0)*100:5.1f}%  "
                  f"compound={s.get('compound', 0)*100:+7.2f}%")

    # Composite regime: pump-rich = breadth in top tercile AND intensity in top tercile
    print("\n=== Composite regime filter: pump_breadth=hi AND pump_intensity=hi ===")
    feat["b1"] = pd.qcut(feat["F1_pump_breadth"], q=3, labels=["lo", "mid", "hi"], duplicates="drop")
    feat["b2"] = pd.qcut(feat["F2_pump_intensity"], q=3, labels=["lo", "mid", "hi"], duplicates="drop")
    pump_rich = set(feat.loc[(feat["b1"] == "hi") & (feat["b2"] == "hi"), "date"])
    pump_poor = set(feat.loc[(feat["b1"] == "lo") | (feat["b2"] == "lo"), "date"])
    pump_rich_trades = run_h3_with_filter(by_date, pump_rich)
    pump_poor_trades = run_h3_with_filter(by_date, pump_poor)
    print(f"  pump_rich (n_dates={len(pump_rich)}): {summary(pump_rich_trades, 'pump_rich')}")
    print(f"  pump_poor (n_dates={len(pump_poor)}): {summary(pump_poor_trades, 'pump_poor')}")
    out["composite_pump_rich"] = summary(pump_rich_trades, "pump_rich")
    out["composite_pump_poor"] = summary(pump_poor_trades, "pump_poor")

    # Unfiltered baseline
    all_trades = run_h3_with_filter(by_date, set(feat["date"]))
    out["baseline_all_dates"] = summary(all_trades, "baseline_all")
    print(f"\n  baseline (all dates):  {summary(all_trades, 'baseline_all')}")

    (OUT_DIR / "regime_overlay_summary.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nWrote {OUT_DIR / 'regime_overlay_summary.json'}")


if __name__ == "__main__":
    main()
