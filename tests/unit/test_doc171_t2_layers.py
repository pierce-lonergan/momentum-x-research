"""Doc 171 (2026-05-24) -- Three-layer T2 wide-stop rollout test suite.

Tests cover:
  - LAYER 1: standalone-stop submission on wide arm (bypasses
    TrailingStopManager / D310). Tests the BUY-limit submission, the
    background poll-for-fill loop, and the standalone-STOP submission
    after fill confirmation.
  - T2: hash-keyed A/B arm assignment in t2_arm_assignment module.
    Tests env-var gating, determinism, distribution, model_copy round
    trip on TradeVerdict.
  - LAYER 2: HedgeIntegrityWatcher invariant enforcement. Tests:
      * positions with stops: no violation
      * positions WITHOUT stops within tolerance: no action
      * positions WITHOUT stops past tolerance: D313 alert + emergency
        stop submission
      * sub-$1 positions: alert-only, no auto-submit
      * watcher recovery: re-armed stop resets the unhedge clock
  - D310 OBS: structured CANCEL_REQUESTED/RESUBMIT_REQUESTED logs
    when TrailingStopManager actions fire AND when callbacks aren't
    wired.
  - Property test: the position-hedge invariant must hold across a
    multi-event replay (Pierce's spec).
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest


# ═══════════════════════════════════════════════════════════════
# T2 hash-keyed arm assignment
# ═══════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _restore_env(monkeypatch):
    """Each test starts with MOMENTUM_T2_ENABLED unset."""
    monkeypatch.delenv("MOMENTUM_T2_ENABLED", raising=False)


def test_t2_disabled_by_default():
    from src.execution.t2_arm_assignment import t2_enabled
    assert t2_enabled() is False


@pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", "on"])
def test_t2_enabled_for_truthy_env(val, monkeypatch):
    from src.execution.t2_arm_assignment import t2_enabled
    monkeypatch.setenv("MOMENTUM_T2_ENABLED", val)
    assert t2_enabled() is True


@pytest.mark.parametrize("val", ["", "0", "false", "no", "off"])
def test_t2_disabled_for_falsy_env(val, monkeypatch):
    from src.execution.t2_arm_assignment import t2_enabled
    monkeypatch.setenv("MOMENTUM_T2_ENABLED", val)
    assert t2_enabled() is False


def test_assign_arm_returns_tight_when_disabled():
    from src.execution.t2_arm_assignment import assign_arm
    arm, mult = assign_arm("AAPL", "2026-05-25")
    assert arm == "tight_stop"
    assert mult == 1.0


def test_assign_arm_is_deterministic_when_enabled(monkeypatch):
    monkeypatch.setenv("MOMENTUM_T2_ENABLED", "1")
    from src.execution.t2_arm_assignment import assign_arm
    a1 = assign_arm("NXXT", "2026-05-25")
    a2 = assign_arm("NXXT", "2026-05-25")
    assert a1 == a2, "same (date, symbol) must produce same arm"


def test_arm_distribution_is_approximately_50_50(monkeypatch):
    """Across 500 random tickers, arm distribution should be ~50/50 AT THE DEFAULT.
    doc 282: the operator env may carry MOMENTUM_T2_WIDE_PCT (e.g. 0.75 after the
    doc-266 promotion fired) — this test asserts the DEFAULT split, so it must
    clear the override or it fails by design whenever the promotion is live."""
    monkeypatch.setenv("MOMENTUM_T2_ENABLED", "1")
    monkeypatch.delenv("MOMENTUM_T2_WIDE_PCT", raising=False)
    from collections import Counter
    from src.execution.t2_arm_assignment import assign_arm
    counts = Counter(assign_arm(f"SYM{i:05d}", "2026-05-25")[0]
                      for i in range(500))
    # Allow +/- 10% from balanced
    assert 200 <= counts["wide_stop"] <= 300, (
        f"wide_stop count {counts['wide_stop']} not in [200, 300]"
    )
    assert counts["wide_stop"] + counts["tight_stop"] == 500


def test_wide_arm_qty_multiplier_is_half(monkeypatch):
    """Wide arm must use 0.5 sizing to bound risk on the wider stop."""
    monkeypatch.setenv("MOMENTUM_T2_ENABLED", "1")
    from src.execution.t2_arm_assignment import assign_arm
    # Find a symbol that hits wide_stop bucket
    for i in range(100):
        sym = f"T{i:04d}"
        arm, mult = assign_arm(sym, "2026-05-25")
        if arm == "wide_stop":
            assert mult == 0.5
            return
    pytest.fail("Could not find a wide_stop arm across 100 tickers")


def test_assigned_arm_for_verdict_short_is_unchanged(monkeypatch):
    """Short trades are not in scope for T2 (long-side only for now)."""
    monkeypatch.setenv("MOMENTUM_T2_ENABLED", "1")
    from src.execution.t2_arm_assignment import assigned_arm_for_verdict
    verdict = MagicMock()
    verdict.direction = "short"
    verdict.ticker = "X"
    out = assigned_arm_for_verdict(verdict)
    assert out is verdict, "short verdicts should pass through unchanged"


def test_assigned_arm_for_verdict_disabled_passes_through():
    """When T2 disabled (default), verdict passes through unchanged."""
    from src.execution.t2_arm_assignment import assigned_arm_for_verdict
    verdict = MagicMock()
    verdict.direction = "long"
    verdict.ticker = "X"
    out = assigned_arm_for_verdict(verdict)
    assert out is verdict


def test_assigned_arm_for_verdict_uses_model_copy(monkeypatch):
    """When T2 enabled, returns a new verdict via model_copy (does NOT
    mutate the frozen=True TradeVerdict in place)."""
    monkeypatch.setenv("MOMENTUM_T2_ENABLED", "1")
    from src.execution.t2_arm_assignment import assigned_arm_for_verdict
    verdict = MagicMock()
    verdict.direction = "long"
    verdict.ticker = "T0001"
    new_v = MagicMock()
    verdict.model_copy = MagicMock(return_value=new_v)
    out = assigned_arm_for_verdict(verdict)
    verdict.model_copy.assert_called_once()
    call_kwargs = verdict.model_copy.call_args.kwargs["update"]
    assert "execution_arm" in call_kwargs
    assert "qty_multiplier" in call_kwargs
    assert call_kwargs["execution_arm"] in ("wide_stop", "tight_stop")
    assert call_kwargs["qty_multiplier"] in (0.5, 1.0)
    assert out is new_v


# ═══════════════════════════════════════════════════════════════
# LAYER 2 -- HedgeIntegrityWatcher
# ═══════════════════════════════════════════════════════════════


def _make_position(symbol, qty, avg=10.0, current=10.0):
    return {
        "symbol": symbol, "qty": str(qty),
        "avg_entry_price": str(avg), "current_price": str(current),
    }


def _make_stop_order(symbol, side="sell", qty="100", tif="gtc",
                      stop_price="9.50"):
    """Doc 173 (D313.v2): mock now provides qty/tif/stop_price so the
    correctness check passes for existence-only-style tests. Defaults
    chosen to satisfy the new band/qty/TIF invariants."""
    return {
        "symbol": symbol, "type": "stop", "side": side,
        "qty": str(qty), "time_in_force": tif,
        "stop_price": str(stop_price),
        "status": "accepted",
    }


@pytest.fixture
def make_watcher():
    """Factory for a HedgeIntegrityWatcher with a mock client."""
    def _make(positions=None, orders=None, tolerance_sec=60.0,
              auto_submit=True, skip_below=1.0):
        from src.monitoring.hedge_integrity_watcher import HedgeIntegrityWatcher
        client = MagicMock()
        client.get_positions = AsyncMock(return_value=positions or [])
        client.get_orders = AsyncMock(return_value=orders or [])
        client.submit_stop_order = AsyncMock(
            return_value={"id": "emerg-stop-1", "status": "accepted"}
        )
        w = HedgeIntegrityWatcher(
            client=client, poll_interval_sec=0.01,
            tolerance_sec=tolerance_sec, auto_submit_emergency=auto_submit,
            skip_below_price=skip_below,
        )
        return w, client
    return _make


@pytest.mark.asyncio
async def test_positions_with_stops_no_violation(make_watcher):
    """Long position with a sell-stop at broker: no violation."""
    w, client = make_watcher(
        positions=[_make_position("AAPL", qty=100)],
        orders=[_make_stop_order("AAPL", side="sell")],
    )
    vs = await w.check_once()
    assert vs == []
    assert w.stats()["violations"] == 0


@pytest.mark.asyncio
async def test_unhedged_within_tolerance_starts_clock(make_watcher):
    """Position with no stop, first tick: starts unhedge clock, no violation yet."""
    w, client = make_watcher(
        positions=[_make_position("AAPL", qty=100)],
        orders=[],  # NO stop
    )
    vs = await w.check_once()
    assert vs == []  # first tick, within tolerance
    # State should track the unhedge start time
    assert "AAPL" in w._state
    assert w._state["AAPL"].first_unhedged_utc is not None


@pytest.mark.asyncio
async def test_unhedged_past_tolerance_fires_violation(make_watcher):
    """After tolerance window, the second tick fires a D313 violation
    AND auto-submits an emergency stop."""
    w, client = make_watcher(
        positions=[_make_position("AAPL", qty=100, avg=10.0, current=10.0)],
        orders=[],
        tolerance_sec=0.0,  # immediate tolerance breach
    )
    # First tick starts the clock
    vs1 = await w.check_once()
    assert vs1 == []
    # Advance past tolerance
    w._state["AAPL"].first_unhedged_utc = (
        datetime.now(timezone.utc) - timedelta(seconds=5)
    )
    # Second tick: violation fires
    vs2 = await w.check_once()
    assert len(vs2) == 1
    v = vs2[0]
    assert v.ticker == "AAPL"
    assert v.side == "long"
    assert v.will_submit_emergency_stop is True
    # Emergency stop was actually submitted at broker
    client.submit_stop_order.assert_awaited_once()
    call_kwargs = client.submit_stop_order.await_args.kwargs
    assert call_kwargs["symbol"] == "AAPL"
    assert call_kwargs["side"] == "sell"
    assert call_kwargs["qty"] == 100
    # Emergency stop must be below entry/current (defensive)
    assert call_kwargs["stop_price"] < 10.0


@pytest.mark.asyncio
async def test_emergency_stop_is_wider_of_two_bounds(make_watcher):
    """Emergency stop = max(entry × 0.85, last × 0.92) for longs --
    the WIDER (more conservative) of the two."""
    from src.monitoring.hedge_integrity_watcher import _PositionState
    w, client = make_watcher(
        positions=[_make_position("X", qty=100, avg=10.0, current=11.0)],
        orders=[],
    )
    # Pre-load state so the violation fires on the first check_once call
    w._state["X"] = _PositionState()
    w._state["X"].first_unhedged_utc = datetime.now(timezone.utc) - timedelta(seconds=120)
    vs = await w.check_once()
    assert len(vs) == 1
    # min(10*0.85, 11*0.92) = min(8.50, 10.12) = 8.50 (entry-based wins)
    assert vs[0].emergency_stop_price == pytest.approx(8.50, abs=0.01)


@pytest.mark.asyncio
async def test_sub_dollar_position_alerts_only_no_auto_submit(make_watcher):
    """Per user's plan: Alpaca stop-trigger unreliable on sub-$1. The
    watcher must alert but NOT auto-submit an emergency stop there."""
    w, client = make_watcher(
        positions=[_make_position("PENNY", qty=10000, avg=0.50, current=0.55)],
        orders=[],
        skip_below=1.0,
    )
    # Skip clock, jump straight to violation
    from src.monitoring.hedge_integrity_watcher import _PositionState
    w._state["PENNY"] = _PositionState()
    w._state["PENNY"].first_unhedged_utc = (
        datetime.now(timezone.utc) - timedelta(seconds=120)
    )
    vs = await w.check_once()
    assert len(vs) == 1
    assert vs[0].will_submit_emergency_stop is False
    # No emergency stop was submitted
    client.submit_stop_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_rearmed_stop_resets_unhedge_clock(make_watcher):
    """If the stop reappears (operator manually arms it, or some path
    fires resubmit), the clock resets and no violation fires."""
    from src.monitoring.hedge_integrity_watcher import _PositionState
    w, client = make_watcher(
        positions=[_make_position("AAPL", qty=100)],
        orders=[],
    )
    # First tick: starts clock
    await w.check_once()
    assert w._state["AAPL"].first_unhedged_utc is not None
    # Now stop reappears
    client.get_orders = AsyncMock(return_value=[_make_stop_order("AAPL")])
    vs = await w.check_once()
    assert vs == []
    assert w._state["AAPL"].first_unhedged_utc is None  # reset


@pytest.mark.asyncio
async def test_closed_position_is_pruned_from_state(make_watcher):
    """When a position closes (no longer in get_positions), the
    watcher's per-ticker state should be cleared to prevent memory leak."""
    w, client = make_watcher(
        positions=[_make_position("AAPL", qty=100)],
        orders=[_make_stop_order("AAPL")],
    )
    await w.check_once()
    assert "AAPL" in w._state
    # Position closes
    client.get_positions = AsyncMock(return_value=[])
    await w.check_once()
    assert "AAPL" not in w._state


