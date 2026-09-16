"""
D211 Phantom Portfolio — Counterfactual Trade Journal

Records every BUY verdict BEFORE gates can block it, with gate attribution.
Used by scripts/phantom_replay.py to measure which gates add/destroy value.

Usage in main.py:
    phantom = PhantomJournal("data/phantom")
    phantom.record(verdict, blocked_by="D204_NEWS_CONFIDENCE")

EOD replay:
    entries = phantom.load("2026-04-08")
    for e in entries:
        # Simulate position through minute bars
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class PhantomJournal:
    """Append-only JSONL writer for phantom (counterfactual) trade verdicts."""

    def __init__(self, output_dir: str = "data/phantom") -> None:
        self._output_dir = Path(output_dir)
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._file_path = self._output_dir / f"phantom_{self._today}.jsonl"
        self._count = 0
        # D312 (2026-05-24, doc 172): in-memory per-session gate-rejection
        # counter. Surfaced via .gate_summary() and wired into the D304
        # EOD Discord alert's veto_summary kwarg. Closes the doc 168
        # follow-up + the Friday silent-BUY observability gap (123
        # D216 rejects on 5/22 invisible to operator).
        # Each call to update_gate increments by 1; record() initial
        # blocked_by also increments. Final "EXECUTED" status (the
        # post-fact resolution) is NOT counted as a rejection.
        from collections import Counter as _Counter
        self._gate_counts: _Counter = _Counter()

    def record(
        self,
        ticker: str,
        entry_price: float,
        stop_loss: float,
        target_prices: list[float],
        position_size_pct: float,
        mfcs: float,
        confidence: float,
        direction: str = "long",
        blocked_by: str = "EXECUTED",
        reasoning_summary: str = "",
        faller_score: float | None = None,
        gap_pct: float = 0.0,
        rvol: float = 0.0,
        market_cap: float | None = None,
        kelly_tier: int = 1,
        vix_level: float | None = None,
        eval_timestamp: str | None = None,
    ) -> None:
        """Record a phantom verdict. Called for EVERY BUY verdict before gates."""
        entry = {
            "ticker": ticker,
            "entry_price": round(entry_price, 4),
            "stop_loss": round(stop_loss, 4),
            "target_prices": [round(t, 4) for t in target_prices],
            "position_size_pct": round(position_size_pct, 6),
            "mfcs": round(mfcs, 4),
            "confidence": round(confidence, 4),
            "direction": direction,
            "blocked_by": blocked_by,
            "reasoning_summary": reasoning_summary[:200],
            "faller_score": round(faller_score, 4) if faller_score is not None else None,
            "gap_pct": round(gap_pct, 4),
            "rvol": round(rvol, 2),
            "market_cap": market_cap,
            "kelly_tier": kelly_tier,
            "vix_level": round(vix_level, 2) if vix_level is not None else None,
            "eval_timestamp": eval_timestamp or datetime.now(timezone.utc).isoformat(),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            with open(self._file_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
            self._count += 1
        except Exception as e:
            logger.warning("Phantom journal write failed: %s", e)

    def update_gate(self, ticker: str, blocked_by: str) -> None:
        """Record a gate decision for a ticker.

        Appends a gate-update entry instead of read-modify-write (which had
        a file race condition when multiple paths write concurrently).
        The replay script deduplicates by taking the LAST entry per ticker.

        D312: also increments the in-memory _gate_counts counter so EOD
        Discord can surface gate-rejection breakdown via veto_summary.
        EXECUTED / PENDING values are operational states, not rejections,
        so they're excluded from the counter.
        """
        try:
            update_entry = {
                "ticker": ticker,
                "blocked_by": blocked_by,
                "type": "gate_update",
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }
            with open(self._file_path, "a") as f:
                f.write(json.dumps(update_entry) + "\n")
        except Exception as e:
            logger.debug("Phantom journal update failed for %s: %s", ticker, e)
        # D312: count only actual gate rejections, not operational states
        if blocked_by and blocked_by not in ("EXECUTED", "PENDING"):
            try:
                self._gate_counts[blocked_by] += 1
            except Exception:
                pass  # never block production on counter failure

    def gate_summary(self) -> dict[str, int]:
        """D312 (2026-05-24, doc 172): per-session gate rejection counts.

        Returns a {gate_name: rejection_count} dict for surfacing in the
        EOD Discord alert. Includes only gates that actually rejected
        verdicts; never includes EXECUTED / PENDING.

        Example return value for Friday 2026-05-22:
            {"D216_RECENTLY_CLOSED": 123, "D85_FAST_PATH": 21,
             "D56_DUPLICATE": 1}

        This was the visibility gap that hid Friday's "10 BUYs, 0 OTOs"
        pattern from the operator. With this surfaced in Discord:
        operator immediately sees "D216 blocked 123 entries" and can
        decide whether to tune the cooldown or accept the behavior.
        """
        try:
            return dict(self._gate_counts)
        except Exception:
            return {}

    @staticmethod
    def load(date: str, output_dir: str = "data/phantom") -> list[dict]:
        """Load all phantom entries for a given date."""
        path = Path(output_dir) / f"phantom_{date}.jsonl"
        if not path.exists():
            return []
        entries = []
        for line in path.read_text().strip().split("\n"):
            if line.strip():
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return entries
