"""Tests for decision replay engine."""

from __future__ import annotations

import pytest

from arena.decision_replay import (
    CandidateContext,
    replay_decisions,
    _evaluate_technical,
    _signal_to_score,
    SIGNAL_NUMERIC,
)


def _make_candidate(
    ticker: str = "TEST",
    gap_pct: float = 0.20,
    rvol: float = 5.0,
    current_price: float = 5.0,
    journal_action: str = "BUY",
    news_signal: str = "BULL",
    news_confidence: float = 0.5,
) -> CandidateContext:
    """Create a test candidate with journal signals."""
    return CandidateContext(
        ticker=ticker,
        current_price=current_price,
        previous_close=current_price / (1 + gap_pct),
        gap_pct=gap_pct,
        rvol=rvol,
        premarket_volume=1_000_000,
        has_news_catalyst=(news_signal in ("BULL", "STRONG_BULL")),
        entry_price=current_price,
        stop_loss=current_price * 0.96,
        journal_signals=[
            {"agent_id": "news_agent", "signal": news_signal,
             "confidence": news_confidence, "reasoning": "Test catalyst"},
            {"agent_id": "fundamental_agent", "signal": "NEUTRAL",
             "confidence": 0.0, "reasoning": ""},
            {"agent_id": "risk_agent", "signal": "BULL",
             "confidence": 0.95, "risk_score": 0.05, "reasoning": "Low risk"},
        ],
        journal_mfcs=0.25,
        journal_action=journal_action,
        journal_component_scores={"catalyst_news": 0.25},
        journal_risk_score=0.05,
    )


class TestTechnicalEvaluation:
    """Test the gap momentum mode re-evaluation."""

    def test_gap_momentum_activates_above_threshold(self):
        cand = _make_candidate(gap_pct=0.20, rvol=6.0)
        # momentum_score = 0.20 * 6.0 = 1.2 > threshold 1.0
        signal, conf = _evaluate_technical(cand, gm_threshold=1.0, gm_min_gap=0.08, gm_min_rvol=2.5)
        assert signal == "STRONG_BULL"

    def test_gap_momentum_inactive_below_threshold(self):
        cand = _make_candidate(gap_pct=0.10, rvol=3.0)
        # momentum_score = 0.10 * 3.0 = 0.3 < threshold 1.0
        signal, conf = _evaluate_technical(cand, gm_threshold=1.0, gm_min_gap=0.08, gm_min_rvol=2.5)
        assert signal != "STRONG_BULL"

    def test_higher_threshold_blocks_marginal_stocks(self):
        cand = _make_candidate(gap_pct=0.15, rvol=5.0)
        # momentum_score = 0.15 * 5.0 = 0.75

        # At threshold 0.5: activates
        sig_low, _ = _evaluate_technical(cand, gm_threshold=0.5, gm_min_gap=0.08, gm_min_rvol=2.5)
        assert sig_low == "STRONG_BULL"

        # At threshold 1.0: doesn't activate
        sig_high, _ = _evaluate_technical(cand, gm_threshold=1.0, gm_min_gap=0.08, gm_min_rvol=2.5)
        assert sig_high != "STRONG_BULL"

    def test_min_rvol_gate(self):
        cand = _make_candidate(gap_pct=0.50, rvol=2.0)
        # momentum_score = 0.50 * 2.0 = 1.0 > threshold, but rvol < min_rvol
        signal, _ = _evaluate_technical(cand, gm_threshold=0.5, gm_min_gap=0.08, gm_min_rvol=2.5)
        assert signal != "STRONG_BULL"

    def test_min_gap_gate(self):
        cand = _make_candidate(gap_pct=0.05, rvol=30.0)
        # momentum_score = 0.05 * 30 = 1.5 > threshold, but gap < min_gap
        signal, _ = _evaluate_technical(cand, gm_threshold=1.0, gm_min_gap=0.08, gm_min_rvol=2.5)
        assert signal != "STRONG_BULL"


