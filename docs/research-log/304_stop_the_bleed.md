# 304 — Stop the Bleed, and What the Kill Switch Was Missing

**Date:** 2026-09-22
**Status:** Live entries are **quarantined** before the 2026-09-23 04:30 launch, and the quarantine
is verified. Both queued volatility candidates are **NO-GO**. SEVP is **UNRESOLVED-BY-DEATH**; this
document is the report its own pre-registration required on 2026-09-01. **One** candidate is
selected: SS0006's short leg, as a **forward** test, with a reviewed pre-registration drafted in 304b.
**It is not frozen, Trial 34 was not consumed, and I recommend against registering it** under the
program's own rules. At the default prior it is UNDISCRIMINATING, and even a full pass would add
~2.3 bps/day, below the target and below simply holding SPY (§4).
**Method:** two workflows. The first was an adversarial audit of the quarantine (4 lenses, 13
verifiers) plus a triage of every candidate (4 tracks, 10 skeptics, a completeness critic). The
second drafted and adversarially reviewed the pre-registration. All read-only, except the quarantine
edits themselves.

---

## 0. The answer

| deliverable | result |
|---|---|
| **1. Quarantine** | In force and verified. The first version had **six defects**, all fixed (§1). Three of them predate tonight and were live for months: the operator kill switch never covered ~75% of long entries, never covered the lottery at all, and `FADER_SHORT_DRY_RUN=1` never took effect, so **three live short orders** went out after the fader was "retired". |
| **2. Volatility spec sheet** | `sevp_event_vol_carry`: **UNRESOLVED-BY-DEATH**. Economics never measured, and the frozen structure cannot be traded at this broker. `anomaly_vol_scale_monetisation`: **NO MEASURED ROUTE**, retired (§2). |
| **3. SS0006 synthetic short** | ETF proxy: **no**, as the equity-premium carry dominates. Listed puts: **no**, since shares dominate them 28× on cost. And the premise was wrong: the short leg is **borrowable** for ~81% of names. Doc 303's `STRUCTURALLY_UNAVAILABLE` is corrected (§3). |
| **4. Go / No-Go** | **NO-GO** on both volatility candidates. **SS0006 forward fader-detection is the one selected**, with its pre-registration drafted and reviewed (304b). **Registration recommendation: no.** At the program's default prior the design is UNDISCRIMINATING, and the ledger forbids registering it. It needs 24 months to certify ~95–199 bps net, and a full pass is worth ~2.3 bps/day. **No candidate in the program can be certified at the 33-trial bar and deliver 5 bps/day; holding the index does** (§4). |

Standing context the directive did not state: **buy-and-hold SPY returned +14.91%/yr over 2016-2026,
more than the +12.7%/yr target.** Every route below is weighed against simply holding the index.

---

## 1. Deliverable 1 — the quarantine

### 1a. What was switched off, and why the repo `.env` was the wrong place

`scripts/daily_paper_trade.ps1:129` runs `Copy-Item $SecretsFile -> .env -Force` at every launch, so the
repo `.env` is overwritten each morning by `~/momentum-x-secrets.env`. **A quarantine written only to
`.env` would have reverted at 04:30.** Doc 285 called this class the "dead-letter config", and here it
sits one level up. Every change went into the secrets file, and `.env` was synced from it the same way
the launcher does. Only the intended keys moved (55 → 58 keys), and every credential is byte-identical.

| process | layer 1 | layer 2 | layer 3 |
|---|---|---|---|
| main bot, OTO long/short | `data/HALT_NEW_ENTRIES` (checked live per submit) | `EXEC_HALT_NEW_ENTRIES=true` | `EXEC_MAX_POSITIONS=0` *(was 8)* |
| main bot, **T2 wide arm** | `data/HALT_NEW_ENTRIES` *(new)* | `EXEC_HALT_NEW_ENTRIES=true` *(new)* | `EXEC_MAX_POSITIONS=0` |
| lottery, 09:00 | `data/HALT_NEW_ENTRIES` *(new)* | `MOMENTUM_LOTTERY_HALT=1` *(now effective)* | — |
| fader short, 15:50 | `data/HALT_NEW_ENTRIES` *(new)* | `MOMENTUM_FADER_SHORT_HALT=1` | `FADER_SHORT_DRY_RUN=1` *(effective for the first time)* |

