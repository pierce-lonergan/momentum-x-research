"""D312 (2026-05-24, doc 172) — Phase-2-BUY-to-OTO gate observability.

Pin the production gap from Friday 2026-05-22:
  Phase 2 emitted "10 BUY, 0 HOLD, 0 NO_TRADE" repeatedly all morning.
  Bot made 0 OTO submits all day. EOD Discord said "0 trades, system
  scanned but no candidates met the quality bar" — misleading.

  Root cause: 581 gate-reject logs fired but the operator couldn't
  see the breakdown. Distribution by gate:
    D216_RECENTLY_CLOSED: 123  (recently-stopped re-entry guard)
    D85_FAST_PATH:        21
    D56_DUPLICATE:        1
    + many other gates    400+

  Pierce's argument: if T2 fires Monday and D216 silently eats half
  the wide-arm signals, the A/B data is corrupted and we won't know
  why. Fix is observability, not gate logic.

D312 fix:
  1. PhantomJournal counts gate rejections in-memory (per session).
  2. .gate_summary() returns {gate_name: count}.
  3. main.py D304 EOD block pulls this and passes as veto_summary
     kwarg into alert_session_end_rich.
  4. The EOD Discord embed (alerts.py) already had veto_summary
     rendering logic (doc 161), it just wasn't receiving data.

Tests:
  - PhantomJournal.update_gate increments the counter
  - EXECUTED / PENDING are operational, NOT counted
  - gate_summary returns dict copy (immutability)
  - alert_session_end_rich receives + renders veto_summary
  - Static guard: alert kwarg contract includes veto_summary
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ═══════════════════════════════════════════════════════════════
# PhantomJournal gate counter
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def journal(tmp_path):
    from src.data.phantom_journal import PhantomJournal
    return PhantomJournal(output_dir=str(tmp_path))


def test_initial_gate_summary_is_empty(journal):
    assert journal.gate_summary() == {}


def test_update_gate_increments_counter(journal):
    journal.update_gate("AAPL", "D216_RECENTLY_CLOSED")
    journal.update_gate("MSFT", "D216_RECENTLY_CLOSED")
    journal.update_gate("X", "D85_FAST_PATH")
    s = journal.gate_summary()
    assert s["D216_RECENTLY_CLOSED"] == 2
    assert s["D85_FAST_PATH"] == 1


def test_executed_pending_are_NOT_counted(journal):
    """Operational states (EXECUTED, PENDING) are not rejections."""
    journal.update_gate("AAPL", "EXECUTED")
    journal.update_gate("MSFT", "PENDING")
    journal.update_gate("X", "D216_RECENTLY_CLOSED")
    s = journal.gate_summary()
    assert "EXECUTED" not in s
    assert "PENDING" not in s
    assert s == {"D216_RECENTLY_CLOSED": 1}


def test_gate_summary_returns_dict_copy(journal):
    """Mutating the returned dict must NOT corrupt internal state."""
    journal.update_gate("A", "X")
    s1 = journal.gate_summary()
    s1["X"] = 999
    s2 = journal.gate_summary()
    assert s2["X"] == 1


def test_friday_replay_produces_expected_breakdown(journal):
    """Replay the Friday 5/22 pattern: 123 D216 + 21 D85 + 1 D56."""
    for _ in range(123):
        journal.update_gate("X", "D216_RECENTLY_CLOSED")
    for _ in range(21):
        journal.update_gate("Y", "D85_FAST_PATH")
    journal.update_gate("Z", "D56_DUPLICATE")
    s = journal.gate_summary()
    assert s == {
        "D216_RECENTLY_CLOSED": 123,
        "D85_FAST_PATH": 21,
        "D56_DUPLICATE": 1,
    }
    # Top gate is the dominant one
    top = max(s.items(), key=lambda kv: kv[1])
    assert top == ("D216_RECENTLY_CLOSED", 123)


def test_update_gate_jsonl_write_still_happens(journal, tmp_path):
    """Backwards-compat: counter is ADDITIONAL, doesn't replace the
    JSONL write that phantom_replay.py depends on."""
    journal.update_gate("AAPL", "D216_RECENTLY_CLOSED")
    lines = list(journal._file_path.read_text(encoding="utf-8").splitlines())
    assert any('"D216_RECENTLY_CLOSED"' in ln for ln in lines)


def test_update_gate_never_raises_on_counter_failure(journal, monkeypatch):
    """If the counter explodes (memory pressure, etc.), update_gate
    must still emit the JSONL line and not crash the caller."""
    # Sabotage the counter
    journal._gate_counts = None  # type: ignore
    try:
        journal.update_gate("AAPL", "D216_RECENTLY_CLOSED")
    except Exception:
        pytest.fail("update_gate must never raise on counter failure")


# ═══════════════════════════════════════════════════════════════
# EOD alert wiring contract (D304 + D312 union)
# ═══════════════════════════════════════════════════════════════


def test_main_py_passes_veto_summary_to_eod_alert():
    """Static guard: main.py D304 block must pull _phantom.gate_summary()
    and pass it as veto_summary kwarg into alert_session_end_rich."""
    src = Path("main.py").read_text(encoding="utf-8")
    # The kwarg must appear in the alert call
    assert "veto_summary=_d312_veto_summary" in src, (
        "main.py D304 EOD block must pass veto_summary kwarg to "
        "alert_session_end_rich -- D312 fix"
    )
    # And the summary must come from _phantom.gate_summary()
    assert "_phantom.gate_summary()" in src, (
        "main.py must invoke _phantom.gate_summary() to populate veto_summary"
    )


def test_phantom_journal_exposes_gate_summary_method():
    from src.data.phantom_journal import PhantomJournal
    assert hasattr(PhantomJournal, "gate_summary")
    assert callable(PhantomJournal.gate_summary)


def test_eod_alert_renders_veto_summary_when_provided():
    """alert_session_end_rich already had veto_summary rendering logic
    (doc 161) -- this test confirms the embed includes a 'Vetoes by
    Reason' field when veto_summary is non-empty."""
    import asyncio
    from unittest.mock import patch
    from src.monitoring import alerts
    alerts._last_sent.clear()
    captured = []
    async def fake_post(url, payload): captured.append(payload)
    with patch.object(alerts, "_post", fake_post):
        asyncio.run(alerts.alert_session_end_rich(
            session_date="2026-05-22",
            trades=0, realized_pnl=0.0, unrealized_pnl=0.0,
            positions_at_close=0,
            starting_equity=148000.0, ending_equity=148000.0,
            veto_summary={
                "D216_RECENTLY_CLOSED": 123,
                "D85_FAST_PATH": 21,
                "D56_DUPLICATE": 1,
            },
            halt_blocked_count=0,
            webhook_url="https://example.com/wh",
        ))
    assert len(captured) == 1
    embed = captured[0]["embeds"][0]
    # The veto summary field should appear
    field_names = [f["name"] for f in embed["fields"]]
    veto_field = next((f for f in embed["fields"] if "Veto" in f["name"]), None)
    assert veto_field is not None, (
        f"alert_session_end_rich did not render veto_summary field. "
        f"field names: {field_names}"
    )
    # And the breakdown shows the top gate
    assert "D216_RECENTLY_CLOSED" in veto_field["value"]
    assert "123" in veto_field["value"]


def test_eod_alert_omits_veto_field_when_summary_is_none_or_empty():
    """Backwards-compat: no veto_summary -> no field, no broken layout."""
    import asyncio
    from unittest.mock import patch
    from src.monitoring import alerts
    alerts._last_sent.clear()
    captured = []
    async def fake_post(url, payload): captured.append(payload)
    with patch.object(alerts, "_post", fake_post):
        asyncio.run(alerts.alert_session_end_rich(
            session_date="2026-05-22",
            trades=0, realized_pnl=0.0, unrealized_pnl=0.0,
            positions_at_close=0,
            starting_equity=148000.0, ending_equity=148000.0,
            veto_summary=None,
            halt_blocked_count=0,
            webhook_url="https://example.com/wh",
        ))
    embed = captured[0]["embeds"][0]
    field_names = [f["name"] for f in embed["fields"]]
    assert not any("Veto" in fn for fn in field_names)
