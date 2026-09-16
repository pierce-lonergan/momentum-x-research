"""doc 222 (B1 Phase-1 task#0): capture RAW Alpaca trade_updates payloads verbatim.

Phase 0 (doc 221) found the fill-confirmation channel is the live trade_updates WebSocket,
but we DROP the per-fill dedup key (parse_trade_update never read data.execution_id) and
never PERSIST fills. Before designing the B1 event schema around `data.execution_id`, we must
CONFIRM its real key from a live payload (the cap-bug discipline: verify, don't assume).

This writes every raw trade_updates message verbatim to data/ops/raw_fills_<date>.jsonl —
append-only, never-raises, kill-switchable. Zero behavior change to trading: it's a tee. It
serves two purposes: (1) Phase-1 ground truth (what key carries the per-fill id), and (2) the
seed corpus for the Phase-6 6/1-replay + the eventual durable ledger ingest.
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
_DIR = _ROOT / "data" / "ops"


def is_enabled() -> bool:
    """Kill switch: OPS_RAW_FILL_CAPTURE (default ON — it's a zero-risk tee)."""
    return os.environ.get("OPS_RAW_FILL_CAPTURE", "true").strip().lower() not in (
        "false", "0", "no", "off")


def capture_raw(msg: dict) -> bool:
    """Append one raw trade_updates message verbatim. NEVER raises into the caller."""
    if not is_enabled():
        return False
    try:
        now = datetime.now(timezone.utc)
        date = now.astimezone(_NY).strftime("%Y-%m-%d")
        row = {"captured_ts_utc": now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "raw": msg}
        _DIR.mkdir(parents=True, exist_ok=True)
        with (_DIR / f"raw_fills_{date}.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, default=str) + "\n")
        return True
    except Exception as e:  # noqa: BLE001 — a capture tee must never break the fill stream
        logger.warning("fill_capture.capture_raw failed: %s", e)
        return False
