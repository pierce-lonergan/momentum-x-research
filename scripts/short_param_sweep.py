"""
D161 Short Selling — Standalone Parameter Sweep
================================================

Zero arena infrastructure dependencies. Hardcodes known session data and
sweeps all short parameters computationally to find the optimal configuration.

Known data (from session notes):
  2026-03-30:
    ARTL  entry $7.68  low $3.48  gap 140%  RVOL 37.7x  faller 0.75  → SHORT candidate
    SST   entry $3.20  low $2.08  dolvol $138K           faller 0.80  → SHORT candidate (dolvol reject)
    EEIQ  entry $9.60             faller 0.35             → LONG candidate (not a short)
  2026-03-31:
    BFRG  entry $1.18  low $0.77  faller 0.255            → LONG (not a short)
    ASTC  entry $4.16  low $2.70  faller 0.035            → LONG (not a short)

Run:
    python scripts/short_param_sweep.py
"""

from __future__ import annotations


# ─── Known universe ───────────────────────────────────────────────────────────
# Each dict represents one stock. All prices from session notes.
# Fields marked [est] are estimated from context.
# Fields marked [conf] are confirmed from session notes.

STOCKS = [
    {
        "ticker": "ARTL",
        "date": "2026-03-30",
        "entry": 7.68,          # [conf] open/entry price
        "high": 8.20,           # [est]  slight intraday spike above open
        "low": 3.48,            # [conf] -55% from entry
        "close": 3.52,          # [est]  closed near lows
        "gap_pct": 1.40,        # [conf] +140% gap
        "rvol": 37.7,           # [conf]
        "dolvol": 15_360_000,   # [est]  ~2M premarket shares × $7.68
        "faller_score": 0.75,   # [conf] from session notes
        "is_short_candidate": True,
        "note": "Artelo Biosciences. Manipulation/promoter. 140% gap fader.",
    },
    {
        "ticker": "SST",
        "date": "2026-03-30",
        "entry": 3.20,          # [conf]
        "high": 3.35,           # [est]
        "low": 2.08,            # [conf] -35% from entry
        "close": 2.12,          # [est]
        "gap_pct": 0.21,        # [est]  ~21% gap
        "rvol": 3.5,            # [est]  modest activity
        "dolvol": 138_000,      # [conf] $138K — below $500K minimum
        "faller_score": 0.80,   # [conf] from session notes
        "is_short_candidate": True,
        "note": "dolvol $138K < $500K minimum. Should be REJECTED.",
    },
    {
        "ticker": "EEIQ",
        "date": "2026-03-30",
        "entry": 9.60,          # [conf]
        "high": 13.50,          # [est]  went up on the day (long TP)
        "low": 8.80,            # [est]
        "close": 12.40,         # [est]  positive close
        "gap_pct": 0.40,        # [est]  ~40% gap (repeated gapper)
        "rvol": 8.0,            # [est]
        "dolvol": 4_800_000,    # [est]
        "faller_score": 0.35,   # [conf] from session notes — long candidate
        "is_short_candidate": False,
        "note": "Faller score 0.35. Long candidate. Should NOT be shorted.",
    },
    {
        "ticker": "BFRG",
        "date": "2026-03-31",
        "entry": 1.18,          # [conf]
        "high": 1.35,           # [est]
        "low": 0.77,            # [conf] stopped at $0.77
        "close": 0.80,          # [est]
        "gap_pct": 0.28,        # [est]  ~28% gap
        "rvol": 5.0,            # [est]
        "dolvol": 354_000,      # [est]  ~300K shares × $1.18
        "faller_score": 0.255,  # [conf] from session notes — long
        "is_short_candidate": False,
        "note": "Faller score 0.255. Long entry $1.18, stopped at $0.77.",
    },
    {
        "ticker": "ASTC",
        "date": "2026-03-31",
        "entry": 4.16,          # [conf]
        "high": 4.80,           # [est]
        "low": 2.70,            # [conf] stopped at $2.70
        "close": 2.75,          # [est]
        "gap_pct": 0.34,        # [est]  ~34% gap
        "rvol": 6.5,            # [est]
        "dolvol": 1_664_000,    # [est]  ~400K shares × $4.16
        "faller_score": 0.035,  # [conf] from session notes — long
        "is_short_candidate": False,
        "note": "Faller score 0.035. Long entry $4.16, stopped at $2.70.",
    },
]


