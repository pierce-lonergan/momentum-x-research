"""
MOMENTUM-X Health & Control HTTP Server

### ARCHITECTURAL CONTEXT
Provides a lightweight HTTP control plane for remote monitoring and management.
Runs alongside the existing MetricsServer (port 9090) on a separate port.

Endpoints:
  GET  /health    → Detailed health status (heartbeat, phase, positions, uptime)
  GET  /status    → Full system status (positions, P&L, signals, phase)
  POST /pause     → Pause new entries (existing positions continue monitoring)
  POST /resume    → Resume normal trading
  POST /shutdown  → Graceful shutdown (close positions, then exit)

### DESIGN DECISIONS
- Separate from MetricsServer (different concerns: ops vs observability)
- Port 9091 by default (MetricsServer uses 9090)
- POST for state-changing operations, GET for reads
- Simple auth via shared secret in X-Auth-Token header (optional)
- Non-blocking daemon thread (same pattern as MetricsServer)
- Control callbacks allow main loop to respond to commands
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time as time_mod
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

logger = logging.getLogger(__name__)


class TradingControlState:
    """Thread-safe state container for control commands.

    The main trading loop checks this state each iteration to respond
    to remote control commands.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._paused: bool = False
        self._shutdown_requested: bool = False
        self._phase: str = "INIT"
        self._positions: list[dict] = []
        self._daily_pnl: float = 0.0
        self._trades_today: int = 0
        self._start_time: float = time_mod.monotonic()
        self._start_utc: str = datetime.now(timezone.utc).isoformat()
        # D107 WS3: System reliability score tracking
        self._sessions_attempted: int = 0
        self._sessions_with_trades: int = 0

    @property
    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    @is_paused.setter
    def is_paused(self, value: bool) -> None:
        with self._lock:
            self._paused = value

    @property
    def shutdown_requested(self) -> bool:
        with self._lock:
            return self._shutdown_requested

    @shutdown_requested.setter
    def shutdown_requested(self, value: bool) -> None:
        with self._lock:
            self._shutdown_requested = value

    # ── D107 WS3: System Reliability Score ──────────────────────

    @property
    def reliability_score(self) -> float:
        """Ratio of sessions with trades to total sessions attempted.

        Returns 0.0 if no sessions have been recorded yet.
        Target: > 0.90 for paper trading graduation.
        """
        with self._lock:
            if self._sessions_attempted == 0:
                return 0.0
            return self._sessions_with_trades / self._sessions_attempted

    def record_session(self, had_trades: bool) -> None:
        """Record a trading session's outcome for reliability tracking.

        Call once at session end (or at startup for session start tracking).

        Args:
            had_trades: True if the session executed at least one trade.
        """
        with self._lock:
            self._sessions_attempted += 1
            if had_trades:
                self._sessions_with_trades += 1

    def update_status(
        self,
        phase: str | None = None,
        positions: list[dict] | None = None,
        daily_pnl: float | None = None,
        trades_today: int | None = None,
    ) -> None:
        """Update status fields (called by main loop)."""
        with self._lock:
            if phase is not None:
                self._phase = phase
            if positions is not None:
                self._positions = positions
            if daily_pnl is not None:
                self._daily_pnl = daily_pnl
            if trades_today is not None:
                self._trades_today = trades_today

    def get_status(self) -> dict:
        """Get full status snapshot."""
        with self._lock:
            uptime_s = time_mod.monotonic() - self._start_time
            rel = (self._sessions_with_trades / self._sessions_attempted
                   if self._sessions_attempted > 0 else 0.0)
            return {
                "phase": self._phase,
                "paused": self._paused,
                "shutdown_requested": self._shutdown_requested,
                "positions": self._positions,
                "daily_pnl": self._daily_pnl,
                "trades_today": self._trades_today,
                "uptime_seconds": round(uptime_s, 1),
                "uptime_human": _format_duration(uptime_s),
                "started_at": self._start_utc,
                # D107 WS3: Reliability score
                "reliability_score": round(rel, 4),
                "sessions_attempted": self._sessions_attempted,
                "sessions_with_trades": self._sessions_with_trades,
            }


