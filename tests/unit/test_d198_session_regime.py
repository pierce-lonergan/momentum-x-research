"""D198: Unit tests for SessionRegimeDetector."""

from __future__ import annotations

import pytest

from src.execution.session_regime import SessionRegime, SessionRegimeDetector


EQUITY = 100_000.0


def make_detector(vix_at_open: float = 15.0) -> SessionRegimeDetector:
    d = SessionRegimeDetector()
    d.initialize_session(starting_equity=EQUITY, vix_at_open=vix_at_open)
    return d


# ── Regime transitions ───────────────────────────────────────────────────────

def test_normal_regime_at_start():
    d = make_detector()
    assert d.get_regime() == SessionRegime.NORMAL


def test_cautious_after_one_loss():
    d = make_detector()
    d.record_trade_result(pnl=-200.0, is_win=False)
    assert d.get_regime() == SessionRegime.CAUTIOUS


def test_defensive_after_two_losses():
    d = make_detector()
    d.record_trade_result(pnl=-200.0, is_win=False)
    d.record_trade_result(pnl=-150.0, is_win=False)
    assert d.get_regime() == SessionRegime.DEFENSIVE


def test_halted_after_three_losses():
    d = make_detector()
    d.record_trade_result(pnl=-200.0, is_win=False)
    d.record_trade_result(pnl=-150.0, is_win=False)
    d.record_trade_result(pnl=-100.0, is_win=False)
    assert d.get_regime() == SessionRegime.HALTED


def test_momentum_after_two_wins():
    d = make_detector()
    d.record_trade_result(pnl=500.0, is_win=True)
    d.record_trade_result(pnl=300.0, is_win=True)
    assert d.get_regime() == SessionRegime.MOMENTUM


def test_regime_resets_after_win():
    """One loss → CAUTIOUS, then a win → back to NORMAL (not MOMENTUM yet)."""
    d = make_detector()
    d.record_trade_result(pnl=-200.0, is_win=False)
    assert d.get_regime() == SessionRegime.CAUTIOUS
    d.record_trade_result(pnl=300.0, is_win=True)
    # consecutive_losses reset to 0, consecutive_wins = 1 → NORMAL
    assert d.get_regime() == SessionRegime.NORMAL


def test_drawdown_halt():
    """3% drawdown triggers HALTED regardless of consecutive losses."""
    d = make_detector()
    # Equity drops 3.5% — just above halt threshold
    d.update_equity(EQUITY * (1 - 0.035))
    assert d.get_regime() == SessionRegime.HALTED


def test_vix_spike_detection():
    d = make_detector(vix_at_open=15.0)
    d.update_vix(18.1)  # +3.1 pts — above threshold
    assert d._perf.vix_spike_detected is True
    assert d.get_regime() == SessionRegime.HALTED


def test_vix_below_spike_threshold():
    d = make_detector(vix_at_open=15.0)
    d.update_vix(17.9)  # +2.9 pts — below threshold
    assert d._perf.vix_spike_detected is False
    assert d.get_regime() == SessionRegime.NORMAL


# ── Size multipliers ─────────────────────────────────────────────────────────

def test_size_multiplier_normal():
    d = make_detector()
    assert d.get_size_multiplier() == 1.0


def test_size_multiplier_cautious():
    d = make_detector()
    d.record_trade_result(pnl=-100.0, is_win=False)
    assert d.get_size_multiplier() == 0.50


def test_size_multiplier_defensive():
    d = make_detector()
    d.record_trade_result(pnl=-100.0, is_win=False)
    d.record_trade_result(pnl=-100.0, is_win=False)
    assert d.get_size_multiplier() == 0.25


def test_size_multiplier_halted():
    d = make_detector()
    for _ in range(3):
        d.record_trade_result(pnl=-100.0, is_win=False)
    assert d.get_size_multiplier() == 0.0


def test_size_multiplier_momentum():
    d = make_detector()
    d.record_trade_result(pnl=500.0, is_win=True)
    d.record_trade_result(pnl=300.0, is_win=True)
    assert d.get_size_multiplier() == 1.5


# ── Entry gating ─────────────────────────────────────────────────────────────

def test_entry_blocked_when_halted():
    d = make_detector()
    for _ in range(3):
        d.record_trade_result(pnl=-100.0, is_win=False)
    allowed, reason = d.should_allow_new_entry()
    assert allowed is False
    assert "consecutive losses" in reason


def test_entry_allowed_when_normal():
    d = make_detector()
    allowed, reason = d.should_allow_new_entry()
    assert allowed is True
    assert reason == ""


def test_entry_allowed_when_cautious():
    d = make_detector()
    d.record_trade_result(pnl=-100.0, is_win=False)
    allowed, _ = d.should_allow_new_entry()
    assert allowed is True  # CAUTIOUS still allows entries


def test_entry_blocked_by_vix_spike():
    d = make_detector(vix_at_open=15.0)
    d.update_vix(20.0)
    allowed, reason = d.should_allow_new_entry()
    assert allowed is False
    assert "VIX" in reason


def test_entry_blocked_by_drawdown():
    d = make_detector()
    d.update_equity(EQUITY * 0.96)  # 4% drawdown → above 3% halt threshold
    allowed, reason = d.should_allow_new_entry()
    assert allowed is False
    assert "drawdown" in reason


# ── Status report ────────────────────────────────────────────────────────────

def test_status_report():
    d = make_detector()
    report = d.get_status_report()
    assert "D198" in report
    assert "regime=normal" in report
    assert "trades=0" in report
    assert "size_mult=1.00x" in report


# ── Consecutive counter resets on opposite result ────────────────────────────

def test_consecutive_counter_resets_on_opposite():
    d = make_detector()
    # Build up two losses
    d.record_trade_result(pnl=-100.0, is_win=False)
    d.record_trade_result(pnl=-100.0, is_win=False)
    assert d._perf.consecutive_losses == 2
    assert d._perf.consecutive_wins == 0

    # Win resets loss streak
    d.record_trade_result(pnl=200.0, is_win=True)
    assert d._perf.consecutive_losses == 0
    assert d._perf.consecutive_wins == 1

    # Another loss resets win streak
    d.record_trade_result(pnl=-50.0, is_win=False)
    assert d._perf.consecutive_wins == 0
    assert d._perf.consecutive_losses == 1
