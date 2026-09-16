# 185 — Where Deep Research Would Pay Off: Gaps + Creative Ideas

**Author**: Claude Opus 4.8
**Date**: 2026-05-31 (Sunday)
**Mandate**: Pierce — "Where would your development process benefit from deep
research? Where are the gaps? Brainstorm creative ideas to cover them. Record it."
**Grounding**: Friday 5/29's full session (reviewed today), where the bot ran on
doc 178 (no restart → 179-184 never deployed).

---

## 0. The one observation that reframes everything

Friday's broker truth: **realized −$1,500**, equity **+$6,363**. The gap is
**unrealized ghost MTM**. The standout: **STG was a ghost worth +$6,999 at EOD** —
the bot "closed" it in the journal at **+$507**, the broker close 403'd, so it was
*forced to hold*, and it ran to +$6,999 (the EOD failsafe finally captured it).
Same story as APPS (+$1,625) the day before.

> **The bot's DELIBERATE trades lose money; its ACCIDENTAL forced-holds win.**
> Its exits cut winners early (doc 176's posture thesis); its bugs (403-stuck
> ghosts it *can't* exit) accidentally do the right thing — hold the runner.

This is not a curiosity. It is the single most important empirical signal we have:
**holding is the edge, and our exit machinery is destroying it.** Every gap below
is in service of one goal — *deliberately* doing what the ghosts do by accident,
while not bleeding on the names that actually fade.

---

## PART A — Development-PROCESS gaps (how we build)

### A1. No pre-deploy simulation gate for STRATEGY changes ⟶ HIGHEST process leverage
I shipped the full posture inversion (doc 178) "full send" and only learned its
live effect on Friday (−$1,500 realized + 2 ghosts). We have ingredients —
`bar_recordings/`, `scripts/simulate_day.py` (LLM-replay), the arena, polygon
warehouse — but **no fast, faithful harness that scores a proposed config/posture
against N recorded sessions BEFORE it goes live.**
- **Why it matters**: "full send" is only responsible if it's been replayed. A
  same-day backtest of doc 178 on 5/26-5/28 recordings would have shown the
  downgrade admits pumps that the faller/D170 then block anyway → 0 net change,
  predicting Friday.
- **Creative ideas**: (a) a **"config diff replayer"** — given a git diff of
  `settings.py`/gate code, replay the last 10 recorded sessions on BOTH old and new
  config and emit the P&L/funnel delta (an A/B on tape). (b) **Property-based
  posture tests**: assert invariants like "loosening a gate never *reduces* fills"
  on recorded data. (c) A **"shadow-deploy" mode**: the live bot runs the NEW config
  in shadow (logging would-be decisions) for a day before it's armed.
- **Research**: fidelity of intraday replay — how faithfully can we reproduce fills,
  VWAP, and LLM signals from recordings? What's the minimum recording set
  (bars + quotes + the agent outputs) to make a replay trustworthy?

### A2. The deploy gap: fixes sit committed-but-not-running
Friday ran on `b33b853` (doc 178). Docs 179-184 — including the D249 re-arm fix that
**failed live again Friday (2×)** and left a position naked — were on disk, never
deployed. Bug fixes that don't deploy are worth zero.
- **Creative ideas**: (a) **commit-aware self-restart** — the bot's D239 heartbeat
  already logs `commit=` vs disk; have the watchdog do a *safe* restart at the next
  flat-and-pre-market window when disk is ahead of the running commit. (b) A
  **one-command `deploy.ps1`** (elevated) that stop→verify→start→confirm-new-commit,
  removing the manual multi-step dance. (c) **Deploy receipts**: post to Discord
  "now running commit X (was Y); diff: …" so it's never ambiguous what's live.
- **Research**: blue-green / hot-reload patterns for a single-process stateful
  trading loop (how to restart without losing in-flight order state).

### A3. Bug discovery is reactive (post-mortem), not proactive
The slice crash, the D249 re-arm signature, the phantom paths — all found AFTER they
fired live. They share a profile: **error/recovery paths that normal tests don't
exercise** (the slice was *in* an error handler; the re-arm only fires on
close-after-cancel-failure; the phantom on a 403-ghost). And a test *masked* the
D249 bug (a permissive mock accepted the wrong signature).
- **Creative ideas**: (a) **a chaos/fault-injection suite** for the broker client —
  a `FlakyBroker` that injects 403/held_for_orders, partial fills, cancel-races,
  timeouts, and stale fills, run against the full close/exit state machine
  (the repo has a `failure_injector` — wire it into CI). (b) **contract tests** that
  call the REAL method signatures (no mocks) for the close/submit paths, so a
  signature drift fails CI (would have caught D249). (c) **mutation testing** focused
  on `bridge.py`/`alpaca_executor.py`/`exit_intelligence.py` — if a mutant survives,
  the close path is undertested.
- **Research**: which invariants, if continuously checked, would have caught the
  most live incidents? (Mine the SYSTEM_MAP bug history for a taxonomy.)

### A4. Producer/consumer contract drift
The Continuer_v2 near-miss (a model wanting 54 features, the bot supplies 12) and
the D249 signature mismatch are the same disease: **components evolve without a
checked contract between them.**
- **Creative idea**: a **"feature availability" linter** — for any model/scorer wired
  into a process, assert at load time that ≥X% of its features are actually produced
  by that process's candidate dict (would have flagged Continuer_v2 at 22% instantly).

---

## PART B — SYSTEM / STRATEGY gaps (the trading edge)

### B1. The phantom/ghost bug is STRUCTURAL — P&L is booked on intentions, not fills ⟶ fix the architecture, not the path
Friday proved doc 177's per-path fix is whack-a-mole: the D76 path is fixed, but the
INTRADAY exit paths still book the journal close, then ghost at the broker (CMND,
STG; D222: "an exit path closed positions without calling record_close()"). The
root: **internal P&L is decoupled from broker confirmation.**
- **The creative fix (the keystone re-architecture)**: an **event-sourced position
  ledger where P&L is a PROJECTION of confirmed broker fill events** (from the
  trade-updates WebSocket), never of internal close *intentions*. An exit path emits
  a *close intent*; P&L is booked ONLY when the matching broker fill arrives. No
  fill event → no P&L, full stop. This collapses the entire ghost/phantom bug class
  into "the ledger can't lie because it only knows fills." (Doc 177's MEMORY already
  says "book P&L ONLY on confirmed broker close" — this generalizes it from a
  discipline into an *architecture*.)
- **Research**: event-sourcing / CQRS for trading state; reconciling an internal
  fill-event stream against the broker's positions endpoint as the source of truth.

### B2. The 403/held_for_orders war (66× Friday) — the bot fights its own stops
Every protective stop reserves the qty (`held_for_orders`), so every close must
cancel-then-retry. More activity (posture inversion) = more collisions. Phase A
band-aids it (cancel-blocking-stops), but it's a structural mismatch.
- **Creative ideas**: (a) **software-only stops** for the active session (monitor in
  Phase 3, fire a market close) + a single un-reserved close path — no broker stop to
  fight; the broker stop only as an overnight/crash backstop (the APPS case shows the
  software trail already does most of the work). (b) **OCO/atomic close** — use the
  broker's native one-cancels-other so closing auto-cancels the stop. (c) A
  **single serialized "exit coordinator"** — one actor owns all exits for a symbol;
  no two paths race to close.
- **Research**: the correct Alpaca order architecture for a strategy that re-exits
  frequently; the latency of cancel→release→close on the paper vs live API.

### B3. The continuation edge (squeeze vs pump) — THE alpha gap
doc 184's intraday continuer is the scaffold, but the open question is *what
features actually separate a continuer from a pump intraday?* Friday's STG (ran to
+$6,999) vs the faded CGTL/UMAC is exactly the discrimination we need.
- **Creative feature ideas** (beyond gap/rvol/float): **order-flow imbalance** &
  **L2 book depth** (is there a real bid wall or is it hollow?); **the tape** (large
  block prints = institutional vs retail churn); **VWAP-reclaim dynamics** (a name
  that dips below VWAP and *reclaims* it continues; one that loses it fades —
  testable on recordings); **float-rotation rate** (volume/float velocity); **borrow
  fee / short-interest** (squeeze fuel — already fetched via D192); **news-sentiment
  velocity** (accelerating coverage vs a single stale headline); **the first-5-min
  range and its break**.
