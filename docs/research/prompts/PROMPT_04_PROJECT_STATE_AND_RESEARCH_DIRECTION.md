# RESEARCH PROMPT 4: MOMENTUM-X — Project State, Verified Findings, and Multi-Session Research Direction

**Author**: synthesis as of 2026-04-28 (after the catalyst-only inconclusive verdict)
**Purpose**: comprehensive project state + research direction document. Serves as the primary research prompt for many subsequent sessions until the central question — "is there a strategy class + data combination that produces demonstrable edge?" — is either answered affirmatively or definitively rejected.
**Last commit referenced**: `3dac051` on `develop`, pushed to `origin`.
**Status**: project pivoted from execution-mode to research-mode. Halt switch (`MOMENTUM_HALT_NEW_ENTRIES`) stays on indefinitely.

---

## ⚠️ §-1 — CRITICAL CORRECTION (added 2026-04-29) — READ BEFORE EVERYTHING ELSE

**This document was written under the false assumption that starting equity was $150K.** Pierce corrected on 2026-04-29: starting equity was **$100K**, today equity is **$140,654 (+40.6% over ~3 months)**. The "no demonstrated edge" verdict throughout this document is wrong. The harness simulation produced no edge; **the actual broker P&L shows +$37,463 realized over 280 closed trades**.

But this is not a clean win. **Broker-truth analysis** (`docs/oos/2025-11-to-2026-04_broker_truth_analysis.md`, `docs/research-log/68_starting_equity_correction_and_broker_truth.md`) reveals:
- **CRCA on March 2 (held 5 days, $3.03 → $38.81, +1180%)** made $40,985.
- **Subtracting CRCA: -$3,521 across the other 278 trades.**
- Win rate: **23.9%** (67 wins / 209 losses). Profit factor: 2.5.
- All gains in March 2026 (+$39,612). Feb -$639, April -$1,509.
- Intraday trades (262): **-$5,675**. Carry trades (18): +$43,138 (CRCA dominates).
- Shuffle test: shuffled mean Sharpe **+8.09 > baseline +3.29.** Random pairings beat the strategy on this trade pool.

**The corrected operational interpretation**:
- The intraday gap-up momentum thesis is FALSIFIED by broker truth.
- The strategy makes money via long-tail catalyst-driven multi-day holds.
- The +40% gain is real but **single-trade-dependent**. One quarter without a CRCA-class event = strategy loses 6-10%/month.

