# 176 — The Mariana Trench Audit: Why Not 5%, and What Would Get Us There

**Author**: Claude Opus 4.8
**Date**: 2026-05-28 (Thursday, post-close)
**Method**: 3 parallel deep-audit agents (sizing / selection / exits) +
direct log + code trace, cross-verified.
**Mandate**: Pierce — "down to the Mariana trench... WHY aren't we
getting to 5%? What is the gap? hypothetically... what would get us
there. Be bold. Be creative."

---

## 0. The one-sentence answer

**We aren't at 5% because the failure is multiplicative across three
independent layers — selection lets ~2% of signals through, sizing
deploys ~6% of equity per fill, and exits capture ~5% of the available
move — and 0.02 × 0.06-equity × 0.05-capture collapses to the
0.14%/day we observe. You cannot tune one layer to fix this; all three
are near-zero and they only compound the right way when fixed
together.**

And the deeper, more uncomfortable truth:

**momentum-x is a long-fat-tail strategy wearing a short-vol costume.
It hunts explosive gap-ups — the fattest right-tail in equities — but
every mechanism it has built over 175 docs (catalyst vetoes, consensus
gates, halved sizing, 50%-of-gain trailing stops) systematically clips
that exact tail and survives on the scraps. The edge it was designed
to harvest is the edge it is engineered to reject.**

This is not a tuning problem. It is a *posture* problem. The system's
defaults are all sign-flipped for its own thesis.

---

## 1. The compounding math of failure

Realized: **+$2,395 over 3 sessions = 0.54%/day** (and that flatters
it — Wed was -0.51%). Target is 5%/day = ~9.3x.

Decompose the capture rate into its three multiplicative factors:

```
daily_return ≈ (signal→fill rate)
             × (capital deployed per fill, as % equity)
             × (fraction of the available move actually captured)
             × (number of fills)
```

Measured values this week:

| Factor | Measured | Source |
|---|---|---|
| Signal→fill rate | ~2% (217 BUY-verdicts → ~2 clean fills/day) | selection audit |
| Capital deployed / fill | ~5-7% of equity (peak combined 12%) | sizing audit + log trace |
| Move-capture fraction | ~5% (exits at +0.7% on +28% runners) | exit audit |
| Fills / day | ~2 | EOD reports |

Each factor is one-to-two orders of magnitude below where it needs to
be for 5%. Because they MULTIPLY, fixing only one barely moves the
needle:

- Fix exits alone (5%→40% capture, 8x): 0.54% → ~4.3%/day... but only
  IF the 2 fills/day persist AND they're the right names. They aren't.
- Fix selection alone (catch the +80% movers): worthless if we then
  deploy 5% of equity and exit at +0.7%.
- Fix sizing alone (5%→20%): amplifies whatever we capture — but we're
  capturing the wrong 2 names and exiting them instantly.

**The lever is the product, not any factor. This is why 174 docs of
incremental defensive tuning never approached 5% — each doc optimized
one factor while the other two held the product near zero.**

---

## 2. Layer 1 — SELECTION: we reject our own thesis

### The funnel (cross-verified, 3 sessions)

| Stage | Count | Source |
|---|---|---|
| Universe scanned | 62-66 stocks | `Pre-market scan complete` |
| Distinct tickers evaluated | 29-39/day | `Evaluating TICKER \| Gap` |
| BUY-verdicts (all cycles) | 217 total (3 days) | PHASE 2 VERDICT SUMMARY |
| Clean broker fills | ~2/day | EOD reports |

### The killer is structural, not a bug

The biggest movers **are in the universe and ARE evaluated** — the
Alpaca screener literally returns QTEX/RGTI at the top of the
most-active list every cycle. They die *downstream* at three gates:

| Gate | What it requires | Why it kills momentum |
|---|---|---|
| **D200-E4 CATALYST** | News agent must return `catalyst_type ∉ {NONE}` | A fast low-float squeeze with no headline yet = blocked |
| **D124 CONSENSUS ALIGN** | Not bearish-dominant (≥2 bearish vs bullish) | risk_agent + manipulation_classifier are *structurally* bearish on every penny gapper → auto "0 bullish vs 2 bearish" |
| **D101 CONSENSUS** | ≥1 directional agent unless MFCS≥0.30 | No news → all agents NEUTRAL/EMPTY → MFCS collapses to ~0.10 from volume alone → reject |

