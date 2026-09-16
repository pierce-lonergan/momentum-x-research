"""Production Arena — end-to-end backtesting harness.

Runs the REAL production orchestrator against historical scenarios and
captures gate-by-gate verdicts. Used to:

1. Validate that proposed config changes (e.g. D112 threshold tuning) actually
   produce trades against historical data BEFORE shipping to live trading.
2. Counterfactual sweep: "if D112 max_float was X, +N trades, +Y%."
3. Compare arena predictions to live session results (divergence budget).

Designed AFTER the Apr 14-16 zero-trade week revealed that none of the existing
arenas (selection_arena, llm_arena, strategy_arena) test the full gate cascade.

See docs/research-log/02_arena_critique.md for design rationale.
"""

from src.production_arena.types import (
    Scenario,
    ProductionVerdict,
    SweepResult,
    ArenaConfig,
)

__all__ = [
    "Scenario",
    "ProductionVerdict",
    "SweepResult",
    "ArenaConfig",
]