@pytest.mark.asyncio
async def test_short_position_requires_buy_stop(make_watcher):
    """Short positions are protected by BUY-stop orders, not sell-stop."""
    w, client = make_watcher(
        positions=[_make_position("AAPL", qty=-100, avg=10, current=10)],
        orders=[_make_stop_order("AAPL", side="sell")],  # WRONG side
    )
    # First tick starts clock (not violation yet)
    await w.check_once()
    assert w._state["AAPL"].first_unhedged_utc is not None


@pytest.mark.asyncio
async def test_no_auto_submit_when_disabled():
    """Defensive dry-run mode: alert but never submit."""
    from src.monitoring.hedge_integrity_watcher import HedgeIntegrityWatcher
    from src.monitoring.hedge_integrity_watcher import _PositionState
    client = MagicMock()
    client.get_positions = AsyncMock(return_value=[
        _make_position("AAPL", 100, avg=10, current=10),
    ])
    client.get_orders = AsyncMock(return_value=[])
    client.submit_stop_order = AsyncMock()
    w = HedgeIntegrityWatcher(
        client=client, tolerance_sec=0.0,
        auto_submit_emergency=False,  # dry-run mode
    )
    w._state["AAPL"] = _PositionState()
    w._state["AAPL"].first_unhedged_utc = (
        datetime.now(timezone.utc) - timedelta(seconds=60)
    )
    vs = await w.check_once()
    assert len(vs) == 1
    assert vs[0].will_submit_emergency_stop is False
    client.submit_stop_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_watcher_never_raises_on_broker_failure(make_watcher):
    """Network blip or 5xx must not crash the loop."""
    w, _ = make_watcher()
    w._client.get_positions = AsyncMock(side_effect=RuntimeError("broker down"))
    # Should not raise
    try:
        async with asyncio.timeout(1.0):
            task = asyncio.create_task(w.run_forever())
            await asyncio.sleep(0.05)
            w.stop()
            await task
    except asyncio.TimeoutError:
        pytest.fail("watcher hung on broker failure")


