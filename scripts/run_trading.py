#!/usr/bin/env python3
"""
MOMENTUM-X Cross-Platform Trading Launcher

### ARCHITECTURAL CONTEXT
Replaces the Windows-only daily_paper_trade.ps1 with a Python-based launcher
that works on Windows, Linux, and macOS. Can be scheduled via:
- Windows Task Scheduler
- cron (Linux/macOS)
- systemd timer
- Docker/Kubernetes CronJob
- Cloud scheduler (AWS EventBridge, GCP Cloud Scheduler)

### RESPONSIBILITIES
1. Holiday calendar check (NYSE holidays + weekends)
2. Stale process cleanup (lock file + process detection)
3. Pre-flight validation (Python imports, API keys, .env)
4. Session state cleanup (remove yesterday's state)
5. Run paper trading session with logging
6. Lock file management (prevent duplicate instances)

### USAGE
    python scripts/run_trading.py              # Run today if trading day
    python scripts/run_trading.py --force       # Run regardless of calendar
    python scripts/run_trading.py --check-only  # Pre-flight check, no trading

### CRON EXAMPLE (Linux)
    30 3 * * 1-5 cd /opt/momentum-x && python scripts/run_trading.py >> /var/log/momentum-x.log 2>&1
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

# Add project root to path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))


def setup_logging(log_dir: Path) -> Path:
    """Configure logging to file and console."""
    log_dir.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    log_file = log_dir / f"paper_{today}.log"

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(str(log_file), mode="a", encoding="utf-8"),
        ],
    )
    return log_file


def check_trading_day(force: bool = False) -> tuple[bool, str]:
    """Check if today is a valid trading day."""
    from src.scheduling.market_calendar import check_market_open

    should_run, reason = check_market_open()

    if force and not should_run:
        return True, f"FORCED: {reason} (overridden by --force flag)"

    return should_run, reason


def check_lock_file(lock_path: Path) -> bool:
    """Check for and handle stale lock files.

    Returns True if we can proceed, False if another instance is running.
    """
    logger = logging.getLogger(__name__)

    if not lock_path.exists():
        return True

    try:
        pid_str = lock_path.read_text().strip()
        pid = int(pid_str)
    except (ValueError, OSError):
        logger.warning("Corrupt lock file — removing: %s", lock_path)
        lock_path.unlink(missing_ok=True)
        return True

    # Check if process is still running
    try:
        os.kill(pid, 0)  # Signal 0 = check existence
        logger.warning("Another instance is running (PID %d) — exiting", pid)
        return False
    except (OSError, ProcessLookupError):
        logger.warning("Stale lock file (PID %d no longer running) — removing", pid)
        lock_path.unlink(missing_ok=True)
        return True


def write_lock_file(lock_path: Path) -> None:
    """Write current PID to lock file."""
    lock_path.write_text(str(os.getpid()))


def cleanup_stale_state(data_dir: Path) -> None:
    """Remove yesterday's session state for clean start."""
    logger = logging.getLogger(__name__)
    state_file = data_dir / "session_state.json"

    if not state_file.exists():
        return

    try:
        with open(state_file) as f:
            state = json.load(f)
        state_date = state.get("session_date", state.get("date", ""))
        today = date.today().isoformat()

        if state_date != today:
            logger.info("Removing stale session state from %s", state_date)
            state_file.unlink()
    except (json.JSONDecodeError, OSError):
        logger.warning("Removing corrupt session state file")
        state_file.unlink(missing_ok=True)


