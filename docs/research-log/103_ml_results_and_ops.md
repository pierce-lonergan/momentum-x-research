# 103 — ML Results, Splits Backfill, Operational Deployment

**Date:** 2026-05-02
**Headline:** XGBoost + walk-forward + conformal beats the manual chronic-fader
gate by **+5.28pp per trade** (ML +4.05% vs baseline −1.23%, Sharpe 2.91).
Splits backfill complete (2,914 splits, 8,410 dividends). Three scheduled
tasks live for Monday: main bot 04:30 (halted), lottery 09:00, fader-short 15:50.

---

## §1 — ML continuer model (XGBoost + WF + conformal)

### §1.1 — Setup

- **Data**: 20,029 clean catalog rows, 2024-01 → 2026-04
- **Features (16, all walk-forward-safe)**:
  log_open, log_dvol_d0, intraday_pct, intraday_pct_log, ret_open_close_d0,
  close_strength, prior_n, prior_n_log, prior_cont_rate, prior_fade_rate,
  prior_avg_t5, prior_7d_count, dow, month, year, day_of_year
- **Targets**: binary continuer (ret_t5 ≥ +10%) + regression on ret_t5
- **Model**: XGBoost classifier + regressor (300 trees, depth 4, lr 0.05)
- **Conformal**: split conformal at α=0.10, threshold from calibration set
- **Walk-forward CV**: rolling 365d train, 30d test, 16 folds

### §1.2 — Headline results (16-fold walk-forward)

| Strategy | n | weighted_avg | weighted_win | sharpe~ |
|---|---|---|---|---|
| BASELINE (V3-WF chronic-fader gate) | 1,395 | **−1.23%** | 41.7% | −0.66 |
| **ML threshold P≥0.30** | **1,109** | **+4.05%** | **46.3%** | **+2.91** |
| ML threshold P≥0.50 | 84 | **+18.58%** | 58.3% | +2.33 |

**ML beats the manual gate by 5.28pp per trade with comparable trade count.**

The ML model selects ~80% as many trades as the baseline gate, but those
trades average +4.05% vs baseline's −1.23%. Net: same opportunity volume,
much better filtering.

### §1.3 — Per-fold breakdown (13 of 16 folds positive for ML30)

| Fold | Test start | n | Base avg | ML30 avg | ML50 avg |
|---|---|---|---|---|---|
| 0 | 2025-01-15 | 724 | +0.6% | −4.1% | +23.9% |
| 1 | 2025-02-14 | 660 | −4.3% | +2.3% | +5.3% |
| 2 | 2025-03-17 | 952 | −1.4% | +10.8% | +61.1% |
| 3 | 2025-04-15 | 892 | +1.1% | +10.4% | +34.5% |
| 4 | 2025-05-15 | 799 | −3.9% | +7.4% | +13.8% |
| 5 | 2025-06-16 | 782 | +3.2% | +4.4% | +66.1% |
| 6 | 2025-07-14 | 812 | −4.4% | +4.4% | +19.4% |
| 7 | 2025-08-13 | 856 | +1.5% | +9.2% | +15.4% |
| 8 | 2025-09-12 | 972 | +3.8% | +7.3% | +22.8% |
| **9** | **2025-10-13** | 873 | **−5.9%** | **−3.8%** | **−2.4%** |
| 10 | 2025-11-11 | 707 | −2.1% | +2.4% | +5.4% |
| 11 | 2025-12-11 | 583 | +0.8% | +2.5% | +15.7% |
| 12 | 2026-01-12 | 819 | −3.7% | +7.6% | (n=0) |
| 13 | 2026-02-09 | 708 | −2.5% | +15.5% | −6.2% |
| 14 | 2026-03-11 | 637 | −1.7% | +4.4% | −16.6% |
| 15 | 2026-04-10 | 416 | +6.4% | +2.1% | +30.1% |

ML30 was **positive in 13 of 16 folds (81%)** vs baseline 6 of 16 (37%).
Only folds 0 (Jan 2025) and 9 (Oct 2025) were negative for ML30.

### §1.4 — Feature importance (gain-based, final fold)

