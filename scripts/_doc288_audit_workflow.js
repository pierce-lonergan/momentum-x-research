export const meta = {
  name: 'doc288-edge-corpus-audit',
  description: 'Adversarial re-audit of the momentum-x edge-search corpus for bugs/conflations that misrepresented conclusions',
  phases: [
    { title: 'Audit', detail: '5 error-class hunters scan the verdict-bearing corpus' },
    { title: 'Verify', detail: 'a skeptic tries to refute each finding' },
    { title: 'Synthesize', detail: 'dedupe, rank, emit the fix work-list' },
  ],
}

const REPO = '<repo-root>'

const PREAMBLE = `momentum-x EDGE-CORPUS RE-AUDIT, Saturday 2026-07-11 (READ-ONLY; do NOT edit prod code, do NOT run the live bot; analysis scripts to data/research/doc288/ or stdout only; read-only Alpaca GET is fine; PYTHONIOENCODING=utf-8; .env has ALPACA keys, NEVER print them). Repo: "${REPO}" (the session cwd may be an EMPTY worktree -- cd to the real repo).

MISSION: find MISTAKES that could have CONFLATED our conclusions or MISREPRESENTED our results -- methodological errors AND code bugs -- in the experiments this project ran in its multi-month search for a trading edge. The project's headline conclusions are NEGATIVE (no long edge in price or information across the gapper universe; short door dead net of borrow; frontier ~+0.13%/day). Your job is to test whether any of those verdicts (or any "breakthrough" that was later walked back) was driven by a BUG rather than by the market.

A finding ONLY matters if fixing it would PLAUSIBLY FLIP OR MATERIALLY CHANGE A CONCLUSION -- i.e. it makes a dead thing look alive, a live thing look dead, or inflates/deflates a headline number enough to change the decision. Cosmetic bugs, style, and already-known/already-fixed issues do NOT count. If you cannot tie a defect to a specific verdict it would move, drop it.

CORPUS MAP (verdict-bearing):
- No-edge arc (docs 250-261): rocket_basket_exits_doc250.py, universe_gate_scan_doc251.py, coiled_catalyst_backtest_doc254.py, prior_runner_veto_doc254.py, rocket_watchlist_engine_doc255.py, multi_day_candidacy_signal_doc258.py, pead_stage_a_doc259.py / pead_stage_a_v2_doc259.py, stage_b_text_probe_doc260.py / stage_b_hostile_doc260.py, catalyst_handicap_pilot_doc261.py.
- Microstructure/rocket arc (docs 242-248): build_tick_features_doc242.py, build_true_ofi_doc244.py, build_xregime_tick_features_doc245.py, build_rocket_trade_cache_doc248.py, build_microstructure_features*.py.
- Falsification arc (docs 273-279): _doc273_*.py, doc274_*.py, gate_doc275.py, _doc276_*.py (esp. _doc276_seedmatch.py), _doc277_seedmatch.py, _doc278_*.py, _doc279_*.py.
- Profit arc (docs 280-284): _doc280_counterfactual.py, _doc280_posture_backtest.py, _doc280_retest.py, _doc284_borrow_pricer.py, _doc284_rocket_gate_ledger.py.
- Corpus builders (shared, high-blast-radius if wrong): build_cross_regime_corpus.py, build_exit_label_corpus.py, build_intraday_paths.py, build_multiday_hold_dataset.py, entry_ab_replay_doc265.py, adversarial_audit_doc234.py, backtest_*.py.

STANDING KNOWN GOTCHAS (verify each is/was handled where relevant; a re-introduction is a real finding):
- trades_v1 parquet: stored ts_et is UTC-MISLABELED. ET must be derived from raw sip_timestamp. 21 scripts reference ts_et -- any that uses ts_et for intraday time-of-day (entry-hour gates, opening-range windows, first-30-min, VWAP-time) is SUSPECT of a 4-5h shift that conflates intraday windows. THIS IS THE HIGHEST-VALUE LEAD.
- day_aggs is an ERRATIC proxy (doc 278) with warehouse holes (2026-05-29, 2026-06-30 whole-session); coverage/universe-membership must be audited before inference.
- PHANTOM-P&L: book state must change only on confirmed broker evidence; trade_results.jsonl has known phantom rows.
- Same-day vs next-day alignment (the "shadows run a day behind" class, doc 51).

Read the doc(s) for a verdict, then read the CODE that produced its numbers, then look for your assigned error class. Prefer depth on the load-bearing verdicts over breadth. Cite file:line and quote the offending code. For each finding, state the SPECIFIC verdict/number it would move and in which direction.`

