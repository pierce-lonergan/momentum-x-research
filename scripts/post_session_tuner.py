#!/usr/bin/env python3
"""
D195: Post-Session Auto-Tuner — CLI entry point.

Runs after each trading session to detect parameter drift and generate
tuning recommendations.

Usage:
    # Run after today's session (default: today's date)
    python scripts/post_session_tuner.py

    # Run for a specific date
    python scripts/post_session_tuner.py --date 2026-04-06

    # Run full weekly optimization
    python scripts/post_session_tuner.py --weekly

    # Show only warning/critical drift alerts
    python scripts/post_session_tuner.py --alerts

    # Save report to disk
    python scripts/post_session_tuner.py --save

    # Custom data directory
    python scripts/post_session_tuner.py --data-dir /path/to/data
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

# ── Project root ──────────────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.data.auto_tuner import AutoTuner

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Windows UTF-8 ─────────────────────────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(
        description="D195 Post-Session Auto-Tuner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--weekly",
        action="store_true",
        help="Run full weekly optimization across all arena data",
    )
    mode.add_argument(
        "--alerts",
        action="store_true",
        help="Print only active warning/critical drift alerts and exit",
    )

    parser.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        help="Session date to ingest (default: today)",
    )
    parser.add_argument(
        "--data-dir",
        metavar="DIR",
        default=str(_ROOT / "data"),
        help="Root data directory (default: data/)",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Save report to data/tuning_reports/",
    )
    parser.add_argument(
        "--save-dir",
        metavar="DIR",
        help="Override default save directory",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show DEBUG logs",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.INFO)
        logging.getLogger("src.data.auto_tuner").setLevel(logging.DEBUG)

    tuner = AutoTuner(data_dir=args.data_dir)

    # ── Mode: alerts only ────────────────────────────────────────────────────
    if args.alerts:
        alerts = tuner.get_active_alerts()
        if not alerts:
            print("No active drift alerts. All parameters nominal.")
            return

        _SEVERITY_ICON = {"critical": "[!!!]", "warning": "[!]  "}
        print(f"\nActive drift alerts ({len(alerts)}):\n")
        for alert in alerts:
            icon = _SEVERITY_ICON.get(alert.severity, "[?]  ")
            print(f"  {icon} {alert.parameter}")
            print(f"         Current:  {alert.current_value:.4g}")
            print(f"         Optimal:  {alert.optimal_value:.4g}")
            print(f"         Drift:    {alert.drift_pct:.1f}%")
            print(f"         Action:   {alert.recommendation}")
            print(f"         Evidence: {alert.evidence}")
            print()
        return

    # ── Mode: weekly optimization ────────────────────────────────────────────
    if args.weekly:
        print("Running weekly optimization...\n")
        report = tuner.run_weekly_optimization()
        print(report.report_text)
        if args.save:
            path = tuner.save_report(report, args.save_dir)
            print(f"\nReport saved: {path}")
        _print_recommendations(report)
        return

    # ── Mode: post-session (default) ─────────────────────────────────────────
    session_date: date | None = None
    if args.date:
        try:
            session_date = date.fromisoformat(args.date)
        except ValueError:
            print(f"ERROR: Invalid date format '{args.date}'. Use YYYY-MM-DD.", file=sys.stderr)
            sys.exit(1)

    date_str = (session_date or date.today()).isoformat()
    print(f"Running post-session tuning for {date_str}...\n")

    report = tuner.run_post_session(session_date)
    print(report.report_text)

    if args.save:
        path = tuner.save_report(report, args.save_dir)
        print(f"\nReport saved: {path}")

    _print_recommendations(report)

    # Exit code: 2 if critical alerts, 1 if warnings, 0 if clean
    if any(a.severity == "critical" for a in report.drift_alerts):
        sys.exit(2)
    if any(a.severity == "warning" for a in report.drift_alerts):
        sys.exit(1)


def _print_recommendations(report) -> None:
    """Print actionable recommended changes in a compact format."""
    if not report.recommended_changes:
        return
    print("\nRECOMMENDED CHANGES:")
    for change in report.recommended_changes:
        sev = change["severity"].upper()
        print(
            f"  [{sev}] {change['parameter']}: "
            f"{change['current']:.4g} → {change['recommended']:.4g}"
        )
        print(f"         {change['action']}")
    print()


if __name__ == "__main__":
    main()
