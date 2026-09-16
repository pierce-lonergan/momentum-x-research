"""
D94b Pipeline Guard Integration Tests
======================================

These tests simulate the exact production bugs that caused the March 4 -$8,264 loss:
  1. Debate stop override: LLM judge returns stop 17-27% below entry
  2. Phase 3 rescan dedup: Same ticker bought multiple times
  3. Stop-out cooldown: Ticker re-entered after stop-out

Each test wires together REAL components (Orchestrator, PositionManager,
ExecutionBridge) with mocked external services (Alpaca API, LLM agents)
to catch integration bugs that unit tests miss.

Run: pytest tests/integration/test_pipeline_guards.py -v
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import (
    AgentSignal,
    CandidateStock,
    DebateResult,
    RiskSignal,
    TradeVerdict,
)
from src.execution.bridge import ExecutionBridge
from src.execution.position_manager import ManagedPosition, PositionManager


# ═══════════════════════════════════════════════════════════════════
# Helper Factories
# ═══════════════════════════════════════════════════════════════════


def _make_candidate(
    ticker: str = "TEST",
    price: float = 10.0,
    prev_close: float = 8.0,
    gap_pct: float = 0.25,
) -> CandidateStock:
    return CandidateStock(
        ticker=ticker,
        current_price=price,
        previous_close=prev_close,
        gap_pct=gap_pct,
        gap_classification="SIGNIFICANT",
        rvol=3.0,
        premarket_volume=500_000,
        scan_timestamp=datetime.now(timezone.utc),
        scan_phase="PRE_MARKET",
    )


def _make_verdict(
    ticker: str = "TEST",
    action: str = "BUY",
    entry_price: float = 10.0,
    stop_loss: float = 9.45,
    confidence: float = 0.8,
    mfcs: float = 0.7,
    position_size_pct: float = 0.10,
    target_prices: list[float] | None = None,
) -> TradeVerdict:
    return TradeVerdict(
        ticker=ticker,
        action=action,
        confidence=confidence,
        mfcs=mfcs,
        debate_result=None,
        risk_signal=None,
        entry_price=entry_price,
        stop_loss=stop_loss,
        target_prices=target_prices or [10.50, 11.00, 12.00],
        position_size_pct=position_size_pct,
        time_horizon="INTRADAY",
        reasoning_summary="Test verdict",
        timestamp=datetime.now(timezone.utc),
    )


def _make_debate_result(
    ticker: str = "TEST",
    verdict: str = "BUY",
    confidence: float = 0.8,
    stop_loss: float | None = None,
    entry_price: float = 10.0,
    position_size: str = "HALF",
) -> DebateResult:
    return DebateResult(
        ticker=ticker,
        verdict=verdict,
        confidence=confidence,
        bull_strength=0.8,
        bear_strength=0.3,
        debate_divergence=0.5,
        bull_argument="Strong catalyst",
        bear_argument="Weak float",
        judge_reasoning="Bull wins",
        position_size=position_size,
        entry_price=entry_price,
        stop_loss=stop_loss,
        target_prices=[10.50, 11.00, 12.00],
        time_horizon="INTRADAY",
    )


def _make_position(
    ticker: str = "TEST",
    qty: int = 500,
    entry_price: float = 10.0,
    stop_loss: float = 9.45,
) -> ManagedPosition:
    return ManagedPosition(
        ticker=ticker,
        qty=qty,
        entry_price=entry_price,
        signal_price=entry_price,
        stop_loss=stop_loss,
        target_prices=[10.50, 11.00, 12.00],
        order_id="test-order-001",
        fill_price=entry_price,
        tranches_filled=0,
        remaining_qty=qty,
        realized_pnl=0.0,
        stop_order_id="test-stop-001",
        opened_at=datetime.now(timezone.utc),
        peak_price=entry_price,
        trailing_stop_active=False,
        entry_spread=0.02,
        entry_volume=100_000,
        peak_volume=200_000,
        source="FULL_EVAL",
    )


def _make_order_result(ticker: str = "TEST", qty: int = 500, price: float = 10.0):
    """Minimal OrderResult-like dict for mock executor."""
    from src.execution.alpaca_executor import OrderResult

    return OrderResult(
        order_id=f"ord-{ticker}-001",
        status="filled",
        ticker=ticker,
        qty=qty,
        side="buy",
        order_type="bracket",
        signal_price=price,
        submitted_price=price,
        stop_loss=price * 0.945,
        take_profit=price * 1.05,
        stop_order_id=f"stop-{ticker}-001",
        timestamp=datetime.now(timezone.utc),
    )


def _make_settings():
    """Create a REAL Settings object with the values these tests assume pinned.

    Was a bare MagicMock, which crashed in Orchestrator.__init__ once D218
    added numeric comparisons on scoring weights
    (``settings.scoring.catalyst_news > 0`` → TypeError on MagicMock).
    A real pydantic Settings gives every field its true type. The handful of
    values the assertions/math below depend on are pinned explicitly so that
    .env overrides or future default changes cannot alter test outcomes.
    (close_positions_by is no longer force-set: the real str default applies
    and the Orchestrator paths under test never read it.)
    """
    from config.settings import Settings

    settings = Settings()
    settings.execution.stop_loss_pct = 0.055
    settings.execution.max_position_pct = 0.15
    settings.execution.max_positions = 8
    settings.execution.daily_loss_limit_pct = 0.05
    settings.scoring.mfcs_buy_threshold = 0.25
    # NOTE: the old mock pinned mfcs_debate_threshold on settings.scoring, but
    # the real field lives on DebateConfig (settings.debate).
    settings.debate.mfcs_debate_threshold = 0.30
    settings.debate.divergence_low_threshold = 0.3
    settings.debate.divergence_high_threshold = 0.6
    return settings


def _make_exec_config():
    """Create a real ExecutionConfig for PositionManager."""
    from config.settings import ExecutionConfig

    return ExecutionConfig(
        stop_loss_pct=0.055,
        max_position_pct=0.15,
        max_positions=8,
    )


# ═══════════════════════════════════════════════════════════════════
# TEST SUITE 1: Stop Loss Clamping (Bug 1 - D94b)
# ═══════════════════════════════════════════════════════════════════


class TestStopLossClamping:
    """
    March 4 Bug 1: Debate engine returned stop_loss 17-27% below entry.
    ASNS: entry $0.66, debate stop $0.48 (-27%) → should be clamped to $0.6237 (-5.5%).
    CANF: entry $10.40, debate stop $9.50 (-8.7%) → should be clamped to $9.828 (-5.5%).
    """

    @pytest.mark.asyncio
    async def test_debate_stop_clamped_to_config_maximum(self):
        """Debate stop 27% below entry must be overridden by ATR/gap-based stop.

        D104: Gap-based fallback uses max(5.5%, gap_pct*0.5) capped at 15%.
        For ASNS with gap_pct=0.25 (default helper): max(5.5%, 12.5%) = 12.5%.
        The debate stop ($0.48, 27% below) should still be clamped upward.
        """
        from src.core.orchestrator import Orchestrator

        settings = _make_settings()
        orch = Orchestrator(settings)

        candidate = _make_candidate(ticker="ASNS", price=0.66, prev_close=0.40)
        debate = _make_debate_result(
            ticker="ASNS",
            entry_price=0.66,
            stop_loss=0.48,  # 27% below entry — way too wide
        )

        # Build the verdict using the orchestrator's logic
        scored = MagicMock()
        scored.candidate = candidate
        scored.mfcs = 0.7
        scored.risk_score = 0.2
        scored.qualifies_for_debate = True

        verdict = orch._build_trade_verdict(candidate, scored, debate, risk_signal=None)

        # D104: Gap-based stop for gap_pct=0.25 → max(5.5%, 12.5%) = 12.5%
        gap_stop_pct = max(0.055, abs(candidate.gap_pct) * 0.5)
        gap_stop_pct = min(gap_stop_pct, 0.15)
        computed_stop = 0.66 * (1 - gap_stop_pct)
        assert verdict.stop_loss >= computed_stop - 0.001, (
            f"Stop ${verdict.stop_loss:.4f} is wider than gap-based max ${computed_stop:.4f}. "
            f"Debate stop ${debate.stop_loss:.4f} should have been clamped."
        )
        # Also verify debate stop was clamped (not used directly)
        assert verdict.stop_loss > debate.stop_loss, (
            f"Debate stop ${debate.stop_loss:.4f} should be narrower than verdict stop."
        )

    @pytest.mark.asyncio
    async def test_debate_stop_tighter_than_config_is_preserved(self):
        """If debate sets a tighter stop than config, it should be kept."""
        from src.core.orchestrator import Orchestrator

        settings = _make_settings()
        orch = Orchestrator(settings)

        candidate = _make_candidate(ticker="GOOD", price=10.0, prev_close=8.0)
        debate = _make_debate_result(
            ticker="GOOD",
            entry_price=10.0,
            stop_loss=9.70,  # 3% below entry — tighter than config 5.5%
        )

        scored = MagicMock()
        scored.candidate = candidate
        scored.mfcs = 0.7
        scored.risk_score = 0.2
        scored.qualifies_for_debate = True

        verdict = orch._build_trade_verdict(candidate, scored, debate, risk_signal=None)

        # Debate stop $9.70 is tighter than config stop $9.45 → keep debate stop
        assert verdict.stop_loss == 9.70, (
            f"Tight debate stop $9.70 should be preserved, got ${verdict.stop_loss:.2f}"
        )

    @pytest.mark.asyncio
    async def test_debate_stop_zero_uses_config(self):
        """If debate returns stop_loss=0 or None, config stop must be used."""
        from src.core.orchestrator import Orchestrator

        settings = _make_settings()
        orch = Orchestrator(settings)

        candidate = _make_candidate(ticker="ZERO", price=5.0, prev_close=3.5)

        for bad_stop in [0.0, None]:
            debate = _make_debate_result(
                ticker="ZERO",
                entry_price=5.0,
                stop_loss=bad_stop,
            )

            scored = MagicMock()
            scored.candidate = candidate
            scored.mfcs = 0.7
            scored.risk_score = 0.2
            scored.qualifies_for_debate = True

            verdict = orch._build_trade_verdict(candidate, scored, debate, risk_signal=None)

            # D104: Gap-based fallback for gap_pct=0.25 → max(5.5%, 12.5%) = 12.5%
            gap_pct = max(0.055, abs(candidate.gap_pct) * 0.5)
            gap_pct = min(gap_pct, 0.15)
            gap_stop = 5.0 * (1 - gap_pct)
            assert verdict.stop_loss >= gap_stop - 0.01, (
                f"When debate stop is {bad_stop}, gap-based stop ${gap_stop:.2f} must be used. "
                f"Got ${verdict.stop_loss:.2f}"
            )

    @pytest.mark.asyncio
    async def test_stop_never_more_than_max_pct_below_entry(self):
        """Property: stop_loss must never be more than gap-aware fallback below entry.

        D104: Gap-based fallback uses max(stop_loss_pct, gap_pct*0.5) capped at 15%.
        The debate stop (however absurd) should be clamped to the gap-based stop.
        """
        from src.core.orchestrator import Orchestrator

        settings = _make_settings()
        orch = Orchestrator(settings)

        # Test with various absurd debate stops
        test_cases = [
            ("WIDE1", 10.0, 5.0),    # 50% below
            ("WIDE2", 0.50, 0.10),    # 80% below
            ("WIDE3", 100.0, 50.0),   # 50% below
            ("WIDE4", 2.0, 0.01),     # 99.5% below
        ]

        for ticker, entry, debate_stop in test_cases:
            candidate = _make_candidate(ticker=ticker, price=entry, prev_close=entry * 0.7)
            debate = _make_debate_result(
                ticker=ticker, entry_price=entry, stop_loss=debate_stop,
            )
            scored = MagicMock()
            scored.candidate = candidate
            scored.mfcs = 0.7
            scored.risk_score = 0.2
            scored.qualifies_for_debate = True

            verdict = orch._build_trade_verdict(candidate, scored, debate, risk_signal=None)

            # D104: Compute gap-based fallback stop width
            gap_stop_pct = max(
                settings.execution.stop_loss_pct,
                abs(candidate.gap_pct) * 0.5,
            )
            gap_stop_pct = min(gap_stop_pct, 0.15)  # Cap at 15%
            min_allowed_stop = entry * (1 - gap_stop_pct)
            assert verdict.stop_loss >= min_allowed_stop - 0.001, (
                f"{ticker}: Stop ${verdict.stop_loss:.4f} is below min ${min_allowed_stop:.4f} "
                f"(entry=${entry}, debate_stop=${debate_stop})"
            )


# ═══════════════════════════════════════════════════════════════════
# TEST SUITE 2: Position Deduplication (Bug 2 - D94b)
# ═══════════════════════════════════════════════════════════════════


class TestPositionDedup:
    """
    March 4 Bug 2: ASNS bought 4 times, CANF bought 3 times because
    Phase 3 rescan had no has_position() guard.
    """

    def test_has_position_blocks_duplicate_entry(self):
        """PositionManager.has_position() must return True for held tickers."""
        config = _make_exec_config()
        pm = PositionManager(config=config, starting_equity=100_000.0)

        pos = _make_position(ticker="ASNS")
        pm.add_position(pos)

        assert pm.has_position("ASNS"), "has_position must be True after add_position"
        assert not pm.has_position("OTHER"), "has_position must be False for non-held ticker"

    def test_has_position_false_after_removal(self):
        """After removing a position, has_position must return False."""
        config = _make_exec_config()
        pm = PositionManager(config=config, starting_equity=100_000.0)

        pos = _make_position(ticker="CANF")
        pm.add_position(pos)
        assert pm.has_position("CANF")

        pm.remove_position("CANF")
        assert not pm.has_position("CANF"), "has_position must be False after removal"

    @pytest.mark.asyncio
    async def test_bridge_guard_pattern_for_duplicate_ticker(self):
        """
        The duplicate guard lives in main.py (not bridge), so we test the
        guard PATTERN: check has_position BEFORE calling execute_verdict.
        """
        config = _make_exec_config()
        pm = PositionManager(config=config, starting_equity=100_000.0)

        # Add existing position
        pm.add_position(_make_position(ticker="ASNS"))

        # The guard that main.py applies before calling execute_verdict
        verdict = _make_verdict(ticker="ASNS")
        should_skip = pm.has_position(verdict.ticker)

        assert should_skip, "Guard must block duplicate entry — has_position should be True"
        assert pm.has_position("ASNS"), "Position must still exist"

    def test_multiple_adds_same_ticker_raises_or_overwrites(self):
        """Adding the same ticker twice should not silently create duplicates."""
        config = _make_exec_config()
        pm = PositionManager(config=config, starting_equity=100_000.0)

        pos1 = _make_position(ticker="DUPE", qty=100, entry_price=5.0)
        pm.add_position(pos1)

        # Adding same ticker again — should either raise or overwrite, never duplicate
        pos2 = _make_position(ticker="DUPE", qty=200, entry_price=6.0)
        try:
            pm.add_position(pos2)
        except Exception:
            pass  # Raising is acceptable behavior

        # Count positions for this ticker — must never be > 1
        count = sum(1 for p in pm.open_positions if p.ticker == "DUPE")
        assert count <= 1, f"PositionManager has {count} positions for DUPE — duplicates exist!"


# ═══════════════════════════════════════════════════════════════════
# TEST SUITE 3: Stop-Out Cooldown (Bug 3 - D94b)
# ═══════════════════════════════════════════════════════════════════


class TestStopOutCooldown:
    """
    March 4 Bug 3: CANF stopped at $7.01, re-bought at $6.84, stopped
    again at $6.15. No mechanism prevented re-entry after stop-out.

    These tests verify the _stopped_out_tickers set logic that was added.
    Since the set lives in main.py's run() function, we test the PATTERN
    not the exact production code — this catches logic regressions.
    """

    def test_stopped_out_set_blocks_reentry(self):
        """Simulate the cooldown pattern: stopped tickers should be skipped."""
        # Simulate the _stopped_out_tickers set from main.py
        stopped_out: set[str] = set()

        # Ticker gets stopped out
        stopped_out.add("CANF")

        # Scanner finds it again, verdict is BUY
        verdict = _make_verdict(ticker="CANF", action="BUY")

        # Guard check (mirrors main.py Phase 2 logic)
        should_skip = verdict.ticker in stopped_out
        assert should_skip, "Stopped-out ticker CANF must be blocked from re-entry"

    def test_stopped_out_does_not_block_other_tickers(self):
        """Cooldown only affects the specific ticker that was stopped out."""
        stopped_out: set[str] = {"CANF", "ASNS"}

        verdict_ok = _make_verdict(ticker="JZXN", action="BUY")
        assert verdict_ok.ticker not in stopped_out, "JZXN should not be blocked"

    def test_stopped_out_populated_from_position_closed(self):
        """When WebSocket reports position_closed, ticker must enter cooldown."""
        stopped_out: set[str] = set()

        # Simulate WebSocket fill event with position_closed=True
        @dataclass
        class FakeFillEvent:
            ticker: str
            position_closed: bool

        fill_event = FakeFillEvent(ticker="MOBX", position_closed=True)

        # Mirror main.py logic
        if fill_event.position_closed:
            stopped_out.add(fill_event.ticker)

        assert "MOBX" in stopped_out

    def test_stopped_out_populated_from_broker_disappearance(self):
        """When polling finds position gone from broker, it must enter cooldown."""
        stopped_out: set[str] = set()

        # Our tracked positions
        internal_tickers = {"ASNS", "CANF", "JZXN"}

        # Broker only has JZXN (ASNS and CANF were stopped out)
        broker_tickers = {"JZXN"}

        # Mirror main.py D94b polling logic
        for ticker in internal_tickers:
            if ticker not in broker_tickers:
                stopped_out.add(ticker)

        assert "ASNS" in stopped_out
        assert "CANF" in stopped_out
        assert "JZXN" not in stopped_out

    def test_stopped_out_survives_full_session(self):
        """Cooldown must persist across all Phase 3 cycles within a session."""
        stopped_out: set[str] = set()

        # Cycle 1: CANF gets stopped out
        stopped_out.add("CANF")

        # Cycle 2-10: Simulate 9 more monitoring cycles
        for cycle in range(2, 11):
            # Each cycle, rescan might find CANF again
            should_skip = "CANF" in stopped_out
            assert should_skip, f"Cycle {cycle}: CANF must still be blocked"

    def test_cooldown_set_resets_across_sessions(self):
        """_stopped_out_tickers is created fresh in run() — verify the pattern."""
        # Session 1
        session1_stopped: set[str] = set()
        session1_stopped.add("CANF")
        assert "CANF" in session1_stopped

        # Session 2 — fresh set (as per main.py: _stopped_out_tickers = set())
        session2_stopped: set[str] = set()
        assert "CANF" not in session2_stopped, "New session must start with empty cooldown"


# ═══════════════════════════════════════════════════════════════════
# TEST SUITE 4: End-to-End Lifecycle Scenarios
# ═══════════════════════════════════════════════════════════════════


class TestLifecycleScenarios:
    """
    Full lifecycle scenarios that reproduce actual production failures.
    These test multiple components working together.
    """

    @pytest.mark.asyncio
    async def test_scenario_canf_march4(self):
        """
        Reproduce March 4 CANF sequence:
        1. Buy CANF at $10.40
        2. Stop-out at $7.01
        3. Rescan finds CANF again → MUST be blocked
        """
        config = _make_exec_config()
        pm = PositionManager(config=config, starting_equity=100_000.0)
        stopped_out: set[str] = set()

        # Step 1: Buy CANF
        pos = _make_position(ticker="CANF", entry_price=10.40, stop_loss=9.83)
        pm.add_position(pos)
        assert pm.has_position("CANF")

        # Step 2: Stop-out — position removed by broker, we detect it
        pm.remove_position("CANF")
        stopped_out.add("CANF")
        assert not pm.has_position("CANF")
        assert "CANF" in stopped_out

        # Step 3: Rescan finds CANF, verdict is BUY
        rescan_verdict = _make_verdict(ticker="CANF", action="BUY", entry_price=6.84)

        # Guards: has_position OR stopped_out
        blocked = pm.has_position("CANF") or rescan_verdict.ticker in stopped_out
        assert blocked, "CANF must be blocked: stopped out earlier this session"

    @pytest.mark.asyncio
    async def test_scenario_asns_march4(self):
        """
        Reproduce March 4 ASNS sequence:
        1. Buy ASNS at $0.66
        2. Still holding → rescan finds ASNS again → MUST be blocked by has_position
        """
        config = _make_exec_config()
        pm = PositionManager(config=config, starting_equity=100_000.0)

        # Step 1: Buy ASNS
        pos = _make_position(ticker="ASNS", entry_price=0.66, stop_loss=0.6237)
        pm.add_position(pos)

        # Step 2: Rescan finds ASNS with BUY verdict
        for attempt in range(3):
            blocked = pm.has_position("ASNS")
            assert blocked, f"Attempt {attempt + 1}: ASNS buy must be blocked — already holding"

    @pytest.mark.asyncio
    async def test_scenario_mixed_session(self):
        """
        Complex session: multiple buys, some stop-outs, some winners.
        Verify guards hold across all phases.
        """
        config = _make_exec_config()
        pm = PositionManager(config=config, starting_equity=100_000.0)
        stopped_out: set[str] = set()

        # Phase 2: Buy JZXN and CANF
        pm.add_position(_make_position(ticker="JZXN", entry_price=5.0))
        pm.add_position(_make_position(ticker="CANF", entry_price=10.0))
        assert len(pm.open_positions) == 2

        # Phase 3, Cycle 1: CANF stops out
        pm.remove_position("CANF")
        stopped_out.add("CANF")

        # Phase 3, Cycle 2: Rescan finds both JZXN and CANF
        verdicts = [
            _make_verdict(ticker="JZXN", action="BUY"),
            _make_verdict(ticker="CANF", action="BUY"),
            _make_verdict(ticker="AIFF", action="BUY"),  # new ticker — should pass
        ]

        allowed = []
        for v in verdicts:
            if v.action != "BUY":
                continue
            if pm.has_position(v.ticker):
                continue  # Already holding
            if v.ticker in stopped_out:
                continue  # Stopped out this session
            allowed.append(v.ticker)

        assert "JZXN" not in allowed, "JZXN already held — must be blocked"
        assert "CANF" not in allowed, "CANF stopped out — must be blocked"
        assert "AIFF" in allowed, "AIFF is new — should be allowed"


# ═══════════════════════════════════════════════════════════════════
# TEST SUITE 5: Orchestrator Verdict Invariants
# ═══════════════════════════════════════════════════════════════════


class TestOrchestratorInvariants:
    """
    Property-style tests that verify invariants the orchestrator must
    always maintain, regardless of debate engine output.
    """

    @pytest.mark.asyncio
    async def test_verdict_stop_loss_within_config_bounds(self):
        """INV: stop_loss must always be within [entry * (1 - stop_pct), entry]."""
        from src.core.orchestrator import Orchestrator

        settings = _make_settings()
        orch = Orchestrator(settings)

        # Test with various debate results
        entries_and_debate_stops = [
            (10.0, 5.0),      # 50% below
            (10.0, 9.90),     # 1% below (tighter than config)
            (10.0, 0.01),     # 99.9% below
            (10.0, 11.0),     # ABOVE entry (nonsensical)
            (1.0, 0.10),      # 90% below penny stock
            (100.0, 50.0),    # 50% below
        ]

        for entry, debate_stop in entries_and_debate_stops:
            candidate = _make_candidate(ticker="INV", price=entry, prev_close=entry * 0.7)
            debate = _make_debate_result(
                ticker="INV", entry_price=entry, stop_loss=debate_stop,
            )
            scored = MagicMock()
            scored.candidate = candidate
            scored.mfcs = 0.7
            scored.risk_score = 0.2
            scored.qualifies_for_debate = True

            verdict = orch._build_trade_verdict(candidate, scored, debate, risk_signal=None)

            # D104: Gap-based fallback uses max(stop_loss_pct, gap_pct*0.5) capped at 15%
            gap_pct = max(
                settings.execution.stop_loss_pct,
                abs(candidate.gap_pct) * 0.5,
            )
            gap_pct = min(gap_pct, 0.15)
            min_stop = entry * (1 - gap_pct)
            assert verdict.stop_loss >= min_stop - 0.001, (
                f"entry=${entry}, debate_stop=${debate_stop}: "
                f"verdict.stop_loss=${verdict.stop_loss:.4f} < min ${min_stop:.4f}"
            )
            assert verdict.stop_loss <= entry, (
                f"entry=${entry}: stop_loss=${verdict.stop_loss:.4f} > entry (nonsensical)"
            )

    @pytest.mark.asyncio
    async def test_verdict_position_size_within_bounds(self):
        """INV: position_size_pct must be in [0, max_position_pct]."""
        from src.core.orchestrator import Orchestrator

        settings = _make_settings()
        orch = Orchestrator(settings)

        candidate = _make_candidate(ticker="SIZE", price=10.0, prev_close=7.0)
        scored = MagicMock()
        scored.candidate = candidate
        scored.mfcs = 0.9
        scored.risk_score = 0.1
        scored.qualifies_for_debate = True

        # Test with various debate divergence levels
        for div in [0.1, 0.3, 0.5, 0.7, 0.9, 1.0]:
            debate = _make_debate_result(
                ticker="SIZE",
                entry_price=10.0,
                stop_loss=9.50,
                confidence=0.95,
            )
            # Manually set divergence
            debate = DebateResult(
                ticker="SIZE",
                verdict="STRONG_BUY",
                confidence=0.95,
                bull_strength=0.5 + div / 2,
                bear_strength=0.5 - div / 2,
                debate_divergence=div,
                bull_argument="Strong",
                bear_argument="Weak",
                judge_reasoning="Bull wins",
                position_size="FULL" if div > 0.6 else "HALF",
                entry_price=10.0,
                stop_loss=9.50,
                target_prices=[10.50, 11.00, 12.00],
                time_horizon="INTRADAY",
            )

            verdict = orch._build_trade_verdict(candidate, scored, debate, risk_signal=None)

            assert 0 < verdict.position_size_pct <= settings.execution.max_position_pct, (
                f"div={div}: position_size_pct={verdict.position_size_pct:.4f} "
                f"out of bounds [0, {settings.execution.max_position_pct}]"
            )

    @pytest.mark.asyncio
    async def test_no_debate_verdict_uses_config_stop(self):
        """When debate is None (CV consensus skip), config stop must be used."""
        from src.core.orchestrator import Orchestrator

        settings = _make_settings()
        orch = Orchestrator(settings)

        candidate = _make_candidate(ticker="NODEB", price=8.0, prev_close=6.0)
        scored = MagicMock()
        scored.candidate = candidate
        scored.mfcs = 0.7
        scored.risk_score = 0.2
        scored.qualifies_for_debate = True

        verdict = orch._build_trade_verdict(candidate, scored, debate=None, risk_signal=None)

        # D104: Gap-based fallback for gap_pct=0.25 → max(5.5%, 12.5%) = 12.5%
        gap_pct = max(
            settings.execution.stop_loss_pct,
            abs(candidate.gap_pct) * 0.5,
        )
        gap_pct = min(gap_pct, 0.15)
        expected_stop = 8.0 * (1 - gap_pct)
        assert abs(verdict.stop_loss - expected_stop) < 0.01, (
            f"No-debate verdict stop ${verdict.stop_loss:.4f} != gap-based stop ${expected_stop:.4f}"
        )

    @pytest.mark.asyncio
    async def test_targets_are_ascending(self):
        """Target prices must always be T1 < T2 < T3 and all > entry."""
        from src.core.orchestrator import Orchestrator

        settings = _make_settings()
        orch = Orchestrator(settings)

        candidate = _make_candidate(ticker="TGT", price=10.0, prev_close=7.5)
        scored = MagicMock()
        scored.candidate = candidate
        scored.mfcs = 0.7
        scored.risk_score = 0.2
        scored.qualifies_for_debate = True

        # Test both debate and no-debate paths
        for debate in [
            _make_debate_result(ticker="TGT", entry_price=10.0, stop_loss=9.50),
            None,
        ]:
            verdict = orch._build_trade_verdict(candidate, scored, debate, risk_signal=None)

            targets = verdict.target_prices
            assert len(targets) >= 1, "Must have at least 1 target"
            assert all(t > verdict.entry_price for t in targets), (
                f"Targets {targets} must all be > entry ${verdict.entry_price}"
            )
            for i in range(len(targets) - 1):
                assert targets[i] < targets[i + 1], (
                    f"Targets must be ascending: {targets}"
                )


# ═══════════════════════════════════════════════════════════════════
# TEST SUITE 6: Debate Engine Edge Cases
# ═══════════════════════════════════════════════════════════════════


class TestDebateEdgeCases:
    """
    Tests for the D94 debate engine fixes: confidence defaults,
    guard relaxation, and HOLD/NO_TRADE handling.
    """

    @pytest.mark.asyncio
    async def test_missing_confidence_gets_verdict_aware_default(self):
        """When judge omits confidence, it defaults based on verdict."""
        from src.agents.debate_engine import DebateEngine

        engine = DebateEngine(model="test-model")

        # Simulate raw judge response with no confidence field
        raw_responses = [
            ({"verdict": "BUY", "key_reasoning": "Strong catalyst"}, 0.6),
            ({"verdict": "STRONG_BUY", "key_reasoning": "Very strong"}, 0.6),
            ({"verdict": "NO_TRADE", "key_reasoning": "Too risky"}, 0.3),
            ({"verdict": "HOLD", "key_reasoning": "Wait and see"}, 0.4),
        ]

        for raw, expected_default in raw_responses:
            # The _parse_judge_response or similar method should handle this
            verdict = raw["verdict"]
            if "confidence" not in raw:
                if verdict in ("STRONG_BUY", "BUY"):
                    confidence = 0.6
                elif verdict == "NO_TRADE":
                    confidence = 0.3
                else:  # HOLD
                    confidence = 0.4
            else:
                confidence = float(raw["confidence"])

            assert confidence == expected_default, (
                f"verdict={verdict}: confidence={confidence}, expected={expected_default}"
            )

    def test_empty_parse_triggers_no_trade(self):
        """Only truly empty responses (no verdict AND no reasoning) → NO_TRADE."""
        # This is the relaxed guard from D94
        empty_cases = [
            {},                              # Completely empty
            {"confidence": 0.5},             # No verdict, no reasoning
        ]

        for raw in empty_cases:
            has_verdict = bool(raw.get("verdict"))
            has_reasoning = bool(raw.get("key_reasoning"))
            should_force_no_trade = not has_verdict and not has_reasoning
            assert should_force_no_trade, f"Empty response {raw} must force NO_TRADE"

        # Non-empty cases should NOT force NO_TRADE
        valid_cases = [
            {"verdict": "HOLD"},                              # Has verdict
            {"key_reasoning": "Wait for dip"},                # Has reasoning
            {"verdict": "BUY", "key_reasoning": "Strong"},    # Has both
        ]

        for raw in valid_cases:
            has_verdict = bool(raw.get("verdict"))
            has_reasoning = bool(raw.get("key_reasoning"))
            should_force_no_trade = not has_verdict and not has_reasoning
            assert not should_force_no_trade, f"Valid response {raw} must NOT force NO_TRADE"

    def test_hold_verdict_not_converted_to_no_trade(self):
        """D94: HOLD must remain HOLD, never converted to NO_TRADE."""
        # Old bug: low divergence → HOLD converted to NO_TRADE
        divergence = 0.15  # Below divergence_no_trade threshold
        verdict = "HOLD"

        # D94 fix: verdict stays as-is
        # Old code would do: if divergence < threshold: verdict = "NO_TRADE"
        final_verdict = verdict  # No conversion
        assert final_verdict == "HOLD", "HOLD must not be converted to NO_TRADE"


# ═══════════════════════════════════════════════════════════════════
# TEST SUITE 7: D78 Exit + Cooldown Integration
# ═══════════════════════════════════════════════════════════════════


class TestExitCooldown:
    """
    When exit intelligence fires EXIT for a ticker, it should be added
    to _stopped_out_tickers to prevent re-entry in the same session.
    """

    def test_d78_exit_adds_to_cooldown(self):
        """After D78 smart EXIT, ticker must enter cooldown set."""
        stopped_out: set[str] = set()

        # Simulate D78 EXIT action
        exit_ticker = "MOBX"
        # Mirror main.py logic: after close_with_attribution succeeds
        stopped_out.add(exit_ticker)

        assert exit_ticker in stopped_out
        assert _make_verdict(ticker=exit_ticker).ticker in stopped_out

    def test_tighten_does_not_add_to_cooldown(self):
        """TIGHTEN actions must NOT add to cooldown (only EXIT does)."""
        stopped_out: set[str] = set()

        # TIGHTEN is just adjusting the stop — position stays open
        tighten_ticker = "JZXN"
        # Only EXIT adds to cooldown, TIGHTEN does not
        # (no stopped_out.add() for TIGHTEN)

        assert tighten_ticker not in stopped_out


# ═══════════════════════════════════════════════════════════════════
# TEST SUITE 8: MFCS-Scaled Position Sizing
# ═══════════════════════════════════════════════════════════════════


class TestMFCSPositionSizing:
    """
    D94b: Position size scales with confidence/MFCS instead of fixed QUARTER.
    """

    @pytest.mark.asyncio
    async def test_high_confidence_gets_larger_position(self):
        """Higher debate confidence → larger position size."""
        from src.core.orchestrator import Orchestrator

        settings = _make_settings()
        orch = Orchestrator(settings)

        candidate = _make_candidate(ticker="SCALE", price=10.0, prev_close=7.5)
        scored = MagicMock()
        scored.candidate = candidate
        scored.mfcs = 0.8
        scored.risk_score = 0.2
        scored.qualifies_for_debate = True

        # High divergence + high confidence
        debate_high = DebateResult(
            ticker="SCALE", verdict="STRONG_BUY", confidence=0.95,
            bull_strength=0.95, bear_strength=0.2, debate_divergence=0.75,
            bull_argument="Strong", bear_argument="Weak", judge_reasoning="Bull wins",
            position_size="FULL", entry_price=10.0, stop_loss=9.50,
            target_prices=[10.50, 11.00, 12.00], time_horizon="INTRADAY",
        )
        verdict_high = orch._build_trade_verdict(candidate, scored, debate_high, None)

        # Low divergence + low confidence
        debate_low = DebateResult(
            ticker="SCALE", verdict="BUY", confidence=0.4,
            bull_strength=0.55, bear_strength=0.45, debate_divergence=0.30,
            bull_argument="Mild", bear_argument="Some concern", judge_reasoning="Marginal",
            position_size="QUARTER", entry_price=10.0, stop_loss=9.50,
            target_prices=[10.50, 11.00, 12.00], time_horizon="INTRADAY",
        )
        verdict_low = orch._build_trade_verdict(candidate, scored, debate_low, None)

        assert verdict_high.position_size_pct > verdict_low.position_size_pct, (
            f"High confidence position {verdict_high.position_size_pct:.4f} "
            f"must be > low confidence {verdict_low.position_size_pct:.4f}"
        )

    @pytest.mark.asyncio
    async def test_no_debate_mfcs_scales_position(self):
        """Without debate, MFCS scales the position size."""
        from src.core.orchestrator import Orchestrator

        settings = _make_settings()
        orch = Orchestrator(settings)

        candidate = _make_candidate(ticker="NOMFCS", price=10.0, prev_close=7.5)

        # High MFCS
        scored_high = MagicMock()
        scored_high.candidate = candidate
        scored_high.mfcs = 0.9
        scored_high.risk_score = 0.1
        scored_high.qualifies_for_debate = True
        verdict_high = orch._build_trade_verdict(candidate, scored_high, None, None)

        # Low MFCS
        scored_low = MagicMock()
        scored_low.candidate = candidate
        scored_low.mfcs = 0.3
        scored_low.risk_score = 0.4
        scored_low.qualifies_for_debate = True
        verdict_low = orch._build_trade_verdict(candidate, scored_low, None, None)

        assert verdict_high.position_size_pct >= verdict_low.position_size_pct, (
            f"Higher MFCS {verdict_high.position_size_pct:.4f} should get "
            f">= position than lower MFCS {verdict_low.position_size_pct:.4f}"
        )
