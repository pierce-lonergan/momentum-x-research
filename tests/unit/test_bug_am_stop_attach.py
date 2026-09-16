"""Bug AM tests: PositionManager.attach_external_stop + auto-discovery.

Pins today's LIDR scenario as a regression guard:
- Operator submitted manual GTC stop @ $2.10 at 08:22 ET
- D56 sync ran fresh-start path, set COMPUTED DEFAULT $2.29 stop_loss
  with stop_order_id=""
- D230 RECON_WARN fired every 30s for the entire trading day
- Position bled to -$1316 unrealized while the tracker had wrong stop info

Bug AM provides:
  1. attach_external_stop() — operator/recovery API to register a
     known broker stop with the tracker
  2. Auto-discovery in sync_from_broker — when broker_orders is passed,
     scan for matching sell-stop orders and auto-attach

Aggressive testing per Tier 3 mandate.

See docs/research-log/52_bug_am_stop_attach.md.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from src.execution.position_manager import PositionManager


def _build_pm() -> PositionManager:
    """Test factory — minimal config so PositionManager init succeeds."""
    cfg = MagicMock()
    cfg.daily_loss_limit_pct = 0.10
    cfg.max_positions = 8
    cfg.stop_loss_pct = 0.055
    cfg.max_position_size_pct = 0.15
    cfg.tier1_max_concurrent = 2
    cfg.tier2_max_concurrent = 4
    cfg.tier3_max_concurrent = 6
    cfg.tier4_max_concurrent = 8
    return PositionManager(config=cfg, starting_equity=100_000.0)


def _broker_position_lidr() -> dict:
    return {
        "symbol": "LIDR", "qty": "5264", "side": "long",
        "avg_entry_price": "2.42", "current_price": "2.20",
        "unrealized_pl": "-1158",
    }


def _broker_stop_lidr_at_2_10() -> dict:
    """Today's actual operator-submitted stop."""
    return {
        "id": "859be64d-718e-4f6d-a09a-932d03a79916",
        "symbol": "LIDR", "side": "sell", "qty": "5264",
        "type": "stop", "stop_price": "2.10",
        "status": "new", "limit_price": None,
    }


# ── attach_external_stop direct tests ──────────────────────────────


def test_attach_external_stop_updates_tracker(caplog):
    """The canonical operator action: register the manual stop with
    the tracker, see stop_order_id + stop_loss flip from
    COMPUTED-DEFAULT to broker-confirmed."""
    pm = _build_pm()
    # Sync fresh (no broker_orders → COMPUTED DEFAULT)
    pm.sync_from_broker([_broker_position_lidr()])
    pos = pm._positions["LIDR"]
    assert pos.stop_order_id == "", "fresh sync has no broker stop attached"
    pre_stop = pos.stop_loss
    assert pre_stop != 2.10, "computed default should not coincidentally be 2.10"

    # Operator attaches the stop after-the-fact
    with caplog.at_level(logging.INFO):
        result = pm.attach_external_stop(
            ticker="LIDR",
            stop_order_id="859be64d-718e-4f6d-a09a-932d03a79916",
            stop_price=2.10,
        )
    assert result is True
    assert pos.stop_order_id == "859be64d-718e-4f6d-a09a-932d03a79916"
    assert pos.stop_loss == 2.10
    assert any("D273 STOP_ATTACHED" in r.message for r in caplog.records), (
        "D273 must log so operator sees the attach succeeded"
    )


def test_attach_external_stop_qty_sanity_check(caplog):
    """If caller passes qty, it must match position.qty (defense
    against attaching a stop sized for a different position)."""
    pm = _build_pm()
    pm.sync_from_broker([_broker_position_lidr()])
    with caplog.at_level(logging.WARNING):
        result = pm.attach_external_stop(
            ticker="LIDR", stop_order_id="x", stop_price=2.10,
            qty=100,  # WRONG — position has qty=5264
        )
    assert result is False
    assert pm._positions["LIDR"].stop_order_id == "", "stop must NOT be attached on qty mismatch"


@pytest.mark.parametrize("ticker,oid,price,qty,description", [
    ("UNKNOWN", "x", 1.0, None, "ticker not in tracker"),
    ("LIDR", "", 1.0, None, "empty stop_order_id"),
    ("LIDR", "x", 0.0, None, "zero stop_price"),
    ("LIDR", "x", -1.0, None, "negative stop_price"),
])
def test_attach_external_stop_validation_failures(ticker, oid, price, qty, description):
    """All input validation paths must return False, NOT raise."""
    pm = _build_pm()
    pm.sync_from_broker([_broker_position_lidr()])
    result = pm.attach_external_stop(
        ticker=ticker, stop_order_id=oid, stop_price=price, qty=qty,
    )
    assert result is False, f"validation failure should return False: {description}"


# ── Auto-discovery tests via sync_from_broker(broker_orders=...) ──