| Rank | Feature | Importance |
|------|---------|-----------|
| 1 | prior_7d_count | 0.100 |
| 2 | prior_avg_t5 | 0.075 |
| 3 | year | 0.071 |
| 4 | day_of_year | 0.069 |
| 5 | prior_fade_rate | 0.065 |
| 6 | log_dvol_d0 | 0.063 |
| 7 | log_open | 0.061 |
| 8 | intraday_pct_log | 0.061 |
| 9 | close_strength | 0.061 |
| 10 | prior_n_log | 0.060 |
| 11 | ret_open_close_d0 | 0.058 |
| 12 | prior_cont_rate | 0.058 |
| 13 | prior_n | 0.052 |
| 14 | intraday_pct | 0.052 |
| 15 | month | 0.048 |
| 16 | dow | 0.047 |

**Top driver: prior_7d_count.** Recurring pumpers in the last week are
HIGHLY informative. **Year and day_of_year matter** — there's temporal
structure (regime / seasonality) the model is exploiting.

The walk-forward priors (prior_avg_t5, prior_fade_rate, prior_cont_rate)
collectively dominate.

### §1.5 — Why ML beats the manual gate

The manual V3-WF gate is a 4-rule AND:
- (rate ≥ 5% OR no history)
- prior_7d_count ≤ 1
- dvol_d0 in [$100k, $100M]
- intraday_pct in [30%, 100%]

XGBoost auto-discovers nonlinear AND-ed splits like:
- "low log_dvol AND high prior_fade_rate AND prior_n > 5 → fade"
- "moderate intra AND fresh AND late-year (year=2025) → continuer"

Manual gates are coarse; tree splits are fine-grained.

---

## §2 — Position sizer (Kelly + conformal width)

`scripts/ml_position_sizer.py` provides:

```python
size_long(p_continuer, conformal_width, equity) → SizerOutput
size_short(p_continuer, conformal_width, equity) → SizerOutput  # uses 1-p
```

