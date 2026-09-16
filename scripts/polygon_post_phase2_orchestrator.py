"""Post-Phase-2 orchestrator: convert minute_aggs to Parquet, build the
manifest, run sample queries, and build the high-mover catalog.

Run AFTER scripts/polygon_flatfile_pull.py --dataset minute_aggs_v1 ... finishes.

USAGE:
    python scripts/polygon_post_phase2_orchestrator.py
"""
from __future__ import annotations
import json
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WAREHOUSE = REPO / "data" / "polygon_warehouse"
FLATFILES = REPO / "data" / "polygon_flatfiles"


def run(cmd: list[str], desc: str) -> int:
    print(f"\n{'='*70}\n{desc}\n{'='*70}")
    print(f"  $ {' '.join(cmd)}")
    t0 = time.perf_counter()
    rc = subprocess.call(cmd)
    elapsed = time.perf_counter() - t0
    print(f"  -> exit={rc} in {elapsed:.1f}s")
    return rc


def disk_size(path: Path) -> int:
    if not path.exists(): return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def main():
    # Step 1: Verify Phase 2 actually downloaded files
    src = FLATFILES / "minute_aggs_v1"
    if not src.exists():
        print(f"ERROR: {src} does not exist. Run Phase 2 pull first.")
        return 1
    n_files = len(list(src.rglob("*.csv.gz")))
    sz_mb = disk_size(src) / 1e6
    print(f"Phase 2 source: {n_files} files, {sz_mb:.1f} MB")
    if n_files < 100:
        print("WARNING: fewer than 100 files. Did Phase 2 finish?")

    # Step 2: Convert to Parquet warehouse
    rc = run([sys.executable, "scripts/polygon_parquet_warehouse.py",
              "--dataset", "minute_aggs_v1"],
             "STEP 2 — Convert minute_aggs CSV.gz -> Hive Parquet (ZSTD)")
    if rc != 0:
        print("FATAL: parquet conversion failed.")
        return rc

    # Step 3: Verify warehouse
    wh = WAREHOUSE / "minute_aggs"
    n_parquet = len(list(wh.rglob("*.parquet")))
    sz_wh_mb = disk_size(wh) / 1e6
    print(f"\nWarehouse: {n_parquet} parquet files, {sz_wh_mb:.1f} MB")
    print(f"Compression ratio: {sz_mb / max(sz_wh_mb, 1):.2f}x smaller")

    # Step 4: Build day_aggs from minute_aggs (rollup; cheap from warehouse)
    # (skipped — day_aggs already has its own pull path; could add later)

    # Step 5: Sample queries to validate
    rc = run([sys.executable, "scripts/polygon_warehouse_query_demo.py"],
             "STEP 5 — Sample queries against day_aggs warehouse")

    # Step 6: Build high-mover catalog (the big one)
    rc = run([sys.executable, "scripts/polygon_high_mover_catalog.py",
              "--source", "minute", "--threshold", "0.30",
              "--price-min", "0.50", "--price-max", "50",
              "--volume-min", "100000"],
             "STEP 6 — Build high-mover catalog (>=30%) from minute_aggs")

    print("\n" + "="*70)
    print("PHASE 2 ORCHESTRATION COMPLETE")
    print("="*70)
    print("Next steps:")
    print("  1. Re-run doc 89 5-hypothesis backtest on the warehouse")
    print("  2. Re-run doc 90 lottery backtest with --universe full")
    print("  3. Compute Ising magnetization (doc 95 §9 idea #9)")
    print("  4. Schedule Phase 3 (filtered trades for high-mover catalog)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