def _format_duration(seconds: float) -> str:
    """Format seconds into human-readable duration."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    return f"{hours}h {minutes}m"


class HealthControlHandler(BaseHTTPRequestHandler):
    """HTTP handler for health checks and remote control."""

    # Set by HealthControlServer before starting
    control_state: TradingControlState | None = None
    heartbeat_watchdog: Any = None
    auth_token: str | None = None

    def do_GET(self) -> None:
        if self.path == "/health":
            self._serve_health()
        elif self.path == "/status":
            if not self._check_auth():
                return
            self._serve_status()
        else:
            self._send_json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        if not self._check_auth():
            return

        if self.path == "/pause":
            self._handle_pause()
        elif self.path == "/resume":
            self._handle_resume()
        elif self.path == "/shutdown":
            self._handle_shutdown()
        else:
            self._send_json(404, {"error": "Not found"})

    def _check_auth(self) -> bool:
        """Validate auth token if configured."""
        if not self.auth_token:
            return True  # No auth configured

        token = self.headers.get("X-Auth-Token", "")
        if token != self.auth_token:
            self._send_json(401, {"error": "Unauthorized"})
            return False
        return True

    def _serve_health(self) -> None:
        """Lightweight health check (no auth required)."""
        health: dict[str, Any] = {
            "status": "ok",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if self.control_state:
            status = self.control_state.get_status()
            health["phase"] = status["phase"]
            health["paused"] = status["paused"]
            health["uptime_seconds"] = status["uptime_seconds"]

        if self.heartbeat_watchdog:
            hb = self.heartbeat_watchdog.status
            health["heartbeat"] = hb
            if not hb.get("alive", True):
                health["status"] = "degraded"

        self._send_json(200, health)

    def _serve_status(self) -> None:
        """Full system status (auth required)."""
        if not self.control_state:
            self._send_json(503, {"error": "Control state not initialized"})
            return

        status = self.control_state.get_status()
        status["timestamp"] = datetime.now(timezone.utc).isoformat()

        if self.heartbeat_watchdog:
            status["heartbeat"] = self.heartbeat_watchdog.status

        self._send_json(200, status)

    def _handle_pause(self) -> None:
        """Pause new entries."""
        if self.control_state:
            self.control_state.is_paused = True
            logger.warning("REMOTE CONTROL: Trading PAUSED via HTTP")
            self._send_json(200, {"action": "paused", "message": "New entries paused"})
        else:
            self._send_json(503, {"error": "Control state not initialized"})

    def _handle_resume(self) -> None:
        """Resume trading."""
        if self.control_state:
            self.control_state.is_paused = False
            logger.info("REMOTE CONTROL: Trading RESUMED via HTTP")
            self._send_json(200, {"action": "resumed", "message": "Trading resumed"})
        else:
            self._send_json(503, {"error": "Control state not initialized"})

    def _handle_shutdown(self) -> None:
        """Request graceful shutdown."""
        if self.control_state:
            self.control_state.shutdown_requested = True
            logger.warning("REMOTE CONTROL: SHUTDOWN requested via HTTP")
            self._send_json(200, {
                "action": "shutdown_requested",
                "message": "Graceful shutdown initiated — closing positions",
            })
        else:
            self._send_json(503, {"error": "Control state not initialized"})

    def _send_json(self, code: int, data: dict) -> None:
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress default stderr logging."""
        pass


class HealthControlServer:
    """HTTP server for health monitoring and remote control.

    Usage:
        control_state = TradingControlState()
        server = HealthControlServer(
            control_state=control_state,
            heartbeat=watchdog,
            port=9091,
        )
        server.start()

        # In trading loop:
        if control_state.is_paused:
            continue  # Skip new entries
        if control_state.shutdown_requested:
            break  # Exit loop

        server.stop()
    """

    def __init__(
        self,
        control_state: TradingControlState,
        heartbeat: Any = None,
        host: str = "0.0.0.0",
        port: int = 9091,
        auth_token: str | None = None,
    ) -> None:
        self._control_state = control_state
        self._heartbeat = heartbeat
        self._host = host
        self._port = port
        self._auth_token = auth_token or os.environ.get("MOMENTUM_HEALTH_TOKEN")
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the health/control server in a daemon thread."""
        # Configure handler class attributes
        HealthControlHandler.control_state = self._control_state
        HealthControlHandler.heartbeat_watchdog = self._heartbeat
        HealthControlHandler.auth_token = self._auth_token

        # D217: Allow port reuse so restart after crash doesn't fail with
        # "Address already in use" from TIME_WAIT socket state.
        # HTTPServer.allow_reuse_address must be set as class attr before __init__
        HTTPServer.allow_reuse_address = True
        try:
            self._server = HTTPServer((self._host, self._port), HealthControlHandler)
        except OSError as bind_err:
            logger.error(
                "D217 CRITICAL: Health server failed to bind %s:%d — %s. "
                "External watchdog will NOT be able to reach /health. "
                "Check for stale process on this port.",
                self._host, self._port, bind_err,
            )
            self._server = None
            raise
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="health-control-server",
        )
        self._thread.start()

        auth_status = "enabled" if self._auth_token else "disabled"
        logger.info(
            "Health/control server started at http://%s:%d (auth=%s)",
            self._host, self._port, auth_status,
        )

    def stop(self) -> None:
        """Stop the health/control server."""
        if self._server:
            self._server.shutdown()
            self._server = None
            logger.info("Health/control server stopped")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def port(self) -> int:
        return self._port
