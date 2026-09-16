"""Pin the noqa-line-position convention.

Discovered the hard way: when a `# noqa: silent-handler` marker is placed
on the body line (`pass`) instead of the `except` line, the violation is
NOT suppressed because `parse_whitelist` is line-exact and the AST
emits `node.lineno` from the ExceptHandler itself (the `except` line),
not from the body.

This test:
  1. Documents the convention as runnable code (in-memory synthetic source).
  2. Catches a future regression where node.lineno semantics shift, by
     asserting the exact line where the violation is reported.
  3. Provides fast feedback for new contributors who hit this gotcha:
     the failure message points at the README's whitelist convention table.
"""
from __future__ import annotations

from tests.static_analysis._ast_helpers import parse_whitelist


# ── silent-handler — the canonical "wrong line" footgun ─────────────


_SOURCE_NOQA_ON_BODY = (
    "import asyncio\n"
    "async def f(stop):\n"
    "    try:\n"
    "        await asyncio.wait_for(stop.wait(), timeout=1)\n"
    "    except asyncio.TimeoutError:\n"
    "        pass  # noqa: silent-handler\n"
)

_SOURCE_NOQA_ON_EXCEPT = (
    "import asyncio\n"
    "async def f(stop):\n"
    "    try:\n"
    "        await asyncio.wait_for(stop.wait(), timeout=1)\n"
    "    except asyncio.TimeoutError:  # noqa: silent-handler\n"
    "        pass\n"
)


def _silent_violations_after_whitelist(source: str) -> list:
    """Run the silent-handler visitor + whitelist pipeline on synthetic source."""
    from tests.static_analysis.test_silent_handlers import visit_file
    raw = visit_file("synthetic.py", source)
    suppressed = parse_whitelist(source, "silent-handler")
    return [v for v in raw if v.line not in suppressed]


def test_violation_is_reported_on_except_line() -> None:
    """The AST emits ExceptHandler.lineno from the `except` keyword.

    If a future Python version changes this (or the visitor is rewritten to
    use the body line), this test fails LOUDLY — preventing silent drift.
    """
    from tests.static_analysis.test_silent_handlers import visit_file
    raw = visit_file("synthetic.py", _SOURCE_NOQA_ON_BODY)
    assert len(raw) == 1, (
        f"Expected exactly one raw silent-handler violation, got {len(raw)}: {raw}"
    )
    assert raw[0].line == 5, (
        f"Violation MUST be reported on the `except` line (5), got line {raw[0].line}. "
        "If this changed, update the README §noqa-line-position convention table."
    )


def test_noqa_on_body_line_DOES_NOT_suppress() -> None:
    """The footgun: marker on `pass` line does nothing.

    This documents the WRONG pattern. If this test ever passes (i.e. the
    body-line marker DOES suppress), the convention has changed and the
    README plus all existing whitelists need a coordinated update.
    """
    after = _silent_violations_after_whitelist(_SOURCE_NOQA_ON_BODY)
    assert len(after) == 1, (
        "noqa on body line MUST NOT suppress (per the README convention). "
        "If this test fails, parse_whitelist semantics have changed — "
        "ALL existing `# noqa: silent-handler` markers in the codebase "
        "now need their positions audited."
    )


def test_noqa_on_except_line_DOES_suppress() -> None:
    """The right pattern: marker on the `except` line suppresses cleanly."""
    after = _silent_violations_after_whitelist(_SOURCE_NOQA_ON_EXCEPT)
    assert after == [], (
        f"noqa on except line MUST suppress, but got: {after}. "
        "If this test fails, the visitor is no longer reporting on the "
        "except line — the README and all whitelists need updating."
    )


# ── frozen-mutate — single-line statement, marker is co-located ─────


def test_frozen_mutate_noqa_on_assignment_line_suppresses() -> None:
    """Sanity: the frozen-mutate convention (marker on the `=` line)
    is the natural single-line pattern. This pins it."""
    from tests.static_analysis.test_frozen_mutations import visit_file
    source = (
        "from src.core.models import CandidateStock\n"
        "def f(c):\n"
        "    c.float_shares = 999  # noqa: frozen-mutate\n"
    )
    raw = visit_file("synthetic.py", source)
    suppressed = parse_whitelist(source, "frozen-mutate")
    after = [v for v in raw if v.line not in suppressed]
    assert after == [], (
        f"frozen-mutate noqa on assignment line should suppress, got {after}"
    )


# ── async-leak — single-line constructor, marker is co-located ──────


def test_async_leak_noqa_on_constructor_line_suppresses() -> None:
    """Sanity: the async-leak convention (marker on the constructor call)."""
    from tests.static_analysis.test_async_lifecycle import visit_file
    source = (
        "import httpx\n"
        "def f():\n"
        "    c = httpx.AsyncClient(timeout=3)  # noqa: async-leak\n"
        "    return c\n"
    )
    raw = visit_file("synthetic.py", source)
    suppressed = parse_whitelist(source, "async-leak")
    after = [v for v in raw if v.line not in suppressed]
    assert after == [], (
        f"async-leak noqa on constructor line should suppress, got {after}"
    )


# ── relative-path — single-line Path call, marker is co-located ─────


def test_relative_path_noqa_on_call_line_suppresses() -> None:
    """Sanity: the relative-path convention (marker on the Path() call)."""
    from tests.static_analysis.test_relative_paths import visit_file
    source = (
        "from pathlib import Path\n"
        "def f():\n"
        "    return Path('data/foo.json')  # noqa: relative-path\n"
    )
    raw = visit_file("synthetic.py", source)
    suppressed = parse_whitelist(source, "relative-path")
    after = [v for v in raw if v.line not in suppressed]
    assert after == [], (
        f"relative-path noqa on call line should suppress, got {after}"
    )
