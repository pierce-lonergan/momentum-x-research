# 114 — Polygon news + Placebo TCN debunk + Long trades_v1 backfill + Drift cron task

**Session date:** 2026-05-02
**Branch:** develop → main (merged + pushed)
**Predecessor:** [113b drawdown + Polygon S3 + microstructure end-to-end](113_drawdown_creds_phase3_preflight.md)

---

## TL;DR — three production wins, one major debunk

1. **Polygon news features ship** (`scripts/build_news_features_polygon.py`,
   ~280 LOC). Replaces the Finnhub-based scaffold from s113 with broader
   coverage. Required two fixes: `/v2/reference/news` endpoint (not v3) +
   `Authorization: Bearer` header (not `apiKey=` query param). 180-day
   backfill running in background.

2. **🚨 Placebo TCN test DEBUNKS the TCN-veto signal.** 20-seed Monte
   Carlo with random uniform TCN predictions → mean VETOED-tier P&L
   **+8.13% ± 2.35%** (n≈80.7). Production TCN: +9.07% (n=92).
   **z = +0.40σ — within noise.** The +12.74% VETOED lift in s109 was an
   artifact of the v3+MID-mag interaction; the TCN's `<0.30` filter is
   essentially randomly subsampling a known-good cohort. **TCN is doing
   nothing useful — can be removed from production for compute savings.**

3. **Long trades_v1 backfill kicked off** (2026-01-26 → 2026-04-14,
   ~80 trading days, ~240 GB raw). Running in background. Will finish in
   ~10-15 hours unattended; enables real v4 microstructure ensemble
   retrain next session.

4. **Drift cron Task Scheduler scripts ship** (`scripts/drift_cron.ps1` +
   `scripts/install_drift_cron_task.ps1`). One command (`.\install_*.ps1`)
   creates a Windows Task Scheduler entry: weekdays at 08:00 ET, runs as
   current user (no UAC), 30-min timeout, with Discord alerts on drift.

---

## 1. Polygon news features (production-ready)

