"""DOC 290 STAGE 0 — the event universe + embedding (build only; NO claims made here).

EVENT UNIT (frozen): one event per (name, session). t0 = the name's first morning snapshot (<=10:30 ET);
OBSERVATION window = minute bars in [t0, t0+15); DECISION time = t0+15min. All features are strictly
pre-decision; all outcomes are strictly post-decision, from the authoritative minute-bar warehouse
(feature-file current_price is stale 57% of the time — never an outcome source, per doc 289).

Why this unit: it is the largest leakage-safe universe with pre-decision features (rvol/float/pmv/news/mfcs
exist only for scanner cohorts); the 15-min observation window buys path-SHAPE features (the "micro-trend"
signature Pierce's vector-DB idea retrieves on) at the price of a 15-min-later decision. One event per
name-session avoids overlapping-window pseudo-replication; effective N is reported, not inflated.

FEATURES (all pre-decision):
  absolute  : gap_pct, rvol, float_shares, premarket_volume, market_cap, price, has_news, mfcs,
              float_rotation (+ missingness indicators)
  attention : pm_dvol_share (name's share of cohort premarket $vol), cohort premarket Herfindahl,
              cohort_size, cohort dispersion (sd of gap/rvol/float_rotation), centroid_dist,
              z/rank of gap,rvol,price,mfcs,float_rotation within cohort  (doc-289 field features)
  path shape: r5,r10,r15 (window returns), maxdrawup15, maxdd15, range15, vwap_dev15,
              higher_low_frac, vol_slope15, dvol15_share (share of cohort window $vol)
OUTCOMES (post-decision, warehouse): peak_run60 (max high in (15,75] / decision_px - 1),
  ret_close (session close / decision_px - 1), maxdd60, stop10_hit (maxdd60<=-10%).

Outputs: data/research/doc290/events.jsonl + datasheet data/research/doc290/stage0_datasheet.json
(coverage, missingness, N, sessions, ICC + effective N, regime split counts).
"""
from __future__ import annotations
import json, math, os, sys
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
os.chdir(_ROOT); sys.path.insert(0, str(_ROOT / "scripts"))
FEATURES = _ROOT / "data" / "features"
OUT = _ROOT / "data" / "research" / "doc290"
OUT.mkdir(parents=True, exist_ok=True)

OBS_MIN = 15.0          # observation window length (frozen)
FWD_MIN = 60.0          # outcome window after decision (frozen)
MIN_OBS_BARS = 5        # need >=5 bars in the window (thin names dropped, counted)
MIN_COHORT = 5
REGIME_SPLIT = "2026-05-26"


def _f(x, d=None):
    try:
        return float(x) if x is not None else d
    except Exception:
        return d


def zscores(vals):
    xs = [v for v in vals if v is not None]
    if len(xs) < 2:
        return [0.0] * len(vals)
    mu = sum(xs) / len(xs)
    sd = (sum((x - mu) ** 2 for x in xs) / (len(xs) - 1)) ** 0.5 or 1.0
    return [((v - mu) / sd if v is not None else 0.0) for v in vals]


def ranks(vals):
    idx = [(i, v) for i, v in enumerate(vals) if v is not None]
    out = [0.5] * len(vals)
    if not idx:
        return out
    idx.sort(key=lambda t: t[1])
    for r, (i, _v) in enumerate(idx):
        out[i] = (r + 0.5) / len(idx)
    return out


