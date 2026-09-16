"""
D115: Tests for Tiered Kelly Criterion Position Sizing.
"""

import pytest

from config.settings import KellyTierConfig
from src.core.kelly_tier import KellyTier, KellyTierClassifier, KellyTierResult


# ── Helpers ──────────────────────────────────────────────────────────

def _make_tracker(win_rate=0.40, tier3_count=0, had_stop=False):
    from unittest.mock import MagicMock
    tracker = MagicMock()
    tracker.win_rate.return_value = win_rate
    tracker.tier3_plus_count_today.return_value = tier3_count
    tracker.had_tier3_stop_today.return_value = had_stop
    return tracker


def _make_classifier(enabled=True, tracker=None, **overrides):
    # doc 286: pin the D208 default risk pcts explicitly — KellyTierConfig is a pydantic-settings
    # class that reads the operator .env (KELLY_TIER*_RISK_PCT is 0.0067 in prod since doc-286's
    # size package), and these tests assert the DEFAULT semantics. Env-independent forever.
    defaults = dict(tier1_risk_pct=0.02, tier2_risk_pct=0.04, tier3_risk_pct=0.06, tier4_risk_pct=0.08)
    defaults.update(overrides)
    cfg = KellyTierConfig(enabled=enabled, **defaults)
    return KellyTierClassifier(config=cfg, trade_tracker=tracker, daily_loss_limit_pct=0.10)


def _tier2_kwargs():
    """Kwargs that satisfy ALL Tier 2 requirements but NOT Tier 3."""
    return dict(
        mfcs=0.30,
        rvol=6.0,
        gap_pct=0.15,
        stop_loss_pct=0.055,
        catalyst_type="FDA_APPROVAL",
        catalyst_specificity="CONFIRMED",
        float_shares=10_000_000,
        sector="healthcare",
        directional_agent_count=3,
        vix_level=15.0,
        spy_return_pct=0.005,
        daily_realized_pnl=500.0,
        daily_unrealized_pnl=200.0,
        open_positions=[],
        portfolio_heat_pct=0.01,
        max_open_exit_urgency=0.05,
        minutes_since_open=10.0,
    )


def _tier3_kwargs():
    """Kwargs that satisfy ALL Tier 3 requirements but NOT Tier 4."""
    kw = _tier2_kwargs()
    kw.update(
        mfcs=0.40,  # Sweep: recalibrated from 0.85 (above T3=0.35, below T4=0.45)
        gap_pct=0.15,  # in [10%, 50%]
        float_shares=15_000_000,  # <= 20M
        vix_level=18.0,  # < 20
    )
    return kw


def _tier4_kwargs():
    """Kwargs that satisfy ALL Tier 4 requirements."""
    kw = _tier3_kwargs()
    kw.update(
        mfcs=0.50,  # Sweep: recalibrated from 0.92 (above T4=0.45)
        catalyst_specificity="CONFIRMED",
        daily_unrealized_pnl=300.0,
        portfolio_heat_pct=0.02,
        # reward_risk = 0.15 / 0.055 = 2.73 — need >= 3.0 for Tier 4
        gap_pct=0.20,  # 0.20 / 0.055 = 3.64
    )
    return kw


# ── Tier 1: Default ──────────────────────────────────────────────────

class TestTier1Default:
    def test_low_mfcs_returns_tier1(self):
        c = _make_classifier()
        result = c.classify(**{**_tier2_kwargs(), "mfcs": 0.20})
        assert result.tier == KellyTier.STANDARD
        # D208: Tier 1 risk raised from 1%→2%, max position 15%→25%
        assert result.risk_per_trade_pct == 0.02
        assert result.max_position_pct == 0.25

    def test_default_with_no_data(self):
        c = _make_classifier()
        result = c.classify(
            mfcs=0.20, rvol=1.5, gap_pct=0.05, stop_loss_pct=0.055,
            catalyst_type="NONE", catalyst_specificity="SPECULATIVE",
            float_shares=50_000_000, sector="tech",
            directional_agent_count=1, vix_level=None,
            spy_return_pct=None, daily_realized_pnl=0.0,
            minutes_since_open=45.0,
        )
        assert result.tier == KellyTier.STANDARD


# ── Tier 2: High Conviction ──────────────────────────────────────────

