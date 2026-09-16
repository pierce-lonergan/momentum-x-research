"""Shared type contract for the production arena.

This file defines the dataclass shapes used by scenarios.py, verdict.py,
aggregator.py, and pipeline_runner.py. Subagents implementing those modules
MUST read this contract first — divergent field names defeat the purpose of
having an end-to-end arena.

Design principles:
1. **Immutability**: All dataclasses frozen — they cross subprocess boundaries
   in pipeline_runner's process pool fallback and must be hashable / picklable.
2. **No external deps**: stdlib only — no pandas, no pydantic, no httpx. The
   arena is pure compute against historical data on disk.
3. **JSON-serializable**: Each type has to_dict() so verdicts can be appended
   to JSONL output and ingested by aggregator without round-trip ambiguity.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field, fields
from datetime import date, datetime
from typing import Any, Literal


# ── Scenario (input to the pipeline) ────────────────────────────────────


@dataclass(frozen=True)
class MinuteBar:
    """One minute OHLCV bar from data/bar_recordings/<date>/<ticker>.json."""
    timestamp: str           # ISO 8601 UTC, e.g. "2026-04-16T13:30:00Z"
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: float | None = None


@dataclass(frozen=True)
class LabeledOutcome:
    """Multi-horizon outcome labels from data/backfill/features_labeled.jsonl.

    Returns are decimal (e.g. 0.052 = +5.2%). All optional — historical
    candidates without bar coverage have None for all fields.
    """
    close_return: float | None = None       # session close vs entry
    mfe_pct: float | None = None            # max favorable excursion
    mae_pct: float | None = None            # max adverse excursion
    time_to_mfe_min: int | None = None      # minutes from entry to MFE
    win_t1: bool | None = None
    win_t5: bool | None = None
    win_t15: bool | None = None
    win_t30: bool | None = None
    win_t60: bool | None = None
    win_close: bool | None = None
    entry_price: float | None = None        # 9:31 open used for return calc
    orb_high: float | None = None           # 9:30-9:35 5min high
    orb_low: float | None = None
    orb_broken: bool | None = None          # did price exceed orb_high after t+5?


@dataclass(frozen=True)
class Scenario:
    """One historical pre-market candidate + its minute bars + labeled outcome.

    Loaded from data/backfill/candidates.jsonl + data/bar_recordings/
    + data/backfill/features_labeled.jsonl.
    """
    ticker: str
    session_date: str                       # "YYYY-MM-DD"
    premarket_features: dict[str, Any]      # gap_pct, premarket_volume, dolvol,
                                            # price (=open), float_shares, rvol,
                                            # atr, market_cap, prior_close, etc.
    minute_bars: tuple[MinuteBar, ...]      # tuple for hashability; chronological
    labeled_outcome: LabeledOutcome         # may be all-None if unlabeled

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "session_date": self.session_date,
            "premarket_features": self.premarket_features,
            "minute_bars": [dataclasses.asdict(b) for b in self.minute_bars],
            "labeled_outcome": dataclasses.asdict(self.labeled_outcome),
        }


# ── ProductionVerdict (output of one arena run) ─────────────────────────


VerdictDecision = Literal["BUY", "NO_TRADE", "ERROR"]


@dataclass(frozen=True)
class ProductionVerdict:
    """The arena's verdict on a single scenario.

    `gate_rejected` uses 'file:line' format so anyone can jump straight to
    the rejecting code (e.g. 'adaptive_router.py:149').

    `would_be_*` fields are populated only when decision == "BUY" — they
    represent the arena's simulated trade, not a hypothetical.
    """
    ticker: str
    session_date: str
    decision: VerdictDecision
    gate_rejected: str | None = None
    mfcs: float | None = None
    mfcs_components: dict[str, float] | None = None  # {news, technical, risk, ...}
    would_be_entry_price: float | None = None
    would_be_exit_price: float | None = None
    would_be_exit_time: str | None = None  # ISO 8601, "YYYY-MM-DDTHH:MM:SSZ"
    would_be_pnl_pct: float | None = None
    mfe_pct: float | None = None           # from labeled outcome — what we missed
    mae_pct: float | None = None
    orb_confirmed: bool | None = None
    elapsed_ms: float = 0.0
    error_msg: str | None = None
    # Optional shadow-mode fields populated by Phase 5
    composite_score_prescore: float | None = None
    composite_score_full: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def summary(self) -> str:
        """One-line summary for log output."""
        if self.decision == "BUY":
            pnl = self.would_be_pnl_pct
            pnl_s = f"{pnl*100:+.1f}%" if pnl is not None else "n/a"
            return (
                f"{self.session_date} {self.ticker:>6s} BUY "
                f"mfcs={self.mfcs or 0:.3f} pnl={pnl_s} "
                f"mfe={self.mfe_pct*100 if self.mfe_pct else 0:+.1f}%"
            )
        if self.decision == "ERROR":
            return f"{self.session_date} {self.ticker:>6s} ERROR {self.error_msg}"
        return (
            f"{self.session_date} {self.ticker:>6s} NO_TRADE "
            f"mfcs={self.mfcs or 0:.3f} gate={self.gate_rejected or 'unknown'}"
        )


# ── Aggregator outputs ──────────────────────────────────────────────────


@dataclass(frozen=True)
class SweepResult:
    """One row of the counterfactual sweep — the result of running the arena
    against the full date range with a specific config override."""
    param_combo: dict[str, Any]            # e.g. {"max_float": 1_000_000_000, ...}
    n_scenarios: int
    n_trades: int
    n_winners: int
    n_losers: int
    win_rate: float                         # n_winners / n_trades, 0 if n_trades=0
    avg_return_pct: float                   # mean realized PnL across trades
    median_return_pct: float
    max_drawdown_pct: float
    sharpe_annualized: float | None         # None if n_trades < 5
    avg_mfe_captured_pct: float             # how much of MFE we captured on average

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


# ── ArenaConfig (control surface) ───────────────────────────────────────


@dataclass(frozen=True)
class ArenaConfig:
    """Per-run configuration overrides for the arena.

    These map onto config/settings.py paths. `pipeline_runner.run_scenario()`
    applies them transiently for the run, restoring afterwards.
    """
    instant_reject_max_float: int | None = None       # RouterConfig
    instant_reject_min_price: float | None = None     # RouterConfig
    vwap_bias_threshold_pct: float | None = None      # orchestrator (future env-binding)
    mfcs_buy_threshold: float | None = None           # MFCSConfig (currently 'scoring')
    enable_shadow_score: bool = False                 # Phase 5

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def is_default(self) -> bool:
        """True when no overrides — used by aggregator to mark the baseline run."""
        return all(
            getattr(self, f.name) is None or getattr(self, f.name) is False
            for f in fields(self)
        )
