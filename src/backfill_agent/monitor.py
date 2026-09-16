"""Status reporter — read-only, fast (<1s), no DB locks held.

Reads heartbeat.json + queue.db (read-only connection). Prints a one-screen
report. Designed to be runnable from any shell at 7:30 AM in one line.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.backfill_agent.state import WorkQueue

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_HEARTBEAT_PATH = _PROJECT_ROOT / "data" / "backfill_agent" / "heartbeat.json"


def _format_eta(seconds: float) -> str:
    if seconds == float("inf"):
        return "infinite (no throughput)"
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds//60)}m"
    if seconds < 86400:
        return f"{seconds/3600:.1f}h"
    return f"{seconds/86400:.1f}d"


def render_status() -> str:
    lines = []
    now = datetime.now(timezone.utc)

    # Heartbeat
    if _HEARTBEAT_PATH.exists():
        try:
            hb = json.loads(_HEARTBEAT_PATH.read_text(encoding="utf-8"))
            ts = datetime.fromisoformat(hb["timestamp"].replace("Z", "+00:00"))
            stale_secs = (now - ts).total_seconds()
            stale_warn = "  [!] STALE" if stale_secs > 120 else ""
            lines.append(f"Heartbeat: {hb['timestamp']}  (age {int(stale_secs)}s){stale_warn}")
            lines.append(f"  started_at: {hb.get('started_at','?')}  "
                         f"workers={hb.get('n_workers','?')}  "
                         f"in_flight={hb.get('in_flight','?')}  "
                         f"completed_this_run={hb.get('completed_this_run','?')}")
            if hb.get("dry_run"):
                lines.append("  [!] DRY-RUN MODE")
            if hb.get("shutdown_requested"):
                lines.append("  [!] SHUTDOWN REQUESTED")
            if hb.get("paused"):
                lines.append("  [!] PAUSED (sentinel: data/backfill_agent/PAUSED)")
            budget = hb.get("budget", {})
            lines.append(f"  budget: window={budget.get('window','?')} "
                         f"rate={budget.get('rate_per_min','?')}/min "
                         f"et_time={budget.get('et_time','?')}")
        except Exception as e:
            lines.append(f"Heartbeat: ERROR reading: {e}")
    else:
        lines.append("Heartbeat: NOT FOUND (daemon not running?)")

    # Queue stats — fresh read (don't trust heartbeat snapshot for current count)
    try:
        q = WorkQueue()
        stats = q.stats()
        counts = stats["counts"]
        lines.append("")
        lines.append(f"Queue:")
        for status in ("pending", "in_progress", "completed", "failed_transient",
                       "failed_permanent", "blocked"):
            n = counts.get(status, 0)
            if n > 0 or status in ("pending", "completed"):
                lines.append(f"  {status:>20s}: {n:>6,}")
        lines.append(f"  throughput_1h:  {stats['throughput_1h']:>6,}")
        lines.append(f"  throughput_24h: {stats['throughput_24h']:>6,}")
        lines.append(f"  ETA at current rate: {_format_eta(stats['eta_seconds'])}")

        # Recent errors
        errs = stats.get("recent_errors", [])
        if errs:
            lines.append("")
            lines.append(f"Recent errors (last {len(errs)}):")
            for e in errs[:10]:
                ts = (e.get("last_attempt_at") or "")[:19]
                lines.append(f"  {ts} {e['ticker']:>6s} {e['date']}  {(e.get('last_error') or '')[:80]}")
    except Exception as e:
        lines.append(f"Queue: ERROR reading: {e}")

    return "\n".join(lines)


def main() -> int:
    print(render_status())
    return 0


if __name__ == "__main__":
    sys.exit(main())