At the moment of quarantine the account was flat: **$188,927.83, 0 positions, 0 open orders**
(GET-verified). `EXEC_MAX_POSITIONS=0` also zeroes the emergent 40% gross the directive named
(8 × `EXEC_MAX_POSITION_PCT` 0.05).

### 1b. What the adversarial audit found in my first version

Four lenses tried to get an entry order to Alpaca tomorrow, or to prove the quarantine broke an exit.
Every finding below was reproduced from the code by an independent verifier.

1. **The quarantine stopped the bot from starting.** `preflight_check.py` put "Halt Switch" in its
   **BLOCKING** list, while the function's own docstring says *"Bot will still START, this is just a
   fail-loud warning"*. With `EXEC_HALT_NEW_ENTRIES=true` the 04:30 launch would have exited before
   `main.py`: no exits, no stop ratchets, no EOD flatten, and no post-close chain (config-truth
   recon, posture scoreboard, RV forward ledger). The watchdog then kills any `momentum` python
   process as a "hung bot". It killed the lottery at 09:02 on 2026-08-13, 09-14 and 09-15 this way.
   **Fix:** the check is advisory, as documented, and it now reports the halt file too.
2. **The kill switch never covered T2.** The T2 wide arm enters with a plain
   `submit_limit_order(side="buy")`, and `_d277_halt_response` lived only inside the OTO methods. At
   `MOMENTUM_T2_WIDE_PCT=0.75`, roughly **75% of long entries sat outside the operator halt.** Doc 285
   flagged this as gap #8, and doc 286's "gap fixes landed" never fixed it. **Fix:** gated at the entry,
   not inside `submit_limit_order`, which also carries exits and buy-to-cover.
3. **The guard that should have caught (2) pinned the wrong property.**
   `test_submit_oto_order_is_the_only_entry_chokepoint` grepped `src/` for a literal buy-side payload
   *dict*. A keyword call passes it, and `scripts/`, where the lottery lives, was never scanned. **Fix:**
   an AST guard requires `_d277_halt_response` in the function around every `side="buy"` submit in
   `src/` and `main.py`, plus a behavioural test of the real executor under a halt with a control case.
   Mutation-checked: removing the gate fails both.
4. **The lottery ignored the operator halt entirely.** It buys at 09:30 through its own raw
   `/v2/orders` POST and ran live every weekday through 2026-09-22 (`HALT=False DRY_RUN=False` in
   every log). **Fix:** the lottery and fader runners read `data/HALT_NEW_ENTRIES` at import, and a
   regression test pins it.
5. **Every commented env flag was dead in four PowerShell launchers.** Their parsers kept
   `  # comment` inside the value, so `KEY=1   # note` arrived as `"1   # note"` and read as False. The
   secrets file puts inline comments on most of its trading keys. **Consequence: `FADER_SHORT_DRY_RUN=1`
   never took effect after doc 282.** All 53 fader sessions since 2026-07-04 logged `DRY_RUN=False`,
   and **three live `sell_short` orders were sent: FBRX on 2026-07-09, HUIZ on 08-07 and SPAI on 08-14.**
   All three were rejected 422 on borrow. Borrow scarcity, not the retirement, is what kept the fader
   flat. **Fix:** all four parsers strip an unquoted inline comment the way python-dotenv does. A dry
   run against the secrets file changed 27 values, every one a comment, and **0 credential values**.
6. **The test suite read live production state and messaged the live ops channel.** `Settings()`
   reads the repo `.env` (the secrets copy). The D277 helper posts "⛔ HALT BLOCKED Entry" to the real
   `OPS_ALERT_WEBHOOK_URL` whenever it blocks, and `test_d277_halt_new_entries.py` exercises exactly
   that with real settings. **So every run of those tests sends to the ops Discord.** The code path is
   verified; delivery was not checked. Three of my own pre-fix runs tonight reached that file, and the
   alert is rate-limited only per ticker per minute within a process. **Fix:** a conftest fixture pins pre-quarantine values, hides
   the live halt file, and blanks every webhook URL for every test.

### 1c. Verified, with no order placed

* Each D277 layer halts **on its own**: file alone, then settings alone. **The control case (both off)
  does not halt**, so the probe can tell the two states apart.
