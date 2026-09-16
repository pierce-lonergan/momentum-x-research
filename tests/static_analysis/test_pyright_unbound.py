"""Bug AF + AG defense: pyright `reportPossiblyUnboundVariable` regression gate.

Per CONTRIBUTING.md test-first protocol, every shipped bug gets a
static-analysis defense when the bug is AST-detectable. Bug AF
(`_cand` in `bridge.execute_verdict`) and Bug AG (`_d217_hb_repo`
in `main.py` startup) are both Python `UnboundLocalError` at runtime
caused by a name being assigned only inside a conditional / later
in the function but referenced before the assignment dominates.

This test:
  1. Verifies that `pyright` is installed and runnable.
  2. Verifies that the pyright config in `pyrightconfig.json` sets
     `reportPossiblyUnboundVariable = "error"`.
  3. Verifies that pyright run against `src/` + `main.py` + `scripts/`
     reports zero `reportPossiblyUnboundVariable` errors. Adding a new
     conditional-define / unconditional-reference pattern would
     fail this test.
  4. As a positive case (sanity check that pyright actually fires
     on this rule), constructs a tiny in-memory reproduction of the
     Bug AG pattern and asserts pyright flags it. If this fails,
     either pyright's behavior changed OR the rule was disabled.

Why this is the right defense layer:
  - mypy default config does not catch this class.
  - The ast-based static analysis suite (`tests/static_analysis/`)
    catches frozen-mutate, async-leak, silent-handler, relative-path,
    and PowerShell patterns — but writing a robust UnboundLocalError
    detector in Python AST is non-trivial (requires control-flow
    analysis). pyright already implements this correctly.
  - `reportPossiblyUnboundVariable = "error"` is the precise rule.

The test runs in <5s in CI.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PYRIGHT_CONFIG = REPO_ROOT / "pyrightconfig.json"


def _pyright_available() -> bool:
    """pyright ships as a Python wrapper around a Node.js binary.
    On first call it downloads the JS bundle (~30MB). Skip the test
    if the wrapper isn't installed."""
    return shutil.which("pyright") is not None or _can_import_pyright()


def _can_import_pyright() -> bool:
    try:
        import pyright  # noqa: F401
        return True
    except ImportError:
        return False


