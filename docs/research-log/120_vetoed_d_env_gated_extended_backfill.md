# 120 — VETOED rule D env-gated + extended trades_v1 backfill running

**Session date:** 2026-05-03
**Branch:** develop → main (merged + pushed)
**Predecessor:** [119 Path B v4-routed HOLDS v3](119_v4_routed_holds_v3_path_b_negative.md)

---

## TL;DR — production-ready post-Monday improvement shipped

1. **VETOED rule D wired into production inference, env-gated.** Per s115
   ablation: `v3+MID + intraday_pct < p25` delivers **+13.37%/trade WF**
   (n=67) vs production rule E's +9.07% (**+47% per-trade lift**). Default
   stays on rule E; activate post-stable Monday with `MX_VETOED_RULE=D`.

2. **5 new unit tests for VETOED-D** — full positive/negative coverage.
   **42/42 tests pass** (37 prior + 5 new).

3. **Extended trades_v1 backfill running** in background — pulling
   Nov 2025 → Jan 2026 (~52 trading days). Combined with current 64-day
   coverage = ~116 days when complete = **~25%+ aftermath coverage**.
   When done: retry v4 with proper sample size; retrain-and-compare verdict.

---

## 1. VETOED rule D — env-gated alternative

### The problem (recap from s114-s115)
- s114: TCN signal in production VETOED rule was DEBUNKED (random TCN
  produces same lift; z = +0.40σ). Production VETOED is mathematically a
  random subsample of v3+MID-mag with NO discriminative power from TCN.
- s115: Ablation found `v3+MID + intraday_pct < p25` delivers +13.37%
  vs production +9.07% — Lou/Polk/Skouras 2019 fade prior is REAL.

### The shipped fix

`scripts/ml_meta_scorer_inference.py`:

```python
# Module-level (read once at import)
VETOED_RULE = os.environ.get("MX_VETOED_RULE", "E").strip().upper()
INTRA_P25_THRESHOLD = float(os.environ.get("MX_VETOED_INTRA_P25", "0.3608"))

# In assign_tier(...):
if VETOED_RULE == "D":
    if (v3t_proba >= 0.30 and mag == "MID"
            and intraday_pct is not None
            and intraday_pct < INTRA_P25_THRESHOLD):
        return "VETOED", "v3t>=0.30 AND mag=MID AND intraday_pct<p25 [rule D]"
else:  # rule E (default; production)
    if (v3t_proba >= 0.30 and mag == "MID"
            and tcn_proba is not None and tcn_proba < 0.30):
        return "VETOED", "v3t>=0.30 AND mag=MID AND tcn<0.30 [rule E]"
```

`score_candidate()` now reads `intraday_pct` from the candidate dict and
passes through. Lottery_runner already provides `intraday_pct` in the
candidate features — no changes needed there.

### Test coverage (5 new tests)

```
TestVetoedRuleD::test_d_fires_when_intra_below_p25         PASS
TestVetoedRuleD::test_d_blocked_when_intra_above_p25       PASS
TestVetoedRuleD::test_d_blocked_in_hi_mag_even_low_intra   PASS
TestVetoedRuleD::test_d_skipped_when_intra_missing         PASS
TestVetoedRuleD::test_d_ignores_tcn (proves rule D is TCN-free)  PASS
```

setUp/tearDown safely patches module-level VETOED_RULE without leaking
state between tests.

### Activation post-Monday

```powershell
# Single env flag toggle:
$env:MX_VETOED_RULE = "D"
python scripts/lottery_runner.py
```

That's it. The rule swap is a one-line env change. Optional refresh of
INTRA_P25_THRESHOLD via `MX_VETOED_INTRA_P25=0.xx` if WF data shifts.

---

## 2. Extended trades_v1 backfill (Nov 2025 - Jan 23 2026)

Launched in background:
```
python scripts/polygon_trades_v1_pull.py \
    --start 2025-11-04 --end 2026-01-23 \
    --workers 12 --no-convert
```

### Volume estimate
- ~52 trading days × ~3 GB/day = **~156 GB raw download**
- Combined with existing 64-day coverage → ~116 days total
- ~25% aftermath coverage when conversion complete

### Status (commit time)
- 271 GB total in trades_v1/ (vs 185 GB pre-extension) → ~86 GB downloaded
- ETA: ~3-4 more hours for download
- Then conversion: ~2-3 hours
- Then microstructure rebuild on 116 days: ~30-45 min
- Then v4 retry with proper sample size

---

## 3. Edge stack (post-120)

```
PRODUCTION (Monday deploy):
  Meta-scorer 16-fold AGGRESSIVE  +122.83% bankroll WF (~92% APY)
  VETOED rule E (TCN-based)       +9.07%/trade — DEBUNKED but used in
                                                   production WF measurement
  Sharpe 3.15, Calmar 31.24, max DD -2.81%

POST-STABLE-MONDAY (single env flag):
  MX_VETOED_RULE=D
  -> VETOED rule D (intra<p25)    +13.37%/trade  (+47% per-trade lift)
  -> Same overall meta-scorer; only VETOED tier slice changes

NEXT-NEXT (when extended backfill + microstructure rebuild done):
  v4 retry with ~25%+ coverage
  -> retrain-and-compare verdict gates (s116)
  -> SHIP only if +$500 lift + no >30% tier regression
```

---

## 4. Files shipped this session

| Path | Status | LOC |
|---|---|---|
| `scripts/ml_meta_scorer_inference.py` | modified (+43, VETOED_RULE selector + intraday_pct flow) | +43 |
| `scripts/test_meta_scorer_inference.py` | modified (+56, 5 new tests) | +56 |
| `docs/research-log/120_vetoed_d_env_gated_extended_backfill.md` | NEW (this doc) | this |

Total: 2 modified + doc, +99 LOC code + 5 new tests.

---

## 5. Next-session priorities (post-Monday paper deploy)

1. **MONDAY: paper-deploy + monitor.** v3-tuned-16fold, aggressive Kelly,
   intraday refresh, rule E (production). Use `monitor_paper_deploy.py
   --eod` for anomaly detection.
2. **POST-STABLE-MONDAY: switch to rule D** (`MX_VETOED_RULE=D`) for
   +47% per-trade VETOED lift. Verify next EOD matches expected pattern.
3. **When extended backfill completes (~5-7 hr from this commit):**
   - Convert remaining ~52 days to parquet
   - Rebuild microstructure features at ~25%+ coverage
   - v4 retry with retrain-and-compare verdict
4. **Strip TCN if rule D works** — TCN model + intraday refresh become
   unnecessary if VETOED is rule-D-based (purely intraday_pct-driven).
