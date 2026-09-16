"""DOC 291 STAGE 2 — TRANSFER FEASIBILITY: does our feature vocabulary forecast realized volatility on
LIQUID names better than HAR-RV? Gates frozen in scripts/_doc291_PREREG.md (sha256 5155f3df..., 7cc09fe):
challenger must beat BOTH HAR-OLS and GBM[HAR-only] on pooled QLIKE (date-blocked bootstrap CI95 excl. 0,
both halves) AND clear the frozen minimum meaningful improvement (>=2.0% pooled, >=1.0% each half).
Free local data only (151 liquid names, warehouse minute bars, 2024-01..2026-07). Losing to HAR kills the pivot.
"""
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
OUT = _ROOT / "data" / "research" / "doc291"
OUT.mkdir(parents=True, exist_ok=True)
SEED = 291
MIN_TRAIN_SESSIONS = 120
REFIT_EVERY = 21
BLOCK = 10
B_BOOT = 5000
MMI_POOLED = 0.02   # frozen: >=2.0% pooled QLIKE reduction vs the better baseline
MMI_HALF = 0.01

LIQUID = ['AAPL','MSFT','NVDA','AMZN','GOOGL','META','TSLA','AVGO','JPM','V','UNH','XOM','LLY','MA','HD','COST','PG','JNJ','ABBV','WMT','NFLX','CRM','BAC','ORCL','AMD','KO','PEP','TMO','CSCO','ACN','ADBE','MCD','ABT','WFC','INTU','QCOM','IBM','GE','CAT','DHR','AXP','TXN','AMGN','VZ','PFE','MS','GS','NOW','UBER','NEE','RTX','LOW','SPGI','T','HON','UNP','BLK','INTC','BA','SBUX','ELV','PLD','DE','LMT','MDT','GILD','TJX','BKNG','ADI','MMC','VRTX','SYK','ADP','C','CB','AMT','SCHW','MO','SO','ZTS','CI','FI','DUK','BSX','EQIX','CME','ITW','SHW','MU','PANW','SNPS','CDNS','KLAC','CRWD','ABNB','MAR','ORLY','CTAS','MRK','DIS','PM','PYPL','COIN','PLTR','SMCI','SHOP','SQ','ROKU','DKNG','MARA','RIOT','SOFI','HOOD','SNAP','F','GM','AAL','CCL','PLUG','NIO','RIVN','LCID','AFRM','UPST','CVNA','ETSY','W','CHWY','DDOG','NET','SNOW','ZS','MDB','TEAM','OKTA','TWLO','DOCU','ZM','PTON','SPY','QQQ','IWM','DIA','XLF','XLE','SMH','ARKK','TQQQ','SQQQ','SOXL','SOXS']

HAR_F = ["log_rv_d", "log_rv_w", "log_rv_m"]
OUR_F = ["abs_overnight_gap", "range_pct", "first15_ret", "first15_range", "last_hour_rv_share",
         "vwap_dev_close", "higher_low_frac", "vol_slope", "log_dvol", "rv_ratio_dw"]


def build_panel():
    import duckdb
    con = duckdb.connect(); con.execute("SET threads=6")
    tk = "','".join(LIQUID)
    df = con.execute(f"""
        WITH b AS (
          SELECT ticker, strftime(ts_et,'%Y-%m-%d') d, ts_et, open, high, low, close, volume,
                 strftime(ts_et,'%H:%M') tm
          FROM read_parquet('data/polygon_warehouse/minute_aggs/**/*.parquet', union_by_name=true)
          WHERE ticker IN ('{tk}') AND strftime(ts_et,'%H:%M') BETWEEN '09:30' AND '16:00'
        ), r AS (
          SELECT *, ln(close/NULLIF(lag(close) OVER (PARTITION BY ticker, d ORDER BY ts_et),0)) lr,
                 CASE WHEN low > lag(low) OVER (PARTITION BY ticker, d ORDER BY ts_et) THEN 1.0 ELSE 0.0 END hl
          FROM b )
        SELECT ticker, d,
          sqrt(sum(lr*lr)) rv,
          sqrt(sum(CASE WHEN tm >= '15:00' THEN lr*lr ELSE 0 END)) rv_lh,
          first(open ORDER BY ts_et) o, max(high) h, min(low) l, last(close ORDER BY ts_et) c,
          sum(volume) vol, sum(volume*close) dvol,
          sum(volume*close)/NULLIF(sum(volume),0) vwap,
          last(CASE WHEN tm < '09:45' THEN close END ORDER BY ts_et) c15,
          max(CASE WHEN tm < '09:45' THEN high END) h15,
          min(CASE WHEN tm < '09:45' THEN low END) l15,
          avg(hl) higher_low_frac,
          sum(CASE WHEN tm >= '12:45' THEN volume ELSE 0 END)/NULLIF(sum(volume),0) vol_2h_share,
          count(*) nbars
        FROM r GROUP BY ticker, d ORDER BY ticker, d""").df()
    con.close()
    df = df[(df.nbars >= 300) & (df.rv > 0) & (df.o > 0)].copy()
    df = df.sort_values(["ticker", "d"]).reset_index(drop=True)
    g = df.groupby("ticker", group_keys=False)
    df["prev_c"] = g["c"].shift(1)
    df["log_rv"] = np.log(df.rv)
    df["log_rv_d"] = g["log_rv"].shift(0)          # today's RV (feature for forecasting TOMORROW)
    df["log_rv_w"] = np.log(g["rv"].apply(lambda s: s.rolling(5, min_periods=3).mean()))
    df["log_rv_m"] = np.log(g["rv"].apply(lambda s: s.rolling(22, min_periods=10).mean()))
    df["target"] = g["log_rv"].shift(-1)           # next-day log RV
    df["target_rv"] = g["rv"].shift(-1)
    # transferable features (all computed from TODAY, forecasting tomorrow)
    df["abs_overnight_gap"] = (df.o / df.prev_c - 1).abs()
    df["range_pct"] = (df.h - df.l) / df.o
    df["first15_ret"] = df.c15 / df.o - 1
    df["first15_range"] = (df.h15 - df.l15) / df.o
    df["last_hour_rv_share"] = (df.rv_lh ** 2) / (df.rv ** 2)
    df["vwap_dev_close"] = df.c / df.vwap - 1
    df["vol_slope"] = df.vol_2h_share
    df["log_dvol"] = np.log(np.maximum(df.dvol, 1.0))
    df["rv_ratio_dw"] = df.rv / np.exp(df.log_rv_w)
    df = df.dropna(subset=["target", "log_rv_d", "log_rv_w", "log_rv_m", "prev_c"]).reset_index(drop=True)
    return df


