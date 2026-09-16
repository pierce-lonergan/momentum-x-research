"""
D212: Float Rotation Tracker

Tracks cumulative volume / float_shares ratio to detect when the tradeable
float has fully turned over. Above 1.0x, every shareholder has theoretically
exited — the stock is pure momentum. Above 3.0x, exhaustion is imminent.

Usage:
    tracker = FloatRotationTracker()
    result = tracker.compute("SILO", cumulative_volume=5_000_000, float_shares=2_000_000)
    # result.level == RotationLevel.HYPER_ROTATED (2.5x)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class RotationLevel(str, Enum):
    NORMAL = "normal"               # < 1.0x — float not yet turned over
    ROTATED = "rotated"             # 1.0-2.0x — full turnover, momentum phase
    HYPER_ROTATED = "hyper_rotated" # 2.0-3.0x — aggressive turnover, late momentum
    EXHAUSTED = "exhausted"         # > 3.0x — everyone who wanted in is in


@dataclass(frozen=True)
class FloatRotationResult:
    ticker: str
    cumulative_volume: int
    float_shares: int
    rotation_ratio: float
    level: RotationLevel


class FloatRotationTracker:
    """Stateless calculator — compute rotation from volume and float."""

    ROTATED_THRESHOLD = 1.0
    HYPER_THRESHOLD = 2.0
    EXHAUSTED_THRESHOLD = 3.0

    def compute(
        self,
        ticker: str,
        cumulative_volume: int,
        float_shares: int | None,
    ) -> FloatRotationResult | None:
        """Compute float rotation ratio. Returns None if float unknown."""
        if float_shares is None or float_shares <= 0:
            return None
        if cumulative_volume <= 0:
            return None

        ratio = cumulative_volume / float_shares

        if ratio >= self.EXHAUSTED_THRESHOLD:
            level = RotationLevel.EXHAUSTED
        elif ratio >= self.HYPER_THRESHOLD:
            level = RotationLevel.HYPER_ROTATED
        elif ratio >= self.ROTATED_THRESHOLD:
            level = RotationLevel.ROTATED
        else:
            level = RotationLevel.NORMAL

        return FloatRotationResult(
            ticker=ticker,
            cumulative_volume=cumulative_volume,
            float_shares=float_shares,
            rotation_ratio=round(ratio, 2),
            level=level,
        )
