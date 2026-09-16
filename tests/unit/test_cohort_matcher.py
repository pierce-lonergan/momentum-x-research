"""Cohort matcher v0.1 tests.

Per Tuesday call agenda discussion item #3 — same-(catalyst, market_cap_bucket,
hour_bucket) matching with ±15min entry window, 5-peer cap.

10 tests covering bucketing + matching + payload construction.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.analysis.cohort_matcher import (
    DEFAULT_PEER_CAP,
    PeerCandidate,
    build_cohort_rows,
    find_cohort_peers,
    hour_bucket,
    market_cap_bucket,
)


# ── Bucketing helpers ─────────────────────────────────────────


class TestBuckets:

    def test_market_cap_buckets_cover_range(self) -> None:
        assert market_cap_bucket(None) == "unknown"
        assert market_cap_bucket(0) == "unknown"
        assert market_cap_bucket(10_000_000) == "nano"
        assert market_cap_bucket(100_000_000) == "micro"
        assert market_cap_bucket(1_000_000_000) == "small"
        assert market_cap_bucket(5_000_000_000) == "mid"
        assert market_cap_bucket(50_000_000_000) == "large"

    def test_hour_bucket_covers_session(self) -> None:
        # 09:30 ET = 13:30 UTC
        ts_open = datetime(2026, 4, 25, 13, 30, tzinfo=timezone.utc)
        assert hour_bucket(ts_open) == "0930-1000"
        # 10:15 ET = 14:15 UTC
        ts_mid = datetime(2026, 4, 25, 14, 15, tzinfo=timezone.utc)
        assert hour_bucket(ts_mid) == "1000-1030"
        # 14:00 ET = 18:00 UTC
        ts_aft = datetime(2026, 4, 25, 18, 0, tzinfo=timezone.utc)
        assert hour_bucket(ts_aft) == "1200-1600"


# ── Matching ──────────────────────────────────────────────────


class TestFindCohortPeers:

    def _peer(
        self, ticker: str, catalyst: str = "earnings_beat",
        cap: float = 100_000_000, hours_offset: float = 0,
        ref_px: float = 5.0,
    ) -> PeerCandidate:
        ts = datetime(2026, 4, 25, 13, 30, tzinfo=timezone.utc) + timedelta(hours=hours_offset)
        return PeerCandidate(
            ticker=ticker, catalyst_type=catalyst,
            market_cap=cap, entry_ts=ts, reference_price=ref_px,
        )

    def test_self_excluded(self) -> None:
        traded_ts = datetime(2026, 4, 25, 13, 30, tzinfo=timezone.utc)
        peers = find_cohort_peers(
            traded_ticker="AAPL", traded_catalyst_type="earnings_beat",
            traded_market_cap=100_000_000, traded_entry_ts=traded_ts,
            candidate_pool=[self._peer("AAPL"), self._peer("MSFT")],
        )
        assert {p.ticker for p in peers} == {"MSFT"}

    def test_catalyst_mismatch_filtered(self) -> None:
        traded_ts = datetime(2026, 4, 25, 13, 30, tzinfo=timezone.utc)
        peers = find_cohort_peers(
            traded_ticker="X", traded_catalyst_type="earnings_beat",
            traded_market_cap=100_000_000, traded_entry_ts=traded_ts,
            candidate_pool=[
                self._peer("A", catalyst="fda_approval"),
                self._peer("B", catalyst="earnings_beat"),
            ],
        )
        assert {p.ticker for p in peers} == {"B"}

    def test_market_cap_bucket_filter(self) -> None:
        traded_ts = datetime(2026, 4, 25, 13, 30, tzinfo=timezone.utc)
        peers = find_cohort_peers(
            traded_ticker="X", traded_catalyst_type="earnings_beat",
            traded_market_cap=100_000_000,  # micro
            traded_entry_ts=traded_ts,
            candidate_pool=[
                self._peer("A", cap=50_000_000_000),  # large — filtered
                self._peer("B", cap=200_000_000),     # micro — kept
            ],
        )
        assert {p.ticker for p in peers} == {"B"}

    def test_entry_window_filter(self) -> None:
        """Beyond the ±15min window, peers are excluded."""
        traded_ts = datetime(2026, 4, 25, 13, 30, tzinfo=timezone.utc)
        peers = find_cohort_peers(
            traded_ticker="X", traded_catalyst_type="earnings_beat",
            traded_market_cap=100_000_000, traded_entry_ts=traded_ts,
            candidate_pool=[
                self._peer("A", hours_offset=0.10),   # 6 minutes — kept
                self._peer("B", hours_offset=0.40),   # 24 minutes — filtered
            ],
            entry_window_minutes=15,
        )
        assert {p.ticker for p in peers} == {"A"}

    def test_zero_reference_price_filtered(self) -> None:
        traded_ts = datetime(2026, 4, 25, 13, 30, tzinfo=timezone.utc)
        peers = find_cohort_peers(
            traded_ticker="X", traded_catalyst_type="earnings_beat",
            traded_market_cap=100_000_000, traded_entry_ts=traded_ts,
            candidate_pool=[self._peer("A", ref_px=0.0), self._peer("B", ref_px=5.0)],
        )
        assert {p.ticker for p in peers} == {"B"}

    def test_peer_cap_enforced(self) -> None:
        traded_ts = datetime(2026, 4, 25, 13, 30, tzinfo=timezone.utc)
        pool = [self._peer(f"T{i}") for i in range(20)]
        peers = find_cohort_peers(
            traded_ticker="X", traded_catalyst_type="earnings_beat",
            traded_market_cap=100_000_000, traded_entry_ts=traded_ts,
            candidate_pool=pool, peer_cap=5,
        )
        assert len(peers) == 5

    def test_empty_pool_returns_empty(self) -> None:
        traded_ts = datetime(2026, 4, 25, 13, 30, tzinfo=timezone.utc)
        peers = find_cohort_peers(
            traded_ticker="X", traded_catalyst_type="earnings_beat",
            traded_market_cap=100_000_000, traded_entry_ts=traded_ts,
            candidate_pool=[],
        )
        assert peers == []


# ── CohortRow dict payloads ────────────────────────────────────


class TestBuildCohortRows:

    def test_one_row_per_peer(self) -> None:
        ts = datetime(2026, 4, 25, 13, 30, tzinfo=timezone.utc)
        peers = [
            PeerCandidate(
                ticker=f"T{i}", catalyst_type="earnings_beat",
                market_cap=100_000_000, entry_ts=ts, reference_price=5.0 + i,
            ) for i in range(3)
        ]
        rows = build_cohort_rows(
            traded_ticker="X", traded_entry_ts=ts, peers=peers,
            catalyst_type="earnings_beat", market_cap=100_000_000,
        )
        assert len(rows) == 3
        assert {r["cohort_ticker"] for r in rows} == {"T0", "T1", "T2"}
        # Match features attached
        for r in rows:
            assert r["match_features"]["catalyst_type"] == "earnings_beat"
            assert r["match_features"]["market_cap_bucket"] == "micro"
            assert r["traded_ticker"] == "X"
            assert r["cohort_data_quality"] == "partial"

    def test_each_row_has_unique_cohort_id(self) -> None:
        ts = datetime(2026, 4, 25, 13, 30, tzinfo=timezone.utc)
        peers = [
            PeerCandidate(
                ticker=f"T{i}", catalyst_type="earnings_beat",
                market_cap=100_000_000, entry_ts=ts, reference_price=5.0,
            ) for i in range(3)
        ]
        rows = build_cohort_rows(
            traded_ticker="X", traded_entry_ts=ts, peers=peers,
            catalyst_type="earnings_beat", market_cap=100_000_000,
        )
        ids = {r["cohort_id"] for r in rows}
        assert len(ids) == 3  # all unique
