# 134 — v6 Phase 1: Microstructure Feature Pack — Built, Tested, Backfilling

**Session date:** 2026-05-07
**Branch:** develop
**Predecessors:** [133 v6 Phase 0 validation results](133_v6_phase0_validation_results.md)
**Status:** Module + tests SHIPPED; full backfill IN PROGRESS; validation NOT YET RUN

---

## TL;DR

Phase 1 ships a 6-feature microstructure pack computed per `(ticker, d0)`
from `data/polygon_warehouse/trades_v1_parquet`. Features are based on
the M.md research priority list; expected lift per that doc is +0.05–0.10
neutralized Spearman.

**Critical hygiene rule from Phase 0:** these features are NOT wired
into production. They will only ship if the v3+v6_pack model passes the
full Phase 0 validation (CPCV stability + DSR with explicit N_trials +
Numerai feature neutralization).

| Feature | Source | Expected role |
|---|---|---|
| `vpin_d0` | Easley-LdP-O'Hara RFS 2012 | Informed-trader concentration on d0 |
| `ofi_first30_d0` | Cont-Kukanov-Stoikov JFE 2014 (simplified) | Opening-window directional aggression |
| `kyle_lambda_d0` | Kyle 1985 | Liquidity fragility (price-impact-per-dvol) |
| `hawkes_fano_d0` | Bauwens-Hautsch 2009 (proxy via Fano factor) | Trade-arrival burstiness |
| `amihud_illiq_d0` | Amihud 2002 | Cross-section-stable illiquidity proxy |
| `iso_sweep_count_d0` | Polygon condition code 14 | Aggressive liquidity-taker count (also FIXES the v2 builder bug that used code 15) |

---

## 1. Files this commit

| Path | Status | Notes |
|---|---|---|
| `scripts/v6_microstructure_pack.py` | NEW (~280 LOC) | Pure-pandas/numpy feature functions |
| `tests/unit/test_v6_microstructure_pack.py` | NEW (24 tests, all pass) | Structural-invariant pins |
| `scripts/build_v6_microstructure_pack.py` | NEW (~170 LOC) | Per-key builder; `--limit`, `--month`, `--out` |
| `data/polygon_warehouse/derived/microstructure_v6_pack.parquet` | NEW (gitignored) | Backfill output (in progress) |
| `docs/research-log/134_v6_phase1_microstructure_pack.md` | NEW (this doc) | |

The compute layer and the builder are SEPARATE files so the feature
functions can be unit-tested in isolation without warehouse access.

---

## 2. Why these 6 features (per M.md research)

The M.md research thesis is that v3/v4 have **architectural ceiling
exhausted** — adding a 6th transformer head, an 8th specialist tier, or
a CORN cumulative-product output won't move neutralized Spearman past
0.10. The unexploited information is in DATA we haven't featurized yet.

The Polygon `trades_v1_parquet` warehouse (425 GB after backfill) is the
biggest untapped data source we have. M.md ranks microstructure
features as the highest-ROI Phase 1 build.

### 2.1 VPIN (Volume-Synchronized PIN)

**Source:** Easley, López de Prado, O'Hara, *Flow toxicity and liquidity
in a high-frequency world*, RFS 2012.

**What it captures:** order-flow toxicity. When informed traders cluster,
they sign trades all in one direction; VPIN measures the imbalance
within equal-volume buckets across a day.

**Method:**
```
sign each trade via Lee-Ready tick rule (no NBBO available)
form 50 equal-volume buckets across RTH
per bucket: imb = |B - S| / (B + S)
VPIN = mean(imb) across buckets ∈ [0, 1]
```

**Why it should matter for microcap gap-ups:** the d0 pre-print regime
should look more "informed" (lower volume, directional positioning by
people who saw the catalyst early) than the post-news public-trading
regime. VPIN should be strongly bimodal across our keys.

### 2.2 OFI_first30 (Order Flow Imbalance, opening 30 min)

**Source:** Cont, Kukanov, Stoikov, *The price impact of order book events*,
JFE 2014. Multi-Level OFI (MLOFI) requires Level 2 quote data, which
the Polygon warehouse does not include — so we use trade-only signed
volume as a downgraded proxy.

