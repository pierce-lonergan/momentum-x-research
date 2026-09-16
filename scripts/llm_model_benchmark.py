"""LLM model benchmark — test new model candidates against current production.

Tests for:
  1. Availability (does Together AI host this model?)
  2. Latency (cold + warm)
  3. Token usage + cost
  4. Output structural validity for trading-agent prompts
  5. Output content sample for human review

Models tested:
  - deepseek-ai/DeepSeek-V3.1                  (current production tier1)
  - Qwen/Qwen3-235B-A22B-Instruct-2507-tput   (current Qwen completion path)
  - deepseek-ai/DeepSeek-V4-Pro                (NEW candidate)
  - moonshotai/Kimi-K2.6                       (NEW candidate)
  - zai-org/GLM-5.1                            (NEW candidate)

Usage:
  python scripts/llm_model_benchmark.py
  python scripts/llm_model_benchmark.py --quick   # NY-only, skip trading prompt
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# Force UTF-8 stdout for Windows console (box-drawing chars)
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: silent-handler — best-effort UTF-8 stdout config; non-fatal
        pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Load secrets
_secrets_path = Path.home() / "momentum-x-secrets.env"
if _secrets_path.exists():
    with _secrets_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


CANDIDATE_MODELS = [
    ("deepseek-ai/DeepSeek-V3.1", "current-production-tier1"),
    ("Qwen/Qwen3-235B-A22B-Instruct-2507-tput", "current-litellm-completion"),
    ("deepseek-ai/DeepSeek-V4-Pro", "NEW-candidate"),
    ("moonshotai/Kimi-K2.6", "NEW-candidate"),
    ("zai-org/GLM-5.1", "NEW-candidate"),
]


# Real news_agent system prompt (truncated from src/agents/news_agent.py)
TRADING_SYSTEM_PROMPT = (
    "You are a financial news analyst. Your ONLY job is to determine whether "
    "a specific, material, company-level catalyst exists that explains a stock's "
    "gap-up move. You are NOT looking for general market context.\n\n"
    "WHAT COUNTS AS A REAL CATALYST: FDA approval, earnings beat with numbers, "
    "named M&A with terms, named contract win with value, named analyst upgrade.\n"
    "WHAT IS NOT A CATALYST: listicles ('Top Movers Today'), generic sector strength, "
    "social media buzz, vague 'corporate update'.\n\n"
    "Respond ONLY with valid JSON in this format:\n"
    '{\n'
    '  "signal": "STRONG_BULL" | "BULL" | "NEUTRAL" | "BEAR" | "STRONG_BEAR",\n'
    '  "confidence": <float 0-1>,\n'
    '  "catalyst_type": "FDA_APPROVAL" | "EARNINGS_BEAT" | "M&A" | "CONTRACT_WIN" '
    '| "ANALYST_UPGRADE" | "NONE",\n'
    '  "catalyst_specificity": "CONFIRMED" | "RUMORED" | "SPECULATIVE" | "NONE",\n'
    '  "reasoning": "<1-2 sentence justification>"\n'
    '}'
)


# Real OGN scenario from today's session (the one trade that actually fired)
TRADING_USER_PROMPT = (
    "Ticker: OGN\n"
    "Pre-market gap: +53.8%\n"
    "RVOL: 7.5x\n"
    "Current price: $11.25 (vs prior close $7.32)\n"
    "Float: 208.3M shares\n"
    "Market cap: $2.93B\n"
    "Sector: Pharmaceuticals\n\n"
    "Recent news headlines (last 24h):\n"
    "1. \"Organon & Co. Q4 Revenue $1.59B Beats Estimates, Raises 2026 Guidance\"\n"
    "2. \"OGN Stock Jumps Premarket on Earnings Beat\"\n"
    "3. \"Pharma Movers: OGN, ABT, JNJ in Spotlight\"\n\n"
    "Should we BUY this gap-up? Apply your catalyst analysis framework."
)


@dataclass
class ModelResult:
    model: str
    label: str
    available: bool = False
    latency_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    output_sample: str = ""
    parsed_json: dict | None = None
    json_valid: bool = False
    error: str = ""

    def cost_usd(self, *, in_per_million: float = 0.6, out_per_million: float = 0.6) -> float:
        """Rough cost estimate (Together AI's published rates vary by model;
        $0.6 per 1M tokens is a reasonable midpoint for the open-source
        models tested. Real costs reported by the API supersede this)."""
        return (
            self.prompt_tokens * in_per_million / 1_000_000
            + self.completion_tokens * out_per_million / 1_000_000
        )


def call_model(model: str, label: str, *, prompt: list[dict]) -> ModelResult:
    """Single Together AI call with timing + structural validation."""
    result = ModelResult(model=model, label=label)
    try:
        from together import Together
    except ImportError:
        result.error = "together-python not installed (pip install together)"
        return result

    try:
        client = Together()
        t0 = time.perf_counter()
        response = client.chat.completions.create(
            model=model,
            messages=prompt,
        )
        result.latency_ms = (time.perf_counter() - t0) * 1000
        result.available = True
        if response.usage:
            result.prompt_tokens = getattr(response.usage, "prompt_tokens", 0)
            result.completion_tokens = getattr(response.usage, "completion_tokens", 0)
            result.total_tokens = getattr(response.usage, "total_tokens", 0)
        if response.choices:
            content = response.choices[0].message.content or ""
            result.output_sample = content[:600]
            # Try to parse JSON from response (for trading prompt)
            parsed = _try_extract_json(content)
            if parsed is not None:
                result.parsed_json = parsed
                result.json_valid = _validate_news_agent_schema(parsed)
    except Exception as e:
        result.error = f"{type(e).__name__}: {str(e)[:200]}"
    return result


def _try_extract_json(text: str) -> dict | None:
    """Extract a JSON object from the text. Handles ```json fences."""
    text = text.strip()
    # Strip code fences
    if "```" in text:
        # Get the content between first and last ```
        parts = text.split("```")
        for p in parts:
            p = p.strip()
            if p.startswith("json"):
                p = p[4:].strip()
            if p.startswith("{"):
                try:
                    return json.loads(p)
                except json.JSONDecodeError:  # noqa: silent-handler — try next fence
                    continue
    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:  # noqa: silent-handler — fall through to substring extraction
        pass
    # Try to find first {...} block
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:  # noqa: silent-handler — gave it our best shot, return None
            pass
    return None


