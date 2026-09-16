"""
MOMENTUM-X Post-Session ETL: JSONL → SQLite (D109 Phase 2)

Loads all JSONL/JSON data streams into a single SQLite database for
cross-referencing and analytical queries. Designed to run AFTER each
trading session ends — does NOT touch the hot path.

Data streams ingested:
  1. Trade Journal      → trades + agent_signals tables
  2. Experiment Journal → experiment_variants table
  3. Signal History     → signal_history table
  4. Metric Snapshots   → metric_snapshots table
  5. Session Reports    → sessions table

Usage:
  python scripts/etl_sqlite.py                    # Load all new data
  python scripts/etl_sqlite.py --rebuild          # Drop and rebuild from scratch
  python scripts/etl_sqlite.py --stats            # Show table counts
  python scripts/etl_sqlite.py --verify           # Verify integrity
  python scripts/etl_sqlite.py --query "SELECT ..." # Run arbitrary SQL query
  python scripts/etl_sqlite.py --time-travel "2026-03-10T10:30:00"  # System state at timestamp

All JSONL files are preserved — SQLite is additive, not a replacement.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
_DATA_DIR = _PROJECT_ROOT / "data"
_DB_PATH = _DATA_DIR / "mx.db"

# Force UTF-8 output on Windows
if sys.stdout and sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
if sys.stderr and sys.stderr.encoding != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]


# ── Schema ─────────────────────────────────────────────────────────

SCHEMA_SQL = """
-- D109 Phase 2: MOMENTUM-X Analytical Database
-- Post-session ETL target. Read-only analysis. JSONL preserved.
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- 1. Sessions: one row per trading session
CREATE TABLE IF NOT EXISTS sessions (
    session_date       TEXT PRIMARY KEY,
    session_start      TEXT,
    session_end        TEXT,
    duration_minutes   REAL,
    mode               TEXT,
    scan_iterations    INTEGER,
    candidates_found   INTEGER,
    evaluations_total  INTEGER,
    orders_submitted   INTEGER,
    orders_filled      INTEGER,
    session_trades     INTEGER,
    daily_pnl          REAL,
    realized_pnl_pm    REAL,
    win_count          INTEGER,
    loss_count         INTEGER,
    stop_outs          INTEGER,
    smart_exits        INTEGER,
    circuit_breaker_activations INTEGER,
    risk_vetoes        INTEGER,
    agent_errors       INTEGER,
    phase3_cycles      INTEGER,
    experiment_variants_tested INTEGER,
    source_file        TEXT
);

-- 2. Trades: one row per candidate evaluation (BUY, HOLD, or NO_TRADE)
CREATE TABLE IF NOT EXISTS trades (
    trade_id           TEXT PRIMARY KEY,
    ticker             TEXT NOT NULL,
    timestamp          TEXT NOT NULL,
    session_date       TEXT,
    phase              TEXT,
    action             TEXT,
    mfcs               REAL,
    risk_score         REAL,
    confidence         REAL,
    entry_price        REAL,
    stop_loss          REAL,
    gap_pct            REAL,
    rvol               REAL,
    current_price      REAL,
    position_size_pct  REAL,
    pipeline_latency_ms REAL,
    -- Outcome fields (filled post-trade)
    fill_price         REAL,
    fill_qty           INTEGER,
    slippage_bps       REAL,
    exit_price         REAL,
    exit_time          TEXT,
    realized_pnl       REAL,
    hold_duration_min  REAL,
    exit_reason        TEXT,
    tranches_filled    INTEGER,
    -- Transaction costs
    total_round_trip_cost_bps REAL,
    source_file        TEXT
);
CREATE INDEX IF NOT EXISTS idx_trades_ticker ON trades(ticker);
CREATE INDEX IF NOT EXISTS idx_trades_session ON trades(session_date);