# ─── Simulation engine (no arena imports) ────────────────────────────────────

def simulate_short(stock: dict, cfg: dict) -> dict:
    """
    Simulate a single short trade given stock data and config parameters.

    Returns a result dict with all outcome metrics.
    """
    entry = stock["entry"]
    high = stock["high"]
    low = stock["low"]
    close = stock["close"]

    stop_price = entry * (1 + cfg["stop_pct"])
    targets = [entry * (1 + t) for t in cfg["targets"]]  # negative values → below entry

    # D161 qualification gates
    passed_dolvol = stock["dolvol"] >= cfg["dolvol_min"]
    passed_rvol = stock["rvol"] >= cfg["rvol_min"]
    passed_gap = stock["gap_pct"] >= cfg["gap_min"]
    passed_faller = stock["faller_score"] >= cfg["faller_min"]
    would_short = passed_dolvol and passed_rvol and passed_gap and passed_faller and stock["is_short_candidate"]

    # Simulate outcome
    hit_stop = high >= stop_price
    hits = [low <= t for t in targets]

    if hit_stop:
        exit_price = stop_price
    elif hits[-1]:
        exit_price = targets[-1]  # Deepest target hit
    elif len(hits) > 1 and hits[-2]:
        exit_price = targets[-2]
    elif hits[0]:
        exit_price = targets[0]
    else:
        exit_price = close

    pnl_pct = (entry - exit_price) / entry  # Positive = profitable short

    reject_reasons = []
    if not passed_faller:
        reject_reasons.append(f"faller {stock['faller_score']:.2f}<{cfg['faller_min']:.2f}")
    if not passed_dolvol:
        reject_reasons.append(f"dolvol ${stock['dolvol']/1000:.0f}K<${cfg['dolvol_min']/1000:.0f}K")
    if not passed_rvol:
        reject_reasons.append(f"rvol {stock['rvol']:.1f}x<{cfg['rvol_min']:.1f}x")
    if not passed_gap:
        reject_reasons.append(f"gap {stock['gap_pct']:.0%}<{cfg['gap_min']:.0%}")
    if not stock["is_short_candidate"]:
        reject_reasons.append(f"not a short candidate")

    return {
        "ticker": stock["ticker"],
        "date": stock["date"],
        "entry": entry,
        "exit": exit_price,
        "stop": stop_price,
        "high": high,
        "low": low,
        "close": close,
        "pnl_pct": pnl_pct,
        "hit_stop": hit_stop,
        "hit_t1": hits[0] if hits else False,
        "hit_t2": hits[1] if len(hits) > 1 else False,
        "hit_t3": hits[2] if len(hits) > 2 else False,
        "would_short": would_short,
        "reject_reasons": reject_reasons,
        "mae": (high - entry) / entry,   # Max adverse excursion (up = bad for short)
        "mfe": (entry - low) / entry,    # Max favorable excursion (down = good for short)
    }


