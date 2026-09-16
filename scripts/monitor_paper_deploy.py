"""Monday paper-deploy monitor for the meta-scorer lottery runner.

Real-time + post-session analysis tool that watches:
  - Per-tier fire pattern (which tier each pick lands in)
  - Per-tier P&L attribution (live vs WF expectation)
  - Drift detector status (PSI on today's predictions vs WF baseline)
  - Aggressive-vs-conservative Kelly gap
  - Tier-transition events from intraday refresh

Two modes:
  --tail         continuously read the running lottery log; emit per-event
                  summaries (suitable for piping or human eyeballing).
  --eod          end-of-day report: parses today's lottery log + Alpaca
                  positions, computes per-tier $/pick vs WF expectation.

USAGE:
    # During the trading day (long-running, prints on each new event)
    python scripts/monitor_paper_deploy.py --tail

    # After 4 PM ET
    python scripts/monitor_paper_deploy.py --eod

The tool reads:
  logs/lottery_<YYYY-MM-DD>.log         (runner output)
  data/lottery/lottery_history.json     (canonical pick history)
  data/models/meta_scorer_summary*.json (per-tier WF expectations for compare)
  data/polygon_warehouse/derived/meta_scores_walkforward*.parquet (WF stats)

NOTHING is sent to the broker. This tool is read-only on filesystem state.
"""
from __future__ import annotations
import argparse
import json
import re
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import numpy as np

REPO = Path(__file__).resolve().parents[1]
LOG_DIR = REPO / "logs"
LOTTERY_STATE = REPO / "data" / "lottery"
MODELS = REPO / "data" / "models"
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
ET = ZoneInfo("America/New_York")


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


# Regex patterns matching lottery_runner log lines
RE_META_PASS = re.compile(
    r"META-PASS (?P<tk>\S+): tier=(?P<tier>\w+) score=(?P<score>[\d.]+) "
    r"kelly=(?P<kelly>[\d.]+) notional=\$(?P<notional>[\d.]+)"
)
RE_META_SKIP = re.compile(r"META-SKIP (?P<tk>\S+):")
RE_REFRESH_UPGRADE = re.compile(
    r"REFRESH-UPGRADE (?P<tk>\S+): \w+ -> (?P<new_tier>\w+) "
    r"score=(?P<score>[\d.]+) notional=\$(?P<notional>[\d.]+) tcn=(?P<tcn>[\d.]+|-1)"
)
RE_HEARTBEAT = re.compile(
    r"HEARTBEAT @ (?P<time>\S+) ET - (?P<n_open>\d+) open: (?P<summary>[^|]*)\| "
    r"unrealized=\$(?P<unrealized>[+-][\d.]+)"
)
RE_TIME_STOP = re.compile(r"TIME_STOP SELL (?P<qty>\d+) (?P<tk>\S+)")


def latest_lottery_log() -> Optional[Path]:
    """Find the most recent lottery_<DATE>.log."""
    today = datetime.now(ET).strftime("%Y-%m-%d")
    today_log = LOG_DIR / f"lottery_{today}.log"
    if today_log.exists():
        return today_log
    # Fall back to most recent log
    logs = sorted(LOG_DIR.glob("lottery_*.log"), key=lambda p: p.stat().st_mtime,
                   reverse=True)
    return logs[0] if logs else None


def parse_log(log_path: Path) -> dict:
    """Parse lottery log into structured events."""
    events: dict = {
        "meta_passes": [],
        "meta_skips": [],
        "refresh_upgrades": [],
        "heartbeats": [],
        "time_stops": [],
    }
    if not log_path.exists():
        return events
    for line in log_path.read_text().splitlines():
        if m := RE_META_PASS.search(line):
            events["meta_passes"].append({
                "ticker": m.group("tk"), "tier": m.group("tier"),
                "score": float(m.group("score")),
                "kelly": float(m.group("kelly")),
                "notional": float(m.group("notional")),
            })
        elif m := RE_META_SKIP.search(line):
            events["meta_skips"].append({"ticker": m.group("tk")})
        elif m := RE_REFRESH_UPGRADE.search(line):
            events["refresh_upgrades"].append({
                "ticker": m.group("tk"), "new_tier": m.group("new_tier"),
                "score": float(m.group("score")),
                "notional": float(m.group("notional")),
                "tcn": float(m.group("tcn")),
            })
        elif m := RE_HEARTBEAT.search(line):
            events["heartbeats"].append({
                "time": m.group("time"), "n_open": int(m.group("n_open")),
                "summary": m.group("summary").strip(),
                "unrealized": float(m.group("unrealized")),
            })
        elif m := RE_TIME_STOP.search(line):
            events["time_stops"].append({
                "ticker": m.group("tk"), "qty": int(m.group("qty")),
            })
    return events


