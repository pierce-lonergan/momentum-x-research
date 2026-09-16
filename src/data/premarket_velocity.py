"""
D212: Premarket Velocity Tracker

Tracks periodic price/volume/spread snapshots during premarket (4-9:30 AM ET)
to compute velocity and acceleration. Identifies stocks that are accelerating
into the open (strong) vs fading into the open (weak).

Usage:
    tracker = PremarketVelocityTracker()
    # Called every ~2 min during Phase 1 scan loop:
    tracker.record_snapshot("SILO", price=2.50, volume=500000, bid=2.48, ask=2.52)
    tracker.record_snapshot("SILO", price=2.65, volume=800000, bid=2.63, ask=2.67)

    result = tracker.compute_velocity("SILO")
    # result.classification == VelocityClassification.ACCELERATING
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)


class VelocityClassification(str, Enum):
    ACCELERATING = "accelerating"       # Price up + volume increasing
    DECELERATING = "decelerating"       # Price up but volume decreasing
    STABLE = "stable"                   # Little movement
    FADING_INTO_OPEN = "fading"         # Price dropping from premarket highs


@dataclass
class VelocitySnapshot:
    timestamp: datetime
    price: float
    volume: int
    bid: float
    ask: float


@dataclass(frozen=True)
class PremarketVelocityResult:
    ticker: str
    classification: VelocityClassification
    price_velocity_per_min: float       # $/min (positive = rising)
    volume_acceleration: float          # Ratio: second_half_rate / first_half_rate
    spread_initial_pct: float           # Spread % at first snapshot
    spread_current_pct: float           # Spread % at latest snapshot
    spread_trend: float                 # Negative = narrowing (bullish)
    snapshot_count: int
    duration_minutes: float


class PremarketVelocityTracker:
    """Accumulates premarket snapshots and computes velocity metrics."""

    def __init__(self) -> None:
        self._snapshots: dict[str, list[VelocitySnapshot]] = {}

    def record_snapshot(
        self,
        ticker: str,
        price: float,
        volume: int,
        bid: float,
        ask: float,
    ) -> None:
        """Record a premarket snapshot. Call every scan loop iteration (~2 min)."""
        if ticker not in self._snapshots:
            self._snapshots[ticker] = []
        self._snapshots[ticker].append(VelocitySnapshot(
            timestamp=datetime.now(timezone.utc),
            price=price,
            volume=volume,
            bid=bid,
            ask=ask,
        ))

    def compute_velocity(self, ticker: str) -> PremarketVelocityResult | None:
        """Compute velocity metrics for a ticker. Needs >= 2 snapshots."""
        snaps = self._snapshots.get(ticker, [])
        if len(snaps) < 2:
            return None

        first = snaps[0]
        last = snaps[-1]
        duration_sec = (last.timestamp - first.timestamp).total_seconds()
        if duration_sec <= 0:
            return None
        duration_min = duration_sec / 60.0

        # Price velocity: $/min
        price_delta = last.price - first.price
        price_velocity = price_delta / duration_min

        # Volume acceleration: split snapshots in half, compare volume rates
        mid = len(snaps) // 2
        first_half = snaps[:mid]
        second_half = snaps[mid:]

        if len(first_half) >= 2 and len(second_half) >= 2:
            fh_vol_rate = (first_half[-1].volume - first_half[0].volume) / max(
                (first_half[-1].timestamp - first_half[0].timestamp).total_seconds(), 1
            )
            sh_vol_rate = (second_half[-1].volume - second_half[0].volume) / max(
                (second_half[-1].timestamp - second_half[0].timestamp).total_seconds(), 1
            )
            vol_accel = sh_vol_rate / max(fh_vol_rate, 1.0) if fh_vol_rate > 0 else 1.0
        else:
            vol_accel = 1.0

        # Spread dynamics
        spread_initial = (first.ask - first.bid) / first.price if first.price > 0 else 0
        spread_current = (last.ask - last.bid) / last.price if last.price > 0 else 0
        spread_trend = spread_current - spread_initial  # Negative = narrowing

        # Classification
        price_change_pct = price_delta / first.price if first.price > 0 else 0
        if price_change_pct < -0.02:
            classification = VelocityClassification.FADING_INTO_OPEN
        elif price_change_pct > 0.01 and vol_accel > 1.2:
            classification = VelocityClassification.ACCELERATING
        elif price_change_pct > 0.01 and vol_accel < 0.8:
            classification = VelocityClassification.DECELERATING
        else:
            classification = VelocityClassification.STABLE

        result = PremarketVelocityResult(
            ticker=ticker,
            classification=classification,
            price_velocity_per_min=round(price_velocity, 4),
            volume_acceleration=round(vol_accel, 2),
            spread_initial_pct=round(spread_initial * 100, 3),
            spread_current_pct=round(spread_current * 100, 3),
            spread_trend=round(spread_trend * 100, 3),
            snapshot_count=len(snaps),
            duration_minutes=round(duration_min, 1),
        )

        # D221 Phase F: forward-only persistence (no-op when env-toggle is off).
        # Persists the computed result, NOT the raw snapshots — snapshots fire
        # every 2 min per ticker, would be 250k+ writes per session.
        from src.data._feature_persistence import persist_feature_row
        persist_feature_row("premarket_velocity", {
            "ticker": ticker,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "classification": result.classification.value,
            "price_velocity_per_min": result.price_velocity_per_min,
            "volume_acceleration": result.volume_acceleration,
            "spread_initial_pct": result.spread_initial_pct,
            "spread_current_pct": result.spread_current_pct,
            "spread_trend": result.spread_trend,
            "snapshot_count": result.snapshot_count,
            "duration_minutes": result.duration_minutes,
        })

        return result

    def get_all_results(self) -> dict[str, PremarketVelocityResult]:
        """Compute velocity for all tracked tickers."""
        results = {}
        for ticker in self._snapshots:
            r = self.compute_velocity(ticker)
            if r is not None:
                results[ticker] = r
        return results

    def reset(self) -> None:
        """Clear all snapshots (call at session start)."""
        self._snapshots.clear()
