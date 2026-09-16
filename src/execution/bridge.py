"""
MOMENTUM-X Execution Bridge

### ARCHITECTURAL CONTEXT
Node ID: execution.bridge
Graph Link: docs/memory/graph_state.json → "execution.bridge"

### RESEARCH BASIS
Coordinates the handoff between pipeline evaluation and trade execution.
Orchestrator produces (TradeVerdict, ScoredCandidate) → ExecutionBridge:
  1. Checks PositionManager.can_enter_new_position()
  2. Calls AlpacaExecutor.execute(verdict) → OrderResult
  3. Builds ManagedPosition from OrderResult
  4. Caches ScoredCandidate for Shapley attribution
  5. Adds position to PositionManager

On close:
  1. PositionManager.close_position_with_attribution() → EnrichedTradeResult
  2. PostTradeAnalyzer.analyze_with_shapley() updates Elo

Ref: ADR-003 (Execution Layer)
Ref: ADR-014 (Pipeline Closure)
Ref: ADR-015 (Production Readiness, D2)

### CRITICAL INVARIANTS
1. Circuit breaker check BEFORE executor call (no wasted API calls).
2. ScoredCandidate cached BEFORE order submission (crash-safe attribution).
3. OrderResult failure → no position tracked, cache cleared.
4. NO_TRADE verdicts never reach executor.
"""

from __future__ import annotations

import asyncio
import logging
import weakref
from datetime import datetime, timezone
from typing import Any

from config.settings import Settings
from src.core.models import ScoredCandidate, TradeVerdict
from src.execution.alpaca_executor import AlpacaExecutor, OrderResult
from src.execution.position_manager import ManagedPosition, PositionManager
from src.execution.trade_result_tracker import TradeResult, TradeResultTracker
from src.monitoring.metrics import get_metrics

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────
# D217 / D218 — Bug D regression: terminal-fill polling + qty-drift assert
# ────────────────────────────────────────────────────────────────────────
# Bug D root cause (Wed 2026-04-22 EOD): the inline poll loop treated
# `filled_avg_price > 0` as the terminal-state signal. Partially-filled
# orders also have a non-zero filled_avg_price, so AGPU's first poll at
# submit+2s caught a 505/846-share partial and broke out of the loop.
# Internal tracker held 505; broker held 846. Exit sold 846 (broker-driven);
# attribution journal recorded 505 — silent qty drift.
#
# Fix:
#   1. Poll until status ∈ TERMINAL_ORDER_STATES (not until fill_price > 0).
#   2. After entry, schedule T+5s/T+30s/T+60s broker-truth assertions that
#      log "D218 QTY_DRIFT" warnings + auto-reconcile internal qty.
#   3. Helpers are module-level + free-standing so they can be unit-tested
#      without spinning up the full ExecutionBridge.

TERMINAL_ORDER_STATES = frozenset({
    "filled", "done_for_day", "canceled", "expired", "rejected", "replaced",
})
TERMINAL_SUCCESS_STATES = frozenset({"filled", "done_for_day"})


async def _poll_for_terminal_fill(
    *,
    client: Any,
    order_id: str,
    ticker: str,
    max_polls: int = 6,
    poll_interval_s: float = 2.0,
) -> dict[str, Any] | None:
    """
    Poll the broker for an order until it reaches a terminal state.

    Returns the final order dict (terminal OR last-seen partial if max_polls
    exhausted) so the caller can decide what to do. Returns None only if
    the broker call raises or the order genuinely cannot be located after
    every poll.

    Terminal status set:
      success: {filled, done_for_day}
      failure: {canceled, expired, rejected, replaced}
      partial: {partially_filled} ← keeps polling; returned only if
               max_polls is exhausted

    Bug D fix: previously the inline loop broke on `filled_avg_price > 0`
    which fires on partial fills too. Now we explicitly check `status`.

    Args:
        client: Alpaca async client (must implement get_orders).
        order_id: Broker order id to follow.
        ticker: Symbol (used only for the symbols filter on get_orders).
        max_polls: How many times to poll before giving up. Default 6
            (12s total at 2s spacing). Bumped from 3 since AGPU's
            terminal fill arrived at submit+4.5s — 3 polls = 6s was just
            enough on a clean run but left no margin.
        poll_interval_s: Seconds between polls.

    Returns:
        The terminal (or last-seen partial) order dict, or None on
        broker-API failure or genuine not-found.
    """
    last_seen: dict[str, Any] | None = None
    for poll_idx in range(max_polls):
        try:
            orders = await client.get_orders(
                status="all", limit=5, symbols=ticker,
            )
        except Exception as e:
            logger.warning(
                "D217: get_orders raised for %s (poll %d/%d): %s",
                ticker, poll_idx + 1, max_polls, e,
            )
            return last_seen  # may be None — caller handles

        if not orders:
            logger.debug(
                "D217: get_orders empty for %s (poll %d/%d)",
                ticker, poll_idx + 1, max_polls,
            )
            # Wait then retry — order may not have propagated yet.
            if poll_idx < max_polls - 1:
                await asyncio.sleep(poll_interval_s)
            continue

        match = next(
            (o for o in orders if o.get("id") == order_id),
            None,
        )
        if match is None:
            logger.debug(
                "D217: order %s not in returned page for %s (poll %d/%d)",
                order_id[:8] if order_id else "?", ticker,
                poll_idx + 1, max_polls,
            )
            if poll_idx < max_polls - 1:
                await asyncio.sleep(poll_interval_s)
            continue

        last_seen = match
        status = match.get("status", "unknown")

        if status in TERMINAL_ORDER_STATES:
            logger.info(
                "D217: %s order %s reached terminal status=%s "
                "filled_qty=%s filled_avg_price=%s (poll %d/%d)",
                ticker, (order_id or "")[:8], status,
                match.get("filled_qty"), match.get("filled_avg_price"),
                poll_idx + 1, max_polls,
            )
            return match

        # Non-terminal — partially_filled, accepted, new, pending_new, etc.
        logger.debug(
            "D217: %s order %s status=%s filled_qty=%s (poll %d/%d) — continuing",
            ticker, (order_id or "")[:8], status,
            match.get("filled_qty"), poll_idx + 1, max_polls,
        )
        if poll_idx < max_polls - 1:
            await asyncio.sleep(poll_interval_s)

    # Exhausted polls without terminal — return the best partial we saw.
    if last_seen is not None:
        logger.warning(
            "D217: %s order %s never reached terminal in %d polls "
            "(last status=%s, last filled_qty=%s) — returning partial",
            ticker, (order_id or "")[:8], max_polls,
            last_seen.get("status"), last_seen.get("filled_qty"),
        )
    return last_seen


# ────────────────────────────────────────────────────────────────────────
# D91 / Bug E — Overnight-position detection + close routine
# ────────────────────────────────────────────────────────────────────────
# Bug E root cause (Wed 2026-04-22 EOD): D91 detection at main.py:1161
# was gated on `session_state is None`. After the Bug B Phase-3 restart
# fix, session_state survives restart — so the elif branch never fired
# and ELSE (held overnight from Tue 21:48) silently skipped the 09:30
# market-open close routine.
#
# Fix:
#   1. `_is_overnight_position` — pure detection by opened_at vs
#      today's 04:00 ET pre-market start. Independent of session_state.
#   2. ManagedPosition.close_pending flag — set at detection so
#      `filter_eligible_for_eval` excludes the position from re-scoring.
#   3. `_close_overnight_position` — explicit step-by-step close routine:
#         STEP 1: cancel stop_order_id (if present)
#         STEP 2: submit market sell via close_position
#         STEP 3: remove from internal tracker
#      Each step emits one INFO log line with the D91 STEP N marker.

# Pre-market session start: Alpaca extended-hours opens at 04:00 ET.
# A position opened before today's 04:00 ET boundary is "overnight".
_PREMARKET_OPEN_HOUR_ET = 4


def _is_overnight_position(*, opened_at: datetime, now_et: datetime) -> bool:
    """
    True iff `opened_at` is strictly before today's 04:00 ET pre-market
    open. Both arguments must be timezone-aware. Comparison is done in
    the timezone of `now_et` so DST transitions are handled correctly.

    Bug E fix: this used to live inline at main.py:1161 gated on
    `session_state is None`. The session-state coupling is wrong; an
    overnight position is overnight whether or not we have prior session
    state. Now a pure function of opened_at vs today's pre-market open.
    """
    today_premarket_open = now_et.replace(
        hour=_PREMARKET_OPEN_HOUR_ET, minute=0, second=0, microsecond=0,
    )
    # Normalize opened_at to the now_et timezone for comparison
    opened_in_et = opened_at.astimezone(now_et.tzinfo)
    return opened_in_et < today_premarket_open


def filter_eligible_for_eval(positions: Any) -> list[Any]:
    """
    Filter out positions tagged close_pending=True so the agent eval /
    scorer pipeline doesn't waste cycles on positions slated for an
    imminent operator close.

    Backward-compat: positions that don't expose `close_pending`
    (e.g., third-party mocks) are treated as eligible.
    """
    return [p for p in positions if not getattr(p, "close_pending", False)]


async def _cancel_order_or_warn(
    client: Any,
    order_id: str,
    ticker: str,
) -> bool:
    """
    Bug V fix (2026-04-24) helper. Cancel an order at the broker before
    the bridge gives up tracking it. Used in the rejection branches of
    `execute_verdict` to prevent ghost positions from late fills on
    day-only OTO orders.

    Behaviour:
      - On success: log D237 BRIDGE_CANCEL INFO with ticker + order_id
      - On failure: log D237 BRIDGE_CANCEL WARN with the failure cause
      - Always returns (does not raise) so the caller's rejection
        return-None path is never blocked by a broker cancel failure

    Returns True if cancel succeeded, False otherwise (informational
    — caller is expected to ignore and proceed with rejection).
    """
    if not order_id:
        return False
    try:
        await client.cancel_order(order_id)
        logger.info(
            "D237 BRIDGE_CANCEL %s: cancelled order %s at broker "
            "(prevents ghost fill from rejected day-OTO)",
            ticker, (order_id or "?")[:8],
        )
        return True
    except Exception as e:
        logger.warning(
            "D237 BRIDGE_CANCEL %s: cancel of order %s FAILED (%s) — "
            "broker may still fill this order; manual intervention "
            "may be required to avoid a ghost position",
            ticker, (order_id or "?")[:8], e,
        )
        return False