- **Creative model ideas**: an **online-learning continuer** that updates within the
  session; a **regime-conditional** continuer (different model in high-VIX); using the
  doc-182 REJECTED-names outcomes as a *natural experiment* (the gates randomize
  exposure for us); a **survival model** (time-to-fade) rather than a binary.
- **Research**: the microstructure literature on intraday momentum continuation in
  low-float small-caps specifically (most momentum research is large-cap daily).
  **This is the #1 candidate for the deep-research skill.**

### B4. The blind funnel — catalyst signal is hostage to a 25s LLM ⟶ decouple it
news_agent EMPTY 54-67% because it shares the 29s pipeline + Together AI latency.
The downgrade (doc 178) mitigates the *block*, but the catalyst SIGNAL is still
mostly absent.
- **Creative ideas**: (a) a **fast deterministic catalyst detector** — headline
  keyword match + SEC filing type (8-K/424B5) + price-action signature, sub-100ms, no
  LLM — that supplies `catalyst_type` even when the LLM times out. (b) **pre-market
  news pre-fetch + cache** so the LLM scores once at 9:20, not under 9:31 load. (c) a
  **small local extraction model** (the task is extraction, not reasoning — a 3B
  local model at 50ms beats a 25s remote one). (d) **catalyst-as-a-feature** for the
  continuer rather than a hard gate.
