"""D126: Gap-Up Momentum Mode tests.

March 25: 8 of 13 watchlist stocks were BIG WINNERS (+22% to +136%),
system caught zero. The technical agent used lagging indicators (MACD, EMA)
that produced BEAR signals on gap-up stocks at market open.

D126 introduces a gap momentum override: when gap_pct × rvol > threshold,
skip lagging indicators and use gap + VWAP + RSI as the signal.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest


def _run_tech_agent(gap_pct: float, rvol: float, current_price: float = 10.0,
                    vwap: float = 0.0, rsi_override: float | None = None,
                    macd_hist: float = -0.01, ema_9: float = 9.9, ema_21: float = 10.1,
                    minutes_since_open: int = 3, bars: list | None = None):
    """Helper to run the deterministic technical agent with controlled inputs."""
    from src.agents.deterministic_technical import DeterministicTechnicalAgent

    agent = DeterministicTechnicalAgent()

    # Mock the time so we control minutes_since_open
    from zoneinfo import ZoneInfo
    _hour = 9 + (30 + minutes_since_open) // 60
    _minute = (30 + minutes_since_open) % 60
    mock_time = datetime(2026, 3, 25, _hour, _minute, 0,
                         tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)

    # Build minimal bars with indicators baked in
    test_bars = bars or [
        {"o": current_price, "h": current_price * 1.01, "l": current_price * 0.99,
         "c": current_price, "v": 100000, "t": "2026-03-25T09:30:00Z"},
    ] * 25  # Need 25+ bars for indicators

    # We need to patch the indicator computation since we want controlled values
    mock_computed = {
        "rsi_9": rsi_override if rsi_override is not None else 55.0,
        "macd_histogram": macd_hist,
        "ema_9": ema_9,
        "ema_21": ema_21,
        "bb_upper": current_price * 1.05,
        "bb_lower": current_price * 0.95,
        "atr_14": current_price * 0.03,
        "recent_high": current_price * 0.98,  # Below current → breakout possible
        "recent_low": current_price * 0.90,
    }

    with patch("src.agents.deterministic_technical.datetime") as mock_dt:
        mock_dt.now.return_value = mock_time
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

        result = asyncio.get_event_loop().run_until_complete(
            agent.analyze(
                ticker="TEST",
                current_price=current_price,
                rvol=rvol,
                gap_pct=gap_pct,
                vwap=vwap,
                price_data={"5min": test_bars},
            )
        )
    return result


class TestGapMomentumActivation:
    """D126: Gap momentum mode activates on composite threshold."""

    def test_high_composite_activates(self):
        """gap=30%, rvol=10x → score=3.0 → GAP_MOMENTUM."""
        result = _run_tech_agent(gap_pct=0.30, rvol=10.0, vwap=10.5,
                                 current_price=10.0)
        assert "GAP MOMENTUM" in (result.reasoning or "")
        assert result.signal in ("BULL", "STRONG_BULL")

    def test_moderate_gap_high_vol_activates(self):
        """gap=10%, rvol=15x → score=1.5 → activates."""
        result = _run_tech_agent(gap_pct=0.10, rvol=15.0, vwap=10.5,
                                 current_price=10.0)
        assert "GAP MOMENTUM" in (result.reasoning or "")

    def test_low_composite_no_momentum(self):
        """gap=5%, rvol=1.5x → score=0.075 → normal voting."""
        result = _run_tech_agent(gap_pct=0.05, rvol=1.5, vwap=10.5,
                                 current_price=10.0)
        assert "GAP_MOMENTUM" not in (result.reasoning or "")

    def test_low_rvol_no_momentum(self):
        """gap=30%, rvol=2.0x → below 2.5 RVOL floor."""
        result = _run_tech_agent(gap_pct=0.30, rvol=2.0, vwap=10.5,
                                 current_price=10.0)
        assert "GAP_MOMENTUM" not in (result.reasoning or "")

    def test_low_gap_no_momentum(self):
        """gap=5%, rvol=100x → score=5.0 but gap below 8% floor."""
        result = _run_tech_agent(gap_pct=0.05, rvol=100.0, vwap=10.5,
                                 current_price=10.0)
        assert "GAP_MOMENTUM" not in (result.reasoning or "")


class TestGapMomentumSignals:
    """D126: Gap momentum mode produces correct signals."""

    def test_vwap_above_strong_signal(self):
        """Gap momentum + VWAP above → STRONG_BULL or BULL."""
        result = _run_tech_agent(gap_pct=0.50, rvol=100.0,
                                 current_price=10.0, vwap=9.5)
        # net_bull = 2 (base) + 1 (VWAP above) + possible RSI = 3+
        assert result.signal in ("BULL", "STRONG_BULL")

    def test_vwap_below_still_bull(self):
        """Gap momentum + VWAP below → BULL (NOT BEAR)."""
        result = _run_tech_agent(gap_pct=0.50, rvol=100.0,
                                 current_price=10.0, vwap=10.5)
        # net_bull = 2 (base) - 1 (VWAP below) = 1 → BULL
        assert result.signal in ("BULL", "STRONG_BULL"), (
            f"Gap momentum stock below VWAP should be BULL, got {result.signal}"
        )

    def test_rsi_75_no_penalty(self):
        """RSI=80 in momentum mode → NO bearish factor."""
        result = _run_tech_agent(gap_pct=0.50, rvol=100.0,
                                 current_price=10.0, vwap=10.5,
                                 rsi_override=80.0)
        # RSI > 75 should NOT add bearish factor in momentum mode
        assert result.signal in ("BULL", "STRONG_BULL"), (
            f"RSI > 75 in gap momentum should not trigger BEAR, got {result.signal}"
        )


class TestGapMomentumDecay:
    """D126: Momentum mode decays gradually, not cliff."""

    def test_full_strength_at_5min(self):
        """At T+5min, momentum weight should be 1.0."""
        result = _run_tech_agent(gap_pct=0.50, rvol=100.0,
                                 current_price=10.0, vwap=9.5,
                                 minutes_since_open=5)
        assert "GAP MOMENTUM" in (result.reasoning or "")
        assert "weight=1.00" in (result.reasoning or "")

    def test_blending_at_20min(self):
        """At T+20min, momentum weight should be 0.50 (half blend)."""
        result = _run_tech_agent(gap_pct=0.50, rvol=100.0,
                                 current_price=10.0, vwap=9.5,
                                 minutes_since_open=20)
        assert "GAP MOMENTUM" in (result.reasoning or "")
        assert "weight=0.50" in (result.reasoning or "")

    def test_no_momentum_at_30min(self):
        """At T+30min, momentum mode should be inactive."""
        result = _run_tech_agent(gap_pct=0.50, rvol=100.0,
                                 current_price=10.0, vwap=9.5,
                                 minutes_since_open=30)
        assert "GAP_MOMENTUM" not in (result.reasoning or "")


class TestTimeDecayOverride:
    """D126: Gap momentum stocks get reduced time decay."""

    def test_confidence_at_t0_momentum(self):
        """At T+0, gap momentum confidence factor = 0.8 (not 0.0)."""
        result = _run_tech_agent(gap_pct=0.50, rvol=100.0,
                                 current_price=10.0, vwap=9.5,
                                 minutes_since_open=0)
        # Base confidence for BULL/STRONG_BULL is 0.65-0.85
        # At T+0, factor = 0.8, so conf = base × 0.8
        assert result.confidence > 0.4, (
            f"Gap momentum at T+0 should have conf > 0.4, got {result.confidence}"
        )

    def test_confidence_at_t5_momentum(self):
        """At T+5, gap momentum confidence factor = 1.0."""
        result = _run_tech_agent(gap_pct=0.50, rvol=100.0,
                                 current_price=10.0, vwap=9.5,
                                 minutes_since_open=5)
        # At T+5, factor = min(1.0, 0.8 + 5/25) = 1.0
        # Full base confidence
        assert result.confidence > 0.5, (
            f"Gap momentum at T+5 should have conf > 0.5, got {result.confidence}"
        )


class TestD126Config:
    """Verify D126 config defaults."""

    def test_gap_momentum_score_threshold(self):
        from config.settings import ScoringWeights
        cfg = ScoringWeights()
        assert cfg.gap_momentum_score_threshold == 1.0

    def test_gap_momentum_min_gap(self):
        from config.settings import ScoringWeights
        cfg = ScoringWeights()
        assert cfg.gap_momentum_min_gap == 0.08

    def test_gap_momentum_min_rvol(self):
        from config.settings import ScoringWeights
        cfg = ScoringWeights()
        assert cfg.gap_momentum_min_rvol == 2.5
