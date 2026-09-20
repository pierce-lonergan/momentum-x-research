"""doc 205: Incident bus — the durable, never-raises event log the Operator polls.

The bot EMITS incidents (errors / critical decisions / anomalies) and keeps trading;
the Operator (doc 204) reads them out-of-band, triages, and acts within its autonomy tier.
Trading NEVER blocks on this and emit_incident NEVER raises into the caller.

Storage: append-only JSONL at data/ops/incidents_<session_date>.jsonl. A CRITICAL incident
also drops a data/ops/WAKE sentinel so an event-driven Operator session can spawn
immediately instead of waiting for the next scheduled pulse.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_NY = ZoneInfo("America/New_York")
_ROOT = Path(__file__).resolve().parent.parent.parent
_OPS_DIR = _ROOT / "data" / "ops"
_WAKE = _OPS_DIR / "WAKE"

SEVERITIES = ("INFO", "WARN", "CRITICAL", "DECISION")
# in-process dedup window: skip identical dedup_keys seen within this many seconds
_DEDUP_WINDOW_SEC = 300
_recent: dict[str, float] = {}
# monotonic-ish counter for unique ids within a process-second (no Date.now in tests, fine here)
_seq = 0


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def is_enabled() -> bool:
    """Kill switch: OPS_INCIDENT_BUS_ENABLED (default on)."""
    return os.environ.get("OPS_INCIDENT_BUS_ENABLED", "true").strip().lower() not in (
        "false", "0", "no", "off")


def _bus_file(session_date: str) -> Path:
    return _OPS_DIR / f"incidents_{session_date}.jsonl"


def emit_incident(
    kind: str,
    severity: str = "WARN",
    *,
    ticker: str | None = None,
    context: dict | None = None,
    suggested: list[str] | None = None,
    dedup_key: str | None = None,
    session_date: str | None = None,
) -> bool:
    """Record one incident. Returns True if written. NEVER raises into the caller.

    Args:
        kind: short machine label, e.g. "CIRCUIT_BREAKER_TRIP", "GHOST_DETECTED",
              "RECON_LETHAL", "FADE_SHORT_CANDIDATE", "FLAG_LOOKS_WRONG".
        severity: INFO | WARN | CRITICAL | DECISION.
        ticker: optional symbol the incident is about.
        context: act-cold detail (state snapshot, log tail, file:line, numbers).
        suggested: optional list of suggested operator actions (hints, not commands).
        dedup_key: if given, identical keys within ~5 min are suppressed (anti-flap).
    """
    if not is_enabled():
        return False
    global _seq
    try:
        sev = severity.upper() if severity else "WARN"
        if sev not in SEVERITIES:
            sev = "WARN"
        ts = _now_utc()
        # dedup
        if dedup_key:
            last = _recent.get(dedup_key)
            now_s = ts.timestamp()
            if last is not None and (now_s - last) < _DEDUP_WINDOW_SEC:
                return False
            _recent[dedup_key] = now_s
        # An incident SYNTHESIZED for a past session must be filed under THAT
        # session, not under "now": read_incidents(), the pager and
        # pipeline_health_check all index by date, so a backfilled incident filed
        # under today is both invisible where it belongs and noise where it lands.
        session_date = session_date or ts.astimezone(_NY).strftime("%Y-%m-%d")
        _seq += 1
        inc_id = f"{ts.strftime('%Y%m%dT%H%M%S')}_{_seq}_{kind}"
        row = {
            "id": inc_id, "kind": str(kind), "severity": sev,
            "ticker": str(ticker) if ticker else None,
            "ts_utc": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "session_date": session_date,
            "context": context or {},
            "suggested": list(suggested) if suggested else [],
            "resolved": False, "resolution": None,
        }
        _OPS_DIR.mkdir(parents=True, exist_ok=True)
        with _bus_file(session_date).open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        if sev == "CRITICAL":
            try:
                _WAKE.write_text(f"{inc_id}\n{ts.isoformat()}\n", encoding="utf-8")
            except Exception:
                pass
        logger.info("OPS incident: [%s] %s %s", sev, kind, ticker or "")
        return True
    except Exception as e:  # noqa: BLE001 — telemetry must never break trading
        logger.warning("incident_bus.emit failed for %s: %s", kind, e)
        return False


def read_incidents(
    session_date: str | None = None,
    *,
    min_severity: str | None = None,
    unresolved_only: bool = False,
) -> list[dict]:
    """Read the day's incidents (newest last). Safe; returns [] on any error."""
    try:
        date = session_date or _now_utc().astimezone(_NY).strftime("%Y-%m-%d")
        f = _bus_file(date)
        if not f.exists():
            return []
        rows = []
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
        if min_severity:
            order = {s: i for i, s in enumerate(("INFO", "WARN", "DECISION", "CRITICAL"))}
            floor = order.get(min_severity.upper(), 0)
            rows = [r for r in rows if order.get(r.get("severity", "INFO"), 0) >= floor]
        if unresolved_only:
            rows = [r for r in rows if not r.get("resolved")]
        return rows
    except Exception as e:  # noqa: BLE001
        logger.warning("incident_bus.read failed: %s", e)
        return []


def clear_wake() -> None:
    """Operator calls this once it has handled the wake."""
    try:
        if _WAKE.exists():
            _WAKE.unlink()
    except Exception:
        pass


def has_wake() -> bool:
    return _WAKE.exists()