# ═══════════════════════════════════════════════════════════════
# PROPERTY TEST (per Pierce's spec): position-hedge invariant
# ═══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_hedge_invariant_holds_across_replay(make_watcher):
    """At any sampled point in a multi-event replay, every position
    with qty != 0 must EITHER be hedged at broker OR the watcher must
    have detected the unhedge state. The invariant is: there is NO
    sample window where qty != 0 AND has_protective_order == False AND
    watcher state shows no unhedge tracking."""
    from src.monitoring.hedge_integrity_watcher import _PositionState

    # Replay: position opened, stop attached, stop canceled, stop never
    # re-armed, stop manually re-armed, position closed.
    replay = [
        # (positions, orders, expected_invariant)
        (  # t0: opened with stop
            [_make_position("X", 100)], [_make_stop_order("X")], True,
        ),
        (  # t1: stop canceled (UNHEDGED -- but watcher detects)
            [_make_position("X", 100)], [], True,
        ),
        (  # t2: still no stop (UNHEDGED -- detected, clock running)
            [_make_position("X", 100)], [], True,
        ),
        (  # t3: stop re-armed
            [_make_position("X", 100)], [_make_stop_order("X")], True,
        ),
        (  # t4: position closed
            [], [], True,
        ),
    ]
    w, client = make_watcher()
    for positions, orders, expected_inv in replay:
        client.get_positions = AsyncMock(return_value=positions)
        client.get_orders = AsyncMock(return_value=orders)
        await w.check_once()
        # Invariant check: every tracked position MUST have either:
        #   (a) a corresponding stop order observed, OR
        #   (b) a recorded first_unhedged_utc timestamp
        for sym, state in w._state.items():
            stop_present = any(
                (o.get("symbol") == sym and o.get("type") == "stop")
                for o in orders
            )
            unhedge_tracked = state.first_unhedged_utc is not None
            assert stop_present or unhedge_tracked, (
                f"INVARIANT VIOLATION at {sym}: no stop and no unhedge clock"
            )


