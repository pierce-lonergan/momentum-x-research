"""ShadowLogger — JSONL append-only writer for shadow telemetry.

Writes to data/shadow/shadow_<YYYY-MM-DD>.jsonl. Each line is a self-contained
JSON object with a `kind` field discriminating composite vs inverted entries.

Design constraints:
  - Append-only file I/O. NEVER reads shadow files.
  - Failures are logged as WARNING and swallowed — shadow logging must never
    raise into the production hot path.
  - Thread-safe via single lock per process (cheap; we write at low rate).
  - Lazy file open: only creates the file when the first entry is logged.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
_SHADOW_DIR: Path = _PROJECT_ROOT / "data" / "shadow"


class ShadowLogger:
    """Per-process append-only writer for shadow JSONL.

    Use `get_shadow_logger()` to get the singleton. The singleton pattern is
    fine here because (a) we only write, (b) the file handle is opened lazily,
    (c) all writes are guarded by a mutex.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._file_handle = None
        self._current_date: str | None = None

    def _path_for(self, session_date: str) -> Path:
        return _SHADOW_DIR / f"shadow_{session_date}.jsonl"

    def _ensure_open(self, session_date: str) -> None:
        """Open (or rotate to) today's shadow file. Caller holds the lock."""
        if self._current_date == session_date and self._file_handle is not None:
            return
        # Close any prior handle (date rollover)
        if self._file_handle is not None:
            try:
                self._file_handle.close()
            except Exception as e:
                logger.warning("shadow logger: failed to close prior handle: %s", e)
        _SHADOW_DIR.mkdir(parents=True, exist_ok=True)
        self._file_handle = open(self._path_for(session_date), "a", encoding="utf-8", newline="\n")
        self._current_date = session_date

    def log(self, entry: dict[str, Any]) -> None:
        """Append one entry. Failures logged + swallowed.

        `entry` MUST contain a `session_date` field (YYYY-MM-DD) to determine
        which file to write to. If missing, today's UTC date is used.
        """
        try:
            session_date = entry.get("session_date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
            with self._lock:
                self._ensure_open(session_date)
                self._file_handle.write(json.dumps(entry, default=str) + "\n")
                self._file_handle.flush()
        except Exception as e:
            logger.warning(
                "shadow logger: write failed (kind=%s ticker=%s): %s",
                entry.get("kind"), entry.get("ticker"), e,
            )

    def close(self) -> None:
        """Close the file handle. Safe to call even if never opened."""
        with self._lock:
            if self._file_handle is not None:
                try:
                    self._file_handle.close()
                except Exception as e:
                    logger.warning("shadow logger: close failed: %s", e)
                self._file_handle = None
                self._current_date = None


# ── Singleton accessor ──────────────────────────────────────────────────


_singleton_lock = threading.Lock()
_singleton: ShadowLogger | None = None


def get_shadow_logger() -> ShadowLogger:
    """Return the per-process singleton ShadowLogger.

    Cheap: no I/O until first log() call.
    """
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = ShadowLogger()
    return _singleton
