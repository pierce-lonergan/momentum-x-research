"""Async runner — calls litellm against each (model × example) pair.

Concurrency: asyncio.Semaphore per provider (avoid rate limits).
Timeouts: per-call timeout, 1 retry on timeout, then ERROR record.
Failures: NEVER raise — every error becomes a ModelResponse with `error` set.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from src.model_arena.models import ModelEntry
from src.model_arena.tasks import Task, TaskExample, get_parser

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_S = 30.0
_DEFAULT_MAX_RETRIES = 1
_DEFAULT_PER_PROVIDER_CONCURRENCY = 5


@dataclass(frozen=True)
class ModelResponse:
    """One (model × example) call result."""
    sample_id: str
    model_key: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float
    cost_usd: float
    raw_response: str
    prediction: str | None
    truth: str
    correct: bool
    error: str | None = None


# ── Semaphore registry per provider ─────────────────────────────────────


_PROVIDER_SEMAPHORES: dict[str, asyncio.Semaphore] = {}


def _get_provider_semaphore(provider: str, max_concurrent: int) -> asyncio.Semaphore:
    """One semaphore per provider per process. Lazy-created."""
    if provider not in _PROVIDER_SEMAPHORES:
        _PROVIDER_SEMAPHORES[provider] = asyncio.Semaphore(max_concurrent)
    return _PROVIDER_SEMAPHORES[provider]


# ── Single call ──────────────────────────────────────────────────────────


async def _call_model(
    model: ModelEntry,
    task: Task,
    example: TaskExample,
    timeout_s: float,
) -> ModelResponse:
    """Make ONE litellm call. Returns a ModelResponse. NEVER raises."""
    import os
    import litellm

    parser = get_parser(task.parse_fn_name)
    user_prompt = task.render_user_prompt(example)

    # Bridge alternate env-var names: the project's .env uses
    # TOGETHER_AI_API_KEY but litellm expects TOGETHER_API_KEY.
    if model.provider == "together" and not os.environ.get("TOGETHER_API_KEY"):
        alt_key = os.environ.get("TOGETHER_AI_API_KEY") or os.environ.get("TOGETHERAI_API_KEY")
        if alt_key:
            os.environ["TOGETHER_API_KEY"] = alt_key

    t0 = time.perf_counter()
    try:
        response = await asyncio.wait_for(
            litellm.acompletion(
                model=model.api_model_id,
                messages=[
                    {"role": "system", "content": task.system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                max_tokens=128,
                timeout=timeout_s,
            ),
            timeout=timeout_s + 2.0,  # outer timeout slightly larger than inner
        )
    except asyncio.TimeoutError:
        return ModelResponse(
            sample_id=example.example_id,
            model_key=model.key,
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=(time.perf_counter() - t0) * 1000,
            cost_usd=0.0,
            raw_response="",
            prediction=None,
            truth=example.expected_output,
            correct=False,
            error=f"timeout after {timeout_s}s",
        )
    except Exception as e:
        return ModelResponse(
            sample_id=example.example_id,
            model_key=model.key,
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=(time.perf_counter() - t0) * 1000,
            cost_usd=0.0,
            raw_response="",
            prediction=None,
            truth=example.expected_output,
            correct=False,
            error=f"{type(e).__name__}: {e}",
        )

    elapsed_ms = (time.perf_counter() - t0) * 1000

    # Extract content
    try:
        raw = response.choices[0].message.content or ""
    except (AttributeError, IndexError, TypeError) as e:
        return ModelResponse(
            sample_id=example.example_id,
            model_key=model.key,
            prompt_tokens=0,
            completion_tokens=0,
            latency_ms=elapsed_ms,
            cost_usd=0.0,
            raw_response="",
            prediction=None,
            truth=example.expected_output,
            correct=False,
            error=f"response parse: {type(e).__name__}: {e}",
        )

    # Token usage
    try:
        usage = response.usage
        prompt_tokens = int(usage.prompt_tokens) if hasattr(usage, "prompt_tokens") else 0
        completion_tokens = int(usage.completion_tokens) if hasattr(usage, "completion_tokens") else 0
    except (AttributeError, TypeError):
        prompt_tokens, completion_tokens = 0, 0

    cost_usd = model.cost_for(prompt_tokens, completion_tokens)
    prediction = parser(raw, task.valid_outputs)
    correct = (prediction == example.expected_output)

    return ModelResponse(
        sample_id=example.example_id,
        model_key=model.key,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=elapsed_ms,
        cost_usd=cost_usd,
        raw_response=raw,
        prediction=prediction,
        truth=example.expected_output,
        correct=correct,
        error=None,
    )


async def _call_with_retry(
    model: ModelEntry,
    task: Task,
    example: TaskExample,
    timeout_s: float,
    max_retries: int,
    provider_sem: asyncio.Semaphore,
) -> ModelResponse:
    """Wrap _call_model with retry on timeout and provider semaphore."""
    last_resp: ModelResponse | None = None
    for attempt in range(max_retries + 1):
        async with provider_sem:
            resp = await _call_model(model, task, example, timeout_s)
        if resp.error is None or not resp.error.startswith("timeout"):
            return resp
        last_resp = resp
        # Retry on timeout
    return last_resp  # type: ignore[return-value]


# ── Public entry point ──────────────────────────────────────────────────


async def run_task(
    task: Task,
    models: list[ModelEntry],
    n_samples: int | None = None,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    max_retries: int = _DEFAULT_MAX_RETRIES,
    per_provider_concurrency: int = _DEFAULT_PER_PROVIDER_CONCURRENCY,
) -> list[ModelResponse]:
    """Run task across models × samples. Returns flat list of ModelResponses.

    Args:
        task: the loaded Task to run
        models: list of ModelEntry (already filtered for enabled / has-key)
        n_samples: optional cap on examples (default: all)
        timeout_s: per-call timeout
        max_retries: number of retries on timeout (default 1)
        per_provider_concurrency: max concurrent calls per provider
    """
    examples = task.examples[:n_samples] if n_samples else task.examples
    if not examples or not models:
        logger.warning("model_arena: nothing to run (examples=%d models=%d)",
                       len(examples), len(models))
        return []

    logger.info(
        "model_arena: running task=%s models=%d examples=%d (%d total calls, "
        "concurrency=%d/provider)",
        task.name, len(models), len(examples),
        len(models) * len(examples), per_provider_concurrency,
    )

    coros = []
    for m in models:
        sem = _get_provider_semaphore(m.provider, per_provider_concurrency)
        for ex in examples:
            coros.append(_call_with_retry(m, task, ex, timeout_s, max_retries, sem))

    t0 = time.perf_counter()
    responses = await asyncio.gather(*coros, return_exceptions=False)
    elapsed = time.perf_counter() - t0

    n_ok = sum(1 for r in responses if r.error is None)
    n_correct = sum(1 for r in responses if r.correct)
    logger.info(
        "model_arena: task complete in %.1fs — %d/%d responses ok, %d correct overall",
        elapsed, n_ok, len(responses), n_correct,
    )
    return list(responses)
