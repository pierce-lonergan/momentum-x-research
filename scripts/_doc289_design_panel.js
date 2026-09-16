export const meta = {
  name: 'doc289-design-panel',
  description: 'Divergent panel: propose maximally-novel edge experiments through distinct scientific lenses, then judge',
  phases: [
    { title: 'Propose', detail: '6 lenses each design one never-done experiment' },
    { title: 'Judge', detail: 'rank by novelty x rigor x executability x beauty; synthesize' },
  ],
}

const REPO = '<repo-root>'

const CONTEXT = `momentum-x is an autonomous LLM day-trading bot on low-float small-cap GAP-UP stocks (Alpaca paper). Over ~4 months it has EXHAUSTIVELY tested for a trading edge and found essentially NONE:
- No long edge in PRICE momentum or in INFORMATION (earnings/filings/news), across all horizons and universes (docs 250-261).
- No SHORT edge net of borrow cost (short door tombstoned, doc 284).
- Rocket (big-runner) selection ex-ante is ~random in the right tail; cross-regime money-gate kills it (BET#3).
- ML models (v3-v6, TabPFN, MomTrans, TCN, focal) that looked like breakthroughs in-sample reverted out-of-regime (docs 100-155).
- The honest measured frontier is ~+0.13%/day central; 2%/day is a negative existence proof (doc 283).
- Method lessons (docs 273-279): recursive self-audit does not converge; cross-model peer review is the gold standard; universe-membership must be audited before inference; per-experiment p<.05 is dead as a promote-criterion; denominator honesty is mandatory.

EVERY prior experiment framed the problem as PER-NAME PREDICTION: "will ticker X continue?" measured by per-name predictive accuracy. That lens is thoroughly dead.

ASSETS ON HAND (what an experiment can actually use):
- 59 morning COHORTS (2026-04-14 .. 07-09): each session is a set of ~15-40 low-float names that gapped together. Per name, a pre-decision cross-section: gap_pct, rvol, float_shares, premarket_volume, market_cap, has_news_catalyst, mfcs, price, plus intraday snapshots of current_price through the day (data/features/features_*.jsonl).
- An authoritative local minute-bar warehouse for intraday paths (lands T+1).
- Catalyst/news presence flags (has_news_catalyst); some filings/news text from the doc-259/261 work.
- An LLM (can embed catalyst text, score narratives, act as a semantic metric).
- The doc-280 arena fill model (cost-realistic fills), a borrow pricer, and a rigorous acceptance harness (bootstrap CI, chronological-half split, permutation null, cross-regime gate).

THE MANDATE (Pierce): treat the problem space as a UNIVERSE and yourself as a curious civilization turning chaos into something more beautiful. Design an experiment that has NEVER been done here, coalescing ideas never thought to combine. It must be principled (not "throw model N+1 at it"), and it must have a CHEAP EARLY KILL so an honest negative is fast. The beauty is in a definitive answer -- a LAW about what is/ isn't knowable -- not a pretend-positive.`

