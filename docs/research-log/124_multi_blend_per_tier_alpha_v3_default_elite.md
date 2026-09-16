# 124 — Multi-blend HOLD + Per-tier alpha NEUTRAL + v3-default ELITE bonus

**Session date:** 2026-05-04 (Monday late EOD)
**Branch:** develop → main (merged + pushed)
**Predecessor:** [123 feature-coverage + dvol fix + temperature scaling](123_feature_coverage_test_dvol_fix_temperature_scaling.md)

---

## TL;DR — two architectural experiments, one surprise bonus

1. **Multi-v3 blend experiment: HOLD baseline.** Blending mean/median/
   trimmed across 4 v3 variants UNDERPERFORMS by $-3,360 to $-3,831.
   T-scaled (v3_temp) was the best single model at $30,111 vs baseline
   $29,719 (+$392 / +1.3%). Already shipped in s123.

2. **Per-tier alpha conformal: NEUTRAL** ($+0.21 delta). Tighter alpha for
   ELITE/HIGH didn't fire because tier sample sizes are small (7, 27);
   custom widths for VETOED/BROAD pushed the modulator to exp(-2) ≈ 0.135
   (blocks all picks).

3. **🎯 BONUS finding: v3 default (no Optuna) ELITE tier = $11,054** vs
   v3_tuned_16f's $9,666 = **+$1,388 (+14.4%) on the ELITE tier alone**.
   Optuna's regularization may over-tune away from the rare-event ELITE
   picks. Worth investigating: hybrid model-selection (use v3 default
   for ELITE, v3_tuned_16f for HIGH/VETOED/BROAD).

---

## 1. Multi-v3 blend experiment

`scripts/ml_v3_multi_blend.py` (NEW, ~245 LOC).

### Setup
4 existing v3-flavor models joined on (d0, ticker), 12,192 OOS rows:
- `v3` — default (no Optuna)
- `v3_tuned_6f` — 6-fold Optuna tuning (s107)
- `v3_tuned_16f` — 16-fold Optuna tuning (s109; PRODUCTION)
- `v3_temp` — v3_tuned_16f + post-training T-scaling (s123)

3 blend strategies:
- mean
- median (robust to outliers)
- trimmed (drop high+low, average middle)

### Per-tier $ P&L (Aggressive Kelly, $10k bankroll)

| Variant | ELITE $ | HIGH $ | VETOED $ | BROAD $ | TOTAL $ |
|---|---|---|---|---|---|
| v3 | **+11,054** ★ | +7,454 | +7,077 | +3,108 | +28,694 |
| v3_tuned_6f | +9,334 | +3,286 | +10,149 | +3,900 | +26,670 |
| **v3_tuned_16f** ★ | +9,666 | +7,680 | +8,160 | +4,213 | **+29,719** (baseline) |
| v3_temp | +9,597 | +7,653 | +8,502 | +4,360 | **+30,111** (s123 ship) |
| blend_mean | +6,813 | +7,501 | +7,169 | +4,404 | +25,888 |
| blend_median | +6,990 | +6,754 | +7,913 | +4,702 | +26,359 |
| blend_trimmed | +6,990 | +6,754 | +7,913 | +4,702 | +26,359 |

### Verdict: HOLD baseline; v3_temp is the working architectural lift

- All blends regress -$3,360 to -$3,831 vs baseline
- Blending averages out the strongest individual model's signal
- v3_temp (s123 T-scaling) at +$392 is the only architectural lift that survived

### Bonus: v3 default ELITE is unexpectedly strong

ELITE-tier $-PNL by variant:
- v3 default: **$11,054** ← winner
- v3_tuned_16f (production): $9,666
- v3_tuned_6f: $9,334
- v3_temp: $9,597

The Optuna-tuned models all UNDERPERFORM v3 default on the ELITE tier
specifically. Hypothesis: Optuna's hyperparameter search optimizes for
the broader objective (P≥0.30 weighted avg) and over-regularizes away
from the rare 7 ELITE picks per WF.

**Future work:** hybrid model-selection — use v3 default for ELITE
predictions (P≥0.60 cohort), v3_tuned_16f for HIGH/VETOED/BROAD.
Potential lift: +$1,388 on ELITE tier alone = ~+11% bankroll improvement.

---

