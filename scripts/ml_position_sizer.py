"""ML position sizer: Kelly + conformal-width modulation.

Per doc 102 I7: given P(continuer), conformal interval width, and edge
size, compute a position size as a fraction of capital.

Key features:
  - Fractional Kelly (default 0.25× full Kelly) for safety
  - Hard cap (default 5% of equity per trade)
  - Conformal width modulation (wider CI -> smaller position)
  - Hard refuse when P < threshold (default 0.30)
  - Optional minimum size (don't bother with sub-$50 trades)

Used by: lottery_runner.py (long), fader_short_runner.py (short)
"""
from __future__ import annotations
import json
import pickle
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MODELS = REPO / "data" / "models"


@dataclass
class SizerConfig:
    p_threshold: float = 0.30          # don't trade if P below this
    p_threshold_short: float = 0.10    # for shorts, P < this = high fader confidence
    kelly_fraction: float = 0.25        # fractional Kelly
    hard_cap_pct_equity: float = 0.02  # max 2% of equity per trade
    min_notional_usd: float = 50.0      # skip if computed size below this
    max_notional_usd: float = 500.0     # hard cap
    width_floor: float = 0.20           # below this width, don't downweight
    width_kill: float = 0.80            # above this, refuse trade


@dataclass
class SizerOutput:
    notional_usd: float        # 0 if refused
    reason: str                # explanation
    p_proba: float | None = None
    width: float | None = None
    expected_edge: float | None = None
    kelly_raw: float | None = None


def kelly_fraction(p: float, win_pct: float, loss_pct: float) -> float:
    """Optimal Kelly fraction for binary outcome.

    Win → +win_pct, Loss → -loss_pct (loss_pct positive number)
    f* = (p * b - (1-p)) / b   where b = win_pct/loss_pct (odds)
    """
    if loss_pct <= 0: return 0.0
    b = win_pct / loss_pct
    f = (p * b - (1 - p)) / max(b, 1e-6)
    return max(0.0, f)


def size_long(
    p_continuer: float,
    conformal_width: float,
    equity: float,
    config: SizerConfig | None = None,
    expected_win_pct: float = 0.30,
    expected_loss_pct: float = 0.15,
) -> SizerOutput:
    """Size a long position based on ML continuer probability + conformal width.

    Args:
        p_continuer: model's predicted P(continuer)
        conformal_width: model's per-prediction confidence width (0=tight, 1=loose)
        equity: total account equity
        config: SizerConfig (defaults)
        expected_win_pct: expected return if continuer (default 30%)
        expected_loss_pct: expected stop loss (default 15%, lottery trail)
    """
    cfg = config or SizerConfig()
    if p_continuer < cfg.p_threshold:
        return SizerOutput(0.0, f"P={p_continuer:.3f} < {cfg.p_threshold}",
                           p_proba=p_continuer, width=conformal_width)
    if conformal_width > cfg.width_kill:
        return SizerOutput(0.0, f"width={conformal_width:.2f} > {cfg.width_kill}",
                           p_proba=p_continuer, width=conformal_width)
    kelly = kelly_fraction(p_continuer, expected_win_pct, expected_loss_pct)
    width_mod = max(0.2, 1 - max(0, conformal_width - cfg.width_floor) / (cfg.width_kill - cfg.width_floor))
    size_pct = min(cfg.hard_cap_pct_equity, kelly * cfg.kelly_fraction * width_mod)
    notional = max(0, size_pct * equity)
    if notional < cfg.min_notional_usd:
        return SizerOutput(0.0, f"sized={notional:.0f} < min={cfg.min_notional_usd}",
                           p_proba=p_continuer, width=conformal_width,
                           kelly_raw=kelly)
    notional = min(notional, cfg.max_notional_usd)
    return SizerOutput(
        notional_usd=notional,
        reason=f"P={p_continuer:.3f} kelly={kelly:.3f} width_mod={width_mod:.2f}",
        p_proba=p_continuer, width=conformal_width,
        expected_edge=p_continuer * expected_win_pct - (1 - p_continuer) * expected_loss_pct,
        kelly_raw=kelly,
    )


