"""Bayesian Beta-Binomial cohort baseline for the continuer model.

WHY: The v2 stacked ensemble achieves +4.94% on P>=0.30 walk-forward (and
+10% with Ising HI-mag overlay). But how much of that edge is the ensemble
vs. simply knowing "this cohort historically continues at rate X"?

This script builds a deliberately simple Bayesian baseline:
  - Bin each row into a (intraday_decile, dvol_decile, sector_bucket) cohort
  - For each cohort, maintain a Beta(alpha, beta) posterior over
    P(continuer | cohort) using prior continuer events / fade events
  - Predict using the posterior MEAN (= alpha / (alpha + beta))
  - Provide a credible interval (= Beta CDF inverse at 5% / 95%)

This is the conjugate version of a Bayesian logistic regression — no MCMC,
fast, interpretable. If v2 ensemble can't beat this, we don't have an edge
beyond cohort-level historical base rates.

Outputs:
  data/polygon_warehouse/derived/bayesian_baseline_walkforward.parquet
  Console: per-decile lift table + comparison vs v2

Walk-forward semantics: at fold k, the cohort priors come from rows STRICTLY
before the fold's test window — no leakage.
"""
from __future__ import annotations
import json
import sys
from datetime import timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy.stats import beta as beta_dist

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MODELS = REPO / "data" / "models"
MODELS.mkdir(parents=True, exist_ok=True)


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def load_data() -> pd.DataFrame:
    """Same data slice as v2 ensemble for fair comparison.

    Joins ticker_details for sic_description (needed for sector cohort bucket).
    """
    aftermath = (DERIVED / "aftermath_strat.parquet").as_posix()
    td_path = REPO / "data" / "polygon_warehouse" / "reference" / "ticker_details.parquet"
    con = duckdb.connect()
    con.sql("SET memory_limit='6GB'")
    con.sql(f"SET temp_directory='{(REPO / '.duckdb_tmp').as_posix()}'")
    df = con.sql(f"""
        SELECT d0, ticker, intraday_pct, dvol_d0, ret_t5, ret_open_close_d0
        FROM read_parquet('{aftermath}')
        WHERE ca_flag = 'clean' AND ret_t5 IS NOT NULL
          AND open BETWEEN 0.5 AND 50 AND volume > 100000
        ORDER BY d0
    """).df()
    df["d0"] = pd.to_datetime(df["d0"])
    if td_path.exists():
        td = pd.read_parquet(td_path, columns=["ticker", "sic_description"])
        td = td.drop_duplicates(subset=["ticker"], keep="last")
        df = df.merge(td, on="ticker", how="left")
        n_with = df["sic_description"].notna().sum()
        print(f"  Joined ticker_details: {n_with}/{len(df)} have sic_description")
    else:
        df["sic_description"] = ""
        print("  ticker_details missing — sector bucket = OTHER for all rows")
    return df


def assign_cohort_vec(df: pd.DataFrame, intra_breaks: np.ndarray,
                       dvol_breaks: np.ndarray) -> pd.Series:
    """Vectorized cohort assignment for the whole DataFrame.

    Cohort id = intra_decile + dvol_decile + sector_bucket.
    """
    intra_idx = np.searchsorted(intra_breaks, df["intraday_pct"].values, side="right")
    dvol_idx = np.searchsorted(dvol_breaks, df["dvol_d0"].values, side="right")
    sic = df["sic_description"].fillna("").astype(str).str.upper()
    sec = pd.Series("OTHER", index=df.index)
    sec[sic.str.contains("PHARMACEUTICAL|BIOLOGICAL|MEDICAL", regex=True, na=False)] = "BIO"
    sec[sic.str.contains("SOFTWARE|SERVICES-COMPUTER", regex=True, na=False)] = "TECH"
    sec[sic.str.contains("CRUDE|MINING|PETROLEUM", regex=True, na=False)] = "ENERGY"
    sec[sic.str.contains("BLANK", regex=False, na=False)] = "SPAC"
    return ("i" + pd.Series(intra_idx, index=df.index).astype(str)
            + "_d" + pd.Series(dvol_idx, index=df.index).astype(str)
            + "_" + sec.values)


