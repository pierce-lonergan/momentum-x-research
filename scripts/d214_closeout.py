#!/usr/bin/env python
"""D214 Closeout: Difference-in-differences test + PF precision check.

Two final tests before writing the post-mortem:
1. Does archetype label predict which exit window (T+1m vs T+15m) wins?
2. Are the T+1m and T+15m PFs truly identical or coincidentally close?

Usage:
    python scripts/d214_closeout.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

_DATA = Path(__file__).resolve().parent.parent / "data"


def bootstrap_pf(pnls: list[float], n_resamples: int = 1000) -> dict:
    if len(pnls) < 3:
        return {"mean": 0.0, "ci_lower": 0.0, "ci_upper": 0.0}
    rng = np.random.default_rng(42)
    arr = np.array(pnls)
    samples = []
    for _ in range(n_resamples):
        s = rng.choice(arr, size=len(arr), replace=True)
        wins = sum(p for p in s if p > 0)
        losses = abs(sum(p for p in s if p <= 0))
        pf = wins / losses if losses > 0 else 10.0
        samples.append(min(pf, 10.0))
    return {
        "mean": float(np.mean(samples)),
        "ci_lower": float(np.percentile(samples, 2.5)),
        "ci_upper": float(np.percentile(samples, 97.5)),
    }


def main():
    from src.execution.archetype_exit import (
        ArchetypeClassifier, DecayCurveLibrary, FALLBACK_ARCHETYPE_ID,
    )

    # Load data
    scenario_path = _DATA / "scenarios" / "gap_scenarios.json"
    data = json.loads(scenario_path.read_text())
    scenarios = data if isinstance(data, list) else data.get("scenarios", [])
    bar_dir = _DATA / "scenarios" / "minute_bars"

    valid = []
    paths = []
    for s in scenarios:
        bf = bar_dir / f"{s['ticker']}_{s['date']}.json"
        if not bf.exists():
            continue
        bars = json.loads(bf.read_text()).get("bars", [])
        if len(bars) < 16:
            continue
        entry = bars[0]["open"]
        if entry <= 0:
            continue
        path = [((b["high"] + b["close"]) / 2.0 - entry) / entry for b in bars[:61]]
        while len(path) < 61:
            path.append(path[-1] if path else 0.0)
        valid.append(s)
        paths.append(path)

    clf = ArchetypeClassifier()
    clf.fit(valid)

    # Assign archetypes
    arch_map: dict[int, list[int]] = defaultdict(list)
    for i, s in enumerate(valid):
        aid, _ = clf.predict(
            abs(s.get("gap_pct", 0)), s.get("rvol", 1),
            s.get("prior_gap_count", 0) or 0, s.get("is_day2_runner", False),
        )
        arch_map[aid].append(i)

    slippage = 0.005

    print("=" * 70)
    print("  D214 CLOSEOUT: DIFFERENCE-IN-DIFFERENCES + PF PRECISION")
    print("=" * 70)

    # ═══════════════════════════════════════════════════════════════
    # TEST 1: Difference-in-differences
    # ═══════════════════════════════════════════════════════════════
    print("\n  TEST 1: Does archetype label predict optimal exit window?")
    print("  " + "-" * 55)

    for aid in sorted(arch_map.keys()):
        indices = arch_map[aid]
        if aid == FALLBACK_ARCHETYPE_ID:
            label = "FALLBACK"
        else:
            label = f"Archetype {aid}"
            meta = clf._archetype_meta.get(aid, {})
            centroid = meta.get("centroid", {})
            label += f" (gaps={centroid.get('prior_gap_count', 0):.1f})"

        # Compute T+1m and T+15m PnLs for each scenario in this archetype
        t1_pnls = []
        t15_pnls = []
        for i in indices:
            t1_pnls.append(paths[i][1] - slippage if len(paths[i]) > 1 else 0.0)
            t15_pnls.append(paths[i][15] - slippage if len(paths[i]) > 15 else 0.0)

        t1_pf = bootstrap_pf(t1_pnls)
        t15_pf = bootstrap_pf(t15_pnls)

        t1_wr = sum(1 for p in t1_pnls if p > 0) / len(t1_pnls) * 100
        t15_wr = sum(1 for p in t15_pnls if p > 0) / len(t15_pnls) * 100
        t1_avg = np.mean(t1_pnls) * 100
        t15_avg = np.mean(t15_pnls) * 100

        prefers = "T+1m" if t1_pf["mean"] > t15_pf["mean"] else "T+15m"

        # Check CI overlap
        ci_overlap = t1_pf["ci_lower"] < t15_pf["ci_upper"] and t15_pf["ci_lower"] < t1_pf["ci_upper"]

        print(f"\n    {label} (n={len(indices)}):")
        print(f"      T+1m:  PF={t1_pf['mean']:.3f} CI=[{t1_pf['ci_lower']:.3f}, {t1_pf['ci_upper']:.3f}] WR={t1_wr:.0f}% avg={t1_avg:+.2f}%")
        print(f"      T+15m: PF={t15_pf['mean']:.3f} CI=[{t15_pf['ci_lower']:.3f}, {t15_pf['ci_upper']:.3f}] WR={t15_wr:.0f}% avg={t15_avg:+.2f}%")
        print(f"      Prefers: {prefers} | CIs overlap: {'YES' if ci_overlap else 'NO'}")

    # Global (universe-level)
    print(f"\n    UNIVERSE (all n={len(valid)}):")
    all_t1 = [paths[i][1] - slippage for i in range(len(valid))]
    all_t15 = [paths[i][15] - slippage for i in range(len(valid))]
    t1_pf_all = bootstrap_pf(all_t1)
    t15_pf_all = bootstrap_pf(all_t15)
    print(f"      T+1m:  PF={t1_pf_all['mean']:.3f} CI=[{t1_pf_all['ci_lower']:.3f}, {t1_pf_all['ci_upper']:.3f}]")
    print(f"      T+15m: PF={t15_pf_all['mean']:.3f} CI=[{t15_pf_all['ci_lower']:.3f}, {t15_pf_all['ci_upper']:.3f}]")

    print("\n    DIFF-IN-DIFF VERDICT:")
    # Does the preference FLIP between archetypes?
    arch_prefs = {}
    for aid in sorted(arch_map.keys()):
        if aid == FALLBACK_ARCHETYPE_ID:
            continue
        indices = arch_map[aid]
        t1 = [paths[i][1] - slippage for i in indices]
        t15 = [paths[i][15] - slippage for i in indices]
        t1_pf_val = bootstrap_pf(t1)["mean"]
        t15_pf_val = bootstrap_pf(t15)["mean"]
        arch_prefs[aid] = "T+1m" if t1_pf_val > t15_pf_val else "T+15m"

    prefs_list = list(arch_prefs.values())
    if len(set(prefs_list)) > 1:
        print("      Preference FLIPS between archetypes — label carries coarse signal.")
    else:
        print(f"      Both archetypes prefer {prefs_list[0]} — label does NOT predict exit window.")
        print(f"      The bimodal pattern is universe-level, not archetype-specific.")

    # ═══════════════════════════════════════════════════════════════
    # TEST 2: PF precision check
    # ═══════════════════════════════════════════════════════════════
    print("\n\n  TEST 2: PF Precision (T+1m vs T+15m)")
    print("  " + "-" * 55)

    # Raw PF (not bootstrapped)
    def raw_pf(pnls):
        wins = sum(p for p in pnls if p > 0)
        losses = abs(sum(p for p in pnls if p <= 0))
        return wins / losses if losses > 0 else float("inf")

    t1_raw = raw_pf(all_t1)
    t15_raw = raw_pf(all_t15)
    print(f"    T+1m  raw PF: {t1_raw:.6f}")
    print(f"    T+15m raw PF: {t15_raw:.6f}")
    print(f"    Difference:   {abs(t1_raw - t15_raw):.6f}")

    if abs(t1_raw - t15_raw) < 0.0001:
        print(f"    IDENTICAL TO 4 DECIMALS — possible bug")
    elif abs(t1_raw - t15_raw) < 0.01:
        print(f"    Close but distinct — coincidence at small n")
    else:
        print(f"    Clearly different values")

    # Dump winner/loser sets
    t1_winners = sum(1 for p in all_t1 if p > 0)
    t1_losers = sum(1 for p in all_t1 if p <= 0)
    t15_winners = sum(1 for p in all_t15 if p > 0)
    t15_losers = sum(1 for p in all_t15 if p <= 0)
    overlap_winners = sum(1 for i in range(len(valid)) if all_t1[i] > 0 and all_t15[i] > 0)

    print(f"\n    T+1m:  {t1_winners}W / {t1_losers}L  (sum_wins={sum(p for p in all_t1 if p > 0):.4f}, sum_losses={sum(p for p in all_t1 if p <= 0):.4f})")
    print(f"    T+15m: {t15_winners}W / {t15_losers}L (sum_wins={sum(p for p in all_t15 if p > 0):.4f}, sum_losses={sum(p for p in all_t15 if p <= 0):.4f})")
    print(f"    Winner overlap: {overlap_winners} scenarios are winners under BOTH exits")
    print(f"    Different winner sets: {t1_winners + t15_winners - 2 * overlap_winners} trades differ")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
