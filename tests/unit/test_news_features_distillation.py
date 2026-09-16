"""
D221 Phase F: unit tests for news_agent's dense feature vector.

Tests the `compute_news_features` pure function and its integration with
NewsSignal schema. Covers:
  - 12 keys present with documented semantics
  - Value ranges for each key
  - Catalyst quality mapping (HIGH/MEDIUM/LOW/NONE tiers)
  - Signal -> numeric mapping (-2 to +2)
  - Specificity scoring (CONFIRMED=1.0, RUMORED=0.5, SPECULATIVE=0.0)
  - Interaction terms (signal_x_confidence)
  - Defensive coercion: None/NaN/out-of-range inputs handled cleanly
  - NewsSignal.news_features populated when parse_response runs
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.agents.news_agent import (
    _CATALYST_QUALITY_SCORES,
    _HIGH_QUALITY_CATALYSTS,
    _SIGNAL_TO_NUMERIC,
    _SPECIFICITY_SCORES,
    NewsAgent,
    compute_news_features,
)
from src.core.models import NewsSignal


# Expected 12 keys per the v2 architecture spec.
_EXPECTED_KEYS = frozenset({
    "catalyst_quality_score",
    "catalyst_specificity_score",
    "sentiment_score",
    "sentiment_intensity",
    "signal_direction_numeric",
    "confidence",
    "signal_x_confidence",
    "n_source_citations",
    "n_red_flags",
    "reasoning_length_chars",
    "has_specific_catalyst",
    "is_high_quality_catalyst",
})


class TestSchema:
    """Exactly 12 features, matching the spec."""

    def test_twelve_keys_present(self):
        f = compute_news_features(
            signal="BULL", confidence=0.5,
            catalyst_type="FDA_APPROVAL", specificity="CONFIRMED",
            sentiment_score=0.5, reasoning="x", red_flags=[],
            source_citations=[],
        )
        assert set(f.keys()) == _EXPECTED_KEYS

    def test_all_values_are_floats(self):
        f = compute_news_features(
            signal="NEUTRAL", confidence=0.5, catalyst_type="NONE",
            specificity="SPECULATIVE", sentiment_score=0.0,
            reasoning="", red_flags=[], source_citations=[],
        )
        for k, v in f.items():
            assert isinstance(v, float), f"{k} must be float, got {type(v).__name__}"


class TestCatalystQualityMapping:
    """Major catalysts -> 1.0, minor -> 0.6, weak -> 0.3, none -> 0.0."""

    @pytest.mark.parametrize("ctype", [
        "FDA_APPROVAL", "M_AND_A", "EARNINGS_BEAT",
        "CONTRACT_WIN", "LEGAL_WIN",
    ])
    def test_major_catalysts_score_one(self, ctype):
        f = compute_news_features(
            signal="BULL", confidence=0.5, catalyst_type=ctype,
            specificity="CONFIRMED", sentiment_score=0.5,
            reasoning="", red_flags=[], source_citations=[],
        )
        assert f["catalyst_quality_score"] == 1.0
        assert f["is_high_quality_catalyst"] == 1.0
        assert f["has_specific_catalyst"] == 1.0

    @pytest.mark.parametrize("ctype", [
        "ANALYST_UPGRADE", "PRODUCT_LAUNCH", "REGULATORY",
        "SHORT_SQUEEZE", "MANAGEMENT_CHANGE",
    ])
    def test_minor_catalysts_score_0_6(self, ctype):
        f = compute_news_features(
            signal="BULL", confidence=0.5, catalyst_type=ctype,
            specificity="CONFIRMED", sentiment_score=0.5,
            reasoning="", red_flags=[], source_citations=[],
        )
        assert f["catalyst_quality_score"] == 0.6
        assert f["is_high_quality_catalyst"] == 0.0
        assert f["has_specific_catalyst"] == 1.0

    @pytest.mark.parametrize("ctype", ["CORPORATE_UPDATE", "SECTOR_CATALYST"])
    def test_weak_catalysts_score_0_3(self, ctype):
        f = compute_news_features(
            signal="BULL", confidence=0.5, catalyst_type=ctype,
            specificity="CONFIRMED", sentiment_score=0.5,
            reasoning="", red_flags=[], source_citations=[],
        )
        assert f["catalyst_quality_score"] == 0.3

    def test_no_catalyst_scores_zero(self):
        f = compute_news_features(
            signal="NEUTRAL", confidence=0.3, catalyst_type="NONE",
            specificity="SPECULATIVE", sentiment_score=0.0,
            reasoning="", red_flags=[], source_citations=[],
        )
        assert f["catalyst_quality_score"] == 0.0
        assert f["has_specific_catalyst"] == 0.0
        assert f["is_high_quality_catalyst"] == 0.0

    def test_unknown_catalyst_type_defaults_zero(self):
        """Unknown catalyst types should not crash; they score 0.0."""
        f = compute_news_features(
            signal="NEUTRAL", confidence=0.5, catalyst_type="ALIEN_INVASION",
            specificity="CONFIRMED", sentiment_score=0.0,
            reasoning="", red_flags=[], source_citations=[],
        )
        assert f["catalyst_quality_score"] == 0.0


class TestSignalDirectionMapping:
    """SignalDirection -> numeric (-2, -1, 0, +1, +2)."""

    @pytest.mark.parametrize("sig,expected", [
        ("STRONG_BEAR", -2.0),
        ("BEAR", -1.0),
        ("NEUTRAL", 0.0),
        ("BULL", 1.0),
        ("STRONG_BULL", 2.0),
    ])
    def test_signal_numeric_mapping(self, sig, expected):
        f = compute_news_features(
            signal=sig, confidence=0.5, catalyst_type="NONE",
            specificity="SPECULATIVE", sentiment_score=0.0,
            reasoning="", red_flags=[], source_citations=[],
        )
        assert f["signal_direction_numeric"] == expected

    def test_unknown_signal_defaults_neutral(self):
        """Unknown signal strings shouldn't crash; they default to 0.0 (NEUTRAL)."""
        f = compute_news_features(
            signal="NONSENSE", confidence=0.5, catalyst_type="NONE",
            specificity="SPECULATIVE", sentiment_score=0.0,
            reasoning="", red_flags=[], source_citations=[],
        )
        assert f["signal_direction_numeric"] == 0.0


