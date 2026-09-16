"""
Tests for D85: Fast-Path Entry System.

Covers:
- DipEntryCalculator: dip factors for each gap classification
- FastPathScorer: 3-agent scoring (News + Fundamental + Deep Search),
  threshold filtering, max entries cap, parallel scoring
- FastPathExecutor: circuit breaker, existing position, portfolio risk checks
- FastPathReconciler: confirm on agree, cancel on disagree
- get_fast_path_tickers: active ticker filtering
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import (
    AgentSignal,
    CandidateStock,
    GapClassification,
    NewsSignal,
    TradeVerdict,
)
from src.execution.fast_path import (
    DIP_TABLE,
    DipEntryCalculator,
    FastPathEntry,
    FastPathExecutor,
    FastPathReconciler,
    FastPathScorer,
    get_fast_path_tickers,
)


# ── Fixtures ──────────────────────────────────────────────────────────

def _make_candidate(
    ticker: str = "TEST",
    price: float = 10.0,
    prev_close: float = 8.0,
    gap_classification: GapClassification = "MINOR",
    rvol: float = 3.0,
    gap_pct: float = 0.25,
) -> CandidateStock:
    return CandidateStock(
        ticker=ticker,
        current_price=price,
        previous_close=prev_close,
        gap_pct=gap_pct,
        gap_classification=gap_classification,
        rvol=rvol,
        premarket_volume=500_000,
        scan_timestamp=datetime.now(timezone.utc),
        scan_phase="PRE_MARKET",
    )


def _make_news_signal(
    ticker: str = "TEST",
    signal: str = "BULL",
    confidence: float = 0.8,
    reasoning: str = "FDA approval confirmed",
) -> NewsSignal:
    return NewsSignal(
        agent_id="news_agent",
        ticker=ticker,
        timestamp=datetime.now(timezone.utc),
        signal=signal,
        confidence=confidence,
        reasoning=reasoning,
        catalyst_type="FDA_APPROVAL",
        catalyst_specificity="CONFIRMED",
        sentiment_score=0.8,
    )


def _make_agent_signal(
    agent_id: str = "fundamental_agent",
    ticker: str = "TEST",
    signal: str = "BULL",
    confidence: float = 0.7,
    reasoning: str = "Low float, no dilution",
) -> AgentSignal:
    return AgentSignal(
        agent_id=agent_id,
        ticker=ticker,
        timestamp=datetime.now(timezone.utc),
        signal=signal,
        confidence=confidence,
        reasoning=reasoning,
    )


def _make_settings(**overrides):
    """Create a mock Settings with sensible defaults."""
    settings = MagicMock()
    settings.fast_path.enabled = True
    settings.fast_path.fast_path_threshold = 0.35
    settings.fast_path.max_fast_path_entries = 3
    settings.fast_path.position_size_pct = 0.08
    settings.fast_path.cancel_on_disagree = True
    settings.fast_path.upgrade_on_confirm = True
    settings.execution.stop_loss_pct = 0.04
    settings.execution.max_position_pct = 0.15
    settings.scoring.catalyst_news = 0.30
    settings.scoring.technical = 0.20
    settings.scoring.volume_rvol = 0.20
    settings.scoring.float_structure = 0.15
    settings.scoring.institutional = 0.10
    settings.scoring.deep_search = 0.05
    settings.scoring.risk_aversion_lambda = 0.15
    for k, v in overrides.items():
        parts = k.split(".")
        obj = settings
        for part in parts[:-1]:
            obj = getattr(obj, part)
        setattr(obj, parts[-1], v)
    return settings


# ── DipEntryCalculator Tests ─────────────────────────────────────────

class TestDipEntryCalculator:
    def test_minor_gap_2pct_dip(self):
        """MINOR gap → 2% dip below premarket price."""
        candidate = _make_candidate(price=10.0, gap_classification="MINOR")
        entry, stop, targets = DipEntryCalculator.compute_entry(candidate)

        assert entry == pytest.approx(10.0 * 0.98, abs=0.01)
        assert stop < entry  # Stop is below entry
        assert stop == pytest.approx(entry * 0.96, abs=0.01)
        assert len(targets) == 3
        assert all(t > entry for t in targets)

    def test_significant_gap_5pct_dip(self):
        """SIGNIFICANT gap → 5% dip below premarket price."""
        candidate = _make_candidate(price=10.0, gap_classification="SIGNIFICANT")
        entry, stop, targets = DipEntryCalculator.compute_entry(candidate)

        assert entry == pytest.approx(10.0 * 0.95, abs=0.01)

    def test_major_gap_10pct_dip(self):
        """MAJOR gap → 10% dip below premarket price."""
        candidate = _make_candidate(price=10.0, gap_classification="MAJOR")
        entry, stop, targets = DipEntryCalculator.compute_entry(candidate)

        assert entry == pytest.approx(10.0 * 0.90, abs=0.01)

    def test_explosive_gap_15pct_dip(self):
        """EXPLOSIVE gap → 15% dip below premarket price."""
        candidate = _make_candidate(price=10.0, gap_classification="EXPLOSIVE")
        entry, stop, targets = DipEntryCalculator.compute_entry(candidate)

        assert entry == pytest.approx(10.0 * 0.85, abs=0.01)

    def test_targets_ascending(self):
        """Targets are at +5%, +10%, +20% from entry price (D121: updated from +3/6/10%)."""
        candidate = _make_candidate(price=10.0, gap_classification="MINOR")
        entry, _, targets = DipEntryCalculator.compute_entry(candidate)

        assert targets[0] == pytest.approx(entry * 1.05, abs=0.01)
        assert targets[1] == pytest.approx(entry * 1.10, abs=0.01)
        assert targets[2] == pytest.approx(entry * 1.20, abs=0.01)

    def test_sub_dollar_rounding(self):
        """Sub-dollar stocks use 4 decimal places (SEC Rule 612)."""
        candidate = _make_candidate(price=0.50, gap_classification="MINOR")
        entry, stop, targets = DipEntryCalculator.compute_entry(candidate)

        # Should have 4 decimal places
        assert entry == round(entry, 4)
        assert stop == round(stop, 4)

    def test_get_dip_factor(self):
        """get_dip_factor returns correct values from DIP_TABLE."""
        assert DipEntryCalculator.get_dip_factor("MINOR") == 0.02
        assert DipEntryCalculator.get_dip_factor("SIGNIFICANT") == 0.05
        assert DipEntryCalculator.get_dip_factor("MAJOR") == 0.10
        assert DipEntryCalculator.get_dip_factor("EXPLOSIVE") == 0.15

    def test_custom_stop_pct(self):
        """Custom stop_pct parameter is respected."""
        candidate = _make_candidate(price=10.0, gap_classification="MINOR")
        entry, stop, _ = DipEntryCalculator.compute_entry(candidate, stop_pct=0.02)

        # 2% stop instead of default 4%
        assert stop == pytest.approx(entry * 0.98, abs=0.01)


# ── FastPathScorer Tests ─────────────────────────────────────────────

class TestFastPathScorer:
    @pytest.mark.asyncio
    async def test_scores_candidates_with_news(self):
        """Scorer runs news agent and filters by threshold."""
        settings = _make_settings()
        mock_agent = AsyncMock()
        mock_agent.analyze = AsyncMock(
            return_value=_make_news_signal(signal="STRONG_BULL", confidence=0.9)
        )

        scorer = FastPathScorer(
            settings=settings,
            news_agent=mock_agent,
            threshold=0.20,  # Low threshold to ensure pass
            max_entries=3,
        )

        candidates = [_make_candidate(ticker="ALUR", rvol=5.0)]
        entries = await scorer.score_candidates(candidates)

        assert len(entries) == 1
        assert entries[0].ticker == "ALUR"
        assert entries[0].partial_mfcs > 0.20
        assert entries[0].status == "QUEUED"
        mock_agent.analyze.assert_called_once()

    @pytest.mark.asyncio
    async def test_3_agent_scoring(self):
        """3-agent scorer runs News + Fundamental + Deep Search in parallel."""
        settings = _make_settings()

        mock_news = AsyncMock()
        mock_news.analyze = AsyncMock(
            return_value=_make_news_signal(signal="BULL", confidence=0.8)
        )
        mock_fund = AsyncMock()
        mock_fund.analyze = AsyncMock(
            return_value=_make_agent_signal("fundamental_agent", signal="BULL", confidence=0.7)
        )
        mock_deep = AsyncMock()
        mock_deep.analyze = AsyncMock(
            return_value=_make_agent_signal("deep_search_agent", signal="BULL", confidence=0.6)
        )

        scorer = FastPathScorer(
            settings=settings,
            news_agent=mock_news,
            fundamental_agent=mock_fund,
            deep_search_agent=mock_deep,
            threshold=0.10,
            max_entries=3,
        )

        candidates = [_make_candidate(ticker="MULTI", rvol=5.0)]
        sec_filings = {"MULTI": [{"form": "8-K", "description": "Earnings", "date": "2026-02-20"}]}
        entries = await scorer.score_candidates(
            candidates, sec_filings_by_ticker=sec_filings,
        )

        assert len(entries) == 1
        # 3 agents reporting → higher MFCS than 1 agent alone
        assert entries[0].partial_mfcs > 0.20
        # All 3 agents should have been called
        mock_news.analyze.assert_called_once()
        mock_fund.analyze.assert_called_once()
        mock_deep.analyze.assert_called_once()

    @pytest.mark.asyncio
    async def test_3_agent_all_strong_bull_high_mfcs(self):
        """3 STRONG_BULL agents at high confidence produce strong MFCS."""
        settings = _make_settings()

        mock_news = AsyncMock()
        mock_news.analyze = AsyncMock(
            return_value=_make_news_signal(signal="STRONG_BULL", confidence=0.95)
        )
        mock_fund = AsyncMock()
        mock_fund.analyze = AsyncMock(
            return_value=_make_agent_signal("fundamental_agent", signal="STRONG_BULL", confidence=0.9)
        )
        mock_deep = AsyncMock()
        mock_deep.analyze = AsyncMock(
            return_value=_make_agent_signal("deep_search_agent", signal="STRONG_BULL", confidence=0.85)
        )

        scorer = FastPathScorer(
            settings=settings,
            news_agent=mock_news,
            fundamental_agent=mock_fund,
            deep_search_agent=mock_deep,
            threshold=0.01,
            max_entries=3,
        )
        candidates = [_make_candidate(ticker="CMP", rvol=5.0)]
        entries = await scorer.score_candidates(candidates)

        assert len(entries) == 1
        # 3 STRONG_BULL agents at high confidence → solid MFCS
        assert entries[0].partial_mfcs > 0.40

    @pytest.mark.asyncio
    async def test_fundamental_failure_graceful(self):
        """Fundamental agent failure doesn't crash — still scores with news."""
        settings = _make_settings()

        mock_news = AsyncMock()
        mock_news.analyze = AsyncMock(
            return_value=_make_news_signal(signal="STRONG_BULL", confidence=0.9)
        )
        mock_fund = AsyncMock()
        mock_fund.analyze = AsyncMock(side_effect=Exception("LLM timeout"))

        scorer = FastPathScorer(
            settings=settings,
            news_agent=mock_news,
            fundamental_agent=mock_fund,
            threshold=0.10,
            max_entries=3,
        )

        candidates = [_make_candidate(ticker="PARTIAL", rvol=5.0)]
        entries = await scorer.score_candidates(candidates)

        # Should still produce entry from news signal alone
        assert len(entries) == 1
        assert entries[0].ticker == "PARTIAL"

    @pytest.mark.asyncio
    async def test_filters_below_threshold(self):
        """Candidates below threshold are not queued."""
        settings = _make_settings()
        mock_agent = AsyncMock()
        # NEUTRAL with low confidence → low MFCS
        mock_agent.analyze = AsyncMock(
            return_value=AgentSignal(
                agent_id="news_agent",
                ticker="WEAK",
                timestamp=datetime.now(timezone.utc),
                signal="NEUTRAL",
                confidence=0.2,
                reasoning="",  # Empty reasoning → excluded by D26
            )
        )

        scorer = FastPathScorer(
            settings=settings,
            news_agent=mock_agent,
            threshold=0.35,
            max_entries=3,
        )

        candidates = [_make_candidate(ticker="WEAK", rvol=1.5)]
        entries = await scorer.score_candidates(candidates)

        # NEUTRAL with empty reasoning is excluded by D26, and RVOL 1.5 alone
        # should produce a score below threshold
        for e in entries:
            assert e.partial_mfcs >= 0.35

    @pytest.mark.asyncio
    async def test_caps_at_max_entries(self):
        """Max entries cap is respected."""
        settings = _make_settings()
        mock_agent = AsyncMock()
        mock_agent.analyze = AsyncMock(
            return_value=_make_news_signal(signal="STRONG_BULL", confidence=0.9)
        )

        scorer = FastPathScorer(
            settings=settings,
            news_agent=mock_agent,
            threshold=0.10,  # Very low to ensure all pass
            max_entries=2,  # Cap at 2
        )

        candidates = [
            _make_candidate(ticker="A", rvol=5.0),
            _make_candidate(ticker="B", rvol=4.0),
            _make_candidate(ticker="C", rvol=3.0),
            _make_candidate(ticker="D", rvol=2.0),
        ]
        entries = await scorer.score_candidates(candidates)

        assert len(entries) <= 2

    @pytest.mark.asyncio
    async def test_handles_news_agent_failure(self):
        """All agent failures for a candidate → no entry (graceful degradation)."""
        settings = _make_settings()
        mock_agent = AsyncMock()
        mock_agent.analyze = AsyncMock(side_effect=Exception("LLM timeout"))

        scorer = FastPathScorer(
            settings=settings,
            news_agent=mock_agent,
            threshold=0.35,
            max_entries=3,
        )

        candidates = [_make_candidate(ticker="FAIL")]
        entries = await scorer.score_candidates(candidates)

        assert len(entries) == 0  # Graceful degradation

    @pytest.mark.asyncio
    async def test_empty_candidates(self):
        """Empty candidate list returns empty entries."""
        settings = _make_settings()
        mock_agent = AsyncMock()

        scorer = FastPathScorer(
            settings=settings,
            news_agent=mock_agent,
            threshold=0.35,
            max_entries=3,
        )

        entries = await scorer.score_candidates([])
        assert entries == []
        mock_agent.analyze.assert_not_called()

    @pytest.mark.asyncio
    async def test_parallel_3_agent_fetch(self):
        """All 3 agents run in parallel across multiple candidates."""
        settings = _make_settings()
        call_counts = {"news": 0, "fund": 0, "deep": 0}

        async def mock_news_analyze(**kwargs):
            call_counts["news"] += 1
            return _make_news_signal(ticker=kwargs["ticker"], signal="BULL", confidence=0.7)

        async def mock_fund_analyze(**kwargs):
            call_counts["fund"] += 1
            return _make_agent_signal("fundamental_agent", ticker=kwargs["ticker"], signal="BULL")

        async def mock_deep_analyze(**kwargs):
            call_counts["deep"] += 1
            return _make_agent_signal("deep_search_agent", ticker=kwargs["ticker"], signal="NEUTRAL")

        mock_news = AsyncMock()
        mock_news.analyze = mock_news_analyze
        mock_fund = AsyncMock()
        mock_fund.analyze = mock_fund_analyze
        mock_deep = AsyncMock()
        mock_deep.analyze = mock_deep_analyze

        scorer = FastPathScorer(
            settings=settings,
            news_agent=mock_news,
            fundamental_agent=mock_fund,
            deep_search_agent=mock_deep,
            threshold=0.10,
            max_entries=5,
        )

        candidates = [
            _make_candidate(ticker=f"T{i}", rvol=3.0) for i in range(4)
        ]
        entries = await scorer.score_candidates(candidates)

        # 4 candidates × 3 agents = 12 total LLM calls
        assert call_counts["news"] == 4
        assert call_counts["fund"] == 4
        assert call_counts["deep"] == 4

    @pytest.mark.asyncio
    async def test_sec_filings_passed_to_agents(self):
        """SEC filings from premarket cache are passed to fundamental + deep search."""
        settings = _make_settings()

        received_filings = {}

        async def mock_fund_analyze(**kwargs):
            received_filings["fund"] = kwargs.get("recent_filings", [])
            return _make_agent_signal("fundamental_agent", signal="BEAR", confidence=0.8,
                                      reasoning="Dilution risk from S-3 filing")

        async def mock_deep_analyze(**kwargs):
            received_filings["deep"] = kwargs.get("sec_filings", [])
            return _make_agent_signal("deep_search_agent", signal="BEAR", confidence=0.6,
                                      reasoning="Recent shelf registration")

        mock_news = AsyncMock()
        mock_news.analyze = AsyncMock(
            return_value=_make_news_signal(signal="BULL", confidence=0.7)
        )
        mock_fund = AsyncMock()
        mock_fund.analyze = mock_fund_analyze
        mock_deep = AsyncMock()
        mock_deep.analyze = mock_deep_analyze

        scorer = FastPathScorer(
            settings=settings,
            news_agent=mock_news,
            fundamental_agent=mock_fund,
            deep_search_agent=mock_deep,
            threshold=0.01,
            max_entries=3,
        )

        filings = [{"form": "S-3", "description": "Shelf registration", "date": "2026-02-15"}]
        candidates = [_make_candidate(ticker="DIL", rvol=4.0)]
        await scorer.score_candidates(
            candidates, sec_filings_by_ticker={"DIL": filings},
        )

        # Verify filings were passed through
        assert received_filings["fund"] == filings
        assert received_filings["deep"] == filings


