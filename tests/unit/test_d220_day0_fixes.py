"""
D220 Phase 1 (Day 0) regression tests.

Tests pinning the three Day-0 fixes that unblocked trading:
  1. D112 router float-cap with MFCS escape hatch (config 200M -> 2B,
     plus the (mfcs is None or mfcs < 0.40) guard in adaptive_router.py)
  2. D219 enrichment sanity check (drop shareOutstanding >= 50_000 millions
     to catch Finnhub units bugs like XHG=98_972 returning 98B "shares")
  3. VWAP bias gate loosening (skip first 10min, threshold 0.5% -> 2%)

Reference: docs/research-log/03_immediate_fixes.md and 05_action_plan.md.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from config.settings import RouterConfig
from src.core.adaptive_router import AdaptiveComputeRouter, EvalTier
from src.core.models import CandidateStock


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_candidate(**overrides) -> CandidateStock:
    defaults = dict(
        ticker="TEST",
        company_name="Test Corp",
        current_price=5.0,
        previous_close=4.5,
        gap_pct=0.10,
        gap_classification="SIGNIFICANT",
        rvol=5.0,
        premarket_volume=500_000,
        float_shares=5_000_000,
        market_cap=25_000_000.0,
        scan_timestamp=datetime.now(timezone.utc),
        scan_phase="MARKET_OPEN",
    )
    defaults.update(overrides)
    return CandidateStock(**defaults)


def _make_router(**config_overrides) -> AdaptiveComputeRouter:
    config = RouterConfig(**config_overrides)
    return AdaptiveComputeRouter(config=config, max_entry_spread_pct=0.01)


# ── Fix 1 — D112 router float-cap escape hatch ──────────────────────────


class TestD112FloatEscapeHatch:
    """The D220 router fix: float >max_float passes if deterministic_mfcs is strong."""

    def test_router_passes_large_float_with_strong_mfcs(self):
        """3B float + MFCS=0.45 (above deterministic_strong_pass=0.40) → escapes INSTANT_REJECT.

        This is today's IMMP/VSA/QBTS scenario at scale. Pre-D220 (cap 200M) any
        of the three would have been INSTANT_REJECT; post-D220 the strong
        deterministic MFCS bypasses the structural float check.
        """
        router = _make_router()
        candidate = _make_candidate(float_shares=3_000_000_000)
        decision = router.classify(candidate, deterministic_mfcs=0.45)
        assert decision.tier != EvalTier.INSTANT_REJECT, (
            f"Expected escape from INSTANT_REJECT but got {decision.tier} "
            f"with reason: {decision.reason}"
        )

    def test_router_rejects_large_float_with_weak_mfcs(self):
        """3B float + MFCS=0.20 (below deterministic_strong_pass=0.40) → INSTANT_REJECT.

        Confirms the escape hatch is gated on MFCS quality, not just float size.
        """
        router = _make_router()
        candidate = _make_candidate(float_shares=3_000_000_000)
        decision = router.classify(candidate, deterministic_mfcs=0.20)
        assert decision.tier == EvalTier.INSTANT_REJECT
        assert "float" in decision.reason and "max" in decision.reason

    def test_router_rejects_large_float_with_no_mfcs(self):
        """3B float + MFCS=None → INSTANT_REJECT (preserves status quo for unscored).

        When the router runs before any deterministic scoring, it can't make an
        informed bypass decision. Default to the conservative behavior (reject).
        """
        router = _make_router()
        candidate = _make_candidate(float_shares=3_000_000_000)
        decision = router.classify(candidate, deterministic_mfcs=None)
        assert decision.tier == EvalTier.INSTANT_REJECT
        assert "float" in decision.reason

    def test_router_default_max_float_is_2b(self):
        """The Apr 16 hotfix: default raised from 200M to 2B."""
        config = RouterConfig()
        assert config.instant_reject_max_float == 2_000_000_000

    def test_router_min_price_unchanged(self):
        """HUBC at $0.16 was rejected correctly — leave the $0.50 floor in place."""
        config = RouterConfig()
        assert config.instant_reject_min_price == 0.50


# ── Fix 2 — D219 enrichment sanity check ────────────────────────────────


def _enrich_logic(share_outstanding: float | None, market_cap: float | None,
                  ticker: str = "TEST", logger_arg: logging.Logger | None = None) -> dict:
    """Replicate the D220 enrichment branch from main.py:1445-1460.

    Kept in sync with main.py manually — the test_d220_enrichment_inline_matches
    integration test below verifies they don't drift.
    """
    log = logger_arg or logging.getLogger("test")
    enrich = {"float_shares": None, "market_cap": None}
    so = share_outstanding
    mc = market_cap
    if so and 0 < so < 50_000:
        enrich["float_shares"] = int(so * 1_000_000 * 0.80)
    elif so and so >= 50_000:
        log.warning(
            "D220 enrichment: dropping implausible shareOutstanding for %s: %s (>=50,000M)",
            ticker, so,
        )
    if mc and mc > 0:
        enrich["market_cap"] = mc * 1_000_000
    return enrich


class TestD219EnrichmentSanity:
    """The D220 sanity check: shareOutstanding above 50,000 millions (50B shares) is a Finnhub bug."""

    def test_d219_enrichment_drops_implausible_shares(self):
        """XHG-style bug: shareOutstanding=98_972 (= 98B shares). Drop it."""
        result = _enrich_logic(share_outstanding=98_972, market_cap=500.0)
        assert result["float_shares"] is None, (
            "98_972M shares is implausible (Microsoft has ~7B); should be dropped"
        )
        # Market cap is still recorded — only float was bogus
        assert result["market_cap"] == 500_000_000.0

    def test_d219_enrichment_keeps_valid_shares(self):
        """Realistic small-cap: shareOutstanding=1_200M shares. Float = 80% = 960M."""
        result = _enrich_logic(share_outstanding=1_200, market_cap=4_500.0)
        assert result["float_shares"] == 960_000_000, (
            f"Expected 960M float, got {result['float_shares']:,}"
        )
        assert result["market_cap"] == 4_500_000_000.0

    def test_d219_enrichment_drops_zero(self):
        """Zero is invalid input."""
        result = _enrich_logic(share_outstanding=0, market_cap=100.0)
        assert result["float_shares"] is None

    def test_d219_enrichment_drops_negative(self):
        """Negative is invalid input."""
        result = _enrich_logic(share_outstanding=-50, market_cap=100.0)
        assert result["float_shares"] is None

    def test_d219_enrichment_logs_warning_on_implausible(self, caplog):
        """The drop should log a WARNING with the ticker so we can audit data feed quality."""
        caplog.set_level(logging.WARNING)
        _enrich_logic(share_outstanding=98_972, market_cap=500.0, ticker="XHG")
        assert any(
            "XHG" in rec.message and "implausible" in rec.message.lower()
            for rec in caplog.records
        )

    def test_d220_enrichment_inline_matches_helper(self):
        """Drift guard: ensure the helper above stays in sync with main.py.

        Reads main.py source, finds the enrichment block, asserts the constants
        we use here (50_000 ceiling, 0.80 multiplier) match.
        """
        from pathlib import Path
        main_src = (Path(__file__).resolve().parents[2] / "main.py").read_text(encoding="utf-8")
        # The exact constants must be present in the live code
        assert "0 < _so < 50_000" in main_src, (
            "main.py:1450-area no longer uses the 50,000-millions sanity ceiling — "
            "update this test or re-add the guard"
        )
        assert "_so * 1_000_000 * 0.80" in main_src, (
            "main.py:1451-area no longer uses 80% float-of-outstanding — "
            "update this test or re-add the guard"
        )


# ── Fix 3 — VWAP bias gate (skip first 10min, 2% threshold) ─────────────


def _vwap_skip_decision(now_et: datetime, vwap: float | None,
                         current_price: float) -> tuple[bool, str | None]:
    """Replicate the D220 VWAP gate logic from orchestrator.py:1322-1360.

    Returns (would_reject, rejection_reason). would_reject=False means the
    candidate proceeds past this gate.
    """
    market_open_minute = now_et.hour * 60 + now_et.minute
    if 570 <= market_open_minute < 580:  # 9:30-9:40 ET
        return (False, None)
    if vwap is None:
        return (False, None)
    if current_price < vwap and vwap > 0:
        deficit_pct = (vwap - current_price) / vwap * 100
        if deficit_pct > 2.0:
            return (True, f"D101 VWAP bias: {deficit_pct:.1f}% below VWAP")
    return (False, None)


class TestVwapGate:
    """The D220 VWAP fix: skip first 10min, threshold raised from 0.5% to 2%."""

    def test_vwap_gate_skipped_in_first_10_min(self):
        """At 9:35 ET (5min after open), 1% below VWAP should NOT reject."""
        now_et = datetime(2026, 4, 17, 9, 35, tzinfo=ZoneInfo("America/New_York"))
        rejected, reason = _vwap_skip_decision(now_et, vwap=10.0, current_price=9.90)
        assert not rejected, f"Should skip in first 10min, got: {reason}"

    def test_vwap_gate_skipped_at_first_minute(self):
        """At 9:30 ET (open), even big deficit should skip."""
        now_et = datetime(2026, 4, 17, 9, 30, tzinfo=ZoneInfo("America/New_York"))
        rejected, _ = _vwap_skip_decision(now_et, vwap=10.0, current_price=9.50)
        assert not rejected

    def test_vwap_gate_active_at_3_pct_after_10_min(self):
        """At 10:00 ET, 3% below VWAP should reject."""
        now_et = datetime(2026, 4, 17, 10, 0, tzinfo=ZoneInfo("America/New_York"))
        rejected, reason = _vwap_skip_decision(now_et, vwap=10.0, current_price=9.70)
        assert rejected, "3% below VWAP at 10:00 ET should reject"
        assert reason and "3.0%" in reason

    def test_vwap_gate_passes_at_1_pct_after_10_min(self):
        """At 10:00 ET, 1% below VWAP (within new 2% tolerance) should NOT reject.

        Pre-D220: rejected (>0.5%). Post-D220: passes (<2%).
        """
        now_et = datetime(2026, 4, 17, 10, 0, tzinfo=ZoneInfo("America/New_York"))
        rejected, _ = _vwap_skip_decision(now_et, vwap=10.0, current_price=9.90)
        assert not rejected, "1% below VWAP at 10:00 ET should pass under D220"

    def test_vwap_gate_skips_when_vwap_unavailable(self):
        """No VWAP signal → no gate, regardless of price."""
        now_et = datetime(2026, 4, 17, 11, 0, tzinfo=ZoneInfo("America/New_York"))
        rejected, _ = _vwap_skip_decision(now_et, vwap=None, current_price=5.0)
        assert not rejected

    def test_orchestrator_vwap_constants(self):
        """Drift guard: ensure orchestrator.py still uses 580 (10min) and 2.0 threshold."""
        from pathlib import Path
        orch_src = (Path(__file__).resolve().parents[2] / "src" / "core" / "orchestrator.py").read_text(encoding="utf-8")
        assert "570 <= _market_open_minute < 580" in orch_src, (
            "VWAP skip window no longer 9:30-9:40 ET; update test or re-add"
        )
        assert "_vwap_deficit_pct > 2.0" in orch_src, (
            "VWAP threshold no longer 2%; update test or re-add"
        )