### Setup
- `POLYGON_API_KEY` from .env (the user's `cz_*` key)
- Endpoint: `https://api.polygon.io/v2/reference/news`
  - **NOT** `/v3/reference/news` — that returns 404 on this account
- Auth: `Authorization: Bearer <key>` header
  - **NOT** `apiKey=<key>` query param (also returns 404)
- Time filter `?published_utc.gte=...&published_utc.lte=...` works correctly

### Per-(ticker, d0) features (window: d0-24h to d0 09:30 ET)

| Feature | Source |
|---|---|
| `n_articles_24h` | raw count |
| `n_unique_publishers` | distinct sources (1 source × 50 syndications ≠ 50 catalysts) |
| `pct_positive` / `pct_negative` / `pct_neutral` | Polygon Insights labels |
| `weighted_sentiment` | sum over (publisher_tier × insight_score) |
| `weighted_sentiment_norm` | normalized version |
| `hours_to_first_article` | time gap from market close to first article |
| `co_mention_count_avg` | avg # other tickers tagged in articles |
| `has_official_filing` | 8-K / earnings / FDA / press release in title |
| `has_ratings_change` | upgrade/downgrade in title |
| `insights_coverage_pct` | fraction of articles with Insights field |

### Coverage validation (100-key sample, last 30 days)

```
done: 100 API calls, 5/100 keys with news (5.0%)
n_articles_24h        mean=0.05  std=0.22  max=1
weighted_sentiment    mean=0.03  std=0.22  range=[-1.0, +1.0]
insights_coverage_pct mean=0.05  std=0.22  max=1.0
```

**5% coverage is consistent with literature** — most microcap gap-ups
are NOT news-driven (they're momentum / squeeze / promotion plays). The
5% that DO have news will likely have stronger sentiment signal — that's
the v4 hypothesis to test.

### 180-day backfill running in background
Will produce ~3,000-5,000 (ticker, d0) feature rows by completion. Once
complete, ready to wire into v3 ensemble via a `--include-news` flag
(next session).

---

## 2. Placebo TCN — major negative finding

### Hypothesis
Session 113 found TCN AUC = 0.49 (essentially random). Session 109's
VETOED-tier rule (`v3t≥0.30 + MID-mag + tcn<0.30`) showed +12.74% on n=73.
Three possibilities:
- **A**: TCN is noisy but real (correlates with v2's mistakes)
- **B**: VETOED lift is statistical fluke (TCN-`<0.30` filter just samples random subset)
- **C**: VETOED lift is REAL but driven by v3 path features, not TCN

### Method
Replace TCN predictions with uniform[0,1] random noise. Re-apply VETOED
rule. Compare P&L vs production TCN. Run 20 seeds for statistical
robustness.

### Result

| Bucket | Production TCN | Placebo (mean ± std, 20 seeds) | z-score |
|---|---|---|---|
| **VETOED** (`v3t≥0.30 + MID + tcn<0.30`) | +9.07% (n=92) | **+8.13% ± 2.35%** (n≈80.7) | **+0.40σ** |
| `BOTH_MID` (`v3t≥0.30 + MID + tcn≥0.30`) | +7.35% (n=177) | +7.85% ± 0.98% (n≈188.3) | -0.51σ |
| BROAD (`v3t≥0.30 + (HI|MID)`) | +9.58% (n=485) | +9.58% ± 0.00% (n=485) | -1.00σ |

### Verdict

**TCN signal is INDISTINGUISHABLE from random noise** in the production
VETOED rule. The +12.74% historical lift was option **B**: TCN-`<0.30`
filter randomly subsamples the known-good v3+MID-mag cohort. Production
VETOED tier P&L is real but comes from `v3+MID-mag`, not the TCN.

### Production decisions

1. **VETOED tier still works in WF** — the slice has +9.07% (production)
   and +8.13% (placebo mean) — both real lift relative to SKIP's -4.04%.
   Keep the rule but rename to reflect what it actually does.

2. **TCN model is doing nothing useful.** Remove from production for:
   - ~50 MB disk (`tcn_intraday.pt`)
   - Inference latency (per-pick TCN forward pass)
   - Code complexity (`build_path_from_alpaca_bars` + intraday path
     fetching at 10:00 ET)

3. **Intraday refresh is still valuable** — but for a different reason
   than thought. It catches v3+MID-mag picks that didn't fire at 9:30
   (because v3t was just below 0.30). Re-evaluating with NO TCN at 10:00
   ET could still trigger entries.

### Don't simplify yet (Monday is in 2 days)

For Monday's deploy: KEEP the existing TCN-based VETOED rule. The
production WF P&L was measured with this rule, so the +122.83% bankroll
expectation assumes it. Replacing pre-deploy adds risk without time to
re-validate. Strip TCN POST-Monday, after the live deploy is stable.

---

## 3. Long trades_v1 backfill

Launched: `python scripts/polygon_trades_v1_pull.py --start 2026-01-26
--end 2026-04-14 --workers 12 --no-convert`

- 80 trading days × ~3 GB/day ≈ **240 GB raw** download
- ETA: 10-15 hours unattended at 12 parallel workers
- Combined with the 9 days from s113 → ~89 days total = 4 months coverage

After completion (next session):
1. Convert to ZSTD parquet (~30-45 min per day → ~30 hours total; or
   parallelize 4-way)
2. Build microstructure features → ~5,000-7,000 (ticker, d0) rows
   covering ~30-40% of aftermath_strat
3. Run v3 ensemble with `--include-microstructure` → measure v4 lift

If v4 lifts broad-tier WF by even 0.5pp, the meta-scorer aggressive Kelly
bankroll could go from $12,283 (+122.83%) to $13,500-14,500 range.

---

## 4. Drift cron Task Scheduler integration

`scripts/drift_cron.ps1` (~30 LOC) — wrapper PS1 invoked by Task Scheduler:
- Activates venv if present
- Runs `python scripts/drift_cron.py`
- Tees output to `logs/drift_cron_<YYYYMMDD>.log`
- Sets exit code based on drift detection (0=clean, 1=alarm)

`scripts/install_drift_cron_task.ps1` (~50 LOC) — one-shot installer:
- Creates "MX Drift Cron" scheduled task
- Trigger: weekdays at 08:00 ET local
- Runs as current user (no UAC, no admin needed)
- 30-min execution time limit
- Network-required (uses Polygon API)
- StartWhenAvailable + battery-friendly

### Install (one command)

```powershell
.\scripts\install_drift_cron_task.ps1
```

### Verify / test

```powershell
Get-ScheduledTask -TaskName "MX Drift Cron"
Start-ScheduledTask -TaskName "MX Drift Cron"   # manual fire
```

### Remove

```powershell
Unregister-ScheduledTask -TaskName "MX Drift Cron" -Confirm:$false
```

---

## 5. Files shipped this session

| Path | Status | LOC |
|---|---|---|
| `scripts/build_news_features_polygon.py` | NEW | ~280 |
| `scripts/ml_placebo_tcn_test.py` | NEW | ~200 |
| `scripts/drift_cron.ps1` | NEW | ~30 |
| `scripts/install_drift_cron_task.ps1` | NEW | ~50 |
| `docs/research-log/114_polygon_news_placebo_tcn_long_backfill.md` | NEW (this doc) | this |

Total: 4 new scripts + doc, +560 LOC.

---

## 6. Validated edge stack (post-114)

```
Meta-scorer 16-fold AGGRESSIVE       +122.83% bankroll WF (~92% APY)
  Drawdown profile (s113):
    Sharpe 3.15, Calmar 31.24, max DD -2.81%, recovered 6 days
  TCN signal (s114): DEBUNKED — random TCN gives same lift
                                  TCN can be removed for compute savings
                                  VETOED tier P&L is from v3+MID-mag overlay

Production model: continuer_v2_v3_tuned.pkl (16-fold Optuna params)
                  tcn_intraday.pt (BCE-trained; doing nothing useful but
                                    keeping for Monday deploy continuity)

Background data jobs:
  trades_v1 80-day backfill        running ~10-15 hr unattended
  Polygon news 180-day backfill    running few hours
  Drift cron Task Scheduler        ready to install
```

---

## 7. Next-session priorities (post-114)

1. **Monday: paper-deploy + monitor.** Final config in doc 113 §10; the
   only change post-114 is awareness that TCN is irrelevant (don't worry
   about TCN failures).
2. **Convert + microstructure on the 80-day backfill** when complete.
3. **v4 ensemble retrain** with `--include-microstructure --include-news`
   to measure lift over v3-tuned-16fold.
4. **Strip TCN from production** post-stable-Monday-deploy. Either:
   - A) Replace VETOED rule with `v3t≥0.30 + MID-mag` (no TCN dependency)
   - B) Keep rule but stop training/loading TCN model (rule still works
     by random subsampling)
5. **Install drift cron** (`./install_drift_cron_task.ps1`).
6. **News-feature ablation:** retrain v3 with `--include-news`; if lift
   is real, add to v4. The 5% coverage might be enough if the 5% has
   strong directional signal.

---

## 8. Key insight from this session

The TCN debunk is the second major "we built something that wasn't needed"
finding (first was Bayesian baseline being negative, s107). Both are
**positive findings** for the discipline of the system: the rigor verifier
caught a real-world model that fooled us into thinking it was contributing.

The production lift survives the debunk because the WF P&L was measured
with the existing (TCN-using) rules. The aggressive Kelly +122.83%
bankroll prediction is intact — we just understand it better now.
