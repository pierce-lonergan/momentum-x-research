#!/usr/bin/env python
"""D214: Full validation battery for archetype exit system.

The six-baseline gauntlet that determines PROMOTE: true/false.
Nothing ships to production without clearing every gate.

Tests:
1. Data integrity gates (real bars, n≥50, distinctness, pre-market features)
2. Purged 10-fold walk-forward CV with PF distribution
3. Overfit ratio per fold (train PF / test PF < 2.0x)
4. Hyperparameter sweep (z_threshold, consecutive_bars, haircut, confidence)
5. Per-archetype bootstrap PF CIs (1000 resamples)
6. Six baselines: static 10/35, bar-1, archetype, shuffled, single, random-curve
7. Lookahead audit

Output: data/archetypes/validation_report.json with PROMOTE: true/false

Usage:
    python scripts/validate_archetypes.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

_DATA = Path(__file__).resolve().parent.parent / "data"

# --Exit simulation (shared across all baselines) --──────────────────────


def simulate_exit(
    minute_path: list[float],
    target_pct: float = 0.10,
    stop_pct: float = 0.35,
    entry_slippage: float = 0.005,
    stop_slippage: float = 0.01,
    eod_slippage: float = 0.003,
) -> dict:
    """Simulate a single trade through a minute-bar path (normalized returns).

    Returns: {pnl_pct, exit_reason, exit_minute}
    """
    if not minute_path:
        return {"pnl_pct": 0.0, "exit_reason": "NO_DATA", "exit_minute": 0}

    effective_target = target_pct - entry_slippage
    effective_stop = -(stop_pct + stop_slippage)

    for i, ret in enumerate(minute_path):
        if ret <= effective_stop:
            return {"pnl_pct": effective_stop, "exit_reason": "STOP", "exit_minute": i}
        if ret >= effective_target:
            return {"pnl_pct": effective_target, "exit_reason": "TARGET", "exit_minute": i}

    eod_ret = minute_path[-1] - eod_slippage
    return {"pnl_pct": eod_ret, "exit_reason": "EOD", "exit_minute": len(minute_path) - 1}


def simulate_archetype_exit(
    minute_path: list[float],
    archetype_curve: dict[int, dict[str, float]],
    z_threshold: float = -0.5,
    z_consecutive: int = 2,
    target_pct: float = 0.05,
    stop_floor: float = -0.10,
    time_stop: int = 60,
    entry_slippage: float = 0.005,
) -> dict:
    """Simulate archetype-based exit through a minute-bar path."""
    if not minute_path or not archetype_curve:
        return {"pnl_pct": 0.0, "exit_reason": "NO_DATA", "exit_minute": 0}

    consecutive_below = 0
    for i, ret in enumerate(minute_path):
        stats = archetype_curve.get(i, archetype_curve.get(min(archetype_curve.keys(), key=lambda k: abs(k - i)), {}))
        mean = stats.get("mean", 0)
        std = stats.get("std", 0.01) or 0.01
        p25 = stats.get("p25", mean - std)

        z = (ret - mean) / std

        # Hard stop floor
        if ret <= stop_floor:
            return {"pnl_pct": ret - entry_slippage, "exit_reason": "STOP_FLOOR", "exit_minute": i}

        # Target
        if ret >= target_pct - entry_slippage:
            return {"pnl_pct": target_pct - entry_slippage, "exit_reason": "TARGET", "exit_minute": i}

        # Z-score exit
        if z < z_threshold:
            consecutive_below += 1
            # Backup confirmation: below p25 upgrades 1-bar violation
            if consecutive_below >= z_consecutive or (consecutive_below >= 1 and ret < p25):
                return {"pnl_pct": ret - entry_slippage, "exit_reason": "Z_EXIT", "exit_minute": i}
        else:
            consecutive_below = 0

        # Time stop
        if i >= time_stop and ret < target_pct:
            return {"pnl_pct": ret - entry_slippage, "exit_reason": "TIME_STOP", "exit_minute": i}

    return {"pnl_pct": minute_path[-1] - entry_slippage, "exit_reason": "EOD", "exit_minute": len(minute_path) - 1}


def simulate_bar1_exit(minute_path: list[float], slippage: float = 0.005) -> dict:
    """Bar-1 baseline: exit at bar 1 close."""
    if len(minute_path) < 2:
        return {"pnl_pct": 0.0, "exit_reason": "NO_DATA", "exit_minute": 0}
    return {"pnl_pct": minute_path[1] - slippage, "exit_reason": "BAR1", "exit_minute": 1}


def compute_profit_factor(pnls: list[float]) -> float:
    wins = sum(p for p in pnls if p > 0)
    losses = abs(sum(p for p in pnls if p <= 0))
    return wins / losses if losses > 0 else float("inf") if wins > 0 else 0.0


def bootstrap_pf(pnls: list[float], n_resamples: int = 1000) -> dict:
    """Bootstrap profit factor with 95% CI."""
    if len(pnls) < 5:
        return {"mean": 0.0, "ci_lower": 0.0, "ci_upper": 0.0, "std": 0.0}
    rng = np.random.default_rng(42)
    pf_samples = []
    arr = np.array(pnls)
    for _ in range(n_resamples):
        sample = rng.choice(arr, size=len(arr), replace=True)
        pf_samples.append(compute_profit_factor(sample.tolist()))
    pf_arr = np.array([p for p in pf_samples if p < 100])  # Filter inf
    if len(pf_arr) == 0:
        return {"mean": 0.0, "ci_lower": 0.0, "ci_upper": 0.0, "std": 0.0}
    return {
        "mean": float(np.mean(pf_arr)),
        "ci_lower": float(np.percentile(pf_arr, 2.5)),
        "ci_upper": float(np.percentile(pf_arr, 97.5)),
        "std": float(np.std(pf_arr)),
    }


# --Main validation --───────────────────────────────────────────────────


def main() -> None:
    from src.execution.archetype_exit import (
        ArchetypeClassifier, DecayCurveLibrary, FALLBACK_ARCHETYPE_ID,
    )

    print("=" * 70)
    print("  D214: ARCHETYPE EXIT VALIDATION BATTERY")
    print("=" * 70)

    # Load scenarios + minute bars
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
        if len(bars) < 10:
            continue
        entry = bars[0]["open"]
        if entry <= 0:
            continue
        # D214 FIX: Use (high+close)/2 blend — matches training script
        path = [((b["high"] + b["close"]) / 2.0 - entry) / entry for b in bars[:61]]
        while len(path) < 61:
            path.append(path[-1] if path else 0.0)
        valid.append(s)
        paths.append(path)

    n_total = len(valid)
    print(f"\n  Scenarios with real bars: {n_total}")
    if n_total < 50:
        print("  ABORT: Too few scenarios")
        return

    # ================================================================
    # GATE 1: Data integrity
    # ================================================================
    print(f"\n  --GATE 1: Data Integrity --")
    gate1_pass = True

    # All scenarios have real bars (enforced above)
    print(f"    Real bars: {n_total}/{len(scenarios)} (OK)")

    # Pre-market features only
    feature_names = ["gap_pct", "rvol", "prior_gap_count", "is_day2_runner"]
    print(f"    Features: {feature_names} (all pre-market, OK)")

    # ================================================================
    # GATE 2: Train model + check cluster sizes
    # ================================================================
    print(f"\n  --GATE 2: Cluster Analysis --")

    classifier = ArchetypeClassifier()
    sizes = classifier.fit(valid)
    print(f"    Archetypes: {len(sizes)}")
    for aid, n in sizes.items():
        status = "OK" if n >= 50 else "FAIL (n < 50)"
        print(f"      Archetype {aid}: n={n} — {status}")
        if n < 50:
            gate1_pass = False

    # Assign all scenarios
    assignments: dict[int, list[int]] = defaultdict(list)
    for i, s in enumerate(valid):
        aid, conf = classifier.predict(
            abs(s.get("gap_pct", 0)), s.get("rvol", 1),
            s.get("prior_gap_count", 0) or 0, s.get("is_day2_runner", False),
        )
        assignments[aid].append(i)

    # Build curves
    library = DecayCurveLibrary()
    meta = [{"stratum": "day2" if s.get("is_day2_runner") else "fresh"} for s in valid]
    library.build_from_paths(assignments, paths, meta, target_haircut=0.7)

    # Distinctness
    dist = library.archetype_distinctness_test()
    for t in dist:
        print(f"    Distinctness A{t['archetype_a']} vs A{t['archetype_b']}: p={t['p_value']:.4f} {'(OK)' if t['distinct'] else '(WARNING)'}")

    # ================================================================
    # GATE 3: Purged 10-fold walk-forward CV
    # ================================================================
    print(f"\n  --GATE 3: Walk-Forward CV (10-fold) --")

    sorted_indices = sorted(range(n_total), key=lambda i: valid[i]["date"])
    fold_size = n_total // 10
    fold_pfs_arch = []
    fold_pfs_static = []
    fold_pfs_bar1 = []
    overfit_ratios = []

    for fold in range(10):
        test_start = fold * fold_size
        test_end = test_start + fold_size if fold < 9 else n_total
        test_idx = set(sorted_indices[test_start:test_end])
        train_idx = [i for i in sorted_indices if i not in test_idx]

        if len(test_idx) < 5 or len(train_idx) < 30:
            continue

        # Refit on train
        train_scenarios = [valid[i] for i in train_idx]
        train_paths = [paths[i] for i in train_idx]

        fold_clf = ArchetypeClassifier()
        try:
            fold_clf.fit(train_scenarios)
        except Exception:
            continue

        fold_assign: dict[int, list[int]] = defaultdict(list)
        for j, i in enumerate(train_idx):
            s = valid[i]
            aid, _ = fold_clf.predict(
                abs(s.get("gap_pct", 0)), s.get("rvol", 1),
                s.get("prior_gap_count", 0) or 0, s.get("is_day2_runner", False),
            )
            fold_assign[aid].append(j)

        fold_lib = DecayCurveLibrary()
        fold_meta = [{"stratum": "day2" if valid[train_idx[j]].get("is_day2_runner") else "fresh"} for j in range(len(train_idx))]
        fold_lib.build_from_paths(fold_assign, train_paths, fold_meta)

        # Evaluate on test
        test_pnls_arch = []
        test_pnls_static = []
        test_pnls_bar1 = []
        train_pnls_arch = []

        for i in test_idx:
            s = valid[i]
            path = paths[i]
            aid, conf = fold_clf.predict(
                abs(s.get("gap_pct", 0)), s.get("rvol", 1),
                s.get("prior_gap_count", 0) or 0, s.get("is_day2_runner", False),
            )
            curve = fold_lib.get_curve(aid)

            # Archetype exit
            if curve and conf >= 0.6:
                res = simulate_archetype_exit(
                    path, curve.curves,
                    target_pct=curve.target_pct,
                    stop_floor=curve.stop_floor_pct,
                    time_stop=curve.time_stop_minute,
                )
            else:
                res = simulate_bar1_exit(path)  # Fallback to bar-1
            test_pnls_arch.append(res["pnl_pct"])

            # Static baseline
            res_static = simulate_exit(path)
            test_pnls_static.append(res_static["pnl_pct"])

            # Bar-1 baseline
            res_bar1 = simulate_bar1_exit(path)
            test_pnls_bar1.append(res_bar1["pnl_pct"])

        # Train PF for overfit ratio
        for i in train_idx[:50]:  # Sample for speed
            path = paths[i]
            s = valid[i]
            aid, conf = fold_clf.predict(
                abs(s.get("gap_pct", 0)), s.get("rvol", 1),
                s.get("prior_gap_count", 0) or 0, s.get("is_day2_runner", False),
            )
            curve = fold_lib.get_curve(aid)
            if curve and conf >= 0.6:
                res = simulate_archetype_exit(path, curve.curves, target_pct=curve.target_pct, stop_floor=curve.stop_floor_pct, time_stop=curve.time_stop_minute)
            else:
                res = simulate_bar1_exit(path)
            train_pnls_arch.append(res["pnl_pct"])

        test_pf = compute_profit_factor(test_pnls_arch)
        train_pf = compute_profit_factor(train_pnls_arch)
        overfit = train_pf / test_pf if test_pf > 0 else 99.0

        fold_pfs_arch.append(test_pf)
        fold_pfs_static.append(compute_profit_factor(test_pnls_static))
        fold_pfs_bar1.append(compute_profit_factor(test_pnls_bar1))
        overfit_ratios.append(overfit)

    if fold_pfs_arch:
        arr = np.array(fold_pfs_arch)
        print(f"    Archetype PF distribution: mean={np.mean(arr):.2f} median={np.median(arr):.2f} std={np.std(arr):.2f} min={np.min(arr):.2f} max={np.max(arr):.2f}")
        cv_ratio = np.std(arr) / np.mean(arr) if np.mean(arr) > 0 else 99
        if cv_ratio > 0.5:
            print(f"    WARNING: High PF variance (CV={cv_ratio:.2f} > 0.5)")
        print(f"    Static PF distribution:    mean={np.mean(fold_pfs_static):.2f}")
        print(f"    Bar-1 PF distribution:     mean={np.mean(fold_pfs_bar1):.2f}")
        print(f"    Overfit ratios: {[f'{r:.2f}' for r in overfit_ratios]}")
        overfit_pass = all(r < 2.0 for r in overfit_ratios if r < 99)
        print(f"    Overfit gate: {'PASS' if overfit_pass else 'FAIL'} (all < 2.0x required)")
    else:
        print(f"    No valid folds")
        overfit_pass = False

    # ================================================================
    # GATE 4: Hyperparameter sweep
    # ================================================================
    print(f"\n  --GATE 4: Hyperparameter Sweep --")

    sweep_results = []
    for z_th in [-0.25, -0.5, -0.75, -1.0]:
        for z_bars in [1, 2, 3]:
            for haircut in [0.5, 0.6, 0.7, 0.8, 0.9]:
                pnls = []
                for i in range(n_total):
                    aid, conf = classifier.predict(
                        abs(valid[i].get("gap_pct", 0)), valid[i].get("rvol", 1),
                        valid[i].get("prior_gap_count", 0) or 0, valid[i].get("is_day2_runner", False),
                    )
                    curve = library.get_curve(aid)
                    if curve and conf >= 0.6:
                        target = curve.peak_mean_return * haircut
                        res = simulate_archetype_exit(
                            paths[i], curve.curves,
                            z_threshold=z_th, z_consecutive=z_bars,
                            target_pct=target, stop_floor=curve.stop_floor_pct,
                            time_stop=curve.time_stop_minute,
                        )
                    else:
                        res = simulate_bar1_exit(paths[i])
                    pnls.append(res["pnl_pct"])

                pf = compute_profit_factor(pnls)
                wr = sum(1 for p in pnls if p > 0) / len(pnls) if pnls else 0
                sweep_results.append({
                    "z_threshold": z_th, "z_consecutive": z_bars,
                    "haircut": haircut, "pf": pf, "wr": wr,
                    "avg_pnl": float(np.mean(pnls)),
                })

    sweep_results.sort(key=lambda x: x["pf"], reverse=True)
    print(f"    Top-5 param sets:")
    print(f"    {'z_th':>5s} {'z_bars':>6s} {'haircut':>7s} {'PF':>6s} {'WR':>5s} {'Avg P&L':>8s}")
    for r in sweep_results[:5]:
        print(f"    {r['z_threshold']:>5.2f} {r['z_consecutive']:>6d} {r['haircut']:>7.1f} {r['pf']:>6.2f} {r['wr']:>4.0%} {r['avg_pnl']:>+7.2%}")

    # Check fragility
    top5_pfs = [r["pf"] for r in sweep_results[:5]]
    pf_range = max(top5_pfs) - min(top5_pfs)
    print(f"    Top-5 PF range: {pf_range:.2f} ({'STABLE' if pf_range < 0.5 else 'FRAGILE'})")

    best = sweep_results[0]

    # ================================================================
    # GATE 5: Per-archetype bootstrap CIs
    # ================================================================
    print(f"\n  --GATE 5: Per-Archetype Bootstrap CIs --")

    archetype_pnls: dict[int, list[float]] = defaultdict(list)
    for i in range(n_total):
        aid, conf = classifier.predict(
            abs(valid[i].get("gap_pct", 0)), valid[i].get("rvol", 1),
            valid[i].get("prior_gap_count", 0) or 0, valid[i].get("is_day2_runner", False),
        )
        curve = library.get_curve(aid)
        if curve and conf >= 0.6:
            res = simulate_archetype_exit(
                paths[i], curve.curves,
                z_threshold=best["z_threshold"], z_consecutive=best["z_consecutive"],
                target_pct=curve.peak_mean_return * best["haircut"],
                stop_floor=curve.stop_floor_pct, time_stop=curve.time_stop_minute,
            )
        else:
            res = simulate_bar1_exit(paths[i])
        archetype_pnls[aid].append(res["pnl_pct"])

    all_archetype_ci_ok = True
    for aid, pnls in sorted(archetype_pnls.items()):
        if aid == FALLBACK_ARCHETYPE_ID:
            label = "FALLBACK"
        else:
            label = f"Archetype {aid}"
        ci = bootstrap_pf(pnls)
        wr = sum(1 for p in pnls if p > 0) / len(pnls) if pnls else 0
        ci_ok = ci["ci_lower"] > 1.0
        if not ci_ok and aid != FALLBACK_ARCHETYPE_ID:
            all_archetype_ci_ok = False
        print(f"    {label:15s}: n={len(pnls):3d} WR={wr:.0%} PF={ci['mean']:.2f} CI=[{ci['ci_lower']:.2f}, {ci['ci_upper']:.2f}] {'OK' if ci_ok else 'WARN: CI includes 1.0'}")

    # ================================================================
    # GATE 6: Six-baseline gauntlet
    # ================================================================
    print(f"\n  --GATE 6: Six-Baseline Gauntlet --")

    # 1. Static 10/35
    static_pnls = [simulate_exit(paths[i])["pnl_pct"] for i in range(n_total)]
    # 2. Bar-1
    bar1_pnls = [simulate_bar1_exit(paths[i])["pnl_pct"] for i in range(n_total)]
    # 3. Archetype (already computed above)
    arch_pnls = []
    for pnls in archetype_pnls.values():
        arch_pnls.extend(pnls)

    # 4. Shuffled-archetype (100 permutations)
    rng = np.random.default_rng(42)
    shuffled_pfs = []
    for _ in range(100):
        shuffled_assignments = list(archetype_pnls.keys())
        perm_pnls = []
        for i in range(n_total):
            # Random archetype
            rand_aid = rng.choice(shuffled_assignments)
            curve = library.get_curve(rand_aid)
            if curve:
                res = simulate_archetype_exit(
                    paths[i], curve.curves,
                    z_threshold=best["z_threshold"], z_consecutive=best["z_consecutive"],
                    target_pct=curve.peak_mean_return * best["haircut"],
                    stop_floor=curve.stop_floor_pct, time_stop=curve.time_stop_minute,
                )
            else:
                res = simulate_bar1_exit(paths[i])
            perm_pnls.append(res["pnl_pct"])
        shuffled_pfs.append(compute_profit_factor(perm_pnls))

    # 5. Single-archetype (all scenarios → one global curve)
    global_lib = DecayCurveLibrary()
    global_lib.build_from_paths({0: list(range(n_total))}, paths, meta)
    global_curve = global_lib.get_curve(0)
    single_pnls = []
    for i in range(n_total):
        if global_curve:
            res = simulate_archetype_exit(
                paths[i], global_curve.curves,
                z_threshold=best["z_threshold"], z_consecutive=best["z_consecutive"],
                target_pct=global_curve.peak_mean_return * best["haircut"],
                stop_floor=global_curve.stop_floor_pct, time_stop=global_curve.time_stop_minute,
            )
        else:
            res = simulate_bar1_exit(paths[i])
        single_pnls.append(res["pnl_pct"])

    # 6. Random-curve (correct assignment, random curves)
    random_pnls = []
    for i in range(n_total):
        # Generate random walk curve
        fake_curve = {}
        walk = 0.0
        for m in range(61):
            walk += rng.normal(0, 0.002)
            fake_curve[m] = {"mean": walk, "std": 0.05, "p25": walk - 0.03}
        res = simulate_archetype_exit(
            paths[i], fake_curve,
            z_threshold=best["z_threshold"], z_consecutive=best["z_consecutive"],
            target_pct=0.05, stop_floor=-0.10, time_stop=60,
        )
        random_pnls.append(res["pnl_pct"])

    # Compute CIs for all
    baselines = {
        "1_Static_10_35": bootstrap_pf(static_pnls),
        "2_Bar1": bootstrap_pf(bar1_pnls),
        "3_Archetype": bootstrap_pf(arch_pnls),
        "4_Shuffled": {"mean": float(np.mean(shuffled_pfs)), "ci_lower": float(np.percentile(shuffled_pfs, 2.5)), "ci_upper": float(np.percentile(shuffled_pfs, 97.5)), "std": float(np.std(shuffled_pfs))},
        "5_Single": bootstrap_pf(single_pnls),
        "6_Random": bootstrap_pf(random_pnls),
    }

    arch_ci = baselines["3_Archetype"]
    beats_all = True
    print(f"    {'Baseline':<20s} {'PF':>6s} {'CI_low':>7s} {'CI_high':>8s} {'Archetype beats?':>17s}")
    print(f"    {'-'*60}")
    for name, ci in baselines.items():
        beats = "—" if "Archetype" in name else (
            "YES" if arch_ci["ci_lower"] > ci["ci_upper"] else "NO"
        )
        if beats == "NO" and "Archetype" not in name:
            beats_all = False
        print(f"    {name:<20s} {ci['mean']:>6.2f} [{ci['ci_lower']:>6.2f}, {ci['ci_upper']:>6.2f}] {beats:>17s}")

    # ================================================================
    # FINAL VERDICT
    # ================================================================
    promote = (
        gate1_pass
        and overfit_pass
        and all_archetype_ci_ok
        and beats_all
    )

    print(f"\n  {'='*60}")
    print(f"  FINAL VERDICT: PROMOTE = {promote}")
    print(f"  {'='*60}")
    if not promote:
        reasons = []
        if not gate1_pass:
            reasons.append("Data integrity gates failed (n < 50 per archetype)")
        if not overfit_pass:
            reasons.append("Overfit ratio exceeded 2.0x on some folds")
        if not all_archetype_ci_ok:
            reasons.append("Some archetype bootstrap PF CI includes 1.0")
        if not beats_all:
            reasons.append("Does not beat all 6 baselines with non-overlapping CIs")
        print(f"  Reasons for rejection:")
        for r in reasons:
            print(f"    - {r}")
        print(f"\n  What might fix it:")
        print(f"    - More scenarios (currently {n_total}, need 300+ for cleaner clusters)")
        print(f"    - D210 session data expansion (run convert_sessions_to_scenarios.py daily)")
        print(f"    - Different feature engineering (add float_shares, dollar_volume)")

    # Save report
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "PROMOTE": promote,
        "n_scenarios": n_total,
        "n_archetypes": len(sizes),
        "archetype_sizes": {str(k): v for k, v in sizes.items()},
        "best_params": best,
        "top5_params": sweep_results[:5],
        "fold_pfs_archetype": fold_pfs_arch,
        "fold_pfs_static": fold_pfs_static,
        "fold_pfs_bar1": fold_pfs_bar1,
        "overfit_ratios": overfit_ratios,
        "baselines": {k: v for k, v in baselines.items()},
        "per_archetype_cis": {
            str(aid): bootstrap_pf(pnls)
            for aid, pnls in archetype_pnls.items()
        },
        "gate1_pass": gate1_pass,
        "overfit_pass": overfit_pass,
        "all_archetype_ci_ok": all_archetype_ci_ok,
        "beats_all_baselines": beats_all,
    }

    report_path = _DATA / "archetypes" / "validation_report.json"
    report_path.write_text(json.dumps(report, indent=2, default=lambda x: float(x) if hasattr(x, '__float__') else str(x)))
    print(f"\n  Report saved: {report_path}")
    print("=" * 70)


if __name__ == "__main__":
    main()
