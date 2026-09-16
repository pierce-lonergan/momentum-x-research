"""DOC 288 — the ts_et UTC-mislabel GUARD (the audit's #1 systemic finding).

`trades_v1.ts_et` is a timestamp[us,tz=UTC] column that stores the WRONG instant (~+240 min off true
sip_timestamp), so any wall-clock time-of-day windowing off ts_et silently selects ~05:30 pre-dawn instead
of the 09:30 open. It broke doc-242/244, was fixed in doc-245 (derive ET from sip_timestamp), and was then
RE-INTRODUCED in v6_microstructure_pack.py. This guard makes the next re-introduction loud.

Heuristic: a line is SUSPECT if it extracts wall-clock hour/minute from `ts_et` (ts_et.hour / .dt.hour /
EXTRACT(hour FROM ts_et) / ts_et.dt.hour*60) AND its file neither (a) derives from `sip_timestamp` nor
(b) converts UTC->ET via tz_convert/tz_localize. Order-bucketed windows (ROWS BETWEEN ... PRECEDING/FOLLOWING)
are NOT wall-clock and are ignored. Run standalone for a report; the pytest test pins the SUSPECT set so a
NEW occurrence fails CI.
"""
from __future__ import annotations
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = [_ROOT / "scripts", _ROOT / "src"]

# wall-clock extraction of ts_et (NOT order-bucketed ROWS windows, NOT ::DATE casts)
_WALLCLOCK = re.compile(
    r"ts_et\s*\.\s*(dt\s*\.\s*)?hour"           # ts_et.hour / ts_et.dt.hour
    r"|ts_et\s*\.\s*(dt\s*\.\s*)?minute"
    r"|EXTRACT\s*\(\s*hour\s+FROM\s+[\w.]*ts_et"  # EXTRACT(hour FROM ts_et)
    r"|EXTRACT\s*\(\s*minute\s+FROM\s+[\w.]*ts_et",
    re.IGNORECASE)


def scan():
    suspect, safe = [], []
    for d in SCAN_DIRS:
        if not d.exists():
            continue
        for fp in d.rglob("*.py"):
            text = fp.read_text(encoding="utf-8", errors="replace")
            file_safe = ("sip_timestamp" in text) or ("tz_convert" in text) or ("tz_localize" in text)
            for i, line in enumerate(text.splitlines(), 1):
                if line.lstrip().startswith("#"):
                    continue
                if _WALLCLOCK.search(line):
                    rel = str(fp.relative_to(_ROOT)).replace("\\", "/")
                    (safe if file_safe else suspect).append(f"{rel}:{i}")
    return sorted(set(suspect)), sorted(set(safe))


def main():
    suspect, safe = scan()
    print(f"ts_et wall-clock extraction: {len(suspect)} SUSPECT (no sip_timestamp / no tz_convert in file), "
          f"{len(safe)} safe (file converts UTC->ET or uses sip_timestamp).")
    print("\nSUSPECT (review — may be silently mis-windowing to pre-dawn):")
    for s in suspect:
        print("  ", s)


if __name__ == "__main__":
    main()
