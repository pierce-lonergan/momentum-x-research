"""doc 286 (gap #7): tag the 2026-07-06 PHANTOM stop-out rows in trade_results.jsonl as
infrastructure_contaminated (the LFS/APPS pattern), so the book stays honest.

The D98 phantom close at 10:48:51 ET (14:48 UTC) booked fake stop-outs for RIVN and LUCY while the real
positions stayed open at the broker (doc 285 §2 gap #2; the code fix is doc 286 workstream A). The REAL
closes book later (EOD path, ~19:45+ UTC). Discriminator: ticker in {RIVN, LUCY}, session_date 2026-07-06,
exit_time in the 14:40-15:10 UTC window. Runs AFTER the close (scheduled one-shot) so it never races the
live appender. Idempotent; prints exactly what it tagged."""
import json
from datetime import datetime

PATH = "data/trade_results.jsonl"
TICKERS = {"RIVN", "LUCY"}
DATE = "2026-07-06"
LO, HI = "14:40", "15:10"  # UTC window of the phantom bookings

rows = []
tagged = 0
for line in open(PATH, encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    r = json.loads(line)
    try:
        et = str(r.get("exit_time", ""))
        hhmm = et[11:16]
        if (r.get("ticker") in TICKERS and r.get("session_date") == DATE
                and LO <= hhmm <= HI and not r.get("infrastructure_contaminated")):
            r["infrastructure_contaminated"] = True
            r["contamination_reason"] = "doc286: D98 phantom stop-out on breaker-open get_positions (doc 285 gap #2); position was open at broker"
            tagged += 1
            print(f"TAGGED: {r['ticker']} {r['session_date']} exit={et[:19]} pnl={r.get('pnl')}")
    except Exception as e:
        print(f"skip line ({e})")
    rows.append(r)

if tagged:
    with open(PATH, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(f"rewrote {PATH}: {tagged} row(s) tagged")
else:
    print("nothing to tag (already tagged or rows not present)")
