"""Single ticker-day worker.

Steps per work item:
  1. Acquire bar-fetch budget (Alpaca)
  2. Download 1-min bars from 04:00-20:00 UTC (covers 4:00-16:00 ET both DST modes)
  3. Atomic write to data/bar_recordings/<date>/<ticker>.json
  4. Run labeling via scripts/backfill_features.compute_features_and_outcomes()
  5. Append label to per-day shard data/backfill/labels_shards/labels_<date>.jsonl

Failure handling:
  - HTTP 429: release back to queue, sleep based on retry-after
  - HTTP 4xx (except 429): fail_permanent (ticker doesn't exist that day, etc.)
  - HTTP 5xx / network error: fail_transient (queue retries up to 3x)
  - Insufficient bars (<30): fail_permanent (data quality issue)
  - Labeling exception: fail_permanent with traceback

asyncio.CancelledError handling:
  - In-flight HTTP request: cancellation propagates naturally
  - Tempfile cleanup: try/finally with explicit unlink
  - DB state: worker doesn't write to queue itself; coordinator does that based
    on returned WorkResult, so a cancelled worker leaves the item in_progress
    until the stale-release sweep picks it up (10 min later)
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import sys
import tempfile
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.backfill_agent.budget import RateBudget
from src.backfill_agent.state import WorkItem

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_BAR_ROOT = _PROJECT_ROOT / "data" / "bar_recordings"
_SHARD_ROOT = _PROJECT_ROOT / "data" / "backfill" / "labels_shards"

# Cache the compute_features_and_outcomes() function loaded from scripts/.
_LABEL_FN = None


def _load_label_function():
    """Lazy-import the labeling primitive from scripts/backfill_features.py.

    We don't `import scripts.backfill_features` because scripts/ isn't on
    sys.path by default. Use importlib to load it as a side-effect-free
    module just to pull out the function.
    """
    global _LABEL_FN
    if _LABEL_FN is not None:
        return _LABEL_FN
    path = _PROJECT_ROOT / "scripts" / "backfill_features.py"
    spec = importlib.util.spec_from_file_location("backfill_features_dyn", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _LABEL_FN = mod.compute_features_and_outcomes
    return _LABEL_FN


# ── Result dataclass ────────────────────────────────────────────────────


@dataclass(frozen=True)
class WorkResult:
    item_id: int
    success: bool
    bars_count: int = 0
    labeled: bool = False
    error: str | None = None
    fail_permanent: bool = False             # if True, queue marks failed_permanent


# ── HTTP fetch ───────────────────────────────────────────────────────────


async def _fetch_bars(
    ticker: str,
    date: str,
    api_key: str,
    api_secret: str,
    timeout_s: float = 15.0,
    *,
    http_client_factory=None,
) -> tuple[list[dict] | None, int, str | None]:
    """Fetch 1-min bars from Alpaca. Returns (bars, http_status, error_msg).

    bars=None on any error. http_status=0 if no response received (network).
    """
    import httpx
    factory = http_client_factory or (lambda: httpx.AsyncClient(timeout=timeout_s))  # noqa: async-leak
    url = f"https://data.alpaca.markets/v2/stocks/{ticker}/bars"
    params = {
        "timeframe": "1Min",
        "start": f"{date}T08:00:00Z",        # 04:00 ET (covers both DST modes)
        "end": f"{date}T22:00:00Z",          # 18:00 ET (covers both DST modes)
        "limit": 10000,
    }
    headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret}
    try:
        async with factory() as client:
            resp = await client.get(url, params=params, headers=headers)
            if resp.status_code != 200:
                return None, resp.status_code, f"HTTP {resp.status_code}: {resp.text[:200]}"
            data = resp.json()
            raw_bars = data.get("bars") or []
            bars = [
                {
                    "timestamp": b["t"], "open": b["o"], "high": b["h"],
                    "low": b["l"], "close": b["c"], "volume": b["v"],
                    "vwap": b.get("vw", 0),
                }
                for b in raw_bars
            ]
            return bars, 200, None
    except asyncio.CancelledError:
        raise
    except httpx.HTTPError as e:
        return None, 0, f"network: {type(e).__name__}: {e}"
    except Exception as e:
        return None, 0, f"unexpected: {type(e).__name__}: {e}"


# ── Atomic file writes ──────────────────────────────────────────────────


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Write JSON atomically: tempfile in same dir + rename. Crash-safe."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_str = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(payload, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)           # atomic on POSIX and NTFS
    except BaseException:
        # Cleanup on any failure (including CancelledError)
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            logger.warning("failed to cleanup tempfile %s", tmp_path)
        raise


def _append_jsonl_safe(path: Path, row: dict) -> None:
    """Append one JSONL row. The shard file is per-day so cross-shard contention
    is by-day (different workers writing different shards in parallel = no contention).
    Within a day, we use simple file-locking via O_APPEND atomicity for small writes.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row) + "\n"
    # POSIX/NTFS: writes <= PIPE_BUF (4096 bytes) to O_APPEND files are atomic.
    # Our JSONL rows are well under 4KB.
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(line)
        f.flush()


# ── Worker ──────────────────────────────────────────────────────────────


