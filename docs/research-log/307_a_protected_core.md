# 307 — A Protected Core: Observe Mode Proven, the Exits Disarmed, and a Clean Book

**Date:** 2026-09-23 → 2026-09-27
**Status:**
- **Mode B is proven on two real sessions.** On 09-25, 13 buy verdicts reached the executor, every one was refused, and the broker shows **0 orders and 0 fills**.
- **The core is built and protected.** A three-round adversarial build hardened 24 bot code paths so none can sell, stop, adopt, cancel or miscount it, and added a paper-only rebalancer with its own kill switch.
- **The core is not bought yet.** The initial 80/20 allocation waits for the next session (Monday 2026-09-28, 10:00–15:30 ET) with a human watching (§2d).
- **The four equity jumps are explained.** One of them is a **$40,003.59 phantom** still inside today's $188,927.83. The overlay candidate is filed as a design only. **Trial 34 is not registered; the registry stays at 33.**

**Method:** three workflows (22 agents): two independent position-safety auditors plus a merger; one builder per workstream; and a fresh adversarial skeptic on every result in every round. Fix rounds continued until the skeptics' findings were LOW. Every agent was barred from credential values and from sending orders.

---

## 0. The answer

| deliverable | result |
|---|---|
| **1. Mode B verification** | **09-25: `verify_observe_mode.py` exit 0, 0 FAIL, 0 WARN.** 199 evaluations, **13 `BLOCKED_OPERATOR_HALT_EXEC`**, 0 SUBMITTED, 0 broker orders, 0 fills, D95 LLM preflight PASSED on Llama. 09-23: exit 0 post-open, with one WARN (no verdict had reached the executor by 09:40). **09-24 had no session: the PC was off** (no task logged anything) |
| **2. Core rebalancer** | `scripts/core_rebalancer.py` + `src/core_benchmark.py` + 24 audited exemptions across the bot. Paper-only, own kill switch (`MOMENTUM_CORE_HALT` in env or `.env`, or `data/CORE_HALT`), OS run lock, intent-before-POST ledger, broker reconciliation, 10:00–15:30 ET order window. The live `--check` on 09-27 plans **BUY SPY 166 @ 772.04 + BIL 350 @ 91.62** ($160,226; est. 0.26 / 0.55 bps vs mid). **Not executed yet** (market closed) |
| **3. Equity jumps** | No reset and no mis-booked deposit. 02-26 is an **unapplied CRCA 1-for-10 reverse split: $40,003.59 of phantom gain still inside equity** (true equity $148,924.24; true P&L since funding +$48,924.24, not +$88,927.83). 05-19 is genuine. 06-02/06-03 and 05-04/05-05 are stale daily bars that reverse the next day. All are registered in the tracker |
| **3b. Paper credits no distributions** | The account has never received a dividend or interest row, and Alpaca's docs say paper does not simulate dividends. **So a paper 80/20 earns price return only: 4.079 bps/day geometric (10.82% CAGR), below the 4.7394 target**, against 4.761 on total return. BIL's return is 99.1% distribution. The tracker's CORE line shows "+ uncredited distributions" separately |
| **4. Overlay** | Neither candidate is registrable at the default prior. **C1 (LLM macro-regime core tilter)** is filed in the design queue as an information-value option: INFEASIBLE until a point-in-time macro-text corpus exists, and ADMISSIBLE only at a 1,077-session combined design. **C2 (S&P-100 earnings text) is rejected.** Pre-registration draft: `307p_c1_core_tilter_prereg_DRAFT.md` |

---

## 1. Deliverable 1 — observe mode, proven

`python scripts/verify_observe_mode.py --date D` checks the boot, the evaluation pipeline, order containment at the broker, alert throttling, and the side runners.

**2026-09-25 (exit 0; 0 FAIL, 0 WARN):**

