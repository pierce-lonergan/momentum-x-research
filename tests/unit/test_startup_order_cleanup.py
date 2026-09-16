"""
Wed 2026-04-22 Bug A tests: D86 cancel-stale-orders must preserve
GTC stops on currently-held positions.

The user's adversarial spec:
  T1. GTC stop on held position survives D86 — the bug we're fixing
  T2. DAY stop on held position is canceled — DAY expires anyway, stale
  T3. GTC stop on position we no longer hold is canceled — orphaned
  T4. Position-quantity check handles partial fills correctly

Plus edge cases that could re-break it later:
  T5. Empty inputs (no orders, no positions) — returns empty plan
  T6. Malformed order missing 'id' is skipped + logged
  T7. Short position with GTC buy-to-cover stop is preserved
  T8. Limit order (not stop) is canceled even if GTC
  T9. trailing_stop type is treated as protective
  T10. Oversized stop (qty > position) is canceled (defensive)
  T11. Multi-position multi-order — full mixed scenario, rule (e)
       positive case mirroring this morning's actual broker state
"""

from __future__ import annotations

import pytest

from src.execution.startup_order_cleanup import (
    CleanupPlan,
    _is_gtc,
    _is_protective_stop,
    _position_qty_by_symbol,
    _should_preserve,
    select_orders_to_cancel,
)


# ── Helpers to build broker-shaped dicts ────────────────────────────


def order(
    *, id="o1", symbol="ELSE", qty="100", side="sell", type="stop",
    tif="gtc", stop_price="6.50", limit_price=None,
):
    return {
        "id": id, "symbol": symbol, "qty": qty, "side": side,
        "type": type, "time_in_force": tif,
        "stop_price": stop_price, "limit_price": limit_price,
    }


def position(*, symbol="ELSE", qty="100", side="long"):
    return {"symbol": symbol, "qty": qty, "side": side}


# ── T1: The bug we're fixing ────────────────────────────────────────


class TestPreserveGTCStopOnHeldPosition:
    """The exact case from this morning's failure: GTC sell-stop on
    a held long position must survive D86."""

    def test_else_morning_scenario_preserves_stop(self):
        # Mirrors this morning's broker state at 04:30:29
        orders = [order(id="6490714a", symbol="ELSE", qty="2478",
                        side="sell", type="stop", tif="gtc",
                        stop_price="6.50")]
        positions = [position(symbol="ELSE", qty="2478", side="long")]

        plan = select_orders_to_cancel(orders, positions)

        assert plan.cancel_ids == [], (
            "GTC protective stop on held position must NOT be canceled"
        )
        assert len(plan.preserved) == 1
        assert plan.preserved[0][0] == "6490714a"
        assert plan.preserved[0][1] == "ELSE"
        assert "GTC" in plan.preserved[0][2]


# ── T2: DAY stops still get cancelled ───────────────────────────────


class TestDayStopsAreStillCanceled:
    """A DAY-TIF protective stop on a held position should still be
    canceled — DAY orders expire at EOD anyway. (We expect callers
    to be on Fix 1 now, so this case is rare, but the filter must
    still cancel it.)"""

    def test_day_stop_on_held_position_is_canceled(self):
        orders = [order(id="o1", symbol="ELSE", qty="100",
                        type="stop", tif="day")]
        positions = [position(symbol="ELSE", qty="100")]
        plan = select_orders_to_cancel(orders, positions)
        assert plan.cancel_ids == ["o1"]
        assert plan.preserved == []


# ── T3: Orphaned GTC stops are cancelled ────────────────────────────


class TestOrphanedGTCStops:
    """GTC stop on a position we no longer hold is orphaned — cancel."""

    def test_gtc_stop_no_held_position(self):
        orders = [order(id="o1", symbol="ELSE", qty="100",
                        type="stop", tif="gtc")]
        positions = []  # we don't hold anything
        plan = select_orders_to_cancel(orders, positions)
        assert plan.cancel_ids == ["o1"]
        assert plan.preserved == []

    def test_gtc_stop_different_symbol_held(self):
        orders = [order(id="o1", symbol="ELSE", qty="100",
                        type="stop", tif="gtc")]
        positions = [position(symbol="OTHER", qty="200")]
        plan = select_orders_to_cancel(orders, positions)
        assert plan.cancel_ids == ["o1"]


