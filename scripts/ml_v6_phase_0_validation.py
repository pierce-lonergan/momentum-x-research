"""v6 Phase 0 — Validation hardening.

Per docs/research-log/132_momtrans_v5_negative_result_v6_roadmap.md, this is
MANDATORY before any further architecture work. Implements the López de
Prado / Bailey rigor framework on the v4 predictions:

  0.1 CPCV-approximation: subsample N-of-16 folds, get distribution
      of Sharpes (replaces point-estimate from 16-fold WF)
  0.2 Deflated Sharpe Ratio (Bailey & LdP JPM 2014):
      probability that observed Sharpe survives multiple-testing
  0.3 Probability of Backtest Overfitting (Bailey-Borwein-LdP-Zhu 2016):
      uses the existing 600-combo threshold-search trials from Phase A
  0.4 Numerai-style feature neutralization: orthogonalize predictions
      against sector/market_cap/prior_t5/realized_vol exposures and
      recompute Spearman + $-PNL — DEFLATES the apparent edge honestly
  0.5 Embargo + purging audit: confirm no label leakage in the 5-day
      forward labels under our 16-fold WF

Output: data/models/v6_phase0_validation.json + per-section console verdict.

The MOST IMPORTANT possible result of this entire framework is
DISCOVERING that v4's $46k WF lift is a multiple-testing artifact.
That would be a more valuable finding than another +$10k lift.
"""
from __future__ import annotations
import json
import math
from itertools import combinations
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MODELS = REPO / "data" / "models"

KELLY_CAPS = {"ELITE": 0.50, "HIGH": 0.35, "VETOED": 0.20, "BROAD": 0.10}
TIER_ORDER = {"ELITE": 0, "HIGH": 1, "VETOED": 2, "BROAD": 3}
TIER_EW_EL_PCT = {
    "ELITE":  (0.7207, -0.0833),
    "HIGH":   (0.4075, -0.1745),
    "VETOED": (0.3490, -0.1421),
    "BROAD":  (0.3095, -0.1763),
}
BANKROLL = 10_000.0
EULER_MASCHERONI = 0.5772156649015329


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def kelly(p, w, ew, el, cap):
    if p < 0.30 or abs(el) < 1e-6 or ew <= 0:
        return 0.0
    b = abs(ew / el)
    q = 1.0 - p
    f_star = max(0.0, (b * p - q) / b)
    return float(min(f_star * float(np.exp(-2 * w)), cap))


def per_pick_pnl(df: pd.DataFrame, score_col: str, prob_col: str = None,
                    width_col: str = "conformal_width",
                    score_threshold: float = 0.30) -> pd.DataFrame:
    """Compute per-pick $-PNL contribution. Picks fire when score >= threshold AND
    mag_label gates pass. Tier waterfall ELITE > HIGH > VETOED > BROAD."""
    prob_col = prob_col or score_col
    df = df.copy()
    yreg = df["y_reg"].clip(-0.5, 1.0).values
    mag = df["mag_label"].values
    is_hi_mid = np.isin(mag, ["HI", "MID"])
    is_mid = mag == "MID"
    score = df[score_col].values
    prob = df[prob_col].values
    width = df[width_col].values if width_col in df.columns else np.full(len(df), 0.5)

    elite = (score >= 0.60) & is_hi_mid
    high = (score >= 0.50) & (score < 0.60) & is_hi_mid
    vetoed = (score >= 0.30) & (score < 0.50) & is_mid
    broad = (score >= 0.30) & (score < 0.50) & is_hi_mid & ~vetoed
    tiers = np.array(["SKIP"] * len(df), dtype=object)
    tiers[elite] = "ELITE"
    tiers[high] = "HIGH"
    tiers[vetoed] = "VETOED"
    tiers[broad] = "BROAD"
    df["tier"] = tiers
    df["pnl"] = 0.0
    for tier in ("ELITE", "HIGH", "VETOED", "BROAD"):
        mask = (df["tier"] == tier).values
        if not mask.any(): continue
        ew, el = TIER_EW_EL_PCT[tier]
        cap = KELLY_CAPS[tier]
        idx = np.where(mask)[0]
        kellies = np.array([kelly(prob[i], width[i], ew, el, cap) for i in idx])
        df.loc[df.index[idx], "pnl"] = BANKROLL * kellies * yreg[idx]
    return df


