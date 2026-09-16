"""doc 286 (doc-285 gaps #10/#11) — position-book integrity pair.

Pins the two 2026-07-06 RIVN book-integrity failures as regression guards:

BUG 1 — tranche partial-sell never decremented internal qty (Bug-D class):
  10:04:37 D165 TRANCHE T1 HIT: RIVN selling 517 shares
  10:04:41 D165 TRANCHE T1 FILLED: sold 517 @ $19.44 remaining=1035
  10:04:58 D231 RECON_HARD_BLOCK QTY RIVN: internal_qty=1552
           broker_qty=1035 delta=-517. Bug D regression.
  Every partial-exit submitting path (D164/D165/D269-P2) decrements
  ManagedPosition.remaining_qty AFTER attempt_close_with_status_check
  confirms the fill — but nothing decremented .qty, the field eod_recon
  (D231), D218 QTY_DRIFT and attach_external_stop treat as the current
  position size. Fix: the bridge registers the live PositionManager and
  attempt_close_with_status_check books every broker-CONFIRMED partial
  fill onto .qty (and ONLY .qty — remaining_qty stays owned by the
  submitting path per the doc-270 contract).

BUG 2 — backstop stop-oid write-back refused (D56/D230 case):
  10:04:53 D313 EMERGENCY_STOP_SUBMITTED RIVN: oid=aa9620e7... qty=1035
  10:04:53 D273 STOP_ATTACH FAILED RIVN: qty mismatch — caller passed
           qty=1035 but position.qty=1552 (stop must cover full position)
  10:04:58 D230 RECON_WARN STOP RIVN: internal_stop=$18.22 but no
           stop_order_id
  The watcher submitted a REAL emergency stop for the broker-true qty,
  but attach_external_stop validated against the stale ENTRY qty and
  refused — so recon, D98 and the stop-resubmitter all stayed blind to
  the live stop. Fixes: (a) attach_external_stop validates against the
  CURRENTLY-HELD qty and accepts over-coverage; (b) if the attach path
  still refuses/raises, the watcher force-writes the broker-confirmed
  oid directly onto the tracked position (guarded — position may be
  gone; never a crash).

See docs/research-log/285_first_session_gap_hunt.md §2 items 10/11.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import src.execution.bridge as bridge_mod
from src.execution.bridge import (
    _register_active_position_manager,
    attempt_close_with_status_check,
)
from src.execution.position_manager import ManagedPosition, PositionManager


# ── Factories ────────────────────────────────────────────────────────


def _build_pm() -> PositionManager:
    """Minimal config so PositionManager init succeeds (Bug AM pattern)."""
    cfg = MagicMock()
    cfg.daily_loss_limit_pct = 0.10
    cfg.max_positions = 8
    cfg.stop_loss_pct = 0.055
    cfg.max_position_size_pct = 0.15
    cfg.tier1_max_concurrent = 2
    cfg.tier2_max_concurrent = 4
    cfg.tier3_max_concurrent = 6
    cfg.tier4_max_concurrent = 8
    return PositionManager(config=cfg, starting_equity=150_000.0)


def _rivn_position(qty: int = 1552, remaining: int | None = None) -> ManagedPosition:
    """Today's RIVN as tracked at entry (OTO fill 10:00, stop leg lost)."""
    pos = ManagedPosition(
        ticker="RIVN",
        qty=qty,
        entry_price=19.31,
        signal_price=18.50,
        stop_loss=18.22,
        target_prices=[19.43, 20.08, 21.03],
    )
    if remaining is not None:
        pos.remaining_qty = remaining
    return pos


class _ImmediateFillClient:
    """close_position confirms the fill in the submit response itself
    (the branch RIVN's T1 sell took on 7/6)."""

    def __init__(self, fill_price: float = 19.44, filled_qty: int | None = None):
        self._px = fill_price
        self._fq = filled_qty
        self.close_calls: list[Any] = []

    async def close_position(self, ticker, qty=None):
        self.close_calls.append(qty)
        fq = self._fq if self._fq is not None else qty
        return {
            "id": "ord-doc286",
            "status": "filled",
            "filled_avg_price": str(self._px),
            "filled_qty": str(fq),
        }


