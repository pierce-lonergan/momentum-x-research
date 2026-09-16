"""doc 191: tests for the continuation-confirmed faller EXEMPTION predicate.

The gate-action that turns the faller block from "block all predicted faders" into
"block UNLESS the validated continuation signature (doc 188) is present AND the name
is liquid enough to fill+exit". DEFAULT OFF (observe-first) — the wired call must be a
strict no-op until the flag is flipped. These tests pin that contract.
"""
from __future__ import annotations

from src.analysis.opening_range import faller_exemption_ok

# Production defaults (config/settings.py FallerConfig).
FLOOR = 5_000_000.0
MIN_ORVOL = 2.0


def _ok(**kw):
    base = dict(
        enabled=True, confirmed=True, dollar_volume=10_000_000.0, opening_rvol=3.0,
        min_dollar_volume=FLOOR, min_opening_rvol=MIN_ORVOL,
    )
    base.update(kw)
    return faller_exemption_ok(**base)


def test_disabled_is_always_false_even_when_confirmed_and_liquid():
    """The default-OFF flag must make the exemption a strict no-op."""
    assert _ok(enabled=False) is False


def test_unconfirmed_is_false():
    """No validated continuation signature -> no exemption (block stands)."""
    assert _ok(confirmed=False) is False


def test_confirmed_liquid_enabled_is_true():
    assert _ok() is True


def test_below_liquidity_floor_is_false():
    """The edge needs fills + a survivable exit — never exempt the thinnest names."""
    assert _ok(dollar_volume=FLOOR - 1) is False
    assert _ok(dollar_volume=None) is False


def test_below_min_opening_rvol_is_false():
    """A LIVE override needs extra conviction (higher RVOL bar than mere confirm)."""
    assert _ok(opening_rvol=MIN_ORVOL - 0.01) is False
    assert _ok(opening_rvol=None) is False


def test_exactly_at_thresholds_is_true():
    """Floors are inclusive: at-threshold liquidity + RVOL qualifies."""
    assert _ok(dollar_volume=FLOOR, opening_rvol=MIN_ORVOL) is True


def test_all_four_conditions_required():
    """Every condition is necessary; dropping any one flips the result to False."""
    assert _ok() is True
    assert _ok(enabled=False) is False
    assert _ok(confirmed=False) is False
    assert _ok(dollar_volume=0.0) is False
    assert _ok(opening_rvol=0.0) is False