-- 3. Agent signals: one row per agent per evaluation
CREATE TABLE IF NOT EXISTS agent_signals (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id           TEXT NOT NULL,
    agent_id           TEXT NOT NULL,
    signal             TEXT,
    confidence         REAL,
    reasoning          TEXT,
    model_id           TEXT,
    latency_ms         REAL,
    risk_verdict       TEXT,
    risk_score         REAL,
    catalyst_type      TEXT,
    sentiment_score    REAL,
    pattern_identified TEXT,
    FOREIGN KEY (trade_id) REFERENCES trades(trade_id)
);
CREATE INDEX IF NOT EXISTS idx_agent_signals_agent ON agent_signals(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_signals_trade ON agent_signals(trade_id);

-- 4. Experiment variants: one row per variant per evaluation
CREATE TABLE IF NOT EXISTS experiment_variants (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id           TEXT NOT NULL,
    ticker             TEXT NOT NULL,
    timestamp          TEXT,
    experiment_id      TEXT NOT NULL,
    variant_id         TEXT NOT NULL,
    primary_mfcs       REAL,
    primary_action     TEXT,
    variant_mfcs       REAL,
    would_enter        INTEGER,
    stop_loss          REAL,
    position_size_qty  INTEGER,
    overrides_json     TEXT,
    source_file        TEXT
);
CREATE INDEX IF NOT EXISTS idx_exp_variant ON experiment_variants(experiment_id, variant_id);
CREATE INDEX IF NOT EXISTS idx_exp_ticker ON experiment_variants(ticker);

-- 5. Signal history: one row per exit signal snapshot per position per cycle
CREATE TABLE IF NOT EXISTS signal_history (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp          TEXT NOT NULL,
    ticker             TEXT NOT NULL,
    composite          REAL,
    recommendation     TEXT,
    price              REAL,
    entry_price        REAL,
    pnl_pct            REAL,
    -- Individual signals
    volume_fade        REAL,
    vwap_deterioration REAL,
    spread_widening    REAL,
    time_decay         REAL,
    distribution       REAL,
    resistance_proximity REAL,
    failed_breakout    REAL,
    churning           REAL,
    obv_divergence     REAL,
    volume_climax      REAL,
    momentum_degradation REAL,
    flow_toxicity      REAL,
    distribution_detector REAL,
    source_file        TEXT
);
CREATE INDEX IF NOT EXISTS idx_signals_ticker ON signal_history(ticker);
CREATE INDEX IF NOT EXISTS idx_signals_ts ON signal_history(timestamp);

-- 6. Metric snapshots: one row per periodic snapshot
CREATE TABLE IF NOT EXISTS metric_snapshots (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp          TEXT NOT NULL,
    uptime_seconds     REAL,
    scan_iterations    REAL,
    candidates_found   REAL,
    evaluations_total  REAL,
    pipeline_latency_mean_s REAL,
    orders_submitted   REAL,
    orders_filled      REAL,
    session_trades     REAL,
    daily_pnl          REAL,
    circuit_breaker_activations REAL,
    risk_vetoes        REAL,
    agent_errors       REAL,
    stop_outs          REAL,
    smart_exits        REAL,
    phase3_cycles      REAL,
    overall_fill_rate  REAL,
    source_file        TEXT
);
CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metric_snapshots(timestamp);

-- Metadata: track which files have been loaded
CREATE TABLE IF NOT EXISTS _etl_loaded_files (
    file_path          TEXT PRIMARY KEY,
    loaded_at          TEXT NOT NULL,
    row_count          INTEGER
);
"""

# ── Materialized Views ─────────────────────────────────────────────

VIEWS_SQL = """
-- View 1: Per-ticker statistics (which tickers perform best?)
DROP VIEW IF EXISTS v_ticker_stats;
CREATE VIEW v_ticker_stats AS
SELECT
    ticker,
    COUNT(*) AS evaluations,
    SUM(CASE WHEN action = 'BUY' THEN 1 ELSE 0 END) AS buys,
    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins,
    SUM(CASE WHEN realized_pnl <= 0 AND realized_pnl IS NOT NULL THEN 1 ELSE 0 END) AS losses,
    ROUND(AVG(mfcs), 3) AS avg_mfcs,
    ROUND(AVG(CASE WHEN realized_pnl IS NOT NULL THEN realized_pnl END), 2) AS avg_pnl,
    ROUND(SUM(CASE WHEN realized_pnl IS NOT NULL THEN realized_pnl ELSE 0 END), 2) AS total_pnl,
    ROUND(AVG(gap_pct), 3) AS avg_gap_pct,
    ROUND(AVG(hold_duration_min), 1) AS avg_hold_min,
    ROUND(AVG(slippage_bps), 1) AS avg_slippage_bps
FROM trades
GROUP BY ticker
ORDER BY total_pnl DESC;

-- View 2: Rolling performance by session (trend over time)
DROP VIEW IF EXISTS v_rolling_performance;
CREATE VIEW v_rolling_performance AS
SELECT
    s.session_date,
    s.session_trades,
    s.daily_pnl,
    s.realized_pnl_pm,
    s.win_count,
    s.loss_count,
    CASE WHEN (s.win_count + s.loss_count) > 0
         THEN ROUND(s.win_count * 100.0 / (s.win_count + s.loss_count), 1)
         ELSE 0 END AS win_rate_pct,
    s.stop_outs,
    s.smart_exits,
    s.candidates_found,
    s.evaluations_total,
    s.circuit_breaker_activations,
    s.duration_minutes,
    -- Running totals
    SUM(s.daily_pnl) OVER (ORDER BY s.session_date) AS cumulative_pnl,
    SUM(s.session_trades) OVER (ORDER BY s.session_date) AS cumulative_trades
FROM sessions s
ORDER BY s.session_date;

-- View 3: Exit signal efficacy (which signals are useful?)
DROP VIEW IF EXISTS v_exit_signal_efficacy;
CREATE VIEW v_exit_signal_efficacy AS
SELECT
    ticker,
    COUNT(*) AS signal_snapshots,
    ROUND(AVG(composite), 3) AS avg_composite,
    -- Per-signal average (higher = more frequently firing)
    ROUND(AVG(volume_fade), 3) AS avg_volume_fade,
    ROUND(AVG(vwap_deterioration), 3) AS avg_vwap_deterioration,
    ROUND(AVG(spread_widening), 3) AS avg_spread_widening,
    ROUND(AVG(time_decay), 3) AS avg_time_decay,
    ROUND(AVG(resistance_proximity), 3) AS avg_resistance_proximity,
    ROUND(AVG(failed_breakout), 3) AS avg_failed_breakout,
    ROUND(AVG(churning), 3) AS avg_churning,
    ROUND(AVG(obv_divergence), 3) AS avg_obv_divergence,
    ROUND(AVG(volume_climax), 3) AS avg_volume_climax,
    ROUND(AVG(momentum_degradation), 3) AS avg_momentum_degradation,
    ROUND(AVG(distribution_detector), 3) AS avg_distribution_detector,
    -- How often did composite cross thresholds?
    SUM(CASE WHEN recommendation = 'EXIT' THEN 1 ELSE 0 END) AS exit_recommendations,
    SUM(CASE WHEN recommendation = 'TIGHTEN' THEN 1 ELSE 0 END) AS tighten_recommendations,
    SUM(CASE WHEN recommendation = 'HOLD' THEN 1 ELSE 0 END) AS hold_recommendations
FROM signal_history
GROUP BY ticker
ORDER BY avg_composite DESC;
"""


# ── ETL Loaders ────────────────────────────────────────────────────

def _is_loaded(conn: sqlite3.Connection, file_path: str) -> bool:
    """Check if a file has already been loaded."""
    row = conn.execute(
        "SELECT 1 FROM _etl_loaded_files WHERE file_path = ?",
        (file_path,),
    ).fetchone()
    return row is not None


def _mark_loaded(conn: sqlite3.Connection, file_path: str, row_count: int) -> None:
    """Mark a file as loaded."""
    conn.execute(
        "INSERT OR REPLACE INTO _etl_loaded_files (file_path, loaded_at, row_count) "
        "VALUES (?, ?, ?)",
        (file_path, datetime.now(timezone.utc).isoformat(), row_count),
    )


def _read_jsonl(path: Path) -> list[dict]:
    """Read a JSONL file, skipping malformed lines."""
    entries = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass
    return entries


def _read_json(path: Path) -> dict | None:
    """Read a single JSON file."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _get(d: dict, *keys, default=None):
    """Safely get nested dict value."""
    current = d
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key, default)
        else:
            return default
    return current