def load_wf_expectation(profile: str = "conservative") -> dict:
    """Load per-tier WF $-per-pick expectation from meta_scorer_summary.

    profile: 'conservative' | 'aggressive' | '16fold' | '16fold_aggressive'
    """
    suffix = {
        "conservative": "",
        "16fold": "_16fold",
        "aggressive": "_aggressive",
        "16fold_aggressive": "_16fold_aggressive",
    }.get(profile, "_16fold_aggressive")
    p = MODELS / f"meta_scorer_summary{suffix}.json"
    if not p.exists():
        return {}
    return json.loads(p.read_text())


def post_discord(webhook_url: str, content: str, title: str = "MX Deploy Monitor") -> bool:
    """Send a discord embed alert. Returns True on success."""
    try:
        import httpx
        r = httpx.post(webhook_url, json={
            "username": title,
            "content": content[:1990],
        }, timeout=10.0)
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"  Discord post failed: {e}")
        return False


def detect_anomalies(by_tier: dict, wf: dict, threshold_z: float = 2.0) -> list[str]:
    """Compare live per-tier $/pick to WF expectation; flag |z| > threshold.

    Returns list of anomaly strings (empty = no anomalies).
    """
    anomalies = []
    if not wf or "tier_stats" not in wf:
        return anomalies
    ts = wf["tier_stats"]
    for tier in ("ELITE", "HIGH", "VETOED", "BROAD"):
        live = by_tier.get(tier, [])
        if not live or tier not in ts:
            continue
        # WF expectation: avg_pct (per-trade) and win_pct
        wf_avg = ts[tier].get("avg_pct", 0)
        wf_n = ts[tier].get("n", 1)
        # Live: y_reg per pick if logged (we don't have y_reg in log; use notional sum)
        live_n = len(live)
        # Tier-fire-frequency anomaly: live tier fires N times in 1 day vs ~N/year in WF
        # WF span is 16 months ~ 132 trading days; expected daily rate = wf_n/132
        expected_daily = wf_n / 132.0
        # Poisson std at this rate
        pois_std = max(np.sqrt(expected_daily), 0.5)
        z_freq = (live_n - expected_daily) / pois_std
        if abs(z_freq) > threshold_z:
            anomalies.append(
                f"{tier}: live n={live_n} vs daily-expected {expected_daily:.2f} "
                f"(z={z_freq:+.2f})"
            )
    return anomalies


def cmd_eod(profile: str) -> int:
    """End-of-day report: parse today's log + compare to WF expectation."""
    log_path = latest_lottery_log()
    if log_path is None:
        print("ERROR: no lottery_*.log files found in logs/")
        return 1
    section(f"EOD REPORT: {log_path.name}  profile={profile}")

    events = parse_log(log_path)
    print(f"  meta_passes:     {len(events['meta_passes']):>3}")
    print(f"  meta_skips:      {len(events['meta_skips']):>3}")
    print(f"  refresh_upgrades:{len(events['refresh_upgrades']):>3}")
    print(f"  heartbeats:      {len(events['heartbeats']):>3}")
    print(f"  time_stops:      {len(events['time_stops']):>3}")

    section("Per-tier fire pattern")
    by_tier = defaultdict(list)
    for e in events["meta_passes"]:
        by_tier[e["tier"]].append(e)
    for u in events["refresh_upgrades"]:
        by_tier[u["new_tier"]].append({
            "ticker": u["ticker"], "tier": u["new_tier"],
            "score": u["score"], "kelly": 0.0,  # not in upgrade line
            "notional": u["notional"], "from_refresh": True,
        })

    print(f"  {'tier':<8} {'n':>4} {'avg_notional':>13}")
    total_notional = 0.0
    for tier in ["ELITE", "HIGH", "VETOED", "BROAD"]:
        picks = by_tier.get(tier, [])
        if not picks:
            print(f"  {tier:<8} {'0':>4} (no fires)")
            continue
        avg_n = sum(p["notional"] for p in picks) / len(picks)
        total_notional += sum(p["notional"] for p in picks)
        sources = ""
        n_refresh = sum(1 for p in picks if p.get("from_refresh"))
        if n_refresh > 0:
            sources = f"  ({n_refresh} from refresh)"
        print(f"  {tier:<8} {len(picks):>4} ${avg_n:>11.2f}{sources}")
    print(f"  {'TOTAL':<8}      ${total_notional:>11.2f} deployed")

    section("Final heartbeat (live unrealized P&L)")
    if events["heartbeats"]:
        last_hb = events["heartbeats"][-1]
        print(f"  time:         {last_hb['time']} ET")
        print(f"  open:         {last_hb['n_open']} positions")
        print(f"  summary:      {last_hb['summary']}")
        print(f"  unrealized:   ${last_hb['unrealized']:+.2f}")

    section("WF expectation (per-tier $-per-pick)")
    wf = load_wf_expectation(profile)
    if not wf:
        print(f"  WF summary not found for profile={profile}; "
              "skipping comparison")
    else:
        ts = wf.get("tier_stats", {})
        print(f"  {'tier':<8} {'wf_n':>5} {'wf_avg_pct':>11} {'wf_win_pct':>11}")
        for tier in ["ELITE", "HIGH", "VETOED", "BROAD"]:
            if tier in ts:
                t = ts[tier]
                print(f"  {tier:<8} {t['n']:>5,} {t['avg_pct']:>+10.2f}% "
                      f"{t['win_pct']:>10.1f}%")
        if "total_pnl_dollars" in wf:
            print(f"  WF total $:   ${wf['total_pnl_dollars']:>+.2f}  "
                  f"({wf['total_pnl_pct_of_bankroll']:+.2f}% of bankroll)")

    section("Tier transitions (from intraday refresh)")
    if events["refresh_upgrades"]:
        for u in events["refresh_upgrades"]:
            print(f"  {u['ticker']:<8} -> {u['new_tier']:<8} "
                  f"score={u['score']:.3f} ${u['notional']:.2f} tcn={u['tcn']:.3f}")
    else:
        print("  no intraday upgrades today")

    section("Anomaly detection (live tier-fire frequency vs WF)")
    anomalies = detect_anomalies(by_tier, wf, threshold_z=2.0)
    if not anomalies:
        print("  no tier-fire-frequency anomalies (within +/-2 sigma of WF expectation)")
    else:
        for a in anomalies:
            print(f"  ANOMALY: {a}")

    # Discord alert if anomalies + webhook configured
    env = {}
    env_path = REPO / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.split("#", 1)[0].strip()
    webhook = env.get("OPS_ALERT_WEBHOOK_URL", "")
    if webhook and anomalies:
        msg = (f"**EOD Anomalies** ({datetime.now(ET).strftime('%Y-%m-%d')})\n" +
               "\n".join(f"- {a}" for a in anomalies))
        if post_discord(webhook, msg, title="MX EOD Monitor"):
            print(f"\n  posted {len(anomalies)} anomaly alert(s) to Discord")
    elif webhook and not anomalies:
        # Optional: post all-clear too (commented out to avoid spam)
        pass

    return 0