# ── FastPathExecutor Tests ───────────────────────────────────────────

class TestFastPathExecutor:
    @pytest.mark.asyncio
    async def test_submits_oto_orders(self):
        """Executor submits OTO orders for valid entries."""
        settings = _make_settings()
        mock_client = AsyncMock()
        mock_client.get_account = AsyncMock(return_value={"equity": "100000"})
        mock_client.submit_oto_order = AsyncMock(return_value={
            "id": "order123",
            "status": "accepted",
            "legs": [{"side": "sell", "type": "stop", "id": "stop456"}],
        })

        mock_pm = MagicMock()
        mock_pm.can_enter_new_position.return_value = True
        mock_pm.has_position.return_value = False
        mock_pm.open_positions = []

        mock_pr = MagicMock()
        mock_pr.check_entry.return_value = MagicMock(allowed=True)

        executor = FastPathExecutor(
            settings=settings,
            alpaca_client=mock_client,
            position_manager=mock_pm,
            portfolio_risk=mock_pr,
        )

        entries = [FastPathEntry(
            ticker="TEST",
            candidate=_make_candidate(),
            partial_mfcs=0.5,
            news_signal=None,
            entry_price=9.80,
            stop_loss=9.41,
            target_prices=[10.09, 10.39, 10.78],
        )]

        result = await executor.execute_queue(entries)

        assert result[0].status == "SUBMITTED"
        assert result[0].order_id == "order123"
        assert result[0].stop_order_id == "stop456"
        assert result[0].qty > 0
        mock_client.submit_oto_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_respects_circuit_breaker(self):
        """Circuit breaker blocks fast-path entries."""
        settings = _make_settings()
        mock_client = AsyncMock()
        mock_client.get_account = AsyncMock(return_value={"equity": "100000"})

        mock_pm = MagicMock()
        mock_pm.can_enter_new_position.return_value = False  # Circuit breaker active

        mock_pr = MagicMock()

        executor = FastPathExecutor(
            settings=settings,
            alpaca_client=mock_client,
            position_manager=mock_pm,
            portfolio_risk=mock_pr,
        )

        entries = [FastPathEntry(
            ticker="BLOCKED",
            candidate=_make_candidate(ticker="BLOCKED"),
            partial_mfcs=0.6,
            news_signal=None,
            entry_price=10.0,
            stop_loss=9.60,
        )]

        result = await executor.execute_queue(entries)

        assert result[0].status == "CANCELLED"
        assert "circuit breaker" in result[0].error.lower() or "max positions" in result[0].error.lower()

    @pytest.mark.asyncio
    async def test_no_duplicate_positions(self):
        """Already-held positions are skipped."""
        settings = _make_settings()
        mock_client = AsyncMock()
        mock_client.get_account = AsyncMock(return_value={"equity": "100000"})

        mock_pm = MagicMock()
        mock_pm.can_enter_new_position.return_value = True
        mock_pm.has_position.return_value = True  # Already held

        mock_pr = MagicMock()

        executor = FastPathExecutor(
            settings=settings,
            alpaca_client=mock_client,
            position_manager=mock_pm,
            portfolio_risk=mock_pr,
        )

        entries = [FastPathEntry(
            ticker="HELD",
            candidate=_make_candidate(ticker="HELD"),
            partial_mfcs=0.5,
            news_signal=None,
            entry_price=10.0,
            stop_loss=9.60,
        )]

        result = await executor.execute_queue(entries)

        assert result[0].status == "CANCELLED"
        assert "already held" in result[0].error.lower()

    @pytest.mark.asyncio
    async def test_third_sizing(self):
        """Position sizing is THIRD (8% of equity)."""
        settings = _make_settings()
        mock_client = AsyncMock()
        mock_client.get_account = AsyncMock(return_value={"equity": "100000"})
        mock_client.submit_oto_order = AsyncMock(return_value={
            "id": "o1", "status": "accepted", "legs": [],
        })

        mock_pm = MagicMock()
        mock_pm.can_enter_new_position.return_value = True
        mock_pm.has_position.return_value = False
        mock_pm.open_positions = []

        mock_pr = MagicMock()
        mock_pr.check_entry.return_value = MagicMock(allowed=True)

        executor = FastPathExecutor(
            settings=settings,
            alpaca_client=mock_client,
            position_manager=mock_pm,
            portfolio_risk=mock_pr,
        )

        entries = [FastPathEntry(
            ticker="SIZE",
            candidate=_make_candidate(ticker="SIZE", price=10.0),
            partial_mfcs=0.5,
            news_signal=None,
            entry_price=9.80,
            stop_loss=9.41,
        )]

        result = await executor.execute_queue(entries)

        # $100k equity × 8% = $8000 / $9.80 = 816 shares
        assert result[0].qty == 816


