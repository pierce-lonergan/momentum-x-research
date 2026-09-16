"""
D119: Strategy Simulation Arena — CLI Entry Point

Replays historical paper trading sessions through different strategy
configurations, ranks them via Elo tournament, and identifies which
parameter combinations are most profitable.

Usage:
  python scripts/run_arena.py                               # Full tournament
  python scripts/run_arena.py --profiles baseline,aggressive_all
  python scripts/run_arena.py --dates 2026-03-20
  python scripts/run_arena.py --rankings                    # Show saved Elo
  python scripts/run_arena.py --counterfactual ANNA         # What-if for ticker
  python scripts/run_arena.py --entry-only                  # Only re-evaluate entries
  python scripts/run_arena.py --exit-only                   # Only replay exits
  python scripts/run_arena.py --fetch-bars                  # Fetch missing bars first
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Project root ──
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# Force UTF-8 output on Windows
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from src.arena.strategy_arena import (
    ArenaDataLoader,
    StrategyArena,
    StrategyProfile,
    TradeCounterfactual,
    TournamentResult,
    build_counterfactuals,
    simulate_profile_session,
)

# ═══════════════════════════════════════════════════════════════════
# Pre-Built Strategy Profiles (22: 12 original + 6 evolved + 4 ablation)
# ═══════════════════════════════════════════════════════════════════

PROFILES: dict[str, StrategyProfile] = {
    # ── Entry-Focused ──
    "baseline": StrategyProfile(
        name="baseline",
        description="All production defaults — control group",
    ),

    "news_heavy": StrategyProfile(
        name="news_heavy",
        description="News-dominant scoring (catalyst_news=0.45)",
        agent_weights={
            "catalyst_news": 0.45, "technical": 0.15,
            "volume_rvol": 0.20, "float_structure": 0.15,
            "institutional": 0.05, "deep_search": 0.00,
        },
    ),

    "technical_heavy": StrategyProfile(
        name="technical_heavy",
        description="TA-dominant scoring (technical=0.40)",
        agent_weights={
            "catalyst_news": 0.20, "technical": 0.40,
            "volume_rvol": 0.20, "float_structure": 0.15,
            "institutional": 0.05, "deep_search": 0.00,
        },
    ),

    "high_bar_entry": StrategyProfile(
        name="high_bar_entry",
        description="Fewer, higher-conviction entries (MFCS>0.35, 3+ directional)",
        mfcs_buy_threshold=0.35,
        min_directional_agents=3,
    ),

    "low_bar_entry": StrategyProfile(
        name="low_bar_entry",
        description="More entries, lower bar (MFCS>0.15, lambda=0.10)",
        mfcs_buy_threshold=0.15,
        risk_aversion_lambda=0.10,
    ),

    # ── Exit-Focused ──
    "tight_exits": StrategyProfile(
        name="tight_exits",
        description="Fast profit capture (exit=0.35, tighten=0.15, trail=0.02)",
        exit_threshold=0.35,
        tighten_threshold=0.15,
        trailing_activation_pct=0.02,
    ),

    "wide_exits": StrategyProfile(
        name="wide_exits",
        description="Let winners run (exit=0.70, tighten=0.40, trail=0.06)",
        exit_threshold=0.70,
        tighten_threshold=0.40,
        trailing_activation_pct=0.06,
    ),

    "wide_targets": StrategyProfile(
        name="wide_targets",
        description="Wider tranches + runner (10/20/40%, runner=25%)",
        tranche_targets=(0.10, 0.20, 0.40),
        runner_pct=0.25,
        exit_threshold=0.70,
        tighten_threshold=0.40,
    ),

    "tight_stops": StrategyProfile(
        name="tight_stops",
        description="Tighter risk (ATR 1.5x, cap 10%, stop 3.5%)",
        atr_multiplier=1.5,
        atr_cap_pct=0.10,
        stop_loss_pct=0.035,
    ),

    "momentum_runner": StrategyProfile(
        name="momentum_runner",
        description="Runner-heavy (30% runner, 12% trail, 5% activation)",
        runner_pct=0.30,
        runner_trail_pct=0.12,
        trailing_activation_pct=0.05,
        exit_threshold=0.70,
    ),

    # ── Combined ──
    "aggressive_all": StrategyProfile(
        name="aggressive_all",
        description="Max risk/reward: low bar, high risk, wide targets, runner",
        mfcs_buy_threshold=0.15,
        risk_aversion_lambda=0.10,
        risk_per_trade_pct=0.02,
        max_position_pct=0.25,
        kelly_enabled=True,
        tier2_min_mfcs=0.20,
        tier2_risk_pct=0.03,
        tier2_max_position_pct=0.30,
        tranche_targets=(0.08, 0.15, 0.30),
        exit_threshold=0.70,
        tighten_threshold=0.35,
        runner_pct=0.25,
        trailing_activation_pct=0.06,
    ),

    "conservative_all": StrategyProfile(
        name="conservative_all",
        description="Min drawdown: high bar, low risk, tight targets, no runner",
        mfcs_buy_threshold=0.30,
        min_directional_agents=3,
        risk_per_trade_pct=0.005,
        max_position_pct=0.10,
        atr_cap_pct=0.12,
        tranche_targets=(0.03, 0.06, 0.10),
        exit_threshold=0.50,
        tighten_threshold=0.25,
        runner_pct=0.0,
    ),

    # ── D120: Evolved Profiles (6) ──
    "evolved_hunter": StrategyProfile(
        name="evolved_hunter",
        description="aggressive_all + decoupled trailing (activate 3%, trail 8%)",
        mfcs_buy_threshold=0.15,
        risk_aversion_lambda=0.10,
        risk_per_trade_pct=0.02,
        max_position_pct=0.25,
        kelly_enabled=True,
        tier2_min_mfcs=0.20,
        tier2_risk_pct=0.03,
        tranche_targets=(0.08, 0.15, 0.30),
        exit_threshold=0.70,
        tighten_threshold=0.35,
        runner_pct=0.25,
        trailing_activation_pct=0.03,
        trailing_trail_distance_pct=0.08,
        runner_trail_pct=0.10,
    ),

    "evolved_holder": StrategyProfile(
        name="evolved_holder",
        description="momentum_runner + suppressed premature exit signals (time_decay=0)",
        runner_pct=0.30,
        runner_trail_pct=0.12,
        trailing_activation_pct=0.05,
        trailing_trail_distance_pct=0.07,
        exit_threshold=0.75,
        tighten_threshold=0.40,
        exit_signal_weight_overrides={
            "time_decay": 0.0,
            "volume_fade": 0.05,
            "distribution_detector": 0.20,
        },
    ),

    "evolved_holder_moderate": StrategyProfile(
        name="evolved_holder_moderate",
        description="evolved_holder but time_decay=0.03 (partial suppression)",
        runner_pct=0.30,
        runner_trail_pct=0.12,
        trailing_activation_pct=0.05,
        trailing_trail_distance_pct=0.07,
        exit_threshold=0.75,
        tighten_threshold=0.40,
        exit_signal_weight_overrides={
            "time_decay": 0.03,
            "volume_fade": 0.05,
            "distribution_detector": 0.20,
        },
    ),

    "evolved_sniper": StrategyProfile(
        name="evolved_sniper",
        description="high bar + aggressive sizing (denom=0.35) + tight ratchet (0.40)",
        mfcs_buy_threshold=0.30,
        min_directional_agents=3,
        mfcs_scaling_denom=0.35,
        risk_per_trade_pct=0.02,
        max_position_pct=0.30,
        kelly_enabled=True,
        tranche_targets=(0.08, 0.15, 0.30),
        tranche_ratchet_ratio=0.40,
        trailing_activation_pct=0.04,
        trailing_trail_distance_pct=0.06,
        runner_pct=0.20,
        runner_trail_pct=0.10,
    ),

    "evolved_hybrid": StrategyProfile(
        name="evolved_hybrid",
        description="All 4 D120 dimensions: trail decouple + exit weights + sizing + ratchet",
        mfcs_buy_threshold=0.15,
        risk_aversion_lambda=0.10,
        risk_per_trade_pct=0.02,
        max_position_pct=0.25,
        kelly_enabled=True,
        mfcs_scaling_denom=0.40,
        tranche_targets=(0.10, 0.20, 0.40),
        tranche_ratchet_ratio=0.35,
        exit_threshold=0.75,
        tighten_threshold=0.40,
        trailing_activation_pct=0.03,
        trailing_trail_distance_pct=0.08,
        runner_pct=0.25,
        runner_trail_pct=0.10,
        exit_signal_weight_overrides={
            "time_decay": 0.0,
            "volume_fade": 0.05,
            "distribution_detector": 0.20,
        },
    ),

    "evolved_conservative": StrategyProfile(
        name="evolved_conservative",
        description="Conservative + decoupled trail + tight ratchet, no runner",
        mfcs_buy_threshold=0.30,
        min_directional_agents=3,
        risk_per_trade_pct=0.005,
        max_position_pct=0.10,
        tranche_targets=(0.05, 0.10, 0.20),
        tranche_ratchet_ratio=0.50,
        trailing_activation_pct=0.03,
        trailing_trail_distance_pct=0.05,
        exit_threshold=0.55,
        tighten_threshold=0.25,
        runner_pct=0.0,
    ),

    # ── D120: Ablation Profiles (4) — isolate each dimension on aggressive_all base ──
    "ablation_trailing": StrategyProfile(
        name="ablation_trailing",
        description="aggressive_all + ONLY trailing decoupling (activate 3%, trail 8%)",
        mfcs_buy_threshold=0.15,
        risk_aversion_lambda=0.10,
        risk_per_trade_pct=0.02,
        max_position_pct=0.25,
        kelly_enabled=True,
        tier2_min_mfcs=0.20,
        tier2_risk_pct=0.03,
        tier2_max_position_pct=0.30,
        tranche_targets=(0.08, 0.15, 0.30),
        exit_threshold=0.70,
        tighten_threshold=0.35,
        runner_pct=0.25,
        trailing_activation_pct=0.03,
        trailing_trail_distance_pct=0.08,
    ),

    "ablation_signals": StrategyProfile(
        name="ablation_signals",
        description="aggressive_all + ONLY exit signal weight overrides",
        mfcs_buy_threshold=0.15,
        risk_aversion_lambda=0.10,
        risk_per_trade_pct=0.02,
        max_position_pct=0.25,
        kelly_enabled=True,
        tier2_min_mfcs=0.20,
        tier2_risk_pct=0.03,
        tier2_max_position_pct=0.30,
        tranche_targets=(0.08, 0.15, 0.30),
        exit_threshold=0.70,
        tighten_threshold=0.35,
        runner_pct=0.25,
        trailing_activation_pct=0.06,
        exit_signal_weight_overrides={
            "time_decay": 0.0,
            "volume_fade": 0.05,
            "distribution_detector": 0.20,
        },
    ),

    "ablation_sizing": StrategyProfile(
        name="ablation_sizing",
        description="aggressive_all + ONLY MFCS scaling denom=0.35",
        mfcs_buy_threshold=0.15,
        risk_aversion_lambda=0.10,
        risk_per_trade_pct=0.02,
        max_position_pct=0.25,
        kelly_enabled=True,
        tier2_min_mfcs=0.20,
        tier2_risk_pct=0.03,
        tier2_max_position_pct=0.30,
        tranche_targets=(0.08, 0.15, 0.30),
        exit_threshold=0.70,
        tighten_threshold=0.35,
        runner_pct=0.25,
        trailing_activation_pct=0.06,
        mfcs_scaling_denom=0.35,
    ),

    "ablation_ratchet": StrategyProfile(
        name="ablation_ratchet",
        description="aggressive_all + ONLY tranche ratchet ratio=0.40",
        mfcs_buy_threshold=0.15,
        risk_aversion_lambda=0.10,
        risk_per_trade_pct=0.02,
        max_position_pct=0.25,
        kelly_enabled=True,
        tier2_min_mfcs=0.20,
        tier2_risk_pct=0.03,
        tier2_max_position_pct=0.30,
        tranche_targets=(0.08, 0.15, 0.30),
        exit_threshold=0.70,
        tighten_threshold=0.35,
        runner_pct=0.25,
        trailing_activation_pct=0.06,
        tranche_ratchet_ratio=0.40,
    ),
}


# ═══════════════════════════════════════════════════════════════════
# Report Output
# ═══════════════════════════════════════════════════════════════════

def print_rankings(result: TournamentResult) -> None:
    """Print Elo-ranked strategy table."""
    print()
    print("=" * 85)
    print(f"  STRATEGY ARENA RANKINGS  ({result.n_sessions} sessions, "
          f"{result.n_profiles} profiles, {result.n_matchups} matchups)")
    print("=" * 85)
    print(f"  {'Rank':>4} | {'Profile':<20} | {'Elo +/- s':>12} | "
          f"{'W/L/D':>9} | {'Win%':>5} | {'Total P&L':>10}")
    print(f"  {'':->4}-+-{'':->20}-+-{'':->12}-+-{'':->9}-+-{'':->5}-+-{'':->10}")

    for rank, v in enumerate(result.rankings, 1):
        elo_str = f"{v.elo_rating:7.1f} (PnL σ={v.pnl_stddev:5.1f})"
        wld = f"{v.win_count}/{v.loss_count}/{v.draw_count}"
        win_pct = f"{v.win_rate * 100:.1f}%"
        pnl_sign = "+" if v.total_pnl >= 0 else ""
        pnl_str = f"{pnl_sign}${v.total_pnl:,.2f}"

        # Mark mid-pack uncertainty
        marker = ""
        if 3 < rank < result.n_profiles - 2 and result.n_sessions < 15:
            marker = " ~"  # Statistically indistinguishable

        print(f"  {rank:4d} | {v.profile_name:<20} | {elo_str:>12} | "
              f"{wld:>9} | {win_pct:>5} | {pnl_str:>10}{marker}")

    print()
    if result.n_sessions < 15:
        print("  NOTE: With <15 sessions, mid-pack rankings (marked ~) may overlap.")
        print("  Trust TOP 2-3 and BOTTOM 2-3 only.")
    print()


def print_profile_detail(
    profile_name: str,
    results: list,  # list[ProfileSessionResult]
) -> None:
    """Print per-session breakdown for a single profile."""
    print(f"\n  Profile: {profile_name}")
    print(f"  {'Date':<12} | {'Trades':>6} | {'Accepted':>8} | {'Rejected':>8} | "
          f"{'W/L':>5} | {'P&L':>10}")
    print(f"  {'':->12}-+-{'':->6}-+-{'':->8}-+-{'':->8}-+-{'':->5}-+-{'':->10}")

    for psr in results:
        n_trades = len(psr.trade_results)
        pnl_sign = "+" if psr.total_pnl >= 0 else ""
        wl = f"{psr.win_count}/{psr.loss_count}"
        print(f"  {psr.session_date:<12} | {n_trades:>6} | {psr.entries_accepted:>8} | "
              f"{psr.entries_rejected:>8} | {wl:>5} | "
              f"{pnl_sign}${psr.total_pnl:>9,.2f}")

        for tr in psr.trade_results:
            pnl_s = "+" if tr.pnl >= 0 else ""
            print(f"    {tr.ticker:<8} ${tr.entry_price:>7.2f} -> ${tr.exit_price:>7.2f} "
                  f"({tr.exit_reason:<15}) {pnl_s}${tr.pnl:>8,.2f} "
                  f"({pnl_s}{tr.pnl_pct:.1f}%) "
                  f"MFE={tr.mfe_pct:.1f}% hold={tr.hold_minutes:.0f}min "
                  f"T{tr.tranches_filled}/3")

    total_pnl = sum(psr.total_pnl for psr in results)
    total_trades = sum(len(psr.trade_results) for psr in results)
    pnl_sign = "+" if total_pnl >= 0 else ""
    print(f"  {'TOTAL':<12} | {total_trades:>6} | {'':>8} | {'':>8} | "
          f"{'':>5} | {pnl_sign}${total_pnl:>9,.2f}")


def print_counterfactuals(
    counterfactuals: list[TradeCounterfactual],
    profiles: list[StrategyProfile],
    ticker_filter: str | None = None,
) -> None:
    """Print per-trade what-if comparison across profiles."""
    for cf in counterfactuals:
        if ticker_filter and cf.ticker != ticker_filter.upper():
            continue

        actual_str = f", Actual P&L ${cf.actual_pnl:+,.2f}" if cf.actual_pnl is not None else ""
        print(f"\n  {cf.ticker} ({cf.session_date}): Entry ${cf.entry_price:.2f}{actual_str}")
        print(f"  {'Profile':<20} | {'Enter?':>6} | {'Exit':>8} | "
              f"{'Reason':<16} | {'P&L':>10} | {'Hold':>6}")
        print(f"  {'':->20}-+-{'':->6}-+-{'':->8}-+-{'':->16}-+-{'':->10}-+-{'':->6}")

        for profile in profiles:
            outcome = cf.profile_outcomes.get(profile.name)
            if not outcome:
                continue

            if not outcome.would_enter:
                mfcs_str = f"MFCS={outcome.recomputed_mfcs:.2f}" if outcome.recomputed_mfcs is not None else "rejected"
                print(f"  {profile.name:<20} |     NO | {'---':>8} | "
                      f"{mfcs_str:<16} | {'---':>10} | {'---':>6}")
            else:
                exit_str = f"${outcome.exit_price:.2f}" if outcome.exit_price else "---"
                reason = outcome.exit_reason or "---"
                if outcome.pnl is not None:
                    pnl_sign = "+" if outcome.pnl >= 0 else ""
                    pnl_str = f"{pnl_sign}${outcome.pnl:,.2f}"
                else:
                    pnl_str = "---"
                hold_str = f"{outcome.hold_minutes:.0f}m" if outcome.hold_minutes else "---"
                print(f"  {profile.name:<20} |    YES | {exit_str:>8} | "
                      f"{reason:<16} | {pnl_str:>10} | {hold_str:>6}")
    print()


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="D119: Strategy Simulation Arena",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dates", type=str, default=None,
                        help="Comma-separated dates to test (default: all available)")
    parser.add_argument("--profiles", type=str, default=None,
                        help="Comma-separated profile names (default: all 12)")
    parser.add_argument("--rankings", action="store_true",
                        help="Show saved Elo rankings and exit")
    parser.add_argument("--counterfactual", type=str, default=None,
                        help="Show what-if for specific ticker")
    parser.add_argument("--entry-only", action="store_true",
                        help="Only re-evaluate entries (no exit replay)")
    parser.add_argument("--exit-only", action="store_true",
                        help="Accept all original entries (no entry re-eval)")
    parser.add_argument("--fetch-bars", action="store_true",
                        help="Fetch missing bars from Alpaca before running")
    parser.add_argument("--detail", type=str, default=None,
                        help="Show detailed results for a specific profile")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Verbose output")

    args = parser.parse_args()

    arena_dir = _PROJECT_ROOT / "data" / "arena"
    arena_dir.mkdir(parents=True, exist_ok=True)
    arena_state_path = arena_dir / "arena_state.json"

    # ── Rankings mode: just show saved state ──
    if args.rankings:
        if arena_state_path.exists():
            arena = StrategyArena.load(arena_state_path)
            rankings = arena.get_rankings()
            if rankings:
                # Fake a TournamentResult for printing
                from src.arena.strategy_arena import TournamentResult
                tr = TournamentResult(
                    rankings=rankings,
                    n_sessions=len(rankings[0].per_session_pnl) if rankings else 0,
                    n_profiles=len(rankings),
                    n_matchups=0,
                    per_profile_results={},
                )
                print_rankings(tr)
            else:
                print("  No rankings saved yet. Run a tournament first.")
        else:
            print("  No arena state found. Run a tournament first.")
        return

    # ── Select profiles ──
    if args.profiles:
        profile_names = [n.strip() for n in args.profiles.split(",")]
        selected_profiles = []
        for name in profile_names:
            if name in PROFILES:
                selected_profiles.append(PROFILES[name])
            else:
                print(f"  Unknown profile: {name}")
                print(f"  Available: {', '.join(PROFILES.keys())}")
                return
    else:
        selected_profiles = list(PROFILES.values())

    # Apply entry-only / exit-only flags
    if args.exit_only:
        # Clear entry overrides so all profiles accept original entries
        adjusted = []
        for p in selected_profiles:
            p2 = StrategyProfile(
                name=p.name, description=p.description,
                # Keep all exit params, clear entry params
                risk_per_trade_pct=p.risk_per_trade_pct,
                max_position_pct=p.max_position_pct,
                kelly_enabled=p.kelly_enabled,
                tier2_min_mfcs=p.tier2_min_mfcs,
                tier2_risk_pct=p.tier2_risk_pct,
                tier2_max_position_pct=p.tier2_max_position_pct,
                tier3_min_mfcs=p.tier3_min_mfcs,
                tier3_risk_pct=p.tier3_risk_pct,
                tier3_max_position_pct=p.tier3_max_position_pct,
                stop_loss_pct=p.stop_loss_pct,
                atr_multiplier=p.atr_multiplier,
                atr_floor_pct=p.atr_floor_pct,
                atr_cap_pct=p.atr_cap_pct,
                tranche_targets=p.tranche_targets,
                exit_threshold=p.exit_threshold,
                tighten_threshold=p.tighten_threshold,
                trailing_activation_pct=p.trailing_activation_pct,
                trailing_trail_distance_pct=p.trailing_trail_distance_pct,  # D121: was missing
                chandelier_multiplier=p.chandelier_multiplier,
                tranche_ratchet_ratio=p.tranche_ratchet_ratio,  # D121: was missing
                exit_signal_weight_overrides=p.exit_signal_weight_overrides,  # D121: was missing
                mfcs_scaling_denom=p.mfcs_scaling_denom,  # D121: was missing
                runner_pct=p.runner_pct,
                runner_trail_pct=p.runner_trail_pct,
                # D121 BUG-A4/A5: Previously dropped these exit-layer fields
                eod_close_hour_et=p.eod_close_hour_et,
                eod_close_minute_et=p.eod_close_minute_et,
                equity=p.equity,
            )
            adjusted.append(p2)
        selected_profiles = adjusted

    # ── Load data ──
    print("\n  Loading historical data...")
    loader = ArenaDataLoader(_PROJECT_ROOT)

    if args.dates:
        dates = [d.strip() for d in args.dates.split(",")]
    else:
        dates = loader.discover_sessions()

    if not dates:
        print("  No sessions found with both journal AND bars data.")
        print(f"  Journals dir: {_PROJECT_ROOT / 'data' / 'journals'}")
        print(f"  Bars dir: {_PROJECT_ROOT / 'data' / 'bars'}")
        return

    # Optionally fetch missing bars
    if args.fetch_bars:
        print("  Fetching missing bars from Alpaca...")
        asyncio.run(_fetch_missing_bars(dates, loader))

    sessions = []
    for date in dates:
        session = loader.load_session(date)
        if session:
            n_setups = len(session.trade_setups)
            n_bars = len(session.tickers_with_bars)
            print(f"    {date}: {n_setups} trades, {n_bars} tickers with bars "
                  f"({', '.join(sorted(session.tickers_with_bars))})")
            sessions.append(session)
        else:
            print(f"    {date}: no usable data — skipping")

    if not sessions:
        print("\n  No sessions loaded. Nothing to simulate.")
        return

    print(f"\n  Running tournament: {len(selected_profiles)} profiles x "
          f"{len(sessions)} sessions...")
    t0 = time.time()

    # ── Run tournament ──
    arena = StrategyArena()
    tournament = arena.run_tournament(sessions, selected_profiles)

    elapsed = time.time() - t0
    print(f"  Completed in {elapsed:.1f}s "
          f"({tournament.n_matchups} matchups)")

    # ── Print rankings ──
    print_rankings(tournament)

    # ── Print detail for specific profile ──
    if args.detail:
        if args.detail in tournament.per_profile_results:
            print_profile_detail(
                args.detail,
                tournament.per_profile_results[args.detail],
            )
        else:
            print(f"  Profile '{args.detail}' not found in results.")

    # ── Print all profile details ──
    if args.verbose:
        for profile_name in sorted(tournament.per_profile_results.keys()):
            print_profile_detail(
                profile_name,
                tournament.per_profile_results[profile_name],
            )

    # ── Counterfactual analysis ──
    if args.counterfactual:
        print(f"\n  Counterfactual analysis for: {args.counterfactual.upper()}")
        for session in sessions:
            results_for_session = {
                p.name: tournament.per_profile_results[p.name][
                    [psr.session_date for psr in tournament.per_profile_results[p.name]].index(session.date)
                ]
                for p in selected_profiles
                if session.date in [psr.session_date for psr in tournament.per_profile_results[p.name]]
            }
            cfs = build_counterfactuals(session, selected_profiles, results_for_session)
            print_counterfactuals(cfs, selected_profiles, ticker_filter=args.counterfactual)

    # ── Save results ──
    arena.save(arena_state_path)
    print(f"  Arena state saved to {arena_state_path}")

    # Save detailed results
    results_path = arena_dir / f"results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    _save_results(tournament, results_path)
    print(f"  Detailed results saved to {results_path}")


async def _fetch_missing_bars(dates: list[str], loader: ArenaDataLoader) -> None:
    """Fetch bars for tickers that don't have cached bars."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "replay_optimizer",
        _PROJECT_ROOT / "scripts" / "replay_optimizer.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    for date in dates:
        session = loader.load_session(date)
        if not session:
            continue
        for setup in session.trade_setups:
            if setup.ticker not in session.tickers_with_bars:
                print(f"    Fetching bars for {setup.ticker} on {date}...")
                try:
                    await mod.fetch_bars(setup.ticker, date)
                except Exception as e:
                    print(f"    Failed: {e}")


