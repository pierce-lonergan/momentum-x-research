"""Unit tests for LLM Arena dataset — models, DatasetManager, AutoLabeler."""

import json
import os
import tempfile
from datetime import date, datetime

import pytest

from src.llm_arena import (
    AutoLabeler,
    CatalystType,
    CorrectSignal,
    DatasetManager,
    LabelConfidence,
    LabeledScenario,
    StockOutcome,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_scenario(
    ticker="TEST",
    d=date(2026, 2, 10),
    outcome=StockOutcome.RUNNER,
    catalyst=CatalystType.FDA,
    gap_pct=0.50,
    rvol=50.0,
    max_gain=0.30,
    max_dd=-0.05,
    close_pct=0.25,
    was_traded=True,
    trade_pnl=500.0,
    open_price=5.0,
    close_price=6.25,
    headlines=None,
    sec_filings=None,
) -> LabeledScenario:
    sid = f"{ticker}_{d.isoformat()}"
    return LabeledScenario(
        ticker=ticker,
        date=d,
        scenario_id=sid,
        gap_pct=gap_pct,
        rvol=rvol,
        open_price=open_price,
        close_price=close_price,
        max_gain_pct=max_gain,
        max_drawdown_pct=max_dd,
        close_pct=close_pct,
        catalyst_type=catalyst,
        outcome=outcome,
        was_traded=was_traded,
        trade_pnl=trade_pnl,
        label_confidence=LabelConfidence.AUTO_HIGH,
        premarket_headlines=headlines or [],
        sec_filings=sec_filings or [],
        created_at=datetime(2026, 2, 10, 9, 30),
        updated_at=datetime(2026, 2, 10, 9, 30),
    )


# ---------------------------------------------------------------------------
# 1-5: LabeledScenario serialization
# ---------------------------------------------------------------------------


def test_scenario_to_dict_round_trip():
    s = _make_scenario()
    d = s.to_dict()
    s2 = LabeledScenario.from_dict(d)
    assert s2.ticker == s.ticker
    assert s2.date == s.date
    assert s2.outcome == s.outcome
    assert s2.catalyst_type == s.catalyst_type


def test_scenario_to_dict_enum_values():
    s = _make_scenario()
    d = s.to_dict()
    assert d["outcome"] == "runner"
    assert d["catalyst_type"] == "fda"
    assert d["label_confidence"] == "auto_high"
    assert d["correct_signal"] == "neutral"  # default unless set


def test_scenario_from_dict_enum_parsing():
    raw = {
        "ticker": "ACME",
        "date": "2026-03-01",
        "scenario_id": "ACME_2026-03-01",
        "catalyst_type": "earnings",
        "outcome": "fader",
        "correct_signal": "strong_bear",
        "label_confidence": "auto_low",
        "was_traded": False,
    }
    s = LabeledScenario.from_dict(raw)
    assert s.catalyst_type == CatalystType.EARNINGS
    assert s.outcome == StockOutcome.FADER
    assert s.correct_signal == CorrectSignal.STRONG_BEAR
    assert s.label_confidence == LabelConfidence.AUTO_LOW


def test_scenario_date_isoformat():
    s = _make_scenario(d=date(2026, 3, 15))
    d = s.to_dict()
    assert d["date"] == "2026-03-15"


def test_scenario_created_updated_roundtrip():
    s = _make_scenario()
    d = s.to_dict()
    s2 = LabeledScenario.from_dict(d)
    assert s2.created_at == s.created_at
    assert s2.updated_at == s.updated_at


# ---------------------------------------------------------------------------
# 6-10: DatasetManager CRUD and persistence
# ---------------------------------------------------------------------------


def test_dataset_add_and_get():
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = DatasetManager(tmpdir)
        s = _make_scenario()
        is_new = mgr.add_scenario(s)
        assert is_new is True
        retrieved = mgr.get(s.scenario_id)
        assert retrieved is not None
        assert retrieved.ticker == "TEST"


def test_dataset_add_update_returns_false():
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = DatasetManager(tmpdir)
        s = _make_scenario()
        mgr.add_scenario(s)
        is_new = mgr.add_scenario(s)  # duplicate
        assert is_new is False


def test_dataset_save_and_load():
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = DatasetManager(tmpdir)
        s1 = _make_scenario("AAPL", date(2026, 2, 10))
        s2 = _make_scenario("TSLA", date(2026, 2, 11))
        mgr.add_scenario(s1)
        mgr.add_scenario(s2)
        mgr.save()

        mgr2 = DatasetManager(tmpdir)
        n = mgr2.load()
        assert n == 2
        assert mgr2.get("AAPL_2026-02-10") is not None
        assert mgr2.get("TSLA_2026-02-11") is not None


def test_dataset_load_empty_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = DatasetManager(tmpdir)
        n = mgr.load()
        assert n == 0


def test_dataset_all_returns_list():
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = DatasetManager(tmpdir)
        for ticker in ["A", "B", "C"]:
            mgr.add_scenario(_make_scenario(ticker))
        result = mgr.all()
        assert len(result) == 3
        assert all(isinstance(s, LabeledScenario) for s in result)


# ---------------------------------------------------------------------------
# 11-14: DatasetManager filter and split
# ---------------------------------------------------------------------------


def test_dataset_filter_by_outcome():
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = DatasetManager(tmpdir)
        mgr.add_scenario(_make_scenario("R1", outcome=StockOutcome.RUNNER))
        mgr.add_scenario(_make_scenario("F1", outcome=StockOutcome.FADER))
        mgr.add_scenario(_make_scenario("F2", outcome=StockOutcome.FADER))

        runners = mgr.filter(outcome=StockOutcome.RUNNER)
        faders = mgr.filter(outcome=StockOutcome.FADER)
        assert len(runners) == 1
        assert len(faders) == 2


def test_dataset_filter_by_catalyst_and_traded():
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = DatasetManager(tmpdir)
        mgr.add_scenario(_make_scenario("X1", catalyst=CatalystType.FDA, was_traded=True))
        mgr.add_scenario(_make_scenario("X2", catalyst=CatalystType.FDA, was_traded=False))
        mgr.add_scenario(_make_scenario("X3", catalyst=CatalystType.EARNINGS, was_traded=True))

        fda_traded = mgr.filter(catalyst_type=CatalystType.FDA, was_traded=True)
        assert len(fda_traded) == 1
        assert fda_traded[0].ticker == "X1"


def test_dataset_filter_min_gap():
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = DatasetManager(tmpdir)
        mgr.add_scenario(_make_scenario("BIG", gap_pct=1.5))
        mgr.add_scenario(_make_scenario("SML", gap_pct=0.1))
        result = mgr.filter(min_gap_pct=0.5)
        assert len(result) == 1
        assert result[0].ticker == "BIG"


def test_dataset_split_preserves_proportion():
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = DatasetManager(tmpdir)
        for i in range(10):
            mgr.add_scenario(_make_scenario(f"R{i}", outcome=StockOutcome.RUNNER))
        for i in range(10):
            mgr.add_scenario(_make_scenario(f"F{i}", outcome=StockOutcome.FADER))

        train, test = mgr.split(test_pct=0.20, seed=42)
        assert len(train) + len(test) == 20
        # Each bucket has 10 items → 20% = 2 test each → 4 test total
        assert len(test) == 4


# ---------------------------------------------------------------------------
# 15-18: AutoLabeler classification logic
# ---------------------------------------------------------------------------


def test_auto_classify_fda_catalyst():
    with tempfile.TemporaryDirectory() as tmpdir:
        labeler = AutoLabeler(tmpdir)
        s = _make_scenario(headlines=["FDA approves new drug for phase 3 trial"])
        result = labeler.auto_classify_catalyst(s)
        assert result.catalyst_type == CatalystType.FDA


def test_auto_classify_sec_filing_dilution():
    with tempfile.TemporaryDirectory() as tmpdir:
        labeler = AutoLabeler(tmpdir)
        s = _make_scenario(sec_filings=["424B5"])
        result = labeler.auto_classify_catalyst(s)
        assert result.catalyst_type == CatalystType.SEC_FILING


def test_auto_classify_outcome_runner():
    with tempfile.TemporaryDirectory() as tmpdir:
        labeler = AutoLabeler(tmpdir)
        s = _make_scenario()
        s.max_gain_pct = 0.35
        s.close_pct = 0.28
        s.max_drawdown_pct = -0.03
        result = labeler.auto_classify_outcome(s)
        assert result.outcome == StockOutcome.RUNNER


def test_auto_classify_outcome_fader():
    with tempfile.TemporaryDirectory() as tmpdir:
        labeler = AutoLabeler(tmpdir)
        s = _make_scenario()
        s.max_gain_pct = 0.05
        s.max_drawdown_pct = -0.25
        s.close_pct = -0.20
        result = labeler.auto_classify_outcome(s)
        assert result.outcome == StockOutcome.FADER


# ---------------------------------------------------------------------------
# 19-20: DatasetManager stats and export_for_agent
# ---------------------------------------------------------------------------


def test_dataset_stats_structure():
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = DatasetManager(tmpdir)
        mgr.add_scenario(_make_scenario("A", was_traded=True, trade_pnl=500))
        mgr.add_scenario(_make_scenario("B", was_traded=True, trade_pnl=-200))
        mgr.add_scenario(_make_scenario("C", was_traded=False, trade_pnl=None))

        stats = mgr.stats()
        assert stats["total"] == 3
        assert stats["traded"] == 2
        assert stats["trade_wins"] == 1
        assert stats["trade_win_rate"] == 0.5
        assert "by_outcome" in stats
        assert "by_catalyst_type" in stats
        assert "by_label_confidence" in stats


def test_export_for_agent_strips_ground_truth():
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = DatasetManager(tmpdir)
        s = _make_scenario()
        mgr.add_scenario(s)

        agent_view = mgr.export_for_agent(s.scenario_id)
        assert "ticker" in agent_view
        assert "gap_pct" in agent_view
        assert "premarket_headlines" in agent_view
        # Ground truth must be absent
        assert "catalyst_type" not in agent_view
        assert "outcome" not in agent_view
        assert "correct_signal" not in agent_view
        assert "trade_pnl" not in agent_view


def test_export_for_agent_missing_scenario():
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = DatasetManager(tmpdir)
        with pytest.raises(KeyError):
            mgr.export_for_agent("NONEXISTENT_2026-01-01")


def test_auto_correct_signal_strong_bull_fda_runner():
    with tempfile.TemporaryDirectory() as tmpdir:
        labeler = AutoLabeler(tmpdir)
        s = _make_scenario(outcome=StockOutcome.RUNNER, catalyst=CatalystType.FDA)
        result = labeler.auto_set_correct_signal(s)
        assert result.correct_signal == CorrectSignal.STRONG_BULL
        assert result.correct_confidence_min >= 0.70


def test_auto_correct_signal_strong_bear_promotional():
    with tempfile.TemporaryDirectory() as tmpdir:
        labeler = AutoLabeler(tmpdir)
        s = _make_scenario(outcome=StockOutcome.FADER, catalyst=CatalystType.PROMOTIONAL)
        result = labeler.auto_set_correct_signal(s)
        assert result.correct_signal == CorrectSignal.STRONG_BEAR
        assert result.correct_confidence_min >= 0.70


def test_label_confidence_auto_high_all_data():
    with tempfile.TemporaryDirectory() as tmpdir:
        labeler = AutoLabeler(tmpdir)
        s = _make_scenario(
            was_traded=True,
            trade_pnl=500,
            open_price=5.0,
            close_price=6.0,
            rvol=50.0,
            headlines=["FDA approves new drug"],
        )
        result = labeler._assign_confidence(s)
        assert result.label_confidence == LabelConfidence.AUTO_HIGH


def test_label_confidence_unlabeled_no_data():
    with tempfile.TemporaryDirectory() as tmpdir:
        labeler = AutoLabeler(tmpdir)
        s = LabeledScenario(
            ticker="BARE",
            date=date(2026, 1, 1),
            scenario_id="BARE_2026-01-01",
        )
        result = labeler._assign_confidence(s)
        assert result.label_confidence == LabelConfidence.UNLABELED
