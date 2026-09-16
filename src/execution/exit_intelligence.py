"""
MOMENTUM-X Smart Exit Intelligence (D78)

### ARCHITECTURAL CONTEXT
Node ID: execution.exit_intelligence
Graph Link: docs/memory/graph_state.json → "execution.exit_intelligence"

### PURPOSE
Detects institutional distribution patterns and momentum degradation to
exit positions BEFORE the dump. On low-float momentum stocks, "big fish"
(institutions, market makers) accumulate pre-market, let retail FOMO push
price +15-30%, then distribute. This module identifies the distribution.

### 4-TIER EXIT INTELLIGENCE
- Tier 0 (Free/Instant): D63 trailing stops — wired into Phase 3
- Tier 1 (Free/Fast): Numeric exit signals — no LLM, $0, <50ms
- Tier 2 (Free): Adaptive target adjustment — ATR/time/volume based
- Tier 3 (Expensive/Rare): LLM exit advisor — opt-in, off by default

### DESIGN DECISIONS
- Pure numeric computation (no LLM calls) for Tier 0-2
- Composite score 0-1 maps to HOLD / TIGHTEN / EXIT recommendation
- EXIT action: cancel limits + market sell (don't wait for targets)
- All signals normalized to [0, 1] for consistent weighting

### CRITICAL INVARIANTS
1. Never increases position size (exit-only logic)
2. Composite threshold ≥ 0.6 triggers market sell
3. All exit actions recorded in trade journal with reason + signal scores
4. Stop ratcheting invariant preserved: stops only move UP

Ref: D78, D63 (trailing stops), MOMENTUM_LOGIC.md §13
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from src.execution.exit_strategies import ParallelExitEngine

logger = logging.getLogger(__name__)


# ── D107: Per-Cycle Exit Signal History Logger ───────────────────────
# Writes all 13 exit signal values per position per cycle to daily JSONL
# files. This data is critical for post-session analysis: which signals
# actually fire? At what intensity? Do they correlate with adverse moves?
# Fire-and-forget: write failures are logged at DEBUG and never affect trading.


class SignalHistoryLogger:
    """
    D107 WS1: Logs all exit signal values per position per cycle to JSONL.

    Each line captures the full signal state for one position at one point
    in time, enabling post-session signal efficacy analysis.

    Daily file rotation: signal_log_{YYYY-MM-DD}.jsonl
    """

    def __init__(self, signal_history_dir: str = "data/signal_history") -> None:
        self._dir = Path(signal_history_dir)
        self._current_date: str = ""
        self._file = None
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.debug("D107: Could not create signal history dir %s: %s", self._dir, e)

    def _ensure_file(self) -> bool:
        """Open/rotate daily JSONL file. Returns True if file is ready."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._current_date or self._file is None:
            self.close()
            self._current_date = today
            filepath = self._dir / f"signal_log_{today}.jsonl"
            try:
                self._file = open(filepath, "a", encoding="utf-8")  # noqa: SIM115
                return True
            except Exception as e:
                logger.debug("D107: Could not open signal log %s: %s", filepath, e)
                self._file = None
                return False
        return self._file is not None

    def log_cycle(
        self,
        ticker: str,
        signal: "ExitSignal",
        current_price: float,
        entry_price: float,
        pnl_pct: float,
        # D109 Phase 4: Extended metrics (all optional for backward compat)
        parallel_log: dict | None = None,
        intraday_atr: float | None = None,
        mfe_pct: float | None = None,
        time_held_min: float | None = None,
    ) -> None:
        """
        Log one cycle's signal state for one position.

        Args:
            ticker: Position ticker symbol
            signal: ExitSignal dataclass with all 13 signal scores
            current_price: Current market price
            entry_price: Position entry price
            pnl_pct: Current P&L percentage
            parallel_log: D109 nested strategy results from ParallelExitEngine.to_log_dict()
            intraday_atr: D109 ATR computed from minute bars
            mfe_pct: D109 max favorable excursion since entry
            time_held_min: D109 minutes since position opened
        """
        try:
            if not self._ensure_file():
                return
            scores = signal.to_scores_dict()
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "ticker": ticker,
                "composite": scores.get("composite", 0.0),
                "recommendation": signal.recommendation,
                "price": round(current_price, 4),
                "entry_price": round(entry_price, 4),
                "pnl_pct": round(pnl_pct, 4),
                # All 13 individual signals
                "volume_fade": scores.get("volume_fade", 0.0),
                "vwap_deterioration": scores.get("vwap_deterioration", 0.0),
                "spread_widening": scores.get("spread_widening", 0.0),
                "time_decay": scores.get("time_decay", 0.0),
                "distribution": scores.get("distribution", 0.0),
                "resistance_proximity": scores.get("resistance_proximity", 0.0),
                "failed_breakout": scores.get("failed_breakout", 0.0),
                "churning": scores.get("churning", 0.0),
                "obv_divergence": scores.get("obv_divergence", 0.0),
                "volume_climax": scores.get("volume_climax", 0.0),
                "momentum_degradation": scores.get("momentum_degradation", 0.0),
                "flow_toxicity": scores.get("flow_toxicity", 0.0),
                "distribution_detector": scores.get("distribution_detector", 0.0),
            }
            # D109 Phase 4: Parallel strategy metrics
            if parallel_log is not None:
                entry.update(parallel_log)
            if intraday_atr is not None:
                entry["intraday_atr"] = round(intraday_atr, 6)
            if mfe_pct is not None:
                entry["mfe_pct"] = round(mfe_pct, 4)
            if time_held_min is not None:
                entry["time_held_min"] = round(time_held_min, 1)

            self._file.write(json.dumps(entry) + "\n")
            self._file.flush()
        except Exception as e:
            logger.debug("D107: Signal log write failed: %s", e)

    def close(self) -> None:
        """Flush and close the current file handle."""
        if self._file is not None:
            try:
                self._file.flush()
                self._file.close()
            except Exception:
                pass
            self._file = None


# ── D100: Module-level ATR computation ───────────────────────────────
# Extracted for reuse in orchestrator (ATR-based initial stops) without
# needing to instantiate ExitIntelligenceManager.

def compute_atr(bars: list[dict], period: int = 14) -> float | None:
    """
    Compute Average True Range from bar history (daily or minute bars).

    ATR uses the max of: (high-low), |high-prev_close|, |low-prev_close|
    for each bar. Returns average of last `period` true ranges.

    Returns None if insufficient bars (need period+1 for prev_close).

    Args:
        bars: List of bar dicts with keys 'h', 'l', 'c' (high, low, close).
        period: ATR lookback period (default 14).

    Ref: D89b (Chandelier Exit), D100 (ATR-based initial stops)
    """
    if not bars or len(bars) < period + 1:
        return None

    trs: list[float] = []
    for i in range(1, len(bars)):
        h = float(bars[i].get("h", 0) or 0)
        l = float(bars[i].get("l", 0) or 0)
        prev_c = float(bars[i - 1].get("c", 0) or 0)
        if h > 0 and l >= 0 and h > l and prev_c > 0:
            trs.append(max(h - l, abs(h - prev_c), abs(l - prev_c)))

    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


