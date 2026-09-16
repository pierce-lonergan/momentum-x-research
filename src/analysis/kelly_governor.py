"""KellyGovernor — D223 BOCPD_BREAK Kelly governance policy.

Per `docs/research-log/22_tuesday_architecture_call_agenda.md` Tuesday call
discussion item #5 (recommendation (b)): when BOCPD detects a regime
break, halve Kelly multiplier for the next 5 trades. Conservative
starting policy — empirically tunable.

Operational semantics:
  * `on_break_detected()` — called by BOCPDState.observe when posterior
    changepoint > kill_switch_threshold. Activates the halver.
  * `current_multiplier()` — read by execution code at every entry.
    Returns 0.5 if active (next 5 trades), 1.0 otherwise.
  * `record_trade_completed()` — called by bridge.close_with_attribution
    after each closed position. Decrements remaining_trades; when
    reaches 0, multiplier resets to 1.0.

Threading: not thread-safe. Designed for single-threaded asyncio
execution loop. State is module-instance-level.

D224 KELLY_HALVED reserved for the activation event (logged once at
the moment of activation + once at the reset moment).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


DEFAULT_HALVE_DURATION_TRADES: int = 5
"""Number of trades to halve Kelly for after a BOCPD_BREAK."""

DEFAULT_HALVE_MULTIPLIER: float = 0.5
"""Kelly multiplier during active halve window. 0.5 = halve."""


@dataclass
class KellyGovernor:
    """Holds the active Kelly multiplier + the trades-remaining countdown.

    Attributes:
      halve_duration_trades:  Number of post-break trades to halve.
      halve_multiplier:       Kelly multiplier during halve window.
      remaining_trades:       Decrements per closed trade; 0 = inactive.
      total_activations:      Diagnostic counter (lifetime).
    """
    halve_duration_trades: int = DEFAULT_HALVE_DURATION_TRADES
    halve_multiplier: float = DEFAULT_HALVE_MULTIPLIER
    remaining_trades: int = 0
    total_activations: int = 0

    def on_break_detected(self, posterior_cp: float) -> None:
        """Activate the halve window. Idempotent within an active window
        (re-activation while already halving extends to halve_duration_trades
        from now, doesn't compound)."""
        previously_active = self.is_active
        self.remaining_trades = self.halve_duration_trades
        self.total_activations += 1
        logger.warning(
            "D224 KELLY_HALVED %s posterior_cp=%.3f → "
            "Kelly multiplier %.2f for next %d trades",
            "RE-ACTIVATED" if previously_active else "ACTIVATED",
            posterior_cp, self.halve_multiplier, self.halve_duration_trades,
        )

    def current_multiplier(self) -> float:
        """Return the active Kelly multiplier (1.0 normal, 0.5 halved)."""
        return self.halve_multiplier if self.is_active else 1.0

    def record_trade_completed(self) -> None:
        """Decrement remaining_trades. When 0, multiplier resets to 1.0."""
        if self.remaining_trades <= 0:
            return
        self.remaining_trades -= 1
        if self.remaining_trades == 0:
            logger.info(
                "D224 KELLY_HALVED RESET — Kelly multiplier back to 1.0 "
                "after %d-trade halve window completed.",
                self.halve_duration_trades,
            )

    @property
    def is_active(self) -> bool:
        return self.remaining_trades > 0

    def health_snapshot(self) -> dict:
        return {
            "is_active": self.is_active,
            "remaining_trades": self.remaining_trades,
            "current_multiplier": self.current_multiplier(),
            "halve_duration_trades": self.halve_duration_trades,
            "halve_multiplier": self.halve_multiplier,
            "total_activations": self.total_activations,
        }
