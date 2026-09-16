"""
Tests for experiment replay — variant results, journal I/O, integration.

D102: Experimentation Framework Phase 1.
"""

from __future__ import annotations

import json
import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from config.settings import Settings
from src.core.models import (
    AgentSignal,
    CandidateStock,
    RiskSignal,
    ScoredCandidate,
    TradeVerdict,
)
from src.core.scoring import compute_mfcs
from src.experiments.models import (
    ExperimentConfig,
    ExperimentJournalEntry,
    ExperimentVariant,
    VariantResult,
)
from src.experiments.registry import ExperimentRegistry
from src.experiments.journal import ExperimentJournal


# ─── Helpers ─────────────────────────────────────────────────────────


def _make_candidate(
    ticker: str = "TEST",
    price: float = 10.0,
    previous_close: float = 8.0,
    gap_pct: float = 0.25,
    rvol: float = 5.0,
) -> CandidateStock:
    return CandidateStock(
        ticker=ticker,
        current_price=price,
        previous_close=previous_close,
        gap_pct=gap_pct,
        gap_classification="EXPLOSIVE",
        rvol=rvol,
        premarket_volume=500_000,
        scan_timestamp=datetime.now(timezone.utc),
        scan_phase="PRE_MARKET",
    )


def _make_signal(
    agent_id: str,
    ticker: str = "TEST",
    signal: str = "BULL",
    confidence: float = 0.8,
    reasoning: str = "test signal",
) -> AgentSignal:
    return AgentSignal(
        agent_id=agent_id,
        ticker=ticker,
        timestamp=datetime.now(timezone.utc),
        signal=signal,
        confidence=confidence,
        reasoning=reasoning,
        key_data={},
        flags=[],
        sources_used=[],
        prompt_variant_id="v0_control",
        model_id="test-model",
        latency_ms=100.0,
    )


def _make_risk_signal(
    ticker: str = "TEST",
    risk_score: float = 0.3,
) -> RiskSignal:
    return RiskSignal(
        agent_id="risk_agent",
        ticker=ticker,
        timestamp=datetime.now(timezone.utc),
        signal="NEUTRAL",
        confidence=1.0,
        reasoning="test risk",
        key_data={},
        flags=[],
        sources_used=[],
        prompt_variant_id="v0_control",
        model_id="deterministic",
        latency_ms=0.1,
        risk_verdict="APPROVE",
        risk_score=risk_score,
        risk_breakdown={},
        veto_reason=None,
        position_size_recommendation="FULL",
    )


# ─── Scoring Replay Tests ───────────────────────────────────────────


