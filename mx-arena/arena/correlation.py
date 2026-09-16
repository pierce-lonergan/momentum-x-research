"""
Cross-Asset Correlation Modeling.

Innovation 10: When positions are correlated (sector selloff), reduce
max_positions to prevent concentrated losses. Compute rolling correlation
between candidates and SPY to detect systemic risk days.

Also includes fill model validation (Gap 3/Innovation 9).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .regime import _load_daily_bars, _extract_date

logger = logging.getLogger(__name__)


def compute_spy_correlation(
    ticker_bars: list[dict],
    spy_bars: list[dict],
    window: int = 20,
) -> float:
    """
    Compute rolling correlation between a ticker and SPY.

    Uses daily close-to-close returns over the trailing window.
    High correlation (>0.7) suggests systemic risk — reduce positions.
    """
    if len(ticker_bars) < window + 1 or len(spy_bars) < window + 1:
        return 0.0

    # Compute daily returns
    ticker_returns = []
    for i in range(1, min(len(ticker_bars), window + 1)):
        prev_c = ticker_bars[i - 1].get("c", 0)
        curr_c = ticker_bars[i].get("c", 0)
        if prev_c > 0:
            ticker_returns.append((curr_c - prev_c) / prev_c)

    spy_returns = []
    for i in range(1, min(len(spy_bars), window + 1)):
        prev_c = spy_bars[i - 1].get("c", 0)
        curr_c = spy_bars[i].get("c", 0)
        if prev_c > 0:
            spy_returns.append((curr_c - prev_c) / prev_c)

    n = min(len(ticker_returns), len(spy_returns))
    if n < 5:
        return 0.0

    ticker_returns = ticker_returns[:n]
    spy_returns = spy_returns[:n]

    # Pearson correlation
    mean_t = sum(ticker_returns) / n
    mean_s = sum(spy_returns) / n

    cov = sum((t - mean_t) * (s - mean_s) for t, s in zip(ticker_returns, spy_returns)) / n
    std_t = (sum((t - mean_t) ** 2 for t in ticker_returns) / n) ** 0.5
    std_s = (sum((s - mean_s) ** 2 for s in spy_returns) / n) ** 0.5

    if std_t == 0 or std_s == 0:
        return 0.0

    return round(cov / (std_t * std_s), 4)


def compute_portfolio_correlation(
    tickers: list[str],
    daily_dir: str | Path,
    date: str,
) -> dict[str, float]:
    """
    Compute SPY correlation for each ticker in the portfolio.

    Returns {ticker: correlation} dict.
    """
    daily = Path(daily_dir)
    spy_bars = _load_daily_bars(daily, "SPY")

    correlations = {}
    for ticker in tickers:
        ticker_bars = _load_daily_bars(daily, ticker)
        if ticker_bars:
            corr = compute_spy_correlation(ticker_bars, spy_bars)
            correlations[ticker] = corr

    return correlations


def suggest_max_positions(
    correlations: dict[str, float],
    base_max: int = 8,
    high_corr_threshold: float = 0.7,
) -> int:
    """
    Reduce max_positions when portfolio is highly correlated.

    If >50% of candidates have SPY correlation > threshold,
    reduce max_positions by 1 per 20% above 50%.
    """
    if not correlations:
        return base_max

    high_corr_count = sum(1 for c in correlations.values() if abs(c) > high_corr_threshold)
    high_corr_pct = high_corr_count / len(correlations)

    if high_corr_pct > 0.7:
        return max(2, base_max - 3)  # Highly correlated: limit to 5
    elif high_corr_pct > 0.5:
        return max(3, base_max - 2)  # Moderately correlated: limit to 6
    elif high_corr_pct > 0.3:
        return max(4, base_max - 1)  # Some correlation: limit to 7

    return base_max


# ── Fill Model Validation (Gap 3 / Innovation 9) ────────────────────

def validate_fill_model(
    journal_entries: list[dict],
    spread_model,
) -> dict[str, Any]:
    """
    Compare spread model's ask price to journal entry prices.

    For each BUY entry, compute what the spread model would have
    charged and compare to the journal's intended entry price.
    """
    from datetime import datetime, timezone
    deltas = []

    for entry in journal_entries:
        if entry.get("action") not in ("BUY", "STRONG_BUY"):
            continue

        price = entry.get("entry_price", 0)
        if price <= 0:
            continue

        # Compute spread model's ask at this price/time
        vol = entry.get("premarket_volume", 50000)
        ts = datetime.now(timezone.utc)  # Placeholder — would use entry timestamp
        _, ask = spread_model.get_bid_ask(price, vol, ts)

        delta = ask - price  # Positive = model charges more than journal price
        delta_pct = delta / price * 100

        deltas.append({
            "ticker": entry.get("ticker", ""),
            "journal_price": price,
            "model_ask": round(ask, 4),
            "delta": round(delta, 4),
            "delta_pct": round(delta_pct, 4),
        })

    if not deltas:
        return {"n": 0, "mean_delta": 0, "mean_delta_pct": 0, "bias": "no_data"}

    mean_delta = sum(d["delta"] for d in deltas) / len(deltas)
    mean_delta_pct = sum(d["delta_pct"] for d in deltas) / len(deltas)

    if mean_delta_pct > 0.5:
        bias = "model_pessimistic"  # Model charges more than journal
    elif mean_delta_pct < -0.5:
        bias = "model_optimistic"  # Model charges less (phantom profit risk)
    else:
        bias = "unbiased"

    return {
        "n": len(deltas),
        "mean_delta": round(mean_delta, 4),
        "mean_delta_pct": round(mean_delta_pct, 4),
        "bias": bias,
        "deltas": deltas[:10],  # First 10 for inspection
    }
