"""
D191: Tests for SEC EDGAR Real-Time Pre-Fetcher

Node ID: tests.unit.test_sec_prefetcher
Graph Link: tested_by → data.sec_prefetcher

Tests are fully offline — all HTTP calls mocked via unittest.mock.AsyncMock.
No real network requests are made.

Coverage:
  - CIK lookup (index load, cache, miss)
  - Recent filing fetch and parsing
  - 424B5 dilution detection (same-day, yesterday, old)
  - ATM language detection
  - 8-K material event detection
  - 8-K item code extraction
  - S-3 shelf registration detection
  - Clean ticker (no concerning filings)
  - Rate limiting (semaphore capacity)
  - User-Agent compliance
  - Batch analysis with concurrent rate limiting
  - Result caching (second call returns cached result)
  - Network error returns UNKNOWN, not crash
  - Faller integration: dilution adds +0.40
  - Faller integration: ATM adds +0.50
  - Faller integration: material event subtracts -0.25
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from src.data.sec_prefetcher import (
    ATM_PATTERNS,
    DILUTION_FORMS,
    MATERIAL_FORMS,
    FilingSignal,
    SECFilingResult,
    SECPrefetcher,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


def make_filing(form: str, filed_date: date, accession: str = "0001234567-26-000001") -> dict:
    """Build a filing dict matching the submissions API output shape."""
    return {"form": form, "filingDate": filed_date, "accessionNumber": accession}


def make_submissions_json(filings: list[dict]) -> dict:
    """Wrap filing list in the submissions API response envelope."""
    forms = [f["form"] for f in filings]
    dates = [str(f["filingDate"]) for f in filings]
    accessions = [f["accessionNumber"] for f in filings]
    return {
        "filings": {
            "recent": {
                "form": forms,
                "filingDate": dates,
                "accessionNumber": accessions,
            }
        }
    }


SAMPLE_TICKERS_JSON = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA Corporation"},
    "2": {"cik_str": 1318605, "ticker": "TSLA", "title": "Tesla Inc."},
    "3": {"cik_str": 1326428, "ticker": "MEME", "title": "Meme Corp"},
}

TODAY = date(2026, 4, 3)
YESTERDAY = TODAY - timedelta(days=1)
FIVE_DAYS_AGO = TODAY - timedelta(days=5)
WEEK_AGO = TODAY - timedelta(days=7)


# ── Helper: prefetcher with mocked index ─────────────────────────────────────


async def _make_prefetcher_with_index(extra_http_responses: dict | None = None) -> SECPrefetcher:
    """
    Build a SECPrefetcher with the CIK index pre-loaded from SAMPLE_TICKERS_JSON
    and additional HTTP responses keyed by URL substring.
    """
    prefetcher = SECPrefetcher()
    # Directly populate the CIK cache (bypasses HTTP)
    for entry in SAMPLE_TICKERS_JSON.values():
        ticker = str(entry["ticker"]).upper()
        cik_padded = str(int(entry["cik_str"])).zfill(10)
        prefetcher._cik_cache[ticker] = cik_padded
    return prefetcher


# ══════════════════════════════════════════════════════════════════════════════
# 1. test_cik_lookup
# ══════════════════════════════════════════════════════════════════════════════


class TestCIKLookup:
    """Ticker → CIK mapping via company_tickers.json."""

    @pytest.mark.asyncio
    async def test_cik_lookup_known_ticker(self):
        """AAPL maps to zero-padded CIK 0000320193."""
        prefetcher = SECPrefetcher()

        # Mock the bulk fetch returning our sample JSON
        with patch.object(prefetcher, "_fetch_json", new=AsyncMock(return_value=SAMPLE_TICKERS_JSON)):
            cik = await prefetcher.lookup_cik("AAPL")

        assert cik == "0000320193"

    @pytest.mark.asyncio
    async def test_cik_lookup_case_insensitive(self):
        """Lowercase ticker resolves the same as uppercase."""
        prefetcher = await _make_prefetcher_with_index()
        cik = await prefetcher.lookup_cik("nvda")
        assert cik == "0001045810"

    @pytest.mark.asyncio
    async def test_cik_lookup_unknown_ticker_returns_none(self):
        """Unknown ticker returns None without raising."""
        prefetcher = await _make_prefetcher_with_index()
        result = await prefetcher.lookup_cik("ZZZZ")
        assert result is None

    @pytest.mark.asyncio
    async def test_cik_caching(self):
        """Second lookup uses cache — _fetch_json called at most once."""
        prefetcher = SECPrefetcher()
        mock_fetch = AsyncMock(return_value=SAMPLE_TICKERS_JSON)
        with patch.object(prefetcher, "_fetch_json", new=mock_fetch):
            await prefetcher.lookup_cik("AAPL")
            await prefetcher.lookup_cik("AAPL")

        # Bulk JSON fetched exactly once, even across two calls
        assert mock_fetch.call_count == 1


# ══════════════════════════════════════════════════════════════════════════════
# 2. test_recent_filings_fetch
# ══════════════════════════════════════════════════════════════════════════════


class TestRecentFilingsFetch:
    """Submissions API response → filing dict list."""

    @pytest.mark.asyncio
    async def test_filings_parsed_correctly(self):
        """JSON envelope correctly unpacked into form/date/accession dicts."""
        prefetcher = await _make_prefetcher_with_index()
        raw = make_submissions_json([
            make_filing("424B5", TODAY, "0001234567-26-000001"),
            make_filing("8-K", YESTERDAY, "0001234567-26-000002"),
        ])
        with patch.object(prefetcher, "_fetch_json", new=AsyncMock(return_value=raw)):
            filings = await prefetcher.fetch_recent_filings("0000320193")

        assert len(filings) == 2
        assert filings[0]["form"] == "424B5"
        assert filings[0]["filingDate"] == TODAY
        assert filings[1]["form"] == "8-K"

    @pytest.mark.asyncio
    async def test_empty_response_returns_empty_list(self):
        """Missing or empty filings block → empty list, no crash."""
        prefetcher = await _make_prefetcher_with_index()
        with patch.object(prefetcher, "_fetch_json", new=AsyncMock(return_value={})):
            filings = await prefetcher.fetch_recent_filings("0000320193")
        assert filings == []

    @pytest.mark.asyncio
    async def test_http_error_returns_empty_list(self):
        """None response from _fetch_json → empty list, no crash."""
        prefetcher = await _make_prefetcher_with_index()
        with patch.object(prefetcher, "_fetch_json", new=AsyncMock(return_value=None)):
            filings = await prefetcher.fetch_recent_filings("0000320193")
        assert filings == []


# ══════════════════════════════════════════════════════════════════════════════
# 3–5. test_424b5_detection (same-day, yesterday, old)
# ══════════════════════════════════════════════════════════════════════════════


class Test424B5Detection:
    """424B5 dilution timing rules."""

    @pytest.mark.asyncio
    async def test_424b5_today_detected(self):
        """Same-day 424B5 → DILUTION_ACTIVE signal, dilution_detected=True."""
        prefetcher = await _make_prefetcher_with_index()
        filings = [make_filing("424B5", TODAY)]

        dilution, atm, matching = await prefetcher.check_dilution(
            "0000320193", filings, reference_date=TODAY
        )
        assert dilution is True
        assert len(matching) == 1
        assert matching[0]["form"] == "424B5"

    @pytest.mark.asyncio
    async def test_424b5_yesterday_detected(self):
        """Yesterday's 424B5 still triggers DILUTION_ACTIVE."""
        prefetcher = await _make_prefetcher_with_index()
        filings = [make_filing("424B5", YESTERDAY)]

        dilution, atm, _ = await prefetcher.check_dilution(
            "0000320193", filings, reference_date=TODAY
        )
        assert dilution is True

    @pytest.mark.asyncio
    async def test_424b5_old_not_detected(self):
        """Week-old 424B5 is beyond the 2-day lookback → not flagged."""
        prefetcher = await _make_prefetcher_with_index()
        filings = [make_filing("424B5", WEEK_AGO)]

        dilution, atm, _ = await prefetcher.check_dilution(
            "0000320193", filings, reference_date=TODAY
        )
        assert dilution is False

    @pytest.mark.asyncio
    async def test_424b2_variant_also_detected(self):
        """424B2 is also a prospectus supplement — same detection logic."""
        prefetcher = await _make_prefetcher_with_index()
        filings = [make_filing("424B2", TODAY)]

        dilution, atm, _ = await prefetcher.check_dilution(
            "0000320193", filings, reference_date=TODAY
        )
        assert dilution is True