async def _cancel_blocking_sell_orders(
    *,
    client: Any,
    ticker: str,
) -> list[dict[str, Any]]:
    """
    Bug AI helper (2026-04-27): cancel any open SELL orders on `ticker`
    that are reserving the position's shares (i.e., blocking close attempts
    with Alpaca's `40310000 — insufficient qty available` error).

    Returns a list of snapshot dicts (one per cancelled order) so the
    caller can re-arm protective stops if the close ultimately fails:
      [{"id": str, "side": str, "qty": int, "type": str,
        "stop_price": float|None, "limit_price": float|None}, ...]

    Why this exists: today's LIDR catastrophe was that the operator-
    submitted protective stop @ $2.10 reserved all 5264 shares. Every
    bridge close attempt (BAR-1 exit, EOD MOO) hit Alpaca with 403
    `available=0`. The bridge correctly refused to silently cancel the
    stop (Bug Z safety contract) but had no way to coordinate. Forced
    closes need an explicit cancel-and-coordinate path.

    Safety contract: this function ONLY cancels SELL orders for a long
    position close. It does NOT touch take-profit limit children unless
    the caller specifies. It returns the snapshot so the caller can
    re-arm protection on failure.
    """
    cancelled: list[dict[str, Any]] = []
    try:
        open_orders = await client.get_orders(status="open", limit=100, symbols=ticker)
    except Exception as e:
        logger.warning(
            "D248 BLOCKING_STOPS %s: get_orders raised (%s) — "
            "cannot enumerate competing stops; proceeding without cancel",
            ticker, e,
        )
        return cancelled

    for order in open_orders or []:
        sym = (order.get("symbol") or "").upper()
        side = (order.get("side") or "").lower()
        otype = (order.get("type") or "").lower()
        if sym != ticker.upper() or side != "sell":
            continue
        # Only cancel orders that REDUCE the position (sell stops + sell limits
        # against the long). Skip if it's already in a terminal state somehow.
        oid = order.get("id") or ""
        if not oid:
            continue
        snap = {
            "id": oid,
            "side": side,
            "qty": int(float(order.get("qty") or 0)),
            "type": otype,
            "stop_price": (
                float(order["stop_price"]) if order.get("stop_price") is not None else None
            ),
            "limit_price": (
                float(order["limit_price"]) if order.get("limit_price") is not None else None
            ),
            "client_order_id": order.get("client_order_id"),
        }
        try:
            await client.cancel_order(oid)
            cancelled.append(snap)
            logger.info(
                "D248 BLOCKING_STOPS %s: cancelled %s sell %s qty=%d "
                "stop=$%s limit=$%s id=%s (was reserving shares)",
                ticker, otype, snap["client_order_id"] or "?",
                snap["qty"], snap["stop_price"], snap["limit_price"], oid[:8],
            )
        except Exception as e:
            logger.warning(
                "D248 BLOCKING_STOPS %s: failed to cancel %s id=%s: %s",
                ticker, otype, oid[:8], e,
            )
    return cancelled


async def _await_qty_available(client, ticker: str, need_qty: int,
                              *, timeout_s: float = 6.0,
                              poll_s: float = 0.5) -> bool:
    """doc 216: poll the broker until `ticker` has >= need_qty shares AVAILABLE (i.e. the
    just-cancelled blocking stop has SETTLED and freed qty_available). Returns True once the
    qty is free, False on timeout. Bounded; never raises. qty_available is Alpaca's native
    field — the exact precondition a market-close/sell needs (the '403 available:0' we kept
    hitting on 6/1 because the cancel hadn't settled within the blind 0.5s wait)."""
    import asyncio as _aio
    waited = 0.0
    while waited < timeout_s:
        await _aio.sleep(poll_s)
        waited += poll_s
        try:
            ps = await client.get_positions()
            p = next((x for x in (ps or []) if x.get("symbol") == ticker), None)
            if p is None:
                return True  # position gone entirely → nothing reserved
            avail = abs(int(float(p.get("qty_available", p.get("qty", 0)) or 0)))
            if avail >= need_qty:
                return True
        except Exception:
            return False  # can't query → caller's retry/backoff backstops
    return False


async def _confirm_partial_close_fill(
    client: Any,
    ticker: str,
    order_id: str,
    *,
    timeout_s: float = 8.0,
    poll_s: float = 0.5,
) -> dict[str, Any] | None:
    """doc 269 (A1): poll the broker until the partial-close order reaches a
    TERMINAL state; return the final order dict (or the latest non-terminal
    view on timeout, or None if the order was never observable).

    Partial exits (D164 early-profit-take, D165 tranche-take) book REAL P&L
    directly from this order's fill. Unlike full closes — where the position
    record disappears and close_with_attribution / EOD recon backstop the
    price — a partial booking from a merely-ACCEPTED order is exactly the
    phantom-P&L disease (TNGX/ABAT 6/8, doc 263), so the fill must be
    broker-CONFIRMED before any booking. Bounded; never raises."""
    import asyncio as _aio
    terminal = {
        "filled", "canceled", "cancelled", "rejected", "expired",
        "done_for_day", "replaced",
    }
    last: dict[str, Any] | None = None
    waited = 0.0
    while True:
        try:
            orders = await client.get_orders(
                status="all", limit=100, symbols=ticker,
            )
        except Exception as e:
            logger.warning(
                "D269 CONFIRM %s: get_orders raised (%s) — retrying within budget",
                ticker, e,
            )
            orders = None
        for o in orders or []:
            if str(o.get("id") or "") == order_id:
                last = o
                break
        if last is not None and str(last.get("status", "")).lower() in terminal:
            return last
        if waited >= timeout_s:
            return last
        await _aio.sleep(poll_s)
        waited += poll_s


def _order_fill_fields(order: dict[str, Any] | None) -> tuple[str, int, float]:
    """doc 269: extract (status, filled_qty, filled_avg_price) from an order
    dict, tolerating Alpaca's string-typed numerics. Never raises."""
    if not isinstance(order, dict):
        return "", 0, 0.0
    status = str(order.get("status", "")).lower()
    try:
        fq = int(float(order.get("filled_qty") or 0))
    except (TypeError, ValueError):
        fq = 0
    try:
        fp_raw = order.get("filled_avg_price")
        fp = float(fp_raw) if fp_raw else 0.0
    except (TypeError, ValueError):
        fp = 0.0
    return status, fq, fp


async def _rearm_protective_stop_from_snapshot(
    *,
    client: Any,
    ticker: str,
    snapshot: dict[str, Any],
) -> dict[str, Any] | None:
    """
    Bug AI helper (2026-04-27): re-submit a protective stop using the
    parameters captured by `_cancel_blocking_sell_orders` before a close
    attempt. Used when the close FAILED — we cancelled the stop, the
    position is still open, so we must restore protection before
    returning to the caller.

    Only re-arms STOP orders (not take-profit limit children — those
    were intentionally part of the OTO bracket and the caller can
    re-create them via the OTO path if needed).

    Returns the broker response dict on success, or None on failure
    (logged as D249 STOP_REARM_FAILED — caller should escalate).
    """
    if snapshot.get("type") != "stop" or snapshot.get("stop_price") is None:
        return None
    # doc 180 FIX: this previously built a payload dict and called
    # client.submit_order(payload). But AlpacaDataClient.submit_order's
    # signature is submit_order(symbol, qty, side, ...) — so the single-dict
    # call raised "submit_order() missing 2 required positional arguments:
    # 'qty' and 'side'" and the re-arm FAILED, leaving the position NAKED.
    # Observed live on APPS 2026-05-29 09:30:18: the doc-177 slice fix made the
    # cancel-blocking-stops path actually execute, which exposed this latent
    # bug in the re-arm safety net (D313 hedge-watcher re-hedged ~90s later).
    # Use the dedicated submit_stop_order (gtc; position_intent='close' to
    # avoid 422 on short-sale-restricted names, matching every other protective
    # stop caller).
    try:
        _rearm_qty = int(float(snapshot["qty"]))
        _rearm_stop = float(snapshot["stop_price"])
        resp = await client.submit_stop_order(
            symbol=ticker,
            qty=_rearm_qty,
            side=(snapshot.get("side") or "sell"),
            stop_price=_rearm_stop,
            time_in_force="gtc",
            position_intent="close",
        )
        logger.warning(
            "D249 STOP_REARM %s: re-submitted protective stop @ $%.2f qty=%d "
            "(close failed; original stop %s was cancelled). New oid=%s",
            ticker, _rearm_stop, _rearm_qty,
            snapshot["id"][:8], (resp.get("id") or "?")[:8] if isinstance(resp, dict) else "?",
        )
        return resp if isinstance(resp, dict) else {"status": "submitted"}
    except Exception as e:
        logger.error(
            "D249 STOP_REARM_FAILED %s: failed to re-submit protective stop "
            "@ $%.2f qty=%d after close FAILED. POSITION IS NOW NAKED. "
            "Operator MUST intervene. Original stop oid=%s. Error: %s",
            ticker, snapshot["stop_price"], snapshot["qty"],
            snapshot["id"][:8], e,
        )
        return None


# ────────────────────────────────────────────────────────────────────────
# doc 286 (doc-285 gap #10) — confirmed-partial-fill qty write-back
# ────────────────────────────────────────────────────────────────────────
# The 7/6 RIVN Bug-D regression: every partial-exit submitting path
# (D164 early-take / D165 tranche / D269 P2) decrements
# ManagedPosition.remaining_qty after this module confirms a partial
# fill, but NOTHING decremented .qty — the field eod_recon (D231 QTY),
# D218 QTY_DRIFT and attach_external_stop all treat as the current
# position size. RIVN sold 517 @ 10:04 (broker 1552→1035) and the
# tracker said qty=1552 for the rest of the session — which then ALSO
# blocked the D313 emergency-stop oid write-back (qty-mismatch refusal).
# The submitting paths live in main.py and own remaining_qty /
# tranches_filled / P&L (doc-270 contract: "tracker left to the
# submitting path"); the ONE common point every broker-CONFIRMED
# partial close flows through is attempt_close_with_status_check. So
# ExecutionBridge registers the live PositionManager at construction
# and the helper books the confirmed fill onto .qty (and ONLY .qty)
# here — on confirmed broker evidence, never on a failed/absent close.

_active_pm_ref: Any = None  # weakref.ref to the live PositionManager


def _register_active_position_manager(pm: Any) -> None:
    """doc 286: remember the live PositionManager (weakly) so the
    module-level close helper can book confirmed partial fills onto the
    tracked position's qty. Never raises; unregisterable objects (or
    None) simply clear the registry."""
    global _active_pm_ref
    try:
        _active_pm_ref = weakref.ref(pm) if pm is not None else None
    except TypeError:  # non-weakref-able test double — behave as unset
        _active_pm_ref = None


def _book_confirmed_partial_qty(ticker: str, filled_qty: Any) -> None:
    """doc 286: decrement the tracked ManagedPosition.qty by a
    broker-CONFIRMED partial fill.

    Touches ONLY .qty — remaining_qty / tranches_filled / realized P&L
    belong to the submitting path (doc-270 contract). Guarded
    end-to-end: no registered manager, unknown ticker, or a bad qty is
    a silent no-op; book-keeping must NEVER break a confirmed close."""
    try:
        fq = int(filled_qty or 0)
    except (TypeError, ValueError):
        return
    if fq <= 0:
        return
    pm = _active_pm_ref() if _active_pm_ref is not None else None
    if pm is None:
        return
    try:
        _getp = getattr(pm, "get_position", None)
        pos = _getp(ticker) if callable(_getp) else None
        if pos is None:
            return
        cur_qty = int(getattr(pos, "qty", 0) or 0)
        if cur_qty <= 0:
            return
        pos.qty = max(0, cur_qty - fq)
        logger.info(
            "doc286 PARTIAL_QTY_BOOKED %s: qty %d -> %d after broker-"
            "confirmed partial fill of %d (recon-facing field; "
            "remaining_qty stays owned by the submitting path)",
            ticker, cur_qty, pos.qty, fq,
        )
    except Exception as e:  # noqa: BLE001 — book-keeping never breaks a confirmed close
        logger.warning(
            "doc286 PARTIAL_QTY_BOOK failed %s (qty drift may persist "
            "until D218/D231 reconcile): %s", ticker, e,
        )


