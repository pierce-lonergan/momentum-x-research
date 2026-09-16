"""doc 182: tests for the rejection-outcome shadow (grade-the-rejected instrumentation).

The production-path guarantee: log_rejection_for_grading writes a well-formed,
lookahead-free row and NEVER raises into the trade loop.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.shadow import rejection_outcome_shadow as ros


def test_logs_well_formed_row():
    captured = {}
    fake_logger = MagicMock()
    fake_logger.log.side_effect = lambda row: captured.update(row)
    with patch.object(ros, "get_shadow_logger", return_value=fake_logger):
        ok = ros.log_rejection_for_grading(
            ticker="CGTL", gate="D160_FALLER", decision_price=0.51,
            mfcs=0.615, score=0.669, reason="predicted_fader",
            gap_pct=0.849, rvol=96.0,
        )
    assert ok is True
    assert captured["kind"] == "rejection_outcome"
    assert captured["ticker"] == "CGTL"
    assert captured["gate"] == "D160_FALLER"
    assert captured["decision_price"] == 0.51
    assert captured["score"] == 0.669
    # Lookahead-free: outcome is null at decision time (finalizer fills it).
    assert captured["outcome"] is None
    assert captured["decision_ts_utc"].endswith("Z")


def test_never_raises_on_bad_input():
    fake_logger = MagicMock()
    with patch.object(ros, "get_shadow_logger", return_value=fake_logger):
        # None price, missing optionals — must not raise, must not crash the caller.
        ok = ros.log_rejection_for_grading(
            ticker="X", gate="D170_ENTRY_DELAY", decision_price=None,
        )
    assert ok is True  # logged (price just stored as None)


def test_disabled_via_env(monkeypatch):
    monkeypatch.setenv("SHADOW_REJECTION_GRADING_ENABLED", "false")
    fake_logger = MagicMock()
    with patch.object(ros, "get_shadow_logger", return_value=fake_logger):
        ok = ros.log_rejection_for_grading(
            ticker="X", gate="D160_FALLER", decision_price=1.0,
        )
    assert ok is False
    fake_logger.log.assert_not_called()


def test_logger_failure_is_swallowed():
    fake_logger = MagicMock()
    fake_logger.log.side_effect = RuntimeError("disk full")
    with patch.object(ros, "get_shadow_logger", return_value=fake_logger):
        ok = ros.log_rejection_for_grading(
            ticker="X", gate="D160_FALLER", decision_price=1.0,
        )
    assert ok is False  # swallowed, returned False, did NOT raise
