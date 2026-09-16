"""Unit tests for the Polygon REST client + endpoints.

Mocked via httpx.MockTransport (no respx dependency, matches the
codebase convention of unittest.mock + AsyncMock).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

# mx-arena is not on sys.path by default — add it for these tests.
REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "mx-arena"))

from data_providers.polygon import (  # noqa: E402
    PolygonClient,
    PolygonEndpoints,
    PolygonAPIError,
    PolygonRateLimitError,
    Bar,
    Quote,
    Trade,
    TickerRef,
    FinancialReport,
)


# ── Fixtures: synthetic Polygon JSON shapes ──────────────────────


def _aggs_payload(ticker: str, n_bars: int = 3) -> dict:
    return {
        "ticker": ticker,
        "queryCount": n_bars,
        "resultsCount": n_bars,
        "adjusted": True,
        "results": [
            {
                "v": 1000.0 + i,
                "vw": 100.0 + i * 0.1,
                "o": 100.0 + i,
                "c": 101.0 + i,
                "h": 102.0 + i,
                "l": 99.0 + i,
                "t": 1_700_000_000_000 + i * 60_000,
                "n": 50 + i,
            }
            for i in range(n_bars)
        ],
        "status": "OK",
        "request_id": "test",
    }


def _quotes_payload(ticker: str, n_quotes: int = 2, with_next: bool = False) -> dict:
    payload = {
        "results": [
            {
                "ask_exchange": 11,
                "ask_price": 100.05 + i * 0.01,
                "ask_size": 100 + i,
                "bid_exchange": 12,
                "bid_price": 99.95 + i * 0.01,
                "bid_size": 200 + i,
                "conditions": [1],
                "participant_timestamp": 1_700_000_000_000_000_000 + i * 1_000,
                "sip_timestamp": 1_700_000_000_000_000_000 + i * 1_000 + 500,
            }
            for i in range(n_quotes)
        ],
        "status": "OK",
    }
    if with_next:
        payload["next_url"] = "https://api.polygon.io/v3/quotes/AAPL?cursor=abc"
    return payload


def _ticker_details_payload(ticker: str) -> dict:
    return {
        "results": {
            "ticker": ticker,
            "name": f"{ticker} Inc.",
            "market": "stocks",
            "locale": "us",
            "primary_exchange": "XNAS",
            "type": "CS",
            "active": True,
            "currency_name": "usd",
            "cik": "0001234567",
            "composite_figi": "BBG000B9XRY4",
            "share_class_figi": "BBG001S5N8V8",
            "market_cap": 3_000_000_000_000,
            "weighted_shares_outstanding": 15_000_000_000,
        },
        "status": "OK",
    }


def _financials_payload(ticker: str) -> dict:
    return {
        "results": [
            {
                "ticker": ticker,
                "fiscal_period": "Q1",
                "fiscal_year": "2025",
                "start_date": "2025-01-01",
                "end_date": "2025-03-31",
                "timeframe": "quarterly",
                "financials": {
                    "income_statement": {
                        "revenues": {"value": 1_000_000_000},
                        "net_income_loss": {"value": 100_000_000},
                        "operating_income_loss": {"value": 150_000_000},
                        "diluted_earnings_per_share": {"value": 1.25},
                    }
                },
            }
        ],
        "status": "OK",
    }


def _make_client_with_mock(handler, tmp_path):
    transport = httpx.MockTransport(handler)
    client = PolygonClient(api_key="TEST_KEY", cache_dir=tmp_path)
    # Inject a mocked AsyncClient so __aenter__/__aexit__ doesn't matter for
    # behaviour, but we still need to set _client manually.
    client._client = httpx.AsyncClient(transport=transport)
    return client


# ── Client tests ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_aggregates_parses_bars(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v2/aggs/ticker/AAPL/range/1/day/2024-01-01/2024-01-02"
        assert request.url.params.get("apiKey") == "TEST_KEY"
        return httpx.Response(200, json=_aggs_payload("AAPL", n_bars=3))

    client = _make_client_with_mock(handler, tmp_path)
    try:
        ep = PolygonEndpoints(client)
        bars = await ep.aggregates("AAPL", 1, "day", "2024-01-01", "2024-01-02")
        assert len(bars) == 3
        assert all(isinstance(b, Bar) for b in bars)
        assert bars[0].open == 100.0
        assert bars[0].close == 101.0
        assert bars[2].volume == 1002.0
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_get_quotes_parses_and_paginates(tmp_path):
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if call_count["n"] == 1:
            # First page advertises a next_url.
            return httpx.Response(200, json=_quotes_payload("AAPL", n_quotes=2, with_next=True))
        # Second page closes the cursor.
        return httpx.Response(200, json=_quotes_payload("AAPL", n_quotes=1, with_next=False))

    client = _make_client_with_mock(handler, tmp_path)
    try:
        ep = PolygonEndpoints(client)
        quotes = await ep.quotes("AAPL", limit=2)
        assert len(quotes) == 3  # 2 + 1
        assert all(isinstance(q, Quote) for q in quotes)
        assert quotes[0].midpoint > 0
        assert call_count["n"] == 2  # paginated once
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_429_raises_after_retries(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="too many requests")

    client = _make_client_with_mock(handler, tmp_path)
    client._max_retries = 2  # speed up the test
    try:
        ep = PolygonEndpoints(client)
        with pytest.raises(PolygonRateLimitError):
            await ep.aggregates("AAPL", 1, "day", "2024-01-01", "2024-01-02")
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_4xx_other_than_429_raises_immediately(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="not found")

    client = _make_client_with_mock(handler, tmp_path)
    try:
        ep = PolygonEndpoints(client)
        with pytest.raises(PolygonAPIError):
            await ep.aggregates("ZZZZ", 1, "day", "2024-01-01", "2024-01-02")
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_cache_hit_avoids_second_call(tmp_path):
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json=_aggs_payload("AAPL", n_bars=2))

    client = _make_client_with_mock(handler, tmp_path)
    try:
        ep = PolygonEndpoints(client)
        bars1 = await ep.aggregates("AAPL", 1, "day", "2024-01-01", "2024-01-02")
        bars2 = await ep.aggregates("AAPL", 1, "day", "2024-01-01", "2024-01-02")
        assert len(bars1) == len(bars2) == 2
        assert call_count["n"] == 1  # second call was cached
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_cache_key_excludes_apikey(tmp_path):
    """Cache should hit even if the API key is rotated between calls."""
    def handler1(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_aggs_payload("MSFT"))

    client_a = _make_client_with_mock(handler1, tmp_path)
    try:
        ep_a = PolygonEndpoints(client_a)
        await ep_a.aggregates("MSFT", 1, "day", "2024-01-01", "2024-01-02")
    finally:
        await client_a._client.aclose()

    # Build a second client with a DIFFERENT api_key but same cache_dir.
    # Cache should hit, so the second handler must NOT be called.
    second_call = {"hit": False}

    def handler2(request: httpx.Request) -> httpx.Response:
        second_call["hit"] = True
        return httpx.Response(500, text="should not be called")

    client_b = PolygonClient(api_key="DIFFERENT_KEY", cache_dir=tmp_path)
    client_b._client = httpx.AsyncClient(transport=httpx.MockTransport(handler2))
    try:
        ep_b = PolygonEndpoints(client_b)
        bars = await ep_b.aggregates("MSFT", 1, "day", "2024-01-01", "2024-01-02")
        assert len(bars) == 3
        assert second_call["hit"] is False
    finally:
        await client_b._client.aclose()


@pytest.mark.asyncio
async def test_ticker_details_parses_market_cap(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ticker_details_payload("AAPL"))

    client = _make_client_with_mock(handler, tmp_path)
    try:
        ep = PolygonEndpoints(client)
        ref = await ep.ticker_details("AAPL")
        assert ref is not None
        assert ref.ticker == "AAPL"
        assert ref.market_cap == 3_000_000_000_000
        assert ref.cik == "0001234567"
    finally:
        await client._client.aclose()


@pytest.mark.asyncio
async def test_financials_parses_income_statement(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_financials_payload("AAPL"))

    client = _make_client_with_mock(handler, tmp_path)
    try:
        ep = PolygonEndpoints(client)
        reports = await ep.financials("AAPL")
        assert len(reports) == 1
        r = reports[0]
        assert r.revenues == 1_000_000_000
        assert r.net_income_loss == 100_000_000
        assert r.diluted_earnings_per_share == 1.25
        assert r.fiscal_period == "Q1"
    finally:
        await client._client.aclose()


# ── Model tests ──────────────────────────────────────────────────


def test_quote_midpoint_zero_when_one_side_missing():
    q = Quote.from_json("AAPL", {
        "sip_timestamp": 1, "participant_timestamp": 0,
        "ask_price": 100.0, "ask_size": 1, "ask_exchange": 1,
        "bid_price": 0, "bid_size": 0, "bid_exchange": 0,
        "conditions": [],
    })
    assert q.midpoint == 0.0


def test_quote_midpoint_average_when_both_sides_present():
    q = Quote.from_json("AAPL", {
        "sip_timestamp": 1, "participant_timestamp": 0,
        "ask_price": 100.10, "ask_size": 1, "ask_exchange": 1,
        "bid_price": 99.90, "bid_size": 1, "bid_exchange": 1,
        "conditions": [],
    })
    assert q.midpoint == pytest.approx(100.0)


def test_bar_handles_missing_vwap_and_n():
    b = Bar.from_json("X", {"t": 1, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 100})
    assert b.vwap is None
    assert b.trade_count is None


def test_financial_report_handles_missing_income_statement():
    r = FinancialReport.from_json("X", {
        "fiscal_period": "Q1", "fiscal_year": "2025",
        "start_date": "x", "end_date": "y", "timeframe": "quarterly",
        # no `financials` key at all
    })
    assert r.revenues is None
    assert r.net_income_loss is None
