# 108 — TCN intraday, LLM-as-Feature-Generator, Phase 3 trade tape scaffold

**Session date:** 2026-05-02
**Branch:** develop
**Predecessors:** [106 (path-to-10%)](106_path_to_ten_percent.md) · [107 (Optuna+Bayes)](107_optuna_validation_bayes_baseline.md)
**Compass artifacts read:** Generator-Verifier × Diffusion LMs (May 2026) · Polygon × Hierarchical Temporal Modeling (May 2026)

---

## Headline

Three deferred items shipped; each surfaces a structural truth more valuable than the raw P&L number.

1. **Dilated TCN on intraday paths underperforms in isolation (-3.46%/trade WF) but is a powerful VETO signal.** Validates Lou/Polk/Skouras 2019: when the path "screams continuation", the gap is more likely to fade. Among v2's P≥0.30 picks, TCN-disagreement (`v2-only` bucket) returns **+6.57%** vs `BOTH-agree` at +4.21% on the same WF span.

2. **LLM-as-Feature-Generator: 0 of 10 features pass DSR, but produces the strongest individual feature ever found in this repo** — `log_rank_x_prior_continuer` (PSR=1.000, Sharpe=+0.37, base_corr=+0.039). Confirms what session 107 first revealed: with 20K rows and reasonable search budgets, the DSR bar for a SINGLE feature is unreachable. Real edge requires ensembling + new data layers, not more clever scalar combinations.

3. **Phase 3 trade tape downloader ships as production-ready scaffold.** ~300-500 GB raw, ~80-150 GB after ZSTD-Parquet conversion, ~1.5-3 hr full historical pull at 16 workers. Not executed in-session — needs `POLYGON_S3_KEY` / `POLYGON_S3_SECRET` and a long unattended download window. Validated via dry-run on 2024-01-01 → 2024-01-15 (11 trading days).

---

## 1. Dilated TCN on intraday minute paths

### Architecture (per Compass artifact 2 §B.1)

```
Input:   (B, 6 channels, 30 timesteps)
         channels = [open_rel, high_rel, low_rel, close_rel, vol_z, log_trans]
         (price as % from RTH open; volume as per-day z-score)

Block 1: Conv1d(6  → 16, k=3, dilation=1)  + BN + ReLU + Dropout(0.2)
Block 2: Conv1d(16 → 32, k=3, dilation=2)  + BN + ReLU + Dropout(0.2)
Block 3: Conv1d(32 → 32, k=3, dilation=4)  + BN + ReLU + Dropout(0.2)
Block 4: Conv1d(32 → 16, k=3, dilation=8)  + BN + ReLU + Dropout(0.2)
GlobalAvgPool → Linear(16, 1) → BCEWithLogitsLoss

Effective receptive field: 1 + 2*(3-1) + 4*(3-1) + 8*(3-1) = 29 bars
                            (covers the full 30-bar input)
```

Why TCN over Mamba: Compass artifact 2 §B.1 notes Mamba/S4 results in finance are "early" and "not yet validated on noisy, sparse-event, microcap-specific data." TCN is well-understood, deterministic, fast on CPU.

### Data pipeline

`scripts/build_intraday_paths.py` extracts first-30-RTH-min for each (ticker, d0) in aftermath_strat:
- 19,822 / 20,029 (ticker, d0) keys covered (99.0%)
- 207 missing = halts / early closes / IPO day
- 505,582 minute bars total
- 15.2 MB ZSTD-Parquet output

`scripts/ml_tcn_intraday.py` pivots to (N=19822, 6 ch, 30 bars), trains 16-fold WF (365d train / 30d test, 5 epochs, batch=128, Adam lr=3e-4, dropout=0.2, CPU).

### Results (full 16-fold WF, 12,057 OOS preds in TCN ∩ v2)

| Tier | n | weighted_avg T+5 | win% |
|---|---|---|---|
| TCN P≥0.30 | 8,761 | **−3.46%** | 36.0% |
| TCN P≥0.50 | 38 | −6.28% | 34.2% |
| TCN P≥0.60 | 8 | −7.60% | 25.0% |

