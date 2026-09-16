"""Limit-price-aware fill model.

Block A.2 of the limit-aware-fill session. Closes the structural
defect documented in `data/audits/limit_vs_baropen_residuals.parquet`:
arena was using bar.open at the entry minute as the fill price for
LIMIT orders, which is structurally wrong by 100-1500 bps for
production OTO and RESCAN limit orders.

Per the brief's minimum-viable scope:
  - If order.type == "limit" and order.limit_price is set:
      * If bar.low <= limit <= bar.high (BUY): fill at limit
      * If bar.low <= limit <= bar.high (SELL): fill at limit
      * Otherwise: failed-to-fill (return None)
  - If order.type == "market": fall back to bar-anchored fill
    (existing FillModel logic)

DEFERRED (per brief scope discipline):
  - Multi-bar limit-fill modeling: if a buy limit is below bar.low
    at submit minute, prod might fill in a LATER minute when price
    drops. This MVP does not look forward. Trades that would have
    filled in a later minute will be marked failed-to-fill in arena.
  - Time-in-force semantics (DAY vs GTC): MVP treats every limit
    as expiring after one bar.
  - Slippage application to the limit price: per discipline of
    doc 64 (calibration v2 ships identity), no per-tier multiplier
    is applied. Limit fills at the limit price exactly.

API mirrors arena.fill_model.AlpacaFillModel.try_fill so it can be
swapped in via composition.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from random import Random
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class LimitAwareFillResult:
    price: float
    qty: int
    event: str = "fill"           # fill | failed_to_fill | bar_anchored_fallback
    fill_path: str = "limit"      # limit | bar_open | unfillable


class LimitAwareFillModel:
    """Wraps an underlying market fill model; pre-empts limit orders
    with limit-price-aware fills."""

    def __init__(self, *, base_market_fill_model) -> None:
        self._base = base_market_fill_model

    def try_fill(self, *, order, bar, bid: float, ask: float, rng: Random) -> Optional[object]:
        """Attempt a fill against the bar.

        For limit orders inside the bar's range, fill at limit price.
        For limit orders outside the bar's range, return None (failed-to-fill).
        For market orders, delegate to the base model.
        """
        if order.type == "limit" and order.limit_price is not None:
            limit = float(order.limit_price)
            bar_low = float(bar.low)
            bar_high = float(bar.high)
            # For BUY: limit acceptable if bar.low <= limit (we'd happily
            # buy at our limit if price came down)
            # For SELL: limit acceptable if bar.high >= limit (we'd happily
            # sell at our limit if price came up)
            if order.side == "buy":
                if bar_low <= limit:
                    return _build_fill(price=limit, qty=order.qty - order.filled_qty)
                # Limit is below the bar's lowest tick — would have to wait
                logger.debug(
                    "limit-aware: BUY %s @ $%.4f below bar.low $%.4f → failed-to-fill",
                    order.symbol, limit, bar_low,
                )
                return None
            elif order.side == "sell":
                if bar_high >= limit:
                    return _build_fill(price=limit, qty=order.qty - order.filled_qty)
                logger.debug(
                    "limit-aware: SELL %s @ $%.4f above bar.high $%.4f → failed-to-fill",
                    order.symbol, limit, bar_high,
                )
                return None

        # Market orders or anything else: delegate to base
        return self._base.try_fill(order, bar, bid, ask, rng)


def _build_fill(*, price: float, qty: int):
    """Build a Fill object compatible with arena.fill_model.Fill shape."""
    from arena.fill_model import Fill
    return Fill(price=round(price, 4), qty=max(0, int(qty)), event="fill")
