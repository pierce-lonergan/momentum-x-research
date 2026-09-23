# TARGET.md — the program target as engineering, not aspiration

**Standing directive (Pierce, 2026-07-29): the program's floor target is +1% per MONTH, and it is a
rung — reach it, then reconsider.** ≈ +0.05%/day ≈ **5 bps/day** ≈ +12.7%/yr. Supersedes the 0.1%/day
directive of 2026-07-28 and the 0.5%/day directive of 2026-07-12.
This file is the requirements ledger that directive implies. Established doc 293, rewritten doc 297,
re-scoped doc 298; amend only by Pierce's written direction.

## 0. What 5 bps/day changes, and what it does not

**Does not:** halving a bar creates no evidence. Everything in the ATTEMPTS_LEDGER stays closed on the
measurement that closed it. Deployment and turns remain on the requirement side only (§2).

**Does:** it changes what is arithmetically *sufficient*, and two non-predictive lines now cover most
of the target — which is a different kind of path than hunting an edge.

| line | bps/day | % of the 5 bps bar | status |
|---|---|---|---|
| **buy-and-hold SPY, 2016–2026** | **+6.13** | **123%** | **the baseline clears the rung by itself** — CAGR 14.91%/yr, vol 17.55%, max DD −33.79% |
| 80% SPY / 20% cash | +5.19 | **104%** | the minimum unlevered allocation that clears; 14.04% vol, max DD −27.7% (−$52,867), worst day −8.62% |
| current live book, last 30 sessions | **−28.09** | **−562%** | ⚠ CI95 [−53.00, −3.18] — **excludes zero on the WRONG side** |
| cash carry on idle balances | +1.44 | 28.8% | ⚠ **NOT bankable on this account** — zero INT rows ever; live-account item |
| addressable execution cost | **≤0.48** | **≤9.6%** | ⚠ **re-scoped from 2.80–3.10** — see below; and VOID if the overlay stops |
| edge reopened by halving the bar | **+0.00** | **0%** | zero of 35 ledger families change status |

⚠ **Two corrections to figures this file previously carried:**
1. **"Current book +1.2 bps/day, flat" was window-selected.** It reproduces for 2026-07-09…07-28 (13
   sessions) and then evaporates: July closed at **−3.725%** (197,916.56 → 190,544.68) while SPY fell
   only 0.79% — the book underperformed a flat-to-down market by 2.9 points in 21 sessions. Over the
   last 30 sessions it is **−28.09 bps/day with a CI excluding zero on the wrong side.** The book is
   not flat; it is losing.
2. **"Addressable execution cost 2.80–3.10 bps/day (56–62% of the bar)" was a benchmark mismatch.**
   Measured against prevailing SIP NBBO across 936 fills the recoverable residual is **−0.06 bps —
   i.e. zero**, and the honest recoverable-by-better-execution ceiling is **≤0.48 bps/day**. This
   removes the larger of the two non-predictive lines that made the bar look mostly covered.

## 0b. The certification invariant — the result that should reshape the program's standards

    n = (2.8016 · √252 / S)²        where S is annualised Sharpe

**The target cancels exactly.** Certifying any mean against any bar at 80% power depends only on
Sharpe, never on the size of the target or the volatility run. Consequences:

- Six months (126 sessions) requires **annualised Sharpe 3.96 — at any volatility, against any target.**
- SPY itself, at Sharpe 0.88, would need **2,554 sessions (10.1 years)** to certify.
- **Raising risk does not help.** It scales numerator and denominator together. The doc-298 framing
  that "the gap is a factor of ~6 in risk taken, not skill" is therefore wrong about certification:
  more volatility buys more return and exactly proportionally more noise.

This is why the program has certified nothing in 60+ gated hypotheses, and it will not change.

⚠ **The cash-carry line is unverifiable here.** Probed 2026-07-29: the account holds $190,544 in cash
and has **zero `INT` activity rows, ever**. Alpaca's paper environment does not simulate interest, so
this line can be neither earned nor validated on paper. It is a **live-account item and therefore
Pierce's decision**, not an evidentiary question. Do not count it toward the bar until it appears in
broker truth.

