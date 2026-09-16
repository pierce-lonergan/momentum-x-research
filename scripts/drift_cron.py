"""Daily drift cron: run drift detector + alert via Discord webhook on alarm.

Designed to run pre-market every trading day (e.g., 08:00 ET via Task
Scheduler / cron). Wraps scripts/ml_drift_detector.py to:
  1. Compare today's most-recent N-day window vs historical baseline (PSI/KS)
  2. Run Page-Hinkley sequential change-point on per-trade returns
  3. If any alarm fires, post a Discord alert via webhook from .env
  4. Exit code 0 = OK, 1 = ALARM (triggers visibility in scheduler logs)

CONFIG:
  Reads .env for OPS_ALERT_WEBHOOK_URL.
  Falls back to console-only if no webhook.

USAGE:
    # Manual run
    python scripts/drift_cron.py

    # Windows Task Scheduler (XML below):
    #   Trigger: daily 08:00 ET
    #   Action: powershell -File scripts/drift_cron.ps1

    # Cron (Linux/Mac):
    #   0 8 * * 1-5 cd /repo && python scripts/drift_cron.py >> logs/drift_cron.log
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parents[1]
LOGS = REPO / "logs"
MODELS = REPO / "data" / "models"
ENV_PATH = REPO / ".env"


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def load_env_dotfile() -> None:
    """Best-effort load of .env into os.environ."""
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.split("#", 1)[0].strip()
        if k and not os.environ.get(k):
            os.environ[k] = v


def post_discord_alert(webhook_url: str, summary: dict, alarms: list[str]) -> bool:
    """Post a drift alarm to Discord via webhook."""
    n_alerts = len(alarms)
    embed = {
        "title": f"🚨 Drift detector ALARM x{n_alerts}",
        "description": "\n".join(f"- `{a}`" for a in alarms[:10]),
        "color": 0xE74C3C,  # red
        "fields": [
            {"name": "Date", "value": summary.get("date", "?"), "inline": True},
            {"name": "Window (days)", "value":
                f"{summary.get('window_recent_days', '?')} vs "
                f"{summary.get('window_baseline_days', '?')}",
             "inline": True},
            {"name": "PSI threshold", "value":
                f"{summary.get('psi_threshold', '?')}", "inline": True},
        ],
        "footer": {"text": "Action: review feature distributions; "
                            "consider model retrain if persistent"},
    }
    try:
        r = httpx.post(webhook_url, json={
            "username": "Drift Cron",
            "embeds": [embed],
        }, timeout=10.0)
        r.raise_for_status()
        return True
    except Exception as e:
        print(f"  Discord post failed: {e}")
        return False


def post_discord_ok(webhook_url: str, summary: dict) -> bool:
    """Post an all-clear (compact)."""
    try:
        r = httpx.post(webhook_url, json={
            "username": "Drift Cron",
            "content": (f"✅ Drift OK · {summary.get('date', '?')} · "
                         f"window {summary.get('window_recent_days', '?')}d vs "
                         f"baseline {summary.get('window_baseline_days', '?')}d · "
                         "0 alerts"),
        }, timeout=10.0)
        r.raise_for_status()
        return True
    except Exception:
        return False


def main():
    load_env_dotfile()
    section(f"DRIFT CRON  date={datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    detector = REPO / "scripts" / "ml_drift_detector.py"
    if not detector.exists():
        print(f"  ERROR: {detector} missing")
        return 2

    section("Step 1 - Run drift detector")
    LOGS.mkdir(parents=True, exist_ok=True)
    log_path = LOGS / f"drift_cron_{datetime.now().strftime('%Y%m%d')}.log"
    try:
        r = subprocess.run(
            [sys.executable, str(detector),
             "--window-recent", "30", "--window-baseline", "90"],
            capture_output=True, text=True, timeout=600,
        )
    except Exception as e:
        print(f"  detector subprocess failed: {e}")
        return 2
    log_path.write_text(r.stdout + "\n---STDERR---\n" + r.stderr,
                          encoding="utf-8")
    print(f"  exit={r.returncode}, stdout={len(r.stdout)} chars (log: {log_path})")
    print(r.stdout[-1500:])

    section("Step 2 - Check report file")
    today = datetime.utcnow().strftime("%Y-%m-%d")
    report_path = MODELS / f"drift_report_{today}.json"
    if not report_path.exists():
        print(f"  ERROR: detector ran but {report_path.name} missing")
        return 2
    summary = json.loads(report_path.read_text())
    n_alerts = summary.get("n_drift_alerts", 0)
    alarms = summary.get("alerts", [])
    print(f"  alarms: {n_alerts}")
    if n_alerts > 0:
        for a in alarms:
            print(f"    - {a}")

    section("Step 3 - Discord alert")
    webhook = os.environ.get("OPS_ALERT_WEBHOOK_URL", "").strip()
    if not webhook:
        print(f"  no OPS_ALERT_WEBHOOK_URL set; skipping Discord")
    elif n_alerts > 0:
        if post_discord_alert(webhook, summary, alarms):
            print(f"  posted ALARM to Discord")
    else:
        if post_discord_ok(webhook, summary):
            print(f"  posted OK to Discord")

    return 1 if n_alerts > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
