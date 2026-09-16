"""
QMP (Quote-Midpoint) trade-print signing per Barber–Huang–Jorion–Odean–Schwarz
2024 (Journal of Finance), "A (Sub)penny for Your Thoughts".

The classical Lee-Ready (1991) rule and the BJZZ subpenny rule both lose
accuracy as bid-ask spreads widen — BJZZ's accuracy collapses from ~93%
at 1¢ spreads to ~52% (random) at 10¢+ spreads, which is precisely the
microcap regime MOMENTUM-X trades. QMP keeps signing accuracy ≥95% across
all spread widths.

The algorithm:
  1. trade_price > midpoint                → +1 (buy)
  2. trade_price < midpoint                → −1 (sell)
  3. trade_price == midpoint               → Lee-Ready tick fallback:
       3a. prior_tick_price is None        → 0 (unknown)
       3b. trade_price > prior_tick_price  → +1 (uptick → buy)
       3c. trade_price < prior_tick_price  → −1 (downtick → sell)
       3d. trade_price == prior_tick_price → 0 (zerotick — no info)

Locked / crossed / missing quotes return 0 (unknown).

This module is the canonical reference implementation. The legacy
classifier in `src/data/order_flow.py:_classify_trade_direction()` uses
a quote-rule + midpoint hybrid that returns UNKNOWN at exact midpoint
(no Lee-Ready fallback). `midpoint_baseline_sign()` mirrors that
behaviour for direct comparison via `signing_disagreement()`.

Wired into EOD reconciliation as D260 SIGNING_DISAGREEMENT: any time
QMP and the legacy classifier disagree on direction by more than a
configured threshold per ticker per session, the trade is flagged for
manual review.

References:
  - Barber, B., Huang, X., Jorion, P., Odean, T., Schwarz, C. (2024).
    "A (Sub)penny for Your Thoughts: Reassessing the Quote and Lee-Ready
    Algorithms for Equity Trade Direction Classification." JF, in press.
  - docs/research-log/18_slippage_methodology_application_v2.md §1.6
  - docs/research-log/23_slippage_methodology_v2.2_offensive.md §QMP
  - docs/research-log/26_d_code_registry.md (D260 reservation)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


# ── Result dataclass ────────────────────────────────────────────────


@dataclass(frozen=True)
class QmpResult:
    """One signed trade.

    Attributes:
      direction: +1 (buy), −1 (sell), 0 (unknown).
      rule_used: which branch produced the answer; useful for diagnostics
                 + the D260 disagreement audit.
      midpoint:  the (bid + ask) / 2 used; None if no usable quote.
    """
    direction: int
    rule_used: str
    midpoint: float | None


# ── Branch labels (kept as constants for greppability) ──────────────


RULE_NO_QUOTE = "no_quote"
RULE_LOCKED_OR_CROSSED = "locked_or_crossed"
RULE_ABOVE_MID = "above_mid"
RULE_BELOW_MID = "below_mid"
RULE_LEE_READY_UPTICK = "lee_ready_uptick"
RULE_LEE_READY_DOWNTICK = "lee_ready_downtick"
RULE_LEE_READY_ZEROTICK = "lee_ready_zerotick"
RULE_AT_MID_NO_PRIOR = "at_mid_no_prior"


# ── Core signing functions ──────────────────────────────────────────


def qmp_sign(
    *,
    trade_price: float,
    bid: float,
    ask: float,
    prior_tick_price: float | None = None,
) -> QmpResult:
    """Sign a single trade print per QMP rule with Lee-Ready fallback.

    Args:
      trade_price:      The print's transaction price.
      bid:              Best bid at trade time.
      ask:              Best ask at trade time.
      prior_tick_price: Optional — the most recent prior trade price for
                        the same symbol. Used only when trade_price ==
                        midpoint (Lee-Ready fallback).

    Returns:
      QmpResult with direction in {+1, -1, 0} and the rule branch label.
    """
    # Quote sanity
    if bid <= 0 or ask <= 0:
        return QmpResult(0, RULE_NO_QUOTE, None)
    if bid >= ask:
        # Locked (bid == ask) or crossed (bid > ask) — no usable midpoint
        return QmpResult(0, RULE_LOCKED_OR_CROSSED, None)

    midpoint = (bid + ask) / 2.0

    if trade_price > midpoint:
        return QmpResult(+1, RULE_ABOVE_MID, midpoint)
    if trade_price < midpoint:
        return QmpResult(-1, RULE_BELOW_MID, midpoint)

    # Exact midpoint → Lee-Ready tick rule
    if prior_tick_price is None:
        return QmpResult(0, RULE_AT_MID_NO_PRIOR, midpoint)
    if trade_price > prior_tick_price:
        return QmpResult(+1, RULE_LEE_READY_UPTICK, midpoint)
    if trade_price < prior_tick_price:
        return QmpResult(-1, RULE_LEE_READY_DOWNTICK, midpoint)
    return QmpResult(0, RULE_LEE_READY_ZEROTICK, midpoint)


def midpoint_baseline_sign(
    *,
    trade_price: float,
    bid: float,
    ask: float,
) -> int:
    """Mirror of `src/data/order_flow.py:_classify_trade_direction()`.

    Quote rule + midpoint hybrid that returns UNKNOWN at exact midpoint
    (no Lee-Ready fallback). Held in pure-function form here so the
    QMP migration can compute disagreement deltas without dragging in
    the full OrderFlowAnalyzer dependency tree.
    """
    if bid <= 0 or ask <= 0:
        return 0
    if bid >= ask:
        return 0
    midpoint = (bid + ask) / 2.0
    if trade_price >= ask:
        return +1
    if trade_price <= bid:
        return -1
    if trade_price > midpoint:
        return +1
    if trade_price < midpoint:
        return -1
    return 0  # exact midpoint → UNKNOWN under the legacy rule


# ── Disagreement audit (D260 SIGNING_DISAGREEMENT) ─────────────────


@dataclass(frozen=True)
class DisagreementSummary:
    """Per-corpus disagreement metrics. Feeds D260 SIGNING_DISAGREEMENT."""
    total_trades: int
    qmp_signed: int
    baseline_signed: int
    disagreed: int                 # both signed but in opposite directions
    qmp_rescued: int               # baseline=0, qmp ∈ {+1, -1}
    baseline_rescued: int          # qmp=0, baseline ∈ {+1, -1} (rare)
    disagreement_rate: float       # disagreed / max(qmp_signed ∩ baseline_signed, 1)
    rescue_rate: float             # qmp_rescued / total_trades


def signing_disagreement(
    trades: Iterable[dict],
    *,
    threshold: float = 0.10,
) -> tuple[DisagreementSummary, bool]:
    """Compute per-corpus QMP vs baseline disagreement metrics.

    Args:
      trades:    Iterable of dicts each with keys
                 trade_price (float), bid (float), ask (float),
                 prior_tick_price (float|None — optional).
      threshold: Disagreement-rate threshold above which D260 fires.

    Returns:
      (summary, fires_d260) — `fires_d260` is True iff
      summary.disagreement_rate > threshold.

    Direction flip (+1 → −1 or vice versa) is the strict "disagreement";
    QMP signing a trade that the baseline left UNKNOWN (the at-midpoint
    rescue case) is counted separately as `qmp_rescued`. Real-world
    expectation: rescue rate dominates disagreement rate, since QMP's
    main lift is recovering at-midpoint trades.
    """
    total = 0
    qmp_signed = 0
    baseline_signed = 0
    disagreed = 0
    qmp_rescued = 0
    baseline_rescued = 0
    both_signed = 0

    for t in trades:
        total += 1
        prior = t.get("prior_tick_price")
        q = qmp_sign(
            trade_price=t["trade_price"],
            bid=t["bid"],
            ask=t["ask"],
            prior_tick_price=prior,
        )
        b = midpoint_baseline_sign(
            trade_price=t["trade_price"],
            bid=t["bid"],
            ask=t["ask"],
        )
        if q.direction != 0:
            qmp_signed += 1
        if b != 0:
            baseline_signed += 1
        if q.direction != 0 and b != 0:
            both_signed += 1
            if q.direction != b:
                disagreed += 1
        elif q.direction != 0 and b == 0:
            qmp_rescued += 1
        elif q.direction == 0 and b != 0:
            baseline_rescued += 1

    disagreement_rate = disagreed / max(both_signed, 1)
    rescue_rate = qmp_rescued / max(total, 1)
    summary = DisagreementSummary(
        total_trades=total,
        qmp_signed=qmp_signed,
        baseline_signed=baseline_signed,
        disagreed=disagreed,
        qmp_rescued=qmp_rescued,
        baseline_rescued=baseline_rescued,
        disagreement_rate=disagreement_rate,
        rescue_rate=rescue_rate,
    )
    return summary, disagreement_rate > threshold
