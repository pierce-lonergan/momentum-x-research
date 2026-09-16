"""
Tests for TradeJournal — comprehensive data capture system.

Node ID: tests.unit.test_trade_journal
Validates JSONL persistence, entry creation, signal recording,
and session summary generation.
"""

import json
import pytest
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass

from src.analysis.trade_journal import (
    TradeJournal,
    JournalEntry,
    AgentSignalRecord,
    DebateRecord,
    DataQualityRecord,
    InputDataRecord,
)


@dataclass
class MockCandidate:
    """Minimal CandidateStock-like object for testing."""

    ticker: str = "TEST"
    current_price: float = 10.0
    previous_close: float = 8.0
    gap_pct: float = 0.25
    gap_classification: str = "EXPLOSIVE"
    rvol: float = 5.0
    premarket_volume: int = 500_000
    float_shares: int = 5_000_000
    market_cap: float = 50_000_000


@dataclass
class MockSignal:
    """Minimal AgentSignal-like object for testing."""

    agent_id: str = "news_agent"
    signal: str = "STRONG_BULL"
    confidence: float = 0.9
    reasoning: str = "FDA approval confirmed"
    key_data: dict = None
    sources_used: list = None
    model_id: str = "test-model"
    latency_ms: float = 1500.0

    def __post_init__(self):
        if self.key_data is None:
            self.key_data = {"catalyst": "FDA"}
        if self.sources_used is None:
            self.sources_used = ["reuters"]


@dataclass
class MockRiskSignal:
    """Risk-specific signal."""

    agent_id: str = "risk_agent"
    signal: str = "BEAR"
    confidence: float = 0.3
    reasoning: str = "High dilution risk"
    key_data: dict = None
    sources_used: list = None
    model_id: str = "test-model"
    latency_ms: float = 800.0
    risk_verdict: str = "CAUTION"
    risk_score: float = 0.6
    veto_reason: str = ""

    def __post_init__(self):
        if self.key_data is None:
            self.key_data = {}
        if self.sources_used is None:
            self.sources_used = []


@dataclass
class MockVerdict:
    """Minimal TradeVerdict-like object."""

    ticker: str = "TEST"
    action: str = "BUY"
    confidence: float = 0.85
    entry_price: float = 10.0
    stop_loss: float = 9.60
    target_prices: list = None
    position_size_pct: float = 0.10
    reasoning_summary: str = "MFCS=0.329 | Debate=YES | Risk=PASS"

    def __post_init__(self):
        if self.target_prices is None:
            self.target_prices = [10.30, 10.60, 11.00]


@dataclass
class MockDebateResult:
    """Minimal DebateResult-like object."""

    verdict: str = "BUY"
    confidence: float = 0.85
    bull_strength: float = 0.9
    bear_strength: float = 0.3
    debate_divergence: float = 0.6
    position_size: str = "FULL"
    arguments: dict = None
    entry_price: float = 10.0
    stop_loss: float = 9.60
    target_prices: list = None

    def __post_init__(self):
        if self.arguments is None:
            self.arguments = {"bull": "Strong catalyst", "bear": "High volatility"}
        if self.target_prices is None:
            self.target_prices = [10.30, 10.60, 11.00]


class TestJournalCreation:
    """Test journal file creation and basic operations."""

    def test_journal_creates_file(self, tmp_path):
        journal = TradeJournal(journal_dir=tmp_path)
        assert journal.path.parent == tmp_path
        assert journal.path.name.startswith("journal_")
        assert journal.path.suffix == ".jsonl"

    def test_journal_entry_count_starts_at_zero(self, tmp_path):
        journal = TradeJournal(journal_dir=tmp_path)
        assert journal.entry_count == 0

    def test_create_entry_increments_count(self, tmp_path):
        journal = TradeJournal(journal_dir=tmp_path)
        cand = MockCandidate()
        entry = journal.create_entry("trade-001", cand, phase="MARKET_OPEN")
        assert journal.entry_count == 1
        assert entry.ticker == "TEST"
        assert entry.trade_id == "trade-001"
        assert entry.phase == "MARKET_OPEN"

    def test_create_entry_captures_candidate_data(self, tmp_path):
        journal = TradeJournal(journal_dir=tmp_path)
        cand = MockCandidate(
            ticker="NVDA", current_price=120.5, gap_pct=0.15, rvol=8.0,
        )
        entry = journal.create_entry("trade-002", cand)
        assert entry.ticker == "NVDA"
        assert entry.current_price == 120.5
        assert entry.gap_pct == 0.15
        assert entry.rvol == 8.0


