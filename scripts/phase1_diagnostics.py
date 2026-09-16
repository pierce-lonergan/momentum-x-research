#!/usr/bin/env python
"""D214 Phase 1 Diagnostics — all four tests before committing to a direction.

A. Smell #9: Rank correlation of archetype curves vs realized paths
B. Extended haircut sweep (0.5 to 1.3)
C. Decompose bar-1: dumb T+60 baseline
D. Purge window impact

Usage:
    python scripts/phase1_diagnostics.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr

sys.path.insert(0, str(Path(__file__).parent.parent))

_DATA = Path(__file__).resolve().parent.parent / "data"


def load_data():
    from src.execution.archetype_exit import (
        ArchetypeClassifier, DecayCurveLibrary, FALLBACK_ARCHETYPE_ID,
    )

    scenario_path = _DATA / "scenarios" / "gap_scenarios.json"
    data = json.loads(scenario_path.read_text())
    scenarios = data if isinstance(data, list) else data.get("scenarios", [])

    bar_dir = _DATA / "scenarios" / "minute_bars"
    valid = []
    paths = []  # (high+close)/2 blend — matches training
    raw_close_paths = []  # raw close paths for live comparison

    for s in scenarios:
        bf = bar_dir / f"{s['ticker']}_{s['date']}.json"
        if not bf.exists():
            continue
        bars = json.loads(bf.read_text()).get("bars", [])
        if len(bars) < 10:
            continue
        entry = bars[0]["open"]
        if entry <= 0:
            continue

        blend_path = [((b["high"] + b["close"]) / 2.0 - entry) / entry for b in bars[:61]]
        close_path = [(b["close"] - entry) / entry for b in bars[:61]]
        while len(blend_path) < 61:
            blend_path.append(blend_path[-1] if blend_path else 0.0)
        while len(close_path) < 61:
            close_path.append(close_path[-1] if close_path else 0.0)

        valid.append(s)
        paths.append(blend_path)
        raw_close_paths.append(close_path)

    clf = ArchetypeClassifier()
    clf.fit(valid)

    assignments = defaultdict(list)
    for i, s in enumerate(valid):
        aid, _ = clf.predict(
            abs(s.get("gap_pct", 0)), s.get("rvol", 1),
            s.get("prior_gap_count", 0) or 0, s.get("is_day2_runner", False),
        )
        assignments[aid].append(i)

    meta = [{"stratum": "day2" if s.get("is_day2_runner") else "fresh"} for s in valid]
    lib = DecayCurveLibrary()
    lib.build_from_paths(dict(assignments), paths, meta)

    return valid, paths, raw_close_paths, clf, lib, assignments


def main():
    from src.execution.archetype_exit import FALLBACK_ARCHETYPE_ID
    from scripts.validate_archetypes import (
        simulate_exit, simulate_bar1_exit, simulate_archetype_exit,
        compute_profit_factor, bootstrap_pf,
    )

    print("=" * 70)
    print("  D214 PHASE 1: DIAGNOSTIC BATTERY")
    print("=" * 70)

    valid, paths, raw_close_paths, clf, lib, assignments = load_data()
    n = len(valid)

    # ══════════════════════════════════════════════════════════════
    # A. SMELL #9: Rank correlation of curves vs realized paths
    # ══════════════════════════════════════════════════════════════
    print("\n  A. SMELL #9: Curve-Path Rank Correlation")
    print("  " + "-" * 55)

    test_minutes = [1, 3, 5, 10, 15, 30]
    rng = np.random.default_rng(42)

    for aid in sorted(lib.archetype_ids()):
        curve = lib.get_curve(aid)
        if not curve:
            continue
        indices = assignments.get(aid, [])
        if len(indices) < 10:
            continue

        print(f"\n    Archetype {aid} (n={len(indices)}):")
        print(f"    {'Min':>5s} {'Curve mean':>11s} {'rho(learned)':>13s} {'p-val':>7s} {'rho(random)':>12s} {'Signal?':>8s}")

        for minute in test_minutes:
            if minute >= 61:
                continue

            # Get curve prediction at this minute
            curve_mean = curve.curves.get(minute, {}).get("mean", 0)

            # Get realized returns for all scenarios in this archetype
            realized = [paths[i][minute] for i in indices]

            # Learned curve prediction is the SAME value for all scenarios
            # in the archetype. Spearman correlation of a constant = undefined.
            # Instead: compute correlation between the ARCHETYPE'S RANKING
            # of scenarios at this minute and the REALIZED RANKING.
            #
            # Better metric: how well does the archetype curve predict
            # the direction (above/below mean) of realized paths?
            #
            # Actually, the right metric is: across ALL scenarios (not just
            # within an archetype), does the curve assignment improve
            # prediction? Compare: does knowing the archetype reduce variance
            # of the residual (realized - curve_mean)?

            # Residual: realized - archetype_mean
            residuals = [r - curve_mean for r in realized]
            # Baseline residual: realized - global_mean
            global_mean = np.mean([paths[i][minute] for i in range(n)])
            baseline_residuals = [paths[i][minute] - global_mean for i in indices]

            # Variance reduction
            var_baseline = np.var(baseline_residuals)
            var_archetype = np.var(residuals)
            var_reduction = 1 - var_archetype / var_baseline if var_baseline > 0 else 0

            # Direction accuracy: does curve_mean predict sign of realized?
            direction_correct = sum(
                1 for r in realized
                if (r > 0 and curve_mean > 0) or (r <= 0 and curve_mean <= 0)
            )
            direction_acc = direction_correct / len(realized)

            # Cross-scenario Spearman: across ALL scenarios, rank by
            # archetype curve prediction, rank by realized return,
            # compute Spearman rho
            # We need to do this across archetypes, not within one.
            # Skip to the cross-archetype analysis below.

            # Random curve prediction for comparison
            random_mean = float(rng.normal(0, 0.01))
            random_residuals = [r - random_mean for r in realized]
            var_random = np.var(random_residuals)
            var_reduction_vs_random = 1 - var_archetype / var_random if var_random > 0 else 0

            print(
                f"    {minute:>5d} {curve_mean*100:>+10.2f}% "
                f"VR={var_reduction*100:>+6.1f}% "
                f"dir_acc={direction_acc:>5.0%} "
                f"VR_vs_rand={var_reduction_vs_random*100:>+6.1f}% "
                f"{'YES' if var_reduction > 0.05 else 'NO':>8s}"
            )

    # Cross-archetype Spearman correlation
    print(f"\n    CROSS-ARCHETYPE SPEARMAN (all {n} scenarios):")
    print(f"    {'Min':>5s} {'rho(learned)':>13s} {'p-val':>8s} {'rho(random)':>13s} {'p-val':>8s} {'Signal?':>8s}")

    for minute in test_minutes:
        # For each scenario, get the archetype curve's prediction at this minute
        learned_predictions = []
        random_predictions = []
        realized_returns = []

        for i in range(n):
            s = valid[i]
            aid, _ = clf.predict(
                abs(s.get("gap_pct", 0)), s.get("rvol", 1),
                s.get("prior_gap_count", 0) or 0, s.get("is_day2_runner", False),
            )
            curve = lib.get_curve(aid)
            if curve:
                pred = curve.curves.get(minute, {}).get("mean", 0)
            else:
                pred = 0.0
            learned_predictions.append(pred)
            random_predictions.append(float(rng.normal(0, np.std(learned_predictions) or 0.01)))
            realized_returns.append(paths[i][minute])

        rho_learned, p_learned = spearmanr(learned_predictions, realized_returns)
        rho_random, p_random = spearmanr(random_predictions, realized_returns)

        signal = "YES" if abs(rho_learned) > 0.2 and p_learned < 0.05 else (
            "WEAK" if abs(rho_learned) > 0.1 else "NO"
        )
        print(
            f"    {minute:>5d} {rho_learned:>+12.3f} {p_learned:>7.4f} "
            f"{rho_random:>+12.3f} {p_random:>7.4f} {signal:>8s}"
        )

    # ══════════════════════════════════════════════════════════════
    # B. EXTENDED HAIRCUT SWEEP (0.5 to 1.3)
    # ══════════════════════════════════════════════════════════════
    print("\n\n  B. EXTENDED HAIRCUT SWEEP")
    print("  " + "-" * 55)
    print(f"    {'Haircut':>7s} {'Target':>7s} {'Net':>5s} {'PF':>6s} {'WR':>5s} {'Avg P&L':>8s} {'n_target':>8s} {'n_z_exit':>8s}")

    for haircut in [0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3]:
        pnls = []
        exit_counts = defaultdict(int)
        for i in range(n):
            s = valid[i]
            aid, conf = clf.predict(
                abs(s.get("gap_pct", 0)), s.get("rvol", 1),
                s.get("prior_gap_count", 0) or 0, s.get("is_day2_runner", False),
            )
            curve = lib.get_curve(aid)
            if curve and conf >= 0.6:
                target = curve.peak_mean_return * haircut
                res = simulate_archetype_exit(
                    paths[i], curve.curves,
                    z_threshold=-0.75, z_consecutive=2,
                    target_pct=target, stop_floor=curve.stop_floor_pct,
                    time_stop=curve.time_stop_minute,
                )
            else:
                res = simulate_bar1_exit(paths[i])
            pnls.append(res["pnl_pct"])
            exit_counts[res["exit_reason"]] += 1

        pf = compute_profit_factor(pnls)
        wr = sum(1 for p in pnls if p > 0) / len(pnls)
        avg = np.mean(pnls) * 100

        # Get representative target for display
        c0 = lib.get_curve(0)
        target_pct = (c0.peak_mean_return * haircut * 100) if c0 else 0

        print(
            f"    {haircut:>7.1f} {target_pct:>6.2f}% {target_pct - 0.5:>+4.1f}% "
            f"{pf:>6.2f} {wr:>4.0%} {avg:>+7.2f}% "
            f"{exit_counts.get('TARGET', 0):>8d} {exit_counts.get('Z_EXIT', 0):>8d}"
        )

    # ══════════════════════════════════════════════════════════════
    # C. DECOMPOSE BAR-1: dumb T+60 baseline
    # ══════════════════════════════════════════════════════════════
    print("\n\n  C. BAR-1 DECOMPOSITION")
    print("  " + "-" * 55)

    # Dumb T+60: exit at close of bar 1 (60 seconds after open)
    dumb_t60_pnls = []
    for i in range(n):
        if len(paths[i]) >= 2:
            pnl = paths[i][1] - 0.005  # Slippage
            dumb_t60_pnls.append(pnl)
        else:
            dumb_t60_pnls.append(0.0)

    # T+120, T+180, T+300, T+600
    time_baselines = {}
    for exit_bar in [1, 2, 3, 5, 10, 15, 20, 30]:
        pnls = []
        for i in range(n):
            if len(paths[i]) > exit_bar:
                pnls.append(paths[i][exit_bar] - 0.005)
            else:
                pnls.append(paths[i][-1] - 0.005)
        pf = compute_profit_factor(pnls)
        wr = sum(1 for p in pnls if p > 0) / len(pnls)
        avg = np.mean(pnls) * 100
        time_baselines[exit_bar] = {"pf": pf, "wr": wr, "avg": avg}

    # Bar-1 (from validate_archetypes)
    bar1_pnls = [simulate_bar1_exit(paths[i])["pnl_pct"] for i in range(n)]
    bar1_pf = compute_profit_factor(bar1_pnls)

    print(f"    Bar-1 (simulate_bar1_exit): PF={bar1_pf:.2f}")
    print()
    print(f"    {'Exit bar':>8s} {'Time':>6s} {'PF':>6s} {'WR':>5s} {'Avg P&L':>8s}")
    for bar, stats in sorted(time_baselines.items()):
        time_str = f"T+{bar}m"
        print(f"    {bar:>8d} {time_str:>6s} {stats['pf']:>6.2f} {stats['wr']:>4.0%} {stats['avg']:>+7.2f}%")

    print(f"\n    If bar-1 PF ({bar1_pf:.2f}) matches dumb T+1m PF ({time_baselines[1]['pf']:.2f}):")
    match = abs(bar1_pf - time_baselines[1]["pf"]) < 0.05
    if match:
        print(f"      YES — bar-1's edge IS purely time discipline.")
        print(f"      Implication: build ON TOP of bar-1, don't replace it.")
    else:
        print(f"      NO — bar-1 has something beyond time discipline.")

    # Find optimal exit time
    best_bar = max(time_baselines.items(), key=lambda x: x[1]["pf"])
    print(f"\n    Optimal fixed-time exit: bar {best_bar[0]} (T+{best_bar[0]}m)")
    print(f"      PF={best_bar[1]['pf']:.2f}, WR={best_bar[1]['wr']:.0%}, Avg={best_bar[1]['avg']:+.2f}%")

    # ══════════════════════════════════════════════════════════════
    # D. (Informational) Purge window note
    # ══════════════════════════════════════════════════════════════
    print("\n\n  D. PURGE WINDOW STATUS")
    print("  " + "-" * 55)
    print("    Not yet implemented in validation gauntlet.")
    print("    Impact: serial gappers (MULN, FFIE) appear on consecutive dates.")
    print("    Without purge, train/test can share adjacent-day scenarios.")
    print("    Should add 1-trading-day purge before next gauntlet run.")

    # ══════════════════════════════════════════════════════════════
    # SUMMARY
    # ══════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("  PHASE 1 SUMMARY — DECISION POINT")
    print("=" * 70)
    print(f"""
    Smell #9:     See correlation table above.
    Haircut:      See extended sweep above.
    Bar-1:        See time-exit decomposition above.

    Read the correlation numbers and the bar-1 decomposition
    before deciding Phase 2 direction.
    """)
    print("=" * 70)


if __name__ == "__main__":
    main()
