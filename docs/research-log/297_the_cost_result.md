# DOC 297 — THE COST RESULT: why sixty null documents were one arithmetic all along

**Verdict in TARGET.md units: the new +0.1%/day floor (10 bps/day) is arithmetically real and it
reopens nothing. The book stands at +1.2 bps/day (CI [−8.2, +10.6]) — flat, not bleeding — against a
strategy whose honest expectancy is −13.8 bps/day, in a universe that measurably loses −2.041% per
ticket and costs 61.3 bps to turn against a 25–58 bps/ticket requirement. The toll equals the prize.
That explains ~60 null ship-docs as one cost-structure result rather than sixty independent failures
of imagination — and the best replacement anyone could find (an overnight ETF sleeve) was verified,
replicated, and then killed: it is beta plus T-bill interest, and buy-and-hold beats it 4-for-4.**

Session date: 2026-07-28. 12-agent assessment fleet + 5-agent verification fleet, ~3.1M tokens.
Artifacts: `data/research/doc297/`.

**Everything below that came from the assessment fleet was put through a verification fleet, and the
verification changed four of the assessment's headline numbers. Both are reported.**

---

## 1. The directive, and the correction it forced

Pierce set the floor at **+0.1%/day, ambition to exceed** (was 0.5%/day, doc 293). `docs/TARGET.md`
is rewritten accordingly. Two things had to change beyond the number.

**First, the old per-ticket table was wrong — and was wrong under the old bar too.** It computed
"+10.0%/ticket net" by assuming one 5% ticket per day. Measured turnover is 3.9–17.2% of equity per
session. The honest requirement is 25–183 bps/ticket depending on regime.

**Second — and this is the finding that matters — the correction changes no verdict.** The governing
identity is:

```
account daily return  ≡  deployment × turns × (net per-ticket return)
```

Deployment and turns appear on the **requirement** side only. They are sign-preserving multipliers.
They cannot make a negative edge positive, and the same multiplier that shrinks the requirement also
scales what is actually earned. Verified against realised dollars: post-LASE the identity predicts
−37.07 bps/day against −36.18 realised.

Four independent agents in this session derived "the requirement falls 18×!" and treated it as good
news. It is not news at all. It is a change of units.

## 2. What the book actually is

Every number below is broker truth from `data/reports/eod_*.json` (62 sessions) and the decision
journals. **`data/broker_truth/*.parquet` is stale — it ends 2026-04-29** and any analysis anchored
there describes a book that no longer exists.

| fact | value |
|---|---|
| lifetime "+90.6%" | **two lottery tickets.** LASE alone = 81.3% of all P&L, held overnight at 27.4% of equity — a 5.5× breach of the frozen 5% cap |
| history ex those two trades | **−$24,037 / −13.8 bps/day** over 190 episodes |
| decline phase 06-03 → 07-09 | −0.545%/day, CI [−0.882, −0.207] |
| **current regime 07-09 → 07-28** | **+1.2 bps/day**, CI [−8.2, +10.6], σ 15.9 bps/day — flat |
| trading funnel (16 July sessions) | 457 tickers evaluated → 166 BUY intents → **31 ordered** → 31 filled |
| what throttles it | the **catalyst gate**: 1,080 blocks on `catalyst_type=NONE`, +122 on weak news |
| internal `daily_pnl` vs broker equity | **−$43,866 over 75 sessions**, mean abs error **$1,408/session** |

That last row deserves its own sentence. **The target is $190/day and the internal P&L instrument's
typical error is $1,408/session — seven times the signal.** No learning loop can run on that series.
Note precisely what this is and is not: the *broker* record is clean (§7.2 — $0.63 unattributed over
110 sessions). It is the bot's own `session_reports.daily_pnl` field that cannot be trusted, which is
why §8 bans it as a gate input rather than calling for a reconciliation project.

The bleed-cut of docs 282/286 was worth **+87.9 bps/day** and is real. But most of it was achieved by
*shrinking* (time-weighted capital at risk is now 0.127% of equity), not by engineering. **You can
only shrink to zero once.** The residual engineering-addressable cost is 2.80 bps/day.

## 3. The cost result

