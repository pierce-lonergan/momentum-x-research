"""
MOMENTUM-X Session Report Generator

### ARCHITECTURAL CONTEXT
Node ID: analysis.session_report
Graph Link: docs/memory/graph_state.json → "analysis.session_report"

### RESEARCH BASIS
Generates a comprehensive end-of-day report summarizing all trading
activity, metrics, and performance for each paper/live session.

Reports include:
  - Session metadata (start time, duration, mode)
  - Pipeline statistics (scans, evaluations, debates)
  - Execution summary (orders, fills, positions, slippage)
  - P&L breakdown (realized, unrealized, daily)
  - Risk events (circuit breaker, vetoes)
  - Agent performance (Elo ratings, errors)
  - GEX filter effectiveness

Saved as JSON + human-readable text to data/session_reports/.

Ref: ADR-019 (Full Observability)
Ref: MOMENTUM_LOGIC.md §17 (Post-trade Analysis)

### CRITICAL INVARIANTS
1. Report is generated from MetricsRegistry.snapshot() — single source of truth.
2. Report always saved to disk even if display fails.
3. Filenames include ISO timestamp for chronological ordering.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.monitoring.metrics import get_metrics

logger = logging.getLogger(__name__)

DEFAULT_REPORT_DIR = Path("data/session_reports")


@dataclass
class SessionReport:
    """
    Complete end-of-day session summary.

    Node ID: analysis.session_report.SessionReport
    """
    # Session metadata
    session_date: str = ""
    session_start: str = ""
    session_end: str = ""
    duration_minutes: float = 0.0
    mode: str = "paper"

    # Pipeline
    scan_iterations: int = 0
    candidates_found: int = 0
    evaluations_total: int = 0
    pipeline_latency_mean_ms: float = 0.0
    debates_triggered: int = 0
    debates_buy: int = 0

    # Execution
    orders_submitted: int = 0
    orders_filled: int = 0
    fill_rate_pct: float = 0.0
    session_trades: int = 0
    open_positions_at_close: int = 0
    fill_slippage_mean_bps: float = 0.0

    # P&L
    daily_pnl: float = 0.0
    realized_pnl_pm: float = 0.0  # D103: Position manager ground-truth P&L
    win_count: int = 0
    loss_count: int = 0

    # Risk
    circuit_breaker_activations: int = 0
    risk_vetoes: int = 0

    # Agents
    agent_elo_ratings: dict[str, float] = field(default_factory=dict)
    agent_errors: int = 0
    agent_latency_mean_ms: float = 0.0

    # GEX
    gex_filter_rejections: int = 0
    gex_filter_passes: int = 0
    gex_rejection_rate_pct: float = 0.0

    # D96: Enhanced audit metrics
    debates_skipped: int = 0
    debates_skipped_budget: int = 0  # D103: Debates skipped due to budget exhaustion
    debates_no_trade: int = 0
    debate_skip_rate_pct: float = 0.0
    phase0_duration_s: float = 0.0
    phase2_duration_s: float = 0.0
    phase3_cycles: int = 0
    stop_outs: int = 0
    smart_exits: int = 0
    agent_signal_distribution: dict[str, dict[str, int]] = field(default_factory=dict)

    # D102: Experiment framework summary
    experiment_variants_tested: int = 0
    experiment_summary: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Export as JSON-serializable dict."""
        return asdict(self)

    def summary_text(self) -> str:
        """Human-readable summary for logging."""
        lines = [
            "═══════════════════════════════════════════════════",
            f"  MOMENTUM-X SESSION REPORT — {self.session_date}",
            "═══════════════════════════════════════════════════",
            f"  Mode: {self.mode.upper()} | Duration: {self.duration_minutes:.1f} min",
            "",
            "  📊 PIPELINE",
            f"    Scans: {self.scan_iterations} | Candidates: {self.candidates_found}",
            f"    Evaluations: {self.evaluations_total} | Latency: {self.pipeline_latency_mean_ms:.0f}ms",
            f"    Debates: {self.debates_triggered} triggered, {self.debates_buy} → BUY",
            "",
            "  ⚡ EXECUTION",
            f"    Orders: {self.orders_submitted} submitted, {self.orders_filled} filled ({self.fill_rate_pct:.0f}%)",
            f"    Trades: {self.session_trades} | Slippage: {self.fill_slippage_mean_bps:.1f}bps",
            f"    Open at close: {self.open_positions_at_close}",
            "",
            "  💰 P&L",
            f"    Daily P&L: ${self.daily_pnl:+.2f}",
            f"    PM Ground Truth: ${self.realized_pnl_pm:+.2f}  "
            f"{'✓' if abs(self.daily_pnl - self.realized_pnl_pm) < 0.01 else '⚠ MISMATCH'}",
            "",
            "  🛡️ RISK",
            f"    Circuit breaker: {self.circuit_breaker_activations} | Vetoes: {self.risk_vetoes}",
            "",
            "  🤖 AGENTS",
            f"    Errors: {self.agent_errors} | Avg latency: {self.agent_latency_mean_ms:.0f}ms",
            "",
            "  🎯 GEX FILTER",
            f"    Rejections: {self.gex_filter_rejections} | Passes: {self.gex_filter_passes} ({self.gex_rejection_rate_pct:.0f}% rejection rate)",
            "",
            "  ⏱️ PHASE TIMING",
            f"    Phase 0: {self.phase0_duration_s:.0f}s | Phase 2: {self.phase2_duration_s:.0f}s | Phase 3 cycles: {self.phase3_cycles}",
            "",
            "  🔄 DEBATE ENGINE",
            f"    Skipped (budget): {self.debates_skipped_budget} | Skipped (CV): {self.debates_skipped} | No-Trade: {self.debates_no_trade} | Skip rate: {self.debate_skip_rate_pct:.0f}%",
            "",
            "  🚪 EXIT EVENTS",
            f"    Stop-outs: {self.stop_outs} | Smart exits: {self.smart_exits}",
        ]

        # D102: Experiment summary section
        if self.experiment_variants_tested > 0:
            lines.append("")
            lines.append("  🧪 EXPERIMENTS")
            lines.append(f"    Variants tested: {self.experiment_variants_tested}")
            for exp_id, variants in self.experiment_summary.items():
                lines.append(f"    {exp_id}:")
                for var_id, stats in variants.items():
                    enter = stats.get("would_enter", 0)
                    skip = stats.get("would_skip", 0)
                    total = enter + skip
                    rate = (enter / total * 100) if total > 0 else 0
                    lines.append(
                        f"      {var_id}: {enter}/{total} enter ({rate:.0f}%)"
                    )

        lines.append("═══════════════════════════════════════════════════")
        return "\n".join(lines)


