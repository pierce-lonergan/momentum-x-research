"""
Fri 2026-04-24 EOD failsafes tests — items 15, 28 + Bug Z (D242 force-close).

D241 EOD_SAFETY_CANCEL    — cancel orphan working orders
D242 EOD_FORCE_CLOSE      — force-close broker positions tracker doesn't know about
D238 EOD_RECONCILIATION   — broker-truth-first per-ticker P&L recon

Each failsafe is independent. Tests cover happy path + degraded
(broker outage) + ghost-detection cases including the LIDR Bug Z replay.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest


# ── D241 EOD_SAFETY_CANCEL ─────────────────────────────────────────


class TestD241SafetyCancel:

    @pytest.mark.asyncio
    async def test_clean_no_orphans_no_action(self, caplog):
        from src.monitoring.eod_failsafes import run_eod_safety_cancel

        client = MagicMock()
        client.get_orders = AsyncMock(return_value=[
            {"id": "expected-1", "symbol": "AAPL", "side": "sell",
             "qty": "100", "status": "new", "type": "stop"},
        ])
        client.cancel_order = AsyncMock()

        with caplog.at_level(logging.INFO, logger="src.monitoring.eod_failsafes"):
            result = await run_eod_safety_cancel(
                client=client, expected_order_ids={"expected-1"},
            )
        assert result["orphans_found"] == 0
        client.cancel_order.assert_not_awaited()
        assert any("D241" in r.message and "clean" in r.message.lower() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_orphan_orders_cancelled_with_d241(self, caplog):
        from src.monitoring.eod_failsafes import run_eod_safety_cancel

        client = MagicMock()
        client.get_orders = AsyncMock(return_value=[
            {"id": "expected-1", "symbol": "AAPL", "side": "sell",
             "qty": "100", "status": "new", "type": "stop"},
            {"id": "orphan-1", "symbol": "GHOST", "side": "sell",
             "qty": "200", "status": "new", "type": "limit"},
        ])
        client.cancel_order = AsyncMock(return_value={"status": "canceled"})

        with caplog.at_level(logging.INFO, logger="src.monitoring.eod_failsafes"):
            result = await run_eod_safety_cancel(
                client=client, expected_order_ids={"expected-1"},
            )
        assert result["orphans_found"] == 1
        assert result["cancels_succeeded"] == 1
        client.cancel_order.assert_awaited_once_with("orphan-1")
        d241 = [r for r in caplog.records if "D241" in r.message]
        assert len(d241) >= 1
        assert "GHOST" in d241[0].message or any("GHOST" in r.message for r in d241)

    @pytest.mark.asyncio
    async def test_cancel_failure_continues_to_next_orphan(self, caplog):
        from src.monitoring.eod_failsafes import run_eod_safety_cancel

        client = MagicMock()
        client.get_orders = AsyncMock(return_value=[
            {"id": "orphan-1", "symbol": "X", "side": "sell", "qty": "1", "status": "new", "type": "stop"},
            {"id": "orphan-2", "symbol": "Y", "side": "sell", "qty": "1", "status": "new", "type": "limit"},
        ])
        # First call fails, second succeeds
        client.cancel_order = AsyncMock(side_effect=[RuntimeError("API down"), {"status": "canceled"}])
        with caplog.at_level(logging.WARNING, logger="src.monitoring.eod_failsafes"):
            result = await run_eod_safety_cancel(client=client, expected_order_ids=set())
        assert result["orphans_found"] == 2
        assert result["cancels_succeeded"] == 1
        assert result["cancels_failed"] == 1

    @pytest.mark.asyncio
    async def test_broker_outage_degrades_gracefully(self):
        from src.monitoring.eod_failsafes import run_eod_safety_cancel

        client = MagicMock()
        client.get_orders = AsyncMock(side_effect=RuntimeError("API down"))
        result = await run_eod_safety_cancel(client=client)
        assert result["broker_reachable"] is False


# ── D242 EOD_FORCE_CLOSE ───────────────────────────────────────────


class TestD242ForceClose:

    @pytest.mark.asyncio
    async def test_lidr_bug_z_replay_force_closes(self, caplog):
        """Replay today's LIDR Bug Z: broker has LIDR qty=5264 with
        unrealized -$789, internal tracker has zero positions. D242
        must force market close + emit WARN."""
        from src.monitoring.eod_failsafes import run_eod_force_close

        client = MagicMock()
        client.get_positions = AsyncMock(return_value=[
            {"symbol": "LIDR", "qty": "5264", "side": "long",
             "unrealized_pl": "-789.60"},
        ])
        client.close_position = AsyncMock(return_value={"id": "force-close-1", "status": "accepted"})
        pm = MagicMock()
        pm.open_positions = []   # tracker thinks empty (Bug Z fake-close)

        with caplog.at_level(logging.WARNING, logger="src.monitoring.eod_failsafes"):
            result = await run_eod_force_close(client=client, position_manager=pm)
        assert result["ghost_positions"] == 1
        assert result["force_closes_succeeded"] == 1
        client.close_position.assert_awaited_once_with("LIDR")
        d242 = [r for r in caplog.records if "D242" in r.message]
        assert len(d242) >= 1
        assert "LIDR" in d242[0].message
        assert "5264" in d242[0].message

    @pytest.mark.asyncio
    async def test_clean_when_all_positions_tracked(self, caplog):
        from src.monitoring.eod_failsafes import run_eod_force_close

        client = MagicMock()
        client.get_positions = AsyncMock(return_value=[
            {"symbol": "AAPL", "qty": "100", "side": "long", "unrealized_pl": "+50"},
        ])
        client.close_position = AsyncMock()
        pm = MagicMock()
        _p = MagicMock(); _p.ticker = "AAPL"
        pm.open_positions = [_p]

        with caplog.at_level(logging.INFO, logger="src.monitoring.eod_failsafes"):
            result = await run_eod_force_close(client=client, position_manager=pm)
        assert result["ghost_positions"] == 0
        client.close_position.assert_not_awaited()
        assert any("D242" in r.message and "clean" in r.message.lower() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_close_failure_logs_error_continues(self, caplog):
        from src.monitoring.eod_failsafes import run_eod_force_close

        client = MagicMock()
        client.get_positions = AsyncMock(return_value=[
            {"symbol": "GHOST1", "qty": "100", "side": "long"},
            {"symbol": "GHOST2", "qty": "200", "side": "long"},
        ])
        client.close_position = AsyncMock(side_effect=[RuntimeError("403 Forbidden"), {"status": "accepted"}])
        pm = MagicMock(); pm.open_positions = []
        with caplog.at_level(logging.ERROR, logger="src.monitoring.eod_failsafes"):
            result = await run_eod_force_close(client=client, position_manager=pm)
        assert result["ghost_positions"] == 2
        assert result["force_closes_succeeded"] == 1
        assert result["force_closes_failed"] == 1


# ── D238 EOD_RECONCILIATION_DELTA ──────────────────────────────────


class TestD238BrokerTruthRecon:

    @pytest.mark.asyncio
    async def test_clean_within_dollar(self, caplog):
        from src.monitoring.eod_failsafes import run_eod_broker_truth_recon

        client = MagicMock()
        client.get_orders = AsyncMock(return_value=[
            {"symbol": "X", "side": "buy", "filled_qty": "100", "filled_avg_price": "10", "status": "filled"},
            {"symbol": "X", "side": "sell", "filled_qty": "100", "filled_avg_price": "10.50", "status": "filled"},
        ])
        with caplog.at_level(logging.INFO, logger="src.monitoring.eod_failsafes"):
            result = await run_eod_broker_truth_recon(
                client=client, journal_pnl=50.00, per_ticker_journal={"X": 50.00},
            )
        assert abs(result["delta_usd"]) < 0.5
        assert any("D238" in r.message and "clean" in r.message.lower() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_disagreement_emits_d238_with_breakdown(self, caplog):
        """Broker shows $171.73 realized on ONMD but journal claims
        $0 (Bug Y/Z scenario where the SMART_EXIT fake-close didn't
        reflect into the journal correctly)."""
        from src.monitoring.eod_failsafes import run_eod_broker_truth_recon

        client = MagicMock()
        client.get_orders = AsyncMock(return_value=[
            {"symbol": "ONMD", "side": "buy", "filled_qty": "17173", "filled_avg_price": "1.15", "status": "filled"},
            {"symbol": "ONMD", "side": "sell", "filled_qty": "17173", "filled_avg_price": "1.16", "status": "filled"},
        ])
        with caplog.at_level(logging.ERROR, logger="src.monitoring.eod_failsafes"):
            result = await run_eod_broker_truth_recon(
                client=client, journal_pnl=0.0, per_ticker_journal={},
            )
        assert result["broker_total_pnl"] == pytest.approx(171.73, abs=0.01)
        assert len(result["ticker_disagreements"]) == 1
        assert result["ticker_disagreements"][0]["ticker"] == "ONMD"
        d238 = [r for r in caplog.records if "D238" in r.message]
        assert len(d238) >= 1
        assert "ONMD" in d238[0].message


# ── Combined runner ────────────────────────────────────────────────


class TestRunAllEodFailsafes:

    @pytest.mark.asyncio
    async def test_combined_runner_calls_all_three(self):
        from src.monitoring.eod_failsafes import run_all_eod_failsafes

        client = MagicMock()
        client.get_positions = AsyncMock(return_value=[])
        client.get_orders = AsyncMock(return_value=[])
        client.close_position = AsyncMock()
        client.cancel_order = AsyncMock()
        pm = MagicMock(); pm.open_positions = []

        result = await run_all_eod_failsafes(
            client=client, position_manager=pm,
            journal_pnl=0.0, per_ticker_journal={},
        )
        assert result["force_close"] is not None
        assert result["safety_cancel"] is not None
        assert result["broker_truth_recon"] is not None
        # All clean
        assert result["force_close"]["ghost_positions"] == 0
        assert result["safety_cancel"]["orphans_found"] == 0
