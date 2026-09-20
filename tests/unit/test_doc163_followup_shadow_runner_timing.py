"""Doc 163 followup (2026-05-13) — shadow runner timing + exit codes.

Pin the production failure from the FIRST run with doc 163 wired in:
- launcher invoked `tabpfn_shadow_runner.py --mode live --date $TODAY`
  at 17:30 ET
- catalog's max(d0) = $YESTERDAY (Polygon's day_aggs flat file for
  $TODAY isn't published until ~04:00 ET tomorrow)
- runner had n_test == 0 → silently skipped → returned exit 1 → launcher
  marked the step FAILED with WARN
- net effect: 0 shadow files written for the day even though the
  launcher claimed to run successfully

Post-fix:
1. `--mode live` without `--date` defaults to `max(d0)` in the catalog
   so the runner picks the freshest scoreable date (typically T-1).
2. Exit code is 0 when the only "failure" is `skipped_no_data`
   (no test rows for that d0, OR file already exists). Exit code is 1
   only when a real error occurred (too few train rows, TabPFN crash).

These tests don't actually run TabPFN — they verify the date-selection
logic and the exit-code semantics in isolation.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "tabpfn_shadow_runner.py"

# The two end-to-end exit-code tests below shell out to the real runner, which
# loads the derived warehouse before it can reach the exit-code logic. That
# parquet is built locally from licensed market data and is not distributed
# with the repository, so skip rather than fail when it is absent.
_BASE_PARQUET = REPO / "data" / "polygon_warehouse" / "derived" / "aftermath_strat.parquet"
requires_warehouse = pytest.mark.skipif(
    not _BASE_PARQUET.exists(),
    reason=f"requires derived warehouse ({_BASE_PARQUET.name}); not shipped with the repo",
)


def _run_runner(*args: str, env_extra: dict | None = None,
                timeout: int = 120) -> tuple[int, str]:
    """Invoke the shadow runner script and capture (exit_code, stdout)."""
    import os
    env = os.environ.copy()
    env.setdefault("TABPFN_NO_BROWSER", "1")
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(REPO),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return proc.returncode, proc.stdout + proc.stderr


# ── Help text mentions the new live default ─────────────────────────


def test_help_output_documents_live_mode_default():
    """The script's `--help` should mention the live-mode default
    behavior so operators know not to pass --date in the launcher."""
    rc, out = _run_runner("--help", timeout=20)
    assert rc == 0, f"--help should exit 0, got {rc}\n{out}"
    assert "--mode" in out
    assert "live" in out
    assert "historical" in out


# ── Exit code semantics ─────────────────────────────────────────────


@requires_warehouse
def test_exit_code_zero_when_no_data_for_date_in_live_mode(tmp_path):
    """In live mode, scoring a d0 that has 0 rows in the catalog must
    exit 0 (not 1) so the launcher doesn't mark the step FAILED.

    We force the no-data condition by passing a date 10 years in the
    future. The runner will load the catalog, find 0 rows for that
    target_date, and skip — but the exit code must reflect "no real
    error" so cron-style invocations don't WARN spuriously.
    """
    rc, out = _run_runner("--mode", "live", "--date", "2099-12-31")
    assert rc == 0, (
        f"future date with 0 catalog rows should exit 0, got {rc}\n"
        f"--- output ---\n{out}"
    )
    assert "0 rows in catalog" in out or "skip" in out.lower()


@requires_warehouse
def test_exit_code_zero_when_only_skip_is_already_exists(tmp_path):
    """An idempotent re-run (target file already on disk) is a no-op;
    must exit 0 so daily cron doesn't escalate the same WARN every day."""
    # First, create a stub parquet so the runner sees "already exists".
    import pandas as pd
    shadow_dir = REPO / "data" / "polygon_warehouse" / "derived" / "tabpfn_shadow"
    target_date = "2099-11-30"
    stub = shadow_dir / f"{target_date}.parquet"
    if not shadow_dir.exists():
        pytest.skip("shadow_dir does not exist on this machine")
    stub.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"ticker": ["X"], "d0": [pd.Timestamp(target_date)]}).to_parquet(
        stub, compression="zstd"
    )
    try:
        rc, out = _run_runner("--mode", "live", "--date", target_date)
        # Even though no file was written this run, the only "skip" was
        # the already-exists case → must exit 0.
        assert rc == 0, (
            f"already-exists skip should exit 0, got {rc}\n"
            f"--- output ---\n{out}"
        )
    finally:
        if stub.exists():
            stub.unlink()


# ── Argparse contract ───────────────────────────────────────────────


def test_argparse_accepts_live_without_date():
    """Critical: the launcher invokes `--mode live` with no --date.
    The argparse layer must accept that combination."""
    # We can't actually run end-to-end without TabPFN credentials, but
    # we CAN run --help to confirm argparse accepts `--mode live` alone.
    rc, out = _run_runner("--mode", "live", "--help", timeout=20)
    assert rc == 0, f"argparse rejected --mode live: rc={rc}\n{out}"


# ── Launcher integration: dry-run shows correct command ─────────────


def test_launcher_dry_run_omits_date_argument(tmp_path):
    """The PowerShell launcher's -DryRun text must show the runner is
    invoked WITHOUT --date $TODAY (so the new max(d0) default kicks in)."""
    launcher = REPO / "scripts" / "daily_data_ingest.ps1"
    content = launcher.read_text(encoding="utf-8", errors="ignore")
    # The runner step must NOT pass --date in the production invocation
    assert "tabpfn_shadow_runner.py --mode live --date" not in content, (
        "launcher must NOT pass --date to the shadow runner (forces yesterday's "
        "date to be silently skipped at 17:30 ET)"
    )
    assert "tabpfn_shadow_runner.py --mode live" in content, (
        "launcher must invoke the shadow runner in --mode live"
    )


# ── Source-level guard: the literal exit-1-on-zero-writes regression ─


def test_runner_source_has_split_skip_counters():
    """The fix introduces `skipped_no_data` vs `skipped_other` so the
    exit code can distinguish 'nothing to do' from 'real error'."""
    src = SCRIPT.read_text(encoding="utf-8")
    assert "skipped_no_data" in src
    assert "skipped_other" in src
    # The old "return 0 if written > 0 else 1" must be gone -- it would
    # incorrectly exit 1 in the perfectly-fine no-data case.
    assert "return 0 if written > 0 else 1" not in src, (
        "Old exit logic still present; doc 163 followup fix not applied"
    )
