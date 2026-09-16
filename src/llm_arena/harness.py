"""Agent Harness — sandboxed execution environment for LLM agent evaluation.

Two modes:
  REPLAY — uses actual_agent_signals already stored in each LabeledScenario.
            Free, instant, deterministic. Works with all 509 existing scenarios.
  LIVE    — actually calls the LLM API with scenario inputs.
            Uses the same litellm infrastructure as the production system.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .models import LabeledScenario


# ---------------------------------------------------------------------------
# Model registry — maps short model_id → API model path + pricing
# ---------------------------------------------------------------------------

#: Registry of models available for live-mode evaluation.
#: Each entry maps a short model_id (used in AgentConfig) to:
#:   api_model_id    — full litellm model string (provider/model_name)
#:   cost_per_1k_input  — USD per 1,000 input tokens
#:   cost_per_1k_output — USD per 1,000 output tokens
MODEL_REGISTRY: dict[str, dict] = {
    # Together AI — production models
    "qwen3.5-397b": {
        "api_model_id": "together_ai/Qwen/Qwen3.5-397B-A17B",
        "cost_per_1k_input": 0.0012,
        "cost_per_1k_output": 0.0016,
        "provider": "together_ai",
    },
    "qwen3-235b": {
        "api_model_id": "together_ai/Qwen/Qwen3-235B-A22B-Instruct-2507-tput",
        "cost_per_1k_input": 0.0009,
        "cost_per_1k_output": 0.0009,
        "provider": "together_ai",
    },
    # Together AI — legacy baseline (used in model_comparison experiment)
    "mixtral-8x7b": {
        "api_model_id": "together_ai/mistralai/Mixtral-8x7B-Instruct-v0.1",
        "cost_per_1k_input": 0.0006,
        "cost_per_1k_output": 0.0006,
        "provider": "together_ai",
    },
    # Together AI — lightweight fast model
    "llama-3.3-70b": {
        "api_model_id": "together_ai/meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "cost_per_1k_input": 0.00088,
        "cost_per_1k_output": 0.00088,
        "provider": "together_ai",
    },
    # Anthropic — requires ANTHROPIC_API_KEY
    "claude-haiku-4-5-20251001": {
        "api_model_id": "anthropic/claude-haiku-4-5-20251001",
        "cost_per_1k_input": 0.00025,
        "cost_per_1k_output": 0.00125,
        "provider": "anthropic",
    },
    # ── FRONTIER MODELS ──────────────────────────────────────────────────────

    # Tier 1: Deep Reasoning — RL-trained, produces <think> chain-of-thought
    "deepseek-r1": {
        "api_model_id": "together_ai/deepseek-ai/DeepSeek-R1",
        "cost_per_1k_input": 0.003,
        "cost_per_1k_output": 0.007,
        "provider": "together_ai",
        "supports_thinking": True,   # Response contains <think>…</think> blocks
    },

    # Tier 1 (efficient): Strong reasoning without explicit thinking tokens
    "deepseek-v3": {
        "api_model_id": "together_ai/deepseek-ai/DeepSeek-V3.1",
        "cost_per_1k_input": 0.0004,
        "cost_per_1k_output": 0.0004,
        "provider": "together_ai",
    },

    # Tier 2: Dense transformer — architecturally distinct from R1/Qwen MoE models
    "llama-3.3-70b": {
        "api_model_id": "together_ai/meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "cost_per_1k_input": 0.00088,
        "cost_per_1k_output": 0.00088,
        "provider": "together_ai",
    },

    # Tier 3: Fast dense classifier — small model, ultra-low latency binary decisions
    "qwen2.5-7b": {
        "api_model_id": "together_ai/Qwen/Qwen2.5-7B-Instruct-Turbo",
        "cost_per_1k_input": 0.0003,
        "cost_per_1k_output": 0.0003,
        "provider": "together_ai",
    },

    # Legacy entries for back-compat (dedicated endpoints — not serverless)
    "llama-4-maverick": {
        "api_model_id": "together_ai/meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
        "cost_per_1k_input": 0.00027,
        "cost_per_1k_output": 0.00085,
        "provider": "together_ai",
        "dedicated_endpoint_required": True,
    },
    "llama-3.1-8b": {
        "api_model_id": "together_ai/meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",
        "cost_per_1k_input": 0.00018,
        "cost_per_1k_output": 0.00018,
        "provider": "together_ai",
        "dedicated_endpoint_required": True,
    },
}


def _calculate_cost(model_info: dict, tokens_input: int, tokens_output: int) -> float:
    """Calculate USD cost from token counts using the model registry pricing."""
    cost = (
        tokens_input / 1000.0 * model_info["cost_per_1k_input"]
        + tokens_output / 1000.0 * model_info["cost_per_1k_output"]
    )
    return round(cost, 8)


# ---------------------------------------------------------------------------
# Lightweight news item proxy for prompt building
# ---------------------------------------------------------------------------


@dataclass
class _SimpleNewsItem:
    """Minimal news item for passing to NewsAgent.build_user_prompt().

    Matches the attributes accessed by NewsAgent (headline, source,
    published_at, summary). Avoids importing src.data.news_client.
    """

    headline: str
    source: str
    published_at: datetime
    summary: str


# ---------------------------------------------------------------------------
# Live prompt builders (one per agent type)
# ---------------------------------------------------------------------------


def _build_live_prompt(
    scenario: LabeledScenario, config: AgentConfig
) -> tuple[str, str]:
    """Build (system_prompt, user_prompt) for a live API call.

    Dispatches to the appropriate agent-type builder. Uses the actual
    production agent classes so the arena tests the real prompt.
    """
    agent_type = config.agent_type
    if agent_type in ("news", "news_agent"):
        return _build_news_live_prompt(scenario, config)
    raise ValueError(
        f"Live prompt builder not implemented for agent_type={agent_type!r}. "
        f"Currently supported: 'news', 'news_agent'."
    )


def _build_news_live_prompt(
    scenario: LabeledScenario, config: AgentConfig
) -> tuple[str, str]:
    """Build news_agent system + user prompt from a LabeledScenario.

    Applies the D169 pre-filter (same as production) then delegates to
    the real NewsAgent prompt methods so the arena tests the exact prompt
    used in production.
    """
    # Lazy import to avoid circular deps and keep harness lightweight
    from src.agents.news_agent import NewsAgent, _filter_promotional_headlines  # type: ignore[attr-defined]

    # Convert scenario headlines → _SimpleNewsItem objects
    news_items: list[_SimpleNewsItem] = []
    for h in scenario.premarket_headlines:
        if isinstance(h, dict):
            headline = h.get("headline", "")
            source = h.get("source", "unknown")
            summary = h.get("summary", "") or ""
            ts_str = h.get("published_at")
        else:
            headline = str(h)
            source = "unknown"
            summary = ""
            ts_str = None

        if ts_str:
            try:
                published_at = datetime.fromisoformat(str(ts_str))
            except (ValueError, TypeError):
                published_at = datetime.now(timezone.utc)
        else:
            published_at = (
                datetime.combine(scenario.date, datetime.min.time(), tzinfo=timezone.utc)
                if scenario.date
                else datetime.now(timezone.utc)
            )

        if headline:
            news_items.append(
                _SimpleNewsItem(
                    headline=headline,
                    source=source,
                    published_at=published_at,
                    summary=summary,
                )
            )

    # Apply D169 pre-filter (same as production pipeline)
    filtered = _filter_promotional_headlines(news_items, scenario.ticker)  # type: ignore[arg-type]

    # Use a throwaway NewsAgent instance just to call its prompt methods
    agent = NewsAgent(model="dummy", provider="")
    user_prompt = agent.build_user_prompt(
        ticker=scenario.ticker,
        company_name=scenario.ticker,
        news_items=filtered,  # type: ignore[arg-type]
        market_cap=scenario.dollar_volume,
        sector="Unknown",
    )
    return agent.system_prompt, user_prompt


# ---------------------------------------------------------------------------
# Live response parser
# ---------------------------------------------------------------------------


def _parse_live_response(
    scenario: LabeledScenario,
    config: "AgentConfig",
    raw_content: str,
    latency_ms: float,
    tokens_input: int,
    tokens_output: int,
    cost_usd: float,
    now: datetime,
) -> "AgentRunResult":
    """Parse raw LLM JSON response into an AgentRunResult."""
    import re

    def _extract_json(text: str) -> dict | None:
        # Strategy 1: strip markdown code fences
        if "```" in text:
            m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(1).strip())
                except (json.JSONDecodeError, ValueError):
                    pass

        # Strategy 2: direct parse
        try:
            return json.loads(text.strip())
        except (json.JSONDecodeError, ValueError):
            pass

        # Strategy 3: find outermost JSON object
        try:
            start = text.index("{")
            end = text.rindex("}") + 1
            return json.loads(text[start:end])
        except (ValueError, json.JSONDecodeError):
            pass

        return None

    raw = _extract_json(raw_content) if raw_content else None

    if raw is None:
        # Strategy 4: regex field extraction
        raw = {}

        for pat in [
            r'"signal"\s*:\s*"([^"]+)"',
            r'"signal_direction"\s*:\s*"([^"]+)"',
            r'signal[:\s]+(\w+)',
            r'\b(BULL|BEAR|NEUTRAL|STRONG_BULL|STRONG_BEAR)\b',
        ]:
            m = re.search(pat, raw_content or "", re.IGNORECASE)
            if m:
                raw["signal"] = m.group(1).upper()
                break

        for pat in [r'"confidence"\s*:\s*([\d.]+)', r'confidence[:\s]+([\d.]+)']:
            m = re.search(pat, raw_content or "", re.IGNORECASE)
            if m:
                raw["confidence"] = float(m.group(1))
                break

        for pat in [r'"catalyst_type"\s*:\s*"([^"]+)"', r'catalyst[_\s]type[:\s]+(\w+)']:
            m = re.search(pat, raw_content or "", re.IGNORECASE)
            if m:
                raw["catalyst_type"] = m.group(1)
                break

        for pat in [
            r'"reasoning"\s*:\s*"((?:[^"\\]|\\.)*)"',
            r'"key_reasoning"\s*:\s*"((?:[^"\\]|\\.)*)"',
        ]:
            m = re.search(pat, raw_content or "", re.IGNORECASE)
            if m:
                raw["reasoning"] = m.group(1)
                break

        if not raw:
            # Strategy 5: return raw text as reasoning with NEUTRAL signal
            raw = {"reasoning": (raw_content or "")[:500], "signal": "NEUTRAL", "confidence": 0.0}

    # Extract fields from parsed dict
    signal_raw = raw.get("signal") or raw.get("signal_direction")
    signal_direction = signal_raw.upper() if isinstance(signal_raw, str) else None

    confidence = raw.get("confidence")
    reasoning = (
        raw.get("key_reasoning")
        or raw.get("reasoning")
        or raw.get("rationale")
    )
    catalyst_type = raw.get("catalyst_type")
    parse_success = signal_direction in (
        "BULL", "STRONG_BULL", "BEAR", "STRONG_BEAR", "NEUTRAL"
    )

    return AgentRunResult(
        scenario_id=scenario.scenario_id,
        agent_config=config,
        signal_direction=signal_direction,
        signal_confidence=float(confidence) if confidence is not None else None,
        reasoning=str(reasoning) if reasoning else None,
        catalyst_type=str(catalyst_type) if catalyst_type else None,
        raw_output=raw,
        latency_ms=latency_ms,
        tokens_input=tokens_input,
        tokens_output=tokens_output,
        cost_usd=cost_usd,
        timed_out=False,
        parse_success=parse_success,
        error=None if parse_success else f"Unrecognised signal: {signal_direction!r}",
        timestamp=now,
    )


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class AgentConfig:
    """Configuration for an agent run."""

    agent_type: str  # "news", "fundamental", "technical", "risk", "manipulation"
    model_id: str    # "mixtral-8x7b", "qwen3-235b", etc.
    prompt_template: Optional[str] = None   # Override default prompt
    timeout_seconds: float = 25.0
    temperature: float = 0.0
    max_tokens: int = 1024
    extra_params: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "agent_type": self.agent_type,
            "model_id": self.model_id,
            "prompt_template": self.prompt_template,
            "timeout_seconds": self.timeout_seconds,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "extra_params": self.extra_params,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AgentConfig":
        return cls(
            agent_type=d["agent_type"],
            model_id=d["model_id"],
            prompt_template=d.get("prompt_template"),
            timeout_seconds=d.get("timeout_seconds", 25.0),
            temperature=d.get("temperature", 0.0),
            max_tokens=d.get("max_tokens", 1024),
            extra_params=d.get("extra_params", {}),
        )


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class AgentRunResult:
    """Result from running an agent against one scenario."""

    # Identity
    scenario_id: str
    agent_config: AgentConfig
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # Agent output
    signal_direction: Optional[str] = None    # BULL, BEAR, NEUTRAL, STRONG_BULL, STRONG_BEAR
    signal_confidence: Optional[float] = None
    reasoning: Optional[str] = None
    catalyst_type: Optional[str] = None       # For news agent
    raw_output: Optional[dict] = None         # Full parsed output dict

    # Operational metrics
    latency_ms: float = 0.0
    tokens_input: int = 0
    tokens_output: int = 0
    cost_usd: float = 0.0
    timed_out: bool = False
    parse_success: bool = False
    error: Optional[str] = None

    # Accuracy (filled by scoring, not harness)
    direction_correct: Optional[bool] = None
    catalyst_correct: Optional[bool] = None

    # Thinking-mode models (e.g. DeepSeek-R1) emit a chain-of-thought block
    # before their JSON answer. We log it here for analysis.
    thinking_block: Optional[str] = None

    timestamp: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "scenario_id": self.scenario_id,
            "agent_config": self.agent_config.to_dict(),
            "run_id": self.run_id,
            "signal_direction": self.signal_direction,
            "signal_confidence": self.signal_confidence,
            "reasoning": self.reasoning,
            "catalyst_type": self.catalyst_type,
            "raw_output": self.raw_output,
            "latency_ms": self.latency_ms,
            "tokens_input": self.tokens_input,
            "tokens_output": self.tokens_output,
            "cost_usd": self.cost_usd,
            "timed_out": self.timed_out,
            "parse_success": self.parse_success,
            "error": self.error,
            "direction_correct": self.direction_correct,
            "catalyst_correct": self.catalyst_correct,
            "thinking_block": self.thinking_block,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AgentRunResult":
        ts = None
        if d.get("timestamp"):
            ts = datetime.fromisoformat(d["timestamp"])
        return cls(
            scenario_id=d["scenario_id"],
            agent_config=AgentConfig.from_dict(d["agent_config"]),
            run_id=d.get("run_id", str(uuid.uuid4())),
            signal_direction=d.get("signal_direction"),
            signal_confidence=d.get("signal_confidence"),
            reasoning=d.get("reasoning"),
            catalyst_type=d.get("catalyst_type"),
            raw_output=d.get("raw_output"),
            latency_ms=d.get("latency_ms", 0.0),
            tokens_input=d.get("tokens_input", 0),
            tokens_output=d.get("tokens_output", 0),
            cost_usd=d.get("cost_usd", 0.0),
            timed_out=d.get("timed_out", False),
            parse_success=d.get("parse_success", False),
            error=d.get("error"),
            direction_correct=d.get("direction_correct"),
            catalyst_correct=d.get("catalyst_correct"),
            thinking_block=d.get("thinking_block"),
            timestamp=ts,
        )


# ---------------------------------------------------------------------------
# Agent-type → production agent_id mapping
# ---------------------------------------------------------------------------

_AGENT_TYPE_TO_ID = {
    "news": "news_agent",
    "fundamental": "fundamental_agent",
    "technical": "technical_agent",
    "risk": "risk_agent",
    "manipulation": "manipulation_classifier",
    "institutional": "institutional_agent",
    "deep_search": "deep_search_agent",
}

_SUPPORTED_AGENT_TYPES = set(_AGENT_TYPE_TO_ID.keys())


def _resolve_agent_id(agent_type: str) -> str:
    """Map short agent_type ('news') → production agent_id ('news_agent')."""
    # Accept both short names and full IDs
    if agent_type in _AGENT_TYPE_TO_ID:
        return _AGENT_TYPE_TO_ID[agent_type]
    # Already a full agent_id
    if agent_type in _AGENT_TYPE_TO_ID.values():
        return agent_type
    return agent_type


# ---------------------------------------------------------------------------
# Input preparation helpers
# ---------------------------------------------------------------------------


def _prepare_news_input(scenario: LabeledScenario) -> dict:
    """Convert scenario to news_agent input format."""
    # news_agent.build_user_prompt kwargs:
    #   ticker, company_name, news_items, market_cap, sector
    news_items = []
    for headline in scenario.premarket_headlines:
        if isinstance(headline, dict):
            news_items.append(headline)
        else:
            news_items.append({
                "headline": str(headline),
                "source": "unknown",
                "published_at": scenario.date.isoformat() if scenario.date else None,
                "summary": "",
            })

    return {
        "ticker": scenario.ticker,
        "company_name": scenario.ticker,  # LabeledScenario doesn't store company_name
        "news_items": news_items,
        "market_cap": scenario.dollar_volume,  # Best proxy available in scenario
        "sector": "Unknown",
    }


def _prepare_fundamental_input(scenario: LabeledScenario) -> dict:
    """Convert scenario to fundamental_agent input format."""
    # fundamental_agent.build_user_prompt kwargs:
    #   ticker, float_shares, shares_outstanding, short_interest,
    #   insider_ownership_pct, institutional_ownership_pct, recent_filings
    recent_filings = []
    for filing in scenario.sec_filings:
        if isinstance(filing, dict):
            recent_filings.append(filing)
        else:
            recent_filings.append({
                "form": str(filing),
                "description": str(filing),
                "date": scenario.date.isoformat() if scenario.date else None,
            })

    return {
        "ticker": scenario.ticker,
        "float_shares": None,
        "shares_outstanding": None,
        "short_interest": None,
        "insider_ownership_pct": None,
        "institutional_ownership_pct": None,
        "recent_filings": recent_filings,
    }


def _prepare_technical_input(scenario: LabeledScenario) -> dict:
    """Convert scenario to technical_agent input format.

    Technical agent needs OHLCV bar data which isn't stored in LabeledScenario.
    Returns the available scalars; price_data/indicators will be empty.
    """
    return {
        "ticker": scenario.ticker,
        "price_data": {},        # Not available in LabeledScenario
        "indicators": {},        # Not available in LabeledScenario
        "current_price": scenario.open_price,
        "rvol": scenario.rvol,
        "vwap": scenario.open_price,  # Best proxy; actual VWAP not stored
    }


def _prepare_manipulation_input(scenario: LabeledScenario) -> dict:
    """Convert scenario to manipulation_classifier input format."""
    # manipulation_classifier.build_user_prompt kwargs:
    #   ticker, news_items, rvol, gap_pct, premarket_volume, float_shares,
    #   filing_summary, sec_filings
    news_items = []
    for headline in scenario.premarket_headlines:
        if isinstance(headline, dict):
            news_items.append(headline)
        else:
            news_items.append({
                "headline": str(headline),
                "source": "unknown",
                "published_at": scenario.date.isoformat() if scenario.date else None,
            })

    sec_filings_raw = []
    has_424b5 = False
    for filing in scenario.sec_filings:
        if isinstance(filing, dict):
            sec_filings_raw.append(filing)
            form = filing.get("form", "")
        else:
            form = str(filing)
            sec_filings_raw.append({"form": form, "date": None, "description": form})
        if "424B5" in form.upper():
            has_424b5 = True

    filing_summary = {
        "s3_age_days": None,
        "has_424b5_same_day": has_424b5,
        "insider_sell_count_30d": 0,
        "dilution_filing_count": sum(
            1 for f in scenario.sec_filings
            if isinstance(f, str) and ("S-3" in f or "424B5" in f)
        ),
        "recent_8k_count": 0,
    }

    return {
        "ticker": scenario.ticker,
        "news_items": news_items,
        "rvol": scenario.rvol,
        "gap_pct": scenario.gap_pct,
        "premarket_volume": None,
        "float_shares": None,
        "filing_summary": filing_summary,
        "sec_filings": sec_filings_raw,
    }


def _prepare_risk_input(scenario: LabeledScenario) -> dict:
    """Convert scenario to risk_agent input format."""
    # risk_agent.build_user_prompt kwargs:
    #   ticker, candidate_signals, market_data, sec_filings
    sec_filings_raw = []
    for filing in scenario.sec_filings:
        if isinstance(filing, dict):
            sec_filings_raw.append(filing)
        else:
            sec_filings_raw.append({"form": str(filing), "date": None, "description": str(filing)})

    market_data = {
        "current_price": scenario.open_price,
        "bid": None,
        "ask": None,
        "rvol": scenario.rvol,
        "spread_pct": None,
        "float_shares": None,
        "gap_pct": scenario.gap_pct,
        "has_news": bool(scenario.premarket_headlines),
        "halt_count": 0,
        "avg_daily_volume": None,
    }

    return {
        "ticker": scenario.ticker,
        "candidate_signals": [],  # No prior signals available in replay
        "market_data": market_data,
        "sec_filings": {"filings": sec_filings_raw},
    }


def _prepare_institutional_input(scenario: LabeledScenario) -> dict:
    """Convert scenario to institutional_agent input format."""
    return {
        "ticker": scenario.ticker,
        "options_data": {},
        "dark_pool_data": {},
        "insider_trades": [],
        "rvol": scenario.rvol,
        "gex_data": None,
    }


_INPUT_PREPARERS = {
    "news": _prepare_news_input,
    "news_agent": _prepare_news_input,
    "fundamental": _prepare_fundamental_input,
    "fundamental_agent": _prepare_fundamental_input,
    "technical": _prepare_technical_input,
    "technical_agent": _prepare_technical_input,
    "manipulation": _prepare_manipulation_input,
    "manipulation_classifier": _prepare_manipulation_input,
    "risk": _prepare_risk_input,
    "risk_agent": _prepare_risk_input,
    "institutional": _prepare_institutional_input,
    "institutional_agent": _prepare_institutional_input,
}


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class AgentHarness:
    """Runs agents against labeled scenarios in isolation.

    Replay mode (free, instant, deterministic):
        Uses actual_agent_signals stored in each LabeledScenario.
        Works with all 509 existing scenarios without any API calls.

    Live mode (Component 3 — not yet implemented):
        Calls LLM API with scenario inputs.
        Raises NotImplementedError.
    """

    def __init__(self, data_dir: str):
        self._data_dir = data_dir
        self._results: list[AgentRunResult] = []

        results_dir = os.path.join(data_dir, "results")
        os.makedirs(results_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # Input preparation
    # ------------------------------------------------------------------

    def prepare_input(self, scenario: LabeledScenario, agent_type: str) -> dict:
        """Convert a LabeledScenario into the input format the agent expects.

        Mimics what the production pipeline feeds the agent.
        """
        preparer = _INPUT_PREPARERS.get(agent_type)
        if preparer is None:
            raise ValueError(
                f"Unknown agent_type '{agent_type}'. "
                f"Supported: {sorted(_INPUT_PREPARERS.keys())}"
            )
        return preparer(scenario)

    # ------------------------------------------------------------------
    # Run single
    # ------------------------------------------------------------------

    def run_single(
        self,
        scenario: LabeledScenario,
        config: AgentConfig,
        *,
        mode: str = "replay",
    ) -> AgentRunResult:
        """Run one agent against one scenario.

        Args:
            scenario: The labeled scenario to evaluate.
            config:   Agent configuration.
            mode:     "replay" (default) or "live".

        Returns:
            AgentRunResult populated with the agent's response.
        """
        if mode == "live":
            result = asyncio.run(self._run_live_single_async(scenario, config))
            self._results.append(result)
            return result
        result = self._run_replay_single(scenario, config)
        self._results.append(result)
        return result

    # ------------------------------------------------------------------
    # Batch run
    # ------------------------------------------------------------------

    def run_batch(
        self,
        scenarios: list[LabeledScenario],
        config: AgentConfig,
        max_concurrent: int = 5,
        *,
        mode: str = "replay",
        rate_limit_delay: float = 0.5,
        progress_callback=None,
    ) -> list[AgentRunResult]:
        """Run an agent against multiple scenarios.

        In live mode, calls are made sequentially with an optional delay
        between requests to stay within provider rate limits.

        Args:
            scenarios:          Scenarios to evaluate.
            config:             Agent configuration.
            max_concurrent:     Reserved for future async batching.
            mode:               "replay" or "live".
            rate_limit_delay:   Seconds to wait between live API calls (default 0.5).
            progress_callback:  Optional callable(done, total) for progress updates.
        """
        if mode == "live":
            return self._run_live_batch(
                scenarios, config, rate_limit_delay, progress_callback
            )
        results = []
        for scenario in scenarios:
            result = self._run_replay_single(scenario, config)
            results.append(result)
        self._results.extend(results)
        return results

    # ------------------------------------------------------------------
    # Live execution
    # ------------------------------------------------------------------

    async def _run_live_single_async(
        self,
        scenario: LabeledScenario,
        config: AgentConfig,
    ) -> AgentRunResult:
        """Make a real LLM API call for one scenario. Returns AgentRunResult."""
        import litellm  # lazy import — only needed in live mode

        now = datetime.now(timezone.utc)

        # Resolve model from registry
        model_info = MODEL_REGISTRY.get(config.model_id)
        if model_info is None:
            return AgentRunResult(
                scenario_id=scenario.scenario_id,
                agent_config=config,
                parse_success=False,
                error=(
                    f"Unknown model_id {config.model_id!r}. "
                    f"Available: {sorted(MODEL_REGISTRY)}"
                ),
                timestamp=now,
            )

        api_model = model_info["api_model_id"]

        # Build prompt from scenario
        try:
            system_prompt, user_prompt = _build_live_prompt(scenario, config)
        except Exception as exc:
            return AgentRunResult(
                scenario_id=scenario.scenario_id,
                agent_config=config,
                parse_success=False,
                error=f"Prompt build failed: {exc}",
                timestamp=now,
            )

        # Thinking models (e.g. DeepSeek-R1) don't support response_format=json_object
        # and produce longer outputs — give them more time and skip JSON mode.
        supports_thinking = model_info.get("supports_thinking", False)
        call_kwargs: dict = {
            "model": api_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "max_retries": 0,
        }
        if not supports_thinking:
            call_kwargs["response_format"] = {"type": "json_object"}

        # Thinking models need a longer timeout for chain-of-thought
        effective_timeout = config.timeout_seconds
        if supports_thinking and effective_timeout < 90.0:
            effective_timeout = 90.0

        start = time.monotonic()
        try:
            response = await asyncio.wait_for(
                litellm.acompletion(**call_kwargs),
                timeout=effective_timeout,
            )
        except asyncio.TimeoutError:
            latency_ms = (time.monotonic() - start) * 1000
            return AgentRunResult(
                scenario_id=scenario.scenario_id,
                agent_config=config,
                latency_ms=latency_ms,
                timed_out=True,
                parse_success=False,
                error=f"Timed out after {effective_timeout}s",
                timestamp=now,
            )
        except Exception as exc:
            latency_ms = (time.monotonic() - start) * 1000
            return AgentRunResult(
                scenario_id=scenario.scenario_id,
                agent_config=config,
                latency_ms=latency_ms,
                parse_success=False,
                error=str(exc)[:500],
                timestamp=now,
            )

        latency_ms = (time.monotonic() - start) * 1000

        # Extract token usage
        usage = getattr(response, "usage", None)
        tokens_input = getattr(usage, "prompt_tokens", 0) or 0
        tokens_output = getattr(usage, "completion_tokens", 0) or 0
        cost_usd = _calculate_cost(model_info, tokens_input, tokens_output)

        raw_content = response.choices[0].message.content or ""

        # For thinking models, extract the <think>…</think> reasoning block.
        # The JSON answer lives after the closing tag.
        thinking_block: str | None = None
        if supports_thinking and "<think>" in raw_content:
            import re as _re
            m = _re.search(r"<think>(.*?)</think>(.*)", raw_content, _re.DOTALL)
            if m:
                thinking_block = m.group(1).strip()
                raw_content = m.group(2).strip()  # answer portion only

        result = _parse_live_response(
            scenario=scenario,
            config=config,
            raw_content=raw_content,
            latency_ms=latency_ms,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            cost_usd=cost_usd,
            now=now,
        )
        result.thinking_block = thinking_block
        return result

    def _run_live_batch(
        self,
        scenarios: list[LabeledScenario],
        config: AgentConfig,
        rate_limit_delay: float,
        progress_callback,
    ) -> list[AgentRunResult]:
        """Run live calls sequentially to avoid rate limit errors."""
        results: list[AgentRunResult] = []
        total = len(scenarios)
        for i, scenario in enumerate(scenarios):
            result = asyncio.run(self._run_live_single_async(scenario, config))
            results.append(result)
            if progress_callback:
                progress_callback(i + 1, total)
            # Rate limit delay between calls (skip after last)
            if rate_limit_delay > 0 and i < total - 1:
                time.sleep(rate_limit_delay)
        self._results.extend(results)
        return results

    # ------------------------------------------------------------------
    # Replay
    # ------------------------------------------------------------------

    def run_replay(self, scenarios: list[LabeledScenario]) -> list[AgentRunResult]:
        """Replay mode: extracts all stored agent signals from each scenario.

        Returns one AgentRunResult per (scenario, agent_id) pair that has
        stored signal data. No API calls are made.
        """
        results = []
        for scenario in scenarios:
            if not scenario.actual_agent_signals:
                continue
            for agent_id, raw in scenario.actual_agent_signals.items():
                # Derive short agent_type from agent_id
                agent_type = agent_id  # use full id as type for config
                config = AgentConfig(
                    agent_type=agent_type,
                    model_id=raw.get("model_id", "unknown") if isinstance(raw, dict) else "unknown",
                )
                result = self._extract_replay_result(scenario, config, agent_id, raw)
                results.append(result)
        self._results.extend(results)
        return results

    # ------------------------------------------------------------------
    # Internal replay extraction
    # ------------------------------------------------------------------

    def _run_replay_single(
        self,
        scenario: LabeledScenario,
        config: AgentConfig,
    ) -> AgentRunResult:
        """Extract stored signal for one agent type from one scenario."""
        agent_id = _resolve_agent_id(config.agent_type)
        raw = scenario.actual_agent_signals.get(agent_id)

        if raw is None:
            # Try the short name as well
            raw = scenario.actual_agent_signals.get(config.agent_type)

        return self._extract_replay_result(scenario, config, agent_id, raw)

    def _extract_replay_result(
        self,
        scenario: LabeledScenario,
        config: AgentConfig,
        agent_id: str,
        raw: object,
    ) -> AgentRunResult:
        """Build an AgentRunResult from stored signal data."""
        now = datetime.now(timezone.utc)

        if raw is None or not isinstance(raw, dict):
            return AgentRunResult(
                scenario_id=scenario.scenario_id,
                agent_config=config,
                parse_success=False,
                error="No stored signal for agent",
                latency_ms=0.0,
                timestamp=now,
            )

        # Normalise signal direction (stored as e.g. "BULL" or "bull")
        signal_raw = raw.get("signal") or raw.get("signal_direction")
        signal_direction = signal_raw.upper() if isinstance(signal_raw, str) else None

        confidence = raw.get("confidence") or raw.get("signal_confidence")
        reasoning = raw.get("reasoning") or raw.get("key_reasoning") or raw.get("rationale")

        # catalyst_type is NewsSignal-specific; stored in key_data or top-level
        catalyst_type = raw.get("catalyst_type")
        if catalyst_type is None:
            key_data = raw.get("key_data", {})
            if isinstance(key_data, dict):
                catalyst_type = key_data.get("catalyst_type")

        parse_success = signal_direction is not None

        return AgentRunResult(
            scenario_id=scenario.scenario_id,
            agent_config=config,
            signal_direction=signal_direction,
            signal_confidence=float(confidence) if confidence is not None else None,
            reasoning=reasoning,
            catalyst_type=str(catalyst_type) if catalyst_type is not None else None,
            raw_output=raw,
            latency_ms=0.0,   # Replay is instant
            tokens_input=0,
            tokens_output=0,
            timed_out=False,
            parse_success=parse_success,
            error=None if parse_success else "Could not extract signal direction",
            timestamp=now,
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_results(self, experiment_name: str) -> str:
        """Save accumulated results to data/llm_arena/results/{experiment_name}.json.

        Returns the path written to.
        """
        results_dir = os.path.join(self._data_dir, "results")
        os.makedirs(results_dir, exist_ok=True)
        path = os.path.join(results_dir, f"{experiment_name}.json")

        payload = {
            "experiment_name": experiment_name,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "scenario_count": len({r.scenario_id for r in self._results}),
            "result_count": len(self._results),
            "results": [r.to_dict() for r in self._results],
        }

        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, default=str)

        return path

    def load_results(self, experiment_name: str) -> list[AgentRunResult]:
        """Load previously saved results from disk."""
        path = os.path.join(self._data_dir, "results", f"{experiment_name}.json")
        if not os.path.exists(path):
            raise FileNotFoundError(f"No results file found: {path}")

        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)

        results = [AgentRunResult.from_dict(d) for d in payload.get("results", [])]
        self._results = results
        return results

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def results(self) -> list[AgentRunResult]:
        """Return all results accumulated in this harness instance."""
        return list(self._results)

    def clear(self) -> None:
        """Reset accumulated results."""
        self._results = []
