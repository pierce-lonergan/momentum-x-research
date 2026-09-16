"""
MOMENTUM-X Base Agent Interface

### ARCHITECTURAL CONTEXT
All LLM-powered agents inherit from BaseAgent. This enforces the standardized
AgentSignal output contract and provides shared infrastructure for:
- LLM invocation via litellm (unified API for all providers)
- Latency tracking per call
- Prompt variant management for Arena integration
- Structured JSON output parsing with retry logic

Ref: ADR-001 (Agent Communication Protocol)
Ref: PROMPT_SIGNATURES.md (Base signatures per agent type)

### DESIGN DECISIONS
- ABC enforces analyze() contract on all subclasses
- litellm over raw provider SDKs for model-agnostic switching
- JSON output mode with structured retry (LLMs sometimes emit invalid JSON)
- Timeout hard-cap at 120s per agent call (ADR-001 latency budget)
- prompt_variant_id threaded through for Arena tracking
"""

from __future__ import annotations

import src.utils.fast_json as json  # D87: orjson drop-in (~3-10x faster)
import asyncio
import logging
import random
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

import litellm

from src.core.models import AgentSignal
from src.utils.trade_logger import get_trade_logger

logger = get_trade_logger(__name__)

# Suppress litellm's verbose logging
litellm.suppress_debug_info = True
# Drop unsupported params (e.g. response_format for Together AI models)
# rather than throwing UnsupportedParamsError. JSON parsing is handled
# robustly by _extract_json() even without response_format.
litellm.drop_params = True
# D83: Zero retries — single attempt only. Speed is alpha.
# With 30s/45s timeouts and 0 retries, worst-case agent latency is 45s
# instead of the 271s observed on Day 3 (90s × 2 attempts + backoff).
litellm.num_retries = 0

# D83-fix: Disable the OpenAI *client-level* retries (httpx transport layer).
# litellm.num_retries only controls litellm's own retry loop. The underlying
# openai.AsyncOpenAI client has a separate max_retries (default=2) that causes
# httpx to retry on connection errors / 5xx, adding ~60s of hidden latency
# (observed: 91s instead of 30s timeout). Setting this env var overrides the
# default in litellm.constants.DEFAULT_MAX_RETRIES which is used when creating
# the OpenAI client instance. We also pass max_retries=0 explicitly in each
# acompletion() call as defense in depth.
import os
os.environ.setdefault("DEFAULT_MAX_RETRIES", "0")

# D296 (2026-05-13): jittered backoff between fallback model retries.
# On 2026-05-13 Together.ai tripped the LLM circuit breaker 21 times
# between 09:20-10:32 ET because primary failures cascaded immediately
# into fallback calls (all hitting the same rate-limit window). Each
# tripped breaker rejected a 60-120s span of agent calls, defaulting
# news/fundamental/deep_search agents to NEUTRAL and effectively
# muting the bot for the morning decision window.
#
# The fix: between primary failure and the first fallback call, sleep a
# JITTERED interval. For RateLimitError specifically, use the longer
# end of the range (rate-limit windows are usually 1-10s wide). For
# Timeout/other errors, use the shorter end (network blips clear fast).
# Jitter is uniform [0.5x, 1.5x] of base so concurrent agent tasks
# don't all retry at the same moment.
_LLM_BACKOFF_BASE_MS_RATE_LIMIT = 1500   # 1.5s base for rate-limit
_LLM_BACKOFF_BASE_MS_OTHER = 200          # 200ms base for timeouts/etc
_LLM_BACKOFF_JITTER_RANGE = (0.5, 1.5)    # uniform multiplier

# doc 285 gap #5 (2026-07-06): consecutive PRIMARY-model rejects, per model id.
# When Together de-serverlessed the pinned Tier-1 model, EVERY primary call
# 400'd but each fallback SUCCESS reset the llm_provider circuit breaker, so
# the bot traded a full session on the degraded chain with ZERO alarms
# (>=21 provider rejects). Fallback success must not silence a dead primary:
# after N consecutive primary rejects we emit a CRITICAL incident (dedup-keyed
# per model; the bus's 5-min window bounds re-emission while it stays dead).
_PRIMARY_REJECTS: dict[str, int] = {}
_PRIMARY_REJECT_ALARM_N = 5


