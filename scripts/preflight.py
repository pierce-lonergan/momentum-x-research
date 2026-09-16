"""Monday-morning preflight health check.

Per `docs/research-log/42_monday_operator_runbook.md` §1: one-command
verification that the system is operationally green BEFORE the 04:30 ET
launcher fires.

Exit codes:
  0 = all green
  1 = a HARD check failed (do NOT start the session)
  2 = a SOFT check warned (review then proceed)

Usage:
    python scripts/preflight.py
    python scripts/preflight.py --json   # machine-readable output
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class CheckResult:
    name: str
    severity: str   # "HARD" / "SOFT"
    passed: bool
    detail: str


# ── Individual checks ──────────────────────────────────────────


def check_test_suite_green() -> CheckResult:
    """Run the fast test suite and report pass/fail."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-m", "not slow", "-q",
             "--no-header", "tests/static_analysis/", "tests/unit/test_kelly_governor.py",
             "tests/unit/test_phase0_instrumentation.py", "tests/unit/test_bocpd.py",
             "tests/unit/test_eod_report.py", "tests/unit/test_phase0_migration.py",
             "tests/unit/test_qmp_signing.py", "tests/unit/test_differential_harness.py",
             "tests/property/test_invariant_injection.py",
             "tests/property/test_simple_broker.py"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=180,
        )
        if result.returncode == 0:
            # Extract pass count from output
            return CheckResult(
                name="test_suite_green",
                severity="HARD", passed=True,
                detail=f"fast suite green: {result.stdout.strip().split(chr(10))[-1]}",
            )
        return CheckResult(
            name="test_suite_green",
            severity="HARD", passed=False,
            detail=f"FAILED — investigate before starting session: {result.stdout.strip().split(chr(10))[-1]}",
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            name="test_suite_green",
            severity="HARD", passed=False,
            detail="timeout (>180s) — suite hung",
        )


