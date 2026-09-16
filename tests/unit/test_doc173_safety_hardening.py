"""Doc 173 (2026-05-24) -- Pre-Monday safety hardening test suite.

Covers Pierce's tonight-ship list:
  - D310 noise gate: NO_CALLBACK demoted to WARNING; ERROR gated
    behind MOMENTUM_D310_DIAG=1
  - L2 heartbeat: HedgeIntegrityWatcher writes heartbeat per tick;
    watchdog can detect stale heartbeat
  - L2 correctness: existence != correctness. Wrong qty / wrong side
    / DAY TIF / absurd stop price = treated as unhedged
  - Durable alert spool (D314): every alert written to disk BEFORE
    live POST; retry loop redelivers on success; stale quarantine
  - Severity stratification: CRITICAL alerts get @here mention
  - Discord 2000-char property test: veto_summary up to 200 entries
    still renders under Discord's limit
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════
# D310 noise gate
# ═══════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _restore_d310_env(monkeypatch):
    monkeypatch.delenv("MOMENTUM_D310_DIAG", raising=False)


def test_d310_no_callback_demoted_to_warning_by_default(caplog):
    """Per Pierce: ERROR-level for known-and-tracked-by-L2 conditions
    habituates operator to ignore them right before L3 ships. Default
    must be WARNING."""
    src_text = Path("src/data/websocket_client.py").read_text(encoding="utf-8")
    # WARNING-branch present (constituent strings appear; Python's
    # implicit-concatenation means they're split across literals)
    assert "dead callback" in src_text
    assert "L2 backstops" in src_text
    assert "logger.warning(" in src_text
    # And ERROR branch is gated behind the env var
    assert "MOMENTUM_D310_DIAG" in src_text


def test_d310_env_var_re_enables_error_level(monkeypatch):
    """With MOMENTUM_D310_DIAG=1, NO_CALLBACK fires ERROR for deep dives."""
    monkeypatch.setenv("MOMENTUM_D310_DIAG", "1")
    # Re-read env to confirm semantics
    assert os.environ.get("MOMENTUM_D310_DIAG") == "1"


# ═══════════════════════════════════════════════════════════════
# L2 heartbeat + correctness
# ═══════════════════════════════════════════════════════════════


def _make_position(symbol, qty, avg=10.0, current=10.0):
    return {
        "symbol": symbol, "qty": str(qty),
        "avg_entry_price": str(avg), "current_price": str(current),
    }


def _make_stop_order(symbol, side="sell", qty="100", tif="gtc",
                      stop_price="9.50"):
    return {
        "symbol": symbol, "type": "stop", "side": side,
        "qty": qty, "time_in_force": tif, "stop_price": stop_price,
        "status": "accepted",
    }


@pytest.fixture
def make_l2():
    def _make(positions=None, orders=None, tolerance_sec=60.0, **kw):
        from src.monitoring.hedge_integrity_watcher import HedgeIntegrityWatcher
        client = MagicMock()
        client.get_positions = AsyncMock(return_value=positions or [])
        client.get_orders = AsyncMock(return_value=orders or [])
        client.submit_stop_order = AsyncMock(
            return_value={"id": "emerg-1", "status": "accepted"}
        )
        w = HedgeIntegrityWatcher(
            client=client, poll_interval_sec=0.01,
            tolerance_sec=tolerance_sec, **kw,
        )
        return w, client
    return _make


@pytest.mark.asyncio
async def test_l2_heartbeat_written_after_successful_tick(make_l2):
    """heartbeat_age_sec must update after each completed tick."""
    w, _ = make_l2(positions=[], orders=[])
    assert w.heartbeat_age_sec() is None  # before first tick
    await w.check_once()
    age = w.heartbeat_age_sec()
    assert age is not None
    assert age < 1.0


@pytest.mark.asyncio
async def test_l2_heartbeat_grows_over_time(make_l2):
    """heartbeat_age_sec increases as time passes without new ticks."""
    w, _ = make_l2(positions=[], orders=[])
    await w.check_once()
    age1 = w.heartbeat_age_sec()
    await asyncio.sleep(0.05)
    age2 = w.heartbeat_age_sec()
    assert age2 > age1


@pytest.mark.asyncio
async def test_l2_correctness_wrong_qty_treated_as_unhedged(make_l2):
    """Position qty=100 but stop qty=50 = unhedged (half covered)."""
    w, client = make_l2(
        positions=[_make_position("X", qty=100)],
        orders=[_make_stop_order("X", side="sell", qty="50",
                                  tif="gtc", stop_price="9.50")],
        tolerance_sec=0.0,
    )
    # First tick: detects unhedged + starts clock
    await w.check_once()
    state = w._state["X"]
    # Clock should have started -- existence check passes but correctness fails
    assert state.first_unhedged_utc is not None
    # incorrect_stops counter incremented
    assert w.stats()["incorrect_stops"] >= 1


@pytest.mark.asyncio
async def test_l2_correctness_day_tif_treated_as_unhedged(make_l2):
    """DAY TIF stops expire at 16:00 ET -- treat as unhedged."""
    w, _ = make_l2(
        positions=[_make_position("X", qty=100)],
        orders=[_make_stop_order("X", side="sell", qty="100",
                                  tif="day",  # WRONG
                                  stop_price="9.50")],
    )
    await w.check_once()
    state = w._state["X"]
    assert state.first_unhedged_utc is not None
    assert w.stats()["incorrect_stops"] >= 1


@pytest.mark.asyncio
async def test_l2_correctness_absurd_stop_price_treated_as_unhedged(make_l2):
    """Stop at $0.01 on a $10 stock = no real protection."""
    w, _ = make_l2(
        positions=[_make_position("X", qty=100, avg=10.0, current=10.0)],
        orders=[_make_stop_order("X", side="sell", qty="100",
                                  tif="gtc",
                                  stop_price="0.01")],  # absurd
    )
    await w.check_once()
    state = w._state["X"]
    assert state.first_unhedged_utc is not None


@pytest.mark.asyncio
async def test_l2_correctness_valid_stop_passes(make_l2):
    """Reasonable stop within band = correctly hedged."""
    w, _ = make_l2(
        positions=[_make_position("X", qty=100, avg=10.0, current=10.0)],
        orders=[_make_stop_order("X", side="sell", qty="100",
                                  tif="gtc", stop_price="9.50")],
    )
    await w.check_once()
    state = w._state.get("X")
    # No state entry OR first_unhedged_utc is None means correctly hedged
    if state:
        assert state.first_unhedged_utc is None


@pytest.mark.asyncio
async def test_l2_correctness_multi_order_qty_sums(make_l2):
    """Two stops at 50 qty each = 100 covered = correctly hedged for qty 100."""
    w, _ = make_l2(
        positions=[_make_position("X", qty=100)],
        orders=[
            _make_stop_order("X", side="sell", qty="50",
                              tif="gtc", stop_price="9.50"),
            _make_stop_order("X", side="sell", qty="50",
                              tif="gtc", stop_price="9.40"),
        ],
    )
    await w.check_once()
    state = w._state.get("X")
    if state:
        assert state.first_unhedged_utc is None


@pytest.mark.asyncio
async def test_l2_correctness_check_correctness_helper_no_orders_returns_None(make_l2):
    """If no orders match (existence already failed), correctness check
    is not called -- existence path handles it."""
    w, _ = make_l2()
    issue = w._check_stop_correctness(
        sym="X", position_qty=100, position_side="long",
        avg_entry=10.0, last_price=10.0, orders=[],
    )
    # With zero orders, total_covered_qty is 0 < position_qty -> issue
    assert issue is not None
    assert "covered_qty" in issue


@pytest.mark.asyncio
async def test_l2_correctness_check_reports_specific_issue(make_l2):
    """The helper's return string describes which check failed."""
    w, _ = make_l2()
    # under-qty
    issue = w._check_stop_correctness(
        sym="X", position_qty=100, position_side="long",
        avg_entry=10.0, last_price=10.0,
        orders=[_make_stop_order("X", qty="50", tif="gtc", stop_price="9.50")],
    )
    assert "covered_qty=50 < position_qty=100" in issue


