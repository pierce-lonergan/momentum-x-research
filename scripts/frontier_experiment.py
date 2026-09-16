"""Frontier model experiment — definitive multi-model comparison.

Experiment A: 6-model head-to-head (all models, all scenarios)
Experiment B: Architecturally diverse ensemble (DeepSeek-R1 + Llama-4-Maverick + Llama-3.1-8B)
Experiment C: Two-pass pipeline (fast 8B binary classifier → DeepSeek-R1 deep analysis)

Usage:
    python scripts/frontier_experiment.py [--limit N] [--skip-a] [--skip-b] [--skip-c]

Results saved to data/llm_arena/ensemble/ and printed to stdout.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Add repo root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from src.llm_arena.dataset import DatasetManager
from src.llm_arena.harness import AgentConfig, AgentHarness, AgentRunResult, MODEL_REGISTRY
from src.llm_arena.models import LabeledScenario


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DATA_DIR = "data/llm_arena"

EXP_A_MODELS = [
    "deepseek-r1",       # Tier 1: RL-trained chain-of-thought reasoner
    "deepseek-v3",       # Tier 1 (efficient): strong reasoning, no thinking tokens
    "llama-3.3-70b",     # Tier 2: dense transformer (architecturally distinct)
    "qwen2.5-7b",        # Tier 3: fast small classifier
    "mixtral-8x7b",      # Baseline: legacy production model
    "qwen3-235b",        # Baseline: current production model
]

EXP_B_MODELS = [
    "deepseek-r1",       # RL-trained chain-of-thought reasoner
    "llama-3.3-70b",     # Dense transformer (different training + architecture from R1/Qwen)
    "qwen3-235b",        # MoE architecture (different from dense Llama)
]

TWOPASS_SCREENER  = "qwen2.5-7b"    # Fast, small binary classifier
TWOPASS_ANALYZER  = "deepseek-r1"   # Deep reasoning with thinking mode

# Signals that count as directional (non-neutral)
_BULL_SIGNALS = {"BULL", "STRONG_BULL"}
_BEAR_SIGNALS = {"BEAR", "STRONG_BEAR"}
_NEUTRAL_SIGNALS = {"NEUTRAL"}


def _normalize_signal(s: str) -> str:
    s = (s or "NEUTRAL").upper().strip()
    if s in _BULL_SIGNALS:
        return "BULL"
    if s in _BEAR_SIGNALS:
        return "BEAR"
    return "NEUTRAL"


def _is_correct(predicted: Optional[str], correct: Optional[str]) -> Optional[bool]:
    """Direction accuracy: predicted signal matches the correct label.

    Collapses STRONG_BULL→BULL and STRONG_BEAR→BEAR for scoring.
    Returns None if correct signal is unknown.
    """
    if not correct or not predicted:
        return None
    p = _normalize_signal(predicted)
    c = _normalize_signal(correct)
    return p == c


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

@dataclass
class ModelScorecard:
    model_id: str
    total: int = 0
    scored: int = 0          # scenarios with known ground truth
    correct: int = 0
    parse_ok: int = 0
    timed_out: int = 0
    errors: int = 0
    total_latency_ms: float = 0.0
    total_cost_usd: float = 0.0
    signal_distribution: Counter = field(default_factory=Counter)
    false_positives: int = 0   # predicted BULL but actual was not runner
    false_negatives: int = 0   # predicted NEUTRAL/BEAR but actual was runner

    @property
    def accuracy(self) -> Optional[float]:
        return self.correct / self.scored if self.scored > 0 else None

    @property
    def median_latency_ms(self) -> float:
        return self.total_latency_ms / self.total if self.total > 0 else 0.0

    @property
    def cost_per_signal(self) -> float:
        return self.total_cost_usd / self.total if self.total > 0 else 0.0

    def add_result(self, result: AgentRunResult, correct_signal: Optional[str]) -> None:
        self.total += 1
        self.parse_ok += int(result.parse_success)
        self.timed_out += int(result.timed_out)
        self.errors += int(bool(result.error and not result.timed_out))
        self.total_latency_ms += result.latency_ms
        self.total_cost_usd += result.cost_usd

        if result.signal_direction:
            self.signal_distribution[_normalize_signal(result.signal_direction)] += 1

        is_correct = _is_correct(result.signal_direction, correct_signal)
        if is_correct is not None:
            self.scored += 1
            if is_correct:
                self.correct += 1

        # FP/FN vs runner outcome
        if correct_signal and result.signal_direction:
            pred = _normalize_signal(result.signal_direction)
            actual = _normalize_signal(correct_signal)
            if pred == "BULL" and actual != "BULL":
                self.false_positives += 1
            if pred != "BULL" and actual == "BULL":
                self.false_negatives += 1

    def summary_line(self) -> str:
        acc = f"{self.accuracy:.1%}" if self.accuracy is not None else "N/A"
        dist = dict(self.signal_distribution.most_common())
        return (
            f"  acc={acc:>6}  scored={self.scored}/{self.total}"
            f"  FP={self.false_positives}  FN={self.false_negatives}"
            f"  p50_lat={self.median_latency_ms:>7.0f}ms"
            f"  cost/sig=${self.cost_per_signal:.5f}"
            f"  parse={self.parse_ok}/{self.total}"
            f"  signals={dist}"
        )


def _score_results(
    results: list[AgentRunResult],
    scenarios_by_id: dict[str, LabeledScenario],
) -> dict[str, ModelScorecard]:
    """Score a list of AgentRunResult against ground truth. Returns per-model scorecards."""
    cards: dict[str, ModelScorecard] = {}
    for r in results:
        mid = r.agent_config.model_id
        if mid not in cards:
            cards[mid] = ModelScorecard(model_id=mid)
        scenario = scenarios_by_id.get(r.scenario_id)
        correct = scenario.correct_signal if scenario else None
        cards[mid].add_result(r, correct)
    return cards


# ---------------------------------------------------------------------------
# Binary prompt builder for Pass 1 (fast screener)
# ---------------------------------------------------------------------------

BINARY_SYSTEM_PROMPT = """\
You are a pre-market catalyst screener for a momentum trading desk.

