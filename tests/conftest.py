"""doc 272 A2 — global test isolation for the incident bus.

WHY THIS EXISTS: detectors now emit_incident() at fire time (recon daemon QTY_GHOST /
RECON_LETHAL, D313 HEDGE_VIOLATION, circuit-breaker trips, plus the pre-existing bridge
NAKED_POSITION_RISK). Unit tests deliberately exercise those exact code paths, so without
isolation every `pytest` run appends realistic-looking CRITICAL rows (AGPU, PENNY, ...)
to the REAL data/ops/incidents_<today>.jsonl — which the doc-272 incident pager would
then PAGE and pipeline_health_check would count as a red CRIT. Observed live while
building doc 272: one unit-suite run wrote 16 fake incidents.

The fixture redirects the bus's module-level paths to pytest's tmp_path for EVERY test.
Tests that patch incident_bus._OPS_DIR themselves (test_ops_foundation) still work —
their later monkeypatch simply wins. Tests that want the bus disabled set
OPS_INCIDENT_BUS_ENABLED=false as before.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_incident_bus(tmp_path, monkeypatch):
    from src.ops import incident_bus

    bus_dir = tmp_path / "_isolated_ops_bus"
    monkeypatch.setattr(incident_bus, "_OPS_DIR", bus_dir)
    monkeypatch.setattr(incident_bus, "_WAKE", bus_dir / "WAKE")
    # fresh in-process dedup per test so cross-test order can't suppress emits
    incident_bus._recent.clear()
    yield


@pytest.fixture(autouse=True)
def _isolate_verdict_ledger(tmp_path, monkeypatch):
    """Same hazard as the incident bus, one module over.

    vll_emit() appends to data/ops/verdict_trace_<today>.jsonl using a module-level
    _DIR. The bus was isolated; the ledger was not, so every suite run wrote real-
    looking lifecycle rows into the production ops directory. Evidence found in the
    working tree: 23 rows reading

        "'>' not supported between instances of 'MagicMock' and 'int'"

    plus orders named order-001 and auud-test-1 - unmistakably test artifacts sitting
    in operational data that pipeline_health_check and the operator both read.
    """
    from src.ops import verdict_ledger

    monkeypatch.setattr(verdict_ledger, "_DIR", str(tmp_path / "_isolated_vll"))
    yield


@pytest.fixture(autouse=True)
def _reset_circuit_breakers():
    """Reset the module-level circuit breakers between tests.

    `src/utils/circuit_breaker.py` exposes three process-wide singletons
    (`alpaca_breaker`, `llm_breaker`, `news_breaker`). A test that deliberately
    trips one leaves it OPEN for every test that follows in the same process,
    and the symptom is silent: the affected code takes its *degraded* path
    rather than raising. That is how `test_mixed_headlines_filters_and_calls_llm`
    came to fail only in a full-suite run — the LLM call was short-circuited by
    an already-open breaker and the FinBERT backstop answered BULL where the
    test expected the LLM's STRONG_BULL.

    Restoring the constructor's state is enough; there is no public reset, and
    adding one to production code for a test's benefit would be the wrong trade.
    """
    from src.utils import circuit_breaker as _cb

    breakers = [
        getattr(_cb, n)
        for n in ("alpaca_breaker", "llm_breaker", "news_breaker")
        if hasattr(_cb, n)
    ]

    def _close_all():
        for b in breakers:
            b._state = b.CLOSED
            b._fail_count = 0
            b._last_failure_time = 0.0
            b._current_reset_timeout = b._base_reset_timeout

    _close_all()
    yield
    _close_all()