**The honest shape of the path (doc 298, after the baseline was measured):** the deterministic column
tops out at **≤1.92 bps/day = ≤38% of the bar**, and its two lines are mutually inconsistent — the
1.44 needs a live account, the 0.48 needs continuing to trade a book that is losing. **The
deterministic column cannot reach the bar.** Research contributed +0.00. What clears the bar is an
**index allocation**, which is a risk decision rather than a research result, and is Pierce's alone.

⚠ **Engineering blocker before any passive sleeve is possible:** `src/monitoring/eod_failsafes.py`
`run_eod_force_close` market-liquidates every broker position absent from
`position_manager.open_positions`, nightly, **with no symbol whitelist anywhere in the file**. A
passive index position is a "ghost" to that path and would be sold every evening. Also noted:
`.env:72` sets `EXEC_MAX_POSITIONS=8` (40% gross), not the 3 the D162 docstring claims.

## 1. The compounding fact

1%/month compounds to **+12.7%/yr**. For scale: the only audited 12-year live retail systematic track
the program could find (Carver) runs Sharpe 0.80 for ≈13%/yr — so **this target is roughly a
Carver-equivalent outcome**, which is demanding but documented, rather than beyond the frontier.
Medallion ran ≈0.20%/day gross before fees.

The target's true size is not in return space, it is in **Sharpe** space, because a mean is only reachable
at a volatility you are willing to run (doc 297 §arithmetic):

| annualised Sharpe | daily σ this implies | annual vol | sessions to certify +10 bps/day |
|---|---|---|---|
| 0.80 (Carver, audited live) | 198 bps | 31.5% | 3,091 (12.3 yr) |
| 1.0 | 159 bps | 25.2% | 1,979 (7.9 yr) |
| 2.0 | 79 bps | 12.6% | 495 (2.0 yr) |
| 4.0 | 40 bps | 6.3% | 124 (0.49 yr) |
| 10.0 | 15.9 bps | 2.5% | 20 |

**Certification is cheap only in the regime where the target is unreachable, and the target is reachable
only in the regime where certification is expensive.** The book's current σ is 15.9 bps/day; earning
+10 bps/day on it would be Sharpe 10. +0.1%/day is a top-decile-hedge-fund outcome, not a modest one.

## 2. The identity that governs everything (doc 297 — replaces the old per-ticket table)

    account daily return  ≡  deployment × turns × (net per-ticket return)
    required NET per ticket = 0.0005 / (deployment × turns)      # 5 bps/day (amended doc 305; was 0.001)
    achieved NET per ticket = gross per-ticket edge − round-trip cost

**Deployment and turns appear on the requirement side only. They are sign-preserving multipliers: they
cannot make a negative edge positive.** The old "+10.0%/ticket net" headline assumed one 5% ticket per
day. At the standing **5 bps/day** the honest requirement is **12.5–92 bps/ticket net**. The low end is
the maximum permitted deployment × turns (40%: 8 positions × 5%); the high end is the **measured
effective** deployment × turns of 5.46% (doc 297; its verification artifact measured 5.336% all-session,
`data/research/doc297/verify_requirement_arithmetic.md`:13-17, 264). That correction was real, and it
changes no verdict, because the same multiplier scales what is actually earned. Verified against
realised dollars: post-LASE predicted −37.07 bps/day vs −36.18 realised.

> *Amended 2026-09-22 on Pierce's written direction (doc 305).* This section previously used 0.001, the
> superseded 10 bps/day, and so quoted **25–183 bps/ticket**: twice the standing requirement. The same
> amendment corrects the prose. It had said "measured turnover is 3.9–17.2% of equity per session, so…
> 25–183", but 3.9–17.2% is a **both-legs turnover** that the verification artifact says double-counts
> (:307). At either constant it would give a different range (58–256 bps at 0.001). The endpoints were
> always 40% and 5.46%.

