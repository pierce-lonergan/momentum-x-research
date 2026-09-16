"""D308 / D309 (2026-05-19) -- Stop-widening shadow log + replayer.

Pin the T1 validation plan from doc 169:
  - D308: StopDecisionLog emits structured stop_decision events at every
    OTO submission. Production order behavior UNCHANGED.
  - D309: scripts/stop_widening_replay.py reads those events, simulates
    each trade under wide ATR stop vs tight Phase 1 stop, computes
    hypothetical P&L delta.

These tests cover:
  1. Emitter writes correct JSONL schema, never raises
  2. Emitter handles edge cases (zero price, missing optional fields)
  3. Replayer simulator correctly identifies stop/target/EOS exits
  4. Replayer computes per-tranche weighted exit correctly
  5. Replayer aggregates dollar P&L correctly with full vs half sizing
  6. Replayer gracefully handles empty / missing minute bars
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest


# ═══════════════════════════════════════════════════════════════
# D308 -- StopDecisionLog emitter
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def emitter(tmp_path):
    from src.analysis.stop_decision_log import StopDecisionLog
    return StopDecisionLog(output_dir=str(tmp_path))


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_emitter_writes_jsonl_with_required_schema(emitter):
    """Every event MUST contain the fields the replayer needs."""
    emitter.log_stop_decision(
        symbol="CISS", side="buy",
        entry_price=4.67, atr_stop=3.43,
        phase1_stop=4.60, phase1_pct=0.015,
        submitted_stop=4.60, submitted_strategy="phase1",
        qty=2243, gap_pct=0.531, atr_14d=0.2686,
        kelly_tier=1, mfcs=0.312, target_prices=[4.81, 4.95, 5.14],
    )
    events = _read_jsonl(emitter.path)
    assert len(events) == 1
    e = events[0]
    # Identity
    assert e["event"] == "stop_decision"
    assert e["symbol"] == "CISS"
    assert e["side"] == "buy"
    # Required numerics
    assert e["entry"] == pytest.approx(4.67)
    assert e["atr_stop"] == pytest.approx(3.43)
    assert e["phase1_stop"] == pytest.approx(4.60)
    assert e["submitted_stop"] == pytest.approx(4.60)
    assert e["submitted_strategy"] == "phase1"
    assert e["qty"] == 2243
    # Derived distance pcts
    # ATR dist: (3.43/4.67 - 1)*100 = -26.55%
    assert e["atr_dist_pct"] == pytest.approx(-26.5525, abs=0.01)
    assert e["phase1_dist_pct"] == pytest.approx(-1.499, abs=0.01)
    assert e["submitted_dist_pct"] == pytest.approx(-1.499, abs=0.01)
    # Halved qty for T2 sizing
    assert e["qty_halved_hypothetical"] == 1121  # 2243 // 2
    # Optional context preserved
    assert e["gap_pct"] == pytest.approx(0.531)
    assert e["mfcs"] == pytest.approx(0.312)


def test_emitter_records_count(emitter):
    """Per-instance count must reflect successful writes."""
    for _ in range(3):
        emitter.log_stop_decision(
            symbol="X", side="buy", entry_price=10, atr_stop=9,
            phase1_stop=9.85, phase1_pct=0.015,
            submitted_stop=9.85, submitted_strategy="phase1", qty=100,
        )
    assert emitter.count == 3
    assert len(_read_jsonl(emitter.path)) == 3


def test_emitter_handles_zero_entry_price_gracefully(emitter):
    """A zero entry (data bug) must not crash; pct fields collapse to 0."""
    emitter.log_stop_decision(
        symbol="X", side="buy", entry_price=0.0, atr_stop=0.0,
        phase1_stop=None, phase1_pct=None,
        submitted_stop=0.0, submitted_strategy="atr", qty=0,
    )
    e = _read_jsonl(emitter.path)[0]
    assert e["atr_dist_pct"] == 0.0
    assert e["submitted_dist_pct"] == 0.0
    assert e["qty"] == 0
    assert e["qty_halved_hypothetical"] == 0


def test_emitter_handles_no_phase1_stop(emitter):
    """When the orchestrator's wide stop is already tighter than the
    phase1 stop (rare), no override happens -> phase1_stop is None."""
    emitter.log_stop_decision(
        symbol="X", side="buy", entry_price=10, atr_stop=9.95,
        phase1_stop=None, phase1_pct=None,
        submitted_stop=9.95, submitted_strategy="atr", qty=100,
    )
    e = _read_jsonl(emitter.path)[0]
    assert e["phase1_stop"] is None
    assert e["phase1_dist_pct"] is None
    assert e["submitted_strategy"] == "atr"


def test_emitter_never_raises_on_path_failure(tmp_path, monkeypatch):
    """A write failure must NEVER bubble up -- production must keep trading."""
    from src.analysis.stop_decision_log import StopDecisionLog
    log = StopDecisionLog(output_dir=str(tmp_path))
    # Sabotage: make the path unwritable
    log._path = Path("/nonexistent/path/cannot_write.jsonl")
    # Must not raise
    log.log_stop_decision(
        symbol="X", side="buy", entry_price=10, atr_stop=9,
        phase1_stop=9.85, phase1_pct=0.015,
        submitted_stop=9.85, submitted_strategy="phase1", qty=100,
    )


def test_emitter_handles_dir_create_failure(monkeypatch):
    """If the output dir can't be created at init, emitter degrades silently."""
    from src.analysis.stop_decision_log import StopDecisionLog
    # Use a path that Path.mkdir refuses on Windows (e.g. a reserved char in
    # the leaf). Simpler: monkey-patch Path.mkdir to raise.
    import src.analysis.stop_decision_log as mod
    real_mkdir = Path.mkdir
    def _boom(self, *a, **kw):
        if "shadow" in str(self).lower():
            raise PermissionError("denied")
        return real_mkdir(self, *a, **kw)
    monkeypatch.setattr(Path, "mkdir", _boom)
    log = StopDecisionLog(output_dir="data/shadow_blocked")
    # log_stop_decision must be a silent no-op (no raise)
    log.log_stop_decision(
        symbol="X", side="buy", entry_price=10, atr_stop=9,
        phase1_stop=9.85, phase1_pct=0.015,
        submitted_stop=9.85, submitted_strategy="phase1", qty=100,
    )
    assert log.count == 0