class TestSpecificityMapping:
    """CONFIRMED=1.0, RUMORED=0.5, SPECULATIVE=0.0."""

    @pytest.mark.parametrize("spec,expected", [
        ("CONFIRMED", 1.0),
        ("RUMORED", 0.5),
        ("SPECULATIVE", 0.0),
    ])
    def test_specificity_score(self, spec, expected):
        f = compute_news_features(
            signal="BULL", confidence=0.5, catalyst_type="FDA_APPROVAL",
            specificity=spec, sentiment_score=0.0,
            reasoning="", red_flags=[], source_citations=[],
        )
        assert f["catalyst_specificity_score"] == expected


class TestInteractionTerms:
    """signal_x_confidence = signal_direction_numeric * confidence."""

    def test_bull_high_confidence(self):
        f = compute_news_features(
            signal="STRONG_BULL", confidence=0.9, catalyst_type="FDA_APPROVAL",
            specificity="CONFIRMED", sentiment_score=0.8,
            reasoning="", red_flags=[], source_citations=[],
        )
        # STRONG_BULL = 2.0; 2.0 * 0.9 = 1.8
        assert f["signal_x_confidence"] == pytest.approx(1.8)

    def test_bear_high_confidence(self):
        f = compute_news_features(
            signal="STRONG_BEAR", confidence=0.8, catalyst_type="NONE",
            specificity="SPECULATIVE", sentiment_score=-0.9,
            reasoning="", red_flags=[], source_citations=[],
        )
        # STRONG_BEAR = -2.0; -2.0 * 0.8 = -1.6
        assert f["signal_x_confidence"] == pytest.approx(-1.6)

    def test_neutral_is_zero_regardless_of_confidence(self):
        f = compute_news_features(
            signal="NEUTRAL", confidence=0.9, catalyst_type="NONE",
            specificity="SPECULATIVE", sentiment_score=0.0,
            reasoning="", red_flags=[], source_citations=[],
        )
        assert f["signal_x_confidence"] == 0.0


