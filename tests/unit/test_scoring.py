"""
MOMENTUM-X Unit Tests: Scoring Engine

### TEST PHILOSOPHY (TR-P §III.3)
Verify that MFCS computation matches MOMENTUM_LOGIC.md §5 formula exactly.

MFCS(S, t) = Σ w_k · σ_k(S, t) - λ · RISK(S, t)

D101: Updated for bipolar score mapping where BEAR=-0.5, STRONG_BEAR=-1.0.
MFCS can now be negative (clamped to [-1, 1]).
"""

from datetime import datetime, timezone

import pytest

from src.core.models import AgentSignal, CandidateStock, RiskSignal
from src.core.scoring import compute_mfcs, signal_to_score, SIGNAL_NUMERIC


class TestSignalToScore:
    """Test the signal direction → numeric mapping."""

    def test_strong_bull_full_confidence(self):
        """STRONG_BULL with confidence=1.0 should score 1.0."""
        signal = _make_signal("test", "XYZ", "STRONG_BULL", 1.0)
        assert signal_to_score(signal) == 1.0

    def test_strong_bear_full_confidence(self):
        """D101: STRONG_BEAR with confidence=1.0 should score -1.0 (was 0.0)."""
        signal = _make_signal("test", "XYZ", "STRONG_BEAR", 1.0)
        assert signal_to_score(signal) == -1.0

    def test_bear_full_confidence(self):
        """D101: BEAR with confidence=1.0 should score -0.5."""
        signal = _make_signal("test", "XYZ", "BEAR", 1.0)
        assert signal_to_score(signal) == -0.5

    def test_neutral_half_confidence(self):
        """D100: NEUTRAL with confidence=0.5 should score 0.0 × 0.5 = 0.0."""
        signal = _make_signal("test", "XYZ", "NEUTRAL", 0.5)
        assert signal_to_score(signal) == 0.0

    def test_bull_scales_with_confidence(self):
        """D101/D116: BULL direction (0.5) × confidence, with 0.20 floor for non-NEUTRAL."""
        for conf in [0.25, 0.5, 0.75, 1.0]:
            signal = _make_signal("test", "XYZ", "BULL", conf)
            expected = 0.5 * conf  # D101: BULL=0.5 (was 0.7)
            assert abs(signal_to_score(signal) - expected) < 1e-10
        # D116: conf=0.0 with directional signal → floor to 0.20
        signal = _make_signal("test", "XYZ", "BULL", 0.0)
        assert abs(signal_to_score(signal) - 0.5 * 0.20) < 1e-10

    def test_bear_scales_with_confidence(self):
        """D101/D116: BEAR direction (-0.5) × confidence, with 0.20 floor for non-NEUTRAL."""
        for conf in [0.25, 0.5, 0.75, 1.0]:
            signal = _make_signal("test", "XYZ", "BEAR", conf)
            expected = -0.5 * conf
            assert abs(signal_to_score(signal) - expected) < 1e-10
        # D116: conf=0.0 with directional signal → floor to 0.20
        signal = _make_signal("test", "XYZ", "BEAR", 0.0)
        assert abs(signal_to_score(signal) - (-0.5 * 0.20)) < 1e-10

    def test_bipolar_symmetry(self):
        """D101: BULL and BEAR at same confidence should be symmetric around zero."""
        bull = _make_signal("test", "XYZ", "BULL", 0.8)
        bear = _make_signal("test", "XYZ", "BEAR", 0.8)
        assert abs(signal_to_score(bull) + signal_to_score(bear)) < 1e-10

    def test_bipolar_signal_numeric_values(self):
        """D101: Verify the exact SIGNAL_NUMERIC mapping."""
        assert SIGNAL_NUMERIC["STRONG_BULL"] == 1.0
        assert SIGNAL_NUMERIC["BULL"] == 0.5
        assert SIGNAL_NUMERIC["NEUTRAL"] == 0.0
        assert SIGNAL_NUMERIC["BEAR"] == -0.5
        assert SIGNAL_NUMERIC["STRONG_BEAR"] == -1.0


