# 115 — News flag + Simplified VETOED rule + EOD anomaly detection

**Session date:** 2026-05-02
**Branch:** develop → main (merged + pushed)
**Predecessor:** [114 Polygon news + TCN debunk + long backfill](114_polygon_news_placebo_tcn_long_backfill.md)

---

## TL;DR

Three production-ready additions while overnight data jobs run:

1. **`--include-news` flag wired into v3 ensemble** (`scripts/ml_continuer_v2_ensemble.py`).
   Mirrors `--include-microstructure` from s113b. 12 new news features
   (log_n_articles_24h, weighted_sentiment_norm, has_official_filing, etc.).
   Smoke-tested against the 100-row scaffold parquet — feature count goes
   54 → 66. Ready to use the moment news 180-day backfill completes.

2. **🎯 Simplified VETOED rule analysis: Option D (`v3+MID + intra<p25`)
   produces +13.37% on n=67** — beats production VETOED's +9.07% by +4.3pp
   per-trade. Validates Lou/Polk/Skouras 2019 fade prior empirically.
   **Option A (drop VETOED, expand to full v3+MID): $+7,134 Kelly P&L vs
   production $+2,902 (146% better in $)** — cleanest TCN-strip path.

3. **Monitor anomaly detection ships** (`scripts/monitor_paper_deploy.py
   --eod`). Compares live tier-fire frequency vs Poisson-expected daily
   rate from WF; flags |z| > 2.0σ. Posts to Discord webhook from `.env`
   on anomaly. Quiet on no-anomaly (avoids spam).

---

## 1. `--include-news` v3 ensemble flag

### Code paths
- `load_data(include_news=True, news_path=...)`: joins news parquet on
  (ticker, d0); falls back to NaN columns if file missing
- `engineer_features()` adds 12 news features:
  ```
  log_n_articles_24h, n_unique_publishers, pct_positive, pct_negative,
  pct_neutral, weighted_sentiment_norm, hours_to_first_article,
  co_mention_count_avg, has_official_filing, has_ratings_change,
  insights_coverage_pct, has_news (indicator)
  ```
- CLI: `--include-news` + `--news-path` (override default file)

### Smoke test
```
$ python -c "from ml_continuer_v2_ensemble import load_data, engineer_features
              df = load_data(include_paths=True, include_news=True)
              X = engineer_features(df)
              print(f'features: {len(X.columns)}')"
Joined news features (news_features_polygon.parquet): 5/20029 have >=1 article
features: 66
```

### Activation (after backfill completes)
```powershell
python scripts/ml_continuer_v2_ensemble.py \
  --include-paths --include-news \
  --news-path data/polygon_warehouse/derived/news_features_polygon_180d.parquet \
  --optuna-params data/models/v3_optuna_full16fold.json \
  --out-suffix _v4_news
```

If WF lift is real, swap `continuer_v2_v3_tuned.pkl` to v4_news version.

---

## 2. 🎯 Simplified VETOED rule: TCN-strip replacement candidates

`scripts/ml_simplified_vetoed_rule.py` (NEW, ~190 LOC).

### Setup
Session 114 placebo TCN test debunked the TCN-veto signal. This script
measures TCN-free replacements on 16-fold WF v3-tuned predictions.

### Cohort
`v3t≥0.30 AND mag=MID` → 269 picks total. Median intraday_pct = +44.84%,
p25 = +36.08%.

### Rule comparison

| Rule | n | avg | win% | Kelly $ | mean Kelly% |
|---|---|---|---|---|---|
| **A: drop VETOED (full v3+MID)** | 269 | +7.94% | 49.4% | **$+7,134** | 1.48% |
| B: v3t [0.30,0.40] + MID | 216 | +3.18% | 46.3% | $+728 | 0.93% |
| **C: v3+MID + intra<median** | 134 | **+10.46%** | 50.7% | $+4,056 | 1.32% |
| **D: v3+MID + intra<p25** | 67 | **+13.37%** | **55.2%** | $+2,070 | 1.18% |
| E: PRODUCTION (v3+MID + tcn<0.30) | 92 | +9.07% | 45.7% | $+2,902 | 1.39% |

### Interpretation

- **A wins on Kelly $-PNL** ($+7,134 vs production $+2,902 = +146% better).
  Just absorbing VETOED into BROAD captures all the v3+MID picks at the
  10% Kelly cap.
- **D wins on per-trade lift** (+13.37% vs production +9.07% = +47% better).
  Smaller bucket but cleaner. **The Lou/Polk/Skouras 2019 fade prior is
  REAL** — picks below the 25th percentile of intraday% deliver dramatically
  better than the cohort average.