class TestDefensiveCoercion:
    """None/out-of-range inputs handled cleanly, never raise."""

    def test_none_sentiment_defaults_zero(self):
        f = compute_news_features(
            signal="NEUTRAL", confidence=0.5, catalyst_type="NONE",
            specificity="SPECULATIVE", sentiment_score=None,
            reasoning="", red_flags=[], source_citations=[],
        )
        assert f["sentiment_score"] == 0.0
        assert f["sentiment_intensity"] == 0.0

    def test_out_of_range_sentiment_clamped(self):
        f = compute_news_features(
            signal="NEUTRAL", confidence=0.5, catalyst_type="NONE",
            specificity="SPECULATIVE", sentiment_score=1.5,
            reasoning="", red_flags=[], source_citations=[],
        )
        assert f["sentiment_score"] == 1.0
        assert f["sentiment_intensity"] == 1.0

    def test_out_of_range_confidence_clamped(self):
        f = compute_news_features(
            signal="BULL", confidence=1.5, catalyst_type="NONE",
            specificity="SPECULATIVE", sentiment_score=0.0,
            reasoning="", red_flags=[], source_citations=[],
        )
        assert f["confidence"] == 1.0
        # signal_x_confidence also bounded by clamped confidence
        assert f["signal_x_confidence"] == 1.0

    def test_none_reasoning_and_lists(self):
        f = compute_news_features(
            signal="NEUTRAL", confidence=0.5, catalyst_type="NONE",
            specificity="SPECULATIVE", sentiment_score=0.0,
            reasoning=None, red_flags=None, source_citations=None,
        )
        assert f["reasoning_length_chars"] == 0.0
        assert f["n_red_flags"] == 0.0
        assert f["n_source_citations"] == 0.0

    def test_counts_nonzero(self):
        f = compute_news_features(
            signal="BULL", confidence=0.5, catalyst_type="FDA_APPROVAL",
            specificity="CONFIRMED", sentiment_score=0.5,
            reasoning="Four words here yes",
            red_flags=["dilution risk", "thin float"],
            source_citations=[{"source": "Reuters"}, {"source": "AP"}, {"source": "SEC"}],
        )
        assert f["reasoning_length_chars"] == 19.0
        assert f["n_red_flags"] == 2.0
        assert f["n_source_citations"] == 3.0


class TestNewsSignalIntegration:
    """NewsSignal.news_features is populated when parse_response runs."""

    def test_parse_response_populates_features(self):
        agent = NewsAgent(model="test-model")
        raw = {
            "signal": "BULL",
            "confidence": 0.75,
            "catalyst_type": "FDA_APPROVAL",
            "catalyst_specificity": "CONFIRMED",
            "sentiment_score": 0.6,
            "key_reasoning": "FDA approval for novel compound announced.",
            "red_flags": [],
            "source_citations": [
                {"source": "Reuters", "headline": "FDA approves..."},
                {"source": "BusinessWire", "headline": "Company announces..."},
            ],
        }
        result = agent.parse_response(raw, "TESTTICKER")
        assert isinstance(result, NewsSignal)
        assert len(result.news_features) == 12
        assert result.news_features["catalyst_quality_score"] == 1.0
        assert result.news_features["is_high_quality_catalyst"] == 1.0
        assert result.news_features["signal_direction_numeric"] == 1.0  # BULL
        # Reasoning length matches the input text
        assert result.news_features["reasoning_length_chars"] == float(len(raw["key_reasoning"]))

    def test_news_features_backward_compat_default_empty(self):
        """NewsSignal constructed without news_features defaults to {}."""
        sig = NewsSignal(
            agent_id="news_agent", ticker="X",
            timestamp=datetime.now(timezone.utc),
            signal="NEUTRAL", confidence=0.3, reasoning="test",
        )
        assert sig.news_features == {}


class TestModuleConstants:
    """Sanity checks on the module-level mapping tables."""

    def test_signal_numeric_covers_all_literal_values(self):
        expected = {"STRONG_BEAR", "BEAR", "NEUTRAL", "BULL", "STRONG_BULL"}
        assert set(_SIGNAL_TO_NUMERIC.keys()) == expected

    def test_specificity_scores_cover_all_literal_values(self):
        assert set(_SPECIFICITY_SCORES.keys()) == {
            "CONFIRMED", "RUMORED", "SPECULATIVE",
        }

    def test_high_quality_subset_of_catalyst_scores(self):
        """Every HIGH_QUALITY_CATALYST must have a catalyst_quality_score."""
        for cat in _HIGH_QUALITY_CATALYSTS:
            assert cat in _CATALYST_QUALITY_SCORES
            assert _CATALYST_QUALITY_SCORES[cat] == 1.0
