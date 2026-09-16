"""Unit tests for src/production_arena/scenarios.py.

Verifies the loader's contract against the on-disk backfill:
  - data/backfill/candidates.jsonl  (5,862 rows, Dec 11 2025 - Apr 14 2026)
  - data/bar_recordings/<date>/<ticker>.json (top 500 candidates)
  - data/backfill/features_labeled.jsonl (407 fully-labeled rows)
  - data/backfill/labels_shards/labels_<date>.jsonl (D221 daemon shards)

These tests touch real disk data — they're skipped if the candidates JSONL
isn't checked in (e.g. fresh clone before backfill_harvester.py runs).

The `TestLabelIndex` and `TestSchemaParity` classes at the bottom are
isolated unit tests for the D221 shard-aware label loader and do NOT
require the on-disk backfill (they use tmp_path fixtures or read at most
one line from each real file).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from src.production_arena.scenarios import (
    ScenarioCollection,
    _build_label_index,
    _CANDIDATES_PATH,
    _LABELS_PATH,
    _SHARDS_DIR,
    load_scenarios,
)
from src.production_arena.types import Scenario


_BACKFILL_AVAILABLE = _CANDIDATES_PATH.exists()
pytestmark = pytest.mark.skipif(
    not _BACKFILL_AVAILABLE,
    reason=f"backfill not available at {_CANDIDATES_PATH}",
)


# ── Cached collections (real disk IO is expensive; reuse across tests) ──


@pytest.fixture(scope="module")
def all_scenarios() -> ScenarioCollection:
    """All scenarios with bars (the default loader behaviour)."""
    return load_scenarios()


@pytest.fixture(scope="module")
def jan_window() -> ScenarioCollection:
    """Scenarios from 2026-01-15..2026-01-20 — known-populated window."""
    return load_scenarios(date_range=(date(2026, 1, 15), date(2026, 1, 20)))


# ── 1. test_load_all ───────────────────────────────────────────────────


def test_load_all(all_scenarios: ScenarioCollection) -> None:
    """Default load returns >0 scenarios and they conform to the contract."""
    assert len(all_scenarios) > 0, "expected at least 1 scenario from backfill"
    sample = next(iter(all_scenarios))
    assert isinstance(sample, Scenario)
    assert sample.ticker
    assert sample.session_date
    # Default require_bars=True ⇒ every scenario carries minute bars.
    assert len(sample.minute_bars) > 0
    # Required premarket features per the loader contract.
    for required in ("gap_pct", "premarket_volume", "dollar_volume", "price", "prior_close"):
        assert required in sample.premarket_features, f"missing feature: {required}"


# ── 2. test_load_with_date_range ───────────────────────────────────────


def test_load_with_date_range(jan_window: ScenarioCollection) -> None:
    """Date filter is inclusive on both bounds."""
    assert len(jan_window) > 0, "no scenarios loaded for 2026-01-15..2026-01-20"
    for s in jan_window:
        assert "2026-01-15" <= s.session_date <= "2026-01-20", (
            f"out-of-range date in result: {s.session_date} ({s.ticker})"
        )


# ── 3. test_load_with_ticker_filter ─────────────────────────────────────


def test_load_with_ticker_filter() -> None:
    """Ticker whitelist is enforced (case-insensitive)."""
    # CGTL is in candidates.jsonl on 2026-01-15 with a bar recording on disk.
    coll = load_scenarios(
        date_range=(date(2026, 1, 15), date(2026, 1, 15)),
        tickers=["CGTL"],
    )
    assert len(coll) >= 1, "expected CGTL on 2026-01-15"
    for s in coll:
        assert s.ticker.upper() == "CGTL"
        assert s.session_date == "2026-01-15"


# ── 4. test_filter_by_min_gap ──────────────────────────────────────────


def test_filter_by_min_gap(jan_window: ScenarioCollection) -> None:
    """filter_by(min_gap=...) drops anything with a smaller gap; chains."""
    threshold = 0.20
    filtered = jan_window.filter_by(min_gap=threshold)
    assert len(filtered) <= len(jan_window)
    for s in filtered:
        assert s.premarket_features["gap_pct"] >= threshold

    # Chaining: applying a second filter narrows further.
    twice = filtered.filter_by(min_gap=0.50)
    assert len(twice) <= len(filtered)
    for s in twice:
        assert s.premarket_features["gap_pct"] >= 0.50


# ── 5. test_missing_bars_skip ──────────────────────────────────────────


def test_missing_bars_skip() -> None:
    """require_bars=True drops candidates with no bar recording on disk;
    require_bars=False keeps them (count rises strictly when any are missing)."""
    window = (date(2026, 1, 15), date(2026, 1, 20))
    with_bars = load_scenarios(date_range=window, require_bars=True)
    without_constraint = load_scenarios(date_range=window, require_bars=False)
    assert len(without_constraint) >= len(with_bars), (
        "require_bars=False should never return fewer scenarios than require_bars=True"
    )
    # Backfill only carries bars for the top 500 candidates per day, so any
    # multi-day window is overwhelmingly likely to include un-recorded rows.
    assert len(without_constraint) > len(with_bars), (
        "expected some candidates without bar recordings in this window"
    )


# ── 6. test_collection_stats (sanity) ──────────────────────────────────


def test_collection_stats(jan_window: ScenarioCollection) -> None:
    """`.stats` exposes the documented summary keys."""
    stats = jan_window.stats
    expected_keys = {
        "n_total",
        "n_with_bars",
        "n_labeled",
        "n_unique_tickers",
        "date_range",
        "n_dates",
    }
    assert expected_keys.issubset(stats.keys())
    assert stats["n_total"] == len(jan_window)
    assert stats["n_with_bars"] <= stats["n_total"]
    assert stats["n_labeled"] <= stats["n_total"]
    assert isinstance(stats["date_range"], tuple) and len(stats["date_range"]) == 2
    if stats["n_total"] > 0:
        lo, hi = stats["date_range"]
        assert lo is not None and hi is not None
        assert lo <= hi


# ─────────────────────────────────────────────────────────────────────────
# D221 shard-aware label loader
# Isolated tests: no dependency on the on-disk backfill (use tmp_path).
# ─────────────────────────────────────────────────────────────────────────


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    """Helper: write a list of dicts as JSONL to `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


class TestLabelIndex:
    """Isolated tests for `_build_label_index` — no backfill on disk needed.

    Override the module-level skipif (which gates on _CANDIDATES_PATH) since
    these tests use tmp_path and do not touch real disk.
    """

    pytestmark: list = []  # type: ignore[assignment]

    def test_base_only(self, tmp_path: Path) -> None:
        """No shards dir → all rows from canonical file, counts.shards = 0."""
        base = tmp_path / "features_labeled.jsonl"
        shards = tmp_path / "labels_shards"  # never created
        _write_jsonl(base, [
            {"date": "2026-01-15", "ticker": "AAA", "close_win": True},
            {"date": "2026-01-16", "ticker": "BBB", "close_win": False},
        ])

        index, counts = _build_label_index(base, shards)

        assert counts == {"base": 2, "shards": 0, "conflicts": 0}
        assert index[("2026-01-15", "AAA")]["close_win"] is True
        assert index[("2026-01-16", "BBB")]["close_win"] is False

    def test_shards_are_read_and_unioned(self, tmp_path: Path) -> None:
        """Base + shards with disjoint (date, ticker) → all rows kept."""
        base = tmp_path / "features_labeled.jsonl"
        shards = tmp_path / "labels_shards"
        _write_jsonl(base, [
            {"date": "2026-01-15", "ticker": "AAA", "close_win": True},
        ])
        _write_jsonl(shards / "labels_2025-12-11.jsonl", [
            {"date": "2025-12-11", "ticker": "CCC", "close_win": False},
            {"date": "2025-12-11", "ticker": "DDD", "close_win": True},
        ])
        _write_jsonl(shards / "labels_2025-12-12.jsonl", [
            {"date": "2025-12-12", "ticker": "EEE", "close_win": False},
        ])

        index, counts = _build_label_index(base, shards)

        assert counts == {"base": 1, "shards": 3, "conflicts": 0}
        assert len(index) == 4
        assert index[("2026-01-15", "AAA")]["close_win"] is True
        assert index[("2025-12-11", "CCC")]["close_win"] is False
        assert index[("2025-12-11", "DDD")]["close_win"] is True
        assert index[("2025-12-12", "EEE")]["close_win"] is False

    def test_base_wins_on_conflict(self, tmp_path: Path, caplog) -> None:
        """When (date, ticker) is in both base and a shard, base row is kept."""
        import logging

        base = tmp_path / "features_labeled.jsonl"
        shards = tmp_path / "labels_shards"
        # Same (date, ticker) in both, with DIFFERENT close_win values to
        # make the dedup decision observable.
        _write_jsonl(base, [
            {"date": "2026-01-15", "ticker": "AAA", "close_win": True, "src": "base"},
        ])
        _write_jsonl(shards / "labels_2026-01-15.jsonl", [
            {"date": "2026-01-15", "ticker": "AAA", "close_win": False, "src": "shard"},
            {"date": "2026-01-15", "ticker": "BBB", "close_win": True, "src": "shard"},
        ])

        with caplog.at_level(logging.DEBUG, logger="src.production_arena.scenarios"):
            index, counts = _build_label_index(base, shards)

        # Base wins for AAA; BBB-from-shard is kept (no conflict).
        assert counts == {"base": 1, "shards": 1, "conflicts": 1}
        assert index[("2026-01-15", "AAA")]["src"] == "base"
        assert index[("2026-01-15", "AAA")]["close_win"] is True
        assert index[("2026-01-15", "BBB")]["src"] == "shard"
        # Conflict was logged at DEBUG with shard filename and (date, ticker).
        conflict_logs = [r for r in caplog.records if "label dedup" in r.message]
        assert len(conflict_logs) == 1
        assert "AAA" in conflict_logs[0].message
        assert "labels_2026-01-15.jsonl" in conflict_logs[0].message


# ─────────────────────────────────────────────────────────────────────────
# Schema canary — fires loudly if base and shard schemas drift apart.
# Reads at most one line from each real file (cheap; no full load).
# ─────────────────────────────────────────────────────────────────────────


def test_schema_canary_base_and_shard_have_same_keys() -> None:
    """If anyone adds a new feature to compute_features_and_outcomes() without
    regenerating features_labeled.jsonl, the schemas drift. This canary fails
    LOUDLY so the next change either migrates the base file or version-gates
    the new feature explicitly. Reads one line from each file."""
    if not _LABELS_PATH.exists():
        pytest.skip(f"canonical labels file not present: {_LABELS_PATH}")
    if not _SHARDS_DIR.exists():
        pytest.skip(f"shards dir not present: {_SHARDS_DIR}")

    shard_files = sorted(_SHARDS_DIR.glob("labels_*.jsonl"))
    if not shard_files:
        pytest.skip(f"no shards present in {_SHARDS_DIR}")

    with open(_LABELS_PATH, encoding="utf-8") as f:
        base_row = json.loads(f.readline())
    with open(shard_files[0], encoding="utf-8") as f:
        shard_row = json.loads(f.readline())

    base_keys = set(base_row.keys())
    shard_keys = set(shard_row.keys())

    # Symmetric diff surfaces drift in either direction.
    only_base = base_keys - shard_keys
    only_shard = shard_keys - base_keys
    assert not only_base, (
        f"keys present in base file but missing from shards: {sorted(only_base)}. "
        "Either migrate the shard writer (src/backfill_agent/worker.py) to emit "
        "these keys, or version-gate the feature behind a flag."
    )
    assert not only_shard, (
        f"keys present in shards but missing from base file: {sorted(only_shard)}. "
        "Either regenerate features_labeled.jsonl with the new feature, or "
        "remove the feature from compute_features_and_outcomes() until the "
        "base file is migrated."
    )