class TestReplayDecisions:
    """Test the full decision replay pipeline."""

    def test_strong_candidate_produces_buy(self):
        cand = _make_candidate(gap_pct=0.25, rvol=8.0, news_signal="BULL")
        buys = replay_decisions([cand], {})
        assert len(buys) == 1
        assert buys[0]["ticker"] == "TEST"

    def test_weak_candidate_filtered_by_mfcs(self):
        cand = _make_candidate(gap_pct=0.05, rvol=1.0, news_signal="NEUTRAL", news_confidence=0.0)
        buys = replay_decisions([cand], {"mfcs_buy_threshold": 0.15})
        assert len(buys) == 0

    def test_different_thresholds_produce_different_buy_sets(self):
        # Marginal candidate: gap_pct=0.12, rvol=4.0, momentum_score=0.48
        cand = _make_candidate(gap_pct=0.12, rvol=4.0, news_signal="BULL")

        # Loose threshold: should pass
        buys_loose = replay_decisions([cand], {"gap_momentum_score_threshold": 0.3})
        # Tight threshold: should fail
        buys_tight = replay_decisions([cand], {"gap_momentum_score_threshold": 1.0})

        # The whole point: different thresholds -> different trade sets
        assert len(buys_loose) != len(buys_tight) or (
            len(buys_loose) > 0 and buys_loose[0].get("tech_signal") != buys_tight[0].get("tech_signal", "")
        )

    def test_bearish_consensus_blocks_buy(self):
        cand = CandidateContext(
            ticker="BAD",
            current_price=5.0,
            previous_close=4.0,
            gap_pct=0.25,
            rvol=6.0,
            premarket_volume=500_000,
            has_news_catalyst=False,
            entry_price=5.0,
            stop_loss=4.80,
            journal_signals=[
                {"agent_id": "news_agent", "signal": "BEAR", "confidence": 0.7, "reasoning": "Negative"},
                {"agent_id": "fundamental_agent", "signal": "BEAR", "confidence": 0.6, "reasoning": "Weak"},
                {"agent_id": "risk_agent", "signal": "NEUTRAL", "confidence": 0.5, "risk_score": 0.5, "reasoning": ""},
            ],
            journal_mfcs=-0.2,
            journal_action="NO_TRADE",
            journal_component_scores={},
            journal_risk_score=0.5,
        )
        buys = replay_decisions([cand], {"gap_momentum_score_threshold": 0.5})
        assert len(buys) == 0  # Bearish consensus blocks even with gap momentum

    def test_stop_loss_override_applied(self):
        cand = _make_candidate(gap_pct=0.25, rvol=8.0)
        buys = replay_decisions([cand], {"stop_loss_pct": 0.03})
        assert len(buys) == 1
        expected_stop = cand.current_price * (1 - 0.03)
        assert abs(buys[0]["stop_loss"] - expected_stop) < 0.01

    def test_new_buy_flagged(self):
        """Candidate that was NO_TRADE in journal should be flagged as new."""
        cand = _make_candidate(gap_pct=0.30, rvol=10.0, journal_action="NO_TRADE")
        buys = replay_decisions([cand], {"gap_momentum_score_threshold": 0.5})
        if buys:
            assert buys[0]["is_new_buy"] is True

    def test_multiple_candidates_filtered(self):
        candidates = [
            _make_candidate("STRONG", gap_pct=0.30, rvol=10.0),
            _make_candidate("MARGINAL", gap_pct=0.10, rvol=3.0),
            _make_candidate("WEAK", gap_pct=0.03, rvol=1.0, news_signal="NEUTRAL", news_confidence=0.0),
        ]
        buys = replay_decisions(candidates, {"gap_momentum_score_threshold": 1.0})
        buy_tickers = [b["ticker"] for b in buys]
        assert "STRONG" in buy_tickers
        assert "WEAK" not in buy_tickers


class TestSignalScoring:
    def test_strong_bull_high_confidence(self):
        assert _signal_to_score("STRONG_BULL", 0.9) == pytest.approx(0.9)

    def test_bear_negative_score(self):
        assert _signal_to_score("BEAR", 0.7) == pytest.approx(-0.35)

    def test_neutral_zero(self):
        assert _signal_to_score("NEUTRAL", 0.5) == 0.0

    def test_confidence_floor(self):
        # Low confidence directional signal should use 0.20 floor
        assert _signal_to_score("BULL", 0.05) == pytest.approx(0.5 * 0.20)


class TestBootstrapCI:
    """Test the stats module."""

    def test_bootstrap_with_clear_winner(self):
        from arena.stats import bootstrap_profit_factor
        # All profitable trades
        trades = [{"pnl": 1.0} for _ in range(20)]
        pf, ci_lo, ci_hi = bootstrap_profit_factor(trades)
        assert pf > 1.0
        assert ci_lo > 1.0  # Even CI lower should be profitable

    def test_bootstrap_with_mixed_results(self):
        from arena.stats import bootstrap_profit_factor
        trades = [{"pnl": 1.0}] * 5 + [{"pnl": -0.5}] * 5
        pf, ci_lo, ci_hi = bootstrap_profit_factor(trades)
        assert ci_lo < pf < ci_hi

    def test_rank_by_ci_lower(self):
        from arena.stats import rank_by_ci_lower
        results = [
            {"sim_trades": [{"pnl": 2.0}] * 10 + [{"pnl": -1.0}] * 5},
            {"sim_trades": [{"pnl": 0.5}] * 10 + [{"pnl": -0.1}] * 5},
        ]
        ranked = rank_by_ci_lower(results)
        # Both should have CI fields
        assert "pf_ci_lower" in ranked[0]
        assert "pf_ci_upper" in ranked[0]
