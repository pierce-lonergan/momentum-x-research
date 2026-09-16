#!/usr/bin/env python
"""D201-E8: Signal Consistency Test.

Tests whether the same stock evaluated twice gets the same verdict.
LLM agents are non-deterministic. If the same candidate evaluated at 9:25
and 9:35 gets different verdicts (BUY vs NO_TRADE), the signal is noise.

Scans journal entries for tickers that appear multiple times on the same day
(re-evaluations) and measures verdict stability.

Usage:
    python scripts/experiment_signal_consistency.py
"""

from __future__ import annotations

import json
import glob
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_DATA = Path(__file__).resolve().parent.parent / "data"


def main() -> None:
    print("=" * 70)
    print("D201-E8: SIGNAL CONSISTENCY TEST")
    print("=" * 70)

    # Group journal entries by (ticker, date) to find re-evaluations
    entries_by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)

    for jf in sorted(glob.glob(str(_DATA / "journals" / "journal_*.jsonl"))):
        with open(jf) as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    ticker = entry.get("ticker", "")
                    date = entry.get("session_date", "")
                    if ticker and date:
                        entries_by_key[(ticker, date)].append(entry)
                except json.JSONDecodeError:
                    continue

    # Find tickers with multiple evaluations
    multi_eval = {k: v for k, v in entries_by_key.items() if len(v) >= 2}
    print(f"Total (ticker, date) pairs: {len(entries_by_key)}")
    print(f"Pairs with 2+ evaluations: {len(multi_eval)}")

    # Analyze consistency
    consistent = 0
    inconsistent = 0
    mfcs_drift_sum = 0.0
    mfcs_drift_count = 0
    agent_flip_counts: dict[str, int] = defaultdict(int)
    agent_eval_counts: dict[str, int] = defaultdict(int)

    inconsistent_examples: list[dict] = []

    for (ticker, date), evals in multi_eval.items():
        actions = [e.get("action", "UNKNOWN") for e in evals]
        mfcs_values = [e.get("mfcs", 0.0) for e in evals]

        # Verdict consistency
        unique_actions = set(actions)
        if len(unique_actions) == 1:
            consistent += 1
        else:
            inconsistent += 1
            if len(inconsistent_examples) < 10:
                inconsistent_examples.append({
                    "ticker": ticker,
                    "date": date,
                    "actions": actions,
                    "mfcs_values": [round(m, 3) for m in mfcs_values],
                })

        # MFCS drift
        if len(mfcs_values) >= 2:
            drift = max(mfcs_values) - min(mfcs_values)
            mfcs_drift_sum += drift
            mfcs_drift_count += 1

        # Per-agent signal flips
        for i in range(1, len(evals)):
            for sig_prev in (evals[i - 1].get("agent_signals") or []):
                agent_id = sig_prev.get("agent_id", "")
                prev_signal = sig_prev.get("signal", "NEUTRAL")
                # Find matching agent in current eval
                for sig_curr in (evals[i].get("agent_signals") or []):
                    if sig_curr.get("agent_id") == agent_id:
                        curr_signal = sig_curr.get("signal", "NEUTRAL")
                        agent_eval_counts[agent_id] = agent_eval_counts.get(agent_id, 0) + 1
                        if prev_signal != curr_signal:
                            agent_flip_counts[agent_id] = agent_flip_counts.get(agent_id, 0) + 1
                        break

    total = consistent + inconsistent
    print(f"\n--- VERDICT CONSISTENCY ---")
    print(f"  Consistent (same verdict):   {consistent}/{total} ({consistent/total:.0%})" if total > 0 else "  No multi-evals")
    print(f"  Inconsistent (flip-flopped): {inconsistent}/{total} ({inconsistent/total:.0%})" if total > 0 else "")

    if mfcs_drift_count > 0:
        avg_drift = mfcs_drift_sum / mfcs_drift_count
        print(f"\n--- MFCS DRIFT ---")
        print(f"  Avg MFCS drift between evals: {avg_drift:.3f}")
        print(f"  (For context, buy threshold = 0.25)")

    if agent_flip_counts:
        print(f"\n--- PER-AGENT SIGNAL FLIPS ---")
        print(f"  {'Agent':<25s} | {'Flips':>6s} | {'Evals':>6s} | {'Flip%':>6s}")
        print("  " + "-" * 55)
        for agent_id in sorted(agent_eval_counts.keys()):
            flips = agent_flip_counts.get(agent_id, 0)
            evals = agent_eval_counts[agent_id]
            flip_pct = flips / evals if evals > 0 else 0
            tag = " *** UNSTABLE" if flip_pct > 0.30 else ""
            print(f"  {agent_id:<25s} | {flips:6d} | {evals:6d} | {flip_pct:5.0%}{tag}")

    if inconsistent_examples:
        print(f"\n--- INCONSISTENCY EXAMPLES ---")
        for ex in inconsistent_examples[:5]:
            print(f"  {ex['ticker']} ({ex['date']}): verdicts={ex['actions']} mfcs={ex['mfcs_values']}")

    print()
    print("--- INTERPRETATION ---")
    if total > 0 and inconsistent / total > 0.20:
        print("  !!! >20% verdict inconsistency. LLM signals are noisy.")
        print("  Consider: deterministic-only scoring, or averaging multiple LLM calls.")
    elif total > 0:
        print(f"  {inconsistent/total:.0%} flip rate is {'acceptable' if inconsistent/total < 0.10 else 'concerning'}.")

    print("=" * 70)


if __name__ == "__main__":
    main()
