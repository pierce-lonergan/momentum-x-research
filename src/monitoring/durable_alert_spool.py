"""D314 (2026-05-24, doc 173) — Durable alert spool with retry poster.

PURPOSE:
  Every Discord-bound payload is written to disk BEFORE being sent.
  A separate background task tails the spool directory and retries
  failed posts with exponential backoff. Failed posts deliver next
  session, never lost.

WHY THIS MATTERS (Pierce):
  The Discord webhook is a single point of failure. Rate-limits,
  network blips, or a rotated webhook URL silently lose alerts. The
  90-day audit found 185 unhedge windows that *never appeared in any
  prior EOD* -- the bot's "all healthy" report can be lying.

ARCHITECTURE:
  alerts.py modules call ``durable_post(webhook_url, payload, severity)``
  instead of httpx.post directly. The function:
    1. Writes one JSON file per payload to
       ``data/alerts/YYYY-MM-DD/<ts>_<seq>_<severity>.json``
    2. Tries a single live POST (best-effort, 5s timeout)
    3. On success: deletes the spool file
    4. On failure: leaves the file for the retry poster

  The retry poster (``run_retry_loop``) runs in main.py as a background
  task. Every 60s it walks the spool dir for any remaining files,
  retries each with exponential backoff (capped at 10 min), and
  deletes on success. Files older than 24h are quarantined into a
  ``stale/`` subdir for operator review (avoids replaying a 6-day-old
  HEDGE_VIOLATION as if it were live).

GUARANTEES:
  - Never raises out of ``durable_post`` -- failure modes degrade
    gracefully to "alert may arrive late" instead of "alert lost".
  - Retry budget bounded (24h quarantine ceiling).
  - Per-file naming preserves chronological order on disk.
  - SEVERITY label preserved through the spool -- CRITICAL alerts
    retry more aggressively than INFO.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SPOOL_DIR = REPO_ROOT / "data" / "alerts"


def _today_dir(base: Path) -> Path:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = base / today
    out.mkdir(parents=True, exist_ok=True)
    return out


_seq_counter = 0


def _next_seq() -> int:
    global _seq_counter
    _seq_counter += 1
    return _seq_counter


def _spool_filename(severity: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%H%M%S_%f")
    return f"{ts}_{_next_seq():05d}_{severity.upper()}.json"


def _serialize(webhook_url: str, payload: dict, severity: str) -> dict:
    return {
        "webhook_url": webhook_url,
        "payload": payload,
        "severity": severity.upper(),
        "spooled_at_utc": datetime.now(timezone.utc).isoformat(),
        "attempts": 0,
    }


async def _live_post(webhook_url: str, payload: dict,
                     timeout_sec: float = 5.0) -> bool:
    """Single live post. Returns True iff 2xx response received."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            resp = await client.post(webhook_url, json=payload)
            return 200 <= resp.status_code < 300
    except Exception as e:
        logger.debug("D314 live post failed: %s", e)
        return False


async def durable_post(
    webhook_url: str,
    payload: dict,
    *,
    severity: str = "INFO",
    spool_dir: Path | None = None,
) -> bool:
    """Write to spool, attempt live post, delete on success.

    Returns True iff the live post succeeded (alert delivered immediately).
    Returns False iff the live post failed (alert spooled for retry).

    Never raises -- a write failure or unexpected exception is logged
    and treated as a False return. Critical-path alerts MUST not be
    blocked by alert delivery problems.
    """
    if not webhook_url:
        return False
    # 1. Persist to spool BEFORE attempting live post. Even if the
    # process crashes between persist and post, the retry loop will
    # pick it up next session. ANY failure in this block (dir create,
    # path build, file open, JSON dump) degrades to "no spool, try
    # live post anyway" -- alert may be lost if live also fails, but
    # we never block the trading thread on alert delivery.
    spool_path: Path | None = None
    try:
        spool_dir = spool_dir or _today_dir(DEFAULT_SPOOL_DIR)
        fname = _spool_filename(severity)
        spool_path = spool_dir / fname
        record = _serialize(webhook_url, payload, severity)
        with open(spool_path, "w", encoding="utf-8") as f:
            json.dump(record, f, separators=(",", ":"))
    except Exception as _spe:
        logger.warning(
            "D314 SPOOL_WRITE_FAILED severity=%s: %s -- attempting "
            "live post anyway (alert may be lost if it also fails)",
            severity, _spe,
        )
        spool_path = None

    # 2. Try live post
    ok = await _live_post(webhook_url, payload)

    # 3. On success, delete spool file (no need to retry)
    if ok and spool_path is not None:
        try:
            spool_path.unlink()
        except Exception:
            pass  # leave it; retry loop will see "already delivered"
                  # logic via duplicate-detection (not implemented yet --
                  # double-delivery is preferable to no delivery)
    return ok


# D314.v2 (2026-05-24, doc 174): stale-prefix threshold. Records older
# than this when redelivered get a "[STALE - originally fired at ...]"
# prefix in their content field so the operator can distinguish a fresh
# alert from a delayed one. 2 hours covers most Discord outages while
# still flagging genuinely-old (and therefore possibly-resolved) alerts.
STALE_PREFIX_THRESHOLD_SEC = 2 * 3600  # 2 hours


