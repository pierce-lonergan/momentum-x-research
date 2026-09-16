"""Rigor verifier: Deflated Sharpe + Probabilistic Sharpe + PBO + leakage probe.

Per the May 2026 Compass research synthesis (Bailey & López de Prado 2014,
Probabilistic Sharpe Ratio 2012/13, PBO 2013/14):

A trading Verifier worthy of the name must enforce:
  1. Walk-forward integrity (already have)
  2. Look-ahead / target-leakage detection (artificial-lag probe)
  3. Conformal calibration (already have)
  4. Multiple-testing correction (DSR + PSR)
  5. Drift detection (separate file, future)
  6. Reproducibility (hash + seed)

This module ships #2 and #4. Used by:
  - ml_continuer_v3 candidate-feature evaluation (this session)
  - lottery/fader-short candidate-strategy evaluation (future)

KEY PRINCIPLE (DeepSeekMath-V2): the Verifier must scale faster than the
Generator. With 20K rows we are statistically capped — DSR + PSR are how
we avoid Sharpe-inflation when running many candidates.
"""
from __future__ import annotations
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd


# ── Probabilistic Sharpe Ratio (Bailey & López de Prado 2012/13) ──

def probabilistic_sharpe(
    sharpe_observed: float,
    n_obs: int,
    sharpe_benchmark: float = 0.0,
    skew: float = 0.0,
    kurt: float = 3.0,
) -> float:
    """P(true SR > benchmark | observed SR over n_obs samples).

    Closed-form per Bailey & López de Prado (2012). Returns probability in [0,1].
    Use kurt=3 for "no excess kurtosis" (Gaussian); higher kurt for fat tails.

    Standard error of SR with non-Gaussian moments:
      se(SR) = sqrt[ (1 - skew*SR + (kurt-1)/4 * SR^2) / (n - 1) ]
    """
    if n_obs <= 1:
        return 0.5
    var = max(1.0 - skew * sharpe_observed
              + (kurt - 1.0) / 4.0 * sharpe_observed**2, 1e-9)
    se = math.sqrt(var / (n_obs - 1))
    z = (sharpe_observed - sharpe_benchmark) / max(se, 1e-9)
    # Phi(z) — standard normal CDF
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def sharpe_ratio(returns: np.ndarray, freq: int = 252) -> float:
    """Annualized Sharpe ratio from returns array."""
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    if len(r) < 2:
        return 0.0
    mu = r.mean()
    sigma = r.std(ddof=1)
    if sigma <= 0:
        return 0.0
    return (mu / sigma) * math.sqrt(freq)


def per_trade_sharpe(per_trade_returns: np.ndarray, trades_per_year: float = 252) -> float:
    """For per-trade returns (not periodic), annualize via expected trade count."""
    r = np.asarray(per_trade_returns, dtype=float)
    r = r[~np.isnan(r)]
    if len(r) < 2:
        return 0.0
    mu = r.mean()
    sigma = r.std(ddof=1)
    if sigma <= 0:
        return 0.0
    return (mu / sigma) * math.sqrt(trades_per_year)


# ── Deflated Sharpe Ratio (Bailey & López de Prado 2014) ──

def expected_max_sharpe_under_null(n_trials: int) -> float:
    """E[max(SR_1, ..., SR_N)] under H0 (all SRs ~ N(0, 1/sqrt(T-1))).

    Approximation per Bailey & López de Prado 2014:
      E[max(SR_i)] ≈ (1 - gamma) * Phi^{-1}(1 - 1/N) + gamma * Phi^{-1}(1 - 1/(N*e))
    where gamma is the Euler-Mascheroni constant ~ 0.5772.

    Returns expected max in units of SR (not annualized; multiply by sqrt(freq) externally).
    """
    if n_trials <= 1:
        return 0.0
    from scipy.stats import norm  # local import (only here)
    gamma = 0.5772156649015329
    # Inverse normal CDF
    a = norm.ppf(1.0 - 1.0 / n_trials)
    b = norm.ppf(1.0 - 1.0 / (n_trials * math.e))
    return (1 - gamma) * a + gamma * b


