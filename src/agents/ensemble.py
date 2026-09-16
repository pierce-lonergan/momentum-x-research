"""D202: Ensemble Agent — multi-call signal averaging for noise reduction.

### PROBLEM
D201-E8 found 47% verdict inconsistency: the same stock evaluated twice gets
a different BUY/NO_TRADE verdict nearly half the time. MFCS drifts 0.158
between evaluations (buy threshold = 0.25). LLM signals are fundamentally noisy.

### SOLUTION
Call each LLM agent N times in parallel and aggregate:
- Direction: majority vote (e.g., 2/3 BULL → BULL)
- Confidence: mean of all N calls
- Reasoning: concatenate top reasoning from each call
- Catalyst: most specific non-NONE catalyst wins (for news agent)

### DESIGN
- Parallel dispatch: all N calls launch simultaneously → no latency penalty
- Graceful degradation: if only K < N calls succeed, aggregate K results
- Minimum quorum: need at least 2 successful calls for ensemble benefit
- Skip ensemble for deterministic agents (technical, risk) — they're already stable

### LATENCY IMPACT
- Single call: ~5-15s
- 3 parallel calls: ~5-15s (same — all run concurrently)
- Together AI rate limit: 150ms stagger between agents (not within ensemble)
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from src.core.models import AgentSignal

logger = logging.getLogger(__name__)

# Signal direction strength ordering (for tie-breaking)
_SIGNAL_STRENGTH = {
    "STRONG_BULL": 2,
    "BULL": 1,
    "NEUTRAL": 0,
    "BEAR": -1,
    "STRONG_BEAR": -2,
}


class EnsembleWrapper:
    """
    Wraps any agent (duck-typed with async analyze()) and calls it N times,
    then aggregates the results into a single, more stable signal.

    Usage:
        from src.agents.ensemble import EnsembleWrapper
        ensemble_news = EnsembleWrapper(news_agent, n_calls=3, min_quorum=2)
        signal = await ensemble_news.analyze(ticker="BFRG", **kwargs)
    """

    def __init__(
        self,
        agent: Any,
        n_calls: int = 3,
        min_quorum: int = 2,
        intra_stagger_ms: float = 50.0,
    ):
        """
        Args:
            agent: Any object with async analyze(ticker, **kwargs) → AgentSignal
            n_calls: Number of parallel LLM calls per evaluation
            min_quorum: Minimum successful calls needed for ensemble aggregation.
                        If fewer succeed, returns the single best result.
            intra_stagger_ms: Millisecond delay between launching parallel calls
                              (avoids hitting rate limits on the same second)
        """
        self._agent = agent
        self._n_calls = max(1, n_calls)
        self._min_quorum = max(1, min(min_quorum, n_calls))
        self._intra_stagger_ms = intra_stagger_ms

    @property
    def agent_id(self) -> str:
        return getattr(self._agent, "agent_id", "unknown_ensemble")

    async def analyze(self, ticker: str, **kwargs: Any) -> AgentSignal:
        """
        Run the wrapped agent N times in parallel and aggregate results.

        Returns a single AgentSignal with:
        - Direction: majority vote across N calls
        - Confidence: mean confidence across N calls
        - Flags: includes "D202_ENSEMBLE_N={n}" and agreement stats
        - Reasoning: summary of ensemble agreement
        """
        if self._n_calls == 1:
            return await self._agent.analyze(ticker=ticker, **kwargs)

        start = time.monotonic()

        # Launch N calls in parallel with small stagger
        tasks = []
        for i in range(self._n_calls):
            if i > 0 and self._intra_stagger_ms > 0:
                await asyncio.sleep(self._intra_stagger_ms / 1000)
            task = asyncio.create_task(
                self._safe_call(ticker, i, **kwargs)
            )
            tasks.append(task)

        # Gather all results — return_exceptions=True prevents one failed task
        # from crashing the entire gather. BUG-FIX: without this, an unhandled
        # exception in _safe_call would abort all parallel calls.
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)
        signals = [
            r for r in raw_results
            if r is not None and not isinstance(r, BaseException)
        ]

        total_ms = (time.monotonic() - start) * 1000

        if not signals:
            logger.error(
                "D202 ENSEMBLE %s: ALL %d calls failed for %s",
                self.agent_id, self._n_calls, ticker,
            )
            return AgentSignal(
                agent_id=self.agent_id,
                ticker=ticker,
                timestamp=datetime.now(timezone.utc),
                signal="NEUTRAL",
                confidence=0.0,
                reasoning=f"D202: All {self._n_calls} ensemble calls failed",
                flags=["AGENT_ERROR", "D202_ENSEMBLE_ALL_FAILED"],
                model_id="",
                latency_ms=total_ms,
            )

        if len(signals) == 1:
            # Only one succeeded — return it directly with ensemble flag
            sig = signals[0]
            return sig.model_copy(update={
                "flags": list(sig.flags or []) + [
                    f"D202_ENSEMBLE_N=1/{self._n_calls}",
                    "D202_NO_QUORUM",
                ],
                "latency_ms": total_ms,
            })

        # ── Aggregate N successful signals ──────────────────────────
        aggregated = self._aggregate(signals, total_ms, ticker)
        return aggregated

    async def _safe_call(
        self, ticker: str, call_idx: int, **kwargs: Any
    ) -> AgentSignal | None:
        """Call the agent, returning None on any failure."""
        try:
            return await self._agent.analyze(ticker=ticker, **kwargs)
        except Exception as e:
            logger.debug(
                "D202 ENSEMBLE %s call %d/%d failed for %s: %s",
                self.agent_id, call_idx + 1, self._n_calls, ticker, e,
            )
            return None

    def _aggregate(
        self,
        signals: list[AgentSignal],
        total_ms: float,
        ticker: str,
    ) -> AgentSignal:
        """Aggregate N signals into one consensus signal."""
        n = len(signals)

        # ── Direction: majority vote ────────────────────────────────
        direction_votes = Counter(s.signal for s in signals)
        majority_signal, majority_count = direction_votes.most_common(1)[0]

        # On tie (e.g., 1 BULL, 1 BEAR, 1 NEUTRAL), pick the more bearish
        # direction (conservative — better to miss a trade than take a bad one)
        if majority_count <= n // 2 and n > 2:
            # No clear majority — pick the signal closest to NEUTRAL
            # (conservative: bias toward not trading)
            avg_strength = sum(
                _SIGNAL_STRENGTH.get(s.signal, 0) for s in signals
            ) / n
            if avg_strength > 0.5:
                majority_signal = "BULL"
            elif avg_strength < -0.5:
                majority_signal = "BEAR"
            else:
                majority_signal = "NEUTRAL"

        # ── Confidence: mean across all calls ───────────────────────
        mean_confidence = sum(s.confidence for s in signals) / n
        # Also compute agreement-weighted confidence: if all agree, full confidence
        # If split, reduce confidence proportionally
        agreement_ratio = majority_count / n
        adjusted_confidence = mean_confidence * agreement_ratio

        # ── Catalyst: most specific non-NONE wins (for news agent) ──
        best_catalyst = None
        best_catalyst_specificity = None
        for s in signals:
            ct = getattr(s, "catalyst_type", None)
            cs = getattr(s, "catalyst_specificity", None)
            if ct and ct not in ("NONE", "None", "null", "unknown", None):
                if best_catalyst is None:
                    best_catalyst = ct
                    best_catalyst_specificity = cs
                elif cs == "CONFIRMED" and best_catalyst_specificity != "CONFIRMED":
                    best_catalyst = ct
                    best_catalyst_specificity = cs

        # ── Raw confidence: mean of pre-deflation values ────────────
        raw_confs = [
            getattr(s, "raw_confidence", None) or s.confidence
            for s in signals
        ]
        mean_raw_confidence = sum(raw_confs) / n if raw_confs else mean_confidence

        # ── Reasoning: summarize agreement ──────────────────────────
        vote_summary = ", ".join(
            f"{sig}={cnt}" for sig, cnt in direction_votes.most_common()
        )
        reasoning = (
            f"D202 ENSEMBLE ({n}/{self._n_calls} calls): "
            f"votes=[{vote_summary}] → {majority_signal} | "
            f"agreement={agreement_ratio:.0%} | "
            f"mean_conf={mean_confidence:.3f} adj_conf={adjusted_confidence:.3f}"
        )
        # Append first signal's reasoning for context
        if signals[0].reasoning:
            reasoning += f" | lead: {signals[0].reasoning[:200]}"

        # ── Build aggregated signal ─────────────────────────────────
        # Start from the first signal (preserves subclass type and fields)
        base_signal = signals[0]
        update_dict: dict[str, Any] = {
            "signal": majority_signal,
            "confidence": adjusted_confidence,
            "raw_confidence": mean_raw_confidence,
            "reasoning": reasoning,
            "latency_ms": total_ms,
            "flags": list(base_signal.flags or []) + [
                f"D202_ENSEMBLE_N={n}/{self._n_calls}",
                f"D202_AGREEMENT={agreement_ratio:.0%}",
                f"D202_VOTES={vote_summary}",
            ],
        }

        # Preserve catalyst type from best signal (news agent)
        if best_catalyst is not None and hasattr(base_signal, "catalyst_type"):
            update_dict["catalyst_type"] = best_catalyst
            if best_catalyst_specificity:
                update_dict["catalyst_specificity"] = best_catalyst_specificity

        logger.info(
            "D202 ENSEMBLE %s %s: %d/%d calls → %s (%.0f%% agreement, conf=%.3f→%.3f) [%.0fms]",
            self.agent_id, ticker, n, self._n_calls,
            majority_signal, agreement_ratio * 100,
            mean_confidence, adjusted_confidence, total_ms,
        )

        return base_signal.model_copy(update=update_dict)
