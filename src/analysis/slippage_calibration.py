"""Bayesian (η, γ) slippage calibration estimator — PyMC NUTS implementation.

Per the v2.2 §13 spec (compass_artifact_wf-e274cbca-...md §1.6) and items
12-15 of the 2026-04-25 next-actions list. Estimates the two parameters
governing market-impact slippage:

  realized_slippage = η_perm * (q/v_τ) ** γ + ε

where:
  η_perm  ∈ [0, 1)  — permanent-impact coefficient
  γ       > 0       — exponent (typically 0.4-0.7 for equities)
  q/v_τ             — participation ratio (our share of bar volume)
  ε                 — Student-t(ν=4) noise (heavy-tailed for outliers)

The full v2.2 model also accommodates Tobit-censoring for left-censored
small fills (where realized slippage is below quote-tick resolution).
We model both observed slippages AND censored "<min_slippage" rows.

Four automated trigger gates fire D256-D259 (per 26_d_code_registry.md):
  D256  η_perm posterior mean ≥ 0.65            → capacity reduces; halt high-tier sizing
  D257  γ rolling drift > 0.20 between halves   → halt methodology paper drafting
  D258  Halt rate > 25% in calibration sample   → cohort screen too loose
  D259  Martingale residual rejects zero-mean   → decomposition inadequate

Wire-in: EOD job runs `fit_eta_gamma_from_corpus()` against the Phase 0
corpus + emits the four-trigger gates via `eod_slippage_gates()`. For
the MVP the estimator runs on a curated synthetic + small-real corpus;
the real corpus accumulates as Phase 0 captures sessions.

References:
  - compass_artifact_wf-e274cbca §1.6 (the model spec)
  - 23_slippage_methodology_v2.2_offensive.md §13.4 (gate thresholds)
  - 22_tuesday_architecture_call_agenda.md item #2 (PyMC vs rpy2 decision)
"""
from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass
from typing import Sequence

logger = logging.getLogger(__name__)


# ── Default hyperparameters (per v2.2 spec §13.4) ─────────────────


DEFAULT_NUM_CHAINS: int = 2
"""NUTS chains. v2.2 spec recommends 4; we use 2 for faster CI/EOD runs.
Increase via `chains=4` for the methodology paper figures."""

DEFAULT_NUM_TUNE: int = 1000
DEFAULT_NUM_DRAWS: int = 1000

# Gate thresholds — D256-D259
ETA_PERM_GATE_THRESHOLD: float = 0.65
GAMMA_DRIFT_THRESHOLD: float = 0.20
HALT_RATE_THRESHOLD: float = 0.25
MARTINGALE_T_STAT_THRESHOLD: float = 2.0


# ── Result dataclass ─────────────────────────────────────────────


@dataclass(frozen=True)
class EtaGammaPosterior:
    """Posterior summary of a single fit run."""
    eta_mean: float
    eta_std: float
    eta_q05: float
    eta_q95: float
    gamma_mean: float
    gamma_std: float
    gamma_q05: float
    gamma_q95: float
    n_observations: int
    n_chains: int
    n_draws: int


@dataclass(frozen=True)
class SlippageGateResult:
    """One D256-D259 gate's evaluation outcome."""
    gate_code: str        # "D256" / "D257" / "D258" / "D259"
    gate_name: str        # human label
    triggered: bool
    measured_value: float
    threshold: float
    detail: str


# ── Core estimator ──────────────────────────────────────────────