async def attempt_close_with_status_check(
    *,
    client: Any,
    ticker: str,
    qty: int,
    max_retries: int = 3,
    retry_backoff_s: float = 1.0,
    cancel_blocking_stops_first: bool = False,
    partial: bool = False,
) -> dict[str, Any]:
    """
    Bug Z fix (Fri 2026-04-24, Knight-Capital-class).
    Bug AI extension (2026-04-27): coordinated stop-cancel-then-close.
    doc 269 extension (2026-06-09): `partial=True` closes only `qty` shares.

    Attempt to close a position via the broker, with explicit
    status-checking + retry-with-backoff. Today's LIDR catastrophe
    happened because the SMART_EXIT path treated a 403 broker
    rejection as silent success and proceeded to cancel protective
    stops, mark the position internally-closed at fake-positive P&L,
    while the broker still held it naked.

    Behaviour:
      - On 2xx success → return {"succeeded": True, "fill_price":...,
                                "filled_qty":..., "attempts": N}
      - On 4xx/5xx/raise → emit D245 SMART_EXIT_REJECTED, retry up to
                            max_retries with exponential backoff
      - On retry success → emit D246 SMART_EXIT_RETRY for the prior
                            failures, return succeeded=True
      - On retry exhaustion → emit D247 SMART_EXIT_ESCALATE, return
                              succeeded=False, attempts=max_retries

    Bug AI extension — `cancel_blocking_stops_first`:
      - When True AND a close attempt fails with the broker's
        `insufficient qty available` error (403 with `available=0`,
        Alpaca code 40310000), the function:
          1. Enumerates open SELL orders on `ticker` via get_orders
          2. Cancels each (blocking-stop pattern), snapshotting params
          3. Briefly waits for broker propagation (0.5s)
          4. Retries the close
      - If the close ultimately fails after stop-cancel, the function
        RE-ARMS each cancelled stop using the snapshot before
        escalating with D247. Position is never left naked.
      - When False (default), preserves the original Bug Z behavior:
        does NOT touch protective stops; D247 escalates to operator.

    The CALLER is responsible for honoring the contract:
      - If succeeded=False AND cancel_blocking_stops_first=False:
        do NOT mutate tracker; do NOT cancel stops; escalate.
      - If succeeded=False AND cancel_blocking_stops_first=True:
        the function has attempted to re-arm any cancelled stops.
        Caller must verify via broker query before assuming protection.

    Args:
        client: Alpaca async client.
        ticker: Symbol to close.
        qty: Position quantity (used for fallback paths).
        max_retries: Max close attempts before D247 escalates.
        retry_backoff_s: Base seconds for exponential backoff
                         (attempt N waits retry_backoff_s * 2**(N-1)).
        cancel_blocking_stops_first: Bug AI — opt in to the
            cancel-stops-then-close coordination. Use ONLY for force-
            close paths (BAR-1 exit, EOD MOO, smart exit on full
            position) or doc-269 partial exits (see `partial`). For a
            partial close the protective stop reserves the FULL qty, so
            the dance is required to free shares — but the CALLER must
            re-arm/resize the stop for the REMAINING qty on success
            (the helper re-arms the full snapshot only on FAILURE).
        partial: doc 269 (A1) — close only `qty` shares via
            DELETE /v2/positions/{symbol}?qty= instead of the whole
            position. In this mode a 2xx submit is NOT enough: the
            order is polled to a TERMINAL state and `succeeded=True` is
            returned ONLY with a broker-confirmed fill_price/filled_qty
            (partial bookings have no close_with_attribution / EOD-recon
            price backstop, so an unconfirmed booking would be phantom
            P&L — TNGX/ABAT 6/8 family, doc 263). If the order cannot
            be confirmed it is cancelled and the call escalates with
            succeeded=False (D269 PARTIAL_CLOSE_UNCONFIRMED).

    Returns:
        dict with keys: succeeded (bool), attempts (int),
        fill_price (float|None), filled_qty (int|None),
        last_error (str|None), cancelled_stops (list — Bug AI)
    """
    result: dict[str, Any] = {
        "succeeded": False,
        "attempts": 0,
        "fill_price": None,
        "filled_qty": None,
        "last_error": None,
        "cancelled_stops": [],  # Bug AI: snapshots of stops we cancelled
    }
    cancelled_snapshots: list[dict[str, Any]] = []
    # doc 269: a partial close MUST carry a positive qty — close_position(qty=0/None)
    # degrades to a FULL-position DELETE, which would liquidate the whole book on a
    # caller bug. All current callers guard qty>0; this is belt-and-braces.
    if partial and qty <= 0:
        result["last_error"] = f"partial close requires qty > 0 (got {qty})"
        logger.error(
            "D269 PARTIAL_CLOSE %s: refusing partial close with qty=%s — "
            "would degrade to a FULL-position close", ticker, qty,
        )
        return result
    # doc 216: settle-waits (after cancelling a blocking stop) get their OWN bounded budget
    # so they do NOT consume the close-retry budget. Without this, the cancel-then-settle
    # dance burned all max_retries before the broker freed qty_available (6/1 CMND/OPTU went
    # naked + carried). Hard-capped to prevent any infinite loop.
    settle_budget = 3
    attempt = 0
    while attempt < max_retries:
        attempt += 1
        result["attempts"] = attempt
        try:
            if partial:
                # doc 269: partial exit — only `qty` shares leave the book.
                response = await client.close_position(ticker, qty=qty)
            else:
                response = await client.close_position(ticker)
            # extract fill details
            _status = ""
            if isinstance(response, dict):
                _status = str(response.get("status", "")).lower()
                _fp = response.get("filled_avg_price")
                _fq = response.get("filled_qty")
                if _fp is not None:
                    try:
                        result["fill_price"] = float(_fp)
                    except (TypeError, ValueError) as _e:
                        logger.debug("D245 close response: bad filled_avg_price %r: %s", _fp, _e)
                if _fq is not None:
                    try:
                        result["filled_qty"] = int(float(_fq))
                    except (TypeError, ValueError) as _e:
                        logger.debug("D245 close response: bad filled_qty %r: %s", _fq, _e)
            # doc 269 (A1): PARTIAL mode — the caller books P&L for a fraction
            # of the position DIRECTLY from this result, so success requires a
            # broker-CONFIRMED fill (full mode tolerates fill_price=None because
            # close_with_attribution + EOD recon backstop the price; a partial
            # booking has no such backstop). Every branch below returns,
            # continues (retry) or breaks (escalate) — full-mode flow is
            # untouched.
            if partial:
                _oid = (
                    str(response.get("id") or "")
                    if isinstance(response, dict) else ""
                )
                # Derive strictly from THIS attempt's response — `result`
                # may carry stale fill fields from a prior dead attempt.
                _r_status, _r_qty, _r_px = _order_fill_fields(
                    response if isinstance(response, dict) else None
                )
                if _r_status == "filled" and _r_px > 0:
                    # Broker confirmed the fill in the submit response itself.
                    result["fill_price"] = _r_px
                    result["filled_qty"] = _r_qty or qty
                    result["succeeded"] = True
                    if attempt > 1:
                        logger.info(
                            "D246 SMART_EXIT_RETRY %s: partial close succeeded "
                            "on attempt %d/%d", ticker, attempt, max_retries,
                        )
                    _book_confirmed_partial_qty(ticker, result["filled_qty"])  # doc 286
                    return result
                if not _oid:
                    # No order id → the fill can never be confirmed. Do NOT
                    # retry blindly (the unconfirmable order may still fill →
                    # double-sell). Escalate.
                    result["succeeded"] = False
                    result["last_error"] = (
                        "partial close response had no order id; "
                        "fill unconfirmable"
                    )
                    logger.error(
                        "D269 PARTIAL_CLOSE_UNCONFIRMED %s: %s — escalating, "
                        "no P&L may be booked",
                        ticker, result["last_error"],
                    )
                    break
                _final = await _confirm_partial_close_fill(client, ticker, _oid)
                _f_status, _f_qty, _f_px = _order_fill_fields(_final)
                if _f_status == "filled" and _f_px > 0:
                    result["succeeded"] = True
                    result["fill_price"] = _f_px
                    result["filled_qty"] = _f_qty or qty
                    if attempt > 1:
                        logger.info(
                            "D246 SMART_EXIT_RETRY %s: partial close succeeded "
                            "on attempt %d/%d", ticker, attempt, max_retries,
                        )
                    _book_confirmed_partial_qty(ticker, result["filled_qty"])  # doc 286
                    return result
                if _f_status in ("canceled", "cancelled", "rejected", "expired"):
                    if _f_qty > 0 and _f_px > 0:
                        # Terminal partial-of-partial: book EXACTLY what the
                        # broker confirms filled — never the requested qty.
                        result["succeeded"] = True
                        result["fill_price"] = _f_px
                        result["filled_qty"] = _f_qty
                        result["partial_filled_qty"] = _f_qty
                        logger.warning(
                            "D269 PARTIAL_CLOSE %s: order %s terminal=%s with "
                            "%d/%d filled — booking the confirmed fraction only",
                            ticker, _oid[:8], _f_status, _f_qty, qty,
                        )
                        _book_confirmed_partial_qty(ticker, _f_qty)  # doc 286
                        return result
                    # Terminal with nothing filled → nothing sold; safe to retry.
                    result["last_error"] = (
                        f"partial close order {_oid[:8]} {_f_status} with 0 filled"
                    )
                    logger.warning(
                        "D245 SMART_EXIT_REJECTED %s (attempt %d/%d): %s",
                        ticker, attempt, max_retries, result["last_error"],
                    )
                    if attempt < max_retries:
                        await asyncio.sleep(retry_backoff_s * (2 ** (attempt - 1)))
                    continue
                # Still live after the confirm budget: cancel it (prevents an
                # untracked later fill), then re-check ONCE — cancel can race
                # a fill and the fill wins.
                try:
                    await client.cancel_order(_oid)
                except Exception as _cx:
                    logger.warning(
                        "D269 PARTIAL_CLOSE %s: cancel of unconfirmed order %s "
                        "raised: %s", ticker, _oid[:8], _cx,
                    )
                _final = await _confirm_partial_close_fill(
                    client, ticker, _oid, timeout_s=2.0,
                )
                _f_status, _f_qty, _f_px = _order_fill_fields(_final)
                if _f_qty > 0 and _f_px > 0:
                    result["succeeded"] = True
                    result["fill_price"] = _f_px
                    result["filled_qty"] = _f_qty
                    if _f_qty < qty:
                        result["partial_filled_qty"] = _f_qty
                    _book_confirmed_partial_qty(ticker, _f_qty)  # doc 286
                    return result
                if _f_status in ("canceled", "cancelled", "rejected", "expired"):
                    # Confirmed dead with nothing filled → safe to retry.
                    result["last_error"] = (
                        f"partial close order {_oid[:8]} cancelled unfilled "
                        "after confirm timeout"
                    )
                    logger.warning(
                        "D245 SMART_EXIT_REJECTED %s (attempt %d/%d): %s",
                        ticker, attempt, max_retries, result["last_error"],
                    )
                    if attempt < max_retries:
                        await asyncio.sleep(retry_backoff_s * (2 ** (attempt - 1)))
                    continue
                # Unknown state (order may still be live and may still fill).
                # Retrying could double-sell — escalate instead.
                result["succeeded"] = False
                result["last_error"] = (
                    f"partial close order {_oid[:8]} UNCONFIRMED "
                    f"(last status={_f_status or '?'}) after cancel+recheck"
                )
                logger.error(
                    "D269 PARTIAL_CLOSE_UNCONFIRMED %s: %s — escalating, "
                    "no P&L may be booked; operator must reconcile order %s",
                    ticker, result["last_error"], _oid,
                )
                break
            # doc 219 (the Adversary's catch): a PARTIAL fill is NOT success. The old code set
            # succeeded=True on any response -> the unfilled remainder STRANDED at the broker
            # with no stop while the bot believed it was flat. Detect partial (status or
            # filled_qty < requested) and RE-CLOSE the remainder instead of declaring victory.
            _fq_done = result.get("filled_qty")
            _is_partial = (_status == "partially_filled") or (
                _fq_done is not None and 0 < int(_fq_done) < qty)
            if _is_partial:
                result["partial_filled_qty"] = int(_fq_done or 0)
                logger.warning(
                    "D219 PARTIAL_CLOSE %s: filled %s/%d — remainder NOT flat; re-closing "
                    "the remainder (attempt %d/%d)",
                    ticker, _fq_done, qty, attempt, max_retries,
                )
                if attempt < max_retries:
                    await asyncio.sleep(0.5)  # let the partial settle
                    continue  # loop back and close the remainder
                # out of attempts with a remainder still open -> NOT a clean success
                result["succeeded"] = False
                result["last_error"] = f"partial fill {_fq_done}/{qty}, remainder open"
                break
            result["succeeded"] = True
            if attempt > 1:
                logger.info(
                    "D246 SMART_EXIT_RETRY %s: succeeded on attempt %d/%d",
                    ticker, attempt, max_retries,
                )
            return result
        except Exception as e:
            # Bug AR fix (2026-04-28): str(e) on httpx.HTTPStatusError
            # only renders "Client error '403 Forbidden' for url ..." —
            # the Alpaca JSON body containing "insufficient qty
            # available" / "available: 0" / code 40310000 is in
            # e.response.text. Without extracting it, the Bug AI
            # substring match below NEVER fires and cancel-and-coordinate
            # is dead code. Today's LIDR 10:00:38 ET 403 confirmed
            # this — D248 BLOCKING_STOPS log line was absent from the
            # transcript despite a textbook-shape qty conflict.
            err_body = ""
            resp = getattr(e, "response", None)
            if resp is not None:
                try:
                    err_body = resp.text or ""
                except Exception:  # noqa: BLE001 — last-resort defensive
                    err_body = ""
            err_str = f"{type(e).__name__}: {e}"
            if err_body:
                err_str = f"{err_str}; body={err_body}"
            result["last_error"] = err_str
            logger.warning(
                "D245 SMART_EXIT_REJECTED %s (attempt %d/%d): %s",
                ticker, attempt, max_retries, err_str,
            )
            # Bug AI: detect "insufficient qty available" + cancel blocking
            # stops on the FIRST occurrence, then retry. We only do this
            # once per call (cancelled_snapshots non-empty acts as the
            # "already tried this dance" sentinel).
            #
            # Bug AR (2026-04-28): match against the COMBINED string
            # (exception repr + response body). Case-insensitive to
            # tolerate Alpaca casing drift. "40310000" is the Alpaca
            # error code for qty conflicts and is the most reliable
            # signal — it appears in the JSON body even when the
            # human-readable message changes.
            err_lower = err_str.lower()
            is_qty_blocked = (
                cancel_blocking_stops_first
                and not cancelled_snapshots
                and ("insufficient qty available" in err_lower
                     or "40310000" in err_str
                     or "available: 0" in err_lower
                     or "qty available" in err_lower)
            )
            if is_qty_blocked:
                logger.info(
                    "D248 BLOCKING_STOPS %s: 'insufficient qty' detected — "
                    "enumerating and cancelling competing sell orders before retry",
                    ticker,
                )
                fresh_cancels = await _cancel_blocking_sell_orders(
                    client=client, ticker=ticker,
                )
                if fresh_cancels:
                    cancelled_snapshots = fresh_cancels
                    result["cancelled_stops"] = cancelled_snapshots
                # doc 216 (the highest-ROI execution fix): the cancel is ASYNC at the broker
                # and takes 1-3s+ to free qty_available. The old code slept a BLIND 0.5s then
                # retried — and on 6/1 CMND/OPTU the cancel took >2s, so all 3 retries 403'd on
                # the same unsettled cancel, the close FAILED, the stop RE-ARM also 403'd, and
                # both positions went NAKED + carried overnight (the dominant P&L leak). FIX:
                # after cancelling (or if already cancelled upstream), actively POLL
                # qty_available until the shares actually free up, on a SEPARATE bounded budget
                # so it does NOT consume the close-retry budget. Then retry the close.
                if settle_budget > 0:
                    settle_budget -= 1
                    settled = await _await_qty_available(client, ticker, qty, timeout_s=6.0)
                    logger.info(
                        "D248 BLOCKING_STOPS %s: %s; qty %s after settle-poll — retrying "
                        "close (settle budget %d left, attempt budget intact)",
                        ticker,
                        ("cancelled %d blocking order(s)" % len(fresh_cancels)) if fresh_cancels
                        else "already cancelled upstream",
                        "FREED" if settled else "still reserved", settle_budget,
                    )
                    if not cancelled_snapshots:
                        cancelled_snapshots = ["__waited_for_settle__"]
                        result["cancelled_stops"] = []
                    attempt -= 1  # the settle-wait does NOT consume a close attempt
                    continue
            # Standard backoff between retries
            if attempt < max_retries:
                logger.info(
                    "D246 SMART_EXIT_RETRY %s: retrying after backoff "
                    "(attempt %d failed; will retry %d more time(s))",
                    ticker, attempt, max_retries - attempt,
                )
                await asyncio.sleep(retry_backoff_s * (2 ** (attempt - 1)))

    # Exhausted retries — escalate
    logger.error(
        "D247 SMART_EXIT_ESCALATE %s: %d retries exhausted; "
        "broker close FAILED. Position remains open at broker; "
        "tracker MUST NOT be mutated. Last error: %s",
        ticker, max_retries, result["last_error"],
    )

    # Bug AI: if we cancelled stops as part of the attempt, re-arm them
    # before returning. This preserves the Bug Z safety contract: position
    # must never be left without protection unless the operator explicitly
    # authorizes it.
    # doc 218 (the Adversary's catch): the re-arm itself 403s if the cancelled stop hasn't
    # SETTLED yet (qty still reserved) — exactly the 6/1 "D249 STOP_REARM_FAILED -> NAKED"
    # path. So BEFORE re-arming: (a) wait for qty_available to settle, and (b) only re-arm
    # from REAL snapshots (the "__waited_for_settle__" sentinel is not a stop to re-arm).
    real_snaps = [s for s in cancelled_snapshots if isinstance(s, dict)]
    if real_snaps:
        logger.warning(
            "D249 STOP_REARM %s: close FAILED after cancelling %d stop(s); waiting for qty "
            "to settle, then re-arming protection before returning to caller",
            ticker, len(real_snaps),
        )
        # let the cancel settle so the re-arm doesn't 403 into reserved qty
        await _await_qty_available(client, ticker, qty, timeout_s=6.0)
        rearmed_ok = 0
        for snap in real_snaps:
            try:
                if await _rearm_protective_stop_from_snapshot(
                    client=client, ticker=ticker, snapshot=snap,
                ):
                    rearmed_ok += 1
            except Exception as _re:
                logger.error("D249 STOP_REARM %s: re-arm raised: %s", ticker, _re)
        if rearmed_ok < len(real_snaps):
            # last-resort: the position may be NAKED. emit a CRITICAL incident so the Operator
            # sees it immediately (doc 205/212) — not just a log line that scrolls past.
            logger.error(
                "D249 STOP_REARM_FAILED %s: %d/%d stops re-armed — position may be NAKED. "
                "OPERATOR INTERVENTION REQUIRED.", ticker, rearmed_ok, len(real_snaps),
            )
            try:
                from src.ops.incident_bus import emit_incident as _emit
                _emit("NAKED_POSITION_RISK", "CRITICAL", ticker=ticker,
                      context={"reason": "close failed + stop re-arm incomplete",
                               "rearmed": rearmed_ok, "needed": len(real_snaps),
                               "last_error": (result.get("last_error") or "")[:160]},
                      suggested=["manually verify/submit a protective stop at the broker NOW",
                                 "or flatten the position manually"],
                      dedup_key=f"naked_{ticker}")
            except Exception:
                pass
        else:
            logger.error(
                "D247 SMART_EXIT_ESCALATE %s: protective stops re-armed via D249 from "
                "snapshot. OPERATOR: verify stop is at broker before next session.", ticker,
            )
    elif cancelled_snapshots:
        # doc 218: sentinel-only (a stop was cancelled UPSTREAM, e.g. D91 STEP 1, so we have
        # no snapshot to re-arm from here). The position may be naked — the upstream caller
        # owns re-arming, but emit a CRITICAL so it's never silent.
        logger.error(
            "D247 SMART_EXIT_ESCALATE %s: close FAILED; a blocking stop was cancelled "
            "upstream (no snapshot here to re-arm). Position may be NAKED — upstream/operator "
            "must verify a protective stop. OPERATOR INTERVENTION REQUIRED.", ticker,
        )
        try:
            from src.ops.incident_bus import emit_incident as _emit
            _emit("NAKED_POSITION_RISK", "CRITICAL", ticker=ticker,
                  context={"reason": "close failed; stop cancelled upstream, no re-arm snapshot",
                           "last_error": (result.get("last_error") or "")[:160]},
                  suggested=["verify/submit a protective stop at the broker for this ticker NOW"],
                  dedup_key=f"naked_{ticker}")
        except Exception:
            pass
    else:
        logger.error(
            "D247 SMART_EXIT_ESCALATE %s: protective stops MUST NOT be "
            "cancelled. OPERATOR INTERVENTION REQUIRED.",
            ticker,
        )
    return result


