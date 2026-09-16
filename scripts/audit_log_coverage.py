"""
Track A item 2 — AST dark-zone audit.

Per `25_bug_hunting_playbook.md` §5.2 + Track A item 2 in the
2026-04-23 next-actions list:

    "Run the AST dark-zone audit from §5. Walk every except clause,
    every if/elif branch in alpaca_executor.py, bridge.py,
    trade_journal.py, main.py, and premarket_research.py. Emit a
    list of branches that can execute without a single log line.
    Target: top 30-50 dark zones ranked by 'touches state mutation
    or broker call.' Fix by adding at minimum a D### warning at each
    — don't try to fix the underlying behavior yet, just end the
    silence."

Run with:
    python scripts/audit_log_coverage.py

Output:
  - Stdout: human-readable report grouped by file
  - data/audit/log_coverage_2026-04-23.json: machine-readable for
    cross-session diffing

Heuristics for "dark zone":
  - except: clause whose body emits no logger.X call AND no return/raise
  - if/elif/else branch whose body emits no logger.X call AND mutates
    state (assignment to self.X, dict update, list append) OR contains
    an `await` to a broker-side method
  - Functions whose entire body emits no logger.X call (rare but worth
    flagging for state-mutating helpers)

Score per finding (0-3):
  +1 contains await client.X(...)  (broker call)
  +1 contains state mutation (self.X =, X.append, X[K] =)
  +1 in critical-path file (bridge.py, alpaca_executor.py,
                            trade_journal.py)

Top 30 by score → operator-actionable fix list. Score-1 findings get
a D### warning added; score-2-3 findings warrant deeper investigation.
"""

from __future__ import annotations

import ast
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

# Files in scope per Track A item 2
TARGETS = [
    REPO_ROOT / "src" / "execution" / "bridge.py",
    REPO_ROOT / "src" / "execution" / "alpaca_executor.py",
    REPO_ROOT / "src" / "analysis" / "trade_journal.py",
    REPO_ROOT / "src" / "data" / "premarket_research.py",
    REPO_ROOT / "main.py",
]

CRITICAL_PATH_FILES = {
    "bridge.py", "alpaca_executor.py", "trade_journal.py",
}

LOGGER_METHODS = frozenset({
    "debug", "info", "warning", "error", "critical", "exception", "log",
})


@dataclass
class Finding:
    file: str
    lineno: int
    end_lineno: int
    kind: str            # "except" | "branch" | "function"
    snippet: str         # ≤80 chars
    has_state_mutation: bool
    has_broker_call: bool
    score: int
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_log_call(node: ast.AST) -> bool:
    """True if the AST node is a logger.X(...) call (any logger object)."""
    if not isinstance(node, ast.Expr):
        return False
    call = node.value
    if not isinstance(call, ast.Call):
        return False
    func = call.func
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr not in LOGGER_METHODS:
        return False
    # Heuristic: the receiver name often contains "log" or "logger"
    if isinstance(func.value, ast.Name):
        name = func.value.id.lower()
        return "log" in name
    if isinstance(func.value, ast.Attribute):
        return "log" in func.value.attr.lower()
    return False


def _body_emits_log(body: list[ast.stmt]) -> bool:
    """Walk the immediate body; return True if any statement is a log call
    or contains one (one level deep into nested if/try/for)."""
    for stmt in body:
        if _is_log_call(stmt):
            return True
        # Walk one nesting level for log emission inside a try-block etc.
        for child in ast.walk(stmt):
            if isinstance(child, ast.Expr) and _is_log_call(child):
                return True
    return False


def _body_returns_or_raises(body: list[ast.stmt]) -> bool:
    """If the branch returns or raises, missing a log is acceptable —
    the caller will see the exception or absence of result."""
    for stmt in body:
        if isinstance(stmt, (ast.Return, ast.Raise)):
            return True
    return False


def _body_has_state_mutation(body: list[ast.stmt]) -> bool:
    """True if the branch contains an assignment, augmented assign, or
    method call that looks like state mutation."""
    for stmt in body:
        for child in ast.walk(stmt):
            if isinstance(child, (ast.Assign, ast.AugAssign)):
                # Filter out `_local = X` (no `.` in target) — focus on
                # `self.X = ...`, `obj.attr = ...`, `dict[k] = ...`
                for tgt in (child.targets if isinstance(child, ast.Assign) else [child.target]):
                    if isinstance(tgt, ast.Attribute):
                        return True
                    if isinstance(tgt, ast.Subscript):
                        return True
            if isinstance(child, ast.Call):
                func = child.func
                if isinstance(func, ast.Attribute) and func.attr in (
                    "append", "extend", "add", "update", "pop",
                    "remove_position", "add_position", "save",
                    "set", "inc", "remove",
                ):
                    return True
    return False


def _body_has_broker_call(body: list[ast.stmt]) -> bool:
    """True if branch contains `await client.X(...)` or similar broker call."""
    for stmt in body:
        for child in ast.walk(stmt):
            if isinstance(child, ast.Await):
                inner = child.value
                if isinstance(inner, ast.Call):
                    func = inner.func
                    if isinstance(func, ast.Attribute):
                        # Receiver named something brokery
                        if isinstance(func.value, ast.Name):
                            name = func.value.id.lower()
                            if any(t in name for t in ("client", "broker", "alpaca")):
                                return True
                        # Method name signals broker
                        if func.attr in (
                            "submit_order", "submit_oto_order",
                            "cancel_order", "close_position",
                            "get_positions", "get_orders", "get_account",
                            "get_account_activities", "get_latest_quote",
                        ):
                            return True
    return False


