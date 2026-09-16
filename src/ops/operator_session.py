"""doc 207: Operator session — the ORIENT context assembler ("never guess about state").

The Operator is a Claude (Opus 4.8 MAX) session; THIS module is not a decision-maker — it
assembles the full-state brief the session reads FIRST (Pierce's hard requirement), and
provides the session lifecycle helpers (lock + standings). The decisions/actions are the
Claude session, guided by the runbook, using operator_actions + its own tools.

`assemble_context()` returns a dict; `render_brief()` renders the markdown brief; running
this module prints the brief (what the scheduled `claude -p` reads at the top of a session).
All reads are guarded — assembling the brief never raises.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from src.ops import incident_bus, operator_actions
from src.ops import operator_governance as gov
from src.ops import operator_scorecard

logger = logging.getLogger(__name__)
_NY = ZoneInfo("America/New_York")
_ROOT = Path(__file__).resolve().parent.parent.parent


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def _git() -> dict:
    def run(args):
        out = subprocess.run(["git"] + args, cwd=str(_ROOT), capture_output=True,
                             text=True, timeout=15)
        return out.stdout.strip() if out.returncode == 0 else ""
    return {
        "head": _safe(lambda: run(["rev-parse", "--short", "HEAD"]), ""),
        "subject": _safe(lambda: run(["log", "-1", "--pretty=%s"]), ""),
        "dirty": _safe(lambda: bool(run(["status", "--porcelain"])), False),
    }


def _deployed_commit() -> str:
    """The commit the RUNNING bot booted on (startup 'D217 STARTUP ... commit=')."""
    try:
        logs = sorted((_ROOT / "logs").glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        for lg in logs[:3]:
            txt = _safe(lambda: lg.read_text(encoding="utf-8", errors="ignore"), "") or ""
            m = re.findall(r"commit=([0-9a-f]{7,40})", txt)
            if m:
                return m[-1][:8]
    except Exception:
        pass
    return "unknown"


def _json(p: Path):
    return _safe(lambda: json.loads(p.read_text(encoding="utf-8")) if p.exists() else None)


def _latest_eod() -> dict | None:
    try:
        files = sorted((_ROOT / "data" / "reports").glob("eod_*.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        return _json(files[0]) if files else None
    except Exception:
        return None


def assemble_context(session_date: str | None = None) -> dict:
    date = session_date or datetime.now(timezone.utc).astimezone(_NY).strftime("%Y-%m-%d")
    incidents = incident_bus.read_incidents(date, unresolved_only=True)
    sev_counts: dict[str, int] = {}
    for i in incidents:
        sev_counts[i.get("severity", "?")] = sev_counts.get(i.get("severity", "?"), 0) + 1
    git = _git()
    deployed = _deployed_commit()
    recon = _json(_ROOT / "data" / "recon_status.json")
    eod = _latest_eod()
    armed, arm_reason = gov.is_t1_armed()
    return {
        "session_date": date,
        "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git": git,
        "deployed_commit": deployed,
        "code_deployed_is_head": (deployed != "unknown" and git.get("head", "").startswith(deployed[:7])),
        "halted": operator_actions.is_halted(),
        "t1_armed": armed, "t1_arm_reason": arm_reason,
        "fuse": gov.fuse_state(),
        "in_flight": gov.read_in_flight(),
        "incidents_unresolved": len(incidents),
        "incidents_by_severity": sev_counts,
        "incidents": incidents[-12:],
        "scorecard": operator_scorecard.standings(date),
        "recon_status": recon,
        "eod_recon": (eod or {}).get("eod_failsafes", {}).get("broker_truth_recon") if eod else None,
        "wake_pending": incident_bus.has_wake(),
    }


def render_brief(ctx: dict) -> str:
    g = ctx["git"]
    code_ok = "✅ HEAD deployed" if ctx["code_deployed_is_head"] else \
        f"⚠️ running {ctx['deployed_commit']} != HEAD {g.get('head')} (a restart will deploy newer code)"
    lines = [
        f"# OPERATOR BRIEF — {ctx['session_date']} ({ctx['ts_utc']})",
        f"- CODE: HEAD {g.get('head')} \"{g.get('subject','')[:60]}\"{' [dirty]' if g.get('dirty') else ''} · {code_ok}",
        f"- HALT new entries: {'🛑 ON' if ctx['halted'] else 'off'} · "
        f"T1 {'armed' if ctx['t1_armed'] else 'DISARMED (' + ctx['t1_arm_reason'] + ')'}"
        f"{' · FUSE TRIPPED: ' + str(ctx['fuse'].get('tripped_reason')) if ctx['fuse'].get('tripped') else ''}",
        f"- INCIDENTS unresolved: {ctx['incidents_unresolved']} {ctx['incidents_by_severity']}"
        f"{' · ⚡WAKE pending' if ctx['wake_pending'] else ''}",
        f"- IN-FLIGHT T2: {len(ctx['in_flight'].get('t2', []))}",
        f"- SCORE: {operator_scorecard.game_line(ctx['session_date'])}",
    ]
    if ctx.get("eod_recon"):
        lines.append(f"- LAST EOD broker-truth recon: {json.dumps(ctx['eod_recon'])[:200]}")
    if ctx["incidents"]:
        lines.append("- OPEN INCIDENTS:")
        for i in ctx["incidents"]:
            lines.append(f"    [{i.get('severity')}] {i.get('kind')} {i.get('ticker') or ''} "
                         f"— {json.dumps(i.get('context', {}))[:120]}")
    lines.append("\nORIENT done. Now: triage (severity order) → act within tier (runbook) → "
                 "snapshot-first for any T1 → score + Discord. Restraint is the high score.")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    # the brief carries emoji (Discord is UTF-8); guard the Windows cp1252 console
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    brief = render_brief(assemble_context())
    try:
        print(brief)
    except UnicodeEncodeError:
        print(brief.encode("ascii", "replace").decode("ascii"))
