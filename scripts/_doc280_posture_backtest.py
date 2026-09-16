"""DOC 280 PATH B (PRIMARY) — exit-posture backtest through the VALIDATED arena fill model, on local minute tape.
For every real BUY candidate (data/features/features_*.jsonl) across all sessions: price the ENTRY fill via the arena
AlpacaFillModel + Polygon SpreadModel (reused from fill_model_backtest = the validated model), then replay FOUR EXIT
ARMS mapping to the shippable exit_policy flag, and measure realized per-candidate return under each:

  A_bar1        : sell at T+60s close            (exit_policy=bar1_legacy — the prod default that LOSES)
  B_hold_close  : sell at 16:00 ET same session  (flatten-at-close; no overnight)
  C_next_open   : sell at next session's open     (exit_policy=t1_next_open — the flag; carries overnight)
  D_hold_stop15 : hold to close, exit if intraday low <= entry*(1-0.15)  (disaster-stop-only posture)

The lever = B/C/D minus A. Returns are net of a HOSTILE one-way exit spread; the entry fill already bears its
marketable-limit spread (net both sides). Bars are LOCAL minute_aggs (no API, no rate limit). Metric is per-candidate
RETURN (unit-size normalized -> no qty problem); dollarized via a representative notional at the end.

HONESTY (mandate 4): total return + winsorized + median + win-rate + bootstrap CI; variance-vs-expectancy (does the
mean survive winsor?); lucky-draw (top-k share); cross-regime (by month/year). RUN FULL ONLY AFTER PREREG COMMIT."""
from __future__ import annotations
import argparse, glob, sys
from datetime import timedelta
from pathlib import Path
from random import Random
from types import SimpleNamespace
import duckdb, numpy as np

sys.path.insert(0, "scripts")
import fill_model_backtest as F  # validated fill/spread models + _fill/_limits/_load_buy_candidates

_DA = "data/polygon_warehouse/minute_aggs/**/*.parquet"


def _load_local_bars(con, ticker, ts0):
    """Forward bars from entry ts0 to end of NEXT session, in F's format: (off_min, lo, op, hi, cl, vol).
    Also returns entry-session close offset and next-session open offset."""
    lo = ts0.replace(tzinfo=None)
    hi = lo + timedelta(days=5)
    df = con.execute(f"""SELECT ts_utc, ts_et, open, high, low, close, volume
        FROM read_parquet('{_DA}', union_by_name=true)
        WHERE ticker=? AND ts_utc BETWEEN ? AND ? ORDER BY ts_utc""", [ticker, lo - timedelta(minutes=1), hi]).df()
    if df.empty:
        return None
    df["ts_utc"] = df["ts_utc"].astype("datetime64[ns]")
    df["etstr"] = df["ts_et"].astype(str)
    df["etdate"], df["ettime"] = df["etstr"].str[:10], df["etstr"].str[11:16]
    reg = df[(df.ettime >= "09:30") & (df.ettime <= "16:00")].copy()
    if reg.empty:
        return None
    t0 = lo
    reg["off"] = (reg.ts_utc - t0).dt.total_seconds() / 60.0
    fwd = reg[reg.off >= -1.0]
    if fwd.empty:
        return None
    entry_date = fwd.etdate.iloc[0]
    same = fwd[fwd.etdate == entry_date]
    later = fwd[fwd.etdate > entry_date]
    bars = [(float(r.off), float(r.low), float(r.open), float(r.high), float(r.close), float(r.volume))
            for r in same.itertuples()]
    close_off = bars[-1][0] if bars else None
    close_px = bars[-1][4] if bars else None
    nopen_px = float(later.open.iloc[0]) if len(later) else close_px
    return {"bars": bars, "close_off": close_off, "close_px": close_px, "nopen_px": nopen_px}


