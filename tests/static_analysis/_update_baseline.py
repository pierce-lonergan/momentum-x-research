"""Regenerate `_baselines.json` from current source state.

Usage:
    python tests/static_analysis/_update_baseline.py          # write new baseline
    python tests/static_analysis/_update_baseline.py --check  # exit 1 if would change

The `--check` mode is useful for reviewers verifying that a PR's claimed
cleanup actually reduced violation counts (or for CI to detect drift).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

# Make the package importable when running this file directly
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.static_analysis import _ast_helpers as helpers  # noqa: E402

# Import each visitor factory. We do this lazily inside main() so a syntax
# error in one test file doesn't kill the whole baseline regeneration.
KIND_TO_MODULE: dict[str, str] = {
    "frozen-mutate": "tests.static_analysis.test_frozen_mutations",
    "async-leak": "tests.static_analysis.test_async_lifecycle",
    "silent-handler": "tests.static_analysis.test_silent_handlers",
    "relative-path": "tests.static_analysis.test_relative_paths",
    "ps-datetime-utc": "tests.static_analysis.test_powershell_safety",
    "ps-pipe-deadlock": "tests.static_analysis.test_powershell_safety",
}


def _scan_kind(kind: str, module_name: str) -> dict[str, int]:
    """Import the test module and run its visitor over all production files."""
    import importlib
    mod = importlib.import_module(module_name)

    # Each test module exports `scan(kind: str) -> list[Violation]`
    if not hasattr(mod, "scan"):
        raise RuntimeError(
            f"{module_name} must define `scan(kind: str) -> list[Violation]`"
        )
    violations = mod.scan(kind)
    counts: dict[str, int] = defaultdict(int)
    for v in violations:
        counts[v.path] += 1
    return dict(counts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Don't write; exit 1 if regeneration would change the baseline.",
    )
    args = parser.parse_args()

    new_counts: dict[str, dict[str, int]] = {}
    for kind, module_name in KIND_TO_MODULE.items():
        try:
            new_counts[kind] = _scan_kind(kind, module_name)
        except Exception as e:
            print(f"  [ERROR] {kind}: {type(e).__name__}: {e}", file=sys.stderr)
            new_counts[kind] = {}

    if args.check:
        old_raw = helpers.load_baselines()
        # Normalize for comparison (drop zero entries)
        old_clean = {k: {f: c for f, c in v.items() if c > 0} for k, v in old_raw.items()}
        new_clean = {k: {f: c for f, c in v.items() if c > 0} for k, v in new_counts.items()}
        if old_clean == new_clean:
            print("baseline up to date")
            return 0
        # Show diff
        print("baseline DRIFT detected:")
        for kind in sorted(set(old_clean) | set(new_clean)):
            old_files = old_clean.get(kind, {})
            new_files = new_clean.get(kind, {})
            for f in sorted(set(old_files) | set(new_files)):
                ob, nb = old_files.get(f, 0), new_files.get(f, 0)
                if ob != nb:
                    sign = "+" if nb > ob else "-"
                    print(f"  {kind}  {f}  {ob} -> {nb}  ({sign}{abs(nb - ob)})")
        return 1

    helpers.save_baselines(new_counts)
    total = sum(sum(v.values()) for v in new_counts.values())
    print(f"wrote {helpers.BASELINE_PATH.relative_to(helpers.REPO_ROOT)}")
    print(f"  total violations baselined: {total}")
    for kind in sorted(new_counts):
        files = new_counts[kind]
        if files:
            print(f"  {kind}: {sum(files.values())} across {len(files)} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