def load_sessions(conn: sqlite3.Connection, data_dir: Path) -> int:
    """Load session report JSON files into sessions table."""
    reports_dir = data_dir / "session_reports"
    if not reports_dir.exists():
        return 0

    loaded = 0
    for path in sorted(reports_dir.glob("session_*.json")):
        rel = str(path.relative_to(data_dir))
        if _is_loaded(conn, rel):
            continue

        report = _read_json(path)
        if not report:
            continue

        session_date = report.get("session_date", "")
        if not session_date:
            continue

        try:
            conn.execute(
                """INSERT OR REPLACE INTO sessions (
                    session_date, session_start, session_end, duration_minutes,
                    mode, scan_iterations, candidates_found, evaluations_total,
                    orders_submitted, orders_filled, session_trades,
                    daily_pnl, realized_pnl_pm, win_count, loss_count,
                    stop_outs, smart_exits, circuit_breaker_activations,
                    risk_vetoes, agent_errors, phase3_cycles,
                    experiment_variants_tested, source_file
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_date,
                    report.get("session_start"),
                    report.get("session_end"),
                    report.get("duration_minutes"),
                    report.get("mode"),
                    report.get("scan_iterations"),
                    report.get("candidates_found"),
                    report.get("evaluations_total"),
                    report.get("orders_submitted"),
                    report.get("orders_filled"),
                    report.get("session_trades"),
                    report.get("daily_pnl"),
                    report.get("realized_pnl_pm"),
                    report.get("win_count"),
                    report.get("loss_count"),
                    report.get("stop_outs"),
                    report.get("smart_exits"),
                    report.get("circuit_breaker_activations"),
                    report.get("risk_vetoes"),
                    report.get("agent_errors"),
                    report.get("phase3_cycles"),
                    report.get("experiment_variants_tested"),
                    rel,
                ),
            )
            _mark_loaded(conn, rel, 1)
            loaded += 1
        except sqlite3.IntegrityError:
            pass

    return loaded


def load_trades(conn: sqlite3.Connection, data_dir: Path) -> int:
    """Load trade journal JSONL files into trades + agent_signals tables."""
    journals_dir = data_dir / "journals"
    if not journals_dir.exists():
        return 0

    loaded = 0
    for path in sorted(journals_dir.glob("journal_*.jsonl")):
        rel = str(path.relative_to(data_dir))
        if _is_loaded(conn, rel):
            continue

        entries = _read_jsonl(path)
        if not entries:
            continue

        row_count = 0
        for entry in entries:
            trade_id = entry.get("trade_id", "")
            if not trade_id:
                continue

            try:
                conn.execute(
                    """INSERT OR IGNORE INTO trades (
                        trade_id, ticker, timestamp, session_date, phase,
                        action, mfcs, risk_score, confidence,
                        entry_price, stop_loss, gap_pct, rvol,
                        current_price, position_size_pct, pipeline_latency_ms,
                        fill_price, fill_qty, slippage_bps,
                        exit_price, exit_time, realized_pnl,
                        hold_duration_min, exit_reason, tranches_filled,
                        total_round_trip_cost_bps, source_file
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        trade_id,
                        entry.get("ticker"),
                        entry.get("timestamp"),
                        entry.get("session_date"),
                        entry.get("phase"),
                        entry.get("action"),
                        entry.get("mfcs"),
                        entry.get("risk_score"),
                        entry.get("confidence"),
                        entry.get("entry_price"),
                        entry.get("stop_loss"),
                        entry.get("gap_pct"),
                        entry.get("rvol"),
                        entry.get("current_price"),
                        entry.get("position_size_pct"),
                        entry.get("pipeline_latency_ms"),
                        entry.get("fill_price"),
                        entry.get("fill_qty"),
                        entry.get("slippage_bps"),
                        entry.get("exit_price"),
                        entry.get("exit_time"),
                        entry.get("realized_pnl"),
                        entry.get("hold_duration_minutes"),
                        entry.get("exit_reason"),
                        entry.get("tranches_filled"),
                        entry.get("total_round_trip_cost_bps"),
                        rel,
                    ),
                )
                row_count += 1

                # Extract agent signals
                for agent in entry.get("agent_signals", []):
                    conn.execute(
                        """INSERT INTO agent_signals (
                            trade_id, agent_id, signal, confidence,
                            reasoning, model_id, latency_ms,
                            risk_verdict, risk_score,
                            catalyst_type, sentiment_score,
                            pattern_identified
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            trade_id,
                            agent.get("agent_id"),
                            agent.get("signal"),
                            agent.get("confidence"),
                            agent.get("reasoning"),
                            agent.get("model_id"),
                            agent.get("latency_ms"),
                            agent.get("risk_verdict"),
                            agent.get("risk_score"),
                            agent.get("catalyst_type"),
                            agent.get("sentiment_score"),
                            agent.get("pattern_identified"),
                        ),
                    )

            except sqlite3.IntegrityError:
                continue

        _mark_loaded(conn, rel, row_count)
        loaded += row_count

    return loaded


def load_experiments(conn: sqlite3.Connection, data_dir: Path) -> int:
    """Load experiment journal JSONL files into experiment_variants table."""
    exp_dir = data_dir / "experiments"
    if not exp_dir.exists():
        return 0

    loaded = 0
    for path in sorted(exp_dir.glob("experiment_journal_*.jsonl")):
        rel = str(path.relative_to(data_dir))
        if _is_loaded(conn, rel):
            continue

        entries = _read_jsonl(path)
        if not entries:
            continue

        row_count = 0
        for entry in entries:
            trade_id = entry.get("trade_id", "")
            ticker = entry.get("ticker", "")
            timestamp = entry.get("timestamp", "")
            primary_mfcs = entry.get("primary_mfcs")
            primary_action = entry.get("primary_action")

            for variant in entry.get("variant_results", []):
                try:
                    conn.execute(
                        """INSERT INTO experiment_variants (
                            trade_id, ticker, timestamp,
                            experiment_id, variant_id,
                            primary_mfcs, primary_action,
                            variant_mfcs, would_enter,
                            stop_loss, position_size_qty,
                            overrides_json, source_file
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            trade_id,
                            ticker,
                            timestamp,
                            variant.get("experiment_id"),
                            variant.get("variant_id"),
                            primary_mfcs,
                            primary_action,
                            variant.get("mfcs"),
                            1 if variant.get("would_enter") else 0,
                            variant.get("stop_loss"),
                            variant.get("position_size_qty"),
                            json.dumps(variant.get("overrides", {})),
                            rel,
                        ),
                    )
                    row_count += 1
                except sqlite3.IntegrityError:
                    continue

        _mark_loaded(conn, rel, row_count)
        loaded += row_count

    return loaded


