"""Pipeline runner — replay the production gate cascade against a Scenario.

DESIGN: Gate-replay, not full-LLM-replay
=========================================
The orchestrator audit (Phase 2.0) revealed that full pipeline replay needs:
  1. Mock litellm.acompletion() — every agent makes LLM calls
  2. Patch time.monotonic() in 13+ places (freezegun doesn't catch it)
  3. Reset module-level singletons between scenarios (FinBERT, circuit breakers,
     alert rate-limiter)
  4. Hybrid in-process + subprocess strategy for circuit-breaker recovery

That's 4-6 hours of plumbing for limited extra signal: the LLM agent outputs
would be deterministic mocks anyway — the same input always produces the same
mock output. The actual GATES (D112 router, D101 consensus, D124 alignment,
VWAP bias, MFCS buy threshold, ORB confirmation) are pure logic and exactly
what's been blocking trades.

So the arena uses gate-replay mode: synthesize realistic agent signals from
candidate features, compute the deterministic MFCS the same way production
does, then run the EXACT gate logic against it. Every gate's threshold and
condition is sourced from production code — when production rejects with
"D112 Router: float 1.17B > 200M max", this arena rejects with the same
reason on the same scenario.

Phase 5 may upgrade to "real-LLM-with-cached-responses" mode for shadow score
training. The signal-synthesis layer is pluggable — see _synthesize_signals().

Public API
----------
    run_scenario(scenario, config=None) -> ProductionVerdict
    run_scenarios(scenarios, config=None, parallel_workers=1) -> Iterator[verdict]
    reset_pipeline_state() -> None  # for tests; arena is stateless by design

NB: top-level functions only — multiprocessing.Pool requires picklable callables.
"""

from __future__ import annotations

import logging
import math
import multiprocessing as mp
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from src.production_arena.types import (
    ArenaConfig,
    ProductionVerdict,
    Scenario,
)

logger = logging.getLogger(__name__)


# ── Production thresholds (mirror config/settings.py) ────────────────────
# These are the post-D220 defaults. ArenaConfig overrides override these for
# counterfactual sweeps. Numbers match config/settings.py exactly so any
# divergence is a bug, not a feature.

_DEFAULT_MAX_FLOAT = 2_000_000_000          # config/settings.py:1342 (D220)
_DEFAULT_MIN_PRICE = 0.50                   # config/settings.py:1338
_DEFAULT_MIN_RVOL = 1.0                     # config/settings.py:1330
_DEFAULT_MIN_GAP_PCT = 0.03                 # config/settings.py:1346
_DETERMINISTIC_STRONG_PASS = 0.40           # config/settings.py:1366
_VWAP_BIAS_THRESHOLD_PCT = 2.0              # orchestrator.py:1349 (D220)
_MFCS_BUY_THRESHOLD = 0.25                  # config/settings.py
_PUMP_GAP_THRESHOLD = 0.30                  # config/settings.py:1351
_PUMP_PRICE_THRESHOLD = 3.00                # config/settings.py:1356


# ── Gate identifiers (file:line format used in ProductionVerdict.gate_rejected) ──

GATE_ROUTER_FLOAT = "adaptive_router.py:149"
GATE_ROUTER_PRICE = "adaptive_router.py:141"
GATE_ROUTER_RVOL = "adaptive_router.py:133"
GATE_ROUTER_GAP = "adaptive_router.py:160"
GATE_CONSENSUS_D101 = "orchestrator.py:1016"
GATE_ALIGNMENT_D124 = "orchestrator.py:1054"
GATE_VWAP_BIAS = "orchestrator.py:1349"
GATE_MFCS_THRESHOLD = "orchestrator.py:mfcs_buy_threshold"
GATE_ORB_HELD = "entry_delay.py:orb_confirmation"


# ── Synthetic agent signal model ─────────────────────────────────────────


