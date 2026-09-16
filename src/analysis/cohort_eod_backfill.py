"""Cohort EOD backfill — populate deferred CohortRow fields after session close.

Per `21_phase0_instrumentation_mvp.md` §4 + the cohort-matcher v0.1
ship (commit `c34f654`): CohortRow rows are emitted at entry time
with `cohort_eod_px`, `cohort_60min_px`, and `cohort_signed_return_60min`
all NULL. This module backfills them at EOD using the existing bar
recorder / price snapshot interface.

The backfill is purely additive — it reads the partition's existing
Parquet, fetches the prices, and atomic-rewrites the partition with
the populated fields. Any failures degrade per-row (one bad lookup
doesn't kill the whole backfill).

Hook site: `main.py` EOD section, after Phase 0 flush, after BOCPD
refit check, before EOD failsafes. New helper:
`run_eod_cohort_backfill(client, base_dir, session_date)`.

The `client` interface is the standard `AlpacaDataClient` shape with:
    `await client.get_latest_quote(symbol) -> dict`
    `await client.get_bars(symbol, start, end, timeframe) -> list[dict]`

If the client is None or unavailable (e.g., tests, paper mode), the
backfill no-ops with a warn log.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── Result dataclass ────────────────────────────────────────────


@dataclass(frozen=True)
class CohortBackfillResult:
    """Per-session backfill summary for the EOD report."""
    rows_total: int
    rows_backfilled: int
    rows_skipped: int           # client unavailable, malformed, etc.
    rows_failed: int            # quote/bar lookup raised
    eod_px_filled: int
    sixty_min_px_filled: int


# ── Pure async backfill ─────────────────────────────────────────


async def fetch_eod_price(client: Any, ticker: str) -> float | None:
    """Best-effort EOD price fetch — returns latest quote midpoint or None."""
    if client is None:
        return None
    try:
        snap = await client.get_latest_quote(ticker)
        if not snap:
            return None
        # Try common shapes: bid/ask, bp/ap, latestQuote
        bid = float(snap.get("bid") or snap.get("bp") or 0)
        ask = float(snap.get("ask") or snap.get("ap") or 0)
        if bid > 0 and ask > 0:
            return (bid + ask) / 2.0
        # Fall back to lastTrade.price if exposed
        lt = snap.get("lastTrade") or {}
        px = float(lt.get("price") or lt.get("p") or 0)
        return px if px > 0 else None
    except Exception as e:
        logger.debug("Cohort EOD price fetch failed for %s: %s", ticker, e)
        return None


async def fetch_60min_price(
    client: Any, ticker: str, entry_ts: datetime,
) -> float | None:
    """Fetch the price ~60 min after entry_ts. Best-effort — None if data
    unavailable. Uses 1-min bars; takes the close of the bar nearest T+60."""
    if client is None or not entry_ts:
        return None
    try:
        target = entry_ts + timedelta(minutes=60)
        # Window ±5 min around target to catch the actual bar
        start = (target - timedelta(minutes=5)).isoformat()
        end = (target + timedelta(minutes=5)).isoformat()
        bars = await client.get_bars(ticker, start=start, end=end, timeframe="1Min")
        if not bars:
            return None
        # Pick the bar whose timestamp is nearest target (within window)
        best: dict | None = None
        best_dt = None
        for b in bars:
            try:
                t_str = b.get("t") or b.get("timestamp")
                if not t_str:
                    continue
                t = datetime.fromisoformat(str(t_str).replace("Z", "+00:00"))
                if best is None or abs((t - target).total_seconds()) < abs(
                    (best_dt - target).total_seconds()
                ):
                    best = b
                    best_dt = t
            except (TypeError, ValueError) as _be:
                logger.debug("Cohort 60min bar parse failed for %s: %s", ticker, _be)
                continue
        if best is None:
            return None
        px = float(best.get("c") or best.get("close") or 0)
        return px if px > 0 else None
    except Exception as e:
        logger.debug("Cohort 60min price fetch failed for %s: %s", ticker, e)
        return None


# ── Backfill driver ─────────────────────────────────────────────


async def run_eod_cohort_backfill(
    client: Any,
    *,
    base_dir: Path | str = "data/instrumentation",
    session_date: str | None = None,
) -> dict[str, Any]:
    """Read the day's CohortRow Parquet, backfill EOD + 60min prices +
    signed return, atomic-rewrite. Returns summary dict for EOD report.

    Args:
      client:        AlpacaDataClient-shaped object exposing get_latest_quote
                     + get_bars. Pass None to no-op (tests).
      base_dir:      Phase 0 instrumentation root (default `data/instrumentation`).
      session_date:  YYYY-MM-DD partition to backfill (default: today UTC).

    Returns the CohortBackfillResult dataclass as a dict, plus an `error`
    field when the partition is missing or unreadable.
    """
    if session_date is None:
        session_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    partition_dir = Path(base_dir) / "cohort_registry" / f"session_date={session_date}"
    parquet_path = partition_dir / "cohorts.parquet"

    if not parquet_path.exists():
        logger.info(
            "Cohort EOD backfill: no partition at %s — nothing to backfill",
            parquet_path,
        )
        return {"error": "no_partition", "result": None}

    if client is None:
        logger.warning(
            "Cohort EOD backfill: client is None — skipping (no quote source)"
        )
        return {"error": "no_client", "result": None}

    try:
        import pandas as pd
        df = pd.read_parquet(parquet_path)
    except Exception as e:
        logger.warning("Cohort EOD backfill: failed to read %s: %s", parquet_path, e)
        return {"error": str(e), "result": None}

    rows_total = len(df)
    rows_backfilled = 0
    rows_skipped = 0
    rows_failed = 0
    eod_px_filled = 0
    sixty_min_px_filled = 0

    # We populate cohort_eod_px + cohort_60min_px + cohort_signed_return_60min
    # only when both prices are available + cohort_entry_ref_px > 0.
    eod_pxs: list[float | None] = []
    sixty_pxs: list[float | None] = []
    signed_returns: list[float | None] = []
    qualities: list[str] = []

    for _, row in df.iterrows():
        ticker = row.get("cohort_ticker")
        ref_px = row.get("cohort_entry_ref_px")
        match_dt = row.get("match_dt")
        if not ticker or not ref_px or float(ref_px) <= 0:
            rows_skipped += 1
            eod_pxs.append(None)
            sixty_pxs.append(None)
            signed_returns.append(None)
            qualities.append(row.get("cohort_data_quality", "partial"))
            continue
        try:
            eod_px = await fetch_eod_price(client, str(ticker))
            sixty_px = None
            if isinstance(match_dt, str):
                try:
                    entry_ts = datetime.fromisoformat(match_dt.replace("Z", "+00:00"))
                    sixty_px = await fetch_60min_price(client, str(ticker), entry_ts)
                except (TypeError, ValueError):
                    sixty_px = None
            elif hasattr(match_dt, "to_pydatetime"):
                sixty_px = await fetch_60min_price(client, str(ticker), match_dt.to_pydatetime())
            elif isinstance(match_dt, datetime):
                sixty_px = await fetch_60min_price(client, str(ticker), match_dt)

            eod_pxs.append(eod_px)
            sixty_pxs.append(sixty_px)
            if eod_px is not None:
                eod_px_filled += 1
            if sixty_px is not None:
                sixty_min_px_filled += 1

            if sixty_px is not None and float(ref_px) > 0:
                signed_ret = (sixty_px - float(ref_px)) / float(ref_px)
                signed_returns.append(signed_ret)
                rows_backfilled += 1
                qualities.append("complete")
            else:
                signed_returns.append(None)
                qualities.append("partial")
        except Exception as e:
            logger.debug("Cohort EOD backfill: row failed for %s: %s", ticker, e)
            rows_failed += 1
            eod_pxs.append(None)
            sixty_pxs.append(None)
            signed_returns.append(None)
            qualities.append(row.get("cohort_data_quality", "partial"))

    # Update DataFrame columns
    df["cohort_eod_px"] = eod_pxs
    df["cohort_60min_px"] = sixty_pxs
    df["cohort_signed_return_60min"] = signed_returns
    df["cohort_data_quality"] = qualities

    # Atomic rewrite (D218 pattern)
    tmp_path = parquet_path.with_suffix(parquet_path.suffix + ".tmp")
    try:
        df.to_parquet(tmp_path, index=False)
        os.replace(tmp_path, parquet_path)
    except Exception as e:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:  # noqa: silent-handler — best-effort cleanup; outer except re-raises
                pass
        logger.error("Cohort EOD backfill: atomic rewrite failed: %s", e)
        return {"error": str(e), "result": None}

    result = CohortBackfillResult(
        rows_total=rows_total,
        rows_backfilled=rows_backfilled,
        rows_skipped=rows_skipped,
        rows_failed=rows_failed,
        eod_px_filled=eod_px_filled,
        sixty_min_px_filled=sixty_min_px_filled,
    )
    logger.info(
        "Cohort EOD backfill: %d/%d rows fully backfilled "
        "(eod_px=%d, 60min_px=%d, skipped=%d, failed=%d) → %s",
        rows_backfilled, rows_total, eod_px_filled, sixty_min_px_filled,
        rows_skipped, rows_failed, parquet_path,
    )
    return {
        "error": None,
        "result": {
            "rows_total": result.rows_total,
            "rows_backfilled": result.rows_backfilled,
            "rows_skipped": result.rows_skipped,
            "rows_failed": result.rows_failed,
            "eod_px_filled": result.eod_px_filled,
            "sixty_min_px_filled": result.sixty_min_px_filled,
        },
    }
