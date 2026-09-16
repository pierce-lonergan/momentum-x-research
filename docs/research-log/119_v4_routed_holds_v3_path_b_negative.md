# 119 — Path B v4-routed: HOLD v3 (third consecutive v4 negative finding)

**Session date:** 2026-05-02 → 2026-05-03 (UTC roll mid-session)
**Branch:** develop → main (merged + pushed)
**Predecessor:** [118 data-availability ablation + Path B plan](118_data_availability_ablation_path_b.md)

---

## TL;DR — third v4 attempt fails; production stays on v3-tuned-16fold

1. **trades_v1 80-day download DONE** (64 days, 185 GB raw, 115 GB parquet
   after Feb-Apr conversion).

2. **Microstructure features rebuilt at full coverage:** **1,345 rows
   spanning 63 trading days / 699 unique tickers / 6.7% of aftermath**
   (up from 543 / 2.7%).

3. **🚨 Path B v4-routed: HOLD v3 with -18.12pp regression.**
   - v4-routed (trained on 1,345 micro=1 rows, 30d/7d WF, 9 folds):
     **-3.49%** on 199 P≥0.30 picks
   - v3-tuned-16fold (on the SAME rows): **+14.63%** on 68 P≥0.30 picks
   - v4 lost despite specialization for the recent regime

4. **Three consecutive v4 attempts have all lost:**
   - s117: v4 with all rows + sparse features = **-$4,232 regression**
   - s118: v3 appeared to underperform by -7.80pp on micro=1 (BIASED by
     small 543-row sample; with 1,345 rows v3 actually OUTPERFORMS)
   - s119: v4 trained ONLY on micro=1 rows = **-18.12pp regression**

5. **Lottery launcher Monday-ready.** All env defaults set in
   `lottery_paper_trade.ps1`: meta-scorer=1, aggressive Kelly=1, intraday
   refresh=10:00 ET. Task Scheduler will use these automatically.

6. **Monday preflight: ALL GREEN.** Account equity $140,208. Drift cron
   refreshed for 2026-05-03 UTC date.

---

## 1. Microstructure rebuild at full coverage

`scripts/build_microstructure_features_v2.py` (NEW, ~140 LOC).

### Performance fix vs v1

The single-shot SQL in v1 became intractable at 64-day scale (~115 GB
parquet) — DuckDB's hive partition pruning broke down with
`CAST(t.ts_et AS DATE) = k.d0` join. v1 ran for 30+ min producing no output.

v2 strategy: process ONE (year, month) at a time:
- Filter aftermath keys to month
- Read only that month's parquet partitions (DuckDB hive prune by year/month)
- Run aggregation on smaller chunk
- Concat all monthly results

### Result
```
(2026, 1):  129 rows in   92.7s (of 781 keys)  17% match (Jan 26-30 only)
(2026, 2):  415 rows in  572.7s (of 656 keys)  63% match (Feb 2-27)
(2026, 3):  397 rows in 1045.1s (of 739 keys)  54% match (Mar)
(2026, 4):  404 rows in  905.8s (of 608 keys)  66% match (Apr 1-29)

total: 1,345 rows / 63 dates / 699 tickers / 6.7% of aftermath
```

### Bug fix: Python stdout buffering
v2 builder hung silently for 30+ min on first attempt. Root cause: Python
`print()` is fully-buffered when stdout isn't a TTY. Fixed by running
with `python -u scripts/build_microstructure_features_v2.py`.

---

## 2. Path B v4-routed: third negative finding

`scripts/ml_v4_routed.py` (s119, ~220 LOC). Per doc 118 plan: train v4
ONLY on rows where has_microstructure=1. Avoids s117's dilution pathology.

### WF setup
- Universe: 1,345 micro=1 rows (vs 20,029 full)
- Features: 77 (54 v3 + 11 microstructure + 12 news)
- WF window: 30d train / 7d test, min_train_size=100
- Folds: 9 (vs the 16 v3-tuned uses on full data)

### Result

| Metric | v4-routed | v3-tuned-16fold (same rows) | Δ |
|---|---|---|---|
| P≥0.30 n | 199 | 68 | +131 (v4 picks more) |
| P≥0.30 avg | **-3.49%** ❌ | **+14.63%** | **-18.12pp** |
| P≥0.50 n | 25 | (n/a in v3 same-row eval) | — |
| P≥0.50 avg | -0.17% | — | — |

**Verdict: HOLD v3 single model.**

### Why v4-routed lost

1. **Smaller training data** (1,345 rows / 9 folds) → mean training set
   per fold = ~150-450 rows. Tree ensembles overfit aggressively at this
   scale.