Turns divide the requirement **and multiply the cost**. One full round-trip turn of the account currently
costs 52.7 bps of equity = **10.5 days of the 5 bps/day target** (5.3 days at the superseded 10 bps/day). At 26–53 bps/turn the cost term dominates.

### 2b. The cost condition (this, not deployment, is what binds)

A family is viable only if its **gross per-ticket edge exceeds its round-trip cost, at any deployment**.

**Measured from TRUE NBBO** (doc 298, `scripts/true_nbbo_cost.py`; 613 observations, 10 sessions × 7
intraday clock times, session-clustered; round trip = one full quoted spread):

| tier | quoted spread | **+ regulatory fees** | **ALL-IN round trip** | depth at touch |
|---|---|---|---|---|
| **index ETFs** (SPY/QQQ/IWM/DIA) | 0.51 bps | +0.21 | **0.72 bps** | $4.44M |
| mega caps | 1.55 bps | +0.21 | **1.76 bps** | $2.32M |
| large caps | 2.04 bps | +0.23 | **2.27 bps** | $3.46M |
| mid-liquid | 6.81 bps | +0.25 | **7.06 bps** | $7.95M |
| low-priced | 17.74 bps | +0.76 | **~18.5 bps** | $1.69M |
| the bot's low-float gappers | ~61 bps (Roll; no true-NBBO rebuild yet — **provisional**) | — | — | — |

⚠ **Regulatory fees were missing from this table and add ~41% to the liquid tiers** (doc 298): SEC
$0.0000206 × value on sells, TAF $0.000195/share on sells, CAT $0.000003/share both sides.
⚠ **There is NO midpoint or peg order type on this broker.** The full quoted spread is an
**unreducible floor** — no "trade at mid" assumption is admissible in any proposal.
⚠ **The paper account understates regulatory fees by 1.44×** ($184.06 charged vs $264.83 owed across
1,343 real fills): it bills TAF at a retired rate and never charges the SEC fee at all. Any cost
figure taken from paper activity is optimistic by that factor.

**Intraday timing, TIERED** (doc 303, **re-measured doc 305**: 4,185 true-NBBO observations, 5 tiers ×
20 sessions × 7 ET instants, one symbol per request; quoted spread, fees not included):

> *Corrected 2026-09-22 (doc 305), measurement only.* Doc 303's probe requested quotes for many symbols at
> once under a single 1,000-quote cap and never paged, so busy seconds silently dropped the
> alphabetically later tickers (SPY survived in 3 of 19 sessions). The index-ETF row was a composition
> artifact: its 15:30 cell was 0.559 and is **0.945**; its close premium was 1.49× and is **1.01×**. The
> stock tiers moved by a few percent. Every conclusion below survives, and the close-vs-open one gets
> stronger.

| tier | 09:45 | 10:30 | 11:30 | 13:00 | 14:30 | 15:30 | **15:50** | close vs 14:30 | n (old → new) |
|---|---|---|---|---|---|---|---|---|---|
| index ETFs (9 names) | 1.223 | 1.099 | 1.160 | 1.031 | 0.996 | 0.945 | **1.010** | 1.01× | 576 → 1189 |
| mega caps | 2.344 | 1.772 | 1.516 | 1.227 | 1.182 | 0.950 | **0.918** | 0.78× | 713 → 789 |
| large caps | 5.519 | 2.807 | 2.449 | 1.958 | 1.838 | 1.840 | **1.825** | 0.99× | 753 → 775 |
| mid-liquid | 7.590 | 7.318 | 7.361 | 7.265 | 7.388 | 7.388 | **7.399** | 1.00× | 747 → 749 |
| low-priced | 22.346 | 22.198 | 21.299 | 23.068 | 22.232 | 22.805 | **22.962** | 1.03× | 682 → 683 |

