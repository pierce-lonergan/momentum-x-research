"""Tier-1 candidate feature generator (hand-crafted, typology-aligned).

Per the May 2026 Compass synthesis (DeepInsightTheorem typology):
  - CONSTRUCTION: derived signals from raw OHLCV+catalyst
  - THEOREM CALL: invoke known empirical regularities
  - TRANSFORMATION: change basis / timeframe / population

This script hand-crafts ~30 candidate features across all three categories,
runs each through the rigor verifier (leakage probe + PSR + DSR with
multiple-testing correction at the search size), and reports survivors.

The LLM-as-Generator can replace the hand-crafted batch later — for now
we exercise the full Verifier pipeline with curated proposals.

Outputs:
  data/polygon_warehouse/derived/tier1_feature_results.parquet
  Console table of survivors
"""
from __future__ import annotations
import sys
from pathlib import Path
from typing import Callable

import duckdb
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"

sys.path.insert(0, str(REPO / "scripts"))
from ml_rigor_verifier import verify_feature  # noqa: E402


def section(t):
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def load_data() -> pd.DataFrame:
    """Same loader as v2: aftermath_strat + walk-forward priors + cross-sectional."""
    aftermath = (DERIVED / "aftermath_strat.parquet").as_posix()
    con = duckdb.connect()
    con.sql("SET memory_limit='6GB'")
    con.sql(f"SET temp_directory='{(REPO / '.duckdb_tmp').as_posix()}'")
    df = con.sql(f"""
        WITH base AS (
            SELECT * FROM read_parquet('{aftermath}')
            WHERE ca_flag = 'clean' AND ret_t5 IS NOT NULL
              AND open BETWEEN 0.5 AND 50 AND volume > 100000
        ),
        enriched AS (
            SELECT *,
                   COUNT(*) OVER (PARTITION BY ticker ORDER BY d0
                                   ROWS BETWEEN UNBOUNDED PRECEDING
                                       AND 1 PRECEDING) AS prior_n,
                   SUM(CASE WHEN ret_t5 >= 0.10 THEN 1 ELSE 0 END) OVER (
                       PARTITION BY ticker ORDER BY d0
                       ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prior_cont,
                   SUM(CASE WHEN ret_t5 <= -0.10 THEN 1 ELSE 0 END) OVER (
                       PARTITION BY ticker ORDER BY d0
                       ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prior_fade,
                   COUNT(*) OVER (PARTITION BY ticker
                                   ORDER BY d0
                                   RANGE BETWEEN INTERVAL 7 DAY PRECEDING
                                           AND INTERVAL 1 DAY PRECEDING) AS prior_7d_count,
                   AVG(ret_t5) OVER (PARTITION BY ticker ORDER BY d0
                                     ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prior_avg_t5,
                   AVG(intraday_pct) OVER (PARTITION BY ticker ORDER BY d0
                                           ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prior_avg_intra
            FROM base
        ),
        cross_sectional AS (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY d0 ORDER BY intraday_pct DESC) AS rank_intra,
                   ROW_NUMBER() OVER (PARTITION BY d0 ORDER BY dvol_d0 DESC) AS rank_dvol,
                   COUNT(*) OVER (PARTITION BY d0) AS n_today,
                   AVG(intraday_pct) OVER (PARTITION BY d0) AS today_avg_intra,
                   AVG(dvol_d0) OVER (PARTITION BY d0) AS today_avg_dvol,
                   STDDEV(intraday_pct) OVER (PARTITION BY d0) AS today_std_intra
            FROM enriched
        )
        SELECT * FROM cross_sectional ORDER BY d0
    """).df()
    return df


# ── Candidate feature library ──