def _validate_news_agent_schema(d: dict) -> bool:
    """Returns True if the dict has all expected news_agent fields."""
    required = {"signal", "confidence", "catalyst_type", "catalyst_specificity", "reasoning"}
    if not required.issubset(d.keys()):
        return False
    if d["signal"] not in ("STRONG_BULL", "BULL", "NEUTRAL", "BEAR", "STRONG_BEAR"):
        return False
    try:
        c = float(d["confidence"])
        if not (0.0 <= c <= 1.0):
            return False
    except (TypeError, ValueError):
        return False
    return True


def _print_result(r: ModelResult, *, kind: str) -> None:
    print(f"\n{'─' * 70}")
    print(f"  Model:    {r.model}  ({r.label})  — {kind}")
    print(f"{'─' * 70}")
    if not r.available:
        print(f"  ❌ UNAVAILABLE: {r.error}")
        return
    print(f"  ✓ available")
    print(f"  latency:  {r.latency_ms:>8.0f} ms")
    print(f"  tokens:   prompt={r.prompt_tokens}  completion={r.completion_tokens}  total={r.total_tokens}")
    print(f"  cost est: ${r.cost_usd():.5f}  (assumes $0.60/M I/O — Together rates vary by model)")
    if kind == "trading":
        print(f"  json valid: {'✓' if r.json_valid else '❌'}")
        if r.parsed_json:
            print(
                f"  parsed:   signal={r.parsed_json.get('signal')!r}  "
                f"confidence={r.parsed_json.get('confidence')!r}  "
                f"catalyst={r.parsed_json.get('catalyst_type')!r}  "
                f"specificity={r.parsed_json.get('catalyst_specificity')!r}"
            )
    print(f"  output sample (first 600 chars):")
    for line in r.output_sample.splitlines()[:8]:
        print(f"    {line}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--quick", action="store_true",
                        help="Skip the trading-prompt test, just verify availability")
    args = parser.parse_args()

    if not os.environ.get("TOGETHER_API_KEY") and not os.environ.get("TOGETHER_AI_API_KEY"):
        print("ERROR: TOGETHER_API_KEY (or TOGETHER_AI_API_KEY) not set", file=sys.stderr)
        print("Set in ~/momentum-x-secrets.env", file=sys.stderr)
        return 1
    # together-python looks for TOGETHER_API_KEY specifically
    if not os.environ.get("TOGETHER_API_KEY") and os.environ.get("TOGETHER_AI_API_KEY"):
        os.environ["TOGETHER_API_KEY"] = os.environ["TOGETHER_AI_API_KEY"]

    print("=" * 70)
    print("  LLM MODEL BENCHMARK — current production vs new candidates")
    print("=" * 70)

    # ── Phase 1: simple availability + latency baseline ──
    print("\n┌─ PHASE 1: AVAILABILITY + BASELINE LATENCY ─────────────────────────")
    print("│  Prompt: 'What are some fun things to do in New York?'")
    print("│  Tests cold-start latency + token cost on a generic short prompt")
    print("└────────────────────────────────────────────────────────────────────")
    simple_prompt = [{"role": "user", "content": "What are some fun things to do in New York?"}]
    simple_results: list[ModelResult] = []
    for model, label in CANDIDATE_MODELS:
        r = call_model(model, label, prompt=simple_prompt)
        simple_results.append(r)
        _print_result(r, kind="availability")

    if args.quick:
        return _emit_summary(simple_results, [])

    # ── Phase 2: real trading-agent prompt ──
    print("\n\n┌─ PHASE 2: REAL TRADING-AGENT PROMPT ───────────────────────────────")
    print("│  System: news_agent system prompt (truncated)")
    print("│  User:   OGN earnings catalyst from today's session (real data)")
    print("│  Tests:  JSON output validity + signal/catalyst classification")
    print("└────────────────────────────────────────────────────────────────────")
    trading_prompt = [
        {"role": "system", "content": TRADING_SYSTEM_PROMPT},
        {"role": "user", "content": TRADING_USER_PROMPT},
    ]
    trading_results: list[ModelResult] = []
    for model, label in CANDIDATE_MODELS:
        # Skip if availability test already failed
        avail = next((r for r in simple_results if r.model == model), None)
        if avail and not avail.available:
            print(f"\n  Skipping {model} — Phase 1 unavailable")
            continue
        r = call_model(model, label, prompt=trading_prompt)
        trading_results.append(r)
        _print_result(r, kind="trading")

    return _emit_summary(simple_results, trading_results)


def _emit_summary(simple: list[ModelResult], trading: list[ModelResult]) -> int:
    print("\n\n" + "=" * 70)
    print("  SUMMARY MATRIX")
    print("=" * 70)
    print(f"\n{'Model':<48} {'Avail':<8} {'NY ms':<8} {'Trade ms':<10} {'JSON':<6}")
    print("─" * 80)
    for r in simple:
        t = next((tr for tr in trading if tr.model == r.model), None)
        avail = "✓" if r.available else "❌"
        ny = f"{r.latency_ms:.0f}" if r.available else "—"
        trade_ms = f"{t.latency_ms:.0f}" if t and t.available else "—"
        json_ok = ("✓" if t.json_valid else "❌") if t and t.available else "—"
        model_short = r.model.replace("deepseek-ai/", "ds/").replace("moonshotai/", "moon/").replace("zai-org/", "zai/")
        print(f"{model_short:<48} {avail:<8} {ny:<8} {trade_ms:<10} {json_ok:<6}")

    available_models = [r.model for r in simple if r.available]
    print(f"\n  Available models: {len(available_models)} / {len(simple)}")
    if not available_models:
        print("  ERROR: No models reachable. Check TOGETHER_API_KEY.")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