def qlike(v_true, f_var):
    r = v_true / np.maximum(f_var, 1e-12)
    return r - np.log(r) - 1.0


def walk_forward(df):
    from sklearn.ensemble import HistGradientBoostingRegressor
    dates = sorted(df.d.unique())
    if len(dates) < MIN_TRAIN_SESSIONS + 42:
        raise SystemExit(f"insufficient sessions: {len(dates)}")
    # ticker fixed-effect HAR-OLS: demean log target/features by ticker train means
    preds = {m: np.full(len(df), np.nan) for m in ["har", "gbm_har", "gbm_full"]}
    date_idx = {d: i for i, d in enumerate(dates)}
    df["di"] = df.d.map(date_idx)
    refit_points = list(range(MIN_TRAIN_SESSIONS, len(dates), REFIT_EVERY))
    for rp in refit_points:
        tr = df.di < rp
        te = (df.di >= rp) & (df.di < rp + REFIT_EVERY)
        if te.sum() == 0 or tr.sum() < 1000:
            continue
        Xtr_h = df.loc[tr, HAR_F].values; ytr = df.loc[tr, "target"].values
        Xte_h = df.loc[te, HAR_F].values
        # (a) HAR-OLS with ticker FE (demeaned)
        tmeans = df.loc[tr].groupby("ticker")["target"].mean()
        gmean = float(ytr.mean())
        fe_tr = df.loc[tr, "ticker"].map(tmeans).fillna(gmean).values
        fe_te = df.loc[te, "ticker"].map(tmeans).fillna(gmean).values
        A_ = np.column_stack([np.ones(tr.sum()), Xtr_h])
        coef, *_ = np.linalg.lstsq(A_, ytr - fe_tr, rcond=None)
        preds["har"][te.values] = fe_te + np.column_stack([np.ones(te.sum()), Xte_h]) @ coef
        # (b) GBM[HAR only]
        gb1 = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, min_samples_leaf=50,
                                            l2_regularization=1.0, random_state=SEED)
        gb1.fit(Xtr_h, ytr)
        preds["gbm_har"][te.values] = gb1.predict(Xte_h)
        # (c) challenger: GBM[HAR + ours]
        Xtr_f = df.loc[tr, HAR_F + OUR_F].values; Xte_f = df.loc[te, HAR_F + OUR_F].values
        gb2 = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, min_samples_leaf=50,
                                            l2_regularization=1.0, random_state=SEED)
        gb2.fit(Xtr_f, ytr)
        preds["gbm_full"][te.values] = gb2.predict(Xte_f)
    return preds, dates