This is the session's one genuinely new and correct insight — **after the verification fleet corrected
it.** The assessment led with 213.7 bps. That number is a post-close NBBO snapshot on 20 tickers and
overstates the truth by 3.5×. The honest figure, from the intraday Roll estimator on the names the bot
actually trades, is **61.3 bps**.

| universe | round-trip cost | requirement at plausible breadth | viable? |
|---|---|---|---|
| the bot's low-float gappers | **61.3 bps** (Roll, traded names) | 25–58 bps/ticket | **no — the toll equals the prize** |
| liquid names / index ETFs | ~~7.2 bps~~ → **0.51 bps** (see below) | 25–58 bps/ticket | yes, *if* an edge exists |

> **SUPERSEDED BY DOC 298 — the liquid line was wrong and unsourced.** The 7.2 bps figure in this table
> had no artifact behind it; doc 297's own `_atk7_roll.json` holds 0.42–3.57 bps and no 7.2. Rebuilt from
> **true NBBO** (which is free and available — the Roll proxy was never necessary): index ETFs cost
> **0.51 bps** round trip, mega caps 1.55, large caps 2.04. The liquid end is **14× cheaper** than this
> document claimed. The Roll estimator underneath the remaining numbers is 0.56× truth on thin names and
> 3.5× on SPY, so the 61.3 bps gapper figure is itself provisional. See `docs/TARGET.md` §2b.

Liquid instruments are **≈120× cheaper to turn** than the gapper book (revised up from 42–62× by the
doc-298 rebuild) — that ratio is the durable finding, and the correction strengthens rather than weakens
it. What the correction *does* change is the hurdle a liquid strategy must clear: ~0.5 bps per round
trip, not 7.2.

**But the correction matters for how the case is made.** At 213.7 bps the cost argument alone condemned
the universe. At 61.3 bps it does not quite, on its own. **The case for abandoning this universe rests
on the −2.041%/ticket measurement below — cost explains *why*, the measurement is the *evidence*.**
Reporting it the other way round would have been exactly the ceilings-as-capabilities error this
document warns about, committed by me.

Alongside it, what this document called the most robust measurement the program owns:

> **The bot's own long universe loses −2.041% per ticket.** Day-blocked bootstrap CI95
> [−2.823%, −1.226%], n=4,851 stock-days over 586 sessions, 64.8% of sessions negative, −2.52% after
> dropping the ten most extreme days, and negative with a zero-excluding CI in **2024, 2025 and 2026
> separately**.

> **DOC-298 REPLICATION (trial T00032) — CORROBORATED in sign and significance, magnitude ~2× high.**
>
> Re-run on independently-pulled Alpaca split/dividend adjusted daily bars, same window, using this
> document's **exact** universe definition (`adv = prev_close × prev_volume`, a single prior day):
> **mean −0.979%, day-blocked CI95 [−1.841, −0.071] — excludes zero**, n=3,317.
>
> So the direction and the significance hold on independent data. What does not hold is the magnitude:
> **−0.98% here versus −2.04% from `day_aggs`**, on 3,317 rows versus 4,851 — the `day_aggs` universe
> is 46% larger and fades twice as hard, which points at source composition (doc 278 already called
> `day_aggs` "the erratic proxy" and had to rebuild its Q1). Quote **−1% to −2%/ticket**, not −2.041%.
>
> ⚠ **A correction to the correction, recorded because the error is instructive.** My first pass
> reported this as a *replication failure* (−0.621%, CI spanning zero). That result came from my having
> silently changed the liquidity filter to a 20-day average dollar volume. Switching back to this
> document's single-day definition restores the negative CI. **I introduced a variant and reported the
> difference as evidence about the data** — the exact doc-296 failure class this program keeps
> catching, committed by the person auditing it. The one-day filter matters because it conditions on
> *yesterday* having been the high-volume day, which for low-float names selects day-2-of-a-squeeze:
> a genuinely different and harder-fading population than names with sustained liquidity.
>
> Net effect on §11's "stop searching this universe": **unchanged.** The effect is negative with a
> zero-excluding CI on two independent data sources under the frozen definition.

Separately, trial T00031 established that the claim cannot be tested before 2024 at all: qualifying
gapper-days run 63–127/yr across 2016–2019 against ~1,940/yr in 2024–2026, so the pre-2024 window is a
different population rather than an out-of-sample check.

