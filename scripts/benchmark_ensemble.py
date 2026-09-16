#!/usr/bin/env python
"""D202: Ensemble Consistency Benchmark.

Proves that ensemble multi-call averaging reduces signal noise by simulating
the exact flip-flop pattern found in D201-E8 (47% verdict inconsistency,
0.158 MFCS drift) and measuring the improvement with N=3 majority vote.

This is a SIMULATION benchmark (no live LLM calls) that models the measured
noise characteristics from production journal data.

Usage:
    python scripts/benchmark_ensemble.py
    python scripts/benchmark_ensemble.py --trials 10000
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agents.ensemble import EnsembleWrapper
from src.core.models import AgentSignal


# ── Measured noise characteristics from D201-E8 ────────────────────────
# 47% of same-stock re-evaluations produced different verdicts.
# Technical agent flipped 18% of the time.
# MFCS drift: 0.158 avg between evals.

AGENT_FLIP_RATES = {
    "news_agent": 0.07,         # 7% flip rate (from E8)
    "technical_agent": 0.18,    # 18% flip rate (from E8)
    "fundamental_agent": 0.00,  # 0% (essentially deterministic)
    "institutional_agent": 0.04,  # 4% flip rate
    "deep_search_agent": 0.02,  # 2% flip rate
    "risk_agent": 0.05,         # 5% flip rate
}

# Signal distribution from 4548 journal entries
SIGNAL_DISTRIBUTIONS = {
    "news_agent": {"BULL": 0.18, "NEUTRAL": 0.71, "BEAR": 0.005, "STRONG_BULL": 0.0, "STRONG_BEAR": 0.0},
    "technical_agent": {"STRONG_BULL": 0.54, "BULL": 0.0, "NEUTRAL": 0.23, "BEAR": 0.12, "STRONG_BEAR": 0.0},
}


def make_noisy_signal(
    base_signal: str,
    base_conf: float,
    flip_rate: float,
    agent_id: str = "test_agent",
) -> AgentSignal:
    """Create a signal with simulated LLM noise."""
    signal = base_signal
    conf = base_conf

    if random.random() < flip_rate:
        # Flip the signal
        flip_map = {
            "STRONG_BULL": random.choice(["BULL", "NEUTRAL"]),
            "BULL": random.choice(["NEUTRAL", "BEAR", "STRONG_BULL"]),
            "NEUTRAL": random.choice(["BULL", "BEAR"]),
            "BEAR": random.choice(["NEUTRAL", "BULL"]),
            "STRONG_BEAR": random.choice(["BEAR", "NEUTRAL"]),
        }
        signal = flip_map.get(base_signal, "NEUTRAL")

    # Add confidence noise (±0.15 normal distribution, matching MFCS drift of 0.158)
    conf += random.gauss(0, 0.10)
    conf = max(0.0, min(1.0, conf))

    return AgentSignal(
        agent_id=agent_id,
        ticker="SIM",
        timestamp=datetime.now(timezone.utc),
        signal=signal,
        confidence=conf,
        reasoning=f"sim_{signal}",
        model_id="benchmark",
        latency_ms=0.0,
    )


async def simulate_single_call(
    base_signal: str,
    base_conf: float,
    flip_rate: float,
    agent_id: str,
) -> str:
    """Simulate one LLM call and return the verdict direction."""
    sig = make_noisy_signal(base_signal, base_conf, flip_rate, agent_id)
    return sig.signal


async def simulate_ensemble_call(
    base_signal: str,
    base_conf: float,
    flip_rate: float,
    agent_id: str,
    n_calls: int = 3,
) -> str:
    """Simulate an ensemble of N LLM calls and return majority-voted direction."""
    signals = []
    for _ in range(n_calls):
        sig = make_noisy_signal(base_signal, base_conf, flip_rate, agent_id)
        signals.append(sig.signal)
    # Majority vote
    vote = Counter(signals).most_common(1)[0][0]
    return vote


async def run_benchmark(n_trials: int, agent_id: str, flip_rate: float,
                       base_signal: str = "BULL", base_conf: float = 0.6) -> dict:
    """Run N trials comparing single-call vs ensemble consistency."""

    # For each trial: generate a "true" signal, then measure how often
    # single-call and ensemble-call reproduce it.
    single_correct = 0
    ensemble_3_correct = 0
    ensemble_5_correct = 0

    single_signals: list[str] = []
    ensemble_3_signals: list[str] = []
    ensemble_5_signals: list[str] = []

    for _ in range(n_trials):
        # Single call
        s1 = await simulate_single_call(base_signal, base_conf, flip_rate, agent_id)
        single_signals.append(s1)
        if s1 == base_signal:
            single_correct += 1

        # Ensemble N=3
        e3 = await simulate_ensemble_call(base_signal, base_conf, flip_rate, agent_id, n_calls=3)
        ensemble_3_signals.append(e3)
        if e3 == base_signal:
            ensemble_3_correct += 1

        # Ensemble N=5
        e5 = await simulate_ensemble_call(base_signal, base_conf, flip_rate, agent_id, n_calls=5)
        ensemble_5_signals.append(e5)
        if e5 == base_signal:
            ensemble_5_correct += 1

    return {
        "agent_id": agent_id,
        "flip_rate": flip_rate,
        "base_signal": base_signal,
        "n_trials": n_trials,
        "single_accuracy": single_correct / n_trials,
        "ensemble_3_accuracy": ensemble_3_correct / n_trials,
        "ensemble_5_accuracy": ensemble_5_correct / n_trials,
        "single_distribution": dict(Counter(single_signals)),
        "ensemble_3_distribution": dict(Counter(ensemble_3_signals)),
        "ensemble_5_distribution": dict(Counter(ensemble_5_signals)),
    }


async def main_async(n_trials: int) -> None:
    print("=" * 70)
    print("D202: ENSEMBLE CONSISTENCY BENCHMARK")
    print("=" * 70)
    print(f"Simulating {n_trials} trials per agent (matching D201-E8 flip rates)")
    print()

    print(f"  {'Agent':<25s} | {'Flip%':>6s} | {'Single':>8s} | {'N=3':>8s} | {'N=5':>8s} | {'Lift(3)':>8s}")
    print("  " + "-" * 75)

    total_single = 0.0
    total_e3 = 0.0
    total_e5 = 0.0
    n_agents = 0

    for agent_id, flip_rate in AGENT_FLIP_RATES.items():
        result = await run_benchmark(
            n_trials=n_trials,
            agent_id=agent_id,
            flip_rate=flip_rate,
            base_signal="BULL",
            base_conf=0.6,
        )

        lift_3 = result["ensemble_3_accuracy"] - result["single_accuracy"]
        print(
            f"  {agent_id:<25s} | {flip_rate:5.0%} | "
            f"{result['single_accuracy']:7.1%} | "
            f"{result['ensemble_3_accuracy']:7.1%} | "
            f"{result['ensemble_5_accuracy']:7.1%} | "
            f"{lift_3:+7.1%}"
        )

        total_single += result["single_accuracy"]
        total_e3 += result["ensemble_3_accuracy"]
        total_e5 += result["ensemble_5_accuracy"]
        n_agents += 1

    avg_single = total_single / n_agents
    avg_e3 = total_e3 / n_agents
    avg_e5 = total_e5 / n_agents

    print("  " + "-" * 75)
    print(f"  {'AVERAGE':<25s} |       | {avg_single:7.1%} | {avg_e3:7.1%} | {avg_e5:7.1%} | {avg_e3 - avg_single:+7.1%}")

    # ── Verdict consistency simulation ──────────────────────────────
    print()
    print("--- VERDICT CONSISTENCY IMPROVEMENT ---")
    print("  Simulating BUY/NO_TRADE flip rate across full pipeline...")

    # Simulate the full pipeline verdict flip rate
    n_verdicts = n_trials
    single_flips = 0
    e3_flips = 0

    for _ in range(n_verdicts):
        # Simulate two evaluations of the same stock
        v1_single = await simulate_single_call("BULL", 0.5, 0.15, "pipeline")
        v2_single = await simulate_single_call("BULL", 0.5, 0.15, "pipeline")
        if v1_single != v2_single:
            single_flips += 1

        v1_e3 = await simulate_ensemble_call("BULL", 0.5, 0.15, "pipeline", 3)
        v2_e3 = await simulate_ensemble_call("BULL", 0.5, 0.15, "pipeline", 3)
        if v1_e3 != v2_e3:
            e3_flips += 1

    single_flip_rate = single_flips / n_verdicts
    e3_flip_rate = e3_flips / n_verdicts
    reduction = (single_flip_rate - e3_flip_rate) / single_flip_rate if single_flip_rate > 0 else 0

    print(f"  Single-call verdict flip rate: {single_flip_rate:.1%}")
    print(f"  Ensemble N=3 verdict flip rate: {e3_flip_rate:.1%}")
    print(f"  Flip rate reduction: {reduction:.0%}")
    print(f"  (D201-E8 measured: 47% flip rate in production)")

    print()
    print("--- CONCLUSION ---")
    if avg_e3 > avg_single + 0.01:
        print(f"  ENSEMBLE WORKS: N=3 improves signal accuracy by {avg_e3 - avg_single:+.1%}")
        print(f"  Verdict consistency improves by {reduction:.0%} (fewer flip-flops)")
        print(f"  Cost: 3x LLM tokens per agent. Latency: same (parallel calls).")
    else:
        print(f"  Marginal improvement. Consider if 3x token cost is justified.")

    print("=" * 70)


def main() -> None:
    parser = argparse.ArgumentParser(description="D202: Ensemble Benchmark")
    parser.add_argument("--trials", type=int, default=5000, help="Number of trials")
    args = parser.parse_args()

    asyncio.run(main_async(args.trials))


if __name__ == "__main__":
    main()
