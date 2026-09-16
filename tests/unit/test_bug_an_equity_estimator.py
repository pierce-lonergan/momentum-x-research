"""Bug AN tests: equity estimator typo regression guard.

Pins today's D230 RECON_WARN EQUITY firing-every-30s as a
regression guard. The pre-Bug-AN code did:

    float(getattr(position_manager, "starting_equity", 0) or 0)

But PositionManager only had `_starting_equity` (underscore-private),
so the getattr returned the default 0. Every session had:

    internal_estimate = 0
    delta = broker_equity - 0 = broker_equity (~$140K)
    abs(delta) > $1 tolerance → D230 RECON_WARN

The fix:
  1. Added a public `starting_equity` property to PositionManager
  2. eod_recon now correctly reads the public name

These tests use REAL PositionManager (not MagicMock) — the typo bug
would have surfaced if the test had been written against the real
class instead of MagicMock-with-explicit-attrs (which masked the bug
because MagicMock auto-creates any accessed attribute).

Aggressive testing per Tier 3 mandate. See docs/research-log/53_bug_an_equity_estimator.md.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.execution.position_manager import PositionManager


def _build_real_pm(starting_equity: float = 100_000.0) -> PositionManager:
    """Build a REAL PositionManager (not MagicMock). The Bug AN typo
    would have surfaced under this fixture but was hidden because
    test_d230_eod_recon_invariants.py uses MagicMock."""
    cfg = MagicMock()
    cfg.daily_loss_limit_pct = 0.10
    cfg.max_positions = 8
    cfg.stop_loss_pct = 0.055
    cfg.max_position_size_pct = 0.15
    cfg.tier1_max_concurrent = 2
    cfg.tier2_max_concurrent = 4
    cfg.tier3_max_concurrent = 6
    cfg.tier4_max_concurrent = 8
    return PositionManager(config=cfg, starting_equity=starting_equity)


# ── Direct property tests ──────────────────────────────────────────


def test_position_manager_exposes_public_starting_equity():
    """The Bug AN root cause was that no public `starting_equity`
    attribute existed. Bug AN added a property; pin it as the public
    contract."""
    pm = _build_real_pm(starting_equity=140_000.0)
    assert pm.starting_equity == 140_000.0
    assert pm._starting_equity == 140_000.0
    # Both are the same value (property delegates to private)
    assert pm.starting_equity == pm._starting_equity


def test_starting_equity_reflects_init_value():
    """Pin that init's `starting_equity` parameter flows to the
    public property (the wiring that was broken in eod_recon)."""
    for v in (50_000.0, 100_000.0, 142_520.59, 1_000_000.0):
        pm = _build_real_pm(starting_equity=v)
        assert pm.starting_equity == v


def test_starting_equity_is_read_only_property():
    """The public starting_equity is a property (read-only).
    Mutating it directly should raise — preserves the underscore-
    private convention's intent (only init sets it)."""
    pm = _build_real_pm(starting_equity=100_000.0)
    with pytest.raises((AttributeError, TypeError)):
        pm.starting_equity = 999_999.0  # type: ignore


# ── End-to-end against real eod_recon ──


@pytest.mark.asyncio
async def test_real_pm_in_eod_recon_does_not_trigger_d230_on_clean_state(caplog):
    """The canonical regression: real PositionManager + clean broker
    state → no D230 RECON_WARN EQUITY. The pre-Bug-AN code would
    have fired D230 because internal_estimate was always $0."""
    from src.monitoring.eod_recon import run_eod_invariants

    pm = _build_real_pm(starting_equity=100_000.0)

    client = MagicMock()
    client.get_positions = AsyncMock(return_value=[])
    client.get_orders = AsyncMock(return_value=[])
    # Broker says equity is $100,000 — exactly matches starting_equity
    # (no positions, no realized PnL)
    client.get_account = AsyncMock(return_value={"equity": "100000.00"})

    with caplog.at_level(logging.WARNING, logger="src.monitoring.eod_recon"):
        result = await run_eod_invariants(client=client, position_manager=pm)

    # Bug AN regression: no D230 EQUITY warning
    equity_warnings = [
        r for r in caplog.records
        if "D230 RECON_WARN EQUITY" in r.message
    ]
    assert len(equity_warnings) == 0, (
        f"Bug AN regression: D230 EQUITY fired on clean state. "
        f"internal_estimate was {result.get('internal_equity_estimate')}, "
        f"broker_equity was {result.get('broker_equity')}"
    )
    assert result["internal_equity_estimate"] == 100_000.0, (
        "Bug AN regression: internal estimate must reflect starting equity"
    )
    assert result["equity_within_tolerance"] is True


@pytest.mark.asyncio
async def test_real_pm_with_realized_pnl_added_correctly(caplog):
    """Realized P&L correctly adjusts the internal estimate.
    Pre-Bug-AN: estimate was always $0 regardless of P&L."""
    from src.monitoring.eod_recon import run_eod_invariants

    pm = _build_real_pm(starting_equity=100_000.0)
    pm._daily_realized_pnl = 500.0  # +$500 realized today

    client = MagicMock()
    client.get_positions = AsyncMock(return_value=[])
    client.get_orders = AsyncMock(return_value=[])
    # Broker reflects: starting + realized = $100,500
    client.get_account = AsyncMock(return_value={"equity": "100500.00"})

    result = await run_eod_invariants(client=client, position_manager=pm)
    assert result["internal_equity_estimate"] == 100_500.0
    assert result["equity_within_tolerance"] is True


@pytest.mark.asyncio
async def test_real_pm_with_actual_drift_correctly_fires_d230(caplog):
    """The OPPOSITE direction: when there's REAL drift between
    broker and internal estimate (operator deposit, position
    unrealized P&L not in tracker, etc.), D230 SHOULD fire — and
    Bug AN's fix preserves this signal."""
    from src.monitoring.eod_recon import run_eod_invariants

    pm = _build_real_pm(starting_equity=100_000.0)
    pm._daily_realized_pnl = 0.0

    client = MagicMock()
    client.get_positions = AsyncMock(return_value=[])
    client.get_orders = AsyncMock(return_value=[])
    # Broker says $99,000 — $1,000 less than internal estimate
    client.get_account = AsyncMock(return_value={"equity": "99000.00"})

    with caplog.at_level(logging.WARNING, logger="src.monitoring.eod_recon"):
        result = await run_eod_invariants(client=client, position_manager=pm)

    equity_warnings = [
        r for r in caplog.records
        if "D230 RECON_WARN EQUITY" in r.message
    ]
    assert len(equity_warnings) == 1, (
        "Real drift of $1000 should fire D230 — Bug AN must not silence "
        "the legitimate signal"
    )
    assert result["equity_within_tolerance"] is False


def test_pre_bug_an_typo_pattern_does_not_match_pm_anymore():
    """Bug AN root-cause regression guard: the previously-broken
    code path was `getattr(pm, "starting_equity", 0)` returning 0
    because no such attribute existed. Now that the property exists,
    even the broken-style call returns the correct value."""
    pm = _build_real_pm(starting_equity=100_000.0)
    # Even using the EXACT broken access pattern from the pre-Bug-AN
    # code, we now get the correct value (because the property exists)
    legacy_access = getattr(pm, "starting_equity", 0)
    assert legacy_access == 100_000.0, (
        "Pre-Bug-AN regression: getattr(pm, 'starting_equity', 0) MUST "
        "return real value (the public property closes the typo gap)"
    )
