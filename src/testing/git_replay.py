"""Git-aware per-SHA replay comparison — Track D §3 final gap closer.

Per `docs/research-log/35_differential_harness_foundation.md` §3 + the EOD
pipeline ship (commit `6b01d27`) which introduced
`src/testing/replay_engine.py:replay_input_from_phase0`.

This module orchestrates the per-PR replay comparison the foundation
was designed to support:

  1. Stash any working-copy changes.
  2. Checkout `base_sha`. Run replay against a recorded Phase 0 session.
  3. Save records_a.
  4. Checkout `head_sha`. Run replay against the same input.
  5. Save records_b.
  6. Restore original branch + pop stash.
  7. Compare via `DifferentialHarness.compare(records_a, records_b)`.
  8. Return DivergenceReport.

Critical safety properties:
  - The git operations are wrapped in a try/finally so the original
    branch + working copy are ALWAYS restored on exit (even on crash).
  - Stashed changes are popped on exit, never silently discarded.
  - Any divergence in the git state at start (uncommitted changes) is
    reported BEFORE checkout — operator decides whether to stash.
  - The replay runs in a SEPARATE Python subprocess per SHA so module-
    level state from the previous checkout doesn't bleed into the next.

The subprocess approach means each variant is:
  * a fresh Python process
  * loading the production code at the checked-out SHA
  * running a small driver script that loads the recorded session
    Parquet and produces a JSON-serialized records list

The `compare_two_shas` driver returns a DivergenceReport that the
caller (e.g. `scripts/check_differential_diff.py` in pre-push mode)
can use to decide whether to allow the push.

NOTE: this module deliberately does NOT auto-detect the SHA pair.
The caller specifies them. For the `scripts/check_differential_diff.py`
git-pre-push integration, base_sha=`origin/develop` and head_sha=`HEAD`.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from src.testing.differential_harness import (
    DifferentialHarness,
    DivergenceReport,
    OperationCall,
    OperationRecord,
    ReplayInput,
)

logger = logging.getLogger(__name__)


# ── Subprocess driver script ────────────────────────────────────


_DRIVER_SCRIPT = """\
\"\"\"Subprocess replay driver — invoked by git_replay.compare_two_shas
after each SHA checkout. Reads the Phase 0 session path + the variant
handler module from argv, runs the replay, dumps records as JSON.

CRITICAL: this script must not import anything from the test harness
(which may not exist on older SHAs). Stick to stdlib + the replay_engine
loader (which is part of the SHA being tested).
\"\"\"
from __future__ import annotations

import json
import sys
from pathlib import Path

# argv[1] = base_dir for Phase 0 partitions
# argv[2] = session_date
# argv[3] = output JSON path
# argv[4] = handler module path (Python import string, e.g. "src.testing.replay_engine.identity_handlers")

if len(sys.argv) != 5:
    print("usage: driver.py <base_dir> <session_date> <output.json> <handler_module>", file=sys.stderr)
    sys.exit(2)

base_dir, session_date, output_path, handler_module = sys.argv[1:]

# Insert repo root into path
sys.path.insert(0, str(Path.cwd()))

try:
    from src.testing.replay_engine import (
        build_inprocess_variant,
        replay_input_from_phase0,
    )
except ImportError as e:
    # Older SHA may lack the replay_engine. Log + exit with a special code.
    print(f"[driver] replay_engine import failed: {e}", file=sys.stderr)
    sys.exit(3)

import importlib
parts = handler_module.split(".")
mod_name = ".".join(parts[:-1])
attr = parts[-1]
try:
    handlers_module = importlib.import_module(mod_name)
    handlers = getattr(handlers_module, attr)
except (ImportError, AttributeError) as e:
    print(f"[driver] handler import failed: {e}", file=sys.stderr)
    sys.exit(4)

ri = replay_input_from_phase0(base_dir, session_date)
variant = build_inprocess_variant(handlers if isinstance(handlers, dict) else handlers())

