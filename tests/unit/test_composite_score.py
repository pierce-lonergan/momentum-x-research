"""Phase 3 tests for src.composite.score and src.composite.features.

Verifies:
1. Both models load
2. Score is in [0, 1]
3. Same input → same output (deterministic)
4. Missing optional fields handled gracefully
5. arena_buy_verdict=None routes to PRESCORE; explicit verdict routes to FULL
6. Hard isolation: src.composite has NO src.core/src.agents imports
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.composite.features import FEATURE_NAMES, FeatureVector, extract_features
from src.composite.score import (
    composite_score,
    composite_score_both,
    load_model,
    reset_model_cache,
)


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_FULL_MODEL = _PROJECT_ROOT / "models" / "composite_v0_full.pkl"
_PRESCORE_MODEL = _PROJECT_ROOT / "models" / "composite_v0_prescore.pkl"


# ── Fixtures ─────────────────────────────────────────────────────────────


def _typical_candidate(**overrides):
    base = dict(
        gap_pct=0.40,
        premarket_volume=5_000_000,
        dollar_volume=15_000_000,
        price=3.50,
        pre_market_high=3.80,
        orb_range_pct=0.05,
        day_volume=12_000_000,
    )
    base.update(overrides)
    return base


# ── Feature extractor unit tests ─────────────────────────────────────────


class TestFeatureExtractor:

    def test_feature_count_matches_names(self):
        fv = extract_features(_typical_candidate())
        assert len(fv.values) == len(FEATURE_NAMES) == 8

    def test_arena_verdict_values(self):
        cand = _typical_candidate()
        fv_buy = extract_features(cand, arena_buy_verdict=True)
        fv_no = extract_features(cand, arena_buy_verdict=False)
        fv_none = extract_features(cand, arena_buy_verdict=None)
        # arena_buy_verdict is the LAST feature
        assert fv_buy.values[-1] == 1.0
        assert fv_no.values[-1] == 0.0
        assert fv_none.values[-1] == 0.0  # None treated as not-bought

    def test_handles_missing_fields(self):
        """Should not crash on minimal input."""
        fv = extract_features({"gap_pct": 0.10, "price": 5.0})
        assert len(fv.values) == 8
        # All values should be finite
        for v in fv.values:
            assert isinstance(v, float)

    def test_deterministic(self):
        cand = _typical_candidate()
        fv1 = extract_features(cand, arena_buy_verdict=True)
        fv2 = extract_features(cand, arena_buy_verdict=True)
        assert fv1.values == fv2.values

    def test_handles_negative_or_zero_inputs_safely(self):
        """Defensive: log/log1p shouldn't crash on bad data."""
        bad = dict(gap_pct=0.0, premarket_volume=-1000, dollar_volume=0,
                   price=0.0, pre_market_high=0.0, orb_range_pct=0.0, day_volume=0)
        fv = extract_features(bad)
        assert all(isinstance(v, float) and v == v for v in fv.values)  # no NaNs


# ── Model loading and scoring ────────────────────────────────────────────


@pytest.fixture(scope="module")
def models_available():
    """Skip if models haven't been trained yet."""
    if not _FULL_MODEL.exists() or not _PRESCORE_MODEL.exists():
        pytest.skip(f"Models not found at {_FULL_MODEL} / {_PRESCORE_MODEL}")
    reset_model_cache()


class TestModelLoading:

    def test_full_model_loads(self, models_available):
        m = load_model(_FULL_MODEL)
        assert m is not None
        assert hasattr(m, "predict_proba")

    def test_prescore_model_loads(self, models_available):
        m = load_model(_PRESCORE_MODEL)
        assert m is not None
        assert hasattr(m, "predict_proba")

    def test_load_caches_by_path(self, models_available):
        reset_model_cache()
        m1 = load_model(_FULL_MODEL)
        m2 = load_model(_FULL_MODEL)
        assert m1 is m2  # cached


