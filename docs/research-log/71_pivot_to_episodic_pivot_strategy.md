# 71 — Pivot to Episodic Pivot strategy: research synthesis + 6-month forward plan

**Status:** plan, 2026-04-29 PM. Sequel to:
- doc 68 ($100K starting equity correction; CRCA dependency)
- doc 69 (counterfactual exit analysis; t1_next_open thesis)
- doc 70 (D278 EXIT_POLICY shipped + restart procedure)

**Severity:** STRATEGIC reframe. Recommends halting live entries Mon 2026-05-04 onward pending γ-audit and a deterministic-classifier rebuild. Does NOT touch tomorrow's 04:30 ET auto-launch unless operator sets `MOMENTUM_HALT_NEW_ENTRIES=1` at User scope tonight.

---

## §0 — TL;DR

Today's research report ([research_2026_04_29_episodic_pivot_reframe.md], external) lands three claims with strong evidence:

1. **MOMENTUM-X is, by accident, a degraded version of the Episodic Pivot (EP) strategy** popularized by Pradeep Bonde and Kristjan Kullamägi. The CRCA trade ($3.03 → $38.81 over 5 days, +1180%) is a textbook EP. The strategy's documented signature — 5–10 day hold, ~30% win rate, lottery-skewed P&L, 10–12 classic setups per year — matches both the literature and our real result.

2. **The shuffle-test failure (shuffled mean Sharpe +8.09 > baseline +3.29) indicts the 6-agent LLM ensemble, not the catalyst hypothesis.** The candidate pool contains alpha; the ensemble actively destroys value within it.

3. **We have n=1, not n=280.** Without CRCA we have **no** statistical evidence of edge. Bessembinder's structural skewness (4% of stocks → 100% of wealth) means our P&L distribution is normal for the asset class — it just hasn't accumulated enough Real EPs to constitute a track record.

**Forward path:** lean into α (catalyst-catcher), drop the LLM-ensemble overhead the shuffle test indicts, hold 3–10 days per Real EP, run a γ-audit on CRCA first, earn ≥30 EP-quality trades over 6 months, recompute deflated Sharpe monthly. Target reframe from "10× in 12 months" to "demonstrate edge with statistical confidence."

This doc also catalogs the 5 bugs surfaced by today's session (D139/D142, D222, D278 visibility, ENV_AUDIT gap, deeper Phase 2 fill-handler bypass) — 4 are fixed in the same commit; the 5th (Phase 2 bypass) is filed for next session.

---

## §1 — Research synthesis (what changed in our model)

### §1.1 The strategy class is EP, not "intraday gap-up momentum"

The honest edge statement we've been writing for weeks ("Sub-$15 small caps that gap up >20% on a NAMED, MATERIAL, COMPANY-SPECIFIC catalyst tend to continue") is the EP thesis nearly verbatim. Mapping our project against the EP literature:

| Feature | EP literature (Bonde / Kullamägi / TraderLion) | MOMENTUM-X realized |
|---|---|---|
| Universe | Small/mid-cap, neglected, often sub-$15 | Sub-$15 small-caps ✓ |
| Trigger | Gap >10% on named catalyst | Gap >5% pre-market with multi-agent score (loose match) |
| Hold | 3–10 days ("Real Catalyst EPs warrant 5–10 day holds") | CRCA: 5 days ✓ |
| Win rate | Kullamägi self-reports ~30%; bull-market high-conviction 60–70% | 23.9% (dragged down by intraday tail) |
| P&L shape | "20–100%+ per trade", asymmetric, lottery-tail | CRCA +1180%, rest ≈ -$3.5K ✓ |
| Frequency | "10–12 classic EP opportunities annually" in bull | We found ~18 carry trades in 3 months; 3–4 plausibly "real" EPs |
| P&L attribution | "Most profits from 9M / Sugar Babies" — small minority | All gains from one trade ✓ |

**Implication.** We didn't invent a strategy class; we stumbled into a 20-year-old discretionary swing-trader playbook and tried to run it as an intraday LLM bot.

### §1.2 The shuffle-test failure indicts the SELECTOR, not the catalyst

A permutation/shuffle test where shuffled mean Sharpe (+8.09) > baseline Sharpe (+3.29) says: random pairings of (entry, exit) timestamps drawn from our trade pool out-perform the actual sequence the bot chose. This means:

- The candidate pool of small-cap gap-ups **contains alpha** (otherwise shuffled would not be positive).
- The 6-agent LLM consensus + debate + half-Kelly tier sizing + OTO/SMART_EXIT machinery **actively destroys value** vs. random selection within that pool.

