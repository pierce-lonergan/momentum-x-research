"""LLM-as-Feature-Generator (Tier 1 per Compass artifact 1 §4.1).

WHY: Compass artifact 1 §4.1 rates Tier 1 LLM-as-Feature-Generator at 9/10
for sprint deliverables. Session 107 confirmed 0/28 hand-crafted features
survive multiple-testing-corrected DSR — so we need a different approach,
not more "by hand" cleverness.

The LLM here is THIS Claude instance (Opus 4.7). I act as the Generator
informed by:
  - Compass artifact 1 (DeepInsightTheorem typology: Construction / Theorem
    Call / Transformation)
  - Compass artifact 2 (Polygon-specific empirical guidance: Lou/Polk/Skouras
    intraday-fade, condition-code patterns, retail-attention-fade,
    rotation-ratio, catalyst-typology)
  - Session-106 negative finding (single-axis features lose; ensemble +
    regime overlay wins -- so propose features that interact with regime)
  - Session-107 negative finding (cohort priors lose; v2 ensemble extracts
    interactions -- so propose features that ARE interactions)

Each feature is sandboxed-executed, run through `verify_feature` (leakage
probe + PSR + DSR with multiple-testing correction at the search size),
and reported. Survivors get added to a curated "v3 candidate features"
artifact for inclusion in the next ensemble retrain.

Outputs:
  data/polygon_warehouse/derived/llm_feature_results.parquet
  data/models/llm_feature_survivors.json
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from typing import Callable

import duckdb
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MODELS = REPO / "data" / "models"

sys.path.insert(0, str(REPO / "scripts"))
from ml_rigor_verifier import verify_feature  # noqa: E402


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def load_data() -> pd.DataFrame:
    """Same loader as Tier-1 generator but also joins TCN preds + Ising regime
    so we can build INTERACTION features."""
    aftermath = (DERIVED / "aftermath_strat.parquet").as_posix()
    ising = (DERIVED / "ising_daily.parquet").as_posix()
    tcn_p = (DERIVED / "tcn_intraday_walkforward_predictions.parquet").as_posix()
    paths_p = (DERIVED / "intraday_paths_30min.parquet").as_posix()

    con = duckdb.connect()
    con.sql("SET memory_limit='6GB'")
    con.sql(f"SET temp_directory='{(REPO / '.duckdb_tmp').as_posix()}'")

    base = con.sql(f"""
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
                                           AND INTERVAL 1 DAY PRECEDING) AS prior_7d_count
            FROM base
        ),
        cross_sectional AS (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY d0 ORDER BY intraday_pct DESC) AS rank_intra,
                   ROW_NUMBER() OVER (PARTITION BY d0 ORDER BY dvol_d0 DESC) AS rank_dvol,
                   COUNT(*) OVER (PARTITION BY d0) AS n_today,
                   AVG(intraday_pct) OVER (PARTITION BY d0) AS today_avg_intra,
                   STDDEV(intraday_pct) OVER (PARTITION BY d0) AS today_std_intra
            FROM enriched
        ),
        with_ising AS (
            SELECT c.*, i.magnetization, i.n_huge_up,
                   AVG(i.magnetization) OVER (ORDER BY i.d ROWS BETWEEN 4 PRECEDING AND CURRENT ROW) AS mag_5d
            FROM cross_sectional c
            LEFT JOIN read_parquet('{ising}') i ON c.d0 = i.d
        ),
        with_tcn AS (
            SELECT w.*, t.tcn_proba
            FROM with_ising w
            LEFT JOIN read_parquet('{tcn_p}') t ON w.ticker = t.ticker AND w.d0 = t.d0
        ),
        with_path_summary AS (
            SELECT wt.*,
                   ps.first_5min_max_close,
                   ps.first_5min_min_close,
                   ps.last_5min_avg_close,
                   ps.first_5min_avg_volz,
                   ps.last_5min_avg_volz
            FROM with_tcn wt
            LEFT JOIN (
                SELECT ticker, d0,
                       MAX(close_rel) FILTER (WHERE bar_idx < 5) AS first_5min_max_close,
                       MIN(close_rel) FILTER (WHERE bar_idx < 5) AS first_5min_min_close,
                       AVG(close_rel) FILTER (WHERE bar_idx >= 25) AS last_5min_avg_close,
                       AVG(vol_z) FILTER (WHERE bar_idx < 5) AS first_5min_avg_volz,
                       AVG(vol_z) FILTER (WHERE bar_idx >= 25) AS last_5min_avg_volz
                FROM read_parquet('{paths_p}')
                GROUP BY ticker, d0
            ) ps ON wt.ticker = ps.ticker AND wt.d0 = ps.d0
        )
        SELECT * FROM with_path_summary ORDER BY d0
    """).df()
    return base


def llm_proposed_features() -> dict[str, Callable[[pd.DataFrame], pd.Series]]:
    """Features I propose as the LLM Generator, informed by both Compass
    artifacts. Each comes with a one-line theory of WHY it should work.

    Typology label is in the docstring (CONSTRUCTION / THEOREM CALL /
    TRANSFORMATION / INTERACTION).
    """
    F: dict[str, Callable] = {}

    # ── INTERACTION: TCN x cross-sectional rank ──
    # Theory: TCN-high alone fades (LSP 2019). But TCN-high + low rank-intra
    # = "fast riser ranked low overall" might be a different regime.
    F["tcn_x_rank_intra"] = lambda d: (
        d["tcn_proba"].fillna(0.5) * np.log1p(d["rank_intra"])
    )

    # ── INTERACTION: TCN x Ising mag (regime-conditional path) ──
    # Theory: a path that "looks like continuation" should be more reliable
    # when the broad regime is bullish (HI mag) than bearish.
    F["tcn_x_ising_mag5d"] = lambda d: (
        d["tcn_proba"].fillna(0.5) * d["mag_5d"].fillna(0)
    )

    # ── INTERACTION: TCN INVERSE x prior continuer rate ──
    # Theory: TCN-low + HI prior continuer rate = "this ticker has continued
    # before AND today's path is calm" — high-prob continuation.
    F["tcn_inverse_x_prior_cont_rate"] = lambda d: (
        (1 - d["tcn_proba"].fillna(0.5))
        * (1 + (d["prior_cont"].fillna(0) / d["prior_n"].clip(lower=1)))
    )

    # ── CONSTRUCTION: U-shape detector (drop then recover) ──
    # Theory: a U-shape in first 30 min often precedes continuation; an
    # inverted-U (rise then fade) precedes more fading.
    F["u_shape_intraday"] = lambda d: (
        d["last_5min_avg_close"].fillna(0)
        - 0.5 * (d["first_5min_max_close"].fillna(0) + d["first_5min_min_close"].fillna(0))
    )

    # ── CONSTRUCTION: Volume cooling rate ──
    # Theory: opening surge that COOLS quickly = exhaustion (fade); volume
    # that ACCELERATES into 10am = sustained interest (continue).
    F["volume_acceleration"] = lambda d: (
        d["last_5min_avg_volz"].fillna(0) - d["first_5min_avg_volz"].fillna(0)
    )

    # ── THEOREM CALL: LSP 2019 intraday fade prior ──
    # Theory: the higher today's intraday % the more it should fade. Use
    # this as an inverse signal — penalize high-intra picks.
    F["lsp_fade_penalty"] = lambda d: -d["intraday_pct"]

    # ── INTERACTION: rotation-ratio proxy x Ising regime ──
    # Theory (Compass artifact 2): "first-hour vol > 2x float = high-prob
    # continuation". We don't have float, but dvol/(open*proxy_float) ~ rotation.
    # Combined with regime: HI mag amplifies real rotation.
    F["rotation_x_ising"] = lambda d: (
        d["dvol_d0"] / (d["open"] * 5e6).clip(lower=1)
        * (1 + d["mag_5d"].fillna(0))
    )

    # ── TRANSFORMATION: log-rank x prior continuer (compress tail) ──
    # Theory: rank tail contains a lot of the action; log compresses it.
    # Combine with prior history to avoid noise from no-history names.
    F["log_rank_x_prior_continuer"] = lambda d: (
        np.log1p(d["rank_intra"])
        * ((d["prior_cont"].fillna(0) + 1) / (d["prior_n"].clip(lower=1) + 2))
    )

    # ── INTERACTION: pump-purity x close-strength (catalyst quality proxy) ──
    # Theory: high dvol/volume * close-near-high = "smart money" pattern
    # (real institutional buying that closes strong, not retail churn).
    F["pump_purity_x_close_strength"] = lambda d: (
        (d["dvol_d0"] / (d["volume"] * d["intraday_pct"]).clip(lower=1))
        * d["ret_open_close_d0"]
    )

    # ── INTERACTION: breadth gate x intraday rank ──
    # Theory: HI breadth (many movers) + LOW intra-rank means "this isn't
    # the leader of a broad rally" — likely sympathy; tends to fade.
    # Use as inverse signal.
    F["breadth_x_intra_rank_inverse"] = lambda d: -(
        d["n_huge_up"].fillna(0) * np.log1p(d["rank_intra"])
    )

    return F


def main():
    section("STEP 1 - Load enriched data (aftermath + Ising + TCN preds + path summary)")
    df = load_data()
    print(f"  loaded {len(df):,} rows")
    print(f"  features available:")
    for c in ["tcn_proba", "mag_5d", "first_5min_max_close", "last_5min_avg_volz"]:
        avail = df[c].notna().sum() if c in df.columns else 0
        print(f"    {c:<30} {avail:>5,}/{len(df):,} non-null")

    section("STEP 2 - LLM (Claude Opus 4.7)-proposed candidate features")
    feats = llm_proposed_features()
    print(f"  generated {len(feats)} candidate features (LLM = me)")
    target = df["ret_t5"]

    section("STEP 3 - Sandbox-execute + verify")
    n_trials = len(feats)
    results = []
    for name, fn in feats.items():
        try:
            values = fn(df)
            if isinstance(values, np.ndarray):
                values = pd.Series(values, name=name)
            else:
                values.name = name
            res = verify_feature(values, target, name, n_trials_in_search=n_trials)
            results.append(res)
            print(f"  {name:<40} pass={'YES' if res.passes else 'no '}  "
                  f"corr={res.leakage_probe.base_correlation:>+6.4f}  "
                  f"sr={res.sharpe:>+6.2f}  psr={res.psr:>5.3f}  dsr={res.dsr:>5.3f}  "
                  f"n={res.n_obs:>5d}")
        except Exception as e:
            print(f"  {name:<40} EXEC-ERROR: {str(e)[:60]}")
            results.append(None)

    section("STEP 4 - Survivors + top-by-corr")
    survivors = [r for r in results if r is not None and r.passes]
    print(f"  {len(survivors)} of {len(feats)} survived after multiple-testing correction")
    if survivors:
        for r in sorted(survivors, key=lambda x: -x.dsr):
            print(f"    SURVIVE: {r.feature_name:<40} sr={r.sharpe:>+5.2f} psr={r.psr:.3f} dsr={r.dsr:.3f}")

    valid = [r for r in results if r is not None]
    print()
    print("  Top 10 by |base_corr| (informational):")
    for r in sorted(valid, key=lambda r: -abs(r.leakage_probe.base_correlation))[:10]:
        passes = "PASS" if r.passes else "fail"
        print(f"    {r.feature_name:<40} corr={r.leakage_probe.base_correlation:>+6.4f}  "
              f"sr={r.sharpe:>+5.2f}  psr={r.psr:.3f}  dsr={r.dsr:.3f}  [{passes}]")

    section("STEP 5 - Persist")
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
    out = DERIVED / "llm_feature_results.parquet"
    pd.DataFrame(rows).to_parquet(out, compression="zstd")
    print(f"  Wrote {out} ({len(rows)} feature results)")

    surv_path = MODELS / "llm_feature_survivors.json"
    surv_path.write_text(json.dumps({
        "generator": "Claude Opus 4.7 (this session)",
        "informed_by": ["compass_artifact_1", "compass_artifact_2",
                        "session_106_findings", "session_107_findings"],
        "n_proposed": len(feats),
        "n_survived": len(survivors),
        "survivors": [r.feature_name for r in survivors],
        "top_by_corr": [r.feature_name for r in
                          sorted(valid, key=lambda x: -abs(x.leakage_probe.base_correlation))[:5]],
    }, indent=2))
    print(f"  Wrote {surv_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
