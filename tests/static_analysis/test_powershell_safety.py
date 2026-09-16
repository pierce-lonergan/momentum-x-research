"""Detect two known-fatal PowerShell patterns from the watchdog (D218 fixes).

1. `[datetime]::Parse(<utc_string>)` — interprets ISO UTC timestamps as LOCAL time.
   Result: a 4-hour offset (EDT = UTC-4). The watchdog computed heartbeat age as
   ~14,400 seconds stale instead of ~30 seconds and killed the healthy process.
   Fix: use `[DateTimeOffset]::Parse(...)` (preserves offset) OR pass
   `[Globalization.DateTimeStyles]::AssumeUniversal` to `[datetime]::Parse`.

2. `2>&1 | ForEach-Object { ... }` (and similar) — the buffer-fillable pipe pattern
   that froze the daily trading process for 10+ hours. PowerShell's pipe buffer
   fills when the producer (Python stderr) outpaces the consumer (Add-Content),
   blocking the writer.
   Fix: `2>> $LogFile` (direct redirect), or `Start-Process -RedirectStandardOutput`.

Whitelist markers (PowerShell uses `#` for line comments):
    [datetime]::Parse($x)  # noqa: ps-datetime-utc
    & python -m main 2>&1 | Out-File $log  # noqa: ps-pipe-deadlock
"""

from __future__ import annotations

import re

from tests.static_analysis._ast_helpers import (
    Violation,
    assert_no_new_violations,
    collect_violations,
    get_snippet,
    iter_powershell_files,
    parse_whitelist,
    rel_path,
)

KIND_DATETIME = "ps-datetime-utc"
KIND_PIPE = "ps-pipe-deadlock"

# ── Patterns ────────────────────────────────────────────────────────────

_DATETIME_PARSE_RE = re.compile(
    r"\[(?:System\.)?DateTime\]::Parse\s*\(",
    re.IGNORECASE,
)
_DATETIME_SAFE_RE = re.compile(r"AssumeUniversal", re.IGNORECASE)

# Pipe to a consumer that buffers (the deadlock-prone ones)
# Note: `Select-Object` and `Sort-Object` also buffer, but they're rarely paired
# with `2>&1` for trading code. Conservative pattern matches what we've actually hit.
_PIPE_DEADLOCK_RE = re.compile(
    r"\b2>&1\s*\|\s*(?:ForEach-Object|Out-File|Tee-Object|Where-Object|Add-Content|%\s|\?\s)",
    re.IGNORECASE,
)


# ── Detectors ───────────────────────────────────────────────────────────


def _scan_datetime(file_path: str, source: str) -> list[Violation]:
    """Flag `[datetime]::Parse(...)` without `AssumeUniversal` within ±3 lines."""
    violations: list[Violation] = []
    lines = source.splitlines()
    for line_num, line in enumerate(lines, start=1):
        if not _DATETIME_PARSE_RE.search(line):
            continue
        # Look in a small window for AssumeUniversal (it might be on next line)
        window_start = max(0, line_num - 4)
        window_end = min(len(lines), line_num + 3)
        window_text = "\n".join(lines[window_start:window_end])
        if _DATETIME_SAFE_RE.search(window_text):
            continue
        violations.append(
            Violation(
                path=file_path,
                line=line_num,
                col=line.find("[") if "[" in line else 0,
                kind=KIND_DATETIME,
                snippet=get_snippet(source, line_num),
            )
        )
    return violations


def _scan_pipe_deadlock(file_path: str, source: str) -> list[Violation]:
    """Flag `2>&1 | <buffering-cmdlet>` patterns."""
    violations: list[Violation] = []
    for line_num, line in enumerate(source.splitlines(), start=1):
        m = _PIPE_DEADLOCK_RE.search(line)
        if not m:
            continue
        violations.append(
            Violation(
                path=file_path,
                line=line_num,
                col=m.start(),
                kind=KIND_PIPE,
                snippet=get_snippet(source, line_num),
            )
        )
    return violations


# ── Public scan API ─────────────────────────────────────────────────────


def scan(kind: str) -> list[Violation]:
    """Public entry for _update_baseline.py — multiplexes by kind."""
    detector = {
        KIND_DATETIME: _scan_datetime,
        KIND_PIPE: _scan_pipe_deadlock,
    }[kind]

    out: list[Violation] = []
    for path in iter_powershell_files():
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            try:
                source = path.read_text(encoding="utf-8-sig")  # PowerShell often writes BOM
            except Exception:
                continue
        rel = rel_path(path)
        file_violations = detector(rel, source)
        if not file_violations:
            continue
        whitelist = parse_whitelist(source, kind)
        out.extend(v for v in file_violations if v.line not in whitelist)
    return out


# Adapter so collect_violations() can be reused (matches Python visitor signature)
def visit_file_datetime(file_path: str, source: str) -> list[Violation]:
    file_violations = _scan_datetime(file_path, source)
    whitelist = parse_whitelist(source, KIND_DATETIME)
    return [v for v in file_violations if v.line not in whitelist]


def visit_file_pipe(file_path: str, source: str) -> list[Violation]:
    file_violations = _scan_pipe_deadlock(file_path, source)
    whitelist = parse_whitelist(source, KIND_PIPE)
    return [v for v in file_violations if v.line not in whitelist]


# ── Pytest tests ────────────────────────────────────────────────────────


def test_no_new_ps_datetime_utc() -> None:
    """Fail if any .ps1 has new `[datetime]::Parse` calls without
    `AssumeUniversal` (causes 4-hour timezone offset)."""
    assert_no_new_violations(KIND_DATETIME, scan(KIND_DATETIME))


def test_no_new_ps_pipe_deadlock() -> None:
    """Fail if any .ps1 has new `2>&1 | <buffering-cmdlet>` patterns
    (deadlocks when stderr fills the pipe buffer)."""
    assert_no_new_violations(KIND_PIPE, scan(KIND_PIPE))
