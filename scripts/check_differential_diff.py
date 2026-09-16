"""Pre-push differential check — gate any patch that touches the
critical execution stack on the differential harness passing.

Usage (from pre-push hook):
    python scripts/check_differential_diff.py [base_ref]

Where `base_ref` defaults to `origin/develop`. The script:
  1. Inspects `git diff --name-only base_ref...HEAD` for hits on the
     gated file list (bridge.py, alpaca_executor.py, trade_journal.py,
     main.py).
  2. If no hit → exits 0 (no diff-test required).
  3. If hit → runs the pinned differential harness suite as a sanity
     check (the harness itself must be green).
  4. Future enhancement: replay a recorded session against the
     pre-/post-patch SHAs and assert equivalence outside the documented
     allowlist. Stub for now — gates on the harness suite passing,
     which proves the harness itself is operational.

Wire into `.git/hooks/pre-push` (chmod +x):
    #!/bin/sh
    exec python scripts/check_differential_diff.py origin/develop

Exit codes:
  0 = pass (no gated file modified, OR harness suite green)
  1 = harness suite failed — push blocked
  2 = subprocess invocation failed (non-blocking; warn + allow push)

This is the FOUNDATION for the differential-replay pre-push gate.
The full per-PR replay comparison (checkout pre-patch SHA, run replay,
checkout post-patch SHA, run replay, compare) ships in a follow-up
once Phase 0 has captured one full recorded session worth using as
the canonical replay input.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

# Files whose modification triggers the differential check.
GATED_FILES: set[str] = {
    "src/execution/bridge.py",
    "src/execution/alpaca_executor.py",
    "src/analysis/trade_journal.py",
    "main.py",
}


def _git_diff_files(base_ref: str) -> list[str]:
    """Return list of repo-relative paths changed since base_ref."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
            capture_output=True, text=True, check=True,
        )
        return [
            line.strip().replace("\\", "/")
            for line in result.stdout.splitlines()
            if line.strip()
        ]
    except subprocess.CalledProcessError as e:
        print(f"[differential] git diff failed: {e}", file=sys.stderr)
        return []


def _run_harness_suite() -> int:
    """Run the differential harness's own test suite.

    Returns the pytest exit code (0 = green)."""
    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest",
            "tests/unit/test_differential_harness.py",
            "tests/unit/test_git_replay.py",
            "-q", "--no-header",
        ],
        cwd=str(repo_root),
    )
    return result.returncode


def _try_per_sha_replay(base_ref: str) -> int:
    """If a Phase 0 session exists for today, run the per-SHA replay
    comparison via src.testing.git_replay.compare_two_shas. Returns:
      0 = no divergence (or no Phase 0 capture available — degrade safely)
      1 = divergence detected
      2 = replay setup failed (non-blocking; warn)
    """
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))
    try:
        from src.testing.git_replay import compare_two_shas
    except ImportError as e:
        print(f"[differential] git_replay import failed (non-blocking): {e}",
              file=sys.stderr)
        return 2

    # Find the most recent Phase 0 partition
    from datetime import date, timedelta
    today = date.today()
    base_dir = "data/instrumentation"
    session_date = None
    for delta in range(0, 14):
        d = today - timedelta(days=delta)
        candidate = repo_root / base_dir / "trade_context" / f"session_date={d.isoformat()}"
        if candidate.exists():
            session_date = d.isoformat()
            break
    if session_date is None:
        print(
            "[differential] no Phase 0 capture found in last 14 days — "
            "skipping per-SHA replay (run after Monday's session captures data)"
        )
        return 0

    print(f"[differential] per-SHA replay against session {session_date}")
    try:
        result = compare_two_shas(
            base_sha=base_ref, head_sha="HEAD",
            base_dir=base_dir, session_date=session_date,
            handler_module="src.testing.git_replay.identity_handlers",
            repo_root=repo_root,
        )
    except Exception as e:
        print(f"[differential] per-SHA replay raised (non-blocking): {e}", file=sys.stderr)
        return 2

    if result.error:
        print(f"[differential] per-SHA replay error (non-blocking): {result.error}",
              file=sys.stderr)
        return 2
    if result.divergence_report is None:
        print("[differential] per-SHA replay produced no report (non-blocking)")
        return 2

    rep = result.divergence_report
    if rep.passed:
        print(f"[differential] per-SHA replay clean: 0 divergences across "
              f"{rep.total_ops} ops")
        return 0
    print(
        f"[differential] PER-SHA REPLAY DIVERGED: {len(rep.out_of_allowlist)} "
        f"out-of-allowlist divergences across {rep.total_ops} ops",
        file=sys.stderr,
    )
    for d in rep.out_of_allowlist[:5]:
        print(f"  op[{d.op_index}] {d.operation.name}: {d.reason}", file=sys.stderr)
    if len(rep.out_of_allowlist) > 5:
        print(f"  ... and {len(rep.out_of_allowlist) - 5} more", file=sys.stderr)
    return 1


def main() -> int:
    base_ref = sys.argv[1] if len(sys.argv) > 1 else "origin/develop"

    changed = _git_diff_files(base_ref)
    if not changed:
        print(f"[differential] no diff vs {base_ref} — skipping check")
        return 0

    gated_hits = [f for f in changed if f in GATED_FILES]
    if not gated_hits:
        print(
            f"[differential] {len(changed)} files changed, none in gated set "
            f"({sorted(GATED_FILES)}) — skipping check",
        )
        return 0

    print(f"[differential] gated files modified: {gated_hits}")
    print("[differential] running harness suite...")
    rc = _run_harness_suite()
    if rc != 0:
        print(
            "[differential] HARNESS SUITE FAILED — push blocked. "
            "Fix tests/unit/test_differential_harness.py or test_git_replay.py first.",
            file=sys.stderr,
        )
        return 1

    # Phase 2: per-SHA replay against latest Phase 0 capture (degrades to no-op
    # when no capture exists yet)
    replay_rc = _try_per_sha_replay(base_ref)
    if replay_rc == 1:
        print(
            "[differential] PUSH BLOCKED — per-SHA replay shows undocumented "
            "divergence on gated files. Review the divergences above.",
            file=sys.stderr,
        )
        return 1

    print("[differential] all checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
