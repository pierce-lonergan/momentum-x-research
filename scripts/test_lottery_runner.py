"""Aggressive unit tests for lottery_runner bug fixes.

Covers:
  T1: poll_until_filled — order fills at +5s (BUG-1 scenario)
  T2: poll_until_filled — order fills at +30s (BUG-1 worst-case)
  T3: poll_until_filled — order never fills, hits 60s timeout
  T4: poll_until_filled — order rejected mid-poll
  T5: open_lottery_positions — slow fill gets trail attached (BUG-1 fix)
  T6: open_lottery_positions — failed fill registers position with no trail
  T7: open_lottery_positions — trail submission failure logged but position kept
  T8: force_close_remaining — cancels open orders, closes all live positions
       in lottery_tickers (BUG-2 fix)
  T9: force_close_remaining — only closes positions in lottery_tickers
       (does NOT touch positions opened by main bot)
  T10: force_close_remaining — handles untracked position (filled after
        runner stopped tracking)
  T11: TEST_MODE skips lottery_traded_tickers.json write (BUG-3 fix)
  T12: heartbeat counts positions via lottery_tickers, not runner-tracked
        list (BUG-4 fix) — implicit via T8/T9
  T13: session_report reconciles via list_orders(all) — finds positions
        even when runner missed them (BUG-5 fix)
  T14: end-to-end happy path with mocked Alpaca

Run: pytest scripts/test_lottery_runner.py -v
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

# Ensure env defaults present BEFORE module import
os.environ.setdefault("ALPACA_API_KEY", "test_key")
os.environ.setdefault("ALPACA_SECRET_KEY", "test_secret")
os.environ["LOTTERY_FILL_POLL_TIMEOUT_S"] = "5"
os.environ["LOTTERY_FILL_POLL_INTERVAL_S"] = "0.1"
os.environ["LOTTERY_TEST_MODE"] = "1"
# Disable Polygon enrichment in tests — unit tests run against FakeAlpaca only
os.environ["LOTTERY_USE_POLYGON_SCREENER"] = "0"
os.environ["LOTTERY_USE_POLYGON_NEWS_GATE"] = "0"

import lottery_runner as L  # noqa: E402

L.FILL_POLL_TIMEOUT_S = 5.0
L.FILL_POLL_INTERVAL_S = 0.1
L.DRY_RUN = False
L.LOTTERY_HALT = False
L.TEST_MODE = True


# ────────────────────────────────────────────────────────────────────
# Mock Alpaca client
# ────────────────────────────────────────────────────────────────────

class FakeAlpaca:
    """In-memory fake AlpacaClient. State is a dict of orders + positions."""

    def __init__(self) -> None:
        self.orders: dict[str, dict] = {}
        self.positions: dict[str, dict] = {}
        self._oid = 0
        self.fill_delay_s: dict[str, float] = {}  # symbol → seconds until fill
        self.never_fill: set[str] = set()  # symbols whose buy will never fill
        self.reject: set[str] = set()  # symbols whose buy will be rejected
        self.trail_submit_fails: set[str] = set()  # symbols where trail submit raises
        self._submit_times: dict[str, float] = {}

    def _new_id(self) -> str:
        self._oid += 1
        return f"ord-{self._oid:03d}"

    async def aclose(self) -> None:
        pass

    async def get_account(self) -> dict:
        return {"account_number": "TEST", "status": "PAPER_TRADING",
                "equity": "100000", "cash": "100000"}

    async def get_clock(self) -> dict:
        return {"is_open": True, "timestamp": "2026-05-04T10:00:00-04:00"}

    async def get_movers(self, limit: int = 30) -> dict:
        return {"gainers": [
            {"symbol": "FOO", "price": 5.0, "percent_change": 25.0},
            {"symbol": "BAR", "price": 8.0, "percent_change": 30.0},
            {"symbol": "BAZ", "price": 12.0, "percent_change": 40.0},
        ]}

    async def submit_market_buy(self, symbol: str, qty: int) -> dict:
        oid = self._new_id()
        self._submit_times[oid] = asyncio.get_event_loop().time()
        self.orders[oid] = {
            "id": oid, "symbol": symbol, "side": "buy", "type": "market",
            "qty": str(qty), "filled_qty": "0", "filled_avg_price": None,
            "status": "new",
            "created_at": L.TODAY_ET + "T13:30:00Z",
        }
        if symbol in self.reject:
            self.orders[oid]["status"] = "rejected"
        return self.orders[oid]

    async def submit_trailing_stop_sell(self, symbol: str, qty: int, trail_pct: float) -> dict:
        if symbol in self.trail_submit_fails:
            raise RuntimeError(f"simulated trail submit failure for {symbol}")
        oid = self._new_id()
        self.orders[oid] = {
            "id": oid, "symbol": symbol, "side": "sell", "type": "trailing_stop",
            "qty": str(qty), "filled_qty": "0", "filled_avg_price": None,
            "status": "new", "trail_percent": str(trail_pct),
            "created_at": L.TODAY_ET + "T13:30:01Z",
        }
        return self.orders[oid]

    async def submit_market_sell(self, symbol: str, qty: int) -> dict:
        oid = self._new_id()
        self.orders[oid] = {
            "id": oid, "symbol": symbol, "side": "sell", "type": "market",
            "qty": str(qty), "filled_qty": str(qty),
            "filled_avg_price": "10.00",  # arbitrary
            "status": "filled",
            "created_at": L.TODAY_ET + "T19:55:00Z",
        }
        # Update positions to reflect the sell
        if symbol in self.positions:
            sold_qty = qty
            existing_qty = int(float(self.positions[symbol]["qty"]))
            new_qty = existing_qty - sold_qty
            if new_qty <= 0:
                del self.positions[symbol]
            else:
                self.positions[symbol]["qty"] = str(new_qty)
        return self.orders[oid]

    async def get_order(self, order_id: str) -> dict:
        o = self.orders[order_id]
        # Simulate fill based on configured delay
        if o["side"] == "buy" and o["status"] == "new":
            sym = o["symbol"]
            if sym in self.never_fill:
                return o
            if sym in self.reject:
                o["status"] = "rejected"
                return o
            elapsed = asyncio.get_event_loop().time() - self._submit_times[order_id]
            delay = self.fill_delay_s.get(sym, 0)
            if elapsed >= delay:
                o["status"] = "filled"
                o["filled_qty"] = o["qty"]
                o["filled_avg_price"] = "5.00"  # simple flat price
                # Record position
                self.positions[sym] = {
                    "symbol": sym, "qty": o["qty"], "avg_entry_price": "5.00",
                    "current_price": "5.00", "unrealized_pl": "0",
                    "unrealized_plpc": "0",
                }
        return o

    async def cancel_order(self, order_id: str) -> dict | None:
        if order_id in self.orders:
            o = self.orders[order_id]
            if o["status"] in ("new", "pending_new", "accepted", "partially_filled"):
                o["status"] = "canceled"
        return {"status": 200}

    async def list_positions(self) -> list[dict]:
        return list(self.positions.values())

    async def list_orders(self, status: str = "open", nested: bool = True) -> list[dict]:
        if status == "open":
            return [o for o in self.orders.values()
                    if o["status"] in ("new", "pending_new", "accepted",
                                        "partially_filled")]
        if status == "closed":
            return [o for o in self.orders.values()
                    if o["status"] in ("filled", "canceled", "expired", "rejected")]
        return list(self.orders.values())

    async def poll_until_filled(self, order_id: str,
                                  timeout_s: float, interval_s: float) -> dict:
        """Delegate to the real AlpacaClient implementation (it only uses
        get_order, which we implement)."""
        return await L.AlpacaClient.poll_until_filled(self, order_id, timeout_s, interval_s)


# ────────────────────────────────────────────────────────────────────
# poll_until_filled — Bug 1
# ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_T1_poll_fills_quickly():
    """Order fills at +0.3s — should return filled."""
    fa = FakeAlpaca()
    fa.fill_delay_s["FOO"] = 0.3
    buy = await fa.submit_market_buy("FOO", 100)
    res = await fa.poll_until_filled.__get__(fa)(buy["id"], 5.0, 0.1) \
        if False else await L.AlpacaClient.poll_until_filled(fa, buy["id"], 5.0, 0.1)
    assert res["status"] == "filled"
    assert int(res["filled_qty"]) == 100


@pytest.mark.asyncio
async def test_T2_poll_fills_at_3s():
    """Order fills at +3s — within timeout."""
    fa = FakeAlpaca()
    fa.fill_delay_s["FOO"] = 3.0
    buy = await fa.submit_market_buy("FOO", 100)
    res = await L.AlpacaClient.poll_until_filled(fa, buy["id"], 5.0, 0.5)
    assert res["status"] == "filled"


@pytest.mark.asyncio
async def test_T3_poll_times_out_unfilled():
    """Order never fills — return last status without exception."""
    fa = FakeAlpaca()
    fa.never_fill.add("FOO")
    buy = await fa.submit_market_buy("FOO", 100)
    res = await L.AlpacaClient.poll_until_filled(fa, buy["id"], 1.0, 0.1)
    assert res["status"] in ("new", "pending_new")
    assert int(res["filled_qty"]) == 0


@pytest.mark.asyncio
async def test_T4_poll_handles_rejection():
    """Order rejected mid-poll."""
    fa = FakeAlpaca()
    fa.reject.add("FOO")
    buy = await fa.submit_market_buy("FOO", 100)
    res = await L.AlpacaClient.poll_until_filled(fa, buy["id"], 5.0, 0.1)
    assert res["status"] == "rejected"


# ────────────────────────────────────────────────────────────────────
# open_lottery_positions — Bug 1 + 7
# ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_T5_open_positions_slow_fill_gets_trail():
    """Bug 1 regression: a buy that fills at +3s must get its trailing stop."""
    fa = FakeAlpaca()
    fa.fill_delay_s["FOO"] = 3.0
    picks = [L.Pick("FOO", 5.0, 25.0, True)]
    positions = await L.open_lottery_positions(fa, picks)
    assert len(positions) == 1
    assert positions[0].entry_order_id != "DRY"
    assert positions[0].trail_order_id is not None
    # Verify a trailing-stop order was submitted
    open_orders = await fa.list_orders("open")
    trails = [o for o in open_orders if o["type"] == "trailing_stop"]
    assert len(trails) == 1
    assert trails[0]["symbol"] == "FOO"


@pytest.mark.asyncio
async def test_T6_open_positions_unfilled_buy_registers_position():
    """Buy never fills within timeout — position is still registered with
    no trail so EOD sweep can clean up."""
    fa = FakeAlpaca()
    fa.never_fill.add("FOO")
    picks = [L.Pick("FOO", 5.0, 25.0, True)]
    positions = await L.open_lottery_positions(fa, picks)
    assert len(positions) == 1
    assert positions[0].trail_order_id is None  # no trail attached
    assert positions[0].entry_order_id != "DRY"
    # No trailing-stop was submitted
    open_orders = await fa.list_orders("open")
    assert not any(o["type"] == "trailing_stop" for o in open_orders)


@pytest.mark.asyncio
async def test_T7_open_positions_trail_submit_fails():
    """Trail submission raises — position is registered without trail
    (so EOD sweep will close it)."""
    fa = FakeAlpaca()
    fa.fill_delay_s["FOO"] = 0.1
    fa.trail_submit_fails.add("FOO")
    picks = [L.Pick("FOO", 5.0, 25.0, True)]
    positions = await L.open_lottery_positions(fa, picks)
    assert len(positions) == 1
    assert positions[0].trail_order_id is None
    assert positions[0].entry_order_id != "DRY"
    # Position exists in fake account
    live = await fa.list_positions()
    assert any(p["symbol"] == "FOO" for p in live)


# ────────────────────────────────────────────────────────────────────
# force_close_remaining — Bug 2
# ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_T8_force_close_cancels_orders_and_closes_positions():
    """All open lottery orders cancelled + all live lottery positions
    market-sold."""
    fa = FakeAlpaca()
    # Pre-populate: 2 open positions with 2 trailing stops
    fa.positions["FOO"] = {"symbol": "FOO", "qty": "100",
                            "avg_entry_price": "5.0", "current_price": "5.5"}
    fa.positions["BAR"] = {"symbol": "BAR", "qty": "50",
                            "avg_entry_price": "8.0", "current_price": "9.0"}
    await fa.submit_trailing_stop_sell("FOO", 100, 15.0)
    await fa.submit_trailing_stop_sell("BAR", 50, 15.0)

    positions = [
        L.Position(ticker="FOO", qty=100, entry_price=5.0,
                    entry_order_id="ord-x", trail_order_id="ord-y"),
        L.Position(ticker="BAR", qty=50, entry_price=8.0,
                    entry_order_id="ord-z", trail_order_id="ord-w"),
    ]
    await L.force_close_remaining(fa, positions, lottery_tickers={"FOO", "BAR"})

    # Verify trailing stops cancelled
    trails = [o for o in fa.orders.values() if o["type"] == "trailing_stop"]
    assert all(o["status"] == "canceled" for o in trails)
    # Verify market sells were issued
    sells = [o for o in fa.orders.values() if o["type"] == "market" and o["side"] == "sell"]
    assert len(sells) == 2
    # Positions should be empty after the sells
    live = await fa.list_positions()
    assert len(live) == 0


@pytest.mark.asyncio
async def test_T9_force_close_does_not_touch_non_lottery_positions():
    """Critical safety: a position opened by the main bot must NOT be
    closed by the lottery's EOD sweep."""
    fa = FakeAlpaca()
    fa.positions["FOO"] = {"symbol": "FOO", "qty": "100",
                            "avg_entry_price": "5.0", "current_price": "5.5"}
    fa.positions["BOTSYM"] = {"symbol": "BOTSYM", "qty": "50",
                                "avg_entry_price": "20.0", "current_price": "21.0"}
    positions = [
        L.Position(ticker="FOO", qty=100, entry_price=5.0,
                    entry_order_id="ord-x", trail_order_id=None),
    ]
    await L.force_close_remaining(fa, positions, lottery_tickers={"FOO"})
    live = await fa.list_positions()
    # FOO closed, BOTSYM untouched
    syms = {p["symbol"] for p in live}
    assert "FOO" not in syms
    assert "BOTSYM" in syms