def _save_results(tournament: TournamentResult, path: Path) -> None:
    """Save detailed tournament results to JSON."""
    data = {
        "n_sessions": tournament.n_sessions,
        "n_profiles": tournament.n_profiles,
        "n_matchups": tournament.n_matchups,
        "rankings": [v.to_dict() for v in tournament.rankings],
        "per_profile": {},
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }

    for profile_name, results in tournament.per_profile_results.items():
        data["per_profile"][profile_name] = {
            "sessions": [
                {
                    "date": psr.session_date,
                    "total_pnl": round(psr.total_pnl, 2),
                    "entries_accepted": psr.entries_accepted,
                    "entries_rejected": psr.entries_rejected,
                    "win_count": psr.win_count,
                    "loss_count": psr.loss_count,
                    "trades": [
                        {
                            "ticker": tr.ticker,
                            "entry": tr.entry_price,
                            "exit": tr.exit_price,
                            "reason": tr.exit_reason,
                            "pnl": tr.pnl,
                            "pnl_pct": tr.pnl_pct,
                            "mfe_pct": tr.mfe_pct,
                            "mae_pct": tr.mae_pct,
                            "hold_min": tr.hold_minutes,
                            "tranches": tr.tranches_filled,
                            "tier": tr.kelly_tier,
                        }
                        for tr in psr.trade_results
                    ],
                }
                for psr in results
            ],
        }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


if __name__ == "__main__":
    main()