@dataclass(frozen=True)
class _SyntheticSignals:
    """Deterministic agent-output stand-ins.

    These mirror what production's news_agent / technical_agent / risk_agent /
    manipulation_classifier would produce. The synthesis is heuristic but
    consistent — same scenario always produces the same signals.
    """
    news_signal: str          # "BULL" | "BEAR" | "NEUTRAL"
    news_confidence: float    # 0.0-1.0
    technical_signal: str
    technical_confidence: float
    risk_signal: str          # BEAR if pump pattern, else NEUTRAL
    risk_confidence: float
    manipulation_signal: str
    manipulation_confidence: float

    @property
    def directional_count(self) -> int:
        """How many agents produced a non-NEUTRAL signal (D101 input)."""
        return sum(
            1
            for s in (
                self.news_signal,
                self.technical_signal,
                self.risk_signal,
                self.manipulation_signal,
            )
            if s != "NEUTRAL"
        )

    @property
    def bullish_count(self) -> int:
        return sum(
            1
            for s in (
                self.news_signal,
                self.technical_signal,
                self.risk_signal,
                self.manipulation_signal,
            )
            if s == "BULL"
        )

    @property
    def bearish_count(self) -> int:
        return sum(
            1
            for s in (
                self.news_signal,
                self.technical_signal,
                self.risk_signal,
                self.manipulation_signal,
            )
            if s == "BEAR"
        )

    @property
    def bullish_confidence(self) -> float:
        return sum(
            c
            for s, c in (
                (self.news_signal, self.news_confidence),
                (self.technical_signal, self.technical_confidence),
                (self.risk_signal, self.risk_confidence),
                (self.manipulation_signal, self.manipulation_confidence),
            )
            if s == "BULL"
        )

    @property
    def bearish_confidence(self) -> float:
        return sum(
            c
            for s, c in (
                (self.news_signal, self.news_confidence),
                (self.technical_signal, self.technical_confidence),
                (self.risk_signal, self.risk_confidence),
                (self.manipulation_signal, self.manipulation_confidence),
            )
            if s == "BEAR"
        )


# ── Scenario → CandidateStock-equivalent features ────────────────────────


def _extract_candidate_fields(scenario: Scenario) -> dict[str, Any]:
    """Extract the fields the gate logic cares about. Minimal — no Pydantic."""
    f = scenario.premarket_features
    # RVOL: prefer explicit, fall back to crude estimate from premarket vs daily.
    rvol = f.get("rvol")
    if rvol is None:
        pmv = f.get("premarket_volume", 0) or 0
        dv = f.get("day_volume", 0) or 0
        # Rough proxy: premarket_vol vs hypothetical 1-min average daily volume.
        # If premarket_vol > 5min of avg → RVOL > 5. If 0 → fall back to a neutral 3.0
        # (passes the 1.0 gate but isn't artificially high).
        if pmv > 0 and dv > 0:
            rvol = pmv / max(1.0, dv / 390.0)
        else:
            rvol = 3.0
    return {
        "ticker": scenario.ticker,
        "current_price": f.get("price") or f.get("open") or 0.0,
        "previous_close": f.get("prior_close") or 0.0,
        "gap_pct": f.get("gap_pct") or 0.0,
        "rvol": rvol,
        "premarket_volume": int(f.get("premarket_volume") or 0),
        "dollar_volume": float(f.get("dollar_volume") or 0.0),
        "float_shares": f.get("float_shares"),  # often None in backfill — that's fine
        "market_cap": f.get("market_cap"),
        "vwap": f.get("vwap"),
    }


# ── Signal synthesis ─────────────────────────────────────────────────────