def preflight_checks() -> tuple[bool, list[str]]:
    """Run pre-flight validation checks.

    Returns (success, list_of_errors).
    """
    errors: list[str] = []

    # Check critical imports
    try:
        import config.settings  # noqa: F401
    except Exception as e:
        errors.append(f"config.settings: {e}")

    try:
        import litellm  # noqa: F401
    except Exception as e:
        errors.append(f"litellm: {e}")

    try:
        import httpx  # noqa: F401
    except Exception as e:
        errors.append(f"httpx: {e}")

    try:
        from dotenv import load_dotenv
        load_dotenv(_PROJECT_ROOT / ".env")
    except Exception as e:
        errors.append(f"dotenv: {e}")

    # Check API keys
    if not os.environ.get("ALPACA_API_KEY"):
        errors.append("ALPACA_API_KEY not set")
    if not os.environ.get("TOGETHER_AI_API_KEY"):
        errors.append("TOGETHER_AI_API_KEY not set")

    return len(errors) == 0, errors


def load_secrets(secrets_path: Path, env_target: Path) -> None:
    """Copy secrets file to .env if it exists."""
    logger = logging.getLogger(__name__)

    if secrets_path.exists():
        import shutil
        shutil.copy2(str(secrets_path), str(env_target))
        logger.info("Secrets loaded from %s", secrets_path)
    elif env_target.exists():
        logger.warning("Secrets file not found (%s) — using existing .env", secrets_path)
    else:
        raise FileNotFoundError(
            f"No secrets file ({secrets_path}) and no .env found — cannot start"
        )


def run_trading_session(project_root: Path) -> int:
    """Run the paper trading session as a subprocess.

    Returns the exit code.
    """
    logger = logging.getLogger(__name__)

    cmd = [sys.executable, "-m", "main", "paper"]
    logger.info("Starting: %s", " ".join(cmd))

    process = subprocess.Popen(
        cmd,
        cwd=str(project_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    # Stream output line by line
    if process.stdout:
        for line in process.stdout:
            line = line.rstrip()
            if line:
                logger.info(line)

    process.wait()
    return process.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Momentum-X Trading Launcher")
    parser.add_argument(
        "--force", action="store_true",
        help="Run even on holidays/weekends",
    )
    parser.add_argument(
        "--check-only", action="store_true",
        help="Run pre-flight checks only, don't trade",
    )
    parser.add_argument(
        "--secrets-file", type=str,
        default=str(Path.home() / "momentum-x-secrets.env"),
        help="Path to secrets .env file",
    )
    args = parser.parse_args()

    log_dir = _PROJECT_ROOT / "logs"
    log_file = setup_logging(log_dir)
    logger = logging.getLogger(__name__)

    logger.info("=" * 60)
    logger.info("MOMENTUM-X Daily Trading Launcher — %s", date.today())
    logger.info("Platform: %s | Python: %s", sys.platform, sys.version.split()[0])
    logger.info("=" * 60)

    # 1. Holiday calendar check
    should_run, reason = check_trading_day(force=args.force)
    logger.info("Calendar check: %s — %s", "RUN" if should_run else "SKIP", reason)
    if not should_run:
        return 0

    # 2. Lock file check
    lock_path = log_dir / "momentum-x.lock"
    if not check_lock_file(lock_path):
        return 0

    # 3. Load secrets
    try:
        load_secrets(
            Path(args.secrets_file),
            _PROJECT_ROOT / ".env",
        )
    except FileNotFoundError as e:
        logger.error("FATAL: %s", e)
        return 1

    # 4. Pre-flight checks
    logger.info("Running pre-flight checks...")
    ok, errors = preflight_checks()
    if not ok:
        logger.error("FATAL: Pre-flight failed: %s", " | ".join(errors))
        return 1
    logger.info("Pre-flight checks passed")

    if args.check_only:
        logger.info("--check-only: pre-flight passed, exiting without trading")
        return 0

    # 5. Clean stale state
    cleanup_stale_state(_PROJECT_ROOT / "data")

    # 6. Write lock file
    write_lock_file(lock_path)

    try:
        # 7. Run trading session
        exit_code = run_trading_session(_PROJECT_ROOT)

        if exit_code == 0:
            logger.info("Paper trading session completed successfully")
        else:
            logger.error("Paper trading exited with code %d", exit_code)

        return exit_code

    finally:
        # Clean up lock file
        lock_path.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