class TestScoringReplay:
    """Verify that parameter replay produces correct variant-specific results."""

    @pytest.fixture
    def candidate(self) -> CandidateStock:
        return _make_candidate()

    @pytest.fixture
    def signals(self) -> list[AgentSignal]:
        return [
            _make_signal("news_agent", signal="STRONG_BULL", confidence=0.8),
            _make_signal("technical_agent", signal="BULL", confidence=0.7),
            _make_signal("fundamental_agent", signal="NEUTRAL", confidence=0.3, reasoning=""),
            _make_risk_signal(risk_score=0.4),
        ]

    def test_threshold_sweep_different_pass_fail(self, candidate, signals):
        """Different MFCS thresholds produce different entry decisions."""
        settings = Settings()

        # Score once — get the primary MFCS
        primary = compute_mfcs(
            candidate=candidate,
            signals=signals,
            weights={
                "catalyst_news": settings.scoring.catalyst_news,
                "technical": settings.scoring.technical,
                "volume_rvol": settings.scoring.volume_rvol,
                "float_structure": settings.scoring.float_structure,
                "institutional": settings.scoring.institutional,
                "deep_search": settings.scoring.deep_search,
            },
            risk_aversion_lambda=settings.scoring.risk_aversion_lambda,
        )

        # Test with a very low threshold (should pass)
        assert primary.mfcs >= 0.10, f"MFCS {primary.mfcs} too low for test"

        # Test with a very high threshold (should fail)
        assert primary.mfcs < 0.90, f"MFCS {primary.mfcs} too high for test"

    def test_weight_sweep_different_mfcs(self, candidate, signals):
        """Different agent weights produce different MFCS values."""
        weights_a = {
            "catalyst_news": 0.45,
            "technical": 0.25,
            "volume_rvol": 0.15,
            "float_structure": 0.15,
        }
        weights_b = {
            "catalyst_news": 0.20,
            "technical": 0.45,
            "volume_rvol": 0.20,
            "float_structure": 0.15,
        }

        scored_a = compute_mfcs(candidate=candidate, signals=signals, weights=weights_a)
        scored_b = compute_mfcs(candidate=candidate, signals=signals, weights=weights_b)

        # News is STRONG_BULL(0.8), tech is BULL(0.7) — news_heavy should score higher
        assert scored_a.mfcs != scored_b.mfcs

    def test_lambda_sweep_different_risk_penalty(self, candidate, signals):
        """Different lambda values produce different MFCS via risk penalty."""
        weights = {
            "catalyst_news": 0.35,
            "technical": 0.30,
            "volume_rvol": 0.20,
            "float_structure": 0.15,
        }

        scored_low = compute_mfcs(
            candidate=candidate,
            signals=signals,
            weights=weights,
            risk_aversion_lambda=0.05,
        )
        scored_high = compute_mfcs(
            candidate=candidate,
            signals=signals,
            weights=weights,
            risk_aversion_lambda=0.30,
        )

        # Higher lambda means more risk penalty → lower MFCS
        assert scored_low.mfcs > scored_high.mfcs

    def test_atr_sweep_different_stop_distances(self):
        """Different ATR multipliers produce different stop levels."""
        entry_price = 10.0
        atr = 0.50  # $0.50 ATR

        stops = {}
        for mult in [1.5, 2.0, 2.5, 3.0]:
            stop_distance = atr * mult
            stop = entry_price - stop_distance
            stops[mult] = stop

        # Higher multiplier → wider stop (lower price)
        assert stops[1.5] > stops[2.0] > stops[2.5] > stops[3.0]

    def test_atr_sweep_different_position_sizes(self):
        """Different ATR stops → different fixed-risk position sizes."""
        entry_price = 10.0
        atr = 0.50
        equity = 100_000
        risk_pct = 0.01

        sizes = {}
        for mult in [1.5, 2.0, 2.5, 3.0]:
            stop_distance = atr * mult
            qty = int((equity * risk_pct) / stop_distance)
            sizes[mult] = qty

        # Tighter stop (lower mult) → more shares (higher risk per share, same total $)
        assert sizes[1.5] > sizes[2.0] > sizes[2.5] > sizes[3.0]


# ─── apply_overrides Integration ────────────────────────────────────


class TestApplyOverridesIntegration:
    """Test that overrides produce correct scoring changes end-to-end."""

    def test_threshold_override_changes_entry_decision(self):
        """Changing mfcs_buy_threshold changes would-enter for borderline candidates."""
        settings = Settings()
        candidate = _make_candidate()
        signals = [
            _make_signal("news_agent", signal="BULL", confidence=0.6),
            _make_signal("technical_agent", signal="BULL", confidence=0.5),
            _make_risk_signal(risk_score=0.3),
        ]

        weights = {
            "catalyst_news": settings.scoring.catalyst_news,
            "technical": settings.scoring.technical,
            "volume_rvol": settings.scoring.volume_rvol,
            "float_structure": settings.scoring.float_structure,
            "institutional": settings.scoring.institutional,
            "deep_search": settings.scoring.deep_search,
        }
        scored = compute_mfcs(
            candidate=candidate,
            signals=signals,
            weights=weights,
            risk_aversion_lambda=settings.scoring.risk_aversion_lambda,
        )

        # Apply a very low threshold → should enter
        low_settings = ExperimentRegistry.apply_overrides(
            settings, {"scoring.mfcs_buy_threshold": 0.01}
        )
        assert scored.mfcs >= low_settings.scoring.mfcs_buy_threshold

        # Apply a very high threshold → should skip
        high_settings = ExperimentRegistry.apply_overrides(
            settings, {"scoring.mfcs_buy_threshold": 0.99}
        )
        assert scored.mfcs < high_settings.scoring.mfcs_buy_threshold


# ─── Journal I/O Tests ──────────────────────────────────────────────


