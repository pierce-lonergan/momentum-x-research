"""Inverted-strategy shadow telemetry — paranoid schema for audit trail.

Per Phase 5 calibration: when shadow data comes back tomorrow, we MUST be able
to prove no lookahead crept into the logging. This schema physically separates
decision-time inputs from outcome data; timestamps are validated at write time
to catch ordering bugs early.

Hard invariants enforced by InvertedShadowEntry construction:
  1. decision_timestamp >= 09:35:00 ET on session_date (strict)
  2. orb_break_timestamp < decision_timestamp when both present
  3. simulated_entry_basis is a literal from a fixed enum (no free-form strings)
  4. The "outcome" sub-dict is a separate, optional structure populated post-close

Usage flow:
  Pre-9:36: scenarios/candidates are scanned for ORB breaks of their 5-min
            opening range high. As the break is detected, an `InvertedShadowEntry`
            is constructed (no outcome yet) and logged via ShadowLogger.
  Post-close: a separate batch (`scripts/d220_finalize_inverted_shadow.py`) reads
              the day's shadow file, computes outcomes, and re-emits with outcome.

This file's content is the SCHEMA + the eligibility/timing logic. It does NOT
read minute bars itself — that's the caller's job (orchestrator or batch script).
The split keeps the hot path lean.
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from src.shadow.logger import get_shadow_logger

logger = logging.getLogger(__name__)

# Fixed enum — the only allowed entry-basis strings. New bases require a code
# change AND a schema-version bump, by design.
INVERTED_ENTRY_BASIS = (
    "vwap_9_36_to_9_37",   # the only basis used by the V0 strategy
    "open_9_36",           # alternative — a single-print fill
)

_MARKET_OPEN_ET = time(9, 30, 0)
_DECISION_FLOOR_ET = time(9, 35, 0)  # decisions BEFORE this time are rejected
_NY_TZ = ZoneInfo("America/New_York")
_SCHEMA_VERSION = "v0"


def is_inverted_shadow_enabled() -> bool:
    """Kill switch: SHADOW_INVERTED_ENABLED env var. Default True.

    Independent of SHADOW_SCORING_ENABLED — flip just this one if the inverted
    shadow path throws errors in tomorrow's session.
    """
    val = os.environ.get("SHADOW_INVERTED_ENABLED", "true").strip().lower()
    return val not in ("false", "0", "no", "off")


class _SchemaError(ValueError):
    """Raised when an InvertedShadowEntry violates a paranoid invariant.

    NOT propagated to production — the orchestrator catches and logs.
    """


def _to_iso_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise _SchemaError(f"timestamp must be tz-aware: {dt!r}")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _et_time_of(dt: datetime) -> time:
    return dt.astimezone(_NY_TZ).time()


@dataclass(frozen=True)
class InvertedShadowOutcome:
    """Outcome fields filled post-close by the finalizer batch."""
    filled: bool = False
    session_close_price: float | None = None
    realized_return_pct: float | None = None
    max_favorable_excursion_pct: float | None = None
    max_adverse_excursion_pct: float | None = None


@dataclass(frozen=True)
class InvertedShadowEntry:
    """One inverted-strategy shadow log entry — paranoid schema.

    All decision-time fields are validated at construction. Outcome is optional
    and is filled by a separate post-close pass.
    """
    # Identity
    ticker: str
    session_date: str                        # "YYYY-MM-DD"

    # Production-side context (what cascade did)
    production_decision: str                 # always "NO_TRADE" for inverted
    production_gate_rejected: str | None     # which gate
    production_mfcs: float | None

    # Decision-time inputs
    decision_timestamp: str                  # ISO 8601 UTC; ET time MUST be >= 09:36:00
    orb_high_5min: float                     # the 5-min opening range high
    orb_break_timestamp: str | None          # ISO 8601 UTC; MUST be < decision_timestamp
    orb_broken_by_decision_time: bool

    # Simulated trade specification
    simulated_entry_price: float | None
    simulated_entry_basis: str               # MUST be one of INVERTED_ENTRY_BASIS

    # Decision
    would_have_inverted_bought: bool

    # Optional outcome (populated post-close)
    outcome: InvertedShadowOutcome = field(default_factory=InvertedShadowOutcome)

    # Schema metadata
    schema_version: str = _SCHEMA_VERSION

    def __post_init__(self) -> None:
        # Invariant 1: decision_timestamp ET time >= 09:36:00
        try:
            dt = datetime.fromisoformat(self.decision_timestamp.replace("Z", "+00:00"))
        except ValueError as e:
            raise _SchemaError(f"decision_timestamp not parseable: {e}")
        et = _et_time_of(dt)
        if et < time(9, 36, 0):
            raise _SchemaError(
                f"decision_timestamp ET {et} is before 09:36:00 — "
                f"inverted-strategy decisions require post-9:36 confirmation"
            )

        # Invariant 2: orb_break_timestamp < decision_timestamp
        if self.orb_break_timestamp is not None:
            try:
                obt = datetime.fromisoformat(
                    self.orb_break_timestamp.replace("Z", "+00:00")
                )
            except ValueError as e:
                raise _SchemaError(f"orb_break_timestamp not parseable: {e}")
            if obt >= dt:
                raise _SchemaError(
                    f"orb_break_timestamp {self.orb_break_timestamp} must be "
                    f"STRICTLY before decision_timestamp {self.decision_timestamp}"
                )

        # Invariant 3: simulated_entry_basis in fixed enum
        if self.simulated_entry_basis not in INVERTED_ENTRY_BASIS:
            raise _SchemaError(
                f"simulated_entry_basis {self.simulated_entry_basis!r} not in "
                f"allowed enum {INVERTED_ENTRY_BASIS}"
            )

        # Invariant 4: would_have_inverted_bought consistency
        # If broken_by_decision_time is False, we cannot have decided to buy.
        if self.would_have_inverted_bought and not self.orb_broken_by_decision_time:
            raise _SchemaError(
                "would_have_inverted_bought=True requires "
                "orb_broken_by_decision_time=True"
            )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = "inverted_shadow"
        return d


def log_inverted_shadow_safe(entry_kwargs: dict[str, Any]) -> bool:
    """Build + log an InvertedShadowEntry. Returns True on success.

    NEVER raises into the caller. Schema violations are logged at WARNING and
    the entry is dropped. This is the orchestrator-safe entry point.
    """
    if not is_inverted_shadow_enabled():
        return False
    try:
        entry = InvertedShadowEntry(**entry_kwargs)
        get_shadow_logger().log(entry.to_dict())
        return True
    except _SchemaError as e:
        logger.warning(
            "inverted_shadow: schema violation for %s — entry dropped: %s",
            entry_kwargs.get("ticker"), e,
        )
    except Exception as e:
        logger.warning(
            "inverted_shadow: unexpected error for %s: %s",
            entry_kwargs.get("ticker"), e,
        )
    return False
