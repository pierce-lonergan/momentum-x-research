"""D278 EXIT_POLICY tests: gating BAR-1 EXIT on the exit_policy config.

Per docs/research-log/69_strategy_reevaluation_exit_policy_is_the_bug.md
Enhancement 1: replace BAR-1 EXIT (T+60s) with T+1 next-day open.

The implementation gates the BAR-1 EXIT call sites in main.py
(Phase 2 + Phase 3) on a helper `_d278_bar1_gated_off(settings)`.
When the helper returns True (policy != bar1_legacy), BAR-1 EXIT
is skipped; positions carry overnight; existing D86/D91 close-at-
next-open path handles T+1.

Operator override: MOMENTUM_EXIT_POLICY=t1_next_open env var.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("MOMENTUM_EXIT_POLICY", raising=False)


# ── Helper unit tests (the gating logic) ────────────────────────────


def _import_helpers():
    """Extract helpers from main.py and load into a single namespace
    so they can call each other."""
    import ast
    src = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    helper_names = {"_d278_active_policy", "_d278_bar1_gated_off"}
    helper_nodes = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in helper_names
    ]
    assert len(helper_nodes) == 2, f"expected 2 helpers, found {len(helper_nodes)}"
    mod = ast.Module(body=helper_nodes, type_ignores=[])
    ns: dict = {"os": os}
    exec(compile(mod, filename="<helpers>", mode="exec"), ns)
    return ns["_d278_active_policy"], ns["_d278_bar1_gated_off"]


class _FakeExec:
    exit_policy = "bar1_legacy"


class _FakeSettings:
    execution = _FakeExec()


def test_default_is_bar1_legacy(monkeypatch):
    """No env, default config → bar1_legacy → BAR-1 EXIT remains active."""
    monkeypatch.delenv("MOMENTUM_EXIT_POLICY", raising=False)
    active, gated_off = _import_helpers()
    s = _FakeSettings()
    assert active(s) == "bar1_legacy"
    assert gated_off(s) is False


@pytest.mark.parametrize("env_value", [
    "t1_next_open", "T1_NEXT_OPEN", "T1_Next_Open", "  t1_next_open  ",
])
def test_env_t1_next_open_gates_bar1_off(monkeypatch, env_value):
    """MOMENTUM_EXIT_POLICY=t1_next_open (case/whitespace tolerant)
    flips gated_off to True."""
    monkeypatch.setenv("MOMENTUM_EXIT_POLICY", env_value)
    active, gated_off = _import_helpers()
    s = _FakeSettings()
    assert active(s) == "t1_next_open"
    assert gated_off(s) is True


def test_env_bar1_legacy_keeps_active(monkeypatch):
    """Explicit env=bar1_legacy keeps BAR-1 EXIT active even if config disagrees."""
    monkeypatch.setenv("MOMENTUM_EXIT_POLICY", "bar1_legacy")
    active, gated_off = _import_helpers()
    s = _FakeSettings()
    s.execution.exit_policy = "t1_next_open"
    assert active(s) == "bar1_legacy"
    assert gated_off(s) is False


def test_invalid_env_falls_back_to_config(monkeypatch):
    """Garbage env value → use config field."""
    monkeypatch.setenv("MOMENTUM_EXIT_POLICY", "garbage_policy")
    active, gated_off = _import_helpers()
    s = _FakeSettings()
    s.execution.exit_policy = "t1_next_open"
    assert active(s) == "t1_next_open"
    assert gated_off(s) is True


def test_invalid_env_and_invalid_config_falls_back_to_legacy(monkeypatch):
    """Both env and config invalid → legacy default (safe fallback)."""
    monkeypatch.setenv("MOMENTUM_EXIT_POLICY", "garbage")
    active, gated_off = _import_helpers()
    s = _FakeSettings()
    s.execution.exit_policy = "also_garbage"
    assert active(s) == "bar1_legacy"
    assert gated_off(s) is False


def test_missing_settings_attr_safe(monkeypatch):
    """If settings.execution doesn't exist, helper doesn't crash."""
    monkeypatch.delenv("MOMENTUM_EXIT_POLICY", raising=False)
    active, gated_off = _import_helpers()

    class _Empty:
        pass

    assert active(_Empty()) == "bar1_legacy"
    assert gated_off(_Empty()) is False


# ── Settings load tests ─────────────────────────────────────────────


def test_settings_default_exit_policy_is_bar1_legacy():
    """The ExecutionConfig default is the safe legacy behavior."""
    from config.settings import Settings
    s = Settings()
    assert s.execution.exit_policy == "bar1_legacy"


# ── Source-grep guards (BAR-1 EXIT call sites must check the helper) ──


def test_main_py_phase2_bar1_call_site_uses_d278_helper():
    """The Phase 2 (post-fill) BAR-1 EXIT scheduler MUST gate on
    `_d278_bar1_gated_off`. Future regressions that bypass the gate
    fail this test.

    Updated 2026-04-29 (doc 83): after the unified post_fill_bookkeeping
    helper extraction, the Phase 2 inline BAR-1 block was removed from
    main.py and now lives in src/execution/post_fill_handler.py. The
    test now asserts that the PHASE2_BUY call site invokes the helper
    AND that the helper itself contains the D278 gate."""
    main_text = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
    # Find ALL occurrences of path="PHASE2_BUY" — the Discord webhook
    # uses it as a label too; only the post_fill_bookkeeping call counts.
    import re
    matches = [m.start() for m in re.finditer(r'path="PHASE2_BUY"', main_text)]
    assert matches, "Phase 2 marker (path=\"PHASE2_BUY\") not found anywhere"
    helper_call_found = False
    for idx in matches:
        window = main_text[max(0, idx - 500):idx + 500]
        if "post_fill_bookkeeping" in window:
            helper_call_found = True
            break
    assert helper_call_found, (
        "No path=\"PHASE2_BUY\" occurrence is near a post_fill_bookkeeping call. "
        "Regression: the PHASE2_BUY path is back to inline bookkeeping. "
        "Re-add the helper call per docs/research-log/82."
    )
    # And the helper itself must contain the D278 gate
    helper_text = (REPO_ROOT / "src" / "execution" / "post_fill_handler.py").read_text(encoding="utf-8")
    assert "_d278_bar1_gated_off" in helper_text, (
        "post_fill_handler.py does NOT call _d278_bar1_gated_off. "
        "Regression: the D278 gate has been removed from the unified helper. "
        "BAR-1 EXIT would fire even with MOMENTUM_EXIT_POLICY=t1_next_open. "
        "Re-add the gate per docs/research-log/69."
    )


def test_main_py_phase3_bar1_call_site_uses_d278_helper():
    """The Phase 3 monitoring loop's BAR-1 EXIT check MUST gate on
    `_d278_bar1_gated_off`."""
    main_text = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
    idx = main_text.find("D146: Bar-1 exit — sell 100% at T+60s")
    assert idx >= 0, "Phase 3 BAR-1 EXIT site marker not found"
    window = main_text[idx:idx + 2000]
    assert "_d278_bar1_gated_off" in window, (
        "Phase 3 BAR-1 EXIT monitoring check does NOT call "
        "_d278_bar1_gated_off. Regression: a future commit removed the "
        "D278 gate; BAR-1 EXIT would fire from the monitoring loop "
        "even with MOMENTUM_EXIT_POLICY=t1_next_open. "
        "Re-add the gate per docs/research-log/69."
    )


# ── Bug AT family coverage gaps (added 2026-04-29 PM, post-Bug-AS audit) ──
#
# Per docs/research-log/82 (Bug AT family synthesis), Phase 2 fill bookkeeping
# is duplicated inline at SIX entry call sites in main.py. RESCAN,
# VWAP_BREAKOUT, FAST_PATH, D207_SHORT, D161_FALLER_SHORT, D170_OBSERVATION
# are five copy-paste paths that drifted independently from PHASE2_BUY,
# omitting the BAR-1 EXIT scheduler + the D278 SKIPPED log.
#
# Until those sites are unified into a single helper (next session per
# doc 82 §5), at minimum the source-grep tests below will catch any NEW
# entry path that's added without going through the unified path.
#
# Strategy: assert each call site is either (a) calling the unified helper
# (whatever it ends up being named) OR (b) directly invoking the D278 gate.
# Today (pre-refactor) only PHASE2_BUY satisfies this. After the refactor,
# all six should.
#
# Gating mechanism in this test: each entry-site test that's known to be
# broken pre-refactor is marked xfail so this file passes today AND fails
# loudly the moment a regression slips in.


_RESCAN_MARKER = "RESCAN ORDER:"
_VWAP_MARKER = "VWAP_BREAKOUT"  # may need adjustment when refactor lands
_FAST_PATH_MARKER = "Registered fast-path position"


def test_main_py_rescan_call_site_uses_d278_helper():
    """RESCAN entry path MUST call post_fill_bookkeeping (which gates
    on _d278_bar1_gated_off internally). Doc 82's unified-handler
    refactor closed this gap on 2026-04-29 (PROMPT_06 / commit TBD)."""
    main_text = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
    idx = main_text.find(_RESCAN_MARKER)
    assert idx >= 0, "RESCAN ORDER marker not found"
    window = main_text[idx:idx + 4000]
    assert "_d278_bar1_gated_off" in window or "post_fill_bookkeeping" in window, (
        "RESCAN entry call site does NOT gate on _d278_bar1_gated_off "
        "(or call the unified post-fill handler). This is Bug AT-bypass — "
        "the RESCAN copy of Phase 2 omits the BAR-1 EXIT scheduler. "
        "Fix per doc 82's unified-handler refactor."
    )


def test_main_py_vwap_breakout_call_site_uses_d278_helper():
    """VWAP_BREAKOUT entry path MUST call post_fill_bookkeeping. Closed
    by doc 82's unified-handler refactor on 2026-04-29 (PROMPT_06)."""
    main_text = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
    idx = main_text.find("VWAP_BREAKOUT")
    if idx < 0:
        pytest.skip("VWAP_BREAKOUT marker not found (path may have been renamed)")
    window = main_text[idx:idx + 4000]
    assert "_d278_bar1_gated_off" in window or "post_fill_bookkeeping" in window, (
        "VWAP_BREAKOUT entry call site does NOT gate on _d278_bar1_gated_off. "
        "Bug AT-bypass — fix per doc 82."
    )