**Method:**
```
signed_vol = tick_rule_sign × size
OFI_first30 = (B - S) / (B + S) over [09:30, 10:00) ET
```

**Why opening 30 min specifically:** the v3 model already has a
`prior_avg_t5` feature for the previous-day return, but it has no
intraday opening-window features. The first 30 min on a gap-up day is
where the news shock manifests.

### 2.3 Kyle's λ

**Source:** Kyle, *Continuous auctions and insider trading*, Econometrica
1985. Modern empirical form: regress |bar return| on |bar signed dollar
volume| in 5-min bars; slope is λ.

**Method:**
```
aggregate trades to 5-min bars (RTH only)
per bar: abs_ret = |log(close/open)|; abs_dvol = |Σ sign·price·size|
λ = OLS slope of abs_ret on abs_dvol (clipped to ≥ 0)
```

**Why it matters:** higher λ means smaller orders move the price more
— fragile liquidity. On gap-up microcaps with thin books, λ can vary 4
orders of magnitude. The hypothesis: continuers have *lower* λ at the
window we trade (deep enough liquidity for our orders to land); faders
have higher λ.

### 2.4 Hawkes-proxy Fano factor

**Source:** Bauwens-Hautsch, *Modelling financial high frequency data
using point processes*, 2009. Proper Hawkes parameter estimation
requires fitting a self-exciting point process; the Fano factor is a
moments-based proxy that captures the same clustering signal at 0.1%
of the compute cost.

**Method:**
```
bucket RTH trade timestamps into 60-second windows
F = var(count_per_window) / mean(count_per_window)
F = 1: Poisson; F > 1: clustered; F < 1: regular
```

**Why it matters:** clustered arrivals on news-driven microcaps signal
the *bursty* regime our continuer thesis depends on. We expect F ≫ 1
for clean continuers and F ≈ 1–3 for choppy faders.

### 2.5 Amihud illiquidity

**Source:** Amihud, *Illiquidity and stock returns*, JFM 2002.

**Method:**
```
aggregate trades to 5-min bars
per bar: illiq = |log_return| / dollar_volume
Amihud_d0 = mean(illiq) × 1e6  (scale for tabular input)
```

**Why both Kyle and Amihud?** Kyle's λ is the OLS slope; Amihud is the
arithmetic mean of the ratio. They correlate but disagree in the tails
(Kyle weights high-dvol bars more; Amihud weights all bars equally).
Including both lets the model learn the discrepancy.

### 2.6 ISO sweep count (the bug fix bonus)

The existing `microstructure_features.parquet` has `sweep_burst_count_*`
and `iso_to_dark_ratio` columns that are **identically zero** for all
3,841 rows. Root cause: `build_microstructure_features_v2.py` checks
condition code `'15'`, but Polygon's Intermarket Sweep Order is code
**`'14'`**. Code `'15'` is *Average Price Trade*, which essentially
never fires intraday for momentum names.

The new pack uses code 14 and validates with a dedicated unit test.

---

## 3. Hygiene constraints (carrying forward from Phase 0)

Per [doc 133 § 7](133_v6_phase0_validation_results.md), no v6 feature
ships to production until it survives all three Phase 0 gates. For
Phase 1 specifically:

1. **Numerai-style neutralization:** train a model on
   `v3_features ⊕ v6_pack`; project predictions onto the residual space
   orthogonal to `(log_market_cap, log_dvol_d0, prior_avg_t5,
   intraday_pct, 8 sector dummies)`; report 100%-neutralized Spearman.
   Target: > 0.076 (the v3 baseline) by a meaningful margin.

2. **Deflated Sharpe Ratio:** if we tune any thresholds during the v6
   evaluation, the N_trials count must propagate into DSR. Target:
   DSR > 0.5 with N=actual_trials_we_ran.

3. **CPCV stability:** the per-day Sharpe across 200 random 4-of-16 fold
   subsets must have **frac > 0 ≥ 90%** (v3 hits 100%; v4 cascade only
   hit 61%). This is the strictest gate.