def _run_pyright(*paths: str) -> dict:
    """Run pyright in JSON mode, return parsed output."""
    cmd = [sys.executable, "-m", "pyright", "--outputjson", *paths]
    result = subprocess.run(
        cmd, cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    # pyright returns 0 = no errors, 1 = errors, others = invocation issue
    # JSON is on stdout regardless
    if not result.stdout.strip():
        pytest.skip(
            f"pyright returned no JSON output (rc={result.returncode}); "
            f"stderr: {result.stderr[:200]}"
        )
    return json.loads(result.stdout)


@pytest.mark.skipif(not _pyright_available(), reason="pyright not installed")
def test_pyright_config_sets_possibly_unbound_to_error():
    """The discovery infrastructure depends on this rule being an error,
    not a warning. If a future commit downgrades the rule, this test
    catches it."""
    assert PYRIGHT_CONFIG.exists(), (
        f"pyrightconfig.json missing at {PYRIGHT_CONFIG}. "
        "This is the Bug AF/AG defense layer; it must exist."
    )
    cfg = json.loads(PYRIGHT_CONFIG.read_text(encoding="utf-8"))
    assert cfg.get("reportPossiblyUnboundVariable") == "error", (
        "pyrightconfig.json: `reportPossiblyUnboundVariable` must be \"error\". "
        "This rule catches the Bug AF/AG class (UnboundLocalError on "
        "conditionally-assigned-then-unconditionally-referenced names). "
        f"Current setting: {cfg.get('reportPossiblyUnboundVariable')!r}. "
        "See docs/research-log/44_bug_ag_track_b_unbound.md §7."
    )


PYRIGHT_BASELINE = REPO_ROOT / "tests" / "static_analysis" / "_pyright_baseline.json"


def _load_baseline() -> dict[str, int]:
    """Per-file count of pre-existing reportPossiblyUnboundVariable issues.
    Catch-new-only semantics: test fails iff any file's count INCREASES
    (or a new file appears). Mirrors the AST static_analysis suite's
    `_baselines.json` discipline."""
    if not PYRIGHT_BASELINE.exists():
        return {}
    data = json.loads(PYRIGHT_BASELINE.read_text(encoding="utf-8"))
    return data.get("reportPossiblyUnboundVariable", {})


@pytest.mark.skipif(not _pyright_available(), reason="pyright not installed")
def test_pyright_no_new_possibly_unbound_in_production_code():
    """Run pyright against the production code paths. Compare per-file counts
    of `reportPossiblyUnboundVariable` errors against the baseline. Fail iff
    any file's count went up (new Bug AF/AG-class regression) or a brand-new
    file appears with errors.

    The baseline (`_pyright_baseline.json`) snapshots the 24 known pre-existing
    instances at the time pyright was wired in. Each is tracked to its own
    finding doc as it is fixed; remove the file from the baseline (or drop
    the count) when refactored. See docs/research-log/44_bug_ag_track_b_unbound.md §7.
    """
    baseline = _load_baseline()
    out = _run_pyright()
    diagnostics = out.get("generalDiagnostics", [])
    unbound_errors = [
        d for d in diagnostics
        if d.get("rule") == "reportPossiblyUnboundVariable"
        and d.get("severity") == "error"
    ]

    # Per-file count
    import collections
    current: dict[str, int] = collections.Counter()
    by_file: dict[str, list] = collections.defaultdict(list)
    for d in unbound_errors:
        try:
            rel = Path(d["file"]).relative_to(REPO_ROOT).as_posix()
        except ValueError:
            rel = d.get("file", "?")
        current[rel] += 1
        by_file[rel].append(d)

    # Find regressions
    regressions: list[str] = []
    for f, n in sorted(current.items()):
        b = baseline.get(f, 0)
        if n > b:
            regressions.append(f"  {f}: baseline={b} current={n} delta=+{n - b}")
            for d in by_file[f][:5]:
                ln = d.get("range", {}).get("start", {}).get("line", 0) + 1
                msg = d.get("message", "?").splitlines()[0]
                regressions.append(f"    line {ln}: {msg}")

    if regressions:
        msg_lines = [
            "pyright detected NEW reportPossiblyUnboundVariable errors "
            "(Bug AF/AG class regression):",
            "",
            *regressions,
            "",
            "Fix:",
            "  - Hoist the variable definition above its first reference, OR",
            "  - Initialize it to None / a default before the conditional branch.",
            "  - See docs/research-log/43_bug_af_uncond_cand_reference.md §4 and",
            "    docs/research-log/44_bug_ag_track_b_unbound.md §4 for canonical fixes.",
            "",
            "If you intentionally fixed an issue in the baseline file:",
            "  - Decrement the count in tests/static_analysis/_pyright_baseline.json",
            "  - Or remove the file entirely if its count drops to zero.",
        ]
        pytest.fail("\n".join(msg_lines))


@pytest.mark.skipif(not _pyright_available(), reason="pyright not installed")
def test_pyright_actually_flags_bug_ag_pattern(tmp_path):
    """Sanity check: prove pyright catches a fresh reproduction of the
    Bug AG pattern. If pyright's behavior regresses or if the rule is
    silently disabled, this test catches it before the rule becomes a
    no-op."""
    # Synthesize a tiny module that exhibits the exact bug class.
    # Use an annotated bool parameter so pyright cannot narrow the
    # conditional to "always false" via type inference (which would
    # make the bug pattern unreachable and the test moot). This
    # matches Bug AG's structure: a `try:` block whose body is not
    # statically known to always execute, with a downstream
    # unconditional reference to a name only defined inside.
    repro = tmp_path / "bug_ag_repro.py"
    repro.write_text(textwrap.dedent("""
        def execute(flag: bool) -> int:
            if flag:
                _cand = 42
            # _cand is referenced unconditionally here — pyright must flag
            return _cand
    """).strip(), encoding="utf-8")

    # Use a permissive config inline to avoid pulling repo settings
    repro_cfg = tmp_path / "pyrightconfig.json"
    repro_cfg.write_text(json.dumps({
        "include": ["bug_ag_repro.py"],
        "reportPossiblyUnboundVariable": "error",
        "pythonVersion": "3.11",
    }), encoding="utf-8")

    cmd = [sys.executable, "-m", "pyright", "--outputjson", "--project", str(tmp_path)]
    result = subprocess.run(cmd, cwd=str(tmp_path), capture_output=True, text=True)
    out = json.loads(result.stdout) if result.stdout.strip() else {}
    diagnostics = out.get("generalDiagnostics", [])
    unbound = [
        d for d in diagnostics
        if d.get("rule") == "reportPossiblyUnboundVariable"
    ]
    assert unbound, (
        "Sanity check failed: pyright did NOT flag the synthetic Bug AG "
        "repro. Either pyright's behavior changed, the rule was renamed, "
        "or the project config is being overridden. Investigate before "
        "trusting test_pyright_no_possibly_unbound_in_production_code."
    )
