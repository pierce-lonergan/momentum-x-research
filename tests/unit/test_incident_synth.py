"""doc 212: regression tests for incident_synth — the Operator's day-1 blind spot.

On 6/1 the Operator reported "0 incidents / correct no-op / score 25" while the broker had a
phantom (journal +$304 vs broker $0) and a ghost (qty_drift=1). incident_synth must catch
these from the EOD json so the Operator never goes blind to that class again.
"""
from __future__ import annotations

import json

from src.ops import incident_bus, incident_synth


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(incident_bus, "_OPS_DIR", tmp_path / "ops")
    monkeypatch.setattr(incident_bus, "_WAKE", tmp_path / "ops" / "WAKE")
    monkeypatch.setattr(incident_synth, "_ROOT", tmp_path)
    incident_bus._recent.clear()
    (tmp_path / "data" / "reports").mkdir(parents=True, exist_ok=True)


def _write_eod(tmp_path, *, delta, qty_drift, equity_ok=True):
    eod = {
        "session_date": "2026-06-01",
        "sections": {
            "eod_failsafes": {"broker_truth_recon": {
                "broker_total_pnl": 0.0, "journal_total_pnl": 304.2, "delta_usd": delta,
                "ticker_disagreements": [{"ticker": "STG", "journal": 304.2, "broker": 0.0}],
            }},
            "eod_recon": {"qty_drift_count": qty_drift, "equity_within_tolerance": equity_ok,
                          "broker_equity": 148879.58},
        },
    }
    (tmp_path / "data" / "reports" / "eod_2026-06-01.json").write_text(
        json.dumps(eod), encoding="utf-8")


def test_day1_phantom_and_ghost_are_detected(tmp_path, monkeypatch):
    """The exact 6/1 case: journal +304 vs broker 0 (phantom) + qty_drift=1 (ghost)."""
    _setup(tmp_path, monkeypatch)
    _write_eod(tmp_path, delta=-304.2, qty_drift=1, equity_ok=False)
    n = incident_synth._from_eod_report("2026-06-01")
    kinds = {r["kind"] for r in incident_bus.read_incidents("2026-06-01")}
    assert n == 2
    assert "PNL_RECON_DIVERGENCE" in kinds   # the phantom
    assert "QTY_DRIFT_GHOST" in kinds        # the ghost
    # both CRITICAL -> would drop a WAKE + fan to Discord
    assert all(r["severity"] == "CRITICAL"
               for r in incident_bus.read_incidents("2026-06-01"))


def test_clean_day_emits_nothing(tmp_path, monkeypatch):
    """A truly clean EOD (no delta, no drift, equity ok) -> 0 incidents (no false positives)."""
    _setup(tmp_path, monkeypatch)
    _write_eod(tmp_path, delta=0.0, qty_drift=0, equity_ok=True)
    n = incident_synth._from_eod_report("2026-06-01")
    assert n == 0


def test_pnl_within_tolerance_not_flagged(tmp_path, monkeypatch):
    """A sub-$1 delta is within tolerance -> not a phantom incident."""
    _setup(tmp_path, monkeypatch)
    _write_eod(tmp_path, delta=-0.5, qty_drift=0, equity_ok=True)
    assert incident_synth._from_eod_report("2026-06-01") == 0


def test_equity_out_of_tolerance_alone_is_warn(tmp_path, monkeypatch):
    """No drift but equity out of tolerance -> a WARN (not silent)."""
    _setup(tmp_path, monkeypatch)
    _write_eod(tmp_path, delta=0.0, qty_drift=0, equity_ok=False)
    n = incident_synth._from_eod_report("2026-06-01")
    rows = incident_bus.read_incidents("2026-06-01")
    assert n == 1 and rows[0]["kind"] == "EQUITY_OUT_OF_TOLERANCE"
    assert rows[0]["severity"] == "WARN"
