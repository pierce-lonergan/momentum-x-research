"""doc 184: the momentum bot's OWN intraday continuation model (squeeze vs pump).

WHY (doc 183): the lottery's Continuer_v2 is an end-of-d0 multi-day model — wrong
horizon, 22% feature availability for the intraday bot. The momentum bot needs a
SMALL model that answers, at decision time, "will this gapper keep running the next
30-60 min (capturable continuation) or fade?" — trained on the bot's OWN
decision-time features + the doc-182 graded forward outcomes.

This module is the SHARED contract used by both the trainer
(`scripts/train_intraday_continuer.py`) and the eventual live shadow scorer, so
features are extracted IDENTICALLY at train and inference time (the #1 way ML
shadows go wrong is a train/serve feature skew).

Deliberately small + interpretable: logistic regression on ~9 features. With the
sparse early data (tens of rows) a 54-feature ensemble would overfit instantly;
logistic with L2 degrades gracefully and its coefficients are auditable.

Nothing here trades. The Scorer is write-only telemetry until a future doc wires
it to a live A/B vs the faller gate.
"""
from __future__ import annotations

import logging
import math
import os
import pickle
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_REPO = Path(__file__).resolve().parents[2]
MODEL_PATH = Path(
    os.environ.get("INTRADAY_CONTINUER_MODEL",
                   str(_REPO / "data" / "models" / "intraday_continuer.pkl"))
)

# The feature contract. ORDER MATTERS (the scaler/model are fit on this order).
FEATURE_NAMES: list[str] = [
    "gap_pct",            # decision-time gap (raw)
    "rvol_log",           # log1p(rvol) — RVOL is the #1 confirmed signal (doc 187 RANK 1).
                          #   NOTE: bot rvol ~ scanner RVOL; doc-187 ideal is opening-5min
                          #   RVOL (first-5min vol / 14d avg of that window) — see roadmap.
    "mfcs",               # composite score at decision
    "faller_score",       # the heuristic faller gate's score (the baseline to beat)
    "minutes_since_open",  # time-of-day — doc 187 RANK 4: the decision window is the first ~45 min
    "vwap_distance",      # doc 187: VWAP interaction (signed % above/below VWAP). Above VWAP =
                          #   continuation bias; below = fade. A top research-backed feature.
    "log_price",          # log(decision_price) — sub-$1 vs $10+ behave differently
    "log_float",          # log(float_shares) — low float (NOT short-interest, debunked doc 187)
    "log_mcap",           # log(market_cap) — size cohort
    "is_d170",            # gate flag: 1 if D170 entry-delay reject, 0 if D160 faller
    # doc 188 — the validated continuation edge (doc 187 RANK 1+2; opening_range.py).
    # 0 until the opening-range tracker is populated (post-9:35, bars available).
    "opening_rvol_log",   # log1p(opening RVOL) — RANK 1 confirmed signal
    "opening_range_sign",  # +1 bullish open / -1 bearish / 0 doji — RANK 2 (direction)
    "broke_or_high",      # 1 if price broke the 5-min opening-range high (in-direction break)
]

# A capturable intraday continuation = a favorable excursion the bot could have
# ridden. Default: max-favorable-excursion >= +8% within the post-decision window
# AND not closing deeply red. Tunable at train time.
DEFAULT_RUN_MFE = 0.08


def _safe_log(x: float | None, floor: float = 1e-9) -> float:
    try:
        v = float(x) if x is not None else 0.0
        return math.log(max(v, floor)) if v > 0 else 0.0
    except Exception:
        return 0.0


def extract_features(row: dict[str, Any]) -> dict[str, float]:
    """Map a doc-182 rejection_outcome row (or any candidate dict with the same
    keys) to the model feature dict. Missing values default to 0.0 — but UNLIKE
    the Continuer_v2 mismatch, these 9 features are ALL produced by the momentum
    bot at decision time, so 0-defaults are rare, not the norm."""
    rvol = row.get("rvol")
    gate = str(row.get("gate", "") or "")
    return {
        "gap_pct": float(row.get("gap_pct") or 0.0),
        "rvol_log": math.log1p(float(rvol)) if rvol else 0.0,
        "mfcs": float(row.get("mfcs") or 0.0),
        "faller_score": float(row.get("score") or 0.0),
        "minutes_since_open": float(row.get("minutes_since_open") or 0.0),
        "vwap_distance": float(row.get("vwap_distance") or 0.0),
        "log_price": _safe_log(row.get("decision_price")),
        "log_float": _safe_log(row.get("float_shares")),
        "log_mcap": _safe_log(row.get("market_cap")),
        "is_d170": 1.0 if "D170" in gate else 0.0,
        # doc 188 — opening-range continuation features (0 when not yet computed)
        "opening_rvol_log": math.log1p(float(row["opening_rvol"]))
        if row.get("opening_rvol") else 0.0,
        "opening_range_sign": float(row.get("opening_range_sign") or 0.0),
        "broke_or_high": 1.0 if row.get("broke_or_high") else 0.0,
    }


def label_from_outcome(outcome: dict[str, Any] | None,
                       run_mfe: float = DEFAULT_RUN_MFE) -> int | None:
    """Binary continuation label from a doc-182 graded outcome.

    1 = CONTINUED (a capturable run: MFE >= run_mfe and didn't close deeply red),
    0 = FADED. Returns None when the outcome has no price data (unlabelable).
    """
    if not outcome or not outcome.get("filled"):
        return None
    mfe = outcome.get("mfe_pct")
    reod = outcome.get("return_eod")
    if mfe is None:
        return None
    # Continued if it offered a real favorable excursion and didn't fully reverse.
    if float(mfe) >= run_mfe and (reod is None or float(reod) >= -0.05):
        return 1
    return 0


def features_vector(features: dict[str, float]) -> list[float]:
    return [float(features.get(name, 0.0)) for name in FEATURE_NAMES]


class IntradayContinuer:
    """Loads a trained artifact and scores candidates. Returns P(continue).

    Artifact schema (pickle): {"model", "scaler", "feature_names", "metrics"}.
    """

    def __init__(self, model: Any, scaler: Any, feature_names: list[str],
                 metrics: dict | None = None):
        self.model = model
        self.scaler = scaler
        self.feature_names = feature_names
        self.metrics = metrics or {}

    @classmethod
    def load(cls, path: Path | None = None) -> "IntradayContinuer | None":
        p = path or MODEL_PATH
        if not Path(p).exists():
            logger.info("IntradayContinuer: no artifact at %s (not trained yet)", p)
            return None
        try:
            with open(p, "rb") as f:
                art = pickle.load(f)
            return cls(art["model"], art["scaler"],
                       art.get("feature_names", FEATURE_NAMES), art.get("metrics"))
        except Exception as e:  # noqa: BLE001
            logger.warning("IntradayContinuer: failed to load %s: %s", p, e)
            return None

    def score(self, row_or_features: dict[str, Any]) -> float:
        """P(continue) in [0,1] for a candidate. Accepts either a raw doc-182-style
        row (keys gap_pct/rvol/... ) or an already-extracted feature dict."""
        feats = (row_or_features if "rvol_log" in row_or_features
                 else extract_features(row_or_features))
        import numpy as np
        x = np.array([features_vector(feats)], dtype=float)
        if self.scaler is not None:
            x = self.scaler.transform(x)
        try:
            return float(self.model.predict_proba(x)[0, 1])
        except Exception:
            # Some models expose decision_function only
            d = float(self.model.decision_function(x)[0])
            return float(1.0 / (1.0 + math.exp(-d)))
