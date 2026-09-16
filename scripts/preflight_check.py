"""
D218: Pre-flight health check — minimal version for Monday's first clean session.

Runs before the trading system starts. Fails fast if critical dependencies
are unreachable, preventing a wasted session that hangs or crashes silently.

4 checks, each with a clear pass/fail:
1. Alpaca API reachable (get_account succeeds, equity > 0)
2. Finnhub API reachable (one earnings calendar call succeeds)
3. Disk space > 500MB in the data directory
4. Heartbeat file is writable (test write + verify)

Exit code 0 = all checks pass. Exit code 1 = at least one check failed.
Called by daily_paper_trade.ps1 before launching the trading system.

Usage:
    python scripts/preflight_check.py [--webhook URL]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))


def _alert_webhook(url: str, message: str) -> None:
    """Send a Discord/Slack webhook alert. Best-effort, never raises."""
    if not url:
        return
    try:
        from src.monitoring.alerts import alert_critical_sync
        alert_critical_sync(message, webhook_url=url)
    except Exception:
        # Fallback if src.monitoring.alerts can't be imported
        try:
            import httpx
            httpx.post(url, json={"content": f"[MomentumX Preflight] {message}"}, timeout=5)
        except Exception:
            pass


def check_alpaca() -> tuple[bool, str]:
    """Check 1: Alpaca API connectivity and account health.

    doc 287: a single 10s morning API blip used to FATAL-abort the whole session — it cost 2 of 4
    trading days the week of 2026-07-06 (7/8 + 7/10, 'Alpaca check failed: timed out'). Uptime is
    the #1 lever for a daily-compounding goal, so:
      - RETRY transient network failures (3 attempts, 15s each, 2s/5s backoff), and
      - if still unreachable, START DEGRADED-WITH-ALARM (return True + Discord alert) instead of
        aborting — the running bot's D79 clock-retry + alpaca_rest circuit breaker reconnect on
        their own, and no order can fill while the API is down, so degraded-start is strictly safer
        than losing the session.
    REAL problems still BLOCK: missing/bad keys, auth 401/403, equity<=0, non-ACTIVE status, and any
    non-transport (unexpected) error."""
    try:
        from dotenv import load_dotenv
        load_dotenv(_project_root / ".env", override=False)

        api_key = os.environ.get("ALPACA_API_KEY", "")
        secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
        base_url = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

        if not api_key or not secret_key:
            return False, "ALPACA_API_KEY or ALPACA_SECRET_KEY not set in .env"

        import time

        import httpx

        headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": secret_key}
        backoffs = (2.0, 5.0, 0.0)  # sleep after attempts 0 and 1; none after the last
        last_err = None
        for attempt in range(3):
            try:
                resp = httpx.get(f"{base_url}/v2/account", headers=headers, timeout=15)
                if resp.status_code in (401, 403):
                    return False, f"Alpaca auth failed (HTTP {resp.status_code}) — check API keys"
                if resp.status_code != 200:
                    last_err = f"HTTP {resp.status_code}"
                    if backoffs[attempt]:
                        time.sleep(backoffs[attempt])
                    continue
                account = resp.json()
                equity = float(account.get("equity", 0))
                status = account.get("status", "unknown")
                if equity <= 0:
                    return False, f"Alpaca equity is ${equity:.2f} (must be > 0)"
                if status != "ACTIVE":
                    return False, f"Alpaca account status is {status} (must be ACTIVE)"
                mode = "paper" if "paper" in base_url else "LIVE"
                extra = f" (recovered on retry {attempt + 1})" if attempt else ""
                return True, f"Alpaca OK: equity=${equity:,.2f}, status={status}, mode={mode}{extra}"
            except httpx.TransportError as e:  # timeouts + connect/network errors — transient
                last_err = f"{type(e).__name__}: {e}"
                if backoffs[attempt]:
                    time.sleep(backoffs[attempt])

        # all 3 attempts exhausted on TRANSIENT failures -> DEGRADED START (non-blocking) + loud alarm.
        msg = (f"[DEGRADED-START] Alpaca unreachable after 3 retries ({last_err}); starting the bot "
               f"ANYWAY — D79/circuit-breaker resilience reconnects. doc 287: a transient network blip "
               f"must not cost a trading day.")
        try:
            from config.settings import Settings as _S
            _wh = _S().ops.alert_webhook_url
            if _wh:
                _alert_webhook(_wh, "PREFLIGHT DEGRADED-START: " + msg)
        except Exception:
            pass
        return True, msg

    except ImportError as e:
        return False, f"Missing dependency: {e}"
    except Exception as e:
        return False, f"Alpaca check failed (non-transient): {e}"


def check_finnhub() -> tuple[bool, str]:
    """Check 2: Finnhub API connectivity."""
    try:
        api_key = os.environ.get("FINNHUB_API_KEY", "")
        if not api_key:
            return False, "FINNHUB_API_KEY not set in .env"

        import httpx
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        resp = httpx.get(
            "https://finnhub.io/api/v1/calendar/earnings",
            params={"from": today, "to": today, "token": api_key},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            count = len(data.get("earningsCalendar", []))
            return True, f"Finnhub OK: {count} earnings events for {today}"
        elif resp.status_code == 401:
            return False, "Finnhub API key invalid (HTTP 401)"
        else:
            return False, f"Finnhub returned HTTP {resp.status_code}"

    except Exception as e:
        return False, f"Finnhub check failed: {e}"


def check_disk_space() -> tuple[bool, str]:
    """Check 3: Sufficient disk space for data directory."""
    try:
        data_dir = _project_root / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        usage = shutil.disk_usage(str(data_dir))
        free_mb = usage.free / (1024 * 1024)
        free_gb = free_mb / 1024

        if free_mb < 500:
            return False, f"Disk space critically low: {free_mb:.0f}MB free (need > 500MB)"

        return True, f"Disk OK: {free_gb:.1f}GB free"

    except Exception as e:
        return False, f"Disk check failed: {e}"


def check_halt_switch() -> tuple[bool, str]:
    """2026-05-12: D277 HALT_NEW_ENTRIES sentinel check.

    Pre-flight surfaces the halt switch state EARLY so an operator
    sees "BOT WILL REFUSE TRADES" instead of discovering 4 hours later
    via 0 fills (as happened 2026-05-11/12 -- 8 valid entries blocked
    silently while operator thought trades would happen).

    Returns FAIL when MOMENTUM_HALT_NEW_ENTRIES is set (any of
    1/true/yes/on) OR when settings.execution.halt_new_entries is True.
    Bot will still START, this is just a fail-loud warning so the
    operator can decide to lift it before market open.
    """
    halt_env = os.environ.get("MOMENTUM_HALT_NEW_ENTRIES", "").strip().lower()
    halt_via_env = halt_env in {"1", "true", "yes", "on"}

    halt_via_config = False
    try:
        from config.settings import Settings
        halt_via_config = bool(getattr(Settings().execution, "halt_new_entries", False))
    except Exception:
        pass

    if halt_via_env or halt_via_config:
        sources = []
        if halt_via_env:
            sources.append(f"MOMENTUM_HALT_NEW_ENTRIES env var = '{halt_env}'")
        if halt_via_config:
            sources.append("settings.execution.halt_new_entries = True in config")
        return False, (
            "HALT SWITCH IS ON -- bot will refuse ALL new OTO entries today. "
            "Source(s): " + "; ".join(sources) +
            ". To lift: [Environment]::SetEnvironmentVariable("
            "'MOMENTUM_HALT_NEW_ENTRIES', $null, 'User') AND restart bot."
        )
    return True, "Halt switch OFF (bot can submit new entries)"


def check_heartbeat_writable() -> tuple[bool, str]:
    """Check 4: Heartbeat file can be written and read back."""
    try:
        hb_path = _project_root / "data" / "heartbeat.json"
        hb_path.parent.mkdir(parents=True, exist_ok=True)

        # Write test payload
        test_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "phase": "PREFLIGHT_CHECK",
            "last_function": "preflight_check.py",
            "pulse_count": 0,
            "positions": 0,
            "trades_today": 0,
            "pid": os.getpid(),
        }
        tmp_path = str(hb_path) + ".preflight_tmp"
        with open(tmp_path, "w") as f:
            json.dump(test_data, f)
        os.replace(tmp_path, str(hb_path))

        # Read back and verify
        with open(str(hb_path)) as f:
            readback = json.load(f)

        if readback.get("phase") != "PREFLIGHT_CHECK":
            return False, "Heartbeat read-back mismatch"

        return True, f"Heartbeat OK: writable at {hb_path}"

    except Exception as e:
        return False, f"Heartbeat write check failed: {e}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-flight health check")
    parser.add_argument("--webhook", type=str, default="",
                        help="Discord/Slack webhook URL for failure alerts")
    parser.add_argument("--test-alert", action="store_true",
                        help="Send a test alert and exit (verifies webhook works)")
    args = parser.parse_args()

    # Load webhook from config if not provided via CLI
    if not args.webhook:
        try:
            from config.settings import Settings
            args.webhook = Settings().ops.alert_webhook_url
        except Exception:
            pass

    if args.test_alert:
        if not args.webhook:
            print("ERROR: No webhook URL. Set OPS_ALERT_WEBHOOK_URL in .env or pass --webhook URL")
            return 1
        print(f"Sending test alert to {args.webhook[:30]}...")
        _alert_webhook(args.webhook, "Test alert from preflight_check.py — if you see this, alerts are working.")
        print("Test alert sent. Check your Discord/Slack.")
        return 0

    print(f"{'=' * 60}")
    print(f"  MOMENTUM-X PRE-FLIGHT CHECK — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 60}")

    # 2026-05-27: separate BLOCKING (fail = abort boot) from ADVISORY
    # (fail = warn but proceed). Finnhub is news-only enrichment with
    # graceful fallback in agents; a transient timeout shouldn't prevent
    # the bot from booting and protecting open positions. Caught live
    # 2026-05-27 06:41 ET when 3 retries of the launcher all failed on
    # Finnhub timeout while curl confirmed Finnhub was responding at
    # 200ms latency. Filed `Preflight_finnhub_advisory_not_blocking`.
    blocking = [
        ("Alpaca API", check_alpaca),       # broker connectivity is non-negotiable
        ("Disk Space", check_disk_space),   # full disk == journal corruption
        ("Heartbeat File", check_heartbeat_writable),  # watchdog signal
        ("Halt Switch", check_halt_switch), # operator-set kill switch
    ]
    advisory = [
        ("Finnhub API", check_finnhub),     # news-only, has graceful fallback
    ]

    results = []
    for name, check_fn in blocking:
        passed, message = check_fn()
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}: {message}")
        results.append((name, passed, message, True))  # is_blocking=True
    for name, check_fn in advisory:
        passed, message = check_fn()
        status = "PASS" if passed else "WARN"
        print(f"  [{status}] {name}: {message} (advisory only, "
              f"non-blocking)")
        results.append((name, passed, message, False))  # is_blocking=False

    print(f"{'=' * 60}")

    # Only blocking failures count toward abort decision
    failures = [(name, msg) for name, passed, msg, is_blocking in results
                if not passed and is_blocking]
    advisory_failures = [(name, msg) for name, passed, msg, is_blocking in results
                         if not passed and not is_blocking]
    if advisory_failures:
        adv_summary = "; ".join(f"{name}: {msg}" for name, msg in advisory_failures)
        print(f"  ADVISORY WARNINGS (non-blocking): {adv_summary}")

    if failures:
        fail_summary = "; ".join(f"{name}: {msg}" for name, msg in failures)
        print(f"\n  PREFLIGHT FAILED — {len(failures)} BLOCKING check(s) failed")
        print(f"  System will NOT start. Fix the issues above and retry.\n")
        _alert_webhook(args.webhook, f"PREFLIGHT FAILED: {fail_summary}")
        return 1
    else:
        total = len(blocking) + len(advisory)
        print(f"\n  PREFLIGHT PASSED — all {len(blocking)} blocking checks OK"
              f" ({len(advisory_failures)} advisory warning(s) deferred)")
        print(f"  System is cleared to start.\n")
        return 0


if __name__ == "__main__":
    sys.exit(main())
