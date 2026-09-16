"""
MOMENTUM-X Internal Heartbeat Watchdog

### ARCHITECTURAL CONTEXT
Self-monitoring daemon that detects if the main trading loop has become
unresponsive. If no heartbeat is received for `timeout_minutes` during
market hours, the watchdog triggers graceful shutdown.

### DESIGN DECISIONS
- Runs in a daemon thread (same as MetricsServer pattern)
- Trading loop calls heartbeat.pulse() periodically
- If pulse() is not called within timeout, watchdog initiates shutdown
- Only active during market hours (9:00 AM - 4:30 PM ET)
- Pre-market (3:30-9:00 AM) uses a longer timeout since scan loops are slow
- Thread-safe via threading.Event
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time as time_mod
from datetime import datetime, time, timezone, timedelta

logger = logging.getLogger(__name__)

# Eastern Time — use ZoneInfo for correct EST/EDT handling.
# Fallback to UTC-5 if zoneinfo unavailable (rare).
try:
    from zoneinfo import ZoneInfo
    _ET_TZ = ZoneInfo("America/New_York")
except ImportError:
    # Sweep fix: Log warning — fixed UTC-5 ignores EDT (UTC-4), causing
    # timeout thresholds to shift by 1 hour during daylight saving time.
    logger.warning(
        "zoneinfo unavailable — heartbeat using fixed UTC-5 (EST only). "
        "EDT transitions will shift timeout boundaries by 1 hour."
    )
    _ET_TZ = timezone(timedelta(hours=-5))


class HeartbeatWatchdog:
    """Internal watchdog that monitors the trading loop's liveness.

    Usage:
        watchdog = HeartbeatWatchdog(timeout_minutes=30)
        watchdog.start()

        # In the trading loop:
        while running:
            watchdog.pulse()  # Signal "I'm alive"
            # ... do work ...

        watchdog.stop()
    """

    def __init__(
        self,
        timeout_minutes: float = 30.0,
        premarket_timeout_minutes: float = 60.0,
        shutdown_callback: callable | None = None,
        webhook_url: str = "",
    ) -> None:
        """
        Args:
            timeout_minutes: Max minutes without pulse during market hours
                before triggering shutdown.
            premarket_timeout_minutes: Max minutes without pulse during
                pre-market (3:30-9:30 AM ET) before triggering shutdown.
                Pre-market loops are slower (60s scan intervals).
            shutdown_callback: Optional callback invoked on timeout.
                If None, sends SIGTERM to own process.
            webhook_url: D107 WS4: External heartbeat webhook URL.
                If non-empty, pings this URL every 5th pulse via fire-and-forget
                daemon thread. Uses stdlib urllib (no new dependencies).
                Designed for Healthchecks.io, Uptime Robot, etc.
        """
        self._timeout = timeout_minutes * 60  # Convert to seconds
        self._premarket_timeout = premarket_timeout_minutes * 60
        self._shutdown_callback = shutdown_callback
        self._last_pulse: float = time_mod.monotonic()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._phase: str = "INIT"
        self._pulse_count: int = 0
        # D107 WS4: External heartbeat webhook
        self._webhook_url: str = webhook_url
        self._webhook_failures: int = 0

    def start(self) -> None:
        """Start the watchdog monitoring thread."""
        self._last_pulse = time_mod.monotonic()
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._monitor_loop,
            daemon=True,
            name="heartbeat-watchdog",
        )
        self._thread.start()
        logger.info(
            "Heartbeat watchdog started (market timeout=%dmin, premarket timeout=%dmin)",
            int(self._timeout / 60),
            int(self._premarket_timeout / 60),
        )

    def stop(self) -> None:
        """Stop the watchdog monitoring thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None
        logger.info("Heartbeat watchdog stopped")

    def pulse(self, phase: str = "") -> None:
        """Signal that the trading loop is alive.

        Call this at least once per timeout period.

        Args:
            phase: Optional current phase name for diagnostics.
        """
        with self._lock:
            self._last_pulse = time_mod.monotonic()
            self._pulse_count += 1
            if phase:
                self._phase = phase
            count = self._pulse_count

        # D107 WS4: Ping external webhook every 5th pulse (~5 minutes)
        if self._webhook_url and count % 5 == 0:
            t = threading.Thread(
                target=self._ping_webhook,
                daemon=True,
                name="heartbeat-webhook-ping",
            )
            t.start()

    @property
    def seconds_since_last_pulse(self) -> float:
        """Seconds elapsed since last pulse() call."""
        with self._lock:
            return time_mod.monotonic() - self._last_pulse

    @property
    def status(self) -> dict:
        """Return watchdog status for health endpoint."""
        with self._lock:
            elapsed = time_mod.monotonic() - self._last_pulse
            timeout = self._get_active_timeout()
            return {
                "alive": elapsed < timeout,
                "seconds_since_pulse": round(elapsed, 1),
                "timeout_seconds": timeout,
                "phase": self._phase,
                "pulse_count": self._pulse_count,
            }

    def _ping_webhook(self) -> None:
        """D107 WS4: Fire-and-forget POST to external webhook URL.

        Uses stdlib urllib.request (no new dependencies). Silently stops
        logging after 3 consecutive failures to avoid log spam.
        """
        import urllib.request
        try:
            req = urllib.request.Request(
                self._webhook_url,
                data=b"",
                method="POST",
            )
            req.add_header("User-Agent", "Momentum-X Heartbeat/D107")
            urllib.request.urlopen(req, timeout=5)
            self._webhook_failures = 0
        except Exception as e:
            self._webhook_failures += 1
            if self._webhook_failures <= 3:
                logger.debug("D107: Webhook ping failed (%d): %s", self._webhook_failures, e)

    def _get_active_timeout(self) -> float:
        """Get the active timeout based on current time of day."""
        now_et = datetime.now(timezone.utc).astimezone(_ET_TZ)
        # Sweep fix: use minute-level precision for 9:30 AM boundary.
        # Previous hour-level check used premarket timeout until 10:00 AM,
        # leaving a 30-minute gap after market open with doubled tolerance.
        minutes_since_midnight = now_et.hour * 60 + now_et.minute

        # Pre-market: 3:00-9:30 AM ET — longer timeout
        if 180 <= minutes_since_midnight < 570:  # 3:00 AM to 9:30 AM
            return self._premarket_timeout

        # Market hours + post-market: 9:30 AM - 5:00 PM ET
        if 570 <= minutes_since_midnight < 1020:  # 9:30 AM to 5:00 PM
            return self._timeout

        # Outside hours: very long timeout (system may be idle)
        return self._premarket_timeout * 2

    def _monitor_loop(self) -> None:
        """Background monitoring loop. Checks every 60 seconds."""
        check_interval = 60.0  # Check every minute

        while not self._stop_event.is_set():
            self._stop_event.wait(check_interval)
            if self._stop_event.is_set():
                break

            with self._lock:
                elapsed = time_mod.monotonic() - self._last_pulse
                timeout = self._get_active_timeout()
                phase = self._phase

            if elapsed > timeout:
                logger.critical(
                    "HEARTBEAT TIMEOUT: No pulse for %.0f seconds "
                    "(timeout=%.0fs, phase=%s, pulses=%d). Triggering shutdown.",
                    elapsed, timeout, phase, self._pulse_count,
                )
                self._trigger_shutdown()
                break

            # Warn at 75% of timeout
            if elapsed > timeout * 0.75:
                logger.warning(
                    "Heartbeat warning: %.0f/%.0fs since last pulse (phase=%s)",
                    elapsed, timeout, phase,
                )

    def _trigger_shutdown(self) -> None:
        """Initiate graceful shutdown."""
        if self._shutdown_callback:
            try:
                self._shutdown_callback()
            except Exception:
                logger.exception("Shutdown callback failed")
        else:
            # Default: send SIGTERM to own process for graceful shutdown
            pid = os.getpid()
            logger.info("Sending SIGTERM to PID %d", pid)
            if sys.platform == "win32":
                # Windows doesn't support SIGTERM well — use SIGBREAK or os._exit
                try:
                    os.kill(pid, signal.CTRL_BREAK_EVENT)
                except (OSError, AttributeError):
                    logger.warning("SIGTERM failed on Windows, forcing exit")
                    os._exit(1)
            else:
                os.kill(pid, signal.SIGTERM)