class TestTier2HighConviction:
    def test_all_checks_pass(self):
        c = _make_classifier(tracker=_make_tracker())
        result = c.classify(**_tier2_kwargs())
        assert result.tier == KellyTier.HIGH_CONVICTION
        # D208: Tier 2 risk raised from 2%→4%, max position 25%→35%
        assert result.risk_per_trade_pct == 0.04
        assert result.max_position_pct == 0.35

    def test_fails_on_low_mfcs(self):
        c = _make_classifier(tracker=_make_tracker())
        result = c.classify(**{**_tier2_kwargs(), "mfcs": 0.20})
        assert result.tier == KellyTier.STANDARD

    def test_fails_on_low_rvol(self):
        c = _make_classifier(tracker=_make_tracker())
        result = c.classify(**{**_tier2_kwargs(), "rvol": 3.0})
        assert result.tier == KellyTier.STANDARD

    def test_fails_on_wrong_catalyst(self):
        c = _make_classifier(tracker=_make_tracker())
        result = c.classify(**{**_tier2_kwargs(), "catalyst_type": "CORPORATE_UPDATE"})
        assert result.tier == KellyTier.STANDARD

    def test_fails_on_low_directional_count(self):
        c = _make_classifier(tracker=_make_tracker())
        result = c.classify(**{**_tier2_kwargs(), "directional_agent_count": 2})
        assert result.tier == KellyTier.STANDARD

    def test_fails_on_same_sector(self):
        """Same-sector open position blocks Tier 2."""
        from unittest.mock import MagicMock
        pos = MagicMock()
        pos.sector = "healthcare"
        pos.kelly_tier = 1
        c = _make_classifier(tracker=_make_tracker())
        result = c.classify(**{**_tier2_kwargs(), "open_positions": [pos]})
        assert result.tier == KellyTier.STANDARD

    def test_fails_on_late_time(self):
        c = _make_classifier(tracker=_make_tracker())
        result = c.classify(**{**_tier2_kwargs(), "minutes_since_open": 45.0})
        assert result.tier == KellyTier.STANDARD

    def test_fails_on_low_reward_risk(self):
        c = _make_classifier(tracker=_make_tracker())
        # gap=0.05 / stop=0.055 = 0.91 < 2.5
        result = c.classify(**{**_tier2_kwargs(), "gap_pct": 0.05})
        assert result.tier == KellyTier.STANDARD

    def test_fails_on_no_win_rate(self):
        """No trade tracker → win rate is None → fails Tier 2."""
        c = _make_classifier()  # No tracker
        result = c.classify(**_tier2_kwargs())
        assert result.tier == KellyTier.STANDARD

    def test_fails_on_low_win_rate(self):
        c = _make_classifier(tracker=_make_tracker(win_rate=0.25))
        result = c.classify(**_tier2_kwargs())
        assert result.tier == KellyTier.STANDARD


# ── Tier 3: Exceptional ──────────────────────────────────────────────

class TestTier3Exceptional:
    def test_all_checks_pass(self):
        c = _make_classifier(tracker=_make_tracker())
        result = c.classify(**_tier3_kwargs())
        assert result.tier == KellyTier.EXCEPTIONAL
        # D208: Tier 3 risk raised from 4%→6%, max position 35%→50%
        assert result.risk_per_trade_pct == 0.06
        assert result.max_position_pct == 0.50

    def test_fails_on_high_vix(self):
        c = _make_classifier(tracker=_make_tracker())
        result = c.classify(**{**_tier3_kwargs(), "vix_level": 22.0})
        assert result.tier == KellyTier.HIGH_CONVICTION

    def test_fails_on_large_float(self):
        c = _make_classifier(tracker=_make_tracker())
        result = c.classify(**{**_tier3_kwargs(), "float_shares": 50_000_000})
        assert result.tier == KellyTier.HIGH_CONVICTION

    def test_fails_on_spy_down(self):
        c = _make_classifier(tracker=_make_tracker())
        result = c.classify(**{**_tier3_kwargs(), "spy_return_pct": -0.01})
        assert result.tier == KellyTier.HIGH_CONVICTION

    def test_fails_on_negative_daily_pnl(self):
        c = _make_classifier(tracker=_make_tracker())
        result = c.classify(**{**_tier3_kwargs(), "daily_realized_pnl": -200.0})
        assert result.tier == KellyTier.HIGH_CONVICTION

    def test_fails_on_gap_too_small(self):
        c = _make_classifier(tracker=_make_tracker())
        result = c.classify(**{**_tier3_kwargs(), "gap_pct": 0.08})
        # gap=0.08 also breaks reward_risk for Tier 2 (0.08/0.055=1.45<2.5)
        assert result.tier < KellyTier.EXCEPTIONAL

    def test_fails_on_gap_too_large(self):
        c = _make_classifier(tracker=_make_tracker())
        result = c.classify(**{**_tier3_kwargs(), "gap_pct": 0.55})
        # Gap > 50% fails Tier 3 but may still pass Tier 2
        assert result.tier < KellyTier.EXCEPTIONAL

    def test_fails_on_wrong_catalyst(self):
        c = _make_classifier(tracker=_make_tracker())
        result = c.classify(**{**_tier3_kwargs(), "catalyst_type": "SHORT_SQUEEZE"})
        assert result.tier == KellyTier.HIGH_CONVICTION

    def test_fails_on_vix_none(self):
        """Missing VIX data should fail Tier 3 (safety-critical guardrail).
        Falls to Tier 2 since VIX isn't checked there."""
        c = _make_classifier(tracker=_make_tracker())
        result = c.classify(**{**_tier3_kwargs(), "vix_level": None})
        assert result.tier == KellyTier.HIGH_CONVICTION  # Falls to Tier 2, not 3

    def test_fails_on_spy_none(self):
        """Missing SPY data should fail Tier 3 (safety-critical guardrail).
        Falls to Tier 2 since SPY isn't checked there."""
        c = _make_classifier(tracker=_make_tracker())
        result = c.classify(**{**_tier3_kwargs(), "spy_return_pct": None})
        assert result.tier == KellyTier.HIGH_CONVICTION  # Falls to Tier 2, not 3