class TestInputDataRecording:
    """Test recording of input data provenance."""

    def test_record_news_items(self, tmp_path):
        journal = TradeJournal(journal_dir=tmp_path)
        entry = journal.create_entry("t-001", MockCandidate())

        @dataclass
        class FakeNews:
            headline: str = "FDA approves drug"
            summary: str = "Big news"
            source: str = "Reuters"
            url: str = "https://example.com"
            published_at: str = "2026-02-10T10:00:00Z"
            provider: str = "alpaca"

        journal.record_input_data(entry, news_items=[FakeNews(), FakeNews()])
        assert entry.input_data.news_items_count == 2
        assert "FDA approves drug" in entry.input_data.news_headlines

    def test_record_technical_indicators(self, tmp_path):
        journal = TradeJournal(journal_dir=tmp_path)
        entry = journal.create_entry("t-002", MockCandidate())
        journal.record_input_data(
            entry,
            market_data={"indicators": {"rsi_14": 72.5, "macd_histogram": 0.15}},
        )
        assert entry.input_data.technical_indicators["rsi_14"] == 72.5

    def test_record_empty_data(self, tmp_path):
        journal = TradeJournal(journal_dir=tmp_path)
        entry = journal.create_entry("t-003", MockCandidate())
        journal.record_input_data(entry)
        assert entry.input_data.news_items_count == 0
        assert entry.input_data.sec_filings_count == 0


class TestAgentSignalRecording:
    """Test recording of agent signals with full details."""

    def test_record_basic_signal(self, tmp_path):
        journal = TradeJournal(journal_dir=tmp_path)
        entry = journal.create_entry("t-001", MockCandidate())
        journal.record_agent_signals(entry, [MockSignal()])
        assert len(entry.agent_signals) == 1
        assert entry.agent_signals[0].agent_id == "news_agent"
        assert entry.agent_signals[0].signal == "STRONG_BULL"
        assert entry.agent_signals[0].confidence == 0.9
        assert "FDA" in entry.agent_signals[0].reasoning

    def test_record_risk_signal(self, tmp_path):
        journal = TradeJournal(journal_dir=tmp_path)
        entry = journal.create_entry("t-002", MockCandidate())
        journal.record_agent_signals(entry, [MockRiskSignal()])
        assert entry.agent_signals[0].risk_verdict == "CAUTION"
        assert entry.agent_signals[0].risk_score == 0.6

    def test_record_multiple_signals(self, tmp_path):
        journal = TradeJournal(journal_dir=tmp_path)
        entry = journal.create_entry("t-003", MockCandidate())
        signals = [
            MockSignal(agent_id="news_agent"),
            MockSignal(agent_id="technical_agent", signal="BULL", confidence=0.7),
            MockRiskSignal(),
        ]
        journal.record_agent_signals(entry, signals)
        assert len(entry.agent_signals) == 3

    def test_record_variant_map(self, tmp_path):
        journal = TradeJournal(journal_dir=tmp_path)
        entry = journal.create_entry("t-004", MockCandidate())
        journal.record_agent_signals(
            entry,
            [MockSignal()],
            variant_map={"news_agent": "v2-aggressive"},
        )
        assert entry.agent_signals[0].prompt_variant_id == "v2-aggressive"
        assert entry.arena_variant_map == {"news_agent": "v2-aggressive"}

    def test_record_data_quality(self, tmp_path):
        journal = TradeJournal(journal_dir=tmp_path)
        entry = journal.create_entry("t-005", MockCandidate())
        journal.record_agent_signals(
            entry,
            [MockSignal()],
            data_report={
                "news_agent": {"status": "COMPLETE"},
                "institutional_agent": {"status": "EMPTY", "missing_fields": ["options_data"]},
            },
        )
        assert len(entry.data_quality) == 2
        assert entry.data_quality[1].status == "EMPTY"
        assert "options_data" in entry.data_quality[1].missing_fields


class TestDebateRecording:
    """Test debate result capture."""

    def test_record_debate(self, tmp_path):
        journal = TradeJournal(journal_dir=tmp_path)
        entry = journal.create_entry("t-001", MockCandidate())
        journal.record_debate(entry, MockDebateResult())
        assert entry.debate is not None
        assert entry.debate.verdict == "BUY"
        assert entry.debate.debate_divergence == 0.6
        assert entry.debate.bull_strength == 0.9

    def test_record_no_debate(self, tmp_path):
        journal = TradeJournal(journal_dir=tmp_path)
        entry = journal.create_entry("t-002", MockCandidate())
        journal.record_debate(entry, None)
        assert entry.debate is None


