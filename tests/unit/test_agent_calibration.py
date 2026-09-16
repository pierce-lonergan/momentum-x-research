"""
Tests for src.analysis.agent_calibration

Node ID: tests.unit.test_agent_calibration
Validates calibration computation, binning, and correlation logic.
"""

from __future__ import annotations

from src.analysis.agent_calibration import (
    _rank_array,
    _spearman_correlation,
    compute_agent_calibration,
)

# ── Helper fixtures ─────────────────────────────────────────────────


def _make_result(
    outcome: str = "WIN",
    intraday_return: float = 0.05,
    agents: dict | None = None,
    verdict: str = "BUY",
) -> dict:
    """Create a mock ScenarioResult dict for calibration testing."""
    if agents is None:
        agents = {
            "news_agent": {"signal": "BUY", "confidence": 0.8, "reasoning": "Catalyst"},
            "technical_agent": {"signal": "BUY", "confidence": 0.7, "reasoning": "RSI"},
        }
    return {
        "scenario": {"outcome": outcome, "intraday_return": intraday_return},
        "agent_signals": agents,
        "verdict_action": verdict,
    }


# ── Rank and Correlation Tests ──────────────────────────────────────


class TestRankArray:
    def test_simple_ranking(self):
        ranks = _rank_array([10, 30, 20])
        assert ranks == [1.0, 3.0, 2.0]

    def test_tied_ranking(self):
        ranks = _rank_array([10, 20, 20])
        assert ranks[0] == 1.0
        assert ranks[1] == 2.5
        assert ranks[2] == 2.5

    def test_all_same(self):
        ranks = _rank_array([5, 5, 5])
        assert all(r == 2.0 for r in ranks)


class TestSpearmanCorrelation:
    def test_perfect_positive(self):
        corr = _spearman_correlation([1, 2, 3, 4, 5], [10, 20, 30, 40, 50])
        assert abs(corr - 1.0) < 0.001

    def test_perfect_negative(self):
        corr = _spearman_correlation([1, 2, 3, 4, 5], [50, 40, 30, 20, 10])
        assert abs(corr - (-1.0)) < 0.001

    def test_no_correlation(self):
        # Not perfectly uncorrelated but shouldn't be near +/-1
        corr = _spearman_correlation([1, 2, 3, 4, 5], [3, 1, 4, 5, 2])
        assert abs(corr) < 0.8

    def test_too_few_values(self):
        corr = _spearman_correlation([1, 2], [3, 4])
        assert corr == 0.0


# ── Agent Calibration Tests ─────────────────────────────────────────


class TestComputeAgentCalibration:
    def test_basic_calibration(self):
        results = [
            _make_result(outcome="WIN", intraday_return=0.05),
            _make_result(outcome="WIN", intraday_return=0.03),
            _make_result(outcome="LOSS", intraday_return=-0.04),
            _make_result(outcome="LOSS", intraday_return=-0.02),
            _make_result(outcome="WIN", intraday_return=0.08),
        ]
        suite = compute_agent_calibration(results, n_bins=3)

        assert len(suite.agent_reports) >= 1
        # news_agent and technical_agent should both be present
        agent_ids = [r.agent_id for r in suite.agent_reports]
        assert "news_agent" in agent_ids
        assert "technical_agent" in agent_ids

    def test_per_agent_accuracy(self):
        results = [
            _make_result(
                outcome="WIN",
                agents={"agent_a": {"signal": "BUY", "confidence": 0.9, "reasoning": ""}},
            ),
            _make_result(
                outcome="WIN",
                agents={"agent_a": {"signal": "BUY", "confidence": 0.8, "reasoning": ""}},
            ),
            _make_result(
                outcome="LOSS",
                agents={"agent_a": {"signal": "BUY", "confidence": 0.7, "reasoning": ""}},
            ),
        ]
        suite = compute_agent_calibration(results)

        assert len(suite.agent_reports) == 1
        report = suite.agent_reports[0]
        assert report.agent_id == "agent_a"
        assert report.n_buy == 3
        assert abs(report.buy_accuracy - 2 / 3) < 0.01

    def test_neutral_signals_not_counted(self):
        results = [
            _make_result(
                outcome="WIN",
                agents={"agent_a": {"signal": "NEUTRAL", "confidence": 0.5, "reasoning": ""}},
            ),
        ]
        suite = compute_agent_calibration(results)
        report = suite.agent_reports[0]
        assert report.n_buy == 0
        assert report.n_neutral == 1
        assert report.buy_accuracy == 0.0

    def test_empty_results(self):
        suite = compute_agent_calibration([])
        assert len(suite.agent_reports) == 0
        assert suite.overall_accuracy == 0.0

    def test_calibration_bins(self):
        # Create results with varying confidence levels
        results = []
        for conf in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]:
            win = conf > 0.5  # Higher confidence → more wins
            results.append(_make_result(
                outcome="WIN" if win else "LOSS",
                intraday_return=0.05 if win else -0.03,
                agents={"agent_a": {"signal": "BUY", "confidence": conf, "reasoning": ""}},
            ))

        suite = compute_agent_calibration(results, n_bins=3)
        report = suite.agent_reports[0]

        # Should have 3 bins
        assert len(report.calibration_bins) == 3
        # Total samples across bins should equal n_buy
        total_in_bins = sum(b.n_samples for b in report.calibration_bins)
        assert total_in_bins == report.n_buy

    def test_calibration_error_computed(self):
        results = [
            _make_result(
                outcome="WIN",
                agents={"a": {"signal": "BUY", "confidence": 0.9, "reasoning": ""}},
            ),
            _make_result(
                outcome="LOSS",
                agents={"a": {"signal": "BUY", "confidence": 0.9, "reasoning": ""}},
            ),
        ]
        suite = compute_agent_calibration(results, n_bins=2)
        report = suite.agent_reports[0]
        # Calibration error should be > 0 since confidence is 0.9 but win rate is 0.5
        assert report.calibration_error > 0

    def test_to_dict_format(self):
        results = [
            _make_result(
                outcome="WIN",
                agents={"a": {"signal": "BUY", "confidence": 0.8, "reasoning": "test"}},
            ),
        ]
        suite = compute_agent_calibration(results)
        d = suite.to_dict()
        assert "overall_accuracy" in d
        assert "agents" in d
        assert len(d["agents"]) == 1
        assert "bins" in d["agents"][0]


class TestCalibrationSuite:
    def test_overall_stats(self):
        results = [
            _make_result(
                outcome="WIN",
                agents={
                    "a": {"signal": "BUY", "confidence": 0.8, "reasoning": ""},
                    "b": {"signal": "BUY", "confidence": 0.6, "reasoning": ""},
                },
            ),
            _make_result(
                outcome="LOSS",
                agents={
                    "a": {"signal": "BUY", "confidence": 0.7, "reasoning": ""},
                    "b": {"signal": "BUY", "confidence": 0.5, "reasoning": ""},
                },
            ),
        ]
        suite = compute_agent_calibration(results)
        assert suite.overall_accuracy == 0.5  # 1 correct out of 2 for each agent
