"""doc 269 B3 - NIGHTLY PIPELINE HEALTH CHECK. One line answers "did everything run?" (DORMANT-C, read-only,
never raises out). Registry-as-code below = the source of truth for the fleet (census doc 269). Posts ONE line:
  PIPELINES 9/9 GREEN | outputs 6/6 fresh | CRIT incidents 0 | disk 412GB free
or names the reds. Runs from run_shadow_grader.ps1 @ ~19:31 (no new elevated task needed).

Usage: python scripts/pipeline_health_check.py [--discord] [--date YYYY-MM-DD]
"""
from __future__ import annotations
import os, sys, json, glob, argparse, subprocess, datetime as dt, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def P(*a): return os.path.join(ROOT, *a)

# doc 272 A2: synthetic-incident namespace centralized in src/ops/synthetics.py
# (shared with scripts/incident_pager.py). Fallback keeps DORMANT-C never-raises.
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
try:
    from src.ops.synthetics import is_synthetic_incident
except Exception:
    _SYN_FALLBACK = frozenset({"X", "TEST", "AAPL", "TSLA"})
    def is_synthetic_incident(row):
        try:
            return (str(row.get("ticker") or "").upper() in _SYN_FALLBACK
                    or row.get("synthetic") is True
                    or (isinstance(row.get("context"), dict)
                        and row["context"].get("synthetic") is True))
        except Exception:
            return False

# ---- THE REGISTRY (verified census, doc 269; update when the fleet changes) ----
TASKS = [  # (task name, must-run-daily-on-trading-days)
    ("MomentumX-PaperTrading", True), ("MomentumX-Lottery", True), ("MomentumX-FaderShort", True),
    ("MomentumX-Watchdog", True), ("MomentumX-DataIngest", True), ("MomentumX-Operator", True),
    ("MomentumX-RocketShadow", True), ("MomentumX-RocketWatchlist", True), ("MomentumX-ShadowGrader", True),
]
def OUTPUTS(date):  # (label, path, max-age-hours from now)
    return [
        ("eod", P("data", "reports", f"eod_{date}.json"), 8),
        ("lake", P("data", "polygon_warehouse", "derived", "aftermath_strat.parquet"), 30),
        ("raw_fills", P("data", "ops", f"raw_fills_{date}.jsonl"), 12),
        ("vll_trace", P("data", "ops", f"verdict_trace_{date}.jsonl"), 16),
        ("watchlist", P("data", "research", "rocket_watchlist_log.jsonl"), 30),
        ("grader", P("data", "reports", f"shadow_vs_prod_{date}.txt"), 6),
    ]

def task_states():
    ps = ("$r=@(); foreach($t in Get-ScheduledTask -TaskName 'MomentumX-*'){ $i=$t|Get-ScheduledTaskInfo; "
          "$r += [pscustomobject]@{n=$t.TaskName; lr=$i.LastRunTime.ToString('yyyy-MM-dd HH:mm'); rc=$i.LastTaskResult} }; "
          "$r | ConvertTo-Json -Compress")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=60).stdout.strip()
        rows = json.loads(out)
        if isinstance(rows, dict): rows = [rows]
        return {r["n"]: (r["lr"], int(r["rc"])) for r in rows}
    except Exception as e:
        return {"_error": (str(e)[:80], -1)}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=dt.date.today().isoformat())
    ap.add_argument("--discord", action="store_true")
    a = ap.parse_args()
    date = a.date
    is_weekend = dt.date.fromisoformat(date).weekday() >= 5
    reds, notes = [], []

    st = task_states()
    if "_error" in st:
        reds.append(f"scheduler-query-failed({st['_error'][0][:40]})")
        green_tasks = 0
    else:
        green_tasks = 0
        for name, _ in TASKS:
            lr, rc = st.get(name, ("missing", -1))
            ran_today = lr.startswith(date)
            if rc == 0 and (ran_today or is_weekend):
                green_tasks += 1
            else:
                reds.append(f"{name.replace('MomentumX-','')}(rc={rc},last={lr})")

    fresh = 0
    outs = OUTPUTS(date)
    if not is_weekend:
        now = dt.datetime.now().timestamp()
        for label, path, max_h in outs:
            if os.path.exists(path) and (now - os.path.getmtime(path)) < max_h * 3600:
                fresh += 1
            else:
                reds.append(f"stale:{label}")

    crit = 0
    inc = P("data", "ops", f"incidents_{date}.jsonl")
    if os.path.exists(inc):
        for l in open(inc, encoding="utf-8", errors="replace"):
            try:
                e = json.loads(l)
                # exclude synthetic escalations (Adversary battle ticker X, pager self-tests
                # ticker TEST, fixtures) — namespace centralized in src/ops/synthetics.py
                if (str(e.get("severity", "")).upper() == "CRITICAL"
                        and not is_synthetic_incident(e)):
                    crit += 1
            except Exception:
                continue
    if crit: reds.append(f"CRIT-incidents:{crit}")

    free_gb = -1
    try:
        import shutil
        free_gb = shutil.disk_usage(ROOT).free // (1024 ** 3)
        if free_gb < 25: reds.append(f"disk-low:{free_gb}GB")
    except Exception:
        pass

    status = "GREEN" if not reds else "RED"
    line = (f"PIPELINES {green_tasks}/{len(TASKS)} | outputs {fresh}/{len(outs)} fresh | "
            f"CRIT {crit} | disk {free_gb}GB | {status}"
            + ("" if not reds else " — " + "; ".join(reds[:6])))
    print(line)
    try:
        os.makedirs(P("data", "ops"), exist_ok=True)
        json.dump({"date": date, "line": line, "reds": reds},
                  open(P("data", "ops", f"pipeline_health_{date}.json"), "w", encoding="utf-8"))
    except Exception:
        pass
    if a.discord:
        wh = None
        for f in [os.path.expanduser("~/momentum-x-secrets.env"), P(".env")]:
            if os.path.exists(f):
                for l in open(f, encoding="utf-8", errors="replace"):
                    if l.startswith("OPS_ALERT_WEBHOOK_URL="):
                        wh = l.split("=", 1)[1].strip().strip('"').strip("'"); break
            if wh: break
        if wh:
            try:
                emoji = ":white_check_mark:" if status == "GREEN" else ":rotating_light:"
                body = json.dumps({"content": f"{emoji} `{line}`"}).encode()
                urllib.request.urlopen(urllib.request.Request(
                    wh, data=body, method="POST",
                    headers={"Content-Type": "application/json", "User-Agent": "MomentumX-Health/1.0"}), timeout=15)
            except Exception as e:
                print(f"[discord failed: {str(e)[:60]}]")

if __name__ == "__main__":
    main()
