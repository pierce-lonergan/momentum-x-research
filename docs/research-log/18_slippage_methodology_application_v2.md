# 18 — Slippage Methodology Application, v2

**Wed 2026-04-22 (morning, post-Phase-3 restart) — successor to
[`15_slippage_methodology_application.md`](./15_slippage_methodology_application.md).
Planning document, no code.**

Source research:
- `compass_artifact_wf-e274cbca...` — "Live-fill slippage methodology for
  microcap gap-up momentum at $5M AUM" (Alimoradian-derived estimator)
- `compass_artifact_wf-aef3259b...` — "Frontier critique of the microcap
  gap-up DiD plan" (adversarial spot-check at Rambachan–Roth / Abi Jaber–Neuman
  / Sato–Kanazawa / Schwarz-et-al. level)
- `compass_artifact_wf-616ec49c...` — "Upstream research for MOMENTUM-X
  v2.2: scaling-arc evidence and adjacent literature" (Hou–Xue–Zhang,
  Jensen–Kelly–Pedersen, Aggarwal–Jorion, SEC/CFTC regulatory map)

This v2 supersedes v1's theoretical framing, tightens its empirical
identification, and replaces its business-model framing entirely.
Every v1 claim that survives is explicitly marked; everything else
is rebuilt. v1 remains as the historical record of the plan as I
originally saw it; reviewers should read v1 first to understand what
the critique changed.

---

## 0. TL;DR — three findings that rewrite the plan at three levels

The three new research artifacts each operate at a different level
of the stack. Read together they do not just "refine v1." They
**invalidate v1's theoretical justification, bias v1's empirical
estimator by 2–5×, and compress v1's business-model timeline by a
factor of 3 into an architecture v1 did not contemplate**. I'll
name the three at the top so the rest of the document can develop
each:

