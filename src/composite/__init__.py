"""Composite probability score — calibrated P(win_close > 0) from candidate features.

Hard isolation: this package MUST NOT import from `src.core.*` or `src.agents.*`.
The composite scorer is the future replacement for the 13-gate cascade and must
be runnable standalone (Jupyter notebook, sweep harness, shadow-mode wiring) without
booting half the trading system.

See docs/research-log/04_structural_redesign.md for the architectural intent and
docs/research-log/06_composite_v0_training.md (after Phase 3) for training results.
"""

from src.composite.features import FeatureVector, FEATURE_NAMES, extract_features
from src.composite.score import composite_score, load_model

__all__ = [
    "FeatureVector",
    "FEATURE_NAMES",
    "extract_features",
    "composite_score",
    "load_model",
]
