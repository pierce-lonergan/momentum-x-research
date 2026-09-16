#!/usr/bin/env python3
"""D150: Compare baseline vs aggressive tier-based position sizing across all historical trades."""

import sys, os, asyncio, time, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arena.decision_replay import load_candidates_from_journals, replay_decisions
from arena.runner import _simulate_journal_trades
from arena.harness import ArenaConfig, ArenaInstance

PROJECT = Path(__file__).resolve().parent.parent.parent
ARENA = PROJECT / "mx-arena"
ENRICHMENT_CACHE = ARENA / "data" / "enrichment_cache.json"

DATES = [
    '2026-02-10', '2026-02-11', '2026-02-17', '2026-02-18', '2026-02-19',
    '2026-02-24', '2026-02-25', '2026-03-03', '2026-03-04', '2026-03-05',
    '2026-03-06', '2026-03-09', '2026-03-12', '2026-03-18', '2026-03-19',
    '2026-03-20', '2026-03-26',
]

# Bar-1 exit config (proven optimal strategy)
BASE_CFG = {
    'time_exit_bars': [1],
    'time_exit_pcts': [1.00],
    'mfcs_buy_threshold': 0.15,
    'max_positions': 8,
}

# D150 tier config (matches production alpaca_executor.py)
TIER_CONFIG = {
    'tier1_float_max': 5_000_000,
    'tier1_gap_min': 0.20,
    'tier1_rvol_min': 5.0,
    'tier1_position_pct': 0.50,
    'tier2_float_max': 20_000_000,
    'tier2_gap_min': 0.10,
    'tier2_rvol_min': 3.0,
    'tier2_position_pct': 0.30,
    'tier3_position_pct': 0.15,
}


def load_enrichment_cache():
    """Load cached enrichment data (float_shares, market_cap, etc.)."""
    if ENRICHMENT_CACHE.exists():
        with open(ENRICHMENT_CACHE) as f:
            return json.load(f)
    return {}


def save_enrichment_cache(cache):
    """Save enrichment cache to disk."""
    ENRICHMENT_CACHE.parent.mkdir(parents=True, exist_ok=True)
    with open(ENRICHMENT_CACHE, "w") as f:
        json.dump(cache, f, indent=2)


async def enrich_missing_tickers(tickers, cache):
    """Enrich tickers not in cache using Finnhub."""
    missing = [t for t in tickers if t not in cache]
    if not missing:
        return cache

    print(f"  Enriching {len(missing)} tickers via Finnhub...")
    from src.data.enrichment import CandidateEnricher
    key = os.environ.get("FINNHUB_API_KEY")
    if not key:
        raise SystemExit("FINNHUB_API_KEY is not set. Export it; this script never embeds a key.")
    enricher = CandidateEnricher(finnhub_api_key=key)

    for ticker in missing:
        try:
            data = await enricher.enrich(ticker, current_price=5.0)
            cache[ticker] = {
                "float_shares": data.float_shares,
                "shares_outstanding": data.shares_outstanding,
                "market_cap": data.market_cap,
                "short_interest_pct": data.short_interest_pct,
            }
        except Exception as e:
            print(f"    {ticker}: enrichment failed ({e})")
            cache[ticker] = {}
        time.sleep(0.15)  # Rate limit

    save_enrichment_cache(cache)
    return cache


def run_simulation(dates, param_overrides, enrichment_cache=None):
    """Run all trades across dates with given params."""
    all_trades = []
    for date in dates:
        cands = load_candidates_from_journals(date, str(PROJECT / "data/journals"))
        buys = replay_decisions(cands, {"mfcs_buy_threshold": param_overrides.get("mfcs_buy_threshold", 0.15)})
        if not buys:
            continue

        # Inject enrichment data into buy dicts
        if enrichment_cache:
            for b in buys:
                e = enrichment_cache.get(b["ticker"], {})
                if e.get("float_shares") is not None and b.get("float_shares") is None:
                    b["float_shares"] = e["float_shares"]
                if e.get("market_cap") is not None and b.get("market_cap") is None:
                    b["market_cap"] = e["market_cap"]

        config = ArenaConfig(
            date=date,
            symbols=[b["ticker"] for b in buys],
            data_dir=str(ARENA / "data/historical"),
        )
        inst = ArenaInstance(config)
        inst.data_engine.json_bars_dir = PROJECT / "data/bars"
        inst.load_data()
        trades = _simulate_journal_trades(inst, buys, param_overrides)
        for t, b in zip(trades, buys[:len(trades)]):
            t["_date"] = date
            t["gap_pct"] = b.get("gap_pct", 0)
            t["rvol"] = b.get("rvol", 0)
        all_trades.extend(trades)
    return all_trades