def fit_predict_walk_forward(df: pd.DataFrame,
                              train_window_days: int = 365,
                              test_window_days: int = 30,
                              prior_alpha: float = 2.0,
                              prior_beta: float = 8.0,
                              continuer_threshold: float = 0.10,
                              ci_alpha: float = 0.10) -> pd.DataFrame:
    """Walk-forward Bayesian Beta-Binomial cohort fit.

    For each fold:
      1. Compute decile breakpoints from TRAIN window only (no leakage)
      2. Assign each TRAIN row to a cohort
      3. Compute alpha = prior_alpha + sum(y==1), beta = prior_beta + sum(y==0)
         per cohort
      4. For each TEST row, look up cohort, predict posterior mean
      5. Provide credible interval [Beta.ppf(ci_alpha/2), Beta.ppf(1-ci_alpha/2)]
    """
    df = df.copy().sort_values("d0").reset_index(drop=True)
    df["y_cls"] = (df["ret_t5"] >= continuer_threshold).astype(int)

    dates = df["d0"]
    start = dates.min() + timedelta(days=train_window_days)
    end = dates.max()

    folds = []
    cur = start
    while cur < end:
        train_mask = (dates >= cur - timedelta(days=train_window_days)) & (dates < cur)
        test_mask = (dates >= cur) & (dates < cur + timedelta(days=test_window_days))
        if train_mask.sum() >= 100 and test_mask.sum() >= 30:
            folds.append((train_mask, test_mask))
        cur += timedelta(days=test_window_days)

    print(f"  n folds: {len(folds)}")

    all_preds = []
    for fold_i, (tr, te) in enumerate(folds):
        train = df[tr]
        test = df[te]

        # Decile breakpoints from TRAIN (10 bins → 9 cuts)
        intra_breaks = np.quantile(train["intraday_pct"].values,
                                     np.linspace(0.1, 0.9, 9))
        dvol_breaks = np.quantile(train["dvol_d0"].values,
                                    np.linspace(0.1, 0.9, 9))

        train_cohorts = assign_cohort_vec(train, intra_breaks, dvol_breaks)
        test_cohorts = assign_cohort_vec(test, intra_breaks, dvol_breaks)

        # Per-cohort posterior alpha, beta
        train_with = train.assign(_cohort=train_cohorts.values)
        agg = train_with.groupby("_cohort")["y_cls"].agg(["sum", "count"]).reset_index()
        agg["alpha"] = prior_alpha + agg["sum"]
        agg["beta"] = prior_beta + (agg["count"] - agg["sum"])
        cohort_post = dict(zip(agg["_cohort"], zip(agg["alpha"], agg["beta"])))

        # Default for unseen cohorts: prior only
        default_post = (prior_alpha, prior_beta)

        # Predict
        test_post = [cohort_post.get(c, default_post) for c in test_cohorts]
        post_arr = np.array(test_post)
        mean = post_arr[:, 0] / post_arr.sum(axis=1)
        ci_lo = beta_dist.ppf(ci_alpha / 2, post_arr[:, 0], post_arr[:, 1])
        ci_hi = beta_dist.ppf(1 - ci_alpha / 2, post_arr[:, 0], post_arr[:, 1])

        per_row = pd.DataFrame({
            "fold": fold_i,
            "d0": test["d0"].values,
            "ticker": test["ticker"].values,
            "y_cls": test["y_cls"].values,
            "y_reg": test["ret_t5"].values,
            "cohort": test_cohorts.values,
            "bayes_mean": mean,
            "bayes_ci_lo": ci_lo,
            "bayes_ci_hi": ci_hi,
            "n_train_for_cohort": [cohort_post.get(c, (0, 0))[0] +
                                    cohort_post.get(c, (0, 0))[1] - prior_alpha - prior_beta
                                    for c in test_cohorts],
        })
        all_preds.append(per_row)

    return pd.concat(all_preds, ignore_index=True)


