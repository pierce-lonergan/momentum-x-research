"""
D221 Phase F: bug-sweep + happy-path tests for src/monitoring/distribution_cache.

Per the Sunday-night Tier 3 directive: every percentile/winrate query must
handle empty distributions, single-element distributions, NaN values, and
out-of-range queries WITHOUT raising. Adversarial tests come first; the
happy-path is verified after.

The bug-sweep template applied to every helper:
  1. Required arg = None (explicit)
  2. Required arg = wrong type (string / dict / list)
  3. Numeric arg = NaN
  4. Single-element distributions
  5. Empty distributions

If ANY adversarial test surfaces a bug that requires more than a
trivial fix, per the directive, stop and ship Tier 2 instead.
"""

from __future__ import annotations

import math

import pytest

from src.monitoring.distribution_cache import (
    DistributionCache,
    StatLookup,
    _bin_winrate,
    _percentile_rank,
    _safe_float,
)


# ── Pure helpers ─────────────────────────────────────────────────────


class TestSafeFloat:
    def test_none(self):
        assert _safe_float(None) is None

    def test_string_number(self):
        assert _safe_float("3.14") == pytest.approx(3.14)

    def test_string_garbage(self):
        assert _safe_float("not a number") is None

    def test_nan(self):
        assert _safe_float(float("nan")) is None

    def test_int(self):
        assert _safe_float(42) == 42.0

    def test_dict(self):
        assert _safe_float({"x": 1}) is None

    def test_inf_passes_through(self):
        # inf is a valid float; query/binning code handles it gracefully
        assert _safe_float(float("inf")) == float("inf")


class TestPercentileRank:
    def test_empty_returns_none(self):
        assert _percentile_rank([], 0.5) is None

    def test_single_element_returns_half(self):
        assert _percentile_rank([0.5], 0.5) == 0.5
        # Single-element fallback ignores query value (per docstring)
        assert _percentile_rank([0.5], 100.0) == 0.5

    def test_query_below_minimum(self):
        # rank = 1, n=3 -> 1/4 = 0.25
        assert _percentile_rank([0.4, 0.5, 0.6], 0.0) == pytest.approx(0.25)

    def test_query_above_maximum(self):
        # rank = 4 (1-indexed beyond all), n=3 -> 4/4 = 1.0
        assert _percentile_rank([0.4, 0.5, 0.6], 1.0) == pytest.approx(1.0)

    def test_query_in_middle(self):
        # 0.5 in [0.4, 0.5, 0.6] -> rank=2 -> 2/4 = 0.5
        assert _percentile_rank([0.4, 0.5, 0.6], 0.5) == pytest.approx(0.5)

    def test_nan_query_returns_none(self):
        assert _percentile_rank([0.4, 0.5, 0.6], float("nan")) is None


class TestBinWinrate:
    def test_empty_samples(self):
        r = _bin_winrate([], 0.5, 0.05)
        assert r.value is None and r.note == "empty_dist"

    def test_no_overlap_in_bin(self):
        # query at 0.5 +- 0.05; samples all far away -> no_overlap
        r = _bin_winrate([(0.1, True), (0.9, False)], 0.5, 0.05)
        assert r.value is None and r.note == "no_overlap"

    def test_winrate_in_bin(self):
        samples = [(0.5, True), (0.51, True), (0.52, False), (0.99, False)]
        r = _bin_winrate(samples, 0.50, 0.05)
        # First three in bin: 2 wins / 3 = 0.667
        assert r.value == pytest.approx(2/3)
        assert r.n == 3

    def test_nan_query_safe(self):
        r = _bin_winrate([(0.5, True)], float("nan"), 0.05)
        assert r.value is None and r.note == "out_of_range"


# ── Cache: empty-disk path ───────────────────────────────────────────