This is consistent with López de Prado's *Deflated Sharpe Ratio* and *Probability of Backtest Overfitting* literature: complex selection layered on a thin signal is the textbook recipe for negative selection bias under multiple testing. It's also consistent with "Not All Factors Crowd Equally" (arXiv 2512.11913, 2025): mechanical momentum has decayed from ~10% annual in the 1990s to ~2% today; judgment factors crowd less.

### §1.3 Statistical evidence: n=1, not n=280

Under Bailey & López de Prado's Deflated Sharpe Ratio and Minimum Track Record Length frameworks, with one dominant +1180% observation:

- Our **effective sample size is ~1**.
- Standard PSR will not clear 95% confidence.
- For a true Sharpe of ~1.0 (typical EP target), MinTRL requires multiple years OR hundreds of independent EP trades.
- Bessembinder (JFE 2018): 4% of CRSP stocks 1926–2019 generated 100% of net wealth above T-bills; smallest decile, only 31.5% of monthly returns beat T-bills. **Our 1-trade-out-of-280 P&L distribution is structural, not a bug.**

**Honest position: we have evidence of one good trade, not evidence of an edge.**

### §1.4 Tailwinds for path α (catalyst-hold)

- **Microcap PEAD survives, large-cap PEAD does not** (Subrahmanyam 2024, UCLA Anderson Review). t-stat 2.18 with all stocks, 1.43 (insignificant) without microcaps. Exactly our segment.
- **FDA Fast Track event-study**: 5-day CAR +21.59%, 30-day +38.34%, 1-year +76.64% on 25 small biotechs (Drug Discovery Today, 2024).
- **5-min ORB on Stocks-in-Play** (Zarattini, Barbon, Aziz — Swiss Finance Institute, Feb 2024): Sharpe **2.81**, annualized α **36%**, on 7,000 stocks 2016–2023. The single best-published academic backtest of an intraday strategy *gated by news catalysts* — and it converges back into path α as the entry primitive.
- **Zero-commission microstructure** (Jain et al., J. Banking & Finance 2024): retail flow is uninformed, intraday volatility up, price impact down. Bullish for catalyst-driven swing holds (more noise around the catalyst = more institutional alpha-reload window). Bearish for our 60-second BAR-1 exits.

### §1.5 What this says about D278 / t1_next_open (shipped today)

The t1_next_open hypothesis (doc 69) was directionally right but **incomplete**: it treats "BAR-1 EXIT was the bug" but the deeper issue is "the EP playbook calls for 3–10 day holds and we were exiting at T+60s OR T+20min OR EOD". T+1 next-day open is the floor of the EP hold window — better than intraday auto-exit, but still cutting most Real EPs short.

D278 is the right *first* step. Phase 2 (after γ-audit) will likely add a `t5_close` mode and selective multi-day carries.

---

## §2 — γ-Audit plan (Week 1, starting Mon 2026-05-04)

**Goal:** decide whether the existing system flagged CRCA correctly (replicable signal) or got lucky (signal happened despite system).

### §2.1 Recommended halt

Set `MOMENTUM_HALT_NEW_ENTRIES=1` at User scope **tonight** so tomorrow's 04:30 ET auto-launch doesn't take new entries while the audit runs. Existing positions continue to be managed normally (stops, SMART_EXIT, etc.).

```powershell
[Environment]::SetEnvironmentVariable("MOMENTUM_HALT_NEW_ENTRIES", "1", "User")
```

