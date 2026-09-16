"""doc 202: tests for the ELITE-fade-short signature gate.

The flag-gated SHORT of the bot's predictable faders. Must be a strict no-op when OFF, and
the SQUEEZE CIRCUIT-BREAKER (never short a running name) is safety-critical — pin both.
"""
from __future__ import annotations

from src.analysis.opening_range import fade_short_signature_ok

FLOOR = 5_000_000.0
MIN_MFCS = 0.50


def _ok(**kw):
    base = dict(
        enabled=True, mfcs=0.60, shortable=True, easy_to_borrow=True,
        dollar_volume=10_000_000.0, above_vwap=False, orb_confirmed=False,
        min_mfcs=MIN_MFCS, min_dollar_volume=FLOOR,
    )
    base.update(kw)
    return fade_short_signature_ok(**base)


def test_happy_path_is_true():
    assert _ok() is True


def test_disabled_is_no_op():
    assert _ok(enabled=False) is False


def test_below_min_mfcs_is_false():
    """Only the ELITE-fade bucket (>=0.50) — the worst longs / best shorts."""
    assert _ok(mfcs=0.49) is False
    assert _ok(mfcs=None) is False


def test_not_borrowable_is_false():
    """Un-borrowable = unshortable (doc 201: only 18% of BUYs are ETB)."""
    assert _ok(shortable=False) is False
    assert _ok(easy_to_borrow=False) is False


def test_below_liquidity_floor_is_false():
    assert _ok(dollar_volume=FLOOR - 1) is False
    assert _ok(dollar_volume=None) is False


def test_squeeze_guard_orb_confirmed_blocks_short():
    """SAFETY: never short a doc-188 continuation-CONFIRMED name (it's a runner)."""
    assert _ok(orb_confirmed=True) is False


def test_squeeze_guard_above_vwap_blocks_short():
    """SAFETY: holding above VWAP = running -> do NOT short (the squeeze tail)."""
    assert _ok(above_vwap=True) is False


def test_all_conditions_required():
    assert _ok() is True
    for kill in (
        {"enabled": False}, {"mfcs": 0.4}, {"shortable": False},
        {"easy_to_borrow": False}, {"dollar_volume": 0.0},
        {"orb_confirmed": True}, {"above_vwap": True},
    ):
        assert _ok(**kill) is False, f"expected False when {kill}"
