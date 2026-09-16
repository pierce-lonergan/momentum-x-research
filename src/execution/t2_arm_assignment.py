"""D310.T2 (2026-05-24, doc 171) — A/B arm assignment for stop-widening.

PURPOSE:
  At verdict-emission time, deterministically assign each candidate to
  either the wide_stop or tight_stop arm of the T2 experiment. Arms
  are split 50/50 keyed on hash(trading_date, symbol) so:
    - Same symbol picked twice on the same day → same arm (no churn)
    - Across days, the same symbol may flip arms (independent draws)
    - Arm distribution converges to 50/50 across many trades

GATING:
  Only fires when ``MOMENTUM_T2_ENABLED=1`` in env. Default OFF -- every
  verdict gets ``tight_stop`` arm + ``qty_multiplier=1.0`` so the
  pre-T2 production behavior is the default. Operator can flip the
  switch at any time without redeploy (env var read per-call, not
  cached at startup).

OUTPUT (passed via verdict.model_copy(update={...})):
  - execution_arm: "wide_stop" or "tight_stop"
  - qty_multiplier: 0.5 (wide_stop) or 1.0 (tight_stop)

CALL SITE:
  orchestrator.py at the point where the verdict is finalized, just
  before being added to _scored_by_ticker / returned to the bridge.
"""
from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

# Env var. Re-read per call so operator can flip without restart.
_ENV_FLAG = "MOMENTUM_T2_ENABLED"

# Wide arm gets HALVED qty to bound risk: wider stop (5-25%) x same %
# portfolio risk would mean larger losses on stop-out. Halving keeps
# dollar-risk roughly comparable to the tight-stop arm's full-size
# baseline.
_WIDE_QTY_MULT = 0.5

# 50/50 split. Future iterations can tilt this (e.g. 70% wide once
# the data confirms T2 wins by enough margin).
_WIDE_BUCKET_THRESHOLD = 50


def _wide_bucket_threshold() -> int:
    """doc 265: operator-tunable wide-arm fraction via MOMENTUM_T2_WIDE_PCT.

    Accepts a fraction (0.0-1.0) or a percent (0-100). Unset/invalid ->
    the original 50/50 split (exact pre-doc-265 behavior). Read PER-CALL
    (same philosophy as t2_enabled) so the operator can promote the
    winning arm -- e.g. MOMENTUM_T2_WIDE_PCT=1.0 after the per-arm
    broker-truth read -- without redeploy. Evidence basis: doc-265
    replay (n=306 fills: -10% stops +$11.1K vs -15/-20% +$20.7/+$30.8K
    total) + D308/D309 live shadow (n=14: ATR +$425/decision vs phase1).
    """
    raw = os.environ.get("MOMENTUM_T2_WIDE_PCT", "").strip()
    if not raw:
        return _WIDE_BUCKET_THRESHOLD
    try:
        v = float(raw)
    except ValueError:
        return _WIDE_BUCKET_THRESHOLD
    if v <= 1.0:
        v *= 100.0
    return max(0, min(100, int(round(v))))


def t2_enabled() -> bool:
    """Read env var. Returns True iff T2 A/B split is active.

    Operator can toggle this at any time without restart -- the executor
    queries the verdict's execution_arm field, which was set by this
    module at verdict-emission time based on env at THAT moment.
    """
    return os.environ.get(_ENV_FLAG, "").strip().lower() in ("1", "true", "yes", "on")


def _trading_date_str(now: datetime | None = None) -> str:
    """ET-anchored trading date as 'YYYY-MM-DD'.

    Anchored to America/New_York because the arm assignment must be
    stable across multiple verdict emissions on the same trading day.
    UTC midnight would split the morning batch into two arm buckets
    on days where the bot starts pre-04:00 ET (early shift / DST).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    return now.astimezone(_ET).strftime("%Y-%m-%d")


def assign_arm(symbol: str, trading_date: str | None = None
               ) -> tuple[str, float]:
    """Deterministically map (date, symbol) -> (arm_label, qty_multiplier).

    Returns ("tight_stop", 1.0) when T2 is disabled (env unset/falsy)
    so every caller is safe to invoke regardless of env state.

    When T2 is enabled:
      - MD5 hash of ``{trading_date}:{symbol}``
      - Take top 8 hex chars as a 32-bit unsigned int
      - Bucket = int % 100
      - Bucket < 50  -> wide_stop, qty_multiplier=0.5
      - Bucket >= 50 -> tight_stop, qty_multiplier=1.0
    """
    if not t2_enabled():
        return ("tight_stop", 1.0)

    if trading_date is None:
        trading_date = _trading_date_str()

    key = f"{trading_date}:{symbol.upper()}".encode("utf-8")
    digest_hex = hashlib.md5(key, usedforsecurity=False).hexdigest()
    bucket = int(digest_hex[:8], 16) % 100

    if bucket < _wide_bucket_threshold():
        return ("wide_stop", _WIDE_QTY_MULT)
    else:
        return ("tight_stop", 1.0)


def assigned_arm_for_verdict(verdict, now: datetime | None = None):
    """Apply T2 arm assignment to a TradeVerdict via model_copy.

    Returns the verdict (unchanged) when T2 disabled OR when the verdict
    is for a short trade (T2 is long-side only for now -- short stops
    have different mechanics).

    When T2 enabled and verdict is long-side, returns a new TradeVerdict
    with execution_arm + qty_multiplier set.
    """
    if not t2_enabled():
        return verdict

    if getattr(verdict, "direction", "long") != "long":
        # Short trades use buy-stop above entry; T2 wide-stop logic
        # was designed for long-side ATR widening. Punt for now.
        return verdict

    arm, mult = assign_arm(verdict.ticker, _trading_date_str(now))

    try:
        new_verdict = verdict.model_copy(update={
            "execution_arm": arm,
            "qty_multiplier": mult,
        })
        logger.info(
            "D310.T2 ARM ASSIGNED %s: arm=%s qty_mult=%.2f "
            "(MOMENTUM_T2_ENABLED=1)",
            verdict.ticker, arm, mult,
        )
        return new_verdict
    except Exception as _e:
        # D310.T2.v2 (2026-05-27): RAISE rather than silently fall
        # through to unchanged verdict. The orchestrator catches this
        # and HARD-ABORTS the entry when T2 is enabled -- preserving
        # A/B experiment integrity. A trade entering without an arm
        # assignment silently conflates the experiment.
        # Previously we returned verdict unchanged here; that was the
        # silent-non-fatal failure mode filed in the 2026-05-26
        # pre-open deep-dive.
        logger.warning(
            "D310.T2 arm assignment failed for %s (%s); raising for "
            "orchestrator to HARD ABORT.",
            verdict.ticker, _e,
        )
        raise