def main():
    section("STEP 1 - Load data")
    df = load_data()
    print(f"  loaded {len(df):,} rows")

    section("STEP 2 - Walk-forward Bayesian fit")
    preds = fit_predict_walk_forward(df)
    print(f"  predictions: {len(preds):,} rows")

    section("STEP 3 - Aggregate by predicted-probability decile")
    preds["decile"] = pd.qcut(preds["bayes_mean"], 10, labels=False, duplicates="drop")
    print(f"  {'decile':<6} {'n':>5}  {'mean_p':>7}  {'avg_t5':>9}  {'win%':>6}")
    for d, g in preds.groupby("decile"):
        avg_t5 = g["y_reg"].clip(-0.5, 1.0).mean() * 100
        win = (g["y_reg"] > 0).mean() * 100
        print(f"  {d:<6} {len(g):>5,}  {g['bayes_mean'].mean():>7.3f}  "
              f"{avg_t5:>+8.2f}%  {win:>5.1f}%")

    section("STEP 4 - Threshold-based gates (compare with v2)")
    print(f"  {'gate':<25} {'n':>5}  {'avg_t5':>9}  {'win%':>6}")
    for thr in [0.20, 0.25, 0.30, 0.40, 0.50]:
        mask = preds["bayes_mean"] >= thr
        n = mask.sum()
        if n == 0:
            print(f"  bayes_mean >= {thr:.2f}    n=0")
            continue
        avg = preds.loc[mask, "y_reg"].clip(-0.5, 1.0).mean() * 100
        win = (preds.loc[mask, "y_reg"] > 0).mean() * 100
        print(f"  bayes_mean >= {thr:.2f}    {n:>5,}  {avg:>+8.2f}%  {win:>5.1f}%")

    section("STEP 5 - Compare against v2 ensemble (same WF span)")
    v2_path = DERIVED / "ml_v2_walkforward_predictions.parquet"
    if v2_path.exists():
        v2 = pd.read_parquet(v2_path)
        v2["d0"] = pd.to_datetime(v2["d0"])
        join_keys = ["d0", "ticker"]
        merged = preds.merge(v2[join_keys + ["prob_continuer"]],
                             on=join_keys, how="inner")
        print(f"  merged: {len(merged):,} rows (preds joined to v2 by date+ticker)")

        # Compare on rows where both models predict
        both_high = merged[(merged["bayes_mean"] >= 0.25) &
                            (merged["prob_continuer"] >= 0.30)]
        bayes_only = merged[(merged["bayes_mean"] >= 0.25) &
                              (merged["prob_continuer"] < 0.30)]
        v2_only = merged[(merged["bayes_mean"] < 0.25) &
                          (merged["prob_continuer"] >= 0.30)]
        neither = merged[(merged["bayes_mean"] < 0.25) &
                          (merged["prob_continuer"] < 0.30)]

        for label, g in [("BOTH agree (long)", both_high),
                          ("Bayes-only (long)", bayes_only),
                          ("v2-only (long)", v2_only),
                          ("NEITHER (skip)", neither)]:
            if len(g) == 0: continue
            avg = g["y_reg"].clip(-0.5, 1.0).mean() * 100
            win = (g["y_reg"] > 0).mean() * 100
            print(f"  {label:<25} n={len(g):>5,}  avg={avg:>+6.2f}%  win={win:>5.1f}%")

    section("STEP 6 - Persist")
    out = DERIVED / "bayesian_baseline_walkforward.parquet"
    preds.to_parquet(out, compression="zstd")
    print(f"  Wrote {out}")

    summary = {
        "n_rows": len(preds),
        "n_folds": int(preds["fold"].nunique()),
        "thresholds": {
            f"bayes_mean_ge_{thr:.2f}": {
                "n": int((preds["bayes_mean"] >= thr).sum()),
                "avg_t5_pct": float(preds.loc[preds["bayes_mean"] >= thr, "y_reg"]
                                      .clip(-0.5, 1.0).mean() * 100)
                              if (preds["bayes_mean"] >= thr).sum() > 0 else None,
            }
            for thr in [0.20, 0.25, 0.30, 0.40, 0.50]
        },
    }
    summary_path = MODELS / "bayesian_baseline_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"  Wrote {summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
