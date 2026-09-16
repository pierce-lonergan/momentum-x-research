"""Integration tests for the three EOD-pipeline modules shipped together:

  - src/analysis/cohort_eod_backfill.py
  - src/analysis/bayesian_eod_runner.py
  - src/testing/replay_engine.py

These exercise the wiring against synthetic Parquet partitions that mimic
the Phase 0 schemas — proves the wiring works TODAY, before Phase 0
captures real data Monday.

11 tests across 3 categories.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest


# ── Fixtures ─────────────────────────────────────────────────


@pytest.fixture
def synthetic_phase0(tmp_path: Path) -> tuple[Path, str]:
    """Build a synthetic Phase 0 capture covering 5 positions with
    fills + bars + cohort matches. Returns (base_dir, session_date)."""
    base = tmp_path / "instrumentation"
    sd = "2026-04-25"
    t0 = datetime(2026, 4, 25, 14, 30, tzinfo=timezone.utc)

    # trade_context — submits + terminals
    trade_rows = []
    for i in range(5):
        oid = f"oid-{i}"
        trade_rows.append({
            "schema_version": 1, "order_id": oid, "ticker": f"TKR{i}",
            "side": "buy", "requested_qty": 100, "requested_px": 5.0 + i,
            "submit_ts": (t0 + timedelta(seconds=i)).isoformat(),
            "terminal_status": "pending", "terminal_filled_qty": 0,
        })
        trade_rows.append({
            "schema_version": 1, "order_id": oid, "ticker": f"TKR{i}",
            "side": "buy", "requested_qty": 100, "requested_px": 5.0 + i,
            "submit_ts": (t0 + timedelta(seconds=i)).isoformat(),
            "terminal_ts": (t0 + timedelta(seconds=i + 30)).isoformat(),
            "terminal_status": "filled", "terminal_filled_qty": 100,
        })
    tc_dir = base / "trade_context" / f"session_date={sd}"
    tc_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(trade_rows).to_parquet(tc_dir / "orders.parquet", index=False)

    # child_fill_ticks — 50 fills total (10 per position) for n>=30 floor
    fill_rows = []
    for i in range(5):
        oid = f"oid-{i}"
        for j in range(10):
            fill_px = (5.0 + i) * (1.0 + 0.001 * j)  # tiny upward slip
            fill_rows.append({
                "schema_version": 1, "parent_order_id": oid,
                "child_fill_ts": (t0 + timedelta(seconds=10 + i + j)).isoformat(),
                "qty": 10, "price": fill_px,
                "cumulative_filled_qty": 10 * (j + 1),
            })
    cf_dir = base / "child_fill_ticks" / f"session_date={sd}"
    cf_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(fill_rows).to_parquet(cf_dir / "fills.parquet", index=False)

    # bar_context — one per position
    bar_rows = []
    for i in range(5):
        bar_rows.append({
            "schema_version": 1, "position_id": f"oid-{i}", "ticker": f"TKR{i}",
            "entry_ts": (t0 + timedelta(seconds=i + 30)).isoformat(),
            "entry_bar_open_ts": t0.isoformat(),
            "entry_bar_open": 5.0 + i, "entry_bar_high": 5.5 + i,
            "entry_bar_low": 4.5 + i, "entry_bar_close": 5.2 + i,
            "entry_bar_volume": 5000, "our_q_shares": 100,
            "q_over_v_tau": 100 / 5000.0,  # 0.02
            "bar_data_quality": "complete",
        })
    bc_dir = base / "bar_context" / f"session_date={sd}"
    bc_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(bar_rows).to_parquet(bc_dir / "entries.parquet", index=False)

    # cohort_registry — 2 peers per position
    cohort_rows = []
    for i in range(5):
        for k in range(2):
            cohort_rows.append({
                "schema_version": 1, "cohort_id": f"cohort-{i}-{k}",
                "traded_ticker": f"TKR{i}", "cohort_ticker": f"PEER{i}{k}",
                "match_dt": (t0 + timedelta(seconds=i + 30)).isoformat(),
                "match_features": {"catalyst_type": "earnings_beat"},
                "cohort_entry_ref_px": 10.0 + k,
                "cohort_eod_px": None, "cohort_60min_px": None,
                "cohort_signed_return_60min": None,
                "cohort_data_quality": "partial",
            })
    co_dir = base / "cohort_registry" / f"session_date={sd}"
    co_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(cohort_rows).to_parquet(co_dir / "cohorts.parquet", index=False)

    return base, sd


# ── Cohort EOD backfill ─────────────────────────────────────


class TestCohortEodBackfill:

    @pytest.mark.asyncio
    async def test_backfill_no_partition_returns_no_partition_error(
        self, tmp_path: Path,
    ) -> None:
        from src.analysis.cohort_eod_backfill import run_eod_cohort_backfill
        result = await run_eod_cohort_backfill(
            client=None, base_dir=tmp_path / "missing", session_date="2026-04-25",
        )
        assert result["error"] == "no_partition"

    @pytest.mark.asyncio
    async def test_backfill_no_client_skips_gracefully(
        self, synthetic_phase0,
    ) -> None:
        from src.analysis.cohort_eod_backfill import run_eod_cohort_backfill
        base, sd = synthetic_phase0
        result = await run_eod_cohort_backfill(
            client=None, base_dir=base, session_date=sd,
        )
        assert result["error"] == "no_client"

    @pytest.mark.asyncio
    async def test_backfill_with_synthetic_client_populates_fields(
        self, synthetic_phase0,
    ) -> None:
        from src.analysis.cohort_eod_backfill import run_eod_cohort_backfill

        class _Client:
            async def get_latest_quote(self, sym: str) -> dict:
                return {"bid": 11.0, "ask": 11.10}  # mid = 11.05
            async def get_bars(self, sym, start, end, timeframe):
                return [
                    {"t": "2026-04-25T15:30:00+00:00", "c": 11.20},
                ]

        base, sd = synthetic_phase0
        result = await run_eod_cohort_backfill(
            client=_Client(), base_dir=base, session_date=sd,
        )
        assert result["error"] is None
        r = result["result"]
        assert r["rows_total"] == 10  # 5 positions × 2 peers
        assert r["eod_px_filled"] == 10
        # Read back + verify the populated columns
        df = pd.read_parquet(base / "cohort_registry" / f"session_date={sd}" / "cohorts.parquet")
        assert df["cohort_eod_px"].notna().all()
        assert (df["cohort_data_quality"] == "complete").any()


# ── Bayesian EOD runner ────────────────────────────────────


class TestBayesianEodRunner:

    def test_no_phase0_data_returns_no_data_error(
        self, tmp_path: Path,
    ) -> None:
        from src.analysis.bayesian_eod_runner import run_eod_bayesian_fit
        result = run_eod_bayesian_fit(
            base_dir=tmp_path / "missing", session_date="2026-04-25",
            report_dir=tmp_path / "reports",
        )
        assert result["error"] == "no_phase0_data"

    def test_below_min_observations_skips(self, tmp_path: Path) -> None:
        from src.analysis.bayesian_eod_runner import run_eod_bayesian_fit
        # Build a synthetic capture with only 5 fills (below floor of 30)
        base = tmp_path / "instrumentation"
        sd = "2026-04-25"
        for schema, fname, rows in [
            ("child_fill_ticks", "fills.parquet", [
                {"parent_order_id": "p", "qty": 1, "price": 1.0,
                 "cumulative_filled_qty": 1} for _ in range(5)
            ]),
            ("bar_context", "entries.parquet", [
                {"position_id": "p", "q_over_v_tau": 0.02, "entry_bar_open": 1.0},
            ]),
        ]:
            d = base / schema / f"session_date={sd}"
            d.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(rows).to_parquet(d / fname, index=False)
        result = run_eod_bayesian_fit(
            base_dir=base, session_date=sd, report_dir=tmp_path / "reports",
            min_observations=30,
        )
        assert result["error"] == "below_min_observations"
        assert result["n_observations"] == 5

    def test_build_q_slippage_tuples_handles_missing_data(self) -> None:
        """Edge case: empty dataframes → empty tuples."""
        from src.analysis.bayesian_eod_runner import build_q_slippage_tuples
        assert build_q_slippage_tuples(None, None) == []
        empty = pd.DataFrame()
        assert build_q_slippage_tuples(empty, empty) == []


# ── Differential replay engine ─────────────────────────────


class TestReplayEngine:

    def test_replay_input_from_phase0_emits_all_op_types(
        self, synthetic_phase0,
    ) -> None:
        from src.testing.replay_engine import replay_input_from_phase0
        base, sd = synthetic_phase0
        ri = replay_input_from_phase0(base, sd)
        op_names = {op.name for op in ri.operations}
        assert "submit_order" in op_names
        assert "child_fill" in op_names
        assert "add_position" in op_names
        assert "cohort_match" in op_names
        assert "terminal_fill" in op_names
        # Check counts
        assert sum(1 for op in ri.operations if op.name == "submit_order") == 5
        assert sum(1 for op in ri.operations if op.name == "child_fill") == 50
        assert sum(1 for op in ri.operations if op.name == "add_position") == 5
        assert sum(1 for op in ri.operations if op.name == "cohort_match") == 10
        assert sum(1 for op in ri.operations if op.name == "terminal_fill") == 5

    def test_replay_input_empty_when_no_partitions(
        self, tmp_path: Path,
    ) -> None:
        from src.testing.replay_engine import replay_input_from_phase0
        ri = replay_input_from_phase0(tmp_path / "missing", "2026-04-25")
        assert ri.operations == ()

    def test_build_inprocess_variant_dispatches_to_handlers(self) -> None:
        from src.testing.replay_engine import build_inprocess_variant
        handlers = {
            "submit_order": lambda **kw: f"submitted {kw.get('ticker', '?')}",
            "child_fill": lambda **kw: f"filled {kw.get('qty', 0)}",
        }
        variant = build_inprocess_variant(handlers)
        assert variant("submit_order", ticker="AAPL") == "submitted AAPL"
        assert variant("child_fill", qty=100) == "filled 100"
        with pytest.raises(KeyError, match="no handler for op"):
            variant("unknown_op")

    def test_replay_engine_integrates_with_differential_harness(
        self, synthetic_phase0,
    ) -> None:
        """Full integration: load synthetic Phase 0 → build two variants
        with one differing handler → expect DivergenceReport at the
        differing op."""
        from src.testing import DifferentialHarness
        from src.testing.replay_engine import (
            build_inprocess_variant,
            replay_input_from_phase0,
        )
        base, sd = synthetic_phase0
        ri = replay_input_from_phase0(base, sd)

        # Variant A: returns the qty as-is
        variant_a = build_inprocess_variant({
            "submit_order": lambda **kw: kw.get("requested_qty", 0),
            "child_fill": lambda **kw: kw.get("qty", 0),
            "add_position": lambda **kw: kw.get("our_q_shares", 0),
            "cohort_match": lambda **kw: kw.get("traded_ticker", ""),
            "terminal_fill": lambda **kw: kw.get("terminal_filled_qty", 0),
        })
        # Variant B: adds 1 to every qty (simulates a buggy patch that drifts)
        variant_b = build_inprocess_variant({
            "submit_order": lambda **kw: kw.get("requested_qty", 0) + 1,
            "child_fill": lambda **kw: kw.get("qty", 0) + 1,
            "add_position": lambda **kw: kw.get("our_q_shares", 0) + 1,
            "cohort_match": lambda **kw: kw.get("traded_ticker", ""),  # same!
            "terminal_fill": lambda **kw: kw.get("terminal_filled_qty", 0) + 1,
        })
        harness = DifferentialHarness(variant_a, variant_b)
        report = harness.compare(*harness.replay(ri))
        # All ops except cohort_match should diverge: 5 + 50 + 5 + 5 = 65
        assert report.total_ops == len(ri.operations)
        assert len(report.divergences) == 65
        # cohort_match is allowlistable as the documented intentional path
        assert all(d.operation.name != "cohort_match" for d in report.divergences)
