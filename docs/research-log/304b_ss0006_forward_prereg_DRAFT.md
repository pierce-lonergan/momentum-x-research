# 304b - SS0006 forward fader-detection: pre-registration DRAFT (NOT FROZEN, no trial registered)

**Author**: Claude (drafting subagent, doc-304 workflow). Revision 1 applies four adversarial reviews: executable-prereg, provenance, validity and directive. | **Date**: 2026-09-22 | **Class**: PREREG DRAFT. Nothing below is frozen, hashed or registered. | **Parent**: doc 304 §4, "GO → pre-registration (DRAFT 304b)" (304_stop_the_bleed.md:338) | **Lineage**: doc 259 (staged plan; acceptance-anchor rule 259:45) → doc 260 (Stage-B text probe) → doc 301 (re-filed UNDERPOWERED, not closed) → doc 304 §3c-3d (the borrow wall was asserted, never measured; the anchor has look-ahead) → this draft.

> **STATUS: DRAFT (NOT FROZEN, no trial registered).** Not frozen and not hashed. **Trial T00034 has not been consumed. The registry stays at 33 trials** (`trial_count()` returned 33 this session). No LLM spend has been made and no task has been scheduled. Nothing may be touched except the §13 prerequisites, which compute no effect statistic. **If this draft is not frozen by 2026-11-30 it is VOID and is never registered.**
>
> Once frozen, every number below is fixed before the first forward session. **A gate that fails is a success, because it closes the door cheaply. Re-tuning any frozen constant automatically kills the gate** (284_rocket_gate_prereg.md:5; 302_letf_prereg_DRAFT.md §6.4). A different model, prompt, anchor, horizon, tranche rule, cost line or gate is a different pre-registration. It needs its own freeze, hash, trial number and forward window.
>
> **What revision 1 changes.** The hypothesis direction, primary endpoint, horizon, model and prompt are unchanged. The revision:
> - adds a frozen SEC Rule 201 exclusion;
> - makes the tranche windows as-of, persisted and replayed;
> - removes every exit-side exclusion;
> - completes the verdict function;
> - pins the statistics.
>
> The Review log at the end lists each blocking finding and how it was resolved.

**Citation key.**
- **P** = `momentum-x/scripts/stage_b_text_probe_doc260.py`. **E** = `momentum-x/scripts/rocket_watchlist_engine_doc255.py`. P/E line numbers are at private-repo HEAD `0b05ef1`.
- `ATTEMPTS_LEDGER.md` and `TARGET.md` lines use research-repo HEAD `6b8ffc3` numbering unless marked "working tree".
- **Doc 304 is untracked** in the research repo (`git status`: `?? docs/research-log/304_stop_the_bleed.md`). Its line numbers are as read at revision time (420 lines). They moved about 56 lines while the first draft was being written, so they must be re-pinned to a committed hash before freeze (P-9).
- **`trial_registry`** means the research-repo copy `scripts/trial_registry.py` at `6b8ffc3`. It carries the doc-300 `periods_per_year` fix, and `promote()` at :254 refuses by design. The private copy (`promote()` at :238) gives the same bars and the same count, 33, today. The research copy is the one named for G1a.
- The drafter and the reviser **did not open doc 260**, for blindness. Every doc-260 line reference is as reported by the evidence packs.
- "Computed this session" means a script in scratch `p304b/` (the drafter's) or `p304c/` (the reviser's). "Reproduced this session" means a reviewer's script was re-run by the reviser.

---

## §0 The hypothesis and the primary endpoint

**H1 (one sentence).** Take Form 8-K filings carrying Item 2.02 that the SEC accepts after the freeze, and keep the stocks that the frozen Qwen3-235B protocol (§3) places in its real-time bottom tercile (§4). Short each one at scheduled close − 30 min (15:30 ET, or 12:30 on a 13:00 early close) on the first regular session whose open follows SEC acceptance, but only if at that instant Alpaca flags it easy-to-borrow and no Rule 201 short-sale restriction is in force (§2.3). Hold it for ten sessions against an equal-notional IWM long. H1 states that these tickets have a positive primary endpoint.

**PRIMARY ENDPOINT** (defined here and nowhere else): *the mean, over all gate-sample tickets S (§6), of the per-ticket net return r_net = g − c, per $1 of short notional. Here g is the IWM-hedged short return from the entry instant to the exit instant (§5.3), and c is the frozen per-ticket cost (§5.4).*

Every other statistic in this document is one of three things:
- a gate on the primary endpoint;
- a secondary contrast whose role is stated in §6;
- a diagnostic with no acceptance role.

Nothing in the design reads `risk_aversion_lambda`, MFCS, or any other bot scoring.

**Registrability arithmetic, stated up front as the directive requires.**
- On the 2025 forward proxy, the universe's unconditional IWM-hedged short is **−26 bps gross** per ticket (§7.1).
- For the tranche's mean gross to reach the directive's **20 bps**, it must beat the universe by **≥ 46 bps**.
- For a net of **15 bps** at the 16.4 bps median-ticket cost (§5.4), it must beat the universe by **≥ 57 bps**. The gap needed is larger if the mean ticket costs more than the median ticket, which is likely: 7.8% of proxy events are priced under $5 and 37.8% have ADV20 below $14M (computed this session).
- To reach the promotion bar *on average*, the net must be 95 bps (basis A) or 199 bps (basis B). That is a gap of about **137 or 241 bps** (§7.1).
- The only evidence of any gap is doc 260's ~240 bps. That figure is a median, unhedged and gross, and it is an upper bound (304:326). For a short, a squeeze tail makes the mean lower than the median.
- Pierce's registration call is made against these numbers.

---

## §1 Why a forward test, and what doc 260 found

### 1.1 What doc 260 found

These are read through the adjudicating documents, not from the ledger row.

- **Instrument.** 400 sampled events per seed. 290 (seed 7) and 278 (seed 11) were scored (260:13). The model was `Qwen/Qwen3-235B-A22B-Instruct-2507-tput` on Together (P:26).
- **Short leg.**
  - The bottom ("fader") tranche had a median f10 of **−3.44% on seed 7** and **−3.5% on seed 11**. The universe was −1.13% and −1.1% (task brief; 260:21-22).
  - n was about 55-93 per tranche (260:16, 26). The long-short CI crosses zero: seed 7 gives [−0.8, +5.1] (260:16).
  - The long leg is dead (260:20).
- **Gates (260:23, as reported).** Seed 7: self, beats-reaction and winsor all pass. Seed 11: self passes, **beats-reaction fails**, winsor passes.
- **What "beats-reaction" tested** (P:229, read this session). `g_vs` requires the LLM **long-short** median f10 (`ls10`) to exceed the announcement-reaction long-short `ls10` in each of 2024 and 2025. The reaction sort is the anchor-day close-to-close return (P:69).
  - This is a top-minus-bottom spread that includes the dead long leg.
  - **Its failure shows that the LLM's long-short spread did not beat the reaction's on seed 11. It does not show that the short leg lacks an edge over reaction.**
- **What "out-of-sample" means in doc 260.** Seed 11 is a disjoint random resample of the *same* 2024-25 events (P:83-86), not a later period. The phrase is used only in that sense below.
- **Status.** "UNDERPOWERED, not closed (doc 301)" (ATTEMPTS_LEDGER.md:17, working tree). HEAD still reads "gate fails" under CLOSED.
- **Doc 260 names this exact test** as "the only forward-validatable morsel … immune to look-ahead & lucky-sample" (quoted at 304:350).

### 1.2 Four defects that only a forward test removes

1. **Anchor look-ahead.**
   - The doc-260 anchor is the day with the highest dollar volume in (end+10d, end+80d] (P:63-65). That choice uses future volume.
   - On a knowable first-spike anchor, the universe median f10 moves from −1.74% to −0.40% (task brief; replicated at −1.73% and −0.39%, `ss0006_params_output.txt` §2).
   - Replaying P:123-130 against EDGAR:
     - 15.1% and 14.3% of the matched 8-Ks were filed *after* the anchor day;
     - 19.9% and 18.6% were accepted at or after 16:00 ET on or after the anchor day. Their text could not be known at the entry close (evidence pack 1, `match_audit.py`).
   - Doc 260 claimed "no look-ahead by construction" (260:11) and conceded the leak only as "a leak can only inflate" (260:28). Doc 259:45 had already required the anchor to be `acceptance_datetime`, with the drift window starting the *next* session.
2. **Text contamination.**
   - Only 71.5% and 73.9% of the texts fed to the model carried Item 2.02 (evidence pack 1).
   - Unconditional median f10:
     - non-2.02 texts: −3.41% (n=156), against −0.97% for Item 2.02 (n=415);
     - 8-Ks filed after the anchor: −2.99% (n=84), against −0.99% for those filed on or before it (n=487).
   - These subsets fade about as much as the fader tranche did. Whether that tranche was enriched for them cannot be tested blind. This design removes both by construction (§2.1).
