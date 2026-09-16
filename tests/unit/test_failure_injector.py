"""Tests for mx-arena/arena/failure_injector.py — Block 2.2 of the
arena↔prod parity program.

Pins:
  1. LIDR Tue 2026-04-28 10:00:38 ET 403 fixture reproduces the Bug AR
     scenario in arena (byte-for-byte body match).
  2. Rule matching is correct on (ticker, action, time-window, max_fires).
  3. First-match-wins ordering is preserved across rule registrations.
  4. The injected body is parseable JSON and contains the substrings
     Bug AI / Bug AR look for ("insufficient qty available", "40310000",
     "available: 0", "qty available").
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "mx-arena"))

from arena.failure_injector import (  # noqa: E402
    FailureInjector,
    FailureRule,
    InjectedFailure,
    LIDR_TUE_403_BODY,
    lidr_tue_2026_04_28_qty_conflict,
)


# ── Fixture: LIDR Bug AR scenario reproduces in arena ───────────────


def test_lidr_fixture_body_byte_for_byte_matches_production():
    """The seeded body MUST equal the byte-for-byte production
    response body. If Alpaca payload shape drifts, this catches it
    (manually: compare the constant against fresh prod transcripts)."""
    expected = (
        '{"available":"0","code":40310000,"existing_qty":"5264",'
        '"held_for_orders":"5264","message":"insufficient qty available '
        'for order (requested: 5264, available: 0)","symbol":"LIDR"}'
    )
    assert LIDR_TUE_403_BODY == expected


@pytest.mark.parametrize("needle", [
    "insufficient qty available",
    "40310000",
    "available: 0",
    "qty available",
])
def test_lidr_fixture_contains_all_bug_ai_substrings(needle):
    """Bug AR fix matches against {insufficient qty available, 40310000,
    available: 0, qty available}. The fixture body MUST contain ALL
    of them so an arena replay exercises the same matching logic that
    prod's err_str does."""
    assert needle.lower() in LIDR_TUE_403_BODY.lower()


def test_lidr_fixture_fires_within_window_only_once():
    """Drop the seeded fixture into an injector. Fire the canonical
    timestamp — should match. Fire again — max_fires=1 prevents
    duplicate. Fire outside the window — should NOT match."""
    inj = FailureInjector([lidr_tue_2026_04_28_qty_conflict()])

    in_window = datetime.fromisoformat("2026-04-28T14:00:38+00:00")
    fail1 = inj.evaluate(ticker="LIDR", action="close_position", ts_utc=in_window)
    assert fail1 is not None
    assert fail1.status_code == 403
    assert fail1.body == LIDR_TUE_403_BODY

    # Second fire same window: max_fires=1 already exhausted
    fail2 = inj.evaluate(ticker="LIDR", action="close_position", ts_utc=in_window)
    assert fail2 is None

    # Outside the window
    inj.clear()
    inj.add_rule(lidr_tue_2026_04_28_qty_conflict())
    out_of_window = datetime.fromisoformat("2026-04-28T15:00:00+00:00")
    fail3 = inj.evaluate(ticker="LIDR", action="close_position", ts_utc=out_of_window)
    assert fail3 is None


def test_lidr_fixture_body_is_valid_json():
    """The seeded body parses as JSON. Tests assertions about the body
    shape can use the json view; production's Bug AR substring match
    works on the raw text either way."""
    rule = lidr_tue_2026_04_28_qty_conflict()
    parsed = rule.response.body_json
    assert parsed["code"] == 40310000
    assert parsed["symbol"] == "LIDR"
    assert parsed["available"] == "0"
    assert parsed["existing_qty"] == "5264"
    assert "insufficient qty available" in parsed["message"]


# ── Rule matching semantics ────────────────────────────────────────


def test_ticker_wildcard_matches_any_symbol():
    """ticker=None means match any symbol. Used for broad failure
    injection (e.g., 'all closes return 5xx for the next 30s')."""
    inj = FailureInjector([
        FailureRule(
            response=InjectedFailure(status_code=503, body='{"detail":"outage"}'),
            ticker=None, action="close_position",
        ),
    ])
    ts = datetime.now(timezone.utc)
    assert inj.evaluate(ticker="LIDR", action="close_position", ts_utc=ts) is not None
    assert inj.evaluate(ticker="OGN", action="close_position", ts_utc=ts) is not None