def path_features(bars):
    """bars: (off,lo,op,hi,cl,vol) with 0<=off<OBS_MIN, sorted. Returns dict or None if unusable."""
    if len(bars) < MIN_OBS_BARS:
        return None
    o0 = bars[0][2] if bars[0][2] > 0 else bars[0][4]
    if not o0 or o0 <= 0:
        return None
    def px_at(m):
        c = [b[4] for b in bars if b[0] <= m]
        return c[-1] if c else o0
    hi = max(b[3] for b in bars); lo = min(b[1] for b in bars)
    closes = [b[4] for b in bars]; lows = [b[1] for b in bars]; vols = [b[5] for b in bars]
    cum_dv = sum(b[5] * b[4] for b in bars); cum_v = sum(vols) or 1.0
    vwap = cum_dv / cum_v
    px15 = closes[-1]
    hl = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1]) / max(len(lows) - 1, 1)
    half = len(vols) // 2 or 1
    vs = (sum(vols[half:]) - sum(vols[:half])) / (sum(vols) or 1.0)
    return {
        "r5": px_at(5.0) / o0 - 1, "r10": px_at(10.0) / o0 - 1, "r15": px15 / o0 - 1,
        "maxdrawup15": hi / o0 - 1, "maxdd15": lo / o0 - 1, "range15": (hi - lo) / o0,
        "vwap_dev15": px15 / vwap - 1 if vwap > 0 else 0.0,
        "higher_low_frac": hl, "vol_slope15": vs, "_win_dvol": cum_dv, "_decision_px": px15,
    }


