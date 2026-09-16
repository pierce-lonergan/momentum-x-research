# 177 — Today's Run, Four Bugs Fixed, and the Blind Funnel

**Author**: Claude Opus 4.8
**Date**: 2026-05-28 (Thursday, post-close — continuation of docs 175/176)
**Method**: Independent log/code re-verification of docs 175/176 + 3 parallel
forensic agents (phantom-P&L learning-corruption / selection funnel hard counts /
fresh-eyes bug sweep) + direct edits with regression test. Every claim below is
cited to a log line, JSON field, or file:line and was re-derived from the raw
artifacts, not inherited from the prior docs.
**Mandate**: Pierce — "check how today's run was. resolve any bugs and then
really dig… WHY aren't we making 5%. do a deep dive and make this thing great."

---

## 0. The one-paragraph answer

Today (5/28) the bot reported **+$1,175.94** but actually realized **−$269.56**
at the broker — the difference is a **phantom**: a 970-share APPS *ghost* it
didn't know it held, whose overnight appreciation got booked as realized P&L and
then **poisoned the BOCPD/Kelly learning corpus**. Underneath the accounting lie,
the strategy is worse than doc 176 implied, for a reason doc 176 missed: the
**news agent timed out (15s) in ~67% of evaluations**, so the selection gates
(D200 catalyst, D204 news-confidence) **blocked candidates on data *absence*, not
on real signal**. The bot was effectively **blind two-thirds of the time, vetoed
its best ideas (NCPL MFCS 0.525, AMSS 0.546) when it could see, and the only two
names it actually traded (UMAC, SPRC) were its 6th-best ideas.** I fixed the four
mechanical/accounting bugs tonight; the path to 5% is a *selection + posture*
problem and is laid out in §5 for your go/no-go.

---

## 1. What today's run actually was (verified, three-number reconciliation)

| Source | Number | What it is |
|---|---|---|
| `session_reports/…json` `daily_pnl` | **+$1,175.94** | phantom (PM accumulator) |
| `eod_2026-05-28.json` `broker_truth_recon.broker_total_pnl` | **−$269.56** | **broker truth** |
| `session_state.json` `daily_realized_pnl` | **0.0** | wiped at shutdown (see Bug C) |

**The arithmetic of the lie (exact):** real UMAC −$249.96 **+** phantom APPS
+$1,425.90 = +$1,175.94. The APPS row in `trade_results.jsonl` carries the
synthetic prior-day entry timestamp `2026-05-27T20:00:00` and `source:"basic"` —
the tell of a stale carry-over closed at mark-to-market while the **broker close
403-failed** (`force_closes_failed:1, ghost_positions:1`). Broker equity *did*
rise +$1,403 today, but that was the **ghost APPS appreciating**, not the
strategy working.

**Session funnel (hard counts, agent-verified against `momentum_2026-05-28.log`):**

| Stage | Count |
|---|---|
| Distinct tickers evaluated | 35 |
| Total BUY-verdict log lines | 93 |
| Distinct tickers with ≥1 BUY verdict | 6 (AMSS, APPS, NCPL, QTTB, SNGX, VTIX) |
| Orders submitted to broker | 2 (**UMAC, SPRC** — *not in the BUY-6 list*) |
| Broker fills | 1 |
| Broker-realized closes | 1 (UMAC −$269.56) |

The two names the bot actually *traded* were not among its six highest-conviction
BUY verdicts. Its best ideas were all blocked (next section). It traded its
leftovers.

---

## 2. The four bugs fixed tonight (mechanical / accounting — shipped)

All four are pure correctness fixes; none changes trading strategy. They make
Friday's Phase-A validation *cleaner*, not muddier.

### Bug A — `body[:120]` slice crash in the broker error handler  ⟶ FIXED
`src/data/alpaca_client.py:1266` did `f"…body={body[:120]}"` where `body` is a
**dict** from `resp.json()` (line 1232). Slicing a dict raises *inside the error
handler*, **before** `resp.raise_for_status()` (1268). Consequences, all observed
today (6 occurrences):
- Callers got a meaningless `slice(None, 120, None)` error instead of the real
  403, so the **Phase-A cancel-blocking-stops detection never fired** (it matches
  on `"40310000"`/`"insufficient qty"`, never on a slice repr).
