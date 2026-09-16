"""
Golden-file regression tests for anchor dates.

Any code change that shifts these results must be investigated and justified.
These lock in the known-good behavior of the simulator on dates where we
have real journal data to compare against.

Sprint 5: The trust framework — known outputs for known inputs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from arena.decision_replay import load_candidates_from_journals, replay_decisions
from arena.runner import _simulate_journal_trades
from arena.harness import ArenaConfig, ArenaInstance

GOLDEN_DIR = Path(__file__).parent
JOURNALS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "journals"
BARS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "bars"
HIST_DIR = Path(__file__).parent.parent / "data" / "historical"
PARAMS = {"mfcs_buy_threshold": 0.15}


def _run_date(date: str) -> dict:
    """Run decision replay + trade simulation for one date."""
    candidates = load_candidates_from_journals(date, str(JOURNALS_DIR))
    buys = replay_decisions(candidates, PARAMS)

    symbols = [b["ticker"] for b in buys] or ["SPY"]
    config = ArenaConfig(date=date, symbols=symbols, data_dir=str(HIST_DIR))
    instance = ArenaInstance(config)
    instance.data_engine.json_bars_dir = BARS_DIR
    instance.load_data()

    trades = _simulate_journal_trades(instance, buys, PARAMS)
    return {
        "n_candidates": len(candidates),
        "n_buys": len(buys),
        "n_trades": len(trades),
        "total_pnl": round(sum(t.get("pnl", 0) for t in trades), 4),
        "tickers_traded": [t["ticker"] for t in trades],
    }


def _load_golden(date: str) -> dict:
    golden_file = GOLDEN_DIR / f"golden_{date}.json"
    if not golden_file.exists():
        pytest.skip(f"Golden file not found: {golden_file}")
    with open(golden_file) as f:
        return json.load(f)


class TestMarch25Regression:
    """Mar 25: First day, conservative. MKDW/FEED/CVV traded."""

    def test_candidate_count(self):
        golden = _load_golden("2026-03-25")
        result = _run_date("2026-03-25")
        assert result["n_candidates"] == golden["n_candidates"]

    def test_buy_count(self):
        golden = _load_golden("2026-03-25")
        result = _run_date("2026-03-25")
        assert result["n_buys"] == golden["n_buys"]

    def test_trade_count(self):
        golden = _load_golden("2026-03-25")
        result = _run_date("2026-03-25")
        assert result["n_trades"] == golden["n_trades"]

    def test_pnl_within_tolerance(self):
        golden = _load_golden("2026-03-25")
        result = _run_date("2026-03-25")
        assert abs(result["total_pnl"] - golden["total_pnl"]) < 0.01

    def test_same_tickers(self):
        golden = _load_golden("2026-03-25")
        result = _run_date("2026-03-25")
        assert sorted(result["tickers_traded"]) == sorted(golden["tickers_traded"])


class TestMarch26Regression:
    """Mar 26: First real trading day. EEIQ big winner."""

    def test_candidate_count(self):
        golden = _load_golden("2026-03-26")
        result = _run_date("2026-03-26")
        assert result["n_candidates"] == golden["n_candidates"]

    def test_buy_count(self):
        golden = _load_golden("2026-03-26")
        result = _run_date("2026-03-26")
        assert result["n_buys"] == golden["n_buys"]

    def test_trade_count(self):
        golden = _load_golden("2026-03-26")
        result = _run_date("2026-03-26")
        assert result["n_trades"] == golden["n_trades"]

    def test_pnl_within_tolerance(self):
        golden = _load_golden("2026-03-26")
        result = _run_date("2026-03-26")
        assert abs(result["total_pnl"] - golden["total_pnl"]) < 0.01

    def test_same_tickers(self):
        golden = _load_golden("2026-03-26")
        result = _run_date("2026-03-26")
        assert sorted(result["tickers_traded"]) == sorted(golden["tickers_traded"])


class TestMarch27Regression:
    """Mar 27: SPY halt day. Zero BUY signals, zero trades."""

    def test_zero_buys(self):
        golden = _load_golden("2026-03-27")
        result = _run_date("2026-03-27")
        assert result["n_buys"] == 0

    def test_zero_trades(self):
        golden = _load_golden("2026-03-27")
        result = _run_date("2026-03-27")
        assert result["n_trades"] == 0

    def test_zero_pnl(self):
        golden = _load_golden("2026-03-27")
        result = _run_date("2026-03-27")
        assert result["total_pnl"] == 0.0
