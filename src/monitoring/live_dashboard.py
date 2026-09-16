"""
MOMENTUM-X Live Terminal Dashboard

### ARCHITECTURAL CONTEXT
Node ID: monitoring.live_dashboard
Graph Link: docs/memory/graph_state.json → "monitoring.live_dashboard"

### RESEARCH BASIS
Real-time terminal status display for paper/live trading sessions.
Prints a formatted status block every N seconds, reading from
MetricsRegistry singleton. Toggleable at runtime.

Ref: ADR-019 (Full Observability)
Ref: S038 WS3 (D67)

### CRITICAL INVARIANTS
1. Never blocks the main trading loop — runs as independent async task.
2. All reads from MetricsRegistry are O(1) — no I/O in the render path.
3. Catches and suppresses all render errors to prevent dashboard from crashing trading.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from src.monitoring.metrics import get_metrics

logger = logging.getLogger(__name__)


class LiveDashboard:
    """
    Periodic terminal status display for paper/live trading sessions.

    Reads MetricsRegistry.snapshot() and optionally PositionManager state
    to render a compact status block every ``interval_seconds``.

    Usage:
        dashboard = LiveDashboard(interval_seconds=30, enabled=True)
        task = asyncio.create_task(dashboard.run(shutdown_event))
        # ... later ...
        dashboard.toggle()  # Enable/disable at runtime
    """

    def __init__(
        self,
        interval_seconds: int = 30,
        enabled: bool = True,
        position_manager: Any = None,
    ) -> None:
        self._interval = interval_seconds
        self._enabled = enabled
        self._pm = position_manager

    def toggle(self) -> bool:
        """Toggle dashboard on/off. Returns new enabled state."""
        self._enabled = not self._enabled
        return self._enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def run(self, shutdown: asyncio.Event) -> None:
        """Main loop: print status block every interval until shutdown."""
        while not shutdown.is_set():
            if self._enabled:
                try:
                    self._print_status()
                except Exception as e:
                    logger.debug("Dashboard render error: %s", e)
            try:
                await asyncio.wait_for(shutdown.wait(), timeout=self._interval)
                break  # shutdown was set
            except asyncio.TimeoutError:
                pass

    def _print_status(self) -> None:
        """Format and print the status block."""
        m = get_metrics()
        snap = m.snapshot()
        now = datetime.now(timezone.utc)

        pipeline = snap.get("pipeline", {})
        execution = snap.get("execution", {})
        risk = snap.get("risk", {})
        agents = snap.get("agents", {})

        # Build positions text from PositionManager if available
        # Wed 2026-04-22 Bug C: distinguish broker-confirmed stop from
        # computed-default. A position with stop_order_id == "" has NO
        # confirmed broker-side stop -- the dashboard must show this
        # explicitly so operators don't believe a phantom stop exists.
        positions_text = ""
        if self._pm is not None:
            try:
                for pos in self._pm.open_positions:
                    # Stop annotation: confirmed (broker order id known)
                    # vs unverified (no broker order id, value is a default).
                    has_broker_stop = bool(getattr(pos, "stop_order_id", ""))
                    stop_marker = "" if has_broker_stop else " ⚠UNVERIFIED"
                    positions_text += (
                        f"    {pos.ticker}: qty={pos.remaining_qty}, "
                        f"entry=${pos.entry_price:.2f}, "
                        f"stop=${pos.stop_loss:.2f}{stop_marker}, "
                        f"tranches={pos.tranches_filled}\n"
                    )
            except Exception:
                positions_text = "    (error reading positions)\n"

        # D87: Enhanced circuit breaker status from per-service breakers
        try:
            from src.utils.circuit_breaker import get_breaker_status
            _cb_status = get_breaker_status()
            _cb_open = [k for k, v in _cb_status.items() if v != "closed"]
            cb_text = f"OPEN: {', '.join(_cb_open)}" if _cb_open else "OK"
        except Exception:
            cb_count = risk.get("circuit_breaker_activations", 0)
            cb_text = f"TRIGGERED ({cb_count}x)" if cb_count > 0 else "OK"

        # Order rejection info
        rejected = execution.get("orders_rejected", 0)
        rejection_text = f" | Rejected: {rejected}" if rejected > 0 else ""

        # D103: Convert UTC to actual Eastern Time instead of labeling UTC as ET
        now_et = now.astimezone(ZoneInfo("America/New_York"))

        block = (
            f"\n{'=' * 62}\n"
            f"  MOMENTUM-X LIVE  |  {now.strftime('%H:%M:%S UTC')}  "
            f"({now_et.strftime('%H:%M ET')})\n"
            f"{'=' * 62}\n"
            f"  P&L: ${risk.get('daily_pnl', 0):+.2f}  |  "
            f"Trades: {execution.get('session_trades', 0)}  |  "
            f"Open: {execution.get('open_positions', 0)}\n"
            f"  Orders: {execution.get('orders_submitted', 0)} sub / "
            f"{execution.get('orders_filled', 0)} fill  |  "
            f"Slippage: {execution.get('fill_slippage_mean_bps', 0):.1f}bps"
            f"{rejection_text}\n"
            f"  Scans: {pipeline.get('scan_iterations', 0)}  |  "
            f"Evals: {pipeline.get('evaluations_total', 0)}  |  "
            f"Debates: {pipeline.get('debates_triggered', 0)} "
            f"({pipeline.get('debates_buy', 0)} BUY)\n"
            f"  Agent latency: {agents.get('latency_mean_s', 0) * 1000:.0f}ms  |  "
            f"Errors: {agents.get('errors_total', 0)}  |  "
            f"Vetoes: {risk.get('risk_vetoes', 0)}\n"
            f"  Circuit breaker: {cb_text}\n"
        )

        if positions_text:
            block += f"  POSITIONS:\n{positions_text}"

        block += f"{'=' * 62}"

        logger.info(block)

    def render_status(self) -> str:
        """Render status block as string (for testing / programmatic use)."""
        m = get_metrics()
        snap = m.snapshot()
        now = datetime.now(timezone.utc)

        pipeline = snap.get("pipeline", {})
        execution = snap.get("execution", {})
        risk = snap.get("risk", {})
        agents = snap.get("agents", {})

        return (
            f"P&L=${risk.get('daily_pnl', 0):+.2f} "
            f"Trades={execution.get('session_trades', 0)} "
            f"Open={execution.get('open_positions', 0)} "
            f"Evals={pipeline.get('evaluations_total', 0)} "
            f"AgentLatency={agents.get('latency_mean_s', 0) * 1000:.0f}ms"
        )