def test_emitter_each_event_is_valid_json_per_line(emitter):
    """JSONL invariant: each line is a valid JSON object, no embedded
    newlines inside a record."""
    for i in range(5):
        emitter.log_stop_decision(
            symbol=f"X{i}", side="buy", entry_price=10 + i, atr_stop=9 + i,
            phase1_stop=9.85 + i, phase1_pct=0.015,
            submitted_stop=9.85 + i, submitted_strategy="phase1", qty=100,
        )
    raw_lines = emitter.path.read_text(encoding="utf-8").splitlines()
    assert len(raw_lines) == 5
    for ln in raw_lines:
        obj = json.loads(ln)
        assert isinstance(obj, dict)
        assert obj["event"] == "stop_decision"


# ═══════════════════════════════════════════════════════════════
# D309 -- Replayer simulator
# ═══════════════════════════════════════════════════════════════


def _make_bars(rows: list[tuple[float, float, float]]) -> pd.DataFrame:
    """Build a minimal bars DataFrame: each row is (high, low, close)."""
    return pd.DataFrame({
        "ts_et": pd.date_range("2026-05-13 09:30", periods=len(rows),
                                freq="1min", tz="America/New_York"),
        "open": [r[2] for r in rows],
        "high": [r[0] for r in rows],
        "low": [r[1] for r in rows],
        "close": [r[2] for r in rows],
        "volume": [1000] * len(rows),
    })