Your ONLY job is to answer one question:
  Does this stock have a GENUINE institutional-quality catalyst today?

A genuine catalyst is something that could realistically cause sustained momentum
(e.g., FDA approval, earnings beat, real M&A news, contract award, partnership).
NOT a genuine catalyst: paid promotional articles, vague press releases, speculative
social media, short-squeeze narrative without real news, or unknown/unnamed sources.

Respond with valid JSON only. No preamble.
Schema:
{
  "has_catalyst": true | false,
  "catalyst_type": "pharma_deal | earnings | contract | fda | merger | sec_filing | promotional | squeeze | technical_breakout | unknown",
  "confidence": 0.0-1.0,
  "signal": "BULL | NEUTRAL",
  "reasoning": "1-sentence explanation"
}
"""


def _build_binary_prompt(scenario: LabeledScenario) -> str:
    """Build a compact binary screener prompt from a scenario."""
    headlines = []
    for h in (scenario.premarket_headlines or []):
        if isinstance(h, dict):
            hl = h.get("headline", "")
            src = h.get("source", "unknown")
            if hl:
                headlines.append(f"- [{src}] {hl}")
        elif h:
            headlines.append(f"- {h}")

    headline_block = "\n".join(headlines) if headlines else "(no headlines)"
    gap_str = f"{scenario.gap_pct:+.1f}%" if scenario.gap_pct is not None else "n/a"
    rvol_str = f"{scenario.rvol:.1f}x" if scenario.rvol is not None else "n/a"
    return (
        f"Ticker: {scenario.ticker}\n"
        f"Gap: {gap_str}  RVOL: {rvol_str}\n"
        f"Date: {scenario.date}\n\n"
        f"Pre-market headlines:\n{headline_block}\n\n"
        "Does this stock have a genuine catalyst? Respond with JSON."
    )


# ---------------------------------------------------------------------------
# Two-pass pipeline runner
# ---------------------------------------------------------------------------

async def _run_binary_pass(
    scenario: LabeledScenario,
    harness: AgentHarness,
) -> tuple[bool, float, float, float, Optional[str]]:
    """
    Run the fast binary screener (llama-3.1-8b) on one scenario.

    Returns: (has_catalyst, latency_ms, cost_usd, confidence, pass1_signal)
    """
    import litellm  # lazy

    model_info = MODEL_REGISTRY[TWOPASS_SCREENER]
    api_model = model_info["api_model_id"]
    system_prompt = BINARY_SYSTEM_PROMPT
    user_prompt = _build_binary_prompt(scenario)

    start = time.monotonic()
    try:
        response = await asyncio.wait_for(
            litellm.acompletion(
                model=api_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.0,
                max_tokens=256,
                response_format={"type": "json_object"},
                max_retries=0,
            ),
            timeout=15.0,
        )
    except Exception as exc:
        latency_ms = (time.monotonic() - start) * 1000
        print(f"    [Pass1 error] {scenario.scenario_id}: {exc}")
        return False, latency_ms, 0.0, 0.0, None

    latency_ms = (time.monotonic() - start) * 1000
    usage = getattr(response, "usage", None)
    tok_in = getattr(usage, "prompt_tokens", 0) or 0
    tok_out = getattr(usage, "completion_tokens", 0) or 0
    cost = (tok_in / 1000 * model_info["cost_per_1k_input"]
            + tok_out / 1000 * model_info["cost_per_1k_output"])

    content = response.choices[0].message.content or ""
    try:
        # Attempt to parse JSON directly
        if "```" in content:
            m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
            content = m.group(1).strip() if m else content
        parsed = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        # Try finding outermost braces
        try:
            start_idx = content.index("{")
            end_idx = content.rindex("}") + 1
            parsed = json.loads(content[start_idx:end_idx])
        except (ValueError, json.JSONDecodeError):
            parsed = {}

    has_catalyst = bool(parsed.get("has_catalyst", False))
    confidence = float(parsed.get("confidence", 0.5))
    pass1_signal = str(parsed.get("signal", "NEUTRAL")).upper()

    return has_catalyst, latency_ms, cost, confidence, pass1_signal


@dataclass
class TwoPassResult:
    scenario_id: str
    pass1_has_catalyst: bool
    pass1_signal: Optional[str]
    pass1_latency_ms: float
    pass1_cost_usd: float
    pass2_ran: bool
    pass2_signal: Optional[str]
    pass2_confidence: Optional[float]
    pass2_latency_ms: float
    pass2_cost_usd: float
    final_signal: str
    thinking_block: Optional[str]  # DeepSeek-R1 chain-of-thought

    @property
    def total_latency_ms(self) -> float:
        return self.pass1_latency_ms + self.pass2_latency_ms

    @property
    def total_cost_usd(self) -> float:
        return self.pass1_cost_usd + self.pass2_cost_usd

    def to_dict(self) -> dict:
        return {
            "scenario_id": self.scenario_id,
            "pass1_has_catalyst": self.pass1_has_catalyst,
            "pass1_signal": self.pass1_signal,
            "pass1_latency_ms": self.pass1_latency_ms,
            "pass1_cost_usd": self.pass1_cost_usd,
            "pass2_ran": self.pass2_ran,
            "pass2_signal": self.pass2_signal,
            "pass2_confidence": self.pass2_confidence,
            "pass2_latency_ms": self.pass2_latency_ms,
            "pass2_cost_usd": self.pass2_cost_usd,
            "final_signal": self.final_signal,
            "thinking_block_len": len(self.thinking_block) if self.thinking_block else 0,
        }


async def _run_twopass_one(
    scenario: LabeledScenario,
    harness: AgentHarness,
    rate_limit_delay: float = 0.5,
) -> TwoPassResult:
    """Run the two-pass pipeline for one scenario."""
    # Pass 1: fast binary screener
    has_catalyst, p1_lat, p1_cost, p1_conf, p1_signal = await _run_binary_pass(scenario, harness)

    p2_ran = False
    p2_signal: Optional[str] = None
    p2_conf: Optional[float] = None
    p2_lat = 0.0
    p2_cost = 0.0
    thinking_block: Optional[str] = None

    if has_catalyst:
        # Pass 2: deep reasoning with DeepSeek-R1
        if rate_limit_delay > 0:
            await asyncio.sleep(rate_limit_delay)
        config = AgentConfig(
            agent_type="news",
            model_id=TWOPASS_ANALYZER,
            max_tokens=2048,
            timeout_seconds=90.0,
        )
        result = await harness._run_live_single_async(scenario, config)
        p2_ran = True
        p2_signal = result.signal_direction
        p2_conf = result.signal_confidence
        p2_lat = result.latency_ms
        p2_cost = result.cost_usd
        thinking_block = result.thinking_block

    # Final signal: pass2 if it ran, else pass1
    final = p2_signal if (p2_ran and p2_signal) else (p1_signal or "NEUTRAL")

    return TwoPassResult(
        scenario_id=scenario.scenario_id,
        pass1_has_catalyst=has_catalyst,
        pass1_signal=p1_signal,
        pass1_latency_ms=p1_lat,
        pass1_cost_usd=p1_cost,
        pass2_ran=p2_ran,
        pass2_signal=p2_signal,
        pass2_confidence=p2_conf,
        pass2_latency_ms=p2_lat,
        pass2_cost_usd=p2_cost,
        final_signal=final,
        thinking_block=thinking_block,
    )


def run_twopass_batch(
    scenarios: list[LabeledScenario],
    harness: AgentHarness,
    rate_limit_delay: float = 0.8,
) -> list[TwoPassResult]:
    """Run two-pass pipeline sequentially on all scenarios."""
    results = []
    total = len(scenarios)
    for i, scenario in enumerate(scenarios):
        print(f"  [{i+1}/{total}] {scenario.scenario_id} ...", end="", flush=True)
        result = asyncio.run(_run_twopass_one(scenario, harness, rate_limit_delay))
        catalyst_flag = "CATALYST" if result.pass1_has_catalyst else "no-cat"
        think_len = len(result.thinking_block) if result.thinking_block else 0
        print(
            f" P1={result.pass1_signal}({catalyst_flag})"
            + (f" P2={result.pass2_signal} think={think_len}c" if result.pass2_ran else " [skipped P2]")
            + f" → {result.final_signal}"
            + f" {result.total_latency_ms:.0f}ms ${result.total_cost_usd:.5f}"
        )
        results.append(result)
        if rate_limit_delay > 0 and i < total - 1:
            time.sleep(rate_limit_delay)
    return results


# ---------------------------------------------------------------------------
# Experiment A: head-to-head runner
# ---------------------------------------------------------------------------

def run_experiment_a(
    scenarios: list[LabeledScenario],
    harness: AgentHarness,
    models: list[str],
    rate_limit_delay: float = 0.8,
) -> dict[str, list[AgentRunResult]]:
    """Run each model against every scenario sequentially. Returns model→results."""
    all_results: dict[str, list[AgentRunResult]] = {}
    total_calls = len(models) * len(scenarios)
    call_n = 0

    for model_id in models:
        results = []
        model_info = MODEL_REGISTRY.get(model_id)
        if model_info is None:
            print(f"  [SKIP] {model_id} — not in MODEL_REGISTRY")
            continue

        print(f"\n  -- Model: {model_id} ({len(scenarios)} scenarios) --")
        config = AgentConfig(
            agent_type="news",
            model_id=model_id,
            max_tokens=2048 if model_info.get("supports_thinking") else 1024,
            timeout_seconds=90.0 if model_info.get("supports_thinking") else 30.0,
        )

        for i, scenario in enumerate(scenarios):
            call_n += 1
            print(
                f"    [{call_n}/{total_calls}] {model_id} × {scenario.scenario_id} ...",
                end="", flush=True
            )
            result = asyncio.run(harness._run_live_single_async(scenario, config))
            think_len = len(result.thinking_block) if result.thinking_block else 0
            status = "OK" if result.parse_success else ("TIMEOUT" if result.timed_out else "ERR")
            print(
                f" {status} sig={result.signal_direction or '-'}"
                f" {result.latency_ms:.0f}ms ${result.cost_usd:.5f}"
                + (f" think={think_len}c" if think_len else "")
            )
            results.append(result)

            if rate_limit_delay > 0 and i < len(scenarios) - 1:
                time.sleep(rate_limit_delay)

        all_results[model_id] = results

    return all_results


# ---------------------------------------------------------------------------
# Experiment B: diverse ensemble voter
# ---------------------------------------------------------------------------

def _majority_vote(signals: list[str], require_min: int = 2) -> str:
    """Return majority vote from a list of normalised signals."""
    if len(signals) < require_min:
        return "NEUTRAL"
    counts = Counter(_normalize_signal(s) for s in signals if s)
    top = counts.most_common(1)
    return top[0][0] if top else "NEUTRAL"


def run_experiment_b(
    scenarios: list[LabeledScenario],
    harness: AgentHarness,
    models: list[str],
    rate_limit_delay: float = 0.8,
) -> list[dict]:
    """Run each model per scenario, then majority-vote the ensemble.

    Calls models sequentially (per scenario, per model) to respect rate limits.
    Returns list of per-scenario ensemble dicts.
    """
    # Collect results per model first (reuses run_experiment_a logic)
    per_model: dict[str, list[AgentRunResult]] = run_experiment_a(
        scenarios, harness, models, rate_limit_delay
    )

    # Build per-scenario ensemble dicts
    ensemble_results = []
    by_scenario: dict[str, dict[str, AgentRunResult]] = defaultdict(dict)
    for model_id, results in per_model.items():
        for r in results:
            by_scenario[r.scenario_id][model_id] = r

    for scenario in scenarios:
        sid = scenario.scenario_id
        model_responses = by_scenario.get(sid, {})
        signals = [
            r.signal_direction
            for r in model_responses.values()
            if r.signal_direction and r.parse_success
        ]
        normed = [_normalize_signal(s) for s in signals]
        agreement_count = Counter(normed).most_common(1)[0][1] if normed else 0
        voted = _majority_vote(signals)
        total_cost = sum(r.cost_usd for r in model_responses.values())
        max_latency = max((r.latency_ms for r in model_responses.values()), default=0.0)
        agreement_ratio = agreement_count / len(signals) if signals else 0.0

        ensemble_results.append({
            "scenario_id": sid,
            "voted_signal": voted,
            "model_signals": {m: r.signal_direction for m, r in model_responses.items()},
            "agreement_ratio": agreement_ratio,
            "models_responded": len(signals),
            "total_cost_usd": total_cost,
            "max_latency_ms": max_latency,
        })

    return ensemble_results


# ---------------------------------------------------------------------------
# Report printer
# ---------------------------------------------------------------------------

def _print_scorecard(cards: dict[str, ModelScorecard], title: str) -> None:
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")

    # Sort by accuracy desc
    sorted_models = sorted(
        cards.items(),
        key=lambda x: (x[1].accuracy or -1),
        reverse=True,
    )
    for model_id, card in sorted_models:
        print(f"\n  {model_id}")
        print(card.summary_line())

    print()


def _print_ensemble_summary(
    ensemble_results: list[dict],
    scenarios_by_id: dict[str, LabeledScenario],
    title: str,
) -> None:
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")

    total = len(ensemble_results)
    scored = correct = 0
    total_cost = 0.0
    total_lat = 0.0
    agreements = []
    signal_dist: Counter = Counter()

    for er in ensemble_results:
        scenario = scenarios_by_id.get(er["scenario_id"])
        correct_sig = scenario.correct_signal if scenario else None
        voted = er["voted_signal"]
        signal_dist[voted] += 1
        total_cost += er.get("total_cost_usd", 0.0)
        total_lat += er.get("max_latency_ms", 0.0)
        agreements.append(er.get("agreement_ratio", 0.0))

        is_correct = _is_correct(voted, correct_sig)
        if is_correct is not None:
            scored += 1
            if is_correct:
                correct += 1

    acc = f"{correct/scored:.1%}" if scored > 0 else "N/A"
    avg_agreement = sum(agreements) / len(agreements) if agreements else 0.0
    avg_lat = total_lat / total if total else 0.0
    avg_cost = total_cost / total if total else 0.0

    print(f"\n  Accuracy:        {acc} ({correct}/{scored} scored)")
    print(f"  Avg agreement:   {avg_agreement:.1%}")
    print(f"  Signal dist:     {dict(signal_dist.most_common())}")
    print(f"  Avg latency:     {avg_lat:.0f}ms (max across models)")
    print(f"  Avg cost/signal: ${avg_cost:.5f}")
    print(f"  Total cost:      ${total_cost:.4f}")
    print()


def _print_twopass_summary(
    tp_results: list[TwoPassResult],
    scenarios_by_id: dict[str, LabeledScenario],
) -> None:
    print(f"\n{'='*70}")
    print("  Experiment C: Two-Pass Pipeline Summary")
    print(f"{'='*70}")

    total = len(tp_results)
    pass2_count = sum(1 for r in tp_results if r.pass2_ran)
    scored = correct = 0
    total_cost = total_lat = 0.0
    signal_dist: Counter = Counter()

    for r in tp_results:
        scenario = scenarios_by_id.get(r.scenario_id)
        correct_sig = scenario.correct_signal if scenario else None
        signal_dist[_normalize_signal(r.final_signal)] += 1
        total_cost += r.total_cost_usd
        total_lat += r.total_latency_ms

        is_correct = _is_correct(r.final_signal, correct_sig)
        if is_correct is not None:
            scored += 1
            if is_correct:
                correct += 1

    acc = f"{correct/scored:.1%}" if scored > 0 else "N/A"
    avg_lat = total_lat / total if total else 0.0
    avg_cost = total_cost / total if total else 0.0

    thinking_lens = [len(r.thinking_block) for r in tp_results if r.thinking_block]
    avg_think_len = int(sum(thinking_lens) / len(thinking_lens)) if thinking_lens else 0

    print(f"\n  Accuracy:           {acc} ({correct}/{scored} scored)")
    print(f"  Pass-2 invocations: {pass2_count}/{total} ({pass2_count/total:.0%} of scenarios)")
    print(f"  Signal dist:        {dict(signal_dist.most_common())}")
    print(f"  Avg total latency:  {avg_lat:.0f}ms")
    print(f"  Avg cost/signal:    ${avg_cost:.5f}")
    print(f"  Total cost:         ${total_cost:.4f}")
    if thinking_lens:
        print(f"  Avg thinking chars: {avg_think_len:,}")
    print()


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _save_json(path: str, data: object) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, default=str)
    print(f"  [saved] {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Frontier model comparison experiment")
    parser.add_argument("--limit", type=int, default=0, help="Cap scenarios (0=all)")
    parser.add_argument("--skip-a", action="store_true", help="Skip Experiment A")
    parser.add_argument("--skip-b", action="store_true", help="Skip Experiment B")
    parser.add_argument("--skip-c", action="store_true", help="Skip Experiment C")
    parser.add_argument("--rate-limit-delay", type=float, default=0.8,
                        help="Seconds between API calls (default 0.8)")
    args = parser.parse_args()

    # ── Load dataset ────────────────────────────────────────────────────────
    print("\n[frontier_experiment] Loading dataset …")
    dm = DatasetManager(DATA_DIR)
    count = dm.load()
    scenarios = dm.all() if count > 0 else []
    if not scenarios:
        print("ERROR: No scenarios found. Run `python scripts/run_llm_arena.py seed` first.")
        sys.exit(1)

    if args.limit > 0:
        scenarios = scenarios[: args.limit]

    scenarios_by_id = {s.scenario_id: s for s in scenarios}
    print(f"  Loaded {len(scenarios)} scenarios")

    # Print ground truth distribution
    gt_dist = Counter(
        _normalize_signal(s.correct_signal)
        for s in scenarios
        if s.correct_signal
    )
    print(f"  Ground truth: {dict(gt_dist.most_common())}")

    harness = AgentHarness(DATA_DIR)

    ensemble_dir = os.path.join(DATA_DIR, "ensemble")
    os.makedirs(ensemble_dir, exist_ok=True)
    run_ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

    # ── Experiment A: Head-to-head ───────────────────────────────────────────
    if not args.skip_a:
        print(f"\n{'#'*70}")
        print("# EXPERIMENT A: 6-Model Head-to-Head")
        print(f"# {len(EXP_A_MODELS)} models × {len(scenarios)} scenarios")
        print(f"{'#'*70}")

        exp_a_results: dict[str, list[AgentRunResult]] = run_experiment_a(
            scenarios, harness, EXP_A_MODELS, args.rate_limit_delay
        )

        # Flatten for scoring
        all_flat = [r for results in exp_a_results.values() for r in results]
        scorecards = _score_results(all_flat, scenarios_by_id)
        _print_scorecard(scorecards, "Experiment A — Per-model Scorecards")

        # Save
        save_data = {
            "experiment": "A_headtohead",
            "run_at": run_ts,
            "models": EXP_A_MODELS,
            "scenario_count": len(scenarios),
            "results": {
                model_id: [r.to_dict() for r in results]
                for model_id, results in exp_a_results.items()
            },
            "scorecards": {
                mid: {
                    "accuracy": c.accuracy,
                    "correct": c.correct,
                    "scored": c.scored,
                    "total": c.total,
                    "false_positives": c.false_positives,
                    "false_negatives": c.false_negatives,
                    "parse_ok": c.parse_ok,
                    "median_latency_ms": c.median_latency_ms,
                    "cost_per_signal_usd": c.cost_per_signal,
                    "total_cost_usd": c.total_cost_usd,
                    "signal_distribution": dict(c.signal_distribution),
                }
                for mid, c in scorecards.items()
            },
        }
        _save_json(f"{ensemble_dir}/frontier_headtohead_{run_ts}.json", save_data)

    # ── Experiment B: Diverse ensemble ──────────────────────────────────────
    if not args.skip_b:
        print(f"\n{'#'*70}")
        print("# EXPERIMENT B: Architecturally Diverse Ensemble")
        print(f"# Models: {', '.join(EXP_B_MODELS)}")
        print(f"# {len(scenarios)} scenarios")
        print(f"{'#'*70}")

        exp_b_ensemble = run_experiment_b(
            scenarios, harness, EXP_B_MODELS, args.rate_limit_delay
        )
        _print_ensemble_summary(exp_b_ensemble, scenarios_by_id,
                                "Experiment B — Diverse Ensemble Results")

        _save_json(
            f"{ensemble_dir}/frontier_ensemble_{run_ts}.json",
            {
                "experiment": "B_diverse_ensemble",
                "run_at": run_ts,
                "models": EXP_B_MODELS,
                "scenario_count": len(scenarios),
                "ensemble_results": exp_b_ensemble,
            },
        )

    # ── Experiment C: Two-pass pipeline ─────────────────────────────────────
    if not args.skip_c:
        print(f"\n{'#'*70}")
        print("# EXPERIMENT C: Two-Pass Pipeline")
        print(f"# Pass 1: {TWOPASS_SCREENER} (binary screener)")
        print(f"# Pass 2: {TWOPASS_ANALYZER} (deep reasoning, only if catalyst detected)")
        print(f"# {len(scenarios)} scenarios")
        print(f"{'#'*70}")

        tp_results = run_twopass_batch(
            scenarios, harness, args.rate_limit_delay
        )
        _print_twopass_summary(tp_results, scenarios_by_id)

        _save_json(
            f"{ensemble_dir}/frontier_twopass_{run_ts}.json",
            {
                "experiment": "C_twopass_pipeline",
                "run_at": run_ts,
                "pass1_model": TWOPASS_SCREENER,
                "pass2_model": TWOPASS_ANALYZER,
                "scenario_count": len(scenarios),
                "results": [r.to_dict() for r in tp_results],
            },
        )

    print("\n[frontier_experiment] All experiments complete.")


if __name__ == "__main__":
    main()