**Finding 1 — Theoretical: the Alimoradian paper was never going
to license what v1 asked it to do.** v1 built the estimator on a
portable-object extraction from Alimoradian–Barigou–Eyraud-Loisel
(2026). The frontier critique (aef3259b) verifies the paper exists
as v1 cited it but shows the invocation is a **category error**:
Alimoradian's `𝕀*` is an insider-filtration change-of-measure for
derivatives hedging; v1 translated it into "matched non-traded cohort"
as a counterfactual for empirical impact estimation. The paper's
`𝕀*` is not a DiD counterfactual and was never going to be. v1's
theoretical anchor is salvageable only as inspiration. The correct
anchor is the **propagator literature** (Bouchaud–Bonart–Donier–Gould;
Abi Jaber–Bondi–De Carvalho–Neuman–Tuschmann 2025
["Fredholm Approach to Nonlinear Propagator Models"](https://arxiv.org/abs/2503.04323);
Hey–Bouchaud–Mastromatteo–Muhle-Karbe–Webster 2024 "Cost of
Misspecifying Price Impact"). **The estimator needs a theoretical
rebuild, not a patch.**

**Finding 2 — Empirical: v1's DiD is broken by signal-driven
selection, and its √ law doesn't live in our regime.** Two serious
identification failures compound. The **selection failure**: v1's
median-of-cohort counterfactual is unbiased only under parallel
trends, but *the signal is the selection rule*, so post-treatment
differential trends are built in. Rambachan–Roth (2023) breakdown-M̄
analysis will almost certainly reject v1's naive estimate. **The
regime failure**: Sato–Kanazawa (2024) δ=½ is universal on TSE
mid/large-cap metaorders, not on 20–50% participation microcap gap
child-orders. Bucci–Benzaquen–Lillo–Bouchaud (2019) crossover:
impact is linear for small Q/V, √ in the middle, saturates at high
participation — v1's regime is *beyond* the calibrated √ window.
Add the **denominator endogeneity** (first-6-min volume is inflated
by the same catalyst that triggered the trade) and the **PFOF
wedge** (Alpaca retail routing inflates η 2–5× vs lit-venue
institutional metaorder calibrations) and v1's
$η$-estimated-from-priors is wrong in a direction we can sign:
too low. **Expected correction: implementable capacity at the
current stack is $1–2M microcap, not $5M.**

**Finding 3 — Business-model: the $5M-in-12-months arc doesn't
survive the scaling-arc evidence; a 36-month arc with an RIA+SMA
structure does, and a two-strategy portfolio opens a diversified
capacity path the single-strategy plan does not see.** The
scaling-arc research (616ec49c) finds **no primary-source case of
a solo quant scaling $150K → $5M of deployed own capital in 12–15
months** on a published-signal basis. Faber took 11 years. The
operational cliff at $20M AUM is where the fund economics work;
at $5M a fund is irrational. But $5M via own capital + 2–5
accredited-SMA clients via state RIA registration is
**operationally cleaner, regulatorily modest ($20K setup,
$15–40K/yr), and reachable in 36 months under documented
precedent**. And — the innovation v1 did not see — the same
scaling-arc evidence shows overnight-gap-continuation (Lou–Polk–Skouras
2019) is **proven in mid-cap at economically significant magnitudes**
while cascade-anti-selection is predicted to attenuate there. This
is a **two-strategy portfolio** (microcap cascade at its ceiling,
mid-cap overnight-continuation as a second signal), not a universe
migration. The target AUM is unlocked by *diversifying the capacity
constraint*, not by scaling-up a single signal.

The rest of the document develops each finding into concrete plan
changes, culminating in a phased roadmap that makes $5M
**defensible, not optimistic** — and makes Paper 1 a publishable
methodological contribution rather than a synthesis of somebody
else's work.

---

## 1. Critical engagement with the frontier critique (aef3259b)

The frontier critique is the most damaging document for v1. It reads
like a JFE reviewer and it's correct on every point that matters.
I'll engage each finding on its own terms, assign severity, and name
the specific v1 section that breaks.

### 1.1 FATAL — the Alimoradian citation

**The critique.** v1's §1 extracts four "portable objects" from the
Alimoradian paper: decomposition architecture, counterfactual
price `S*_t`, power-law functional form, martingale diagnostic.
The critique shows that the paper's `𝕀*` construction is an
insider-filtration enlargement (Amendinger, Grorud–Pontier,
Eyraud-Loisel 2005) defined relative to **the large trader's own
policy**, not an exogenous counterfactual cohort. The no-manipulation
martingale condition requires measurability in a specific enlarged
filtration that the *signal* — because it selects on post-treatment
features — breaks by construction. The paper's numerical results
compare delta-hedges for a single OTM call under continuous
Black–Scholes-plus-impact dynamics. **Extrapolating to discrete
microcap gap-ups at 9:30–9:36 ET with LULD halts has no support
in the paper.**

**v1's response (wrong).** v1 read Alimoradian as a source of the
decomposition architecture and the counterfactual price, treated
them as empirically-portable, and cited the paper as theoretical
cover.

**Correct engagement.** The critique is right; v1 is a category
error. The paper's decomposition is valid in its own domain
(derivatives hedging under insider filtration) but does not
transfer to empirical identification of impact on a retail-routed
microcap order. The correct anchor is:

- **Abi Jaber–Bondi–De Carvalho–Neuman–Tuschmann (2025)
  "Fredholm Approach to Nonlinear Propagator Models"** — explicit
  conditions under which transient and permanent components are
  *separable*. Those conditions **fail under nonlinear power-law
  impact at high participation**, which is where v1 operates.
  v1's additive decomposition is therefore theoretically incorrect
  before any empirical issue is considered.
- **Bouchaud–Bonart–Donier–Gould *Trades, Quotes and Prices*** —
  propagator kernels, empirical evidence against Obizhaeva–Wang
  exponential decay for US equity.
- **Hey–Bouchaud–Mastromatteo–Muhle-Karbe–Webster (2024) "The Cost
  of Misspecifying Price Impact"** (*Risk* 2024) — misspecification
  costs are **asymmetric**; symmetric MSE-style confidence intervals
  understate downside P&L risk. v1's kernel-CI reporting is
  directionally wrong.
- **Hey–Mastromatteo–Muhle-Karbe–Webster (2024 *Operations Research*)** —
  single-exponential and single-power decay are empirically
  rejected; **two decay timescales (intraday + near-permanent) are
  required**.

**v2 plan change.** Drop Alimoradian from the estimator's
theoretical chain. Keep it in the reading list as the source of
the insider-observer intuition (which is worth something). Replace
§1 with a propagator-literature anchor that cites the five papers
above. Abandon the additive-separable `I_temp + I_perm` framing as
primary; adopt the Benzaquen–Bouchaud crossover functional with a
two-timescale decay kernel (§3.3 below).

### 1.2 SERIOUS — DiD broken by signal-driven selection

**The critique.** v1's I_perm = return_traded − median(return_cohort).
Selection into "traded" is the trading signal itself. The signal
conditions on variables (early-tape order-flow imbalance, gap
magnitude, news-flag, float) that are themselves predictors of the
*untreated* potential outcome. This is the canonical
**selection-on-post-treatment-potential-outcomes** failure.
Parallel trends in the pre-treatment window (minutes before 9:30)
do not rule out differential trends in the post-treatment window,
because the signal is *designed* to pick names with differential
post-trends.

The critique offers three frontier tools v1 omits:

1. **Rambachan–Roth (2023) HonestDiD** (*Review of Economic Studies*
   90(5):2555–2591). Report breakdown M̄: how large a post-treatment
   differential trend, relative to the observed pre-trend, would
   need to be to overturn statistical significance of I_perm.
   "Given that the pre-trend *is* the signal, expect breakdown
   values near 1 — i.e., the estimator is not robust."
2. **Arkhangelsky–Athey–Hirshberg–Imbens–Wager (2021) Synthetic
   DiD** (*AER* 111(12):4088–4118). SDID optimally reweights
   both control units and pre-treatment periods. Implementation:
   `synthdid` (R), `sdid` (Stata; Clarke–Pailañir–Athey–Imbens 2024).
   Plus Arkhangelsky–Ciccia (2024) **Sequential SDID** for staggered
   adoption under interactive fixed effects.
3. **Chernozhukov–Chetverikov–Demirer–Duflo–Hansen–Newey–Robins
   (2018) Double/Debiased ML**. Neyman-orthogonal scores + cross-fitting
   to purge regularization bias when the nuisance propensity is
   estimated with ML on the same covariates used in matching.

**v1's response (wrong).** v1 treated median-of-cohort as "the
primary estimator" and "difference-in-differences under the
identification assumption that non-traded cohort members provide
an unbiased counterfactual." v1 flagged cohort size and cohort
representativeness but did not flag signal-driven selection as
the first-order threat.

**Correct engagement.** The critique is right. v1's I_perm is
biased **upward** (in magnitude) by 30–60% in the direction the
signal selects. The correction pathway is not refinement; it's a
replacement estimator.

**v2 plan change.** Replace the v1 estimator with a three-layer
stack:

1. **Primary estimator: SDID on a per-trade 30-min pre/post panel**
   with the matched cohort. The cohort provides the donor pool;
   SDID reweights both donor units and pre-periods so that the
   synthetic control matches our pre-period. Use Arkhangelsky-Ciccia
   sequential SDID to handle same-day staggered treatment (our
   trades don't all happen at 9:30:00).
2. **Robustness: HonestDiD breakdown M̄**. Report *every* I_perm
   estimate alongside its breakdown M̄. A breakdown M̄ < 1 is the
   honest statement "the estimate collapses under a plausible
   differential-trend violation." This goes in the report next to
   the point estimate; it is not optional.
3. **Companion: augsynth and MC-panel**. Ben-Michael–Feller–Rothstein
   `augsynth` partially-pooled SCM; Athey–Bayati matrix completion.
   Both are companion estimates whose agreement or disagreement
   with SDID is itself a diagnostic. Modern practitioner consensus
   (Roth–Sant'Anna–Bilinski–Poe 2023 *J Econometrics*;
   Baker–Callaway–Cunningham–Goodman-Bacon–Sant'Anna 2024 *JEL*)
   is not to privilege SDID — it is to report the cross-estimator
   range and let it speak to robustness.

The v1 plan's §6 Phase-1 gate was "one estimator, one decision
rule." The v2 plan's Phase-1 gate is "a three-estimator consensus
where the cross-estimator range is reported alongside the point."

### 1.3 SERIOUS — square-root law outside its calibrated regime

**The critique.** Sato–Kanazawa (2024, arXiv:2411.13965, revised
Dec 2025) establishes δ=½ with error bars below 0.01 across all
liquid TSE stocks — but on mid/large-cap *metaorders*. Bucci et al.
(2019 *PRL*) show impact has a **crossover**: approximately linear
for small Q/V, √ at moderate Q/V, saturation at high. v1's
regime (Q/V_τ 20–50%) is **beyond the calibrated √ window**, in
territory where propagator models predict saturation and
Neuman–Voss (2023) predicts crowding-induced nonlinearity. The
denominator V_τ (first-6-min volume) is *endogenous to the gap
event* — the same catalyst inflates it — so √(Q/V_τ) systematically
understates η. And Sato–Kanazawa show α and γ differ across single
stocks, contradicting Tóth et al.'s universal-γ latent-liquidity
derivation and warning against cross-name parameter transfer.

**v1's response (wrong).** v1 adopted `I_temp = Spread₀/2 + η·(Q/V_τ)^γ·σ_τ`
with TruncN(0.70, 0.20) prior on γ. The prior was defended as
"motivated upper bound" for microcap books. v1 acknowledged
denominator choice was hard (§1.5) but landed on `max(α·V₀:₆^residual,
β·ADV/13)` — still endogenous because V₀:₆ is the gap's own volume.

**Correct engagement.** The crossover is real and empirically
established. v1's functional form is a special case of the
crossover valid only in the middle regime. The functional form is
wrong for our regime; the prior is centered too high; the
denominator is endogenous.

**v2 plan change.**

- **Functional form**: replace `η·(Q/V)^γ·σ` with the
  **Benzaquen–Bouchaud crossover** `MI(Q) ≈ cσ(Q/V)^½ · F(Q/V)`
  where `F(x) ≈ √x` for small x and saturates for large x.
  Concretely: a two-parameter family
  `I_temp = cσ · (Q/V) / √(1 + Q/V)^2` (a smooth crossover) or
  the Abi Jaber–Neuman nonlinear propagator form where available.
  Report both (crossover and pure power-law) and let the
  posterior choose.
- **Prior on γ**: re-center at 0.55 (between Sato–Kanazawa's 0.50
  and the microcap amplification v1 used) with TruncN(0.55, 0.20,
  [0.4, 1.2]). The upper bound of 1.2 stays to permit linear
  limit, but the prior no longer asserts microcap-heavy γ by
  default.
- **Denominator endogeneity**: replace the blended V_τ with a
  **pre-event conditional ADV**: 20-day median ADV × intraday
  U-shape expectation × gap-day indicator-dummy expansion factor
  (the last estimated from historical similar-catalyst events).
  The V_τ is now exogenous to today's catalyst; this lifts the
  downward bias on η.
- **Hierarchical structure**: fit η and γ as a *hierarchical*
  Bayesian model pooling across similar-liquidity-tier names,
  with participation Q/V as a covariate. Per the critique:
  "expect γ̂ to vary meaningfully across the 20–50% participation
  range." Not one γ; a γ-function of Q/V.

### 1.4 SERIOUS — PFOF wedge is 2–5× larger than v1 assumes

**The critique.** v1 assumed η priors drawn from Almgren et al.
(2005) and Frazzini–Israel–Moskowitz (2018), both of which use
institutional lit-venue data. Alpaca routes retail equity flow to
wholesalers (Citadel, Virtu, G1X), not lit venues. Four documented
wedges:

- **Schwarz–Barber–Huang–Jorion–Odean (2025 *JF*; SSRN 4189239)**:
  85,000 simultaneous market orders across six brokers (Dec
  2021–Jun 2022, 128 stocks). Account-level round-trip costs
  ranged from −0.07% (TD Ameritrade) to −0.46% (IBKR Pro) — a
  **6.6× broker spread** that is almost entirely routing-driven
  rather than PFOF-rate-driven.
- **Ernst–Spatt–Sun (2025 *JF*)** on order-by-order auctions: a
  winner's-curse effect reduces retail welfare when liquidity is
  limited, exactly the microcap-gap-up regime.
- **Ernst–Spatt (NBER 2022)** on PFOF and asset choice: PFOF is
  small in equities in *rate* terms but routing conflicts of
  interest produce statistically and economically significant
  effective-spread differences across wholesaler assignments.
- **Bryzgalova–Pavlova–Sikorskaya (2023 *JF*)** on retail segmentation:
  impact dynamics on segmented retail flow are not the same as on
  lit ANcerno institutional flow.

**Scaling-arc research (616ec49c) decomposes the wedge**: ~20–30%
internalization economics, **~70–80% routing sophistication +
short-locate access + depth-discovery + rejection-avoidance**. A
DMA migration (IBKR Pro → Cobra/Centerpoint tier) recovers most
of the wedge by addressing the 70–80%, not by eliminating PFOF.

**v1's response (partial).** v1 §5 risk #2 named "Alpaca routing
opacity" and said "document the calibration as 'Alpaca-routed
retail-microcap impact'; if we later move to DMA, recalibrate from
scratch." That's correct but undersells the magnitude and ignores
the DMA-as-scaling-lever.

**v2 plan change.**

- **Calibrate η from own fills** (which the frontier critique
  demands explicitly), not from institutional-metaorder priors.
  This is already implicit in v1's Phase 1 but not named as a
  *correction factor over lit-venue priors*. Name it: the η we
  measure is the **η_Alpaca**, not the η_universal.
- **Add DMA migration to the plan's AUM-tier sequence** (§4 below).
  Tier B (~$500K–$1M own capital) migrates to IBKR Pro, recovering
  the 30–50% of the wedge that's routing-driven. Tier C
  (~$1M–$5M own + SMA) migrates to Cobra/Centerpoint for full
  short-locate + depth access. The calibration **must be redone
  at each tier transition** (v1 said so; v2 bakes it into the
  timeline with engineer-weeks estimated per the scaling-arc
  research: 4–12 weeks per migration).
- **Add BJZZ → QMP signing migration** (§1.6 below) so cohort
  matching uses retail-flow density correctly at microcap spreads
  where BJZZ signing drops to ~52% accuracy (Barber et al. 2024).

### 1.5 IMPORTANT — T+1 settlement has structurally changed microcap

**The critique.** T+1 went live May 28, 2024. Securities-lending
markets show ~10% higher fail rates; locate fees elevated for
hard-to-borrow names (most microcaps). The pool of capital willing
to short gap-ups is smaller. This makes gap-up fades less reliable
post-May 2024. Plus: v1's I_perm is measured relative to EOD
returns, but under T+1 EOD flows include accelerated
delivery-matching activity that did not exist pre-2024 — a regime
shift in the outcome variable itself.

**v1's response (missed entirely).** v1 does not mention T+1.

**v2 plan change.**

- **Restrict the calibration training data to post-May-28-2024**.
  Or, equivalently, include a `post_t1_dummy` regressor in the
  SDID/DiD specification and test for a regime shift.
- **Segregate the I_perm EOD-horizon from intraday horizons** in
  the report. v1's multi-horizon reporting already does this for
  decay-vs-unreverted-temporary reasons; v2 adds a T+1 reason
  (EOD is now contaminated by delivery-matching).
- **Document T+1 as a structural stationarity assumption** in the
  risk register: the calibration is valid for the post-T+1 regime
  and will need refreshing if the SEC further changes settlement
  (T+0 discussions are not hypothetical).

### 1.6 IMPORTANT — BJZZ → QMP migration is effectively mandatory

**The critique.** Barber–Huang–Jorion–Odean–Schwarz (2024 *JF*) "A
(Sub)penny for Your Thoughts": using 85,000 real retail orders,
BJZZ (Boehmer–Jones–Zhang–Zhang 2021 *RFS*) subpenny-signing
identifies only **35% of true retail trades**, incorrectly signs
**28% of those**, and accuracy drops from 93% at 1¢ spreads to
**52% at 10¢+ spreads** — effectively random for microcaps, which
routinely show >5–10¢ spreads. The **quote-midpoint (QMP)
modification** reduces signing error to 5% across all spread
widths.

**v1's response (implicit).** v1's cohort matching was by
"screen features" and didn't name retail-flow density as a
covariate at all. So BJZZ vs QMP was never in the plan.

**v2 plan change.** If retail-flow density becomes a matching
covariate (which the frontier critique recommends and v2 adopts),
use **QMP signing** from day one. Never BJZZ for microcap cohort
construction. Drop retail-OIB for names with avg spread > 5¢ or
require wholesaler/odd-lot cross-validation.

### 1.7 SECONDARY — pre-averaged realized volatility for σ_τ

**The critique.** A naïve realized-vol estimator on 10-second bars
will be severely biased by microstructure noise in $0.50–$20
microcaps where the relative tick is large. Use Jacod–Li–Mykland
pre-averaged realized volatility or Andersen–Dobrev–Schaumburg
MedRV/MinRV (jump-robust). Expected bias reduction on σ_τ:
15–30%. Since σ_τ enters I_temp multiplicatively, this is a
direct 15–30% multiplicative correction on everything downstream.

**v2 plan change.** Adopt pre-averaged realized vol as the primary
σ_τ estimator. MedRV as jump-robust companion. Document the
sensitivity of the reported posterior to the σ_τ choice.

### 1.8 SECONDARY — capacity derivation per Jensen–Kelly–Pedersen

**The critique.** v1's "$5M / 35%-participation" number is an
*assertion*, not a *derivation*. Jensen–Kelly–Pedersen (2023 *JF*)
and Jensen–Kelly–Malamud–Pedersen (2024) give a Garleanu–Pedersen
framework for deriving capacity as the AUM at which marginal cost
equals marginal alpha. Plugging v1's own η (corrected for the
PFOF wedge) into this framework **likely yields capacity $1–2M,
not $5M**. McLean–Pontiff (2016): 58% post-publication decay for
the median anomaly. Detzel et al. (2023): up to 93% post-cost
decay. Net-of-realistic-cost alpha is much smaller than in-sample.

**v1's response (partial).** v1's headline finding ("$5M is
marginally feasible under central parameters; honest ceiling is
$1.5–2.5M") is directionally aligned with this critique. But v1
didn't **derive** the $1.5–2.5M from Jensen–Kelly–Pedersen; it
derived it from a single-trade worked example with tiered
participation. The critique's framework is more rigorous.

**v2 plan change.** Add a **capacity-derivation appendix** that
plugs the Phase-1 posterior η into Jensen–Kelly–Pedersen and
reports the implied capacity with uncertainty bounds. This
becomes the Phase-1 report's headline deliverable: "implementable
capacity on this stack is [X, Y] with 90% CI [A, B] conditional
on the post-T+1 regime and the current Alpaca routing." The
reported capacity is a *distribution*, not a point.

### 1.9 The five Topic-7 methodology updates (scaling-arc research §7)

The scaling-arc research provides five prioritized updates that
overlap with — and sharpen — the frontier critique's. I'll adopt
the ranked order directly:

| Priority | Update | What it replaces | Landing in v2 |
|---|---|---|---|
| 1 | **BJZZ → QMP signing** | Any retail-flow covariate using subpenny signing | §1.6 above |
| 2 | **MC-panel or augsynth alongside SDID** | SDID alone | §1.2 above |
| 3 | **Two-timescale propagator kernel with pessimistic-loss inference** | Single-√-law Obizhaeva–Wang | §1.3 above + §3.3 below |
| 4 | **Bayesian prior-predictive simulation** | Harvey-Liu haircut thresholds | §5 below |
| 5 | **HonestDiD restricted to T_pre ≥ 4** | HonestDiD at any T_pre | Estimator gate |

Plus the cross-cutting observation: **every one of these methods
has sharper problems at N < 20 than in the regime it was designed
for**. SDID jackknife invalid. Propagator kernels unidentified.
HonestDiD sensitivity bounds explode at T_pre ≤ 2. BJZZ signing
collapses with spread. Bayesian thresholds become prior-dominated.
The plan's honest-error bar at N=30 is **much wider than v1
reported**. v2's Phase-1 gate must reflect this: not "UCB_75%"
narrowly, but **a triangulated posterior across three estimators
with breakdown bounds**.

---

## 2. Critical engagement with the scaling-arc research (616ec49c)

The scaling-arc research is the most strategically damaging of the
three, because it rewrites the *goal*, not just the estimator.

### 2.1 The $5M-in-12-months claim does not survive the primary-source evidence

**The finding.** No documented case of a research-grade solo quant
scaling $150K → $5M of deployed own capital in 12–15 months on a
published-signal basis. Cleanest primary data points:

- Brown–Goetzmann–Ibbotson (1999), Liang (2000), Gregoriou (2002):
  8–9% annual attrition in small/emerging hedge funds; 50% dead by
  5.5 years, 70% dead by 47 months.
- Aggarwal–Jorion (2010 *JFQA*): each additional year of age
  reduces performance by ~48 bps/year (consistent with Berk–Green
  2004).
- McLean–Pontiff (2016 *JoF*): 26% out-of-sample decline, 58%
  post-publication decline in anomaly returns. Jacobs–Müller
  (2020 *JFE*): 62–66% globally. Detzel et al. (2023): up to 93%
  post-cost for the median anomaly.
- Faber's Cambria timeline: 2006 RIA founding → 2010 first ETF
  (via AdvisorShares platform) → 2013 canonical SSRN paper →
  2017 $1B AUM. **Eleven years.**
- Carver (AHL-pedigreed solo): refuses outside capital; monetizes
  via books/consulting/teaching. Never reaches $5M on trading
  alone.
- Scott Phillips (Flirting with Models S7E15, March 2025):
  explicitly describes needing to leave TradFi trend for crypto
  at "a little less than a million bucks in capital" because
  "you're still a bit suboptimal." Same binding constraint
  MOMENTUM-X faces in reverse.

AIMA/GPP 2017 emerging-manager survey: **average break-even AUM
is $86M** (CTAs $78M, global macro $132M). Below ~$20M AUM,
management-fee revenue covers neither audit/admin/legal
(~$150–300K/yr minimum) nor a founder salary. The operational
cliff between own-capital book and fund is $1M–$20M.

**v1's response (silent).** v1 took the $5M-12-month framing as
given and asked "how do we execute it?" The v1 plan's implicit
assumption: if the calibration says $5M is feasible, we deploy
$5M. The plan did not contemplate the business-model timeline.

**Correct engagement.** The scaling-arc research is right. $5M in
12 months on own capital requires >200% compounded return against
documented research-grade Sharpe, not credible. $5M via external
capital requires entering Tier C RIA + SMA territory, which
requires a 1–3 year audited track record, which is the business
constraint v1 did not see. The research names this exactly:
"the quant traders who scaled successfully all understood this
and budgeted for it; the ones who failed typically thought the
trading result would pull the business result along with it. It
does not."

**v2 plan change.** Reframe the AUM arc as **36 months, not 12**,
with an explicit business-model layer that runs on a separate
clock from the research and trading layer. §4 below develops the
36-month map. The original "$5M in 12 months" framing stays in
the risk register as **the unrecoverable assumption v1 was built
on**.

### 2.2 The operational cliff at $1M–$20M and the RIA-SMA insight

**The finding.** Tier A ($150K–$1M own capital, retail brokerage):
PDT, §475(f), §1256; annual cost ~$2–7K. Tier B ($1M–$5M own,
DMA migration): same framework plus Large Trader Reporting (Rule
13h-1 at $20M/day or $200M/month notional NMS); annual ~$8–25K.
**Tier C ($1M–$5M including external SMA capital)**: state RIA
registration (except New York: SEC at $25M); Form ADV Parts 1A
and 2A/2B via IARD; performance fees only from qualified clients
(2026 thresholds: $2.2M net worth ex-primary-residence or $1.1M
AUM with adviser); setup $5–20K, ongoing $15–40K/year. **Tier D
($5M+ with pooled vehicle)**: 3(c)(1) or 3(c)(7) fund; Reg D 506(b)
or 506(c); ERA under §203(m) or full SEC RIA at $150M RAUM. Fund
year-1 cost ~$150–400K; annual ~$125–350K. **At $5M, fund
economics are irrational** — fund cost stack consumes the entire
2% management fee.

The scaling-arc research's critical observation:

> "The $5M-in-a-fund target needs reframing: $5M via own capital
> + one to three accredited-investor SMAs is operationally cleaner
> than a $5M fund."

**v1's response (silent).** v1 did not discuss regulatory tiers at
all. The "$5M" number was treated as undifferentiated AUM.

**v2 plan change.** The v2 target structure is:

- **Own capital**: grow from $142K current toward the microcap
  ceiling ($1M–$2M per the frontier-critique-corrected capacity),
  via compounded returns and/or personal-capital infusion.
- **2–5 accredited SMA clients** at $500K–$1M each, opening at
  the 18–24 month mark once a 12–18 month track record exists.
  Total SMA book: $2M–$5M.
- **Combined target**: $5M = $1.5M own + $3.5M SMA, approximately.
- **Regulatory structure**: state RIA registration (not SEC until
  $25M RAUM in NY or $110M RAUM elsewhere under the mid-year 2025
  NSMIA provisions).
- **NOT a fund**. The target is SMA-based from the outset; a
  3(c)(1) fund becomes economically rational at ~$20M, not $5M.

This is a **business-model architecture** v1 did not contemplate.

### 2.3 The cascade-vs-overnight distinction opens a two-strategy path

**The finding.** Hou–Xue–Zhang (2020 *RFS*) "Replicating Anomalies":
286 of 447 anomalies (64%) are insignificant under NYSE breakpoints
and value-weighted returns (microcap-suppressed). Category-level:
momentum, value, investment, profitability replication improves
when microcap weight is allowed but does not collapse when it is
suppressed; the category that collapses most (**96% failure under
microcap suppression**) is trading-frictions/liquidity — Amihud,
Pastor–Stambaugh, Easley–Hvidkjaer–O'Hara PIN. "That is precisely
the category 'cascade-anti-selection' most resembles mechanically:
a microstructure-friction effect."

**The empirical prior**: a gap-up momentum signal whose causal
story is "slow information diffusion + retail herding + short-sale
constraints" will attenuate significantly in mid-cap, possibly to a
third or less of microcap magnitude.

**But**: Lou–Polk–Skouras (2019 *JFE*) studied an overnight-winner
hedge portfolio **excluding stocks below $5 and the bottom NYSE
size quintile**. It earned **3.47%/month three-factor alpha overnight
with a −3.02% intraday reversal** on a non-microcap sample.
Akbas–Boehmer–Jiang–Koch reinforce. Heston–Korajczyk–Sadka (2010)
document 30-minute intraday momentum persistence across size
deciles. The "overnight-to-open gap predicts next-period return"
skeleton **has direct published support in non-microcap
universes**.

**The failure-mode taxonomy**:
1. "Doesn't transfer" — most likely when the decisive causal
   mechanism is microstructure friction. If MOMENTUM-X's alpha is
   primarily cascade-anti-selection contingent on microcap
   illiquidity, expect **50–80% attenuation in mid-cap**.
2. "Transfers but capacity still binds" — less likely; mid-cap
   capacity is structurally abundant ($20–60M per 10-name portfolio
   at 1–3% ADV participation per FIM 2018). $5M is well below this.
3. "Reduces to standard factor" — the subtlest failure. UMD / SMB
   exposure absorbs most of the apparent alpha. Novy-Marx–Velikov
   (2016) document this pattern.

**v1's response (partial).** v1's Phase 4 named "mid-cap migration"
but framed it as an *alternative* to microcap ("if microcap
doesn't scale") and required re-running the whole Phase 0 through
Phase 1 cycle for mid-cap. v1 did not see the two-strategy
diversification angle.

**v2 plan change — the key innovation.** Adopt a
**two-strategy portfolio** architecture:

- **Strategy S1 (microcap cascade)** at its frontier-critique-corrected
  capacity ceiling. Target: $1.5M own capital, running at 2.5%
  per position, Q/V_τ ≤ 25% per trade. This is the v1 plan,
  re-estimated with the v2 estimator.
- **Strategy S2 (mid-cap overnight-gap-continuation)** as a second,
  published-signal-backed strategy. Target: $3.5M AUM via SMA
  book, running the Lou–Polk–Skouras-style overnight continuation
  on the Russell Midcap universe. This is a new project but
  leverages the same instrumentation + SDID + HonestDiD
  methodology — most of Phase 0 transfers.

The AUM target is unlocked by **diversifying the capacity constraint
across two signals**, not by scaling up a single signal. Both
signals have published academic support; neither is being asked
to reach beyond where the literature says it can go.

This is the architectural innovation v1 did not see. It's what
makes the 36-month $5M target *defensible* rather than
aspirational.

### 2.4 IV-dispersion is a novel methodological contribution, not a replication

**The finding.** The direct literature using cross-sectional
IV-dispersion as a *validation tool for equity signal
transferability across size deciles* is thin to non-existent in
published academic work. Adjacent literature supports the
*premise* — Cremers–Weinbaum (2010 *JFQA*), Xing–Zhang–Zhao (2010),
Bali–Hovakimian (2009), An–Ang–Bali–Cakici (2014), Goyal–Saretto
(2009), Driessen–Maenhout–Vilkov — all show option-implied signals
contain information the underlying equity market underprices. But
**the specific technique of using IV-dispersion to pre-test whether
an equity signal will transfer from one size cohort to another is
not established**.

The research's directive: "frame IV-dispersion cross-validation
as a methodological contribution, not a replication; cite the
adjacent option-signal literature to establish priors; pre-register
the specific test."

**v1's response (wrong).** v1 Phase 4 described IV-dispersion as a
"backtest" requiring OptionMetrics-caliber data and treated it as
an established technique borrowed from the Bali–Hovakimian / Xing /
Cremers–Weinbaum literature.

**Correct engagement.** The critique is right. If we do this, we're
building something novel. That's either an opportunity or a risk
depending on framing.

**v2 plan change.** Frame IV-dispersion cross-validation as **the
publishable methodological contribution of Paper 2**. Specifically:

- **Adjacent-literature anchor**: Cremers–Weinbaum, Xing et al.,
  Bali–Hovakimian cited as the adjacent literature; the novelty
  is the application to size-decile transferability.
- **Pre-registration**: specify the exact test before running it.
  "Mid-cap signal validates if IV-dispersion-conditional return
  spread exceeds X bps with t > 3 (Harvey–Liu adjusted for N tests
  multiple-testing controls)."
- **Adversarial rigor**: Harvey–Liu–Zhu (2016) multiple-testing
  adjustment, because first-use methodologies are exactly where
  p-hacking risk lives.

Paper 2 then becomes: "Cross-Asset Pre-Registration of Equity
Signal Transferability: Evidence from Microcap-to-Mid-Cap Overnight
Continuation." JFDS or JPM practitioner-weighted venue.

---

## 3. The updated estimator (building from §1 + §2)

Tying the above together, here is the v2 estimator in full, with
every element backed by a cited paper and every deviation from v1
named.

### 3.1 Decomposition

Instead of v1's Alimoradian-derived
`I_temp + I_perm` additive decomposition, v2 uses a
**propagator-kernel framework** with two decay timescales:

```
Observed return over horizon τ:
  r_τ = m̂(τ)                                           (ambient, via SDID counterfactual)
      + ∫₀ᵗ K(t-s) · dQ_s                              (our footprint via kernel K)
      + ε_τ                                             (noise)

K(u) = a · exp(-u/λ_fast) + b · u^(-β) · 1{u < T_long}
                                    ↑           ↑
                              fast decay    power-law long tail
```

- `K(u)` is the two-timescale propagator. `a, λ_fast` are the
  intraday decay parameters (Hey et al. 2024 *OR* calibration: ~5–30
  min half-life). `b, β, T_long` are the near-permanent tail
  parameters. Both (a, λ_fast) and (b, β) are estimated jointly.
- `m̂(τ)` is the SDID-produced ambient counterfactual (not the
  median-of-cohort).
- `dQ_s` is the signed executed quantity over an infinitesimal
  window (in practice: per-partial-fill tick, which v1 Phase 0
  instrumentation captures).

"Temporary" and "permanent" are no longer primary quantities; they
are *derived* from K(u). I_temp at horizon τ is the contribution
of the fast decay term, I_perm is the contribution of the long
tail. The Abi Jaber–Neuman (2025) Fredholm theorem gives conditions
under which this decomposition is well-defined; we check those
conditions in the diagnostic battery.

### 3.2 Functional form for I_temp at arrival

Instead of v1's `η·(Q/V)^γ·σ`, v2 uses the **Benzaquen–Bouchaud
crossover**:

```
I_temp(Q, V_τ) ≈ c · σ_τ · [ (Q/V_τ)^(1/2) · F(Q/V_τ) ]

F(x) = x / √(1 + x²)    (a smooth crossover: F → √x for small x,
                         F → 1 for large x, so I_temp saturates)
```

or equivalently, a more flexible 3-parameter form with η, γ, and
a saturation scale:

```
I_temp(Q, V_τ) ≈ c · σ_τ · (Q/V_τ)^γ · 1/(1 + (Q/V_κ)^δ)
```

Both are fit; the posterior chooses. Pure power-law (v1) is a
special case with δ → 0.

### 3.3 Volume denominator V_τ — exogenous

v2 uses **pre-event conditional ADV**:

```
V_τ = ADV_20d_median × U_shape(t) × gap_day_expansion_factor
```

where:
- `ADV_20d_median` is the 20-day trailing median daily volume,
  computed *before* the gap event.
- `U_shape(t)` is the intraday pro-rata expectation (higher at
  open and close, lower mid-session).
- `gap_day_expansion_factor` is estimated from *historical*
  similar-catalyst events (a KNN lookup: "what fraction of daily
  volume typically arrives in the first 6 minutes on a +8% gap
  +3× RVOL event?") — exogenous to today's realized volume.

This eliminates the endogeneity of V_τ w.r.t. the gap catalyst.

### 3.4 Volatility prefactor σ_τ — pre-averaged

v2 uses **Jacod–Li–Mykland pre-averaged realized volatility** on
10-second (or finer) bars over the arrival window, with MedRV/MinRV
(Andersen–Dobrev–Schaumburg) as jump-robust companion. Reports
both; calibration uses pre-averaged unless the MedRV companion
disagrees by more than 30% (flag as "jumpy regime").

### 3.5 Counterfactual — SDID, not median

For each trade, build a 30-min pre/post panel:
- T = our executed trade (one row per minute in [-30, +30] minutes
  relative to 9:30:00).
- Donor pool = the matched cohort (same-day non-traded gap-up
  candidates passing the same screen, matched on gap%, RVOL, float,
  price, **retail-flow density via QMP signing**, and post-T+1
  regime indicator).
- Estimator: Arkhangelsky–Ciccia Sequential SDID via `sdid_event`
  (Stata/R). Companion: `augsynth` partially-pooled SCM, Athey–Bayati
  matrix completion.
- Inference: Lee–Wooldridge (2026 SSRN) exact cross-sectional
  collapse when N_treated is small; wild cluster bootstrap otherwise.
  **Not jackknife** (biased at N_treated < ~10; Arkhangelsky's own
  guidance).

### 3.6 Robustness — HonestDiD breakdown M̄

For every SDID estimate of I_perm, report HonestDiD breakdown M̄
(Rambachan–Roth 2023; `HonestDiD` R v0.2.8 or `honestdid` Stata
v1.3.4). **Constraint**: use only when T_pre ≥ 4 (minutes). At
T_pre < 4, pair with Roth (2024) Empirical-Bayes-prior extension
or drop HonestDiD and rely on cross-estimator triangulation.

Report format: "I_perm = X bps (90% CI [A, B]); HonestDiD breakdown
M̄ = Y. The estimate is robust to differential-trend violations
up to Y× the observed pre-trend."

### 3.7 Retail-flow matching — QMP not BJZZ

For names with average spread > 5¢ (most microcaps), **never use
BJZZ subpenny signing**. Use QMP (Barber–Huang–Jorion–Odean–Schwarz
2024) directly, or — for matching purposes where exact signing
isn't required — use wholesaler/odd-lot cross-validation.

### 3.8 Hierarchical Bayesian estimation

Replace v1's single-name-agnostic hierarchy with **liquidity-tier
pooling**:

```
I_temp_i ~ StudentT(ν=4, μ_i, σ²_i)
μ_i      = c · σ_τ_i · (Q_i/V_τ_i)^γ_{tier(i)} / (1 + (Q_i/V_κ)^δ)

γ_{tier(i)} ~ Normal(μ_γ, τ_γ)                         (per-tier γ, pooled)
μ_γ        ~ Normal(0.55, 0.20)                        (cross-tier prior)
τ_γ        ~ HalfNormal(0.10)

log c      ~ Normal(log(0.1) - ε_PFOF, 1.5)            (ε_PFOF = 2-5× wedge adjustment)
δ, V_κ     ~ informative priors from Bucci et al. crossover calibration
σ²_i       ~ per-regime-bucket (low vs high VIX, halt vs no-halt)
```

Stan or PyMC. 4 chains, ≥ 2000 warmup / ≥ 2000 sampling. Report
posterior mean, median, 90% CI for every parameter AND the
implied slippage distribution for the projected AUM targets.

### 3.9 Diagnostic battery (expanded from v1)

v1's diagnostics (prior-vs-posterior CI, PSIS-LOO, rolling-γ,
martingale residual) stay. v2 adds:

- **HonestDiD breakdown M̄** per estimate (§3.6).
- **Hey et al. (2024) pessimistic-loss CI** for the propagator
  kernel: instead of symmetric MSE CIs, report downside-skewed CIs
  reflecting the asymmetric misspecification cost.
- **Cross-estimator range** across SDID, augsynth, MC-panel.
  Report the range; flag where the range exceeds the 90% CI of
  any single estimator (= estimators disagree → estimate is
  estimator-dependent → don't trust it yet).
- **Prior-predictive simulation** (`priorsense` R package;
  Kallioinen et al. 2024 *JMLR*). Power-scaling sensitivity via
  Pareto-smoothed importance sampling. Report Pareto-k and
  posterior-moment sensitivity. Flag estimates where
  posterior/prior evidence ratio < 2 as "prior-driven."
- **Jensen–Kelly–Pedersen capacity projection** with the posterior
  η. Report implementable capacity as a distribution.

### 3.10 Cross-cutting: the "N < 20 caveat"

Every estimator above has sharper problems at N < 20 than in its
calibrated regime. The v2 Phase-1 gate must report the honest
uncertainty. The summary format is:

> "At N=30 the posterior on η_perm is [X bps, Y bps] (90% CI)
> with cross-estimator range Z bps. HonestDiD M̄ = M. Prior
> sensitivity Pareto-k = K (flag if > 0.7). Implementable capacity
> at $5M projected target: P% of the posterior mass falls below
> 50% slippage-to-alpha. The honest statement is that the Phase-1
> calibration is prior-dominated; Phase-2 scaling must be a
> learning-from-live-fills exercise, not a commit-to-scale."

---

## 4. The updated 36-month roadmap (business model + trading arc)

v1's plan was 6–10 weeks (instrument + calibrate + stage-to-$2.5M).
v2's plan is **36 months** and has **two tracks running on
separate clocks**: a trading-and-methodology track and a
business-model track. The tracks converge at month 24 when the
SMA book opens.

### 4.1 Month-by-month roadmap (high level)

| Months | Trading & Methodology Track | Business-Model Track | Regulatory Tier |
|---|---|---|---|
| 0–2 | Phase 0: instrumentation (NBBO, fill ticks, cohort, post-trade, + QMP, + pre-event ADV). Estimator scaffold: SDID + augsynth + propagator kernel. Baseline on own fills (no estimate yet — instrument only). | §475(f) MTM election if not done. TTS evaluation. | Tier A ($142K, Alpaca) |
| 3–6 | Phase 1a: N=30 trades, Bayesian fit. Report + honest-uncertainty per §3.10. | Begin drafting Paper 1 (methodology paper). | Tier A |
| 7–9 | Phase 1b: extend to N=60–80. Re-fit. First credible (η, γ) estimate. Capacity projection per JKP. | Submit Paper 1 to JPM or JFDS. Start RIA registration research (state-by-state requirements). | Tier A |
| 10–12 | Own-capital growth checkpoint. If capacity estimate = $1–2M microcap and own capital is approaching, consider Tier B migration. | Paper 1 under review. | Tier A → B if own >$500K |
| 13–15 | Tier B migration if triggered: IBKR Pro DMA. **Recalibrate** (separate 60-trade calibration on DMA regime because η_Alpaca ≠ η_DMA). | Paper 1 revision. Start S2 research — Russell Midcap universe, overnight continuation signal, QMP-based retail filtering. | Tier B (DMA) |
| 16–18 | S2 instrumentation on mid-cap. Pre-register IV-dispersion transferability test (Paper 2). | Complete state RIA registration (Form ADV 1A/2A/2B). E&O coverage. Owner-CCO setup. | Tier B → C prep |
| 19–21 | S2 Phase 1 calibration on mid-cap overnight continuation. Parallel S1 maintenance recalibration. | RIA registration approved. First accredited-SMA conversations (existing network, friends-of-firm). | Tier C |
| 22–24 | S2 Phase 1 report. Both strategies at calibration-complete state. Pre-deployment diagnostics (all estimators agree; cross-estimator range bounded; Jensen–Kelly–Pedersen capacity ≥ target). | First SMA mandate. $500K–$1M. | Tier C |
| 25–30 | Dual-strategy live: S1 at microcap ceiling, S2 at $1–3M SMA book. Continuous calibration. Paper 2 empirical work. | Scale SMA book: 2–3 clients, $1.5M–$3M total. | Tier C |
| 31–36 | Target-state: $5M total ($1.5M own + $3.5M SMA). Paper 2 submission. | Paper 2 under review. Practitioner-venue presentation (CQA, QWAFAFEW). Allocator network expansion. | Tier C steady-state |

The numbers are illustrative, not commitments. The **gates** between
phases are the commitments:

### 4.2 The gates — what must be true to proceed

**Gate 0→1** (instrumentation → calibration start): Phase 0
complete per v1 §6 (1 full session produces a complete record
set). v2 adds: **propagator kernel scaffold runs on synthetic
well-specified data and recovers known parameters**. Estimator is
tested before the data exists.

**Gate 1a→1b** (N=30 → N=60–80): v2 is explicit that N=30 is
*prior-dominated*. The decision at Gate 1a is NOT a scaling
decision — it is "does the data broadly agree with priors; do
diagnostics pass; proceed to accumulate more data." If priors
and posterior sharply disagree (e.g., η empirically much larger
than the 2–5× PFOF-wedge adjustment predicts), this is a
signal-model mismatch; halt and investigate before more capital
is at risk.

**Gate 1b→2** (N=60–80 → staged scale to $2M microcap + begin
S2 research):
- SDID + augsynth + MC-panel all agree within CI overlap.
- HonestDiD breakdown M̄ > 1.5 (estimate robust to 1.5× pre-trend
  differential).
- Jensen–Kelly–Pedersen capacity projection: 90% CI lower bound
  ≥ 2× current own capital.
- Prior-sensitivity Pareto-k < 0.7 for posterior means.
- Martingale residual test not rejected at 2σ.

**Gate 2→3** (staged scale + DMA migration done + S2 Phase 1
complete → target state):
- Realized S1 slippage at live capital inside 90% CI of projection.
- S2 Phase 1 posterior passes same battery as S1 did at Gate 1b.
- IV-dispersion pre-registered test result available.
- Tier C RIA registration complete.
- First SMA mandate signed.

**Gate 3→4** (target state → steady-state expansion):
- 12-month audited track record across S1 + S2.
- Cross-estimator calibration stability (rolling-window γ and η
  drift < pre-registered bounds).
- Paper 2 published or revise-and-resubmit.

### 4.3 Capacity math with the v2 corrections

v1's $5M/7%-alpha worked example:
```
At Q/V_τ = 50%: total slip 2.32%, UCB_75% ≈ 43% of alpha.
```

v2's corrected expected capacity, *if* the frontier critique is
directionally right:

- **η (Alpaca-routed microcap)** is 2–5× larger than v1's
  lit-venue-prior-derived value. Say η_temp = 0.63, η_perm = 1.20
  (3× PFOF wedge).
- **Crossover functional** saturates above Q/V ≈ 0.3; our
  high-participation trades are no longer sub-linear.
- **Endogeneity correction**: V_τ is smaller (exogenous ADV rather
  than inflated V₀:₆), so Q/V is LARGER for the same Q. Net effect
  on slippage: amplified.
- **S1 capacity (microcap, corrected)**: $1.5–2.0M own capital
  at 2.5% per position. Total slip at the ceiling ≈ 2.5% of
  alpha = 35–45% of 7%, at the scale gate.
- **S2 capacity (mid-cap overnight continuation)**: $10–40M
  theoretical, with $3–5M easily absorbed at $5–50M ADV names at
  1% participation per-name.
- **Combined target**: $1.5M (S1) + $3.5M (S2) = $5M total AUM.
  Both individually within capacity; combined risk-capacity
  modestly diversified (S1 and S2 have partial correlation;
  don't assume zero).

**The $5M target survives** under v2's diversified-capacity
architecture where it doesn't survive under v1's single-strategy
scaling.

---

## 5. Updated gap analysis

v1's 9-capability gap matrix (§4.1 of v1) still applies. v2 adds
**6 new capabilities** driven by the frontier critique and the
scaling-arc research:

| # | v2-new capability | Where it lands | Severity |
|---|---|---|---|
| 10 | QMP-based retail-flow density signing | New helper alongside NBBO capture | High (gates cohort matching) |
| 11 | SDID / augsynth / MC-panel estimator stack | `scripts/slippage_calibration_v2.py`; R subprocess via `rpy2` for `synthdid` / `augsynth` | Medium (Phase 1b, not 1a) |
| 12 | HonestDiD breakdown M̄ computation | Same script; `HonestDiD` R package | Medium |
| 13 | Pre-averaged realized-vol estimator (Jacod–Li–Mykland) + MedRV | Within σ_τ computation | Medium |
| 14 | Pre-event ADV with gap-day expansion factor | `src/data/volume_proxies.py` | Critical (fixes endogeneity) |
| 15 | S2 mid-cap overnight-continuation signal + its instrumentation | Whole parallel project, months 16–24 | Project-scale |

v1's 9 + these 6 = **15 capabilities** total. The Phase-0 scope
is unchanged; the post-Phase-1 scope roughly doubles.

### 5.1 New risks surfaced

v1's risk register (§9) had 7 items. v2 adds:

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| 8 | Alimoradian invocation is a category error → entire theoretical chain invalid | Historical (caught in v2) | v2 drops the chain; anchors on propagator literature |
| 9 | SDID invalid at N < ~10 treated | High | Use cross-estimator triangulation + Lee-Wooldridge exact inference for small-N windows |
| 10 | HonestDiD bounds explode at T_pre ≤ 2 | High | Gate: don't run HonestDiD below T_pre = 4 minutes; use Roth EB-prior as companion |
| 11 | Propagator kernel unidentified at small N | High | Hierarchical pooling across similar-liquidity names; pessimistic-loss CIs |
| 12 | BJZZ signing inaccurate above 5¢ spread | High | Never use BJZZ for microcap; QMP only |
| 13 | T+1 regime shift in EOD outcome | Medium | Restrict training data to post-2024-05-28 OR include regime dummy |
| 14 | Cascade-anti-selection fails in mid-cap | High for S2 | IV-dispersion pre-registered transferability test; failure is "don't run S2"; this is the Paper 2 contribution |
| 15 | Paper 1 review cycle longer than budgeted | Medium | Paper 1 is credibility infrastructure, not capital access; SMA track runs asynchronously |
| 16 | Tier-C regulatory registration delays | Medium | Start drafting at month 12, well ahead of first SMA mandate |
| 17 | DMA migration calibration invalidation (η_Alpaca ≠ η_DMA) | High | Budget 60-trade recalibration at Tier B transition; do NOT assume Alpaca calibration transfers |
| 18 | Scaling-arc business-model constraint (1–3 year track record for allocators) | High — this is the binding constraint | Start the 12-month clock at Tier A; own-capital performance IS the track record |

---

## 6. The decision tree, rewritten

```
                                                       ┌──────────────────────┐
                                                       │ BUSINESS-MODEL TRACK │
                                                       │   (runs asynchronously) │
                                                       └──────────┬───────────┘
                                                                  │
  TRADING & METHODOLOGY TRACK                                     │ month 12: begin
  ┌──────────────────────────────┐                                │ RIA registration
  │ Phase 0:                      │                                │
  │   Instrumentation + QMP       │                                │ month 15-18:
  │   + propagator scaffold       │                                │ RIA approved
  │   + SDID pre-work             │                                │
  │   gate: synthetic recovery    │                                │ month 20-24:
  └────────────┬─────────────────┘                                 │ first SMA mandate
               │ pass                                              │
               ▼                                                   │
  ┌──────────────────────────────┐                                 │
  │ Phase 1a (N=30):              │                                │
  │   First Bayesian fit          │                                │
  │   HONEST statement: prior-    │                                │
  │   dominated; not a gate       │                                │
  └────────────┬─────────────────┘                                 │
               │ diagnostics broadly pass                          │
               ▼                                                   │
  ┌──────────────────────────────┐                                 │
  │ Phase 1b (N=60-80):           │                                │
  │   Cross-estimator consensus   │                                │
  │   HonestDiD M̄ > 1.5           │                                │
  │   JKP capacity ≥ 2× own       │                                │
  │   Pareto-k < 0.7              │                                │
  └────────────┬─────────────────┘                                 │
               │                                                   │
     ┌─────────┴──────────┐                                        │
     │                    │                                        │
     ▼ all pass           ▼ fails                                  │
  ┌─────────────┐    ┌────────────┐                                │
  │ Phase 2:    │    │ Extend to  │                                │
  │ Stage to    │    │ N=100 or   │                                │
  │ $1-2M S1    │    │ investigate│                                │
  │ + begin S2  │    │ specific   │                                │
  │ research    │    │ failure    │                                │
  └──────┬──────┘    └────────────┘                                │
         │                                                         │
         ▼                                                         │
  ┌──────────────────────────────┐                                 │
  │ Tier B DMA migration          │                                │
  │   (IBKR Pro)                  │                                │
  │   Recalibrate η_DMA           │                                │
  └────────────┬─────────────────┘                                 │
               │                                                   │
               ▼                                                   │
  ┌──────────────────────────────┐                                 │
  │ S2 Phase 1 (mid-cap):         │                                │
  │   Overnight continuation      │                                │
  │   Pre-register IV-dispersion  │                                │
  │   transferability test        │                                │
  └────────────┬─────────────────┘                                 │
               │                                                   │
               ▼                                                   │
  ┌──────────────────────────────┐    ┌───────────────────────────▼┐
  │ S2 gate:                      │◄───┤ SMA book building:         │
  │   Same diagnostic battery     │    │   2-5 accredited clients   │
  │   as S1 at Gate 1b            │    │   $500K-$1M each           │
  └────────────┬─────────────────┘    └────────────────────────────┘
               │ pass
               ▼
  ┌──────────────────────────────┐
  │ Phase 3: dual-strategy live   │
  │   S1: $1.5M microcap ceiling  │
  │   S2: $3.5M mid-cap SMA       │
  │   Total: $5M                  │
  │   Quarterly recalibration     │
  │   Rolling-γ drift < 0.15      │
  └────────────┬─────────────────┘
               │
               ▼
       Target state: month 30-36
```

The v1 decision tree had one strategy and three terminal states
(scale / stage / abort). The v2 tree has two strategies (S1 and
S2) in parallel, a business-model track running asynchronously,
and explicit gates at every transition tied to published-methodology
evidence, not point-estimate thresholds.

---

## 7. Publishing program (methodology-credibility infrastructure)

Per the scaling-arc research, publication is **credibility
infrastructure**, not a capital-access lever. The allocator
conversion funnel is publication + sustained track record + warm
network, and publication alone is typically neither necessary nor
sufficient. That doesn't mean don't publish — it means decouple
the paper track from the AUM track and plan for the paper to
compound slowly.

### 7.1 Three-paper arc

**Paper 1: "Signal-Selection-Aware Identification of Market Impact
on Retail-Routed Microcap Flow"** — *JPM or JFDS*, targeted
submission month 6–9.

Novelty: the SDID + HonestDiD + QMP-matching + T+1-regime
identification stack as a *methodological framework*. Demonstrated
on the microcap gap-up signal but generalizes. Claims:

- Signal-driven selection into treated arms makes naive DiD biased
  in a predictable direction; our framework controls for it.
- PFOF routing creates a systematic wedge that lit-venue
  metaorder priors under-estimate by 2–5×; we quantify it.
- Post-T+1 settlement introduces a regime boundary in the outcome
  variable; we test and correct for it.

This is a publishable methodological contribution *independent of
the empirical results*. Even if the empirical estimate comes out
"microcap impact is X bps," the methodology is novel and cite-worthy.

**Paper 2: "Cross-Asset Pre-Registration of Equity Signal
Transferability: Evidence from Microcap-to-Mid-Cap Overnight
Continuation"** — *JPM, JFDS, or RAPS*, targeted month 24–30.

Novelty: the IV-dispersion transferability test as a
pre-registered cross-asset validation method. The empirical
contribution: mid-cap overnight continuation transfers (or doesn't)
per the pre-registered criterion. Either outcome is publishable.

**Paper 3: "Capacity-Constrained Migration Strategies for
Retail-Routed Equity Momentum"** — *JoF or JFE*, targeted month
30–36.

Synthesis. Combines the methodology from Paper 1 and the
empirical results from Paper 2 into a framework for systematic
retail-origin quant scaling across size cohorts. The audience:
academic microstructure researchers + practitioner quants. The
backing: the 24+ month audited own-capital + SMA track record +
two prior publications.

### 7.2 Timing

Each paper arrives asynchronously with the AUM arc. Paper 1 at
month 6–9 compounds credibility through month 24; Paper 2 at
month 24–30 compounds through month 36; Paper 3 at month 30–36
compounds through year 3–5. The AUM arc from $142K → $5M runs on
its own clock (mostly track-record + regulatory + SMA-network
constraints).

### 7.3 Registration venues

Per the scaling-arc research §3, practitioner read-rate order:
*JPM*, *Journal of Investing*, *JAI*, *JFDS* > *JoF*, *JFE*, *RFS*.
SSRN working papers necessary but not sufficient. Conference
attendance: CQA, QWAFAFEW, CFA Institute events, Battle of the
Quants for allocator-adjacent exposure; iConnections and Context
Summits for actual allocator prospecting.

---

## 8. What v2 is explicitly NOT doing (stricter than v1)

v1's non-goals (no code tonight, no ML, no Hasbrouck VAR, no options
overlay, no HJB, no live mid-cap until microcap complete, no joint
(η, γ) at N=30) all stand.

v2 adds:

- **No citing Alimoradian as theoretical cover for the empirical
  estimator**. The paper goes in the reading list as inspiration,
  not justification.
- **No median-of-cohort as primary I_perm estimator**. SDID is
  primary; median-of-cohort is a lower-bound sanity check at best.
- **No pure √(Q/V) functional**. Crossover or nothing.
- **No endogenous V_τ**. Pre-event ADV with gap-day expansion only.
- **No BJZZ signing for microcap cohort matching**. QMP only.
- **No capacity assertions**. Only derived distributions via
  Jensen–Kelly–Pedersen.
- **No "$5M in 12 months" framing**. The target is $5M in 36
  months via a two-strategy + own-capital + SMA architecture.
- **No fund at $5M**. SMA-based from the outset; fund economics
  are rational at $20M+.
- **No single-estimator Phase-1 gate**. Cross-estimator consensus
  or don't deploy capital.
- **No ignoring T+1**. Restrict training data or include regime
  dummy explicitly.

---

## 9. Architecture-call agenda items produced by this document

This plan surfaces five agenda items for the Tuesday architecture
call. They're ordered by the leverage they create for the $5M-12m
(now 36m) arc:

1. **Ratify the two-strategy portfolio architecture.** The v1
   plan assumed one signal scaled to a single capacity target.
   v2 proposes one signal at its ceiling plus a second
   published-signal-backed strategy running on mid-cap overnight
   continuation. This is a business-model commitment, not just a
   technical choice. Before v2 Phase 0 starts, agree that S2 is
   on the roadmap.
2. **Ratify the RIA+SMA structure and 36-month horizon.** The
   scaling-arc evidence is clear that a fund at $5M is irrational
   and a 12-month own-capital arc is inconsistent with every
   documented case. The v1 plan's "$5M in 12 months" framing was
   the unrecoverable assumption. v2's 36-month RIA+SMA architecture
   is defensible but requires committing to the regulatory
   registration + SMA-network-building work early.
3. **Choose whether to drop Alimoradian citation publicly or
   privately.** Paper 1 cannot cite Alimoradian as theoretical
   cover — the category error is detectable by any competent
   reviewer. But v1 was shared internally with the framing. v2
   drops it. Is the internal communication about the change
   explicit?
4. **Decide on rpy2 vs Python-only estimator stack.** `synthdid`,
   `augsynth`, `HonestDiD` are R-primary. Python has Stata-style
   `sdid_event` via third-party port but no canonical `HonestDiD`.
   Options: (a) rpy2 subprocess for R-primary tools,
   (b) Stata CLI subprocess (simpler, slower), (c) port what we
   need to Python. Option (a) is fastest; (c) is slowest but most
   maintainable. Pre-commit to the choice.
5. **Adopt the state-mutation reversibility rule (f) for the
   calibration loop.** Today's Phase 3 restart discipline
   surfaced the pattern: every state-mutation path needs a
   reversibility test. The calibration loop mutates model state
   (posterior updates, parameter drift, universe changes). Each
   needs: "if this mutation is wrong, how do we roll back?" This
   morning's restart-template pattern is the template for
   calibration-parameter rollouts: pre-snapshot, fit, diff,
   rollback-if-diff-exceeds-bound.

Plus the three items already queued from this morning:
- State-mutation reversibility rule (f) as a new bug-sweep template rule
- Silent-fallback audit retrospective sweep (90 min)
- `docs/research-log/17_restart_template.md` formalization

---

## 10. The pattern, named

v1 was built on a theoretical chain (Alimoradian decomposition
→ empirical estimator → $5M feasibility) that doesn't hold under
scrutiny. The three research artifacts engage v1 at each link:

- aef3259b invalidates the theoretical anchor (category error).
- aef3259b invalidates the empirical estimator (selection + √-law +
  PFOF + T+1 + BJZZ + capacity-math failures, compounding to a
  2–5× bias).
- 616ec49c invalidates the business-model framing (12 months is
  not feasible; SMA not fund; RIA registration; two-strategy
  diversification).

v1's failure mode isn't that it was sloppy — it was plausibly
correct if you accepted its assumptions. The failure is that
**the assumptions were load-bearing and under-examined**. The
same failure mode we've been catching in the code: deterministic
logic that silently does the wrong thing at the edges of its
calibrated regime, hidden behind output that looks correct.

v2 ships with the edges *explicit*: every estimator has a stated
failure regime; every business-model assumption is anchored in
a named primary source; every gate has a defined pass condition
rather than a vibe.

The compounding discipline we're building in the code — rule (e)
"every fail-safe catch ships with a positive-case test" — has a
research-plan analog: **every load-bearing assumption in a
scaling plan ships with the primary-source citation that backs
it and the failure-regime statement that would invalidate it**.
That rule is what separates v1 from v2. It's also what separates
an engineer's plan from a reviewer-defensible plan.

Paper 1 is a publishable methodological contribution because the
discipline we use in the codebase is now visible in the research
plan. Peer reviewers read plans. Our plans need to survive that
read.

---

## 11. What survives from v1

v1 was not wasted work. v2 keeps:

- **The Phase-0 instrumentation list** (NBBO capture, per-partial-fill
  ticks, cohort registry, post-trade tracker, trade-journal schema
  extension). Every item carries over.
- **The observability-before-behavior-change framing.** Phase 0
  ships before any calibration attempt, same as env-audit shipped
  before preflight-abort.
- **The compounding-discipline framing.** Rule (e). Atomic
  commits. Adversarial tests ship with each change.
- **The staged-scale pattern.** v2 refines the numbers and adds
  tracks, but the pattern of "small, measure, verify, expand" is
  unchanged.
- **The "experiment framework hosts the calibration" observation.**
  Still true. `src/experiments/registry.py` is the right home.
- **The heartbeat-embedded calibration-parameter idea** (v1 §11
  question 4). v2 makes it non-optional: current calibrated η, γ,
  breakdown M̄, and Pareto-k appear in the heartbeat.

What doesn't survive: the Alimoradian citation chain, the
median-of-cohort estimator, the pure-√ functional, the endogenous
V_τ, the single-strategy 12-month $5M framing, the fund-centric
regulatory assumption.

---

## 12. The one-paragraph summary for readers who skip ahead

**The frontier critique invalidates v1's theoretical anchor, biases
v1's empirical estimator by 2–5×, and the scaling-arc research
invalidates v1's 12-month business-model framing. v2 replaces the
Alimoradian chain with a propagator-kernel framework (Abi Jaber–Neuman
2025 plus Bouchaud et al.), replaces median-of-cohort with SDID
+ HonestDiD + cross-estimator triangulation, replaces √ with
Benzaquen–Bouchaud crossover, exogenizes V_τ, adopts QMP signing,
restricts data to post-T+1, calibrates η from own fills, and
derives capacity via Jensen–Kelly–Pedersen. v2 reframes $5M as a
36-month target reached via a two-strategy portfolio (microcap
cascade at its corrected ceiling + mid-cap overnight continuation
with pre-registered IV-dispersion transferability test) plus an
RIA+SMA structure, not a fund. Paper 1 (methodology) submits at
month 6–9; Paper 2 (empirical mid-cap transferability) at month
24–30; Paper 3 (capacity-migration synthesis) at month 30–36.
The target AUM is unlocked by diversifying the capacity constraint
across two published signals, not by scaling up a single signal
beyond where the literature says it lives.**

---

*End of v2 plan. No code. Next action: discuss this document at
the Tuesday architecture call alongside the five agenda items in §9.*
