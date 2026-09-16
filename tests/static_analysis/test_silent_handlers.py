"""Detect `except <Anything>:` clauses whose body silently swallows the exception.

The bug pattern (D218 sweep): `except Exception: pass` or `except: continue`
makes failures invisible. The system runs but never recovers correctly because
nothing ever logs the failure.

What counts as silent (after stripping leading docstring strings):
- Body is exactly one of: `pass`, `continue`, `Ellipsis`
- Body is exactly one `logger.debug(...)` / `log.debug(...)` call where the
  exception variable does NOT appear in the args (true info-loss)

What is acceptable (D218's canonical fix):
- Multi-statement body
- `raise` (re-raise)
- `logger.warning('...%s', e)` / `logger.error(...)` / `logger.exception(...)`
- Any call that captures the exception variable in its args
- A bare `pass` followed by a tracking call (e.g. `_record_silent_failure(...)`)
"""

from __future__ import annotations

import ast

from tests.static_analysis._ast_helpers import (
    Violation,
    assert_no_new_violations,
    collect_violations,
    get_snippet,
    iter_python_files,
)

KIND = "silent-handler"

_SILENT_LOGGER_LEVELS: frozenset[str] = frozenset({"debug"})
"""Logger calls at these levels count as 'whisper' (info-loss) handlers."""


def _is_docstring_or_constant_string(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _strip_leading_strings(body: list[ast.stmt]) -> list[ast.stmt]:
    """Drop Expr(Constant(str)) statements (docstring-style comments)."""
    return [s for s in body if not _is_docstring_or_constant_string(s)]


def _is_pass_continue_ellipsis(stmt: ast.stmt) -> bool:
    if isinstance(stmt, (ast.Pass, ast.Continue)):
        return True
    if (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and stmt.value.value is Ellipsis
    ):
        return True
    return False


def _is_silent_logger_call(stmt: ast.stmt, exc_name: str | None) -> bool:
    """`logger.debug(...)` where `exc_name` does NOT appear in the args."""
    if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
        return False
    call = stmt.value
    if not isinstance(call.func, ast.Attribute):
        return False
    if call.func.attr not in _SILENT_LOGGER_LEVELS:
        return False
    # Must be on a logger-like name (heuristic: ends with 'log' or 'logger')
    if not isinstance(call.func.value, ast.Name):
        return False
    name = call.func.value.id.lower()
    if not (name == "logger" or name == "log" or name.endswith("_logger") or name.endswith("_log")):
        return False
    # If the exception variable appears anywhere in args/keywords, it's NOT silent
    if exc_name:
        for arg in call.args:
            if any(isinstance(n, ast.Name) and n.id == exc_name for n in ast.walk(arg)):
                return False
        for kw in call.keywords:
            if kw.value and any(
                isinstance(n, ast.Name) and n.id == exc_name for n in ast.walk(kw.value)
            ):
                return False
    return True


def _handler_is_silent(handler: ast.ExceptHandler) -> bool:
    body = _strip_leading_strings(handler.body)
    if len(body) != 1:
        return False
    stmt = body[0]
    exc_name = handler.name
    return _is_pass_continue_ellipsis(stmt) or _is_silent_logger_call(stmt, exc_name)


# ── Public scan API ─────────────────────────────────────────────────────


def visit_file(file_path: str, source: str) -> list[Violation]:
    tree = ast.parse(source, filename=file_path)
    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if not _handler_is_silent(node):
            continue
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


def test_no_new_silent_handlers() -> None:
    """Fail if any production file has more silent exception handlers than baseline."""
    assert_no_new_violations(KIND, scan(KIND))