def _px_at_off(bars, target_off):
    """close of the last bar with off <= target_off (>= for forward-looking targets handled by caller)."""
    out = None
    for (off, lo, op, hi, cl, vol) in bars:
        if off <= target_off:
            out = cl
        else:
            break
    return out if out is not None else (bars[0][4] if bars else None)


def _arm_returns(fill_px, fill_off, bars, close_px, nopen_px, stop_pct, exit_cost):
    """Realized returns for the 4 exit arms, net of a one-way exit-cost haircut."""
    def net(exit_px):
        return (exit_px * (1 - exit_cost) - fill_px) / fill_px if fill_px > 0 else 0.0
    # A: T+60s close
    a_px = _px_at_off([b for b in bars if b[0] >= fill_off] or bars, fill_off + 1.0)
    # B: session close
    b_px = close_px
    # C: next open
    c_px = nopen_px
    # D: hold to close, disaster stop at -stop_pct on the intraday low path
    stop = fill_px * (1 - stop_pct)
    d_px = close_px
    for (off, lo, op, hi, cl, vol) in bars:
        if off < fill_off:
            continue
        if lo <= stop:
            d_px = stop; break
    return {"A_bar1": net(a_px), "B_hold_close": net(b_px), "C_next_open": net(c_px), "D_hold_stop15": net(d_px)}


def run(dates, stop_pct=0.15, exit_cost=0.005, max_per_day=0):
    con = duckdb.connect(); con.execute("SET threads=4")
    if F._HAVE_ARENA:
        spread, fill_model = F.SpreadModel(), F.AlpacaFillModel()
        fidelity = "arena"
    else:
        spread = fill_model = None; fidelity = "proxy"
    settings = F.Settings()
    rng = Random(280)
    sub_off = 45 / 60.0
    per = []
    for date in dates:
        cands = F._load_buy_candidates(date)
        if max_per_day and len(cands) > max_per_day:
            stride = len(cands) / max_per_day
            cands = [cands[int(i * stride)] for i in range(max_per_day)]
        for c in cands:
            try:
                ts0 = F._parse_ts(c["ts"])
            except Exception:
                continue
            lb = _load_local_bars(con, c["ticker"], ts0)
            if not lb or not lb["bars"] or lb["close_px"] is None:
                continue
            r = {"ticker": c["ticker"], "price": c["price"], "ts0": ts0.replace(tzinfo=None),
                 "rvol": float(c.get("rvol") or 1.0), "gap": float(c.get("gap") or 0.0),
                 "mfcs": c.get("mfcs"), "bars": lb["bars"]}
            passive, mkt = F._limits(r, sub_off, spread, settings.execution)
            filled, fill_px, fill_off = F._fill(r, mkt, 2.0, sub_off, fidelity, spread, fill_model, rng)
            if not filled or fill_px <= 0:
                continue
            arms = _arm_returns(fill_px, fill_off, lb["bars"], lb["close_px"], lb["nopen_px"], stop_pct, exit_cost)
            per.append({"date": date, "ticker": c["ticker"], "fill_px": fill_px, **arms})
    con.close()
    return per


def _stats(vals):
    v = np.array(vals, float)
    return {"n": len(v), "mean": float(v.mean()) if len(v) else 0.0, "median": float(np.median(v)) if len(v) else 0.0,
            "win": float((v > 0).mean()) if len(v) else 0.0, "total": float(v.sum())}


def _winsor(vals, pct=0.05):
    v = np.array(sorted(vals), float)
    if len(v) < 10:
        return v
    k = int(len(v) * pct)
    if k:
        v[-k:] = v[-k - 1]; v[:k] = v[k]
    return v