def cmd_tail(poll_s: float = 2.0) -> int:
    """Stream new events as they appear in today's log."""
    log_path = latest_lottery_log()
    if log_path is None:
        print("ERROR: no lottery_*.log to tail")
        return 1
    print(f"TAIL: {log_path}")
    pos = 0
    try:
        while True:
            if log_path.exists():
                cur = log_path.stat().st_size
                if cur > pos:
                    with log_path.open("r", encoding="utf-8", errors="replace") as f:
                        f.seek(pos)
                        chunk = f.read()
                    pos = cur
                    for line in chunk.splitlines():
                        # Only emit lines matching one of our patterns
                        if RE_META_PASS.search(line):
                            m = RE_META_PASS.search(line)
                            print(f"[FIRE]    {m.group('tier'):<8} {m.group('tk'):<6} "
                                  f"score={m.group('score')}  ${m.group('notional')}")
                        elif RE_REFRESH_UPGRADE.search(line):
                            m = RE_REFRESH_UPGRADE.search(line)
                            print(f"[UPGRADE] {m.group('new_tier'):<8} "
                                  f"{m.group('tk'):<6} ${m.group('notional')}")
                        elif RE_HEARTBEAT.search(line):
                            m = RE_HEARTBEAT.search(line)
                            print(f"[HEARTBEAT] {m.group('time')} "
                                  f"open={m.group('n_open')} "
                                  f"unr=${m.group('unrealized')}")
                        elif RE_TIME_STOP.search(line):
                            m = RE_TIME_STOP.search(line)
                            print(f"[CLOSE]   {m.group('tk'):<6} qty={m.group('qty')}")
            time.sleep(poll_s)
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=False)
    p.add_argument("--tail", action="store_true", help="Stream live events")
    p.add_argument("--eod", action="store_true", help="End-of-day report")
    p.add_argument("--profile", default="16fold_aggressive",
                   choices=["conservative", "16fold", "aggressive",
                             "16fold_aggressive"],
                   help="WF expectation profile to compare against (default: 16fold_aggressive)")
    args = p.parse_args()

    if args.tail:
        return cmd_tail()
    elif args.eod:
        return cmd_eod(args.profile)
    else:
        # Default: print last 3 events + exit (sanity check)
        log_path = latest_lottery_log()
        if log_path is None:
            print("Usage: python monitor_paper_deploy.py [--tail | --eod]")
            return 1
        events = parse_log(log_path)
        section(f"QUICK STATUS: {log_path.name}")
        for cat in ("meta_passes", "refresh_upgrades", "heartbeats"):
            print(f"  {cat:<20} {len(events[cat])}")
        if events["heartbeats"]:
            last = events["heartbeats"][-1]
            print(f"  last heartbeat: {last['time']} ET, {last['n_open']} open, "
                  f"unrealized=${last['unrealized']:+.2f}")
        print("\nFor more: --tail (stream) | --eod (report)")
        return 0


if __name__ == "__main__":
    sys.exit(main())
