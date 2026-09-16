"""
Selection Arena Data Models

Data contracts for the filter optimization system. Mirrors the immutable-model
pattern from src/core/models.py but for selection-layer concerns.

D161: Extended with short-selling simulation models. The short analysis answers:
  "What would have happened if we shorted the high-faller-score rejects?"
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════════════
# Filter Profile — tunable scanner parameters
# ═══════════════════════════════════════════════════════════════════

@dataclass
class FilterProfile:
    """Complete set of scanner filter parameters.

    Each field maps directly to a gate in src/scanners/premarket.py.
    None means "disabled" — the filter is not applied.

    The 'current' profile mirrors live ScannerThresholds defaults.
    """
    name: str
    description: str = ""

    # ── Price gates ──
    price_floor: float = 3.00           # Minimum price (standard path)
    price_floor_high_vol: float = 0.50  # Lower price floor when high-vol conditions met
    price_ceiling: float = 999.0        # Maximum price (no ceiling by default)

    # ── Volume gates ──
    rvol_min: float = 2.0               # Minimum relative volume
    absolute_volume_min: int = 500_000  # RVOL alternative: raw premarket volume

    # ── Gap gate ──
    gap_pct_min: float = 0.05           # Minimum gap % (5%)

    # ── Dollar volume gate ──
    dollar_volume_min: float = 5_000_000  # Minimum dollar volume (prev day ADV × price)

    # ── Override thresholds ──
    # When RVOL >= extreme_rvol_override: bypass price_floor and dollar_volume_min
    extreme_rvol_override: float = 30.0
    # When dollar_volume > price_override_dollar_vol AND rvol > price_override_rvol:
    # use price_floor_high_vol instead of price_floor
    price_override_dollar_vol: float = 2_000_000
    price_override_rvol: float = 5.0

    # ── Spread gate (optional) ──
    max_spread_pct: float | None = None  # Max bid-ask spread as % of price

    # ── News/catalyst gate (optional) ──
    require_news: bool = False           # If True, only pass stocks with confirmed news

    # ── D160: Mega dollar volume override threshold ──
    # If dollar_volume > this, all price floor checks are bypassed.
    # Mirrors ScannerThresholds.price_override_mega_dollar_vol.
    price_override_mega_dollar_vol: float = 10_000_000

    # ── D160: Faller detection threshold (post-scanner gate) ──
    # Candidates with faller_score > this are rejected before order submission.
    # Used in arena analysis to model how the faller gate would affect TP/FP/FN/TN.
    # None = faller detection disabled for this profile.
    faller_score_threshold: float | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "price_floor": self.price_floor,
            "price_floor_high_vol": self.price_floor_high_vol,
            "price_ceiling": self.price_ceiling,
            "rvol_min": self.rvol_min,
            "absolute_volume_min": self.absolute_volume_min,
            "gap_pct_min": self.gap_pct_min,
            "dollar_volume_min": self.dollar_volume_min,
            "extreme_rvol_override": self.extreme_rvol_override,
            "price_override_dollar_vol": self.price_override_dollar_vol,
            "price_override_rvol": self.price_override_rvol,
            "price_override_mega_dollar_vol": self.price_override_mega_dollar_vol,
            "max_spread_pct": self.max_spread_pct,
            "require_news": self.require_news,
            "faller_score_threshold": self.faller_score_threshold,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FilterProfile":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ═══════════════════════════════════════════════════════════════════
# Mover Record — per-ticker per-day data
# ═══════════════════════════════════════════════════════════════════

DataConfidence = Literal["confirmed", "confirmed_partial", "partial", "estimated", "unknown"]
# D162: Added "confirmed_partial" and "partial" — Pydantic was silently skipping
# all Mar 31 stocks because these values caused validation errors at model instantiation.


class MoverRecord(BaseModel, frozen=True):
    """A stock on a given trading day — scanner inputs + known outcome.

    Fields come from two sources:
    1. Pre-market / open metrics: what the scanner would have seen (RVOL, gap%, etc.)
    2. Outcome metrics: what actually happened (max gain, close, etc.)

    Fields marked with data_confidence="estimated" should be treated as
    approximate. Fields with None are unknown.
    """

    ticker: str
    date: str  # "YYYY-MM-DD"

    # ── Pre-market / open metrics (scanner inputs) ──
    open_price: float | None = None
    previous_close: float | None = None

    # gap_pct = (open_price - previous_close) / previous_close
    gap_pct: float | None = None

    # RVOL computed at or before 9:30 AM from premarket volume
    rvol_at_open: float | None = None

    # Total premarket volume (shares traded before 9:30 AM)
    premarket_volume: int | None = None

    # Previous day's full-session volume (ADV proxy for dollar volume calculation)
    prev_volume: int | None = None

    # Dollar volume = prev_volume × open_price (liquidity proxy)
    dollar_volume: float | None = None

    # Whether a news catalyst was identified premarket
    has_news: bool = False

    # Fundamental metadata (optional)
    float_shares: int | None = None
    market_cap: float | None = None

    # ── Outcome metrics (ground truth) ──
    high: float | None = None            # Day high
    low: float | None = None             # Day low
    close: float | None = None           # Day close

    # Max intraday gain from open: (high - open) / open
    # This is what the selection arena optimizes for: catching stocks that run
    max_gain_from_open: float | None = Field(
        default=None,
        description="(day_high - open_price) / open_price. "
        "Primary outcome metric for big-mover classification.",
    )

    # ── Scanner metadata ──
    # Did the LIVE production scanner include this stock in its candidates?
    scanner_found: bool = False

    # Journal action if evaluated by the full pipeline (BUY / NO_TRADE / None)
    journal_action: str | None = None

    # MFCS if evaluated (for understanding pipeline-level filtering beyond scanner)
    journal_mfcs: float | None = None

    # ── Data quality ──
    data_confidence: DataConfidence = "confirmed"

    # Human notes on data quality or stock context
    notes: str = ""


# ═══════════════════════════════════════════════════════════════════
# Filter Check — result of one filter gate for one stock
# ═══════════════════════════════════════════════════════════════════

@dataclass
class FilterCheck:
    """Result of applying one filter gate to a stock."""
    filter_name: str         # e.g., "price_floor", "rvol_min"
    passed: bool             # Did the stock pass this filter?
    actual_value: float | None   # The stock's actual metric value
    threshold: float | None  # The filter's configured threshold
    path: str = "standard"   # Which override path applied: "standard", "high_vol", "extreme_rvol", "absolute_vol"
    reason: str = ""         # Human-readable explanation

    def __str__(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        if self.actual_value is not None and self.threshold is not None:
            return f"{self.filter_name}: {status} ({self.actual_value:.3g} vs threshold {self.threshold:.3g})"
        return f"{self.filter_name}: {status} (insufficient data)"


# ═══════════════════════════════════════════════════════════════════
# Stock Classification — TP / FP / FN / TN
# ═══════════════════════════════════════════════════════════════════

Category = Literal["TP", "FP", "FN", "TN", "UNKNOWN"]


@dataclass
class StockClassification:
    """Classification of one stock under one filter profile for one day.

    TP (True Positive):  Scanner selected it AND it ran 20%+. Money captured.
    FP (False Positive): Scanner selected it but it didn't run. Noise.
    FN (False Negative): It ran 20%+ but scanner missed it. Money left on table.
    TN (True Negative):  Scanner correctly filtered it (didn't run).
    UNKNOWN:             Outcome not known (incomplete data).
    """
    ticker: str
    date: str
    profile_name: str
    category: Category
    filter_passed: bool       # Did ALL filters pass?
    is_big_mover: bool        # Did it actually run 20%+?
    max_gain: float | None    # Actual max intraday gain from open
    filter_checks: list[FilterCheck] = field(default_factory=list)
    killing_filters: list[str] = field(default_factory=list)  # Filters that caused FN

    @property
    def all_passed(self) -> bool:
        """True if all filter checks passed (no data-missing checks count as failures)."""
        return self.filter_passed

    @property
    def killing_filter_summary(self) -> str:
        """Human-readable summary of why this stock was missed (FN only)."""
        if self.category != "FN":
            return ""
        if not self.killing_filters:
            return "unknown (no filter data)"
        return " + ".join(self.killing_filters)


# ═══════════════════════════════════════════════════════════════════
# Day Analysis — one profile × one day
# ═══════════════════════════════════════════════════════════════════

@dataclass
class DayAnalysis:
    """Full analysis of one filter profile against one day's universe.

    The primary metrics are capture_rate and precision:
    - capture_rate = TP / (TP + FN) = "how much of the daily opportunity did we see?"
    - precision    = TP / (TP + FP) = "how much of what we looked at was real?"
    """
    date: str
    profile_name: str
    universe_size: int  # Total stocks in universe (TP + FP + FN + TN)
    big_mover_threshold: float  # The gain threshold used (default 0.20 = 20%)

    # Classification sets (ticker lists)
    true_positives: list[str] = field(default_factory=list)    # Caught + ran
    false_negatives: list[str] = field(default_factory=list)   # Ran but missed
    false_positives: list[str] = field(default_factory=list)   # Caught but didn't run
    true_negatives: list[str] = field(default_factory=list)    # Correctly filtered

    # Attribution: ticker → list of killing filters (for false negatives)
    fn_attribution: dict[str, list[str]] = field(default_factory=dict)

    # Gains for context: ticker → max_gain_from_open
    fn_gains: dict[str, float] = field(default_factory=dict)   # FN gains (missed money)
    tp_gains: dict[str, float] = field(default_factory=dict)   # TP gains (captured)
    fp_gains: dict[str, float] = field(default_factory=dict)   # FP gains (noise)

    # Per-ticker full classifications (for drill-down)
    classifications: list[StockClassification] = field(default_factory=list)

    @property
    def n_winners(self) -> int:
        return len(self.true_positives) + len(self.false_negatives)

    @property
    def n_selected(self) -> int:
        return len(self.true_positives) + len(self.false_positives)

    @property
    def capture_rate(self) -> float:
        """Recall: fraction of big movers that the scanner caught."""
        if self.n_winners == 0:
            return 1.0  # No winners on this day — vacuously perfect
        return len(self.true_positives) / self.n_winners

    @property
    def precision(self) -> float:
        """Precision: fraction of scanner picks that were big movers."""
        if self.n_selected == 0:
            return 0.0  # Nothing selected — precision undefined, treat as 0
        return len(self.true_positives) / self.n_selected

    def combined_score(self, beta: float = 2.0) -> float:
        """F-beta score weighting recall (capture) more than precision.

        beta=2.0 (default): Capture rate weighted 4× over precision.
        beta=1.0: Standard F1 (harmonic mean).
        beta=0.5: Precision-weighted.

        Rationale: Missing a big mover (FN) is worse than picking a dud (FP),
        because FPs just waste evaluation time, while FNs are lost opportunity.
        """
        p = self.precision
        r = self.capture_rate
        if p + r == 0:
            return 0.0
        return (1 + beta ** 2) * p * r / (beta ** 2 * p + r)

    def to_dict(self) -> dict:
        return {
            "date": self.date,
            "profile_name": self.profile_name,
            "universe_size": self.universe_size,
            "big_mover_threshold": self.big_mover_threshold,
            "true_positives": self.true_positives,
            "false_negatives": self.false_negatives,
            "false_positives": self.false_positives,
            "true_negatives": self.true_negatives,
            "fn_attribution": self.fn_attribution,
            "fn_gains": self.fn_gains,
            "tp_gains": self.tp_gains,
            "fp_gains": self.fp_gains,
            "capture_rate": round(self.capture_rate, 4),
            "precision": round(self.precision, 4),
            "combined_score": round(self.combined_score(), 4),
            "n_winners": self.n_winners,
            "n_selected": self.n_selected,
        }


# ═══════════════════════════════════════════════════════════════════
# Aggregate Stats — multi-day summary per profile
# ═══════════════════════════════════════════════════════════════════

@dataclass
class AggregateStats:
    """Multi-day aggregate for one filter profile."""
    profile_name: str
    n_days: int
    avg_capture_rate: float
    avg_precision: float
    avg_combined_score: float
    avg_candidates_per_day: float  # Avg (TP + FP) per day — scanner workload proxy
    total_winners_caught: int
    total_winners_missed: int
    total_selected: int  # Total candidates across all days

    # Filter-by-filter attribution: filter_name → count of FNs where this filter killed
    filter_kill_counts: dict[str, int] = field(default_factory=dict)

    # Weighted attribution: what % of FNs does each filter explain?
    filter_kill_pct: dict[str, float] = field(default_factory=dict)

    # Biggest missed movers (ticker, date, gain) for qualitative review
    biggest_misses: list[dict] = field(default_factory=list)

    # Per-day breakdown
    daily_capture_rates: dict[str, float] = field(default_factory=dict)
    daily_precisions: dict[str, float] = field(default_factory=dict)

    @property
    def overall_capture_rate(self) -> float:
        total = self.total_winners_caught + self.total_winners_missed
        if total == 0:
            return 1.0
        return self.total_winners_caught / total

    @property
    def overall_precision(self) -> float:
        if self.total_selected == 0:
            return 0.0
        return self.total_winners_caught / self.total_selected

    def to_dict(self) -> dict:
        return {
            "profile_name": self.profile_name,
            "n_days": self.n_days,
            "avg_capture_rate": round(self.avg_capture_rate, 4),
            "avg_precision": round(self.avg_precision, 4),
            "avg_combined_score": round(self.avg_combined_score, 4),
            "avg_candidates_per_day": round(self.avg_candidates_per_day, 1),
            "total_winners_caught": self.total_winners_caught,
            "total_winners_missed": self.total_winners_missed,
            "total_selected": self.total_selected,
            "overall_capture_rate": round(self.overall_capture_rate, 4),
            "overall_precision": round(self.overall_precision, 4),
            "filter_kill_counts": self.filter_kill_counts,
            "filter_kill_pct": {k: round(v, 3) for k, v in self.filter_kill_pct.items()},
            "biggest_misses": self.biggest_misses[:10],
            "daily_capture_rates": {k: round(v, 4) for k, v in self.daily_capture_rates.items()},
            "daily_precisions": {k: round(v, 4) for k, v in self.daily_precisions.items()},
        }


# ═══════════════════════════════════════════════════════════════════
# Backtest Result — full sweep output
# ═══════════════════════════════════════════════════════════════════

@dataclass
class BacktestResult:
    """Complete output of a multi-day, multi-profile selection arena backtest."""
    profile_names: list[str]
    dates: list[str]
    big_mover_threshold: float  # The gain threshold used across all days
    beta: float                  # F-beta weight used for combined score

    # Per-day results: date → list of DayAnalysis (one per profile)
    per_day: dict[str, list[DayAnalysis]] = field(default_factory=dict)

    # Per-profile aggregates
    aggregate: dict[str, AggregateStats] = field(default_factory=dict)

    # Ranked profiles by combined score (best first)
    rankings: list[tuple[str, float]] = field(default_factory=list)  # (profile_name, score)

    # Human-readable recommendations
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "profile_names": self.profile_names,
            "dates": self.dates,
            "big_mover_threshold": self.big_mover_threshold,
            "beta": self.beta,
            "per_day": {
                date: [da.to_dict() for da in analyses]
                for date, analyses in self.per_day.items()
            },
            "aggregate": {
                name: stats.to_dict()
                for name, stats in self.aggregate.items()
            },
            "rankings": self.rankings,
            "recommendations": self.recommendations,
        }


# ═══════════════════════════════════════════════════════════════════
# D161: Short Selling Simulation Models
# ═══════════════════════════════════════════════════════════════════

@dataclass
class ShortSimResult:
    """
    Result of simulating a short trade on a high-faller-score stock.

    Models what would have happened if D161 had shorted this stock instead
    of rejecting it. Uses realistic parameters from ShortSellingConfig:
      - Entry at open_price (market open price after gap-up)
      - Stop: entry × (1 + stop_pct)  — e.g. +35% above entry
      - Targets: entry × (1 - t)      — e.g. -3%, -6%, -10% below entry

    Adverse excursion = how far above entry the price went (against the short).
    Favorable excursion = how far below entry the price went (with the short).
    """
    ticker: str
    date: str
    entry_price: float          # Open price — where short entry fires
    stop_price: float           # Buy-stop above entry (e.g. entry × 1.35)
    target_prices: list[float]  # Limit buy targets below entry (T1, T2, T3)
    close_price: float          # Day close — exit price if held to EOD
    day_high: float             # Day high — used for adverse excursion / stop hit
    day_low: float              # Day low — used for favorable excursion / target hit
    gap_pct: float              # Gap % at open (for filter qualification check)
    rvol: float                 # RVOL at open (for filter qualification check)
    dollar_volume: float        # Dollar volume (for filter qualification check)

    # D161 qualification filters
    passed_dolvol_filter: bool  # dollar_volume >= min_dollar_volume_short ($500K)
    passed_rvol_filter: bool    # rvol >= rvol_min_short (3.0x)
    passed_gap_filter: bool     # gap_pct >= min_gap_pct_short (20%)
    would_have_shorted: bool    # All three filters passed AND stock shortable (assumed)

    # Simulated outcome
    hit_stop: bool              # day_high >= stop_price (short stopped out)
    hit_t1: bool                # day_low <= target_prices[0]
    hit_t2: bool                # day_low <= target_prices[1]
    hit_t3: bool                # day_low <= target_prices[2]

    # P&L metrics (per share, assuming EOD exit if no stop/target)
    # For shorts: profit = entry - exit (price going DOWN = gain)
    exit_price: float           # Actual exit (stop, first target, or close)
    short_pnl_pct: float        # (entry - exit_price) / entry
    max_adverse_excursion_pct: float  # (day_high - entry) / entry — worst case against short
    max_favorable_excursion_pct: float  # (entry - day_low) / entry — best case for short

    # Context
    notes: str = ""

    @property
    def pnl_at_close(self) -> float:
        """P&L per share if held to EOD close."""
        return self.entry_price - self.close_price

    @property
    def pnl_at_close_pct(self) -> float:
        """% P&L if held to EOD close."""
        if self.entry_price == 0:
            return 0.0
        return (self.entry_price - self.close_price) / self.entry_price

    @property
    def is_win(self) -> bool:
        """True if the simulated short was profitable (price dropped)."""
        return self.short_pnl_pct > 0

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "date": self.date,
            "entry_price": round(self.entry_price, 4),
            "stop_price": round(self.stop_price, 4),
            "target_prices": [round(t, 4) for t in self.target_prices],
            "close_price": round(self.close_price, 4),
            "day_high": round(self.day_high, 4),
            "day_low": round(self.day_low, 4),
            "gap_pct": round(self.gap_pct or 0, 4),
            "rvol": round(self.rvol or 0, 2),
            "dollar_volume": round(self.dollar_volume or 0),
            "passed_dolvol_filter": self.passed_dolvol_filter,
            "passed_rvol_filter": self.passed_rvol_filter,
            "passed_gap_filter": self.passed_gap_filter,
            "would_have_shorted": self.would_have_shorted,
            "hit_stop": self.hit_stop,
            "hit_t1": self.hit_t1,
            "hit_t2": self.hit_t2,
            "hit_t3": self.hit_t3,
            "exit_price": round(self.exit_price, 4),
            "short_pnl_pct": round(self.short_pnl_pct, 4),
            "pnl_at_close_pct": round(self.pnl_at_close_pct, 4),
            "max_adverse_excursion_pct": round(self.max_adverse_excursion_pct, 4),
            "max_favorable_excursion_pct": round(self.max_favorable_excursion_pct, 4),
            "is_win": self.is_win,
            "notes": self.notes,
        }


@dataclass
class ShortBacktestStats:
    """
    Aggregate statistics across all simulated short trades.

    Answers: "If D161 had been live on these dates, how would the short
    book have performed?"
    """
    n_candidates: int        # Total stocks that were high-faller-score rejects
    n_qualified: int         # Passed all D161 qualification filters
    n_would_short: int       # would_have_shorted = True

    # Win/loss breakdown
    n_wins: int              # Profitable shorts (price went down)
    n_losses: int            # Losing shorts (price went up or stopped out)
    n_stops: int             # Hit buy-stop
    n_t1_hits: int           # Hit T1 target (-3%)
    n_t2_hits: int           # Hit T2 target (-6%)
    n_t3_hits: int           # Hit T3 target (-10%)

    # P&L metrics (per-share, not dollar-weighted)
    avg_pnl_pct: float       # Average (entry - exit) / entry across all shorts
    avg_pnl_at_close_pct: float  # Average EOD P&L % across all shorts
    median_pnl_pct: float
    total_pnl_pct: float     # Sum of pnl_pct (proportional to equal-dollar sizing)
    avg_max_adverse_excursion_pct: float  # Avg worst-case upside against short
    avg_max_favorable_excursion_pct: float  # Avg best-case downside for short

    # Per-ticker detail (sorted by pnl descending)
    results: list[ShortSimResult] = field(default_factory=list)

    # Notable examples
    biggest_winner: ShortSimResult | None = None   # Best short
    biggest_loser: ShortSimResult | None = None    # Worst short
    biggest_stop_out: ShortSimResult | None = None # Worst adverse excursion

    @property
    def win_rate(self) -> float:
        """Win rate among qualified short candidates."""
        if self.n_would_short == 0:
            return 0.0
        return self.n_wins / self.n_would_short

    @property
    def stop_rate(self) -> float:
        """Rate of stop-outs among qualified short candidates."""
        if self.n_would_short == 0:
            return 0.0
        return self.n_stops / self.n_would_short

    def to_dict(self) -> dict:
        return {
            "n_candidates": self.n_candidates,
            "n_qualified": self.n_qualified,
            "n_would_short": self.n_would_short,
            "win_rate": round(self.win_rate, 3),
            "stop_rate": round(self.stop_rate, 3),
            "n_wins": self.n_wins,
            "n_losses": self.n_losses,
            "n_stops": self.n_stops,
            "n_t1_hits": self.n_t1_hits,
            "n_t2_hits": self.n_t2_hits,
            "n_t3_hits": self.n_t3_hits,
            "avg_pnl_pct": round(self.avg_pnl_pct, 4),
            "avg_pnl_at_close_pct": round(self.avg_pnl_at_close_pct, 4),
            "median_pnl_pct": round(self.median_pnl_pct, 4),
            "total_pnl_pct": round(self.total_pnl_pct, 4),
            "avg_max_adverse_excursion_pct": round(self.avg_max_adverse_excursion_pct, 4),
            "avg_max_favorable_excursion_pct": round(self.avg_max_favorable_excursion_pct, 4),
            "results": [r.to_dict() for r in self.results],
            "biggest_winner": self.biggest_winner.to_dict() if self.biggest_winner else None,
            "biggest_loser": self.biggest_loser.to_dict() if self.biggest_loser else None,
            "biggest_stop_out": self.biggest_stop_out.to_dict() if self.biggest_stop_out else None,
        }
