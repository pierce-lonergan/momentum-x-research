"""Build the multi-day-hold research dataset (doc 229).

Hypothesis under test (Pierce): "many times when we fail to close the next day the stock
unexpectedly skyrockets. I want to run an experiment for holding stocks for multiple days,
5 days max."

This script joins our EVALUATION universe (every candidate the bot scored, from the journals)
to SPLIT-ADJUSTED forward daily bars (day_aggs x splits.parquet), so we can measure the true
holder return of holding N days past the signal-day close.

CRITICAL methodology guards (the data gaps this script closes):
  - GAP-6 UNADJUSTED BARS: day_aggs_v1 is RAW. Low-float small-caps reverse-split constantly
    (LNKS 250:1, JTAI 200:1, ...). A naive close-to-close return shows phantom +1000s%. We
    SPLIT-ADJUST every forward price to the entry-day scale via splits.parquet. Without this,
    the experiment "discovers" fake skyrockets.
  - SURVIVORSHIP/AVAILABILITY: we score the FULL evaluation corpus (hundreds/day), not just the
    handful of trades we remember. Median + Wilson CI, never outlier-skewed mean.
  - STALENESS: warehouse maxes at the last published flat-file day; entries without a full
    `--horizon`-day forward window are dropped (logged), not silently truncated.

Output: data/research/multiday_hold_dataset.parquet  (one row per (ticker, session_date) entry)

Usage:
  python scripts/build_multiday_hold_dataset.py --horizon 5
  python scripts/build_multiday_hold_dataset.py --horizon 5 --min-mfcs 0.45   # qualified-only
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
from collections import defaultdict

import duckdb
import pandas as pd

WAREHOUSE = "data/polygon_warehouse/day_aggs/**/*.parquet"
SPLITS = "data/polygon_warehouse/reference/splits.parquet"
JOURNAL_GLOB = "data/journals/journal_*.jsonl"
OUT = "data/research/multiday_hold_dataset.parquet"


def load_entries() -> pd.DataFrame:
    """One entry per (ticker, session_date): the EARLIEST eval of the day (closest to entry)."""
    best: dict[tuple, dict] = {}
    for fn in sorted(glob.glob(JOURNAL_GLOB)):
        try:
            with open(fn, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    tk = r.get("ticker")
                    sd = r.get("session_date") or (r.get("timestamp", "") or "")[:10]
                    ts = r.get("timestamp", "")
                    if not tk or not sd:
                        continue
                    key = (tk, sd)
                    prev = best.get(key)
                    if prev is None or ts < prev["_ts"]:
                        best[key] = {
                            "ticker": tk,
                            "session_date": sd,
                            "_ts": ts,
                            "eval_price": r.get("current_price"),
                            "previous_close": r.get("previous_close"),
                            "gap_pct": r.get("gap_pct"),
                            "gap_classification": r.get("gap_classification"),
                            "rvol": r.get("rvol"),
                            "mfcs": r.get("mfcs"),
                            "phase": r.get("phase"),
                        }
        except Exception:
            continue
    df = pd.DataFrame(best.values())
    if not df.empty:
        df = df.drop(columns=["_ts"])
    return df


def split_factor_map(con, tickers: list[str]) -> dict:
    """For each (ticker, date) we need Pi(split_to/split_from) over splits AFTER that date,
    to express a forward raw price in ENTRY-date scale. We return the raw split events; the
    per-entry adjustment is applied in build()."""
    if not tickers:
        return {}
    tk_list = ",".join("'" + t.replace("'", "") + "'" for t in tickers)
    rows = con.execute(
        f"""SELECT ticker, execution_date::DATE d, split_from, split_to
            FROM read_parquet('{SPLITS}') WHERE ticker IN ({tk_list})
            AND split_from > 0 AND split_to > 0 ORDER BY ticker, d"""
    ).fetchall()
    m = defaultdict(list)
    for tk, d, sf, st in rows:
        m[tk].append((d, float(sf), float(st)))
    return m


def adj_factor(splits_for_tk, entry_date, fwd_date) -> float:
    """Product of (split_to/split_from) for splits in (entry_date, fwd_date].
    Reverse split 250:1 (from=250,to=1) -> factor 1/250, shrinking the inflated post-split
    forward price back to entry-day scale so the return is the TRUE holder return."""
    f = 1.0
    for d, sf, st in splits_for_tk:
        if entry_date < d <= fwd_date:
            f *= (st / sf)
    return f


def wilson(p, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n) / denom
    return (max(0.0, centre - margin), min(1.0, centre + margin))


def build(horizon: int, min_mfcs: float | None) -> pd.DataFrame:
    con = duckdb.connect()
    entries = load_entries()
    if min_mfcs is not None:
        entries = entries[entries["mfcs"].fillna(0) >= min_mfcs]
    print(f"entries (one per ticker/day): {len(entries)} | sessions: {entries['session_date'].nunique()}")
    tickers = sorted(entries["ticker"].dropna().unique().tolist())

    # forward daily bars for just our tickers (RAW), keyed by date
    tk_list = ",".join("'" + t.replace("'", "") + "'" for t in tickers)
    bars = con.execute(
        f"""SELECT ticker, ts_et::DATE d, open, high, low, close, volume
            FROM read_parquet('{WAREHOUSE}') WHERE ticker IN ({tk_list}) ORDER BY ticker, d"""
    ).df()
    bars["d"] = pd.to_datetime(bars["d"]).dt.date
    by_tk = {tk: g.sort_values("d").reset_index(drop=True) for tk, g in bars.groupby("ticker")}
    warehouse_max = bars["d"].max()
    splits_map = split_factor_map(con, tickers)

    out = []
    dropped_nodata = dropped_short = 0
    for _, e in entries.iterrows():
        tk = e["ticker"]
        try:
            ed = pd.to_datetime(e["session_date"]).date()
        except Exception:
            continue
        g = by_tk.get(tk)
        if g is None or g.empty:
            dropped_nodata += 1
            continue
        fwd = g[g["d"] >= ed].reset_index(drop=True)
        if fwd.empty:
            dropped_nodata += 1
            continue
        # need entry-day bar + `horizon` forward sessions
        if len(fwd) < horizon + 1:
            dropped_short += 1
            continue
        spl = splits_map.get(tk, [])
        base_close = float(fwd.iloc[0]["close"])  # signal-day CLOSE = the price we "failed to sell at"
        if base_close <= 0:
            continue
        row = {
            "ticker": tk, "session_date": str(ed), "mfcs": e["mfcs"], "gap_pct": e["gap_pct"],
            "gap_classification": e["gap_classification"], "rvol": e["rvol"], "phase": e["phase"],
            "eval_price": e["eval_price"], "base_close": base_close,
        }
        # forward adjusted path d1..horizon
        run_high = base_close
        suspected_split = False
        for k in range(1, horizon + 1):
            b = fwd.iloc[k]
            pb = fwd.iloc[k - 1]
            fd = b["d"]
            af = adj_factor(spl, ed, fd)
            af_prev = adj_factor(spl, ed, pb["d"])
            adj_open = float(b["open"]) * af
            adj_high = float(b["high"]) * af
            adj_low = float(b["low"]) * af
            adj_close = float(b["close"]) * af
            prev_adj_close = float(pb["close"]) * af_prev
            # UNCAPTURED-SPLIT GUARD (doc 229): splits.parquet is stale (max 5/18) and incomplete
            # (ASBP 5/11 30x, WGRX 5/26 47x not in it). A reverse split shows as a >2.5x overnight
            # price jump with volume CRATERING (<0.25x) — real squeezes EXPAND volume. When the
            # recorded split factor did NOT change across the day (af==af_prev) yet we see that
            # signature, it's almost certainly a missing split -> flag + exclude from stats so the
            # tail isn't a phantom skyrocket.
            pr = (adj_open / prev_adj_close) if prev_adj_close > 0 else 1.0
            pv = float(pb["volume"]) or 1.0
            vr = (float(b["volume"]) / pv) if pv > 0 else 1.0
            if af == af_prev and ((pr > 2.5 and vr < 0.25) or (pr < 0.40 and vr < 0.25)):
                suspected_split = True
            run_high = max(run_high, adj_high)
            row[f"d{k}_close_ret"] = adj_close / base_close - 1.0
            row[f"d{k}_high_ret"] = adj_high / base_close - 1.0
            row[f"d{k}_low_ret"] = adj_low / base_close - 1.0
            row[f"d{k}_open_gap"] = (adj_open / prev_adj_close - 1.0) if prev_adj_close > 0 else 0.0
            row[f"run_high_thru_d{k}"] = run_high / base_close - 1.0
            row[f"d{k}_split_adj"] = af  # < 1.0 flags a reverse split inside the window
        row["suspected_uncaptured_split"] = suspected_split
        out.append(row)

    df = pd.DataFrame(out)
    print(f"built {len(df)} entries with full {horizon}d window "
          f"(dropped: {dropped_nodata} no-bars, {dropped_short} short-window; warehouse_max={warehouse_max})")
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    df.to_parquet(OUT, index=False)
    print(f"wrote {OUT}")
    return df


def summarize(df: pd.DataFrame, horizon: int):
    if df.empty:
        print("no rows to summarize")
        return
    n_split = int(df.get("suspected_uncaptured_split", pd.Series(dtype=bool)).sum())
    df = df[~df.get("suspected_uncaptured_split", False)].copy()
    print(f"\n[clean] excluded {n_split} suspected UNCAPTURED-SPLIT entries (reverse-split "
          f"phantoms missing from splits.parquet); {len(df)} clean entries remain")
    print("\n=== MULTI-DAY HOLD: forward CLOSE return vs signal-day close (split-adjusted, clean) ===")
    print(f"{'horizon':>8} {'n':>5} {'median':>8} {'mean':>8} {'p25':>7} {'p75':>7} {'%>+10%':>7} {'%<-10%':>7}")
    for k in range(1, horizon + 1):
        s = df[f"d{k}_close_ret"].dropna()
        n = len(s)
        up = (s > 0.10).mean() * 100
        dn = (s < -0.10).mean() * 100
        print(f"  d{k:<6} {n:>5} {s.median()*100:>7.1f}% {s.mean()*100:>7.1f}% "
              f"{s.quantile(.25)*100:>6.1f}% {s.quantile(.75)*100:>6.1f}% {up:>6.1f}% {dn:>6.1f}%")

    print("\n=== BEST-CASE (run-high through dN: perfect trail) vs signal-day close ===")
    for k in range(1, horizon + 1):
        s = df[f"run_high_thru_d{k}"].dropna()
        print(f"  thru d{k}: median peak {s.median()*100:+.1f}%  mean {s.mean()*100:+.1f}%  "
              f"p75 {s.quantile(.75)*100:+.1f}%  max {s.max()*100:+.0f}%")

    print("\n=== by MFCS bucket: does signal STRENGTH predict multi-day continuation? (d{} close) ===".format(horizon))
    dd = df.dropna(subset=["mfcs"]).copy()
    dd["bucket"] = pd.cut(dd["mfcs"], [0, 0.3, 0.45, 0.55, 1.0])
    col = f"d{horizon}_close_ret"
    for b, g in dd.groupby("bucket", observed=True):
        s = g[col].dropna()
        if len(s) < 5:
            continue
        winrate = (s > 0).mean()
        lo, hi = wilson(winrate, len(s))
        print(f"  MFCS {str(b):>12}: n={len(s):>4} median {s.median()*100:>+6.1f}%  "
              f"P(up)={winrate*100:>4.0f}% CI[{lo*100:.0f},{hi*100:.0f}]")

    rs = (df.filter(like="_split_adj") < 0.99).any(axis=1).sum()
    print(f"\n[guard] {rs} entries had a reverse split inside the window (correctly de-inflated, "
          f"not phantom skyrockets)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=5)
    ap.add_argument("--min-mfcs", type=float, default=None)
    a = ap.parse_args()
    df = build(a.horizon, a.min_mfcs)
    summarize(df, a.horizon)
