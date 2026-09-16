"""
Statistical tools for parameter sweep analysis.

Bootstrap confidence intervals prevent drawing conclusions from noise.
Rank configurations by CI lower bound, not point estimate.
"""

from __future__ import annotations

import random
from typing import Any


def bootstrap_profit_factor(
    trades: list[dict],
    n_bootstrap: int = 1000,
    seed: int = 42,
) -> tuple[float, float, float]:
    """
    Bootstrap 95% CI on profit factor.

    Returns (point_estimate, ci_lower, ci_upper).
    If CI lower bound > 1.0, the strategy is profitable with 95% confidence.
    """
    pnls = [t.get("pnl", 0) for t in trades if "pnl" in t]
    if len(pnls) < 2:
        pf = _compute_pf(pnls)
        return (pf, 0.0, pf * 2)

    rng = random.Random(seed)
    pf_samples = []
    for _ in range(n_bootstrap):
        sample = rng.choices(pnls, k=len(pnls))
        pf_samples.append(_compute_pf(sample))

    pf_samples.sort()
    ci_lower = pf_samples[int(0.025 * n_bootstrap)]
    ci_upper = pf_samples[int(0.975 * n_bootstrap)]
    point = _compute_pf(pnls)
    return (round(point, 3), round(ci_lower, 3), round(ci_upper, 3))


def bootstrap_mean_pnl(
    trades: list[dict],
    n_bootstrap: int = 1000,
    seed: int = 42,
) -> tuple[float, float, float]:
    """Bootstrap 95% CI on mean P&L per trade."""
    pnls = [t.get("pnl", 0) for t in trades if "pnl" in t]
    if len(pnls) < 2:
        m = sum(pnls) / max(len(pnls), 1)
        return (m, m, m)

    rng = random.Random(seed)
    means = []
    for _ in range(n_bootstrap):
        sample = rng.choices(pnls, k=len(pnls))
        means.append(sum(sample) / len(sample))

    means.sort()
    ci_lower = means[int(0.025 * n_bootstrap)]
    ci_upper = means[int(0.975 * n_bootstrap)]
    point = sum(pnls) / len(pnls)
    return (round(point, 4), round(ci_lower, 4), round(ci_upper, 4))


def _compute_pf(pnls: list[float]) -> float:
    """Compute profit factor from list of P&L values."""
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    if gross_loss == 0:
        return 99.0 if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def rank_by_ci_lower(
    results: list[dict],
    metric: str = "profit_factor",
    n_bootstrap: int = 1000,
) -> list[dict]:
    """
    Add bootstrap CI to results and rank by CI lower bound.

    This is the statistically correct way to compare configurations:
    a config with PF 1.5 CI [1.1, 2.0] beats PF 1.8 CI [0.9, 3.2]
    because its CI excludes unprofitable territory.
    """
    for r in results:
        trades = r.get("sim_trades", [])
        pf, ci_lo, ci_hi = bootstrap_profit_factor(trades, n_bootstrap)
        r["pf_point"] = pf
        r["pf_ci_lower"] = ci_lo
        r["pf_ci_upper"] = ci_hi

        mean, mean_lo, mean_hi = bootstrap_mean_pnl(trades, n_bootstrap)
        r["mean_pnl"] = mean
        r["mean_pnl_ci_lower"] = mean_lo
        r["mean_pnl_ci_upper"] = mean_hi

    results.sort(key=lambda r: r.get("pf_ci_lower", 0), reverse=True)
    return results