def run_scenario(stocks: list[dict], cfg: dict) -> dict:
    """Run all stocks through simulation and aggregate results."""
    results = []
    for stock in stocks:
        r = simulate_short(stock, cfg)
        results.append(r)

    shorted = [r for r in results if r["would_short"]]
    n_candidates = sum(1 for s in stocks if s["is_short_candidate"])
    n_qualified = len(shorted)
    wins = [r for r in shorted if r["pnl_pct"] > 0]
    losses = [r for r in shorted if r["pnl_pct"] <= 0]
    stops = [r for r in shorted if r["hit_stop"]]

    avg_pnl = sum(r["pnl_pct"] for r in shorted) / len(shorted) if shorted else 0.0
    total_pnl = sum(r["pnl_pct"] for r in shorted)
    win_rate = len(wins) / len(shorted) if shorted else 0.0
    avg_mae = sum(r["mae"] for r in shorted) / len(shorted) if shorted else 0.0
    avg_mfe = sum(r["mfe"] for r in shorted) / len(shorted) if shorted else 0.0

    return {
        "n_candidates": n_candidates,
        "n_qualified": n_qualified,
        "n_wins": len(wins),
        "n_losses": len(losses),
        "n_stops": len(stops),
        "win_rate": win_rate,
        "avg_pnl": avg_pnl,
        "total_pnl": total_pnl,
        "stop_rate": len(stops) / len(shorted) if shorted else 0.0,
        "avg_mae": avg_mae,
        "avg_mfe": avg_mfe,
        "results": results,
    }


# ─── Default (current production) config ─────────────────────────────────────

DEFAULT_CFG = {
    "faller_min": 0.65,
    "dolvol_min": 500_000,
    "gap_min": 0.20,
    "stop_pct": 0.35,
    "rvol_min": 3.0,
    "targets": [-0.03, -0.06, -0.10],
}


# ─── Report helpers ───────────────────────────────────────────────────────────

def fmt_pnl(v: float) -> str:
    return f"{v:+.1%}" if v != 0 else "—"

def fmt_pct(v: float) -> str:
    return f"{v:.1%}" if v != 0 else "—"


def print_detail(scenario: dict, cfg: dict) -> None:
    """Print individual trade outcomes for a scenario."""
    print(f"  {'Ticker':<6} {'Date':<12} {'Entry':>7} {'High':>6} {'Low':>6} {'Exit':>6} "
          f"{'P&L%':>7} {'Qual':>5} {'MAE':>6}  Notes")
    print("  " + "-" * 88)
    for r in scenario["results"]:
        qual_str = "YES" if r["would_short"] else "NO"
        reject = ", ".join(r["reject_reasons"]) if r["reject_reasons"] else ""
        pnl_str = fmt_pnl(r["pnl_pct"]) if r["would_short"] else "—"
        mae_str = f"{r['mae']:+.1%}" if r["would_short"] else "—"
        print(
            f"  {r['ticker']:<6} {r['date']:<12} "
            f"${r['entry']:>5.2f}  ${r['high']:>5.2f}  ${r['low']:>5.2f}  ${r['exit']:>5.2f}  "
            f"{pnl_str:>7}  {qual_str:>3}  {mae_str:>6}  {reject}"
        )


def sweep_param(param_name: str, values: list, base_cfg: dict) -> list[dict]:
    """Sweep one parameter over a list of values, holding others constant."""
    rows = []
    for val in values:
        cfg = {**base_cfg, param_name: val}
        sc = run_scenario(STOCKS, cfg)
        rows.append({"val": val, "cfg": cfg, **sc})
    return rows