# ── T4: Partial-fill / qty-coverage logic ───────────────────────────


class TestPositionQtyCoverage:
    """The stop must be for AT MOST as many shares as we hold."""

    def test_stop_qty_less_than_position_preserves(self):
        """Common after T1 fills: position 1652, stop for 1652."""
        orders = [order(id="o1", qty="1652")]
        positions = [position(qty="1652")]
        plan = select_orders_to_cancel(orders, positions)
        assert plan.cancel_ids == []

    def test_stop_qty_equals_position_preserves(self):
        orders = [order(id="o1", qty="2478")]
        positions = [position(qty="2478")]
        plan = select_orders_to_cancel(orders, positions)
        assert plan.cancel_ids == []

    def test_stop_qty_GREATER_than_position_cancels(self):
        """Defensive: oversized stop is incoherent — cancel and let
        live code re-establish a properly-sized one."""
        orders = [order(id="o1", qty="3000")]   # asking to sell 3000
        positions = [position(qty="2478")]      # only own 2478
        plan = select_orders_to_cancel(orders, positions)
        assert plan.cancel_ids == ["o1"]


# ── T5: Empty inputs ────────────────────────────────────────────────


class TestEmptyInputs:

    def test_no_orders_no_positions(self):
        plan = select_orders_to_cancel([], [])
        assert plan.cancel_ids == [] and plan.preserved == []

    def test_no_orders_with_positions(self):
        plan = select_orders_to_cancel([], [position()])
        assert plan.cancel_ids == [] and plan.preserved == []

    def test_orders_no_positions(self):
        """Every order is orphaned → all canceled."""
        orders = [order(id="o1"), order(id="o2", symbol="OTHER")]
        plan = select_orders_to_cancel(orders, [])
        assert sorted(plan.cancel_ids) == ["o1", "o2"]
        assert plan.preserved == []


# ── T6: Malformed orders are skipped + logged ───────────────────────


class TestMalformedOrders:

    def test_order_missing_id_is_skipped(self, caplog):
        import logging
        orders = [{"symbol": "ELSE", "type": "stop", "qty": "100",
                   "time_in_force": "gtc"}]  # no 'id'!
        positions = [position()]
        with caplog.at_level(logging.WARNING):
            plan = select_orders_to_cancel(orders, positions)
        assert plan.cancel_ids == []
        assert plan.preserved == []
        assert any("no 'id'" in r.message for r in caplog.records)

    def test_order_unparseable_qty_is_canceled(self):
        """Defensive: garbage qty → cancel rather than preserve
        (unknown-sized stop is not safer)."""
        orders = [order(id="o1", qty="NaN")]
        positions = [position(qty="100")]
        plan = select_orders_to_cancel(orders, positions)
        assert plan.cancel_ids == ["o1"]


# ── T7: Short positions ─────────────────────────────────────────────


class TestShortPositions:
    """Short cover stops (buy-to-cover at ABOVE entry) are also
    protective. Same preservation rule, different side."""

    def test_short_cover_stop_preserved(self):
        orders = [order(id="o1", side="buy", type="stop",
                        tif="gtc", stop_price="9.00", qty="100")]
        # Short position: Alpaca returns qty as negative string
        positions = [position(qty="-100", side="short")]
        plan = select_orders_to_cancel(orders, positions)
        # abs(qty) means short of 100 covers a 100-share buy stop
        assert plan.cancel_ids == []
        assert len(plan.preserved) == 1


# ── T8: Limit orders (non-stops) are NOT preserved ──────────────────


