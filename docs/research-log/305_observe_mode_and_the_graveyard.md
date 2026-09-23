# 305 — Observe Mode, a Closed Graveyard, and What the Cash Is Actually Worth

**Date:** 2026-09-22 (evening)
**Status:** The quarantine is in **observe mode** (mode B). **Three trials are closed**: rocket-gate
T00027 (NOT PASSED), SEVP T00029 and Kalshi T00028. SS0006's zero-cost prerequisites were run and
**none kills 304b**. **Trial 34 was not consumed; the registry stays at 33.** Credential rotation and
Together billing are Pierce's actions: a checker is built and waiting.
**Method:** one workflow (a blind conformance audit before any outcome was read, then the frozen
acceptance test, an independent replication, two skeptics per result and a completeness critic),
plus inline changes to config, registry and ledger. Every agent was barred from reading credentials;
authenticated calls went through a helper that never exposes a key.

---

## 0. The answer

| deliverable | result |
|---|---|
| **1. Security and state** | Rotation: **Pierce's action**. `scripts/verify_credentials.py` confirms it without printing a value. Baseline: Alpaca, Polygon and Finnhub return 200. ~~Together returns 403 on every endpoint, including the free model listing, so the problem is the key or the account, not one endpoint.~~ **[Corrected in doc 306: the 403 was my checker's fault, not the key.** Together sits behind Cloudflare, which answers 403 to urllib's default `Python-urllib` User-Agent. With any normal User-Agent, `/v1/models` returns 200 and chat completions return **402 `credit_limit`**. The key works. The account is out of credit.**]** The secrets file is cleaned, with every effective value unchanged. `TARGET.md` §2 is amended. **Mode B is live and tested.** |
| **2. Rocket-gate T00027** | **NOT PASSED** at its frozen n ≥ 30 evaluation point: n=36, CI [−$11,084, +$7,582] per session, halves +$60,053 / −$133,205. The forward interval **excludes the +$21,912/session it was mined on**. Replicated from the frozen prose; five sessions re-priced from raw bars to the cent. Closed, `REFUTED_BY_NATURE` |
| **3. Graveyard** | SEVP → `INSTRUMENT_LIMITED`. Kalshi → `REFUTED_BY_NATURE`: LLM Brier worse by +0.135, CI [+0.114, +0.158], n=793. Kalshi task disabled. The design queue is down to 3 live designs |
| **4. SS0006 P-4/5/6** | **P-6 done:** Alpaca's easy-to-borrow fee is **0.00%**, so the freeze stays at 1.2127% (4.8 bps/ticket). **P-5, retrospective parts:** nominal pass, but the median spread at entry is **20.3 bps, ~2× the draft's cost assumption**, and **Rule 201 blocks 31%**. **P-4 blocked** (Together ~~403~~ **402 credit_limit**; doc 306). Nothing kills 304b |
| **5. Treasury** | **The premise needs two corrections.** Paper cash cannot earn yield, because Alpaca paper pays no interest and simulates no dividends. T-bills yield **~3.7% (1.45 bps/day), not 5%**. On total return, an 80/20 SPY/BIL core cleared 5 bps/day over 2016-2026 by 0.16 bps/day, with a −27.5% drawdown, and failed on 2007-2026 (§5) |

---

## 1. Deliverable 1 — security and state

### 1a. What rotation needs, and why I did not do it

Issuing new keys means signing into each provider and handling live credentials. That is Pierce's to
do, and after tonight's leak (doc 304) there is no appetite for an agent touching key values. What is
built instead:

* **`scripts/verify_credentials.py`** reads the secrets file once and passes values only to request
  headers. Every failure reports an HTTP status or an exception *class name*, never a message. It
  checks hygiene (duplicates, inline comments, quoting, empty required keys), makes one minimal
  read-only request per credential, optionally sends 1-token LLM probes, checks the ACL, and scans for
  leaks. **After rotating, run it; exit code 0 means every key works.**
* **Pre-rotation baseline (20:50 ET):**

