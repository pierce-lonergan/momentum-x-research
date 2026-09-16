"""
MOMENTUM-X Parallel Exit Strategies (D109 Phase 4, D110 Phase 5)

Six independent exit strategies that evaluate positions each Phase 3 cycle,
plus a cross-position ContagionNetwork.  Any single strategy can trigger an
exit or tighten action independently — no consensus required (Any-Of architecture).

During the configuration freeze, these strategies COMPUTE and LOG what they
would do without changing actual trading behavior. Post-freeze, setting
``parallel_strategies_active = True`` in ExitIntelligenceConfig activates them.

Strategies (D109):
  1. VelocityEngine            — 3-min rolling price velocity with time-based phases
  2. PullbackClassifier        — State machine tracking retracement depth (stateful)
  3. VolumeExhaustionStrategy  — Volume ratio analysis (buyers leaving)
  4. GratitudeExitStrategy     — Time-decaying R-multiple profit capture

Strategies (D110):
  5. CatalystHalfLifeStrategy  — Catalyst-type-specific exit timing (information-side)
  6. AlphaDecayOracle          — Real-time edge estimation vs null return curve

Cross-Position Layer (D110):
  ContagionNetwork             — Propagates exit signals to correlated positions

All strategies treat the ``bars`` list as READ-ONLY — slicing creates new lists.
The bar history is a shared reference from ExitIntelligenceManager._bar_history.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)


# ── Result Dataclass ─────────────────────────────────────────────


@dataclass
class ExitStrategyResult:
    """Output from a single exit strategy evaluation."""

    strategy_name: str
    should_exit: bool = False
    should_tighten: bool = False
    confidence: float = 0.0  # 0.0-1.0
    details: dict = field(default_factory=dict)
    reasoning: str = ""

    def to_log_dict(self) -> dict:
        """Return strategy-specific fields for nested logging."""
        d: dict = {
            "exit": self.should_exit,
            "tighten": self.should_tighten,
            "confidence": round(self.confidence, 3),
        }
        for k, v in self.details.items():
            d[k] = round(v, 4) if isinstance(v, float) else v
        return d


# ── Strategy 1: Velocity Engine ──────────────────────────────────


class VelocityPhase(str, Enum):
    """Time-based trade lifecycle phases."""

    IGNITION = "IGNITION"  # 0-5 min: initial thrust
    THRUST = "THRUST"      # 5-15 min: sustained move
    CRUISE = "CRUISE"      # 15-30 min: slowing
    DECAY = "DECAY"        # 30+ min: momentum exhausted


# Per-phase velocity thresholds (price change per minute / entry_price)
# Below threshold → tighten; below threshold AND negative → exit
_PHASE_VELOCITY_THRESHOLDS: dict[VelocityPhase, float] = {
    VelocityPhase.IGNITION: 0.005,   # 0.5%/min — strong initial thrust required
    VelocityPhase.THRUST: 0.002,     # 0.2%/min — sustained but can slow
    VelocityPhase.CRUISE: 0.0005,    # 0.05%/min — just don't fall
    VelocityPhase.DECAY: 0.0,        # any negative is concerning
}


class VelocityEngine:
    """3-minute rolling price velocity with time-based phase classification.

    Phase is determined by elapsed time since entry (not by velocity level).
    Each phase has a velocity threshold — falling below triggers TIGHTEN,
    falling below with negative velocity triggers EXIT.
    """

    def evaluate(
        self,
        entry_price: float,
        minutes_held: float,
        bars: list[dict] | None,
    ) -> ExitStrategyResult:
        """Compute velocity and classify against phase threshold."""
        result = ExitStrategyResult(strategy_name="velocity")

        if not bars or len(bars) < 3 or entry_price <= 0:
            result.details = {
                "velocity_per_min": 0.0,
                "phase": self._phase_for_time(minutes_held).value,
                "bars_used": len(bars) if bars else 0,
            }
            return result

        # 3-bar rolling velocity (normalized by entry price)
        price_start = bars[-3].get("c", 0)
        price_end = bars[-1].get("c", 0)
        if price_start <= 0 or price_end <= 0:
            result.details = {
                "velocity_per_min": 0.0,
                "phase": self._phase_for_time(minutes_held).value,
                "bars_used": len(bars),
            }
            return result

        velocity = (price_end - price_start) / (entry_price * 3.0)
        phase = self._phase_for_time(minutes_held)
        threshold = _PHASE_VELOCITY_THRESHOLDS[phase]

        # Also compute 5-bar velocity for comparison logging
        velocity_5bar = 0.0
        if len(bars) >= 5:
            p5_start = bars[-5].get("c", 0)
            if p5_start > 0:
                velocity_5bar = (price_end - p5_start) / (entry_price * 5.0)

        result.details = {
            "velocity_per_min": velocity,
            "velocity_5bar": velocity_5bar,
            "phase": phase.value,
            "threshold": threshold,
            "bars_used": len(bars),
        }

        if velocity < threshold:
            if velocity < 0:
                # Below threshold AND negative — exit signal
                # During IGNITION, only tighten (early noise tolerance)
                if phase == VelocityPhase.IGNITION:
                    result.should_tighten = True
                    result.confidence = min(1.0, abs(velocity) / max(threshold, 0.001))
                    result.reasoning = (
                        f"IGNITION velocity negative ({velocity:.4f}/min) "
                        f"— tighten only (early noise tolerance)"
                    )
                else:
                    result.should_exit = True
                    result.confidence = min(1.0, abs(velocity) / max(threshold, 0.001))
                    result.reasoning = (
                        f"{phase.value} velocity {velocity:.4f}/min below "
                        f"threshold {threshold:.4f} and negative — exit"
                    )
            else:
                # Below threshold but still positive — tighten
                result.should_tighten = True
                result.confidence = min(1.0, 1.0 - velocity / max(threshold, 0.001))
                result.reasoning = (
                    f"{phase.value} velocity {velocity:.4f}/min below "
                    f"threshold {threshold:.4f} — tighten"
                )

        return result

    @staticmethod
    def _phase_for_time(minutes_held: float) -> VelocityPhase:
        """Determine phase from elapsed time (time-based, not velocity-based)."""
        if minutes_held < 5:
            return VelocityPhase.IGNITION
        elif minutes_held < 15:
            return VelocityPhase.THRUST
        elif minutes_held < 30:
            return VelocityPhase.CRUISE
        else:
            return VelocityPhase.DECAY


# ── Strategy 2: First Pullback Classifier ────────────────────────


class PullbackState(str, Enum):
    """Pullback tracking state machine states."""

    ADVANCING = "ADVANCING"
    PULLBACK = "PULLBACK"
    RECOVERED = "RECOVERED"
    EXHAUSTED = "EXHAUSTED"


@dataclass
class PullbackTrackerState:
    """Per-position mutable state for the pullback classifier."""

    current_state: PullbackState = PullbackState.ADVANCING
    peak_since_entry: float = 0.0
    advance_size: float = 0.0  # peak - entry at time of first pullback
    pullback_low: float = float("inf")
    cycles_in_pullback: int = 0


class PullbackClassifier:
    """State machine tracking first pullback depth relative to advance size.

    Thresholds are RELATIVE to the initial advance (peak - entry), not
    absolute percentages. A stock that advanced $0.30 needs a $0.09
    pullback (30%) to enter PULLBACK; a $0.15 pullback (50%) to trigger
    EXHAUSTED.  This scales correctly across different move sizes.
    """

    # Fraction of advance that defines each transition
    PULLBACK_RETRACEMENT = 0.30   # 30% retracement → PULLBACK
    EXHAUSTED_RETRACEMENT = 0.50  # 50% retracement → EXHAUSTED
    RECOVERY_FRACTION = 0.90      # recover to 90% of peak → RECOVERED
    MAX_PULLBACK_CYCLES = 5       # cycles in PULLBACK without recovery → EXHAUSTED
    # D122: Minimum advance before retracement tracking begins.
    # Without this, a $0.01 advance on a $5 stock (0.2%) counts as a valid
    # advance, and a $0.005 pullback is a "50% retracement" triggering EXHAUSTED.
    # Signal history showed 325/355 (92%) cycles in EXHAUSTED state because
    # flat positions with tiny noise-level advances hit 50% retracement
    # immediately. Set to 1% (0.01) of entry price as minimum advance.
    MIN_ADVANCE_PCT = 0.01        # 1% minimum advance before tracking

    def evaluate(
        self,
        entry_price: float,
        current_price: float,
        peak_price: float,
        tracker: PullbackTrackerState,
    ) -> ExitStrategyResult:
        """Evaluate pullback state and update tracker in-place."""
        result = ExitStrategyResult(strategy_name="pullback")

        if entry_price <= 0 or current_price <= 0 or peak_price <= 0:
            result.details = {
                "state": tracker.current_state.value,
                "retracement_pct": 0.0,
            }
            return result

        # Update peak tracking
        if current_price > tracker.peak_since_entry:
            tracker.peak_since_entry = current_price

        # Use the peak we've tracked (may differ from position peak_price
        # if we started tracking after position was already open)
        peak = max(tracker.peak_since_entry, peak_price)
        advance = peak - entry_price
        pullback_depth = peak - current_price

        # D122: Minimum advance gate. Don't compute retracement until the
        # advance is meaningful (>= MIN_ADVANCE_PCT of entry). A stock that
        # goes +0.2% then -0.1% should not register as "50% retracement" —
        # it should register as "hasn't moved meaningfully yet."
        min_advance = entry_price * self.MIN_ADVANCE_PCT
        if advance < min_advance:
            advance_meaningful = False
            retracement_pct = 0.0
        else:
            advance_meaningful = True
            retracement_pct = pullback_depth / advance if advance > 0 else 0.0

        result.details = {
            "state": tracker.current_state.value,
            "retracement_pct": retracement_pct,
            "advance": advance,
            "pullback_depth": pullback_depth,
            "peak": peak,
            "cycles_in_pullback": tracker.cycles_in_pullback,
        }

        # EXHAUSTED is terminal — no further transitions
        # D122: Only honor EXHAUSTED if the advance was meaningful.
        # If the advance was < MIN_ADVANCE_PCT, the EXHAUSTED state was
        # triggered by noise, not genuine momentum exhaustion. Reset to
        # ADVANCING so the strategy can re-evaluate on a real move.
        if tracker.current_state == PullbackState.EXHAUSTED:
            if advance_meaningful:
                result.should_exit = True
                result.confidence = 1.0
                result.reasoning = "Pullback EXHAUSTED (terminal) — exit"
                return result
            else:
                # Reset — EXHAUSTED was triggered by noise-level advance
                tracker.current_state = PullbackState.ADVANCING
                tracker.cycles_in_pullback = 0
                tracker.pullback_low = float("inf")
                result.details["state"] = PullbackState.ADVANCING.value

        # State transitions
        if tracker.current_state in (PullbackState.ADVANCING, PullbackState.RECOVERED):
            # Check for new high
            if current_price >= peak:
                tracker.current_state = PullbackState.ADVANCING
                tracker.cycles_in_pullback = 0
                tracker.pullback_low = float("inf")

            # Check for pullback entry (D122: requires meaningful advance)
            elif advance_meaningful and retracement_pct >= self.PULLBACK_RETRACEMENT:
                tracker.current_state = PullbackState.PULLBACK
                tracker.advance_size = advance
                tracker.pullback_low = min(tracker.pullback_low, current_price)
                tracker.cycles_in_pullback = 1
                result.details["state"] = PullbackState.PULLBACK.value
                # Signal tighten on pullback entry
                result.should_tighten = True
                result.confidence = min(1.0, retracement_pct / self.EXHAUSTED_RETRACEMENT)
                result.reasoning = (
                    f"Entered pullback: {retracement_pct:.1%} retracement"
                )

        elif tracker.current_state == PullbackState.PULLBACK:
            tracker.pullback_low = min(tracker.pullback_low, current_price)
            tracker.cycles_in_pullback += 1

            # Check for exhaustion by depth (D122: requires meaningful advance)
            if advance_meaningful and retracement_pct >= self.EXHAUSTED_RETRACEMENT:
                tracker.current_state = PullbackState.EXHAUSTED
                result.should_exit = True
                result.confidence = min(1.0, retracement_pct / self.EXHAUSTED_RETRACEMENT)
                result.reasoning = (
                    f"Pullback exhausted: {retracement_pct:.1%} retracement "
                    f"exceeds {self.EXHAUSTED_RETRACEMENT:.0%} threshold"
                )
                result.details["state"] = PullbackState.EXHAUSTED.value
                return result

            # Check for exhaustion by time
            if tracker.cycles_in_pullback > self.MAX_PULLBACK_CYCLES:
                tracker.current_state = PullbackState.EXHAUSTED
                result.should_exit = True
                result.confidence = 0.8
                result.reasoning = (
                    f"Pullback exhausted: {tracker.cycles_in_pullback} cycles "
                    f"without recovery"
                )
                result.details["state"] = PullbackState.EXHAUSTED.value
                return result

            # Check for recovery
            if peak > 0 and current_price >= peak * self.RECOVERY_FRACTION:
                tracker.current_state = PullbackState.RECOVERED
                tracker.cycles_in_pullback = 0
                result.details["state"] = PullbackState.RECOVERED.value
            else:
                # Still in pullback — tighten signal
                result.should_tighten = True
                result.confidence = min(1.0, retracement_pct / self.EXHAUSTED_RETRACEMENT)
                result.reasoning = (
                    f"In pullback: {retracement_pct:.1%} retracement, "
                    f"cycle {tracker.cycles_in_pullback}/{self.MAX_PULLBACK_CYCLES}"
                )

        result.details["state"] = tracker.current_state.value
        result.details["cycles_in_pullback"] = tracker.cycles_in_pullback
        return result


# ── Strategy 3: Volume Exhaustion ────────────────────────────────


class VolumeExhaustionStrategy:
    """Detects when buying volume has dried up relative to entry.

    Compares recent average volume to the entry-bar volume. When buyers
    leave (ratio drops below 0.30), the move is likely over.
    """

    TIGHTEN_RATIO = 0.30   # 5-bar avg < 30% of entry bar → tighten
    EXIT_RATIO = 0.15      # 5-bar avg < 15% of entry bar → exit

    def evaluate(
        self,
        entry_volume: int,
        bars: list[dict] | None,
    ) -> ExitStrategyResult:
        """Compute volume ratio and check for exhaustion."""
        result = ExitStrategyResult(strategy_name="volume_exhaustion")

        if not bars or len(bars) < 3 or entry_volume <= 0:
            result.details = {"volume_ratio": 0.0, "bars_used": len(bars) if bars else 0}
            return result

        # 5-bar (or available) average volume
        recent_bars = bars[-5:] if len(bars) >= 5 else bars[-3:]
        volumes = [b.get("v", 0) for b in recent_bars if b.get("v", 0) > 0]

        if not volumes:
            result.details = {"volume_ratio": 0.0, "bars_used": len(bars)}
            return result

        avg_volume = sum(volumes) / len(volumes)
        ratio = avg_volume / entry_volume

        # Volume trend: are last 3 bars declining vs prior 3?
        trend_declining = False
        if len(bars) >= 6:
            recent_3 = [b.get("v", 0) for b in bars[-3:] if b.get("v", 0) > 0]
            prior_3 = [b.get("v", 0) for b in bars[-6:-3] if b.get("v", 0) > 0]
            if recent_3 and prior_3:
                trend_declining = (sum(recent_3) / len(recent_3)) < (
                    sum(prior_3) / len(prior_3)
                )

        result.details = {
            "volume_ratio": ratio,
            "avg_volume": avg_volume,
            "entry_volume": entry_volume,
            "trend_declining": trend_declining,
            "bars_used": len(volumes),
        }

        if ratio < self.EXIT_RATIO:
            result.should_exit = True
            result.confidence = min(1.0, (self.EXIT_RATIO - ratio) / self.EXIT_RATIO + 0.5)
            result.reasoning = (
                f"Volume exhausted: ratio {ratio:.2f} < {self.EXIT_RATIO} "
                f"(avg {avg_volume:.0f} vs entry {entry_volume})"
            )
        elif ratio < self.TIGHTEN_RATIO:
            result.should_tighten = True
            result.confidence = min(
                1.0, (self.TIGHTEN_RATIO - ratio) / (self.TIGHTEN_RATIO - self.EXIT_RATIO)
            )
            result.reasoning = (
                f"Volume fading: ratio {ratio:.2f} < {self.TIGHTEN_RATIO} "
                f"(avg {avg_volume:.0f} vs entry {entry_volume})"
            )

        # Boost confidence if trend is also declining
        if trend_declining and (result.should_exit or result.should_tighten):
            result.confidence = min(1.0, result.confidence + 0.15)

        return result


# ── Strategy 4: R-Multiple Gratitude Exit ────────────────────────


class GratitudeExitStrategy:
    """Time-decaying R-multiple profit capture.

    The gratitude threshold starts at 3.0R (ambitious early target) and
    decays 0.05R per minute held, flooring at 0.75R. When unrealized
    profit exceeds the threshold → tighten. When it exceeds 1.5× the
    threshold → exit (take the money).

    The 0.75R floor ensures captured profit exceeds transaction costs
    even on high-spread sub-$3 stocks.
    """

    INITIAL_R = 3.0       # Starting threshold
    DECAY_PER_MIN = 0.05  # R threshold drops 0.05 per minute
    FLOOR_R = 0.75        # Never go below this
    EXIT_MULTIPLIER = 1.5  # Exit when unrealized ≥ threshold × this

    def evaluate(
        self,
        entry_price: float,
        stop_loss: float,
        current_price: float,
        minutes_held: float,
        decay_per_min: float | None = None,
    ) -> ExitStrategyResult:
        """Check if the trade has given us 'enough' relative to risk.

        D118: ``decay_per_min`` overrides the class constant when the
        catalyst profiler is active.  Higher decay = grab-and-go (EPHEMERAL),
        lower decay = hold longer (PERMANENT_REVALUATION).
        """
        result = ExitStrategyResult(strategy_name="gratitude")

        one_r = entry_price - stop_loss
        if one_r <= 0 or entry_price <= 0:
            result.details = {
                "unrealized_r": 0.0,
                "threshold": self.INITIAL_R,
                "one_r": 0.0,
                "minutes_held": minutes_held,
            }
            return result

        _decay = decay_per_min if decay_per_min is not None else self.DECAY_PER_MIN
        unrealized_r = (current_price - entry_price) / one_r
        threshold = max(self.FLOOR_R, self.INITIAL_R - _decay * minutes_held)

        result.details = {
            "unrealized_r": unrealized_r,
            "threshold": threshold,
            "one_r": one_r,
            "minutes_held": minutes_held,
        }

        if unrealized_r <= 0:
            # Not profitable — no gratitude signal
            return result

        if unrealized_r >= threshold * self.EXIT_MULTIPLIER:
            result.should_exit = True
            result.confidence = min(1.0, unrealized_r / (threshold * self.EXIT_MULTIPLIER))
            result.reasoning = (
                f"Gratitude EXIT: {unrealized_r:.1f}R captured ≥ "
                f"{threshold * self.EXIT_MULTIPLIER:.1f}R "
                f"(threshold {threshold:.2f}R × {self.EXIT_MULTIPLIER})"
            )
        elif unrealized_r >= threshold:
            result.should_tighten = True
            result.confidence = min(
                1.0, (unrealized_r - threshold) / (threshold * (self.EXIT_MULTIPLIER - 1.0))
            )
            result.reasoning = (
                f"Gratitude TIGHTEN: {unrealized_r:.1f}R captured ≥ "
                f"threshold {threshold:.2f}R"
            )

        return result


# ── Strategy 5: Catalyst Information Half-Life (D110) ────────────


# Default half-life table: conservative domain-knowledge estimates.
# Median = expected minutes for 50% of catalyst's price impact to decay.
# Configurable via ExitIntelligenceConfig.catalyst_half_life_table.
DEFAULT_CATALYST_HALF_LIFE_TABLE: dict[str, dict[str, float]] = {
    "fda_approval": {"median": 180, "p25": 60, "p75": 390},
    "earnings_beat": {"median": 120, "p25": 45, "p75": 390},
    "contract_deal": {"median": 90, "p25": 30, "p75": 240},
    "technical_breakout": {"median": 25, "p25": 12, "p75": 60},
    "social_media_promo": {"median": 8, "p25": 4, "p75": 15},
    "sec_filing_dilutive": {"median": 12, "p25": 5, "p75": 25},
    "unknown": {"median": 20, "p25": 8, "p75": 45},
}

# ManipulationClassifier phase adjustments to half-life.
_MANIPULATION_HALF_LIFE_MULTIPLIER: dict[str, float] = {
    "PROMOTIONAL_EARLY": 0.6,
    "PROMOTIONAL_LATE": 0.3,
}


class CatalystHalfLifeStrategy:
    """Information-side exit timing based on catalyst shelf life.

    Assigns a half-life to each trade based on catalyst type (FDA = 180 min,
    social media pump = 8 min, technical breakout = 25 min).  When the
    catalyst's information has been absorbed, the remaining price support
    is noise, not edge.

    **Exit trigger requires BOTH time AND velocity:**
        should_exit = minutes_held > half_life × 1.5 AND velocity_per_min ≤ 0

    This prevents cutting winners still actively advancing past their
    expected shelf life.  Tighten is time-only (precautionary).
    """

    TIGHTEN_MULTIPLIER = 0.8  # Tighten at 80% of half-life
    EXIT_MULTIPLIER = 1.5     # Exit at 150% of half-life (+ velocity gate)

    def __init__(
        self,
        half_life_table: dict[str, dict[str, float]] | None = None,
    ) -> None:
        self._table = half_life_table or DEFAULT_CATALYST_HALF_LIFE_TABLE

    def _effective_half_life(
        self,
        catalyst_type: str,
        manipulation_phase: str,
    ) -> float:
        """Return the effective median half-life in minutes."""
        entry = self._table.get(catalyst_type, self._table.get("unknown", {"median": 20}))
        median = entry.get("median", 20)
        multiplier = _MANIPULATION_HALF_LIFE_MULTIPLIER.get(manipulation_phase, 1.0)
        return median * multiplier

    def evaluate(
        self,
        minutes_held: float,
        velocity_per_min: float,
        catalyst_type: str = "unknown",
        manipulation_phase: str = "ORGANIC_MOMENTUM",
        half_life_override: float | None = None,
    ) -> ExitStrategyResult:
        """Check if catalyst shelf life is expiring.

        D118: ``half_life_override`` bypasses the static table lookup when the
        catalyst profiler provides an LLM-assessed half-life.
        """
        result = ExitStrategyResult(strategy_name="catalyst_half_life")

        if half_life_override is not None and half_life_override > 0:
            half_life = half_life_override
        else:
            half_life = self._effective_half_life(catalyst_type, manipulation_phase)
        tighten_deadline = half_life * self.TIGHTEN_MULTIPLIER
        exit_deadline = half_life * self.EXIT_MULTIPLIER
        minutes_remaining = max(0.0, exit_deadline - minutes_held)

        result.details = {
            "catalyst_type": catalyst_type,
            "manipulation_phase": manipulation_phase,
            "half_life_min": round(half_life, 1),
            "exit_deadline_min": round(exit_deadline, 1),
            "minutes_remaining": round(minutes_remaining, 1),
            "velocity_per_min": velocity_per_min,
        }

        if minutes_held >= exit_deadline and velocity_per_min <= 0:
            # Past exit deadline AND momentum is flat/negative — exit
            result.should_exit = True
            overshoot = (minutes_held - exit_deadline) / max(half_life, 1.0)
            result.confidence = min(1.0, 0.6 + overshoot * 0.4)
            result.reasoning = (
                f"Catalyst shelf life expired: {minutes_held:.0f} min held > "
                f"{exit_deadline:.0f} min deadline ({catalyst_type}, "
                f"half-life {half_life:.0f} min) AND velocity {velocity_per_min:.4f} ≤ 0"
            )
        elif minutes_held >= tighten_deadline:
            # Past tighten deadline — precautionary tighten (no velocity gate)
            result.should_tighten = True
            progress = (minutes_held - tighten_deadline) / max(exit_deadline - tighten_deadline, 1.0)
            result.confidence = min(1.0, 0.3 + progress * 0.4)
            result.reasoning = (
                f"Catalyst half-life approaching: {minutes_held:.0f} min held > "
                f"{tighten_deadline:.0f} min ({catalyst_type}, "
                f"half-life {half_life:.0f} min)"
            )

        return result


# ── Strategy 6: Alpha Decay Oracle v1 (D110) ────────────────────


# Default null return curve: domain-knowledge estimate.
# (minutes_since_open, expected_return_pct) — average gap-up stock return.
# Refined later from backtest --null-time output.
DEFAULT_NULL_CURVE: list[tuple[int, float]] = [
    (5, 3.0),
    (10, 2.0),
    (15, 1.5),
    (20, 1.0),
    (30, 0.5),
    (45, 0.2),
    (60, 0.0),
]


@dataclass
class AlphaMeasurement:
    """Single alpha observation for decay rate tracking."""

    minutes_since_open: float
    alpha: float


class AlphaDecayOracle:
    """Measures remaining alpha by comparing observed return vs null return.

    The null return curve represents the average return of all gap-up stocks
    at each minute after market open.  Alpha = observed_return - null_return.
    When alpha ≤ 0, the stock is no longer outperforming the base rate —
    the edge has been absorbed.

    v1 uses a single aggregate curve (no gap-bucket segmentation).
    The curve is a static lookup table from domain knowledge, refined
    later from backtest ``--null-time`` output.
    """

    THIN_ALPHA_THRESHOLD = 0.5  # Alpha < 0.5% → tighten (thin edge)
    MIN_EVALUATION_MINUTES = 5  # D122: Don't evaluate before first null curve point

    def __init__(
        self,
        null_curve: list[tuple[int, float]] | None = None,
        curve_override_fn: "Callable[[str], list[tuple[int, float]] | None] | None" = None,
    ) -> None:
        raw = null_curve or DEFAULT_NULL_CURVE
        # Sort by minutes for interpolation
        self._curve = sorted(raw, key=lambda x: x[0])
        # D214: Per-ticker curve override (from archetype exit system)
        self._curve_override_fn = curve_override_fn
        # Per-position history for decay rate computation
        self._history: dict[str, list[AlphaMeasurement]] = {}

    def _get_curve(self, ticker: str) -> list[tuple[int, float]]:
        """Get curve for ticker — archetype-specific if available, else default."""
        if self._curve_override_fn is not None:
            override = self._curve_override_fn(ticker)
            if override:
                return sorted(override, key=lambda x: x[0])
        return self._curve

    def _interpolate(self, minutes_since_open: float, curve: list[tuple[int, float]] | None = None) -> float:
        """Linearly interpolate the null return curve."""
        c = curve if curve is not None else self._curve
        if not c:
            return 0.0
        if minutes_since_open <= c[0][0]:
            return c[0][1]
        if minutes_since_open >= c[-1][0]:
            return c[-1][1]
        for i in range(len(c) - 1):
            t0, r0 = c[i]
            t1, r1 = c[i + 1]
            if t0 <= minutes_since_open <= t1:
                frac = (minutes_since_open - t0) / (t1 - t0) if t1 > t0 else 0.0
                return r0 + frac * (r1 - r0)
        return 0.0

    def _compute_decay_rate(self, ticker: str) -> float:
        """Compute alpha decay rate from last 5 measurements (%/min)."""
        history = self._history.get(ticker, [])
        if len(history) < 2:
            return 0.0
        window = history[-5:]
        dt = window[-1].minutes_since_open - window[0].minutes_since_open
        if dt <= 0:
            return 0.0
        return (window[-1].alpha - window[0].alpha) / dt

    def evaluate(
        self,
        ticker: str,
        current_return_pct: float,
        minutes_since_open: float,
        durability_shift_minutes: float = 0.0,
    ) -> ExitStrategyResult:
        """Measure alpha and check if edge is exhausted.

        D118: ``durability_shift_minutes`` shifts the null curve lookup left
        (negative = expect faster decay) or right (positive = expect returns
        to persist longer).  PERMANENT_REVALUATION → +15 min shift,
        EPHEMERAL → -5 min shift.
        """
        result = ExitStrategyResult(strategy_name="alpha_oracle")

        # D122: Minimum evaluation time. At minute 0, observed return ≈ 0%
        # but the null curve says gap-up stocks should be at +3.0% by minute 5.
        # So alpha = 0% - 3.0% = -3.0%, triggering EXIT immediately on every
        # position. The oracle's premise is "compare observed return to what a
        # typical gap-up stock does" — at minute 0, there's no observation to
        # compare against. Return HOLD until the first null curve point.
        if minutes_since_open < self.MIN_EVALUATION_MINUTES:
            result.details = {
                "alpha_pct": 0.0,
                "null_return_pct": 0.0,
                "observed_return_pct": round(current_return_pct, 3),
                "decay_rate_per_min": 0.0,
                "minutes_remaining": -1,
                "skipped": "below minimum evaluation time",
            }
            return result

        _shifted_time = max(0.0, minutes_since_open - durability_shift_minutes)
        # D214: Use per-ticker archetype curve if available
        _ticker_curve = self._get_curve(ticker)
        null_return = self._interpolate(_shifted_time, _ticker_curve)
        alpha = current_return_pct - null_return

        # Track history
        if ticker not in self._history:
            self._history[ticker] = []
        self._history[ticker].append(
            AlphaMeasurement(minutes_since_open=minutes_since_open, alpha=alpha)
        )
        # Keep max 30 measurements per ticker
        if len(self._history[ticker]) > 30:
            self._history[ticker] = self._history[ticker][-30:]

        decay_rate = self._compute_decay_rate(ticker)

        # Predicted minutes remaining
        if decay_rate < 0 and alpha > 0:
            minutes_remaining = alpha / abs(decay_rate)
        else:
            minutes_remaining = float("inf")

        result.details = {
            "alpha_pct": round(alpha, 3),
            "null_return_pct": round(null_return, 3),
            "observed_return_pct": round(current_return_pct, 3),
            "decay_rate_per_min": round(decay_rate, 4),
            "minutes_remaining": round(minutes_remaining, 1) if minutes_remaining != float("inf") else -1,
        }

        if alpha <= 0:
            result.should_exit = True
            result.confidence = min(1.0, 0.5 + abs(alpha) * 0.1)
            result.reasoning = (
                f"Alpha exhausted: observed {current_return_pct:.1f}% vs "
                f"null {null_return:.1f}% → alpha {alpha:.2f}%"
            )
        elif alpha < self.THIN_ALPHA_THRESHOLD:
            result.should_tighten = True
            result.confidence = min(1.0, 0.3 + (self.THIN_ALPHA_THRESHOLD - alpha) * 0.4)
            result.reasoning = (
                f"Thin alpha: {alpha:.2f}% (< {self.THIN_ALPHA_THRESHOLD}%)"
            )

        return result

    def cleanup_position(self, ticker: str) -> None:
        """Remove history for a closed position."""
        self._history.pop(ticker, None)

    def prune_stale(self, active_tickers: set[str]) -> None:
        """Remove history for tickers no longer in active positions."""
        stale = [t for t in self._history if t not in active_tickers]
        for t in stale:
            self._history.pop(t, None)


# ── Cross-Position: Contagion Network (D110) ────────────────────


@dataclass
class ContagionSignal:
    """Recorded exit/tighten signal from a source position."""

    ticker: str
    sector: str
    catalyst_type: str
    gap_pct: float
    confidence: float
    is_exit: bool  # True = EXIT, False = TIGHTEN
    timestamp: datetime


@dataclass
class ContagionResult:
    """Contagion propagation result for a target position."""

    intensity: float = 0.0
    should_tighten: bool = False
    source_count: int = 0
    sources: list[str] = field(default_factory=list)  # source tickers

    def to_log_dict(self) -> dict:
        """Return dict for nested logging."""
        return {
            "intensity": round(self.intensity, 3),
            "tighten": self.should_tighten,
            "source_count": self.source_count,
            "sources": self.sources,
        }


class ContagionNetwork:
    """Cross-position signal propagation for correlated exit detection.

    When Position A fires a TIGHTEN/EXIT signal, contagion is propagated
    to correlated positions (same sector, catalyst type) with intensity
    proportional to estimated correlation.

    Contagion is a TIGHTEN-only signal — it warns correlated positions
    to tighten stops, but does not override their own exit logic.

    Design constraints (from evaluator feedback):
    - Decay window: 10 minutes (configurable), not 5
    - First position to deteriorate has no contagion sources (bootstrapping)
    - Log ``contagion_active_positions`` to measure coverage
    """

    def __init__(
        self,
        decay_minutes: float = 10.0,
        threshold: float = 0.3,
    ) -> None:
        self.decay_minutes = decay_minutes
        self.threshold = threshold
        self._active_signals: dict[str, ContagionSignal] = {}

    def record_signal(
        self,
        ticker: str,
        sector: str,
        catalyst_type: str,
        gap_pct: float,
        confidence: float,
        is_exit: bool,
        timestamp: datetime | None = None,
    ) -> None:
        """Record when a position fires an exit/tighten signal."""
        self._active_signals[ticker] = ContagionSignal(
            ticker=ticker,
            sector=sector,
            catalyst_type=catalyst_type,
            gap_pct=gap_pct,
            confidence=confidence,
            is_exit=is_exit,
            timestamp=timestamp or datetime.now(timezone.utc),
        )

    def _estimate_correlation(
        self,
        source: ContagionSignal,
        target_sector: str,
        target_catalyst: str,
        target_gap_pct: float,
    ) -> float:
        """Estimate pairwise correlation from observable features."""
        rho = 0.0
        # Same sector: +0.4
        if source.sector and target_sector and source.sector == target_sector:
            rho += 0.4
        # Same catalyst type: +0.2
        if source.catalyst_type and target_catalyst and source.catalyst_type == target_catalyst:
            rho += 0.2
        # Similar gap size (within 2x): +0.1
        if source.gap_pct > 0 and target_gap_pct > 0:
            ratio = source.gap_pct / target_gap_pct
            if 0.5 <= ratio <= 2.0:
                rho += 0.1
        return min(rho, 0.8)

    def propagate(
        self,
        target_ticker: str,
        target_sector: str,
        target_catalyst: str,
        target_gap_pct: float,
        current_time: datetime | None = None,
    ) -> ContagionResult:
        """Compute contagion intensity for target from all active sources."""
        now = current_time or datetime.now(timezone.utc)
        max_intensity = 0.0
        sources: list[str] = []

        for source_ticker, signal in self._active_signals.items():
            # No self-contagion
            if source_ticker == target_ticker:
                continue

            # Time decay
            elapsed = (now - signal.timestamp).total_seconds() / 60.0
            if elapsed >= self.decay_minutes:
                continue
            time_decay = max(0.0, 1.0 - elapsed / self.decay_minutes)

            # Correlation
            rho = self._estimate_correlation(
                signal, target_sector, target_catalyst, target_gap_pct
            )

            # Intensity = confidence × correlation × time_decay
            intensity = signal.confidence * rho * time_decay
            if intensity > 0:
                sources.append(source_ticker)
            if intensity > max_intensity:
                max_intensity = intensity

        return ContagionResult(
            intensity=max_intensity,
            should_tighten=max_intensity >= self.threshold,
            source_count=len(sources),
            sources=sources,
        )

    def active_position_count(self) -> int:
        """Number of positions with active contagion signals."""
        now = datetime.now(timezone.utc)
        count = 0
        for signal in self._active_signals.values():
            elapsed = (now - signal.timestamp).total_seconds() / 60.0
            if elapsed < self.decay_minutes:
                count += 1
        return count

    def clear(self) -> None:
        """Remove all recorded signals."""
        self._active_signals.clear()


# ── Orchestrator: Parallel Exit Engine ───────────────────────────


class ParallelExitEngine:
    """Runs all 6 exit strategies and aggregates results.

    Holds per-ticker PullbackTrackerState for the stateful pullback
    classifier.  State is auto-pruned for closed positions.

    D110 additions: CatalystHalfLifeStrategy (strategy 5),
    AlphaDecayOracle (strategy 6), and ContagionNetwork (cross-position).
    """

    def __init__(
        self,
        half_life_table: dict[str, dict[str, float]] | None = None,
        null_curve: list[tuple[int, float]] | None = None,
        contagion_decay_minutes: float = 10.0,
        contagion_threshold: float = 0.3,
        archetype_strategy: "ArchetypeExitStrategy | None" = None,
    ) -> None:
        self._velocity = VelocityEngine()
        self._pullback = PullbackClassifier()
        self._volume_exhaustion = VolumeExhaustionStrategy()
        self._gratitude = GratitudeExitStrategy()
        self._catalyst_half_life = CatalystHalfLifeStrategy(half_life_table)
        # D214: If archetype strategy is provided, feed its curves to the oracle
        _curve_override = archetype_strategy.get_archetype_null_curve if archetype_strategy else None
        self._alpha_oracle = AlphaDecayOracle(null_curve, curve_override_fn=_curve_override)
        self._archetype = archetype_strategy
        self._contagion = ContagionNetwork(contagion_decay_minutes, contagion_threshold)
        self._pullback_state: dict[str, PullbackTrackerState] = {}

    def evaluate_all(
        self,
        ticker: str,
        entry_price: float,
        stop_loss: float,
        current_price: float,
        peak_price: float,
        minutes_held: float,
        entry_volume: int,
        bars: list[dict] | None,
        *,
        catalyst_type: str = "unknown",
        manipulation_phase: str = "ORGANIC_MOMENTUM",
        minutes_since_open: float = 0.0,
        catalyst_half_life_minutes: float | None = None,
        catalyst_gratitude_decay: float | None = None,
        catalyst_durability_shift: float = 0.0,
    ) -> list[ExitStrategyResult]:
        """Run all 6 strategies and return results.

        D110 adds ``catalyst_type``, ``manipulation_phase``, and
        ``minutes_since_open`` as keyword-only arguments to preserve
        backward compatibility with existing callers.

        D118 adds ``catalyst_half_life_minutes``, ``catalyst_gratitude_decay``,
        and ``catalyst_durability_shift`` — position-level overrides from the
        Entry Catalyst Profiler.  When non-default, these bypass static tables.
        """
        # Get or create pullback state
        if ticker not in self._pullback_state:
            self._pullback_state[ticker] = PullbackTrackerState(
                peak_since_entry=peak_price if peak_price > 0 else entry_price,
            )
        tracker = self._pullback_state[ticker]

        # Run velocity first — its result feeds into CatalystHalfLife
        velocity_result = self._velocity.evaluate(entry_price, minutes_held, bars)
        velocity_per_min = velocity_result.details.get("velocity_per_min", 0.0)

        # Compute current return for Alpha Oracle
        current_return_pct = (
            (current_price - entry_price) / entry_price * 100.0
            if entry_price > 0
            else 0.0
        )

        results = [
            velocity_result,
            self._pullback.evaluate(entry_price, current_price, peak_price, tracker),
            self._volume_exhaustion.evaluate(entry_volume, bars),
            self._gratitude.evaluate(
                entry_price, stop_loss, current_price, minutes_held,
                decay_per_min=catalyst_gratitude_decay,
            ),
            self._catalyst_half_life.evaluate(
                minutes_held, velocity_per_min, catalyst_type, manipulation_phase,
                half_life_override=catalyst_half_life_minutes,
            ),
            self._alpha_oracle.evaluate(
                ticker, current_return_pct, minutes_since_open,
                durability_shift_minutes=catalyst_durability_shift,
            ),
        ]
        return results

    def evaluate_contagion(
        self,
        target_ticker: str,
        target_sector: str,
        target_catalyst: str,
        target_gap_pct: float,
        current_time: datetime | None = None,
    ) -> ContagionResult:
        """Compute contagion intensity for a target position."""
        return self._contagion.propagate(
            target_ticker, target_sector, target_catalyst,
            target_gap_pct, current_time,
        )

    def record_contagion_signal(
        self,
        ticker: str,
        sector: str,
        catalyst_type: str,
        gap_pct: float,
        confidence: float,
        is_exit: bool,
        timestamp: datetime | None = None,
    ) -> None:
        """Record an exit/tighten signal for contagion propagation."""
        self._contagion.record_signal(
            ticker, sector, catalyst_type, gap_pct,
            confidence, is_exit, timestamp,
        )

    def cleanup_position(self, ticker: str) -> None:
        """Remove state for a closed position."""
        self._pullback_state.pop(ticker, None)
        self._alpha_oracle.cleanup_position(ticker)

    def prune_stale(self, active_tickers: set[str]) -> None:
        """Remove state for tickers no longer in the active positions set."""
        stale = [t for t in self._pullback_state if t not in active_tickers]
        for t in stale:
            self._pullback_state.pop(t, None)
        self._alpha_oracle.prune_stale(active_tickers)

    @staticmethod
    def to_log_dict(
        results: list[ExitStrategyResult],
        contagion: ContagionResult | None = None,
        contagion_active_positions: int = 0,
    ) -> dict:
        """Build nested log dict + flat summary fields.

        Returns::

            {
                "parallel_strategies": {
                    "velocity": {...},
                    "pullback": {...},
                    "catalyst_half_life": {...},
                    "alpha_oracle": {...},
                    ...
                },
                "contagion": {...},
                "contagion_active_positions": int,
                "anyof_would_exit": bool,
                "anyof_would_tighten": bool,
            }
        """
        nested: dict[str, dict] = {}
        for r in results:
            nested[r.strategy_name] = r.to_log_dict()

        d: dict = {
            "parallel_strategies": nested,
            "anyof_would_exit": any(r.should_exit for r in results),
            "anyof_would_tighten": any(
                r.should_tighten or r.should_exit for r in results
            ),
        }

        if contagion is not None:
            d["contagion"] = contagion.to_log_dict()
            # If contagion triggers tighten, include in anyof
            if contagion.should_tighten:
                d["anyof_would_tighten"] = True
        d["contagion_active_positions"] = contagion_active_positions

        return d
