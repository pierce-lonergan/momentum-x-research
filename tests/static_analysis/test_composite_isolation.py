"""Permanent guard: src.composite must NOT import from production code.

The composite scorer is the future replacement for the 13-gate cascade. It must
be runnable from a Jupyter notebook, from shadow-mode wiring, from sweep
harnesses, and from anywhere else without booting half the trading system.

Forbidden import prefixes:
  - src.core.*           (orchestrator, scoring, models — production hot path)
  - src.agents.*         (LLM agents with caching, network, singletons)
  - src.production_arena.*  (arena uses agents indirectly via signal synthesis;
                              composite must not depend on the arena either)

Allowed: stdlib, numpy, sklearn, src.composite.*

This test runs as part of `pytest tests/static_analysis/` and fails any PR
that breaks composite isolation. The temptation to "just import this one thing
from src.core" arrives often during Phase 5+ — this guard is the wall.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_COMPOSITE_DIR = _PROJECT_ROOT / "src" / "composite"

_FORBIDDEN_PREFIXES = (
    "src.core.",
    "src.agents.",
    "src.production_arena.",
)


def _gather_imports(file_path: Path) -> set[str]:
    """Static-AST import scan — finds all imported module paths in a Python file."""
    with open(file_path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=str(file_path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
    return imports


def _check_isolation(file_path: Path) -> list[str]:
    """Return list of forbidden imports (empty list = clean)."""
    imports = _gather_imports(file_path)
    return [m for m in imports if any(m.startswith(p) for p in _FORBIDDEN_PREFIXES)]


def _composite_files_to_check() -> list[Path]:
    """Files in src/composite/ that should respect isolation.

    Note: src/composite/train.py is intentionally allowed to import
    src.production_arena.* — it's offline tooling, not the hot path.
    """
    files = []
    for p in _COMPOSITE_DIR.glob("*.py"):
        if p.name in {"train.py"}:
            continue  # offline tool, exempt
        files.append(p)
    return files


# ── Tests ────────────────────────────────────────────────────────────────


def test_composite_init_is_isolated():
    path = _COMPOSITE_DIR / "__init__.py"
    forbidden = _check_isolation(path)
    assert not forbidden, (
        f"src/composite/__init__.py imports forbidden module(s): {forbidden}\n"
        f"The composite package must be runnable standalone — no imports from "
        f"{_FORBIDDEN_PREFIXES}."
    )


def test_composite_features_is_isolated():
    path = _COMPOSITE_DIR / "features.py"
    forbidden = _check_isolation(path)
    assert not forbidden, (
        f"src/composite/features.py imports forbidden module(s): {forbidden}\n"
        f"Hot-path isolation broken. Add the import to train.py if it's offline-only."
    )


def test_composite_score_is_isolated():
    path = _COMPOSITE_DIR / "score.py"
    forbidden = _check_isolation(path)
    assert not forbidden, (
        f"src/composite/score.py imports forbidden module(s): {forbidden}\n"
        f"score.py is THE hot path — it gets called from shadow mode in production "
        f"and must never pull in src.core / src.agents / src.production_arena."
    )


def test_train_py_is_excluded_from_isolation():
    """Sanity: train.py IS allowed to import from src.production_arena.

    This test exists to document that exception and catch regressions to the
    rule (e.g. if someone tries to "fix" train.py by removing those imports
    and we lose the arena-conditioned training loop).
    """
    path = _COMPOSITE_DIR / "train.py"
    if not path.exists():
        pytest.skip("train.py not present (composite package may have moved)")
    imports = _gather_imports(path)
    expected_arena = any(m.startswith("src.production_arena") for m in imports)
    assert expected_arena, (
        "train.py no longer imports src.production_arena — verify the training "
        "loop is still arena-conditioned (per Phase 3 design)."
    )


def test_all_composite_hot_path_files_isolated():
    """Catch-all: every .py file in src/composite/ except train.py must be isolated."""
    failures: dict[str, list[str]] = {}
    for path in _composite_files_to_check():
        forbidden = _check_isolation(path)
        if forbidden:
            failures[path.name] = forbidden
    assert not failures, (
        "Composite isolation violations:\n"
        + "\n".join(f"  {name}: {imports}" for name, imports in failures.items())
    )