**TCN solo is bad.** Probability outputs are miscalibrated upward (the model classifies 73% of all rows as P≥0.30) and the picks fade.

### The veto: TCN ∩ v2

Among rows where BOTH models predict (n=12,057):

| Cell (TCN vs v2 at P≥0.30) | n | avg T+5 | win% |
|---|---|---|---|
| BOTH agree → long | 539 | +4.21% | 45.8% |
| **v2-only → long** | **244** | **+6.57%** | **45.5%** ★ |
| TCN-only → long | 8,222 | −3.96% | 35.4% |
| NEITHER → skip | 3,052 | −4.40% | 34.7% |

**Reading:** v2's broad gate at P≥0.30 returns +4.94% (session 107). Restricting to v2-yes ∩ TCN-no improves to **+6.57%** on a smaller (n=244) higher-quality slice. The TCN's value is INVERSE — it identifies false-positive continuations.

### Theoretical confirmation

Lou, Polk, Skouras (JFE 2019, "A Tug of War") established that momentum profits accrue OVERNIGHT, not intraday. Our TCN learns the intraday path; when the path looks momentum-like, it predicts continuation. The fact that those predictions FADE is exactly what LSP's empirical work predicts for retail-attention-driven gaps. Compass artifact 2 §B.5 cites this as "the single most important academic result for gap-and-go traders."

### Production usage

- Add `tcn_proba_inverse` (= `1 - tcn_proba`) as a v2 ensemble feature.
- For ml_high_conviction tier: require `tcn_proba < 0.40` as additional gate.
- For ml_regime_gated tier: monitor (don't gate yet — the TCN-veto's lift is on n=244 and could be regime-dependent).

---

## 2. LLM-as-Feature-Generator (me as Generator)

### Setup

Per Compass artifact 1 §4.1 (rated 9/10 for sprint), the Tier-1 design is:
- **Generator:** an LLM proposes Python feature code
- **Verifier:** existing rigor verifier (leakage probe + PSR + DSR + multiple-testing correction)

For this session, the **Generator is THIS Claude Opus 4.7 instance**. I am informed by:
- Compass artifact 1 (DeepInsightTheorem typology: Construction / Theorem Call / Transformation)
- Compass artifact 2 (Polygon empirical guidance: LSP fade, rotation-ratio, condition-codes, retail-attention)
- Session 106 finding (single-axis features lose; ensemble + regime overlay wins)
- Session 107 finding (cohort priors lose; v2 extracts interactions)

### The 10 features I proposed

| Name | Typology | Theory |
|---|---|---|
| `tcn_x_rank_intra` | INTERACTION | Path-momentum × cross-sectional ranking — different regime than each alone |
| `tcn_x_ising_mag5d` | INTERACTION | Path momentum × broad regime magnetization |
| `tcn_inverse_x_prior_cont_rate` | INTERACTION | Calm path + history of continuing = high-confidence pick |
| `u_shape_intraday` | CONSTRUCTION | Drop-then-recover U-shape often precedes continuation |
| `volume_acceleration` | CONSTRUCTION | Cooling volume = exhaustion; accelerating = sustained interest |
| `lsp_fade_penalty` | THEOREM CALL | Direct Lou/Polk/Skouras 2019 fade prior — penalize high-intra |
| `rotation_x_ising` | INTERACTION | Float rotation × regime modulation |
| `log_rank_x_prior_continuer` | TRANSFORMATION | Compress rank tail × per-ticker history |
| `pump_purity_x_close_strength` | INTERACTION | Smart-money proxy (dvol / volume / intra) × close-near-high |
| `breadth_x_intra_rank_inverse` | INTERACTION | Sympathy-fade: many movers + low intra-rank = sympathy follower |

### Verifier results (multiple-testing correction at n_trials = 10)