def _synthesize_signals(cand: dict[str, Any]) -> _SyntheticSignals:
    """Heuristic agent-signal generator.

    NEWS: Stocks with high dollar volume on a meaningful gap almost always have
        a real catalyst (FDA, earnings, M&A). dolvol >= $5M with gap >= 10% =>
        BULL. Below thresholds => NEUTRAL.

    TECHNICAL: RVOL is the strongest deterministic technical signal. >= 5x =>
        BULL with confidence proportional to how far above 5x. Below 2x =>
        BEAR (wrong universe). Otherwise NEUTRAL.

    RISK: Classic pump pattern (gap > 30% AND price < $3) => BEAR.
        Otherwise NEUTRAL.

    MANIPULATION: Sub-5M float with > 50% gap is a classic pump-and-dump setup
        but also a legit small-float runner. Mark NEUTRAL when uncertain;
        BULL when float in [1M, 20M] (the sweet spot per the user's Mar-30
        Selection Arena finding).
    """
    gap = cand["gap_pct"]
    price = cand["current_price"]
    rvol = cand["rvol"]
    dolvol = cand["dollar_volume"]
    floatv = cand.get("float_shares")

    # NEWS
    if dolvol >= 5_000_000 and gap >= 0.10:
        news_signal, news_conf = "BULL", min(0.95, 0.5 + min(0.4, dolvol / 50_000_000))
    elif dolvol >= 2_000_000 and gap >= 0.05:
        news_signal, news_conf = "BULL", 0.55
    else:
        news_signal, news_conf = "NEUTRAL", 0.30

    # TECHNICAL
    if rvol >= 5.0:
        technical_signal = "BULL"
        technical_conf = min(0.90, 0.5 + (rvol - 5.0) / 20.0)
    elif rvol < 2.0:
        technical_signal = "BEAR"
        technical_conf = 0.60
    else:
        technical_signal, technical_conf = "NEUTRAL", 0.40

    # RISK — pump pattern detection
    if gap > _PUMP_GAP_THRESHOLD and price < _PUMP_PRICE_THRESHOLD:
        risk_signal, risk_conf = "BEAR", 0.65
    else:
        risk_signal, risk_conf = "NEUTRAL", 0.25

    # MANIPULATION — float-based heuristic
    if floatv is not None and 1_000_000 <= floatv <= 20_000_000:
        manip_signal, manip_conf = "BULL", 0.60
    elif floatv is not None and floatv > 500_000_000:
        manip_signal, manip_conf = "BEAR", 0.40
    else:
        manip_signal, manip_conf = "NEUTRAL", 0.30

    return _SyntheticSignals(
        news_signal=news_signal,
        news_confidence=news_conf,
        technical_signal=technical_signal,
        technical_confidence=technical_conf,
        risk_signal=risk_signal,
        risk_confidence=risk_conf,
        manipulation_signal=manip_signal,
        manipulation_confidence=manip_conf,
    )


# ── MFCS computation ─────────────────────────────────────────────────────


def _normalize_score(value: float, target: float, prefer_low: bool = False) -> float:
    """Map a value to [0, 1] relative to a target. prefer_low: closer to 0 is better."""
    if target <= 0:
        return 0.0
    if prefer_low:
        # Inverse: closer to 0 is 1.0; >= target is 0.0
        return max(0.0, min(1.0, 1.0 - (value / target)))
    # Forward: 0 is 0.0; >= target is 1.0; saturating beyond
    return max(0.0, min(1.0, value / target))


def _deterministic_mfcs(cand: dict[str, Any]) -> float:
    """RVOL + float + technical subset — pure math, no LLM.

    Mirrors src/core/scoring.py's deterministic-only path. Used by the D112
    router as the gating signal for the strong-pass escape hatch.
    """
    rvol_score = _normalize_score(cand["rvol"], target=10.0)
    floatv = cand.get("float_shares")
    if floatv is not None:
        # Sub-5M is ideal; saturate at 50M.
        if floatv < 5_000_000:
            float_score = 0.95
        elif floatv < 50_000_000:
            float_score = 0.6
        else:
            float_score = 0.2
    else:
        float_score = 0.4
    # Gap quality — sweet spot 10-50%
    gap = cand["gap_pct"]
    if 0.10 <= gap <= 0.50:
        gap_score = 0.85
    elif 0.05 <= gap < 0.10:
        gap_score = 0.40
    elif gap > 0.50:
        gap_score = 0.55
    else:
        gap_score = 0.10

    # Weighted combo (matches the ScoringWeights defaults: vol 25%, float 15%, technical 5%)
    return 0.45 * rvol_score + 0.30 * float_score + 0.25 * gap_score