class TestExperimentJournal:
    @pytest.fixture
    def journal(self, tmp_path: Path) -> ExperimentJournal:
        return ExperimentJournal(journal_dir=tmp_path, session_date="2026-03-12")

    @pytest.fixture
    def sample_entry(self) -> ExperimentJournalEntry:
        return ExperimentJournalEntry(
            trade_id="TEST_20260312T093000Z",
            ticker="TEST",
            timestamp="2026-03-12T09:30:00Z",
            primary_mfcs=0.45,
            primary_action="BUY",
            primary_stop_loss=9.50,
            variant_results=[
                VariantResult(
                    experiment_id="atr_sweep",
                    variant_id="atr_1.5",
                    overrides={"execution.initial_stop_atr_multiplier": 1.5},
                    mfcs=0.45,
                    would_enter=True,
                    stop_loss=9.25,
                    position_size_qty=133,
                    position_size_pct=0.133,
                    reasoning="MFCS=0.450 vs threshold=0.25",
                ),
                VariantResult(
                    experiment_id="threshold_sweep",
                    variant_id="thresh_0.35",
                    overrides={"scoring.mfcs_buy_threshold": 0.35},
                    mfcs=0.45,
                    would_enter=True,
                    stop_loss=None,
                    position_size_qty=None,
                    position_size_pct=None,
                    reasoning="MFCS=0.450 vs threshold=0.35",
                ),
                VariantResult(
                    experiment_id="threshold_sweep",
                    variant_id="thresh_0.50",
                    overrides={"scoring.mfcs_buy_threshold": 0.50},
                    mfcs=0.45,
                    would_enter=False,
                    stop_loss=None,
                    position_size_qty=None,
                    position_size_pct=None,
                    reasoning="MFCS=0.450 vs threshold=0.50",
                ),
            ],
        )

    def test_record_and_load(self, journal: ExperimentJournal, sample_entry):
        """Record an entry, then load it back — roundtrip fidelity."""
        journal.record(sample_entry)
        assert journal.entries_recorded == 1

        entries = ExperimentJournal.load(journal.journal_path)
        assert len(entries) == 1

        loaded = entries[0]
        assert loaded.trade_id == "TEST_20260312T093000Z"
        assert loaded.ticker == "TEST"
        assert loaded.primary_mfcs == 0.45
        assert loaded.primary_action == "BUY"
        assert len(loaded.variant_results) == 3

    def test_multiple_records(self, journal: ExperimentJournal, sample_entry):
        """Multiple records append to the same file."""
        journal.record(sample_entry)
        journal.record(sample_entry)
        journal.record(sample_entry)

        assert journal.entries_recorded == 3
        entries = ExperimentJournal.load(journal.journal_path)
        assert len(entries) == 3

    def test_variant_result_fields(self, journal: ExperimentJournal, sample_entry):
        """Variant result fields survive serialization roundtrip."""
        journal.record(sample_entry)
        entries = ExperimentJournal.load(journal.journal_path)
        vr = entries[0].variant_results[0]

        assert vr.experiment_id == "atr_sweep"
        assert vr.variant_id == "atr_1.5"
        assert vr.overrides == {"execution.initial_stop_atr_multiplier": 1.5}
        assert vr.mfcs == 0.45
        assert vr.would_enter is True
        assert vr.stop_loss == 9.25
        assert vr.position_size_qty == 133

    def test_load_nonexistent_file(self):
        """Loading a nonexistent file returns empty list."""
        entries = ExperimentJournal.load(Path("/nonexistent/path.jsonl"))
        assert entries == []

    def test_load_malformed_lines_skipped(self, tmp_path: Path):
        """Malformed JSONL lines are skipped, valid lines still load."""
        f = tmp_path / "malformed.jsonl"
        good_entry = ExperimentJournalEntry(
            trade_id="GOOD",
            ticker="GOOD",
            timestamp="2026-03-12T09:30:00Z",
            primary_mfcs=0.5,
            primary_action="BUY",
            variant_results=[],
        )
        f.write_text(
            good_entry.model_dump_json() + "\n"
            + "this is not valid json\n"
            + good_entry.model_dump_json() + "\n"
        )

        entries = ExperimentJournal.load(f)
        assert len(entries) == 2  # 2 good, 1 bad skipped

    def test_journal_path_includes_date(self, journal: ExperimentJournal):
        """Journal filename includes the session date."""
        assert "2026-03-12" in journal.journal_path.name

    def test_journal_dir_created_on_write(self, tmp_path: Path, sample_entry):
        """Journal directory is auto-created if it doesn't exist."""
        nested_dir = tmp_path / "deep" / "nested" / "path"
        journal = ExperimentJournal(journal_dir=nested_dir)
        journal.record(sample_entry)
        assert nested_dir.exists()
        assert journal.entries_recorded == 1

    def test_record_never_raises(self, sample_entry, tmp_path: Path):
        """Journal record catches all exceptions internally."""
        # Create a file where the journal dir should be, so mkdir fails
        blocker = tmp_path / "blocker_file"
        blocker.write_text("I am a file, not a directory")
        bad_dir = blocker / "subdir"  # Can't mkdir inside a file
        journal = ExperimentJournal(journal_dir=bad_dir)
        # Should NOT raise — error is logged and swallowed
        journal.record(sample_entry)
        # entries_recorded should remain 0 since write failed
        assert journal.entries_recorded == 0