class TestMFCS:
    """Test the full MFCS computation."""

    @pytest.fixture
    def candidate(self) -> CandidateStock:
        return CandidateStock(
            ticker="TEST",
            current_price=10.0,
            previous_close=8.0,
            gap_pct=0.25,
            gap_classification="EXPLOSIVE",
            rvol=5.0,
            premarket_volume=500_000,
            scan_timestamp=datetime.now(timezone.utc),
            scan_phase="PRE_MARKET",
        )

    def test_all_strong_bull_max_score(self, candidate):
        """All STRONG_BULL signals with zero risk should approach max score."""
        signals = [
            _make_signal("news_agent", "TEST", "STRONG_BULL", 1.0),
            _make_signal("technical_agent", "TEST", "STRONG_BULL", 1.0),
            _make_signal("volume_agent", "TEST", "STRONG_BULL", 1.0),
            _make_signal("fundamental_agent", "TEST", "STRONG_BULL", 1.0),
            _make_signal("institutional_agent", "TEST", "STRONG_BULL", 1.0),
            _make_signal("deep_search_agent", "TEST", "STRONG_BULL", 1.0),
            _make_risk_signal("risk_agent", "TEST", risk_score=0.0),
        ]
        result = compute_mfcs(candidate, signals)
        # All weights sum to 1.0, all scores are 1.0, risk = 0
        # MFCS = 1.0 - 0.3 × 0.0 = 1.0
        assert result.mfcs == 1.0
        assert result.qualifies_for_debate is True

    def test_all_bearish_negative_score(self, candidate):
        """D101: All STRONG_BEAR signals should produce NEGATIVE score."""
        signals = [
            _make_signal("news_agent", "TEST", "STRONG_BEAR", 1.0),
            _make_signal("technical_agent", "TEST", "STRONG_BEAR", 1.0),
            _make_signal("volume_agent", "TEST", "STRONG_BEAR", 1.0),
            _make_signal("fundamental_agent", "TEST", "STRONG_BEAR", 1.0),
            _make_signal("institutional_agent", "TEST", "STRONG_BEAR", 1.0),
            _make_signal("deep_search_agent", "TEST", "STRONG_BEAR", 1.0),
            _make_risk_signal("risk_agent", "TEST", risk_score=0.9),
        ]
        result = compute_mfcs(candidate, signals)
        # D101: All direction scores are -1.0, risk penalty = 0.3 × 0.9 = 0.27
        # weighted_sum = -1.0 (all agents score -1.0)
        # MFCS = -1.0 - 0.27 = -1.27, clamped to -1.0
        assert result.mfcs == -1.0
        assert result.qualifies_for_debate is False

    def test_bearish_consensus_goes_negative(self, candidate):
        """D101: Bearish signals should drive MFCS below zero, not clamp at zero."""
        signals = [
            _make_signal("news_agent", "TEST", "BEAR", 0.7),
            _make_signal("technical_agent", "TEST", "BEAR", 0.8),
            _make_risk_signal("risk_agent", "TEST", risk_score=0.5),
        ]
        result = compute_mfcs(candidate, signals)
        # Bearish signals produce negative component scores → MFCS should be negative
        assert result.mfcs < 0.0, f"MFCS {result.mfcs} should be negative with bearish consensus"
        assert result.qualifies_for_debate is False

    def test_risk_penalty_applied(self, candidate):
        """High risk score should reduce MFCS per §5 formula."""
        signals_low_risk = [
            _make_signal("news_agent", "TEST", "BULL", 0.8),
            _make_risk_signal("risk_agent", "TEST", risk_score=0.1),
        ]
        signals_high_risk = [
            _make_signal("news_agent", "TEST", "BULL", 0.8),
            _make_risk_signal("risk_agent", "TEST", risk_score=0.9),
        ]
        result_low = compute_mfcs(candidate, signals_low_risk)
        result_high = compute_mfcs(candidate, signals_high_risk)
        assert result_low.mfcs > result_high.mfcs

    def test_debate_threshold(self, candidate):
        """Only candidates above threshold qualify for debate."""
        # Moderate signals — should be near threshold
        signals = [
            _make_signal("news_agent", "TEST", "BULL", 0.7),
            _make_signal("technical_agent", "TEST", "BULL", 0.6),
            _make_risk_signal("risk_agent", "TEST", risk_score=0.2),
        ]
        result = compute_mfcs(candidate, signals, debate_threshold=0.5)
        # Check that threshold logic works (exact score depends on weights)
        if result.mfcs >= 0.5:
            assert result.qualifies_for_debate is True
        else:
            assert result.qualifies_for_debate is False

    def test_custom_weights(self, candidate):
        """Custom weights should override defaults."""
        signals = [
            _make_signal("news_agent", "TEST", "STRONG_BULL", 1.0),
            _make_signal("technical_agent", "TEST", "STRONG_BEAR", 1.0),
        ]
        # Weight news heavily
        weights_news = {"catalyst_news": 0.9, "technical": 0.1}
        result_news = compute_mfcs(candidate, signals, weights=weights_news)

        # Weight technicals heavily
        weights_tech = {"catalyst_news": 0.1, "technical": 0.9}
        result_tech = compute_mfcs(candidate, signals, weights=weights_tech)

        assert result_news.mfcs > result_tech.mfcs

    def test_mixed_bull_bear_cancellation(self, candidate):
        """D101: Mixed bull/bear signals should partially cancel each other."""
        # STRONG_BULL news + STRONG_BEAR technical — should produce moderate MFCS
        signals = [
            _make_signal("news_agent", "TEST", "STRONG_BULL", 1.0),
            _make_signal("technical_agent", "TEST", "STRONG_BEAR", 1.0),
            _make_risk_signal("risk_agent", "TEST", risk_score=0.3),
        ]
        result = compute_mfcs(candidate, signals)
        # With bipolar: news contributes +1.0, tech contributes -1.0
        # The net should be moderate, much lower than all-bull
        all_bull_signals = [
            _make_signal("news_agent", "TEST", "STRONG_BULL", 1.0),
            _make_signal("technical_agent", "TEST", "STRONG_BULL", 1.0),
            _make_risk_signal("risk_agent", "TEST", risk_score=0.3),
        ]
        result_all_bull = compute_mfcs(candidate, all_bull_signals)
        assert result.mfcs < result_all_bull.mfcs, "Mixed signals should produce lower MFCS than all-bull"

    def test_negative_mfcs_never_passes_buy_threshold(self, candidate):
        """D101: Negative MFCS should never pass any positive buy threshold."""
        signals = [
            _make_signal("news_agent", "TEST", "STRONG_BEAR", 0.9),
            _make_signal("technical_agent", "TEST", "BEAR", 0.7),
            _make_risk_signal("risk_agent", "TEST", risk_score=0.6),
        ]
        result = compute_mfcs(candidate, signals)
        assert result.mfcs < 0.25, f"Bearish MFCS={result.mfcs} should be below buy threshold 0.25"


