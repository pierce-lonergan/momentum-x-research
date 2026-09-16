"""Phase 0 instrumentation schemas — Pydantic models for the four MVP rows.

Per `docs/research-log/21_phase0_instrumentation_mvp.md` §1-4. Every row carries
a `schema_version` field so a future migration can detect mixed-version
corpora without breaking existing readers.

All models are `frozen=True` so a row, once constructed, is immutable. This
matches the D219 discipline — instrumentation rows are facts about a moment
in time, never mutated post-construction. Mutating an instrumentation row
would falsify the data; the type system prevents it at compile time.

Schema version policy:
  v1 (this commit) — initial MVP. Bumps require a `__migrations__` map
                     in this module + a writer-side dispatcher.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


SCHEMA_VERSION: int = 1
"""Bumped when any schema in this module changes shape. Single source of
truth — every emitted row carries this value."""


# ── 1. trade_context — one row per order ──────────────────────────────


class TradeContextRow(BaseModel):
    """Per-order NBBO + state at submit / first-fill / terminal-fill.

    Source: `21_phase0_instrumentation_mvp.md` §1. 16 columns.

    Hook sites (deferred to follow-up commit):
      - `src/execution/alpaca_executor.py` `submit_oto_order` (write submit row)
      - `src/execution/bridge.py` `_poll_for_terminal_fill` (update terminal cols)

    For MVP: first-fill = terminal-fill on instant fills, NULL otherwise.
    Real per-fill capture deferred until WebSocket trade-updates subscription.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = SCHEMA_VERSION

    order_id: str = Field(..., min_length=1)
    ticker: str = Field(..., min_length=1)
    side: Literal["buy", "sell"]
    requested_qty: int = Field(..., gt=0)
    requested_px: float = Field(..., ge=0.0)
    submit_ts: datetime
    submit_nbbo_bid: float | None = None
    submit_nbbo_ask: float | None = None
    first_fill_ts: datetime | None = None
    first_fill_nbbo_bid: float | None = None
    first_fill_nbbo_ask: float | None = None
    terminal_ts: datetime | None = None
    terminal_nbbo_bid: float | None = None
    terminal_nbbo_ask: float | None = None
    terminal_status: Literal[
        "filled", "partially_filled", "canceled", "rejected", "expired", "pending",
    ] = "pending"
    terminal_filled_qty: int = Field(..., ge=0)


# ── 2. bar_context — one row per entry bar ────────────────────────────


class BarContextRow(BaseModel):
    """Per-position OHLCV of the 1-min bar containing the entry, plus
    our share of that bar's volume (the q/v_τ denominator).

    Source: `21_phase0_instrumentation_mvp.md` §2. 12 columns.

    Hook site (deferred): `bridge.execute_verdict()` after `add_position`.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = SCHEMA_VERSION

    position_id: str = Field(..., min_length=1)
    ticker: str = Field(..., min_length=1)
    entry_ts: datetime
    entry_bar_open_ts: datetime
    entry_bar_open: float = Field(..., gt=0.0)
    entry_bar_high: float = Field(..., gt=0.0)
    entry_bar_low: float = Field(..., gt=0.0)
    entry_bar_close: float = Field(..., gt=0.0)
    entry_bar_volume: int = Field(..., ge=0)  # 0 allowed for missing data
    our_q_shares: int = Field(..., ge=0)
    q_over_v_tau: float = Field(..., ge=0.0)
    bar_data_quality: Literal["complete", "partial", "missing"]


# ── 3. child_fill_ticks — one row per partial fill ────────────────────


class ChildFillRow(BaseModel):
    """Per-child-fill capture for multi-leg fills (e.g., AGPU 505 then 341).

    Source: `21_phase0_instrumentation_mvp.md` §3. 8 columns.

    Hook site (deferred): `bridge._poll_for_terminal_fill` success path,
    via `client.get_orders(order_id=...).legs`. Per MVP decision: REST-
    derived only; `venue` and `nbbo_*_at_fill` NULL until WebSocket
    NBBO subscription ships (v2.1).
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = SCHEMA_VERSION

    parent_order_id: str = Field(..., min_length=1)
    child_fill_ts: datetime
    qty: int = Field(..., gt=0)
    price: float = Field(..., gt=0.0)
    venue: str | None = None
    cumulative_filled_qty: int = Field(..., ge=0)
    nbbo_bid_at_fill: float | None = None
    nbbo_ask_at_fill: float | None = None


# ── 4. cohort_registry — one row per cohort match ─────────────────────