class _PollConfirmClient:
    """close_position returns merely-ACCEPTED; the fill is confirmed by
    the doc-269 terminal poll (get_orders)."""

    def __init__(self, filled_qty: int = 517, fill_price: float = 19.44):
        self._fq = filled_qty
        self._px = fill_price

    async def close_position(self, ticker, qty=None):
        return {"id": "ord-doc286", "status": "accepted"}

    async def get_orders(self, status="all", limit=100, symbols=None):
        return [{
            "id": "ord-doc286",
            "status": "filled",
            "filled_qty": str(self._fq),
            "filled_avg_price": str(self._px),
        }]


class _AlwaysRejectClient:
    """Every close attempt raises — the fill is NEVER broker-confirmed."""

    async def close_position(self, ticker, qty=None):
        raise RuntimeError("403 Forbidden: broker says no")


@pytest.fixture(autouse=True)
def _clean_pm_registry():
    """doc 286 registry is module-global — isolate every test."""
    _register_active_position_manager(None)
    yield
    _register_active_position_manager(None)


@pytest.fixture(autouse=True)
def _no_incident_bus(monkeypatch):
    """The live bot appends to data/ops/incidents_*.jsonl — unit tests
    must not touch it. OPS_INCIDENT_BUS_ENABLED is the bus kill switch."""
    monkeypatch.setenv("OPS_INCIDENT_BUS_ENABLED", "false")


# ═══════════════════════════════════════════════════════════════
# BUG 1 — confirmed partial fill must decrement ManagedPosition.qty
# ═══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_confirmed_partial_close_decrements_qty_exactly_once():
    """THE 7/6 RIVN T1 replay: 517 of 1552 confirmed sold → qty must
    read 1035 (broker truth), decremented exactly once. FAILS on the
    old behavior (qty stayed 1552 → D231 Bug-D recon block all day)."""
    pm = _build_pm()
    pos = pm.add_position(_rivn_position())
    _register_active_position_manager(pm)

    res = await attempt_close_with_status_check(
        client=_ImmediateFillClient(),
        ticker="RIVN",
        qty=517,
        max_retries=3,
        retry_backoff_s=0.01,
        partial=True,
    )

    assert res["succeeded"] is True
    assert res["filled_qty"] == 517
    # The fix: qty now tracks the broker-confirmed holding.
    assert pos.qty == 1035, (
        "confirmed partial fill must decrement ManagedPosition.qty "
        "(old behavior left it at 1552 — the Bug-D recon regression)"
    )
    # Exactly once — not 1552-517-517=518.
    assert pos.qty != 518
    # remaining_qty is OWNED BY THE SUBMITTING PATH (doc-270 contract);
    # the helper must NOT touch it (the caller decrements it next —
    # double-decrement would half-close the book).
    assert pos.remaining_qty == 1552
    # ... and once the caller does its own decrement, the book agrees:
    pos.remaining_qty = max(0, pos.remaining_qty - int(res["filled_qty"]))
    assert pos.remaining_qty == pos.qty == 1035


@pytest.mark.asyncio
async def test_poll_confirmed_partial_close_decrements_qty():
    """Same booking through the doc-269 terminal-poll branch (submit
    response merely ACCEPTED; get_orders confirms the fill)."""
    pm = _build_pm()
    pos = pm.add_position(_rivn_position())
    _register_active_position_manager(pm)

    res = await attempt_close_with_status_check(
        client=_PollConfirmClient(filled_qty=517),
        ticker="RIVN",
        qty=517,
        max_retries=3,
        retry_backoff_s=0.01,
        partial=True,
    )

    assert res["succeeded"] is True
    assert pos.qty == 1035


class _TerminalCanceledFractionClient:
    """close_position returns merely-ACCEPTED; the poll finds the order
    terminal-CANCELED with a confirmed fraction filled (the bridge books
    exactly that fraction — 'terminal partial-of-partial' branch)."""

    def __init__(self, filled_qty: int = 200, fill_price: float = 19.40):
        self._fq = filled_qty
        self._px = fill_price

    async def close_position(self, ticker, qty=None):
        return {"id": "ord-doc286", "status": "accepted"}

    async def get_orders(self, status="all", limit=100, symbols=None):
        return [{
            "id": "ord-doc286",
            "status": "canceled",
            "filled_qty": str(self._fq),
            "filled_avg_price": str(self._px),
        }]