2. **Feature space too rich relative to data** — 77 features vs ~150-450
   training rows = 1:2 to 1:6 feature-to-row ratio. Should be 1:20+ for
   ensembles.
3. **Picks more aggressively** (199 vs v3's 68 at P≥0.30) — model is
   overconfident on small-sample patterns.
4. **Each fold trains on a DIFFERENT recent week's regime** — even within
   the routed subset, sub-regime variance is high.

### Why s118 ablation was biased

Doc 118 claimed v3 was -1.62% on micro=1 rows. That was based on the
**old 543-row microstructure** (s113b + Jan-Feb conversion). The 21-day
window was small and concentrated on Jan 26-Feb 10 + April 15-29 — a
specific volatile slice. With the **new 1,345 rows spanning 63 days**,
v3 actually performs **+14.63%** on the broader micro=1 universe.

The s118 finding was a small-sample fluke. v3 generalizes well to
microstructure-covered rows when the sample is big enough.

---

## 3. Three negative findings, one production decision

| Session | Attempt | Result |
|---|---|---|
| s117 | v4 with all rows + sparse features | **-$4,232** bankroll regression |
| s118 | (apparent) v3 underperforms on micro=1 | **-7.80pp** ← biased by small sample |
| s119 | v4 specialized on micro=1 only | **-18.12pp** regression on routed slice |

**The pattern is clear:** v3-tuned-16fold (trained on full 20,029 rows
across 16 monthly folds) extracts a calibrated, regime-spanning signal
that no specialization or feature-augmentation has improved.

**Production decision: STAY ON v3-tuned-16fold** for Monday and beyond
unless and until (a) microstructure coverage hits 25%+ AND (b) news
coverage with sentiment hits 15%+ AND (c) a SHIP verdict is produced
by the retrain-and-compare pipeline.

---

## 4. Files shipped this session

| Path | Status | LOC |
|---|---|---|
| `scripts/build_microstructure_features_v2.py` | NEW (per-month chunked) | ~140 |
| `scripts/ml_v4_routed.py` | NEW (Path B prototype) | ~220 |
| `scripts/lottery_paper_trade.ps1` | modified (Monday env defaults) | +37 |
| `data/polygon_warehouse/derived/microstructure_features.parquet` | rebuilt (gitignored) | 1,345 rows |
| `data/polygon_warehouse/trades_v1_parquet/year=2026/month={02,03,04}/` | NEW (gitignored) | 43 days |
| `data/models/v4_routed_summary_v4_routed.json` | NEW (gitignored) | small |
| `docs/research-log/119_v4_routed_holds_v3_path_b_negative.md` | NEW (this doc) | this |

---

## 5. Validated edge stack (post-119, unchanged)

```
Meta-scorer 16-fold AGGRESSIVE       +122.83% bankroll WF (~92% APY)
  Sharpe 3.15, Calmar 31.24, max DD -2.81%

THREE v4 attempts attempted, all failed:
  s117: v4 + sparse features (all rows)  -> -$4,232 regression
  s119: v4 specialized on micro=1 only   -> -18.12pp regression

PATH FORWARD: v3-tuned-16fold remains production. v4 deferred until:
  - microstructure coverage > 25%
  - news coverage > 15% with-news
  - retrain-and-compare verdict produces SHIP
```

---

## 6. Background jobs status (commit time)

- ✅ trades_v1 80-day download: DONE (64 days, 185 GB raw)
- ✅ Feb-Apr conversion: DONE (43 days, 0 errors)
- ✅ Microstructure v2 rebuild: DONE (1,345 rows / 6.7% coverage)
- ✅ News v2 backfill: DONE (4,080 rows / 4.66% with news)
- ✅ Path B v4-routed: complete (verdict: HOLD)
- ✅ Lottery launcher: Monday-ready
- ✅ Monday preflight: ALL GREEN

**All Monday deploy gates are green. Production is v3-tuned-16fold.**

---

## 7. Next-session priorities (post-Monday)

1. **Monday: paper-deploy + monitor.** v3-tuned-16fold + aggressive Kelly.
   Use `monitor_paper_deploy.py --eod` for anomaly detection.
2. **Strip TCN from production** (post-stable Monday): swap VETOED rule
   to `v3+MID+intra<p25` per s115 ablation (+13.37% per-trade vs +9.07%).
3. **Continue trades_v1 backfill** to 6+ months / 25%+ coverage; THEN
   retry v4 with retrain-and-compare verdict gate.
4. **Install drift cron Task Scheduler** (one-shot; per s114 doc).