# ── Tier 4: Statistical Outlier ──────────────────────────────────────

class TestTier4StatisticalOutlier:
    def test_all_checks_pass(self):
        c = _make_classifier(tracker=_make_tracker(win_rate=0.50))
        result = c.classify(**_tier4_kwargs())
        assert result.tier == KellyTier.STATISTICAL_OUTLIER
        # D208: Tier 4 risk raised from 5%→8%, max position 40%→50%
        assert result.risk_per_trade_pct == 0.08
        assert result.max_position_pct == 0.50

    def test_fails_on_unconfirmed_catalyst(self):
        c = _make_classifier(tracker=_make_tracker(win_rate=0.50))
        result = c.classify(**{**_tier4_kwargs(), "catalyst_specificity": "RUMORED"})
        assert result.tier == KellyTier.EXCEPTIONAL

    def test_fails_on_negative_unrealized(self):
        c = _make_classifier(tracker=_make_tracker(win_rate=0.50))
        result = c.classify(**{**_tier4_kwargs(), "daily_unrealized_pnl": -100.0})
        assert result.tier == KellyTier.EXCEPTIONAL

    def test_fails_on_high_portfolio_heat(self):
        c = _make_classifier(tracker=_make_tracker(win_rate=0.50))
        result = c.classify(**{**_tier4_kwargs(), "portfolio_heat_pct": 0.05})
        assert result.tier == KellyTier.EXCEPTIONAL

    def test_fails_on_low_win_rate(self):
        c = _make_classifier(tracker=_make_tracker(win_rate=0.38))
        result = c.classify(**_tier4_kwargs())
        assert result.tier == KellyTier.EXCEPTIONAL

    def test_fails_on_concurrent_tier3_plus_open(self):
        """Tier 4 blocked when another Tier 3+ position is already open.
        Falls to Tier 3 since concurrent check is Tier 4 only."""
        from unittest.mock import MagicMock
        pos = MagicMock()
        pos.kelly_tier = 3
        pos.sector = "energy"  # Different sector so same-sector check passes
        c = _make_classifier(tracker=_make_tracker(win_rate=0.50))
        result = c.classify(**{**_tier4_kwargs(), "open_positions": [pos]})
        assert result.tier == KellyTier.EXCEPTIONAL  # Tier 3, not 4


# ── Safety Guardrails ────────────────────────────────────────────────

class TestSafetyGuardrails:
    def test_max_tier3_plus_per_day(self):
        """After 2 Tier 3+ trades today, cap at Tier 2."""
        c = _make_classifier(tracker=_make_tracker(win_rate=0.50, tier3_count=2))
        result = c.classify(**_tier3_kwargs())
        assert result.tier <= KellyTier.HIGH_CONVICTION

    def test_sequential_lockout(self):
        """After a Tier 3+ stop-loss today, no more Tier 3+."""
        c = _make_classifier(tracker=_make_tracker(win_rate=0.50, had_stop=True))
        result = c.classify(**_tier3_kwargs())
        assert result.tier <= KellyTier.HIGH_CONVICTION

    def test_tier2_still_available_during_lockout(self):
        """Lockout only blocks Tier 3+, not Tier 2."""
        c = _make_classifier(tracker=_make_tracker(win_rate=0.50, had_stop=True))
        result = c.classify(**_tier2_kwargs())
        assert result.tier == KellyTier.HIGH_CONVICTION


# ── Result Metadata ──────────────────────────────────────────────────

class TestResultMetadata:
    def test_to_log_dict(self):
        result = KellyTierResult(
            tier=KellyTier.HIGH_CONVICTION,
            risk_per_trade_pct=0.02,
            max_position_pct=0.25,
            reason="test",
            checks_passed=("mfcs=0.30>=0.25",),
            checks_failed=(),
        )
        d = result.to_log_dict()
        assert d["kelly_tier"] == 2
        assert d["kelly_tier_name"] == "HIGH_CONVICTION"
        assert d["risk_per_trade_pct"] == 0.02
        assert isinstance(d["checks_passed"], list)

    def test_checks_audit_trail(self):
        c = _make_classifier()
        result = c.classify(**{**_tier2_kwargs(), "mfcs": 0.20})
        assert result.tier == KellyTier.STANDARD
        # Failed checks should mention mfcs
        assert any("mfcs" in f for f in result.checks_failed)
