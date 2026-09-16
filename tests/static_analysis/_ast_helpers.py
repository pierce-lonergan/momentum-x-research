"""Shared AST/regex helpers for tests/static_analysis/.

All test files in this package use this module to:
1. Walk the repo for production source files (`.py` or `.ps1`)
2. Parse whitelist comments (`# noqa: <kind>`)
3. Compare violation counts against `_baselines.json`
4. Emit a uniform failure message with `fix:` hints

Design constraints:
- Standard library only (ast, json, pathlib, re) — no external deps
- "Catch new only" semantics: per-file count baselines
- Forward-compatible: unknown `kind` values are ignored (allows adding new
  bug classes without breaking existing whitelist comments)
"""

from __future__ import annotations

import ast
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator

import pytest

# ── Repo layout ──────────────────────────────────────────────────────────

REPO_ROOT: Path = Path(__file__).resolve().parents[2]
"""Project root — directory containing main.py, src/, scripts/, tests/."""

PY_INCLUDE_TOP: tuple[str, ...] = ("main.py", "src", "scripts", "config")
"""Top-level entries to scan for Python sources."""

PY_EXCLUDE_DIRS: frozenset[str] = frozenset({
    "__pycache__", ".venv", "venv", "tests", "mx-arena",
    "data", "logs", ".claude", "node_modules", ".git",
    "models", "archive", "backups",
})

PS1_INCLUDE_TOP: tuple[str, ...] = ("scripts",)

BASELINE_PATH: Path = Path(__file__).with_name("_baselines.json")

# ── Curated allowlists ──────────────────────────────────────────────────

KNOWN_FROZEN_TYPES: frozenset[str] = frozenset({
    # Pydantic models with frozen=True (src/core/models.py and adjacent)
    "CandidateStock", "AgentSignal", "NewsSignal", "TechnicalSignal",
    "RiskSignal", "ManipulationSignal", "ScoredCandidate", "DebateResult",
    "CatalystProfile", "TradeVerdict", "MoverRecord",
    "ExperimentVariant", "ExperimentConfig", "VariantResult",
    # @dataclass(frozen=True)
    "KellyTier", "ArchetypeExitParams", "AdaptiveRouterConfig",
    "GEXResult", "GEXBucket", "SlippageEstimate", "SlippageModelConfig",
})
"""Class names whose instances are immutable. Add to this set when introducing
a new ``frozen=True`` Pydantic model or ``@dataclass(frozen=True)``."""

DATA_DIR_NAMES: frozenset[str] = frozenset({
    "data", "logs", "config", "mx-arena", "models", "scripts", "cache",
})
"""First path segments that indicate a relative data path (likely cwd-dependent)."""

# ── Fix hints (one line each, shown in failure messages) ────────────────

FIX_HINTS: dict[str, str] = {
    "frozen-mutate": (
        "use `inst.model_copy(update={...})` (Pydantic) or "
        "`dataclasses.replace(inst, ...)` (dataclass)."
    ),
    "async-leak": (
        "wrap in `async with httpx.AsyncClient(...) as c:` or "
        "`await c.aclose()` in a finally block."
    ),
    "silent-handler": (
        "log via `logger.warning('... %s', e)` or call "
        "`_record_silent_failure(e, context=...)`."
    ),
    "relative-path": (
        "use `_PROJECT_ROOT / 'data' / 'foo.json'` "
        "(import or define `_PROJECT_ROOT` from main.py)."
    ),
    "ps-datetime-utc": (
        "use `[DateTimeOffset]::Parse(...)` or pass "
        "`[Globalization.DateTimeStyles]::AssumeUniversal` to `[datetime]::Parse`."
    ),
    "ps-pipe-deadlock": (
        "use `Start-Process -RedirectStandardOutput` or write stderr to a file "
        "with `2>> $LogFile` instead of `2>&1 | ForEach-Object`."
    ),
}

# ── Violation record ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class Violation:
    """A single static-analysis hit. Compared by file path for baseline diffing."""

    path: str          # repo-relative, forward-slash normalized
    line: int
    col: int
    kind: str
    snippet: str       # ≤80 char source extract


# ── File walkers ────────────────────────────────────────────────────────


def _is_excluded(path: Path) -> bool:
    """Return True if any segment of `path` (relative to REPO_ROOT) is excluded."""
    try:
        rel = path.relative_to(REPO_ROOT)
    except ValueError:
        return True
    return any(part in PY_EXCLUDE_DIRS for part in rel.parts)


def iter_python_files() -> Iterator[Path]:
    """Yield production .py files in main.py, src/, scripts/, config/."""
    for top in PY_INCLUDE_TOP:
        root = REPO_ROOT / top
        if not root.exists():
            continue
        if root.is_file():
            if root.suffix == ".py" and not _is_excluded(root):
                yield root
            continue
        for p in root.rglob("*.py"):
            if not _is_excluded(p):
                yield p


def iter_powershell_files() -> Iterator[Path]:
    """Yield production .ps1 files in scripts/."""
    for top in PS1_INCLUDE_TOP:
        root = REPO_ROOT / top
        if not root.exists():
            continue
        for p in root.rglob("*.ps1"):
            if not _is_excluded(p):
                yield p


def rel_path(p: Path) -> str:
    """Return repo-relative, forward-slash normalized path string."""
    try:
        return p.relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return p.as_posix()


# ── Whitelist parsing ────────────────────────────────────────────────────


_NOQA_PY_RE = re.compile(r"#\s*noqa:\s*([a-z0-9-]+(?:\s*,\s*[a-z0-9-]+)*)", re.IGNORECASE)
_NOQA_PS_RE = _NOQA_PY_RE  # PS uses # for line comments too


