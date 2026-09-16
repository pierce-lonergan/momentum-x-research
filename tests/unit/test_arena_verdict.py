"""Unit tests for src/production_arena/verdict.py.

Covers:
1. JSONL round-trip (write then read).
2. Append safety (mode="a" behavior).
3. Lenient ``verdict_from_dict`` for minimal payloads.
4. Journal-row → ProductionVerdict bridge for BUY / NO_TRADE / non-eval rows.
5. ``ProductionVerdict.summary()`` content.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.production_arena.types import ProductionVerdict
from src.production_arena.verdict import (
    journal_to_verdict,
    read_verdicts,
    verdict_from_dict,
    write_verdicts,
)


# ── Fixtures ────────────────────────────────────────────────────────────


def _make_verdict(ticker: str = "ABCD", decision: str = "BUY", **overrides) -> ProductionVerdict:
    base = dict(
        ticker=ticker,
        session_date="2026-04-15",
        decision=decision,
        gate_rejected=None,
        mfcs=0.42,
        mfcs_components={"news": 0.5, "technical": 0.3, "risk": 0.7},
        would_be_entry_price=1.23,
        would_be_exit_price=1.45,
        would_be_exit_time="2026-04-15T15:30:00Z",
        would_be_pnl_pct=0.0179,
        mfe_pct=0.052,
        mae_pct=-0.011,
        orb_confirmed=True,
        elapsed_ms=12.5,
        error_msg=None,
    )
    base.update(overrides)
    return ProductionVerdict(**base)


# ── Tests ───────────────────────────────────────────────────────────────


def test_write_then_read_roundtrip(tmp_path: Path) -> None:
    """Write 3 verdicts, read them back, assert equality."""
    out = tmp_path / "verdicts.jsonl"
    verdicts = [
        _make_verdict("AAAA", "BUY"),
        _make_verdict("BBBB", "NO_TRADE", gate_rejected="adaptive_router.py:149", mfcs=0.10),
        _make_verdict("CCCC", "ERROR", error_msg="bar load failed", mfcs=None,
                      mfcs_components=None, would_be_entry_price=None,
                      would_be_exit_price=None, would_be_exit_time=None,
                      would_be_pnl_pct=None, mfe_pct=None, mae_pct=None,
                      orb_confirmed=None),
    ]
    n = write_verdicts(verdicts, out)
    assert n == 3
    assert out.exists()

    loaded = read_verdicts(out)
    assert loaded == verdicts


def test_append_to_existing(tmp_path: Path) -> None:
    """Write 2, append 2 more, read back 4 in original order."""
    out = tmp_path / "verdicts.jsonl"
    first = [_make_verdict("ONE"), _make_verdict("TWO")]
    second = [_make_verdict("THREE"), _make_verdict("FOUR")]

    assert write_verdicts(first, out) == 2
    assert write_verdicts(second, out) == 2

    loaded = read_verdicts(out)
    assert len(loaded) == 4
    assert [v.ticker for v in loaded] == ["ONE", "TWO", "THREE", "FOUR"]


def test_verdict_from_dict_handles_missing_optional() -> None:
    """A minimal dict with only the 3 required fields produces a valid verdict."""
    minimal = {"ticker": "MIN", "session_date": "2026-04-16", "decision": "NO_TRADE"}
    v = verdict_from_dict(minimal)
    assert v.ticker == "MIN"
    assert v.session_date == "2026-04-16"
    assert v.decision == "NO_TRADE"
    assert v.mfcs is None
    assert v.mfcs_components is None
    assert v.would_be_entry_price is None
    assert v.elapsed_ms == 0.0  # default fallback
    assert v.error_msg is None


def test_verdict_from_dict_missing_required_raises() -> None:
    """Missing ticker/session_date/decision must raise — not silently fill."""
    with pytest.raises(KeyError):
        verdict_from_dict({"session_date": "2026-04-16", "decision": "BUY"})


def test_journal_to_verdict_buy_action() -> None:
    """A journal row with action=BUY produces decision=BUY plus would_be_entry_price."""
    row = {
        "ticker": "BUYY",
        "session_date": "2026-04-16",
        "action": "BUY",
        "mfcs": 0.55,
        "component_scores": {"news": 0.6, "technical": 0.5, "risk": 0.7, "volume_rvol": 0.4},
        "entry_price": 2.34,
        "rejection_reason": "",
        "pipeline_latency_ms": 87.4,
    }
    v = journal_to_verdict(row)
    assert v is not None
    assert v.decision == "BUY"
    assert v.ticker == "BUYY"
    assert v.session_date == "2026-04-16"
    assert v.would_be_entry_price == pytest.approx(2.34)
    assert v.mfcs == pytest.approx(0.55)
    assert v.mfcs_components is not None
    assert v.mfcs_components["news"] == pytest.approx(0.6)
    assert v.gate_rejected is None
    assert v.elapsed_ms == pytest.approx(87.4)


def test_journal_to_verdict_no_trade_action() -> None:
    """action=NO_TRADE with rejection_reason produces decision=NO_TRADE + gate_rejected."""
    row = {
        "ticker": "BEX",
        "session_date": "2026-04-15",
        "action": "NO_TRADE",
        "mfcs": 0.085,
        "component_scores": {"catalyst_news": 0.0, "volume_rvol": 0.34},
        "entry_price": 41.0,
        "rejection_reason": "D101 consensus gate: 0 directional agents < 1 minimum",
        "pipeline_latency_ms": 0.0,
    }
    v = journal_to_verdict(row)
    assert v is not None
    assert v.decision == "NO_TRADE"
    assert v.gate_rejected == "D101 consensus gate: 0 directional agents < 1 minimum"
    # entry_price should NOT propagate for NO_TRADE — that's a "would-be"
    # field reserved for actual buys.
    assert v.would_be_entry_price is None
    assert v.mfcs == pytest.approx(0.085)


def test_journal_to_verdict_skips_non_evaluation() -> None:
    """Session-start markers, telemetry pings, and empty-action stubs return None."""
    # Session-start marker style — no ticker/action.
    assert journal_to_verdict({"event": "session_start", "timestamp": "..."}) is None
    # Telemetry ping with ticker but no action.
    assert journal_to_verdict({"ticker": "PING", "metric": "heartbeat"}) is None
    # Real production fill-stub: action is the empty string.
    assert journal_to_verdict({
        "ticker": "SOPA",
        "session_date": "2026-04-16",
        "action": "",
        "fill_price": 0.4314,
        "order_status": "filled",
    }) is None
    # Defensive: non-dict / None inputs.
    assert journal_to_verdict({}) is None  # type: ignore[arg-type]


def test_summary_format() -> None:
    """summary() string contains ticker, date, decision."""
    buy = _make_verdict("AAAA", "BUY", mfcs=0.42, would_be_pnl_pct=0.05, mfe_pct=0.08)
    s_buy = buy.summary()
    assert "AAAA" in s_buy
    assert "2026-04-15" in s_buy
    assert "BUY" in s_buy

    no_trade = _make_verdict(
        "BBBB", "NO_TRADE", gate_rejected="adaptive_router.py:149", mfcs=0.10,
    )
    s_no = no_trade.summary()
    assert "BBBB" in s_no
    assert "2026-04-15" in s_no
    assert "NO_TRADE" in s_no

    err = _make_verdict("CCCC", "ERROR", error_msg="bar load failed")
    s_err = err.summary()
    assert "CCCC" in s_err
    assert "2026-04-15" in s_err
    assert "ERROR" in s_err