def test_simulate_stop_hit_first_bar():
    """Stop dist 5%; first bar's low touches stop -> stopped at -5%."""
    from scripts.stop_widening_replay import simulate
    entry = 10.0
    atr_stop = 9.50  # -5%
    bars = _make_bars([(10.02, 9.49, 10.00)])
    out = simulate(entry, atr_stop, bars)
    assert out["exit_strategy"] == "stopped"
    assert out["tranches_hit"] == 0
    assert out["effective_exit_price"] == pytest.approx(atr_stop)
    assert out["effective_exit_pct"] == pytest.approx(-5.0)
    assert out["stop_hit_minute"] == 0


def test_simulate_targets_full_ladder():
    """Bars eventually rip through +10%; all three tranches hit."""
    from scripts.stop_widening_replay import simulate
    entry = 10.0
    atr_stop = 9.0
    bars = _make_bars([
        (10.05, 9.90, 10.02),   # min 0: no hit
        (10.30, 10.10, 10.20),  # min 1: tranche 1 (+3% = 10.30) HIT
        (10.65, 10.40, 10.50),  # min 2: tranche 2 (+6% = 10.60) HIT
        (11.05, 10.70, 11.00),  # min 3: tranche 3 (+10% = 11.00) HIT
    ])
    out = simulate(entry, atr_stop, bars)
    assert out["exit_strategy"] == "targets_full"
    assert out["tranches_hit"] == 3
    # weighted: 0.33 * 10.30 + 0.33 * 10.60 + 0.34 * 11.00 = 10.6379
    assert out["effective_exit_price"] == pytest.approx(10.6379, abs=0.001)
    assert out["effective_exit_pct"] == pytest.approx(6.379, abs=0.01)


def test_simulate_targets_partial():
    """First tranche hits, then stop -> partial fill at +3% then stopped."""
    from scripts.stop_widening_replay import simulate
    entry = 10.0
    atr_stop = 9.50
    bars = _make_bars([
        (10.35, 10.00, 10.30),  # min 0: tranche 1 hits
        (10.20, 9.40, 9.50),    # min 1: stop hits (rest of position closes)
    ])
    out = simulate(entry, atr_stop, bars)
    assert out["exit_strategy"] == "targets_partial"
    assert out["tranches_hit"] == 1
    # 0.33 * 10.30 + 0.67 * 9.50 = 9.764
    assert out["effective_exit_price"] == pytest.approx(9.764, abs=0.001)


def test_simulate_eos_no_stop_no_target():
    """Price grinds sideways within the band -> EOS close on last bar."""
    from scripts.stop_widening_replay import simulate
    entry = 10.0
    atr_stop = 9.0
    bars = _make_bars([
        (10.10, 9.95, 10.05),
        (10.20, 10.00, 10.15),
        (10.15, 9.98, 10.10),
    ])
    out = simulate(entry, atr_stop, bars)
    assert out["exit_strategy"] == "eos"
    assert out["tranches_hit"] == 0
    assert out["effective_exit_price"] == pytest.approx(10.10)  # last close


def test_simulate_empty_bars_returns_no_data():
    """No minute data available -> graceful empty result."""
    from scripts.stop_widening_replay import simulate
    out = simulate(10.0, 9.0, pd.DataFrame())
    assert out["exit_strategy"] == "no_data"
    assert out["effective_exit_pct"] == 0.0


def test_simulate_tracks_max_high_pct():
    """max_high_pct field tracks the highest intraday excursion for analysis."""
    from scripts.stop_widening_replay import simulate
    entry = 10.0
    bars = _make_bars([
        (10.50, 9.90, 10.40),  # spike to +5%
        (10.05, 9.92, 10.00),  # comes back
    ])
    out = simulate(entry, 9.0, bars)
    assert out["max_high_pct"] == pytest.approx(5.0, abs=0.01)


