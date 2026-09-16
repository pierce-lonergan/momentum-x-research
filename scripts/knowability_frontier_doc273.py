"""doc 273 — THE KNOWABILITY FRONTIER (pre-registered research, DORMANT-C, read-only vs production).

THE QUESTION: as a function of elapsed session time t, how much information about the cost-cleared
remainder-of-day outcome exists in the consolidated tape-so-far — I(t) — and at the first t where
I(t) is detectably non-zero (if any), does the remaining move still pay for acting — A(t)?

Universe: the doc-235 certified-null gapper gate, inherited verbatim (gap>=8% open-vs-prev-close,
open in [$0.50,$20], trailing-20d ADV>=$1M), restricted to the tick-warehouse window 2025-08-01..
2026-04-30 (~3,553 ticker-days, 134 days, median 20 names/day).

Timestamps: ET derived from raw sip_timestamp ONLY (ns UTC -> America/New_York). Never ts_et.

Curves (the deliverable, one row per grid time t):
  I(t)  information: out-of-fold log-loss reduction (nats/row) of tape-prefix classifiers vs the
        train-fold base rate, max over 2 frozen families (HistGBM, rank-Gauss logistic);
        inference = within-day label permutation, max-over-(t,family) statistic (Westfall-Young).
  M(t)  oracle frontier: base rate of the cost-cleared label + net payoff of the true-label class
        (what perfect knowledge at t would earn per trade, winsorized 1/99).
  A(t)  achieved economics: mean net return of the top-decile by GBM out-of-fold score (the
        pre-specified primary family), winsorized 1/99, day-block bootstrap 95% CI.
  MDE(t) power: planted-signal recovery — labels partially flipped toward a designated prefix
        statistic at known strength s in {0.05,0.10,0.20}; MDE = smallest s the full pipeline
        recovers above the permutation threshold. A null without power proof is not a null.

Label (primary): y_t = 1[ exit_vwap/entry_vwap - 1 > 0.01 ]  (1% = the doc-250 microcap round-trip).
  entry_vwap = size-weighted price of prints in [t+5s, t+300s) (>=3 prints and >=500 sh else
  dropout at t, counted); exit_vwap = prints in [15:50,16:00) (fallback: last print >=15:00).
  Sensitivity thetas: 0.005, 0.015. Features use prints STRICTLY before t. Halts are FEATURES
  (gap counts/durations/staleness), never exclusions.

Verdicts (frozen before running — see docs/research-log/273_knowability_frontier.md):
  V-NULL   max-(t,fam) permutation p >= 0.05  -> no detectable tape information anywhere, with MDE.
  V-RACE   I significant somewhere, but A(t) CI includes/below 0 at every significant t
           -> the information arrives only after the move it would predict has been spent.
  V-WINDOW I significant AND A(t) CI > 0 at some t -> a LOCATED HYPOTHESIS (not an edge) ->
           doc-234 hostile gauntlet next session.
  V-UNPOWERED plants fail to recover even s=0.20 -> no conclusion; reported as such.

Usage:
  python scripts/knowability_frontier_doc273.py --build            # spine (features+labels)
  python scripts/knowability_frontier_doc273.py --run              # real curves + bootstraps
  python scripts/knowability_frontier_doc273.py --null 100         # permutation null (joblib)
  python scripts/knowability_frontier_doc273.py --plant            # planted-signal MDE
  python scripts/knowability_frontier_doc273.py --verdict          # assemble pre-registered verdict
"""
from __future__ import annotations
import argparse, json, os, sys, warnings
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

PANEL = "data/research/exit_labels_cross_regime.parquet"
TRADES = "data/polygon_warehouse/trades_v1_parquet"
SPINE = "data/research/doc273_spine.parquet"
RESULTS = "data/research/doc273_results.json"
NULLF = "data/research/doc273_null.parquet"
PLANTF = "data/research/doc273_plants.parquet"
FRONTIER = "data/research/doc273_frontier.parquet"

LO, HI = "2025-08-01", "2026-04-30"
GRID_MIN = [575, 580, 585, 590, 600, 615, 630, 660, 690, 720, 780, 840, 900]  # ET min-of-day
GRID_SEC = [m * 60 for m in GRID_MIN]
GRID_LBL = ["9:35", "9:40", "9:45", "9:50", "10:00", "10:15", "10:30",
            "11:00", "11:30", "12:00", "13:00", "14:00", "15:00"]