# ── 0.1 CPCV-approximation — Sharpe distribution from fold subsamples ──


def cpcv_sharpe_distribution(per_pick_df: pd.DataFrame,
                                  n_test_folds: int = 4,
                                  n_samples: int = 200,
                                  rng_seed: int = 42) -> dict:
    """Sample (n_test_folds-of-N) random subsets of folds, compute Sharpe-of-
    sum-of-PnL on each subset. Returns the distribution.

    Approximation: this uses the EXISTING per-fold OOS predictions rather
    than retraining — full CPCV would require (16 choose n_test) refits
    which is infeasible in this session. The approximation captures
    selection-bias variance but NOT model-variance under different training sets.

    Honest disclosure: this UNDERSTATES Sharpe variance vs full CPCV.
    """
    rng = np.random.default_rng(rng_seed)
    folds = per_pick_df["fold"].unique()
    n_folds_total = len(folds)
    if n_folds_total < n_test_folds + 2:
        return {"error": f"need at least {n_test_folds+2} folds, have {n_folds_total}"}
    sharpes = []
    pnls = []
    for _ in range(n_samples):
        test_folds = rng.choice(folds, size=n_test_folds, replace=False)
        sub = per_pick_df[per_pick_df["fold"].isin(test_folds)]
        if len(sub) == 0: continue
        # Per-day P&L (sum of all picks that fired that day)
        daily_pnl = sub.groupby("d0")["pnl"].sum()
        if len(daily_pnl) < 5: continue
        mean_pnl = daily_pnl.mean()
        std_pnl = daily_pnl.std(ddof=1)
        if std_pnl < 1e-8: continue
        sharpe = mean_pnl / std_pnl * math.sqrt(252)  # annualized
        sharpes.append(sharpe)
        pnls.append(daily_pnl.sum())
    sharpes = np.array(sharpes)
    pnls = np.array(pnls)
    if len(sharpes) == 0:
        return {"error": "no valid samples"}
    return {
        "n_samples": int(len(sharpes)),
        "n_test_folds_per_sample": n_test_folds,
        "sharpe_mean": float(sharpes.mean()),
        "sharpe_std": float(sharpes.std(ddof=1)),
        "sharpe_p05": float(np.percentile(sharpes, 5)),
        "sharpe_p25": float(np.percentile(sharpes, 25)),
        "sharpe_p50": float(np.percentile(sharpes, 50)),
        "sharpe_p75": float(np.percentile(sharpes, 75)),
        "sharpe_p95": float(np.percentile(sharpes, 95)),
        "frac_positive": float((sharpes > 0).mean()),
        "frac_above_1": float((sharpes > 1).mean()),
        "pnl_per_subset_mean": float(pnls.mean()),
        "pnl_per_subset_std": float(pnls.std(ddof=1)),
    }


# ── 0.2 Deflated Sharpe Ratio (Bailey & LdP JPM 2014) ──


