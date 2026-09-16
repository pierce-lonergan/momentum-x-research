"""Label the eval corpus with intraday CONTINUATION outcomes from minute_aggs (doc 231 / BET #1).

For every (ticker, session_date) the bot evaluated, pull that day's regular-session minute bars and,
at each decision point (every --cadence min), emit one labeled row:

  FEATURES@t (all computable LIVE from minute bars up to t — no look-ahead):
    minute_et, ret_session, ret_5m, ret_15m, ret_30m, vwap_dist, high_dist, low_dist, range_pos,
    rvol_cum, vol_accel_5m, realized_vol_15m, up_min_frac_15m, gap_pct, mfcs  (+ entry context)

  LABELS@t (forward over the next --horizon min — the thing a live model can't see):
    fwd_mfe, fwd_mae, fwd_ret_h, continued (TRIPLE-BARRIER: +up before -down within horizon),
    time_to_up, exit_better_than_now (did holding beat exiting at t?)

This is the training corpus for the continuation-EXIT classifier (decide hold/scale/dump per minute).
Triple-barrier / meta-labeling per Lopez de Prado. RTH only (09:30-16:00 ET). No look-ahead: every
feature uses data <= t; every label uses data > t.

Usage:
  python scripts/build_exit_label_corpus.py --cadence 5 --horizon 30 --up 0.05 --down 0.05
"""
from __future__ import annotations

import argparse
import glob
import json

import duckdb
import numpy as np
import pandas as pd

JOURNAL_GLOB = "data/journals/journal_*.jsonl"
MIN_GLOB = "data/polygon_warehouse/minute_aggs/**/*.parquet"
OUT = "data/research/exit_labels.parquet"


def load_eval_daykeys():
    """One context row per (ticker, session_date): earliest eval that day + its mfcs/gap/rvol."""
    best = {}
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
                    tk, sd, ts = r.get("ticker"), r.get("session_date"), r.get("timestamp", "")
                    if not tk or not sd:
                        continue
                    k = (tk, sd)
                    if k not in best or ts < best[k]["_ts"]:
                        best[k] = {"ticker": tk, "session_date": sd, "_ts": ts,
                                   "gap_pct": r.get("gap_pct"), "rvol": r.get("rvol"),
                                   "mfcs": r.get("mfcs")}
        except Exception:
            continue
    return pd.DataFrame(best.values())


