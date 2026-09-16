"""D200: Tests for Decision Quality Arena and experiment components."""

import pytest
from src.arena.decision_quality import (
    AgentAccuracy,
    CatalystBreakdown,
    DecisionQualityArena,
    DecisionQualityReport,
    DecisionRecord,
    MFCSBucket,
)


class TestDecisionRecord:
    def test_create_basic(self):
        rec = DecisionRecord(
            ticker="ARTL", date="2026-03-30", mfcs=0.42,
            action="BUY", agent_signals={},
        )
        assert rec.ticker == "ARTL"
        assert rec.actual_win is None

    def test_with_outcome(self):
        rec = DecisionRecord(
            ticker="BFRG", date="2026-03-30", mfcs=0.65,
            action="STRONG_BUY", agent_signals={},
            actual_win=True, actual_pnl=1200.0,
        )
        assert rec.actual_win is True
        assert rec.actual_pnl == 1200.0


class TestAgentAccuracy:
    def test_zero_signals(self):
        aa = AgentAccuracy(agent_id="news_agent")
        assert aa.bullish_accuracy == 0.0
        assert aa.bearish_accuracy == 0.0
        assert aa.overall_accuracy == 0.0

    def test_perfect_bullish(self):
        aa = AgentAccuracy(
            agent_id="news_agent",
            bullish_correct=5, bullish_total_with_outcome=5,
        )
        assert aa.bullish_accuracy == 1.0

    def test_mixed_accuracy(self):
        aa = AgentAccuracy(
            agent_id="technical_agent",
            bullish_correct=3, bullish_total_with_outcome=10,
            bearish_correct=7, bearish_total_with_outcome=10,
        )
        assert aa.bullish_accuracy == 0.3
        assert aa.bearish_accuracy == 0.7
        assert aa.overall_accuracy == 0.5


class TestMFCSBucket:
    def test_empty_bucket(self):
        b = MFCSBucket(0.0, 0.2)
        assert b.win_rate == 0.0
        assert b.avg_pnl == 0.0

    def test_populated_bucket(self):
        b = MFCSBucket(0.4, 0.6, total=10, wins=6, total_pnl=500.0)
        assert b.win_rate == 0.6
        assert b.avg_pnl == 50.0


class TestCatalystBreakdown:
    def test_no_catalyst(self):
        cb = CatalystBreakdown(catalyst_type="unknown", total=14, wins=1)
        assert cb.win_rate == pytest.approx(1 / 14, rel=0.01)

    def test_good_catalyst(self):
        cb = CatalystBreakdown(catalyst_type="FDA_APPROVAL", total=5, wins=4)
        assert cb.win_rate == 0.8


class TestDecisionQualityArena:
    def test_parse_journal_entry_basic(self):
        arena = DecisionQualityArena()
        entry = {
            "ticker": "BFRG",
            "session_date": "2026-03-30",
            "mfcs": 0.65,
            "action": "BUY",
            "gap_pct": 0.45,
            "rvol": 8.0,
            "qualifies_for_debate": True,
            "agent_signals": [
                {
                    "agent_id": "news_agent",
                    "signal": "BULL",
                    "confidence": 0.7,
                    "catalyst_type": "FDA_APPROVAL",
                    "catalyst_specificity": "CONFIRMED",
                },
                {
                    "agent_id": "technical_agent",
                    "signal": "STRONG_BULL",
                    "confidence": 0.8,
                },
            ],
        }
        rec = arena._parse_journal_entry(entry)
        assert rec is not None
        assert rec.ticker == "BFRG"
        assert rec.mfcs == 0.65
        assert rec.catalyst_type == "FDA_APPROVAL"
        assert "news_agent" in rec.agent_signals
        assert "technical_agent" in rec.agent_signals

    def test_parse_journal_entry_no_catalyst(self):
        arena = DecisionQualityArena()
        entry = {
            "ticker": "ARTL",
            "session_date": "2026-03-30",
            "mfcs": 0.30,
            "action": "BUY",
            "agent_signals": [
                {
                    "agent_id": "news_agent",
                    "signal": "NEUTRAL",
                    "confidence": 0.0,
                    "catalyst_type": "NONE",
                },
            ],
        }
        rec = arena._parse_journal_entry(entry)
        assert rec is not None
        assert rec.catalyst_type is None  # NONE is treated as no catalyst

    def test_parse_journal_entry_missing_ticker(self):
        arena = DecisionQualityArena()
        entry = {"mfcs": 0.5, "action": "BUY"}
        rec = arena._parse_journal_entry(entry)
        assert rec is None

    def test_analyze_empty(self):
        arena = DecisionQualityArena()
        report = arena.analyze()
        assert report.total_decisions == 0
        assert report.buy_win_rate == 0.0

    def test_analyze_with_records(self):
        arena = DecisionQualityArena()
        # Manually inject records
        arena._records = [
            DecisionRecord(
                ticker="WIN", date="2026-01-01", mfcs=0.7,
                action="BUY", agent_signals={
                    "news_agent": {"signal": "BULL", "confidence": 0.7},
                },
                actual_win=True, catalyst_type="FDA_APPROVAL",
                qualifies_for_debate=True,
            ),
            DecisionRecord(
                ticker="LOSS", date="2026-01-01", mfcs=0.3,
                action="BUY", agent_signals={
                    "news_agent": {"signal": "NEUTRAL", "confidence": 0.0},
                },
                actual_win=False, catalyst_type=None,
            ),
        ]
        report = arena.analyze()
        assert report.total_decisions == 2
        assert report.decisions_with_outcomes == 2
        assert report.buy_decisions == 2
        assert report.buy_win_rate == 0.5

    def test_format_report_no_crash(self):
        arena = DecisionQualityArena()
        report = arena.analyze()
        text = arena.format_report(report)
        assert "DECISION QUALITY ARENA" in text


class TestReportFormatting:
    def test_empty_report_formats(self):
        report = DecisionQualityReport()
        arena = DecisionQualityArena()
        text = arena.format_report(report)
        assert "Total decisions analyzed: 0" in text

    def test_report_with_calibration(self):
        report = DecisionQualityReport(
            mfcs_calibration=[
                MFCSBucket(0.0, 0.2, total=5, wins=1),
                MFCSBucket(0.2, 0.4, total=10, wins=5),
            ],
        )
        arena = DecisionQualityArena()
        text = arena.format_report(report)
        assert "MFCS CALIBRATION" in text