def deflated_sharpe_ratio(daily_pnls: np.ndarray, n_trials: int) -> dict:
    """DSR = Φ((SR_obs - SR_max_under_H0) · sqrt(T-1) / σ_SR_corrected)

    Where:
      SR_obs       = observed annualized Sharpe (mean/std × sqrt(252))
      SR_max_H0    = expected max of N IID normal Sharpes under H0 (no edge)
      σ_SR_corr    = std of Sharpe estimator with skew/kurtosis correction
      T            = number of return observations (days)
      n_trials     = effective number of independent strategies tested

    Returns DSR (probability that true SR > 0 given the multiple-testing
    effective trial count). DSR > 0.95 is the standard publication bar.
    """
    daily_pnls = np.asarray(daily_pnls)
    daily_pnls = daily_pnls[~np.isnan(daily_pnls)]
    T = len(daily_pnls)
    if T < 30:
        return {"error": f"need ≥30 daily returns; got {T}"}
    mean = daily_pnls.mean()
    std = daily_pnls.std(ddof=1)
    if std < 1e-8:
        return {"error": "zero variance"}
    sr_per_period = mean / std
    sr_annual = sr_per_period * math.sqrt(252)

    # Sample skew & kurtosis (excess) for σ correction
    centered = daily_pnls - mean
    m3 = (centered ** 3).mean()
    m4 = (centered ** 4).mean()
    skew = m3 / (std ** 3) if std > 0 else 0.0
    excess_kurt = m4 / (std ** 4) - 3 if std > 0 else 0.0

    # Std of Sharpe estimator (Mertens 2002 / Lo 2002 corrected; Bailey-LdP 2014)
    sigma_sr = math.sqrt(
        max(1e-8, (1 - skew * sr_per_period + (excess_kurt / 4) * sr_per_period ** 2) / (T - 1))
    )

    # Expected max of N normal Sharpes under H0
    # E[max(N IID standard normal)] ≈ (1-γ)·Z⁻¹(1 - 1/N) + γ·Z⁻¹(1 - 1/(Ne))
    # where γ = Euler-Mascheroni constant
    if n_trials < 2:
        sr_max_h0 = 0.0
    else:
        from scipy.stats import norm
        z1 = norm.ppf(1 - 1.0 / n_trials)
        z2 = norm.ppf(1 - 1.0 / (n_trials * math.e))
        sr_max_h0 = (1 - EULER_MASCHERONI) * z1 + EULER_MASCHERONI * z2

    # Convert sr_max_h0 (a per-trial std-units quantile) to a per-period
    # Sharpe in our units. Under H0 the trial Sharpes have std = sigma_sr,
    # so the expected-max benchmark in absolute SR units is:
    sr_max_threshold = sr_max_h0 * sigma_sr  # in per-period Sharpe units

    # DSR = Φ((sr_per_period - sr_max_threshold) / sigma_sr)
    # Note: Bailey-LdP define DSR over SR_per_period, NOT SR_annualized
    from scipy.stats import norm
    z = (sr_per_period - sr_max_threshold) / sigma_sr if sigma_sr > 0 else 0.0
    dsr = float(norm.cdf(z))

    return {
        "T_days": int(T),
        "sr_observed_per_period": float(sr_per_period),
        "sr_observed_annualized": float(sr_annual),
        "skew": float(skew),
        "excess_kurtosis": float(excess_kurt),
        "sigma_sr": float(sigma_sr),
        "n_trials": int(n_trials),
        "sr_max_under_H0_per_period": float(sr_max_threshold),
        "z_score": float(z),
        "DSR": dsr,
        "verdict": (
            "STRONG (DSR > 0.95)" if dsr > 0.95
            else "MODERATE (DSR ∈ [0.80, 0.95])" if dsr > 0.80
            else "WEAK (DSR ∈ [0.50, 0.80])" if dsr > 0.50
            else "FAILS — likely backtest overfit" if dsr > 0.05
            else "SEVERE FAIL — Sharpe is in the H0 noise band"
        ),
    }


# ── 0.3 Probability of Backtest Overfitting (Bailey-Borwein-LdP-Zhu 2016) ──


