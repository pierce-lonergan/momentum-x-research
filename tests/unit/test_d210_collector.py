"""D210: Tests for SessionDataCollector.

Coverage:
  - register_candidate stores scanner fields
  - record_evaluation stores mfcs, verdict, signals
  - record_sec_data / record_short_interest / record_sentiment
  - record_rejection appends reasons
  - record_trade marks traded=True
  - save_session writes per-ticker JSON + session_summary.json
  - load_session round-trips all fields
  - _get_or_create creates missing records
  - save_session summary counts are accurate
  - fetch_and_store_bars stores bars (mocked client)
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.data.session_data_collector import SessionDataCollector, TickerSessionData


# ── Fixtures ───────────────────────────────────────────────────────────────────


def _make_collector(tmp_path: Path) -> SessionDataCollector:
    return SessionDataCollector(
        session_date=date(2026, 4, 6),
        storage_root=tmp_path,
    )


@dataclass
class FakeCandidate:
    ticker: str
    gap_pct: float = 0.15
    rvol: float = 8.5
    current_price: float = 3.50
    previous_close: float = 3.04
    float_shares: float = 5_000_000.0
    market_cap: float = 17_500_000.0
    bid: float = 3.48
    ask: float = 3.52
    premarket_volume: int = 2_000_000


# ── Tests ──────────────────────────────────────────────────────────────────────


def test_register_candidate_stores_fields(tmp_path):
    col = _make_collector(tmp_path)
    c = FakeCandidate(ticker="COCP")
    col.register_candidate(c)

    rec = col.get_record("COCP")
    assert rec is not None
    assert rec.ticker == "COCP"
    assert rec.gap_pct == pytest.approx(0.15)
    assert rec.rvol == pytest.approx(8.5)
    assert rec.current_price == pytest.approx(3.50)
    assert rec.float_shares == pytest.approx(5_000_000.0)
    assert rec.dolvol == pytest.approx(3.50 * 2_000_000)


def test_register_candidate_is_idempotent(tmp_path):
    col = _make_collector(tmp_path)
    c = FakeCandidate(ticker="SIDU", current_price=1.20)
    col.register_candidate(c)
    col.register_candidate(c)  # second registration should overwrite, not duplicate
    assert len(col.tickers) == 1


def test_record_evaluation_stores_verdict(tmp_path):
    col = _make_collector(tmp_path)
    col._get_or_create("FC")
    col.record_evaluation(
        ticker="FC",
        verdict="BUY",
        mfcs=0.72,
        mfcs_components={"news": 0.8, "technical": 0.6},
        qualifies_debate=True,
    )
    rec = col.get_record("FC")
    assert rec.final_verdict == "BUY"
    assert rec.mfcs == pytest.approx(0.72)
    assert rec.mfcs_components["news"] == pytest.approx(0.8)
    assert rec.qualifies_debate is True
    assert rec.eval_count == 1


def test_record_evaluation_increments_eval_count(tmp_path):
    col = _make_collector(tmp_path)
    col._get_or_create("GVH")
    col.record_evaluation("GVH", verdict="NO_TRADE", mfcs=0.30)
    col.record_evaluation("GVH", verdict="NO_TRADE", mfcs=0.28)
    assert col.get_record("GVH").eval_count == 2


def test_record_sec_data(tmp_path):
    col = _make_collector(tmp_path)
    col._get_or_create("VSA")
    col.record_sec_data("VSA", has_recent_filing=True, filing_type="S-3", dilution_risk="high")
    rec = col.get_record("VSA")
    assert rec.sec_has_recent_filing is True
    assert rec.sec_filing_type == "S-3"
    assert rec.sec_dilution_risk == "high"


def test_record_short_interest(tmp_path):
    col = _make_collector(tmp_path)
    col._get_or_create("MLEC")
    col.record_short_interest("MLEC", short_pct=0.35, days_to_cover=2.1, source="finra")
    rec = col.get_record("MLEC")
    assert rec.short_interest_pct == pytest.approx(0.35)
    assert rec.days_to_cover == pytest.approx(2.1)
    assert rec.short_interest_source == "finra"


def test_record_sentiment(tmp_path):
    col = _make_collector(tmp_path)
    col._get_or_create("PRFX")
    col.record_sentiment("PRFX", score=0.65, velocity=0.12, headline_count=4, source="alpaca")
    rec = col.get_record("PRFX")
    assert rec.sentiment_score == pytest.approx(0.65)
    assert rec.headline_count == 4


def test_record_rejection_appends(tmp_path):
    col = _make_collector(tmp_path)
    col._get_or_create("HUIZ")
    col.record_rejection("HUIZ", reasons=["spread too wide"], agent="DeterministicRisk")
    col.record_rejection("HUIZ", reasons=["low MFCS"])
    rec = col.get_record("HUIZ")
    assert "spread too wide" in rec.rejection_reasons
    assert "low MFCS" in rec.rejection_reasons
    assert rec.rejection_agent == "DeterministicRisk"


def test_record_trade_marks_traded(tmp_path):
    col = _make_collector(tmp_path)
    col._get_or_create("COCP")
    col.record_trade("COCP", entry_price=3.55, shares=1000)
    rec = col.get_record("COCP")
    assert rec.traded is True
    assert rec.entry_price == pytest.approx(3.55)
    assert rec.shares == 1000


def test_save_creates_files(tmp_path):
    col = _make_collector(tmp_path)
    col.register_candidate(FakeCandidate("FC"))
    col.record_evaluation("FC", verdict="BUY", mfcs=0.71)
    col.record_trade("FC", entry_price=5.10, shares=500, pnl_dollars=120.0)
    col.save_session()

    out_dir = tmp_path / "2026-04-06"
    assert (out_dir / "FC.json").exists()
    assert (out_dir / "session_summary.json").exists()


def test_save_summary_counts(tmp_path):
    col = _make_collector(tmp_path)
    for ticker in ("A", "B", "C"):
        col.register_candidate(FakeCandidate(ticker))
        col.record_evaluation(ticker, verdict="NO_TRADE", mfcs=0.3)
    col.record_trade("A", entry_price=1.0, shares=100, pnl_dollars=10.0)
    col.save_session()

    summary = json.loads((tmp_path / "2026-04-06" / "session_summary.json").read_text())
    assert summary["total_candidates"] == 3
    assert summary["total_evaluated"] == 3
    assert summary["total_traded"] == 1


def test_load_session_roundtrip(tmp_path):
    col = _make_collector(tmp_path)
    col.register_candidate(FakeCandidate("SIDU", gap_pct=0.22))
    col.record_evaluation("SIDU", verdict="BUY", mfcs=0.68)
    col.record_trade("SIDU", entry_price=0.80, exit_price=1.10, pnl_dollars=300.0, pnl_pct=0.375)
    col.save_session()

    loaded = SessionDataCollector.load_session(date(2026, 4, 6), storage_root=tmp_path)
    rec = loaded.get_record("SIDU")
    assert rec is not None
    assert rec.gap_pct == pytest.approx(0.22)
    assert rec.final_verdict == "BUY"
    assert rec.mfcs == pytest.approx(0.68)
    assert rec.pnl_dollars == pytest.approx(300.0)
    assert rec.traded is True


def test_load_session_missing_dir(tmp_path):
    loaded = SessionDataCollector.load_session(date(2020, 1, 1), storage_root=tmp_path)
    assert loaded.tickers == []


@pytest.mark.asyncio
async def test_fetch_and_store_bars(tmp_path):
    col = _make_collector(tmp_path)
    col.register_candidate(FakeCandidate("GVH"))

    mock_client = MagicMock()
    mock_client.get_bars = AsyncMock(return_value=[
        {"t": "2026-04-06T13:30:00Z", "o": 2.0, "h": 2.1, "l": 1.9, "c": 2.05, "v": 50000, "vw": 2.02},
        {"t": "2026-04-06T13:31:00Z", "o": 2.05, "h": 2.15, "l": 2.0, "c": 2.12, "v": 45000, "vw": 2.08},
    ])

    await col.fetch_and_store_bars(mock_client, tickers=["GVH"])
    rec = col.get_record("GVH")
    assert len(rec.minute_bars) == 2
    assert rec.bars_fetched_at is not None
    mock_client.get_bars.assert_called_once()


@pytest.mark.asyncio
async def test_fetch_and_store_bars_client_failure(tmp_path):
    col = _make_collector(tmp_path)
    col.register_candidate(FakeCandidate("FAIL"))

    mock_client = MagicMock()
    mock_client.get_bars = AsyncMock(side_effect=RuntimeError("network error"))

    # Should not raise — errors are swallowed with a warning
    await col.fetch_and_store_bars(mock_client, tickers=["FAIL"])
    rec = col.get_record("FAIL")
    assert rec.minute_bars == []


def test_all_records_returns_copy(tmp_path):
    col = _make_collector(tmp_path)
    col.register_candidate(FakeCandidate("A"))
    col.register_candidate(FakeCandidate("B"))
    records = col.all_records()
    assert set(records.keys()) == {"A", "B"}
    # Modifying the copy doesn't affect the collector
    del records["A"]
    assert "A" in col.tickers