def size_short(
    p_continuer: float,           # model gives P(continuer); for short we want LOW
    conformal_width: float,
    equity: float,
    config: SizerConfig | None = None,
    expected_win_pct: float = 0.10,
    expected_loss_pct: float = 0.10,
) -> SizerOutput:
    """For shorts: trade when P(continuer) is LOW (= high fader confidence).

    Effective short probability = 1 - P(continuer).
    """
    cfg = config or SizerConfig()
    p_fade = 1 - p_continuer
    if p_continuer > (1 - cfg.p_threshold_short):
        return SizerOutput(0.0, f"P_continue={p_continuer:.3f} too high for short",
                           p_proba=p_continuer, width=conformal_width)
    if conformal_width > cfg.width_kill:
        return SizerOutput(0.0, f"width={conformal_width:.2f} > {cfg.width_kill}",
                           p_proba=p_continuer, width=conformal_width)
    kelly = kelly_fraction(p_fade, expected_win_pct, expected_loss_pct)
    width_mod = max(0.2, 1 - max(0, conformal_width - cfg.width_floor) / (cfg.width_kill - cfg.width_floor))
    size_pct = min(cfg.hard_cap_pct_equity, kelly * cfg.kelly_fraction * width_mod)
    notional = max(0, size_pct * equity)
    if notional < cfg.min_notional_usd:
        return SizerOutput(0.0, f"sized={notional:.0f} < min", p_proba=p_continuer)
    notional = min(notional, cfg.max_notional_usd)
    return SizerOutput(
        notional_usd=notional,
        reason=f"P_continue={p_continuer:.3f} kelly={kelly:.3f} width_mod={width_mod:.2f}",
        p_proba=p_continuer, width=conformal_width,
        expected_edge=p_fade * expected_win_pct - (1 - p_fade) * expected_loss_pct,
        kelly_raw=kelly,
    )


def load_model(path: Path | str | None = None):
    """Load the trained ML model artifacts (defaults to v2 if available, else v1)."""
    if path is None:
        v2 = MODELS / "continuer_v2.pkl"
        v1 = MODELS / "continuer_v1.pkl"
        path = v2 if v2.exists() else v1
    with open(path, "rb") as f:
        return pickle.load(f)


def predict_for_row(features: dict, model_artifacts: dict) -> tuple[float, float]:
    """Run inference: return (P(continuer), conformal_width).

    Supports v1 (single classifier) and v2 (stacked ensemble) artifacts.
    """
    import numpy as np
    cols = model_artifacts["feature_columns"]
    X_row = np.array([[features.get(c, 0) for c in cols]])
    # Detect v1 vs v2
    if "base_learners" in model_artifacts and "meta_learner" in model_artifacts:
        # v2 stacked ensemble
        base = model_artifacts["base_learners"]
        meta = model_artifacts["meta_learner"]
        P_base = np.column_stack([m.predict_proba(X_row)[:, 1] for m in base.values()])
        p = float(meta.predict_proba(P_base)[0, 1])
    else:
        # v1 single classifier
        p = float(model_artifacts["classifier"].predict_proba(X_row)[0, 1])
    # Width from conformal threshold
    thr = model_artifacts["conformal_threshold"]
    width = (1 - abs(p - 0.5) * 2) * (1 + thr)
    width = float(min(max(width, 0), 1))
    return p, width


# ── CLI for testing ──

def _cli():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--p", type=float, required=True, help="P(continuer)")
    p.add_argument("--width", type=float, default=0.5)
    p.add_argument("--equity", type=float, default=140000)
    p.add_argument("--side", choices=["long", "short"], default="long")
    args = p.parse_args()
    fn = size_long if args.side == "long" else size_short
    out = fn(args.p, args.width, args.equity)
    print(f"  notional_usd:    ${out.notional_usd:.2f}")
    print(f"  reason:          {out.reason}")
    print(f"  p_proba:         {out.p_proba}")
    print(f"  width:           {out.width}")
    print(f"  expected_edge:   {out.expected_edge}")
    print(f"  kelly_raw:       {out.kelly_raw}")


if __name__ == "__main__":
    _cli()
