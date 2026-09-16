"""doc 274 — THE MUTATION GAUNTLET (pre-registered research, DORMANT-C, read-only vs production).

273 measured what the tape can know; 274 measures what the engine can certify.
Mutation testing transplanted to the quant falsification stack: inject mechanism-typed
pathologies of graded severity into the EXACT doc-273 engine running on the certified-null
CLEAN spine; instrument every defense layer; measure which layer catches which class at
what severity. The injector is validated against the REAL 2026-03-23 contamination specimen
(ground truth from the doc-273 kill) and against a zero-knob identity check (mutated worker
with all knobs at zero must reproduce the original spine bit-for-bit on a sample).

DUAL-BRANCH HARM TAXONOMY:
  (i) false-positive generators (P1 membership, P2 feature look-ahead, P3 label-window
      leakage, P4 survivorship) — harm = a CONVINCING DISCOVERY; "detection" = a defense
      flags the positive as suspect (D1 firing IS the harm, not the detection);
  (ii) false-null / power killers (P1-dilution, P5 drift) — harm = an unearned clean null;
      detection = the plant layer (D6) reports degraded power.

DEFENSES (frozen thresholds in DEFENSE_THRESHOLDS):
  D1 maxT permutation verdict (cell-level mirrored null, B=60)   [records harm]
  D2 membership audit: gate recomputation + prev-close freshness vs raw day_aggs
  D3 leave-one-day-out concentration: max single-day share of the positive dll mass
  D4 truncation audit: recompute feature sample from tape hard-truncated at t
  D5 split-half stability: pooled-significant but both halves negative = the 273 signature
  D6 MDE plants: s=0.20 recovery at 10:30 (3 reps) vs cell null
  D7 effect-vs-MDE plausibility: S* >> plant-oracle ceiling
  D8 economics coherence: uncond net mean + base rate vs clean-reference bands

Engine for cells: doc-273 registered pipeline on the 5-point grid {9:40,10:30,12:00,14:00,15:00},
B=60 fully mirrored null (own+donor validity, ONE shared within-day permutation across t),
logit+GBM, theta=1%. Base data: data/research/doc273_spine_clean.parquet (membership-audited).

Usage:
  python scripts/mutation_gauntlet_doc274.py --identity     # zero-knob injector fidelity check
  python scripts/mutation_gauntlet_doc274.py --cell NAME    # run one named cell
  python scripts/mutation_gauntlet_doc274.py --all          # run the full registered grid
  python scripts/mutation_gauntlet_doc274.py --matrix       # assemble the capability matrix
"""
from __future__ import annotations
import argparse, importlib.util, json, os, sys
import numpy as np
import pandas as pd

_spec = importlib.util.spec_from_file_location(
    "kf", os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowability_frontier_doc273.py"))
kf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kf)

CLEAN_SPINE = "data/research/doc273_spine_clean.parquet"
CONTAM_SPINE = "data/research/doc273_spine.parquet"          # holds the real 2026-03-23 specimen
OUTDIR = "data/research/doc274_cells"
GRID5 = ["9:40", "10:30", "12:00", "14:00", "15:00"]
GRID5_SEC = {lbl: sec for sec, lbl in zip(kf.GRID_SEC, kf.GRID_LBL) if lbl in GRID5}
B_CELL = 60
N_JOBS = 8     # capped: the live bot must never starve

DEFENSE_THRESHOLDS = dict(
    D1_p=0.05,                 # cell maxT p < .05 -> "discovery" (harm for FP classes)
    D3_lodo_share=0.50,        # max single-day share of positive dll mass
    D4_mismatch=0,             # ANY feature mismatch vs truncated-tape recomputation
    D6_plant_s=0.20,           # plant strength whose recovery is required
    D7_ceiling=0.040,          # S* above ~2x the measured plant-oracle ceiling (~0.02) flags
    D8_uncond_band=0.010,      # |uncond net mean - clean ref| > 1.0pt flags
    D8_base_band=0.05,         # |base rate - clean ref| > 5pts flags
)


# ─────────────────────────── engine on a cell ───────────────────────────

def _grid_df(spine: pd.DataFrame) -> pd.DataFrame:
    spine = spine[spine.t_lbl.isin(GRID5)].reset_index(drop=True)
    if "day" not in spine.columns:
        spine = spine.assign(day=spine.session_date)
    return spine