def load_signal_history(conn: sqlite3.Connection, data_dir: Path) -> int:
    """Load signal history JSONL files into signal_history table."""
    signals_dir = data_dir / "signal_history"
    if not signals_dir.exists():
        return 0

    loaded = 0
    for path in sorted(signals_dir.glob("signal_log_*.jsonl")):
        rel = str(path.relative_to(data_dir))
        if _is_loaded(conn, rel):
            continue

        entries = _read_jsonl(path)
        if not entries:
            continue

        row_count = 0
        for entry in entries:
            try:
                conn.execute(
                    """INSERT INTO signal_history (
                        timestamp, ticker, composite, recommendation,
                        price, entry_price, pnl_pct,
                        volume_fade, vwap_deterioration, spread_widening,
                        time_decay, distribution, resistance_proximity,
                        failed_breakout, churning, obv_divergence,
                        volume_climax, momentum_degradation, flow_toxicity,
                        distribution_detector, source_file
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        entry.get("ts"),
                        entry.get("ticker"),
                        entry.get("composite"),
                        entry.get("recommendation"),
                        entry.get("price"),
                        entry.get("entry_price"),
                        entry.get("pnl_pct"),
                        entry.get("volume_fade"),
                        entry.get("vwap_deterioration"),
                        entry.get("spread_widening"),
                        entry.get("time_decay"),
                        entry.get("distribution"),
                        entry.get("resistance_proximity"),
                        entry.get("failed_breakout"),
                        entry.get("churning"),
                        entry.get("obv_divergence"),
                        entry.get("volume_climax"),
                        entry.get("momentum_degradation"),
                        entry.get("flow_toxicity"),
                        entry.get("distribution_detector"),
                        rel,
                    ),
                )
                row_count += 1
            except sqlite3.IntegrityError:
                continue

        _mark_loaded(conn, rel, row_count)
        loaded += row_count

    return loaded


def load_metric_snapshots(conn: sqlite3.Connection, data_dir: Path) -> int:
    """Load metric snapshot JSON files into metric_snapshots table."""
    metrics_dir = data_dir / "metrics"
    if not metrics_dir.exists():
        return 0

    loaded = 0
    for path in sorted(metrics_dir.glob("metrics_*.json")):
        rel = str(path.relative_to(data_dir))
        if _is_loaded(conn, rel):
            continue

        snap = _read_json(path)
        if not snap:
            continue

        try:
            conn.execute(
                """INSERT INTO metric_snapshots (
                    timestamp, uptime_seconds,
                    scan_iterations, candidates_found, evaluations_total,
                    pipeline_latency_mean_s,
                    orders_submitted, orders_filled, session_trades,
                    daily_pnl, circuit_breaker_activations, risk_vetoes,
                    agent_errors, stop_outs, smart_exits, phase3_cycles,
                    overall_fill_rate, source_file
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    snap.get("timestamp"),
                    snap.get("uptime_seconds"),
                    _get(snap, "pipeline", "scan_iterations"),
                    _get(snap, "pipeline", "candidates_found"),
                    _get(snap, "pipeline", "evaluations_total"),
                    _get(snap, "pipeline", "pipeline_latency_mean_s"),
                    _get(snap, "execution", "orders_submitted"),
                    _get(snap, "execution", "orders_filled"),
                    _get(snap, "execution", "session_trades"),
                    _get(snap, "risk", "daily_pnl"),
                    _get(snap, "risk", "circuit_breaker_activations"),
                    _get(snap, "risk", "risk_vetoes"),
                    _get(snap, "agents", "errors_total"),
                    _get(snap, "exit_events", "stop_outs"),
                    _get(snap, "exit_events", "smart_exits"),
                    _get(snap, "phase_timing", "phase3_cycles"),
                    _get(snap, "data_completeness", "overall_fill_rate"),
                    rel,
                ),
            )
            _mark_loaded(conn, rel, 1)
            loaded += 1
        except sqlite3.IntegrityError:
            pass

    return loaded


