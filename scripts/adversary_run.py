#!/usr/bin/env python
"""doc 218: The Adversary harness — battle our REAL execution code, score invariant breaks.

Drives our production `attempt_close_with_status_check` (the shared close wrapper D76/D91/D245
all use) against `AdversaryBroker`, which controls broker timing + errors. The Adversary
tries to make us go NAKED, book a PHANTOM, or strand a position; we score whether our real
code survives. This is the "market battles our process" framing applied to the DETERMINISTIC
execution plumbing (can't be overfit — doc 217).

Each scenario seeds a position + a reserving protective stop, sets the adversary's
settle-latency / failure mode, runs the REAL close, books P&L ONLY on confirmed success
(mirroring the doc-177 discipline), then checks invariants.

Usage:
    python scripts/adversary_run.py            # full sweep
    python scripts/adversary_run.py --json data/reports/adversary_report.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.execution.bridge import attempt_close_with_status_check  # noqa: E402
from src.ops import incident_bus  # noqa: E402
from src.ops.adversary import AdversaryBroker, check_invariants  # noqa: E402


async def _battle(name, *, settle_delay, close_hard_fail=False, qty=3282, max_retries=3,
                  partial_fill_frac=None, stop_fills_at_tick=None,
                  deadline_tick=None, halt_until_tick=None):
    """One round: seed a held position + reserving stop, run the REAL close, score it."""
    incident_bus._recent.clear()  # isolate this scenario's incidents
    _before = len(incident_bus.read_incidents())
    broker = AdversaryBroker(settle_delay_ticks=settle_delay, close_hard_fail=close_hard_fail,
                             partial_fill_frac=partial_fill_frac,
                             stop_fills_at_tick=stop_fills_at_tick,
                             deadline_tick=deadline_tick, halt_until_tick=halt_until_tick)
    broker.seed_position("X", qty, entry=3.46, current=3.00)   # an underwater loser (the EOD case)
    broker.seed_stop("X", qty, stop_price=2.94)                # reserves all qty -> close 403s

    booked_pnl = False
    close_result = None
    try:
        close_result = await attempt_close_with_status_check(
            client=broker, ticker="X", qty=qty,
            max_retries=max_retries, cancel_blocking_stops_first=True)
        # doc-177 discipline: book P&L ONLY on confirmed broker close
        if close_result.get("succeeded"):
            booked_pnl = True
    except Exception as e:  # the close wrapper should never raise into the caller
        close_result = {"succeeded": False, "raised": str(e)[:120]}

    # did the close path ESCALATE a NAKED_POSITION_RISK (CRITICAL -> operator)?
    escalated = any(i.get("kind") == "NAKED_POSITION_RISK"
                    for i in incident_bus.read_incidents()[_before:])
    rep = check_invariants(name, broker, close_result=close_result,
                           booked_pnl=booked_pnl, escalated=escalated)
    return rep, close_result, broker


SCENARIOS = [
    # each = (name, kwargs-for-_battle) — the Adversary's moves
    ("fast_settle (1 tick)",          {"settle_delay": 1}),
    ("realistic_settle (3 ticks)",    {"settle_delay": 3}),     # ~the 6/1 CMND timing
    ("slow_settle (5 ticks)",         {"settle_delay": 5}),
    ("very_slow_settle (10 ticks)",   {"settle_delay": 10}),    # cancel barely settles in budget
    ("never_settles (999)",           {"settle_delay": 999}),   # cancel NEVER frees -> FAIL CLEAN
    ("close_hard_fail (broker down)", {"settle_delay": 1, "close_hard_fail": True}),
    # ── doc 219: new weapons ──
    ("partial_fill (60%)",            {"settle_delay": 2, "partial_fill_frac": 0.6}),  # remainder must not strand
    ("stop_fills_mid_close",          {"settle_delay": 3, "stop_fills_at_tick": 2}),   # position vanishes mid-close
    ("trading_halt (lifts @4)",       {"settle_delay": 1, "halt_until_tick": 4}),      # close 403s until halt lifts
    ("eod_deadline (bell @2)",        {"settle_delay": 5, "deadline_tick": 2}),        # settle pushes past the bell
    ("eod_deadline_never_settles",    {"settle_delay": 999, "deadline_tick": 2}),      # past bell AND fails -> carry
]


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    print("\n=== THE ADVERSARY vs our real attempt_close_with_status_check ===")
    print(f"  {'scenario':>28} | {'close':>8} | {'naked?':>6} | {'phantom?':>8} | verdict")
    print("  " + "-" * 78)
    results = []
    survived = 0
    for name, kw in SCENARIOS:
        rep, cr, broker = await _battle(name, **kw)
        closed = "OK" if (cr and cr.get("succeeded")) else "FAILED"
        naked = any("SILENT-NAKED" in b for b in rep.broke)
        phantom = any("PHANTOM" in b for b in rep.broke)
        verdict = "✅ SURVIVED" if rep.survived else "🔴 ADVERSARY WINS: " + "; ".join(rep.broke)
        print(f"  {name:>28} | {closed:>8} | {('YES' if naked else 'no'):>6} | "
              f"{('YES' if phantom else 'no'):>8} | {verdict}")
        results.append({"scenario": name, "kwargs": kw,
                        "close_succeeded": bool(cr and cr.get("succeeded")),
                        "survived": rep.survived, "broke": rep.broke,
                        "attempts": (cr or {}).get("attempts")})
        if rep.survived:
            survived += 1

    # doc 219: separate WRAPPER bugs (the close code can/should fix) from ARCHITECTURAL flags
    # (the close ran past the bell -> only the EOD-timing fix can prevent the carry; the
    # wrapper correctly FAILS rather than phantom-closing). The latter is not a wrapper defect.
    arch_flagged = [r for r in results
                    if not r["survived"] and any("DEADLINE-CARRY" in b for b in r["broke"])]
    wrapper_bugs = [r for r in results
                    if not r["survived"] and r not in arch_flagged]
    print(f"\n  RESULT: survived {survived}/{len(SCENARIOS)} scenarios.")
    print(f"    wrapper bugs (must fix in the close code): {len(wrapper_bugs)} "
          f"{[r['scenario'] for r in wrapper_bugs] or '— none ✅'}")
    print(f"    architectural flags (need the EOD-timing fix, NOT a wrapper bug): "
          f"{[r['scenario'] for r in arch_flagged] or '— none'}")
    print("  KEY INVARIANTS: never silent-naked · never phantom · never partial-strand. A")
    print("  FAILED close is CORRECT when the broker won't fill — as long as the position stays")
    print("  protected/escalated and no phantom P&L is booked.")

    # the headline test: realistic_settle MUST close (the doc-216 fix); never_settles &")
    realistic = next(r for r in results if "realistic" in r["scenario"])
    never = next(r for r in results if "never_settles" in r["scenario"])
    print(f"\n  doc-216 CHECK: realistic_settle close={'OK' if realistic['close_succeeded'] else 'FAILED'} "
          f"(must be OK) · never_settles survived={never['survived']} (must be True: clean fail, not naked)")

    if args.json:
        Path(args.json).write_text(json.dumps(
            {"scenarios": results, "survived": survived, "total": len(SCENARIOS),
             "wrapper_bugs": [r["scenario"] for r in wrapper_bugs],
             "architectural_flags": [r["scenario"] for r in arch_flagged]},
            indent=2), encoding="utf-8")
        print(f"\n  JSON -> {args.json}")
    # PASS iff there are no WRAPPER bugs (architectural flags are expected until the
    # EOD-timing fix lands; they point AT that fix, they're not close-code defects).
    return 0 if not wrapper_bugs else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    raise SystemExit(asyncio.run(main()))
