"""D-code registry audit.

Per `26_d_code_registry.md`: every D-code reserved in the registry should
appear in the codebase somewhere — production code (the wire-in), tests
(injection / behavioral), or docs (spec). The audit categorizes each
reserved code as:

  LIVE      — referenced in production source (src/, main.py, scripts/, config/)
  TESTED    — referenced in tests/ but not production
  DOC_ONLY  — referenced only in docs/ (reserved but not yet wired)
  ORPHAN    — mentioned nowhere outside the registry itself (REMOVE or WIRE)

Usage:
    python scripts/audit_d_codes.py            # print categorized table
    python scripts/audit_d_codes.py --strict   # exit 1 if any orphans

Wired into `tests/static_analysis/test_d_code_audit.py` as a regression
gate: orphans must be either removed from the registry or wired in code.

Why this matters: reserved-but-unwired D-codes accumulate as silent debt.
A code reserved 3 months ago that never landed means a production
condition that should have been logged is silently passing through.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = REPO_ROOT / "docs" / "research-log" / "26_d_code_registry.md"

# D-code regex: matches D### with 2-3 digits. Excludes commit-hash-like
# patterns by requiring word-boundary + uppercase D + digits + word-boundary.
_DCODE_RE = re.compile(r"\bD(\d{2,3})\b")

# Known non-codes to filter out of the audit:
#   - D000-D199 / D200-D299 are convention boundary refs in the doc text
#   - D23 / D24 / D25 are date references ("post-D23 patches", "D22-D25 discipline")
#   - "Next available code: DXXX" sentinel auto-detected separately
KNOWN_NON_CODES: set[str] = {
    "D000", "D199", "D200", "D299",
    "D23", "D24", "D25",
}

# Auto-detect the "Next available code" sentinel
_NEXT_AVAILABLE_RE = re.compile(
    # Tolerates markdown bolding: `**Next available code:** D271`
    r"Next available code[\s\*:]+D(\d{2,3})", re.IGNORECASE,
)

PRODUCTION_DIRS = ("src", "scripts", "config", "main.py")
TEST_DIRS = ("tests",)
DOC_DIRS = ("docs",)


@dataclass
class DCodeStatus:
    code: str          # e.g. "D241"
    in_production: int  # number of production-source references
    in_tests: int       # number of test references
    in_docs_outside_registry: int  # docs/ references EXCLUDING the registry itself
    category: str       # LIVE / TESTED / DOC_ONLY / ORPHAN

    @property
    def is_orphan(self) -> bool:
        return self.category == "ORPHAN"


def _parse_reserved_codes(registry_path: Path) -> set[str]:
    """Return all D-codes mentioned in the registry as the canonical
    reserved set, EXCLUDING:
      - convention-boundary refs (D000, D199, D200, D299, etc. — see KNOWN_NON_CODES)
      - date references like "D23", "D24", "D25"
      - "Next available code: DXXX" sentinel markers
    """
    text = registry_path.read_text(encoding="utf-8")
    all_codes = {f"D{m.group(1)}" for m in _DCODE_RE.finditer(text)}
    next_available = {f"D{m.group(1)}" for m in _NEXT_AVAILABLE_RE.finditer(text)}
    return all_codes - KNOWN_NON_CODES - next_available


def _count_references(code: str, root: Path, includes: tuple[str, ...]) -> int:
    """Count file-level (not line-level) references to `code` under any of
    `includes` directories within `root`."""
    pattern = re.compile(rf"\b{re.escape(code)}\b")
    count = 0
    for inc in includes:
        path = root / inc
        if not path.exists():
            continue
        if path.is_file():
            try:
                if pattern.search(path.read_text(encoding="utf-8", errors="ignore")):
                    count += 1
            except OSError as _e:  # noqa: silent-handler — file unreadable; skip per audit traversal discipline
                _ = _e  # explicit: ignored unreadable file in walk
                continue
        else:
            for p in path.rglob("*"):
                if not p.is_file():
                    continue
                # Skip binary / large files
                if p.suffix in {".pyc", ".parquet", ".json", ".jsonl", ".csv", ".log",
                                 ".lock", ".png", ".jpg", ".gif", ".pdf"}:
                    continue
                if "__pycache__" in p.parts or ".git" in p.parts:
                    continue
                try:
                    if pattern.search(p.read_text(encoding="utf-8", errors="ignore")):
                        count += 1
                except OSError as _e:  # noqa: silent-handler — unreadable file; skip per audit traversal discipline
                    _ = _e
                    continue
    return count


def _count_docs_references_excluding_registry(code: str, root: Path) -> int:
    """Count doc references to `code` EXCLUDING the registry file itself."""
    pattern = re.compile(rf"\b{re.escape(code)}\b")
    count = 0
    docs_root = root / "docs"
    if not docs_root.exists():
        return 0
    for p in docs_root.rglob("*.md"):
        if p == REGISTRY_PATH:
            continue
        try:
            if pattern.search(p.read_text(encoding="utf-8", errors="ignore")):
                count += 1
        except OSError as _e:  # noqa: silent-handler — unreadable file; skip per audit traversal discipline
            _ = _e
            continue
    return count


def audit_d_codes(
    registry_path: Path = REGISTRY_PATH,
    repo_root: Path = REPO_ROOT,
) -> list[DCodeStatus]:
    """Run the full audit. Returns one DCodeStatus per reserved code."""
    reserved = _parse_reserved_codes(registry_path)
    results: list[DCodeStatus] = []
    for code in sorted(reserved):
        prod = _count_references(code, repo_root, PRODUCTION_DIRS)
        tests = _count_references(code, repo_root, TEST_DIRS)
        docs = _count_docs_references_excluding_registry(code, repo_root)
        if prod > 0:
            cat = "LIVE"
        elif tests > 0:
            cat = "TESTED"
        elif docs > 0:
            cat = "DOC_ONLY"
        else:
            cat = "ORPHAN"
        results.append(DCodeStatus(
            code=code, in_production=prod, in_tests=tests,
            in_docs_outside_registry=docs, category=cat,
        ))
    return results


def _format_table(results: list[DCodeStatus]) -> str:
    lines = []
    lines.append(f"{'CODE':<8} {'CATEGORY':<10} {'PROD':>4} {'TESTS':>5} {'DOCS':>4}")
    lines.append("-" * 38)
    for r in results:
        lines.append(
            f"{r.code:<8} {r.category:<10} "
            f"{r.in_production:>4} {r.in_tests:>5} {r.in_docs_outside_registry:>4}"
        )
    # Summary line
    total = len(results)
    by_cat: dict[str, int] = {}
    for r in results:
        by_cat[r.category] = by_cat.get(r.category, 0) + 1
    lines.append("-" * 38)
    summary = ", ".join(f"{k}={v}" for k, v in sorted(by_cat.items()))
    lines.append(f"TOTAL {total} ({summary})")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict", action="store_true",
        help="Exit 1 if any orphan D-codes are detected.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Emit machine-readable JSON instead of the formatted table.",
    )
    args = parser.parse_args()

    results = audit_d_codes()

    if args.json:
        print(json.dumps([{
            "code": r.code, "category": r.category,
            "in_production": r.in_production, "in_tests": r.in_tests,
            "in_docs_outside_registry": r.in_docs_outside_registry,
        } for r in results], indent=2))
    else:
        print(_format_table(results))

    if args.strict:
        orphans = [r.code for r in results if r.is_orphan]
        if orphans:
            print(
                f"\n[d-code-audit] STRICT MODE: {len(orphans)} orphan code(s) "
                f"detected: {orphans}",
                file=sys.stderr,
            )
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