class SessionReportGenerator:
    """
    Generates end-of-day session reports from MetricsRegistry.

    Node ID: analysis.session_report
    Ref: ADR-019 (Full Observability)

    Usage:
        generator = SessionReportGenerator(mode="paper")
        report = generator.generate()
        generator.save(report)
        logger.info(report.summary_text())
    """

    def __init__(
        self,
        mode: str = "paper",
        report_dir: Path = DEFAULT_REPORT_DIR,
        session_start: datetime | None = None,
    ) -> None:
        self._mode = mode
        self._report_dir = report_dir
        self._session_start = session_start or datetime.now(timezone.utc)

    def generate(self, realized_pnl_pm: float | None = None) -> SessionReport:
        """
        Generate session report from current MetricsRegistry state.

        Args:
            realized_pnl_pm: D103 ground-truth P&L from PositionManager.
                Passed as a cross-check against the metrics-derived daily_pnl.

        Returns:
            SessionReport populated from metrics snapshot.
        """
        metrics = get_metrics()
        snap = metrics.snapshot()
        now = datetime.now(timezone.utc)
        duration = (now - self._session_start).total_seconds() / 60.0

        pipeline = snap.get("pipeline", {})
        agents = snap.get("agents", {})
        risk = snap.get("risk", {})
        gex = snap.get("gex", {})
        execution = snap.get("execution", {})

        orders_sub = execution.get("orders_submitted", 0)
        orders_fill = execution.get("orders_filled", 0)
        fill_rate = (orders_fill / max(1, orders_sub)) * 100

        gex_rej = gex.get("filter_rejections", 0)
        gex_pass = gex.get("filter_passes", 0)
        gex_rate = (gex_rej / max(1, gex_rej + gex_pass)) * 100

        return SessionReport(
            session_date=now.strftime("%Y-%m-%d"),
            session_start=self._session_start.isoformat(),
            session_end=now.isoformat(),
            duration_minutes=round(duration, 1),
            mode=self._mode,

            scan_iterations=pipeline.get("scan_iterations", 0),
            candidates_found=pipeline.get("candidates_found", 0),
            evaluations_total=pipeline.get("evaluations_total", 0),
            pipeline_latency_mean_ms=round(pipeline.get("pipeline_latency_mean_s", 0) * 1000, 1),
            debates_triggered=pipeline.get("debates_triggered", 0),
            debates_buy=pipeline.get("debates_buy", 0),

            orders_submitted=orders_sub,
            orders_filled=orders_fill,
            fill_rate_pct=round(fill_rate, 1),
            session_trades=execution.get("session_trades", 0),
            open_positions_at_close=execution.get("open_positions", 0),
            fill_slippage_mean_bps=execution.get("fill_slippage_mean_bps", 0.0),

            daily_pnl=risk.get("daily_pnl", 0.0),
            realized_pnl_pm=realized_pnl_pm if realized_pnl_pm is not None else risk.get("daily_pnl", 0.0),
            # Sweep fix: win/loss counters were never populated from metrics
            win_count=int(execution.get("win_count", 0)),
            loss_count=int(execution.get("loss_count", 0)),

            circuit_breaker_activations=risk.get("circuit_breaker_activations", 0),
            risk_vetoes=risk.get("risk_vetoes", 0),

            agent_elo_ratings=agents.get("elo_ratings", {}),
            agent_errors=agents.get("errors_total", 0),
            agent_latency_mean_ms=round(agents.get("latency_mean_s", 0) * 1000, 1),

            gex_filter_rejections=gex_rej,
            gex_filter_passes=gex_pass,
            gex_rejection_rate_pct=round(gex_rate, 1),

            # D96: Enhanced audit metrics
            debates_skipped=int(snap.get("debate", {}).get("skipped_cv", 0)),
            debates_skipped_budget=int(snap.get("debate", {}).get("skipped_budget", 0)),
            debates_no_trade=int(snap.get("debate", {}).get("no_trade", 0)),
            debate_skip_rate_pct=round(snap.get("debate", {}).get("skip_rate", 0) * 100, 1),
            phase0_duration_s=round(snap.get("phase_timing", {}).get("phase0_duration_s", 0), 1),
            phase2_duration_s=round(snap.get("phase_timing", {}).get("phase2_duration_s", 0), 1),
            phase3_cycles=int(snap.get("phase_timing", {}).get("phase3_cycles", 0)),
            stop_outs=int(snap.get("exit_events", {}).get("stop_outs", 0)),
            smart_exits=int(snap.get("exit_events", {}).get("smart_exits", 0)),
            agent_signal_distribution=snap.get("agent_signals", {}),
        )

    def generate_from_journal(
        self,
        journal_path: Path | None = None,
        journal_dir: Path = Path("data/journals"),
    ) -> SessionReport:
        """
        Generate session report from TradeJournal JSONL file.

        D60 fallback: when MetricsRegistry counters are unreliable (e.g. after
        restart loses in-memory state), derive pipeline/execution/P&L statistics
        directly from the journal entries which are always written to disk.

        Args:
            journal_path: Path to specific JSONL file. If None, finds the
                most recent journal file for today's date.
            journal_dir: Directory to search for journal files.

        Returns:
            SessionReport populated from journal data.
        """
        from src.analysis.trade_journal import TradeJournal

        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        duration = (now - self._session_start).total_seconds() / 60.0

        if journal_path is None:
            candidates = sorted(
                journal_dir.glob(f"journal_{today}_*.jsonl"),
                reverse=True,
            )
            if not candidates:
                logger.warning("D60: No journal file found for %s", today)
                return SessionReport(session_date=today, mode=self._mode)
            journal_path = candidates[0]

        try:
            entries = TradeJournal.load(journal_path)
        except Exception as e:
            logger.warning("D60: Failed to load journal %s: %s", journal_path, e)
            return SessionReport(session_date=today, mode=self._mode)

        if not entries:
            return SessionReport(session_date=today, mode=self._mode)

        buy_entries = [e for e in entries if e.action == "BUY"]
        filled_entries = [e for e in buy_entries if e.order_id]
        closed_entries = [e for e in buy_entries if e.exit_price is not None]
        debated = [e for e in entries if e.debate is not None]
        debate_buys = [
            e for e in debated
            if e.debate and getattr(e.debate, "verdict", "") in ("BUY", "STRONG_BUY")
        ]

        wins = [e for e in closed_entries if (e.realized_pnl or 0) > 0]
        losses = [e for e in closed_entries if (e.realized_pnl or 0) <= 0]
        total_pnl = sum(e.realized_pnl or 0 for e in closed_entries)

        slippage_values = [
            e.slippage_bps for e in buy_entries if e.slippage_bps is not None
        ]
        avg_slippage = (
            sum(slippage_values) / len(slippage_values) if slippage_values else 0.0
        )

        latencies = [e.pipeline_latency_ms for e in entries if e.pipeline_latency_ms > 0]
        avg_latency = sum(latencies) / len(latencies) if latencies else 0.0

        orders_sub = len(buy_entries)
        orders_fill = len(filled_entries)
        fill_rate = (orders_fill / max(1, orders_sub)) * 100

        # D102: Load experiment journal for this session
        exp_variants_tested = 0
        exp_summary: dict[str, dict[str, Any]] = {}
        try:
            from src.experiments.journal import ExperimentJournal
            exp_entries = ExperimentJournal.load_latest(date=today)
            for exp_entry in exp_entries:
                for vr in exp_entry.variant_results:
                    exp_variants_tested += 1
                    if vr.experiment_id not in exp_summary:
                        exp_summary[vr.experiment_id] = {}
                    if vr.variant_id not in exp_summary[vr.experiment_id]:
                        exp_summary[vr.experiment_id][vr.variant_id] = {
                            "would_enter": 0,
                            "would_skip": 0,
                        }
                    if vr.would_enter:
                        exp_summary[vr.experiment_id][vr.variant_id]["would_enter"] += 1
                    else:
                        exp_summary[vr.experiment_id][vr.variant_id]["would_skip"] += 1
        except Exception as e:
            logger.debug("D102: Experiment journal load failed (non-fatal): %s", e)

        return SessionReport(
            session_date=today,
            session_start=self._session_start.isoformat(),
            session_end=now.isoformat(),
            duration_minutes=round(duration, 1),
            mode=self._mode,
            evaluations_total=len(entries),
            pipeline_latency_mean_ms=round(avg_latency, 1),
            debates_triggered=len(debated),
            debates_buy=len(debate_buys),
            orders_submitted=orders_sub,
            orders_filled=orders_fill,
            fill_rate_pct=round(fill_rate, 1),
            session_trades=len(closed_entries),
            fill_slippage_mean_bps=round(avg_slippage, 2),
            daily_pnl=round(total_pnl, 2),
            win_count=len(wins),
            loss_count=len(losses),
            experiment_variants_tested=exp_variants_tested,
            experiment_summary=exp_summary,
        )

    def save(self, report: SessionReport) -> Path:
        """
        Save report to disk as JSON using atomic write pattern.

        D218 FIX (W-5): Uses .tmp + os.replace() to prevent half-written
        files on crash. Previous write_text() could leave corrupted reports
        if the process was killed mid-write. The session report is the primary
        "was the session clean?" artifact — it must never be partial.

        Returns:
            Path to saved report file.
        """
        self._report_dir.mkdir(parents=True, exist_ok=True)
        timestamp = report.session_end.replace(":", "-").replace("+", "p")
        filename = f"session_{report.session_date}_{timestamp[:19]}.json"
        path = self._report_dir / filename
        tmp_path = path.with_suffix(".tmp")

        try:
            # Serialize first — catches serialization errors before any IO
            content = json.dumps(report.to_dict(), indent=2, default=str)

            # Write to temp file
            tmp_path.write_text(content, encoding="utf-8")

            # Backup previous report if it exists (best-effort)
            if path.exists():
                bak_path = path.with_suffix(".bak")
                try:
                    import shutil
                    shutil.copy2(str(path), str(bak_path))
                except Exception as bak_e:
                    logger.debug("D218: Report backup failed (non-fatal): %s", bak_e)

            # Atomic replace
            os.replace(str(tmp_path), str(path))
            logger.info("Session report saved: %s", path)

        except Exception as e:
            logger.error("D218: Session report write failed: %s", e)
            # Clean up tmp file
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
            raise

        return path

    def send_notification(self, report: SessionReport, webhook_url: str) -> None:
        """D108: POST compact session summary to external webhook.

        Uses stdlib urllib (same pattern as heartbeat webhook). Non-fatal:
        all exceptions caught and logged. Designed for ntfy.sh, Pushover,
        Healthchecks.io, or any webhook that accepts JSON POST.
        """
        try:
            summary = {
                "date": report.session_date,
                "mode": report.mode,
                "pnl": round(report.daily_pnl, 2),
                "trades": int(report.session_trades),
                "wins": int(report.win_count),
                "losses": int(report.loss_count),
                "stop_outs": int(report.stop_outs),
                "duration_min": int(report.duration_minutes),
                "evaluations": int(report.evaluations_total),
            }
            body = json.dumps(summary).encode("utf-8")
            req = urllib.request.Request(
                webhook_url,
                data=body,
                method="POST",
            )
            req.add_header("Content-Type", "application/json")
            req.add_header("User-Agent", "Momentum-X SessionReport/D108")
            urllib.request.urlopen(req, timeout=10)
            logger.info("D108: Session notification sent to %s", webhook_url)
        except Exception as e:
            logger.warning("D108: Session notification failed (non-fatal): %s", e)