def _full_mfcs(cand: dict[str, Any], signals: _SyntheticSignals, det_mfcs: float) -> float:
    """Combine deterministic + agent signals into the final MFCS.

    Mirrors orchestrator scoring: 55% news + 25% volume + 15% float + 5% technical.
    The deterministic component already absorbed volume/float/technical, so we just
    layer the news on top.
    """
    news_contribution = 0.0
    if signals.news_signal == "BULL":
        news_contribution = 0.55 * signals.news_confidence
    elif signals.news_signal == "BEAR":
        news_contribution = -0.55 * signals.news_confidence

    # Risk penalty — pump pattern subtracts (mirrors the risk veto in production)
    risk_penalty = 0.0
    if signals.risk_signal == "BEAR":
        risk_penalty = -0.20 * signals.risk_confidence

    return max(0.0, min(1.0, 0.45 * det_mfcs + news_contribution + risk_penalty + 0.20))


# ── Gate logic (mirrors production exactly) ──────────────────────────────


def _check_router(
    cand: dict[str, Any], det_mfcs: float, config: ArenaConfig
) -> str | None:
    """Returns gate name if rejected, None if pass. Mirrors adaptive_router.py."""
    max_float = config.instant_reject_max_float or _DEFAULT_MAX_FLOAT
    min_price = config.instant_reject_min_price or _DEFAULT_MIN_PRICE

    if cand["rvol"] < _DEFAULT_MIN_RVOL:
        return GATE_ROUTER_RVOL
    if cand["current_price"] < min_price:
        return GATE_ROUTER_PRICE
    # Float check WITH D220 escape hatch:
    floatv = cand.get("float_shares")
    if (
        floatv is not None
        and floatv > max_float
        and det_mfcs < _DETERMINISTIC_STRONG_PASS
    ):
        return GATE_ROUTER_FLOAT
    if cand["gap_pct"] < _DEFAULT_MIN_GAP_PCT:
        return GATE_ROUTER_GAP
    return None


def _check_consensus(signals: _SyntheticSignals, det_mfcs: float) -> str | None:
    """D101 consensus: ≥1 directional agent OR MFCS≥0.30 waiver."""
    if signals.directional_count >= 1:
        return None
    if det_mfcs >= 0.30:
        return None
    return GATE_CONSENSUS_D101


def _check_alignment(signals: _SyntheticSignals) -> str | None:
    """D124: reject if bearish dominates by 2+ OR by 1.5x confidence."""
    if signals.bearish_count >= signals.bullish_count + 2:
        return GATE_ALIGNMENT_D124
    if (
        signals.bearish_count > signals.bullish_count
        and signals.bearish_confidence > signals.bullish_confidence * 1.5
    ):
        return GATE_ALIGNMENT_D124
    return None


def _check_vwap(cand: dict[str, Any], config: ArenaConfig) -> str | None:
    """VWAP bias: skip first 10 min, threshold 2% (D220 defaults).

    The arena assumes all scenarios are evaluated AFTER the 10-minute skip
    window — by definition we're scoring against post-open data. So we always
    apply the threshold (no time skip in arena context).
    """
    threshold = config.vwap_bias_threshold_pct or _VWAP_BIAS_THRESHOLD_PCT
    vwap = cand.get("vwap")
    if vwap is None or vwap <= 0:
        return None
    price = cand["current_price"]
    if price >= vwap:
        return None
    deficit_pct = (vwap - price) / vwap * 100
    if deficit_pct > threshold:
        return GATE_VWAP_BIAS
    return None


