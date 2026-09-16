"""ML drift detector: monitor live feature distributions + model calibration.

Per the May 2026 Compass research synthesis (Pillar 1 §1.5 #5):
  Concept drift (P(Y|X)) vs data drift (P(X)) — different responses needed.
  Tools: PSI (industry standard, threshold >= 0.25), KS test, Wasserstein,
  Page-Hinkley for sequential change-point detection.

This module ships:
  - PSI (Population Stability Index) per feature
  - KS (Kolmogorov-Smirnov) two-sample test per feature
  - Page-Hinkley test on rolling model calibration
  - Drift report writer

Usage (cron-friendly):
    python scripts/ml_drift_detector.py --window-recent 30 --window-baseline 90

Output:
    data/models/drift_report_{date}.json
    Returns exit code 1 if any metric exceeds threshold (operator action needed).
"""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MODELS = REPO / "data" / "models"
MODELS.mkdir(parents=True, exist_ok=True)


def section(t):
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def psi(reference: np.ndarray, current: np.ndarray, n_bins: int = 10) -> float:
    """Population Stability Index.

    PSI = sum_i ((c_i - r_i) * ln(c_i / r_i))
    where c_i, r_i are bin proportions in current vs reference.

    Thresholds (industry standard):
      < 0.10  no significant change
      0.10-0.25  some change, monitor
      >= 0.25  significant drift, action needed
    """
    ref = np.asarray(reference, dtype=float)
    cur = np.asarray(current, dtype=float)
    ref = ref[~np.isnan(ref)]
    cur = cur[~np.isnan(cur)]
    if len(ref) < 50 or len(cur) < 50:
        return float("nan")
    # Use reference quantiles to bin both
    bins = np.unique(np.quantile(ref, np.linspace(0, 1, n_bins + 1)))
    if len(bins) < 3:
        return float("nan")
    r_counts, _ = np.histogram(ref, bins=bins)
    c_counts, _ = np.histogram(cur, bins=bins)
    r_prop = (r_counts + 1) / (r_counts.sum() + n_bins)  # smooth
    c_prop = (c_counts + 1) / (c_counts.sum() + n_bins)
    return float(np.sum((c_prop - r_prop) * np.log(c_prop / r_prop)))


def ks_test(reference: np.ndarray, current: np.ndarray) -> tuple[float, float]:
    """Kolmogorov-Smirnov two-sample test. Returns (statistic, p_value)."""
    from scipy.stats import ks_2samp
    ref = np.asarray(reference, dtype=float)
    cur = np.asarray(current, dtype=float)
    ref = ref[~np.isnan(ref)]
    cur = cur[~np.isnan(cur)]
    if len(ref) < 50 or len(cur) < 50:
        return float("nan"), float("nan")
    res = ks_2samp(ref, cur)
    return float(res.statistic), float(res.pvalue)


