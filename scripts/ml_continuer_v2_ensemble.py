"""ML continuer model v2: stacked ensemble + new features.

vs v1 (XGBoost + 16 features → +4.05%/trade WF):
  - 8 NEW features (cross-sectional rank, rolling per-ticker, time-of-month)
  - SECTOR + market_cap from ticker_details (when available)
  - Base learners: XGBoost + LightGBM + CatBoost + Logistic + RandomForest
  - Meta-learner: Logistic Regression on out-of-fold predictions
  - Per-fold conformal calibration

Goal: push +4.05% → +5-6%+ per trade T+5 walk-forward.

Outputs:
  data/models/continuer_v2.pkl
  data/models/continuer_v2_manifest.json
"""
from __future__ import annotations
import argparse
import json
import pickle
import sys
import time
from datetime import timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
REF_DIR = REPO / "data" / "polygon_warehouse" / "reference"
MODELS = REPO / "data" / "models"
MODELS.mkdir(parents=True, exist_ok=True)


def section(t):
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def load_data(include_paths: bool = False,
               include_microstructure: bool = False,
               include_news: bool = False,
               news_path: str | None = None,
               include_unlabeled: bool = False) -> pd.DataFrame:
    """Aftermath_strat + walk-forward priors + cross-sectional + rolling.

    If include_paths=True, also joins per-(ticker, d0) intraday path summary
    from intraday_paths_30min.parquet (built by build_intraday_paths.py).
    Adds 4 path-summary columns: first_5min_max_close, first_5min_min_close,
    last_5min_avg_close, first_5min_avg_volz, last_5min_avg_volz.

    If include_microstructure=True, also joins per-(ticker, d0) tick-level
    microstructure features from microstructure_features.parquet (built by
    build_microstructure_features.py from Polygon trades_v1). Adds
    sweep_burst_rate, dark_pool_pct, large_print_pct, true_vwap, etc.

    If include_news=True, also joins per-(ticker, d0) news catalyst features
    from news_features_polygon.parquet (built by build_news_features_polygon.py
    using Polygon /v2/reference/news). Adds n_articles_24h, weighted_sentiment,
    insights_coverage_pct, etc.
    news_path: optional override for the news parquet path (e.g., the 180d
    backfill version 'news_features_polygon_180d.parquet').

    2026-05-12 (doc 163): include_unlabeled=True omits the
    `ret_t5 IS NOT NULL` filter so the most recent ~5 trading days of
    rows (where forward returns aren't yet available) are returned. Used
    by the live-mode shadow runner to predict on today's d0 BEFORE
    realized returns exist. Default False = unchanged behavior for all
    existing callers (training/backtest/research scripts that need
    labeled data only).
    """
    aftermath = (DERIVED / "aftermath_strat.parquet").as_posix()
    con = duckdb.connect()
    con.sql("SET memory_limit='6GB'")
    con.sql(f"SET temp_directory='{(REPO / '.duckdb_tmp').as_posix()}'")

    # Doc 163: conditional ret_t5 filter for live-mode shadow scoring.
    # When include_unlabeled=True, return today's d0 rows (NULL ret_t5)
    # along with the labeled history; the shadow runner uses them as
    # the predict-target set.
    _ret_t5_clause = "" if include_unlabeled else "AND ret_t5 IS NOT NULL"

    df = con.sql(f"""
        WITH base AS (
            SELECT * FROM read_parquet('{aftermath}')
            WHERE ca_flag = 'clean'
              {_ret_t5_clause}
              AND open BETWEEN 0.5 AND 50 AND volume > 100000
        ),
        enriched AS (
            SELECT *,
                   COUNT(*) OVER (PARTITION BY ticker ORDER BY d0
                                   ROWS BETWEEN UNBOUNDED PRECEDING
                                       AND 1 PRECEDING) AS prior_n,
                   SUM(CASE WHEN ret_t5 >= 0.10 THEN 1 ELSE 0 END) OVER (
                       PARTITION BY ticker ORDER BY d0
                       ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prior_cont,
                   SUM(CASE WHEN ret_t5 <= -0.10 THEN 1 ELSE 0 END) OVER (
                       PARTITION BY ticker ORDER BY d0
                       ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS prior_fade,
                   COUNT(*) OVER (PARTITION BY ticker
                                   ORDER BY d0
                                   RANGE BETWEEN INTERVAL 7 DAY PRECEDING
                                           AND INTERVAL 1 DAY PRECEDING) AS prior_7d_count,
                   COUNT(*) OVER (PARTITION BY ticker
                                   ORDER BY d0
                                   RANGE BETWEEN INTERVAL 30 DAY PRECEDING
                                           AND INTERVAL 1 DAY PRECEDING) AS prior_30d_count,
                   AVG(ret_t5) OVER (PARTITION BY ticker ORDER BY d0
                                     ROWS BETWEEN UNBOUNDED PRECEDING
                                         AND 1 PRECEDING) AS prior_avg_t5,
                   AVG(intraday_pct) OVER (PARTITION BY ticker ORDER BY d0
                                           ROWS BETWEEN UNBOUNDED PRECEDING
                                               AND 1 PRECEDING) AS prior_avg_intra,
                   AVG(ret_open_close_d0) OVER (PARTITION BY ticker ORDER BY d0
                                           ROWS BETWEEN UNBOUNDED PRECEDING
                                               AND 1 PRECEDING) AS prior_avg_oc
            FROM base
        ),
        cross_sectional AS (
            SELECT *,
                   ROW_NUMBER() OVER (PARTITION BY d0 ORDER BY intraday_pct DESC) AS rank_intra,
                   ROW_NUMBER() OVER (PARTITION BY d0 ORDER BY dvol_d0 DESC) AS rank_dvol,
                   COUNT(*) OVER (PARTITION BY d0) AS n_today_total,
                   AVG(intraday_pct) OVER (PARTITION BY d0) AS today_avg_intra,
                   AVG(dvol_d0) OVER (PARTITION BY d0) AS today_avg_dvol
            FROM enriched
        )
        -- D287 (2026-05-11, doc 145): add ticker as secondary sort key for
        -- deterministic intra-day row order. DuckDB's ORDER BY d0 alone is
        -- non-deterministic for ties (multi-thread execution), which caused
        -- TabPFN preds saved with `_idx` to misalign with later reconstructions
        -- of (ticker, d0) — see doc 145 § validation hygiene.
        SELECT * FROM cross_sectional ORDER BY d0, ticker
    """).df()

    # Try to enrich with ticker_details if available
    td_path = REF_DIR / "ticker_details.parquet"
    if td_path.exists():
        td = pd.read_parquet(td_path)
        # Keep only fields we'll use (incl. sic_description for sector dummies)
        cols = ["ticker", "market_cap", "sic_code", "sic_description",
                 "total_employees", "share_class_shares_outstanding", "list_date"]
        cols = [c for c in cols if c in td.columns]
        td_slim = td[cols].copy()
        df = df.merge(td_slim, on="ticker", how="left")
        print(f"  Joined ticker_details: {df['market_cap'].notna().sum()}/{len(df)} have mcap")
    else:
        # Stub columns so feature engineering doesn't break
        for c in ["market_cap", "sic_code", "total_employees",
                   "share_class_shares_outstanding", "list_date"]:
            df[c] = np.nan
        print("  ticker_details.parquet not yet available; skipping enrichment")

    # Optional: enrich with intraday-path summary for v3 features
    if include_paths:
        paths_p = DERIVED / "intraday_paths_30min.parquet"
        if paths_p.exists():
            ps = duckdb.connect().sql(f"""
                SELECT ticker, d0,
                       MAX(close_rel) FILTER (WHERE bar_idx < 5)  AS first_5min_max_close,
                       MIN(close_rel) FILTER (WHERE bar_idx < 5)  AS first_5min_min_close,
                       AVG(close_rel) FILTER (WHERE bar_idx >= 25) AS last_5min_avg_close,
                       AVG(vol_z)     FILTER (WHERE bar_idx < 5)  AS first_5min_avg_volz,
                       AVG(vol_z)     FILTER (WHERE bar_idx >= 25) AS last_5min_avg_volz
                FROM read_parquet('{paths_p.as_posix()}')
                GROUP BY ticker, d0
            """).df()
            ps["d0"] = pd.to_datetime(ps["d0"])
            df["d0"] = pd.to_datetime(df["d0"])
            df = df.merge(ps, on=["ticker", "d0"], how="left")
            n_with = df["last_5min_avg_close"].notna().sum()
            print(f"  Joined intraday paths: {n_with}/{len(df)} have path summary")
        else:
            print(f"  WARNING: --include-paths requested but {paths_p} missing; skipping")
            for c in ["first_5min_max_close", "first_5min_min_close",
                      "last_5min_avg_close", "first_5min_avg_volz",
                      "last_5min_avg_volz"]:
                df[c] = np.nan

    # Optional: enrich with news catalyst features (Phase 3)
    if include_news:
        news_p = Path(news_path) if news_path else (DERIVED / "news_features_polygon.parquet")
        if news_p.exists():
            news = pd.read_parquet(news_p)
            news["d0"] = pd.to_datetime(news["d0"])
            df["d0"] = pd.to_datetime(df["d0"])
            df = df.merge(news, on=["ticker", "d0"], how="left",
                            suffixes=("", "_news"))
            n_with = (df["n_articles_24h"] > 0).sum() if "n_articles_24h" in df.columns else 0
            print(f"  Joined news features ({news_p.name}): "
                  f"{n_with}/{len(df)} have >=1 article")
        else:
            print(f"  WARNING: --include-news requested but {news_p} missing; skipping")
            for c in ["n_articles_24h", "n_unique_publishers", "pct_positive",
                       "pct_negative", "pct_neutral", "weighted_sentiment",
                       "weighted_sentiment_norm", "hours_to_first_article",
                       "co_mention_count_avg", "has_official_filing",
                       "has_ratings_change", "insights_coverage_pct"]:
                df[c] = np.nan

    # Optional: enrich with tick-level microstructure features (Phase 3)
    if include_microstructure:
        ms_p = DERIVED / "microstructure_features.parquet"
        if ms_p.exists():
            ms = pd.read_parquet(ms_p)
            ms["d0"] = pd.to_datetime(ms["d0"])
            df["d0"] = pd.to_datetime(df["d0"])
            df = df.merge(ms, on=["ticker", "d0"], how="left")
            n_with = df["sweep_burst_rate_first30"].notna().sum()
            print(f"  Joined microstructure: {n_with}/{len(df)} have tick-level features")
        else:
            print(f"  WARNING: --include-microstructure requested but {ms_p} missing; skipping")
            for c in ["sweep_burst_count_first30", "sweep_burst_rate_first30",
                      "dark_pool_pct_first30", "large_print_pct_first30",
                      "odd_lot_pct_first30", "true_vwap_first30",
                      "print_size_p90_first30", "n_trades_first30",
                      "sweep_burst_count_full", "dark_pool_pct_full",
                      "large_print_pct_full", "n_trades_full",
                      "iso_to_dark_ratio"]:
                df[c] = np.nan

    return df


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """Walk-forward-safe feature matrix."""
    f = pd.DataFrame()
    # ── Original 16 features (v1)
    f["log_open"] = np.log(df["open"].clip(lower=0.01))
    f["log_dvol_d0"] = np.log(df["dvol_d0"].clip(lower=1))
    f["intraday_pct"] = df["intraday_pct"]
    f["intraday_pct_log"] = np.log1p(df["intraday_pct"].clip(lower=0))
    f["ret_open_close_d0"] = df["ret_open_close_d0"]
    f["close_strength"] = df["ret_open_close_d0"] / df["intraday_pct"].clip(lower=0.01)
    f["prior_n"] = df["prior_n"].fillna(0)
    f["prior_n_log"] = np.log1p(df["prior_n"].fillna(0))
    f["prior_cont_rate"] = (20.78 + df["prior_cont"].fillna(0) * 100) / (100 + df["prior_n"].fillna(0))
    f["prior_fade_rate"] = (43.36 + df["prior_fade"].fillna(0) * 100) / (100 + df["prior_n"].fillna(0))
    f["prior_avg_t5"] = df["prior_avg_t5"].fillna(0)
    f["prior_7d_count"] = df["prior_7d_count"].fillna(0)
    d0 = pd.to_datetime(df["d0"])
    f["dow"] = d0.dt.dayofweek
    f["month"] = d0.dt.month
    f["year"] = d0.dt.year
    f["day_of_year"] = d0.dt.dayofyear

    # ── NEW features (v2)
    # Cross-sectional ranks (per-day)
    f["rank_intra"] = df["rank_intra"].fillna(99)
    f["rank_intra_log"] = np.log1p(df["rank_intra"].fillna(99))
    f["rank_dvol"] = df["rank_dvol"].fillna(99)
    f["n_today_total"] = df["n_today_total"].fillna(0)
    # Relative measures: am I above today's average?
    f["intra_vs_today_avg"] = df["intraday_pct"] / df["today_avg_intra"].clip(lower=0.01)
    f["dvol_vs_today_avg"] = df["dvol_d0"] / df["today_avg_dvol"].clip(lower=1)
    # Rolling per-ticker stats
    f["prior_avg_intra"] = df["prior_avg_intra"].fillna(0)
    f["prior_avg_oc"] = df["prior_avg_oc"].fillna(0)
    f["prior_30d_count"] = df["prior_30d_count"].fillna(0)
    # Intra vs ticker's historical avg intra
    f["intra_vs_prior"] = (df["intraday_pct"] / df["prior_avg_intra"].clip(lower=0.01)).fillna(1.0)
    # Time-of-month
    f["day_of_month"] = d0.dt.day
    f["week_of_month"] = (d0.dt.day - 1) // 7 + 1
    # Quarter
    f["quarter"] = d0.dt.quarter
    # Year fraction (continuous time)
    f["year_frac"] = d0.dt.dayofyear / 365.25

    # ── Ticker_details features (when available)
    if "market_cap" in df.columns and df["market_cap"].notna().any():
        f["log_market_cap"] = np.log(df["market_cap"].fillna(50e6).clip(lower=1))
        f["mcap_known"] = df["market_cap"].notna().astype(int)
        f["log_employees"] = np.log(df["total_employees"].fillna(100).clip(lower=1))
        # Days since IPO
        if "list_date" in df.columns:
            list_date = pd.to_datetime(df["list_date"], errors="coerce")
            days_since_ipo = (d0 - list_date).dt.days.fillna(3650)  # default 10y if unknown
            f["log_days_since_ipo"] = np.log1p(days_since_ipo.clip(lower=1))
        else:
            f["log_days_since_ipo"] = np.log1p(3650)
        # SIC code as integer (will let tree models handle as categorical-ish)
        # Coerce to numeric (some rows are strings e.g., '1234' vs 1234)
        sic_numeric = pd.to_numeric(df["sic_code"], errors="coerce").fillna(0).astype(int)
        f["sic_code"] = sic_numeric
        # SIC group (first 2 digits = industry sector)
        f["sic_group"] = (sic_numeric // 100).astype(int)
        # Sector dummies for top-7 sectors in catalog (per doc 105 §4.4):
        # PHARMA, BIOLOGICAL, MEDICAL, SOFTWARE, FINANCE, SEMICONDUCTORS, BLANK_CHECK (SPACs)
        sic_str = df["sic_description"].fillna("").str.upper() if "sic_description" in df.columns else pd.Series("", index=df.index)
        f["sec_pharma"] = sic_str.str.contains("PHARMACEUTICAL", regex=False).astype(int)
        f["sec_bio"] = sic_str.str.contains("BIOLOGICAL", regex=False).astype(int)
        f["sec_medical"] = sic_str.str.contains("SURGICAL|MEDICAL", regex=True).astype(int)
        f["sec_software"] = sic_str.str.contains("SOFTWARE", regex=False).astype(int)
        f["sec_finance"] = sic_str.str.contains("FINANCE", regex=False).astype(int)
        f["sec_semi"] = sic_str.str.contains("SEMICONDUCTOR", regex=False).astype(int)
        f["sec_spac"] = sic_str.str.contains("BLANK CHECK", regex=False).astype(int)
        f["sec_reit"] = sic_str.str.contains("REAL ESTATE", regex=False).astype(int)
        # Float (shares outstanding)
        sso = df.get("share_class_shares_outstanding", pd.Series(np.nan, index=df.index))
        f["log_float"] = np.log(sso.fillna(1e7).clip(lower=1))
    else:
        # Stub zero columns to keep feature shape consistent
        for c in ("log_market_cap", "mcap_known", "log_employees",
                   "log_days_since_ipo", "sic_code", "sic_group", "log_float"):
            f[c] = 0

    # ── v3 features (path-derived + LLM survivor) ──
    # Only added if path summary columns exist on df (load_data(include_paths=True)).
    # LLM survivor `log_rank_x_prior_continuer` is pure-function of v2 features
    # and ALWAYS added — gated by checking we haven't already.
    if "log_rank_x_prior_continuer" not in f.columns:
        # Smoothed prior continuer rate (Beta(1,4) prior over [0,1])
        smoothed_cr = (1 + df["prior_cont"].fillna(0)) / (5 + df["prior_n"].fillna(0))
        f["log_rank_x_prior_continuer"] = np.log1p(df["rank_intra"].fillna(99)) * smoothed_cr

    # Path-derived (v3): only present when --include-paths used
    if "first_5min_max_close" in df.columns:
        f["first_5min_max_close"] = df["first_5min_max_close"].fillna(0)
        f["first_5min_min_close"] = df["first_5min_min_close"].fillna(0)
        f["last_5min_avg_close"]  = df["last_5min_avg_close"].fillna(0)
        f["first_5min_avg_volz"]  = df["first_5min_avg_volz"].fillna(0)
        f["last_5min_avg_volz"]   = df["last_5min_avg_volz"].fillna(0)
        # Derived: u-shape detector (last_5 - midpoint(first_5_max, first_5_min))
        f["u_shape_intraday"] = (
            f["last_5min_avg_close"]
            - 0.5 * (f["first_5min_max_close"] + f["first_5min_min_close"])
        )
        # Derived: volume acceleration (last_5 vol_z - first_5 vol_z)
        f["volume_acceleration"] = f["last_5min_avg_volz"] - f["first_5min_avg_volz"]
        # Indicator: have path data? (model can use as gate)
        f["has_path"] = df["last_5min_avg_close"].notna().astype(int)

    # News catalyst (v4): only present when --include-news used.
    # Uses log1p / clip transforms to stabilize; NaN -> 0 with explicit indicator.
    if "n_articles_24h" in df.columns:
        f["log_n_articles_24h"] = np.log1p(df["n_articles_24h"].fillna(0))
        f["n_unique_publishers"] = df["n_unique_publishers"].fillna(0)
        f["pct_positive"] = df["pct_positive"].fillna(0).clip(0, 1)
        f["pct_negative"] = df["pct_negative"].fillna(0).clip(0, 1)
        f["pct_neutral"] = df["pct_neutral"].fillna(0).clip(0, 1)
        f["weighted_sentiment_norm"] = df["weighted_sentiment_norm"].fillna(0).clip(-1, 1)
        # Hours to first article: -1 means no news (preserve as flag); else clip 0-48
        hours = df["hours_to_first_article"].fillna(-1)
        f["hours_to_first_article"] = hours.clip(-1, 48)
        f["co_mention_count_avg"] = np.log1p(df["co_mention_count_avg"].fillna(0))
        f["has_official_filing"] = df["has_official_filing"].fillna(0).astype(int)
        f["has_ratings_change"] = df["has_ratings_change"].fillna(0).astype(int)
        f["insights_coverage_pct"] = df["insights_coverage_pct"].fillna(0).clip(0, 1)
        # Has-news indicator (gate for trees)
        f["has_news"] = (df["n_articles_24h"] > 0).fillna(False).astype(int)

    # Microstructure (v4): only present when --include-microstructure used.
    # Uses log1p / clip transforms to stabilize. NaN -> 0 (model handles
    # absence implicitly via has_microstructure indicator).
    if "sweep_burst_rate_first30" in df.columns:
        f["sweep_burst_rate_first30"] = df["sweep_burst_rate_first30"].fillna(0)
        f["sweep_burst_count_first30_log"] = np.log1p(df["sweep_burst_count_first30"].fillna(0))
        f["dark_pool_pct_first30"] = df["dark_pool_pct_first30"].fillna(0).clip(0, 1)
        f["large_print_pct_first30"] = df["large_print_pct_first30"].fillna(0).clip(0, 1)
        f["odd_lot_pct_first30"] = df["odd_lot_pct_first30"].fillna(0).clip(0, 1)
        f["log_n_trades_first30"] = np.log1p(df["n_trades_first30"].fillna(0))
        f["log_print_size_p90"] = np.log1p(df["print_size_p90_first30"].fillna(0))
        f["dark_pool_pct_full"] = df["dark_pool_pct_full"].fillna(0).clip(0, 1)
        f["large_print_pct_full"] = df["large_print_pct_full"].fillna(0).clip(0, 1)
        # ISO-to-dark ratio with safety
        ratio = df["iso_to_dark_ratio"].fillna(0).clip(-1e6, 1e6)
        f["log_iso_to_dark_ratio"] = np.log1p(ratio.clip(lower=0))
        # Has-microstructure indicator
        f["has_microstructure"] = df["sweep_burst_rate_first30"].notna().astype(int)

    return f


def build_targets(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    y_cls = (df["ret_t5"] >= 0.10).astype(int)
    y_reg = df["ret_t5"].clip(-0.50, 1.00)
    return y_cls, y_reg


def walk_forward_cv(dates, train_window_days=365, test_window_days=30,
                     min_train_size=1000):
    dates_dt = pd.to_datetime(dates)
    start = dates_dt.min() + timedelta(days=train_window_days)
    end = dates_dt.max()
    cur = start
    while cur < end:
        train_end = cur
        train_start = cur - timedelta(days=train_window_days)
        test_end = cur + timedelta(days=test_window_days)
        train_mask = (dates_dt >= train_start) & (dates_dt < train_end)
        test_mask = (dates_dt >= train_end) & (dates_dt < test_end)
        if train_mask.sum() < min_train_size or test_mask.sum() == 0:
            cur += timedelta(days=test_window_days); continue
        yield train_mask.values, test_mask.values
        cur += timedelta(days=test_window_days)


# ── Base learners ──

def fit_xgb(X, y, params: dict | None = None):
    import xgboost as xgb
    p = params or {}
    m = xgb.XGBClassifier(
        n_estimators=int(p.get("xgb_n_estimators", 400)),
        max_depth=int(p.get("xgb_max_depth", 4)),
        learning_rate=float(p.get("xgb_lr", 0.04)),
        subsample=float(p.get("xgb_subsample", 0.8)),
        colsample_bytree=float(p.get("xgb_colsample", 0.8)),
        min_child_weight=int(p.get("xgb_min_child", 1)),
        random_state=42, verbosity=0, n_jobs=-1, eval_metric="logloss",
    )
    return m.fit(X, y)


def fit_lgbm(X, y, params: dict | None = None):
    import lightgbm as lgb
    p = params or {}
    m = lgb.LGBMClassifier(
        n_estimators=int(p.get("lgbm_n_estimators", 400)),
        max_depth=int(p.get("lgbm_max_depth", 6)),
        learning_rate=float(p.get("lgbm_lr", 0.04)),
        num_leaves=int(p.get("lgbm_num_leaves", 31)),
        subsample=float(p.get("lgbm_subsample", 0.8)),
        colsample_bytree=float(p.get("lgbm_colsample", 0.8)),
        random_state=42, verbosity=-1, n_jobs=-1,
    )
    return m.fit(X, y)


def fit_logreg(X, y):
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import Pipeline
    p = Pipeline([("sc", StandardScaler()),
                   ("lr", LogisticRegression(max_iter=400, C=0.5,
                                              solver="lbfgs",
                                              random_state=42))])
    return p.fit(X, y)


def fit_rf(X, y):
    from sklearn.ensemble import RandomForestClassifier
    m = RandomForestClassifier(n_estimators=300, max_depth=8, min_samples_leaf=20,
                                 random_state=42, n_jobs=-1)
    return m.fit(X, y)


# ── Meta learner ──

def fit_meta(P, y):
    """Logistic regression over out-of-fold base predictions."""
    from sklearn.linear_model import LogisticRegression
    return LogisticRegression(max_iter=400, C=1.0, random_state=42).fit(P, y)


def conformal_calibrate(probs, y, alpha=0.10):
    nc = 1 - np.where(y == 1, probs, 1 - probs)
    n = len(nc)
    q = np.ceil((n + 1) * (1 - alpha)) / n
    return float(np.quantile(nc, min(q, 1.0)))


def conformal_width(probs, thr):
    width = (1 - np.abs(probs - 0.5) * 2) * (1 + thr)
    return np.clip(width, 0, 1)


def baseline_chronic_fader_gate(features: pd.DataFrame) -> pd.Series:
    dvol = np.exp(features["log_dvol_d0"])
    rate = features["prior_cont_rate"]
    n = features["prior_n"]
    p7d = features["prior_7d_count"]
    intra = features["intraday_pct"]
    return ((n == 0) | (rate >= 5.0)) & (p7d <= 1) & (dvol >= 1e5) & (dvol <= 100e6) & (intra >= 0.30) & (intra <= 1.00)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--optuna-params", type=str, default=None,
                        help="Path to v2_optuna_best_params.json (uses 'best_params')")
    parser.add_argument("--out-suffix", type=str, default="",
                        help="Suffix for output artifacts (e.g. '_tuned', '_v3')")
    parser.add_argument("--include-paths", action="store_true",
                        help="Join intraday_paths_30min.parquet + add v3 path features")
    parser.add_argument("--include-microstructure", action="store_true",
                        help="Join microstructure_features.parquet + add v4 tick-level features")
    parser.add_argument("--include-news", action="store_true",
                        help="Join news_features_polygon.parquet + add v4 catalyst features")
    parser.add_argument("--news-path", type=str, default=None,
                        help="Override news parquet path (default: news_features_polygon.parquet)")
    args = parser.parse_args()

    optuna_params: dict | None = None
    if args.optuna_params:
        op = json.loads(Path(args.optuna_params).read_text())
        optuna_params = op.get("best_params", op)
        print(f"  Loaded Optuna params from {args.optuna_params}: {len(optuna_params)} keys")
        print(f"    {optuna_params}")

    section("STEP 1 - Load + enrich")
    t0 = time.perf_counter()
    df = load_data(include_paths=args.include_paths,
                    include_microstructure=args.include_microstructure,
                    include_news=args.include_news,
                    news_path=args.news_path)
    print(f"  loaded {len(df):,} rows in {time.perf_counter()-t0:.1f}s")

    section("STEP 2 - Feature engineering (v2)")
    X = engineer_features(df)
    # Final NaN/inf safety: replace with 0 (sklearn's logreg/scaler intolerant)
    X = X.replace([np.inf, -np.inf], 0).fillna(0)
    y_cls, _ = build_targets(df)
    dates = df["d0"]
    print(f"  features ({len(X.columns)}): {list(X.columns)}")
    print(f"  X: {X.shape}, y: {y_cls.mean()*100:.2f}% positive")

    section("STEP 3 - Walk-forward stacked ensemble")
    folds = list(walk_forward_cv(dates, train_window_days=365, test_window_days=30))
    print(f"  n folds: {len(folds)}")

    fold_results = []
    all_test_predictions = []  # NEW: per-row predictions for downstream Ising stratification
    for fold_i, (tr_mask, te_mask) in enumerate(folds):
        Xtr, Xte = X[tr_mask], X[te_mask]
        ytr, yte = y_cls[tr_mask], y_cls[te_mask]
        if len(Xte) < 30: continue

        # Inside-fold split: 60% base train, 20% meta train, 20% calibration
        n = len(Xtr)
        rng = np.random.RandomState(42 + fold_i)
        perm = rng.permutation(n)
        n_meta = int(n * 0.20)
        n_calib = int(n * 0.20)
        meta_idx = perm[:n_meta]
        calib_idx = perm[n_meta:n_meta + n_calib]
        base_idx = perm[n_meta + n_calib:]
        Xb, yb = Xtr.iloc[base_idx], ytr.iloc[base_idx]
        Xm, ym = Xtr.iloc[meta_idx], ytr.iloc[meta_idx]
        Xc, yc = Xtr.iloc[calib_idx], ytr.iloc[calib_idx]

        # Train base learners
        learners = {
            "xgb":    fit_xgb(Xb, yb, optuna_params),
            "lgbm":   fit_lgbm(Xb, yb, optuna_params),
            "logreg": fit_logreg(Xb, yb),
            "rf":     fit_rf(Xb, yb),
        }
        # Get meta-train base predictions
        P_meta = np.column_stack([m.predict_proba(Xm)[:, 1] for m in learners.values()])
        # Train meta
        meta = fit_meta(P_meta, ym.values)

        # Get test predictions through full stack
        P_test = np.column_stack([m.predict_proba(Xte)[:, 1] for m in learners.values()])
        probs_test = meta.predict_proba(P_test)[:, 1]

        # Conformal threshold from calibration set (through full stack)
        P_calib = np.column_stack([m.predict_proba(Xc)[:, 1] for m in learners.values()])
        probs_calib = meta.predict_proba(P_calib)[:, 1]
        thr = conformal_calibrate(probs_calib, yc.values, alpha=0.10)
        widths = conformal_width(probs_test, thr)

        # Compare strategies
        baseline_te = baseline_chronic_fader_gate(Xte)
        y_reg_te = df.loc[te_mask, "ret_t5"].clip(-0.50, 1.00)

        def stat(mask):
            n = mask.sum()
            if n == 0: return (0, 0, 0)
            return (n, y_reg_te[mask].mean(), (y_reg_te[mask] > 0).mean())

        # Per base learner (using simple mean, no meta) for comparison
        prob_xgb = learners["xgb"].predict_proba(Xte)[:, 1]
        prob_lgbm = learners["lgbm"].predict_proba(Xte)[:, 1]

        baseline_n, baseline_avg, baseline_win = stat(baseline_te)
        ml_30_n, ml_30_avg, ml_30_win = stat(probs_test >= 0.30)
        ml_50_n, ml_50_avg, ml_50_win = stat(probs_test >= 0.50)
        ml_60_n, ml_60_avg, ml_60_win = stat(probs_test >= 0.60)
        # XGB-only 0.30 (compare to v1)
        xgb_30_n, xgb_30_avg, xgb_30_win = stat(prob_xgb >= 0.30)

        fold_results.append({
            "fold": fold_i,
            "test_start": str(dates[te_mask].min()),
            "n_test": len(Xte),
            "baseline_n": int(baseline_n), "baseline_avg": baseline_avg, "baseline_win": baseline_win,
            "xgb_30_n": int(xgb_30_n), "xgb_30_avg": xgb_30_avg, "xgb_30_win": xgb_30_win,
            "ml_30_n": int(ml_30_n), "ml_30_avg": ml_30_avg, "ml_30_win": ml_30_win,
            "ml_50_n": int(ml_50_n), "ml_50_avg": ml_50_avg, "ml_50_win": ml_50_win,
            "ml_60_n": int(ml_60_n), "ml_60_avg": ml_60_avg, "ml_60_win": ml_60_win,
            "conformal_threshold": thr,
        })

        # NEW: persist per-row predictions for downstream Ising stratification
        per_row = pd.DataFrame({
            "fold": fold_i,
            "d0": df.loc[te_mask, "d0"].values,
            "ticker": df.loc[te_mask, "ticker"].values,
            "y_cls": yte.values if hasattr(yte, "values") else yte,
            "y_reg": y_reg_te.values if hasattr(y_reg_te, "values") else y_reg_te,
            "prob_continuer": probs_test,
            "conformal_width": widths,
            "baseline_pass": baseline_te.values if hasattr(baseline_te, "values") else baseline_te,
            "xgb_only_proba": prob_xgb,
        })
        all_test_predictions.append(per_row)

    section("STEP 4 - Aggregate")
    fr = pd.DataFrame(fold_results)
    fr = fr[fr["n_test"] >= 30]
    print(f"  n folds with >=30 test rows: {len(fr)}")

    def agg(name, n_col, avg_col, win_col):
        total_n = fr[n_col].sum()
        if total_n == 0: return f"{name}: no samples"
        weighted_avg = (fr[n_col] * fr[avg_col]).sum() / total_n
        weighted_win = (fr[n_col] * fr[win_col]).sum() / total_n
        sharpe = fr[avg_col].mean() / max(fr[avg_col].std(), 1e-6) * (252/30)**0.5
        return (f"{name:<35} n={total_n:>6,}  weighted_avg={weighted_avg*100:>+6.2f}%  "
                f"weighted_win={weighted_win*100:>5.1f}%  sharpe~{sharpe:>+5.2f}")

    print()
    print("  STRATEGY                              SAMPLES   WEIGHTED AVG  WIN%   SHARPE")
    print("  " + "-" * 88)
    print("  " + agg("BASELINE (V3-WF gate)", "baseline_n", "baseline_avg", "baseline_win"))
    print("  " + agg("v1 XGBoost only (P>=0.30)", "xgb_30_n", "xgb_30_avg", "xgb_30_win"))
    print("  " + agg("v2 STACKED ENSEMBLE P>=0.30", "ml_30_n", "ml_30_avg", "ml_30_win"))
    print("  " + agg("v2 STACKED ENSEMBLE P>=0.50", "ml_50_n", "ml_50_avg", "ml_50_win"))
    print("  " + agg("v2 STACKED ENSEMBLE P>=0.60", "ml_60_n", "ml_60_avg", "ml_60_win"))

    section("STEP 5 - Per-fold breakdown (v2 ensemble P>=0.30)")
    print(f"  {'fold':<5} {'test_start':<12} {'n':>4} {'b_n':>4} {'b_avg':>8} "
          f"{'v1_n':>4} {'v1_avg':>8} {'v2_n':>4} {'v2_avg':>8}")
    for _, r in fr.iterrows():
        print(f"  {r['fold']:<5} {r['test_start']:<12} {r['n_test']:>4,} "
              f"{r['baseline_n']:>4,} {r['baseline_avg']*100:>+7.2f}% "
              f"{r['xgb_30_n']:>4,} {r['xgb_30_avg']*100:>+7.2f}% "
              f"{r['ml_30_n']:>4,} {r['ml_30_avg']*100:>+7.2f}%")

    # Persist per-row predictions for downstream Ising stratification
    if all_test_predictions:
        preds_path = DERIVED / f"ml_v2_walkforward_predictions{args.out_suffix}.parquet"
        all_preds = pd.concat(all_test_predictions, ignore_index=True)
        all_preds.to_parquet(preds_path, compression="zstd")
        print(f"\n  Wrote {preds_path} ({len(all_preds):,} rows)")

    section("STEP 6 - Persist v2 model")
    # Train final stack on all data through last cutoff
    cutoff = dates.max() - timedelta(days=5)
    final_mask = (dates < cutoff).values
    Xf, yf = X[final_mask], y_cls[final_mask]
    rng = np.random.RandomState(42)
    n = len(Xf)
    perm = rng.permutation(n)
    n_meta = int(n * 0.20)
    n_calib = int(n * 0.20)
    meta_idx, calib_idx, base_idx = perm[:n_meta], perm[n_meta:n_meta+n_calib], perm[n_meta+n_calib:]
    Xfb, yfb = Xf.iloc[base_idx], yf.iloc[base_idx]

    final_learners = {
        "xgb":    fit_xgb(Xfb, yfb, optuna_params),
        "lgbm":   fit_lgbm(Xfb, yfb, optuna_params),
        "logreg": fit_logreg(Xfb, yfb),
        "rf":     fit_rf(Xfb, yfb),
    }
    P_meta = np.column_stack([m.predict_proba(Xf.iloc[meta_idx])[:, 1] for m in final_learners.values()])
    final_meta = fit_meta(P_meta, yf.iloc[meta_idx].values)
    P_calib = np.column_stack([m.predict_proba(Xf.iloc[calib_idx])[:, 1] for m in final_learners.values()])
    probs_calib = final_meta.predict_proba(P_calib)[:, 1]
    final_thr = conformal_calibrate(probs_calib, yf.iloc[calib_idx].values, alpha=0.10)

    artifacts = {
        "version": "v2",
        "base_learners": final_learners,
        "meta_learner": final_meta,
        "conformal_threshold": float(final_thr),
        "feature_columns": list(X.columns),
        "training_rows": len(Xf),
    }
    pkl_path = MODELS / f"continuer_v2{args.out_suffix}.pkl"
    with open(pkl_path, "wb") as f:
        pickle.dump(artifacts, f)
    print(f"  -> {pkl_path}")

    manifest = {
        "version": "v2",
        "trained_at": pd.Timestamp.utcnow().isoformat(),
        "data_rows": len(df),
        "training_rows": len(Xf),
        "feature_columns": list(X.columns),
        "n_features": len(X.columns),
        "conformal_threshold": float(final_thr),
        "base_learners": list(final_learners.keys()),
        "meta_learner": "logreg",
        "walk_forward_results": {
            "n_folds": int(len(fr)),
            "baseline_weighted_avg": float((fr["baseline_n"] * fr["baseline_avg"]).sum() / max(fr["baseline_n"].sum(), 1)),
            "xgb_30_weighted_avg":   float((fr["xgb_30_n"]   * fr["xgb_30_avg"]).sum()   / max(fr["xgb_30_n"].sum(), 1)),
            "ml_30_weighted_avg":    float((fr["ml_30_n"]    * fr["ml_30_avg"]).sum()    / max(fr["ml_30_n"].sum(), 1)),
            "ml_50_weighted_avg":    float((fr["ml_50_n"]    * fr["ml_50_avg"]).sum()    / max(fr["ml_50_n"].sum(), 1)),
            "ml_60_weighted_avg":    float((fr["ml_60_n"]    * fr["ml_60_avg"]).sum()    / max(fr["ml_60_n"].sum(), 1)),
        },
    }
    manifest["optuna_params"] = optuna_params
    manifest_path = MODELS / f"continuer_v2{args.out_suffix}_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str))
    print(f"  -> {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
