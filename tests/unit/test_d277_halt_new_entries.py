"""D277 HALT_NEW_ENTRIES tests: operator kill switch on the single
chokepoint AlpacaDataClient.submit_oto_order.

Pins the discipline: every entry path (FAST_PATH, Phase 2, Phase 3
RESCAN, VWAP) goes through submit_oto_order. Gating it with a single
kill switch guarantees no new entries can be submitted when the
operator decides to halt — without touching ANY of the entry call
sites. Existing positions, exits, and stop ratchets are unaffected.

Background (2026-04-28):
  - Phase 0 audit + AR + AS shipped today restored recon observability.
  - Phase 1 honest edge statement: no demonstrated edge yet; n=13
    deduped trades, 2 wins both on non-NEUTRAL news_agent signals.
  - Operator decision per evaluation/2026-04-28-catalyst-stratification.md
    §6: halt new entries, ship a catalyst gate, observe ≥3 sessions
    before resuming.

The kill switch reads BOTH the env var and the config field — env
wins (operator override is fastest). Either source set to truthy
halts entries.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def _clear_halt_env(monkeypatch):
    """Ensure no leftover halt env from prior tests / shell."""
    monkeypatch.delenv("MOMENTUM_HALT_NEW_ENTRIES", raising=False)


# ── Behavior: env var halts ──────────────────────────────────────────


@pytest.mark.parametrize("env_value", ["1", "true", "TRUE", "yes", "on"])
@pytest.mark.asyncio
async def test_env_var_truthy_halts_oto_submission(env_value, monkeypatch, _clear_halt_env):
    """MOMENTUM_HALT_NEW_ENTRIES=1 (and other truthy variants) MUST
    cause submit_oto_order to refuse all submissions and return a
    structured 'halted_by_operator' response — without making any
    HTTP call to Alpaca."""
    monkeypatch.setenv("MOMENTUM_HALT_NEW_ENTRIES", env_value)
    from src.data.alpaca_client import AlpacaDataClient
    client = AlpacaDataClient.__new__(AlpacaDataClient)  # bypass __init__
    client._trade_base = "https://paper-api.alpaca.markets"
    # Patch the underlying HTTP call so a regression that bypasses the
    # halt would loudly fail this test (the mock would record a call).
    with patch.object(client, "_trading_post", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = {"id": "should-not-happen"}
        resp = await client.submit_oto_order(
            symbol="LIDR", qty=5264, limit_price=2.42, stop_loss=2.10,
        )
    assert resp["status"] == "halted_by_operator"
    # 2026-05-12 contract change: halt response now uses synthetic
    # "halted-<symbol>-<utc-ts>" id instead of empty string, to satisfy
    # downstream TradeContextRow Pydantic schema (min_length=1) and
    # eliminate D261 PHASE0_SCHEMA_VALIDATION_FAILED warnings.
    # Real Alpaca order_ids are UUIDs without prefixes, so this is
    # unambiguously distinguishable from a real order.
    assert resp["id"].startswith("halted-LIDR-"), (
        f"halt response id should start with 'halted-LIDR-' prefix; got '{resp['id']}'"
    )
    assert resp["halt_reason"] == "MOMENTUM_HALT_NEW_ENTRIES"
    assert resp["symbol"] == "LIDR"
    assert resp["legs"] == []
    mock_req.assert_not_called(), (
        "REGRESSION: halt was set but _trading_post was still invoked. "
        "An HTTP call would have been made to Alpaca."
    )


@pytest.mark.parametrize("env_value", ["", "0", "false", "no", "off"])
@pytest.mark.asyncio
async def test_env_var_falsy_does_NOT_halt(env_value, monkeypatch, _clear_halt_env):
    """Falsy values must NOT halt. Default behavior preserved."""
    if env_value:
        monkeypatch.setenv("MOMENTUM_HALT_NEW_ENTRIES", env_value)
    # Also ensure config halt is False (test shouldn't depend on global state)
    with patch("config.settings.Settings") as MockSettings:
        MockSettings.return_value.execution.halt_new_entries = False
        from src.data.alpaca_client import AlpacaDataClient
        client = AlpacaDataClient.__new__(AlpacaDataClient)
        client._trade_base = "https://paper-api.alpaca.markets"
        with patch.object(client, "_trading_post", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"id": "real-order-id", "status": "accepted", "legs": []}
            resp = await client.submit_oto_order(
                symbol="LIDR", qty=5264, limit_price=2.42, stop_loss=2.10,
            )
    # The real path was taken — _request was invoked
    assert resp["status"] != "halted_by_operator"
    mock_req.assert_called_once()


# ── Behavior: config field halts even without env var ────────────────


@pytest.mark.asyncio
async def test_config_field_halts_when_env_unset(monkeypatch, _clear_halt_env):
    """settings.execution.halt_new_entries=True must also halt, even
    when the env var is unset. This lets ops set the kill switch via
    config persistence (survives across process restarts) instead of
    every-shell env exports."""
    with patch("config.settings.Settings") as MockSettings:
        MockSettings.return_value.execution.halt_new_entries = True
        from src.data.alpaca_client import AlpacaDataClient
        client = AlpacaDataClient.__new__(AlpacaDataClient)
        client._trade_base = "https://paper-api.alpaca.markets"
        with patch.object(client, "_trading_post", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"id": "should-not-happen"}
            resp = await client.submit_oto_order(
                symbol="LIDR", qty=5264, limit_price=2.42, stop_loss=2.10,
            )
    assert resp["status"] == "halted_by_operator"
    mock_req.assert_not_called()


@pytest.mark.asyncio
async def test_settings_load_failure_does_not_block_entries(monkeypatch, _clear_halt_env):
    """DEFENSIVE: if config.settings raises (corrupt .env, missing
    secrets), the halt check must NOT block exit/order paths. Default
    posture under settings failure: NOT halted (better to allow normal
    operation than to silently freeze the system on a config bug)."""
    with patch("config.settings.Settings", side_effect=RuntimeError("corrupt env")):
        from src.data.alpaca_client import AlpacaDataClient
        client = AlpacaDataClient.__new__(AlpacaDataClient)
        client._trade_base = "https://paper-api.alpaca.markets"
        with patch.object(client, "_trading_post", new_callable=AsyncMock) as mock_req:
            mock_req.return_value = {"id": "real-order", "status": "accepted", "legs": []}
            resp = await client.submit_oto_order(
                symbol="LIDR", qty=5264, limit_price=2.42, stop_loss=2.10,
            )
    # Settings failure → halt defaults to False → real path taken
    assert resp.get("status") != "halted_by_operator"
    assert resp.get("halt_reason") is None
    mock_req.assert_called_once()


# ── Short-side parity: submit_oto_short_order also halts ─────────────


@pytest.mark.asyncio
async def test_short_oto_also_halts_under_env_var(monkeypatch, _clear_halt_env):
    """submit_oto_short_order is the SECOND entry chokepoint (D161 short
    selling). It MUST also respect the halt — otherwise the kill switch
    silently allows shorts to slip through while blocking longs."""
    monkeypatch.setenv("MOMENTUM_HALT_NEW_ENTRIES", "1")
    from src.data.alpaca_client import AlpacaDataClient
    client = AlpacaDataClient.__new__(AlpacaDataClient)
    client._trade_base = "https://paper-api.alpaca.markets"
    with patch.object(client, "_trading_post", new_callable=AsyncMock) as mock_req:
        mock_req.return_value = {"id": "should-not-happen"}
        resp = await client.submit_oto_short_order(
            symbol="LIDR", qty=5264, limit_price=2.42, stop_loss=2.70,
        )
    assert resp["status"] == "halted_by_operator"
    assert resp["side"] == "sell"
    mock_req.assert_not_called()


# ── Source-grep guard: single chokepoint maintained ─────────────────


def test_submit_oto_order_is_the_only_entry_chokepoint():
    """SOURCE-GREP GUARD: any new code path that submits a buy order
    (instead of going through submit_oto_order) bypasses the halt.
    This test pins the chokepoint: only submit_oto_order may construct
    the buy-side OTO payload. If a new function shows up that builds
    buy payloads directly (e.g., scripts that bypass the helper), this
    test surfaces it.

    Allowlist intentionally narrow — adding to it requires a halt-aware
    code review."""
    import re
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    src = repo / "src"
    offenders: list[tuple[str, int, str]] = []
    # Pattern: a literal "side": "buy" string in source code outside
    # the chokepoint and outside test/comment/doc files.
    for py in src.rglob("*.py"):
        # Allowlist files
        rel = py.relative_to(repo).as_posix()
        if rel == "src/data/alpaca_client.py":
            continue  # the chokepoint itself
        if "test_" in py.name or "/tests/" in rel:
            continue
        try:
            text = py.read_text(encoding="utf-8")
        except Exception:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.lstrip()
            if not stripped or stripped.startswith("#"):
                continue
            # Strip inline comments
            code = re.sub(r'\s+#.*$', '', line)
            if re.search(r'"side"\s*:\s*"buy"', code) or re.search(r"'side'\s*:\s*'buy'", code):
                offenders.append((rel, lineno, line.strip()[:120]))
    assert offenders == [], (
        "D277 chokepoint regression: code outside src/data/alpaca_client.py "
        "constructs a buy-side order payload, bypassing the halt switch.\n"
        "Add the file to the allowlist (with a halt-aware code review) OR "
        "route the new path through submit_oto_order:\n  " +
        "\n  ".join(f"{f}:{ln}: {t}" for f, ln, t in offenders)
    )