## 2. Per-tier alpha conformal calibration

`scripts/ml_per_tier_alpha_conformal.py` (NEW, ~170 LOC).

### Setup
Different conformal alpha per tier (smaller alpha = tighter CI):
- ELITE   alpha=0.05 (tightest)
- HIGH    alpha=0.10 (production default)
- VETOED  alpha=0.15
- BROAD   alpha=0.20 (loosest)

Refit conformal_threshold per-tier using only that tier's calibration
rows; recompute conformal_width modulator.

### Result

| Tier | n | mean width | $ P&L | Δ $ |
|---|---|---|---|---|
| ELITE | 7 | 0.965 | +$4,218 | $+0 (too few rows; baseline kept) |
| HIGH | 27 | 1.000 | +$2,825 | $+0 (too few rows; baseline kept) |
| VETOED | 254 | 0.999 | +$3,002 | +$0.21 |
| BROAD | 200 | 1.000 | +$1,550 | $+0 |

### Verdict: NEUTRAL ($+0.21 total)

### Why it failed
- ELITE/HIGH have <30 rows → not enough samples for tier-specific calibration
- VETOED/BROAD calibrated thresholds (0.66, 0.64) push widths to 1.0
- Width=1.0 → modulator = exp(-2) ≈ 0.135 → blocks all picks
- Net: same as baseline

### Future work
- Use TRAINING-set conformal calibration per tier (not OOS); would have
  more samples
- OR use cross-tier shrinkage: blend per-tier alpha with global alpha

---

## 3. Production decision

**v3_tuned_16f remains baseline. v3_temp is the active architectural lift
(+$392). All other architectural experiments NEUTRAL or HOLD.**

Single env-flag improvements (all shipping):
- `MX_VETOED_RULE=D` (s120) +47% per-trade VETOED
- `MX_V3_TEMPERATURE=1.0275` (s123) -45% ECE / +2.9% lift
- 24-feature ticker_details enrichment (s122) +31% raw scores
- Real prev-day dvol (s123) eliminates stub bias

**Promising future direction (not shipped today):** v3-default for ELITE
+ v3_tuned_16f for HIGH/VETOED/BROAD = potential +$1,388 lift.

---

## 4. Files shipped this session

| Path | Status | LOC |
|---|---|---|
| `scripts/ml_v3_multi_blend.py` | NEW | ~245 |
| `scripts/ml_per_tier_alpha_conformal.py` | NEW | ~170 |
| `data/polygon_warehouse/derived/ml_v3_blend_predictions.parquet` | NEW (gitignored) | 12k rows |
| `data/models/v3_blend_summary.json` | NEW (gitignored) | small |
| `data/models/per_tier_alpha_conformal_summary.json` | NEW (gitignored) | small |
| `docs/research-log/124_multi_blend_per_tier_alpha_v3_default_elite.md` | NEW (this doc) | this |

Total: 2 new analysis scripts + doc, +415 LOC.

---

## 5. Validated edge stack (post-124)

```
PRODUCTION:
  v3-tuned-16fold + AGGRESSIVE Kelly + VETOED rule D
  + s122: ticker_details enrichment + TCN-skip
  + s123: real dvol + temperature scaling T=1.0275
  + s124: blend HOLD + per-tier alpha NEUTRAL (architectural caps)

  WF baseline: +$29,719 (Aggressive Kelly, $10k bankroll, HI|MID gate)
  + s123 T-scaling: +$392 (v3_temp = $30,111)
  Cumulative: production = ~$30,111

KNOWN UNTAPPED LIFT (not yet shipped):
  + v3-default for ELITE tier:  potential +$1,388 (+14.4% on ELITE)
    (s124 finding; needs hybrid model-router implementation)
```

---

## 6. Next-session priorities

1. **TUESDAY DEPLOY** with all s122/s123 fixes; verify picks fire.
2. **Hybrid ELITE router** — load v3_default for P≥0.60 cohort routing
   (per s124 finding: +$1,388 ELITE lift). Single-line tier-conditional
   model selection in `MetaScorer.predict_v3t`.
3. **Stop chasing architectural diversification of v3** — both blend and
   per-tier alpha NEUTRAL/NEG. Accept v3-tuned-16f + s123 T-scaling as
   the current ceiling.