class TestDistributionCacheEmpty:
    """Verify cache handles missing/empty disk state without raising."""

    def test_construct_does_not_load(self, monkeypatch, tmp_path):
        # Redirect data dirs to empty tmp_path
        from src.monitoring import distribution_cache as mod
        monkeypatch.setattr(mod, "_JOURNALS_DIR", tmp_path / "journals")
        monkeypatch.setattr(mod, "_LABELS_BASE", tmp_path / "labels.jsonl")
        monkeypatch.setattr(mod, "_LABELS_SHARDS", tmp_path / "shards")
        cache = DistributionCache()
        # Constructor must not load.
        assert cache._loaded is False

    def test_query_on_empty_returns_sentinel(self, monkeypatch, tmp_path):
        from src.monitoring import distribution_cache as mod
        monkeypatch.setattr(mod, "_JOURNALS_DIR", tmp_path / "journals")
        monkeypatch.setattr(mod, "_LABELS_BASE", tmp_path / "labels.jsonl")
        monkeypatch.setattr(mod, "_LABELS_SHARDS", tmp_path / "shards")
        cache = DistributionCache()
        r = cache.mfcs_percentile(0.5)
        assert isinstance(r, StatLookup)
        assert r.value is None
        assert r.note == "empty_dist"
        assert r.n == 0

    def test_all_query_methods_safe_on_empty(self, monkeypatch, tmp_path):
        from src.monitoring import distribution_cache as mod
        monkeypatch.setattr(mod, "_JOURNALS_DIR", tmp_path / "journals")
        monkeypatch.setattr(mod, "_LABELS_BASE", tmp_path / "labels.jsonl")
        monkeypatch.setattr(mod, "_LABELS_SHARDS", tmp_path / "shards")
        cache = DistributionCache()
        # Every method must return a StatLookup (not raise)
        assert isinstance(cache.mfcs_percentile(0.5), StatLookup)
        assert isinstance(cache.mfcs_historical_winrate(0.5), StatLookup)
        assert isinstance(cache.catalyst_winrate("FDA_APPROVAL"), StatLookup)
        assert isinstance(cache.agent_historical_accuracy("news_agent"), StatLookup)
        assert isinstance(cache.consensus_strength_percentile(0.3), StatLookup)
        assert isinstance(cache.agent_confidence_percentile("news_agent", 0.5), StatLookup)


# ── Cache: malformed-input adversarial sweep ─────────────────────────


class TestDistributionCacheAdversarialQueries:
    """All public methods accept (and tolerate) None / NaN / wrong-type
    arguments without raising."""

    @pytest.fixture
    def empty_cache(self, monkeypatch, tmp_path) -> DistributionCache:
        from src.monitoring import distribution_cache as mod
        monkeypatch.setattr(mod, "_JOURNALS_DIR", tmp_path / "journals")
        monkeypatch.setattr(mod, "_LABELS_BASE", tmp_path / "labels.jsonl")
        monkeypatch.setattr(mod, "_LABELS_SHARDS", tmp_path / "shards")
        return DistributionCache()

    def test_mfcs_percentile_none(self, empty_cache):
        r = empty_cache.mfcs_percentile(None)
        assert r.value is None and r.note == "out_of_range"

    def test_mfcs_percentile_nan(self, empty_cache):
        r = empty_cache.mfcs_percentile(float("nan"))
        assert r.value is None and r.note == "out_of_range"

    def test_mfcs_percentile_string(self, empty_cache):
        # String is coerced via _safe_float -> None
        r = empty_cache.mfcs_percentile("0.5")
        # _safe_float("0.5") = 0.5, query continues; empty dist sentinel
        assert r.value is None and r.note == "empty_dist"

    def test_mfcs_percentile_garbage_string(self, empty_cache):
        r = empty_cache.mfcs_percentile("not a number")
        assert r.value is None and r.note == "out_of_range"

    def test_catalyst_winrate_none(self, empty_cache):
        r = empty_cache.catalyst_winrate(None)
        assert r.value is None and r.note == "out_of_range"

    def test_catalyst_winrate_empty_string(self, empty_cache):
        r = empty_cache.catalyst_winrate("")
        assert r.value is None and r.note == "out_of_range"

    def test_catalyst_winrate_dict(self, empty_cache):
        r = empty_cache.catalyst_winrate({"nested": "dict"})  # type: ignore[arg-type]
        assert r.value is None and r.note == "out_of_range"

    def test_agent_accuracy_none(self, empty_cache):
        r = empty_cache.agent_historical_accuracy(None)
        assert r.value is None and r.note == "out_of_range"

    def test_agent_confidence_percentile_none_agent(self, empty_cache):
        r = empty_cache.agent_confidence_percentile(None, 0.5)
        assert r.value is None and r.note == "out_of_range"

    def test_agent_confidence_percentile_none_conf(self, empty_cache):
        r = empty_cache.agent_confidence_percentile("news_agent", None)
        assert r.value is None and r.note == "out_of_range"


# ── Cache: real-data smoke (uses on-disk journals if present) ────────


class TestDistributionCacheLiveData:
    """Loads the real data on disk. Skips if journals are absent."""

    def test_load_completes_without_raising(self):
        cache = DistributionCache()
        s = cache.stats()
        # Must produce a stats dict regardless of disk state
        assert isinstance(s, dict)
        assert "n_mfcs_observations" in s

    def test_query_real_mfcs_value(self):
        cache = DistributionCache()
        s = cache.stats()
        if s["n_mfcs_observations"] == 0:
            pytest.skip("no journal data on disk")
        r = cache.mfcs_percentile(0.5)
        assert isinstance(r, StatLookup)
        # If we have data, we should get a real percentile
        assert r.note == "ok"
        assert r.value is not None
        assert 0.0 <= r.value <= 1.0
        assert r.n == s["n_mfcs_observations"]