# ── FastPathReconciler Tests ─────────────────────────────────────────

class TestFastPathReconciler:
    @pytest.mark.asyncio
    async def test_confirms_on_agree(self):
        """BUY verdict confirms fast-path entry."""
        settings = _make_settings()
        mock_client = AsyncMock()
        mock_pm = MagicMock()

        reconciler = FastPathReconciler(
            settings=settings,
            alpaca_client=mock_client,
            position_manager=mock_pm,
        )

        entries = [FastPathEntry(
            ticker="GOOD",
            candidate=_make_candidate(ticker="GOOD"),
            partial_mfcs=0.5,
            news_signal=None,
            entry_price=10.0,
            stop_loss=9.60,
            status="SUBMITTED",
        )]

        verdicts = [TradeVerdict(
            ticker="GOOD",
            action="BUY",
            confidence=0.8,
            mfcs=0.6,
            entry_price=10.0,
            stop_loss=9.60,
            target_prices=[10.30, 10.60, 11.00],
            position_size_pct=0.10,
        )]

        result = await reconciler.reconcile(entries, verdicts)

        assert result[0].status == "CONFIRMED"

    @pytest.mark.asyncio
    async def test_keeps_on_neutral_disagree(self):
        """D139: NO_TRADE without BEAR signal KEEPS fast-path entry (pipeline inversion)."""
        settings = _make_settings()
        mock_client = AsyncMock()
        mock_client.cancel_order = AsyncMock(return_value={})
        mock_pm = MagicMock()
        mock_pm.has_position.return_value = False

        reconciler = FastPathReconciler(
            settings=settings,
            alpaca_client=mock_client,
            position_manager=mock_pm,
        )

        entries = [FastPathEntry(
            ticker="KEEP",
            candidate=_make_candidate(ticker="KEEP"),
            partial_mfcs=0.4,
            news_signal=None,
            entry_price=10.0,
            stop_loss=9.60,
            order_id="keep_me",
            status="SUBMITTED",
        )]

        verdicts = [TradeVerdict(
            ticker="KEEP",
            action="NO_TRADE",
            confidence=0.0,
            mfcs=0.2,
            entry_price=10.0,
            stop_loss=10.0,
            target_prices=[],
            position_size_pct=0.0,
        )]

        result = await reconciler.reconcile(entries, verdicts)

        # D139: NO_TRADE without BEAR signal -> keep entry
        assert result[0].status == "SUBMITTED"  # Not cancelled
        mock_client.cancel_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_verdict_keeps_entry(self):
        """Entry without matching verdict stays as-is."""
        settings = _make_settings()
        mock_client = AsyncMock()
        mock_pm = MagicMock()

        reconciler = FastPathReconciler(
            settings=settings,
            alpaca_client=mock_client,
            position_manager=mock_pm,
        )

        entries = [FastPathEntry(
            ticker="ORPHAN",
            candidate=_make_candidate(ticker="ORPHAN"),
            partial_mfcs=0.5,
            news_signal=None,
            entry_price=10.0,
            stop_loss=9.60,
            status="SUBMITTED",
        )]

        # No verdict for ORPHAN
        verdicts = [TradeVerdict(
            ticker="OTHER",
            action="BUY",
            confidence=0.8,
            mfcs=0.6,
            entry_price=5.0,
            stop_loss=4.80,
            target_prices=[5.15, 5.30, 5.50],
            position_size_pct=0.10,
        )]

        result = await reconciler.reconcile(entries, verdicts)

        assert result[0].status == "SUBMITTED"  # Unchanged

    @pytest.mark.asyncio
    async def test_keeps_filled_position_on_neutral_disagree(self):
        """D139: If fast-path filled and eval returns NO_TRADE without BEAR, keep position."""
        settings = _make_settings()
        mock_client = AsyncMock()
        mock_client.cancel_order = AsyncMock(side_effect=Exception("not found"))
        mock_client.close_position = AsyncMock(return_value={"status": "closed"})
        mock_pm = MagicMock()
        mock_pm.has_position.return_value = True

        reconciler = FastPathReconciler(
            settings=settings,
            alpaca_client=mock_client,
            position_manager=mock_pm,
        )

        entries = [FastPathEntry(
            ticker="FILLED",
            candidate=_make_candidate(ticker="FILLED"),
            partial_mfcs=0.4,
            news_signal=None,
            entry_price=10.0,
            stop_loss=9.60,
            order_id="filled_order",
            status="FILLED",
        )]

        verdicts = [TradeVerdict(
            ticker="FILLED",
            action="NO_TRADE",
            confidence=0.0,
            mfcs=0.1,
            entry_price=10.0,
            stop_loss=10.0,
            target_prices=[],
            position_size_pct=0.0,
        )]

        result = await reconciler.reconcile(entries, verdicts)

        # D139: NO_TRADE without BEAR -> keep position (pipeline inversion)
        assert result[0].status == "FILLED"  # Kept, not cancelled
        mock_client.close_position.assert_not_called()