**What still stands** (the framework's value):
- Limit-aware fill, prod-mirror replay $0.34 firewall, falsification suite, 30/0 discovery rate.
- BAR-1 timing falsification (T+15min lift COLLAPSES) — correct AS A FINDING about HARNESS sweeps, not about prod's actual decisions.
- All infrastructure work; all stop conditions correctly handled.

**What needs revision throughout this document**:
- §0 TL;DR claims "no demonstrated edge" — should read "harness simulation shows no edge; broker truth shows +40% concentrated in CRCA single-trade".
- §1 mission says "$150K → $1M (6.67×)" — actual is "$100K → $1M (10×)" target; current $140K is 14% of the way there.
- §2 negative findings about strategy-class are scoped to "harness simulation" not "live strategy."
- §3 catalyst hypothesis is now PARTIALLY EVIDENCED by CRCA being a clear catalyst event in the broker-truth top trades.
- §6 research tracks need a Track 6 added: "audit CRCA + understand the actual prod strategy's behavior on multi-day holds."
- §13 closing note should add: "the framework's value is preserved; its claims about strategy edge were scoped to the wrong measurement."

**Three corrected forward paths** (replacing yesterday's "Track 1 / 2 / 3 / 4 / 5"):

α — **LEAN INTO catalyst-catcher**: explicitly target multi-day catalyst holds, drop intraday momentum, accept lottery distribution. Requires catalyst-identification machinery.

β — **LEAN OUT of catalysts, find better intraday signal**: try mean-reversion / time-of-day / volume-profile. Drop the LLM ensemble overhead. Risk: we may have already tested everything that works.

γ — **HALT + DEEP STUDY**: the +40% gain is real but unrepeatable. Halt new entries until we audit CRCA's March 2 trade specifically (was BAR-1 EXIT supposed to close it at T+60s? Did Bug Z / Bug AR fail it open? Was there manual intervention?). Without this audit, the strategy can't be reproduced; without reproducibility, it isn't a strategy.

**Halt switch posture (corrected)**: argument FOR halt is no longer "no demonstrated edge"; it is "we don't yet understand WHAT specifically produced the +40%, so we can't responsibly evaluate the variance." Argument AGAINST halt is "the strategy IS making money on real capital." Operator decision; halt remains default-False until explicitly set.

**Discipline finding logged**: data-source-validity is a prerequisite for measurement-validity. Future sessions MUST:
1. Validate starting equity against broker on session start.
2. Compute realized P&L from broker activities, NOT from any project-internal log file, when the question is "is the strategy profitable?"
3. Treat harness outputs as proxies for STRATEGY-ALTERNATIVES analysis, not as ground truth for STRATEGY-PERFORMANCE measurement.

**Read the rest of this document with the §-1 correction overlaid on every claim.** The infrastructure findings (§2.1, §5, §8, §11, §12) are correct. The strategy findings (§2.2, §3, §6, §10) need scoping to "harness simulation; broker truth requires separate analysis." The operational guidance (§7 decision points) is reframed by §-1 above.

---

---

## §0 — EXECUTIVE SUMMARY (TL;DR)

MOMENTUM-X is a small-cap gap-up momentum trading bot built around a multi-agent LLM evaluation pipeline, designed to compound a $150K paper account to $1M (6.67×) over ~12 months. After roughly 7 sessions of rigorous infrastructure work and falsification testing, the central finding is:

**On the corpus we have (86 sessions, Dec 2025 → Apr 2026), with the strategy class as currently configured (sub-$15 small-cap gappers + LLM ensemble + BAR-1 exit at T+60s), the rig produces no demonstrated edge that survives skeptical testing.**

This is not a measurement failure. It is a measurement success. The discipline framework has caught and overturned every optimistic interpretation produced so far:

| Headline | Status |
|---|---|
| BAR-1 timing T+15min lift = 6.6× | COLLAPSES under OOS test + adversarial fade |
| 86-session aggregate Sharpe = +3.776 | OVERTURNED by stratification + shuffle test |
| Catalyst-only OOS variant | INCONCLUSIVE — Finnhub free-tier cap + bar-recordings selection bias produce 0% earnings overlap |
| Catalyst gate sweep (prod-pnl method) | DIRECTION-AGREES at n=11 (small sample) |

The replay rig itself is honest — prod-mirror tests pass at $0.34 Σ |Δ| across 9 of 9 trades, the limit-aware fill model is audit-confirmed for both FAST_PATH OTO and RESCAN limit paths, and the falsification suite has a documented 30/0 production-bug discovery ratio. **What's missing is not better infrastructure; it's better data and possibly a different strategy class.**

The project has reached a fork: continue investing in the current strategy + better data (Track 1, Track 3), explore alternative strategy classes on existing data (Track 2), refine rig fidelity (Track 4), or pursue meta-research on whether the underlying market phenomenon exists (Track 5). This document lays out all five tracks in detail and identifies the operator-decision-points that gate which track is funded.

---

## §1 — THE MISSION (UNCHANGED IN INTENT)

**Goal**: $150K paper account → $1,000,000 in 12 months. Per-trade EV ≈ $190 at 5 trades/day produces ~3.6 years; at 1 trade/day produces ~18 years. The plan is contingent on the strategy producing per-trade EV > 0 net of slippage at scale.

**Strategy class as designed**: sub-$15 small-cap stocks gapping up >5% pre-market, evaluated by a 6-agent LLM ensemble (news/catalyst, technical, fundamental, institutional flow, deep search, risk) that produces directional signals (STRONG_BULL → STRONG_BEAR), aggregated into MFCS, filtered through D124 consensus + debate, sized via half-Kelly with tier brackets, entered via OTO orders with protective stops, exited via BAR-1 (T+60s) or D245 SMART_EXIT or tranche-limit fills.

**Constraint**: paper trading on Alpaca. Halt switch wired into `AlpacaDataClient.submit_oto_order`; defaults False; operator must set `MOMENTUM_HALT_NEW_ENTRIES=1` in launcher env to halt new entries. Existing positions, exits, stop ratchets unaffected by halt.

**The original honest edge statement** (`evaluation/2026-04-28-edge-assessment.md` §1.4):

> "Sub-$15 small caps that gap up >20% on a NAMED, MATERIAL, COMPANY-SPECIFIC catalyst (earnings beat with revenue numbers, FDA approval, M&A with terms) tend to continue intraday. Edge comes from being long the catalyst-confirmed setup before the broader market re-prices."

**This hypothesis remains untested at meaningful scale.** Two confirmed wins (LIDR 4/24 +$421, OGN 4/27 +$515) both had clear named catalysts. Everything else in the trade history is noise. n=2 wins is too small for any statistical claim.

---

## §2 — WHAT'S BEEN PROVED (with rigor)

### §2.1 — Infrastructure findings (all confirmed)

1. **The replay rig is honest where prod-mirror truth is available.** 9 of 9 OK trades replay within $20 P&L tolerance, Σ |Δ| = $0.34. Validated against 5 sessions (4/22-4/28). The rig does NOT lie about the trades it has truth data for.

2. **Limit-aware fills are correct for both FAST_PATH OTO and RESCAN_LIMIT paths.** Audit-confirmed: 5 of 6 RESCAN trades had structural >100 bps residuals when bar.open was used as fill proxy; the limit-aware model fills at the actual limit price when within bar [low, high]. Prod-mirror replay still passes $0.34 firewall.

3. **The 30/0 discovery rate held across the entire program.** 30+ production bugs surfaced (Bug A through Bug AS plus Bug AO orchestrator hook, Bug AP overnight detection, Bug AR cancel-and-coordinate, etc.). Zero of these patches introduced regressions. The bug-letter alphabet pattern + falsification discipline produced this ratio.

4. **The falsification framework reliably catches false positives.** Two consecutive sessions surfaced optimistic results (BAR-1 6.6× lift, 86-session aggregate Sharpe +3.776). Both were rejected by orthogonal stress tests:
   - **Stratification by data-completeness**: aggregate vs strata divergence catches simulator artifacts.
   - **Shuffle test (entry-exit re-pairing)**: catches autocorrelation / sequencing artifacts. Tonight's shuffled mean Sharpe was +7.05 vs baseline +3.78 — random pairings beat the strategy.
   - **Adversarial fade modeling (A.5)**: catches arena under-modeling adverse selection on long holds.
   - **Out-of-sample test (A.4)**: catches in-sample overfit.

   Discipline rule pre-committed: if Sharpe > 2.0, falsification mandatory before claim. The rule has fired twice; both times the headline was overturned or relegated.

5. **The discipline pattern of "ship the framework, decline misleading fits" is correct.** Slippage calibration v0.1 was reverted on stop condition; v2 was sanity-capped to identity. Both decisions preserved 9/9 in-tolerance replay rather than shipping multipliers that would distort downstream sweeps. The framework's value is partly in what it doesn't ship.

### §2.2 — Negative findings (also confirmed, with the same rigor)

1. **The current strategy class does NOT produce edge in the corpus we have.** First 86-session OOS:
   - Aggregate Sharpe +3.776 (SUSPECT bucket)
   - Stratification: full_decision_row (n=3) Sharpe 0.000; partial_news_only (n=27) Sharpe **-4.27**; synthesized_no_news (n=225) Sharpe +4.36
   - **In the high-quality data strata where we have actual news classifications, the strategy LOSES money.** The aggregate's positive number is an artifact of the synthesized stratum where placeholder filter values + bar-corpus selection bias produce false positives.
   - Shuffle test: random entry-exit re-pairing produces mean Sharpe +7.05 — HIGHER than baseline. The strategy isn't adding value; the apparent edge is in the bar corpus's price distribution.

2. **BAR-1 timing changes (T+15min vs T+60s) are NOT a candidate config.** Pre-falsification headline was 6.6× lift in Σ P&L. Post-falsification (A.4 OOS + A.5 adversarial): COLLAPSES. The lift was a combination of in-sample overfit + arena under-modeling adverse selection on long holds. This was tonight's most consequential negative finding because it removed a tempting candidate config from consideration.

3. **Slippage cannot be calibrated to the existing residual data.** Per doc 64, 4 of 18 residuals are LIMIT-vs-bar-open structural mismatches (-1487 bps OGN, -1042 bps ATER); the remaining 14 cleaned residuals still contain intra-bar drift, not pure slippage. Without intra-bar tick data we cannot separate. The naive side-only fit produces 18× multiplier which is structurally unrealistic. v2 ships identity.

4. **The catalyst-only test cannot run on Finnhub free-tier + bar_recordings corpus.** 0% overlap at ±1 trading day, 0.03% at ±5 days. The test is data-blocked; the catalyst hypothesis is neither rejected nor validated.

### §2.3 — Methodological findings (logged, not bugs)

1. **The arena fill model has zero stochasticity in PRICE for market orders.** Only partial-fill qty is randomized, and the BAR-1 sweep ignores qty effects. Multi-seed variance test is degenerate as currently wired.

2. **Uniform spread multipliers preserve hold-time ratios by construction.** Differential per-hold multipliers are needed for proper falsification via that mechanism.

3. **A.5 adversarial fade is structurally inapplicable to T+60s exits** (5-min fade threshold). A.5 needs an exit-policy-aware variant for OOS runs at short hold times. Currently the test runs and reports "no effect" which doesn't actually validate the result.

4. **Per-ticker entry-time alignment is mandatory for sweep arms** (catalyst gate ON vs OFF). Without it, gates that filter candidates produce arms that take DIFFERENT trades for the same tickers — making the "gate effect" measurement an apples-to-oranges comparison.

---

## §3 — WHAT'S NOT BEEN PROVED (and why)

### §3.1 — The catalyst hypothesis (single most important open question)

**Statement**: "Sub-$15 small caps that gap up on a NAMED, MATERIAL, COMPANY-SPECIFIC catalyst (earnings beat / FDA approval / M&A) produce per-trade EV > 0 in the 60-second window after entry, sufficient to net positive Sharpe at 1-3 trades/day."

**Why not proved**:
- **Sample size**: 2 wins (LIDR 4/24, OGN 4/27) is below any statistical threshold.
- **Data accessibility**: tonight's catalyst-only OOS test couldn't fire because Finnhub free-tier returns 1500 events for 5 months (vs ~5K-10K full universe), and the small-cap names that gap on earnings aren't in the cap-truncated set.
- **Internal classification gap**: the system's own news_agent classifications are only persisted in decision_row corpus for 4/28 (Bug AO orchestrator-hook gap). Pre-4/28 sessions cannot be stratified by internal catalyst tag without log scraping.

**Why this matters**: this is the only positive signal in months of data. If true, the strategy class is salvageable with a different filter; if false, the strategy class is wrong.

**Required to test**: full earnings calendar coverage (paid Finnhub ~$60/mo, or Polygon, or IEX) OR the system's decision_row corpus extended to all sessions (Bug AO retro-fix, multi-session backfill).

### §3.2 — The strategy-class question

**Statement**: "Sub-$15 small-cap intraday gap-up momentum is a real phenomenon at <60s timescales after open."

**Why not proved**: literature is mixed. The strategy assumes that retail money chases gap-ups, broker arbitrage takes time to re-price, and there's a 30-90 second window where being long captures alpha. Empirical evidence in our 86-session corpus:
- aggregate strategy on this assumption produces Sharpe in synthesized-stratum that's a simulator artifact
- the 2 wins both happened on tickers with major catalysts
- without the catalyst, the strategy appears to lose money

**Possible meta-explanations** (not exhaustive):
1. The phenomenon is real but only on catalyst-confirmed gappers (specific subset, requires §3.1 to test)
2. The phenomenon is real but at a longer timescale (5-15 min) — but BAR-1 sweep falsification already overturned that
3. The phenomenon was real pre-2024 zero-commission saturation but has decayed since (regime decay)
4. The phenomenon was never real and the literature is publication-biased toward positive results

**Required to test**: depends on which meta-explanation. (1) needs paid earnings data. (2) is closed (BAR-1 overturned). (3) needs literature review on regime decay specifically post-Robinhood. (4) is a meta-claim that requires comparing our results to multiple independent backtests of the same strategy class — out of scope for this project alone.

### §3.3 — Whether different strategy classes work on this corpus

The current pipeline tests sub-$15 gap-up momentum. **Five other strategy classes have not been tested** on the same bar corpus:
1. Mean-reversion (sub-$15 gap-DOWN that recovers)
2. Multi-day overnight holds (catalyst confirmed, hold to next-day close)
3. Pair-trades / sector rotation (long winner / short loser within sector)
4. Different price tier ($15-$50, $50+, options)
5. Volatility events (VIX > X% triggers different rules)

Each of these is a multi-session research question. Some may be cheaper than waiting for paid earnings data.

### §3.4 — Whether the rig's modeled exits represent reality

For trades NOT in the prod-mirror truth corpus (i.e., 76 of 86 sessions), the exit model is bar-anchored (fill at bar.open of the exit minute). The BAR-1 falsification verdict suggests this model under-rewards adverse selection (synthesized stratum looks too positive in arena vs prod-pnl-based stratum). The fix would require:
- Multi-bar limit walk-forward (for trades where prod's actual exit was at a tranche-limit price the arena bar-anchored model doesn't capture)
- Per-side / per-hold-time slippage model (currently identity per doc 64)
- Adversarial selection model (currently only A.5 with 5-min threshold)

**None of these can be calibrated without intra-bar tick data.** Polygon ($79+/mo) or Databento (variable) provide this; without it, modeled exits remain bounded by these caveats.

---

## §4 — STRUCTURAL DATA GAPS (the constraints that actually bind)

### §4.1 — Earnings calendar
- **What we have**: Finnhub free-tier, capped at 1500 events for the corpus window. 0% overlap with bar_recordings corpus.
- **What we need**: full US-equity earnings universe (5K-10K events for 5 months). Paid Finnhub ~$60/mo, Polygon, IEX, or scraping SEC EDGAR for 8-K filings.
- **Cost**: $60-200/mo + integration time (~1-2 sessions).
- **Gates**: Track 3 (catalyst-only test), partial Track 5 (regime decay analysis).

### §4.2 — Intra-bar tick data
- **What we have**: 1-minute OHLCV bars from Alpaca (or backfilled).
- **What we need**: tick-by-tick price + size data within each bar. Allows separating intra-bar drift from broker slippage; allows multi-bar limit walk-forward; allows per-side / per-volume slippage calibration.
- **Cost**: Polygon $79+/mo, Databento variable.
- **Gates**: Track 4 (rig fidelity), per-side slippage v3 calibration, mechanistic limit-fill modeling.

### §4.3 — Bar recorder coverage
- **What we have**: bar_recordings populated by D121 dynamic-subscription path; misses tickers added mid-session.
- **What we need**: D121 BUG-P9 fix to propagate dynamic subscriptions to the bar recorder. Doc 61 §6 Option B.
- **Cost**: ~1-2 sessions of recorder + tests.
- **Gates**: complete coverage of the live trade tape; no more 5-of-12-trades-missing scenarios.

### §4.4 — Trade attribution corpus (Bug AO orchestrator hook)
- **What we have**: trade_context parquet for 4/27 + 4/28 only. 4/22-4/26 have no orchestrator-hook capture.
- **What we need**: forward-going hook that writes attribution rows for every entry, OR retroactive backfill from log scraping for the 86-session corpus.
- **Cost**: forward-going hook ~50 LOC across writer.py + alpaca_executor.py (per doc 63 §3 plan); backfill is a script run.
- **Gates**: catalyst-stratified analyses across the full corpus, validation of the news-classification signal, retroactive comparison of decisions vs outcomes.

### §4.5 — News-event corpus
- **What we have**: news_agent classifications in momentum logs (extractable for 4/22-4/28). No separate news corpus.
- **What we need**: a news-event timeline keyed by (ticker, timestamp, classification, source). Would let us test "news arrives → price moves" hypotheses without depending on the live news_agent.
- **Cost**: depends on source. NewsAPI free-tier, Finnhub news, or scraping headlines.
- **Gates**: causal modeling of news → price reaction (vs the system's current correlation modeling).

### §4.6 — Comparable-corpus benchmarks
- **What we have**: this 86-session run is uncalibrated against any external benchmark.
- **What we need**: independent backtests of the same strategy class on different corpora (other small-cap watchlists, longer time windows). Allows distinguishing "our rig is wrong" from "the strategy class is wrong."
- **Cost**: literature search + replication of a published backtest. ~1-2 sessions.

---

## §5 — THE INFRASTRUCTURE WIN (don't underweight)

It's tempting to read tonight's negative result as a project failure. It is not. The discipline framework's value is precisely what allowed the result to be honest.

**Concrete wins from the rig + framework**:
1. The replay rig validates against prod at $0.34 Σ |Δ| — every future strategy proposal can be tested against this rig with confidence in its measurement.
2. The falsification suite has a documented track record of catching false positives. Pre-committed SUSPECT-RANGE rule for any future Sharpe > 2.0.
3. The bug-letter alphabet + 30/0 discovery rate means infrastructure changes ship without regressions. This is rare.
4. The stratification-by-data-completeness pattern has caught a bias that aggregate-only reporting would have missed twice.
5. The halt switch lets us run experiments without operational risk.

**These wins compound across future research tracks.** Whatever strategy is tested next (Track 1-5) inherits the rig + framework. The infrastructure investment has been front-loaded; the marginal cost of testing the next hypothesis is low.

**The right framing for tonight's result**: the project produced honest evidence that the current strategy doesn't have demonstrable edge, AND a measurement framework capable of producing that evidence. Most projects produce one or the other; producing both is what allows the next iteration to be informed rather than guessed.

---

## §6 — RESEARCH TRACKS (multi-session direction)

Each track is multi-session work. Tracks have explicit prerequisites; some block others.

### Track 1 — Better data infrastructure

**Goal**: close the data gaps in §4 so future tests can actually fire.

**Subtracks**:
- **1.A — Paid earnings calendar** (gates Track 3). Procurement decision: Finnhub paid ($60+/mo), Polygon ($79+/mo), IEX, SEC EDGAR scraping. Operator decision required. Implementation ~1-2 sessions after procurement.
- **1.B — Intra-bar tick data** (gates Track 4 calibration). Polygon or Databento. Larger budget. Operator decision required.
- **1.C — Bar recorder D121 fix** (per doc 61 §6 Option B). Event-driven mirror so dynamic-subscription tickers get captured. ~1-2 sessions.
- **1.D — Bug AO orchestrator hook** (per doc 63 §3 plan). Forward-going attribution capture. ~50 LOC across writer + executor + 1 emit site. ~1 session.
- **1.E — Trade attribution backfill** for pre-4/28 sessions via log scraping. Mechanical. ~1 session.

**Discipline**: do NOT pursue Track 3 or Track 4 calibration without 1.A or 1.B respectively. Doing so produces inconclusive results (as tonight demonstrated for catalyst-only).

### Track 2 — Strategy class exploration on existing data

**Goal**: test whether a different strategy class produces edge on the corpus we already have, without needing additional data.

**Subtracks**:
- **2.A — Mean-reversion baseline**. Same bar corpus; sub-$15 gap-DOWN that recovers within session. Hypothesis: gap-downs over-shoot retail panic and bounce. Test via the existing harness with inverted entry rules. ~1-2 sessions.
- **2.B — Multi-day overnight holds**. Hypothesis: catalyst-confirmed setups continue past intraday horizon. Requires cross-session state continuity (deferred from Block 4 of an earlier session). ~2-3 sessions.
- **2.C — Volatility-conditional rules**. Test whether high-VIX sessions (or session-specific RVOL spikes) produce edge with different rules than low-volatility sessions. Stratification + sweep. ~2 sessions.
- **2.D — Different price tier** ($15-$50 small-mid cap). Same strategy class, different ticker universe. Tests whether sub-$15 microstructure is the issue vs the strategy itself. ~2 sessions, contingent on bar coverage extension.
- **2.E — Sector pair-trades**. Long the sector winner, short the loser within the same gap-up day. Hedged exposure. ~3 sessions.

**Discipline**: each subtrack ships as a separate sweep with falsification verdict before being declared a candidate. Same SUSPECT-RANGE rule as the original strategy.

### Track 3 — Catalyst-specific work (gated on Track 1.A)

**Goal**: actually run the catalyst-only test that tonight couldn't fire.

**Subtracks** (all post-1.A):
- **3.A — Catalyst-only OOS rerun**. With proper earnings data, the test from tonight becomes runnable. Same falsification suite. ~1 session.
- **3.B — Catalyst-type stratification**. Earnings beat vs FDA approval vs M&A vs offering vs contract win. Different catalyst types may have different EV signatures. ~1-2 sessions.
- **3.C — Catalyst confirmation lag analysis**. Does the strategy do better on Day-of-catalyst vs Day-after vs Pre-catalyst-day? Stratification reveals timing constraints. ~1 session.
- **3.D — Combined gate (catalyst + news_signal + RVOL)**. If 3.A surfaces a positive signal, test whether layering filters preserves or destroys the signal. Watch for n-shrinkage to insignificance. ~1 session.

### Track 4 — Rig fidelity improvements (gated on Track 1.B for full closure)

**Goal**: close the rig limitations so modeled-exit results are more trustworthy.

**Subtracks**:
- **4.A — Multi-bar limit walk-forward** (no tick data needed). For OGN-class trades (limit below bar.open at entry minute), walk forward across N bars looking for the first bar where limit is inside [low, high]. ~1 session.
- **4.B — Tranche-limit modeling** (no tick data needed). Mechanistic check: if bar.high crosses prod's recorded tranche limit, model the tranche fill at that price. Captures the kind of upside our 2 wins came from. ~1-2 sessions.
- **4.C — Per-side / per-hold-time slippage v3** (gated on 1.B tick data). With tick data, residuals can be cleanly separated from intra-bar drift. ~2 sessions after 1.B.
- **4.D — Adversarial selection v2** (no tick data needed). Replace A.5's static 50-bps/min fade with empirical fade fitted from prod-corpus exits. ~1 session.
- **4.E — Mid-session recon hooks** (D231 RECON_HARD_BLOCK in arena). Per doc 61 §6 deferred. Useful for testing the recon path itself. ~1 session.

### Track 5 — Meta-research (parallel to all tracks)

**Goal**: understand whether the underlying market phenomenon exists, independent of our strategy + rig.

**Subtracks**:
- **5.A — Literature review on small-cap gap-up momentum decay**. Specifically post-2024 zero-commission saturation. Has the regime changed? Are recent backtests showing decay? SSRN, arXiv-q-fin, journal of trading, etc. ~1-2 sessions.
- **5.B — Capacity ceiling analysis with synthetic ADV impact**. Without L2 we use parametric. Estimate at what AUM market impact would erase whatever edge we hypothetically have. Bounds the $1M target's feasibility. ~1 session.
- **5.C — Compare our 86-session aggregate against an external benchmark backtest**. Find a published backtest of small-cap gap-up momentum on 2025-2026 data. If theirs shows positive Sharpe and ours doesn't, the issue is our rig or our specific filters. If theirs also shows zero/negative, the regime is real and the strategy is dead. ~1-2 sessions.
- **5.D — Best-practice catalyst NLP for trading systems**. Recent academic literature on financial NLP comparing prompt-based vs fine-tuned classifiers. Could improve news_agent fidelity (which would help Track 3.A). ~1 session.

---

## §7 — DECISION POINTS (operator)

The project cannot self-direct between these tracks. The operator decides:

### Decision 1 — Track 1 funding
**Question**: Is paid data (1.A earnings, 1.B tick data) within budget?
- If YES to 1.A only: prioritize Track 3 (catalyst-only with proper data) + Track 1.D-E (orchestrator hook + backfill). Estimated 4-5 sessions to land a defensible catalyst-only OOS verdict.
- If YES to both 1.A + 1.B: also unlock Track 4.C (proper slippage calibration) + Track 4.B (tranche-limit modeling). Estimated 8-10 sessions to land a fully-calibrated 86-session OOS with both catalyst and modeled-exit fidelity.
- If NO: prioritize Track 2 (strategy class exploration on existing data) + Track 5 (meta-research). 6-month horizon for any defensible answer.

### Decision 2 — Strategy commitment
**Question**: Is the project committed to the sub-$15 gap-up momentum thesis, or are alternative classes on the table?
- If COMMITTED: Track 1 + Track 3 are the only path forward. Other tracks deprioritized.
- If OPEN: Track 2 should run in parallel to Track 1's data procurement window. Cheap parallelization.

### Decision 3 — Halt switch policy
**Question**: When (if ever) does the halt switch lift?
- Per pre-committed plan doc §11 SUSPECT-RANGE rule: halt stays on until Sharpe > 0.5 in the high-quality strata in a falsification-surviving OOS run.
- Currently: zero such conditions met. Halt stays on indefinitely.
- Policy question: if Track 3.A produces Sharpe > 1.5 in catalyst-only OOS + survives falsification, what's the operator's next gate? Real-money paper test? Live small-size? 30-day shadow?

### Decision 4 — Termination criteria
**Question**: At what point does the project conclude "this strategy class doesn't work, pivot or stop"?
- Suggested criterion: if Track 1 + Track 3 land catalyst-only OOS with Sharpe < 0.5 across all data-quality strata, the strategy class is rejected. Pivot to Track 2 or Track 5.
- If Track 2 also produces no positive class within 4-6 sessions, the project goal ($150K → $1M via gap-up momentum) is rejected and the operator chooses: different goal, different market, or stop.

---

## §8 — OPERATING PRINCIPLES (the discipline rules that have served us)

These rules have been tested across 7+ sessions of work. They've caught false positives that would have produced misleading headlines. They are the project's most valuable infrastructure.

### Pre-commit rules
1. **Halt switch confirmed at session START AND END.** Never assume.
2. **Audit before code.** No fix lands without evidence that the fix is needed (e.g., the limit-vs-bar-open audit before the limit-aware fill).
3. **Replay $0.34 firewall is non-negotiable.** Any change that breaks prod-mirror replay is reverted, no exceptions.
4. **30/0 discovery rate.** Every new bug surfaced gets a finding doc. Production code is not silently changed.
5. **Stop conditions are mandatory.** If a stop condition trips during a block, it's resolved before the next block runs.

### Falsification rules
1. **Headline > 2.0 Sharpe → A.5 + shuffle test mandatory.** Pre-committed.
2. **Aggregate is fictional without per-data-completeness stratification.** Single numbers without breakdown cannot be claimed.
3. **Direction agreement at small-n is not magnitude validation.** The catalyst gate is direction-agrees but magnitude-contested; this is documented, not promoted.
4. **Sweep results are PROVISIONAL until 86-session OOS validates.** Catalyst gate sweep on n=11 is informative but cannot be operational config.

### Promotion rules
1. **No live config changes regardless of any sweep result.** Halt stays on.
2. **No retroactive labeling of arena as "production-faithful".** Even after Track 4 fidelity work, the rig is "calibrated as of X" not "validated as a production model."
3. **Negative results are documented with the same rigor as positive ones.** Tonight's overturn is a finding equal in value to a positive finding.
4. **The hardest stop condition: if a result looks too clean, it probably is.** The +3.776 aggregate Sharpe was the project's single strongest temptation to skip falsification. The framework caught it.

---

## §9 — RESEARCH SESSION TEMPLATE (for future sessions to follow)

To make this prompt usable across many sessions, a template for what each session ships:

### Pre-session
1. Read the most recent plan doc 58 status section.
2. Re-confirm halt switch.
3. Identify which Track + Subtrack this session pursues.
4. Pre-commit stop conditions for the session's blocks.

### During session
1. Audit before code (where applicable).
2. Each block ends with a verdict / status, not a TODO.
3. Stop conditions trip → revert + document, don't paper over.

### Per-session deliverables
1. One or more finding docs (`docs/research-log/NN_topic.md`).
2. Tests for any new code (regression-test discipline).
3. Plan doc 58 status section updated.
4. Commit with structured message including: blocks completed, stop conditions tripped + handled, halt switch confirmation, discovery rate update.

### Post-session
1. Re-confirm halt switch.
2. Push to remote.
3. Update this prompt's §6 with subtrack progress.
4. Identify next session's Track + Subtrack.

---

## §10 — OPEN PROBLEMS (concrete, prioritized for the next 5-10 sessions)

In rough order of leverage / cost ratio. Each is a session-sized chunk.

1. **Track 1.D — Bug AO orchestrator hook MVP** (~50 LOC, ~1 session). Forward-going trade attribution. Unblocks catalyst stratification on future sessions even without paid earnings data.
2. **Track 4.A — Multi-bar limit walk-forward** (~1 session). Closes OGN-class structural mismatch in arena. Improves rig fidelity for any future modeled-exit work.
3. **Track 1.E — Trade attribution backfill** for pre-4/28 sessions (~1 session). Enables retroactive catalyst stratification.
4. **Track 2.A — Mean-reversion baseline on existing corpus** (~1-2 sessions). Cheapest test of a different strategy class. Existing harness, inverted entry rules. May surface signal where momentum doesn't.
5. **Track 5.A — Literature review on momentum decay 2024-2026** (~1-2 sessions). Cheap meta-research; may save the project from chasing a dead phenomenon.
6. **Track 1.A — Operator decision on paid earnings data**. NOT a coding session — requires user input on budget. If YES: Track 3 unlocks.
7. **Track 4.B — Tranche-limit modeling** (~1-2 sessions). Captures the kind of upside our 2 wins came from. Higher fidelity for catalyst-trade simulation.
8. **Track 2.C — Volatility-conditional sweep** (~2 sessions). Tests whether the strategy class works in specific regimes.
9. **Track 4.D — Adversarial selection v2** (~1 session). Replaces A.5's static fade with empirical fit. Tightens the falsification suite for OOS runs.
10. **Track 5.C — External benchmark comparison** (~1-2 sessions). Distinguishes "our rig is wrong" from "the strategy class is dead."

---

## §11 — APPENDIX A — KEY ARTIFACTS

### Code (rig + harness)
- `mx-arena/arena/exchange.py` — SimExchange (order lifecycle + fills)
- `mx-arena/arena/fill_model.py` — AlpacaFillModel (base) + RealisticFillModel
- `mx-arena/arena/limit_aware_fill.py` — LimitAwareFillModel (Block A this session)
- `mx-arena/arena/spread_model.py` — SpreadModel (calibration-aware)
- `mx-arena/arena/sim_alpaca_client.py` — thin shim mirroring prod's order surface
- `mx-arena/arena/orchestrator_stub.py` — DecisionRow + Policy + earnings-gate
- `mx-arena/arena/consensus_stub.py` — 3-rule consensus (news + gap + rvol)
- `mx-arena/arena/failure_injector.py` — FailureInjector + LIDR Bug AR fixture
- `scripts/arena_replay_session.py` — prod-mirror replay
- `scripts/diff_replay.py` — replay diff report
- `scripts/strategy_harness_run.py` — strategy harness driver
- `scripts/run_block_44_oos.py` — 86-session OOS aggregator
- `scripts/falsify_bar1_sweep.py` — 5-test BAR-1 falsification
- `scripts/falsify_oos_run.py` — A.5 + shuffle + stratification on OOS
- `scripts/sweep_catalyst_gate.py` — prod-pnl-based catalyst gate sweep
- `scripts/sweep_catalyst_gate_arena.py` — arena-driven catalyst gate sweep
- `scripts/sweep_bar1_timing.py` — original BAR-1 timing sweep (overturned)
- `scripts/calibrate_slippage_v2.py` — slippage calibration v2 (identity-shipped)
- `scripts/extract_prod_qty_truth.py` — qty truth from logs
- `scripts/extract_exit_truth.py` — exit truth from logs
- `scripts/build_trade_attribution_corpus.py` — sidecar attribution backfill
- `scripts/audit_bar_coverage.py` — bar coverage audit
- `scripts/backfill_historical_bars.py` — Alpaca historical bar backfill
- `scripts/audit_limit_vs_baropen.py` — limit-vs-bar-open residual audit
- `scripts/convert_bars_to_arena_parquet.py` — JSON → arena parquet
- `scripts/fetch_historical_earnings.py` — Finnhub earnings calendar bulk fetch

### Data
- `data/bar_recordings/{date}/{TICKER}.json` — raw bar recordings (production-shape)
- `mx-arena/data/historical/{TICKER}/{date}.parquet` — converted for arena
- `data/replay/prod_qty_truth.parquet` — qty truth side-table
- `data/replay/prod_exit_truth.parquet` — exit truth side-table
- `data/replay/arena_session_*.parquet` — replay outputs
- `data/oos/per_trade_outcomes.parquet` — 86-session OOS raw trades
- `data/oos/per_trade_outcomes_catalyst_only.parquet` — catalyst-only variant
- `data/instrumentation/trade_attribution/session_date=*/attribution.parquet` — sidecar corpus
- `data/instrumentation/decision_row/session_date=2026-04-28/decisions.parquet` — Bug AO 4/28-only
- `data/instrumentation/trade_context/session_date={2026-04-27,28}/orders.parquet` — Bug AO partial
- `data/calibration/historical_earnings_2025-12_to_2026-04.json` — Finnhub corpus (capped)
- `data/calibration/slippage_residuals.parquet` — calibration residuals (with structural-outlier tags)
- `data/audits/bar_coverage_status.parquet` — bar coverage audit
- `data/audits/limit_vs_baropen_residuals.parquet` — entry-fill residual audit

### Documentation
- `docs/research-log/56_bug_ar_alpaca_body_extraction.md` — Bug AR fix
- `docs/research-log/57_bug_as_stop_oid_corruption.md` — Bug AS fix
- `docs/research-log/58_arena_prod_parity_plan.md` — master plan with all session statuses (§7-§15)
- `docs/research-log/59_spread_calibration.md` — slippage v0.1 (reverted)
- `docs/research-log/60_failure_injector.md` — FailureInjector + Bug AR fixture
- `docs/research-log/61_bar_coverage_gap.md` — D121 dynamic-subscription gap (diagnosed)
- `docs/research-log/62_h2_exit_semantics.md` — prod-mirror exits (H2 fix)
- `docs/research-log/63_bug_ao_orchestrator_hook.md` — Bug AO sidecar MVP
- `docs/research-log/64_slippage_calibration_v2.md` — v2 (identity-shipped)
- `docs/research-log/65_strategy_harness_skeleton.md` — harness skeleton
- `docs/research-log/66_limit_price_aware_fill.md` — limit-aware fill model
- `docs/research-log/67_catalyst_only_test_inconclusive.md` — catalyst-only verdict
- `docs/sweeps/catalyst_gate_pareto.md` — prod-pnl catalyst gate (DIRECTION-AGREES)
- `docs/sweeps/catalyst_gate_arena_driven.md` — arena-driven catalyst gate (CONTESTED)
- `docs/sweeps/bar1_timing_sweep.md` — original BAR-1 sweep (overturned)
- `docs/sweeps/bar1_timing_falsification.md` — BAR-1 5-test verdict (COLLAPSES)
- `docs/oos/2025-12-to-2026-04_oos_run.md` — 86-session OOS (OVERTURNED)
- `docs/oos/2025-12-to-2026-04_oos_run_catalyst_only.md` — catalyst-only variant (INCONCLUSIVE)
- `docs/oos/2025-12-to-2026-04_oos_falsification.md` — A.5 + shuffle + stratification verdict
- `docs/replay_diffs/2026-04-28_error_decomposition.md` — H1 + H2 audit
- `docs/replay_diffs/2026-04-22_to_2026-04-28_diff_report.md` — replay diff
- `evaluation/2026-04-28-edge-assessment.md` — original honest §1.4 statement
- `evaluation/2026-04-28-catalyst-stratification.md` — n=11 stratification
- `audit/2026-04-28-infrastructure-audit.md` — Phase 0 audit
- `gaps/2026-04-28-gap-register.md` — gap register

### Recent commits (most relevant for context)
- `3dac051` — Catalyst-only OOS INCONCLUSIVE
- `d015112` — 86-session OOS Sharpe +3.776 OVERTURNED
- `ca2a72e` — limit-aware fill shipped + arena-driven catalyst sweep CONTESTED
- `fbbc77b` — BAR-1 sweep COLLAPSES + slippage v2 identity + harness skeleton
- `4597005` — H2 prod-mirror + bar backfill + Bug AO sidecar
- `4ab240d` — Rig forensic audit (H1 + H2 decomposition)
- `41d427e` — original BAR-1 timing sweep PROVISIONAL (later overturned)

---

## §12 — APPENDIX B — TERMINOLOGY

- **Halt switch**: `MOMENTUM_HALT_NEW_ENTRIES` env var + `ExecutionConfig.halt_new_entries` config flag. When set, `AlpacaDataClient.submit_oto_order` returns `halted_by_operator` without making any HTTP call. Existing positions, exits, ratchets unaffected.
- **Prod-mirror replay**: arena uses prod's actual fill prices (from truth tables) instead of computing them from bars. Tautologically clean by design; validates rig wiring.
- **Modeled exit**: arena computes exit price from bar data + fill model. Used when no prod-mirror truth available. Fidelity bounded by spread/slippage calibration.
- **Stratification**: per-data-completeness breakdown of OOS results. `full_decision_row` (4/28), `partial_news_only` (4/22-4/27), `synthesized_no_news` (pre-4/22).
- **Falsification suite**: 5-test stress battery (multi-seed, slippage-multiplier, decomposition, OOS, adversarial-fade) plus shuffle test for OOS runs.
- **SUSPECT bucket**: Sharpe > 2.0. Triggers mandatory falsification before any operational claim.
- **Bug-letter alphabet**: A-Z production bugs, AA-AE oracle PBT bugs, AF onwards post-sprint. Each bug has a finding doc.
- **D-code registry**: D85 fast-path, D124 consensus, D146 BAR-1 exit, D215 execution recorder, D217 D-code polling, D231 RECON_HARD_BLOCK, D245/D246/D247 SMART_EXIT escalation, D277 halt switch.
- **DIRECTION-AGREES vs MAGNITUDE-CONTESTED**: applies to catalyst gate. Direction (positive effect) agrees across both methods (prod-pnl + arena-driven). Magnitude differs ~28× due to harness-design quirks. Documented as candidate-config but not promoted.
- **30/0 discovery rate**: 30+ production bugs surfaced + fixed; 0 patches introduced regressions. Discipline metric.

---

## §13 — CLOSING NOTE

This prompt is intended to be the primary reference for many subsequent sessions. It captures the project state at the moment of the catalyst-only inconclusive verdict — the point at which the project is no longer chasing a single experiment and is instead deciding which research track to fund.

**The project is in research mode**, not execution mode. The halt switch is on indefinitely. The next sessions should each pick one Track + Subtrack from §6, follow the §9 template, and update §6's subtrack progress notes.

**The most important question this prompt asks**: which of Tracks 1-5 produces evidence first? The framework has been built to answer that question honestly. The answer, when it comes, will be earned.

The $150K → $1M plan was built on assumptions that have not survived testing. The infrastructure built to test those assumptions has earned its keep. **The project's value going forward is whatever the framework proves about ANY strategy on the bar corpus we have or the corpora we acquire** — not specifically the original strategy class.

If Track 1.A + Track 3 ultimately produce no edge in catalyst-only entries, the original strategy class is rejected. If Track 2 also produces no edge in any tested class, the corpus is rejected. If Track 5 reveals the underlying market phenomenon has decayed, the regime is rejected. Each rejection is informative; each negative result is the same shape as a positive one would have been.

**The framework's job is to make whatever answer comes the honest one.** It has done so consistently. Future sessions should preserve the discipline rules in §8 above all else.