def dollar_pnl(trade, equity=100_000):
    """Compute dollar P&L for a trade given account equity.

    Arena P&L is normalized per-share (pnl = price_move / entry_price-ish).
    Dollar P&L = pnl_pct * equity * tier_pct (position size as fraction of equity).
    """
    tier_pct = trade.get("tier_pct", 0.15) or 0.15
    pnl_pct = trade.get("pnl_pct", 0)
    return pnl_pct * equity * tier_pct


def analyze(label, trades, use_dollar=False, equity=100_000):
    """Compute aggregate stats for a set of trades."""
    if not trades:
        return {}

    if use_dollar:
        pnls = [dollar_pnl(t, equity) for t in trades]
    else:
        pnls = [t["pnl"] for t in trades]

    total_pnl = sum(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    win_rate = len(wins) / len(pnls) if pnls else 0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    worst = min(pnls) if pnls else 0
    best = max(pnls) if pnls else 0
    outliers = [t for t in trades if t["pnl"] > 0 and t.get("fill_price", 0) > 0
                and t["pnl"] / t["fill_price"] > 0.03]
    return {
        "n": len(trades),
        "total_pnl": total_pnl,
        "avg_pnl": total_pnl / len(trades),
        "win_rate": win_rate,
        "profit_factor": pf,
        "worst": worst,
        "best": best,
        "outliers": len(outliers),
        "outlier_rate": len(outliers) / len(trades) if trades else 0,
    }


async def async_main():
    print("=" * 80)
    print("D150: AGGRESSIVE TIER SIZING -- ARENA COMPARISON")
    print("=" * 80)

    # Step 0: Load/build enrichment cache (float data from Finnhub)
    enrichment_cache = load_enrichment_cache()

    # Collect all unique tickers first
    all_tickers = set()
    for date in DATES:
        cands = load_candidates_from_journals(date, str(PROJECT / "data/journals"))
        buys = replay_decisions(cands, {"mfcs_buy_threshold": 0.15})
        for b in buys:
            all_tickers.add(b["ticker"])

    print(f"\n{len(all_tickers)} unique tickers across {len(DATES)} dates")
    enrichment_cache = await enrich_missing_tickers(list(all_tickers), enrichment_cache)
    cached_count = sum(1 for t in all_tickers if enrichment_cache.get(t, {}).get("float_shares"))
    print(f"Enrichment cache: {cached_count}/{len(all_tickers)} tickers have float data")

    # Run baseline (uniform sizing)
    print("\nRunning BASELINE (uniform 100 shares)...")
    baseline_trades = run_simulation(DATES, BASE_CFG, enrichment_cache)

    # Run aggressive (tier sizing)
    print("Running AGGRESSIVE (tier-scaled sizing)...")
    aggressive_cfg = {**BASE_CFG, 'tier_config': TIER_CONFIG}
    aggressive_trades = run_simulation(DATES, aggressive_cfg, enrichment_cache)

    # ── Aggregate comparison (Dollar P&L on $100k account) ──
    EQUITY = 100_000

    # For baseline: all trades at 15% uniform sizing
    for t in baseline_trades:
        t["tier_pct"] = 0.15  # Uniform baseline
    b = analyze("Baseline", baseline_trades, use_dollar=True, equity=EQUITY)

    # For aggressive: tier_pct already set by simulation
    # But we need to ensure tier_pct is set (it's in the trade dict from runner)
    for t in aggressive_trades:
        if not t.get("tier_pct"):
            t["tier_pct"] = 0.15
    a = analyze("Aggressive", aggressive_trades, use_dollar=True, equity=EQUITY)

    print("\n" + "=" * 80)
    print(f"AGGREGATE COMPARISON (Dollar P&L on ${EQUITY:,.0f} account)")
    print("=" * 80)
    print(f"  {'Metric':<20s} {'Baseline':>14s} {'Aggressive':>14s} {'Delta':>14s}")
    print(f"  {'-'*20} {'-'*14} {'-'*14} {'-'*14}")
    print(f"  {'Trades':<20s} {b['n']:>14d} {a['n']:>14d}")
    print(f"  {'Total P&L':<20s} ${b['total_pnl']:>+12.2f} ${a['total_pnl']:>+12.2f} ${a['total_pnl']-b['total_pnl']:>+12.2f}")
    print(f"  {'Avg P&L/trade':<20s} ${b['avg_pnl']:>+12.2f} ${a['avg_pnl']:>+12.2f} ${a['avg_pnl']-b['avg_pnl']:>+12.2f}")
    print(f"  {'Win Rate':<20s} {b['win_rate']:>13.1%} {a['win_rate']:>13.1%}")
    print(f"  {'Profit Factor':<20s} {b['profit_factor']:>14.2f} {a['profit_factor']:>14.2f}")
    print(f"  {'Worst Trade':<20s} ${b['worst']:>+12.2f} ${a['worst']:>+12.2f}")
    print(f"  {'Best Trade':<20s} ${b['best']:>+12.2f} ${a['best']:>+12.2f}")
    print(f"  {'Outliers (>3%)':<20s} {b['outliers']:>14d} {a['outliers']:>14d}")
    print(f"  {'Outlier Rate':<20s} {b['outlier_rate']:>13.1%} {a['outlier_rate']:>13.1%}")

    # ── Per-tier breakdown (aggressive only) ──
    print("\n" + "=" * 80)
    print("PER-TIER BREAKDOWN (Aggressive)")
    print("=" * 80)

    for tier_num in [1, 2, 3]:
        tier_trades = [t for t in aggressive_trades if t.get("tier") == tier_num]
        if not tier_trades:
            print(f"\n  Tier {tier_num}: 0 trades")
            continue
        s = analyze(f"Tier {tier_num}", tier_trades, use_dollar=True, equity=EQUITY)
        pct_label = {1: "50%", 2: "30%", 3: "15%"}[tier_num]
        qty_label = {1: "333", 2: "200", 3: "100"}[tier_num]
        print(f"\n  Tier {tier_num} ({pct_label} equity, {qty_label} shares):")
        print(f"    Trades: {s['n']}")
        print(f"    Total P&L: ${s['total_pnl']:+,.2f}")
        print(f"    Avg P&L:   ${s['avg_pnl']:+,.2f}")
        print(f"    Win Rate:  {s['win_rate']:.1%}")
        print(f"    Outliers:  {s['outliers']} ({s['outlier_rate']:.0%})")
        print(f"    Best:      ${s['best']:+,.2f}  Worst: ${s['worst']:+,.2f}")

    # ── Per-trade detail ──
    print("\n" + "=" * 80)
    print("PER-TRADE DETAIL")
    print("=" * 80)
    print(f"  {'Date':<12s} {'Ticker':<7s} {'Tier':>4s} {'Fill$':>7s} "
          f"{'Base $':>10s} {'Aggr $':>10s} {'Delta $':>10s} {'Float':>8s} {'Gap%':>6s} {'RVOL':>5s}")
    print(f"  {'-'*12} {'-'*7} {'-'*4} {'-'*7} {'-'*10} {'-'*10} {'-'*10} {'-'*8} {'-'*6} {'-'*5}")

    # Match baseline and aggressive trades by ticker+date
    baseline_map = {}
    for t in baseline_trades:
        key = (t.get("_date", ""), t["ticker"])
        baseline_map[key] = t

    for t in sorted(aggressive_trades, key=lambda x: (x.get("_date", ""), x["ticker"])):
        key = (t.get("_date", ""), t["ticker"])
        bt = baseline_map.get(key)
        b_dollar = dollar_pnl(bt, EQUITY) if bt else 0
        a_dollar = dollar_pnl(t, EQUITY)
        delta = a_dollar - b_dollar
        fl = t.get("float_shares")
        fl_str = f"{fl/1e6:.1f}M" if fl else "?"
        gap = abs(t.get("gap_pct", 0)) if "gap_pct" in t else 0
        rvol = t.get("rvol", 0)
        tier = t.get("tier", "?")
        print(f"  {t.get('_date','?'):<12s} {t['ticker']:<7s} {tier:>4} "
              f"${t['fill_price']:>6.2f} {b_dollar:>+10.2f} {a_dollar:>+10.2f} {delta:>+10.2f} "
              f"{fl_str:>8s} {gap*100:>5.0f}% {rvol:>5.1f}")

    # ── P&L amplification ratio ──
    print("\n" + "=" * 80)
    print("AMPLIFICATION ANALYSIS")
    print("=" * 80)
    if b['total_pnl'] != 0:
        amp = a['total_pnl'] / b['total_pnl']
        print(f"  Aggressive / Baseline P&L ratio: {amp:.2f}x")
        print(f"  Baseline P&L:   ${b['total_pnl']:+,.2f}")
        print(f"  Aggressive P&L: ${a['total_pnl']:+,.2f}")
        print(f"  P&L Delta:      ${a['total_pnl'] - b['total_pnl']:+,.2f}")
    else:
        print("  Baseline P&L is zero -- cannot compute ratio")

    # Worst-case analysis
    print("\n  Worst-case risk (aggressive, dollar P&L):")
    worst_trades = sorted(aggressive_trades, key=lambda t: dollar_pnl(t, EQUITY))[:5]
    for t in worst_trades:
        tier = t.get("tier", "?")
        d_pnl = dollar_pnl(t, EQUITY)
        print(f"    {t['ticker']:6s} Tier {tier} ({t.get('tier_pct', 0.15):.0%} eq) "
              f"P&L=${d_pnl:+,.2f} (${t['fill_price']:.2f} -> stop)")


if __name__ == "__main__":
    asyncio.run(async_main())