def main():
    import duckdb
    import fill_model_backtest as F
    import _doc280_posture_backtest as B
    con = duckdb.connect(); con.execute("SET threads=4")

    events = []
    ds = {"sessions_total": 0, "sessions_used": 0, "names_seen": 0, "dropped_no_bars": 0,
          "dropped_thin_window": 0, "dropped_no_outcome": 0, "obs_min": OBS_MIN, "fwd_min": FWD_MIN}

    for fp in sorted(FEATURES.glob("features_2026-*.jsonl")):
        date = fp.stem.replace("features_", "")
        ds["sessions_total"] += 1
        rows = [json.loads(l) for l in fp.read_text(encoding="utf-8").splitlines() if l.strip()]
        byt = defaultdict(list)
        for r in rows:
            byt[r["ticker"]].append(r)
        members = []
        for tk, snaps in byt.items():
            m = [r for r in snaps if r.get("timestamp") and (
                r.get("hour_et") == 9 or (r.get("hour_et") == 10 and (r.get("minute_et") or 0) <= 30))]
            if not m:
                continue
            ds["names_seen"] += 1
            m.sort(key=lambda r: r["timestamp"])
            r0 = m[0]
            try:
                ts0 = F._parse_ts(r0["timestamp"])
            except Exception:
                continue
            lb = B._load_local_bars(con, tk, ts0)
            if not lb or not lb["bars"] or not lb["close_px"]:
                ds["dropped_no_bars"] += 1
                continue
            bars = lb["bars"]
            obs = [b for b in bars if 0 <= b[0] < OBS_MIN]
            pf = path_features(sorted(obs, key=lambda b: b[0]))
            if pf is None:
                ds["dropped_thin_window"] += 1
                continue
            dpx = pf.pop("_decision_px"); win_dvol = pf.pop("_win_dvol")
            fwd = [b for b in bars if OBS_MIN <= b[0] <= OBS_MIN + FWD_MIN]
            if not fwd or dpx <= 0:
                ds["dropped_no_outcome"] += 1
                continue
            peak60 = max(b[3] for b in fwd) / dpx - 1
            dd60 = min(b[1] for b in fwd) / dpx - 1
            retc = lb["close_px"] / dpx - 1
            # per-window peaks for Workstream B family F5 (time-of-day coupling windows)
            def _peak(mins):
                w = [b for b in bars if OBS_MIN <= b[0] <= OBS_MIN + mins]
                return (max(b[3] for b in w) / dpx - 1) if w else None
            peak15, peak30 = _peak(15.0), _peak(30.0)
            fullw = [b for b in bars if b[0] >= OBS_MIN]
            peak_full = (max(b[3] for b in fullw) / dpx - 1) if fullw else None
            fl = _f(r0.get("float_shares")); pmv = _f(r0.get("premarket_volume"))
            px0 = _f(r0.get("current_price")) or dpx
            ev = {"date": date, "ticker": tk, "ts0": r0["timestamp"],
                  "gap_pct": _f(r0.get("gap_pct"), 0.0), "rvol": _f(r0.get("rvol"), 0.0),
                  "float_shares": fl, "premarket_volume": pmv, "market_cap": _f(r0.get("market_cap")),
                  "price": px0, "has_news": 1.0 if r0.get("has_news_catalyst") else 0.0,
                  "mfcs": _f(r0.get("mfcs")), "float_rotation": (pmv / fl) if (pmv and fl and fl > 0) else None,
                  "pm_dvol": (pmv * px0) if pmv else None, "win_dvol": win_dvol,
                  **pf,
                  "peak_run60": peak60, "ret_close": retc, "maxdd60": dd60,
                  "peak_run15": peak15, "peak_run30": peak30, "peak_run_full": peak_full,
                  "stop10_hit": 1 if dd60 <= -0.10 else 0}
            members.append(ev)
        if len(members) < MIN_COHORT:
            continue
        ds["sessions_used"] += 1
        n = len(members)
        # attention-field + relative features (within cohort, pre-decision)
        tot_pm = sum(m["pm_dvol"] for m in members if m["pm_dvol"]) or None
        tot_win = sum(m["win_dvol"] for m in members) or 1.0
        pm_sh = [(m["pm_dvol"] / tot_pm if (m["pm_dvol"] and tot_pm) else None) for m in members]
        pmH = sum(s * s for s in pm_sh if s is not None) if tot_pm else None
        for feat in ["gap_pct", "rvol", "price", "mfcs", "float_rotation"]:
            vals = [m.get(feat) for m in members]
            for m, z, r in zip(members, zscores(vals), ranks(vals)):
                m[f"z_{feat}"] = z; m[f"rank_{feat}"] = r
        gf = ["z_gap_pct", "z_rvol", "z_price", "z_float_rotation"]
        for feat in ["gap_pct", "rvol", "float_rotation"]:
            xs = [m[feat] for m in members if m.get(feat) is not None]
            sd = (sum((x - sum(xs) / len(xs)) ** 2 for x in xs) / len(xs)) ** 0.5 if len(xs) > 1 else 0.0
            for m in members:
                m[f"cohort_sd_{feat}"] = sd
        for i, m in enumerate(members):
            m["cohort_size"] = n
            m["centroid_dist"] = math.sqrt(sum(m[g] ** 2 for g in gf))
            m["pm_dvol_share"] = pm_sh[i]
            m["cohort_pm_herfindahl"] = pmH
            m["dvol15_share"] = m["win_dvol"] / tot_win
        events.extend(members)
    con.close()

    (OUT / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")

    # datasheet: effective N via ICC on primary outcome (one-way ANOVA estimator)
    import numpy as np
    sess = sorted({e["date"] for e in events})
    ds["n_events"] = len(events); ds["n_sessions"] = len(sess)
    ds["events_per_session_mean"] = round(len(events) / max(len(sess), 1), 1)
    groups = defaultdict(list)
    for e in events:
        groups[e["date"]].append(e["peak_run60"])
    y = np.array([e["peak_run60"] for e in events]); gm = y.mean()
    ssb = sum(len(v) * (np.mean(v) - gm) ** 2 for v in groups.values())
    ssw = sum(sum((x - np.mean(v)) ** 2 for x in v) for v in groups.values())
    k = len(groups); N = len(y)
    msb = ssb / max(k - 1, 1); msw = ssw / max(N - k, 1)
    n0 = (N - sum(len(v) ** 2 for v in groups.values()) / N) / max(k - 1, 1)
    icc = (msb - msw) / (msb + (n0 - 1) * msw) if (msb + (n0 - 1) * msw) > 0 else 0.0
    icc = max(icc, 0.0)
    m_bar = N / k
    ds["icc_peak_run60"] = round(float(icc), 4)
    ds["effective_N"] = round(N / (1 + (m_bar - 1) * icc), 1)
    ds["regime_split"] = REGIME_SPLIT
    ds["events_early"] = sum(1 for e in events if e["date"] < REGIME_SPLIT)
    ds["events_late"] = sum(1 for e in events if e["date"] >= REGIME_SPLIT)
    ds["missing_float_pct"] = round(100 * sum(1 for e in events if e["float_shares"] is None) / N, 1)
    ds["missing_pm_dvol_pct"] = round(100 * sum(1 for e in events if e["pm_dvol"] is None) / N, 1)
    (OUT / "stage0_datasheet.json").write_text(json.dumps(ds, indent=2), encoding="utf-8")
    print(json.dumps(ds, indent=2))


if __name__ == "__main__":
    main()