class CohortRow(BaseModel):
    """Per-cohort-match row for matched-cohort return computation.

    Source: `21_phase0_instrumentation_mvp.md` §4. 10 columns.

    Hook site (deferred): new module called from `bridge.execute_verdict()`.
    Cohort matching algorithm itself is a v2 implementation detail per spec;
    the SCHEMA is what this MVP must ship.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = SCHEMA_VERSION

    cohort_id: str = Field(..., min_length=1)  # UUID as str (Parquet-friendly)
    traded_ticker: str = Field(..., min_length=1)
    cohort_ticker: str = Field(..., min_length=1)
    match_dt: datetime
    match_features: dict = Field(default_factory=dict)  # {catalyst_type, market_cap_bucket, gap_pct_bucket, hour_bucket}
    cohort_entry_ref_px: float = Field(..., gt=0.0)
    cohort_eod_px: float | None = None
    cohort_60min_px: float | None = None
    cohort_signed_return_60min: float | None = None
    cohort_data_quality: Literal["complete", "partial", "missing"]


# ── 5. decision_row — Tier 4 #15: one row per orchestrator verdict ────


class DecisionRow(BaseModel):
    """Tier 4 #15 (Bug AO, 2026-04-27): per-orchestrator-verdict capture.

    The MISSING piece for "rigorous answer to does this make money."
    Phase 0 already captures TRADE events (TradeContextRow, ChildFillRow,
    BarContextRow, CohortRow). This schema captures the upstream
    DECISION events — every input that drove the orchestrator to BUY,
    HOLD, or NO_TRADE on a candidate.

    With this in place, counterfactual replay becomes possible:
      "Given today's 2400 captured decisions, if Bug AK's three-tier
       D124 rule had been in place, how many would have flipped from
       NO_TRADE to BUY? What would the synthetic P&L have been?"

    The arena assessment from feedback_arena_assessment.md flagged
    "decision replay covers 1/50 signal types" as the #1 architectural
    gap. This schema closes that gap.

    Hook site: orchestrator's verdict-finalization paths
    (_build_no_trade_verdict, _build_buy_verdict, _build_hold_verdict).

    All fields are typed, validated, and frozen at construction time.
    Schema versioning matches Phase 0's SCHEMA_VERSION constant.
    """
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = SCHEMA_VERSION

    # ── Identity ──
    decision_id: str = Field(..., min_length=1)  # UUID for cross-table join
    timestamp: datetime
    session_date: str = Field(..., min_length=10, max_length=10)  # YYYY-MM-DD
    cycle_number: int = Field(..., ge=0)  # which Phase 2/3 evaluation cycle
    ticker: str = Field(..., min_length=1)

    # ── Candidate snapshot at decision time ──
    candidate_gap_pct: float
    candidate_rvol: float
    candidate_current_price: float
    candidate_float_shares: int | None = None
    candidate_market_cap: float | None = None
    candidate_gap_classification: str = ""
    candidate_has_news_catalyst: bool = False

    # ── Agent signals (full breakdown, JSON-serializable list) ──
    # Each entry: {"agent_id": str, "signal": str, "confidence": float,
    #              "weight": float, "flags": list[str]}
    agent_signals: list[dict] = Field(default_factory=list)
    n_agents_total: int = Field(..., ge=0)
    n_agents_returned: int = Field(..., ge=0)
    n_agents_failed: int = Field(..., ge=0)

    # ── MFCS scoring ──
    mfcs_score: float = Field(..., ge=0.0, le=1.0)
    mfcs_components: dict = Field(default_factory=dict)  # {catalyst_news, technical, ...}

    # ── D124 consensus alignment intermediate state (Bug AK proof case) ──
    d124_bullish_count: int = Field(..., ge=0)
    d124_bearish_count: int = Field(..., ge=0)
    d124_bullish_conf: float = Field(..., ge=0.0)
    d124_bearish_conf: float = Field(..., ge=0.0)
    d124_max_bearish_conf: float = Field(..., ge=0.0)
    d124_was_rejected: bool = False
    d124_rejection_tier: str | None = None  # "A" / "B" / "C" if rejected

    # ── MFCS threshold gate ──
    mfcs_threshold_static: float = Field(..., gt=0.0)
    mfcs_threshold_effective: float = Field(..., gt=0.0)  # post-D216 dynamic reduction
    mfcs_threshold_passed: bool

    # ── Final verdict ──
    verdict_action: Literal["BUY", "STRONG_BUY", "HOLD", "NO_TRADE"]
    verdict_reason: str = ""
    verdict_confidence: float = Field(0.0, ge=0.0, le=1.0)

    # ── Outcome attribution (filled in by post-trade analyzer if trade fired) ──
    led_to_trade: bool = False
    trade_oid: str | None = None
    trade_realized_pnl: float | None = None  # populated when position closes