* The lottery launcher's **actual** env-loading block, run against the secrets file, yields
  `MOMENTUM_LOTTERY_HALT='1'`, `FADER_SHORT_DRY_RUN='1'` and `EXEC_MAX_POSITIONS='0'`.
* The preflight halt check reports both sources and sits in the advisory list.
* An AST scan finds exactly **one** non-OTO buy submission in `src/` and `main.py`: the T2 entry, now
  gated. No other scheduled process calls an order method.
* The Operator cannot lift the halt. `clear_halt()` has no callers, the Operator is disabled, and
  governance forbids re-arming (`legal_t1_transition("MOMENTUM_HALT_NEW_ENTRIES", "1", "0") is False`).
* **Tests:** the private repo's full suite is 81 failed / 4,453 passed. **All 81 fail identically with
  tonight's code stashed**, so none come from this change; that private suite was never greened. One
  further test (`test_doc298_sevp_prereg_conformity`) now outlives its timeout. It builds a HAR panel
  over the full minute warehouse, which grew ~4× in yesterday's backfill, and pytest-timeout's thread
  method then kills the whole run.

Committed locally to the private repo's `develop` as `b0c4d00` and `9428e50` (below), not pushed,
per that repo's practice.
The record is `momentum-x/docs/QUARANTINE.md`, with an explicit five-step un-quarantine procedure.
Deleting the halt file alone resumes nothing.

### 1d. The critic pass, a second commit, and what the quarantine pauses

The completeness critic found **no path that opens exposure tomorrow**. It did point out that every
audit verdict above was computed **before** `b0c4d00`. That commit flipped the regime: the bot now
*boots* under the quarantine, so the in-process layers carry a live session for the first time. Three
gaps became reachable and were fixed in `9428e50`:

* **The only halt state visible in Discord would have been wrong.** The boot banner read just the env
  var and would have posted "HALT: OFF" at 04:30. It now reads the three sources the enforcement
  reads; its own lines, executed against the live config, print **"HALT: ON (settings+file)"**.
* **A halted order became a phantom order.** The executor treated `{"status": "halted_by_operator",
  "id": "halted-…"}` as live: D308 and Phase-0 rows for an order that never existed, bridge polling,
  and a DELETE of the sentinel id. It was unreachable at `max_positions=0`, but it poisoned any observe
  mode. The executor now checks D277 once, before any row or submission. Mutation-checked: without the
  guard, `execute()` returns `OrderResult(order_id='halted-BOOM-…')`.
* Stale text: "the single chokepoint" in `alpaca_client.py`, and a preflight lift instruction naming
  a variable that is not the halt source.

An independent three-lens re-verification of HEAD then ran before the launch (§1e).

**What pauses, corrected.** I first wrote that collectors pause because there are no fills. That was
wrong. **`EXEC_MAX_POSITIONS=0` stops evaluation itself**: all three `evaluate_candidates` calls sit
behind `can_enter_new_position()`. So no features file, no VLL verdict trace, and no prod rows for the
shadow grader get written, and the doc-284 rocket-gate ledger logs "no features file". That ledger has
100 rows and evaluates at n ≥ 30 with no calendar death date, so it pauses rather than dies. The RV
forward ledger is warehouse-based and runs in the post-close chain, which now runs.

**The trade-off, Pierce's call.** (A) As configured, the directive's "disable the 40%": the bot runs
and exits work, but evaluation-fed research stops. (B) Observe mode, with `EXEC_MAX_POSITIONS` > 0 and
both D277 halts kept: the bot evaluates and collectors run while every entry is refused. Tonight's
executor guard is what makes (B) clean; before it, (B) would have written phantom rows.

**An un-quarantine prerequisite the critic found.** The doc-282 re-entry ban lives only in
`submit_oto_order`, so the T2 arm skips it. That arm made **58 of 86** long submits from July to
September. Replaying the ban shows it would have refused one T2 entry (ATAI, 2026-07-17). **It has
never refused a live entry**, so doc 281's "+$9,948" has no live realization.

### 1e. Independent re-verification of HEAD before the launch

Three fresh verifiers ran against `9428e50`, with the earlier verdicts withheld. All three returned
**SAFE_WITH_GAPS with no blocker**.

* **Entries.** An AST scan of 601 files and every scheduled task's import closure found three order-capable
  processes: the main bot, the lottery and the fader. Every entry site has **at least two independent
  refusals**, and main-bot entries have four or more. The verifier reconstructed each process's effective
  config by running the launchers' own env blocks against the secrets file and main.py's own dotenv
  call. Nothing assigns or mutates `max_positions` or `halt_new_entries` at runtime, and the halt file's
  only deleter has no caller.
