"""
MOMENTUM-X Tests: SEC EDGAR Client — doc 270 D2 fetch hardening

Node ID: tests.unit.test_sec_client_doc270
Graph Link: tested_by → data.sec_client

Covers the doc 270 D2 ship properties:
(a) 500 → retries twice with exponential backoff, returns [] gracefully,
    failure is negative-cached (second call makes NO HTTP attempt)
(b) 200 → cached in-process AND on-disk (second call / new instance = no HTTP)
(c) timeout → bounded (per-request timeout + total deadline) + returns []
(d) 404 → NO retry
(e) SEC fair-access User-Agent header present on EVERY request (incl. retries)
(+) warning dedupe per session, never-raises guarantee through
    check_dilution_risk, expired cache entries refetch
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import ClassVar

import httpx
import pytest

import src.data.sec_client as sec_client_mod
from src.data.sec_client import FilingType, SECEdgarClient

# Minimal EFTS-shaped payload: one recent S-3 filing.
EFTS_PAYLOAD = {
    "hits": {
        "hits": [
            {
                "_id": "0001234567-26-000123",
                "_source": {
                    "form_type": "S-3",
                    "file_date": "2026-05-20",
                    "display_names": ["Test Corp"],
                    "entity_id": "0001234567",
                    "file_description": "Shelf registration",
                },
            }
        ]
    }
}


class FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class FakeAsyncClient:
    """Stands in for httpx.AsyncClient; replays a scripted behavior list."""

    script: ClassVar[list] = []  # items: FakeResponse (returned) or Exception (raised)
    calls: ClassVar[list] = []  # recorded: {"url", "headers", "timeout"}

    def __init__(self, timeout=None, **kwargs):
        self._timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, headers=None, **kwargs):
        FakeAsyncClient.calls.append(
            {"url": url, "headers": dict(headers or {}), "timeout": self._timeout}
        )
        if not FakeAsyncClient.script:
            raise AssertionError("FakeAsyncClient script exhausted — unexpected extra HTTP call")
        item = FakeAsyncClient.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """Fresh fake transport + clean module-level warn-dedupe set per test."""
    FakeAsyncClient.script = []
    FakeAsyncClient.calls = []
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    sec_client_mod._WARN_SEEN.clear()
    yield
    sec_client_mod._WARN_SEEN.clear()


@pytest.fixture()
def sleeps(monkeypatch):
    """Record (and skip) backoff sleeps so retry tests are instant."""
    recorded: list[float] = []

    async def fake_sleep(delay):
        recorded.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    return recorded


def _client(tmp_path) -> SECEdgarClient:
    return SECEdgarClient(cache_dir=tmp_path / "edgar_cache")


# ── (a) 5xx: bounded retry + negative cache ───────────────────


@pytest.mark.asyncio
async def test_500_retries_twice_with_backoff_then_returns_empty(tmp_path, sleeps):
    client = _client(tmp_path)
    FakeAsyncClient.script = [FakeResponse(500), FakeResponse(500), FakeResponse(500)]

    out = await client.search_filings("AAPL", form_types=["S-3"])

    assert out == []  # graceful empty, no exception
    assert len(FakeAsyncClient.calls) == 3  # initial + exactly 2 retries
    assert sleeps == [0.5, 1.0]  # exponential backoff


@pytest.mark.asyncio
async def test_500_failure_is_negative_cached_no_second_http(tmp_path, sleeps):
    client = _client(tmp_path)
    FakeAsyncClient.script = [FakeResponse(500), FakeResponse(500), FakeResponse(500)]

    out1 = await client.search_filings("AAPL", form_types=["S-3"])
    out2 = await client.search_filings("AAPL", form_types=["S-3"])  # negative cache

    assert out1 == [] and out2 == []
    assert len(FakeAsyncClient.calls) == 3  # second call made NO HTTP attempt

    # Negative cache also persists to disk: a fresh instance skips HTTP too.
    client2 = _client(tmp_path)
    out3 = await client2.search_filings("AAPL", form_types=["S-3"])
    assert out3 == []
    assert len(FakeAsyncClient.calls) == 3


# ── (b) 200: positive cache (memory + disk) ───────────────────


@pytest.mark.asyncio
async def test_200_is_cached_second_call_no_http(tmp_path):
    client = _client(tmp_path)
    FakeAsyncClient.script = [FakeResponse(200, EFTS_PAYLOAD)]

    out1 = await client.search_filings("MOBX", form_types=["S-3", "424B5"])
    out2 = await client.search_filings("MOBX", form_types=["S-3", "424B5"])

    assert len(out1) == 1 and out1[0].filing_type is FilingType.S3
    assert out2 == out1
    assert len(FakeAsyncClient.calls) == 1  # exactly one HTTP round-trip

    # On-disk cache: fresh instance (cold memory) still makes no HTTP call.
    client2 = _client(tmp_path)
    out3 = await client2.search_filings("MOBX", form_types=["S-3", "424B5"])
    assert out3 == out1
    assert len(FakeAsyncClient.calls) == 1

    cache_files = list((tmp_path / "edgar_cache").glob("*.json"))
    assert cache_files, "expected an on-disk cache entry"
    entry = json.loads(cache_files[0].read_text(encoding="utf-8"))
    assert entry["ok"] is True
    assert entry["ttl_s"] == sec_client_mod._CACHE_TTL_OK_S


@pytest.mark.asyncio
async def test_expired_cache_entry_refetches(tmp_path):
    client = _client(tmp_path)

    # Seed an already-expired positive entry for the exact request key.
    url = client._build_search_url("LFS", ["S-3"], 90)
    client._cache_put(url, ok=True, payload=EFTS_PAYLOAD, ttl_s=-1.0)

    FakeAsyncClient.script = [FakeResponse(200, EFTS_PAYLOAD)]
    out = await client.search_filings("LFS", form_types=["S-3"])

    assert len(out) == 1
    assert len(FakeAsyncClient.calls) == 1  # expired entry → real refetch


# ── (c) timeouts: bounded everywhere, returns empty ───────────


@pytest.mark.asyncio
async def test_timeout_is_bounded_retried_and_returns_empty(tmp_path, sleeps):
    client = _client(tmp_path)
    FakeAsyncClient.script = [
        httpx.ReadTimeout("slow"),
        httpx.ReadTimeout("slow"),
        httpx.ReadTimeout("slow"),
    ]

    out = await client.search_filings("RITR")

    assert out == []
    assert len(FakeAsyncClient.calls) == 3  # timeouts are retryable (max 2)
    for call in FakeAsyncClient.calls:
        # Every request carries a finite httpx timeout — nothing unbounded.
        assert call["timeout"] == sec_client_mod._REQUEST_TIMEOUT_S

    # Timeout failures are negative-cached like any other failure.
    await client.search_filings("RITR")
    assert len(FakeAsyncClient.calls) == 3


@pytest.mark.asyncio
async def test_total_deadline_cuts_a_hanging_request(tmp_path, monkeypatch):
    """asyncio.wait_for guarantees the cycle can never block on EDGAR."""
    client = _client(tmp_path)
    monkeypatch.setattr(sec_client_mod, "_TOTAL_DEADLINE_S", 0.25)

    class HangingClient(FakeAsyncClient):
        async def get(self, url, headers=None, **kwargs):
            FakeAsyncClient.calls.append(
                {"url": url, "headers": dict(headers or {}), "timeout": self._timeout}
            )
            await asyncio.Event().wait()  # hangs until cancelled

    monkeypatch.setattr(httpx, "AsyncClient", HangingClient)

    t0 = time.monotonic()
    out = await client.search_filings("HANG")
    elapsed = time.monotonic() - t0

    assert out == []  # graceful, no TimeoutError into the cycle
    assert elapsed < 2.0
    # And the deadline failure is negative-cached: no further HTTP attempts.
    n_calls = len(FakeAsyncClient.calls)
    await client.search_filings("HANG")
    assert len(FakeAsyncClient.calls) == n_calls


# ── (d) 4xx: never retried ────────────────────────────────────


@pytest.mark.asyncio
async def test_404_is_not_retried(tmp_path):
    client = _client(tmp_path)
    FakeAsyncClient.script = [FakeResponse(404)]

    out = await client.search_filings("GXAI")

    assert out == []
    assert len(FakeAsyncClient.calls) == 1  # exactly one attempt, NO retry

    # 4xx failures are negative-cached too (no hammering).
    await client.search_filings("GXAI")
    assert len(FakeAsyncClient.calls) == 1


# ── (e) SEC fair-access User-Agent on every request ───────────


@pytest.mark.asyncio
async def test_user_agent_present_on_every_request_including_retries(tmp_path, sleeps):
    client = _client(tmp_path)
    FakeAsyncClient.script = [
        FakeResponse(500),
        FakeResponse(500),
        FakeResponse(200, EFTS_PAYLOAD),
    ]

    out = await client.search_filings("ASNS")

    assert len(out) == 1  # recovered on the second retry
    assert len(FakeAsyncClient.calls) == 3
    for call in FakeAsyncClient.calls:
        ua = call["headers"].get("User-Agent", "")
        assert ua == client._user_agent
        # SEC fair-access format: company name + contact (space + @).
        assert " " in ua and "@" in ua


# ── never-raises + log hygiene ────────────────────────────────


@pytest.mark.asyncio
async def test_identical_warnings_deduped_per_session(tmp_path, caplog):
    caplog.set_level(logging.DEBUG, logger="src.data.sec_client")

    # Two independent clients (fresh caches) hitting the same failure.
    c1 = SECEdgarClient(cache_dir=tmp_path / "c1")
    FakeAsyncClient.script = [FakeResponse(404)]
    await c1.search_filings("CANF")

    c2 = SECEdgarClient(cache_dir=tmp_path / "c2")
    FakeAsyncClient.script = [FakeResponse(404)]
    await c2.search_filings("CANF")

    warnings = [
        r for r in caplog.records if r.levelno == logging.WARNING and "CANF" in r.getMessage()
    ]
    assert len(warnings) == 1  # ONE warning per identical failure per session


@pytest.mark.asyncio
async def test_check_dilution_risk_never_raises_and_keeps_shape(tmp_path, sleeps):
    client = _client(tmp_path)
    FakeAsyncClient.script = [FakeResponse(500), FakeResponse(500), FakeResponse(500)]

    assessment = await client.check_dilution_risk("PRSO")

    # Return shape preserved: DilutionAssessment with degrade-to-CLEAN
    # semantics (documented in docs/engineering_hygiene/sec_degradation_fix.md).
    assert assessment.risk_level == "CLEAN"
    assert assessment.active_dilution is False
    assert assessment.total_filings_analyzed == 0


@pytest.mark.asyncio
async def test_transport_error_not_retried_and_swallowed(tmp_path):
    client = _client(tmp_path)
    FakeAsyncClient.script = [httpx.ConnectError("boom")]

    out = await client.search_filings("SYNX")

    assert out == []  # never raises into the cycle
    assert len(FakeAsyncClient.calls) == 1  # only 5xx/timeouts retry