# ── get_fast_path_tickers Tests ──────────────────────────────────────

class TestGetFastPathTickers:
    def test_returns_active_tickers(self):
        """Active entries (QUEUED, SUBMITTED, FILLED, CONFIRMED) are included."""
        entries = [
            FastPathEntry(ticker="A", candidate=_make_candidate(ticker="A"),
                          partial_mfcs=0.5, news_signal=None,
                          entry_price=10.0, stop_loss=9.6, status="SUBMITTED"),
            FastPathEntry(ticker="B", candidate=_make_candidate(ticker="B"),
                          partial_mfcs=0.4, news_signal=None,
                          entry_price=10.0, stop_loss=9.6, status="CANCELLED"),
            FastPathEntry(ticker="C", candidate=_make_candidate(ticker="C"),
                          partial_mfcs=0.6, news_signal=None,
                          entry_price=10.0, stop_loss=9.6, status="CONFIRMED"),
        ]

        tickers = get_fast_path_tickers(entries)

        assert tickers == {"A", "C"}
        assert "B" not in tickers

    def test_empty_list(self):
        """Empty list returns empty set."""
        assert get_fast_path_tickers([]) == set()


# ── DIP_TABLE Tests ──────────────────────────────────────────────────

class TestDipTable:
    def test_all_gap_classifications_covered(self):
        """Every GapClassification has a dip factor."""
        expected = {"MINOR", "SIGNIFICANT", "MAJOR", "EXPLOSIVE"}
        assert set(DIP_TABLE.keys()) == expected

    def test_dip_factors_ascending(self):
        """Larger gaps → larger dip factors."""
        assert DIP_TABLE["MINOR"] < DIP_TABLE["SIGNIFICANT"]
        assert DIP_TABLE["SIGNIFICANT"] < DIP_TABLE["MAJOR"]
        assert DIP_TABLE["MAJOR"] < DIP_TABLE["EXPLOSIVE"]

    def test_all_factors_positive(self):
        """All dip factors are positive."""
        for gap, factor in DIP_TABLE.items():
            assert factor > 0, f"{gap} has non-positive dip factor"