def check_d_code_orphans() -> CheckResult:
    """Run the D-code audit; expect 0 orphans."""
    try:
        result = subprocess.run(
            [sys.executable, "scripts/audit_d_codes.py", "--strict"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0:
            return CheckResult(
                name="d_code_orphans",
                severity="HARD", passed=True,
                detail="0 orphans — registry clean",
            )
        return CheckResult(
            name="d_code_orphans",
            severity="HARD", passed=False,
            detail=f"orphan codes detected: {result.stderr.strip().split(chr(10))[-1]}",
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            name="d_code_orphans",
            severity="HARD", passed=False,
            detail="audit timeout (>60s)",
        )


def check_bocpd_prior_present() -> CheckResult:
    """Verify the persisted BOCPD prior exists + has expected schema."""
    prior_path = REPO_ROOT / "data" / "priors" / "s1_bocpd_prior.parquet"
    if not prior_path.exists():
        return CheckResult(
            name="bocpd_prior_present",
            severity="SOFT", passed=False,
            detail=(
                f"prior missing at {prior_path.relative_to(REPO_ROOT)} — "
                "session will use safe default. Run `python scripts/pretrain_bocpd_prior.py`."
            ),
        )
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from src.analysis.bocpd import BOCPDPrior
        prior = BOCPDPrior.from_parquet(prior_path)
        return CheckResult(
            name="bocpd_prior_present",
            severity="SOFT", passed=True,
            detail=(
                f"prior loaded: mu={prior.mu_edge:.4f} sigma={prior.sigma_edge:.4f} "
                f"hazard={prior.hazard_rate:.5f} n_trades={prior.n_trades}"
            ),
        )
    except Exception as e:
        return CheckResult(
            name="bocpd_prior_present",
            severity="SOFT", passed=False,
            detail=f"prior load failed: {e}",
        )


def check_phase0_dir_writable() -> CheckResult:
    """Verify data/instrumentation/ exists + writable; warn on stale tmp files."""
    base = REPO_ROOT / "data" / "instrumentation"
    if not base.exists():
        try:
            base.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return CheckResult(
                name="phase0_dir_writable",
                severity="HARD", passed=False,
                detail=f"cannot create {base}: {e}",
            )
    # Check writable
    test_file = base / ".preflight_writability_check"
    try:
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
    except Exception as e:
        return CheckResult(
            name="phase0_dir_writable",
            severity="HARD", passed=False,
            detail=f"directory not writable: {e}",
        )
    # Check stale tmp files
    stale = list(base.rglob("*.tmp"))
    if stale:
        return CheckResult(
            name="phase0_dir_writable",
            severity="SOFT", passed=False,
            detail=(
                f"writable but {len(stale)} stale .tmp file(s) — clean up "
                f"(prior crash residue): {[str(p.relative_to(REPO_ROOT)) for p in stale[:3]]}"
            ),
        )
    return CheckResult(
        name="phase0_dir_writable",
        severity="HARD", passed=True,
        detail=f"writable, no stale .tmp files",
    )


def check_git_clean() -> CheckResult:
    """Warn if uncommitted changes exist (D239 will fire downstream)."""
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return CheckResult(
                name="git_clean",
                severity="SOFT", passed=False,
                detail=f"git status failed: {result.stderr.strip()}",
            )
        changes = [line for line in result.stdout.splitlines() if line.strip()]
        if changes:
            return CheckResult(
                name="git_clean",
                severity="SOFT", passed=False,
                detail=(
                    f"{len(changes)} uncommitted change(s) — D239 HEARTBEAT_DIRTY_WORKTREE "
                    f"will fire downstream. Sample: {changes[:3]}"
                ),
            )
        return CheckResult(
            name="git_clean",
            severity="SOFT", passed=True,
            detail="working copy clean",
        )
    except Exception as e:
        return CheckResult(
            name="git_clean",
            severity="SOFT", passed=False,
            detail=f"git check failed: {e}",
        )


def check_disk_space() -> CheckResult:
    """Warn if <1GB free on the data/ partition."""
    try:
        usage = shutil.disk_usage(str(REPO_ROOT / "data"))
        free_gb = usage.free / (1024 ** 3)
        if free_gb < 1.0:
            return CheckResult(
                name="disk_space",
                severity="HARD", passed=False,
                detail=f"only {free_gb:.2f} GB free — Phase 0 captures will fail mid-session",
            )
        if free_gb < 5.0:
            return CheckResult(
                name="disk_space",
                severity="SOFT", passed=False,
                detail=f"only {free_gb:.2f} GB free — clean old captures soon",
            )
        return CheckResult(
            name="disk_space",
            severity="SOFT", passed=True,
            detail=f"{free_gb:.1f} GB free",
        )
    except Exception as e:
        return CheckResult(
            name="disk_space",
            severity="SOFT", passed=False,
            detail=f"disk check failed: {e}",
        )


# ── Driver ──────────────────────────────────────────────────────


CHECKS: list = [
    check_test_suite_green,
    check_d_code_orphans,
    check_bocpd_prior_present,
    check_phase0_dir_writable,
    check_git_clean,
    check_disk_space,
]


def run_all_checks() -> list[CheckResult]:
    return [fn() for fn in CHECKS]


def _format_table(results: list[CheckResult]) -> str:
    # ASCII-only markers for Windows cp1252 console compatibility
    lines = []
    lines.append(f"{'CHECK':<28} {'SEV':<5} {'PASS':<6} DETAIL")
    lines.append("-" * 100)
    for r in results:
        status = "[OK]" if r.passed else "[FAIL]"
        lines.append(
            f"{r.name:<28} {r.severity:<5} {status:<6} {r.detail[:60]}"
        )
    n_hard_fail = sum(1 for r in results if not r.passed and r.severity == "HARD")
    n_soft_fail = sum(1 for r in results if not r.passed and r.severity == "SOFT")
    lines.append("-" * 100)
    if n_hard_fail > 0:
        lines.append(f"!!! {n_hard_fail} HARD check(s) failed -- DO NOT START SESSION")
    elif n_soft_fail > 0:
        lines.append(f"WARN {n_soft_fail} SOFT check(s) warned -- review then proceed")
    else:
        lines.append("ALL GREEN -- session safe to start")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json", action="store_true",
        help="Emit machine-readable JSON instead of the formatted table.",
    )
    args = parser.parse_args()

    print("Running preflight checks...\n")
    results = run_all_checks()

    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        print(_format_table(results))

    n_hard_fail = sum(1 for r in results if not r.passed and r.severity == "HARD")
    n_soft_fail = sum(1 for r in results if not r.passed and r.severity == "SOFT")
    if n_hard_fail > 0:
        return 1
    if n_soft_fail > 0:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