def block_boot_ci(diff, date_arr, B=B_BOOT, block=BLOCK, seed=SEED):
    """moving-block bootstrap by DATE of the mean loss differential."""
    dts = sorted(set(date_arr))
    bydate = {d: diff[date_arr == d] for d in dts}
    day_means = np.array([bydate[d].mean() for d in dts])
    n = len(day_means)
    rng = np.random.default_rng(seed)
    nblocks = int(np.ceil(n / block))
    means = []
    for _ in range(B):
        starts = rng.integers(0, max(n - block, 1), nblocks)
        sample = np.concatenate([day_means[s:s + block] for s in starts])[:n]
        means.append(sample.mean())
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def main():
    df = build_panel()
    print(f"panel: {len(df)} rows, {df.ticker.nunique()} names, {df.d.nunique()} sessions "
          f"({df.d.min()}..{df.d.max()})")
    preds, dates = walk_forward(df)
    v = ~np.isnan(preds["har"]) & ~np.isnan(preds["gbm_har"]) & ~np.isnan(preds["gbm_full"])
    sub = df.loc[v].copy()
    v_true = (sub.target_rv.values) ** 2
    losses = {}
    for m in preds:
        f_var = np.exp(preds[m][v]) ** 2   # identical transform across models (Jensen bias shared)
        losses[m] = qlike(v_true, f_var)
    date_arr = sub.d.values
    half_split = sorted(set(date_arr))[len(set(date_arr)) // 2]

    res = {"prereg_sha256": "5155f3df3ffe3ec425c931ff96e05de508a0ddfe16a8c4c9dd9d08573a85a0c4",
           "panel_rows_scored": int(v.sum()), "names": int(sub.ticker.nunique()),
           "sessions_scored": int(sub.d.nunique()), "eval_half_split": half_split,
           "qlike_pooled": {m: round(float(losses[m].mean()), 5) for m in losses}}
    best_base = min(["har", "gbm_har"], key=lambda m: losses[m].mean())
    res["better_baseline"] = best_base
    d_all = losses[best_base] - losses["gbm_full"]      # >0 = challenger better
    impr_pooled = float(d_all.mean() / losses[best_base].mean())
    halves = {}
    for hname, mask in [("H1", date_arr < half_split), ("H2", date_arr >= half_split)]:
        dh = d_all[mask]
        impr = float(dh.mean() / losses[best_base][mask].mean())
        lo, hi = block_boot_ci(dh, date_arr[mask])
        halves[hname] = {"qlike_improvement_pct": round(100 * impr, 3),
                         "diff_ci95": [round(lo, 6), round(hi, 6)], "n_days": int(len(set(date_arr[mask])))}
    lo_all, hi_all = block_boot_ci(d_all, date_arr)
    # also vs the OTHER baseline (gate = beat BOTH)
    other = "gbm_har" if best_base == "har" else "har"
    d_oth = losses[other] - losses["gbm_full"]
    lo_o, hi_o = block_boot_ci(d_oth, date_arr)
    # rank-rho per day (descriptive)
    rhos = {}
    from scipy.stats import spearmanr
    for m in preds:
        rr = []
        for d in sorted(set(date_arr)):
            mm = date_arr == d
            if mm.sum() > 20:
                rr.append(spearmanr(np.exp(preds[m][v][mm]), sub.target_rv.values[mm])[0])
        rhos[m] = round(float(np.nanmean(rr)), 4)
    res.update({
        "challenger_vs_better_baseline": {"qlike_improvement_pct_pooled": round(100 * impr_pooled, 3),
                                          "diff_ci95_pooled": [round(lo_all, 6), round(hi_all, 6)],
                                          "halves": halves},
        "challenger_vs_other_baseline": {"diff_ci95_pooled": [round(lo_o, 6), round(hi_o, 6)]},
        "per_day_rank_rho_descriptive": rhos,
        "mmi_frozen": {"pooled": MMI_POOLED, "half": MMI_HALF},
    })
    beats_both = (lo_all > 0) and (lo_o > 0)
    both_halves = all(h["diff_ci95"][0] > 0 for h in halves.values())
    mmi = (impr_pooled >= MMI_POOLED) and all(h["qlike_improvement_pct"] >= 100 * MMI_HALF for h in halves.values())
    res["gates"] = {"beats_both_baselines_ci": bool(beats_both), "both_halves_ci": bool(both_halves),
                    "minimum_meaningful_improvement": bool(mmi)}
    res["STAGE_2_PASS"] = bool(beats_both and both_halves and mmi)
    res["VERDICT"] = ("VOLATILITY DOOR OPEN AT THE HAR-RV LEVEL — necessary condition met; NOT sufficient "
                      "for edge (implied vol is the stronger tradable benchmark); procurement memo earned"
                      if res["STAGE_2_PASS"] else
                      ("open at the margin, not pivot-worthy (statistically better but below the frozen "
                       "minimum meaningful improvement)" if (beats_both and both_halves) else
                       "VOLATILITY DOOR CLOSED — our features do not beat HAR-RV on liquid names; pivot dead "
                       "at zero cost"))
    # EWMA(0.94) reference (GARCH stand-in; descriptive only, disclosed)
    ew = []
    for t, g in sub.groupby("ticker"):
        r2 = (np.log(g.c / g.prev_c) ** 2).values
        s = np.zeros(len(r2)); s[0] = r2[:5].mean() if len(r2) >= 5 else r2[0]
        for i in range(1, len(r2)):
            s[i] = 0.94 * s[i - 1] + 0.06 * r2[i - 1]
        ew.append(qlike((g.target_rv.values) ** 2, np.maximum(s, 1e-12)))
    res["ewma094_reference_qlike"] = round(float(np.concatenate(ew).mean()), 5)
    (OUT / "stage2_harrv.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
