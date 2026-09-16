"""
MOMENTUM-X Domain Models

### ARCHITECTURAL CONTEXT
Immutable data contracts for the entire pipeline. Every agent emits an AgentSignal,
the scoring engine produces a ScoredCandidate, and the debate engine produces a
TradeVerdict. These models are the shared vocabulary of the system.

Ref: ADR-001 (Agent Communication Protocol)
Ref: MOMENTUM_LOGIC.md §5 (MFCS), §10 (Debate Divergence)

### DESIGN DECISIONS
- Pydantic models for runtime validation + serialization
- Frozen=True for immutability (signals should never be mutated after creation)
- Literal types for constrained enums (better than stringly-typed)
- All timestamps in UTC
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Literal, get_args

from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


# ─── Enums as Literals ───────────────────────────────────────────────

SignalDirection = Literal[
    "STRONG_BULL", "BULL", "NEUTRAL", "BEAR", "STRONG_BEAR"
]

CatalystType = Literal[
    "FDA_APPROVAL", "EARNINGS_BEAT", "M_AND_A", "CONTRACT_WIN",
    "LEGAL_WIN", "MANAGEMENT_CHANGE", "ANALYST_UPGRADE",
    "PRODUCT_LAUNCH", "REGULATORY", "SHORT_SQUEEZE",
    "CORPORATE_UPDATE", "SECTOR_CATALYST",  # D121: news agent uses these
    "NONE",
]

CatalystSpecificity = Literal["CONFIRMED", "RUMORED", "SPECULATIVE"]

PatternType = Literal[
    "BULL_FLAG", "CUP_HANDLE", "ASC_TRIANGLE",
    "CONSOLIDATION_BREAKOUT", "BB_SQUEEZE", "NONE",
]

RiskVerdict = Literal["APPROVE", "CAUTION", "VETO"]

TradeAction = Literal["STRONG_BUY", "BUY", "HOLD", "NO_TRADE"]

PositionSize = Literal["FULL", "HALF", "QUARTER", "NONE"]

TimeHorizon = Literal["INTRADAY", "OVERNIGHT", "MULTI_DAY"]

GapClassification = Literal["MINOR", "SIGNIFICANT", "MAJOR", "EXPLOSIVE"]

# D101 §3.2: Gap quality — distinguishes breakaway from exhaustion gaps
GapQuality = Literal["BREAKAWAY", "CONTINUATION", "EXHAUSTION", "UNKNOWN"]

# D106 §2A: Manipulation lifecycle phase — distinguishes organic momentum
# from promotional pump-and-dump schemes. Used by ManipulationClassifier agent
# to gate entry parameters (position sizing, stops, targets, hard exit time).
ManipulationPhase = Literal[
    "ORGANIC_MOMENTUM",    # Genuine catalyst-driven move
    "PROMOTIONAL_EARLY",   # Pump in progress, still tradeable with tighter params
    "PROMOTIONAL_LATE",    # Dump imminent or in progress — NO_TRADE
    "UNCERTAIN",           # Insufficient data to classify
]


# ─── LLM Output Sanitization ─────────────────────────────────────────
# D33: DeepSeek R1 sometimes returns whitespace (" ") or casing variants
# ("confirmed") for Literal fields. Pydantic rejects these immediately,
# causing agent fallback to NEUTRAL. This helper provides defensive
# sanitization: strip whitespace, case-insensitive match, safe default.


def _sanitize_literal(
    value: Any, valid_options: tuple[str, ...], default: str,
) -> str:
    """Sanitize LLM output for Literal fields: strip, uppercase-match, fallback.

    D33: Defensive layer against LLM output noise. Applied via
    @field_validator(mode="before") on all Literal fields in signal models.
    """
    if not isinstance(value, str):
        return default
    cleaned = value.strip()
    if not cleaned:
        return default
    # Exact match (most common path)
    if cleaned in valid_options:
        return cleaned
    # Case-insensitive match
    upper = cleaned.upper()
    for opt in valid_options:
        if upper == opt.upper():
            return opt
    # Partial prefix match (e.g., "CONF" → "CONFIRMED")
    for opt in valid_options:
        if opt.upper().startswith(upper):
            return opt
    logger.debug(
        "Literal sanitization fallback: %r not in %s, using %r",
        value, valid_options, default,
    )
    return default


# ─── Scanner Output ──────────────────────────────────────────────────

class CandidateStock(BaseModel, frozen=True):
    """
    Raw candidate emitted by the Scanner Engine (no LLM involved).
    Ref: MOMENTUM_LOGIC.md §1 (EMC definition)
    """

    ticker: str
    company_name: str = ""
    current_price: float
    previous_close: float
    gap_pct: float = Field(description="MOMENTUM_LOGIC.md §3")
    gap_classification: GapClassification
    rvol: float = Field(description="MOMENTUM_LOGIC.md §2")
    premarket_volume: int
    float_shares: int | None = None
    market_cap: float | None = None
    atr_ratio: float | None = Field(
        default=None, description="MOMENTUM_LOGIC.md §4"
    )
    has_news_catalyst: bool = False
    avg_daily_volume: int | None = Field(
        default=None,
        description="Previous day full-session volume (ADV proxy from prevDailyBar). "
        "Used for GEX normalization instead of premarket_volume × 10.",
    )
    scan_timestamp: datetime
    scan_phase: Literal["PRE_MARKET", "MARKET_OPEN", "INTRADAY", "AFTER_HOURS"]

    # ── GEX Fields (ADR-012, §19) ──
    # Populated by GEXCalculator when options data is available.
    # All optional for backward compatibility.
    gex_net: float | None = Field(
        default=None, description="Net dollar GEX. §19.2"
    )
    gex_normalized: float | None = Field(
        default=None, description="GEX / (ADV × Spot). §19.3"
    )
    gamma_flip_price: float | None = Field(
        default=None, description="Gamma zero-crossing price. §19.4"
    )
    gex_regime: str | None = Field(
        default=None, description="SUPPRESSION / NEUTRAL / ACCELERATION. §19.5"
    )

    # ── D101 §3.2: Gap Quality Classification ──
    gap_quality: GapQuality = Field(
        default="UNKNOWN",
        description="D101 §3.2: BREAKAWAY (from consolidation with catalyst) vs "
        "EXHAUSTION (multi-day run, RSI > 70) vs CONTINUATION vs UNKNOWN.",
    )

    # ── D87: Exhaustion & Corporate Action Flags ──
    rvol_exhaustion: bool = Field(
        default=False,
        description="D87: RVOL > 5.0 signals potential exhaustion, not momentum",
    )
    corporate_action_flag: str | None = Field(
        default=None,
        description="D87: Detected corporate action (e.g. 'REVERSE_SPLIT', 'SPLIT')",
    )

    # ── D212: Free Data Enrichment Fields ──
    sector: str | None = Field(
        default=None, description="D212: Industry sector from Finnhub profile2"
    )
    industry: str | None = Field(
        default=None, description="D212: Industry classification from Finnhub"
    )
    prior_gap_count: int | None = Field(
        default=None,
        description="D212: Number of 5%+ gap days in last 20 sessions. "
        "3+ = serial gapper (promotional pump pattern).",
    )
    is_day2_runner: bool = Field(
        default=False,
        description="D212: True if yesterday was also a 5%+ gap day for this ticker.",
    )

    # ── Bug AL (Tier 3 #9, 2026-04-27): Float plausibility validator ──
    # SCNI today was reported by Finnhub with float_shares=11,313,568,000
    # (11.3 BILLION shares) on a $2M market cap stock at $0.40 — clearly
    # impossible. The D112 adaptive router rejected on `float > 2B max`
    # before any agent could evaluate. This was the highest-RVOL setup
    # of the day (3667.6x) — lost to a 1000× data error.
    #
    # The fix: if float_shares is wildly out of band vs the implied
    # share count from market_cap / current_price, drop float_shares
    # to None so downstream gates treat it as "unknown" rather than
    # "too large." The router's float_max check skips when float is
    # None (per adaptive_router.py:156: `candidate.float_shares is not None`).
    #
    # Threshold of 100× implied is generous — normal stocks have ratio
    # ~1.0; ETFs may be 2-3×; an actual 100× divergence indicates a
    # data error (wrong CIK, decimal place misread, "shares outstanding"
    # mistakenly returned as "shares × 1000", etc.).
    @model_validator(mode='before')
    @classmethod
    def _validate_float_plausibility(cls, data):
        """Bug AL: detect implausible float_shares vs market_cap/price.

        See class-level note above. Mutates the input dict in place
        (model_validator(mode='before') is the only Pydantic hook that
        can do this; field_validator can't see other fields)."""
        if not isinstance(data, dict):
            return data
        fs = data.get('float_shares')
        mc = data.get('market_cap')
        px = data.get('current_price')
        # Need all three positive numbers to compute ratio
        try:
            fs_v = float(fs) if fs is not None else 0
            mc_v = float(mc) if mc is not None else 0
            px_v = float(px) if px is not None else 0
        except (TypeError, ValueError):
            return data
        if fs_v <= 0 or mc_v <= 0 or px_v <= 0:
            return data
        implied = mc_v / px_v
        if implied <= 0:
            return data
        ratio = fs_v / implied
        if ratio > 100:
            # Wildly implausible — drop to None so downstream router
            # treats float as unknown (skips the float_max check) and
            # the trade flows through normal evaluation. Operator can
            # see this in logs and refresh the data source.
            import logging as _bug_al_logging
            _bug_al_logging.getLogger(__name__).warning(
                "D272 FLOAT_IMPLAUSIBLE %s: float_shares=%.0f vs implied=%.0f "
                "(ratio=%.1fx > 100x); dropping float_shares to None — "
                "data source likely wrong (CIK mismatch, decimal misread). "
                "market_cap=$%.0f, price=$%.4f.",
                data.get('ticker', '?'), fs_v, implied, ratio, mc_v, px_v,
            )
            data = dict(data)  # make sure we don't mutate caller's dict
            data['float_shares'] = None
        return data


# ─── Agent Outputs ───────────────────────────────────────────────────

class AgentSignal(BaseModel, frozen=True):
    """
    Standardized signal emitted by every analytical agent.
    Ref: ADR-001 (Agent Communication Protocol)
    """

    agent_id: str
    ticker: str
    timestamp: datetime
    signal: SignalDirection
    confidence: float = Field(ge=0.0, le=1.0)
    raw_confidence: float | None = Field(
        default=None, ge=0.0, le=1.0,
        description="D122: Pre-deflation confidence from LLM. Stored so experiment "
        "replay can re-apply variant deflation factors. None for deterministic agents "
        "(which bypass deflation entirely).",
    )
    reasoning: str
    key_data: dict = Field(default_factory=dict)
    flags: list[str] = Field(default_factory=list)
    sources_used: list[str] = Field(default_factory=list)
    prompt_variant_id: str = "v0_control"
    model_id: str = ""
    latency_ms: float = 0.0

    @field_validator("reasoning", mode="before")
    @classmethod
    def _coerce_reasoning(cls, v: Any) -> str:
        # D162: Mixtral returns reasoning as a list[str] instead of str.
        # Recurring error every session. Join list items with newlines so
        # the NewsSignal model accepts the value without a validation error.
        if isinstance(v, list):
            return "\n".join(str(item) for item in v)
        return v

    @field_validator("signal", mode="before")
    @classmethod
    def _sanitize_signal(cls, v: Any) -> str:
        return _sanitize_literal(
            v, get_args(SignalDirection), "NEUTRAL",
        )


class NewsSignal(AgentSignal, frozen=True):
    """Extended signal from News Agent with catalyst-specific fields."""

    catalyst_type: CatalystType = "NONE"
    catalyst_specificity: CatalystSpecificity = "SPECULATIVE"
    sentiment_score: float = Field(default=0.0, ge=-1.0, le=1.0)
    sentiment_velocity: float = Field(
        default=0.0,
        description="MOMENTUM_LOGIC.md §9 — first derivative of sentiment",
    )
    source_citations: list[dict] = Field(default_factory=list)

    # D221 Phase F: dense feature vector produced by agent-as-feature-distiller
    # pattern. Populated by NewsAgent.parse_response alongside the legacy verdict
    # fields. 12-dim vector with documented value ranges -- see
    # docs/research-log/08_overfit_vs_regime_experiment.md and the NewsAgent docstring
    # for semantics. Consumers that previously read `signal` should migrate to
    # reading from this dict; the verdict field stays for schema backward
    # compatibility until all 6 agents are distilled.
    news_features: dict[str, float] = Field(default_factory=dict)

    @field_validator("catalyst_type", mode="before")
    @classmethod
    def _sanitize_catalyst_type(cls, v: Any) -> str:
        return _sanitize_literal(v, get_args(CatalystType), "NONE")

    @field_validator("catalyst_specificity", mode="before")
    @classmethod
    def _sanitize_catalyst_specificity(cls, v: Any) -> str:
        return _sanitize_literal(v, get_args(CatalystSpecificity), "SPECULATIVE")


class TechnicalSignal(AgentSignal, frozen=True):
    """Extended signal from Technical Agent with pattern-specific fields."""

    pattern_identified: PatternType = "NONE"
    pattern_timeframe: str = ""
    breakout_confirmed: bool = False
    breakout_rvol: float = 0.0
    vwap_above: bool = False
    projected_target: float | None = None
    stop_loss_level: float | None = None

    @field_validator("pattern_identified", mode="before")
    @classmethod
    def _sanitize_pattern(cls, v: Any) -> str:
        return _sanitize_literal(v, get_args(PatternType), "NONE")


class RiskSignal(AgentSignal, frozen=True):
    """Extended signal from Risk Agent with veto capability."""

    risk_verdict: RiskVerdict = "CAUTION"
    risk_score: float = Field(default=0.5, ge=0.0, le=1.0)
    risk_breakdown: dict = Field(default_factory=dict)
    veto_reason: str | None = None
    position_size_recommendation: PositionSize = "NONE"

    @field_validator("risk_verdict", mode="before")
    @classmethod
    def _sanitize_risk_verdict(cls, v: Any) -> str:
        return _sanitize_literal(v, get_args(RiskVerdict), "CAUTION")

    @field_validator("position_size_recommendation", mode="before")
    @classmethod
    def _sanitize_position_size(cls, v: Any) -> str:
        return _sanitize_literal(v, get_args(PositionSize), "NONE")


class ManipulationSignal(AgentSignal, frozen=True):
    """
    D106 §2A: Extended signal from ManipulationClassifier agent.

    Classifies whether a gap-up candidate is driven by organic momentum
    (genuine catalyst) or a promotional pump-and-dump (dilution play).
    Does NOT contribute to MFCS scoring — used exclusively to gate entry
    parameters (position size, stops, targets, hard exit time).

    Lifecycle Phases:
        ORGANIC_MOMENTUM:  Confirmed catalyst, no recent dilution filings
        PROMOTIONAL_EARLY: S-3 shelf active, vague news, tradeable with tight params
        PROMOTIONAL_LATE:  Same-day 424B5 or dump in progress — block trade
        UNCERTAIN:         Insufficient data to classify

    Ref: D106 WS2 (Manipulation Detection)
    """

    phase: ManipulationPhase = "UNCERTAIN"
    manipulation_probability: float = Field(default=0.5, ge=0.0, le=1.0)
    estimated_remaining_upside_minutes: int = Field(
        default=0,
        description="Estimated minutes of upside remaining before distribution. "
        "0 = unknown or distribution imminent.",
    )
    key_evidence: list[str] = Field(default_factory=list)
    red_flags: list[str] = Field(default_factory=list)
    filing_summary: dict = Field(
        default_factory=dict,
        description="Structured SEC filing analysis: s3_age_days, has_424b5_same_day, "
        "insider_sell_count_30d, dilution_filing_count, recent_8k_count",
    )

    @field_validator("phase", mode="before")
    @classmethod
    def _sanitize_phase(cls, v: Any) -> str:
        return _sanitize_literal(
            v, get_args(ManipulationPhase), "UNCERTAIN",
        )


# ─── Scoring Engine Output ───────────────────────────────────────────

class ScoredCandidate(BaseModel, frozen=True):
    """
    Candidate with Multi-Factor Composite Score.
    Ref: MOMENTUM_LOGIC.md §5
    """

    candidate: CandidateStock
    mfcs: float = Field(
        ge=-1.0, le=1.0,
        description="Multi-Factor Composite Score. MOMENTUM_LOGIC.md §5"
    )
    agent_signals: list[AgentSignal] = Field(default_factory=list)
    component_scores: dict[str, float] = Field(
        default_factory=dict,
        description="Individual agent contribution to MFCS",
    )
    risk_score: float = 0.0
    qualifies_for_debate: bool = False


# ─── Debate Engine Output ────────────────────────────────────────────

class DebateResult(BaseModel, frozen=True):
    """
    Output of the Bull/Bear/Judge debate.
    Ref: ADR-001 (Debate Engine Protocol)
    Ref: MOMENTUM_LOGIC.md §10 (Debate Divergence)
    """

    ticker: str
    verdict: TradeAction
    confidence: float = Field(ge=0.0, le=1.0)
    bull_strength: float = Field(ge=0.0, le=1.0)
    bear_strength: float = Field(ge=0.0, le=1.0)
    debate_divergence: float = Field(
        description="MOMENTUM_LOGIC.md §10"
    )
    bull_argument: str = ""
    bear_argument: str = ""
    judge_reasoning: str = ""
    position_size: PositionSize = "NONE"
    entry_price: float | None = None
    stop_loss: float | None = None
    target_prices: list[float] = Field(default_factory=list)
    time_horizon: TimeHorizon = "INTRADAY"

    @field_validator("verdict", mode="before")
    @classmethod
    def _sanitize_verdict(cls, v: Any) -> str:
        return _sanitize_literal(v, get_args(TradeAction), "NO_TRADE")

    @field_validator("position_size", mode="before")
    @classmethod
    def _sanitize_pos_size(cls, v: Any) -> str:
        return _sanitize_literal(v, get_args(PositionSize), "NONE")

    @field_validator("time_horizon", mode="before")
    @classmethod
    def _sanitize_time_horizon(cls, v: Any) -> str:
        return _sanitize_literal(v, get_args(TimeHorizon), "INTRADAY")


# ─── Catalyst Profile (D118) ────────────────────────────────────────

CatalystDurability = Literal[
    "PERMANENT_REVALUATION",  # FDA, M&A — hours to days
    "MULTI_HOUR",             # Earnings beat, major partnership — 1-4 hours
    "SHORT_LIVED",            # Technical breakout, moderate news — 15-60 min
    "EPHEMERAL",              # Social media pump, no real catalyst — 5-15 min
]


class CatalystProfile(BaseModel, frozen=True):
    """
    D118: LLM-generated catalyst assessment that parameterizes the position lifecycle.

    One LLM call at entry time produces this profile, which then feeds into
    CatalystHalfLife, GratitudeExit, VelocityEngine, and AlphaDecayOracle —
    all deterministic exit strategies that run in microseconds during Phase 3.

    Ref: D118 (Entry Catalyst Profiler)
    """

    ticker: str
    catalyst_type: str = "unknown"
    durability: CatalystDurability = "SHORT_LIVED"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reasoning: str = ""

    # Downstream parameter recommendations
    recommended_half_life_minutes: int = Field(
        default=20,
        description="Feeds CatalystHalfLife strategy. FDA=180, earnings=120, "
        "technical=25, social_pump=8, unknown=20.",
    )
    recommended_atr_multiplier: float = Field(
        default=2.0,
        description="Feeds stop computation. PERMANENT=3.0x (wide, survives noise), "
        "EPHEMERAL=1.5x (tight, grab and go).",
    )
    recommended_risk_scale: float = Field(
        default=1.0,
        ge=0.25, le=2.0,
        description="Multiplier on base risk. Strong catalyst=1.5x, weak=0.5x.",
    )
    recommended_gratitude_decay: float = Field(
        default=0.05,
        description="R/min decay rate for GratitudeExit. PERMANENT=0.02 (hold longer), "
        "EPHEMERAL=0.10 (grab and go).",
    )

    @field_validator("durability", mode="before")
    @classmethod
    def _sanitize_durability(cls, v: Any) -> str:
        return _sanitize_literal(
            v, get_args(CatalystDurability), "SHORT_LIVED"
        )


# ─── Final Trade Verdict ─────────────────────────────────────────────

class TradeVerdict(BaseModel, frozen=True):
    """
    The final, fully-vetted trading decision ready for execution.
    Produced after debate + risk review.
    """

    ticker: str
    action: TradeAction
    confidence: float = Field(ge=0.0, le=1.0)
    mfcs: float
    debate_result: DebateResult | None = None
    risk_signal: RiskSignal | None = None
    entry_price: float
    stop_loss: float
    target_prices: list[float]
    position_size_pct: float = Field(
        ge=0.0, le=0.50,
        description="D115: Raised from 0.25 to 0.50 for Kelly Tier 3 (35%) and Tier 4 (40%). "
        "Actual capping happens in KellyTierConfig, not Pydantic validation. "
        "Original MOMENTUM_LOGIC.md §6 cap was 5%.",
    )
    kelly_tier: int = Field(
        default=1,
        description="D115: Kelly conviction tier (1-4). Tier 1=standard 1% risk, "
        "Tier 4=statistical outlier 5% risk.",
    )
    risk_per_trade_pct: float = Field(
        default=0.01,
        description="D115: Per-trade risk percentage. Overrides global config "
        "when Kelly tier system is active.",
    )
    time_horizon: TimeHorizon = "INTRADAY"
    reasoning_summary: str = ""
    catalyst_profile: CatalystProfile | None = Field(
        default=None,
        description="D118: LLM catalyst assessment. Parameterizes exit strategies "
        "(half-life, stop width, gratitude decay) for this position's lifecycle.",
    )
    # D150: Enrichment data for tier-based aggressive sizing
    float_shares: int | None = Field(
        default=None,
        description="D150: Free float from CandidateStock. Used by executor for "
        "tier assignment (Tier 1: <5M, Tier 2: <20M, Tier 3: rest).",
    )
    gap_pct: float = Field(
        default=0.0,
        description="D150: Gap percentage from CandidateStock. Tier 1 requires >=20%.",
    )
    rvol: float = Field(
        default=0.0,
        description="D150: Relative volume from CandidateStock. Tier 1 requires >=5x.",
    )
    direction: str = Field(
        default="long",
        description="D161: Trade direction — 'long' (standard buy) or 'short' (sell-short fader). "
        "Set to 'short' by the faller gate when score > short_selling.min_faller_score and "
        "shortability/liquidity requirements pass.",
    )
    # D310.T2 (2026-05-24, doc 171): A/B execution arm for the
    # stop-widening rollout. Defaults preserve pre-T2 behavior:
    # tight_stop = existing OTO submission with D142 Phase 1 1.5%
    # override; wide_stop = standalone STOP submission with ATR-based
    # stop, halved position size to bound risk. Arm is assigned at
    # orchestrator verdict-emission time via a deterministic hash of
    # (trading_date, symbol) so the same name picked twice on the
    # same day gets the same arm. Only fires when env var
    # MOMENTUM_T2_ENABLED=1 -- otherwise every verdict is tight_stop.
    execution_arm: str = Field(
        default="tight_stop",
        description="D310.T2: 'wide_stop' (ATR, halved qty, standalone) "
        "or 'tight_stop' (existing OTO + D142 Phase 1 1.5%).",
    )
    qty_multiplier: float = Field(
        default=1.0, ge=0.0, le=1.0,
        description="D310.T2: applied at executor sizing. wide_stop arm "
        "uses 0.5 to halve dollar risk (compensates for the 3-25x "
        "wider stop). tight_stop arm uses 1.0 (unchanged).",
    )
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("action", mode="before")
    @classmethod
    def _sanitize_action(cls, v: Any) -> str:
        return _sanitize_literal(v, get_args(TradeAction), "NO_TRADE")

    @field_validator("time_horizon", mode="before")
    @classmethod
    def _sanitize_time_horizon(cls, v: Any) -> str:
        return _sanitize_literal(v, get_args(TimeHorizon), "INTRADAY")


# ─── Arena Tracking ──────────────────────────────────────────────────

class ArenaOutcome(BaseModel):
    """Tracks prompt variant performance for Elo scoring."""

    prompt_variant_id: str
    model_id: str
    ticker: str
    timestamp: datetime
    predicted_signal: SignalDirection
    predicted_confidence: float
    actual_return_pct: float | None = None  # Filled post-trade
    hit_target: bool | None = None  # Did it reach +20%?
    max_drawdown_pct: float | None = None