def _llm_fallback_backoff_ms(err: Exception) -> int:
    """Return jittered sleep in ms before the next fallback attempt.

    Rate-limit errors (Together_aiException 'too many requests') need a
    larger window; transient timeouts and 5xx clear faster.
    """
    err_msg = str(err).lower()
    is_rate_limit = (
        "ratelimit" in err_msg
        or "rate_limit" in err_msg
        or "too many requests" in err_msg
        or "429" in err_msg
    )
    base = (
        _LLM_BACKOFF_BASE_MS_RATE_LIMIT if is_rate_limit
        else _LLM_BACKOFF_BASE_MS_OTHER
    )
    multiplier = random.uniform(*_LLM_BACKOFF_JITTER_RANGE)
    return int(base * multiplier)


class BaseAgent(ABC):
    """
    Abstract base class for all LLM-powered analytical agents.

    Subclasses must implement:
        - agent_id: str property
        - system_prompt: str property
        - build_user_prompt(**kwargs) -> str
        - parse_response(raw: dict) -> AgentSignal
    """

    def __init__(
        self,
        model: str,
        provider: str = "",
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout: int = 120,
        prompt_variant_id: str = "v0_control",
        fallback_model: str = "",
        fallback_provider: str = "",
        emergency_model: str = "",
        emergency_provider: str = "",
    ):
        """
        Args:
            model: Model identifier (e.g., "Qwen/Qwen3.5-397B-A17B")
            provider: LiteLLM provider prefix (e.g., "together_ai")
            temperature: Sampling temperature (Arena tests 0.1-0.7)
            max_tokens: Max response tokens
            timeout: Hard timeout in seconds (D92: 15s Tier1, 10s Tier2)
            prompt_variant_id: Arena tracking ID for this prompt configuration
            fallback_model: D91: Backup model when primary fails (different family)
            fallback_provider: D91: Provider prefix for fallback model
            emergency_model: D92: Last-resort model when both primary + fallback fail
            emergency_provider: D92: Provider prefix for emergency model
        """
        self.model = f"{provider}/{model}" if provider else model
        # D91: Fallback model for provider resilience — different model family
        # so a single model's outage doesn't blind the entire system.
        self.fallback_model = (
            f"{fallback_provider}/{fallback_model}"
            if fallback_provider and fallback_model
            else ""
        )
        # D92: Emergency model — last resort (Llama 3.3 70B Turbo).
        # Three-tier chain: Primary → Fallback → Emergency.
        self.emergency_model = (
            f"{emergency_provider}/{emergency_model}"
            if emergency_provider and emergency_model
            else ""
        )
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.prompt_variant_id = prompt_variant_id

    @property
    @abstractmethod
    def agent_id(self) -> str:
        """Unique identifier for this agent type (e.g., 'news_agent')."""
        ...

    @property
    @abstractmethod
    def system_prompt(self) -> str:
        """System prompt defining agent behavior. Ref: PROMPT_SIGNATURES.md."""
        ...

    @abstractmethod
    def build_user_prompt(self, **kwargs: Any) -> str:
        """
        Build the user prompt from input data.
        Subclasses define what data they need.
        """
        ...

    @abstractmethod
    def parse_response(self, raw: dict, ticker: str) -> AgentSignal:
        """
        Parse LLM JSON response into a typed AgentSignal.
        Subclasses define their specific signal type.
        """
        ...

    async def _call_llm(
        self, model: str, user_prompt: str
    ) -> tuple[str, float]:
        """
        Make a single LLM call. Returns (raw_content, latency_ms).
        Raises on failure (timeout, API error, circuit breaker).
        """
        start = time.monotonic()
        response = await litellm.acompletion(
            model=model,
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            timeout=self.timeout,
            max_retries=0,  # D83-fix: disable OpenAI client-level httpx retries
            response_format={"type": "json_object"},
        )
        latency_ms = (time.monotonic() - start) * 1000
        raw_content = response.choices[0].message.content
        # D211: Empty responses (Qwen3.5 at low temp) must trigger fallback chain,
        # not silently parse as NEUTRAL with 0.0 confidence.
        if not raw_content or not raw_content.strip():
            raise ValueError(
                f"Empty response from {model} (temp={self.temperature}) — "
                f"likely Qwen3 thinking-mode issue at low temperature"
            )
        return raw_content, latency_ms

    async def analyze(self, ticker: str, **kwargs: Any) -> AgentSignal:
        """
        Execute the full agent pipeline:
        1. Build prompt from input data
        2. Call LLM via litellm (primary model)
        3. D91: On failure, retry with fallback model (different model family)
        4. Parse structured response
        5. Return typed AgentSignal with latency tracking

        This method handles retries, JSON parsing failures, and timeouts.
        D91: Added fallback model support — when primary model fails (timeout,
        API error, circuit breaker), automatically retry with a different model
        family. This prevents a single model's outage from blinding the system.
        """
        user_prompt = self.build_user_prompt(ticker=ticker, **kwargs)
        start_time = time.monotonic()

        try:
            # D87: LLM circuit breaker — if provider is down, fail fast
            from src.utils.circuit_breaker import llm_breaker
            llm_breaker.check()

            raw_content, latency_ms = await self._call_llm(self.model, user_prompt)
            used_model = self.model

            llm_breaker.record_success()
            _PRIMARY_REJECTS.pop(self.model, None)  # doc 285 #5: primary healthy

        except Exception as primary_err:
            # D87: Record LLM failure for circuit breaker
            # D279: forward the exception so the trip log shows the actual
            # upstream cause (was empty on 2026-05-05's 65× Together.ai
            # storm — operators had no diagnostic without parsing LiteLLM
            # log lines manually).
            from src.utils.circuit_breaker import llm_breaker as _lb, CircuitBreakerError
            if not isinstance(primary_err, CircuitBreakerError):
                _lb.record_failure(primary_err)
                # doc 285 gap #5 fail-loud: count consecutive PRIMARY rejects
                # (breaker fast-fails excluded — those aren't provider verdicts
                # on THIS model). Fallback successes below keep the breaker
                # green, so a dead primary previously never alarmed.
                try:
                    _n = _PRIMARY_REJECTS.get(self.model, 0) + 1
                    _PRIMARY_REJECTS[self.model] = _n
                    if _n >= _PRIMARY_REJECT_ALARM_N:
                        from src.ops.incident_bus import emit_incident
                        emit_incident(
                            "LLM_PRIMARY_MODEL_DEAD", "CRITICAL",
                            context={
                                "model": self.model,
                                "agent_id": self.agent_id,
                                "consecutive_primary_rejects": _n,
                                "last_error": str(primary_err)[:240],
                            },
                            suggested=[
                                "primary LLM is rejecting every call (e.g. "
                                "Together de-serverlessed the pinned model); "
                                "the bot is trading on the FALLBACK chain",
                                "verify a serverless replacement + update "
                                "LLM_TIER1_MODEL in .env (see doc 285 #5)",
                            ],
                            dedup_key=f"llm_primary_dead_{self.model}",
                        )
                        _PRIMARY_REJECTS[self.model] = 0
                except Exception:  # noqa: BLE001 — telemetry never breaks trading
                    pass

            primary_latency = (time.monotonic() - start_time) * 1000

            # D92: Three-tier fallback chain: Primary → Fallback → Emergency
            _fallback_models = []
            if self.fallback_model:
                _fallback_models.append(("fallback", self.fallback_model))
            if self.emergency_model:
                _fallback_models.append(("emergency", self.emergency_model))

            if _fallback_models:
                _last_err = primary_err
                for _fb_label, _fb_model in _fallback_models:
                    # D296 (2026-05-13): jittered backoff before fallback.
                    # Without this every concurrent agent task retried at
                    # the same instant, multiplying rate-limit pressure.
                    _backoff_ms = _llm_fallback_backoff_ms(_last_err)
                    await asyncio.sleep(_backoff_ms / 1000.0)
                    logger.warning(
                        "D92: Agent %s primary failed for %s (%.0fms): %s — "
                        "sleep %dms then trying %s %s",
                        self.agent_id, ticker, primary_latency,
                        str(_last_err)[:100], _backoff_ms, _fb_label, _fb_model,
                    )
                    try:
                        raw_content, fb_latency = await self._call_llm(
                            _fb_model, user_prompt,
                        )
                        used_model = _fb_model
                        latency_ms = (time.monotonic() - start_time) * 1000
                        # Record success on fallback — helps circuit breaker recover
                        _lb.record_success()
                        logger.info(
                            "D92: Agent %s %s SUCCESS for %s via %s (%.0fms total)",
                            self.agent_id, _fb_label.upper(), ticker,
                            _fb_model, latency_ms,
                        )
                        break  # Success — exit fallback chain
                    except Exception as fb_err:
                        _last_err = fb_err
                        primary_latency = (time.monotonic() - start_time) * 1000
                        continue  # Try next fallback
                else:
                    # All models in the chain failed
                    latency_ms = (time.monotonic() - start_time) * 1000
                    logger.error(
                        "D92: Agent %s ALL %d models failed for %s (%.0fms): %s",
                        self.agent_id, 1 + len(_fallback_models), ticker,
                        latency_ms, str(_last_err)[:100],
                    )
                    return AgentSignal(
                        agent_id=self.agent_id,
                        ticker=ticker,
                        timestamp=datetime.now(timezone.utc),
                        signal="NEUTRAL",
                        confidence=0.0,
                        reasoning=f"All {1 + len(_fallback_models)} models failed: {str(_last_err)[:100]}",
                        flags=["AGENT_ERROR", "D92_ALL_FAILED"],
                        prompt_variant_id=self.prompt_variant_id,
                        model_id=self.model,
                        latency_ms=latency_ms,
                    )
            else:
                # No fallback configured — return neutral (original behavior)
                latency_ms = (time.monotonic() - start_time) * 1000
                logger.error(
                    "Agent %s failed for %s: %s (%.0fms)",
                    self.agent_id, ticker, str(primary_err), latency_ms,
                )
                return AgentSignal(
                    agent_id=self.agent_id,
                    ticker=ticker,
                    timestamp=datetime.now(timezone.utc),
                    signal="NEUTRAL",
                    confidence=0.0,
                    reasoning=f"Agent error: {str(primary_err)}",
                    flags=["AGENT_ERROR"],
                    prompt_variant_id=self.prompt_variant_id,
                    model_id=self.model,
                    latency_ms=latency_ms,
                )

        # ── Parse response (reached via primary OR fallback success) ──
        # Sweep fix: guard against None content (LLM can return null content)
        raw_content = raw_content or ""
        logger.debug(
            "Agent %s raw response for %s (first 500 chars): %s",
            self.agent_id, ticker, raw_content[:500] if raw_content else "<empty>",
        )

        parsed = self._extract_json(raw_content)

        logger.debug(
            "Agent %s parsed JSON for %s: signal=%s confidence=%s keys=%s",
            self.agent_id, ticker,
            parsed.get("signal", "<missing>"),
            parsed.get("confidence", "<missing>"),
            list(parsed.keys()),
        )

        signal = self.parse_response(parsed, ticker)

        # ── D101/D106: Confidence deflation ──
        # KalshiBench found LLMs overconfident with ECE 0.12-0.40.
        # Apply deflation factor to raw LLM confidence scores.
        # NOTE: This deflation ONLY applies to LLM-based agents (NewsAgent,
        # FundamentalAgent, InstitutionalAgent, DeepSearchAgent) because only
        # they inherit from BaseAgent. DeterministicRiskAgent and
        # DeterministicTechnicalAgent are duck-typed and bypass this entirely.
        raw_confidence = signal.confidence
        try:
            from config.settings import load_settings
            _deflation = load_settings().scoring.confidence_deflation_factor
        except Exception:
            _deflation = 0.70  # D121 BUG-M7: Match config default (0.70)
        deflated_confidence = min(1.0, max(0.0, raw_confidence * _deflation))

        logger.info(
            "Agent %s → %s | signal=%s conf=%.2f→%.2f (×%.1f deflation) | %s",
            self.agent_id, ticker, signal.signal,
            raw_confidence, deflated_confidence, _deflation,
            signal.reasoning[:100] if signal.reasoning else "no reasoning",
        )

        return signal.model_copy(
            update={
                "prompt_variant_id": self.prompt_variant_id,
                "model_id": used_model,
                "latency_ms": latency_ms,
                "confidence": deflated_confidence,
                "raw_confidence": raw_confidence,  # D122: preserve pre-deflation for experiment replay
            }
        )

    def _extract_json(self, raw: str) -> dict:
        """
        Extract JSON from LLM response, handling common formatting issues:
        - Markdown code fences (```json ... ```)
        - Leading/trailing whitespace
        - <think>...</think> blocks before JSON (D92: defensive — primary models
          are instruct now, but fallback DeepSeek V3.1 may still emit these)
        - JSON arrays (some models wrap response in [...] instead of {...})
        - JSON embedded in free-text (regex extraction fallback)
        """
        import re

        text = raw.strip()

        # Strip R1 thinking blocks (handle both complete and incomplete tags)
        if "<think>" in text:
            think_end = text.rfind("</think>")
            if think_end != -1:
                text = text[think_end + len("</think>"):].strip()
            else:
                # Incomplete think block — try to find JSON after <think>
                think_start = text.find("<think>")
                # Look for JSON after the opening tag
                after_think = text[think_start + len("<think>"):]
                # Find first { or [ that might be JSON
                json_start = min(
                    (after_think.find("{") if after_think.find("{") >= 0 else len(after_think)),
                    (after_think.find("[") if after_think.find("[") >= 0 else len(after_think)),
                )
                if json_start < len(after_think):
                    text = after_think[json_start:].strip()

        # Strip markdown fences
        if "```" in text:
            # Extract content between fences
            fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
            if fence_match:
                text = fence_match.group(1).strip()
            elif text.startswith("```"):
                # Fallback: remove fence lines
                lines = text.split("\n")
                lines = [
                    line for line in lines
                    if not line.strip().startswith("```")
                ]
                text = "\n".join(lines).strip()

        # Try direct JSON parse first
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            # Fallback: try to find a JSON object or array in the text
            json_match = re.search(r"(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})", text, re.DOTALL)
            if json_match:
                try:
                    parsed = json.loads(json_match.group(1))
                except json.JSONDecodeError:
                    logger.warning(
                        "Agent %s: Could not parse JSON from response (len=%d): %s...",
                        self.agent_id, len(text), text[:200],
                    )
                    return {}
            else:
                logger.warning(
                    "Agent %s: No JSON found in response (len=%d): %s...",
                    self.agent_id, len(text), text[:200],
                )
                return {}

        # Some models (e.g. DeepSeek R1) wrap the response in a JSON array.
        # Unwrap single-element arrays to get the dict inside.
        if isinstance(parsed, list):
            if len(parsed) == 1 and isinstance(parsed[0], dict):
                parsed = parsed[0]
            elif len(parsed) > 0 and isinstance(parsed[0], dict):
                # Multiple dicts in array — take the first one
                parsed = parsed[0]
            else:
                # Can't extract a dict — return empty to trigger fallback
                parsed = {}

        if not isinstance(parsed, dict):
            parsed = {}

        return parsed
