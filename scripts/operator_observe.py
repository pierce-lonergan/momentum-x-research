#!/usr/bin/env python
"""doc 208: Operator OBSERVE session (T0-only) — the Monday shakedown runner.

Assembles the ORIENT brief, triages unresolved incidents (CLASSIFY ONLY — no actions in
observe mode), posts to Discord, appends to the daily operator log, and records a
restraint-aware score. This is the safe, deterministic Operator the scheduler runs BEFORE
T1 is armed. The richer Opus-4.8-MAX triage (`claude -p`) is invoked by operator_pulse.ps1
when available; this guarantees a working, visible session regardless.

Usage:
    python scripts/operator_observe.py --label post-open
    python scripts/operator_observe.py --label boot-health --no-discord
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Load the bot's .env so the scheduled Operator process inherits OPS_ALERT_WEBHOOK_URL +
# OPS_* flags (the Discord heartbeat + governance flags live there / in the machine env).
try:
    from dotenv import load_dotenv
    load_dotenv(_ROOT / ".env")
except Exception:
    pass

from src.ops import discord_notify, incident_bus, incident_synth  # noqa: E402
from src.ops.operator_scorecard import OperatorMetrics, record_session, game_line  # noqa: E402
from src.ops.operator_session import assemble_context, render_brief  # noqa: E402

_NY = ZoneInfo("America/New_York")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="pulse", help="which scheduled session (e.g. post-open)")
    ap.add_argument("--no-discord", action="store_true")
    args = ap.parse_args()

    # doc 224: master Operator kill-switch. When OPS_OPERATOR_ENABLED is off, the scheduled
    # pulse is a clean NO-OP — no observe session, no Discord, no scoring. (Turned OFF for the
    # Tuesday 6/2 deploy to focus on execution-hardening + raw-fill capture without Operator
    # noise. NOTE: this does NOT touch the raw-fill capture [websocket-side], the incident bus
    # [bot-side], or the post-close scorecard/adversary [trading-launcher Phase-4 tail] — those
    # stay live. Re-enable: set OPS_OPERATOR_ENABLED=1.)
    if os.environ.get("OPS_OPERATOR_ENABLED", "1").strip().lower() in ("0", "false", "no", "off"):
        print(f"[operator-observe {args.label}] DISABLED via OPS_OPERATOR_ENABLED — no-op")
        return 0

    date = datetime.now(timezone.utc).astimezone(_NY).strftime("%Y-%m-%d")
    # derive incidents from the bot's existing artifacts (recon status, log) before ORIENT
    synth_n = incident_synth.synthesize(date)
    ctx = assemble_context(date)
    brief = render_brief(ctx)

    # Triage (observe-only): classify unresolved incidents; act on NOTHING (T1 disarmed).
    incidents = ctx.get("incidents", [])
    crit = [i for i in incidents if i.get("severity") in ("CRITICAL", "DECISION")]
    clean = (len(crit) == 0)

    # Append to the daily operator log (the prev-session context for the next pulse).
    log = _ROOT / "docs" / "research-log" / f"operator_log_{date}.md"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(f"\n## {ctx['ts_utc']} — session: {args.label} (OBSERVE / T0-only)\n\n")
            f.write(brief + "\n")
            if crit:
                f.write(f"\n**TRIAGE**: {len(crit)} unresolved CRITICAL/DECISION — "
                        f"observe-mode cannot act; ESCALATING to Discord.\n")
            else:
                f.write("\n**TRIAGE**: nothing requires action — correct no-op (restraint).\n")
    except Exception:
        pass

    # Score: observe-only. A clean session = correct no-op (restraint is the high score).
    # Unresolved CRITICAL that observe-mode can't act on is an escalation, not a no-op.
    try:
        record_session(OperatorMetrics(
            session_date=date, correct_noops=(1 if clean else 0),
            notes=f"observe:{args.label}" + ("" if clean else f" ESCALATED:{len(crit)}")))
    except Exception:
        pass

    # Discord (Pierce wants it visible).
    if not args.no_discord:
        head = f"[{args.label}] {date}"
        if clean:
            msg = (f"{head} — all clear ✅\n"
                   f"HALT:{'ON' if ctx['halted'] else 'off'} · "
                   f"T1:{'armed' if ctx['t1_armed'] else 'disarmed'} · "
                   f"incidents:{ctx['incidents_unresolved']} · {game_line(date)}")
        else:
            lines = "\n".join(f"  • [{i.get('severity')}] {i.get('kind')} {i.get('ticker') or ''}"
                              for i in crit[:6])
            msg = (f"{head} — ⚠️ {len(crit)} CRITICAL/DECISION need attention:\n{lines}\n"
                   f"(observe-mode: not acting — T1 disarmed. {game_line(date)})")
        discord_notify.post(msg, prefix="🤖 Operator (observe)")

    incident_bus.clear_wake()  # this session handled the wake by observing

    # encode-safe console print
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    print(f"[operator-observe {args.label}] clean={clean} incidents={ctx['incidents_unresolved']} "
          f"discord={'off' if args.no_discord else discord_notify.is_configured()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