- **Research**: low-latency catalyst extraction; does a fast keyword+filing detector
  match the LLM's catalyst labels on recorded data?

### B5. No regime modulation of POSTURE (only a kill-switch)
The bot trades identically in a momentum tape and a chop tape; BOCPD only halts. Yet
`data/polygon_warehouse/derived/ising_daily.parquet` (the Ising regime detector) is
ALREADY computed — and wired to the *lottery*, not the momentum bot.
- **Creative idea**: **regime-conditional posture** — in a confirmed momentum/risk-on
  regime, loosen the gates + press size (capture continuers); in chop, tighten to
  capital-preservation. The intraday continuer's threshold itself could be
  regime-conditional. Wire the existing Ising/`mag_5d` signal into the momentum
  posture (it's free — already computed).
- **Research**: regime detection that's *actionable intraday* (not just daily); does
  conditioning the faller/continuer threshold on regime improve OOS P&L on recordings?

### B6. Execution / fills (limit orders miss on thin names)
CMND was 0/2 early (passive limit at a stale price). The order-fill shadow (doc 181
§3) is unbuilt.
- **Creative idea**: **momentum-adaptive limit pricing** — marketable+buffer when the
  name is accelerating (don't miss the runner), passive when fading (don't chase);
  size the aggression by the continuer's P(continue).
- **Research**: optimal execution for thin small-caps (Almgren-Chriss / Bouchaud
  impact — the lottery's `MetaScorer` already uses the Bouchaud √-impact law for
  *sizing*; reuse it for *execution* timing).

---

## PART C — The top 3 deep-research bets (ranked by $/insight)

1. **Intraday continuation microstructure (B3)** — *what actually separates a
   continuer from a pump in the first 30-60 min of a low-float gapper?* This is the
   core alpha. Deep-research the microstructure literature + design the feature set
   the doc-182/184 pipeline should be collecting. **Run the deep-research skill here.**
2. **Broker-fill-driven position architecture (B1)** — event-sourced ledger where
   P&L = projection of confirmed fills. Kills the entire ghost/phantom class
   structurally. Research event-sourcing/CQRS for trading + Alpaca fill-stream
   reconciliation patterns.
3. **Pre-deploy strategy replay harness (A1)** — so "full send" is replayed before it
   ships. Research intraday-replay fidelity; reuse `simulate_day.py` + recordings.

## The meta-lesson from Friday
The bot is **accidentally** discovering the right strategy (hold winners) via a bug
(403-stuck ghosts that run). The entire program is: **make the right thing happen on
purpose** — identify the continuer (B3), hold it deliberately (fix exits, doc 176),
and stop the machinery from lying about it (B1) or fighting itself (B2). The gaps
above are that program, in priority order.

## Appendix — Friday 5/29 facts (this doc's grounding)
- No restart (ran `b33b853`/doc 178). Realized −$1,500.60; equity +$6,363 (unrealized
  ghost MTM). 9 orders submitted / 3 filled / 2 ghosts (CMND +$494, STG +$6,999
  unrealized at EOD force-close). Session report +$673 = phantom (journal, not
  broker). 66× 403/held_for_orders. 0 slice errors (doc-177 slice fix held). D249
  re-arm failed 2× (fix in doc 180, not deployed). Phase A EOD force-close worked
  (both ghosts cleaned on attempt 2). **Restart still pending → 179-184 not live.**