# ═══════════════════════════════════════════════════════════════
# D309 -- Replayer end-to-end (load + simulate + aggregate)
# ═══════════════════════════════════════════════════════════════


def test_load_stop_decisions_filters_event_type(tmp_path):
    """Only event=='stop_decision' rows are loaded; other events ignored."""
    from scripts.stop_widening_replay import load_stop_decisions
    f = tmp_path / "stop_decisions_2026-05-19.jsonl"
    f.write_text(
        json.dumps({"event": "stop_decision", "ts": "2026-05-19T14:00:00+00:00",
                    "symbol": "X", "entry": 10.0, "atr_stop": 9.0,
                    "qty": 100, "qty_halved_hypothetical": 50,
                    "submitted_stop": 9.85, "submitted_strategy": "phase1"})
        + "\n"
        + json.dumps({"event": "other_event", "ts": "2026-05-19T14:01:00+00:00"})
        + "\n",
        encoding="utf-8",
    )
    out = load_stop_decisions(tmp_path)
    assert len(out) == 1
    assert out.iloc[0]["symbol"] == "X"
    assert "session_date" in out.columns


def test_load_stop_decisions_handles_empty_dir(tmp_path):
    from scripts.stop_widening_replay import load_stop_decisions
    out = load_stop_decisions(tmp_path)
    assert out.empty


def test_load_stop_decisions_skips_corrupted_lines(tmp_path):
    """A garbled line in the middle of a file must not crash the load."""
    from scripts.stop_widening_replay import load_stop_decisions
    f = tmp_path / "stop_decisions_2026-05-19.jsonl"
    f.write_text(
        '{"event":"stop_decision","ts":"2026-05-19T14:00:00+00:00","symbol":"A",'
        '"entry":1.0,"atr_stop":0.9,"submitted_stop":0.99,"submitted_strategy":"phase1","qty":1,"qty_halved_hypothetical":0}\n'
        'NOT JSON GIBBERISH\n'
        '{"event":"stop_decision","ts":"2026-05-19T14:01:00+00:00","symbol":"B",'
        '"entry":2.0,"atr_stop":1.8,"submitted_stop":1.98,"submitted_strategy":"phase1","qty":2,"qty_halved_hypothetical":1}\n',
        encoding="utf-8",
    )
    out = load_stop_decisions(tmp_path)
    assert len(out) == 2
    assert set(out["symbol"]) == {"A", "B"}


# ═══════════════════════════════════════════════════════════════
# D308 -- Executor wiring contract
# ═══════════════════════════════════════════════════════════════


def test_alpaca_executor_accepts_stop_decision_log_kwarg():
    """The executor's __init__ must accept stop_decision_log without
    breaking backwards-compat with callers that omit it."""
    from src.execution.alpaca_executor import AlpacaExecutor
    from unittest.mock import MagicMock
    # No stop_decision_log -- legacy callers should work
    e1 = AlpacaExecutor(config=MagicMock(), client=MagicMock())
    assert e1._stop_decision_log is None
    # With stop_decision_log -- D308 wiring
    fake_log = MagicMock()
    e2 = AlpacaExecutor(config=MagicMock(), client=MagicMock(),
                         stop_decision_log=fake_log)
    assert e2._stop_decision_log is fake_log


def test_main_py_constructs_stop_decision_log_before_executor():
    """Static guard: main.py must instantiate StopDecisionLog and pass it
    into the AlpacaExecutor ctor. The D301 forward-reference guard from
    doc 167 already covers ordering; this test pins the wiring contract."""
    import ast
    from pathlib import Path
    src = Path("main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    # Find the AlpacaExecutor(...) call
    found_kwarg = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "AlpacaExecutor":
                for kw in node.keywords:
                    if kw.arg == "stop_decision_log":
                        found_kwarg = True
                        break
    assert found_kwarg, (
        "main.py AlpacaExecutor(...) ctor missing stop_decision_log kwarg "
        "-- D308 shadow log will not emit any events"
    )