## 4. The bar change reopens nothing

The adversary went family by family. Every candidate the re-score called "revived by the lower bar"
falls into one of two classes:

**(a) Triage ceilings with no measurement behind them.** Doc 293 says in its own text: *"0 kill tests
yet run."* The figures "4–8% of requirement" (turns-compressor), "12.5% ceiling" (LETF), "~70%"
(SEVP) are arithmetic chains recomputing a proposal's own assumptions. They have no n, no CI, no
out-of-sample. **Multiplying a ceiling by 5 does not create evidence.** Un-filtered ≠ reopened:
removing an arithmetic pre-filter is permission to run a $0 kill test, not a finding.

**(b) Measurements that die under an honest denominator.** The "#1 revival" — doc-251's liquid gap-up
fade — was re-measured with day-clustering:

| statistic | naive (doc 251) | day-blocked |
|---|---|---|
| mean | −0.413% | **−0.126%** |
| CI95 | [−0.500, −0.326] **excludes 0** | **[−0.345, +0.090] — spans 0** |
| within-day intraclass ρ | — | 0.353 (design effect 6.06, SE inflates 2.46×) |
| sessions negative | — | **49.4% — a coin flip** |
| 2026 sign | — | **positive (flips)** |

Reopening it would be self-deception *caused by the lowered bar*: at 0.5%/day nobody would have looked
twice. And the Kalshi shadow — the one family with real forward data — is **failing**: Brier advantage
−0.1216, CI [−0.171, −0.072], excluding zero in the wrong direction.

**The hazard is now documented and live in this session's own artifacts:** four independent agents
converted doc 293's unmeasured "4–8% of requirement" into "a validated 0.40–0.80%/ticket capability,"
and three named the doc-251 fade the top revival on a CI that dies the moment you cluster by day.
That is the failure mode, caught in the act.

## 5. The deployment trap, quantified

Breadth is the lever the new arithmetic seems to hand you. It is a trap, and the machine will not stop
you from pulling it.

| deployment | account mean | daily σ | annual vol | sessions to certify +10 bps/day |
|---|---|---|---|---|
| 3.9% (measured) | −8.8 bps/day | 30.5 bps | 4.8% | 73 |
| 17.2% (exec audit) | −38.7 bps/day | 134.6 bps | 21.4% | 1,421 |
| **40% (already authorised in `.env`)** | **−90.0 bps/day** | 312.9 bps | **49.7%** | 7,687 |
| 100% | −225.0 bps/day | 782.3 bps | 124.2% | 48,048 |

Three compounding reasons breadth does not save you:

1. **Correlation.** 20 gap-up names are **2.6–7.5 effective bets**, not 20. On 7.7% of sessions more
   than 80% of the universe moves the same way. There is no cell where breadth solves the problem:
   the liquid universe has low σ and high ρ (0.35); the bot's universe has low ρ (0.088) but 20.46%
   per-name daily σ.
2. **Capacity.** "20 concurrent tickets" is unavailable on **83–98% of sessions**. Median names/day
   is 7–8.
3. **Certification cost.** Certifying against a *fixed absolute bar* gets quadratically harder as σ
   grows: n ∝ (σ/Δ)². More names per day helps; more deployment does not. The two were conflated
   everywhere in this session.

Which produces the cleanest statement of the whole problem:

> **Certification is cheap only in the regime where the target is unreachable, and the target is
> reachable only in the regime where certification is expensive.**

The book's current σ is 15.9 bps/day. Earning 10 bps/day on it would be **Sharpe 10**. At Carver's
audited 12-year live Sharpe of 0.80, +0.1%/day requires 31.5% annual volatility.
**+0.1%/day is a top-decile hedge-fund outcome, not a modest one.**

⚠ **`EXEC_MAX_POSITIONS=8 × EXEC_MAX_POSITION_PCT=0.05` = 40% deployment is live in `.env` today, and
`config_truth_recon.py`'s `_REL_TOL = 1.25` alarms only on *over*-deployment.** A ramp from 3.9%
toward 40% would be silent by construction. The only brake is the operator.

## 6. The best replacement candidate: replicated, then killed

