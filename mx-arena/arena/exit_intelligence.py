"""
Exit Intelligence — port of production exit strategies for arena simulation.

4 strategies from src/execution/exit_strategies.py, adapted to work purely
from bar data (no WebSocket, no LLM). Each returns should_exit / should_tighten
with a confidence score. Aggregated via any-of voting (D122 parallel mode).

Sprint 3: Brings arena from 8.5 to 9.0 by matching production exit timing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .fill_model import Bar

logger = logging.getLogger(__name__)


@dataclass
class ExitResult:
    """Result from one exit strategy evaluation."""
    strategy: str
    should_exit: bool = False
    should_tighten: bool = False
    confidence: float = 0.0
    details: dict = field(default_factory=dict)


# ── Strategy 1: Velocity Exit ────────────────────────────────────────

# Phase-specific velocity thresholds (% per minute of entry price)
_VELOCITY_THRESHOLDS = {
    "IGNITION": 0.005,   # First 5 min: 0.5%/min required
    "THRUST": 0.002,     # 5-15 min: 0.2%/min
    "CRUISE": 0.0005,    # 15-30 min: 0.05%/min
    "DECAY": 0.0,        # 30+ min: any negative concerning
}


def velocity_exit(
    bars: list[Bar],
    entry_price: float,
    minutes_held: float,
) -> ExitResult:
    """
    Detect trend exhaustion from bar-over-bar velocity.

    Computes 3-bar rolling velocity = (close[-1] - close[-3]) / (entry × 3).
    Compares to phase-specific thresholds.
    """
    result = ExitResult(strategy="velocity")

    if len(bars) < 3 or entry_price <= 0:
        return result

    price_start = bars[-3].close
    price_end = bars[-1].close
    velocity = (price_end - price_start) / (entry_price * 3.0)

    # Phase classification
    if minutes_held < 5:
        phase = "IGNITION"
    elif minutes_held < 15:
        phase = "THRUST"
    elif minutes_held < 30:
        phase = "CRUISE"
    else:
        phase = "DECAY"

    threshold = _VELOCITY_THRESHOLDS[phase]
    result.details = {"velocity": round(velocity, 6), "phase": phase, "threshold": threshold}

    if velocity < threshold:
        if velocity < 0:
            if phase == "IGNITION":
                result.should_tighten = True
                result.confidence = min(1.0, abs(velocity) / max(threshold, 0.001))
            else:
                result.should_exit = True
                result.confidence = min(1.0, abs(velocity) / max(threshold, 0.001))
        else:
            result.should_tighten = True
            result.confidence = min(1.0, 1.0 - velocity / max(threshold, 0.001))

    return result


# ── Strategy 2: Volume Exhaustion ────────────────────────────────────

_VOL_EXIT_RATIO = 0.15    # 5-bar avg < 15% of entry bar -> EXIT
_VOL_TIGHTEN_RATIO = 0.30  # 5-bar avg < 30% of entry bar -> TIGHTEN


def volume_exhaustion(
    bars: list[Bar],
    entry_volume: int,
) -> ExitResult:
    """
    Detect volume fading from entry-bar peak.

    Computes ratio of recent 5-bar average volume to entry bar volume.
    """
    result = ExitResult(strategy="volume_exhaustion")

    if len(bars) < 3 or entry_volume <= 0:
        return result

    recent = bars[-5:] if len(bars) >= 5 else bars[-3:]
    volumes = [b.volume for b in recent if b.volume > 0]
    if not volumes:
        return result

    avg_vol = sum(volumes) / len(volumes)
    ratio = avg_vol / entry_volume
    result.details = {"volume_ratio": round(ratio, 4), "avg_volume": int(avg_vol)}

    # Trend detection (optional confidence boost)
    trend_declining = False
    if len(bars) >= 6:
        recent_3 = sum(b.volume for b in bars[-3:]) / 3
        prior_3 = sum(b.volume for b in bars[-6:-3]) / 3
        trend_declining = recent_3 < prior_3

    if ratio < _VOL_EXIT_RATIO:
        result.should_exit = True
        result.confidence = min(1.0, (_VOL_EXIT_RATIO - ratio) / _VOL_EXIT_RATIO + 0.5)
    elif ratio < _VOL_TIGHTEN_RATIO:
        result.should_tighten = True
        result.confidence = min(
            1.0, (_VOL_TIGHTEN_RATIO - ratio) / (_VOL_TIGHTEN_RATIO - _VOL_EXIT_RATIO)
        )

    if trend_declining and (result.should_exit or result.should_tighten):
        result.confidence = min(1.0, result.confidence + 0.15)

    return result


# ── Strategy 3: Gratitude Exit (R-Multiple Decay) ────────────────────

_INITIAL_R = 3.0
_DECAY_PER_MIN = 0.05
_FLOOR_R = 0.75
_EXIT_MULT = 1.5


def gratitude_exit(
    entry_price: float,
    current_price: float,
    stop_loss: float,
    minutes_held: float,
    decay_per_min: float | None = None,
) -> ExitResult:
    """
    Time-decaying R-multiple target.

    Starts ambitious (3R), decays toward floor (0.75R) over time.
    Exit when unrealized R >= threshold * 1.5 (grab profit before it fades).
    """
    result = ExitResult(strategy="gratitude")

    one_r = entry_price - stop_loss
    if one_r <= 0 or entry_price <= 0:
        return result

    unrealized_r = (current_price - entry_price) / one_r
    decay = decay_per_min if decay_per_min is not None else _DECAY_PER_MIN
    threshold = max(_FLOOR_R, _INITIAL_R - decay * minutes_held)

    result.details = {
        "unrealized_r": round(unrealized_r, 3),
        "threshold": round(threshold, 3),
        "exit_target": round(threshold * _EXIT_MULT, 3),
    }

    if unrealized_r <= 0:
        return result

    if unrealized_r >= threshold * _EXIT_MULT:
        result.should_exit = True
        result.confidence = min(1.0, unrealized_r / (threshold * _EXIT_MULT))
    elif unrealized_r >= threshold:
        result.should_tighten = True
        result.confidence = min(1.0, (unrealized_r - threshold) / (threshold * 0.5))

    return result


# ── Strategy 4: Alpha Decay Oracle ───────────────────────────────────

_DEFAULT_NULL_CURVE = [
    (5, 3.0), (10, 2.0), (15, 1.5), (20, 1.0),
    (30, 0.5), (45, 0.2), (60, 0.0),
]
_THIN_ALPHA = 0.5
_MIN_EVAL_MINUTES = 5


def _interpolate_null(minutes: float, curve: list[tuple] | None = None) -> float:
    """Linear interpolation on the null curve."""
    pts = curve or _DEFAULT_NULL_CURVE
    if minutes <= pts[0][0]:
        return pts[0][1]
    if minutes >= pts[-1][0]:
        return pts[-1][1]
    for i in range(len(pts) - 1):
        t0, v0 = pts[i]
        t1, v1 = pts[i + 1]
        if t0 <= minutes <= t1:
            frac = (minutes - t0) / (t1 - t0)
            return v0 + frac * (v1 - v0)
    return 0.0


def alpha_decay_oracle(
    entry_price: float,
    current_price: float,
    minutes_since_open: float,
    durability_shift: float = 0.0,
) -> ExitResult:
    """
    Compare observed return to expected return from null curve.

    Alpha = observed_return - expected_return.
    Negative alpha = underperforming expectations -> exit.
    """
    result = ExitResult(strategy="alpha_oracle")

    if entry_price <= 0 or minutes_since_open < _MIN_EVAL_MINUTES:
        return result

    current_return = ((current_price - entry_price) / entry_price) * 100
    null_return = _interpolate_null(minutes_since_open + durability_shift)
    alpha = current_return - null_return

    result.details = {
        "current_return_pct": round(current_return, 3),
        "null_return_pct": round(null_return, 3),
        "alpha": round(alpha, 3),
    }

    if alpha <= 0:
        result.should_exit = True
        result.confidence = min(1.0, 0.5 + abs(alpha) * 0.1)
    elif alpha < _THIN_ALPHA:
        result.should_tighten = True
        result.confidence = min(1.0, 0.3 + (_THIN_ALPHA - alpha) * 0.4)

    return result


# ── Aggregator: Parallel Strategy Voting ─────────────────────────────

_MIN_STRATEGIES_FOR_EXIT = 2  # D122: at least 2 must agree for EXIT


def evaluate_exit(
    bars_since_entry: list[Bar],
    entry_price: float,
    current_price: float,
    stop_loss: float,
    entry_volume: int,
    minutes_held: float,
    minutes_since_open: float,
    decay_per_min: float | None = None,
    durability_shift: float = 0.0,
) -> tuple[str, float, list[ExitResult]]:
    """
    Run all 4 exit strategies and aggregate via any-of voting.

    Returns:
        (action, confidence, results) where action is "EXIT", "TIGHTEN", or "HOLD"
    """
    results = [
        velocity_exit(bars_since_entry, entry_price, minutes_held),
        volume_exhaustion(bars_since_entry, entry_volume),
        gratitude_exit(entry_price, current_price, stop_loss, minutes_held, decay_per_min),
        alpha_decay_oracle(entry_price, current_price, minutes_since_open, durability_shift),
    ]

    exits = [r for r in results if r.should_exit and r.confidence > 0]
    tightens = [r for r in results if r.should_tighten and r.confidence > 0]

    if len(exits) >= _MIN_STRATEGIES_FOR_EXIT:
        max_conf = max(r.confidence for r in exits)
        return ("EXIT", max_conf, results)
    elif tightens:
        max_conf = max(r.confidence for r in tightens)
        return ("TIGHTEN", max_conf, results)

    return ("HOLD", 0.0, results)