def candidate_features() -> dict[str, Callable[[pd.DataFrame], pd.Series]]:
    """Return name -> callable mapping. Each callable takes the loaded df
    and returns a Series of the same length."""
    F: dict[str, Callable] = {}

    # ── CONSTRUCTION (derived from raw OHLCV+catalyst) ──
    F["spike_intensity"] = lambda d: d["intraday_pct"] / np.log(d["dvol_d0"].clip(lower=1))
    F["close_efficiency"] = lambda d: d["ret_open_close_d0"] / d["intraday_pct"].clip(lower=0.01)
    F["fade_probability_proxy"] = lambda d: 1 - d["ret_open_close_d0"] / d["intraday_pct"].clip(lower=0.01)
    F["dollar_per_dollar_pumped"] = lambda d: d["dvol_d0"] / (d["intraday_pct"] * d["open"]).clip(lower=1)
    F["volume_to_price_ratio"] = lambda d: d["volume"] / d["open"].clip(lower=0.01)
    F["log_dvol_x_intra"] = lambda d: np.log(d["dvol_d0"].clip(lower=1)) * d["intraday_pct"]
    F["sqrt_intra_x_log_dvol"] = lambda d: np.sqrt(d["intraday_pct"].clip(lower=0)) * np.log(d["dvol_d0"].clip(lower=1))

    # ── THEOREM CALL (invoke known empirical regularities) ──
    # Mean reversion (Lo-MacKinlay): high recent returns → expect lower forward
    F["mean_reversion_proxy"] = lambda d: -d["intraday_pct"]
    # Cascade anti-selection: many recent appearances + high intra = exhaustion
    F["cascade_exhaustion"] = lambda d: d["prior_7d_count"].fillna(0) * d["intraday_pct"]
    # Float-rotation proxy: dvol / shares
    F["float_rotation_proxy"] = lambda d: d["dvol_d0"] / (d["open"] * 5e6).clip(lower=1)
    # Strong-into-close = continuation per ORB / gap-and-go literature
    F["close_strength_inverse"] = lambda d: -d["ret_open_close_d0"]
    # PSAR-style: how strong was the close vs intraday range
    F["close_in_range"] = lambda d: (d["close_t0"] - d["low"]) / (d["high"] - d["low"]).clip(lower=0.001)

    # ── TRANSFORMATION (change basis) ──
    # Log returns
    F["log_intraday"] = lambda d: np.log1p(d["intraday_pct"].clip(lower=-0.99))
    F["log_oc"] = lambda d: np.log1p(d["ret_open_close_d0"].clip(lower=-0.99))
    # Z-score within today's universe
    F["intra_zscore_today"] = lambda d: (d["intraday_pct"] - d["today_avg_intra"]) / d["today_std_intra"].clip(lower=0.01)
    # Rank * relative strength
    F["rank_x_zscore"] = lambda d: d["rank_intra"] * (d["intraday_pct"] / d["today_avg_intra"].clip(lower=0.01))
    # Log-rank
    F["log_rank_intra"] = lambda d: np.log1p(d["rank_intra"])
    # Square-root rank (compresses tail)
    F["sqrt_rank_intra"] = lambda d: np.sqrt(d["rank_intra"])

    # ── COMBINED (multi-class interactions) ──
    F["fresh_x_low_dvol"] = lambda d: (
        ((d["prior_n"].fillna(0) == 0).astype(float))
        * (1.0 / np.log(d["dvol_d0"].clip(lower=1)))
    )
    F["chronic_fader_indicator"] = lambda d: (
        (((20.78 + d["prior_cont"].fillna(0)*100) / (100 + d["prior_n"].fillna(0))) < 3.0).astype(float)
        * (d["prior_n"].fillna(0) >= 5).astype(float)
    )
    F["new_ticker_in_high_breadth"] = lambda d: (
        (d["prior_n"].fillna(0) == 0).astype(float)
        * (d["n_today"] > 30).astype(float)
    )
    F["expected_reversal_score"] = lambda d: (
        d["intraday_pct"]
        * (1 + d["prior_7d_count"].fillna(0) * 0.5)
        * (d["dvol_d0"] / 1e7).clip(lower=0)
    )
    F["pump_purity_score"] = lambda d: (
        d["dvol_d0"] / (d["volume"] * d["intraday_pct"]).clip(lower=1)
    )
    F["smart_money_proxy"] = lambda d: (
        d["dvol_d0"] / d["today_avg_dvol"].clip(lower=1) * d["close_t0"] / d["open"].clip(lower=0.01)
    )
    F["rank_consistency"] = lambda d: (d["rank_intra"] - d["rank_dvol"]).abs()

    # ── TIME (seasonality) ──
    d_dt = pd.to_datetime("2024-01-01")  # placeholder
    F["dow_weekend_proxy"] = lambda d: pd.to_datetime(d["d0"]).dt.dayofweek.astype(int)
    F["month_end"] = lambda d: (pd.to_datetime(d["d0"]).dt.day > 25).astype(int)
    F["q4"] = lambda d: (pd.to_datetime(d["d0"]).dt.month >= 10).astype(int)

    return F


