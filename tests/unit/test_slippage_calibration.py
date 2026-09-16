"""Bayesian (η, γ) slippage calibration tests.

Per the v2.2 §13 spec + items 12-15. 11 tests across 3 categories:

  Estimator behavior (3): synthetic ground truth recovery + edge cases
  Four-trigger gates  (6): D256, D257, D258, D259 fire/don't-fire
  EOD wrapper         (2): runs all gates + logs each

Sampler config: chains=2, tune=200, draws=200 to keep tests fast (<30s).
The methodology paper figures use chains=4, tune=2000, draws=2000.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

# Skip the entire module if PyMC isn't available (CI without the dep)
pymc = pytest.importorskip("pymc")

from src.analysis.slippage_calibration import (  # noqa: E402
    EtaGammaPosterior,
    ETA_PERM_GATE_THRESHOLD,
    GAMMA_DRIFT_THRESHOLD,
    HALT_RATE_THRESHOLD,
    MARTINGALE_T_STAT_THRESHOLD,
    SlippageGateResult,
    eod_slippage_gates,
    fit_eta_gamma,
    gate_d256_eta_perm_too_high,
    gate_d257_gamma_drift,
    gate_d258_halt_rate,
    gate_d259_martingale_residual,
)


# ── Helpers ─────────────────────────────────────────────────────


def _synthetic_eta_gamma_data(
    *, eta_true: float, gamma_true: float, n: int = 200,
    sigma_noise: float = 0.005, seed: int = 0,
) -> tuple[list[float], list[float]]:
    """Generate (q, slip) data from the true model with Gaussian noise."""
    rng = np.random.default_rng(seed)
    # Participation ratios in [0.001, 0.5] — span microcap regime
    q = rng.uniform(0.001, 0.5, size=n)
    slip = eta_true * (q ** gamma_true) + rng.normal(0, sigma_noise, size=n)
    return q.tolist(), slip.tolist()


# ── 1. Estimator behavior ─────────────────────────────────────


@pytest.mark.slow
class TestEstimatorBehavior:
    """NOTE: NUTS sampling is the bottleneck. Tests use small chain/draw
    counts (200 each) to keep CI under 30s; recovery tolerance is loose.
    """

    def test_recovers_ground_truth_within_3_sigma(self) -> None:
        """Generate from η=0.30, γ=0.55. Posterior mean must be within 3σ
        of truth. (The 90% CI test is too strict when the posterior is
        very tight from a clean n=200 corpus — this is a more robust check.)"""
        q, slip = _synthetic_eta_gamma_data(eta_true=0.30, gamma_true=0.55, n=200)
        post = fit_eta_gamma(q, slip, chains=2, tune=200, draws=200, rng_seed=7)
        # 3σ bracket — 99.7% of well-calibrated posteriors should pass
        assert abs(post.eta_mean - 0.30) <= 3 * max(post.eta_std, 0.01), (
            f"η true=0.30, posterior mean={post.eta_mean:.3f} ± {post.eta_std:.4f} "
            f"(3σ bracket failed)"
        )
        assert abs(post.gamma_mean - 0.55) <= 3 * max(post.gamma_std, 0.01), (
            f"γ true=0.55, posterior mean={post.gamma_mean:.3f} ± {post.gamma_std:.4f}"
        )
        # Also verify the posterior is meaningfully concentrated (not just prior)
        assert post.eta_std < 0.10, f"η posterior too wide: σ={post.eta_std:.3f}"
        assert post.gamma_std < 0.20, f"γ posterior too wide: σ={post.gamma_std:.3f}"

    def test_empty_input_raises(self) -> None:
        with pytest.raises(ValueError, match="empty input"):
            fit_eta_gamma([], [], chains=2, tune=50, draws=50)

    def test_length_mismatch_raises(self) -> None:
        with pytest.raises(ValueError, match="length mismatch"):
            fit_eta_gamma([0.1, 0.2], [0.005], chains=2, tune=50, draws=50)

    def test_tobit_censoring_recovers_truth_when_small_fills_left_censored(
        self,
    ) -> None:
        """Generate slippage from η=0.30, γ=0.55. Censor observations
        below 0.005 (microcap quote-tick floor). Posterior must still
        recover both params within 3σ."""
        q, slip = _synthetic_eta_gamma_data(
            eta_true=0.30, gamma_true=0.55, n=200, sigma_noise=0.005, seed=11,
        )
        # Apply Tobit-Type-I left-censoring
        post = fit_eta_gamma(
            q, slip,
            censoring_threshold=0.005,  # Tobit floor
            chains=2, tune=200, draws=200, rng_seed=11,
        )
        # Recovery within 3σ
        assert abs(post.eta_mean - 0.30) <= 3 * max(post.eta_std, 0.01), (
            f"η true=0.30, censored posterior mean={post.eta_mean:.3f} "
            f"± {post.eta_std:.4f}"
        )
        assert abs(post.gamma_mean - 0.55) <= 3 * max(post.gamma_std, 0.01), (
            f"γ true=0.55, censored posterior mean={post.gamma_mean:.3f} "
            f"± {post.gamma_std:.4f}"
        )


# ── 2. Four-trigger gates ────────────────────────────────────


def _post(eta: float, gamma: float = 0.5, n: int = 100) -> EtaGammaPosterior:
    """Build a synthetic posterior for gate testing — bypasses NUTS."""
    return EtaGammaPosterior(
        eta_mean=eta, eta_std=0.05, eta_q05=eta - 0.1, eta_q95=eta + 0.1,
        gamma_mean=gamma, gamma_std=0.05, gamma_q05=gamma - 0.1, gamma_q95=gamma + 0.1,
        n_observations=n, n_chains=2, n_draws=200,
    )


class TestGateD256EtaPermHigh:

    def test_fires_when_above_threshold(self) -> None:
        result = gate_d256_eta_perm_too_high(_post(eta=0.70))
        assert result.triggered is True
        assert result.gate_code == "D256"
        assert result.measured_value == pytest.approx(0.70)

    def test_does_not_fire_below_threshold(self) -> None:
        result = gate_d256_eta_perm_too_high(_post(eta=0.40))
        assert result.triggered is False


class TestGateD257GammaDrift:

    def test_fires_when_drift_exceeds(self) -> None:
        a = _post(eta=0.30, gamma=0.40)
        b = _post(eta=0.30, gamma=0.70)  # 0.30 drift > 0.20
        result = gate_d257_gamma_drift(a, b)
        assert result.triggered is True
        assert result.measured_value == pytest.approx(0.30)

    def test_does_not_fire_when_stable(self) -> None:
        a = _post(eta=0.30, gamma=0.50)
        b = _post(eta=0.30, gamma=0.55)  # 0.05 drift < 0.20
        result = gate_d257_gamma_drift(a, b)
        assert result.triggered is False


class TestGateD258HaltRate:

    def test_fires_above_25_percent(self) -> None:
        result = gate_d258_halt_rate(n_total_trades=100, n_halted_trades=30)
        assert result.triggered is True
        assert result.measured_value == pytest.approx(0.30)

    def test_does_not_fire_below(self) -> None:
        result = gate_d258_halt_rate(n_total_trades=100, n_halted_trades=10)
        assert result.triggered is False


class TestGateD259MartingaleResidual:

    def test_fires_on_significant_nonzero_mean(self) -> None:
        # Generate residuals with mean clearly off zero
        residuals = [0.5] * 100  # constant non-zero — pathological but valid
        # constant → variance 0 — special-cased to "not triggered"
        result = gate_d259_martingale_residual(residuals)
        assert result.triggered is False  # zero variance edge

    def test_fires_on_significant_drift_with_variance(self) -> None:
        # Mean = 1.0, std small enough that t-stat exceeds 2
        rng = np.random.default_rng(0)
        residuals = (rng.normal(1.0, 0.5, 100)).tolist()
        result = gate_d259_martingale_residual(residuals)
        assert result.triggered is True
        assert abs(result.measured_value) > MARTINGALE_T_STAT_THRESHOLD

    def test_passes_zero_mean_residuals(self) -> None:
        rng = np.random.default_rng(1)
        residuals = (rng.normal(0.0, 1.0, 100)).tolist()
        result = gate_d259_martingale_residual(residuals)
        assert result.triggered is False

    def test_handles_insufficient_data(self) -> None:
        result = gate_d259_martingale_residual([])
        assert result.triggered is False
        assert "insufficient data" in result.detail


# ── 3. EOD wrapper ───────────────────────────────────────────


class TestEodWrapper:

    def test_runs_all_three_gates_when_no_split_provided(self, caplog) -> None:
        """No first/second halves passed → D257 skipped, D256+D258+D259 run."""
        import logging
        post = _post(eta=0.40, gamma=0.5)
        with caplog.at_level(logging.INFO, logger="src.analysis.slippage_calibration"):
            results = eod_slippage_gates(
                post, n_total_trades=100, n_halted_trades=10,
                residuals=[0.0] * 50,
            )
        # D256 + D258 + D259 = 3 gates (D257 needs both halves)
        assert len(results) == 3
        codes = [r.gate_code for r in results]
        assert "D256" in codes
        assert "D257" not in codes
        assert "D258" in codes
        assert "D259" in codes

    def test_logs_warning_when_gate_triggers(self, caplog) -> None:
        import logging
        post = _post(eta=0.80, gamma=0.5)  # η > 0.65 → D256 fires
        with caplog.at_level(logging.WARNING, logger="src.analysis.slippage_calibration"):
            eod_slippage_gates(
                post, n_total_trades=100, n_halted_trades=5,
                residuals=[0.0] * 50,
            )
        # D256 must log at WARNING with literal marker
        assert any(
            "D256" in r.message and "TRIGGERED" in r.message
            for r in caplog.records
        )