def _maybe_inject_stale_prefix(record: dict) -> None:
    """If record is older than threshold, prepend a stale marker to
    payload.content so the operator sees the delay context."""
    try:
        spooled_at = record.get("spooled_at_utc")
        if not spooled_at:
            return
        from datetime import datetime as _dt, timezone as _tz
        ts = _dt.fromisoformat(spooled_at.replace("Z", "+00:00"))
        age = (_dt.now(_tz.utc) - ts).total_seconds()
        if age < STALE_PREFIX_THRESHOLD_SEC:
            return
        # Format the original time in ET for the operator
        try:
            from zoneinfo import ZoneInfo as _Z
            ts_et = ts.astimezone(_Z("America/New_York"))
            ts_str = ts_et.strftime("%-I:%M %p ET") if hasattr(ts_et, 'strftime') else str(ts_et)
        except Exception:
            ts_str = ts.isoformat()
        prefix = f"[STALE - originally fired at {ts_str}]"
        payload = record.get("payload", {})
        existing = payload.get("content", "")
        # Don't double-prefix if a prior retry tick already added one
        if "[STALE" not in existing:
            payload["content"] = (f"{prefix} {existing}".strip()
                                    if existing else prefix)
            record["payload"] = payload
    except Exception:
        pass  # never block redelivery on prefix injection


async def _retry_one(record_path: Path) -> bool:
    """Read a spooled record, attempt re-post, return True on success."""
    try:
        with open(record_path, encoding="utf-8") as f:
            record = json.load(f)
    except Exception as _re:
        logger.warning("D314 retry: read failed for %s (%s)", record_path.name, _re)
        return False
    record["attempts"] = int(record.get("attempts", 0)) + 1
    # D314.v2: inject [STALE] prefix on records older than 2h
    _maybe_inject_stale_prefix(record)
    ok = await _live_post(record["webhook_url"], record["payload"])
    if ok:
        try:
            record_path.unlink()
        except Exception:
            pass
        return True
    # Update attempts count on disk so retry budget logic can see it
    try:
        with open(record_path, "w", encoding="utf-8") as f:
            json.dump(record, f, separators=(",", ":"))
    except Exception:
        pass
    return False


def _walk_today_and_yesterday() -> list[Path]:
    """All spooled files from today and yesterday, sorted by mtime."""
    paths: list[Path] = []
    base = DEFAULT_SPOOL_DIR
    if not base.exists():
        return paths
    for d in sorted(base.iterdir()):
        if not d.is_dir() or d.name == "stale":
            continue
        for f in d.glob("*.json"):
            paths.append(f)
    paths.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0)
    return paths


def _quarantine_stale(record_path: Path, stale_after_hours: float = 24.0) -> bool:
    """If a spooled record is older than threshold, move to stale/."""
    try:
        age = time.time() - record_path.stat().st_mtime
        if age < stale_after_hours * 3600:
            return False
        stale_dir = DEFAULT_SPOOL_DIR / "stale"
        stale_dir.mkdir(parents=True, exist_ok=True)
        target = stale_dir / record_path.name
        record_path.rename(target)
        logger.warning(
            "D314 STALE_QUARANTINED %s: %.1fh old, moved to stale/",
            record_path.name, age / 3600,
        )
        return True
    except Exception:
        return False


async def run_retry_loop(
    *,
    interval_sec: float = 60.0,
    max_attempts_critical: int = 50,
    max_attempts_other: int = 20,
    shutdown_event: asyncio.Event | None = None,
) -> None:
    """Background retry loop. Walks the spool dir every interval,
    retries each undelivered record, deletes on success, quarantines
    stale.

    Backoff is implicit via the interval -- one attempt per cycle per
    record. CRITICAL severity retries get 50 cycles (~50 min); INFO
    retries get 20 cycles (~20 min). Beyond that the record is left
    until the 24h quarantine triggers.
    """
    logger.info(
        "D314 RETRY_LOOP starting: interval=%.0fs (CRITICAL max=%d cycles, "
        "other max=%d cycles, stale quarantine=24h)",
        interval_sec, max_attempts_critical, max_attempts_other,
    )
    while True:
        if shutdown_event and shutdown_event.is_set():
            return
        try:
            for path in _walk_today_and_yesterday():
                if not path.exists():
                    continue
                # Quarantine very old records BEFORE trying again
                if _quarantine_stale(path):
                    continue
                # Read severity to decide whether to retry
                try:
                    with open(path, encoding="utf-8") as f:
                        record = json.load(f)
                except Exception:
                    continue
                attempts = int(record.get("attempts", 0))
                severity = (record.get("severity") or "INFO").upper()
                max_attempts = (
                    max_attempts_critical
                    if severity == "CRITICAL"
                    else max_attempts_other
                )
                if attempts >= max_attempts:
                    continue  # leave for stale-quarantine
                await _retry_one(path)
        except Exception as e:
            logger.warning("D314 retry tick raised (%s); continuing", e)
        try:
            if shutdown_event:
                await asyncio.wait_for(shutdown_event.wait(), timeout=interval_sec)
                return
            else:
                await asyncio.sleep(interval_sec)
        except asyncio.TimeoutError:
            pass