async def _close_overnight_position(
    *,
    client: Any,
    position_manager: Any,
    ticker: str,
    trade_journal: Any = None,
) -> bool:
    """
    Explicit, instrumented overnight-position close.

    Sequence:
      STEP 1: cancel stop_order_id (skip if empty; warn if cancel fails
              but proceed — the goal is to flatten the position)
      STEP 2: submit market sell via client.close_position(ticker)
      STEP 3: remove from internal tracker (position_manager.remove_position)
      STEP 4 (D295, 2026-05-13): record_close() in trade_journal so
              EOD reconciliation (D222 / D238) doesn't flag this exit as
              "journal=MISSING". Mirrors the fix made to BAR-1 (D146)
              after the original Bug #13 in 2026-04-08; same hole was
              re-introduced by D278's t1_next_open carry-overnight path
              because D91 close lives in bridge (not the post-fill
              handler). On 2026-05-13 broker showed +$41,083 across 70
              tickers all marked journal=MISSING -- that was 2 weeks of
              D91 closes silently bypassing the journal.

    The trade_journal kwarg is OPTIONAL for backwards compatibility
    with existing tests (TestD91CloseRoutineSteps + D279 tests pass
    only client + position_manager). Production callers in main.py
    pass the live TradeJournal singleton.

    Returns True if the sell was submitted (whether or not all steps
    succeeded), False if there was no position to close or the sell
    submission itself raised.

    Each step emits one INFO log line with a 'D91 STEP N' marker so
    operators can grep the log for the close trace per ticker.
    """
    if not position_manager.has_position(ticker):
        logger.info("D91: %s no longer in tracker — nothing to close", ticker)
        return False

    pos = position_manager.get_position(ticker)
    if pos is None:
        logger.info("D91: %s get_position returned None — nothing to close", ticker)
        return False

    stop_oid = getattr(pos, "stop_order_id", "") or ""
    qty = getattr(pos, "remaining_qty", 0) or 0
    # D295: capture entry_price BEFORE STEP 3 wipes the position.
    entry_px = float(getattr(pos, "entry_price", 0.0) or 0.0)

    # ── STEP 1: cancel stop ─────────────────────────────────────────
    if stop_oid:
        logger.info("D91 STEP 1 %s: cancel stop oid=%s", ticker, stop_oid)
        try:
            await client.cancel_order(stop_oid)
            # doc 209 BUGFIX (the APPS 3-session carry): the cancel is ASYNC at the broker.
            # On 5/29 STEP 2 fired ~1s later and 403'd with held_for_orders=970 because the
            # cancel had not SETTLED — and attempt_close's cancel-blocking-stops logic found
            # nothing left to cancel (STEP 1 already did) so it never applied its settle-wait
            # → 3 retries into the same unsettled 403 → close FAILED → overnight carry.
            # Fix: poll the broker until this position's qty_available frees up (the exact
            # condition STEP 2's market-sell needs), bounded ~3s. qty_available is Alpaca's
            # native field (the '403 available:0' we saw); get_positions passes it through.
            import asyncio as _aio91
            for _i in range(6):  # up to ~3s
                await _aio91.sleep(0.5)
                try:
                    _ps = await client.get_positions()
                    _p = next((x for x in (_ps or []) if x.get("symbol") == ticker), None)
                    if _p is None:
                        break  # position gone entirely → nothing to sell
                    _avail = abs(int(float(_p.get("qty_available", _p.get("qty", 0)) or 0)))
                    if _avail >= qty:
                        logger.info("D91 STEP 1 %s: cancel settled (qty_available=%d) after %.1fs",
                                    ticker, _avail, (_i + 1) * 0.5)
                        break
                except Exception:
                    break  # can't query → fall through; STEP 2's retry/rearm still backstops
        except Exception as e:
            # Stop may already be canceled / filled / expired.
            logger.warning(
                "D91 STEP 1 %s: cancel stop oid=%s failed (%s) — proceeding to market sell",
                ticker, stop_oid, e,
            )
    else:
        logger.info(
            "D91 STEP 1 %s: no stop_order_id (D56 no-broker-stop case) — skipping cancel",
            ticker,
        )

    # ── STEP 2: market sell ──────────────────────────────────────────
    # 2026-05-28 (doc 175): route through cancel-blocking-stops-then-close.
    # Tue's LFS overnight close hit 403 because the previous-session OTO
    # child stop reserved 2506 shares. Direct close_position cannot
    # succeed when held_for_orders == existing_qty. attempt_close_with_
    # status_check cancels the protective stop, retries the close, and
    # re-arms if close still fails — never leaves position naked.
    logger.info("D91 STEP 2 %s: submit market sell qty=%d", ticker, qty)
    try:
        close_result = await attempt_close_with_status_check(
            client=client,
            ticker=ticker,
            qty=qty,
            max_retries=3,
            cancel_blocking_stops_first=True,
        )
        if not close_result.get("succeeded"):
            logger.error(
                "D91 STEP 2 %s: close failed after %d retries with "
                "cancel-stops; last_error=%s — tracker NOT updated",
                ticker, close_result.get("attempts", 0),
                close_result.get("last_error"),
            )
            return False
        if close_result.get("attempts", 1) > 1:
            logger.info(
                "D91 STEP 2 %s: close succeeded on attempt %d/3 "
                "(cancel-stops path used)",
                ticker, close_result["attempts"],
            )
    except Exception as e:
        logger.error(
            "D91 STEP 2 %s: close_position raised: %s — internal tracker NOT updated",
            ticker, e,
        )
        return False

    # ── STEP 3: remove from internal tracker ────────────────────────
    logger.info("D91 STEP 3 %s: confirmed close, removing from internal tracker", ticker)
    try:
        position_manager.remove_position(ticker)
    except Exception as e:  # pragma: no cover — defensive
        logger.warning(
            "D91 STEP 3 %s: remove_position raised: %s",
            ticker, e,
        )

    # ── STEP 4 (D295): record close in journal ──────────────────────
    # Best-effort: never let a journaling error mask a successful close.
    # We use a snapshot for exit_price (market sell will fill near here);
    # D238 EOD recon will reconcile to broker truth at session end with
    # $1 tolerance. The point of this call is to make D238 STOP flagging
    # journal=MISSING -- the exact dollar accuracy is secondary.
    if trade_journal is not None:
        try:
            exit_px = entry_px  # safe fallback (zero pnl)
            try:
                _snaps = await client.get_snapshots([ticker])
                _last = (_snaps or {}).get(ticker, {}).get("last_price", 0)
                if _last:
                    exit_px = float(_last)
            except Exception:
                pass  # snapshot is advisory only
            realized_pnl = (exit_px - entry_px) * qty if entry_px > 0 else 0.0
            from datetime import datetime, timezone
            for _j_tid, _j_ent in list(trade_journal._entries.items()):
                if _j_ent.ticker == ticker and _j_ent.action == "BUY":
                    trade_journal.record_close(
                        _j_tid,
                        exit_price=exit_px,
                        realized_pnl=realized_pnl,
                        exit_time=datetime.now(timezone.utc),
                        exit_reason="NEXT_OPEN",
                    )
                    logger.info(
                        "D91 STEP 4 %s: journal record_close OK "
                        "(exit=$%.4f pnl=$%+.2f reason=NEXT_OPEN)",
                        ticker, exit_px, realized_pnl,
                    )
                    break
            else:
                logger.warning(
                    "D91 STEP 4 %s: no open BUY entry in journal — "
                    "EOD recon may still flag this as journal=MISSING",
                    ticker,
                )
        except Exception as _je:
            logger.warning(
                "D91 STEP 4 %s: record_close raised: %s "
                "(close already succeeded; journal is advisory)",
                ticker, _je,
            )

    return True


