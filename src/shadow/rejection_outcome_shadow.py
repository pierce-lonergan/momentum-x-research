"""doc 182: Rejection-outcome shadow — grade the names the gates REJECTED.

WHY: the faller gate (D160) and the entry-delay gate (D170) are the binding
constraints on the momentum funnel (they block/defer most candidates), yet they
are hand-tuned heuristics with NO outcome-calibration loop. On 2026-05-29 they
correctly avoided fades (CGTL -14%, UMAC -13.8%) — but we only know that by
manual after-the-fact price-checking. This module records, at each rejection, the
decision-time context so a post-close finalizer
(`scripts/finalize_rejection_outcomes.py`) can fetch the name's FORWARD return and
answer the only question that matters: did the rejected name FADE (block correct)
or RUN (block wrong)? After ~20-30 graded rejections per gate we can calibrate the
faller 0.60 threshold and the D170 10%-drawdown limit on DATA, not anecdote — and,
critically, decide whether it is SAFE to loosen the entry machinery (doc 181: do
NOT loosen until this shows the gates are wrong, else we buy the fades).

CONTRACT: write-only, decision-time only (no lookahead — `outcome` is null here and
filled post-close). NEVER raises into production (guarded). Enable/disable via
`SHADOW_REJECTION_GRADING_ENABLED` (default true).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from src.shadow.logger import get_shadow_logger

logger = logging.getLogger(__name__)

_NY_TZ = ZoneInfo("America/New_York")
_SCHEMA_VERSION = "v0"
_KIND = "rejection_outcome"


def is_rejection_grading_enabled() -> bool:
    """Kill switch: SHADOW_REJECTION_GRADING_ENABLED env var. Default True."""
    val = os.environ.get("SHADOW_REJECTION_GRADING_ENABLED", "true").strip().lower()
    return val not in ("false", "0", "no", "off")


def _minutes_since_open(ts_utc: datetime) -> float | None:
    """ET minutes since the 09:30 open for the decision timestamp (DST-robust).

    Time-of-day is a primary squeeze-vs-pump discriminator (early gappers behave
    differently than late ones), so we capture it for the eventual intraday
    continuation model (doc 183)."""
    try:
        et = ts_utc.astimezone(_NY_TZ)
        open_et = et.replace(hour=9, minute=30, second=0, microsecond=0)
        return round((et - open_et).total_seconds() / 60.0, 1)
    except Exception:
        return None


def log_rejection_for_grading(
    *,
    ticker: str,
    gate: str,
    decision_price: float | None,
    decision_ts: datetime | None = None,
    mfcs: float | None = None,
    score: float | None = None,
    reason: str | None = None,
    gap_pct: float | None = None,
    rvol: float | None = None,
    float_shares: float | None = None,
    market_cap: float | None = None,
    vwap_distance: float | None = None,
    opening_rvol: float | None = None,
    opening_range_sign: int | None = None,
    broke_or_high: bool | None = None,
) -> bool:
    """Log one gate-rejection for post-close outcome grading.

    Args:
        ticker: rejected symbol.
        gate: which gate rejected it, e.g. "D160_FALLER" or "D170_ENTRY_DELAY".
        decision_price: the price at the moment of rejection (the would-be entry).
        decision_ts: tz-aware decision time (defaults to now-UTC).
        mfcs/score/reason/gap_pct/rvol/float_shares/market_cap: decision-time
            features for the eventual intraday-continuation model (doc 183).

    Returns True if logged. NEVER raises into the caller.
    """
    if not is_rejection_grading_enabled():
        return False
    try:
        ts = decision_ts or datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        ts_utc = ts.astimezone(timezone.utc)
        session_date = ts_utc.astimezone(_NY_TZ).strftime("%Y-%m-%d")
        row = {
            "kind": _KIND,
            "schema_version": _SCHEMA_VERSION,
            "ticker": str(ticker),
            "session_date": session_date,
            "gate": str(gate),
            "reason": str(reason) if reason is not None else None,
            "decision_ts_utc": ts_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "minutes_since_open": _minutes_since_open(ts_utc),
            "decision_price": float(decision_price) if decision_price else None,
            "mfcs": float(mfcs) if mfcs is not None else None,
            "score": float(score) if score is not None else None,
            "gap_pct": float(gap_pct) if gap_pct is not None else None,
            "rvol": float(rvol) if rvol is not None else None,
            "float_shares": float(float_shares) if float_shares else None,
            "market_cap": float(market_cap) if market_cap else None,
            # doc 187: VWAP interaction — a top confirmed continuation signal.
            "vwap_distance": float(vwap_distance) if vwap_distance is not None else None,
            # doc 188: the validated continuation edge (opening RVOL + range direction).
            "opening_rvol": float(opening_rvol) if opening_rvol is not None else None,
            "opening_range_sign": int(opening_range_sign) if opening_range_sign is not None else None,
            "broke_or_high": bool(broke_or_high) if broke_or_high is not None else None,
            # Filled post-close by scripts/finalize_rejection_outcomes.py.
            # Null here BY DESIGN — keeps the decision-time record lookahead-free.
            "outcome": None,
        }
        get_shadow_logger().log(row)
        return True
    except Exception as e:  # noqa: BLE001 — shadow telemetry must never break trading
        logger.warning("rejection_outcome_shadow: log failed for %s: %s", ticker, e)
        return False
