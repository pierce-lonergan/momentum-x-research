"""
MOMENTUM-X Stop Resubmitter

### ARCHITECTURAL CONTEXT
Node ID: execution.stop_resubmitter
Graph Link: docs/memory/graph_state.json → "execution.stop_resubmitter"

### RESEARCH BASIS
When a tranche exit fills and the stop ratchets (T1→breakeven, T2→T1 target),
the original bracket stop at Alpaca is stale. This module:
  1. Cancels the old stop order
  2. Submits a new stop-loss order at the ratcheted level
  3. Updates the position's stop_order_id for future ratchets

This ensures the protective stop always matches the current ratcheted level.

Ref: ADR-003 §2 (Stop Ratcheting: stop only moves UP)
Ref: ADR-021 (Tranche Wiring)

### CRITICAL INVARIANTS
1. Cancel MUST succeed before new stop is submitted (two-phase).
2. If cancel fails → old stop remains active (safety fallback).
3. If submit fails after cancel → log CRITICAL and flag for manual review.
4. New stop price MUST be >= old stop price (ratchet-up only).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class StopResubmitResult:
    """
    Result of a stop resubmission operation.

    Node ID: execution.stop_resubmitter.StopResubmitResult
    """
    ticker: str
    old_stop_price: float
    new_stop_price: float
    old_order_id: str
    new_order_id: str
    success: bool
    error: str | None = None


@dataclass
class TrackedStop:
    """
    Tracks the current active stop order for a position.

    Node ID: execution.stop_resubmitter.TrackedStop
    """
    ticker: str
    order_id: str
    stop_price: float
    qty: int


class StopResubmitter:
    """
    Manages stop-loss resubmission after tranche fills ratchet the stop level.

    Node ID: execution.stop_resubmitter
    Ref: ADR-003 §2 (Stop Management)

    Usage:
        resubmitter = StopResubmitter(client=alpaca_client)

        # Track the initial bracket stop
        resubmitter.register_stop("AAPL", order_id="stop-001", stop_price=145.0, qty=99)

        # After tranche T1 fills and stop ratchets to breakeven ($150):
        result = await resubmitter.resubmit("AAPL", new_stop_price=150.0, new_qty=66)
        # result.success == True, result.new_order_id == "stop-002"
    """

    # D108: Retry constants for Phase 2 (new stop submission)
    _MAX_RETRIES = 3
    _BACKOFF_BASE_S = 1.0  # 1s, 2s, 4s

    def __init__(self, client: Any) -> None:
        """
        Args:
            client: AlpacaDataClient instance for cancel/submit operations.
        """
        self._client = client
        self._stops: dict[str, TrackedStop] = {}  # ticker → TrackedStop

    def register_stop(
        self,
        ticker: str,
        order_id: str,
        stop_price: float,
        qty: int,
    ) -> None:
        """
        Register the initial stop order for a position.

        Called after bracket order submission to track the stop leg.

        Args:
            ticker: Stock symbol.
            order_id: Alpaca order ID of the stop leg.
            stop_price: Current stop-loss price.
            qty: Number of shares covered by stop.
        """
        self._stops[ticker] = TrackedStop(
            ticker=ticker,
            order_id=order_id,
            stop_price=stop_price,
            qty=qty,
        )
        logger.debug("Registered stop for %s: %s @ $%.2f (qty=%d)", ticker, order_id, stop_price, qty)

    async def resubmit(
        self,
        ticker: str,
        new_stop_price: float,
        new_qty: int | None = None,
    ) -> StopResubmitResult:
        """
        Cancel current stop and submit new one at ratcheted price.

        Two-phase operation:
          Phase 1: Cancel old stop order
          Phase 2: Submit new stop order at ratcheted level

        INVARIANT: new_stop_price >= old_stop_price (ratchet-up only).

        Args:
            ticker: Stock symbol.
            new_stop_price: Ratcheted stop price.
            new_qty: Remaining qty (None = use tracked qty).

        Returns:
            StopResubmitResult with success status and order IDs.

        Ref: ADR-003 §2 (Stop only ratchets UP)
        """
        tracked = self._stops.get(ticker)
        if tracked is None:
            return StopResubmitResult(
                ticker=ticker,
                old_stop_price=0.0,
                new_stop_price=new_stop_price,
                old_order_id="",
                new_order_id="",
                success=False,
                error=f"No tracked stop for {ticker}",
            )

        # INVARIANT: stop only ratchets up
        if new_stop_price < tracked.stop_price:
            return StopResubmitResult(
                ticker=ticker,
                old_stop_price=tracked.stop_price,
                new_stop_price=new_stop_price,
                old_order_id=tracked.order_id,
                new_order_id="",
                success=False,
                error=f"Ratchet violation: {new_stop_price} < {tracked.stop_price}",
            )

        qty = new_qty if new_qty is not None else tracked.qty
        # D121 BUG-R7: Guard against 0 or negative qty — would create
        # invalid stop order or broker rejection.
        if qty <= 0:
            logger.warning(
                "STOP RESUBMIT SKIP: %s qty=%d invalid — not resubmitting",
                ticker, qty,
            )
            return StopResubmitResult(
                ticker=ticker,
                old_stop_price=tracked.stop_price,
                new_stop_price=new_stop_price,
                old_order_id=tracked.order_id,
                new_order_id="",
                success=False,
                error=f"Invalid qty={qty}",
            )

        # Phase 1: Cancel old stop
        try:
            cancel_result = await self._client.cancel_order(tracked.order_id)
            if cancel_result is None:
                return StopResubmitResult(
                    ticker=ticker,
                    old_stop_price=tracked.stop_price,
                    new_stop_price=new_stop_price,
                    old_order_id=tracked.order_id,
                    new_order_id="",
                    success=False,
                    error=f"Failed to cancel old stop {tracked.order_id}",
                )
        except Exception as e:
            return StopResubmitResult(
                ticker=ticker,
                old_stop_price=tracked.stop_price,
                new_stop_price=new_stop_price,
                old_order_id=tracked.order_id,
                new_order_id="",
                success=False,
                error=f"Cancel exception: {e}",
            )

        logger.info(
            "STOP CANCELED: %s old stop %s @ $%.2f",
            ticker, tracked.order_id, tracked.stop_price,
        )

        # Phase 2: Submit new stop (D108: retry with exponential backoff)
        last_error: Exception | None = None
        for attempt in range(self._MAX_RETRIES):
            try:
                new_order = await self._client.submit_stop_order(
                    symbol=ticker,
                    qty=qty,
                    side="sell",
                    stop_price=new_stop_price,
                    time_in_force="gtc",  # Tue 2026-04-21: never DAY for protective stops
                )
                new_order_id = new_order.get("id", "")

                # Update tracking
                self._stops[ticker] = TrackedStop(
                    ticker=ticker,
                    order_id=new_order_id,
                    stop_price=new_stop_price,
                    qty=qty,
                )

                logger.info(
                    "STOP RESUBMITTED: %s new stop %s @ $%.2f (was $%.2f)%s",
                    ticker, new_order_id, new_stop_price, tracked.stop_price,
                    f" (attempt {attempt + 1})" if attempt > 0 else "",
                )

                return StopResubmitResult(
                    ticker=ticker,
                    old_stop_price=tracked.stop_price,
                    new_stop_price=new_stop_price,
                    old_order_id=tracked.order_id,
                    new_order_id=new_order_id,
                    success=True,
                )

            except Exception as e:
                last_error = e
                if attempt < self._MAX_RETRIES - 1:
                    delay = self._BACKOFF_BASE_S * (2 ** attempt)
                    logger.warning(
                        "D108: Stop resubmit attempt %d/%d failed for %s: %s. "
                        "Retrying in %.0fs...",
                        attempt + 1, self._MAX_RETRIES, ticker, e, delay,
                    )
                    await asyncio.sleep(delay)

        # All retries exhausted — position has NO stop protection
        logger.critical(
            "STOP RESUBMIT FAILED: %s — Old stop canceled but new stop NOT placed "
            "after %d attempts! Attempting emergency stop at original price. Error: %s",
            ticker, self._MAX_RETRIES, last_error,
        )

        # D150: Emergency fallback — try to place stop at OLD price as safety net
        try:
            emergency_order = await self._client.submit_stop_order(
                symbol=ticker,
                qty=qty,
                side="sell",
                stop_price=tracked.stop_price,  # Fall back to old price
                time_in_force="gtc",  # Tue 2026-04-21
            )
            emergency_oid = emergency_order.get("id", "")
            if emergency_oid:
                self._stops[ticker] = TrackedStop(
                    ticker=ticker,
                    order_id=emergency_oid,
                    stop_price=tracked.stop_price,
                    qty=qty,
                )
                logger.warning(
                    "EMERGENCY STOP PLACED: %s at old price $%.2f (oid=%s) — "
                    "ratchet failed but position is protected",
                    ticker, tracked.stop_price, emergency_oid,
                )
        except Exception as emergency_err:
            logger.critical(
                "EMERGENCY STOP ALSO FAILED: %s — position UNPROTECTED! "
                "Manual intervention required. Error: %s",
                ticker, emergency_err,
            )

        return StopResubmitResult(
            ticker=ticker,
            old_stop_price=tracked.stop_price,
            new_stop_price=new_stop_price,
            old_order_id=tracked.order_id,
            new_order_id="",
            success=False,
            error=f"CRITICAL: Stop gap — old canceled, new failed after {self._MAX_RETRIES} retries: {last_error}",
        )

    def get_tracked_stop(self, ticker: str) -> TrackedStop | None:
        """Get current tracked stop for a ticker."""
        return self._stops.get(ticker)

    @property
    def tracked_tickers(self) -> list[str]:
        """List of tickers with tracked stops."""
        return list(self._stops.keys())

    async def cancel_and_remove(self, ticker: str) -> None:
        """Cancel the active stop at the broker and remove tracking.

        D121 CQ-2: Extracted from duplicated patterns in main.py exit paths.
        Safe to call if no stop is tracked (no-op).
        """
        tracked = self._stops.get(ticker)
        if tracked:
            try:
                await self._client.cancel_order(tracked.order_id)
                logger.info("STOP CANCELED: %s (oid=%s)", ticker, tracked.order_id)
            except Exception as e:
                logger.debug("Stop cancel for %s (may already be canceled): %s", ticker, e)
        self._stops.pop(ticker, None)

    def remove(self, ticker: str) -> None:
        """Remove tracking for a closed position (sync, no broker call)."""
        self._stops.pop(ticker, None)

    def reset(self) -> None:
        """Clear all tracked stops for new session."""
        self._stops.clear()
