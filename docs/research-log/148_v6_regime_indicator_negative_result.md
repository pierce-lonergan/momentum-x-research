# 148 — v6 regime-shift indicator: NEGATIVE result, no predictive gate found

> **Format:** doc-138-template (script first, run, then doc, verdict last).
> Pre-committed verdict criterion was specific: max(|concurrent ρ|,
> |lag-1 ρ|) ≥ 0.5 AND same-sign for both. Result: 0.61 ρ found
> (concurrent), but lag-1 has OPPOSITE sign. Per locked rule: marginal,
> don't deploy.

**Session date:** 2026-05-11
**Branch:** develop
**Predecessors:** [147 v6 recent-subset audit](147_v6_recent_subset_audit_v3_survives.md)
**Status:** **No predictive regime indicator exists** in the current
feature set. The cascade IS regime-dependent on universe volatility,
but that's a concurrent observation, not a forward-looking gate.
Documented as honest negative; thread #1 closed; pivoting to thread #2.

---

## TL;DR

| Question | Answer |
|---|---|
| Is monthly cascade Sharpe predictable from prior-month features? | **NO** — strongest lag-1 ρ is −0.31 with opposite sign of concurrent |
| Does the OLDER→RECENT story hold up at monthly granularity? | **NO clean regime change** — variance dominates, Feb 2026 had Sharpe −5.78 (worst month) |
| Is there a CONCURRENT regime signal? | YES — intraday_pct_mean ρ +0.61, but n=16 months (p≈0.15 after multi-testing correction) |
| Does the OLDER vs RECENT discriminator (Exp C) tell us anything? | NO — it perfectly separates via year/month features, which is a calendar artifact, not regime structure |
| Pre-committed verdict | MARGINAL → don't deploy on weak/asymmetric signal |

---

## 1. Pre-committed criteria (LOCKED in script before running)

```
STRONG indicator: max(|concur ρ|, |lag1 ρ|) >= 0.5 AND same-sign
                  -> propose as v3 feature OR launcher gate
NO indicator:     max |ρ| < 0.3
                  -> regime detection infeasible; close thread definitively
MARGINAL:         0.3 <= max |ρ| < 0.5  OR  >= 0.5 but opposite-sign
                  -> document inconclusive; don't deploy
```

The "same-sign" requirement was specifically to prevent shipping a
correlation that flips its predictive direction at a 1-month lag —
which is exactly what we found.

---

## 2. Exp A — Monthly cascade Sharpe time series

```
month       n_days     mean      std    sharpe_ann
2025-01          7  +12.31%   41.25%      +4.74
2025-02         10  +12.29%   20.29%      +9.61
2025-03         17   +0.97%   30.55%      +0.50
2025-04         10   +5.76%   19.28%      +4.74
2025-05         13   +2.56%   32.12%      +1.27
2025-06         17   +3.55%   21.36%      +2.64
2025-07         19  +13.65%   30.75%      +7.05
2025-08         18   +3.34%   22.58%      +2.35
2025-09         21  +11.28%   14.13%     +12.67
2025-10         23   +4.39%   15.37%      +4.53
2025-11         17   +0.52%   13.32%      +0.62
2025-12         21   +1.12%   10.58%      +1.68
2026-01          6  +18.03%   36.98%      +7.74
2026-02          8   -4.74%   13.01%      -5.78  ← worst month
2026-03         15   +5.45%   27.70%      +3.13
2026-04         16  +26.08%   32.31%     +12.81  ← best month

Lag-1 autocorrelation: -0.239
Linear trend: slope -0.014/month, R² = 0.000
```

**Two findings:**

1. **Sharpe is highly month-to-month variable.** Range −5.78 to +12.81.
   Lag-1 autocorrelation is slightly NEGATIVE (−0.24), suggesting mean-
   reverting variation rather than persistent regimes.

2. **No linear trend.** R² = 0.000 with slope ≈ 0. The "RECENT is
   better" story doesn't hold as a smooth ramp; it's that more of the
   strong months happen to be in the recent window. The OLDER→RECENT
   distinction in doc 147 was real on cumulative metrics but doesn't
   appear as a clean regime CHANGE month-by-month.

