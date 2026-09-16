"""
MOMENTUM-X Distribution Detector (D106 §3A)

### ARCHITECTURAL CONTEXT
Node ID: execution.distribution_detector
Graph Link: docs/memory/graph_state.json → "execution.distribution_detector"

### PURPOSE
Pure Python, zero LLM, <1ms post-entry distribution detector.
Complements the ManipulationClassifier (pre-entry) with real-time
monitoring for institutional distribution patterns once a position
is open. Particularly important for PROMOTIONAL_EARLY positions
where the window between "tradeable pump" and "distribution dump"
can close rapidly.

### 7 WEIGHTED SIGNALS
1. volume_without_advance (0.20) — high volume but price flat/declining
2. new_dilutive_filing    (0.20) — 424B5 filed since entry
3. spread_expansion       (0.15) — bid-ask spread widened vs entry
4. large_block_sells      (0.15) — large ask-side prints
5. price_below_vwap       (0.10) — price slipped below VWAP
6. minutes_since_entry    (0.10) — time decay (pump fades)
7. peak_drawdown          (0.10) — drawdown from peak = distribution

### DESIGN DECISIONS
- Pure deterministic computation — no LLM, no I/O, <1ms
- Composite > 0.50 → should_exit = True
- Only activated for PROMOTIONAL_EARLY positions (0.0 for ORGANIC/UNCERTAIN)
- Returns DistributionSignal dataclass for integration with exit intelligence

Ref: D106 WS3 (Distribution Detection)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


# ── Signal Weights ──────────────────────────────────────────────

DISTRIBUTION_WEIGHTS: dict[str, float] = {
    "volume_without_advance": 0.20,
    "new_dilutive_filing": 0.20,
    "spread_expansion": 0.15,
    "large_block_sells": 0.15,
    "price_below_vwap": 0.10,
    "minutes_since_entry": 0.10,
    "peak_drawdown": 0.10,
}


# ── Data Classes ────────────────────────────────────────────────


@dataclass
class DistributionSignal:
    """
    Result of distribution detection analysis.

    Composite > 0.50 → should_exit = True.
    All individual signals are normalized to [0.0, 1.0].
    """

    ticker: str
    should_exit: bool = False
    composite_score: float = 0.0

    # Individual signal scores
    volume_without_advance: float = 0.0
    new_dilutive_filing: float = 0.0
    spread_expansion: float = 0.0
    large_block_sells: float = 0.0
    price_below_vwap: float = 0.0
    minutes_since_entry: float = 0.0
    peak_drawdown: float = 0.0

    # Metadata
    timestamp: datetime | None = None

    def to_dict(self) -> dict[str, float]:
        """Export signal scores for logging/journal."""
        return {
            "volume_without_advance": round(self.volume_without_advance, 3),
            "new_dilutive_filing": round(self.new_dilutive_filing, 3),
            "spread_expansion": round(self.spread_expansion, 3),
            "large_block_sells": round(self.large_block_sells, 3),
            "price_below_vwap": round(self.price_below_vwap, 3),
            "minutes_since_entry": round(self.minutes_since_entry, 3),
            "peak_drawdown": round(self.peak_drawdown, 3),
            "composite": round(self.composite_score, 3),
            "should_exit": self.should_exit,
        }


# ── Detector ────────────────────────────────────────────────────


class DistributionDetector:
    """
    Pure-Python distribution detector for post-entry monitoring.

    Zero LLM calls, <1ms latency. Designed for PROMOTIONAL_EARLY
    positions where distribution can begin at any moment.

    Usage:
        detector = DistributionDetector()
        signal = detector.analyze(
            ticker="PUMP", current_price=5.20, entry_price=5.00,
            current_volume=50000, entry_volume=40000,
            peak_price=5.50, current_spread=0.05, entry_spread=0.02,
            vwap=5.10, minutes_held=45,
        )
        if signal.should_exit:
            # Trigger EXIT
    """

    def __init__(
        self,
        exit_threshold: float = 0.50,
        weights: dict[str, float] | None = None,
    ) -> None:
        self._exit_threshold = exit_threshold
        self._weights = weights or dict(DISTRIBUTION_WEIGHTS)

    def analyze(
        self,
        ticker: str,
        current_price: float,
        entry_price: float,
        # Volume
        current_volume: int = 0,
        entry_volume: int = 0,
        # Peak tracking
        peak_price: float = 0.0,
        # Spread
        current_spread: float = 0.0,
        entry_spread: float = 0.0,
        # Order book
        bid_size: int = 0,
        ask_size: int = 0,
        # VWAP
        vwap: float = 0.0,
        # Time
        minutes_held: int = 0,
        # Filing
        has_new_dilutive_filing: bool = False,
    ) -> DistributionSignal:
        """
        Compute all 7 distribution signals and composite score.

        All inputs are optional with safe defaults. Missing data
        produces 0.0 for that signal (conservative = less trigger-happy).

        Returns DistributionSignal with should_exit=True if composite > threshold.
        """
        sig = DistributionSignal(
            ticker=ticker,
            timestamp=datetime.now(timezone.utc),
        )

        # 1. Volume without advance — high volume + flat/declining price
        sig.volume_without_advance = self._volume_without_advance(
            current_price, entry_price, current_volume, entry_volume,
        )

        # 2. New dilutive filing — 424B5 filed since entry
        sig.new_dilutive_filing = 1.0 if has_new_dilutive_filing else 0.0

        # 3. Spread expansion — spread widened vs entry
        sig.spread_expansion = self._spread_expansion(
            current_spread, entry_spread, entry_price,
        )

        # 4. Large block sells — ask >> bid
        sig.large_block_sells = self._large_block_sells(bid_size, ask_size)

        # 5. Price below VWAP
        sig.price_below_vwap = self._price_below_vwap(
            current_price, vwap, entry_price,
        )

        # 6. Minutes since entry — time decay for pump plays
        sig.minutes_since_entry = self._time_decay(minutes_held)

        # 7. Peak drawdown — how far from peak
        sig.peak_drawdown = self._peak_drawdown(
            current_price, peak_price, entry_price,
        )

        # Composite
        sig.composite_score = self._composite(sig)
        sig.should_exit = sig.composite_score > self._exit_threshold

        return sig

    # ── Individual Signals ─────────────────────────────────────

    @staticmethod
    def _volume_without_advance(
        current_price: float,
        entry_price: float,
        current_volume: int,
        entry_volume: int,
    ) -> float:
        """
        Detect churning: high volume but price hasn't advanced.
        Institutions are selling into retail buying.

        Score 0.0 = healthy (volume + price advance)
        Score 1.0 = classic distribution (3x+ volume, price flat/down)
        """
        if entry_price <= 0 or entry_volume <= 0 or current_volume <= 0:
            return 0.0

        # Volume ratio: how much more volume than entry
        vol_ratio = current_volume / entry_volume
        if vol_ratio < 1.5:
            return 0.0  # Volume not elevated enough

        # Price advance from entry
        price_advance_pct = (current_price - entry_price) / entry_price

        # High volume + flat/declining = churning
        if price_advance_pct <= 0:
            # Price hasn't advanced at all — volume is distribution
            return min(1.0, vol_ratio / 3.0)

        if price_advance_pct < 0.02:
            # Price barely moved despite volume — suspicious
            return min(1.0, (vol_ratio / 3.0) * 0.7)

        return 0.0  # Volume + price advance = healthy

    @staticmethod
    def _spread_expansion(
        current_spread: float,
        entry_spread: float,
        entry_price: float,
    ) -> float:
        """
        Detect market maker withdrawal: spread widening vs entry.

        Score 0.0 = spread normal
        Score 1.0 = spread 3x+ entry (market makers pulling back)
        """
        if entry_price <= 0:
            return 0.0

        if entry_spread > 0 and current_spread > 0:
            ratio = current_spread / entry_spread
            if ratio >= 3.0:
                return 1.0
            if ratio >= 2.0:
                return 0.6
            if ratio >= 1.5:
                return 0.3
            return 0.0

        # Absolute check: spread > 2% of price = bad
        if entry_price > 0 and current_spread > 0:
            spread_pct = current_spread / entry_price
            if spread_pct >= 0.03:
                return 1.0
            if spread_pct >= 0.02:
                return 0.6
            if spread_pct >= 0.01:
                return 0.3

        return 0.0

    @staticmethod
    def _large_block_sells(bid_size: int, ask_size: int) -> float:
        """
        Detect institutional selling: ask_size >> bid_size.

        Score 0.0 = balanced
        Score 1.0 = ask 4x+ bid (heavy distribution)
        """
        if bid_size <= 0 or ask_size <= 0:
            return 0.0

        ratio = ask_size / bid_size
        if ratio <= 1.5:
            return 0.0
        if ratio >= 4.0:
            return 1.0
        # Linear: 1.5 → 0.0, 4.0 → 1.0
        return min(1.0, max(0.0, (ratio - 1.5) / 2.5))

    @staticmethod
    def _price_below_vwap(
        current_price: float,
        vwap: float,
        entry_price: float,
    ) -> float:
        """
        Price below VWAP = institutional selling pressure.

        Score 0.0 = above VWAP (healthy)
        Score 1.0 = far below VWAP (thesis broken)
        """
        if vwap <= 0 or current_price <= 0 or entry_price <= 0:
            return 0.0

        if current_price >= vwap:
            return 0.0

        pct_below = (vwap - current_price) / entry_price
        return min(1.0, max(0.0, pct_below / 0.03))

    @staticmethod
    def _time_decay(minutes_held: int) -> float:
        """
        Pump plays fade with time. Most promotional runs exhaust
        within 60-90 minutes of market open.

        Score 0.0 = first 30 minutes (peak window)
        Score 1.0 = >90 minutes (pump exhausted)
        """
        if minutes_held <= 0:
            return 0.0
        if minutes_held <= 30:
            return 0.0
        if minutes_held >= 90:
            return 1.0
        # Linear: 30 → 0.0, 90 → 1.0
        return (minutes_held - 30) / 60.0

    @staticmethod
    def _peak_drawdown(
        current_price: float,
        peak_price: float,
        entry_price: float,
    ) -> float:
        """
        How far price has dropped from its peak since entry.

        Score 0.0 = at or near peak
        Score 1.0 = >10% drawdown from peak
        """
        if peak_price <= 0 or current_price <= 0 or entry_price <= 0:
            return 0.0

        if current_price >= peak_price:
            return 0.0

        drawdown_pct = (peak_price - current_price) / peak_price
        # Scale: 0% → 0.0, 10% → 1.0
        return min(1.0, max(0.0, drawdown_pct / 0.10))

    def _composite(self, sig: DistributionSignal) -> float:
        """Compute weighted composite distribution score."""
        scores = {
            "volume_without_advance": sig.volume_without_advance,
            "new_dilutive_filing": sig.new_dilutive_filing,
            "spread_expansion": sig.spread_expansion,
            "large_block_sells": sig.large_block_sells,
            "price_below_vwap": sig.price_below_vwap,
            "minutes_since_entry": sig.minutes_since_entry,
            "peak_drawdown": sig.peak_drawdown,
        }
        weighted = sum(
            scores.get(k, 0.0) * self._weights.get(k, 0.0)
            for k in self._weights
        )
        total_weight = sum(self._weights.values())
        return weighted / total_weight if total_weight > 0 else 0.0