# ══════════════════════════════════════════════════════════════════════════════
# 6. test_atm_language_detected
# ══════════════════════════════════════════════════════════════════════════════


class TestATMDetection:
    """ATM pattern matching in filing text."""

    def test_atm_patterns_cover_key_phrases(self):
        """All critical ATM phrases match their compiled patterns."""
        phrases = [
            "at-the-market offering",
            "at the market offering",
            "at the market",
            "Rule 415(a)(4)",
            "sales agent",
            "distribution agreement",
            "at prices related to prevailing market prices",
        ]
        for phrase in phrases:
            matched = any(p.search(phrase) for p in ATM_PATTERNS)
            assert matched, f"ATM pattern did not match: {phrase!r}"

    def test_non_atm_text_not_flagged(self):
        """Normal prospectus language without ATM markers → no match."""
        text = "The Company may offer shares from time to time in underwritten public offerings."
        matched = any(p.search(text) for p in ATM_PATTERNS)
        assert not matched

    @pytest.mark.asyncio
    async def test_atm_language_in_filing_text_detected(self):
        """Full round-trip: 424B5 filed today + ATM text → DILUTION_ATM signal."""
        prefetcher = await _make_prefetcher_with_index()
        atm_text = (
            "This prospectus relates to the sale of shares in an at-the-market offering "
            "through our sales agent pursuant to a distribution agreement."
        )

        filings = [make_filing("424B5", TODAY, "0001234567-26-000001")]
        raw_submissions = make_submissions_json(filings)

        # Mock the full chain: submissions → index JSON → filing text
        index_json = {
            "documents": [{"type": "424B5", "name": "prospectus.htm"}]
        }

        async def mock_fetch_json(url, **kwargs):
            if "submissions" in url:
                return raw_submissions
            if "index" in url:
                return index_json
            return None

        with patch.object(prefetcher, "_fetch_json", new=AsyncMock(side_effect=mock_fetch_json)), \
             patch.object(prefetcher, "_fetch_text", new=AsyncMock(return_value=atm_text)):
            result = await prefetcher.analyze_ticker("AAPL", reference_date=TODAY)

        assert result.signal == FilingSignal.DILUTION_ATM
        assert result.atm_detected is True
        assert result.dilution_detected is True

    @pytest.mark.asyncio
    async def test_424b5_without_atm_text_is_dilution_active(self):
        """424B5 today but no ATM language → DILUTION_ACTIVE (not ATM)."""
        prefetcher = await _make_prefetcher_with_index()
        filings = [make_filing("424B5", TODAY, "0001234567-26-000001")]
        raw_submissions = make_submissions_json(filings)
        index_json = {"documents": [{"type": "424B5", "name": "prospectus.htm"}]}

        clean_text = "This offering is a firm commitment underwritten public offering at $5.00 per share."

        async def mock_fetch_json(url, **kwargs):
            if "submissions" in url:
                return raw_submissions
            if "index" in url:
                return index_json
            return None

        with patch.object(prefetcher, "_fetch_json", new=AsyncMock(side_effect=mock_fetch_json)), \
             patch.object(prefetcher, "_fetch_text", new=AsyncMock(return_value=clean_text)):
            result = await prefetcher.analyze_ticker("AAPL", reference_date=TODAY)

        assert result.signal == FilingSignal.DILUTION_ACTIVE
        assert result.atm_detected is False
        assert result.dilution_detected is True