RTH0, RTH1 = 34200, 57600          # 9:30, 16:00 ET in sec-of-day
EXIT0, EXIT1, EXIT_FB = 57000, 57600, 54000   # 15:50-16:00 exit window; 15:00 fallback floor
THETA = 0.01                        # primary round-trip cost (doc-250 microcap standard)
THETAS = [0.005, 0.01, 0.015]
ENTRY_MIN_PRINTS, ENTRY_MIN_SH = 3, 500
PLANT_T = [580, 600, 690, 840]      # 9:40, 10:00, 11:30, 14:00
PLANT_S = [0.05, 0.10, 0.20]
PLANT_REPS = 5
TOPF = 0.10                         # A(t) top-decile
WINS = (0.01, 0.99)
NFOLD = 6

FEATURES = [
    # premarket (04:00-9:30 prints)
    "pm_logvol", "pm_logdollar", "pm_ret", "pm_ofi", "pm_logtrades", "pm_to_open",
    # RTH prefix path (prints strictly < t)
    "ret_open_t", "ret_5m", "ret_15m", "dd_from_high", "up_from_low", "range_pos",
    "t_of_high_frac", "t_of_low_frac", "rv_1m",
    # VWAP
    "vwap_dist", "vwap_slope_10m",
    # volume
    "log_cumvol", "log_cumdollar", "dollar_vs_adv", "vol_last5_frac", "vol_accel",
    # tape
    "ofi_cum", "ofi_last10", "mean_logsize", "large_print_frac", "odd_lot_frac",
    "max_print_frac", "intensity", "intensity_last5_ratio",
    # gaps / halt structure (halts are features, not exclusions)
    "n_gaps_60", "n_gaps_300", "max_gap_log", "t_since_print_log", "ret_across_max_gap", "stale_at_t",
    # static ex-ante context
    "log_open_px", "log_adv20",
]


# ───────────────────────────── spine build ─────────────────────────────

def _corpus() -> pd.DataFrame:
    import duckdb
    df = duckdb.connect().execute(
        f"""SELECT DISTINCT ticker, session_date, base_close, adv20
            FROM read_parquet('{PANEL}')
            WHERE session_date BETWEEN '{LO}' AND '{HI}'"""
    ).df()
    return df.sort_values(["session_date", "ticker"]).reset_index(drop=True)


def _read_day(ticker: str, d: str):
    """One ticker-day of prints with ET sec-of-day derived from sip_timestamp ONLY.

    ParquetFile (not read_table): the hive path (year=/month=/...) makes read_table
    infer partition fields that collide with the in-file year column. Partitions can
    be multi-file (data_0, data_1, ...) — read them all.
    """
    import glob as _glob
    import pyarrow.parquet as pq
    y, m, dd = d[:4], d[5:7], d[8:10]
    parts = sorted(_glob.glob(f"{TRADES}/year={y}/month={m}/day={dd}/ticker={ticker}/data_*.parquet"))
    if not parts:
        return None
    try:
        t = pd.concat([pq.ParquetFile(p).read(columns=["sip_timestamp", "price", "size"]).to_pandas()
                       for p in parts], ignore_index=True)
    except Exception:
        return None
    t = t[(t["price"] > 0) & (t["size"] > 0)]
    if len(t) < 5:
        return None
    et = pd.to_datetime(t["sip_timestamp"], utc=True).dt.tz_convert("America/New_York")
    sec = (et.dt.hour * 3600 + et.dt.minute * 60 + et.dt.second
           + et.dt.microsecond / 1e6).to_numpy()
    keep = (sec >= 14400) & (sec < RTH1)            # 04:00-16:00
    sec, px, sz = sec[keep], t["price"].to_numpy(float)[keep], t["size"].to_numpy(float)[keep]
    o = np.argsort(sec, kind="stable")
    return sec[o], px[o], sz[o]


def _tick_sign(px: np.ndarray) -> np.ndarray:
    s = np.sign(np.diff(px, prepend=px[0]))
    nz = s != 0
    idx = np.where(nz, np.arange(len(s)), 0)
    return s[np.maximum.accumulate(idx)]