const LENSES = [
  { key: 'information-theorist', lens: `Through the lens of INFORMATION THEORY & rate-distortion: think in channel capacity, mutual information, Fano bounds, minimum description length, predictability-of-predictability. Design an experiment that MEASURES a fundamental bound (how much is knowable at all, cost-adjusted) rather than fitting a model. The prize: prove an edge is impossible (chaos has a floor) OR localize exactly where it must live.` },
  { key: 'physicist', lens: `Through the lens of PHYSICS & conservation laws: is attention/liquidity a CONSERVED quantity merely REDISTRIBUTED within the daily cohort (zero-sum flow) vs created/destroyed? Think symmetries, order parameters, phase transitions, critical points, fluctuation-dissipation. Design an experiment that looks for a conserved quantity or a phase transition in the cohort that determines where capital flows.` },
  { key: 'topologist', lens: `Through the lens of GEOMETRY & TOPOLOGY: the cohort each morning is a point cloud in feature space; the tape is a trajectory. Think optimal transport (how the volume/attention distribution FLOWS intraday), persistent homology / the SHAPE of the data, manifold position, relative geometry. Design an experiment where the SHAPE or the FLOW-geometry -- not any single coordinate -- carries the signal per-name models averaged away.` },
  { key: 'ecologist', lens: `Through the lens of COMPLEX SYSTEMS & ECOLOGY: the cohort is a competitive ecosystem of names fighting for one finite pool of retail attention (a niche). Think competitive exclusion, carrying capacity, relative fitness, reflexivity, herding cascades, agent-based emergence. Design an experiment where a name's RELATIVE fitness within its cohort (not its absolute traits) predicts which one wins the attention, and test whether that is exploitable.` },
  { key: 'mechanism-designer', lens: `Through the lens of ECONOMICS & MECHANISM DESIGN: who is on the other side, and what is the equilibrium? Think adverse selection, the winner's curse, the marginal informed trader, put-call parity leaks, borrow/locate frictions as information, dealer inventory. Design an experiment that isolates a STRUCTURAL friction or a counterparty-behavior regularity that survives costs -- an edge from market plumbing, not prediction.` },
  { key: 'adversary', lens: `Through the lens of the ADVERSARY / falsificationist: assume any edge we "find" is an artifact. Design an experiment whose PRIMARY output is a rigorous UPPER BOUND on any achievable edge in this universe, built to be maximally hard to fool (leakage-proof, cost-honest, multiplicity-corrected, cross-regime). The beautiful result here is a credible ceiling that ends the search or proves exactly one crack worth widening.` },
]

const DESIGN_SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['title', 'one_line', 'never_combined', 'hypothesis', 'why_missed', 'method', 'stage0_kill', 'executable_now', 'data_needed', 'expected_verdict_space', 'beauty', 'novelty_self_score'],
  properties: {
    title: { type: 'string' },
    one_line: { type: 'string', description: 'the experiment in one sentence' },
    never_combined: { type: 'string', description: 'which >=2 method families / ideas are combined that have NEVER been combined in this project' },
    hypothesis: { type: 'string', description: 'the precise, falsifiable hypothesis' },
    why_missed: { type: 'string', description: 'why every prior per-name experiment would have been blind to this' },
    method: { type: 'string', description: 'the concrete measurement/estimator and the pre-registered test + null' },
    stage0_kill: { type: 'string', description: 'the CHEAP early falsification that kills it fast if wrong' },
    executable_now: { type: 'boolean', description: 'can it run on the assets-on-hand this session?' },
    data_needed: { type: 'string', description: 'exactly what data/fields it consumes' },
    expected_verdict_space: { type: 'string', description: 'the possible outcomes and what each would MEAN (a law either way)' },
    beauty: { type: 'string', description: 'why the result is beautiful regardless of sign' },
    novelty_self_score: { type: 'integer', description: '1-10 how novel vs everything this project has done' },
  },
}

phase('Propose')
const designs = await parallel(LENSES.map((L) => () =>
  agent(`${CONTEXT}\n\nYOUR LENS: ${L.lens}\n\nPropose ONE experiment. Be bold and specific and PRINCIPLED. It must combine ideas never combined here, be executable on the assets on hand (or say exactly what is missing), and have a cheap early kill. Do NOT propose "train another model." Return the schema.`,
    { label: `design:${L.key}`, phase: 'Propose', schema: DESIGN_SCHEMA, effort: 'high' })
    .then((d) => ({ lens: L.key, design: d })).catch(() => null)
))

phase('Judge')
const valid = designs.filter(Boolean)
const judged = await agent(
  `${CONTEXT}\n\nSix designers each proposed an experiment through a different lens. Rank and synthesize into a decision-ready recommendation for what to actually build THIS session.\n\nDESIGNS:\n${JSON.stringify(valid, null, 2)}\n\nDeliver: (1) a ranking by (novelty x principled-rigor x executability-now x beauty), with a one-line why for each. (2) THE RECOMMENDED experiment to build -- which may be a SYNTHESIS that grafts the best mechanism from one design onto another (say exactly which pieces from which lenses). (3) its pre-registered Stage-0 kill and Stage-A existence test with a concrete null and acceptance threshold. (4) the single sharpest way it could still fool us, and the guard against it. Favor an experiment that yields a LAW (a knowability/impossibility result) over one that just might fit. Be concrete enough that an engineer can start building from your answer.`,
  { label: 'design-judge', phase: 'Judge', effort: 'high' }
)

return { designs: valid, recommendation: judged }