* **Boot.** The 04:30 launch reaches `python -m main paper`. Preflight's blocking list is Alpaca, disk
  and heartbeat, and the halt check is advisory and reports both sources. Every name in the new banner
  is bound, and the banner sits inside a `try`. Exits, ratchets, the hedge watcher, EOD flatten and the
  post-close chain are reachable and ungated. `max_positions=0` produces a few hundred "Max positions
  reached (0)" WARNING lines a day, all of them local-only.
* **Parser blast radius.** All four launchers now agree with python-dotenv on **58 of 58** keys (before:
  27 disagreements). No credential changed. Among the programs those launchers run, only three reads of
  the changed keys exist, all of them halt or dry-run flags, so the change can only make those processes
  **more** restrictive.

Gaps the verifiers left open, none of which opens exposure tomorrow:

* **Coverage gaps in the tests.** No test pins the preflight fix; the existing test only checks that the
  string "Halt Switch" appears. The banner and the four parser fixes are untested too. The new AST
  guard misses a `submit_bracket_order` caller (no `side` kwarg), a variable `side`, short entries, and
  `scripts/`.
* **Exits can open a short if they fire without a position**, since the account has `shorting_enabled`.
  Tomorrow that is prevented by state: nothing holds a position. No gate stops it.
* **`MOMENTUM_T2_ENABLED=1` lives only in Windows User scope**, not in the secrets file: the doc-285
  invisible config surface.
* **The machine rebooted unexpectedly** at 00:46 on 2026-09-22, and that morning's "04:30" launch fired
  at 07:45. That only matters when positions are carried overnight.
* **A `Settings()` load failure fails open.** Both the preflight check and the D277 helper would then
  treat the settings layer as off, and only the file would halt.
* **The suite was writing fixture verdicts into the live verdict trace.** The 2026-09-22 trace held
  `TEST` (52 rows), `BOOM` (25, partly my own runs tonight), `INV`, `AAPL` and `MAAS` beside the real
  session. Fixed in `5fd5132`: the conftest now redirects the writer, and a test run leaves the live
  trace unchanged (286 → 286 rows). The rows already written are documented, not deleted.

### 1f. Collectors that went dark, found along the way

The same "starved collector" failure that killed SEVP turned up three more times:

* **The doc-255 LLM red-flag scorer**, the forward test doc 260 prescribed, has scored nothing since
  **2026-07-06**. Its LLM calls failed with HTTP 400 through July (305) and **HTTP 402, "payment
  required", since 2026-08-04** (684). The main bot's own LLM path shows only sporadic 402s, so the
  failure looks specific to that scorer's endpoint. The cause is undetermined.
* **The Kalshi zero-capital shadow** has written **0 forecasts since 2026-08-10**: 32 runs, with parse
  failures and zero tokens. Its gate printed FAILED at n=237 resolved.
* **The doc-284 rocket-gate forward ledger has passed its evaluation point unnoticed**, with 36 gated
  measurable sessions against a frozen n ≥ 30. Nobody ran the frozen acceptance test. It is not run here
  either, because it measures an exit posture on the quarantined gapper book; running it is Pierce's call.
* And the structural cause: **no death-date sweeper exists.** The registry stores `death_date` and
  nothing reads it.

---

## 2. Deliverable 2 — the volatility spec sheet