def label_one_day(g, ctx, cadence, horizon, up, down):
    """g: a single ticker-day's RTH minute bars (sorted). Returns list of labeled row dicts."""
    g = g.sort_values("ts_et").reset_index(drop=True)
    n = len(g)
    if n < cadence + horizon + 5:
        return []
    px = g["close"].to_numpy(float)
    hi = g["high"].to_numpy(float)
    lo = g["low"].to_numpy(float)
    vol = g["volume"].to_numpy(float)
    open_px = float(g["open"].iloc[0]) or px[0]
    # session VWAP (running)
    typ = (hi + lo + px) / 3.0
    cum_pv = np.cumsum(typ * vol)
    cum_v = np.cumsum(vol)
    vwap = np.where(cum_v > 0, cum_pv / np.maximum(cum_v, 1e-9), px)
    run_hi = np.maximum.accumulate(hi)
    run_lo = np.minimum.accumulate(lo)
    ret1 = np.zeros(n)
    ret1[1:] = px[1:] / np.maximum(px[:-1], 1e-9) - 1.0

    rows = []
    # decision points every `cadence` minutes, leaving room for the forward horizon
    for t in range(cadence, n - horizon, cadence):
        p = px[t]
        if p <= 0:
            continue
        w5, w15, w30 = max(0, t - 5), max(0, t - 15), max(0, t - 30)
        # ---- FEATURES (data <= t only) ----
        feat = {
            "ticker": ctx["ticker"], "session_date": ctx["session_date"],
            "minute_idx": t,
            "ret_session": p / open_px - 1.0,
            "ret_5m": p / max(px[w5], 1e-9) - 1.0,
            "ret_15m": p / max(px[w15], 1e-9) - 1.0,
            "ret_30m": p / max(px[w30], 1e-9) - 1.0,
            "vwap_dist": p / max(vwap[t], 1e-9) - 1.0,
            "high_dist": p / max(run_hi[t], 1e-9) - 1.0,   # <=0: how far below the day high
            "low_dist": p / max(run_lo[t], 1e-9) - 1.0,    # >=0: how far above the day low
            "range_pos": (p - run_lo[t]) / max(run_hi[t] - run_lo[t], 1e-9),
            "rvol_cum": cum_v[t] / max(cum_v[min(t, 30)] / max(min(t, 30), 1), 1e-9),  # vs first-30m pace
            "vol_accel_5m": vol[w5:t].sum() / max(vol[max(0, t - 10):w5].sum(), 1e-9),
            "realized_vol_15m": float(np.std(ret1[w15:t + 1])) if t - w15 > 2 else 0.0,
            "up_min_frac_15m": float((ret1[w15:t + 1] > 0).mean()) if t > w15 else 0.5,
            "gap_pct": ctx.get("gap_pct"), "rvol_entry": ctx.get("rvol"), "mfcs": ctx.get("mfcs"),
        }
        # ---- LABELS (data > t only; triple barrier over the next `horizon` min) ----
        fwd_hi = hi[t + 1:t + 1 + horizon]
        fwd_lo = lo[t + 1:t + 1 + horizon]
        fwd_cl = px[t + 1:t + 1 + horizon]
        up_lvl, dn_lvl = p * (1 + up), p * (1 - down)
        up_hits = np.where(fwd_hi >= up_lvl)[0]
        dn_hits = np.where(fwd_lo <= dn_lvl)[0]
        t_up = up_hits[0] if len(up_hits) else 10 ** 9
        t_dn = dn_hits[0] if len(dn_hits) else 10 ** 9
        continued = int(t_up < t_dn)            # upper barrier first = continuation
        feat["fwd_mfe"] = float(fwd_hi.max() / p - 1.0) if len(fwd_hi) else 0.0
        feat["fwd_mae"] = float(fwd_lo.min() / p - 1.0) if len(fwd_lo) else 0.0
        feat["fwd_ret_h"] = float(fwd_cl[-1] / p - 1.0) if len(fwd_cl) else 0.0
        feat["continued"] = continued
        feat["time_to_up"] = int(t_up) if t_up < 10 ** 9 else -1
        # did HOLDING beat exiting now? (forward best vs 0)
        feat["hold_beats_exit"] = int(feat["fwd_mfe"] > up and t_up < t_dn)
        rows.append(feat)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cadence", type=int, default=5)
    ap.add_argument("--horizon", type=int, default=30)
    ap.add_argument("--up", type=float, default=0.05)
    ap.add_argument("--down", type=float, default=0.05)
    a = ap.parse_args()

    keys = load_eval_daykeys()
    keys["yyyymm"] = keys["session_date"].str[:7]
    tickers = sorted(keys["ticker"].dropna().unique().tolist())
    print(f"eval ticker-days: {len(keys)} | distinct tickers: {len(tickers)} "
          f"| months: {sorted(keys['yyyymm'].unique())}")

    con = duckdb.connect()
    tk_list = ",".join("'" + t.replace("'", "") + "'" for t in tickers)
    # pull all 2026 RTH-ish minute bars for our tickers in one partition-pruned scan
    print("querying minute_aggs (year=2026, eval tickers)...")
    bars = con.execute(f"""
        SELECT ticker, ts_et, open, high, low, close, volume
        FROM read_parquet('{MIN_GLOB}', hive_partitioning=1)
        WHERE year=2026 AND ticker IN ({tk_list})
    """).df()
    print(f"  pulled {len(bars):,} minute rows")
    bars["ts_et"] = pd.to_datetime(bars["ts_et"], utc=True).dt.tz_convert("America/New_York")
    bars["d"] = bars["ts_et"].dt.strftime("%Y-%m-%d")
    # RTH only: 09:30 <= t < 16:00 ET
    mins = bars["ts_et"].dt.hour * 60 + bars["ts_et"].dt.minute
    bars = bars[(mins >= 570) & (mins < 960)].copy()

    ctx_by_key = {(r.ticker, r.session_date): {"ticker": r.ticker, "session_date": r.session_date,
                  "gap_pct": r.gap_pct, "rvol": r.rvol, "mfcs": r.mfcs} for r in keys.itertuples()}

    out = []
    have = no_bars = 0
    for (tk, d), g in bars.groupby(["ticker", "d"]):
        ctx = ctx_by_key.get((tk, d))
        if ctx is None:
            continue
        r = label_one_day(g, ctx, a.cadence, a.horizon, a.up, a.down)
        if r:
            out.extend(r); have += 1
        else:
            no_bars += 1
    # ticker-days with NO minute coverage at all
    covered = {(tk, d) for (tk, d), _ in bars.groupby(["ticker", "d"])}
    missing = sum(1 for k in ctx_by_key if k not in covered)

    df = pd.DataFrame(out)
    print(f"\nlabeled ticker-days: {have} | too-few-bars: {no_bars} | no-minute-coverage: {missing}")
    print(f"labeled rows: {len(df):,}")
    if not df.empty:
        df.to_parquet(OUT, index=False)
        print(f"wrote {OUT}")
        print(f"\n=== label balance (cadence={a.cadence}m horizon={a.horizon}m barrier +{a.up:.0%}/-{a.down:.0%}) ===")
        print(f"  continued (up-barrier first): {df['continued'].mean()*100:.1f}%")
        print(f"  hold_beats_exit:              {df['hold_beats_exit'].mean()*100:.1f}%")
        print(f"  median fwd_mfe {df['fwd_mfe'].median()*100:+.1f}%  median fwd_mae {df['fwd_mae'].median()*100:+.1f}%")
        # sanity: does a feature already separate? (high_dist = distance below day high)
        print("\n=== quick signal check: P(continue) by feature tercile ===")
        for col in ["high_dist", "vwap_dist", "ret_15m", "range_pos", "rvol_cum"]:
            try:
                df["_b"] = pd.qcut(df[col], 3, labels=["low", "mid", "high"], duplicates="drop")
                rates = df.groupby("_b", observed=True)["continued"].mean()
                print(f"  {col:>16}: " + "  ".join(f"{lab}={v*100:.0f}%" for lab, v in rates.items()))
            except Exception:
                pass


if __name__ == "__main__":
    main()