class Worker:
    """Single async worker. Stateless across items."""

    def __init__(
        self,
        worker_id: str,
        budget: RateBudget,
        api_key: str | None = None,
        api_secret: str | None = None,
        *,
        dry_run: bool = False,
        http_client_factory=None,
    ) -> None:
        self.worker_id = worker_id
        self.budget = budget
        self.api_key = api_key or os.environ.get("ALPACA_API_KEY", "")
        self.api_secret = api_secret or os.environ.get("ALPACA_SECRET_KEY", "")
        self.dry_run = dry_run
        self._http_factory = http_client_factory

    async def process(self, item: WorkItem) -> WorkResult:
        """Process one work item. NEVER raises (except CancelledError, propagated)."""
        try:
            return await self._process_inner(item)
        except asyncio.CancelledError:
            logger.info("worker %s: cancelled mid-process for %s %s",
                        self.worker_id, item.ticker, item.date)
            raise
        except Exception as e:
            tb = traceback.format_exc()
            logger.error("worker %s: unexpected %s for %s %s: %s",
                         self.worker_id, type(e).__name__, item.ticker, item.date, e)
            return WorkResult(item_id=item.id, success=False,
                              error=f"unexpected: {type(e).__name__}: {e}\n{tb[-300:]}",
                              fail_permanent=True)

    async def _process_inner(self, item: WorkItem) -> WorkResult:
        bar_path = _BAR_ROOT / item.date / f"{item.ticker}.json"

        # Step 1+2: fetch bars (or skip if file exists already and is valid)
        if bar_path.exists():
            try:
                existing = json.loads(bar_path.read_text(encoding="utf-8"))
                if len(existing.get("bars", [])) >= 30:
                    bars = existing["bars"]
                    logger.debug("worker %s: bars already on disk for %s %s",
                                 self.worker_id, item.ticker, item.date)
                else:
                    bars = None  # too few — refetch
            except (json.JSONDecodeError, OSError):
                bars = None  # corrupted — refetch
        else:
            bars = None

        if bars is None:
            if self.dry_run:
                logger.info("DRY-RUN worker %s: would fetch %s %s",
                            self.worker_id, item.ticker, item.date)
                return WorkResult(item_id=item.id, success=True, bars_count=0, labeled=False)

            await self.budget.acquire("alpaca", n=1)
            bars, status, err = await _fetch_bars(
                item.ticker, item.date, self.api_key, self.api_secret,
                http_client_factory=self._http_factory,
            )
            if bars is None:
                if status == 429:
                    return WorkResult(item_id=item.id, success=False,
                                      error=f"rate limited: {err}", fail_permanent=False)
                if status >= 400 and status < 500 and status != 429:
                    return WorkResult(item_id=item.id, success=False,
                                      error=f"HTTP {status}: {err}", fail_permanent=True)
                return WorkResult(item_id=item.id, success=False,
                                  error=err or "unknown fetch error", fail_permanent=False)

            if len(bars) < 30:
                return WorkResult(item_id=item.id, success=False,
                                  error=f"only {len(bars)} bars (need >=30)",
                                  fail_permanent=True)

            # Atomic write
            _atomic_write_json(bar_path, {"ticker": item.ticker, "date": item.date, "bars": bars})

        # Step 3+4: label
        try:
            label_fn = _load_label_function()
        except Exception as e:
            return WorkResult(item_id=item.id, success=True, bars_count=len(bars),
                              labeled=False, error=f"label fn import failed: {e}",
                              fail_permanent=False)

        # Reconstruct candidate dict from candidates.jsonl (lazy lookup)
        candidate = self._candidate_lookup(item.ticker, item.date)
        if candidate is None:
            return WorkResult(item_id=item.id, success=True, bars_count=len(bars),
                              labeled=False,
                              error="candidate metadata not found in candidates.jsonl",
                              fail_permanent=True)

        try:
            row = label_fn(candidate, bars)
        except Exception as e:
            return WorkResult(item_id=item.id, success=False,
                              error=f"labeling raised {type(e).__name__}: {e}",
                              fail_permanent=True)
        if row is None:
            return WorkResult(item_id=item.id, success=True, bars_count=len(bars),
                              labeled=False,
                              error="labeler returned None (no 9:30 bar?)",
                              fail_permanent=True)

        # Append to per-day shard
        shard = _SHARD_ROOT / f"labels_{item.date}.jsonl"
        _append_jsonl_safe(shard, row)

        return WorkResult(item_id=item.id, success=True, bars_count=len(bars), labeled=True)

    # ── Candidate lookup helper ──

    _CANDIDATE_INDEX: dict | None = None

    @classmethod
    def _candidate_lookup(cls, ticker: str, date: str) -> dict | None:
        """One-time load of candidates.jsonl into a (ticker, date) -> dict index."""
        if cls._CANDIDATE_INDEX is None:
            cls._CANDIDATE_INDEX = {}
            path = _PROJECT_ROOT / "data" / "backfill" / "candidates.jsonl"
            if path.exists():
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            c = json.loads(line)
                            cls._CANDIDATE_INDEX[(c["ticker"], c["date"])] = c
                        except (json.JSONDecodeError, KeyError) as e:
                            logger.warning(
                                "candidate index: skipping malformed row in candidates.jsonl: %s",
                                type(e).__name__,
                            )
        return cls._CANDIDATE_INDEX.get((ticker, date))