async def _assert_qty_matches_broker(
    *,
    client: Any,
    position: Any,
) -> None:
    """
    Compare internal position qty against broker-reported qty. On drift,
    emit a D218 QTY_DRIFT warning and auto-reconcile the internal tracker
    to broker truth.

    Non-fatal: if the broker call raises (API down, network blip), log
    a debug line and return without mutating. The next scheduled tick
    will retry.

    Args:
        client: Alpaca async client (must implement get_positions).
        position: ManagedPosition-like with .ticker, .qty, .remaining_qty.

    Mutation contract:
        - On drift: position.qty and position.remaining_qty are
          overwritten with broker_qty.
        - On no drift: no mutation.
        - On API failure: no mutation.
    """
    ticker = getattr(position, "ticker", "?")
    internal_qty = int(getattr(position, "qty", 0) or 0)

    try:
        broker_positions = await client.get_positions()
    except Exception as e:
        logger.debug(
            "D218: broker get_positions raised for %s qty-drift check: %s",
            ticker, e,
        )
        return

    broker_match = next(
        (p for p in (broker_positions or []) if p.get("symbol") == ticker),
        None,
    )
    if broker_match is None:
        logger.debug(
            "D218: %s not in broker open-positions list (already closed?) — skipping qty-drift check",
            ticker,
        )
        return

    try:
        broker_qty = int(float(broker_match.get("qty", 0) or 0))
    except (TypeError, ValueError):
        logger.debug(
            "D218: %s broker qty unparseable (%r) — skipping",
            ticker, broker_match.get("qty"),
        )
        return

    if broker_qty == internal_qty:
        return  # quiet success — common case

    # Drift detected. Log + reconcile.
    logger.warning(
        "D218 QTY_DRIFT %s: internal_qty=%d broker_qty=%d "
        "delta=%+d — reconciling internal tracker to broker truth. "
        "Likely cause: partial-fill captured by D217 poll before terminal fill.",
        ticker, internal_qty, broker_qty, broker_qty - internal_qty,
    )
    try:
        position.qty = broker_qty
        position.remaining_qty = broker_qty
    except Exception as e:  # pragma: no cover — defensive
        logger.error(
            "D218: %s failed to mutate position after drift detection: %s",
            ticker, e,
        )