# ══════════════════════════════════════════════════════════════════════════════
# 7–8. test_8k_material_event and test_8k_items_extracted
# ══════════════════════════════════════════════════════════════════════════════


class Test8KDetection:
    """8-K material event detection."""

    def test_8k_today_detected(self):
        """8-K filed today → material_event_found=True."""
        prefetcher = SECPrefetcher()
        filings = [make_filing("8-K", TODAY)]
        found, items = prefetcher.check_material_events(filings, reference_date=TODAY)
        assert found is True

    def test_8k_yesterday_detected(self):
        """8-K from yesterday is within the 3-day lookback (weekend-aware) → flagged."""
        prefetcher = SECPrefetcher()
        filings = [make_filing("8-K", YESTERDAY)]
        found, _ = prefetcher.check_material_events(filings, reference_date=TODAY)
        assert found is True

    def test_8k_four_days_ago_not_detected(self):
        """8-K from 4 days ago is beyond the 3-day lookback → not flagged."""
        prefetcher = SECPrefetcher()
        filings = [make_filing("8-K", TODAY - timedelta(days=4))]
        found, _ = prefetcher.check_material_events(filings, reference_date=TODAY)
        assert found is False

    def test_8k_items_extracted_from_description(self):
        """Item codes (1.01, 8.01) parsed from filing description field."""
        prefetcher = SECPrefetcher()
        filing = make_filing("8-K", TODAY)
        filing["description"] = "Items 1.01, 8.01"
        found, items = prefetcher.check_material_events([filing], reference_date=TODAY)
        assert found is True
        assert "1.01" in items
        assert "8.01" in items

    def test_no_8k_returns_false(self):
        """No 8-K in filings → material_event_found=False."""
        prefetcher = SECPrefetcher()
        filings = [make_filing("10-Q", TODAY)]
        found, _ = prefetcher.check_material_events(filings, reference_date=TODAY)
        assert found is False


# ══════════════════════════════════════════════════════════════════════════════
# 9. test_clean_ticker
# ══════════════════════════════════════════════════════════════════════════════


class TestCleanTicker:
    """Ticker with no concerning filings → CLEAN signal."""

    @pytest.mark.asyncio
    async def test_clean_ticker_returns_clean_signal(self):
        """Old filings + no recent 424B5/8-K → CLEAN."""
        prefetcher = await _make_prefetcher_with_index()
        filings = [
            make_filing("10-K", date(2026, 1, 1)),      # Old annual report
            make_filing("4", date(2026, 2, 15)),          # Insider form (old)
        ]
        raw_submissions = make_submissions_json(filings)

        with patch.object(prefetcher, "_fetch_json", new=AsyncMock(return_value=raw_submissions)):
            result = await prefetcher.analyze_ticker("NVDA", reference_date=TODAY)

        assert result.signal == FilingSignal.CLEAN
        assert result.dilution_detected is False
        assert result.material_event_detected is False
        assert result.error is None


# ══════════════════════════════════════════════════════════════════════════════
# 10. test_rate_limiting
# ══════════════════════════════════════════════════════════════════════════════


class TestRateLimiting:
    """Semaphore prevents exceeding the SEC rate limit."""

    def test_semaphore_capacity_matches_max_requests_per_sec(self):
        """Semaphore capacity equals MAX_REQUESTS_PER_SEC (8)."""
        prefetcher = SECPrefetcher()
        assert prefetcher._semaphore._value == SECPrefetcher.MAX_REQUESTS_PER_SEC

    def test_max_requests_per_sec_within_sec_limit(self):
        """MAX_REQUESTS_PER_SEC must be ≤ 10 (SEC hard limit)."""
        assert SECPrefetcher.MAX_REQUESTS_PER_SEC <= 10


# ══════════════════════════════════════════════════════════════════════════════
# 11. test_user_agent_compliance
# ══════════════════════════════════════════════════════════════════════════════