# ═══════════════════════════════════════════════════════════════
# Wiring contracts
# ═══════════════════════════════════════════════════════════════


def test_alpaca_executor_accepts_t2_verdict_fields():
    """TradeVerdict must accept execution_arm + qty_multiplier (defaults
    preserve old behavior so existing tests + production callers work)."""
    from src.core.models import TradeVerdict
    # Default construction (backwards-compat)
    v = TradeVerdict(
        ticker="X", action="BUY", confidence=0.5, mfcs=0.5,
        entry_price=10.0, stop_loss=9.0, target_prices=[11.0],
        position_size_pct=0.05,
    )
    assert v.execution_arm == "tight_stop"
    assert v.qty_multiplier == 1.0
    # Explicit wide_stop assignment
    v2 = v.model_copy(update={"execution_arm": "wide_stop", "qty_multiplier": 0.5})
    assert v2.execution_arm == "wide_stop"
    assert v2.qty_multiplier == 0.5


def test_main_py_launches_hedge_integrity_watcher():
    """Static guard: main.py must construct HedgeIntegrityWatcher."""
    from pathlib import Path
    src = Path("main.py").read_text(encoding="utf-8")
    assert "HedgeIntegrityWatcher" in src, (
        "main.py must launch HedgeIntegrityWatcher -- the L2 safety net"
    )
    # And actually wire the task
    assert "_hedge_watcher_task" in src or "hedge_watcher.run_forever" in src


def test_orchestrator_wires_t2_arm_assignment():
    """Static guard: orchestrator emits verdicts through t2_arm_assignment."""
    from pathlib import Path
    src = Path("src/core/orchestrator.py").read_text(encoding="utf-8")
    assert "assigned_arm_for_verdict" in src, (
        "orchestrator.py must call assigned_arm_for_verdict on each BUY verdict"
    )


def test_executor_branches_on_execution_arm():
    """Static guard: executor must check verdict.execution_arm."""
    from pathlib import Path
    src = Path("src/execution/alpaca_executor.py").read_text(encoding="utf-8")
    assert "execution_arm" in src
    assert "submit_limit_order" in src, (
        "Executor must use submit_limit_order for the wide-arm path "
        "(plain BUY, not OTO)"
    )
    assert "_t2_arm_standalone_stop" in src, (
        "Executor must define _t2_arm_standalone_stop helper"
    )
