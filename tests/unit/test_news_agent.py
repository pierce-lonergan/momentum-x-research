"""
MOMENTUM-X Tests: News Agent

Node ID: tests.unit.test_news_agent
Tests verify PROMPT_SIGNATURES constraints are enforced by parse_response(),
and D169 headline pre-filter correctly blocks promotional content.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from src.agents.news_agent import (
    NewsAgent,
    _filter_promotional_headlines,
    _is_promotional_headline,
)
from src.core.models import NewsSignal
from src.data.news_client import NewsItem


def _make_item(headline: str, summary: str = "") -> NewsItem:
    return NewsItem(
        headline=headline,
        summary=summary,
        source="TestSource",
        url="https://example.com",
        published_at=datetime.now(timezone.utc),
        tickers=["TEST"],
        provider="test",
    )


class TestNewsAgentParsing:
    """Test parse_response enforces all PROMPT_SIGNATURES invariants."""

    @pytest.fixture
    def agent(self) -> NewsAgent:
        return NewsAgent(model="test-model")

    def test_no_catalyst_forced_neutral(self, agent):
        """INV: No catalyst → signal MUST be NEUTRAL."""
        raw = {
            "signal": "STRONG_BULL",
            "confidence": 0.9,
            "catalyst_type": "NONE",
            "catalyst_specificity": "SPECULATIVE",
            "sentiment_score": 0.5,
            "key_reasoning": "Price action looks good",
            "red_flags": [],
            "source_citations": [],
        }
        result = agent.parse_response(raw, "TEST")
        assert result.signal == "NEUTRAL"
        assert result.confidence <= 0.3

    def test_strong_bull_requires_confirmed_major_catalyst(self, agent):
        """INV: STRONG_BULL requires CONFIRMED + FDA/M&A/EARNINGS."""
        raw = {
            "signal": "STRONG_BULL",
            "confidence": 0.95,
            "catalyst_type": "PRODUCT_LAUNCH",  # Not major enough
            "catalyst_specificity": "CONFIRMED",
            "sentiment_score": 0.8,
            "key_reasoning": "New product announced",
            "red_flags": [],
            "source_citations": [{"headline": "Test", "source": "PR", "timestamp": "now"}],
        }
        result = agent.parse_response(raw, "TEST")
        assert result.signal == "BULL"  # Downgraded from STRONG_BULL

    def test_strong_bull_requires_confirmed_specificity(self, agent):
        """INV: STRONG_BULL with RUMORED specificity must downgrade."""
        raw = {
            "signal": "STRONG_BULL",
            "confidence": 0.9,
            "catalyst_type": "M_AND_A",
            "catalyst_specificity": "RUMORED",  # Not confirmed
            "sentiment_score": 0.9,
            "key_reasoning": "M&A rumors",
            "red_flags": [],
            "source_citations": [],
        }
        result = agent.parse_response(raw, "TEST")
        assert result.signal == "BULL"
        assert result.confidence <= 0.7

    def test_analyst_upgrade_caps_at_bull_06(self, agent):
        """INV: Analyst upgrades cap at BULL with confidence <= 0.6."""
        raw = {
            "signal": "STRONG_BULL",
            "confidence": 0.95,
            "catalyst_type": "ANALYST_UPGRADE",
            "catalyst_specificity": "CONFIRMED",
            "sentiment_score": 0.7,
            "key_reasoning": "JPM upgraded to Overweight",
            "red_flags": [],
            "source_citations": [{"headline": "JPM Upgrade", "source": "Bloomberg", "timestamp": "now"}],
        }
        result = agent.parse_response(raw, "TEST")
        assert result.signal == "BULL"
        assert result.confidence <= 0.6

    def test_speculative_caps_confidence_03(self, agent):
        """INV: Speculative sources cap confidence at 0.3."""
        raw = {
            "signal": "BULL",
            "confidence": 0.8,
            "catalyst_type": "CONTRACT_WIN",
            "catalyst_specificity": "SPECULATIVE",
            "sentiment_score": 0.6,
            "key_reasoning": "Rumored contract from Reddit",
            "red_flags": [],
            "source_citations": [],
        }
        result = agent.parse_response(raw, "TEST")
        assert result.confidence <= 0.3

    def test_valid_strong_bull_passes(self, agent):
        """Valid STRONG_BULL with confirmed FDA approval should pass through."""
        raw = {
            "signal": "STRONG_BULL",
            "confidence": 0.95,
            "catalyst_type": "FDA_APPROVAL",
            "catalyst_specificity": "CONFIRMED",
            "sentiment_score": 0.95,
            "key_reasoning": "FDA approved Phase 3 drug",
            "red_flags": [],
            "source_citations": [{"headline": "FDA Approves", "source": "FDA.gov", "timestamp": "now"}],
        }
        result = agent.parse_response(raw, "TEST")
        assert result.signal == "STRONG_BULL"
        assert result.confidence == 0.95
        assert result.catalyst_type == "FDA_APPROVAL"

    # ── D114: Broader catalyst type tests ──

    def test_corporate_update_allows_bull(self, agent):
        """D114: CORPORATE_UPDATE catalyst allows BULL signal (not forced NEUTRAL)."""
        raw = {
            "signal": "BULL",
            "confidence": 0.7,
            "catalyst_type": "CORPORATE_UPDATE",
            "catalyst_specificity": "CONFIRMED",
            "sentiment_score": 0.5,
            "key_reasoning": "Company disclosed crypto holdings",
            "red_flags": [],
            "source_citations": [{"headline": "Crypto Holdings", "source": "SEC", "timestamp": "now"}],
        }
        result = agent.parse_response(raw, "BMNR")
        assert result.signal == "BULL"
        assert result.confidence <= 0.65
        assert result.confidence > 0.0

    def test_sector_catalyst_allows_bull(self, agent):
        """D114: SECTOR_CATALYST allows BULL signal with capped confidence."""
        raw = {
            "signal": "BULL",
            "confidence": 0.8,
            "catalyst_type": "SECTOR_CATALYST",
            "catalyst_specificity": "CONFIRMED",
            "sentiment_score": 0.6,
            "key_reasoning": "Bitcoin rally driving crypto miners",
            "red_flags": [],
            "source_citations": [{"headline": "BTC Rally", "source": "CoinDesk", "timestamp": "now"}],
        }
        result = agent.parse_response(raw, "TEST")
        assert result.signal == "BULL"
        assert result.confidence <= 0.65

    def test_corporate_update_strong_bull_downgraded(self, agent):
        """D114: CORPORATE_UPDATE cannot produce STRONG_BULL, must downgrade to BULL."""
        raw = {
            "signal": "STRONG_BULL",
            "confidence": 0.95,
            "catalyst_type": "CORPORATE_UPDATE",
            "catalyst_specificity": "CONFIRMED",
            "sentiment_score": 0.8,
            "key_reasoning": "Major corporate update",
            "red_flags": [],
            "source_citations": [{"headline": "Update", "source": "PR", "timestamp": "now"}],
        }
        result = agent.parse_response(raw, "TEST")
        assert result.signal == "BULL"  # Downgraded
        assert result.confidence <= 0.65  # Capped

    def test_corporate_update_speculative_still_capped(self, agent):
        """D114: CORPORATE_UPDATE + SPECULATIVE still gets speculative 0.3 cap (stricter wins)."""
        raw = {
            "signal": "BULL",
            "confidence": 0.7,
            "catalyst_type": "CORPORATE_UPDATE",
            "catalyst_specificity": "SPECULATIVE",
            "sentiment_score": 0.4,
            "key_reasoning": "Unverified corporate rumor",
            "red_flags": [],
            "source_citations": [],
        }
        result = agent.parse_response(raw, "TEST")
        # Speculative cap (0.3) is applied first, then D114 cap (0.65) — 0.3 wins
        assert result.confidence <= 0.3

    def test_no_catalyst_still_forced_neutral(self, agent):
        """D114: catalyst_type=NONE still forces NEUTRAL — no regression."""
        raw = {
            "signal": "BULL",
            "confidence": 0.7,
            "catalyst_type": "NONE",
            "catalyst_specificity": "CONFIRMED",
            "sentiment_score": 0.5,
            "key_reasoning": "No real catalyst",
            "red_flags": [],
            "source_citations": [],
        }
        result = agent.parse_response(raw, "TEST")
        assert result.signal == "NEUTRAL"
        assert result.confidence <= 0.3

    def test_output_is_news_signal_type(self, agent):
        """Result must be a NewsSignal with extended fields."""
        raw = {
            "signal": "NEUTRAL",
            "confidence": 0.5,
            "catalyst_type": "NONE",
            "catalyst_specificity": "SPECULATIVE",
            "sentiment_score": 0.0,
            "key_reasoning": "No news",
            "red_flags": [],
            "source_citations": [],
        }
        result = agent.parse_response(raw, "TEST")
        assert isinstance(result, NewsSignal)
        assert hasattr(result, "catalyst_type")
        assert hasattr(result, "sentiment_score")


class TestHeadlinePreFilter:
    """D169: Verify _is_promotional_headline and _filter_promotional_headlines."""

    # ── _is_promotional_headline: should catch ──

    @pytest.mark.parametrize("headline", [
        "12 Healthcare Stocks Moving In Pre-Market Session",
        "12 Biotech Stocks to Watch Today",
        "Top 10 Stocks Making Moves This Week",
        "Pre-Market Movers: What's Driving Markets",
        "After-Hours Buzz: Tonight's Big Movers",
        "Morning Brief: Today's Top Market Movers",
        "Stocks to Watch on Monday",
        "Hot Stocks Trending Now",
        "Penny Stocks to Watch This Week",
        "Small-Cap Stocks on the Move",
        "Today's Biggest Gainers and Losers",
        "Weekly Roundup: Best Performing Stocks",
        "Stocks Making Headlines Today",
        "Pre-Market Action: Top Gainers",
        "5 Tech Stocks Moving After Earnings",
    ])
    def test_promotional_headlines_caught(self, headline):
        """Pre-filter should identify these as promotional/non-specific."""
        assert _is_promotional_headline(headline, "XYZ"), (
            f"Expected promotional but passed: {headline!r}"
        )

    # ── _is_promotional_headline: should pass through ──

    @pytest.mark.parametrize("headline", [
        "XYZ Pharmaceuticals Receives FDA Approval for Drug ABC",
        "ABC Corp Reports Q4 EPS $0.45, Beats $0.31 Estimate by 45%",
        "DEF Inc. Signs $50M Government Contract for Satellite Services",
        "Goldman Sachs Upgrades GHI to Buy from Neutral, PT $28",
        "XYZ Inc Announces Merger Agreement with LargeCo for $12 Per Share",
        "FDA Grants Breakthrough Therapy Designation to XYZ Drug Candidate",
        "XYZ Corp CEO Resigns, Board Names Interim Chief Executive",
        "XYZ Announces $100M Share Repurchase Program",
        "SEC Issues Subpoena to XYZ Corp in Accounting Investigation",
    ])
    def test_specific_headlines_pass_through(self, headline):
        """Pre-filter should NOT block these specific catalyst headlines."""
        assert not _is_promotional_headline(headline, "XYZ"), (
            f"Expected specific but blocked: {headline!r}"
        )

    def test_multi_ticker_listicle_caught(self):
        """Headline listing 3+ distinct tickers is a roundup."""
        headline = "Small Caps Making Moves: TICK1, TICK2, XYZ, TICK3, TICK4"
        assert _is_promotional_headline(headline, "XYZ")

    def test_two_tickers_not_caught(self):
        """Two tickers in a headline might be a real M&A or comparison — don't block."""
        # e.g., "XYZ Acquires ABCD in $500M Deal"
        headline = "XYZ Acquires ABCD in $500M All-Stock Deal"
        assert not _is_promotional_headline(headline, "XYZ")

    # ── _filter_promotional_headlines ──

    def test_filter_removes_promotional(self):
        """Filter keeps only specific headlines."""
        items = [
            _make_item("12 Biotech Stocks to Watch Today"),
            _make_item("Pre-Market Movers: Big Gainers"),
            _make_item("XYZ Pharma Receives FDA Approval for Drug ABC"),
        ]
        result = _filter_promotional_headlines(items, "XYZ")
        assert len(result) == 1
        assert result[0].headline == "XYZ Pharma Receives FDA Approval for Drug ABC"

    def test_filter_empty_on_all_promotional(self):
        """If every headline is promotional, filter returns empty list."""
        items = [
            _make_item("12 Healthcare Stocks Moving In Pre-Market"),
            _make_item("Top 5 Penny Stocks to Watch"),
            _make_item("Morning Brief: Today's Biggest Movers"),
        ]
        result = _filter_promotional_headlines(items, "XYZ")
        assert result == []

    def test_filter_passes_all_specific(self):
        """If all headlines are specific, none are removed."""
        items = [
            _make_item("XYZ Receives FDA Approval for Drug X"),
            _make_item("XYZ Q4 Beats Estimates: Revenue Up 45%"),
        ]
        result = _filter_promotional_headlines(items, "XYZ")
        assert len(result) == 2

    def test_filter_empty_input(self):
        """Empty input returns empty output without error."""
        assert _filter_promotional_headlines([], "XYZ") == []


