# 68 — CORRECTION: Starting equity was $100K, not $150K. Broker truth shows +$37K realized but concentrated in ONE CRCA trade.

**Status:** correction shipped 2026-04-29. Major reframe of all prior conclusions.
**Severity:** CRITICAL — every prior P&L claim was based on incomplete data. The verdict needs revision.

---

## §0 — TL;DR

Pierce corrected an assumption I had been operating under for ~7 sessions: **starting equity was $100,000, not $150,000.** Today's broker equity is $140,654 → **+$40,654 realized + unrealized over ~3 months (+40.6% on $100K).**

Prior analyses (86-session OOS, BAR-1 falsification, catalyst-only) all used `data/trade_results.jsonl` as the P&L source. That file covers only 5 sessions (4/22-4/28) with **-$6,612 in P&L.** The +$47K unattributed gap forced pulling broker truth from Alpaca `/v2/account/activities/FILL`.

**Broker truth (Nov 2025 → today, 280 closed trades):**
- Σ realized P&L: **+$37,463 (+37.5% on $100K)**
- Win rate: **23.9% (67 wins / 209 losses)** — strategy loses 3 of 4 trades
- Avg win $930 / Avg loss -$119 → 7.8× win-loss ratio
- Profit factor: 2.5
- Daily-Sharpe annualized: **+3.287** (SUSPECT bucket per plan doc 58 §11)
- Max drawdown: $14,340 (9.7%)
- Trading session-days: 17 (yes, only 17 days of actual trading produced these 280 trades)

But the headline numbers hide the real story:

**ONE TRADE (CRCA on March 2, held 5 days, $3.03 → $38.81, +1180%) made $25,046.**
The same-day same-ticker second fill (CRCA $3.03 → $39.01, qty 443) made $15,939.
**Combined CRCA = +$40,985 — MORE than the project's entire $37,463 realized P&L.**

Without CRCA: **-$3,521 across the other 278 trades.** The strategy is roughly breakeven on everything except one biotech/small-cap moonshot that happened to run for 5 days.

Per-month: February -$639, **March +$39,612, April -$1,509.** All gains came from a single month, dominated by a single trade.

Shuffle test on broker-truth pairings (200 iters): shuffled mean Sharpe **+8.088 > baseline +3.287.** Random entry-exit re-pairings produce HIGHER Sharpe than the actual strategy. The strategy isn't beating random pairings on this trade pool — it just happened to be IN the right tickers (especially CRCA) at the right time.

---

## §1 — What the data ACTUALLY shows

### Per-month P&L (where everything happened)

| Month | Σ P&L | Notes |
|---|---:|---|
| 2026-02 | -$639 | Small loss |
| 2026-03 | **+$39,612** | **The entire winning year** |
| 2026-04 | -$1,509 | Small loss; consistent with last week's analysis |

### Per-ticker concentration (the long tail)

| Ticker | n trades | Σ P&L | Win rate | Notes |
|---|---:|---:|---:|---|
| **CRCA** | **2** | **+$40,985** | **100%** | **Both fills of one moonshot** |
| ASNS | 14 | -$6,179 | **0%** | Lost every single trade |
| CANF | 11 | -$6,133 | **0%** | Lost every single trade |
| MOBX | 9 | +$5,165 | 11% | One $7K win covered 8 losses |
| AIFF | 5 | +$3,276 | 100% | All won — pattern? |
| JZXN | 15 | +$2,987 | 60% | Genuine positive expectancy at small n |
| RLMD | 1 | +$1,841 | 100% | Single moonshot |
| ANNA | 2 | +$1,460 | 100% | Both wins on single move |

Top 5 trades = +$52,891 = **141% of total realized P&L.** Top 5 tickers = +$54,255 = **145% of total.** Without the long-tail wins, the strategy would be deeply negative.

### Intraday vs carry split

- **Intraday trades**: 262 trades, **Σ P&L = -$5,675** — the intraday gap-up momentum thesis LOSES money in production
- **Carry trades** (held overnight or longer): 18 trades, Σ P&L = +$43,138 — but dominated by CRCA's 5-day hold

### The strategy class is NOT what we thought it was

The system was designed as: **sub-$15 small-cap intraday gap-up momentum + LLM ensemble + BAR-1 EXIT (T+60s).**

What the data shows production is actually doing:
- Intraday gap-up momentum: -$5,675 across 262 trades. **Falsified.**
- Multi-day catalyst-driven holds: +$43,138 across 18 trades, dominated by 1 outlier.
- The "strategy" appears to be: try gap-ups, mostly lose, occasionally catch a multi-day biotech-style runner.

---

## §2 — How the prior analyses were wrong

The error chain:

1. **I assumed $150K starting equity.** Pierce never confirmed this; I inferred it from somewhere (probably the original edge-assessment doc that mentioned "$150K paper account") and never validated against broker reality.

