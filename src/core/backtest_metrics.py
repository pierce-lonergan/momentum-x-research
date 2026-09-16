"""
MOMENTUM-X Backtest Metrics: Deflated Sharpe Ratio

### ARCHITECTURAL CONTEXT
Node ID: core.backtest_metrics
Graph Link: extends core.backtester

### RESEARCH BASIS
Implements the Deflated Sharpe Ratio (DSR) from §18.4 and §18.5.
DSR corrects the observed Sharpe ratio for:
  1. Multiple testing (N strategies tried)
  2. Non-normal returns (skewness, kurtosis)
  3. Finite sample bias

The key insight: with N backtested strategies, the maximum Sharpe
is expected to be high even if no strategy has genuine alpha.
DSR tests whether the observed Sharpe exceeds this random threshold.

Ref: Bailey & Lopez de Prado (2014) — "The Deflated Sharpe Ratio"
Ref: docs/research/CPCV_LLM_LEAKAGE.md §4
Ref: MOMENTUM_LOGIC.md §18.4

### CRITICAL INVARIANTS
1. DSR > 0.95 required for strategy acceptance (95% confidence).
2. Standard error accounts for skewness (γ₃) and kurtosis (γ₄).
3. Expected max SR uses Euler-Mascheroni constant γ ≈ 0.5772.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Euler-Mascheroni constant
_EULER_MASCHERONI = 0.5772156649015329


def _standard_normal_cdf(x: float) -> float:
    """Standard normal CDF Φ(x) via error function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _standard_normal_ppf(p: float) -> float:
    """
    Approximate inverse standard normal CDF (probit function).

    Uses rational approximation (Abramowitz and Stegun 26.2.23).
    Accurate to ~4.5e-4 for 0.0 < p < 1.0.
    """
    if p <= 0.0:
        return -10.0
    if p >= 1.0:
        return 10.0

    # Symmetry trick
    if p < 0.5:
        return -_standard_normal_ppf(1.0 - p)

    t = math.sqrt(-2.0 * math.log(1.0 - p))
    # Rational approximation constants
    c0, c1, c2 = 2.515517, 0.802853, 0.010328
    d1, d2, d3 = 1.432788, 0.189269, 0.001308

    return t - (c0 + c1 * t + c2 * t**2) / (1.0 + d1 * t + d2 * t**2 + d3 * t**3)