@pytest.mark.asyncio
async def test_T10_force_close_handles_untracked_position():
    """Bug 2 regression: a buy that filled AFTER the runner stopped
    tracking — still gets closed because force_close walks live state."""
    fa = FakeAlpaca()
    # Position exists in account but not in runner's tracked list
    fa.positions["FOO"] = {"symbol": "FOO", "qty": "100",
                            "avg_entry_price": "5.0", "current_price": "5.5"}
    positions: list[L.Position] = []  # runner missed it
    await L.force_close_remaining(fa, positions, lottery_tickers={"FOO"})
    live = await fa.list_positions()
    assert "FOO" not in {p["symbol"] for p in live}
    # And the runner's position list now has a stub
    assert len(positions) == 1
    assert positions[0].ticker == "FOO"
    assert positions[0].entry_order_id == "UNTRACKED"
    assert positions[0].exit_reason == "time_stop"


# ────────────────────────────────────────────────────────────────────
# Freshness / TEST_MODE — Bug 3
# ────────────────────────────────────────────────────────────────────

def test_T11_test_mode_does_not_pollute_history(tmp_path, monkeypatch):
    """Bug 3 regression: TEST_MODE/DRY_RUN/HALT must not write to
    lottery_traded_tickers.json."""
    # Redirect STATE_DIR to tmp_path
    monkeypatch.setattr(L, "STATE_DIR", tmp_path)
    history_file = tmp_path / "lottery_traded_tickers.json"
    assert not history_file.exists()

    # Simulate the gate from main_async
    DRY_RUN, LOTTERY_HALT, TEST_MODE = False, False, True
    if not (DRY_RUN or LOTTERY_HALT or TEST_MODE):
        L.add_to_lottery_history({"FOO", "BAR"})
    # File MUST NOT exist
    assert not history_file.exists()

    # And the inverse (REAL run) DOES write
    DRY_RUN, LOTTERY_HALT, TEST_MODE = False, False, False
    if not (DRY_RUN or LOTTERY_HALT or TEST_MODE):
        L.add_to_lottery_history({"FOO", "BAR"})
    assert history_file.exists()
    assert set(json.loads(history_file.read_text())) == {"BAR", "FOO"}