The external sweep's top find was the **overnight/intraday decomposition of index ETFs** — hold SPY
from close to open, in cash intraday — claimed at 4.8–5.0 bps/day net, on capital this book already
leaves idle every night. It was the one candidate that looked like half the bar.

**The replication succeeded, emphatically.** Rebuilt from our own `minute_aggs`, priced at every print
a real order could obtain, n=625 consecutive pairs. Gross bps/day:

| exit print | SPY | QQQ | IWM | DIA |
|---|---|---|---|---|
| 09:30 auction open | 6.13 | 8.87 | 6.94 | 2.74 |
| close of 09:35 | 5.51 | 7.64 | 7.15 | 3.29 |
| close of 10:30 | 6.57 | 8.13 | 6.92 | 5.00 |

90–102% of the effect survives a five-to-sixty-minute delay in the exit, and the entry leg is equally
insensitive. It is not an auction-print artifact and not an execution-timing illusion. Three
independent constructions agree.

**And it is still not a strategy.** Five measured grounds, every one on our own warehouse:

1. **It double-counts the cash sleeve.** 1.566 of the 4.769 net bps is simply T-bill interest. The
   overnight sleeve and "cash carry" are *the same money*, not two additive sleeves.
2. **Excess of cash, in the decision-relevant window: 1.03 bps/day, CI spans zero** — and negative for
   IWM and DIA.
3. **46–59% of the gross is beta × a bull sample.** In a flat tape it nets 0.41–2.08 bps; in a
   2022-style bear it is negative.
4. **Buy-and-hold beats it** on total return in 4/4 ETFs in both windows, and on Sharpe in 4/4 in 2025+.
5. **The sleeves are not uncorrelated.** Effective independent bets: **1.50** across four ETFs; 1.75
   across {overnight equity, short vol}, falling to **1.31 in a crisis**. All the diversification comes
   from the cash leg, which earns 1.70 bps/day and can never earn more.

And the constraint no engineering removes:

> **Certifying that the SPY overnight sleeve beats a T-bill takes 3,079 sessions (12.2 years) at the
> full-sample estimate, and 34,806 sessions (138 years) at the 2025+ estimate. Certifying that it hits
> 10 bps/day is impossible at any sample size — the point estimate is already 5.2–9.5 bps below it.**

The proposed rebuild's own design requirement — "each sleeve independently forward-certified" — is
**unsatisfiable by construction** for the sleeves it proposed. This is the deepest lesson in the
document: *the honest certification standard, applied to small real effects, rules out small real
effects.* A 1 bps/day edge is not bankable by a program that requires proof.

## 7. What is actually real

1. **The account is flat, not bleeding.** Three independent measurements agree (+1.20, +1.23, +1.48
   bps/day). A genuine engineering achievement; it makes the book cheap to keep alive.
2. **The books are clean.** The scariest number in the assessment — "29% of net equity change on
   zero-fill sessions, top-2 unattributed days = 145% of all net gain" — was **refuted**. It was an
   artifact of dating Alpaca's 00:00-UTC `portfolio_history` rows by UTC date. **Truly unattributable
   equity motion is exactly $0.00, and the whole account reconciles to the public tape with a $0.63
   residual over 110 sessions.** The bookkeeping this program spent docs 237/285/286 fixing is fixed.
3. **Cash carry** is +1.31–1.70 bps/day at zero market risk — 13–17% of the bar, the only line with a
   CI excluding zero in the right direction. But per §6.1 it cannot be *added* to an overnight sleeve;
   it is the floor, not a sleeve.
4. **The cost-vs-universe ratio** (§3): liquids are 42–62× cheaper to turn. A search-direction fact.

### The entitlements correction — real, but not the unlock it looked like

Verified by direct read-only GETs: the **paper** account has options **level 3**, **5,249 shortable
names**, 4× margin, crypto, overnight sessions; Alpaca serves full option chains (AAPL: 3,530
contracts in 4 calls, 0.99s) **with OPRA NBBO and free greeks/IV**, at 10,000 calls/min.

