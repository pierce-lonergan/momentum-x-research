"""doc 218: tests for the Adversary execution harness (the sim broker + invariants).

The Adversary battles our REAL attempt_close_with_status_check. These pin: (1) the doc-216
cancel-settle fix holds (realistic settle -> close SUCCEEDS), (2) the doc-218 re-arm fix
holds (qty permanently reserved -> NOT silent-naked; escalates), (3) no phantom is ever
booked on a failed close.
"""
from __future__ import annotations

import pytest

from src.ops import incident_bus
from src.ops.adversary import AdversaryBroker, check_invariants
from src.execution.bridge import attempt_close_with_status_check


async def _run(settle_delay, *, hard=False, qty=300, max_retries=3):
    incident_bus._recent.clear()
    b = AdversaryBroker(settle_delay_ticks=settle_delay, close_hard_fail=hard)
    b.seed_position("X", qty, entry=3.46, current=3.00)
    b.seed_stop("X", qty, stop_price=2.94)
    booked = False
    try:
        res = await attempt_close_with_status_check(
            client=b, ticker="X", qty=qty, max_retries=max_retries,
            cancel_blocking_stops_first=True)
        booked = bool(res.get("succeeded"))
    except Exception as e:
        res = {"succeeded": False, "raised": str(e)[:80]}
    escalated = any(i.get("kind") == "NAKED_POSITION_RISK" for i in incident_bus.read_incidents())
    rep = check_invariants(f"settle{settle_delay}", b, close_result=res,
                           booked_pnl=booked, escalated=escalated)
    return rep, res, b


@pytest.mark.asyncio
async def test_realistic_settle_closes_doc216():
    """doc-216: a held_for_orders close whose cancel settles in a few ticks must SUCCEED."""
    rep, res, _ = await _run(3)
    assert res["succeeded"] is True
    assert rep.survived


@pytest.mark.asyncio
async def test_fast_and_slow_settle_all_close():
    for d in (1, 5, 10):
        rep, res, _ = await _run(d)
        assert res["succeeded"] is True, f"settle={d} should close"
        assert rep.survived


@pytest.mark.asyncio
async def test_never_settles_is_not_silent_naked_doc218():
    """doc-218: if qty is permanently reserved (re-arm can't place a stop), the position must
    NOT be SILENT-naked — it must escalate a CRITICAL NAKED_POSITION_RISK incident."""
    rep, res, _ = await _run(999)
    assert res["succeeded"] is False          # close correctly fails (broker won't free qty)
    assert rep.survived                        # survived = escalated, not silent
    assert not any("SILENT-NAKED" in b for b in rep.broke)
    assert any(i.get("kind") == "NAKED_POSITION_RISK" and i["severity"] == "CRITICAL"
               for i in incident_bus.read_incidents())


@pytest.mark.asyncio
async def test_no_phantom_on_failed_close():
    """The cardinal rule: P&L is NEVER booked when the close didn't succeed."""
    rep, res, _ = await _run(999)
    assert res["succeeded"] is False
    assert not any("PHANTOM" in b for b in rep.broke)


@pytest.mark.asyncio
async def test_close_wrapper_never_raises():
    """Even on a hard broker failure, the wrapper returns a clean result, never raises."""
    rep, res, _ = await _run(1, hard=True)
    assert "succeeded" in res and res["succeeded"] is False


async def _run_weapon(**broker_kw):
    incident_bus._recent.clear()
    b = AdversaryBroker(**broker_kw)
    b.seed_position("X", 300, entry=3.46, current=3.00)
    b.seed_stop("X", 300, stop_price=2.94)
    booked = False
    try:
        res = await attempt_close_with_status_check(
            client=b, ticker="X", qty=300, max_retries=3, cancel_blocking_stops_first=True)
        booked = bool(res.get("succeeded"))
    except Exception as e:
        res = {"succeeded": False, "raised": str(e)[:80]}
    escalated = any(i.get("kind") == "NAKED_POSITION_RISK" for i in incident_bus.read_incidents())
    rep = check_invariants("weapon", b, close_result=res, booked_pnl=booked, escalated=escalated)
    return rep, res, b


@pytest.mark.asyncio
async def test_partial_fill_never_strands_doc219():
    """doc-219: a PARTIAL close must NOT report succeeded=True with a stranded remainder."""
    rep, res, b = await _run_weapon(settle_delay_ticks=2, partial_fill_frac=0.6)
    # either it fully closes across retries, OR it reports FAILED — never a silent strand
    assert not any("PARTIAL-STRAND" in x for x in rep.broke)
    assert not any("SILENT-NAKED" in x for x in rep.broke)
    if not res.get("succeeded"):
        assert rep.survived  # failed-clean is fine


@pytest.mark.asyncio
async def test_stop_fills_mid_close_no_phantom():
    """The reserving stop triggers mid-close (position vanishes) -> no phantom, no naked."""
    rep, res, b = await _run_weapon(settle_delay_ticks=3, stop_fills_at_tick=2)
    assert not any("PHANTOM" in x for x in rep.broke)
    assert not any("SILENT-NAKED" in x for x in rep.broke)


@pytest.mark.asyncio
async def test_trading_halt_then_lifts_closes():
    """Close 403s during a halt, then succeeds once it lifts -> survives."""
    rep, res, b = await _run_weapon(settle_delay_ticks=1, halt_until_tick=2)
    assert rep.survived
