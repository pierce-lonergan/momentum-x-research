"""SYSTEM_MAP linter — enforces invariants documented in INDEX.md.

Parses fenced TOML blocks from d_codes.md, experiments.md, backlog.md
and enforces 6 invariants per d_codes + 2 per experiments + 2 per
backlog. Also emits derived artifacts: _by_category.md, _by_status.md,
and _dependency_graph.dot (if graphviz_emit is called separately).

USAGE:
  python docs/SYSTEM_MAP/_linter.py                # run all checks
  python docs/SYSTEM_MAP/_linter.py --emit-derived # also write reverse-index docs
  python docs/SYSTEM_MAP/_linter.py --strict       # warnings become errors

EXIT CODES:
  0 = all invariants pass
  1 = at least one invariant violation
  2 = warnings only (--strict promotes to 1)

Run before each commit (CI hook also). The hook in .githooks/pre-commit
enforces changelog atomicity but does NOT run this linter (would slow
every commit). Run manually after editing any SYSTEM_MAP file.
"""
from __future__ import annotations

import argparse
import re
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
SM_DIR = Path(__file__).resolve().parent

D_CODES_FILE = SM_DIR / "d_codes.md"
EXPERIMENTS_FILE = SM_DIR / "experiments.md"
BACKLOG_FILE = SM_DIR / "backlog.md"
OPUS_DIR = REPO / "docs" / "research-log"
SRC_DIRS = [REPO / "src", REPO / "scripts", REPO / "main.py"]


# ── data classes ────────────────────────────────────────────────────


@dataclass
class Violation:
    severity: str          # "ERROR" | "WARNING"
    file: str
    rule: str
    target: str            # D-code or experiment ID
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.file} · rule={self.rule} · {self.target}: {self.message}"


@dataclass
class LintReport:
    errors: list[Violation] = field(default_factory=list)
    warnings: list[Violation] = field(default_factory=list)
    n_d_codes: int = 0
    n_experiments: int = 0
    n_backlog: int = 0

    def add_error(self, **kw: Any) -> None:
        self.errors.append(Violation(severity="ERROR", **kw))

    def add_warning(self, **kw: Any) -> None:
        self.warnings.append(Violation(severity="WARNING", **kw))

    def has_issues(self) -> bool:
        return bool(self.errors) or bool(self.warnings)


# ── TOML extraction from fenced markdown blocks ─────────────────────


_TOML_BLOCK_RE = re.compile(r"```toml\s*\n(.*?)```", re.DOTALL)


def extract_toml_blocks(md_path: Path) -> dict[str, dict[str, Any]]:
    """Read markdown file, concatenate every ```toml block, parse as one
    TOML document. Returns the parsed dict (keys = block identifiers,
    e.g. D310 / Continuer_v2 / D310_L3)."""
    if not md_path.exists():
        return {}
    text = md_path.read_text(encoding="utf-8")
    blocks = _TOML_BLOCK_RE.findall(text)
    if not blocks:
        return {}
    combined = "\n\n".join(blocks)
    try:
        return tomllib.loads(combined)
    except tomllib.TOMLDecodeError as e:
        raise SystemExit(
            f"FATAL: TOML parse error in {md_path}: {e}\n"
            f"Fix the file by hand and re-run the linter."
        )


# ── invariants: d_codes.md ──────────────────────────────────────────


