"""D192: Tests for multi-tier short interest pipeline and SQUEEZE archetype detection.

All tests are fully mocked — no real API calls, no network access.

Test coverage:
  1.  test_alpaca_short_interest_fetch         — Tier 1 happy path
  2.  test_yfinance_fallback                   — Tier 2 happy path
  3.  test_finviz_fallback                     — Tier 3 happy path
  4.  test_tier_cascade_alpaca_fails           — Tier 1 fails → Tier 2 succeeds
  5.  test_all_tiers_fail                      — All tiers fail → UNKNOWN
  6.  test_squeeze_classification_above_30     — >30% short + gap → SQUEEZE
  7.  test_high_short_classification_above_20  — 20-30% → HIGH_SHORT
  8.  test_normal_classification_below_20      — <20% → NORMAL
  9.  test_squeeze_blocks_shorting             — SQUEEZE sets shorting_blocked_by_squeeze
  10. test_squeeze_widens_faller_score         — SQUEEZE reduces faller score by 0.30
  11. test_cache_hit                           — Second call uses cached result
  12. test_batch_fetch                         — Multiple tickers fetched concurrently
  13. test_itrm_scenario                       — 30%+ short float + gap → SQUEEZE end-to-end
  14. test_finviz_anti_scraping_headers        — Finviz request includes required headers
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from src.data.short_interest import (
    ShortInterestProvider,
    ShortInterestResult,
    SqueezeClassification,
    FINVIZ_HEADERS,
    _parse_finviz_short_float,
    _estimate_age_days,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def provider():
    """ShortInterestProvider with test credentials."""
    return ShortInterestProvider(
        alpaca_api_key="test-key",
        alpaca_secret="test-secret",
    )


@pytest.fixture
def provider_no_alpaca():
    """ShortInterestProvider without Alpaca credentials (Tier 1 skipped)."""
    return ShortInterestProvider()


def _make_alpaca_response(short_shares: int = 3_500_000, settlement_date: str = "2026-03-15") -> dict:
    """Build a mock Alpaca short interest API response."""
    return {
        "short_interest": [
            {
                "date": settlement_date,
                "short_interest": short_shares,
                "short_exempt_interest": 0,
            }
        ]
    }


def _make_yfinance_info(short_pct_float: float = 0.35, shares_short: int = 3_500_000) -> dict:
    """Build a mock yfinance Ticker.info dict. shortPercentOfFloat is decimal (0.35 = 35%)."""
    return {
        "shortPercentOfFloat": short_pct_float,
        "sharesShort": shares_short,
        "floatShares": 10_000_000,
        "averageVolume": 500_000,
    }


def _make_finviz_html(short_float_pct: str = "32.45%") -> str:
    """Build minimal Finviz quote HTML containing a Short Float cell."""
    return f"""
    <html><body>
    <table class="snapshot-table2">
      <tr>
        <td class="snapshot-td2-cp">Short Float</td>
        <td class="snapshot-td2">{short_float_pct}</td>
      </tr>
    </table>
    </body></html>
    """


# ── Helpers to build minimal FallerAssessment dependencies ───────────────────


@dataclass
class _MockCandidate:
    ticker: str = "ITRM"
    current_price: float = 5.00
    gap_pct: float = 0.45       # 45% gap — well above 5% threshold
    avg_daily_volume: Optional[float] = 1_000_000
    float_shares: Optional[int] = 10_000_000
    has_news_catalyst: bool = True


@dataclass
class _MockSignal:
    agent_id: str = "news_agent"
    signal: str = "BULL"
    reasoning: str = "Strong positive catalyst"
    filing_summary: str = ""


@dataclass
class _MockScoredCandidate:
    agent_signals: list = field(default_factory=list)


# ── Test Class ────────────────────────────────────────────────────────────────


class TestShortInterestTiers:
    """Tests for individual tier fetch methods."""

    @pytest.mark.asyncio
    async def test_alpaca_short_interest_fetch(self, provider):
        """Tier 1: Alpaca returns short_shares → result has short_float_pct."""
        alpaca_data = _make_alpaca_response(short_shares=3_500_000)
        float_from_snapshot = None  # Alpaca snapshot doesn't expose float; yfinance fills it

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = alpaca_data

        # Alpaca float fetch returns None (typical) → short_float_pct also None
        # but short_shares is set — caller can combine with external float
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.get.return_value = mock_resp

            result = await provider._fetch_alpaca("ITRM")

        # short_shares should be populated even if float_pct can't be computed
        assert result is not None
        assert result.ticker == "ITRM"
        assert result.short_shares == 3_500_000
        assert result.data_source == "alpaca"
        assert result.fetch_latency_ms >= 0

    @pytest.mark.asyncio
    async def test_yfinance_fallback(self, provider):
        """Tier 2: yfinance returns shortPercentOfFloat=0.35 → result has 35.0%."""
        yf_info = _make_yfinance_info(short_pct_float=0.35)

        with patch("yfinance.Ticker") as mock_ticker_cls:
            mock_ticker = MagicMock()
            mock_ticker_cls.return_value = mock_ticker
            mock_ticker.info = yf_info

            result = await provider._fetch_yfinance("ITRM")

        assert result is not None
        assert result.ticker == "ITRM"
        assert result.short_float_pct == pytest.approx(35.0, abs=0.01)
        assert result.short_shares == 3_500_000
        assert result.float_shares == 10_000_000
        assert result.days_to_cover == pytest.approx(7.0, abs=0.1)
        assert result.data_source == "yfinance"

    @pytest.mark.asyncio
    async def test_finviz_fallback(self, provider):
        """Tier 3: Finviz HTML contains Short Float 32.45% → result has 32.45%."""
        html = _make_finviz_html("32.45%")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.get.return_value = mock_resp

            result = await provider._fetch_finviz("ITRM")

        assert result is not None
        assert result.ticker == "ITRM"
        assert result.short_float_pct == pytest.approx(32.45, abs=0.01)
        assert result.data_source == "finviz"

    @pytest.mark.asyncio
    async def test_tier_cascade_alpaca_fails(self, provider):
        """
        Tier 1 returns HTTP 404 → Tier 2 succeeds.
        Result should have data_source='yfinance'.
        """
        alpaca_resp = MagicMock()
        alpaca_resp.status_code = 404

        yf_info = _make_yfinance_info(short_pct_float=0.42)

        with patch.object(provider, "_fetch_alpaca", new_callable=AsyncMock) as mock_alpaca, \
             patch.object(provider, "_fetch_yfinance", new_callable=AsyncMock) as mock_yf:

            mock_alpaca.return_value = None  # Tier 1 returns None (404 path)
            mock_yf.return_value = ShortInterestResult(
                ticker="ITRM",
                short_float_pct=42.0,
                data_source="yfinance",
            )

            result = await provider.get_short_interest("ITRM")

        assert result.data_source == "yfinance"
        assert result.short_float_pct == pytest.approx(42.0, abs=0.01)
        mock_alpaca.assert_called_once_with("ITRM")
        mock_yf.assert_called_once_with("ITRM")

    @pytest.mark.asyncio
    async def test_all_tiers_fail(self, provider):
        """All three tiers return None → result has classification=UNKNOWN."""
        with patch.object(provider, "_fetch_alpaca", new_callable=AsyncMock, return_value=None), \
             patch.object(provider, "_fetch_yfinance", new_callable=AsyncMock, return_value=None), \
             patch.object(provider, "_fetch_finviz", new_callable=AsyncMock, return_value=None), \
             patch.object(provider, "_fetch_finra_stub", new_callable=AsyncMock, return_value=None):

            result = await provider.get_short_interest("ZZZT")

        assert result.ticker == "ZZZT"
        assert result.short_float_pct is None
        assert result.classification == SqueezeClassification.UNKNOWN
        assert result.data_source == "all_tiers_failed"
        assert result.error is not None


class TestSqueezeClassification:
    """Tests for the classify() method thresholds."""

    def test_squeeze_classification_above_30(self, provider):
        """short_float_pct=35% + gap=45% → SQUEEZE."""
        result = ShortInterestResult(ticker="ITRM", short_float_pct=35.0)
        classification = provider.classify(result, gap_pct=0.45)
        assert classification == SqueezeClassification.SQUEEZE

    def test_high_short_classification_above_20(self, provider):
        """short_float_pct=25% + gap=3% (below 5% trigger) → HIGH_SHORT."""
        result = ShortInterestResult(ticker="MEME", short_float_pct=25.0)
        # Gap of 3% doesn't trigger squeeze (needs >5%)
        classification = provider.classify(result, gap_pct=0.03)
        assert classification == SqueezeClassification.HIGH_SHORT

    def test_normal_classification_below_20(self, provider):
        """short_float_pct=10% → NORMAL regardless of gap."""
        result = ShortInterestResult(ticker="AAPL", short_float_pct=10.0)
        classification = provider.classify(result, gap_pct=0.50)
        assert classification == SqueezeClassification.NORMAL

    def test_unknown_classification_on_none(self, provider):
        """short_float_pct=None → UNKNOWN (no data to decide)."""
        result = ShortInterestResult(ticker="ZZZT")
        classification = provider.classify(result, gap_pct=0.40)
        assert classification == SqueezeClassification.UNKNOWN

    def test_squeeze_boundary_exactly_30_pct(self, provider):
        """short_float_pct=30.0% exactly + gap=6% → SQUEEZE (>= threshold)."""
        result = ShortInterestResult(ticker="TEST", short_float_pct=30.0)
        classification = provider.classify(result, gap_pct=0.06)
        assert classification == SqueezeClassification.SQUEEZE

    def test_high_short_with_small_gap_no_squeeze(self, provider):
        """short_float_pct=35% but gap=2% (below 5%) → HIGH_SHORT, not SQUEEZE."""
        result = ShortInterestResult(ticker="TEST", short_float_pct=35.0)
        classification = provider.classify(result, gap_pct=0.02)
        assert classification == SqueezeClassification.HIGH_SHORT


class TestFallerDetectionIntegration:
    """Tests for squeeze signal integration into FallerRiskDetector.score()."""

    def _make_config(self):
        from config.settings import FallerDetectionConfig
        return FallerDetectionConfig()

    def _make_scored(self, signals=None):
        from unittest.mock import MagicMock
        scored = MagicMock()
        scored.agent_signals = signals or []
        return scored

    def _make_candidate(self, ticker="ITRM", gap_pct=0.45, float_shares=10_000_000):
        candidate = MagicMock()
        candidate.ticker = ticker
        candidate.current_price = 5.00
        candidate.gap_pct = gap_pct
        candidate.avg_daily_volume = 1_000_000
        candidate.float_shares = float_shares
        candidate.has_news_catalyst = True
        # D212-B reads this via getattr; a bare MagicMock returns a Mock that is
        # "not None" and then fails the numeric comparison. None is the contract's
        # absent value (src/core/models.py: prior_gap_count: int | None).
        candidate.prior_gap_count = None
        return candidate

    def test_squeeze_blocks_shorting(self):
        """When squeeze=SQUEEZE, assessment.shorting_blocked_by_squeeze=True."""
        from src.execution.faller_detection import FallerRiskDetector

        config = self._make_config()
        detector = FallerRiskDetector(config)
        candidate = self._make_candidate()
        scored = self._make_scored()
        indicators = {"vwap": 4.80, "rsi_9": 72.0}

        si_result = ShortInterestResult(
            ticker="ITRM",
            short_float_pct=35.0,
            classification=SqueezeClassification.SQUEEZE,
            data_source="yfinance",
        )

        assessment = detector.score(
            candidate, scored, indicators, short_interest_result=si_result
        )

        assert assessment.shorting_blocked_by_squeeze is True
        assert assessment.squeeze_classification == "squeeze"

    def test_squeeze_widens_faller_score(self):
        """SQUEEZE applies bullish credit of weight_squeeze_faller_reduction (0.30).

        Verifies via bullish_factors (not absolute score delta) because scores
        can clamp to [0, 1] — the factor list is always truthful.
        """
        from src.execution.faller_detection import FallerRiskDetector

        config = self._make_config()
        detector = FallerRiskDetector(config)
        candidate = self._make_candidate()
        scored = self._make_scored()
        indicators = {"vwap": 4.80, "rsi_9": 72.0}

        si_result = ShortInterestResult(
            ticker="ITRM",
            short_float_pct=35.0,
            classification=SqueezeClassification.SQUEEZE,
            data_source="yfinance",
        )
        assessment = detector.score(
            candidate, scored, indicators, short_interest_result=si_result
        )

        # The squeeze credit should appear explicitly in bullish_factors
        factor_names = [name for name, _ in assessment.bullish_factors]
        assert "squeeze_forced_covering" in factor_names, (
            f"Expected 'squeeze_forced_covering' in bullish_factors, got: {factor_names}"
        )

        # The weight should match config
        squeeze_weight = next(
            w for name, w in assessment.bullish_factors if name == "squeeze_forced_covering"
        )
        assert squeeze_weight == pytest.approx(config.weight_squeeze_faller_reduction, abs=0.001)

    def test_high_short_blocks_shorting_but_partial_credit(self):
        """HIGH_SHORT blocks shorting and adds 0.5× squeeze reduction to bullish_factors.

        Verifies via bullish_factors (not absolute score delta) because scores
        can clamp to [0, 1].
        """
        from src.execution.faller_detection import FallerRiskDetector

        config = self._make_config()
        detector = FallerRiskDetector(config)
        candidate = self._make_candidate()
        scored = self._make_scored()
        indicators = {"vwap": 4.80, "rsi_9": 72.0}

        si_result = ShortInterestResult(
            ticker="ITRM",
            short_float_pct=25.0,
            classification=SqueezeClassification.HIGH_SHORT,
            data_source="yfinance",
        )
        with_high_short = detector.score(
            candidate, scored, indicators, short_interest_result=si_result
        )

        assert with_high_short.shorting_blocked_by_squeeze is True
        assert with_high_short.squeeze_classification == "high_short"

        # high_short_float_caution should appear in bullish_factors at half weight
        factor_names = [name for name, _ in with_high_short.bullish_factors]
        assert "high_short_float_caution" in factor_names, (
            f"Expected 'high_short_float_caution' in bullish_factors, got: {factor_names}"
        )
        caution_weight = next(
            w for name, w in with_high_short.bullish_factors if name == "high_short_float_caution"
        )
        expected_reduction = config.weight_squeeze_faller_reduction * 0.5
        assert caution_weight == pytest.approx(expected_reduction, abs=0.001)


class TestCacheAndBatch:
    """Tests for cache TTL and batch fetch behavior."""

    @pytest.mark.asyncio
    async def test_cache_hit(self, provider):
        """Second call for the same ticker returns cached result without fetching."""
        cached_result = ShortInterestResult(
            ticker="ITRM",
            short_float_pct=35.0,
            data_source="yfinance",
            classification=SqueezeClassification.SQUEEZE,
        )

        call_count = 0

        async def mock_yf_once(ticker):
            nonlocal call_count
            call_count += 1
            return cached_result

        with patch.object(provider, "_fetch_alpaca", new_callable=AsyncMock, return_value=None), \
             patch.object(provider, "_fetch_yfinance", new_callable=AsyncMock, side_effect=mock_yf_once):

            result1 = await provider.get_short_interest("ITRM")
            result2 = await provider.get_short_interest("ITRM")

        assert call_count == 1  # Only fetched once — second was from cache
        assert result1.short_float_pct == result2.short_float_pct
        assert result2.data_source == "yfinance"

    @pytest.mark.asyncio
    async def test_batch_fetch(self, provider):
        """get_batch() returns results for all tickers including failed ones."""
        async def mock_get(ticker):
            if ticker == "ITRM":
                return ShortInterestResult(
                    ticker="ITRM",
                    short_float_pct=35.0,
                    data_source="yfinance",
                    classification=SqueezeClassification.SQUEEZE,
                )
            elif ticker == "AAPL":
                return ShortInterestResult(
                    ticker="AAPL",
                    short_float_pct=2.0,
                    data_source="yfinance",
                    classification=SqueezeClassification.NORMAL,
                )
            else:
                return ShortInterestResult(
                    ticker=ticker,
                    classification=SqueezeClassification.UNKNOWN,
                    data_source="all_tiers_failed",
                    error="All tiers failed",
                )

        with patch.object(provider, "get_short_interest", side_effect=mock_get):
            results = await provider.get_batch(["ITRM", "AAPL", "ZZZT"])

        assert len(results) == 3
        assert results["ITRM"].classification == SqueezeClassification.SQUEEZE
        assert results["AAPL"].classification == SqueezeClassification.NORMAL
        assert results["ZZZT"].classification == SqueezeClassification.UNKNOWN


class TestITRMScenario:
    """End-to-end scenario test: ITRM with 30%+ short float gapping up."""

    @pytest.mark.asyncio
    async def test_itrm_scenario(self, provider):
        """
        ITRM scenario: 30%+ short float + 45% gap → SQUEEZE classification.

        ITRM (Iterion Medical) type scenario: small biotech with high short
        interest gaps up on trial data. Short sellers are forced to cover,
        creating a self-reinforcing squeeze.

        Verifies: full pipeline from yfinance fetch → classify → SQUEEZE.
        """
        yf_info = _make_yfinance_info(short_pct_float=0.38)  # 38% short float

        with patch.object(provider, "_fetch_alpaca", new_callable=AsyncMock, return_value=None), \
             patch("yfinance.Ticker") as mock_ticker_cls:

            mock_ticker = MagicMock()
            mock_ticker_cls.return_value = mock_ticker
            mock_ticker.info = yf_info

            result = await provider.get_short_interest("ITRM")

        assert result.short_float_pct == pytest.approx(38.0, abs=0.1)
        assert result.data_source == "yfinance"

        # Apply classify with 45% gap
        classification = provider.classify(result, gap_pct=0.45)
        assert classification == SqueezeClassification.SQUEEZE

        # Verify the utility properties
        result.classification = classification
        assert result.is_squeeze_candidate is True
        assert result.is_high_short is True


class TestFinvizAntiScrapingHeaders:
    """Verify Finviz requests include required anti-scraping headers."""

    @pytest.mark.asyncio
    async def test_finviz_anti_scraping_headers(self, provider):
        """Finviz request uses browser spoofing headers to avoid 403."""
        # Required headers for anti-scraping
        assert "User-Agent" in FINVIZ_HEADERS
        assert "Mozilla" in FINVIZ_HEADERS["User-Agent"]
        assert "Referer" in FINVIZ_HEADERS
        assert "finviz.com" in FINVIZ_HEADERS["Referer"]
        assert "Accept" in FINVIZ_HEADERS

        # Verify the headers are actually sent in a real request
        html = _make_finviz_html("28.50%")
        captured_headers: dict = {}

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = html

        async def capture_get(url, headers=None, **kwargs):
            if headers:
                captured_headers.update(headers)
            return mock_resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__.return_value = mock_client
            mock_client.get = capture_get

            result = await provider._fetch_finviz("TEST")

        assert result is not None
        assert result.short_float_pct == pytest.approx(28.50, abs=0.01)
        assert "User-Agent" in captured_headers
        assert "Mozilla" in captured_headers["User-Agent"]
        assert "Referer" in captured_headers


class TestFinvizParser:
    """Unit tests for the Finviz HTML parser."""

    def test_parse_finviz_short_float_standard(self):
        """Standard Finviz HTML parses Short Float correctly."""
        from bs4 import BeautifulSoup

        html = _make_finviz_html("32.45%")
        soup = BeautifulSoup(html, "html.parser")
        result = _parse_finviz_short_float(soup, "ITRM")
        assert result == pytest.approx(32.45, abs=0.01)

    def test_parse_finviz_short_float_dash(self):
        """Finviz shows '-' for unavailable data → returns None."""
        from bs4 import BeautifulSoup

        html = _make_finviz_html("-")
        soup = BeautifulSoup(html, "html.parser")
        result = _parse_finviz_short_float(soup, "NEWCO")
        assert result is None

    def test_estimate_age_days(self):
        """_estimate_age_days parses date strings correctly."""
        # Recent date (within last year) should return a small positive number
        from datetime import date

        today = date.today()
        recent = today.strftime("%Y-%m-%d")
        assert _estimate_age_days(recent) == 0

        assert _estimate_age_days("unknown") is None
        assert _estimate_age_days("") is None
        assert _estimate_age_days("not-a-date") is None