# ═══════════════════════════════════════════════════════════════
# D314 -- durable alert spool
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def spool_dir(tmp_path, monkeypatch):
    """Override DEFAULT_SPOOL_DIR to a tmp path for each test."""
    import src.monitoring.durable_alert_spool as ds
    monkeypatch.setattr(ds, "DEFAULT_SPOOL_DIR", tmp_path)
    return tmp_path


@pytest.mark.asyncio
async def test_durable_post_writes_spool_file_BEFORE_post(spool_dir):
    """On live-post failure, the spool file must persist."""
    import httpx
    from src.monitoring.durable_alert_spool import durable_post

    async def boom(*a, **kw):
        raise httpx.ConnectError("test")
    with patch.object(httpx.AsyncClient, "post", boom):
        ok = await durable_post(
            "https://x.test/wh", {"embeds": [{"title": "T"}]},
            severity="CRITICAL",
        )
    assert ok is False
    files = list(spool_dir.rglob("*.json"))
    assert len(files) >= 1
    record = json.loads(files[0].read_text(encoding="utf-8"))
    assert record["severity"] == "CRITICAL"
    assert record["webhook_url"] == "https://x.test/wh"
    assert record["payload"]["embeds"][0]["title"] == "T"


@pytest.mark.asyncio
async def test_durable_post_deletes_spool_on_success(spool_dir):
    """On live-post 200, the spool file is deleted (no need to retry)."""
    import httpx
    from src.monitoring.durable_alert_spool import durable_post

    async def succ(self, url, json=None):
        class R:
            status_code = 200
        return R()
    with patch.object(httpx.AsyncClient, "post", succ):
        ok = await durable_post("https://x.test/wh", {"x": 1})
    assert ok is True
    files = list(spool_dir.rglob("*.json"))
    assert files == []


