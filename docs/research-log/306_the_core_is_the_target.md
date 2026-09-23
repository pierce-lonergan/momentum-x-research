# 306 — The Core Is the Target: Restored Access, a Shadow Benchmark, and an Empty Queue

**Date:** 2026-09-22 (evening) → 2026-09-23 00:20 ET
**Status:** Credentials verify, exit 0. **The bot's tier-1 model no longer exists on Together**, so tier 1 and 2
now name the one model the account can still call. A **shadow 80/20 SPY/BIL benchmark** runs nightly, and the
target is pinned as a compound standard. **SS0006 is NO-GO**: its power is out of reach and its frozen model is
retired. **Trial 34 was not registered; the registry stays at 33.** The design queue is **empty**. Mode B
(observe) is unchanged, and its first proof comes from the 2026-09-23 session (§5).
**Method:** one workflow of six builders, three of them adversarially verified by independent skeptics
(tracker, P-3, LLM forensic), plus inline work. Every agent was barred from reading credential values;
authenticated calls went through a helper that never exposes a key.

---

## 0. The answer

| deliverable | result |
|---|---|
| **1. Credentials / Together** | `verify_credentials.py` **exit 0**: Alpaca, Polygon, Finnhub, Together `/v1/models` and a 1-token Together completion all return 200. **Doc 305's "403, key revoked" was my checker's bug** (Cloudflare rejects urllib's default User-Agent). The real state was **402 `credit_limit`**, and Pierce's $50 cleared it at about 00:00 ET. **The directive's tier-1 lock cannot be honoured:** `Qwen3-235B-A22B-Instruct-2507-tput` returns **400 "non-serverless"** and is absent from the model list. Of 8 models probed, only **Llama-3.3-70B-Instruct-Turbo** answers, so tier 1/2 now name it |
| **1b. What the dead LLM did** | The bot ran on substitute models from **07-13** and with **no LLM at all from 08-11** (29,797 of 29,800 agent calls defaulted). It kept trading on a FinBERT backstop. The CRITICAL alarms fired **every session** and nobody acted. **Kalshi T00028 survives** the audit (no default forecasts exist). The rocket-gate halves split **exactly on the LLM-dead onset**; NOT PASSED stands, now with a scope note |
| **2. Shadow benchmark** | `scripts/shadow_benchmark_tracker.py`, nightly in `post_close_scorecard.py`. 80/20 SPY/BIL total return over 2016-01-04 → 2026-09-22: **CAGR 12.74%, 5.155 bps/day arithmetic, 4.761 geometric, MaxDD −27.48%**, re-derived by a skeptic to 3e-13. **Since 07-06 the strategy trails the shadow by −5.31 pp (−$10,229) in 55 sessions.** Target pinned (TARGET.md §0a): **+1%/month compound = 4.7394 bps/day geometric binds**; 5.00 bps/day is shorthand only |
| **3. SS0006 recalibration** | **NO-GO on Trial 34.** On the true forward definition: Sharpe/bps **0.02407** (−13.8% vs basis A); Rule 201 **24.5%**; the non-SSR universe drifts **−61.9 bps against the short**; mean ticket cost **26.9 bps** (41.9 gross for 15 net). 80% power at ω=0.25 needs a **249 bps (24 mo) / 315 bps (12 mo)** gap, above doc 260's contaminated 240 bps upper bound. **P-4 run: the frozen model returns 400**, so the instrument is retired (§3.7). The EX-99 fix is done (cover-page fallbacks 35.9% → 0% on filings that carry an EX-99) |
| **4. Queue audit** | RV forward ledger **RETIRED** (CEREMONIAL at the default prior); vol-score risk-shaping **RETIRED** (UNDISCRIMINATING); overnight-ETF **CLOSED** on doc-297 evidence. **The queue is empty.** `revive` no longer re-proposes SS0020/SS0019: the consumed `lower_requirement` keystone was retired, which was doc 303's root cause |
| **5. Mode B proof** | **Pending the 2026-09-23 session.** The launcher is scheduled at 04:30 ET. `scripts/verify_observe_mode.py` is built, and it correctly FAILS 09-22 (a live day) as a negative control. The boot log line now records the halt **sources**, so "HALT: ON (settings+file)" is visible in the log, not only on Discord |

---

## 1. Deliverable 1 — credentials, Together, and the six weeks nobody saw

### 1a. The checker, and the correction to doc 305

Together sits behind Cloudflare, which answers **403** to urllib's default `Python-urllib/3.x` User-Agent. My
doc-305 checker sent that default, so it read every Together endpoint as 403 and I reported a revoked key.
With any real User-Agent: `/v1/models` 200, and chat completions **402 `credit_limit`**. Both checkers now send an
explicit User-Agent, and credentials go in as *unredirected* headers (urllib otherwise copies them onto a 30x
target). Doc 305 §0/§1b/§4/§7, the 304b P-4 row and the memory record carry correction markers.

**Final run, 2026-09-23 00:13 ET** (`python scripts/verify_credentials.py --llm-probe meta-llama/Llama-3.3-70B-Instruct-Turbo`, exit **0**):

| check | result |
|---|---|
| hygiene: duplicate / inline comment / quoted / empty required | all clean (58 keys) |
| Alpaca trading `GET /v2/account` · Alpaca data latest SPY quote | 200 · 200 |
| Polygon `GET /v3/reference/tickers` · Finnhub `GET /quote` | 200 · 200 |
| Together `GET /v1/models` · 1-token chat on Llama-3.3-70B-Instruct-Turbo | 200 · **200** |
| secrets-file ACL | restricted to you, SYSTEM, Administrators |

The checker prints key names, OK/FAIL and status codes only. It never prints a value, a message, or a URL.

### 1b. The tier-1 lock is impossible, so tier 1 now names what answers

1-token probes at 00:0x ET, after the credit posted:

| model | result |
|---|---|
| `Qwen/Qwen3-235B-A22B-Instruct-2507-tput` (the directive's lock; SS0006's frozen id) | **400** "Unable to access non-serverless model"; absent from `/v1/models` (271 listed) |
| `…-2507-FP8`, `Qwen3-Next-80B-A3B-Instruct`, `DeepSeek-V3.1`, `Qwen3.5-397B-A17B`, `Qwen2.5-7B-Instruct-Turbo` | **400** non-serverless |
| `openai/gpt-oss-120b` | **402** credit_limit, while Llama answers (cause unverified; possibly a thin balance and per-model pre-authorisation) |
| **`meta-llama/Llama-3.3-70B-Instruct-Turbo`** | **200** |

**Executive call:** `LLM_TIER1_MODEL` and `LLM_TIER2_MODEL` now name Llama-3.3-70B-Instruct-Turbo, in
`~/momentum-x-secrets.env` (backup `.bak-doc306`; hash check: exactly those two keys changed; `.env`
byte-identical).
- The bot has run on this model as its fallback since 07-13, so its behaviour does not change.
- What changes: the free 400 hop disappears, and so do the **9–16 false `LLM_PRIMARY_MODEL_DEAD` CRITICAL incidents per session**.
- The D95 preflight can pass again, and the configured model is the model that answers.
- The bot's Qwen2.5-7B screener fallback is non-serverless too, so its calls land on Llama as well.

### 1c. The forensic: what the bot has actually been since July (skeptic-verified)

Every count was re-derived by an independent skeptic from raw logs, after reading the code that writes each
line (`src/agents/base.py:412` success, `:340` all-failed default).

| class (rule) | sessions | agent calls | real completions | defaults |
|---|---|---|---|---|
| HEALTHY | 2 (07-07, 07-09) | 2,296 | 2,296 | 0 |
| DEGRADED: configured primary 0%, all on Llama/Qwen2.5-7B | 21 (07-06, 07-13 → 08-10) | 22,636 | 22,603 | 33 |
| **DEAD: < 5% real completions** | **22 (08-11 → 09-22)** | 29,800 | **3** | **29,797** |

- **Timeline.**
  - 06-30: Qwen3.5-397B, the manipulation classifier's primary, starts returning 400.
  - **07-13:** the `-tput` tier-1 starts returning 400 "non-serverless". Its last success was the 07-09 session. From then on everything ran on Llama fallbacks.
  - **08-10 14:00:38 ET:** the first 402 hits all three models within 0.33 s.
  - **08-11 09:32:13:** the last successful Together completion in any log.
- **The pipeline kept trading.** A dead agent returns NEUTRAL, conf 0.0, and still counts at full weight in the MFCS. The FinBERT keyword backstop turns NEUTRAL into BULL. Result: **11 entries on DEAD days, every one preceded by a backstop BULL 1–6 s earlier**, plus 2 broker fills not in the bot's log (CAPR 08-17, PSNYW 08-27; runner not identified).
- **The alarms worked; the response did not.** From 07-13 on, every session had a failed D95 preflight and 9–16 CRITICAL `LLM_PRIMARY_MODEL_DEAD` incidents, matched in `data/ops/incidents_*.jsonl`. The skeptic adds that the configured primary answered under 90% of calls in **42 of 58 sessions from 04-14 to 07-09**. The bot has rarely run as designed.
- **Rocket-gate T00027** (disclosure; not re-tested):
  - H1 (07-06..08-10, LLM alive) **+$60,053**; H2 (08-11..09-18, LLM dead) **−$133,205**. The boundary falls exactly on the onset.
  - The 18 LLM-alive sessions alone also fail criterion 1: CI [−$8,071, +$17,431]. The alive-vs-dead difference has permutation **p = 0.276**.
  - **NOT PASSED stands.** But the forward sample never re-tested the mined population: the configured primary answered 0% in 34 of 36 forward sessions, and never did in the mined window.
  - The registry and ledger carry a scope note: the refutation covers the selector as it actually ran.
- **Kalshi T00028 — the Brier 0.25 worry is cleared.** A constant 0.5 forecast scores exactly 0.25, and the LLM scored 0.2451.
  - The code writes a row only on a parsed HTTP 200; 781/796 rows match the run log; token counts are stable across all 32 runs. Collection simply stopped on 08-11.
  - Recomputed: gap +0.1350, day-blocked CI [+0.1138, +0.1570]. **REFUTED_BY_NATURE stands.**
  - Scope: a no-retrieval, single-call gpt-oss-120b once a day vs the Kalshi mid. The forecaster had almost no resolution (0.0066 vs 0.0954).
- **Also dark:** the doc-255 red-flag LLM scorer has had **no real score for any gap date since 07-07** (400s, then 402s). It wrote `None` rather than inventing values.
- **Correction:** "402 since 08-04" was a row-date artifact. Rows are keyed by gap date and rewritten nightly by `--catchup`.

### 1d. What the $50 buys

- When the LLM worked on fallback (July), the bot made about **1,100 successful completions per session**.
- The bot does not log token usage, so cost per session is **unknown**. At 2K–5K tokens per call on Llama-Turbo ($1.04/Mtok), that is **~$2.3–5.7 per session**, so $50 lasts **~9–22 sessions**. The Together dashboard has the true figure.
- gpt-oss-120b's 402 hints that the balance is thin.
- Observe mode's evidence (evaluation, features, trace) does not strictly need the LLM, because the backstop path runs without it. It does need it to be the designed system. **Pierce's call** (§7).

---

## 2. Deliverable 2 — the shadow benchmark

### 2a. What was built

`scripts/shadow_benchmark_tracker.py` (private repo, ~990 lines) and 36 unit tests. Wired into
`post_close_scorecard.py` as a best-effort block (`--append --compare`, 600 s timeout, can never fail the
scorecard).

- **Series.** Official Polygon daily closes (`adjusted=false`), plus cash distributions on ex-date, plus splits derived from `split_from/split_to`.
  - BIL's 2017-11-30 reverse split: raw +100%, total return 0.0000%.
  - All 95 BIL ex-dates match Alpaca corporate actions.
- **Sleeve.** 80/20 SPY/BIL. Weights drift and reset at the last close of each month. A trade at close t is booked in t+1's return (no look-ahead). Cost is one-way bps × Σ|Δw| (SPY 0.36, BIL 1.135).
- **Ledger.** `data/reports/shadow_benchmark_ledger.jsonl`, 2,695 rows. Idempotent upsert, T+1 healing, no row for a non-final close. A revised source prints a loud `RESTATEMENT`.
- **Credentials.** Keys are sent only as unredirected headers, never in a URL. Failures print a status or class name. The live path ran once on 2026-09-23: all sources 200, 0 restatements, no key in any cache file.

### 2b. Calibration, 2016-01-04 close → 2026-09-22 (2,694 returns)

| portfolio | CAGR | arith bps/day | geo bps/day | vol | MaxDD | Sharpe vs BIL | trailing-252 windows meeting the binding bar | worst / best trailing-252 |
|---|---|---|---|---|---|---|---|---|
| SPY TR | 15.29% | 6.274 | 5.648 | 17.73% | −33.70% | 0.772 | 68.6% | −19.73% / +77.52% |
| **80/20** | **12.74%** | **5.155** | **4.761** | 14.08% | −27.48% | 0.772 | **59.5%** | −15.53% / +57.94% |
| 60/40 | 10.15% | 4.056 | 3.837 | 10.49% | −20.98% | 0.772 | 36.2% | −11.30% / +40.71% |
| BIL TR | 2.14% | 0.842 | 0.842 | 0.26% | −0.21% | n/a | 0.0% | −0.13% / +5.44% |

80/20 by calendar year (%): 2016 10.83 · 2017 17.21 · 2018 −3.16 · 2019 24.98 · 2020 15.12 · 2021 22.54 ·
2022 −14.23 · 2023 21.83 · 2024 20.79 · 2025 14.98 · 2026 YTD 11.99.

**Verification.**
- The skeptic re-derived every field with independent code, agreeing to 3e-13. Against doc 305's blueprint at 09-21 the SPY and BIL series match exactly; 80/20 differs by 6e-7 pp, from booking the rebalance cost at t+1.
- A third-source check found **the warehouse is wrong, not the tracker**. In March 2020 its closes are Alpaca raw closes, not official closes (2020-03-12: 255.24 vs 248.11). It also omits two SPY dividends: 2016-03-18 and 2018-06-15, confirmed against IVV/VOO. **TARGET.md's "SPY 14.91%" came from that series**; it is corrected to 15.29%.

### 2c. Strategy vs shadow vs cash (first nightly lines)

| window | STRATEGY (broker equity) | SHADOW 80/20 | CASH (paper earns 0; BIL TR forgone) | strategy − shadow |
|---|---|---|---|---|
| last session (09-21) | +0.000% | +1.243% | 0 (BIL +0.022%) | −1.243 pp (−$2,349) |
| MTD (14 sessions) | +0.188% (+$354) | +0.914% (+$1,724) | 0 (BIL +0.208%) | −0.727 pp (−$1,370) |
| **since 2026-07-06 (55 sessions)** | **−1.876% (−$3,611)** | **+3.437% (+$6,618)** | 0 (BIL +0.749%, $1,441) | **−5.313 pp (−$10,229)** |
| trailing 252 | n/a: the account was funded 2026-02-17 | | | |

**Four unexplained broker days.** Alpaca books four single-day P/L moves larger than 10% of equity as P/L, not
deposits: **2026-02-26 +42.7%, 05-19 +12.4%, 06-02 −13.4%, 06-03 +68.6%**. No trade log explains them. Any
window containing one now prints a WARNING. They dominate any since-inception figure.

### 2d. Skeptic defects and fixes

The skeptic's verdict was DEFECTS: the numbers were right, but the protections around them were thin.

| # | defect | fix |
|---|---|---|
| D1 | urllib forwards `Authorization`/`APCA-*` headers to any redirect target | keys are added with `add_unredirected_header` (tracker and credential checker); test |
| D2 | `Request()` built outside `try`, so a malformed base URL raised and lost the night's row | built inside `try`; test |
| D3 | the "binding test is geometric" test could not tell geometric from arithmetic (the two never disagreed on its fixture), so 3 mutants survived | a high-volatility fixture where arithmetic ≥ 5.00 and geometric < 4.74; the verdict, the ledger flag and the window target are all pinned |
| D4 | ledger drawdown and STRATEGY cash-flow handling unpinned | hand-checked drawdown-vs-peak test; deposit-in-window test |
| D5 | the four broker jumps would silently dominate the trailing-252 line from ~Feb 2027 | `broker_jumps()` flags them in every window; test |
| D6 | a test message claimed the shorthand is always looser | corrected (true only above ~11.5% vol) |

**Mutation re-check:** 8 of 8 targeted mutants killed, including the valid arithmetic-for-geometric swap.
36/36 tests pass.

### 2e. The standard, pinned (TARGET.md §0a)

- **+1%/month compound = 12.6825%/yr = 4.7394 bps/day geometric, on a trailing-252 window. That is the pass test.**
- 5.00 bps/day arithmetic is shorthand. At the core's 14.1% vol the two disagree; 60.7% vs 59.5% of windows pass.
- **The 80/20 core clears the binding bar by 0.02 bps/day over ten years.** It *is* the target, with a −27.5% drawdown, and 40.5% of its trailing years missed.
- Every active strategy is therefore judged as an **overlay on the shadow**, not against the target.

---

## 3. Deliverable 3 — SS0006 recalibration memo

### 3a. EX-99 selection, fixed

The new rule selects by **EDGAR document Type**, not file name: the lowest-numbered `^EX-99(\.\d+)?$` exhibit
(.htm/.html/.txt) from the index page, else `primaryDocument`, counted as PRIMARY-FALLBACK. It is a pure
function with 15 unit tests, and all 9 plausible-bug mutants are caught.

| rule | cover fallback, P-5 eligible (64) | cover fallback, 2025 sample (400) | wrong document where the release existed (2025) |
|---|---|---|---|
| frozen doc-260 name regex `ex.?99` | 23 (35.9%) | 143 (35.8%), 138 of them despite an EX-99 | 35.4% |
| **type rule (adopted)** | **0** | **5 (1.2%), all with no EX-99 at all** | **0.8%** (BXP, Novelis; hand-checked) |
| broader filename regex | 37 (57.8%) | 223 (55.8%) | 56.0% |

No filename rule can work: 71 of 400 releases carry no "99" in the name. 304b §3.2, F-12 and §1.4 are updated,
with the declared deviation. **A caution about doc 260 itself:** if its filings behaved like these, about a third
of its scores were made on cover pages (not measured).

### 3b. P-3 power on the true forward definition (2025; skeptic confirmed all five load-bearing numbers)

- **Events.**
  - Form 8-K Item 2.02: 16,826, then 16,771 after one-per-issuer-per-session, leaving **11,769 eligible** (3,198 tickers, ~47/session). D_e is the filing date for 41% of events.
  - Thin months: Jun/Sep/Dec, at ~1.3–1.5 tranche tickets/session.
  - Survivorship: 939 events on since-delisted tickers are kept.
- **Rule 201 at T_e, from 1-minute bars: 24.54%** (Wilson 23.8–25.3%). The draft's 10.6/22.1% were *not* upper bounds, because daily bars miss after-hours prints. It runs 43% under $5 and 36–37% in the off-season months (P-5 measured 31.2%).
- **Universe.**
  - Non-SSR mean gross **−61.9 bps** (moving-block 90% [−114, −6]); 10.4 bps of that is dividends the short owes.
  - The SSR-flagged subset alone would have gained **+81.0 bps**; the exclusion removes exactly the names that kept falling.
  - Hedged sd 10.19%.
- **Book.** Sharpe per bps **0.02407** iid, **0.02331** with block-10 (basis A was 0.02791). About 2,296 tranche tickets/yr.
- **Cost** (648 tickets, measured SIP quotes): **mean 26.92 bps**, median 18.96. By tercile: low 40.5, mid 24.5, high 15.7. The frozen last-quote mark adds ~1.5 bps. **Gross for 15 net: 41.9 bps.**

| n (sessions) | BAR (34 trials) | X80 net / gross (ω=1) | X80 net / gross (ω=0.25) | tranche − universe gap needed (ω=1 / ω=0.25) |
|---|---|---|---|---|
| 252 (12 mo) | 3.7694 | 192 / 218 | 227 / 253 | 280 / **315** |
| 504 (24 mo) | 2.6654 | 135 / 162 | 160 / 187 | 224 / **249** |
| 756 (36 mo) | 2.1763 | 111 / 138 | 131 / 158 | 199 / 220 |

**Is the ω=0.25 MDE within reach? No.**
- Doc 260's 240 bps is a contaminated, median, unhedged, gross upper bound, and it still falls short at 24 months.
- Half of it (≈31 bps net) gives **P(G1a) ≈ 0.3%**.
- eig at N(0, 0.5): **CEREMONIAL at 252, UNDISCRIMINATING at 504**. Admissibility needs prior sd ≥ 0.68–0.70 (≈28–29 bps net), wider than any design ever queued.

**Also found:** some submissions-JSON `acceptanceDateTime` "Z" values are really Eastern time. About 0.2% of D_e
land one session *early*, which contradicts 304b §2.4's robustness claim. There is no look-ahead. 304b §2.4 is
corrected.

### 3c. P-4, executed

`python scripts/ss0006_p4_canary.py` ran at 04:02Z. The warm-up on the frozen id returned **HTTP 400
`invalid_request_error`**, and no canary was scored (`data/research/ss0006_p4/p4_run_20260923T040251Z.json`).
Under 304b §3.7 ("the provider retires the id") the instrument is gone before freeze. Any substitute model is a
new pre-registration, with its own power case.

The runner, the five synthetic canaries (fictional tickers TQLM/QRWB/OVNQ/PLZC/DXCB, verified unlisted,
spanning STRONG_UP→STRONG_DOWN), the template hash check (`9369…cfe` reproduced) and 51 tests are kept, so a
successor has its canary machinery ready.

### 3d. Go/no-go

**NO-GO. Trial 34 is not registered.** There are two independent grounds:
1. The design cannot certify any effect the evidence supports.
2. Its frozen model no longer exists.

304b carries a disposition banner. It will VOID on its own death date (2026-11-30) unless superseded.

---

## 4. Deliverable 4 — design-queue audit

| design | verdict at N(0, 0.5), 33 trials, ω=0.25 | state | disposition |
|---|---|---|---|
| `rv_forward_shadow_ledger` (T00030) | **CEREMONIAL** (p_cert 0.0307, informativeness 1.032, EIG 0.0074); ADMISSIBLE only at sd ≥ 1.96 | blind at **47/60** forward sessions; by its own prereg it "changes no decision"; 2026-08-28 missing from minute_aggs; 4 rows scored on a training panel changed by the 09-21 backfill (`build_panel` has no date floor) | **RETIRED, ABANDONED.** Nightly append **stopped** (`_RV_FORWARD_CLOSED`) |
| `vol_score_risk_shaping` (T00021) | **UNDISCRIMINATING** (informativeness 1.467 vs 1.5; ADMISSIBLE at sd ≥ 0.517, a fragile margin) | HELD Stage-1 config-diff; its only book is the gapper universe, SEARCH CLOSED at −2.041%/ticket; sizing is sign-preserving | **RETIRED, ABANDONED.** The retirement does not depend on the margin |
| `overnight_etf_excess_of_cash` (T00024) | ADMISSIBLE (p_cert 0.0734), but that measures information value, not a licence | doc 297: excess-of-cash 1.03 bps/day, CI spans 0; buy-and-hold wins 4/4; certifying it needs 3,079 sessions and 2,692 exist | **CLOSED** on the doc-297 evidence |

**The queue is empty**, and `epistemics.py plan` returns `[]`. At the default prior, the smallest effective sample the
planner rates ADMISSIBLE is **~927 sessions (~3.7 years)**. No shorter forward collector can be registered.

**The re-proposal hazard, fixed at the root.**
- `revive` ranked **SS0020 (overnight-ETF) #1 FULLY UNBLOCKED** on `lower_requirement`. SS0019 (LETF), SS0009, SS0015 and SS0023 showed the same.
- That keystone was consumed on 2026-07-29: TARGET.md §0 re-checked all 35 families at the halved bar, and **zero** changed status. Left "available", it re-proposes them forever, which is doc 303's root cause.
- It is now moved to `_consumed_lower_requirement`. SS0019/SS0020 are re-keyed to the evidence their closures actually need. `revive` now ranks nothing FULLY UNBLOCKED.

**Alarm hygiene.**
- `config_truth_recon.py` had reported the doc-305-closed rocket gate and Kalshi as `INSTRUMENT_DARK` every night since their closure, and would have added the RV ledger.
- A breach list that fires on purpose teaches the reader to ignore it. That is how `LLM_PRIMARY_MODEL_DEAD` fired for six weeks unanswered.
- Closed instruments now report `CLOSED (doc N)`, not a breach (`CLOSED_INSTRUMENTS`; 2 new tests).

**Registry** (hash-chained; both copies identical, sha `ed272aaa…`; chain OK, 77 rows; **33 trials**): T00030 →
ABANDONED; T00021 → ABANDONED; T00024, T00027 and T00028 get doc-306 notes.

---

## 5. Deliverable 5 — Mode B execution proof (pending the 09-23 session)

It is 00:20 ET on 09-23. The launcher (`MomentumX-PaperTrading`) fires at **04:30 ET**. It fired at 04:30:01
every day from 08-22 to 09-21; on 09-22 it ran at 07:45, most likely because the PC was asleep. Built for the
proof:

- **`scripts/verify_observe_mode.py [--date D]`.** One line per check:
  - Boot: secrets loaded, pre-flight passed, pre-flight Halt Switch ON (advisory), D315 `HALT=True HALT_SOURCES=settings+file`, D95 tier-1 = configured, LLM preflight.
  - Session: Phase-A evaluations ran, feature logs, verdict trace with **0 SUBMITTED** and halted verdicts ending in `BLOCKED_OPERATOR_HALT_EXEC`, D277 refusals, and fixture-row detection.
  - Broker: **0 orders and 0 fills** from Alpaca, the check that counts.
  - Alerts: halt-alert rate ≤ 1 per ticker-minute (spool titles only; spool files hold the webhook URL).
  - Side: lottery/fader runners did not submit.
- **Negative control on 09-22**, a live day: **3 FAIL** (pre-flight halt line OFF, banner HALT=OFF, 13 SUBMITTED). It also caught that those 13 SUBMITTED are **test-fixture rows** (BOOM/test-order-0, MagicMock errors) written at 18:23 ET during the doc-304 test runs, before the suite's trace isolation landed. The broker shows **0 orders**, and the account's last real order was 2026-09-16. Nothing has leaked since 20:00 ET.
- **`main.py` boot log** now carries `HALT_SOURCES=` (logging only; 14 quarantine tests pass).

Tomorrow's session is also the first with tier 1 = Llama. The D95 preflight should pass, and the
`LLM_PRIMARY_MODEL_DEAD` storm should stop.

---

## 6. What this means for profitability

1. **The core is the target.** A $0 index/T-bill sleeve has historically delivered +1%/month compounded, barely, with −27.5% drawdowns.
2. **Every overlay the program has measured subtracts from it.** Since 07-06 the bot is −5.31 pp behind the shadow. SS0006, the one live candidate, cannot be certified. The design queue is empty.
3. **The bot has not been the designed system since at least 07-13.** Its measured P&L since then is the P&L of a FinBERT keyword backstop plus technicals. That does not revive the closed long-universe verdict, which rests on market data, not on the bot's log. It does mean nobody should read the live book as "the LLM strategy".
4. **The honest next step toward profit is an allocation question, not a research question.**
   - Whether to run the core for real in the paper account has three written blockers from doc 305 (§5c): the EOD failsafe force-closes untracked positions, the 5% position cap, and the lack of a kill switch for a core.
   - That is Pierce's decision, and this is not personal investment advice.
   - The shadow ledger now keeps score either way.

## 7. Decisions that are Pierce's

1. **LLM spend in observe mode.** At an estimated $2.3–5.7 per session, $50 lasts ~9–22 sessions (no token logging; the dashboard has the truth). Keep it, cap it, or run observe mode on the backstop alone.
2. **The four broker equity jumps** (02-26, 05-19, 06-02, 06-03). Were these paper-account resets or adjustments? Until labelled, since-inception comparisons are meaningless.
3. **The core.** Whether to build a real 80/20 sleeve in the paper account, which needs the three doc-305 blockers resolved.
4. **SS0006.** Let 304b VOID on 2026-11-30 (recommended), or commission a successor with a served model and a new power case.

## 8. Provenance

- **Private repo (local commits).**
  - Code: `scripts/shadow_benchmark_tracker.py`, `scripts/verify_observe_mode.py`, `scripts/ss0006_p4_canary.py`, `scripts/verify_credentials.py` (User-Agent, unredirected headers), `scripts/post_close_scorecard.py` (shadow block; RV append stopped), `scripts/config_truth_recon.py` (`CLOSED_INSTRUMENTS`), `main.py` (boot log sources).
  - Tests: `tests/unit/test_shadow_benchmark_tracker.py`, `test_ss0006_p4_canary.py`, `test_config_truth_recon.py`.
  - Registry mirror.
- **Research repo.** This doc; `TARGET.md` §0a and the SPY correction; `ATTEMPTS_LEDGER.md` (rows 17, 31, 39–43); `304b` (banner, §1.4, §2.4, §3.2, F-12, P-3/P-4); the 305 correction markers; `design_queue.json`; `seed_stepping_stones.py` and `stepping_stones.jsonl` (2 stones re-keyed, nothing else changed); `trial_registry.jsonl`.
- **Workflow artifacts** (scratch `doc306/`): `shadow_benchmark/`, `verify_tracker/`, `p3/`, `verify_p3/`, `ex99/`, `p4_canary/`, `triage/`, `llm/`, `verify_llm/`. Every number above traces to a script there, or to a command quoted in this doc.
- **Config.** `~/momentum-x-secrets.env`: `LLM_TIER1_MODEL`/`LLM_TIER2_MODEL` → Llama-3.3-70B-Instruct-Turbo (backup `.bak-doc306`). Quarantine layers untouched: `EXEC_HALT_NEW_ENTRIES=true`, `data/HALT_NEW_ENTRIES`, `EXEC_MAX_POSITIONS=8`, lottery/fader halts.