def _check_mfcs_threshold(full_mfcs: float, config: ArenaConfig) -> str | None:
    threshold = config.mfcs_buy_threshold or _MFCS_BUY_THRESHOLD
    if full_mfcs < threshold:
        return GATE_MFCS_THRESHOLD
    return None


def _check_orb(scenario: Scenario) -> tuple[str | None, bool | None]:
    """Returns (gate_or_None, orb_confirmed). orb_broken == False explicitly rejects.

    If labeled_outcome.orb_broken is None (no label), we don't enforce ORB —
    arena passes through with orb_confirmed=None.
    """
    orb = scenario.labeled_outcome.orb_broken
    if orb is False:
        return (GATE_ORB_HELD, False)
    return (None, orb)  # orb is True or None


# ── Verdict construction ────────────────────────────────────────────────


def _no_trade_verdict(
    scenario: Scenario,
    gate: str,
    mfcs: float | None,
    signals: _SyntheticSignals | None,
    elapsed_ms: float,
    orb_confirmed: bool | None = None,
) -> ProductionVerdict:
    components = None
    if signals is not None:
        components = {
            "news": signals.news_confidence if signals.news_signal == "BULL" else 0.0,
            "technical": signals.technical_confidence if signals.technical_signal == "BULL" else 0.0,
            "risk": signals.risk_confidence if signals.risk_signal == "BEAR" else 0.0,
            "manipulation": signals.manipulation_confidence,
        }
    return ProductionVerdict(
        ticker=scenario.ticker,
        session_date=scenario.session_date,
        decision="NO_TRADE",
        gate_rejected=gate,
        mfcs=mfcs,
        mfcs_components=components,
        mfe_pct=scenario.labeled_outcome.mfe_pct,
        mae_pct=scenario.labeled_outcome.mae_pct,
        orb_confirmed=orb_confirmed,
        elapsed_ms=elapsed_ms,
    )


def _buy_verdict(
    scenario: Scenario,
    mfcs: float,
    signals: _SyntheticSignals,
    orb_confirmed: bool | None,
    elapsed_ms: float,
) -> ProductionVerdict:
    """Construct a BUY verdict with simulated PnL from labeled outcome.

    The arena uses the close-of-session return as the realized PnL — this
    matches the conservative "don't try to model exit logic" stance of Phase 2.
    Phase 4 may extend with TP1/TP2/trail simulation.
    """
    label = scenario.labeled_outcome
    entry = label.entry_price
    pnl = label.close_return  # decimal, e.g. 0.05 = +5%
    exit_price = None
    if entry is not None and pnl is not None:
        exit_price = entry * (1.0 + pnl)
    return ProductionVerdict(
        ticker=scenario.ticker,
        session_date=scenario.session_date,
        decision="BUY",
        gate_rejected=None,
        mfcs=mfcs,
        mfcs_components={
            "news": signals.news_confidence if signals.news_signal == "BULL" else 0.0,
            "technical": signals.technical_confidence if signals.technical_signal == "BULL" else 0.0,
            "risk": signals.risk_confidence if signals.risk_signal == "BEAR" else 0.0,
            "manipulation": signals.manipulation_confidence,
        },
        would_be_entry_price=entry,
        would_be_exit_price=exit_price,
        would_be_exit_time=None,  # session close — not modeled to the minute here
        would_be_pnl_pct=pnl,
        mfe_pct=label.mfe_pct,
        mae_pct=label.mae_pct,
        orb_confirmed=orb_confirmed,
        elapsed_ms=elapsed_ms,
    )


def _error_verdict(scenario: Scenario, msg: str, elapsed_ms: float) -> ProductionVerdict:
    return ProductionVerdict(
        ticker=scenario.ticker,
        session_date=scenario.session_date,
        decision="ERROR",
        elapsed_ms=elapsed_ms,
        error_msg=msg,
    )