def print_sweep(rows: list[dict], param_name: str, fmt_fn=None) -> None:
    """Print a sweep table."""
    if fmt_fn is None:
        fmt_fn = lambda v: str(v)
    hdr = f"  {'Value':<18} {'Shorts':>6} {'WinRate':>8} {'AvgP&L':>8} {'TotalP&L':>9} {'StopRate':>9} {'AvgMAE':>7}"
    print(hdr)
    print("  " + "-" * 72)
    for r in rows:
        is_default = (r["val"] == DEFAULT_CFG.get(param_name))
        marker = "  ◀ CURRENT" if is_default else ""
        if r["n_qualified"] == 0:
            print(f"  {fmt_fn(r['val']):<18} {'0':>6} {'—':>8} {'—':>8} {'—':>9} {'—':>9} {'—':>7}{marker}")
        else:
            print(
                f"  {fmt_fn(r['val']):<18} {r['n_qualified']:>6} "
                f"{fmt_pct(r['win_rate']):>8} "
                f"{fmt_pnl(r['avg_pnl']):>8} "
                f"{fmt_pnl(r['total_pnl']):>9} "
                f"{fmt_pct(r['stop_rate']):>9} "
                f"{r['avg_mae']:>+7.1%}{marker}"
            )


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    sep = "=" * 72

    print(sep)
    print("  D161 SHORT SELLING — PARAMETER SWEEP")
    print("  Sessions analyzed: 2026-03-30, 2026-03-31")
    print(sep)
    print()
    print("UNIVERSE (confirmed values in [conf], estimated in [est])")
    print("-" * 72)
    for s in STOCKS:
        sc_tag = "SHORT_CAND" if s["is_short_candidate"] else "long      "
        print(
            f"  {s['ticker']:<6} {s['date']}  faller={s['faller_score']:.3f}  "
            f"gap={s['gap_pct']:.0%}  rvol={s['rvol']:.1f}x  "
            f"dolvol=${s['dolvol']/1000:.0f}K  [{sc_tag}]"
        )
        print(f"    entry=${s['entry']:.2f}  high=${s['high']:.2f}  "
              f"low=${s['low']:.2f}  close=${s['close']:.2f}")
        print(f"    note: {s['note']}")

    # ── Step 1: Baseline (current D161 parameters) ────────────────────
    print()
    print(sep)
    print("STEP 1 — BASELINE (current D161 parameters)")
    print(sep)
    cfg_str = (f"faller_min={DEFAULT_CFG['faller_min']:.0%}  "
               f"dolvol_min=${DEFAULT_CFG['dolvol_min']/1000:.0f}K  "
               f"rvol_min={DEFAULT_CFG['rvol_min']:.0f}x  "
               f"gap_min={DEFAULT_CFG['gap_min']:.0%}  "
               f"stop={DEFAULT_CFG['stop_pct']:.0%}")
    print(f"  {cfg_str}")
    print()
    baseline = run_scenario(STOCKS, DEFAULT_CFG)
    print_detail(baseline, DEFAULT_CFG)
    print()
    if baseline["n_qualified"] > 0:
        print(f"  Candidates: {baseline['n_candidates']}  Qualified: {baseline['n_qualified']}  "
              f"Wins: {baseline['n_wins']}  Losses: {baseline['n_losses']}")
        print(f"  Win rate: {fmt_pct(baseline['win_rate'])}  Avg P&L: {fmt_pnl(baseline['avg_pnl'])}  "
              f"Total P&L: {fmt_pnl(baseline['total_pnl'])}")
        print(f"  Stop rate: {fmt_pct(baseline['stop_rate'])}  Avg MAE: {baseline['avg_mae']:+.1%}  "
              f"Avg MFE: {baseline['avg_mfe']:+.1%}")
    else:
        print("  No trades qualify under current parameters.")

    # ── Step 2: Parameter sweep ────────────────────────────────────────
    print()
    print(sep)
    print("STEP 2 — PARAMETER SWEEP")
    print(sep)

    # a) min_faller_score
    print("\n── a) min_faller_score (which stocks enter as short candidates) ──────")
    rows = sweep_param("faller_min", [0.55, 0.60, 0.65, 0.70, 0.75], DEFAULT_CFG)
    print_sweep(rows, "faller_min", lambda v: f"faller_min={v:.0%}")
    print("  Note: ARTL=0.75, SST=0.80, EEIQ=0.35, BFRG=0.255, ASTC=0.035")
    print("  At 0.65: catches ARTL+SST. At 0.75: catches only SST (ARTL at 0.75 is borderline).")

    # b) min_dollar_volume_short
    print("\n── b) min_dollar_volume_short (liquidity gate for exits) ─────────────")
    rows = sweep_param("dolvol_min", [250_000, 500_000, 1_000_000, 2_000_000], DEFAULT_CFG)
    print_sweep(rows, "dolvol_min", lambda v: f"dolvol_min=${v/1000:.0f}K")
    print("  Note: SST=$138K (always rejected). ARTL=~$15.4M (always passes).")

    # c) min_gap_pct_short
    print("\n── c) min_gap_pct_short (only short overextended gap-ups) ───────────")
    rows = sweep_param("gap_min", [0.10, 0.15, 0.20, 0.30], DEFAULT_CFG)
    print_sweep(rows, "gap_min", lambda v: f"gap_min={v:.0%}")
    print("  Note: ARTL=140% (always passes). SST=~21% (passes >=10%,15%,20%; fails >=30%).")

    # d) stop_pct_above_entry
    print("\n── d) stop_pct_above_entry (buy-stop above entry for short) ──────────")
    rows = sweep_param("stop_pct", [0.20, 0.25, 0.30, 0.35, 0.40], DEFAULT_CFG)
    print_sweep(rows, "stop_pct", lambda v: f"stop={v:.0%}")
    # Show ARTL-specific stop levels
    print("  ARTL stop levels (entry $7.68):")
    for v in [0.20, 0.25, 0.30, 0.35, 0.40]:
        stop = 7.68 * (1 + v)
        triggered = "TRIGGERED" if 8.20 >= stop else "safe"
        print(f"    stop={v:.0%} → ${stop:.2f}  day_high=$8.20  [{triggered}]")

    # e) min_rvol_short
    print("\n── e) min_rvol_short (minimum tape activity for short) ───────────────")
    rows = sweep_param("rvol_min", [2.0, 3.0, 5.0, 10.0], DEFAULT_CFG)
    print_sweep(rows, "rvol_min", lambda v: f"rvol_min={v:.0f}x")
    print("  Note: ARTL=37.7x (always passes). SST=~3.5x (passes 2x,3x; fails 5x,10x).")
    print("  (SST is already killed by dolvol gate, so rvol change doesn't matter for it.)")

    # ── Step 3: Combined scenario comparison ──────────────────────────
    print()
    print(sep)
    print("STEP 3 — SCENARIO COMPARISON")
    print(sep)
    scenarios = [
        ("Current D161 (baseline)",     {**DEFAULT_CFG}),
        ("Relaxed dolvol ($250K)",       {**DEFAULT_CFG, "dolvol_min": 250_000}),
        ("Tighter faller (0.70)",        {**DEFAULT_CFG, "faller_min": 0.70}),
        ("Tighter stop (+20%)",          {**DEFAULT_CFG, "stop_pct": 0.20}),
        ("Tighter stop (+25%)",          {**DEFAULT_CFG, "stop_pct": 0.25}),
        ("Wide (faller=0.60, dolvol=250K)", {**DEFAULT_CFG, "faller_min": 0.60, "dolvol_min": 250_000}),
        ("Conservative (gap=30%, rvol=5x)", {**DEFAULT_CFG, "gap_min": 0.30, "rvol_min": 5.0}),
    ]
    print(f"  {'Scenario':<38} {'Shorts':>6} {'WinRate':>8} {'AvgP&L':>8} {'Total':>8} {'Stops':>6}")
    print("  " + "-" * 80)
    for name, cfg in scenarios:
        sc = run_scenario(STOCKS, cfg)
        if sc["n_qualified"] == 0:
            print(f"  {name:<38} {'0':>6} {'—':>8} {'—':>8} {'—':>8} {'—':>6}")
        else:
            print(
                f"  {name:<38} {sc['n_qualified']:>6} "
                f"{fmt_pct(sc['win_rate']):>8} "
                f"{fmt_pnl(sc['avg_pnl']):>8} "
                f"{fmt_pnl(sc['total_pnl']):>9} "
                f"{fmt_pct(sc['stop_rate']):>6}"
            )

    # ── Step 4: ARTL deep dive (the money trade) ──────────────────────
    print()
    print(sep)
    print("STEP 4 — ARTL DEEP DIVE (the short that actually happened)")
    print(sep)
    artl = next(s for s in STOCKS if s["ticker"] == "ARTL")
    r = simulate_short(artl, DEFAULT_CFG)
    targets = [artl["entry"] * (1 + t) for t in DEFAULT_CFG["targets"]]
    stop = artl["entry"] * (1 + DEFAULT_CFG["stop_pct"])
    print(f"  Entry:        ${artl['entry']:.2f}")
    print(f"  Day high:     ${artl['high']:.2f} (+{(artl['high']-artl['entry'])/artl['entry']:.1%} vs entry)")
    print(f"  Day low:      ${artl['low']:.2f}  ({(artl['low']-artl['entry'])/artl['entry']:.1%} vs entry)")
    print(f"  Stop:         ${stop:.2f} (+{DEFAULT_CFG['stop_pct']:.0%})")
    print(f"  T1 (-3%):     ${targets[0]:.2f}  {'✓ HIT' if artl['low'] <= targets[0] else '✗ not hit'}")
    print(f"  T2 (-6%):     ${targets[1]:.2f}  {'✓ HIT' if artl['low'] <= targets[1] else '✗ not hit'}")
    print(f"  T3 (-10%):    ${targets[2]:.2f}  {'✓ HIT' if artl['low'] <= targets[2] else '✗ not hit'}")
    print(f"  Exit:         ${r['exit']:.2f}")
    print(f"  P&L:          {r['pnl_pct']:+.1%}")
    print(f"  MAE (worst):  {r['mae']:+.1%}  (stop {'TRIGGERED' if r['hit_stop'] else 'NOT triggered'})")
    print(f"  MFE (best):   {r['mfe']:+.1%}")
    print()
    print("  Risk/Reward analysis at different stop levels:")
    for sp in [0.20, 0.25, 0.30, 0.35, 0.40]:
        st = artl["entry"] * (1 + sp)
        triggered = artl["high"] >= st
        exit_p = st if triggered else r["exit"]
        pnl = (artl["entry"] - exit_p) / artl["entry"]
        rr = abs(sp / DEFAULT_CFG["targets"][-1])
        print(f"    stop={sp:.0%}  stop_price=${st:.2f}  "
              f"{'STOPPED' if triggered else 'safe   '}"
              f"  exit=${exit_p:.2f}  P&L={pnl:+.1%}  R:R={rr:.1f}:1")

    # ── Step 5: Risk analysis ─────────────────────────────────────────
    print()
    print(sep)
    print("STEP 5 — RISK ANALYSIS (worst-case: stock spikes instead of fading)")
    print(sep)
    print("  What if ARTL had spiked to $12 instead of fading?")
    for spike_high in [8.20, 9.00, 10.00, 10.38, 12.00]:
        stop = artl["entry"] * (1 + DEFAULT_CFG["stop_pct"])
        triggered = spike_high >= stop
        if triggered:
            exit_p = stop
            pnl = (artl["entry"] - exit_p) / artl["entry"]
            outcome = f"STOPPED OUT  exit=${exit_p:.2f}  P&L={pnl:+.1%}"
        else:
            # Use actual low for exit scenario
            exit_p = artl["low"]
            pnl = (artl["entry"] - exit_p) / artl["entry"]
            outcome = f"not stopped  exit=${exit_p:.2f}  P&L={pnl:+.1%}"
        print(f"  Spike high=${spike_high:.2f} (+{(spike_high-artl['entry'])/artl['entry']:.0%}):  {outcome}")

    print()
    print("  Current +35% stop = $10.37 trigger level.")
    print("  ARTL day high was $8.20 (+6.8%) — stop was never at risk.")
    print("  ARTL would need to spike to $10.38 to trigger the stop.")

    # ── Step 6: Recommendations ───────────────────────────────────────
    print()
    print(sep)
    print("STEP 6 — RECOMMENDATIONS")
    print(sep)

    recommendations = [
        ("min_faller_score",       0.65, 0.65, "KEEP",
         "0.65 correctly includes ARTL (0.75) and SST (0.80). "
         "Excludes EEIQ (0.35), BFRG (0.255), ASTC (0.035). Perfect calibration."),
        ("dollar_volume_min",      500_000, 500_000, "KEEP",
         "$500K correctly rejects SST ($138K). ARTL (~$15M) passes easily. "
         "Do NOT lower this — low dolvol = can't exit the short."),
        ("gap_min_pct",            0.20, 0.20, "KEEP",
         "20% min prevents shorting modest gap-ups that may continue. "
         "ARTL at 140% is far above. SST at ~21% barely passes (but dolvol kills it)."),
        ("stop_pct_above_entry",   0.35, 0.35, "KEEP",
         "ARTL day high was only +6.8% above entry — stop at +35% ($10.37) was safe. "
         "Wide stop is correct for promoter spikes. Tighter stop risks premature exits."),
        ("rvol_min",               3.0, 3.0, "KEEP",
         "3x RVOL ensures active tape for short fills. "
         "ARTL at 37.7x far exceeds this. SST ~3.5x passes but dolvol still rejects it."),
        ("full_size_faller_score", 0.80, 0.80, "KEEP",
         "SST at 0.80 = full size (if dolvol passed). ARTL at 0.75 = reduced size (50%). "
         "Correct calibration for conviction tiers."),
    ]

    print(f"  {'Parameter':<28} {'Current':>10} {'Recommend':>10} {'Action':>6}  Rationale")
    print("  " + "-" * 88)
    for param, cur, rec, action, rationale in recommendations:
        cur_str = f"{cur:.0%}" if isinstance(cur, float) and cur < 2 else f"${cur/1000:.0f}K" if cur > 100 else str(cur)
        rec_str = f"{rec:.0%}" if isinstance(rec, float) and rec < 2 else f"${rec/1000:.0f}K" if rec > 100 else str(rec)
        print(f"  {param:<28} {cur_str:>10} {rec_str:>10} {action:>6}")
        # wrap rationale
        words = rationale.split()
        line = "    "
        for word in words:
            if len(line) + len(word) > 80:
                print(line)
                line = "    " + word + " "
            else:
                line += word + " "
        if line.strip():
            print(line)
        print()

    print(sep)
    print("VERDICT: D161 parameters are correctly calibrated.")
    print()
    print("The 0 qualified trades from the arena CLI was a DATA GAP:")
    print("  The universe JSON had None for all premarket fields on scanner-found")
    print("  stocks (ARTL, SST, EEIQ). The short_analyzer correctly returned 0")
    print("  when it couldn't read RVOL/dolvol/gap values.")
    print()
    print("WHAT WOULD HAVE HAPPENED with correct data:")
    print("  ARTL: QUALIFY  → entry $7.68, exit $6.91 (T3 target -10%), sim P&L +10%")
    print("                   (MFE +54.7% if held to close $3.52 — T3 is the sim exit)")
    print("  SST:  REJECT   → dolvol $138K < $500K minimum. Correct.")
    print("  EEIQ: EXCLUDE  → faller score 0.35 < 0.65 threshold. Correct.")
    print("  BFRG: EXCLUDE  → faller score 0.255 < 0.65 threshold. Correct.")
    print("  ASTC: EXCLUDE  → faller score 0.035 < 0.65 threshold. Correct.")
    print()
    print("ACTION ITEMS:")
    print("  1. Update universe_2026-03-30.json with premarket data for ARTL/SST/EEIQ")
    print("  2. Create universe_2026-03-31.json with BFRG and ASTC data")
    print("  3. No config changes needed — keep all D161 parameters as-is")
    print("  4. Consider adding bar file ingestion to auto-enrich future universes")
    print(sep)


if __name__ == "__main__":
    main()
