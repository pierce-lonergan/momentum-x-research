"""
D87: Feature vector logging for future ML meta-labeling.

Captures a complete snapshot of every candidate evaluation:
  - Scanner features (RVOL, gap%, float, market cap)
  - Agent scores and signals (per-agent direction + confidence)
  - MFCS score and components
  - Technical indicators (RSI, MACD, BB, etc.)
  - Time-of-day features
  - Debate result (if triggered)
  - Trade outcome (filled post-trade via record_outcome)

Output: data/features/features_YYYY-MM-DD.jsonl
Each line is one evaluation. Post-trade outcomes are matched by trade_id.

Future use:
  - XGBoost meta-labeling (Phase 2 of research roadmap)
  - Triple Barrier Method labeling
  - Feature importance analysis
  - Concept drift detection
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.utils.fast_json import dumps

logger = logging.getLogger(__name__)


class FeatureLogger:
    """Append-only feature vector logger for ML training data."""

    def __init__(self, output_dir: str = "data/features"):
        self._dir = Path(output_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._path = self._dir / f"features_{today}.jsonl"
        self._count = 0
        logger.info("D87: FeatureLogger initialized: %s", self._path)

    def log_evaluation(
        self,
        trade_id: str,
        ticker: str,
        # Scanner features
        gap_pct: float,
        rvol: float,
        current_price: float,
        previous_close: float,
        premarket_volume: int,
        float_shares: int | None,
        market_cap: float | None,
        has_news_catalyst: bool,
        rvol_exhaustion: bool,
        # Agent scores
        agent_signals: list[dict[str, Any]],
        # MFCS
        mfcs: float,
        component_scores: dict[str, float],
        risk_score: float,
        qualifies_for_debate: bool,
        # Technical indicators
        indicators: dict[str, Any] | None = None,
        # Debate
        debate_verdict: str | None = None,
        debate_confidence: float | None = None,
        debate_divergence: float | None = None,
        debate_skipped: bool = False,
        # Time features
        hour_et: int | None = None,
        minute_et: int | None = None,
        day_of_week: int | None = None,
        # Verdict
        final_action: str = "NO_TRADE",
        position_size_pct: float = 0.0,
    ) -> None:
        """Log a single evaluation feature vector."""
        now = datetime.now(timezone.utc)

        row = {
            "timestamp": now.isoformat(),
            "trade_id": trade_id,
            "ticker": ticker,
            # Scanner features
            "gap_pct": round(gap_pct, 4),
            "rvol": round(rvol, 2),
            "current_price": round(current_price, 4),
            "previous_close": round(previous_close, 4),
            "premarket_volume": premarket_volume,
            "float_shares": float_shares,
            "market_cap": market_cap,
            "has_news_catalyst": has_news_catalyst,
            "rvol_exhaustion": rvol_exhaustion,
            # Agent scores (simplified)
            "agent_signals": agent_signals,
            # MFCS
            "mfcs": round(mfcs, 4),
            "component_scores": {k: round(v, 4) for k, v in component_scores.items()},
            "risk_score": round(risk_score, 4),
            "qualifies_for_debate": qualifies_for_debate,
            # Technical indicators (pass through)
            "indicators": indicators or {},
            # Debate
            "debate_verdict": debate_verdict,
            "debate_confidence": round(debate_confidence, 3) if debate_confidence else None,
            "debate_divergence": round(debate_divergence, 3) if debate_divergence else None,
            "debate_skipped": debate_skipped,
            # Time features (critical for ML — time-of-day is highly predictive)
            "hour_et": hour_et,
            "minute_et": minute_et,
            "day_of_week": day_of_week,
            # Verdict
            "final_action": final_action,
            "position_size_pct": round(position_size_pct, 4),
            # Outcome (filled later via record_outcome)
            "entry_price": None,
            "exit_price": None,
            "pnl_pct": None,
            "max_drawdown_pct": None,
            "hit_target": None,
        }

        try:
            line = dumps(row, default=str)
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
            self._count += 1
        except Exception as e:
            logger.warning("D87: Feature logging failed: %s", e)

    def record_outcome(
        self,
        trade_id: str,
        entry_price: float,
        exit_price: float,
        pnl_pct: float,
        max_drawdown_pct: float | None = None,
        hit_target: bool | None = None,
    ) -> None:
        """
        Record trade outcome for a previously logged evaluation.

        Note: This appends a new line with outcome data keyed by trade_id.
        Post-processing scripts will join on trade_id.
        """
        row = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "trade_id": trade_id,
            "type": "outcome",
            "entry_price": round(entry_price, 4),
            "exit_price": round(exit_price, 4),
            "pnl_pct": round(pnl_pct, 4),
            "max_drawdown_pct": round(max_drawdown_pct, 4) if max_drawdown_pct else None,
            "hit_target": hit_target,
        }
        try:
            line = dumps(row, default=str)
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception as e:
            logger.warning("D87: Feature outcome logging failed: %s", e)

    @property
    def count(self) -> int:
        return self._count