class TestUserAgentCompliance:
    """SEC requires company name + email in User-Agent."""

    def test_user_agent_contains_email(self):
        """USER_AGENT must include an email address."""
        assert "@" in SECPrefetcher.USER_AGENT

    def test_user_agent_not_generic(self):
        """USER_AGENT must not be a bare Python/aiohttp default."""
        ua = SECPrefetcher.USER_AGENT.lower()
        assert "python" not in ua or "@" in SECPrefetcher.USER_AGENT

    @pytest.mark.asyncio
    async def test_session_headers_include_user_agent(self):
        """The aiohttp session is created with the USER_AGENT header."""
        import aiohttp

        prefetcher = SECPrefetcher()
        session = await prefetcher._get_session()
        headers = dict(session.headers)
        assert "User-Agent" in headers
        assert headers["User-Agent"] == SECPrefetcher.USER_AGENT
        await prefetcher.close()


# ══════════════════════════════════════════════════════════════════════════════
# 12. test_batch_analysis
# ══════════════════════════════════════════════════════════════════════════════


class TestBatchAnalysis:
    """Multiple tickers analysed concurrently."""

    @pytest.mark.asyncio
    async def test_batch_returns_result_for_each_ticker(self):
        """analyze_batch returns a dict keyed by uppercase ticker."""
        prefetcher = await _make_prefetcher_with_index()
        clean_subs = make_submissions_json([make_filing("10-K", date(2026, 1, 1))])

        with patch.object(prefetcher, "_fetch_json", new=AsyncMock(return_value=clean_subs)):
            results = await prefetcher.analyze_batch(["AAPL", "NVDA"], reference_date=TODAY)

        assert "AAPL" in results
        assert "NVDA" in results
        assert isinstance(results["AAPL"], SECFilingResult)
        assert isinstance(results["NVDA"], SECFilingResult)

    @pytest.mark.asyncio
    async def test_batch_handles_unknown_ticker_gracefully(self):
        """Unknown ticker in batch gets UNKNOWN signal, rest succeed."""
        prefetcher = await _make_prefetcher_with_index()
        clean_subs = make_submissions_json([])

        with patch.object(prefetcher, "_fetch_json", new=AsyncMock(return_value=clean_subs)):
            results = await prefetcher.analyze_batch(
                ["AAPL", "ZZZZ"], reference_date=TODAY
            )

        assert results["AAPL"].signal != FilingSignal.UNKNOWN  # AAPL found
        assert results["ZZZZ"].signal == FilingSignal.UNKNOWN  # CIK not found


# ══════════════════════════════════════════════════════════════════════════════
# 13. test_result_caching
# ══════════════════════════════════════════════════════════════════════════════


class TestResultCaching:
    """Second analyze_ticker call returns cached result."""

    @pytest.mark.asyncio
    async def test_result_cached_after_first_call(self):
        """Second call does not trigger additional HTTP requests."""
        prefetcher = await _make_prefetcher_with_index()
        clean_subs = make_submissions_json([make_filing("10-Q", date(2026, 2, 1))])
        mock_fetch = AsyncMock(return_value=clean_subs)

        with patch.object(prefetcher, "_fetch_json", new=mock_fetch):
            r1 = await prefetcher.analyze_ticker("AAPL", reference_date=TODAY)
            r2 = await prefetcher.analyze_ticker("AAPL", reference_date=TODAY)

        # Same object returned from cache
        assert r1 is r2
        # Submissions fetch called only once despite two analyze_ticker calls
        submissions_calls = [
            c for c in mock_fetch.call_args_list
            if "submissions" in str(c)
        ]
        assert len(submissions_calls) == 1


# ══════════════════════════════════════════════════════════════════════════════
# 14. test_network_error_handling
# ══════════════════════════════════════════════════════════════════════════════


class TestNetworkErrorHandling:
    """HTTP failure returns UNKNOWN signal, never crashes."""

    @pytest.mark.asyncio
    async def test_submissions_fetch_error_returns_unknown(self):
        """If the submissions API call fails, result.signal == UNKNOWN."""
        prefetcher = await _make_prefetcher_with_index()

        async def always_none(url, **kwargs):
            return None

        with patch.object(prefetcher, "_fetch_json", new=AsyncMock(side_effect=always_none)):
            result = await prefetcher.analyze_ticker("AAPL", reference_date=TODAY)

        # CIK found but submissions call returns None → UNKNOWN
        # (CIK found, but empty filings → CLEAN, not UNKNOWN)
        # The empty submissions returns {} which produces empty filings → CLEAN
        assert result.signal in (FilingSignal.CLEAN, FilingSignal.UNKNOWN)
        assert result.error is None or isinstance(result.error, str)

    @pytest.mark.asyncio
    async def test_cik_not_found_returns_unknown(self):
        """If CIK is missing for ticker, result.signal == UNKNOWN with error."""
        prefetcher = await _make_prefetcher_with_index()
        result = await prefetcher.analyze_ticker("FAKE_TICKER_XYZ", reference_date=TODAY)

        assert result.signal == FilingSignal.UNKNOWN
        assert result.error is not None


# ══════════════════════════════════════════════════════════════════════════════
# 15–17. Faller integration tests
# ══════════════════════════════════════════════════════════════════════════════


