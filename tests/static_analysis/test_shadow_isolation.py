"""Permanent guard: production code MUST NOT READ shadow telemetry.

Per the Phase 5 calibration:
> Both shadows must be write-only, never-read by the production decision path.
> No orchestrator code reads `shadow_v0` or `shadow_inverted` fields to make
> decisions. The shadow data is pure telemetry until at least 5 sessions have
> accumulated AND we've done a post-session analysis that survives a diagnostic
> pass as rigorous as today's.

This test enforces the rule statically. It scans production source files for:

  1. Subscript access to shadow field names (`d["shadow_v0"]`, `d["shadow_inverted"]`,
     `d["composite_score_full"]`, etc.) — direct read indicators.
  2. Attribute access to shadow field names (`obj.shadow_v0`, etc.).
  3. Imports from `src.shadow.*` that are NOT through the documented WRITE-only
     entry points (`maybe_score_composite`, `log_inverted_shadow_safe`,
     `is_*_shadow_enabled`, `get_shadow_logger`).

Production code being scanned:
  - src/core/* (orchestrator, scoring)
  - src/execution/* (order routing, position management)
  - src/agents/* (LLM agents — should never even know about shadow)
  - main.py (the trading loop)
  - src/scanners/* (pre-market scanning)

Files explicitly NOT scanned (legitimate shadow consumers):
  - src/shadow/* (the module itself)
  - tests/* (tests of course read shadow data)
  - scripts/* (offline tooling — shadow analysis scripts)
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]

_PRODUCTION_DIRS_TO_SCAN = [
    _PROJECT_ROOT / "src" / "core",
    _PROJECT_ROOT / "src" / "execution",
    _PROJECT_ROOT / "src" / "agents",
    _PROJECT_ROOT / "src" / "scanners",
    _PROJECT_ROOT / "src" / "data",
]
_MAIN_PY = _PROJECT_ROOT / "main.py"

# Names that indicate shadow READ access. Direct dict subscripting or attribute access.
_FORBIDDEN_FIELD_NAMES = frozenset({
    "shadow_v0",
    "shadow_inverted",
    "shadow_inverted_v0",
    "composite_score_full",
    "composite_score_prescore",
    "shadow_decision_at_threshold_0_40",
    "would_have_inverted_bought",
    "orb_break_timestamp",
    "simulated_entry_price",
    "simulated_entry_basis",
    "agreement",  # the composite-shadow agreement field — but this is generic word.
                  # Filtered to dict-subscript with this exact key string only.
})

# Allowed imports from src.shadow — write-only entry points.
_ALLOWED_SHADOW_IMPORTS = frozenset({
    "maybe_score_composite",
    "log_inverted_shadow_safe",
    "is_composite_shadow_enabled",
    "is_inverted_shadow_enabled",
    "get_shadow_logger",
    "ShadowLogger",
    "INVERTED_ENTRY_BASIS",
    "InvertedShadowEntry",  # may be needed for type hints in write paths
})


def _iter_production_files():
    for dir_ in _PRODUCTION_DIRS_TO_SCAN:
        if not dir_.exists():
            continue
        for p in dir_.rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            yield p
    if _MAIN_PY.exists():
        yield _MAIN_PY


def _check_subscript_reads(tree: ast.AST, file_path: str) -> list[str]:
    """Find `d["<forbidden_field>"]` or `d['<forbidden_field>']` patterns."""
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        # Look for string-constant subscripts
        sl = node.slice
        if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
            if sl.value in _FORBIDDEN_FIELD_NAMES:
                violations.append(
                    f"{file_path}:{node.lineno} — subscript read of "
                    f"shadow field {sl.value!r}"
                )
    return violations


def _check_attribute_reads(tree: ast.AST, file_path: str) -> list[str]:
    """Find `obj.<forbidden_field>` patterns. Strict subset of FORBIDDEN — only
    attribute names that are unambiguously shadow-related (not 'agreement')."""
    strict_attrs = _FORBIDDEN_FIELD_NAMES - {"agreement"}
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        if node.attr in strict_attrs:
            violations.append(
                f"{file_path}:{node.lineno} — attribute read of "
                f"shadow field .{node.attr}"
            )
    return violations


def _check_shadow_imports(tree: ast.AST, file_path: str) -> list[str]:
    """Verify imports from `src.shadow.*` only use the allowed write-only API."""
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if not node.module or not node.module.startswith("src.shadow"):
            continue
        for alias in node.names:
            name = alias.name
            if name == "*":
                violations.append(
                    f"{file_path}:{node.lineno} — wildcard import from "
                    f"src.shadow is forbidden"
                )
                continue
            if name not in _ALLOWED_SHADOW_IMPORTS:
                violations.append(
                    f"{file_path}:{node.lineno} — import of {name!r} from "
                    f"{node.module} is not in the write-only allowlist "
                    f"({sorted(_ALLOWED_SHADOW_IMPORTS)})"
                )
    return violations


def _gather_violations(file_path: Path) -> list[str]:
    try:
        source = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError:
        return []
    rel = str(file_path.relative_to(_PROJECT_ROOT)).replace("\\", "/")
    return (
        _check_subscript_reads(tree, rel)
        + _check_attribute_reads(tree, rel)
        + _check_shadow_imports(tree, rel)
    )


# ── Tests ────────────────────────────────────────────────────────────────


def test_production_code_does_not_read_shadow_fields():
    """No production file may read shadow_v0 / shadow_inverted / composite_score_*
    via subscript or attribute access."""
    all_violations: list[str] = []
    for p in _iter_production_files():
        all_violations.extend(_gather_violations(p))
    assert not all_violations, (
        "Shadow-isolation violations detected:\n" + "\n".join(all_violations)
    )


def test_production_imports_from_src_shadow_use_only_write_api():
    """Imports from src.shadow.* must use the allowed write-only entry points
    (`maybe_score_composite`, `log_inverted_shadow_safe`, etc.). Imports of
    classes/functions not in the allowlist defeat the isolation."""
    import_violations: list[str] = []
    for p in _iter_production_files():
        try:
            source = p.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(p))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        rel = str(p.relative_to(_PROJECT_ROOT)).replace("\\", "/")
        import_violations.extend(_check_shadow_imports(tree, rel))
    assert not import_violations, (
        "Production code imports non-allowlisted symbols from src.shadow:\n"
        + "\n".join(import_violations)
    )


def test_orchestrator_is_specifically_clean():
    """Belt-and-suspenders: orchestrator.py is the highest-risk file. Test it
    explicitly so failures point at it directly."""
    p = _PROJECT_ROOT / "src" / "core" / "orchestrator.py"
    if not p.exists():
        pytest.skip("orchestrator.py not present")
    violations = _gather_violations(p)
    assert not violations, (
        "Orchestrator shadow-isolation violations:\n" + "\n".join(violations)
    )


def test_main_py_is_specifically_clean():
    """Belt-and-suspenders: main.py is the trading loop. Same enforcement."""
    if not _MAIN_PY.exists():
        pytest.skip("main.py not present")
    violations = _gather_violations(_MAIN_PY)
    assert not violations, (
        "main.py shadow-isolation violations:\n" + "\n".join(violations)
    )