(D277 halt switch is `os.environ`-cached at process start, so tomorrow's bot will see it. To lift: set to "0" or remove, then bot must restart for the change to take effect.)

### §2.2 The 6 audit deliverables

1. **CRCA tick reconstruction.** Pull NBBO tick data for CRCA Mar 2–6, 2026 from Databento (≤$50 one-off). Reconstruct what the 6-agent ensemble *actually scored* CRCA on. Was it tagged Real-EP-grade or did it slip through despite low confidence?

2. **17-trade carry classification.** Pull the 17 other carry trades (>1 day hold) and classify each per Bonde's MAGNA-N rubric retroactively:
   - **M**: Massive Acceleration in profit growth ≥100%?
   - **A**: Acceleration in sales ≥39% two consecutive quarters?
   - **G**: Gap-up ≥10%?
   - **N**: Neglected (low coverage, low float)?
   How many were Real EPs? What's the dollar-weighted P&L from Real EPs vs Story EPs vs noise?

3. **Restricted shuffle test.** Run the shuffle test *on the carry-trade subset only*. If shuffled mean ≥ baseline within carry trades, the catalyst-tagging is also broken. If shuffled < baseline, the tagging works and the *intraday* trades are pure noise.

4. **Three-component shuffle test.** Per the research report's caveat, shuffle (a) only entry timestamps, (b) only exit timestamps, (c) only ticker selection separately. This distinguishes "selector destroys value" from "exit policy destroys value" from "entry timing destroys value".

5. **Slippage forensics on NBBO data.** Re-run the slippage analysis on consolidated tape (Databento) instead of IEX-only (Alpaca free). For sub-$15 small-caps, IEX is ~2% of volume — current slippage numbers are structurally compromised.

6. **CRCA pre-positioning check.** Per Da et al. ("Who Pays Attention to SEC Form 8-K?"), most price discovery occurs in the pre-filing window. Did CRCA's catalyst hit before our system saw it? If yes, the 5-day +1180% capture was tail-of-distribution, not signal-detection alpha.

### §2.3 Decision rule

If Real-EP P&L ≫ noise P&L within the carry-trade subset → proceed to Phase 2 (rebuild as deterministic classifier).
If not → switch to a 6-month deep-study halt; the system has no demonstrated edge and shouldn't trade live.

---

## §3 — Path α rebuild (Weeks 2–4)

**Premise:** keep the EP playbook, drop the LLM-ensemble overhead the shuffle test indicts.

### §3.1 Strip the 6-agent ensemble

The current pipeline:
- news_agent (LLM)
- technical_agent (deterministic + LLM ensemble)
- fundamental_agent (LLM)
- institutional_agent (LLM, often EMPTY in logs)
- deep_search_agent (LLM, often EMPTY)
- manipulation_classifier (LLM, often slow-cancelled)
- D124 consensus / debate over the above
- D78 SMART_EXIT composite

Replace with:

1. **Deterministic catalyst classifier** (rules + small models, no LLM):
   - 8-K Item 2.02 (earnings) + IBES surprise % + sales acceleration (MAGNA-N M+A check)
   - 8-K Item 1.01 / 2.01 (M&A with disclosed terms)
   - FDA Drug Approvals DB + Fast Track / Breakthrough Designation
   - PR Newswire / BusinessWire keyword + entity NER (FinBERT-class)
2. **One LLM call per surviving candidate** for adversarial review against MAGNA-N rubric (analogous to a senior trader's gut-check). Total LLM cost target: <$50/mo (down from the current ~$300/mo via Together.ai).
3. **Drop everything else.** No D124 consensus, no debate, no half-Kelly tier sizing on this pool, no D78 SMART_EXIT (or keep but stratify — see §3.4).

### §3.2 Entry rule

- 5-min ORB on the catalyst day (Zarattini 2024 primitive).
- Only after price clears morning high on >2× avg volume.
- Universe: lift the sub-$15 cap to **sub-$30** (Bonde and Kullamägi don't restrict to $15; this restriction was self-imposed without empirical justification).

### §3.3 Hold and exit rules

- Default hold: **3–10 days** (matching `t1_next_open` direction but extending past T+1).
- Scale out in thirds at +20% / +40% / trail.
- Stop: 2.5% standard, 10% high-conviction (Bonde's bifurcation).
- Time-stop if not working in 3–5 days.
- **Never average down.** The thesis is the *initial* repricing.

### §3.4 D78 SMART_EXIT stratification

OPFI today closed via D78 SMART_EXIT (alpha_oracle vol_fade + below_vwap) at T+34 min. That's a signal-based exit and per doc 70 §1 D278 deliberately preserves it. But under the new EP framework, D78 may be cutting Real EPs short with the same false-positives that polluted the intraday tail.

Action: log every D78 SMART_EXIT firing with its component breakdown (vol_fade, below_vwap, dist, churn, etc.) and back-test what would have happened if we'd ignored it. If the alpha_oracle component is the offender, we may want to disable it for catalyst-tagged positions specifically.

### §3.5 Sizing

**¼ Kelly** (not half) for the first 30 EP trades to compensate for thin sample. Promote to ½ Kelly only after deflated Sharpe clears 95% PSR over a real sample.

---

## §4 — Months 2–6: earn evidence

- Target: ≥**30 Real-EP trades** before any "we have edge" claim (research report says 30 minimum, 60 realistic).
- Track Real-EP-only Sharpe, win rate, profit factor separately from any other trades.
- **Run shuffle test monthly** as the primary falsification check.
- Run deflated Sharpe + PSR + MinTRL recalculation monthly.
- **Reframe the $1M target.** 10× in 12 months on a paper account starting at $100K, even on EP at full Kelly, requires extraordinary luck or extraordinary leverage. The research report's base-rate is 1.3–1.8× per year on EP at sane Kelly fractions; 2–3× in a strong bull. The honest goal is "demonstrate edge with statistical confidence", not "10×".

---

## §5 — Data infrastructure (concrete pricing as of April 2026)

Recommended package for the next 6 months (~$277/mo all-in, 0.2% of $140K equity):

| Provider | Plan | Price | Purpose |
|---|---|---|---|
| Polygon.io | Stocks Developer | $79/mo | Real-time WebSocket + tick + NBBO + 10y history (replaces Alpaca free IEX-only feed) |
| Alpaca | Algo Trader Plus | $99/mo | Full SIP for execution (CTA + UTP) — 100% market volume |
| Benzinga Pro | Essential | $99/mo | Real-time news feed + Calendar Suite + AI tagging for catalyst detection |
| edgartools (open source) | Free | $0 | Direct EDGAR scraping, structured 8-K items, XBRL parsing |
| Databento | US Equities Mini | Pay-per-use | One-shot ~$50 for the CRCA week (γ-audit only) |

**Why this matters for the bugs we saw today.** Multiple D231/D232 RECON_HARD_BLOCK errors and a -$54.30 EOD equity drift are at least partially attributable to fill data quality on the IEX-only free tier. Sub-$15 small-caps trade ≤2% of volume on IEX; we're flying blind on the other 98%. Several of the 209 losing trades may be artifacts of fills on a thin venue rather than real strategy losses. The CRCA-audit week is the place to check this.

---

## §6 — Bugs surfaced today (5 found, 4 fixed in commit alongside this doc)

### §6.1 D139/D142 stop_resubmitter AttributeError — FIXED

**Symptom:** `'ExecutionBridge' object has no attribute 'stop_resubmitter'` at `main.py:4868` (D142 Phase 0→1) and `main.py:4902` (D139 Phase 1→2). Cascading recon failures all day for both XTLB and OPFI.

**Root cause:** code routed `await bridge.stop_resubmitter.resubmit(...)` through the `ExecutionBridge` instance, but `StopResubmitter` is a separate singleton built at `main.py:701` and injected only into `TrancheExitMonitor` and `FillStreamBridge`. The bridge has no such attribute.

**Fix:** changed both call sites to use the local singleton `stop_resubmitter` directly (already in scope — used 7 other times in the same function at lines 4946, 5241, 5490, 5525, 5698, 5830, 5954).

**Downstream impact:** also fixes the cascading D231 RECON_HARD_BLOCK STOP errors for tickers that hit Phase 1→2 transition, and prevents D232 RECON_LETHAL from triggering in shadow mode (which would auto-flat positions in live mode).

### §6.2 D222 broker fill fetch AttributeError — FIXED

**Symptom:** `'AlpacaDataClient' object has no attribute 'get_account_activities'` at `main.py:7177` during EOD reconciliation. Falls through to journal-only counts, undercounting today's trades.

**Root cause:** regression of Bug N (fixed in `trade_journal.py` on 2026-04-23 with `hasattr` guard) — the same pattern was left unguarded in `main.py`. A broad `except Exception` then silently swallowed the AttributeError.

**Fix:** added `hasattr` guard at the call site, falling back to `client.get_orders(status="all", limit=500)` which `summarize_from_broker_fills` handles via Bug N flexibility.

**Followup (next session):** add a real `get_account_activities` method to `AlpacaDataClient` using the canonical pattern from `scripts/pull_broker_truth.py:97-152`. This eliminates the fallback branch in `trade_journal.py` and makes EOD reports match broker-truth analyses exactly.

### §6.3 D278 BAR-1 SKIPPED log emission — PARTIALLY FIXED

**Symptom:** zero D278 log lines today despite 2 OTO fills with `MOMENTUM_EXIT_POLICY=t1_next_open`. Doc 70 §2 step 6 promised these lines for runtime confirmation.

**Root cause (deeper than I expected):** the Phase 2 fill-handler block at `main.py:3870-3965` (which contains the D278 skip log at line 3956) was **never reached** today. Today's OTO fills came in via the `bridge.py` poll path (`D217: <ticker> order ... reached terminal status=filled`), not through the main.py fill handler. This bypass also explains the absent "STOP registered" lines and the cascading D231 stop drift.

**Partial fix shipped:** Added a Phase 3 silent-gate visibility log at `main.py:4694` that fires once per position when D278 is preventing BAR-1 EXIT. From tomorrow forward, every gated position will emit one `D278 BAR-1 SKIPPED Phase3 <TICKER>: exit_policy=t1_next_open age=<s>s ...` line.

**Filed for next session:** investigate why the Phase 2 fill-handler block (main.py:3870-3965) is being bypassed. This is a much bigger bug — it means stop registration, post-fill state save, and the entire Phase 2 BAR-1 scheduler are all silently skipped on every fill that comes through the bridge path. Likely root cause: a change to how the bridge dispatches fills, or a missing handler subscription. **Critical to fix before any meaningful runtime verification of D278 (or any other Phase 2 logic).**

### §6.4 ENV_AUDIT extension — FIXED

**Symptom:** ENV_AUDIT did not surface `MOMENTUM_EXIT_POLICY` or `MOMENTUM_HALT_NEW_ENTRIES` at startup, making it impossible to verify policy state from the bot's own logs (Doc 70 §4 gap #1).

**Fix:** added two `EnvVarSpec` entries to `_SAFETY_KILL_SWITCHES` in `src/utils/env_audit.py`. From the next bot start, ENV_AUDIT will show 16 vars (was 14).

### §6.5 EOD JSON empty `{}` — NOT A BUG

**False alarm.** The file `data/reports/eod_2026-04-29.json` is 10,413 valid bytes with a complete EOD report (fires, eod_recon, phase0_health, bocpd_refit, broker_truth_recon with 53-ticker disagreement block). The earlier `cat | python -c json.load` returning `{}` was a viewing artifact (likely a json filter step that returned only top-level non-array keys). No writer bug.

The recon block does confirm the D222-class undercounting symptom — `journal_total_pnl=-$341.92` vs `broker_total_pnl=$41,040.40` (53 tickers with `journal=$0.00`) — but this is the historical journal-vs-broker drift documented in Bug #13 (multiple exit paths skip `record_close()`), not a new bug.

### §6.6 Other observations (no action this session)

- **D87 LLM circuit breaker tripped 14× today** (Together.ai instability). External; not a code bug. Argues for the §3.1 reduction in LLM dependency.
- **D230 EOD equity drift -$54.30** (broker $140,105.75 vs internal $140,160.05). Within lethal threshold but outside tolerance. Likely related to the Bug #13 journal-skipping issue and the IEX-only fill data thinness.
- **Wakeup latency** (process gap, not a code bug): ScheduleWakeup fired ~9.5 hours late today (intended 09:05 ET, fired 17:38 ET). Migrate monitoring to `/schedule` (cloud cron) for tomorrow.

---

## §7 — Operator decisions needed

1. **Halt for tomorrow?** RECOMMENDED. Set `MOMENTUM_HALT_NEW_ENTRIES=1` at User scope tonight so tomorrow's auto-launch doesn't take new entries while the γ-audit runs. Existing position management continues normally. (Reversible — set to `"0"` or remove + restart bot.)

2. **γ-audit scope.** Approve §2.2's 6 deliverables, or pare back? Each takes 0.5–2 days of effort.

3. **Data subscription budget.** §5 recommends ~$277/mo. Approve, defer, or scale differently?

4. **Rebuild scope.** §3 strips the 6-agent ensemble. Approve in principle, or want to stratify (e.g. keep ensemble in shadow mode for a few weeks)?

5. **Target reframe.** §4 recommends moving from "10× in 12 months" to "demonstrate edge with statistical confidence." Approve or push back?

6. **Phase 2 fill-handler bypass.** Filed in §6.3 — this is the biggest bug we found today and it predates D278. Should it be the headline next-session task?

---

## §8 — Status

- ✅ Doc written (this file)
- ✅ D139/D142 stop_resubmitter — fixed (`main.py:4868`, `4902`)
- ✅ D222 broker fill fetch — fixed (`main.py:7177` hasattr guard + fallback)
- ✅ D278 Phase 3 skip log — added (`main.py:~4694`)
- ✅ ENV_AUDIT extension — added (`src/utils/env_audit.py:92-98`)
- ✅ EOD JSON not-a-bug — confirmed
- ✅ Existing D278 + ENV_AUDIT regression tests pass
- ⏳ Phase 2 fill-handler bypass — filed for next session, NOT fixed today (too deep)
- ⏳ Operator decisions §7 — pending

**Discovery rate: 30/0 holds.** No production bugs introduced; 4 pre-existing bugs fixed; 1 deeper bug filed.
