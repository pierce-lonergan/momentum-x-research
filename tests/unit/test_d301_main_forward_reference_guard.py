"""D301 (2026-05-19) -- Static guard against forward-reference crashes in main.py.

Pin the 2026-05-19 catastrophic startup crash from doc 166:
  D297 added `trade_journal=trade_journal` as a kwarg to FillStreamBridge
  at main.py:729, but `trade_journal = TradeJournal()` was at line 1004 --
  275 lines AFTER the use. Python is dynamically scoped, but a name
  referenced inside the same function scope before assignment raises
  ``UnboundLocalError: cannot access local variable 'trade_journal'
  where it is not associated with a value``.

  Consequence: the bot crashed at 04:30 ET startup with exit code 90.
  No trades fired. No D91 close of yesterday's NXXT position. No Discord
  alerts (the crash happened before any alert path could initialize).
  The day was saved only by NXXT's overnight rally luck (+$8,880 unrealized).

  D297's own unit tests (16/16) passed because they constructed
  FillStreamBridge directly with mocks -- they never exercised main.py's
  full top-level cmd_paper() function and so missed the ordering bug.

This file is the AST-driven static guard that catches this class of bug
BEFORE any commit lands. Every name passed as a kwarg or arg in
main.cmd_paper must be assigned ABOVE its first use.

The test runs in <1s and would have caught D301 the moment doc 166 was
committed.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterator

import pytest

REPO = Path(__file__).resolve().parents[2]
MAIN_PY = REPO / "main.py"


# ── AST helper ──────────────────────────────────────────────────────


def _find_func(tree: ast.Module, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"function `{name}` not found in main.py")


def _local_assignments(func: ast.AsyncFunctionDef | ast.FunctionDef) -> dict[str, int]:
    """Return ``{name: first_lineno}`` for every name assigned in the
    function's local scope. Includes:
      - Plain assigns (``x = ...``)
      - Augmented assigns (``x += ...``) — first one counts
      - For/with target bindings (``for x in ...``, ``with ... as x:``)
      - Except handler bindings (``except E as x:``)
      - Import statements (``from foo import bar``, ``import baz``)

    We only look at top-level assignments inside the function body
    (we descend through if/for/while/try blocks since Python treats
    those as the same scope, but stop at nested function/class defs).

    Comprehension scopes (``[x for x in ...]``) bind their iteration
    variables in a CLOSED scope -- the function's local scope is NOT
    affected. We must not descend into them.
    """
    out: dict[str, int] = {}

    # Function parameters are bound at function entry (use func.lineno).
    for arg in (func.args.posonlyargs + func.args.args + func.args.kwonlyargs):
        out.setdefault(arg.arg, func.lineno)
    if func.args.vararg:
        out.setdefault(func.args.vararg.arg, func.lineno)
    if func.args.kwarg:
        out.setdefault(func.args.kwarg.arg, func.lineno)

    def visit(node: ast.AST) -> None:
        # Don't descend into nested functions/classes (different scope)
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef, ast.Lambda,
                              ast.ClassDef)) and node is not func:
            return
        # Don't descend into comprehensions -- they have their own scope
        # in Python 3 (PEP 3101). Names bound in a `[x for x in ...]`
        # do NOT leak to the enclosing function's local scope.
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            return
        if isinstance(node, ast.Assign):
            for target in node.targets:
                for n in _iter_names(target):
                    out.setdefault(n, node.lineno)
        elif isinstance(node, ast.AugAssign):
            for n in _iter_names(node.target):
                out.setdefault(n, node.lineno)
        elif isinstance(node, ast.AnnAssign):
            for n in _iter_names(node.target):
                out.setdefault(n, node.lineno)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            for n in _iter_names(node.target):
                out.setdefault(n, node.lineno)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    for n in _iter_names(item.optional_vars):
                        out.setdefault(n, node.lineno)
        elif isinstance(node, ast.ExceptHandler):
            # `except E as x:` -- x is bound in the handler body
            if node.name is not None:
                out.setdefault(node.name, node.lineno)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                out.setdefault(alias.asname or alias.name.split(".")[0],
                                node.lineno)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                out.setdefault(alias.asname or alias.name, node.lineno)
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(func)
    return out


def _iter_names(target: ast.AST) -> Iterator[str]:
    """Yield every Name id touched by an assignment target."""
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            yield from _iter_names(elt)
    elif isinstance(target, ast.Starred):
        yield from _iter_names(target.value)


def _all_name_uses(func: ast.AsyncFunctionDef | ast.FunctionDef
                   ) -> list[tuple[str, int]]:
    """Return [(name, lineno), ...] for every Name(Load) reference inside
    the function. Excludes uses inside nested function/class scopes AND
    inside comprehension scopes (those have closed scopes in Python 3 --
    a Name referenced inside a list/set/dict/gen comp does not consume
    the enclosing function's local).
    """
    uses: list[tuple[str, int]] = []

    def visit(node: ast.AST) -> None:
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef, ast.Lambda,
                              ast.ClassDef)) and node is not func:
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            return  # closed scope -- skip
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            uses.append((node.id, node.lineno))
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(func)
    return uses


# ── Tests ───────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def main_tree():
    return ast.parse(MAIN_PY.read_text(encoding="utf-8"))


def test_main_py_parses_cleanly(main_tree):
    """If this fails, main.py has a syntax error and the bot won't even
    start. We want this to fail BEFORE pytest reaches any other test."""
    assert main_tree is not None


def test_cmd_paper_exists(main_tree):
    """The entrypoint the scheduled task invokes."""
    fn = _find_func(main_tree, "cmd_paper")
    assert fn is not None


# ── Headline regression: the exact D297 forward-reference case ──────


def test_trade_journal_assigned_before_first_use_in_cmd_paper(main_tree):
    """The 2026-05-19 crash. ``trade_journal`` was used at line 729 (as a
    kwarg to FillStreamBridge) but assigned at line 1004 -- a 275-line
    forward reference inside the same local scope."""
    fn = _find_func(main_tree, "cmd_paper")
    assignments = _local_assignments(fn)
    uses = _all_name_uses(fn)

    assert "trade_journal" in assignments, (
        "trade_journal must be assigned in cmd_paper"
    )
    assign_line = assignments["trade_journal"]
    first_use = min((ln for n, ln in uses if n == "trade_journal"),
                    default=None)
    assert first_use is not None, "no use of trade_journal? unexpected"
    assert first_use >= assign_line, (
        f"FORWARD-REFERENCE: trade_journal first used at line {first_use} "
        f"but assigned at line {assign_line}. This is the D301 / 2026-05-19 "
        "crash pattern -- would UnboundLocalError at startup."
    )


# ── Generalised guard: catch the pattern for ALL local names ────────


# Some names appear as kwargs but ARE legitimately Python builtins,
# imports at module level, or globals. We exempt names that are NOT
# in the function's local-assignment set (those resolve to module scope).
# We ALSO exempt names that come from `for name in ...` targets where
# the target may legitimately precede the reference site (unusual but
# valid). The whitelist below is for known false-positives only.
_KNOWN_NON_LOCAL_NAMES: frozenset[str] = frozenset({
    # Built-in or module-level names commonly referenced before any
    # local def in cmd_paper. Add to this list with justification if
    # the guard generates a false positive.
})


def test_no_forward_references_in_cmd_paper(main_tree):
    """For every name that is BOTH locally assigned and locally used in
    cmd_paper, the first use must come at or after the first assignment.
    A forward reference inside the same scope is the exact pattern that
    crashed the bot today."""
    fn = _find_func(main_tree, "cmd_paper")
    assignments = _local_assignments(fn)
    uses = _all_name_uses(fn)

    # First use per name (only for names that ARE locally assigned)
    first_use_per_name: dict[str, int] = {}
    for name, ln in uses:
        if name not in assignments:
            continue
        if name in _KNOWN_NON_LOCAL_NAMES:
            continue
        if name not in first_use_per_name or ln < first_use_per_name[name]:
            first_use_per_name[name] = ln

    violations = []
    for name, first_use in first_use_per_name.items():
        assign_line = assignments[name]
        if first_use < assign_line:
            violations.append((name, first_use, assign_line))

    if violations:
        msg = "FORWARD-REFERENCE BUG(S) in cmd_paper -- would crash at startup:\n"
        for name, use, assign in sorted(violations, key=lambda v: v[1]):
            msg += (f"  {name!r}: first used at line {use}, "
                    f"first assigned at line {assign}\n")
        pytest.fail(msg)


# ── Specific guards: the four names doc 166 introduced as kwargs ────


@pytest.mark.parametrize("name", [
    "position_manager",  # D295 + D297 callsites
    "trade_journal",     # D295 + D297 callsites -- THE bug
    "fill_bridge",       # D297 ctor target
    "tranche_monitor",   # D297 ctor input
])
def test_d166_kwargs_are_resolved_in_order(name, main_tree):
    """Each of the names doc 166's commits passed as kwargs into
    FillStreamBridge() (or upstream constructors) must be defined before
    its first use. This is a finer-grained version of the general guard
    above so any of these breaking gets a named failure."""
    fn = _find_func(main_tree, "cmd_paper")
    assignments = _local_assignments(fn)
    uses = [(n, ln) for n, ln in _all_name_uses(fn) if n == name]
    if name not in assignments:
        pytest.skip(f"{name} is not locally assigned in cmd_paper "
                    "(module-level or imported -- not subject to this check)")
    if not uses:
        pytest.skip(f"{name} is assigned but never used -- guard inapplicable")
    first_use = min(ln for _, ln in uses)
    assign_line = assignments[name]
    assert first_use >= assign_line, (
        f"{name!r}: first used at line {first_use} but assigned at line "
        f"{assign_line} (D301 forward-reference pattern)"
    )


# ── Static check: no duplicate assignment of trade_journal ──────────


def test_trade_journal_assigned_exactly_once_in_cmd_paper(main_tree):
    """During the D301 hotfix we moved the trade_journal init block up
    and deleted the old site. If a future commit accidentally re-creates
    the duplicate (two TradeJournal() calls in the same function), the
    second one silently overwrites the first and any state populated
    between them is lost. Pin: there must be exactly ONE init."""
    fn = _find_func(main_tree, "cmd_paper")
    n_inits = 0
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign):
            # Match `x = TradeJournal()`
            if not any(isinstance(t, ast.Name) and t.id == "trade_journal"
                       for t in node.targets):
                continue
            v = node.value
            if isinstance(v, ast.Call):
                func_repr = (v.func.id if isinstance(v.func, ast.Name)
                             else (v.func.attr if isinstance(v.func, ast.Attribute) else ""))
                if func_repr == "TradeJournal":
                    n_inits += 1
    assert n_inits == 1, (
        f"trade_journal = TradeJournal() found {n_inits} time(s) -- "
        "must be exactly 1 (duplicate init would silently drop state)"
    )


# ── End-to-end sanity: main.py can be imported without crashing ─────


def test_main_module_imports_without_executing_cmd_paper():
    """If a future change introduces an import-time error (e.g. a top-
    level name that doesn't exist), `import main` will crash. This test
    confirms the module loads even though we don't run cmd_paper."""
    import importlib
    # main.py guards heavy work behind `if __name__ == '__main__':`,
    # so importing it should not perform any I/O.
    import main as _m  # noqa
    importlib.reload(_m)  # safety: actually re-execute top-level


# ── Coverage guard: the D297 kwargs are actually passed through ─────


def test_fill_stream_bridge_ctor_in_main_passes_d297_kwargs(main_tree):
    """The D297 fix is only effective if main.py passes both kwargs into
    the bridge ctor. If a future refactor accidentally drops either,
    Bug A + B reopen silently (Discord alerts wouldn't catch it; only
    the EOD recon would, the next day).
    """
    fn = _find_func(main_tree, "cmd_paper")
    found = False
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        # Match FillStreamBridge(...)
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == "FillStreamBridge"):
            continue
        kwarg_names = {kw.arg for kw in node.keywords}
        assert "position_manager" in kwarg_names, (
            "FillStreamBridge ctor missing D297 kwarg `position_manager`"
        )
        assert "trade_journal" in kwarg_names, (
            "FillStreamBridge ctor missing D297 kwarg `trade_journal`"
        )
        found = True
    assert found, "no FillStreamBridge(...) call found in cmd_paper"
