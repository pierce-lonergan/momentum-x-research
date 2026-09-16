"""Profile the Track C state machine PBT to identify the hotspot.

Run with HYP_MAX_EXAMPLES=2000 (default in tests) so the profile reflects
realistic per-commit overhead. cProfile output is written to
`pbt_profile.prof` for inspection via:
    snakeviz pbt_profile.prof  (interactive)
    or
    python -c "import pstats; p=pstats.Stats('pbt_profile.prof'); p.sort_stats('cumulative').print_stats(30)"

Usage:
    HYP_MAX_EXAMPLES=2000 python scripts/profile_pbt.py
"""
from __future__ import annotations

import cProfile
import os
import pstats
import sys
from io import StringIO
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def run_pbt():
    """Drive the state machine TestCase via unittest, mimicking pytest."""
    import unittest
    # Import here so cProfile sees the import time too
    from tests.property.test_bridge_state_machine import TestBridgeBrokerStateMachine
    runner = unittest.TextTestRunner(verbosity=0, stream=open(os.devnull, "w"))
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestBridgeBrokerStateMachine)
    runner.run(suite)


def main() -> int:
    out_path = REPO_ROOT / "pbt_profile.prof"
    print(f"Profiling Track C PBT (HYP_MAX_EXAMPLES={os.environ.get('HYP_MAX_EXAMPLES', '2000')})...")
    pr = cProfile.Profile()
    pr.enable()
    run_pbt()
    pr.disable()
    pr.dump_stats(str(out_path))

    # Print top 30 by cumulative time
    print(f"\nTop 30 functions by cumulative time:\n")
    s = StringIO()
    stats = pstats.Stats(pr, stream=s).sort_stats("cumulative")
    stats.print_stats(30)
    output = s.getvalue()
    # Trim long absolute paths for readability
    lines = output.splitlines()
    for line in lines:
        if "C:\\Users" in line or "site-packages" in line:
            # Keep just the leaf module / function
            print(line)
        else:
            print(line)
    print(f"\nFull profile dumped to: {out_path}")
    print(f"Inspect via: python -c \"import pstats; p=pstats.Stats('{out_path}'); p.sort_stats('tottime').print_stats(30)\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