def test_main_py_fast_path_records_actual_fill_not_limit():
    """The FAST_PATH execution_recorder.record_execution call MUST receive
    the actual broker fill price, not the limit/entry price.

    Closed by doc 84 (PROMPT_07, 2026-04-30): FAST_PATH now routes
    through bridge.execute_verdict (which polls for broker confirmation)
    + post_fill_bookkeeping (which uses order.fill_price = realized).
    The OLD code path passed `fill_price=fpe.entry_price` (the limit) —
    no path in main.py should still do that."""
    main_text = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
    # Bug AT-1 was: D215 emitted with fill_price = limit price.
    # The fix: the limit-as-fill kwarg pattern must NOT appear anywhere
    # in main.py for FAST_PATH (or any path).
    bad_pattern = "fill_price=fpe.entry_price"
    assert bad_pattern not in main_text, (
        "FAST_PATH (or another path) is passing fpe.entry_price as "
        "fill_price. This is Bug AT-1 — the limit price gets recorded "
        "as the actual fill, even when the order never fills. The fix "
        "is to route through bridge.execute_verdict + the unified "
        "post_fill_bookkeeping helper. See docs/research-log/82 §3.1 and "
        "doc 84 for the migration template."
    )
    # Additionally assert the new flow IS in place: FAST_PATH should
    # now call bridge.execute_verdict + post_fill_bookkeeping with
    # path="FAST_PATH"
    assert 'path="FAST_PATH"' in main_text, (
        "FAST_PATH path string not found in main.py — the call site "
        "may have been deleted entirely. Restore the new flow per doc 84."
    )
    # Find the path="FAST_PATH" occurrence and verify post_fill_bookkeeping
    # is called near it
    fp_idx = main_text.find('path="FAST_PATH"')
    window = main_text[max(0, fp_idx - 1000):fp_idx + 500]
    assert "post_fill_bookkeeping" in window, (
        "FAST_PATH path label exists but the surrounding code does NOT "
        "call post_fill_bookkeeping. The unified post-fill helper is the "
        "single source of truth for D215 emission. Re-route FAST_PATH "
        "through it per doc 84."
    )


def test_d101_time_decay_gated_on_t1_next_open():
    """D101 §3.5 TIME_EXIT closes positions at T+20min if no 1R move.
    Under t1_next_open policy, this would prematurely close positions
    before they can carry overnight. The gate must skip D101 in that
    mode while keeping it active under bar1_legacy."""
    src = (REPO_ROOT / "src" / "execution" / "exit_intelligence.py").read_text(encoding="utf-8")
    idx = src.find("D101 §3.5: Time-Based Momentum Exit")
    assert idx >= 0, "D101 §3.5 site marker not found"
    window = src[idx:idx + 2000]
    assert "MOMENTUM_EXIT_POLICY" in window or "_d278" in window, (
        "D101 §3.5 TIME_EXIT does NOT honor MOMENTUM_EXIT_POLICY. "
        "Regression: under t1_next_open, positions would still be "
        "closed at T+20min by D101 §3.5, defeating the policy. "
        "Re-add the env-aware skip per docs/research-log/69."
    )
    assert "t1_next_open" in window, (
        "D101 §3.5 must explicitly check for the 't1_next_open' policy "
        "value (not just any value) to gate its time-decay behavior."
    )