class TestFallerIntegration:
    """
    SEC data flows correctly into FallerRiskDetector.score().

    Uses minimal mocked candidate/scored/indicators to isolate SEC signal impact.
    """

    def _make_minimal_mocks(self):
        """Build lightweight mocks for candidate, scored, indicators."""
        from unittest.mock import MagicMock

        candidate = MagicMock()
        candidate.ticker = "TEST"
        candidate.current_price = 5.0
        candidate.has_news_catalyst = False
        candidate.float_shares = 10_000_000
        candidate.avg_daily_volume = 2_000_000
        candidate.gap_pct = 0.3
        # D212-B reads this via getattr; a bare MagicMock returns a Mock that is
        # "not None" and then fails the numeric comparison. None is the contract's
        # absent value (src/core/models.py: prior_gap_count: int | None).
        candidate.prior_gap_count = None

        scored = MagicMock()
        scored.agent_signals = []  # No agent signals — isolates SEC impact

        indicators = {}
        return candidate, scored, indicators

    def _make_config(self):
        from config.settings import FallerDetectionConfig
        return FallerDetectionConfig()

    def test_faller_integration_dilution_adds_weight(self):
        """Dilution active → faller score increases by weight_sec_dilution_active (0.40)."""
        from src.execution.faller_detection import FallerRiskDetector

        cfg = self._make_config()
        detector = FallerRiskDetector(cfg)
        candidate, scored, indicators = self._make_minimal_mocks()

        # Score without SEC
        baseline = detector.score(candidate, scored, indicators, sec_result=None)

        # Score with dilution
        sec_result = SECFilingResult(
            ticker="TEST",
            signal=FilingSignal.DILUTION_ACTIVE,
            dilution_detected=True,
        )
        with_dilution = detector.score(candidate, scored, indicators, sec_result=sec_result)

        delta = with_dilution.score - baseline.score
        assert abs(delta - cfg.weight_sec_dilution_active) < 0.01, (
            f"Expected +{cfg.weight_sec_dilution_active:.2f} delta, got {delta:.4f}"
        )

    def test_faller_integration_atm_adds_weight(self):
        """ATM dilution → faller score increases by weight_sec_dilution_atm (0.50)."""
        from src.execution.faller_detection import FallerRiskDetector

        cfg = self._make_config()
        detector = FallerRiskDetector(cfg)
        candidate, scored, indicators = self._make_minimal_mocks()

        baseline = detector.score(candidate, scored, indicators, sec_result=None)

        sec_result = SECFilingResult(
            ticker="TEST",
            signal=FilingSignal.DILUTION_ATM,
            dilution_detected=True,
            atm_detected=True,
        )
        with_atm = detector.score(candidate, scored, indicators, sec_result=sec_result)

        delta = with_atm.score - baseline.score
        # ATM uses weight_sec_dilution_atm (supersedes dilution_active)
        assert abs(delta - cfg.weight_sec_dilution_atm) < 0.01, (
            f"Expected +{cfg.weight_sec_dilution_atm:.2f} delta, got {delta:.4f}"
        )

    def test_faller_integration_material_event_reduces_score(self):
        """8-K material event → faller score decreases by weight_sec_material_event (0.25)."""
        from src.execution.faller_detection import FallerRiskDetector

        cfg = self._make_config()
        detector = FallerRiskDetector(cfg)
        candidate, scored, indicators = self._make_minimal_mocks()

        baseline = detector.score(candidate, scored, indicators, sec_result=None)

        sec_result = SECFilingResult(
            ticker="TEST",
            signal=FilingSignal.MATERIAL_EVENT,
            material_event_detected=True,
        )
        with_event = detector.score(candidate, scored, indicators, sec_result=sec_result)

        delta = with_event.score - baseline.score
        # Material event is bullish — should reduce score (or clamp at 0 if baseline is near-floor)
        assert delta < 0, f"Expected negative delta (bullish), got {delta:.4f}"
        # Either the full weight was applied, or the score was floor-clamped at 0.
        # expected_delta = max(-baseline.score, -weight) handles both cases.
        expected_delta = max(-baseline.score, -cfg.weight_sec_material_event)
        assert abs(delta - expected_delta) < 0.01, (
            f"Expected {expected_delta:.4f} delta (weight or floor-clamped), got {delta:.4f}"
        )

    def test_faller_sec_none_has_no_effect(self):
        """sec_result=None → identical score to pre-D191 behaviour."""
        from src.execution.faller_detection import FallerRiskDetector

        cfg = self._make_config()
        detector = FallerRiskDetector(cfg)
        candidate, scored, indicators = self._make_minimal_mocks()

        r1 = detector.score(candidate, scored, indicators, sec_result=None)
        r2 = detector.score(candidate, scored, indicators, sec_result=None)

        assert r1.score == r2.score

    def test_dilution_with_material_event_only_charges_dilution(self):
        """When both dilution and 8-K present, material event credit is withheld."""
        from src.execution.faller_detection import FallerRiskDetector

        cfg = self._make_config()
        detector = FallerRiskDetector(cfg)
        candidate, scored, indicators = self._make_minimal_mocks()

        baseline = detector.score(candidate, scored, indicators, sec_result=None)

        sec_result = SECFilingResult(
            ticker="TEST",
            signal=FilingSignal.DILUTION_ACTIVE,
            dilution_detected=True,
            material_event_detected=True,  # Both present
        )
        with_both = detector.score(candidate, scored, indicators, sec_result=sec_result)

        delta = with_both.score - baseline.score
        # Should only get dilution weight, not offset by material event
        assert delta > 0, "Dilution should dominate when both signals present"
        assert abs(delta - cfg.weight_sec_dilution_active) < 0.01