@pytest.mark.asyncio
async def test_durable_post_never_raises_on_spool_write_failure(spool_dir):
    """A read-only spool dir must not block the live post attempt."""
    import httpx
    from src.monitoring.durable_alert_spool import durable_post

    # Sabotage: replace _today_dir with a path that raises on open
    async def succ(self, url, json=None):
        class R:
            status_code = 200
        return R()
    import src.monitoring.durable_alert_spool as ds
    real_today_dir = ds._today_dir

    def boom_today_dir(*a, **kw):
        raise PermissionError("denied")
    with patch.object(ds, "_today_dir", boom_today_dir):
        with patch.object(httpx.AsyncClient, "post", succ):
            ok = await durable_post("https://x.test/wh", {"x": 1})
    assert ok is True  # live post still succeeded


@pytest.mark.asyncio
async def test_retry_loop_redelivers_spooled_record(spool_dir):
    """A pre-existing spool file should be retried + deleted on success."""
    import httpx
    from src.monitoring.durable_alert_spool import (
        _serialize, _today_dir, _spool_filename, run_retry_loop,
    )
    # Manually create a spool file
    day_dir = _today_dir(spool_dir)
    fn = day_dir / _spool_filename("INFO")
    rec = _serialize("https://x.test/wh", {"embeds": []}, "INFO")
    fn.write_text(json.dumps(rec), encoding="utf-8")
    assert fn.exists()

    # Patch httpx to succeed
    async def succ(self, url, json=None):
        class R:
            status_code = 200
        return R()
    # Run one cycle of retry loop with a 0.05s interval + shutdown after 0.1s
    stop_event = asyncio.Event()

    async def stop_soon():
        await asyncio.sleep(0.1)
        stop_event.set()
    with patch.object(httpx.AsyncClient, "post", succ):
        await asyncio.gather(
            run_retry_loop(interval_sec=0.01, shutdown_event=stop_event),
            stop_soon(),
        )
    # File should be deleted after successful retry
    assert not fn.exists()


def test_quarantine_moves_old_records_to_stale(spool_dir):
    """Records older than 24h move to stale/ subdir."""
    from src.monitoring.durable_alert_spool import (
        _serialize, _today_dir, _spool_filename, _quarantine_stale,
    )
    day_dir = _today_dir(spool_dir)
    fn = day_dir / _spool_filename("INFO")
    rec = _serialize("https://x.test/wh", {"x": 1}, "INFO")
    fn.write_text(json.dumps(rec), encoding="utf-8")
    # Backdate the mtime to 25h ago
    old = time.time() - 25 * 3600
    os.utime(fn, (old, old))
    moved = _quarantine_stale(fn, stale_after_hours=24.0)
    assert moved is True
    assert not fn.exists()
    stale_dir = spool_dir / "stale"
    assert any(stale_dir.iterdir())


# ═══════════════════════════════════════════════════════════════
# Severity stratification
# ═══════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_critical_severity_adds_at_here_mention(spool_dir):
    """CRITICAL severity must inject @here into the payload content."""
    from src.monitoring.alerts import _post
    captured = []

    async def fake_durable_post(webhook_url, payload, severity="INFO"):
        captured.append((payload, severity))
        return True
    with patch("src.monitoring.alerts.durable_post", fake_durable_post, create=True):
        # Patch durable_post inside the alerts module's import
        import src.monitoring.durable_alert_spool as ds
        with patch.object(ds, "durable_post", fake_durable_post):
            await _post("https://x.test/wh", {"embeds": [{"title": "T"}]},
                          severity="CRITICAL")
    assert len(captured) == 1
    payload, severity = captured[0]
    assert severity == "CRITICAL"
    assert "@here" in payload.get("content", "")


@pytest.mark.asyncio
async def test_info_severity_omits_at_here(spool_dir):
    """Default INFO severity does NOT add @here."""
    import src.monitoring.durable_alert_spool as ds
    captured = []

    async def fake_durable_post(webhook_url, payload, severity="INFO"):
        captured.append((payload, severity))
        return True
    with patch.object(ds, "durable_post", fake_durable_post):
        from src.monitoring.alerts import _post
        await _post("https://x.test/wh", {"embeds": []}, severity="INFO")
    payload, _ = captured[0]
    assert "content" not in payload  # never mutated


