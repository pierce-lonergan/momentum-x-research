# 113 — Drawdown analysis + Polygon credentials + Phase 3 launch + Monday preflight

**Session date:** 2026-05-02
**Branch:** develop → main (merged + pushed)
**Predecessor:** [112 aggressive Kelly + intraday + monitor](112_aggressive_kelly_intraday_live_monitor.md)
**Goal:** validate aggressive Kelly risk profile, activate Polygon S3, ship Monday preflight.

---

## TL;DR — six wins, one risk note

1. **Aggressive Kelly is mathematically clean.** Drawdown analysis shows
   max DD = -2.81% (recovered in 6 days), Calmar = 31.24,
   Sharpe = 3.15, CAGR = 87.89%. Aggressive nearly DOUBLES CAGR
   (vs conservative 48.62%) for only 1.56x the drawdown.

2. **Polygon credentials wired.** Single-day trades_v1 download tested
   end-to-end: 3.10 GB CSV → 2.64 GB ZSTD parquet via DuckDB. Endpoint
   updated to `files.massive.com` (post Polygon → Massive rebrand).

3. **8-day trades_v1 backfill running** (~24 GB raw). Will finish in
   ~30-60 min. Once converted, microstructure features unlock for the
   8 most-recent regime days, validating the v4 pipeline.

4. **Drift cron daemon ships and works.** `scripts/drift_cron.py` runs
   the existing detector + posts Discord alerts via `OPS_ALERT_WEBHOOK_URL`.
   Verified: alarm + ok messages both post correctly.

5. **News catalyst features ship.** `scripts/build_news_catalyst_features.py`
   uses Finnhub (~3000 tickers covered, sparse for microcaps). Smoke-tested
   on 50 keys in 140s. Coverage: mean 1.68 articles/24h per ticker.

6. **Monday preflight: ALL GREEN.** `scripts/monday_full_send_preflight.py`
   validates 8 deploy categories. Account equity $140,208. Lottery_runner
   DRY_RUN exits cleanly with all flags ON.

7. **Risk note (TCN)**: post-training temperature scaling reveals TCN
   AUC = 0.49 (essentially random). The veto signal is empirically
   discovered, not principled. WF lift is real but mechanism is unclear —
   future work needed.

---

## 1. Drawdown decomposition

`scripts/ml_drawdown_analysis.py` (NEW, ~220 LOC) — equity-curve drawdown,
Calmar, Sharpe, daily P&L, per-tier risk decomposition.

### Aggressive Kelly results ($10k bankroll, 16-fold WF)

| Metric | Aggressive (50/35/20/10%) | Conservative (5/3/2/1%) |
|---|---|---|
| Total return | +122.83% | +65.43% |
| **CAGR** | **+87.89%** | +48.62% |
| Annualized Sharpe | +3.15 | +3.36 |
| **Max drawdown** | **-2.81%** | -1.80% |
| DD recovery time | 6 days | 2 days |
| **Calmar ratio** | **31.24** | 27.07 |
| Worst pick | -$224.82 (-2.25%) | varies |
| Negative days | 40.9% | 41.7% |

### Key finding

Aggressive Kelly **improves Calmar** (31.24 vs 27.07) — we're still UNDER
full Kelly even with 50/35/20/10 caps. The conformal-width modulator
prevents over-leverage; mean ELITE Kelly stays at 9.30%, well below the 50%
cap.

### Per-tier risk

| Tier | n | mean $ | std $ | min $ (worst) | max $ (best) | % loss |
|---|---|---|---|---|---|---|
| ELITE | 7 | +$601.51 | $581.78 | -$67.46 | +$1,691.98 | 14.3% |
| HIGH | 27 | +$125.97 | $222.54 | -$224.82 | +$618.18 | 37.0% |
| VETOED | 53 | +$14.25 | $64.73 | -$82.61 | +$286.34 | 58.5% |
| BROAD | 238 | +$16.45 | $62.86 | -$102.29 | +$345.83 | 50.4% |

Worst single pick across all tiers: **-$224.82** (HIGH). Far from the
theoretical "ELITE @ 50% × -100% = -$5,000" doomsday case. Real-world
microcap intraday returns are bounded; the worst observed pick was -22%
position-level.

---

## 2. Polygon S3 credentials wired