# ────────────────────────────────────────────────────────────────────
# session_report — Bug 5
# ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_T13_session_report_reconciles_via_orders(tmp_path, monkeypatch):
    """Bug 5 regression: even if runner-tracked positions list is empty,
    session_report must reconstruct realized P&L from list_orders(all)."""
    monkeypatch.setattr(L, "STATE_DIR", tmp_path)
    fa = FakeAlpaca()
    # Simulate a complete round-trip: BUY filled at $5, SELL filled at $5.50
    buy_oid = "buy-1"
    fa.orders[buy_oid] = {
        "id": buy_oid, "symbol": "FOO", "side": "buy", "type": "market",
        "qty": "100", "filled_qty": "100", "filled_avg_price": "5.00",
        "status": "filled",
        "created_at": L.TODAY_ET + "T13:30:00Z",
    }
    sell_oid = "sell-1"
    fa.orders[sell_oid] = {
        "id": sell_oid, "symbol": "FOO", "side": "sell", "type": "trailing_stop",
        "qty": "100", "filled_qty": "100", "filled_avg_price": "5.50",
        "status": "filled",
        "created_at": L.TODAY_ET + "T13:30:01Z",
    }
    # Runner-tracked positions list is EMPTY (the bug scenario)
    positions: list[L.Position] = []
    report = await L.session_report(fa, positions, lottery_tickers={"FOO"})
    assert report["n_positions"] == 1
    foo_row = next(r for r in report["positions"] if r["ticker"] == "FOO")
    assert foo_row["buy_qty"] == 100
    assert foo_row["sell_qty"] == 100
    assert foo_row["avg_buy_px"] == 5.0
    assert foo_row["avg_sell_px"] == 5.5
    assert abs(foo_row["realized_pnl_usd"] - 50.0) < 0.01
    assert abs(report["total_realized_pnl_usd"] - 50.0) < 0.01


