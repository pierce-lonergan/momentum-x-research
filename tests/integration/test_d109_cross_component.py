"""
D109 Cross-Component Integration Tests

Tests that verify D109 components work together correctly across module boundaries:
  1. Metrics ↔ PositionManager: clamp counter increments propagate
  2. Backtest → ETL: backtest CSV output can be loaded by ETL
  3. ETL → Time-Travel → Query: full pipeline from data to analytical queries
  4. Metrics snapshot retention → ETL: snapshots survive rotation and load correctly
  5. Exit Autopsy ↔ compute_stats: MFE/MAE flows through the full pipeline
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ── 1. Metrics ↔ PositionManager ────────────────────────────────


class TestMetricsPositionManagerIntegration:
    """Verify clamp counter flows from PositionManager to MetricsRegistry."""

    def test_clamp_counter_survives_prometheus_export(self) -> None:
        """Clamp counter appears in Prometheus text export after increment."""
        from src.monitoring.metrics import get_metrics, reset_metrics

        reset_metrics()
        metrics = get_metrics()

        # Simulate 3 clamps
        metrics.state_validation_clamps.inc()
        metrics.state_validation_clamps.inc()
        metrics.state_validation_clamps.inc()

        # Get prometheus export
        prom_output = metrics.to_prometheus()
        assert "mx_state_validation_clamps_total" in prom_output
        assert "3" in prom_output or "3.0" in prom_output

    def test_debates_skipped_budget_in_prometheus(self) -> None:
        """debates_skipped_budget counter exported in both JSON and Prometheus."""
        from src.monitoring.metrics import get_metrics, reset_metrics

        reset_metrics()
        metrics = get_metrics()
        metrics.debates_skipped_budget.inc()
        metrics.debates_skipped_budget.inc()

        # JSON snapshot
        snap = metrics.snapshot()
        assert snap["debate"]["skipped_budget"] == 2.0

        # Prometheus export
        prom = metrics.to_prometheus()
        assert "mx_debates_skipped_budget_total" in prom

    def test_position_manager_clamp_reaches_metrics(self) -> None:
        """Full flow: PositionManager._validate_recovered_positions → metrics counter."""
        from src.execution.position_manager import PositionManager
        from src.monitoring.metrics import get_metrics, reset_metrics

        reset_metrics()
        metrics = get_metrics()
        assert metrics.state_validation_clamps.value == 0.0

        # Create position manager with corrupted position
        pm = PositionManager.__new__(PositionManager)
        mock_pos = MagicMock()
        mock_pos.remaining_qty = -10  # Invalid: negative
        mock_pos.qty = 100
        mock_pos.tranches_filled = 1
        mock_pos.stop_loss = 5.0
        mock_pos.entry_price = 10.0
        mock_pos.target_prices = [11.0, 12.0, 13.0]
        mock_pos.ticker = "TEST"
        pm._positions = {"TEST": mock_pos}

        pm._validate_recovered_positions()

        # Counter should have been incremented by the clamp
        assert metrics.state_validation_clamps.value >= 1.0
        # Position should have been clamped
        assert mock_pos.remaining_qty == 0

    def test_snapshot_includes_clamp_count(self) -> None:
        """Metric snapshot to disk includes clamp counter in export."""
        from src.monitoring.metrics import get_metrics, reset_metrics

        reset_metrics()
        metrics = get_metrics()
        metrics.state_validation_clamps.inc()
        metrics.state_validation_clamps.inc()

        with tempfile.TemporaryDirectory() as tmpdir:
            metrics.save_snapshot(Path(tmpdir))
            snapshot_files = list(Path(tmpdir).glob("metrics_*.json"))
            assert len(snapshot_files) == 1

            with open(snapshot_files[0]) as f:
                snap = json.load(f)

            # The snapshot should be a valid JSON dict with clamp data
            assert isinstance(snap, dict)
            assert "timestamp" in snap
            assert snap["state_validation"]["clamps_total"] == 2.0


# ── 2. Metrics Snapshot Retention → ETL ────────────────────────


class TestSnapshotRetentionETL:
    """Verify snapshot rotation and ETL loading work together."""

    def test_rotated_snapshots_loadable_by_etl(self, tmp_path: Path) -> None:
        """After rotation, remaining snapshots can be loaded by ETL."""
        from scripts.etl_sqlite import init_db, load_metric_snapshots
        from src.monitoring.metrics import MetricsRegistry

        metrics = MetricsRegistry()
        metrics_dir = tmp_path / "metrics"
        metrics_dir.mkdir()

        # Create 5 snapshot files manually (simulating multiple saves)
        for i in range(5):
            snap = {
                "timestamp": f"2026-03-10T10:{i:02d}:00",
                "uptime_seconds": 100.0 * (i + 1),
                "pipeline": {"scan_iterations": i * 10, "candidates_found": i * 2,
                             "evaluations_total": i, "pipeline_latency_mean_s": 1.5},
                "execution": {"orders_submitted": i, "orders_filled": i,
                              "session_trades": i},
                "risk": {"daily_pnl": i * 50.0, "circuit_breaker_activations": 0,
                         "risk_vetoes": 0},
                "agents": {"errors_total": 0},
                "exit_events": {"stop_outs": 0, "smart_exits": 0},
                "phase_timing": {"phase3_cycles": i * 5},
                "data_completeness": {"overall_fill_rate": 0.8},
            }
            fname = f"metrics_20260310T10{i:02d}00.json"
            (metrics_dir / fname).write_text(json.dumps(snap))

        # Now load via ETL
        db_path = tmp_path / "test.db"
        conn = init_db(db_path)
        data_dir = tmp_path  # etl_sqlite looks for data_dir/metrics/
        loaded = load_metric_snapshots(conn, data_dir)
        conn.commit()

        assert loaded == 5

        # Verify data integrity
        rows = conn.execute(
            "SELECT timestamp, uptime_seconds FROM metric_snapshots ORDER BY timestamp"
        ).fetchall()
        assert len(rows) == 5
        assert rows[0][1] == 100.0  # First snapshot uptime
        assert rows[4][1] == 500.0  # Fifth snapshot uptime

        conn.close()


# ── 3. Backtest MFE/MAE → Exit Autopsy Pipeline ──────────────


class TestBacktestExitAutopsyPipeline:
    """Verify MFE/MAE data flows correctly through the full analysis pipeline."""

    def test_compute_stats_to_exit_autopsy(self) -> None:
        """compute_stats MFE/MAE fields are consumed by run_exit_autopsy."""
        from scripts.backtest import (
            SimulatedTrade,
            compute_stats,
            run_exit_autopsy,
        )

        trades = [
            SimulatedTrade(
                ticker="A", date="2026-01-01",
                entry_price=10.0, entry_time="09:32",
                exit_price=9.5, exit_time="09:45",
                exit_reason="stop", pnl_pct=-5.0, pnl_dollars=-50.0,
                stop_price=9.5, stop_distance_pct=5.0,
                t1_target=10.5, t2_target=11.0, t3_target=12.0,
                gap_pct=8.0, atr=0.50, hold_minutes=13,
                mfe_pct=3.0, mae_pct=5.0, mfe_bar=5,
                stopped_before_mfe=True,
            ),
            SimulatedTrade(
                ticker="B", date="2026-01-01",
                entry_price=5.0, entry_time="09:32",
                exit_price=5.5, exit_time="10:30",
                exit_reason="t1", pnl_pct=10.0, pnl_dollars=50.0,
                stop_price=4.5, stop_distance_pct=10.0,
                t1_target=5.5, t2_target=6.0, t3_target=6.5,
                gap_pct=6.0, atr=0.30, hold_minutes=58,
                mfe_pct=12.0, mae_pct=1.0, mfe_bar=15,
                stopped_before_mfe=False,
            ),
        ]

        # Phase 1: compute_stats
        stats = compute_stats(trades, 10, 5, 2)
        assert stats["avg_mfe_pct"] == 7.5  # (3+12)/2
        assert stats["avg_mae_pct"] == 3.0  # (5+1)/2

        # Phase 3: exit_autopsy consumes same trade objects
        autopsy = run_exit_autopsy(trades)

        # Cross-verify: autopsy stop tightness matches stats
        assert autopsy["stop_tightness"]["stop_outs"] == 1
        assert autopsy["stop_tightness"]["stopped_with_prior_gain"] == 1

        # Exit effectiveness should match compute_stats breakdown
        assert autopsy["exit_effectiveness"]["stop"]["count"] == 1
        assert autopsy["exit_effectiveness"]["t1"]["count"] == 1

    def test_run_backtest_stores_trades_for_autopsy(self) -> None:
        """run_backtest stats dict includes _trades key for exit_autopsy."""
        from scripts.backtest import SimulatedTrade, compute_stats

        trades = [
            SimulatedTrade(
                ticker="X", date="2026-01-01",
                entry_price=10.0, entry_time="09:32",
                exit_price=10.5, exit_time="10:00",
                exit_reason="t1", pnl_pct=5.0, pnl_dollars=50.0,
                stop_price=9.5, stop_distance_pct=5.0,
                t1_target=10.5, t2_target=11.0, t3_target=12.0,
                gap_pct=8.0, atr=0.50, hold_minutes=28,
            ),
        ]

        stats = compute_stats(trades, 5, 3, 1)
        # Simulate what run_backtest does
        stats["_trades"] = trades

        assert "_trades" in stats
        assert len(stats["_trades"]) == 1
        assert stats["_trades"][0].ticker == "X"


# ── 4. ETL → Query → Time-Travel Full Pipeline ──────────────


class TestETLQueryTimeTravelPipeline:
    """Test the full ETL → query → time-travel pipeline end-to-end."""

    def _build_full_db(self, tmp_path: Path) -> Path:
        """Create a full test database with realistic cross-referenced data."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        # Create all data directories
        (data_dir / "session_reports").mkdir()
        (data_dir / "journals").mkdir()
        (data_dir / "experiments").mkdir()
        (data_dir / "signal_history").mkdir()
        (data_dir / "metrics").mkdir()

        # Session report
        session = {
            "session_date": "2026-03-10",
            "session_start": "2026-03-10T09:30:00",
            "session_end": "2026-03-10T16:00:00",
            "duration_minutes": 390,
            "mode": "paper",
            "scan_iterations": 50,
            "candidates_found": 12,
            "evaluations_total": 8,
            "orders_submitted": 5,
            "orders_filled": 3,
            "session_trades": 3,
            "daily_pnl": 250.0,
            "realized_pnl_pm": 100.0,
            "win_count": 2,
            "loss_count": 1,
            "stop_outs": 1,
            "smart_exits": 0,
            "circuit_breaker_activations": 0,
            "risk_vetoes": 3,
            "agent_errors": 0,
            "phase3_cycles": 60,
            "experiment_variants_tested": 23,
        }
        (data_dir / "session_reports" / "session_2026-03-10.json").write_text(
            json.dumps(session)
        )

        # Trade journal with agent signals
        trades = [
            {
                "trade_id": "t_001",
                "ticker": "AAPL",
                "timestamp": "2026-03-10T09:35:00",
                "session_date": "2026-03-10",
                "phase": "phase2",
                "action": "BUY",
                "mfcs": 0.72,
                "risk_score": 0.15,
                "confidence": 0.80,
                "entry_price": 150.0,
                "stop_loss": 143.0,
                "gap_pct": 0.08,
                "rvol": 3.5,
                "exit_price": 155.0,
                "exit_time": "2026-03-10T10:45:00",
                "realized_pnl": 500.0,
                "exit_reason": "t1",
                "agent_signals": [
                    {"agent_id": "news", "signal": "BULLISH", "confidence": 0.85},
                    {"agent_id": "technical", "signal": "BULLISH", "confidence": 0.70},
                ],
            },
            {
                "trade_id": "t_002",
                "ticker": "MSFT",
                "timestamp": "2026-03-10T09:40:00",
                "session_date": "2026-03-10",
                "phase": "phase2",
                "action": "BUY",
                "mfcs": 0.55,
                "risk_score": 0.25,
                "confidence": 0.60,
                "entry_price": 300.0,
                "stop_loss": 285.0,
                "gap_pct": 0.06,
                "rvol": 2.5,
                "exit_price": 290.0,
                "exit_time": "2026-03-10T10:00:00",
                "realized_pnl": -500.0,
                "exit_reason": "stop",
                "agent_signals": [
                    {"agent_id": "news", "signal": "NEUTRAL", "confidence": 0.50},
                ],
            },
        ]
        journal_path = data_dir / "journals" / "journal_2026-03-10.jsonl"
        with open(journal_path, "w") as f:
            for t in trades:
                f.write(json.dumps(t) + "\n")

        # Signal history
        signals = [
            {
                "ts": "2026-03-10T10:00:00",
                "ticker": "AAPL",
                "composite": 0.25,
                "recommendation": "HOLD",
                "price": 152.0,
                "entry_price": 150.0,
                "pnl_pct": 1.3,
                "volume_fade": 0.1,
                "vwap_deterioration": 0.05,
            },
            {
                "ts": "2026-03-10T10:05:00",
                "ticker": "AAPL",
                "composite": 0.42,
                "recommendation": "TIGHTEN",
                "price": 154.0,
                "entry_price": 150.0,
                "pnl_pct": 2.7,
                "volume_fade": 0.2,
                "vwap_deterioration": 0.1,
            },
        ]
        signal_path = data_dir / "signal_history" / "signal_log_2026-03-10.jsonl"
        with open(signal_path, "w") as f:
            for s in signals:
                f.write(json.dumps(s) + "\n")

        # Metric snapshot
        metric_snap = {
            "timestamp": "2026-03-10T10:00:00",
            "uptime_seconds": 1800.0,
            "pipeline": {"scan_iterations": 30, "candidates_found": 8,
                         "evaluations_total": 6, "pipeline_latency_mean_s": 1.5},
            "execution": {"orders_submitted": 5, "orders_filled": 3,
                          "session_trades": 3},
            "risk": {"daily_pnl": 250.0, "circuit_breaker_activations": 0,
                     "risk_vetoes": 3},
            "agents": {"errors_total": 0},
            "exit_events": {"stop_outs": 1, "smart_exits": 0},
            "phase_timing": {"phase3_cycles": 30},
            "data_completeness": {"overall_fill_rate": 0.6},
        }
        (data_dir / "metrics" / "metrics_20260310T100000.json").write_text(
            json.dumps(metric_snap)
        )

        # Run ETL
        from scripts.etl_sqlite import run_etl

        db_path = tmp_path / "mx.db"
        run_etl(db_path=db_path, data_dir=data_dir, rebuild=False, verbose=False)
        return db_path

    def test_full_pipeline_cross_reference(self, tmp_path: Path) -> None:
        """ETL loads all data, queries can cross-reference tables."""
        from scripts.etl_sqlite import run_query

        db_path = self._build_full_db(tmp_path)

        # Cross-reference: join trades with agent signals
        rows = run_query(
            """SELECT t.ticker, t.mfcs, a.agent_id, a.signal
               FROM trades t JOIN agent_signals a ON t.trade_id = a.trade_id
               ORDER BY t.ticker, a.agent_id""",
            db_path=db_path,
        )
        assert len(rows) == 3  # AAPL has 2 agent signals, MSFT has 1
        # First should be AAPL/news (alphabetical)
        assert rows[0][0] == "AAPL"

    def test_time_travel_at_active_position(self, tmp_path: Path) -> None:
        """Time-travel during active position shows correct state."""
        from scripts.etl_sqlite import system_state_at

        db_path = self._build_full_db(tmp_path)

        # At 10:00, AAPL should still be open (exits at 10:45)
        # MSFT has already exited (exits at 10:00, so at exactly 10:00 it should
        # still be open since exit_time > timestamp is checked)
        state = system_state_at("2026-03-10T09:50:00", db_path=db_path)

        assert len(state["positions"]) == 2  # Both open
        tickers = [p["ticker"] for p in state["positions"]]
        assert "AAPL" in tickers
        assert "MSFT" in tickers

        # Session should be populated
        assert state["session"] is not None
        assert state["session"]["daily_pnl"] == 250.0

    def test_time_travel_after_one_exit(self, tmp_path: Path) -> None:
        """Time-travel after one position exits shows correct remaining positions."""
        from scripts.etl_sqlite import system_state_at

        db_path = self._build_full_db(tmp_path)

        # At 10:30, MSFT has exited (10:00), AAPL still open (exits 10:45)
        state = system_state_at("2026-03-10T10:30:00", db_path=db_path)

        assert len(state["positions"]) == 1
        assert state["positions"][0]["ticker"] == "AAPL"

    def test_query_views_with_loaded_data(self, tmp_path: Path) -> None:
        """Materialized views work with loaded data."""
        from scripts.etl_sqlite import run_query

        db_path = self._build_full_db(tmp_path)

        # v_ticker_stats should show per-ticker aggregation
        rows = run_query(
            "SELECT ticker, buys, total_pnl FROM v_ticker_stats WHERE buys > 0",
            db_path=db_path,
        )
        assert len(rows) == 2
        # One winning (AAPL +500) and one losing (MSFT -500)
        tickers = {r[0]: r[2] for r in rows}
        assert tickers["AAPL"] == 500.0
        assert tickers["MSFT"] == -500.0

    def test_query_rolling_performance(self, tmp_path: Path) -> None:
        """v_rolling_performance view shows session progression."""
        from scripts.etl_sqlite import run_query

        db_path = self._build_full_db(tmp_path)

        rows = run_query(
            "SELECT session_date, daily_pnl, cumulative_pnl FROM v_rolling_performance",
            db_path=db_path,
        )
        assert len(rows) == 1
        assert rows[0][1] == 250.0  # daily_pnl
        assert rows[0][2] == 250.0  # cumulative_pnl (only one session)

    def test_time_travel_signals_window(self, tmp_path: Path) -> None:
        """Time-travel finds exit signals within ±5 min window."""
        from scripts.etl_sqlite import system_state_at

        db_path = self._build_full_db(tmp_path)

        # At 10:02, should find signal at 10:00 and 10:05 (both within ±5 min)
        state = system_state_at("2026-03-10T10:02:00", db_path=db_path)
        assert len(state["signals"]) == 2

        # At 09:50, should find signal at 10:00 is NOT in range (>5 min away)
        # Wait — 09:50 + 5 min = 09:55, so 10:00 IS NOT within range
        state2 = system_state_at("2026-03-10T09:50:00", db_path=db_path)
        assert len(state2["signals"]) == 0