def _boot_ci(vals, B=5000, seed=280):
    rng = np.random.RandomState(seed); v = np.array(vals, float)
    m = [rng.choice(v, len(v), replace=True).mean() for _ in range(B)]
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def report(per, notional=8000.0):
    n = len(per)
    print(f"\n=== DOC 280 PATH B: exit-posture backtest through the validated arena fill model ===")
    print(f"candidates filled = {n} across {len(set(p['date'] for p in per))} sessions | dollarize @ ~${notional:,.0f}/ticket")
    arms = ["A_bar1", "B_hold_close", "C_next_open", "D_hold_stop15"]
    base = "A_bar1"
    print(f"\n  {'arm':>14} | {'mean':>8} | {'median':>8} | {'win%':>5} | {'Δmean vs A':>10} | {'Δwinsor@5%':>10} | {'ΔCI95(mean)':>22} | {'$/ticket Δ':>10}")
    baserets = [p[base] for p in per]
    out = {"n": n, "notional": notional, "arms": {}}
    for a in arms:
        rets = [p[a] for p in per]
        s = _stats(rets)
        deltas = [p[a] - p[base] for p in per]
        dmean = float(np.mean(deltas)) if deltas else 0.0
        dwin = float(np.mean(_winsor(deltas))) if deltas else 0.0
        ci = _boot_ci(deltas) if a != base else (0.0, 0.0)
        dpt = dmean * notional
        excl0 = "excl0" if (a != base and (ci[0] > 0 or ci[1] < 0)) else ""
        print(f"  {a:>14} | {s['mean']:>+7.2%} | {s['median']:>+7.2%} | {100*s['win']:>4.0f} | {dmean:>+9.2%} | {dwin:>+9.2%} | [{ci[0]:>+7.2%},{ci[1]:>+7.2%}] {excl0:>5} | {dpt:>+9,.0f}")
        out["arms"][a] = {"mean": s["mean"], "median": s["median"], "win": s["win"],
                          "dmean_vs_A": dmean, "dwinsor": dwin, "ci95": ci, "dollar_per_ticket": dpt}
    # variance-vs-expectancy + lucky-draw + cross-regime for the headline arm (C_next_open) vs A
    for a in ("B_hold_close", "C_next_open", "D_hold_stop15"):
        deltas = sorted([p[a] - p[base] for p in per])
        tot = sum(deltas)
        top = sum(deltas[-max(1, n // 100):])
        from collections import defaultdict
        bym = defaultdict(list)
        for p in per:
            bym[str(p["date"])[:7]].append(p[a] - p[base])
        print(f"\n  {a} vs A_bar1: total Δret {tot:+.1%} over {n} tickets | top-1% tickets = {100*top/tot if tot else 0:.0f}% of Δ | survives winsor? {'YES' if np.mean(_winsor(deltas))>0 else 'NO'}")
        print("    by-month Δmean: " + " ".join(f"{m}:{np.mean(v):+.2%}" for m, v in sorted(bym.items())))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--stop-pct", type=float, default=0.15)
    ap.add_argument("--exit-cost", type=float, default=0.005)
    ap.add_argument("--max-per-day", type=int, default=0)
    ap.add_argument("--notional", type=float, default=8000.0)
    ap.add_argument("--smoke", type=int, default=0, help=">0: only this many recent sessions (mechanics)")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    dates = [Path(f).stem.replace("features_", "") for f in sorted(glob.glob("data/features/features_2026-*.jsonl"))]
    if a.smoke:
        dates = dates[-a.smoke:]
    per = run(dates, a.stop_pct, a.exit_cost, a.max_per_day)
    if a.smoke:
        print(f"SMOKE: {len(per)} candidates filled across {len(dates)} sessions; mechanics OK (no full-run claim).")
        for p in per[:5]:
            print(f"  {p['ticker']:>6} A={p['A_bar1']:+.2%} B={p['B_hold_close']:+.2%} C={p['C_next_open']:+.2%} D={p['D_hold_stop15']:+.2%}")
        sys.exit(0)
    out = report(per, a.notional)
    if a.json:
        import json
        json.dump({"per": per, "summary": out}, open(a.json, "w"), indent=1, default=str)
        print(f"\nwrote {a.json}")