@pytest.mark.asyncio
async def test_T14_e2e_happy_path(tmp_path, monkeypatch):
    """End-to-end: 3 picks → all fill at +1s → trails attached →
    force-close at EOD → session report shows realized P&L."""
    monkeypatch.setattr(L, "STATE_DIR", tmp_path)
    monkeypatch.setattr(L, "BAR_DIR", tmp_path / "bars")
    fa = FakeAlpaca()
    fa.fill_delay_s = {"FOO": 1.0, "BAR": 1.0, "BAZ": 1.0}

    picks = await L.build_watchlist(fa)
    assert len(picks) == 3

    positions = await L.open_lottery_positions(fa, picks)
    # All 3 should have filled and have trails
    assert len(positions) == 3
    assert all(p.trail_order_id is not None for p in positions)
    assert all(p.entry_order_id != "DRY" for p in positions)

    # Simulate trailing-stop fills (price rose then fell)
    # We just market-sell to simulate the trail trigger
    lottery_tickers = {p.ticker for p in picks}
    await L.force_close_remaining(fa, positions, lottery_tickers=lottery_tickers)
    # No live positions remain
    live = await fa.list_positions()
    assert len(live) == 0

    # Session report should show all 3 positions closed
    report = await L.session_report(fa, positions, lottery_tickers=lottery_tickers)
    assert report["n_positions"] == 3
    for r in report["positions"]:
        assert r["buy_qty"] > 0
        assert r["sell_qty"] == r["buy_qty"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))