def engine_run(spine: pd.DataFrame) -> dict:
    """273 registered statistic on the 5-pt grid: per-t dll both families + gbm OOF."""
    fams = kf._families()
    out = {"I": {}, "oof": {}, "valid_n": {}, "base": {}, "uncond": {}}
    for lbl in GRID5:
        s, X, y, fold = kf._per_t(spine, lbl)
        dll, gbm_oof = kf._dll_curve(X, y, None, fold, fams)
        out["I"][lbl] = dll
        out["oof"][lbl] = (s, gbm_oof)
        out["valid_n"][lbl] = int(len(s))
        out["base"][lbl] = float(y.mean()) if len(y) else float("nan")
        net = s.gross_ret.to_numpy(float) - kf.THETA
        out["uncond"][lbl] = float(kf._wins(net).mean()) if len(net) else float("nan")
    out["S_star"] = float(np.nanmax([max(v.values()) for v in out["I"].values()]))
    out["S_star_at"] = max(out["I"], key=lambda l: max(out["I"][l].values()))
    return out


def mirrored_null(spine: pd.DataFrame, B: int = B_CELL, seed0: int = 7000) -> pd.DataFrame:
    """273's corrected null (own+donor validity, ONE shared within-day perm across t), 5-pt grid."""
    from joblib import Parallel, delayed
    packs = {}
    for lbl in GRID5:
        s, X, y, fold = kf._per_t(spine, lbl)
        all_t = spine[spine.t_lbl == lbl]
        ymap = {d: dict(zip(g.ticker, g.gross_ret)) for d, g in all_t.groupby("session_date")}
        packs[lbl] = (X, s.session_date.to_numpy(), s.ticker.to_numpy(), fold, ymap)
    days_all = sorted(spine.session_date.unique())
    tickers_by_day = {d: sorted(spine[spine.session_date == d].ticker.unique()) for d in days_all}

    def one_rep(seed):
        rng = np.random.default_rng(seed)
        fams = kf._families()
        perm = {}
        for d in days_all:
            tk = tickers_by_day[d]
            perm[d] = dict(zip(tk, [tk[i] for i in rng.permutation(len(tk))]))
        per_t = {}
        for lbl, (X, day, tick, fold, ymap) in packs.items():
            yd = np.array([ymap[d].get(perm[d].get(t), np.nan) for d, t in zip(day, tick)], float)
            keep = ~np.isnan(yd)
            yb = (yd[keep] > kf.THETA).astype(int)
            if keep.sum() < 200 or yb.sum() in (0, len(yb)):
                per_t[lbl] = np.nan
                continue
            dll, _ = kf._dll_curve(X[keep], yb, None, fold[keep], fams)
            per_t[lbl] = float(np.nanmax(list(dll.values())))
        return per_t

    reps = Parallel(n_jobs=N_JOBS, verbose=0)(delayed(one_rep)(seed0 + b) for b in range(B))
    nd = pd.DataFrame(reps)
    nd["S_star"] = nd.max(axis=1)
    return nd


# ─────────────────────────── defense battery ───────────────────────────

def d2_membership(spine: pd.DataFrame) -> dict:
    """Gate recomputation + freshness vs raw day_aggs (the doc-273 standing rule, mechanized)."""
    import duckdb
    rows = spine[["ticker", "session_date"]].drop_duplicates()
    con = duckdb.connect()
    da = con.execute("""
      WITH b AS (SELECT ticker, ts_et::DATE d, open, close, volume,
                   lag(close) OVER (PARTITION BY ticker ORDER BY ts_et) pc,
                   lag(ts_et::DATE) OVER (PARTITION BY ticker ORDER BY ts_et) pd,
                   avg(volume*close) OVER (PARTITION BY ticker ORDER BY ts_et
                       ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) adv20
                 FROM read_parquet('data/polygon_warehouse/day_aggs/**/*.parquet'))
      SELECT ticker, strftime(d, '%Y-%m-%d') session_date, open, pc, adv20,
             date_diff('day', pd, d) lag_days FROM b""").df()
    m = rows.merge(da, on=["ticker", "session_date"], how="left")
    ok_gate = ((m.pc > 0) & (m.open / m.pc - 1 >= 0.08)
               & (m.open >= 0.50) & (m.open <= 20.0) & (m.adv20 >= 1e6))
    ok_fresh = m.lag_days <= 4
    missing = m.pc.isna() | m.lag_days.isna()
    bad = (~ok_gate.fillna(False)) | (~ok_fresh.fillna(False)) | missing
    return dict(n=int(len(m)), n_bad=int(bad.sum()), frac_bad=float(bad.mean()),
                fired=bool(bad.any()),
                bad_days=m[bad].session_date.value_counts().head(5).to_dict())


