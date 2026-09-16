"""EOD report consolidation — single JSON dict for Monday-morning review.

Per the Saturday 2026-04-26 sprint plan: each EOD section currently logs
independently (D241/D242/D238/D261/D262/Bayesian/Cohort/Recon). The
consolidator collects every section's structured return + persists a
single `data/reports/eod_<session_date>.json` file that:

  * The Monday-morning review reads to triage the day in one place
  * The dashboard ingests for time-series visualization
  * The Track B daemon arming criteria check (per `26_d_code_registry.md`)
    references for "what fired in the last N=2/N=5 sessions"

Atomic-write via tmp + os.replace (D218 discipline). Non-fatal — a
report-write failure NEVER blocks session close.

The report shape is INTENTIONALLY flat / dict-based (not Pydantic) so:
  1. Adding new sections is a one-line dict update — no schema bump
  2. Older readers tolerate new sections via dict.get(..., default)
  3. The dashboard / Monday review can grep section names directly

If a section is None (skipped or failed), it appears as `null` in JSON
so the reader can distinguish "didn't run" from "ran but no result."
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


DEFAULT_REPORT_DIR: Path = Path("data/reports")  # noqa: relative-path — repo-relative default; main.py + tests pass absolute paths


def build_eod_report(
    *,
    session_date: str | None = None,
    eod_recon: dict | None = None,
    phase0_health: dict | None = None,
    bocpd_refit: dict | None = None,
    cohort_backfill: dict | None = None,
    bayesian_fit: dict | None = None,
    eod_failsafes: dict | None = None,
    signing_audit: dict | None = None,
    extra_metadata: dict | None = None,
) -> dict[str, Any]:
    """Aggregate per-section outputs into a single EOD report dict.

    Args:
      session_date:    YYYY-MM-DD (default: today UTC)
      eod_recon:       run_eod_invariants() return value
      phase0_health:   run_eod_phase0_health(writer) return value
      bocpd_refit:     run_eod_bocpd_refit_check() return value
      cohort_backfill: run_eod_cohort_backfill(client) return value
      bayesian_fit:    run_eod_bayesian_fit() return value
      eod_failsafes:   run_all_eod_failsafes(client, ...) return value
      signing_audit:   run_eod_signing_audit(corpus) return value
      extra_metadata:  caller-supplied additional fields (e.g. equity)

    Returns the assembled dict, ready for json.dump.
    """
    if session_date is None:
        session_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Top-level "fires" summary — quick triage line for the dashboard
    fires: list[str] = []
    for code, section in [
        ("D260", signing_audit),
        ("D261", phase0_health),
        ("D262", bocpd_refit),
        ("D256-D259", bayesian_fit),
    ]:
        if not section:
            continue
        if section.get("fires_d260") or section.get("fires_d261") or \
           section.get("fires_d262"):
            fires.append(code)
        if code == "D256-D259" and section.get("gate_results"):
            for gr in section["gate_results"]:
                if gr.get("triggered"):
                    fires.append(gr.get("code", "D256-D259"))

    # eod_recon's structured fields (qty_drift_count, stop_drift_count, etc.)
    recon_summary = None
    if eod_recon:
        recon_summary = {
            "broker_reachable": eod_recon.get("broker_reachable"),
            "qty_drift_count": eod_recon.get("qty_drift_count", 0),
            "stop_drift_count": eod_recon.get("stop_drift_count", 0),
            "equity_within_tolerance": eod_recon.get("equity_within_tolerance"),
            "broker_equity": eod_recon.get("broker_equity"),
        }
        if eod_recon.get("qty_drift_count", 0) > 0:
            fires.append("D231-qty")
        if eod_recon.get("stop_drift_count", 0) > 0:
            fires.append("D231-stop")
        if eod_recon.get("equity_within_tolerance") is False:
            fires.append("D230-equity")

    return {
        "session_date": session_date,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fires": fires,                        # Quick-triage list of D-codes
        "fires_count": len(fires),
        "sections": {
            "eod_recon": recon_summary,
            "phase0_health": phase0_health,
            "bocpd_refit": bocpd_refit,
            "cohort_backfill": cohort_backfill,
            "bayesian_fit": bayesian_fit,
            "eod_failsafes": eod_failsafes,
            "signing_audit": signing_audit,
        },
        "metadata": extra_metadata or {},
    }


def write_eod_report(
    report: dict,
    *,
    report_dir: Path | str = DEFAULT_REPORT_DIR,
) -> Path | None:
    """Atomic-write the EOD report. Returns the path on success, None on
    failure (logs at ERROR but never raises)."""
    session_date = report.get("session_date", "unknown")
    report_dir_p = Path(report_dir)
    try:
        report_dir_p.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        logger.error("EOD report: failed to create dir %s: %s", report_dir_p, e)
        return None
    out_path = report_dir_p / f"eod_{session_date}.json"
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, sort_keys=True, default=str)
        os.replace(tmp_path, out_path)
    except Exception as e:
        logger.error("EOD report: atomic write failed for %s: %s", out_path, e)
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:  # noqa: silent-handler — best-effort cleanup
                pass
        return None

    fires = report.get("fires", [])
    if fires:
        logger.warning(
            "EOD REPORT %s: %d D-codes fired this session: %s. Review %s",
            session_date, len(fires), ", ".join(fires), out_path,
        )
    else:
        logger.info(
            "EOD REPORT %s: 0 D-codes fired this session (clean). Report at %s",
            session_date, out_path,
        )
    return out_path
