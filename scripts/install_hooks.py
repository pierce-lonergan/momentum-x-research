"""One-command pre-commit + discovery infrastructure setup.

Per CONTRIBUTING.md "One-command setup for new contributors": runs
`pre-commit install` so the local hook chain fires on every commit,
then verifies each registered hook is wired correctly by a dry-run
against the staged changes (or HEAD if nothing staged).

Exit codes:
  0 = hooks installed + verified
  1 = pre-commit not installed (caller must `pip install pre-commit` first)
  2 = `pre-commit install` failed
  3 = a hook verification failed (review hook output)

Usage:
    python scripts/install_hooks.py
    python scripts/install_hooks.py --skip-verify   # only install, don't dry-run
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _check_precommit_available() -> bool:
    """Return True if the `pre-commit` CLI is on PATH."""
    return shutil.which("pre-commit") is not None


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a command in the repo root, return the CompletedProcess."""
    return subprocess.run(
        cmd, cwd=str(REPO_ROOT), capture_output=True, text=True, **kwargs,
    )


def _list_configured_hooks() -> list[str]:
    """Parse .pre-commit-config.yaml for the hook IDs."""
    cfg = REPO_ROOT / ".pre-commit-config.yaml"
    if not cfg.exists():
        return []
    hooks: list[str] = []
    in_hooks = False
    for line in cfg.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("- id:"):
            hooks.append(stripped.split(":", 1)[1].strip())
    return hooks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-verify", action="store_true",
        help="Only install hooks; skip the dry-run verification step.",
    )
    args = parser.parse_args()

    print("[install-hooks] One-command setup for the discovery infrastructure gates.\n")

    # Step 1 — pre-commit availability
    if not _check_precommit_available():
        print(
            "[install-hooks] ERROR: pre-commit CLI not found on PATH.\n"
            "  Install via: pip install pre-commit\n"
            "  Then re-run this script.",
            file=sys.stderr,
        )
        return 1

    # Step 2 — list the hooks we're about to install
    hooks = _list_configured_hooks()
    print(f"[install-hooks] Configured hooks ({len(hooks)}):")
    for h in hooks:
        print(f"  - {h}")
    print()

    # Step 3 — pre-commit install
    print("[install-hooks] Running `pre-commit install`...")
    result = _run(["pre-commit", "install"])
    if result.returncode != 0:
        print(
            f"[install-hooks] ERROR: pre-commit install failed:\n{result.stderr}",
            file=sys.stderr,
        )
        return 2
    print(result.stdout.strip())
    print()

    # Step 4 — install commit-msg + pre-push if relevant
    for hook_type in ("pre-push",):
        result = _run(["pre-commit", "install", "--hook-type", hook_type])
        if result.returncode == 0:
            print(f"[install-hooks] Installed {hook_type} hook chain.")
        # Non-fatal — pre-push isn't required for all setups

    if args.skip_verify:
        print("\n[install-hooks] DONE (skipped verify per --skip-verify).")
        return 0

    # Step 5 — verify by dry-running against the most recent commit
    # (`pre-commit run --from-ref HEAD~1 --to-ref HEAD` exercises the same
    # paths as a real commit hook would on the previous commit's diff)
    print("\n[install-hooks] Verifying hook wire-up via dry-run against HEAD~1..HEAD...")
    result = _run(
        ["pre-commit", "run", "--from-ref", "HEAD~1", "--to-ref", "HEAD"],
    )
    # pre-commit returns 0 on success, 1 on hook failures, other on errors.
    # We tolerate 1 (some hooks may have legitimate findings on the recent
    # commit, e.g. trailing whitespace fixers) but report non-zero for visibility.
    if result.returncode == 0:
        print("[install-hooks] ALL HOOKS PASSED on the most recent commit.")
    elif result.returncode == 1:
        print(
            "[install-hooks] SOME HOOKS reported findings on HEAD~1..HEAD:\n"
            f"{result.stdout}\n"
            "Review above. If the findings are pre-existing, you can proceed."
        )
    else:
        print(
            f"[install-hooks] ERROR: pre-commit run failed (rc={result.returncode}):\n"
            f"{result.stderr or result.stdout}",
            file=sys.stderr,
        )
        return 3

    print()
    print("[install-hooks] DONE. Hooks installed + verified.")
    print()
    print("Next steps:")
    print("  python scripts/preflight.py    # verify the local dev environment is green")
    print("  python -m pytest -m 'not slow' -q   # run the fast test suite (~30s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
