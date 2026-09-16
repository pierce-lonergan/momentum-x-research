"""Tests for mx-arena/arena/sim_alpaca_client.py.

Block C.1 of the strategy-harness session. Pins:
  1. SimAlpacaClient routes order submissions to SimExchange
  2. FailureInjector queries fire BEFORE any state mutation
  3. AR-scenario fixture replays through the wired path: an injected
     403 raises an exception with .response.text containing the
     production-shape body (so Bug AR's str(e) extraction would
     pull the right body)
  4. Halt switch parity: same env-aware check as production AlpacaDataClient
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "mx-arena"))

from arena.clock import SimClock, ClockMode  # noqa: E402
from arena.exchange import SimExchange  # noqa: E402
from arena.failure_injector import (  # noqa: E402
    FailureInjector, FailureRule, InjectedFailure,
    lidr_tue_2026_04_28_qty_conflict,
)
from arena.sim_alpaca_client import SimAlpacaClient  # noqa: E402


def _make_client(failure_injector=None) -> SimAlpacaClient:
    clock = SimClock(
        start=datetime.fromisoformat("2026-04-28T13:30:00+00:00"),
        end=datetime.fromisoformat("2026-04-28T20:00:00+00:00"),
        mode=ClockMode.REPLAY,
    )
    sim = SimExchange(clock=clock)
    return SimAlpacaClient(sim_exchange=sim, failure_injector=failure_injector)


@pytest.fixture(autouse=True)
def _clear_halt_env(monkeypatch):
    monkeypatch.delenv("MOMENTUM_HALT_NEW_ENTRIES", raising=False)


# ── Basic submission ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_submit_oto_order_routes_to_sim_exchange():
    client = _make_client()
    resp = await client.submit_oto_order(
        symbol="LIDR", qty=5264, limit_price=2.42, stop_loss=2.10,
    )
    assert resp["symbol"] == "LIDR"
    assert resp["status"] != "halted_by_operator"
    # SimExchange should now have the order recorded
    orders = await client.get_orders(status="all")
    assert any(o["symbol"] == "LIDR" for o in orders)


# ── Halt switch parity ────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("halt_value", ["1", "true", "yes", "on"])
async def test_halt_env_blocks_submission(monkeypatch, halt_value):
    monkeypatch.setenv("MOMENTUM_HALT_NEW_ENTRIES", halt_value)
    client = _make_client()
    resp = await client.submit_oto_order(
        symbol="LIDR", qty=5264, limit_price=2.42, stop_loss=2.10,
    )
    assert resp["status"] == "halted_by_operator"
    assert resp["halt_reason"] == "MOMENTUM_HALT_NEW_ENTRIES"
    # No order should have been recorded in SimExchange
    orders = await client.get_orders(status="all")
    assert not any(o["symbol"] == "LIDR" for o in orders)


# ── FailureInjector AR scenario ───────────────────────────────────


@pytest.mark.asyncio
async def test_ar_scenario_fixture_raises_with_response_body():
    """Inject the LIDR 2026-04-28 10:00:38 ET Bug AR fixture; submit a
    close on LIDR within the window. The sim client must raise an
    exception whose .response.text contains the production-shape body
    that Bug AR's e.response.text extraction would read."""
    inj = FailureInjector([lidr_tue_2026_04_28_qty_conflict()])
    # First create a position so close_position has something to close
    client = _make_client(failure_injector=inj)
    await client.submit_oto_order(
        symbol="LIDR", qty=5264, limit_price=2.42, stop_loss=2.10,
    )
    # Force a fill via the SimExchange machinery
    # (the real exchange would fill on next bar; for this test we
    # don't need a fill — close_position will raise before checking).

    # Wind the FailureInjector's clock comparison: rule fires within
    # the window 14:00:00Z to 14:01:30Z.
    # SimAlpacaClient uses datetime.now(UTC). For test, monkeypatch
    # so the close happens "in window."
    with patch("arena.sim_alpaca_client.datetime") as mock_dt:
        mock_dt.now.return_value = datetime.fromisoformat("2026-04-28T14:00:38+00:00")
        mock_dt.timezone = timezone
        with pytest.raises(Exception) as exc_info:
            await client.close_position("LIDR")
    err = exc_info.value
    assert hasattr(err, "response"), (
        "FailureInjector raised an exception without .response — "
        "Bug AR's e.response.text extraction can't read the body"
    )
    body = err.response.text
    # The body MUST contain the substrings the Bug AR fix matches against
    for needle in ("insufficient qty available", "40310000", "available: 0"):
        assert needle.lower() in body.lower(), (
            f"injected body missing {needle!r} — Bug AR substring match would not fire"
        )


@pytest.mark.asyncio
async def test_no_failure_rule_means_normal_path():
    """With an empty injector, close_position runs normally (raises
    only if no position exists, which it doesn't here without a fill)."""
    client = _make_client()
    with pytest.raises(RuntimeError):  # no position
        await client.close_position("LIDR")