And the error is bigger than claimed: **Polygon was never rate-limited to 5/min either** — measured at
**708/min, zero 429s**. Doc 293's cited entitlement probes contain no rate-limit test at all. The
20-hour, 4,460-call, 98-tranche multi-night drip of docs 293–296 was **self-imposed**. The equivalent
rebuild on Alpaca measures at ~6 minutes for 151 names.

Three sobering qualifications, all verified:
- **Paper ≠ live.** The same key returns **HTTP 401** against the live host. Live entitlements are
  unmeasurable from here and remain Pierce's to establish.
- **Historical option NBBO does not exist** on this plan (404 on every documented endpoint shape; only
  *latest* quotes are served). Stage 4's blocker is real and stands.
- **The short-door tombstone is not overturned** — the shortable set does not reach the universe the
  tombstone was measured on.

## 8. What "living and breathing" has to mean here

The operator asked for a system that continuously learns and feeds itself. The honest engineering
answer is that **such a system is a multiple-testing machine, and this program has already measured
its own inference layer failing**:

- Doc 275 certified the family-wide multiplicity gate and issued **no certificate**. At deployed scale
  it has **8% power** against a real signal and lets a planted money-leak worth +0.9%/trade through.
- Doc 276 measured the self-audit layer at sensitivity 0.67 — below its own pre-registered 0.75 floor
  — and found five defects on the "clean" controls. Its blind classes are OMIT and S1-recompute:
  exactly what an autonomous loop generates at volume.
- Doc 296 is the existence proof at the individual level: a hash-frozen prereg specifying h=21 **in
  prose** was executed by a runner coded at h=1. The hash covered the words. Every gate passed.

So the organism is buildable, but **its metabolism must be evidence, not capital.** Eight invariants
must be mechanically enforced; anything less and "autonomous learning" means "automated p-hacking at
machine speed":

1. **Machine-readable trial counter** feeding the promotion threshold — *built this session, §9*
2. Executable prereg fixture (doc 296 rule) — prose hashes are void
3. Point-in-time CI guard asserting `features(data[:t]) == features(full)[t]` for every feature
4. **No self-promotion.** A loop may propose, freeze, collect and report. It may never promote.
5. Broker-truth-only reward; `session_reports.daily_pnl` banned as a gate input
6. Re-tune = kill, extended to any change of code, target, universe or horizon after arming
7. **Two-sided deployment alarm** (§5) — currently one-sided
8. Death dates on every collector, structurally

## 9. What was built this session

