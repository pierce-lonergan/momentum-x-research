"""
D165: Price-based Tranche Profit-Taking

### PROBLEM
The original tranche system submitted limit orders at entry and monitored WebSocket
fills. This broke silently in two ways:
  1. For non-shortable stocks (SST, ARTL), D147 kept the OTO bracket in place and
     partial-sell limit orders got 403 "cannot be sold short" rejections.
  2. Even when limit orders were submitted, Phase 3 had NO fallback: if the limit
     never filled (price overshot, order expired), tranches were silently skipped.

Forensic evidence: ARTL entered $7.68, hit $8.22 (+7%). T1 target $7.91 (+3%) and
T2 $8.14 (+6%) were both reached with zero tranche fires. Same for SST.

### FIX
D165TrancheTaker is a pure-software, price-based fallback that runs every Phase 3
cycle. It checks whether the current market price has crossed any outstanding tranche
target and fires a market sell if so. It is independent of Alpaca limit order state —
it fires whether or not limit orders were submitted or filled.

### INTEGRATION
- Runs in Phase 3 AFTER D164 (early profit take) and BEFORE D163 (trailing stop).
- Uses position.target_prices (already computed at entry from settings tranche_t*_pct).
- Uses position.remaining_qty for tranche sizes (auto-adjusts after D164 sells 50%).
- Tracks fired tranches internally per-position to prevent double-fires.
- Returns TrancheHit list for the caller (main.py) to execute and log.

### EXECUTION PRIORITY
D164 (T+2min partial) → D165 (price tranche) → D163 (trailing stop) → D78 (SMART_EXIT)

Ref: docs/D165_tranche_profit_taking.md
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.execution.position_manager import ManagedPosition

logger = logging.getLogger(__name__)


@dataclass
class TrancheHit:
    """A tranche that should fire at the current price."""
    tranche_number: int   # 1, 2, or 3
    qty: int              # shares to sell
    target_price: float   # configured target (for logging)


@dataclass
class D165Config:
    """Configuration for the D165 price-based tranche system."""
    enabled: bool = True
    # Target percentages from entry. Defaults match settings.tranche_t*_pct.
    # Kept here for unit-test convenience; production code reads from settings.
    target_pcts: list[float] = field(default_factory=lambda: [0.03, 0.06, 0.10])
    adjust_stop_after_partial: bool = True


class D165TrancheTaker:
    """
    Price-based tranche profit-taking fallback.

    Checks every Phase 3 cycle whether current price has crossed any unclaimed
    tranche target. Fires market sells in order (T1 → T2 → T3), one per cycle
    per position, to avoid over-selling in a single tick.

    Thread-safety: single-threaded async loop — no locks needed.

    Usage (main.py Phase 3):
        hits = tranche_taker.check(pos, current_price)
        for hit in hits:
            await client.submit_order(symbol=pos.ticker, qty=hit.qty, side="sell")
            tranche_taker.mark_fired(pos.ticker, hit.tranche_number)
    """

    def __init__(self, config: D165Config | None = None) -> None:
        self._config = config or D165Config()
        # ticker → set of fired tranche numbers (1-based)
        self._fired: dict[str, set[int]] = {}

    # ── Public API ─────────────────────────────────────────────────────────────

    def register_fill(self, symbol: str) -> None:
        """Register a new position after fill. Idempotent."""
        self._fired.setdefault(symbol, set())

    def check(
        self,
        position: "ManagedPosition",
        current_price: float,
    ) -> list[TrancheHit]:
        """
        Return ordered list of tranches that should fire at current_price.

        Rules:
        - Only fires tranches not yet taken (neither by limit order fill nor D165).
        - Tranches are sequential: T2 only fires if T1 has already fired (or fires
          in the same cycle when price jumps past multiple targets).
        - Qty is computed from position.remaining_qty so it auto-adjusts after D164.
        - Returns empty list when config.enabled=False or no targets available.
        """
        if not self._config.enabled:
            return []

        if not position.target_prices:
            return []

        if current_price <= 0:
            return []

        fired = self._fired.get(position.ticker, set())
        hits: list[TrancheHit] = []

        for i, target in enumerate(position.target_prices):
            t_num = i + 1

            # Skip tranches already fired (limit-order system OR D165)
            if t_num in fired:
                continue

            # Sync with limit-order system: if tranche_monitor already counted it
            if position.tranches_filled >= t_num:
                fired.add(t_num)  # keep internal state in sync
                self._fired[position.ticker] = fired
                continue

            # Check price trigger
            if not self._is_triggered(position.direction, current_price, target):
                # Tranches are ordered; if this one isn't triggered, later ones won't be
                break

            qty = self._tranche_qty(position, i)
            qty = min(qty, position.remaining_qty - sum(h.qty for h in hits))
            if qty <= 0:
                # Mark as fired so we don't loop on this forever
                fired.add(t_num)
                self._fired[position.ticker] = fired
                continue

            hits.append(TrancheHit(
                tranche_number=t_num,
                qty=qty,
                target_price=target,
            ))
            # Don't break — allow multiple hits if price cleared several targets

        return hits

    def mark_fired(self, symbol: str, tranche_number: int) -> None:
        """Mark a tranche as fired after the market sell is submitted."""
        self._fired.setdefault(symbol, set()).add(tranche_number)

    def remove(self, symbol: str) -> None:
        """Remove a position from tracking (called on position close)."""
        self._fired.pop(symbol, None)

    def reset(self) -> None:
        """Reset all state for a new session."""
        self._fired.clear()

    def is_fired(self, symbol: str, tranche_number: int) -> bool:
        """Return True if the given tranche has already been fired for this symbol."""
        return tranche_number in self._fired.get(symbol, set())

    # ── Static helpers (used in tests + settings computation) ─────────────────

    @staticmethod
    def compute_targets(
        entry_price: float,
        direction: str = "long",
        pcts: list[float] | None = None,
    ) -> list[float]:
        """
        Compute [T1, T2, T3] absolute target prices from entry price.

        Long:  targets are ABOVE entry (entry × (1 + pct))
        Short: targets are BELOW entry (entry × (1 - pct))

        Default pcts: [0.03, 0.06, 0.10] (+3%, +6%, +10%)
        """
        if pcts is None:
            pcts = [0.03, 0.06, 0.10]
        if direction == "long":
            return [round(entry_price * (1.0 + p), 4) for p in pcts]
        else:
            return [round(entry_price * (1.0 - p), 4) for p in pcts]

    @staticmethod
    def compute_tranche_sizes(qty: int) -> tuple[int, int, int]:
        """
        Split qty into (t1, t2, t3) shares.

        T1 = floor(qty/3)
        T2 = floor(qty/3)
        T3 = qty - T1 - T2  (remainder)

        Examples:
            300 → (100, 100, 100)
            333 → (111, 111, 111)
            100 → (33,  33,  34)
        """
        t1 = qty // 3
        t2 = qty // 3
        t3 = qty - t1 - t2
        return t1, t2, t3

    # ── Private helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _is_triggered(direction: str, current: float, target: float) -> bool:
        """True if current price has crossed the tranche target."""
        if direction == "long":
            return current >= target
        else:
            return current <= target

    def _tranche_qty(self, position: "ManagedPosition", idx: int) -> int:
        """
        Calculate shares to sell for tranche at index `idx` (0-based).

        T1 = floor(remaining / 3)
        T2 = floor(remaining / 3)
        T3 = all remaining shares (full exit, per task spec)

        Uses position.remaining_qty as the base, so D164 partial sells are
        automatically reflected in tranche sizes.
        """
        remaining = position.remaining_qty
        if idx == 2:
            # T3 is always "sell everything left"
            return remaining
        t1, t2, _ = self.compute_tranche_sizes(remaining)
        return t1 if idx == 0 else t2