⚠⚠ **RETRACTED (doc 303): "intraday timing matters ~4×; median 1.62 bps at 14:30–15:30 versus 6.42 bps
at 15:50; trading near the close costs four times trading mid-afternoon."** That claim was a **pooled
cross-tier median over 613 observations**, and it is **wrong in magnitude and wrong in direction.**

* **No tier shows a 4× close premium. Not one.** The largest is low-priced at **1.03×** (index ETFs: 1.01×, corrected from 1.49×); mega caps are
  **cheaper** at the close (0.75×) and the three illiquid tiers are flat to within 4%.
* **The OPEN is the expensive instant, not the close.** Large caps cost **5.519 bps at 09:45 against
  1.821 at 15:50 — the open is 3.0× the close.** Every liquid tier is monotonically cheaper through
  the session until a small uptick into the last ten minutes.
* Re-pooled across these five tiers the curve is **~1.84 bps at 14:30 versus ~1.82 at 15:50 — a ratio
  of 0.99×, not 3.96×.** The old 6.42 is most consistent with **uneven per-instant tier coverage** in a
  613-observation sample: drop a few liquid quotes at one clock time and the pooled median jumps a
  tier. This is the composition-shift failure mode, and it is why this table is tiered.

**Use the tier row, never a pooled number.** A pooled intraday median has no referent: it describes a
portfolio nobody trades, and its value is set by which tiers happened to answer at that instant.

⚠ **Two prior cost figures are RETRACTED (doc 298):**
- **"7.2 bps for liquid names / index ETFs" had no artifact behind it.** It was never measured; doc 297's
  own `_atk7_roll.json` holds 0.42–3.57 bps and no 7.2. True index-ETF cost is **0.51 bps — 14× lower.**
- **"213.7 bps"** (post-close NBBO snapshot, n=20) was already retracted as ~3.5× overstated.
- The **Roll estimator** underneath every remaining cost number is measured at 0.56× truth on thin names,
  3.5× truth on SPY, and returns an undefined (≤0) estimate on 29.5% of 463,183 liquid ticker-days. One
  doc-298 generator's cost script coded a failed estimate as 0.0 bps and carried the zeros into a median,
  producing a "monotone cost ladder" that mapped estimator failure rather than cost. **Roll numbers are
  provisional until rebuilt from NBBO.** Historical equity NBBO is available and free — there is no
  excuse for a proxy.

**What the correction changes:** the liquid end is far cheaper than this ledger claimed, so the cost
hurdle for an index-ETF strategy is ~0.5 bps per round trip, not 7.2. The *ratio* that condemns the
gapper universe survives and widens (≈120× rather than 42–62×), but the case for abandoning that
universe rests on its measured **−2.041%/ticket** (§3), not on a cost figure. Cost explains *why*; the
per-ticket measurement is the *evidence*.

## 3. The distance statement (doc 297 — measured in bps/day against the 10 bps/day bar)

| measured configuration | value | basis | status |
|---|---|---|---|
| **the bot's own long universe** | **−1% to −2%/ticket** | doc 297 on `day_aggs`: −2.041%, day-blocked CI [−2.823, −1.226]. doc-298 replication (trial T00032) on independently-pulled Alpaca bars, same window and the same single-day ADV definition: **−0.979%, CI [−1.841, −0.071] — also excludes zero**, n=3,317 vs 4,851 | **CORROBORATED** in sign and significance on two independent sources; magnitude ~2× lower off `day_aggs` (doc 278: "the erratic proxy"). Quote the range, not −2.041%. ⚠ Sensitive to the liquidity filter: a 20-day-average ADV instead of the frozen single-day version moves it to −0.605% with the CI spanning zero — the single-day filter selects day-2-of-a-squeeze |
| current live book (last 11–12 sessions) | **+1.2 bps/day** | CI [−8.2, +10.6]; three independent measurements agree (+1.20/+1.23/+1.48) | flat, not bleeding — a real engineering achievement |
| full history ex the two outlier trades | **−13.8 bps/day** | −$24,037 over 190 episodes | the honest expectancy of the strategy |
| lifetime "+90.6%" | **two lottery tickets** | LASE alone = 81.3% of all P&L, held overnight at 27.4% of equity (5.5× the frozen cap) | **not a performance statistic — do not cite it** |
| cash carry on idle balances | **+1.31–1.63 bps/day** | zero market risk | **13–16% of the bar; the only line on the board whose CI excludes zero in the right direction — and it is not being taken** |
| engineering-addressable execution cost | 2.80–3.10 bps/day | at current turnover | real, bankable, ~28–31% of the bar |
| certified edges | **none** | 0-for-60+ gated hypotheses | — |
| RV-forecast advantage (doc 291) | ~3.6% QLIKE | not a return | Stage-3 VOID at h=1, negative at frozen h=21 (doc 296); family closure recommended |

