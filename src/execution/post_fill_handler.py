"""Single canonical post-fill bookkeeping handler.

Per doc 82 (architectural synthesis) and PROMPT_06.

EVERY entry call site (PHASE2_BUY, RESCAN, VWAP_BREAKOUT, etc.) MUST
call `post_fill_bookkeeping` after `bridge.execute_verdict()` returns
a non-None order.

Operations performed (in order):
    1. D215 EXECUTION RECORDED via exec_recorder
    2. Trade journal record_fill
    3. Exit ladder (cancel old stop, submit tranches, residual stop,
       mirror in-memory stop_order_id) via cancel_stop_and_submit_exit_ladder
    4. Stop register with stop_resubmitter (Bug AS hardened — empty
       OID instead of entry-order-ID fallback)
    5. State_mgr update_position + save (canonical schema)
    6. D278 BAR-1 EXIT gate:
       - if t1_next_open: emit "D278 BAR-1 SKIPPED <path>" log
       - if bar1_legacy AND bar1_exit_enabled: schedule async BAR-1 task
         that fires at T+60s, sets _bar1_exit_fired flag (Phase 3 race
         prevention), cancels stop+tranches, calls close_position,
         performs full cleanup (trailing/early_profit/tranche/entry_delay
         /d170 trackers + state_mgr.remove_position).

Items NOT in the helper (kept inline at call sites for path-specific
behavior preservation):
    - Discord webhooks (post_trade_open, post_trade_open_enriched) —
      currently PHASE2_BUY-only; preserving that intentional difference
    - D164 EARLY_PROFIT_TAKE registration — happens AFTER this helper
    - Position-tier / kelly_tier / gap_pct in state_mgr — those are
      passed as kwargs to this helper and used iff non-None

The helper takes order.fill_price (with submitted_price fallback for
display), but the underlying source — `order.fill_price` — comes from
broker confirmation in bridge.execute_verdict()'s D217 poll path.
That's the contract that hardens against AT-1 going forward.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ── Helper-local helpers (D278 gate) ──────────────────────────────


def _d278_active_policy(settings) -> str:
    """Mirror of main.py:_d278_active_policy. Local copy avoids circular
    import: this module is imported by main.py."""
    import os
    env_val = os.environ.get("MOMENTUM_EXIT_POLICY", "").strip().lower()
    if env_val in {"bar1_legacy", "t1_next_open"}:
        return env_val
    cfg_val = str(
        getattr(getattr(settings, "execution", None), "exit_policy", "bar1_legacy")
        or "bar1_legacy"
    ).strip().lower()
    if cfg_val in {"bar1_legacy", "t1_next_open"}:
        return cfg_val
    return "bar1_legacy"


def _d278_bar1_gated_off(settings) -> bool:
    return _d278_active_policy(settings) != "bar1_legacy"


# ── Context bundle ────────────────────────────────────────────────


@dataclass
class PostFillContext:
    """Singleton bundle. Construct once at startup; pass to every
    `post_fill_bookkeeping` call.

    All fields are required EXCEPT trailing_manager / early_profit_taker /
    tranche_taker / entry_delay_mgr / d170_pending_verdicts /
    stopped_out_tickers — those are only used by the BAR-1 EXIT cleanup
    path and can be None if the call site doesn't set
    bar1_exit_enabled (or sets t1_next_open).
    """
    settings: Any
    bridge: Any
    client: Any                          # AlpacaDataClient
    state_mgr: Any
    trade_journal: Any
    stop_resubmitter: Any
    tranche_monitor: Any
    exec_recorder: Any
    orchestrator: Any                    # for vix_level
    # BAR-1 cleanup dependencies (optional; required only when BAR-1 fires)
    trailing_manager: Optional[Any] = None
    early_profit_taker: Optional[Any] = None
    tranche_taker: Optional[Any] = None
    entry_delay_mgr: Optional[Any] = None
    d170_pending_verdicts: Optional[dict] = None
    stopped_out_tickers: Optional[set] = None


# ── Main entry point ──────────────────────────────────────────────


async def post_fill_bookkeeping(
    *,
    order: Any,
    verdict: Any,
    scored: Any,
    candidate: Any,
    path: str,                           # "PHASE2_BUY" | "RESCAN" | "VWAP_BREAKOUT"
    ctx: PostFillContext,
    log_prefix: str,                     # "Phase2" | "RESCAN" | "VWAP" — for log readability
    faller_assessment: Optional[Any] = None,
    position_tier: Optional[int] = None,
    kelly_tier: Optional[int] = None,
    gap_pct: Optional[float] = None,
) -> None:
    """The unified post-fill bookkeeping handler.

    Calling this MUST be idempotent only at the level of the operations
    it wraps (D215 / journal / state_mgr writes are idempotent by
    ticker/order_id keys; exit_ladder is NOT idempotent — calling it
    twice for the same fill double-cancels). Don't double-call.
    """
    settings = ctx.settings
    bridge = ctx.bridge
    client = ctx.client
    state_mgr = ctx.state_mgr
    trade_journal = ctx.trade_journal
    stop_resubmitter = ctx.stop_resubmitter
    tranche_monitor = ctx.tranche_monitor
    exec_recorder = ctx.exec_recorder
    orchestrator = ctx.orchestrator

    # ── 1. D215 EXECUTION RECORDED ────────────────────────────────
    fill_price = (
        order.fill_price
        if order.fill_price and order.fill_price > 0
        else order.submitted_price
    )
    # Bug AV fix (PROMPT_09, 2026-04-30): derive side from verdict.direction.
    # Pre-fix: hardcoded "buy" propagated direction-blind to D215 record +
    # journal lookup. Long-only paths coincidentally worked but any short
    # routed through this helper (D161/D207/D170 in N+1) would have its
    # broker-confirmed short order recorded as a long buy in the journal.
    _verdict_direction = getattr(verdict, "direction", "long")
    _side = "sell_short" if _verdict_direction == "short" else "buy"
    exec_recorder.record_execution(
        ticker=order.ticker,
        side=_side,
        fill_price=fill_price,
        signal_price=order.submitted_price,
        qty=order.qty,
        order_id=order.order_id,
        execution_path=path,
        gap_pct=abs(candidate.gap_pct) if candidate else 0.0,
        rvol=candidate.rvol if candidate else 0.0,
        mfcs=verdict.mfcs,
        float_shares=candidate.float_shares if candidate else None,
        market_cap=candidate.market_cap if candidate else None,
        catalyst_type=(
            getattr(verdict, "catalyst_profile", None)
            and getattr(verdict.catalyst_profile, "catalyst_type", "unknown")
            or "unknown"
        ),
        faller_score=faller_assessment.score if faller_assessment else None,
        prior_gap_count=getattr(candidate, "prior_gap_count", None) if candidate else None,
        is_day2_runner=getattr(candidate, "is_day2_runner", False) if candidate else False,
        stop_loss=verdict.stop_loss,
        target_prices=list(verdict.target_prices),
        kelly_tier=getattr(verdict, "kelly_tier", 1),
        vix_level=getattr(orchestrator, "_vix_level", None),
        direction=getattr(verdict, "direction", "long"),
        verdict=verdict,
        scored=scored,
        candidate=candidate,
    )

    # ── 2. Trade journal record_fill ──────────────────────────────
    try:
        for j_tid, j_ent in trade_journal._entries.items():
            if j_ent.ticker == verdict.ticker and j_ent.action == "BUY":
                _journal_slip = 0.0
                if order.fill_price and order.fill_price > 0 and order.submitted_price > 0:
                    _journal_slip = (
                        (order.fill_price - order.submitted_price) / order.submitted_price * 10000
                    )
                trade_journal.record_fill(
                    j_tid, order.order_id,
                    fill_price, order.qty,
                    slippage_bps=_journal_slip,
                    order_status=order.status,
                    stop_order_id=order.stop_order_id,
                )
                break
    except Exception:
        pass

    # ── 3. Exit ladder (cancel old stop, submit tranches, residual stop) ──
    new_stop_oid = ""
    tranche_oids: list[str] = []
    pos_obj = None
    el_result = None
    try:
        from src.execution.exit_ladder import cancel_stop_and_submit_exit_ladder
        for p in bridge.position_manager.open_positions:
            if p.ticker == order.ticker:
                pos_obj = p
                break
        if pos_obj is None:
            logger.warning(
                "%s exit-ladder %s: position not found post-fill",
                log_prefix, order.ticker,
            )
        else:
            is_shortable = True
            try:
                _asset = await client.check_asset_tradable(order.ticker)
                is_shortable = _asset.get("shortable", True)
            except Exception as _ae:
                logger.debug(
                    "%s D147 shortability check failed %s: %s — assuming shortable",
                    log_prefix, order.ticker, _ae,
                )
            tranches = bridge.position_manager.compute_exit_tranches(pos_obj)
            # Bug W fix (2026-04-24): broker-truth stop, not verdict.stop_loss
            # doc 228 (#3): size the exit ladder off pos_obj.remaining_qty (the MERGED held
            # qty), NOT order.qty (the single fill). On a re-buy these differ — passing the
            # single fill made the ladder's residual go negative -> the routine bailed before
            # arming any stop/targets, leaving the added shares naked. Use the merged qty.
            el_result = await cancel_stop_and_submit_exit_ladder(
                client,
                ticker=order.ticker,
                position_qty=int(getattr(pos_obj, "remaining_qty", 0) or order.qty),
                tranches=tranches,
                stop_price=pos_obj.stop_loss,
                old_stop_order_id=order.stop_order_id,
                is_shortable=is_shortable,
                log_prefix=log_prefix,
            )
            new_stop_oid = el_result.new_stop_order_id
            # Bug AS fix (2026-04-28): mirror new stop OID into in-memory pos
            pos_obj.stop_order_id = new_stop_oid
            tranche_oids = list(el_result.tranche_order_ids)
            limit_tranches = [_t for _t in tranches if _t.exit_type == "limit"]
            for _t_oid, _t in zip(tranche_oids, limit_tranches):
                if _t_oid:
                    tranche_monitor.register_tranche_order(
                        order_id=_t_oid,
                        ticker=order.ticker,
                        tranche_number=_t.tranche_number,
                        target_price=_t.target,
                        qty=_t.qty,
                    )
            if el_result.is_position_unprotected:
                logger.critical(
                    "%s exit-ladder %s: POSITION UNPROTECTED — "
                    "residual_qty=%d skip=%s",
                    log_prefix, order.ticker, el_result.residual_qty,
                    el_result.skipped_reason or "(none)",
                )
    except Exception as _el_e:
        logger.error(
            "%s exit-ladder %s: unexpected error: %s",
            log_prefix, order.ticker, _el_e, exc_info=True,
        )

    # ── 4. Stop register with stop_resubmitter ────────────────────
    # Bug AS hardening: NO entry-order-ID fallback (corrupts stop_order_id).
    if not new_stop_oid:
        logger.warning(
            "%s stop register %s: exit_ladder yielded no new stop OID "
            "(residual_qty=%s skip=%s unprotected=%s) — registering empty "
            "OID; position has NO ratchetable broker stop",
            log_prefix, order.ticker,
            getattr(el_result, "residual_qty", "?"),
            getattr(el_result, "skipped_reason", "(unknown)"),
            getattr(el_result, "is_position_unprotected", "?"),
        )
    effective_stop_oid = new_stop_oid
    try:
        stop_resubmitter.register_stop(
            ticker=order.ticker,
            order_id=effective_stop_oid,
            stop_price=pos_obj.stop_loss if pos_obj else verdict.stop_loss,
            # doc 229 (#5): register the MERGED held qty, not the single fill — on a re-buy a
            # later stop-ratchet resubmit must cover all merged shares, not just the last fill's.
            # (Matches the exit-ladder sizing at line ~236.)
            qty=int(getattr(pos_obj, "remaining_qty", 0) or order.qty) if pos_obj else order.qty,
        )
        logger.info(
            "STOP registered: %s @ $%.2f (oid=%s, type=%s, path=%s)",
            order.ticker,
            pos_obj.stop_loss if pos_obj else verdict.stop_loss,
            effective_stop_oid,
            "standalone" if new_stop_oid else "OTO-legacy",
            path,
        )
    except Exception as e:
        logger.warning("Stop registration error: %s", e)

    # ── 5. state_mgr.update_position + save ───────────────────────
    try:
        # Resolve position_tier: explicit param > position_manager attr > 3
        if position_tier is None:
            _pos_for_tier = bridge.position_manager._positions.get(order.ticker)
            position_tier = getattr(_pos_for_tier, "position_tier", 3)
        if kelly_tier is None:
            kelly_tier = getattr(verdict, "kelly_tier", 1)
        if gap_pct is None:
            gap_pct = getattr(verdict, "gap_pct", 0.0)

        # doc 228 (#2, the sharpest ripple): persist the MERGED tracker values, NOT the
        # single-fill `order`. On a re-buy, writing order.qty/order.timestamp would overwrite
        # the persisted merged qty with the single fill + reset opened_at to "now" -> a crash
        # restart would restore the UNDERCOUNT (re-creating the exact LASE 403/overnight-carry
        # on the persistence layer). Source qty/remaining_qty/entry/opened_at/tranches from
        # pos_obj (the canonical merged tracker) when available.
        _p = pos_obj if pos_obj is not None else None
        _persist_qty = int(getattr(_p, "qty", 0) or order.qty)
        _persist_remaining = int(getattr(_p, "remaining_qty", 0) or order.qty)
        _persist_entry = float(getattr(_p, "entry_price", 0) or order.submitted_price)
        _persist_tranches = int(getattr(_p, "tranches_filled", 0) or 0)
        _persist_opened = (_p.opened_at.isoformat() if _p is not None and getattr(_p, "opened_at", None)
                           else order.timestamp.isoformat())
        state_mgr.update_position(
            ticker=order.ticker,
            qty=_persist_qty,
            entry_price=_persist_entry,
            signal_price=order.signal_price,
            stop_loss=verdict.stop_loss,
            target_prices=list(verdict.target_prices),
            tranches_filled=_persist_tranches,
            remaining_qty=_persist_remaining,
            realized_pnl=0.0,
            entry_order_id=order.order_id,
            stop_order_id=new_stop_oid or order.stop_order_id,
            tranche_order_ids=tranche_oids,
            opened_at=_persist_opened,
            position_tier=position_tier,
            kelly_tier=kelly_tier,
            gap_pct=gap_pct,
        )
        state_mgr.save()
    except Exception as e:
        logger.debug("D64: State save error: %s", e)

    # ── 6. BAR-1 EXIT gate (D278) ─────────────────────────────────
    # Per doc 70 + doc 82: gate BAR-1 EXIT on exit_policy.
    # When 't1_next_open': SKIPPED — positions carry overnight.
    if _d278_bar1_gated_off(settings):
        logger.info(
            "D278 BAR-1 SKIPPED %s %s: exit_policy=%s "
            "(position will carry; close-at-next-open via D91)",
            path, order.ticker, _d278_active_policy(settings),
        )

    if (
        getattr(settings.execution, "bar1_exit_enabled", False)
        and not _d278_bar1_gated_off(settings)
    ):
        _b1_delay = getattr(settings.execution, "bar1_exit_delay_seconds", 60)
        _b1_pct = getattr(settings.execution, "bar1_exit_pct", 1.0)

        async def _bar1_exit_task(
            _ticker=order.ticker,
            _qty=order.qty,
            _delay=_b1_delay,
            _pct=_b1_pct,
            _path=path,
        ):
            await asyncio.sleep(_delay)
            _pos = bridge.position_manager._positions.get(_ticker)
            if _pos is None or getattr(_pos, "_bar1_exit_fired", False):
                return
            _exit_qty = int(_pos.remaining_qty * _pct)
            if _exit_qty <= 0:
                return
            # D160 RACE FIX: Set flag BEFORE the first await
            _pos._bar1_exit_fired = True
            try:
                if hasattr(_pos, "stop_order_id") and _pos.stop_order_id:
                    try:
                        await client.cancel_order(_pos.stop_order_id)
                    except Exception:
                        pass
                for _toid in getattr(_pos, "tranche_order_ids", []) or []:
                    try:
                        await client.cancel_order(_toid)
                    except Exception:
                        pass
                # D147 FIX: close_position (DELETE /v2/positions)
                await client.close_position(_ticker)
                logger.info(
                    "D146 %s BAR-1 EXIT: %s — sold %d shares at T+%.0fs",
                    _path, _ticker, _exit_qty, _delay,
                )
                if _pct >= 1.0:
                    if ctx.stopped_out_tickers is not None:
                        ctx.stopped_out_tickers.add(_ticker)
                    _snap_b1 = await client.get_snapshots([_ticker])
                    _b1_px = float(
                        _snap_b1.get(_ticker, {}).get("last_price", 0)
                        or _pos.entry_price
                    )
                    if ctx.trailing_manager is not None:
                        ctx.trailing_manager.remove_position(_ticker)
                    if ctx.early_profit_taker is not None:
                        ctx.early_profit_taker.remove(_ticker)
                    if ctx.tranche_taker is not None:
                        ctx.tranche_taker.remove(_ticker)
                    if ctx.entry_delay_mgr is not None:
                        ctx.entry_delay_mgr.remove(_ticker)
                    if ctx.d170_pending_verdicts is not None:
                        ctx.d170_pending_verdicts.pop(_ticker, None)
                    try:
                        _b1_e = await bridge.close_with_attribution(
                            ticker=_ticker, exit_price=_b1_px,
                        )
                        if _b1_e:
                            pass  # session_trades not accessible in task
                    except Exception as _b1_ce:
                        logger.error(
                            "D146 %s: close_with_attribution failed for %s: %s",
                            _path, _ticker, _b1_ce,
                        )
                        bridge.position_manager.remove_position(_ticker)
                    try:
                        state_mgr.remove_position(_ticker)
                        state_mgr.add_stopped_out_ticker(_ticker)
                        state_mgr.save()
                    except Exception as e:
                        logger.warning("D218: position_removal failed: %s", e)
            except Exception as _b1_err:
                logger.error(
                    "D146 %s bar-1 exit failed for %s: %s — will retry in Phase 3",
                    _path, _ticker, _b1_err,
                )
                # D160 RACE FIX: Reset flag so Phase 3 can retry
                _retry_pos = bridge.position_manager._positions.get(_ticker)
                if _retry_pos is not None:
                    _retry_pos._bar1_exit_fired = False

        asyncio.create_task(_bar1_exit_task())
        logger.info(
            "D146 %s: Scheduled bar-1 exit for %s in %.0fs",
            path, order.ticker, _b1_delay,
        )