def test_sync_from_broker_auto_attaches_matching_stop(caplog):
    """Today's LIDR scenario, fixed: when broker_orders are passed,
    sync_from_broker auto-attaches the matching sell-stop. The
    tracker reflects reality from the start. D230 RECON_WARN
    should NOT fire for this position."""
    pm = _build_pm()
    with caplog.at_level(logging.INFO):
        pm.sync_from_broker(
            [_broker_position_lidr()],
            broker_orders=[_broker_stop_lidr_at_2_10()],
        )
    pos = pm._positions["LIDR"]
    assert pos.stop_order_id == "859be64d-718e-4f6d-a09a-932d03a79916"
    assert pos.stop_loss == 2.10  # from broker, not the COMPUTED DEFAULT $2.29
    # Log line should distinguish auto-attach from COMPUTED-DEFAULT
    assert any(
        "Bug AM auto-attach" in r.message
        for r in caplog.records
    ), "D56 log must say 'Bug AM auto-attach' when match found"


def test_sync_from_broker_no_orders_falls_through_to_computed_default(caplog):
    """When broker_orders is None or empty, fall through to the
    pre-Bug-AM COMPUTED DEFAULT behavior (preserves Wed 2026-04-22
    Bug C honesty about no-broker-stop state)."""
    pm = _build_pm()
    with caplog.at_level(logging.INFO):
        pm.sync_from_broker([_broker_position_lidr()], broker_orders=None)
    pos = pm._positions["LIDR"]
    assert pos.stop_order_id == ""
    assert any("COMPUTED DEFAULT" in r.message for r in caplog.records), (
        "no-orders path must still log COMPUTED DEFAULT for honesty"
    )


def test_sync_from_broker_skips_non_matching_stops():
    """Auto-attach must NOT pick up: wrong-symbol stops, buy stops,
    take-profit limits, terminal-status stops, smaller-qty stops."""
    pm = _build_pm()
    irrelevant_orders = [
        # Wrong symbol
        {"id": "x1", "symbol": "AAPL", "side": "sell", "qty": "5264",
         "type": "stop", "stop_price": "100.00", "status": "new"},
        # Buy stop (covering a short — NOT relevant for long)
        {"id": "x2", "symbol": "LIDR", "side": "buy", "qty": "5264",
         "type": "stop", "stop_price": "3.00", "status": "new"},
        # Take-profit limit (not a stop)
        {"id": "x3", "symbol": "LIDR", "side": "sell", "qty": "5264",
         "type": "limit", "limit_price": "2.60", "stop_price": None, "status": "new"},
        # Stop with qty too small (only covers part of position)
        {"id": "x4", "symbol": "LIDR", "side": "sell", "qty": "1000",
         "type": "stop", "stop_price": "2.10", "status": "new"},
        # Terminal status
        {"id": "x5", "symbol": "LIDR", "side": "sell", "qty": "5264",
         "type": "stop", "stop_price": "2.05", "status": "canceled"},
    ]
    pm.sync_from_broker([_broker_position_lidr()], broker_orders=irrelevant_orders)
    pos = pm._positions["LIDR"]
    assert pos.stop_order_id == "", (
        "auto-attach must not match any of these — symbol/side/type/status/qty filter"
    )


def test_sync_from_broker_picks_first_matching_stop_when_multiple():
    """If somehow multiple valid stops exist (operator quirk, race),
    pick the first one. Subsequent attach_external_stop calls can
    override if needed."""
    pm = _build_pm()
    multi_stops = [
        {"id": "first", "symbol": "LIDR", "side": "sell", "qty": "5264",
         "type": "stop", "stop_price": "2.10", "status": "new"},
        {"id": "second", "symbol": "LIDR", "side": "sell", "qty": "5264",
         "type": "stop", "stop_price": "2.05", "status": "new"},
    ]
    pm.sync_from_broker([_broker_position_lidr()], broker_orders=multi_stops)
    assert pm._positions["LIDR"].stop_order_id == "first"


def test_sync_from_broker_accepts_stop_covering_more_than_position():
    """Stop sized for MORE shares than the position is over-protective
    (not a defect — could be a leftover from a partial close).
    Auto-attach should accept it."""
    pm = _build_pm()
    over_stop = [{
        "id": "over", "symbol": "LIDR", "side": "sell", "qty": "10000",
        "type": "stop", "stop_price": "2.10", "status": "new",
    }]
    pm.sync_from_broker([_broker_position_lidr()], broker_orders=over_stop)
    assert pm._positions["LIDR"].stop_order_id == "over"


# ── End-to-end: today's LIDR scenario goes from broken to fixed ──


def test_end_to_end_today_lidr_scenario_fixed():
    """The canonical regression: today (2026-04-27) had:
    - LIDR position at broker (qty=5264 @ $2.42)
    - Operator-submitted protective stop at broker ($2.10)
    - Tracker showed stop=$2.29 COMPUTED DEFAULT, stop_order_id=""
    - D230 RECON_WARN fired every 30s for the entire session

    With Bug AM: the same startup data produces a tracker that
    matches reality from second 0."""
    pm = _build_pm()
    pm.sync_from_broker(
        [_broker_position_lidr()],
        broker_orders=[_broker_stop_lidr_at_2_10()],
    )
    pos = pm._positions["LIDR"]
    # The tracker's stop matches the broker's stop — D230 won't fire
    assert pos.stop_order_id == "859be64d-718e-4f6d-a09a-932d03a79916"
    assert pos.stop_loss == 2.10
    assert pos.qty == 5264
    # And explicitly confirm the COMPUTED DEFAULT $2.29 was NOT used
    assert pos.stop_loss != round(2.42 * (1.0 - 0.055), 4)