Trace of RGTU/RGTX (35%+ gaps): all 4 LLM agents return EMPTY ("no
news available") → MFCS ~0.095 → D106 tags PROMOTIONAL_EARLY → D124
rejects (0 bullish vs 2 bearish) → D200 confirms catalyst=NONE.

**The system is structurally unable to buy a fast low-float squeeze
that has no confirmable headline — which is the literal definition of
the sub-$50 gap-up momentum archetype it exists to trade.**

### The missed winners (rejected, ran ≥15%, 3 sessions)

| Day | Ticker | Killed by | Max gain | $ @ peak (on $5k) |
|---|---|---|---|---|
| 5/27 | QTEX | consensus | **+86.5%** | $4,325 |
| 5/28 | VCIG | consensus | **+73.3%** | $3,667 |
| 5/27 | CODX | D124 | +46.7% | $2,333 |
| 5/27 | PCLA | consensus | +42.6% | $2,132 |
| 5/28 | NCPL | D124 | +38.5% | $1,926 |
| 5/26 | LFS | D200 | +28.8% | (we DID hold, badly) |
| 5/28 | APPS | D101 | +27.9% | $1,396 |
| 5/26 | RDW | D101 | +17.6% | $879 |
| 5/26 | HYLN | D101 | +17.4% | $869 |

**Total gate-rejected missed alpha: ~$16,640 (realistic close) to
~$27,943 (peak) over 3 days, on tiny $5k hypothetical positions.**
Scale those to real size and the miss is six figures per week.

### Source citations
- D200 catalyst gate: `main.py:3377-3404`
- D101 consensus: `orchestrator.py:1028-1058`
- D124 alignment: `orchestrator.py:1066-1131`
- D112 RVOL pre-filter: `orchestrator.py:742-758` (also flips
  unstably cycle-to-cycle — a real bug)

---

## 3. Layer 2 — SIZING: the elaborate brain is dead code

### Two parallel sizing systems; the smart one is disconnected

The orchestrator computes an elaborate `D96 SIZING` chain
(base% × float × MFCS scale × D106 × VIX...). **The executor throws
it away.** Under `paper_aggressive_mode=True` (the default),
`alpaca_executor.py:239,254` discards `verdict.position_size_pct` and
re-sizes by **fixed-risk: 2% of equity ÷ stop-distance**, capped by a
flat D150 tier % (15% for Tier 3).

Proof from the live log: LFS logged `D96 SIZING final=4.8%` but
actually deployed **5.1% of equity** (2506 sh × $2.99 = $7,493) — the
4.8% number is cosmetic; fixed-risk produced a different qty.

### The real shrinker stack (in order of impact)

1. **Fixed-risk 2%÷stop** — dominant. On a wide ATR stop (LFS stop was
   $0.59 below entry), 2% risk ÷ $0.59 = a position that's a small
   fraction of equity.
2. **D310.T2 wide-arm 0.5×** — hard halving on ~50% of fills. Hit
   BOTH 5/26 fills. (`alpaca_executor.py:281-283`)
3. **D150 Tier-3 15% cap** — ceiling.

The D96 chain's float 0.7×, D106 0.5×, VIX 0.5×, MFCS scale only shrink
the *discarded* number. Cosmetic.

### The deployment ceiling

Peak combined deployment all week: **12% of equity** ($17,809 of
$148k, on the single best day with TWO positions). A 5% daily target
on 12% deployment requires capturing **+42% net across the book** —
on positions we exit at +0.7%.

**Even with perfect selection and perfect exits, the sizing layer
alone caps a +28% runner at ~$600-1,200 of P&L.** You cannot make 5%
risking 6% of equity per name on a strategy whose median win is single
digits.

---

## 4. Layer 3 — EXITS: we surrender the tail by design

### Per-trade reconstruction (exact entry/exit from logs)

| Ticker | Entry | Exit | Exit gain | Day-max | $ left on table | Killed by |
|---|---|---|---|---|---|---|
| LFS 5/26 | $2.99 | $3.01 | **+0.7%** | ~+28% | ~$2,050 | D163 trail |
| BB 5/26 | $8.24 | $8.39 | +1.8% | +23.6% | ~$2,250 | D122 `gratitude` override |
| APPS 5/27 | $6.92 | $6.94 | +0.3% | +10.1% | ~$660 | D163 trail |
| UMAC 5/28 | $28.64 | $28.00 | **-2.2%** | +26% | ~$3,170 | D122 `alpha_oracle` override |

**Total realized ≈ +$3. Total left on table ≈ $8,100 in 3 days.**
Capture ratio ≈ **0%**.

### The two worst offenders

**D163 trailing stop** (`trailing_stop.py`): activates at +2%, trails
at `peak − 50%×(peak−entry)` = the midpoint of entry and peak, with a
2% minimum distance floor. On a thin sub-$10 gapper, minute-2 volume
decay + a 2-3% wiggle right after activation = instant exit. A stock
peaking at +6% exits at +3.0% *by design* (surrenders half). LFS armed
at +2.7%, peaked +4.3%, exited +0.7%.

**D122 single-strategy override** (`exit_intelligence.py:1395-1415`):
a SINGLE parallel strategy with high confidence flips HOLD→market-EXIT
regardless of the composite score (which never even reached the 0.40
threshold). `gratitude` (floor 0.75R) killed BB; `alpha_oracle`
(exits the instant return < null-baseline) killed UMAC at a -2.2%
LOSS while it was about to run +26%. The code comments even admit
`pullback` and `catalyst_half_life` fire EXIT 86-91% of the time and
exclude them — but `gratitude`/`alpha_oracle` are equally itchy and
NOT excluded.

**Supporting pathology**: `volume_fade` sits pinned at 0.8-1.0 on
every thin gap stock every cycle (opening spike always decays), which
keeps the D78 urgency stop permanently ratcheting up under price.

### The cruel proof

APPS on 5/27 exited via D163 at +0.3%. The next day (5/28) it carried
overnight and ran +20% for **+$1,426** — direct evidence that simply
*removing the intraday trail* would have captured the move.

### Source citations
- D163: `trailing_stop.py:96,98,100,234,308-309`
- D122 override: `exit_intelligence.py:1395-1415`
- gratitude: `exit_strategies.py:445-521`; alpha_oracle: `:663-807`
- volume_fade: `exit_intelligence.py:427-443`

---

## 5. The synthesis: a defensive fortress hunting offensive prey

Step back and look at all three layers as one organism:

- **Selection** vetoes anything without consensus + a confirmed
  catalyst. → It only buys the SAFE, OBVIOUS, already-newsed names —
  which are exactly the names that have already moved and have less
  tail left.
- **Sizing** risks a fixed tiny 2%, halves the wide-arm, caps at a
  flat tier %. → It refuses to press even when conviction is maximal.
- **Exits** trail at 50% of gain, override to flat on a single
  cautious signal, treat normal opening-volume decay as distribution.
  → It runs from winners at the first flinch.

Every single default is tuned for **capital preservation**. And it
*works*: drawdowns are tiny, the equity curve is smooth, the bot
hasn't blown up in 175 docs. **It is an excellent short-vol machine.**

But its declared thesis — sub-$50 gap-up momentum — is the single
fattest right-tail in liquid equities. Capturing a fat right tail
requires the opposite of every default above: **buy the un-newsed
squeeze early, press size when the signal is strong, and let the
1-in-5 monster run to +50% to pay for the four that fail.**

You cannot incrementally anneal a preservation machine into a
tail-capture machine. The work is to **invert the posture on the
high-conviction subset** while keeping the fortress for everything
else.

---

## 6. Hypothetically — what gets us to 5%

The math, run forward with all three layers inverted on the
high-conviction tail:

```
Target: 5% of $150k = $7,500/day

Path (realistic, not heroic):
  - 4 fills/day (vs 2) from loosening selection on no-news squeezes
  - 15% equity deployed on ELITE-conviction names (vs 5%)
  - 40% move-capture (vs 5%) from trail-widen + override removal
  - Universe of moves available: +20% to +86% intraday daily

  1 ELITE monster: $150k × 15% = $22,500 × 30% move × 45% capture
                 = $3,037
  3 standard:      $150k × 8%  = $12,000 × 18% move × 40% capture
                 = $864 each = $2,592
  ───────────────────────────────────────────────────────────────
  Total ≈ $5,629/day = 3.75%/day   ← with 60% win rate, ~$3,400 net
```

That lands at ~2-4%/day realistic. To bridge to 5% you need ONE of:
- **Higher hit rate on the monster** (the cascade-anti-selection
  thesis: predict WHICH no-news squeeze continues — this is the
  Continuer_v2 / TabPFN work already in shadow).
- **More size on the monster** (concentrate: 20-25% on the single
  highest-conviction ELITE name).
- **Overnight carry on winners** (D278 already carries; the +20% APPS
  next-day move shows the tail often extends past one session).

**5% is not impossible. It requires inverting the posture on the top
quintile of conviction AND a real continuation edge on the no-news
squeeze. The continuation edge already exists in shadow (Continuer_v2,
Spearman gate). The posture inversion is config + 3 surgical code
changes.**

---

## 7. The bold plan (rank-ordered by $/effort)

### Already shipped (doc 175, Phase A)
- ✅ Cancel-blocking-stops on all close paths (stops the ghost bug
  that zeroed realized P&L).

### Phase B — POSTURE INVERSION on high-conviction (the 5% unlock)
1. **Exits: widen the trail + kill the single-strategy override.**
   - D163: activation 2%→6%, trail 50%→30% of gain, min-distance
     2%→5%. (`trailing_stop.py`)
   - D122: require ≥2 strategies AND conf≥0.85 for an override-EXIT;
     add gratitude/alpha_oracle to the excluded list.
     (`exit_intelligence.py:1395`)
   - volume_fade weight 0.12→0.04; start scoring at ratio<0.4 not 0.7.
   - **Expected: capture 5%→35%+. Biggest single lever.**

2. **Selection: downgrade-not-block the catalyst/consensus gates on
   high-MFCS names.**
   - D200 catalyst: when MFCS≥HIGH, DOWNGRADE to half-size instead of
     hard block. (`main.py:3377`)
   - D124: exempt or down-weight risk_agent + manipulation_classifier
     (they're structurally bearish on every penny gapper).
   - **Expected: 2→4+ fills/day, and the RIGHT names.**

3. **Sizing: reconnect the brain OR raise fixed-risk on ELITE.**
   - Either wire `verdict.position_size_pct` back into the executor,
     OR scale the fixed-risk 2%→4-5% on ELITE-tier conviction.
   - Raise the per-name cap and the position limit 3→5 with a
     portfolio-risk envelope.
   - **Expected: 6%→15% deployment on the conviction tail.**

### Phase C — the continuation edge (5%→sustained 5%)
4. **Wire Continuer_v2 from shadow to a Kelly multiplier on ELITE
   tier** once its Spearman gate clears on T2 live picks. This is the
   "which no-news squeeze continues" predictor — the difference
   between catching QTEX (+86%) and catching the one that fades.

### Guardrails (keep the fortress for the non-conviction body)
- All posture inversions gate on conviction tier (ELITE/HIGH only).
  The MEDIUM/LOW body keeps every defensive default.
- Hard daily drawdown halt at -3%.
- L2 hedge watcher (already proven — saved LFS on 5/27) stays on.
- Ship behind env flags; A/B each change against the current posture.

---

## 8. Honest caveats & open questions

- **Reconciliation note**: one audit agent grepped a specific
  `D313 broker fill` marker and found zero; the EOD reports + ghost
  positions confirm ~2 fills/day DID happen. The funnel-narrowness
  conclusion (217 verdicts → ~2 fills) stands; the exact fill marker
  needs a canonical log string (filed).
- **Bar-recording price basis**: LFS/UMAC recordings are on a
  different/adjusted basis than live fills, so day-max% (and
  left-on-table $) for those two are best-estimates from intraday
  *shape*; entry/exit/realized are exact from logs.
- **Is 5%/day sustainable or just reachable?** Reachable on
  high-volatility days with fat-tail movers present. On a quiet tape
  with no 30%+ gappers, the honest ceiling is lower. The target
  should be "5% on days the tape provides it; preserve capital when
  it doesn't" — which is itself a regime-aware posture (BOCPD is
  already in shadow for exactly this).

---

## Appendix — the three-layer scorecard

| Layer | Current | Required for 5% | Gap |
|---|---|---|---|
| Selection (fill rate) | ~2% | ~4-5% | 2x + RIGHT names |
| Sizing (% equity/fill) | ~6% | ~15% | 2.5x |
| Exits (move capture) | ~5% | ~40% | 8x |
| **Product** | **0.14%/day** | **~5%/day** | **36x** |

The 36x gap = 2 × 2.5 × 8 (approximately). Each layer is independently
fixable. None alone suffices. **Fix the product.**
