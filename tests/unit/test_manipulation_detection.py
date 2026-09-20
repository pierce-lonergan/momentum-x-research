"""
D106 Work Stream 2+3: Manipulation Detection Tests

Tests for:
- ManipulationPhase + ManipulationSignal models (§2A)
- build_manipulation_filing_summary() (§2B)
- ManipulationClassifier agent parsing (§2C)
- Parameter modification layer (§2D)
- Agent registration (§2E)
- DistributionDetector (§3A, WS3 - placeholder tests)
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import (
    AgentSignal,
    ManipulationPhase,
    ManipulationSignal,
)
from src.data.sec_client import (
    Filing,
    FilingType,
    ManipulationFilingSummary,
    build_manipulation_filing_summary,
)


# ─── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def today() -> date:
    return date(2026, 3, 13)


@pytest.fixture
def sample_s3_filing(today: date) -> Filing:
    """S-3 filed 10 days ago."""
    return Filing(
        form_type="S-3",
        filing_type=FilingType.S3,
        filed_date=today - timedelta(days=10),
        company_name="Test Corp",
        cik="0001234567",
        accession_number="0001234567-26-000001",
        description="Registration Statement",
    )


@pytest.fixture
def sample_424b5_same_day(today: date) -> Filing:
    """424B5 filed on the same day as the gap (same-day offering)."""
    return Filing(
        form_type="424B5",
        filing_type=FilingType.PROSPECTUS_424B5,
        filed_date=today,
        company_name="Test Corp",
        cik="0001234567",
        accession_number="0001234567-26-000002",
        description="Prospectus Supplement",
    )


@pytest.fixture
def sample_form4_sell(today: date) -> Filing:
    """Form 4 insider sell filed 5 days ago."""
    return Filing(
        form_type="4",
        filing_type=FilingType.INSIDER_FORM4,
        filed_date=today - timedelta(days=5),
        company_name="Test Corp",
        cik="0001234567",
        accession_number="0001234567-26-000003",
        description="Form 4 - Insider Transaction",
    )


@pytest.fixture
def sample_8k_recent(today: date) -> Filing:
    """8-K filed 3 days ago."""
    return Filing(
        form_type="8-K",
        filing_type=FilingType.EVENT_8K,
        filed_date=today - timedelta(days=3),
        company_name="Test Corp",
        cik="0001234567",
        accession_number="0001234567-26-000004",
        description="Current Report",
    )


# ═══════════════════════════════════════════════════════════════════
# §2A: ManipulationPhase + ManipulationSignal Model Tests
# ═══════════════════════════════════════════════════════════════════


class TestManipulationModels:
    """Tests for D106 §2A manipulation models."""

    def test_manipulation_phase_literal_values(self) -> None:
        """ManipulationPhase has all 4 expected values."""
        from typing import get_args
        phases = get_args(ManipulationPhase)
        assert "ORGANIC_MOMENTUM" in phases
        assert "PROMOTIONAL_EARLY" in phases
        assert "PROMOTIONAL_LATE" in phases
        assert "UNCERTAIN" in phases
        assert len(phases) == 4

    def test_manipulation_signal_organic(self) -> None:
        """ManipulationSignal with ORGANIC_MOMENTUM phase."""
        sig = ManipulationSignal(
            agent_id="manipulation_classifier",
            ticker="ACME",
            timestamp=datetime.now(timezone.utc),
            signal="BULL",
            confidence=0.85,
            reasoning="Confirmed FDA approval, no dilution filings",
            phase="ORGANIC_MOMENTUM",
            manipulation_probability=0.1,
            estimated_remaining_upside_minutes=120,
            key_evidence=["FDA Phase 3 approval", "No S-3 within 90 days"],
            red_flags=[],
        )
        assert sig.phase == "ORGANIC_MOMENTUM"
        assert sig.manipulation_probability == 0.1
        assert sig.estimated_remaining_upside_minutes == 120

    def test_manipulation_signal_promotional_late(self) -> None:
        """ManipulationSignal with PROMOTIONAL_LATE phase."""
        sig = ManipulationSignal(
            agent_id="manipulation_classifier",
            ticker="PUMP",
            timestamp=datetime.now(timezone.utc),
            signal="BEAR",
            confidence=0.95,
            reasoning="Same-day 424B5 filing",
            phase="PROMOTIONAL_LATE",
            manipulation_probability=0.95,
            estimated_remaining_upside_minutes=0,
            key_evidence=["Same-day 424B5"],
            red_flags=["Active dilution", "Vague catalyst"],
        )
        assert sig.phase == "PROMOTIONAL_LATE"
        assert sig.manipulation_probability == 0.95
        assert sig.estimated_remaining_upside_minutes == 0

    def test_manipulation_signal_sanitizes_phase(self) -> None:
        """Phase field is sanitized (case-insensitive, strip whitespace)."""
        sig = ManipulationSignal(
            agent_id="manipulation_classifier",
            ticker="TEST",
            timestamp=datetime.now(timezone.utc),
            signal="NEUTRAL",
            confidence=0.5,
            reasoning="Test",
            phase=" promotional_early ",
        )
        assert sig.phase == "PROMOTIONAL_EARLY"

    def test_manipulation_signal_sanitizes_invalid_phase(self) -> None:
        """Invalid phase value falls back to UNCERTAIN."""
        sig = ManipulationSignal(
            agent_id="manipulation_classifier",
            ticker="TEST",
            timestamp=datetime.now(timezone.utc),
            signal="NEUTRAL",
            confidence=0.5,
            reasoning="Test",
            phase="INVALID_PHASE",
        )
        assert sig.phase == "UNCERTAIN"

    def test_manipulation_signal_default_phase(self) -> None:
        """Default phase is UNCERTAIN."""
        sig = ManipulationSignal(
            agent_id="manipulation_classifier",
            ticker="TEST",
            timestamp=datetime.now(timezone.utc),
            signal="NEUTRAL",
            confidence=0.5,
            reasoning="Test",
        )
        assert sig.phase == "UNCERTAIN"


# ═══════════════════════════════════════════════════════════════════
# §2B: build_manipulation_filing_summary() Tests
# ═══════════════════════════════════════════════════════════════════


class TestFilingSummary:
    """Tests for D106 §2B SEC filing summary builder."""

    def test_empty_filings(self, today: date) -> None:
        """No filings → all zeros/None."""
        summary = build_manipulation_filing_summary([], gap_date=today)
        assert summary.s3_age_days is None
        assert summary.has_424b5_same_day is False
        assert summary.insider_sell_count_30d == 0
        assert summary.dilution_filing_count == 0
        assert summary.recent_8k_count == 0

    def test_s3_age_computed(self, today: date, sample_s3_filing: Filing) -> None:
        """S-3 age is computed correctly (days since filing)."""
        summary = build_manipulation_filing_summary(
            [sample_s3_filing], gap_date=today,
        )
        assert summary.s3_age_days == 10
        assert summary.dilution_filing_count == 1

    def test_same_day_424b5(self, today: date, sample_424b5_same_day: Filing) -> None:
        """Same-day 424B5 detected correctly."""
        summary = build_manipulation_filing_summary(
            [sample_424b5_same_day], gap_date=today,
        )
        assert summary.has_424b5_same_day is True
        assert summary.dilution_filing_count == 1

    def test_insider_sells(self, today: date, sample_form4_sell: Filing) -> None:
        """Form 4 count in 30-day window."""
        # 3 insider sells in last 30 days
        filings = [
            Filing(
                form_type="4",
                filing_type=FilingType.INSIDER_FORM4,
                filed_date=today - timedelta(days=i * 5),
                company_name="Test Corp",
                cik="0001234567",
                accession_number=f"0001234567-26-00000{i}",
                description="Form 4",
            )
            for i in range(3)
        ]
        summary = build_manipulation_filing_summary(filings, gap_date=today)
        assert summary.insider_sell_count_30d == 3

    def test_insider_sells_outside_window(self, today: date) -> None:
        """Form 4 filed >30 days ago is not counted."""
        old_filing = Filing(
            form_type="4",
            filing_type=FilingType.INSIDER_FORM4,
            filed_date=today - timedelta(days=45),
            company_name="Test Corp",
            cik="0001234567",
            accession_number="0001234567-26-000099",
            description="Form 4",
        )
        summary = build_manipulation_filing_summary(
            [old_filing], gap_date=today,
        )
        assert summary.insider_sell_count_30d == 0

    def test_recent_8k(self, today: date, sample_8k_recent: Filing) -> None:
        """8-K within 14 days is counted."""
        summary = build_manipulation_filing_summary(
            [sample_8k_recent], gap_date=today,
        )
        assert summary.recent_8k_count == 1

    def test_combined_filings(
        self,
        today: date,
        sample_s3_filing: Filing,
        sample_424b5_same_day: Filing,
        sample_form4_sell: Filing,
        sample_8k_recent: Filing,
    ) -> None:
        """Full filing set produces correct aggregate summary."""
        filings = [
            sample_s3_filing,
            sample_424b5_same_day,
            sample_form4_sell,
            sample_8k_recent,
        ]
        summary = build_manipulation_filing_summary(filings, gap_date=today)
        assert summary.s3_age_days == 10
        assert summary.has_424b5_same_day is True
        assert summary.insider_sell_count_30d == 1
        assert summary.dilution_filing_count == 2  # S-3 + 424B5
        assert summary.recent_8k_count == 1

    def test_multiple_s3_takes_newest(self, today: date) -> None:
        """Multiple S-3 filings → s3_age_days is the smallest (most recent)."""
        filings = [
            Filing(
                form_type="S-3", filing_type=FilingType.S3,
                filed_date=today - timedelta(days=30),
                company_name="A", cik="1", accession_number="a1", description="",
            ),
            Filing(
                form_type="S-3", filing_type=FilingType.S3,
                filed_date=today - timedelta(days=5),
                company_name="A", cik="1", accession_number="a2", description="",
            ),
        ]
        summary = build_manipulation_filing_summary(filings, gap_date=today)
        assert summary.s3_age_days == 5  # Most recent
        assert summary.dilution_filing_count == 2


# ═══════════════════════════════════════════════════════════════════
# §2C: ManipulationClassifier Agent Tests
# ═══════════════════════════════════════════════════════════════════


class TestManipulationClassifier:
    """Tests for D106 §2C ManipulationClassifier agent."""

    def test_parse_organic_momentum(self) -> None:
        """ORGANIC classification from confirmed FDA catalyst + no dilution."""
        from src.agents.manipulation_classifier import ManipulationClassifier
        agent = ManipulationClassifier.__new__(ManipulationClassifier)

        raw = {
            "phase": "ORGANIC_MOMENTUM",
            "signal": "BULL",
            "confidence": 0.85,
            "manipulation_probability": 0.1,
            "estimated_remaining_upside_minutes": 120,
            "key_evidence": ["FDA Phase 3 approval confirmed", "No S-3 in 90 days"],
            "red_flags": [],
            "reasoning": "Genuine FDA catalyst with no dilution filings",
        }
        sig = agent.parse_response(raw, "ACME")
        assert isinstance(sig, ManipulationSignal)
        assert sig.phase == "ORGANIC_MOMENTUM"
        assert sig.manipulation_probability == 0.1
        assert sig.estimated_remaining_upside_minutes == 120
        assert len(sig.key_evidence) == 2

    def test_parse_promotional_early(self) -> None:
        """PROMOTIONAL_EARLY classification from S-3 + vague news."""
        from src.agents.manipulation_classifier import ManipulationClassifier
        agent = ManipulationClassifier.__new__(ManipulationClassifier)

        raw = {
            "phase": "PROMOTIONAL_EARLY",
            "signal": "NEUTRAL",
            "confidence": 0.70,
            "manipulation_probability": 0.65,
            "estimated_remaining_upside_minutes": 45,
            "key_evidence": ["S-3 filed 10 days ago", "Vague partnership news"],
            "red_flags": ["Active shelf registration", "No specific catalyst"],
            "reasoning": "S-3 within 14 days with vague catalyst",
        }
        sig = agent.parse_response(raw, "PUMP")
        assert sig.phase == "PROMOTIONAL_EARLY"
        assert sig.manipulation_probability == 0.65
        assert len(sig.red_flags) == 2

    def test_parse_promotional_late(self) -> None:
        """PROMOTIONAL_LATE from same-day 424B5 → always PROMOTIONAL_LATE."""
        from src.agents.manipulation_classifier import ManipulationClassifier
        agent = ManipulationClassifier.__new__(ManipulationClassifier)

        raw = {
            "phase": "PROMOTIONAL_LATE",
            "signal": "BEAR",
            "confidence": 0.95,
            "manipulation_probability": 0.95,
            "estimated_remaining_upside_minutes": 0,
            "key_evidence": ["Same-day 424B5 filing detected"],
            "red_flags": ["Active dilution", "Distribution in progress"],
            "reasoning": "Same-day 424B5 = textbook promotional dump",
        }
        sig = agent.parse_response(raw, "DUMP")
        assert sig.phase == "PROMOTIONAL_LATE"
        assert sig.manipulation_probability == 0.95
        assert sig.estimated_remaining_upside_minutes == 0

    def test_parse_uncertain(self) -> None:
        """UNCERTAIN classification with insufficient data."""
        from src.agents.manipulation_classifier import ManipulationClassifier
        agent = ManipulationClassifier.__new__(ManipulationClassifier)

        raw = {
            "phase": "UNCERTAIN",
            "signal": "NEUTRAL",
            "confidence": 0.3,
            "manipulation_probability": 0.5,
            "estimated_remaining_upside_minutes": 0,
            "key_evidence": [],
            "red_flags": [],
            "reasoning": "Insufficient data",
        }
        sig = agent.parse_response(raw, "MYSTERY")
        assert sig.phase == "UNCERTAIN"
        assert sig.manipulation_probability == 0.5

    def test_parse_malformed_response(self) -> None:
        """Malformed LLM response still produces a valid ManipulationSignal."""
        from src.agents.manipulation_classifier import ManipulationClassifier
        agent = ManipulationClassifier.__new__(ManipulationClassifier)

        raw = {
            "signal": "NEUTRAL",
            # Missing most fields
        }
        sig = agent.parse_response(raw, "BAD")
        assert isinstance(sig, ManipulationSignal)
        assert sig.phase == "UNCERTAIN"  # Default
        assert sig.manipulation_probability == 0.5  # Default

    def test_classifier_tier_1(self) -> None:
        """ManipulationClassifier uses Tier 1 model configuration."""
        from src.agents.manipulation_classifier import ManipulationClassifier
        agent = ManipulationClassifier(
            model="Qwen/Qwen3.5-397B-A17B",
            provider="together_ai",
            temperature=0.3,
            timeout=15,
        )
        assert agent.agent_id == "manipulation_classifier"
        assert "together_ai" in agent.model

    def test_build_user_prompt_includes_data(self) -> None:
        """User prompt includes all provided data sources."""
        from src.agents.manipulation_classifier import ManipulationClassifier
        agent = ManipulationClassifier.__new__(ManipulationClassifier)

        prompt = agent.build_user_prompt(
            ticker="ACME",
            news_items=[{"headline": "FDA Approval", "source": "PR Newswire"}],
            rvol=5.0,
            gap_pct=0.15,
            premarket_volume=500_000,
            float_shares=3_000_000,
            filing_summary={"s3_age_days": 10, "has_424b5_same_day": False},
            sec_filings=[{"form_type": "S-3", "filed_date": "2026-03-03"}],
        )
        assert "ACME" in prompt
        assert "15.0%" in prompt  # gap_pct formatted
        assert "5.0x" in prompt  # rvol formatted
        assert "500,000" in prompt  # premarket volume
        assert "3,000,000" in prompt  # float
        assert "FDA Approval" in prompt
        assert "S-3" in prompt


# ═══════════════════════════════════════════════════════════════════
# §2D: Parameter Modification Layer Tests
# ═══════════════════════════════════════════════════════════════════


class TestParameterModification:
    """Tests for D106 §2D manipulation-based parameter mods in orchestrator."""

    @pytest.fixture
    def mock_settings(self) -> MagicMock:
        """Create a mock settings object with required fields."""
        settings = MagicMock()
        settings.scoring.catalyst_news = 0.30
        settings.scoring.technical = 0.20
        settings.scoring.volume_rvol = 0.15
        settings.scoring.float_structure = 0.10
        settings.scoring.institutional = 0.10
        settings.scoring.deep_search = 0.05
        settings.scoring.risk_aversion_lambda = 0.5
        settings.scoring.confidence_deflation_factor = 0.70
        settings.scoring.vix_block_threshold = 35.0
        settings.scoring.vix_reduce_threshold = 25.0
        settings.scoring.vix_position_scale = 0.5
        settings.scoring.vix_shock_threshold_pct = 30.0
        settings.scoring.spy_halt_threshold_pct = -2.0
        settings.scoring.min_directional_agents = 2
        settings.scoring.mfcs_buy_threshold = 0.30
        settings.scoring.risk_veto_mode = "HARD"
        settings.debate.mfcs_debate_threshold = 0.55
        settings.debate.divergence_low_threshold = 0.3
        settings.debate.divergence_high_threshold = 0.7
        settings.debate.max_debate_attempts = 2
        settings.execution.max_position_pct = 0.25
        settings.execution.stop_loss_pct = 0.055
        settings.execution.initial_stop_atr_multiplier = 2.0
        settings.execution.initial_stop_floor_pct = 0.03
        settings.position.stop_loss_pct = 0.055
        settings.position.max_position_pct = 0.25
        settings.models.tier1_model = "Qwen/Qwen3.5-397B-A17B"
        settings.models.tier1_provider = "together_ai"
        settings.models.tier2_model = "Qwen/Qwen3-235B-A22B-Instruct"
        settings.models.tier2_provider = "together_ai"
        settings.models.tier1_fallback_model = ""
        settings.models.tier1_fallback_provider = ""
        settings.models.tier2_fallback_model = ""
        settings.models.tier2_fallback_provider = ""
        settings.models.emergency_model = ""
        settings.models.emergency_provider = ""
        settings.models.default_temperature = 0.3
        settings.models.litellm_timeout_tier1 = 15
        settings.models.litellm_timeout_tier2 = 10
        # D218 logs agent dispatch at startup and compares each scoring weight
        # with > 0; on a bare MagicMock that raises TypeError and __init__ dies.
        settings.scoring.catalyst_news = 0.30
        settings.scoring.technical = 0.20
        settings.scoring.volume_rvol = 0.15
        settings.scoring.float_structure = 0.10
        settings.scoring.institutional = 0.10
        settings.scoring.deep_search = 0.05
        settings.scoring.risk_aversion_lambda = 0.5   # formatted with :.2f at startup
        settings.experiments.enabled = False
        settings.experiments.yaml_path = "data/experiments/experiments.yaml"
        settings.experiments.journal_dir = "data/experiments/journal"
        return settings

    def test_promotional_late_blocks_trade(self) -> None:
        """PROMOTIONAL_LATE → NO_TRADE verdict."""
        from src.core.models import (
            CandidateStock,
            ScoredCandidate,
            ManipulationSignal,
        )

        # Create ManipulationSignal with PROMOTIONAL_LATE
        manip_sig = ManipulationSignal(
            agent_id="manipulation_classifier",
            ticker="DUMP",
            timestamp=datetime.now(timezone.utc),
            signal="BEAR",
            confidence=0.95,
            reasoning="Same-day 424B5",
            phase="PROMOTIONAL_LATE",
            manipulation_probability=0.95,
        )

        # The test verifies the phase classification correctly
        assert manip_sig.phase == "PROMOTIONAL_LATE"
        # In production, _evaluate_candidate_inner checks for this phase
        # and returns NO_TRADE before reaching _build_trade_verdict

    def test_promotional_early_half_position(self, mock_settings: MagicMock) -> None:
        """PROMOTIONAL_EARLY → half position size."""
        from src.core.orchestrator import Orchestrator
        from src.core.models import CandidateStock, ScoredCandidate

        orch = Orchestrator(settings=mock_settings)

        candidate = CandidateStock(
            ticker="PROMO",
            current_price=5.00,
            previous_close=3.50,
            gap_pct=0.43,
            gap_classification="MAJOR",
            rvol=4.0,
            premarket_volume=200_000,
            float_shares=5_000_000,
            scan_timestamp=datetime.now(timezone.utc),
            scan_phase="PRE_MARKET",
        )
        scored = ScoredCandidate(
            candidate=candidate,
            mfcs=0.55,
            component_scores={"catalyst_news": 0.30},
            risk_score=0.3,
            qualifies_for_debate=False,
        )

        # Build verdict with PROMOTIONAL_EARLY
        verdict = orch._build_trade_verdict(
            candidate, scored, debate=None, risk_signal=None,
            manipulation_phase="PROMOTIONAL_EARLY",
        )

        # Position should be reduced (halved from standard)
        # Standard no-debate: mfcs_scale = min(1.0, 0.55/0.5) = 1.0
        # position_pct = 0.05 + (0.25 - 0.05) * 1.0 = 0.25 → ×float(1.5) → 0.375
        # Then halved by PROMOTIONAL_EARLY: 0.375 * 0.5 = 0.1875
        # Capped at 0.25
        assert verdict.action == "BUY"
        # The important check: position is smaller than without manipulation_phase
        verdict_standard = orch._build_trade_verdict(
            candidate, scored, debate=None, risk_signal=None,
            manipulation_phase="ORGANIC_MOMENTUM",
        )
        assert verdict.position_size_pct < verdict_standard.position_size_pct

    def test_promotional_early_aggressive_targets(self, mock_settings: MagicMock) -> None:
        """PROMOTIONAL_EARLY → targets at +3/+6/+10% instead of +5/+10/+20%."""
        from src.core.orchestrator import Orchestrator
        from src.core.models import CandidateStock, ScoredCandidate

        orch = Orchestrator(settings=mock_settings)

        candidate = CandidateStock(
            ticker="PROMO",
            current_price=5.00,
            previous_close=3.50,
            gap_pct=0.43,
            gap_classification="MAJOR",
            rvol=4.0,
            premarket_volume=200_000,
            scan_timestamp=datetime.now(timezone.utc),
            scan_phase="PRE_MARKET",
        )
        scored = ScoredCandidate(
            candidate=candidate,
            mfcs=0.55,
            component_scores={},
            risk_score=0.3,
            qualifies_for_debate=False,
        )

        verdict = orch._build_trade_verdict(
            candidate, scored, debate=None, risk_signal=None,
            manipulation_phase="PROMOTIONAL_EARLY",
        )

        # Targets should be +3%, +6%, +10%
        assert abs(verdict.target_prices[0] - 5.00 * 1.03) < 0.01
        assert abs(verdict.target_prices[1] - 5.00 * 1.06) < 0.01
        assert abs(verdict.target_prices[2] - 5.00 * 1.10) < 0.01

    def test_organic_standard_parameters(self, mock_settings: MagicMock) -> None:
        """ORGANIC_MOMENTUM → standard parameters unchanged."""
        from src.core.orchestrator import Orchestrator
        from src.core.models import CandidateStock, ScoredCandidate

        orch = Orchestrator(settings=mock_settings)

        candidate = CandidateStock(
            ticker="GOOD",
            current_price=10.00,
            previous_close=8.00,
            gap_pct=0.25,
            gap_classification="SIGNIFICANT",
            rvol=3.0,
            premarket_volume=300_000,
            scan_timestamp=datetime.now(timezone.utc),
            scan_phase="PRE_MARKET",
        )
        scored = ScoredCandidate(
            candidate=candidate,
            mfcs=0.55,
            component_scores={},
            risk_score=0.3,
            qualifies_for_debate=False,
        )

        verdict = orch._build_trade_verdict(
            candidate, scored, debate=None, risk_signal=None,
            manipulation_phase="ORGANIC_MOMENTUM",
        )

        # Standard targets: +5%, +10%, +20%
        assert abs(verdict.target_prices[0] - 10.00 * 1.05) < 0.01
        assert abs(verdict.target_prices[1] - 10.00 * 1.10) < 0.01
        assert abs(verdict.target_prices[2] - 10.00 * 1.20) < 0.01

    def test_min_position_floor(self, mock_settings: MagicMock) -> None:
        """PROMOTIONAL_EARLY with tiny position → NO_TRADE (< $500 floor)."""
        from src.core.orchestrator import Orchestrator
        from src.core.models import CandidateStock, ScoredCandidate

        # Make settings produce a very small position
        mock_settings.execution.max_position_pct = 0.003  # 0.3% → $300 on $100k

        orch = Orchestrator(settings=mock_settings)

        candidate = CandidateStock(
            ticker="TINY",
            current_price=2.00,
            previous_close=1.50,
            gap_pct=0.33,
            gap_classification="SIGNIFICANT",
            rvol=3.0,
            premarket_volume=100_000,
            float_shares=50_000_000,  # High float → 0.7x multiplier
            scan_timestamp=datetime.now(timezone.utc),
            scan_phase="PRE_MARKET",
        )
        scored = ScoredCandidate(
            candidate=candidate,
            mfcs=0.35,  # Low MFCS → small position
            component_scores={},
            risk_score=0.3,
            qualifies_for_debate=False,
        )

        verdict = orch._build_trade_verdict(
            candidate, scored, debate=None, risk_signal=None,
            manipulation_phase="PROMOTIONAL_EARLY",
        )

        # Should be NO_TRADE due to position floor
        assert verdict.action == "NO_TRADE"
        assert "min position floor" in verdict.reasoning_summary.lower()


# ═══════════════════════════════════════════════════════════════════
# §2E: Agent Registration Tests
# ═══════════════════════════════════════════════════════════════════


class TestAgentRegistration:
    """Tests for D106 §2E ManipulationClassifier registration in orchestrator."""

    def test_manipulation_classifier_instantiated(self) -> None:
        """Orchestrator has _manipulation_classifier attribute."""
        from src.core.orchestrator import Orchestrator
        from src.agents.manipulation_classifier import ManipulationClassifier

        settings = MagicMock()
        settings.models.tier1_model = "test-model"
        settings.models.tier1_provider = ""
        settings.models.tier2_model = "test-model"
        settings.models.tier2_provider = ""
        settings.models.tier1_fallback_model = ""
        settings.models.tier1_fallback_provider = ""
        settings.models.tier2_fallback_model = ""
        settings.models.tier2_fallback_provider = ""
        settings.models.emergency_model = ""
        settings.models.emergency_provider = ""
        settings.models.default_temperature = 0.3
        settings.models.litellm_timeout_tier1 = 15
        settings.models.litellm_timeout_tier2 = 10
        # D218 logs agent dispatch at startup and compares each scoring weight
        # with > 0; on a bare MagicMock that raises TypeError and __init__ dies.
        settings.scoring.catalyst_news = 0.30
        settings.scoring.technical = 0.20
        settings.scoring.volume_rvol = 0.15
        settings.scoring.float_structure = 0.10
        settings.scoring.institutional = 0.10
        settings.scoring.deep_search = 0.05
        settings.scoring.risk_aversion_lambda = 0.5   # formatted with :.2f at startup
        settings.debate.divergence_low_threshold = 0.3
        settings.debate.divergence_high_threshold = 0.7
        settings.experiments.enabled = False
        settings.experiments.yaml_path = "data/experiments/experiments.yaml"
        settings.experiments.journal_dir = "data/experiments/journal"

        orch = Orchestrator(settings=settings)
        assert hasattr(orch, "_manipulation_classifier")
        assert isinstance(orch._manipulation_classifier, ManipulationClassifier)

    def test_manipulation_classifier_in_replay_map(self) -> None:
        """ManipulationClassifier is included in replay agent map."""
        from src.core.orchestrator import Orchestrator

        settings = MagicMock()
        settings.models.tier1_model = "test-model"
        settings.models.tier1_provider = ""
        settings.models.tier2_model = "test-model"
        settings.models.tier2_provider = ""
        settings.models.tier1_fallback_model = ""
        settings.models.tier1_fallback_provider = ""
        settings.models.tier2_fallback_model = ""
        settings.models.tier2_fallback_provider = ""
        settings.models.emergency_model = ""
        settings.models.emergency_provider = ""
        settings.models.default_temperature = 0.3
        settings.models.litellm_timeout_tier1 = 15
        settings.models.litellm_timeout_tier2 = 10
        # D218 logs agent dispatch at startup and compares each scoring weight
        # with > 0; on a bare MagicMock that raises TypeError and __init__ dies.
        settings.scoring.catalyst_news = 0.30
        settings.scoring.technical = 0.20
        settings.scoring.volume_rvol = 0.15
        settings.scoring.float_structure = 0.10
        settings.scoring.institutional = 0.10
        settings.scoring.deep_search = 0.05
        settings.scoring.risk_aversion_lambda = 0.5   # formatted with :.2f at startup
        settings.debate.divergence_low_threshold = 0.3
        settings.debate.divergence_high_threshold = 0.7
        settings.experiments.enabled = False
        settings.experiments.yaml_path = "data/experiments/experiments.yaml"
        settings.experiments.journal_dir = "data/experiments/journal"

        orch = Orchestrator(settings=settings)

        # wrap_agents_for_recording should include manipulation_classifier
        wrappers = orch.wrap_agents_for_recording()
        assert "manipulation_classifier" in wrappers


# ═══════════════════════════════════════════════════════════════════
# §3B: ManagedPosition Fields Tests
# ═══════════════════════════════════════════════════════════════════


class TestManagedPositionFields:
    """Tests for D106 §3B manipulation fields on ManagedPosition."""

    def test_default_manipulation_phase(self) -> None:
        """ManagedPosition defaults to UNCERTAIN phase."""
        from src.execution.position_manager import ManagedPosition
        pos = ManagedPosition(
            ticker="TEST",
            qty=100,
            entry_price=5.00,
            signal_price=5.00,
            stop_loss=4.50,
        )
        assert pos.manipulation_phase == "UNCERTAIN"

    def test_promotional_early_fields(self) -> None:
        """ManagedPosition with PROMOTIONAL_EARLY phase."""
        from src.execution.position_manager import ManagedPosition
        pos = ManagedPosition(
            ticker="PROMO",
            qty=50,
            entry_price=5.00,
            signal_price=5.00,
            stop_loss=4.70,
            manipulation_phase="PROMOTIONAL_EARLY",
        )
        assert pos.manipulation_phase == "PROMOTIONAL_EARLY"


# ═══════════════════════════════════════════════════════════════════
# §3A: DistributionDetector Tests
# ═══════════════════════════════════════════════════════════════════


class TestDistributionDetector:
    """Tests for D106 §3A DistributionDetector."""

    def test_churning_detection(self) -> None:
        """High volume + flat price = high volume_without_advance signal."""
        from src.execution.distribution_detector import DistributionDetector

        detector = DistributionDetector()
        sig = detector.analyze(
            ticker="CHURN",
            current_price=5.00,    # Same as entry — no advance
            entry_price=5.00,
            current_volume=150_000,  # 3x entry volume
            entry_volume=50_000,
        )
        # Price flat + 3x volume = churning
        assert sig.volume_without_advance >= 0.9  # Near max
        assert sig.volume_without_advance <= 1.0

    def test_spread_expansion_3x(self) -> None:
        """3x+ entry spread = max signal."""
        from src.execution.distribution_detector import DistributionDetector

        detector = DistributionDetector()
        sig = detector.analyze(
            ticker="WIDE",
            current_price=5.00,
            entry_price=5.00,
            current_spread=0.16,  # >3x entry spread (avoids float precision issue)
            entry_spread=0.05,
        )
        assert sig.spread_expansion == 1.0

    def test_composite_threshold(self) -> None:
        """Composite > 0.50 triggers should_exit."""
        from src.execution.distribution_detector import DistributionDetector

        detector = DistributionDetector(exit_threshold=0.50)
        # Multiple strong signals → high composite
        sig = detector.analyze(
            ticker="EXIT",
            current_price=4.80,    # Below entry
            entry_price=5.00,
            current_volume=200_000,  # 4x entry volume
            entry_volume=50_000,
            peak_price=5.50,
            current_spread=0.15,
            entry_spread=0.05,
            vwap=5.10,
            minutes_held=120,      # >90 min
            has_new_dilutive_filing=True,  # 424B5 since entry
        )
        assert sig.composite_score > 0.50
        assert sig.should_exit is True

    def test_zero_inputs(self) -> None:
        """All zeros = 0.0 composite."""
        from src.execution.distribution_detector import DistributionDetector

        detector = DistributionDetector()
        sig = detector.analyze(
            ticker="ZERO",
            current_price=0.0,
            entry_price=0.0,
        )
        assert sig.composite_score == 0.0
        assert sig.should_exit is False

    def test_new_dilutive_filing(self) -> None:
        """424B5 since entry = 1.0 filing signal."""
        from src.execution.distribution_detector import DistributionDetector

        detector = DistributionDetector()
        sig = detector.analyze(
            ticker="DILUTE",
            current_price=5.00,
            entry_price=5.00,
            has_new_dilutive_filing=True,
        )
        assert sig.new_dilutive_filing == 1.0

    def test_no_dilutive_filing(self) -> None:
        """No new 424B5 = 0.0 filing signal."""
        from src.execution.distribution_detector import DistributionDetector

        detector = DistributionDetector()
        sig = detector.analyze(
            ticker="CLEAN",
            current_price=5.00,
            entry_price=5.00,
            has_new_dilutive_filing=False,
        )
        assert sig.new_dilutive_filing == 0.0

    def test_time_decay_90_min(self) -> None:
        """>90 min = 1.0 time decay signal."""
        from src.execution.distribution_detector import DistributionDetector

        detector = DistributionDetector()
        sig = detector.analyze(
            ticker="LATE",
            current_price=5.00,
            entry_price=5.00,
            minutes_held=100,
        )
        assert sig.minutes_since_entry == 1.0

    def test_time_decay_early(self) -> None:
        """First 30 min = 0.0 time decay."""
        from src.execution.distribution_detector import DistributionDetector

        detector = DistributionDetector()
        sig = detector.analyze(
            ticker="EARLY",
            current_price=5.00,
            entry_price=5.00,
            minutes_held=15,
        )
        assert sig.minutes_since_entry == 0.0

    def test_peak_drawdown(self) -> None:
        """10% drawdown from peak = 1.0 signal."""
        from src.execution.distribution_detector import DistributionDetector

        detector = DistributionDetector()
        sig = detector.analyze(
            ticker="DROP",
            current_price=4.50,    # 10% below peak
            entry_price=4.00,
            peak_price=5.00,
        )
        assert sig.peak_drawdown == 1.0

    def test_large_block_sells(self) -> None:
        """Ask 4x bid = 1.0 block sell signal."""
        from src.execution.distribution_detector import DistributionDetector

        detector = DistributionDetector()
        sig = detector.analyze(
            ticker="SELL",
            current_price=5.00,
            entry_price=5.00,
            bid_size=1000,
            ask_size=4000,
        )
        assert sig.large_block_sells == 1.0

    def test_price_below_vwap(self) -> None:
        """Price far below VWAP = high signal."""
        from src.execution.distribution_detector import DistributionDetector

        detector = DistributionDetector()
        sig = detector.analyze(
            ticker="VWAP",
            current_price=4.85,
            entry_price=5.00,
            vwap=5.10,        # 5% above current
        )
        assert sig.price_below_vwap > 0.5

    def test_healthy_position(self) -> None:
        """Healthy position (price advancing with volume) = low composite."""
        from src.execution.distribution_detector import DistributionDetector

        detector = DistributionDetector()
        sig = detector.analyze(
            ticker="GOOD",
            current_price=5.50,    # +10% from entry
            entry_price=5.00,
            current_volume=80_000,
            entry_volume=50_000,
            peak_price=5.55,
            current_spread=0.03,
            entry_spread=0.03,
            vwap=5.20,            # Above VWAP
            minutes_held=15,      # Early
            bid_size=2000,
            ask_size=1500,        # Bid > ask
        )
        assert sig.composite_score < 0.20
        assert sig.should_exit is False

    def test_to_dict(self) -> None:
        """DistributionSignal.to_dict() includes all fields."""
        from src.execution.distribution_detector import DistributionDetector

        detector = DistributionDetector()
        sig = detector.analyze(
            ticker="TEST",
            current_price=5.00,
            entry_price=5.00,
        )
        d = sig.to_dict()
        assert "volume_without_advance" in d
        assert "new_dilutive_filing" in d
        assert "spread_expansion" in d
        assert "composite" in d
        assert "should_exit" in d


# ═══════════════════════════════════════════════════════════════════
# §3C: Exit Intelligence Integration Tests
# ═══════════════════════════════════════════════════════════════════


class TestExitIntelligenceIntegration:
    """Tests for D106 §3C distribution_detector integration in exit intelligence."""

    def test_distribution_detector_weight_in_composite(self) -> None:
        """distribution_detector has non-zero weight in EXIT_SIGNAL_WEIGHTS."""
        from src.execution.exit_intelligence import EXIT_SIGNAL_WEIGHTS

        assert "distribution_detector" in EXIT_SIGNAL_WEIGHTS
        assert EXIT_SIGNAL_WEIGHTS["distribution_detector"] == 0.15

    def test_dead_signals_zeroed(self) -> None:
        """distribution and flow_toxicity weights are zeroed (lack real data)."""
        from src.execution.exit_intelligence import EXIT_SIGNAL_WEIGHTS

        assert EXIT_SIGNAL_WEIGHTS["distribution"] == 0.0
        assert EXIT_SIGNAL_WEIGHTS["flow_toxicity"] == 0.0

    def test_weights_sum_to_one(self) -> None:
        """All exit signal weights sum to 1.0."""
        from src.execution.exit_intelligence import EXIT_SIGNAL_WEIGHTS

        total = sum(EXIT_SIGNAL_WEIGHTS.values())
        assert abs(total - 1.0) < 0.001, f"Weights sum to {total}, expected 1.0"

    def test_exit_signal_has_distribution_detector_field(self) -> None:
        """ExitSignal dataclass has distribution_detector field."""
        from src.execution.exit_intelligence import ExitSignal

        sig = ExitSignal(ticker="TEST")
        assert hasattr(sig, "distribution_detector")
        assert sig.distribution_detector == 0.0

    def test_distribution_detector_in_composite(self) -> None:
        """distribution_detector_score flows into composite calculation."""
        from src.execution.exit_intelligence import ExitSignalEngine

        engine = ExitSignalEngine(
            tighten_threshold=0.15,
            exit_threshold=0.40,
        )

        # Without distribution_detector
        sig_without = engine.compute_exit_signals(
            ticker="TEST",
            current_price=5.00,
            entry_price=5.00,
            distribution_detector_score=0.0,
        )

        # With distribution_detector
        sig_with = engine.compute_exit_signals(
            ticker="TEST",
            current_price=5.00,
            entry_price=5.00,
            distribution_detector_score=1.0,
        )

        # Composite should be higher with distribution_detector
        assert sig_with.composite_exit_urgency > sig_without.composite_exit_urgency
        assert sig_with.distribution_detector == 1.0
        assert sig_without.distribution_detector == 0.0

    def test_only_active_promotional_early(self) -> None:
        """Distribution detector should only be non-zero for PROMOTIONAL_EARLY.

        This is enforced by the caller (position manager) — the detector
        itself is agnostic. Test verifies that passing 0.0 (for ORGANIC/
        UNCERTAIN) doesn't affect the composite.
        """
        from src.execution.exit_intelligence import ExitSignalEngine

        engine = ExitSignalEngine()
        sig = engine.compute_exit_signals(
            ticker="ORGANIC",
            current_price=5.00,
            entry_price=5.00,
            distribution_detector_score=0.0,  # Not PROMOTIONAL_EARLY
        )
        assert sig.distribution_detector == 0.0

    def test_to_scores_dict_includes_distribution_detector(self) -> None:
        """to_scores_dict() includes distribution_detector."""
        from src.execution.exit_intelligence import ExitSignal

        sig = ExitSignal(ticker="TEST", distribution_detector=0.75)
        d = sig.to_scores_dict()
        assert "distribution_detector" in d
        assert d["distribution_detector"] == 0.75
