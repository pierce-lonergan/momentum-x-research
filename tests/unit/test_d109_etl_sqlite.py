"""
D109 Phase 2: ETL SQLite Tests

Tests for the post-session JSONL → SQLite ETL pipeline, schema creation,
data loading, materialized views, and integrity verification.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import sys
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_PROJECT_ROOT / "scripts"))

from scripts.etl_sqlite import (
    SCHEMA_SQL,
    VIEWS_SQL,
    init_db,
    load_experiments,
    load_metric_snapshots,
    load_sessions,
    load_signal_history,
    load_trades,
    rebuild_db,
    run_etl,
    verify_integrity,
)


# ── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """Create a temporary data directory with sample JSONL/JSON files."""
    # Session reports
    reports_dir = tmp_path / "session_reports"
    reports_dir.mkdir()
    report = {
        "session_date": "2026-03-12",
        "session_start": "2026-03-12T08:30:00Z",
        "session_end": "2026-03-12T16:00:00Z",
        "duration_minutes": 450.0,
        "mode": "paper",
        "scan_iterations": 120,
        "candidates_found": 15,
        "evaluations_total": 8,
        "orders_submitted": 3,
        "orders_filled": 2,
        "session_trades": 2,
        "daily_pnl": -150.25,
        "realized_pnl_pm": -148.00,
        "win_count": 0,
        "loss_count": 2,
        "stop_outs": 2,
        "smart_exits": 0,
        "circuit_breaker_activations": 0,
        "risk_vetoes": 1,
        "agent_errors": 0,
        "phase3_cycles": 45,
        "experiment_variants_tested": 176,
    }
    (reports_dir / "session_2026-03-12_2026-03-12T16-00-00.json").write_text(
        json.dumps(report), encoding="utf-8"
    )

    # Trade journal
    journals_dir = tmp_path / "journals"
    journals_dir.mkdir()
    trade_entry = {
        "trade_id": "T_20260312_PRSO_1",
        "ticker": "PRSO",
        "timestamp": "2026-03-12T09:32:15Z",
        "session_date": "2026-03-12",
        "phase": "MARKET_OPEN",
        "action": "BUY",
        "mfcs": 0.42,
        "risk_score": 0.35,
        "confidence": 0.65,
        "entry_price": 1.63,
        "stop_loss": 1.39,
        "gap_pct": 0.12,
        "rvol": 4.5,
        "current_price": 1.65,
        "position_size_pct": 0.01,
        "pipeline_latency_ms": 2450.0,
        "fill_price": 1.64,
        "fill_qty": 850,
        "slippage_bps": 6.1,
        "exit_price": 1.39,
        "exit_time": "2026-03-12T09:47:00Z",
        "realized_pnl": -212.50,
        "hold_duration_minutes": 15.0,
        "exit_reason": "STOP_LOSS",
        "tranches_filled": 0,
        "total_round_trip_cost_bps": 18.5,
        "agent_signals": [
            {
                "agent_id": "news",
                "signal": "BULL",
                "confidence": 0.72,
                "reasoning": "FDA approval catalyst for phase 2 trial",
                "model_id": "qwen3.5-397b",
                "latency_ms": 1200.0,
                "catalyst_type": "FDA",
                "sentiment_score": 0.8,
            },
            {
                "agent_id": "deterministic_risk",
                "signal": "NEUTRAL",
                "confidence": 0.50,
                "reasoning": "Moderate risk profile, adequate liquidity",
                "model_id": "deterministic",
                "latency_ms": 0.5,
                "risk_verdict": "APPROVE",
                "risk_score": 0.35,
            },
            {
                "agent_id": "deterministic_technical",
                "signal": "BULL",
                "confidence": 0.60,
                "reasoning": "Gap above resistance, RSI not overbought",
                "model_id": "deterministic",
                "latency_ms": 0.8,
                "pattern_identified": "GAP_BREAKOUT",
            },
        ],
    }
    hold_entry = {
        "trade_id": "T_20260312_NVTS_1",
        "ticker": "NVTS",
        "timestamp": "2026-03-12T09:35:00Z",
        "session_date": "2026-03-12",
        "phase": "MARKET_OPEN",
        "action": "HOLD",
        "mfcs": 0.18,
        "risk_score": 0.60,
        "confidence": 0.40,
        "entry_price": 5.20,
        "stop_loss": 4.80,
        "gap_pct": 0.07,
        "rvol": 2.1,
        "current_price": 5.22,
        "position_size_pct": 0.0,
        "pipeline_latency_ms": 1800.0,
        "agent_signals": [
            {
                "agent_id": "news",
                "signal": "NEUTRAL",
                "confidence": 0.45,
                "reasoning": "No strong catalyst",
                "model_id": "qwen3.5-397b",
                "latency_ms": 1100.0,
            },
        ],
    }
    with open(journals_dir / "journal_2026-03-12_083000.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps(trade_entry) + "\n")
        f.write(json.dumps(hold_entry) + "\n")

    # Experiment journal
    exp_dir = tmp_path / "experiments"
    exp_dir.mkdir()
    exp_entry = {
        "trade_id": "T_20260312_PRSO_1",
        "ticker": "PRSO",
        "timestamp": "2026-03-12T09:32:15Z",
        "primary_mfcs": 0.42,
        "primary_action": "BUY",
        "primary_stop_loss": 1.39,
        "variant_results": [
            {
                "experiment_id": "atr_sweep",
                "variant_id": "atr_1.5",
                "overrides": {"execution.initial_stop_atr_multiplier": 1.5},
                "mfcs": 0.42,
                "would_enter": True,
                "stop_loss": 1.45,
                "position_size_qty": 960,
            },
            {
                "experiment_id": "atr_sweep",
                "variant_id": "atr_2.5",
                "overrides": {"execution.initial_stop_atr_multiplier": 2.5},
                "mfcs": 0.42,
                "would_enter": True,
                "stop_loss": 1.33,
                "position_size_qty": 700,
            },
            {
                "experiment_id": "threshold_sweep",
                "variant_id": "thresh_0.20",
                "overrides": {"scoring.mfcs_buy_threshold": 0.20},
                "mfcs": 0.42,
                "would_enter": True,
                "stop_loss": 1.39,
                "position_size_qty": 850,
            },
        ],
    }
    with open(exp_dir / "experiment_journal_2026-03-12.jsonl", "w", encoding="utf-8") as f:
        f.write(json.dumps(exp_entry) + "\n")

    # Signal history
    signals_dir = tmp_path / "signal_history"
    signals_dir.mkdir()
    signal_entries = [
        {
            "ts": "2026-03-12T09:35:00Z",
            "ticker": "PRSO",
            "composite": 0.15,
            "recommendation": "HOLD",
            "price": 1.68,
            "entry_price": 1.64,
            "pnl_pct": 2.4,
            "volume_fade": 0.10,
            "vwap_deterioration": 0.05,
            "spread_widening": 0.20,
            "time_decay": 0.08,
            "distribution": 0.0,
            "resistance_proximity": 0.30,
            "failed_breakout": 0.0,
            "churning": 0.05,
            "obv_divergence": 0.10,
            "volume_climax": 0.0,
            "momentum_degradation": 0.15,
            "flow_toxicity": 0.0,
            "distribution_detector": 0.12,
        },
        {
            "ts": "2026-03-12T09:40:00Z",
            "ticker": "PRSO",
            "composite": 0.45,
            "recommendation": "TIGHTEN",
            "price": 1.55,
            "entry_price": 1.64,
            "pnl_pct": -5.5,
            "volume_fade": 0.40,
            "vwap_deterioration": 0.55,
            "spread_widening": 0.35,
            "time_decay": 0.12,
            "distribution": 0.0,
            "resistance_proximity": 0.10,
            "failed_breakout": 0.50,
            "churning": 0.30,
            "obv_divergence": 0.45,
            "volume_climax": 0.10,
            "momentum_degradation": 0.60,
            "flow_toxicity": 0.0,
            "distribution_detector": 0.38,
        },
    ]
    with open(signals_dir / "signal_log_2026-03-12.jsonl", "w", encoding="utf-8") as f:
        for entry in signal_entries:
            f.write(json.dumps(entry) + "\n")

    # Metric snapshots
    metrics_dir = tmp_path / "metrics"
    metrics_dir.mkdir()
    snapshot = {
        "timestamp": "2026-03-12T10:00:00Z",
        "uptime_seconds": 5400.0,
        "pipeline": {
            "scan_iterations": 120.0,
            "candidates_found": 15.0,
            "evaluations_total": 8.0,
            "pipeline_latency_mean_s": 2.45,
        },
        "execution": {
            "orders_submitted": 3.0,
            "orders_filled": 2.0,
            "session_trades": 2.0,
        },
        "risk": {
            "daily_pnl": -150.25,
            "circuit_breaker_activations": 0.0,
            "risk_vetoes": 1.0,
        },
        "agents": {"errors_total": 0.0},
        "exit_events": {"stop_outs": 2.0, "smart_exits": 0.0},
        "phase_timing": {"phase3_cycles": 45.0},
        "data_completeness": {"overall_fill_rate": 0.85},
    }
    (metrics_dir / "metrics_20260312T100000.json").write_text(
        json.dumps(snapshot), encoding="utf-8"
    )

    return tmp_path


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_mx.db"


# ── Schema Tests ───────────────────────────────────────────────────


class TestSchema:
    """Verify database schema creation."""

    def test_init_db_creates_tables(self, db_path: Path) -> None:
        """All 6 tables + _etl_loaded_files are created."""
        conn = init_db(db_path)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = [t[0] for t in tables]

        assert "sessions" in table_names
        assert "trades" in table_names
        assert "agent_signals" in table_names
        assert "experiment_variants" in table_names
        assert "signal_history" in table_names
        assert "metric_snapshots" in table_names
        assert "_etl_loaded_files" in table_names
        conn.close()

    def test_init_db_creates_views(self, db_path: Path) -> None:
        """All 3 materialized views are created."""
        conn = init_db(db_path)
        views = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='view' ORDER BY name"
        ).fetchall()
        view_names = [v[0] for v in views]

        assert "v_ticker_stats" in view_names
        assert "v_rolling_performance" in view_names
        assert "v_exit_signal_efficacy" in view_names
        conn.close()

    def test_init_db_creates_indexes(self, db_path: Path) -> None:
        """Key indexes are created."""
        conn = init_db(db_path)
        indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
        ).fetchall()
        idx_names = [i[0] for i in indexes]

        assert "idx_trades_ticker" in idx_names
        assert "idx_trades_session" in idx_names
        assert "idx_agent_signals_agent" in idx_names
        assert "idx_agent_signals_trade" in idx_names
        assert "idx_exp_variant" in idx_names
        assert "idx_signals_ticker" in idx_names
        assert "idx_metrics_ts" in idx_names
        conn.close()

    def test_init_db_idempotent(self, db_path: Path) -> None:
        """Calling init_db twice doesn't error or duplicate tables."""
        conn1 = init_db(db_path)
        conn1.close()
        conn2 = init_db(db_path)
        tables = conn2.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()
        # 6 data tables + _etl_loaded_files + sqlite_sequence (autoincrement)
        assert tables[0] == 8
        conn2.close()

    def test_rebuild_db_clears_data(self, db_path: Path) -> None:
        """rebuild_db drops existing data."""
        conn = init_db(db_path)
        conn.execute(
            "INSERT INTO sessions (session_date) VALUES ('2026-01-01')"
        )
        conn.commit()
        conn.close()

        conn = rebuild_db(db_path)
        row = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
        assert row[0] == 0
        conn.close()