class TestWeightRedistribution:
    """D26: Weight redistribution when agents return empty defaults."""

    @pytest.fixture
    def candidate(self) -> CandidateStock:
        return CandidateStock(
            ticker="TEST",
            current_price=10.0,
            previous_close=8.0,
            gap_pct=0.25,
            gap_classification="EXPLOSIVE",
            rvol=5.0,
            premarket_volume=500_000,
            scan_timestamp=datetime.now(timezone.utc),
            scan_phase="PRE_MARKET",
        )

    def test_empty_neutral_excluded(self, candidate):
        """NEUTRAL signals with empty reasoning should not contribute."""
        # Real signal + empty NEUTRAL default
        signals = [
            _make_signal("technical_agent", "TEST", "BULL", 0.8),
            _make_signal_empty("news_agent", "TEST"),  # NEUTRAL, empty reasoning
            _make_risk_signal("risk_agent", "TEST", risk_score=0.2),
        ]
        result = compute_mfcs(candidate, signals)
        # News agent should be excluded — only technical + RVOL contribute
        assert "catalyst_news" not in result.component_scores

    def test_real_neutral_included(self, candidate):
        """NEUTRAL signals with actual reasoning should still contribute."""
        signals = [
            _make_signal("technical_agent", "TEST", "BULL", 0.8),
            _make_signal("news_agent", "TEST", "NEUTRAL", 0.5),  # Has reasoning
            _make_risk_signal("risk_agent", "TEST", risk_score=0.2),
        ]
        result = compute_mfcs(candidate, signals)
        # News agent HAS reasoning → should be included
        assert "catalyst_news" in result.component_scores

    def test_weight_boost_increases_score(self, candidate):
        """With only 2 active agents (0.40 weight), MFCS should be boosted."""
        # Only technical agent + RVOL (from candidate.rvol=5.0)
        signals = [
            _make_signal("technical_agent", "TEST", "STRONG_BULL", 0.9),
            _make_signal_empty("news_agent", "TEST"),
            _make_signal_empty("fundamental_agent", "TEST"),
            _make_signal_empty("institutional_agent", "TEST"),
            _make_signal_empty("deep_search_agent", "TEST"),
            _make_risk_signal("risk_agent", "TEST", risk_score=0.3),
        ]
        result = compute_mfcs(candidate, signals, risk_aversion_lambda=0.15)
        # With weight redistribution: tech (0.20) + rvol (0.20) = 0.40 active
        # boost = 1/0.40 = 2.5
        # D101: tech: 0.20 * 2.5 * (1.0 * 0.9) = 0.45  (STRONG_BULL=1.0, same as before)
        # rvol: 0.20 * 2.5 * rvol_score ≈ 0.20 * 2.5 * 0.75 = 0.375
        # weighted_sum ≈ 0.825, risk = 0.15 * 0.3 = 0.045
        # MFCS ≈ 0.780 — well above buy threshold
        assert result.mfcs > 0.10, f"MFCS {result.mfcs} should exceed buy threshold"
        assert result.mfcs > 0.50, f"MFCS {result.mfcs} should be significantly boosted"

    def test_all_agents_active_no_boost(self, candidate):
        """With all 6 agents active, no weight redistribution occurs."""
        signals = [
            _make_signal("news_agent", "TEST", "STRONG_BULL", 1.0),
            _make_signal("technical_agent", "TEST", "STRONG_BULL", 1.0),
            _make_signal("volume_agent", "TEST", "STRONG_BULL", 1.0),
            _make_signal("fundamental_agent", "TEST", "STRONG_BULL", 1.0),
            _make_signal("institutional_agent", "TEST", "STRONG_BULL", 1.0),
            _make_signal("deep_search_agent", "TEST", "STRONG_BULL", 1.0),
            _make_risk_signal("risk_agent", "TEST", risk_score=0.0),
        ]
        result = compute_mfcs(candidate, signals)
        # All weights sum to 1.0, all scores 1.0, boost = 1.0
        # MFCS = 1.0 - 0.0 = 1.0
        assert result.mfcs == 1.0

    def test_lower_lambda_reduces_penalty(self, candidate):
        """D27: Lower lambda should reduce risk penalty."""
        signals = [
            _make_signal("technical_agent", "TEST", "BULL", 0.8),
            _make_risk_signal("risk_agent", "TEST", risk_score=0.5),
        ]
        result_high_lambda = compute_mfcs(
            candidate, signals, risk_aversion_lambda=0.3
        )
        result_low_lambda = compute_mfcs(
            candidate, signals, risk_aversion_lambda=0.15
        )
        assert result_low_lambda.mfcs > result_high_lambda.mfcs


