"""
Tue 2026-04-21 Fix 4: 403 OTO tranche conflict — exit-ladder orchestrator.

Background. Today's ELSE trade illustrated the bug:

    10:17:06  ELSE entry filled, qty=2478 @ $7.65
              OTO stop reserves all 2478 shares as held_for_orders
    10:17:09  D100 stop CANCELED, NEW standalone stop SUBMITTED for full
              qty (2478) at $6.50 — re-locks all 2478 shares
    10:17:10  POST tranche limit sell (826 @ $8.03) → 403:
              {"available": "0", "held_for_orders": "2478"}

The D100 cancel-and-replace pattern (committed ~2 weeks ago to fix the
prior "OTO stop blocks tranche sells" bug) had a logical hole: the new
standalone stop reserves the FULL position qty, so tranches still 403.
Per the in-code comment at the original D100 site:

    "Profit-taking has NEVER worked in 14 days because of this."

Truer than originally written: D100 didn't actually fix it. ELSE today
proved it.

# Root cause

Alpaca's reservation model: total `held_for_orders` across all OPEN
sell-side orders cannot exceed position qty. A protective stop for the
FULL qty leaves zero available for tranche limits.

# Fix

`compute_exit_tranches` already returns 3 tranches where the third has
`exit_type == "trailing_stop"` (a strategy label — meaning "this slice
is the residual that the protective stop covers and trails upward").
The previous code ignored the `exit_type` field and submitted ALL
three as `submit_limit_order` calls, summing to 100% of position +
the stop's 100%, exceeding the broker's reservation limit.

This module orchestrates the correct pattern:

  1. (precondition) the OTO stop is still active, holding full qty.
  2. Cancel the OTO stop. (frees full qty)
  3. Submit each LIMIT-typed tranche as a sell limit. (reserves
     ⅓ + ⅓ = ⅔ of qty across 2 tranches)
  4. Submit a NEW protective stop for the RESIDUAL qty (= position
     qty − sum of tranche qtys, typically ⅓). The trailing-stop
     mechanism (StopResubmitter) ratchets this stop upward as price
     climbs, providing the "trailing stop" semantics tranche 3
     originally implied.
  5. After all reservations: held_for_orders = ⅓+⅓+⅓ = position qty
     ✓. No 403.

# Naked window

Between step 2 (cancel old stop) and step 4 (submit new stop), the
position has NO broker-side downside protection. In practice this is
sub-second on a healthy connection (3 sequential HTTP roundtrips).
The window is logged at WARNING. If step 3 or step 4 fails, the
result reports the failure clearly so the caller can re-submit.

# What this module does NOT do

- Does NOT submit the entry order (caller's job).
- Does NOT cancel tranches when the stop fires (StopResubmitter +
  tranche_monitor handle that).
- Does NOT ratchet the stop upward (StopResubmitter handles that).
- Does NOT handle the `exit_type == "trailing_stop"` tranche as a
  separate broker order — that label means "the residual slice
  covered by the protective stop." The position_manager comments at
  position_manager.py:613 describe T3 as "Limit at target_prices[2]
  or trailing stop" — this module implements the "or trailing stop"
  side of the disjunction.

# Adversarial cases handled

- Non-shortable ticker (D147): skip the cancel/replace entirely,
  keep the OTO stop in place, return early without tranches. The
  position runs to its OTO stop or to manual close.
- All tranches `exit_type == "limit"` (no residual): submit tranches
  but emit WARNING — the position will have NO downside protection
  after this routine. Caller should treat as a soft-fail.
- Empty tranche list: short-circuit; restore the previous stop only
  if old_stop_order_id was provided (caller decides cancellation).
- Cancel failure: don't proceed to tranche submission; return failure.
- Mid-ladder failure: report which tranches succeeded; the residual
  stop submission is attempted regardless so we don't leave the
  position completely naked.
- Residual-stop failure: CRITICAL log; result reports unprotected.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol


logger = logging.getLogger(__name__)


# ── Protocol for the broker client (just the methods this module needs) ─


class _BrokerClientLike(Protocol):
    async def cancel_order(self, order_id: str) -> Any: ...

    async def submit_limit_order(
        self, *, symbol: str, qty: int, side: str, limit_price: float,
    ) -> dict[str, Any]: ...

    async def submit_stop_order(
        self, *, symbol: str, qty: int, side: str, stop_price: float,
        time_in_force: str = "gtc",
        position_intent: str | None = None,
    ) -> dict[str, Any]: ...


# ── Result type ──────────────────────────────────────────────────────


@dataclass
class ExitLadderResult:
    """Outcome of cancel_stop_and_submit_exit_ladder.

    Attributes:
        new_stop_order_id: ID of the residual protective stop, or
            empty string if (a) skipped due to non-shortable, (b)
            residual qty was 0 (all tranches consumed position), or
            (c) submission failed.
        tranche_order_ids: List of submitted tranche limit order IDs,
            in tranche order. May be shorter than the input tranche
            list if a mid-ladder failure occurred.
        residual_qty: Computed residual = position_qty - sum(limit
            tranche qtys). 0 means full coverage by tranches.
        is_position_unprotected: True iff this routine returned with
            the position holding NO active broker-side stop. The
            caller MUST handle this (alert, retry, manual close).
        skipped_reason: Human-readable reason if the routine
            short-circuited early (e.g., "non-shortable").
        partial_failure: True iff some tranches submitted but at
            least one failed. tranche_order_ids holds the successes.
    """
    new_stop_order_id: str = ""
    tranche_order_ids: list[str] = field(default_factory=list)
    residual_qty: int = 0
    is_position_unprotected: bool = False
    skipped_reason: str = ""
    partial_failure: bool = False


# ── Tranche shape (matches position_manager.ExitTranche) ─────────────


class _TrancheLike(Protocol):
    """Duck-typed view of position_manager.ExitTranche.
    The fields we read are tranche_number, qty, target, exit_type."""
    tranche_number: int
    qty: int
    target: float
    exit_type: str  # "limit" | "trailing_stop"


# ── The orchestrator ────────────────────────────────────────────────


def _filter_limit_tranches(tranches: list[_TrancheLike]) -> list[_TrancheLike]:
    """Return only tranches that should be submitted as broker LIMIT
    orders. Tranches with exit_type == 'trailing_stop' are NOT
    submitted as separate orders — their qty is the residual covered
    by the protective stop (and trailed upward by StopResubmitter)."""
    return [t for t in tranches if t.exit_type == "limit"]


def _compute_residual_qty(
    position_qty: int, limit_tranches: list[_TrancheLike],
) -> int:
    """Residual = position_qty - sum(limit tranche qtys). >= 0."""
    used = sum(t.qty for t in limit_tranches)
    return position_qty - used


async def cancel_stop_and_submit_exit_ladder(
    client: _BrokerClientLike,
    *,
    ticker: str,
    position_qty: int,
    tranches: list[_TrancheLike],
    stop_price: float,
    old_stop_order_id: str | None,
    is_shortable: bool = True,
    log_prefix: str = "",
) -> ExitLadderResult:
    """Atomically transition the broker-side state from
    OTO-stop-only to {limit tranches + residual GTC stop}.

    See module docstring for the full design + adversarial cases.

    Parameters
    ----------
    client:
        Broker client implementing cancel_order, submit_limit_order,
        submit_stop_order. AlpacaDataClient satisfies this.
    ticker:
        Symbol.
    position_qty:
        Total open position quantity.
    tranches:
        ExitTranche objects from PositionManager.compute_exit_tranches.
        Tranches with exit_type == "trailing_stop" are NOT submitted
        as broker orders — their qty becomes the residual.
    stop_price:
        Price for the residual protective stop.
    old_stop_order_id:
        ID of the existing OTO stop (or any prior protective stop)
        to cancel. Pass empty string / None to skip cancellation.
    is_shortable:
        D147 — if False, the routine skips cancel-and-replace and
        keeps the existing stop. The position runs to its OTO stop.
    log_prefix:
        Prepended to log lines for source identification (e.g.
        "VWAP", "RESCAN", "FAST_PATH").

    Returns
    -------
    ExitLadderResult — see field docs.
    """
    pre = f"{log_prefix} " if log_prefix else ""
    out = ExitLadderResult()

    # Validation
    if position_qty <= 0:
        out.skipped_reason = f"position_qty <= 0 ({position_qty})"
        logger.warning("%sexit_ladder %s: %s", pre, ticker, out.skipped_reason)
        return out

    # D147: non-shortable ticker — preserve OTO stop, skip everything
    if not is_shortable:
        out.skipped_reason = "non-shortable (D147)"
        out.new_stop_order_id = old_stop_order_id or ""
        logger.warning(
            "%sexit_ladder %s: non-shortable — keeping OTO stop "
            "(oid=%s), skipping tranches",
            pre, ticker, old_stop_order_id or "<none>",
        )
        return out

    # Compute residual BEFORE any broker action so we can validate
    limit_tranches = _filter_limit_tranches(tranches)
    residual_qty = _compute_residual_qty(position_qty, limit_tranches)
    out.residual_qty = residual_qty

    if residual_qty < 0:
        # Configuration error — limit tranches sum to MORE than position.
        # Refuse to act; signal the bug.
        msg = (
            f"limit tranches qty ({position_qty - residual_qty}) > "
            f"position qty ({position_qty}) — configuration error, "
            f"refusing to submit"
        )
        out.skipped_reason = msg
        logger.error("%sexit_ladder %s: %s", pre, ticker, msg)
        return out

    # Step 1: cancel the existing protective stop (frees full qty)
    if old_stop_order_id:
        try:
            await client.cancel_order(old_stop_order_id)
            logger.info(
                "%sexit_ladder %s: cancelled prior stop oid=%s "
                "(naked window opens)",
                pre, ticker, old_stop_order_id,
            )
        except Exception as e:
            out.skipped_reason = f"cancel of {old_stop_order_id} failed: {e}"
            logger.error(
                "%sexit_ladder %s: %s — refusing to proceed "
                "(prior stop may still be active)",
                pre, ticker, out.skipped_reason,
            )
            # Position still has its prior stop (cancel failed) — not unprotected
            return out

    # Step 2: submit each limit tranche
    for tranche in limit_tranches:
        try:
            resp = await client.submit_limit_order(
                symbol=ticker,
                qty=tranche.qty,
                side="sell",
                limit_price=tranche.target,
            )
            tranche_oid = resp.get("id", "")
            out.tranche_order_ids.append(tranche_oid)
            logger.info(
                "%sexit_ladder %s: tranche T%d submitted qty=%d "
                "limit=$%.4f oid=%s",
                pre, ticker, tranche.tranche_number, tranche.qty,
                tranche.target, tranche_oid,
            )
        except Exception as e:
            out.partial_failure = True
            logger.error(
                "%sexit_ladder %s: tranche T%d submit FAILED qty=%d "
                "limit=$%.4f: %s — continuing to residual stop "
                "to minimize naked window",
                pre, ticker, tranche.tranche_number, tranche.qty,
                tranche.target, e,
            )
            # Continue to the residual stop — better to protect what
            # we can than abandon everything.

    # Step 3: submit residual stop (closes the naked window)
    if residual_qty > 0:
        try:
            stop_resp = await client.submit_stop_order(
                symbol=ticker,
                qty=residual_qty,
                side="sell",
                stop_price=stop_price,
                time_in_force="gtc",  # Fix 1: never DAY for protective stops
                position_intent="close",  # D124
            )
            out.new_stop_order_id = stop_resp.get("id", "")
            logger.info(
                "%sexit_ladder %s: residual stop submitted qty=%d "
                "stop=$%.4f oid=%s (naked window closed)",
                pre, ticker, residual_qty, stop_price, out.new_stop_order_id,
            )
        except Exception as e:
            out.is_position_unprotected = True
            logger.critical(
                "%sexit_ladder %s: residual stop SUBMIT FAILED — "
                "POSITION UNPROTECTED. qty=%d stop=$%.4f err=%s",
                pre, ticker, residual_qty, stop_price, e,
            )
    else:
        # residual_qty == 0: tranches consumed the entire position.
        # No protective stop needed — the limit ladder IS the protection
        # (price must climb to fill them; if it falls, no broker-side
        # safety net). Caller should be aware.
        out.is_position_unprotected = True
        logger.warning(
            "%sexit_ladder %s: residual_qty=0 (limit tranches consumed "
            "full position) — NO residual stop submitted, position has "
            "ONLY upside-target ladder for protection. Verify intended.",
            pre, ticker,
        )

    return out