# ── Exit Signal Weights ──────────────────────────────────────────────
# D89/D106: 13 signals (was 12). Weights sum to 1.0.
# D106: Added distribution_detector (0.15 weight, redistributed from
# distribution (0.08→0.00, lacks L2 data) and flow_toxicity (0.05→0.00,
# lacks tick data). These signals never had real data sources and always
# returned 0.0 — replacing them with DistributionDetector which uses
# actually available data (volume, spread, VWAP, time, filings).
EXIT_SIGNAL_WEIGHTS: dict[str, float] = {
    # Original signals (rebalanced for D106)
    "volume_fade": 0.12,
    "vwap_deterioration": 0.15,
    "spread_widening": 0.10,
    "time_decay": 0.10,
    "distribution": 0.00,  # D106: zeroed — lacks L2 data, replaced by distribution_detector
    "resistance_proximity": 0.05,
    "failed_breakout": 0.05,
    # D89: Multi-bar signals (rebalanced for D106)
    "churning": 0.10,
    "obv_divergence": 0.08,
    "volume_climax": 0.05,
    "momentum_degradation": 0.05,
    "flow_toxicity": 0.00,  # D106: zeroed — lacks tick data, replaced by distribution_detector
    # D106: Distribution detector — only non-zero for PROMOTIONAL_EARLY positions
    "distribution_detector": 0.15,
}


# ── Data Classes ─────────────────────────────────────────────────────


@dataclass
class ExitSignal:
    """Result of lightweight exit signal computation for a single position."""

    ticker: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # Numeric signals (all 0.0-1.0, higher = more urgent to exit)
    volume_fade: float = 0.0  # Current vol vs peak vol ratio
    vwap_deterioration: float = 0.0  # Price below VWAP = bad
    spread_widening: float = 0.0  # Current spread vs entry spread
    time_decay: float = 0.0  # Time-of-day urgency
    distribution: float = 0.0  # Large ask-side prints
    resistance_proximity: float = 0.0  # Near resistance = stalling
    failed_breakout: float = 0.0  # Repeated rejection at level

    # D89: Multi-bar signals (require bar history, default 0.0 for backward compat)
    churning: float = 0.0  # High vol + narrow range = institutional distribution
    obv_divergence: float = 0.0  # Price up but OBV down = bearish divergence
    volume_climax: float = 0.0  # Blow-off top exhaustion
    momentum_degradation: float = 0.0  # Consecutive lower lows = trend dying
    flow_toxicity: float = 0.0  # BVC/VPIN-lite informed trading detection

    # D106: Distribution detector — only non-zero for PROMOTIONAL_EARLY positions.
    # Fed by DistributionDetector composite score (7 weighted signals).
    distribution_detector: float = 0.0

    # Composite
    composite_exit_urgency: float = 0.0  # Weighted blend (0-1)

    # Recommendation
    recommendation: str = "HOLD"  # HOLD, TIGHTEN, EXIT
    reasoning: str = ""

    def to_scores_dict(self) -> dict[str, float]:
        """Export signal scores for trade journal."""
        return {
            "volume_fade": round(self.volume_fade, 3),
            "vwap_deterioration": round(self.vwap_deterioration, 3),
            "spread_widening": round(self.spread_widening, 3),
            "time_decay": round(self.time_decay, 3),
            "distribution": round(self.distribution, 3),
            "resistance_proximity": round(self.resistance_proximity, 3),
            "failed_breakout": round(self.failed_breakout, 3),
            # D89: Multi-bar signals
            "churning": round(self.churning, 3),
            "obv_divergence": round(self.obv_divergence, 3),
            "volume_climax": round(self.volume_climax, 3),
            "momentum_degradation": round(self.momentum_degradation, 3),
            "flow_toxicity": round(self.flow_toxicity, 3),
            # D106: Distribution detector signal
            "distribution_detector": round(self.distribution_detector, 3),
            "composite": round(self.composite_exit_urgency, 3),
        }


@dataclass
class ExitAction:
    """Recommended exit action for a single position."""

    ticker: str
    action: str  # "HOLD", "TIGHTEN", "EXIT"
    signal: ExitSignal
    new_stop: float | None = None
    exit_reason: str = ""


# ── Exit Signal Engine ───────────────────────────────────────────────


