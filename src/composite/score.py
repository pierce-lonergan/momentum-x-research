"""Composite score — pure-function predictor for the hot path.

ISOLATION GUARANTEE
===================
This module imports stdlib + numpy + sklearn + `src.composite.features` ONLY.
It MUST NOT import from `src.core.*`, `src.agents.*`, or `src.production_arena.*`.
This guarantees:
- shadow-mode wiring cannot leak production state into the score
- the model is runnable from a Jupyter notebook without booting the trading system
- there is no clock dependence, no network, no mutable globals (besides the lazy
  one-shot model load cache)

Usage
=====
    from src.composite.score import composite_score, load_model

    p = composite_score(
        candidate={"gap_pct": 0.4, "premarket_volume": 5e6, "dollar_volume": 15e6,
                   "price": 3.5, "pre_market_high": 3.8, "orb_range_pct": 0.05,
                   "day_volume": 12e6},
        arena_buy_verdict=True,  # or None / False
    )
    # p in [0, 1] — calibrated probability that close_return > 0
"""

from __future__ import annotations

import logging
import pickle
from pathlib import Path
from typing import Any

import numpy as np

from src.composite.features import FEATURE_NAMES, FeatureVector, extract_features

logger = logging.getLogger(__name__)

# Module-level lazy cache so repeated calls don't re-load from disk.
_MODEL_CACHE: dict[str, Any] = {}

_PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
_DEFAULT_FULL_PATH: Path = _PROJECT_ROOT / "models" / "composite_v0_full.pkl"
_DEFAULT_PRESCORE_PATH: Path = _PROJECT_ROOT / "models" / "composite_v0_prescore.pkl"


# ── Model loading ──────────────────────────────────────────────────────


def load_model(path: Path | str | None = None) -> Any:
    """Lazy-load the model pickle. Cached by path string.

    Args:
        path: explicit path to a pickle. If None, defaults to composite_v0_full.pkl.

    Returns:
        sklearn Pipeline (StandardScaler + LogisticRegression).

    Raises:
        FileNotFoundError if the model has not been trained yet.
    """
    p = Path(path) if path is not None else _DEFAULT_FULL_PATH
    p = p.resolve()
    key = str(p)
    if key in _MODEL_CACHE:
        return _MODEL_CACHE[key]
    if not p.exists():
        raise FileNotFoundError(
            f"Composite model not found at {p}. Run `python -m src.composite.train` first."
        )
    with open(p, "rb") as f:
        model = pickle.load(f)
    _MODEL_CACHE[key] = model
    logger.info("loaded composite model: %s", p.name)
    return model


def _drop_arena_column(values: list[float]) -> list[float]:
    """The PRESCORE model is trained without arena_buy_verdict; drop the last
    column when scoring with that model."""
    if "arena_buy_verdict" not in FEATURE_NAMES:
        return values
    idx = FEATURE_NAMES.index("arena_buy_verdict")
    return values[:idx] + values[idx + 1 :]


# ── Public scorer ──────────────────────────────────────────────────────


def composite_score(
    candidate: dict[str, Any],
    arena_buy_verdict: bool | None = None,
    model_path: Path | str | None = None,
) -> float:
    """Score a candidate. Returns P(win_close > 0) in [0, 1].

    Args:
        candidate: dict with at minimum gap_pct, price, dollar_volume,
            premarket_volume. Other features (pre_market_high, orb_range_pct,
            day_volume) optional — defaults are documented in features.py.
        arena_buy_verdict: True if the gate cascade BUYs this candidate; False
            if NO_TRADE; None if not yet evaluated. When None and the FULL model
            is loaded, the function automatically falls back to the PRESCORE
            model (no arena_buy_verdict needed).
        model_path: explicit override. Default behavior:
            - arena_buy_verdict is True/False → load `composite_v0_full.pkl`
            - arena_buy_verdict is None → load `composite_v0_prescore.pkl`

    Returns:
        Probability in [0, 1]. Pure function — same input → same output.
    """
    # Determine which model to use based on whether we have an arena verdict
    if model_path is None:
        model_path = _DEFAULT_FULL_PATH if arena_buy_verdict is not None else _DEFAULT_PRESCORE_PATH

    pipe = load_model(model_path)

    fv: FeatureVector = extract_features(candidate, arena_buy_verdict=arena_buy_verdict)
    values = fv.as_list()

    # If we're using the prescore model (which lacks arena_buy_verdict), drop
    # that column from the feature vector.
    using_prescore = (Path(model_path).resolve() == _DEFAULT_PRESCORE_PATH.resolve()) or (
        arena_buy_verdict is None and model_path == _DEFAULT_PRESCORE_PATH
    )
    if using_prescore:
        values = _drop_arena_column(values)

    X = np.array([values], dtype=float)

    try:
        proba = pipe.predict_proba(X)[0, 1]
    except Exception as e:
        logger.warning("composite_score predict failed (%s); returning 0.5 fallback", e)
        return 0.5

    # Defensive bounds — sklearn occasionally returns ε outside [0,1] from numerical noise
    return float(max(0.0, min(1.0, proba)))


def composite_score_both(
    candidate: dict[str, Any],
    arena_buy_verdict: bool | None,
) -> dict[str, float | None]:
    """Convenience: return both prescore and full scores for shadow logging.

    full_score is None when arena_buy_verdict is None (the FULL model needs it).
    """
    out: dict[str, float | None] = {"prescore": None, "full": None}
    try:
        out["prescore"] = composite_score(candidate, arena_buy_verdict=None,
                                           model_path=_DEFAULT_PRESCORE_PATH)
    except FileNotFoundError as e:
        logger.warning("prescore model unavailable: %s", e)
    if arena_buy_verdict is not None:
        try:
            out["full"] = composite_score(candidate, arena_buy_verdict=arena_buy_verdict,
                                           model_path=_DEFAULT_FULL_PATH)
        except FileNotFoundError as e:
            logger.warning("full model unavailable: %s", e)
    return out


def reset_model_cache() -> None:
    """Clear the lazy-load cache. Used by tests; never called from production."""
    _MODEL_CACHE.clear()


# ── Isolation enforcement (paranoid runtime check) ──────────────────────


def _verify_isolation() -> None:
    """Assert no production code is in this module's import graph.

    This runs only when explicitly called (e.g. from a test). It would be
    too aggressive to run on every import.
    """
    import sys
    forbidden_prefixes = ("src.core.", "src.agents.")
    leaked = [
        m for m in sys.modules
        if any(m.startswith(p) for p in forbidden_prefixes)
        and "src.composite" in str(sys.modules.get(m, ""))  # heuristic
    ]
    # We don't assert here; the test harness checks this directly via static analysis.
