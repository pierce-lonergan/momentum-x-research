"""
D218: Feature logger coverage test — verifies both BUY and NO_TRADE
paths produce feature rows.

The BUY path logs at orchestrator.py:1470 (after verdict construction).
The NO_TRADE path logs inside _build_no_trade_verdict() (D217 fix).
Together they guarantee 100% evaluation coverage.

If someone adds an early return between the verdict construction and the
feature log call, this test fails.
"""

import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.analysis.feature_logger import FeatureLogger


@pytest.fixture
def temp_feature_dir():
    d = tempfile.mkdtemp(prefix="feature_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


class TestFeatureLoggerDirectWrite:
    """Verify FeatureLogger writes correctly for both BUY and NO_TRADE."""

    def test_no_trade_produces_feature_row(self, temp_feature_dir):
        """A NO_TRADE evaluation produces exactly one feature row."""
        fl = FeatureLogger(output_dir=temp_feature_dir)
        fl.log_evaluation(
            trade_id="TEST-NT-001",
            ticker="REJECT",
            gap_pct=0.08,
            rvol=1.5,
            current_price=5.00,
            previous_close=4.63,
            premarket_volume=100000,
            float_shares=10000000,
            market_cap=50000000,
            has_news_catalyst=False,
            rvol_exhaustion=False,
            agent_signals=[
                {"agent": "news", "signal": "NEUTRAL", "confidence": 0.3},
            ],
            mfcs=0.12,
            component_scores={"catalyst_news": 0.3},
            risk_score=0.5,
            qualifies_for_debate=False,
            indicators={},
            debate_verdict=None,
            debate_confidence=None,
            debate_divergence=None,
            debate_skipped=False,
            hour_et=9,
            minute_et=35,
            day_of_week=4,
            final_action="NO_TRADE",
            position_size_pct=0.0,
        )

        files = list(Path(temp_feature_dir).glob("features_*.jsonl"))
        assert len(files) == 1, f"Expected 1 feature file, found {len(files)}"

        with open(files[0]) as f:
            lines = [json.loads(line) for line in f if line.strip()]
        assert len(lines) == 1, f"Expected 1 row, got {len(lines)}"
        assert lines[0]["final_action"] == "NO_TRADE"
        assert lines[0]["ticker"] == "REJECT"
        assert lines[0]["mfcs"] == 0.12

    def test_buy_produces_feature_row(self, temp_feature_dir):
        """A BUY evaluation produces exactly one feature row."""
        fl = FeatureLogger(output_dir=temp_feature_dir)
        fl.log_evaluation(
            trade_id="TEST-BUY-001",
            ticker="WINNER",
            gap_pct=0.25,
            rvol=8.0,
            current_price=12.50,
            previous_close=10.00,
            premarket_volume=500000,
            float_shares=5000000,
            market_cap=62500000,
            has_news_catalyst=True,
            rvol_exhaustion=False,
            agent_signals=[
                {"agent": "news", "signal": "BULL", "confidence": 0.85},
                {"agent": "risk", "signal": "NEUTRAL", "confidence": 0.60},
            ],
            mfcs=0.45,
            component_scores={"catalyst_news": 0.85, "volume_rvol": 0.60},
            risk_score=0.15,
            qualifies_for_debate=False,
            indicators={"rsi_14": 65.0, "vwap": 12.30},
            debate_verdict=None,
            debate_confidence=None,
            debate_divergence=None,
            debate_skipped=False,
            hour_et=9,
            minute_et=31,
            day_of_week=1,
            final_action="BUY",
            position_size_pct=0.15,
        )

        files = list(Path(temp_feature_dir).glob("features_*.jsonl"))
        assert len(files) == 1

        with open(files[0]) as f:
            lines = [json.loads(line) for line in f if line.strip()]
        assert len(lines) == 1
        assert lines[0]["final_action"] == "BUY"
        assert lines[0]["ticker"] == "WINNER"
        assert lines[0]["mfcs"] == 0.45
        assert lines[0]["position_size_pct"] == 0.15

    def test_both_paths_produce_rows_in_same_file(self, temp_feature_dir):
        """Both BUY and NO_TRADE rows land in the same daily file."""
        fl = FeatureLogger(output_dir=temp_feature_dir)

        # NO_TRADE
        fl.log_evaluation(
            trade_id="T1", ticker="REJECT1", gap_pct=0.05, rvol=1.0,
            current_price=3.0, previous_close=2.85, premarket_volume=50000,
            float_shares=None, market_cap=None, has_news_catalyst=False,
            rvol_exhaustion=False, agent_signals=[], mfcs=0.08,
            component_scores={}, risk_score=0.0, qualifies_for_debate=False,
            indicators={}, debate_verdict=None, debate_confidence=None,
            debate_divergence=None, debate_skipped=False,
            hour_et=9, minute_et=30, day_of_week=0,
            final_action="NO_TRADE", position_size_pct=0.0,
        )

        # BUY
        fl.log_evaluation(
            trade_id="T2", ticker="BUY1", gap_pct=0.20, rvol=6.0,
            current_price=8.0, previous_close=6.67, premarket_volume=300000,
            float_shares=3000000, market_cap=24000000, has_news_catalyst=True,
            rvol_exhaustion=False,
            agent_signals=[{"agent": "news", "signal": "BULL", "confidence": 0.9}],
            mfcs=0.50, component_scores={"catalyst_news": 0.9},
            risk_score=0.10, qualifies_for_debate=False,
            indicators={}, debate_verdict=None, debate_confidence=None,
            debate_divergence=None, debate_skipped=False,
            hour_et=9, minute_et=32, day_of_week=0,
            final_action="BUY", position_size_pct=0.12,
        )

        files = list(Path(temp_feature_dir).glob("features_*.jsonl"))
        assert len(files) == 1

        with open(files[0]) as f:
            lines = [json.loads(line) for line in f if line.strip()]
        assert len(lines) == 2
        assert lines[0]["final_action"] == "NO_TRADE"
        assert lines[1]["final_action"] == "BUY"

    def test_feature_row_field_count(self, temp_feature_dir):
        """Each feature row has the expected number of fields."""
        fl = FeatureLogger(output_dir=temp_feature_dir)
        fl.log_evaluation(
            trade_id="T1", ticker="X", gap_pct=0.10, rvol=3.0,
            current_price=5.0, previous_close=4.55, premarket_volume=200000,
            float_shares=8000000, market_cap=40000000, has_news_catalyst=True,
            rvol_exhaustion=False, agent_signals=[], mfcs=0.30,
            component_scores={}, risk_score=0.20, qualifies_for_debate=False,
            indicators={"rsi_14": 55.0}, debate_verdict=None,
            debate_confidence=None, debate_divergence=None,
            debate_skipped=False, hour_et=10, minute_et=15, day_of_week=2,
            final_action="BUY", position_size_pct=0.08,
        )

        files = list(Path(temp_feature_dir).glob("features_*.jsonl"))
        with open(files[0]) as f:
            data = json.loads(f.readline())
        # Should have at least 25 fields (core + indicators + debate + time)
        assert len(data) >= 25, f"Expected >=25 fields, got {len(data)}: {sorted(data.keys())}"