def fit_eta_gamma(
    participation_ratios: Sequence[float],
    realized_slippages: Sequence[float],
    *,
    censoring_threshold: float | None = None,
    chains: int = DEFAULT_NUM_CHAINS,
    tune: int = DEFAULT_NUM_TUNE,
    draws: int = DEFAULT_NUM_DRAWS,
    student_t_nu: float = 4.0,
    rng_seed: int = 42,
    progressbar: bool = False,
) -> EtaGammaPosterior:
    """Fit the η_perm / γ posterior via PyMC NUTS sampling.

    Model:
      η_perm     ~ Beta(1, 4)        # prior: [0, 1), preferring small
      γ          ~ HalfNormal(σ=1)   # positive exponent
      slip_pred  = η_perm * q ** γ
      slip_obs   ~ StudentT(ν=4, μ=slip_pred, σ=σ_noise)  [pm.Censored if threshold]
      σ_noise    ~ HalfNormal(σ=0.05)

    Args:
      participation_ratios:    sequence of q/v_τ values, all in [0, ∞)
      realized_slippages:      sequence of realized slippage values, same length
      censoring_threshold:     optional Tobit-Type-I left-censoring threshold
                               (per v2.2 spec §1.6). When provided, observations
                               <= this value are treated as left-censored. Useful
                               for microcap regimes where small slippages fall
                               below quote-tick resolution. None = uncensored.
      chains, tune, draws:     NUTS sampler controls
      student_t_nu:            degrees of freedom (heavy tails)
      rng_seed:                for reproducibility

    Returns:
      EtaGammaPosterior with mean / std / 90% credible interval for both params.

    Raises:
      ValueError on length mismatch or empty input.
      ImportError if PyMC isn't installed (caller decides whether to defer).
    """
    if len(participation_ratios) != len(realized_slippages):
        raise ValueError(
            f"length mismatch: q={len(participation_ratios)} "
            f"slip={len(realized_slippages)}"
        )
    if not participation_ratios:
        raise ValueError("empty input — need at least one observation to fit")

    import numpy as np
    # Suppress PyMC's noisy startup warnings during normal estimation
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        import pymc as pm

        q = np.asarray(participation_ratios, dtype=float)
        slip = np.asarray(realized_slippages, dtype=float)

        with pm.Model():
            eta = pm.Beta("eta", alpha=1.0, beta=4.0)
            gamma = pm.HalfNormal("gamma", sigma=1.0)
            sigma_noise = pm.HalfNormal("sigma_noise", sigma=0.05)
            mu = eta * (q ** gamma)

            if censoring_threshold is not None:
                # Tobit-Type-I left-censoring per v2.2 §1.6: observations
                # <= threshold contribute the censored likelihood
                # log(P(StudentT < threshold | μ)). pm.Censored handles
                # this automatically — left-censored values are clamped
                # to the threshold and contribute the CDF.
                slip_clamped = np.maximum(slip, float(censoring_threshold))
                pm.Censored(
                    "slip_obs",
                    pm.StudentT.dist(nu=student_t_nu, mu=mu, sigma=sigma_noise),
                    lower=float(censoring_threshold),
                    upper=None,
                    observed=slip_clamped,
                )
            else:
                # Uncensored Student-t (original v0.1 behavior)
                pm.StudentT(
                    "slip_obs", nu=student_t_nu, mu=mu, sigma=sigma_noise,
                    observed=slip,
                )
            trace = pm.sample(
                draws=draws, tune=tune, chains=chains,
                random_seed=rng_seed, progressbar=progressbar,
                return_inferencedata=True,
            )

    # Extract posterior arrays
    eta_samples = np.asarray(trace.posterior["eta"]).flatten()
    gamma_samples = np.asarray(trace.posterior["gamma"]).flatten()

    return EtaGammaPosterior(
        eta_mean=float(eta_samples.mean()),
        eta_std=float(eta_samples.std()),
        eta_q05=float(np.quantile(eta_samples, 0.05)),
        eta_q95=float(np.quantile(eta_samples, 0.95)),
        gamma_mean=float(gamma_samples.mean()),
        gamma_std=float(gamma_samples.std()),
        gamma_q05=float(np.quantile(gamma_samples, 0.05)),
        gamma_q95=float(np.quantile(gamma_samples, 0.95)),
        n_observations=len(q),
        n_chains=chains,
        n_draws=draws,
    )


# ── Four-trigger gates (D256-D259) ────────────────────────────────


def gate_d256_eta_perm_too_high(posterior: EtaGammaPosterior) -> SlippageGateResult:
    """D256: η_perm posterior mean ≥ 0.65 → capacity reduces."""
    triggered = posterior.eta_mean >= ETA_PERM_GATE_THRESHOLD
    return SlippageGateResult(
        gate_code="D256", gate_name="ETA_PERM_HIGH",
        triggered=triggered,
        measured_value=posterior.eta_mean,
        threshold=ETA_PERM_GATE_THRESHOLD,
        detail=(
            f"η_perm posterior mean={posterior.eta_mean:.3f} "
            f">= {ETA_PERM_GATE_THRESHOLD}"
            if triggered else
            f"η_perm posterior mean={posterior.eta_mean:.3f} within threshold"
        ),
    )