class ExecutionBridge:
    """
    Stateless coordinator: TradeVerdict → Order → ManagedPosition.

    Node ID: execution.bridge
    Graph Link: docs/memory/graph_state.json → "execution.bridge"

    Invariants:
    1. Circuit breaker blocks before API call.
    2. ScoredCandidate cached before order for crash-safe Shapley.
    3. Failed orders → no position + cache cleanup.
    4. NO_TRADE verdicts short-circuit immediately.

    Ref: ADR-003 §1, ADR-014 (Pipeline Closure)
    """

    def __init__(
        self,
        executor: AlpacaExecutor,
        position_manager: PositionManager,
        alpaca_client: Any | None = None,
        settings: Settings | None = None,
        trade_tracker: TradeResultTracker | None = None,
        instrumentation: Any | None = None,
        bocpd_state: Any | None = None,
        kelly_governor: Any | None = None,
    ) -> None:
        self._executor = executor
        self._pm = position_manager
        # doc 286: register the live PositionManager so the module-level
        # partial-close helper can book broker-confirmed partial fills
        # onto ManagedPosition.qty (see _book_confirmed_partial_qty).
        _register_active_position_manager(position_manager)
        self._client = alpaca_client  # D101: For spread filter quote lookups
        self._settings = settings
        self._trade_tracker = trade_tracker  # D115: Kelly tier win rate tracking
        # Phase 0 instrumentation writer — optional; emits guarded so a
        # failure here NEVER crashes the trading hot path. Pass None to
        # disable.
        self._instrumentation = instrumentation
        # BOCPD changepoint detector — optional. observe(realized_pnl) is
        # called once per closed position from close_with_attribution(),
        # guarded so a BOCPD failure NEVER crashes trading. D223
        # BOCPD_BREAK is logged internally by observe() when the posterior
        # changepoint probability crosses the kill-switch threshold.
        self._bocpd_state = bocpd_state
        # Kelly governor — wires the Kelly-multiplier countdown after a
        # D223 BOCPD_BREAK fires. record_trade_completed() called once
        # per closed position alongside BOCPD observe.
        self._kelly_governor = kelly_governor
        # doc 229: defensive default — main.py:721 overrides with settings.ops.watchlist_webhook_url
        # for the live path, but non-main construction (tests, replay) must not AttributeError when
        # close_with_attribution reaches the post-close Discord block.
        self._watchlist_webhook_url = None

    async def execute_verdict(
        self,
        verdict: TradeVerdict,
        scored: ScoredCandidate | None = None,
        variant_map: dict[str, str] | None = None,
        entry_snapshot: dict | None = None,
    ) -> OrderResult | None:
        """
        Execute a TradeVerdict through the full entry pipeline.

        Pipeline:
            1. Guard: NO_TRADE / zero-size → skip
            2. Guard: circuit breaker / max positions → skip
            3. Cache ScoredCandidate (for Shapley on close)
            4. Submit to AlpacaExecutor
            5. Build ManagedPosition from OrderResult
            6. Register with PositionManager

        Args:
            verdict: Fully-vetted TradeVerdict from Orchestrator.
            scored: ScoredCandidate from MFCS computation (for Shapley).
            variant_map: agent_id → variant_id from Arena selection.

        Returns:
            OrderResult on success, None if skipped/failed.

        Ref: ADR-003 (Execution Layer)
        Ref: ADR-014 (Pipeline Closure)
        """
        ticker = verdict.ticker

        # ── Guard: non-actionable verdicts ──
        if verdict.action in ("NO_TRADE", "HOLD"):
            logger.debug("%s: Skipping (action=%s)", ticker, verdict.action)
            # doc 272 VLL: HOLD/NO_TRADE reached the bridge — informational terminal
            try:
                from src.ops.verdict_ledger import vll_emit
                vll_emit("SKIPPED_HOLD", ticker,
                         reason=f"action={verdict.action}", path="BRIDGE")
            except Exception:
                pass
            return None

        if verdict.position_size_pct <= 0:
            logger.debug("%s: Skipping (zero position size)", ticker)
            # doc 272 VLL: zero-size terminal
            try:
                from src.ops.verdict_ledger import vll_emit
                vll_emit("BLOCKED_ZERO_SIZE", ticker,
                         reason="position_size_pct <= 0", path="BRIDGE")
            except Exception:
                pass
            return None

        # ── D101: Guard: bid-ask spread filter ──
        # Reject entries where real-time spread > max_entry_spread_pct of price.
        # This is the single highest-impact filter — prevents entering illiquid
        # names with 3-5% spreads that destroy edge.
        # D125: Context-aware threshold — on high-conviction entries (MFCS > buy
        # threshold), use wider gap_day_spread_max. PTLE missed: 2.65% spread
        # blocked a +20% winner with confirmed catalyst. The spread cost is
        # acceptable when expected move >> spread.
        if self._client is not None and self._settings is not None:
            try:
                max_spread_pct = self._settings.execution.max_entry_spread_pct
                # D125: Widen spread tolerance for high-conviction entries
                _mfcs_threshold = getattr(
                    self._settings.scoring, "mfcs_buy_threshold", 0.15
                )
                if verdict.mfcs >= _mfcs_threshold:
                    max_spread_pct = self._settings.execution.gap_day_spread_max
                snapshot = await self._client.get_snapshots([ticker])
                snap_data = snapshot.get(ticker, {})
                bid = float(snap_data.get("bid", 0) or 0)
                ask = float(snap_data.get("ask", 0) or 0)
                if not snap_data or (bid == 0 and ask == 0):
                    logger.warning(
                        "D101 SPREAD CHECK: No bid/ask data for %s — snapshot may be stale. Proceeding with caution.",
                        ticker,
                    )
                if ask > bid > 0:
                    spread_pct = (ask - bid) / ask
                    if spread_pct >= max_spread_pct:
                        logger.warning(
                            "D101 SPREAD REJECT %s: spread=%.2f%% (bid=$%.2f, ask=$%.2f) >= max=%.1f%%",
                            ticker, spread_pct * 100, bid, ask, max_spread_pct * 100,
                        )
                        # doc 272 VLL: spread-filter terminal
                        try:
                            from src.ops.verdict_ledger import vll_emit
                            vll_emit("BLOCKED_SPREAD", ticker,
                                     reason=(f"spread={spread_pct * 100:.2f}%"
                                             f">={max_spread_pct * 100:.1f}%"),
                                     path="BRIDGE")
                        except Exception:
                            pass
                        return None
                    logger.debug(
                        "D101 SPREAD OK %s: spread=%.2f%% (bid=$%.2f, ask=$%.2f)",
                        ticker, spread_pct * 100, bid, ask,
                    )
            except Exception as e:
                logger.warning("D101: Spread check failed for %s: %s — proceeding", ticker, e)

        # ── Guard: position manager constraints (D150: tier-aware) ──
        _verdict_tier = 3
        if self._settings and getattr(self._settings.execution, "paper_aggressive_mode", False):
            _vf = getattr(verdict, "float_shares", None)
            _vg = abs(getattr(verdict, "gap_pct", 0))
            _vr = getattr(verdict, "rvol", 0)
            if (_vf is not None and _vf < self._settings.execution.tier1_float_max
                    and _vg >= self._settings.execution.tier1_gap_min
                    and _vr >= self._settings.execution.tier1_rvol_min):
                _verdict_tier = 1
            elif (_vf is not None and _vf < self._settings.execution.tier2_float_max
                    and _vg >= self._settings.execution.tier2_gap_min
                    and _vr >= self._settings.execution.tier2_rvol_min):
                _verdict_tier = 2
        if not self._pm.can_enter_new_position(tier=_verdict_tier):
            logger.warning(
                "%s: Blocked by PositionManager (circuit_breaker=%s, positions=%d, tier=%d)",
                ticker,
                self._pm.is_circuit_breaker_active,
                len(self._pm.open_positions),
                _verdict_tier,
            )
            # doc 272 VLL: position-manager recheck terminal (circuit/max-pos
            # backstop for ALL paths, incl. VWAP/RESCAN which lack own gates)
            try:
                from src.ops.verdict_ledger import vll_emit
                vll_emit("BLOCKED_PM_RECHECK", ticker,
                         reason=(f"circuit={self._pm.is_circuit_breaker_active} "
                                 f"positions={len(self._pm.open_positions)} "
                                 f"tier={_verdict_tier}"),
                         path="BRIDGE")
            except Exception:
                pass
            return None

        # ── Cache ScoredCandidate BEFORE order (crash-safe) ──
        if scored is not None:
            self._pm.cache_scored_candidate(ticker, scored)
            logger.debug(
                "%s: Cached ScoredCandidate (MFCS=%.3f) for Shapley",
                ticker,
                scored.mfcs,
            )

        # ── Submit order to Alpaca ──
        bridge_metrics = get_metrics()
        try:
            bridge_metrics.orders_submitted.inc()
            order_result = await self._executor.execute(verdict)
        except Exception as e:
            logger.error("%s: Executor failed: %s", ticker, e)
            # doc 272 VLL: executor-exception terminal
            try:
                from src.ops.verdict_ledger import vll_emit
                vll_emit("ERROR_EXECUTOR", ticker,
                         reason=str(e)[:120], path="BRIDGE")
            except Exception:
                pass
            # Clean up cache on failure
            self._pm._scored_cache.pop(ticker, None)
            return None

        if order_result is None:
            # Executor skipped (e.g., qty=0, max positions at broker level)
            self._pm._scored_cache.pop(ticker, None)
            return None

        # D216 / D217 / D218 — Verify fill before creating position.
        # Prevents ghost positions (e.g., CYCU 2026-04-08: OTO canceled
        # by Alpaca but position tracker thought it was filled, phantom P&L).
        #
        # Bug D fix (2026-04-22): the previous inline loop used
        # `filled_avg_price > 0` as the terminal-state signal. AGPU's
        # first poll at submit+2s caught a 505/846-share PARTIAL fill
        # (filled_avg_price=$9.59 — non-zero) and wrongly broke out.
        # Now we delegate to `_poll_for_terminal_fill` which polls until
        # status ∈ TERMINAL_ORDER_STATES.
        #
        # Always run the poll loop unless the executor has already
        # handed us a definitive terminal state. We treat
        # 'partially_filled' from the executor as NOT terminal here, so
        # bridge-level polling can drive it to completion.
        if order_result.status not in TERMINAL_SUCCESS_STATES:
            terminal = await _poll_for_terminal_fill(
                client=self._client,
                order_id=order_result.order_id,
                ticker=ticker,
                max_polls=6,
                poll_interval_s=2.0,
            )
            if terminal is None:
                logger.warning(
                    "D216: Order %s for %s not visible at broker after polls — "
                    "no position created",
                    (order_result.order_id or "?")[:8], ticker,
                )
                # doc 272 VLL: order never visible at broker — terminal
                try:
                    from src.ops.verdict_ledger import vll_emit
                    vll_emit("EXPIRED_NOT_VISIBLE", ticker,
                             reason=f"order {(order_result.order_id or '?')[:12]} not visible after polls",
                             path="BRIDGE")
                except Exception:
                    pass
                # Bug V fix (2026-04-24): cancel at broker before bridge
                # gives up tracking. The order may still be live at the
                # broker (just not returned by the get_orders page); a
                # later fill would create a ghost position.
                await _cancel_order_or_warn(
                    self._client, order_result.order_id, ticker,
                )
                self._pm._scored_cache.pop(ticker, None)
                return None

            terminal_status = terminal.get("status", "unknown")
            if terminal_status in ("canceled", "expired", "rejected"):
                logger.warning(
                    "D216: Order %s for %s was %s — no position created",
                    (order_result.order_id or "?")[:8], ticker, terminal_status,
                )
                # doc 272 VLL: broker reached terminal-failed state — terminal
                try:
                    from src.ops.verdict_ledger import vll_emit
                    vll_emit("REJECTED_BROKER", ticker,
                             reason=f"order {(order_result.order_id or '?')[:12]} {terminal_status}",
                             path="BRIDGE")
                except Exception:
                    pass
                self._pm._scored_cache.pop(ticker, None)
                return None

            terminal_fill_price = float(terminal.get("filled_avg_price") or 0)
            terminal_filled_qty = int(float(terminal.get("filled_qty") or 0))

            if terminal_status not in TERMINAL_SUCCESS_STATES:
                # Still partially_filled after max polls. Accept the
                # broker truth (whatever shares actually filled) so the
                # subsequent T+5/T+30/T+60s D218 checks can reconcile if
                # the broker eventually completes; reject only if zero.
                if terminal_filled_qty <= 0 or terminal_fill_price <= 0:
                    logger.warning(
                        "D217: Order %s for %s still status=%s filled_qty=%d "
                        "after polls — rejecting to prevent ghost position",
                        (order_result.order_id or "?")[:8], ticker,
                        terminal_status, terminal_filled_qty,
                    )
                    # doc 272 VLL: non-terminal with zero fill — terminal (rejected)
                    try:
                        from src.ops.verdict_ledger import vll_emit
                        vll_emit("REJECTED_PARTIAL_ZERO", ticker,
                                 reason=(f"order {(order_result.order_id or '?')[:12]} "
                                         f"status={terminal_status} filled_qty={terminal_filled_qty}"),
                                 path="BRIDGE")
                    except Exception:
                        pass
                    # Bug V fix (2026-04-24): the OTO with time_in_force="day"
                    # remains LIVE at the broker after we give up tracking.
                    # Without this cancel it can fill later in the session
                    # and create an untracked ghost position with active
                    # OTO stop leg. Yesterday's $978.50 silent loss was
                    # exactly this failure mode firing 4 times.
                    await _cancel_order_or_warn(
                        self._client, order_result.order_id, ticker,
                    )
                    self._pm._scored_cache.pop(ticker, None)
                    return None
                logger.warning(
                    "D217: Order %s for %s accepting partial fill "
                    "(status=%s filled_qty=%d @ $%.2f) — D218 will reconcile",
                    (order_result.order_id or "?")[:8], ticker,
                    terminal_status, terminal_filled_qty, terminal_fill_price,
                )

            if terminal_fill_price > 0:
                order_result.fill_price = terminal_fill_price
            if terminal_filled_qty > 0:
                order_result.qty = terminal_filled_qty

            # doc 268 VLL: SUBMITTED terminal — the verdict reached the broker.
            try:
                from src.ops.verdict_ledger import vll_emit
                vll_emit("SUBMITTED", ticker,
                         reason=str(order_result.order_id or "")[:12],
                         fill_price=float(terminal_fill_price or 0),
                         qty=int(terminal_filled_qty or 0))
            except Exception:
                pass

            # Phase 0 — emit terminal TradeContextRow + one ChildFillRow per leg.
            # Wrapped to never crash the trading hot path.
            if self._instrumentation is not None:
                try:
                    from datetime import datetime, timezone
                    _now = datetime.now(timezone.utc)
                    self._instrumentation.emit_trade_context_dict({
                        "order_id": order_result.order_id or "unknown",
                        "ticker": ticker,
                        "side": getattr(verdict, "direction", "long") == "short" and "sell" or "buy",
                        "requested_qty": int(order_result.qty or 0) or 1,
                        "requested_px": float(verdict.entry_price or 0),
                        "submit_ts": _now,  # Bridge doesn't have the original submit_ts; use now
                        "terminal_ts": _now,
                        "terminal_status": str(terminal_status) if terminal_status in
                            ("filled", "partially_filled", "canceled", "rejected", "expired") else "pending",
                        "terminal_filled_qty": int(terminal_filled_qty),
                    })
                    # ChildFillRow per leg from the terminal broker response
                    _legs = terminal.get("legs") or []
                    _cumulative = 0
                    for _leg in _legs:
                        try:
                            _q = int(float(_leg.get("filled_qty", 0) or 0))
                            _p = float(_leg.get("filled_avg_price", 0) or 0)
                            if _q <= 0 or _p <= 0:
                                continue
                            _cumulative += _q
                            self._instrumentation.emit_child_fill_dict({
                                "parent_order_id": order_result.order_id or "unknown",
                                "child_fill_ts": _now,
                                "qty": _q,
                                "price": _p,
                                "cumulative_filled_qty": _cumulative,
                            })
                        except (TypeError, ValueError) as _e:
                            logger.debug("Phase 0 emit_child_fill (leg) failed: %s", _e)
                except Exception as _e:
                    logger.debug("Phase 0 emit terminal TradeContext / ChildFill failed: %s", _e)
        else:
            # doc 272 VLL: SUBMITTED terminal for the INSTANT-FILL path. The
            # executor returned an already-terminal status (e.g. "filled"), so
            # the poll block above — which carries the doc-268 SUBMITTED emit —
            # is skipped entirely. Without this branch every instant fill was
            # an orphan (journal BUY with no trace terminal).
            try:
                from src.ops.verdict_ledger import vll_emit
                vll_emit("SUBMITTED", ticker,
                         reason=str(order_result.order_id or "")[:12],
                         fill_price=float(order_result.fill_price or 0),
                         qty=int(order_result.qty or 0))
            except Exception:
                pass

        # ── Build ManagedPosition from OrderResult ──
        # D78 FIX: Capture entry_spread from snapshot at entry time for
        # spread widening detection.  Without this, the spread_widening
        # signal in ExitIntelligenceManager has no baseline to compare.
        _entry_spread = 0.0
        _entry_volume = 0
        if entry_snapshot:
            _bid = float(entry_snapshot.get("bid", 0) or
                         entry_snapshot.get("latestQuote", {}).get("bp", 0) or 0)
            _ask = float(entry_snapshot.get("ask", 0) or
                         entry_snapshot.get("latestQuote", {}).get("ap", 0) or 0)
            if _ask > _bid > 0:
                _entry_spread = _ask - _bid
            _entry_volume = int(entry_snapshot.get("volume", 0) or
                                entry_snapshot.get("dailyBar", {}).get("v", 0) or 0)

        # D110: Extract catalyst/sector/gap info from scored candidate or verdict
        _catalyst_type = "unknown"
        _sector = ""
        _gap_pct = 0.0
        _manipulation_phase = "UNCERTAIN"
        # Bug AF fix (2026-04-26): _cand was previously assigned only inside
        # the `if scored is not None:` block, but referenced unconditionally
        # at lines 929-931 below (rvol/prior_gap_count/is_day2_runner kwargs
        # to ManagedPosition). When scored=None — a legal production state
        # for verdicts that bypass the Shapley scoring path — this raised
        # UnboundLocalError, killing the entry pipeline. Surfaced by the
        # production-shape integration test in
        # tests/integration/test_phase0_production_lifecycle.py.
        _cand = None
        if scored is not None:
            # D121 BUG-O2: These fields are on scored.candidate (CandidateStock),
            # not on ScoredCandidate itself. getattr always fell to defaults.
            _cand = getattr(scored, "candidate", None)
            if _cand is not None:
                _catalyst_type = getattr(_cand, "catalyst_type", "unknown") or "unknown"
                _gap_pct = getattr(_cand, "gap_pct", 0.0) or 0.0
            # Manipulation phase from ManipulationSignal in agent_signals
            for _sig in getattr(scored, "agent_signals", []):
                if hasattr(_sig, "phase") and _sig.phase and _sig.phase != "UNCERTAIN":
                    _manipulation_phase = _sig.phase
                    break
        # D121: Also check verdict.catalyst_profile for catalyst_type
        _cat_prof = getattr(verdict, "catalyst_profile", None)
        if _cat_prof is not None and getattr(_cat_prof, "catalyst_type", "unknown") != "unknown":
            _catalyst_type = _cat_prof.catalyst_type
        # Sector from portfolio_risk module
        try:
            from src.execution.portfolio_risk import get_sector
            _sector = get_sector(ticker)
        except Exception:
            _sector = ""

        # D118: Extract catalyst profile params for position lifecycle
        _cat_half_life = 20
        _cat_grat_decay = 0.05
        _cat_profile = getattr(verdict, "catalyst_profile", None)
        if _cat_profile is not None:
            _cat_half_life = getattr(_cat_profile, "recommended_half_life_minutes", 20)
            _cat_grat_decay = getattr(_cat_profile, "recommended_gratitude_decay", 0.05)

        # D121 BUG-M1: Use actual fill price from OrderResult, not verdict's
        # pre-fill estimate. verdict.entry_price is the price at evaluation time;
        # order_result.fill_price (or signal_price) is the actual execution price.
        _actual_entry = order_result.fill_price if order_result.fill_price > 0 else order_result.submitted_price
        if _actual_entry <= 0:
            _actual_entry = verdict.entry_price
        # Bug R fix (2026-04-23): when the executor exposes the actual
        # post-Phase-0/1-tightened stop the broker received, use that
        # — not the un-tightened verdict.stop_loss. Today's XNDU trade
        # had verdict.stop_loss=$25.72 but broker stop=$32.44; the
        # tracker carrying $25.72 made all downstream risk math wrong
        # by ~14×. Falls back to verdict.stop_loss for backward compat
        # when actual_stop_price is None.
        _broker_stop = (
            float(order_result.actual_stop_price)
            if getattr(order_result, "actual_stop_price", None) is not None
            else verdict.stop_loss
        )
        position = ManagedPosition(
            ticker=ticker,
            qty=order_result.qty,
            entry_price=_actual_entry,
            signal_price=order_result.signal_price,
            stop_loss=_broker_stop,  # Bug R: broker truth, not verdict original
            target_prices=verdict.target_prices,
            order_id=order_result.order_id,
            entry_spread=_entry_spread,
            entry_volume=_entry_volume,
            peak_volume=_entry_volume,
            peak_price=_actual_entry,
            catalyst_type=_catalyst_type,
            sector=_sector,
            gap_pct=_gap_pct,
            manipulation_phase=_manipulation_phase,
            kelly_tier=getattr(verdict, "kelly_tier", 1),
            catalyst_half_life_minutes=_cat_half_life,
            catalyst_gratitude_decay=_cat_grat_decay,
            fill_price=_actual_entry,  # D150: Set fill_price for slippage_bps property
            stop_order_id=order_result.stop_order_id,  # D121 BUG-1: Transfer stop OID for StopResubmitter
            position_tier=_verdict_tier,  # D150: Tier for max_concurrent enforcement
            direction=getattr(verdict, "direction", "long"),  # D161: Track long vs short for P&L
            # D214: Archetype exit pre-entry features
            rvol=getattr(_cand, "rvol", 1.0) if _cand else 1.0,
            prior_gap_count=getattr(_cand, "prior_gap_count", 0) or 0 if _cand else 0,
            is_day2_runner=getattr(_cand, "is_day2_runner", False) if _cand else False,
        )

        # doc 228: add_position returns the CANONICAL tracked object (the merged `existing` on
        # a re-buy, else `position`). Reconcile/monitor against THAT — not the discarded
        # single-fill `position` — so the qty-drift check (D218) verifies the real merged
        # tracker against broker truth (otherwise the merge's safety net no-ops on a re-buy).
        tracked_position = self._pm.add_position(position) or position
        bridge_metrics.orders_filled.inc()
        bridge_metrics.open_positions.set(len(self._pm.open_positions))

        # Phase 0 — emit BarContextRow capturing the entry-bar context.
        # OHLCV fields fall back to the entry_snapshot if a true 1-min
        # bar isn't available; bar_data_quality flags the difference.
        if self._instrumentation is not None:
            try:
                from datetime import datetime, timezone
                _now = datetime.now(timezone.utc)
                _bar_open = float(_actual_entry or 0) or 1.0
                _bar_high = _bar_open
                _bar_low = _bar_open
                _bar_close = _bar_open
                _bar_quality = "missing"
                if entry_snapshot:
                    _daily = entry_snapshot.get("dailyBar") or {}
                    if _daily:
                        _bar_open = float(_daily.get("o") or _bar_open) or _bar_open
                        _bar_high = float(_daily.get("h") or _bar_open) or _bar_open
                        _bar_low = float(_daily.get("l") or _bar_open) or _bar_open
                        _bar_close = float(_daily.get("c") or _bar_open) or _bar_open
                        _bar_quality = "partial"  # daily not 1-min — partial signal
                _our_q = int(order_result.qty or 0)
                _bar_vol = max(int(_entry_volume or 0), _our_q)  # ensure denom >= our_q
                _qv = (_our_q / _bar_vol) if _bar_vol > 0 else 0.0
                self._instrumentation.emit_bar_context_dict({
                    "position_id": order_result.order_id or "unknown",
                    "ticker": ticker,
                    "entry_ts": _now,
                    "entry_bar_open_ts": _now,
                    "entry_bar_open": _bar_open,
                    "entry_bar_high": _bar_high,
                    "entry_bar_low": _bar_low,
                    "entry_bar_close": _bar_close,
                    "entry_bar_volume": _bar_vol,
                    "our_q_shares": _our_q,
                    "q_over_v_tau": _qv,
                    "bar_data_quality": _bar_quality,
                })
            except Exception as _e:
                logger.debug("Phase 0 emit_bar_context failed: %s", _e)

            # Phase 0 — emit CohortRow per matched peer (cohort registry v0.1).
            # Wrapped — never crashes the trading hot path. The candidate pool
            # is best-effort: if the scanner exposes a recent-candidates cache
            # we use it; otherwise we skip cohort emission for this trade.
            try:
                from src.analysis.cohort_matcher import (
                    PeerCandidate, build_cohort_rows, find_cohort_peers,
                )
                from datetime import datetime as _dt2, timezone as _tz2
                _cohort_pool: list[PeerCandidate] = []
                # The position_manager exposes _scored_cache (per-ticker cached
                # scored candidates from recent scans). Use its keys + cached
                # data to build PeerCandidate entries best-effort.
                _scored_cache = getattr(self._pm, "_scored_cache", {}) or {}
                for _pt, _ps in _scored_cache.items():
                    if _pt == ticker:
                        continue
                    _pc_cand = getattr(_ps, "candidate", None)
                    if _pc_cand is None:
                        continue
                    _pc_cap = getattr(_pc_cand, "market_cap", None)
                    _pc_cat = getattr(_pc_cand, "catalyst_type", None) or "unknown"
                    _pc_px = float(getattr(_pc_cand, "current_price", 0) or 0)
                    if _pc_px <= 0:
                        continue
                    _cohort_pool.append(PeerCandidate(
                        ticker=_pt, catalyst_type=_pc_cat,
                        market_cap=float(_pc_cap) if _pc_cap else None,
                        entry_ts=_dt2.now(_tz2.utc),  # v0.1: now() for all peers
                        reference_price=_pc_px,
                    ))
                _peers = find_cohort_peers(
                    traded_ticker=ticker, traded_catalyst_type=_catalyst_type,
                    traded_market_cap=float(getattr(_cand, "market_cap", 0) or 0)
                    if _cand else None,
                    traded_entry_ts=_now,
                    candidate_pool=_cohort_pool,
                )
                if _peers:
                    _ccap_for_features = (
                        float(getattr(_cand, "market_cap", 0) or 0) if _cand else None
                    )
                    _rows = build_cohort_rows(
                        traded_ticker=ticker, traded_entry_ts=_now,
                        peers=_peers, catalyst_type=_catalyst_type,
                        market_cap=_ccap_for_features,
                    )
                    for _crow in _rows:
                        self._instrumentation.emit_cohort_registry_dict(_crow)
                    logger.info(
                        "Phase 0 cohort emit: %s matched %d peer(s) (catalyst=%s)",
                        ticker, len(_rows), _catalyst_type,
                    )
            except Exception as _ce:
                logger.debug("Phase 0 emit_cohort_registry failed: %s", _ce)

        # D218 QTY_DRIFT post-entry assertions (Bug D follow-up).
        # Schedule three non-blocking broker-truth checks at T+5s, T+30s,
        # T+60s to catch late terminal fills that arrived after our poll
        # loop's 12s budget. Each tick compares internal qty vs broker qty;
        # drift auto-reconciles with a D218 warning log.
        if self._client is not None:
            asyncio.create_task(
                self._schedule_qty_drift_checks(tracked_position),  # doc 228: the merged object
                name=f"d218-qty-drift-{ticker}",
            )

        # Track fill slippage in basis points
        # D121 BUG-O3: Previously compared submitted_price vs verdict.entry_price
        # which are always identical → slippage always 0. Use fill_price when available.
        _slip_price = order_result.fill_price if order_result.fill_price > 0 else order_result.submitted_price
        if verdict.entry_price > 0 and _slip_price > 0:
            slippage_bps = abs(_slip_price - verdict.entry_price) / verdict.entry_price * 10000
            bridge_metrics.fill_slippage_bps.observe(slippage_bps)

        logger.info(
            "%s: Position opened — qty=%d, entry=$%.2f, stop=$%.2f, "
            "targets=%s, order_id=%s",
            ticker,
            position.qty,
            position.entry_price,
            position.stop_loss,
            [f"${t:.2f}" for t in position.target_prices],
            position.order_id,
        )

        return order_result

    async def _schedule_qty_drift_checks(
        self,
        position: ManagedPosition,
        delays_s: tuple[float, ...] = (5.0, 30.0, 60.0),
    ) -> None:
        """
        Background task: run D218 qty-drift checks at scheduled offsets
        after position open. Each tick is best-effort; failures are
        swallowed (logged at debug). The position may have already
        closed by T+60s — `_assert_qty_matches_broker` handles missing
        broker positions gracefully.
        """
        ticker = getattr(position, "ticker", "?")
        for delay in delays_s:
            try:
                await asyncio.sleep(delay)
                # If the position has already been closed and removed
                # from the manager, skip — drift no longer matters.
                if ticker not in self._pm._positions:
                    logger.debug(
                        "D218: %s position already closed, skipping T+%.0fs check",
                        ticker, delay,
                    )
                    return
                await _assert_qty_matches_broker(
                    client=self._client, position=position,
                )
            except asyncio.CancelledError:
                raise
            except Exception as e:  # pragma: no cover — defensive
                logger.debug(
                    "D218: %s T+%.0fs check failed: %s", ticker, delay, e,
                )

    async def close_with_attribution(
        self,
        ticker: str,
        exit_price: float,
        exit_time: datetime | None = None,
        agent_signals_map: dict[str, str] | None = None,
        variant_map: dict[str, str] | None = None,
        exit_reason: str = "unknown",
    ) -> Any | None:
        """
        Close a position and produce EnrichedTradeResult for Shapley attribution.

        doc 229: `exit_reason` was referenced inside the post-close Discord + decision-learning
        blocks (lines ~1684/1711) but was NEVER a parameter -> NameError on EVERY close, swallowed
        by the surrounding bare `except` -> close-Discord alerts and decision-learning enrichment
        have been silently DEAD since D218. P&L booking (above those blocks) was unaffected. Adding
        the param restores both. Callers that don't pass it get "unknown".

        Delegates to PositionManager.close_position_with_attribution().

        Args:
            ticker: Stock symbol.
            exit_price: Fill price at exit.
            exit_time: When position was closed.
            agent_signals_map: agent_id → signal direction at entry.
            variant_map: agent_id → variant_id from Arena.

        Returns:
            EnrichedTradeResult if Shapley data available, None otherwise.

        Ref: ADR-014 (Pipeline Closure)
        Ref: MOMENTUM_LOGIC.md §17 (Shapley Attribution)
        """
        if exit_time is None:
            exit_time = datetime.now(timezone.utc)

        # Sweep fix: Capture position data BEFORE close removes it.
        # Need remaining_qty for total PnL (enriched.pnl is per-share only).
        # D121 BUG-C1: Use remaining_qty, not qty. Tranche fills already
        # recorded PnL for sold shares — only remaining shares are being closed here.
        _pos = self._pm._positions.get(ticker)
        # D121 BUG-L5: If position already removed (e.g., tranche_monitor fully
        # closed it), bail early. Otherwise _pos_qty defaults to 1 and we'd
        # record phantom PnL from per-share value as if 1 share was closed.
        if _pos is None:
            logger.info(
                "%s: Position already closed (likely by tranche_monitor) — skipping close_with_attribution",
                ticker,
            )
            # Still pop scored cache to avoid stale entries
            self._pm._scored_cache.pop(ticker, None)
            return None
        _kelly_tier = getattr(_pos, "kelly_tier", 1)
        _pos_qty = _pos.remaining_qty

        enriched = self._pm.close_position_with_attribution(
            ticker=ticker,
            exit_price=exit_price,
            exit_time=exit_time,
            agent_signals_map=agent_signals_map or {},
            variant_map=variant_map or {},
        )

        if enriched is not None:
            # Sweep fix: enriched.pnl is per-share (exit - entry). Multiply by
            # qty for total PnL. Previous code recorded per-share into daily_pnl
            # gauge — a 100-share $0.50/share gain recorded $0.50 instead of $50.
            _total_pnl = enriched.pnl * _pos_qty
            close_metrics = get_metrics()
            close_metrics.session_trades.inc()
            close_metrics.open_positions.set(len(self._pm.open_positions))
            close_metrics.daily_pnl.inc(_total_pnl)
            # Sweep fix: track wins/losses for session report
            if _total_pnl > 0:
                close_metrics.win_count.inc()
            else:
                close_metrics.loss_count.inc()
            logger.info(
                "%s: Closed with attribution — PnL=$%.2f (%d shares), MFCS=%.3f",
                ticker,
                _total_pnl,
                _pos_qty,
                enriched.mfcs_at_entry,
            )
            # D218: Post trade close to Discord
            try:
                from src.monitoring.alerts import post_trade_close
                import asyncio as _close_aio
                _hold_min = (
                    (exit_time - _pos.opened_at).total_seconds() / 60
                    if _pos and hasattr(_pos, "opened_at") and _pos.opened_at else None
                )
                _close_aio.ensure_future(post_trade_close(
                    ticker=ticker, qty=_pos_qty,
                    entry_price=_pos.entry_price if _pos else 0,
                    exit_price=exit_price, pnl=_total_pnl,
                    exit_reason=exit_reason or "unknown",
                    hold_minutes=_hold_min,
                    webhook_url=self._watchlist_webhook_url,
                ))
            except Exception:
                pass
            # D221 Phase F: enriched decision-learning channel.
            # Adds Shapley attribution, calibration check, debate/risk
            # flags, and Paper-1 JSON footer. Parallel to legacy alert.
            try:
                from src.monitoring.decision_learning import (
                    post_trade_close_enriched, get_distribution_cache,
                )
                # Compute Shapley attribution if attributor available
                _shapley_dict = None
                _component_scores = getattr(enriched, "agent_component_scores", None)
                try:
                    _attributor = getattr(self, "_shapley_attributor", None)
                    if _attributor is not None and _component_scores:
                        _attribution = _attributor.compute_attributions(enriched)
                        _shapley_dict = dict(_attribution.agent_shapley_values)
                except Exception as _shap_e:
                    logger.debug("Shapley compute failed for enriched message: %s", _shap_e)
                _close_aio.ensure_future(post_trade_close_enriched(
                    ticker=ticker, qty=_pos_qty,
                    entry_price=_pos.entry_price if _pos else 0,
                    exit_price=exit_price, pnl=_total_pnl,
                    exit_reason=exit_reason or "unknown",
                    hold_minutes=_hold_min,
                    mfcs_at_entry=getattr(enriched, "mfcs_at_entry", None),
                    agent_component_scores=_component_scores,
                    shapley_attribution=_shapley_dict,
                    debate_triggered=getattr(enriched, "debate_triggered", False),
                    risk_score=getattr(enriched, "risk_score", None),
                    catalyst_type=getattr(_pos, "catalyst_type", None),
                    distribution_cache=get_distribution_cache(),
                ))
            except Exception as _dl_e:
                logger.debug("decision_learning trade_close enrich failed: %s", _dl_e)
            # D115: Record trade result for Kelly tier win rate tracking
            if self._trade_tracker is not None:
                from datetime import date as _date

                self._trade_tracker.record(TradeResult(
                    ticker=ticker,
                    catalyst_type=getattr(enriched, "catalyst_type", "unknown"),
                    entry_time=getattr(enriched, "entry_time", exit_time).isoformat()
                    if hasattr(enriched, "entry_time") else exit_time.isoformat(),
                    exit_time=exit_time.isoformat(),
                    pnl=_total_pnl,
                    is_win=enriched.pnl > 0,
                    kelly_tier=_kelly_tier,
                    session_date=_date.today().isoformat(),
                    source="shapley",
                ))
            # BOCPD observe — guarded, NEVER crashes trading. D223
            # BOCPD_BREAK is logged internally by observe() when posterior
            # changepoint > kill-switch threshold; D224 KELLY_HALVED fires
            # transitively via the wired KellyGovernor (if any).
            if self._bocpd_state is not None:
                try:
                    self._bocpd_state.observe(float(_total_pnl))
                except Exception as _bocpd_e:
                    logger.debug("BOCPD observe (shapley path) failed: %s", _bocpd_e)
            # Kelly governor: decrement remaining_trades after each closed trade
            if self._kelly_governor is not None:
                try:
                    self._kelly_governor.record_trade_completed()
                except Exception as _kge:
                    logger.debug("Kelly governor record_trade_completed (shapley): %s", _kge)
        else:
            # D217 FIX: Record trade result EVEN when Shapley attribution fails
            # (enriched=None because ScoredCandidate wasn't cached). The Kelly
            # tier system needs win/loss data regardless of attribution quality.
            # Without this, trade_results.jsonl is never written and Kelly tiers
            # are decorative (every trade gets Tier 1).
            _total_pnl = (exit_price - _pos.entry_price) * _pos_qty if _pos else 0.0
            logger.warning(
                "%s: Closed WITHOUT attribution (no cached ScoredCandidate) — "
                "PnL=$%.2f (%d shares). Shapley unavailable but Kelly result recorded.",
                ticker, _total_pnl, _pos_qty,
            )
            try:
                close_metrics = get_metrics()
                close_metrics.session_trades.inc()
                close_metrics.open_positions.set(len(self._pm.open_positions))
                close_metrics.daily_pnl.inc(_total_pnl)
                if _total_pnl > 0:
                    close_metrics.win_count.inc()
                else:
                    close_metrics.loss_count.inc()
            except Exception:
                pass
            if self._trade_tracker is not None:
                from datetime import date as _date
                try:
                    self._trade_tracker.record(TradeResult(
                        ticker=ticker,
                        catalyst_type=getattr(_pos, "catalyst_type", "unknown"),
                        entry_time=getattr(_pos, "opened_at", exit_time).isoformat()
                        if hasattr(_pos, "opened_at") and _pos.opened_at else exit_time.isoformat(),
                        exit_time=exit_time.isoformat(),
                        pnl=_total_pnl,
                        is_win=_total_pnl > 0,
                        kelly_tier=_kelly_tier,
                        session_date=_date.today().isoformat(),
                        source="basic",
                    ))
                except Exception as _tr_e:
                    logger.warning("D217: Trade result recording failed: %s", _tr_e)
            # BOCPD observe — basic-attribution path. Same discipline as
            # shapley path: guarded, never crashes trading.
            if self._bocpd_state is not None:
                try:
                    self._bocpd_state.observe(float(_total_pnl))
                except Exception as _bocpd_e:
                    logger.debug("BOCPD observe (basic path) failed: %s", _bocpd_e)
            # Kelly governor: decrement remaining_trades after each closed trade
            if self._kelly_governor is not None:
                try:
                    self._kelly_governor.record_trade_completed()
                except Exception as _kge:
                    logger.debug("Kelly governor record_trade_completed (basic): %s", _kge)

        return enriched

    @property
    def position_manager(self) -> PositionManager:
        """Access underlying PositionManager."""
        return self._pm

    @property
    def executor(self) -> AlpacaExecutor:
        """Access underlying AlpacaExecutor."""
        return self._executor
