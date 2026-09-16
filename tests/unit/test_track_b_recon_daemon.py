"""
Fri 2026-04-24 Track B — reconciliation daemon (items 14-20).

Per `25_bug_hunting_playbook.md` §3 + 2026-04-24 next-actions list:
  - Wraps run_eod_invariants in a 30s asyncio loop
  - Three escalation tiers: D230 warn, D231 hard-block, D232 lethal
  - Shadow mode for first week (logs decisions, takes no action)
  - Lethal threshold: 5% equity divergence OR qty mismatch >60s
  - Must NOT false-positive on benign partial-fill transients
    (e.g. Bug D's 846-vs-505 window during a normal partial fill)

Tests:
  T1 — shadow_mode_logs_but_does_not_block
  T2 — soft_drift_emits_d230_only
  T3 — qty_mismatch_emits_d231_blocks_new_entries
  T4 — sustained_qty_mismatch_>60s_escalates_to_d232_lethal
  T5 — benign_partial_fill_window_does_NOT_escalate (Bug D replay)
  T6 — equity_divergence_5pct_escalates_to_d232_lethal
  T7 — daemon_loop_runs_without_blocking_on_broker_outage
  T8 — daemon_writes_status_file_for_independent_observability
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest


# ── Helpers ────────────────────────────────────────────────────────


def _mk_pos(ticker, qty, stop_loss, stop_oid="stop-1"):
    pos = MagicMock()
    pos.ticker = ticker
    pos.qty = qty
    pos.stop_loss = stop_loss
    pos.stop_order_id = stop_oid
    return pos


def _mk_pm(positions, starting_equity=142000.00, realized=0.0):
    pm = MagicMock()
    pm.open_positions = positions
    pm.starting_equity = starting_equity
    pm._daily_realized_pnl = realized
    return pm


def _mk_client(broker_positions=None, broker_orders=None, equity="142000.00",
               raise_on_positions=False):
    client = MagicMock()
    if raise_on_positions:
        client.get_positions = AsyncMock(side_effect=RuntimeError("API down"))
    else:
        client.get_positions = AsyncMock(return_value=broker_positions or [])
    client.get_orders = AsyncMock(return_value=broker_orders or [])
    client.get_account = AsyncMock(return_value={"equity": equity})
    return client


# ── T1 — shadow mode ───────────────────────────────────────────────


class TestTrackB_ShadowMode:

    @pytest.mark.asyncio
    async def test_shadow_mode_logs_but_does_not_block(self, caplog):
        """Daemon in shadow_mode=True logs what it WOULD have done at
        each tier, but never sets the trading-blocked flag or invokes
        the lethal-tier flatten/halt."""
        from src.monitoring.recon_daemon import ReconDaemon, ReconState

        # Setup: qty mismatch (Tier-1 condition) — would normally D231
        client = _mk_client(
            broker_positions=[{"symbol": "AGPU", "qty": "846"}],
            broker_orders=[],
        )
        pm = _mk_pm([_mk_pos("AGPU", qty=505, stop_loss=9.46, stop_oid="")])

        state = ReconState()
        daemon = ReconDaemon(
            client=client, position_manager=pm,
            shadow_mode=True, state=state,
        )
        with caplog.at_level(logging.WARNING, logger="src.monitoring.recon_daemon"):
            await daemon.run_one_tick()

        # The D231 warning still fires (it's reporting the violation)
        assert any("D231" in r.message for r in caplog.records)
        # But shadow mode prefix appears in the action-log line
        shadow_logs = [r for r in caplog.records if "SHADOW" in r.message]
        assert any(shadow_logs), (
            f"shadow mode must annotate its decision with SHADOW marker; "
            f"got: {[r.message for r in caplog.records]}"
        )
        # State must NOT have set trading_blocked
        assert state.trading_blocked is False, (
            "shadow_mode must never set trading_blocked=True"
        )
        # State must NOT have set lethal_armed
        assert state.lethal_triggered is False


# ── T2/T3/T4 — escalation tiers ────────────────────────────────────


class TestTrackB_EscalationTiers:

    @pytest.mark.asyncio
    async def test_soft_drift_emits_d230_only(self, caplog):
        """Equity drift > $1 but no qty/stop violations → D230 only,
        no D231, trading not blocked."""
        from src.monitoring.recon_daemon import ReconDaemon, ReconState

        client = _mk_client(
            broker_positions=[],
            broker_orders=[],
            equity="141000.00",  # broker reports $1000 less than internal
        )
        pm = _mk_pm([], starting_equity=142000.00, realized=0.0)
        state = ReconState()
        daemon = ReconDaemon(client=client, position_manager=pm, shadow_mode=False, state=state)
        with caplog.at_level(logging.WARNING, logger="src.monitoring.recon_daemon"):
            await daemon.run_one_tick()
        # D230 fires
        assert any("D230" in r.message for r in caplog.records)
        # No D231/D232
        assert not any("D231" in r.message for r in caplog.records)
        assert not any("D232" in r.message for r in caplog.records)
        # Trading not blocked (Tier 2 is soft)
        assert state.trading_blocked is False

    @pytest.mark.asyncio
    async def test_qty_mismatch_emits_d231_blocks_new_entries(self, caplog):
        """Qty mismatch (Tier-1) → D231 fires and trading_blocked=True
        (existing positions untouched, new entries denied)."""
        from src.monitoring.recon_daemon import ReconDaemon, ReconState

        client = _mk_client(
            broker_positions=[{"symbol": "AGPU", "qty": "846"}],
            broker_orders=[],
        )
        pm = _mk_pm([_mk_pos("AGPU", qty=505, stop_loss=9.46, stop_oid="")])
        state = ReconState()
        daemon = ReconDaemon(client=client, position_manager=pm, shadow_mode=False, state=state)
        with caplog.at_level(logging.WARNING, logger="src.monitoring.recon_daemon"):
            await daemon.run_one_tick()
        assert any("D231" in r.message for r in caplog.records)
        assert state.trading_blocked is True
        # Lethal not triggered yet (need >60s sustained)
        assert state.lethal_triggered is False

    @pytest.mark.asyncio
    async def test_sustained_qty_mismatch_escalates_to_d232_lethal(self, caplog):
        """Same qty mismatch persisting across multiple ticks for >60s
        → D232 lethal (cancel all + flat all + halt)."""
        from src.monitoring.recon_daemon import ReconDaemon, ReconState

        client = _mk_client(
            broker_positions=[{"symbol": "AGPU", "qty": "846"}],
            broker_orders=[],
        )
        pm = _mk_pm([_mk_pos("AGPU", qty=505, stop_loss=9.46, stop_oid="")])
        state = ReconState()
        daemon = ReconDaemon(
            client=client, position_manager=pm,
            shadow_mode=False, state=state,
            # Use a tiny lethal threshold so the test doesn't need to
            # actually wait 60s.
            lethal_qty_drift_seconds=2.0,
        )
        # Tick 1: violation observed, blocked, not yet lethal
        await daemon.run_one_tick()
        assert state.trading_blocked is True
        assert state.lethal_triggered is False

        # Wait past the lethal threshold
        await asyncio.sleep(2.1)

        # Tick 2: violation still present + duration exceeded → lethal
        with caplog.at_level(logging.ERROR, logger="src.monitoring.recon_daemon"):
            await daemon.run_one_tick()
        assert state.lethal_triggered is True
        assert any("D232" in r.message for r in caplog.records)


# ── T5 — benign partial-fill (Bug D 846/505 transient) ────────────


class TestTrackB_BenignPartialFillNoEscalation:

    @pytest.mark.asyncio
    async def test_brief_qty_mismatch_during_partial_fill_does_not_escalate(self, caplog):
        """Bug D scenario at submit+2s: broker has filled 505 of 846,
        bridge tracks 505 (the seen partial). They AGREE — no
        violation. Then bridge updates to 846 after terminal fill.
        Critical: the daemon must NOT D232-escalate during this window."""
        from src.monitoring.recon_daemon import ReconDaemon, ReconState

        # Tick 1: both broker and bridge see 505 (mid partial fill)
        client_t1 = _mk_client(
            broker_positions=[{"symbol": "AGPU", "qty": "505"}],
            broker_orders=[],
        )
        pm_t1 = _mk_pm([_mk_pos("AGPU", qty=505, stop_loss=9.46, stop_oid="")])

        state = ReconState()
        daemon = ReconDaemon(
            client=client_t1, position_manager=pm_t1,
            shadow_mode=False, state=state,
            lethal_qty_drift_seconds=2.0,
        )
        with caplog.at_level(logging.WARNING, logger="src.monitoring.recon_daemon"):
            await daemon.run_one_tick()

        # No violations because both sides agree on 505
        assert state.trading_blocked is False
        assert not any("D231" in r.message for r in caplog.records)

        # Tick 2 (a few seconds later): both see 846 (terminal fill)
        client_t2 = _mk_client(
            broker_positions=[{"symbol": "AGPU", "qty": "846"}],
            broker_orders=[],
        )
        pm_t2 = _mk_pm([_mk_pos("AGPU", qty=846, stop_loss=9.46, stop_oid="")])
        daemon._client = client_t2
        daemon._pm = pm_t2

        await daemon.run_one_tick()
        assert state.trading_blocked is False
        assert state.lethal_triggered is False


# ── T6 — equity divergence lethal threshold ────────────────────────


class TestTrackB_EquityLethalThreshold:

    @pytest.mark.asyncio
    async def test_equity_divergence_5pct_escalates_to_d232_lethal(self, caplog):
        """Broker equity 5% below internal estimate → D232 lethal
        immediately (no need to wait — the magnitude itself is the
        signal)."""
        from src.monitoring.recon_daemon import ReconDaemon, ReconState

        # Internal estimate: $142,000 starting + $0 realized = $142,000
        # Broker: $134,000 = -5.6% drift
        client = _mk_client(
            broker_positions=[],
            broker_orders=[],
            equity="134000.00",
        )
        pm = _mk_pm([], starting_equity=142000.00, realized=0.0)
        state = ReconState()
        daemon = ReconDaemon(
            client=client, position_manager=pm,
            shadow_mode=False, state=state,
            lethal_equity_drift_pct=0.05,    # 5% threshold
        )
        with caplog.at_level(logging.ERROR, logger="src.monitoring.recon_daemon"):
            await daemon.run_one_tick()
        assert state.lethal_triggered is True
        assert any("D232" in r.message for r in caplog.records)
        # The lethal log MUST mention the equity-drift cause for triage
        d232_logs = [r for r in caplog.records if "D232" in r.message]
        assert any("EQUITY" in r.message or "equity" in r.message
                   for r in d232_logs)


# ── T7 — broker outage ────────────────────────────────────────────


class TestTrackB_NonFatalOnBrokerOutage:

    @pytest.mark.asyncio
    async def test_daemon_does_not_crash_on_broker_outage(self, caplog):
        """If the broker is unreachable, daemon must log degraded and
        keep running. Trading state unchanged (we cannot prove violation
        → cannot block; we cannot prove safety → cannot unblock)."""
        from src.monitoring.recon_daemon import ReconDaemon, ReconState

        client = _mk_client(raise_on_positions=True)
        pm = _mk_pm([])
        state = ReconState()
        daemon = ReconDaemon(client=client, position_manager=pm, shadow_mode=False, state=state)
        with caplog.at_level(logging.WARNING, logger="src.monitoring.recon_daemon"):
            await daemon.run_one_tick()
        # Did not crash
        # Did NOT trigger lethal (cannot lethal-act without confirmed truth)
        assert state.lethal_triggered is False


# ── T8 — independent status file ──────────────────────────────────


class TestTrackB_StatusFile:

    @pytest.mark.asyncio
    async def test_daemon_writes_status_file(self, tmp_path):
        """Daemon writes a JSON status file every tick so external
        process / dashboard / Discord webhook can read state without
        the trading loop being involved."""
        import json
        from src.monitoring.recon_daemon import ReconDaemon, ReconState

        client = _mk_client(broker_positions=[], broker_orders=[], equity="142000.00")
        pm = _mk_pm([], starting_equity=142000.00, realized=0.0)
        state = ReconState()
        status_path = tmp_path / "recon_status.json"
        daemon = ReconDaemon(
            client=client, position_manager=pm, shadow_mode=False, state=state,
            status_file=str(status_path),
        )
        await daemon.run_one_tick()

        assert status_path.exists()
        data = json.loads(status_path.read_text())
        # Required keys for an independent observer to make decisions
        assert "tick_time_utc" in data
        assert "tier" in data           # one of "ok", "soft", "hard", "lethal"
        assert "trading_blocked" in data
        assert "lethal_triggered" in data
        assert "shadow_mode" in data
        assert "qty_drift_count" in data
        assert "equity_drift_usd" in data
        assert data["tier"] == "ok"  # clean state in this test
