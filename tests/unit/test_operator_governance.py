"""doc 206: tests for the Operator governance rails (Pierce's critique made mechanical)."""
from __future__ import annotations

import importlib

from src.ops import operator_governance as g


def _fresh(tmp_path, monkeypatch):
    """Point all governance state files at a tmp dir + reset env."""
    monkeypatch.setattr(g, "_OPS", tmp_path)
    monkeypatch.setattr(g, "_FUSE", tmp_path / "fuse.json")
    monkeypatch.setattr(g, "_LOCK", tmp_path / "repo.lock")
    monkeypatch.setattr(g, "_IN_FLIGHT", tmp_path / "in_flight.json")
    for k in ("OPS_OPERATOR_T1_ENABLED", "OPS_OPERATOR_T1_REARM"):
        monkeypatch.delenv(k, raising=False)


# ── declared T1 transitions (no feel, no up) ──

def test_ladder_only_steps_toward_safety():
    assert g.legal_t1_transition("EXEC_RISK_PER_TRADE_PCT", 0.02, 0.01) is True   # safer
    assert g.legal_t1_transition("EXEC_RISK_PER_TRADE_PCT", 0.01, 0.02) is False  # UP = illegal
    assert g.legal_t1_transition("EXEC_RISK_PER_TRADE_PCT", 0.01, 0.013) is False  # off-ladder
    assert g.legal_t1_transition("EXEC_MAX_POSITIONS", 8, 4) is True
    assert g.legal_t1_transition("EXEC_MAX_POSITIONS", 4, 8) is False


def test_safe_set_can_disable_not_enable():
    assert g.legal_t1_transition("EXEC_ELITE_SIZING_PRESS_ENABLED", "true", "false") is True
    assert g.legal_t1_transition("EXEC_ELITE_SIZING_PRESS_ENABLED", "false", "true") is False
    assert g.legal_t1_transition("MOMENTUM_HALT_NEW_ENTRIES", "0", "1") is True  # may HALT
    assert g.legal_t1_transition("MOMENTUM_HALT_NEW_ENTRIES", "1", "0") is False  # re-arm = Pierce


def test_unknown_flag_is_not_t1():
    assert g.legal_t1_transition("SOME_RANDOM_FLAG", 1, 2) is False


# ── confidence + reversibility ──

def test_confidence_gate():
    assert g.confidence_ok(0.71) is True
    assert g.confidence_ok(0.5) is False
    assert g.confidence_ok(None) is False


def test_reversibility_requires_one_line_command():
    assert g.reversibility_ok("set MOMENTUM_HALT_NEW_ENTRIES=1") is True
    assert g.reversibility_ok("") is False
    assert g.reversibility_ok(None) is False
    assert g.reversibility_ok("do a thing\nthen another") is False  # multi-line = not crisp


# ── the soft fuse ──

def test_soft_fuse_trips_on_failed_t1s(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    assert g.is_t1_armed()[0] is True
    g.record_t1_outcome(False); g.record_t1_outcome(False)
    assert g.is_t1_armed()[0] is True   # 2 fails: still armed
    g.record_t1_outcome(False)          # 3rd fail -> trip
    armed, why = g.is_t1_armed()
    assert armed is False and "fuse" in why
    # Pierce re-arms
    monkeypatch.setenv("OPS_OPERATOR_T1_REARM", "1")
    assert g.is_t1_armed()[0] is True
    g.record_t1_outcome(True)           # success resets the streak
    assert g.fuse_state()["failed_t1_streak"] == 0


def test_t1_allowed_requires_everything(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    base = dict(flag="EXEC_MAX_POSITIONS", current=8, proposed=4, confidence=0.8,
                rollback_cmd="set EXEC_MAX_POSITIONS=8", session_count=0, daily_count=0)
    assert g.t1_allowed(**base)[0] is True
    assert g.t1_allowed(**{**base, "confidence": 0.5})[0] is False        # confidence
    assert g.t1_allowed(**{**base, "rollback_cmd": ""})[0] is False       # reversibility
    assert g.t1_allowed(**{**base, "proposed": 10})[0] is False           # not safer
    assert g.t1_allowed(**{**base, "session_count": 6})[0] is False       # session cap
    assert g.t1_allowed(**{**base, "daily_count": 12})[0] is False        # daily cap


# ── the mechanical T2 gate ──

def test_t2_gate_demands_domination_of_production():
    prod = {"primary": 1.0, "max_drawdown": 0.06, "hit_rate": 0.50}
    assert g.t2_gate(prod, {"primary": 1.2, "max_drawdown": 0.05, "hit_rate": 0.52}, True)[0] is True
    assert g.t2_gate(prod, {"primary": 0.9, "max_drawdown": 0.05, "hit_rate": 0.52}, True)[0] is False  # worse primary
    assert g.t2_gate(prod, {"primary": 1.2, "max_drawdown": 0.09, "hit_rate": 0.52}, True)[0] is False  # worse DD
    assert g.t2_gate(prod, {"primary": 1.2, "max_drawdown": 0.05, "hit_rate": 0.40}, True)[0] is False  # hit-rate floor
    assert g.t2_gate(prod, {"primary": 1.2, "max_drawdown": 0.05, "hit_rate": 0.52}, False)[0] is False  # tests red


# ── repo lock (session coordination) ──

def test_repo_lock_is_exclusive(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    assert g.acquire_repo_lock("sess-A") is True
    assert g.acquire_repo_lock("sess-B") is False   # A owns it
    g.release_repo_lock("sess-A")
    assert g.acquire_repo_lock("sess-B") is True     # now free
