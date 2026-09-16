"""
D109 Quick Wins — Tests for metric snapshot retention, validation clamp
counter, MFE/MAE tracking, and backtest analytical extensions.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── 1. Metric Snapshot Retention (50 → 200) ───────────────────────


class TestMetricSnapshotRetention:
    """Verify snapshot rotation keeps the correct maximum files."""

    def test_metric_snapshot_rotation_keeps_200(self, tmp_path: Path) -> None:
        """Write 210 snapshot files → only 200 remain after rotation."""
        from src.monitoring.metrics import MetricsRegistry

        metrics = MetricsRegistry()

        # Directly create 210 uniquely-named snapshot files to test rotation
        for i in range(210):
            fname = f"metrics_20260101T{i:06d}.json"
            (tmp_path / fname).write_text('{"test": true}')

        # Now call save_snapshot which will trigger rotation
        metrics.save_snapshot(tmp_path)

        snapshot_files = list(tmp_path.glob("metrics_*.json"))
        # Should be ≤ 200 (210 pre-existing + 1 new - rotated old ones)
        assert len(snapshot_files) <= 200, (
            f"Expected ≤200 snapshot files after rotation, got {len(snapshot_files)}"
        )

    def test_max_snapshots_is_200(self) -> None:
        """Verify the _MAX_SNAPSHOTS constant is 200 (D109 increase from 50)."""
        from src.monitoring.metrics import MetricsRegistry
        assert MetricsRegistry._MAX_SNAPSHOTS == 200


# ── 2. Validation Clamp Counter Metric ────────────────────────────


class TestValidationClampCounter:
    """Verify that position validation clamps increment the counter metric."""

    def test_validation_clamp_increments_counter(self) -> None:
        """Clamp negative remaining_qty → counter increments."""
        from src.monitoring.metrics import get_metrics, reset_metrics

        reset_metrics()
        metrics = get_metrics()

        # Start at 0
        assert metrics.state_validation_clamps.value == 0.0

        # Create a mock position with negative remaining_qty
        mock_pos = MagicMock()
        mock_pos.remaining_qty = -5
        mock_pos.qty = 100
        mock_pos.tranches_filled = 1
        mock_pos.stop_loss = 5.0
        mock_pos.entry_price = 10.0
        mock_pos.target_prices = [11.0, 12.0, 13.0]
        mock_pos.ticker = "TEST"

        # Import and create a position manager to call validation
        from src.execution.position_manager import PositionManager

        pm = PositionManager.__new__(PositionManager)
        pm._positions = {"TEST": mock_pos}

        # Run validation
        pm._validate_recovered_positions()

        # Counter should have incremented (negative qty clamped)
        assert metrics.state_validation_clamps.value >= 1.0, (
            "Expected clamp counter to increment for negative remaining_qty"
        )
        # The position should be clamped
        assert mock_pos.remaining_qty == 0

    def test_validation_clamp_counter_multiple_issues(self) -> None:
        """Multiple clamp-worthy issues → counter increments for each."""
        from src.monitoring.metrics import get_metrics, reset_metrics

        reset_metrics()
        metrics = get_metrics()

        # Position with negative qty AND out-of-range tranches
        mock_pos = MagicMock()
        mock_pos.remaining_qty = -3
        mock_pos.qty = 50
        mock_pos.tranches_filled = 5  # Out of range [0,3]
        mock_pos.stop_loss = 5.0
        mock_pos.entry_price = 10.0
        mock_pos.target_prices = [11.0, 12.0, 13.0]
        mock_pos.ticker = "MULTI"

        from src.execution.position_manager import PositionManager

        pm = PositionManager.__new__(PositionManager)
        pm._positions = {"MULTI": mock_pos}
        pm._validate_recovered_positions()

        # Should have at least 2 increments (negative qty + bad tranches)
        assert metrics.state_validation_clamps.value >= 2.0, (
            f"Expected ≥2 clamp counter increments, got {metrics.state_validation_clamps.value}"
        )


# ── 3. MFE/MAE Tracking ──────────────────────────────────────────


class TestMFEMAETracking:
    """Verify MFE/MAE fields are computed correctly in backtest simulation."""

    def test_mfe_mae_fields_exist_on_simulated_trade(self) -> None:
        """SimulatedTrade dataclass has MFE/MAE fields."""
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from scripts.backtest import SimulatedTrade

        trade = SimulatedTrade(
            ticker="TEST", date="2026-01-01",
            entry_price=10.0, entry_time="09:32",
            exit_price=9.5, exit_time="10:15",
            exit_reason="stop", pnl_pct=-5.0, pnl_dollars=-50.0,
            stop_price=9.5, stop_distance_pct=5.0,
            t1_target=10.5, t2_target=11.0, t3_target=12.0,
            gap_pct=8.0, atr=0.50, hold_minutes=43,
            mfe_pct=3.0, mae_pct=5.0, mfe_bar=5,
            stopped_before_mfe=True,
        )
        assert trade.mfe_pct == 3.0
        assert trade.mae_pct == 5.0
        assert trade.mfe_bar == 5
        assert trade.stopped_before_mfe is True

    def test_mfe_mae_defaults_to_zero(self) -> None:
        """MFE/MAE defaults to 0 when not specified."""
        from scripts.backtest import SimulatedTrade

        trade = SimulatedTrade(
            ticker="TEST", date="2026-01-01",
            entry_price=10.0, entry_time="09:32",
            exit_price=10.5, exit_time="10:00",
            exit_reason="t1", pnl_pct=5.0, pnl_dollars=50.0,
            stop_price=9.5, stop_distance_pct=5.0,
            t1_target=10.5, t2_target=11.0, t3_target=12.0,
            gap_pct=8.0, atr=0.50, hold_minutes=28,
        )
        assert trade.mfe_pct == 0.0
        assert trade.mae_pct == 0.0
        assert trade.mfe_bar == 0
        assert trade.stopped_before_mfe is False

    def test_mfe_mae_in_stats_computation(self) -> None:
        """compute_stats includes MFE/MAE summary statistics."""
        from scripts.backtest import SimulatedTrade, compute_stats

        trades = [
            SimulatedTrade(
                ticker="A", date="2026-01-01",
                entry_price=10.0, entry_time="09:32",
                exit_price=9.5, exit_time="10:00",
                exit_reason="stop", pnl_pct=-5.0, pnl_dollars=-50.0,
                stop_price=9.5, stop_distance_pct=5.0,
                t1_target=10.5, t2_target=11.0, t3_target=12.0,
                gap_pct=8.0, atr=0.50, hold_minutes=28,
                mfe_pct=2.0, mae_pct=5.0, mfe_bar=3,
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
                mfe_pct=12.0, mae_pct=2.0, mfe_bar=10,
                stopped_before_mfe=False,
            ),
        ]

        stats = compute_stats(trades, 10, 5, 2)

        assert "avg_mfe_pct" in stats
        assert "avg_mae_pct" in stats
        assert "mfe_mae_ratio" in stats
        assert "stopped_before_mfe_pct" in stats
        assert "stopped_before_mfe_count" in stats

        # avg MFE = (2.0 + 12.0) / 2 = 7.0
        assert abs(stats["avg_mfe_pct"] - 7.0) < 0.01
        # avg MAE = (5.0 + 2.0) / 2 = 3.5
        assert abs(stats["avg_mae_pct"] - 3.5) < 0.01
        # MFE/MAE ratio = 7.0 / 3.5 = 2.0
        assert abs(stats["mfe_mae_ratio"] - 2.0) < 0.01
        # 1 out of 2 stopped before MFE = 50%
        assert stats["stopped_before_mfe_count"] == 1
        assert abs(stats["stopped_before_mfe_pct"] - 50.0) < 0.01


# ── 4. Backtest CLI Flags ─────────────────────────────────────────


class TestBacktestCLIFlags:
    """Verify D109 analytical extension flags are registered."""

    def test_null_time_flag_registered(self) -> None:
        """--null-time flag is recognized by argparse."""
        import argparse
        from scripts.backtest import main

        # Access the parser indirectly by testing the module has the functions
        from scripts.backtest import run_null_time_benchmark
        assert callable(run_null_time_benchmark)

    def test_null_filter_flag_registered(self) -> None:
        """--null-filter function exists and is callable."""
        from scripts.backtest import run_null_filter_benchmark
        assert callable(run_null_filter_benchmark)

    def test_anti_signal_flag_registered(self) -> None:
        """--anti-signal function exists and is callable."""
        from scripts.backtest import run_anti_signal_analysis
        assert callable(run_anti_signal_analysis)

    def test_compare_groups_function(self) -> None:
        """_compare_groups produces output without error."""
        from scripts.backtest import SimulatedTrade, _compare_groups

        trades_a = [
            SimulatedTrade(
                ticker="A", date="2026-01-01",
                entry_price=10.0, entry_time="09:32",
                exit_price=10.5, exit_time="10:00",
                exit_reason="t1", pnl_pct=5.0, pnl_dollars=50.0,
                stop_price=9.5, stop_distance_pct=5.0,
                t1_target=10.5, t2_target=11.0, t3_target=12.0,
                gap_pct=8.0, atr=0.50, hold_minutes=28,
            ),
        ]
        trades_b = [
            SimulatedTrade(
                ticker="B", date="2026-01-01",
                entry_price=5.0, entry_time="09:32",
                exit_price=4.5, exit_time="10:00",
                exit_reason="stop", pnl_pct=-10.0, pnl_dollars=-50.0,
                stop_price=4.5, stop_distance_pct=10.0,
                t1_target=5.5, t2_target=6.0, t3_target=6.5,
                gap_pct=6.0, atr=0.30, hold_minutes=28,
            ),
        ]

        # Should not raise
        _compare_groups("Group A", trades_a, "Group B", trades_b)


# ── 5. D109 Phase 3: Exit Autopsy Engine ─────────────────────────


class TestExitAutopsy:
    """Verify full Exit Autopsy engine produces correct analysis."""

    def _make_trades(self) -> list:
        from scripts.backtest import SimulatedTrade
        return [
            SimulatedTrade(
                ticker="STOP1", date="2026-01-01",
                entry_price=10.0, entry_time="09:32",
                exit_price=9.5, exit_time="09:45",
                exit_reason="stop", pnl_pct=-5.0, pnl_dollars=-50.0,
                stop_price=9.5, stop_distance_pct=5.0,
                t1_target=10.5, t2_target=11.0, t3_target=12.0,
                gap_pct=8.0, atr=0.50, hold_minutes=13,
                mfe_pct=2.0, mae_pct=5.0, mfe_bar=3,
                stopped_before_mfe=True,
            ),
            SimulatedTrade(
                ticker="WIN1", date="2026-01-01",
                entry_price=5.0, entry_time="09:32",
                exit_price=5.5, exit_time="10:30",
                exit_reason="t1", pnl_pct=10.0, pnl_dollars=50.0,
                stop_price=4.5, stop_distance_pct=10.0,
                t1_target=5.5, t2_target=6.0, t3_target=6.5,
                gap_pct=6.0, atr=0.30, hold_minutes=58,
                mfe_pct=12.0, mae_pct=2.0, mfe_bar=10,
                stopped_before_mfe=False,
            ),
            SimulatedTrade(
                ticker="STOP2", date="2026-01-02",
                entry_price=20.0, entry_time="09:35",
                exit_price=19.0, exit_time="09:40",
                exit_reason="stop", pnl_pct=-5.0, pnl_dollars=-100.0,
                stop_price=19.0, stop_distance_pct=5.0,
                t1_target=21.0, t2_target=22.0, t3_target=24.0,
                gap_pct=10.0, atr=1.0, hold_minutes=5,
                mfe_pct=1.0, mae_pct=5.0, mfe_bar=1,
                stopped_before_mfe=True,
            ),
            SimulatedTrade(
                ticker="EOD1", date="2026-01-02",
                entry_price=15.0, entry_time="09:32",
                exit_price=15.3, exit_time="15:55",
                exit_reason="eod", pnl_pct=2.0, pnl_dollars=30.0,
                stop_price=14.0, stop_distance_pct=6.7,
                t1_target=15.75, t2_target=16.5, t3_target=18.0,
                gap_pct=7.0, atr=0.60, hold_minutes=383,
                mfe_pct=5.0, mae_pct=1.0, mfe_bar=25,
                stopped_before_mfe=False,
            ),
        ]

    def test_exit_autopsy_returns_all_sections(self) -> None:
        """run_exit_autopsy returns all 6 analysis sections."""
        from scripts.backtest import run_exit_autopsy
        trades = self._make_trades()
        autopsy = run_exit_autopsy(trades)

        assert "stop_tightness" in autopsy
        assert "money_left_on_table" in autopsy
        assert "exit_effectiveness" in autopsy
        assert "hold_duration_analysis" in autopsy
        assert "mfe_timing" in autopsy
        assert "optimal_stop" in autopsy

    def test_stop_tightness_counts(self) -> None:
        """Stop tightness correctly counts stopped trades."""
        from scripts.backtest import run_exit_autopsy
        trades = self._make_trades()
        autopsy = run_exit_autopsy(trades)

        st = autopsy["stop_tightness"]
        assert st["total_trades"] == 4
        assert st["stop_outs"] == 2
        assert st["stop_out_pct"] == 50.0
        # Both stopped trades had prior gain (MFE > 0)
        assert st["stopped_with_prior_gain"] == 2
        assert st["stopped_with_prior_gain_pct"] == 100.0

    def test_exit_effectiveness_breakdown(self) -> None:
        """Exit effectiveness breaks down by exit reason."""
        from scripts.backtest import run_exit_autopsy
        trades = self._make_trades()
        autopsy = run_exit_autopsy(trades)

        ee = autopsy["exit_effectiveness"]
        assert "stop" in ee
        assert "t1" in ee
        assert "eod" in ee
        assert ee["stop"]["count"] == 2
        assert ee["t1"]["count"] == 1
        assert ee["eod"]["count"] == 1
        # t1 should have 100% win rate
        assert ee["t1"]["win_rate"] == 100.0

    def test_hold_duration_analysis(self) -> None:
        """Hold duration analysis assigns trades to correct buckets."""
        from scripts.backtest import run_exit_autopsy
        trades = self._make_trades()
        autopsy = run_exit_autopsy(trades)

        hd = autopsy["hold_duration_analysis"]
        # STOP2 at 5 min → "5-15 min" bucket (boundary is 0 <= x < 5)
        assert hd["5-15 min"]["count"] == 2  # STOP1 at 13 min + STOP2 at 5 min
        assert hd["30-60 min"]["count"] == 1  # WIN1 at 58 min
        assert hd["60+ min"]["count"] == 1  # EOD1 at 383 min

    def test_optimal_stop_sweep(self) -> None:
        """Optimal stop finder sweeps multiple distances."""
        from scripts.backtest import run_exit_autopsy
        trades = self._make_trades()
        autopsy = run_exit_autopsy(trades)

        os_data = autopsy["optimal_stop"]
        assert len(os_data["sweep_results"]) == 10
        assert os_data["best_stop_pct"] > 0
        assert os_data["best_profit_factor"] > 0

    def test_mfe_timing_analysis(self) -> None:
        """MFE timing correctly counts bar distributions."""
        from scripts.backtest import run_exit_autopsy
        trades = self._make_trades()
        autopsy = run_exit_autopsy(trades)

        mt = autopsy["mfe_timing"]
        assert mt["total_with_positive_mfe"] == 4  # All trades have MFE > 0
        # Bars: 3, 10, 1, 25 → within 5: bar 3 and bar 1 = 2
        assert mt["mfe_within_5_bars"] == 2

    def test_print_exit_autopsy_runs_without_error(self) -> None:
        """print_exit_autopsy produces output without error."""
        from scripts.backtest import print_exit_autopsy
        trades = self._make_trades()
        # Should not raise
        print_exit_autopsy(trades)

    def test_exit_autopsy_empty_trades(self) -> None:
        """Exit autopsy handles empty trade list gracefully."""
        from scripts.backtest import run_exit_autopsy
        result = run_exit_autopsy([])
        assert result == {}


# ── 6. D109 Phase 3: ETL CLI Query & Time-Travel ────────────────


class TestETLQueryInterface:
    """Verify CLI query interface and time-travel queries."""

    def _setup_db(self, tmp_path: Path) -> Path:
        """Create a test database with sample data."""
        import sqlite3
        from scripts.etl_sqlite import SCHEMA_SQL, VIEWS_SQL

        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.executescript(SCHEMA_SQL)
        conn.executescript(VIEWS_SQL)

        # Insert test data
        conn.execute(
            "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("2026-03-10", "2026-03-10T09:30:00", "2026-03-10T16:00:00",
             390, "paper", 50, 10, 8, 5, 3, 3,
             150.0, 100.0, 2, 1, 1, 0, 0, 2, 0, 45, 23, "test"),
        )
        conn.execute(
            """INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
             ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("t1", "AAPL", "2026-03-10T09:35:00", "2026-03-10", "phase2",
             "BUY", 0.65, 0.2, 0.7, 150.0, 143.0, 0.08, 3.5,
             151.0, 0.15, 1200.0,
             150.5, 100, 33.0, 155.0, "2026-03-10T10:30:00",
             450.0, 55.0, "t1", 1, 45.0, "test"),
        )
        conn.execute(
            """INSERT INTO trades VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
             ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("t2", "MSFT", "2026-03-10T09:40:00", "2026-03-10", "phase2",
             "BUY", 0.55, 0.3, 0.6, 300.0, 285.0, 0.06, 2.5,
             301.0, 0.10, 1500.0,
             300.5, 50, 17.0, None, None,
             None, None, None, None, None, "test"),
        )
        conn.execute(
            """INSERT INTO metric_snapshots (timestamp, uptime_seconds,
             scan_iterations, candidates_found, evaluations_total,
             pipeline_latency_mean_s, orders_submitted, orders_filled,
             session_trades, daily_pnl, circuit_breaker_activations,
             risk_vetoes, agent_errors, stop_outs, smart_exits,
             phase3_cycles, overall_fill_rate, source_file)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("2026-03-10T10:00:00", 1800.0, 30, 8, 6, 1.5,
             5, 3, 3, 150.0, 0, 2, 0, 1, 0, 20, 0.6, "test"),
        )
        conn.execute(
            """INSERT INTO signal_history (timestamp, ticker, composite,
             recommendation, price, entry_price, pnl_pct,
             volume_fade, vwap_deterioration, spread_widening,
             time_decay, distribution, resistance_proximity,
             failed_breakout, churning, obv_divergence,
             volume_climax, momentum_degradation, flow_toxicity,
             distribution_detector, source_file)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("2026-03-10T10:02:00", "AAPL", 0.35, "HOLD",
             152.0, 150.0, 1.3,
             0.1, 0.05, 0.0, 0.1, 0.0, 0.05, 0.0, 0.0, 0.0, 0.0, 0.05, 0.0, 0.0,
             "test"),
        )
        conn.commit()
        conn.close()
        return db_path

    def test_run_query_table_format(self, tmp_path: Path) -> None:
        """run_query returns results in table format."""
        from scripts.etl_sqlite import run_query
        db_path = self._setup_db(tmp_path)
        rows = run_query("SELECT ticker, mfcs FROM trades", db_path=db_path)
        assert len(rows) == 2
        assert rows[0][0] == "AAPL"
        assert rows[1][0] == "MSFT"

    def test_run_query_json_format(self, tmp_path: Path, capsys) -> None:
        """run_query outputs valid JSON when format=json."""
        from scripts.etl_sqlite import run_query
        db_path = self._setup_db(tmp_path)
        run_query("SELECT ticker FROM trades", db_path=db_path, fmt="json")
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert len(data) == 2
        assert data[0]["ticker"] == "AAPL"

    def test_run_query_csv_format(self, tmp_path: Path, capsys) -> None:
        """run_query outputs CSV when format=csv."""
        from scripts.etl_sqlite import run_query
        db_path = self._setup_db(tmp_path)
        run_query("SELECT ticker, mfcs FROM trades", db_path=db_path, fmt="csv")
        captured = capsys.readouterr()
        lines = captured.out.strip().split("\n")
        assert lines[0] == "ticker,mfcs"
        assert "AAPL" in lines[1]

    def test_run_query_handles_bad_sql(self, tmp_path: Path) -> None:
        """run_query handles SQL errors gracefully."""
        from scripts.etl_sqlite import run_query
        db_path = self._setup_db(tmp_path)
        rows = run_query("SELECT * FROM nonexistent_table", db_path=db_path)
        assert rows == []

    def test_positions_at_finds_open_positions(self, tmp_path: Path) -> None:
        """positions_at returns positions open at a given timestamp."""
        from scripts.etl_sqlite import positions_at
        db_path = self._setup_db(tmp_path)

        # At 10:00, both positions should be open
        # (t1 AAPL entered 09:35, exits 10:30; t2 MSFT entered 09:40, no exit)
        positions = positions_at("2026-03-10T10:00:00", db_path=db_path)
        tickers = [p["ticker"] for p in positions]
        assert "AAPL" in tickers
        assert "MSFT" in tickers

    def test_positions_at_after_exit(self, tmp_path: Path) -> None:
        """positions_at excludes positions that have exited."""
        from scripts.etl_sqlite import positions_at
        db_path = self._setup_db(tmp_path)

        # At 11:00, AAPL should have exited (exit_time 10:30)
        positions = positions_at("2026-03-10T11:00:00", db_path=db_path)
        tickers = [p["ticker"] for p in positions]
        assert "AAPL" not in tickers
        assert "MSFT" in tickers  # Still open (no exit_time)

    def test_system_state_at_returns_all_sections(self, tmp_path: Path) -> None:
        """system_state_at returns positions, metrics, signals, session."""
        from scripts.etl_sqlite import system_state_at
        db_path = self._setup_db(tmp_path)

        state = system_state_at("2026-03-10T10:00:00", db_path=db_path)
        assert "positions" in state
        assert "metrics" in state
        assert "signals" in state
        assert "session" in state
        assert "trades_this_session" in state

        # Should have metrics from 10:00
        assert state["metrics"] is not None
        assert state["metrics"]["uptime_seconds"] == 1800.0

        # Should have session info
        assert state["session"] is not None
        assert state["session"]["session_date"] == "2026-03-10"

    def test_system_state_at_finds_nearby_signals(self, tmp_path: Path) -> None:
        """system_state_at finds signals within ±5 min window."""
        from scripts.etl_sqlite import system_state_at
        db_path = self._setup_db(tmp_path)

        # Signal at 10:02, query at 10:00 → within ±5 min
        state = system_state_at("2026-03-10T10:00:00", db_path=db_path)
        assert len(state["signals"]) >= 1
        assert state["signals"][0]["ticker"] == "AAPL"

    def test_show_time_travel_runs_without_error(self, tmp_path: Path) -> None:
        """show_time_travel produces formatted output without error."""
        from scripts.etl_sqlite import show_time_travel
        db_path = self._setup_db(tmp_path)
        # Should not raise
        show_time_travel("2026-03-10T10:00:00", db_path=db_path)
