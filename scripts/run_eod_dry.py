"""Dry-run the EOD pipeline against synthetic Phase 0 data.

Useful when:
  * A contributor adds a new EOD section and wants to verify the wire-up
    + report shape WITHOUT waiting for Monday's first capture
  * Diagnosing why a real EOD report is missing a section
  * Smoke-testing the consolidator after refactoring

Generates a synthetic Phase 0 capture in a tmpdir (5 positions, 25 fills,
5 cohort matches), then drives the full EOD pipeline:
  - run_eod_phase0_health
  - run_eod_bocpd_refit_check
  - run_eod_cohort_backfill (with mock client)
  - run_eod_bayesian_fit
  - build_eod_report → write_eod_report

Prints the resulting JSON report to stdout. Optionally persists to
data/reports/eod_<date>_dry.json with --persist.

Usage:
    python scripts/run_eod_dry.py                # print to stdout
    python scripts/run_eod_dry.py --persist      # also write to data/reports/
    python scripts/run_eod_dry.py --json         # JSON-only output (no banners)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _build_synthetic_phase0(base_dir: Path, session_date: str) -> None:
    """Write 4-schema Phase 0 partition to base_dir."""
    t0 = datetime.now(timezone.utc).replace(hour=14, minute=30, second=0, microsecond=0)

    # 5 trade_context entries (terminal=filled)
    trade_rows = [{
        "schema_version": 1, "order_id": f"oid-{i}", "ticker": f"TKR{i}",
        "side": "buy", "requested_qty": 100, "requested_px": 5.0 + i,
        "submit_ts": (t0 + timedelta(seconds=i)).isoformat(),
        "submit_nbbo_bid": 5.0 + i - 0.05, "submit_nbbo_ask": 5.0 + i + 0.05,
        "first_fill_ts": (t0 + timedelta(seconds=i + 1)).isoformat(),
        "first_fill_nbbo_bid": 5.0 + i - 0.05, "first_fill_nbbo_ask": 5.0 + i + 0.05,
        "terminal_ts": (t0 + timedelta(seconds=i + 30)).isoformat(),
        "terminal_nbbo_bid": 5.0 + i - 0.05, "terminal_nbbo_ask": 5.0 + i + 0.05,
        "terminal_status": "filled", "terminal_filled_qty": 100,
    } for i in range(5)]
    tc_dir = base_dir / "trade_context" / f"session_date={session_date}"
    tc_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(trade_rows).to_parquet(tc_dir / "orders.parquet", index=False)

    # 25 child_fill_ticks (5 per position with small slippage)
    fill_rows = []
    for i in range(5):
        for j in range(5):
            fill_rows.append({
                "schema_version": 1, "parent_order_id": f"oid-{i}",
                "child_fill_ts": (t0 + timedelta(seconds=10 + i * 5 + j)).isoformat(),
                "qty": 20, "price": (5.0 + i) * (1.0 + 0.0008 * j),
                "venue": "NYSE",
                "cumulative_filled_qty": 20 * (j + 1),
                "nbbo_bid_at_fill": (5.0 + i) - 0.05,
                "nbbo_ask_at_fill": (5.0 + i) + 0.05,
            })
    cf_dir = base_dir / "child_fill_ticks" / f"session_date={session_date}"
    cf_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(fill_rows).to_parquet(cf_dir / "fills.parquet", index=False)

    # 5 bar_context entries (one per position)
    bar_rows = [{
        "schema_version": 1, "position_id": f"oid-{i}", "ticker": f"TKR{i}",
        "entry_ts": (t0 + timedelta(seconds=i + 30)).isoformat(),
        "entry_bar_open_ts": t0.isoformat(),
        "entry_bar_open": 5.0 + i, "entry_bar_high": 5.5 + i,
        "entry_bar_low": 4.5 + i, "entry_bar_close": 5.2 + i,
        "entry_bar_volume": 5000, "our_q_shares": 100,
        "q_over_v_tau": 100 / 5000.0,
        "bar_data_quality": "complete",
    } for i in range(5)]
    bc_dir = base_dir / "bar_context" / f"session_date={session_date}"
    bc_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(bar_rows).to_parquet(bc_dir / "entries.parquet", index=False)

    # 5 cohort_registry entries (one peer per traded ticker)
    cohort_rows = [{
        "schema_version": 1, "cohort_id": f"cohort-{i}",
        "traded_ticker": f"TKR{i}", "cohort_ticker": f"PEER{i}",
        "match_dt": (t0 + timedelta(seconds=i + 30)).isoformat(),
        "match_features": {"catalyst_type": "earnings_beat",
                            "market_cap_bucket": "micro"},
        "cohort_entry_ref_px": 10.0 + i,
        "cohort_eod_px": None, "cohort_60min_px": None,
        "cohort_signed_return_60min": None,
        "cohort_data_quality": "partial",
    } for i in range(5)]
    co_dir = base_dir / "cohort_registry" / f"session_date={session_date}"
    co_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(cohort_rows).to_parquet(co_dir / "cohorts.parquet", index=False)


def _build_synthetic_trade_results(corpus_path: Path) -> None:
    """Write a synthetic trade_results.jsonl for BOCPD refit + Bayesian
    runner to consume."""
    rows = [
        {"ticker": f"TKR{i}", "session_date": "2026-04-26",
         "pnl": (-30.0 + i * 12.0), "is_win": (i * 12.0 - 30) > 0,
         "kelly_tier": 1, "catalyst_type": "earnings_beat"}
        for i in range(35)  # >= MIN_OBSERVATIONS_FOR_FIT (30)
    ]
    with open(corpus_path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


async def _run_eod_pipeline(verbose: bool = True) -> dict:
    """Drive the EOD pipeline against a synthetic Phase 0 capture."""
    from src.analysis.bayesian_eod_runner import run_eod_bayesian_fit
    from src.analysis.cohort_eod_backfill import run_eod_cohort_backfill
    from src.analysis.instrumentation import InstrumentationWriter
    from src.monitoring.eod_recon import (
        run_eod_bocpd_refit_check, run_eod_phase0_health,
    )
    from src.monitoring.eod_report import build_eod_report

    tmp = Path(tempfile.mkdtemp(prefix="eod_dry_"))
    try:
        sd = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        instrumentation_dir = tmp / "instrumentation"
        reports_dir = tmp / "reports"
        priors_dir = tmp / "priors"
        priors_dir.mkdir(parents=True, exist_ok=True)
        corpus_path = tmp / "trade_results.jsonl"

        if verbose:
            print(f"[eod-dry] tmpdir: {tmp}")
            print(f"[eod-dry] session_date: {sd}")
            print(f"[eod-dry] writing synthetic Phase 0 capture...")
        _build_synthetic_phase0(instrumentation_dir, sd)
        _build_synthetic_trade_results(corpus_path)

        # Construct an InstrumentationWriter for health snapshot
        writer = InstrumentationWriter(
            base_dir=instrumentation_dir, session_date=sd, ring_size=10,
        )

        # Mock client for cohort backfill
        client = MagicMock()
        async def _quote(sym):
            return {"bid": 11.0, "ask": 11.10}
        async def _bars(sym, start, end, timeframe):
            return [{"t": "2026-04-26T15:30:00+00:00", "c": 11.20}]
        client.get_latest_quote = AsyncMock(side_effect=_quote)
        client.get_bars = AsyncMock(side_effect=_bars)

        if verbose:
            print(f"[eod-dry] running EOD pipeline sections...")

        # Section 1: Phase 0 health
        phase0_health = run_eod_phase0_health(writer)

        # Section 2: BOCPD refit
        bocpd_refit = run_eod_bocpd_refit_check(
            corpus_path=corpus_path,
            prior_path=priors_dir / "s1_bocpd_prior.parquet",
        )

        # Section 3: Cohort backfill
        cohort_bf = await run_eod_cohort_backfill(
            client=client, base_dir=instrumentation_dir, session_date=sd,
        )

        # Section 4: Bayesian fit (will skip if observations < 30)
        bayes_fit = run_eod_bayesian_fit(
            base_dir=instrumentation_dir, session_date=sd,
            report_dir=reports_dir, min_observations=20,  # lower for dry-run
            chains=2, tune=100, draws=100,
        )

        # Section 5: Consolidator
        report = build_eod_report(
            session_date=sd,
            eod_recon=None,  # no broker → no recon
            phase0_health=phase0_health,
            bocpd_refit=bocpd_refit,
            cohort_backfill=cohort_bf,
            bayesian_fit=bayes_fit,
            extra_metadata={
                "broker_equity_eod": 100_000.0,
                "open_positions_eod": 5,
                "dry_run": True,
            },
        )
        return report

    finally:
        # Don't delete tmp until after we've captured the report
        if verbose:
            print(f"[eod-dry] cleaning up tmpdir: {tmp}")
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--persist", action="store_true",
        help="Also write to data/reports/eod_<date>_dry.json",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="JSON-only output (no banner / progress messages)",
    )
    args = parser.parse_args()

    verbose = not args.json
    report = asyncio.run(_run_eod_pipeline(verbose=verbose))

    if args.persist:
        from src.monitoring.eod_report import write_eod_report
        # Override session_date to mark as dry-run
        report["session_date"] = report["session_date"] + "_dry"
        out_path = write_eod_report(report)
        if verbose and out_path:
            print(f"[eod-dry] persisted to {out_path}")

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print("\n" + "=" * 60)
        print(f"EOD DRY-RUN REPORT (session_date={report['session_date']})")
        print("=" * 60)
        print(f"fires:         {report['fires']}")
        print(f"fires_count:   {report['fires_count']}")
        print(f"sections present:")
        for k, v in report["sections"].items():
            status = "(populated)" if v else "(null)"
            print(f"  {k:<20} {status}")
        print(f"metadata:      {report['metadata']}")
        print("=" * 60)
        print("\nFull JSON:\n")
        print(json.dumps(report, indent=2, default=str)[:2000])
        print("..." if len(json.dumps(report, default=str)) > 2000 else "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
