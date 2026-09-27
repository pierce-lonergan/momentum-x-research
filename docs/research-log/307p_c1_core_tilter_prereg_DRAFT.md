# 307p — C1 LLM macro-regime core tilter: pre-registration DRAFT (NOT FROZEN, no trial registered)

**Status.** Draft only: not frozen, not hashed. **Disposition (doc 307): submitted to the design queue as an information-value option, NOT for registration.** It is INFEASIBLE until a point-in-time macro-text corpus exists, UNDISCRIMINATING on the 447 clean sessions that exist, and ADMISSIBLE only at the 1,077-session combined design. The independent skeptic judged "register neither" the better-supported call; the core already meets the target. **Trial 34 is not consumed; the registry stays at 33.** No LLM spend, no task scheduled. **VOID if not frozen by 2026-12-31.** A gate that fails is a success. Re-tuning any frozen constant kills the gate. A different model, prompt, source list, label map, decision day or gate means a new pre-registration. **Pierce rules before freeze on:** (i) whether to register at all (§Power); (ii) whether the retro half R is admissible; (iii) the host-portability option; (iv) Together funding.

**H1 (one sentence).** Each week, a frozen Llama-3.3-70B multi-agent reading of point-in-time Fed, macro-release and earnings-release text sets the SPY weight of the 80/20 SPY/BIL core to 0.60, 0.80 or 1.00. H1 says this earns a positive information ratio over the static 80/20 core, net of cost.

**Primary endpoint (defined here only).** Ŝ = mean(r_t) / sd(r_t, ddof=1) · √252 over the gate sample, where:
- r_t = (w_t − 0.80)·(R_SPY,t − R_BIL,t) − c_t;
- R are total returns from the shadow tracker's series (`shadow_benchmark_tracker.py` `returns_frame`: Polygon official closes plus distributions);
- w_t is the frozen label map applied from the session after the decision close;
- c_t = |Δw|·(0.36 + 1.135) bps, booked on the first session after a change.

**Gate sample.**
- **Retro half R**: every session from 2024-12-09 to the last session before COLLECTING. That is at least 447 sessions and 94 weekly decisions to 2026-09-22.
- **Forward half F**: sessions 0–629 (630 sessions).
- **n ≥ 1,077.** Hole weeks are excluded and counted.

**Knowable decision anchor.**
- **Decision day.** One decision per ISO week, on its last regular session (frozen Alpaca calendar).
- **Inputs.** Only first-release documents whose official release timestamp is ≤ 12:00 ET that day, from a frozen source list:
  - **A1 Fed** (federalreserve.gov): FOMC statement, minutes, SEP, Beige Book.
  - **A2 macro**: BLS Employment Situation and CPI; BEA GDP and Personal Income & Outlays; Treasury quarterly-refunding statement.
  - **A3 earnings**: the EX-99 release (doc-306 `select_text_document` rule) of each 8-K Item 2.02 accepted that week by the 50 highest-ADV20 filers. ADV20 is computed as of the prior session, and the info-set date is logged (doc 296).
  - **Lookback**: the latest document of each type plus everything released in the prior 7 calendar days.
- **No price inputs.** No price, return, volatility or market-data series enters any prompt.
- **Synthesis.** A4 reads only the A1–A3 outputs and emits `{"regime": DEFENSIVE|NEUTRAL|AGGRESSIVE}`, mapped to w = 0.60 / 0.80 / 1.00.
- **Holes.** A label not written by 15:00 ET makes the week a hole. It is excluded from the gate; the money line books it at 0.80.
- **Turnover cap.** At most 2 weight changes per calendar month. A third is not executed and is flagged TURNOVER-CAPPED.