const FINDINGS_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['findings'],
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        required: ['title', 'error_class', 'file', 'line', 'offending_code', 'what_it_conflates', 'verdict_moved', 'direction', 'severity', 'confidence', 'proposed_fix'],
        properties: {
          title: { type: 'string', description: 'one-line defect name' },
          error_class: { type: 'string', description: 'temporal-leakage | selection-survivorship | multiplicity-overfit | cost-fill-denominator | code-bug' },
          file: { type: 'string', description: 'repo-relative path' },
          line: { type: 'integer', description: '1-indexed line the defect anchors to (best estimate)' },
          offending_code: { type: 'string', description: 'the actual quoted code/line(s)' },
          what_it_conflates: { type: 'string', description: 'precisely what is wrong and why it corrupts the result' },
          verdict_moved: { type: 'string', description: 'the specific doc/verdict/number this would change' },
          direction: { type: 'string', description: 'which way it moves the conclusion (e.g. made a real edge look dead / made noise look like edge / inflated AUC)' },
          severity: { type: 'string', description: 'CRITICAL | MAJOR | MINOR' },
          confidence: { type: 'string', description: 'HIGH | MEDIUM | LOW that this is a real defect' },
          proposed_fix: { type: 'string', description: 'concrete, minimal fix + how to re-verify the affected verdict' },
        },
      },
    },
  },
}

const VERDICT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['confirmed', 'verdict_flip_plausible', 'corrected_severity', 'reasoning'],
  properties: {
    confirmed: { type: 'boolean', description: 'true only if you independently reproduced the defect by reading the code/data; default false if uncertain' },
    verdict_flip_plausible: { type: 'boolean', description: 'true only if fixing it would plausibly move the cited verdict/number in the cited direction' },
    corrected_severity: { type: 'string', description: 'CRITICAL | MAJOR | MINOR | NOT-A-BUG' },
    reasoning: { type: 'string', description: 'what you checked to confirm or refute, with file:line evidence' },
  },
}

const DIMENSIONS = [
  { key: 'temporal-leakage', prompt: `YOUR ERROR CLASS: TEMPORAL LEAKAGE / LOOK-AHEAD. Hunt for: (1) the trades_v1 ts_et tz mislabel used for any intraday time-of-day logic (entry-hour gates, opening-range, first-N-min, VWAP-by-time) -- grep every ts_et use and check whether ET-of-day is derived from sip_timestamp or naively from ts_et; a 4-5h shift here silently conflates the whole intraday window and could have killed a real microstructure/rocket signal or manufactured a fake one. (2) features computed with data from AFTER the decision time (future bars leaking into an entry-time feature). (3) label windows that overlap the feature window. (4) same-day compute before the data warehouse is populated (the rocket-gate class). (5) next-day/same-day join misalignment. Focus: build_*features*, build_*corpus*, the doc-242/244/245 microstructure builders, rocket_watchlist_engine_doc255.py, _doc284_rocket_gate_ledger.py, entry_ab_replay_doc265.py.` },
  { key: 'selection-survivorship', prompt: `YOUR ERROR CLASS: SELECTION / SURVIVORSHIP / UNIVERSE-MEMBERSHIP. Hunt for: (1) conditioning on the outcome (e.g. building a "rocket" set using end-of-day returns then testing predictability, or matched controls selected using future info -- the doc-277 leaky-seed-matcher class; read _doc276_seedmatch.py and _doc277_seedmatch.py adversarially). (2) universe membership not audited before inference (doc-273 rule) -- a cohort/watchlist filtered by info not available pre-open. (3) day_aggs holes (2026-05-29, 2026-06-30) silently dropping sessions and biasing the sample. (4) survivorship in the traded/evaluated set (only names that filled, only sessions that ran). (5) train/test rows sharing a ticker-day. Focus: universe_gate_scan_doc251.py, rocket_watchlist_engine_doc255.py, the seedmatch scripts, coiled_catalyst_backtest_doc254.py, build_cross_regime_corpus.py.` },
  { key: 'multiplicity-overfit', prompt: `YOUR ERROR CLASS: MULTIPLE COMPARISONS / OVERFITTING / P-HACKING. Hunt for: (1) many hypotheses/thresholds/features tried, best reported, no multiplicity correction (doc-275 established per-experiment p<.05 is dead as a promote-criterion). (2) hyperparameter/threshold tuning on the same data used to report the result (in-sample leakage into the "OOS" claim) -- the v3/v4/v6 "breakthroughs" (docs 104-155) that later reverted are prime suspects. (3) regime cherry-picking (reporting the window where it worked). (4) a metric optimized then reported on the same split. (5) tiny-n verdicts dressed as significant. For each, estimate how many effective comparisons were hidden and whether the headline survives a correction. Focus: gate_doc275.py, _doc279_factorial.py, the model-arc docs 104-155, pead_stage_a*_doc259.py, stage_b_*_doc260.py.` },
  { key: 'cost-fill-denominator', prompt: `YOUR ERROR CLASS: COST / FILL REALISM / DENOMINATOR HONESTY. Hunt for: (1) fills assumed at prices not achievable (mid/last instead of marketable; fills on unfillable gated tickets counted as real P&L). (2) borrow/short costs omitted or mis-priced (cross-check _doc284_borrow_pricer.py logic). (3) slippage/fees missing or too optimistic. (4) denominator dishonesty (doc-278): reporting per-fill on a filtered subset, dividing by the wrong base, mixing realized+unrealized, counting avoided-loss as gain. (5) the arena fill model's assumptions -- does it credit fills that the deployable estimand should score as $0? Focus: rocket_basket_exits_doc250.py, _doc280_counterfactual.py, _doc280_posture_backtest.py, _doc284_borrow_pricer.py, _doc284_rocket_gate_ledger.py, coiled_catalyst_backtest_doc254.py.` },
  { key: 'code-bug', prompt: `YOUR ERROR CLASS: OUTRIGHT CODE BUGS in the money/metric computation that flip a sign or inflate a metric. Hunt for: (1) wrong joins / merge keys (ticker without date, or date without ticker) silently cross-joining or dropping rows. (2) off-by-one in bar indexing / entry-exit alignment. (3) sign errors (long vs short P&L, buy vs sell, delta direction). (4) aggregation bugs (mean of ratios vs ratio of sums; groupby leaking; cumsum reset missing). (5) NaN handling that biases (dropna after a filter that correlates with outcome; fillna(0) inflating). (6) unit mismatches ($ vs %, shares vs notional, bps vs fraction). Read the actual compute in the load-bearing scripts and trace one number end-to-end. Focus: _doc280_counterfactual.py, _doc280_posture_backtest.py, _doc280_retest.py, _doc284_rocket_gate_ledger.py, build_exit_label_corpus.py, build_multiday_hold_dataset.py, entry_ab_replay_doc265.py, adversarial_audit_doc234.py.` },
]

