"""D202: Tests for Ensemble Agent multi-call signal averaging."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from src.agents.ensemble import EnsembleWrapper, _SIGNAL_STRENGTH
from src.core.models import AgentSignal


def _make_signal(
    signal: str = "BULL",
    confidence: float = 0.6,
    agent_id: str = "news_agent",
    reasoning: str = "test",
    **kwargs,
) -> AgentSignal:
    return AgentSignal(
        agent_id=agent_id,
        ticker="TEST",
        timestamp=datetime.now(timezone.utc),
        signal=signal,
        confidence=confidence,
        reasoning=reasoning,
        model_id="test_model",
        latency_ms=100.0,
        **kwargs,
    )


class TestEnsembleAggregation:
    """Test signal aggregation logic."""

    def _make_wrapper(self, signals: list[AgentSignal], n_calls: int = 3):
        """Create wrapper with mock agent returning given signals in sequence."""
        agent = AsyncMock()
        agent.agent_id = "news_agent"
        call_count = 0

        async def side_effect(*args, **kwargs):
            nonlocal call_count
            idx = min(call_count, len(signals) - 1)
            call_count += 1
            return signals[idx]

        agent.analyze = AsyncMock(side_effect=side_effect)
        return EnsembleWrapper(agent, n_calls=n_calls, min_quorum=2, intra_stagger_ms=0)

    @pytest.mark.asyncio
    async def test_unanimous_bull(self):
        """3/3 BULL → BULL with full agreement."""
        signals = [
            _make_signal("BULL", 0.7),
            _make_signal("BULL", 0.8),
            _make_signal("BULL", 0.6),
        ]
        wrapper = self._make_wrapper(signals)
        result = await wrapper.analyze(ticker="TEST")

        assert result.signal == "BULL"
        # Agreement = 3/3 = 100%
        # Mean confidence = (0.7 + 0.8 + 0.6) / 3 = 0.7
        # Adjusted = 0.7 * 1.0 = 0.7
        assert result.confidence == pytest.approx(0.7, rel=0.01)
        assert "D202_ENSEMBLE_N=3/3" in str(result.flags)
        assert "D202_AGREEMENT=100%" in str(result.flags)

    @pytest.mark.asyncio
    async def test_majority_bull_one_bear(self):
        """2/3 BULL, 1/3 BEAR → BULL with reduced confidence."""
        signals = [
            _make_signal("BULL", 0.7),
            _make_signal("BULL", 0.6),
            _make_signal("BEAR", 0.5),
        ]
        wrapper = self._make_wrapper(signals)
        result = await wrapper.analyze(ticker="TEST")

        assert result.signal == "BULL"
        # Agreement = 2/3 = 67%
        # Mean confidence = (0.7 + 0.6 + 0.5) / 3 = 0.6
        # Adjusted = 0.6 * 0.667 = 0.4
        assert result.confidence == pytest.approx(0.4, rel=0.05)

    @pytest.mark.asyncio
    async def test_unanimous_bear(self):
        """3/3 BEAR → BEAR."""
        signals = [
            _make_signal("BEAR", 0.8),
            _make_signal("BEAR", 0.7),
            _make_signal("BEAR", 0.9),
        ]
        wrapper = self._make_wrapper(signals)
        result = await wrapper.analyze(ticker="TEST")

        assert result.signal == "BEAR"
        assert result.confidence == pytest.approx(0.8, rel=0.05)

    @pytest.mark.asyncio
    async def test_split_vote_goes_neutral(self):
        """1 BULL + 1 BEAR + 1 NEUTRAL → NEUTRAL (conservative)."""
        signals = [
            _make_signal("BULL", 0.6),
            _make_signal("BEAR", 0.6),
            _make_signal("NEUTRAL", 0.0),
        ]
        wrapper = self._make_wrapper(signals)
        result = await wrapper.analyze(ticker="TEST")

        assert result.signal == "NEUTRAL"

    @pytest.mark.asyncio
    async def test_n_calls_1_bypasses_ensemble(self):
        """n_calls=1 passes through directly (no ensemble overhead)."""
        sig = _make_signal("STRONG_BULL", 0.9)
        agent = AsyncMock()
        agent.agent_id = "news_agent"
        agent.analyze = AsyncMock(return_value=sig)
        wrapper = EnsembleWrapper(agent, n_calls=1)

        result = await wrapper.analyze(ticker="TEST")
        assert result.signal == "STRONG_BULL"
        assert result.confidence == 0.9

    @pytest.mark.asyncio
    async def test_partial_failure_still_aggregates(self):
        """2/3 calls succeed → aggregate with quorum flag."""
        agent = AsyncMock()
        agent.agent_id = "news_agent"
        call_count = 0

        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise TimeoutError("LLM timeout")
            return _make_signal("BULL", 0.7)

        agent.analyze = AsyncMock(side_effect=side_effect)
        wrapper = EnsembleWrapper(agent, n_calls=3, min_quorum=2, intra_stagger_ms=0)
        result = await wrapper.analyze(ticker="TEST")

        assert result.signal == "BULL"
        assert "D202_ENSEMBLE_N=2/3" in str(result.flags)

    @pytest.mark.asyncio
    async def test_all_fail_returns_neutral(self):
        """All calls fail → NEUTRAL with error flag."""
        agent = AsyncMock()
        agent.agent_id = "news_agent"
        agent.analyze = AsyncMock(side_effect=TimeoutError("boom"))
        wrapper = EnsembleWrapper(agent, n_calls=3, min_quorum=2, intra_stagger_ms=0)

        result = await wrapper.analyze(ticker="TEST")
        assert result.signal == "NEUTRAL"
        assert result.confidence == 0.0
        assert "D202_ENSEMBLE_ALL_FAILED" in str(result.flags)

    @pytest.mark.asyncio
    async def test_single_success_below_quorum(self):
        """1/3 succeed (below quorum=2) → returns single result with NO_QUORUM."""
        agent = AsyncMock()
        agent.agent_id = "news_agent"
        call_count = 0

        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_signal("BULL", 0.7)
            raise TimeoutError("fail")

        agent.analyze = AsyncMock(side_effect=side_effect)
        wrapper = EnsembleWrapper(agent, n_calls=3, min_quorum=2, intra_stagger_ms=0)
        result = await wrapper.analyze(ticker="TEST")

        assert result.signal == "BULL"
        assert "D202_NO_QUORUM" in str(result.flags)


class TestEnsembleConfig:
    """Test ensemble configuration from settings."""

    def test_default_enabled(self):
        from config.settings import ModelConfig
        config = ModelConfig()
        assert config.ensemble_enabled is True
        assert config.ensemble_n_calls == 3
        assert config.ensemble_min_quorum == 2

    def test_disable_via_env(self, monkeypatch):
        monkeypatch.setenv("LLM_ENSEMBLE_ENABLED", "false")
        from config.settings import ModelConfig
        config = ModelConfig()
        assert config.ensemble_enabled is False


class TestSignalStrength:
    """Test the signal strength ordering."""

    def test_ordering(self):
        assert _SIGNAL_STRENGTH["STRONG_BULL"] > _SIGNAL_STRENGTH["BULL"]
        assert _SIGNAL_STRENGTH["BULL"] > _SIGNAL_STRENGTH["NEUTRAL"]
        assert _SIGNAL_STRENGTH["NEUTRAL"] > _SIGNAL_STRENGTH["BEAR"]
        assert _SIGNAL_STRENGTH["BEAR"] > _SIGNAL_STRENGTH["STRONG_BEAR"]


class TestEnsembleConsistency:
    """Test that ensemble reduces variance (the core purpose)."""

    @pytest.mark.asyncio
    async def test_noisy_agent_stabilized(self):
        """An agent that flip-flops 50/50 → ensemble picks the majority."""
        # Simulate noisy agent: alternates BULL/BEAR
        agent = AsyncMock()
        agent.agent_id = "noisy_agent"
        call_count = 0

        async def noisy_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count % 2 == 1:
                return _make_signal("BULL", 0.6)
            return _make_signal("BEAR", 0.5)

        agent.analyze = AsyncMock(side_effect=noisy_side_effect)
        wrapper = EnsembleWrapper(agent, n_calls=3, min_quorum=2, intra_stagger_ms=0)

        # With 3 calls: 2 BULL + 1 BEAR → majority BULL
        result = await wrapper.analyze(ticker="TEST")
        assert result.signal == "BULL"
        # Agreement is only 67%, so confidence is reduced
        assert result.confidence < 0.6  # Mean conf * agreement

    @pytest.mark.asyncio
    async def test_ensemble_reduces_confidence_on_disagreement(self):
        """When agents disagree, adjusted confidence drops."""
        signals = [
            _make_signal("BULL", 0.9),
            _make_signal("BULL", 0.8),
            _make_signal("BEAR", 0.7),
        ]
        wrapper = self._make_wrapper_from_signals(signals)
        result = await wrapper.analyze(ticker="TEST")

        # Mean confidence = (0.9 + 0.8 + 0.7) / 3 = 0.8
        # Agreement = 2/3 = 0.667
        # Adjusted = 0.8 * 0.667 = 0.533
        assert result.confidence < 0.8  # Reduced from mean
        assert result.confidence > 0.0  # But not zero

    def _make_wrapper_from_signals(self, signals):
        agent = AsyncMock()
        agent.agent_id = "test_agent"
        call_idx = 0

        async def se(*args, **kwargs):
            nonlocal call_idx
            idx = min(call_idx, len(signals) - 1)
            call_idx += 1
            return signals[idx]

        agent.analyze = AsyncMock(side_effect=se)
        return EnsembleWrapper(agent, n_calls=len(signals), min_quorum=2, intra_stagger_ms=0)