class _CancelRecheckFractionClient:
    """The order stays LIVE through the whole confirm budget; the bridge
    cancels it and the post-cancel recheck shows a confirmed fraction
    (cancel raced a fill — the fill wins)."""

    def __init__(self, filled_qty: int = 300, fill_price: float = 19.38):
        self._fq = filled_qty
        self._px = fill_price
        self.cancelled = False

    async def close_position(self, ticker, qty=None):
        return {"id": "ord-doc286", "status": "accepted"}

    async def cancel_order(self, oid):
        self.cancelled = True

    async def get_orders(self, status="all", limit=100, symbols=None):
        if not self.cancelled:
            return [{"id": "ord-doc286", "status": "new"}]
        return [{
            "id": "ord-doc286",
            "status": "canceled",
            "filled_qty": str(self._fq),
            "filled_avg_price": str(self._px),
        }]


@pytest.mark.asyncio
async def test_terminal_canceled_fraction_decrements_qty():
    """Booking site 3 of 4: order terminal=canceled with 200/517 filled →
    the bridge books succeeded=True filled_qty=200 and qty must drop by
    exactly the broker-confirmed 200 (never the requested 517)."""
    pm = _build_pm()
    pos = pm.add_position(_rivn_position())
    _register_active_position_manager(pm)

    res = await attempt_close_with_status_check(
        client=_TerminalCanceledFractionClient(filled_qty=200),
        ticker="RIVN",
        qty=517,
        max_retries=1,
        retry_backoff_s=0.01,
        partial=True,
    )

    assert res["succeeded"] is True
    assert res["filled_qty"] == 200
    assert pos.qty == 1552 - 200, "decrement by the CONFIRMED fraction only"


@pytest.mark.asyncio
async def test_cancel_recheck_confirmed_fraction_decrements_qty(monkeypatch):
    """Booking site 4 of 4: unconfirmable order → cancel → recheck finds
    the cancel raced a fill (300 confirmed) → qty drops by exactly 300."""

    async def _fast_sleep(_s):  # collapse the 8s confirm budget
        return None

    monkeypatch.setattr("asyncio.sleep", _fast_sleep)
    pm = _build_pm()
    pos = pm.add_position(_rivn_position())
    _register_active_position_manager(pm)

    client = _CancelRecheckFractionClient(filled_qty=300)
    res = await attempt_close_with_status_check(
        client=client,
        ticker="RIVN",
        qty=517,
        max_retries=1,
        retry_backoff_s=0.01,
        partial=True,
    )

    assert client.cancelled is True
    assert res["succeeded"] is True
    assert res["filled_qty"] == 300
    assert pos.qty == 1552 - 300


@pytest.mark.asyncio
async def test_unconfirmed_partial_close_does_not_touch_qty():
    """IRON RULE: book state changes ONLY on confirmed broker evidence.
    A rejected/unconfirmed close must leave qty exactly as it was."""
    pm = _build_pm()
    pos = pm.add_position(_rivn_position())
    _register_active_position_manager(pm)

    res = await attempt_close_with_status_check(
        client=_AlwaysRejectClient(),
        ticker="RIVN",
        qty=517,
        max_retries=2,
        retry_backoff_s=0.01,
        partial=True,
    )

    assert res["succeeded"] is False
    assert pos.qty == 1552, "no confirmed fill → no booking"
    assert pos.remaining_qty == 1552


@pytest.mark.asyncio
async def test_full_close_does_not_decrement_qty():
    """partial=False closes remove the whole position via the caller
    (close_with_attribution) — the doc-286 booking must not fire."""
    pm = _build_pm()
    pos = pm.add_position(_rivn_position())
    _register_active_position_manager(pm)

    res = await attempt_close_with_status_check(
        client=_ImmediateFillClient(filled_qty=1552),
        ticker="RIVN",
        qty=1552,
        max_retries=3,
        retry_backoff_s=0.01,
        partial=False,
    )

    assert res["succeeded"] is True
    assert pos.qty == 1552, "full-close bookkeeping belongs to the caller"


@pytest.mark.asyncio
async def test_partial_close_without_registered_pm_is_safe():
    """No registered PositionManager (bare helper usage, old tests,
    replay tools) → the close still succeeds; booking is a no-op."""
    _register_active_position_manager(None)
    res = await attempt_close_with_status_check(
        client=_ImmediateFillClient(),
        ticker="RIVN",
        qty=517,
        max_retries=3,
        retry_backoff_s=0.01,
        partial=True,
    )
    assert res["succeeded"] is True