def d3_lodo(eng: dict) -> dict:
    """Max single-day share of the positive per-row dll mass at the S*-carrying (t, family)."""
    lbl = eng["S_star_at"]
    s, gbm_oof = eng["oof"][lbl]
    fam = max(eng["I"][lbl], key=eng["I"][lbl].get)
    if fam != "gbm":     # recompute logit OOF for attribution
        fams = kf._families()
        Xl = s[kf.FEATURES].to_numpy(float)
        yl = (s.gross_ret.to_numpy(float) > kf.THETA).astype(int)
        days = sorted(s.session_date.unique())
        fold = s.session_date.map(kf._fold_of_day(days)).to_numpy()
        oof = np.full(len(yl), np.nan)
        for f in range(kf.NFOLD):
            tr, te = fold != f, fold == f
            if te.sum() and len(np.unique(yl[tr])) == 2:
                mdl = fams["logit"](int(tr.sum())); mdl.fit(Xl[tr], yl[tr])
                oof[te] = mdl.predict_proba(Xl[te])[:, 1]
        gbm_oof = oof
        y = yl
    else:
        y = (s.gross_ret.to_numpy(float) > kf.THETA).astype(int)
    ok = ~np.isnan(gbm_oof)
    eps = 1e-4
    p = np.clip(gbm_oof[ok], eps, 1 - eps)
    days = sorted(s.session_date[ok].unique())
    fold = s.session_date[ok].map(kf._fold_of_day(sorted(s.session_date.unique()))).to_numpy()
    base = np.empty(ok.sum())
    yv = y[ok]
    foldv = s.session_date[ok].map(kf._fold_of_day(sorted(s.session_date.unique()))).to_numpy()
    for f in range(kf.NFOLD):
        tr = foldv != f
        base[foldv == f] = yv[tr].mean() if tr.sum() else yv.mean()
    b = np.clip(base, eps, 1 - eps)
    row_red = (-(yv * np.log(b) + (1 - yv) * np.log(1 - b))
               + (yv * np.log(p) + (1 - yv) * np.log(1 - p)))
    df = pd.DataFrame({"day": s.session_date[ok].to_numpy(), "red": row_red})
    tot = df.red.sum()
    if tot <= 0:
        return dict(share=0.0, fired=False, day=None, total=float(tot))
    by_day = df.groupby("day").red.sum()
    share = float(by_day.max() / tot)
    return dict(share=share, fired=bool(share > DEFENSE_THRESHOLDS["D3_lodo_share"]),
                day=str(by_day.idxmax()), total=float(tot))


def d4_truncation(spine: pd.DataFrame, n_sample: int = 40, seed: int = 11) -> dict:
    """Recompute a feature sample from the RAW tape hard-truncated strictly at t; compare."""
    rng = np.random.default_rng(seed)
    g = spine[spine.t_lbl.isin(GRID5) & spine.ret_open_t.notna()]
    pick = g.sample(min(n_sample, len(g)), random_state=int(rng.integers(1e6)))
    mism = 0; checked = 0
    for r in pick.itertuples():
        got = kf._read_day(r.ticker, r.session_date)
        if got is None:
            continue
        sec, px, sz = got
        rth = sec >= kf.RTH0
        rsec, rpx, rsz = sec[rth], px[rth], sz[rth]
        if len(rpx) < 5:
            continue
        sec_t = GRID5_SEC[r.t_lbl]
        i = int(np.searchsorted(rsec, sec_t))
        if i < 2:
            continue
        checked += 1
        open_px = rpx[0]
        truth = dict(
            ret_open_t=rpx[i - 1] / open_px - 1.0,
            log_cumvol=np.log1p(float(rsz[:i].sum())),
            intensity=i / (max(sec_t - kf.RTH0, 1.0) / 60.0),
        )
        for k, v in truth.items():
            if not np.isclose(getattr(r, k), v, rtol=1e-6, atol=1e-9):
                mism += 1
                break
    return dict(checked=int(checked), mismatches=int(mism),
                fired=bool(mism > DEFENSE_THRESHOLDS["D4_mismatch"]))