# ── Database Management ────────────────────────────────────────────

def init_db(db_path: Path) -> sqlite3.Connection:
    """Create or open the database with schema."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_SQL)
    conn.executescript(VIEWS_SQL)
    conn.commit()
    return conn


def rebuild_db(db_path: Path) -> sqlite3.Connection:
    """Drop and recreate the database from scratch."""
    if db_path.exists():
        db_path.unlink()
    return init_db(db_path)


def run_etl(
    db_path: Path = _DB_PATH,
    data_dir: Path = _DATA_DIR,
    rebuild: bool = False,
    verbose: bool = True,
) -> dict[str, int]:
    """Run the full ETL pipeline."""
    if rebuild:
        conn = rebuild_db(db_path)
        if verbose:
            print(f"  Database rebuilt: {db_path}")
    else:
        conn = init_db(db_path)

    results = {}

    if verbose:
        print(f"\n{'='*60}")
        print(f"  MOMENTUM-X ETL: JSONL → SQLite")
        print(f"  Database: {db_path}")
        print(f"  Data dir: {data_dir}")
        print(f"{'='*60}\n")

    # Load each data stream
    loaders = [
        ("sessions", load_sessions),
        ("trades", load_trades),
        ("experiments", load_experiments),
        ("signal_history", load_signal_history),
        ("metric_snapshots", load_metric_snapshots),
    ]

    for name, loader in loaders:
        count = loader(conn, data_dir)
        results[name] = count
        if verbose:
            status = f"+{count} new rows" if count > 0 else "up to date"
            print(f"  {name:20s}: {status}")

    conn.commit()

    # Print totals
    if verbose:
        print(f"\n{'─'*60}")
        print(f"  TABLE COUNTS:")
        for table in ["sessions", "trades", "agent_signals",
                       "experiment_variants", "signal_history", "metric_snapshots"]:
            row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            print(f"    {table:25s}: {row[0]:,d} rows")

        loaded = conn.execute("SELECT COUNT(*) FROM _etl_loaded_files").fetchone()
        print(f"    {'_etl_loaded_files':25s}: {loaded[0]:,d} files tracked")
        print(f"{'='*60}\n")

    conn.close()
    return results


def show_stats(db_path: Path = _DB_PATH) -> None:
    """Print database statistics."""
    if not db_path.exists():
        print(f"  Database not found: {db_path}")
        print(f"  Run: python scripts/etl_sqlite.py")
        return

    conn = sqlite3.connect(str(db_path))

    print(f"\n{'='*60}")
    print(f"  MOMENTUM-X DATABASE STATS")
    print(f"  {db_path}")
    print(f"{'='*60}\n")

    for table in ["sessions", "trades", "agent_signals",
                   "experiment_variants", "signal_history", "metric_snapshots"]:
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        print(f"  {table:25s}: {row[0]:,d} rows")

    # Quick analytical queries
    print(f"\n{'─'*60}")

    # Session summary
    row = conn.execute(
        "SELECT MIN(session_date), MAX(session_date), "
        "SUM(session_trades), SUM(daily_pnl) FROM sessions"
    ).fetchone()
    if row and row[0]:
        print(f"  Sessions: {row[0]} to {row[1]}")
        print(f"  Total trades: {row[2] or 0}")
        print(f"  Total P&L: ${row[3] or 0:,.2f}")

    # Ticker breakdown from view
    print(f"\n  TOP TICKERS (by total P&L):")
    rows = conn.execute(
        "SELECT ticker, buys, wins, losses, avg_mfcs, total_pnl "
        "FROM v_ticker_stats WHERE buys > 0 LIMIT 10"
    ).fetchall()
    if rows:
        print(f"  {'Ticker':8s} {'Buys':>5s} {'Wins':>5s} {'Loss':>5s} "
              f"{'MFCS':>6s} {'Total P&L':>10s}")
        for r in rows:
            print(f"  {r[0]:8s} {r[1]:5d} {r[2]:5d} {r[3]:5d} "
                  f"{r[4]:6.3f} ${r[5]:9,.2f}")
    else:
        print("  (no trade data yet)")

    print(f"{'='*60}\n")
    conn.close()


def verify_integrity(db_path: Path = _DB_PATH) -> bool:
    """Run integrity checks on the database."""
    if not db_path.exists():
        print(f"  Database not found: {db_path}")
        return False

    conn = sqlite3.connect(str(db_path))
    ok = True

    print(f"\n{'='*60}")
    print(f"  INTEGRITY VERIFICATION")
    print(f"{'='*60}\n")

    # 1. SQLite integrity check
    row = conn.execute("PRAGMA integrity_check").fetchone()
    status = "PASS" if row[0] == "ok" else "FAIL"
    print(f"  SQLite integrity:     {status}")
    if row[0] != "ok":
        ok = False

    # 2. Foreign key check (agent_signals → trades)
    orphans = conn.execute(
        "SELECT COUNT(*) FROM agent_signals "
        "WHERE trade_id NOT IN (SELECT trade_id FROM trades)"
    ).fetchone()
    status = "PASS" if orphans[0] == 0 else f"FAIL ({orphans[0]} orphans)"
    print(f"  FK agent_signals:     {status}")
    if orphans[0] > 0:
        ok = False

    # 3. No NULL trade_ids
    nulls = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE trade_id IS NULL OR trade_id = ''"
    ).fetchone()
    status = "PASS" if nulls[0] == 0 else f"FAIL ({nulls[0]} nulls)"
    print(f"  Non-null trade_ids:   {status}")
    if nulls[0] > 0:
        ok = False

    # 4. Views exist and are queryable
    for view in ["v_ticker_stats", "v_rolling_performance", "v_exit_signal_efficacy"]:
        try:
            conn.execute(f"SELECT * FROM {view} LIMIT 1")
            print(f"  View {view:30s}: PASS")
        except Exception as e:
            print(f"  View {view:30s}: FAIL ({e})")
            ok = False

    print(f"\n  Overall: {'ALL CHECKS PASSED' if ok else 'SOME CHECKS FAILED'}")
    print(f"{'='*60}\n")

    conn.close()
    return ok


# ── CLI Query Interface (D109 Phase 3) ────────────────────────────


def run_query(
    sql: str,
    db_path: Path = _DB_PATH,
    fmt: str = "table",
    limit: int = 100,
) -> list[tuple]:
    """Execute an arbitrary SQL query and display results.

    This is an offline CLI tool — SQL comes directly from the operator's
    command line (like ``sqlite3 data/mx.db``).  There is no web interface
    or untrusted input path, so parameterisation is unnecessary.

    Args:
        sql: SQL statement to execute.
        db_path: Path to the SQLite database.
        fmt: Output format — "table" (default), "csv", or "json".
        limit: Maximum rows to display.

    Returns:
        List of result tuples.
    """
    if not db_path.exists():
        print(f"  Database not found: {db_path}")
        print(f"  Run: python scripts/etl_sqlite.py")
        return []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    try:
        cursor = conn.execute(sql)
        rows = cursor.fetchmany(limit)

        if not rows:
            print("  (no results)")
            return []

        columns = [desc[0] for desc in cursor.description]

        if fmt == "json":
            import json as _json
            result = [dict(zip(columns, row)) for row in rows]
            print(_json.dumps(result, indent=2, default=str))
        elif fmt == "csv":
            print(",".join(columns))
            for row in rows:
                print(",".join(str(v) if v is not None else "" for v in row))
        else:
            # Table format
            col_widths = [len(c) for c in columns]
            for row in rows:
                for i, val in enumerate(row):
                    col_widths[i] = max(col_widths[i], len(str(val) if val is not None else ""))

            # Cap column widths at 40
            col_widths = [min(w, 40) for w in col_widths]

            # Header
            header = "  ".join(f"{c:<{col_widths[i]}}" for i, c in enumerate(columns))
            print(f"\n  {header}")
            print(f"  {'─' * len(header)}")

            # Rows
            for row in rows:
                vals = []
                for i, val in enumerate(row):
                    s = str(val) if val is not None else ""
                    if len(s) > col_widths[i]:
                        s = s[:col_widths[i] - 2] + ".."
                    vals.append(f"{s:<{col_widths[i]}}")
                print(f"  {'  '.join(vals)}")

            remaining = cursor.fetchone()
            if remaining:
                print(f"\n  (showing first {limit} rows; use --limit N to increase)")

        return [tuple(r) for r in rows]
    except sqlite3.OperationalError as e:
        print(f"  SQL Error: {e}")
        return []
    finally:
        conn.close()


# ── Time-Travel Queries (D109 Phase 3) ────────────────────────────


def positions_at(
    timestamp: str,
    db_path: Path = _DB_PATH,
) -> list[dict]:
    """Return active positions at a given timestamp.

    Finds trades that were entered before *timestamp* and not yet exited
    (or exited after *timestamp*). Uses trade journal timestamps and
    exit_time to determine position lifecycle.

    Args:
        timestamp: ISO 8601 timestamp (e.g., "2026-03-10T10:30:00").
        db_path: Path to the SQLite database.

    Returns:
        List of dicts with position details.
    """
    if not db_path.exists():
        return []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Active positions: entered before timestamp, exit after or no exit
    rows = conn.execute(
        """
        SELECT
            trade_id, ticker, timestamp AS entry_timestamp,
            action, mfcs, entry_price, stop_loss, gap_pct, rvol,
            fill_price, fill_qty, exit_price, exit_time,
            realized_pnl, exit_reason, session_date
        FROM trades
        WHERE action = 'BUY'
          AND timestamp <= ?
          AND (exit_time IS NULL OR exit_time > ?)
        ORDER BY timestamp
        """,
        (timestamp, timestamp),
    ).fetchall()

    result = [dict(row) for row in rows]
    conn.close()
    return result


def system_state_at(
    timestamp: str,
    db_path: Path = _DB_PATH,
) -> dict:
    """Reconstruct approximate system state at a given timestamp.

    Correlates positions, latest metric snapshot, and active signals
    to provide a snapshot of what the system was doing at that moment.

    Args:
        timestamp: ISO 8601 timestamp.
        db_path: Path to the SQLite database.

    Returns:
        Dict with positions, metrics, signals, and session context.
    """
    if not db_path.exists():
        return {}

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    state: dict = {"timestamp": timestamp}

    # 1. Active positions at that time
    state["positions"] = positions_at(timestamp, db_path)

    # 2. Most recent metric snapshot before timestamp
    metric_row = conn.execute(
        """
        SELECT * FROM metric_snapshots
        WHERE timestamp <= ?
        ORDER BY timestamp DESC
        LIMIT 1
        """,
        (timestamp,),
    ).fetchone()
    state["metrics"] = dict(metric_row) if metric_row else None

    # 3. Active exit signals around that time (±5 min window)
    # SQLite datetime() works with space-separated timestamps
    ts_normalized = timestamp.replace("T", " ")
    signals = conn.execute(
        """
        SELECT ticker, timestamp, composite, recommendation,
               price, entry_price, pnl_pct
        FROM signal_history
        WHERE REPLACE(timestamp, 'T', ' ') BETWEEN
            datetime(?, '-5 minutes') AND datetime(?, '+5 minutes')
        ORDER BY timestamp
        """,
        (ts_normalized, ts_normalized),
    ).fetchall()
    state["signals"] = [dict(s) for s in signals]

    # 4. Session context
    # Extract date part for session lookup
    date_part = timestamp[:10]  # "YYYY-MM-DD"
    session_row = conn.execute(
        "SELECT * FROM sessions WHERE session_date = ?",
        (date_part,),
    ).fetchone()
    state["session"] = dict(session_row) if session_row else None

    # 5. Trades taken in the same session up to this point
    trades_before = conn.execute(
        """
        SELECT trade_id, ticker, timestamp, action, mfcs,
               entry_price, realized_pnl, exit_reason
        FROM trades
        WHERE session_date = ? AND timestamp <= ?
        ORDER BY timestamp
        """,
        (date_part, timestamp),
    ).fetchall()
    state["trades_this_session"] = [dict(t) for t in trades_before]

    conn.close()
    return state


def show_time_travel(timestamp: str, db_path: Path = _DB_PATH) -> None:
    """Display formatted time-travel state for a timestamp."""
    state = system_state_at(timestamp, db_path)
    if not state:
        print(f"  No data available (database not found)")
        return

    print(f"\n{'='*70}")
    print(f"  TIME-TRAVEL: System state at {timestamp}")
    print(f"{'='*70}")

    # Session context
    sess = state.get("session")
    if sess:
        print(f"\n  SESSION: {sess.get('session_date')}")
        print(f"    Mode: {sess.get('mode')}  |  Trades: {sess.get('session_trades')}")
        print(f"    Daily P&L: ${sess.get('daily_pnl', 0) or 0:,.2f}")
        print(f"    Candidates: {sess.get('candidates_found')}  |  "
              f"Evaluations: {sess.get('evaluations_total')}")
    else:
        print(f"\n  SESSION: (no session data for {timestamp[:10]})")

    # Active positions
    positions = state.get("positions", [])
    print(f"\n  ACTIVE POSITIONS: {len(positions)}")
    if positions:
        print(f"    {'Ticker':8s} {'Entry':>8s} {'Stop':>8s} {'MFCS':>6s} {'Gap%':>6s}")
        print(f"    {'─'*38}")
        for p in positions:
            print(f"    {p.get('ticker', ''):8s} "
                  f"${p.get('entry_price', 0) or 0:7.2f} "
                  f"${p.get('stop_loss', 0) or 0:7.2f} "
                  f"{p.get('mfcs', 0) or 0:5.3f} "
                  f"{(p.get('gap_pct', 0) or 0)*100:5.1f}%")

    # Metrics
    metrics = state.get("metrics")
    if metrics:
        print(f"\n  LATEST METRICS (as of {metrics.get('timestamp', '?')}):")
        print(f"    Uptime: {metrics.get('uptime_seconds', 0) or 0:.0f}s  |  "
              f"Scans: {metrics.get('scan_iterations', 0) or 0:.0f}  |  "
              f"Evals: {metrics.get('evaluations_total', 0) or 0:.0f}")
        print(f"    Orders: {metrics.get('orders_submitted', 0) or 0:.0f} submitted, "
              f"{metrics.get('orders_filled', 0) or 0:.0f} filled  |  "
              f"P&L: ${metrics.get('daily_pnl', 0) or 0:,.2f}")
    else:
        print(f"\n  METRICS: (no snapshot available)")

    # Active signals
    signals = state.get("signals", [])
    if signals:
        print(f"\n  EXIT SIGNALS (±5 min window): {len(signals)}")
        print(f"    {'Ticker':8s} {'Composite':>10s} {'Rec':>8s} {'P&L%':>7s}")
        print(f"    {'─'*35}")
        for s in signals:
            print(f"    {s.get('ticker', ''):8s} "
                  f"{s.get('composite', 0) or 0:9.3f} "
                  f"{s.get('recommendation', ''):>8s} "
                  f"{s.get('pnl_pct', 0) or 0:+6.2f}%")

    # Trades this session
    session_trades = state.get("trades_this_session", [])
    if session_trades:
        print(f"\n  TRADES THIS SESSION (up to timestamp): {len(session_trades)}")
        for t in session_trades:
            action = t.get("action", "")
            pnl = t.get("realized_pnl")
            pnl_str = f"${pnl:+,.2f}" if pnl is not None else "open"
            print(f"    {t.get('ticker', ''):8s} {action:10s} "
                  f"MFCS={t.get('mfcs', 0) or 0:.3f}  {pnl_str}")

    print(f"\n{'='*70}\n")


# ── CLI ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="MOMENTUM-X Post-Session ETL: JSONL → SQLite"
    )
    parser.add_argument(
        "--rebuild", action="store_true",
        help="Drop and rebuild the database from scratch",
    )
    parser.add_argument(
        "--stats", action="store_true",
        help="Show database statistics and exit",
    )
    parser.add_argument(
        "--verify", action="store_true",
        help="Run integrity verification and exit",
    )
    parser.add_argument(
        "--db", type=str, default=None,
        help=f"Database path (default: {_DB_PATH})",
    )
    parser.add_argument(
        "--data-dir", type=str, default=None,
        help=f"Data directory (default: {_DATA_DIR})",
    )
    # D109 Phase 3: Query interface
    parser.add_argument(
        "--query", type=str, default=None,
        help='Run an arbitrary SQL query (e.g., --query "SELECT * FROM trades LIMIT 5")',
    )
    parser.add_argument(
        "--format", type=str, default="table", choices=["table", "csv", "json"],
        help="Output format for --query (default: table)",
    )
    parser.add_argument(
        "--limit", type=int, default=100,
        help="Max rows to display for --query (default: 100)",
    )
    # D109 Phase 3: Time-travel
    parser.add_argument(
        "--time-travel", type=str, default=None,
        help='Show system state at timestamp (e.g., --time-travel "2026-03-10T10:30:00")',
    )
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else _DB_PATH
    data_dir = Path(args.data_dir) if args.data_dir else _DATA_DIR

    if args.query:
        run_query(args.query, db_path=db_path, fmt=args.format, limit=args.limit)
    elif args.time_travel:
        show_time_travel(args.time_travel, db_path=db_path)
    elif args.stats:
        show_stats(db_path)
    elif args.verify:
        ok = verify_integrity(db_path)
        sys.exit(0 if ok else 1)
    else:
        run_etl(db_path=db_path, data_dir=data_dir, rebuild=args.rebuild)


if __name__ == "__main__":
    main()