If a feature fails any of the three, it stays out of production. The
neutralized-Spearman gate is the most bypassable for "directional"
features (Kyle's λ should add cross-sectional info even if it
correlates with sector); the CPCV-stability gate is the hardest.

---

## 4. Expected backfill output

Aftermath universe: 20,029 (ticker, d0) keys spanning 2024-01 → 2026-04.
Trades_v1 warehouse window: 2025-08 → 2026-04. Expected coverage:
~6,000–7,000 keys (the 2025-08 onward subset).

Per-key compute cost:
- Read 100KB–35MB parquet: ~50ms–500ms
- Compute pack: ~50ms (mostly Python overhead on small DataFrames)
- Total: ~100ms–600ms per key

Full backfill at average 250ms/key × 7000 keys ≈ 30 min.

---

## 5. What's NOT in this commit (deferred to Phase 1.5)

| Feature | Reason for deferral |
|---|---|
| **Multi-Level OFI (MLOFI proper)** | Needs Polygon `quotes_v1` NBBO; not in our warehouse. Trade-only OFI is the substitute. |
| **Hawkes parameter fit (μ, α, β)** | Full self-exciting point process estimation is 100× compute of Fano factor. Will revisit if Fano shows lift. |
| **Print-tape forensics v2** | The v2 builder has buggy sweep but functional dark-pool/large-print fields. Kept untouched. |
| **WorldQuant 101 alphas** | Different research stream; pure cross-sectional ranking from OHLCV. Phase 1.5 candidate. |
| **News/catalyst embeddings (FinBERT)** | Phase 2 per the v6 roadmap. |

---

## 6. Plan for Phase 1 validation (after backfill)

```
1. Join v6_pack to v3 feature parquet on (ticker, d0)
2. Train a v3+v6 XGBoost (single config — no grid) on the standard
   16-fold WF setup
3. Compute v3-only baseline Spearman + neutralized Spearman
4. Compute v3+v6 Spearman + neutralized Spearman
5. Compute DSR for both with N_trials=1
6. Compute CPCV-approximation Sharpe distribution for both
7. Decision rule:
   - If neutralized ρ improves by ≥ 0.01 AND
     CPCV frac>0 stays ≥ 90% AND
     DSR ≥ 0.5
   - Then ship v6_pack as additional features in v3 retraining loop
   - Otherwise, document the failure in 135 and decide which features
     to drop
```

**Critical:** no per-feature ablation (would induce 6-trial multiple-
testing penalty in DSR). Either the pack ships together or none of it
does.

---

## 7. Why this is the right Phase 1

The v5 Tier-S architectural enhancements (CORN + soft-rank + Muon +
Mixup) FAILED to improve over v4. The Phase 0 validation showed v4's
apparent lift was a multiple-testing artifact, not real edge. The
pattern is consistent: **architectural complexity doesn't help when the
underlying input features are already saturated.**

The microstructure pack adds ORTHOGONAL information: trade-flow
toxicity, opening-window directionality, liquidity fragility, arrival
clustering, illiquidity. These are signals the v3 features
(`prior_avg_t5`, `intraday_pct`, sector dummies, market cap) cannot
express because they have no notion of intraday tape behavior.

If this fails too — the conclusion isn't "transformers are wrong" or
"microstructure is wrong" — it's that we've reached the genuine ceiling
for this universe and need to either change universe (e.g., add larger
caps for cross-sectional context) or change horizon (e.g., trade ret_t1
instead of ret_t5).

---

## 8. References

- Easley, López de Prado, O'Hara (2012), *Flow toxicity and liquidity*, RFS
- Cont, Kukanov, Stoikov (2014), *Price impact of order book events*, JFE
- Kyle (1985), *Continuous auctions and insider trading*, Econometrica
- Kolm, Turiel, Westray (2023), *Deep order flow imbalance*, Math.Finance
- Bauwens, Hautsch (2009), *Modelling high frequency data using point processes*
- Amihud (2002), *Illiquidity and stock returns*, JFM
- Lee, Ready (1991), *Inferring trade direction from intraday data*, JF
- Holden, Jacobsen (2014), *Liquidity measurement problems in fast, competitive markets*, JF