def d5_split_half(spine: pd.DataFrame, eng: dict) -> dict:
    lbl = eng["S_star_at"]
    fam = max(eng["I"][lbl], key=eng["I"][lbl].get)
    fams = {fam: kf._families()[fam]}
    halves = {}
    for h, (a, b) in {"H1": ("2025-08-01", "2025-12-31"), "H2": ("2026-01-01", "2026-04-30")}.items():
        sub = spine[(spine.session_date >= a) & (spine.session_date <= b)]
        s, X, y, fold = kf._per_t(sub, lbl)
        if len(s) < 200:
            halves[h] = np.nan
            continue
        dll, _ = kf._dll_curve(X, y, None, fold, fams)
        halves[h] = dll[fam]
    both_neg = all(np.isfinite(v) and v < 0 for v in halves.values())
    return dict(H1=halves.get("H1"), H2=halves.get("H2"),
                fired=bool(both_neg), note="fired = both halves negative (273 signature; "
                "meaningful only when D1 also fired)")


def d6_plants(spine: pd.DataFrame, null_thr: float, reps: int = 3) -> dict:
    """s=0.20 plant recovery at 10:30 vs the cell's null threshold; power-degradation flag."""
    lbl = "10:30"
    s, X, y0, fold = kf._per_t(spine, lbl)
    drv = s["ofi_last10"].fillna(s.groupby("session_date")["ofi_last10"].transform("median")).fillna(0.0)
    hi = (drv.groupby(s.session_date).rank(pct=True) >= 0.5).to_numpy().astype(int)
    fams = kf._families()
    recs = []
    for rep in range(reps):
        rng = np.random.default_rng(74_000 + rep)
        y = y0.copy()
        flip = rng.random(len(y)) < DEFENSE_THRESHOLDS["D6_plant_s"]
        y[flip] = hi[flip]
        if y.sum() in (0, len(y)):
            continue
        dll, _ = kf._dll_curve(X, y, None, fold, fams)
        recs.append(float(np.nanmax(list(dll.values()))))
    med = float(np.median(recs)) if recs else float("nan")
    return dict(recovery_median=med, null_thr=float(null_thr),
                fired=bool(np.isfinite(med) and med < null_thr),
                note="fired = power BROKEN (s=0.20 plant no longer recovered)")


def d7_plausibility(eng: dict) -> dict:
    return dict(S_star=eng["S_star"],
                fired=bool(eng["S_star"] > DEFENSE_THRESHOLDS["D7_ceiling"]),
                note="fired = effect implausibly large vs measured plant-oracle ceiling")


def d8_econ(eng: dict, ref: dict) -> dict:
    flags = {}
    for lbl in GRID5:
        du = abs(eng["uncond"][lbl] - ref["uncond"][lbl])
        db = abs(eng["base"][lbl] - ref["base"][lbl])
        flags[lbl] = bool(du > DEFENSE_THRESHOLDS["D8_uncond_band"]
                          or db > DEFENSE_THRESHOLDS["D8_base_band"])
    return dict(fired=bool(any(flags.values())), per_t=flags)


# ─────────────────────────── mutated spine builder ───────────────────────────