phase('Audit')
const results = await pipeline(
  DIMENSIONS,
  (d) => agent(`${PREAMBLE}\n\n${d.prompt}\n\nReturn ONLY findings that pass the verdict-move test. Quality over quantity -- a single confirmed sign-flip beats ten cosmetic nits.`,
    { label: `audit:${d.key}`, phase: 'Audit', schema: FINDINGS_SCHEMA, effort: 'high' }),
  (res, d) => {
    const findings = (res && res.findings) || []
    if (!findings.length) return []
    phase('Verify')
    return parallel(findings.map((f) => () =>
      agent(`${PREAMBLE}\n\nYou are an ADVERSARIAL SKEPTIC. Another auditor filed this finding. Your DEFAULT is that it is NOT a real verdict-moving bug -- confirm it ONLY if you independently reproduce it by reading the cited code/data. Try hard to REFUTE: maybe the code derives ET from sip_timestamp elsewhere; maybe the split is clean upstream; maybe the cost is applied later; maybe the verdict does not actually depend on this path.\n\nFINDING:\n${JSON.stringify(f, null, 2)}\n\nRead ${f.file} around line ${f.line} and its callers/data. Decide: is the defect REAL, and would fixing it plausibly move "${f.verdict_moved}" in the direction "${f.direction}"?`,
        { label: `verify:${d.key}:${(f.file || '').split('/').pop()}`, phase: 'Verify', schema: VERDICT_SCHEMA, effort: 'high' })
        .then((v) => ({ finding: f, verdict: v }))
        .catch(() => null)
    ))
  }
)

phase('Synthesize')
const all = results.flat().filter(Boolean)
const confirmed = all.filter((x) => x.verdict && x.verdict.confirmed && x.verdict.verdict_flip_plausible && x.verdict.corrected_severity !== 'NOT-A-BUG')
const summary = await agent(
  `${PREAMBLE}\n\nYou are the SYNTHESIS lead. Below are audit findings, each with an adversarial skeptic's verdict. Produce a decision-ready audit report.\n\nCONFIRMED (skeptic agreed real + verdict-moving):\n${JSON.stringify(confirmed.map((x) => x.finding), null, 2)}\n\nALL (incl. refuted, for context):\n${JSON.stringify(all.map((x) => ({ f: x.finding, v: x.verdict })), null, 2)}\n\nDeliver: (1) a ranked FIX WORK-LIST of the confirmed verdict-moving defects (most consequential first), each with file:line, the exact minimal fix, and the specific re-verification (which experiment to re-run and what number to check). (2) which project VERDICTS are now IN DOUBT and must be re-run before being trusted. (3) an explicit list of what you checked that was CLEAN (so the negative results we keep are strengthened, not just doubted). (4) any dangerous pattern that recurs across scripts (e.g. a shared builder bug that contaminates many downstream experiments). Be denominator-honest and precise; do not inflate the count of "bugs" to seem thorough.`,
  { label: 'audit-synthesis', phase: 'Synthesize', effort: 'high' }
)

return { confirmed_count: confirmed.length, total_findings: all.length, confirmed: confirmed.map((x) => x.finding), report: summary }
