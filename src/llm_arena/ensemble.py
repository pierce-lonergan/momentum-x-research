"""Multi-model ensemble voting for catalyst classification.

Calls N models in parallel with the same prompt, collects responses,
and produces a majority-vote classification that is more accurate
than any single model.

Usage::

    from src.llm_arena.ensemble import EnsembleConfig, EnsembleVoter

    config = EnsembleConfig(models=["qwen3-235b", "llama-3.3-70b", "mixtral-8x7b"])
    voter = EnsembleVoter(config, data_dir="data/llm_arena")
    result = voter.evaluate_batch(scenarios)
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .harness import AgentConfig, AgentHarness, AgentRunResult, MODEL_REGISTRY
from .models import LabeledScenario


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass
class EnsembleConfig:
    """Configuration for the ensemble voter."""

    models: list[str]           # Model IDs from MODEL_REGISTRY
    agent_type: str = "news"
    timeout_seconds: float = 20.0
    require_min_responses: int = 2  # Need at least N/M to vote
    tie_break: str = "neutral"      # What to return on a true tie
    temperature: float = 0.0
    max_tokens: int = 1024


# ---------------------------------------------------------------------------
# Per-model response (lightweight, serialisable)
# ---------------------------------------------------------------------------


@dataclass
class ModelResponse:
    """Result from one model within an ensemble evaluation."""

    model_id: str
    signal: str           # Raw signal: BULL, BEAR, NEUTRAL, STRONG_BULL, STRONG_BEAR
    confidence: float
    catalyst_type: str
    reasoning: str
    latency_ms: float
    success: bool
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "signal": self.signal,
            "confidence": self.confidence,
            "catalyst_type": self.catalyst_type,
            "reasoning": self.reasoning,
            "latency_ms": self.latency_ms,
            "success": self.success,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ModelResponse":
        return cls(
            model_id=d["model_id"],
            signal=d.get("signal", "NEUTRAL"),
            confidence=d.get("confidence", 0.5),
            catalyst_type=d.get("catalyst_type", "unknown"),
            reasoning=d.get("reasoning", ""),
            latency_ms=d.get("latency_ms", 0.0),
            success=d.get("success", False),
            error=d.get("error", ""),
        )


# ---------------------------------------------------------------------------
# Ensemble result
# ---------------------------------------------------------------------------


@dataclass
class EnsembleResult:
    """Majority-vote result from running N models on one scenario."""

    scenario_id: str

    # Individual model responses
    responses: list[ModelResponse]

    # Ensemble output (majority vote)
    voted_signal: str           # BULL, BEAR, or NEUTRAL
    voted_confidence: float     # Average confidence of agreeing models
    voted_catalyst_type: str    # Most common catalyst type among agreeing models
    agreement_ratio: float      # 3/3 = 1.0, 2/3 = 0.67

    # Operational meta
    models_responded: int       # How many models returned a parseable signal
    models_agreed: int          # How many voted for the majority signal
    total_latency_ms: float     # Wall-clock time (parallel — max of all)
    total_cost: float           # Sum of all model costs

    # Dissent info
    dissenting_model: str = ""
    dissenting_signal: str = ""

    # Accuracy (filled by scoring layer, not ensemble voter)
    direction_correct: Optional[bool] = None
    catalyst_correct: Optional[bool] = None

    def to_dict(self) -> dict:
        return {
            "scenario_id": self.scenario_id,
            "responses": [r.to_dict() for r in self.responses],
            "voted_signal": self.voted_signal,
            "voted_confidence": self.voted_confidence,
            "voted_catalyst_type": self.voted_catalyst_type,
            "agreement_ratio": self.agreement_ratio,
            "models_responded": self.models_responded,
            "models_agreed": self.models_agreed,
            "total_latency_ms": self.total_latency_ms,
            "total_cost": self.total_cost,
            "dissenting_model": self.dissenting_model,
            "dissenting_signal": self.dissenting_signal,
            "direction_correct": self.direction_correct,
            "catalyst_correct": self.catalyst_correct,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EnsembleResult":
        return cls(
            scenario_id=d["scenario_id"],
            responses=[ModelResponse.from_dict(r) for r in d.get("responses", [])],
            voted_signal=d["voted_signal"],
            voted_confidence=d["voted_confidence"],
            voted_catalyst_type=d["voted_catalyst_type"],
            agreement_ratio=d["agreement_ratio"],
            models_responded=d["models_responded"],
            models_agreed=d["models_agreed"],
            total_latency_ms=d["total_latency_ms"],
            total_cost=d["total_cost"],
            dissenting_model=d.get("dissenting_model", ""),
            dissenting_signal=d.get("dissenting_signal", ""),
            direction_correct=d.get("direction_correct"),
            catalyst_correct=d.get("catalyst_correct"),
        )


# ---------------------------------------------------------------------------
# Signal normalisation helpers
# ---------------------------------------------------------------------------

_BULL_SIGNALS = {"BULL", "STRONG_BULL"}
_BEAR_SIGNALS = {"BEAR", "STRONG_BEAR"}
_NEUTRAL_SIGNALS = {"NEUTRAL"}


def _normalize_signal(signal: str) -> str:
    """Collapse STRONG_BULL -> BULL, STRONG_BEAR -> BEAR for voting purposes."""
    s = (signal or "NEUTRAL").upper()
    if s in _BULL_SIGNALS:
        return "BULL"
    if s in _BEAR_SIGNALS:
        return "BEAR"
    return "NEUTRAL"


# ---------------------------------------------------------------------------
# Ensemble voter
# ---------------------------------------------------------------------------


class EnsembleVoter:
    """Calls N models in parallel with the same prompt and returns majority vote.

    Args:
        config:   EnsembleConfig specifying which models to call.
        data_dir: Arena data directory (passed to AgentHarness).
    """

    def __init__(self, config: EnsembleConfig, data_dir: str):
        self._config = config
        self._data_dir = data_dir

    # ------------------------------------------------------------------
    # Public: evaluate one scenario
    # ------------------------------------------------------------------

    async def evaluate(self, scenario: LabeledScenario) -> EnsembleResult:
        """Run all models in parallel and return the ensemble result."""
        harness = AgentHarness(self._data_dir)

        agent_configs = [
            AgentConfig(
                agent_type=self._config.agent_type,
                model_id=model_id,
                timeout_seconds=self._config.timeout_seconds,
                temperature=self._config.temperature,
                max_tokens=self._config.max_tokens,
            )
            for model_id in self._config.models
        ]

        # Fire all models in parallel — wall-clock time ≈ max(individual latencies)
        start = time.monotonic()
        raw_results: list[AgentRunResult] = await asyncio.gather(
            *[harness._run_live_single_async(scenario, cfg) for cfg in agent_configs]
        )
        wall_ms = (time.monotonic() - start) * 1000

        # Convert AgentRunResult → ModelResponse
        responses: list[ModelResponse] = []
        for result in raw_results:
            responses.append(
                ModelResponse(
                    model_id=result.agent_config.model_id,
                    signal=result.signal_direction or "NEUTRAL",
                    confidence=result.signal_confidence or 0.5,
                    catalyst_type=result.catalyst_type or "unknown",
                    reasoning=result.reasoning or "",
                    latency_ms=result.latency_ms,
                    success=result.parse_success,
                    error=result.error or "",
                )
            )

        voted_signal, voted_confidence, models_agreed, agreement_ratio = (
            self._majority_vote(responses)
        )
        voted_catalyst = self._most_common_catalyst(responses, voted_signal)

        # Identify first dissenting model (if any)
        dissenting_model = ""
        dissenting_signal = ""
        for r in responses:
            if r.success and _normalize_signal(r.signal) != voted_signal:
                dissenting_model = r.model_id
                dissenting_signal = r.signal
                break

        total_cost = sum(r.cost_usd for r in raw_results)

        return EnsembleResult(
            scenario_id=scenario.scenario_id,
            responses=responses,
            voted_signal=voted_signal,
            voted_confidence=voted_confidence,
            voted_catalyst_type=voted_catalyst,
            agreement_ratio=agreement_ratio,
            models_responded=sum(1 for r in responses if r.success),
            models_agreed=models_agreed,
            total_latency_ms=wall_ms,
            total_cost=total_cost,
            dissenting_model=dissenting_model,
            dissenting_signal=dissenting_signal,
        )

    # ------------------------------------------------------------------
    # Public: evaluate a batch of scenarios
    # ------------------------------------------------------------------

    def evaluate_batch(
        self,
        scenarios: list[LabeledScenario],
        rate_limit_delay: float = 0.3,
        progress_callback=None,
    ) -> list[EnsembleResult]:
        """Run ensemble on multiple scenarios sequentially.

        Each scenario fires all N models in parallel, then waits
        ``rate_limit_delay`` seconds before the next scenario.

        Args:
            scenarios:         Scenarios to evaluate.
            rate_limit_delay:  Seconds between scenario batches (rate limit).
            progress_callback: Optional callable(done, total).
        """
        results: list[EnsembleResult] = []
        total = len(scenarios)
        for i, scenario in enumerate(scenarios):
            result = asyncio.run(self.evaluate(scenario))
            results.append(result)
            if progress_callback:
                progress_callback(i + 1, total)
            if rate_limit_delay > 0 and i < total - 1:
                time.sleep(rate_limit_delay)
        return results

    # ------------------------------------------------------------------
    # Voting logic
    # ------------------------------------------------------------------

    def _majority_vote(
        self, responses: list[ModelResponse]
    ) -> tuple[str, float, int, float]:
        """Compute majority vote.

        Returns:
            (voted_signal, avg_confidence_of_agreeing_models, models_agreed, agreement_ratio)
        """
        successful = [r for r in responses if r.success]

        if len(successful) < self._config.require_min_responses:
            # Not enough models responded — fall back to tie-break
            return self._config.tie_break.upper(), 0.5, 0, 0.0

        normalized = [_normalize_signal(r.signal) for r in successful]
        counts = Counter(normalized)
        top_signal, top_count = counts.most_common(1)[0]

        # True tie (all different, e.g. 1 BULL, 1 BEAR, 1 NEUTRAL with 3 models)
        if top_count == 1:
            top_signal = self._config.tie_break.upper()

        agreeing = [r for r in successful if _normalize_signal(r.signal) == top_signal]
        avg_conf = (
            sum(r.confidence for r in agreeing) / len(agreeing) if agreeing else 0.5
        )
        agreement_ratio = top_count / len(successful)

        return top_signal, round(avg_conf, 4), top_count, round(agreement_ratio, 4)

    def _most_common_catalyst(
        self, responses: list[ModelResponse], voted_signal: str
    ) -> str:
        """Most common catalyst type among models that agree with the majority vote."""
        agreeing = [
            r for r in responses
            if r.success and _normalize_signal(r.signal) == voted_signal
        ]
        if not agreeing:
            agreeing = [r for r in responses if r.success]
        if not agreeing:
            return "unknown"
        counts = Counter(r.catalyst_type for r in agreeing if r.catalyst_type)
        return counts.most_common(1)[0][0] if counts else "unknown"