# ── Loader Tests ───────────────────────────────────────────────────


class TestLoaders:
    """Verify each data stream loader."""

    def test_load_sessions(self, db_path: Path, data_dir: Path) -> None:
        """Session reports load correctly."""
        conn = init_db(db_path)
        count = load_sessions(conn, data_dir)
        conn.commit()

        assert count == 1
        row = conn.execute(
            "SELECT session_date, daily_pnl, session_trades, mode FROM sessions"
        ).fetchone()
        assert row[0] == "2026-03-12"
        assert abs(row[1] - (-150.25)) < 0.01
        assert row[2] == 2
        assert row[3] == "paper"
        conn.close()

    def test_load_trades_and_agent_signals(
        self, db_path: Path, data_dir: Path
    ) -> None:
        """Trade journal loads trades + extracts agent signals."""
        conn = init_db(db_path)
        count = load_trades(conn, data_dir)
        conn.commit()

        assert count == 2  # Two journal entries

        # Check trade
        trade = conn.execute(
            "SELECT ticker, action, mfcs, exit_reason FROM trades "
            "WHERE trade_id = 'T_20260312_PRSO_1'"
        ).fetchone()
        assert trade[0] == "PRSO"
        assert trade[1] == "BUY"
        assert abs(trade[2] - 0.42) < 0.01
        assert trade[3] == "STOP_LOSS"

        # Check HOLD trade
        hold = conn.execute(
            "SELECT action, mfcs FROM trades "
            "WHERE trade_id = 'T_20260312_NVTS_1'"
        ).fetchone()
        assert hold[0] == "HOLD"

        # Check agent signals extracted
        agents = conn.execute(
            "SELECT agent_id, signal, confidence FROM agent_signals "
            "WHERE trade_id = 'T_20260312_PRSO_1' ORDER BY agent_id"
        ).fetchall()
        assert len(agents) == 3
        agent_ids = [a[0] for a in agents]
        assert "news" in agent_ids
        assert "deterministic_risk" in agent_ids
        assert "deterministic_technical" in agent_ids

        # Check risk agent specific fields
        risk = conn.execute(
            "SELECT risk_verdict, risk_score FROM agent_signals "
            "WHERE trade_id = 'T_20260312_PRSO_1' AND agent_id = 'deterministic_risk'"
        ).fetchone()
        assert risk[0] == "APPROVE"
        assert abs(risk[1] - 0.35) < 0.01

        conn.close()

    def test_load_experiments(self, db_path: Path, data_dir: Path) -> None:
        """Experiment journal loads variant results."""
        conn = init_db(db_path)
        count = load_experiments(conn, data_dir)
        conn.commit()

        assert count == 3  # 3 variants in the entry

        rows = conn.execute(
            "SELECT experiment_id, variant_id, variant_mfcs, would_enter "
            "FROM experiment_variants WHERE trade_id = 'T_20260312_PRSO_1' "
            "ORDER BY variant_id"
        ).fetchall()
        assert len(rows) == 3
        # Check ATR 1.5 variant
        atr_15 = [r for r in rows if r[1] == "atr_1.5"][0]
        assert atr_15[0] == "atr_sweep"
        assert atr_15[3] == 1  # would_enter = True

        conn.close()

    def test_load_signal_history(self, db_path: Path, data_dir: Path) -> None:
        """Signal history loads exit signal snapshots."""
        conn = init_db(db_path)
        count = load_signal_history(conn, data_dir)
        conn.commit()

        assert count == 2  # Two signal snapshots

        rows = conn.execute(
            "SELECT ticker, composite, recommendation, momentum_degradation "
            "FROM signal_history ORDER BY timestamp"
        ).fetchall()
        assert rows[0][0] == "PRSO"
        assert abs(rows[0][1] - 0.15) < 0.01
        assert rows[0][2] == "HOLD"
        assert rows[1][2] == "TIGHTEN"
        assert abs(rows[1][3] - 0.60) < 0.01

        conn.close()

    def test_load_metric_snapshots(self, db_path: Path, data_dir: Path) -> None:
        """Metric snapshots load flattened metrics."""
        conn = init_db(db_path)
        count = load_metric_snapshots(conn, data_dir)
        conn.commit()

        assert count == 1

        row = conn.execute(
            "SELECT timestamp, uptime_seconds, scan_iterations, daily_pnl, "
            "stop_outs, overall_fill_rate FROM metric_snapshots"
        ).fetchone()
        assert row[0] == "2026-03-12T10:00:00Z"
        assert abs(row[1] - 5400.0) < 0.01
        assert abs(row[2] - 120.0) < 0.01
        assert abs(row[3] - (-150.25)) < 0.01
        assert abs(row[4] - 2.0) < 0.01
        assert abs(row[5] - 0.85) < 0.01

        conn.close()