| | `sevp_event_vol_carry` | `anomaly_vol_scale_monetisation` |
|---|---|---|
| **what it is** | Trial **T00029**. Short an ATM straddle on single-name earnings, T−1 close → T+1 close | The doc-290 anomaly: volatility *scale* is predictable (ρ ≈ 0.30) while direction is not |
| **instrument** | Frozen as a **naked** short straddle. **Alpaca offers no uncovered writing at any level.** The tradeable analogue is an iron fly, whose single-name wing tax is 0.49–0.87 of credit. The ledger's "defined-risk" label was wrong | **None named in the queue entry.** The anomaly ranks 60-minute *upside run-up* on ~$3 gappers, ~73% a known proxy stack (doc 291 D2). Options exist on 202 of 496 tickers (35.5% of events) but cost a median ~81% of premium round trip below $5 (§2b) |
| **horizon** | One close-to-close interval around the announcement. 26 of 205 covered events span 2–11 sessions because of stale legs | 60 minutes (the anomaly). Daily for any index route |
| **gross margin** | **Unmeasured.** The "~70% / ~18% ceiling" is an unrecorded doc-293 assumption chain against a *net* requirement; M4 calls it "UNMEAS" | Best index route, vol-managed SPY: **+0.14 bps/day** vs a vol-matched SPY/BIL blend (IR +0.08 full, **−0.04 on 2018+**). Short index vol: CLOSED (T00033) |
| **cost** | 5–10% of premium, frozen. Measured on only 15 of 205 covered events' names, on one non-event day, and two tables disagree ~2× | 0.006 bps/day for the index overlay; options 0.43% (SPY) to 31–68% (VIX ETPs) of premium |
| **capacity** | Unmeasured; historical option NBBO returns 404 | Unlimited for SPY. Irrelevant, since there is no edge |
| **expected Sharpe** | None exists. The planner's "ADMISSIBLE, bar 2.29" ran on a declared prior and an **n_obs of 1,200 that does not exist** (603 known, 205 covered) | Vol-managed SPY ≈ 0.66–0.79. That is below buy-and-hold on return, and certifying the overlay's increment would take ~1,200–4,200 years |
| **≥ 3× the doc-303 cost table?** | Not computable: no gross exists | No: 0.14 bps/day vs a 20 bps/ticket floor |
| **verdict** | **UNRESOLVED-BY-DEATH (2026-09-01)**, §2a | **NO MEASURED ROUTE.** Retired from the queue |

### 2a. SEVP: the report its pre-registration required three weeks ago

The frozen text (`momentum-x/scripts/_doc294_PREREG.md` L35-36; the private copy matches the frozen hash
`ae62ea59…`) reads:

> *"PRE-UNBLINDING COVERAGE GATE: N ≥ 300 events with full two-leg price coverage. Below N: status
> PENDING-COLLECTION, gates stay blind. DEATH DATE: 2026-09-01 → report UNRESOLVED to Pierce."*

Coverage on 2026-09-01 was **205/300**, and it still is. The IV collector feeding it last ran on
**2026-07-13**. **No scheduled task ever ran it.** The forward ledger was never written, and no gate
output exists, so **blindness is intact**. Nobody made the report. Meanwhile the ledger, the registry,
the design queue and docs 299/302 all carried SEVP as a live BUILD-NEXT.

**Pierce, this is that report.** SEVP is UNRESOLVED. The disposition is yours, and the triage found no
option that yields a tradeable result:

* **Close it** as `INSTRUMENT_LIMITED`. The keystone it lacked was a registered collector, and the frozen
  structure is untradeable here anyway.
* **Re-freeze it as a new pre-registration.** That means a new hash, a new death date, a defined-risk
  structure, and a runner that no longer conditions G2 on realized outcomes. That runner defect is still
  present: the "forecast" is same-day realized RV and the "implied move" is the realized straddle
  return. It would consume a new trial. Doc 295 disclaims deadline-sliding, so quietly resuming
  collection under the old freeze is not an option.

The design queue's SEVP row now carries n_obs **205** instead of 1,200 and a "do not build" note. The
planner still labels it "ADMISSIBLE" at a bar of **5.53**; that verdict rests on a declared prior, not
on anything measured.

A related finding for the record: **the public copy of `_doc294_PREREG.md` no longer matches its frozen
hash.** The public-release sanitisation replaced "Pierce" with "The operator" and changed line endings.
Hash checks must use the private copy.

### 2b. Vol-scale: why "no measured route" rather than "no route"

**First, a correction a skeptic forced.** The triage's reason, "the names have no options", came from a
4-ticker probe (`COST_TABLE.json:18`: LGHL, MUU, JLHL, EHGO). A full read-only check of all 496 doc-290
tickers found **202 optionable**, with **311 of 875 events (35.5%)** having contracts within 45 days.
Doc 290:83 and doc 303:292 ("unmonetisable in this account") inherited that unsupported premise. The
verdict survives on different grounds. **Cost:** option round trips on sub-$5 names run a median ~81% of
premium, and the doc-290 events have a median price of $3.02. **Horizon:** the anomaly predicts a
60-minute *upside run-up* (`peak_run60`), which is not the absolute move an option pays over its
holding period. Long single-name options on the optionable subset are **unmeasured, not refuted**.