def lint_d_codes(d_codes: dict[str, dict], report: LintReport) -> None:
    """Apply 6 invariants from INDEX.md schemas section."""
    report.n_d_codes = len(d_codes)
    valid_keys = set(d_codes.keys())

    for code, entry in d_codes.items():
        status = entry.get("status", "")
        superseded_by = entry.get("superseded_by", []) or []
        supersedes = entry.get("supersedes", []) or []
        depends_on = entry.get("depends_on", []) or []
        docs = entry.get("docs", []) or []
        category = entry.get("category", "")

        # 1. Status-supersession consistency
        if superseded_by and status not in ("DEPRECATED", "REVERTED"):
            report.add_error(
                file="d_codes.md", rule="status_supersession", target=code,
                message=(
                    f"superseded_by={superseded_by} but status={status!r}; "
                    f"must be DEPRECATED or REVERTED"
                ),
            )

        # 2. Bidirectional pointer agreement
        for b in supersedes:
            if b not in d_codes:
                report.add_warning(
                    file="d_codes.md", rule="bidirectional_pointer",
                    target=code,
                    message=f"supersedes contains {b!r} which is not in this file",
                )
                continue
            b_sb = d_codes[b].get("superseded_by", []) or []
            if code not in b_sb:
                report.add_error(
                    file="d_codes.md", rule="bidirectional_pointer",
                    target=code,
                    message=(
                        f"{code}.supersedes contains {b}, but {b}.superseded_by "
                        f"does not contain {code} (currently: {b_sb})"
                    ),
                )

        for a in superseded_by:
            if a not in d_codes:
                report.add_warning(
                    file="d_codes.md", rule="bidirectional_pointer",
                    target=code,
                    message=f"superseded_by contains {a!r} which is not in this file",
                )
                continue
            a_s = d_codes[a].get("supersedes", []) or []
            if code not in a_s:
                report.add_error(
                    file="d_codes.md", rule="bidirectional_pointer",
                    target=code,
                    message=(
                        f"{code}.superseded_by contains {a}, but {a}.supersedes "
                        f"does not contain {code} (currently: {a_s})"
                    ),
                )

        # 3. Dependency resolution
        for dep in depends_on:
            # Three valid forms:
            #   (a) D-code identifier present in this file: "D310"
            #   (b) module path: "src/agents/base.py" or "main.py"
            #   (c) module:line ref: "main.py:1234"
            file_part = dep.split(":")[0] if ":" in dep else dep
            is_path = (file_part.startswith(("src/", "scripts/", "tests/"))
                       or file_part == "main.py"
                       or file_part.endswith(".py"))
            if is_path:
                fp = REPO / file_part
                if not fp.exists():
                    report.add_warning(
                        file="d_codes.md", rule="dependency_resolution",
                        target=code,
                        message=f"depends_on {dep!r} -- file {file_part} does not exist",
                    )
            elif dep in valid_keys:
                continue
            else:
                report.add_warning(
                    file="d_codes.md", rule="dependency_resolution",
                    target=code,
                    message=(
                        f"depends_on {dep!r} -- not a known D-code in this file "
                        f"and not a module:path reference"
                    ),
                )

        # 4. Category mandatory for ACTIVE
        if status == "ACTIVE" and not category:
            report.add_error(
                file="d_codes.md", rule="category_mandatory", target=code,
                message="ACTIVE codes must declare a category (alpha/safety/observability/data)",
            )

        # 5. Doc references must exist
        for doc_num in docs:
            doc_glob = list(OPUS_DIR.glob(f"{doc_num}_*.md"))
            if not doc_glob:
                report.add_warning(
                    file="d_codes.md", rule="doc_reference", target=code,
                    message=f"docs entry {doc_num!r} -- no matching file at docs/research-log/{doc_num}_*.md",
                )

        # 6. No orphan ACTIVE codes (lightweight check — only flags
        #    codes that appear nowhere; not a guarantee they're load-
        #    bearing). We check src/ + scripts/ + main.py + the d_codes
        #    inter-references + this very file (which counts as a
        #    reference). Schema-example placeholders are skipped.
        if status == "ACTIVE" and not _is_example_identifier(code):
            referenced_elsewhere = any(
                code in (d.get("depends_on") or [])
                for k, d in d_codes.items() if k != code
            )
            if not referenced_elsewhere:
                # Check src text references
                found = _grep_code_in_repo(code)
                if not found:
                    report.add_warning(
                        file="d_codes.md", rule="orphan_active", target=code,
                        message=(
                            f"ACTIVE code {code} not referenced in src/, scripts/, "
                            f"main.py, or another D-code's depends_on. Delete candidate?"
                        ),
                    )


def _is_example_identifier(name: str) -> bool:
    """Schema example placeholders are skipped by orphan checks."""
    name_upper = name.upper()
    return (name_upper.startswith("DXXX")
            or name_upper.startswith("EXAMPLE_")
            or "_EXAMPLE" in name_upper
            or "_SCHEMA" in name_upper)


def _grep_code_in_repo(code: str) -> bool:
    """Cheap check: does the literal D-code identifier appear in any
    .py file under src/ or scripts/ or main.py? Used by orphan check."""
    # Accept either D310 or D310_5 (drop underscore variant for grep)
    needles = {code, code.replace("_", ".")}
    py_files: list[Path] = []
    for d in [REPO / "src", REPO / "scripts"]:
        if d.exists():
            py_files.extend(d.rglob("*.py"))
    main = REPO / "main.py"
    if main.exists():
        py_files.append(main)
    for f in py_files[:500]:  # cap to avoid runaway on huge repos
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if any(n in text for n in needles):
            return True
    return False


# ── invariants: experiments.md ──────────────────────────────────────