# ── Public entry points ──────────────────────────────────────────────────


def run_scenario(
    scenario: Scenario, config: ArenaConfig | None = None
) -> ProductionVerdict:
    """Run one scenario through the gate cascade.

    Pure function: same scenario + config → same verdict every time. No I/O,
    no globals, no clock dependence. Picklable for multiprocessing.
    """
    config = config or ArenaConfig()
    t0 = time.perf_counter()

    try:
        cand = _extract_candidate_fields(scenario)

        # Sanity: a stock with current_price <= 0 is unusable
        if cand["current_price"] <= 0:
            return _error_verdict(scenario, "current_price <= 0", (time.perf_counter() - t0) * 1000)

        signals = _synthesize_signals(cand)
        det_mfcs = _deterministic_mfcs(cand)

        # Gate 1: D112 router
        gate = _check_router(cand, det_mfcs, config)
        if gate:
            return _no_trade_verdict(scenario, gate, det_mfcs, signals, (time.perf_counter() - t0) * 1000)

        # Gate 2: D101 consensus
        gate = _check_consensus(signals, det_mfcs)
        if gate:
            return _no_trade_verdict(scenario, gate, det_mfcs, signals, (time.perf_counter() - t0) * 1000)

        # Gate 3: D124 alignment
        gate = _check_alignment(signals)
        if gate:
            return _no_trade_verdict(scenario, gate, det_mfcs, signals, (time.perf_counter() - t0) * 1000)

        # Gate 4: VWAP bias
        gate = _check_vwap(cand, config)
        if gate:
            return _no_trade_verdict(scenario, gate, det_mfcs, signals, (time.perf_counter() - t0) * 1000)

        # Compute full MFCS
        full_mfcs = _full_mfcs(cand, signals, det_mfcs)

        # Gate 5: MFCS buy threshold
        gate = _check_mfcs_threshold(full_mfcs, config)
        if gate:
            return _no_trade_verdict(scenario, gate, full_mfcs, signals, (time.perf_counter() - t0) * 1000)

        # Gate 6: ORB confirmation (from labeled outcome)
        gate, orb_confirmed = _check_orb(scenario)
        if gate:
            return _no_trade_verdict(
                scenario, gate, full_mfcs, signals, (time.perf_counter() - t0) * 1000, orb_confirmed=orb_confirmed
            )

        # All gates passed: BUY
        return _buy_verdict(
            scenario, full_mfcs, signals, orb_confirmed, (time.perf_counter() - t0) * 1000
        )

    except Exception as e:
        logger.exception("run_scenario failed for %s %s: %s", scenario.ticker, scenario.session_date, e)
        return _error_verdict(scenario, f"{type(e).__name__}: {e}", (time.perf_counter() - t0) * 1000)


def _run_one_for_pool(args: tuple[Scenario, ArenaConfig]) -> ProductionVerdict:
    """Top-level helper for multiprocessing.Pool. Must be picklable."""
    return run_scenario(args[0], args[1])


def run_scenarios(
    scenarios: list[Scenario],
    config: ArenaConfig | None = None,
    parallel_workers: int = 1,
) -> Iterator[ProductionVerdict]:
    """Run many scenarios. parallel_workers>1 uses multiprocessing.Pool.

    Yields verdicts as they complete (lazy when serial, batched when parallel).
    """
    config = config or ArenaConfig()
    if parallel_workers <= 1:
        for s in scenarios:
            yield run_scenario(s, config)
        return

    with mp.Pool(parallel_workers) as pool:
        for v in pool.imap_unordered(
            _run_one_for_pool,
            [(s, config) for s in scenarios],
            chunksize=max(1, len(scenarios) // (parallel_workers * 4)),
        ):
            yield v


def reset_pipeline_state() -> None:
    """No-op: the arena is intentionally stateless. Provided for symmetry with
    a hypothetical full-LLM-replay mode that would have state to reset."""
    pass