**Instrument and retirement clause.**
- **Model and decoding.** Together `meta-llama/Llama-3.3-70B-Instruct-Turbo`, hard-coded (environment overrides are ignored). Temperature 0. Templates are sha256-pinned.
- **Retries.** One retry, on transport errors, 429 or 5xx only. Nothing is ever re-scored.
- **Stored per call.** The raw response, the response `model` field and the token counts.
- **MODEL_ECHO** is the `model` string observed at P-2, and it is frozen.
- **Why R starts on 2024-12-09.** Llama 3.3 70B Instruct was released 2024-12-06, with pretraining data cut off in December 2023 (HF model card). Weights fixed at release cannot contain any later outcome.
- **UNRESOLVED-INSTRUMENT** (class INSTRUMENT_LIMITED; keystone "frozen model served") if any of these happen:
  - the id is retired or de-served (400 "non-serverless", or absent from `/v1/models`);
  - errors on ≥ 2 consecutive decision days, with service not restored within 4 weeks;
  - `model` ≠ MODEL_ECHO on ≥ 2 decisions.
- **No mid-trial substitution** (304b §3.7; the doc-306 lesson that the last frozen id died mid-flight).
- **Host-portability option.** Decided before freeze or never: the same open weights on another host count as the same instrument only if a pre-frozen 5-canary × 3-draw test matches ≥ 4/5 modal labels.
- **Weekly synthetic canary**, as in 304b §3.7.
- **Credit exhaustion (402)** is an outage, not a model change. It creates holes, and the watchdog pages the same day.

**Blindness.**
- **R is scored once, after freeze, in a single batch.** Its labels are hashed into the external manifest.
- **Forbidden before UNBLIND**, except the interim bit: any overlay return or P&L, and any label-versus-return association, for R or F.
- **Readable**: counts, label frequencies, holes, tokens, canary results, MODEL_ECHO.
- **Disclosure.** The designers know the 2025–26 market path. Every choice above was fixed without scoring any label against returns. That is why G1d requires F to be positive on its own.