| check | result |
|---|---|
| Alpaca trading `GET /v2/account` | 200 |
| Alpaca data, latest SPY quote | 200 |
| Polygon `GET /v3/reference/tickers` | 200 |
| Finnhub `GET /quote` | 200 |
| **Together `GET /v1/models`** | ~~403~~ **200 with a normal User-Agent** (doc 306) |
| Together 1-token chat: Qwen3-235B-tput / Qwen3.5-397B / gpt-oss-120b | ~~403 / 403 / 403~~ **402 `credit_limit`** on Qwen3-235B-tput and Llama-3.3-70B (doc 306) |
| secrets-file ACL | already restricted to you, SYSTEM and Administrators |

### 1b. Together: account-wide now, not endpoint-specific

**[Corrected in doc 306: the 403 was my checker's fault, not the key.** Together sits behind Cloudflare, which answers 403 to urllib's default `Python-urllib` User-Agent. With any normal User-Agent, `/v1/models` returns 200 and chat completions return **402 `credit_limit`**. The key works. The account is out of credit.**]** The paragraph below is kept as written; its revocation/suspension reading is wrong.

The August failures were **endpoint-specific**. The red-flag scorer's calls to the `-tput` model got
**402**, while the bot's own calls, whose last one today went to `meta-llama/Llama-3.3-70B`, kept
succeeding. Tonight **every** call returns **403**, including the free model listing, which only
needs a valid key. The key has been revoked, or the account suspended. If Pierce revoked the leaked key
after doc 304, that is the whole explanation. Otherwise the dashboard will show a suspension, and the
August 402s point to an unpaid balance. **Without a working key, tomorrow's evaluations fail. They
fail loudly:** `src/agents/base.py:266` raises a CRITICAL incident after N consecutive primary-model
rejects.

### 1c. The secrets file, cleaned without changing a value

* **27 inline comments** moved onto their own lines above their keys.
* **A shadowed duplicate removed.** `LLM_TIER1_MODEL` was defined twice (`Qwen3.5-397B-A17B`, then
  `Qwen3-235B-A22B-Instruct-2507-tput`). Both python-dotenv and the launchers always kept the **last**
  line, so tier 1 has been the `-tput` model all along, and the first line was dead. If `Qwen3.5-397B`
  was the intended tier-1 model, that is a decision for Pierce, and it has not been in effect.
* **Every effective value is hash-identical before and after**, under python-dotenv and under the
  PowerShell launcher parse (58/58). The file now passes all hygiene checks. Backup:
  `~/momentum-x-secrets.env.bak-doc305`.

### 1d. `TARGET.md` §2 amended (Pierce's written direction)

The identity now reads `0.0005 / (deployment × turns)`. Recomputing the range turned up a second,
older error. §2 said *"measured turnover is 3.9–17.2% … so the honest requirement is 25–183
bps/ticket"*, but 3.9–17.2% gives 58–256 at 0.001. The real endpoints were always **40%** (the maximum
permitted deployment) and **5.46%** (the measured effective deployment × turns; doc 297's verification
artifact measured 5.336%). The 3.9–17.2% figure is a both-legs turnover that the same artifact says
double-counts. **At 5 bps/day the requirement is 12.5–92 bps/ticket**, and one full account turn
costs 10.5 days of target, not 5.3.

### 1e. Observe mode (mode B)

`EXEC_MAX_POSITIONS=8` in the secrets file, with both D277 halts kept. What that changes and what it
does not:

* **Evaluation runs again.** With the position-manager gate open, candidates are scored, and the
  features files, VLL traces and shadow collectors resume.
* **Every order is still refused**, by the executor's pre-submission D277 guard. A new integration test
  runs the **real** bridge, position manager and executor, with only the broker mocked, at
  `max_positions=8`. For OTO and T2 entries, under both the env and the settings halt layers, nothing
  is submitted and no position opens. A control with the halt off *does* submit, so the check is not
  vacuous.
* **Each halted verdict now closes its trace** with a `BLOCKED_OPERATOR_HALT_EXEC` VLL terminal, so the
  nightly grader can join it to the journal.
* **Expect a Discord "HALT BLOCKED Entry" alert for each refused BUY**, rate-limited to one per ticker
  per minute. This is by design (doc 156).
* **Clean evaluation needs Together restored.** Every LLM tier is on Together.
* The 04:30 boot banner will read `HALT: ON (settings+file)`. Verifying it live is tomorrow's check.

---

## 2. Deliverable 2 — rocket-gate T00027 acceptance report