def lint_experiments(experiments: dict[str, dict], report: LintReport) -> None:
    """Apply 2 invariants."""
    report.n_experiments = len(experiments)
    today = datetime.now(timezone.utc).date()

    for name, entry in experiments.items():
        status = entry.get("status", "")
        decision_date = entry.get("decision_date", "")
        target_date = entry.get("target_date", "")

        # 1. decision_date mandatory for SHADOW/LIVE
        if status in ("SHADOW", "LIVE") and not decision_date:
            report.add_error(
                file="experiments.md", rule="decision_date_required",
                target=name,
                message=f"status={status} but decision_date missing",
            )

        # 2. SHADOW with past decision_date and no verdict = sunk-cost alarm
        if status == "SHADOW" and decision_date:
            try:
                dt = datetime.strptime(decision_date, "%Y-%m-%d").date()
                if dt < today:
                    report.add_error(
                        file="experiments.md", rule="shadow_past_decision_date",
                        target=name,
                        message=(
                            f"SHADOW with decision_date={decision_date} (in the past). "
                            f"This is the sunk-cost-accumulation alarm. Either set "
                            f"status to PASS/FAIL/DELETED and document, OR extend "
                            f"the decision_date with explicit reasoning in the doc "
                            f"that bumps it."
                        ),
                    )
            except ValueError:
                report.add_warning(
                    file="experiments.md", rule="decision_date_format",
                    target=name,
                    message=f"decision_date={decision_date!r} not in YYYY-MM-DD format",
                )

        # Aspirational target_date sanity (within 6 months)
        if target_date and target_date not in ("", "post_provider_setup",
                                                  "post-T2-stabilization (~Q3 2026)",
                                                  "2026-Q3", "2026-Q4"):
            try:
                dt = datetime.strptime(target_date, "%Y-%m-%d").date()
                days = (dt - today).days
                if days > 365:
                    report.add_warning(
                        file="experiments.md", rule="target_date_sanity",
                        target=name,
                        message=f"target_date {target_date} is >1y out; consider re-scoping",
                    )
            except ValueError:
                pass  # non-date strings (quarters) are OK


# ── invariants: backlog.md ──────────────────────────────────────────


def lint_backlog(backlog: dict[str, dict], report: LintReport) -> None:
    """Apply 2 invariants."""
    report.n_backlog = len(backlog)

    # 1. blocks/blocked_by bidirectional agreement
    for name, entry in backlog.items():
        blocks = entry.get("blocks", []) or []
        blocked_by = entry.get("blocked_by", []) or []

        for b in blocks:
            if b in backlog:
                b_bb = backlog[b].get("blocked_by", []) or []
                if name not in b_bb:
                    report.add_warning(
                        file="backlog.md", rule="bidirectional_pointer",
                        target=name,
                        message=(
                            f"{name}.blocks contains {b}, but {b}.blocked_by "
                            f"does not contain {name} (currently: {b_bb})"
                        ),
                    )
            # Items can reference top-level milestone names like "T3" that
            # aren't backlog entries -- those are fine (no agreement check).

        for a in blocked_by:
            if a in backlog:
                a_b = backlog[a].get("blocks", []) or []
                if name not in a_b:
                    report.add_warning(
                        file="backlog.md", rule="bidirectional_pointer",
                        target=name,
                        message=(
                            f"{name}.blocked_by contains {a}, but {a}.blocks "
                            f"does not contain {name} (currently: {a_b})"
                        ),
                    )

    # 2. priority values must be valid
    valid_priorities = {"S", "1", "2", "3"}
    for name, entry in backlog.items():
        p = entry.get("priority", "")
        if p and p not in valid_priorities:
            report.add_error(
                file="backlog.md", rule="priority_valid", target=name,
                message=f"priority={p!r} must be one of {sorted(valid_priorities)}",
            )


# ── derived artifacts ──────────────────────────────────────────────


