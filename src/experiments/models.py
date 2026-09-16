"""
MOMENTUM-X Experiment Data Models

### ARCHITECTURAL CONTEXT
Node ID: experiments.models
Graph Link: docs/memory/graph_state.json → "experiments.models"

### DESIGN DECISIONS
- Frozen Pydantic models matching src/core/models.py pattern
- ExperimentConfig/ExperimentVariant define what to test (loaded from YAML)
- VariantResult/ExperimentJournalEntry capture what happened (written to JSONL)
- Overrides use dotted paths ("scoring.mfcs_buy_threshold") for nested config access

Ref: D102 (Experimentation Framework Phase 1)
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ExperimentVariant(BaseModel, frozen=True):
    """
    One parameter variant within an experiment.

    Overrides are dotted paths into the Settings hierarchy:
      "execution.initial_stop_atr_multiplier": 2.5
      "scoring.mfcs_buy_threshold": 0.30
    """

    variant_id: str = Field(description="Unique variant identifier, e.g. 'atr_2.5'")
    overrides: dict[str, Any] = Field(
        description="Dotted-path config overrides, e.g. {'execution.initial_stop_atr_multiplier': 2.5}"
    )


class ExperimentConfig(BaseModel, frozen=True):
    """
    Single experiment definition loaded from YAML.

    experiment_type categories:
      - "parameter": ATR multiplier, stop floor, etc.
      - "threshold": MFCS buy threshold variants
      - "weight": Agent weight allocation variants
      - "lambda": Risk aversion lambda sweep
    """

    experiment_id: str = Field(description="Unique experiment identifier, e.g. 'atr_sweep'")
    experiment_type: str = Field(
        description="Experiment category: 'parameter', 'threshold', 'weight', 'lambda'"
    )
    description: str = Field(description="Human-readable experiment description")
    enabled: bool = Field(default=True, description="Whether this experiment is active")
    variants: list[ExperimentVariant] = Field(
        description="List of parameter variants to test"
    )


class VariantResult(BaseModel, frozen=True):
    """
    Result of replaying one variant for one candidate evaluation.

    Captures what would have happened if this variant's config had been active:
    different MFCS, different entry/skip decision, different stop/sizing.
    """

    experiment_id: str
    variant_id: str
    overrides: dict[str, Any]
    mfcs: float = Field(description="Re-scored MFCS with variant weights/threshold/lambda")
    would_enter: bool = Field(description="Would this variant pass the buy threshold?")
    stop_loss: float | None = Field(
        default=None, description="Variant stop loss (differs for ATR experiments)"
    )
    position_size_qty: int | None = Field(
        default=None, description="Variant position size in shares"
    )
    position_size_pct: float | None = Field(
        default=None, description="Variant position size as % of equity"
    )
    reasoning: str = Field(
        default="", description="Short explanation of difference vs primary"
    )


class ExperimentJournalEntry(BaseModel):
    """
    One evaluation's experiment replay results.

    Written as a single JSONL line to the experiment journal.
    Links to the primary trade journal via trade_id.
    """

    trade_id: str = Field(description="Matches trade_id in primary TradeJournal")
    ticker: str
    timestamp: str = Field(description="ISO-8601 UTC timestamp")
    primary_mfcs: float = Field(description="MFCS from the active/primary configuration")
    primary_action: str = Field(description="Primary verdict: BUY, HOLD, NO_TRADE")
    primary_stop_loss: float | None = Field(
        default=None, description="Primary stop loss (if BUY)"
    )
    variant_results: list[VariantResult] = Field(
        default_factory=list, description="Results from all experiment variants"
    )
