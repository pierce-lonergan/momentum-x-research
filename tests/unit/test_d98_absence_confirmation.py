"""
MOMENTUM-X Tests: D98 absence confirmation (doc-285 gap #2)

Node ID: tests.unit.test_d98_absence_confirmation
Graph Link: tested_by -> cli.main (_d98_fetch_broker_positions,
            _d98_confirm_stop_out, cmd_paper Phase 3 stop-out scan)

The 2026-07-06 10:48 phantom close: a circuit-breaker-open
get_positions() raise was substituted with a fabricated empty list
("using empty"); the D98 stop-out poll diffed internal positions
against that empty book and booked two fake stop-outs on LIVE
positions (-$1,272 phantom realized; regime flipped off a fake loss;
$35.7K ran unmanaged). These tests pin the fix — the PHANTOM-P&L iron
rule extended to ABSENCE:

  1. A failed get_positions() yields None (UNKNOWN), never []; the
     stop-out scan skips the cycle entirely on UNKNOWN.
  2. Booking a stop-out requires EITHER the tracked stop order
     reporting status=filled via the orders API (booked at its real
     filled_avg_price), OR 2 CONSECUTIVE successful snapshots both
     showing the symbol absent. Streaks reset on any fetch failure or
     reappearance.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import main


# ═══════════════════════════════════════════════════════════════════
# Mocks + a cycle driver mirroring cmd_paper's Phase-3 D98 wiring
# ═══════════════════════════════════════════════════════════════════


class _ScriptedClient:
    """Mock Alpaca client: scripted get_positions() cycles + orders.

    positions_script: list of per-cycle results; an Exception entry is
    raised (simulating e.g. the circuit breaker being open).
    orders: list returned by get_orders(), or an Exception to raise.
    """

    def __init__(self, positions_script=None, orders=None):
        self._script = list(positions_script or [])
        self._orders = orders if orders is not None else []
        self.get_positions_calls = 0
        self.get_orders_calls = 0

    async def get_positions(self):
        self.get_positions_calls += 1
        result = self._script.pop(0) if self._script else []
        if isinstance(result, Exception):
            raise result
        return result

    async def get_orders(self, status="open", limit=100, symbols=None, **kw):
        self.get_orders_calls += 1
        if isinstance(self._orders, Exception):
            raise self._orders
        return self._orders


def _make_pos(ticker="RIVN", stop_loss=16.41, stop_order_id=""):
    return SimpleNamespace(
        ticker=ticker, stop_loss=stop_loss, stop_order_id=stop_order_id,
    )


async def _run_scan_cycle(client, internal_positions, absent_counts, booked):
    """One D98 poll cycle, mirroring cmd_paper's Phase-3 wiring.

    Calls the REAL production helpers (_d98_fetch_broker_positions,
    _d98_confirm_stop_out); the inline glue this mirrors is pinned
    string-for-string by TestPhase3Wiring below.
    """
    broker_positions = await main._d98_fetch_broker_positions(client)
    if broker_positions is None:
        # UNKNOWN: skip the stop-out scan entirely; reset streaks.
        absent_counts.clear()
        return
    broker_syms = {ap.get("symbol") for ap in broker_positions}
    # Streaks reset on reappearance AND on episode boundary (ticker no
    # longer tracked) — a stale count must never carry into a re-entry.
    open_syms = {pos.ticker for pos in internal_positions}
    for sym in list(absent_counts):
        if sym in broker_syms or sym not in open_syms:
            del absent_counts[sym]
    for pos in list(internal_positions):
        if pos.ticker not in broker_syms:
            confirmed, fill_px = await main._d98_confirm_stop_out(
                client, pos.ticker,
                getattr(pos, "stop_order_id", "") or "",
                absent_counts,
            )
            if not confirmed:
                continue
            absent_counts.pop(pos.ticker, None)
            booked.append(
                (pos.ticker, fill_px if fill_px is not None else pos.stop_loss)
            )
            internal_positions.remove(pos)


# ═══════════════════════════════════════════════════════════════════
# 1. Breaker-open cycle -> NO close booked (FAILS on pre-fix code:
#    the old path fabricated [] and booked phantom stop-outs)
# ═══════════════════════════════════════════════════════════════════


class TestBreakerOpenCycle:

    async def test_failed_fetch_returns_unknown_not_empty(self):
        """Old code substituted [] on failure; sentinel must be None."""
        client = _ScriptedClient(
            positions_script=[ConnectionError("circuit breaker OPEN")],
        )
        result = await main._d98_fetch_broker_positions(client)
        assert result is None, (
            "get_positions() failure must yield None (UNKNOWN), never a "
            "fabricated empty book"
        )

    async def test_breaker_open_cycle_books_no_close(self):
        """doc-285 10:48 replay: breaker open, live position tracked
        internally -> NO stop-out may be booked this cycle."""
        client = _ScriptedClient(
            positions_script=[ConnectionError("circuit breaker OPEN")],
        )
        internal = [_make_pos("RIVN"), _make_pos("LUCY", stop_loss=0.9843)]
        absent_counts: dict[str, int] = {}
        booked: list = []
        await _run_scan_cycle(client, internal, absent_counts, booked)
        assert booked == [], "phantom close: booked stop-out on a failed read"
        assert len(internal) == 2, "positions must survive an UNKNOWN cycle"
        assert absent_counts == {}, "failed fetch must not build a streak"

    async def test_fetch_failure_resets_absence_streak(self):
        """absent(1) -> breaker-open -> absent(1): the failure resets the
        streak, so two non-consecutive absences never book."""
        client = _ScriptedClient(positions_script=[
            [],                                   # cycle 1: absent (1/2)
            ConnectionError("circuit breaker OPEN"),  # cycle 2: UNKNOWN
            [],                                   # cycle 3: absent (1/2 again)
        ])
        internal = [_make_pos("RIVN")]
        absent_counts: dict[str, int] = {}
        booked: list = []
        for _ in range(3):
            await _run_scan_cycle(client, internal, absent_counts, booked)
        assert booked == []
        assert absent_counts == {"RIVN": 1}


# ═══════════════════════════════════════════════════════════════════
# 2. Absent once then reappears -> no close
# ═══════════════════════════════════════════════════════════════════


class TestReappearance:

    async def test_absent_once_then_reappears_no_close(self):
        client = _ScriptedClient(positions_script=[
            [],                                        # cycle 1: absent (1/2)
            [{"symbol": "RIVN", "qty": "1035",
              "current_price": "17.10"}],              # cycle 2: back
        ])
        internal = [_make_pos("RIVN")]
        absent_counts: dict[str, int] = {}
        booked: list = []
        await _run_scan_cycle(client, internal, absent_counts, booked)
        assert absent_counts == {"RIVN": 1}
        await _run_scan_cycle(client, internal, absent_counts, booked)
        assert booked == []
        assert absent_counts == {}, "reappearance must reset the streak"
        assert len(internal) == 1

    async def test_stale_streak_never_carries_into_reentry(self):
        """Validator regression (episode boundary): a leftover streak
        from a CLOSED position must not survive into a same-ticker
        re-entry — else one transient absence instantly 'confirms' a
        phantom stop-out on the NEW position (the doc-285 class again,
        with N=2 silently degraded to N=1)."""
        client = _ScriptedClient(positions_script=[
            [],   # cycle 1: episode 1 transiently absent -> streak 1/2
            [],   # cycle 2: episode 1 closed via another path; flat is real
            [],   # cycle 3: episode 2 live but transiently absent
        ])
        internal = [_make_pos("RIVN")]
        absent_counts: dict[str, int] = {}
        booked: list = []
        await _run_scan_cycle(client, internal, absent_counts, booked)
        assert absent_counts == {"RIVN": 1}
        # Episode boundary: closed via e.g. tranche T3 full close / BAR-1
        # (broker sell confirmed elsewhere) — no longer tracked.
        internal.clear()
        await _run_scan_cycle(client, internal, absent_counts, booked)
        assert absent_counts == {}, (
            "streak must be dropped once the ticker is no longer tracked"
        )
        # Episode 2: same-session re-entry on the same ticker.
        internal.append(_make_pos("RIVN"))
        await _run_scan_cycle(client, internal, absent_counts, booked)
        assert booked == [], (
            "stale streak carried across episodes: one transient absence "
            "phantom-closed the re-entered position"
        )
        assert absent_counts == {"RIVN": 1}, "re-entry must start a fresh streak"
        assert len(internal) == 1


# ═══════════════════════════════════════════════════════════════════
# 3. Absent twice (consecutive, successful fetches) -> close booked
# ═══════════════════════════════════════════════════════════════════


class TestConfirmedAbsence:

    async def test_absent_twice_confirmed_books_close(self):
        client = _ScriptedClient(positions_script=[[], []])
        internal = [_make_pos("RIVN", stop_loss=16.41)]
        absent_counts: dict[str, int] = {}
        booked: list = []
        await _run_scan_cycle(client, internal, absent_counts, booked)
        assert booked == [], "first absence alone must not book"
        await _run_scan_cycle(client, internal, absent_counts, booked)
        assert booked == [("RIVN", 16.41)], (
            "second consecutive confirmed absence must book at stop_loss"
        )
        assert internal == []
        assert absent_counts == {}

    async def test_helper_first_absence_unconfirmed(self):
        counts: dict[str, int] = {}
        confirmed, px = await main._d98_confirm_stop_out(
            _ScriptedClient(), "RIVN", "", counts,
        )
        assert (confirmed, px) == (False, None)
        assert counts == {"RIVN": 1}

    async def test_helper_second_absence_confirms(self):
        counts = {"RIVN": 1}
        confirmed, px = await main._d98_confirm_stop_out(
            _ScriptedClient(), "RIVN", "", counts,
        )
        assert (confirmed, px) == (True, None)


# ═══════════════════════════════════════════════════════════════════
# 4. Tracked stop order reports filled -> close booked promptly at the
#    broker's filled_avg_price (not the intended stop price)
# ═══════════════════════════════════════════════════════════════════


class TestStopOrderFillConfirmation:

    async def test_stop_order_filled_books_at_broker_price(self):
        client = _ScriptedClient(
            positions_script=[[]],  # single successful absent snapshot
            orders=[{"id": "stop-123", "symbol": "RIVN", "status": "filled",
                     "filled_avg_price": "16.05"}],
        )
        internal = [_make_pos("RIVN", stop_loss=16.41,
                              stop_order_id="stop-123")]
        absent_counts: dict[str, int] = {}
        booked: list = []
        await _run_scan_cycle(client, internal, absent_counts, booked)
        assert booked == [("RIVN", 16.05)], (
            "a filled stop leg is direct broker evidence: book promptly "
            "at ITS filled_avg_price, not the intended stop"
        )
        assert client.get_orders_calls == 1

    async def test_stop_order_still_open_requires_second_absence(self):
        client = _ScriptedClient(
            positions_script=[[], []],
            orders=[{"id": "stop-123", "symbol": "RIVN", "status": "new",
                     "filled_avg_price": None}],
        )
        internal = [_make_pos("RIVN", stop_loss=16.41,
                              stop_order_id="stop-123")]
        absent_counts: dict[str, int] = {}
        booked: list = []
        await _run_scan_cycle(client, internal, absent_counts, booked)
        assert booked == [], "open stop order: absence not yet confirmed"
        await _run_scan_cycle(client, internal, absent_counts, booked)
        assert booked == [("RIVN", 16.41)]

    async def test_orders_api_failure_falls_back_to_streak(self):
        client = _ScriptedClient(
            positions_script=[[]],
            orders=ConnectionError("orders API down"),
        )
        internal = [_make_pos("RIVN", stop_order_id="stop-123")]
        absent_counts: dict[str, int] = {}
        booked: list = []
        await _run_scan_cycle(client, internal, absent_counts, booked)
        assert booked == [], "orders-API failure is not fill confirmation"
        assert absent_counts == {"RIVN": 1}

    async def test_filled_with_bad_price_books_at_intended_stop(self):
        """filled_avg_price of 0/None must not book a $0 exit."""
        counts: dict[str, int] = {}
        client = _ScriptedClient(
            orders=[{"id": "stop-123", "status": "filled",
                     "filled_avg_price": "0"}],
        )
        confirmed, px = await main._d98_confirm_stop_out(
            client, "RIVN", "stop-123", counts,
        )
        assert confirmed is True
        assert px is None, "non-positive fill price must fall back"

    async def test_filled_confirmation_resets_streak(self):
        counts = {"RIVN": 1}
        client = _ScriptedClient(
            orders=[{"id": "stop-123", "status": "filled",
                     "filled_avg_price": "16.05"}],
        )
        confirmed, px = await main._d98_confirm_stop_out(
            client, "RIVN", "stop-123", counts,
        )
        assert (confirmed, px) == (True, 16.05)
        assert counts == {}


# ═══════════════════════════════════════════════════════════════════
# 5. Wiring pins: cmd_paper's inline glue matches the harness above
#    (these FAIL on pre-fix code, which fabricated [] on failure)
# ═══════════════════════════════════════════════════════════════════


class TestPhase3Wiring:

    def _src(self):
        return inspect.getsource(main.cmd_paper)

    def test_fabricated_empty_substitution_removed(self):
        src = self._src()
        assert "— using empty" not in src, (
            "doc-285 regression: a failed get_positions() must never be "
            "substituted with an empty book"
        )
        assert "_d98_broker_positions = []" not in src

    def test_fetch_routes_through_unknown_sentinel_helper(self):
        src = self._src()
        assert ("_d98_broker_positions = "
                "await _d98_fetch_broker_positions(client)") in src

    def test_scan_skips_cycle_on_unknown(self):
        src = self._src()
        assert "if _d98_broker_positions is None:" in src
        assert "D98: broker state unknown — skipping stop-out " in src
        assert "scan this cycle" in src
        assert "_d98_absent_counts.clear()" in src

    def test_absence_diff_guarded_on_unknown(self):
        src = self._src()
        assert ("if _broker_syms is not None "
                "and pos.ticker not in _broker_syms:") in src

    def test_booking_requires_confirmation(self):
        src = self._src()
        assert "await _d98_confirm_stop_out(" in src
        assert "if not _d98_confirmed:" in src

    def test_exit_price_prefers_broker_fill(self):
        src = self._src()
        assert "_d98_fill_px if _d98_fill_px is not None" in src

    def test_absence_counter_initialized(self):
        src = self._src()
        assert "_d98_absent_counts: dict[str, int] = {}" in src

    def test_streak_pruned_on_episode_boundary(self):
        """Validator pin: the reset loop must also drop streaks whose
        ticker is no longer tracked, so a stale count never carries
        into a same-ticker re-entry (instant false confirm)."""
        src = self._src()
        assert ("if _sym98 in _broker_syms "
                "or _sym98 not in _open_syms98:") in src
