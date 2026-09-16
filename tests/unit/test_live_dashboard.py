"""
Tests for LiveDashboard (D67).

S038 WS3: Live terminal status display for paper trading sessions.
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import patch, MagicMock

from src.monitoring.live_dashboard import LiveDashboard
from src.monitoring.metrics import reset_metrics, get_metrics


@pytest.fixture(autouse=True)
def _clean_metrics():
    reset_metrics()
    yield
    reset_metrics()


class TestLiveDashboard:
    """LiveDashboard unit tests."""

    def test_toggle_flips_enabled(self):
        d = LiveDashboard(enabled=True)
        assert d.enabled is True
        result = d.toggle()
        assert result is False
        assert d.enabled is False
        result = d.toggle()
        assert result is True
        assert d.enabled is True

    def test_render_status_returns_string(self):
        d = LiveDashboard()
        status = d.render_status()
        assert isinstance(status, str)
        assert "P&L=" in status
        assert "Trades=" in status
        assert "Open=" in status
        assert "Evals=" in status
        assert "AgentLatency=" in status

    def test_render_status_reflects_metrics(self):
        m = get_metrics()
        m.daily_pnl.set(1234.56)
        m.session_trades.inc(5)
        m.evaluations_total.inc(10)

        d = LiveDashboard()
        status = d.render_status()
        assert "P&L=$+1234.56" in status
        assert "Trades=5" in status
        assert "Evals=10" in status

    def test_print_status_does_not_crash(self):
        d = LiveDashboard()
        d._print_status()  # Should not raise

    def test_print_status_with_position_manager(self):
        mock_pm = MagicMock()
        mock_pos = MagicMock()
        mock_pos.ticker = "AAPL"
        mock_pos.remaining_qty = 100
        mock_pos.entry_price = 150.0
        mock_pos.stop_loss = 145.0
        mock_pos.tranches_filled = 1
        mock_pm.open_positions = [mock_pos]

        d = LiveDashboard(position_manager=mock_pm)
        d._print_status()  # Should not crash

    def test_print_status_with_broken_pm(self):
        """Dashboard should not crash if position manager raises."""
        mock_pm = MagicMock()
        mock_pm.open_positions = property(lambda self: (_ for _ in ()).throw(RuntimeError("broken")))

        d = LiveDashboard(position_manager=mock_pm)
        d._print_status()  # Should not raise

    @pytest.mark.asyncio
    async def test_run_exits_on_shutdown(self):
        shutdown = asyncio.Event()
        d = LiveDashboard(interval_seconds=1, enabled=False)

        # Set shutdown immediately
        shutdown.set()
        await asyncio.wait_for(d.run(shutdown), timeout=2.0)
        # Should return without hanging

    @pytest.mark.asyncio
    async def test_run_disabled_does_not_render(self):
        shutdown = asyncio.Event()
        d = LiveDashboard(interval_seconds=1, enabled=False)

        # Let it run for a brief moment then shutdown
        async def stop_soon():
            await asyncio.sleep(0.1)
            shutdown.set()

        await asyncio.gather(d.run(shutdown), stop_soon())
        # If enabled=False, _print_status should not be called (no crash test)
