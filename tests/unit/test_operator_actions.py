"""doc 207: tests for the Operator T1 action toolkit (governance-gated, reversible, live)."""
from __future__ import annotations

from src.ops import operator_actions as A
from src.ops import operator_governance as g


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "_HALT_FILE", tmp_path / "HALT_NEW_ENTRIES")
    monkeypatch.setattr(g, "_OPS", tmp_path)
    monkeypatch.setattr(g, "_FUSE", tmp_path / "fuse.json")
    # snapshot is best-effort + guarded; let it run against tmp
    import src.ops.state_snapshot as ss
    monkeypatch.setattr(ss, "_SNAP_DIR", tmp_path / "snaps")
    monkeypatch.setattr(ss, "_ROOT", tmp_path)
    monkeypatch.setattr(ss, "_STATE_FILES", [])
    for k in ("OPS_OPERATOR_T1_ENABLED", "OPS_OPERATOR_T1_REARM"):
        monkeypatch.delenv(k, raising=False)


def test_halt_drops_file_and_is_reversible(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    r = A.halt_new_entries("drawdown approaching", confidence=0.9,
                           session_count=0, daily_count=0)
    assert r.ok and A.is_halted() is True
    assert r.rollback_cmd and "HALT_NEW_ENTRIES" in r.rollback_cmd  # the one-line rollback
    assert "LIVE" in r.applies
    # rollback
    A.clear_halt()
    assert A.is_halted() is False


def test_halt_blocked_by_low_confidence(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    r = A.halt_new_entries("hunch", confidence=0.5, session_count=0, daily_count=0)
    assert r.ok is False and r.blocked is True and A.is_halted() is False


def test_halt_blocked_when_fuse_tripped(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    g.record_t1_outcome(False); g.record_t1_outcome(False); g.record_t1_outcome(False)  # trip
    r = A.halt_new_entries("real issue", confidence=0.95, session_count=0, daily_count=0)
    assert r.ok is False and r.blocked is True   # soft fuse refuses
    assert A.is_halted() is False


def test_halt_blocked_by_session_cap(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    r = A.halt_new_entries("x", confidence=0.9, session_count=6, daily_count=0)
    assert r.ok is False and r.blocked is True


def test_stage_flag_safer_only_steps_toward_safety(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    sp = tmp_path / "secrets.env"
    sp.write_text("EXEC_MAX_POSITIONS=8\n", encoding="utf-8")
    # legal: 8 -> 4 (safer)
    r = A.stage_flag_safer("EXEC_MAX_POSITIONS", 8, 4, reason="derisk", confidence=0.9,
                           session_count=0, daily_count=0, secrets_path=sp)
    assert r.ok and "NEXT RESTART" in r.applies
    assert "EXEC_MAX_POSITIONS=4" in sp.read_text()
    # illegal: 4 -> 8 (UP) is refused by governance
    r2 = A.stage_flag_safer("EXEC_MAX_POSITIONS", 4, 8, reason="nope", confidence=0.9,
                            session_count=0, daily_count=0, secrets_path=sp)
    assert r2.ok is False and r2.blocked is True
