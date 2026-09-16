"""
Selection Arena CLI

Analyze scanner filter performance against historical data to find which
filter parameter combinations best capture big daily movers.

Usage examples:

  # Analyze March 30 with all profiles (uses universe file if present)
  python scripts/run_selection_arena.py --date 2026-03-30

  # Compare specific profiles
  python scripts/run_selection_arena.py --date 2026-03-30 --profiles current,wide_net,price_floor_150

  # Multi-day backtest
  python scripts/run_selection_arena.py --dates 2026-03-25,2026-03-26,2026-03-27

  # Show per-stock filter detail for a specific date + profile
  python scripts/run_selection_arena.py --date 2026-03-30 --detail current

  # List all available profiles
  python scripts/run_selection_arena.py --list-profiles

  # Save results to file
  python scripts/run_selection_arena.py --date 2026-03-30 --save --save-format json

  # Show what sessions are available
  python scripts/run_selection_arena.py --list-dates

  # Add a stock to the universe manually (for stocks the scanner missed)
  python scripts/run_selection_arena.py --add-to-universe 2026-03-30 \\
    --stock ticker=ITRM,open_price=2.30,previous_close=1.50,gap_pct=0.533,\\
            rvol_at_open=12.0,premarket_volume=480000,max_gain_from_open=0.98,\\
            has_news=true,scanner_found=false,data_confidence=estimated
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# ── Project root ──────────────────────────────────────────────────────
_THIS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _THIS_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.selection_arena.backtest import BacktestConfig, BacktestRunner
from src.selection_arena.market_movers import MarketMoversDB, build_records_from_dicts
from src.selection_arena.models import FilterProfile, MoverRecord
from src.selection_arena.profiles import (
    PROFILES,
    get_profile,
    profiles_summary_table,
)
from src.selection_arena.short_analyzer import ShortAnalyzer, ShortAnalyzerConfig
from src.selection_arena.report import (
    generate_day_detail,
    generate_text_report,
    save_report,
)

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Windows UTF-8 output ──────────────────────────────────────────────
import sys as _sys
if hasattr(_sys.stdout, "reconfigure"):
    try:
        _sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Selection Arena — scanner filter optimization",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # ── Date selection ──
    date_group = parser.add_mutually_exclusive_group()
    date_group.add_argument(
        "--date",
        metavar="DATE",
        help="Single date to analyze (YYYY-MM-DD)",
    )
    date_group.add_argument(
        "--dates",
        metavar="DATES",
        help="Comma-separated dates (YYYY-MM-DD,YYYY-MM-DD,...)",
    )
    date_group.add_argument(
        "--all-dates",
        action="store_true",
        help="Analyze all dates that have universe files or journals",
    )

    # ── Profile selection ──
    parser.add_argument(
        "--profiles",
        metavar="PROFILES",
        help="Comma-separated profile names. Default: all profiles",
    )
    parser.add_argument(
        "--profiles-file",
        metavar="FILE",
        type=Path,
        help="Load additional custom profiles from JSON file",
    )

    # ── Scoring ──
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.20,
        metavar="PCT",
        help="Big mover threshold: stocks that run X%% from open (default 0.20 = 20%%)",
    )
    parser.add_argument(
        "--beta",
        type=float,
        default=2.0,
        metavar="BETA",
        help="F-beta weight (default 2.0 = recall weighted 4× over precision)",
    )

    # ── Output ──
    parser.add_argument(
        "--detail",
        metavar="PROFILE",
        help="Show per-stock filter breakdown for this profile (requires --date)",
    )
    parser.add_argument(
        "--no-day-detail",
        action="store_true",
        help="Suppress per-day table in report",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="Save report to data/selection_arena/results/",
    )
    parser.add_argument(
        "--save-format",
        choices=["text", "json", "both"],
        default="text",
        help="Format for saved report (default: text)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show DEBUG logs",
    )

    # ── Listing commands ──
    parser.add_argument(
        "--list-profiles",
        action="store_true",
        help="List all available filter profiles and their parameters",
    )
    parser.add_argument(
        "--list-dates",
        action="store_true",
        help="List dates with available universe or journal data",
    )

    # ── D161: Short analysis ──
    parser.add_argument(
        "--short-analysis",
        action="store_true",
        help="D161: Run short selling simulation on high-faller candidates. "
             "Shows what would have happened if faller-rejected stocks were shorted.",
    )
    parser.add_argument(
        "--short-tickers",
        metavar="TICKERS",
        help="Comma-separated tickers to simulate as shorts (overrides heuristic detection). "
             "E.g. --short-tickers ARTL,EEIQ,SST",
    )
    parser.add_argument(
        "--short-stop-pct",
        type=float,
        default=0.35,
        metavar="PCT",
        help="D161: Stop-loss %% above short entry (default 0.35 = 35%% above)",
    )
    parser.add_argument(
        "--short-dolvol-min",
        type=float,
        default=500_000,
        metavar="DOLVOL",
        help="D161: Minimum dollar volume to qualify for short (default $500K)",
    )

    # ── Universe management ──
    parser.add_argument(
        "--add-to-universe",
        metavar="DATE",
        help="Add a stock to the universe for DATE",
    )
    parser.add_argument(
        "--stock",
        metavar="K=V,K=V,...",
        help="Stock fields to add (with --add-to-universe). "
             "Required: ticker. Optional: open_price, gap_pct, rvol_at_open, "
             "premarket_volume, max_gain_from_open, has_news, scanner_found, etc.",
    )
    parser.add_argument(
        "--show-universe",
        metavar="DATE",
        help="Show the loaded universe for a date",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        logging.getLogger("src.selection_arena").setLevel(logging.DEBUG)

    db = MarketMoversDB()

    # ── Listing commands ────────────────────────────────────────────
    if args.list_profiles:
        print("\nAvailable filter profiles:")
        print("=" * 70)
        print(profiles_summary_table())
        print()
        for name, profile in sorted(PROFILES.items()):
            if profile.description:
                print(f"  {name}: {profile.description}")
        return

    if args.list_dates:
        dates = db.list_available_dates()
        if dates:
            print(f"\nAvailable dates ({len(dates)}):")
            for d in dates:
                universe = db.load_universe(d)
                n = len(universe)
                n_found = sum(1 for r in universe if r.scanner_found)
                n_movers = sum(
                    1 for r in universe
                    if r.max_gain_from_open is not None and r.max_gain_from_open >= 0.20
                )
                print(f"  {d}  — {n} stocks  ({n_found} scanner-found, {n_movers} big movers)")
        else:
            print("No dates found. Add universe files to data/selection_arena/ or run the bot.")
        return

    if args.show_universe:
        universe = db.load_universe(args.show_universe)
        if not universe:
            print(f"No universe data for {args.show_universe}")
            return
        print(f"\nUniverse for {args.show_universe} ({len(universe)} stocks):")
        print(f"  {'Ticker':<8} {'Found':<6} {'Action':<10} {'Open':>6} {'Gap':>7} {'RVOL':>7} {'MaxGain':>8} {'Conf':<12} Notes")
        print("  " + "-" * 80)
        for r in sorted(universe, key=lambda x: (not x.scanner_found, x.ticker)):
            found_marker = "✓" if r.scanner_found else " "
            action = r.journal_action or "—"
            open_str = f"${r.open_price:.2f}" if r.open_price else "?"
            gap_str = f"{r.gap_pct:.1%}" if r.gap_pct is not None else "?"
            rvol_str = f"{r.rvol_at_open:.1f}x" if r.rvol_at_open is not None else "?"
            gain_str = f"+{r.max_gain_from_open:.0%}" if r.max_gain_from_open is not None else "?"
            print(
                f"  {r.ticker:<8} {found_marker:<6} {action:<10} {open_str:>6} "
                f"{gap_str:>7} {rvol_str:>7} {gain_str:>8} {r.data_confidence:<12} {r.notes[:30]}"
            )
        return

    if args.add_to_universe:
        date = args.add_to_universe
        if not args.stock:
            print("--stock K=V,K=V,... required with --add-to-universe")
            return
        stock_dict = _parse_stock_args(args.stock)
        stock_dict["date"] = date
        records = build_records_from_dicts([stock_dict])
        if records:
            merged = db.add_stocks_to_universe(date, records)
            print(f"Added {len(records)} stock(s) to universe for {date}. Total: {len(merged)}")
        else:
            print("Failed to parse stock data")
        return

    # ── Resolve dates ───────────────────────────────────────────────
    if args.all_dates:
        dates = db.list_available_dates()
        if not dates:
            print("No dates found. Use --list-dates to check.")
            return
    elif args.dates:
        dates = [d.strip() for d in args.dates.split(",")]
    elif args.date:
        dates = [args.date]
    else:
        parser.print_help()
        print("\nError: specify --date, --dates, --all-dates, or use --list-dates / --list-profiles")
        return

    # ── Resolve profiles ────────────────────────────────────────────
    if args.profiles_file:
        from src.selection_arena.profiles import load_profiles_from_file
        extra = load_profiles_from_file(args.profiles_file)
        PROFILES.update(extra)

    if args.profiles:
        profile_names = [n.strip() for n in args.profiles.split(",")]
        try:
            profiles = [get_profile(n) for n in profile_names]
        except KeyError as e:
            print(f"Error: {e}")
            print(f"Available profiles: {', '.join(sorted(PROFILES.keys()))}")
            return
    else:
        # Default: run a focused set (not ALL ablations unless verbose)
        if args.verbose:
            profiles = list(PROFILES.values())
        else:
            default_names = [
                "current", "wide_net", "moderate", "tight",
                "price_floor_150", "rvol_1_5x", "relaxed_price_rvol",
                "ablation_price_floor", "ablation_rvol", "ablation_dolvol",
            ]
            profiles = [PROFILES[n] for n in default_names if n in PROFILES]

    # ── Run backtest ────────────────────────────────────────────────
    config = BacktestConfig(
        big_mover_threshold=args.threshold,
        beta=args.beta,
    )
    runner = BacktestRunner(config=config)

    print(f"\nRunning selection arena: {len(dates)} date(s) × {len(profiles)} profile(s)...")
    result = runner.run(dates=dates, profiles=profiles)

    if not result.dates:
        print(f"\nNo data found for requested dates. Use --list-dates to check.")
        print("To add data manually: --add-to-universe DATE --stock ticker=X,open_price=Y,...")
        return

    # ── Detail view for one profile ──────────────────────────────────
    if args.detail:
        if len(dates) != 1:
            print("--detail requires exactly one --date")
            return
        date = dates[0]
        day_results = result.per_day.get(date, [])
        detail_analysis = next(
            (da for da in day_results if da.profile_name == args.detail), None
        )
        if detail_analysis is None:
            available = [da.profile_name for da in day_results]
            print(f"Profile '{args.detail}' not found. Available: {available}")
            return
        print(generate_day_detail(detail_analysis, show_all_stocks=True))
        return

    # ── Main report ────────────────────────────────────────────────
    print(generate_text_report(
        result,
        show_day_detail=not args.no_day_detail,
        show_filter_detail=args.verbose,
    ))

    # ── D161: Short analysis ─────────────────────────────────────────
    if args.short_analysis:
        _run_short_analysis(args, dates, db)

    # ── Save ────────────────────────────────────────────────────────
    if args.save:
        output_dir = _PROJECT_ROOT / "data" / "selection_arena" / "results"
        if args.save_format in ("text", "both"):
            path = save_report(result, output_dir, fmt="text")
            print(f"\nReport saved: {path}")
        if args.save_format in ("json", "both"):
            path = save_report(result, output_dir, fmt="json")
            print(f"JSON saved: {path}")


# ── Helpers ───────────────────────────────────────────────────────────

def _run_short_analysis(args, dates: list[str], db: MarketMoversDB) -> None:
    """D161: Run short selling simulation and print results.

    Loads the universe for each date, then simulates shorting high-faller candidates.
    Uses either --short-tickers (specific tickers) or heuristic detection
    (stocks with gap>=20%, RVOL>=2x, that actually faded on the day).
    """
    cfg = ShortAnalyzerConfig(
        stop_pct_above_entry=getattr(args, "short_stop_pct", 0.35),
        dollar_volume_min=getattr(args, "short_dolvol_min", 500_000),
    )
    analyzer = ShortAnalyzer(config=cfg)

    # Build combined universe across all dates
    all_records = []
    for date in dates:
        universe = db.load_universe(date)
        all_records.extend(universe)

    if not all_records:
        print("\nD161 SHORT ANALYSIS: No universe data found for requested dates.")
        print("Add data with --add-to-universe DATE --stock ticker=X,...")
        return

    print(f"\n{'='*60}")
    print("D161 SHORT SELLING SIMULATION")
    print(f"{'='*60}")
    print(f"Dates: {', '.join(dates)} | {len(all_records)} stocks in universe")
    print(f"Config: stop=+{cfg.stop_pct_above_entry:.0%}, dolvol_min=${cfg.dollar_volume_min/1000:.0f}K, "
          f"rvol_min={cfg.rvol_min:.1f}x, gap_min={cfg.gap_min_pct:.0%}")
    print()

    short_tickers = None
    if getattr(args, "short_tickers", None):
        short_tickers = [t.strip().upper() for t in args.short_tickers.split(",")]
        print(f"Using specified tickers: {short_tickers}")
        stats = analyzer.run_on_tickers(short_tickers, all_records)
    else:
        print("Using heuristic detection (gap>=20%, RVOL>=2x, actual faders)")
        stats = analyzer.run(all_records)

    print(analyzer.format_report(stats))

    # Combined portfolio context
    if stats.n_would_short > 0:
        print()
        print("── Combined Long+Short Context ─────────────────────────")
        print(f"  If {stats.n_would_short} shorts had been live alongside longs:")
        print(f"  Avg short P&L:  {stats.avg_pnl_pct:+.2%} per trade")
        print(f"  Win rate:       {stats.win_rate:.1%} ({stats.n_wins}W / {stats.n_losses}L)")
        print(f"  Stop-out rate:  {stats.stop_rate:.1%}")
        print(f"  Key insight: High MAE ({stats.avg_max_adverse_excursion_pct:+.1%} avg)")
        print(f"               means tight stops get hit frequently — the 35% stop is intentional.")
        if stats.total_pnl_pct > 0:
            print(f"  Verdict: SHORT BOOK PROFITABLE — total {stats.total_pnl_pct:+.2%} equal-dollar return")
        else:
            print(f"  Verdict: SHORT BOOK UNPROFITABLE — total {stats.total_pnl_pct:+.2%} equal-dollar return")


def _parse_stock_args(args_str: str) -> dict:
    """Parse 'key=value,key=value,...' into a dict with type coercion."""
    result = {}
    for pair in args_str.split(","):
        if "=" not in pair:
            continue
        key, _, value = pair.partition("=")
        key = key.strip()
        value = value.strip()

        # Type coercion
        if value.lower() == "true":
            result[key] = True
        elif value.lower() == "false":
            result[key] = False
        elif value.lower() in ("none", "null", ""):
            result[key] = None
        else:
            try:
                result[key] = int(value)
            except ValueError:
                try:
                    result[key] = float(value)
                except ValueError:
                    result[key] = value

    return result


if __name__ == "__main__":
    main()
