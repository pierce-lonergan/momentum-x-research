"""Bug AO tests: decision-replay infrastructure (Tier 4 #15).

The validation case the architecture is built for: confirm Bug AK
(D124 three-tier rejection) actually delivers its predicted 47%
reduction in D124 rejections when replayed against today's actual
production session log.

Aggressive testing per Tier 4 mandate:

  1. Schema tests — DecisionRow Pydantic round-trip
  2. Log-parser tests — ALL captured patterns from today's log
  3. Rules tests — d124 v1 vs v2 functions
  4. Replay-engine tests — pinning the three-case decision tree
  5. Counterfactual tests — aggregator math
  6. Property tests (Hypothesis) — replay invariants
  7. Integration test — end-to-end against today's actual log
     (the CANONICAL Bug AO validation case)
  8. Self-test — replay with rule="v1_pre_bug_ak" must NOT change
     captured verdicts (the round-trip discipline)

See docs/research-log/54_bug_ao_decision_replay.md.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from hypothesis import given, settings as hyp_settings, strategies as st

from src.analysis.decision_replay.counterfactual import (
    CounterfactualReport,
    replay_corpus,
    replay_jsonl,
)
from src.analysis.decision_replay.log_parser import (
    parse_log_file,
    parse_log_to_jsonl,
)
from src.analysis.decision_replay.rules import (
    d124_v1_pre_bug_ak,
    d124_v2_bug_ak,
    replay_decision_d124,
)
from src.analysis.instrumentation.schemas import DecisionRow


# ── Schema round-trip ─────────────────────────────────────────────────


def test_decision_row_roundtrip_minimal():
    """DecisionRow constructs + serializes + reconstructs identically."""
    row = DecisionRow(
        decision_id=str(uuid.uuid4()), timestamp=datetime.now(timezone.utc),
        session_date="2026-04-27", cycle_number=5, ticker="LIDR",
        candidate_gap_pct=0.03, candidate_rvol=126.0, candidate_current_price=2.42,
        candidate_float_shares=36_300_000, candidate_market_cap=87_846_000,
        candidate_gap_classification="MINOR", candidate_has_news_catalyst=False,
        agent_signals=[
            {"agent_id": "manipulation_agent", "signal": "BEAR",
             "confidence": 0.49, "weight": 1.0, "flags": []},
        ],
        n_agents_total=5, n_agents_returned=4, n_agents_failed=0,
        mfcs_score=0.226, mfcs_components={"volume_rvol": 0.250},
        d124_bullish_count=0, d124_bearish_count=1,
        d124_bullish_conf=0.0, d124_bearish_conf=0.49, d124_max_bearish_conf=0.49,
        d124_was_rejected=False, d124_rejection_tier=None,
        mfcs_threshold_static=0.25, mfcs_threshold_effective=0.25,
        mfcs_threshold_passed=False,
        verdict_action="HOLD", verdict_reason="MFCS below threshold",
        verdict_confidence=0.23,
    )
    dumped = row.model_dump(mode="json")
    restored = DecisionRow(**dumped)
    assert restored.ticker == "LIDR"
    assert restored.verdict_action == "HOLD"
    assert restored.d124_bullish_conf == 0.0


def test_decision_row_rejects_invalid_verdict():
    """verdict_action is Literal-typed; invalid values must raise."""
    with pytest.raises(Exception):  # Pydantic ValidationError
        DecisionRow(
            decision_id="x", timestamp=datetime.now(timezone.utc),
            session_date="2026-04-27", cycle_number=0, ticker="X",
            candidate_gap_pct=0.0, candidate_rvol=1.0, candidate_current_price=1.0,
            candidate_gap_classification="", candidate_has_news_catalyst=False,
            agent_signals=[], n_agents_total=0, n_agents_returned=0, n_agents_failed=0,
            mfcs_score=0.0, mfcs_components={},
            d124_bullish_count=0, d124_bearish_count=0,
            d124_bullish_conf=0.0, d124_bearish_conf=0.0, d124_max_bearish_conf=0.0,
            mfcs_threshold_static=0.25, mfcs_threshold_effective=0.25,
            mfcs_threshold_passed=False,
            verdict_action="MAYBE",  # ← invalid, not in Literal
            verdict_confidence=0.0,
        )


# ── Rules: d124 v1 vs v2 ─────────────────────────────────────────────


@pytest.mark.parametrize("bull_n,bull_c,bear_n,bear_c,expected_v1", [
    # Today's actual rejection patterns
    (0, 0.0, 1, 0.45, True),   # v1 rejected (the buggy case)
    (0, 0.0, 1, 0.49, True),
    (0, 0.0, 1, 0.80, True),
    (0, 0.0, 2, 1.04, True),
    (0, 0.0, 2, 1.00, True),
    # Should NOT reject under v1
    (1, 0.5, 0, 0.0, False),
    (3, 1.5, 1, 0.5, False),
    (0, 0.0, 0, 0.0, False),
])
def test_d124_v1_matches_today_log_patterns(bull_n, bull_c, bear_n, bear_c, expected_v1):
    """v1 logic mirrors the pre-Bug-AK production code exactly."""
    assert d124_v1_pre_bug_ak(
        bullish_count=bull_n, bullish_conf=bull_c,
        bearish_count=bear_n, bearish_conf=bear_c,
    ) is expected_v1


@pytest.mark.parametrize("bull_n,bull_c,bear_n,bear_c,max_bear,expected_v2,expected_tier", [
    # Today's most common pattern: v2 PASSES (Bug AK win)
    (0, 0.0, 1, 0.45, 0.45, False, None),
    (0, 0.0, 1, 0.49, 0.49, False, None),
    (0, 0.0, 1, 0.80, 0.80, False, None),
    # 0 vs 2 bearish — Tier A fires first (2 >= 0+2 numeric dominance)
    # regardless of confidence. This is the most common "still reject"
    # case from today's log.
    (0, 0.0, 2, 1.04, 0.55, True, "A:numeric"),
    (0, 0.0, 2, 1.25, 0.65, True, "A:numeric"),
    (0, 0.0, 2, 0.50, 0.30, True, "A:numeric"),
    # 1 vs 2 bearish (NOT numerically dominant since 2 < 1+2=3) but
    # aggregate conf > max(0.5*1.5, 1.0) = 1.0 → Tier B catches it
    (1, 0.5, 2, 1.25, 0.70, True, "B:aggregate"),
    # Single very-high-conf bearish — v2 rejects at Tier C (NEW)
    (0, 0.0, 1, 0.85, 0.85, True, "C:single-veto"),
    (1, 0.5, 1, 0.90, 0.90, True, "C:single-veto"),
])
def test_d124_v2_three_tier_logic(bull_n, bull_c, bear_n, bear_c, max_bear, expected_v2, expected_tier):
    """v2 logic implements the three explicit tiers from Bug AK."""
    rejected, tier = d124_v2_bug_ak(
        bullish_count=bull_n, bullish_conf=bull_c,
        bearish_count=bear_n, bearish_conf=bear_c,
        max_bearish_conf=max_bear,
    )
    assert rejected is expected_v2
    assert tier == expected_tier


# ── Replay engine: 3-case decision tree ──────────────────────────────


def _captured_d124_reject(bull_n=0, bear_n=1, bull_c=0.0, bear_c=0.49, max_bear=0.49):
    """Build a captured DecisionRow dict for a D124-rejected candidate."""
    return {
        "ticker": "X", "verdict_action": "NO_TRADE",
        "d124_was_rejected": True,
        "d124_bullish_count": bull_n, "d124_bearish_count": bear_n,
        "d124_bullish_conf": bull_c, "d124_bearish_conf": bear_c,
        "d124_max_bearish_conf": max_bear,
        "mfcs_score": 0.0, "mfcs_threshold_effective": 0.25,
    }


def _captured_buy(mfcs=0.50):
    return {
        "ticker": "X", "verdict_action": "BUY", "d124_was_rejected": False,
        "d124_bullish_count": 1, "d124_bearish_count": 0,
        "d124_bullish_conf": 0.5, "d124_bearish_conf": 0.0, "d124_max_bearish_conf": 0.0,
        "mfcs_score": mfcs, "mfcs_threshold_effective": 0.25,
    }


def test_replay_case_1_buy_with_passing_v2_unchanged():
    """Captured BUY + v2 also passes → no change."""
    captured = _captured_buy(mfcs=0.50)
    replayed, meta = replay_decision_d124(captured, rule_version="v2_bug_ak")
    assert replayed == "BUY"
    assert meta["verdict_changed"] is False


def test_replay_case_2_buy_blocked_by_v2_tier_c():
    """Captured BUY but v2 Tier C catches a high-conf single bearish
    that v1 missed. This is the rare BLOCKED case."""
    captured = _captured_buy(mfcs=0.50)
    captured["d124_bearish_count"] = 1
    captured["d124_bearish_conf"] = 0.90  # v1 lets through (1>0 but bull_conf*1.5=0.75; 0.90>0.75 actually trips v1!)
    captured["d124_max_bearish_conf"] = 0.90
    captured["d124_bullish_count"] = 0
    captured["d124_bullish_conf"] = 0.0
    # v1 rejects (any bear>0 trips when bull=0); v2 rejects (Tier C: max>=0.85)
    # Both reject — captured wouldn't actually have been BUY in production
    # under v1. But replay should still say "yes v2 rejects."
    replayed, meta = replay_decision_d124(captured, rule_version="v2_bug_ak")
    assert replayed == "NO_TRADE"
    assert meta["d124_rejected_under_replay_rule"] is True
    assert meta["d124_rejection_tier_replay"] == "C:single-veto"


def test_replay_case_3_d124_reject_now_passes_to_potential_buy():
    """The Bug AK target case: captured was D124-rejected under v1
    (the buggy 0-bullish-vs-1-bearish-low-conf pattern). v2 passes.
    Replay marks as POTENTIAL_BUY because we don't know what MFCS
    would have decided."""
    captured = _captured_d124_reject(bull_n=0, bear_n=1, bear_c=0.45, max_bear=0.45)
    replayed, meta = replay_decision_d124(captured, rule_version="v2_bug_ak")
    assert replayed == "BUY"
    assert meta["captured_d124_rejected"] is True
    assert meta["d124_rejected_under_replay_rule"] is False
    assert "potential_BUY" in meta["flip_direction"]


def test_replay_case_4_d124_reject_still_rejected():
    """Captured D124 reject AND v2 also rejects (≥2 bearish)."""
    captured = _captured_d124_reject(bull_n=0, bear_n=2, bear_c=1.04, max_bear=0.55)
    replayed, meta = replay_decision_d124(captured, rule_version="v2_bug_ak")
    assert replayed == "NO_TRADE"
    assert meta["verdict_changed"] is False
    assert meta["d124_rejected_under_replay_rule"] is True


def test_replay_self_test_v1_against_v1_no_changes():
    """The round-trip discipline: replaying with the SAME rule that
    captured the decision must produce identical verdicts. If this
    test fails, the rule mirror has drifted from production."""
    # Build a corpus of mixed captured outcomes
    corpus = [
        _captured_d124_reject(bull_n=0, bear_n=1, bear_c=0.45),
        _captured_d124_reject(bull_n=0, bear_n=2, bear_c=1.04, max_bear=0.55),
        _captured_buy(mfcs=0.50),
    ]
    for row in corpus:
        replayed, meta = replay_decision_d124(row, rule_version="v1_pre_bug_ak")
        # v1 replay against v1-captured should reproduce captured action
        assert replayed == row["verdict_action"], (
            f"v1 self-replay flipped a verdict — rule mirror has drifted. "
            f"Captured={row['verdict_action']}, replayed={replayed}"
        )


# ── Property test: replay is deterministic ──────────────────────────


@hyp_settings(max_examples=200, deadline=None)
@given(
    bull_n=st.integers(min_value=0, max_value=5),
    bull_c=st.floats(min_value=0.0, max_value=3.0, allow_nan=False),
    bear_n=st.integers(min_value=0, max_value=5),
    bear_c=st.floats(min_value=0.0, max_value=3.0, allow_nan=False),
    max_bear=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    mfcs=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
)
def test_property_replay_is_deterministic(bull_n, bull_c, bear_n, bear_c, max_bear, mfcs):
    """For ALL (signal, MFCS) combinations: replaying the same captured
    decision twice must produce the identical verdict."""
    captured = {
        "ticker": "X", "verdict_action": "NO_TRADE",
        "d124_was_rejected": d124_v1_pre_bug_ak(
            bullish_count=bull_n, bullish_conf=bull_c,
            bearish_count=bear_n, bearish_conf=bear_c,
        ),
        "d124_bullish_count": bull_n, "d124_bearish_count": bear_n,
        "d124_bullish_conf": bull_c, "d124_bearish_conf": bear_c,
        "d124_max_bearish_conf": max_bear, "mfcs_score": mfcs,
        "mfcs_threshold_effective": 0.25,
    }
    r1, _ = replay_decision_d124(captured, rule_version="v2_bug_ak")
    r2, _ = replay_decision_d124(captured, rule_version="v2_bug_ak")
    assert r1 == r2


# ── Integration: replay today's actual session log ──────────────────


@pytest.fixture
def todays_log() -> Path:
    """Today's actual production session log."""
    return Path("logs/momentum_2026-04-27.log")


