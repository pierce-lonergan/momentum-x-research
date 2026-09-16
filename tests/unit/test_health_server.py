"""
Tests for HealthControlServer and TradingControlState.

Tests the control state management and HTTP endpoints.
"""

from __future__ import annotations

import json
import time
import urllib.request

import pytest

from src.scheduling.health_server import (
    HealthControlServer,
    TradingControlState,
)
from src.scheduling.heartbeat import HeartbeatWatchdog


class TestTradingControlState:
    def test_initial_state(self):
        state = TradingControlState()
        assert state.is_paused is False
        assert state.shutdown_requested is False

    def test_pause_toggle(self):
        state = TradingControlState()
        state.is_paused = True
        assert state.is_paused is True
        state.is_paused = False
        assert state.is_paused is False

    def test_shutdown_request(self):
        state = TradingControlState()
        state.shutdown_requested = True
        assert state.shutdown_requested is True

    def test_update_and_get_status(self):
        state = TradingControlState()
        state.update_status(
            phase="PHASE_3",
            positions=[{"ticker": "AAPL", "qty": 100}],
            daily_pnl=-150.0,
            trades_today=3,
        )
        status = state.get_status()
        assert status["phase"] == "PHASE_3"
        assert len(status["positions"]) == 1
        assert status["daily_pnl"] == -150.0
        assert status["trades_today"] == 3
        assert status["uptime_seconds"] >= 0
        assert "h" in status["uptime_human"]

    def test_partial_update(self):
        state = TradingControlState()
        state.update_status(phase="PHASE_1")
        state.update_status(daily_pnl=500.0)
        status = state.get_status()
        assert status["phase"] == "PHASE_1"
        assert status["daily_pnl"] == 500.0


class TestHealthControlServerIntegration:
    """Integration tests that start the HTTP server on an ephemeral port."""

    @pytest.fixture
    def server_with_state(self):
        """Start a health server on an available port."""
        state = TradingControlState()
        state.update_status(phase="PHASE_2", daily_pnl=100.0, trades_today=2)

        watchdog = HeartbeatWatchdog(timeout_minutes=30)
        watchdog.pulse(phase="PHASE_2")

        # Use high port to avoid conflicts
        port = 19091
        server = HealthControlServer(
            control_state=state,
            heartbeat=watchdog,
            port=port,
            auth_token="test-token-123",
        )
        server.start()
        time.sleep(0.2)  # Let server bind

        yield server, state, port

        server.stop()

    def test_health_endpoint_no_auth(self, server_with_state):
        """Health endpoint should work without auth."""
        _, _, port = server_with_state
        req = urllib.request.Request(f"http://127.0.0.1:{port}/health")
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read())
        assert data["status"] == "ok"
        assert "timestamp" in data
        assert "phase" in data
        assert "heartbeat" in data

    def test_status_endpoint_requires_auth(self, server_with_state):
        """Status endpoint should reject without auth."""
        _, _, port = server_with_state
        req = urllib.request.Request(f"http://127.0.0.1:{port}/status")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req, timeout=2)
        assert exc_info.value.code == 401

    def test_status_endpoint_with_auth(self, server_with_state):
        """Status endpoint should work with correct auth token."""
        _, _, port = server_with_state
        req = urllib.request.Request(f"http://127.0.0.1:{port}/status")
        req.add_header("X-Auth-Token", "test-token-123")
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read())
        assert data["phase"] == "PHASE_2"
        assert data["daily_pnl"] == 100.0
        assert data["trades_today"] == 2

    def test_pause_endpoint(self, server_with_state):
        """POST /pause should set paused state."""
        _, state, port = server_with_state
        assert state.is_paused is False

        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/pause",
            data=b"",
            method="POST",
        )
        req.add_header("X-Auth-Token", "test-token-123")
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read())
        assert data["action"] == "paused"
        assert state.is_paused is True

    def test_resume_endpoint(self, server_with_state):
        """POST /resume should clear paused state."""
        _, state, port = server_with_state
        state.is_paused = True

        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/resume",
            data=b"",
            method="POST",
        )
        req.add_header("X-Auth-Token", "test-token-123")
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read())
        assert data["action"] == "resumed"
        assert state.is_paused is False

    def test_shutdown_endpoint(self, server_with_state):
        """POST /shutdown should set shutdown flag."""
        _, state, port = server_with_state
        assert state.shutdown_requested is False

        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/shutdown",
            data=b"",
            method="POST",
        )
        req.add_header("X-Auth-Token", "test-token-123")
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read())
        assert data["action"] == "shutdown_requested"
        assert state.shutdown_requested is True

    def test_404_on_unknown_path(self, server_with_state):
        """Unknown paths should return 404."""
        _, _, port = server_with_state
        req = urllib.request.Request(f"http://127.0.0.1:{port}/unknown")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req, timeout=2)
        assert exc_info.value.code == 404


# ── D107 WS3: System Reliability Score ───────────────────────────


class TestReliabilityScore:
    """D107: Tests for system reliability score tracking."""

    def test_reliability_score_zero_sessions(self):
        """Returns 0.0 when no sessions have been recorded."""
        state = TradingControlState()
        assert state.reliability_score == 0.0

    def test_reliability_score_calculation(self):
        """3 sessions with trades, 1 without = 0.75."""
        state = TradingControlState()
        state.record_session(had_trades=True)
        state.record_session(had_trades=True)
        state.record_session(had_trades=True)
        state.record_session(had_trades=False)
        assert state.reliability_score == 0.75

    def test_record_session_thread_safety(self):
        """Concurrent calls don't corrupt counts."""
        import threading
        state = TradingControlState()

        def record_many(with_trades: bool, count: int):
            for _ in range(count):
                state.record_session(had_trades=with_trades)

        threads = [
            threading.Thread(target=record_many, args=(True, 50)),
            threading.Thread(target=record_many, args=(False, 50)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert state._sessions_attempted == 100
        assert state._sessions_with_trades == 50
        assert abs(state.reliability_score - 0.5) < 0.01

    def test_status_includes_reliability(self):
        """get_status() returns reliability fields."""
        state = TradingControlState()
        state.record_session(had_trades=True)
        state.record_session(had_trades=False)
        status = state.get_status()
        assert "reliability_score" in status
        assert "sessions_attempted" in status
        assert "sessions_with_trades" in status
        assert status["reliability_score"] == 0.5
        assert status["sessions_attempted"] == 2
        assert status["sessions_with_trades"] == 1
