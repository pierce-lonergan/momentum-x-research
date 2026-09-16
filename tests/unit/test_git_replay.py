"""Tests for src/testing/git_replay.py — the per-SHA replay orchestrator.

Covers the pieces that don't require a real git checkout:
  - Records JSON round-trip (write via driver-shape JSON, read back as
    OperationRecord tuple suitable for DifferentialHarness.compare)
  - identity_handlers() reference handler shape
  - Subprocess driver integration via a tmp Phase 0 partition

8 tests, all fast (no actual subprocess git operations — those are
exercised via the integration smoke test which is gated behind
`-m subprocess` if added).
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from src.testing.differential_harness import (
    DifferentialHarness,
    OperationCall,
    OperationRecord,
)
from src.testing.git_replay import (
    GitReplayResult,
    _load_records,
    identity_handlers,
)


# ── Records JSON round-trip ────────────────────────────────────


class TestRecordsLoad:

    def test_load_records_handles_basic_results(self, tmp_path: Path) -> None:
        path = tmp_path / "records.json"
        rows = [
            {"name": "submit", "args": ["X"], "kwargs": {"qty": 100},
             "result": "100", "exception": None},
            {"name": "fill", "args": [], "kwargs": {"px": 5.5},
             "result": "5.5", "exception": None},
        ]
        path.write_text(json.dumps(rows), encoding="utf-8")
        records = _load_records(path)
        assert len(records) == 2
        assert records[0].call.name == "submit"
        assert records[0].call.args == ("X",)
        assert dict(records[0].call.kwargs) == {"qty": 100}
        assert records[0].result == "100"
        assert records[0].exception is None

    def test_load_records_handles_exception_records(self, tmp_path: Path) -> None:
        path = tmp_path / "records.json"
        rows = [
            {"name": "boom", "args": [], "kwargs": {},
             "result": None, "exception": ["RuntimeError", "ouch"]},
        ]
        path.write_text(json.dumps(rows), encoding="utf-8")
        records = _load_records(path)
        assert len(records) == 1
        assert records[0].exception == ("RuntimeError", "ouch")
        assert records[0].result is None
        assert records[0].raised is True

    def test_load_records_kwargs_become_sorted_tuple(self, tmp_path: Path) -> None:
        """OperationCall.kwargs is a sorted tuple for hashability."""
        path = tmp_path / "records.json"
        rows = [
            {"name": "x", "args": [], "kwargs": {"z": 1, "a": 2, "m": 3},
             "result": "ok", "exception": None},
        ]
        path.write_text(json.dumps(rows), encoding="utf-8")
        records = _load_records(path)
        # Sorted alphabetically
        assert records[0].call.kwargs == (("a", 2), ("m", 3), ("z", 1))

    def test_round_trip_compatible_with_differential_harness_compare(
        self, tmp_path: Path,
    ) -> None:
        """Two identical records files compared by DifferentialHarness should
        report zero divergence."""
        rows = [
            {"name": "op1", "args": [1], "kwargs": {"k": "v"},
             "result": "result1", "exception": None},
            {"name": "op2", "args": [], "kwargs": {},
             "result": None, "exception": ["ValueError", "bad"]},
        ]
        path_a = tmp_path / "a.json"
        path_b = tmp_path / "b.json"
        path_a.write_text(json.dumps(rows), encoding="utf-8")
        path_b.write_text(json.dumps(rows), encoding="utf-8")
        records_a = _load_records(path_a)
        records_b = _load_records(path_b)
        harness = DifferentialHarness(
            variant_a=lambda *a, **k: None,
            variant_b=lambda *a, **k: None,
        )
        report = harness.compare(records_a, records_b)
        assert report.passed
        assert len(report.divergences) == 0


# ── Identity handlers reference ────────────────────────────────


class TestIdentityHandlers:

    def test_identity_handlers_dispatches_all_op_types(self) -> None:
        h = identity_handlers()
        for op_name in (
            "submit_order", "child_fill", "add_position",
            "cohort_match", "terminal_fill",
        ):
            assert op_name in h
            # Each handler returns first kwarg value
            assert h[op_name](ticker="AAPL") == "AAPL"
            assert h[op_name](qty=42) == 42

    def test_identity_handler_with_no_kwargs_returns_none(self) -> None:
        h = identity_handlers()
        assert h["submit_order"]() is None


# ── GitReplayResult dataclass ──────────────────────────────────


class TestGitReplayResult:

    def test_dataclass_construction(self) -> None:
        result = GitReplayResult(
            base_sha="abc", head_sha="def",
            divergence_report=None,
            error="test error",
            base_records_path=None,
            head_records_path=None,
        )
        assert result.base_sha == "abc"
        assert result.error == "test error"
        assert result.divergence_report is None


# ── Subprocess driver integration (no git, just spawn replay) ──


class TestSubprocessDriverIntegration:

    def test_subprocess_replay_against_synthetic_phase0(
        self, tmp_path: Path,
    ) -> None:
        """Drive the subprocess replay against a synthetic Phase 0 capture.
        Doesn't checkout SHAs — just verifies the driver wires up correctly."""
        from src.testing.git_replay import _run_subprocess_replay

        # Build a minimal synthetic Phase 0 capture
        base = tmp_path / "instr"
        sd = "2026-04-26"
        for schema, fname, rows in [
            ("trade_context", "orders.parquet", [{
                "schema_version": 1, "order_id": "o1", "ticker": "AAPL",
                "side": "buy", "requested_qty": 10, "requested_px": 5.0,
                "submit_ts": "2026-04-26T12:00:00+00:00",
                "terminal_status": "pending", "terminal_filled_qty": 0,
            }]),
            ("child_fill_ticks", "fills.parquet", [{
                "schema_version": 1, "parent_order_id": "o1",
                "child_fill_ts": "2026-04-26T12:00:01+00:00",
                "qty": 10, "price": 5.0, "cumulative_filled_qty": 10,
            }]),
            ("bar_context", "entries.parquet", []),
            ("cohort_registry", "cohorts.parquet", []),
        ]:
            d = base / schema / f"session_date={sd}"
            d.mkdir(parents=True, exist_ok=True)
            if rows:
                pd.DataFrame(rows).to_parquet(d / fname, index=False)
            else:
                pd.DataFrame(columns=["schema_version"]).to_parquet(d / fname, index=False)

        output_path = tmp_path / "records.json"
        repo_root = Path.cwd()
        ok, msg = _run_subprocess_replay(
            repo_root=repo_root,
            base_dir=str(base), session_date=sd,
            handler_module="src.testing.git_replay.identity_handlers",
            output_path=output_path,
        )
        assert ok, f"subprocess replay failed: {msg}"
        assert output_path.exists()

        # Read back and verify shape
        records = _load_records(output_path)
        # Should have at least 1 op (submit_order from trade_context)
        assert len(records) >= 1
        names = {r.call.name for r in records}
        assert "submit_order" in names