**Gates.** One intersection decision, resolved by an ordered verdict list as in 304b §6.
- **G1a, the bar.** Ŝ > BAR = `trial_registry.promotion_threshold(0, n_eff, N)['OPERATIVE_BAR']`, with n_eff = floor(n / max(1, r_NW)) (the 304b Newey-West convention, L = 10) and **N = max(34, `trial_count()` at EVAL)** (doc-307 skeptic: a bar frozen at 34 would be weaker than the registry's own `record_result()` bar if anything else registers during the ~31 months). At N = 34 and n = 1,077, BAR = **1.8233**.
- **G1b, the CI.** The one-sided 95% lower bound of mean r_t is > 0. Moving blocks of 2 ISO weeks, 10,000 replicates, `default_rng(307)`.
- **G1c, the floor.** Mean r_t ≥ +0.40 bps/day (≈1%/yr; a declared judgement).
- **G1d, both halves.** Mean r_t > 0 on R, and on F the **one-sided 90% lower bound** of mean r_t is > 0. (Strengthened, doc 307: the designers know R's market path, so "F > 0" alone lets a leaked R pass with a no-skill F at 0.36% / 1.7% / 6.2% for R-Ŝ of 2.0 / 2.5 / 3.02, against 0.008% for a clean single trial.)
- **G2a, drift attribution.** The exposure-matched timing IR, computed on (w_t − w̄)(R_SPY − R_BIL) − c_t, must also exceed BAR. If it does not, the verdict is **PASS-UNATTRIBUTED, which is never promotable** (as 304b D9). An always-AGGRESSIVE rule has w_t − w̄ ≡ 0, so its G2a statistic is undefined, and **undefined = FAIL**. Reason: a no-skill always-AGGRESSIVE rule already scores IR **0.772** (2016–26) and **0.726** on R against the static core.
- **G2b, strong baselines (doc 290).** Mean r_t minus the better of two baselines is > 0 on R and on F. Both baselines use the same label map, turnover cap and costs:
  - **B1**: AGGRESSIVE if SPY TR is above its 210-session mean, else DEFENSIVE.
  - **B2**: AGGRESSIVE if trailing-60-session realised vol is below its 2016-01-05 → 2024-12-06 median, else DEFENSIVE.
  - **B3 (added doc 307; the strongest found)**: weekly reversal — AGGRESSIVE after a down week in SPY − BIL, DEFENSIVE after an up week. Demeaned IR **+0.54** (2016–26) / **+0.35** (R), against B1 −0.115 / −0.392 and B2 −0.45 / −1.07; without B3, G2b on R reduces to "mean > 0.044 bps/day" (doc 290: rank-calibrate against a strong baseline).
- **G3, coverage.** Holes ≤ 10% of decisions, else BLOCKED-COVERAGE.
- **Interim look.** At forward session 252; kill-only; one bit. KILL-FUTILITY iff mean r_t over F so far ≤ 0, or pooled Ŝ ≤ 0. From F alone, P(kill) = Φ(−IR): **0.50** at IR 0, **0.16** at 1.0, **0.034** at the bar, **0.004** at the MDE.
- **Closure.**
  - Ŝ ≤ 0 with a one-sided 95% upper bound < 1.0 → **REFUTED_BY_NATURE**. This excludes an overlay worth ≳3.5%/yr at ±0.20.
  - Otherwise → **UNDERPOWERED**, keystone "sample length".
  - A PROVISIONAL-PASS goes to the §A fleet, then Pierce. The word "certified" is banned.

**Dates.** The frozen calendar governs. The example below assumes session 0 = 2027-01-04 (Alpaca calendar, fetched this session).

| milestone | date |
|---|---|
| VOID_BY (freeze deadline) | 2026-12-31 |
| COLLECT_BY (latest session 0) | 2027-02-01 |
| INTERIM | 2028-01-04 |
| EVAL (forward session 629) | 2029-07-06 |
| UNBLIND | 2029-07-13 |
| **DEATH_DATE** | **2029-08-17** |
| outer bound | 2029-12-31 |

There is no extension.

**Executable fixture.** It must PASS before the power gate is consulted or anything is unblinded. Each case must separate the right answer from at least two plausible bugs.

| # | what it tests | planted case → required behaviour |
|---|---|---|
| F-1 | anchor | a document released at 12:01 ET is excluded; a DST week; Good Friday → the decision falls on Thursday |
| F-2 | booking | a label at close t earns t+1; same-day booking gives a pinned wrong value |
| F-3 | overlay and cost arithmetic | Δw 0.40 → 0.598 bps |
| F-4 | turnover cap | the third change in a month is not executed |
| F-5 | parse and holes | bad JSON, an unknown label, a 4xx or a late label → a hole, excluded, never defaulted |
| F-6 | drift world | a planted drift world where always-AGGRESSIVE clears G1a → PASS-UNATTRIBUTED (G2a undefined = FAIL), never promoted |
| F-7 | baselines | B1 and B2 on a synthetic series give pinned labels |
| F-8 | NW, n_eff and BAR | planted r_NW 1.10 → n_eff 979 and the pinned BAR |
| F-9 | both halves | R positive, F negative → KILL at G1d |
| F-10 | instrument | environment override ignored; MODEL-DRIFT on an echo mismatch; template hashes |
| F-11 | knowledge-boundary probe | a mock that knows post-2024-12 facts is flagged → R is VOID; pins a Llama-3.3 entry in `src/core/llm_leakage.py`'s cutoff registry, which has none today |
| F-12 | point-in-time corpus | revised vintages and post-cutoff documents are excluded |
| F-13 | zero capital | no order endpoint is reachable (AST scan); the collector never imports `core_rebalancer` |
| F-14 | verdict function | every combination of gate booleans × {VOID, INSTRUMENT, FUTILITY, DEATH} |

Plus four synthetic worlds (W-PASS, W-KILL, W-FUTILITY, W-COVERAGE), each pinned by an independent reference implementation.

**Costs.**
- **Frozen schedule** (the tracker's): SPY 0.36 / BIL 1.135 bps one-way. That is 0.598 bps of NAV per full 60/40 ↔ 100/0 switch.
- **At the cap** (≤ 24 switches a year): ≤ 14.35 bps/yr, an IR drag of ≤ 0.041.
- **Measured this session**: BIL 1.093 bps quoted round trip (one tick, n = 57); SPY 0.2585 bps (replicates v2's 0.261). That gives 0.354 bps per switch including fees. The frozen schedule is therefore the conservative one.
- **LLM**: a few dollars a year (estimated from doc 306's $1.04/Mtok, which is unverified).
- **Capital: zero.** The design is shadow-only, and the core rebalancer never reads these labels during the trial.

**Precision rules the fixtures must pin (doc-307 skeptic).** "Latest document of each type" = the latest released ≤ 12:00 ET on the decision day (the R batch runs later); 8-K/A amendments are excluded; a capped change counts in the calendar month of its decision day; after a hole week Δw is measured from the last executed weight; R has no 15:00 hole process, so R's holes come only from missing corpus documents.

**Power.** Default prior N(0, 0.5), ω = 0.25, 33 registered trials. Decisions are weekly and the tilt is ±0.20; the overlay's sd is 22.34 bps/day.

| design | n | BAR@34 | verdict | p_cert | informativeness | threshold prior sd | MDE IR (ω .25 / ω 1) | weekly hit rate at MDE | overlay %/yr at MDE |
|---|---|---|---|---|---|---|---|---|---|
| R only | 447 | 2.8302 | UNDISCRIMINATING | 0.0369 | 1.240 | 0.720 | 4.09 / 3.46 | 0.92 | 14.5 |
| fwd 12 mo | 252 | 3.7694 | CEREMONIAL | 0.0337 | 1.135 | 0.958 | 5.45 / 4.61 | >1 (impossible) | 19.3 |
| fwd 24 mo | 504 | 2.6654 | UNDISCRIMINATING | 0.0378 | 1.271 | 0.678 | 3.86 / 3.26 | 0.89 | 13.7 |
| R + 24 mo | 951 | 1.9404 | ADMISSIBLE, knife-edge: UNDISCRIMINATING at r_NW 1.05 | 0.0450 | 1.514 | 0.493 | 2.81 / 2.37 | 0.79 | 9.95 |
| **R + 30 mo (this draft)** | **1,077** | **1.8233** | **ADMISSIBLE, holds to r_NW 1.10** | **0.0470** | **1.582** | **0.464** | **2.64 / 2.23** | **0.77** | **9.35** |

- At ω = 1, every row is INFORMATIVE BUT UNCERTIFIABLE.
- **Under a literature-shaped prior** (N(0, 0.3) or N(0.1, 0.3)) this design is **CEREMONIAL**.
- ⚠ **Hit-rate column corrected (doc-307 skeptic).** The column above ignores the 2-changes-per-month cap. Under the cap, perfect weekly foresight scores IR **3.59** (full) / **3.02** (R), and the MDE of 2.64 needs a weekly hit rate of about **0.86** (full) / **0.92** (R); merely clearing the bar (1.8233) needs about 0.75 / 0.78. The MDE is 74–87% of cap-limited perfect foresight.
- For scale: uncapped perfect weekly foresight scores IR 5.04. A literature-sized timing IR of about 0.25 would need 340–476 years to certify.

**Family note (doc-307 skeptic).** New at the mechanism level (text-driven index timing), but A3 reads the same 8-K Item-2.02 EX-99 channel as ATTEMPTS_LEDGER row 17, and row 31 (overnight index ETF, "46–59% of gross is beta×bull-sample") and the doc-298 M2 vol-managed SPY overlay (IR +0.08/−0.04) are the closest measured neighbours.

**What would kill it.**
- **G1a fails.** This is the expected outcome.
- **UNRESOLVED-INSTRUMENT** when Together retires the model. This is the likeliest non-statistical end over the roughly 31 months from freeze to EVAL.
- **Holes above 10%**, for example from credit exhaustion.
- **Any fixture or hash failure**, which makes the trial VOID.

**How it is judged as an overlay.** The gate statistic is already measured relative to the static core. The money line is a diagnostic with no gate role. It reports the tilted sleeve (month-end drift, the tracker's rules) minus `shadow_benchmark_ledger.jsonl`, and its trailing-252 geometric return against 4.7394 bps/day.