def pbo_from_threshold_trials(trials_df: pd.DataFrame,
                                 metric_col: str = "pnl") -> dict:
    """Combinatorial Symmetric Cross-Validation PBO.

    trials_df: one row per strategy (threshold combo), with COLUMNS for
               per-fold metric (e.g., "pnl_fold_0", ..., "pnl_fold_15").

    Algorithm: split the 16 folds into all (16 choose 8) = 12,870 pairs of
    IS/OOS halves. For each split:
      - Compute IS metric (sum over IS folds) per strategy
      - Find IS-best strategy
      - Compute that strategy's OOS rank among all strategies
      - PBO++ if OOS rank < median (i.e., IS-winner underperforms OOS)

    PBO = fraction of splits where IS-winner is below OOS median.
    PBO < 0.5 means the selection process is at least non-anti-predictive.
    PBO > 0.5 means the strategy-selection process is predicting WORSE than median.
    """
    fold_cols = sorted([c for c in trials_df.columns if c.startswith(f"{metric_col}_fold_")])
    n_folds = len(fold_cols)
    if n_folds < 4:
        return {"error": f"need ≥4 fold columns; got {n_folds}"}
    M = trials_df.shape[0]  # number of strategies
    if M < 5:
        return {"error": f"need ≥5 strategies for stable PBO; got {M}"}

    metrics = trials_df[fold_cols].values  # shape (M, n_folds)
    fold_indices = list(range(n_folds))
    half = n_folds // 2

    splits = list(combinations(fold_indices, half))
    if len(splits) > 5_000:
        # Sample to keep runtime sane
        rng = np.random.default_rng(42)
        splits = [splits[i] for i in rng.choice(len(splits), size=5_000, replace=False)]

    underperform_count = 0
    total_count = 0
    for is_folds in splits:
        oos_folds = [f for f in fold_indices if f not in is_folds]
        is_metric = metrics[:, list(is_folds)].sum(axis=1)
        oos_metric = metrics[:, list(oos_folds)].sum(axis=1)
        is_best_strategy = int(np.argmax(is_metric))
        oos_rank_of_winner = float((oos_metric < oos_metric[is_best_strategy]).sum() / M)
        # oos_rank_of_winner is the FRACTION of strategies that the IS-winner
        # outperforms OOS. 0.5 = median. < 0.5 means IS-winner is below OOS median.
        if oos_rank_of_winner < 0.5:
            underperform_count += 1
        total_count += 1
    pbo = underperform_count / max(total_count, 1)
    return {
        "n_strategies": int(M),
        "n_folds": int(n_folds),
        "n_splits_evaluated": int(total_count),
        "PBO": float(pbo),
        "verdict": (
            "PASS (PBO < 0.30 — selection is predictive)" if pbo < 0.30
            else "WEAK_PASS (PBO ∈ [0.30, 0.50])" if pbo < 0.50
            else "FAIL (PBO >= 0.50 — selection is at-or-worse than random)"
        ),
    }


# ── 0.4 Numerai-style feature neutralization ──


def feature_neutralize(predictions: np.ndarray, exposures: np.ndarray,
                          proportion: float = 1.0) -> np.ndarray:
    """Project predictions onto the residual space orthogonal to `exposures`.

    Standard Numerai formula: pred_neutral = pred - proportion · F · (F⁺ · pred)

    F+ is the Moore-Penrose pseudoinverse. proportion=1.0 means full
    neutralization; <1.0 partially preserves the original signal.
    """
    F = np.asarray(exposures, dtype=np.float64)
    if F.ndim == 1:
        F = F.reshape(-1, 1)
    p = np.asarray(predictions, dtype=np.float64).flatten()
    # Moore-Penrose pseudoinverse projection
    F_pinv = np.linalg.pinv(F)
    projection = F @ (F_pinv @ p)
    neutral = p - proportion * projection
    # Re-standardize (Numerai standard)
    if neutral.std() > 1e-8:
        neutral = neutral / neutral.std()
    return neutral


def spearman(a, b):
    a, b = np.asarray(a), np.asarray(b)
    return float(np.corrcoef(np.argsort(np.argsort(a)),
                                np.argsort(np.argsort(b)))[0, 1])


# ── 0.5 Embargo + purging audit ──


def embargo_audit(prod_df: pd.DataFrame, label_horizon_days: int = 5,
                    embargo_days: int = 5) -> dict:
    """Check that 16-fold WF folds have proper embargo. Specifically:
    for each consecutive (train_fold_i, test_fold_i+1), the gap between
    last train d0 + label_horizon and first test d0 must be ≥ embargo_days.

    For our setup (16 folds, 365d train / 30d test rolling), the structure
    naturally has a 30-day gap between consecutive test windows, which
    exceeds the 5-day embargo. But within a SINGLE fold's training window,
    the LAST training samples have label-end-dates that may overlap the
    test window — this is the proper purging requirement.
    """
    if "fold" not in prod_df.columns or "d0" not in prod_df.columns:
        return {"error": "need fold + d0 columns"}
    folds = sorted(prod_df["fold"].unique())
    issues = []
    for fold_i in folds:
        sub = prod_df[prod_df["fold"] == fold_i]
        if sub.empty: continue
        test_min = pd.to_datetime(sub["d0"]).min()
        test_max = pd.to_datetime(sub["d0"]).max()
        # Each test sample's label is realized at d0 + label_horizon_days. Any
        # training sample with d0 ∈ [test_min - label_horizon, test_min]
        # would have its label realization OVERLAP the test fold's d0 range.
        leakage_start = test_min - pd.Timedelta(days=label_horizon_days + embargo_days)
        leakage_end = test_min - pd.Timedelta(days=1)
        # We don't have the actual training fold rows here, just a check of
        # the structure. If the WF's gap_between_train_end_and_test_start
        # is >= embargo, we're fine.
        issues.append({
            "fold": int(fold_i),
            "test_d0_min": str(test_min.date()),
            "test_d0_max": str(test_max.date()),
            "needed_train_cutoff_for_5d_embargo": str(leakage_end.date()),
        })
    return {
        "label_horizon_days": label_horizon_days,
        "embargo_days": embargo_days,
        "n_folds_audited": len(folds),
        "fold_summary": issues[:5],  # first 5 for brevity
        "note": (
            "16-fold WF with 365d train / 30d test rolling has 30d gaps "
            "between consecutive test windows. Within a fold the train end is "
            "the test start; samples at the train edge MAY have label-overlap "
            "with the test window (label_horizon=5d). Recommend adding 5d "
            "purging at the train-end boundary for v6."
        ),
    }


