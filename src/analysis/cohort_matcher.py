"""Cohort matcher v0.1 — match a traded ticker to peer donor pool.

Per Tuesday call agenda discussion item #3 (recommendation):
  Same-(catalyst_type, market_cap_bucket, hour_bucket) match within
  ±15min entry window, 5-peer cap.

This is the v0.1 algorithm — explicitly minimal so the matched-cohort
returns calculation can land in the Bayesian estimator + capacity report.
v2 enhancements (gap_pct similarity, sector affinity, fuzzy hour matching)
are deferred per `21_phase0_instrumentation_mvp.md` §4 ("matching algorithm
is a v2 implementation detail").

Hook site: `src/execution/bridge.py:execute_verdict()` after the
BarContextRow emit (line ~877). One CohortRow emitted per matched peer.

EOD backfill (`cohort_eod_px`, `cohort_60min_px`, `cohort_signed_return_60min`)
is the next-iteration enhancement — for now those fields are NULL and
filled by a separate EOD batch job.

Bucketing constants — kept as module-level constants for greppability.
Tunable but the v0.1 starting set is deliberately wide:
  - market_cap_bucket: 5 buckets (nano <$50M, micro <$300M, small <$2B,
                                  mid <$10B, large >=$10B)
  - hour_bucket: 4 buckets (09:30-10:00, 10:00-10:30, 10:30-12:00, 12:00-16:00)
  - entry_window: ±15min around the traded ticker's entry_ts
  - peer_cap: 5
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

logger = logging.getLogger(__name__)


# ── Bucketing helpers ──────────────────────────────────────────────


MARKET_CAP_BUCKETS: tuple[tuple[float, str], ...] = (
    (50_000_000, "nano"),
    (300_000_000, "micro"),
    (2_000_000_000, "small"),
    (10_000_000_000, "mid"),
    (float("inf"), "large"),
)
"""Threshold-bucket pairs (max_cap_exclusive, label) ordered ascending."""

HOUR_BUCKETS: tuple[tuple[int, int, str], ...] = (
    (9, 30, "0930-1000"),
    (10, 0, "1000-1030"),
    (10, 30, "1030-1200"),
    (12, 0, "1200-1600"),
)
"""Bucket-start (hour, minute, label). Bucket extends until the next start."""

DEFAULT_ENTRY_WINDOW_MINUTES: int = 15
DEFAULT_PEER_CAP: int = 5


def market_cap_bucket(market_cap: float | None) -> str:
    """Return the bucket label for a market cap. None / 0 → 'unknown'."""
    if not market_cap or market_cap <= 0:
        return "unknown"
    for threshold, label in MARKET_CAP_BUCKETS:
        if market_cap < threshold:
            return label
    return "large"


def hour_bucket(ts: datetime) -> str:
    """Return the trading-hour bucket label for a UTC timestamp.

    The four buckets cover the regular session (09:30 - 16:00 ET).
    Outside-session timestamps fall into the closest bucket.
    """
    # ET is UTC-4 during DST, UTC-5 otherwise. For v0.1 use a fixed
    # UTC-4 (DST). v2 enhancement: pytz-aware conversion.
    et_hour = (ts.hour - 4) % 24
    et_minute = ts.minute
    et_total = et_hour * 60 + et_minute
    last_label = HOUR_BUCKETS[0][2]
    for hr, mn, label in HOUR_BUCKETS:
        bucket_start = hr * 60 + mn
        if et_total < bucket_start:
            return last_label
        last_label = label
    return last_label


# ── Peer candidate dataclass ────────────────────────────────────


@dataclass(frozen=True)
class PeerCandidate:
    """One eligible cohort donor — extracted from the scanner / candidate
    pool at the time of the traded entry. Fed to find_cohort_peers().
    """
    ticker: str
    catalyst_type: str
    market_cap: float | None
    entry_ts: datetime
    reference_price: float


# ── Match function ─────────────────────────────────────────────


def find_cohort_peers(
    *,
    traded_ticker: str,
    traded_catalyst_type: str,
    traded_market_cap: float | None,
    traded_entry_ts: datetime,
    candidate_pool: Iterable[PeerCandidate],
    entry_window_minutes: int = DEFAULT_ENTRY_WINDOW_MINUTES,
    peer_cap: int = DEFAULT_PEER_CAP,
) -> list[PeerCandidate]:
    """Find up to `peer_cap` cohort peers from `candidate_pool`.

    Match criteria (all must hold):
      1. peer.ticker != traded_ticker
      2. peer.catalyst_type == traded_catalyst_type
      3. market_cap_bucket(peer.market_cap) == market_cap_bucket(traded_market_cap)
      4. hour_bucket(peer.entry_ts) == hour_bucket(traded_entry_ts)
      5. abs(peer.entry_ts - traded_entry_ts) <= entry_window_minutes
      6. peer.reference_price > 0

    Returns the first `peer_cap` matches encountered (insertion order).
    Empty pool / no matches → empty list.
    """
    target_cap_bucket = market_cap_bucket(traded_market_cap)
    target_hour_bucket = hour_bucket(traded_entry_ts)
    window = timedelta(minutes=entry_window_minutes)

    matched: list[PeerCandidate] = []
    for peer in candidate_pool:
        if peer.ticker == traded_ticker:
            continue
        if peer.catalyst_type != traded_catalyst_type:
            continue
        if market_cap_bucket(peer.market_cap) != target_cap_bucket:
            continue
        if hour_bucket(peer.entry_ts) != target_hour_bucket:
            continue
        if abs(peer.entry_ts - traded_entry_ts) > window:
            continue
        if peer.reference_price <= 0:
            continue
        matched.append(peer)
        if len(matched) >= peer_cap:
            break
    return matched


# ── Build CohortRow payloads ─────────────────────────────────────


def build_cohort_rows(
    *,
    traded_ticker: str,
    traded_entry_ts: datetime,
    peers: list[PeerCandidate],
    catalyst_type: str,
    market_cap: float | None,
) -> list[dict]:
    """Build the dict payloads for InstrumentationWriter.emit_cohort_registry_dict.

    Returns a list of dicts (one per peer), each containing all required
    CohortRow fields. EOD-backfilled fields (cohort_eod_px, cohort_60min_px,
    cohort_signed_return_60min) are left as None — populated by the EOD
    backfill job.
    """
    cap_b = market_cap_bucket(market_cap)
    hour_b = hour_bucket(traded_entry_ts)
    match_features = {
        "catalyst_type": catalyst_type,
        "market_cap_bucket": cap_b,
        "hour_bucket": hour_b,
    }
    out: list[dict] = []
    for peer in peers:
        cohort_id = str(uuid.uuid4())
        out.append({
            "cohort_id": cohort_id,
            "traded_ticker": traded_ticker,
            "cohort_ticker": peer.ticker,
            "match_dt": datetime.now(timezone.utc),
            "match_features": match_features,
            "cohort_entry_ref_px": float(peer.reference_price),
            "cohort_data_quality": "partial",  # v0.1 — EOD backfill upgrades to "complete"
        })
    return out
