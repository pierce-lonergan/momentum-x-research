"""D279 (2026-05-05) — news_client error log includes exception detail.

Pin Tuesday 2026-05-05 09:35:23 production observation:

    src.data.news_client | ERROR | Alpaca news fetch failed:

— with EMPTY exception detail. Some httpx exceptions (notably
HTTPStatusError, ReadTimeout, ConnectError) render as "" via str() — only
their type/repr carries useful info. Operators couldn't tell what failed.

Post-fix: the log uses repr(e) (always shows class name) and, when the
exception carries a `.response`, appends `status=<code> body=<text[:200]>`.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.data.news_client import NewsClient


@pytest.fixture
def news_client():
    return NewsClient(
        alpaca_api_key="test-key",
        alpaca_secret_key="test-secret",
    )


@pytest.mark.asyncio
async def test_log_includes_exception_class_name(news_client, caplog):
    """Even when str(e) is empty, repr(e) shows the class name. This was
    the literal Tuesday 2026-05-05 outage: log line ended at 'failed: '."""
    class _SilentExc(Exception):
        def __str__(self):
            return ""  # Mimics httpx exceptions whose str() is empty

    mock_http = MagicMock()
    mock_http.is_closed = False
    mock_http.get = AsyncMock(side_effect=_SilentExc())
    news_client._http_client = mock_http

    with caplog.at_level(logging.ERROR, logger="src.data.news_client"):
        result = await news_client._fetch_alpaca_news(
            ticker="MRAM", lookback_hours=1
        )

    assert result == [], "fetch must still return [] on failure"
    error_msgs = [
        r.message for r in caplog.records
        if "Alpaca news fetch failed" in r.message
    ]
    assert len(error_msgs) >= 1, f"expected error log; got: {[r.message for r in caplog.records]}"
    msg = error_msgs[0]
    assert "_SilentExc" in msg, (
        f"FIX REGRESSION: log must show exception class via repr(); got: {msg!r}"
    )
    # Strict regression: the legacy "failed: <empty>" pattern must not recur
    assert not msg.rstrip().endswith("failed:"), (
        f"BUG REGRESSION: empty error suffix is back — {msg!r}"
    )


@pytest.mark.asyncio
async def test_log_includes_http_status_and_body_when_available(news_client, caplog):
    """When the exception carries a .response, log status + body[:200].
    Critical for diagnosing 4xx/5xx without parsing the httpx stack-trace."""
    fake_response = MagicMock()
    fake_response.status_code = 503
    fake_response.text = "Service Unavailable - Alpaca degraded"

    class _StatusErr(Exception):
        def __init__(self):
            self.response = fake_response
        def __str__(self):
            return ""

    mock_http = MagicMock()
    mock_http.is_closed = False
    mock_http.get = AsyncMock(side_effect=_StatusErr())
    news_client._http_client = mock_http

    with caplog.at_level(logging.ERROR, logger="src.data.news_client"):
        await news_client._fetch_alpaca_news(ticker="MRAM", lookback_hours=1)

    error_msgs = [
        r.message for r in caplog.records
        if "Alpaca news fetch failed" in r.message
    ]
    assert len(error_msgs) >= 1
    msg = error_msgs[0]
    assert "status=503" in msg, f"missing status code: {msg!r}"
    assert "Service Unavailable" in msg, f"missing response body: {msg!r}"
    assert "Alpaca degraded" in msg, (
        f"body truncation lost the diagnostic detail: {msg!r}"
    )


@pytest.mark.asyncio
async def test_response_body_capped_at_200_chars(news_client, caplog):
    """Defends against megabyte-scale Alpaca error pages bloating the log."""
    fake_response = MagicMock()
    fake_response.status_code = 502
    fake_response.text = "X" * 5000  # 5KB error page

    class _Err(Exception):
        def __init__(self):
            self.response = fake_response

    mock_http = MagicMock()
    mock_http.is_closed = False
    mock_http.get = AsyncMock(side_effect=_Err())
    news_client._http_client = mock_http

    with caplog.at_level(logging.ERROR, logger="src.data.news_client"):
        await news_client._fetch_alpaca_news(ticker="X", lookback_hours=1)

    msg = next(r.message for r in caplog.records
                  if "Alpaca news fetch failed" in r.message)
    # Should contain SOME Xs but not all 5000
    assert msg.count("X") <= 250, (
        f"body was not truncated: contains {msg.count('X')} X's"
    )