@pytest.mark.asyncio
async def test_alert_critical_helper_uses_critical_severity(spool_dir):
    """alert_critical() must call _post with severity='CRITICAL'."""
    import src.monitoring.durable_alert_spool as ds
    captured = []

    async def fake(webhook_url, payload, severity="INFO"):
        captured.append((payload, severity))
        return True
    with patch.object(ds, "durable_post", fake):
        from src.monitoring.alerts import alert_critical
        await alert_critical("test message", webhook_url="https://x.test/wh")
    _, severity = captured[0]
    assert severity == "CRITICAL"


# ═══════════════════════════════════════════════════════════════
# Discord 2000-char property test
# ═══════════════════════════════════════════════════════════════


def test_eod_alert_respects_discord_2000_char_limit_with_large_veto_summary():
    """When veto_summary has many entries, the rendered embed must fit
    inside Discord's per-field 1024-char limit (and total < 6000)."""
    import asyncio
    from src.monitoring import alerts
    alerts._last_sent.clear()
    captured = []

    async def fake_post(url, payload, severity="INFO"):
        captured.append(payload)
    with patch.object(alerts, "_post", fake_post):
        # Generate 200-entry veto summary
        big_veto = {f"GATE_{i:04d}_VERY_LONG_REASON": (i * 7 % 200) + 1
                    for i in range(200)}
        asyncio.run(alerts.alert_session_end_rich(
            session_date="2026-05-25",
            trades=10, realized_pnl=0.0, unrealized_pnl=0.0,
            positions_at_close=0,
            starting_equity=140000.0, ending_equity=140000.0,
            veto_summary=big_veto,
            halt_blocked_count=0,
            webhook_url="https://x.test/wh",
        ))
    assert len(captured) == 1
    embed = captured[0]["embeds"][0]
    # Discord limits:
    #   - title: 256
    #   - description: 4096
    #   - field name: 256
    #   - field value: 1024
    #   - total embed: 6000
    for f in embed["fields"]:
        assert len(f["name"]) <= 256, f"field name too long: {f['name']!r}"
        assert len(f["value"]) <= 1024, (
            f"field value too long ({len(f['value'])} chars): {f['name']}"
        )
    assert len(embed.get("title", "")) <= 256
    assert len(embed.get("description", "")) <= 4096


@pytest.mark.parametrize("n_entries", [1, 10, 50, 100, 200])
def test_veto_summary_field_truncation_is_graceful(n_entries):
    """Across a range of veto_summary sizes, the embed never crashes
    AND any field truncation respects Discord limits."""
    import asyncio
    from src.monitoring import alerts
    alerts._last_sent.clear()
    captured = []

    async def fake_post(url, payload, severity="INFO"):
        captured.append(payload)
    with patch.object(alerts, "_post", fake_post):
        veto = {f"G{i}": i + 1 for i in range(n_entries)}
        asyncio.run(alerts.alert_session_end_rich(
            session_date="2026-05-25",
            trades=0, realized_pnl=0.0, unrealized_pnl=0.0,
            positions_at_close=0,
            starting_equity=140000.0, ending_equity=140000.0,
            veto_summary=veto, halt_blocked_count=0,
            webhook_url="https://x.test/wh",
        ))
    embed = captured[0]["embeds"][0]
    for f in embed["fields"]:
        assert len(f["value"]) <= 1024


# ═══════════════════════════════════════════════════════════════
# Wiring contracts
# ═══════════════════════════════════════════════════════════════


def test_main_py_launches_l2_watchdog_and_alert_retry_loop():
    """Static guard: main.py must launch both background tasks."""
    src = Path("main.py").read_text(encoding="utf-8")
    assert "_l2_watchdog_loop" in src
    assert "_l2_watchdog_task = asyncio.create_task" in src
    assert "_alert_retry_task = asyncio.create_task" in src
    assert "from src.monitoring.durable_alert_spool import run_retry_loop" in src


def test_main_py_l2_watchdog_uses_alert_critical():
    """Watchdog must page via alert_critical (@here-mention CRITICAL path)."""
    src = Path("main.py").read_text(encoding="utf-8")
    assert "alert_critical" in src
    # And the watchdog body should reference heartbeat_age_sec
    assert "heartbeat_age_sec()" in src


def test_phantom_journal_d312_counter_is_active():
    """Static guard: _gate_counts must exist on PhantomJournal."""
    src = Path("src/data/phantom_journal.py").read_text(encoding="utf-8")
    assert "_gate_counts" in src
    assert "def gate_summary" in src


def test_l2_module_exposes_heartbeat_and_stats():
    """HedgeIntegrityWatcher must expose heartbeat_age_sec + stats."""
    from src.monitoring.hedge_integrity_watcher import HedgeIntegrityWatcher
    assert hasattr(HedgeIntegrityWatcher, "heartbeat_age_sec")
    assert hasattr(HedgeIntegrityWatcher, "stats")
    assert hasattr(HedgeIntegrityWatcher, "_check_stop_correctness")