### Setup
- `.env` updated with `POLYGON_API_KEY`, `POLYGON_S3_KEY`,
  `POLYGON_S3_SECRET`, `POLYGON_S3_ENDPOINT=https://files.massive.com`
- `scripts/polygon_trades_v1_pull.py` updated:
  - `_load_env_dotfile()` auto-loads .env without shell exports
  - S3 endpoint env-overridable (defaults to massive.com post-rebrand)
  - DuckDB convert: bumped to 12GB memory + temp_directory + threads=8 +
    `preserve_insertion_order=false`
  - Better error messages (clip to 300 chars instead of 80)

### Single-day end-to-end validation

```
$ python scripts/polygon_trades_v1_pull.py --start 2026-04-29 --end 2026-04-29
S3 endpoint: https://files.massive.com/flatfiles/us_stocks_sip/trades_v1/
expected raw download volume: ~1 GB (1 days x ~850 MB)
downloaded=1, skipped=0, errors=0, total=3.10 GB
                ★ Actual is 3.6x Compass artifact estimate; modern volume

$ python scripts/polygon_trades_v1_pull.py --start 2026-04-29 --end 2026-04-29 --convert-only
converted=1, errors=0, total parquet=2.64 GB
                ★ ZSTD compression only 15% — trade IDs/timestamps mostly random
```

### 8-day backfill in flight

`2026-04-15 → 2026-04-24` (8 trading days × ~3 GB ≈ 24 GB):
- Workers: 8 parallel boto3
- Status at doc-time: 13 GB (~54%) downloaded, 8 in-progress multipart files
- ETA: ~30-60 min

Once complete, run `scripts/build_microstructure_features.py` to produce
sweep-burst rate, dark-pool %, large-print %, true-VWAP per (ticker, d0)
for the 8-day window. Then v4 retrain (`--include-microstructure` flag —
next session).

---

## 3. Microstructure feature builder bug fix

Found bug in `build_microstructure_features.py`:
```sql
-- WRONG (minute < 0 is impossible):
WHERE EXTRACT(hour FROM ts_et) = 9 AND EXTRACT(minute FROM ts_et) >= 30
   OR (EXTRACT(hour FROM ts_et) = 10 AND EXTRACT(minute FROM ts_et) < 0)

-- FIXED:
WHERE (EXTRACT(hour FROM ts_et) = 9 AND EXTRACT(minute FROM ts_et) >= 30)
   OR (EXTRACT(hour FROM ts_et) = 10 AND EXTRACT(minute FROM ts_et) = 0)
```

Also added explicit parens around the OR for clarity.

---

## 4. News catalyst features

`scripts/build_news_catalyst_features.py` (NEW, ~270 LOC).
Per-(ticker, d0) features from Finnhub `/api/v1/company-news`:

| Feature | Source |
|---|---|
| `n_articles_24h` | count in (d0 - 24h, d0 09:30 ET) |
| `n_unique_publishers` | distinct sources (1 source × 50 syndications ≠ 50 catalysts) |
| `pct_positive` / `pct_negative` | sentiment from headline keyword scan |
| `weighted_sentiment` | publisher-tier weighted (Compass §A.3 schema) |
| `hours_to_first_article` | time gap from market close to first article |
| `has_ratings_change` | upgrade/downgrade keyword in headline |
| `has_official_filing` | 8-K / earnings / FDA / press release keyword |

Publisher weights (Compass artifact 2 §A.3):
- BusinessWire / PR Newswire / GlobeNewswire = 1.0 (primary source)
- Reuters / Bloomberg / AP = 0.95
- Benzinga / WSJ = 0.7
- MarketWatch / CNBC / Barrons = 0.6
- Yahoo / SeekingAlpha / Motley Fool = 0.2-0.4 (opinion)

### Smoke test (50 keys)

```
done: 50 API calls, 50 (ticker, d0) features in 140.1s
mean n_articles_24h:    1.68
mean n_unique_publishers: 0.76
mean weighted_sentiment: 0.02 (~ neutral)
has_official_filing:    4% of rows
```

**Sparse coverage** for microcaps — Finnhub free tier covers ~3000 large-cap
tickers. Most microcap gap-ups have 0 articles. Production deployment
should switch to Polygon `/v3/reference/news` (broader coverage, requires
the now-active POLYGON_API_KEY).

---

## 5. TCN temperature scaling — negative finding

