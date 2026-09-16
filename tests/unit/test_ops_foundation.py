"""doc 205: tests for the Operator foundation — incident bus, snapshot/rollback, scorecard.

These are the safety + game backbone for the autonomous Operator (doc 204). The bus must
never raise into trading; the snapshot must produce a restorable point; the score must be
honest and "beat your own best" must work.
"""
from __future__ import annotations

import json

from src.ops import incident_bus, state_snapshot
from src.ops.operator_scorecard import OperatorMetrics, compute_score, record_session, standings


# ── incident bus ──

def test_emit_and_read_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(incident_bus, "_OPS_DIR", tmp_path)
    monkeypatch.setattr(incident_bus, "_WAKE", tmp_path / "WAKE")
    incident_bus._recent.clear()
    assert incident_bus.emit_incident("GHOST_DETECTED", "CRITICAL", ticker="APPS",
                                      context={"qty": 970}) is True
    rows = incident_bus.read_incidents()
    assert len(rows) == 1 and rows[0]["kind"] == "GHOST_DETECTED"
    assert rows[0]["severity"] == "CRITICAL" and rows[0]["ticker"] == "APPS"
    # CRITICAL drops a WAKE sentinel for the event-driven Operator
    assert incident_bus.has_wake()
    incident_bus.clear_wake()
    assert not incident_bus.has_wake()


def test_emit_never_raises_on_bad_input(tmp_path, monkeypatch):
    monkeypatch.setattr(incident_bus, "_OPS_DIR", tmp_path)
    incident_bus._recent.clear()
    # unknown severity is coerced to WARN, not an error
    assert incident_bus.emit_incident("X", "NONSENSE") is True
    assert incident_bus.read_incidents()[0]["severity"] == "WARN"


def test_dedup_suppresses_flapping(tmp_path, monkeypatch):
    monkeypatch.setattr(incident_bus, "_OPS_DIR", tmp_path)
    monkeypatch.setattr(incident_bus, "_WAKE", tmp_path / "WAKE")
    incident_bus._recent.clear()
    assert incident_bus.emit_incident("CB_TRIP", "WARN", dedup_key="cb") is True
    assert incident_bus.emit_incident("CB_TRIP", "WARN", dedup_key="cb") is False  # suppressed
    assert len(incident_bus.read_incidents()) == 1


def test_disabled_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(incident_bus, "_OPS_DIR", tmp_path)
    monkeypatch.setenv("OPS_INCIDENT_BUS_ENABLED", "false")
    incident_bus._recent.clear()
    assert incident_bus.emit_incident("X", "WARN") is False


# ── snapshot / rollback ──

def test_snapshot_restores_state(tmp_path, monkeypatch):
    monkeypatch.setattr(state_snapshot, "_SNAP_DIR", tmp_path / "snaps")
    monkeypatch.setattr(state_snapshot, "_ROOT", tmp_path)
    monkeypatch.setattr(state_snapshot, "_STATE_FILES", ["state.json"])
    sf = tmp_path / "state.json"
    sf.write_text(json.dumps({"v": 1}), encoding="utf-8")
    man = state_snapshot.snapshot("before-T1")
    assert man.get("id") and "state.json" in man.get("files", [])
    # mutate, then restore
    sf.write_text(json.dumps({"v": 999}), encoding="utf-8")
    assert state_snapshot.restore_state(man["id"]) is True
    assert json.loads(sf.read_text())["v"] == 1   # rolled back


# ── scorecard / the game ──

def test_restraint_scores_higher_than_activity():
    """doc 206 Goodhart fix: a session that correctly does NOTHING beats a busy one."""
    quiet = compute_score(OperatorMetrics(session_date="d", correct_noops=2))   # +50
    busy = compute_score(OperatorMetrics(session_date="d", appropriate_actions=1,
                                         unnecessary_actions=1, false_positives=1))  # 10-20-25
    assert quiet["score"] == 50.0
    assert busy["score"] < quiet["score"]            # activity is taxed
    assert quiet["hygiene"] >= quiet["value"]        # hygiene is the optimization target


def test_value_is_gated_and_capped():
    # pnl_saved does NOT count without a logged counterfactual
    no_cf = compute_score(OperatorMetrics(session_date="d", pnl_saved_est=10_000))
    assert no_cf["value"] == 0.0
    with_cf = compute_score(OperatorMetrics(session_date="d", pnl_saved_est=10_000,
                                            counterfactual_logged=True))
    assert with_cf["value"] == 60.0   # capped at value_cap, can't dominate hygiene


def test_beat_your_own_best(tmp_path, monkeypatch):
    monkeypatch.setattr("src.ops.operator_scorecard._LEDGER", tmp_path / "lb.jsonl")
    record_session(OperatorMetrics(session_date="2026-06-01", correct_noops=2))   # 50
    record_session(OperatorMetrics(session_date="2026-06-02", correct_noops=3))   # 75
    s = standings("2026-06-02")
    assert s["today"] == 75.0 and s["prior_best"] == 50.0 and s["beat_best"] is True
