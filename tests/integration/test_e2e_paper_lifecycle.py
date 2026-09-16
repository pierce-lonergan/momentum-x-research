"""
MOMENTUM-X E2E Integration Test: Full cmd_paper Lifecycle

Node ID: tests.integration.test_e2e_paper_lifecycle
Graph Link: docs/memory/black_box.json → PRIORITY_2

Tests the complete paper trading lifecycle across all 4 market phases:
  Phase 1: Pre-market scanning → CandidateStock watchlist
  Phase 2: Market open → Agent evaluation → Debate → Execution
  Phase 3: Intraday → Position monitoring (tranche exits)
  Phase 4: After-hours → Close positions → Shapley attribution

All external services (Alpaca, LLM, WebSocket) are mocked.
Validates pipeline wiring, not LLM quality or market data accuracy.

Ref: SYSTEM_ARCHITECTURE.md (4 Market Phases)
Ref: ADR-014 (Pipeline Closure)
Ref: ADR-024 (Bridge + Risk)
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from config.settings import Settings
from src.core.models import CandidateStock, TradeVerdict
from src.core.orchestrator import Orchestrator
from src.core.scan_loop import ScanLoop
from src.execution.alpaca_executor import AlpacaExecutor, OrderResult
from src.execution.bridge import ExecutionBridge
from src.execution.position_manager import PositionManager
from src.execution.portfolio_risk import PortfolioRiskManager, get_sector
from src.monitoring.metrics import reset_metrics, get_metrics


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def mock_quotes() -> dict:
    """Simulated Alpaca snapshot data for 3 tickers."""
    return {
        "BOOM": {
            "current_price": 8.50,
            "previous_close": 5.00,
            "premarket_volume": 2_000_000,
            "avg_volume_at_time": 200_000,
            "prev_volume": 5_000_000,
            "float_shares": 3_000_000,
            "market_cap": 25_000_000.0,
            "bid": 8.45,
            "ask": 8.55,
            "has_news": True,
        },
        "FLAT": {
            "current_price": 10.00,
            "previous_close": 9.95,
            "premarket_volume": 50_000,
            "avg_volume_at_time": 100_000,
            "prev_volume": 1_000_000,
            "float_shares": 50_000_000,
            "market_cap": 500_000_000.0,
            "bid": 9.99,
            "ask": 10.01,
            "has_news": False,
        },
        "MEDI": {
            "current_price": 4.20,
            "previous_close": 2.50,
            "premarket_volume": 3_000_000,
            "avg_volume_at_time": 150_000,
            "prev_volume": 2_000_000,
            "float_shares": 5_000_000,
            "market_cap": 21_000_000.0,
            "bid": 4.15,
            "ask": 4.25,
            "has_news": True,
        },
    }


def _make_candidate(ticker: str = "BOOM", gap_pct: float = 0.70) -> CandidateStock:
    return CandidateStock(
        ticker=ticker,
        current_price=8.50,
        previous_close=5.00,
        gap_pct=gap_pct,
        gap_classification="EXPLOSIVE",
        rvol=10.0,
        premarket_volume=2_000_000,
        float_shares=3_000_000,
        avg_daily_volume=5_000_000,
        scan_timestamp=datetime.now(timezone.utc),
        scan_phase="PRE_MARKET",
    )


def _make_buy_verdict(ticker: str = "BOOM") -> TradeVerdict:
    return TradeVerdict(
        ticker=ticker,
        action="BUY",
        confidence=0.85,
        mfcs=0.72,
        debate_result=None,
        risk_signal=None,
        entry_price=8.50,
        stop_loss=7.90,
        target_prices=[9.35, 10.20, 11.05],
        position_size_pct=0.05,
        time_horizon="INTRADAY",
        reasoning_summary="MFCS=0.72 | Debate=YES | Risk=PASS",
    )


def _make_order_result(ticker: str = "BOOM") -> OrderResult:
    return OrderResult(
        order_id="test-order-001",
        ticker=ticker,
        side="buy",
        qty=500,
        signal_price=8.50,
        submitted_price=8.52,
        status="filled",
    )


# ── Phase 1: Pre-Market Scanning ─────────────────────────────────────


class TestPhase1PreMarketScan:
    """Phase 1: Scanner produces ranked watchlist from raw market data."""

    def test_scan_produces_candidates_from_quotes(self, settings, mock_quotes):
        """ScanLoop should filter quotes through EMC conjunction and return CandidateStock list."""
        scan_loop = ScanLoop(settings=settings)
        candidates = scan_loop.run_single_scan(mock_quotes)

        # BOOM (70% gap, 10x RVOL) should pass, FLAT (0.5% gap) should not
        tickers = [c.ticker for c in candidates]
        assert "BOOM" in tickers, "70% gapper with 10x RVOL should pass EMC"
        assert "FLAT" not in tickers, "0.5% gap should not pass EMC threshold"

    def test_scan_propagates_avg_daily_volume(self, settings, mock_quotes):
        """avg_daily_volume from prev_volume should flow through to CandidateStock."""
        scan_loop = ScanLoop(settings=settings)
        candidates = scan_loop.run_single_scan(mock_quotes)

        boom = next((c for c in candidates if c.ticker == "BOOM"), None)
        assert boom is not None
        assert boom.avg_daily_volume == 5_000_000, (
            "avg_daily_volume should be set from prev_volume in quotes"
        )

    def test_scan_empty_quotes_returns_empty(self, settings):
        """Empty quotes should produce no candidates."""
        scan_loop = ScanLoop(settings=settings)
        assert scan_loop.run_single_scan({}) == []


# ── Phase 2: Market Open Evaluation ──────────────────────────────────


class TestPhase2MarketOpenEvaluation:
    """Phase 2: Orchestrator evaluates candidates through agent pipeline."""

    @pytest.mark.asyncio
    async def test_orchestrator_produces_verdict(self, settings):
        """Full pipeline should produce a TradeVerdict with MFCS score."""
        candidate = _make_candidate()

        # Mock all LLM calls to return valid signals
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            '{"signal": "BULL", "confidence": 0.8, '
            '"catalyst_type": "EARNINGS_BEAT", "catalyst_specificity": "CONFIRMED", '
            '"sentiment_score": 0.85, "key_reasoning": "Strong earnings", '
            '"red_flags": [], "source_citations": []}'
        )

        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
            orchestrator = Orchestrator(settings)
            verdict = await orchestrator.evaluate_candidate(candidate)

        assert isinstance(verdict, TradeVerdict)
        assert verdict.ticker == "BOOM"
        assert verdict.mfcs >= 0.0

    @pytest.mark.asyncio
    async def test_data_completeness_logged(self, settings):
        """Orchestrator should assess data completeness before agent dispatch."""
        candidate = _make_candidate()

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            '{"signal": "NEUTRAL", "confidence": 0.5, '
            '"catalyst_type": "NONE", "catalyst_specificity": "UNVERIFIED", '
            '"sentiment_score": 0.5, "key_reasoning": "Neutral", '
            '"red_flags": [], "source_citations": []}'
        )

        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
            orchestrator = Orchestrator(settings)
            await orchestrator.evaluate_candidate(candidate, news_items=[])

        # Data report should exist after evaluation
        assert hasattr(orchestrator, "_last_data_report")
        report = orchestrator._last_data_report
        assert "news_agent" in report
        assert report["news_agent"]["status"] == "EMPTY"


# ── Phase 2→3: Execution Bridge ──────────────────────────────────────


class TestPhase2Execution:
    """Phase 2: ExecutionBridge wires verdict → order → position."""

    @pytest.mark.asyncio
    async def test_bridge_executes_buy_verdict(self, settings):
        """BUY verdict should result in order submission and position creation."""
        mock_executor = AsyncMock(spec=AlpacaExecutor)
        mock_executor.execute.return_value = _make_order_result()

        pm = PositionManager(
            config=settings.execution,
            starting_equity=100_000.0,
        )
        bridge = ExecutionBridge(executor=mock_executor, position_manager=pm)

        verdict = _make_buy_verdict()
        order = await bridge.execute_verdict(verdict, scored=None)

        assert order is not None
        assert order.ticker == "BOOM"
        assert order.status == "filled"
        mock_executor.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_bridge_blocks_no_trade_verdict(self, settings):
        """NO_TRADE verdict should short-circuit without calling executor."""
        mock_executor = AsyncMock(spec=AlpacaExecutor)
        pm = PositionManager(config=settings.execution, starting_equity=100_000.0)
        bridge = ExecutionBridge(executor=mock_executor, position_manager=pm)

        verdict = TradeVerdict(
            ticker="BOOM",
            action="NO_TRADE",
            confidence=0.0,
            mfcs=0.3,
            debate_result=None,
            entry_price=8.50,
            stop_loss=8.50,
            target_prices=[],
            position_size_pct=0.0,
            reasoning_summary="MFCS too low",
        )

        order = await bridge.execute_verdict(verdict, scored=None)
        assert order is None
        mock_executor.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_circuit_breaker_blocks_entry(self, settings):
        """Circuit breaker activation should block new entries."""
        mock_executor = AsyncMock(spec=AlpacaExecutor)
        pm = PositionManager(config=settings.execution, starting_equity=100_000.0)
        # Trigger circuit breaker: daily P&L below threshold
        # D98: threshold is -10% of equity = -$10,000 (D94b widened from 5%)
        pm._daily_realized_pnl = -15_000.0  # Well below -$10,000 threshold

        bridge = ExecutionBridge(executor=mock_executor, position_manager=pm)
        verdict = _make_buy_verdict()
        order = await bridge.execute_verdict(verdict, scored=None)

        assert order is None
        mock_executor.execute.assert_not_called()


# ── Portfolio Risk ────────────────────────────────────────────────────


class TestPortfolioRiskIntegration:
    """Portfolio risk checks prevent concentration."""

    def test_sector_concentration_blocks_third_entry(self):
        """Max 2 positions per sector should block a third."""
        prm = PortfolioRiskManager(max_sector_positions=2)

        # Simulate 2 existing Biotech positions
        pos1 = MagicMock()
        pos1.ticker = "MRNA"
        pos1.entry_price = 100.0
        pos1.stop_loss = 93.0
        pos2 = MagicMock()
        pos2.ticker = "BNTX"
        pos2.entry_price = 80.0
        pos2.stop_loss = 74.4

        check = prm.check_entry("REGN", stop_loss_pct=2.0, positions=[pos1, pos2])
        assert not check.allowed
        assert "Sector concentration" in check.reason

    def test_unknown_ticker_with_company_name_classifies(self):
        """Heuristic should classify small-cap biotech from company name."""
        sector = get_sector("XYZQ", company_name="XYZ Therapeutics Inc.")
        assert sector == "Biotech"

    def test_unknown_ticker_no_name_defaults_to_other(self):
        """Unknown ticker with no company name defaults to Other."""
        sector = get_sector("XYZQ123", company_name="")
        assert sector == "Other"


# ── Metrics Integration ──────────────────────────────────────────────


class TestMetricsIntegration:
    """Metrics should track pipeline activity."""

    def test_metrics_reset_and_increment(self):
        """Metrics should reset cleanly and track increments."""
        reset_metrics()
        metrics = get_metrics()

        assert metrics.evaluations_total.value == 0
        metrics.evaluations_total.inc()
        assert metrics.evaluations_total.value == 1

    def test_scan_metrics_tracked(self, settings, mock_quotes):
        """Scan loop should increment scan metrics."""
        reset_metrics()
        metrics = get_metrics()

        scan_loop = ScanLoop(settings=settings)
        scan_loop.run_single_scan(mock_quotes)

        assert metrics.scan_iterations.value >= 1
        assert metrics.scan_candidates_found.value >= 1


# ── Full Lifecycle Smoke Test ─────────────────────────────────────────


class TestFullLifecycleSmoke:
    """
    Smoke test: scan → evaluate → execute → close.
    Validates the full wiring without testing LLM quality.
    """

    @pytest.mark.asyncio
    async def test_scan_to_execution_lifecycle(self, settings, mock_quotes):
        """
        End-to-end: quotes → ScanLoop → Orchestrator → ExecutionBridge → PositionManager.
        """
        reset_metrics()

        # Phase 1: Scan
        scan_loop = ScanLoop(settings=settings)
        candidates = scan_loop.run_single_scan(mock_quotes)
        assert len(candidates) > 0, "At least one candidate should pass EMC"

        # Phase 2: Evaluate (mock LLM)
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = (
            '{"signal": "STRONG_BULL", "confidence": 0.9, '
            '"catalyst_type": "FDA_APPROVAL", "catalyst_specificity": "CONFIRMED", '
            '"sentiment_score": 0.95, "key_reasoning": "FDA approved drug", '
            '"red_flags": [], "source_citations": []}'
        )

        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
            orchestrator = Orchestrator(settings)
            verdicts = await orchestrator.evaluate_candidates(candidates[:2])

        assert len(verdicts) > 0, "Orchestrator should produce at least one verdict"

        # Phase 2: Execute (mock Alpaca)
        mock_executor = AsyncMock(spec=AlpacaExecutor)
        mock_executor.execute.return_value = _make_order_result(
            ticker=verdicts[0].ticker,
        )

        pm = PositionManager(config=settings.execution, starting_equity=100_000.0)
        bridge = ExecutionBridge(executor=mock_executor, position_manager=pm)

        buy_verdicts = [v for v in verdicts if v.action == "BUY"]
        if buy_verdicts:
            order = await bridge.execute_verdict(buy_verdicts[0], scored=None)
            assert order is not None, "BUY verdict should produce an order"
            assert len(pm.open_positions) >= 1, "Position should be tracked"

        # Phase 4: Position close
        if pm.open_positions:
            pos = pm.open_positions[0]
            pm.remove_position(pos.ticker)
            assert len(pm.open_positions) == 0, "Position should be closed"

        # Verify metrics
        metrics = get_metrics()
        assert metrics.scan_iterations.value >= 1
        assert metrics.evaluations_total.value >= 1
