"""doc 287: preflight check_alpaca must RETRY transient network failures and DEGRADE-START rather than
FATAL-abort the session (7/8 + 7/10 lost to a single 10s timeout). Real problems still block."""
import sys
import types

import httpx
import pytest

import scripts.preflight_check as pf


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {"equity": "190000", "status": "ACTIVE"}

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _fast_and_keyed(monkeypatch):
    # keys present; no real sleeping; no real webhook; env fully controlled (no .env bleed-through)
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    monkeypatch.setattr(pf, "_alert_webhook", lambda *a, **k: None)
    # check_alpaca does `from dotenv import load_dotenv; load_dotenv(..., override=False)`, which would
    # repopulate a deleted key from the real .env — neutralize it so the test owns the environment.
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    import time as _t
    monkeypatch.setattr(_t, "sleep", lambda *_a, **_k: None)


def _seq(monkeypatch, responses):
    """responses: list of _Resp or Exception; consumed one per httpx.get call."""
    calls = {"i": 0}

    def fake_get(*a, **k):
        r = responses[min(calls["i"], len(responses) - 1)]
        calls["i"] += 1
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(httpx, "get", fake_get)
    return calls


def test_three_timeouts_degrade_start_not_abort(monkeypatch):
    """THE fix (fails on old code, which returned False on the first timeout)."""
    _seq(monkeypatch, [httpx.ConnectTimeout("timed out")])
    ok, msg = pf.check_alpaca()
    assert ok is True
    assert "DEGRADED-START" in msg


def test_recovers_on_second_attempt(monkeypatch):
    calls = _seq(monkeypatch, [httpx.ReadTimeout("blip"), _Resp()])
    ok, msg = pf.check_alpaca()
    assert ok is True and "Alpaca OK" in msg and "retry 2" in msg
    assert calls["i"] == 2


def test_auth_failure_blocks(monkeypatch):
    _seq(monkeypatch, [_Resp(status_code=403)])
    ok, msg = pf.check_alpaca()
    assert ok is False and "auth failed" in msg


def test_zero_equity_blocks(monkeypatch):
    _seq(monkeypatch, [_Resp(payload={"equity": "0", "status": "ACTIVE"})])
    ok, msg = pf.check_alpaca()
    assert ok is False and "equity" in msg


def test_inactive_status_blocks(monkeypatch):
    _seq(monkeypatch, [_Resp(payload={"equity": "190000", "status": "ACCOUNT_UPDATED"})])
    ok, msg = pf.check_alpaca()
    assert ok is False and "status" in msg


def test_missing_keys_block(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    ok, msg = pf.check_alpaca()
    assert ok is False and "not set" in msg


def test_healthy_passes_first_try(monkeypatch):
    _seq(monkeypatch, [_Resp()])
    ok, msg = pf.check_alpaca()
    assert ok is True and "Alpaca OK" in msg and "retry" not in msg