# ─── Test Helpers ────────────────────────────────────────────────────

def _make_signal(
    agent_id: str,
    ticker: str,
    direction: str,
    confidence: float,
) -> AgentSignal:
    return AgentSignal(
        agent_id=agent_id,
        ticker=ticker,
        timestamp=datetime.now(timezone.utc),
        signal=direction,
        confidence=confidence,
        reasoning="Test signal",
    )


def _make_signal_empty(
    agent_id: str,
    ticker: str,
) -> AgentSignal:
    """Create a NEUTRAL signal with empty reasoning (agent received no data)."""
    return AgentSignal(
        agent_id=agent_id,
        ticker=ticker,
        timestamp=datetime.now(timezone.utc),
        signal="NEUTRAL",
        confidence=0.5,
        reasoning="",  # Empty — agent had no data
    )


def _make_risk_signal(
    agent_id: str,
    ticker: str,
    risk_score: float,
) -> RiskSignal:
    return RiskSignal(
        agent_id=agent_id,
        ticker=ticker,
        timestamp=datetime.now(timezone.utc),
        signal="NEUTRAL",
        confidence=1.0,
        reasoning="Test risk signal",
        risk_verdict="APPROVE" if risk_score < 0.5 else "CAUTION",
        risk_score=risk_score,
    )
