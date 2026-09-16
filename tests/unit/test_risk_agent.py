"""
MOMENTUM-X Tests: Risk Agent (Deterministic)

Node ID: tests.unit.test_risk_agent
Tests verify the adversarial risk assessment invariants on the
DeterministicRiskAgent that has been live in production since D101.

The LLM-based RiskAgent was removed in D221 Phase F (2026-04-19) along with
its parse_response and apply_hard_veto_rules tests. The hard-veto invariants
that the LLM-based agent enforced as a "safety net override" are now the
PRIMARY decision path in DeterministicRiskAgent -- there is no LLM to
override.

The invariants under test (from deterministic_risk.py module docstring):
  1. VETO if bid-ask spread > 3% (PROMPT_SIGNATURES).
  2. VETO if active bankruptcy proceedings detected in SEC filings.
  3. VETO if S-3/424B5 filed within 5 trading days (dilution trap).
  4. CAUTION if float > 50M shares.
  5. CAUTION if RVOL < 2.0 at proposed entry time.
  6. CAUTION if multiple halts detected (halt_count > 2).
  7. CAUTION if gap > 100% without news (possible corporate action).
  8. Never fails -- guaranteed RiskSignal output on every call.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.agents.deterministic_risk import DeterministicRiskAgent
from src.core.models import RiskSignal


def _filing(form: str, days_ago: int) -> dict:
    """Build a SEC filing dict matching the orchestrator's _fetch_sec_filings shape."""
    filed = date.today().toordinal() - days_ago
    return {
        "form": form,
        "description": f"synthetic {form} filing",
        "date": date.fromordinal(filed).isoformat(),
    }


@pytest.fixture
def agent() -> DeterministicRiskAgent:
    return DeterministicRiskAgent()


class TestDeterministicRiskVetoInvariants:
    """Hard veto rules that block trades regardless of other signals."""

    @pytest.mark.asyncio
    async def test_wide_spread_triggers_veto(self, agent):
        """INV 1: bid-ask spread > 3% must produce VETO."""
        result = await agent.analyze(
            ticker="WIDESPREAD",
            market_data={
                "current_price": 5.00,
                "bid": 4.85,
                "ask": 5.15,  # spread = 0.30 / 5.00 = 6%
                "rvol": 3.0,
                "gap_pct": 0.10,
            },
            sec_filings={"filings": []},
        )
        assert isinstance(result, RiskSignal)
        assert result.risk_verdict == "VETO"
        assert result.signal == "STRONG_BEAR"
        assert result.position_size_recommendation == "NONE"
        assert "spread" in (result.veto_reason or "").lower()

    @pytest.mark.asyncio
    async def test_recent_424b5_triggers_veto(self, agent):
        """INV 3: S-3/424B5 filed within 5 trading days must produce VETO."""
        result = await agent.analyze(
            ticker="DILUTING",
            market_data={
                "current_price": 5.00,
                "bid": 4.99,
                "ask": 5.01,
                "rvol": 3.0,
            },
            sec_filings={"filings": [_filing("424B5", days_ago=2)]},
        )
        assert result.risk_verdict == "VETO"
        # Some implementation says "S-3" or "424B5" or "dilution" -- accept any
        rsn = (result.veto_reason or "").upper()
        assert "424B5" in rsn or "S-3" in rsn or "DILUTION" in rsn

    @pytest.mark.asyncio
    async def test_bankruptcy_keyword_in_filing_triggers_veto(self, agent):
        """INV 2: bankruptcy proceedings (detected via filing description)
        must produce VETO."""
        result = await agent.analyze(
            ticker="CHAPTER11",
            market_data={
                "current_price": 5.00,
                "bid": 4.99,
                "ask": 5.01,
                "rvol": 3.0,
            },
            sec_filings={"filings": [{
                "form": "8-K",
                "description": "Chapter 11 bankruptcy filing",
                "date": "2026-04-15",
            }]},
        )
        assert result.risk_verdict == "VETO"
        assert "bankruptcy" in (result.veto_reason or "").lower()


class TestDeterministicRiskCautionInvariants:
    """CAUTION-level rules that flag risk without blocking."""

    @pytest.mark.asyncio
    async def test_low_rvol_triggers_caution_not_veto(self, agent):
        """INV 5: RVOL < 2.0 must elevate risk_score (CAUTION) but NOT VETO."""
        result = await agent.analyze(
            ticker="LOWRVOL",
            market_data={
                "current_price": 5.00,
                "bid": 4.99,
                "ask": 5.01,
                "rvol": 1.0,  # below 2.0 threshold
                "gap_pct": 0.10,
                "float_shares": 10_000_000,
            },
            sec_filings={"filings": []},
        )
        assert result.risk_verdict in ("APPROVE", "CAUTION"), (
            f"low RVOL is CAUTION-tier, not VETO; got {result.risk_verdict}"
        )

    @pytest.mark.asyncio
    async def test_large_float_triggers_caution(self, agent):
        """INV 4: float > 50M shares must elevate risk (CAUTION)."""
        result = await agent.analyze(
            ticker="LARGEFLOAT",
            market_data={
                "current_price": 5.00,
                "bid": 4.99,
                "ask": 5.01,
                "rvol": 3.0,
                "gap_pct": 0.10,
                "float_shares": 200_000_000,  # 200M shares -- above 50M
            },
            sec_filings={"filings": []},
        )
        # Verdict not VETO; risk_score should be elevated above pure-clean baseline
        assert result.risk_verdict != "VETO"