def page_hinkley(values: np.ndarray, delta: float = 0.005, threshold: float = 50.0) -> dict:
    """Page-Hinkley sequential change-point test.

    Detects whether the running average has shifted DOWN (model degrading).
    Returns earliest index where threshold was exceeded, or -1 if none.

    Args:
        values: time-ordered metric values (e.g., per-trade P&L)
        delta: minimum magnitude to detect (e.g., 0.5pp drop)
        threshold: alarm threshold (50 = aggressive, 100 = conservative)
    """
    v = np.asarray(values, dtype=float)
    v = v[~np.isnan(v)]
    if len(v) < 10:
        return {"alarm_idx": -1, "max_PH": 0.0, "n": len(v)}
    mean = v.mean()
    PH_min = 0.0
    PH_t = 0.0
    alarm_idx = -1
    max_PH = 0.0
    for i, x in enumerate(v):
        PH_t += (mean - x - delta)
        PH_min = min(PH_min, PH_t)
        cur_PH = PH_t - PH_min
        max_PH = max(max_PH, cur_PH)
        if cur_PH > threshold and alarm_idx < 0:
            alarm_idx = i
    return {"alarm_idx": int(alarm_idx), "max_PH": float(max_PH), "n": len(v)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--window-recent", type=int, default=30,
                         help="Recent window size in days (current)")
    parser.add_argument("--window-baseline", type=int, default=90,
                         help="Baseline window in days (reference)")
    parser.add_argument("--psi-threshold", type=float, default=0.25)
    parser.add_argument("--ph-delta", type=float, default=0.005)
    parser.add_argument("--ph-threshold", type=float, default=50.0)
    args = parser.parse_args()

    section("STEP 1 - Load data")
    aftermath = (DERIVED / "aftermath_strat.parquet").as_posix()
    con = duckdb.connect()
    con.sql("SET memory_limit='6GB'")
    con.sql(f"SET temp_directory='{(REPO / '.duckdb_tmp').as_posix()}'")
    df = con.sql(f"""
        SELECT * FROM read_parquet('{aftermath}')
        WHERE ca_flag = 'clean' AND ret_t5 IS NOT NULL
          AND open BETWEEN 0.5 AND 50 AND volume > 100000
        ORDER BY d0
    """).df()
    df["d0"] = pd.to_datetime(df["d0"])
    print(f"  loaded {len(df):,} rows, date range {df['d0'].min()} -> {df['d0'].max()}")

    end_date = df["d0"].max()
    cur_start = end_date - timedelta(days=args.window_recent)
    base_start = cur_start - timedelta(days=args.window_baseline)
    cur = df[df["d0"] >= cur_start]
    base = df[(df["d0"] >= base_start) & (df["d0"] < cur_start)]
    print(f"  baseline: {base_start.date()} -> {cur_start.date()} ({len(base):,} rows)")
    print(f"  recent:   {cur_start.date()} -> {end_date.date()} ({len(cur):,} rows)")

    section("STEP 2 - PSI per feature (data drift)")
    monitor_features = [
        "open", "intraday_pct", "ret_open_close_d0", "dvol_d0", "volume",
    ]
    print(f"  {'feature':<22} {'PSI':>8}  status")
    drift_alerts = []
    for f in monitor_features:
        if f not in df.columns: continue
        score = psi(base[f].values, cur[f].values)
        if pd.isna(score):
            status = "skip (insufficient data)"
        elif score < 0.10:
            status = "stable"
        elif score < args.psi_threshold:
            status = "monitor"
        else:
            status = "DRIFT (action needed)"
            drift_alerts.append(f"PSI {f}={score:.3f}")
        print(f"  {f:<22} {score:>7.3f}  {status}")

    section("STEP 3 - KS two-sample test per feature")
    print(f"  {'feature':<22} {'KS_stat':>8} {'p_value':>10}  status")
    for f in monitor_features:
        if f not in df.columns: continue
        stat, p = ks_test(base[f].values, cur[f].values)
        if pd.isna(stat):
            status = "skip"
        elif p < 0.001:
            status = "DRIFT (p<0.001)"
            drift_alerts.append(f"KS {f} p={p:.4f}")
        elif p < 0.01:
            status = "monitor (p<0.01)"
        else:
            status = "stable"
        print(f"  {f:<22} {stat:>7.3f} {p:>9.4f}  {status}")

    section("STEP 4 - Page-Hinkley on per-trade T+5 returns (concept drift)")
    # Take recent N trades and check if avg has shifted down
    recent_trades = df.tail(500).sort_values("d0")
    ph = page_hinkley(recent_trades["ret_t5"].values,
                       delta=args.ph_delta, threshold=args.ph_threshold)
    if ph["alarm_idx"] >= 0:
        status = f"ALARM at trade {ph['alarm_idx']} (max_PH={ph['max_PH']:.2f})"
        drift_alerts.append(f"Page-Hinkley alarm at idx {ph['alarm_idx']}")
    else:
        status = f"no alarm (max_PH={ph['max_PH']:.2f})"
    print(f"  {status}")

    section("STEP 5 - Summary")
    n_alerts = len(drift_alerts)
    if n_alerts == 0:
        print(f"  All checks PASSED. No drift detected.")
    else:
        print(f"  {n_alerts} drift alert(s):")
        for a in drift_alerts:
            print(f"    - {a}")

    today = datetime.utcnow().strftime("%Y-%m-%d")
    out = MODELS / f"drift_report_{today}.json"
    out.write_text(json.dumps({
        "date": today,
        "window_recent_days": args.window_recent,
        "window_baseline_days": args.window_baseline,
        "n_drift_alerts": n_alerts,
        "alerts": drift_alerts,
        "ph_threshold": args.ph_threshold,
        "psi_threshold": args.psi_threshold,
    }, indent=2))
    print(f"\n  Wrote {out}")
    return 1 if n_alerts > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
