# 121 — v4 at 19.2% coverage: DEFINITIVE HOLD v3 (4th negative finding)

**Session date:** 2026-05-04 (Monday morning ET)
**Branch:** develop → main (merged + pushed)
**Predecessor:** [120 VETOED-D env-gated + extended backfill](120_vetoed_d_env_gated_extended_backfill.md)

---

## TL;DR — fourth v4 negative finding closes the case

1. **Extended trades_v1 backfill complete:** 184 days = 9 months
   (Aug 2025 → Apr 2026), 147 GB ZSTD parquet.

2. **Microstructure rebuilt at 19.2% coverage:** **3,841 rows** spanning
   9 months (up from 1,345 / 6.7%; up from 543 / 2.7%).

3. **🚨 v4 retry HOLDS v3 with -$4,390 regression** at 19.2% coverage —
   even WORSE than s117's -$4,232 at 1% coverage. The hypothesis "more
   data fixes v4" is FALSIFIED.

4. **Four consecutive v4 attempts, zero wins:**
   - s117: v4 + sparse features (1% coverage)        → -$4,232 regression
   - s118: (apparent) v3 -7.80pp on micro=1          → biased small sample
   - s119: v4 specialized on 1,345 micro=1 rows      → -18.12pp delta
   - **s121**: v4 at **19.2% coverage** + full news  → **-$4,390 regression**

5. **Production: v3-tuned-16fold is the model.** Edge stack capped at
   +122.83% bankroll WF / 92% APY / Calmar 31.24. Future improvements
   require ARCHITECTURAL changes, not feature additions.

---

## 1. The numbers

### Final v4 vs v3-tuned-16fold (16-fold WF, $10k bankroll, Aggressive Kelly)

| Tier | v3 n | v3 avg | v3 $ | v4 n | v4 avg | v4 $ | Δ $ |
|---|---|---|---|---|---|---|---|
| ELITE | 7 | +58.79% | +$176 | 8 | +48.14% | +$107 | -$69 |
| HIGH | 27 | +23.45% | +$680 | 26 | +15.62% | +$349 | -$331 |
| VETOED | 87 | +5.36% | +$2,190 | 100 | +8.30% | +$1,342 | -$847 |
| BROAD | 367 | +8.54% | +$9,237 | 454 | +5.00% | +$6,094 | -$3,143 |
| **TOTAL** | | | **+$12,283** | | | **+$7,893** | **-$4,390** |

### Per-tier raw stats from v4 ensemble run

| Tier | n | avg | win% | Sharpe |
|---|---|---|---|---|
| v4 P≥0.30 | 833 | +5.31% | 47.0% | (similar) |
| v4 P≥0.50 | 50 | +20.42% | 60.0% | (similar) |
| v4 P≥0.60 | 8 | +25.45% | 75.0% (vs v3's 90.0%) | -15pp ★ |

**P≥0.60 tier hit hardest** — the elite v3 picks at +47.53% / 90% win
become +25.45% / 75% under v4. This is exactly the opposite of what
adding "smart" features should do.

---

## 2. Why ALL v4 attempts have failed (synthesis)

After 4 attempts, the pattern is unambiguous:

### v3-tuned-16fold extracts a calibrated, regime-spanning signal that:
1. **Adding sparse features hurts** (s117): NaN dominance + indicator
   leakage of "this is recent"
2. **Specialization on a subset hurts** (s119): smaller training data,
   higher feature-to-row ratio
3. **More data doesn't help** (s121): same architectural problem at scale

### What v3 IS doing right:
- 54 features × 16 monthly folds × 16,000+ rows per fold
- Ensemble of XGBoost + LightGBM + LogReg + RF + meta-learner
- Conformal calibration per fold
- Optuna-tuned across full WF
- Cross-sectional + temporal + sector signals

### Why microstructure ADDS NOTHING measurable:
The path-derived features (`first_5min_max_close`, `last_5min_avg_close`,
`vol_z` early/late, `u_shape_intraday`, `volume_acceleration`) already
capture the intraday dynamics. The tick-level microstructure aggregates
(`sweep_burst_rate`, `dark_pool_pct`, etc.) just add noise on top of an
already-saturated signal.

### Why news ADDS NOTHING measurable:
Only 4.66% of microcap gap-ups are news-driven (literature consistent).
The 95% no-news rows dominate the model's learning. The 4.66% with-news
slice doesn't have enough discriminating signal at that rarity.

---

## 3. Production decision (final)

**v3-tuned-16fold is the model. Lock in +122.83% bankroll WF.**

### Edge stack (frozen)
```
Production model: continuer_v2_v3_tuned.pkl (16-fold Optuna params)
Production rule: VETOED rule E (TCN-based) [for Monday WF compatibility]
Production sizing: aggressive Kelly (50/35/20/10 caps)

WF expectations:
  ELITE     7 picks  +58.79%  85.7% win  +$2,058
  HIGH     27 picks  +23.45%  63.0% win  +$1,899
  VETOED   87 picks  +5.36%   42.5% win  +$591
  BROAD   367 picks  +8.54%   49.9% win  +$1,994
  TOTAL              +122.83% bankroll over 16 mo (~92% APY)

Drawdown profile:
  Sharpe 3.15, Calmar 31.24, max DD -2.81%, recovered 6 days
```

### Post-stable-Monday improvement (single env flag)
```
$env:MX_VETOED_RULE = "D"  -> +13.37%/trade VETOED (vs +9.07%, +47% lift)
                              From s115 ablation; rule D is TCN-free
```

### Future architectural avenues (not v4-via-features)
- **Multi-model ensemble**: train 3-5 v3-style models on different
  data slices (Q1-only, year-old-only, etc.); blend predictions
- **Calibration improvements**: temperature scaling on v3 outputs
- **Conformal width refinement**: smaller alpha, per-tier alpha
- **Position sizing innovations**: dynamic Kelly based on regime confidence

---

## 4. Files this session

| Path | Status | Note |
|---|---|---|
| `data/polygon_warehouse/trades_v1_parquet/year=2025/{month=08,09,10}` | NEW (gitignored) | 65 days |
| `data/polygon_warehouse/derived/microstructure_features.parquet` | rebuilt (gitignored) | 3,841 rows / 19.2% |
| `data/polygon_warehouse/derived/ml_v2_walkforward_predictions_v4.parquet` | rebuilt (gitignored) | 12K rows |
| `data/models/continuer_v2_v4.pkl` | NEW (gitignored) | NOT promoted |
| `data/models/v4_lift_summary.json` | NEW (gitignored) | HOLD v3 verdict |
| `docs/research-log/121_v4_at_19pct_coverage_definitive_hold.md` | NEW (this doc) | this |

No code changes — pipeline (s116) did its job; verdict is final.

---

## 5. Next-session priorities (post-final-v4)

1. **MONDAY: paper-deploy** with v3-tuned-16fold + aggressive Kelly + rule E.
2. **Monitor live deploy** via `monitor_paper_deploy.py --tail` and `--eod`.
3. **Post-stable-Monday: switch to VETOED rule D** (`MX_VETOED_RULE=D`)
   for +13.37% VETOED tier.
4. **Strip TCN model + intraday refresh** (rule D doesn't need them) —
   compute savings without P&L cost.
5. **Architectural experiments** (NOT v4-via-features):
   - Multi-data-slice v3 ensemble
   - Temperature scaling post-training
   - Per-tier alpha conformal calibration
6. **Phase 4 features (if pursued)**: order book imbalance, real-time
   sweep tape — fundamentally different from v4's tick aggregates.
