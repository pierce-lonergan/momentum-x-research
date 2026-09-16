"""D279 (2026-05-05) — Lottery prev-day-bar fetch regression guard.

Pin Tuesday 2026-05-05 09:25 ET production failure:

    INFO  pre-fetched prev-day bars: 0/10 tickers

`scripts/lottery_runner.py:AlpacaClient.get_prev_day_bar` previously called
the Alpaca bars endpoint with only ``limit=2`` and no date range. Pre-market
at 09:25 ET, the API responded with today's in-progress bar (volume=0 for
microcaps on the IEX feed). The caller's ``bar_vol > 0`` guard then
rejected every result, falling back to the ``dvol_d0 = price * 1e6`` $1M
stub. The s123 dvol-fix lift never materialized in production.

Three coupled fixes are tested here:

  1. **Bounded ``end`` parameter.** ``get_prev_day_bar`` now passes
     ``end=<yesterday 23:59 UTC>`` so the API always returns a completed
     bar regardless of when called.
  2. **Configurable feed via ``ALPACA_DATA_FEED``.** Default unchanged
     (``iex``); ``lottery_paper_trade.ps1`` sets it to ``sip`` for full
     microcap coverage.
  3. **Empty-bar warnings surface.** Per-ticker ``bars=[]`` responses
     now log a WARNING instead of silently returning None.

NB: this test mocks the HTTP client; we don't depend on Alpaca being
reachable from CI. The probe in `tools/probe_alpaca_bar.py` (manually
re-runnable) covers the live-API contract.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# scripts/ isn't a package; load lottery_runner via the path
REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))


@pytest.fixture
def alpaca_client(monkeypatch):
    """Fresh AlpacaClient with mocked httpx + minimal env."""
    monkeypatch.setenv("ALPACA_API_KEY", "test-key-26char-aaaaaaaaaaaa")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret-44char-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
    monkeypatch.delenv("ALPACA_DATA_FEED", raising=False)
    import importlib
    import lottery_runner
    importlib.reload(lottery_runner)
    client = lottery_runner.AlpacaClient()
    # Replace the httpx client with a MagicMock so we can inspect params
    client.client = MagicMock()
    return client


# ── 1. End parameter is bounded to yesterday ─────────────────────────


@pytest.mark.asyncio
async def test_get_prev_day_bar_passes_end_param(alpaca_client):
    """Pin the fix: API call MUST include `end` and `start` params so the
    response always contains a completed prior-day bar."""
    fake_bar = {
        "c": 5.5, "v": 1_000_000, "vw": 5.45,
        "t": "2026-05-04T04:00:00Z",
    }
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={"bars": [fake_bar]})
    alpaca_client.client.get = AsyncMock(return_value=response)

    bar = await alpaca_client.get_prev_day_bar("MRAM")

    assert bar == fake_bar
    # Verify the params dict that was passed
    call = alpaca_client.client.get.await_args
    assert call is not None, "client.get was never awaited"
    params = call.kwargs["params"]
    assert "end" in params, "FIX REGRESSION: `end` param missing — API will return today's partial bar"
    assert "start" in params, "FIX REGRESSION: `start` param missing"
    assert params["end"].endswith("Z"), "end must be ISO-8601 UTC"
    assert params["timeframe"] == "1Day"


@pytest.mark.asyncio
async def test_get_prev_day_bar_uses_configured_feed(alpaca_client, monkeypatch):
    """ALPACA_DATA_FEED env var routes to the right feed (sip vs iex).
    Production launcher sets sip; tests + CI default to iex."""
    monkeypatch.setenv("ALPACA_DATA_FEED", "sip")
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={"bars": [{"c": 1.0, "v": 100, "vw": 1.0, "t": "x"}]})
    alpaca_client.client.get = AsyncMock(return_value=response)

    await alpaca_client.get_prev_day_bar("SKK")

    params = alpaca_client.client.get.await_args.kwargs["params"]
    assert params["feed"] == "sip", (
        "ALPACA_DATA_FEED env should override default — production launcher "
        "now sets this to sip after the 2026-05-05 IEX-microcap-coverage outage"
    )


@pytest.mark.asyncio
async def test_get_prev_day_bar_default_feed_is_iex(alpaca_client, monkeypatch):
    """Default unchanged: IEX is still the fallback when env is unset.
    The launcher (lottery_paper_trade.ps1) overrides to sip — tests don't."""
    monkeypatch.delenv("ALPACA_DATA_FEED", raising=False)
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={"bars": [{"c": 1.0, "v": 100, "vw": 1.0, "t": "x"}]})
    alpaca_client.client.get = AsyncMock(return_value=response)

    await alpaca_client.get_prev_day_bar("SKK")

    params = alpaca_client.client.get.await_args.kwargs["params"]
    assert params["feed"] == "iex"


# ── 2. Empty bars no longer fail silently ────────────────────────────


@pytest.mark.asyncio
async def test_get_prev_day_bar_empty_response_logs_warning(alpaca_client, caplog):
    """When the API returns 200 OK with bars=[], we MUST emit a WARNING
    and return None — no more silent fallback to the $1M stub."""
    import logging
    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={"bars": []})
    alpaca_client.client.get = AsyncMock(return_value=response)

    with caplog.at_level(logging.WARNING):
        bar = await alpaca_client.get_prev_day_bar("DELISTED")

    assert bar is None
    assert any(
        "DELISTED" in r.message and "0 bars" in r.message
        for r in caplog.records
    ), f"Expected WARNING with 'DELISTED' and '0 bars', got: {[r.message for r in caplog.records]}"


@pytest.mark.asyncio
async def test_get_prev_day_bar_http_exception_returns_none(alpaca_client):
    """Exception path: the silent-handler that exists for HTTP errors must
    NOT change behavior. Returns None, logs at WARNING."""
    alpaca_client.client.get = AsyncMock(side_effect=Exception("HTTP 500"))
    bar = await alpaca_client.get_prev_day_bar("BROKEN")
    assert bar is None


# ── 3. Pin the production scenario as integration ────────────────────


@pytest.mark.asyncio
async def test_pre_market_call_does_not_return_today_partial_bar(alpaca_client):
    """The literal 09:25 ET production scenario: API returns today's
    partial bar with v=0. Our fix bounds `end` to yesterday so the API
    cannot return today's bar. We test the request is correctly bounded."""
    from datetime import datetime, timezone, timedelta

    response = MagicMock()
    response.raise_for_status = MagicMock()
    response.json = MagicMock(return_value={"bars": [
        {"c": 5.0, "v": 1_000_000, "vw": 5.0, "t": "2026-05-04T04:00:00Z"},
    ]})
    alpaca_client.client.get = AsyncMock(return_value=response)

    bar = await alpaca_client.get_prev_day_bar("CNSP")
    assert bar is not None

    params = alpaca_client.client.get.await_args.kwargs["params"]
    end_iso = params["end"]
    end_dt = datetime.strptime(end_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    now_utc = datetime.now(timezone.utc)
    # `end` must be capped at yesterday-23:59 UTC. Use start-of-today UTC as
    # the upper bound — robust to test execution time crossing midnight UTC
    # while still proving the request can't return today's in-progress bar.
    today_start_utc = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    assert end_dt < today_start_utc, (
        f"end={end_iso} should be at most yesterday-23:59 UTC "
        f"(today_start_utc={today_start_utc.isoformat()}); "
        f"otherwise the API may return today's in-progress bar"
    )
