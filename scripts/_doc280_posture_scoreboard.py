"""DOC 280 SCOREBOARD INSTRUMENT (the honest gamification — mandate §6.6).
There is no P&L flip to celebrate (the posture-flip lever measured NEGATIVE in the current regime), so the durable
deliverable is the INSTRUMENT that tracks the sign session over session: "would HOLDING have beaten the actual exits
this session, and by how much?" It reads negative now and would light up if a rocket-rich regime ever flips holding
positive (doc 280 §6: the hold edge is regime-conditional + currently off).

Reuses the pre-registered counterfactual harness (_doc280_counterfactual). Read-only; appends a scoreboard line to
data/reports/posture_delta_trend.jsonl and prints a leaderboard. STAGED for Pierce to wire into the nightly cycle
(post_close_scorecard.py) — the agent does not touch the running bot's cron.

Usage:  python scripts/_doc280_posture_scoreboard.py            # full history leaderboard
        python scripts/_doc280_posture_scoreboard.py --append   # append the latest session's line to the trend file
"""
from __future__ import annotations
import argparse, json, sys
from collections import defaultdict

# doc 287: this instrument's --append write lives AFTER a leaderboard print that emits U+0394/U+2605. When the
# EOD launcher runs us with a cp1252 stdout, that print raised UnicodeEncodeError and killed the process before
# the write -> posture_delta_trend.jsonl stayed dark for weeks. Harden stdout so a glyph can never pre-empt the
# file append, regardless of how we're invoked (belt-and-suspenders alongside PYTHONIOENCODING on the caller).
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, "scripts")
import _doc280_counterfactual as C

TREND = "data/reports/posture_delta_trend.jsonl"


def per_session_deltas():
    rows = C.measure()
    usable = [r for r in rows if r.get("status") == "OK" and r.get("tier") in ("CONFIRMED", "SIZE_BASED")]
    by = defaultdict(lambda: {"n": 0, "actual_pnl": 0.0, "d_hold_close": 0.0, "d_hold_nextopen": 0.0,
                              "d_hold_stop15": 0.0, "n_confirmed": 0})
    for r in usable:
        s = by[r["session_date"]]
        s["n"] += 1
        s["n_confirmed"] += 1 if r.get("tier") == "CONFIRMED" else 0
        s["actual_pnl"] += r["pnl"]
        for k in ("d_hold_close", "d_hold_nextopen", "d_hold_stop15"):
            s[k] += r.get(k, 0.0)
    return dict(sorted(by.items()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--append", action="store_true", help="append the latest session line to the trend file")
    args = ap.parse_args()
    sessions = per_session_deltas()
    if not sessions:
        print("no reconstructable sessions"); return
    # leaderboard: cumulative "hold would have added" vs actual, with a beat-your-own-best marker on the best session
    cum_close = 0.0
    best = max(sessions.items(), key=lambda kv: kv[1]["d_hold_close"])
    print("=== POSTURE SCOREBOARD — would HOLDING have beaten the actual exits? (best-arm = hold-to-close) ===")
    print(f"{'session':>11} {'n':>3} {'actual$':>9} {'Δhold_close':>11} {'Δnext_open':>11} {'cum Δclose':>11} {'verdict':>8}")
    for d, s in sessions.items():
        cum_close += s["d_hold_close"]
        star = " ★BEST" if d == best[0] else ""
        verdict = "HOLD+" if s["d_hold_close"] > 0 else "exit+"
        print(f"{d:>11} {s['n']:>3} {s['actual_pnl']:>+9,.0f} {s['d_hold_close']:>+11,.0f} {s['d_hold_nextopen']:>+11,.0f} {cum_close:>+11,.0f} {verdict:>8}{star}")
    n_hold = sum(1 for s in sessions.values() if s["d_hold_close"] > 0)
    tot_close = sum(s["d_hold_close"] for s in sessions.values())
    tot_actual = sum(s["actual_pnl"] for s in sessions.values())
    print(f"\nBASELINE (the score to beat): reconciled book ${tot_actual:+,.0f}  (full clean book -$22,961)")
    print(f"CURRENT VERDICT: holding would have {'ADDED' if tot_close>0 else 'SUBTRACTED'} ${tot_close:+,.0f} "
          f"across {len(sessions)} sessions; HOLD beat exits in only {n_hold}/{len(sessions)} sessions.")
    print(f"  -> the flag stays OFF. Instrument lights up (ships the hold flag) only when this cum-Δclose turns "
          f"durably positive (a rocket-rich regime). Beat-your-own-best session: {best[0]} (Δclose ${best[1]['d_hold_close']:+,.0f}).")
    if args.append:
        last = list(sessions.items())[-1]
        line = {"session_date": last[0], "n_trades": last[1]["n"], "actual_pnl": round(last[1]["actual_pnl"], 2),
                "hold_close_delta": round(last[1]["d_hold_close"], 2),
                "hold_nextopen_delta": round(last[1]["d_hold_nextopen"], 2),
                "hold_beats_exits": last[1]["d_hold_close"] > 0, "metric": "doc280_posture_delta"}
        with open(TREND, "a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")
        print(f"\nappended latest session -> {TREND}: {json.dumps(line)}")


if __name__ == "__main__":
    main()