def _day_rows_mut(args, feat_shift_s: float = 0.0, exit0: int = kf.EXIT0, exit1: int = kf.EXIT1):
    """273's _day_rows with two pathology knobs. ZERO-KNOB OUTPUT MUST EQUAL THE ORIGINAL
    (asserted by --identity). feat_shift_s>0: features illegally see that many seconds past
    t (P2). exit0/exit1: label exit window (P3 moves it early). Entries stay at t."""
    ticker, d, open_ref, adv20 = args
    got = kf._read_day(ticker, d)
    if got is None:
        return []
    sec, px, sz = got
    pm = sec < kf.RTH0
    psec, ppx, psz = sec[pm], px[pm], sz[pm]
    rsec, rpx, rsz = sec[~pm], px[~pm], sz[~pm]
    if len(rpx) < 5:
        return []
    open_px = rpx[0]
    sign = kf._tick_sign(rpx)
    n = len(rpx)
    CV = np.concatenate([[0.0], np.cumsum(rsz)])
    CD = np.concatenate([[0.0], np.cumsum(rpx * rsz)])
    CS = np.concatenate([[0.0], np.cumsum(sign * rsz)])
    run_hi = np.maximum.accumulate(rpx)
    run_lo = np.minimum.accumulate(rpx)
    if len(ppx) >= 2:
        pmsign = kf._tick_sign(ppx)
        pm_feats = dict(pm_logvol=np.log1p(psz.sum()), pm_logdollar=np.log1p((ppx * psz).sum()),
                        pm_ret=ppx[-1] / ppx[0] - 1.0,
                        pm_ofi=float((pmsign * psz).sum() / max(psz.sum(), 1e-9)),
                        pm_logtrades=np.log1p(len(ppx)), pm_to_open=open_px / ppx[-1] - 1.0)
    else:
        pm_feats = dict(pm_logvol=0.0, pm_logdollar=0.0, pm_ret=np.nan,
                        pm_ofi=np.nan, pm_logtrades=0.0, pm_to_open=np.nan)
    ea, eb = np.searchsorted(rsec, exit0), np.searchsorted(rsec, exit1)
    if eb > ea:
        exit_px = float((rpx[ea:eb] * rsz[ea:eb]).sum() / rsz[ea:eb].sum())
    else:
        late = np.searchsorted(rsec, kf.EXIT_FB)
        exit_px = float(rpx[-1]) if len(rpx) > late else np.nan
    out = []
    for lbl in GRID5:
        sec_t = GRID5_SEC[lbl]
        fsec = sec_t + feat_shift_s                       # the P2 knob
        i = int(np.searchsorted(rsec, fsec))
        row = dict(ticker=ticker, session_date=d, t_sec=sec_t, t_lbl=lbl,
                   log_open_px=np.log(max(open_px, 1e-9)), log_adv20=np.log(max(adv20, 1.0)))
        row.update(pm_feats)
        if i >= 2:
            last = rpx[i - 1]
            w5 = int(np.searchsorted(rsec, fsec - 300))
            w10 = int(np.searchsorted(rsec, fsec - 600))
            w15 = int(np.searchsorted(rsec, fsec - 900))
            vol = CV[i]; dol = CD[i]
            vwap = dol / max(vol, 1e-9)
            vwap10 = ((CD[i] - CD[w10]) / max(CV[i] - CV[w10], 1e-9)) if i > w10 else vwap
            dt_gaps = np.diff(rsec[:i])
            if len(dt_gaps):
                gmax = int(np.argmax(dt_gaps))
                max_gap, ret_gap = float(dt_gaps[gmax]), float(rpx[gmax + 1] / rpx[gmax] - 1.0)
            else:
                max_gap, ret_gap = 0.0, 0.0
            elapsed = max(fsec - kf.RTH0, 1.0)
            ihi, ilo = int(np.argmax(rpx[:i])), int(np.argmin(rpx[:i]))
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
                t_of_high_frac=(rsec[ihi] - kf.RTH0) / elapsed,
                t_of_low_frac=(rsec[ilo] - kf.RTH0) / elapsed,
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
                t_since_print_log=np.log1p(fsec - rsec[i - 1]),
                ret_across_max_gap=ret_gap,
                stale_at_t=float(fsec - rsec[i - 1] >= 300.0),
            )
        ia, ib = int(np.searchsorted(rsec, sec_t + 5)), int(np.searchsorted(rsec, sec_t + 300))
        if ib - ia >= kf.ENTRY_MIN_PRINTS and rsz[ia:ib].sum() >= kf.ENTRY_MIN_SH and np.isfinite(exit_px):
            entry = float((rpx[ia:ib] * rsz[ia:ib]).sum() / rsz[ia:ib].sum())
            row["entry_px"], row["gross_ret"] = entry, exit_px / entry - 1.0
        else:
            row["entry_px"], row["gross_ret"] = np.nan, np.nan
        out.append(row)
    return out


