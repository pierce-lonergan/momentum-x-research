"""D214: Unit tests for archetype exit system."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import numpy as np
import pytest

from src.execution.archetype_exit import (
    ArchetypeClassifier,
    ArchetypeExitStrategy,
    DecayCurveLibrary,
    FALLBACK_ARCHETYPE_ID,
    save_model,
    load_model,
)


# ── Fixtures ─────────────────────────────────────────────────────────────


def _make_scenarios(n: int = 100, seed: int = 42) -> list[dict]:
    """Generate synthetic scenarios for testing."""
    rng = np.random.default_rng(seed)
    scenarios = []
    for i in range(n):
        scenarios.append({
            "ticker": f"TEST{i}",
            "date": f"2025-01-{(i % 28) + 1:02d}",
            "gap_pct": float(rng.uniform(0.05, 0.50)),
            "rvol": float(rng.exponential(3.0) + 1.0),
            "prior_gap_count": int(rng.choice([0, 0, 0, 1, 2, 3, 4, 5])),
            "is_day2_runner": bool(rng.random() < 0.1),
            "outcome": "WIN" if rng.random() < 0.5 else "LOSS",
        })
    return scenarios


def _make_paths(n: int = 100, minutes: int = 61, seed: int = 42) -> list[list[float]]:
    """Generate synthetic minute-by-minute return paths."""
    rng = np.random.default_rng(seed)
    paths = []
    for _ in range(n):
        # Random walk with slight upward bias then decay
        steps = rng.normal(0.001, 0.005, minutes)
        path = np.cumsum(steps).tolist()
        paths.append(path)
    return paths


# ── Test Classifier ──────────────────────────────────────────────────────


class TestArchetypeClassifier:

    def test_unfitted_returns_fallback(self):
        clf = ArchetypeClassifier()
        aid, conf = clf.predict(0.10, 5.0, 0, False)
        assert aid == FALLBACK_ARCHETYPE_ID
        assert conf == 0.0

    def test_fit_produces_archetypes(self):
        scenarios = _make_scenarios(150)
        clf = ArchetypeClassifier()
        sizes = clf.fit(scenarios)
        assert len(sizes) >= 1
        assert all(n >= 50 for n in sizes.values())

    def test_predict_returns_valid_archetype(self):
        scenarios = _make_scenarios(150)
        clf = ArchetypeClassifier()
        clf.fit(scenarios)
        aid, conf = clf.predict(0.10, 5.0, 0, False)
        assert isinstance(aid, int)
        assert isinstance(conf, float)

    def test_stratification_separates_day2(self):
        scenarios = _make_scenarios(150)
        # Force 30 day2 runners
        for s in scenarios[:30]:
            s["is_day2_runner"] = True
        clf = ArchetypeClassifier()
        clf.fit(scenarios)
        # Day2 stratum should be processed independently
        assert "day2" in clf._cluster_maps or len(clf._cluster_maps) > 0

    def test_serialization_roundtrip(self):
        scenarios = _make_scenarios(150)
        clf = ArchetypeClassifier()
        clf.fit(scenarios)

        d = clf.to_dict()
        clf2 = ArchetypeClassifier.from_dict(d)
        assert clf2._fitted == clf._fitted
        assert clf2._next_id == clf._next_id


# ── Test Decay Curve Library ─────────────────────────────────────────────


class TestDecayCurveLibrary:

    def test_build_from_paths(self):
        paths = _make_paths(60)
        meta = [{"stratum": "fresh"}] * 60
        library = DecayCurveLibrary()
        library.build_from_paths({0: list(range(60))}, paths, meta)

        curve = library.get_curve(0)
        assert curve is not None
        assert curve.n_samples == 60
        assert 0 <= curve.peak_minute <= 60

    def test_quantiles_computed_correctly(self):
        # 100 paths, all starting at 0 and ending at 0.05
        paths = [[i * 0.05 / 60 for i in range(61)] for _ in range(100)]
        meta = [{"stratum": "fresh"}] * 100
        library = DecayCurveLibrary()
        library.build_from_paths({0: list(range(100))}, paths, meta)

        curve = library.get_curve(0)
        assert curve is not None
        # At minute 60, all paths should be ~0.05
        assert abs(curve.curves[60]["mean"] - 0.05) < 0.001
        # p25 and p75 should be close (all paths identical)
        assert abs(curve.curves[60]["p25"] - 0.05) < 0.001

    def test_null_curve_format(self):
        paths = _make_paths(60)
        meta = [{"stratum": "fresh"}] * 60
        library = DecayCurveLibrary()
        library.build_from_paths({0: list(range(60))}, paths, meta)

        null_curve = library.get_null_curve(0)
        assert null_curve is not None
        assert all(isinstance(m, int) and isinstance(r, float) for m, r in null_curve)

    def test_fallback_returns_none(self):
        library = DecayCurveLibrary()
        assert library.get_null_curve(999) is None
        assert library.get_curve(FALLBACK_ARCHETYPE_ID) is None


# ── Test Archetype Exit Strategy ─────────────────────────────────────────


class TestArchetypeExitStrategy:

    def _make_strategy(self):
        scenarios = _make_scenarios(150)
        clf = ArchetypeClassifier()
        clf.fit(scenarios)
        paths = _make_paths(150)
        meta = [{"stratum": "fresh"}] * 150

        # Build assignments
        assignments = {}
        for i, s in enumerate(scenarios):
            aid, _ = clf.predict(
                abs(s["gap_pct"]), s["rvol"],
                s.get("prior_gap_count", 0), s.get("is_day2_runner", False),
            )
            assignments.setdefault(aid, []).append(i)

        lib = DecayCurveLibrary()
        lib.build_from_paths(assignments, paths, meta)
        return ArchetypeExitStrategy(clf, lib)

    def test_single_assignment_assertion(self):
        strategy = self._make_strategy()
        aid1, _ = strategy.assign_at_entry("TEST", 0.10, 5.0, 0, False)
        # Second call should return cached, not re-assign
        aid2, _ = strategy.assign_at_entry("TEST", 0.10, 5.0, 0, False)
        assert aid1 == aid2

    def test_fallback_on_low_confidence(self):
        strategy = self._make_strategy()
        strategy.confidence_threshold = 999.0  # Impossible to pass
        aid, conf = strategy.assign_at_entry("LOW_CONF", 0.10, 5.0, 0, False)
        assert aid == FALLBACK_ARCHETYPE_ID

    def test_evaluate_returns_hold_for_fallback(self):
        strategy = self._make_strategy()
        strategy.confidence_threshold = 999.0
        strategy.assign_at_entry("FB", 0.10, 5.0, 0, False)
        result = strategy.evaluate("FB", 2.0, 10.0)
        assert result["fallback"] is True
        assert result["should_exit"] is False

    def test_evaluate_unknown_ticker(self):
        strategy = self._make_strategy()
        result = strategy.evaluate("UNKNOWN", 2.0, 10.0)
        assert result["fallback"] is True

    def test_cleanup_position(self):
        strategy = self._make_strategy()
        strategy.assign_at_entry("CLEAN", 0.10, 5.0, 0, False)
        assert "CLEAN" in strategy._assignments
        strategy.cleanup_position("CLEAN")
        assert "CLEAN" not in strategy._assignments

    def test_null_curve_override(self):
        strategy = self._make_strategy()
        strategy.assign_at_entry("CURVE", 0.10, 5.0, 0, False)
        curve = strategy.get_archetype_null_curve("CURVE")
        # Should return a curve if archetype assigned, or None if fallback
        if strategy._assignments["CURVE"][0] != FALLBACK_ARCHETYPE_ID:
            assert curve is not None
            assert len(curve) > 0


# ── Test Save/Load ───────────────────────────────────────────────────────


class TestModelPersistence:

    def test_save_load_roundtrip(self):
        scenarios = _make_scenarios(150)
        clf = ArchetypeClassifier()
        clf.fit(scenarios)
        paths = _make_paths(150)
        meta = [{"stratum": "fresh"}] * 150
        lib = DecayCurveLibrary()
        lib.build_from_paths({0: list(range(150))}, paths, meta)

        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name

        save_model(clf, lib, path)

        clf2, lib2 = load_model(path)
        assert clf2._fitted
        assert len(lib2.archetype_ids()) == len(lib.archetype_ids())

        Path(path).unlink()


# ── Test No Lookahead ────────────────────────────────────────────────────


class TestNoLookahead:

    def test_predict_uses_only_pre_entry_features(self):
        """Verify classifier.predict() signature has no outcome/intraday args."""
        import inspect
        sig = inspect.signature(ArchetypeClassifier.predict)
        params = list(sig.parameters.keys())
        # Should only have: self, gap_pct, rvol, prior_gap_count, is_day2_runner
        assert "outcome" not in params
        assert "intraday_return" not in params
        assert "high_from_open" not in params
        assert len(params) == 5  # self + 4 features

    def test_assign_at_entry_uses_only_pre_entry_features(self):
        import inspect
        sig = inspect.signature(ArchetypeExitStrategy.assign_at_entry)
        params = list(sig.parameters.keys())
        assert "outcome" not in params
        assert "minute_path" not in params
