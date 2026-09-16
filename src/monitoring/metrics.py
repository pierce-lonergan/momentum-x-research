"""
MOMENTUM-X Observability Metrics

### ARCHITECTURAL CONTEXT
Node ID: monitoring.metrics
Graph Link: docs/memory/graph_state.json → "monitoring.metrics"

### RESEARCH BASIS
Production trading systems require real-time observability to detect
degradation, attribution drift, and risk control failures.

Metrics are organized by subsystem:
  - Pipeline: scan→evaluate→execute latency, throughput
  - Agents: Elo ratings, latency per agent, error rates
  - Risk: circuit breaker activations, daily P&L
  - GEX: filter hit rate, suppression/acceleration counts
  - Positions: open count, session P&L, fill slippage

Ref: ADR-015 (Production Readiness)
Ref: docs/research/POST_TRADE_ANALYSIS.md (Elo tracking)

### CRITICAL INVARIANTS
1. Metric updates are O(1) — never block the pipeline.
2. Thread-safe: protected by per-metric threading.Lock.
3. Exportable as JSON (for structured logging) or Prometheus text format.
4. Zero external dependencies (no prometheus_client required).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

logger = logging.getLogger(__name__)


@dataclass
class CounterMetric:
    """Monotonically increasing counter. Thread-safe via lock."""
    name: str
    help: str
    value: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def inc(self, amount: float = 1.0) -> None:
        # Sweep fix: Python `+=` is NOT atomic for dataclass fields.
        # Multiple threads (heartbeat, metrics server, trading loop) can race.
        with self._lock:
            self.value += amount


@dataclass
class GaugeMetric:
    """Value that can go up and down. Thread-safe via lock."""
    name: str
    help: str
    value: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def set(self, value: float) -> None:
        with self._lock:
            self.value = value

    def inc(self, amount: float = 1.0) -> None:
        with self._lock:
            self.value += amount

    def dec(self, amount: float = 1.0) -> None:
        with self._lock:
            self.value -= amount


@dataclass
class HistogramMetric:
    """Distribution tracker with sum, count, and configurable buckets. Thread-safe via lock."""
    name: str
    help: str
    _sum: float = 0.0
    _count: int = 0
    _min: float = float("inf")
    _max: float = float("-inf")
    _buckets: dict[float, int] = field(default_factory=dict)
    bucket_boundaries: tuple[float, ...] = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        if not self._buckets:
            self._buckets = {b: 0 for b in self.bucket_boundaries}

    def observe(self, value: float) -> None:
        with self._lock:
            self._sum += value
            self._count += 1
            self._min = min(self._min, value)
            self._max = max(self._max, value)
            # D121 BUG: Only increment the first matching bucket (differential).
            # to_prometheus() re-accumulates into cumulative form.
            # Previously incremented ALL buckets >= value, then to_prometheus()
            # accumulated again, double-counting.
            for boundary in self.bucket_boundaries:
                if value <= boundary:
                    self._buckets[boundary] = self._buckets.get(boundary, 0) + 1
                    break

    @property
    def mean(self) -> float:
        return self._sum / self._count if self._count > 0 else 0.0

    @property
    def count(self) -> int:
        return self._count

    @property
    def sum(self) -> float:
        return self._sum

    @property
    def min(self) -> float:
        return self._min if self._count > 0 else 0.0

    @property
    def max(self) -> float:
        return self._max if self._count > 0 else 0.0


class MetricsRegistry:
    """
    Central metrics registry for the Momentum-X system.

    Node ID: monitoring.metrics.MetricsRegistry
    Ref: ADR-015 (Production Readiness)

    Usage:
        metrics = MetricsRegistry()
        metrics.scan_iterations.inc()
        with metrics.timer(metrics.pipeline_latency):
            await evaluate_candidate(...)
        metrics.agent_elo.set("news_agent", 1520.5)
        snapshot = metrics.snapshot()
    """

    def __init__(self) -> None:
        # ── Pipeline Metrics ──
        self.scan_iterations = CounterMetric(
            name="mx_scan_iterations_total",
            help="Total scan iterations executed",
        )
        self.scan_candidates_found = CounterMetric(
            name="mx_scan_candidates_found_total",
            help="Total candidates passing EMC filter",
        )
        self.pipeline_latency = HistogramMetric(
            name="mx_pipeline_latency_seconds",
            help="Full evaluation pipeline latency per candidate",
            bucket_boundaries=(0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 90.0),
        )
        self.evaluations_total = CounterMetric(
            name="mx_evaluations_total",
            help="Total candidates evaluated through full pipeline",
        )

        # ── Agent Metrics ──
        self.agent_elo_ratings: dict[str, float] = {}
        self.agent_latency = HistogramMetric(
            name="mx_agent_latency_seconds",
            help="Individual agent LLM call latency",
            bucket_boundaries=(0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0),
        )
        self.agent_errors = CounterMetric(
            name="mx_agent_errors_total",
            help="Total agent errors (timeouts, parse failures)",
        )

        # ── Risk Metrics ──
        self.circuit_breaker_activations = CounterMetric(
            name="mx_circuit_breaker_activations_total",
            help="Times circuit breaker has triggered",
        )
        self.daily_pnl = GaugeMetric(
            name="mx_daily_pnl_dollars",
            help="Current daily realized P&L in dollars",
        )
        self.risk_vetoes = CounterMetric(
            name="mx_risk_vetoes_total",
            help="Total trades vetoed by Risk Agent",
        )

        # ── GEX Metrics ──
        self.gex_filter_rejections = CounterMetric(
            name="mx_gex_filter_rejections_total",
            help="Candidates rejected by GEX hard filter",
        )
        self.gex_filter_passes = CounterMetric(
            name="mx_gex_filter_passes_total",
            help="Candidates passing GEX hard filter",
        )

        # ── Execution Metrics ──
        self.orders_submitted = CounterMetric(
            name="mx_orders_submitted_total",
            help="Total orders submitted to Alpaca",
        )
        self.orders_filled = CounterMetric(
            name="mx_orders_filled_total",
            help="Total orders confirmed filled",
        )
        self.open_positions = GaugeMetric(
            name="mx_open_positions",
            help="Current number of open positions",
        )
        self.session_trades = CounterMetric(
            name="mx_session_trades_total",
            help="Total trades completed this session",
        )
        # Sweep fix: win/loss counters for session report
        self.win_count = CounterMetric(
            name="mx_win_count_total",
            help="Total winning trades this session",
        )
        self.loss_count = CounterMetric(
            name="mx_loss_count_total",
            help="Total losing trades this session",
        )
        self.fill_slippage_bps = HistogramMetric(
            name="mx_fill_slippage_bps",
            help="Fill slippage in basis points",
            bucket_boundaries=(1.0, 5.0, 10.0, 25.0, 50.0, 100.0),
        )
        self.orders_rejected = CounterMetric(
            name="mx_orders_rejected_total",
            help="Total orders rejected by broker",
        )
        self.orders_partial_fills = CounterMetric(
            name="mx_orders_partial_fills_total",
            help="Total partial fill events",
        )

        # ── Debate Metrics ──
        self.debates_triggered = CounterMetric(
            name="mx_debates_triggered_total",
            help="Candidates qualifying for debate",
        )
        self.debates_buy = CounterMetric(
            name="mx_debates_buy_total",
            help="Debates resulting in BUY verdict",
        )
        self.debates_skipped = CounterMetric(
            name="mx_debates_skipped_total",
            help="Debates skipped due to CV consensus",
        )
        # D103: Separate counter for budget-exhaustion skips vs CV-consensus skips
        self.debates_skipped_budget = CounterMetric(
            name="mx_debates_skipped_budget_total",
            help="Debates skipped due to budget exhaustion (max_debate_attempts=0)",
        )
        self.debates_no_trade = CounterMetric(
            name="mx_debates_no_trade_total",
            help="Debates resulting in NO_TRADE verdict",
        )

        # ── D96: Phase Timing Metrics ──
        self.phase0_duration = GaugeMetric(
            name="mx_phase0_duration_seconds",
            help="Phase 0 (pre-market research) wall-clock duration",
        )
        self.phase2_duration = GaugeMetric(
            name="mx_phase2_duration_seconds",
            help="Phase 2 (eval+trade) wall-clock duration",
        )
        self.phase3_cycles = CounterMetric(
            name="mx_phase3_cycles_total",
            help="Total Phase 3 monitoring cycles completed",
        )

        # ── D96: Stop-out Metrics ──
        self.stop_outs = CounterMetric(
            name="mx_stop_outs_total",
            help="Total stop-out events detected",
        )
        self.smart_exits = CounterMetric(
            name="mx_smart_exits_total",
            help="Total D78 smart exit events",
        )

        # ── D96: Per-agent signal distribution ──
        self._agent_signal_counts: dict[str, dict[str, int]] = {}  # agent_id → {signal → count}

        # ── Data Completeness Metrics ──
        self.data_complete_agents = CounterMetric(
            name="mx_data_complete_agents_total",
            help="Agent-evaluations with COMPLETE data",
        )
        self.data_partial_agents = CounterMetric(
            name="mx_data_partial_agents_total",
            help="Agent-evaluations with PARTIAL data",
        )
        self.data_empty_agents = CounterMetric(
            name="mx_data_empty_agents_total",
            help="Agent-evaluations with EMPTY data (confabulation risk)",
        )
        self._data_fill_rates: dict[str, list[float]] = {}

        # ── D109: State Validation Metrics ──
        self.state_validation_clamps = CounterMetric(
            name="mx_state_validation_clamps_total",
            help="Position recovery clamp operations (impossible state corrections)",
        )

        # ── Timestamps ──
        self._created_at = datetime.now(timezone.utc)

    def set_agent_elo(self, agent_id: str, rating: float) -> None:
        """Update an agent's Elo rating."""
        self.agent_elo_ratings[agent_id] = rating

    def record_agent_signal(self, agent_id: str, signal: str) -> None:
        """D96: Record an agent's signal direction for EOD distribution analysis."""
        if agent_id not in self._agent_signal_counts:
            self._agent_signal_counts[agent_id] = {}
        self._agent_signal_counts[agent_id][signal] = (
            self._agent_signal_counts[agent_id].get(signal, 0) + 1
        )

    def get_agent_signal_summary(self) -> dict[str, dict[str, int]]:
        """D96: Get per-agent signal direction distribution."""
        return dict(self._agent_signal_counts)

    def record_data_completeness(self, report: dict[str, dict[str, Any]]) -> None:
        """
        Record data completeness from orchestrator's per-agent report.

        Args:
            report: Dict of agent_id → {"status": COMPLETE|PARTIAL|EMPTY, ...}
        """
        for agent_id, info in report.items():
            status = info.get("status", "EMPTY")
            if status == "COMPLETE":
                self.data_complete_agents.inc()
                fill_rate = 1.0
            elif status == "PARTIAL":
                self.data_partial_agents.inc()
                fill_rate = 0.5
            else:
                self.data_empty_agents.inc()
                fill_rate = 0.0

            if agent_id not in self._data_fill_rates:
                self._data_fill_rates[agent_id] = []
            self._data_fill_rates[agent_id].append(fill_rate)

    def get_data_fill_summary(self) -> dict[str, dict[str, float]]:
        """
        Get per-agent data fill rate summary.

        Returns:
            Dict of agent_id → {"fill_rate": avg, "evaluations": count}.
        """
        summary: dict[str, dict[str, float]] = {}
        for agent_id, rates in self._data_fill_rates.items():
            summary[agent_id] = {
                "fill_rate": sum(rates) / len(rates) if rates else 0.0,
                "evaluations": float(len(rates)),
            }
        return summary

    @contextmanager
    def timer(self, histogram: HistogramMetric) -> Generator[None, None, None]:
        """Context manager for timing operations into a histogram."""
        start = time.monotonic()
        try:
            yield
        finally:
            elapsed = time.monotonic() - start
            histogram.observe(elapsed)

    def snapshot(self) -> dict[str, Any]:
        """
        Export all metrics as a JSON-serializable dict.

        Structure:
            {"pipeline": {...}, "agents": {...}, "risk": {...}, "gex": {...}, "execution": {...}}
        """
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "uptime_seconds": (datetime.now(timezone.utc) - self._created_at).total_seconds(),
            "pipeline": {
                "scan_iterations": self.scan_iterations.value,
                "candidates_found": self.scan_candidates_found.value,
                "evaluations_total": self.evaluations_total.value,
                "pipeline_latency_mean_s": round(self.pipeline_latency.mean, 3),
                "pipeline_latency_p99_s": round(self.pipeline_latency.max, 3),
                "debates_triggered": self.debates_triggered.value,
                "debates_buy": self.debates_buy.value,
            },
            "agents": {
                "elo_ratings": dict(self.agent_elo_ratings),
                "latency_mean_s": round(self.agent_latency.mean, 3),
                "errors_total": self.agent_errors.value,
            },
            "risk": {
                "circuit_breaker_activations": self.circuit_breaker_activations.value,
                "daily_pnl": round(self.daily_pnl.value, 2),
                "risk_vetoes": self.risk_vetoes.value,
            },
            "gex": {
                "filter_rejections": self.gex_filter_rejections.value,
                "filter_passes": self.gex_filter_passes.value,
                "rejection_rate": round(
                    self.gex_filter_rejections.value /
                    max(1, self.gex_filter_rejections.value + self.gex_filter_passes.value),
                    3,
                ),
            },
            "execution": {
                "orders_submitted": self.orders_submitted.value,
                "orders_filled": self.orders_filled.value,
                "orders_rejected": self.orders_rejected.value,
                "orders_partial_fills": self.orders_partial_fills.value,
                "open_positions": self.open_positions.value,
                "session_trades": self.session_trades.value,
                "win_count": self.win_count.value,
                "loss_count": self.loss_count.value,
                "fill_slippage_mean_bps": round(self.fill_slippage_bps.mean, 2),
            },
            "debate": {
                "triggered": self.debates_triggered.value,
                "buy": self.debates_buy.value,
                "skipped_cv": self.debates_skipped.value,
                "skipped_budget": self.debates_skipped_budget.value,  # D103
                "no_trade": self.debates_no_trade.value,
                "skip_rate": round(
                    (self.debates_skipped.value + self.debates_skipped_budget.value) /
                    max(1, self.debates_triggered.value),
                    3,
                ),
            },
            "phase_timing": {
                "phase0_duration_s": self.phase0_duration.value,
                "phase2_duration_s": self.phase2_duration.value,
                "phase3_cycles": self.phase3_cycles.value,
            },
            "exit_events": {
                "stop_outs": self.stop_outs.value,
                "smart_exits": self.smart_exits.value,
            },
            "state_validation": {
                "clamps_total": self.state_validation_clamps.value,
            },
            "agent_signals": self.get_agent_signal_summary(),
            "data_completeness": {
                "complete_total": self.data_complete_agents.value,
                "partial_total": self.data_partial_agents.value,
                "empty_total": self.data_empty_agents.value,
                "overall_fill_rate": round(
                    self.data_complete_agents.value /
                    max(1, self.data_complete_agents.value + self.data_partial_agents.value + self.data_empty_agents.value),
                    3,
                ),
                "per_agent": self.get_data_fill_summary(),
            },
        }

    # D108: Maximum number of metric snapshot files to keep
    _MAX_SNAPSHOTS = 200

    def save_snapshot(self, output_dir: Path) -> None:
        """D108: Write metrics snapshot to disk for crash-recovery observability.

        Keeps only the last _MAX_SNAPSHOTS files to prevent disk bloat.
        Non-fatal: all exceptions caught and logged.
        """
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
            snapshot_path = output_dir / f"metrics_{ts}.json"
            snapshot_path.write_text(
                json.dumps(self.snapshot(), indent=2),
                encoding="utf-8",
            )
            # Rotate: keep only last N snapshots
            files = sorted(output_dir.glob("metrics_*.json"))
            if len(files) > self._MAX_SNAPSHOTS:
                for old_file in files[: len(files) - self._MAX_SNAPSHOTS]:
                    old_file.unlink(missing_ok=True)
        except Exception as e:
            logger.debug("D108: Metric snapshot failed (non-fatal): %s", e)

    def to_prometheus(self) -> str:
        """
        Export metrics in Prometheus text exposition format.

        Suitable for scraping by Prometheus server or pushing to Pushgateway.
        """
        lines: list[str] = []

        def _counter(m: CounterMetric) -> None:
            lines.append(f"# HELP {m.name} {m.help}")
            lines.append(f"# TYPE {m.name} counter")
            lines.append(f"{m.name} {m.value}")

        def _gauge(m: GaugeMetric) -> None:
            lines.append(f"# HELP {m.name} {m.help}")
            lines.append(f"# TYPE {m.name} gauge")
            lines.append(f"{m.name} {m.value}")

        def _histogram(m: HistogramMetric) -> None:
            lines.append(f"# HELP {m.name} {m.help}")
            lines.append(f"# TYPE {m.name} histogram")
            cumulative = 0
            for boundary in sorted(m._buckets.keys()):
                cumulative += m._buckets[boundary]
                lines.append(f'{m.name}_bucket{{le="{boundary}"}} {cumulative}')
            lines.append(f'{m.name}_bucket{{le="+Inf"}} {m._count}')
            lines.append(f"{m.name}_sum {m._sum}")
            lines.append(f"{m.name}_count {m._count}")

        _counter(self.scan_iterations)
        _counter(self.scan_candidates_found)
        _histogram(self.pipeline_latency)
        _counter(self.evaluations_total)
        _histogram(self.agent_latency)
        _counter(self.agent_errors)
        _counter(self.circuit_breaker_activations)
        _gauge(self.daily_pnl)
        _counter(self.risk_vetoes)
        _counter(self.gex_filter_rejections)
        _counter(self.gex_filter_passes)
        _counter(self.orders_submitted)
        _counter(self.orders_filled)
        _counter(self.orders_rejected)
        _counter(self.orders_partial_fills)
        _gauge(self.open_positions)
        _counter(self.session_trades)
        _counter(self.win_count)
        _counter(self.loss_count)
        _histogram(self.fill_slippage_bps)
        _counter(self.debates_triggered)
        _counter(self.debates_buy)
        _counter(self.debates_skipped)
        _counter(self.debates_skipped_budget)
        _counter(self.debates_no_trade)
        _gauge(self.phase0_duration)
        _gauge(self.phase2_duration)
        _counter(self.phase3_cycles)
        _counter(self.stop_outs)
        _counter(self.smart_exits)
        _counter(self.data_complete_agents)
        _counter(self.data_partial_agents)
        _counter(self.data_empty_agents)
        _counter(self.state_validation_clamps)

        # Per-agent fill rates as labeled gauges
        for agent_id, info in self.get_data_fill_summary().items():
            lines.append(f'mx_agent_data_fill_rate{{agent="{agent_id}"}} {info["fill_rate"]:.3f}')

        # Agent Elo as labeled gauges
        for agent_id, rating in self.agent_elo_ratings.items():
            lines.append(f'mx_agent_elo_rating{{agent="{agent_id}"}} {rating}')

        return "\n".join(lines) + "\n"


# ── Global singleton (optional, for convenience) ──
_global_metrics: MetricsRegistry | None = None


def get_metrics() -> MetricsRegistry:
    """Get or create the global MetricsRegistry singleton."""
    global _global_metrics
    if _global_metrics is None:
        _global_metrics = MetricsRegistry()
    return _global_metrics


def reset_metrics() -> None:
    """Reset global metrics (for testing)."""
    global _global_metrics
    _global_metrics = None
