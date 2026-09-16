"""
Track A item 3 — three reconciliation invariants as EOD assertions.

Per `25_bug_hunting_playbook.md` §3.3 + Track A item 3 in the
2026-04-23 next-actions list:

    "Add the three reconciliation invariants as standalone assertions
    right now — even before the daemon. `assert internal_qty ==
    broker_filled_qty`, `assert internal_stop_price == broker_stop_price`,
    `assert abs(internal_equity - broker_equity) < 1.00` — wired into
    the existing EOD summary hook. This is a 30-minute change that
    catches D-class and R-class recurrence before the full daemon ships."

These are asynchronous, broker-aware checks that fire at the end of
every paper-trading session. They are NOT the recon daemon (which
runs every 30s) — they are the cheap stopgap that catches Bug D, Bug R,
and Bug N regression at the EOD horizon while the full daemon (Track B
Week 2) is being built.

D-codes:
    D230 RECON_WARN          — soft alert, within tolerance
    D231 RECON_HARD_BLOCK    — Tier-1 violation (qty/stop drift)
    (D232 LETHAL is daemon-only; not appropriate for EOD batch checks)

Tests:
  T1  qty_invariant clean (broker == internal) — no warning
  T2  qty_invariant violated — D230 fires + per-ticker breakdown in message
  T3  stop_invariant clean — no warning
  T4  stop_invariant violated — D230 fires
  T5  equity_invariant within $1.00 — no warning
  T6  equity_invariant exceeds $1.00 — D230 fires
  T7  broker call fails — non-fatal degraded log, no crash
  T8  Tier-1 (qty mismatch on any ticker) — D231 escalation
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest


# ── T1, T2: qty invariant ──────────────────────────────────────────


class TestEodQtyInvariant:

    @pytest.mark.asyncio
    async def test_qty_invariant_clean_no_warning(self, caplog):
        from src.monitoring.eod_recon import run_eod_invariants

        client = MagicMock()
        client.get_positions = AsyncMock(return_value=[
            {"symbol": "XNDU", "qty": "395"},
        ])
        # Clean case: position has a broker-confirmed stop matching internal
        client.get_orders = AsyncMock(return_value=[
            {"id": "stop-clean-1", "symbol": "XNDU", "type": "stop",
             "side": "sell", "stop_price": "25.72", "status": "new"},
        ])
        client.get_account = AsyncMock(return_value={"equity": "142520.59"})

        pm = MagicMock()
        _pos = MagicMock()
        _pos.ticker = "XNDU"
        _pos.qty = 395
        _pos.stop_loss = 25.72
        _pos.stop_order_id = "stop-clean-1"   # broker-confirmed
        pm.open_positions = [_pos]
        pm.starting_equity = 142520.59
        pm._daily_realized_pnl = 0.0

        with caplog.at_level(logging.WARNING, logger="src.monitoring.eod_recon"):
            result = await run_eod_invariants(client=client, position_manager=pm)
        warnings = [r for r in caplog.records if "D230" in r.message or "D231" in r.message]
        assert len(warnings) == 0, f"unexpected drift warnings on clean state: {warnings}"
        assert result["qty_drift_count"] == 0
        assert result["stop_drift_count"] == 0
        assert result["equity_within_tolerance"] is True

    @pytest.mark.asyncio
    async def test_qty_invariant_violated_emits_d231(self, caplog):
        """Bug D regression at EOD — broker has 846, internal has 505.
        Must emit D231 (Tier-1 hard block code; at EOD this is recorded
        as a hard violation but no live blocking happens because session
        is closing)."""
        from src.monitoring.eod_recon import run_eod_invariants

        client = MagicMock()
        client.get_positions = AsyncMock(return_value=[
            {"symbol": "AGPU", "qty": "846"},
        ])
        client.get_orders = AsyncMock(return_value=[])
        client.get_account = AsyncMock(return_value={"equity": "142520.59"})

        pm = MagicMock()
        _pos = MagicMock()
        _pos.ticker = "AGPU"
        _pos.qty = 505           # the bug case
        _pos.stop_loss = 9.46
        _pos.stop_order_id = ""
        pm.open_positions = [_pos]
        pm.starting_equity = 142520.59
        pm._daily_realized_pnl = 0.0

        with caplog.at_level(logging.WARNING, logger="src.monitoring.eod_recon"):
            result = await run_eod_invariants(client=client, position_manager=pm)
        # D231 because qty mismatch is Tier-1 per registry
        msgs = [r.message for r in caplog.records if "D231" in r.message]
        assert len(msgs) >= 1, f"expected D231 hard-block warning; got: {[r.message for r in caplog.records]}"
        assert "AGPU" in msgs[0]
        assert "846" in msgs[0]
        assert "505" in msgs[0]
        assert result["qty_drift_count"] == 1


# ── T3, T4: stop invariant ─────────────────────────────────────────


class TestEodStopInvariant:

    @pytest.mark.asyncio
    async def test_stop_invariant_clean_no_warning(self, caplog):
        from src.monitoring.eod_recon import run_eod_invariants

        client = MagicMock()
        client.get_positions = AsyncMock(return_value=[
            {"symbol": "XNDU", "qty": "395"},
        ])
        # Broker stop order exists at $32.44 (Phase-0 tightened)
        client.get_orders = AsyncMock(return_value=[
            {"id": "stop-1", "symbol": "XNDU", "type": "stop",
             "side": "sell", "stop_price": "32.44", "status": "new"},
        ])
        client.get_account = AsyncMock(return_value={"equity": "142520.59"})

        pm = MagicMock()
        _pos = MagicMock()
        _pos.ticker = "XNDU"
        _pos.qty = 395
        _pos.stop_loss = 32.44   # tracker matches broker (post Bug R fix)
        _pos.stop_order_id = "stop-1"
        pm.open_positions = [_pos]
        pm.starting_equity = 142520.59
        pm._daily_realized_pnl = 0.0

        with caplog.at_level(logging.WARNING, logger="src.monitoring.eod_recon"):
            await run_eod_invariants(client=client, position_manager=pm)
        warnings = [r for r in caplog.records if "D230" in r.message or "D231" in r.message]
        assert len(warnings) == 0

    @pytest.mark.asyncio
    async def test_stop_invariant_violated_bug_r_replay(self, caplog):
        """Bug R regression at EOD — tracker stop $25.72, broker stop
        $32.44 (Phase-0 tightened). Must emit D231."""
        from src.monitoring.eod_recon import run_eod_invariants

        client = MagicMock()
        client.get_positions = AsyncMock(return_value=[
            {"symbol": "XNDU", "qty": "395"},
        ])
        client.get_orders = AsyncMock(return_value=[
            {"id": "stop-1", "symbol": "XNDU", "type": "stop",
             "side": "sell", "stop_price": "32.44", "status": "new"},
        ])
        client.get_account = AsyncMock(return_value={"equity": "142520.59"})

        pm = MagicMock()
        _pos = MagicMock()
        _pos.ticker = "XNDU"
        _pos.qty = 395
        _pos.stop_loss = 25.72   # the Bug R divergence
        _pos.stop_order_id = "stop-1"
        pm.open_positions = [_pos]
        pm.starting_equity = 142520.59
        pm._daily_realized_pnl = 0.0

        with caplog.at_level(logging.WARNING, logger="src.monitoring.eod_recon"):
            await run_eod_invariants(client=client, position_manager=pm)
        msgs = [r.message for r in caplog.records if "D231" in r.message]
        assert len(msgs) >= 1, f"expected D231 stop-drift warning; got: {[r.message for r in caplog.records]}"
        assert "XNDU" in msgs[0]
        # Both numbers present
        assert "32.44" in msgs[0]
        assert "25.72" in msgs[0]


# ── T5, T6: equity invariant ───────────────────────────────────────


class TestEodEquityInvariant:

    @pytest.mark.asyncio
    async def test_equity_within_tolerance(self, caplog):
        from src.monitoring.eod_recon import run_eod_invariants

        client = MagicMock()
        client.get_positions = AsyncMock(return_value=[])
        client.get_orders = AsyncMock(return_value=[])
        client.get_account = AsyncMock(return_value={"equity": "142520.59"})

        pm = MagicMock()
        pm.open_positions = []
        pm.starting_equity = 142520.59
        pm._daily_realized_pnl = 0.0

        with caplog.at_level(logging.WARNING, logger="src.monitoring.eod_recon"):
            result = await run_eod_invariants(client=client, position_manager=pm)
        assert result["equity_within_tolerance"] is True
        warnings = [r for r in caplog.records if "D230" in r.message]
        assert len(warnings) == 0

    @pytest.mark.asyncio
    async def test_equity_exceeds_tolerance_emits_d230(self, caplog):
        """Broker shows equity drift beyond the 0.5% band — soft warn (D230).
        doc 179 (M1): the tolerance is now max($1, 0.5% of equity) and the
        internal estimate marks-to-market open positions, so a small intraday
        unrealized swing no longer false-WARNs. A drift LARGER than 0.5% of
        equity (here ~2.5%, the size of a real position-tracking drift) must
        still emit D230. Equity drift is Tier-2 (soft), not Tier-1."""
        from src.monitoring.eod_recon import run_eod_invariants

        client = MagicMock()
        client.get_positions = AsyncMock(return_value=[])
        client.get_orders = AsyncMock(return_value=[])
        # ~2.5% below internal estimate — well beyond the 0.5% band.
        client.get_account = AsyncMock(return_value={"equity": "139000.00"})

        pm = MagicMock()
        pm.open_positions = []
        pm.starting_equity = 142520.59
        pm._daily_realized_pnl = 0.0   # internal expects unchanged
        pm._unrealized_pnl = 0.0       # no open MTM — isolate the drift

        with caplog.at_level(logging.WARNING, logger="src.monitoring.eod_recon"):
            await run_eod_invariants(client=client, position_manager=pm)
        msgs = [r.message for r in caplog.records if "D230" in r.message and "EQUITY" in r.message]
        assert len(msgs) >= 1, f"expected D230 equity drift warning; got: {[r.message for r in caplog.records]}"


# ── T7: non-fatal on broker outage ─────────────────────────────────


class TestEodNonFatalOnBrokerOutage:

    @pytest.mark.asyncio
    async def test_broker_unreachable_does_not_crash(self, caplog):
        from src.monitoring.eod_recon import run_eod_invariants

        client = MagicMock()
        client.get_positions = AsyncMock(side_effect=RuntimeError("API down"))
        client.get_orders = AsyncMock(side_effect=RuntimeError("API down"))
        client.get_account = AsyncMock(side_effect=RuntimeError("API down"))

        pm = MagicMock()
        pm.open_positions = []
        pm.starting_equity = 142520.59
        pm._daily_realized_pnl = 0.0

        with caplog.at_level(logging.WARNING, logger="src.monitoring.eod_recon"):
            result = await run_eod_invariants(client=client, position_manager=pm)
        assert result["broker_reachable"] is False
        # Must have logged the degraded state explicitly
        degraded = [r for r in caplog.records if "DEGRADED" in r.message]
        assert len(degraded) >= 1


# ── T8: Tier-1 escalation ──────────────────────────────────────────


class TestTier1Escalation:

    @pytest.mark.asyncio
    async def test_qty_mismatch_escalates_to_d231_not_d230(self, caplog):
        """Per registry: qty mismatch is Tier-1 (D231), equity drift is
        Tier-2 (D230). Make sure the codes are not swapped."""
        from src.monitoring.eod_recon import run_eod_invariants

        client = MagicMock()
        client.get_positions = AsyncMock(return_value=[
            {"symbol": "AGPU", "qty": "846"},
        ])
        client.get_orders = AsyncMock(return_value=[])
        client.get_account = AsyncMock(return_value={"equity": "142520.59"})

        pm = MagicMock()
        _pos = MagicMock()
        _pos.ticker = "AGPU"
        _pos.qty = 505
        _pos.stop_loss = 9.46
        _pos.stop_order_id = ""
        pm.open_positions = [_pos]
        pm.starting_equity = 142520.59
        pm._daily_realized_pnl = 0.0

        with caplog.at_level(logging.WARNING, logger="src.monitoring.eod_recon"):
            await run_eod_invariants(client=client, position_manager=pm)
        # Qty mismatch is Tier-1 → D231, not D230
        d231_qty = [
            r for r in caplog.records
            if "D231" in r.message and "QTY" in r.message
        ]
        d230_qty = [
            r for r in caplog.records
            if "D230" in r.message and "QTY" in r.message
        ]
        assert len(d231_qty) >= 1, (
            f"qty mismatch must emit D231 RECON_HARD_BLOCK QTY (Tier-1); "
            f"got: {[r.message for r in caplog.records]}"
        )
        # The QTY-tagged D230 code does not exist by design (qty is
        # Tier-1 only). Confirm we never emit one.
        assert len(d230_qty) == 0, (
            "qty mismatch must NOT emit D230 QTY (that code path is "
            "reserved for Tier-2 soft; qty is always Tier-1 D231)"
        )