**`scripts/trial_registry.py` — the multiplicity counter.** Append-only, hash-chained, tamper-evident.
Every hypothesis registers before data is touched; the promotion bar is computed *from* the trial
count rather than chosen after seeing the result. `promote()` raises by design. It reproduces the
Bailey–López de Prado expected-maximum-Sharpe figures independently (N=35 on 4y → 1.068 vs the
adversary's 1.07; N=1000 → 1.628 vs 1.63).

The bar it computes, which the program has never applied to itself:

| trials run | 1yr sample | 2yr | 4yr | 10yr |
|---|---|---|---|---|
| 1 | 0.52 | 0.37 | 0.26 | 0.16 |
| 35 | 2.14 | 1.51 | 1.07 | 0.68 |
| **60** | **2.35** | 1.66 | 1.17 | 0.74 |
| 1000 | 3.26 | 2.30 | 1.63 | 1.03 |

**At this program's ~60 gated hypotheses on ~1 year of data, the null-expected best Sharpe is 2.35.**
A strategy showing Sharpe 1.2 is indistinguishable from the best of sixty coin flips.

**A live safety defect, fixed and verified.** `DeterministicTechnicalAgent` computed its stop as
`price − 1.5×ATR(14)` with no floor. On low-float gap-ups ATR-14 routinely exceeds the price, so the
stop went **negative** — unplaceable at the broker, so the bracket never attaches and the position
rides naked. This is the doc-281 "vanished stop" class at its source. Ten occurrences Feb–Jul 2026:

| ticker | date | entry | proposed stop |
|---|---|---|---|
| MUU | 07-20 | $29.51 | **−$171.92** |
| JLHL | 07-09 | $5.54 | −$5.10 |
| EHGO | 07-01 | $2.15 | −$0.10 — **reached `filled`** |
| LGHL | **07-28** | $0.99 | −$0.04 |

Fixed with a clamp into a legal band plus a loud red flag. **9 of 10 new tests fail on the old code;
all 10 pass on the new; 125 existing tests unaffected.** (`tests/unit/test_doc297_stop_clamp.py`)

**Record corrections.** Doc 296 and commit `09a700f` were stamped 2026-07-13 by a skewed sandbox
clock; the true date is 2026-07-28, which moves Stage-3's death date to 18 days out and SEVP's to 34.
And the IV collector **has not run since 2026-07-13** — the nightly task was never registered, so
SEVP has been frozen at 205/300 for eleven sessions while its death date approached.

## 10. The honest odds

**P(durable, forward-certified +0.1%/day within 6 months) ≈ 5%** (range 3–8%), decomposed:

| gate | P | why |
|---|---|---|
| a positive, cost-net, CI-excludes-zero per-ticket edge exists and is found | 0.15 | 0-for-60+; the only durable effect in the universe is −2.04%/ticket |
| prereg-frozen and collecting within 4 weeks | 0.5 | executable-fixture rule adds cost; SEVP's collector is still unregistered |
| runs at Sharpe ≥ 4 so 124 sessions suffice | 0.15 | Carver's audited 12-year live record is 0.80; median live-vs-backtest haircut is −73% |
| survives the mandatory skeptic fleet | 0.6 | 1-for-1 against (doc 296 voided the first-ever pass) |

**The most likely way this fails is not the absence of an edge — the program has faced that for sixty
documents. It is that the lowered bar plus enthusiasm converts unmeasured ceilings into quoted
capabilities, and the next six months get spent building against numbers that were never
measurements.** That mechanism is visible in this session's own artifacts (§4).

### What to stop doing

1. Stop quoting "%-of-requirement" for any family that has never been measured.
2. Stop citing the equity curve. +90.6% is two positions.
3. Stop treating the +87.9 bps/day bleed-cut as a repeatable lever. You can only shrink to zero once.
4. Stop proposing breadth increases until a per-ticket net edge has a CI excluding zero.
5. Stop running "N turns divides the requirement" without the cost term — turns divide the
   requirement and *multiply* the cost, and at 26–53 bps/turn the cost term dominates.
6. Stop letting any loop promote anything.
7. Stop measuring progress on `session_reports.daily_pnl`.
8. **Stop searching this universe for a long edge.**

## 11. Pierce items

1. **Two-sided deployment alarm** — the 40%-authorised/3.9%-actual gap is silent today (§5). This is
   the one change I would make before anything else moves.
2. **The incident system has no resolve path at all.** Verified: **1,584 of 1,584 incidents
   unresolved — not neglected, but because no resolve code path exists.** Availability is 85.0%
   (34/40). A system that records 1,584 problems and can close none of them is not observing itself;
   it is accumulating.
3. **The watchdog kills siblings.** Its process matcher `$cmd -match "momentum"` has killed a sibling
   sleeve **six times**, not once. Any multi-sleeve architecture dies on this line of code.
4. **Register the nightly IV collector** — eleven sessions lost; SEVP dies 2026-09-01. Note §7: the
   Alpaca path makes the same rebuild a ~6-minute job, so this may be worth re-planning rather than
   re-scheduling.
5. **Ratify the doc-296 vol-door closure** (still open from last session).
6. Elevated `_doc288_apply_catchup_triggers.ps1` — sixth session pending.
7. **The strategic decision, which is yours alone:** the current universe measurably loses −2.04% per
   ticket and cannot clear the bar at any breadth or deployment. Continuing to trade it is a choice to
   keep that book alive at minimum scale — which, at the current +1.2 bps/day and 0.127% capital at
   risk, is nearly free and buys optionality. The alternative is to move the search to instruments
   where cost is small relative to the target, knowing from §6 that the honest certification standard
   may rule out the small effects that live there. Nothing in this document instructs either choice.

## 12. The honest sentence

The program spent sixty documents asking whether it could predict this universe, and the answer was
always going to be no — not because the signal is hidden, but because the toll to collect it is 8.6×
the prize. That is a real discovery, arrived at honestly, and it is worth more than another null.
The book is flat, the machine is sound, the instruments are sharper than they were this morning, and
for the first time the program can count how many times it has looked.