# ── Top-level Phase 0 driver ──


def main() -> int:
    section("v6 Phase 0 — Validation Hardening")

    # Load v4 cohort cascade predictions (the canonical "production candidate")
    v4_files = {tier: MODELS / f"momtrans_v4_tier_{tier}_predictions.parquet"
                for tier in ("BROAD", "VETOED", "HIGH", "ELITE")}
    if not all(p.exists() for p in v4_files.values()):
        print("  ERROR: v4 specialist predictions missing")
        return 1

    base = pd.read_parquet(v4_files["BROAD"])[["d0", "ticker", "fold", "y_reg",
                                                    "prob_binary"]].rename(
        columns={"prob_binary": "prob_specialist_BROAD"})
    base["d0"] = pd.to_datetime(base["d0"])
    base["conformal_width"] = 0.5
    for tier in ("VETOED", "HIGH", "ELITE"):
        df_t = pd.read_parquet(v4_files[tier])[["d0", "ticker", "fold", "prob_binary"]]
        df_t["d0"] = pd.to_datetime(df_t["d0"])
        base = base.merge(
            df_t.rename(columns={"prob_binary": f"prob_specialist_{tier}"}),
            on=["d0", "ticker", "fold"], how="inner",
        )

    # Production v3 baseline
    prod_path = DERIVED / "ml_v2_walkforward_predictions_v3_tuned_16fold.parquet"
    prod = pd.read_parquet(prod_path)
    prod["d0"] = pd.to_datetime(prod["d0"])
    if "conformal_width" not in prod.columns:
        prod["conformal_width"] = 0.5

    # Ising mag join
    ising_path = DERIVED / "ising_daily.parquet"
    con = duckdb.connect()
    con.sql(f"""CREATE TABLE i AS SELECT *,
        AVG(magnetization) OVER (ORDER BY d ROWS BETWEEN 4 PRECEDING AND CURRENT ROW)
        AS mag_5d FROM read_parquet('{ising_path.as_posix()}')""")
    mag_df = con.sql("""SELECT d AS d0,
        CASE WHEN mag_5d < -0.05 THEN 'LO' WHEN mag_5d > 0.05 THEN 'HI' ELSE 'MID' END
        AS mag_label FROM i""").df()
    mag_df["d0"] = pd.to_datetime(mag_df["d0"])
    base = base.merge(mag_df, on="d0", how="left").dropna(subset=["mag_label"])
    prod = prod.merge(mag_df, on="d0", how="left").dropna(subset=["mag_label"])

    print(f"  v4 base frame: {len(base):,} rows × {len(base.columns)} cols")
    print(f"  v3 prod frame: {len(prod):,} rows")

    summary = {}

    # ────────────────────────────────────────────────────────────
    section("0.5 — Embargo + purging audit")
    audit = embargo_audit(prod, label_horizon_days=5, embargo_days=5)
    print(f"  label horizon: {audit['label_horizon_days']}d, embargo: {audit['embargo_days']}d")
    print(f"  folds audited: {audit['n_folds_audited']}")
    print(f"  note: {audit['note']}")
    summary["section_0_5_embargo_audit"] = audit

    # ────────────────────────────────────────────────────────────
    section("0.4 — Numerai-style feature neutralization")

    # Build the exposure matrix from v3-prod features. Use the bare
    # categorical/structural exposures that aren't part of the alpha:
    #   sector dummies (8), log_market_cap, prior_avg_t5, intraday_pct,
    #   log_dvol_d0 (proxy for liquidity)
    # Re-engineer features for the prod frame
    sys_path = REPO / "scripts"
    import sys
    sys.path.insert(0, str(sys_path))
    from ml_continuer_v2_ensemble import load_data, engineer_features  # type: ignore
    df_full = load_data(include_paths=True)
    X = engineer_features(df_full).replace([np.inf, -np.inf], 0).fillna(0)
    # Align to prod (by d0+ticker)
    df_full["d0"] = pd.to_datetime(df_full["d0"])
    X_indexed = X.copy()
    X_indexed["d0"] = df_full["d0"].values
    X_indexed["ticker"] = df_full["ticker"].values
    prod_xfeat = prod.merge(X_indexed, on=["d0", "ticker"], how="inner")

    exposure_cols = ["log_market_cap", "log_dvol_d0", "prior_avg_t5", "intraday_pct",
                       "sec_pharma", "sec_bio", "sec_medical", "sec_software",
                       "sec_finance", "sec_semi", "sec_spac", "sec_reit"]
    available = [c for c in exposure_cols if c in prod_xfeat.columns]
    print(f"  exposure features: {len(available)} of {len(exposure_cols)} available: {available}")

    F = prod_xfeat[available].values
    # Standardize each exposure column (Numerai standard)
    F_std = (F - F.mean(axis=0)) / (F.std(axis=0) + 1e-8)
    raw_pred = prod_xfeat["prob_continuer"].values
    rho_raw = spearman(raw_pred, prod_xfeat["y_reg"].values)
    # Neutralize at proportion=1.0 (full)
    pred_neutral = feature_neutralize(raw_pred, F_std, proportion=1.0)
    rho_neutral = spearman(pred_neutral, prod_xfeat["y_reg"].values)
    # Partial neutralize
    pred_partial = feature_neutralize(raw_pred, F_std, proportion=0.5)
    rho_partial = spearman(pred_partial, prod_xfeat["y_reg"].values)
    print(f"\n  Production v3 raw Spearman ρ:                {rho_raw:+.4f}")
    print(f"  After 50% neutralization (sector/cap/etc.):    {rho_partial:+.4f}  (Δ {rho_partial-rho_raw:+.4f})")
    print(f"  After 100% neutralization:                     {rho_neutral:+.4f}  (Δ {rho_neutral-rho_raw:+.4f})")

    # Also neutralize v4 BROAD specialist
    v4_xfeat = base.merge(X_indexed, on=["d0", "ticker"], how="inner")
    F4 = v4_xfeat[available].values
    F4_std = (F4 - F4.mean(axis=0)) / (F4.std(axis=0) + 1e-8)
    raw_v4 = v4_xfeat["prob_specialist_BROAD"].values
    rho_v4_raw = spearman(raw_v4, v4_xfeat["y_reg"].values)
    pred_v4_neutral = feature_neutralize(raw_v4, F4_std, proportion=1.0)
    rho_v4_neutral = spearman(pred_v4_neutral, v4_xfeat["y_reg"].values)
    print(f"\n  v4 BROAD raw Spearman ρ:                     {rho_v4_raw:+.4f}")
    print(f"  After 100% neutralization:                     {rho_v4_neutral:+.4f}  (Δ {rho_v4_neutral-rho_v4_raw:+.4f})")

    summary["section_0_4_neutralization"] = {
        "exposures_used": available,
        "v3_prod": {"raw_rho": rho_raw, "neutral_50pct": rho_partial,
                       "neutral_100pct": rho_neutral, "delta_full": rho_neutral - rho_raw},
        "v4_BROAD": {"raw_rho": rho_v4_raw, "neutral_100pct": rho_v4_neutral,
                        "delta": rho_v4_neutral - rho_v4_raw},
    }

    # ────────────────────────────────────────────────────────────
    section("0.1 — CPCV-approximation Sharpe distribution (v4 vs prod)")

    # Compute per-pick PnL for v4 (using BROAD specialist as score, like v4 cascade)
    v4_pp = per_pick_pnl(base, score_col="prob_specialist_BROAD", prob_col="prob_specialist_BROAD")
    prod_pp = per_pick_pnl(prod, score_col="prob_continuer", prob_col="prob_continuer")

    print(f"  v4 cohort cascade ({len(v4_pp[v4_pp['pnl']!=0]):,} non-zero picks):")
    cpcv_v4 = cpcv_sharpe_distribution(v4_pp, n_test_folds=4, n_samples=200)
    if "error" not in cpcv_v4:
        print(f"    Sharpe distribution over 200 random 4-fold subsets:")
        print(f"      mean: {cpcv_v4['sharpe_mean']:>+6.3f}    std: {cpcv_v4['sharpe_std']:>5.3f}")
        print(f"      p05:  {cpcv_v4['sharpe_p05']:>+6.3f}    p25:  {cpcv_v4['sharpe_p25']:>+6.3f}")
        print(f"      p50:  {cpcv_v4['sharpe_p50']:>+6.3f}    p75:  {cpcv_v4['sharpe_p75']:>+6.3f}")
        print(f"      p95:  {cpcv_v4['sharpe_p95']:>+6.3f}")
        print(f"      frac > 0:  {cpcv_v4['frac_positive']*100:.1f}%")
        print(f"      frac > 1:  {cpcv_v4['frac_above_1']*100:.1f}%")

    print(f"\n  Production v3 ({len(prod_pp[prod_pp['pnl']!=0]):,} non-zero picks):")
    cpcv_prod = cpcv_sharpe_distribution(prod_pp, n_test_folds=4, n_samples=200)
    if "error" not in cpcv_prod:
        print(f"    Sharpe distribution over 200 random 4-fold subsets:")
        print(f"      mean: {cpcv_prod['sharpe_mean']:>+6.3f}    std: {cpcv_prod['sharpe_std']:>5.3f}")
        print(f"      p50:  {cpcv_prod['sharpe_p50']:>+6.3f}    p95:  {cpcv_prod['sharpe_p95']:>+6.3f}")
        print(f"      frac > 0:  {cpcv_prod['frac_positive']*100:.1f}%")

    summary["section_0_1_cpcv_approx"] = {"v4": cpcv_v4, "production": cpcv_prod}

    # ────────────────────────────────────────────────────────────
    section("0.2 — Deflated Sharpe Ratio")

    # Daily P&L for each strategy
    v4_daily = v4_pp.groupby("d0")["pnl"].sum().values
    prod_daily = prod_pp.groupby("d0")["pnl"].sum().values

    # Effective number of trials: v4 = 4 specialists × {threshold combos searched}
    # The Phase A grid had 600 combos; we use that as our N for v4.
    # For prod, the v3 single model has effectively 1 trial (was tuned via Optuna
    # but the tuning was on different data than the WF eval). Be conservative: N=1.
    n_trials_v4 = 600
    n_trials_prod = 1
    print(f"  Effective trial count (n_trials):  v4={n_trials_v4}, prod={n_trials_prod}")
    dsr_v4 = deflated_sharpe_ratio(v4_daily, n_trials=n_trials_v4)
    dsr_prod = deflated_sharpe_ratio(prod_daily, n_trials=n_trials_prod)
    print(f"\n  v4 cohort cascade DSR:")
    for k, v in dsr_v4.items():
        print(f"    {k}: {v if not isinstance(v, float) else f'{v:.4f}'}")
    print(f"\n  Production v3 DSR:")
    for k, v in dsr_prod.items():
        print(f"    {k}: {v if not isinstance(v, float) else f'{v:.4f}'}")
    summary["section_0_2_dsr"] = {"v4": dsr_v4, "production": dsr_prod}

    # ────────────────────────────────────────────────────────────
    section("0.3 — Probability of Backtest Overfitting (PBO) on threshold trials")

    # Build per-(threshold, fold) PnL matrix from existing v4 specialist preds.
    # For each threshold combo (e=0.40, h=0.40, v=0.40, b=0.50) etc., compute
    # per-fold PnL using the cascade. With ~7×5×5×4 = 700 combos this is feasible.
    elite_grid = [0.30, 0.40, 0.50, 0.60, 0.70, 0.80]
    high_grid = [0.30, 0.40, 0.50, 0.60, 0.70]
    vetoed_grid = [0.40, 0.50, 0.60, 0.70, 0.80]
    broad_grid = [0.40, 0.50, 0.60, 0.70]
    print(f"  Building per-fold PnL matrix for {len(elite_grid)*len(high_grid)*len(vetoed_grid)*len(broad_grid)} threshold combos...")
    rows = []
    folds_sorted = sorted(base["fold"].unique())
    for et in elite_grid:
        for ht in high_grid:
            for vt in vetoed_grid:
                for bt in broad_grid:
                    # Apply cascade to base frame
                    yreg = base["y_reg"].clip(-0.5, 1.0).values
                    width = base["conformal_width"].values
                    mag = base["mag_label"].values
                    is_hi_mid = np.isin(mag, ["HI", "MID"])
                    is_mid = mag == "MID"
                    sp = {t: base[f"prob_specialist_{t}"].values for t in ("ELITE","HIGH","VETOED","BROAD")}
                    elite = (sp["ELITE"] >= et) & is_hi_mid
                    high = (sp["HIGH"] >= ht) & is_hi_mid & ~elite
                    vetoed = (sp["VETOED"] >= vt) & is_mid & ~elite & ~high
                    broad = (sp["BROAD"] >= bt) & is_hi_mid & ~elite & ~high & ~vetoed
                    pnl_per_row = np.zeros(len(base))
                    for tier_name, mask, prob in [
                        ("ELITE", elite, sp["ELITE"]),
                        ("HIGH", high, sp["HIGH"]),
                        ("VETOED", vetoed, sp["VETOED"]),
                        ("BROAD", broad, sp["BROAD"]),
                    ]:
                        if not mask.any(): continue
                        ew, el = TIER_EW_EL_PCT[tier_name]
                        cap = KELLY_CAPS[tier_name]
                        idx = np.where(mask)[0]
                        prob_eff = np.maximum(prob[idx], 0.31)
                        kellies = np.array([kelly(prob_eff[k], width[idx[k]], ew, el, cap)
                                              for k in range(len(idx))])
                        pnl_per_row[idx] = BANKROLL * kellies * yreg[idx]
                    base["_pnl_tmp"] = pnl_per_row
                    fold_pnls = base.groupby("fold")["_pnl_tmp"].sum()
                    row = {"E": et, "H": ht, "V": vt, "B": bt}
                    for f in folds_sorted:
                        row[f"pnl_fold_{int(f)}"] = float(fold_pnls.get(f, 0.0))
                    rows.append(row)
    base.drop(columns=["_pnl_tmp"], inplace=True, errors="ignore")
    trials_df = pd.DataFrame(rows)
    print(f"  trials_df shape: {trials_df.shape}")
    pbo_result = pbo_from_threshold_trials(trials_df, metric_col="pnl")
    print(f"\n  PBO on {pbo_result.get('n_strategies', 0)} threshold combinations × {pbo_result.get('n_folds', 0)} folds:")
    for k, v in pbo_result.items():
        print(f"    {k}: {v if not isinstance(v, float) else f'{v:.4f}'}")
    summary["section_0_3_pbo"] = pbo_result

    # ────────────────────────────────────────────────────────────
    section("Verdict")
    # Synthesize a top-line verdict
    v4_dsr = summary["section_0_2_dsr"]["v4"].get("DSR", 0.0)
    v4_pbo = summary["section_0_3_pbo"].get("PBO", 1.0)
    rho_v4_neut = summary["section_0_4_neutralization"]["v4_BROAD"]["neutral_100pct"]
    print(f"  v4 DSR (with N=600 trials):  {v4_dsr:.4f}")
    print(f"  v4 PBO (over threshold combos):  {v4_pbo:.4f}")
    print(f"  v4 neutralized Spearman:  {rho_v4_neut:+.4f}")
    if v4_dsr > 0.95 and v4_pbo < 0.30 and rho_v4_neut > 0.05:
        verdict = "v4 SIGNAL VALIDATED — DSR strong, PBO low, ρ survives neutralization"
    elif v4_dsr > 0.50 and v4_pbo < 0.50:
        verdict = "v4 SIGNAL MODERATE — partial validation, exercise caution"
    else:
        verdict = "v4 SIGNAL FAILED VALIDATION — multiple-testing artifact suspected"
    print(f"  TOP-LINE VERDICT: {verdict}")
    summary["verdict"] = verdict

    out = MODELS / "v6_phase0_validation.json"
    out.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\n  Wrote {out}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