def deflated_sharpe(
    sharpe_observed: float,
    n_obs: int,
    n_trials: int,
    skew: float = 0.0,
    kurt: float = 3.0,
) -> float:
    """P(true SR > 0 | observed SR survived best-of-N selection).

    Combines PSR with multiple-testing correction. Strategy passes DSR
    typically when DSR > 0.95 (i.e., 95% confident the surviving strategy
    has positive true SR after accounting for selection bias).
    """
    if n_trials <= 1:
        return probabilistic_sharpe(sharpe_observed, n_obs, 0.0, skew, kurt)
    expected_max = expected_max_sharpe_under_null(n_trials)
    # Use expected_max as the benchmark in PSR
    return probabilistic_sharpe(sharpe_observed, n_obs, expected_max, skew, kurt)


# ── Probability of Backtest Overfitting (PBO) — Bailey, Borwein, López de Prado, Zhu ──

def pbo(per_strategy_returns: np.ndarray, n_partitions: int = 16) -> float:
    """Combinatorially symmetric cross-validation PBO.

    Args:
        per_strategy_returns: 2D array (T x N) where T = time periods,
            N = number of strategies tried.
        n_partitions: even number of partitions (S in BBLP). 16 typical.

    Returns:
        Probability that the best-in-sample strategy has a sub-median
        out-of-sample Sharpe. Lower = less overfitting. PBO < 0.5 is
        the threshold (chance level).

    Implementation per Bailey, Borwein, López de Prado, Zhu (2013/14).
    """
    R = np.asarray(per_strategy_returns, dtype=float)
    if R.ndim != 2:
        raise ValueError("per_strategy_returns must be 2D (T x N)")
    T, N = R.shape
    if T < n_partitions * 2:
        return float("nan")  # not enough data
    # Force even partitions
    n_partitions = (n_partitions // 2) * 2
    # Drop trailing rows so T divides cleanly
    chunk = T // n_partitions
    T_used = chunk * n_partitions
    R_used = R[:T_used]
    # Reshape to (n_partitions, chunk, N) for combinatorial selection
    chunks = R_used.reshape(n_partitions, chunk, N)
    from itertools import combinations
    half = n_partitions // 2
    n_overfits = 0
    n_total = 0
    # For each split into IS/OOS halves
    for IS_idx in combinations(range(n_partitions), half):
        IS_set = set(IS_idx)
        OOS_set = set(range(n_partitions)) - IS_set
        IS_returns = np.concatenate([chunks[i] for i in IS_set], axis=0)  # (T/2, N)
        OOS_returns = np.concatenate([chunks[i] for i in OOS_set], axis=0)
        # Best-in-sample strategy
        IS_sr = (IS_returns.mean(axis=0)
                 / np.maximum(IS_returns.std(axis=0, ddof=1), 1e-9))
        best_IS = int(np.argmax(IS_sr))
        # Its OOS rank (lower rank = better)
        OOS_sr = (OOS_returns.mean(axis=0)
                  / np.maximum(OOS_returns.std(axis=0, ddof=1), 1e-9))
        # Overfit if best IS strategy has below-median OOS SR
        if OOS_sr[best_IS] < np.median(OOS_sr):
            n_overfits += 1
        n_total += 1
    return n_overfits / max(n_total, 1)


# ── Leakage probe (artificial-lag test) ──

@dataclass
class LeakageProbeResult:
    feature_name: str
    base_correlation: float
    lagged_correlation: float
    expected_decay: float  # what correlation SHOULD be after lag (if no leak)
    suspicious: bool
    suspicion_score: float  # how much closer to base than expected
    note: str


def leakage_probe(
    feature: pd.Series,
    target: pd.Series,
    lag_periods: int = 1,
    expected_decay_factor: float = 0.7,
) -> LeakageProbeResult:
    """Artificial-lag leakage probe.

    A feature with no future leakage should show ROUGHLY proportional
    correlation decay when shifted forward (lagged). E.g., if the feature
    correlates 0.10 with target at lag 0, lagging it 1 day should produce
    ~0.07 (decay factor of 0.7 for 1-day momentum).

    A feature THAT LEAKS (uses future info) won't decay as expected —
    even when lagged it stays correlated with target because it already
    encoded future state.

    Args:
        feature: predictor, indexed sequentially (per-row in our case)
        target: outcome (e.g., ret_t5)
        lag_periods: how many rows to shift forward
        expected_decay_factor: what fraction of base correlation should
            remain after lag in absence of leakage (0.7 = expect 30% decay)

    Returns:
        LeakageProbeResult with `suspicious=True` if lagged correlation
        is too close to base (indicating leak).
    """
    f = pd.Series(feature).reset_index(drop=True)
    t = pd.Series(target).reset_index(drop=True)
    # Drop NaN-aligned
    mask = ~(f.isna() | t.isna())
    f = f[mask].reset_index(drop=True)
    t = t[mask].reset_index(drop=True)
    if len(f) < lag_periods + 10:
        return LeakageProbeResult(
            feature_name=str(f.name or "?"),
            base_correlation=float("nan"),
            lagged_correlation=float("nan"),
            expected_decay=float("nan"),
            suspicious=False, suspicion_score=0.0,
            note="insufficient_data",
        )
    base_corr = f.corr(t)
    # Shift feature forward by lag_periods (so it now uses LATER data than target)
    f_lagged = f.shift(lag_periods)
    mask2 = ~(f_lagged.isna() | t.isna())
    lagged_corr = f_lagged[mask2].corr(t[mask2])
    if pd.isna(base_corr) or pd.isna(lagged_corr):
        return LeakageProbeResult(
            feature_name=str(f.name or "?"),
            base_correlation=float(base_corr) if not pd.isna(base_corr) else 0,
            lagged_correlation=float(lagged_corr) if not pd.isna(lagged_corr) else 0,
            expected_decay=float("nan"),
            suspicious=False, suspicion_score=0.0,
            note="nan_correlation",
        )
    expected = base_corr * expected_decay_factor
    # Suspicion: |lagged_corr| / |base_corr| > expected_decay_factor + tolerance
    actual_decay_factor = (abs(lagged_corr) / max(abs(base_corr), 1e-6))
    # If actual decay much less than expected (correlation barely dropped), suspicious
    suspicion = max(0.0, actual_decay_factor - expected_decay_factor)
    is_suspicious = suspicion > 0.20  # tolerance 20pp above expected
    return LeakageProbeResult(
        feature_name=str(f.name or "?"),
        base_correlation=float(base_corr),
        lagged_correlation=float(lagged_corr),
        expected_decay=float(expected),
        suspicious=bool(is_suspicious),
        suspicion_score=float(suspicion),
        note=("OK" if not is_suspicious else f"lagged_too_close (actual_decay_factor={actual_decay_factor:.2f})"),
    )


# ── End-to-end verifier ──

@dataclass
class FeatureVerificationResult:
    feature_name: str
    passes: bool
    sharpe: float
    psr: float
    dsr: float
    leakage_probe: LeakageProbeResult
    n_obs: int
    note: str


def verify_feature(
    feature_values: pd.Series,
    forward_returns: pd.Series,  # ret_t5
    feature_name: str,
    n_trials_in_search: int = 1,
    psr_threshold: float = 0.95,
    dsr_threshold: float = 0.95,
) -> FeatureVerificationResult:
    """End-to-end Tier-1 verification of a candidate feature.

    Checks:
      (a) leakage probe (artificial-lag)
      (b) PSR (P[true SR > 0] given observed)
      (c) DSR (PSR with multi-test correction for n_trials)
      (d) sample size sanity

    Returns FeatureVerificationResult.passes=True iff all pass.
    """
    probe = leakage_probe(feature_values, forward_returns)
    if probe.suspicious:
        return FeatureVerificationResult(
            feature_name=feature_name, passes=False,
            sharpe=0.0, psr=0.0, dsr=0.0, leakage_probe=probe,
            n_obs=len(forward_returns), note="leakage_probe_failed",
        )

    # Build a per-trade "strategy return" by selecting top-quintile of feature
    # and measuring the average forward return
    df = pd.DataFrame({"feat": feature_values, "ret": forward_returns}).dropna()
    if len(df) < 50:
        return FeatureVerificationResult(
            feature_name=feature_name, passes=False,
            sharpe=0.0, psr=0.0, dsr=0.0, leakage_probe=probe,
            n_obs=len(df), note="too_few_obs",
        )
    # Top quintile by feature value (or bottom quintile if anti-correlated)
    base_corr = probe.base_correlation
    if abs(base_corr) < 0.005:
        return FeatureVerificationResult(
            feature_name=feature_name, passes=False,
            sharpe=0.0, psr=0.0, dsr=0.0, leakage_probe=probe,
            n_obs=len(df), note=f"base_correlation_too_weak ({base_corr:.4f})",
        )
    # Pick direction so we long when expected positive
    if base_corr > 0:
        threshold = df["feat"].quantile(0.80)
        selected = df["ret"][df["feat"] >= threshold]
    else:
        threshold = df["feat"].quantile(0.20)
        selected = df["ret"][df["feat"] <= threshold]
    if len(selected) < 30:
        return FeatureVerificationResult(
            feature_name=feature_name, passes=False,
            sharpe=0.0, psr=0.0, dsr=0.0, leakage_probe=probe,
            n_obs=len(selected), note="too_few_selected",
        )

    sr = per_trade_sharpe(selected.values, trades_per_year=252/5)  # T+5 horizon
    psr = probabilistic_sharpe(sr / math.sqrt(252/5), len(selected))
    dsr = deflated_sharpe(sr / math.sqrt(252/5), len(selected), n_trials_in_search)
    passes = (psr >= psr_threshold) and (dsr >= dsr_threshold)
    return FeatureVerificationResult(
        feature_name=feature_name, passes=passes,
        sharpe=float(sr), psr=float(psr), dsr=float(dsr),
        leakage_probe=probe, n_obs=len(selected),
        note=("OK" if passes else
              f"psr={psr:.3f}<{psr_threshold} or dsr={dsr:.3f}<{dsr_threshold}"),
    )


# ── CLI smoke test ──

def _smoke_test():
    print("=== rigor_verifier smoke test ===\n")

    # 1. PSR
    print("PSR for SR=1.0, n=100, benchmark=0.5:")
    print(f"  {probabilistic_sharpe(1.0, 100, 0.5):.4f}  (should be ~0.99)")

    # 2. DSR
    print("\nDSR for SR=2.0, n=100, n_trials=100:")
    em = expected_max_sharpe_under_null(100)
    print(f"  E[max] under null = {em:.4f}")
    print(f"  DSR = {deflated_sharpe(2.0, 100, 100):.4f}")

    # 3. Leakage probe (fake feature)
    np.random.seed(42)
    n = 200
    target = np.random.randn(n)
    # Clean feature: weak signal
    clean_feat = target * 0.1 + np.random.randn(n) * 0.95
    print("\nLeakage probe on CLEAN feature (slight signal):")
    res = leakage_probe(pd.Series(clean_feat, name="clean"), pd.Series(target))
    print(f"  base_corr={res.base_correlation:.4f}  lagged_corr={res.lagged_correlation:.4f}")
    print(f"  suspicious={res.suspicious}  note={res.note}")

    # Leaking feature: encodes future
    leak_feat = target + np.random.randn(n) * 0.1
    print("\nLeakage probe on LEAKING feature:")
    res = leakage_probe(pd.Series(leak_feat, name="leak"), pd.Series(target))
    print(f"  base_corr={res.base_correlation:.4f}  lagged_corr={res.lagged_correlation:.4f}")
    print(f"  suspicious={res.suspicious}  note={res.note}")

    # 4. End-to-end verify_feature
    print("\nverify_feature on CLEAN:")
    r = verify_feature(pd.Series(clean_feat, name="clean"), pd.Series(target), "clean", n_trials_in_search=1)
    print(f"  passes={r.passes}  sharpe={r.sharpe:.4f}  psr={r.psr:.4f}  dsr={r.dsr:.4f}  note={r.note}")

    print("\nverify_feature on CLEAN with n_trials=50 (multiple-testing correction):")
    r = verify_feature(pd.Series(clean_feat, name="clean"), pd.Series(target), "clean", n_trials_in_search=50)
    print(f"  passes={r.passes}  sharpe={r.sharpe:.4f}  psr={r.psr:.4f}  dsr={r.dsr:.4f}  note={r.note}")


if __name__ == "__main__":
    _smoke_test()