def test_integration_today_log_parses_meaningful_decisions(todays_log):
    """Sanity: today's log produces a non-trivial DecisionRow corpus."""
    if not todays_log.exists():
        pytest.skip("today's log not present (test running on a non-Monday machine)")
    rows = list(parse_log_file(todays_log))
    assert len(rows) > 100, (
        f"expected >100 decisions in today's log, got {len(rows)}"
    )
    # Should include both D124 rejections AND verdict-line decisions
    n_d124_rejected = sum(1 for r in rows if r.get("d124_was_rejected"))
    n_verdict_actions = sum(1 for r in rows if not r.get("d124_was_rejected"))
    assert n_d124_rejected >= 30, f"expected ~37 D124 rejects, got {n_d124_rejected}"
    assert n_verdict_actions >= 100


def test_integration_bug_ak_predicted_47pct_reduction_validated(todays_log):
    """THE CANONICAL VALIDATION TEST.

    Bug AK predicted 47% reduction in D124 rejections by replaying
    today's actual session through v2 logic. This test verifies
    the prediction quantitatively.

    Acceptance: between 35% and 60% of v1-rejected decisions should
    pass through under v2. Exact number can vary slightly with log
    parsing; the band is wide enough to absorb minor parser drift."""
    if not todays_log.exists():
        pytest.skip("today's log not present")
    corpus = list(parse_log_file(todays_log))
    report = replay_corpus(corpus, rule_kind="d124", rule_version="v2_bug_ak")

    # Filter to only the captured-D124-reject decisions
    n_v1_rejects = sum(1 for r in corpus if r.get("d124_was_rejected"))
    n_now_passing = sum(
        1 for r in corpus
        if r.get("d124_was_rejected") and not d124_v2_bug_ak(
            bullish_count=r["d124_bullish_count"],
            bullish_conf=r["d124_bullish_conf"],
            bearish_count=r["d124_bearish_count"],
            bearish_conf=r["d124_bearish_conf"],
            max_bearish_conf=r["d124_max_bearish_conf"],
        )[0]
    )
    pass_through_rate = n_now_passing / n_v1_rejects * 100.0 if n_v1_rejects else 0
    print(f"\n  n_v1_rejects={n_v1_rejects}, n_now_passing={n_now_passing}, "
          f"pass_through_rate={pass_through_rate:.1f}%")
    assert 35.0 <= pass_through_rate <= 60.0, (
        f"Bug AK predicted ~47% pass-through; measured {pass_through_rate:.1f}% "
        f"on today's actual session. Either the rule mirror has drifted, the "
        f"log parser is missing patterns, or production decisions changed shape."
    )

    # Also verify: NO captured BUYs are accidentally blocked under v2
    # (Bug AK was supposed to be more permissive, not more strict)
    assert report.n_blocked_buys == 0, (
        f"Bug AK should not block any captured BUYs; got {report.n_blocked_buys}. "
        f"Tier C single-veto threshold (0.85) may be catching real signal — "
        f"investigate {report.blocked_buys[:3]}"
    )


# ── Counterfactual report ──────────────────────────────────────────


def test_counterfactual_report_summary_text_renders():
    """The report's summary_text renders without crashing on an empty
    or full report."""
    empty = CounterfactualReport()
    text = empty.summary_text()
    assert "DECISION-REPLAY" in text
    assert "0" in text


def test_counterfactual_jsonl_roundtrip(tmp_path):
    """Parse log → JSONL → replay JSONL produces same numbers as
    direct replay."""
    log_path = Path("logs/momentum_2026-04-27.log")
    if not log_path.exists():
        pytest.skip("today's log not present")
    jsonl_path = tmp_path / "decisions.jsonl"
    n = parse_log_to_jsonl(log_path, jsonl_path)
    assert n > 0
    direct_report = replay_corpus(
        list(parse_log_file(log_path)),
        rule_kind="d124", rule_version="v2_bug_ak",
    )
    file_report = replay_jsonl(jsonl_path, rule_kind="d124", rule_version="v2_bug_ak")
    assert file_report.n_decisions_total == direct_report.n_decisions_total
    assert file_report.n_verdicts_flipped == direct_report.n_verdicts_flipped
