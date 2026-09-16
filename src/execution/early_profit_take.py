"""
D164: Time-based partial profit take at T+2 minutes after entry fill.

### ARCHITECTURAL CONTEXT
Node ID: execution.early_profit_take
Design decision log: docs/D164_early_profit_take.md

### PROBLEM SOLVED
Arena data shows MFE (max favorable excursion) peaks at bar 1 — the first
minute after entry. Most gap stocks give a brief profit window before reversing.
D146 (bar-1 exit) captures this by selling 100% at T+60s. D164 is a softer
version: sell 50% at T+2 minutes, keep the remaining 50% for the trailing stop
(D163) to manage. This locks in partial profit while preserving upside exposure.

### DESIGN
- At T+2 minutes after fill: check if profitable (>= min_profit_pct)
- If yes → sell exit_pct (50%) at market, hold the rest
- If no  → SKIP, let D163 / D78 / hard stop manage the full position

### INTERACTION WITH OTHER SYSTEMS
- D146 (bar-1 exit): if bar-1 fires before T+2, position is already closed →
  `check()` returns NOT_TRACKED (position removed). No conflict.
- D163 (trailing stop): continues managing the REMAINING 50% after D164 fires.
  Trailing stop tracks by symbol — qty is unaffected (no qty stored in D163 state).
- D78 (smart exit): if smart exit fires before T+2, position is closed →
  `check()` returns NOT_TRACKED. No conflict.
- Tranches: if tranche fills reduce remaining_qty, the sell in D164 uses
  original_qty for the 50% calculation. If remaining_qty < qty_to_sell at
  execution time, the caller should clamp.

### THREAD SAFETY
Designed for single-threaded asyncio use. The asyncio.create_task() pattern in
Phase 2 fires the T+2 trigger concurrently with the Phase 3 monitoring loop.
To prevent a double-sell race:
  - Phase 2 task: set `state.executed = True` BEFORE the first await. Reset on
    failure so Phase 3 can retry.
  - Phase 3 loop: check() returns ALREADY_TAKEN immediately if executed=True.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

logger = logging.getLogger(__name__)


# ── Action enum ───────────────────────────────────────────────────────────────


class EarlyProfitAction(Enum):
    """Return value from EarlyProfitTaker.check() each cycle."""
    WAIT = "WAIT"                   # Not yet T+delay_seconds
    TAKE_PROFIT = "TAKE_PROFIT"     # Profitable at T+delay_seconds → sell
    SKIP = "SKIP"                   # Not profitable at trigger, or past deadline
    ALREADY_TAKEN = "ALREADY_TAKEN" # Already executed — no further action
    NOT_TRACKED = "NOT_TRACKED"     # Unknown symbol (position closed upstream)


# ── State ─────────────────────────────────────────────────────────────────────


@dataclass
class EarlyProfitState:
    """Per-position state for the time-based early profit take."""

    symbol: str
    fill_time: datetime        # UTC time when fill was confirmed
    entry_price: float         # Fill price (long: buy price, short: sell price)
    original_qty: int          # Quantity at time of fill (before any tranche reduces)
    direction: str             # "long" or "short"
    trigger_time: datetime     # fill_time + delay_seconds — when to check
    deadline: datetime         # fill_time + max_delay_seconds — last moment to fire

    # Execution state
    executed: bool = False     # True after partial sell was submitted
    skipped: bool = False      # True if we decided not to fire (not profitable, past deadline, error)
    exit_price: float = 0.0    # Price at which partial sell executed
    qty_sold: int = 0          # Shares actually sold


# ── Config ────────────────────────────────────────────────────────────────────


@dataclass
class EarlyProfitTakeConfig:
    """D164: Configuration for the time-based early profit take system."""

    enabled: bool = True
    delay_seconds: float = 120.0      # T+2 minutes after fill
    exit_pct: float = 0.50            # Sell 50% of position
    min_profit_pct: float = 0.005     # Must be at least +0.5% profitable to trigger
    max_delay_seconds: float = 300.0  # Window closes at T+5 minutes
    apply_to_shorts: bool = True      # Also applies to short positions


# ── Manager ───────────────────────────────────────────────────────────────────


class EarlyProfitTaker:
    """
    Manages time-based partial profit taking for all open positions.

    Usage lifecycle per position:
      1. On fill: register_fill(symbol, fill_time, entry_price, qty, direction)
      2. Each monitoring cycle: check(symbol, current_price, now)
         → WAIT: not yet T+delay_seconds
         → TAKE_PROFIT: submit partial sell, then mark_executed()
         → SKIP: no action, never TAKE_PROFIT for this position
         → ALREADY_TAKEN: partial sell already done
         → NOT_TRACKED: position closed upstream
      3. On full close (any reason): remove(symbol)
    """

    def __init__(self, config: EarlyProfitTakeConfig | None = None) -> None:
        self._config = config if config is not None else EarlyProfitTakeConfig()
        self._tracked: dict[str, EarlyProfitState] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def register_fill(
        self,
        symbol: str,
        fill_time: datetime,
        entry_price: float,
        qty: int,
        direction: str,
    ) -> None:
        """
        Register a newly-confirmed fill for time-based profit tracking.

        Idempotent — calling twice for the same symbol is a no-op (crash-recovery).
        direction: "long" or "short"
        """
        if not self._config.enabled:
            return

        if symbol in self._tracked:
            logger.debug(
                "D164 EARLY PROFIT: %s already registered, skipping (crash-recovery?)", symbol
            )
            return

        if direction == "short" and not self._config.apply_to_shorts:
            logger.debug("D164 EARLY PROFIT: %s is short, apply_to_shorts=False — skipping", symbol)
            return

        trigger_time = fill_time + timedelta(seconds=self._config.delay_seconds)
        deadline = fill_time + timedelta(seconds=self._config.max_delay_seconds)

        state = EarlyProfitState(
            symbol=symbol,
            fill_time=fill_time,
            entry_price=entry_price,
            original_qty=qty,
            direction=direction,
            trigger_time=trigger_time,
            deadline=deadline,
        )
        self._tracked[symbol] = state
        logger.debug(
            "D164 EARLY PROFIT: registered %s dir=%s entry=$%.4f qty=%d "
            "trigger=T+%.0fs deadline=T+%.0fs",
            symbol, direction, entry_price, qty,
            self._config.delay_seconds, self._config.max_delay_seconds,
        )

    def check(
        self, symbol: str, current_price: float, now: datetime
    ) -> EarlyProfitAction:
        """
        Called every monitoring cycle. Returns the appropriate action.

        Does NOT mutate state (except skipped=True on first skip decision).
        Thread safety: set executed=True BEFORE the first await, then call
        mark_executed() on success or reset executed=False on failure.
        """
        if not self._config.enabled:
            return EarlyProfitAction.NOT_TRACKED

        state = self._tracked.get(symbol)
        if state is None:
            return EarlyProfitAction.NOT_TRACKED

        if state.executed:
            return EarlyProfitAction.ALREADY_TAKEN

        if state.skipped:
            return EarlyProfitAction.SKIP

        if now < state.trigger_time:
            return EarlyProfitAction.WAIT

        # Past trigger time — check deadline
        if now > state.deadline:
            state.skipped = True
            elapsed = (now - state.fill_time).total_seconds()
            logger.info(
                "D164 EARLY PROFIT SKIP: %s — past deadline (T+%.0fs > max=%.0fs)",
                symbol, elapsed, self._config.max_delay_seconds,
            )
            return EarlyProfitAction.SKIP

        # Within window — check profitability
        if state.direction == "long":
            profit_pct = (current_price - state.entry_price) / state.entry_price
        else:
            profit_pct = (state.entry_price - current_price) / state.entry_price

        if profit_pct < self._config.min_profit_pct:
            state.skipped = True
            elapsed = (now - state.fill_time).total_seconds()
            logger.info(
                "D164 EARLY PROFIT SKIP: %s — not profitable at T+%.0fs "
                "(profit=%.2f%%, min=%.2f%%)",
                symbol, elapsed, profit_pct * 100, self._config.min_profit_pct * 100,
            )
            return EarlyProfitAction.SKIP

        return EarlyProfitAction.TAKE_PROFIT

    def qty_to_sell(self, symbol: str) -> int:
        """
        Number of shares to sell in the partial exit.

        = floor(original_qty * exit_pct)
        e.g. 333 shares × 50% = 166 (floor, not round).
        """
        state = self._tracked.get(symbol)
        if state is None:
            return 0
        return math.floor(state.original_qty * self._config.exit_pct)

    def mark_executed(self, symbol: str, exit_price: float, qty_sold: int) -> None:
        """
        Record that the partial exit was successfully submitted.
        Must be called AFTER the sell order is confirmed.
        """
        state = self._tracked.get(symbol)
        if state is None:
            return
        state.executed = True
        state.exit_price = exit_price
        state.qty_sold = qty_sold
        logger.debug(
            "D164 EARLY PROFIT: %s marked executed exit=$%.4f qty_sold=%d",
            symbol, exit_price, qty_sold,
        )

    def mark_skipped(self, symbol: str) -> None:
        """
        Force-skip a symbol (e.g. after a submit failure, to prevent retries).
        Can be reset by calling un_skip() to allow Phase 3 retry.
        """
        state = self._tracked.get(symbol)
        if state is not None:
            state.skipped = True

    def un_skip(self, symbol: str) -> None:
        """
        Reset skipped flag (used when submit fails and retry is desired).
        Only useful if called before the deadline.
        """
        state = self._tracked.get(symbol)
        if state is not None:
            state.skipped = False

    def remove(self, symbol: str) -> None:
        """
        Remove tracking when position is fully closed (any reason).
        Safe to call even if symbol was never registered.
        """
        if symbol in self._tracked:
            self._tracked.pop(symbol)
            logger.debug("D164 EARLY PROFIT: removed tracking for %s", symbol)

    def get_state(self, symbol: str) -> EarlyProfitState | None:
        """Full state snapshot (for testing / debugging)."""
        return self._tracked.get(symbol)
