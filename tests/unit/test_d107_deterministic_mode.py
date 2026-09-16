"""
Tests for D107 WS6: Deterministic-Only Mode.

Covers:
  - Config flag wiring
  - Deterministic-only produces a TradeVerdict with zero LLM calls
  - Synthetic RVOL signal generation
  - CLI flag propagation
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from config.settings import Settings, ExecutionConfig
from src.core.models import CandidateStock, AgentSignal


def _make_candidate(**kwargs) -> CandidateStock:
    defaults = {
        "ticker": "TEST",
        "current_price": 10.0,
        "previous_close": 9.09,
        "gap_pct": 0.10,
        "gap_classification": "SIGNIFICANT",
        "rvol": 5.0,
        "float_shares": 10_000_000,
        "market_cap": 100_000_000,
        "premarket_volume": 500_000,
        "volume": 500_000,
        "avg_volume": 100_000,
        "scan_timestamp": datetime.now(timezone.utc),
        "scan_phase": "PRE_MARKET",
    }
    defaults.update(kwargs)
    return CandidateStock(**defaults)


class TestDeterministicOnlyConfig:
    """Config flag and CLI wiring tests."""

    def test_deterministic_only_default_false(self):
        """Default config has deterministic_only=False."""
        config = ExecutionConfig()
        assert config.deterministic_only is False

    def test_deterministic_only_set_true(self):
        """Can set deterministic_only=True."""
        config = ExecutionConfig(deterministic_only=True)
        assert config.deterministic_only is True

    def test_settings_execution_deterministic(self):
        """Root Settings propagates deterministic_only."""
        settings = Settings()
        assert settings.execution.deterministic_only is False
        settings.execution.deterministic_only = True
        assert settings.execution.deterministic_only is True


class TestDeterministicOnlyMode:
    """Tests that deterministic mode skips LLM and produces valid output."""

    @pytest.fixture
    def settings(self) -> Settings:
        s = Settings()
        s.execution.deterministic_only = True
        return s

    @pytest.mark.asyncio
    async def test_deterministic_only_produces_verdict(self, settings):
        """Deterministic-only mode should produce a TradeVerdict."""
        from src.core.orchestrator import Orchestrator

        orch = Orchestrator(settings=settings)
        # Mock the data client and Alpaca client to prevent real API calls
        orch._data_client = None
        orch._sec_client = None

        candidate = _make_candidate(ticker="DTEST", rvol=5.0)

        verdict = await orch.evaluate_candidate(
            candidate=candidate,
            news_items=[],
            market_data={},
        )

        assert verdict is not None
        assert verdict.ticker == "DTEST"
        # Should have a verdict (BUY, NO_TRADE, or HOLD)
        assert verdict.action in ("STRONG_BUY", "BUY", "NO_TRADE", "HOLD")

    @pytest.mark.asyncio
    async def test_deterministic_only_skips_llm(self, settings):
        """No LLM agent.analyze() calls in deterministic mode."""
        from src.core.orchestrator import Orchestrator

        orch = Orchestrator(settings=settings)
        orch._data_client = None
        orch._sec_client = None

        candidate = _make_candidate(ticker="NOLLM", rvol=4.0)

        # Track if _dispatch_agents is called (it shouldn't be)
        dispatch_called = False
        original_dispatch = orch._dispatch_agents

        async def mock_dispatch(*args, **kwargs):
            nonlocal dispatch_called
            dispatch_called = True
            return await original_dispatch(*args, **kwargs)

        orch._dispatch_agents = mock_dispatch

        verdict = await orch.evaluate_candidate(
            candidate=candidate,
            news_items=[],
            market_data={},
        )

        assert not dispatch_called, "_dispatch_agents should not be called in deterministic mode"

    @pytest.mark.asyncio
    async def test_deterministic_only_rvol_signal(self, settings):
        """Synthetic RVOL signal should reflect candidate RVOL."""
        from src.core.orchestrator import Orchestrator

        orch = Orchestrator(settings=settings)
        orch._data_client = None
        orch._sec_client = None

        # High RVOL candidate
        candidate = _make_candidate(ticker="HVOL", rvol=8.0)
        verdict = await orch.evaluate_candidate(
            candidate=candidate,
            news_items=[],
            market_data={},
        )

        # Check that agent_signals were recorded
        signals = orch._last_agent_signals
        rvol_signals = [s for s in signals if s.agent_id == "rvol_synthetic"]
        assert len(rvol_signals) == 1
        assert rvol_signals[0].signal == "BULL"
        assert rvol_signals[0].confidence == 0.8  # min(1.0, 8.0/10.0)

    @pytest.mark.asyncio
    async def test_deterministic_only_low_rvol_neutral(self, settings):
        """Low RVOL produces NEUTRAL synthetic signal."""
        from src.core.orchestrator import Orchestrator

        orch = Orchestrator(settings=settings)
        orch._data_client = None
        orch._sec_client = None

        candidate = _make_candidate(ticker="LVOL", rvol=2.0)
        verdict = await orch.evaluate_candidate(
            candidate=candidate,
            news_items=[],
            market_data={},
        )

        signals = orch._last_agent_signals
        rvol_signals = [s for s in signals if s.agent_id == "rvol_synthetic"]
        assert len(rvol_signals) == 1
        assert rvol_signals[0].signal == "NEUTRAL"