A skeptic tightened the triage's "NO ROUTE" to "**no measured** route". One index branch, the
delta-hedged SPY/IWM variance risk premium, is *unmeasured, not refuted* (T00033 §4). It needs SPY
ATM IV history back to 2016 and would be a data purchase, which is Pierce's decision. Under T00033
§3(C) it cannot clear the multiplicity bar on the rolling ~2-year option plan in any case. The only
line in this family that clears +5 bps/day is **holding the index itself**, which is a risk and
allocation decision rather than a research result.

---

## 3. Step 3 — SS0006's short leg without borrow, and the premise that turned out to be wrong

### 3a. A borrow-free ETF short: no

Structural transmission was measured at $0, **blind to the LLM score**: 7,412 covered events against
IWM and SIC-mapped sector ETFs, over a 460-cell sensitivity grid. Under the **most favourable**
assumption (the short-leg excess loads on the systematic factor in proportion to R²), the signal that
reaches IWM is **−26.1 bps per 10 sessions**. IWM's unconditional drift, which a short pays, is
**+53.8 bps** over 2016-2026 and +76.1 bps over the event period. The short's expected gross is
**−27.7 to −50.0 bps/ticket**. No cell of the grid reaches +20 bps gross; the best is +16.6 bps, on a
cherry-picked subsample and price-only returns. Provenance, arithmetic and adjudication skeptics all
upheld it.

### 3b. Listed puts: no, but not for the reason doc 283 gave

Access is fine. **93%** of sampled events had listed puts, and **69%** saw a near-ATM put trade that
day. What kills it is cost. The measured closing-NBBO round trip on a 2–3 week ATM put has a median of
**50% of premium = 264 bps of the stock price**, against a gross capture of ~183 bps
(½ × 3.5% drift). The median name nets **−81 bps**, and that is the central case, not the best case.
Bear put spreads are worse. **Shares are 28× cheaper**: a median quoted spread of ~10.5 bps on the same
names.

### 3c. The correction: the short leg was never behind a borrow wall

Doc 303 §4b called SS0006's short leg `STRUCTURALLY_UNAVAILABLE`, citing the doc-284 tombstone and doc
281's 0-for-94. **That was wrong, and it was my error.**

* The tombstone was measured on the **gapper** BUY ledger (2,167 tickets, 186 tickers). Doc 284:18 and
  doc 297:274-275 scope it to that universe.
* Only **34 of 1,447** stage-B tickers overlap its borrow data.
* **80.7%** of eligible stage-B names are easy-to-borrow at Alpaca (87.6% event-weighted). These are
  paper-account flags, indicative only, and live permissions are unverified.
* Doc 260's "borrow wall" was **asserted, never measured**. The stage-B scripts contain no borrow
  check, and doc 259:43 states it as a design assumption.

This is the summary-without-adjudication error doc 303 §6 names, committed inside doc 303. Doc 303 §4b
now carries a correction marker, and the ledger row is amended. **The directive's instruction to
"confirm STRUCTURALLY_UNAVAILABLE and do not revisit" was not followed, because the evidence says
otherwise.**

### 3d. What still stands against SS0006

* **Underpowered**: n ≈ 55–93 per tranche, and the long-short interval spans zero.
* **It failed its own out-of-sample gate** against a cheap price-reaction baseline.
* **Its anchor has look-ahead.** The event anchor is a volume-spike argmax that can sit after the
  filing. On a knowable first-spike anchor the universe median f10 moves from **−1.74% to −0.40%**
  (medians; the sign flip appears only in the unwinsorized mean). Doc 260 itself warned that "a leak
  can only inflate".
* **The historical short-leg magnitude (−3.44% / −3.5% median) is therefore an upper bound.**

---

## 4. Deliverable 3 — Go / No-Go

| candidate | verdict | reason |
|---|---|---|
| `sevp_event_vol_carry` | **NO-GO** | UNRESOLVED by its own rule. Economics unmeasured, a structure the broker cannot trade, and a runner that still conditions on outcomes. Pierce disposes (§2a) |
| `anomaly_vol_scale_monetisation` | **NO-GO** | No measured route; retired |
| overnight ETF excess-of-cash (queue) | **NO-GO** | 1.03 bps/day, far under the 20 bps/ticket floor; M4 keeps the family closed |
| LETF close-window (queue) | **NO-GO** | VOID in doc 303. Its two queue rows had never been retired, **so the planner was still ranking the voided family #1 and #2**. Retired tonight |
| **SS0006 forward fader-detection** | **SELECTED; pre-registration drafted (304b); registration not recommended** | See below |

