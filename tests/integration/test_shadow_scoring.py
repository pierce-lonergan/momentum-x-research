"""Integration tests for D220 Phase 5 shadow telemetry.

Verifies the four constraints from the Phase 5 spec:
  1. Composite shadow runs at the end of evaluation (BUY + NO_TRADE paths)
  2. Inverted shadow's paranoid schema rejects lookahead violations
  3. Both kill switches work via env vars (independent)
  4. Shadow failures NEVER raise into the production hot path
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from src.shadow.composite_shadow import (
    is_composite_shadow_enabled,
    maybe_score_composite,
)
from src.shadow.inverted_shadow import (
    INVERTED_ENTRY_BASIS,
    InvertedShadowEntry,
    InvertedShadowOutcome,
    is_inverted_shadow_enabled,
    log_inverted_shadow_safe,
)
from src.shadow.logger import ShadowLogger, get_shadow_logger


_NY = ZoneInfo("America/New_York")


def _utc(et_dt: datetime) -> str:
    """Convert ET-local datetime to ISO UTC."""
    return et_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _et(year: int, month: int, day: int, h: int, m: int, s: int = 0) -> datetime:
    return datetime(year, month, day, h, m, s, tzinfo=_NY)


# ── Composite-shadow kill switch ─────────────────────────────────────────


class TestCompositeShadowKillSwitch:

    def test_default_enabled(self, monkeypatch):
        monkeypatch.delenv("SHADOW_SCORING_ENABLED", raising=False)
        assert is_composite_shadow_enabled() is True

    def test_explicit_false_disables(self, monkeypatch):
        monkeypatch.setenv("SHADOW_SCORING_ENABLED", "false")
        assert is_composite_shadow_enabled() is False

    def test_zero_disables(self, monkeypatch):
        monkeypatch.setenv("SHADOW_SCORING_ENABLED", "0")
        assert is_composite_shadow_enabled() is False

    def test_truthy_string_enables(self, monkeypatch):
        monkeypatch.setenv("SHADOW_SCORING_ENABLED", "yes")
        assert is_composite_shadow_enabled() is True

    def test_disabled_returns_without_logging(self, monkeypatch):
        """When kill switch is off, maybe_score_composite is a no-op."""
        monkeypatch.setenv("SHADOW_SCORING_ENABLED", "false")
        # Should NOT raise even with bogus inputs — short-circuit before scoring
        maybe_score_composite(
            candidate_features={"gap_pct": "INVALID-NOT-A-NUMBER"},
            ticker="X",
            session_date="2026-04-17",
            production_decision="BUY",
        )


# ── Composite-shadow safe-failure ────────────────────────────────────────


class TestCompositeShadowSafeFailure:

    def test_failure_does_not_raise(self, monkeypatch, caplog):
        """Even if the composite model is missing or the call throws,
        maybe_score_composite must NEVER raise."""
        monkeypatch.setenv("SHADOW_SCORING_ENABLED", "true")
        # Patch composite_score_both to raise
        with patch(
            "src.composite.score.composite_score_both",
            side_effect=RuntimeError("simulated failure"),
        ):
            # This call must return None without raising
            maybe_score_composite(
                candidate_features={"gap_pct": 0.10, "price": 5.0},
                ticker="FAIL",
                session_date="2026-04-17",
                production_decision="BUY",
            )
        # And it should have logged a warning
        assert any(
            "composite_shadow" in r.message and "FAIL" in r.message
            for r in caplog.records
        )


# ── Inverted-shadow kill switch ──────────────────────────────────────────


class TestInvertedShadowKillSwitch:

    def test_default_enabled(self, monkeypatch):
        monkeypatch.delenv("SHADOW_INVERTED_ENABLED", raising=False)
        assert is_inverted_shadow_enabled() is True

    def test_independent_of_composite_switch(self, monkeypatch):
        """The two kill switches are INDEPENDENT — flipping one does not
        affect the other."""
        monkeypatch.setenv("SHADOW_SCORING_ENABLED", "false")
        monkeypatch.setenv("SHADOW_INVERTED_ENABLED", "true")
        assert is_composite_shadow_enabled() is False
        assert is_inverted_shadow_enabled() is True

        monkeypatch.setenv("SHADOW_SCORING_ENABLED", "true")
        monkeypatch.setenv("SHADOW_INVERTED_ENABLED", "false")
        assert is_composite_shadow_enabled() is True
        assert is_inverted_shadow_enabled() is False

    def test_disabled_skips_logging(self, monkeypatch, tmp_path):
        monkeypatch.setenv("SHADOW_INVERTED_ENABLED", "false")
        result = log_inverted_shadow_safe({
            "ticker": "X",
            "session_date": "2026-04-17",
            "production_decision": "NO_TRADE",
            "production_gate_rejected": "test",
            "production_mfcs": 0.3,
            "decision_timestamp": _utc(_et(2026, 4, 17, 9, 36, 30)),
            "orb_high_5min": 5.50,
            "orb_break_timestamp": _utc(_et(2026, 4, 17, 9, 35, 30)),
            "orb_broken_by_decision_time": True,
            "simulated_entry_price": 5.65,
            "simulated_entry_basis": "vwap_9_36_to_9_37",
            "would_have_inverted_bought": True,
        })
        assert result is False  # signaled disabled


# ── Inverted-shadow paranoid schema ──────────────────────────────────────


class TestInvertedShadowSchema:

    def _valid_entry_kwargs(self) -> dict:
        return {
            "ticker": "TEST",
            "session_date": "2026-04-17",
            "production_decision": "NO_TRADE",
            "production_gate_rejected": "adaptive_router.py:149",
            "production_mfcs": 0.45,
            "decision_timestamp": _utc(_et(2026, 4, 17, 9, 36, 30)),
            "orb_high_5min": 5.50,
            "orb_break_timestamp": _utc(_et(2026, 4, 17, 9, 35, 15)),
            "orb_broken_by_decision_time": True,
            "simulated_entry_price": 5.65,
            "simulated_entry_basis": "vwap_9_36_to_9_37",
            "would_have_inverted_bought": True,
        }

    def test_valid_entry_constructs(self):
        entry = InvertedShadowEntry(**self._valid_entry_kwargs())
        assert entry.ticker == "TEST"
        assert entry.would_have_inverted_bought is True
        assert entry.outcome.filled is False  # default

    def test_decision_before_9_36_rejected(self):
        """Invariant 1: decision_timestamp ET MUST be >= 09:36:00."""
        kw = self._valid_entry_kwargs()
        # 09:35:30 ET — half a minute too early
        kw["decision_timestamp"] = _utc(_et(2026, 4, 17, 9, 35, 30))
        with pytest.raises(ValueError, match="before 09:36"):
            InvertedShadowEntry(**kw)

    def test_orb_break_after_decision_rejected(self):
        """Invariant 2: orb_break_timestamp MUST be < decision_timestamp."""
        kw = self._valid_entry_kwargs()
        # ORB break at 9:37 (1 min AFTER decision) — lookahead!
        kw["orb_break_timestamp"] = _utc(_et(2026, 4, 17, 9, 37, 0))
        with pytest.raises(ValueError, match="STRICTLY before decision"):
            InvertedShadowEntry(**kw)

    def test_orb_break_equal_to_decision_rejected(self):
        """STRICT inequality: orb_break_timestamp = decision_timestamp is rejected."""
        kw = self._valid_entry_kwargs()
        kw["orb_break_timestamp"] = kw["decision_timestamp"]
        with pytest.raises(ValueError, match="STRICTLY before decision"):
            InvertedShadowEntry(**kw)

    def test_unknown_entry_basis_rejected(self):
        """Invariant 3: simulated_entry_basis must be in fixed enum."""
        kw = self._valid_entry_kwargs()
        kw["simulated_entry_basis"] = "free_form_string_attack"
        with pytest.raises(ValueError, match="not in allowed enum"):
            InvertedShadowEntry(**kw)

    def test_buy_without_orb_break_rejected(self):
        """Invariant 4: would_have_inverted_bought=True requires
        orb_broken_by_decision_time=True."""
        kw = self._valid_entry_kwargs()
        kw["orb_broken_by_decision_time"] = False
        with pytest.raises(ValueError, match="orb_broken_by_decision_time=True"):
            InvertedShadowEntry(**kw)

    def test_orb_break_none_allowed(self):
        """orb_break_timestamp may be None when ORB hasn't broken — but then
        would_have_inverted_bought must be False (Invariant 4)."""
        kw = self._valid_entry_kwargs()
        kw["orb_break_timestamp"] = None
        kw["orb_broken_by_decision_time"] = False
        kw["would_have_inverted_bought"] = False
        entry = InvertedShadowEntry(**kw)
        assert entry.orb_break_timestamp is None

    def test_log_inverted_shadow_safe_drops_invalid(self, monkeypatch, caplog):
        """Schema violations should be logged + dropped, NEVER raised."""
        monkeypatch.setenv("SHADOW_INVERTED_ENABLED", "true")
        kw = self._valid_entry_kwargs()
        kw["decision_timestamp"] = _utc(_et(2026, 4, 17, 9, 30, 0))  # before 9:36
        result = log_inverted_shadow_safe(kw)
        assert result is False  # dropped
        # Verify a warning fired
        assert any(
            "schema violation" in r.message.lower() or "schema" in r.message.lower()
            for r in caplog.records
        )

    def test_outcome_can_be_attached(self):
        kw = self._valid_entry_kwargs()
        entry = InvertedShadowEntry(
            **kw,
            outcome=InvertedShadowOutcome(
                filled=True,
                session_close_price=6.10,
                realized_return_pct=0.0796,
                max_favorable_excursion_pct=0.18,
                max_adverse_excursion_pct=-0.04,
            ),
        )
        d = entry.to_dict()
        assert d["outcome"]["filled"] is True
        assert d["outcome"]["realized_return_pct"] == 0.0796
        assert d["kind"] == "inverted_shadow"


# ── ShadowLogger I/O behavior ────────────────────────────────────────────


class TestShadowLoggerIO:

    def test_writes_to_correct_file(self, tmp_path, monkeypatch):
        """Logger writes to data/shadow/shadow_<session_date>.jsonl."""
        # Redirect to a temp shadow dir
        from src.shadow import logger as logger_mod
        monkeypatch.setattr(logger_mod, "_SHADOW_DIR", tmp_path)
        sl = ShadowLogger()
        sl.log({
            "kind": "composite_shadow",
            "session_date": "2026-04-17",
            "ticker": "TEST",
            "production_decision": "BUY",
        })
        sl.close()
        out = tmp_path / "shadow_2026-04-17.jsonl"
        assert out.exists()
        rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
        assert len(rows) == 1
        assert rows[0]["ticker"] == "TEST"

    def test_append_mode(self, tmp_path, monkeypatch):
        """Multiple calls append to the same file."""
        from src.shadow import logger as logger_mod
        monkeypatch.setattr(logger_mod, "_SHADOW_DIR", tmp_path)
        sl = ShadowLogger()
        for i in range(3):
            sl.log({"kind": "composite_shadow", "session_date": "2026-04-17", "ticker": f"T{i}"})
        sl.close()
        out = tmp_path / "shadow_2026-04-17.jsonl"
        rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
        assert len(rows) == 3

    def test_failure_swallowed(self, tmp_path, monkeypatch, caplog):
        """If the file write fails, the logger logs a warning but does NOT raise."""
        from src.shadow import logger as logger_mod
        monkeypatch.setattr(logger_mod, "_SHADOW_DIR", tmp_path)
        sl = ShadowLogger()
        # Force serialization to fail with a non-JSON-serializable object
        # Note: default=str makes most things serializable, so use a BAD circular ref
        bad = {"kind": "x", "session_date": "2026-04-17"}
        bad["self"] = bad  # circular reference
        sl.log(bad)  # should not raise
        # Verify warning logged
        assert any("write failed" in r.message for r in caplog.records)


# ── End-to-end: composite shadow on a known-good candidate ──────────────


class TestCompositeShadowEndToEnd:

    def test_produces_log_entry_when_models_present(self, tmp_path, monkeypatch):
        """Smoke test: a normal candidate → composite scores logged."""
        # Verify models actually exist
        models_dir = Path(__file__).resolve().parents[2] / "models"
        if not (models_dir / "composite_v0_full.pkl").exists():
            pytest.skip("composite model not trained")

        from src.shadow import logger as logger_mod
        monkeypatch.setattr(logger_mod, "_SHADOW_DIR", tmp_path)
        # Reset singleton so it picks up the patched dir
        logger_mod._singleton = None
        monkeypatch.setenv("SHADOW_SCORING_ENABLED", "true")

        maybe_score_composite(
            candidate_features={
                "gap_pct": 0.40,
                "premarket_volume": 5_000_000,
                "dollar_volume": 15_000_000,
                "price": 3.50,
                "pre_market_high": 3.80,
                "orb_range_pct": 0.05,
                "day_volume": 12_000_000,
            },
            ticker="SMOKETEST",
            session_date="2026-04-17",
            production_decision="BUY",
            production_gate_rejected=None,
            production_mfcs=0.45,
        )

        out = tmp_path / "shadow_2026-04-17.jsonl"
        assert out.exists()
        rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
        assert len(rows) == 1
        row = rows[0]
        assert row["kind"] == "composite_shadow"
        assert row["ticker"] == "SMOKETEST"
        assert row["production_decision"] == "BUY"
        assert row["composite_score_full"] is not None
        assert 0.0 <= row["composite_score_full"] <= 1.0
        assert row["agreement"] in (
            "AGREE_BUY", "AGREE_NO_TRADE", "DISAGREE_SHADOW_BUYS", "DISAGREE_PROD_BUYS"
        )
        # D221 Phase F: sec_data_available is reserved in schema, default None
        # until docs/engineering_hygiene/sec_degradation_fix.md ships.
        assert "sec_data_available" in row, "schema must include sec_data_available"
        assert row["sec_data_available"] is None
        # Reset singleton for downstream tests
        logger_mod._singleton = None


class TestShadowSecDataAvailableField:
    """D221 Phase F: sec_data_available is reserved in the shadow schema with
    three-state semantics (None / True / False). Producer-side wiring to
    populate True/False ships with the SEC degradation fix; until then the
    field is always None."""

    def _emit(self, tmp_path, monkeypatch, **extra) -> dict:
        """Run maybe_score_composite with **extra kwargs and return the row."""
        models_dir = Path(__file__).resolve().parents[2] / "models"
        if not (models_dir / "composite_v0_full.pkl").exists():
            pytest.skip("composite model not trained")

        from src.shadow import logger as logger_mod
        monkeypatch.setattr(logger_mod, "_SHADOW_DIR", tmp_path)
        logger_mod._singleton = None
        monkeypatch.setenv("SHADOW_SCORING_ENABLED", "true")

        maybe_score_composite(
            candidate_features={
                "gap_pct": 0.40, "premarket_volume": 5_000_000,
                "dollar_volume": 15_000_000, "price": 3.50,
                "pre_market_high": 3.80, "orb_range_pct": 0.05,
                "day_volume": 12_000_000,
            },
            ticker="SECTEST",
            session_date="2026-04-17",
            production_decision="BUY",
            production_gate_rejected=None,
            production_mfcs=0.45,
            **extra,
        )

        out = tmp_path / "shadow_2026-04-17.jsonl"
        rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
        logger_mod._singleton = None
        return rows[-1]

    def test_default_sec_data_available_is_none(self, tmp_path, monkeypatch):
        """Default value when caller omits the kwarg."""
        row = self._emit(tmp_path, monkeypatch)
        assert "sec_data_available" in row
        assert row["sec_data_available"] is None

    def test_explicit_true_persists(self, tmp_path, monkeypatch):
        """SEC fetched cleanly this evaluation."""
        row = self._emit(tmp_path, monkeypatch, sec_data_available=True)
        assert row["sec_data_available"] is True

    def test_explicit_false_persists(self, tmp_path, monkeypatch):
        """SEC fetch failed (5xx / outage) — must distinguish from True and None."""
        row = self._emit(tmp_path, monkeypatch, sec_data_available=False)
        assert row["sec_data_available"] is False