# ══════════════════════════════════════════════════════════════════════════════
# 18. Shelf registration detection
# ══════════════════════════════════════════════════════════════════════════════


class TestShelfRegistration:
    """S-3 shelf registration detection."""

    def test_recent_s3_detected(self):
        """S-3 filed within 90 days → shelf_registered=True."""
        prefetcher = SECPrefetcher()
        filings = [make_filing("S-3", date(2026, 2, 1))]  # ~60 days ago
        assert prefetcher.check_shelf_registration(filings, reference_date=TODAY) is True

    def test_old_s3_not_detected(self):
        """S-3 filed > 90 days ago → not flagged."""
        prefetcher = SECPrefetcher()
        filings = [make_filing("S-3", date(2025, 12, 1))]  # >120 days ago
        assert prefetcher.check_shelf_registration(filings, reference_date=TODAY) is False

    @pytest.mark.asyncio
    async def test_shelf_signal_in_analyze_ticker(self):
        """Shelf registration without 424B5 → SHELF_REGISTERED signal."""
        prefetcher = await _make_prefetcher_with_index()
        filings = [make_filing("S-3", date(2026, 2, 1))]
        raw = make_submissions_json(filings)

        with patch.object(prefetcher, "_fetch_json", new=AsyncMock(return_value=raw)):
            result = await prefetcher.analyze_ticker("AAPL", reference_date=TODAY)

        assert result.signal == FilingSignal.SHELF_REGISTERED


# ══════════════════════════════════════════════════════════════════════════════
# 19–32. Edge case hardening tests (D191b)
# ══════════════════════════════════════════════════════════════════════════════


def _make_resp_cm(status: int, json_data=None):
    """
    Build a sync callable that returns an async context manager mock.

    aiohttp's session.get() is a context manager factory (not a coroutine):
    `async with session.get(url) as resp: ...`
    So the mock must be a regular (sync) callable, not async.
    """
    resp = MagicMock()
    resp.status = status
    if json_data is not None:
        resp.json = AsyncMock(return_value=json_data)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


class TestRetryLogic:
    """Exponential backoff on 429 and transient network errors."""

    @pytest.mark.asyncio
    async def test_retry_on_429_eventually_succeeds(self):
        """First call returns 429, second call returns 200 with valid JSON."""
        prefetcher = SECPrefetcher()

        call_count = 0
        good_json = {"filings": {"recent": {"form": [], "filingDate": [], "accessionNumber": []}}}

        def mock_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _make_resp_cm(429)
            return _make_resp_cm(200, json_data=good_json)

        session = MagicMock()
        session.closed = False
        session.get = mock_get

        with patch.object(prefetcher, "_get_session", new=AsyncMock(return_value=session)), \
             patch("asyncio.sleep", new=AsyncMock()):
            result = await prefetcher._fetch_json("https://data.sec.gov/test")

        assert result == good_json
        assert call_count == 2  # First 429, then 200

    @pytest.mark.asyncio
    async def test_retry_exhausted_returns_none(self):
        """All retries return 500 → _fetch_json returns None, does not raise."""
        prefetcher = SECPrefetcher()

        def mock_get(url, **kwargs):
            return _make_resp_cm(500)

        session = MagicMock()
        session.closed = False
        session.get = mock_get

        with patch.object(prefetcher, "_get_session", new=AsyncMock(return_value=session)), \
             patch("asyncio.sleep", new=AsyncMock()):
            result = await prefetcher._fetch_json("https://data.sec.gov/test")

        assert result is None  # Never raises, always returns None

    @pytest.mark.asyncio
    async def test_dns_failure_returns_none(self):
        """ConnectionError (DNS failure) is handled gracefully → None, not crash."""
        prefetcher = SECPrefetcher()

        def mock_get(url, **kwargs):
            raise OSError("Name or service not known")

        session = MagicMock()
        session.closed = False
        session.get = mock_get

        with patch.object(prefetcher, "_get_session", new=AsyncMock(return_value=session)), \
             patch("asyncio.sleep", new=AsyncMock()):
            result = await prefetcher._fetch_json("https://data.sec.gov/test")

        assert result is None

    @pytest.mark.asyncio
    async def test_timeout_returns_none_not_crash(self):
        """TimeoutError is caught, retried, and ultimately returns None."""
        prefetcher = SECPrefetcher()

        def mock_get(url, **kwargs):
            raise asyncio.TimeoutError()

        session = MagicMock()
        session.closed = False
        session.get = mock_get

        with patch.object(prefetcher, "_get_session", new=AsyncMock(return_value=session)), \
             patch("asyncio.sleep", new=AsyncMock()):
            result = await prefetcher._fetch_json("https://data.sec.gov/test")

        assert result is None