def _day_rows(args):
    ticker, d, open_ref, adv20 = args
    got = _read_day(ticker, d)
    if got is None:
        return []
    sec, px, sz = got
    pm = sec < RTH0
    psec, ppx, psz = sec[pm], px[pm], sz[pm]
    rsec, rpx, rsz = sec[~pm], px[~pm], sz[~pm]
    if len(rpx) < 5:
        return []
    open_px = rpx[0]
    sign = _tick_sign(rpx)
    n = len(rpx)
    CV = np.concatenate([[0.0], np.cumsum(rsz)])
    CD = np.concatenate([[0.0], np.cumsum(rpx * rsz)])
    CS = np.concatenate([[0.0], np.cumsum(sign * rsz)])
    CN = np.arange(n + 1, dtype=float)
    run_hi = np.maximum.accumulate(rpx)
    run_lo = np.minimum.accumulate(rpx)

    # premarket block (fixed once)
    if len(ppx) >= 2:
        pmsign = _tick_sign(ppx)
        pm_feats = dict(
            pm_logvol=np.log1p(psz.sum()), pm_logdollar=np.log1p((ppx * psz).sum()),
            pm_ret=ppx[-1] / ppx[0] - 1.0,
            pm_ofi=float((pmsign * psz).sum() / max(psz.sum(), 1e-9)),
            pm_logtrades=np.log1p(len(ppx)), pm_to_open=open_px / ppx[-1] - 1.0)
    else:
        pm_feats = dict(pm_logvol=0.0, pm_logdollar=0.0, pm_ret=np.nan,
                        pm_ofi=np.nan, pm_logtrades=0.0, pm_to_open=np.nan)

    # exit (fixed once per day)
    ea, eb = np.searchsorted(rsec, EXIT0), np.searchsorted(rsec, EXIT1)
    if eb > ea:
        exit_px = float((rpx[ea:eb] * rsz[ea:eb]).sum() / rsz[ea:eb].sum())
    else:
        late = np.searchsorted(rsec, EXIT_FB)
        exit_px = float(rpx[-1]) if len(rpx) > late else np.nan

    out = []
    for sec_t, lbl in zip(GRID_SEC, GRID_LBL):
        i = int(np.searchsorted(rsec, sec_t))            # prints strictly before t
        row = dict(ticker=ticker, session_date=d, t_sec=sec_t, t_lbl=lbl,
                   log_open_px=np.log(max(open_px, 1e-9)), log_adv20=np.log(max(adv20, 1.0)))
        row.update(pm_feats)
        if i >= 2:
            last = rpx[i - 1]
            w5 = int(np.searchsorted(rsec, sec_t - 300))
            w10 = int(np.searchsorted(rsec, sec_t - 600))
            w15 = int(np.searchsorted(rsec, sec_t - 900))
            vol = CV[i]; dol = CD[i]
            vwap = dol / max(vol, 1e-9)
            vwap10 = ((CD[i] - CD[w10]) / max(CV[i] - CV[w10], 1e-9)) if i > w10 else vwap
            dt_gaps = np.diff(rsec[:i])
            if len(dt_gaps):
                gmax = int(np.argmax(dt_gaps))
                max_gap, ret_gap = float(dt_gaps[gmax]), float(rpx[gmax + 1] / rpx[gmax] - 1.0)
            else:
                max_gap, ret_gap = 0.0, 0.0
            elapsed = max(sec_t - RTH0, 1.0)
            ihi, ilo = int(np.argmax(rpx[:i])), int(np.argmin(rpx[:i]))
            # 1-min resampled realized vol (last print per minute)
            mids = (rsec[:i] // 60).astype(int)
            _, lastix = np.unique(mids[::-1], return_index=True)
            mpx = rpx[:i][::-1][lastix][::-1]
            rv = float(np.std(np.diff(np.log(np.maximum(mpx, 1e-9))))) if len(mpx) > 3 else 0.0
            v5 = CV[i] - CV[w5]; vprev5 = CV[w5] - CV[w10]
            row.update(
                ret_open_t=last / open_px - 1.0,
                ret_5m=last / max(rpx[w5 - 1] if w5 > 0 else open_px, 1e-9) - 1.0,
                ret_15m=last / max(rpx[w15 - 1] if w15 > 0 else open_px, 1e-9) - 1.0,
                dd_from_high=last / max(run_hi[i - 1], 1e-9) - 1.0,
                up_from_low=last / max(run_lo[i - 1], 1e-9) - 1.0,
                range_pos=(last - run_lo[i - 1]) / max(run_hi[i - 1] - run_lo[i - 1], 1e-9),
                t_of_high_frac=(rsec[ihi] - RTH0) / elapsed,
                t_of_low_frac=(rsec[ilo] - RTH0) / elapsed,
                rv_1m=rv,
                vwap_dist=last / max(vwap, 1e-9) - 1.0,
                vwap_slope_10m=vwap / max(vwap10, 1e-9) - 1.0,
                log_cumvol=np.log1p(vol), log_cumdollar=np.log1p(dol),
                dollar_vs_adv=dol / max(adv20, 1.0),
                vol_last5_frac=v5 / max(vol, 1e-9),
                vol_accel=v5 / max(vprev5, 1e-9),
                ofi_cum=CS[i] / max(vol, 1e-9),
                ofi_last10=(CS[i] - CS[w10]) / max(CV[i] - CV[w10], 1e-9) if i > w10 else 0.0,
                mean_logsize=np.log1p(vol / i),
                large_print_frac=float(rsz[:i][rsz[:i] >= 5000].sum() / max(vol, 1e-9)),
                odd_lot_frac=float((rsz[:i] < 100).mean()),
                max_print_frac=float(rsz[:i].max() / max(vol, 1e-9)),
                intensity=i / (elapsed / 60.0),
                intensity_last5_ratio=((i - w5) / 5.0) / max(i / (elapsed / 60.0), 1e-9),
                n_gaps_60=float((dt_gaps >= 60).sum()), n_gaps_300=float((dt_gaps >= 300).sum()),
                max_gap_log=np.log1p(max_gap),
                t_since_print_log=np.log1p(sec_t - rsec[i - 1]),
                ret_across_max_gap=ret_gap,
                stale_at_t=float(sec_t - rsec[i - 1] >= 300.0),
            )
        # executable entry at t
        ia, ib = int(np.searchsorted(rsec, sec_t + 5)), int(np.searchsorted(rsec, sec_t + 300))
        if ib - ia >= ENTRY_MIN_PRINTS and rsz[ia:ib].sum() >= ENTRY_MIN_SH and np.isfinite(exit_px):
            entry = float((rpx[ia:ib] * rsz[ia:ib]).sum() / rsz[ia:ib].sum())
            row["entry_px"], row["gross_ret"] = entry, exit_px / entry - 1.0
        else:
            row["entry_px"], row["gross_ret"] = np.nan, np.nan
        out.append(row)
    return out


def build():
    from multiprocessing import Pool
    corpus = _corpus()
    print(f"corpus ticker-days in window: {len(corpus)}")
    jobs = [(r.ticker, r.session_date, float(r.base_close), float(r.adv20))
            for r in corpus.itertuples()]
    rows, miss = [], 0
    with Pool(8) as pool:
        for i, rr in enumerate(pool.imap_unordered(_day_rows, jobs, chunksize=16)):
            if not rr:
                miss += 1
            rows.extend(rr)
            if (i + 1) % 500 == 0:
                print(f"  ...{i + 1}/{len(jobs)} ({miss} missing/thin)")
    df = pd.DataFrame(rows)
    df.to_parquet(SPINE, index=False)
    nvalid = df.gross_ret.notna().groupby(df.t_lbl).sum()
    print(f"WROTE {SPINE}: {len(df)} rows | {miss} ticker-days missing/thin")
    for lbl in GRID_LBL:
        s = df[df.t_lbl == lbl]
        v = s.gross_ret.notna()
        br = (s.gross_ret[v] > THETA).mean() if v.sum() else float("nan")
        print(f"  {lbl:>6}  valid {int(v.sum()):4d}/{len(s):4d} ({v.mean()*100:4.1f}%)  "
              f"base-rate(y@1%) {br*100:5.2f}%")


# ───────────────────────────── modelling ─────────────────────────────

def _families():
    from sklearn.ensemble import HistGradientBoostingClassifier as GBM
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.impute import SimpleImputer
    from sklearn.preprocessing import QuantileTransformer
    return {
        "gbm": lambda ntr: GBM(max_iter=300, learning_rate=0.05, max_depth=4,
                               l2_regularization=1.0),
        "logit": lambda ntr: make_pipeline(
            SimpleImputer(strategy="median"),
            QuantileTransformer(output_distribution="normal",
                                n_quantiles=min(1000, max(ntr, 10)), random_state=0),
            LogisticRegression(C=1.0, max_iter=2000)),
    }


def _fold_of_day(days_sorted):
    return {d: f for f, blk in enumerate(np.array_split(np.asarray(days_sorted), NFOLD))
            for d in blk}


def _dll_curve(X, y, day, fold, fams):
    """OOF log-loss reduction (nats/row) per family; also returns gbm OOF scores.

    eps=1e-4: bounded per-row loss (McAllester-Stratos boundedness requirement;
    pre-registered, not tuned — real and null runs share the identical metric).
    """
    eps = 1e-4
    out, gbm_oof = {}, np.full(len(y), np.nan)
    for name, mk in fams.items():
        oof = np.full(len(y), np.nan)
        for f in range(NFOLD):
            tr, te = fold != f, fold == f
            if te.sum() == 0 or len(np.unique(y[tr])) < 2:
                continue
            m = mk(int(tr.sum()))
            m.fit(X[tr], y[tr])
            p = m.predict_proba(X[te])[:, 1]
            base = np.full(te.sum(), y[tr].mean())
            oof[te] = p
            # store per-row base too via closure-free approach: recompute later from fold map
        ok = ~np.isnan(oof)
        if ok.sum() == 0:
            out[name] = np.nan
            continue
        # per-row base prob = train prevalence of that row's fold
        base = np.empty(len(y), float)
        for f in range(NFOLD):
            tr, te = fold != f, fold == f
            base[te] = y[tr].mean() if tr.sum() else y.mean()
        p, b, yy = np.clip(oof[ok], eps, 1 - eps), np.clip(base[ok], eps, 1 - eps), y[ok]
        ll_m = -(yy * np.log(p) + (1 - yy) * np.log(1 - p)).mean()
        ll_b = -(yy * np.log(b) + (1 - yy) * np.log(1 - b)).mean()
        out[name] = float(ll_b - ll_m)
        if name == "gbm":
            gbm_oof = oof
    return out, gbm_oof


def _load_spine():
    df = pd.read_parquet(SPINE)
    df["day"] = df.session_date
    return df


def _per_t(df, t_lbl, theta=THETA):
    s = df[(df.t_lbl == t_lbl) & df.gross_ret.notna()].reset_index(drop=True)
    X = s[FEATURES].to_numpy(float)
    y = (s.gross_ret.to_numpy(float) > theta).astype(int)
    days = sorted(s.day.unique())
    fmap = _fold_of_day(days)
    fold = s.day.map(fmap).to_numpy()
    return s, X, y, fold


def _wins(a, lo=WINS[0], hi=WINS[1]):
    if len(a) == 0:
        return a
    ql, qh = np.quantile(a, lo), np.quantile(a, hi)
    return np.clip(a, ql, qh)


def run_real():
    df = _load_spine()
    fams = _families()
    res = {"theta": THETA, "grid": GRID_LBL, "I": {}, "A": {}, "M": {}, "n": {}, "dropout": {}}
    rng = np.random.default_rng(7)
    frontier = []
    for lbl in GRID_LBL:
        s, X, y, fold = _per_t(df, lbl)
        all_t = df[df.t_lbl == lbl]
        res["n"][lbl] = int(len(s))
        res["dropout"][lbl] = float(1 - len(s) / max(len(all_t), 1))
        dll, gbm_oof = _dll_curve(X, y, s.day.to_numpy(), fold, fams)
        res["I"][lbl] = dll
        net = s.gross_ret.to_numpy(float) - THETA
        wnet = _wins(net)
        # M(t): oracle frontier
        res["M"][lbl] = dict(base_rate=float(y.mean()),
                             oracle_mean=float(wnet[y == 1].mean()) if y.sum() else np.nan,
                             oracle_med=float(np.median(net[y == 1])) if y.sum() else np.nan,
                             uncond_mean=float(wnet.mean()))
        # A(t): top-decile by gbm OOF, winsorized, day-block bootstrap CI
        ok = ~np.isnan(gbm_oof)
        if ok.sum() > 50:
            thr = np.quantile(gbm_oof[ok], 1 - TOPF)
            top = ok & (gbm_oof >= thr)
            a_mean = float(_wins(net[ok])[top[ok]].mean())
            days_u = s.day[ok].to_numpy()
            uds = np.unique(days_u)
            boots = []
            net_ok, top_ok = net[ok], top[ok]
            wnet_ok = _wins(net_ok)
            for _ in range(1000):
                pick = rng.choice(uds, len(uds), replace=True)
                m = np.concatenate([np.where(days_u == d)[0] for d in pick])
                tm = top_ok[m]
                boots.append(wnet_ok[m][tm].mean() if tm.sum() else np.nan)
            boots = np.array([b for b in boots if np.isfinite(b)])
            res["A"][lbl] = dict(mean=a_mean, lo=float(np.percentile(boots, 2.5)),
                                 hi=float(np.percentile(boots, 97.5)),
                                 raw_mean=float(net_ok[top_ok].mean()), n_top=int(top_ok.sum()))
        else:
            res["A"][lbl] = dict(mean=np.nan, lo=np.nan, hi=np.nan, raw_mean=np.nan, n_top=0)
        print(f"  {lbl:>6} n={len(s):4d} drop={res['dropout'][lbl]*100:4.1f}% "
              f"I(gbm)={dll.get('gbm', float('nan')):+.4f} I(logit)={dll.get('logit', float('nan')):+.4f} "
              f"base={res['M'][lbl]['base_rate']*100:5.2f}% oracle={res['M'][lbl]['oracle_mean']*100 if res['M'][lbl]['oracle_mean']==res['M'][lbl]['oracle_mean'] else float('nan'):+.1f}% "
              f"A={res['A'][lbl]['mean']*100 if res['A'][lbl]['mean']==res['A'][lbl]['mean'] else float('nan'):+.2f}% "
              f"[{res['A'][lbl]['lo']*100 if res['A'][lbl]['lo']==res['A'][lbl]['lo'] else float('nan'):+.2f},{res['A'][lbl]['hi']*100 if res['A'][lbl]['hi']==res['A'][lbl]['hi'] else float('nan'):+.2f}]")
        frontier.append(dict(t_lbl=lbl, n=len(s), dropout=res["dropout"][lbl],
                             I_gbm=dll.get("gbm"), I_logit=dll.get("logit"),
                             base_rate=res["M"][lbl]["base_rate"],
                             oracle_mean=res["M"][lbl]["oracle_mean"],
                             A_mean=res["A"][lbl]["mean"], A_lo=res["A"][lbl]["lo"],
                             A_hi=res["A"][lbl]["hi"]))
    # cost sensitivity on I (labels recomputed; gbm only, descriptive)
    res["I_sens"] = {}
    for th in THETAS:
        if th == THETA:
            continue
        vals = {}
        for lbl in GRID_LBL:
            s, X, y, fold = _per_t(df, lbl, theta=th)
            dll, _ = _dll_curve(X, y, s.day.to_numpy(), fold, {"gbm": _families()["gbm"]})
            vals[lbl] = dll["gbm"]
        res["I_sens"][str(th)] = vals
        print(f"  sensitivity theta={th}: max I(gbm) = {np.nanmax(list(vals.values())):+.4f}")
    # split-half robustness (descriptive)
    res["I_half"] = {}
    for half, (a, b) in {"H1": ("2025-08-01", "2025-12-31"), "H2": ("2026-01-01", "2026-04-30")}.items():
        vals = {}
        sub = df[(df.session_date >= a) & (df.session_date <= b)]
        for lbl in GRID_LBL:
            s, X, y, fold = _per_t(sub, lbl)
            if len(s) < 200:
                vals[lbl] = np.nan
                continue
            dll, _ = _dll_curve(X, y, s.day.to_numpy(), fold, {"gbm": _families()["gbm"]})
            vals[lbl] = dll["gbm"]
        res["I_half"][half] = vals
    json.dump(res, open(RESULTS, "w"), indent=1, default=float)
    pd.DataFrame(frontier).to_parquet(FRONTIER, index=False)
    s_star = float(np.nanmax([max(v.values()) for v in res["I"].values()]))
    print(f"WROTE {RESULTS} + {FRONTIER} | S* (max over t,fam) = {s_star:+.4f} nats/row")


# ───────────────────── post-retest amendments (declared, not silent) ─────────────────────
# The doc-273 hostile retest (wf_0da3093e-b87) found: (1) the day_aggs warehouse is missing
# 54 sessions 2026-01-02..2026-03-20, so the corpus gate's lag(close) admitted 389 stale-
# prev-close zombies on 2026-03-23 (92% of the as-registered S*) — the CLEAN universe
# filter below keeps only ticker-days whose prev close is <= 4 calendar days old;
# (2) the registered null packed all rows (validity following the donor) while the real
# statistic conditions on own-row entry validity, deflating the null tail; and the per-rep
# per-t permutations were independent while real labels are cross-t coupled (corr ~0.5).
# run_null_clean fixes both: ONE shared within-day ticker permutation per rep applied
# across all 13 t, estimation on own-valid AND donor-valid rows only.

def _clean_mask(df: pd.DataFrame) -> pd.Series:
    """True for spine rows whose (ticker, session_date) has a fresh (<=4 cal day) prev close."""
    import duckdb
    lag = duckdb.connect().execute("""
      WITH b AS (SELECT ticker, ts_et::DATE d,
                   lag(ts_et::DATE) OVER (PARTITION BY ticker ORDER BY ts_et) pd
                 FROM read_parquet('data/polygon_warehouse/day_aggs/**/*.parquet'))
      SELECT ticker, strftime(d, '%Y-%m-%d') session_date,
             date_diff('day', pd, d) lag_days FROM b WHERE pd IS NOT NULL""").df()
    fresh = {(r.ticker, r.session_date) for r in lag[lag.lag_days <= 4].itertuples()}
    return pd.Series([(t, d) in fresh for t, d in zip(df.ticker, df.session_date)],
                     index=df.index)


def _load_spine_clean():
    df = _load_spine()
    m = _clean_mask(df)
    dropped = df[~m]
    print(f"CLEAN filter: dropped {int((~m).sum())} rows "
          f"({dropped.groupby('session_date').ticker.nunique().to_dict() if (~m).sum() else {}})")
    return df[m].reset_index(drop=True)


def run_null_clean(B):
    """Referee-corrected null on the clean universe: one shared within-day ticker
    permutation per rep (cross-t coupling preserved), estimation on own-valid AND
    donor-valid rows (mirrors the real conditioning)."""
    from joblib import Parallel, delayed
    df = _load_spine_clean()
    packs = {}
    for lbl in GRID_LBL:
        s, X, y, fold = _per_t(df, lbl)             # valid-only, as the real statistic
        all_t = df[df.t_lbl == lbl]
        ymap = {}                                    # day -> ticker -> gross_ret (NaN ok)
        for day, g in all_t.groupby("session_date"):
            ymap[day] = dict(zip(g.ticker, g.gross_ret))
        packs[lbl] = (X, s.gross_ret.to_numpy(float), s.session_date.to_numpy(),
                      s.ticker.to_numpy(), fold, ymap)
    days_all = sorted(df.session_date.unique())
    tickers_by_day = {d: sorted(df[df.session_date == d].ticker.unique()) for d in days_all}

    def one_rep(seed):
        rng = np.random.default_rng(seed)
        fams = _families()
        perm = {}                                    # ONE shared mapping per day
        for d in days_all:
            tk = tickers_by_day[d]
            perm[d] = dict(zip(tk, [tk[i] for i in rng.permutation(len(tk))]))
        per_t = {}
        for lbl, (X, gross, day, tick, fold, ymap) in packs.items():
            yd = np.array([ymap[d].get(perm[d].get(t), np.nan)
                           for d, t in zip(day, tick)], dtype=float)
            keep = ~np.isnan(yd)
            yb = (yd[keep] > THETA).astype(int)
            if keep.sum() < 200 or yb.sum() in (0, len(yb)):
                per_t[lbl] = np.nan
                continue
            dll, _ = _dll_curve(X[keep], yb, None, fold[keep], fams)
            per_t[lbl] = float(np.nanmax(list(dll.values())))
        return per_t

    print(f"CLEAN mirrored null: B={B} (shared within-day ticker perm, own+donor-valid)")
    reps = Parallel(n_jobs=10, verbose=5)(delayed(one_rep)(2000 + b) for b in range(B))
    nd = pd.DataFrame(reps)
    nd["S_star"] = nd.max(axis=1)
    nd.to_parquet(NULLF.replace(".parquet", "_clean.parquet"), index=False)
    print(f"WROTE clean null | S* p95 = {nd.S_star.quantile(0.95):+.4f} "
          f"p99 = {nd.S_star.quantile(0.99):+.4f} max = {nd.S_star.max():+.4f}")


def run_real_clean():
    """Real curves on the clean universe (artifacts suffixed _clean)."""
    global SPINE, RESULTS, FRONTIER
    df = _load_spine_clean()
    df.to_parquet(SPINE.replace(".parquet", "_clean.parquet"), index=False)
    old_spine, old_res, old_fr = SPINE, RESULTS, FRONTIER
    SPINE = SPINE.replace(".parquet", "_clean.parquet")
    RESULTS = RESULTS.replace(".json", "_clean.json")
    FRONTIER = FRONTIER.replace(".parquet", "_clean.parquet")
    try:
        run_real()
    finally:
        SPINE, RESULTS, FRONTIER = old_spine, old_res, old_fr


# ───────────────────────────── permutation null ─────────────────────────────

def _one_null_rep(seed, packs):
    rng = np.random.default_rng(seed)
    fams = _families()
    per_t = {}
    for lbl, (Xall, gross, day_codes, fold) in packs.items():
        g = gross.copy()
        for dc in np.unique(day_codes):
            m = np.where(day_codes == dc)[0]
            g[m] = g[rng.permutation(m)]
        ok = ~np.isnan(g)
        X, y, fd = Xall[ok], (g[ok] > THETA).astype(int), fold[ok]
        if len(y) < 200 or y.sum() in (0, len(y)):
            per_t[lbl] = np.nan
            continue
        dll, _ = _dll_curve(X, y, None, fd, fams)
        per_t[lbl] = float(np.nanmax(list(dll.values())))
    return per_t


def run_null(B):
    from joblib import Parallel, delayed
    df = _load_spine()
    packs = {}
    for lbl in GRID_LBL:
        s = df[df.t_lbl == lbl].reset_index(drop=True)   # ALL rows; validity follows the label donor
        X = s[FEATURES].to_numpy(float)
        gross = s.gross_ret.to_numpy(float)
        day_codes = pd.factorize(s.day)[0]
        days = sorted(s.day.unique())
        fmap = _fold_of_day(days)
        fold = s.day.map(fmap).to_numpy()
        # feature-validity: rows whose own tape produced features (entry validity travels with label)
        feat_ok = ~np.isnan(X).all(axis=1)
        packs[lbl] = (X[feat_ok], gross[feat_ok], day_codes[feat_ok], fold[feat_ok])
    print(f"permutation null: B={B} reps x {len(GRID_LBL)} t x 2 fams (within-day shuffle)")
    reps = Parallel(n_jobs=10, verbose=5)(delayed(_one_null_rep)(1000 + b, packs) for b in range(B))
    nd = pd.DataFrame(reps)
    nd["S_star"] = nd.max(axis=1)
    nd.to_parquet(NULLF, index=False)
    print(f"WROTE {NULLF} | null S* p95 = {nd.S_star.quantile(0.95):+.4f} "
          f"p99 = {nd.S_star.quantile(0.99):+.4f}")


# ───────────────────────────── planted-signal power ─────────────────────────────

def run_plant():
    df = _load_spine()
    fams = _families()
    rows = []
    plant_sec = {m * 60 for m in PLANT_T}        # PLANT_T is ET minutes; grid is seconds
    for sec_t, lbl in zip(GRID_SEC, GRID_LBL):
        if sec_t not in plant_sec:
            continue
        s, X, y0, fold = _per_t(df, lbl)
        drv = s["ofi_last10"].fillna(s.groupby("day")["ofi_last10"].transform("median")).fillna(0.0)
        hi = (drv.groupby(s.day).rank(pct=True) >= 0.5).to_numpy().astype(int)
        oracle_dll, _ = _dll_curve(hi.reshape(-1, 1).astype(float), y0, None, fold,
                                   {"logit": fams["logit"]})
        for strength in PLANT_S:
            for rep in range(PLANT_REPS):
                rng = np.random.default_rng(40_000 + int(strength * 1000) + rep)
                y = y0.copy()
                flip = rng.random(len(y)) < strength
                y[flip] = hi[flip]
                if y.sum() in (0, len(y)):
                    continue
                dll, _ = _dll_curve(X, y, None, fold, fams)
                # oracle on the planted labels: detector that knows the driver
                odll, _ = _dll_curve(hi.reshape(-1, 1).astype(float), y, None, fold,
                                     {"logit": fams["logit"]})
                rows.append(dict(t_lbl=lbl, s=strength, rep=rep,
                                 recovered=float(np.nanmax(list(dll.values()))),
                                 oracle=odll["logit"]))
                print(f"  plant {lbl} s={strength} rep={rep}: recovered "
                      f"{rows[-1]['recovered']:+.4f} oracle {rows[-1]['oracle']:+.4f}")
    pd.DataFrame(rows).to_parquet(PLANTF, index=False)
    print(f"WROTE {PLANTF}")


# ───────────────────────────── verdict ─────────────────────────────

def verdict():
    res = json.load(open(RESULTS))
    nd = pd.read_parquet(NULLF)
    pl = pd.read_parquet(PLANTF)
    s_real = {lbl: float(np.nanmax(list(v.values()))) for lbl, v in res["I"].items()}
    s_star = max(s_real.values())
    p = (1 + (nd.S_star >= s_star).sum()) / (len(nd) + 1)
    thr95 = float(nd.S_star.quantile(0.95))
    sig_t = [lbl for lbl, v in s_real.items() if v >= thr95]
    print(f"S* = {s_star:+.4f} | max-(t,fam) permutation p = {p:.3f} (B={len(nd)}) | "
          f"maxT 95% threshold = {thr95:+.4f}")
    print(f"per-t exceedances of the maxT threshold: {sig_t or 'NONE'}")
    # power: MDE per planted t = smallest s with median recovered >= thr95
    mde = {}
    for lbl, g in pl.groupby("t_lbl"):
        got = sorted(set(g.s))
        rec = {s_: g[g.s == s_].recovered.median() for s_ in got}
        found = [s_ for s_ in got if rec[s_] >= thr95]
        mde[lbl] = min(found) if found else float("nan")
        print(f"  MDE @{lbl}: {mde[lbl] if found else '>0.20 (UNPOWERED)'} "
              f"(median recovery by strength: "
              + ", ".join(f"s={s_}:{rec[s_]:+.4f}" for s_ in got) + ")")
    unpowered = all(np.isnan(v) for v in mde.values())
    if unpowered:
        v = "V-UNPOWERED — plants not recovered even at s=0.20; no conclusion (frontier failure, reported)."
    elif p >= 0.05:
        v = "V-NULL — no detectable tape information anywhere on the grid (qualified by the MDE above)."
    else:
        race = all(not (res["A"][lbl]["lo"] > 0) for lbl in sig_t)
        v = ("V-RACE — information exists but A(t) cannot pay at every significant t "
             "(it arrives after the move is spent)." if race else
             "V-WINDOW — located hypothesis at " + ",".join(
                 lbl for lbl in sig_t if res["A"][lbl]["lo"] > 0)
             + " — NOT an edge claim; doc-234 hostile gauntlet required.")
    print("\nVERDICT:", v)
    return v


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--null", type=int, default=0)
    ap.add_argument("--plant", action="store_true")
    ap.add_argument("--verdict", action="store_true")
    ap.add_argument("--run-clean", action="store_true")
    ap.add_argument("--null-clean", type=int, default=0)
    a = ap.parse_args()
    if a.build:
        build()
    if a.run:
        run_real()
    if a.null:
        run_null(a.null)
    if a.plant:
        run_plant()
    if a.verdict:
        verdict()
    if a.run_clean:
        run_real_clean()
    if a.null_clean:
        run_null_clean(a.null_clean)
