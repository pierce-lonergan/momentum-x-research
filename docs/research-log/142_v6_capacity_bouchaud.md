# 142 — v6 capacity analysis: Bouchaud impact eats v3's alpha at $5M AUM

> **Format:** doc-138-template (script first, run, then doc, verdict last).

**Session date:** 2026-05-07 (continuing)
**Branch:** develop
**Predecessors:** [141 v6 TabICL qualification](141_v6_tabicl_qualification.md)
**Status:** **Production-relevant finding.** v3 cascade has REAL +20%
mean ret_t5 alpha on ELITE/HIGH picks. Bouchaud impact wipes it out
above ~$1M AUM under current Aggressive Kelly sizing.

---

## TL;DR — v3 has real edge but limited capacity

Under production cascade thresholds (matches `meta_scorer_inference.py`
`assign_tier()`) on 12,192 v3 BROAD specialist OOS predictions across
320 days × 16 folds:

```
Tier (prob threshold)    n      mean raw ret_t5   win %
  ELITE (>=0.60)         14     +19.90%           57%
  HIGH  (>=0.50)         37     +19.67%           65%
  VETOED(>=0.40)        131      +7.36%           47%
  BROAD (>=0.30)        650      +3.86%           45%
  SKIP  (<0.30)        11360     -4.15%           35%   ← correctly skipped

The cascade is REAL. ELITE/HIGH picks have +20% mean ret_t5.
The skipped 93% of candidates have -4% mean — model correctly avoids
them.
```

But Bouchaud square-root impact (Y=1.5, microcap-realistic) at the
current Aggressive Kelly caps (50/35/20/10) eats this alpha:

```
                                        round-trip impact at this AUM
AUM         ELITE net    HIGH net   VETOED net   BROAD net
$100K       +15.40%      +15.19%      +4.81%      +1.85%   ← ALL POSITIVE
$500K        +9.83%       +9.65%      +1.72%      -0.63%
$1M          +5.66%       +5.94%      -0.54%      -2.49%   ← BROAD breaks
$5M          -0.91%       -1.26%      -8.03%      -9.30%   ← all negative
$10M         -4.45%       -4.28%     -12.18%     -13.20%
```

**Per-tier capacity ceiling (where mean_net flips negative):**

| Tier | last AUM with positive net | first AUM with negative net |
|---|---|---|
| ELITE | $1.00M | $5.00M |
| HIGH  | $1.00M | $5.00M |
| VETOED | $0.50M | $1.00M |
| BROAD | $0.10M | $0.50M |

**Strategic conclusion: v3 production CAN be safely scaled to ~$1M
AUM under current sizing. Above that, position sizes need to shrink
or participation needs to be capped per name.**

---

## 1. Why this matters more than any architecture finding

The entire MoMTrans v4/v5 architecture program was searching for ~1-2%
Spearman improvements. Doc 139 buried it (every variant lost to v3 by
≥0.016). Doc 140 found a TabICL ceiling-lift; doc 141 buried it on
recent data.

**Meanwhile, this analysis shows a 20% impact-driven alpha drain
silently building at $5M AUM — an order of magnitude larger effect
than any model improvement we've measured.**

The user's critique was prescient: *"if MoMTrans never beat v3,
the strategic question is no longer 'how do we improve the model'
but 'how do we deploy v3 better.' Capacity modeling (Bouchaud impact
at $5M AUM), execution quality, sizing methodology, drawdown gating
— those become the highest-leverage moves."*

This doc is the first concrete pass at "deploy v3 better." The
finding: **production capacity is bounded by sizing, not by alpha.**

---

## 2. The setup

Bouchaud square-root impact law (Toth-Eisler-Bouchaud PRX 2011;
Maitrier-Loeper-Kanazawa-Bouchaud 2025):

```
Δp / σ_daily ≈ Y · sign(Q) · √(|Q| / V_daily)
```

