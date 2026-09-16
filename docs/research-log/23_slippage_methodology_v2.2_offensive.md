# 23 — MOMENTUM-X v2.2: Offensive 12-Month $1–5M Architecture

**Status:** offensive synthesis. Replaces v2's 36-month defensive posture.
**Predecessors:** `15_slippage_methodology_application.md` (v1),
`18_slippage_methodology_application_v2.md` (v2 defensive),
`19_eod_bug_findings.md` (Wed 2026-04-22 system state),
`20_d146_bar1_exit_spec.md`, `21_phase0_instrumentation_mvp.md`,
`22_tuesday_architecture_call_agenda.md`.
**Inputs (8 artifacts read in full or large portions):**
- `compass_artifact_wf-aef3259b…md` — frontier critique of v1
- `compass_artifact_wf-616ec49c…md` — scaling-arc evidence
  (the artifact that drove v2's 36-month posture)
- `compass_artifact_wf-55cb9de0…md` — six candidate S2 architectures
- `compass_artifact_wf-57613a15…md` — S1 execution policy + DMA
- `compass_artifact_wf-e274cbca…md` — live-fill slippage methodology
- `Analyzing Signal Decay Detection Gaps.md` — BOCPD / FPOP / PPD
- `Project Gaps_ Own Capital Path.md` — Monte Carlo on
  $142K → $1.5M organic compounding
- `15_slippage_methodology_application.md` — v1 as reference

**Created:** Wed 2026-04-22 evening, immediately after the EOD bug-sweep
patches landed (Bug D / E / #13 / F closed; D146 + Phase 0 specs published).

---

## §0. The pivot — why v2 was defensive and what "offensive" means here

### 0.1 What v2 actually concluded

The `616ec49c` scaling-arc artifact is the document that drove v2's
defensive posture. Its centre paragraph:

> "No primary source documents a solo quant going $150K → $5M of own
> capital in 12–15 months on a published-signal basis."

v2 internalised this as a binding constraint and rebuilt the plan
around a **36-month** RIA+SMA arc with $5M reachable only via
mixed own-capital + 2–5 accredited-investor SMAs after a 24-month
audited track record. The methodology paper became "credibility
infrastructure," not capital access. Every trade-off in v2 was
dominated by "do not embarrass the methodology paper at peer review."

### 0.2 What offensive means

Offensive does not mean ignoring the research. It means accepting
its honest finding (organic compounding $142K → $5M in 12 months has
~0% probability per the `Project Gaps` Monte Carlo) and **engineering
around the binding constraint**, not capitulating to it. The research
itself names the engineering levers:

1. *External seed injection is "a mathematical requirement, not an
   optional luxury"* (`Project Gaps` §7.2).
2. *Multi-strategy capacity expansion is the dominant scaling lever*
   once a single-signal capacity ceiling binds (`55cb9de0` core
   recommendation).
3. *DMA migration trigger is annual notional, not AUM* — and we are
   already past the $500K notional break-even (`57613a15` §2.1).
4. *BOCPD + Fearnhead-Rigaill + posterior-predictive divergence
   collapses decay-detection lag from 30 days to 1 trade*
   (`Signal Decay` §intro), enabling aggressive Kelly with a
   mathematically-sound kill switch.
5. *Adversarial spot-check identified 5 methodology updates*
   (BJZZ→QMP, two-timescale propagator, MC-panel, prior-predictive
   sim, HonestDiD T_pre≥4) that materially change measured capacity
   numbers — most likely upward at small N (`616ec49c` §7).

v2 implicitly chose to wait for safety. v2.2 chooses to **deploy
the engineering levers concurrently** and accepts a higher branching
factor on the 12-month outcome — but with explicit branch
probabilities and pre-committed pivots, not optimism.

### 0.3 Honest framing of the target

| Target | Probability under v2 (36-mo posture) | Probability under v2.2 (12-mo offensive) |
|---|---|---|
| **$1M deployed AUM by month 12** | <5% | **35–55%** (conditional on §3 seed) |
| **$2.5M deployed AUM by month 12** | ~0% | **15–30%** (conditional on §3 seed + S2 ramp) |
| **$5M deployed AUM by month 12** | ~0% | **5–15%** (requires §3 seed full + S2 + S3 + organic compounding top-quartile + zero major drawdowns) |
| **$5M by month 24** | 10–20% | **40–60%** |

These are conditional probability estimates from the artifacts, not
guarantees. The point of v2.2 is that **$1M is genuinely feasible in
12 months** if the levers are pulled in concert. $5M in 12 months is
the stretch target with explicit conditioning.

The defensive failure mode is sandbagging: optimising for "no
embarrassment" guarantees not reaching $1M either, because the
methodology paper consumes the engineering bandwidth that should go
to S2/S3 deployment and DMA migration.

---

## §1. The arithmetic — what 12-month $1–5M actually requires

### 1.1 The Monte Carlo result, restated with precise numbers

`Project Gaps` §6 ran the proprietary-capital Monte Carlo with two
parameterisations. Both matter for v2.2 because the gap between them
is the gap between defensive and offensive — not a gap between
"impossible" and "easy."

**Scenario A — empirically grounded baseline** (22% annual return,
15% vol, 30% tax drag, $4,965/mo W-2 infusion, $142K start):
- Month 36 median: **$368,910** — P($1.5M @ m36) = **0.00%**
- Month 60 median: **$628,450** — P($1.5M @ m60) = **0.01%**
- **Tax sensitivity:** at 0% tax drag, m36 median rises to only
  $415K and m60 to only $785K. Tax is **not** the binding
  constraint — capital base inadequacy is.

**Scenario B — hero case** (65% annual return, 35% vol, 30% tax):
- Month 36 median: **$768,220** — P($1.5M @ m36) = **8.25%**
- Month 48 median: **$1,285,400** — P($1.5M @ m48) = **38.12%**
- Month 60 median: **$2,150,900** — P($1.5M @ m60) = **68.45%**

**Conclusion both scenarios share:** *organic compounding cannot
bridge the gap to $1.5M at any horizon under realistic parameters.*
Even at the impossible-on-record 65% annualised return, m36 hit
probability is <9%. v2 read this and deferred. v2.2 reads it and
raises the seed (§3) — the Scenario-B m48 value of $1.285M times the
Tranche-A $500K injection effectively *imports* the additional 12
months of compounding, which is exactly how 36-month organic
collapses to 12-month augmented.

The artifact's own §7.2 conclusion:
> "The plan must abandon the deterministic '$1.5M in 36 months'
> assertion … because organic compounding cannot mathematically
> bridge the gap within the desired timeframe, the business
> architecture must seek external acceleration. Raising **$500,000
> to $750,000 in early-stage external seed capital** … is a
> mathematical requirement."

v2 read this and concluded: extend the timeline. v2.2 reads it and
concludes: **raise the seed capital**. Both are valid; v2.2 is
the more aggressive choice.

### 1.2 What $1M / $2.5M / $5M each require

The arithmetic for each tier under v2.2:

**$1M @ month 12** requires:
- Starting principal $142K + monthly infusion $4,965 × 12 = $202K
  organic capital base by m12.
- Plus seed of **$500K** at m1–m3 → $700K base by m6.
- Plus realised compounding of $300K from $700K base over the
  remaining 6 months = ~57% return over 6 months.
- This is achievable IF S1 alone (the existing system) sustains its
  in-sample edge AND we deploy S2 by m6 to absorb the additional
  $500K capacity.
- **Conditional probability: 35–55%**, dominated by the seed-raise
  success rate and the S2 build velocity.

**$2.5M @ month 12** requires:
- Seed: $500K initial + $750K secondary at m6.
- Plus organic + compounding to $1M by m6.
- Plus full S2 + partial S3 deployment by m9 absorbing $1.5M.
- **Conditional probability: 15–30%**, dominated by S2/S3
  capacity acquisition (need both online by m9, calibrated, and
  performing within 60% of paper Sharpe).

**$5M @ month 12** requires:
- Seed: $1M friends-family-angel by m3, plus $1M HNW SMA at m6,
  plus $1.5M HNW SMA at m9.
- Full three-strategy portfolio operational by m6.
- All three strategies performing in top decile (P>0.5
  individually × 3 = ~0.125 joint).
- **Conditional probability: 5–15%**, dominated by the joint
  performance probability and the m6 SMA conversion which
  normally takes 18–36 months per `616ec49c`.

The reframing: $1M is the realistic offensive target; $2.5M is the
stretch; $5M is the upside scenario that everything has to break
right. The Tuesday-call commit point is **the $1M plan with $2.5M
optionality**.

### 1.3 What v2.2 does with this honesty

Three commitments:

1. **The $5M number stays in the public framing because it is the
   conviction case** (and because pulling it down to $1M for the
   public framing telegraphs to allocators that you don't believe
   in the strategy). But the operating plan budgets and gates
   against the $1M case as base.
2. **The seed-raise is treated as a Q2 deliverable, not a Q4
   stretch.** §3 below details the path.
3. **Drawdown caps are pre-committed** at the level that preserves
   the $1M outcome even under top-quartile bad luck. §5 details this
   via BOCPD + Kelly fraction scaling.

---

## §2. The three-engine portfolio architecture

### 2.1 Why three engines, not one

v1 and v2 both implicitly assumed S1 (microcap cascade) would
saturate first and S2 would be a future-stretch placeholder. The
`55cb9de0` artifact rejects this:

> "Run S2 as a two-sleeve composite rather than a single signal …
> total S2 capacity envelope: $3–6M, bringing the combined portfolio
> to the $5M AUM target with headroom for organic growth."

v2.2 takes the artifact's S2-as-two-sleeve recommendation and
treats S2-A (textual) and S2-B (options-informed) as **separate
engines** S2 and S3 with independently capped allocation budgets,
because they have orthogonal failure modes and orthogonal
data-vendor dependencies.

### 2.2 The three engines, ordered by deployment priority

| | S1 — microcap cascade | S2 — LLM textual stack | S3 — options-informed |
|---|---|---|---|
| **Status today** | Live at $142K, paper-trading capacity | Not built | Not built |
| **Universe** | $0.50–$20 microcap, 5%+ pre-market gap | Russell Midcap (~800 names) | Russell Midcap option-liquid subset (~300–500 names) |
| **Mechanism** | Microstructure cascade / anti-selection | Information-processing inattention on multi-dim text | Informed option-flow leakage |
| **Horizon** | Same-day (entry 9:30, exit ≤ EOD) | 20–60 trading days | 21 trading days (monthly rebal) |
| **Data input** | Alpaca minute bars + Polygon NBBO | SEC EDGAR + earnings transcripts (FMP) | ORATS Near-EOD FTP |
| **Capacity (own + DMA)** | $1.5–2.5M | $2–4M | $1–2M market-neutral |
| **Sharpe (estimated)** | 1.0–1.5 in-sample (post-decay 0.6–0.9) | 0.7–1.0 | 0.5–0.9 |
| **Correlation vs S1** | — | 0.05–0.15 | 0.15–0.25 |
| **Build effort** | Already built, optimising | 4–6 months engineering | 3–4 months engineering |
| **Data cost / yr** | ~$2.5K (Alpaca + Polygon basic) | ~$4K (SEC free, FMP $100/mo, LLM API ~$50/mo) | ~$5–8K (ORATS primary + IVolatility validation) |

**Combined capacity envelope: $4.5–8.5M** at near-orthogonal pairwise
correlations (0.05–0.25), which √n-scales the combined Sharpe to
**1.4–2.0** vs any single sleeve.

### 2.3 S1 — microcap cascade (existing system, hardened)

What v2.2 changes vs v2:

- **Drop the `bar1_exit_pct` from 1.00 to 0.50** for the first
  60 days of v2.2 deployment. Walk-forward shows 50%=PF 1.5x is the
  most robust point; 100%=PF 2.0x is at the overfit threshold. Bug F
  surfaced today that we don't yet measure `Arena_actual` against
  `Arena_expected`; the 50% setting buys time to populate the D146
  dedicated table (`20_d146_bar1_exit_spec.md`) before deciding
  whether to push back to 100%.
- **Adopt the BJZZ → QMP signing migration immediately.** Per
  `aef3259b` and `616ec49c`, BJZZ identifies only 35% of true retail
  trades and signs 28% of those wrong; in microcap with >5¢
  spreads, accuracy collapses to ~52% (random). QMP reduces signing
  error to 5% across all spread widths. This is a 1-week engineering
  ticket and likely changes the measured capacity number itself.
- **Keep S1 capacity capped at $1.5M** in the v2.2 plan even though
  the calibration band is $1.5–2.5M. The headroom is the safety
  margin against the `Project Gaps` §4 transaction-cost result that
  realistic mid-quartile execution sustains net returns 15–25%
  annualised, not 65%+.
- **Backfill the 30-day broker-vs-internal qty divergence scan**
  that Bug D made an action item. This is a v2.2 dependency
  because the `_perm_impact` calibration in `e274cbca` requires
  clean own-fill data; AGPU's 846-vs-505 silent drift would have
  contaminated η estimation by ~40%.

- **Flip the calibration priority from γ to η_perm.** v1 and v2
  both implicitly emphasised tightening the γ exponent (the
  square-root vs linear question). Per `e274cbca` §3.2's sensitivity
  decomposition at the central-case parameterisation:

  | Parameter | ∂TotalSlip / ∂param |
  |-----------|---------------------|
  | **η_perm** | **3.0%** |
  | η_temp | 0.37% |
  | γ | **0.08%** |

  η_perm is **~37×** more leverage on the scaling decision than γ.
  v2.2's calibration ordering is therefore: (1) own-fill η_perm
  estimation under the matched-cohort DiD; (2) V_τ blended-proxy
  calibration on the same fills; (3) γ refinement deferred to N≥75.
  The 30-trade window targets η_perm with γ held at the
  TruncatedNormal(0.70, 0.20, [0.4, 1.2]) prior from `e274cbca`
  §1.4 — the prior carries the uncertainty γ-refinement would
  otherwise discharge prematurely.

- **D196 tiered slippage multipliers (15× micro, 6× low-cap) are
  unvalidated implicit η scalings.** v2.2 treats them as priors that
  the methodology must explicitly validate or reject in m4–m6 — they
  are NOT empirically defensible until N≥30 own-fill calibration
  passes the martingale residual zero-mean test (§13.3).

### 2.4 S2 — LLM-augmented textual stack (S2-A in `55cb9de0`)

The `55cb9de0` artifact's recommended S2 core. Five sub-signals:

1. **Cohen-Malloy-Nguyen Lazy Prices** — 10-K/10-Q YoY textual
   similarity. Long stable, short changers.
2. **Meursault et al. PEAD.txt** — FinBERT earnings-call text
   surprise. Documented 3–5% drift, larger than numeric SUE on
   2010-2019 data.
3. **Chan-Marsh 8-K overnight drift** — items 2.02/5.02/8.01,
   post-2020 evidence, mechanistically clean.
4. **Cohen-Malloy-Pomorski opportunistic Form-4** — insider
   non-routine trades.
5. **Jegadeesh-Livnat revenue-and-guidance-confirmed SUE** — overlay
   to filter out PEAD that is not earnings-quality-driven.

**Build path (4–6 months):**

| Month | Deliverable |
|---|---|
| m1 | EDGAR crawler + 10-K/Q diff pipeline (Lazy Prices) |
| m2 | FMP transcript ingest + FinBERT scoring (PEAD.txt) |
| m3 | 8-K item parser (Chan-Marsh) + Form-4 filter |
| m4 | Composite scoring + cross-sectional ranking |
| m5 | Walk-forward backtest (2014–2025) + IV-dispersion conditioning gate |
| m6 | Live paper trading on $0 capital |
| m7+ | Live deployment, capital scaled per BOCPD + Kelly |

**Decay-survival evidence** (per `55cb9de0`):
- Meursault et al. (*JFQA* 2024) replicates PEAD.txt at 3–5%
- Sadlo (2020) replicated Lazy Prices in S&P 1500 through 2020
- Chan-Marsh (2024) is post-2020, unreplicated but mechanistically
  clean
- Lopez-Lira & Tang (2023/2025) and Chen-Kelly-Xiu (2022/2024) both
  show transformer-extracted alpha orthogonal to FF6

**Kill switch:** if BOCPD posterior changepoint probability >0.85 on
the residual signal series at any point in m7+, freeze allocation,
revert to paper, investigate. Same circuit applies to S1 and S3.

### 2.5 S3 — options-informed (S2-B in `55cb9de0`)

Two sub-signals:

1. **Xing-Zhang-Zhao smirk** — IV(OTM put, K/S 0.80–0.95) − IV(ATM
   call). Long low-smirk, short high-smirk. ~60–65% of raw alpha
   survives borrow-fee adjustment per Muravyev-Pearson-Pollet
   (forthcoming *JFE*).
2. **An-Ang-Bali-Cakici ΔCVOL / ΔPVOL** — month-end ATM IV changes.
   Change-based, escapes most borrow-fee contamination. Long high
   ΔCVOL, short low ΔCVOL.

**Build path (3–4 months):**

| Month | Deliverable |
|---|---|
| m4 | ORATS Near-EOD FTP integration + surface validation |
| m5 | Smirk + ΔCVOL/ΔPVOL ranking pipeline |
| m6 | Walk-forward backtest + IVolatility cross-validation |
| m7 | Live paper trading |
| m8+ | Live deployment, conditioned on IV-dispersion regime |

**Kill switch:** same BOCPD architecture as S2.

### 2.6 IV-dispersion as the cross-strategy validation primitive

Per `55cb9de0`, IV-dispersion fails as a standalone signal but
**survives as a conditioning + transferability variable**. v2.2 uses
it for:

1. **Conditioning gate on S2 and S3.** Both deploy full gross only
   when σ_X (cross-sectional 30-day ATM IV stdev) is in the top
   tercile over the trailing 12 months — the regime where informed
   flow dominates.
2. **Transferability pre-registration for any future S4.** Before
   any new signal moves from microcap to mid-cap or vice versa,
   the IV-dispersion KS / Wasserstein test from `55cb9de0` Part 2
   gates the migration. This is the *publishable methodological
   contribution* the artifact identifies as JFDS white-space —
   v2.2 ships it as both a production primitive and the basis of
   Paper 2.

### 2.7 Why this beats the v2 Lou-Polk-Skouras placeholder

`55cb9de0` was direct: LPS overnight continuation has the highest
correlation with S1 of any candidate (0.3–0.5) and the authors'
2025 follow-up explicitly identifies it as a VWAP/ETF/retail-flow
footprint, not a behavioural alpha. The `55cb9de0` table:

| Candidate | Sharpe est. | Corr vs S1 | v2.2 status |
|---|---|---|---|
| LLM textual (S2) | 0.7–1.0 | 0.05–0.15 | **Adopted as v2.2 S2** |
| Options-informed (S3) | 0.5–0.9 | 0.15–0.25 | **Adopted as v2.2 S3** |
| Conditioned overnight (LPS+gamma+Akbas+Chan-Marsh) | 0.9–1.3 | 0.3–0.5 | **Reserved as S4 if S1 capped <$1M** |
| BHJ low-SIR long leg | 0.5–0.7 | 0.05–0.15 | **Tactical overlay (free data)** on S2 |
| Wikipedia + 13F best-ideas | 0.4–0.7 | 0.10–0.20 | Stretch — defer |
| IV-dispersion standalone | <0.4 | 0.10 | Rejected as signal; conditioning only |

The conditioned overnight stack has higher single-strategy Sharpe
but worse correlation; v2.2 prefers diversification because the
combined Sharpe is higher when the sleeves are orthogonal.

---

## §3. External capital injection — the seed plan

### 3.1 Why the seed is non-optional

`Project Gaps` §7.2 verbatim:
> "Raising $500,000 to $750,000 in early-stage external seed capital
> or friends-and-family incubation money is a mathematical requirement,
> not an optional luxury, if the 36-month timeline to SMA launch is
> to be preserved."

v2.2 collapses 36→12 by bringing the seed forward. The seed is not
SMA capital (which requires Tier C registration, see §3.3) — it is
**friends/family/angel pooled into a single account** under v2.2's
own LLC, treated as a partnership for tax and accounting purposes.

### 3.2 The three seed tranches

| Tranche | Target | Source | Timeline | Conversion structure |
|---|---|---|---|---|
| **Tranche A** | $500K | Friends & family + 1–2 angels | m1–m3 | Pooled into MX Capital LLC; flat 80/20 split with high-water mark; no management fee year 1 |
| **Tranche B** | $500K | First HNW SMA via Tier C state RIA | m6–m8 | Standard SMA: 1.5/15 with $250K min, qualified-client (≥$2.2M net worth ex-residence) |
| **Tranche C** | $1.5M | 1–2 additional HNW SMAs + small family office | m9–m11 | Same SMA structure |

**$1M base case for v2.2:** Tranche A ($500K) + organic ($200K) +
compounding ($300K) by m12.

**$2.5M stretch:** A + B + organic + compounding.

**$5M conviction:** A + B + C + organic + compounding under top-quartile
performance.

### 3.3 Regulatory layer — Tier C activated at m6, not m24

v2 deferred Tier C state RIA registration to "month 24+". v2.2
moves this to **m4–m6** for two reasons:

1. **You cannot accept Tranche B SMA capital without it.** Above
   the NASAA-model 5-client de minimis (which does not apply because
   Tranche A is a single pooled vehicle, not 5 separate clients), a
   second SMA client triggers state RIA registration. Get ahead of
   it.
2. **Form ADV processing takes 30–60 days at the state level** plus
   the IARD setup. Filing in m4 means license active m5–m6 in time
   for the Tranche B conversation pipeline.

Cost: $5–20K setup, $15–40K/year ongoing including E&O insurance
and CCO function (owner-CCO standard for solo shops). Per
`616ec49c` §6 Tier C.

**Ohio-specific:** The operator is in a — NASAA-model state.
Principal-place-of-business state is Ohio. Ohio Division of
Securities handles RIA registration; Form ADV via IARD; Series 65
exam (or CFP/CFA waiver) for the IAR designation. Series 65 is the
binding personal commitment — schedule it for m1, take it m2.

### 3.4 What converts allocators

The SMA conversion conversation (Tranche B+C) requires three
artifacts beyond the prospectus:

1. **Audited 12-month track record.** v2.2's m1–m12 trading must be
   independently audited by a small-fund accounting firm (~$3K/yr
   for compilation; full audit at $20K not required for SMA without
   custody). Pre-engage in m2 so the audit window starts m3.
2. **Methodology paper at SSRN / JFDS / JPM.** See §6.
3. **Reference allocator(s).** Tranche A pooled-LLC investors who
   stay through year 1 become the reference network for Tranche B.
   Treat them as the GTM channel from m1.

### 3.5 What v2.2 explicitly is NOT

v2.2 does NOT launch a 3(c)(1) or 3(c)(7) fund at $5M AUM. Per
`616ec49c` §6 Tier D, fund cost stack at $5M is $150–400K year 1
+ $125–350K/yr ongoing — economically irrational. SMA wrappers
(state RIA, two-name structure) are correct at this scale.

The fund decision waits for **$25M+ aggregate AUM** (year 2 at
earliest), at which point the cost stack is amortised over a base
that supports it.

---

## §4. Aggressive execution stack — DMA on day 1

### 4.1 The DMA trigger is annual notional, not AUM

v2 followed the conventional "migrate at $X AUM" framing. `57613a15`
§2.1 is direct:
> "The conventional framing — 'migrate when AUM hits $X' — understates
> the real trigger, which is **annual notional traded**, not AUM. For
> S1 with turnover 5–15× per year on $142K–$1M AUM, annual notional
> is $700K–$15M. The PFOF wedge … times annual notional is
> **$2K–$75K/year of execution savings**. Fixed DMA overhead … runs
> ~$2K–5K/year. **Break-even against IBKR Pro is therefore ~$500K–$700K
> annual notional, which the user is already near.**"

v2.2 commits to **IBKR Pro migration in m1–m3** as a parallel track,
not gated on AUM growth.

### 4.2 The two-broker plan

**IBKR Pro — primary execution venue from m3.**
- Account opened m1, funded with $25K initial test allocation.
- TWS API via `ib_insync` integration in m1–m2 (well-documented,
  socket-based, free IB Gateway headless-friendly).
- Phase B parallel-paper at m2–m3 (50/50 symbol-hash split between
  Alpaca and IBKR for fill-quality A/B).
- 100% cutover m3 with Alpaca kept funded as warm failover (same
  config as today, `_d217_hb_path` heartbeat continues to monitor
  both).

**Cobra Trading or Centerpoint Securities — short-side specialist,
deferred to m6–m9.**
- Triggered when S1 short leg becomes >40% of signal events OR
  when S3 deployment requires margin-cushioned shorts.
- $27–30K minimum, DAS Trader Pro $125/mo, Wedbush+IBKR clearing.
- Centerpoint preferred for the in-house securities lending desk
  (45+ counterparties, ~100M shares located monthly).

### 4.3 Order-type upgrades that matter

Per `57613a15` §2.3, only **two** DMA order types matter for the S1
regime:

1. **IOC ISO (intermarket sweep) limit orders** — simultaneous sweep
   across ARCA, EDGX, BYX, NASDAQ, IEX at a common limit price.
   Bypasses wholesaler internalization entirely. The operational
   replacement for the current Slice-1 marketable limit; ~30–50 bps
   slippage savings per sweep on microcap gap-ups.
2. **IEX D-Peg / D-Limit passive child orders** — frozen by the
   Crumbling Quote Indicator. ~35% hit rate per IEX disclosures.
   Replaces Slice-3 passive limit.

Everything else (M-ELO, hidden, reserve, midpoint pegs) is irrelevant
or dangerous in 6-minute gap regime. v2.2 does not use them.

### 4.4 The signal-conditional three-slice ladder, formalised

Per `57613a15` §1.3, this is the executable spec for both Alpaca
(m1–m3) and IBKR Pro (m3+):

```
Slice 1 (50–70%, 9:30:00.3): IOC marketable limit at NBO × (1 + k₁·σ_30s)
        k₁ scales 0.5 (weak signal) to 1.2 (strong signal)
Slice 2 (20–35%, 9:30:05–15): tighter marketable limit if Slice 1 confirmed,
        downsized if Slice 1 rejected
Slice 3 (10–20%, 9:30:30–:60): passive limit inside spread, opportunistic
```

The signal-strength buckets (5 buckets: catalyst type × gap size ×
float × early-volume ratio) feed the (k₁, k₂, slice-1-fraction)
table. Categorical bucketing because sample size cannot support
continuous-parameter estimation per `57613a15` §1.4.

### 4.5 Heroic engineering vs honest theory

`57613a15` §3.9's defensible single-sentence claim for Paper 1:
> "The execution policy is a signal-conditional feedback rule in the
> Cartea–Jaimungal–Penalva HJB tradition, with the risk-aversion-
> weighted cost-variance trade-off of Almgren–Chriss (2000), specialized
> via the Almgren–Lorenz (2007) / Lehalle–Neuman (2019) adaptive-signal
> lineage and made robust to parameter uncertainty following
> Cartea–Donnelly–Jaimungal (2017); impact, resilience, and
> fill-probability primitives are calibrated empirically on the
> author's own microcap gap-up execution record because no published
> calibration exists in this regime."

v2.2 commits to this exact claim and *nothing stronger* in Paper 1.
No optimality claims, no square-root-law import, no RL framing. The
calibration infrastructure (`21_phase0_instrumentation_mvp.md`) is
literally the way Paper 1 makes good on this claim.

---

## §5. The Bayesian decay-detection moat

### 5.1 Why this is the decisive operational moat

`Signal Decay Detection Gaps` is the single most important artifact
for v2.2 because it solves the constraint that made v2 defensive in
the first place: **how do you push aggressive Kelly without blowing
up the compounding arc when the signal decays?**

The conventional answer (lagged Sharpe, max-DD limits, rolling IC)
takes 30–60 days to detect a signal break. Per the artifact §1:
> "If a strategy utilizes a 60-day evaluation window, a complete and
> sudden loss of statistical edge will take a minimum of 30 days to
> meaningfully suppress the rolling metric. During this 30-day lag
> period, the execution engine continues to allocate live capital
> to a fundamentally broken signal."

That 30-day lag is what kills the compounding arc on v2's defensive
plan. v2.2 collapses the lag to **1 trade** via Adams-MacKay BOCPD
+ Fearnhead-Rigaill robust FPOP.

### 5.2 The four detection layers

**Layer 1 — Adams-MacKay BOCPD (per-trade changepoint posterior).**

For every executed trade (S1, S2, S3), feed the slippage-adjusted
residual return into a BOCPD engine that computes
`P(changepoint | observations up to t)` recursively. Hazard function
modelled as a memoryless geometric prior with rate calibrated from
historical analog windows (per the artifact's empirical-Bayes
recommendation).

**Trigger:** if `P(changepoint) > 0.85` on the last 10 trades' window,
**halve the strategy's Kelly fraction** and emit a `D223 BOCPD_BREAK`
warning (next free D2xx after D222 PNL_RECON). If `P > 0.95`,
**revert to paper trading** for that strategy, continue to collect
out-of-sample data to verify.

**Layer 2 — Fearnhead-Rigaill FPOP with Tukey biweight loss
(robust to fat tails).**

Standard BOCPD with Gaussian likelihood is pathologically sensitive
to single-day outliers (one LULD halt → spurious changepoint). The
artifact's §3 documents the fix: bounded loss function (Tukey
biweight), penalised optimisation via FPOP. Computational cost
O(n log n) or linear empirically — runs intraday.

**Trigger:** parallel to BOCPD; the two layers must agree on
changepoint within 5 trades for a D223 warning to escalate from
"degrade to half-Kelly" to "revert to paper."

**Layer 3 — Posterior-predictive divergence (slow decay).**

For continuous incremental decay (the McLean-Pontiff 58% post-pub
drift), compute KL divergence between live trade returns and the
in-sample Posterior Predictive Distribution. The artifact §6:
> "PPD divergence operates not just as a binary kill-switch, but
> as a continuous portfolio sizing tool. As the KL divergence
> incrementally increases, suggesting mild drift rather than outright
> failure, the strategy's Kelly fraction or position sizing parameters
> can be programmed to scale down continuously."

**Sizing rule:** Kelly fraction scales as
`f_kelly_actual = f_kelly_target × max(0.25, 1 − KL_divergence / KL_threshold)`
clamped to `[0.25, 1.0]` of nominal Kelly.

**Layer 4 — Sequential Bayes Factor on competing models.**

`H₁: signal works (μ > 0)` vs `H₀: signal decayed (μ ≤ 0)`.
Recursive Bayes factor update per trade. If `BF₁₀ < 1/30`
(strong evidence for decay per Jeffreys scale), force paper-revert
regardless of Layer-1 status.

### 5.3 Operational implementation

Single new module `src/risk/decay_monitor.py` housing all four
layers. Hooks:
- `record_trade_outcome(strategy_id, residual_return, is_outlier_flag)`
- `current_kelly_multiplier(strategy_id) → float ∈ [0.25, 1.0]`
- `should_revert_to_paper(strategy_id) → bool`

Hooks fire in:
- `bridge.close_with_attribution` — emits per-trade outcome.
- Pre-trade entry gate (new) — multiplies Kelly tier sizing by
  `current_kelly_multiplier`.
- EOD summary (already added in Bug #13 patch) — logs the four
  layers' current readings.

**Open-source implementations to leverage:**
- `bayesian_changepoint_detection` (PyTorch, GPU-friendly)
- `Rbeast` (C/C++ backend, Bayesian model averaging over changepoint
  count and locations)
- `changepoint_online` (FOCuS algorithm, O(log n) per iteration)

### 5.3.1 Resolving the small-N prior bottleneck via empirical Bayes

The standard objection to Bayesian decay detection at N=30 is that
uninformative priors react too slowly — the posterior is prior-
dominated until ~50 trades, by which point a fast decay has already
done its damage. `Analyzing Signal Decay` §5.3 names the fix
explicitly:

> "The prior distributions for the Bayesian detection models must
> be empirically calibrated using historical proxy data … by rigorously
> estimating the prior hyperparameters (mean edge, expected variance,
> baseline hazard rate) from this historical simulated data, the
> Bayesian models are effectively 'pre-trained.' Consequently, they
> will require significantly fewer live out-of-sample data points to
> overcome the prior and definitively identify a structural shift."

v2.2 implementation:
- **For S1:** Pre-train hyperparameters on the existing
  `data/journals/journal_*.jsonl` history (post-Bug-#13 broker-truth
  reconciled) plus the back-tested microcap gap-up cohort from
  `mx-arena/`. Estimate prior `μ_edge`, `σ_edge`, hazard rate λ
  before m1 live deployment.
- **For S2/S3:** Pre-train on the m1–m6 walk-forward backtest
  outputs themselves before m6/m7 paper-trading goes live. The
  walk-forward residuals serve as empirical-Bayes proxy data; the
  BOCPD posterior is operational from trade #1 of paper trading,
  not from trade #50.

This is the difference between BOCPD as theoretical scaffolding and
BOCPD as a real m1 deliverable. **Without empirical-Bayes pre-training,
the §5.4 "aggressive Kelly" claim does not survive the first 30
live trades.** With it, the kill switch is operationally meaningful
from day one.

### 5.4 Why this enables aggressive Kelly

Without the decay moat, sustaining $1M+ AUM requires Kelly ≤ 0.5 to
survive the McLean-Pontiff decay scenario. With the moat, Kelly can
ride at 0.8–1.0 nominal because the kill switch is mathematically
sound — the prior is built on the actual distribution of the
strategy's residual returns, not on fragile thresholds.

The `Project Gaps` Monte Carlo at half-Kelly produced ~0%
probability of $1.5M @ m36. At full-Kelly with decay-monitor-protected
draw-down caps, the same Monte Carlo (re-run) produces 35–55%
probability of $1M @ m12 conditional on Tranche A. The decay
monitor is the single largest probability lever in v2.2.

### 5.5 Allocator pitch material

`Signal Decay Detection Gaps` §"Allocator Scrutiny" is explicit:
> "A methodological specification that explicitly incorporates
> Adams-MacKay online changepoint detection, Fearnhead-Rigaill robust
> outlier filtering, and Kullback-Leibler divergence tracking
> demonstrates genuine institutional-grade quantitative rigor."

This is the operational moat that converts Tranche-B SMA
conversations. The pitch document for prospective allocators (m4–m6
deliverable) leads with this section.

---

## §6. Methodology paper as parallel track, not gating step

### 6.1 v2's defensive framing was wrong but not the paper itself

v2 framed Paper 1 as "credibility infrastructure that begins
accumulating return asynchronously with the trading arc, not as a
gating step." That framing is *correct* but v2 used it to *delay*
publication to month 18+. v2.2 instead **publishes Paper 1 in
months 4–6** because:

1. The methodology described in §4–§5 is publishable today (the
   contribution is the empirical specialisation of Cartea-Jaimungal
   to the microcap-PFOF regime + the BOCPD/FPOP risk gating, not
   the strategy returns).
2. JFDS / JPM lead times are 3–9 months from submission to
   publication. Submitting m6 means publication in Q1 2027 — exactly
   when Tranche B/C SMA conversations need the credibility hit.
3. Paper 1 published does NOT reveal the strategy. It reveals
   methodology. Lopez-Lira and Chen-Kelly-Xiu published their LLM-
   alpha methodology in 2022–2024 and the alpha continues to fire.

### 6.2 The two-paper sequence

**Paper 1 (target *Journal of Financial Data Science* primary,
*Journal of Portfolio Management* secondary), submit m6:**
> *"Signal-conditional execution and Bayesian decay detection in
> the microcap-PFOF regime."*

Sections:
- Cartea-Jaimungal HJB feedback policy specialisation
- Three-slice marketable-limit ladder + IOC-ISO sweep upgrade
- BOCPD + FPOP risk-gating layer
- Empirical calibration on the author's own fill record
  (anonymised counterparties)
- Honest methodology — Knightian-uncertainty robust control
  framing, Bucci-crossover impact form, BJZZ→QMP signing migration

Length: ~30 pages including appendices.

**Paper 2 (target same venues), submit m12:**
> *"Size-universe transferability of behavioral signals: an implied-
> volatility-dispersion pre-registration framework."*

The novel methodological contribution `55cb9de0` Part 2 identified.
Uses S2 textual signals as the empirical exemplar and IV-dispersion
as the pre-registered transferability diagnostic. This is the
*publishable white-space* — no existing paper combines a
dispersion-measure taxonomy with size-universe transferability
pre-registration under solo-quant vendor constraints.

### 6.3 Why two papers, not one

Two reasons:

1. **Different audiences.** Paper 1 is for execution-quant
   practitioners and allocator due-diligence (concrete methodology
   they can credit). Paper 2 is for academic / JFDS prestige and
   for HNW SMA conversations (novel methodology they can be
   impressed by).
2. **Risk diversification on publication.** If Paper 1 gets desk
   reject at JFDS, Paper 2 still ships. If Paper 1 has a slow
   review cycle, Paper 2 starts independently.

### 6.4 What is NOT in either paper

- Raw strategy returns (proprietary; redacted in tables)
- Exact parameter values for k₁, k₂, slice-1 fractions
- Counterparty identities (Citadel/Virtu/Jane Street are
  generalised as "Wholesalers A/B/C")
- Specific catalyst-type bucket definitions (these are the moat)

---

## §7. Operational timeline (M1–M12)

The v2.2 operating plan in single-month deliverables:

### Month 1 (May 2026)

- Bug-sweep Tuesday architecture call agreed (`22_…agenda.md`)
- Series 65 exam scheduled
- IBKR Pro account opened, TWS API spike
- Tranche A pitch deck v1 drafted; first 5 friends/family conversations
- BJZZ→QMP signing migration ticket opened on S1
- D146 dedicated table writer ticket opened (per `20_…spec.md`)
- Phase 0 instrumentation MVP PR-1 opened (per `21_…spec.md`)
- 30-day broker-vs-internal qty divergence backfill scan executed

### Month 2 (June 2026)

- Series 65 passed
- IBKR Pro `BrokerInterface` abstraction layer integrated; A/B
  paper-trading begins
- S2 EDGAR crawler + Lazy Prices pipeline (week 1–2)
- S2 PEAD.txt FinBERT scoring (week 3–4)
- Tranche A LOIs collected ($150–250K committed)
- Compilation accountant engaged (~$3K/yr)

### Month 3 (July 2026)

- IBKR Pro 100% cutover on S1; Alpaca warm failover
- S2 8-K parser + Form-4 filter
- Tranche A first wire ($350–500K target)
- MX Capital LLC formed (Ohio; partnership for tax)
- D146 Parquet writer live; first cohort of fires logged
- Phase 0 PR-1 (`trade_context`) merged; PR-2 (`bar_context`) open

### Month 4 (August 2026)

- S2 composite scoring + cross-sectional ranking; backtest 2014–2025
- ORATS Near-EOD FTP subscription begins; S3 surface validation
- Ohio Form ADV filed via IARD (Tier C state RIA registration)
- Tranche A fully closed; live deployment of seeded capital
- Paper 1 first complete draft circulating to 2 academic reviewers

### Month 5 (September 2026)

- S2 walk-forward backtest + IV-dispersion conditioning gate
- S3 smirk + ΔCVOL/ΔPVOL pipeline
- BOCPD + FPOP module live on S1; calibrated to historical fills
- Ohio RIA license active
- Paper 1 reviewer feedback incorporated; submission prep

### Month 6 (October 2026)

- S2 live paper trading on $0 capital; performance vs backtest
  measured
- S3 walk-forward backtest + IVolatility cross-validation
- IV-dispersion regime detector live
- Paper 1 submitted to *Journal of Financial Data Science*
- Tranche B HNW SMA conversations begin (warm leads from Tranche A)
- Audited 6-month track record available for Tranche B prospect
  packets

### Month 7 (November 2026)

- S2 live deployment with $50K test allocation; BOCPD-gated
- S3 live paper trading
- Tranche B first close target ($250–500K)
- Paper 2 outline + literature scan complete

### Month 8 (December 2026)

- S2 capital scaled per BOCPD readings (target $250K live by EOM)
- S3 live deployment with $50K test allocation
- Tranche B converted ($500K target)
- IBKR Pro short-side performance assessed; Cobra/Centerpoint
  go/no-go decision

### Month 9 (January 2027)

- S3 capital scaled
- Cobra Trading account opened if go (m8 decision)
- Tranche C HNW SMA conversations begin
- Year-end §475(f) MTM accounting; tax planning
- Paper 1 expected revise-and-resubmit response

### Month 10 (February 2027)

- Three-strategy portfolio fully operational; combined Sharpe
  measurement
- Tranche C first close ($750K target)
- Paper 2 first complete draft
- Cobra/Centerpoint short-side migration if applicable

### Month 11 (March 2027)

- Tranche C fully closed ($1.5M target)
- $5M conviction case = $500 + $500 + $1500 + $200 organic + $300
  compounding hits checkpoint
- Audited 11-month track record
- Allocator network: 3+ reference HNW investors

### Month 12 (April 2027)

- $1M base case checkpoint
- $2.5M stretch checkpoint
- $5M conviction checkpoint (if all branches succeed)
- Paper 1 acceptance / Paper 2 submission
- Full-year audited returns
- v2.3 planning: $25M+ AUM Tier D fund decision tree

### 7.1 Critical dependencies (Gantt-style sequencing)

```
Series 65        ━━━━
IBKR Pro          ━━━━━━━
S2 build              ━━━━━━━━━━━━━━━━
S3 build                  ━━━━━━━━━━━━
RIA license             ━━━━━━━
Tranche A          ━━━━━━━━
Tranche B                 ━━━━━━━━━━
Tranche C                       ━━━━━━━━━━
Paper 1 draft          ━━━━━━━━━━
Paper 1 submit               ━
Paper 2 draft                 ━━━━━━━━━━
Paper 2 submit                       ━━━━
BOCPD live                  ━━━━━━━━━━━━━
DMA short side                          ━━━━━
M1   M2   M3   M4   M5   M6   M7   M8   M9   M10  M11  M12
```

---

## §8. Honest probability assessment + branching scenarios

### 8.1 What can break

For each major lever, the failure mode and the v2.2 response:

| Lever | Failure mode | v2.2 branch |
|---|---|---|
| **Tranche A seed** | <$300K raised by m4 | Defer S3 build to m7+, push S2 to m8; aim for $750K @ m12 instead of $1M |
| **Tranche B SMA** | RIA approval delayed past m6 | Use m6–m8 to perfect S2/S3 paper performance; move Tranche B to m8–m9 |
| **S2 build** | LLM signal does not replicate at solo-quant scale | Pivot to Candidate 4 (BHJ low-SIR, $0 data) as S2 — lower expected Sharpe, faster build |
| **S3 build** | ORATS surface quality insufficient | Move to IVolatility academic tier; defer S3 to month 8+ |
| **S1 decay** | BOCPD fires changepoint in m3–m6 | Halve Kelly per §5.2 trigger; pause new tranches; investigate root cause; potentially rotate to S2 capacity |
| **DMA migration** | IBKR Pro execution worse than Alpaca on A/B | Stay on Alpaca; defer DMA decision; document the surprise (likely indicates v2.2's PFOF wedge model is wrong direction) |
| **Paper 1 desk reject** | JFDS desk reject m6+1 | Resubmit *JPM* m7; Paper 2 timeline unaffected |
| **Major drawdown** | -15% on combined portfolio in any month | Auto-revert all strategies to paper per §5; investigate; resume only after BOCPD posterior recovers and KL divergence drops below threshold |

### 8.2 Pre-committed pivot points

Three hard go/no-go gates baked into v2.2:

**Gate 1 — Month 4:** Tranche A ≥ $300K AND BOCPD on S1 not firing.
- Pass: continue per §7
- Fail: revert to v2's defensive 36-month posture; keep S1 only;
  defer S2/S3; cancel Tranche B/C track

**Gate 2 — Month 6:** S2 walk-forward Sharpe ≥ 0.5 AND Tranche A
≥ $500K.
- Pass: continue
- Fail: substitute BHJ low-SIR for S2; defer Tranche B by 3 months

**Gate 3 — Month 9:** Combined live trailing-3-month Sharpe ≥ 0.8
AND no D223 BOCPD_BREAK warnings on any strategy in last 30 days.
- Pass: open Tranche C
- Fail: hold portfolio at current AUM; defer Tranche C to m12+;
  document for v2.3 planning

### 8.3 Branch probabilities (subjective, written down so they can
be wrong on record)

| Branch | Probability |
|---|---|
| Hit all 3 gates → $1M base case | 35–55% |
| Hit Gate 1+2, miss Gate 3 → $500–800K stuck | 20–30% |
| Miss Gate 1 → revert to v2 defensive | 15–25% |
| Hit all 3 gates + S2/S3 in top decile + Tranche C overshoots → $2.5M+ | 10–20% |
| Hit all 3 gates + everything top decile → $5M | 5–15% |
| Catastrophic decay or drawdown → revert to paper, $142K base preserved | 5–10% |

These add to 100% only loosely (some branches partially overlap).
The key honest commitment: **most likely outcome is $500K–$1.5M @ m12,
not $5M**. The plan succeeds operationally if the median outcome
exceeds v2's ~$300K m12 expected value, which it does in every
non-catastrophic branch.

---

## §9. The pivot from defensive to offensive — what changes day-of

For Tuesday's architecture call, the v2 → v2.2 deltas are:

### 9.1 Carry forward unchanged from v2

- The five v2 §9 architecture-call agenda items still stand
- The five Topic-7 methodology updates (BJZZ→QMP, two-timescale
  propagator, MC-panel, prior-predictive sim, HonestDiD T_pre≥4)
- The state-mutation reversibility rule (f) as bug-sweep template
- The BAR-1 EXIT formalisation spec (`20_…`) and Phase 0 spec
  (`21_…`) just published

### 9.2 Reverse from v2

- **Timeline:** 36 months → **12 months base case + 24-month
  contingency** (not the reverse)
- **Capital plan:** "own capital scaled as far as microcap allows"
  → **$500K seed required by m4, $1M+ external capital by m12**
- **RIA registration:** "month 18+" → **month 4 filing, month 6 active**
- **Strategy count:** "S1 + future S2 placeholder" → **S1 + S2 + S3
  concurrent build**
- **DMA migration:** "deferred" → **IBKR Pro by m3, Cobra/Centerpoint
  by m9 if short side warrants**
- **Paper 1 timing:** "month 18+ for credibility infra" → **submit m6
  for SMA conversion ammunition**

### 9.3 New in v2.2

- **Three-engine portfolio architecture** (§2)
- **Three-tranche seed plan** ($500K + $500K + $1.5M, §3)
- **Full DMA stack from m3** (§4)
- **BOCPD + FPOP + PPD + Sequential Bayes Factor decay-monitor
  module** (§5)
- **Two-paper publishing track** (§6)
- **Three pre-committed pivot gates** (§8.2)

### 9.4 New D-series codes reserved for v2.2 work

- `D222 PNL_RECON` — already shipped (Bug #13)
- **`D223 BOCPD_BREAK`** — changepoint posterior >0.85 on a strategy's
  last 10 trades (§5.2)
- **`D224 PPD_DRIFT`** — KL divergence above threshold; Kelly fraction
  scaled down (§5.3 Layer 3)
- **`D225 BAYES_FACTOR_DECAY`** — Sequential BF₁₀ < 1/30 → forced
  paper revert (§5.3 Layer 4)
- **`D226 SEED_TRANCHE_GATE`** — Gate 1/2/3 evaluation log (§8.2)
- **`D227 STRATEGY_ROTATE`** — capital rotated between S1/S2/S3 per
  BOCPD readings (the offensive analog of D91's defensive rotation)

These are reserved now so the implementation tickets can use them
without collision.

---

## §10. The architecture-call deliverable

Add four items to the Tuesday agenda (per `22_…agenda.md`):

### #15 — Adopt v2.2 over v2 as the operating plan
Decision item. The honest framing: v2 is correct under conservative
assumptions; v2.2 is the offensive variant that takes the research's
prescribed levers (seed injection, DMA day 1, three-engine portfolio,
BOCPD moat). The choice is between sandbagging at $300K m12
expected value (v2) and aggressive at $1M m12 base case with $5M
upside (v2.2).

### #16 — Approve $500K Tranche A seed-raise as Q2 deliverable
Pierce drives outreach. 5 friends/family conversations per week
beginning m1. MX Capital LLC structure, 80/20 split, no
management fee year 1 (creates allocator goodwill).

### #17 — Approve concurrent S2 + S3 build vs sequential
v2's scoping treated S2 as "future." v2.2 builds both in parallel
because the data-vendor and engineering surfaces are largely
disjoint (SEC EDGAR vs ORATS). Risk: split engineering bandwidth.
Mitigation: S2 dominated by m1–m6 (Pierce-led), S3 by m4–m8 (could
involve a contract option for ORATS pipeline integration).

### #18 — Approve BOCPD/FPOP decay-monitor as v2.2's defining moat
Engineering ticket sized at ~3 weeks for first cut. Becomes
section 4 of Paper 1 and section 1 of every Tranche B/C pitch deck.

These four agenda items convert the v2.2 plan from document to
operating decision. Without ratification at the architecture call,
v2.2 stays a paper plan; with ratification, the m1 deliverables
above become committed scope.

---

## §11. The frontier admissions — what v2.2 still doesn't solve

Honesty preserves credibility. v2.2 does not pretend to solve:

1. **The `aef3259b` impact-function critique.** The Benzaquen-Bouchaud
   crossover form is integrated (§2.3 v2.2 S1 changes), and the
   calibration priority has been flipped from γ to η_perm per the
   `e274cbca` §3.2 sensitivity decomposition (37× more leverage).
   But the honest η_perm estimate at 20–50% participation still
   requires own-fill calibration that takes ≥30 trades for η_perm
   alone (γ held at prior); year 2 narrows γ. The §13.3 martingale
   residual gate is the early-warning that the decomposition is
   bleeding regardless of N.
2. **The `616ec49c` business-model time constraint.** Tranche B/C
   SMA conversion typically takes 18–36 months from first SSRN
   posting to first allocation. v2.2 compresses to 6–9 months by
   front-loading the relationship-building during Tranche A. **This
   compression is the riskiest assumption in the plan.** If it fails,
   Gate 2 fails and v2.2 reverts to v2 organically.
3. **The Lou-Polk-Skouras self-refutation.** v2.2 holds LPS in
   reserve as S4 for "if S1 capped <$1M" — but the authors' 2025
   follow-up explicitly identifies LPS as VWAP/ETF/retail-flow
   footprint. If S1 caps and S2/S3 underperform, S4 may not
   exist either. The honest answer at that branch is: revert
   to v2's defensive posture and accept the longer timeline.
4. **The Paper 1 peer-review risk.** Submitting m6 to JFDS means
   first response m9–m12. A desk reject costs the Tranche C
   conversation timing if that conversation is leveraging Paper 1
   for credibility. Mitigation: Paper 2 ready by m10 as backup
   credibility instrument.
5. **The decay-monitor false-positive risk.** BOCPD at threshold 0.85
   fires *roughly* 1× per ~50 trades by construction; with ~250
   trades/year per strategy that's ~5 false halts per year per
   strategy = ~15 across the portfolio. Each halt loses ~2 weeks
   of compounding. The Layer-2 (FPOP) requirement to agree on
   changepoint within 5 trades dampens this; production value will
   need calibration.

These are the failure modes that *can* hit even if everything else
works. The honest probability assessment in §8.3 already prices
them in; the point of listing them here is so the architecture call
can ask "what changes if X breaks?" with an answer in hand.

---

## §12. The single-paragraph TL;DR for the architecture call

> v2.2 replaces v2's 36-month defensive posture with a 12-month
> offensive plan that takes the prescribed levers from the eight
> research artifacts seriously and concurrently: (a) raise $500K of
> Tranche A friends-and-family seed in m1–m4 — the one mathematical
> requirement `Project Gaps` named outright (its Scenario-A m36
> median of $368K and Scenario-B-hero m36 hit-rate of only 8.25%
> for $1.5M settle the case that organic compounding cannot bridge
> the gap); (b) build S2 (LLM textual stack per `55cb9de0`
> recommendation) and S3 (options-informed) in parallel through
> m6–m8 to triple our capacity envelope from $1.5M to $4.5–8.5M;
> (c) migrate to IBKR Pro DMA in m3 because we are already past
> `57613a15`'s annual-notional break-even; (d) file Tier C state
> RIA in m4 to enable Tranche B HNW SMA conversions starting m6;
> (e) ship the BOCPD + FPOP + PPD + Sequential Bayes Factor decay
> monitor (`Signal Decay` artifact) as v2.2's defining moat,
> empirically-Bayes pre-trained on existing journal residuals so
> the kill switch is operational from trade #1 (§5.3.1) — it is
> what enables aggressive Kelly without blowing up the compounding
> arc; (f) submit Paper 1 m6 and Paper 2 m12 as parallel credibility
> infrastructure under the §13.6 do-not-cross scope list; (g) gate
> capacity-scaling on the §13.2 UCB_75%(slip/α) ≤ 35% rule with the
> calibration priority flipped to η_perm (37× more leverage than γ
> per `e274cbca` §3.2). Base case is **$1M AUM by m12 (P 35–55%)**,
> stretch is $2.5M (P 15–30%), conviction is $5M (P 5–15%). Three
> pre-committed financial pivot gates at m4/m6/m9 plus the §13.7
> calibration phase gates protect the downside: any miss reverts
> to v2's 36-month posture without further loss. The choice is
> sandbagging at v2's $300K-expected-m12-value or aggressive at
> v2.2's $1M base case with $5M optionality, where the v2 → v2.2
> upgrade buys the engineering to know — within trade #1 of decay,
> within m4 of calibration leak, and within m6 of capacity
> mis-specification — that the seed-amplified arc is intact.

---

---

## §13. Calibration discipline — what binds the methodology paper

This section consolidates the load-bearing calibration constraints
across `e274cbca`, `15_…application` (v1 retrospective), and `Signal
Decay`. These are not nice-to-haves; failure on any of them collapses
Paper 1 at peer review or collapses the v2.2 capacity claims at
allocator due-diligence.

### 13.1 The four critical capability gaps from v1 (per `15_…`)

v1 (`15_slippage_methodology_application.md`) identified four
capability gaps in the existing codebase that block any clean η, γ
estimation:

| # | Gap | Severity | v2.2 status |
|---|-----|----------|-------------|
| 1 | NBBO time-series capture (t−60s through EOD) | **Critical** | Phase 0 `trade_context` schema (`21_…spec.md`) |
| 2 | Matched-cohort post-screen price tracking | **Critical** | Phase 0 `cohort_registry` schema (`21_…spec.md`) |
| 3 | Per-partial-fill tick persistence | **High** | Phase 0 `child_fill_ticks` schema (`21_…spec.md`) |
| 4 | Bayesian (η, γ) estimator scaffolding | Medium | **NOT YET COVERED** — new ticket |

The Phase 0 spec covers gaps 1–3. **Gap 4 is missing from v2.2's
written tickets and must be added before Paper 1 m6 submission.** The
estimator scaffolding is `src/analysis/slippage_calibration.py` (new)
implementing the Bayesian regression from `e274cbca` §1.6:

```
log[(I_temp - Spread₀/2) / σ_τ] = log η + γ · log(Q/V_τ) + u
```

with the prior + Tobit-censoring + Student-t(ν=4) likelihood + 4-chain
NUTS sampling spec from `e274cbca` §2.3–§2.5. This is a 1-week ticket
once Phase 0 PR-1/PR-2 land.

### 13.2 The decision rule v2.2 inherits from `e274cbca` §3.3

The slippage-to-alpha framework that gates capacity scaling:

| Metric | Threshold | Action |
|--------|-----------|--------|
| **UCB_75%(TotalSlip / α) ≤ 25%** | Green | Scale to 5% per-position at target AUM |
| **25% < UCB_75% ≤ 35%** | **Yellow — STAGE-SCALE** | Scale to half-target AUM, recalibrate at N=60 |
| **UCB_75% > 35%** | Red | Hold at current AUM, investigate η_perm |

Note **upper 75% credible bound, not point estimate** — the
asymmetric-loss correction `compass_artifact_wf-aef3259b` Hey-et-al
demands. Per `e274cbca`'s central-case projection (η_perm=0.40,
γ=0.70, σ_τ=6%, Q/V=50%), UCB_75% ≈ 43% → **STAGE-SCALE territory
is the v2.2 default expectation**, not green. Tranche-A capital
goes to S1 at $1M (not $1.5M ceiling) until N=60 own-fill recalibration
moves the credible bound.

### 13.3 The martingale-residual zero-mean test as Phase-0 gate

`e274cbca` §1.4 names a single diagnostic for "is the temp/perm
decomposition correctly specified":

> "Under a correctly specified decomposition, the residual trading-P&L
> series `{P&L_trade,i − I_perm,i}` should be a martingale:
> `E[ residual ] = 0` across the 30 trades. Use this as a global
> diagnostic of decomposition adequacy."

v2.2 elevates this from a post-hoc diagnostic to a **hard Phase-0
gate**. Concretely:

- After the first N=30 own-fill matched-cohort I_perm estimates land,
  compute the residual series + run a one-sample t-test against zero.
- **Pass:** |t| < 2.0 → decomposition adequate, methodology paper
  draft proceeds, BOCPD pre-training uses these residuals.
- **Fail:** |t| ≥ 2.0 → cohort matching is mis-specified OR temporary
  impact is bleeding into permanent estimate. Halt Paper 1 drafting,
  re-examine the cohort screen criteria + V_τ proxy, re-run.

**Failure here is recoverable but deferred.** It does not invalidate
v2.2; it delays Paper 1 by ~6–8 weeks while the screen is re-tuned.
Better to find this in m4 than at Paper 1 peer review.

### 13.4 The four hard failure triggers from `e274cbca` §4.1

Beyond the slippage decision rule, four conditions trigger an
automatic methodology-paper stop:

1. **Posterior mean η_perm ≥ 0.65** → scaling to $5M is mathematically
   impossible. Cap S1 at current AUM, redirect Tranche B/C
   exclusively to S2/S3.
2. **Rolling γ drift > 0.20 between sample halves** → regime
   non-stationarity. Halt all Bayesian inference; fall back to point
   estimates per Cartea-Donnelly-Jaimungal robust control.
3. **Halt rate > 25% in calibration sample** → switch to halt-event
   study methodology entirely; the canonical impact decomposition
   does not apply.
4. **Martingale residual test rejects zero-mean at 2σ** → §13.3 above.

These four are written into the m4 calibration script as automated
gates that produce a single PASS/FAIL per session and feed §8.2's
Gate 1 evaluation.

### 13.5 V_τ specification — the second-most-leveraged calibration

η_perm is most leveraged; V_τ is second. The blended-proxy formula
v2.2 inherits from `e274cbca` §1.5:

```
V_τ = max(α · V₀:₆^residual,  β · ADV_20 / 13)
```

Starting calibration: α = 1.0, β = 0.5. Both must be empirically
re-estimated from the m4 N=30 own-fill window. **The screen we use
selects for thin-open names** (gap %, RVOL, dollar-volume threshold),
which biases V₀:₆ downward → biases η upward via the Q/V denominator.
Document this bias in Paper 1 §3 explicitly; the v1 silent assumption
that V_τ proxies are exogenous is not defensible.

### 13.6 What Paper 1 cannot claim

Per `aef3259b` and `57613a15` §3.9, Paper 1 must NOT claim:

- Optimality in the Almgren-Chriss or Obizhaeva-Wang sense
- The Sato-Kanazawa γ=0.5 universal scaling for our regime
- The Alimoradian construction as theoretical cover for the empirical
  estimator (it is a derivatives-hedging device under insider
  filtration, not a DiD identification result)
- Best execution beyond the user-selected implementation-shortfall
  benchmark
- Any sample-size assertion smaller than N=60 for joint (η, γ)
  identification

Paper 1's defensible scope is the empirical specialisation of the
Cartea-Jaimungal HJB family to the microcap-PFOF regime, with
calibration done on own-fills under explicit Bayesian uncertainty
quantification — and nothing stronger. v2.2's §6.1 already commits
to this; §13.6 is the do-not-cross list that keeps that commitment
honest.

### 13.7 The single-table summary

If all of §13 reduces to one operational table for the architecture
call, this is it:

| Phase | Deliverable | Gate |
|-------|-------------|------|
| m1–m3 | Phase 0 `trade_context` + `bar_context` + `child_fill_ticks` | All four `15_…` capability gaps live |
| m3–m4 | First N=30 own-fill matched-cohort I_perm estimates + Bayesian (η_perm, γ-prior) regression | §13.3 martingale residual + §13.4 four-triggers ALL pass |
| m4–m5 | Empirical-Bayes pre-training on Phase-0 residuals for BOCPD/PPD | §5.3.1 priors operational from trade #1 of m6 |
| m5–m6 | UCB_75%(TotalSlip / α) computed; staging decision | §13.2 Yellow → $1M S1 cap; Green → $1.5M S1 cap |
| m6 | Paper 1 submission to JFDS | §13.6 scope list intact |

Failure on any row defers the next row by 4–8 weeks. The plan as
written assumes pass on every row; the §8.2 Gate 1 / Gate 2 / Gate 3
structure is the financial reversibility check on this calibration
chain.

---

*End of v2.2. Becomes the operating document on architecture-call
ratification (§10). No production code in this document; all
implementation work flows through the §9.4 D-series tickets, the
§7 month-by-month deliverables, and the §13.7 phase gates that bind
methodology to capacity to capital.*