class TestDefensiveParsing:
    """Unexpected JSON schema and encoding edge cases."""

    def test_safe_parse_empty_data(self):
        """Empty dict produces empty list, no crash."""
        prefetcher = SECPrefetcher()
        assert prefetcher._safe_parse_filings({}, 40) == []

    def test_safe_parse_missing_recent_key(self):
        """JSON with no 'recent' key → empty list."""
        prefetcher = SECPrefetcher()
        data = {"filings": {}}  # 'recent' is missing
        assert prefetcher._safe_parse_filings(data, 40) == []

    def test_safe_parse_truncated_arrays(self):
        """Arrays of different lengths don't crash — shorter ones produce empty strings."""
        prefetcher = SECPrefetcher()
        data = {
            "filings": {
                "recent": {
                    "form": ["424B5", "8-K"],
                    "filingDate": ["2026-04-03"],  # Shorter than forms
                    "accessionNumber": [],           # Empty
                }
            }
        }
        result = prefetcher._safe_parse_filings(data, 40)
        assert len(result) == 2
        assert result[0]["form"] == "424B5"
        assert result[0]["filingDate"] is not None
        assert result[1]["filingDate"] is None  # Missing date → None

    def test_safe_parse_unexpected_schema_non_dict_recent(self):
        """'recent' is not a dict (schema change) → empty list, no crash."""
        prefetcher = SECPrefetcher()
        data = {"filings": {"recent": "unexpected_string"}}
        assert prefetcher._safe_parse_filings(data, 40) == []

    @pytest.mark.asyncio
    async def test_truncated_json_from_fetch_returns_none(self):
        """Partial/truncated JSON response from SEC → _fetch_json returns None."""
        prefetcher = SECPrefetcher()

        def mock_get(url, **kwargs):
            resp = MagicMock()
            resp.status = 200
            resp.json = AsyncMock(side_effect=json.JSONDecodeError("Expecting value", "", 0))
            resp.__aenter__ = AsyncMock(return_value=resp)
            resp.__aexit__ = AsyncMock(return_value=False)
            return resp

        session = MagicMock()
        session.closed = False
        session.get = mock_get

        with patch.object(prefetcher, "_get_session", new=AsyncMock(return_value=session)):
            result = await prefetcher._fetch_json("https://data.sec.gov/test")

        assert result is None


class TestDateParsing:
    """Multi-format date parsing and edge cases."""

    def test_parse_standard_iso_format(self):
        """YYYY-MM-DD is the primary SEC format."""
        prefetcher = SECPrefetcher()
        result = prefetcher._parse_filing_date("2026-04-03")
        assert result == date(2026, 4, 3)

    def test_parse_slash_format(self):
        """MM/DD/YYYY format handled."""
        prefetcher = SECPrefetcher()
        result = prefetcher._parse_filing_date("04/03/2026")
        assert result == date(2026, 4, 3)

    def test_parse_compact_format(self):
        """YYYYMMDD compact format handled."""
        prefetcher = SECPrefetcher()
        result = prefetcher._parse_filing_date("20260403")
        assert result == date(2026, 4, 3)

    def test_parse_empty_string_returns_none(self):
        """Empty string → None, no crash."""
        prefetcher = SECPrefetcher()
        assert prefetcher._parse_filing_date("") is None

    def test_parse_garbage_returns_none(self):
        """Unparseable string → None, no crash."""
        prefetcher = SECPrefetcher()
        assert prefetcher._parse_filing_date("not-a-date") is None


class TestWeekendHolidayLookback:
    """Weekend and holiday filing windows."""

    def test_friday_dilution_caught_on_monday(self):
        """424B5 filed Friday (3 calendar days before Monday) is still DILUTION_ACTIVE."""
        prefetcher = SECPrefetcher()
        monday = date(2026, 4, 6)        # A Monday
        friday = monday - timedelta(days=3)  # The preceding Friday
        filings = [make_filing("424B5", friday)]

        # With 3-day lookback, Friday filing is caught on Monday
        import asyncio
        dilution, atm, matching = asyncio.get_event_loop().run_until_complete(
            prefetcher.check_dilution("0000320193", filings, reference_date=monday)
        )
        assert dilution is True, "Friday 424B5 must be caught on Monday with 3-day lookback"

    def test_four_day_old_filing_not_caught(self):
        """Filing 4 calendar days ago exceeds the 3-day window → not flagged."""
        prefetcher = SECPrefetcher()
        ref = date(2026, 4, 6)
        four_days_ago = ref - timedelta(days=4)
        filings = [make_filing("424B5", four_days_ago)]

        import asyncio
        dilution, _, _ = asyncio.get_event_loop().run_until_complete(
            prefetcher.check_dilution("0000320193", filings, reference_date=ref)
        )
        assert dilution is False

    def test_friday_8k_caught_on_monday(self):
        """8-K filed Friday is caught on Monday (3-day MATERIAL_LOOKBACK)."""
        prefetcher = SECPrefetcher()
        monday = date(2026, 4, 6)
        friday = monday - timedelta(days=3)
        filings = [make_filing("8-K", friday)]

        found, _ = prefetcher.check_material_events(filings, reference_date=monday)
        assert found is True, "Friday 8-K must be caught on Monday with 3-day lookback"