Kelly fraction with `expected_win_pct=0.30`, `expected_loss_pct=0.15`
(matches lottery's trail), capped at **2% of equity per trade**, fractional
Kelly = 0.25.

Width modulation: tight CIs (width < 0.20) get full size; loose CIs
(width > 0.80) refuse trade.

CLI test:
```
size_long(P=0.45, width=0.4, equity=$140K) → $500 (capped)
size_long(P=0.20, width=0.4, equity=$140K) → $0 (P below threshold)
size_short(P=0.05, width=0.3, equity=$140K) → $500 (high fade conviction)
```

---

## §3 — Splits + dividends backfill (rigorous CA flag)

`scripts/polygon_splits_backfill.py` ran in background, completed in
537s (~9 min):
- **2,914 split records** across 3,501 catalog tickers
- **8,410 dividend records**
- Cross-referenced: **3,304 of 20,886 catalog rows (15.82%)** sit within
  ±5d/-90d of a reverse split

| Bucket | n | avg_intra | avg_t5 | max_intra |
|---|---|---|---|---|
| clean (no reverse split nearby) | 17,582 | +66.2% | **−1.4%** | +4,492% |
| reverse_split_window | 3,304 | +78.3% | **+5.5%** | +3,690% |

**Counterintuitive:** rows in a reverse-split window have HIGHER avg t5
(+5.5% vs −1.4%). But the max_intra column shows extreme outliers (one
3,690% row likely an unadjusted price) skewing the mean.

The flag is now in `data/polygon_warehouse/derived/aftermath_catalog_clean.parquet`
for use by future analyses. Rigorous filter ready.

---

## §4 — Lottery runner: ML gate wired in

`scripts/lottery_runner.py` now has 4 gating modes (env-controlled):

| Env var | Effect |
|---|---|
| `LOTTERY_USE_POLYGON_SCREENER=1` | Add Polygon snapshot to Alpaca screener |
| `LOTTERY_USE_CONTINUER_PRIOR=1` | Simple chronic-fader gate (rate < 3% → out) |
| `LOTTERY_USE_ML_MODEL=1` | **ML continuer model gate (P >= threshold → in)** |
| `LOTTERY_ML_P_THRESHOLD=0.30` | Threshold for ML gate |
| `LOTTERY_USE_POLYGON_NEWS_GATE=1` | News catalyst gate (currently OFF) |

The ML gate SUPERSEDES the simple continuer-prior gate when both are on.
Failure-safe: if model load fails, falls back to no ML gate.

**13/13 lottery unit tests still pass** with ML code added (Polygon and
ML disabled in test mocks).

---

## §5 — Scheduled tasks now live (4 total)

```
MomentumX-PaperTrading       state=Ready  next-run=5/3 04:30 AM ET (HALTED via env var)
MomentumX-Lottery            state=Ready  next-run=5/3 09:00 AM ET (long lottery)
MomentumX-FaderShort         state=Ready  next-run=5/2 15:50 PM ET (short, S3 default)
MomentumX-Watchdog           state=Ready  next-run=5/2 12:38 PM ET (existing)
```

All non-elevated, daily, weekend-skip in launcher scripts.

---

## §6 — Monday's recommended config

```powershell
# === MAIN BOT (stay halted) ===
[Environment]::SetEnvironmentVariable("MOMENTUM_HALT_NEW_ENTRIES", "1", "User")

# === LOTTERY LONG (09:00 ET) ===
[Environment]::SetEnvironmentVariable("LOTTERY_USE_POLYGON_SCREENER", "1", "User")
# Choose ONE selection mode:
#   Conservative: simple chronic-fader gate
[Environment]::SetEnvironmentVariable("LOTTERY_USE_CONTINUER_PRIOR", "1", "User")
[Environment]::SetEnvironmentVariable("LOTTERY_CONTINUER_MIN_RATE", "0.03", "User")
[Environment]::SetEnvironmentVariable("LOTTERY_CONTINUER_MIN_APPEARANCES", "5", "User")
#   OR aggressive: ML model gate (preferred per §1)
# [Environment]::SetEnvironmentVariable("LOTTERY_USE_ML_MODEL", "1", "User")
# [Environment]::SetEnvironmentVariable("LOTTERY_ML_P_THRESHOLD", "0.30", "User")

# === FADER SHORT (15:50 ET) ===
# (no LOTTERY_USE flag needed - this is a different process)
# Defaults: variant=S3, notional=$250, max=5, target/stop=10%/10%
# To disable: [Environment]::SetEnvironmentVariable("MOMENTUM_FADER_SHORT_HALT", "1", "User")
```

---

## §7 — Expected daily P&L (Monday baseline)

| Strategy | Per-trade edge | Picks/day | Expected $ | Notes |
|---|---|---|---|---|
| Lottery (continuer-prior gate) | +1.4%/trade T+5 | 5 × $250 | **+$17.50** | Conservative |
| Lottery (ML model gate, P≥0.30) | **+4.05%/trade T+5** | 5 × $250 | **+$50.63** | If ML holds |
| Fader short (S3) | +5.55%/trade T+5 | 5 × $250 | **+$68.75** | Best edge |
| **Combined (conservative)** | | $2,500 deployed | **+$86.25/day** | doc 101 §4.4 |
| **Combined (with ML)** | | $2,500 deployed | **+$119.38/day** | this doc |

ML lift: +$33/day = **+38% improvement** over conservative.

---

## §8 — What I'd recommend on Monday morning

**Option A (conservative)**: enable continuer-prior gate only. Same as
doc 101 §4.4. Validated +1.4% edge, well-tested.

**Option B (aggressive)**: enable ML model gate. Walk-forward shows +4.05%
edge, Sharpe 2.91. **First live test of the model.** Recommended IF you
trust the walk-forward methodology.

The way I'd actually phase it:
- Mon-Tue: Option A (conservative). Verify lottery + fader-short coexist
  cleanly, no operational issues.
- Wed-Thu: Switch to Option B (ML). Compare to first 2 days.
- Fri: Decision — continue ML if it tracks the walk-forward, revert if not.

---

## §9 — What we DIDN'T finish (deferred to next session)

1. **Proper H1 backtest** (non-lookahead) — the previous attempt stalled
   on per-config queries. Need a refactored version.
2. **H3 short-side helper variant** — the PROMPT_10 §7.5 migration.
   Unblocks live H3 deployment (+1.6%/trade additional edge).
3. **Phase 3 trade tape pull** — selective S3 download for the 20K
   catalog rows (~5-10 GB net after filtering).
4. **Reverse-split fraud filter live cron** — schedule the splits backfill
   to run nightly so the flag stays current.
5. **A3 Stacked ensemble** (XGB + LGBM + CatBoost + logistic) — could
   push edge from +4.05% to +5-6% per doc 102 §4.2.
6. **A4 Bayesian baseline** (PyMC) — interpretable uncertainty.
7. **TCN on intraday paths** (Q4 + A5) — needs minute-bar feature pipeline
   and GPU.
8. **Q5 Sector dummies** — needs ticker_details enrichment.

---

## §10 — Files shipped this session

| Path | Purpose |
|---|---|
| `docs/research-log/102_ml_sophistication_brainstorm.md` | 13 questions, 12 architectures, 10 infra ranked |
| `docs/research-log/103_ml_results_and_ops.md` | This document |
| `scripts/ml_continuer_model.py` | XGBoost + WF + conformal + persistence |
| `scripts/ml_position_sizer.py` | Kelly + width-aware sizer (long + short) |
| `scripts/fader_short_launcher.ps1` | PowerShell launcher for daily 15:50 task |
| `scripts/polygon_splits_backfill.py` | (already shipped, ran in background) |
| `scripts/lottery_runner.py` | + `LOTTERY_USE_ML_MODEL` gate |
| `data/models/continuer_v1.pkl` | Trained classifier + regressor + conformal threshold |
| `data/models/continuer_v1_manifest.json` | Versioning + WF metrics + feature importance |
| `data/polygon_warehouse/derived/ml_walkforward_results.parquet` | 12,192 per-row predictions |
| `data/polygon_warehouse/derived/aftermath_catalog_clean.parquet` | + rigorous reverse-split flag |
| `data/polygon_warehouse/reference/splits.parquet` | 2,914 splits |
| `data/polygon_warehouse/reference/dividends.parquet` | 8,410 dividends |
| Scheduled task `MomentumX-FaderShort` | 15:50 ET daily |

---

## §11 — One paragraph synthesis

> "The ML pipeline outperforms the manual chronic-fader gate by 5.28pp
> per trade walk-forward (+4.05% vs −1.23%), 13 of 16 folds positive,
> Sharpe 2.91 annualized. The top features are walk-forward priors
> (prior_7d_count, prior_avg_t5, prior_fade_rate) plus temporal context
> (year, day_of_year) — meaning the model captures regime structure
> beyond what a static gate can. The full operational stack is now live
> for Monday: 4 scheduled tasks, 3 active strategies (lottery long,
> fader short, main bot halted), Kelly+conformal sizer ready, splits
> backfill complete with rigorous reverse-split flag. Combined expected
> daily P&L: +$86 conservative, +$119 with ML — that's +38% lift from
> the model. The walk-forward sample is 1,109 trades over 16 months;
> it's the largest validated edge we have. Phase Mon-Tue conservative,
> Wed-Fri ML, decide live."

---

## §12 — Three closing thoughts

1. **The ML gate works because it's a smarter version of what the
   manual gate was doing.** Same priors as features; tree splits find
   nonlinear AND combinations a binary rule can't. The gain isn't from
   exotic features — it's from letting the model find the THRESHOLDS.

2. **Walk-forward is the single most important integrity check.** In-sample
   numbers (V4 +14% becoming −5% out-of-sample, doc 100 §3.2) prove this.
   Every model from now on should be reported with its walk-forward number,
   not its in-sample number.

3. **The dual-side stack + ML lift puts us at ~+$120/day expected on $2,500
   deployed.** That's +0.085% of equity per day, ~21% annualized after
   compounding. For a paper experiment on a halted bot, that's a real
   number — verifiable Monday.
