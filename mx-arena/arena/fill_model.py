"""
Fill models for order execution simulation.

AlpacaFillModel replicates Alpaca paper trading behavior:
- Market buy fills at ask, market sell fills at bid
- Limit buy fills when limit_price >= ask
- Limit sell fills when limit_price <= bid
- Stop triggers when bar trades through stop_price, then fills as market
- 10% random partial fill probability (seeded for determinism)
- Trailing stop tracks high-water mark

RealisticFillModel adds what Alpaca paper trading explicitly omits:
- Volume-limited fills (can't fill > 5% of bar volume)
- Market impact (large orders move price)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from random import Random
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Bar:
    """Single OHLCV bar."""
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: float = 0.0
    trade_count: int = 0


@dataclass
class Fill:
    """Result of a successful fill attempt."""
    price: float
    qty: int
    event: str = "fill"  # "fill" or "partial_fill"


class AlpacaFillModel:
    """
    Replicates Alpaca paper trading fill behavior.

    From Alpaca docs:
    - Market orders fill at current ask (buy) or bid (sell)
    - Limit orders check marketability against NBBO
    - Stops trigger on price crossing consolidated tape
    - 10% random partial fill probability
    - Trailing stops only trigger during regular hours
    """

    PARTIAL_FILL_PROBABILITY = 0.10

    def try_fill(
        self,
        order: "OrderState",
        bar: Bar,
        bid: float,
        ask: float,
        rng: Random,
    ) -> Optional[Fill]:
        """
        Attempt to fill an order against the current bar + NBBO.

        Args:
            order: The order to evaluate.
            bar: Current minute bar (for stop trigger via high/low).
            bid: Current bid price (from spread model).
            ask: Current ask price (from spread model).
            rng: Seeded RNG for deterministic partial fills.

        Returns:
            Fill if the order should execute, None otherwise.
        """
        remaining = order.qty - order.filled_qty
        if remaining <= 0:
            return None

        if order.type == "market":
            price = ask if order.side == "buy" else bid
            qty = self._maybe_partial(remaining, rng)
            return Fill(
                price=round(price, 4),
                qty=qty,
                event="fill" if qty >= remaining else "partial_fill",
            )

        elif order.type == "limit":
            if order.side == "buy" and order.limit_price is not None:
                if order.limit_price >= ask:
                    qty = self._maybe_partial(remaining, rng)
                    # P1 fix: Fill at NBBO ask, no price improvement.
                    # Alpaca paper fills at top-of-book, not better.
                    return Fill(
                        price=round(ask, 4),
                        qty=qty,
                        event="fill" if qty >= remaining else "partial_fill",
                    )
            elif order.side == "sell" and order.limit_price is not None:
                if order.limit_price <= bid:
                    qty = self._maybe_partial(remaining, rng)
                    # P1 fix: Fill at NBBO bid, no price improvement.
                    return Fill(
                        price=round(bid, 4),
                        qty=qty,
                        event="fill" if qty >= remaining else "partial_fill",
                    )

        elif order.type == "stop":
            triggered = False
            if order.stop_price is not None:
                if order.side == "sell" and bar.low <= order.stop_price:
                    triggered = True
                elif order.side == "buy" and bar.high >= order.stop_price:
                    triggered = True

            if triggered:
                price = ask if order.side == "buy" else bid
                qty = remaining  # Stops fill fully once triggered
                return Fill(
                    price=round(price, 4),
                    qty=qty,
                    event="fill",
                )

        elif order.type == "stop_limit":
            if order.stop_price is not None and not order.stop_triggered:
                if order.side == "sell" and bar.low <= order.stop_price:
                    order.stop_triggered = True
                elif order.side == "buy" and bar.high >= order.stop_price:
                    order.stop_triggered = True

            if order.stop_triggered and order.limit_price is not None:
                if order.side == "buy" and order.limit_price >= ask:
                    qty = self._maybe_partial(remaining, rng)
                    return Fill(
                        price=round(min(order.limit_price, ask), 4),
                        qty=qty,
                        event="fill" if qty >= remaining else "partial_fill",
                    )
                elif order.side == "sell" and order.limit_price <= bid:
                    qty = self._maybe_partial(remaining, rng)
                    return Fill(
                        price=round(max(order.limit_price, bid), 4),
                        qty=qty,
                        event="fill" if qty >= remaining else "partial_fill",
                    )

        elif order.type == "trailing_stop":
            if order.side == "sell" and order.trail_percent is not None:
                # Update high-water mark
                if order.hwm is None or bar.high > order.hwm:
                    order.hwm = bar.high
                trigger = order.hwm * (1.0 - order.trail_percent / 100.0)
                if bar.low <= trigger:
                    return Fill(
                        price=round(bid, 4),
                        qty=remaining,
                        event="fill",
                    )

        return None

    def _maybe_partial(self, remaining: int, rng: Random) -> int:
        """10% chance of partial fill (Alpaca paper trading behavior)."""
        if rng.random() < self.PARTIAL_FILL_PROBABILITY and remaining > 1:
            return rng.randint(1, remaining - 1)
        return remaining


class RealisticFillModel(AlpacaFillModel):
    """
    Adds realism that Alpaca paper trading explicitly omits:
    - Volume-limited fills (can't fill > MAX_PARTICIPATION of bar volume)
    - Market impact (large orders move price proportionally)
    """

    MAX_PARTICIPATION = 0.05  # Can't be > 5% of bar volume
    IMPACT_COEFFICIENT = 0.1  # Price impact per unit of participation

    def try_fill(self, order, bar, bid, ask, rng) -> Optional[Fill]:
        fill = super().try_fill(order, bar, bid, ask, rng)
        if fill is None:
            return None

        # Volume limit
        if bar.volume > 0:
            max_qty = max(1, int(bar.volume * self.MAX_PARTICIPATION))
            if fill.qty > max_qty:
                fill.qty = max_qty
                fill.event = "partial_fill"

            # Market impact
            participation = fill.qty / max(bar.volume, 1)
            impact = participation * self.IMPACT_COEFFICIENT
            if order.side == "buy":
                fill.price = round(fill.price * (1.0 + impact), 4)
            else:
                fill.price = round(fill.price * (1.0 - impact), 4)

        return fill
