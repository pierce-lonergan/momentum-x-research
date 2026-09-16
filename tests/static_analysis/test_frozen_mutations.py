"""Detect attribute assignment to frozen Pydantic / dataclass instances.

The bug pattern (D219, today): `_ec.float_shares = enrich["float_shares"]`
where `_ec` is a `CandidateStock` declared `frozen=True`. Pydantic raises
``ValidationError: Instance is frozen``; dataclasses raise ``FrozenInstanceError``.
Either way, every candidate evaluation crashes.

Approach:
1. Per-file: discover `class X(BaseModel, frozen=True)` and `@dataclass(frozen=True)`.
2. Union with project-wide `KNOWN_FROZEN_TYPES` allowlist.
3. Track local variable names whose RHS is a constructor call to a frozen class
   OR loop variables iterating over a list/iterable annotated with a frozen type.
4. Flag `Assign` / `AugAssign` whose target is `<tracked_name>.<attr>`.

Out of scope (false-negatives accepted): cross-file type inference, function
return-type tracking, type narrowing through `if isinstance(...)`. The behavioral
test in `tests/unit/test_d219_float_enrichment.py` covers today's specific case
end-to-end as a safety net.
"""

from __future__ import annotations

import ast

from tests.static_analysis._ast_helpers import (
    KNOWN_FROZEN_TYPES,
    Violation,
    assert_no_new_violations,
    collect_violations,
    get_snippet,
    iter_python_files,
)

KIND = "frozen-mutate"


# ── Visitor ──────────────────────────────────────────────────────────────


class _FrozenMutationVisitor(ast.NodeVisitor):
    """Two-pass visitor: collect frozen names, then flag attr assignments."""

    def __init__(self, file_path: str, source: str) -> None:
        self.file_path = file_path
        self.source = source
        self.frozen_class_names: set[str] = set(KNOWN_FROZEN_TYPES)
        self.tracked_vars: set[str] = set()
        self.violations: list[Violation] = []

    # Pass 1 — discover frozen classes defined in this file
    def discover_classes(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            # Pydantic: class X(BaseModel, frozen=True)
            has_basemodel_base = any(
                (isinstance(b, ast.Name) and b.id == "BaseModel")
                or (isinstance(b, ast.Attribute) and b.attr == "BaseModel")
                for b in node.bases
            )
            has_frozen_kw = any(
                kw.arg == "frozen" and isinstance(kw.value, ast.Constant) and kw.value.value is True
                for kw in node.keywords
            )
            if has_basemodel_base and has_frozen_kw:
                self.frozen_class_names.add(node.name)
                continue
            # @dataclass(frozen=True) decorator
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                func = dec.func
                if (
                    (isinstance(func, ast.Name) and func.id == "dataclass")
                    or (isinstance(func, ast.Attribute) and func.attr == "dataclass")
                ):
                    if any(
                        kw.arg == "frozen"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is True
                        for kw in dec.keywords
                    ):
                        self.frozen_class_names.add(node.name)

    # Pass 2 — track names that hold frozen instances
    def discover_tracked_vars(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            # Direct construction: x = CandidateStock(...)
            if isinstance(node, ast.Assign):
                if isinstance(node.value, ast.Call) and self._call_returns_frozen(node.value):
                    for tgt in node.targets:
                        if isinstance(tgt, ast.Name):
                            self.tracked_vars.add(tgt.id)
            # for x in <something_frozen_listy>: ...
            if isinstance(node, (ast.For, ast.AsyncFor)):
                if self._iter_yields_frozen(node.iter) and isinstance(node.target, ast.Name):
                    self.tracked_vars.add(node.target.id)
            # for idx, x in enumerate(<frozen_list>):
            if isinstance(node, (ast.For, ast.AsyncFor)) and isinstance(node.target, ast.Tuple):
                if (
                    isinstance(node.iter, ast.Call)
                    and isinstance(node.iter.func, ast.Name)
                    and node.iter.func.id == "enumerate"
                    and node.iter.args
                    and self._iter_yields_frozen(node.iter.args[0])
                ):
                    if len(node.target.elts) == 2 and isinstance(node.target.elts[1], ast.Name):
                        self.tracked_vars.add(node.target.elts[1].id)
            # Type annotations: x: CandidateStock = ...
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if self._annotation_is_frozen(node.annotation):
                    self.tracked_vars.add(node.target.id)

    def _call_returns_frozen(self, call: ast.Call) -> bool:
        func = call.func
        if isinstance(func, ast.Name):
            return func.id in self.frozen_class_names
        if isinstance(func, ast.Attribute):
            # X.model_copy(...) returns frozen X — but assigning back is the FIX,
            # so we still want to track the name for downstream attribute-set checks.
            if func.attr in {"model_copy", "model_validate"}:
                return True
            return func.attr in self.frozen_class_names
        return False

    def _iter_yields_frozen(self, expr: ast.expr) -> bool:
        # Heuristic: a Name that we've already tagged via annotation, or a list/tuple
        # of frozen instances. Accept Name `watchlist` if any annotation in scope
        # said it's a list[CandidateStock]. This is best-effort.
        if isinstance(expr, ast.Name):
            return expr.id in self.tracked_vars
        if isinstance(expr, ast.Call):
            return self._call_returns_frozen(expr)
        return False

    def _annotation_is_frozen(self, annot: ast.expr | None) -> bool:
        if annot is None:
            return False
        if isinstance(annot, ast.Name):
            return annot.id in self.frozen_class_names
        if isinstance(annot, ast.Subscript):
            # list[CandidateStock], List[CandidateStock], Sequence[CandidateStock], ...
            slice_node = annot.slice
            if isinstance(slice_node, ast.Name):
                return slice_node.id in self.frozen_class_names
            if isinstance(slice_node, ast.Tuple):
                return any(
                    isinstance(e, ast.Name) and e.id in self.frozen_class_names
                    for e in slice_node.elts
                )
        return False

    # Pass 3 — flag mutations
    def find_mutations(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for tgt in targets:
                    if (
                        isinstance(tgt, ast.Attribute)
                        and isinstance(tgt.value, ast.Name)
                        and tgt.value.id in self.tracked_vars
                    ):
                        self.violations.append(
                            Violation(
                                path=self.file_path,
                                line=node.lineno,
                                col=node.col_offset,
                                kind=KIND,
                                snippet=get_snippet(self.source, node.lineno),
                            )
                        )


# ── Public scan API (used by _update_baseline.py and the test) ───────────


def visit_file(file_path: str, source: str) -> list[Violation]:
    tree = ast.parse(source, filename=file_path)
    v = _FrozenMutationVisitor(file_path, source)
    v.discover_classes(tree)
    v.discover_tracked_vars(tree)
    v.find_mutations(tree)
    return v.violations


def scan(kind: str = KIND) -> list[Violation]:
    """Public entry for _update_baseline.py."""
    return collect_violations(visit_file, iter_python_files(), kind)


# ── Pytest test ──────────────────────────────────────────────────────────


def test_no_new_frozen_mutations() -> None:
    """Fail if any production file has more `obj.attr = X` on frozen instances
    than its baseline in `_baselines.json`."""
    assert_no_new_violations(KIND, scan(KIND))
