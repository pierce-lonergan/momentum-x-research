"""
D115: Trade Result Tracker — rolling win/loss history for Kelly tier classification.

Maintains a persistent JSONL-backed deque of recent trade results.
Provides win_rate(n) and catalyst-specific win rates needed by KellyTierClassifier.

Ref: docs/PAPER_TRADING_LOG.md Day 1 learnings
"""

from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class TradeResult:
    """A single closed trade outcome."""

    ticker: str
    catalyst_type: str
    entry_time: str  # ISO format
    exit_time: str  # ISO format
    pnl: float
    is_win: bool
    kelly_tier: int = 1
    session_date: str = ""  # YYYY-MM-DD
    # D217: Provenance field — distinguishes full Shapley attribution from
    # basic fallback (no cached ScoredCandidate). Downstream code can filter
    # by source to assess data quality. "shapley" = full attribution,
    # "basic" = position-only P&L without agent contribution analysis.
    source: str = "shapley"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> TradeResult:
        return cls(
            ticker=d.get("ticker", ""),
            catalyst_type=d.get("catalyst_type", "unknown"),
            entry_time=d.get("entry_time", ""),
            exit_time=d.get("exit_time", ""),
            pnl=float(d.get("pnl", 0.0)),
            is_win=bool(d.get("is_win", False)),
            kelly_tier=int(d.get("kelly_tier", 1)),
            session_date=d.get("session_date", ""),
            source=d.get("source", "shapley"),  # D217: backward compat default
        )


class TradeResultTracker:
    """
    Rolling trade result history for Kelly tier classification.

    - Persists to JSONL for cross-session continuity
    - Provides win_rate(n) and win_rate_by_catalyst(type, n)
    - Tracks Tier 3+ usage for daily safety caps
    """

    def __init__(
        self,
        history_file: Path | str = "data/trade_results.jsonl",
        max_history: int = 200,
    ) -> None:
        self._path = Path(history_file)
        self._max_history = max_history
        self._results: deque[TradeResult] = deque(maxlen=max_history)
        self._load()

    def _load(self) -> None:
        """Hydrate from JSONL on startup."""
        if not self._path.exists():
            logger.info("D115: No trade history at %s — starting fresh", self._path)
            return
        count = 0
        try:
            with open(self._path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        self._results.append(TradeResult.from_dict(json.loads(line)))
                        count += 1
                    except (json.JSONDecodeError, KeyError):
                        continue
            logger.info("D115: Loaded %d trade results from %s", count, self._path)
        except OSError as e:
            logger.warning("D115: Could not read trade history: %s", e)

    def record(self, result: TradeResult) -> None:
        """Append a trade result and flush to JSONL."""
        self._results.append(result)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(json.dumps(result.to_dict()) + "\n")
        except OSError as e:
            logger.error("D115: Could not write trade result: %s", e)

    def win_rate(self, n: int = 30) -> float | None:
        """
        Win rate over last n trades. Returns None if fewer than n trades,
        UNLESS cold_start_default is provided.

        Sweep fix: previously returned None until 30 trades existed, which
        caused Kelly Tier 2+ to always fail the win_rate check — a cold-start
        deadlock where you can't size up until you have history, but you can't
        build history without sizing up.
        """
        # Sweep fix: guard against n<=0 to prevent ZeroDivisionError
        if n <= 0:
            return None
        if len(self._results) < n:
            # Cold-start: use whatever history we have, or return None
            if len(self._results) >= 5:
                # Enough trades to compute a rough win rate
                recent = list(self._results)
                wins = sum(1 for r in recent if r.is_win)
                return wins / len(recent)
            return None
        recent = list(self._results)[-n:]
        wins = sum(1 for r in recent if r.is_win)
        return wins / n

    def win_rate_by_catalyst(
        self, catalyst_type: str, n: int = 30
    ) -> float | None:
        """Win rate for a specific catalyst type over last n matching trades."""
        # Sweep fix: guard against n<=0 to prevent ZeroDivisionError
        if n <= 0:
            return None
        matching = [r for r in self._results if r.catalyst_type == catalyst_type]
        if len(matching) < n:
            # D121 BUG-S13: Cold-start fallback — same pattern as win_rate().
            # Without this, catalyst-specific checks always fail early in trading,
            # blocking Kelly tier upgrades for new catalyst types.
            if len(matching) >= 5:
                wins = sum(1 for r in matching if r.is_win)
                return wins / len(matching)
            return None
        recent = matching[-n:]
        wins = sum(1 for r in recent if r.is_win)
        return wins / n

    def tier3_plus_count_today(self) -> int:
        """Count of Tier 3+ trades executed today."""
        today = date.today().isoformat()
        return sum(
            1
            for r in self._results
            if r.kelly_tier >= 3 and r.session_date == today
        )

    def had_tier3_stop_today(self) -> bool:
        """Whether any Tier 3+ trade hit its stop (lost money) today."""
        today = date.today().isoformat()
        return any(
            r.kelly_tier >= 3 and r.pnl < 0 and r.session_date == today
            for r in self._results
        )

    def daily_realized_pnl(self) -> float:
        """Sum of P&L for all trades closed today."""
        today = date.today().isoformat()
        return sum(r.pnl for r in self._results if r.session_date == today)

    @property
    def total_trades(self) -> int:
        return len(self._results)