**Why this one.** It is the only family in the program with **all three** of the following:

1. **A margin far above the 20 bps/ticket floor, if it is real.** The historical upper bound is
   −3.44% median. *If* the whole 1.34 pp anchor shift applied uniformly to the bottom tranche (an
   assumption, not a measurement), it would still sit near −2.1%, about ten times the floor.
2. **An executable instrument.** Direct short of easy-to-borrow shares at ~10.5 bps spread, with the
   borrow flag observable at entry.
3. **A mechanism independent of the bot**: no MFCS and no `risk_aversion_lambda`.

Its evidence is weak, and that is precisely what a clean forward test is for. **Doc 260 already named
this test** as "the only forward-validatable morsel … immune to look-ahead & lucky-sample". A
**forward** design also removes the two defects no backtest can: **LLM memorisation** (only filings
after the model's training cutoff) and **point-in-time borrow**.

**What the pre-registration found about itself, and why I recommend not registering it.** The draft
(304b; four adversarial reviews, six blocking findings resolved) is careful. It uses the SEC-acceptance
anchor, only filings accepted **after** freeze (so no memorisation), entry at 15:30 ET rather than the
expensive open, an IWM hedge, and an easy-to-borrow check at entry. The reviews also added a
**Rule 201** exclusion: after a ≥10% drop, a short sale may not execute at or below the bid, and the
fader tranche is exactly where that bites. Its own numbers are what decide it:

* **Power.** Over a 24-month forward window it **cannot certify a net edge below ~95 bps/ticket**
  (optimistic volatility basis) **or ~199 bps** (pessimistic). A doc-260-sized effect passes the primary
  gate about 49% of the time on the pessimistic basis. **In 12 months it cannot certify doc 260's point
  estimate at all.**
* **Admissibility.** Under the program's **default prior (sd 0.5) the planner rates it
  UNDISCRIMINATING**, and the ledger's rule says such a design must not be registered. It becomes
  ADMISSIBLE only at prior sd ≥ 0.678. The draft discloses that it chose sd 1.0 *after* the default
  failed, which would make it the widest prior in the queue.
* **Payoff.** At its own 80%-power effect, a full pass through the governing identity is worth
  **~2.3 bps/day**. That is below the 5 bps/day target and below buy-and-hold SPY at 6.13 bps/day.
* **Cost** is small: $0 data and at most ~$46/yr of LLM calls. Its §12 "case against" is worth reading in
  full.

**So the answer to "select exactly one" is this one, and the answer to "test it against the 33-trial
bar" is: not as a promotion trial, unless Pierce explicitly ratifies the wider prior.** Freezing,
registering T00034 (which raises the bar for every other design), the prior, the LLM spend, and
scheduling the collector with an anti-starvation monitor are all Pierce's decisions (304b §11).

**The program-level finding under all of this:** no candidate can be certified at the 33-trial
multiplicity bar within a year *and* contribute 5 bps/day. The binding constraint is statistical power
under multiplicity, not ideas and not cost. The only line in reach that clears the target is holding
the index, which is an allocation decision rather than a research result.

---

## 5. Corrections applied tonight

| where | was | now |
|---|---|---|
| doc 303 §4b | SS0006 short leg `STRUCTURALLY_UNAVAILABLE` (borrow tombstone) | correction marker; UNDERPOWERED; borrowable |
| ledger, single-name VRP | "−6.5%, CI [−9.9, −3.1]" | the i.i.d. interval. K1's month-clustered interval is **[−19.9, +6.8], n_eff ~77**; the closure stands on cost, not sign. Doc 303 quoted the wrong interval too |
| ledger, SEVP | "BUILD-NEXT, ~70% / ~18%, ~1,200 events, defined-risk" | UNRESOLVED-BY-DEATH, T00029, naked straddle, 205/300 |
| ledger | no vol-scale row | NO MEASURED ROUTE (filtered) |
| design queue | LETF ×2 ranked #1/#2; vol-scale ADMISSIBLE with no instrument; SEVP n_obs 1,200 | three designs retired with reasons; SEVP n_obs 205 |
| `QUARANTINE.md` v1 | "the fader was already `FADER_SHORT_DRY_RUN=1`"; "restoring the secrets file alone would not resume trading"; collectors pause "for lack of fills" | all three false, and corrected (§1b items 5 and 2, §1d) |
| ledger / doc 304 v1 / queue | vol-scale closed partly because "the names have no options" | false (4-ticker probe; 202/496 are optionable). The closure stands on cost and horizon (§2b) |
| trial registry, T00029 | `REPORTED`, "ARMED, blind, 205/300" | hash-chained note recording UNRESOLVED-BY-DEATH and this report; state left for Pierce (both copies identical, chain verified, count still 33) |

## 6. Method rules this adds

1. **A kill switch is a claim about every path, so test it against every path.** D277 was specified as
   a chokepoint, tested with a grep for one syntactic form, and bypassed by another form and by
   another process. Enumerate submissions with the AST and across processes, and assert the gate is
   present at each.
2. **A config value is only what the reader parses.** The same secrets file meant `1` to python-dotenv
   and `"1   # note"` to four PowerShell launchers. Before trusting a flag, run the *consumer's* parser
   on the *real* file. My own first verification used the wrong parser and reported a layer as
   working when only the master file was doing the halting.
3. **A retirement is not done until the planner stops recommending it.** Doc 303 voided LETF in the
   ledger and left it at #1 in the design queue. After any closure, rerun the planner.
4. **A death date is a scheduled event, not a note.** SEVP's lapsed silently because nothing both
   depended on its collector and could notice the collector had never run. Every forward collector
   needs a freshness check that pages when it goes dark.
5. **Tests must not touch production or its channels.** A suite that reads the live config drifts
   whenever production changes, and one that reaches a real webhook pages the operator.

## 7. Decisions that are Pierce's

1. **SEVP (T00029) disposition**: close as `INSTRUMENT_LIMITED`, or re-freeze as a new trial (§2a).
   Also the still-unrecorded ruling on whether the doc-298 runner edits were a re-tune.
2. **SS0006 forward**: whether to ratify a prior sd ≥ 0.678 (without it the design is UNDISCRIMINATING and
   unregistrable); then freeze 304b, register T00034, schedule the collector with an anti-starvation
   monitor, and approve ~$46/yr of LLM spend. My recommendation is not to register it (§4).
2b. **The dark collectors (§1f)**: the doc-255 red-flag scorer's HTTP 402 (billing), the Kalshi shadow
   writing nothing since 08-10, and whether to run the rocket-gate ledger's frozen acceptance now that
   it has passed n ≥ 30.
3. **The vol-door closure** (doc 296) is still "awaiting Pierce" in the ledger.
4. **Index allocation**: the only line in the volatility family that clears +5 bps/day is holding the
   index. The vol-targeted overlay is an optional drawdown shaper that costs ~2.3 pp/yr.
5. **Live-account permissions** (shorting, options level) were verified on paper only.
6. **Quarantine mode (A) or (B)** (§1d): whether research collection should keep running while entries
   stay refused.
7. **`TARGET.md` §2 is internally inconsistent.** Its header sets the standing rung at 5 bps/day, but
   its identity still reads `required NET per ticket = 0.001 / (deployment × turns)`. That 0.001 is the
   superseded 10 bps/day, so the "25–183 bps/ticket" requirement it quotes is **twice** the standing
   rung. The file says "amend only by Pierce's written direction", so this document flags it rather
   than editing it. Related: the directive's 20 bps/ticket floor is not normalised for holding period.
   A 10-session ticket at 20 bps is 2 bps/day, while one event-length ticket is not comparable to
   either. No verdict here depends on it, since every rejected route nets negative.

## 8. Open

* The private repo's 81 pre-existing test failures, and the warehouse-bound SEVP conformity test that
  outlives its timeout.
* The watchdog kills any `momentum` python process when the main bot is down; the lottery died this way
  three times. This is moot while the bot runs.
* Main-bot boot self-test "HALT: ON/OFF" reads only the env var, so it would announce OFF while the file
  and settings layers are halting.
* Eight git worktrees hold pre-doc-304 runners with no halt file. The risk is manual runs only; no task
  points at them.