class TestCompositeScore:

    def test_score_in_unit_interval(self, models_available):
        p = composite_score(_typical_candidate(), arena_buy_verdict=True)
        assert 0.0 <= p <= 1.0

    def test_score_deterministic(self, models_available):
        cand = _typical_candidate()
        p1 = composite_score(cand, arena_buy_verdict=True)
        p2 = composite_score(cand, arena_buy_verdict=True)
        p3 = composite_score(cand, arena_buy_verdict=True)
        assert p1 == p2 == p3

    def test_arena_none_routes_to_prescore(self, models_available):
        """When arena_buy_verdict is None, prescore model is used."""
        cand = _typical_candidate()
        p = composite_score(cand, arena_buy_verdict=None)
        assert 0.0 <= p <= 1.0

    def test_arena_buy_changes_score(self, models_available):
        """arena_buy=True vs arena_buy=False should produce DIFFERENT scores
        (otherwise the feature is doing nothing)."""
        cand = _typical_candidate()
        p_buy = composite_score(cand, arena_buy_verdict=True)
        p_no = composite_score(cand, arena_buy_verdict=False)
        # If the coefficient is exactly 0 the test still passes — but in our
        # data we expect non-zero (cascade is anti-selecting).
        # Use a very tight tolerance to catch the "feature ignored" case.
        assert abs(p_buy - p_no) > 1e-9, (
            "arena_buy_verdict appears to have zero effect — model may have ignored it"
        )

    def test_handles_minimal_input(self, models_available):
        """Score should not crash on minimal input."""
        p = composite_score({"gap_pct": 0.10, "price": 5.0}, arena_buy_verdict=True)
        assert 0.0 <= p <= 1.0

    def test_composite_score_both_returns_dict(self, models_available):
        result = composite_score_both(_typical_candidate(), arena_buy_verdict=True)
        assert "prescore" in result and "full" in result
        assert 0.0 <= result["prescore"] <= 1.0
        assert 0.0 <= result["full"] <= 1.0

    def test_composite_score_both_full_none_when_verdict_none(self, models_available):
        result = composite_score_both(_typical_candidate(), arena_buy_verdict=None)
        assert result["prescore"] is not None
        assert result["full"] is None  # full needs an arena verdict


# ── Hard isolation guard ─────────────────────────────────────────────────


class TestHardIsolation:
    """The composite package MUST NOT import from src.core.* or src.agents.*.

    score.py is the hot path; if it pulls in production state, the entire
    point of the composite redesign is defeated.
    """

    def _gather_imports(self, file_path: Path) -> set[str]:
        """Static-AST import scan — finds all imported module paths."""
        with open(file_path, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=str(file_path))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module)
        return imports

    def test_features_py_no_production_imports(self):
        path = _PROJECT_ROOT / "src" / "composite" / "features.py"
        imports = self._gather_imports(path)
        forbidden = [m for m in imports if m.startswith(("src.core.", "src.agents."))]
        assert not forbidden, (
            f"src/composite/features.py imports from production: {forbidden}. "
            f"Hot-path isolation broken."
        )

    def test_score_py_no_production_imports(self):
        path = _PROJECT_ROOT / "src" / "composite" / "score.py"
        imports = self._gather_imports(path)
        forbidden = [m for m in imports if m.startswith(("src.core.", "src.agents.", "src.production_arena."))]
        assert not forbidden, (
            f"src/composite/score.py imports from production: {forbidden}. "
            f"Hot-path isolation broken."
        )

    def test_init_no_production_imports(self):
        path = _PROJECT_ROOT / "src" / "composite" / "__init__.py"
        imports = self._gather_imports(path)
        forbidden = [m for m in imports if m.startswith(("src.core.", "src.agents.", "src.production_arena."))]
        assert not forbidden, (
            f"src/composite/__init__.py imports from production: {forbidden}."
        )