3. **Memorisation.**
   - The training cutoff of Qwen3-235B-A22B-Instruct-2507 is **NOT FOUND**. `KnowledgeCutoffRegistry` has no Qwen3 entry (src/core/llm_leakage.py:67-93), and `get_cutoff()` returns None, which gives a "CONTAMINATED" flag (:209-219).
   - The forward design does not need the cutoff date. Every gate-sample filing is accepted after the freeze, and this checkpoint was already being served under this id by June 2026 (doc 260's runs; `stage_b_run.log` line 4). Weights that were served before a document existed cannot have been trained on it.
   - The remaining risk is a silent weight swap under the same id, which §3.7 handles.
4. **Point-in-time borrow.**
   - Doc 260's "borrow wall" was "asserted, never measured" (304:310).
   - 80.7% of eligible stage-B names are easy-to-borrow (87.6% event-weighted). These are paper-account flags, indicative only, and live permissions are unverified (304:308-309). The paper key gets HTTP 401 on the live host (297:270-271).
   - Only 34 of 1,447 stage-B tickers overlap the gapper borrow data behind the doc-284 tombstone (304:307), which 297:274-275 scopes to the gapper universe.
   - **The old borrow evidence is further weakened by an order bug.**
     - Doc 304:82-83 says the three 2026 live fader shorts (FBRX 07-09, HUIZ 08-07, SPAI 08-14) "were rejected 422 on borrow".
     - Their logs read `{"code":40010001,"message":"invalid side"}` (logs/fader_short_2026-07-09.log:16, 2026-08-07.log:17, 2026-08-14.log:20; verified this session). The same body appears on 2026-05-26, 06-03 and 06-11.
     - `scripts/fader_short_runner.py:178` sends `"side": "sell_short"`.
     - The earlier 422s never had their causes logged (validity review). So the "0-for-94" behind `design_queue.json:43` is at least partly a malformed-order artifact.
   - No historical borrow-status source exists, so borrow status can only be observed at entry.

### 1.3 Instruments already ruled out (doc 304)

- **Listed puts.** The median round trip is 264 bps of the stock price, against about 183 bps of capture (304:295).
- **IWM or sector-ETF short.** −27.7 to −50.0 bps per ticket (304:287).
- That leaves a direct short of easy-to-borrow shares, at a median quoted spread of about 10.5 bps (304:297).

### 1.4 Declared departures from the doc-259 plan

These are Pierce's to accept.

- **Stage C was not unlocked.** 259:34 runs Stage C ("forward paper-shadow") only if Stage B clears, and Stage B did not clear. This draft runs Stage C anyway.
  - Its attribution check G2b (§6) is a **new**, short-leg-only gate (BOTTOM against BOTTOM_ρ). It does not re-test doc 260's long-short gate (P:229).
- **The fundamental-surprise baseline is dropped.** 259:32's Stage-B baseline is the numeric fundamental-surprise *top* tranche. The forward collector has no point-in-time fundamentals feed. Only the price-reaction baseline is kept (§4.3).
- **The family is CLOSED at HEAD, under two rows.**
  - ATTEMPTS_LEDGER.md:12 ("Per-name directional prediction … all horizons, all universes | no edge | 250-261, 283") covers doc 260.
  - :17 (the catalyst row) reads "gate fails" at HEAD and "UNDERPOWERED, not closed (doc 301)" in the working tree.
  - Leaving CLOSED needs "doc-289/290-class novelty plus Pierce's sign-off" (:4-5). The novelty argument offered has three parts:
    - (a) only post-freeze filings are scored, which makes the test immune to memorisation;
    - (b) point-in-time borrow and Rule 201 state are observed at entry, which no backtest can do;
    - (c) the acceptance anchor is knowable.
  - The alternative is doc 301's reading, that the row is UNDERPOWERED and not CLOSED. Pierce rules (§11).
- **Queue mechanism class.** `design_queue.json` `history` counts families under `catalyst_text` (2). This design uses that class. The eig verdict, p_cert, informativeness and toll are identical under either label (re-run this session, `p304c/fam.py`).
- **The forward population is not doc 260's.** It is Item 2.02 only, has no volume-spike requirement, uses the acceptance anchor and excludes Rule 201 names. **Doc 260's magnitude is therefore not the expected magnitude here** (§7.2).

---

## §2 Event definition and the knowable anchor

**2.1 Qualifying filing.**
- The form is exactly `8-K` (8-K/A is excluded), `items` contains `2.02`, and `acceptanceDateTime` is present.
- The filing's A must fall after the freeze commit timestamp.
- Discovery, both $0 (evidence pack 1 §7):
  - the EDGAR full-text search `search-index?forms=8-K&dateRange=custom&startdt=D₋₁&enddt=D`, paged with `from=`. Here D₋₁ is the previous business day, because a filing accepted late in the day can carry the next business day's `filingDate` (validity review);
  - per-CIK `data.sec.gov/submissions/CIK##########.json` for `acceptanceDateTime`, `items` and `primaryDocument`.
- **Canonical accession form**: `##########-##-######` (10-2-6 digits with dashes, as in the submissions JSON). EFTS ids (`accession:filename`) and dashless forms are normalised to it before any use, including the §4 hash (fixture F-7).
- A filing first discovered after its scoring deadline (§3.6) is **LATE-DISCOVERY**. It is a hole if it is eligible, or if its eligibility cannot be determined.

**2.2 From issuer to ticker.**
- Candidates are the `tickers` in the submissions JSON whose matching `exchanges` entry is a US national exchange (OTC is excluded), and which Alpaca lists as a tradable `us_equity`.
- If several qualify, take the one with the highest ADV20. Remaining ties go to the lexicographically first ticker.
- One ticket per filing.

**2.3 Eligibility.** Every input is knowable before entry. Eligibility is evaluated at the first Poll run on session D_e(i) (§9.3). **Price inputs** are the Alpaca v2 daily bars (`feed=sip`, `adjustment=raw`), field `c` for close and `v` for volume. If an input fetch fails, the event is **ELIG-UNKNOWN**, a hole that can be repaired (§6 G4).

| rule | definition | outcome if it fails |
|---|---|---|
| price | P_prev = `c` of the last regular session that ended before A. **$2.00 ≤ P_prev ≤ $500.00** (the P:81 / 259:29 universe) | excluded, counted |
| liquidity | ADV20 = mean of c × v over the 20 regular sessions strictly before D_e; **≥ $1,000,000**. Fewer than 20 sessions of history is excluded | excluded, counted |
| instrument | not SIC 6221 (commodity or crypto trusts; evidence pack 2 cleaning); not IWM | excluded, counted |
| **short-sale restriction (Rule 201)** | SSR flag at T_e (definition below) | **SSR-FLAGGED**: scored, enters both windows (§4), not traded, counted, and reported with tranche membership. Its outcome becomes diagnostic D-11 |
| one position per ticker | universe-level OVERLAP rule (below) | **OVERLAP**: scored, enters both windows, not traded, counted |
| one ticket per issuer per session | several Item-2.02 8-Ks from one CIK with the same D_e | the earliest (A, accession) only. There is **no** 14-day same-CIK de-duplication. The forward proxy used one (§7.1); P-3 uses this rule |
| text | stripped text ≥ 300 characters (P:160) | **TEXT-SHORT**, excluded, counted |

**Rule 201 (SEC Regulation SHO short-sale circuit breaker).**
- **Why a rule is needed.** Once a stock trades at or below 90% of its prior close, a short sale may not execute at or below the national best bid for the rest of that day and all of the next. §5.4 books the short entry at the bid, so the modelled fill would be illegal for restricted names.
- **Size.** Reproduced this session (`rev_validity/ssr_rate.py`, unconditional, 11,501 of 11,517 forward-proxy events; daily lows, so these are upper bounds):
  - the restriction is in force at T_e for **10.56%** of events if D_e is the filing-date session, and **22.08%** if D_e is the next session;
  - carry-in alone accounts for 1.40% and 9.45%;
  - names under $5 (n = 896): 22.3% and 39.6%;
  - ADV20 under $14M (n = 4,345): 12.3% and 26.8%.
- The fader tranche is plausibly enriched for restricted names, but that cannot be measured blind.
- Program docs do not model Rule 201 anywhere (validity review).
- **Frozen flag (a declared proxy).** SSR-FLAGGED iff either:
  - (a) min(`l`) over 1-minute bars (`feed=sip`) whose start lies in [04:00 ET, T_e) on D_e is ≤ 0.9 × `c`(D_e−1); or
  - (b) min(`l`) over 1-minute bars whose start lies in [04:00, 20:00) ET on D_e−1 is ≤ 0.9 × `c`(D_e−2).
- The inequality is inclusive. A trigger on D_e−2 does not reach D_e. The Mark task computes the flag from historical bars, so only prices before T_e enter it.
- Bars that are unavailable make the event **SSR-UNKNOWN**, a repairable hole.
- The treatment is a **defined exclusion applied identically to S, U and S_ρ**. The alternative, deferring entry to the first unrestricted session, would be a different design (§11).

**OVERLAP (universe-level).**
- Process candidates in (D_e, A, canonical accession) order.
- Event i is OVERLAP iff an earlier **U member** j on the same ticker has D_e(j) ≤ D_e(i) < D_e(j) + 10.
- The *scheduled* exit is used, which is knowable at entry. An exit and an entry at the same instant are therefore allowed.
- Because the rule is defined on U, S ⊆ U and S_ρ ⊆ U hold by construction (fixture F-32).

**2.4 The anchor A.**
- A = `acceptanceDateTime` parsed as UTC, then converted with `zoneinfo("America/New_York")`.
- Never `filingDate`, never a volume spike, never a fixed UTC offset.
- **The JSON "Z" is true UTC.** This session, 10 of 10 Item-2.02 8-Ks from AAPL, MSFT and NVDA were checked, filed 2025-11-19 to 2026-08-26 (4 in EST, 6 in EDT). In every case the converted time equals the header `ACCEPTANCE-DATETIME`.
  - That check's artifact was not saved. P-1 must save one.
  - Independent corroboration (provenance review, `x260/match_audit.csv`): the ET acceptance hours of 571 matched doc-260 8-Ks cluster at 16h (300) and 06-08h (187), with 370 at −04:00 and 201 at −05:00.
- **Robustness (validity review).** D_e's open is after A, and T_e is about 6 hours after that open. So a misparse of acceptanceDateTime by up to 5 hours in either direction cannot put the entry or the scoring deadline before the true acceptance. It can only move D_e later.

**2.5 Entry session D_e.** D_e is the first regular session, per the Alpaca `GET /v2/calendar` snapshot frozen at freeze, whose **scheduled open is strictly later than A**. This is doc 259:45 applied literally. Sessions include 13:00 early closes.

| acceptance (ET) | D_e |
|---|---|
| Tue 08:00 (pre-market) | Tue |
| Mon 16:05 (after the close) | Tue |
| Tue 12:00 (intraday) | Wed |
| Fri 21:30 | the next session (Mon unless it is a holiday) |
| Sat 10:00, or a market holiday | the next session |

**2.6 Entry instant.** T_e = **the scheduled close of D_e minus 30 minutes**: 15:30:00 ET on normal days, 12:30:00 ET on 13:00 early closes.

Why 15:30. The table pairs each stock tier with the IWM leg (index-ETF tier), using quoted spreads in bps from TARGET.md:137-141 (fees excluded).

| pair | 09:45 | 14:30 | **15:30** | 15:50 |
|---|---|---|---|---|
| mega cap + IWM | 3.310 | 1.778 | **1.503** | 1.764 |
| large cap + IWM | 6.458 | 2.416 | **2.406** | 2.683 |
| mid-liquid + IWM | 8.489 | 7.899 | **7.866** | 8.234 |
| low-priced + IWM | 23.285 | 22.658 | **22.806** | 23.720 |

- 15:30 is cheaper than 15:50 and 09:45 for every pair.
- Against 14:30:
  - 15:30 is cheaper for mega (by 0.275 bps), large (by 0.010) and mid-liquid (by 0.033);
  - the low-priced pair is cheaper at 14:30, by 0.148.
- Sampling intervals per instant are **not available**: `data/research/cost_curve_doc303/*.json` hold per-time medians only.
- The decisive facts:
  - the open is the expensive instant (large caps are 3.0× the close, TARGET.md:149-151);
  - the index-ETF leg ticks up into 15:50 (0.862, against 0.559 at 15:30).
- 15:30 also keeps most of the reaction session inside the entry price, as doc 260's close-to-close f10 did.

**2.7 Exit and horizon.**
- The scheduled exit instant X is scheduled close − 30 minutes on **session D_e + 10**, the tenth regular session after D_e. Calendar days are irrelevant.
- **h = 10 sessions.** This matches the prompt's "NEXT 10 TRADING DAYS" (P:146-151) and doc 260's f10.
- No stop, no early exit, no re-entry.
- The runner prints h and asserts h == 10. This guards against the doc-296 defect, where the spec said h=21 and the runner used h=1 (296:127-131).

**2.8 Prices.**
- **Source**: Alpaca historical quotes `GET /v2/stocks/{symbol}/quotes` with **`feed=sip` stated explicitly**. One symbol per request, paged with `next_page_token` until exhausted.
  - A multi-symbol request with `limit=1000`, as in `true_nbbo_cost.py` `probe()`, can truncate and is not used.
  - Condition codes are recorded but not filtered.
- **Admissible quote**: bid > 0 and ask > bid, with a timestamp inside **that session's scheduled regular hours** per the frozen calendar. An extended-hours quote is never a mark.
- **The mark at instant T** is the **last** quote with timestamp in [T − 5 min, T], if that quote is admissible. This is the NBBO prevailing at T.
  - This departs from `probe()`, which takes the first quote after T (true_nbbo_cost.py:129-134); the tier floors in §5.4 were measured that way.
  - If there is no such quote, or it is inadmissible, the mark is the first admissible quote in (T, T + 5 min], flagged **QUOTE-LATE**.
  - Otherwise, **MARK-MISSING**.
- ET is converted to UTC with zoneinfo, **not** with `true_nbbo_cost.py`'s hard-coded `h + 4` EDT offset (true_nbbo_cost.py:116-117), which is one hour wrong in winter (fixture F-2).
- Mid M = (bid+ask)/2. Quoted spread q = (ask−bid)/M.
- **MARK-MISSING at entry** (stock or IWM): the event is a hole. It is knowable at T_e + 5 min.
- **MARK-MISSING at X**: use the same instant on each of the next sessions, up to 5, flagged **EXIT-DEFERRED**. If all 5 fail, the exit mid is the **last admissible mid in (T_e, X + 5 sessions]**, flagged **EXIT-STALE**.
  - The booked exit session is then X + 5, so borrow accrues through it.
  - The exit spread is that of the quote used.
- **Delisted or cash-merged**: the last admissible NBBO before delisting, flagged.
- **No ticket is ever excluded for anything that happens after T_e.**
- **Intermediate daily marks** (used by the book, §5.5): a missing mark carries the last admissible mid forward, flagged **MARK-CARRIED**. It is not a hole.

**2.9 Corporate actions inside (D_e, D_x].** They are resolved only by `--unblind` (§8), so the collector never computes an in-hold price ratio.
- **(a) Sources.** Alpaca `GET /v2/corporate_actions` and Polygon reference splits and dividends, queried at unblind (session 509 ≥ every D_x + 5).
- **(b) An action recorded identically in both sources is applied.**
  - If it is recorded in only one source, or the sources disagree on ratio or ex-date, it is resolved from the issuer's EDGAR filings in (D_e − 60 calendar days, D_x] (8-K Item 3.03 or 5.03, or an exhibit announcing the split). If that confirms one reading, it is applied.
  - **Otherwise the reading worse for the short is applied.**
  - Dividends: if the sources disagree, the short owes the larger amount and the IWM long receives the smaller.
- **(c) Unrecorded suspect days.** r_t = M(t)/M(t−1) at consecutive daily marks with no recorded action at t.
  - If r_t < 1 and |r_t · k − 1| ≤ 0.03 for some k in K = {1.5, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 15, 20, 25, 30, 40, 50, 100}, the day is SPLIT-SUSPECT. The frozen set K is a declared judgement. A suspect day goes to the (b) third-source step. If unresolved, it is **adjusted as a k:1 split**, which removes the short's gain.
  - If r_t > 1, the day is **booked as genuine**, which books the short's loss, unless a reverse split is confirmed under (b).
- **(d) The short owes** dividends whose ex-date falls in (D_e, D_x]. The IWM long receives its dividends.
- **(e) No ticket is ever excluded on its own price path.** Every resolution, and every unresolved suspect, is counted and reported at unblind (D-13). The bias of these rules runs only against a PASS.

---

## §3 The frozen LLM protocol

**3.1 Provider and model.**
- Base URL `https://api.together.xyz/v1`, model **`Qwen/Qwen3-235B-A22B-Instruct-2507-tput`** (P:26).
- The model is a hard-coded, tripwired constant. P:26 (`SBMODEL`) and E:28 (`REDFLAG_MODEL`) both let an environment variable override the model; **this runner must ignore both** (fixture F-11).

**3.2 Text** (identical to doc 260):
- From the filing's `index.json`, take the first item whose name matches `ex.?99` (case-insensitive) and ends in .htm, .html or .txt. Otherwise take `primaryDocument` (P:136-138).
  - The regex misses names such as `exhibit991.htm`. That behaviour is frozen from doc 260, not fixed. P-5 counts the primary-document fallbacks.
- Strip the HTML with a byte-for-byte copy of `strip_html` (P:111-115).
- Keep the first 9,000 characters (P:140). About half of doc 260's texts hit this cap: 51.9% and 51.1% (evidence pack 1).

**3.3 Prompt.** Verbatim from P:146-151. The Python string value renders as:

```
You are a buy-side equity analyst. Below is the point-in-time earnings disclosure (SEC 8-K / press release) for {tk}, released {dt}. Based ONLY on this text - the reported results, guidance, tone, segment detail and any red/green flags an attentive analyst would weigh - predict this stock's price DRIFT over the NEXT 10 TRADING DAYS relative to the broad market. Think like you are forecasting post-earnings continuation vs fade. Respond with ONLY a compact JSON object, no prose: {{"drift":"STRONG_UP|UP|FLAT|DOWN|STRONG_DOWN","conviction":0.0-1.0,"reason":"<=12 words"}}.

--- DISCLOSURE ---
{txt}
```

- **Hash target** (it overrides the rendering above): the template value is 600 characters, and **sha256(UTF-8) = `936910538ae556eafbe70452493da2d51789e22a3f9f4cce54a72da5a6337cfe`**. It was computed this session with `ast.literal_eval` on the P:146-151 literal, without executing the module.
- Substitutions:
  - `{tk}` = the ticker;
  - **`{dt}` = the acceptance date in ET, as YYYY-MM-DD.** This is a declared deviation: doc 260 used the volume-spike day (P:164-165);
  - `{txt}` = the §3.2 text.
- The call is one user message with no system prompt. The formatted length is at most 9,600 characters (computed for a 5-character ticker).

**3.4 Decoding** (P:157-170, with declared deviations):
- temperature 0.1, max_tokens 120, 40 s timeout, concurrency ≤ 6, no seed.
- **At most 2 attempts per event in total**, across all Poll runs.
- A retry happens only on:
  - a transport exception (connect or read failure, timeout);
  - HTTP 429 or 5xx;
  - a response with no `\{.*\}` match.
- There is **no retry** on:
  - any other HTTP 4xx, which becomes **LLM-ERROR**, a hole;
  - a matched object that fails `json.loads` or validation, which becomes **UNPARSED**, a hole.
- P:162-171 retries on any exception, including a JSON error. That is a declared deviation.

The output is non-deterministic. The permanent record is the raw response, its sha256, the UTC timestamp, the response `model` field and token usage. **No event is ever re-scored.**

**3.5 Output scale and parsing.**
- Take the first match of `\{.*\}` (re.S) and pass it to `json.loads`.
- `drift` must be a JSON string. Upper-cased, it must be in DMAP = {STRONG_UP: 2, UP: 1, FLAT: 0, DOWN: −1, STRONG_DOWN: −2} (P:152).
- `conviction` must be a JSON number (int or float, **not** a bool and **not** a string), finite, in [0, 1].
- **s = DMAP[drift] × conviction ∈ [−2, 2].**
- Declared deviation: a missing or invalid drift or conviction is **UNPARSED**, a hole. Doc 260 defaulted to FLAT and 0.5 instead, and its `float()` accepted `"0.8"` and `true` (P:169-170).
- Why: a silent default is how the Kalshi shadow failed. It wrote 0 forecasts, with 0 tokens, while the task reported success (evidence pack 3 §7a).

**3.6 Deadline.** The score **and the LLM-tranche record (§4.2)** must be in the ledger by **T_e − 30 min** (15:00 ET normally, 12:00 on early closes). Otherwise the event is **LATE-SCORE**, a hole. Scoring happens only on session D_e(i) (§9.3).

**3.7 A model change is a new trial.**
- **MODEL_ECHO** is the exact response `model` string observed at P-4. It is frozen, because the response may not echo the request id verbatim.
- Any of these ends the trial as **UNRESOLVED-INSTRUMENT**:
  - the provider retires the id, or returns errors on ≥ 5 consecutive sessions that have events, and service is not restored within 10 sessions;
  - the response `model` field differs from MODEL_ECHO on ≥ 3 sessions;
  - Pierce rules that a canary drift is a model change.
- A substitute (another model, another host of the same open weights, another quantisation) needs its own pre-registration, trial number and forward window. **There is no mid-trial substitution.**
- **The canary** runs weekly: 5 frozen **synthetic** disclosures, each scored 3 times at the frozen settings.
  - A disclosure's modal label is the label drawn at least twice. If all 3 draws differ, there is no mode, and that counts as a difference.
  - If ≥ 3 of the 5 modal labels differ from those recorded at freeze, the collector PAGEs and PAUSEs.
  - Only Pierce lifts a PAUSE, with a written ruling recorded in the ledger: either "model change", which ends the trial as UNRESOLVED-INSTRUMENT, or "no change", which resumes collection.
  - Events that arrive while paused are holes.

**3.8 Spend (upper bound only).**
- At most 9,600 input tokens (a ceiling of one token per character; no tokenizer was run) at $0.20 per Mtok, plus 120 output tokens at $0.60 per Mtok (src/model_arena/catalog.yaml:62-64). That is **≤ $0.001992 per attempt**.
- At the 2025 forward-proxy rate (§7.1, 11,517 events per year): ≤ $22.94 per year for one attempt each, **≤ $45.88 per year** in the worst case of two attempts every time. Canary spend is negligible next to that.
- The doc-255 red-flag scorer's Together calls have returned HTTP 402 ("payment required") since 2026-08-04. Doc 304 notes that the main bot's LLM path shows only sporadic 402s, and it leaves the cause undetermined (304:198-200).
- P-4 diagnoses whether the key, the account or the endpoint is at fault before any spend request. Funding is Pierce's decision.

---

## §4 A tranche rule that is knowable in real time

### 4.1 Rejected constructions

| option | why it is rejected |
|---|---|
| doc 260's cut (per-year pooled terciles, P:177-188) | the cut points use the whole year's scores, i.e. future events |
| cross-sectional tercile within the entry session | knowable, but sessions are small. In the UK 2024-25 panel the median session had 4 entries and 22.5% had none. The forward proxy's quietest months have 150 events (about 7 per session). A within-session third is dominated by rounding, and it forces a "bottom" even on days when every score is UP |
| fixed thresholds calibrated on doc 260's score distribution | this author may not open `stage_b_scored*` or any score column. That distribution also came from a different `{dt}`, about 27% non-2.02 texts and possibly memorised outcomes |
| structural threshold (drift ∈ {DOWN, STRONG_DOWN}) | needs no tuning, but the tranche size is unknown, so power is unknown. It is kept as diagnostic D-3 |

### 4.2 Adopted rule: a randomised-PIT tercile over an as-of trailing window

- **Window members.**
  - An event j is a **window member** iff all of these hold:
    - it is eligible (§2.3);
    - it has a valid score written before its own deadline;
    - D_e(j) ≥ session 0.
  - Nothing decided at or after T_e(j) plays any part in membership: borrow, SSR, marks, exit status, corporate actions and OVERLAP are all irrelevant. Window members therefore include BURN-IN, NON-ETB, SSR-FLAGGED, OVERLAP and entry-side-hole events, as long as their score is valid.
  - No score from P-4, P-5 or any other pre-window run may seed the window.
- **The window W_i.**
  - Sort all window members with **D_e(j) < D_e(i)** by (D_e, A, canonical accession) descending, and take the first **200**. The 200 boundary may fall inside a session; the sort order decides.
  - Events in the same session never see each other.
  - Every member's deadline passed before session D_e(i) opened, so **W_i is fixed at the open of D_e(i)** and cannot change afterwards.
- **When it is computed.**
  - Only in a Poll run on session D_e(i), after event i is scored, and before the §3.6 deadline. It is never computed on an earlier session.
  - This removes processing-order dependence: an event scored "early" for a later D_e cannot see or miss same-run events.
- **The position of event i.** v_i = F_<(s_i) + u_i · F_=(s_i).
  - F_< and F_= are the shares of W_i with scores strictly below and equal to s_i (equality within 1e-9).
  - u_i = `int(sha256("304b|LLM|" + canonical_accession).hexdigest()[:16], 16) / 2**64`.
- **Membership.** **BOTTOM iff v_i < 1/3.** TOP iff v_i ≥ 2/3 (diagnostic D-1 only).
- **Persisted record, append-only, never recomputed.** Accession, s_i, u_i, |W_i|, **sha256 of W_i's canonical accessions sorted ascending and joined with "\n" (UTF-8)**, v_i, tranche, and `computed_at` (UTC). This is the doc-296 selection-provenance rule.
- **Replay guard** (the 297:297 point-in-time guard).
  - `--unblind` first replays every tranche record as-of: from the append-only ledger, using only rows whose write timestamps precede `computed_at`.
  - Any mismatch in membership, hash or v makes it **refuse before any return is computed**, and the trial is VOID (§6).
  - Membership used by every gate is the persisted one (fixture F-6b).
- **Why this works.** If the score distribution is stationary, v_i ~ U(0,1), so P(BOTTOM) = 1/3 exactly even with discrete, tied scores. Doc 260's "≤ q(1/3)" inclusion produced unequal tranches (evidence pack 1 §5).
- **Burn-in.** While W_i or W^ρ_i (§4.3) holds fewer than 200 events, event i is **BURN-IN**: scored, recorded, a window member, excluded from all gates, and counted.
  - At the 2025 forward-proxy rate (959.8 per month) burn-in takes about one week. A quiet month (150 events in June 2025) would stretch it to about six weeks.
- **Why 200 (a declared judgement).** The standard error of an empirical one-third quantile position is about √(0.2222/200) = 0.033, so the tranche share is 0.33 ± 0.03.
- **Degeneracy diagnostic.** The share of BOTTOM assignments decided by the hash (F_= mass at the cut) is reported. If it exceeds 50%, the collector reports DEGENERATE-SCORE to Pierce. There is no re-tune.

### 4.3 Reaction baseline tranche (used by G2b)

- **ρ_i = M_s(T_e(i)) / P_prev(i) − 1.** P_prev is §2.3's close of the last regular session that ended *before A*. For a Tue 12:00 acceptance, that is **Monday's** close (fixture F-29).
  - ρ is a **pre-entry** quantity: the reaction up to the entry instant. It is not an outcome.
- **Window W^ρ_i.** The first 200, by (D_e, A, canonical accession) descending, of window members with D_e(j) < D_e(i) **whose ρ record is in the ledger when i's ρ record is written**.
- **Position.** v^ρ_i uses salt `"304b|REACT|"`, and BOTTOM_ρ iff v^ρ_i < 1/3.
  - BOTTOM_ρ holds the most negative reactions: a cheap price-reaction sort, the analogue of doc 260's baseline, but applied here to the short leg only.
- **When it is computed and how it is kept.** By the first successful Mark run covering D_e(i). The record is persisted exactly like §4.2's (ρ_i, u^ρ_i, |W^ρ_i|, window hash, v^ρ_i, `computed_at`) and replayed by the same guard.
  - Every input is a price at or before T_e(i), so a late write reads no future information.
- Every ETB, SSR, OVERLAP and hole rule is identical to the LLM tranche's.

---

## §5 The traded instrument and the cost model

**5.1 Instrument.**
- A **shadow** short of shares plus an equal-notional long in IWM. **No orders are placed.** This is a paper ledger only.
- The live bot is quarantined (304:4-8, 42-49).
- **Zero capital is enforced, not just stated.**
  - The collector's Alpaca client exposes GET only and raises on POST, PATCH or DELETE.
  - An AST scan of the runner and the collector finds no order or position endpoint (fixture F-31).
  - `ss0006_fwd` is added to the doc-304 entry-site AST scan.
  - The collector does not depend on quarantine mode A or B, but whether research collection runs during the quarantine is Pierce's call (304:401-402).
- `MomentumX-FaderShort` (the doc-101 chronic-fader runner) is unrelated. Its name collides with this test's, so code uses the identifier `ss0006_fwd`.

**5.2 Easy-to-borrow check at entry.**
- `GET /v2/assets/{symbol}` on the paper host (the live host returns 401, 297:270-271). The call is stamped in **[T_e − 5 min, T_e)**: 15:25:00-15:29:59 ET on normal days, 12:25:00-12:29:59 on early closes.
- **ETB-ELIGIBLE iff `tradable` ∧ `shortable` ∧ `easy_to_borrow` are all true.**
  - Note that `fader_short_runner.py:166-168` returns *can-short = True* for a stock that is shortable but not easy-to-borrow. That function must not be reused.
- Otherwise the event is **NON-ETB**: excluded from the gate sample, counted, and reported with its tranche membership. Its outcome becomes diagnostic D-5 after unblinding.
- Hole classes:
  - a failed GET is **ETB-UNKNOWN**;
  - a snapshot stamped at or after T_e is **ETB-LATE**;
  - a snapshot stamped before T_e − 5 min is **ETB-EARLY**.
- The sha256 of the raw JSON goes into the ledger. **This cannot be backfilled.**
- **Open-position re-snapshot (diagnostic D-12).** The Entry task also snapshots every open U ticket each session. This supports a sensitivity analysis only: a forced cover at the next mark after a flip to `shortable = false`. It has no acceptance role.
  - A shadow ledger cannot otherwise see recalls or buy-ins, which cluster in squeezes.
- **Account-level check (P-10).** A read-only GET of the paper account's `shorting_enabled` and the account configuration's `no_shorting`. Live permissions stay unverified (304:308-309).

**5.3 Gross return.**
- R_s = (M_s(X_b) · k + D_s) / M_s(T_e) − 1. X_b is the booked exit instant (§2.8), k is the shares-per-original-share split factor, and D_s is the dividends per original share (§2.9).
- R_h is defined the same way for IWM.
- **g = −R_s + R_h.**

**5.4 Cost per ticket, in bps of short notional:**

**c = S_s + S_h + F_s + F_h + B_borrow**

- **S_s = ½ · max(q_s(T_e), τ) + ½ · max(q_s(X_b), τ).**
  - This is one full quoted spread per round trip (TARGET.md:113), with no midpoint fills (TARGET.md:126-127).
  - **The floor τ is a declared mapping (a judgement, not a roster rule).** The tier is set **once, at entry, from P_prev and ADV20**, and used for both legs. The price rule takes **precedence**:
    - P_prev < $5: low-priced, **22.247**;
    - else ADV20 ≥ $5,058M: mega, **0.944**;
    - else ADV20 ≥ $921M: large, **1.847**;
    - else mid-liquid, **7.307** (TARGET.md:138-141, 15:30 column).
  - The thresholds come from the evidence-pack-2 roster ranges of the 6-name `TIERS` lists (true_nbbo_cost.py:64-70). Roster prices overlap, so "< $5" is a judgement.
  - The ticket's own measured spread governs whenever it is higher. That matters because **37.8%** of forward-proxy events (computed this session) and 45.9% of UK events (evidence pack 2) have ADV20 below $14M, where no roster name gives a measured referent.
- **S_h = ½ · max(q_IWM(T_e), 0.559) + ½ · max(q_IWM(X_b), 0.559).**
  - 0.559 is the pooled 15:30 median of a 9-ETF basket that includes TLT, GDX and XBI (TARGET.md:137). IWM's own median round trip is 0.348 (303:44). The floor is therefore conservative and almost always binds.
- **Feed.** `true_nbbo_cost.py:116-120` passes no `feed`, so the tier table was measured on the account's default feed. P-5 records that feed. If it is not SIP, the five floors are re-measured with `feed=sip` by the same method before freeze.
- **Fees, from the published schedule** (TARGET.md:124-125). Never taken from paper fills, which understate fees 1.44× (TARGET.md:128-130).
  - The short entry is a sale: SEC 0.206 bps (0.0000206 × value) + TAF 1.95/P_e bps ($0.000195 per share) + CAT 0.03/P_e bps ($0.000003 per share).
  - The cover is a purchase: CAT 0.03/P_x.
  - IWM purchase: CAT 0.03/P. IWM sale: SEC 0.206 + TAF 1.95/P + CAT 0.03/P.
  - P_e and P_x are the mids at T_e and X_b.
- **Borrow = b × (sessions held)/252**, with **b = 1.2127% APR as a DECLARED STRESS VALUE, not a measurement of Alpaca.**
  - Source: the 75th percentile, taken with numpy `method='higher'` (equivalently `inverted_cdf`), of IBKR fee APRs among the 31 ticker-days (23 tickers) in `data/research/doc284/borrow_pricing.json` `ticker_day_detail` with ≥ 1M shares available.
  - The default `linear` method gives 0.9434% (3.74 bps per ticket), and `lower` or `nearest` give 0.6741%. The frozen value is the conservative one.
  - The same data give a minimum of 0.25%, a median of 0.4625% and a 90th percentile of 4.3445% (all recomputed this session). These are gapper names at a different broker.
  - The accrual convention of fee_apr/252 per session follows that artifact's `prereg.day_borrow`.
  - Result: **4.8123 bps for a 10-session hold.** Sensitivity: 0.99 bps at b = 0.25%, 17.24 bps at b = 4.3445%. An EXIT-DEFERRED or EXIT-STALE ticket pays for every session through X_b.
  - Alpaca's actual easy-to-borrow fee is **NOT MEASURED** (P-6).
- **The smallest possible per-ticket cost** is 0.944 + 0.559 + 2 × 0.206 + 4.8123 = **6.727 bps**. That is a lower bound, since TAF and CAT are omitted. So a mean net ≥ 15 already implies a mean gross ≥ 21.7. **The gross floor of G1c can never bind on its own** (computed this session).
- **Median-ticket illustration** (a computation, not a forecast): 10.5 bps own spread (304:297) + 0.559 + 0.323 in stock fees at $17.12 (the UK median price, evidence pack 2) + ≤ 0.226 in IWM fees at any IWM price ≥ $100 + 4.812 borrow ≈ **16.4 bps**. A net of ≥ 15 bps therefore needs a gross of about **≥ 31 bps** on that ticket.
- **Not modelled:**
  - market impact beyond the touch. Depth at the touch is $1.69M-$7.95M by tier (TARGET.md:117-121), against a 5% ticket of about $9.4K at the $188,927.83 quarantine-time equity (304:47);
  - locate failure after the flag;
  - recalls and buy-ins (sensitivity D-12 only);
  - Rule 201 fills, which are excluded rather than modelled (§2.3).

**5.5 The book (used for G1a).**
- **Position.** Each S ticket j holds a **static** position from its entry: n_s = 1/M_s(T_e) shares short and n_h = 1/M_h(T_e) shares of IWM long. It is **not** rebalanced.
- **Value paths.** V_s(t) = n_s · (M̃_s(t) · K_s(t) + D̃_s(t)), with V_s(e) = 1, where:
  - M̃ is the §2.8 mark at T(t), the session's scheduled close − 30 min, carried forward when MARK-CARRIED;
  - K_s(t) is the cumulative §2.9 split factor for ex-dates in (e, t];
  - D̃_s(t) is the cumulative dividend per original share, credited on its ex-date session.
  - V_h is defined likewise.
- **Daily P&L.** For each session t ∈ (e, x_b]: pnl_j(t) = −[V_s(t) − V_s(t−1)] + [V_h(t) − V_h(t−1)] − b/252.
  - Entry costs (half-spreads and entry fees) are booked on e, and exit costs on x_b.
  - The sum telescopes: Σ_t pnl_j(t) − costs = r_net,j exactly (fixture F-30).
- **The book.** B_t = Σ_j pnl_j(t) over sessions 0-503. Idle sessions are 0.
  - P&L on sessions after 503 (possible only for late deferred exits) is outside the book but inside the per-ticket endpoint.
- **n_obs = calendar sessions** (the reasoning is in evidence pack 2 §6).

**5.6 Frozen constants.** All live in one module and are tripwired by a unit test, as in the 284p:13 pattern.

| constant | value |
|---|---|
| form / item | `8-K` exactly / `2.02`; A after the freeze commit |
| discovery | EFTS `dateRange` from the previous business day to today; submissions JSON; canonical accession `##########-##-######` |
| anchor | `acceptanceDateTime` (UTC) → America/New_York via zoneinfo |
| D_e / T_e / deadline / borrow snapshot window | first session with scheduled open > A / close − 30 min / T_e − 30 min (score and LLM-tranche record) / [T_e − 5 min, T_e) |
| scoring session | D_e(i) only (Poll runs 10:15 and 13:15 ET) |
| horizon | 10 sessions; exit at close − 30 min; deferral ≤ 5 sessions, then EXIT-STALE at the last admissible mid |
| universe | P_prev $2.00-$500.00; ADV20 ≥ $1,000,000 over the 20 sessions before D_e; Alpaca daily bars, feed=sip, adjustment=raw; not SIC 6221; exchange-listed; not IWM |
| SSR (Rule 201) | 1-min bars (feed=sip): low in [04:00, T_e) of D_e ≤ 0.9 × c(D_e−1), or low in [04:00, 20:00) of D_e−1 ≤ 0.9 × c(D_e−2) → SSR-FLAGGED, excluded |
| OVERLAP | universe-level; j is open iff D_e(j) ≤ D_e(i) < D_e(j) + 10 |
| text | first `ex.?99` exhibit, else primary document; P:111-115 strip; 9,000 chars; minimum 300 |
| model / base / temperature / max_tokens / timeout / attempts / concurrency | `Qwen/Qwen3-235B-A22B-Instruct-2507-tput` / api.together.xyz/v1 / 0.1 / 120 / 40 s / 2 per event in total / 6; MODEL_ECHO from P-4 |
| retry triggers | transport error, HTTP 429 or 5xx, no `\{.*\}` match; nothing else |
| prompt sha256 | `936910538ae556eafbe70452493da2d51789e22a3f9f4cce54a72da5a6337cfe` |
| DMAP | STRONG_UP 2, UP 1, FLAT 0, DOWN −1, STRONG_DOWN −2; conviction a JSON number in [0, 1]; s = DMAP × conviction |
| windows / burn-in / salts / cut | 200 most recent by (D_e, A, accession) descending with D_e(j) < D_e(i) and D_e(j) ≥ 0 / BURN-IN while either window < 200 / `304b|LLM|`, `304b|REACT|` / v < 1/3; records persisted with window sha256 |
| hedge | IWM, equal notional at entry, static shares |
| NBBO | Alpaca v2 quotes, feed=sip, one symbol per request, paged; last admissible quote in [T − 5 min, T] within regular hours; else first admissible in (T, T + 5 min] (QUOTE-LATE); else MARK-MISSING |
| split-suspect set | K = {1.5, 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 15, 20, 25, 30, 40, 50, 100}; tolerance 3%; unresolved → reading worse for the short |
| tier floors at 15:30 (bps) | index ETF 0.559; mega 0.944; large 1.847; mid 7.307; low-priced 22.247. Precedence: P_prev < $5, then ADV20 ≥ $5,058M, then ≥ $921M; fixed at entry for both legs |
| fees | SEC 0.0000206 × value on sells; TAF $0.000195/share on sells; CAT $0.000003/share on both sides |
| borrow | max(1.2127% APR, P-6 measurement), accrued at 1/252 per session held |
| evaluation | N_EVAL = 504 book sessions (0-503); last entry at session 493; interim at 252 (entries D_e ≤ 241); unblind at session 509 |
| halves | D_e in sessions 0-246 / 247-493, shared by S, S_ρ and U |
| Sharpe / NW | Ŝ = mean(B)/std(B, ddof=1) · √252; Bartlett L = 10, weights 1 − l/11, demeaned, γ_0 divisor n, γ_l divisor n − l |
| bootstrap | moving blocks of 2 consecutive entry-week clusters keyed (iso_year, iso_week); B = 10,000; `numpy.random.default_rng(304)`, one `integers(0, K−1, size=ceil(K/2))` draw per replicate; `np.percentile(…, 5 or 95, method='linear')` |
| floors / tails / coverage | gross ≥ 20.0 bps, net ≥ 15.0 bps / favourable clip at R_s ≥ −20%; top decile < 0.60; squeeze ≤ 0.50 / holes ≤ 10% |
| bar | `promotion_threshold(0.0, n_eff, N)['OPERATIVE_BAR']`, research-repo `trial_registry` at `6b8ffc3`, periods_per_year = 252; n_eff = floor(504 / max(1, r_NW)); N = `trial_count()` immediately after registration |
| dates | VOID_BY 2026-11-30 (freeze); session 0 no later than 2026-12-15; DEATH = session 534, capped at 2029-01-31 |
| knife edge | Ŝ within ±0.25 of the bar, or the G1b lower bound within ±5 bps of 0, triggers a seed table (seeds 304-313) |

---

## §6 Gates, verdicts and the multiplicity ledger

### Window and samples

- **Session numbering.**
  - WINDOW_OPEN is session 0: the first regular session after the **COLLECTING** transition (§9.5), not after the freeze commit.
  - The book covers sessions 0-503 (N_EVAL = 504).
  - Entries are allowed on sessions 0-493, so every scheduled exit falls inside the window.
  - `--unblind` runs at session 509. By then every deferral has resolved and every §2.9 query is at least 5 sessions after D_x.
- **Candidate.** An event that meets all of these:
  - eligible (§2.3), with A after the freeze;
  - a valid LLM-tranche record and a valid ρ-tranche record;
  - not BURN-IN;
  - ETB-ELIGIBLE and not SSR-FLAGGED;
  - admissible entry marks for the stock and for IWM;
  - D_e in sessions 0-493.
- **Samples.**
  - **Universe U** = candidates that are not OVERLAP (§2.3).
  - **Gate sample S** = U ∩ BOTTOM.
  - **S_ρ** = U ∩ BOTTOM_ρ.
- **There is no exit-side condition.** Every U ticket is booked under §2.8-2.9.
- **Halves.** H1 = D_e in sessions 0-246; H2 = D_e in sessions 247-493. They are shared by S, S_ρ and U.
- **Order and ties.** Wherever an order is needed, it is (D_e, A, canonical accession) ascending.

### Statistics (frozen conventions)

- **Sharpe.** Ŝ = mean(B_t) / std(B_t, ddof = 1) × √252 over sessions 0-503, in float64. If std = 0, G1a fails.
- **Newey-West ratio.**
  - Let x_t = B_t − mean(B), with n = 504.
  - γ̂_0 = (1/n) Σ x_t², and γ̂_l = (1/(n − l)) Σ_{t=l}^{n−1} x_t x_{t−l}.
  - **r_NW = [γ̂_0 + 2 Σ_{l=1}^{10} (1 − l/11) γ̂_l] / γ̂_0.**
  - This is the convention of `p304b/acf304b.py`, which produced the 0.856 figure below.
- **Effective n.** **n_eff = floor(504 / max(1, r_NW))**, an integer.
  - `promotion_threshold` truncates n inside `expected_max_sharpe` but not in `se`. At r_NW = 1.37, an integer n_eff of 367 gives 3.1235, while the float gives 3.1219 (computed this session).
- **BAR.** `trial_registry.promotion_threshold(0.0, n_eff, N, periods_per_year=252)['OPERATIVE_BAR']`, with the value as returned (4 dp). **G1a passes iff Ŝ > BAR.**
- **Bootstrap** (G1b, G2a, and the class rules):
  - Clusters are the distinct (iso_year, iso_week) of D_e among the relevant tickets, sorted ascending: c_0 … c_{K−1}.
  - The moving blocks are (c_m, c_{m+1}) for m = 0 … K−2.
  - `rng = numpy.random.default_rng(304)`, created fresh for each CI.
  - For each of 10,000 replicates, draw `starts = rng.integers(0, K−1, size=ceil(K/2))`, concatenate the blocks, and keep the first K clusters.
  - A statistic is computed over all tickets in the kept clusters, with multiplicity.
  - Lower bound = `np.percentile(stats, 5, method='linear')`; upper bound = the 95th percentile of the same replicates.
  - If K < 2, the CI fails.
  - Why moving blocks: 10-session holds overlap neighbouring weeks, so independent week draws would give intervals that are too narrow (executable-prereg and validity reviews).
  - Requires numpy ≥ 1.22.

### G1: PRIMARY (all four required)

- **G1a, the promotion bar.** Ŝ > BAR.
  - At N = 34 and n = 504: BAR = E[max Sharpe | N, n] + 1.6449 · √(252/n) = **1.5023 + 1.6449 × 0.70711 = 2.6654**. In t units the bar is k = 3.7694. This is `promotion_threshold(0, 504, 34)['OPERATIVE_BAR']`, reproduced this session.
  - **Effective n.** The daily book sums every open position into one observation per session, which absorbs same-date clustering and the overlap of holds. So cluster_size = 1 and icc = 0.
  - Measured on the forward proxy (computed this session, 40 random-tercile draws): lag-1 autocorrelation −0.107; r_NW 0.856 on average (range 0.540-1.400).
  - n_eff can only raise the bar. For example, r_NW = 1.5 gives n_eff = 336 and BAR = 3.2644.
  - The bar is frozen with the trial count at freeze. If promotion comes later, the bar is recomputed at the then-current count, and the higher of the two governs.
  - The per-ticket convention is reported but does not bind (evidence pack 2 §6).
- **G1b, the per-ticket CI.** The one-sided 95% lower bound of the primary endpoint must be > 0. It uses the moving-block bootstrap on S's entry-week clusters.
  - Weeks are used rather than dates because the design effect is larger by week than by date in the UK panel: raw 8.69 against 3.83; IWM-adjusted and winsorised 3.75 against 2.05 (evidence pack 2).
  - An independent-week CI and a date-clustered CI are reported as diagnostic D-2.
- **G1c, the floors.** Mean g over S ≥ **20.0 bps** AND primary endpoint ≥ **15.0 bps**.
  - These are the directive's floors. They are not written in TARGET.md or the ledger (evidence pack 3). TARGET.md:221-222 only requires gross > round-trip cost.
  - The gross floor cannot bind on its own (§5.4).
  - The floors are **per 10-session ticket**, not normalised for holding period: 20 bps over 10 sessions is 2 bps per day (304:406-408). Whether that is the intended floor is Pierce's ruling (§11).
- **G1d, both halves.** The primary endpoint must be > 0 over the S tickets of H1 and over those of H2. An empty half fails. This follows the both-halves convention of 284p:42, with halves defined by session index.

### G2: ATTRIBUTION (both required for an SS0006 pass)

- **G2a, within-week contrast against the universe.**
  - For each entry week w with n_{S,w} ≥ 1, let d_w = mean r_net(S_w) − mean r_net(U_w). Then **Δ = Σ_w n_{S,w} · d_w / Σ_w n_{S,w}**.
  - Pass iff Δ > 0 and its one-sided 95% lower bound > 0.
  - The bootstrap is paired: moving blocks over U's week clusters, with Δ recomputed with multiplicities. A replicate with no S ticket is assigned Δ* = −∞.
  - Why within-week (validity review): BOTTOM is ranked against a trailing window, so it can be enriched in late-season or small reporters. A pooled S − U would credit that timing and size composition to the LLM.
  - This prevents both unconditional drift and composition from being credited to the LLM.
- **G2b, against the reaction baseline.** Mean r_net(S) − mean r_net(S_ρ) > 0 **in H1 and in H2**. An empty half fails.
  - **This is a new gate.** It is not doc 260's `g_vs`, which compared long-short spreads per calendar year (P:229), and it is not 259:32, which compared top tranches against the fundamental-surprise baseline.
  - The CI is reported but has no acceptance role.
  - Declared weakness: if the two tranches are exactly equal and the halves are independent, the chance of passing by luck is about 25%. G2b is an attribution check that sits on top of G1a. It is not a promotion statistic.

### G3: TAIL AND SQUEEZE (required)

- **G3a, the favourable-tail clip.** Recompute the primary endpoint with R_s replaced by max(R_s, −0.20), which caps the short's gain from a collapse. The adverse side is never clipped. The result must be > 0.
  - This uses the ±20% level of P:176 and 259:42. **Applying it one-sidedly is a declared departure.** 259:50 ("winsorize the surprising positive as hard as a negative") is symmetric and is not cited as support.
- **G3b, concentration.** Rank S by r_net descending, with ties by (A, accession) ascending. The top ⌈0.1·|S|⌉ tickets must carry < 60% of Σ r_net, and Σ r_net must be > 0 (259:42).
- **G3c, squeeze.** max(0, −min_j r_net,j) ≤ 0.5 × Σ r_net over S, with Σ r_net > 0. If this fails, the best verdict available is FRAGILE-PASS (_doc294_PREREG.md:33-34).
- Reported, with no acceptance role: the distribution of maximum adverse excursion at the daily marks, and the share of tickets with any mark ≥ +50%, for S against U.

### G4: COVERAGE (blocking)

- **Holes.** Every hole is decided at or before T_e + 5 min; none depends on an outcome.
  - discovery: LATE-DISCOVERY, ELIG-UNKNOWN;
  - LLM: UNPARSED, LLM-ERROR, LATE-SCORE (including a missing tranche record);
  - borrow: ETB-UNKNOWN, ETB-EARLY, ETB-LATE;
  - entry: SSR-UNKNOWN, MARK-MISSING at entry (stock or IWM);
  - collector state: events that arrived while a collector was dark or PAUSED.
- **Denominator.** All eligible events with D_e in sessions 0-493. This includes BURN-IN, NON-ETB, SSR-FLAGGED, OVERLAP and every hole, and excludes TEXT-SHORT and ineligible events. An event whose eligibility could not be determined counts in both the denominator and the numerator.
- **Repair.**
  - Holes caused by a failed fetch of fixed historical data (ELIG-UNKNOWN, SSR-UNKNOWN, entry MARK-MISSING due to a fetch failure) may be repaired by re-fetching before unblind.
  - Anything needing a point-in-time observation (discovery, scores, deadlines, borrow snapshots) is permanent.
  - Genuinely absent quotes stay holes.
- **Threshold.** If holes exceed **10%** of the denominator at unblind, the verdict is **BLOCKED-COVERAGE**. The 10% level follows 302:175 (§6.1.5) and is stricter than 284p:38's 20%.
- Holes are counted. **They are never booked as 0.**
- NON-ETB, OVERLAP, SSR-FLAGGED, TEXT-SHORT and BURN-IN are defined exclusions, not holes. EXIT-DEFERRED, EXIT-STALE, QUOTE-LATE, MARK-CARRIED and SPLIT-SUSPECT are flags on booked tickets. All are counted.

### Interim look (binding, kill-only, one bit)

- At session 252, the runner evaluates S and U tickets with D_e ≤ 241 (so D_x ≤ 251), applying §2.8-2.9 as of that run.
- It emits **only** CONTINUE or KILL-FUTILITY.
- **KILL-FUTILITY iff the primary endpoint's point estimate ≤ 0 OR Δ (G2a) ≤ 0.**
- A kill-only look cannot inflate the false-positive rate.
- On KILL-FUTILITY the trial ends, and a close-out run assigns the closure class from the interim sample. On CONTINUE nothing else is printed.
- Approximate chance of a futility kill if the true net edge is X, on basis A (§7): 8.14% at X = 50 bps, 0.26% at X = 100. This is Φ(−E[t_252]) for the primary endpoint alone, computed this session. It does not reflect the within-week Δ or the D_e ≤ 241 cut.

### Verdict decision list (ordered; the first match wins)

| # | verdict | condition | closure class and what follows |
|---|---|---|---|
| D1 | **VOID** | not frozen by 2026-11-30; session 0 later than 2026-12-15; fixture or hash failure; replay-guard mismatch (§4.2); any protocol violation found before a return is computed | before registration, no trial is consumed; after registration, VOIDED_BY_DEFECT |
| D2 | UNRESOLVED-INSTRUMENT | §3.7 fired | INSTRUMENT_LIMITED; keystone "the frozen model id served" |
| D3 | KILL-FUTILITY | the interim bit fired | class rules C1-C3 on the interim sample |
| D4 | UNRESOLVED-BY-DEATH | no REPORTED row by DEATH_DATE | ABANDONED, naming the missing keystone |
| D5 | BLOCKED-COVERAGE | G4 fails at unblind (G1-G3 computed and reported, with no role) | INSTRUMENT_LIMITED; keystone "collector coverage ≤ 10%" |
| D6 | **KILL** | G1a fails. **This is the expected outcome**: no certified edges in 60+ gated hypotheses (TARGET.md:187) | C1-C3 |
| D7 | **KILL** | G1a passes and any of G1b, G1d, G3a, G3b fails | C1-C3, naming the first failing gate in that order |
| D8 | PASS-STATISTICAL / FAIL-ARITHMETIC | G1a, G1b, G1d, G3a and G3b pass; G1c fails | not advanced; C1-C3 |
| D9 | PASS-UNATTRIBUTED | G1a-d, G3a and G3b pass; G2a or G2b fails (G3c reported) | **SS0006 is not promoted**; class UNDERPOWERED for the attribution claim, naming the failing sub-gate. "Short every Item-2.02 8-K, hedged" is a different hypothesis that needs its own prereg and trial |
| D10 | FRAGILE-PASS | G1a-d, G2a-b, G3a and G3b pass; G3c fails | fleet and Pierce; no argument for deployment |
| D11 | **PROVISIONAL-PASS** | every gate passes | the independent adversarial §A fleet reviews (296; ATTEMPTS_LEDGER.md:69-71), then Pierce. The word "certified" is banned, and `promote()` refuses by design (trial_registry.py:254). A broker-truth phase comes before any capital (declared here, in the spirit of 297:299's broker-truth-only reward) |
| D12 | KILL | anything else. It should be unreachable; F-22 asserts that | C1-C3 |

**Closure-class rules** (class names and meanings from src/epistemics/closure.py:50-120; the doc-301 rule at 301:197-199). Let m_g and m_net be the S means. UB_g and UB_net are the one-sided 95% upper bounds from the G1b replicates, with both statistics computed from each draw.
- **C1.** m_g ≤ 0 and UB_g < 20 bps → REFUTED_BY_NATURE, recording the effect and its interval. A registrable gross effect is then excluded; this operationalises "adequate power".
- **C2.** m_g > 0 and UB_net < 15 bps → REFUTED_BY_COST.
- **C3.** Otherwise → UNDERPOWERED. The missing keystone is "sample length", and the first failing gate is named.

**Knife edge.** If Ŝ is within ±0.25 of BAR, or the G1b lower bound is within ±5 bps of 0, the unblind record carries a seed-sensitivity table for seeds 304-313 (296:135-136).

**Multiplicity ledger.**
- Acceptance is **one intersection decision** over G1a-d, G2a-b, G3a-c and G4, resolved by the decision list. Alpha is not split across them.
- The interim look is kill-only and costs no alpha.
- Everything else is a diagnostic. Each is disclosed and counted in the unblind record, as are any sensitivities a skeptic runs (_doc294_PREREG.md:57-59):
  - D-1: TOP-tranche long leg
  - D-2: independent-week and date-clustered CIs
  - D-3: structural DOWN/STRONG_DOWN subset
  - D-4: unhedged and SPY-hedged variants
  - D-5: NON-ETB outcomes
  - D-6: breakdown by price and ADV tier
  - D-7: volume-spike vs non-spike events (the spike ratio is logged at entry)
  - D-8: by month
  - D-9: cost decomposition
  - D-10: maximum adverse excursion
  - D-11: SSR-FLAGGED outcomes, with entry booked at the ask
  - D-12: borrow-flip forced-cover sensitivity
  - D-13: counts of EXIT-DEFERRED, EXIT-STALE, MARK-CARRIED and SPLIT-SUSPECT, resolved and unresolved, for S against U

---

## §7 Power, priors and the price of the trial

### 7.1 Detectable effects at the promotion bar (not against zero)

Both bases measure Sharpe per bps on the same calendar-time book: equal notional, IWM-hedged, a random third of events, unconditional drift removed. The method is `ss0006_params.py` step 7b.

- **Basis A, the forward proxy (computed this session).**
  - Source: 2025 Form 8-Ks from the EDGAR full-text search, 261 weekdays with 0 failed days. 16,824 Item-2.02 accessions.
  - After the §2.3 filters and a 14-day same-CIK de-duplication (which is **not** the frozen rule; P-3 redoes this): **11,517 eligible events**, 959.8 per month (minimum 150 in June and September, maximum 1,727 in February). Median ADV20 $25.8M; 7.8% priced under $5.
  - The entry proxy is the close of the first session after the filing date; acceptance time was not fetched. 11,435 windows were usable.
  - Per-ticket hedged sd 10.73%.
  - **Sharpe per bps = 0.02791** after randomly keeping 80.7% of events to mimic the borrow filter (0.02877 without thinning). That is **2.09×** basis B.
- **Basis B, pessimistic (evidence pack 2).** The Sharpe per bps of **0.013375** comes from the 2025-only UK IWM-hedged tercile book (2.68 at 200 bps). The UK 2025 panel has 2,714 events, spike-only, limited to the vX cache.
  - The per-event hedged sd of 14.67% often quoted with it is the **2024-25 pooled** UK E1-minus-IWM figure (n = 4,156; `ss0006_params_output.txt`:28).
- **Why they differ.** Basis A has 4.2× more events and lower per-event volatility, because it has no volume-spike filter.
- **Why B is kept.** If the LLM's bottom third concentrates in high-reaction, high-volatility names, the tranche's variance moves toward B. That cannot be known blind.
- **Not reflected in either basis**, all recomputed by P-3:
  - the Rule 201 exclusion (upper bound 10.6-22.1% of events);
  - the frozen de-duplication rule;
  - acceptance-time entries;
  - moving-block CIs;
  - the within-week G2a.

Net edge per ticket (bps) needed to reach the bar on average (X50) and for 80% power on G1a alone (X80), with k = 3.7694 and 80% power at E[t] = 4.611. These are untempered calculations, equivalent to eig at ω = 1.

| n (sessions) | BAR | basis A: X50 / X80 | basis B: X50 / X80 | eig MDE at the program's ω = 0.25 (Sharpe → A / B bps) |
|---|---|---|---|---|
| 252 (12 months) | 3.7694 | 135 / 165 | 282 / 345 | 5.4526 → 195 / 408 |
| 378 (18 months) | 3.0777 | 110 / 135 | 230 / 281 | not computed |
| **504 (24 months, frozen)** | **2.6654** | **95 / 117** | **199 / 244** | **3.8556 → 138 / 288** |
| 756 (36 months) | 2.1763 | 78 / 95 | 163 / 199 | 3.1481 → 113 / 235 |

The ω = 0.25 column comes from eig `mde`, re-run this session with `p304b/appraise304b.py`, divided by the Sharpe per bps. eig's ω = 1 MDE at n = 504 is 3.2605, which reproduces the X80 of 117 / 244.

- **P(G1a passes) at n = 504**, at ω = 1. This is **not** P(PROVISIONAL-PASS):
  - basis A: X = 50 → 3.6%; 75 → 20.9%; 100 → 57.1%; 125 → 87.8%; 150 → 98.4%;
  - basis B: X = 100 → 3.0%; 150 → 17.6%; 200 → 50.5%; 240 → 77.9%; 300 → 97.2%.
- **P(PROVISIONAL-PASS) is lower.** It needs the intersection of G1b-d, G2a-b and G3.
  - On basis B the null sd of the G2b contrast is about 55.2 bps per 12-month half (provenance review, `g2sim.py`).
  - A true 50 bps LLM-minus-reaction gap therefore passes both halves only about Φ(50/55.2)² ≈ **67%** of the time, and a 25 bps gap about **46%**.
- **Unhedged, for reference.** X80 at n = 504 is 410 bps on A and 504 bps on B. This is why the design is a hedged pair.
- **G2a and G1b are not the binding gates.** On basis A (independent-week bootstrap, pooled contrast), the 12-month sd is 15.9 bps for the tranche-minus-universe contrast and 29.7 bps for the tranche level. At 24 months that puts the G2a 80%-power effect at about 28 bps and the G1b threshold at about 35 bps. On basis B the 12-month sds are 33.0 and 73.0 bps. **G1a binds.**

### 7.2 Doc 260's point estimate, stated honestly

- The seed-11 gap was about 240 bps: −3.5% against −1.1%.
- Those figures are medians, unhedged and gross. They were measured on the argmax anchor, with about 27% non-2.02 texts, about 15% post-anchor filings, and possible memorisation. Doc 304:326 already calls −3.44% / −3.5% an upper bound.
- **Arithmetic illustration only, not a forecast. It treats medians as means.**
  - Suppose a 240 bps tranche-minus-universe gap existed in this universe. The primary endpoint would be about −26 (the forward-proxy universe's unconditional hedged short, 2025) + 240 − 16 (cost) ≈ **198 bps**.
  - P(G1a) at 24 months: 100.0% on basis A, **49.0% on basis B**. P(PROVISIONAL-PASS) is lower (§7.1).
  - At half that size (about 78 bps): P(G1a) = 24.5% on A, 1.1% on B.
- **Honest statement:**
  - The design **cannot certify a net edge below about 95 bps (A) or 199 bps (B) in 24 months**. At the program's ω = 0.25 the figures are 138 and 288.
  - It certifies doc 260's point estimate in 24 months only if three things hold: that estimate survives the clean anchor, it survives the Item-2.02 and Rule 201 restrictions at close to full size, and the tranche is not much more volatile than a random third.
  - **In 12 months it cannot certify the doc-260 point estimate on basis B at all**, because 345 > ~198.

### 7.3 Declared priors and the price of the trial

`eig.appraise` was run this session with n_obs 504 (and 252 and 756), 252 periods per year, cluster_size 1, icc 0, 33 registered trials, family `catalyst_text` or `llm_filing_text` (identical results), and **no keystones declared**.

| prior (mean, sd) | n | ω | verdict | p_cert | informativeness | EIG (nats) |
|---|---|---|---|---|---|---|
| (0, 0.5), module default | 504 | 0.25 | **UNDISCRIMINATING** | 0.0378 | 1.271 | 0.0589 |
| (0, 0.5), module default | 252 | 0.25 | CEREMONIAL | 0.0337 | 1.135 | 0.0303 |
| **(0, 1.0), proposed** | 504 | 0.25 | **ADMISSIBLE** | 0.0619 | 2.082 | 0.2027 |
| (0, 1.0) | 252 | 0.25 | ADMISSIBLE | 0.0459 | 1.544 | 0.1116 |
| (0, 1.0) | 756 | 0.25 | ADMISSIBLE | 0.0771 | 2.594 | 0.2798 |
| (0.5, 1.0) | 504 | 0.25 | ADMISSIBLE | 0.1056 | 3.552 | 0.2027 |
| (1.0, 1.0) | 504 | 0.25 | ADMISSIBLE | 0.1681 | 5.655 | 0.2027 |

- **The full grid the drafter appraised is disclosed above.** The drafter chose sd 1.0 after the module default of 0.5 came back UNDISCRIMINATING.
- **Stated plainly: under the program's default prior this design is UNDISCRIMINATING and must not be registered** (ATTEMPTS_LEDGER.md:107-108; eig.py:273).
- **The case for sd 1.0 leans on doc 260's magnitude, which this draft calls a contaminated upper bound.** On basis A a 100 bps net edge is Sharpe ≈ 2.8, and a prior with sd 0.5 puts little mass there. Widening the sd does not shift the mean.
- **Admissibility thresholds** (directive review, computed): prior sd **0.678** at n = 504, **0.830** at n_eff = 336, and **0.9585** at n = 252.
  - Every queued design's prior sd is at most **0.7** (SEVP; `data/research/design_queue.json`, verified this session). sd 1.0 would be the widest prior in the queue.
- **Keystones.** The appraisal declared none. A queue entry that declared short-locate access as a required keystone would come back INFEASIBLE while `_excluded_short_locate_access` stands (`design_queue.json:43`). P-10 re-adjudicates it.
- Severity is 0.9703. p_false_positive is 0.0297, which does not vary with n.
- The toll is **+0.0086 Sharpe** at this design's n. On the queue's 2,690-session bar it is 1.1500 → 1.1537 (evidence pack 3). With the design queue passed in, the design stays ADMISSIBLE, with toll_p_cert 0.0032 (provenance review).
- **Pierce ratifies a prior at or above the threshold, or the draft dies.** Type-I error does not depend on the prior; registrability does.

---

## §8 Blindness

**What the collector computes and stores:**
- raw quotes and bars;
- per-event **pre-entry** quantities: s, both tranche records, ρ, the SSR flag, ETB flags, P_prev, ADV20.

It computes **no** mark-to-market after T_e and **no** in-hold price ratio. It fetches **no** corporate actions; `--unblind` does that (§2.9). ρ is the entry mid relative to the pre-acceptance close. §4.3 must assign it in real time. It is not an outcome.

**Readable before unblinding** (and at session 252, only the single futility bit):
- counts: 8-Ks polled, Item-2.02 filings, eligible events, exclusions by class (including SSR-FLAGGED), holes by class, BURN-IN, OVERLAP;
- **tranche sizes** (BOTTOM, TOP, BOTTOM_ρ). These are the only per-tranche numbers allowed; they exist to check the one-third construction. Per-tranche ρ distributions are not reported;
- borrow status (ETB, NON-ETB, UNKNOWN) and the SSR rate, as rates over **all** eligible events, not per tranche;
- LLM plumbing: tokens, spend, latency, error codes, UNPARSED count, drift-label frequencies (the degeneracy check), canary labels, the response `model` field;
- NBBO coverage and quote latency, and spread and cost aggregates over all eligible events, not per tranche;
- heartbeats, watchdog rows and clock skew.

**Forbidden before unblinding:**
- any return, mark-to-market, or gross or net P&L of any ticket, tranche, universe or the book;
- any association between score and outcome, or between reaction and outcome;
- any comparison with the bar;
- per-tranche counts of the open-position borrow re-snapshots.

**How that is enforced.**
- `--unblind` refuses to run before session 509, except for the futility bit at session 252.
- It also refuses unless the fixture passes, the hashes match the external manifest (§10 F-21), and the §4.2 replay guard passes.
- Discord posts contain **counts only**. The doc-255 engine, by contrast, posts a running lead-vs-outcome "gate" every day (run_rocket_watchlist.ps1:12-16).
- Any forbidden read is recorded in the unblind record. The fleet rules on its effect. It cannot change a constant, because re-tuning is a kill.

**Adjacent exposure hazard.**
- The doc-255/261 red-flag leg uses the same default model (E:28), a different prompt, the gapper universe, and an unregistered daily HIGH-vs-LOW "gate".
- It has been dark since 2026-07-06, with HTTP 400 and then HTTP 402 from 2026-08-04 (304:196-200).
- Restoring Together spend for 304b would revive it. The recommendation is to keep its outcome report off during the 304b window. That is Pierce's call.

---

## §9 Death date and anti-starvation

**9.1 Dates.**

| date | value |
|---|---|
| VOID_BY | 2026-11-30, for the freeze |
| COLLECT_BY | session 0 must fall on or before **2026-12-15**. That is the latest session 0 for which session 534 falls on or before 2029-01-31 (computed with a hand-written NYSE holiday list; the frozen Alpaca calendar governs). Otherwise VOID |
| WINDOW_OPEN | session 0 = the first regular session after the COLLECTING transition (§9.5) |
| INTERIM | session 252 |
| EVAL | session 504 (the book ends at 503; last entry 493) |
| UNBLIND | session 509 |
| **DEATH_DATE** | session 534 (the 30th regular session after EVAL), written as an absolute date in the external manifest from the frozen calendar; **outer bound 2029-01-31 whatever happens** |

Example (computed): with session 0 = 2026-12-01, session 246 is 2027-11-23, 252 is 2027-12-02, 493 is 2028-11-15, 503 is 2028-11-30, 509 is 2028-12-08 and 534 is 2029-01-17.

There is no extension (302 §6.3; 284p:43).

**9.2 Why this section exists.** Three collectors have already died silently.
- **SEVP.** Its death date lived only in a docstring (`_doc294_sevp_pipeline.py:3`). Its collector task was left as a "Pierce one-liner" in four consecutive docs (294:27,36; 295:41; 296:162; 297:382). Its forward ledger was never created. It was reported three weeks after its 2026-09-01 death (evidence pack 3 §6).
- **Kalshi.** The task reported success while writing 0 forecasts from 2026-08-10 onward (evidence pack 3 §7a).
- **The doc-255 red-flag leg.** Hundreds of consecutive error rows went unnoticed: HTTP 400s through July (305) and then HTTP 402s (304:198-199; evidence pack 1).

**Rule.** Doc 304 §6 rule 4 (304:386-388) says every forward collector needs a freshness check that pages when it goes dark. Here that means: health is fresh output with real content, not an exit code.

**9.3 Tasks.** Creating them is Pierce's decision. They are standalone and do not ride the bot pipeline or `post_close_scorecard`; the rocket-gate ledger rides that path and is missing 12 of 56 sessions (evidence pack 3 §7c).

| task | when (ET) | does | writes |
|---|---|---|---|
| `MomentumX-SS0006Fwd-Poll` | weekdays 10:15 and 13:15 | EDGAR poll (from the previous business day); for events with D_e = today: eligibility, text, LLM score, and the LLM-tranche record by the deadline | events, scores, tranche records, heartbeat |
| `MomentumX-SS0006Fwd-Entry` | weekdays 12:22 and 15:22; each run acts only if now is in [T_e − 8 min, T_e − 5 min) for today's T_e per the frozen calendar, and otherwise writes a heartbeat and exits | borrow snapshots in [T_e − 5 min, T_e) for today's D_e events (all tranches) and for every open U ticket | borrow rows, heartbeat |
| `MomentumX-SS0006Fwd-Mark` | weekdays 19:30 | historical NBBO at today's T_e (entries, daily marks, exits, stock and IWM); SSR flags from 1-min bars; daily bars; ρ and ρ-tranche records. No corporate actions | raw quotes and bars, ρ records, heartbeat |
| `MomentumX-SS0006Fwd-Watchdog` | daily 07:30 and 20:30 | checks W1-W10, pages | watchdog log |

**9.4 Watchdog checks.** Each one **PAGEs** through `OPS_ALERT_WEBHOOK_URL` (304:88). Tests use a mock channel and never the live webhook (304:389-390: "Tests must not touch production or its channels").

| check | pages when |
|---|---|
| W1 | any collector's heartbeat is missing for the last session |
| W2 | zero 8-Ks of any item are polled on a session day. EDGAR showed 175 on 2026-09-21 and 437 on 2026-07-30 (evidence pack 1), so zero means the poll is broken |
| W3 | eligible > 0 but scored = 0; or tokens_in = 0; or any HTTP 4xx/5xx after retries. Same day |
| W4 | events are entering today but there are no borrow rows by T_e |
| W5 | an open ticket has been missing a mark for more than 1 session |
| W6 | `schtasks` does not show all four tasks existing, enabled, and with LastRunTime within the last session. The Poll task checks the watchdog's heartbeat in turn |
| W7 | \|OS UTC − EDGAR HTTP `Date` header\| > 60 s (296:9-16) |
| W8 | running holes exceed 5% (warning) or 10% (page) |
| W9 | INTERIM, EVAL, UNBLIND and DEATH_DATE come within −20 and −5 sessions, and on the day itself. At DEATH_DATE with no REPORTED row, the watchdog writes UNRESOLVED-BY-DEATH and pages. It reads these dates from the frozen constants and the manifest, **not** from the registry's `death_date` field, which nothing reads (trial_registry.py:118) |
| W10 | the bot watchdog's process selector (below) would match any SS0006 collector command line |

A daily counts-only "alive" post acts as a human dead-man switch for the single-machine failure point. If this PC is off, nothing pages. The point-in-time inputs for those days (scores before the deadline, borrow snapshots) are lost for good and become holes.

**9.5 Freeze interlock.** There is no transition from FROZEN to COLLECTING until all of the following hold, recorded in the external manifest:
- all four tasks exist and are enabled;
- each has written one heartbeat;
- the watchdog has written one row;
- Pierce has acknowledged one synthetic test page;
- the W10 scan returns no match.

Session 0 is the first session after COLLECTING, so interlock days are never counted as dark-collector holes.

**9.6 The bot watchdog can kill the collector.**
- `scripts/watchdog_monitor.ps1:235-239` selects any python process whose command line matches `momentum` when the bot heartbeat is stale, and :292-295 kills it.
- Doc 304 records the lottery dying this way three times (304:415-416), and the bot is now quarantined.
- Every script under the repo path matches. The Entry task's borrow snapshots cannot be backfilled.
- **Requirement:** before COLLECTING, either:
  - `watchdog_monitor.ps1` gains an explicit exclusion for `ss0006_fwd` (a code change, Pierce's); or
  - the collector runs from a path and command line that do not match.
- W10 and P-8 verify it.

---

## §10 The executable fixture

**Rules.**
- `_ss0006_fwd_fixture.py --verify` must return PASS before `--unblind` opens any ledger (296:127-131; ATTEMPTS_LEDGER.md:69-71; 302:345).
- Each assertion names the section it enforces.
- Each assertion's correct answer differs from at least two plausible runner bugs (the test_doc298_sevp_prereg_conformity.py:15-22 pattern).
- There are KILL variants, because "a test can assert a true value and still pin nothing" (300:218-220).
- Micro-cases are exact to 1e-6. Costs are off and IWM is flat at 200.00 unless stated otherwise. **All prices are synthetic fixture values, not market data.** Quotes are planted exactly at the mark instant unless stated otherwise.

| # | enforces | planted case | correct | bug → wrong answer |
|---|---|---|---|---|
| **F-1** | §2.4-2.5 anchor | Ticker AAA. 15:30 mids: Mon 2027-01-11 98.00; Tue 01-12 100.00; Wed 01-13 101.00; Tue 01-26 99.50; Wed 01-27 99.00; Thu 01-28 101.00. MLK Day 01-18 is closed, so Tue + 10 sessions = Wed 01-27. Case A: accepted `2027-01-12T13:00:00Z` (Tue 08:00 EST) | D_e = Tue, **g = +100.0000 bps** | "session after the acceptance date" → Wed → **0.0000**; "prior session" → Mon → **−153.0612** |
| F-1b | §2.5 | Case B: `2027-01-11T21:05:00Z` (Mon 16:05). Case C: `2027-01-12T17:00:00Z` (Tue 12:00). Case D: accepted Mon 01-18 (holiday) 10:00 | B → Tue (+100.0000); C → Wed (0.0000); D → Tue 01-19 | B on the acceptance-day session → −153.0612; C "same session if before the close" → +100.0000 |
| **F-2** | §2.4, §2.8 time zone | Ticker BBB accepted `2026-11-02T14:15:00Z` (Mon 09:15 **EST**; DST ended Sun 11-01). Mids: 20:30Z Mon 50.00; 19:30Z Mon 55.00; Tue 11-03 20:30Z 49.00; exit Mon 11-16 20:30Z 49.50; Tue 11-17 20:30Z 49.49; Mon 11-16 19:30Z 56.10 | D_e = Mon, T_e = 20:30Z, **+100.0000** | UTC−4 on the anchor only → 10:15 → D_e Tue → **−100.0000**; UTC−4 on the anchor and on T_e → Tue 19:30Z, no quote → **MARK-MISSING**; `h+4` on the marks only → 19:30Z quotes → **−200.0000** |
| **F-3** | §2.7 horizon, §2.8 regular hours | Ticker CCC, entry Wed 2026-11-18 at 40.00. Thanksgiving 11-26 closed; 11-27 closes at 13:00, so its mark is 12:30 = 17:30Z; an extended-hours quote is planted at 11-27 20:30Z. Session 9 (Wed 12-02) 39.80; session 10 (Thu 12-03) 39.60; session 11 (Fri 12-04) 39.40 | **+100.0000**; runner prints h = 10; the 11-27 mark exists at 17:30Z | 14 calendar days or h = 9 → **+50.0000**; h = 11 → **+150.0000**; a 15:30 mark on 11-27 → MARK-MISSING (the after-hours quote is inadmissible, never used) |
| **F-4** | §5.4 cost | Ticker DDD at 20.00 flat, IWM 200.00 flat (g = 0); q_s = 10.0 bps, q_IWM = 1.0 bps at both instants; ADV20 $50M; b = 1.2127% | **r_net = −16.33485** = 10.0 + 1.0 + 0.3065 + 0.21605 + 4.81230 | one crossing per leg → **−10.83485**; full spread per crossing → **−27.33485**; hedge leg uncharged → **−15.11880**; borrow omitted → **−11.52255** |
| **F-5** | §5.4 floor and precedence | EEE: $20, ADV20 $50M, q = 3.0. FFF: same with q = 30.0. GGG: P_prev $4.00, ADV20 $6,000M, q = 15.0. IWM q = 0.3 | S_s = 7.307 / 30.0 / **22.247**; S_h = 0.559 | no floor → 3.0 / 30.0 / 15.0 / 0.3; floor only → 7.307 / 7.307 / 22.247 / 0.559; ADV before price → GGG 15.0 |
| **F-6** | §4.2 look-ahead and membership | Window of 200 prior scores: 60 at −1.0 (of which 15 NON-ETB, 5 OVERLAP, 5 SSR-FLAGGED), 80 at 0.0, 60 at +1.0; 25 older tradable events at +1.0 sit outside it. Ticker HHH scores −0.5. There are 100 same-session decoys at −2.0 and 300 future events at −2.0. HHH has g = +500; every other BOTTOM member has g = 0 | **v = 0.300000**, BOTTOM; pinned membership list and tranche mean | window including same-session events → v = 0.533; full sample (600 events) → 0.767; window built from tradable events only → **v = 0.175000** |
| **F-6b** | §4.2 replay guard | After HHH's record is written: (i) one window event ends EXIT-STALE; (ii) one window event's entry mark is repaired, changing W^ρ as recomputed from the final ledger; (iii) a copy of the ledger has one tranche record altered | the persisted v and window hashes equal the as-of replay; gates use persisted membership; on (iii) `--unblind` refuses | recompute from the final ledger → hash mismatch not detected / membership changed; no replay → tampered record accepted |
| **F-7** | §4.2 ties, §2.1 accession | Window: 40 at −1.0, 100 at −0.6, 60 at +0.8. 30 current events at −0.6 → BOTTOM iff u < 4/15. The 30 accessions have pinned u values. Plus the raw EFTS id `9999999999-27-000001:ex99-1.htm` and the dashless `999999999927000001` | exact pinned list, identical across two runs; both raw forms normalise to `9999999999-27-000001`, **u = 0.852281** | "≤ cut includes ties" (P:177-188) → all 30; unseeded RNG → the two runs differ; hashing the raw id → 0.416128; hashing the dashless form → 0.081071 |
| **F-8** | §5.2 borrow | Five tickets: (T,T,T) at 15:27:10; (T,T,F); (T,F,F); (T,T,T) stamped 15:31:00; HTTP 500 | counts ELIGIBLE 1, NON-ETB 2, ETB-LATE 1, ETB-UNKNOWN 1; \|S\| = 1 | `fader_short_runner.is_shortable` → admits (T,T,F); no timestamp check → admits the 15:31 row |
| F-8b | §5.2, §9.3 early close | (T,T,T) stamped 15:24:59 on a normal day; (T,T,T) stamped 12:27:00 on 2026-11-27 (13:00 close) | ETB-EARLY 1; ELIGIBLE 1 | no lower bound → admits 15:24:59; fixed 15:25-15:29:59 window → the 11-27 row is ETB-EARLY |
| **F-9** | §2.9, §5.3 | III: 2:1 split in both sources, ex at session 4, entry 20.00, exit 10.00. JJJ: flat 20.00, $0.20 dividend ex at session 3 (both sources). KKK: entry 20.00, mid 40.00 from session 5 (ratio 2.000), no action in any source. NNN: entry 20.00, mid 10.00 from session 5 (ratio 0.500), no action in any source | III **0.0000**; JJJ **−100.0000**; KKK **−10,000.0000** (booked, not excluded); NNN **0.0000** (adjusted as 2:1, the reading worse for the short) | unadjusted split → +5,000.0000; dividend ignored → 0.0000; dividend credited to the short → +100.0000; KKK dropped as UNMEASURED or treated as a reverse split → absent / 0.0000; NNN booked as genuine → +5,000.0000 |
| F-9b | §2.8 exit fallback | RRA: entry 20.00; last admissible mid 12.00 at session 7; no admissible quote at X or on the 5 deferral sessions. RRB: the same, but quotes resume at D_e + 13 at 30.00 | RRA **+4,000.0000**, EXIT-STALE, booked exit D_e + 15; RRB **−5,000.0000**, EXIT-DEFERRED, exit D_e + 13 | dropped after 5 deferrals → RRA absent; last mid before the gap used for RRB → +4,000.0000 |
| **F-10** | §3.4-3.5 parse and retry | `{"drift":"DOWN","conviction":0.8}`; `{"drift":"down","conviction":0.8}`; `{"conviction":0.8}`; `{"drift":"DOWN"}`; `{"drift":"DOWNWARD","conviction":0.8}`; conviction 1.3; no JSON on both attempts; `{"drift":"DOWN","conviction":0.8,}`; conviction `"0.8"`; conviction `true`; HTTP 402; HTTP 429 then a valid reply | −0.8; −0.8; UNPARSED ×4; UNPARSED after **exactly 2** attempts; UNPARSED after **exactly 1**; UNPARSED; UNPARSED; LLM-ERROR after 1; −0.8 after 2 | P:169-170 defaults → 0.0 and −0.5; P:162-171 retries the JSON error (2 attempts); P:170 `float()` → −0.8 and −1.0; more than 2 attempts |
| **F-11** | §3.1, §3.3 | Environment `SBMODEL` and `REDFLAG_MODEL` set to "other/model"; a mocked response with model = "other/model"; ticker LLL accepted `2027-01-12T02:30:00Z` (Mon 21:30 EST) with a fixed 1,000-character synthetic text | transport records the frozen id; MODEL-DRIFT flag raised; `{dt}` = "2027-01-11"; formatted-prompt sha256 matches a pinned value; template sha256 = `936910…6337cfe` | environment override honoured; UTC date "2027-01-12" → different hash |
| **F-12** | §3.2 text | Index [primary.htm, ex99-1.htm, EX-99.2.htm]; HTML with script and style blocks and entities; a 12,000-character body; a 250-character body | ex99-1.htm chosen; pinned stripped string; 9,000 characters kept; TEXT-SHORT | last exhibit chosen; no truncation; short text scored |
| **F-28** | §2.3 Rule 201 | Ticker OOO with c(D_e−1) = 10.00. A: D_e 1-min low 8.95 at 11:02. B: D_e−1 low 8.90 against c(D_e−2) = 10.00. C: D_e lows ≥ 9.01 before 15:30, 8.80 in the 15:45 bar. D: D_e−2 low 8.50 against c(D_e−3) = 10.00, later lows ≥ 9.50. E: D_e low exactly 9.00 | SSR-FLAGGED: A, B, E; admitted: C, D | no SSR rule → all admitted; full-day low → C flagged; 2-day look-back → D flagged; strict "<" → E admitted |
| **F-29** | §4.3 reaction | PPP accepted Tue 2027-01-12 12:00 ET (17:00Z) → D_e Wed 01-13. Closes: Mon 50.00, Tue 45.00. M(Wed 15:30) = 44.00 | P_prev = 50.00; **ρ = −0.120000** | P_prev = D_e−1 close (Tue) → −0.022222 |
| **F-30** | §5.5 book | SSS: entry 20.00 at session e; marks s1 19.00, s2 missing, s3 18.00, then 18.00 to exit at s10; b = 1.2127% on, other costs off | per-session B: +499.51877, −0.48123 (MARK-CARRIED), +499.51877, then −0.48123 ×7; **Σ = r_net = 995.18770** | missing mark as a hole → ticket absent; daily rebalanced to $1 → **1,021.50349**; borrow booked at exit only → s1 = +500.00000 |
| **F-31** | §5.1 zero capital | runner and collector source; a client call to POST `/v2/orders` | the Alpaca client raises on any non-GET method; the AST scan finds no order or position endpoint | a reachable POST → FAIL |
| **F-32** | §2.3 OVERLAP | Ticker TTT candidates with D_e at sessions 0 (non-BOTTOM), 5 (BOTTOM) and 10 (BOTTOM) | U = {0, 10}; **S = {10}** | overlap tracked within S only → S = {5}; "open through exit inclusive" → S = {} |

**Macro-worlds and the verdict function.**
- Each world uses a 504-session synthetic calendar with about 1,800 eligible events and seeded fat-tailed gross returns.
- The exact statistics are pinned at build time by an **independent reference implementation written from this spec by a different author** (the §A skeptic), then asserted to 1e-6.
- Where no data world can make a gate fail on its own, the world may fail it jointly with others. F-22 then carries the isolated branch.

| # | world | required |
|---|---|---|
| F-13 | W-PASS: BOTTOM ∧ ETB tickets planted at +150 bps net; U and S_ρ at +0; planted Ŝ ≈ 6 ≫ 2.6654 | PROVISIONAL-PASS; the string "certified" never appears |
| F-14 | W-KILL: identical, but BOTTOM planted at +0 | KILL, with the class from C1-C3 |
| F-15 | W-UNATTRIB: U and BOTTOM both at +150 | PASS-UNATTRIBUTED |
| F-16 | W-FUTILITY: BOTTOM at −20 over the first 252 sessions | the interim run prints only the bit KILL-FUTILITY, **no numbers**; the close-out run assigns a class |
| F-17 | W-COVERAGE: W-PASS with 11% of events as entry-side holes | BLOCKED-COVERAGE; a runner that books holes as 0 returns a different, pinned mean |
| F-18 | W-FRAGILE: W-PASS plus one ticket at R_s = +300% whose loss exceeds 50% of Σ net | FRAGILE-PASS; the adverse side is unclipped |
| F-19 | W-CLUSTER: 60 entry weeks, each week's tickets duplicated 8× | n_clusters = 60, not 480; the ratio of the G1b CI width to the iid CI width equals the reference implementation's pinned value (to 1e-6) and is ≥ 0.9·√8 = 2.546 |
| F-20 | W-NW: book planted with r_NW = 1.50, and a second with r_NW = 1.37 | n_eff = 336, BAR = **3.2644**; n_eff = 367, BAR = **3.1235** (a float n_eff gives 3.1219) |
| F-21 | spec, fixture and runner hashes, and every §5.6 constant, echoed and compared with the **external manifest** | any mismatch → refuse (302 F-9); prose hashes are void (297:296) |
| **F-22** | the verdict function: every combination of the ten gate booleans (G1a-d, G2a-b, G3a-c, G4) × the flags {VOID, INSTRUMENT, FUTILITY, DEATH} | the exact verdict and class per the D1-D12 list, from a pinned table; D12 never fires |
| F-23 | W-ARITH: BOTTOM at about +10 bps net with low variance, so that Ŝ > 2.6654, the G1b lower bound > 0 and both halves > 0 | PASS-STATISTICAL / FAIL-ARITHMETIC (a runner that drops the 15 bps floor returns PROVISIONAL-PASS) |
| F-24 | W-HALF: BOTTOM strongly positive in H1 and ≤ 0 in H2, with full-sample G1a passing | KILL, first failing gate G1d |
| F-25 | W-G2b: G1 and G2a pass; mean(S) − mean(S_ρ) < 0 in H2 | PASS-UNATTRIBUTED, failing sub-gate G2b |
| F-26 | W-CLIP: BOTTOM's net carried by collapses beyond −20% | KILL; G3a reported FAIL |
| F-27 | W-CONC: G1 passes; the top decile carries ≥ 60% of Σ net | KILL, first failing gate G3b |

---

## §11 Out of scope, and Pierce's decisions

**Out of scope:**
- live or paper orders;
- the 8-slot selection rule and position sizing. These are requirement-side choices (TARGET.md:94-98);
- stop-loss design;
- the TOP (long) leg, which is diagnostic D-1 only (doc 260's long leg is dead, 260:20);
- any other model, prompt or provider;
- 8-K/A, 10-Q and 10-K text;
- hard-to-borrow names and Rule 201-restricted entries;
- options and ETF shorts (doc 304 §3a-3b);
- **historical LLM scoring of any kind**, because of memorisation and spend;
- the doc-255/261 red-flag leg;
- MFCS and `risk_aversion_lambda`;
- deployment or breadth (TARGET.md:201-207).

**Pierce's decisions** (none is taken here):
1. Freezing, and registering **T00034** (toll +0.0086 at this n; queue bar 1.1500 → 1.1537).
2. Ratifying a prior sd at or above the admissibility threshold (0.678 at n = 504). The proposed value of 1.0 would be the widest in the queue (§7.3).
3. Waiving 259:34's precondition for Stage C; dropping the fundamental-surprise baseline; and reopening the family. The last needs either the §1.4 novelty argument or doc 301's UNDERPOWERED reading, covering ATTEMPTS_LEDGER.md:12 and :17.
4. LLM spend and re-keying Together, after P-4 diagnoses the HTTP 402 (at most $46 per year).
5. Creating the four tasks and acknowledging the test page (§9.5), and the watchdog exclusion for `ss0006_fwd` in `watchdog_monitor.ps1` (a code change) or running the collector from a non-matching path (§9.6).
6. Quarantine mode (A) or (B): whether research collection runs while entries stay refused (304:401-402). The collector places no orders either way.
7. The EDGAR User-Agent contact. All three User-Agents in code are placeholders (P:22; E:24; src/data/sec_client.py:388).
8. The borrow value: the 1.2127% stress value or a measured Alpaca fee (P-6).
9. The Rule 201 treatment. The draft freezes it as an exclusion. Deferring entry to the first unrestricted session would be a different design.
10. Whether the directive's 20 / 15 bps floors per 10-session ticket, which are 2 / 1.5 bps per day, are the intended floors (304:406-408).
11. Whether the doc-255 red-flag leg stays dark or unreported during the window (§8).
12. Accepting a 24-month horizon against buy-and-hold SPY at +6.13 bps/day (TARGET.md:19).
13. Re-adjudicating `design_queue.json:43` `_excluded_short_locate_access` ("0-for-94; shorting closed at this broker") for this universe, in light of the "invalid side" rejections (§1.2), and adding this design to the queue under `catalyst_text`.
14. Optionally, a one-share paper probe short on an easy-to-borrow name. It is an order, so it is Pierce's alone.
15. Whether to pre-declare, at freeze, one fallback host serving the identical published weights. It would be admitted only after a canary-equivalence test written into the frozen spec, never substituted mid-trial. It is **not adopted** in this draft.
16. Every step after a pass: the fleet, a broker-truth phase, and any capital.

---

## §12 The case against running it

1. **Base rate.** The program has no certified edges in 60+ gated hypotheses (TARGET.md:187). KILL is the expected result.
2. **Power depends on something unknowable.** Bases A and B differ by 2.09×.
   - On basis B, even an effect the size of doc 260's has P(G1a) of only 49.0% at 24 months.
   - P(PROVISIONAL-PASS) is lower still: G2b alone passes a true 50 bps LLM-minus-reaction gap only about 67% of the time.
   - At the program's ω = 0.25, the 80%-power effect is 138 bps (A) or 288 bps (B).
3. **The signal is weak.**
   - Its long-short spread did not beat the reaction spread on a resample of the same events.
   - Its magnitude is an upper bound (304:326), contaminated by the anchor leak, non-2.02 texts, post-anchor filings and possible memorisation.
   - This universe also has a different unconditional drift: −26 bps hedged in 2025 on the forward proxy, against +61 bps for the UK panel (evidence pack 2 §8).
   - The Rule 201 exclusion removes up to 10.6-22.1% of events, plausibly more from the fader tranche.
4. **The design is admissible only under a declared prior** that is wider than anything else in the queue (§7.3).
5. **Even a full pass is small.** This is conditional arithmetic, not a capability (TARGET.md:193-195).
   - Take the identity (TARGET.md:94): deployment 0.20 short notional (40% gross in hedged pairs) × 1/10 turns per session (a 10-session hold) = 0.02.
   - At the 80%-power effect on basis A (117 bps), that is 0.02 × 117 ≈ **2.3 bps/day**.
   - The 40% gross is the pre-quarantine emergent figure (304:48-49), and TARGET.md:201 forbids any breadth increase until a per-ticket CI excludes zero.
   - That is below SPY buy-and-hold at **+6.13 bps/day** and below the 5 bps/day target.
   - A roughly market-neutral sleeve could stack on top of an index position, but leverage and margin are Pierce's alone (TARGET.md:208-209).
6. **Model retirement.** Two Together serverless models (DeepSeek-V3.1 and Qwen3-Coder-480B) were already retired during this program (P:24; E:25-26). With one serverless id over 24 months, UNRESOLVED-INSTRUMENT is a live outcome that would spend T00034's toll for nothing.
7. **Operational risk.** This means 24 months on one Windows PC. SEVP, Kalshi and the doc-255 leg all died silently on the same kind of setup, and the bot's own watchdog kills matching processes (§9.6).
8. **Paper is not live** for borrow or recalls, and a shadow ledger is not broker truth.
9. **The argument for running it anyway:**
   - $0 data and at most $46 per year of LLM spend;
   - an executable instrument, and a mechanism independent of the bot;
   - three defects that no backtest can remove are removed by construction, and a fourth (Rule 201) is observed point-in-time;
   - G2 separates the LLM from unconditional drift and from timing composition;
   - doc 260 itself named this test.

   Closing it cleanly on a frozen contract is worth more than leaving it open indefinitely as a maybe.

---

## §13 Pre-freeze prerequisites

These consume no trial and compute no effect statistic. Deadline 2026-11-30. Any failure makes the draft **VOID and unregistered**.

| # | prerequisite | passes when | status |
|---|---|---|---|
| P-1 | acceptance time-zone check | ≥ 30 filings from ≥ 10 issuers, including 08:00 and 21:00 ET acceptances, match their headers; **the artifact is saved** | **partial**: 10/10 across 3 issuers (unsaved) |
| P-2 | the fixture | F-1 to F-32 pass on the PASS and KILL variants; the reference implementation is by a different author | not started |
| P-3 | power on the true forward definition | Sharpe per bps recomputed on 2025 Item-2.02 events with real `acceptanceDateTime`, 15:30 entries, the frozen one-per-issuer-per-session rule (no 14-day de-dup), ETB thinning, the Rule 201 exclusion and moving-block CIs; §7.1 is frozen on that result; no LLM, no tranche outcome | not started |
| P-4 | model access | the HTTP 402 is diagnosed (key, account or endpoint); Together is restored with the frozen id (Pierce's spend); MODEL_ECHO is recorded; canaries are scored 3× and recorded | blocked (HTTP 402) |
| P-5 | plumbing dry run, 10 sessions | EDGAR counts; borrow snapshots at T_e − 5 min, including an early close if one falls in the run; NBBO `feed=sip` present at 15:30 for ≥ 98% of names; the account-default feed used by `true_nbbo_cost.py` identified, with floors re-measured on SIP if it differs; **counts only**: the SSR rate over all events and the primary-document fallbacks. No dry-run score may seed a window | not started |
| P-6 | Alpaca borrow fee | measured from Alpaca's schedule or a live statement; freeze max(measured, 1.2127%) | not started |
| P-7 | prior | Pierce ratifies it; eig re-run at freeze, **with the design queue passed in**, returns ADMISSIBLE | pending |
| P-8 | tasks, paging and watchdog | the §9.5 interlock is satisfied, including a W10 scan with no match | not started |
| P-9 | canonical spec | the private-repo LF copy is the hashed copy. It is sanitised (e.g. "Pierce" → "the operator") **before** hashing, because the public SEVP copy hashes to d7fc38db…, not the canonical ae62ea59… (evidence pack 3 §3). The spec lives in a repo, not the OS Temp dir where T00031-33's specs sit (evidence pack 3 §4). **Doc 304 is committed and every 304:line citation is re-pinned to that hash** | not started |
| P-10 | design queue and account | a queue entry under `catalyst_text`; `_excluded_short_locate_access` re-adjudicated; a read-only GET of the paper account's `shorting_enabled` and `no_shorting` recorded | Pierce |

---

## §14 Pre-freeze disclosure

- **What the drafter and the reviser did not open:** `stage_b_scored*`, doc 260, doc 261, `rocket_watchlist_cron.log`, or any LLM score, prediction, conviction or reason column.
- **What was read:**
  - doc 304 §1, §3-§4, §6-§8 (working tree), and ATTEMPTS_LEDGER.md:1-20, 64-71 and 104-109. These restate doc 260's aggregate tranche medians, which are already in the task brief;
  - code lines only in P (:20-27, 60-87, 128-140, 157-171, 225-230);
  - the order-rejection message field of six fader-short logs;
  - src/epistemics/closure.py class definitions.
  - The hypothesis itself was selected on doc 260's conditional outcomes, and that is the reason it must be tested forward.
- **Exposure through reviewers.** The provenance reviewer incidentally read doc 301:48 and :63, which carry doc 260's aggregate long-short f10 (+2.82%, CI [−0.8, +5.1]). That figure is already in the ledger.
- **Exposure through evidence pack 3.** The author of the rules evidence pack was incidentally exposed to three things. Their values are withheld:
  - (a) one running line from the doc-260/261 red-flag-vs-outcome summary on the live gapper collection;
  - (b) the doc 261:15 one-day illustration;
  - (c) one line of Kalshi Brier and P&L.

  The gate thresholds here were written from the doc 259, 284 and 294 conventions without that exposure. The fleet should confirm them.
- **Pre-freeze computations.** All are unconditional, on events and prices only:
  - the 2025 EDGAR 8-K crawl;
  - the eligibility join;
  - the random-tercile books: Sharpe per bps, the Newey-West ratio, and the sds of the contrasts;
  - **one first moment of the universe baseline**: the unconditional hedged short on the forward proxy, −26 bps (2025). It is disclosed per the 302 precedent;
  - the unconditional Rule 201 trigger rates;
  - the IBKR fee quantiles;
  - the EDGAR time-zone check;
  - the session calendar;
  - the eig appraisals.
- No forward data exists yet, so **no peek at the primary endpoint was possible.**

---

## §15 Provenance

| figure | source |
|---|---|
| Doc-260 medians (−3.44 / −3.5 against −1.13 / −1.1), n per tranche, gate results, CI | task brief; 260:13,16,20,21-22,23,26 (as cited by the evidence packs; doc 260 not opened) |
| What `g_vs` tests; reaction sort; seed-11 construction; model id; retired models | P:229, P:69, P:83-86, P:26, P:24, E:25-28 (read this session at `0b05ef1`) |
| Universe median −1.74% → −0.40%; 80.7% / 87.6% easy-to-borrow; 34/1,447; puts at 264 bps; ETF short at −27.7 to −50.0; ~10.5 bps spread; upper bound; GO row | task brief; 304:287, 295, 297, 307-310, 326, 338, 350 (working tree, untracked) |
| "invalid side" rejections; `sell_short` side | logs/fader_short_2026-07-09.log:16, 2026-08-07.log:17, 2026-08-14.log:20 (and 05-26, 06-03, 06-11); fader_short_runner.py:178 (verified this session) |
| Replay: 15.1/14.3% filed late, 19.9/18.6% accepted late, 71.5/73.9% Item 2.02, subset medians | evidence pack 1, `x260/match_audit.py` |
| Qwen3 cutoff NOT FOUND | src/core/llm_leakage.py:67-93, 209-219 |
| Prompt, text rules and decoding | P:26,111-115,136-140,146-152,157-171 |
| Prompt sha256 and 600-character length | **computed this session** (`p304b/calc.py`, AST literal) |
| Acceptance time zone: 10/10; corroboration from 571 matched 8-Ks | drafter, unsaved (P-1); provenance review from `x260/match_audit.csv` |
| Rule 201 rates (10.56 / 22.08%; carry-in 1.40 / 9.45%; tier splits) | **reproduced this session** (`rev_validity/ssr_rate.py`; daily lows, so upper bounds) |
| Tier table, fees, no midpoint, 1.44×, SPY +6.13, identity, 0 certified, breadth rule | TARGET.md:19,94,113,117-130,137-141,149-151,187,201 |
| IWM own round trip 0.348 | 303:44 |
| UK rates, sds, ICCs, design effects, Sharpe per bps (B), unconditional benchmark | evidence pack 2; `ss0006_params_output.txt`:12, 28 and §7b |
| 16,824 / 11,517 / 959.8 per month / 7.8% / $25.8M / 37.8%; Sharpe per bps 0.02791 and 0.02877; sd 10.73%; −26 bps; NW 0.856; contrast sds | **computed this session** (`p304b/efts_2025.py`, `elig.py`, `book304b.py`, `acf304b.py`, `g2sim.py`) |
| G2b half-sd 55.2 bps; pass probabilities 67% / 46% | provenance review (`g2sim.py`); Φ arithmetic checked this session |
| IBKR fee quantiles (0.25 / 0.4625 / 1.2127 `higher` / 0.9434 `linear` / 4.3445%) | **recomputed this session** from `data/research/doc284/borrow_pricing.json` |
| Bars 2.6654, 3.2644, 3.0777, 2.1763, 3.1235 (int 367), 3.1219 (float); k = 3.7694; registry = 33 | **computed this session** (research `trial_registry.promotion_threshold`, `trial_count`) |
| eig verdicts, p_cert, MDE at ω = 0.25 and 1, toll, severity; family-label invariance | **computed this session** (`p304b/appraise304b.py`, `p304c/fam.py`, research-repo `src/epistemics/eig.py`) |
| Prior admissibility thresholds 0.678 / 0.830 / 0.9585 | directive review (computed) |
| Queue prior sds (max 0.7); `catalyst_text`; `_excluded_short_locate_access` | `data/research/design_queue.json` (read this session), :43 |
| Closure classes | src/epistemics/closure.py:50-120; 301:197-199 |
| Watchdog selector and kill | scripts/watchdog_monitor.ps1:235-239, 292-295; 304:415-416 |
| Session dates (0 → 2026-12-01 … 534 → 2029-01-17; latest session 0 = 2026-12-15); minimum cost 6.727 bps; accession u values; F-9, F-29, F-30 values | **computed this session** (`p304c/cal.py`, hand-written NYSE holiday list) |
| Queue bar 1.1500 → 1.1537; SEVP / Kalshi / rocket-gate failures | evidence pack 3 |
| 1.2127% borrow; prior sd 1.0; window 200; tier mapping; entry at close − 30 min; split set K; one-sided clip; SSR proxy; closure-class thresholds | **DECLARED JUDGEMENTS**, reasons given in place |

---

**FREEZE MANIFEST (external file `ss0006_fwd_manifest.json` plus the registry row; never inside any hashed file).** To be completed only when P-1 to P-10 all pass. Every field is blank. The spec, fixture and runner reference this manifest by path. Its own sha256 goes into the registry row.

```
spec_sha256      : <sha256 of the canonical private-repo copy, byte-exact, post-sanitisation>
fixture_sha256   : <sha256 of scripts/_ss0006_fwd_fixture.py>
runner_sha256    : <sha256 of scripts/_ss0006_fwd_runner.py and collector>
prompt_sha256    : 936910538ae556eafbe70452493da2d51789e22a3f9f4cce54a72da5a6337cfe
model            : Qwen/Qwen3-235B-A22B-Instruct-2507-tput @ https://api.together.xyz/v1
model_echo       : <response `model` string recorded at P-4>
trial_id         : <T00034 if registered; blank until Pierce registers>
family           : catalyst_text / ss0006_forward_fader_short
primary_endpoint : §0 (mean per-ticket net, IWM-hedged, §5.3-5.4)
registry_copy    : momentum-x-research scripts/trial_registry.py @ <commit>
doc304_commit    : <hash that the 304:line citations are pinned to>
freeze_commit_utc: <ISO-8601 UTC>
collecting_utc   : <ISO-8601 UTC of the FROZEN→COLLECTING transition>
window_open      : <session 0 date; must be <= 2026-12-15>
n_eval           : 504 book sessions (last entry 493); interim 252 (kill-only, one bit); unblind 509
bar              : <promotion_threshold(0.0, floor(504/max(1,r_NW)), N)['OPERATIVE_BAR']; 2.6654 at N=34, n_eff=504>
prior            : mean 0.0, sd <ratified>, omega 0.25; eig verdict <at freeze, queue passed in>
borrow_apr       : <max(1.2127%, P-6 measured)>
unblind_date     : <session 509, absolute>
death_date       : <session 534, absolute; outer bound 2029-01-31>
tasks_verified   : <4 task names, first heartbeat UTC, watchdog row UTC, W10 scan UTC, test-page ack UTC>
registered_at    : <ISO-8601 UTC>
state            : PROPOSED
```

---

## Review log (revision 1)

**Blocking findings, and how each was resolved:**

1. **Executable-prereg B1: the tranche rule could read the future.**
   - Window membership now depends only on scores for W, or on pre-entry ρ records for W^ρ, of events with an earlier D_e (§4.2-4.3).
   - The tranche is computed only on session D_e(i), before the deadline.
   - The record is persisted with a window hash and replayed as-of by `--unblind`; a mismatch means VOID.
   - Exit-side status can no longer affect windows.
   - Fixtures: F-6 extended (NON-ETB, OVERLAP and SSR members; v pinned) and F-6b added.
2. **Executable-prereg B2 and directive B1: the verdict map was incomplete and the halves were undefined.**
   - §6 now has an ordered decision list, D1-D12, ending in a catch-all, plus deterministic closure-class rules C1-C3.
   - Halves are defined by D_e session index (0-246 / 247-493) and shared by S, S_ρ and U.
   - Ties are broken by (D_e, A, accession).
   - F-22 tests the full truth table; F-23 to F-27 add a world for each gate that can fail on its own.
3. **Executable-prereg B3: outcome-dependent exclusions could drop the squeeze tail.**
   - No ticket is excluded after T_e.
   - EXIT-DEFERRED ends in EXIT-STALE at the last admissible mid.
   - Undocumented split suspects go to a third source, and if unresolved they are booked under the reading worse for the short (§2.8-2.9).
   - Corporate actions are resolved only at unblind.
   - F-9 changed (KKK is booked, NNN added); F-9b added.
4. **Validity B1: SEC Rule 201 was not modelled.**
   - A frozen, knowable SSR flag at T_e is now a defined exclusion applied identically to S, U and S_ρ (§2.3).
   - Diagnostic D-11 added; fixture F-28 added.
   - The SSR rate is counted in P-5 and reflected in power by P-3.
5. **Directive B2: no fixture tested the directive floors or G1d, G2b, G3a and G3b.**
   - F-23 W-ARITH (net floor) added, along with W-HALF, W-G2b, W-CLIP and W-CONC.
   - F-22 covers any branch no world can isolate.
   - §5.4 notes that the gross floor cannot bind on its own (minimum cost 6.727 bps).
6. **Directive B3: statistical conventions were under-specified.**
   - §6 "Statistics" freezes the Sharpe ddof, the exact NW10 formula (the `acf304b.py` convention), the integer n_eff, the registry copy and N, the moving-block bootstrap with `default_rng(304)` and its draw shape and order, and the percentile method.
   - F-19 and F-20 now pin reference values.

**Non-blocking items applied:**
- doc 304 line numbers re-cited, with a commit required (P-9);
- G2b relabelled as a new gate, and §1.1 softened;
- "P(pass)" relabelled as P(G1a), with ω = 0.25 MDEs and G2b power added;
- the §2.6 0.15 bps claim corrected;
- the §9.2 quote replaced with a paraphrase and correct lines;
- the 284p:38 and 297:299 misattributions fixed;
- the basis-B label corrected;
- the quantile method stated;
- the §12.5 arithmetic shown;
- the book, tier precedence, feed, quote semantics, accession form, window ordering, retry and parse rules, MODEL_ECHO, canary mode and PAUSE, early-close entry, freeze-manifest externalisation, G4 denominator and repair, moving blocks, universe-level OVERLAP, P_prev source, watchdog-kill hazard, quarantine mode, floor normalisation, zero-capital enforcement, within-week G2a, borrow-flip diagnostic, discovery date range, the "invalid side" finding, family scope and queue class, prior-grid disclosure and thresholds, registrability arithmetic, model-retirement risk, and the 402 wording.

**Not adopted:**
- the fallback-host pre-declaration, which is left to Pierce (§11.15);
- a fix to the `ex.?99` regex, which is kept frozen from doc 260 and counted in P-5.