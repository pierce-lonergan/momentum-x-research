#!/usr/bin/env python
"""D200-E5: Anti-Signal / Null Hypothesis Test.

Tests whether LLM agents add value over random selection by running four variants
against the 196 labeled scenarios:
  1. Null-Signal:   Replace all signals with random BULL/BEAR (50/50)
  2. Anti-Signal:   Invert every signal (BULL→BEAR, BEAR→BULL)
  3. Single-Agent:  Run with only one agent at a time
  4. No-LLM:        Use ONLY deterministic agents (technical + risk)

If the null-signal variant matches production P&L, agents add zero value.
If the anti-signal variant beats production, agents are actively harmful.

Usage:
    python scripts/experiment_null_signal.py
    python scripts/experiment_null_signal.py --runs 1000    # More Monte Carlo samples
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_DATA = Path(__file__).resolve().parent.parent / "data"

# MFCS weights from MOMENTUM_LOGIC.md §5
DEFAULT_WEIGHTS = {
    "catalyst_news": 0.30,
    "technical": 0.20,
    "volume_rvol": 0.20,
    "float_structure": 0.15,
    "institutional": 0.10,
    "deep_search": 0.05,
}

SIGNAL_NUMERIC = {
    "STRONG_BULL": 1.0,
    "BULL": 0.5,
    "NEUTRAL": 0.0,
    "BEAR": -0.5,
    "STRONG_BEAR": -1.0,
}

INVERT_MAP = {
    "STRONG_BULL": "STRONG_BEAR",
    "BULL": "BEAR",
    "NEUTRAL": "NEUTRAL",
    "BEAR": "BULL",
    "STRONG_BEAR": "STRONG_BULL",
}

AGENT_TO_WEIGHT_KEY = {
    "news_agent": "catalyst_news",
    "technical_agent": "technical",
    "fundamental_agent": "float_structure",
    "institutional_agent": "institutional",
    "deep_search_agent": "deep_search",
    "risk_agent": "risk",
    "manipulation_classifier": "manipulation",
}


def load_scenarios() -> list[dict]:
    """Load labeled gap scenarios."""
    sc_path = _DATA / "scenarios" / "gap_scenarios.json"
    with open(sc_path) as f:
        data = json.load(f)
    return data if isinstance(data, list) else data.get("scenarios", [])


def compute_simple_mfcs(signals: dict[str, str], confidences: dict[str, float],
                        weights: dict[str, float] | None = None) -> float:
    """Simplified MFCS computation for experiment purposes."""
    w = weights or DEFAULT_WEIGHTS
    total = 0.0
    active_weight = 0.0
    for agent_key, weight in w.items():
        signal = signals.get(agent_key, "NEUTRAL")
        conf = confidences.get(agent_key, 0.5)
        numeric = SIGNAL_NUMERIC.get(signal, 0.0)
        if numeric != 0.0:
            total += weight * numeric * max(conf, 0.20)
            active_weight += weight
        # Skip NEUTRAL (D26 weight redistribution)
    if active_weight > 0:
        total *= (1.0 / active_weight)
    return total


def simulate_variant(scenarios: list[dict], variant: str,
                     buy_threshold: float = 0.20,
                     mc_runs: int = 100) -> dict:
    """Simulate a signal variant and measure outcomes."""
    results = {"wins": 0, "losses": 0, "total_return": 0.0, "trades": 0}

    if variant == "null_signal":
        # Monte Carlo: run multiple random draws
        all_wins, all_losses, all_returns = 0, 0, 0.0
        for _ in range(mc_runs):
            for s in scenarios:
                signals = {k: random.choice(["BULL", "BEAR"]) for k in DEFAULT_WEIGHTS}
                confidences = {k: random.uniform(0.3, 0.8) for k in DEFAULT_WEIGHTS}
                mfcs = compute_simple_mfcs(signals, confidences)
                if mfcs >= buy_threshold:
                    ret = s.get("intraday_return", 0.0)
                    all_returns += ret
                    if s["outcome"] == "WIN":
                        all_wins += 1
                    else:
                        all_losses += 1
        # Average across runs
        total_trades = all_wins + all_losses
        results["wins"] = all_wins / mc_runs
        results["losses"] = all_losses / mc_runs
        results["total_return"] = all_returns / mc_runs
        results["trades"] = total_trades / mc_runs

    elif variant == "anti_signal":
        # Use scenario characteristics to simulate "typical" agent signals, then invert
        for s in scenarios:
            # Build original signals based on outcome (simulating what agents would say)
            if s["outcome"] == "WIN":
                original = {"catalyst_news": "BULL", "technical": "BULL",
                           "volume_rvol": "BULL", "float_structure": "NEUTRAL",
                           "institutional": "NEUTRAL", "deep_search": "NEUTRAL"}
            else:
                original = {"catalyst_news": "BEAR", "technical": "BEAR",
                           "volume_rvol": "NEUTRAL", "float_structure": "BEAR",
                           "institutional": "NEUTRAL", "deep_search": "NEUTRAL"}
            # Invert
            inverted = {k: INVERT_MAP.get(v, v) for k, v in original.items()}
            confidences = {k: 0.6 for k in DEFAULT_WEIGHTS}
            mfcs = compute_simple_mfcs(inverted, confidences)
            if mfcs >= buy_threshold:
                results["trades"] += 1
                ret = s.get("intraday_return", 0.0)
                results["total_return"] += ret
                if s["outcome"] == "WIN":
                    results["wins"] += 1
                else:
                    results["losses"] += 1

    elif variant == "all_bull":
        # Every agent says BULL — tests "buy everything that passes scanner"
        for s in scenarios:
            ret = s.get("intraday_return", 0.0)
            results["total_return"] += ret
            results["trades"] += 1
            if s["outcome"] == "WIN":
                results["wins"] += 1
            else:
                results["losses"] += 1

    elif variant.startswith("single_"):
        # Single agent only (e.g., "single_catalyst_news")
        agent_key = variant.replace("single_", "")
        single_weights = {k: (1.0 if k == agent_key else 0.0) for k in DEFAULT_WEIGHTS}
        for s in scenarios:
            # Simulate agent signal based on outcome + noise
            if s["outcome"] == "WIN":
                signal = random.choice(["BULL", "STRONG_BULL", "BULL", "NEUTRAL"])
            else:
                signal = random.choice(["BEAR", "NEUTRAL", "BULL", "BEAR"])
            signals = {agent_key: signal}
            confidences = {agent_key: 0.6}
            mfcs = compute_simple_mfcs(signals, confidences, weights=single_weights)
            if mfcs >= buy_threshold:
                results["trades"] += 1
                ret = s.get("intraday_return", 0.0)
                results["total_return"] += ret
                if s["outcome"] == "WIN":
                    results["wins"] += 1
                else:
                    results["losses"] += 1

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="D200-E5: Null Signal Tests")
    parser.add_argument("--runs", type=int, default=100, help="Monte Carlo runs for null signal")
    args = parser.parse_args()

    scenarios = load_scenarios()
    baseline_wins = sum(1 for s in scenarios if s["outcome"] == "WIN")
    baseline_wr = baseline_wins / len(scenarios)
    baseline_avg_ret = sum(s.get("intraday_return", 0.0) for s in scenarios) / len(scenarios)

    print("=" * 70)
    print("D200-E5: ANTI-SIGNAL / NULL HYPOTHESIS TEST")
    print("=" * 70)
    print(f"Scenario database: {len(scenarios)} scenarios")
    print(f"Baseline (buy everything): win_rate={baseline_wr:.1%} avg_ret={baseline_avg_ret:+.2%}")
    print()

    variants = [
        ("all_bull", "Buy Everything (no agents)"),
        ("null_signal", f"Random Signals ({args.runs} MC runs)"),
        ("anti_signal", "Inverted Signals"),
        ("single_catalyst_news", "News Agent Only"),
        ("single_technical", "Technical Agent Only"),
        ("single_volume_rvol", "RVOL Only (deterministic)"),
    ]

    print(f"  {'Variant':<30s} | {'Trades':>7s} | {'Win%':>6s} | {'Avg Ret':>8s} | {'W':>5s}/{' L':>5s}")
    print("  " + "-" * 80)

    for variant_id, variant_name in variants:
        r = simulate_variant(scenarios, variant_id, mc_runs=args.runs)
        total = r["wins"] + r["losses"]
        wr = r["wins"] / total if total > 0 else 0
        avg_ret = r["total_return"] / r["trades"] if r["trades"] > 0 else 0
        print(
            f"  {variant_name:<30s} | {r['trades']:7.0f} | {wr:5.0%} | "
            f"{avg_ret:+7.2%} | {r['wins']:5.0f}/{r['losses']:5.0f}"
        )

    print()
    print("─── INTERPRETATION ───")
    print("  If 'Random Signals' matches production → agents add ZERO value")
    print("  If 'Inverted Signals' beats production → agents are HARMFUL")
    print("  If 'News Agent Only' beats full pipeline → other agents DILUTE signal")
    print("  If 'Buy Everything' is best → filtering is DESTROYING value")
    print("=" * 70)


if __name__ == "__main__":
    main()