**Protocol.** Conformance came first, done **blind**: the runner was audited against the frozen text
(`git show 7f003f6`) without reading any outcome column. Verdict: **CONFORMANT, with non-blocking
notes**. Only then did the one permitted command run:
`python scripts/_doc284_rocket_gate_ledger.py --acceptance` (read-only).

| frozen condition | result |
|---|---|
| evaluation point, n ≥ 30 gated forward sessions | **n = 36** (2026-07-06 → 09-18) ✓ reached |
| day-blocked bootstrap (B=10,000, seed 284) 95% CI lower bound > 0 | **[−$11,084, +$7,582]** ✗ |
| both chronological halves positive | **+$60,053 / −$133,205** ✗ |
| unmeasured-ticket fraction ≤ 20% | 12.67% ✓ (not BLOCKED-COVERAGE) |
| kill horizon n = 60 | not reached, so the frozen status is FAILING-SO-FAR |

* **Effect.** Mean **−$2,032 per session** (median −$5,184; 14 of 36 positive). About **−69 bps per
  filled ticket**, with a day-blocked CI of [−377, +270] bps. The 1,318 filled rows cover only 74
  distinct ticker-sessions.
* **Mined vs forward.** The gate was built on a retrospective mean of **+$21,912 per session**. The
  forward upper bound, +$7,582, excludes it. A pass at n=60 would need the next 24 sessions to average
  about **+$21K each**, i.e. to reproduce the mined figure after 36 sessions averaging −$2K.
* **Verification.**
  - The statistic was replicated from the frozen prose, exact to the cent and to the CI (and matched
    under two other RNGs).
  - Two skeptics found nothing: one on statistical reproduction, one on sample integrity. The latter
    explained all 12 missing weekdays (Labor Day, nine bot-down days and two with zero gated rows).
  - **The critic's one gap was that everything re-aggregated stored ledger values. It is now closed.**
    Five sessions, including the −$78,173.58 one that alone makes the total negative and rows priced
    before and after the warehouse rebuild, **re-price from raw bars to the cent**.
* **Caveats, recorded.**
  - n=36 is a pre-repair snapshot. The 09-21 row predates its bars, so the frozen text's sample is n=37;
    no repair can produce a PASS through the CI leg.
  - Both skeptics computed prefix verdicts for n=30–35 that the conformance audit had advised against.
    Every prefix fails, so the extra looks change nothing, but they are looks.
  - A Tier-1 LLM swap (doc 286) landed inside the forward window and was never logged against the
    ledger (§6 of its prereg).
* **Disposition.** Pierce's directive closes a failed test now. This is a **futility stop**, recorded as
  such rather than as the frozen rule's DEAD: stopping early can forfeit a pass but cannot manufacture
  one. Closure `REFUTED_BY_NATURE`, since the claimed effect is excluded. The nightly append is stopped
  in `post_close_scorecard.py`, so the ledger is frozen at its 2026-09-22 state. As the directive asked
  (Step 3.4), **no exit edge exists to transfer.**

## 3. Deliverable 3 — the graveyard, cleaned