`scripts/ml_tcn_temperature_scaling.py` (NEW, ~150 LOC).
Post-training Platt-style scaling (Guo et al. 2017). Fits scalar T such
that `calibrated = sigmoid(logit / T)`.

### Result

```
AUC (ROC):     0.4899   ← below 0.50! TCN is essentially random discriminator
ECE pre:       0.1273
ECE post:      0.2074   (WORSE)
Brier pre:     0.1844
Brier post:    0.2095   (WORSE)
Optimal T:     0.001    (extreme; collapses probas to 0/1)
```

### What this means

The BCE-trained TCN has **AUC 0.49** — it has no directional discriminative
power on continuer prediction. The "veto signal" we documented in
sessions 108-112 (+6.57% on `v2-only` bucket n=244) is **empirically real
but not driven by the TCN's ML signal** — it correlates with v2's mistakes
for unknown reasons.

### Production decision

- Production stays on **original BCE TCN** for the VETOED tier — the WF lift
  was measured directly, so the rule still works empirically.
- **Future work**: validate VETOED-tier rule with a placebo TCN (random
  predictions); if VETOED still produces lift, the path features in v3-tuned
  are doing the work, not the TCN.
- Calibrated parquet saved at
  `data/polygon_warehouse/derived/tcn_intraday_walkforward_predictions_calibrated.parquet`
  for archival.

---

## 6. Drift cron daemon

`scripts/drift_cron.py` (NEW, ~120 LOC).

### Pipeline
1. Loads `.env` (auto-discovers `OPS_ALERT_WEBHOOK_URL`)
2. Subprocess-runs `scripts/ml_drift_detector.py`
3. Reads today's `data/models/drift_report_<YYYY-MM-DD>.json`
4. If `n_drift_alerts > 0`: posts Discord embed alarm
5. Else: posts compact "Drift OK" message
6. Exit code 0 = clean, 1 = alarm

### Smoke test

```
$ python scripts/drift_cron.py
ALARM at trade 370 (max_PH=51.23)
1 drift alert(s):
  - Page-Hinkley alarm at idx 370
posted ALARM to Discord
```

(The Page-Hinkley alarm is the same one from session 106 — known stale
concept-drift baseline. The cron pipeline itself is functional.)

### Scheduling (Windows / Linux)

```powershell
# Windows Task Scheduler
schtasks /create /tn "MX Drift Cron" /tr "powershell -File scripts\drift_cron.ps1" /sc daily /st 08:00
```

```bash
# Linux/Mac cron
0 8 * * 1-5 cd /repo && python scripts/drift_cron.py >> logs/drift_cron.log
```

---

## 7. Monday full-send preflight

`scripts/monday_full_send_preflight.py` (NEW, ~250 LOC).
Eight-step deploy validator:

| Step | Check | Result |
|---|---|---|
| 1 | Credentials (Alpaca, Polygon, Finnhub, Discord) | [OK] all 8 |
| 2 | Production model artifacts on disk | [OK] all 4 |
| 3 | MetaScorer.load_default succeeds | [OK] 54 features, mag MID |
| 4 | Aggressive Kelly profile honored (50/35/20/10) | [OK] all 4 caps |
| 5 | Alpaca paper API reachable | **[OK] equity = $140,208.04** |
| 6 | Drift report fresh (today) | [OK] 1 known alarm |
| 7 | lottery_runner DRY_RUN exits cleanly | **[OK] rc=0** |
| 8 | WF P&L expectation | $+12,283 (+122.83%) Aggressive Kelly |

**Result: ALL GREEN** (1 warning = the known Page-Hinkley alarm; not blocking).

---

## 8. Files shipped this session

| Path | Status | LOC |
|---|---|---|
| `scripts/ml_drawdown_analysis.py` | NEW | ~220 |
| `scripts/ml_tcn_temperature_scaling.py` | NEW | ~150 |
| `scripts/build_news_catalyst_features.py` | NEW | ~270 |
| `scripts/drift_cron.py` | NEW | ~120 |
| `scripts/monday_full_send_preflight.py` | NEW | ~250 |
| `scripts/polygon_trades_v1_pull.py` | modified (env auto-load + endpoint + 12GB mem) | +30 |
| `scripts/build_microstructure_features.py` | modified (SQL bug fix) | +1 |
| `.env` | modified (Polygon credentials) | +6 |
| `docs/research-log/113_drawdown_creds_phase3_preflight.md` | NEW (this doc) | this |