class TestVerdictRecording:
    """Test final verdict capture."""

    def test_record_buy_verdict(self, tmp_path):
        journal = TradeJournal(journal_dir=tmp_path)
        entry = journal.create_entry("t-001", MockCandidate())
        journal.record_verdict(entry, MockVerdict())
        assert entry.action == "BUY"
        assert entry.position_size_pct == 0.10
        assert entry.target_prices == [10.30, 10.60, 11.00]

    def test_record_no_trade_verdict(self, tmp_path):
        journal = TradeJournal(journal_dir=tmp_path)
        entry = journal.create_entry("t-002", MockCandidate())
        journal.record_verdict(
            entry,
            MockVerdict(action="NO_TRADE", reasoning_summary="Risk VETO: dilution"),
        )
        assert entry.action == "NO_TRADE"
        assert "dilution" in entry.rejection_reason


class TestFlushAndLoad:
    """Test JSONL persistence and loading."""

    def test_flush_creates_file(self, tmp_path):
        journal = TradeJournal(journal_dir=tmp_path)
        entry = journal.create_entry("t-001", MockCandidate())
        journal.record_verdict(entry, MockVerdict())
        journal.flush(entry)
        assert journal.path.exists()
        content = journal.path.read_text()
        assert "TEST" in content
        assert "BUY" in content

    def test_flush_is_valid_jsonl(self, tmp_path):
        journal = TradeJournal(journal_dir=tmp_path)

        # Write 3 entries
        for i in range(3):
            entry = journal.create_entry(f"t-{i}", MockCandidate(ticker=f"TICK{i}"))
            journal.record_verdict(entry, MockVerdict(ticker=f"TICK{i}"))
            journal.flush(entry)

        # Read and parse each line
        lines = journal.path.read_text().strip().split("\n")
        assert len(lines) == 3
        for line in lines:
            data = json.loads(line)
            assert "ticker" in data
            assert "action" in data

    def test_load_roundtrip(self, tmp_path):
        journal = TradeJournal(journal_dir=tmp_path)
        entry = journal.create_entry("t-001", MockCandidate())
        journal.record_agent_signals(entry, [MockSignal(), MockRiskSignal()])
        journal.record_debate(entry, MockDebateResult())
        journal.record_verdict(entry, MockVerdict())
        journal.flush(entry)

        loaded = TradeJournal.load(journal.path)
        assert len(loaded) == 1
        assert loaded[0].ticker == "TEST"
        assert loaded[0].action == "BUY"
        assert len(loaded[0].agent_signals) == 2
        assert loaded[0].agent_signals[0].agent_id == "news_agent"
        assert loaded[0].debate is not None
        assert loaded[0].debate.verdict == "BUY"


class TestFillAndCloseRecording:
    """Test execution fill and position close recording."""

    def test_record_fill(self, tmp_path):
        journal = TradeJournal(journal_dir=tmp_path)
        entry = journal.create_entry("t-001", MockCandidate())
        journal.record_verdict(entry, MockVerdict())
        journal.flush(entry)

        journal.record_fill("t-001", "ord-123", 10.05, 500, 50.0)
        assert entry.order_id == "ord-123"
        assert entry.fill_price == 10.05
        assert entry.fill_qty == 500
        assert entry.slippage_bps == 50.0

    def test_record_close(self, tmp_path):
        journal = TradeJournal(journal_dir=tmp_path)
        entry = journal.create_entry("t-001", MockCandidate())
        journal.record_verdict(entry, MockVerdict())
        journal.flush(entry)

        journal.record_close(
            "t-001",
            exit_price=10.50,
            realized_pnl=250.0,
            hold_duration_minutes=45.0,
            tranches_filled=2,
        )
        assert entry.exit_price == 10.50
        assert entry.realized_pnl == 250.0
        assert entry.hold_duration_minutes == 45.0
        assert entry.tranches_filled == 2