class TestNewsAgentAnalyzePreFilter:
    """D169: analyze() early-exit when all headlines are promotional."""

    @pytest.fixture
    def agent(self) -> NewsAgent:
        return NewsAgent(model="test-model")

    @pytest.mark.asyncio
    async def test_all_promotional_returns_neutral_no_llm(self, agent):
        """All-promotional headlines → NEUTRAL immediately, no LLM call."""
        items = [
            _make_item("12 Biotech Stocks Moving Pre-Market"),
            _make_item("Pre-Market Movers: Top Gainers Today"),
        ]

        with patch.object(agent, "_call_llm", new_callable=AsyncMock) as mock_llm:
            result = await agent.analyze(
                ticker="XYZ",
                company_name="XYZ Corp",
                news_items=items,
                market_cap=50_000_000,
                sector="Biotech",
            )

        mock_llm.assert_not_called()
        assert isinstance(result, NewsSignal)
        assert result.signal == "NEUTRAL"
        assert result.confidence == 0.0
        assert result.catalyst_type == "NONE"
        assert "D169_PRE_FILTER" in result.flags
        assert "ALL_HEADLINES_PROMOTIONAL" in result.flags

    @pytest.mark.asyncio
    async def test_empty_news_calls_llm(self, agent):
        """Empty news list (no items at all) still goes to LLM (existing behavior)."""
        fake_llm_response = (
            '{"signal": "NEUTRAL", "confidence": 0.0, "catalyst_type": "NONE", '
            '"catalyst_specificity": "SPECULATIVE", "sentiment_score": 0.0, '
            '"key_reasoning": "No news", "red_flags": [], "source_citations": []}',
            100.0,
        )

        with patch.object(agent, "_call_llm", new_callable=AsyncMock, return_value=fake_llm_response):
            result = await agent.analyze(
                ticker="XYZ",
                company_name="XYZ Corp",
                news_items=[],
                market_cap=None,
                sector="Unknown",
            )

        assert result.signal == "NEUTRAL"

    @pytest.mark.asyncio
    async def test_mixed_headlines_filters_and_calls_llm(self, agent):
        """Mixed headlines: promotional removed, specific ones sent to LLM."""
        items = [
            _make_item("12 Biotech Stocks to Watch Today"),  # promotional — removed
            _make_item("XYZ Pharma Receives FDA Approval for Drug ABC"),  # specific — kept
        ]

        fake_llm_response = (
            '{"signal": "STRONG_BULL", "confidence": 0.9, "catalyst_type": "FDA_APPROVAL", '
            '"catalyst_specificity": "CONFIRMED", "sentiment_score": 0.9, '
            '"key_reasoning": "FDA approved", "red_flags": [], '
            '"source_citations": [{"headline": "XYZ FDA", "source": "FDA.gov", "timestamp": "now"}]}',
            500.0,
        )

        with patch.object(agent, "_call_llm", new_callable=AsyncMock, return_value=fake_llm_response):
            result = await agent.analyze(
                ticker="XYZ",
                company_name="XYZ Pharma",
                news_items=items,
                market_cap=100_000_000,
                sector="Biotech",
            )

        assert result.signal == "STRONG_BULL"
        assert result.catalyst_type == "FDA_APPROVAL"
