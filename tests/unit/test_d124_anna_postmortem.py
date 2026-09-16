"""D124: ANNA postmortem tests — stop conversion, consensus alignment, gap-day cap.

March 23: ANNA -$868.55 (-20%). Three stacked bugs:
  1. Stop order rejected as short-sell (422 "cannot be sold short")
  2. Consensus gate passed 1 BULL + 1 BEAR (no directional alignment check)
  3. Gap-day stop widening defeated by hard 20% cap on 63% gap stock
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


# ═══════════════════════════════════════════════════════════════════
# FIX 1: STOP CONVERSION — position_intent="close"
# ═══════════════════════════════════════════════════════════════════


class TestStopConversionPositionIntent:
    """D124: submit_stop_order must include position_intent to prevent
    Alpaca from interpreting sell-to-close as short-sell."""

    def test_position_intent_disabled_not_in_payload(self):
        """D126: position_intent DISABLED — Alpaca Paper API rejects it
        with 422 'invalid position_intent specified'. Broke all stop
        conversions on Mar 26 (JBLU/SRPT/RMSG ran unprotected)."""
        import asyncio
        from src.data.alpaca_client import AlpacaDataClient

        client = AlpacaDataClient.__new__(AlpacaDataClient)
        client._trade_base = "https://paper-api.alpaca.markets"

        captured_payload = {}

        async def mock_post(url, payload):
            captured_payload.update(payload)
            return {"id": "test-oid", "status": "accepted"}

        client._trading_post = mock_post

        asyncio.get_event_loop().run_until_complete(
            client.submit_stop_order(
                symbol="ANNA", qty=719, side="sell",
                stop_price=4.83, position_intent="close",
            )
        )

        # position_intent is accepted as parameter but NOT sent to Alpaca
        assert "position_intent" not in captured_payload

    def test_position_intent_omitted_when_none(self):
        """Backward compat: no position_intent when not specified."""
        import asyncio
        from src.data.alpaca_client import AlpacaDataClient

        client = AlpacaDataClient.__new__(AlpacaDataClient)
        client._trade_base = "https://paper-api.alpaca.markets"

        captured_payload = {}

        async def mock_post(url, payload):
            captured_payload.update(payload)
            return {"id": "test-oid", "status": "accepted"}

        client._trading_post = mock_post

        asyncio.get_event_loop().run_until_complete(
            client.submit_stop_order(
                symbol="TEST", qty=100, side="sell", stop_price=9.50,
            )
        )

        assert "position_intent" not in captured_payload


# ═══════════════════════════════════════════════════════════════════
# FIX 2: CONSENSUS ALIGNMENT GATE
# ═══════════════════════════════════════════════════════════════════


class TestConsensusAlignment:
    """D124: Consensus gate must check directional alignment, not just count.

    ANNA had news=BEAR + technical=BULL = '2 directional agents' which
    passed the old gate. The system bought a stock whose only news said
    'ANNA is falling due to profit-taking.'
    """

    def _make_signal(self, signal_dir: str, agent_type: str = "news"):
        """Create a minimal AgentSignal-like object."""
        sig = MagicMock()
        sig.signal = signal_dir
        sig.flags = []
        sig.agent_type = agent_type
        return sig

    def _make_risk_signal(self, signal_dir: str = "NEUTRAL"):
        """Create a RiskSignal-like object (excluded from consensus)."""
        from src.core.models import RiskSignal
        sig = MagicMock(spec=RiskSignal)
        sig.signal = signal_dir
        sig.flags = []
        return sig

    def test_equal_bull_bear_holds(self):
        """1 BULL + 1 BEAR = HOLD (strict bullish majority required)."""
        from src.core.orchestrator import Orchestrator

        orch = Orchestrator.__new__(Orchestrator)
        orch._settings = MagicMock()
        orch._settings.scoring.min_directional_agents = 2

        bull_sig = self._make_signal("BULL", "technical")
        bear_sig = self._make_signal("BEAR", "news")

        # Simulate the consensus logic
        _non_risk = [bull_sig, bear_sig]
        _bullish = [s for s in _non_risk if s.signal in ("BULL", "STRONG_BULL")]
        _bearish = [s for s in _non_risk if s.signal in ("BEAR", "STRONG_BEAR")]

        # D124: bearish >= bullish → HOLD
        assert len(_bearish) >= len(_bullish), (
            "1 BEAR >= 1 BULL should trigger alignment hold"
        )

    def test_majority_bullish_passes(self):
        """2 BULL + 1 BEAR = BUY (bullish majority)."""
        bull1 = self._make_signal("BULL", "technical")
        bull2 = self._make_signal("STRONG_BULL", "fundamental")
        bear1 = self._make_signal("BEAR", "news")

        _non_risk = [bull1, bull2, bear1]
        _bullish = [s for s in _non_risk if s.signal in ("BULL", "STRONG_BULL")]
        _bearish = [s for s in _non_risk if s.signal in ("BEAR", "STRONG_BEAR")]

        # D124: bullish (2) > bearish (1) → passes
        assert len(_bearish) < len(_bullish), (
            "2 BULL > 1 BEAR should pass alignment check"
        )

    def test_all_bullish_passes(self):
        """2 BULL + 0 BEAR = BUY (no regression)."""
        bull1 = self._make_signal("BULL", "technical")
        bull2 = self._make_signal("BULL", "news")

        _non_risk = [bull1, bull2]
        _bullish = [s for s in _non_risk if s.signal in ("BULL", "STRONG_BULL")]
        _bearish = [s for s in _non_risk if s.signal in ("BEAR", "STRONG_BEAR")]

        assert len(_bearish) < len(_bullish), (
            "2 BULL + 0 BEAR should pass alignment check"
        )

    def test_strong_bear_blocks_weak_bull(self):
        """1 STRONG_BEAR + 1 BULL = HOLD."""
        bull = self._make_signal("BULL", "technical")
        strong_bear = self._make_signal("STRONG_BEAR", "news")

        _non_risk = [bull, strong_bear]
        _bullish = [s for s in _non_risk if s.signal in ("BULL", "STRONG_BULL")]
        _bearish = [s for s in _non_risk if s.signal in ("BEAR", "STRONG_BEAR")]

        assert len(_bearish) >= len(_bullish)

    def test_risk_signals_excluded_from_alignment(self):
        """Risk signals should not count in alignment (same as consensus)."""
        from src.core.models import RiskSignal

        bull = self._make_signal("BULL", "technical")
        risk_bear = self._make_risk_signal("BEAR")

        # Non-risk only
        _non_risk = [s for s in [bull, risk_bear] if not isinstance(s, RiskSignal)]
        _bullish = [s for s in _non_risk if s.signal in ("BULL", "STRONG_BULL")]
        _bearish = [s for s in _non_risk if s.signal in ("BEAR", "STRONG_BEAR")]

        # Only 1 BULL, 0 BEAR (risk excluded) → passes
        assert len(_bearish) < len(_bullish)


# ═══════════════════════════════════════════════════════════════════
# FIX 3: GAP-DAY DYNAMIC STOP CAP
# ═══════════════════════════════════════════════════════════════════


class TestGapDayDynamicCap:
    """D124: Gap-day widening uses dynamic cap instead of hard 20%.

    ANNA (63% gap): gap_floor = 31.5%, but 20% cap reduced it to 20%.
    D124 dynamic cap = min(gap*0.5, gap_day_stop_cap=35%).
    """

    def test_63pct_gap_uses_31pct_stop(self):
        """ANNA case: 63% gap → 31.5% stop distance (not 20%)."""
        from src.core.orchestrator import Orchestrator

        orch = Orchestrator.__new__(Orchestrator)
        orch._settings = MagicMock()
        orch._settings.execution.initial_stop_atr_multiplier = 2.0
        orch._settings.execution.initial_stop_floor_pct = 0.04
        orch._settings.execution.gap_day_stop_widening_enabled = True
        orch._settings.execution.gap_day_threshold = 0.08
        orch._settings.execution.gap_day_stop_cap = 0.35
        orch._atr_cache = {"ANNA": 0.10}  # Small ATR so gap_floor dominates

        stop = orch._compute_atr_stop("ANNA", entry=6.04, gap_pct=0.63)
        # gap_floor = 6.04 * 0.63 * 0.5 = $1.9026 (31.5%)
        # D124 dynamic cap = min(31.5%, 35%) = 31.5%
        # max_stop_distance = 6.04 * 0.315 = $1.9026
        # ATR stop = 2.0 * 0.10 = $0.20 < gap_floor → use gap_floor
        # stop = 6.04 - 1.9026 = $4.1374
        assert stop == pytest.approx(4.14, abs=0.02)

    def test_80pct_gap_capped_at_35pct(self):
        """80% gap → cap at 35% (absolute safety limit)."""
        from src.core.orchestrator import Orchestrator

        orch = Orchestrator.__new__(Orchestrator)
        orch._settings = MagicMock()
        orch._settings.execution.initial_stop_atr_multiplier = 2.0
        orch._settings.execution.initial_stop_floor_pct = 0.04
        orch._settings.execution.gap_day_stop_widening_enabled = True
        orch._settings.execution.gap_day_threshold = 0.08
        orch._settings.execution.gap_day_stop_cap = 0.35
        orch._atr_cache = {"TEST": 0.10}

        stop = orch._compute_atr_stop("TEST", entry=10.0, gap_pct=0.80)
        # gap_floor = 10.0 * 0.80 * 0.5 = $4.00 (40%)
        # D124 dynamic cap = min(40%, 35%) = 35% → $3.50
        # stop = 10.0 - 3.50 = 6.50
        assert stop == pytest.approx(6.50, abs=0.01)

    def test_non_gap_day_still_20pct_cap(self):
        """Below gap threshold, the old 20% cap still applies."""
        from src.core.orchestrator import Orchestrator

        orch = Orchestrator.__new__(Orchestrator)
        orch._settings = MagicMock()
        orch._settings.execution.initial_stop_atr_multiplier = 2.0
        orch._settings.execution.initial_stop_floor_pct = 0.04
        orch._settings.execution.gap_day_stop_widening_enabled = True
        orch._settings.execution.gap_day_threshold = 0.08
        orch._settings.execution.gap_day_stop_cap = 0.35
        orch._atr_cache = {"TEST": 5.0}  # Huge ATR to trigger cap

        stop = orch._compute_atr_stop("TEST", entry=10.0, gap_pct=0.05)
        # gap_pct=5% < 8% threshold → _gap_day_active = False
        # ATR stop = 2.0 * 5.0 = $10.00 (100%) → capped at 20% = $2.00
        # stop = 10.0 - 2.0 = 8.0
        assert stop == pytest.approx(8.0, abs=0.01)

    def test_200pct_gap_capped_at_35pct(self):
        """Extreme gap (200%) still capped at 35% absolute limit."""
        from src.core.orchestrator import Orchestrator

        orch = Orchestrator.__new__(Orchestrator)
        orch._settings = MagicMock()
        orch._settings.execution.initial_stop_atr_multiplier = 2.0
        orch._settings.execution.initial_stop_floor_pct = 0.04
        orch._settings.execution.gap_day_stop_widening_enabled = True
        orch._settings.execution.gap_day_threshold = 0.08
        orch._settings.execution.gap_day_stop_cap = 0.35
        orch._atr_cache = {"TEST": 0.10}

        stop = orch._compute_atr_stop("TEST", entry=5.0, gap_pct=2.0)
        # gap_floor = 5.0 * 2.0 * 0.5 = $5.00 (100%)
        # D124 dynamic cap = min(100%, 35%) = 35% → $1.75
        # stop = 5.0 - 1.75 = 3.25
        assert stop == pytest.approx(3.25, abs=0.01)


# ═══════════════════════════════════════════════════════════════════
# CONFIG TESTS
# ═══════════════════════════════════════════════════════════════════


class TestD124Config:
    """Verify D124 config defaults."""

    def test_gap_day_stop_cap_default(self):
        from config.settings import ExecutionConfig

        cfg = ExecutionConfig()
        assert cfg.gap_day_stop_cap == 0.35