Total: 5 new scripts + 3 modified, +1040 LOC.

---

## 9. Validated edge stack (post-113, unchanged from 112)

```
v3-tuned-16fold + (HI|MID) P≥0.60 +58.79%/trade WF   (s109/s111 — n=7)
Meta-scorer Kelly conservative    +65.43% bankroll  (~49% APY) (s109/s111)
Meta-scorer Kelly AGGRESSIVE     +122.83% bankroll  (~92% APY) (s112) ★
  Sharpe 3.15, Calmar 31.24, max DD -2.81%, recovered in 6 days  (s113)
+ live broker bar-fetch active    READY for VETOED upgrades      (s112)
+ Polygon trades_v1 download      8 days in flight                (s113)
+ Drift cron + Discord alerts     READY                          (s113)
+ Monday preflight                ALL GREEN, $140K equity         (s113)
```

---

## 10. Monday paper-deploy command (final)

```powershell
# === Required (from .env via daily_paper_trade.ps1, or set manually) ===
$env:ALPACA_API_KEY = "PK_REDACTED_ROTATED_2026-07-29"
$env:ALPACA_SECRET_KEY = "<from .env>"
$env:ALPACA_BASE_URL = "https://paper-api.alpaca.markets"
$env:ALPACA_DATA_FEED = "sip"

# === Meta-scorer + aggressive Kelly ===
$env:LOTTERY_USE_META_SCORER = "1"
$env:LOTTERY_META_BANKROLL_USD = "10000"
$env:LOTTERY_AGGRESSIVE_KELLY = "1"

# === Intraday VETOED-tier refresh at 10:00 ET ===
$env:LOTTERY_USE_INTRADAY_REFRESH = "1"

# === Suppress legacy gates ===
$env:LOTTERY_USE_ML_MODEL = "0"
$env:LOTTERY_USE_ISING_GATE = "0"

# === Run main ===
python scripts/lottery_runner.py

# === In separate terminals: ===
python scripts/monitor_paper_deploy.py --tail
# After EOD:
python scripts/monitor_paper_deploy.py --eod --profile 16fold_aggressive
```

---

## 11. Post-doc: trades_v1 conversion + microstructure features

After doc was first written, in same session:

- **trades_v1 conversion completed** (3 retries needed for case-collision
  bug — found `BCPC` and `BCpC` ticker variants colliding on Windows
  case-insensitive filesystem). Fix: `UPPER(ticker)` before PARTITION_BY.
  Final: 9 days × ~12,200 ticker partitions = 16.27 GB ZSTD parquet.
- **Microstructure features built** for the 9-day window: 191
  (ticker, d0) rows = ~1.0% of the 20,029 aftermath keys.
- Sample feature variances:
  - `dark_pool_pct_first30` ranges 0-95% (TRT=24%, CUE=95%, MXL=64%)
    — strong real signal
  - `odd_lot_pct_first30` 55-100% — confirms microcap retail-heavy
  - `sweep_burst_count_first30` mostly 0 — ISO trades rare on microcaps
    (only 6 ISOs all day on ticker A); validates academic literature
    that ISO is a large-cap institutional mechanic
  - `large_print_pct_first30` mostly 0 — microcaps don't get block trades
- **v3 ensemble extended** with `--include-microstructure` flag:
  features go 54 → 65 (adds 11 microstructure features). Plumbing
  validated.

## 12. Realistic v4 timeline

To actually MEASURE v4 lift, we need trades_v1 coverage for >50% of
aftermath keys (~10K rows × 9 features = need 8+ months of trades_v1).
At current observed 3 GB/day × 22 trading days/month × 8 months = ~530 GB
raw + ~330 GB parquet. That's a 2-3 day download + conversion job.

Not blocking Monday deployment. Schedule the long backfill for next week.

## 13. Next-session priorities

1. **Monday: paper-deploy + monitor.** Full-send config in §10. Watch
   per-tier fires, intraday-refresh upgrades, P&L vs WF expectation.
2. **Long trades_v1 backfill** (~530 GB raw, ~3 days unattended) to enable
   real v4 retrain.
3. **Switch news features to Polygon `/v3/reference/news`** for broader
   microcap coverage (now that POLYGON_API_KEY is active).
4. **Schedule drift_cron via Windows Task Scheduler** (08:00 ET daily).
5. **Validate VETOED-tier rule with placebo TCN** to test the empirical
   veto hypothesis.