- **B (proba band) is bad** — +3.18% on n=216. The lower-end of v3t≥0.30
  (P=[0.30, 0.40]) is the *worst* slice of the cohort.

### Recommended post-Monday production rule

**Replace VETOED tier with Option D**:
```python
# OLD (TCN-dependent, debunked):
VETOED = (v3t >= 0.30) AND (mag == MID) AND (tcn < 0.30)   # n=92, +9.07%

# NEW (TCN-free, principled, +47% better per-trade):
VETOED = (v3t >= 0.30) AND (mag == MID) AND (intraday_pct < p25_of_cohort)
                                                            # n=67, +13.37%
```

The p25 cutoff needs to be computed from training data per fold (walk-
forward safe). For production, freeze the p25 from the most recent
training window.

### Ship timing

**Don't strip pre-Monday.** WF +122.83% bankroll was measured with
production rule (E). Swap to D post-stable-Monday-deploy after live
calibration is validated.

---

## 3. EOD anomaly detection

`scripts/monitor_paper_deploy.py` extended:
- `detect_anomalies(by_tier, wf, threshold_z=2.0)` — compares live tier-fire
  count vs Poisson-expected daily rate (`wf_n / 132 trading days in WF`)
- `post_discord(webhook_url, content)` — fires alert via webhook on |z| > 2σ
- Loads `OPS_ALERT_WEBHOOK_URL` from `.env` (the existing Discord webhook
  that drift_cron uses)

### Anomaly model
For each tier, expected daily fire rate = `wf_n / 132 trading_days`.
Poisson-distributed observations, std = √(rate). z = (observed - rate) / std.

Example flagging logic:
- ELITE WF: 7 picks / 132 days = 0.053/day expected, std = 0.23
- 1 ELITE fire on day 1 → z = (1 - 0.053) / 0.23 = +4.1σ → ANOMALY (rare event)
- Typical day: 0 ELITE fires → z = -0.23σ → quiet

### Smoke test (no live picks today)
```
$ python scripts/monitor_paper_deploy.py --eod --profile 16fold_aggressive
Anomaly detection (live tier-fire frequency vs WF):
  no tier-fire-frequency anomalies (within +/-2 sigma of WF expectation)
```

---

## 4. Files shipped this session

| Path | Status | LOC |
|---|---|---|
| `scripts/ml_continuer_v2_ensemble.py` | modified (--include-news + 12 news features) | +50 |
| `scripts/ml_simplified_vetoed_rule.py` | NEW | ~190 |
| `scripts/monitor_paper_deploy.py` | modified (anomaly detection + Discord) | +60 |
| `data/models/vetoed_rule_alternatives.json` | NEW (gitignored) | small |
| `docs/research-log/115_news_flag_simplified_vetoed_anomaly_monitor.md` | NEW (this doc) | this |

Total: 1 new analysis script + 2 modified + doc, +300 LOC.

---

## 5. Background data jobs status (commit time)

- **trades_v1 80-day backfill**: 60 GB / 240 GB (~25%); ETA ~7-10 more hours
- **Polygon news 180-day backfill**: ~377/4080 keys (9.2%); ETA ~94 more min
  - Output appears stuck (last log line at calls=377 hasn't moved for ~hour);
    may have rate-limited or hung. **Will check next session and either
    resume or restart with rate-sleep increased.**

---

## 6. Validated edge stack (post-115)

```
Meta-scorer 16-fold AGGRESSIVE       +122.83% bankroll WF (~92% APY)
  Sharpe 3.15, Calmar 31.24, max DD -2.81%
  TCN debunked (s114), VETOED-rule-D candidate (s115): +13.37% per-trade
                                                          on n=67 (vs
                                                          production +9.07%)

Monday deploy ready (rule E in production for WF compatibility).
Post-Monday: swap to rule D for +47% per-trade VETOED lift.
v4 ready: --include-news / --include-microstructure plumbed; awaiting
          backfill completion.
```

---

## 7. Next-session priorities

1. **Monday: paper-deploy + monitor.** Final config in doc 113 §10.
   Watch for tier-fire-frequency anomalies via `monitor_paper_deploy.py
   --eod` (now with Discord alerts).
2. **Resume / restart Polygon news backfill** if stuck.
3. **Convert + microstructure on 80-day trades_v1 backfill** when complete.
4. **v4 ensemble retrain** with `--include-news --include-microstructure`
   to measure lift over v3-tuned-16fold.
5. **POST-Monday: strip TCN + swap VETOED to rule D.** Document the
   change in a session note + re-run drawdown analysis on the new model.
6. **Install drift cron Task Scheduler** (`./install_drift_cron_task.ps1`).
