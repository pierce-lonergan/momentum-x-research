"""Detect `httpx.AsyncClient(...)` (and aiohttp.ClientSession) constructed
outside an `async with` block and not explicitly closed via `aclose()`.

The bug pattern (D219, before today's hotfix):
    _d219_resp = await asyncio.wait_for(
        _d219_httpx.AsyncClient(timeout=3).get(...),  # leaks!
        timeout=3.0,
    )
Each call leaked a connection pool. Over a session that's hundreds of leaked
clients — eventually exhausts file descriptors or stalls on TLS handshakes.

The canonical fix:
    async with _d219_httpx.AsyncClient(timeout=3) as client:
        _d219_resp = await asyncio.wait_for(client.get(...), timeout=3.0)
"""

from __future__ import annotations

import ast

from tests.static_analysis._ast_helpers import (
    Violation,
    assert_no_new_violations,
    build_parent_map,
    collect_violations,
    enclosing_function,
    get_snippet,
    iter_python_files,
)

KIND = "async-leak"

# Class names whose constructors must be closed
_LEAKY_CTORS: frozenset[str] = frozenset({
    "AsyncClient",      # httpx
    "ClientSession",    # aiohttp
})


def _is_leaky_ctor_call(call: ast.Call, leaky_qualified: set[str]) -> bool:
    """Return True if `call` is a constructor for a leaky client."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id in leaky_qualified
    if isinstance(func, ast.Attribute):
        # httpx.AsyncClient(...), aiohttp.ClientSession(...)
        return func.attr in _LEAKY_CTORS
    return False


def _is_inside_async_with(parent_map: dict[ast.AST, ast.AST], call: ast.Call) -> bool:
    """Walk up parents — return True if `call` is the context_expr of an `AsyncWith`."""
    cur = parent_map.get(call)
    while cur is not None:
        if isinstance(cur, ast.AsyncWith):
            for item in cur.items:
                # The item may wrap our call directly or via ast.Call chain
                if _expression_contains(item.context_expr, call):
                    return True
            # Don't keep walking past the AsyncWith — if we passed through it,
            # the call wasn't its context_expr.
            return False
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module)):
            return False
        cur = parent_map.get(cur)
    return False


def _expression_contains(expr: ast.AST, target: ast.AST) -> bool:
    """Return True if `target` is a sub-node of `expr`."""
    return any(node is target for node in ast.walk(expr))


def _has_explicit_close(
    parent_map: dict[ast.AST, ast.AST],
    call: ast.Call,
    target_name: str | None,
) -> bool:
    """Return True if the assigned client is closed via `await x.aclose()`
    somewhere in the same enclosing function."""
    if not target_name:
        return False
    func = enclosing_function(parent_map, call)
    if func is None or not hasattr(func, "body"):
        return False
    for node in ast.walk(func):
        if (
            isinstance(node, ast.Await)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "aclose"
            and isinstance(node.value.func.value, ast.Name)
            and node.value.func.value.id == target_name
        ):
            return True
        # Also accept self._client.aclose() pattern
        if (
            isinstance(node, ast.Await)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute)
            and node.value.func.attr == "aclose"
            and isinstance(node.value.func.value, ast.Attribute)
            and node.value.func.value.attr == target_name
        ):
            return True
    return False


def _assignment_target(parent_map: dict[ast.AST, ast.AST], call: ast.Call) -> str | None:
    """If `call` is the RHS of an assignment, return the attribute name being assigned."""
    parent = parent_map.get(call)
    if isinstance(parent, ast.Assign):
        for tgt in parent.targets:
            if isinstance(tgt, ast.Name):
                return tgt.id
            if isinstance(tgt, ast.Attribute):
                return tgt.attr
    if isinstance(parent, ast.AnnAssign) and isinstance(parent.target, ast.Name):
        return parent.target.id
    return None


# ── Public scan API ─────────────────────────────────────────────────────


def visit_file(file_path: str, source: str) -> list[Violation]:
    tree = ast.parse(source, filename=file_path)
    parent_map = build_parent_map(tree)

    # Resolve which Names refer to leaky ctors based on imports
    leaky_local_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {"httpx", "aiohttp"}:
            for alias in node.names:
                if alias.name in _LEAKY_CTORS:
                    leaky_local_names.add(alias.asname or alias.name)

    violations: list[Violation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not _is_leaky_ctor_call(node, leaky_local_names):
            continue
        if _is_inside_async_with(parent_map, node):
            continue
        target_name = _assignment_target(parent_map, node)
        if _has_explicit_close(parent_map, node, target_name):
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


def test_no_new_async_leaks() -> None:
    """Fail if any production file has more leaked AsyncClient/ClientSession
    constructions than its baseline."""
    assert_no_new_violations(KIND, scan(KIND))