# ─── Journal Summarize Tests ────────────────────────────────────────


class TestJournalSummarize:
    def test_summarize_empty(self, tmp_path: Path):
        """Summarize on empty journal returns zero counts."""
        journal = ExperimentJournal(journal_dir=tmp_path)
        summary = journal.summarize()
        assert summary["total_evaluations"] == 0

    def test_summarize_with_data(self, tmp_path: Path):
        """Summarize aggregates would_enter/would_skip correctly."""
        journal = ExperimentJournal(journal_dir=tmp_path, session_date="2026-03-12")

        for i in range(3):
            entry = ExperimentJournalEntry(
                trade_id=f"TRADE_{i}",
                ticker=f"T{i}",
                timestamp="2026-03-12T09:30:00Z",
                primary_mfcs=0.5,
                primary_action="BUY",
                variant_results=[
                    VariantResult(
                        experiment_id="atr_sweep",
                        variant_id="atr_1.5",
                        overrides={},
                        mfcs=0.5,
                        would_enter=True,
                        reasoning="",
                    ),
                    VariantResult(
                        experiment_id="atr_sweep",
                        variant_id="atr_3.0",
                        overrides={},
                        mfcs=0.5,
                        would_enter=i < 2,  # 2 enter, 1 skip
                        reasoning="",
                    ),
                ],
            )
            journal.record(entry)

        summary = journal.summarize()
        assert summary["total_evaluations"] == 3
        assert summary["total_variant_results"] == 6

        atr = summary["experiments"]["atr_sweep"]
        assert atr["atr_1.5"]["would_enter"] == 3
        assert atr["atr_1.5"]["would_skip"] == 0
        assert atr["atr_3.0"]["would_enter"] == 2
        assert atr["atr_3.0"]["would_skip"] == 1


# ─── VariantResult Model Tests ──────────────────────────────────────


class TestVariantResultModel:
    def test_frozen_immutability(self):
        """VariantResult is frozen (immutable)."""
        vr = VariantResult(
            experiment_id="test",
            variant_id="v1",
            overrides={},
            mfcs=0.5,
            would_enter=True,
            reasoning="",
        )
        with pytest.raises(Exception):
            vr.mfcs = 0.9  # type: ignore

    def test_optional_fields_default_none(self):
        """Optional fields default to None."""
        vr = VariantResult(
            experiment_id="test",
            variant_id="v1",
            overrides={},
            mfcs=0.5,
            would_enter=False,
            reasoning="",
        )
        assert vr.stop_loss is None
        assert vr.position_size_qty is None
        assert vr.position_size_pct is None

    def test_serialization_roundtrip(self):
        """VariantResult survives JSON serialization roundtrip."""
        vr = VariantResult(
            experiment_id="atr_sweep",
            variant_id="atr_2.5",
            overrides={"execution.initial_stop_atr_multiplier": 2.5},
            mfcs=0.423,
            would_enter=True,
            stop_loss=9.25,
            position_size_qty=150,
            position_size_pct=0.15,
            reasoning="MFCS=0.423 vs threshold=0.25",
        )
        json_str = vr.model_dump_json()
        loaded = VariantResult.model_validate_json(json_str)
        assert loaded == vr


# ─── ExperimentJournalEntry Model Tests ─────────────────────────────


class TestExperimentJournalEntryModel:
    def test_empty_variant_results(self):
        """Entry with no variant results is valid."""
        entry = ExperimentJournalEntry(
            trade_id="TEST",
            ticker="TEST",
            timestamp="2026-03-12T09:30:00Z",
            primary_mfcs=0.5,
            primary_action="HOLD",
            variant_results=[],
        )
        assert len(entry.variant_results) == 0

    def test_primary_stop_loss_optional(self):
        """primary_stop_loss defaults to None for non-BUY verdicts."""
        entry = ExperimentJournalEntry(
            trade_id="TEST",
            ticker="TEST",
            timestamp="2026-03-12T09:30:00Z",
            primary_mfcs=0.1,
            primary_action="NO_TRADE",
            variant_results=[],
        )
        assert entry.primary_stop_loss is None
