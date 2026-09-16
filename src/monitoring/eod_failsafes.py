"""
EOD failsafe layer — items 15, 28 of 2026-04-24 next-actions list +
D242 force-close motivated by today's LIDR Bug Z catastrophe.

Three failsafes that fire in sequence at the END of the trading day:

  D241 EOD_SAFETY_CANCEL  (15:58 ET) — cancel any working orders not
                                       on the expected-allowlist
  D242 EOD_FORCE_CLOSE    (15:55 ET) — broker has positions internal
                                       tracker doesn't know about;
                                       force market sell to flatten
  D238 EOD_RECONCILIATION (16:00 ET) — broker truth vs internal journal;
                                       per-source breakdown on disagreement

Each failsafe is independent — failure of one does NOT prevent the
others. Each emits its own labeled D-code on action OR explicit
"clean" log on no-action.

Per the discipline: failsafes are LAST resort. The bridge close-loop,
the EOD recon (Track A item 3), and the recon daemon (Track B) should
all catch issues earlier. These exist for the case where ALL of those
layers fail (today's Bug Z + LIDR cascade is exactly that case).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(float(v)) if v not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _fmt_exc(e: BaseException) -> str:
    """D298 (2026-05-19): format an exception for logging so the type
    is always visible even when the message is empty.

    On 2026-05-15 the D238 recon emitted ``get_orders raised () — recon
    skipped`` — empty parens, no diagnostic clue. The cause was an
    httpx timeout exception with no ``args``, where ``f"({e})"``
    collapsed to ``"()"``. This helper always prepends the type name
    so the log is actionable even when ``str(e) == ""``.

    Examples:
        _fmt_exc(httpx.ReadTimeout())            -> "ReadTimeout"
        _fmt_exc(ValueError("foo"))              -> "ValueError: foo"
        _fmt_exc(httpx.HTTPStatusError("502 Bad Gateway", request=None,
                                         response=None))
                                                 -> "HTTPStatusError: 502 Bad Gateway"
    """
    msg = str(e).strip()
    name = type(e).__name__
    return f"{name}: {msg}" if msg else name


# ── D241 EOD_SAFETY_CANCEL ─────────────────────────────────────────


async def run_eod_safety_cancel(
    *,
    client: Any,
    expected_order_ids: set[str] | None = None,
) -> dict[str, Any]:
    """
    Cancel any working broker orders not on the expected_order_ids
    allowlist. The allowlist is the set of order IDs the internal
    tracker knows about (from PositionManager + tranche_monitor).

    Behaviour:
      - If broker has working orders NOT in the allowlist → cancel each
        with `D241 EOD_SAFETY_CANCEL` per-order INFO log
      - If a cancel fails → D241 WARN, proceed to next order
      - If no orphan orders → "D241: clean" INFO log, no action

    Returns: {
      "checked": int,         # total working orders found at broker
      "orphans_found": int,   # orders not in allowlist
      "cancels_succeeded": int,
      "cancels_failed": int,
      "broker_reachable": bool,
    }
    """
    result = {
        "checked": 0,
        "orphans_found": 0,
        "cancels_succeeded": 0,
        "cancels_failed": 0,
        "broker_reachable": True,
    }
    expected_order_ids = expected_order_ids or set()

    try:
        orders = await client.get_orders(status="open", limit=200)
    except Exception as e:
        # D298 (2026-05-19): use _fmt_exc so empty-message exceptions
        # (e.g. httpx.ReadTimeout with no args) still print their type.
        logger.warning(
            "D241 EOD_SAFETY_CANCEL DEGRADED: get_orders raised (%s) — "
            "skipping safety cancel",
            _fmt_exc(e),
        )
        result["broker_reachable"] = False
        return result

    result["checked"] = len(orders or [])
    for o in orders or []:
        oid = o.get("id") or ""
        if not oid:
            continue
        if oid in expected_order_ids:
            continue
        # Orphan order
        result["orphans_found"] += 1
        try:
            await client.cancel_order(oid)
            logger.info(
                "D241 EOD_SAFETY_CANCEL %s: cancelled orphan order "
                "%s (side=%s qty=%s status=%s)",
                o.get("symbol", "?"), oid[:8],
                o.get("side"), o.get("qty"), o.get("status"),
            )
            result["cancels_succeeded"] += 1
        except Exception as e:
            # D298: include exception type so empty messages don't lose info
            logger.warning(
                "D241 EOD_SAFETY_CANCEL %s: cancel of orphan %s FAILED (%s)",
                o.get("symbol", "?"), oid[:8], _fmt_exc(e),
            )
            result["cancels_failed"] += 1

    if result["orphans_found"] == 0:
        logger.info(
            "D241 EOD_SAFETY_CANCEL: clean — %d working orders all on "
            "expected allowlist", result["checked"],
        )
    return result


# ── D242 EOD_FORCE_CLOSE ───────────────────────────────────────────


async def run_eod_force_close(
    *,
    client: Any,
    position_manager: Any,
) -> dict[str, Any]:
    """
    Force-close any broker positions the internal tracker does not
    know about. Motivated by today's LIDR Bug Z: broker had qty=5264
    after a fake-close, internal tracker showed empty, regular EOD
    close logic skipped (had nothing to iterate over).

    Behaviour:
      - For each broker position not in tracker → submit market close
        via `client.close_position(symbol)` with `D242 EOD_FORCE_CLOSE`
        per-symbol WARN log (this is a last-resort intervention)
      - If close fails → D242 ERROR, position remains open (operator
        intervention required)
      - If broker positions are all internally tracked → "D242: clean"
        INFO log, no action

    Returns: {
      "broker_positions": int,
      "ghost_positions": int,
      "force_closes_succeeded": int,
      "force_closes_failed": int,
      "broker_reachable": bool,
    }
    """
    result = {
        "broker_positions": 0,
        "ghost_positions": 0,
        "force_closes_succeeded": 0,
        "force_closes_failed": 0,
        "broker_reachable": True,
    }

    try:
        broker_positions = await client.get_positions()
    except Exception as e:
        # D298: include exception type so empty messages don't lose info
        logger.warning(
            "D242 EOD_FORCE_CLOSE DEGRADED: get_positions raised (%s)",
            _fmt_exc(e),
        )
        result["broker_reachable"] = False
        return result

    result["broker_positions"] = len(broker_positions or [])
    internal_syms: set[str] = {
        getattr(p, "ticker", "") for p in
        (getattr(position_manager, "open_positions", []) or [])
    }

    for bp in broker_positions or []:
        sym = (bp.get("symbol") or "").upper()
        if not sym or sym in internal_syms:
            continue
        # Ghost position — broker has it, tracker doesn't
        result["ghost_positions"] += 1
        # 2026-05-28 (doc 175): route through cancel-blocking-stops-then-
        # close. EVERY EOD force-close this week (Tue LFS, Wed APPS, Thu
        # UMAC) hit 403 "insufficient qty available, held_for_orders=N"
        # because the OTO child stop holds the inventory. Direct
        # close_position cannot succeed in that state.
        try:
            from src.execution.bridge import attempt_close_with_status_check
            qty_int = 0
            try:
                qty_int = int(float(bp.get("qty") or 0))
            except (TypeError, ValueError):
                pass
            close_result = await attempt_close_with_status_check(
                client=client,
                ticker=sym,
                qty=abs(qty_int),
                max_retries=3,
                cancel_blocking_stops_first=True,
            )
            if close_result.get("succeeded"):
                logger.warning(
                    "D242 EOD_FORCE_CLOSE %s: ghost position at broker "
                    "(qty=%s, unrealized_pl=$%s) — closed via "
                    "cancel-stops-then-close on attempt %d",
                    sym, bp.get("qty"), bp.get("unrealized_pl"),
                    close_result.get("attempts", 1),
                )
                result["force_closes_succeeded"] += 1
            else:
                logger.error(
                    "D242 EOD_FORCE_CLOSE %s: ghost close FAILED after "
                    "cancel-stops + %d retries — last_error=%s — "
                    "position remains open; manual intervention required",
                    sym, close_result.get("attempts", 0),
                    close_result.get("last_error"),
                )
                result["force_closes_failed"] += 1
        except Exception as e:
            # D298: include exception type so empty messages don't lose info
            logger.error(
                "D242 EOD_FORCE_CLOSE %s: ghost close FAILED (%s) — "
                "position remains open at broker; manual intervention "
                "required before next session",
                sym, _fmt_exc(e),
            )
            result["force_closes_failed"] += 1

    if result["ghost_positions"] == 0:
        logger.info(
            "D242 EOD_FORCE_CLOSE: clean — %d broker positions all "
            "internally tracked", result["broker_positions"],
        )
    return result


# ── D238 EOD broker-truth-first reconciliation ─────────────────────


async def run_eod_broker_truth_recon(
    *,
    client: Any,
    journal_pnl: float,
    per_ticker_journal: dict[str, float],
) -> dict[str, Any]:
    """
    Broker-truth-first EOD reconciliation. Pulls broker fills as the
    authoritative record of the day; reconciles internal journal
    against it; emits `D238 EOD_RECONCILIATION_DELTA` on any
    disagreement.

    Distinct from `trade_journal.run_pnl_reconciliation` (which uses
    journal as source of truth and fills as the check) — this is the
    inverted version: broker is truth, journal is the candidate for
    discrepancy.

    Behaviour:
      - Pull `client.get_orders(status="closed")` as the broker truth
      - Compute per-ticker realized P&L from filled orders
      - Compare against `per_ticker_journal`
      - Emit D238 ERROR with per-ticker breakdown on any disagreement > $1
      - Emit "D238: clean" INFO on full agreement

    Returns: {
      "broker_total_pnl": float,
      "journal_total_pnl": float,
      "delta_usd": float,
      "ticker_disagreements": list[dict],
      "broker_reachable": bool,
    }
    """
    result = {
        "broker_total_pnl": 0.0,
        "journal_total_pnl": float(journal_pnl),
        "delta_usd": 0.0,
        "ticker_disagreements": [],
        "broker_reachable": True,
    }

    try:
        # D238 (2026-05-12 fix): filter to TODAY's orders only.
        # Previously this pulled the 500 most-recent orders (potentially
        # weeks of history) and compared to today-only journal, generating
        # false-positive $41k+ deltas on accounts with prior holdings.
        # Today defined as: midnight ET = 04:00 UTC of today.
        from datetime import datetime, timezone, timedelta
        # ET is UTC-4 (EDT) or UTC-5 (EST) -- use 09:00 UTC as a safe
        # "before any US session activity" anchor that always precedes
        # today's first ET trading-related event.
        now_utc = datetime.now(timezone.utc)
        today_et_midnight_utc = (now_utc - timedelta(hours=now_utc.hour,
                                                       minutes=now_utc.minute,
                                                       seconds=now_utc.second,
                                                       microseconds=now_utc.microsecond))
        # Use start of UTC day. Alpaca paper trades are paper, so US session
        # boundary leniency is fine; we just need to exclude prior days.
        after_iso = today_et_midnight_utc.isoformat().replace("+00:00", "Z")
        orders = await client.get_orders(status="all", limit=500,
                                          after=after_iso)
    except Exception as e:
        # D298 (2026-05-19): include exception type so the "raised ()"
        # silent failure from Friday 5/15 (httpx.ReadTimeout with empty
        # args) becomes "raised (ReadTimeout)" — actionable diagnostic.
        logger.warning(
            "D238 EOD_RECONCILIATION_DELTA DEGRADED: get_orders raised "
            "(%s) — recon skipped", _fmt_exc(e),
        )
        result["broker_reachable"] = False
        return result

    # Compute broker per-ticker P&L from filled orders
    by_ticker: dict[str, dict[str, list[tuple[float, float]]]] = {}
    for o in orders or []:
        if (o.get("status") or "").lower() not in ("filled", "done_for_day"):
            continue
        sym = (o.get("symbol") or "").upper()
        side = (o.get("side") or "").lower()
        qty = _safe_float(o.get("filled_qty"))
        price = _safe_float(o.get("filled_avg_price"))
        if not sym or qty <= 0 or price <= 0 or side not in ("buy", "sell"):
            continue
        bucket = by_ticker.setdefault(sym, {"buy": [], "sell": []})
        bucket[side].append((qty, price))

    broker_per_ticker: dict[str, float] = {}
    for sym, sides in by_ticker.items():
        buy_qty = sum(q for q, _ in sides["buy"])
        sell_qty = sum(q for q, _ in sides["sell"])
        avg_buy = sum(q * p for q, p in sides["buy"]) / buy_qty if buy_qty else 0
        avg_sell = sum(q * p for q, p in sides["sell"]) / sell_qty if sell_qty else 0
        matched = min(buy_qty, sell_qty)
        if matched > 0 and avg_buy > 0 and avg_sell > 0:
            broker_per_ticker[sym] = round(matched * (avg_sell - avg_buy), 2)

    broker_total = round(sum(broker_per_ticker.values()), 2)
    result["broker_total_pnl"] = broker_total
    result["delta_usd"] = round(broker_total - journal_pnl, 2)

    # Per-ticker disagreements
    all_syms = sorted(set(broker_per_ticker) | set(per_ticker_journal))
    for sym in all_syms:
        b = broker_per_ticker.get(sym)
        j = per_ticker_journal.get(sym)
        if b is None and j is None:
            continue
        b = b or 0.0
        j = j or 0.0
        if abs(b - j) > 1.00:
            result["ticker_disagreements"].append({
                "ticker": sym,
                "broker": b,
                "journal": j,
                "delta": round(b - j, 2),
            })

    if abs(result["delta_usd"]) <= 1.00 and not result["ticker_disagreements"]:
        logger.info(
            "D238 EOD_RECONCILIATION_DELTA: clean — broker_pnl=$%+.2f "
            "journal_pnl=$%+.2f delta=$%+.2f (within $1)",
            broker_total, journal_pnl, result["delta_usd"],
        )
    else:
        breakdown = "\n".join(
            f"    {d['ticker']}: broker=$%+.2f journal=$%+.2f delta=$%+.2f" % (
                d["broker"], d["journal"], d["delta"],
            )
            for d in result["ticker_disagreements"]
        )
        logger.error(
            "D238 EOD_RECONCILIATION_DELTA: broker_pnl=$%+.2f "
            "journal_pnl=$%+.2f delta=$%+.2f — %d ticker disagreement(s):\n%s",
            broker_total, journal_pnl, result["delta_usd"],
            len(result["ticker_disagreements"]), breakdown,
        )
    return result


# ── Combined runner — wire into main.py EOD sequence ──────────────


async def run_all_eod_failsafes(
    *,
    client: Any,
    position_manager: Any,
    journal_pnl: float,
    per_ticker_journal: dict[str, float],
    expected_order_ids: set[str] | None = None,
) -> dict[str, Any]:
    """
    Run the full EOD failsafe sequence. Order matters:
      1. D242 force-close ghosts (clears unprotected positions FIRST)
      2. D241 cancel orphan orders (cleans up after force-close)
      3. D238 broker-truth recon (final P&L attribution check)

    Each failsafe is independent of the others' success.
    Returns a structured dict for monitoring / dashboard surfaces.
    """
    out = {
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "force_close": None,
        "safety_cancel": None,
        "broker_truth_recon": None,
    }

    # 1. Force-close ghost positions FIRST (most urgent — naked overnight risk)
    try:
        out["force_close"] = await run_eod_force_close(
            client=client, position_manager=position_manager,
        )
    except Exception as e:
        logger.error("D242 outer error (non-fatal): %s", e)

    # 2. Cancel orphan working orders
    try:
        out["safety_cancel"] = await run_eod_safety_cancel(
            client=client, expected_order_ids=expected_order_ids,
        )
    except Exception as e:
        logger.error("D241 outer error (non-fatal): %s", e)

    # 3. Broker-truth-first recon (informational, not corrective)
    try:
        out["broker_truth_recon"] = await run_eod_broker_truth_recon(
            client=client,
            journal_pnl=journal_pnl,
            per_ticker_journal=per_ticker_journal,
        )
    except Exception as e:
        logger.error("D238 outer error (non-fatal): %s", e)

    return out
