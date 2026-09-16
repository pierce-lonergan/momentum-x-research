"""Forward-only persistence helper for live feature modules.

Captures live feature outputs as per-day JSONL shards on disk so that v3
training (~90 days from now) can use modules that today have no historical
backfill path.

### Why a separate module
The four feature modules (short_interest, sentiment_velocity, order_flow,
premarket_velocity) are LIVE-ONLY by design — they were built as in-memory
analyzers consumed at gate time. None of them persist. Adding persistence
inline to each would copy-paste a JSONL append + atomic-write + fail-safe
pattern four times. This helper centralizes that pattern.

### Why default-OFF
Live trading paths must not be slowed or destabilized by persistence side
effects. The `MOMENTUM_PERSIST_LIVE_FEATURES` environment variable defaults
to off; opt in explicitly when you want forward accumulation to start.

### Why per-day shards
Same rationale as `src/backfill_agent/worker.py`'s `_SHARD_ROOT` design:
per-day shards avoid cross-day file-handle contention and let `find data/<m>/
-mtime ...` work intuitively.

### Failure mode
Persistence is a side effect that MUST NOT raise into the caller. All
disk I/O is wrapped in try/except logging at WARNING level. If the disk
is full, network FS is wedged, or the path is read-only, the live feature
computation continues unaffected.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_PERSIST_ROOT = _PROJECT_ROOT / "data"

# Read once at import. Toggle requires process restart by design.
_ENABLED = os.environ.get("MOMENTUM_PERSIST_LIVE_FEATURES", "0").strip() in (
    "1", "true", "True", "TRUE", "yes", "on",
)

if _ENABLED:
    logger.info(
        "feature persistence ENABLED (data/<module>/<YYYY-MM-DD>.jsonl). "
        "Set MOMENTUM_PERSIST_LIVE_FEATURES=0 to disable."
    )


def is_enabled() -> bool:
    """Return whether forward-only persistence is on for this process.

    Useful for callers that want to skip the row-construction work entirely
    when persistence is off.
    """
    return _ENABLED


def persist_feature_row(module_name: str, row: dict[str, Any]) -> None:
    """Append `row` as a JSONL line to `data/<module_name>/<today>.jsonl`.

    No-op when persistence is disabled. Never raises — failures are logged
    at WARNING level and swallowed so the live feature path continues.

    Args:
        module_name: Subdirectory name under data/ (e.g. "short_interest",
                     "sentiment_velocity"). Convention: matches the source
                     module's stem.
        row: Dict to serialize as one JSONL line. Caller is responsible
             for including a `ticker` field and a `timestamp` field (ISO 8601
             UTC) — neither is auto-injected so caller controls semantics
             (snapshot-time vs eval-time vs persist-time).
    """
    if not _ENABLED:
        return

    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        shard = _PERSIST_ROOT / module_name / f"{today}.jsonl"
        shard.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(row) + "\n"
        # POSIX/NTFS: writes <= PIPE_BUF (4096 bytes) to O_APPEND files are
        # atomic. Feature rows are well under 4KB.
        with open(shard, "a", encoding="utf-8") as f:
            f.write(line)
    except (OSError, TypeError, ValueError) as e:
        # noqa: silent-handler — persistence is best-effort by contract;
        # failure here must never affect live feature computation.
        logger.warning(
            "feature persistence failed for module=%s: %s (live path unaffected)",
            module_name, e,
        )
