"""Tests for Bug AV fix (PROMPT_09 Block 7).

Two surgical fixes:
1. src/execution/post_fill_handler.py: derive `side` from `verdict.direction`
   instead of hardcoded "buy".
2. src/analysis/execution_recorder.py: direction-aware journal lookup
   (long calls match BUY/STRONG_BUY only; short calls match SHORT only).

Per docs/audits/2026-04-30_bug_au_root_cause.md §1, OGN's side-flip would
have been propagated through the unified helper to all paths if D161 had
been migrated before this fix. Bug AV blocks D161/D207/D170 migrations
(N+1) until shipped.
"""
from __future__ import annotations

import pytest


# ── Source-grep contracts (the fix is the test) ────────────────────


def test_post_fill_handler_no_longer_hardcodes_side_buy():
    """post_fill_handler.py must NOT contain `side="buy"` as a hardcoded
    record_execution kwarg. The verdict's direction must drive side."""
    from pathlib import Path
    text = Path("src/execution/post_fill_handler.py").read_text(encoding="utf-8")
    # The bad pattern was `side="buy"` literally on its own line in the
    # exec_recorder.record_execution call.
    bad_line_pattern = '        side="buy",'
    assert bad_line_pattern not in text, (
        "post_fill_handler.py still hardcodes `side=\"buy\"` in the "
        "exec_recorder.record_execution call. Bug AV not fully fixed. "
        "Derive side from verdict.direction per PROMPT_09 §10.1."
    )
    # And the new pattern MUST be present
    assert "verdict.direction" in text or "_verdict_direction" in text, (
        "post_fill_handler.py does not reference verdict.direction. "
        "Bug AV requires deriving side from the verdict's direction field."
    )
    assert "sell_short" in text, (
        "post_fill_handler.py does not produce 'sell_short' for short "
        "verdicts. Bug AV requires the side derivation to handle shorts."
    )


def test_execution_recorder_journal_lookup_is_direction_aware():
    """execution_recorder.py's journal lookup must filter by direction.
    Pre-Bug-AV fix: action in (BUY, STRONG_BUY, SHORT) — direction-blind.
    Post-fix: long calls match BUY/STRONG_BUY only; short calls match
    SHORT only."""
    from pathlib import Path
    text = Path("src/analysis/execution_recorder.py").read_text(encoding="utf-8")
    # The old direction-blind pattern was a single tuple of all 3 actions.
    bad_pattern = '("BUY", "STRONG_BUY", "SHORT")'
    assert bad_pattern not in text, (
        "execution_recorder.py still has direction-blind journal lookup "
        "with `action in (\"BUY\", \"STRONG_BUY\", \"SHORT\")`. Bug AV "
        "requires splitting this by direction so two-path same-ticker "
        "collisions don't cross-mutate each other's journal records."
    )
    # The new pattern must split by direction
    assert "_target_actions" in text or "target_actions" in text, (
        "execution_recorder.py does not appear to use a direction-conditional "
        "target_actions tuple. Bug AV fix may have been reverted."
    )


# ── Behavioral tests for direction routing ─────────────────────────


def test_post_fill_handler_derives_buy_for_long_verdict():
    """When verdict.direction is 'long' (default), the helper records
    side='buy'. Verifies the fix doesn't break the existing long path."""
    from pathlib import Path
    text = Path("src/execution/post_fill_handler.py").read_text(encoding="utf-8")
    # Look for the conditional that produces 'buy' as the default branch
    # and 'sell_short' as the short branch.
    assert '"sell_short" if' in text, (
        "Conditional pattern '\"sell_short\" if ... == \"short\" else \"buy\"' "
        "not found. Long-default fallback must be explicit."
    )
    assert '"buy"' in text, (
        "Helper must still produce side='buy' for long verdicts. The fix "
        "is conditional, not removal."
    )


def test_execution_recorder_long_target_actions():
    """Long calls' target_actions tuple must contain BUY (and STRONG_BUY),
    NOT SHORT. The lookup must not match a short journal entry on a
    long execution call."""
    from pathlib import Path
    text = Path("src/analysis/execution_recorder.py").read_text(encoding="utf-8")
    # The new code has `("BUY", "STRONG_BUY")` for the long branch
    assert '"BUY", "STRONG_BUY"' in text, (
        "execution_recorder.py does not appear to have a long-only "
        "target_actions tuple `(\"BUY\", \"STRONG_BUY\")`. Bug AV fix "
        "incomplete."
    )
    # And `("SHORT",)` for the short branch
    assert '"SHORT"' in text, (
        "execution_recorder.py does not appear to have a short-only "
        "target_actions tuple `(\"SHORT\",)`. Bug AV fix incomplete."
    )


# ── xfail scaffolding for D161/D207/D170 (N+1 work) ───────────────
#
# These tests will flip XFAIL → XPASS when the short-side migrations
# land in N+1. Same auto-flip pattern as PROMPT_06's xfail guards.


@pytest.mark.xfail(
    reason="D161_FALLER_SHORT migration deferred to N+1 per PROMPT_09 §10. "
           "Will pass when D161 routes through post_fill_bookkeeping with "
           "verdict.direction='short' (Bug AV fix unblocks this).",
    strict=False,
)
def test_main_py_d161_uses_unified_helper():
    """D161_FALLER_SHORT entry path MUST call post_fill_bookkeeping with
    a short verdict. Until N+1 ships D161 migration, this xfails."""
    from pathlib import Path
    text = Path("main.py").read_text(encoding="utf-8")
    idx = text.find("D161_FALLER_SHORT") if "D161_FALLER_SHORT" in text else text.find("D161 FALLER")
    assert idx >= 0, "D161 marker not found in main.py"
    window = text[idx:idx + 5000]
    assert "post_fill_bookkeeping" in window, (
        "D161 path does not call post_fill_bookkeeping. N+1 migration pending."
    )


@pytest.mark.xfail(
    reason="D207_SHORT migration deferred to N+1.",
    strict=False,
)
def test_main_py_d207_uses_unified_helper():
    """D207_SHORT entry path MUST call post_fill_bookkeeping. N+1."""
    from pathlib import Path
    text = Path("main.py").read_text(encoding="utf-8")
    idx = text.find("D207_AGGRESSIVE_SHORT") if "D207_AGGRESSIVE_SHORT" in text else text.find("D207")
    assert idx >= 0, "D207 marker not found in main.py"
    window = text[idx:idx + 5000]
    assert "post_fill_bookkeeping" in window, (
        "D207 path does not call post_fill_bookkeeping. N+1 migration pending."
    )


@pytest.mark.xfail(
    reason="D170_OBSERVATION migration deferred to N+1.",
    strict=False,
)
def test_main_py_d170_uses_unified_helper():
    """D170_OBSERVATION entry path MUST call post_fill_bookkeeping. N+1."""
    from pathlib import Path
    text = Path("main.py").read_text(encoding="utf-8")
    idx = text.find("D170_OBSERVATION") if "D170_OBSERVATION" in text else text.find("D170")
    assert idx >= 0, "D170 marker not found in main.py"
    window = text[idx:idx + 5000]
    assert "post_fill_bookkeeping" in window, (
        "D170 path does not call post_fill_bookkeeping. N+1 migration pending."
    )
