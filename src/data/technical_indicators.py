"""
MOMENTUM-X Technical Indicator Calculator

### ARCHITECTURAL CONTEXT
Node ID: data.technical_indicators
Graph Link: docs/memory/graph_state.json → "data.technical_indicators"

### RESEARCH BASIS
Technical breakout confirmation is 2nd-highest MFCS weight (w=0.20).
This module computes RSI, MACD, Bollinger Bands, support/resistance,
and VWAP from OHLCV bars fetched by AlpacaDataClient.

Feeds directly into TechnicalAgent.analyze(price_data=..., indicators=...).

Ref: MOMENTUM_LOGIC.md §5 (MFCS weights)
Ref: PROMPT_SIGNATURES.md → TECHNICAL_AGENT

### CRITICAL INVARIANTS
1. All computations use only data available at evaluation time (no lookahead).
2. NaN/zero bars are filtered before computation.
3. Returns empty dicts when insufficient data — never fabricates values.
4. All indicator values are plain floats (JSON-serializable for LLM prompt).
"""

from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)


def compute_indicators(
    bars_1min: list[dict[str, Any]],
    bars_5min: list[dict[str, Any]] | None = None,
    bars_daily: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Compute technical indicators from OHLCV bars.

    Args:
        bars_1min: 1-minute bars (most recent last), each {t, o, h, l, c, v}.
        bars_5min: 5-minute bars (optional, derived from 1min if absent).
        bars_daily: Daily bars (optional, for multi-timeframe analysis).

    Returns:
        Dict of indicator_name → value, suitable for TechnicalAgent.
    """
    indicators: dict[str, Any] = {}

    if not bars_1min or len(bars_1min) < 2:
        return indicators

    closes = [b["c"] for b in bars_1min if b.get("c", 0) > 0]
    highs = [b["h"] for b in bars_1min if b.get("h", 0) > 0]
    lows = [b["l"] for b in bars_1min if b.get("l", 0) > 0]
    volumes = [b["v"] for b in bars_1min if b.get("v", 0) > 0]

    if len(closes) < 5:
        return indicators

    # RSI (14-period) — standard
    rsi = _compute_rsi(closes, period=14)
    if rsi is not None:
        indicators["rsi_14"] = round(rsi, 2)

    # D87: RSI (9-period) — faster, tuned for small-cap momentum.
    # RSI(14) is too slow for fast-moving sub-$10 stocks.
    # Thresholds: 75/25 (vs standard 70/30) for small caps.
    rsi_fast = _compute_rsi(closes, period=9)
    if rsi_fast is not None:
        indicators["rsi_9"] = round(rsi_fast, 2)
        indicators["rsi_9_overbought"] = rsi_fast >= 75.0
        indicators["rsi_9_oversold"] = rsi_fast <= 25.0

    # MACD (12, 26, 9) — standard
    macd_data = _compute_macd(closes)
    if macd_data:
        indicators["macd_line"] = round(macd_data["macd"], 4)
        indicators["macd_signal"] = round(macd_data["signal"], 4)
        indicators["macd_histogram"] = round(macd_data["histogram"], 4)

    # D87: Fast MACD (5, 13, 4) — tuned for small-cap momentum.
    # Standard (12,26,9) responds too slowly for intraday breakouts.
    macd_fast = _compute_macd(closes, fast=5, slow=13, signal_period=4)
    if macd_fast:
        indicators["macd_fast_line"] = round(macd_fast["macd"], 4)
        indicators["macd_fast_signal"] = round(macd_fast["signal"], 4)
        indicators["macd_fast_histogram"] = round(macd_fast["histogram"], 4)

    # Bollinger Bands (20-period, 2 std dev)
    bb = _compute_bollinger_bands(closes, period=20, num_std=2.0)
    if bb:
        indicators["bb_upper"] = round(bb["upper"], 4)
        indicators["bb_middle"] = round(bb["middle"], 4)
        indicators["bb_lower"] = round(bb["lower"], 4)
        indicators["bb_width_pct"] = round(bb["width_pct"], 4)
        indicators["bb_position"] = round(bb["position"], 4)

    # Support / Resistance (pivot points from recent bars)
    sr = _compute_support_resistance(highs, lows, closes)
    if sr:
        indicators["support_level"] = round(sr["support"], 4)
        indicators["resistance_level"] = round(sr["resistance"], 4)
        indicators["pivot_point"] = round(sr["pivot"], 4)

    # VWAP (if we have volume data)
    if volumes and len(volumes) == len(closes):
        vwap = _compute_vwap(bars_1min)
        if vwap is not None:
            indicators["vwap"] = round(vwap, 4)

    # Simple Moving Averages
    if len(closes) >= 9:
        indicators["sma_9"] = round(sum(closes[-9:]) / 9, 4)
    if len(closes) >= 20:
        indicators["sma_20"] = round(sum(closes[-20:]) / 20, 4)

    # EMA 9 and 21
    ema9 = _compute_ema(closes, 9)
    if ema9 is not None:
        indicators["ema_9"] = round(ema9, 4)
    ema21 = _compute_ema(closes, 21)
    if ema21 is not None:
        indicators["ema_21"] = round(ema21, 4)

    # Volume profile summary
    if volumes:
        avg_vol = sum(volumes) / len(volumes)
        recent_vol = sum(volumes[-5:]) / min(5, len(volumes[-5:]))
        indicators["avg_bar_volume"] = round(avg_vol, 0)
        indicators["recent_bar_volume"] = round(recent_vol, 0)
        if avg_vol > 0:
            indicators["volume_surge_ratio"] = round(recent_vol / avg_vol, 2)

    return indicators


def format_price_data(
    bars_1min: list[dict[str, Any]],
    bars_5min: list[dict[str, Any]] | None = None,
    bars_daily: list[dict[str, Any]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """
    Format raw bars into the price_data dict expected by TechnicalAgent.

    Returns dict of timeframe → list of last 5 bars as {o, h, l, c, v}.
    """
    price_data: dict[str, list[dict[str, Any]]] = {}

    if bars_1min:
        price_data["1min"] = _last_n_bars(bars_1min, 10)

    if bars_5min:
        price_data["5min"] = _last_n_bars(bars_5min, 5)
    elif bars_1min and len(bars_1min) >= 5:
        # Derive 5-min bars from 1-min by aggregation
        agg = _aggregate_bars(bars_1min, 5)
        if agg:
            price_data["5min"] = _last_n_bars(agg, 5)

    if bars_daily:
        price_data["daily"] = _last_n_bars(bars_daily, 5)

    return price_data


# ── RSI ──────────────────────────────────────────────────────────────


def _compute_rsi(closes: list[float], period: int = 14) -> float | None:
    """Compute RSI using Wilder's smoothing (exponential moving average of gains/losses)."""
    if len(closes) < period + 1:
        return None

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    # Initial average
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # Wilder's smoothing
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss < 1e-10:  # D150: Epsilon — float precision on perfect uptrends
        return 100.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


# ── MACD ─────────────────────────────────────────────────────────────


def _compute_macd(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> dict[str, float] | None:
    """Compute MACD line, signal line, and histogram."""
    if len(closes) < slow + signal_period:
        return None

    ema_fast = _compute_ema_series(closes, fast)
    ema_slow = _compute_ema_series(closes, slow)

    if ema_fast is None or ema_slow is None:
        return None

    # MACD line = EMA(fast) - EMA(slow)
    # Align by using the tail of the longer series
    offset = slow - fast
    macd_line = [
        ema_fast[i + offset] - ema_slow[i]
        for i in range(len(ema_slow))
    ]

    # Signal line = EMA of MACD line
    signal_line = _compute_ema_series(macd_line, signal_period)
    if signal_line is None:
        return None

    macd_val = macd_line[-1]
    signal_val = signal_line[-1]

    return {
        "macd": macd_val,
        "signal": signal_val,
        "histogram": macd_val - signal_val,
    }


# ── Bollinger Bands ──────────────────────────────────────────────────


def _compute_bollinger_bands(
    closes: list[float],
    period: int = 20,
    num_std: float = 2.0,
) -> dict[str, float] | None:
    """Compute Bollinger Bands (middle, upper, lower) and %B position."""
    if len(closes) < period:
        return None

    window = closes[-period:]
    middle = sum(window) / period
    variance = sum((x - middle) ** 2 for x in window) / period
    std = math.sqrt(variance)

    upper = middle + num_std * std
    lower = middle - num_std * std

    current = closes[-1]
    width_pct = ((upper - lower) / middle * 100) if middle > 0 else 0
    position = ((current - lower) / (upper - lower)) if (upper - lower) > 0 else 0.5

    return {
        "upper": upper,
        "middle": middle,
        "lower": lower,
        "width_pct": width_pct,
        "position": position,
    }


# ── Support / Resistance ─────────────────────────────────────────────


def _compute_support_resistance(
    highs: list[float],
    lows: list[float],
    closes: list[float],
) -> dict[str, float] | None:
    """
    Compute pivot-based support and resistance.

    Uses standard floor pivot: Pivot = (H + L + C) / 3
    Support = 2 * Pivot - H
    Resistance = 2 * Pivot - L
    """
    if not highs or not lows or not closes:
        return None

    # Use recent session high/low/close
    recent_high = max(highs[-20:]) if len(highs) >= 20 else max(highs)
    recent_low = min(lows[-20:]) if len(lows) >= 20 else min(lows)
    last_close = closes[-1]

    pivot = (recent_high + recent_low + last_close) / 3
    support = 2 * pivot - recent_high
    resistance = 2 * pivot - recent_low

    return {
        "pivot": pivot,
        "support": support,
        "resistance": resistance,
    }


# ── VWAP ─────────────────────────────────────────────────────────────


def _compute_vwap(bars: list[dict[str, Any]]) -> float | None:
    """Compute VWAP from intraday bars: sum(typical_price * volume) / sum(volume)."""
    total_tp_vol = 0.0
    total_vol = 0

    for bar in bars:
        bar_h = bar.get("h", 0)
        bar_l = bar.get("l", 0)
        bar_c = bar.get("c", 0)
        bar_v = bar.get("v", 0)

        if bar_h <= 0 or bar_l <= 0 or bar_c <= 0 or bar_v <= 0:
            continue

        typical_price = (bar_h + bar_l + bar_c) / 3
        total_tp_vol += typical_price * bar_v
        total_vol += bar_v

    if total_vol == 0:
        return None

    return total_tp_vol / total_vol


# ── EMA Helpers ──────────────────────────────────────────────────────


def _compute_ema(values: list[float], period: int) -> float | None:
    """Compute the latest EMA value for a series."""
    series = _compute_ema_series(values, period)
    return series[-1] if series else None


def _compute_ema_series(values: list[float], period: int) -> list[float] | None:
    """Compute full EMA series using standard multiplier."""
    if len(values) < period:
        return None

    multiplier = 2.0 / (period + 1)
    ema = [sum(values[:period]) / period]

    for val in values[period:]:
        ema.append((val - ema[-1]) * multiplier + ema[-1])

    return ema


# ── Bar Helpers ──────────────────────────────────────────────────────


def _last_n_bars(bars: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    """Extract last N bars in {o, h, l, c, v} format."""
    result = []
    for bar in bars[-n:]:
        result.append({
            "o": bar.get("o", bar.get("open", 0)),
            "h": bar.get("h", bar.get("high", 0)),
            "l": bar.get("l", bar.get("low", 0)),
            "c": bar.get("c", bar.get("close", 0)),
            "v": bar.get("v", bar.get("volume", 0)),
        })
    return result


def _aggregate_bars(
    bars: list[dict[str, Any]],
    factor: int,
) -> list[dict[str, Any]]:
    """Aggregate N 1-min bars into larger timeframe bars."""
    result = []
    for i in range(0, len(bars) - factor + 1, factor):
        chunk = bars[i : i + factor]
        agg = {
            "o": chunk[0].get("o", chunk[0].get("open", 0)),
            "h": max(b.get("h", b.get("high", 0)) for b in chunk),
            "l": min(b.get("l", b.get("low", 0)) for b in chunk if b.get("l", b.get("low", 0)) > 0) if any(b.get("l", b.get("low", 0)) > 0 for b in chunk) else 0,
            "c": chunk[-1].get("c", chunk[-1].get("close", 0)),
            "v": sum(b.get("v", b.get("volume", 0)) for b in chunk),
        }
        result.append(agg)
    return result