| trial | closure | evidence |
|---|---|---|
| **T00029 SEVP** | `INSTRUMENT_LIMITED` (Pierce's disposition) | death date passed at 205/300; collector never scheduled; naked straddle unexecutable at Alpaca; runner conditioned G2 on outcomes. No gate ever computed, so nothing is known about the effect. Deadline not slid |
| **T00028 Kalshi shadow** | `REFUTED_BY_NATURE` | its own frozen gate FAILED at n=237 and at every re-score to n=793. **Brier LLM 0.2451 vs market 0.1101: gap +0.135, forecast-day-blocked 95% CI [+0.114, +0.158]** (computed tonight; the log never printed an interval). Divergence-rule P&L −$22.58 over 631 trades. Task **disabled**, not deleted |
| **T00027 rocket-gate** | `REFUTED_BY_NATURE` (futility stop) | §2 |

All three are recorded in the ledger, the design queue (retired), the stepping-stone archive (now 28
records, 60.7% market evidence) and **both** trial-registry copies, via hash-chained state changes
(byte-identical, chain verified). **The trial count stays at 33:** closing a trial does not un-count
it. Two ledger rows had been stale since July: Kalshi's read "~2/200 resolved", the rocket-gate's
"n=3/30".

**The live design queue is now three entries:** the RV forward shadow ledger, in-universe vol-score
risk-shaping, and overnight ETF excess-of-cash. **None of them has a positive measured edge.**

## 4. Deliverable 4 — SS0006 prerequisites scorecard

| # | status | result |
|---|---|---|
| **P-4** model access | **blocked** | Together ~~403 on every endpoint~~ **402 credit_limit; the key is valid** (§1b, corrected in doc 306). MODEL_ECHO and the canaries cannot run. The directive's kill rule ("latency/errors exceed tolerance") is about the *restored* endpoint, and an unfunded or revoked account is not a property of the design, so this is not treated as a kill |
| **P-5** plumbing | **retrospective parts: nominal pass; forward part not run** | EDGAR discovery complete: EFTS matches the daily index on all 11 published days; items match the submissions JSON on 2,343/2,343. 10 off-season sessions yield 106 Item-2.02 events and **64 eligible**. **The default feed is SIP.** **SIP ±60 s at 15:30: 63/64 = 98.4%**, a pass only on the eligible denominator (all 101 mapped events: 91.1%), so that reading must be frozen. **Rule 201 in force at entry: 31.2%**, CI 21–43%, vs the 10.6% planning rate. **The frozen EX-99 regex misses 35.9%** of eligible filings, all of which carry an EX-99 |
| **P-6** borrow fee | **done** | Alpaca's published schedule (revised 2026-09-17; sha256 recorded): **easy-to-borrow borrow fee is free**. Freeze b = max(0%, 1.2127%) = **1.2127% = 4.81 bps per 10-session ticket**. The paper account does not simulate borrow; no API exposes a rate. ETB share of the eligible universe today: **92.4%** |

**Is net margin crushed below 15 bps/ticket?** Not by P-6. But **P-5's spread measurement moves the
bar**. The draft's median-ticket cost used a 10.5 bps spread; the measured median SIP quoted spread at
15:30 on eligible events is **20.3 bps** (40 of 64 above 10.5). Reconciled, the median-ticket all-in
cost is **≈ 26 bps**, so **15 bps net needs ≈ 41 bps gross**, not 31. That does not kill the design,
whose whole premise is an edge in the hundreds of bps, but P-3's power figures must be recomputed on it.

**Throughput, never compared to power until now.** 64 eligible in 10 off-season sessions leaves **43
after the Rule 201 exclusion and ~38 easy-to-borrow**, about 4 per session. Draft 304b's optimistic
power basis assumed the 2025 average of ~46 eligible per session. September 9–22 is the quietest
stretch of the earnings calendar, so the true annual rate lies between these. **P-3 has to settle it
with the real forward definition before any freeze.** Taken with doc 304's standing recommendation
not to register 304b, nothing here changes that recommendation.

## 5. Deliverable 5 — the treasury and capital blueprint

**Framing.** This is benchmark arithmetic for the *paper* program. It is not personalised financial
advice. How Pierce allocates real money is his own decision, and no one here is a licensed adviser.

### 5a. Two corrections to the premise

1. **The paper account cannot earn a yield, so its "cash drag" cannot be harvested on paper.** Alpaca
   paper pays **no interest**: `/v2/account` has no interest or yield field, and there are zero INT
   rows since 2026-02-18. Its docs also say paper **does not simulate dividends**. So a T-bill ETF held
   on paper shows ≈ **0** (BIL price-only: −0.12% over the trailing 12 months), and SPY shows its
   price return only. On paper, **an 80/20 SPY/BIL book prints 4.48 bps/day over 2016-2026, below the
   bar**. Alpaca's High-Yield Cash program ("up to 3.56% APY", ≈ 1.39 bps/day) exists only for live
   accounts.
2. **T-bills yield about 3.7%, not 5%.** Over the trailing 12 months BIL distributed 3.71% and SGOV
   3.69%, i.e. **≈ 1.45 bps/trading day**, and falling: the last three distributions annualise to ~3.6%.
   The directive's "~5% / ~2.0 bps/day" is about 40% too high.

### 5b. The numbers (total return: official closes plus cash dividends)

| | CAGR | bps/day | max drawdown | on $188,928 | worst 12 months |
|---|---|---|---|---|---|
| SPY, 2016-01 → 2026-09 | 15.30% | 6.28 | −33.7% | −$63.7K | −19.7% |
| **80/20 SPY/BIL** | **12.75%** | **5.16** | **−27.5%** | **−$51.9K** | −15.5% |
| 60/40 SPY/BIL | 10.15% | 4.06 | −21.0% | −$39.6K | −11.3% |
| BIL | 2.15% | 0.84 | −0.2% | — | — |
| SPY, 2007-01 → 2026-09 | **11.04%** | **4.92** | **−55.2%** | −$104.3K | −47.4% |
| 80/20, GFC era | — | — | −46.7% | −$88.2K | — |
| trailing 12 months: SPY / 80/20 / 60/40 | +17.8% / +15.0% / +12.1% | 6.86 / 5.77 / 4.68 | −8.9% / −7.0% / −5.1% | | |

**What this says about the target.**

* Over 2016-2026, a hindsight window containing one of the strongest decades on record, an unlevered
  80/20 core cleared 5 bps/day by **0.16 bps/day** while taking a **−27.5%** drawdown.
* **On 2007-2026, even 100% SPY misses the target** (4.92 bps/day) with a −55% drawdown.
* The minimum SPY weight to reach the bar with a zero-correlation satellite adding Y bps/day, on
  2016-2026 (in-sample, so these are optimistic):

| satellite Y | min SPY weight | max drawdown |
|---|---|---|
| 0 bps/day | 78–80% | −27% |
| 1 bps/day | 59% | −20.5% |
| 2.3 bps/day (304b's full-pass arithmetic) | 32–35% | −11 to −12% |

* **On 2007-2026, Y = 0 needs 102% SPY**, which is unreachable unlevered.

**The target is ambiguous.** 5.00 bps/day arithmetic and 12.7%/yr CAGR differ (78% vs 80% SPY at
Y = 0). Pierce should pick one.

### 5c. A core-plus-satellite blueprint, and what stands in its way

The structure the directive proposes is the right shape for the *program*. It measures any alpha
sleeve against a passive core rather than asking a standalone alpha to carry 5 bps/day. On the numbers
above, a dollar-neutral satellite that is real is worth about **1 bp/day of target relief per bp of
return**. Five concrete things stand between the idea and the book:

1. **A shadow benchmark ledger first.** Paper cannot hold a total-return core truthfully (§5a). The
   honest measurement is a daily shadow ledger that marks an X/(100−X) SPY/BIL core from official
   closes and credits distributions from Polygon cash amounts. The program's P&L is then reported
   **against that line, not against cash**. This costs $0 and needs no order path.
2. **An order path would need its own kill switch and its own family.** A passive core is not the
   quarantined gapper family, so rule 1 does not forbid it. But it would be the first new
   order-capable path since the quarantine. It needs its own D277-style halt, an AST chokepoint test,
   and a disposition in `QUARANTINE.md`.
3. **The EOD failsafe would sell it every night.** `src/monitoring/eod_failsafes.py:204-211` exempts only
   positions the bot tracks internally, so a passive SPY/BIL position would be force-closed at EOD.
4. **The frozen 5% position cap (TARGET.md §4.1) forbids an 80% position.** Changing it is Pierce's
   written decision, as is any leverage or margin.
5. **Real-money allocation is outside this program.** High-Yield Cash, T-bills and an index core in a
   live account are Pierce's personal decisions.

**What I would do, as the program's engineer:** build item 1, the shadow benchmark ledger, and report
every future result against it. Leave 2–5 to Pierce. Treat Y = 0 as the base case, because the program
has zero certified edges and doc 304 recommends not registering the only candidate that would produce
a satellite.

## 6. Corrections found tonight

| where | was | now |
|---|---|---|
| **doc 303 cost table** (`true_nbbo_cost.py`) | multi-symbol quote requests capped at 1,000 and never paged, so later-alphabet symbols vanished in busy seconds. SPY appeared in 3 of 19 sessions and TLT/XBI/XLF in 1. This is the "uneven n" doc 303 noticed without explaining | **fixed**: one symbol per request. Re-measurement below |
| `TARGET.md` §2 | 0.001; "25–183 bps/ticket" justified by the wrong turnover measure | 0.0005; 12.5–92 bps/ticket; provenance corrected (§1d) |
| `TARGET.md` (paper fees) | "the paper account never charges the SEC fee" | contradicted by 10 REG fee rows since 2026-08-05. **Flagged; Pierce amends** |
| secrets file | duplicate `LLM_TIER1_MODEL`, the first line dead | removed (§1c) |
| ledger | Kalshi "~2/200", rocket-gate "n=3/30", both stale since July | closed rows (§3) |
| doc-298 M2 baseline | worst days from the warehouse `daily_2016` close | that close deviates from the official close by up to 287 bps (2020-03-12). SPY's worst day is −10.94%, not −10.78%; VT15's is −5.16%, not −4.70%. The CAGR effect is ≈0.10%/yr |
| warehouse | — | `reference/splits.parquet` lacks BIL's 1-for-2 reverse split (2017-11-30); `day_aggs` is missing 2026-08-28 and lags one session |

**Re-measured cost floors (one symbol per request, the same 20 sessions as doc 303):**

| tier | 09:45 | 10:30 | 11:30 | 13:00 | 14:30 | 15:30 | 15:50 | close vs 14:30 | n (old → new) |
|---|---|---|---|---|---|---|---|---|---|
| index ETFs (9 names) | 1.223 | 1.099 | 1.160 | 1.031 | 0.996 | 0.945 | **1.010** | 1.01× | 576 → 1189 |
| mega caps | 2.344 | 1.772 | 1.516 | 1.227 | 1.182 | 0.950 | **0.918** | 0.78× | 713 → 789 |
| large caps | 5.519 | 2.807 | 2.449 | 1.958 | 1.838 | 1.840 | **1.825** | 0.99× | 753 → 775 |
| mid-liquid | 7.590 | 7.318 | 7.361 | 7.265 | 7.388 | 7.388 | **7.399** | 1.00× | 747 → 749 |
| low-priced | 22.346 | 22.198 | 21.299 | 23.068 | 22.232 | 22.805 | **22.962** | 1.03× | 682 → 683 |

* **Truncation hit the index-ETF tier almost alone.** Its observations doubled (576 → 1,189 of 1,197
  possible); the stock tiers gained 0.1–11%. Single stocks rarely print 1,000 quotes in two seconds,
  while nine index ETFs together routinely do.
* **Per-ticker medians barely move** (SPY 0.261 → 0.261; SOXX moves most, 1.26 → 1.69, now on 133
  observations instead of 40). **The primary-tier all-in cost is 0.575 → 0.576**, so doc 303's LETF result
  is untouched.
* **The pooled by-time index row was the artifact**: its 15:30 cell rises 0.559 → 0.945 and its close
  premium falls 1.49× → 1.01×. That cell had also become draft 304b's index-ETF cost floor. The floor
  should be the hedge instrument's own spread (IWM measured 0.34–0.36 bps at 15:30 by P-5), not a pooled
  basket.
* **The pattern doc 303 warned about, "a pooled intraday median has no referent", was present in doc
  303's own pooled row.** Corrected in `TARGET.md` §2b and marked in doc 303.

## 7. Decisions that are Pierce's

1. **Rotate** the Alpaca paper pair and the Together, Finnhub and Polygon keys, then run
   `python scripts/verify_credentials.py`.
2. **Together:** ~~restore the account or key. The 403 is account- or key-wide.~~ Add credit: the key is valid and the account is out of credit (doc 306).
3. **Tier-1 model:** confirm the `-tput` model is intended (§1c).
4. **Target definition:** 5.00 bps/day arithmetic, or 12.7%/yr CAGR (§5b).
5. **Benchmark:** approve the shadow SPY/BIL benchmark ledger (§5c item 1). Anything with an order path
   (items 2–4) needs written decisions on the kill switch, the EOD exemption and the 5% cap.
6. **304b:** it stays unregistered. If it is ever frozen: the eligible-only NBBO denominator, the
   1-minute SSR flag, the EX-99 regex, and a P-3 recomputed on 20 bps spreads and the real event rate.
7. **`TARGET.md` paper-fee line** (§6).

## 8. Open

* The forward 10-session P-5 dry run (spec in the workflow output; not scheduled).
* The private repo's 81 pre-existing test failures.
* The main-bot boot banner, verified offline; the live 04:30 check is tomorrow.