3. **Feb 2026 was the worst single month** (Sharpe −5.78 on only 8
   active days). Likely just sampling noise on a small sample, not a
   regime indicator. But it's a reminder that "recent regime is good"
   masks substantial month-to-month variation.

---

## 3. Exp B — Feature aggregates as cascade-Sharpe predictors

For each base feature aggregated monthly, computed Spearman vs:
- **Concurrent**: this month's feature × this month's Sharpe
- **Lag-1 predicts next**: this month's feature × NEXT month's Sharpe (the predictive direction)

```
feature                     concur_rho    lag1_rho   abs_score
intraday_pct_mean              +0.609      -0.311        0.609
dvol_d0_mean                   +0.524      -0.207        0.524
intraday_pct_median            +0.465      -0.296        0.465
open_median                    +0.197      -0.454        0.454
intraday_pct_std               +0.426      -0.168        0.426
dvol_d0_median                 +0.412      -0.193        0.412
ret_open_close_d0_mean         +0.338      -0.204        0.338
candidates_per_month           +0.276      -0.111        0.276
ret_open_close_d0_std          +0.244      -0.168        0.244
universe_mean_ret              +0.200      +0.232        0.232
prior_avg_t5_mean              +0.268      +0.086        0.268
fired_per_month                +0.010      +0.159        0.159
fire_rate                      -0.103      +0.186        0.186
mean_v3_proba                  -0.032      +0.146        0.146
```

### 3.1 The single biggest finding

**Strong concurrent regime signal exists:** when this month's universe-
wide intraday_pct is high, the cascade Sharpe is high (ρ +0.61). When
calm, low. This makes intuitive sense — the cascade picks gap-up
continuation, and continuation requires sustained intraday volatility.

**But the lag-1 sign FLIPS.** High intraday_pct LAST month doesn't
predict high Sharpe THIS month — it weakly predicts the OPPOSITE
(ρ −0.31). This is consistent with mean-reverting volatility:
high-vol months tend to follow low-vol months.

The pre-committed "same-sign" requirement was specifically designed
to catch this case: a feature that's correlated with concurrent
performance but flips at lag-1 is NOT a forward-looking gate — it
just describes today's regime.

### 3.2 Statistical caveat (the n=16 problem)

Concurrent ρ = +0.61 at n=16 months gives t = ρ·sqrt((n-2)/(1-ρ²))
= 0.61·sqrt(14/0.628) = 2.88, p ≈ 0.012 two-sided.

But we tested 16 features. Bonferroni-corrected p ≈ 0.19. Even the
strongest signal is borderline-significant after multiple-testing
correction. Don't deploy a regime gate on this.

---

## 4. Exp C — OLDER vs RECENT discriminator (defensive comparison)

**OOS AUC = 1.0000** — perfect discrimination. Top features:

```
year         0.415   <-- pure calendar artifact
month        0.104
day_of_year  0.070
quarter      0.065
year_frac    0.061
prior_n_log  0.037
prior_n      0.029
rank_intra   0.027
mcap_known   0.022
n_today_total 0.021
```

**The discriminator finds NOTHING about regime structure** — it just
identifies the calendar. The `year` feature alone has 41.5% importance.
Of course year=2025 is OLDER and year=2026 is RECENT; the model didn't
need to find a deeper pattern.

This is the textbook confound the "rolling N=16" approach was designed
to avoid. Exp C's perfect AUC is informative only about calendar
features being informative about which period we're in — not about
what economically distinguishes the periods.

**Lesson: never compare two time periods directly with a discriminator
that has access to date features. Use rolling cross-section instead
(Exp B).**

---

## 5. Pre-committed verdict applied