def test_action_filter_distinguishes_endpoints():
    """A rule scoped to action='close_position' must NOT fire for
    'submit_order'. This prevents accidentally injecting failures
    into entry paths when only exits are intended to fail."""
    inj = FailureInjector([
        FailureRule(
            response=InjectedFailure(status_code=403, body="{}"),
            ticker="LIDR", action="close_position",
        ),
    ])
    ts = datetime.now(timezone.utc)
    assert inj.evaluate(ticker="LIDR", action="close_position", ts_utc=ts) is not None
    assert inj.evaluate(ticker="LIDR", action="submit_order", ts_utc=ts) is None


def test_first_match_wins_in_registration_order():
    """If two rules match the same query, the first-registered one
    fires. Predictable for fixture composition."""
    inj = FailureInjector()
    inj.add_rule(FailureRule(
        response=InjectedFailure(status_code=403, body='{"first":true}'),
        ticker="LIDR",
    ))
    inj.add_rule(FailureRule(
        response=InjectedFailure(status_code=503, body='{"second":true}'),
        ticker="LIDR",
    ))
    fail = inj.evaluate(ticker="LIDR", action="anything", ts_utc=datetime.now(timezone.utc))
    assert fail is not None
    assert fail.status_code == 403
    assert "first" in fail.body


def test_empty_injector_returns_none():
    """No rules → no failures. The fast path."""
    inj = FailureInjector()
    assert inj.evaluate(ticker="LIDR", action="any", ts_utc=datetime.now(timezone.utc)) is None


def test_max_fires_zero_disables_rule():
    """A rule with max_fires=0 should never fire (defensive: someone
    might use it to disable a rule without removing it)."""
    inj = FailureInjector([
        FailureRule(
            response=InjectedFailure(status_code=403, body="{}"),
            ticker="LIDR", max_fires=0,
        ),
    ])
    ts = datetime.now(timezone.utc)
    assert inj.evaluate(ticker="LIDR", action="any", ts_utc=ts) is None


def test_after_only_window_no_upper_bound():
    """A rule with only `after_iso` set fires for any ts >= after."""
    inj = FailureInjector([
        FailureRule(
            response=InjectedFailure(status_code=403, body="{}"),
            ticker="X",
            after_iso="2026-04-28T14:00:00Z",
        ),
    ])
    before = datetime.fromisoformat("2026-04-28T13:00:00+00:00")
    after = datetime.fromisoformat("2026-04-28T15:00:00+00:00")
    assert inj.evaluate(ticker="X", action="a", ts_utc=before) is None
    assert inj.evaluate(ticker="X", action="a", ts_utc=after) is not None


# ── Integration check: rules survive registration round-trip ───────


def test_rules_property_returns_a_copy_not_the_internal_list():
    """If `rules` returned the live internal list, mutating it
    externally would corrupt the injector silently. Defensive copy."""
    inj = FailureInjector([
        FailureRule(response=InjectedFailure(status_code=403, body="{}")),
    ])
    snapshot = inj.rules
    snapshot.clear()
    # Internal still has the rule
    assert len(inj.rules) == 1


# ── End-to-end: this is the test that gates Bug AR's arena replay ──


def test_bug_ar_scenario_full_replay_works_end_to_end():
    """The full Bug AR reproduction:

      1. Seed injector with the LIDR fixture.
      2. Simulate a close attempt at the canonical timestamp.
      3. Confirm arena returns the 403 with the prod-shape body.
      4. The body when extracted via the Bug AR fix's e.response.text
         pattern contains every substring the production cancel-and-
         coordinate logic looks for.

    This is the test that validates Bug AR fix would have fired in
    arena had arena been able to inject this 403 from day one. Going
    forward, every Bug AR-class scenario we encounter in prod should
    land here as a fixture + this test parameterized over them."""
    inj = FailureInjector([lidr_tue_2026_04_28_qty_conflict()])
    fail = inj.evaluate(
        ticker="LIDR", action="close_position",
        ts_utc=datetime.fromisoformat("2026-04-28T14:00:38+00:00"),
    )
    assert fail is not None, "fixture failed to fire — Bug AR replay would have silently passed"
    assert fail.status_code == 403

    # The Bug AR fix builds err_str = f"{type(e).__name__}: {e}; body={body}".
    # Then matches case-insensitively. We mimic the match here:
    err_str = f"HTTPStatusError: 403 Forbidden; body={fail.body}"
    err_lower = err_str.lower()
    assert (
        "insufficient qty available" in err_lower
        or "40310000" in err_str
        or "available: 0" in err_lower
        or "qty available" in err_lower
    ), (
        "Bug AR substring match would NOT fire on the arena-injected 403. "
        "Either the fixture body drifted from prod, or the Bug AR fix's "
        "match logic regressed."
    )