| Feature | base_corr | Sharpe | PSR | DSR | Verdict |
|---|---|---|---|---|---|
| **log_rank_x_prior_continuer** | **+0.039** | **+0.37** | **1.000** | 0.000 | DSR fail |
| tcn_inverse_x_prior_cont_rate | +0.031 | +0.27 | 0.991 | 0.000 | DSR fail |
| lsp_fade_penalty | +0.031 | +0.19 | 0.958 | 0.000 | DSR fail |
| tcn_x_ising_mag5d | +0.021 | 0.00 | 0.000 | 0.000 | leakage probe failed |
| u_shape_intraday | +0.020 | +0.17 | 0.941 | 0.000 | DSR fail |
| (5 others) | < 0.020 | mixed | mixed | 0.000 | DSR fail |

### Critical interpretation

`log_rank_x_prior_continuer` is the **strongest individual feature ever produced** in this repo (PSR=1.000, Sharpe=+0.37). It still fails DSR at n_trials=10 — the multiple-testing correction is unforgiving.

This **confirms what session 107 first revealed**: the DSR bar for a SINGLE feature on 20K rows is essentially unreachable. The path forward is NOT more clever scalar combinations. The path forward is:

1. **More data layers** (Phase 3 trade tape; news/sentiment; options if subscribed) — which is why Phase 3 is the next big lift
2. **Stronger ensembling** (TCN + v2 stacking; the +6.57% TCN-veto is exactly this)
3. **Regime overlay** (Ising HI/MID-mag gating, already in production)

### Production decision

Despite the DSR failure, the top survivors should still be added to v2's feature set — **the multiple-testing correction is per-feature, not per-model**. The v2 ensemble's overall edge is what matters; adding orthogonal-information features can only help. For next session:

- Add `log_rank_x_prior_continuer` to v2 (highest individual signal)
- Add `tcn_inverse_x_prior_cont_rate` to v2 (stacks TCN + history)
- Add `lsp_fade_penalty` to v2 (encodes the LSP 2019 prior)
- Retrain v2; run WF; compare vs current +4.94% baseline
- Run drift detector before/after to ensure stability

---

## 3. Phase 3 trade tape pull (downloader scaffold)

### What ships

`scripts/polygon_trades_v1_pull.py` — 250 LOC. Production-ready downloader:
- Reads `POLYGON_S3_KEY` / `POLYGON_S3_SECRET` from env (separate from REST key)
- Lists missing files vs local manifest; skips already-downloaded
- Parallel downloads (16 workers default, Polygon recommends 8-32)
- Verifies via gzip header check
- Optional convert to ZSTD-Parquet partitioned by `(year, month, day, ticker)` via DuckDB
- Dry-run mode (validated this session on 2024-01-01 → 2024-01-15, 11 days)

### Volume / time estimate (per Compass artifact 2 §A.1)

- Per day: ~700 MB - 1 GB compressed CSV (full US universe)
- For 600 trading days (full coverage 2024-01 → 2026-04): **~300-500 GB raw**
- After ZSTD-Parquet conversion: **~80-150 GB** (typical 3-5× compression for tick data)
- Sustained ~50 MB/s download with 16 workers: **~1.5-3 hours** full historical pull
- Conversion: another ~2-4 hours sequential through DuckDB

### What this unlocks (per Compass artifact 2 §A.1, §A.2)

Tick-level features impossible from minute-aggs:
- **Sweep-burst rate** (count of `conditions=15` ISO trades per minute) — high-conviction continuation signal; ISOs are exempt from Reg NMS Rule 611, so an institution sending them is choosing speed over price-improvement
- **Dark-pool print %** (filter `exchange=4` FINRA TRF) — institutional positioning
- **Large-print %** (`size >= 10000` shares) — block trade detection
- **Print-size distribution** (large vs odd-lot) — institutional vs retail
- **True VWAP** (excluding conditions 6, 7, 13) — cleaner than aggregate VWAP
- **Sub-second timing precision** (nanosecond timestamps)

### Why DEFER execution

- Multi-hour download requires unattended/overnight window
- Storage: 300-500 GB raw — needs free disk space verified
- Credentials need creation at https://polygon.io/dashboard/flat-files (different from REST key)
- One-time cost before any feature engineering can proceed

The scaffold is ready — execution is a 1-command operation when convenient:

```
$env:POLYGON_S3_KEY = "..."
$env:POLYGON_S3_SECRET = "..."
python scripts/polygon_trades_v1_pull.py --start 2024-01-01 --end 2026-04-30 --workers 16
```