class ExitSignalEngine:
    """
    Pure numeric exit signal computation. No LLM calls.

    D78: Detects institutional distribution patterns on low-float
    momentum stocks. Runs every Phase 3 cycle (~60s). Cost: $0.
    """

    def __init__(
        self,
        tighten_threshold: float = 0.3,
        exit_threshold: float = 0.6,
        weights: dict[str, float] | None = None,
    ) -> None:
        self._tighten_threshold = tighten_threshold
        self._exit_threshold = exit_threshold
        # D120: merge partial overrides onto defaults (not replace)
        base = dict(EXIT_SIGNAL_WEIGHTS)
        if weights:
            base.update(weights)
        self._weights = base

    # ── Master computation ────────────────────────────────────────

    def compute_exit_signals(
        self,
        ticker: str,
        current_price: float,
        entry_price: float,
        # Snapshot data
        bid: float = 0.0,
        ask: float = 0.0,
        current_volume: int = 0,
        # Entry context
        entry_spread: float = 0.0,
        peak_volume: int = 0,
        # VWAP
        vwap: float = 0.0,
        # Time
        hour_et: int = 10,
        minute_et: int = 0,
        # Resistance (from technical agent at entry)
        resistance_level: float = 0.0,
        # Bid/ask size for distribution detection
        bid_size: int = 0,
        ask_size: int = 0,
        # D89: Multi-bar history for advanced signals
        bars: list[dict] | None = None,
        # D106: Distribution detector score (from DistributionDetector)
        # Only non-zero for PROMOTIONAL_EARLY positions
        distribution_detector_score: float = 0.0,
    ) -> ExitSignal:
        """
        Compute all exit signals for a position. Pure math, no I/O.

        Returns ExitSignal with recommendation: HOLD, TIGHTEN, or EXIT.

        D89: Added `bars` parameter — list of recent minute bar dicts with
        keys (o, h, l, c, v, vw). When provided, enables 5 additional
        multi-bar signals for institutional distribution detection.
        """
        signal = ExitSignal(ticker=ticker)

        # 1. Volume fade
        signal.volume_fade = self._volume_fade(current_volume, peak_volume)

        # 2. VWAP deterioration
        signal.vwap_deterioration = self._vwap_deterioration(
            current_price, vwap, entry_price
        )

        # 3. Spread widening
        current_spread = (ask - bid) if ask > bid > 0 else 0.0
        signal.spread_widening = self._spread_widening(
            current_spread, entry_spread, entry_price
        )

        # 4. Time decay
        signal.time_decay = self._time_decay(hour_et, minute_et)

        # 5. Distribution detection (ask-side pressure)
        signal.distribution = self._distribution_detection(bid_size, ask_size)

        # 6. Resistance proximity
        signal.resistance_proximity = self._resistance_proximity(
            current_price, resistance_level
        )

        # 7. Failed breakout (simplified — uses proximity as proxy)
        signal.failed_breakout = self._failed_breakout(
            current_price, resistance_level, entry_price
        )

        # D89: Multi-bar signals (8-12) — all default to 0.0 when bars=None
        signal.churning = self._churning(bars)
        signal.obv_divergence = self._obv_divergence(bars, current_price)
        signal.volume_climax = self._volume_climax(bars)
        signal.momentum_degradation = self._momentum_degradation(bars)
        signal.flow_toxicity = self._flow_toxicity(bars)

        # D106: Distribution detector score (only non-zero for PROMOTIONAL_EARLY)
        signal.distribution_detector = distribution_detector_score

        # Composite score
        signal.composite_exit_urgency = self._composite_score(signal)

        # Recommendation
        if signal.composite_exit_urgency >= self._exit_threshold:
            signal.recommendation = "EXIT"
            signal.reasoning = self._build_reasoning(signal, "EXIT")
        elif signal.composite_exit_urgency >= self._tighten_threshold:
            signal.recommendation = "TIGHTEN"
            signal.reasoning = self._build_reasoning(signal, "TIGHTEN")
        else:
            signal.recommendation = "HOLD"
            signal.reasoning = "Thesis intact"

        return signal

    # ── Individual Signal Computations ────────────────────────────

    @staticmethod
    def _volume_fade(current_volume: int, peak_volume: int) -> float:
        """
        Detect momentum dying: current volume vs peak volume since entry.

        Score 0.0 = volume healthy (at or above peak)
        Score 1.0 = volume completely dried up
        """
        if peak_volume <= 0 or current_volume <= 0:
            return 0.0
        ratio = current_volume / peak_volume
        if ratio >= 0.7:
            return 0.0  # Still strong
        if ratio <= 0.1:
            return 1.0  # Completely dried up
        # Linear interpolation: 0.7→0.0, 0.1→1.0
        return min(1.0, max(0.0, (0.7 - ratio) / 0.6))

    @staticmethod
    def _vwap_deterioration(
        current_price: float, vwap: float, entry_price: float
    ) -> float:
        """
        Detect thesis degrading: price below VWAP = sellers winning.

        Score 0.0 = price above VWAP (healthy)
        Score 1.0 = price far below VWAP (thesis broken)
        """
        if vwap <= 0 or current_price <= 0 or entry_price <= 0:
            return 0.0
        if current_price >= vwap:
            return 0.0  # Above VWAP = healthy

        # How far below VWAP as % of entry price
        pct_below = (vwap - current_price) / entry_price
        # Scale: 0% below → 0.0, 3%+ below → 1.0
        return min(1.0, max(0.0, pct_below / 0.03))

    @staticmethod
    def _spread_widening(
        current_spread: float, entry_spread: float, entry_price: float
    ) -> float:
        """
        Detect market makers pulling back: spread widening vs entry.

        Score 0.0 = spread normal
        Score 1.0 = spread dangerously wide (3%+ of price)
        """
        if entry_price <= 0:
            return 0.0

        # Absolute spread as pct of price
        spread_pct = current_spread / entry_price if entry_price > 0 else 0

        # If spread > 3% of price, max danger
        if spread_pct >= 0.03:
            return 1.0

        # Relative widening vs entry
        if entry_spread > 0 and current_spread > entry_spread:
            widening_ratio = current_spread / entry_spread
            if widening_ratio >= 3.0:
                return 0.9  # 3x+ widening = very bad
            if widening_ratio >= 2.0:
                return 0.6  # 2x widening = concerning
            if widening_ratio >= 1.5:
                return 0.3  # 1.5x = mild concern

        # Also flag if spread > 1.5% even without comparison
        if spread_pct >= 0.015:
            return 0.4

        return 0.0

    @staticmethod
    def _time_decay(hour_et: int, minute_et: int = 0) -> float:
        """
        Time-of-day urgency: momentum stocks fade after 10:30 AM ET.

        Score 0.0 = early morning (9:30-10:30, peak momentum)
        Score ramps to 1.0 at 15:00+ (must exit before close)
        """
        # Convert to decimal hour for smooth interpolation
        t = hour_et + minute_et / 60.0

        if t < 10.5:
            return 0.0  # Peak momentum window
        if t >= 15.0:
            return 1.0  # Must exit soon

        # Linear ramp from 10:30 (0.0) to 15:00 (1.0)
        return min(1.0, (t - 10.5) / 4.5)

    @staticmethod
    def _distribution_detection(bid_size: int, ask_size: int) -> float:
        """
        Detect institutional distribution: large ask-side prints.

        When big players sell, they place large orders on the ask.
        Bid << Ask = someone is distributing into retail buyers.

        Score 0.0 = balanced or bid-heavy (healthy buying)
        Score 1.0 = ask >> bid (heavy distribution)
        """
        if bid_size <= 0 or ask_size <= 0:
            return 0.0
        ratio = ask_size / bid_size
        if ratio <= 1.5:
            return 0.0  # Balanced or bid-heavy
        if ratio >= 5.0:
            return 1.0  # Extreme ask pressure
        # Scale: 1.5→0.0, 5.0→1.0
        return min(1.0, (ratio - 1.5) / 3.5)

    @staticmethod
    def _resistance_proximity(
        current_price: float, resistance_level: float
    ) -> float:
        """
        Detect stalling at resistance: price near a resistance level.

        Score 0.0 = price far from resistance
        Score 1.0 = price at resistance and stalling
        """
        if resistance_level <= 0 or current_price <= 0:
            return 0.0
        if current_price > resistance_level:
            return 0.0  # Broken through — not a concern

        # How close as % of price
        distance_pct = (resistance_level - current_price) / current_price
        if distance_pct > 0.03:
            return 0.0  # More than 3% away
        # 0% away → 1.0, 3% away → 0.0
        return max(0.0, 1.0 - distance_pct / 0.03)

    @staticmethod
    def _failed_breakout(
        current_price: float,
        resistance_level: float,
        entry_price: float,
    ) -> float:
        """
        Detect failed breakout: price rose to resistance but couldn't break.

        Uses a simplified heuristic: if price is near resistance but below it,
        and has gained from entry (was trying to break out), score higher.
        """
        if resistance_level <= 0 or entry_price <= 0 or current_price <= 0:
            return 0.0
        if current_price >= resistance_level:
            return 0.0  # Broke through

        # Only counts if we've gained from entry (was trying to break)
        gain_from_entry = (current_price - entry_price) / entry_price
        if gain_from_entry < 0.01:
            return 0.0  # Hasn't even run up

        # Distance from resistance
        distance_pct = (resistance_level - current_price) / current_price
        if distance_pct > 0.02:
            return 0.0  # Not close enough
        # Near resistance + has run up = potential failed breakout
        proximity = max(0.0, 1.0 - distance_pct / 0.02)
        return min(1.0, proximity * min(gain_from_entry / 0.05, 1.0))

    # ── D89: Multi-bar Signal Computations ────────────────────────

    @staticmethod
    def _churning(bars: list[dict] | None) -> float:
        """
        D89: Detect institutional churning — high volume with narrow price range.

        When institutions distribute, they trade large volume but keep price
        stable to avoid tipping off retail. This creates high-volume bars
        with suspiciously narrow ranges.

        Uses median-based comparison to avoid average inflation when
        churning bars are the majority.

        Score 0.0 = normal trading
        Score 1.0 = heavy churning (distribution likely)
        Requires ≥5 bars.
        """
        if not bars or len(bars) < 5:
            return 0.0

        volumes = [float(b.get("v", 0) or 0) for b in bars]
        ranges = [float(b.get("h", 0) or 0) - float(b.get("l", 0) or 0) for b in bars]

        # Use median for robust comparison (unaffected by outliers)
        sorted_vols = sorted(volumes)
        sorted_ranges = sorted(ranges)
        n = len(bars)
        median_vol = sorted_vols[n // 2]
        median_range = sorted_ranges[n // 2]

        if median_vol <= 0 or median_range <= 0:
            return 0.0

        # Count bars with high volume (>2x median) AND narrow range (<0.5x median)
        churning_count = 0
        for vol, rng in zip(volumes, ranges):
            if vol > 2.0 * median_vol and rng < 0.5 * median_range:
                churning_count += 1

        # Score: proportion of bars that are churning
        score = churning_count / len(bars)
        return min(1.0, score * 2.0)  # Amplify: 50% churning bars → 1.0

    @staticmethod
    def _obv_divergence(bars: list[dict] | None, current_price: float = 0.0) -> float:
        """
        D89: Detect OBV (On-Balance Volume) divergence.

        Price making higher highs while OBV makes lower highs = bearish
        divergence. OBV is a leading indicator — volume dries up before
        price drops.

        D89b: Uses peak-finding instead of simple half-split. Finds the two
        highest price peaks (separated by ≥3 bars) and checks if OBV at the
        later peak is lower than at the earlier one. This catches divergences
        within consolidation ranges, not just first-half vs second-half.

        Score 0.0 = no divergence (healthy)
        Score 1.0 = strong bearish divergence
        Requires ≥10 bars.
        """
        if not bars or len(bars) < 10:
            return 0.0

        # Compute OBV series
        obv_series = [0.0]
        for i in range(1, len(bars)):
            close_cur = float(bars[i].get("c", 0) or 0)
            close_prev = float(bars[i - 1].get("c", 0) or 0)
            vol = float(bars[i].get("v", 0) or 0)
            if close_cur > close_prev:
                obv_series.append(obv_series[-1] + vol)
            elif close_cur < close_prev:
                obv_series.append(obv_series[-1] - vol)
            else:
                obv_series.append(obv_series[-1])

        closes = [float(b.get("c", 0) or 0) for b in bars]

        # D89b: Find the two highest close peaks (separated by ≥3 bars)
        closes_indexed = sorted(enumerate(closes), key=lambda x: x[1], reverse=True)
        if len(closes_indexed) < 2:
            return 0.0

        # First peak is the absolute high
        peak1_idx, peak1_price = closes_indexed[0]
        if peak1_price <= 0:
            return 0.0

        # Find second-highest peak that is ≥3 bars away from first
        peak2_idx, peak2_price = -1, 0.0
        for idx, price in closes_indexed[1:]:
            if abs(idx - peak1_idx) >= 3:
                peak2_idx, peak2_price = idx, price
                break

        if peak2_idx < 0 or peak2_price <= 0:
            # Fallback to half-split if no separated peaks found
            mid = len(bars) // 2
            first_half_price_max = max(closes[:mid]) if closes[:mid] else 0
            second_half_price_max = max(closes[mid:]) if closes[mid:] else 0
            first_half_obv_max = max(obv_series[:mid]) if obv_series[:mid] else 0
            second_half_obv_max = max(obv_series[mid:]) if obv_series[mid:] else 0

            # D121 BUG-R8: Use consistent <= 0 check (was == 0 for OBV,
            # which could allow near-zero values through to division).
            if first_half_price_max <= 0 or first_half_obv_max <= 0:
                return 0.0
            if second_half_price_max > first_half_price_max and second_half_obv_max < first_half_obv_max:
                obv_decline = (first_half_obv_max - second_half_obv_max) / abs(first_half_obv_max)
                return min(1.0, max(0.0, obv_decline * 2.0))
            return 0.0

        # Determine earlier vs later peak
        earlier_idx = min(peak1_idx, peak2_idx)
        later_idx = max(peak1_idx, peak2_idx)

        # Bearish divergence: later price ≥ earlier price, but later OBV < earlier OBV
        if closes[later_idx] >= closes[earlier_idx] * 0.98:  # Within 2% or higher
            if obv_series[later_idx] < obv_series[earlier_idx]:
                # Divergence strength: how much has OBV dropped?
                earlier_obv = obv_series[earlier_idx]
                # D121 BUG-S11: Use > 1.0 threshold (not > 0) to avoid
                # extreme divergence values from near-zero OBV denominators.
                if abs(earlier_obv) > 1.0:
                    obv_decline = (earlier_obv - obv_series[later_idx]) / abs(earlier_obv)
                    return min(1.0, max(0.0, obv_decline * 2.0))

        return 0.0

    @staticmethod
    def _volume_climax(bars: list[dict] | None) -> float:
        """
        D89: Detect volume climax (blow-off top).

        When volume spikes >3x the moving average at or near the session high,
        it signals exhaustive buying — the last rush of retail FOMO before
        the reversal.

        Score 0.0 = normal volume
        Score 1.0 = extreme volume climax at high
        Requires ≥10 bars.
        """
        if not bars or len(bars) < 10:
            return 0.0

        volumes = [float(b.get("v", 0) or 0) for b in bars]
        vol_sma = sum(volumes) / len(volumes) if volumes else 0

        if vol_sma <= 0:
            return 0.0

        # Check most recent bar
        latest_vol = volumes[-1]
        latest_high = float(bars[-1].get("h", 0) or 0)

        # Is the latest bar near the session high?
        session_high = max(float(b.get("h", 0) or 0) for b in bars)
        if session_high <= 0 or latest_high <= 0:
            return 0.0

        near_high = latest_high >= session_high * 0.98  # Within 2% of high

        if near_high and latest_vol > 3.0 * vol_sma:
            # D89b: Score based on how extreme the volume spike is
            # 3x SMA → 0.0, 5x SMA → 1.0 (linear scale)
            return min(1.0, max(0.0, (latest_vol / vol_sma - 3.0) / 2.0))

        return 0.0

    @staticmethod
    def _momentum_degradation(bars: list[dict] | None) -> float:
        """
        D89: Detect momentum degradation via consecutive lower lows.

        When a momentum stock starts making lower lows on each bar, the
        trend is dying. 3+ consecutive lower lows is a warning sign.

        Score 0.0 = uptrend intact
        Score 1.0 = severe degradation (5+ lower lows)
        Requires ≥5 bars.
        """
        if not bars or len(bars) < 5:
            return 0.0

        # Count consecutive lower lows from the most recent bar backward
        consecutive = 0
        for i in range(len(bars) - 1, 0, -1):
            low_cur = float(bars[i].get("l", 0) or 0)
            low_prev = float(bars[i - 1].get("l", 0) or 0)
            if low_cur < low_prev and low_prev > 0:
                consecutive += 1
            else:
                break

        if consecutive < 3:
            return 0.0

        # 3 lower lows → 0.33, 4 → 0.67, 5+ → 1.0
        return min(1.0, (consecutive - 2) / 3.0)

    @staticmethod
    def _flow_toxicity(bars: list[dict] | None) -> float:
        """
        D89: Detect sell-side flow toxicity using BVC (Bulk Volume Classification).

        D89b: Changed from absolute imbalance to sell-directional. The original
        implementation fired on strong buying (close near high) which is NOT
        a reason to exit. Now only penalizes when sellers dominate — which is
        what we actually want for exit decisions.

        buy_volume = volume × (close - low) / (high - low)
        sell_volume = volume - buy_volume
        sell_toxicity = mean(max(0, sell - buy) / total) over window

        High sell toxicity (>0.15) = informed sellers active = exit risk.

        Score 0.0 = balanced or buy-dominated flow
        Score 1.0 = heavily sell-toxic (informed selling)
        Requires ≥10 bars.
        """
        if not bars or len(bars) < 10:
            return 0.0

        toxicity_values = []
        for b in bars:
            high = float(b.get("h", 0) or 0)
            low = float(b.get("l", 0) or 0)
            close = float(b.get("c", 0) or 0)
            vol = float(b.get("v", 0) or 0)

            bar_range = high - low
            if bar_range <= 0 or vol <= 0:
                continue

            # BVC: fraction of volume classified as buying
            buy_frac = (close - low) / bar_range
            buy_vol = vol * buy_frac
            sell_vol = vol - buy_vol

            # D89b: Sell-directional — only penalize when sellers dominate
            # max(0, ...) means strong buying produces 0.0 (not a concern)
            sell_imbalance = max(0.0, sell_vol - buy_vol) / vol
            toxicity_values.append(sell_imbalance)

        if not toxicity_values:
            return 0.0

        mean_toxicity = sum(toxicity_values) / len(toxicity_values)

        # D89b: Lower thresholds for sell-directional signal
        # Score: toxicity 0.15 → 0.0, toxicity 0.45 → 1.0
        if mean_toxicity <= 0.15:
            return 0.0
        return min(1.0, (mean_toxicity - 0.15) / 0.30)

    # ── Composite Score ───────────────────────────────────────────

    def _composite_score(self, signal: ExitSignal) -> float:
        """Compute weighted composite exit urgency. D89/D106: 13 signals."""
        scores = {
            "volume_fade": signal.volume_fade,
            "vwap_deterioration": signal.vwap_deterioration,
            "spread_widening": signal.spread_widening,
            "time_decay": signal.time_decay,
            "distribution": signal.distribution,
            "resistance_proximity": signal.resistance_proximity,
            "failed_breakout": signal.failed_breakout,
            # D89: Multi-bar signals
            "churning": signal.churning,
            "obv_divergence": signal.obv_divergence,
            "volume_climax": signal.volume_climax,
            "momentum_degradation": signal.momentum_degradation,
            "flow_toxicity": signal.flow_toxicity,
            # D106: Distribution detector
            "distribution_detector": signal.distribution_detector,
        }
        weighted = sum(
            scores.get(k, 0.0) * self._weights.get(k, 0.0)
            for k in self._weights
        )
        total_weight = sum(self._weights.values())
        return weighted / total_weight if total_weight > 0 else 0.0

    # ── Reasoning ─────────────────────────────────────────────────

    @staticmethod
    def _build_reasoning(signal: ExitSignal, action: str) -> str:
        """Build human-readable exit reasoning from top signals."""
        reasons = []
        if signal.volume_fade > 0.5:
            reasons.append(f"vol_fade={signal.volume_fade:.1f}")
        if signal.vwap_deterioration > 0.3:
            reasons.append(f"below_vwap={signal.vwap_deterioration:.1f}")
        if signal.spread_widening > 0.3:
            reasons.append(f"spread_wide={signal.spread_widening:.1f}")
        if signal.time_decay > 0.5:
            reasons.append(f"time_decay={signal.time_decay:.1f}")
        if signal.distribution > 0.3:
            reasons.append(f"distribution={signal.distribution:.1f}")
        if signal.resistance_proximity > 0.3:
            reasons.append(f"at_resist={signal.resistance_proximity:.1f}")
        # D89: New multi-bar signals
        if signal.churning > 0.3:
            reasons.append(f"churning={signal.churning:.1f}")
        if signal.obv_divergence > 0.3:
            reasons.append(f"obv_div={signal.obv_divergence:.1f}")
        if signal.volume_climax > 0.3:
            reasons.append(f"vol_climax={signal.volume_climax:.1f}")
        if signal.momentum_degradation > 0.3:
            reasons.append(f"mom_degrade={signal.momentum_degradation:.1f}")
        if signal.flow_toxicity > 0.3:
            reasons.append(f"flow_toxic={signal.flow_toxicity:.1f}")

        if not reasons:
            reasons.append(f"composite={signal.composite_exit_urgency:.2f}")

        return f"{action}: {', '.join(reasons)}"


# ── Exit Intelligence Manager ────────────────────────────────────────


class ExitIntelligenceManager:
    """
    Orchestrates exit intelligence for all open positions.

    Called every Phase 3 cycle. For each position:
    1. Compute ExitSignal via ExitSignalEngine
    2. Determine action: HOLD, TIGHTEN, EXIT
    3. Return list of ExitActions for main.py to execute
    """

    def __init__(
        self,
        engine: ExitSignalEngine | None = None,
        tighten_threshold: float = 0.3,
        exit_threshold: float = 0.6,
        # D110: Config params forwarded to ParallelExitEngine
        half_life_table: dict[str, dict[str, float]] | None = None,
        null_curve: list[tuple[int, float]] | None = None,
        contagion_decay_minutes: float = 10.0,
        contagion_threshold: float = 0.3,
        # D122: Parallel strategy activation
        parallel_strategies_active: bool = False,
        parallel_exit_min_confidence: float = 0.7,
        parallel_tighten_min_confidence: float = 0.5,
        parallel_min_strategies_for_exit: int = 2,
        parallel_exit_excluded_strategies: list[str] | None = None,
        archetype_strategy: "ArchetypeExitStrategy | None" = None,
    ) -> None:
        self._engine = engine or ExitSignalEngine(
            tighten_threshold=tighten_threshold,
            exit_threshold=exit_threshold,
        )
        # D122: Parallel strategy activation params
        self._parallel_active = parallel_strategies_active
        self._parallel_exit_min_conf = parallel_exit_min_confidence
        self._parallel_tighten_min_conf = parallel_tighten_min_confidence
        self._parallel_min_strats_exit = parallel_min_strategies_for_exit
        # D122: Excluded strategies (broken: pullback fires EXHAUSTED 92%,
        # catalyst_half_life fires EXIT 85% due to catalyst_type=unknown)
        self._parallel_excluded = set(parallel_exit_excluded_strategies or [])
        # D89: Per-position bar ring buffer (last 30 minute bars per ticker)
        self._bar_history: dict[str, list[dict]] = {}
        # D109 Phase 4 + D110: Parallel exit strategies (compute + log)
        # D110 adds CatalystHalfLife, AlphaDecayOracle, ContagionNetwork
        # D122: When _parallel_active=True, results can upgrade HOLD→TIGHTEN/EXIT
        self._archetype_strategy = archetype_strategy
        self._parallel_engine = ParallelExitEngine(
            half_life_table=half_life_table,
            null_curve=null_curve,
            contagion_decay_minutes=contagion_decay_minutes,
            contagion_threshold=contagion_threshold,
            archetype_strategy=archetype_strategy,
        )

    @staticmethod
    def compute_atr(bars: list[dict], period: int = 14) -> float | None:
        """D100: Delegates to module-level compute_atr() for backward compat."""
        return compute_atr(bars, period)

    def get_atr(self, ticker: str, period: int = 14) -> float | None:
        """Get ATR for a ticker from its bar history. Returns None if insufficient data."""
        bars = self._bar_history.get(ticker)
        return self.compute_atr(bars, period)

    def evaluate_positions(
        self,
        positions: list,  # list[ManagedPosition]
        snapshots: dict[str, dict],  # ticker -> snapshot dict
        hour_et: int = 10,
        minute_et: int = 0,
        vwap_lookup: dict[str, float] | None = None,
    ) -> list[ExitAction]:
        """
        Evaluate all positions and return exit actions.

        Args:
            positions: Open ManagedPosition instances
            snapshots: Alpaca snapshots {ticker: {last_price, bid, ask, ...}}
                       Supports both raw Alpaca format (latestTrade.p) and
                       normalized format (last_price, bid, ask, volume, etc.)
            hour_et: Current hour (ET)
            minute_et: Current minute (ET)
            vwap_lookup: {ticker: vwap_value} from WebSocket accumulator

        Returns:
            List of ExitAction (one per position, HOLD included for logging)
        """
        actions: list[ExitAction] = []
        vwap_lookup = vwap_lookup or {}

        for pos in positions:
            snap = snapshots.get(pos.ticker, {})
            if not snap:
                continue

            current_price = float(snap.get("latestTrade", {}).get("p", 0) or
                                  snap.get("last_price", 0) or 0)
            if current_price <= 0:
                continue

            # Update peak price tracking
            if current_price > pos.peak_price:
                pos.peak_price = current_price

            # ── D101 §3.5: Time-Based Momentum Exit (20-Minute Rule) ──
            # If position hasn't moved 1R (one risk unit) in 20 minutes,
            # momentum has likely failed — exit to free capital.
            #
            # D278 EXIT_POLICY (2026-04-29): gate D101 §3.5 TIME_EXIT on
            # exit_policy. When 't1_next_open', skip the 20-minute time
            # decay — positions are intentionally held to next-day open;
            # closing at T+20min would defeat the policy. Stop-loss and
            # signal-based exits still apply. Per doc 69.
            import os as _os
            _d278_env = _os.environ.get("MOMENTUM_EXIT_POLICY", "").strip().lower()
            _d278_skip_time_decay = (_d278_env == "t1_next_open")
            _one_r = pos.entry_price - pos.stop_loss if pos.stop_loss > 0 else 0
            if (
                not _d278_skip_time_decay
                and _one_r > 0
                and hasattr(pos, "opened_at")
                and pos.opened_at
            ):
                _elapsed = (datetime.now(timezone.utc) - pos.opened_at).total_seconds()
                _minutes_held = _elapsed / 60.0
                _target_1r = pos.entry_price + _one_r
                if _minutes_held > 20 and current_price < _target_1r:
                    logger.info(
                        "D101 §3.5 TIME EXIT %s: held %.0fmin, price=$%.2f < 1R=$%.2f "
                        "(entry=$%.2f, stop=$%.2f, 1R=$%.2f)",
                        pos.ticker, _minutes_held, current_price, _target_1r,
                        pos.entry_price, pos.stop_loss, _one_r,
                    )
                    actions.append(ExitAction(
                        ticker=pos.ticker,
                        action="EXIT",
                        signal=ExitSignal(ticker=pos.ticker, recommendation="EXIT",
                                          reasoning=f"D101 TIME_DECAY: No 1R move in "
                                          f"{_minutes_held:.0f}min"),
                        exit_reason=f"D101 TIME_EXIT: {_minutes_held:.0f}min held, "
                        f"price=${current_price:.2f} < 1R=${_target_1r:.2f}",
                    ))
                    continue  # Skip normal signal computation for this position

            # ── Volume extraction ──
            # Daily cumulative volume (only goes up throughout the day)
            daily_vol = int(snap.get("dailyBar", {}).get("v", 0) or
                            snap.get("volume", 0) or 0)

            # FIX: Use minute-bar volume for volume fade detection.
            # Daily cumulative volume ONLY increases throughout the day, so
            # current_vol / peak_volume is always ~1.0 and volume_fade is
            # permanently stuck at 0.0.  Minute-bar volume captures actual
            # per-bar activity which drops when momentum dies.
            minute_vol = int(snap.get("minuteBar", {}).get("v", 0) or
                             snap.get("minute_volume", 0) or 0)

            # Track peak minute volume on the position for fade detection
            if not hasattr(pos, "_peak_minute_vol"):
                pos._peak_minute_vol = 0
            if minute_vol > pos._peak_minute_vol:
                pos._peak_minute_vol = minute_vol

            # Choose which volume pair to use for fade detection:
            # - If we have minute volume data, use it (detects real fade)
            # - Otherwise fall back to daily volume (still monotonic, but
            #   better than nothing with the peak_volume fix below)
            if minute_vol > 0 and pos._peak_minute_vol > 0:
                fade_current_vol = minute_vol
                fade_peak_vol = pos._peak_minute_vol
            else:
                fade_current_vol = daily_vol
                fade_peak_vol = pos.peak_volume

            # Still track daily peak for backward compatibility
            if daily_vol > pos.peak_volume:
                pos.peak_volume = daily_vol

            # ── D89: Accumulate bar history for multi-bar signals ──
            bar_data = snap.get("minuteBar", {})
            if bar_data and int(bar_data.get("v", 0) or 0) > 0:
                hist = self._bar_history.setdefault(pos.ticker, [])
                hist.append({
                    k: float(bar_data.get(k, 0) or 0)
                    for k in ("o", "h", "l", "c", "v", "vw")
                })
                if len(hist) > 30:
                    hist.pop(0)  # Ring buffer: keep last 30 bars

            # ── Quote extraction ──
            bid = float(snap.get("latestQuote", {}).get("bp", 0) or
                        snap.get("bid", 0) or 0)
            ask = float(snap.get("latestQuote", {}).get("ap", 0) or
                        snap.get("ask", 0) or 0)
            bid_size = int(snap.get("latestQuote", {}).get("bs", 0) or
                           snap.get("bid_size", 0) or 0)
            ask_size = int(snap.get("latestQuote", {}).get("as", 0) or
                           snap.get("ask_size", 0) or 0)

            # ── VWAP: use lookup, fall back to snapshot-derived proxy ──
            vwap = vwap_lookup.get(pos.ticker, 0.0)
            if vwap <= 0:
                # FIX: When WebSocket VWAP unavailable, approximate from
                # snapshot data.  Alpaca dailyBar VWAP is often available;
                # otherwise use (open + high + low + close) / 4 as a proxy.
                vwap = float(snap.get("dailyBar", {}).get("vw", 0) or
                             snap.get("vwap", 0) or 0)
            if vwap <= 0:
                # Last resort: typical price from OHLC
                _dh = float(snap.get("dailyBar", {}).get("h", 0) or
                            snap.get("day_high", 0) or 0)
                _dl = float(snap.get("dailyBar", {}).get("l", 0) or
                            snap.get("day_low", 0) or 0)
                _do = float(snap.get("dailyBar", {}).get("o", 0) or
                            snap.get("day_open", 0) or 0)
                if _dh > 0 and _dl > 0 and _do > 0:
                    vwap = (_do + _dh + _dl + current_price) / 4.0

            # ── Entry spread: auto-populate if missing ──
            # FIX: entry_spread was never set at position creation, making
            # the spread widening relative comparison permanently dead.
            # Capture current spread as baseline on first evaluation.
            if pos.entry_spread <= 0 and bid > 0 and ask > bid:
                pos.entry_spread = ask - bid
                logger.debug(
                    "D78: Auto-captured entry_spread for %s: $%.4f",
                    pos.ticker, pos.entry_spread,
                )

            # ── Resistance level: use day_high as proxy ──
            # FIX: resistance_level was never passed to compute_exit_signals,
            # leaving resistance_proximity and failed_breakout permanently at 0.
            # Use the session high as a resistance proxy — if price ran up to
            # day_high and is now pulling back, it acts as resistance.
            day_high = float(snap.get("dailyBar", {}).get("h", 0) or
                             snap.get("day_high", 0) or 0)
            # Only treat day_high as resistance if price is BELOW it
            # (if at or above, it's not resistance)
            resistance_level = day_high if day_high > current_price else 0.0

            signal = self._engine.compute_exit_signals(
                ticker=pos.ticker,
                current_price=current_price,
                entry_price=pos.entry_price,
                bid=bid,
                ask=ask,
                current_volume=fade_current_vol,
                entry_spread=pos.entry_spread,
                peak_volume=fade_peak_vol,
                vwap=vwap,
                hour_et=hour_et,
                minute_et=minute_et,
                resistance_level=resistance_level,
                bid_size=bid_size,
                ask_size=ask_size,
                bars=self._bar_history.get(pos.ticker),  # D89
            )

            # ── D109 Phase 4: Parallel exit strategies (LOGGING ONLY) ──
            _parallel_log = None
            _intraday_atr = None
            _mfe_pct = None
            _time_held_min = None
            try:
                _time_held_min = (
                    (datetime.now(timezone.utc) - pos.opened_at).total_seconds() / 60.0
                    if hasattr(pos, "opened_at") and pos.opened_at else 0.0
                )
                # D110: Extract catalyst info from position if available
                _catalyst_type = getattr(pos, "catalyst_type", "unknown") or "unknown"
                _manipulation_phase = getattr(pos, "manipulation_phase", "ORGANIC_MOMENTUM") or "ORGANIC_MOMENTUM"
                # D110: minutes since market open (9:30 ET) for Alpha Oracle
                # Use actual clock time, not hold time — the null curve is
                # calibrated to minutes-since-open, not minutes-since-entry.
                _minutes_since_open = (hour_et - 9) * 60 + (minute_et - 30)
                if _minutes_since_open < 0:
                    _minutes_since_open = 0.0  # Pre-market fallback
                # D118: Position-level catalyst profiler params
                _cat_half_life = getattr(pos, "catalyst_half_life_minutes", None)
                _cat_grat_decay = getattr(pos, "catalyst_gratitude_decay", None)
                # Durability shift for AlphaDecayOracle: scale by how much
                # the profiler's half-life differs from the 20-min default.
                # E.g., 180-min → shift +16 min right; 8-min → shift -3 min left.
                _cat_dur_shift = 0.0
                if _cat_half_life is not None and _cat_half_life != 20:
                    _cat_dur_shift = (_cat_half_life - 20) * 0.1

                _parallel_results = self._parallel_engine.evaluate_all(
                    ticker=pos.ticker,
                    entry_price=pos.entry_price,
                    stop_loss=pos.stop_loss,
                    current_price=current_price,
                    peak_price=pos.peak_price or pos.entry_price,
                    minutes_held=_time_held_min,
                    entry_volume=getattr(pos, "entry_volume", 0) or 0,
                    bars=self._bar_history.get(pos.ticker),
                    catalyst_type=_catalyst_type,
                    manipulation_phase=_manipulation_phase,
                    minutes_since_open=_minutes_since_open,
                    catalyst_half_life_minutes=_cat_half_life,
                    catalyst_gratitude_decay=_cat_grat_decay,
                    catalyst_durability_shift=_cat_dur_shift,
                )
                # D110: Record contagion signals from any strategy that fired
                _any_fired = any(r.should_exit or r.should_tighten for r in _parallel_results)
                if _any_fired:
                    _best_confidence = max(
                        (r.confidence for r in _parallel_results if r.should_exit or r.should_tighten),
                        default=0.0,
                    )
                    _is_exit = any(r.should_exit for r in _parallel_results)
                    _sector = getattr(pos, "sector", "") or ""
                    _gap_pct = getattr(pos, "gap_pct", 0.0) or 0.0
                    self._parallel_engine.record_contagion_signal(
                        ticker=pos.ticker,
                        sector=_sector,
                        catalyst_type=_catalyst_type,
                        gap_pct=_gap_pct,
                        confidence=_best_confidence,
                        is_exit=_is_exit,
                    )
                # D110: Evaluate contagion for this position
                _contagion_result = self._parallel_engine.evaluate_contagion(
                    target_ticker=pos.ticker,
                    target_sector=getattr(pos, "sector", "") or "",
                    target_catalyst=_catalyst_type,
                    target_gap_pct=getattr(pos, "gap_pct", 0.0) or 0.0,
                )
                _contagion_active = self._parallel_engine._contagion.active_position_count()
                _parallel_log = ParallelExitEngine.to_log_dict(
                    _parallel_results, _contagion_result, _contagion_active
                )
                _intraday_atr = compute_atr(
                    self._bar_history.get(pos.ticker, []), period=14
                )
                _mfe_pct = (
                    (pos.peak_price - pos.entry_price) / pos.entry_price
                    if pos.entry_price > 0
                    and pos.peak_price is not None
                    and pos.peak_price > pos.entry_price
                    else 0.0
                )
            except Exception as _pe:
                logger.debug(
                    "D109: Parallel strategy error for %s: %s", pos.ticker, _pe
                )
            # Attach to signal for downstream logging by SignalHistoryLogger
            signal._parallel_log = _parallel_log  # type: ignore[attr-defined]
            signal._intraday_atr = _intraday_atr  # type: ignore[attr-defined]
            signal._mfe_pct = _mfe_pct  # type: ignore[attr-defined]
            signal._time_held_min = _time_held_min  # type: ignore[attr-defined]

            # ── Diagnostic logging (every cycle, not just TIGHTEN/EXIT) ──
            bars_count = len(self._bar_history.get(pos.ticker, []))
            if signal.recommendation != "HOLD":
                logger.info(
                    "D89 %s %s: composite=%.3f [vf=%.2f vwap=%.2f sprd=%.2f "
                    "time=%.2f dist=%.2f res=%.2f fb=%.2f "
                    "churn=%.2f obv=%.2f vc=%.2f md=%.2f ft=%.2f] bars=%d",
                    signal.recommendation, pos.ticker,
                    signal.composite_exit_urgency,
                    signal.volume_fade, signal.vwap_deterioration,
                    signal.spread_widening, signal.time_decay,
                    signal.distribution, signal.resistance_proximity,
                    signal.failed_breakout,
                    signal.churning, signal.obv_divergence,
                    signal.volume_climax, signal.momentum_degradation,
                    signal.flow_toxicity, bars_count,
                )
            else:
                logger.debug(
                    "D89 HOLD %s: composite=%.3f [vf=%.2f vwap=%.2f sprd=%.2f "
                    "time=%.2f dist=%.2f res=%.2f fb=%.2f "
                    "churn=%.2f obv=%.2f vc=%.2f md=%.2f ft=%.2f] "
                    "(vwap_src=%s, min_vol=%d/%d, entry_sprd=%.4f, bars=%d)",
                    pos.ticker,
                    signal.composite_exit_urgency,
                    signal.volume_fade, signal.vwap_deterioration,
                    signal.spread_widening, signal.time_decay,
                    signal.distribution, signal.resistance_proximity,
                    signal.failed_breakout,
                    signal.churning, signal.obv_divergence,
                    signal.volume_climax, signal.momentum_degradation,
                    signal.flow_toxicity,
                    "ws" if vwap_lookup.get(pos.ticker, 0) > 0 else "snap",
                    fade_current_vol, fade_peak_vol,
                    pos.entry_spread, bars_count,
                )

            # ── D101 §3.4: Chandelier Exit Trailing Stop ──
            # chandelier_stop = peak_price - (ATR × multiplier)
            # Multiplier adapts to volatility:
            #   Low-vol  (ATR < 3% of price): 1.5x — tight trail
            #   Normal   (ATR 3-7% of price): 2.0x — standard
            #   High-vol (ATR > 7% of price): 3.0x — wide trail
            _atr = self.get_atr(pos.ticker)
            _chandelier_stop = None
            if _atr is not None and _atr > 0 and pos.peak_price > 0:
                _atr_pct = _atr / pos.entry_price if pos.entry_price > 0 else 0
                if _atr_pct < 0.03:
                    _chan_mult = 1.5
                elif _atr_pct > 0.07:
                    _chan_mult = 3.0
                else:
                    _chan_mult = 2.0
                _chandelier_stop = pos.peak_price - (_atr * _chan_mult)

            # Build action
            if signal.recommendation == "EXIT":
                action = ExitAction(
                    ticker=pos.ticker,
                    action="EXIT",
                    signal=signal,
                    exit_reason=f"D78 SMART_EXIT: {signal.reasoning}",
                )
            elif signal.recommendation == "TIGHTEN":
                # Compute tighter trailing stop using urgency-based trail
                # More urgency → tighter stop (higher stop price)
                # At urgency 0.3, trail at 2.5%. At urgency 0.6, trail at 1.5%
                trail_pct = max(0.01, 0.03 - 0.03 * signal.composite_exit_urgency)
                urgency_stop = current_price * (1.0 - trail_pct)

                # D101 §3.4: Use the TIGHTEST (highest) of urgency stop and chandelier
                new_stop = urgency_stop
                if _chandelier_stop is not None and _chandelier_stop > new_stop:
                    new_stop = _chandelier_stop

                # INVARIANT: stop only goes UP
                if new_stop > pos.stop_loss:
                    action = ExitAction(
                        ticker=pos.ticker,
                        action="TIGHTEN",
                        signal=signal,
                        new_stop=round(new_stop, 4),
                        exit_reason=f"D78 TIGHTEN: {signal.reasoning}",
                    )
                else:
                    action = ExitAction(
                        ticker=pos.ticker,
                        action="HOLD",
                        signal=signal,
                    )
            elif _chandelier_stop is not None and _chandelier_stop > pos.stop_loss:
                # D101 §3.4: Even during HOLD, chandelier stop can ratchet up
                action = ExitAction(
                    ticker=pos.ticker,
                    action="TIGHTEN",
                    signal=signal,
                    new_stop=round(_chandelier_stop, 4),
                    exit_reason=f"D101 CHANDELIER: peak=${pos.peak_price:.2f} - "
                    f"ATR×{_chan_mult:.1f}=${_atr * _chan_mult:.2f}",
                )
            else:
                action = ExitAction(
                    ticker=pos.ticker,
                    action="HOLD",
                    signal=signal,
                )

            # ── D122: Parallel strategy UPGRADE (any-of architecture) ──
            # Signal history analysis (357 signals, 3 days) showed:
            #   - pullback fires EXIT 91% of time (EXHAUSTED state)
            #   - catalyst_half_life fires EXIT 86% (unknown catalyst → 20min half-life)
            # Gates: min confidence + min agreeing strategies prevent over-firing.
            if self._parallel_active and _parallel_results:
                _p_exits = [
                    r for r in _parallel_results
                    if r.should_exit
                    and r.confidence >= self._parallel_exit_min_conf
                    and r.strategy_name not in self._parallel_excluded
                ]
                _p_tightens = [
                    r for r in _parallel_results
                    if r.should_tighten
                    and r.confidence >= self._parallel_tighten_min_conf
                    and r.strategy_name not in self._parallel_excluded
                ]

                if (
                    len(_p_exits) >= self._parallel_min_strats_exit
                    and action.action != "EXIT"
                ):
                    _best = max(_p_exits, key=lambda r: r.confidence)
                    _names = ", ".join(r.strategy_name for r in _p_exits)
                    logger.info(
                        "D122 %s: parallel EXIT override by %d strategies "
                        "[%s] (best=%s conf=%.2f, was %s)",
                        pos.ticker, len(_p_exits), _names,
                        _best.strategy_name, _best.confidence, action.action,
                    )
                    action = ExitAction(
                        ticker=pos.ticker,
                        action="EXIT",
                        signal=signal,
                        exit_reason=(
                            f"D122 PARALLEL_EXIT: {len(_p_exits)} strategies "
                            f"[{_names}] (best conf={_best.confidence:.2f})"
                        ),
                    )

                elif _p_tightens and action.action == "HOLD":
                    _best = max(_p_tightens, key=lambda r: r.confidence)
                    # Use ATR-grounded trail, NOT confidence-based %.
                    # Confidence-based trail (1% at conf=1.0) is too tight
                    # for sub-$10 small-caps ($0.05 on $5 stock).
                    _pos_atr = self.get_atr(pos.ticker)
                    if _pos_atr and _pos_atr > 0:
                        new_stop = current_price - (1.5 * _pos_atr)
                    else:
                        new_stop = current_price * 0.975

                    if new_stop > pos.stop_loss:
                        action = ExitAction(
                            ticker=pos.ticker,
                            action="TIGHTEN",
                            signal=signal,
                            new_stop=round(new_stop, 4),
                            exit_reason=(
                                f"D122 PARALLEL_TIGHTEN: {_best.strategy_name} "
                                f"(confidence={_best.confidence:.2f})"
                            ),
                        )
                        logger.info(
                            "D122 %s: parallel TIGHTEN by %s → stop $%.4f "
                            "(was $%.4f, confidence=%.2f)",
                            pos.ticker, _best.strategy_name,
                            new_stop, pos.stop_loss, _best.confidence,
                        )
                    else:
                        logger.debug(
                            "D122 %s: parallel TIGHTEN by %s skipped — "
                            "new_stop $%.4f <= current $%.4f",
                            pos.ticker, _best.strategy_name,
                            new_stop, pos.stop_loss,
                        )

            actions.append(action)

        # D109: Prune parallel engine state for closed positions
        _active_tickers = {pos.ticker for pos in positions}
        self._parallel_engine.prune_stale(_active_tickers)

        return actions