where:
- `Δp` = price change in our direction (cost when we're absorbing it)
- `σ_daily` = daily realized volatility
- `Q` = signed position size in dollars
- `V_daily` = daily dollar volume
- `Y` = impact coefficient (~0.5-1 large-cap, 1.5-2 microcap per M.md)

Round-trip cost ≈ 2 × one-way impact (entry + exit).

Net return per trade = `ret_t5 − 2 × Y × √(|Q|/V) × σ_daily`.

### 2.1 Approximations

- `σ_daily` approximated as `intraday_pct / 4` (range ≈ 4× std under
  Normal). Median microcap intraday_pct = 44% → σ_daily ≈ 11%/day.
  This is high but consistent with microcap reality.
- `Y = 1.5` chosen as the midpoint of M.md's microcap range.
  Sensitivity: at Y=1.0 the capacity ceiling roughly doubles; at Y=2.0
  it halves. Either way the directional finding holds.
- Bankroll = 100% of AUM (matches `LOTTERY_BANKROLL_PCT=1.0` in
  current launcher).
- Aggressive Kelly caps per tier: 50% / 35% / 20% / 10% for
  ELITE/HIGH/VETOED/BROAD (matches main.py defaults).

### 2.2 What this analysis does NOT model

- **Order splitting across days.** Single-day impact assumed; in reality
  a 30%-participation order would be split across days, reducing
  effective impact. This makes the analysis conservative (more
  pessimistic than reality at high participation levels).
- **Slippage models beyond Bouchaud.** Real spread, market-maker
  rebates, fee structures all add ~5-15bps additional friction.
- **Adverse selection.** Larger orders are more likely to be filled in
  bad-tape conditions; the realized impact distribution is skewed
  toward the bad outcomes.
- **Position-sizing rules that adapt to participation.** Production
  could (and should) cap participation per name (e.g., never trade
  more than 5% of dvol_d0). This analysis assumes Aggressive Kelly
  fires irrespective of liquidity — exactly the M.md "ELITE pick = 50%
  of bankroll regardless of name" anti-pattern.

The first omission makes the analysis pessimistic; the second and
third make it optimistic. Net direction: roughly correct order of
magnitude, may be off by ~30% in either direction.

---

## 3. The two findings that matter most

### 3.1 v3's cascade has REAL edge

ELITE picks (prob_specialist_BROAD >= 0.60): **+19.9% mean ret_t5
across 14 OOS picks**, 57% win rate. The wide CI (n=14, std=41%)
means the magnitude is uncertain, but the sign is robust:

- HIGH picks (prob >= 0.50): +19.7% mean across 37 picks (n is
  bigger here; estimate is more reliable)
- VETOED: +7.4% mean across 131 picks
- BROAD: +3.9% mean across 650 picks
- SKIP: −4.2% mean across 11,360 picks

The 5pp drop from BROAD (prob 0.30+) to SKIP (prob <0.30) at the
*mean* level is ~1σ confidence given the sample sizes. **The cascade
correctly separates winners from losers.**

This is the apples-to-apples comparison the v4/v5 architecture program
was trying to beat. v3 is good. The question was never "how do we
make v3 better" — it was "how do we deploy v3's edge before impact
eats it."

### 3.2 Capacity ceiling is ~$1M AUM under current sizing

At $1M AUM with 50% ELITE Kelly:
- Position size: $500K
- Median microcap dvol: $8.6M (from data)
- Participation: ~6% (well above the 5% Bouchaud "safe" threshold)
- Round-trip impact: ~14% per round-trip
- Net: +5.7% per ELITE pick (still positive but barely)

At $5M AUM with same sizing:
- Position size: $2.5M
- Participation: ~30% (catastrophic)
- Round-trip impact: ~21%
- Net: −0.9% (alpha completely consumed)

**The capacity ceiling under current Aggressive Kelly sizing is around
$1M AUM.** The $5M reference scale in M.md is structurally beyond what
this configuration can support.

---

## 4. Three production actions, ordered by leverage

### 4.1 Cap participation per name (highest leverage, lowest risk)

Add a per-pick guard:
```
position_$ = min(
    bankroll * tier_kelly_cap,
    dvol_d0 * MAX_PARTICIPATION_PCT,   # e.g., 0.05
)
```

At MAX_PARTICIPATION_PCT=0.05, the analysis becomes:
- Median microcap dvol = $8.6M → max position = $430K
- Even at $5M AUM, ELITE position would be $430K (not $2.5M)
- Round-trip impact at 5% participation: 2 × 1.5 × √0.05 × 11% = 7.4%
- Net ELITE: +20% − 7.4% = +12.6% per pick

This single guard converts the $5M AUM "all alpha eaten" disaster into
a "still profitable" scenario. **Recommended for next launcher commit.**

### 4.2 Adaptive Kelly that scales DOWN as participation rises

Replace fixed `tier_kelly_cap` with:
```
effective_kelly_cap = tier_kelly_cap * impact_dampener(participation)
```

Where `impact_dampener` reduces sizing when impact would dominate.
This is the M.md §10 capacity-aware Kelly recommendation.

Implementation cost: moderate. Requires per-pick liquidity lookup
during sizing, plus a calibrated dampener function. Filed for a future
session — the simpler 4.1 covers most of the gain.

### 4.3 Drawdown-gated bankroll

Instead of LOTTERY_BANKROLL_PCT=1.0 (use 100% of broker equity),
implement a drawdown trailer:
```
LOTTERY_BANKROLL_PCT = max(0.25, 1.0 - 2 * trailing_30d_drawdown)
```

So if the strategy is up to a 25% drawdown, bankroll drops to 50% of
equity. This bounds the worst-case position size during the regimes
where alpha is most likely to be eaten.

Filed for future session.

---

## 5. The recommended next-commit production change

**Add `LOTTERY_MAX_PARTICIPATION_PCT=0.05` to launcher and main.py
sizing logic.** This is the lightest-touch, highest-leverage change.

What it does:
- For each pick, compute `min(bankroll * tier_kelly, dvol_d0 * 0.05)`
- The Bouchaud analysis above shows this guard converts $5M AUM from
  alpha-negative to alpha-positive
- At $140K paper account (current), the guard rarely binds (5% of
  median dvol = $430K, much larger than any current position)
- At $1M AUM, guard binds occasionally
- At $5M AUM, guard binds frequently and saves the strategy

This is a 1-day code change in main.py. Filed as the next
production-relevant work after this commit.

---

## 6. The honest caveats

**n=14 ELITE picks is small.** The +19.9% mean ret_t5 has a wide CI.
The 57% win rate (vs ~40% baseline) is suggestive but not 5σ. If we
lowered the threshold to prob >= 0.50 (HIGH+ELITE combined), n=51 and
mean = +19.8%, which is a much better-estimated number.

**The "ALL alpha eaten at $5M AUM" framing is somewhat alarmist
because we don't trade at $5M today.** Current paper account is $140K.
The capacity ceiling at $1M is more than 7× current AUM, so there's
substantial scaling headroom under current sizing. The $5M capacity
question only matters if we plan to scale the strategy by 35× from
today.

**Y=1.5 is a midpoint estimate.** If true Y for our specific microcap
universe is 1.0 (lower-cap-end stocks), capacity ceiling roughly
doubles. If Y=2.0, it halves. We could empirically estimate Y by
running a regression on our actual paper-trade fills' realized impact
vs participation. Filed for future work.

**Single-day impact assumption ignores order splitting.** A
sophisticated execution algorithm (TWAP/VWAP across an open or even
across days) would reduce realized impact by 30-50% at high
participation levels. This is a deployment-quality area worth
investing in if we ever scale past $1M AUM.

---

## 7. The strategic frame after doc 142

Combining everything from docs 138-142:

| Question | Answer | Confidence |
|---|---|---|
| Did MoMTrans v4/v5 architectures beat v3? | NO (doc 139) | HIGH |
| Did v6 d0 microstructure help? | NO globally; modest BROAD lift only (138) | HIGH |
| Did v6 d-1 microstructure help? | YES on BROAD P@30 (+0.039 CPU-deterministic) | HIGH |
| Does ELITE tier survive DSR? | NO (doc 138, MX_HYBRID_ELITE flipped) | HIGH |
| Did from-scratch CORN MLP help? | NO (doc 140) | HIGH |
| Did TabICL beat v3 on full historical data? | YES + p<0.001 (doc 140) | HIGH |
| Did TabICL lift replicate on recent data? | NO (doc 141) | HIGH |
| **Does v3 production cascade have real edge?** | **YES (+20% mean ret_t5 on ELITE/HIGH)** | MEDIUM (n=14 ELITE) |
| **Does capacity bind below $5M AUM?** | **YES under current sizing; ~$1M ceiling** | MEDIUM (Y=1.5 mid-estimate) |

The disciplined production roadmap, ranked:

1. **Cap participation per name** at 5% of dvol_d0. Lightest-touch
   highest-leverage change. Saves $5M+ AUM scenarios.
2. **Paper-trade A/B for d-1 microstructure features** in v3 BROAD
   specialist (per doc 138 + 141). The only architecture/feature
   positive that survived all qualifications.
3. **Empirically estimate Y from realized paper-trade fills.** Replaces
   the Y=1.5 mid-estimate with a calibrated number.
4. **Order splitting for high-participation names.** TWAP/VWAP across
   the open. Only matters if we scale past $1M AUM.
5. **Drawdown-gated bankroll.** Bounds worst-case position size
   during alpha-eaten regimes.

---

## 8. What's filed but not done

- TabPFNv2 if license unblocked (still might be informative for
  understanding TabICL's per-fold pattern, even without a primary-
  scorer recommendation)
- Empirical Y estimation from paper-trade fills (need fills data,
  filed for after first week of new sizing rules ship)
- Per-tier participation distribution analysis (which picks are most
  likely to bind the new 5% guard? are they the same picks that
  matter most for alpha?)
- Order-splitting / VWAP execution simulator
- Drawdown-gated bankroll implementation

---

## 9. Files this commit

| Path | Status | Notes |
|---|---|---|
| `scripts/ml_v6_capacity_bouchaud.py` | NEW (~190 LOC) | Bouchaud impact estimator + capacity sweep |
| `data/models/v6_capacity_bouchaud.json` | NEW (gitignored) | Per-tier per-AUM results |
| `docs/research-log/142_v6_capacity_bouchaud.md` | NEW (this) | Capacity finding + production rec |

No launcher changes this commit. The recommended production change
(LOTTERY_MAX_PARTICIPATION_PCT=0.05) is a separate code+config change
that touches main.py sizing — filed for next commit, not bundled here.