- `CRITICAL RESCAN exit_ladder UMAC: residual stop SUBMIT FAILED — POSITION
  UNPROTECTED` (10:15:31): UMAC traded all day with no broker stop.
- APPS profit-taking tranches T1/T2/T3 never submitted.
**Fix:** `str(body)[:120]`. **Regression test:** `tests/unit/test_alpaca_client.py::
TestD177TradingPostErrorHandling` — asserts a 403 with a dict body raises
`httpx.HTTPStatusError` (not a slice error) and the breaker receives a string
context carrying `40310000`.

### Bug B — Phantom-P&L fake-positive at the D76 EOD close  ⟶ FIXED
`main.py:4289` called **raw** `client.close_position(pos.ticker)`. On a 403 the
`except` at 4321 *only logged* and **fell through** to `close_with_attribution`
(4335), which booked the APPS +$1,425.90 unrealized MTM as realized P&L into
`_daily_realized_pnl` + `trade_results.jsonl`. **D76 is the close site Phase A
missed** (doc 175 hardened D91/D242/D85, not this one — yet this is the path that
actually books the phantom).
**Fix:** route D76 through `attempt_close_with_status_check(
cancel_blocking_stops_first=True)` (same wrapper Phase A used) and **only book
P&L when the broker close truly succeeds**; on failure, leave the position
tracked for the D242 failsafe / next-session D91 (both Phase-A-hardened) — never
book a phantom. (`main.py` D76 loop.)

### Bug C — Learning corpus poisoned by ≥2 phantom rows  ⟶ FIXED (quarantined)
`trade_results.jsonl` had **two** phantom `source:"basic"` wins: APPS +$1,425.90
(5/28) and **LFS +$1,403.36 (5/27)** — the latter proven phantom by
contradiction (broker net 5/27 was −$759, incompatible with a real +$1,403 win).
These flowed unfiltered into the BOCPD regime prior and Kelly win-rate. Effect,
measured: the EOD `bocpd_refit` reported `mu_edge=−$128.60`; with the phantoms
removed the honest figure is **−$202.08** (`BOCPDPrior.from_corpus`). The
phantoms biased the loss-regime kill-switch **less-negative by ~$74/trade** — i.e.
the bot believed it was losing less than it is, **desensitizing the halt** — and
inflated the Kelly win-rate (one fake win in the `unknown`-catalyst bucket).
**Fix:** both rows tagged `infrastructure_contaminated:true` (respected by
`from_corpus`, bocpd.py:170) and added to `KNOWN_CONTAMINATED` in
`scripts/pretrain_bocpd_prior.py` (the operator refit filter). D262 was actively
recommending a refit tonight that would have baked the phantoms into the
*persistent* prior — now blocked.