def parse_whitelist(source: str, kind: str) -> set[int]:
    """Return line numbers (1-indexed) where `# noqa: <kind>` suppresses violations.

    Multiple kinds may share a marker: `# noqa: frozen-mutate, async-leak`.
    """
    suppressed: set[int] = set()
    kind_lower = kind.lower()
    for line_num, line in enumerate(source.splitlines(), start=1):
        m = _NOQA_PY_RE.search(line)
        if not m:
            continue
        kinds = {k.strip().lower() for k in m.group(1).split(",")}
        if kind_lower in kinds:
            suppressed.add(line_num)
    return suppressed


# ── Snippet extraction ──────────────────────────────────────────────────


def get_snippet(source: str, line: int, max_len: int = 80) -> str:
    """Return the source line trimmed to max_len chars (for failure messages)."""
    lines = source.splitlines()
    if 1 <= line <= len(lines):
        text = lines[line - 1].strip()
        return (text[: max_len - 3] + "...") if len(text) > max_len else text
    return ""


# ── AST utilities ───────────────────────────────────────────────────────


def build_parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    """Return a node → parent map (ast doesn't expose this natively)."""
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def enclosing_function(parent_map: dict[ast.AST, ast.AST], node: ast.AST) -> ast.AST | None:
    """Walk parents until reaching a FunctionDef/AsyncFunctionDef/Module."""
    cur = parent_map.get(node)
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            return cur
        cur = parent_map.get(cur)
    return None


# ── Baseline I/O ────────────────────────────────────────────────────────


def load_baselines() -> dict[str, dict[str, int]]:
    """Load _baselines.json. Returns empty dict if file missing.

    Format: {kind: {file_path: count, ...}, ...}
    """
    if not BASELINE_PATH.exists():
        return {}
    with open(BASELINE_PATH, encoding="utf-8") as f:
        data = json.load(f)
    # Strip _meta and any non-dict entries
    return {k: v for k, v in data.items() if not k.startswith("_") and isinstance(v, dict)}


def save_baselines(counts: dict[str, dict[str, int]], extra_meta: dict | None = None) -> None:
    """Write counts to _baselines.json, sorted for deterministic diffs."""
    from datetime import datetime, timezone
    out: dict = {}
    for kind in sorted(counts):
        out[kind] = {f: counts[kind][f] for f in sorted(counts[kind]) if counts[kind][f] > 0}
    out["_meta"] = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generator": "tests/static_analysis/_update_baseline.py",
        "tickets": ["D217", "D218", "D219"],
        **(extra_meta or {}),
    }
    with open(BASELINE_PATH, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, indent=2, sort_keys=False)
        f.write("\n")


# ── Core: collect and assert ────────────────────────────────────────────


VisitorFactory = Callable[[str, str], list[Violation]]
"""(file_path_relative, source_text) -> list of violations in that file."""


def collect_violations(
    visitor_factory: VisitorFactory,
    files: Iterable[Path],
    kind: str,
) -> list[Violation]:
    """Run `visitor_factory` over each file, drop whitelisted lines."""
    out: list[Violation] = []
    for path in files:
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = rel_path(path)
        try:
            file_violations = visitor_factory(rel, source)
        except SyntaxError:
            # Skip files with parse errors — not our problem
            continue
        if not file_violations:
            continue
        whitelist = parse_whitelist(source, kind)
        out.extend(v for v in file_violations if v.line not in whitelist)
    return out


def _format_failure(kind: str, file_diffs: dict[str, tuple[int, int, list[Violation]]]) -> str:
    """Build the multi-line pytest.fail message."""
    total_new = sum(diff[1] - diff[0] for diff in file_diffs.values() if diff[1] > diff[0])
    plural = "s" if total_new != 1 else ""
    lines: list[str] = [
        f'Static analysis: {total_new} new "{kind}" violation{plural} detected.',
        "",
    ]
    for file_path in sorted(file_diffs):
        baseline, current, violations = file_diffs[file_path]
        delta = current - baseline
        if delta <= 0:
            continue
        lines.append(file_path)
        lines.append(f"  baseline: {baseline}    current: {current}    delta: +{delta}")
        # Show up to `delta` new violations (sorted by line)
        for v in sorted(violations, key=lambda x: x.line)[:delta]:
            lines.append(f"  line {v.line}: {v.snippet}")
            lines.append(f"      fix: {FIX_HINTS.get(v.kind, '(no hint)')}")
        lines.append("")
    lines.append("To accept these changes (e.g. you removed `frozen=True` intentionally):")
    lines.append("  python tests/static_analysis/_update_baseline.py")
    lines.append("")
    lines.append("To suppress a single legitimate exception, add to the offending line:")
    lines.append(f"  ...some code...  # noqa: {kind}")
    return "\n".join(lines)


def assert_no_new_violations(kind: str, violations: list[Violation]) -> None:
    """Compare violation counts per file against the baseline.

    Fails (via pytest.fail) iff any file's current count exceeds its baseline.
    Existing violations within the baseline pass silently — this is the
    "catch new only" semantics from the approved plan.
    """
    baselines = load_baselines().get(kind, {})

    # Group current violations by file
    current_by_file: dict[str, list[Violation]] = defaultdict(list)
    for v in violations:
        current_by_file[v.path].append(v)

    # Compute deltas
    file_diffs: dict[str, tuple[int, int, list[Violation]]] = {}
    all_files = set(baselines) | set(current_by_file)
    for file_path in all_files:
        baseline = baselines.get(file_path, 0)
        current = len(current_by_file.get(file_path, []))
        if current > baseline:
            file_diffs[file_path] = (baseline, current, current_by_file.get(file_path, []))

    if file_diffs:
        pytest.fail(_format_failure(kind, file_diffs), pytrace=False)