def compute_sr_standard_error(
    observed_sharpe: float,
    returns_length: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """
    Compute the standard error of the Sharpe ratio, adjusted for
    higher moments.

    Formula (§18.4):
        σ̂²_SR = (1/(T-1)) × [1 + ½SR² - γ₃SR + ((γ₄-3)/4)SR²]

    Args:
        observed_sharpe: The observed (annualized) Sharpe ratio.
        returns_length: Number of return observations T.
        skewness: Sample skewness γ₃ (0.0 for normal).
        kurtosis: Sample kurtosis γ₄ (3.0 for normal).

    Returns:
        Standard error of the Sharpe ratio.

    Ref: Bailey & Lopez de Prado (2014), Eq. 4
    """
    if returns_length <= 1:
        return float("inf")

    sr = observed_sharpe
    t = returns_length

    variance = (1.0 / (t - 1)) * (
        1.0
        + 0.5 * sr**2
        - skewness * sr
        + ((kurtosis - 3.0) / 4.0) * sr**2
    )

    return math.sqrt(max(0.0, variance))


def compute_expected_max_sharpe(
    num_trials: int,
    returns_length: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """
    Compute the expected maximum Sharpe ratio from N unskilled strategies.

    Formula (§18.4):
        E[max_N] ≈ σ_SR × [(1-γ)Z⁻¹(1-1/N) + γZ⁻¹(1-1/(Ne))]

    Where:
        γ ≈ 0.5772 (Euler-Mascheroni constant)
        Z⁻¹ = inverse standard normal CDF
        σ_SR = standard error of SR under null (SR=0)

    Args:
        num_trials: Number of strategies backtested (N).
        returns_length: Number of return observations T.
        skewness: Sample skewness.
        kurtosis: Sample kurtosis.

    Returns:
        Expected maximum Sharpe ratio under null hypothesis.

    Ref: Bailey & Lopez de Prado (2014), Eq. 6
    """
    if num_trials <= 1:
        return 0.0

    # SR standard error under null (SR=0)
    sigma_sr = compute_sr_standard_error(
        observed_sharpe=0.0,
        returns_length=returns_length,
        skewness=skewness,
        kurtosis=kurtosis,
    )

    gamma = _EULER_MASCHERONI
    n = num_trials
    e = math.e

    # Quantiles
    z1 = _standard_normal_ppf(1.0 - 1.0 / n) if n > 1 else 0.0
    z2 = _standard_normal_ppf(1.0 - 1.0 / (n * e)) if n * e > 1 else 0.0

    expected_max = sigma_sr * ((1.0 - gamma) * z1 + gamma * z2)

    return expected_max


def compute_deflated_sharpe(
    observed_sharpe: float,
    num_trials: int,
    returns_length: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
) -> float:
    """
    Compute the Deflated Sharpe Ratio (DSR).

    Formula (§18.4):
        DSR(SR̂) = Φ[(SR̂ - E[max_N]) / σ̂_SR]

    Where:
        SR̂ = observed Sharpe ratio
        E[max_N] = expected max SR from N unskilled strategies
        σ̂_SR = standard error of SR (adjusted for higher moments)
        Φ = standard normal CDF

    Interpretation:
        DSR > 0.95 → 95% confident the strategy is not a false positive
        DSR < 0.50 → strategy is likely a result of multiple testing

    Args:
        observed_sharpe: The observed (annualized) Sharpe ratio.
        num_trials: Number of strategies backtested (N).
        returns_length: Number of return observations T.
        skewness: Sample skewness γ₃ (0.0 for normal).
        kurtosis: Sample kurtosis γ₄ (3.0 for normal).

    Returns:
        DSR in [0, 1]. Higher is better.

    Ref: Bailey & Lopez de Prado (2014), "The Deflated Sharpe Ratio"
    Ref: MOMENTUM_LOGIC.md §18.4
    """
    # Expected max SR under null
    e_max = compute_expected_max_sharpe(
        num_trials=num_trials,
        returns_length=returns_length,
        skewness=skewness,
        kurtosis=kurtosis,
    )

    # Standard error of observed SR
    sigma_sr = compute_sr_standard_error(
        observed_sharpe=observed_sharpe,
        returns_length=returns_length,
        skewness=skewness,
        kurtosis=kurtosis,
    )

    if sigma_sr <= 0 or not math.isfinite(sigma_sr):
        return 0.0

    # DSR = Φ((SR - E[max]) / σ_SR)
    z = (observed_sharpe - e_max) / sigma_sr
    dsr = _standard_normal_cdf(z)

    return dsr


def evaluate_strategy_acceptance(
    pbo: float,
    observed_sharpe: float,
    num_trials: int,
    returns_length: int,
    skewness: float = 0.0,
    kurtosis: float = 3.0,
    pbo_threshold: float = 0.10,
    dsr_threshold: float = 0.95,
) -> dict[str, float | bool]:
    """
    Combined strategy acceptance gate: PBO + DSR.

    A strategy is accepted if and only if:
        1. PBO < pbo_threshold (INV-001: The 90% Rule)
        2. DSR > dsr_threshold (§18.4: Deflated Sharpe correction)

    Both conditions must hold simultaneously. This prevents:
    - PBO alone missing multiple testing bias
    - DSR alone missing overfitting to training data

    Args:
        pbo: Probability of Backtest Overfitting from CPCV.
        observed_sharpe: Annualized Sharpe ratio from best-performing path.
        num_trials: Number of strategies/configurations tested.
        returns_length: Number of return observations.
        skewness: Sample skewness of returns.
        kurtosis: Sample kurtosis of returns.
        pbo_threshold: Maximum acceptable PBO (default 0.10 per INV-001).
        dsr_threshold: Minimum acceptable DSR (default 0.95).

    Returns:
        Dict with keys:
            accepted: True if strategy passes both gates.
            pbo: The PBO value.
            pbo_pass: True if PBO < threshold.
            dsr: The DSR value.
            dsr_pass: True if DSR > threshold.
            pbo_threshold: The threshold used.
            dsr_threshold: The threshold used.

    Ref: Bailey & Lopez de Prado (2014) — "The Deflated Sharpe Ratio"
    Ref: Lopez de Prado (2018) — "Advances in Financial ML", Ch. 12
    Ref: MOMENTUM_LOGIC.md §18.4, §18.5
    """
    dsr = compute_deflated_sharpe(
        observed_sharpe=observed_sharpe,
        num_trials=num_trials,
        returns_length=returns_length,
        skewness=skewness,
        kurtosis=kurtosis,
    )

    pbo_pass = pbo < pbo_threshold
    dsr_pass = dsr > dsr_threshold
    accepted = pbo_pass and dsr_pass

    return {
        "accepted": accepted,
        "pbo": pbo,
        "pbo_pass": pbo_pass,
        "dsr": dsr,
        "dsr_pass": dsr_pass,
        "pbo_threshold": pbo_threshold,
        "dsr_threshold": dsr_threshold,
    }


# ═══════════════════════════════════════════════════════════════════
# D68: Trade-Level Performance Metrics
# ═══════════════════════════════════════════════════════════════════


@dataclass
class TradeLevelMetrics:
    """
    Aggregated trade-level performance statistics.

    Computed from a list of realized P&L values. Used for both
    backtest evaluation and post-session journal analysis.

    Ref: MOMENTUM_LOGIC.md §17 (Post-trade Analysis)
    Ref: S038 WS5 (D68)
    """

    total_trades: int = 0
    win_rate_pct: float = 0.0
    profit_factor: float = 0.0
    max_drawdown_pct: float = 0.0
    avg_trade_duration_min: float = 0.0
    max_consecutive_losses: int = 0
    avg_winner_pnl: float = 0.0
    avg_loser_pnl: float = 0.0
    largest_winner: float = 0.0
    largest_loser: float = 0.0
    expectancy: float = 0.0  # avg_winner * win_rate - avg_loser * loss_rate


def compute_trade_level_metrics(
    trade_pnls: list[float],
    trade_durations_min: list[float] | None = None,
) -> TradeLevelMetrics:
    """
    Compute trade-level performance metrics from a P&L list.

    Args:
        trade_pnls: List of realized P&L per trade (positive = win).
        trade_durations_min: Optional list of hold durations in minutes.

    Returns:
        TradeLevelMetrics with all computed fields.

    Ref: S038 WS5 (D68)
    """
    if not trade_pnls:
        return TradeLevelMetrics()

    winners = [p for p in trade_pnls if p > 0]
    losers = [p for p in trade_pnls if p <= 0]

    win_rate = len(winners) / len(trade_pnls)
    gross_profit = sum(winners) if winners else 0.0
    gross_loss = abs(sum(losers)) if losers else 0.0

    # Max consecutive losses
    max_streak = 0
    current_streak = 0
    for p in trade_pnls:
        if p <= 0:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0

    # Max drawdown from equity curve
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in trade_pnls:
        equity += p
        peak = max(peak, equity)
        if abs(peak) > 0:
            dd = (peak - equity) / abs(peak)
            max_dd = max(max_dd, dd)

    avg_winner = gross_profit / max(1, len(winners))
    avg_loser = gross_loss / max(1, len(losers))

    avg_dur = 0.0
    if trade_durations_min:
        avg_dur = sum(trade_durations_min) / len(trade_durations_min)

    return TradeLevelMetrics(
        total_trades=len(trade_pnls),
        win_rate_pct=round(win_rate * 100, 1),
        profit_factor=round(gross_profit / max(0.01, gross_loss), 2),
        max_drawdown_pct=round(max_dd * 100, 2),
        avg_trade_duration_min=round(avg_dur, 1),
        max_consecutive_losses=max_streak,
        avg_winner_pnl=round(avg_winner, 2),
        avg_loser_pnl=round(-avg_loser, 2),  # Negative for display
        largest_winner=round(max(trade_pnls), 2),
        largest_loser=round(min(trade_pnls), 2),
        expectancy=round(avg_winner * win_rate - avg_loser * (1 - win_rate), 2),
    )


def compute_metrics_from_journal(entries: list) -> TradeLevelMetrics:
    """
    Compute trade-level metrics from TradeJournal entries.

    Args:
        entries: List of JournalEntry objects (loaded from JSONL).

    Returns:
        TradeLevelMetrics computed from closed trades only.

    Ref: S038 WS5 (D68)
    """
    closed = [e for e in entries if getattr(e, "realized_pnl", None) is not None
              and getattr(e, "action", "") == "BUY"]

    trade_pnls = [e.realized_pnl for e in closed]
    trade_durations = [
        e.hold_duration_minutes for e in closed
        if getattr(e, "hold_duration_minutes", None) is not None
    ]

    return compute_trade_level_metrics(
        trade_pnls=trade_pnls,
        trade_durations_min=trade_durations if trade_durations else None,
    )