def gate_d257_gamma_drift(
    posterior_first_half: EtaGammaPosterior,
    posterior_second_half: EtaGammaPosterior,
) -> SlippageGateResult:
    """D257: |γ_first - γ_second| > 0.20 → methodology paper halts."""
    drift = abs(posterior_first_half.gamma_mean - posterior_second_half.gamma_mean)
    triggered = drift > GAMMA_DRIFT_THRESHOLD
    return SlippageGateResult(
        gate_code="D257", gate_name="GAMMA_DRIFT",
        triggered=triggered,
        measured_value=drift,
        threshold=GAMMA_DRIFT_THRESHOLD,
        detail=(
            f"γ drift={drift:.3f} > {GAMMA_DRIFT_THRESHOLD} "
            f"(γ_first={posterior_first_half.gamma_mean:.3f}, "
            f"γ_second={posterior_second_half.gamma_mean:.3f})"
            if triggered else
            f"γ drift={drift:.3f} stable"
        ),
    )


def gate_d258_halt_rate(
    n_total_trades: int, n_halted_trades: int,
) -> SlippageGateResult:
    """D258: halt_rate > 25% in calibration sample → cohort screen too loose."""
    rate = (n_halted_trades / n_total_trades) if n_total_trades > 0 else 0.0
    triggered = rate > HALT_RATE_THRESHOLD
    return SlippageGateResult(
        gate_code="D258", gate_name="HALT_RATE_HIGH",
        triggered=triggered,
        measured_value=rate,
        threshold=HALT_RATE_THRESHOLD,
        detail=(
            f"halt_rate={rate*100:.1f}% > {HALT_RATE_THRESHOLD*100:.0f}% "
            f"({n_halted_trades}/{n_total_trades})"
            if triggered else
            f"halt_rate={rate*100:.1f}% within threshold"
        ),
    )


def gate_d259_martingale_residual(
    residuals: Sequence[float],
) -> SlippageGateResult:
    """D259: martingale residual t-statistic rejects zero-mean at 2σ.

    Tests H0: E[residual] = 0 via one-sample t-test. If |t| > 2.0,
    reject H0 → decomposition is mis-specified → halt Paper 1 drafting.
    """
    import math
    n = len(residuals)
    if n < 2:
        return SlippageGateResult(
            gate_code="D259", gate_name="MARTINGALE_RESIDUAL",
            triggered=False, measured_value=0.0,
            threshold=MARTINGALE_T_STAT_THRESHOLD,
            detail=f"insufficient data (n={n}) for t-test",
        )
    mean = sum(residuals) / n
    var = sum((r - mean) ** 2 for r in residuals) / (n - 1)
    if var <= 0:
        return SlippageGateResult(
            gate_code="D259", gate_name="MARTINGALE_RESIDUAL",
            triggered=False, measured_value=0.0,
            threshold=MARTINGALE_T_STAT_THRESHOLD,
            detail="zero variance (constant residuals) — degenerate",
        )
    se = math.sqrt(var / n)
    t_stat = mean / se
    triggered = abs(t_stat) > MARTINGALE_T_STAT_THRESHOLD
    return SlippageGateResult(
        gate_code="D259", gate_name="MARTINGALE_RESIDUAL",
        triggered=triggered,
        measured_value=t_stat,
        threshold=MARTINGALE_T_STAT_THRESHOLD,
        detail=(
            f"|t|={abs(t_stat):.2f} > {MARTINGALE_T_STAT_THRESHOLD} "
            f"(mean={mean:.4f}, n={n}) — decomposition mis-specified"
            if triggered else
            f"|t|={abs(t_stat):.2f} within threshold (mean={mean:.4f}, n={n})"
        ),
    )


def eod_slippage_gates(
    posterior: EtaGammaPosterior,
    *,
    n_total_trades: int,
    n_halted_trades: int,
    residuals: Sequence[float],
    posterior_first_half: EtaGammaPosterior | None = None,
    posterior_second_half: EtaGammaPosterior | None = None,
) -> list[SlippageGateResult]:
    """Run all four gates + log each. Returns the list for EOD report."""
    results: list[SlippageGateResult] = []
    results.append(gate_d256_eta_perm_too_high(posterior))
    if posterior_first_half is not None and posterior_second_half is not None:
        results.append(gate_d257_gamma_drift(posterior_first_half, posterior_second_half))
    results.append(gate_d258_halt_rate(n_total_trades, n_halted_trades))
    results.append(gate_d259_martingale_residual(residuals))

    for r in results:
        if r.triggered:
            logger.warning(
                "%s %s TRIGGERED: %s",
                r.gate_code, r.gate_name, r.detail,
            )
        else:
            logger.info(
                "%s %s ok: %s",
                r.gate_code, r.gate_name, r.detail,
            )
    return results
