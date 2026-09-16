#!/usr/bin/env python3
"""Enrich all 79 historical trades with Finnhub + yfinance data and analyze correlations."""

import sys, os, asyncio, json, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arena.decision_replay import load_candidates_from_journals, replay_decisions
from arena.runner import _simulate_journal_trades
from arena.harness import ArenaConfig, ArenaInstance
from src.data.enrichment import CandidateEnricher

dates = ['2026-02-10','2026-02-11','2026-02-17','2026-02-18','2026-02-19',
         '2026-02-24','2026-02-25','2026-03-03','2026-03-04','2026-03-05',
         '2026-03-06','2026-03-09','2026-03-12','2026-03-18','2026-03-19',
         '2026-03-20','2026-03-26']

bar1_cfg = {'time_exit_bars': [1], 'time_exit_pcts': [1.00],
            'mfcs_buy_threshold': 0.15, 'max_positions': 8}

PROJECT = Path(__file__).resolve().parent.parent.parent
ARENA = PROJECT / "mx-arena"


async def main():
    key = os.environ.get("FINNHUB_API_KEY")
    if not key:
        raise SystemExit("FINNHUB_API_KEY is not set. Export it; this script never embeds a key.")
    enricher = CandidateEnricher(finnhub_api_key=key)

    # Step 1: Run all trades
    all_trades = []
    for date in dates:
        cands = load_candidates_from_journals(date, str(PROJECT / "data/journals"))
        buys = replay_decisions(cands, {"mfcs_buy_threshold": 0.15})
        if not buys:
            continue
        config = ArenaConfig(date=date, symbols=[b["ticker"] for b in buys],
                             data_dir=str(ARENA / "data/historical"))
        inst = ArenaInstance(config)
        inst.data_engine.json_bars_dir = PROJECT / "data/bars"
        inst.load_data()
        trades = _simulate_journal_trades(inst, buys, bar1_cfg)
        for t, b in zip(trades, buys[:len(trades)]):
            t["_gap"] = abs(b.get("gap_pct", 0))
            t["_rvol"] = b.get("rvol", 1)
            t["_date"] = date
            bar1_ex = [tr for tr in t.get("tranches", []) if tr.get("type", "").startswith("time_T")]
            fp = t.get("fill_price", 0)
            t["_bar1_ret"] = (bar1_ex[0]["price"] - fp) / fp if bar1_ex and fp > 0 else 0
        all_trades.extend(trades)

    # Step 2: Enrich unique tickers
    unique_tickers = sorted(set(t["ticker"] for t in all_trades))
    print(f"Enriching {len(unique_tickers)} unique tickers from {len(all_trades)} trades...")

    cache = {}
    for ticker in unique_tickers:
        data = await enricher.enrich(ticker, current_price=5.0)
        cache[ticker] = data
        time.sleep(0.15)

    # Step 3: Merge
    for t in all_trades:
        e = cache.get(t["ticker"])
        if e:
            t["_float"] = e.float_shares
            t["_outstanding"] = e.shares_outstanding
            t["_mktcap"] = e.market_cap
            t["_short_pct"] = e.short_interest_pct
            t["_dtc"] = e.days_to_cover
            t["_news_count"] = e.social_velocity_30m
            t["_sentiment"] = e.sentiment_score

    # Step 4: Correlation
    print("\n" + "=" * 70)
    print("CORRELATION: ENRICHMENT FEATURES vs BAR-1 RETURN")
    print("=" * 70)

    def pearson(xs, ys):
        n = len(xs)
        if n < 5:
            return 0
        mx, my = sum(xs)/n, sum(ys)/n
        cov = sum((x-mx)*(y-my) for x, y in zip(xs, ys)) / n
        sx = (sum((x-mx)**2 for x in xs)/n)**0.5
        sy = (sum((y-my)**2 for y in ys)/n)**0.5
        return cov/(sx*sy) if sx > 0 and sy > 0 else 0

    for field, label in [("_float", "Float"), ("_outstanding", "Outstanding"),
                          ("_mktcap", "MarketCap"), ("_short_pct", "Short%"),
                          ("_news_count", "NewsCount"), ("_rvol", "RVOL"),
                          ("_gap", "Gap%")]:
        pairs = [(t[field], t["_bar1_ret"]) for t in all_trades
                 if t.get(field) is not None and t[field] > 0 and t["_bar1_ret"] != 0]
        if len(pairs) >= 5:
            xs, ys = zip(*pairs)
            c = pearson(list(xs), list(ys))
            direction = "NEG (lower=better)" if c < -0.05 else "POS (higher=better)" if c > 0.05 else "NONE"
            print(f"  {label:12s}: corr={c:+.4f} (n={len(pairs)}) {direction}")

    # Step 5: Outlier analysis
    print("\n" + "=" * 70)
    print("OUTLIER ENRICHMENT PROFILE")
    print("=" * 70)

    outliers = sorted(all_trades, key=lambda t: t.get("pnl", 0), reverse=True)[:5]
    rest = all_trades[5:]

    print("\nTop 5 outliers:")
    for t in outliers:
        fl = t.get("_float")
        fl_str = f"{fl/1e6:.1f}M" if fl else "?"
        print(f"  {t['ticker']:6s} pnl=${t['pnl']:+.4f} bar1={t['_bar1_ret']:.1%} "
              f"float={fl_str} short={t.get('_short_pct', '?')} mktcap=${t.get('_mktcap', 0)/1e6:.0f}M")

    def avg(trades, field):
        vals = [t[field] for t in trades if t.get(field) and t[field] > 0]
        return sum(vals)/len(vals) if vals else 0

    print("\nOutlier vs Non-Outlier:")
    for field, label, fmt in [("_float", "Float", ",.0f"), ("_mktcap", "MktCap", ",.0f"),
                               ("_short_pct", "Short%", ".3f")]:
        o = avg(outliers, field)
        n = avg(rest, field)
        r = o/n if n > 0 else 0
        print(f"  {label:8s}: outliers={o:{fmt}}  others={n:{fmt}}  ratio={r:.2f}x")

    # Step 6: Filter-based P&L
    print("\n" + "=" * 70)
    print("P&L WITH ENRICHMENT-BASED FILTERS")
    print("=" * 70)

    filters = [
        ("all (baseline)", lambda t: True),
        ("float < 50M", lambda t: t.get("_float") and t["_float"] < 50e6),
        ("float < 20M", lambda t: t.get("_float") and t["_float"] < 20e6),
        ("float < 10M", lambda t: t.get("_float") and t["_float"] < 10e6),
        ("float < 5M", lambda t: t.get("_float") and t["_float"] < 5e6),
        ("short > 5%", lambda t: t.get("_short_pct") and t["_short_pct"] > 0.05),
        ("short > 10%", lambda t: t.get("_short_pct") and t["_short_pct"] > 0.10),
        ("float<20M + short>5%", lambda t: (t.get("_float") and t["_float"] < 20e6 and
                                             t.get("_short_pct") and t["_short_pct"] > 0.05)),
        ("mktcap < $50M", lambda t: t.get("_mktcap") and t["_mktcap"] < 50e6),
        ("mktcap < $100M", lambda t: t.get("_mktcap") and t["_mktcap"] < 100e6),
    ]

    for label, fn in filters:
        filtered = [t for t in all_trades if fn(t)]
        if filtered:
            pnl = sum(t.get("pnl", 0) for t in filtered)
            outlier_n = sum(1 for t in filtered if t["_bar1_ret"] > 0.03)
            outlier_pct = outlier_n / len(filtered) if filtered else 0
            print(f"  {label:25s}: n={len(filtered):3d} P&L=${pnl:+.2f} "
                  f"outliers={outlier_n} ({outlier_pct:.0%}) avg=${pnl/len(filtered):+.4f}")


if __name__ == "__main__":
    asyncio.run(main())