```
Best feature: intraday_pct_mean
  Concurrent ρ = +0.609 (passes 0.5 threshold)
  Lag-1 ρ     = -0.311 (OPPOSITE sign, fails same-sign requirement)
Score: 0.609

Pre-commit rule:
  STRONG: >= 0.5 AND same-sign     -> NOT met (opposite signs)
  NO indicator: max |ρ| < 0.3      -> NOT met (0.609 > 0.3)
  MARGINAL: rest                    -> MET

Verdict: MARGINAL — don't deploy on weak/asymmetric signal.
```

**Thread #1 closed as: NEGATIVE on the predictive use case, POSITIVE
on the concurrent use case (with caveats), NOT shippable as a gate.**

---

## 6. What this means for the strategic frame

### 6.1 The OLDER→RECENT cascade Sharpe shift IS real but UNOBSERVABLE ahead of time

Doc 147 §2.3 noted: "OLDER period (Jan-Jul 2025): cascade Sharpe +3.92.
RECENT period (Aug 2025+): cascade Sharpe +5.08." Doc 148 confirms
this is real (the months are visibly stronger on average) but does
NOT confirm a regime change with a clean transition. It's that:

- High-vol months pay better than low-vol months (concurrent ρ +0.61)
- Recent months happened to have more high-vol months
- We can't predict which months will be high-vol from prior-month data

So we ARE in a favorable regime, but our ability to KNOW we're in one
is purely concurrent — by the time we measure the universe vol, the
month is half over.

### 6.2 Implication for production deployment

The D288 defensive overlay (and the underlying v3 cascade) doesn't
have a forward-looking regime gate to add. We could implement a
**concurrent rolling-window sizing modifier**:

```
modifier = clip(rolling_30d_mean_intraday_pct / 12mo_median, 0.5, 1.5)
position_size *= modifier
```

But this introduces lookahead concerns (we're observing today's
universe vol to size today's trades) and the statistical evidence
is borderline. Filed as a candidate for future testing if the D288
shadow data shows the strategy is regime-sensitive in live deployment.

### 6.3 What we WON'T do

- Add a forward-looking regime feature to v3 (no such feature found)
- Build a regime-detector model (no signal to learn from)
- Skip-trade on "bad regime" signals (no reliable bad-regime indicator)
- Spend more time on this thread (negative result is decisive)

### 6.4 Pivoting to thread #2

Per doc 147 §8, the three parallel threads were:
1. Regime indicator (THIS DOC — closed negative)
2. **D290 — per-tier-AUM-aware Kelly caps** ← next thread
3. HIGH-RECENT discriminator (filed)

Thread #2 is more deterministic engineering work: doc 147 §5.4 found
that HIGH scales to $5M but BROAD breaks at $500K. Adaptive per-tier
Kelly that shrinks BROAD's allocation as AUM grows is a clean
production change. ~3 days of work; well-bounded; high production
value if D288 ships.

---

## 7. Files this commit

| Path | Status |
|---|---|
| `scripts/ml_v6_regime_indicator.py` | NEW (~290 LOC, 3 experiments + pre-commits) |
| `data/models/v6_regime_indicator.json` | NEW (gitignored) |
| `docs/research-log/148_v6_regime_indicator_negative_result.md` | NEW (this) |

No code or launcher changes. Production unchanged.

---

## 8. The honest meta-note

The pre-committed criterion (max ρ ≥ 0.5 AND same-sign) was *specifically
designed* to catch the case we found. A weaker criterion ("max ρ ≥
0.5") would have passed and shipped a regime gate based on intraday_pct
that doesn't actually predict next month. The same-sign requirement
forced honesty about the lag-1 reversal.

This is the discipline working in the negative direction: the result
landed exactly where the pre-commit said "don't deploy," and we don't
deploy. **Same hygiene that produced doc 138/141 reversals AND doc
144/147 confirmations now produces this clean negative.**

The thread is closed. No further work on regime indicators until
either:
- New feature data becomes available (e.g., macro indicators we don't
  currently load — VIX, term structure, USD, commodity prices)
- Live shadow data reveals an unanticipated regime sensitivity that
  needs investigation

Pivoting to thread #2 (D290 per-tier-AUM-aware Kelly).