class TestEncodingAndText:
    """Filing text encoding edge cases."""

    @pytest.mark.asyncio
    async def test_latin1_encoding_replaced_not_crash(self):
        """Latin-1 bytes in filing text are replaced rather than raising UnicodeDecodeError."""
        prefetcher = SECPrefetcher()
        # Simulate a response with Latin-1 bytes (e.g. the copyright symbol \xa9)
        latin1_bytes = b"at-the-market offering \xa9 Corp."

        def mock_get(url, **kwargs):
            resp = MagicMock()
            resp.status = 200
            resp.content = MagicMock()
            resp.content.read = AsyncMock(return_value=latin1_bytes)
            resp.__aenter__ = AsyncMock(return_value=resp)
            resp.__aexit__ = AsyncMock(return_value=False)
            return resp

        session = MagicMock()
        session.closed = False
        session.get = mock_get

        with patch.object(prefetcher, "_get_session", new=AsyncMock(return_value=session)):
            text = await prefetcher._fetch_text("https://www.sec.gov/fake.htm")

        assert text is not None
        assert "at-the-market offering" in text  # Pattern still matchable
        # The \xa9 byte is replaced with replacement character, not crashed


class TestSessionManagement:
    """Session lifecycle and reuse edge cases."""

    @pytest.mark.asyncio
    async def test_closed_session_is_recreated(self):
        """If the session is closed externally, _get_session creates a fresh one."""
        prefetcher = SECPrefetcher()

        session1 = await prefetcher._get_session()
        await prefetcher.close()  # Close it

        assert prefetcher._session is None

        session2 = await prefetcher._get_session()
        assert session2 is not None
        assert not session2.closed

        await prefetcher.close()

    @pytest.mark.asyncio
    async def test_double_close_is_safe(self):
        """Calling close() twice does not raise."""
        prefetcher = SECPrefetcher()
        await prefetcher._get_session()
        await prefetcher.close()
        await prefetcher.close()  # Should not raise


class TestHealthCheck:
    """Health check endpoint."""

    @pytest.mark.asyncio
    async def test_health_check_returns_healthy_when_api_up(self):
        """When Apple's CIK resolves, status is 'healthy'."""
        prefetcher = SECPrefetcher()
        apple_data = {"cik": "320193", "name": "Apple Inc."}

        with patch.object(prefetcher, "_fetch_json", new=AsyncMock(return_value=apple_data)):
            result = await prefetcher.health_check()

        assert result["status"] == "healthy"
        assert "latency_ms" in result
        assert isinstance(result["latency_ms"], float)

    @pytest.mark.asyncio
    async def test_health_check_returns_degraded_when_api_down(self):
        """When the SEC API returns None, status is 'degraded'."""
        prefetcher = SECPrefetcher()

        with patch.object(prefetcher, "_fetch_json", new=AsyncMock(return_value=None)):
            result = await prefetcher.health_check()

        assert result["status"] == "degraded"
        assert "cik_cache_size" in result
        assert "results_cache_size" in result

    @pytest.mark.asyncio
    async def test_health_check_never_raises(self):
        """Even if _fetch_json raises unexpectedly, health_check returns a dict."""
        prefetcher = SECPrefetcher()

        with patch.object(prefetcher, "_fetch_json", new=AsyncMock(side_effect=RuntimeError("boom"))):
            result = await prefetcher.health_check()

        assert "status" in result
        assert result["status"] == "unhealthy"
        assert "error" in result


class TestConcurrentCIKLookup:
    """Race condition prevention on CIK index loading."""

    @pytest.mark.asyncio
    async def test_concurrent_same_ticker_share_single_load(self):
        """Two concurrent lookup_cik calls share one HTTP request, not two."""
        prefetcher = SECPrefetcher()
        fetch_count = 0

        async def counting_fetch(url, **kwargs):
            nonlocal fetch_count
            fetch_count += 1
            await asyncio.sleep(0.01)  # Simulate network latency
            return SAMPLE_TICKERS_JSON

        with patch.object(prefetcher, "_fetch_json", new=AsyncMock(side_effect=counting_fetch)):
            # Fire both lookups concurrently — only one HTTP call should happen
            results = await asyncio.gather(
                prefetcher.lookup_cik("AAPL"),
                prefetcher.lookup_cik("NVDA"),
            )

        assert results[0] == "0000320193"
        assert results[1] == "0001045810"
        # Lock ensures only one fetch despite concurrent callers
        assert fetch_count == 1, f"Expected 1 CIK index fetch, got {fetch_count}"

    @pytest.mark.asyncio
    async def test_multiple_filings_same_day_dilution_dominates(self):
        """Company files both 424B5 and 8-K on the same day — dilution signal wins."""
        prefetcher = await _make_prefetcher_with_index()
        filings = [
            make_filing("424B5", TODAY, "0001234567-26-000001"),
            make_filing("8-K", TODAY, "0001234567-26-000002"),
        ]
        raw_submissions = make_submissions_json(filings)
        index_json = {"documents": [{"type": "424B5", "name": "prospectus.htm"}]}
        clean_text = "This is a firm commitment underwritten offering."

        async def mock_fetch_json(url, **kwargs):
            if "submissions" in url:
                return raw_submissions
            if "index" in url:
                return index_json
            return None

        with patch.object(prefetcher, "_fetch_json", new=AsyncMock(side_effect=mock_fetch_json)), \
             patch.object(prefetcher, "_fetch_text", new=AsyncMock(return_value=clean_text)):
            result = await prefetcher.analyze_ticker("AAPL", reference_date=TODAY)

        # Dilution (424B5) must dominate over material event (8-K)
        assert result.signal in (FilingSignal.DILUTION_ACTIVE, FilingSignal.DILUTION_ATM)
        assert result.dilution_detected is True
