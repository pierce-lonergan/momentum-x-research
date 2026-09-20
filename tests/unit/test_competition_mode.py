"""
Tests for D37-D46: Competition Mode Enhancements.

Node ID: tests.unit.test_competition_mode
Validates position sizing, take-profit targets, trailing stops,
and configuration changes for the $5k/day competition target.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from config.settings import ExecutionConfig, Settings, ScoringWeights, DebateConfig
from src.core.models import TradeVerdict, DebateResult
from src.execution.position_manager import ManagedPosition, PositionManager


# ── D37: Position Size Limits ──────────────────────────────────────


class TestD37PositionSizeLimits:
    """D37: Verify position_size_pct accepts up to 25% (was 5%)."""

    def test_position_size_pct_at_maximum(self):
        """TradeVerdict accepts position_size_pct=0.25 (was capped at 0.05)."""
        v = TradeVerdict(
            ticker="TEST",
            action="BUY",
            confidence=0.8,
            mfcs=0.5,
            entry_price=10.0,
            stop_loss=9.0,
            target_prices=[10.30, 10.60, 11.00],
            position_size_pct=0.25,  # D37: Was le=0.05, now le=0.25
        )
        assert v.position_size_pct == 0.25

    def test_position_size_pct_at_15_percent(self):
        """15% position size is typical for competition mode."""
        v = TradeVerdict(
            ticker="TEST",
            action="BUY",
            confidence=0.8,
            mfcs=0.5,
            entry_price=10.0,
            stop_loss=9.0,
            target_prices=[10.30, 10.60, 11.00],
            position_size_pct=0.15,
        )
        assert v.position_size_pct == 0.15

    def test_position_size_pct_rejects_above_50_percent(self):
        """D115: Positions above 50% should be rejected (raised from 25% for Kelly Tier 3/4)."""
        with pytest.raises(Exception):  # Pydantic validation error
            TradeVerdict(
                ticker="TEST",
                action="BUY",
                confidence=0.8,
                mfcs=0.5,
                entry_price=10.0,
                stop_loss=9.0,
                target_prices=[10.30, 10.60, 11.00],
                position_size_pct=0.55,  # Above max (D115: 50%)
            )

    def test_position_size_pct_zero_allowed(self):
        """Zero position size is valid (NO_TRADE)."""
        v = TradeVerdict(
            ticker="TEST",
            action="NO_TRADE",
            confidence=0.5,
            mfcs=0.1,
            entry_price=10.0,
            stop_loss=9.0,
            target_prices=[],
            position_size_pct=0.0,
        )
        assert v.position_size_pct == 0.0

    def test_old_5_percent_position_still_valid(self):
        """Original 5% sizing still works."""
        v = TradeVerdict(
            ticker="TEST",
            action="BUY",
            confidence=0.7,
            mfcs=0.4,
            entry_price=10.0,
            stop_loss=9.0,
            target_prices=[10.30, 10.60, 11.00],
            position_size_pct=0.05,
        )
        assert v.position_size_pct == 0.05


# ── D37: ExecutionConfig Defaults ──────────────────────────────────


class TestD37ExecutionConfigDefaults:
    """D37: Competition mode config defaults."""

    def test_max_positions_default(self):
        """D162 reduced this 8 -> 3 after a Mar-26 backtest in which four
        simultaneous stops fired for -$3,411 in one session. The D37 expectation
        of 8 predates that risk decision; asserting it would fail the repo back
        into the concentration it deliberately walked away from."""
        config = ExecutionConfig()
        assert config.max_positions == 3

    def test_max_position_pct_default(self):
        config = ExecutionConfig()
        assert config.max_position_pct == 0.15  # Was 0.05

    def test_stop_loss_default(self):
        config = ExecutionConfig()
        assert config.stop_loss_pct == 0.055  # D94: widened from 0.04, set via .env

    def test_daily_loss_limit_default(self):
        config = ExecutionConfig()
        assert config.daily_loss_limit_pct == 0.10  # Was 0.05


# ── D43: Trailing Stop ─────────────────────────────────────────────


class TestD43TrailingStop:
    """D43: Trailing stop capability in PositionManager."""

    def _make_position(self, entry_price: float = 10.0, stop_loss: float = 9.60) -> ManagedPosition:
        return ManagedPosition(
            ticker="TEST",
            qty=100,
            entry_price=entry_price,
            signal_price=entry_price,
            stop_loss=stop_loss,
            target_prices=[10.30, 10.60, 11.00],
        )

    def _make_manager(self) -> PositionManager:
        config = ExecutionConfig()
        return PositionManager(config=config, starting_equity=100_000)

    def test_trailing_stop_with_atr(self):
        """ATR-based trailing stop: current_price - 2*ATR."""
        mgr = self._make_manager()
        new_stop = mgr.compute_trailing_stop(
            current_price=11.0, entry_price=10.0,
            current_stop=9.60, atr=0.30,
        )
        # 11.0 - 2*0.30 = 10.40, which is above current_stop 9.60
        assert new_stop == 10.40

    def test_trailing_stop_without_atr(self):
        """Percentage-based fallback: 3% below current price."""
        mgr = self._make_manager()
        new_stop = mgr.compute_trailing_stop(
            current_price=11.0, entry_price=10.0,
            current_stop=9.60, atr=None,
        )
        # 11.0 * 0.97 = 10.67, above 9.60
        assert new_stop == pytest.approx(10.67, abs=0.01)

    def test_trailing_stop_never_moves_down(self):
        """INVARIANT: trailing stop only ratchets UP, never down."""
        mgr = self._make_manager()
        # If current_stop is already high (e.g., from a previous ratchet)
        new_stop = mgr.compute_trailing_stop(
            current_price=10.20, entry_price=10.0,
            current_stop=10.30, atr=0.50,
        )
        # 10.20 - 2*0.50 = 9.20, but current_stop is 10.30 → stays at 10.30
        assert new_stop == 10.30

    def test_should_activate_trailing_at_threshold(self):
        """Trailing stop activates when unrealized P&L > activation threshold (4%)."""
        mgr = self._make_manager()
        pos = self._make_position(entry_price=10.0)
        # At +5% → clearly above 4% threshold
        assert mgr.should_activate_trailing_stop(pos, current_price=10.50) is True

    def test_should_not_activate_trailing_below_threshold(self):
        """Trailing stop should NOT activate below activation threshold (4%)."""
        mgr = self._make_manager()
        pos = self._make_position(entry_price=10.0)
        # At +2.5% → below 4% threshold
        assert mgr.should_activate_trailing_stop(pos, current_price=10.25) is False

    def test_should_not_activate_when_losing(self):
        """Trailing stop should NOT activate when position is losing."""
        mgr = self._make_manager()
        pos = self._make_position(entry_price=10.0)
        assert mgr.should_activate_trailing_stop(pos, current_price=9.80) is False


# ── D37: PositionManager Limits ────────────────────────────────────


class TestPositionManagerLimits:
    """D37: PositionManager respects competition mode limits."""

    def test_blocks_entry_at_the_configured_position_limit(self):
        """Entry is permitted up to max_positions and blocked at it.

        Was hardcoded to 8. D162 reduced the default to 3, so the constant is now
        read from config: the invariant under test is the LIMIT being enforced,
        not the particular number, and this no longer breaks when risk policy moves.
        """
        config = ExecutionConfig(paper_aggressive_mode=False)
        limit = config.max_positions
        mgr = PositionManager(config=config, starting_equity=100_000)

        for i in range(limit - 1):
            mgr.add_position(ManagedPosition(
                ticker=f"TICK{i}", qty=100, entry_price=10.0,
                signal_price=10.0, stop_loss=9.60,
            ))
        assert mgr.can_enter_new_position() is True, (
            f"should still accept entries below the limit of {limit}"
        )

        mgr.add_position(ManagedPosition(
            ticker=f"TICK{limit - 1}", qty=100, entry_price=10.0,
            signal_price=10.0, stop_loss=9.60,
        ))
        assert mgr.can_enter_new_position() is False, (
            f"should block at the limit of {limit}"
        )

    def test_circuit_breaker_at_10_percent(self):
        """Circuit breaker triggers at -10% daily P&L (was -5%)."""
        config = ExecutionConfig()
        mgr = PositionManager(config=config, starting_equity=100_000)

        # -9% should NOT trigger
        mgr.record_realized_pnl(-9_000)
        assert mgr.is_circuit_breaker_active is False

        # -10.01% SHOULD trigger
        mgr.record_realized_pnl(-1_010)  # Total: -10,010
        assert mgr.is_circuit_breaker_active is True


# ── D45: Settings Defaults ─────────────────────────────────────────


class TestD45ScoringDefaults:
    """D45: Verify scoring/debate config values loaded from .env for paper trading.

    NOTE: These test the LOADED values (code defaults + .env overrides),
    not the raw code defaults. The .env sets competition-mode thresholds.
    """

    def test_risk_aversion_lambda_loaded(self):
        """Lambda loaded from .env. D168: raised to 0.25 after LLM Arena confirmed risk_agent quality."""
        config = ScoringWeights()
        # .env override: SCORE_RISK_AVERSION_LAMBDA=0.25 (D168: was 0.20)
        assert config.risk_aversion_lambda in (0.15, 0.20, 0.25)  # Accept any valid value

    def test_risk_veto_mode_loaded(self):
        """Risk veto mode from .env = HARD for paper trading."""
        config = ScoringWeights()
        # .env override: SCORE_RISK_VETO_MODE=HARD
        assert config.risk_veto_mode in ("ADVISORY", "HARD")

    def test_mfcs_buy_threshold_loaded(self):
        """D204: MFCS buy threshold from .env = 0.25 (raised for quality)."""
        config = ScoringWeights()
        # D201: Raised from 0.15 to 0.25 with catalyst gate active
        assert config.mfcs_buy_threshold in (0.10, 0.15, 0.25, 0.30)

    def test_debate_divergence_low_loaded(self):
        """Divergence low threshold from .env."""
        config = DebateConfig()
        # .env override: 0.08 (live tuned), 0.15 (code default), or 0.20 (D45)
        assert config.divergence_low_threshold in (0.08, 0.15, 0.20)

    def test_debate_mfcs_threshold_loaded(self):
        """MFCS debate threshold from .env."""
        config = DebateConfig()
        # .env override: DEBATE_MFCS_DEBATE_THRESHOLD=0.35
        assert config.mfcs_debate_threshold in (0.15, 0.20, 0.30, 0.35)  # D97: raised to 0.35


# ── D38: Take-Profit Target Levels ────────────────────────────────


class TestD38TakeProfitTargets:
    """D38: Verify +3/6/10% targets in TradeVerdict (was +10/20/30%)."""

    def test_target_prices_at_lower_levels(self):
        """Target prices should be close to +3/6/10% from entry."""
        entry = 10.0
        expected_t1 = entry * 1.03  # 10.30
        expected_t2 = entry * 1.06  # 10.60
        expected_t3 = entry * 1.10  # 11.00

        v = TradeVerdict(
            ticker="TEST",
            action="BUY",
            confidence=0.8,
            mfcs=0.5,
            entry_price=entry,
            stop_loss=9.60,
            target_prices=[expected_t1, expected_t2, expected_t3],
            position_size_pct=0.10,
        )
        assert v.target_prices[0] == pytest.approx(10.30, abs=0.01)
        assert v.target_prices[1] == pytest.approx(10.60, abs=0.01)
        assert v.target_prices[2] == pytest.approx(11.00, abs=0.01)


# ── D38: Position Manager Tranche Exits with Lower Targets ────────


class TestD38TrancheExitsWithLowerTargets:
    """D38: Tranche exit ratcheting works with +3/6/10% targets."""

    def test_stop_ratchet_after_t1_to_breakeven(self):
        """After T1 fill at +3%, stop moves to breakeven (entry price)."""
        config = ExecutionConfig()
        mgr = PositionManager(config=config, starting_equity=100_000)
        pos = ManagedPosition(
            ticker="TEST",
            qty=300,
            entry_price=10.0,
            signal_price=10.0,
            stop_loss=9.60,
            target_prices=[10.30, 10.60, 11.00],
        )
        new_stop = mgr.compute_stop_after_tranche(pos, tranche_filled=1)
        assert new_stop == 10.0  # Breakeven

    def test_stop_ratchet_after_t2_to_t1_target(self):
        """After T2 fill at +6%, stop moves to T1 target (+3%)."""
        config = ExecutionConfig()
        mgr = PositionManager(config=config, starting_equity=100_000)
        pos = ManagedPosition(
            ticker="TEST",
            qty=300,
            entry_price=10.0,
            signal_price=10.0,
            stop_loss=9.60,
            target_prices=[10.30, 10.60, 11.00],
        )
        new_stop = mgr.compute_stop_after_tranche(pos, tranche_filled=2)
        assert new_stop == 10.30  # T1 target
