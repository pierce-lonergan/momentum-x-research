"""doc 208: Artifact-derived incident synthesis (zero live-code risk).

Turns the bot's EXISTING outputs (recon_status.json, the day's log) into incidents the
Operator can triage — WITHOUT editing live trading code. This gives the Operator real signal
from day one safely; real-time emit_incident() calls wired INTO specific detectors (circuit
breaker, drawdown, ghost) are a clean follow-on once each point is verified. Guarded;
dedup'd so repeated pulses don't spam the bus.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from src.ops.incident_bus import emit_incident

logger = logging.getLogger(__name__)
_NY = ZoneInfo("America/New_York")
_ROOT = Path(__file__).resolve().parent.parent.parent

# log lines that signal a real problem worth an incident. doc 212: BROADENED after day 1 —
# the Operator was BLIND to the STG ghost (D231 fired every 30s), the journal phantom (D222),
# the EOD force-close (D242), and the unintended overnight carry (D90), reporting "0
# incidents / correct no-op / score 25" while a $3K+ mess sat at the broker. These are the
# loudest real signals; they MUST trip an incident.
_CRIT_PAT = re.compile(
    r"RECON_LETHAL|RECON_HARD_BLOCK|CIRCUIT.*OPEN|circuit breaker.*trip|GHOST|phantom|"
    r"PNL_RECON DIVERGENCE|EOD_FORCE_CLOSE|HEDGE VIOLATION|"
    r"DAILY.*LIMIT|DRAWDOWN.*(HALT|BREACH)|CRITICAL", re.I)
# carry-overnight is a WARN-level (not always bad, but the Operator must SEE it)
_CARRY_PAT = re.compile(r"D90:.*carry overnight|will carry overnight|positions will carry", re.I)
_ERR_LOG_THRESHOLD = 25   # ERROR lines in the recent window -> a degradation incident


def _today(date: str | None) -> str:
    return date or datetime.now(timezone.utc).astimezone(_NY).strftime("%Y-%m-%d")


def synthesize(date: str | None = None) -> int:
    """Scan artifacts; emit incidents for anything bad. Returns the count emitted."""
    date = _today(date)
    n = 0
    n += _from_recon_status()
    n += _from_eod_report(date)   # doc 212: the richest source — broker-truth recon
    n += _from_log(date)
    return n


def _from_eod_report(date: str) -> int:
    """doc 212: the EOD json carries broker truth that the Operator was blind to on day 1 —
    journal-vs-broker P&L divergence (phantom), qty_drift (ghost), equity-out-of-tolerance."""
    try:
        f = _ROOT / "data" / "reports" / f"eod_{date}.json"
        if not f.exists():
            return 0
        d = json.loads(f.read_text(encoding="utf-8"))
        s = d.get("sections", {}) or {}
        btr = s.get("eod_failsafes", {}).get("broker_truth_recon", {}) or {}
        recon = s.get("eod_recon", {}) or {}
        emitted = 0
        # phantom: journal P&L disagrees with broker P&L beyond $1 tolerance
        delta = btr.get("delta_usd")
        if delta is not None and abs(float(delta)) > 1.0:
            emit_incident(
                "PNL_RECON_DIVERGENCE", "CRITICAL",
                context={"journal_pnl": btr.get("journal_total_pnl"),
                         "broker_pnl": btr.get("broker_total_pnl"), "delta_usd": delta,
                         "disagreements": btr.get("ticker_disagreements")},
                suggested=["a close path booked P&L without a confirmed broker fill (phantom)",
                           "trust broker_total_pnl; the journal P&L is contaminated"],
                dedup_key=f"pnl_divergence_{date}")
            emitted += 1
        # ghost / position drift between internal tracker and broker
        qd = recon.get("qty_drift_count")
        if qd is not None and int(qd) > 0:
            emit_incident(
                "QTY_DRIFT_GHOST", "CRITICAL",
                context={"qty_drift_count": qd,
                         "equity_within_tolerance": recon.get("equity_within_tolerance"),
                         "broker_equity": recon.get("broker_equity")},
                suggested=["broker holds a position the tracker doesn't (or vice-versa)",
                           "D86/D242 ghost cleanup; verify no naked/unmanaged position"],
                dedup_key=f"qty_drift_{date}")
            emitted += 1
        elif recon.get("equity_within_tolerance") is False:
            emit_incident(
                "EQUITY_OUT_OF_TOLERANCE", "WARN",
                context={"broker_equity": recon.get("broker_equity"),
                         "equity_within_tolerance": False},
                suggested=["internal equity estimate diverged from broker"],
                dedup_key=f"equity_tol_{date}")
            emitted += 1
        return emitted
    except Exception as e:  # noqa: BLE001
        logger.warning("incident_synth eod failed: %s", e)
    return 0


def _from_recon_status() -> int:
    try:
        f = _ROOT / "data" / "recon_status.json"
        if not f.exists():
            return 0
        d = json.loads(f.read_text(encoding="utf-8"))
        # defensive: look for any field that signals trouble
        status = str(d.get("status", "")).upper()
        lethal = bool(d.get("lethal") or d.get("recon_lethal"))
        bad = lethal or status in ("LETHAL", "MISMATCH", "FAIL", "ERROR", "DRIFT")
        if bad:
            emit_incident("RECON_STATUS_BAD", "CRITICAL",
                          context={"recon_status": {k: d.get(k) for k in list(d)[:8]}},
                          suggested=["verify broker vs internal positions",
                                     "consider HALT new entries until reconciled"],
                          dedup_key="recon_status_bad")
            return 1
    except Exception as e:  # noqa: BLE001
        logger.warning("incident_synth recon failed: %s", e)
    return 0


def _from_log(date: str) -> int:
    """Count critical/error patterns in the last slice of the day's log."""
    try:
        logs = list((_ROOT / "logs").glob(f"*{date}*.log"))
        if not logs:
            return 0
        lg = max(logs, key=lambda p: p.stat().st_mtime)
        lines = lg.read_text(encoding="utf-8", errors="ignore").splitlines()[-400:]
        crit = [ln for ln in lines if _CRIT_PAT.search(ln)]
        carry = [ln for ln in lines if _CARRY_PAT.search(ln)]
        errs = [ln for ln in lines if " ERROR " in ln or ln.strip().startswith("ERROR")]
        emitted = 0
        if crit:
            emit_incident("LOG_CRITICAL_PATTERN", "CRITICAL",
                          context={"count": len(crit), "sample": crit[-1][:200], "log": lg.name},
                          suggested=["read the surrounding log context", "triage the named subsystem"],
                          dedup_key=f"log_crit_{crit[-1][:60]}")
            emitted += 1
        if carry:
            emit_incident("OVERNIGHT_CARRY", "WARN",
                          context={"sample": carry[-1][:200], "log": lg.name},
                          suggested=["positions carrying overnight — confirm intentional",
                                     "if unintended: the EOD close failed (avail=0 held_for_orders?)"],
                          dedup_key=f"carry_{date}")
            emitted += 1
        if len(errs) >= _ERR_LOG_THRESHOLD:
            emit_incident("LOG_ERROR_RATE_HIGH", "WARN",
                          context={"error_lines_recent": len(errs), "log": lg.name},
                          suggested=["check for an API/LLM degradation storm"],
                          dedup_key="log_error_rate")
            emitted += 1
        return emitted
    except Exception as e:  # noqa: BLE001
        logger.warning("incident_synth log failed: %s", e)
    return 0