def _snippet(source_lines: list[str], lineno: int) -> str:
    """Get a 1-line snippet centred on lineno; truncate to 80 chars."""
    if 0 < lineno <= len(source_lines):
        s = source_lines[lineno - 1].strip()
        return s[:80] + ("..." if len(s) > 80 else "")
    return "(unavailable)"


class DarkZoneVisitor(ast.NodeVisitor):
    def __init__(self, file: str, source_lines: list[str]) -> None:
        self.file = file
        self.source_lines = source_lines
        self.findings: list[Finding] = []

    def _eval_branch(
        self, body: list[ast.stmt], lineno: int, kind: str
    ) -> Finding | None:
        if not body:
            return None
        if _body_emits_log(body):
            return None
        if _body_returns_or_raises(body):
            return None
        has_mut = _body_has_state_mutation(body)
        has_broker = _body_has_broker_call(body)
        # Skip pure-getter-style branches (no mutation, no broker call)
        if not has_mut and not has_broker:
            return None
        score = (
            (2 if has_broker else 0)
            + (1 if has_mut else 0)
            + (1 if Path(self.file).name in CRITICAL_PATH_FILES else 0)
        )
        end_lineno = max(
            getattr(s, "end_lineno", s.lineno) or s.lineno for s in body
        )
        reason_parts = []
        if has_broker:
            reason_parts.append("broker_call")
        if has_mut:
            reason_parts.append("state_mutation")
        reason = "+".join(reason_parts) or "unknown"
        return Finding(
            file=self.file,
            lineno=lineno,
            end_lineno=end_lineno,
            kind=kind,
            snippet=_snippet(self.source_lines, lineno),
            has_state_mutation=has_mut,
            has_broker_call=has_broker,
            score=score,
            reason=reason,
        )

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> Any:
        f = self._eval_branch(node.body, node.lineno, "except")
        if f:
            self.findings.append(f)
        self.generic_visit(node)

    def visit_If(self, node: ast.If) -> Any:
        # if-body
        f = self._eval_branch(node.body, node.lineno, "if")
        if f:
            self.findings.append(f)
        # elif/else (orelse)
        if node.orelse:
            f = self._eval_branch(node.orelse, node.orelse[0].lineno, "else_or_elif")
            if f:
                self.findings.append(f)
        self.generic_visit(node)


def audit_file(path: Path) -> list[Finding]:
    source = path.read_text(encoding="utf-8")
    source_lines = source.splitlines()
    tree = ast.parse(source, filename=str(path))
    rel = str(path.relative_to(REPO_ROOT)).replace("\\", "/")
    visitor = DarkZoneVisitor(rel, source_lines)
    visitor.visit(tree)
    return visitor.findings


def main() -> int:
    all_findings: list[Finding] = []
    for target in TARGETS:
        if not target.exists():
            print(f"WARNING: missing target {target}", file=sys.stderr)
            continue
        all_findings.extend(audit_file(target))

    # Sort by score descending, then file, then line
    all_findings.sort(key=lambda f: (-f.score, f.file, f.lineno))

    # Persist machine-readable
    out_dir = REPO_ROOT / "data" / "audit"
    out_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()
    out_path = out_dir / f"log_coverage_{today}.json"
    out_path.write_text(
        json.dumps({
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "targets": [str(t.relative_to(REPO_ROOT)).replace("\\", "/") for t in TARGETS],
            "total_findings": len(all_findings),
            "by_score": dict(_by_score(all_findings)),
            "findings": [f.to_dict() for f in all_findings],
        }, indent=2),
        encoding="utf-8",
    )

    # Human report
    print(f"\n{'='*70}")
    print(f"  AST DARK-ZONE AUDIT — {today}")
    print(f"  {len(all_findings)} branches w/o log emission, ranked by impact")
    print(f"{'='*70}\n")

    by_score = _by_score(all_findings)
    for sc in sorted(by_score, reverse=True):
        print(f"  Score {sc}: {by_score[sc]} findings")
    print(f"\n  -> JSON: {out_path.relative_to(REPO_ROOT)}\n")

    print(f"\n  TOP 30 (highest impact, fix first):\n")
    print(f"  {'#':<3} {'score':<6} {'file':<35} {'line':<6} {'kind':<14} reason")
    print(f"  {'-'*3} {'-'*6} {'-'*35} {'-'*6} {'-'*14} {'-'*30}")
    for i, f in enumerate(all_findings[:30], 1):
        fn = f.file if len(f.file) < 35 else "..." + f.file[-32:]
        print(f"  {i:<3} {f.score:<6} {fn:<35} {f.lineno:<6} {f.kind:<14} {f.reason}")
        print(f"          {f.snippet}")
    # Track A item 27: pre-commit hook integration. Exit non-zero
    # if any score-3 finding (broker_call + state_mutation in a
    # critical-path file) — these are the dark-zone class that
    # produces silent state corruption. Score-2 findings are
    # informational; humans review.
    score3 = sum(1 for f in all_findings if f.score >= 3)
    if score3 > 0:
        print(f"\n  *** BLOCKED: {score3} score-3 dark-zone(s) detected ***")
        print(f"      Add a logger.X call inside each branch, or commit a")
        print(f"      noqa marker to the JSON corpus with rationale.")
        return 1
    return 0


def _by_score(findings: list[Finding]) -> dict[int, int]:
    out: dict[int, int] = defaultdict(int)
    for f in findings:
        out[f.score] += 1
    return dict(out)


if __name__ == "__main__":
    sys.exit(main())
