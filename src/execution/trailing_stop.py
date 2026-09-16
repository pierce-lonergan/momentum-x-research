"""
D163: Software-managed trailing stop system.

### ARCHITECTURAL CONTEXT
Node ID: execution.trailing_stop
Design decision log: docs/D163_trailing_stop.md

### WHAT THIS IS (vs D63)
D63 ratchets the *Alpaca stop order* using Chandelier/ATR math (activates at +4%).
D163 is a pure *software* trail — it monitors price each Phase 3 cycle and fires a
market EXIT when the trail is breached. No Alpaca order is submitted or modified.

Coexistence:
  - The OTO hard stop remains unchanged (broker-side safety net).
  - D63 continues ratcheting the Alpaca stop with Chandelier math.
  - D163 fires EARLIER (activation at +2% vs +4%) and uses simpler "50% of gain" math.
  - Whichever mechanism fires first (D163 EXIT, D63 resubmit, D78 SMART_EXIT, or
    broker stop hit) wins — the position is closed via close_position().

### PROBLEM SOLVED
Backtest analysis (Mar 30): 14/18 trades hit their fixed 30-min stop.
ARTL: peaked +7%, reversed all the way to -55% stop. SST: peaked +6%, reversed to -35%.
A software trail at 50% of max gain would have exited ARTL at +3.5% instead of -55%.

### DESIGN
For LONGS:
  - Activate when price moves >= +2% above entry (max_favorable_price >= entry * 1.02)
  - Trail level = peak - 50% of (peak - entry)  [midpoint of entry and peak]
  - Constrained: trail >= current * (1 - max_trail_pct)  [never wider than 35%]
  - Constrained: trail <= current * (1 - min_trail_pct)  [never tighter than 2%]
  - Ratchet: trail only moves UP (never down)
  - EXIT when: current_price <= trail_level

For SHORTS (mirror):
  - Activate when price moves >= -2% below entry (max_favorable_price <= entry * 0.98)
  - Trail level = trough + 50% of (entry - trough)  [midpoint of trough and entry]
  - Constrained: trail <= current * (1 + max_trail_pct)  [never wider than 35%]
  - Constrained: trail >= current * (1 + min_trail_pct)  [never tighter than 2%]
  - Ratchet: trail only moves DOWN (never up)
  - EXIT when: current_price >= trail_level
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

logger = logging.getLogger(__name__)


class TrailingStopAction(Enum):
    """Action returned by update_price() each cycle."""
    HOLD = "HOLD"
    EXIT = "EXIT"


@dataclass
class TrailingStopState:
    """
    Per-position trailing stop state.

    For longs:  max_favorable_price tracks the highest price seen since entry.
    For shorts: max_favorable_price tracks the lowest price seen since entry.
    In both cases it represents the *most favorable* price observed.
    """

    symbol: str
    entry_price: float
    direction: str       # "long" or "short"
    initial_stop: float  # Original hard stop — kept for reference / logging only

    # Running extreme price since entry
    max_favorable_price: float = 0.0

    trailing_active: bool = False
    current_trail_level: float = 0.0
    last_update_time: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def __post_init__(self) -> None:
        if self.max_favorable_price == 0.0:
            self.max_favorable_price = self.entry_price
        if self.current_trail_level == 0.0:
            self.current_trail_level = self.initial_stop


@dataclass
class TrailingStopConfig:
    """D163: Configuration for the software trailing stop system."""

    enabled: bool = True
    # +2% move in our favour before trailing activates
    activation_threshold_pct: float = 0.02
    # Trail at 50% of max gain distance from the peak/trough
    trail_pct_of_gain: float = 0.50
    # Trail can never be tighter than this % away from current price
    min_trail_distance_pct: float = 0.02
    # Trail can never be wider than this % away from current price
    # (should match the maximum initial stop distance on gap days)
    max_trail_distance_pct: float = 0.35
    # Informational — Phase 3 already calls update_price() every cycle
    update_interval_seconds: float = 15.0


class TrailingStopManager:
    """
    Manages software trailing stops for all open positions.

    Thread-safety: designed for single-threaded async use (Phase 3 loop).
    All state is in-memory and reconstructed from open positions on restart.
    """

    def __init__(self, config: TrailingStopConfig | None = None) -> None:
        self._config = config if config is not None else TrailingStopConfig()
        self._tracked: dict[str, TrailingStopState] = {}

    # ── Public API ─────────────────────────────────────────────────────────

    def register_position(
        self,
        symbol: str,
        entry_price: float,
        direction: str,
        initial_stop: float,
    ) -> None:
        """
        Register a newly-opened position.
        Called immediately after an entry fill is confirmed.
        If called again for an existing symbol (e.g. crash-recovery restart),
        the existing state is preserved.
        """
        if symbol in self._tracked:
            logger.debug(
                "D163 TRAILING STOP: %s already registered, skipping (crash-recovery?)",
                symbol,
            )
            return

        state = TrailingStopState(
            symbol=symbol,
            entry_price=entry_price,
            direction=direction,
            initial_stop=initial_stop,
        )
        self._tracked[symbol] = state
        logger.debug(
            "D163 TRAILING STOP: registered %s dir=%s entry=$%.2f stop=$%.2f",
            symbol, direction, entry_price, initial_stop,
        )

    def update_price(self, symbol: str, current_price: float) -> TrailingStopAction:
        """
        Called every Phase 3 cycle after a current price is obtained.

        Returns:
            TrailingStopAction.EXIT  — trail was breached, close position immediately.
            TrailingStopAction.HOLD  — nothing to do this cycle.
        """
        if not self._config.enabled:
            return TrailingStopAction.HOLD

        state = self._tracked.get(symbol)
        if state is None or current_price <= 0:
            return TrailingStopAction.HOLD

        if state.direction == "long":
            return self._update_long(state, current_price)
        else:
            return self._update_short(state, current_price)

    def get_trail_level(self, symbol: str) -> float | None:
        """Current trailing stop price, or None if not registered / not yet active."""
        state = self._tracked.get(symbol)
        if state is None or not state.trailing_active:
            return None
        return state.current_trail_level

    def is_trailing_active(self, symbol: str) -> bool:
        """True once the activation threshold has been crossed."""
        state = self._tracked.get(symbol)
        return state.trailing_active if state else False

    def get_state(self, symbol: str) -> TrailingStopState | None:
        """Full state snapshot (for journal / logging)."""
        return self._tracked.get(symbol)

    def remove_position(self, symbol: str) -> None:
        """Remove tracking when position is closed (any reason)."""
        if symbol in self._tracked:
            self._tracked.pop(symbol)
            logger.debug("D163 TRAILING STOP: removed %s", symbol)

    # ── Internal helpers ───────────────────────────────────────────────────

    def _update_long(self, state: TrailingStopState, current_price: float) -> TrailingStopAction:
        cfg = self._config

        # Update high-water mark
        if current_price > state.max_favorable_price:
            state.max_favorable_price = current_price

        # Activation check
        if not state.trailing_active:
            gain_pct = (state.max_favorable_price - state.entry_price) / state.entry_price
            if gain_pct >= cfg.activation_threshold_pct:
                state.trailing_active = True
                logger.info(
                    "D163 TRAILING STOP ACTIVATED: %s gain=+%.1f%% peak=$%.2f entry=$%.2f",
                    state.symbol, gain_pct * 100,
                    state.max_favorable_price, state.entry_price,
                )
            else:
                return TrailingStopAction.HOLD

        # Compute and ratchet trail
        new_trail = self._compute_trail_long(state, current_price)
        if new_trail > state.current_trail_level:
            old_trail = state.current_trail_level
            state.current_trail_level = new_trail
            max_gain_pct = (state.max_favorable_price / state.entry_price - 1) * 100
            logger.debug(
                "D163 TRAILING STOP: %s trail $%.2f -> $%.2f "
                "(price=$%.2f peak=$%.2f max_gain=+%.1f%%)",
                state.symbol, old_trail, new_trail,
                current_price, state.max_favorable_price, max_gain_pct,
            )

        state.last_update_time = datetime.now(timezone.utc)

        # Breach check
        if current_price <= state.current_trail_level:
            max_gain_pct = (state.max_favorable_price / state.entry_price - 1) * 100
            logger.info(
                "D163 TRAILING STOP EXIT: %s price=$%.2f trail=$%.2f "
                "max_gain=+%.1f%% entry=$%.2f initial_stop=$%.2f",
                state.symbol, current_price, state.current_trail_level,
                max_gain_pct, state.entry_price, state.initial_stop,
            )
            return TrailingStopAction.EXIT

        return TrailingStopAction.HOLD

    def _update_short(self, state: TrailingStopState, current_price: float) -> TrailingStopAction:
        cfg = self._config

        # Update low-water mark (most favorable = lowest for shorts)
        if current_price < state.max_favorable_price:
            state.max_favorable_price = current_price

        # Activation check
        if not state.trailing_active:
            drop_pct = (state.entry_price - state.max_favorable_price) / state.entry_price
            if drop_pct >= cfg.activation_threshold_pct:
                state.trailing_active = True
                logger.info(
                    "D163 TRAILING STOP ACTIVATED (SHORT): %s drop=+%.1f%% trough=$%.2f entry=$%.2f",
                    state.symbol, drop_pct * 100,
                    state.max_favorable_price, state.entry_price,
                )
            else:
                return TrailingStopAction.HOLD

        # Compute and ratchet trail
        new_trail = self._compute_trail_short(state, current_price)
        if new_trail < state.current_trail_level:
            old_trail = state.current_trail_level
            state.current_trail_level = new_trail
            max_gain_pct = (state.entry_price / state.max_favorable_price - 1) * 100
            logger.debug(
                "D163 TRAILING STOP (SHORT): %s trail $%.2f -> $%.2f "
                "(price=$%.2f trough=$%.2f max_gain=+%.1f%%)",
                state.symbol, old_trail, new_trail,
                current_price, state.max_favorable_price, max_gain_pct,
            )

        state.last_update_time = datetime.now(timezone.utc)

        # Breach check
        if current_price >= state.current_trail_level:
            max_gain_pct = (state.entry_price / state.max_favorable_price - 1) * 100
            logger.info(
                "D163 TRAILING STOP EXIT (SHORT): %s price=$%.2f trail=$%.2f "
                "max_gain=+%.1f%% entry=$%.2f initial_stop=$%.2f",
                state.symbol, current_price, state.current_trail_level,
                max_gain_pct, state.entry_price, state.initial_stop,
            )
            return TrailingStopAction.EXIT

        return TrailingStopAction.HOLD

    def _compute_trail_long(self, state: TrailingStopState, current_price: float) -> float:
        """
        Compute updated trail level for a long position.

        Trail = peak - 50% * (peak - entry)  [midpoint of entry and peak]
        Clamped to [current*(1-max_pct), current*(1-min_pct)].
        """
        cfg = self._config
        max_gain = state.max_favorable_price - state.entry_price

        if max_gain <= 0:
            # No gain yet — place trail at min distance below current
            return current_price * (1.0 - cfg.min_trail_distance_pct)

        trail_from_peak = max_gain * cfg.trail_pct_of_gain
        trail_level = state.max_favorable_price - trail_from_peak

        # Never tighter than min_trail_distance_pct below current
        max_allowed = current_price * (1.0 - cfg.min_trail_distance_pct)
        trail_level = min(trail_level, max_allowed)

        # Never wider than max_trail_distance_pct below current
        min_allowed = current_price * (1.0 - cfg.max_trail_distance_pct)
        trail_level = max(trail_level, min_allowed)

        return trail_level

    def _compute_trail_short(self, state: TrailingStopState, current_price: float) -> float:
        """
        Compute updated trail level for a short position.

        Trail = trough + 50% * (entry - trough)  [midpoint of trough and entry]
        Clamped to [current*(1+min_pct), current*(1+max_pct)].
        """
        cfg = self._config
        max_gain = state.entry_price - state.max_favorable_price

        if max_gain <= 0:
            # No gain yet — place trail at min distance above current
            return current_price * (1.0 + cfg.min_trail_distance_pct)

        trail_from_trough = max_gain * cfg.trail_pct_of_gain
        trail_level = state.max_favorable_price + trail_from_trough

        # Never tighter than min_trail_distance_pct above current
        min_allowed = current_price * (1.0 + cfg.min_trail_distance_pct)
        trail_level = max(trail_level, min_allowed)

        # Never wider than max_trail_distance_pct above current
        max_allowed = current_price * (1.0 + cfg.max_trail_distance_pct)
        trail_level = min(trail_level, max_allowed)

        return trail_level
