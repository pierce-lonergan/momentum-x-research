"""
MOMENTUM-X Integration Test: Full Pipeline

Node ID: tests.integration.test_pipeline
Graph Link: tests end-to-end pipeline flow

Tests the full path: CandidateStock → Orchestrator → TradeVerdict
All LLM calls are mocked to test pipeline wiring, not LLM quality.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch, MagicMock

from src.core.models import (
    CandidateStock,
    TradeVerdict,
)
from src.core.orchestrator import Orchestrator
from config.settings import Settings


class TestFullPipeline:
    """End-to-end pipeline integration test."""

    @pytest.fixture
    def candidate(self) -> CandidateStock:
        return CandidateStock(
            ticker="BOOM",
            current_price=8.50,
            previous_close=5.00,
            gap_pct=0.70,
            gap_classification="EXPLOSIVE",
            rvol=12.5,
            premarket_volume=1_500_000,
            float_shares=3_000_000,
            scan_timestamp=datetime.now(timezone.utc),
            scan_phase="PRE_MARKET",
        )

    @pytest.mark.asyncio
    async def test_pipeline_produces_verdict(self, candidate):
        """Full pipeline should produce a TradeVerdict."""
        settings = Settings()

        # Mock litellm.acompletion to return valid JSON responses
        mock_news_response = MagicMock()
        mock_news_response.choices = [MagicMock()]
        mock_news_response.choices[0].message.content = (
            '{"signal": "STRONG_BULL", "confidence": 0.9, '
            '"catalyst_type": "FDA_APPROVAL", "catalyst_specificity": "CONFIRMED", '
            '"sentiment_score": 0.95, "key_reasoning": "FDA approved drug", '
            '"red_flags": [], "source_citations": [{"headline": "FDA Approves", '
            '"source": "FDA.gov", "timestamp": "now"}]}'
        )

        mock_tech_response = MagicMock()
        mock_tech_response.choices = [MagicMock()]
        mock_tech_response.choices[0].message.content = (
            '{"signal": "BULL", "confidence": 0.75, '
            '"pattern_identified": "BULL_FLAG", "pattern_timeframe": "5min", '
            '"breakout_confirmed": true, "breakout_rvol": 4.0, '
            '"vwap_above": true, "projected_target": 12.0, '
            '"stop_loss_level": 7.50, "key_reasoning": "Bull flag breakout", '
            '"red_flags": []}'
        )

        mock_risk_response = MagicMock()
        mock_risk_response.choices = [MagicMock()]
        mock_risk_response.choices[0].message.content = (
            '{"signal": "APPROVE", "risk_score": 0.15, '
            '"critical_risks": [], "risk_breakdown": {"liquidity": 0.1}, '
            '"veto_reason": null, "position_size_recommendation": "FULL", '
            '"key_reasoning": "Low risk setup"}'
        )

        mock_fundamental_response = MagicMock()
        mock_fundamental_response.choices = [MagicMock()]
        mock_fundamental_response.choices[0].message.content = (
            '{"signal": "STRONG_BULL", "confidence": 0.85, '
            '"float_assessment": "NANO", "short_squeeze_potential": 0.7, '
            '"dilution_risk": 0.0, "key_reasoning": "3M float, no dilution", "red_flags": []}'
        )

        mock_institutional_response = MagicMock()
        mock_institutional_response.choices = [MagicMock()]
        mock_institutional_response.choices[0].message.content = (
            '{"signal": "BULL", "confidence": 0.6, '
            '"unusual_options_detected": true, "dark_pool_significant": true, '
            '"insider_net_direction": "BUYING", "smart_money_score": 0.7, '
            '"key_reasoning": "Options + dark pool activity", "red_flags": []}'
        )

        mock_deep_search_response = MagicMock()
        mock_deep_search_response.choices = [MagicMock()]
        mock_deep_search_response.choices[0].message.content = (
            '{"signal": "BULL", "confidence": 0.7, '
            '"sec_clean": true, "social_confirmation": true, '
            '"historical_precedent": true, "key_reasoning": "Clean + confirmed", "red_flags": []}'
        )

        # Debate responses
        mock_bull_response = MagicMock()
        mock_bull_response.choices = [MagicMock()]
        mock_bull_response.choices[0].message.content = (
            "FDA approval is a material catalyst. Float is only 3M shares. "
            "RVOL at 12.5x confirms massive buying interest."
        )

        mock_bear_response = MagicMock()
        mock_bear_response.choices = [MagicMock()]
        mock_bear_response.choices[0].message.content = (
            "High gap stocks often retrace 50% within the first hour. "
            "Bid-ask spread may widen at these levels."
        )

        mock_judge_response = MagicMock()
        mock_judge_response.choices = [MagicMock()]
        mock_judge_response.choices[0].message.content = (
            '{"verdict": "STRONG_BUY", "confidence": 0.85, '
            '"bull_strength": 0.9, "bear_strength": 0.2, '
            '"key_reasoning": "FDA catalyst + low float + massive volume", '
            '"entry_price": 8.50, "stop_loss": 7.50, '
            '"target_prices": [9.35, 10.20, 11.05], '
            '"time_horizon": "INTRADAY"}'
        )

        # D101: Only 2 LLM agents active (news, fundamental).
        # Technical + Risk are now deterministic (zero LLM calls).
        # Institutional/deep_search skipped (weight=0.00).
        # Debate killed (max_debate_attempts=0).
        with patch("litellm.acompletion") as mock_llm:
            mock_llm.side_effect = [
                mock_news_response,
                mock_fundamental_response,
            ]

            orchestrator = Orchestrator(settings)
            verdict = await orchestrator.evaluate_candidate(
                candidate=candidate,
                news_items=[],
            )

        assert isinstance(verdict, TradeVerdict)
        assert verdict.ticker == "BOOM"
        # D101: With bipolar scoring and deterministic agents, MFCS should be positive
        # when news + fundamental are bullish (even if technical is NEUTRAL from no data)
        assert verdict.mfcs > 0.0
        assert verdict.position_size_pct <= 0.15  # D94: MFCS-scaled sizing (max 15%)

    @pytest.mark.asyncio
    async def test_pipeline_risk_veto_blocks_trade(self, candidate):
        """Risk VETO should produce NO_TRADE regardless of other signals.

        D101: Risk agent is now deterministic. VETO is triggered by passing
        sec_filings with an S-3 dilution filing through the pipeline.
        """
        settings = Settings()

        mock_news = MagicMock()
        mock_news.choices = [MagicMock()]
        mock_news.choices[0].message.content = (
            '{"signal": "STRONG_BULL", "confidence": 0.95, '
            '"catalyst_type": "M_AND_A", "catalyst_specificity": "CONFIRMED", '
            '"sentiment_score": 0.9, "key_reasoning": "M&A confirmed", '
            '"red_flags": [], "source_citations": []}'
        )

        mock_fundamental = MagicMock()
        mock_fundamental.choices = [MagicMock()]
        mock_fundamental.choices[0].message.content = (
            '{"signal": "BULL", "confidence": 0.6, '
            '"float_assessment": "MICRO", "short_squeeze_potential": 0.3, '
            '"dilution_risk": 0.2, "key_reasoning": "Low float", "red_flags": []}'
        )

        # The shipped default for risk_veto_mode is ADVISORY (ADR-026 D25: the
        # Phase-3 backtesting default, where the risk agent hallucinated vetoes
        # from sparse data). In ADVISORY mode a risk VETO is logged and the MFCS
        # score still decides, so this test — whose whole subject is the veto
        # *blocking* a trade — must ask for HARD explicitly. Production does the
        # same via SCORE_RISK_VETO_MODE=HARD in .env; relying on the library
        # default here would test the backtesting configuration by accident.
        settings.scoring.risk_veto_mode = "HARD"

        # D101: Provide SEC filings with S-3 to trigger deterministic risk VETO
        sec_filings = {
            "filings": [
                {
                    "form": "S-3",
                    "description": "Shelf registration for up to $50M in securities",
                    # Anchored to the run date, not hardcoded: the agent's
                    # contract is about a *recent* filing, and a fixed date
                    # silently ages out of every window that depends on it.
                    "date": (datetime.now(timezone.utc) - timedelta(days=1))
                            .strftime("%Y-%m-%d"),
                }
            ]
        }

        # D101: Only 2 LLM agents active (news, fundamental).
        # Technical + Risk are deterministic.
        # AsyncMock, not MagicMock: `litellm.acompletion` is awaited. With a plain
        # MagicMock both LLM agents raise on await, the orchestrator sees zero
        # directional agents, and D101 vetoes on *consensus* — so the S-3 dilution
        # veto this test exists to exercise never runs, and the assertion below
        # passes or fails for the wrong reason.
        # A two-element side_effect list is not enough: the base agent retries a
        # primary failure against a fallback and then an emergency model, so the
        # list is exhausted, the remaining calls raise StopAsyncIteration, and
        # after five the llm_provider breaker trips. Dispatch on the prompt
        # instead, so every call — original or retry — gets a valid response.
        def _dispatch(*args, **kwargs):
            blob = str(kwargs.get("messages", args))
            return mock_fundamental if "dilution" in blob.lower() else mock_news

        with patch("litellm.acompletion", new_callable=AsyncMock) as mock_llm:
            mock_llm.side_effect = _dispatch

            orchestrator = Orchestrator(settings)
            verdict = await orchestrator.evaluate_candidate(
                candidate=candidate,
                sec_filings=sec_filings,
            )

        assert verdict.action == "NO_TRADE"
        assert (
            "VETO" in verdict.reasoning_summary
            or "Dilution" in verdict.reasoning_summary
            or "dilution" in verdict.reasoning_summary
        ), verdict.reasoning_summary