| checkpoint | evidence |
|---|---|
| Boot | Launched 08:28 (late: the PC woke then). Secrets loaded. Pre-flight passed with `Halt Switch: HALT SWITCH IS ON … (advisory only, non-blocking)`. Boot log: `D315 BOOT_SELF_TEST posted: T2=True HALT=True HALT_SOURCES=settings+file` |
| LLM health | `D95 MODEL CONFIG: Tier1=meta-llama/Llama-3.3-70B-Instruct-Turbo`; `D95: LLM provider preflight PASSED`. **One** `LLM_PRIMARY_MODEL_DEAD` all session (it was 9–16 per session before doc 306). It was real: at 09:20:35 Together returned `ServiceUnavailable` 5 times in a row and the breaker tripped for 60 s |
| Evaluation | 199 Phase-A evaluations (4 of 6 agents returning signals each time), a feature log, and a populated verdict trace |
| Order containment | Stages: BLOCKED_CATALYST_GATE 58, DEFERRED_OBSERVATION 50, **BLOCKED_OPERATOR_HALT_EXEC 13**, BLOCKED_NEWS_GATE 10. **SUBMITTED 0.** 13 D277 refusals across 4 tickers, 13 executor pre-submission skips. **Alpaca: 0 orders created, 0 fills** |
| Alert throttling | The code rate-limits halt alerts to 1 per ticker per minute (`alerts.py:808`). The 13 refusals span 13 distinct ticker-minutes, so the cap allows at most 13 alerts. Delivered alerts are pruned from the spool, so the disk cannot show the Discord count itself |
| Side runners | Lottery and fader both logged the halt and made 0 submissions |
| Nightly chain | EOD failsafes clean (D242/D241/D238). Scorecard ran the shadow tracker (+2 sessions healed), the frozen RV ledger reported itself stopped, and config-truth found only the long-known user-scope `MOMENTUM_T2_ENABLED` |

**2026-09-23:**
- Boot phase at 05:26: every check OK. The launcher fired at 04:30:01; D95 PASSED; 0 `LLM_PRIMARY_MODEL_DEAD`.
- Post-open at 09:40: exit 0 with one WARN. There were 54 evaluations and 0 SUBMITTED, but no verdict had reached the executor yet, so no `BLOCKED_OPERATOR_HALT_EXEC` existed to check.
- A first attempt ran at 05:40 by mistake: Git Bash ignores `TZ=America/New_York`, so "09:40" meant 09:40 UTC. The re-run used the machine's local clock.

**2026-09-24: no session.** No bot, lottery, fader, ingest or operator log exists for that day. The PC was off or asleep through every scheduled task, and nothing alerted on it. (Uptime was also the first failure found in doc 287.)

---

## 2. Deliverable 2 — the core, and everything that could have sold it

### 2a. Why the order paths had to be audited first

Positions the bot did not open are exactly what its safety machinery closes. The D277 halt blocks **entries**, not exits. Two independent auditors traced every broker mutation and every consumer of broker state; a merger re-read every cited line and confirmed **24 paths** (P01–P24). The worst, on the pre-doc-307 code:
- **P01/P02:** the 04:30 boot would take over SPY/BIL as strategy positions (D56), and **D91 would market-sell the whole core at 09:30**.
- **P07:** the D313 hedge watcher would place GTC sell stops on it within 60–80 s, re-placing them if cancelled.
- **P05/P06:** at 16:00:35, D242 would try to force-close it, and D241 would cancel every core order.
- **P08/P09/P10/P11/P12:** boot and EOD sweeps would cancel core orders (`cancel_all_orders`, D86, D107, D248).
- **P24:** the launcher and watchdog kill any python whose command line contains "momentum", which includes the rebalancer.

There is no flatten-all breaker, and no strategy trades SPY or BIL.

### 2b. What was built