class TestLimitOrdersNotPreserved:
    """Tranche-sell limits / take-profits / entry limits — none of
    these are 'the protective stop' the user means. They get the
    default cancel."""

    def test_limit_order_canceled_even_if_gtc(self):
        orders = [order(id="o1", type="limit", tif="gtc",
                        limit_price="10.00", stop_price=None)]
        positions = [position()]
        plan = select_orders_to_cancel(orders, positions)
        assert plan.cancel_ids == ["o1"]

    def test_market_order_canceled(self):
        orders = [order(id="o1", type="market", tif="day")]
        positions = [position()]
        plan = select_orders_to_cancel(orders, positions)
        assert plan.cancel_ids == ["o1"]


# ── T9: trailing_stop also counts ────────────────────────────────────


class TestTrailingStopType:

    def test_trailing_stop_gtc_on_held_position_preserved(self):
        orders = [order(id="o1", type="trailing_stop", tif="gtc")]
        positions = [position()]
        plan = select_orders_to_cancel(orders, positions)
        assert plan.cancel_ids == []
        assert len(plan.preserved) == 1


# ── T11: Rule (e) positive case — full mixed scenario ───────────────


class TestRealisticMixedScenario:
    """A morning startup with a coherent broker state: 1 held
    position with its GTC stop, 1 stale day-tranche from yesterday,
    1 orphaned stop on a closed-out symbol. Only the held GTC stop
    survives."""

    def test_full_morning_cleanup(self):
        orders = [
            # Today's protective stop on ELSE — PRESERVE
            order(id="STOP_KEEP", symbol="ELSE", qty="2478",
                  type="stop", tif="gtc", stop_price="7.50"),
            # Stale DAY-tranche from previous session — CANCEL
            order(id="STALE_DAY_LIMIT", symbol="ELSE", qty="826",
                  type="limit", tif="day", limit_price="8.03",
                  stop_price=None),
            # Orphaned GTC stop on a symbol we no longer hold — CANCEL
            order(id="ORPHANED", symbol="OLDPOS", qty="500",
                  type="stop", tif="gtc"),
            # Limit-take-profit GTC, oversized vs holding — CANCEL
            order(id="OVERSIZED_GTC_STOP", symbol="ELSE", qty="9999",
                  type="stop", tif="gtc"),
        ]
        positions = [position(symbol="ELSE", qty="2478", side="long")]

        plan = select_orders_to_cancel(orders, positions)

        # Exactly one preserved: the legit GTC stop
        assert len(plan.preserved) == 1
        assert plan.preserved[0][0] == "STOP_KEEP"

        # Three cancellations: stale day, orphan, oversized
        assert sorted(plan.cancel_ids) == sorted([
            "STALE_DAY_LIMIT", "ORPHANED", "OVERSIZED_GTC_STOP",
        ])

        # Cleanup-plan summary fields
        assert plan.n_preserved == 1
        assert plan.n_cancel == 3


# ── Pure-helper unit tests ──────────────────────────────────────────


class TestPureHelpers:

    def test_position_qty_by_symbol_uses_abs(self):
        positions = [
            position(symbol="LONG_A", qty="100"),
            position(symbol="SHORT_B", qty="-50", side="short"),
            position(symbol="ZERO", qty="0"),  # filtered out
        ]
        m = _position_qty_by_symbol(positions)
        assert m == {"LONG_A": 100, "SHORT_B": 50}

    def test_is_gtc(self):
        assert _is_gtc({"time_in_force": "gtc"}) is True
        assert _is_gtc({"time_in_force": "day"}) is False
        assert _is_gtc({}) is False

    def test_is_protective_stop(self):
        assert _is_protective_stop({"type": "stop"}) is True
        assert _is_protective_stop({"type": "stop_limit"}) is True
        assert _is_protective_stop({"type": "trailing_stop"}) is True
        assert _is_protective_stop({"type": "limit"}) is False
        assert _is_protective_stop({"type": "market"}) is False
        assert _is_protective_stop({}) is False

    def test_should_preserve_returns_reason_strings(self):
        ord_obj = order(id="o1", type="stop", tif="gtc", qty="100")
        positions = {"ELSE": 100}
        preserve, reason = _should_preserve(ord_obj, positions)
        assert preserve is True
        assert "GTC" in reason and "ELSE" in reason
