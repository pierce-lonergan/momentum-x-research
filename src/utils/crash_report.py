"""
D221 Bug #3 (Mon 2026-04-20): Crash-report-on-exception minimum-viable.

When the orchestrator loop dies with an unhandled exception, three things
should happen before the process exits:

  1. A FATAL log line with the full traceback (the existing handler at
     main.py already does this).
  2. A self-contained ``crash_report_<ts>.json`` written next to other
     operational state, so post-mortem doesn't depend on log retention
     (which is 8 days). Includes the last heartbeat snapshot.
  3. A *distinguishable* exit code so Task Scheduler can tell the
     difference between a clean shutdown (0), an environment problem
     (1, set by Python on uncaught), an unhandled crash from our
     loop (90), and an asyncio cancellation that propagated out (91).

This module is the minimum-viable shape of (2) and (3). It is NOT the
broader graceful-recovery work — that's Tuesday afternoon. The goal
tonight is: when the next 09:40 EDT crash happens, the operator has a
single artifact on disk that says what crashed and what the system was
doing, and the watchdog sees an exit code that distinguishes "I died"
from "I was killed".

History:
  - Mon 2026-04-20 09:40 EDT: paper-trading session crashed nine minutes
    after open. Watchdog restarted with no signal that anything was
    wrong. We discovered the crash by chance, not by alert. Three
    compound bugs surfaced in the post-mortem (silent pandas_ta
    dormancy, NameError scope-leak in _build_trade_verdict, alpaca_rest
    circuit-breaker triggered the actual crash). At least the third
    one would have been caught much faster with a crash report on disk.

Scope deliberately narrow:
  - Does NOT install signal handlers.
  - Does NOT touch the asyncio loop.
  - Does NOT do graceful position-close on the way out.
  - Does NOT alert Discord (the heartbeat watchdog already handles
    process death).

All of those are Tuesday work. Tonight: persist the crash, exit with
a code, move on.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── Exit codes ──────────────────────────────────────────────────────
# 0    = clean shutdown
# 1    = unhandled at the Python level (set by interpreter); avoid colliding
# 90   = unhandled exception caught by our orchestrator-loop wrapper
# 91   = asyncio.CancelledError that propagated all the way out
#        (almost always indicates we lost our event-loop ownership)

EXIT_CODE_UNHANDLED: int = 90
EXIT_CODE_CANCELLED: int = 91


# ── Paths ───────────────────────────────────────────────────────────


def _repo_root() -> Path:
    """Repo root via __file__. Avoids depending on cwd, which Task
    Scheduler may set differently than interactive runs."""
    return Path(__file__).resolve().parents[2]


def _crash_report_dir() -> Path:
    """`data/crash_reports/` under the repo root. Created lazily by
    ``write_crash_report``."""
    return _repo_root() / "data" / "crash_reports"


def _heartbeat_path() -> Path:
    """The canonical heartbeat file written by main.py during cmd_paper.
    Same path computation as main.py:1162."""
    return _repo_root() / "data" / "heartbeat.json"


# ── Heartbeat snapshot ──────────────────────────────────────────────


def _read_last_heartbeat() -> dict[str, Any] | None:
    """Best-effort read of the last heartbeat. Returns None on any
    failure; callers should treat None as "no heartbeat available"
    rather than as an error.

    Adversarial inputs handled:
      - Path doesn't exist                  -> None
      - Path exists but not readable        -> None (warning logged)
      - Path is a directory, not a file     -> None
      - File contains malformed JSON        -> None (warning logged)
      - File is empty                       -> None
    """
    p = _heartbeat_path()
    if not p.exists() or not p.is_file():
        return None
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("crash_report: heartbeat unreadable (%s): %s", p, e)
        return None
    if not text.strip():
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        logger.warning("crash_report: heartbeat malformed JSON (%s): %s", p, e)
        return None
    if not isinstance(data, dict):
        # Heartbeat schema is always an object; a list/string here means
        # someone wrote a different shape. Surface it but don't crash.
        return {"unexpected_shape": str(type(data).__name__), "value": str(data)[:500]}
    return data


# ── Public API ──────────────────────────────────────────────────────


def write_crash_report(
    exception: BaseException,
    context: dict[str, Any] | None = None,
    crash_dir: Path | None = None,
) -> Path | None:
    """Persist a crash report to `data/crash_reports/crash_report_<ts>.json`.

    Returns the written path on success, None if writing itself failed
    (in which case we've already logged but should not raise — the
    caller is on its way out the door and a second exception here just
    obscures the first).

    Parameters
    ----------
    exception:
        The unhandled exception. Its type, str(), and traceback are
        captured. ``asyncio.CancelledError`` is handled like any other
        exception; the exit-code distinction is the caller's job.
    context:
        Optional dict of extra fields. Use it to thread in command
        name, args, anything not in the heartbeat. Coerced to a dict
        if not already; non-JSON-serializable values are repr()'d.
    crash_dir:
        Override for the crash report dir (tests). Defaults to
        ``<repo>/data/crash_reports``.
    """
    crash_dir = crash_dir if crash_dir is not None else _crash_report_dir()
    try:
        crash_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.error("crash_report: cannot create dir %s: %s", crash_dir, e)
        return None

    ts = datetime.now(timezone.utc)
    # ISO-8601 with seconds resolution; safe for filenames on Windows.
    ts_str = ts.strftime("%Y-%m-%dT%H-%M-%S")
    out_path = crash_dir / f"crash_report_{ts_str}_{os.getpid()}.json"

    # Build the payload. Wrap every field-extract in its own try so a
    # bad exception type doesn't prevent writing the rest.
    payload: dict[str, Any] = {
        "schema_version": 1,
        "captured_at": ts.isoformat(),
        "pid": os.getpid(),
        "exit_code_planned": (
            EXIT_CODE_CANCELLED
            if _is_cancelled_error(exception)
            else EXIT_CODE_UNHANDLED
        ),
    }

    # Exception summary
    try:
        payload["exception_type"] = type(exception).__name__
    except Exception:
        payload["exception_type"] = "<unreadable type>"
    try:
        payload["exception_message"] = _safe_repr(exception)
    except Exception as e:
        payload["exception_message"] = f"<unreadable str(exc): {e!r}>"

    # Full traceback as a list of strings (one per stack frame line)
    try:
        tb_lines = traceback.format_exception(
            type(exception), exception, exception.__traceback__,
        )
        payload["traceback"] = "".join(tb_lines)
        payload["traceback_lines"] = len(tb_lines)
    except Exception as e:
        payload["traceback"] = f"<format_exception failed: {e!r}>"
        payload["traceback_lines"] = 0

    # Last heartbeat snapshot (best-effort)
    try:
        payload["last_heartbeat"] = _read_last_heartbeat()
    except Exception as e:
        # _read_last_heartbeat already swallows its own failures, so
        # this branch is paranoia. Don't let crash-report writing fail
        # because of a heartbeat read.
        payload["last_heartbeat"] = None
        payload["last_heartbeat_error"] = repr(e)

    # Command line + python version (forensic context)
    payload["argv"] = list(sys.argv)
    payload["python_version"] = sys.version.split()[0]
    payload["cwd"] = os.getcwd()

    # Optional caller context
    if context is not None:
        try:
            payload["context"] = _coerce_jsonable(context)
        except Exception as e:
            payload["context"] = {"_coercion_error": repr(e),
                                  "_raw": _safe_repr(context)}

    # Write atomically: write to .tmp then rename, so a partial write
    # doesn't leave an unparseable crash report behind.
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    try:
        tmp_path.write_text(
            json.dumps(payload, indent=2, default=_safe_repr),
            encoding="utf-8",
        )
        os.replace(tmp_path, out_path)
    except OSError as e:
        logger.error("crash_report: write failed for %s: %s", out_path, e)
        return None
    except Exception as e:
        logger.error("crash_report: serialization failed: %s", e)
        return None

    logger.critical(
        "CRASH REPORT WRITTEN: %s  (exit code %d will follow)",
        out_path, payload["exit_code_planned"],
    )
    return out_path


# ── Helpers ─────────────────────────────────────────────────────────


def _is_cancelled_error(exc: BaseException) -> bool:
    """Distinguish asyncio.CancelledError without importing asyncio at
    module level (avoid pulling the loop machinery into a util module
    that should be import-cheap)."""
    name = type(exc).__name__
    return name == "CancelledError"


def _safe_repr(obj: Any) -> str:
    """str(obj) that never raises. Falls back to repr, then to a
    type-name marker. Used as the JSON ``default=`` so any non-
    serializable value lands as a string instead of crashing the
    json.dumps call."""
    try:
        return str(obj)
    except Exception:
        try:
            return repr(obj)
        except Exception:
            return f"<{type(obj).__name__} unprintable>"


def _coerce_jsonable(value: Any) -> Any:
    """Walk a dict/list and replace non-JSON-serializable leaves with
    their repr. Cheap defensive pass so callers can pass arbitrary
    context dicts without us crashing on a Path or datetime."""
    if isinstance(value, dict):
        return {str(k): _coerce_jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_coerce_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    # Anything else (Path, datetime, bytes, custom objects) -> str
    return _safe_repr(value)
