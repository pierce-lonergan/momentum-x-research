"""
Wed 2026-04-22 Bug A fix: filter D86's "cancel stale orders" so it
preserves protective GTC stops on currently-held positions.

Background. Tue 2026-04-21 evening: I placed a GTC stop on ELSE
($6.50, order 6490714a) to protect 2478 shares overnight. Wed
04:30:29 the system started, ran D86 at main.py:1097
(`await client.cancel_all_orders()`), and the GTC stop was
unilaterally cancelled. ELSE then sat naked at the broker for ~2
hours during pre-market while the dashboard showed a phantom
"$7.23 stop" that was actually `entry_price × 0.945` from settings,
not a real broker order.

Root cause. `cancel_all_orders` is a shotgun: it cancels every
open order at the broker without considering whether each order
is a legitimate protective stop on a position we currently hold
(in which case canceling makes the position naked) or a
genuinely stale day-order from the previous session (in which
case canceling is correct).

Fix. Replace `cancel_all_orders()` with a filtered cancel:

  for each open order:
    if order is GTC AND order is a protective stop AND broker
       still holds a position in the same symbol with qty >= order.qty:
        PRESERVE — log "preserved GTC stop for held position"
    else:
        CANCEL

This module exposes a pure-function planner so the main.py call
site is a thin wrapper around testable logic.

Adversarial cases handled:

  - GTC stop on held position: PRESERVE (the bug we're fixing)
  - DAY stop on held position: CANCEL (DAY expires anyway; stale)
  - GTC stop on position we no longer hold: CANCEL (orphaned)
  - GTC stop with qty > position qty: CANCEL (oversized — likely
    pre-tranche-fill state; safer to cancel and let the live
    code re-establish coherent stop)
  - Stop on short position: PRESERVE if symbol matches (short
    cover stops are protective too)
  - Limit order that's NOT a stop: CANCEL (these are tranche
    sells / entry orders / take-profits — none are "the
    protective stop" the user means)
  - Empty inputs: returns empty cancel list (no-op)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any


logger = logging.getLogger(__name__)


# Order types that count as "protective stop"
_PROTECTIVE_STOP_TYPES = frozenset({"stop", "stop_limit", "trailing_stop"})


@dataclass
class CleanupPlan:
    """The plan produced by select_orders_to_cancel.

    Attributes:
        cancel_ids: Order IDs to cancel.
        preserved: List of (order_id, symbol, reason) for orders
            we kept. For audit + log.
    """
    cancel_ids: list[str] = field(default_factory=list)
    preserved: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def n_cancel(self) -> int: return len(self.cancel_ids)
    @property
    def n_preserved(self) -> int: return len(self.preserved)


def _position_qty_by_symbol(positions: list[dict[str, Any]]) -> dict[str, int]:
    """Build {symbol: abs(qty)} from a broker positions list.
    Uses abs() so short positions (qty < 0) are treated by magnitude
    — a short cover stop is still a protective stop on the same
    quantity of shares regardless of sign."""
    out: dict[str, int] = {}
    for p in positions:
        sym = p.get("symbol")
        if not sym:
            continue
        try:
            q = abs(int(p.get("qty", 0)))
        except (ValueError, TypeError):
            q = 0
        if q > 0:
            out[sym] = q
    return out


def _is_protective_stop(order: dict[str, Any]) -> bool:
    """An order is 'a protective stop' iff its type is one of the
    stop variants AND it's a sell (long position) or buy (short
    position) with the same magnitude as the underlying. This
    function only checks the type; symbol+qty matching is upstream."""
    return order.get("type") in _PROTECTIVE_STOP_TYPES


def _is_gtc(order: dict[str, Any]) -> bool:
    return order.get("time_in_force") == "gtc"


def _should_preserve(
    order: dict[str, Any],
    pos_qty_by_symbol: dict[str, int],
) -> tuple[bool, str]:
    """Decide whether this single order should be PRESERVED (skipped
    from cancel) or CANCELED. Returns (preserve_flag, reason_string).

    Rule: preserve iff
      - time_in_force == 'gtc'
      - type ∈ protective-stop types
      - symbol has a held position
      - position qty >= order qty (full coverage)
    """
    if not _is_gtc(order):
        return False, "non-GTC (DAY orders expire — cancel is safe)"
    if not _is_protective_stop(order):
        return False, f"type={order.get('type')!r} is not a protective stop"

    sym = order.get("symbol")
    if not sym or sym not in pos_qty_by_symbol:
        return False, f"orphaned (no held position in {sym!r})"

    held_qty = pos_qty_by_symbol[sym]
    try:
        order_qty = int(order.get("qty", 0))
    except (ValueError, TypeError):
        return False, f"unparseable order qty={order.get('qty')!r}"

    if held_qty < order_qty:
        return False, (
            f"oversized: order qty {order_qty} > held qty {held_qty} "
            f"(stop is for more shares than we own — safer to cancel)"
        )

    # All four conditions satisfied → PRESERVE
    return True, (
        f"GTC {order.get('type')} on held {sym} ({held_qty} shares >= "
        f"order {order_qty})"
    )


def select_orders_to_cancel(
    open_orders: list[dict[str, Any]],
    positions: list[dict[str, Any]],
) -> CleanupPlan:
    """Pure function: given the broker's open orders + held positions,
    return the cleanup plan (which orders to cancel, which to keep).

    Used by D86 startup-cleanup at main.py to replace
    `cancel_all_orders()` (which removed today's protective stops
    along with stale day-orders).

    Both inputs are lists of broker dicts (Alpaca shape). The function
    is fully deterministic given these inputs and has no side effects.
    """
    plan = CleanupPlan()
    if not open_orders:
        return plan

    pos_map = _position_qty_by_symbol(positions)

    for order in open_orders:
        oid = order.get("id", "")
        sym = order.get("symbol", "")
        if not oid:
            # Malformed; can't cancel by ID anyway. Skip + log.
            logger.warning(
                "startup-cleanup: order with no 'id' field, skipping: %r",
                {k: order.get(k) for k in ("symbol", "type", "side", "qty")},
            )
            continue
        preserve, reason = _should_preserve(order, pos_map)
        if preserve:
            plan.preserved.append((oid, sym, reason))
        else:
            plan.cancel_ids.append(oid)

    return plan