- **`src/core_benchmark.py`, the contract.**
  - CORE_SYMBOLS = {SPY, BIL}, `core-` order prefix, `is_core_symbol`, `is_core_owned_order` (the prefix AND a core symbol), `strategy_positions`, `non_core_orders`.
  - `core_halted()` fails closed: any value other than empty/0/false/no/off halts, read from os.environ and the rebalancer's `.env` reader.
- **Exemptions in the bot** (every edited module imports the contract through a guarded block with a hard-coded fallback):
  - boot sync D56/D64 skips core symbols;
  - the D86/D91 boot loops run on strategy positions only;
  - D107 and the D86 cleanup keep core-owned orders;
  - the three `cancel_all_orders` sweeps become "cancel every non-core order", falling back to cancel-all if the listing fails;
  - D248, D242, D241 (by prefix, so un-prefixed stops on SPY/BIL are still cancelled), D313, the fill stream, max-position counts, D238/D222 P&L, the recon ghost check and equity estimate (tracking the core's own moves and cash), and config-truth are all exempted.
  - A strategy order on SPY/BIL is refused at every call site **and at the transport** (`BLOCKED_CORE_SYMBOL`). An un-prefixed SPY/BIL order is surfaced as `[CONFIG-DRIFT] STRATEGY_TRADED_CORE_SYMBOL`.
  - A refused close is never booked as closed.
  - The launcher and watchdog match only the bot's own command line.
- **`scripts/core_rebalancer.py`** (`--check` is the default and read-only; `--execute`; `--liquidate --confirm LIQUIDATE-CORE`; `--status`):
  - **Sleeve:** 80/20 SPY/BIL of equity × `MOMENTUM_CORE_SLEEVE_FRACTION` (default 0.85, per the directive's "up to 85%"; hard cap 0.98).
  - **Triggers:** initial build, the month's last session, or drift beyond ±5 pp. Min $500, whole shares, sells before buys, marketable limits at the SIP bid/ask.
  - **Refusals:**
    - not paper-api.alpaca.markets;
    - halted;
    - outside **10:00–15:30 ET** or within 30 min of the close;
    - account not ACTIVE;
    - an open core order, or a foreign order on a core symbol;
    - a stale, crossed, locked, future-dated or over-wide quote (SPY > 5 bps, BIL > 15 bps);
    - an unreadable state file;
    - `.env` readers that disagree on the fraction;
    - a rebuild after a liquidation without an explicit flag.
  - **Robustness:** an OS lock on an open handle (a dead process releases it; a stalled live run cannot be broken). An `intent` row is written before every POST. Reconciliation at the start of every run, and while halted if rows are waiting, backfills any `core-` order the ledger lacks from the broker's listing. Terminal rows are dated by the broker. A torn ledger tail can't swallow the next row.
  - **Ledger:** `data/reports/core_fills_ledger.jsonl`. The shadow ledger stays pure: it is a $0 benchmark computed from official closes.
- **Shadow tracker:**
  - the admin cash-flow registry (§3);
  - a **CORE** line (the real sleeve vs the shadow on the same capital and dates, plus "+ uncredited distributions" while paper credits none);
  - "of which CORE → OVERLAY" on the STRATEGY lines;
  - every `[shadow]` line ≤ 300 characters so the scorecard never cuts an adjudication off.
- **Docs:** QUARANTINE.md "The core sleeve is not covered by the strategy halt" (how to halt, how to unwind, the window, the lock and reconciliation), plus the core contract in the operator runbook.

### 2c. How it was verified

- **Round 1.** The skeptics found 6 exemption defects and 11 rebalancer defects. The two HIGH ones: no run lock (two overlapping runs doubled the core onto margin), and a halt in `.env` was ignored while `.env` sizing was honoured.
- **Round 2.** 5 new rebalancer defects came from attacking the fixes. Examples: a liquidation could be undone, a stalled run's lock could be broken, and an accepted order could go unrecorded.
- **Round 3.**
  - **Exemptions:** SOUND.
  - **Rebalancer:** LOW only. The torn ledger tail, the unenforced window and the fraction readers were then fixed and mutation-checked in the main session (3 of 3 killed).
- **Both final skeptics answered the go/no-go question explicitly.**
  - **Exemptions:** "Tomorrow's 04:30 boot on this code is safe for a core bought at about 10:05 ET."
  - **Rebalancer:** "Safe to run `--execute` once on paper … with a human watching."
- **Evidence from the rounds:**
  - Fuzz: 2,387 runs, 1,615 orders, 315 hard kills. The cap was never exceeded, cash never went negative, and ledger = broker = tracker in all 600 sessions.
  - OS-lock tests used real second processes.
  - A mock broker aged core orders out of the 500-row listing; the recon showed 0 drift.
  - Mutation: rebalancer 100/100 + tracker 37/37 (round 2) + 3/3 (final); exemptions 31/31 revert-one-exemption.
- **Live proof.** 09-23 and 09-25 ran the working-tree code: the EOD failsafe was lazy-loaded on 09-23, and the full boot on 09-25. Both ran clean.
- **Full suite:** see §6.

### 2d. The initial allocation: why it hasn't happened, and how it should

- **Not on 09-23.** The bot running that day had loaded the old D313 watcher at boot, and it cannot be exempted without a restart. It would have placed emergency stops on the core within 80 s and raised CRITICAL alerts.
- **Not on 09-24.** The PC was off.
- **Not on 09-25.** I did not re-engage that day, and an unattended first live order was never the plan.
- **The first allocation is Monday 2026-09-28**, between 10:05 and 15:30 ET, with a human watching:
  1. `python scripts/core_rebalancer.py --check`: confirm the plan (about SPY 166 / BIL 350 at current prices) and that the fraction line reads as intended.
  2. `python scripts/core_rebalancer.py --execute`: don't Ctrl-C during the 60 s poll.
  3. `python scripts/core_rebalancer.py --status`, and check the Alpaca UI.
  4. After the first 80 s, the bot log must show no D313 stop or D231 ghost line for SPY/BIL. At about 16:00:35, D242/D241 must log the core as exempt.
  5. The 09-29 04:30 boot is the first night held; it must not name SPY/BIL in any D56/D64/D91 line.
- **Nothing schedules the rebalancer yet.** The month-end and drift triggers fire only when someone runs it. A scheduled task is persistent configuration, and it is Pierce's to approve (§7).
- **Transaction costs.** The plan's estimate is SPY +0.26 bps and BIL +0.55 bps vs mid (half-spread), consistent with the directive's NBBO schedule. The real figures will be the ledger's `cost_bps_vs_mid` after Monday's fills. Paper fills are simulated, so they measure Alpaca's simulator, not the market.

---

## 3. Deliverable 3 — the broker equity reconciliation

Evidence:
- **Activities:** every activity type was queried (52; 27 more by the skeptic). Only FILL, FEE and one JNLC exist. Replaying them from zero reproduces final equity to $0.61.
- **Prices:** every held-position day matches Polygon official closes to a constant $0.57–0.65 fee residual, except the stale bars below.

| date | Alpaca P/L | class | amount | excluded from strategy P&L? |
|---|---|---|---|---|
| 2026-02-17 | 0 (starting balance) | FUNDING_DEPOSIT (JNLC dated 02-18) | +$100,000.00 | not booked as P/L, so not subtracted again |
| **2026-02-26** | +$42,757.58 | **UNAPPLIED_CORPORATE_ACTION**: CRCA's 1-for-10 reverse split was never applied to the paper position, and 1,143 shares were marked at the post-split close | **+$41,086.28 phantom** (real day: +$1,671.30) | **yes** |
| 2026-02-27 | −$3,337.73 | same event, re-mark | −$3,363.85 phantom | yes |
| 2026-03-02 | +$2,774.45 | same event: 1,143 "shares" sold, but only 114.3 existed | +$2,281.16 phantom | yes |
| 2026-05-04 / 05-05 | $0.00 / +$112.44 | STALE_DAILY_BAR (05-04 copies 05-01) | true +$105.92 / +$6.59 | no; re-dated |
| 2026-05-19 | +$16,599.21 | GENUINE_PNL (NXXT 0.41 → 0.8201 × 40,476) | +$16,599.21 | no |
| 2026-06-02 / 06-03 | −$19,939.98 / +$88,314.88 | STALE_DAILY_BAR (LASE/STAK marked at prior closes below that day's lows) | true +$22,495.30 / +$45,879.60 | no; re-dated |

- **The phantom is still in the account.** It totals **$40,003.59** (= 0.9 × the $44,448.43 CRCA proceeds). **True equity is $148,924.24.** Cumulative P&L since funding is **+$48,924.24, not +$88,927.83.**
- **Where it is recorded.** The events are in `data/reports/shadow_benchmark/admin_events.json` (private repo). The tracker excludes only events that are both cash flows and booked in Alpaca's P/L, and re-dates stale bars. On the real cache, funding-to-date STRATEGY is +$48,924.24 to the cent, and no registered day raises a WARNING.
- **The core would be sized partly on phantom cash.** The rebalancer sizes on broker equity, which includes the phantom. That is Pierce's call (§7).

---

## 4. Deliverable 4 — the overlay proposal

| | C1 macro-regime core tilter | C2 S&P-100 earnings-quality long/short |
|---|---|---|
| Family | New at the mechanism level (text-driven index timing). But it reads the row-17 EX-99 channel, and its closest neighbours are row 31 (overnight index ETF) and doc-298 M2 (vol-managed SPY, IR +0.08/−0.04) | Filing text is row 17 (UNDERPOWERED, and SS0006 went NO-GO in doc 306). Hedged per-name spreads were ruled a price-feature variant of row 12 |
| Contamination | Llama 3.3 70B: knowledge cutoff Dec 2023, released 2024-12-06 (HF model card, fetched). Clean window **2024-12-09 → 2026-09-22 = 447 sessions / 94 weeks** | Same window: ~413 Item-2.02 events/yr in the top-100 ADV. The model also knows these companies' histories |
| Friction | SPY 0.26 / BIL 1.09 bps quoted; ≤ 14.35 bps/yr at 2 switches/month (IR drag ≤ 0.041) | 1.76–2.27 bps round trip; 100/100 ETB; Rule 201 ≥ 12.1% |
| eig at N(0, 0.5), ω 0.25, 33 trials | R-only 447: UNDISCRIMINATING. Forward 12m CEREMONIAL; 24m/36m UNDISCRIMINATING. **R + 30 months (n = 1,077): ADMISSIBLE** (p_cert 0.0470, BAR 1.8233); CEREMONIAL under a literature-shaped N(0, 0.3) | Same machinery; MDE 236–1,072 bps TOP − BOTTOM per ticket |
| What it would take | MDE IR 2.64 ≈ an **86% weekly hit rate** under the 2-changes/month cap (92% on R). A literature-sized IR of ~0.25 would need 340–476 years to certify | An effect larger than large-cap PEAD ever showed after it decayed |
| Verdict | **Filed in the design queue as an information-value option; the planner says INFEASIBLE** (keystone `point_in_time_macro_text_corpus` missing). The skeptic judges "register neither" the better-supported call | **Rejected** |

The C1 draft carries the skeptic's amendments:
- the bar uses N = max(34, `trial_count()` at EVAL);
- a weekly-reversal baseline B3 is added (IR +0.54, the strongest found);
- PASS-UNATTRIBUTED is never promotable, and an undefined G2a counts as FAIL;
- the forward half needs a one-sided 90% lower bound above 0, because a leaked retro half lets a no-skill forward half pass up to 6.2% of the time;
- the hit rates are corrected for the cap;
- the precision rules for the fixtures are listed.

**The honest framing:** C1's value is information (closing the "LLM macro timing" door cheaply), not expected profit. The core already meets the target on total return. Registration is Pierce's call and is not recommended.

---

## 5. What this means for profit

1. **The binding constraint on paper is the simulator, not strategy.** A real 80/20 core met the target over 2016–2026 by 0.02 bps/day on total return. A paper core cannot show it, because paper credits no distributions: 4.079 bps/day price-only against 4.7394. At the 0.85 fraction it falls to 3.506. On a live account the same sleeve would collect the distributions.
2. **The reported account P&L is overstated by $40,003.59.** Every since-inception number before today includes a split Alpaca never applied.
3. **The bot is safe to hold a core beside.** Every path that could have sold it at 09:30, stopped it within 80 s, or cancelled it at 16:00 is exempted, tested and mutation-checked.

## 6. Tests

**Full private suite (2026-09-27, committed tree):** 80 failed, 4,880 passed, 3 skipped, 3 xfailed.
- **Every one of the 80 failures is in the pre-existing doc-306 baseline set** (81 there). None is new.
- The one baseline failure that now passes is the VLL emit-map test: doc 304 had added a stage without mapping it.
- Private commit `40560e9` (local develop).
- The research repo's epistemics tests pass 92/92 with the C1 queue entry.

**Research repo:** unchanged code; this doc, the 307p draft, the queue entry, TARGET.md and ledger notes.

## 7. Decisions that are Pierce's

1. **Run the first allocation Monday 10:05–15:30 ET** (steps in §2d), or tell me to and watch it.
2. **Schedule the rebalancer** (e.g. weekdays 15:15 ET, `--execute`) for the month-end and drift triggers. This is persistent configuration and needs your approval.
3. **Size on broker equity (includes the $40,003.59 phantom) or on true equity?** The rebalancer uses broker equity today.
4. **The sleeve fraction.** 0.85 follows the directive. With the strategy quarantined, 0.98 recovers some drag. On paper it recovers only SPY price return, because BIL ≈ cash there.
5. **P15, before any un-quarantine:** size overlays on equity minus the core's market value.
6. **Uptime.** 09-24 was lost to the PC being off, with no alert. A wake timer, or keeping the machine awake on weekdays, is yours.
7. **C1:** keep it as a queued option (INFEASIBLE until a corpus exists), or drop it.

## 8. Provenance

- **Private repo (local develop, `40560e9`):**
  - new: `src/core_benchmark.py`, `scripts/core_rebalancer.py`, `tests/unit/test_core_rebalancer.py`, `tests/unit/test_doc307_core_exemptions.py`, `tests/integration/test_doc307_core_positions.py`;
  - bot exemptions in `position_manager.py`, `startup_order_cleanup.py`, `main.py`, `bridge.py`, `eod_failsafes.py`, `hedge_integrity_watcher.py`, `fill_stream_bridge.py`, `alpaca_executor.py`, `eod_recon.py`, `alpaca_client.py`, `trade_journal.py`, `post_fill_handler.py`, `lottery_runner.py`, `fader_short_runner.py`, `lottery_attach_trailing_stops.py`, `config_truth_recon.py`, `verify_observe_mode.py`;
  - the launcher and watchdog `.ps1`, `shadow_benchmark_tracker.py`, QUARANTINE.md and the operator runbook.
- **Research repo:** this doc, `307p_c1_core_tilter_prereg_DRAFT.md`, the `design_queue.json` C1 entry, TARGET.md §0a (paper distributions), and ATTEMPTS_LEDGER (C1 queued, C2 filtered).
- **Workflow artifacts:** scratch `doc307/` (audit1, audit2, merge, exemptions, rebalancer, jumps, overlay, fix_*, fix2_*, and every verify / reverify / reverify2 directory).
