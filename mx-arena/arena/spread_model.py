"""
Bid-ask spread reconstruction from bar data.

Alpaca paper trading fills at NBBO but doesn't provide historical quotes.
We reconstruct realistic spreads from price tier, time of day, and volume.

Spread = base_spread_bps × time_of_day_factor × volume_factor

Calibrated from empirical small-cap momentum stock spreads:
- $0.50-$3:  15-50 bps (wide, illiquid penny stocks)
- $3-$10:    5-20 bps (typical small-cap gappers)
- $10-$50:   2-10 bps (mid-cap)
- $50+:      1-5 bps (large-cap)

Time-of-day U-shape (well-established market microstructure):
- 09:30-09:45: 2.5x (opening auction chop)
- 09:45-10:30: 1.5x (settling)
- 10:30-15:00: 1.0x (midday)
- 15:00-15:45: 1.3x (approaching close)
- 15:45-16:00: 2.0x (closing auction)
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")


class SpreadModel:
    """Reconstructs bid-ask spreads from bar data for fill simulation."""

    def __init__(
        self,
        *,
        calibration: dict[str, float] | None = None,
    ) -> None:
        """Optional calibration multipliers per price tier.

        Block 2.1 finding (docs/research-log/59_spread_calibration.md):
        replay against 4 production trades from 2026-04-22 → 2026-04-28
        showed arena's base spreads were too tight for sub-$3 stocks.
        Calibration is a per-tier scalar applied to the half-spread:
            actual_half_spread = base × tier_multiplier
        Default = 1.0 (uncalibrated, matches pre-Block 2.1 behavior).
        Multipliers are LOADED FROM data when available; fall back to
        the conservative 1.0 default on any missing tier.

        Tier keys: "sub_1", "sub_3", "sub_10", "sub_50", "above_50".
        """
        self.calibration = calibration or {}

    def _tier_key(self, price: float) -> str:
        if price < 1.0:
            return "sub_1"
        if price < 3.0:
            return "sub_3"
        if price < 10.0:
            return "sub_10"
        if price < 50.0:
            return "sub_50"
        return "above_50"

    def get_spread(
        self,
        price: float,
        volume: float,
        timestamp: datetime,
        rvol: float = 1.0,
    ) -> float:
        """
        Compute estimated half-spread for a given price/time/volume.

        Returns the HALF spread (distance from mid to bid or ask).
        Full spread = 2 × get_spread().

        Args:
            price: Current stock price.
            volume: Current bar volume.
            timestamp: Current time (for U-shape).
            rvol: Relative volume multiplier (higher = tighter spreads).

        Returns:
            Half-spread in dollars.
        """
        # Base spread from price tier (basis points)
        if price < 1.0:
            base_bps = 80.0
        elif price < 3.0:
            base_bps = 30.0
        elif price < 10.0:
            base_bps = 12.0
        elif price < 50.0:
            base_bps = 5.0
        else:
            base_bps = 2.0

        # Volume factor: higher volume = tighter spreads
        vol_factor = max(0.3, 1.0 - min(rvol / 20.0, 0.7))

        # Time of day U-shape — MUST use ET, not UTC.
        # P0 fix: timestamp is UTC from SimClock. At 09:30 ET (13:30 UTC),
        # raw .hour=13 gives midday 1.0x. Need 2.5x opening multiplier.
        et = timestamp.astimezone(_ET) if timestamp.tzinfo else timestamp
        hour = et.hour + et.minute / 60.0
        if hour < 9.75:
            tod = 2.5
        elif hour < 10.5:
            tod = 1.5
        elif hour < 15.0:
            tod = 1.0
        elif hour < 15.75:
            tod = 1.3
        else:
            tod = 2.0

        spread_bps = base_bps * vol_factor * tod
        # Block 2.1 calibration multiplier: scale the bps by the
        # empirically-fit per-tier residual. Default 1.0 = uncalibrated.
        tier_mult = float(self.calibration.get(self._tier_key(price), 1.0))
        spread_bps *= tier_mult
        # Convert bps to dollars (half-spread)
        return price * spread_bps / 10000.0 / 2.0

    def get_bid_ask(
        self,
        price: float,
        volume: float,
        timestamp: datetime,
        rvol: float = 1.0,
    ) -> tuple[float, float]:
        """Return (bid, ask) around the given mid price."""
        half = self.get_spread(price, volume, timestamp, rvol)
        return (round(price - half, 4), round(price + half, 4))
