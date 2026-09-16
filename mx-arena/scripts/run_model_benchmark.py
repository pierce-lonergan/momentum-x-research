#!/usr/bin/env python3
"""D150: Comprehensive LLM Model Benchmark for Together AI Models.

Tests 23+ models against historical trading data to measure signal quality,
parse reliability, latency, and MFCS impact on trading performance.

Usage:
    # Dry run — show selected candidates
    python mx-arena/scripts/run_model_benchmark.py --dry-run

    # Quick test — 3 candidates, specific models
    python mx-arena/scripts/run_model_benchmark.py --candidates 3 --models "Qwen/Qwen3-Next-80B-A3B-Instruct,deepseek-ai/DeepSeek-V3.1"

    # Full benchmark
    python mx-arena/scripts/run_model_benchmark.py --output mx-arena/data/model_benchmark_results.json
"""

import argparse
import asyncio
import glob
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PROJECT = Path(__file__).resolve().parent.parent.parent
JOURNALS_DIR = PROJECT / "data" / "journals"

logger = logging.getLogger("model_benchmark")

# ── All Together AI models to benchmark ──────────────────────────────
BENCHMARK_MODELS = [
    # ── Tier 1: Flagship / Frontier ──
    "MiniMaxAI/MiniMax-M2.5",                              # 228K ctx, $0.30/$1.20
    "Qwen/Qwen3.5-397B-A17B",                              # 262K ctx, $0.60/$3.60
    "moonshotai/Kimi-K2.5",                                 # 262K ctx, $0.50/$2.80
    "zai-org/GLM-5",                                        # 202K ctx, $1.00/$3.20
    "deepseek-ai/DeepSeek-V3.1",                            # 128K ctx, $0.60/$1.70
    "deepseek-ai/DeepSeek-R1",                              # 163K ctx, $3.00/$7.00 (reasoning)
    "openai/gpt-oss-120b",                                  # 128K ctx, $0.15/$0.60
    "meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",   # 1M ctx, $0.27/$0.85
    "deepcogito/cogito-v2-1-671b",                          # 32K ctx, $1.25/$1.25
    # ── Tier 2: Strong Mid-Size ──
    "zai-org/GLM-4.7",                                      # 202K ctx, $0.45/$2.00
    "zai-org/GLM-4.5-Air-FP8",                              # 131K ctx, $0.20/$1.10
    "Qwen/Qwen3-Coder-Next-FP8",                            # 262K ctx, $0.50/$1.20
    "Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8",             # 256K ctx, $2.00/$2.00
    "Qwen/Qwen3-235B-A22B-Instruct-2507-tput",             # 262K ctx, $0.20/$0.60
    "meta-llama/Llama-3.3-70B-Instruct-Turbo",             # 131K ctx, $0.88/$0.88
    "mistralai/Mistral-Small-24B-Instruct-2501",            # 32K ctx, $0.10/$0.30
    "mistralai/Mixtral-8x7B-Instruct-v0.1",                # 32K ctx, $0.60/$0.60
    # ── Tier 3: Small / Budget ──
    "openai/gpt-oss-20b",                                   # 128K ctx, $0.05/$0.20
    "Qwen/Qwen3.5-9B",                                     # 262K ctx, $0.10/$0.15
    "Qwen/Qwen3-Next-80B-A3B-Instruct",                    # 262K ctx, $0.15/$1.50
    "Qwen/Qwen2.5-7B-Instruct-Turbo",                      # 32K ctx, $0.30/$0.30
    "google/gemma-3n-E4B-it",                               # 32K ctx, $0.02/$0.04
    "LiquidAI/LFM2-24B-A2B",                               # 32K ctx, $0.03/$0.12
    # ── Reasoning + Vision ──
    "Qwen/Qwen3-235B-A22B-Thinking-2507",                  # 262K ctx, $0.65/$3.00 (reasoning)
    "Qwen/Qwen3-VL-8B-Instruct",                           # 262K ctx, $0.18/$0.68 (vision)
    # ── Additional models discovered from API (139 total) ──
    "Qwen/Qwen3-Next-80B-A3B-Thinking",                    # 262K, $0.15/$1.50 (thinking variant)
    "Qwen/Qwen3.5-35B-A3B",                                # 262K, FREE
    "Qwen/Qwen3-30B-A3B",                                  # 40K, FREE
    "Qwen/Qwen3-8B",                                       # 40K, FREE
    "Qwen/Qwen3-4B-Instruct-2507",                         # 262K, FREE
    "Qwen/Qwen3-Coder-30B-A3B-Instruct",                   # 262K, FREE
    "Qwen/Qwen3.5-397B-A17B-FP8",                          # 262K, FREE (FP8 variant)
    "Qwen/Qwen3.5-9B-FP8",                                 # 262K, FREE (FP8 variant)
    "Qwen/Qwen2.5-72B-Instruct",                           # 32K, $1.20/$1.20
    "Qwen/Qwen2.5-14B-Instruct",                           # 32K, $0.80/$0.80
    "Qwen/QwQ-32B",                                         # 131K, $1.20/$1.20 (reasoning)
    "deepseek-ai/DeepSeek-V3-0324",                         # 163K, $1.25/$1.25
    "deepseek-ai/DeepSeek-V3.2",                            # 163K, FREE
    "deepseek-ai/DeepSeek-R1-Distill-Llama-70B",           # 131K, $2.00/$2.00
    "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B",            # 131K, $1.60/$1.60
    "deepseek-ai/DeepSeek-R1-Original",                     # 163K, FREE
    "moonshotai/Kimi-K2-Thinking",                          # 262K, $1.20/$4.00
    "zai-org/GLM-4.6",                                      # 202K, $0.60/$2.20
    "zai-org/GLM-5-FP4",                                    # 202K, FREE
    "MiniMaxAI/MiniMax-M2",                                 # 196K, FREE
    "MiniMaxAI/MiniMax-M2.1",                               # 196K, FREE
    "MiniMaxAI/MiniMax-M1-80k",                             # 1M, FREE
    "essentialai/rnj-1-instruct",                           # 32K, $0.15/$0.15
    "nvidia/Llama-3.1-Nemotron-70B-Instruct-HF",           # 32K, $0.88/$0.88
    "nvidia/NVIDIA-Nemotron-Nano-9B-v2",                    # 131K, $0.06/$0.25
    "meta-llama/Llama-4-Scout-17B-16E-Instruct",           # 1M, $0.18/$0.59
    "meta-llama/Meta-Llama-3.1-70B-Instruct-Turbo",        # 131K, $0.88/$0.88
    "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo",         # 131K, $0.18/$0.18
    "mistralai/Mixtral-8x22B-Instruct-v0.1",               # 65K, FREE
    "mistralai/Ministral-3-14B-Instruct-2512",              # 262K, $0.20/$0.20
    "google/gemma-2-27b-it",                                # 8K, $0.80/$0.80
    "deepcogito/cogito-v1-preview-llama-70B",               # 131K, FREE
    "deepcogito/cogito-v1-preview-qwen-32B",                # 131K, FREE
    "ServiceNow-AI/Apriel-1.6-15b-Thinker",                # 131K, FREE
    "NousResearch/Nous-Hermes-2-Mixtral-8x7B-DPO",         # 32K, $0.60/$0.60
]