class TestSessionSummary:
    """Test end-of-day summary generation."""

    def test_summary_counts(self, tmp_path):
        journal = TradeJournal(journal_dir=tmp_path)

        # 2 BUY, 3 NO_TRADE
        for i in range(5):
            entry = journal.create_entry(f"t-{i}", MockCandidate(ticker=f"T{i}"))
            if i < 2:
                journal.record_verdict(entry, MockVerdict(ticker=f"T{i}"))
            else:
                journal.record_verdict(
                    entry,
                    MockVerdict(ticker=f"T{i}", action="NO_TRADE"),
                )
            entry.mfcs = 0.25
            journal.flush(entry)

        summary = journal.session_summary()
        assert summary["total_evaluations"] == 5
        assert summary["buy_count"] == 2
        assert summary["no_trade_count"] == 3
        assert summary["unique_tickers"] == 5
        assert summary["avg_mfcs"] == 0.25

    def test_summary_with_pnl(self, tmp_path):
        journal = TradeJournal(journal_dir=tmp_path)
        entry = journal.create_entry("t-001", MockCandidate())
        journal.record_verdict(entry, MockVerdict())
        entry.exit_price = 10.50
        entry.realized_pnl = 250.0
        journal.flush(entry)

        summary = journal.session_summary()
        assert summary["closed_count"] == 1
        assert summary["total_pnl"] == 250.0


class TestD66ExecutionDiagnostics:
    """D66: Test execution diagnostic fields added in S038 WS2."""

    def test_record_fill_with_execution_fields(self, tmp_path):
        journal = TradeJournal(journal_dir=tmp_path)
        entry = journal.create_entry("t-001", MockCandidate())
        journal.record_verdict(entry, MockVerdict())
        journal.flush(entry)

        journal.record_fill(
            "t-001", "ord-123", 10.05, 500, 50.0,
            order_submitted_at="2026-02-11T14:00:00Z",
            order_status="filled",
            stop_order_id="stop-456",
            time_to_fill_s=2.5,
            partial_fill_qty=None,
        )
        assert entry.order_submitted_at == "2026-02-11T14:00:00Z"
        assert entry.order_status == "filled"
        assert entry.stop_order_id == "stop-456"
        assert entry.time_to_fill_s == 2.5

    def test_record_fill_backward_compat(self, tmp_path):
        """Existing callers without new args still work."""
        journal = TradeJournal(journal_dir=tmp_path)
        entry = journal.create_entry("t-001", MockCandidate())
        journal.record_verdict(entry, MockVerdict())
        journal.flush(entry)

        journal.record_fill("t-001", "ord-123", 10.05, 500, 50.0)
        assert entry.order_id == "ord-123"
        assert entry.order_status == "filled"  # Default
        assert entry.stop_order_id == ""  # Default

    def test_record_rejection(self, tmp_path):
        journal = TradeJournal(journal_dir=tmp_path)
        entry = journal.create_entry("t-001", MockCandidate())
        journal.record_verdict(entry, MockVerdict())
        journal.flush(entry)

        journal.record_rejection(
            "t-001", "ord-789",
            rejection_code="INSUFFICIENT_BUYING_POWER",
            order_submitted_at="2026-02-11T14:00:00Z",
        )
        assert entry.order_status == "rejected"
        assert entry.rejection_code == "INSUFFICIENT_BUYING_POWER"
        assert entry.order_id == "ord-789"

    def test_record_rejection_missing_trade_id(self, tmp_path):
        """Rejection with unknown trade_id should warn, not crash."""
        journal = TradeJournal(journal_dir=tmp_path)
        journal.record_rejection("nonexistent", "ord-000", "UNKNOWN")
        # No crash

    def test_new_fields_serialize_in_to_dict(self, tmp_path):
        journal = TradeJournal(journal_dir=tmp_path)
        entry = journal.create_entry("t-001", MockCandidate())
        journal.record_verdict(entry, MockVerdict())
        entry.order_submitted_at = "2026-02-11T14:00:00Z"
        entry.order_status = "filled"
        entry.stop_order_id = "stop-xyz"
        entry.time_to_fill_s = 1.5

        d = entry.to_dict()
        assert d["order_submitted_at"] == "2026-02-11T14:00:00Z"
        assert d["order_status"] == "filled"
        assert d["stop_order_id"] == "stop-xyz"
        assert d["time_to_fill_s"] == 1.5

    def test_new_fields_default_empty(self, tmp_path):
        """New fields should not appear in to_dict when empty/None."""
        journal = TradeJournal(journal_dir=tmp_path)
        entry = journal.create_entry("t-001", MockCandidate())
        d = entry.to_dict()
        # Empty strings are still serialized (not None), but check they exist as ""
        assert d.get("order_submitted_at", "") == ""
        assert d.get("stop_order_id", "") == ""
        # None fields should be omitted
        assert "time_to_fill_s" not in d
        assert "partial_fill_qty" not in d
