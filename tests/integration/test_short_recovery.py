"""doc 202(h): SHORT crash-recovery parity.

Before this fix BOTH recovery paths (sync_from_state_and_orders / sync_from_broker)
skipped shorts (`if side != "long": continue`) and ManagedPosition defaulted
direction="long" — so a fade-short held across a restart was dropped (unmanaged ghost) or
recovered as a long (inverted stop). These tests pin: shorts ARE recovered, with
direction="short" and a stop ABOVE entry; and the LONG path is unchanged.
"""
from __future__ import annotations

from types import SimpleNamespace

from config.settings import ExecutionConfig
from src.execution.position_manager import PositionManager
from src.execution.session_state import PositionState


def _pm():
    return PositionManager(config=ExecutionConfig(), starting_equity=100_000.0)


def _state(**kw):
    pos = PositionState(ticker="FADE", qty=100, entry_price=5.0, **kw)
    return SimpleNamespace(positions={"FADE": pos}, daily_realized_pnl=0.0)


def test_d64_recovers_short_with_direction_and_stop_above():
    pm = _pm()
    cfg = ExecutionConfig()
    st = _state(stop_loss=round(5.0 * (1 + cfg.stop_loss_pct), 4),
                target_prices=[4.75, 4.5, 4.0], direction="short", stop_order_id="oid1")
    broker = [{"symbol": "FADE", "side": "short", "qty": -100,
               "avg_entry_price": 5.0, "current_price": 4.9}]
    pm.sync_from_state_and_orders(st, broker, [])
    assert pm.has_position("FADE")
    p = pm._positions["FADE"]
    assert p.direction == "short"
    assert p.qty == 100                       # abs of the broker's -100
    assert p.stop_loss > p.entry_price        # short stop is ABOVE entry


def test_d64_short_fallback_stop_is_above_when_no_state():
    """No pos_state -> direction-aware default: short stop ABOVE, targets BELOW."""
    pm = _pm()
    cfg = ExecutionConfig()
    empty = SimpleNamespace(positions={}, daily_realized_pnl=0.0)
    broker = [{"symbol": "FADE", "side": "short", "qty": -100,
               "avg_entry_price": 5.0, "current_price": 4.9}]
    pm.sync_from_state_and_orders(empty, broker, [])
    p = pm._positions["FADE"]
    assert p.direction == "short"
    assert p.stop_loss == round(5.0 * (1 + cfg.stop_loss_pct), 4)  # ABOVE entry
    assert all(t < 5.0 for t in p.target_prices)                  # targets BELOW entry


def test_d56_broker_fallback_recovers_short():
    pm = _pm()
    cfg = ExecutionConfig()
    broker = [{"symbol": "FADE", "side": "short", "qty": -100,
               "avg_entry_price": 5.0, "current_price": 4.9}]
    pm.sync_from_broker(broker)
    assert pm.has_position("FADE")
    p = pm._positions["FADE"]
    assert p.direction == "short"
    assert p.qty == 100
    assert p.stop_loss == round(5.0 * (1 + cfg.stop_loss_pct), 4)


def test_long_recovery_unchanged():
    """Regression: the LONG path is byte-for-byte unaffected."""
    pm = _pm()
    cfg = ExecutionConfig()
    st = _state(stop_loss=0.0, target_prices=[], direction="long")
    broker = [{"symbol": "FADE", "side": "long", "qty": 100,
               "avg_entry_price": 5.0, "current_price": 5.1}]
    pm.sync_from_state_and_orders(st, broker, [])
    p = pm._positions["FADE"]
    assert p.direction == "long"
    assert p.qty == 100
    assert p.stop_loss == round(5.0 * (1 - cfg.stop_loss_pct), 4)  # BELOW entry
    assert all(t > 5.0 for t in p.target_prices)                   # targets ABOVE