def emit_by_category(d_codes: dict[str, dict], out_path: Path) -> None:
    cats: dict[str, list[str]] = {}
    for code, entry in d_codes.items():
        if entry.get("status") != "ACTIVE":
            continue
        cat = entry.get("category", "uncategorized")
        cats.setdefault(cat, []).append(code)
    lines = [
        "# _by_category.md (derived — do not edit by hand)",
        "",
        "Generated by `_linter.py`. Each commit regenerates from `d_codes.md`.",
        f"Generated at: {datetime.now(timezone.utc).isoformat()}",
        "",
    ]
    for cat in sorted(cats.keys()):
        lines.append(f"## {cat} ({len(cats[cat])} ACTIVE codes)")
        lines.append("")
        for code in sorted(cats[cat]):
            entry = d_codes[code]
            lines.append(f"- **{code}** ({entry.get('label', '?')}): "
                         f"{entry.get('one_line', '?')}")
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def emit_by_status(d_codes: dict[str, dict], out_path: Path) -> None:
    by_status: dict[str, list[str]] = {}
    for code, entry in d_codes.items():
        status = entry.get("status", "UNKNOWN")
        by_status.setdefault(status, []).append(code)
    lines = [
        "# _by_status.md (derived — do not edit by hand)",
        "",
        "Generated by `_linter.py`. DEPRECATED + REVERTED are delete-candidates / lessons-learned.",
        f"Generated at: {datetime.now(timezone.utc).isoformat()}",
        "",
    ]
    for status in ["ACTIVE", "SHADOW", "FILED", "DEPRECATED", "REVERTED", "UNKNOWN"]:
        codes = by_status.get(status, [])
        if not codes:
            continue
        lines.append(f"## {status} ({len(codes)} codes)")
        lines.append("")
        for code in sorted(codes):
            entry = d_codes[code]
            extra = ""
            if status == "DEPRECATED":
                sb = entry.get("superseded_by", [])
                extra = f" → superseded by {', '.join(sb)}" if sb else ""
            lines.append(f"- **{code}** ({entry.get('label', '?')}){extra}: "
                         f"{entry.get('one_line', '?')}")
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def emit_dependency_graph(d_codes: dict[str, dict], out_path: Path) -> None:
    """Emit Graphviz .dot file. Nodes = D-codes, edges = depends_on
    (D-code → D-code only; module:path references skipped for visual)."""
    lines = [
        "// _dependency_graph.dot (derived -- do not edit by hand)",
        f"// Generated at: {datetime.now(timezone.utc).isoformat()}",
        "// Render: dot -Tsvg _dependency_graph.dot > _dependency_graph.svg",
        "digraph d_codes {",
        "  rankdir=LR;",
        "  node [shape=box, style=rounded, fontname=\"monospace\"];",
        "",
    ]
    # Color by category / status
    color = {
        "alpha": "lightblue",
        "safety": "lightcoral",
        "observability": "lightyellow",
        "data": "lightgreen",
    }
    for code, entry in d_codes.items():
        status = entry.get("status", "")
        cat = entry.get("category", "")
        fill = color.get(cat, "white")
        style = "filled,rounded"
        if status in ("DEPRECATED", "REVERTED"):
            style = "filled,rounded,dashed"
            fill = "lightgray"
        elif status == "FILED":
            style = "filled,rounded,dotted"
            fill = "white"
        label = f"{code}\\n{entry.get('label', '')[:30]}"
        lines.append(f'  "{code}" [label="{label}", fillcolor={fill}, style="{style}"];')
    lines.append("")
    for code, entry in d_codes.items():
        for dep in entry.get("depends_on", []) or []:
            if ":" in dep:
                continue  # skip module:path refs in graph
            if dep in d_codes:
                lines.append(f'  "{code}" -> "{dep}";')
    lines.append("}")
    out_path.write_text("\n".join(lines), encoding="utf-8")


# ── main ────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--emit-derived", action="store_true",
                    help="Also write _by_category.md, _by_status.md, _dependency_graph.dot")
    ap.add_argument("--strict", action="store_true",
                    help="Warnings become errors (exit 1 if any)")
    args = ap.parse_args()

    print(f"SYSTEM_MAP linter -- repo: {REPO}", flush=True)

    # Parse all three files
    d_codes = extract_toml_blocks(D_CODES_FILE)
    experiments = extract_toml_blocks(EXPERIMENTS_FILE)
    backlog = extract_toml_blocks(BACKLOG_FILE)

    report = LintReport()
    lint_d_codes(d_codes, report)
    lint_experiments(experiments, report)
    lint_backlog(backlog, report)

    # Emit derived artifacts if requested
    if args.emit_derived:
        emit_by_category(d_codes, SM_DIR / "_by_category.md")
        emit_by_status(d_codes, SM_DIR / "_by_status.md")
        emit_dependency_graph(d_codes, SM_DIR / "_dependency_graph.dot")
        print("Wrote derived artifacts:", flush=True)
        print(f"  {SM_DIR / '_by_category.md'}", flush=True)
        print(f"  {SM_DIR / '_by_status.md'}", flush=True)
        print(f"  {SM_DIR / '_dependency_graph.dot'}", flush=True)

    # Print summary
    print(f"\nParsed: {report.n_d_codes} d_codes, "
          f"{report.n_experiments} experiments, "
          f"{report.n_backlog} backlog items",
          flush=True)
    print(f"Errors: {len(report.errors)}, Warnings: {len(report.warnings)}\n",
          flush=True)

    for v in report.errors:
        print(v, flush=True)
    for v in report.warnings:
        print(v, flush=True)

    if report.errors:
        return 1
    if args.strict and report.warnings:
        return 1
    if report.warnings:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
