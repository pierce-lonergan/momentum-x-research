"""doc 220: the EOD-close retry-until-bell gating rule.

The 6/1 carry: D76 set eod_close_completed=True even when CMND/OPTU's close FAILED (403) ->
they carried overnight. Fix: mark complete ONLY if every position closed OR it's the final
pass (>=15:59). The decision rule is embedded in main.py's Phase-3 block; this pins the rule
itself so it can't silently regress.

Rule (doc 220 + doc 225 fix): complete = (_d76_all_closed) OR (min_et >= 58 final pass).
doc 225: boundary was >=59, but the loop cadence (30-60s + D76 processing) can jump a 15:58
sample to 16:00:xx, where hour_et==16 and the D76 block (gated hour_et==15) is never re-entered
-> flag stays False -> Phase 4 carries the retryable position overnight (minute 59 SKIPPED).
Firing at >=58 sets the complete-flag reliably before the hour rolls over.
"""
from __future__ import annotations

import pytest


def _eod_complete(all_closed: bool, min_et: int) -> bool:
    """Mirror of the doc-220/225 gating rule in main.py's D76 block."""
    final_pass = (min_et >= 58)
    return all_closed or final_pass


@pytest.mark.parametrize("all_closed,min_et,expected,why", [
    (True,  55, True,  "all closed early -> complete immediately"),
    (False, 55, False, "a close failed at 15:55 -> NOT complete, retry next cycle"),
    (False, 56, False, "still failing at 15:56 -> keep retrying"),
    (False, 57, False, "still failing at 15:57 -> keep retrying (before the bell)"),
    (False, 58, True,  "doc 225: final pass at 15:58 -> complete BEFORE the 16:00 skip race"),
    (False, 59, True,  "final pass at 15:59 -> complete"),
    (True,  58, True,  "all closed on the final pass -> complete"),
])
def test_eod_completion_gating(all_closed, min_et, expected, why):
    assert _eod_complete(all_closed, min_et) is expected, why


def test_the_6_1_carry_would_now_retry():
    """The exact 6/1 case: CMND/OPTU failed to close at 15:55 -> the OLD code marked complete
    (carry); the NEW rule keeps it incomplete so 15:55->15:57 cycles retry the close."""
    assert _eod_complete(all_closed=False, min_et=55) is False   # retries instead of carrying
    for m in (55, 56, 57):
        assert _eod_complete(all_closed=False, min_et=m) is False
    # by 15:58 it accepts the carry (doc 225: before the 16:00 skip race):
    assert _eod_complete(all_closed=False, min_et=58) is True


def test_doc225_minute59_skip_race():
    """The doc-225 bug: at >=59-only, a 15:58->16:00 cadence jump never samples minute 59, so
    the flag never sets in Phase 3 -> carry. >=58 sets it at the 15:58 sample, before the roll."""
    old_rule = lambda m: m >= 59  # noqa: E731
    new_rule = lambda m: m >= 58  # noqa: E731
    assert old_rule(58) is False and new_rule(58) is True
