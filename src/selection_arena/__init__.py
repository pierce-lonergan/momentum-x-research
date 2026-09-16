"""
Selection Arena — Scanner filter optimization engine.

Companion to src/arena/strategy_arena.py. Where the strategy arena optimizes
HOW trades are executed, the selection arena optimizes WHICH stocks are selected
by the scanner.

Answers: "Given different filter parameters, which combination catches the most
big daily movers while minimizing noise?"

Architecture:
    FilterProfile       — scanner filter parameters (price floor, RVOL min, etc.)
    MoverRecord         — per-ticker per-day data: premarket metrics + outcome
    FilterReplay        — replay engine: apply profile against universe, classify TP/FP/FN/TN
    FilterAttributor    — which specific filter killed each false negative
    BacktestRunner      — multi-day, multi-profile sweep
    Report              — generate findings similar to strategy arena findings

Fidelity note: Results depend on universe quality. If a stock is not in the
universe for a given date, it can't be classified. The more complete the universe
(ideally all stocks that were active that day), the more meaningful the analysis.
"""

from src.selection_arena.models import (
    FilterProfile,
    MoverRecord,
    FilterCheck,
    StockClassification,
    DayAnalysis,
    AggregateStats,
    BacktestResult,
    ShortSimResult,
    ShortBacktestStats,
)
from src.selection_arena.filter_replay import FilterReplay
from src.selection_arena.profiles import PROFILES, get_profile
from src.selection_arena.market_movers import MarketMoversDB
from src.selection_arena.backtest import BacktestRunner
from src.selection_arena.short_analyzer import ShortAnalyzer, ShortAnalyzerConfig

__all__ = [
    "FilterProfile",
    "MoverRecord",
    "FilterCheck",
    "StockClassification",
    "DayAnalysis",
    "AggregateStats",
    "BacktestResult",
    "ShortSimResult",
    "ShortBacktestStats",
    "FilterReplay",
    "PROFILES",
    "get_profile",
    "MarketMoversDB",
    "BacktestRunner",
    "ShortAnalyzer",
    "ShortAnalyzerConfig",
]
