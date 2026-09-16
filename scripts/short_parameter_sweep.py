"""
D161 Short Selling -- Standalone Parameter Sweep
================================================
Completely self-contained: no src/ imports, no arena data files.
All stock data hard-coded from session notes (March 30-31 2026).

Run:
    python scripts/short_parameter_sweep.py

Logic:
  - A stock qualifies as a short if: faller_score >= threshold AND
    rvol >= min_rvol AND dolvol >= min_dolvol AND gap >= min_gap_pct
  - P&L (as % of entry):
      - If high >= entry*(1+stop_pct): STOP-OUT -> P&L = -stop_pct
      - Elif low <= entry*(1-0.10): T3 WIN   -> P&L = +10%
      - Elif low <= entry*(1-0.06): T2 WIN   -> P&L = +6%
      - Elif low <= entry*(1-0.03): T1 WIN   -> P&L = +3%
      - Else: NO HIT                          -> P&L = 0%
  - Stop is checked first (conservative: assume stop triggers before targets
    if both conditions are true on the same day)
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass


# -- Known universe: authoritative data from session notes ---------------------
# Fields: ticker, date, entry, low, high, gap_pct, rvol, dolvol, faller_score
# dolvol=None means data unavailable (stock will always fail dolvol gate)

STOCKS = [
    # -- 2026-03-30 ------------------------------------------------------------
    dict(
        ticker="ARTL", date="2026-03-30",
        entry=7.68,  low=3.48,  high=8.22,
        gap_pct=1.408,   # 140.8%
        rvol=37.7,
        dolvol=640_000,  # $640K
        faller_score=0.75,
        notes="Manipulation/promoter. 37.7x RVOL. Faded -55% from open.",
    ),
    dict(
        ticker="SST",  date="2026-03-30",
        entry=3.20,  low=2.08,  high=3.39,
        gap_pct=1.336,   # 133.6%
        rvol=2071.0,
        dolvol=138_000,  # $138K -- known liquidity problem
        faller_score=0.80,
        notes="Bearish news. 2071x RVOL but only $138K dolvol.",
    ),
    dict(
        ticker="EEIQ", date="2026-03-30",
        entry=9.60,  low=7.78,  high=10.56,
        gap_pct=0.375,   # 37.5%
        rvol=6.4,
        dolvol=None,     # not provided -- will fail dolvol gate
        faller_score=0.35,
        notes="Low faller score 0.35 -- long candidate, not a short.",
    ),
    dict(
        ticker="ELAB", date="2026-03-30",
        entry=3.775, low=2.45,  high=4.53,
        gap_pct=1.26,    # 126%
        rvol=4.6,
        dolvol=None,     # not provided
        faller_score=0.10,
        notes="Real catalyst. faller_score=0.10 -- strong long candidate.",
    ),
    # -- 2026-03-31 ------------------------------------------------------------
    dict(
        ticker="BFRG", date="2026-03-31",
        entry=1.18,  low=0.77,  high=1.24,
        gap_pct=1.32,    # 132%
        rvol=1874.0,
        dolvol=271_000,  # $271K
        faller_score=0.255,
        notes="Faller score 0.255 -- long trade, stopped at $0.77.",
    ),
    dict(
        ticker="ASTC", date="2026-03-31",
        entry=4.16,  low=2.70,  high=4.37,
        gap_pct=0.75,    # 75%
        rvol=588.0,
        dolvol=798_000,  # $798K
        faller_score=0.035,
        notes="Faller score 0.035 -- strong long. Stopped at $2.70.",
    ),
]

# Fixed targets (D161 standard -- not swept)
TARGETS = [0.03, 0.06, 0.10]  # T1, T2, T3 -- % below entry

# Current D161 defaults
CURRENT_DEFAULTS = dict(
    min_faller_score=0.65,
    min_dolvol=500_000,
    min_gap_pct=0.20,
    stop_pct=0.35,
    min_rvol=3.0,
)


# -- Core simulation ------------------------------------------------------------

@dataclass
class TradeResult:
    ticker: str
    date: str
    entry: float
    low: float
    high: float
    stop_price: float
    qualified: bool
    reject_reason: str   # "" if qualified
    hit_stop: bool
    t1_hit: bool
    t2_hit: bool
    t3_hit: bool
    pnl_pct: float       # fraction, e.g. 0.10 = +10%


def simulate_stock(stock: dict, min_faller_score: float, min_dolvol: float,
                   min_gap_pct: float, stop_pct: float, min_rvol: float) -> TradeResult:
    entry = stock["entry"]
    low   = stock["low"]
    high  = stock["high"]
    stop_price = entry * (1 + stop_pct)

    # Qualification gates
    if stock["faller_score"] < min_faller_score:
        return TradeResult(
            ticker=stock["ticker"], date=stock["date"],
            entry=entry, low=low, high=high, stop_price=stop_price,
            qualified=False, reject_reason=f"faller_score={stock['faller_score']:.3f}<{min_faller_score:.2f}",
            hit_stop=False, t1_hit=False, t2_hit=False, t3_hit=False, pnl_pct=0.0,
        )
    if stock["dolvol"] is None or stock["dolvol"] < min_dolvol:
        dv = stock["dolvol"] or 0
        return TradeResult(
            ticker=stock["ticker"], date=stock["date"],
            entry=entry, low=low, high=high, stop_price=stop_price,
            qualified=False, reject_reason=f"dolvol=${dv/1000:.0f}K<${min_dolvol/1000:.0f}K",
            hit_stop=False, t1_hit=False, t2_hit=False, t3_hit=False, pnl_pct=0.0,
        )
    if stock["rvol"] < min_rvol:
        return TradeResult(
            ticker=stock["ticker"], date=stock["date"],
            entry=entry, low=low, high=high, stop_price=stop_price,
            qualified=False, reject_reason=f"rvol={stock['rvol']:.1f}x<{min_rvol:.1f}x",
            hit_stop=False, t1_hit=False, t2_hit=False, t3_hit=False, pnl_pct=0.0,
        )
    if stock["gap_pct"] < min_gap_pct:
        return TradeResult(
            ticker=stock["ticker"], date=stock["date"],
            entry=entry, low=low, high=high, stop_price=stop_price,
            qualified=False, reject_reason=f"gap={stock['gap_pct']:.0%}<{min_gap_pct:.0%}",
            hit_stop=False, t1_hit=False, t2_hit=False, t3_hit=False, pnl_pct=0.0,
        )

    # Stop checked first (conservative)
    if high >= stop_price:
        return TradeResult(
            ticker=stock["ticker"], date=stock["date"],
            entry=entry, low=low, high=high, stop_price=stop_price,
            qualified=True, reject_reason="",
            hit_stop=True, t1_hit=False, t2_hit=False, t3_hit=False,
            pnl_pct=-stop_pct,
        )

    # Target cascade (best target hit wins)
    t3_price = entry * (1 - TARGETS[2])
    t2_price = entry * (1 - TARGETS[1])
    t1_price = entry * (1 - TARGETS[0])

    t3_hit = low <= t3_price
    t2_hit = low <= t2_price
    t1_hit = low <= t1_price

    if t3_hit:
        pnl = TARGETS[2]
    elif t2_hit:
        pnl = TARGETS[1]
    elif t1_hit:
        pnl = TARGETS[0]
    else:
        pnl = 0.0

    return TradeResult(
        ticker=stock["ticker"], date=stock["date"],
        entry=entry, low=low, high=high, stop_price=stop_price,
        qualified=True, reject_reason="",
        hit_stop=False, t1_hit=t1_hit, t2_hit=t2_hit, t3_hit=t3_hit,
        pnl_pct=pnl,
    )


def evaluate_params(min_faller_score: float, min_dolvol: float,
                    min_gap_pct: float, stop_pct: float, min_rvol: float) -> dict:
    results = [
        simulate_stock(s, min_faller_score, min_dolvol, min_gap_pct, stop_pct, min_rvol)
        for s in STOCKS
    ]
    shorts = [r for r in results if r.qualified]
    n = len(shorts)
    if n == 0:
        return dict(
            min_faller_score=min_faller_score, min_dolvol=min_dolvol,
            min_gap_pct=min_gap_pct, stop_pct=stop_pct, min_rvol=min_rvol,
            num_shorts=0, total_pnl=0.0, win_rate=0.0, avg_pnl=0.0,
            n_wins=0, n_stops=0, results=results,
        )

    wins   = [r for r in shorts if r.pnl_pct > 0]
    stops  = [r for r in shorts if r.hit_stop]
    total  = sum(r.pnl_pct for r in shorts)
    return dict(
        min_faller_score=min_faller_score, min_dolvol=min_dolvol,
        min_gap_pct=min_gap_pct, stop_pct=stop_pct, min_rvol=min_rvol,
        num_shorts=n, total_pnl=total, win_rate=len(wins)/n,
        avg_pnl=total/n, n_wins=len(wins), n_stops=len(stops),
        results=results,
    )


# -- Formatting helpers ---------------------------------------------------------

def fmt_pct(v: float) -> str:
    return f"{v:+.1%}"

def fmt_dolvol(v: float) -> str:
    if v >= 1_000_000:
        return f"${v/1_000_000:.1f}M"
    return f"${v/1000:.0f}K"

def params_label(p: dict) -> str:
    return (
        f"faller>={p['min_faller_score']:.2f}  "
        f"dolvol>={fmt_dolvol(p['min_dolvol'])}  "
        f"gap>={p['min_gap_pct']:.0%}  "
        f"rvol>={p['min_rvol']:.0f}x  "
        f"stop={p['stop_pct']:.0%}"
    )


# -- Main sweep -----------------------------------------------------------------

def run_sweep() -> None:
    W = 100
    print("=" * W)
    print("  D161 SHORT SELLING -- STANDALONE PARAMETER SWEEP")
    print("  Sessions: 2026-03-30, 2026-03-31  |  Stocks: 6  |  Targets: T1=-3% T2=-6% T3=-10%")
    print("=" * W)

    # -- Universe summary ------------------------------------------------------
    print()
    print("UNIVERSE")
    print("-" * W)
    print(f"  {'Ticker':<6} {'Date':<12} {'Entry':>7} {'Low':>6} {'High':>6}  "
          f"{'Gap':>7}  {'RVOL':>8}  {'DolVol':>10}  {'Faller':>8}  Notes")
    print("  " + "-" * (W - 2))
    for s in STOCKS:
        dv = fmt_dolvol(s["dolvol"]) if s["dolvol"] else "N/A"
        print(
            f"  {s['ticker']:<6} {s['date']:<12} "
            f"${s['entry']:>5.3f}  ${s['low']:>5.2f}  ${s['high']:>5.2f}  "
            f"{s['gap_pct']:>7.1%}  {s['rvol']:>7.1f}x  {dv:>10}  "
            f"{s['faller_score']:>8.3f}  {s['notes']}"
        )
    print()

    # -- Full grid sweep -------------------------------------------------------
    param_grid = dict(
        min_faller_score=[0.55, 0.60, 0.65, 0.70, 0.75],
        min_dolvol=[100_000, 250_000, 500_000, 1_000_000],
        min_gap_pct=[0.10, 0.15, 0.20, 0.30],
        stop_pct=[0.20, 0.25, 0.30, 0.35],
        min_rvol=[2.0, 3.0, 5.0, 10.0],
    )

    total_combos = 1
    for v in param_grid.values():
        total_combos *= len(v)
    print(f"FULL GRID SWEEP  ({total_combos:,} combinations)")
    print("-" * W)

    all_results = []
    keys = list(param_grid.keys())
    for combo in itertools.product(*param_grid.values()):
        p = dict(zip(keys, combo))
        r = evaluate_params(**p)
        all_results.append(r)

    # Sort by total_pnl desc, then win_rate desc, then fewest stops
    all_results.sort(key=lambda x: (-x["total_pnl"], -x["win_rate"], x["n_stops"]))

    # Top 20
    print()
    print("TOP 20 PARAMETER SETS (sorted by total P&L)")
    print("-" * W)
    print(
        f"  {'#':>3}  {'Faller':>7}  {'DolVol':>8}  {'Gap':>5}  {'RVOL':>5}  {'Stop':>5}  "
        f"{'Shorts':>6}  {'WinRate':>8}  {'AvgP&L':>8}  {'TotalP&L':>9}  {'Wins':>5}  {'Stops':>6}"
    )
    print("  " + "-" * (W - 2))
    for i, r in enumerate(all_results[:20], 1):
        print(
            f"  {i:>3}  "
            f"{r['min_faller_score']:>7.2f}  "
            f"{fmt_dolvol(r['min_dolvol']):>8}  "
            f"{r['min_gap_pct']:>5.0%}  "
            f"{r['min_rvol']:>4.0f}x  "
            f"{r['stop_pct']:>5.0%}  "
            f"{r['num_shorts']:>6}  "
            f"{r['win_rate']:>8.1%}  "
            f"{fmt_pct(r['avg_pnl']):>8}  "
            f"{fmt_pct(r['total_pnl']):>9}  "
            f"{r['n_wins']:>5}  "
            f"{r['n_stops']:>6}"
        )

    # -- Bottom 5 (worst -- informational) -------------------------------------
    non_zero = [r for r in all_results if r["num_shorts"] > 0]
    if non_zero:
        print()
        print("WORST 5 (non-zero shorts, sorted by total P&L ascending)")
        print("-" * W)
        non_zero_sorted = sorted(non_zero, key=lambda x: x["total_pnl"])
        for i, r in enumerate(non_zero_sorted[:5], 1):
            print(
                f"  {i:>3}  "
                f"{r['min_faller_score']:>7.2f}  "
                f"{fmt_dolvol(r['min_dolvol']):>8}  "
                f"{r['min_gap_pct']:>5.0%}  "
                f"{r['min_rvol']:>4.0f}x  "
                f"{r['stop_pct']:>5.0%}  "
                f"{r['num_shorts']:>6}  "
                f"{r['win_rate']:>8.1%}  "
                f"{fmt_pct(r['avg_pnl']):>8}  "
                f"{fmt_pct(r['total_pnl']):>9}  "
                f"{r['n_wins']:>5}  "
                f"{r['n_stops']:>6}"
            )

    # -- Current defaults vs optimal -------------------------------------------
    print()
    print("=" * W)
    print("CURRENT DEFAULTS vs OPTIMAL")
    print("=" * W)

    current = evaluate_params(**CURRENT_DEFAULTS)
    optimal = all_results[0]

    print()
    print("CURRENT DEFAULTS:")
    print(f"  {params_label(current)}")
    if current["num_shorts"] == 0:
        print("  -> 0 shorts qualified. No P&L.")
    else:
        print(f"  -> {current['num_shorts']} short(s) qualified")
        for r in current["results"]:
            if r.qualified:
                outcome = "STOP" if r.hit_stop else f"T{'3' if r.t3_hit else '2' if r.t2_hit else '1' if r.t1_hit else '0'}"
                print(f"     {r.ticker}: entry=${r.entry:.2f}  stop=${r.stop_price:.2f}  "
                      f"low=${r.low:.2f}  high=${r.high:.2f}  "
                      f"outcome={outcome}  P&L={fmt_pct(r.pnl_pct)}")
        print(f"  -> Win rate: {current['win_rate']:.1%}  Avg P&L: {fmt_pct(current['avg_pnl'])}  "
              f"Total P&L: {fmt_pct(current['total_pnl'])}")

    print()
    print("OPTIMAL (top result):")
    print(f"  {params_label(optimal)}")
    if optimal["num_shorts"] == 0:
        print("  -> 0 shorts qualified. All combinations filtered out.")
    else:
        print(f"  -> {optimal['num_shorts']} short(s) qualified")
        for r in optimal["results"]:
            if r.qualified:
                outcome = "STOP" if r.hit_stop else f"T{'3' if r.t3_hit else '2' if r.t2_hit else '1' if r.t1_hit else '0'}"
                print(f"     {r.ticker}: entry=${r.entry:.2f}  stop=${r.stop_price:.2f}  "
                      f"low=${r.low:.2f}  high=${r.high:.2f}  "
                      f"outcome={outcome}  P&L={fmt_pct(r.pnl_pct)}")
        print(f"  -> Win rate: {optimal['win_rate']:.1%}  Avg P&L: {fmt_pct(optimal['avg_pnl'])}  "
              f"Total P&L: {fmt_pct(optimal['total_pnl'])}")

    # -- Per-parameter analysis -------------------------------------------------
    print()
    print("=" * W)
    print("PER-PARAMETER SENSITIVITY (all other params at D161 defaults)")
    print("=" * W)
    for param, values in param_grid.items():
        print()
        print(f"  Varying {param}:")
        print(f"  {'Value':>12}  {'Shorts':>6}  {'WinRate':>8}  {'AvgP&L':>8}  {'TotalP&L':>9}  Note")
        print("  " + "-" * 65)
        for val in values:
            params = {**CURRENT_DEFAULTS, param: val}
            r = evaluate_params(**params)
            note = " <-- CURRENT" if val == CURRENT_DEFAULTS[param] else ""
            if r["num_shorts"] == 0:
                print(f"  {_fmt_param_val(param, val):>12}  {'0':>6}  {'--':>8}  {'--':>8}  {'--':>9}{note}")
            else:
                print(
                    f"  {_fmt_param_val(param, val):>12}  "
                    f"{r['num_shorts']:>6}  "
                    f"{r['win_rate']:>8.1%}  "
                    f"{fmt_pct(r['avg_pnl']):>8}  "
                    f"{fmt_pct(r['total_pnl']):>9}{note}"
                )

    # -- Distribution of results across all combinations -----------------------
    print()
    print("=" * W)
    print("SWEEP DISTRIBUTION SUMMARY")
    print("=" * W)
    zero_shorts   = sum(1 for r in all_results if r["num_shorts"] == 0)
    one_short     = sum(1 for r in all_results if r["num_shorts"] == 1)
    two_shorts    = sum(1 for r in all_results if r["num_shorts"] == 2)
    has_stops     = sum(1 for r in all_results if r["n_stops"] > 0)
    all_wins      = sum(1 for r in all_results if r["num_shorts"] > 0 and r["win_rate"] == 1.0)
    positive_pnl  = sum(1 for r in all_results if r["total_pnl"] > 0)

    print(f"  Total combinations:          {total_combos:>6,}")
    print(f"  -> 0 shorts qualified:        {zero_shorts:>6,}  ({zero_shorts/total_combos:.1%})")
    print(f"  -> 1 short qualified:         {one_short:>6,}  ({one_short/total_combos:.1%})")
    print(f"  -> 2 shorts qualified:        {two_shorts:>6,}  ({two_shorts/total_combos:.1%})")
    print(f"  -> Any stop-outs:             {has_stops:>6,}  ({has_stops/total_combos:.1%})")
    print(f"  -> 100%% win rate:             {all_wins:>6,}  ({all_wins/total_combos:.1%})")
    print(f"  -> Positive total P&L:        {positive_pnl:>6,}  ({positive_pnl/total_combos:.1%})")

    # -- Key insight -----------------------------------------------------------
    print()
    print("=" * W)
    print("KEY FINDINGS")
    print("=" * W)
    print()
    print("  1. STOP DOESN'T MATTER in this dataset.")
    print("     ARTL high=$8.22 vs entry=$7.68 -> only +7.0% above entry.")
    print("     Even the tightest stop (20%) means stop=$9.22 -- never triggered.")
    print("     SST  high=$3.39 vs entry=$3.20 -> only +5.9% above entry.")
    print("     Both stocks faded cleanly. Stop calibration matters for FUTURE data.")
    print()
    print("  2. DOLVOL GATE IS THE ONLY DIFFERENTIATOR.")
    print("     ARTL: $640K dolvol -> qualifies at $100K, $250K, $500K; fails at $1M")
    print("     SST:  $138K dolvol -> qualifies at $100K only; correctly filtered at $250K+")
    print("     The $500K current minimum is the right call: catches ARTL, rejects SST.")
    print()
    print("  3. FALLER SCORE GATE WORKS AS DESIGNED.")
    print("     ARTL (0.75) and SST (0.80) are the only stocks above any threshold.")
    print("     EEIQ (0.35), ELAB (0.10), BFRG (0.255), ASTC (0.035) are correctly")
    print("     excluded -- all were long candidates or had real catalysts.")
    print()
    print("  4. RVOL GATE IS IRRELEVANT HERE.")
    print("     ARTL at 37.7x and SST at 2071x both far exceed any min_rvol tested.")
    print("     EEIQ at 6.4x is the lowest, but it fails faller_score gate first.")
    print()
    print("  5. DATASET LIMITATION.")
    print("     Only 2 actual short candidates across 2 sessions.")
    print("     True optimization requires 20+ short candidates to avoid overfitting.")
    print("     These results validate the D161 design but cannot optimize it.")

    print()
    print("=" * W)
    print("PRODUCTION RECOMMENDATION")
    print("=" * W)
    print()
    print("  KEEP ALL D161 PARAMETERS UNCHANGED:")
    print()
    print(f"  {'Parameter':<25}  {'Current':>12}  {'Recommendation':>14}  Rationale")
    print("  " + "-" * 80)
    recs = [
        ("min_faller_score", 0.65,     "KEEP 0.65",  "Correctly captures ARTL (0.75), SST (0.80)"),
        ("min_dolvol",       500_000,  "KEEP $500K",  "Rejects SST ($138K). ARTL ($640K) passes."),
        ("min_gap_pct",      0.20,     "KEEP 20%",    "All candidates 75-140% gap -- well above gate"),
        ("stop_pct",         0.35,     "KEEP 35%",    "Neither stock triggered even 20% stop"),
        ("min_rvol",         3.0,      "KEEP 3x",     "ARTL 37.7x, SST 2071x -- gate is a floor"),
    ]
    for param, current_val, rec, rationale in recs:
        print(f"  {param:<25}  {_fmt_param_val(param, current_val):>12}  {rec:>14}  {rationale}")

    print()
    print("  URGENT ACTION: Enrich universe JSON files with premarket data.")
    print("  The arena's short_analyzer returns 0 qualified because the JSON files")
    print("  have None for gap_pct, rvol_at_open, dollar_volume on scanner-found")
    print("  stocks. Once those fields are populated, the arena will work correctly.")
    print("  Parameters do NOT need to change.")
    print()


def _fmt_param_val(param: str, val: float) -> str:
    if param == "min_dolvol":
        return fmt_dolvol(val)
    if param in ("min_gap_pct", "stop_pct"):
        return f"{val:.0%}"
    if param == "min_rvol":
        return f"{val:.0f}x"
    if param == "min_faller_score":
        return f"{val:.2f}"
    return str(val)


if __name__ == "__main__":
    run_sweep()