2. **I treated `trade_results.jsonl` as the canonical trade history.** It's actually a partial log written only for sessions where some specific instrumentation fired. Coverage: 5 sessions out of ~17 actual trading days.

3. **The 86-session OOS used policy mode for 85 sessions.** Policy mode synthesizes candidate selection (alphabetically first 3 sub-$15 high-volume tickers per day). It does NOT replicate prod's actual decision flow. The shuffle-test result that "overturned" the +3.776 Sharpe was on the wrong trade pool.

4. **The catalyst-only OOS test couldn't fire** because the test was running against the policy-mode synthesized entries with Finnhub-free-tier earnings data that didn't overlap. That finding (test couldn't fire) is still correct — but its operational significance is different given the broker truth.

5. **PROMPT_04 declared "no demonstrated edge"** — based on item 3. That declaration was confidently wrong.

**What was right:**
- The infrastructure (limit-aware fill, prod-mirror replay, falsification framework, halt switch wiring) is all correct.
- The 30/0 production-bug discovery rate is correct.
- The methodological findings (arena fill model determinism, uniform-multiplier ratio preservation, A.5 exit-policy mismatch) are all correct.
- The negative findings about specific candidate configs (BAR-1 timing T+15min lift COLLAPSES) are correct AS findings about HARNESS sweeps, not about prod's actual decisions.

**What was wrong:**
- Treating the harness output as a substitute for broker truth.
- Drawing conclusions about "the strategy" when the harness was testing a strawman.
- Recommending halt-switch posture from a falsified premise.

---

## §3 — The CRCA dependency (the actual finding)

**The project's profitability is currently a function of ONE TRADE.**

CRCA on March 2, 2026:
- Entry: $3.03 (two fills, qtys 700 + 443)
- Exit: ~$38.90 (5 days later)
- Realized: +$40,985
- Project total: +$37,463

The math: **subtract CRCA, the project loses $3,521 across the other 278 trades.**

This is not a "strategy with occasional big winners." This is "a strategy that loses on average, plus one lucky catch."

A 1180% move in 5 days on a small-cap is not normal market behavior. CRCA was either:
- A catalyst event (FDA approval, major contract win, M&A rumor) — investigate
- A pump-and-dump that the bot caught the upside of — possible but lucky
- An IPO / reverse-split / corporate action that produced a momentum cascade

**Without an explicit identification of WHY the bot held CRCA for 5 days through a 1180% move (was it a catalyst signal? Did BAR-1 EXIT fail to fire? Did manual intervention happen?), the gain is unrepeatable and unattributable.**

The shuffle test confirms: shuffled mean Sharpe +8.088 > baseline +3.287. The strategy didn't add value through better entry-exit pairing; it added value through being long CRCA when it ran. Random pairings would have made even more if you held the same trade pool with shuffled timestamps.

---

## §4 — The corrected verdict

The strategy is:
1. **NOT a working intraday momentum strategy.** Intraday trades net -$5,675 on 262 trades.
2. **NOT a working catalyst-only strategy.** We have only n=2 multi-day winners (CRCA dominant).
3. **A long-tail catalyst-catcher embedded in a noisy momentum baseline.** Most trades lose; the rare big winner pays for everything.

The +40% gain is REAL on real money — but **it is concentrated in one trade and is not statistically distinguishable from luck at n=280 with this concentration profile.**

Three honest forward paths:

### Option α — LEAN INTO the catalyst-catcher
- Explicitly target multi-day catalyst holds (biotech FDA, earnings beats, M&A)
- Drop intraday gap-up entries (-$5,675 evidence they don't work)
- Size up trades with explicit catalyst confirmation
- Accept lottery-ticket distribution: 1 of 50 trades pays for everything
- **Risk**: requires identifying catalysts in advance, which is hard
- **Test**: paid earnings data + multi-day hold rules + 86-session retest

### Option β — LEAN OUT of catalysts
- Try different intraday signals (mean-reversion, time-of-day, volume profile)
- Drop the LLM ensemble overhead (it's expensive and the harness shows it doesn't add edge in synthesized form)
- Test each signal class on broker truth, not on harness simulation
- **Risk**: we may already have tested everything that works; the 280-trade history may be close to the strategy ceiling
- **Test**: ship a different strategy class, run for a month, measure broker truth

### Option γ — HALT + DEEP STUDY
- The +40% gain is real but unrepeatable. Halt new entries until we understand:
  - WHY did the bot hold CRCA for 5 days? (Audit logs from March 2 specifically)
  - Was BAR-1 EXIT supposed to close it at T+60s? Did Bug Z / Bug AR fail it open?
  - Are there other CRCA-class candidates in the corpus that we caught vs missed?
- Without this audit, the project is essentially a single-trade fluke that we can't reproduce
- **Risk**: opportunity cost of being out of the market while studying
- **Test**: 1-2 sessions of deep CRCA forensics + audit of all multi-day holds

---

## §5 — Halt switch posture (revised)

**Yesterday's recommendation**: halt because "no demonstrated edge."

**Today's reality**: +40% on real money, but concentrated in one trade that may not be reproducible.

The argument FOR halt is now narrower but defensible:
1. The strategy's profitability is single-trade-dependent. One CRCA-class catch per quarter makes the strategy work; zero such catches makes it lose.
2. We don't yet understand WHY CRCA was held through the entire 1180% move (intentional? bug? exit failure?). Until we do, repeating that pattern is not a strategy decision; it's a hope.
3. April 22-28 was -$6,612. If May without a CRCA is similar, the project loses 6-10% per month until the next outlier.
4. The shuffle test shows strategy decisions aren't adding value over random pairings on this trade pool. Whatever produced +$37K wasn't strategy decision-making; it was being in the right tickers when they ran.

The argument AGAINST halt is also defensible:
1. The strategy IS making money (+$37K is real cash equivalent value).
2. Halt loses opportunity to catch the next CRCA-class event.
3. The strategy as configured has produced positive results for ~3 months.

**Recommendation: halt for today only, while we audit CRCA's March 2 trade. If audit reveals the trade was caught intentionally (catalyst signal fired, exit logic correctly held), lift halt with the understanding that future profitability requires similar catches. If audit reveals the trade was caught accidentally (exit logic failed, manual intervention, lucky bug), the strategy as designed is much worse than the 40% suggests.**

---

## §6 — Action items (next sessions, prioritized)

### Immediate (this commit)
- ✅ Pull broker truth via `/v2/account/activities/FILL`
- ✅ Compute real Sharpe / drawdown / per-month / per-ticker
- ✅ Apply shuffle test to broker-truth pairings
- ✅ Document concentration finding (CRCA dependency)
- ✅ This finding doc + PROMPT_04 addendum

### Next session(s)
1. **CRCA forensic audit** — pull all logs from March 2, 2026 + March 6 (exit). What signal fired entry? What logic prevented BAR-1 EXIT from closing it at T+60s? Was there any operator intervention?
2. **Per-trade catalyst tagging from broker truth** — for each of the 67 winners, identify if there was a same-day or prior-day catalyst (earnings, FDA, news). Build the per-ticker catalyst correlation.
3. **AIFF and JZXN deep dive** — these had positive small-n records (AIFF 5/5, JZXN 9/15). Are they catalyst-driven or pattern-driven?
4. **ASNS, CANF, RITR, BATL exclusion list** — these tickers are 0% win rate. Either the bot is incorrectly trading them or they're a known antipattern. Add to do-not-trade list.
5. **Update PROMPT_04 with broker truth section** — the research direction document needs the corrected baseline.
6. **Update plan doc 58 §16** — add the correction to the plan doc series.

### Strategic decisions (operator)
1. Option α / β / γ from §4 — which path forward?
2. If α: paid earnings data procurement.
3. Halt switch posture for tomorrow and beyond.

---

## §7 — Discipline check

This is the most important finding the project has produced — and it required Pierce to correct an unvalidated assumption. The discipline framework caught false positives in the harness (BAR-1 timing, 86-session OOS) but **did not catch the meta-error that the harness was the wrong measurement device**.

**Logged finding**: data-source-validity is a prerequisite for measurement-validity. Future sessions must:
1. Validate the starting equity assumption against broker on session start.
2. Compute realized P&L from broker activities, NOT from any project-internal log file, when the question is "is the strategy profitable?"
3. Treat harness outputs as proxies for STRATEGY-ALTERNATIVES analysis, not as ground truth for STRATEGY-PERFORMANCE measurement.

**The framework's value persists** — limit-aware fill, prod-mirror replay, falsification suite, halt switch — but its CLAIMS about "edge" need to be scoped to "harness simulation; ground truth requires broker activities."

**30/0 production-bug discovery rate holds.** The error here was not a code bug — it was an assumption-validation gap. Going forward: starting equity + broker P&L are sourced from broker truth, not from internal files.

---

## §8 — Closing note

The good news: the bot is making money on real (paper) capital. Up 40% in 3 months is encouraging.

The honest news: that 40% is one trade. Subtract CRCA and the strategy is mildly negative. The project is one quarter without a CRCA-class event from being deeply red.

The framework caught false positives in the things it measured. It just measured the wrong things. **Today's correction should propagate forward**: every future session computes broker truth FIRST, then uses harness outputs only for what-if analysis. The two are different artifacts and need to stay separated.

Whether the path forward is α, β, or γ is the operator's decision. None of them require lifting the halt switch tonight; all of them require explicitly understanding the CRCA event before any operational decision.