@pytest.mark.asyncio
async def test_partial_close_unknown_ticker_is_safe():
    """PM registered but the ticker is not tracked (ghost close) → no
    crash, close result unaffected."""
    pm = _build_pm()
    _register_active_position_manager(pm)
    res = await attempt_close_with_status_check(
        client=_ImmediateFillClient(),
        ticker="GHOST",
        qty=100,
        max_retries=3,
        retry_backoff_s=0.01,
        partial=True,
    )
    assert res["succeeded"] is True


def test_execution_bridge_init_registers_position_manager():
    """The production wiring: constructing ExecutionBridge must register
    its PositionManager so tomorrow's 4:30 AM launch gets the booking
    without any main.py change."""
    from src.execution.bridge import ExecutionBridge

    pm = _build_pm()
    ExecutionBridge(executor=MagicMock(), position_manager=pm)
    assert bridge_mod._active_pm_ref is not None
    assert bridge_mod._active_pm_ref() is pm


# ═══════════════════════════════════════════════════════════════
# BUG 2 — emergency-stop oid write-back onto the tracked position
# ═══════════════════════════════════════════════════════════════


def _make_watcher(pm, *, broker_qty=1035, emerg_oid="emerg-oid-286"):
    """Watcher wired exactly like today's 10:04:52 RIVN violation:
    broker holds the post-T1 qty, NO protective order at the broker,
    emergency submit succeeds."""
    from src.monitoring.hedge_integrity_watcher import HedgeIntegrityWatcher

    client = MagicMock()
    client.get_positions = AsyncMock(return_value=[{
        "symbol": "RIVN", "qty": str(broker_qty),
        "avg_entry_price": "19.31", "current_price": "19.41",
    }])
    client.get_orders = AsyncMock(return_value=[])
    client.submit_stop_order = AsyncMock(
        return_value={"id": emerg_oid, "status": "accepted"},
    )
    w = HedgeIntegrityWatcher(
        client=client,
        position_manager=pm,
        poll_interval_sec=0.01,
        tolerance_sec=0.0,
        alert_webhook_url=None,
    )
    return w, client


async def _drive_to_violation(w):
    """Tick 1 starts the tolerance clock; tick 2 fires the violation."""
    await w.check_once()
    return await w.check_once()


@pytest.mark.asyncio
async def test_emergency_stop_writeback_sets_stop_order_id():
    """THE 7/6 RIVN 10:04:53 replay: tracker qty is the stale entry 1552,
    remaining_qty=1035 (T1 decremented it), broker holds 1035. The
    watcher's emergency stop for 1035 must land on the position —
    stop_order_id set, stop_loss = the real broker stop. FAILS on the
    old behavior (attach_external_stop refused on the stale qty and the
    position kept 'internal_stop but no stop_order_id' all session)."""
    pm = _build_pm()
    pos = pm.add_position(_rivn_position(qty=1552, remaining=1035))
    assert pos.stop_order_id == ""
    w, client = _make_watcher(pm)

    violations = await _drive_to_violation(w)

    assert len(violations) == 1
    client.submit_stop_order.assert_awaited_once()
    assert pos.stop_order_id == "emerg-oid-286", (
        "emergency-stop oid must be written back onto the tracked "
        "position so eod_recon (D230), D98 and the resubmitter see it"
    )
    # attach mirrors the REAL broker stop price (min(19.31*0.85,
    # 19.41*0.92) = 16.4135 — the value in today's log line).
    assert pos.stop_loss == pytest.approx(16.4135, abs=1e-4)


@pytest.mark.asyncio
async def test_emergency_stop_writeback_force_path_when_attach_refuses():
    """Belt-and-braces: even when attach_external_stop STILL refuses
    (here: remaining_qty also stale → under-cover refusal), the watcher
    must force-write the broker-confirmed oid directly — the stop is
    REAL; leaving stop_order_id empty lets the resubmitter arm a
    duplicate stop."""
    pm = _build_pm()
    # Both qty fields stale at 1552 while broker holds 1035 → the
    # attach path refuses (1035 under-covers 1552).
    pos = pm.add_position(_rivn_position(qty=1552, remaining=1552))
    w, client = _make_watcher(pm)

    await _drive_to_violation(w)

    client.submit_stop_order.assert_awaited_once()
    assert pos.stop_order_id == "emerg-oid-286", (
        "force write-back must record the confirmed-real broker stop "
        "even when the canonical attach path refuses"
    )
    assert pos.stop_loss == pytest.approx(16.4135, abs=1e-4)