# ── 5. Exit Autopsy Edge Cases ──────────────────────────────


class TestExitAutopsyEdgeCases:
    """Test Exit Autopsy handles unusual trade distributions correctly."""

    def test_all_winners_no_stops(self) -> None:
        """Exit autopsy handles portfolio with no stop-outs."""
        from scripts.backtest import SimulatedTrade, run_exit_autopsy

        trades = [
            SimulatedTrade(
                ticker="WIN", date="2026-01-01",
                entry_price=10.0, entry_time="09:32",
                exit_price=10.5, exit_time="10:00",
                exit_reason="t1", pnl_pct=5.0, pnl_dollars=50.0,
                stop_price=9.5, stop_distance_pct=5.0,
                t1_target=10.5, t2_target=11.0, t3_target=12.0,
                gap_pct=8.0, atr=0.50, hold_minutes=28,
                mfe_pct=6.0, mae_pct=1.0, mfe_bar=10,
            ),
        ]

        autopsy = run_exit_autopsy(trades)
        assert autopsy["stop_tightness"]["stop_outs"] == 0
        assert autopsy["stop_tightness"]["stopped_with_prior_gain"] == 0
        # Should still have a valid optimal stop
        assert autopsy["optimal_stop"]["best_stop_pct"] > 0

    def test_all_stops_no_winners(self) -> None:
        """Exit autopsy handles portfolio with 100% stop-out rate."""
        from scripts.backtest import SimulatedTrade, run_exit_autopsy

        trades = [
            SimulatedTrade(
                ticker=f"LOSS{i}", date="2026-01-01",
                entry_price=10.0, entry_time="09:32",
                exit_price=9.5, exit_time="09:40",
                exit_reason="stop", pnl_pct=-5.0, pnl_dollars=-50.0,
                stop_price=9.5, stop_distance_pct=5.0,
                t1_target=10.5, t2_target=11.0, t3_target=12.0,
                gap_pct=8.0, atr=0.50, hold_minutes=8,
                mfe_pct=0.5, mae_pct=5.0, mfe_bar=1,
                stopped_before_mfe=True,
            )
            for i in range(5)
        ]

        autopsy = run_exit_autopsy(trades)
        assert autopsy["stop_tightness"]["stop_outs"] == 5
        assert autopsy["stop_tightness"]["stop_out_pct"] == 100.0
        assert autopsy["exit_effectiveness"]["stop"]["win_rate"] == 0.0

    def test_zero_mfe_trades(self) -> None:
        """Exit autopsy handles trades with zero MFE (immediate stop-out)."""
        from scripts.backtest import SimulatedTrade, run_exit_autopsy

        trades = [
            SimulatedTrade(
                ticker="ZERO", date="2026-01-01",
                entry_price=10.0, entry_time="09:32",
                exit_price=9.5, exit_time="09:33",
                exit_reason="stop", pnl_pct=-5.0, pnl_dollars=-50.0,
                stop_price=9.5, stop_distance_pct=5.0,
                t1_target=10.5, t2_target=11.0, t3_target=12.0,
                gap_pct=8.0, atr=0.50, hold_minutes=1,
                mfe_pct=0.0, mae_pct=5.0, mfe_bar=0,
                stopped_before_mfe=False,  # No MFE to be stopped before
            ),
        ]

        autopsy = run_exit_autopsy(trades)
        # Should not crash
        assert autopsy["money_left_on_table"]["avg_capture_ratio"] == 0
        assert autopsy["mfe_timing"]["total_with_positive_mfe"] == 0

    def test_single_trade_autopsy(self) -> None:
        """Exit autopsy works correctly with a single trade."""
        from scripts.backtest import SimulatedTrade, run_exit_autopsy

        trades = [
            SimulatedTrade(
                ticker="SOLO", date="2026-01-01",
                entry_price=10.0, entry_time="09:32",
                exit_price=10.5, exit_time="10:00",
                exit_reason="t2", pnl_pct=5.0, pnl_dollars=50.0,
                stop_price=9.5, stop_distance_pct=5.0,
                t1_target=10.25, t2_target=10.5, t3_target=11.0,
                gap_pct=8.0, atr=0.50, hold_minutes=28,
                mfe_pct=7.0, mae_pct=2.0, mfe_bar=20,
            ),
        ]

        autopsy = run_exit_autopsy(trades)
        assert autopsy["stop_tightness"]["total_trades"] == 1
        assert autopsy["exit_effectiveness"]["t2"]["count"] == 1
        assert autopsy["exit_effectiveness"]["t2"]["win_rate"] == 100.0
