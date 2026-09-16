"""Detect cwd-dependent relative paths into data dirs.

The bug pattern (D217 heartbeat scope leak):
    _hb_path = os.path.join("data", "heartbeat.json")  # breaks if cwd != repo root
The system was started with Task Scheduler from a stale worktree directory;
the relative path resolved to the wrong location and the heartbeat file silently
went missing. The watchdog then killed the process for "no heartbeat" — but the
process was actually healthy.

Acceptable patterns:
    _PROJECT_ROOT / "data" / "foo.json"          # absolute via __file__
    Path(__file__).parent / "data" / "foo.json"  # absolute via __file__
    REPO_ROOT / "data" / "foo.json"              # absolute via well-known anchor

Flagged patterns:
    Path("data/foo.json")
    Path("logs/bar.log")
    os.path.join("data", "bar.json")             # also flagged
    open("data/foo.json", "w")                   # via FunctionCall detection
"""

from __future__ import annotations

import ast
import os
import re

from tests.static_analysis._ast_helpers import (
    DATA_DIR_NAMES,
    Violation,
    assert_no_new_violations,
    collect_violations,
    get_snippet,
    iter_python_files,
)

KIND = "relative-path"


def _first_segment(literal: str) -> str | None:
    """Return the first path segment of `literal`, or None if it's absolute / empty."""
    if not literal or os.path.isabs(literal):
        return None
    # Normalize separators for cross-platform consistency
    parts = re.split(r"[\\/]", literal.strip(), maxsplit=1)
    if not parts or not parts[0]:
        return None
    return parts[0]


def _is_dangerous_literal(literal: str) -> bool:
    """Return True if `literal` is a relative path into a data-bearing dir."""
    seg = _first_segment(literal)
    return seg is not None and seg in DATA_DIR_NAMES


def _is_path_call(call: ast.Call) -> bool:
    """`Path(...)` or `pathlib.Path(...)`."""
    func = call.func
    if isinstance(func, ast.Name) and func.id == "Path":
        return True
    if isinstance(func, ast.Attribute) and func.attr == "Path":
        return True
    return False


def _is_os_path_join(call: ast.Call) -> bool:
    """`os.path.join(...)` — flag when first arg is a dangerous literal."""
    func = call.func
    if not (isinstance(func, ast.Attribute) and func.attr == "join"):
        return False
    val = func.value
    return isinstance(val, ast.Attribute) and val.attr == "path" and (
        isinstance(val.value, ast.Name) and val.value.id == "os"
    )


# ── Public scan API ─────────────────────────────────────────────────────


def visit_file(file_path: str, source: str) -> list[Violation]:
    tree = ast.parse(source, filename=file_path)
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            continue
        literal = first.value
        if not _is_dangerous_literal(literal):
            continue
        if _is_path_call(node) or _is_os_path_join(node):
            violations.append(
                Violation(
                    path=file_path,
                    line=node.lineno,
                    col=node.col_offset,
                    kind=KIND,
                    snippet=get_snippet(source, node.lineno),
                )
            )
    return violations


def scan(kind: str = KIND) -> list[Violation]:
    return collect_violations(visit_file, iter_python_files(), kind)


# ── Pytest test ──────────────────────────────────────────────────────────


def test_no_new_relative_paths() -> None:
    """Fail if any production file has more cwd-dependent relative paths than baseline."""
    assert_no_new_violations(KIND, scan(KIND))