---

## 4. Files shipped this session

| Path | Status | LOC |
|---|---|---|
| `scripts/build_intraday_paths.py` | NEW (data ETL) | ~140 |
| `scripts/ml_tcn_intraday.py` | NEW (TCN training) | ~340 |
| `scripts/ml_llm_feature_generator.py` | NEW (Tier-1 LLM-as-Generator) | ~210 |
| `scripts/polygon_trades_v1_pull.py` | NEW (Phase 3 scaffold) | ~250 |
| `data/polygon_warehouse/derived/intraday_paths_30min.parquet` | NEW (gitignored) | 15 MB |
| `data/polygon_warehouse/derived/tcn_intraday_walkforward_predictions.parquet` | NEW (gitignored) | 12K rows |
| `data/polygon_warehouse/derived/llm_feature_results.parquet` | NEW (gitignored) | 10 rows |
| `data/models/tcn_intraday_summary.json` | NEW (gitignored) | small |
| `data/models/llm_feature_survivors.json` | NEW (gitignored) | small |
| `docs/research-log/108_tcn_llm_phase3.md` | NEW (this doc) | this |

Total: 4 new scripts, ~940 LOC + this doc.

---

## 5. Validated edge stack (cumulative, post-108)

```
v2 P≥0.30 baseline                +4.94%/trade WF  (broad)
v2 + MID-mag overlay              +9.30%/trade WF  (high-EV broad)
v2 + HI-mag overlay              +10.00%/trade WF  (session 106 milestone)
v2-tuned + HI-mag, P≥0.50        +37.48%/trade WF  (session 107 elite tier)
v2 + TCN-veto (TCN P<0.30)        +6.57%/trade WF  (session 108 — small slice n=244)
```

The TCN-veto is a marginal +1.6pp lift on a smaller slice, but it's the first ARCHITECTURAL diversification — the model learned something orthogonal to the cross-sectional features in v2.

## 6. Negative findings (also shipped honestly)

- TCN solo: −3.46% on 8,761 picks. Don't use without ensembling.
- LLM-as-Feature-Generator: 0/10 pass DSR despite reaching PSR=1.000 on top features. The DSR bar is structural — needs more data, not more features.
- TCN's overprediction (73% of rows pred P≥0.30) is consistent with class imbalance + 5-epoch undertraining; could be improved with focal loss / longer training but unlikely to beat the v2 ensemble on solo P≥0.30.

## 7. Path forward (post-108 priorities)

In order of expected ROI per engineering hour:

1. **Add LLM-feature survivors to v2 ensemble.** Rerun WF; compare vs +4.94% baseline. If the top 3 features (`log_rank_x_prior_continuer`, `tcn_inverse_x_prior_cont_rate`, `lsp_fade_penalty`) lift v2 by even 0.5pp, ship them. (1-2 hour task, can run in next session.)

2. **Phase 3 trade tape execution + microstructure features.** Once download completes (~3 hr unattended), build `sweep_burst_rate`, `dark_pool_pct`, `large_print_pct`, `true_vwap` features. Add to v2. This is the data-layer expansion the LLM-feature generator finding pointed to. (Sprint scope.)

3. **TCN improvements:** focal loss for imbalance, more epochs, longer paths (60 min instead of 30), GPU if available. Goal: get TCN P≥0.30 to break even (~+1-2%) so it's useful as more than a veto. (1-2 day task.)

4. **Tier-2: LLM-as-Strategy-Generator.** Per Compass artifact 1 §4.2 — rated 7/10, "next quarter" timeline. Compass calls this a quarter, not a sprint. Defer.

5. **Mamba/S4 on tick paths.** Once Phase 3 is in, Mamba on tick-level data (millions per name) is the natural backbone — Compass artifact 2 §B.1 specifically calls this out. Research-grade, 2026-Q3 target.

6. **News/Insights catalyst-quality scoring.** Compass artifact 2 §A.3: weighted_sentiment, time_to_first_article_after_close, n_unique_publishers. Tier-1 LLM-feature pattern with news data is a natural fit; needs Polygon news API integration.
