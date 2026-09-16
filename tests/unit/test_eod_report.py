"""EOD report consolidator tests.

Per the Saturday 2026-04-26 sprint plan: each EOD section logs
independently; the consolidator collects everything into a single
data/reports/eod_<date>.json file for Monday-morning triage.

11 tests:
  - build_eod_report shape (5)
  - fires-list aggregation (3)
  - write_eod_report atomic + error paths (3)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.monitoring.eod_report import build_eod_report, write_eod_report


# ── build_eod_report shape ─────────────────────────────────────


class TestBuildEodReport:

    def test_minimal_report_with_no_sections(self) -> None:
        """Report with all-None sections still constructs a valid dict."""
        r = build_eod_report(session_date="2026-04-26")
        assert r["session_date"] == "2026-04-26"
        assert "generated_at" in r
        assert r["fires"] == []
        assert r["fires_count"] == 0
        # All sections are None
        for k in ("eod_recon", "phase0_health", "bocpd_refit",
                  "cohort_backfill", "bayesian_fit", "eod_failsafes",
                  "signing_audit"):
            assert r["sections"][k] is None
        assert r["metadata"] == {}

    def test_session_date_defaults_to_today(self) -> None:
        from datetime import datetime, timezone
        r = build_eod_report()
        expected = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert r["session_date"] == expected

    def test_extra_metadata_propagates(self) -> None:
        r = build_eod_report(
            session_date="2026-04-26",
            extra_metadata={"broker_equity_eod": 100_500.50, "open_positions_eod": 0},
        )
        assert r["metadata"]["broker_equity_eod"] == 100_500.50
        assert r["metadata"]["open_positions_eod"] == 0

    def test_eod_recon_summary_extracted(self) -> None:
        eod_recon = {
            "broker_reachable": True,
            "qty_drift_count": 0,
            "stop_drift_count": 0,
            "equity_within_tolerance": True,
            "broker_equity": 99_750.0,
            # plus other fields the consolidator should ignore
            "internal_equity_estimate": 99_745.0,
            "qty_drift_details": [],
        }
        r = build_eod_report(session_date="2026-04-26", eod_recon=eod_recon)
        assert r["sections"]["eod_recon"]["broker_reachable"] is True
        assert r["sections"]["eod_recon"]["broker_equity"] == 99_750.0
        # Extraneous fields not in the summary
        assert "internal_equity_estimate" not in r["sections"]["eod_recon"]

    def test_full_report_carries_all_sections(self) -> None:
        r = build_eod_report(
            session_date="2026-04-26",
            eod_recon={"broker_reachable": True, "qty_drift_count": 0,
                       "stop_drift_count": 0, "equity_within_tolerance": True,
                       "broker_equity": 100_000.0},
            phase0_health={"session_date": "2026-04-26", "total_d261_failures": 0},
            bocpd_refit={"fires_d262": False, "diff": {"delta_n_trades": 0}},
            cohort_backfill={"error": None, "result": {"rows_backfilled": 5}},
            bayesian_fit={"error": "below_min_observations", "n_observations": 8},
            eod_failsafes={"d242_count": 0, "d241_cancels": 0},
            signing_audit={"fires_d260": False, "summary": {"qmp_signed": 0}},
        )
        assert all(r["sections"][k] is not None for k in (
            "eod_recon", "phase0_health", "bocpd_refit", "cohort_backfill",
            "bayesian_fit", "eod_failsafes", "signing_audit",
        ))


# ── Fires-list aggregation ─────────────────────────────────────


class TestFiresList:

    def test_d262_appears_when_bocpd_refit_recommends(self) -> None:
        r = build_eod_report(
            session_date="2026-04-26",
            bocpd_refit={"fires_d262": True, "diff": {"delta_n_trades": 15}},
        )
        assert "D262" in r["fires"]
        assert r["fires_count"] >= 1

    def test_recon_drift_adds_d231_d230_codes(self) -> None:
        r = build_eod_report(
            session_date="2026-04-26",
            eod_recon={
                "broker_reachable": True,
                "qty_drift_count": 1, "stop_drift_count": 1,
                "equity_within_tolerance": False,
                "broker_equity": 100_000.0,
            },
        )
        assert "D231-qty" in r["fires"]
        assert "D231-stop" in r["fires"]
        assert "D230-equity" in r["fires"]
        assert r["fires_count"] == 3

    def test_bayesian_gate_triggers_propagate_to_fires(self) -> None:
        r = build_eod_report(
            session_date="2026-04-26",
            bayesian_fit={
                "error": None, "n_observations": 50,
                "posterior": {"eta_mean": 0.7, "gamma_mean": 0.5},
                "gate_results": [
                    {"code": "D256", "triggered": True},
                    {"code": "D258", "triggered": False},
                    {"code": "D259", "triggered": False},
                ],
            },
        )
        assert "D256" in r["fires"]
        assert "D258" not in r["fires"]


# ── write_eod_report ────────────────────────────────────────────


class TestWriteEodReport:

    def test_writes_atomic_round_trip(self, tmp_path: Path) -> None:
        r = build_eod_report(session_date="2026-04-26",
                              extra_metadata={"open_positions_eod": 3})
        path = write_eod_report(r, report_dir=tmp_path)
        assert path is not None
        assert path.exists()
        assert path.name == "eod_2026-04-26.json"
        # Round-trip
        with open(path, encoding="utf-8") as f:
            loaded = json.load(f)
        assert loaded["session_date"] == "2026-04-26"
        assert loaded["metadata"]["open_positions_eod"] == 3

    def test_logs_warning_when_fires_present(self, tmp_path: Path, caplog) -> None:
        import logging
        r = build_eod_report(
            session_date="2026-04-26",
            eod_recon={
                "broker_reachable": True,
                "qty_drift_count": 1, "stop_drift_count": 0,
                "equity_within_tolerance": True,
                "broker_equity": 100_000.0,
            },
        )
        with caplog.at_level(logging.WARNING, logger="src.monitoring.eod_report"):
            write_eod_report(r, report_dir=tmp_path)
        assert any("EOD REPORT" in m.message and "D-codes fired" in m.message
                   for m in caplog.records)

    def test_logs_info_when_no_fires(self, tmp_path: Path, caplog) -> None:
        import logging
        r = build_eod_report(session_date="2026-04-26")
        with caplog.at_level(logging.INFO, logger="src.monitoring.eod_report"):
            write_eod_report(r, report_dir=tmp_path)
        assert any("0 D-codes fired" in m.message for m in caplog.records)