def main():
    section("STEP 1 - Load data")
    df = load_data()
    print(f"  loaded {len(df):,} rows")
    target = df["ret_t5"]

    section("STEP 2 - Generate Tier-1 candidate features")
    feats = candidate_features()
    print(f"  generated {len(feats)} candidate features")

    section("STEP 3 - Sandbox-execute + verify each (with multiple-testing correction)")
    results = []
    n_trials = len(feats)  # multiple-testing correction
    for name, fn in feats.items():
        try:
            values = fn(df)
            if isinstance(values, np.ndarray):
                values = pd.Series(values, name=name)
            else:
                values.name = name
            res = verify_feature(values, target, name, n_trials_in_search=n_trials)
            results.append(res)
            print(f"  {name:35s}  passes={'YES' if res.passes else 'no '}  "
                  f"corr={res.leakage_probe.base_correlation:>+6.4f}  "
                  f"sr={res.sharpe:>+6.2f}  psr={res.psr:>5.3f}  dsr={res.dsr:>5.3f}  "
                  f"n={res.n_obs:>5d}  | {res.note[:40]}")
        except Exception as e:
            print(f"  {name:35s}  EXEC-ERROR: {str(e)[:60]}")
            results.append(None)

    section("STEP 4 - Survivors (passes both PSR>=0.95 AND DSR>=0.95)")
    survivors = [r for r in results if r is not None and r.passes]
    print(f"  {len(survivors)} of {len(feats)} survived after multiple-testing correction")
    if survivors:
        for r in sorted(survivors, key=lambda x: -x.dsr):
            print(f"    {r.feature_name:35s} sr={r.sharpe:>+5.2f} psr={r.psr:.3f} dsr={r.dsr:.3f}")

    section("STEP 5 - Top 10 by raw correlation (pre-DSR; informational)")
    valid = [r for r in results if r is not None]
    by_corr = sorted(valid, key=lambda r: -abs(r.leakage_probe.base_correlation))[:10]
    for r in by_corr:
        passes = "PASS" if r.passes else "fail"
        print(f"    {r.feature_name:35s}  corr={r.leakage_probe.base_correlation:>+6.4f}  "
              f"sr={r.sharpe:>+5.2f}  psr={r.psr:.3f}  dsr={r.dsr:.3f}  [{passes}]")

    section("STEP 6 - Persist results")
    rows = []
    for r in valid:
        rows.append({
            "feature": r.feature_name,
            "passes": r.passes,
            "base_corr": r.leakage_probe.base_correlation,
            "lagged_corr": r.leakage_probe.lagged_correlation,
            "leakage_suspicious": r.leakage_probe.suspicious,
            "sharpe": r.sharpe,
            "psr": r.psr,
            "dsr": r.dsr,
            "n_obs": r.n_obs,
            "note": r.note,
        })
    out = DERIVED / "tier1_feature_results.parquet"
    pd.DataFrame(rows).to_parquet(out, compression="zstd")
    print(f"  Wrote {out} ({len(rows)} feature results)")

    return [r.feature_name for r in survivors]


if __name__ == "__main__":
    main()
