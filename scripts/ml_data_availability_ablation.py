"""Ablation: does v3 perform differently on rows with microstructure / news data?

This is the prerequisite to Path B (data-routed v4) from doc 117.

If v3 ALREADY performs better on rows with new data — that's a regime
effect we can exploit by a confidence gate (no v4 needed).

If v3 performs WORSE on those rows — those rows are harder regimes where
extra features could plausibly help.

If v3 performs the SAME — sparse features are pure noise for production.

Compares v3-tuned-16fold predictions on:
  - All rows (production baseline)
  - has_microstructure=1 only (rows in trades_v1 21-day window)
  - has_news=1 only (rows with at least one news article)
  - has_microstructure=1 AND has_news=1 (intersection)
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

import duckdb
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MODELS = REPO / "data" / "models"


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def evaluate_slice(df: pd.DataFrame, label: str, p_thr: float = 0.30) -> dict:
    """Evaluate predictions at threshold; return tier-style stats."""
    yreg = df["y_reg"].clip(-0.5, 1.0)
    mask = df["prob_continuer"] >= p_thr
    n = int(mask.sum())
    if n == 0:
        return {"label": label, "n_total": len(df), "n_picks": 0,
                "avg_pct": 0.0, "win_pct": 0.0}
    avg = float(yreg[mask].mean() * 100)
    win = float((yreg[mask] > 0).mean() * 100)
    return {
        "label": label, "n_total": len(df),
        "n_picks": n, "pick_rate_pct": float(n / len(df) * 100),
        "avg_pct": avg, "win_pct": win,
    }


def main():
    section("STEP 1 - Load WF predictions + microstructure + news")
    v3_p = DERIVED / "ml_v2_walkforward_predictions_v3_tuned_16fold.parquet"
    micro_p = DERIVED / "microstructure_features.parquet"
    news_p = DERIVED / "news_features_polygon_180d.parquet"

    v3 = pd.read_parquet(v3_p)
    v3["d0"] = pd.to_datetime(v3["d0"])
    print(f"  v3 OOS predictions: {len(v3):,} rows")

    micro = pd.read_parquet(micro_p)[["ticker", "d0"]].copy()
    micro["d0"] = pd.to_datetime(micro["d0"])
    micro["has_microstructure"] = 1
    print(f"  microstructure keys: {len(micro):,}")

    news_full = pd.read_parquet(news_p)[["ticker", "d0", "n_articles_24h"]].copy()
    news_full["d0"] = pd.to_datetime(news_full["d0"])
    news_full["has_news"] = (news_full["n_articles_24h"] > 0).astype(int)
    news_full["has_news_data"] = 1   # any news fetch attempt = "we have data for this date"
    print(f"  news keys: {len(news_full):,} ({news_full['has_news'].sum()} with articles)")

    # Join
    df = v3.merge(micro, on=["ticker", "d0"], how="left")
    df["has_microstructure"] = df["has_microstructure"].fillna(0).astype(int)
    df = df.merge(news_full[["ticker", "d0", "has_news", "has_news_data"]],
                   on=["ticker", "d0"], how="left")
    df["has_news"] = df["has_news"].fillna(0).astype(int)
    df["has_news_data"] = df["has_news_data"].fillna(0).astype(int)
    print(f"  joined: {len(df):,} rows ({df['has_microstructure'].sum()} micro, "
          f"{df['has_news'].sum()} with news, "
          f"{df['has_news_data'].sum()} in news fetch window)")

    section("STEP 2 - Slice analysis (P>=0.30)")
    slices = [
        ("ALL ROWS (baseline)", df),
        ("has_microstructure=1", df[df["has_microstructure"] == 1]),
        ("has_microstructure=0", df[df["has_microstructure"] == 0]),
        ("has_news=1 (>=1 article)", df[df["has_news"] == 1]),
        ("has_news=0 BUT in fetch window", df[(df["has_news"] == 0) & (df["has_news_data"] == 1)]),
        ("micro=1 AND news=1", df[(df["has_microstructure"] == 1) & (df["has_news"] == 1)]),
        ("micro=1 AND news=0", df[(df["has_microstructure"] == 1) & (df["has_news"] == 0) & (df["has_news_data"] == 1)]),
    ]

    print(f"  {'slice':<45} {'rows':>5} {'picks':>5} {'pick%':>6} {'avg':>8} {'win%':>6}")
    print(f"  {'-'*82}")
    results = []
    for label, slc in slices:
        r = evaluate_slice(slc, label)
        results.append(r)
        print(f"  {label:<45} {r['n_total']:>5,} {r['n_picks']:>5,} "
              f"{r.get('pick_rate_pct', 0):>5.1f}% {r['avg_pct']:>+7.2f}% "
              f"{r['win_pct']:>5.1f}%")

    section("STEP 3 - Higher tier slices (P>=0.50)")
    print(f"  {'slice':<45} {'rows':>5} {'picks':>5} {'pick%':>6} {'avg':>8} {'win%':>6}")
    print(f"  {'-'*82}")
    for label, slc in slices[:5]:
        r = evaluate_slice(slc, label, p_thr=0.50)
        print(f"  {label:<45} {r['n_total']:>5,} {r['n_picks']:>5,} "
              f"{r.get('pick_rate_pct', 0):>5.1f}% {r['avg_pct']:>+7.2f}% "
              f"{r['win_pct']:>5.1f}%")

    section("STEP 4 - Verdict + recommendation")
    all_r = results[0]
    micro_r = results[1]
    news_r = results[3]

    print(f"  Baseline (all rows P>=0.30):    +{all_r['avg_pct']:.2f}% on {all_r['n_picks']:,}")
    print(f"  Microstructure-1 slice:           +{micro_r['avg_pct']:.2f}% on {micro_r['n_picks']:,}")
    print(f"  News-1 slice:                     +{news_r['avg_pct']:.2f}% on {news_r['n_picks']:,}")
    print()

    micro_lift = micro_r['avg_pct'] - all_r['avg_pct']
    news_lift = news_r['avg_pct'] - all_r['avg_pct']

    print(f"  Microstructure-1 vs baseline lift: {micro_lift:+.2f}pp")
    print(f"  News-1 vs baseline lift:           {news_lift:+.2f}pp")

    if micro_lift > 2.0:
        print(f"\n  *** MICRO-1 lifts > +2pp: confidence-gating on has_microstructure")
        print(f"      could improve broad-tier P&L without retraining v4")
    elif micro_lift < -2.0:
        print(f"\n  --- MICRO-1 lifts < -2pp: those rows are HARDER regimes;")
        print(f"      v4 routing model could plausibly help if trained well")
    else:
        print(f"\n  -   MICRO-1 lift within +/-2pp: data availability is uninformative")
        print(f"      v4 retrain unlikely to help even with full coverage")

    if news_lift > 2.0:
        print(f"\n  *** NEWS-1 lifts > +2pp: confidence-gating on has_news could help")
    elif news_lift < -2.0:
        print(f"\n  --- NEWS-1 lifts < -2pp: news-driven gaps are harder regimes")
    else:
        print(f"\n  -   NEWS-1 lift within +/-2pp: news coverage is uninformative for v3")

    section("STEP 5 - Persist")
    out = {
        "baseline": all_r,
        "micro_lift_pp": micro_lift,
        "news_lift_pp": news_lift,
        "all_slices": results,
    }
    out_path = MODELS / "data_availability_ablation.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(f"  Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