@pytest.mark.asyncio
async def test_emergency_stop_writeback_force_path_when_attach_raises(
    monkeypatch,
):
    """The :578 exception branch: attach_external_stop RAISES (not just
    refuses) after a successful emergency-stop submit → the watcher must
    still force-write the broker-confirmed oid onto the position."""
    pm = _build_pm()
    pos = pm.add_position(_rivn_position(qty=1552, remaining=1035))
    w, client = _make_watcher(pm)

    def _boom(**_kw):
        raise RuntimeError("doc286 injected attach failure")

    monkeypatch.setattr(pm, "attach_external_stop", _boom)

    await _drive_to_violation(w)  # must not raise into the tick

    client.submit_stop_order.assert_awaited_once()
    assert pos.stop_order_id == "emerg-oid-286", (
        "attach raised but the broker stop is confirmed-real — the "
        "exception branch must force-write the oid"
    )
    assert pos.stop_loss == pytest.approx(16.4135, abs=1e-4)


@pytest.mark.asyncio
async def test_emergency_stop_writeback_absent_position_no_crash():
    """Position gone from the tracker (e.g. today's 10:48 phantom close
    wiped it) → the watcher still submits the emergency stop and the
    write-back is a guarded no-op, NEVER a crash of the safety net."""
    pm = _build_pm()  # RIVN not tracked
    w, client = _make_watcher(pm)

    violations = await _drive_to_violation(w)  # must not raise

    assert len(violations) == 1
    client.submit_stop_order.assert_awaited_once()
    assert not pm.has_position("RIVN")


@pytest.mark.asyncio
async def test_emergency_stop_no_position_manager_no_crash():
    """position_manager=None (pre-D313.v4 wiring) keeps working."""
    from src.monitoring.hedge_integrity_watcher import HedgeIntegrityWatcher

    client = MagicMock()
    client.get_positions = AsyncMock(return_value=[{
        "symbol": "RIVN", "qty": "1035",
        "avg_entry_price": "19.31", "current_price": "19.41",
    }])
    client.get_orders = AsyncMock(return_value=[])
    client.submit_stop_order = AsyncMock(
        return_value={"id": "emerg-oid-286", "status": "accepted"},
    )
    w = HedgeIntegrityWatcher(
        client=client, position_manager=None,
        poll_interval_sec=0.01, tolerance_sec=0.0,
    )
    violations = await _drive_to_violation(w)
    assert len(violations) == 1
    client.submit_stop_order.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════
# attach_external_stop currently-held-qty semantics (the enabler)
# ═══════════════════════════════════════════════════════════════


def test_attach_external_stop_accepts_broker_true_qty_after_partial():
    """The direct unit pin of the 10:04:53 refusal: a stop sized for
    the currently-held 1035 shares must attach even though the stale
    entry qty reads 1552. FAILS on the old exact-match-vs-.qty check."""
    pm = _build_pm()
    pos = pm.add_position(_rivn_position(qty=1552, remaining=1035))
    ok = pm.attach_external_stop(
        ticker="RIVN", stop_order_id="emerg-oid-286", stop_price=16.4135,
        qty=1035,
    )
    assert ok is True
    assert pos.stop_order_id == "emerg-oid-286"
    assert pos.stop_loss == pytest.approx(16.4135, abs=1e-4)


def test_attach_external_stop_still_refuses_under_coverage():
    """Conservative direction preserved: a stop covering FEWER shares
    than currently held is under-protection and must still be refused
    (the Bug AM defense against attaching a wrong-position stop)."""
    pm = _build_pm()
    pos = pm.add_position(_rivn_position(qty=1552, remaining=1035))
    ok = pm.attach_external_stop(
        ticker="RIVN", stop_order_id="wrong-stop", stop_price=16.41,
        qty=500,  # under-covers the held 1035
    )
    assert ok is False
    assert pos.stop_order_id == ""


def test_attach_external_stop_accepts_over_coverage():
    """A stop for MORE shares than held is over-protection — acceptable,
    same contract as _find_matching_protective_stop (o_qty >= qty)."""
    pm = _build_pm()
    pos = pm.add_position(_rivn_position(qty=1552, remaining=1035))
    ok = pm.attach_external_stop(
        ticker="RIVN", stop_order_id="big-stop", stop_price=16.41,
        qty=1552,
    )
    assert ok is True
    assert pos.stop_order_id == "big-stop"