### Bug D (verified, not a bug) — debate engine "0/0 attempts"
`settings.debate.max_debate_attempts = 0` is **intentional** ("D100: Debate engine
killed. 0% conversion over 7 debates"). The 34 "skipped (budget)" lines and
`debates_buy:0` are by design, not a defect. No action.

---

## 3. Bugs found but NOT yet fixed (need your call — see §5)

| ID | Severity | What | Why deferred |
|---|---|---|---|
| **H2** | HIGH | Phase-4 shutdown runs `state_mgr.reset()+save()`, wiping `eod_close_completed`/`phase0_completed` → persisted EOD state is always all-false. Defeats D95 crash recovery (a post-16:00 restart could **re-fire D76 / re-run Phase 0**). | Touches the shutdown/reset sequence; want your nod before changing recovery semantics. |
| **H3** | HIGH | **Tier-2 LLM timeout = 15s** (`settings.py:186`). news_agent is Tier-2 + ensemble×3 → **34 timeouts, news_agent EMPTY in ~67% of evals.** This is the *root cause* of the blind funnel (§4). Regression from the D87 35s setting. | One-line config (→25s) but changes *every* eval's behavior; you have deep history tuning this. |
| **M1** | MED | `$1.00` equity-recon tolerance vs $23–164 unrealized deltas → ~2,600 false WARN/ERROR lines/day; shadow `RECON_LETHAL` fired **711×** ("WOULD flat-all + halt" — **would have halted the account in live mode**). | Mis-specified tolerance; needs mark-to-market or a sane band. |
| **M2** | MED | Data WebSocket subscribes a fixed **19-symbol** pre-market list; only **1** dynamic add all day (UMAC). 59× "VWAP unavailable". Post-open candidates trade on **stale REST data**. | Real-time data gap; medium refactor. |
| — | LOW | SEC EDGAR 500s (24×), Finnhub empty-error swallow (10×), dirty-worktree-at-boot warning, the 10 pre-existing `orchestrator.py:312` MagicMock test failures. | Housekeeping. |

---

## 4. WHY not 5% — the reframed thesis (deeper than doc 176)

Doc 176 nailed the *posture* problem (defensive fortress hunting offensive prey).
Today's logs reveal a second, compounding failure doc 176 didn't isolate: **the
gates fire on missing data, not real signal.**

### 4a. The selection gates blocked the best ideas — on absence, not evidence

The #1 alpha killer was **D200-E4 CATALYST GATE** (`main.py:3392`): blocks any BUY
when `news_agent.catalyst_type ∈ {None, NONE, unknown}`. Distinct tickers it
killed today: **5** (AMSS, NCPL, QTTB, SNGX, VTIX). The high-MFCS casualties:

| Ticker | Peak MFCS | Verdict | Killed by | Ran |
|---|---|---|---|---|
| **NCPL** | **0.525** | BUY, Risk=PASS | D200 (catalyst=None) | **+38.5%** |
| **AMSS** | **0.546** | BUY | D200 (catalyst=None) | (mover) |
| QTTB | 0.391 | BUY | D204 news-conf 0.16<0.30 | (mover) |
| **VCIG** | 0.000 | NO_TRADE | D112 RVOL 0.3x<1.0 | **+73%** |

NCPL's own eval said *"premarket volume (28.4M) exceeds float (6.28M)"* — a
textbook squeeze — and the manipulation classifier read that as **distribution**
and the catalyst gate blocked it for having no headline. **That is the literal
definition of the sub-$50 low-float momentum archetype the bot exists to trade.**

### 4b. …but the gates were also *blind*: news_agent EMPTY 67% of the time

Here is the compounding mechanism doc 176 missed. **D200 and D204 both key off
`news_agent`.** The news agent runs on the **15s Tier-2 timeout**, ensembled ×3,
and at the 09:30–11:30 entry window Together AI queue latency pushed it past 15s
→ **34 timeouts → news_agent EMPTY in 104 of 156 data-quality evals (67%)**, with
`institutional` 312× / `deep_search` 295× empty too. So for most candidates the
catalyst gate didn't evaluate "is there a real catalyst?" — it evaluated "did the
news call time out?" and, on timeout, **defaulted to block.** The bot wasn't just
*mis-judging*; it was *not seeing*, then defaulting to the safe (reject) action.

This reframes the #1 lever: **fixing the timeout (H3) plus making the gates
DOWNGRADE-not-BLOCK on high MFCS could open the funnel without loosening a single
quality standard — because today the standard wasn't being applied, a timeout was.**

### 4c. The multiplicative collapse, updated with today's evidence

```
daily_return ≈ (signal→fill) × (% equity/fill) × (move-capture) × (fills/day)
```
| Factor | Today | For 5% | Root cause (this doc) |
|---|---|---|---|
| signal→fill | ~2/93 BUYs | ~4/day, RIGHT names | §4a/§4b blind+block gates |
| % equity/fill | 2.6% logged (cosmetic) | ~15% on ELITE | D96 dead-code; fixed-risk 2%÷stop (doc 176) |
| move-capture | **−2.2%** (UMAC) | ~40% | D163 trail / D122 override / D106 10:30 (doc 176) |
| fills/day | ~2 (the *wrong* 2) | ~4 | all of the above |

The product is ~0 (today was a *realized loss* masked by a ghost). You cannot tune
one factor; §5 sequences all of them.

---

## 5. The path to 5% — ranked, flag-gated, your go/no-go

The mechanical fixes (§2) ship for Friday. Everything below changes **live
competition behavior**, so it's your call. My strong recommendation is the
**staged** column: make Friday a clean test of *mechanics + data quality*, then
invert the posture deliberately once the ghost class is confirmed dead.

### Tier 1 — DATA + RECOVERY (bug-class; recommend shipping for Friday)
1. **H3: Tier-2 timeout 15s → 25s** (matches Tier-1; below the proven D87 35s).
   *Expected:* news_agent EMPTY 67% → <20%; D200/D204 start firing on real
   catalyst data. **Single highest-leverage selection change, lowest risk.**
2. **H2: don't wipe recovery flags at shutdown.** Persist `eod_close_completed`
   instead of `reset()`-ing it. Restores D95 crash-recovery integrity.

### Tier 2 — SELECTION POSTURE (the funnel-opener; flag-gated, A/B)
3. **D200 catalyst gate: DOWNGRADE-not-BLOCK when MFCS ≥ HIGH (~0.45).** Instead
   of vetoing a no-headline squeeze, take it at half size. (`main.py:3392`,
   behind `require_catalyst_downgrade` flag.) *Would have admitted NCPL/AMSS.*
4. **D204 news gate: same downgrade rule** + treat news_agent=EMPTY (timeout) as
   "abstain," not "bearish." (`main.py:3420`.)
5. **D112 RVOL instant-reject: re-evaluate, don't permanently kill.** VCIG was
   0.3x RVOL at 09:20 and ran +73% later; the scan must revisit names that come
   alive. (`orchestrator.py:742`.)

### Tier 3 — EXITS (doc 176's "biggest single lever"; flag-gated)
6. **D163 trail: activation 2%→6%, trail 50%→30% of gain, min-dist 2%→5%.**
7. **D122 single-strategy override: require ≥2 strategies AND conf≥0.85; exclude
   `gratitude`/`alpha_oracle`.** (alpha_oracle exited UMAC at −2.2% while it was
   set to run +26%.)
8. **D106 PROMOTIONAL_EARLY: VWAP-anchored exit instead of 10:30 hard cutoff;
   stop the automatic half-sizing on high-MFCS gappers.**

### Tier 4 — SIZING (amplifier; only after 2+3 prove the names are right)
9. **Reconnect `verdict.position_size_pct` OR raise fixed-risk 2%→4-5% on ELITE
   tier**; raise per-name cap; position limit 3→5 with a portfolio-risk envelope.

### Guardrails (non-negotiable, from doc 176)
- All Tier 2–4 changes gate on conviction tier (ELITE/HIGH only); the MEDIUM/LOW
  body keeps every defensive default.
- Ship behind env flags, A/B against current posture.
- Hard daily drawdown halt at −3%. L2 hedge watcher stays on.
- Fix M1's lethal-recon tolerance **before** any live un-shadowing, or it will
  flat-all-and-halt on a benign unrealized-P&L swing.

---

## 6. Friday hypothesis (what these fixes should produce)

With §2 shipped (mechanics) and, if approved, Tier-1 (data/recovery):
- **0 ghost positions; broker-realized P&L == journal P&L** (Phase A + Bug A + Bug B).
- **news_agent EMPTY < 20%** and at least some D200/D204 passes on real catalysts (H3).
- BOCPD prior reflects honest −$202/trade; kill-switch appropriately sensitive (Bug C).
If those hold, Tier 2–4 (posture inversion) can roll out one flag at a time with
clean attribution. If the funnel is still starved after H3, that *isolates* the
gate logic (Tier 2) as the binding constraint — a clean experiment.

---

## 7. Honest caveats
- **Bug B is in the live EOD close loop.** It compiles, mirrors the proven Phase-A
  pattern, and the existing pipeline-guard/crash-recovery suites still pass (the
  10 failures there pre-date this work — `orchestrator.py:312` MagicMock rot,
  confirmed via stash). But it has not run a live close yet; Friday is its first.
- **LFS-5/27 contamination** is inferred (broker-truth contradiction), not pulled
  from a broker order-history export. If you have the 5/27 broker fills, confirm;
  if LFS was somehow real, untag it. APPS-5/28 is certain.
- **5% sustainability** (per doc 176): reachable on days the tape provides 30%+
  gappers; the honest target is "5% when the tape gives it, preserve when it
  doesn't" — a regime-aware posture (BOCPD, now un-poisoned, is the right home for
  that gate).

## Appendix — files touched tonight
- `src/data/alpaca_client.py:1266` (Bug A) + `tests/unit/test_alpaca_client.py` (regression)
- `main.py` D76 EOD close loop (Bug B)
- `data/trade_results.jsonl` (2 rows tagged) + `scripts/pretrain_bocpd_prior.py` (Bug C)
- This doc: `docs/research-log/177_todays_run_bugs_fixed_and_the_blind_funnel.md`
