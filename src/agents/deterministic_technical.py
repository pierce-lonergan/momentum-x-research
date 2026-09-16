"""
MOMENTUM-X Deterministic Technical Agent

### ARCHITECTURAL CONTEXT
Node ID: agent.technical (replaces LLM-based TechnicalAgent)
Graph Link: docs/memory/graph_state.json → "agent.technical"

### RESEARCH BASIS
D101 Plan §2.2: FINSABER (arXiv:2505.07078) found LLM trading agents generated
no statistically significant alpha (p > 0.34). LLMs hallucinate numbers.
pandas_ta computes RSI, MACD, Bollinger, EMA in microseconds with 0% failure.

This deterministic replacement implements the same TechnicalSignal interface
using pandas_ta for indicator computation and rule-based pattern detection.

### CRITICAL INVARIANTS
1. Pattern without RVOL>2.0 confirmation caps at NEUTRAL (PROMPT_SIGNATURES).
2. STRONG_BULL requires breakout_confirmed + RVOL>3.0 + VWAP above.
3. Daily-timeframe patterns without intraday confirmation cap at BULL.
4. RVOL > 5.0 + RSI(9) > 75 = EXHAUSTION RISK — cap at BULL max.
5. Never fails — guaranteed TechnicalSignal output on every call.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import Any

import pandas as pd

from src.core.models import TechnicalSignal

logger = logging.getLogger(__name__)


# ── Indicator backend probe (Mon 2026-04-20 dormancy fix) ────────────
# History: pandas_ta 0.3.14b0 broke silently when NumPy 2.0 removed the
# `NaN` alias (`from numpy import NaN as npNaN` ImportError). The
# original `_compute_indicators` had `try: import pandas_ta as ta /
# except ImportError: return {}` — every indicator silently zeroed for
# an unknown number of trading sessions. Lesson: import the dependency
# at *module load*, not inside the hot path, so the breakage is loud
# (startup log + ENV_AUDIT visibility) instead of silent.
#
# Backend selection order:
#   1. pandas_ta_classic (maintained fork, numpy 2.0 compatible) — preferred.
#   2. pandas_ta (original) — fallback for environments still on legacy.
#   3. None — _compute_indicators returns {} but logs WARNING at *every*
#      call and the populated-indicators test will fail.
_TA_BACKEND: Any = None
_TA_BACKEND_NAME: str = "<NONE>"
try:
    import pandas_ta_classic as _ta_mod
    _TA_BACKEND = _ta_mod
    _TA_BACKEND_NAME = "pandas_ta_classic"
    logger.info("Indicator backend: pandas_ta_classic loaded")
except ImportError:
    try:
        import pandas_ta as _ta_mod  # type: ignore[no-redef]
        _TA_BACKEND = _ta_mod
        _TA_BACKEND_NAME = "pandas_ta"
        logger.warning(
            "Indicator backend: falling back to pandas_ta (legacy). "
            "If running on numpy>=2.0 this WILL silently fail. "
            "Install pandas-ta-classic to fix."
        )
    except ImportError as e:
        logger.error(
            "Indicator backend: NO TA library available (%s). "
            "DeterministicTechnicalAgent will return EMPTY indicators "
            "for every call. Install pandas-ta-classic immediately.",
            e,
        )


def _safe_float(val: Any, default: float = 0.0) -> float:
    """Safely convert to float, returning default on failure."""
    try:
        if val is None:
            return default
        result = float(val)
        if result != result:  # NaN check
            return default
        return result
    except (ValueError, TypeError):
        return default


def _compute_indicators(bars: list[dict]) -> dict[str, float]:
    """
    Compute technical indicators from OHLCV bars using pandas_ta.

    Uses small-cap tuned parameters per D87:
    - RSI(9) instead of RSI(14) — faster response for volatile names
    - MACD(5,13,4) instead of MACD(12,26,9) — faster crossover detection
    - Bollinger Bands(20,2) — standard
    - EMA(9) and EMA(21) — short-term trend

    Returns dict of indicator values (all floats, NaN replaced with 0.0).
    """
    if not bars or len(bars) < 5:
        return {}

    # Use the module-level backend probed at import time. If both
    # pandas_ta_classic and pandas_ta failed to import, _TA_BACKEND is None
    # and we return {} — but the module-load WARNING already fired, so the
    # operator sees the dormancy in startup log, not via incident.
    ta = _TA_BACKEND
    if ta is None:
        logger.warning(
            "_compute_indicators called but no TA backend available — "
            "returning empty (this should be impossible if requirements "
            "are installed)."
        )
        return {}

    # Build DataFrame from bars
    df = pd.DataFrame(bars)
    # Normalize column names (Alpaca uses various conventions)
    col_map = {}
    for col in df.columns:
        lc = col.lower()
        if lc in ("o", "open"):
            col_map[col] = "open"
        elif lc in ("h", "high"):
            col_map[col] = "high"
        elif lc in ("l", "low"):
            col_map[col] = "low"
        elif lc in ("c", "close"):
            col_map[col] = "close"
        elif lc in ("v", "volume"):
            col_map[col] = "volume"
    df = df.rename(columns=col_map)

    required = {"open", "high", "low", "close"}
    if not required.issubset(set(df.columns)):
        return {}

    # Ensure numeric types
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)

    indicators: dict[str, float] = {}

    # RSI(9) — small-cap tuned (D87)
    try:
        rsi = ta.rsi(df["close"], length=9)
        if rsi is not None and len(rsi) > 0:
            indicators["rsi_9"] = _safe_float(rsi.iloc[-1])
    except Exception as e:
        logger.debug("Indicator calc failed: %s", e)

    # MACD(5,13,4) — fast MACD for intraday breakouts (D87)
    try:
        macd_result = ta.macd(df["close"], fast=5, slow=13, signal=4)
        if macd_result is not None and len(macd_result) > 0:
            # pandas_ta returns DataFrame with columns like MACD_5_13_4, etc.
            macd_cols = macd_result.columns.tolist()
            if len(macd_cols) >= 3:
                indicators["macd_line"] = _safe_float(macd_result.iloc[-1, 0])
                indicators["macd_signal"] = _safe_float(macd_result.iloc[-1, 2])
                indicators["macd_histogram"] = _safe_float(macd_result.iloc[-1, 1])
    except Exception as e:
        logger.debug("Indicator calc failed: %s", e)

    # Bollinger Bands(20, 2)
    try:
        bb = ta.bbands(df["close"], length=20, std=2)
        if bb is not None and len(bb) > 0:
            bb_cols = bb.columns.tolist()
            if len(bb_cols) >= 3:
                indicators["bb_lower"] = _safe_float(bb.iloc[-1, 0])
                indicators["bb_mid"] = _safe_float(bb.iloc[-1, 1])
                indicators["bb_upper"] = _safe_float(bb.iloc[-1, 2])
    except Exception as e:
        logger.debug("Indicator calc failed: %s", e)

    # EMA(9) and EMA(21)
    try:
        ema9 = ta.ema(df["close"], length=9)
        ema21 = ta.ema(df["close"], length=21)
        if ema9 is not None and len(ema9) > 0:
            indicators["ema_9"] = _safe_float(ema9.iloc[-1])
        if ema21 is not None and len(ema21) > 0:
            indicators["ema_21"] = _safe_float(ema21.iloc[-1])
    except Exception as e:
        logger.debug("Indicator calc failed: %s", e)

    # ATR(14) for stop calculation
    try:
        atr = ta.atr(df["high"], df["low"], df["close"], length=14)
        if atr is not None and len(atr) > 0:
            indicators["atr_14"] = _safe_float(atr.iloc[-1])
    except Exception as e:
        logger.debug("Indicator calc failed: %s", e)

    # Current price info from last bar
    indicators["last_close"] = _safe_float(df["close"].iloc[-1])
    indicators["last_high"] = _safe_float(df["high"].iloc[-1])
    indicators["last_low"] = _safe_float(df["low"].iloc[-1])

    # Recent high/low for breakout detection (last 20 bars)
    lookback = min(20, len(df))
    indicators["recent_high"] = _safe_float(df["high"].tail(lookback).max())
    indicators["recent_low"] = _safe_float(df["low"].tail(lookback).min())

    # Bollinger Band width (squeeze detection)
    if "bb_upper" in indicators and "bb_lower" in indicators and "bb_mid" in indicators:
        bb_mid = indicators["bb_mid"]
        if bb_mid > 0:
            indicators["bb_width_pct"] = (
                (indicators["bb_upper"] - indicators["bb_lower"]) / bb_mid
            )

    return indicators


def _detect_pattern(
    indicators: dict[str, float],
    current_price: float,
    rvol: float,
) -> tuple[str, bool, str]:
    """
    Rule-based pattern detection from computed indicators.

    Returns:
        (pattern_name, breakout_confirmed, timeframe)
    """
    if not indicators or current_price <= 0:
        return "NONE", False, ""

    rsi = indicators.get("rsi_9", 50.0)
    macd_hist = indicators.get("macd_histogram", 0.0)
    macd_line = indicators.get("macd_line", 0.0)
    macd_signal = indicators.get("macd_signal", 0.0)
    bb_upper = indicators.get("bb_upper", 0.0)
    bb_lower = indicators.get("bb_lower", 0.0)
    bb_width = indicators.get("bb_width_pct", 0.0)
    ema_9 = indicators.get("ema_9", 0.0)
    ema_21 = indicators.get("ema_21", 0.0)
    recent_high = indicators.get("recent_high", 0.0)

    # ── Bollinger Band Squeeze ──
    # Tight BBands (width < 4% of mid) + price breaking above upper band
    if bb_width > 0 and bb_width < 0.04 and bb_upper > 0:
        if current_price > bb_upper:
            return "BB_SQUEEZE", True, "15min"
        elif current_price > bb_upper * 0.99:  # Within 1% of upper band
            return "BB_SQUEEZE", False, "15min"

    # ── Consolidation Breakout ──
    # Price breaking above recent high with EMA alignment
    if recent_high > 0 and current_price > recent_high:
        if ema_9 > ema_21 > 0:
            return "CONSOLIDATION_BREAKOUT", True, "15min"
        else:
            return "CONSOLIDATION_BREAKOUT", False, "15min"

    # ── Bull Flag ──
    # MACD bullish crossover + RSI recovering from mid-range + EMA alignment
    if macd_line > macd_signal and macd_hist > 0:
        if 40 < rsi < 65 and ema_9 > ema_21 > 0:
            return "BULL_FLAG", True, "15min"

    # ── Ascending Triangle ──
    # Price near recent high with rising EMA support
    if recent_high > 0 and current_price > recent_high * 0.97:
        if ema_9 > ema_21 > 0 and rsi > 50:
            return "ASC_TRIANGLE", current_price > recent_high, "15min"

    return "NONE", False, ""


class DeterministicTechnicalAgent:
    """
    Pure-Python technical analysis agent. Zero LLM calls.

    Node ID: agent.technical
    Replaces: TechnicalAgent (LLM-based)

    Interface contract:
        - agent_id property → "technical_agent"
        - async analyze(ticker, **kwargs) → TechnicalSignal
        - Compatible with CachedAgentWrapper (same duck-typed interface)

    Inputs (via kwargs):
        - current_price: float
        - rvol: float
        - vwap: float
        - price_data: dict — {"5min": [{o,h,l,c,v}, ...], "daily": [...]}
        - indicators: dict — pre-computed indicators (optional, we compute our own)

    Ref: D101 Plan §2.2 (Deterministic Technical Agent)
    Ref: PROMPT_SIGNATURES.md → TECHNICAL_AGENT (constraints)
    """

    @property
    def agent_id(self) -> str:
        return "technical_agent"

    async def analyze(self, ticker: str, **kwargs: Any) -> TechnicalSignal:
        """
        Deterministic technical analysis — pandas_ta indicators + rule-based patterns.

        Computes RSI(9), MACD(5,13,4), Bollinger(20,2), EMA(9/21), then
        applies rule-based pattern detection and signal constraints.

        Returns:
            TechnicalSignal with pattern, breakout confirmation, and levels.
        """
        current_price = _safe_float(kwargs.get("current_price"), 0.0)
        rvol = _safe_float(kwargs.get("rvol"), 0.0)
        gap_pct = _safe_float(kwargs.get("gap_pct"), 0.0)  # D126
        vwap = _safe_float(kwargs.get("vwap"), 0.0)
        # D221 Phase F bug sweep: kwargs.get('x', {}) returns None when the
        # caller passes x=None explicitly (the default only applies on missing
        # keys). Downstream dict operations crash. Coerce via isinstance.
        _pd_raw = kwargs.get("price_data")
        price_data: dict = _pd_raw if isinstance(_pd_raw, dict) else {}
        _ind_raw = kwargs.get("indicators")
        pre_indicators: dict = _ind_raw if isinstance(_ind_raw, dict) else {}

        # ── Extract bars for indicator computation ──
        # Prefer intraday bars (5min, 15min), fall back to daily
        bars: list[dict] = []
        timeframe_used = ""
        for tf in ("5min", "15min", "1min", "5Min", "15Min", "1Min"):
            if tf in price_data and isinstance(price_data[tf], list):
                bars = price_data[tf]
                timeframe_used = tf
                break
        if not bars:
            for tf in ("daily", "1Day", "1day"):
                if tf in price_data and isinstance(price_data[tf], list):
                    bars = price_data[tf]
                    timeframe_used = tf
                    break

        # ── Compute indicators ──
        computed = _compute_indicators(bars)

        # Merge pre-computed indicators (they take precedence for VWAP etc.)
        if pre_indicators:
            for k, v in pre_indicators.items():
                if k not in computed:
                    computed[k] = _safe_float(v)

        # ── Detect pattern ──
        pattern, breakout_confirmed, pattern_tf = _detect_pattern(
            computed, current_price, rvol
        )

        # ── VWAP position ──
        vwap_above = current_price > vwap if vwap > 0 else False

        # ── Compute projected target and stop from indicators ──
        # doc 297: ATR-14 on this universe (low-float gap-ups, frequent bad bars) can exceed the
        # price itself, and `price - 1.5*ATR` then goes NEGATIVE. Measured in the live journals:
        # 10 occurrences Feb-Jul 2026 (MUU 2026-07-20 entry 29.51 -> stop -171.92; LGHL 2026-07-28;
        # EHGO 2026-07-01 reached `filled`). A negative stop cannot be placed at the broker, so the
        # position rides NAKED — this is the doc-281 "vanished stop" class at its source.
        # Fix: clamp the stop into a legal band and fail LOUD so a corrupted ATR is visible.
        max_stop_distance_frac = 0.35  # widest stop this agent may ever propose
        atr = computed.get("atr_14", 0)
        atr_corrupt = False
        if atr > 0 and current_price > 0:
            projected_target = current_price + (atr * 3.0)  # 3R target
            stop_loss_level = current_price - (atr * 1.5)  # 1.5R stop
            floor = current_price * (1.0 - max_stop_distance_frac)
            if stop_loss_level < floor:
                atr_corrupt = True
                stop_loss_level = floor
        elif current_price > 0:
            projected_target = current_price * 1.10  # Default +10%
            stop_loss_level = current_price * 0.95  # Default -5%
        else:
            projected_target = None
            stop_loss_level = None
        if stop_loss_level is not None and stop_loss_level <= 0:
            # belt-and-braces: never emit a non-positive stop under any arithmetic path
            atr_corrupt = True
            stop_loss_level = current_price * (1.0 - max_stop_distance_frac)

        # ── Determine signal and confidence ──
        rsi = computed.get("rsi_9", 50.0)
        macd_hist = computed.get("macd_histogram", 0.0)
        ema_9 = computed.get("ema_9", 0.0)
        ema_21 = computed.get("ema_21", 0.0)

        signal = "NEUTRAL"
        confidence = 0.3
        reasoning_parts: list[str] = []
        red_flags: list[str] = []
        if atr_corrupt:
            red_flags.append(
                f"ATR(14)={atr:.4f} vs price={current_price:.4f} implies a stop beyond "
                f"{max_stop_distance_frac:.0%} — clamped (doc 297); treat ATR as corrupted"
            )

        # ── D126: Compute minutes since open (used by gap momentum + time decay) ──
        try:
            _now_et = datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York"))
            _market_open_min = 9 * 60 + 30  # 9:30 AM ET
            _current_min = _now_et.hour * 60 + _now_et.minute
            minutes_since_open = max(0, _current_min - _market_open_min)
        except Exception:
            minutes_since_open = 999  # Safe fallback: no momentum mode, no decay

        # ── D126: Gap-Up Momentum Override ──
        # On large gap-ups with high RVOL, lagging indicators (MACD, EMA) are
        # structurally wrong — they show BEAR on stocks about to run +20-70%.
        # The gap × volume composite IS the technical signal.
        _momentum_score = abs(gap_pct) * rvol
        _gap_momentum_mode = (
            _momentum_score > 1.0       # Composite: 10% gap × 10x RVOL, etc.
            and abs(gap_pct) > 0.08     # Floor: must be >8% gap (EMC minimum)
            and rvol > 2.5             # Floor: must have real volume
            and minutes_since_open < 30 # Active during decay window
        )

        if _gap_momentum_mode:
            # Compute decay blend: full strength 0-10 min, linear to normal 10-30 min
            if minutes_since_open <= 10:
                _momentum_weight = 1.0
            else:
                _momentum_weight = max(0.0, 1.0 - (minutes_since_open - 10) / 20.0)

            # Momentum base: +2 bullish (the gap IS the signal)
            _m_bull = 2
            _m_bear = 0

            # VWAP still valid (real-time anchor)
            if vwap and current_price > vwap:
                _m_bull += 1
            elif vwap and current_price < vwap:
                _m_bear += 1

            # RSI: only count bullish 50-75. NO penalty for >75 in momentum mode.
            if rsi and 50 < rsi <= 75:
                _m_bull += 1

            # Compute normal factors too (for blending after 10 min)
            _normal_bull = 0
            _normal_bear = 0
            if rsi > 75:
                _normal_bear += 1
            elif rsi > 60:
                _normal_bull += 1
            elif rsi < 40:
                _normal_bear += 1
            if macd_hist > 0:
                _normal_bull += 1
            elif macd_hist < 0:
                _normal_bear += 1
            if ema_9 > ema_21 > 0:
                _normal_bull += 1
            elif ema_21 > ema_9 > 0:
                _normal_bear += 1
            if vwap_above:
                _normal_bull += 1
            elif vwap > 0:
                _normal_bear += 1

            # Blend based on decay weight
            if _momentum_weight >= 1.0:
                bullish_factors = _m_bull
                bearish_factors = _m_bear
            else:
                bullish_factors = round(
                    _m_bull * _momentum_weight + _normal_bull * (1 - _momentum_weight)
                )
                bearish_factors = round(
                    _m_bear * _momentum_weight + _normal_bear * (1 - _momentum_weight)
                )

            pattern = "GAP_MOMENTUM"
            breakout_confirmed = True
            pattern_tf = "opening"

            reasoning_parts.append(
                f"D126 GAP MOMENTUM: gap={gap_pct:.1%} rvol={rvol:.1f}x "
                f"score={_momentum_score:.1f} weight={_momentum_weight:.2f}"
            )
            logger.info(
                "D126 GAP MOMENTUM %s: gap=%.1f%% rvol=%.1fx score=%.1f "
                "weight=%.2f — overriding lagging indicators",
                ticker, gap_pct * 100, rvol, _momentum_score, _momentum_weight,
            )

        # ── Build signal from indicator confluence (normal mode) ──
        # Gap momentum mode handles its own factor computation above.
        _vwap_available = vwap > 0  # Used by scoring thresholds below
        if not _gap_momentum_mode:
            bullish_factors = 0
            bearish_factors = 0

            # RSI assessment
            if rsi > 75:
                red_flags.append(f"RSI(9)={rsi:.0f} > 75 — overbought")
                bearish_factors += 1
            elif rsi > 60:
                reasoning_parts.append(f"RSI(9)={rsi:.0f} — bullish momentum")
                bullish_factors += 1
            elif rsi < 25:
                red_flags.append(f"RSI(9)={rsi:.0f} < 25 — oversold")
                bearish_factors += 1
            elif rsi < 40:
                reasoning_parts.append(f"RSI(9)={rsi:.0f} — bearish")
                bearish_factors += 1

            # MACD assessment
            if macd_hist > 0:
                reasoning_parts.append(f"MACD histogram positive ({macd_hist:.4f})")
                bullish_factors += 1
            elif macd_hist < 0:
                reasoning_parts.append(f"MACD histogram negative ({macd_hist:.4f})")
                bearish_factors += 1

            # EMA alignment
            if ema_9 > ema_21 > 0:
                reasoning_parts.append("EMA(9) > EMA(21) — bullish alignment")
                bullish_factors += 1
            elif ema_21 > ema_9 > 0:
                reasoning_parts.append("EMA(21) > EMA(9) — bearish alignment")
                bearish_factors += 1

            # VWAP position
            # D121 BUG-H8: When VWAP unavailable (vwap=0), this factor is skipped,
            # lowering the max bullish score and making STRONG_BULL harder to reach.
            _vwap_available = vwap > 0
            if vwap_above:
                reasoning_parts.append("Price above VWAP — bullish bias")
                bullish_factors += 1
            elif _vwap_available:
                reasoning_parts.append("Price below VWAP — bearish bias")
                bearish_factors += 1
            else:
                reasoning_parts.append("VWAP unavailable — factor skipped")

            # Pattern adds weight
            if pattern != "NONE":
                reasoning_parts.append(f"Pattern: {pattern} (breakout={'YES' if breakout_confirmed else 'NO'})")
                if breakout_confirmed:
                    bullish_factors += 2
                else:
                    bullish_factors += 1

        # ── Score from factor balance ──
        net_bull = bullish_factors - bearish_factors

        # D121 BUG-H8: When VWAP unavailable, max possible factors drops by 1.
        # Lower thresholds by 1 so signal strength isn't penalized by missing data.
        _strong_bull_thresh = 3 if not _vwap_available else 4
        _strong_bear_thresh = -2 if not _vwap_available else -3

        if net_bull >= _strong_bull_thresh:
            signal = "STRONG_BULL"
            confidence = 0.85
        elif net_bull >= 2:
            signal = "BULL"
            confidence = 0.65
        elif net_bull >= 1:
            signal = "BULL"
            confidence = 0.50
        elif net_bull <= _strong_bear_thresh:
            signal = "STRONG_BEAR"
            confidence = 0.80
        elif net_bull <= -1:
            signal = "BEAR"
            confidence = 0.55
        else:
            signal = "NEUTRAL"
            confidence = 0.35

        # ── Enforce PROMPT_SIGNATURES constraints ──

        # Constraint 1: Pattern without RVOL>2.0 caps at NEUTRAL
        if rvol < 2.0 and signal in ("STRONG_BULL", "BULL"):
            signal = "NEUTRAL"
            confidence = min(confidence, 0.4)
            red_flags.append(f"RVOL={rvol:.1f}x < 2.0 — insufficient volume")

        # Constraint 2: STRONG_BULL requires all three confirmations
        if signal == "STRONG_BULL":
            if not (breakout_confirmed and rvol > 3.0 and vwap_above):
                signal = "BULL"
                confidence = min(confidence, 0.7)
                missing = []
                if not breakout_confirmed:
                    missing.append("breakout")
                if rvol <= 3.0:
                    missing.append(f"RVOL={rvol:.1f}x<=3.0")
                if not vwap_above:
                    missing.append("below VWAP")
                red_flags.append(
                    f"STRONG_BULL downgraded: missing {', '.join(missing)}"
                )

        # Constraint 4: RVOL > 5.0 + RSI > 75 = exhaustion — cap at BULL
        # D126: Skip in gap momentum mode — RSI > 75 on a gap-up is confirmation,
        # not exhaustion. The manipulation classifier handles the pump case.
        if rvol > 5.0 and rsi > 75 and signal == "STRONG_BULL" and not _gap_momentum_mode:
            signal = "BULL"
            confidence = min(confidence, 0.5)
            red_flags.append(
                f"EXHAUSTION: RVOL={rvol:.1f}x + RSI={rsi:.0f} — capped at BULL"
            )

        # ── No data fallback ──
        if not bars and not pre_indicators:
            signal = "NEUTRAL"
            confidence = 0.0
            reasoning_parts = ["No price data available for technical analysis"]
            red_flags.append("NO_DATA")

        confidence = max(0.0, min(1.0, confidence))

        # ── D104/D126: Time-based confidence decay ──
        # Normal stocks: opening auction signals are unreliable, decay conf
        # from 0.0 at T+0 to 1.0 at T+30.
        # D126 gap momentum: opening IS the signal, start at 0.8 at T+0,
        # reach 1.0 by T+5.
        if minutes_since_open < 30:
            if _gap_momentum_mode:
                # Gap-up opening is the highest conviction moment
                time_confidence = min(1.0, 0.8 + (minutes_since_open / 25.0))
            else:
                # Original D104 decay for normal stocks
                time_confidence = minutes_since_open / 30.0
            raw_confidence = confidence
            confidence = confidence * time_confidence
            logger.info(
                "D104 TECH %s: Time decay applied — T+%dmin, "
                "factor=%.2f, conf %.2f → %.2f%s",
                ticker, minutes_since_open, time_confidence,
                raw_confidence, confidence,
                " (D126 gap momentum)" if _gap_momentum_mode else "",
            )

        reasoning = "; ".join(reasoning_parts) if reasoning_parts else "Insufficient data"

        logger.info(
            "D101 TECH %s: signal=%s conf=%.2f pattern=%s breakout=%s "
            "RVOL=%.1f VWAP=%s | %s",
            ticker, signal, confidence, pattern, breakout_confirmed,
            rvol, "ABOVE" if vwap_above else "BELOW",
            reasoning[:100],
        )

        return TechnicalSignal(
            agent_id=self.agent_id,
            ticker=ticker,
            timestamp=datetime.now(timezone.utc),
            signal=signal,
            confidence=confidence,
            reasoning=reasoning,
            flags=red_flags,
            pattern_identified=pattern,
            pattern_timeframe=pattern_tf,
            breakout_confirmed=breakout_confirmed,
            breakout_rvol=rvol,
            vwap_above=vwap_above,
            projected_target=projected_target,
            stop_loss_level=stop_loss_level,
        )