**The bleed-cut that moved the book from −0.864%/day to +0.015%/day was worth +87.9 bps/day — but most of
it was scale reduction, not engineering, and you can only shrink to zero once. The big lever has been spent.**

**Program-level filter (doc 293, restated doc 297): no candidate family earns a build unless its gross
per-ticket edge plausibly exceeds its round-trip cost (§2b). A family that has never been measured has no
%-of-requirement — quoting one is the error doc 297 caught four separate agents committing.**

## 4. Standing constraints the target does NOT override

1. **The 5% position cap stays frozen** until Pierce changes it in writing. Concentration is a risk
   decision, not an edge (doc-290 §6); on a −EV book it only compounds losses.
2. **NO DEPLOYMENT OR BREADTH INCREASE until a per-ticket net edge has a CI excluding zero** (doc 297).
   Deployment is a sign-preserving multiplier. Measured consequence of ignoring this, on the bot's own
   universe at the 40% deployment **already authorised in `.env`** (`EXEC_MAX_POSITIONS=8` ×
   `EXEC_MAX_POSITION_PCT=0.05`): **−90 bps/day at 49.7% annualised volatility**, 3σ day = 9.4%.
   ⚠ **Nothing in the machine prevents this today.** `config_truth_recon.py`'s `_REL_TOL = 1.25` alarms
   only on *over*-deployment, so a ramp from the measured 3.9% toward 40% would be silent by construction.
   The only brake is the operator. A two-sided alarm is on the doc-297 blocking list.
3. **Leverage, margin, and options-approval changes are Pierce's alone.** The target constrains *what we
   hunt*, not *what we risk*.
4. Every doc-275/277 discipline (prereg-freeze, permutation nulls, cross-regime, denominator honesty,
   disclose-don't-retro-tune) applies unchanged. **A lowered target does not lower an evidentiary bar** —
   and doc 297 records the specific hazard that a lowered bar creates: it converts unmeasured *ceilings*
   into quoted *capabilities*.

## 5. What the arithmetic admits (the honest search space)

The old blanket pre-filter — "daily-bar signals on cash equities at 5% cannot arithmetically reach the
target" — **is struck** (doc 297): it was an artifact of the single-ticket framing. It is replaced by the
condition that actually binds:

> **A family is admissible only if its gross per-ticket edge plausibly exceeds its round-trip cost (§2b),
> measured in the universe it would actually trade.**

Under that condition the low-float gapper universe is inadmissible — but on its **measured −2.041%/ticket
outcome** (§3), not on a cost figure. Its cost line (~61 bps, Roll) is itself provisional pending the
true-NBBO rebuild; do not cite 213.7 bps here or anywhere (retracted, §2b). The search moves to
instruments where cost is small relative to the target — index ETFs at 0.51 bps, liquid equities at
1.5–2.0 — or to return sources that are not per-ticket at all (carry on idle balances).

**Un-filtered ≠ reopened.** Removing an arithmetic pre-filter is permission to run a $0 kill test. It is
not a finding, it confers no expected value, and the prior remains the program's 0-for-60 base rate.
