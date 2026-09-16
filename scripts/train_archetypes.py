#!/usr/bin/env python
"""D214: Train archetype classifier and build empirical decay curves.

Loads scenarios with real minute bars, clusters by pre-entry features
(stratified by is_day2_runner), and computes per-archetype price decay
curves. Saves model for production use.

Usage:
    python scripts/train_archetypes.py
    python scripts/train_archetypes.py --haircut 0.7  # Target = peak_mean × 0.7
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

_DATA = Path(__file__).resolve().parent.parent / "data"


def main() -> None:
    parser = argparse.ArgumentParser(description="D214: Train archetype exit system")
    parser.add_argument("--haircut", type=float, default=0.7, help="Target haircut on peak mean")
    args = parser.parse_args()

    print("=" * 70)
    print("  D214: ARCHETYPE TRAINING")
    print("=" * 70)

    # 1. Load scenarios
    scenario_path = _DATA / "scenarios" / "gap_scenarios.json"
    data = json.loads(scenario_path.read_text())
    scenarios = data if isinstance(data, list) else data.get("scenarios", [])
    print(f"\n  Loaded {len(scenarios)} scenarios")

    # 2. Load minute bars, drop scenarios without real bars
    bar_dir = _DATA / "scenarios" / "minute_bars"
    valid_scenarios = []
    minute_paths = []
    scenario_meta = []

    for s in scenarios:
        ticker = s["ticker"]
        date = s["date"]
        bar_file = bar_dir / f"{ticker}_{date}.json"

        if not bar_file.exists():
            continue

        bar_data = json.loads(bar_file.read_text())
        bars = bar_data.get("bars", [])

        if len(bars) < 10:
            continue

        # Normalize price path: (price / entry_price) - 1
        entry_price = bars[0]["open"]
        if entry_price <= 0:
            continue

        # D214 FIX: Use (high+close)/2 for curves — close alone understates
        # peaks by ~10x (close < high at every bar by definition).
        # This blend captures intrabar moves while remaining smooth.
        path = []
        for b in bars[:61]:
            mid = (b["high"] + b["close"]) / 2.0
            ret = (mid - entry_price) / entry_price
            path.append(ret)

        # Pad to 61 if shorter
        while len(path) < 61:
            path.append(path[-1] if path else 0.0)

        valid_scenarios.append(s)
        minute_paths.append(path)
        scenario_meta.append({
            "ticker": ticker,
            "date": date,
            "stratum": "day2" if s.get("is_day2_runner", False) else "fresh",
            "scenario_id": f"{ticker}_{date}",
        })

    print(f"  Scenarios with real bars: {len(valid_scenarios)}")
    print(f"  Dropped (no bars): {len(scenarios) - len(valid_scenarios)}")

    if len(valid_scenarios) < 50:
        print("\n  ERROR: Too few scenarios for training (need >= 50)")
        return

    # 3. Fit classifier
    from src.execution.archetype_exit import (
        ArchetypeClassifier, DecayCurveLibrary, save_model, FALLBACK_ARCHETYPE_ID,
    )

    classifier = ArchetypeClassifier()
    try:
        archetype_sizes = classifier.fit(valid_scenarios)
    except ValueError as e:
        print(f"\n  ERROR: {e}")
        return

    print(f"\n  Archetypes: {len(archetype_sizes)}")
    for aid, n in sorted(archetype_sizes.items()):
        meta = classifier._archetype_meta.get(aid, {})
        centroid = meta.get("centroid", {})
        stratum = meta.get("stratum", "?")
        print(
            f"    Archetype {aid}: n={n}, stratum={stratum}, "
            f"gap={np.exp(centroid.get('log_gap_pct', 0))-1:.1%}, "
            f"rvol={np.exp(centroid.get('log_rvol', 0))-1:.1f}x, "
            f"prior_gaps={centroid.get('prior_gap_count', 0):.1f}"
        )

    # Assign all scenarios to archetypes
    archetype_assignments: dict[int, list[int]] = {FALLBACK_ARCHETYPE_ID: []}
    for aid in archetype_sizes:
        archetype_assignments[aid] = []

    for i, s in enumerate(valid_scenarios):
        aid, conf = classifier.predict(
            gap_pct=abs(s.get("gap_pct", 0)),
            rvol=s.get("rvol", 1),
            prior_gap_count=s.get("prior_gap_count", 0) or 0,
            is_day2_runner=s.get("is_day2_runner", False),
        )
        archetype_assignments.setdefault(aid, []).append(i)

    # Show assignment distribution
    print(f"\n  Assignment distribution:")
    for aid, indices in sorted(archetype_assignments.items()):
        if not indices:
            continue
        wins = sum(1 for i in indices if valid_scenarios[i]["outcome"] == "WIN")
        wr = wins / len(indices) * 100 if indices else 0
        label = "FALLBACK" if aid == FALLBACK_ARCHETYPE_ID else f"Archetype {aid}"
        print(f"    {label}: {len(indices)} scenarios, WR={wr:.0f}%")

    # 4. Build decay curves
    library = DecayCurveLibrary()
    library.build_from_paths(
        archetype_assignments, minute_paths, scenario_meta,
        target_haircut=args.haircut,
    )

    print(f"\n  Decay curves built: {len(library.archetype_ids())} archetypes")
    for aid in library.archetype_ids():
        curve = library.get_curve(aid)
        if curve:
            print(
                f"    Archetype {aid}: n={curve.n_samples}, "
                f"peak=+{curve.peak_mean_return*100:.1f}% at min {curve.peak_minute}, "
                f"target=+{curve.target_pct*100:.1f}%, "
                f"stop_floor={curve.stop_floor_pct*100:.1f}%, "
                f"time_stop=min {curve.time_stop_minute}"
            )

    # 5. Distinctness test
    distinctness = library.archetype_distinctness_test()
    if distinctness:
        print(f"\n  Archetype distinctness (pairwise tests):")
        for t in distinctness:
            status = "DISTINCT" if t["distinct"] else "WARNING: NOT DISTINCT"
            print(
                f"    A{t['archetype_a']} vs A{t['archetype_b']}: "
                f"p={t['p_value']:.4f} — {status}"
            )

    # 6. Save model
    training_ids = [m["scenario_id"] for m in scenario_meta]
    model_path = str(_DATA / "archetypes" / "archetype_model.json")
    save_model(classifier, library, model_path, training_scenario_ids=training_ids)

    print(f"\n  Model saved: {model_path}")

    # 7. Retrain eligibility trigger
    total_scenarios = len(valid_scenarios)
    min_per_archetype = min(archetype_sizes.values()) if archetype_sizes else 0
    retrain_eligible = total_scenarios >= 500 and min_per_archetype >= 100
    print(f"\n  RETRAIN_ELIGIBLE: {retrain_eligible}")
    print(f"    Total scenarios: {total_scenarios} (need >= 500)")
    print(f"    Min archetype n: {min_per_archetype} (need >= 100)")
    if not retrain_eligible:
        needed = max(0, 500 - total_scenarios)
        print(f"    Need ~{needed} more scenarios ({needed // 10} more trading sessions)")
    print("=" * 70)


if __name__ == "__main__":
    main()