def rebuild_spine(corpus_rows, feat_shift_s=0.0, exit0=kf.EXIT0, exit1=kf.EXIT1) -> pd.DataFrame:
    from multiprocessing import Pool
    from functools import partial
    jobs = [(r.ticker, r.session_date, float(r.base_close), float(r.adv20)) for r in corpus_rows.itertuples()]
    fn = partial(_day_rows_mut, feat_shift_s=feat_shift_s, exit0=exit0, exit1=exit1)
    rows = []
    with Pool(N_JOBS) as pool:
        for rr in pool.imap_unordered(fn, jobs, chunksize=16):
            rows.extend(rr)
    df = pd.DataFrame(rows)
    df["day"] = df.session_date
    return df


# ─────────────────────────── cell runner ───────────────────────────

def run_cell(name: str, spine: pd.DataFrame, ref: dict | None = None,
             with_plants: bool = True, provenance: dict | None = None) -> dict:
    """One gauntlet cell: engine + mirrored null + full defense battery -> record."""
    spine = _grid_df(spine)
    eng = engine_run(spine)
    nd = mirrored_null(spine, B=B_CELL)
    p = float((1 + (nd.S_star >= eng["S_star"]).sum()) / (len(nd) + 1))
    thr95 = float(nd.S_star.quantile(0.95))
    rec = dict(cell=name, provenance=provenance or {},
               S_star=eng["S_star"], S_star_at=eng["S_star_at"],
               I=eng["I"], base=eng["base"], uncond=eng["uncond"], valid_n=eng["valid_n"],
               null_p=p, null_thr95=thr95,
               D1=dict(p=p, fired=bool(p < DEFENSE_THRESHOLDS["D1_p"])),
               D2=d2_membership(spine),
               D3=d3_lodo(eng),
               D4=d4_truncation(spine),
               D5=d5_split_half(spine, eng),
               D7=d7_plausibility(eng))
    if with_plants:
        rec["D6"] = d6_plants(spine, thr95)
    if ref is not None:
        rec["D8"] = d8_econ(eng, ref)
    os.makedirs(OUTDIR, exist_ok=True)
    json.dump(rec, open(os.path.join(OUTDIR, f"cell_{name}.json"), "w"), indent=1, default=float)
    fired = [d for d in ("D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8")
             if d in rec and rec[d].get("fired")]
    print(f"CELL {name}: S*={eng['S_star']:+.4f}@{eng['S_star_at']} p={p:.3f} "
          f"| fired: {fired or 'NONE'}")
    return rec


def identity_check(n=25):
    """Zero-knob _day_rows_mut must reproduce the clean spine on a sample (injector fidelity)."""
    clean = pd.read_parquet(CLEAN_SPINE)
    clean = clean[clean.t_lbl.isin(GRID5)]
    corpus = kf._corpus()
    keys = set(zip(clean.ticker, clean.session_date))
    corpus = corpus[[(t, d) in keys for t, d in zip(corpus.ticker, corpus.session_date)]]
    samp = corpus.sample(n, random_state=3)
    rebuilt = rebuild_spine(samp)
    bad = 0; checked = 0
    idx = clean.set_index(["ticker", "session_date", "t_lbl"])
    for r in rebuilt.itertuples():
        try:
            orig = idx.loc[(r.ticker, r.session_date, r.t_lbl)]
        except KeyError:
            continue
        checked += 1
        for c in kf.FEATURES + ["gross_ret"]:
            a, b = getattr(r, c), orig[c]
            if (pd.isna(a)) != (pd.isna(b)) or (pd.notna(a) and not np.isclose(a, b, rtol=1e-9, atol=1e-12, equal_nan=True)):
                bad += 1
                print(f"  MISMATCH {r.ticker} {r.session_date} {r.t_lbl} {c}: {a} vs {b}")
                break
    print(f"identity check: {checked} rows compared, {bad} mismatches")
    return bad == 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--identity", action="store_true")
    ap.add_argument("--cell", type=str, default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--matrix", action="store_true")
    a = ap.parse_args()
    if a.identity:
        ok = identity_check(25)
        print("IDENTITY:", "PASS" if ok else "FAIL")
        sys.exit(0 if ok else 1)
    # cell registry is appended post-prereg (Phase 3); see CELLS below
    if a.cell or a.all or a.matrix:
        from doc274_cells_registry import main as cells_main   # appended at Phase 3
        cells_main(a)
