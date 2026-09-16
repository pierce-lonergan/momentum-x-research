"""Single source of truth for composite score features.

This module is called from BOTH training (`src/composite/train.py`) AND scoring
(`src/composite/score.py`) — train/serve skew is the most common ML bug, and the
fix is one canonical extractor. Do not duplicate this logic anywhere.

Feature philosophy
==================
- ≤15 features for 407 training rows (overfit cliff).
- All features deterministic from candidate-level data — no LLM, no network.
- Numeric only at the model boundary; any categoricals are one-hot encoded here.
- arena_buy_verdict is included per the user's calibration: train conditional on
  gate-cascade survival so the model learns the post-gate distribution rather
  than the universe distribution.
- day_of_week deliberately EXCLUDED — too noisy for 407 rows, the user flagged
  it as the first thing to cut.

ISOLATION GUARANTEE
===================
This module imports stdlib + numpy + the arena's pure types only.
NO imports from `src.core.*` or `src.agents.*`. Verified by Phase 3 commit hook.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable

# Single canonical feature ordering — used at training and serving.
# Adding/removing/reordering REQUIRES retraining the model.
FEATURE_NAMES: tuple[str, ...] = (
    "gap_pct",
    "log1p_premarket_volume",
    "log1p_dollar_volume",
    "log_price",
    "price_to_pre_market_high_ratio",
    "orb_range_pct",
    "day_to_premarket_volume_ratio",
    "arena_buy_verdict",
)


@dataclass(frozen=True)
class FeatureVector:
    """One row of model input. Order matches FEATURE_NAMES."""
    values: tuple[float, ...]
    feature_names: tuple[str, ...] = FEATURE_NAMES

    def as_list(self) -> list[float]:
        return list(self.values)

    def as_dict(self) -> dict[str, float]:
        return dict(zip(self.feature_names, self.values))

    def __post_init__(self) -> None:
        if len(self.values) != len(self.feature_names):
            raise ValueError(
                f"FeatureVector mismatch: {len(self.values)} values vs "
                f"{len(self.feature_names)} feature names"
            )


# ── Defensive numerics ─────────────────────────────────────────────────


def _safe_log(x: float, default: float = 0.0) -> float:
    """log(max(x, 1e-9)) — used for prices that should never be ≤0 but might be NaN."""
    if x is None or not math.isfinite(x) or x <= 0:
        return default
    return math.log(x)


def _safe_log1p(x: float, default: float = 0.0) -> float:
    """log1p(max(x, 0)) — used for volumes / dollar amounts."""
    if x is None or not math.isfinite(x) or x < 0:
        return default
    return math.log1p(x)


def _safe_ratio(num: float | None, denom: float | None, default: float = 1.0) -> float:
    """num / denom with bounded fallback. Default=1.0 means 'no evidence either way'."""
    if num is None or denom is None or not math.isfinite(num) or not math.isfinite(denom):
        return default
    if denom <= 1e-9:
        return default
    r = num / denom
    if not math.isfinite(r):
        return default
    return r


# ── The extractor ──────────────────────────────────────────────────────


def extract_features(
    candidate: dict[str, Any],
    arena_buy_verdict: bool | None = None,
) -> FeatureVector:
    """Build a FeatureVector from a candidate-features dict + optional arena verdict.

    Args:
        candidate: keys at minimum — gap_pct, premarket_volume, dollar_volume,
            price (or open), pre_market_high (optional), orb_range_pct (optional),
            day_volume (optional). Tolerates missing fields with sensible defaults.
        arena_buy_verdict: True if the arena's gate cascade would BUY this
            candidate; False if NO_TRADE; None if not yet evaluated. Models trained
            with this feature will learn conditional on cascade survival.

    Returns:
        FeatureVector with values in FEATURE_NAMES order.

    Determinism
    -----------
    Same input → same output, every time. No clock dependence, no network,
    no global state mutation.
    """
    gap_pct = float(candidate.get("gap_pct") or 0.0)
    premarket_volume = float(candidate.get("premarket_volume") or 0.0)
    dollar_volume = float(candidate.get("dollar_volume") or 0.0)
    price = float(candidate.get("price") or candidate.get("open") or 0.0)
    pre_market_high = float(candidate.get("pre_market_high") or 0.0)
    orb_range_pct = float(candidate.get("orb_range_pct") or 0.0)
    day_volume = float(candidate.get("day_volume") or 0.0)

    # price_to_pre_market_high_ratio: how much of the pre-market high we've
    # retraced from. 1.0 = at the high, 0.5 = halfway down. Defaults to 1.0
    # (no evidence) when pre_market_high unavailable. Capped to [0, 2] to
    # bound outliers.
    pmh_ratio = 1.0
    if pre_market_high > 0 and price > 0:
        pmh_ratio = max(0.0, min(2.0, price / pre_market_high))

    # day_to_premarket_volume_ratio: rough RVOL proxy. day_vol / premarket_vol
    # tells us how much the stock traded post-open vs pre-open. Defaults to
    # 5.0 when premarket_volume == 0 (most labeled rows have 0 premarket
    # because bar recordings start at 9:31). Capped to [0, 50].
    if premarket_volume > 0 and day_volume > 0:
        d2p = max(0.0, min(50.0, day_volume / premarket_volume))
    else:
        d2p = 5.0

    arena_flag = 0.0
    if arena_buy_verdict is True:
        arena_flag = 1.0
    elif arena_buy_verdict is False:
        arena_flag = 0.0
    # None → 0.0 (treat unknown as "not bought") for backward-compat scoring.

    return FeatureVector(
        values=(
            gap_pct,
            _safe_log1p(premarket_volume),
            _safe_log1p(dollar_volume),
            _safe_log(price),
            pmh_ratio,
            orb_range_pct,
            d2p,
            arena_flag,
        )
    )


def extract_batch(
    candidates_with_verdicts: Iterable[tuple[dict[str, Any], bool | None]],
) -> tuple[list[list[float]], list[str]]:
    """Convenience for training: produce X (list of rows) + feature_names.

    Args:
        candidates_with_verdicts: iterable of (candidate_dict, arena_verdict) tuples.

    Returns:
        (X, feature_names) where X is row-major suitable for sklearn.fit().
    """
    X = [extract_features(c, v).as_list() for c, v in candidates_with_verdicts]
    return X, list(FEATURE_NAMES)