# ── Idempotency Tests ──────────────────────────────────────────────


class TestIdempotency:
    """ETL must be idempotent — running twice doesn't duplicate data."""

    def test_double_load_no_duplicates(
        self, db_path: Path, data_dir: Path
    ) -> None:
        """Running ETL twice produces the same row counts."""
        # First run
        results1 = run_etl(db_path=db_path, data_dir=data_dir, verbose=False)

        # Second run
        results2 = run_etl(db_path=db_path, data_dir=data_dir, verbose=False)

        # Second run should load 0 new rows (all files already tracked)
        assert results2["sessions"] == 0
        assert results2["trades"] == 0
        assert results2["experiments"] == 0
        assert results2["signal_history"] == 0
        assert results2["metric_snapshots"] == 0

        # Total row counts should be unchanged
        conn = sqlite3.connect(str(db_path))
        trades_count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        assert trades_count == 2  # Only 2 trades, not 4
        conn.close()


# ── View Tests ─────────────────────────────────────────────────────


class TestViews:
    """Verify materialized views produce correct results."""

    def test_v_ticker_stats(self, db_path: Path, data_dir: Path) -> None:
        """v_ticker_stats aggregates per-ticker data correctly."""
        run_etl(db_path=db_path, data_dir=data_dir, verbose=False)
        conn = sqlite3.connect(str(db_path))

        rows = conn.execute(
            "SELECT ticker, evaluations, buys, total_pnl FROM v_ticker_stats"
        ).fetchall()
        assert len(rows) >= 1

        # PRSO should have 1 evaluation, 1 buy, negative P&L
        prso = [r for r in rows if r[0] == "PRSO"]
        assert len(prso) == 1
        assert prso[0][1] == 1  # evaluations
        assert prso[0][2] == 1  # buys
        assert prso[0][3] < 0   # negative total_pnl

        conn.close()

    def test_v_rolling_performance(
        self, db_path: Path, data_dir: Path
    ) -> None:
        """v_rolling_performance shows session-by-session trend."""
        run_etl(db_path=db_path, data_dir=data_dir, verbose=False)
        conn = sqlite3.connect(str(db_path))

        rows = conn.execute(
            "SELECT session_date, session_trades, daily_pnl, cumulative_pnl "
            "FROM v_rolling_performance"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "2026-03-12"
        assert rows[0][1] == 2
        assert abs(rows[0][2] - (-150.25)) < 0.01

        conn.close()

    def test_v_exit_signal_efficacy(
        self, db_path: Path, data_dir: Path
    ) -> None:
        """v_exit_signal_efficacy shows per-ticker signal averages."""
        run_etl(db_path=db_path, data_dir=data_dir, verbose=False)
        conn = sqlite3.connect(str(db_path))

        rows = conn.execute(
            "SELECT ticker, signal_snapshots, avg_composite, "
            "exit_recommendations, tighten_recommendations, hold_recommendations "
            "FROM v_exit_signal_efficacy"
        ).fetchall()
        assert len(rows) == 1  # Only PRSO
        assert rows[0][0] == "PRSO"
        assert rows[0][1] == 2  # 2 signal snapshots
        assert rows[0][4] == 1  # 1 TIGHTEN
        assert rows[0][5] == 1  # 1 HOLD

        conn.close()


# ── Integrity Tests ────────────────────────────────────────────────


class TestIntegrity:
    """Verify database integrity checks."""

    def test_verify_passes_on_clean_db(
        self, db_path: Path, data_dir: Path
    ) -> None:
        """Verification passes on a correctly loaded database."""
        run_etl(db_path=db_path, data_dir=data_dir, verbose=False)
        ok = verify_integrity(db_path)
        assert ok is True

    def test_missing_data_dirs_handled_gracefully(
        self, db_path: Path, tmp_path: Path
    ) -> None:
        """ETL handles missing data directories without crashing."""
        empty_data = tmp_path / "empty_data"
        empty_data.mkdir()
        results = run_etl(
            db_path=db_path, data_dir=empty_data, verbose=False
        )
        # All counts should be 0
        assert all(v == 0 for v in results.values())


# ── Full Pipeline Test ─────────────────────────────────────────────


class TestFullPipeline:
    """End-to-end ETL pipeline test."""

    def test_full_etl_pipeline(self, db_path: Path, data_dir: Path) -> None:
        """Full pipeline loads all data streams and produces valid views."""
        results = run_etl(db_path=db_path, data_dir=data_dir, verbose=False)

        assert results["sessions"] == 1
        assert results["trades"] == 2
        assert results["experiments"] == 3
        assert results["signal_history"] == 2
        assert results["metric_snapshots"] == 1

        # Verify cross-referencing works
        conn = sqlite3.connect(str(db_path))

        # Join trades with agent_signals
        joined = conn.execute(
            "SELECT t.ticker, t.action, a.agent_id, a.signal "
            "FROM trades t "
            "JOIN agent_signals a ON t.trade_id = a.trade_id "
            "WHERE t.ticker = 'PRSO' "
            "ORDER BY a.agent_id"
        ).fetchall()
        assert len(joined) == 3  # 3 agents for PRSO

        # Join trades with experiment_variants
        variants = conn.execute(
            "SELECT t.ticker, e.experiment_id, e.variant_id, e.would_enter "
            "FROM trades t "
            "JOIN experiment_variants e ON t.trade_id = e.trade_id "
            "WHERE t.ticker = 'PRSO'"
        ).fetchall()
        assert len(variants) == 3  # 3 experiment variants for PRSO

        conn.close()

    def test_etl_with_rebuild(self, db_path: Path, data_dir: Path) -> None:
        """--rebuild flag clears and reloads everything."""
        # Load once
        run_etl(db_path=db_path, data_dir=data_dir, verbose=False)

        # Rebuild
        results = run_etl(
            db_path=db_path, data_dir=data_dir,
            rebuild=True, verbose=False,
        )
        assert results["sessions"] == 1  # Reloaded from scratch
        assert results["trades"] == 2

        # Verify no duplicates
        conn = sqlite3.connect(str(db_path))
        count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        assert count == 2
        conn.close()