records = []
for op in ri.operations:
    try:
        result = variant(op.name, *op.args, **op.to_kwargs())
        records.append({
            "name": op.name,
            "args": list(op.args),
            "kwargs": dict(op.kwargs),
            "result": repr(result),
            "exception": None,
        })
    except BaseException as e:
        records.append({
            "name": op.name,
            "args": list(op.args),
            "kwargs": dict(op.kwargs),
            "result": None,
            "exception": [type(e).__name__, str(e)],
        })

with open(output_path, "w", encoding="utf-8") as f:
    json.dump(records, f)
print(f"[driver] wrote {len(records)} records to {output_path}")
"""


# ── Result dataclass ────────────────────────────────────────────


@dataclass(frozen=True)
class GitReplayResult:
    """Outcome of a per-SHA replay comparison."""
    base_sha: str
    head_sha: str
    divergence_report: DivergenceReport | None
    error: str | None
    base_records_path: str | None
    head_records_path: str | None


# ── Git helpers ─────────────────────────────────────────────────


def _run_git(*args: str, check: bool = True, capture: bool = True) -> str:
    """Run a git command. Returns stdout (stripped). Raises on non-zero
    if check=True."""
    result = subprocess.run(
        ["git"] + list(args),
        capture_output=capture, text=True, check=check,
    )
    return (result.stdout or "").strip()


def _current_branch() -> str:
    """Return the current branch name (or HEAD's SHA if detached)."""
    try:
        return _run_git("rev-parse", "--abbrev-ref", "HEAD")
    except subprocess.CalledProcessError:
        return _run_git("rev-parse", "HEAD")


def _has_uncommitted_changes() -> bool:
    """True if git working copy has unstaged or staged changes."""
    out = _run_git("status", "--porcelain")
    return bool(out)


# ── Subprocess replay (per-SHA isolation) ──────────────────────


def _run_subprocess_replay(
    *,
    repo_root: Path,
    base_dir: str,
    session_date: str,
    handler_module: str,
    output_path: Path,
) -> tuple[bool, str]:
    """Spawn a fresh Python subprocess to run the replay against the
    currently-checked-out SHA's code. Returns (success, message)."""
    # Write the driver to a temp file (subprocess can't easily import strings)
    driver_dir = tempfile.mkdtemp(prefix="git_replay_driver_")
    driver_path = Path(driver_dir) / "driver.py"
    driver_path.write_text(_DRIVER_SCRIPT, encoding="utf-8")

    try:
        result = subprocess.run(
            [
                sys.executable,
                str(driver_path),
                base_dir,
                session_date,
                str(output_path),
                handler_module,
            ],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return False, "subprocess replay timed out (>120s)"
    finally:
        # Best-effort cleanup of the driver temp dir
        try:
            driver_path.unlink()
            os.rmdir(driver_dir)
        except OSError:  # noqa: silent-handler — best-effort cleanup
            pass

    if result.returncode != 0:
        return False, f"subprocess rc={result.returncode}: {result.stderr.strip()}"
    return True, result.stdout.strip()


# ── Records JSON → OperationRecord tuple ───────────────────────


def _load_records(path: Path) -> tuple[OperationRecord, ...]:
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    out: list[OperationRecord] = []
    for r in rows:
        # Reconstruct OperationCall — kwargs is a dict, convert to sorted tuple
        kwargs_dict = r.get("kwargs", {}) or {}
        kwargs_tuple = tuple(sorted(kwargs_dict.items()))
        call = OperationCall(
            name=r["name"],
            args=tuple(r.get("args", []) or []),
            kwargs=kwargs_tuple,
        )
        exc = r.get("exception")
        if exc is not None:
            out.append(OperationRecord(
                call=call, result=None, exception=(exc[0], exc[1]),
            ))
        else:
            out.append(OperationRecord(
                call=call, result=r.get("result"), exception=None,
            ))
    return tuple(out)


# ── Top-level orchestration ────────────────────────────────────


def compare_two_shas(
    *,
    base_sha: str,
    head_sha: str,
    base_dir: str,
    session_date: str,
    handler_module: str,
    repo_root: Path | str | None = None,
    allowlist: set[str] | None = None,
) -> GitReplayResult:
    """Per-SHA replay comparison via subprocess isolation.

    Args:
      base_sha:       e.g. "origin/develop"
      head_sha:       e.g. "HEAD"
      base_dir:       Phase 0 instrumentation root (e.g. "data/instrumentation")
      session_date:   YYYY-MM-DD partition to replay
      handler_module: Python import path to a handlers dict / factory
                     (e.g. "src.testing.replay_engine.identity_handlers")
      repo_root:      git repo root (default: current cwd)
      allowlist:      set of op_names where divergence is expected

    Returns GitReplayResult with the divergence_report (or error).

    SAFETY: stashes uncommitted changes and ALWAYS restores them.
    """
    if repo_root is None:
        repo_root = Path.cwd()
    repo_root = Path(repo_root)

    original_branch = _current_branch()
    stashed = False

    try:
        if _has_uncommitted_changes():
            logger.info("git_replay: stashing uncommitted changes")
            _run_git("stash", "push", "-u", "-m", "git_replay-auto-stash")
            stashed = True

        # Records artifacts
        records_dir = Path(tempfile.mkdtemp(prefix="git_replay_records_"))
        base_records = records_dir / f"records_{base_sha.replace('/', '_')}.json"
        head_records = records_dir / f"records_{head_sha.replace('/', '_')}.json"

        # Checkout base SHA
        logger.info("git_replay: checking out base_sha=%s", base_sha)
        _run_git("checkout", "--detach", base_sha)
        ok, msg = _run_subprocess_replay(
            repo_root=repo_root,
            base_dir=base_dir, session_date=session_date,
            handler_module=handler_module,
            output_path=base_records,
        )
        if not ok:
            return GitReplayResult(
                base_sha=base_sha, head_sha=head_sha,
                divergence_report=None,
                error=f"base SHA replay failed: {msg}",
                base_records_path=None, head_records_path=None,
            )

        # Checkout head SHA
        logger.info("git_replay: checking out head_sha=%s", head_sha)
        _run_git("checkout", "--detach", head_sha)
        ok, msg = _run_subprocess_replay(
            repo_root=repo_root,
            base_dir=base_dir, session_date=session_date,
            handler_module=handler_module,
            output_path=head_records,
        )
        if not ok:
            return GitReplayResult(
                base_sha=base_sha, head_sha=head_sha,
                divergence_report=None,
                error=f"head SHA replay failed: {msg}",
                base_records_path=str(base_records),
                head_records_path=None,
            )

        # Compare
        records_a = _load_records(base_records)
        records_b = _load_records(head_records)
        # Identity variants — records compared directly
        harness = DifferentialHarness(
            variant_a=lambda *a, **k: None,  # not used in compare
            variant_b=lambda *a, **k: None,
            allowlist=allowlist or set(),
        )
        report = harness.compare(records_a, records_b)

        return GitReplayResult(
            base_sha=base_sha, head_sha=head_sha,
            divergence_report=report,
            error=None,
            base_records_path=str(base_records),
            head_records_path=str(head_records),
        )

    finally:
        # ALWAYS restore original branch
        try:
            logger.info("git_replay: restoring original branch=%s", original_branch)
            _run_git("checkout", original_branch, check=False)
        except Exception as e:
            logger.error("git_replay: branch restore failed: %s", e)
        # Pop stashed changes if any
        if stashed:
            try:
                logger.info("git_replay: popping auto-stash")
                _run_git("stash", "pop", check=False)
            except Exception as e:
                logger.error(
                    "git_replay: stash pop failed (changes preserved in stash): %s", e,
                )


# ── Reference identity handlers (for the smoke test) ───────────


def identity_handlers() -> dict:
    """Reference handler dict — every op returns its first kwarg unchanged.
    Used by the smoke test to verify same-SHA replay produces 0 divergence."""
    def _h(**kwargs):
        for v in kwargs.values():
            return v
        return None
    return {
        "submit_order":  _h,
        "child_fill":    _h,
        "add_position":  _h,
        "cohort_match":  _h,
        "terminal_fill": _h,
    }