# MFCS weights matching production (src/core/scoring.py)
DEFAULT_WEIGHTS = {
    "catalyst_news": 0.30,
    "technical": 0.20,
    "volume_rvol": 0.20,
    "fundamental": 0.15,
    "institutional": 0.10,
    "deep_search": 0.05,
}

AGENT_CATEGORY = {
    "news_agent": "catalyst_news",
    "fundamental_agent": "fundamental",
    "institutional_agent": "institutional",
    "deep_search_agent": "deep_search",
    "technical_agent": "technical",
    "manipulation_classifier": None,
}

SIGNAL_SCORES = {
    "STRONG_BULL": 1.0, "BULL": 0.6, "NEUTRAL": 0.0,
    "BEAR": -0.6, "STRONG_BEAR": -1.0,
}


# ── Data Classes ─────────────────────────────────────────────────────

@dataclass
class CallResult:
    """Result of a single model + agent + candidate LLM call."""
    model: str
    agent_id: str
    ticker: str
    date: str
    latency_ms: float = 0.0
    timed_out: bool = False
    json_parsed: bool = False
    schema_valid: bool = False
    signal: str = "NEUTRAL"
    confidence: float = 0.0
    reasoning: str = ""
    baseline_signal: str = "NEUTRAL"
    baseline_confidence: float = 0.0
    signal_agrees: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_cost: float = 0.0
    error: str = ""