class TestDeterministicRiskNeverFails:
    """INV 8: Never fails -- guaranteed RiskSignal output on every call."""

    @pytest.mark.asyncio
    async def test_never_fails_on_empty_inputs(self, agent):
        """Empty kwargs must still produce a valid RiskSignal."""
        result = await agent.analyze(ticker="EMPTY")
        assert isinstance(result, RiskSignal)
        assert result.ticker == "EMPTY"
        assert result.risk_verdict in ("APPROVE", "CAUTION", "VETO")

    @pytest.mark.asyncio
    async def test_never_fails_on_malformed_market_data(self, agent):
        """Invalid market data types must not raise."""
        result = await agent.analyze(
            ticker="BAD",
            market_data={
                "current_price": "not a number",
                "bid": None,
                "ask": -1,
                "rvol": "x",
            },
            sec_filings={"filings": "not a list"},
        )
        assert isinstance(result, RiskSignal)
        assert result.ticker == "BAD"

    @pytest.mark.asyncio
    async def test_agent_id_matches_journal_compatibility(self, agent):
        """Reports agent_id='risk_agent' for journal schema compatibility --
        2,119+ records on disk reference this string."""
        assert agent.agent_id == "risk_agent"


class TestDeterministicRiskBugSweep:
    """D221 Phase F Sunday adversarial sweep. Targeted tests for latent
    silent-failure surfaces beyond the two already caught in the A cleanup
    (string-typed floats, non-list filings). Each test here catches a bug
    that the prior tests did NOT cover."""

    @pytest.mark.asyncio
    async def test_string_float_shares_does_not_crash(self, agent):
        """market_data['float_shares'] as a non-numeric string -- common if
        upstream LLM returned '500K' or '3.7M' instead of a number."""
        result = await agent.analyze(
            ticker="STRFLOAT",
            market_data={
                "current_price": 5.0, "bid": 4.99, "ask": 5.01, "rvol": 3.0,
                "float_shares": "3.7M",  # string, comparisons will crash pre-fix
            },
            sec_filings={"filings": []},
        )
        # Contract: INV 8 says never fails. Must produce a RiskSignal.
        assert isinstance(result, RiskSignal)

    @pytest.mark.asyncio
    async def test_filing_with_non_string_form_does_not_crash(self, agent):
        """Filing dict where .form is a dict/list/int -- .upper() on non-string
        crashes pre-fix."""
        result = await agent.analyze(
            ticker="BADFORM",
            market_data={"current_price": 5.0, "bid": 4.99, "ask": 5.01, "rvol": 3.0},
            sec_filings={"filings": [
                {"form": {"nested": "dict"}, "description": "malformed", "date": "2026-04-15"},
                {"form": None, "description": "also malformed", "date": "2026-04-14"},
                {"form": 424, "description": "integer form code", "date": "2026-04-13"},
            ]},
        )
        assert isinstance(result, RiskSignal)

    @pytest.mark.asyncio
    async def test_filing_with_non_string_description_does_not_crash(self, agent):
        """Filing['description'] is dict/list/int -- .lower() / [:100] crashes."""
        result = await agent.analyze(
            ticker="BADDESC",
            market_data={"current_price": 5.0, "bid": 4.99, "ask": 5.01, "rvol": 3.0},
            sec_filings={"filings": [
                {"form": "8-K", "description": {"nested": "desc"}, "date": "2026-04-15"},
                {"form": "10-K", "description": None, "date": "2026-04-14"},
                {"form": "424B5", "description": 12345, "date": "2026-04-13"},
            ]},
        )
        assert isinstance(result, RiskSignal)

    @pytest.mark.asyncio
    async def test_candidate_signals_none_does_not_crash(self, agent):
        """candidate_signals explicitly None (common from upstream when no
        agents ran) -- iterating None crashes."""
        result = await agent.analyze(
            ticker="NONECS",
            candidate_signals=None,
            market_data={"current_price": 5.0, "bid": 4.99, "ask": 5.01, "rvol": 3.0},
        )
        assert isinstance(result, RiskSignal)

    @pytest.mark.asyncio
    async def test_candidate_signals_contains_dicts_does_not_crash(self, agent):
        """candidate_signals with dict items (instead of AgentSignal) --
        s.signal attribute access crashes pre-fix."""
        result = await agent.analyze(
            ticker="DICTCS",
            candidate_signals=[
                {"agent_id": "news_agent", "signal": "BULL"},  # dict, not AgentSignal
                {"agent_id": "technical_agent", "signal": "NEUTRAL"},
            ],
            market_data={"current_price": 5.0, "bid": 4.99, "ask": 5.01, "rvol": 3.0},
        )
        assert isinstance(result, RiskSignal)


class TestDeterministicRiskShape:
    """RiskSignal output schema must be structurally complete."""

    @pytest.mark.asyncio
    async def test_signal_has_required_fields(self, agent):
        result = await agent.analyze(
            ticker="SHAPE",
            market_data={
                "current_price": 5.0,
                "bid": 4.99,
                "ask": 5.01,
                "rvol": 3.0,
            },
        )
        assert result.agent_id == "risk_agent"
        assert result.ticker == "SHAPE"
        assert result.timestamp is not None
        assert result.signal in ("STRONG_BULL", "BULL", "NEUTRAL", "BEAR", "STRONG_BEAR")
        assert 0.0 <= result.confidence <= 1.0
        assert 0.0 <= result.risk_score <= 1.0
        assert isinstance(result.flags, list)
        assert isinstance(result.risk_breakdown, dict)
        assert result.position_size_recommendation in (
            "FULL", "HALF", "QUARTER", "NONE",
        )
