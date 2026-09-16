"""
Tests for SessionReportGenerator.generate_from_journal() (D60 fallback).

S038 WS4: Session report dual source — derive from journal JSONL when
MetricsRegistry is unreliable.
"""

from __future__ import annotations

import json
import pytest
from datetime import datetime, timezone
from pathlib import Path

from src.analysis.session_report import SessionReportGenerator, SessionReport


def _make_journal_entry(
    ticker: str = "TEST",
    action: str = "BUY",
    order_id: str = "oid-1",
    fill_price: float = 10.0,
    mfcs: float = 0.5,
    pipeline_latency_ms: float = 2500.0,
    slippage_bps: float = 5.0,
    exit_price: float | None = None,
    realized_pnl: float | None = None,
    debate: dict | None = None,
) -> dict:
    entry = {
        "trade_id": f"T-{ticker}-001",
        "ticker": ticker,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "phase": "MARKET_OPEN",
        "current_price": fill_price,
        "action": action,
        "mfcs": mfcs,
        "pipeline_latency_ms": pipeline_latency_ms,
        "order_id": order_id if action == "BUY" else "",
        "fill_price": fill_price if action == "BUY" else None,
        "fill_qty": 100 if action == "BUY" else None,
        "slippage_bps": slippage_bps if action == "BUY" else None,
    }
    if exit_price is not None:
        entry["exit_price"] = exit_price
        entry["realized_pnl"] = realized_pnl
    if debate is not None:
        entry["debate"] = debate
    return entry


@pytest.fixture
def journal_file(tmp_path: Path) -> Path:
    """Create a sample journal JSONL file."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    journal_dir = tmp_path / "journals"
    journal_dir.mkdir()
    journal_path = journal_dir / f"journal_{today}_120000.jsonl"

    entries = [
        # Evaluation that didn't trade
        _make_journal_entry(ticker="SKIP", action="NO_TRADE", order_id=""),
        # Winner
        _make_journal_entry(
            ticker="WIN",
            exit_price=11.0,
            realized_pnl=100.0,
            debate={
                "verdict": "BUY", "confidence": 0.8, "bull_strength": 0.9,
                "bear_strength": 0.2, "debate_divergence": 0.7, "position_size": "FULL",
            },
            pipeline_latency_ms=3000.0,
        ),
        # Loser
        _make_journal_entry(
            ticker="LOSE",
            exit_price=9.0,
            realized_pnl=-50.0,
            debate={
                "verdict": "STRONG_BUY", "confidence": 0.75, "bull_strength": 0.85,
                "bear_strength": 0.3, "debate_divergence": 0.55, "position_size": "HALF",
            },
            pipeline_latency_ms=2000.0,
        ),
        # Open position (no exit)
        _make_journal_entry(ticker="OPEN", pipeline_latency_ms=2500.0),
    ]

    with open(journal_path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")

    return journal_path


class TestGenerateFromJournal:
    """Tests for generate_from_journal() method."""

    def test_basic_generation(self, journal_file: Path):
        gen = SessionReportGenerator(mode="paper")
        report = gen.generate_from_journal(journal_path=journal_file)

        assert isinstance(report, SessionReport)
        assert report.mode == "paper"
        assert report.evaluations_total == 4  # 1 NO_TRADE + 3 BUY
        assert report.orders_submitted == 3  # 3 BUY entries
        assert report.orders_filled == 3  # All have order_id
        assert report.session_trades == 2  # 2 closed (WIN + LOSE)
        assert report.daily_pnl == 50.0  # 100 - 50
        assert report.win_count == 1
        assert report.loss_count == 1

    def test_debates_counted(self, journal_file: Path):
        gen = SessionReportGenerator(mode="paper")
        report = gen.generate_from_journal(journal_path=journal_file)

        assert report.debates_triggered == 2  # WIN + LOSE have debate
        assert report.debates_buy == 2  # Both BUY/STRONG_BUY

    def test_pipeline_latency_averaged(self, journal_file: Path):
        gen = SessionReportGenerator(mode="paper")
        report = gen.generate_from_journal(journal_path=journal_file)

        # (3000 + 2000 + 2500 + 2500) / 4 = 2500.0 (the NO_TRADE has 2500 default)
        assert report.pipeline_latency_mean_ms == 2500.0

    def test_fill_rate_computed(self, journal_file: Path):
        gen = SessionReportGenerator(mode="paper")
        report = gen.generate_from_journal(journal_path=journal_file)

        assert report.fill_rate_pct == 100.0  # 3/3

    def test_missing_journal_returns_empty(self, tmp_path: Path):
        gen = SessionReportGenerator(mode="paper")
        report = gen.generate_from_journal(
            journal_dir=tmp_path / "nonexistent"
        )

        assert report.evaluations_total == 0
        assert report.daily_pnl == 0.0

    def test_empty_journal_returns_empty(self, tmp_path: Path):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        journal_dir = tmp_path / "journals"
        journal_dir.mkdir()
        journal_path = journal_dir / f"journal_{today}_120000.jsonl"
        journal_path.write_text("")

        gen = SessionReportGenerator(mode="paper")
        report = gen.generate_from_journal(journal_path=journal_path)

        assert report.evaluations_total == 0

    def test_auto_finds_todays_journal(self, journal_file: Path):
        """When no explicit path given, finds today's file."""
        gen = SessionReportGenerator(mode="paper")
        report = gen.generate_from_journal(
            journal_dir=journal_file.parent
        )

        assert report.evaluations_total == 4

    def test_slippage_averaged(self, journal_file: Path):
        gen = SessionReportGenerator(mode="paper")
        report = gen.generate_from_journal(journal_path=journal_file)

        assert report.fill_slippage_mean_bps == 5.0  # All entries have 5.0

    def test_duration_computed(self, journal_file: Path):
        from datetime import timedelta
        # Set start time to 30 minutes ago so duration is meaningful
        start = datetime.now(timezone.utc) - timedelta(minutes=30)
        gen = SessionReportGenerator(mode="paper", session_start=start)
        report = gen.generate_from_journal(journal_path=journal_file)

        assert report.duration_minutes >= 29.0