@dataclass
class ModelScore:
    """Aggregate metrics for a model across all calls."""
    model: str
    total_calls: int = 0
    signal_accuracy: float = 0.0
    json_parse_rate: float = 0.0
    schema_valid_rate: float = 0.0
    avg_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    timeout_rate: float = 0.0
    confidence_separation: float = 0.0
    total_cost: float = 0.0
    composite_score: float = 0.0


@dataclass
class BenchmarkCandidate:
    """A journal entry selected for benchmarking."""
    ticker: str
    date: str
    action: str
    mfcs: float
    gap_pct: float
    rvol: float
    entry_price: float
    agent_signals: list[dict]
    input_data: dict
    was_profitable: bool = False


# ── JSON Extraction (from src/agents/base.py) ────────────────────────

def extract_json(raw: str) -> dict:
    """Extract JSON from LLM response, handling think blocks, markdown fences."""
    text = raw.strip()

    # Strip thinking blocks
    if "<think>" in text:
        think_end = text.rfind("</think>")
        if think_end != -1:
            text = text[think_end + len("</think>"):].strip()
        else:
            think_start = text.find("<think>")
            after = text[think_start + len("<think>"):]
            j = min(
                (after.find("{") if after.find("{") >= 0 else len(after)),
                (after.find("[") if after.find("[") >= 0 else len(after)),
            )
            if j < len(after):
                text = after[j:].strip()

    # Strip markdown fences
    if "```" in text:
        m = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
        if m:
            text = m.group(1).strip()

    # Try direct parse
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else (parsed[0] if isinstance(parsed, list) and parsed else {})
    except json.JSONDecodeError:
        pass

    # Regex fallback
    m = re.search(r"(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\})", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    return {}


# ── Candidate Selection ──────────────────────────────────────────────

def load_journal_entries() -> list[dict]:
    """Load all journal entries with agent signals."""
    entries = []
    for f in sorted(glob.glob(str(JOURNALS_DIR / "journal_*.jsonl"))):
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # Only include entries with real agent signals
                if entry.get("agent_signals") and len(entry["agent_signals"]) >= 2:
                    entries.append(entry)
    return entries


def select_candidates(entries: list[dict], n: int = 15) -> list[BenchmarkCandidate]:
    """Select diverse candidates via stratified sampling."""
    # Separate by action
    buys = [e for e in entries if e.get("action") in ("BUY", "STRONG_BUY")]
    no_trades = [e for e in entries if e.get("action") in ("NO_TRADE", "HOLD")]

    # Sort buys by MFCS (highest first) and no_trades by signal count
    buys.sort(key=lambda e: e.get("mfcs", 0), reverse=True)
    no_trades.sort(key=lambda e: len(e.get("agent_signals", [])), reverse=True)

    selected = []
    seen_tickers = set()

    def add_entry(entry):
        if entry["ticker"] in seen_tickers:
            return False
        seen_tickers.add(entry["ticker"])
        # Determine profitability from exit data if available
        pnl = 0
        if entry.get("entry_price") and entry.get("exit_price"):
            pnl = entry["exit_price"] - entry["entry_price"]
        selected.append(BenchmarkCandidate(
            ticker=entry["ticker"],
            date=entry.get("session_date", ""),
            action=entry.get("action", "NO_TRADE"),
            mfcs=entry.get("mfcs", 0),
            gap_pct=entry.get("gap_pct", 0),
            rvol=entry.get("rvol", 0),
            entry_price=entry.get("entry_price", 0),
            agent_signals=entry.get("agent_signals", []),
            input_data=entry.get("input_data", {}),
            was_profitable=pnl > 0,
        ))
        return True

    # Take top BUY entries
    for e in buys[:n // 2 + 1]:
        if len(selected) >= n // 2 + 1:
            break
        add_entry(e)

    # Take diverse NO_TRADE entries
    for e in no_trades:
        if len(selected) >= n:
            break
        add_entry(e)

    # Fill remaining from any pool
    for e in entries:
        if len(selected) >= n:
            break
        add_entry(e)

    return selected[:n]


# ── Prompt Building ──────────────────────────────────────────────────

def build_news_prompt(candidate: BenchmarkCandidate) -> tuple[str, str]:
    """Build system + user prompts for news agent from journal data."""
    system = (
        "You are a financial news analyst specializing in momentum stocks. "
        "Analyze news to identify catalysts that could drive explosive price moves. "
        "Be objective and avoid confirmation bias. "
        "Respond ONLY with a valid JSON object."
    )

    headlines = candidate.input_data.get("news_headlines", [])
    sources = candidate.input_data.get("news_sources", [])

    news_text = ""
    for i, hl in enumerate(headlines[:10]):
        src = sources[i] if i < len(sources) else "unknown"
        news_text += f"\n[{i+1}] Headline: {hl}\n    Source: {src}\n"

    if not news_text:
        news_text = "\n[No news found for this ticker in the last 24 hours]\n"

    user = (
        f"Analyze the following news for {candidate.ticker}.\n"
        f"Gap: {candidate.gap_pct * 100:.1f}% | RVOL: {candidate.rvol:.1f}x | "
        f"Price: ${candidate.entry_price:.2f}\n"
        f"\n--- NEWS ITEMS ---{news_text}\n--- END NEWS ---\n\n"
        f'Provide your analysis as a JSON object with these exact fields:\n'
        f'{{\n'
        f'  "signal": "STRONG_BULL" | "BULL" | "NEUTRAL" | "BEAR" | "STRONG_BEAR",\n'
        f'  "confidence": 0.0 to 1.0,\n'
        f'  "catalyst_type": "FDA_APPROVAL" | "EARNINGS_BEAT" | "CONTRACT_WIN" | "CORPORATE_UPDATE" | "NONE",\n'
        f'  "key_reasoning": "...",\n'
        f'  "red_flags": ["..."]\n'
        f'}}'
    )
    return system, user


def build_manipulation_prompt(candidate: BenchmarkCandidate) -> tuple[str, str]:
    """Build system + user prompts for manipulation classifier."""
    system = (
        "You are a market manipulation detection specialist. "
        "Classify the current phase of this stock's price action. "
        "Respond ONLY with a valid JSON object."
    )

    headlines = candidate.input_data.get("news_headlines", [])
    news_text = "; ".join(headlines[:5]) if headlines else "No news"

    user = (
        f"Classify manipulation risk for {candidate.ticker}:\n"
        f"Gap: {candidate.gap_pct * 100:.1f}% | RVOL: {candidate.rvol:.1f}x | "
        f"Price: ${candidate.entry_price:.2f}\n"
        f"News: {news_text}\n\n"
        f'Respond with JSON:\n'
        f'{{\n'
        f'  "phase": "ORGANIC_MOMENTUM" | "PROMOTIONAL_EARLY" | "PROMOTIONAL_LATE" | "DISTRIBUTION" | "UNCERTAIN",\n'
        f'  "confidence": 0.0 to 1.0,\n'
        f'  "signal": "BULL" | "NEUTRAL" | "BEAR",\n'
        f'  "key_reasoning": "..."\n'
        f'}}'
    )
    return system, user


AGENT_PROMPT_BUILDERS = {
    "news_agent": build_news_prompt,
    "manipulation_classifier": build_manipulation_prompt,
}


# ── LLM Calling ──────────────────────────────────────────────────────

def _get_together_client():
    """Get Together SDK client (cached)."""
    from together import Together
    key = os.environ.get("TOGETHER_API_KEY") or os.environ.get("TOGETHER_AI_API_KEY", "")
    return Together(api_key=key)


async def probe_model(model_id: str) -> bool:
    """Quick probe to check if model is available via native Together SDK."""
    try:
        client = _get_together_client()
        resp = await asyncio.to_thread(
            client.chat.completions.create,
            model=model_id,
            messages=[{"role": "user", "content": "Say OK as JSON: {\"status\": \"ok\"}"}],
            max_tokens=20,
        )
        return bool(resp.choices[0].message.content)
    except Exception as e:
        logger.warning("Model %s unavailable: %s", model_id, str(e)[:80])
        return False


async def call_model(
    model_id: str,
    system_prompt: str,
    user_prompt: str,
    timeout: int = 60,
) -> tuple[str, float, int, int, str]:
    """Call a model via native Together SDK and return (content, latency_ms, in_tokens, out_tokens, error)."""
    start = time.monotonic()
    try:
        client = _get_together_client()

        def _sync_call():
            return client.chat.completions.create(
                model=model_id,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=4096,
            )

        resp = await asyncio.wait_for(asyncio.to_thread(_sync_call), timeout=timeout)
        latency = (time.monotonic() - start) * 1000
        content = resp.choices[0].message.content or ""
        usage = getattr(resp, "usage", None)
        in_tok = getattr(usage, "prompt_tokens", 0) if usage else 0
        out_tok = getattr(usage, "completion_tokens", 0) if usage else 0
        return content, latency, in_tok, out_tok, ""
    except asyncio.TimeoutError:
        return "", (time.monotonic() - start) * 1000, 0, 0, "TIMEOUT"
    except Exception as e:
        return "", (time.monotonic() - start) * 1000, 0, 0, str(e)[:200]


# ── Signal Parsing ───────────────────────────────────────────────────

def parse_signal(raw_json: dict, agent_id: str) -> tuple[str, float]:
    """Extract signal + confidence from parsed JSON."""
    signal = raw_json.get("signal", "NEUTRAL")
    if signal not in SIGNAL_SCORES:
        signal = "NEUTRAL"
    conf = 0.0
    try:
        conf = float(raw_json.get("confidence", 0))
        conf = max(0.0, min(1.0, conf))
    except (ValueError, TypeError):
        conf = 0.0
    return signal, conf


def get_baseline_signal(candidate: BenchmarkCandidate, agent_id: str) -> tuple[str, float]:
    """Get the original production signal for comparison."""
    for sig in candidate.agent_signals:
        if sig.get("agent_id") == agent_id:
            return sig.get("signal", "NEUTRAL"), sig.get("confidence", 0)
    return "NEUTRAL", 0.0


def signals_agree(sig1: str, sig2: str) -> bool:
    """Check if two signals agree in direction (bullish/neutral/bearish)."""
    bullish = {"STRONG_BULL", "BULL"}
    bearish = {"STRONG_BEAR", "BEAR"}
    if sig1 in bullish and sig2 in bullish:
        return True
    if sig1 in bearish and sig2 in bearish:
        return True
    if sig1 == "NEUTRAL" and sig2 == "NEUTRAL":
        return True
    return False


# ── MFCS Recomputation ──────────────────────────────────────────────

def recompute_mfcs(
    candidate: BenchmarkCandidate,
    new_signals: dict[str, tuple[str, float]],
) -> float:
    """Recompute MFCS by substituting new LLM signals."""
    component_scores = {}

    # Keep deterministic signals from original journal
    for sig in candidate.agent_signals:
        aid = sig.get("agent_id", "")
        cat = AGENT_CATEGORY.get(aid)
        if aid in ("technical_agent", "risk_agent"):
            continue  # Skip risk (not in MFCS), technical handled below
        if cat and aid not in new_signals:
            score = SIGNAL_SCORES.get(sig.get("signal", "NEUTRAL"), 0)
            conf = sig.get("confidence", 0)
            component_scores[cat] = score * max(conf, 0.20)

    # Substitute new LLM signals
    for aid, (signal, conf) in new_signals.items():
        cat = AGENT_CATEGORY.get(aid)
        if cat:
            score = SIGNAL_SCORES.get(signal, 0)
            component_scores[cat] = score * max(conf, 0.20)

    # Add RVOL score (deterministic)
    rvol = candidate.rvol
    if rvol < 1.0:
        rvol_score = 0.0
    elif rvol < 2.0:
        rvol_score = 0.2 + 0.2 * (rvol - 1.0)
    elif rvol < 4.0:
        rvol_score = 0.4 + 0.15 * (rvol - 2.0)
    else:
        rvol_score = min(1.0, 0.7 + 0.05 * (rvol - 4.0))
    if "volume_rvol" not in component_scores:
        component_scores["volume_rvol"] = rvol_score

    # Compute weighted MFCS
    active_weight = sum(DEFAULT_WEIGHTS.get(cat, 0) for cat in component_scores if cat in DEFAULT_WEIGHTS)
    if active_weight <= 0:
        return 0.0
    boost = 1.0 / active_weight
    mfcs = sum(DEFAULT_WEIGHTS.get(cat, 0) * boost * score for cat, score in component_scores.items() if cat in DEFAULT_WEIGHTS)
    return round(mfcs, 4)


# ── Aggregation ──────────────────────────────────────────────────────

def aggregate_scores(model: str, calls: list[CallResult], candidates: list[BenchmarkCandidate]) -> ModelScore:
    """Compute aggregate metrics for a model."""
    if not calls:
        return ModelScore(model=model)

    n = len(calls)
    latencies = [c.latency_ms for c in calls if not c.timed_out]

    # Buy vs no-trade confidence separation
    buy_confs = [c.confidence for c in calls if any(
        cd.ticker == c.ticker and cd.action in ("BUY", "STRONG_BUY") for cd in candidates
    ) and c.json_parsed]
    nt_confs = [c.confidence for c in calls if any(
        cd.ticker == c.ticker and cd.action not in ("BUY", "STRONG_BUY") for cd in candidates
    ) and c.json_parsed]
    conf_sep = (sum(buy_confs) / max(len(buy_confs), 1)) - (sum(nt_confs) / max(len(nt_confs), 1))

    return ModelScore(
        model=model,
        total_calls=n,
        signal_accuracy=sum(1 for c in calls if c.signal_agrees) / n,
        json_parse_rate=sum(1 for c in calls if c.json_parsed) / n,
        schema_valid_rate=sum(1 for c in calls if c.schema_valid) / n,
        avg_latency_ms=sum(latencies) / max(len(latencies), 1),
        p95_latency_ms=sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0,
        timeout_rate=sum(1 for c in calls if c.timed_out) / n,
        confidence_separation=conf_sep,
        total_cost=sum(c.estimated_cost for c in calls),
    )


def compute_composite(scores: list[ModelScore]) -> list[ModelScore]:
    """Compute composite ranking score (0-1)."""
    if not scores:
        return scores

    # Min-max normalize
    def norm(vals):
        mn, mx = min(vals), max(vals)
        return [(v - mn) / (mx - mn) if mx > mn else 0.5 for v in vals]

    accs = norm([s.signal_accuracy for s in scores])
    parses = norm([s.json_parse_rate for s in scores])
    confs = norm([s.confidence_separation for s in scores])
    timeouts = norm([1 - s.timeout_rate for s in scores])
    # Invert latency (lower = better)
    lats = norm([1.0 / max(s.avg_latency_ms, 1) for s in scores])
    costs = norm([1.0 / max(s.total_cost, 0.001) for s in scores])

    for i, s in enumerate(scores):
        s.composite_score = round(
            0.30 * accs[i] + 0.20 * parses[i] + 0.15 * confs[i] +
            0.15 * timeouts[i] + 0.10 * lats[i] + 0.10 * costs[i], 4
        )

    scores.sort(key=lambda s: s.composite_score, reverse=True)
    return scores


# ── Output ───────────────────────────────────────────────────────────

def print_ranking(scores: list[ModelScore], unavailable: list[str]):
    """Print ranked comparison table."""
    print("\n" + "=" * 120)
    print("LLM MODEL BENCHMARK RESULTS")
    print("=" * 120)

    print(f"\n  {'Rank':>4} {'Model':<50} {'Sig%':>5} {'Parse%':>6} {'ConfSep':>7} "
          f"{'P50ms':>7} {'TO%':>4} {'Cost$':>6} {'Score':>6}")
    print(f"  {'----':>4} {'-----':<50} {'----':>5} {'------':>6} {'-------':>7} "
          f"{'-----':>7} {'---':>4} {'-----':>6} {'-----':>6}")

    for i, s in enumerate(scores):
        tag = " *" if s.model in (
            "Qwen/Qwen3-Next-80B-A3B-Instruct",
            "Qwen/Qwen3-235B-A22B-Instruct-2507-tput",
        ) else ""
        print(f"  {i+1:>4} {s.model:<50} {s.signal_accuracy*100:>4.0f}% "
              f"{s.json_parse_rate*100:>5.0f}% {s.confidence_separation:>+6.2f} "
              f"{s.avg_latency_ms:>6.0f} {s.timeout_rate*100:>3.0f}% "
              f"${s.total_cost:>5.2f} {s.composite_score:>5.3f}{tag}")

    if unavailable:
        print(f"\n  UNAVAILABLE ({len(unavailable)}): {', '.join(unavailable)}")

    print()


def save_results(scores: list[ModelScore], calls: list[CallResult], unavailable: list[str], path: str):
    """Save full results to JSON."""
    output = {
        "benchmark_timestamp": datetime.now(timezone.utc).isoformat(),
        "model_rankings": [asdict(s) for s in scores],
        "detailed_calls": [asdict(c) for c in calls],
        "unavailable_models": unavailable,
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"Results saved to {path}")


# ── Main Orchestrator ────────────────────────────────────────────────

async def run_benchmark(
    models: list[str],
    candidates: list[BenchmarkCandidate],
    agents: list[str],
    sleep_between: float = 0.5,
    timeout: int = 60,
    verbose: bool = False,
):
    """Run the full benchmark."""
    all_calls: list[CallResult] = []
    all_scores: list[ModelScore] = []
    unavailable: list[str] = []

    print(f"\nProbing {len(models)} models for availability...")
    available_models = []
    for model in models:
        ok = await probe_model(model)
        status = "OK" if ok else "UNAVAILABLE"
        print(f"  {model:<55} {status}")
        if ok:
            available_models.append(model)
        else:
            unavailable.append(model)
        await asyncio.sleep(0.3)

    print(f"\n{len(available_models)} models available, {len(unavailable)} unavailable")
    print(f"Running {len(available_models)} x {len(candidates)} x {len(agents)} = "
          f"{len(available_models) * len(candidates) * len(agents)} calls\n")

    for mi, model in enumerate(available_models):
        model_calls = []
        print(f"[{mi+1}/{len(available_models)}] Testing {model}...")

        for candidate in candidates:
            for agent_id in agents:
                builder = AGENT_PROMPT_BUILDERS.get(agent_id)
                if not builder:
                    continue

                system_prompt, user_prompt = builder(candidate)
                baseline_sig, baseline_conf = get_baseline_signal(candidate, agent_id)

                # Call the model
                content, latency, in_tok, out_tok, error = await call_model(
                    model, system_prompt, user_prompt, timeout
                )

                # Parse response
                result = CallResult(
                    model=model,
                    agent_id=agent_id,
                    ticker=candidate.ticker,
                    date=candidate.date,
                    latency_ms=latency,
                    baseline_signal=baseline_sig,
                    baseline_confidence=baseline_conf,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                )

                if error:
                    result.error = error
                    result.timed_out = "TIMEOUT" in error or "timed out" in error.lower()
                else:
                    parsed = extract_json(content)
                    result.json_parsed = bool(parsed)
                    if parsed:
                        try:
                            signal, conf = parse_signal(parsed, agent_id)
                            result.signal = signal
                            result.confidence = conf
                            result.schema_valid = True
                        except Exception:
                            result.schema_valid = False

                    result.signal_agrees = signals_agree(result.signal, baseline_sig)

                # Estimate cost (rough)
                result.estimated_cost = (in_tok * 0.5 + out_tok * 1.5) / 1_000_000

                model_calls.append(result)

                if verbose:
                    tag = "OK" if result.signal_agrees else "DIFF"
                    print(f"    {candidate.ticker:6s} {agent_id:25s} "
                          f"{result.signal:12s} vs {baseline_sig:12s} [{tag}] "
                          f"{latency:6.0f}ms")

                await asyncio.sleep(sleep_between)

        # Aggregate
        score = aggregate_scores(model, model_calls, candidates)
        all_scores.append(score)
        all_calls.extend(model_calls)

        print(f"  -> Sig={score.signal_accuracy*100:.0f}% Parse={score.json_parse_rate*100:.0f}% "
              f"Lat={score.avg_latency_ms:.0f}ms Cost=${score.total_cost:.2f}")

    # Compute composite and rank
    all_scores = compute_composite(all_scores)
    return all_scores, all_calls, unavailable


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="LLM Model Benchmark for MOMENTUM-X")
    parser.add_argument("--candidates", type=int, default=15, help="Number of candidates (default: 15)")
    parser.add_argument("--models", type=str, default="", help="Comma-separated model IDs (default: all)")
    parser.add_argument("--agents", type=str, default="news_agent,manipulation_classifier",
                        help="Comma-separated agent IDs")
    parser.add_argument("--output", type=str, default="", help="JSON output path")
    parser.add_argument("--dry-run", action="store_true", help="Show candidates without calling APIs")
    parser.add_argument("--sleep", type=float, default=0.5, help="Sleep between calls (seconds)")
    parser.add_argument("--timeout", type=int, default=60, help="Per-call timeout (seconds)")
    parser.add_argument("--verbose", action="store_true", help="Print per-call results")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    # Load and select candidates
    print("Loading journal entries...")
    entries = load_journal_entries()
    print(f"Found {len(entries)} entries with agent signals")

    candidates = select_candidates(entries, args.candidates)
    print(f"\nSelected {len(candidates)} candidates:")
    print(f"  {'Ticker':<8} {'Date':<12} {'Action':<12} {'MFCS':>6} {'Gap%':>6} {'RVOL':>6} {'Signals':>7}")
    print(f"  {'------':<8} {'----':<12} {'------':<12} {'----':>6} {'----':>6} {'----':>6} {'-------':>7}")
    for c in candidates:
        print(f"  {c.ticker:<8} {c.date:<12} {c.action:<12} {c.mfcs:>6.3f} "
              f"{c.gap_pct*100:>5.0f}% {c.rvol:>5.1f} {len(c.agent_signals):>7}")

    if args.dry_run:
        print("\n[DRY RUN] Would test these models:")
        models = args.models.split(",") if args.models else BENCHMARK_MODELS
        for m in models:
            print(f"  {m}")
        agents = args.agents.split(",")
        total = len(models) * len(candidates) * len(agents)
        print(f"\nTotal calls: {total} (est. ${total * 0.04:.0f}, ~{total * 5.5 / 60:.0f} minutes)")
        return

    # Run benchmark
    models = args.models.split(",") if args.models else BENCHMARK_MODELS
    models = [m.strip() for m in models if m.strip()]
    agents = [a.strip() for a in args.agents.split(",")]

    scores, calls, unavailable = asyncio.run(run_benchmark(
        models=models,
        candidates=candidates,
        agents=agents,
        sleep_between=args.sleep,
        timeout=args.timeout,
        verbose=args.verbose,
    ))

    # Output
    print_ranking(scores, unavailable)

    if args.output:
        save_results(scores, calls, unavailable, args.output)


if __name__ == "__main__":
    main()
