#!/usr/bin/env python
"""Seed the stepping-stone archive from ATTEMPTS_LEDGER.md.

Every record below is a transcription of a closure the program already made.
Nothing here re-opens, re-scores, or softens a verdict — the only thing added is
the *discrimination*: for each closure, whether it is a measurement of the
market or a measurement of our own instruments, and if the latter, which
keystone was missing.

Where the ledger states an effect size and interval, it is carried across
verbatim. Where it does not, the fields are left null and the record is written
non-strict. That gap is itself a finding and is reported at the end: a closure
without a recorded measurement cannot be retro-scored quantitatively later, and
most of this program's closures are in that state.

NOTHING IS INVENTED TO SATISFY THE VALIDATOR. That rule was broken in the first
version of this file and caught by the doc-300 audit: two records carried
intervals and counts existing in no artifact anywhere in the repository, added
for no reason other than to clear the REFUTED_BY_NATURE admissibility rule. A
fabricated measurement, in an archive whose whole purpose is to be a trustworthy
record, is worse than a missing one - because the missing one is visible. If a
number is not in the source, the field stays empty and the record stays
inadmissible.

`closed_on` is now SOURCED FROM GIT, not from a guess. ATTEMPTS_LEDGER.md carries
no dates in its text, so the first version of this file invented all 25 - several
of them logically impossible, placing a closure before the document that made it.
The honest source was there all along: the commit that first recorded each row.

Two semantics, and the data says which is which:
  "2026-07-29"    EXACT. The commit added this row when the decision was made.
  "<=2026-07-12"  UPPER BOUND. Commit 03b0432 created ATTEMPTS_LEDGER.md and
                  backfilled 16 closures that already existed in docs 230-292,
                  so its date bounds those closures from above rather than
                  dating them. Narrowing one means reading its cited document.

The "<=" prefix is in the value deliberately. Storing an upper bound in a field
called `closed_on` without saying so is the same over-claim that produced the
fabrications this docstring warns about - a bound is not a measurement, and it
should not be possible to read it as one.

One hazard found while doing this, worth stating because it bit on the first
pass: a family whose ledger row CHANGED STATE has several commit dates, and the
first one dates the wrong event. LETF close-window appears three times - FILTERED
(BLOCKED-AT-$0), then CLOSED, then QUEUED - and its first appearance is
2026-07-12, which is four days before doc 297 set the target its closure was
evaluated against. Dating that record 2026-07-12 would have made the closure
appear to predate the directive it depends on. It is dated 2026-07-29, from the
commit carrying the CLOSED verdict and the 21.07 bps figure. Date the EVENT the
record represents, not the family's first mention.

Run with --check to validate without writing.

Reference: docs/research-log/299_the_epistemic_architecture.md
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.epistemics.archive import Archive, SteppingStone  # noqa: E402
from src.epistemics.closure import Closure, ClosureRecord, Keystone  # noqa: E402

K = Keystone


def _stones() -> list[dict]:
    """The ledger, transcribed. Order follows ATTEMPTS_LEDGER.md."""
    return [
        dict(
            family="Per-name directional prediction (price/momentum, all horizons/universes)",
            claim="A per-name directional signal exists in price and momentum features.",
            closure=Closure.REFUTED_BY_NATURE,
            rationale="No edge across every horizon and universe tested. The most "
                      "heavily attacked family in the corpus.",
            # doc 250:8, verbatim: "Best cross-regime (pooled 2024+2025) is
            # **-0.04%, 95% CI [-0.3, +0.2]** - indistinguishable from zero."
            # Supports the class as effect-ABSENT: a tight interval bracketing zero,
            # and the same sentence adds that after "a realistic ~1% small-cap
            # round-trip spread/slippage, the basket is clearly negative".
            # Two caveats carried from the extraction, both conservative for the
            # closure: "Best" is the MAXIMUM over 7 exit policies, i.e. a favourable
            # extremum; and the cell is an UNCONDITIONAL broad-basket mean (all
            # 10,254 candidates, equal-weight), not a per-name-selection endpoint -
            # the selection-side refutations live in docs 245-249, 251, 252, 258.
            # n_obs absent on purpose: doc 250 states a 10,254 corpus, but the quoted
            # cell excludes 2026 and covers only candidates with reconstructable
            # intraday paths - a runtime subset printed by the basis script and never
            # recorded. Attaching 10,254 would be the corpus-for-subset misattribution.
            # ⚠ THE INTERVAL IS TOO NARROW, and the source says so by accident.
            # doc 250:50 describes the method as "day-block CIs", but
            # scripts/rocket_basket_exits_doc250.py:81 does a FLAT i.i.d. resample:
            #   rng.integers(0, len(x), len(x))  on a plain row vector, no session
            #   grouping, called at line 90 on the pooled 2024+2025 rows.
            # Gapper candidates cluster heavily within sessions - which is exactly
            # why the document claims day-blocking - so an i.i.d. bootstrap
            # understates the interval. At doc 278's measured ~2.5x SE inflation the
            # honest interval is nearer [-0.67, +0.58] than [-0.30, +0.20].
            # The class still holds: the interval brackets zero either way, and the
            # same doc-250 sentence says the basket is "clearly negative" after a
            # realistic ~1% small-cap round trip. What does NOT survive intact is the
            # ADEQUATE-POWER leg. The stated interval is carried here because it is
            # what the source publishes; it must not be treated as denominator-honest.
            # This is doc 296's prose-versus-code failure class in a document that
            # predates the rule. Found by the doc-301 verifier, confirmed by reading
            # the script. NOT silently recomputed - that would change a published
            # number without re-running the analysis.
            effect=-0.04, ci=(-0.3, 0.2),
            docs=["250-261", "283"],
            closed_on="<=2026-07-12",
        ),
        dict(
            family="Exit-timing optimization (AUC to dollars)",
            claim="A better exit rule converts classifier AUC into realised dollars.",
            closure=Closure.REFUTED_BY_NATURE,
            rationale="The edge REVERSES SIGN out of period. Doc 235 Stage 2, primary "
                      "endpoint (pooled 2024+25, top-2, ADV>=$1M, 30-min), Mode A frozen "
                      "2026->OOP: -1.13% CI [-1.70, -0.56], excludes zero; every primary "
                      "cell negative and CI-excluding-zero, and it survives a Bonferroni "
                      "98.75% interval. Doc 235's own words: the edge does not merely "
                      "vanish, it reverses sign.",
            # Was filed REFUTED_BY_COST with the rationale "the frictions are what
            # consumed it" and a realised_fill_distribution keystone, i.e. revivable on
            # "a cheaper venue, instrument, or size regime". That cost claim appears
            # NOWHERE in the ledger - it was the transcriber's inference - and doc 235,
            # one of this record's own cited documents, measured a sign reversal rather
            # than a friction drag. A sign reversal is nature. Reclassified and the
            # measurement carried across, which also makes the record admissible.
            # Caught by the doc-300 audit (seed-fidelity/eig-04).
            effect=-1.13,
            ci=(-1.70, -0.56),
            # n_obs deliberately ABSENT, which keeps this record inadmissible. Doc 235
            # gives a corpus of 632,020 rows / 10,786 ticker-days, but the primary
            # endpoint is a SUBSET of it (pooled 2024+25, top-2, ADV>=$1M, 30-min, so
            # roughly 2 selections x 493 sessions) and the document does not state that
            # cell's n. Writing 10,786 would attach the corpus size to a subset
            # measurement; writing 986 would be arithmetic of my own. Narrowing it means
            # reading doc 235's runner. Until then it stays empty - the same rule that
            # removed the fabricated intervals applies to the one I would rather have.
            docs=["230-235"],
            closed_on="<=2026-07-12",
        ),
        dict(
            family="Loss-cap / re-framed exit variants (b1/b2, c2)",
            claim="Re-framing the exit as a loss cap recovers the edge exit timing lost.",
            closure=Closure.REFUTED_BY_NATURE,
            rationale="No edge in any variant.",
            docs=["236", "239"],
            closed_on="<=2026-07-12",
        ),
        dict(
            family="Rocket ex-ante selection (tick/OFI/tape microstructure)",
            claim="The right tail is identifiable before the move from microstructure.",
            closure=Closure.REFUTED_BY_NATURE,
            rationale="Right tail is ex-ante random; the cross-regime money gate fails.",
            # -4% mean EOD-from-09:50 forward return, CI [-6.8, -1.1], excluding
            # zero. Wrong-signed and CI-separated, which is what the class requires.
            # n_obs not stated for the cell.
            effect=-4.0, ci=(-6.8, -1.1),
            docs=["BET#3", "242-248"],
            closed_on="<=2026-07-12",
            # Anomaly removed. It read: "Right-tail membership is random ex-ante but
            # strongly autocorrelated ex-post - the asymmetry itself was never
            # explained." Two problems: no source in the ledger or the cited documents,
            # and its "never explained" premise is answered in doc 254, which measured
            # the prior-runner variance effect on a clean forward test. An unsourced
            # editorial anomaly in an archive whose purpose is trustworthy record-keeping
            # is the same defect class as a fabricated interval, just less numeric.
            # Caught by the doc-300 audit (seed-fidelity/eig-07). The other six anomalies
            # check out against their documents.
        ),
        dict(
            family="Deep-set raw-tape screens / RL scalper",
            claim="A learned function of the raw tape transfers across names and days.",
            closure=Closure.REFUTED_BY_NATURE,
            rationale="No transferable signal.",
            # -6.6 percentage points of top-5% traded-name capture, CI [-10, -2.6],
            # excluding zero. Wrong-signed and CI-separated.
            effect=-6.6, ci=(-10.0, -2.6),
            docs=["248", "256-257"],
            closed_on="<=2026-07-12",
        ),
        dict(
            family="Catalyst/news amplification (PEAD Stage A/B, filing text)",
            claim="Catalyst text amplifies an otherwise weak directional signal.",
            # RECLASSIFIED NATURE -> UNDERPOWERED (doc 301). The recovered PRIMARY
            # endpoint is POSITIVE in both samples and its interval spans zero, so it
            # cannot support "measured with adequate power; the effect is absent or
            # wrong-signed". doc 260:16 verbatim:
            #   "| LLM L-S median **f10** | +2.82% | **+1.35%** | positive but
            #    **halved**; n~93/tranche -> **CI crosses 0** (s7 CI[-0.8,+5.1]) |"
            # The ledger's "gate fails" is accurate - the pre-registered gate did
            # fail - but a failed gate on a positive, zero-spanning estimate at
            # n~93/tranche is absence of evidence, not evidence of absence. This is
            # precisely the mis-filing doc 275 made acceptance-test power mandatory
            # to prevent, and it was sitting in the archive unnoticed.
            closure=Closure.UNDERPOWERED,
            rationale="Pre-registered gate FAILED, but on a POSITIVE point estimate "
                      "whose interval spans zero: +2.82% in-sample, +1.35% "
                      "out-of-sample, s7 CI [-0.8, +5.1], n~93/tranche (doc 260:16). "
                      "A gate failing is not an effect being absent. Filed "
                      "UNDERPOWERED because n~93/tranche with a zero-spanning "
                      "interval is what the evidence actually establishes. "
                      "doc 304: the event anchor carries look-ahead (the universe "
                      "median f10 is -1.74% on the argmax anchor and -0.40% on a "
                      "knowable first-spike anchor), so the historical short-leg "
                      "magnitude is an upper bound. The short leg is NOT behind a "
                      "borrow wall: 80.7% of eligible names are easy-to-borrow "
                      "(doc 260 assumed the wall and never measured it). The clean "
                      "test is forward, drafted as 304b and not frozen.",
            effect=2.82,
            ci=(-0.8, 5.1),
            keystones=[K("n_obs_sufficient",
                         "enough non-overlapping observations to power the test",
                         "n~93/tranche cannot separate +2.82% from zero")],
            docs=["259-260", "301", "304"],
            closed_on="<=2026-07-12",
        ),
        dict(
            family="Coiled-catalyst / prior-runner patterns",
            claim="Names that ran before run again from a coiled setup.",
            closure=Closure.REFUTED_BY_NATURE,
            rationale="No edge on a clean forward test — the strongest design in this "
                      "group, which is why the null is credible.",
            docs=["254"],
            closed_on="<=2026-07-12",
        ),
        dict(
            family="Multi-day candidacy / dilution signal",
            claim="Dilution risk is predictable and tradeable multi-day.",
            closure=Closure.REFUTED_BY_NATURE,
            rationale="No edge.",
            # doc 258: -1.8% median 10-day forward close. Point estimate only - no
            # interval is stated, and the extraction flagged the cell as SECONDARY.
            # Recorded because the sign is sourced; the record stays inadmissible
            # because the class needs an interval and the source has none.
            effect=-1.8,
            docs=["258"],
            closed_on="<=2026-07-12",
        ),
        dict(
            family="Binary-event handicapping (catalyst calendar)",
            claim="Scheduled binary events can be handicapped better than the market.",
            closure=Closure.REFUTED_BY_ARITHMETIC,
            rationale="Pilot fails feasibility before any edge question is reached.",
            keystones=[K("lower_requirement",
                         "a re-scoped account-level target",
                         "the feasibility arithmetic at the standing target")],
            docs=["261"],
            closed_on="<=2026-07-12",
        ),
        dict(
            family="Blanket exit-posture flip (incl. overnight carry)",
            claim="Flipping the exit posture across the book improves realised P&L.",
            closure=Closure.REFUTED_BY_NATURE,
            rationale="Null-to-negative; the overnight arm is the one robustly negative "
                      "component. Doc-69's '+$23K' evaporated on a clean book.",
            docs=["280"],
            closed_on="<=2026-07-12",
            anomalies=["Overnight carry is the single most robustly negative arm found "
                       "anywhere in the program — a durable effect pointing the wrong way."],
        ),
        dict(
            family="Short door (borrow-priced, full census)",
            claim="The short side of the gapper universe is profitable net of borrow.",
            closure=Closure.REFUTED_BY_COST,
            rationale="Net negative after borrow on a full-census measurement. "
                      "TOMBSTONED. Magnitude corrected by doc 288 (the doc-284 figure "
                      "was overstated roughly 2x); the tombstone stands either way.",
            keystones=[K("short_locate_access",
                         "the ability to borrow and short at this broker",
                         "0-for-94 locate attempts; shorting is closed on Alpaca"),
                       K("borrow_fee_timeseries",
                         "per-name, per-day borrow cost and availability",
                         "pricing the door at any other broker")],
            effect=-0.6,
            docs=["283-284", "288"],
            closed_on="<=2026-07-12",
        ),
        dict(
            family="Cohort-relational selection (within-cohort position)",
            claim="The edge is relational — a name's position within its cohort predicts.",
            closure=Closure.REFUTED_BY_NATURE,
            rationale="Pre-registered channel-capacity test: cohort-to-winner channel is "
                      "approximately empty; the whisper is unbankable.",
            # doc 289: "Relational lift (BOTH - ABS) = -0.019 - negative. Relative
            # representation did not beat absolute." Permutation p_lift = 0.77.
            # NO INTERVAL EXISTS: the extractor grepped all 66 lines of doc 289 for
            # CI / +- / 95% / confidence / interval / bracket notation and got zero
            # hits - the document reports point estimates with permutation p-values
            # only. Nor does any channel capacity in bits exist despite the ledger's
            # framing; the bits language there is qualitative prose.
            effect=-0.019,
            docs=["289"],
            closed_on="<=2026-07-12",
            anomalies=["Attention is conserved and condenses winner-take-all, but is "
                       "DECOUPLED from price. Structure exists in the wrong observable — "
                       "the most interesting unexplained result in the corpus."],
        ),
        dict(
            family="Attention-coupling (16 pre-declared regime cells)",
            claim="Attention flow couples to price within identifiable regime cells.",
            closure=Closure.REFUTED_BY_NATURE,
            rationale="0/32 after correction. The crack is closed.",
            # CONFIRMED BY INDEPENDENT EXTRACTION (doc 301): the measurement this
            # class requires does not exist in any cited document. The primary
            # endpoint is doc 290 section 5's 16-cell pre-declared conditional
            # coupling test, and what it reports is a TALLY - "Zero survive." of
            # "16 pre-declared cells x 2 statistics = 32 tests" - plus a hedged
            # directional description ("~0% of cohorts in nearly every cell (vs ~6%
            # random)"), with the tilde in the source. No per-cell effect estimate
            # with an interval is stated. So the earlier fabrication here invented
            # exactly the thing the corpus does not contain.
            #
            # No effect, interval or n. "0/32 after correction" is a tally of
            # pre-declared regime cells that passed - not an effect size, not an
            # interval, and 32 counts CELLS, not observations. An earlier version
            # recorded effect=0.0, ci=(0.0, 0.0), n_obs=32; a zero-width interval
            # is not an interval and the whole triple was fabricated to clear the
            # REFUTED_BY_NATURE admissibility rule. Removed 2026-09-20. This
            # record is now correctly inadmissible: the closure may well be right,
            # but the ledger does not carry the measurement that licenses the
            # strongest class.
            docs=["289-290", "292"],
            closed_on="<=2026-07-12",
        ),
        dict(
            family="H-LOCAL / micro-regime vector-DB retrieval",
            claim="Local neighbourhoods in feature space carry exploitable micro-regimes.",
            # RECLASSIFIED NATURE -> UNDERPOWERED (doc 301). The frozen G1 gate
            # metric came out POSITIVE - the local model BEAT the global one - and the
            # gate failed on the permutation p-value by a single draw, not on sign.
            # doc 290:21 verbatim: "**G1 (local beats global): FAIL - by exactly one
            # permutation draw** (delta=+0.136, p=0.00995 vs frozen alpha=0.005)",
            # against global GBM 0.175 versus kNN 0.311 out-of-sample Spearman. No
            # interval is stated anywhere. A positive effect one draw short of a
            # frozen alpha is underpowered; the closure may well be right on
            # economic grounds, but this measurement does not license the strongest
            # class. Both original findings stand and are kept in the rationale.
            closure=Closure.UNDERPOWERED,
            rationale="Frozen G1 gate FAILED by exactly one permutation draw "
                      "(delta=+0.136, p=0.00995 vs alpha=0.005) - and on a POSITIVE "
                      "effect, with no interval stated (doc 290:21). The original "
                      "findings stand: the apparent kNN lift was a weak-baseline "
                      "artifact, and roughly four neighbourhoods exist lying on one "
                      "smooth surface. But a positive effect one draw short of a "
                      "frozen alpha is underpowered, not refuted by nature.",
            effect=0.136,
            keystones=[K("n_obs_sufficient",
                         "enough non-overlapping observations to power the test",
                         "p=0.00995 against a frozen alpha=0.005 - one draw short")],
            docs=["290"],
            closed_on="<=2026-07-12",
            anomalies=["Volatility SCALE is predictable (rho ~0.30, attack-survived) "
                       "while direction is not. Durable, positive, and unmonetisable in "
                       "this account — found inside a refutation."],
        ),
        dict(
            family="Low-frequency concentration as an edge substitute",
            claim="Fewer, larger, more concentrated bets substitute for an edge.",
            closure=Closure.REFUTED_BY_ARITHMETIC,
            rationale="Ruled out by arithmetic and risk, not by experiment. Under the "
                      "doc-297 identity, concentration is on the requirement side and "
                      "cannot flip the sign of a negative per-ticket edge.",
            keystones=[K("lower_requirement",
                         "a re-scoped account-level target",
                         "the concentration arithmetic at the standing target")],
            docs=["290", "TARGET.md"],
            closed_on="<=2026-07-12",
        ),
        dict(
            family="Short-tenor (<=9d) RV forecasting vs IV",
            claim="Realised-vol forecasts beat implied at short tenor.",
            closure=Closure.REFUTED_BY_NATURE,
            rationale="Sign-negative.",
            # doc 292: -2.7% incremental QLIKE improvement. Point estimate only; the
            # extraction flagged the cell as SECONDARY and no interval is stated.
            effect=-2.7,
            docs=["292"],
            closed_on="<=2026-07-12",
        ),
        dict(
            family="Passive liquidity provision / maker rebates (~10 hypotheses)",
            claim="Posting passive liquidity earns the maker rebate.",
            closure=Closure.STRUCTURALLY_UNAVAILABLE,
            rationale="Structurally unavailable, not merely unprofitable. Alpaca's 2026Q1 "
                      "Rule 606: 100% non-directed, 100% internalised to wholesalers, "
                      "zero lit-exchange venues. The 20-mils passive rebate is paid to "
                      "Alpaca Securities LLC, not the customer. The one escape hatch "
                      "(Elite DMA Cost Plus) is net -$0.0005 to -$0.0009/share at every "
                      "price this account can reach, and its best tier needs 50M sh/mo "
                      "against a ~5.3M ceiling.",
            keystones=[K("lit_venue_routing",
                         "order routing that reaches a rebate-paying exchange",
                         "earning any maker rebate at all")],
            docs=["298"],
            closed_on="2026-07-29",
        ),
        dict(
            family="Single-name option short-volatility (all defined-risk forms)",
            claim="Single-name variance risk premium is harvestable in defined-risk form.",
            closure=Closure.REFUTED_BY_NATURE,
            rationale="The premium is not there and the cost would eat it anyway. "
                      "Measured single-name VRP -6.5% against an un-winged break-even of "
                      "+6.0%; round-trip cost 28.6-67.9% of net debit on TSLA/AAPL/NVDA.",
            effect=-6.5,
            ci=(-9.9, -3.1),
            n_obs=1175,
            docs=["298"],
            closed_on="2026-07-29",
        ),
        dict(
            family="LETF close-window rebalance-flow harvest",
            claim="Leveraged-ETF rebalance flow is harvestable in the closing window.",
            closure=Closure.REFUTED_BY_ARITHMETIC,
            rationale="The doc-295 $0 blocker was genuinely lifted and the design proved "
                      "WELL-POWERED (3.3-15.1x MDE), contradicting the expectation that "
                      "it was underpowered. It fails on arithmetic: to carry 5 bps/day at "
                      "the only permitted deployment (10 families x 5% cap = 50% gross) it "
                      "must explain R^2 >= 47.6% of day-demeaned close-window return "
                      "variance, against published LETF rebalance-flow estimates of 2-3% "
                      "- a 16-24x shortfall. Annualised Sharpe required 11.67 (residual "
                      "sd 15.06) against a PERFECT-predictor ceiling of 1.27-1.80: "
                      "5.8-9.2x short with denominators matched. Even at R^2=10%, five "
                      "times the published estimates, it is 2.5-2.8x short. "
                      "COST IS NOT THE GROUND: measured all-in round trip is 0.575 bps on "
                      "the primary tier incl. SEC/TAF/CAT (doc 303, 576 NBBO quotes), 25% "
                      "of the 2.268 bps gross ceiling; an independent 1,192-quote doc-298 "
                      "measurement agrees at 1.068 bps blended. "
                      "NOTE: this record previously carried the ground 'ceiling 6.0-8.6% "
                      "vs the >=10% build filter', which doc-298 M4 adjudicated as the "
                      "NON-binding one on 2026-07-29 and asked to have replaced. The "
                      "unapplied amendment caused the doc-299 revival, retracted by doc 303.",
            # doc 306: re-keyed. "lower_requirement" is AVAILABLE (the 5 bps/day target), so
            # `revive` ranked this stone unblocked although the target cut did not move the
            # binding ground. The key is now the evidence the ground actually needs.
            keystones=[K("letf_flow_r2_evidence",
                         "measured evidence that LETF rebalance flow explains >= 47.6% of "
                         "day-demeaned close-window return variance",
                         "the 16-24x shortfall against the published 2-3% estimates; the "
                         "10 -> 5 bps/day target cut already happened and did not close it")],
            docs=["295", "298", "303"],
            # 2026-07-29 (f411a28), the commit carrying the CLOSED verdict and the
            # 21.07 bps figure - NOT 2026-07-12, which is this family's earlier
            # BLOCKED-AT-$0 row. A family whose ledger row changed state has
            # several dates and "first appearance" dates the wrong event.
            closed_on="2026-07-29",
            anomalies=["The only family in the corpus that turned out BETTER powered than "
                       "expected. Worth understanding why the power intuition was wrong."],
        ),
        dict(
            family="Overnight/intraday ETF decomposition (hold index ETFs close-to-open)",
            claim="The overnight component of index ETF returns is a harvestable sleeve.",
            closure=Closure.REFUTED_BY_ARITHMETIC,
            rationale="Replication SUCCEEDED (SPY 6.13 bps gross, robust to exit timing "
                      "09:30-10:30, three independent constructions agree) but the sleeve "
                      "is not a strategy: 1.566 of 4.769 net bps is T-bill interest, "
                      "excess-of-cash is 1.03 bps/day with an interval spanning zero, "
                      "46-59% of gross is beta x bull-sample, buy-and-hold beats it on "
                      "Sharpe 4/4 in 2025+, effective independent bets 1.50. Certifying "
                      "it beats a T-bill needs 3,079 sessions (12.2 yr); certifying 10 "
                      "bps/day is impossible at any n.",
            # doc 306: re-keyed. On "lower_requirement" (available since the 5 bps/day target)
            # `revive` ranked this stone #1 FULLY UNBLOCKED, but no ground of the closure depends
            # on the bar: excess-of-cash spans zero, buy-and-hold wins 4/4, 46-59% is beta.
            keystones=[K("overnight_excess_of_cash_ci_above_zero",
                         "a measured excess-of-cash interval above zero for the close-to-open sleeve",
                         "the 1.03 bps/day point whose interval spans zero; certifying it needs "
                         "~3,079 sessions and 2,692 exist (doc 306)"),
                       K("beats_buy_and_hold_total_return",
                         "the sleeve beating buy-and-hold on total return and Sharpe out of sample",
                         "buy-and-hold wins 4/4 in 2025+; a beta sleeve cannot be an overlay")],
            # effect only. The ledger states "excess-of-cash 1.03 bps/day with CI
            # spanning zero" and does NOT give the bounds. An earlier version of
            # this file carried ci=(-1.0, 3.1), which appears in no artifact
            # anywhere in the repository - it was invented to satisfy validate().
            # Removed 2026-09-20 after the doc-300 audit. The record is weaker
            # without it, and that weakness is the true state of the evidence.
            effect=1.03,
            docs=["297"],
            closed_on="2026-07-28",
            anomalies=["A third of the sleeve's apparent net return was T-bill interest "
                       "double-counted as strategy alpha. Worth auditing whether any "
                       "other measured sleeve carries the same double-count."],
        ),
        dict(
            family="Low-float gapper long universe (the bot's own book)",
            claim="The low-float gapper long universe is profitable per ticket.",
            closure=Closure.REFUTED_BY_NATURE,
            rationale="Measured -2.041%/ticket, day-blocked CI [-2.823, -1.226], n=4,851 "
                      "over 586 sessions, negative in 2024, 2025 AND 2026 separately, "
                      "64.8% of sessions negative, robust to trimming. Round-trip cost "
                      "61.3 bps against a 25-58 bps/ticket requirement. The most durable "
                      "effect the program owns, pointing the wrong way.",
            effect=-2.041,
            ci=(-2.823, -1.226),
            n_obs=4851,
            docs=["250-261", "297"],
            closed_on="2026-07-28",
        ),
        dict(
            family="Single-name RV-vs-IV @30d tenor (Stage 3 + conditional VRP ladder)",
            claim="A realised-vol challenger beats implied at the 30-day tenor.",
            closure=Closure.VOIDED_BY_DEFECT,
            rationale="The unblinded 'pass' is VOID: the runner evaluated h=1 where the "
                      "frozen prereg specified h=21, caught by the mandatory fleet. At "
                      "the frozen horizon the same panel is -4.43% with an interval "
                      "including zero, replicating the doc-292 pilot. Untainted "
                      "certification is no longer possible on this panel; the h=1 "
                      "residual is disclosed-not-certified (IV-noise confound, clean-IV "
                      "half not significant).",
            keystones=[K("executable_prereg_fixture",
                         "a runnable fixture the runner must reproduce",
                         "detecting a spec/runner mismatch before unblinding"),
                       K("clean_iv_surface",
                         "an IV panel free of the doc-296 look-ahead",
                         "an untainted certification on this panel")],
            effect=-4.43,
            docs=["294", "296"],
            closed_on="2026-07-13",
            anomalies=["The first result to pass every frozen gate in the program's "
                       "history, voided by an implementation detail. The pass itself is "
                       "evidence about the gate's reachability, not about volatility."],
        ),
        dict(
            family="Turns-compressor (RV-gated passive LP, intraday capital turns)",
            claim="Compressing capital turns via RV-gated passive fills clears the bar.",
            closure=Closure.REFUTED_BY_ARITHMETIC,
            rationale="Validated at 4-8% of requirement — fails the >=10% build filter at "
                      "honest central assumptions. No kill test was ever run.",
            keystones=[K("lower_requirement",
                         "a re-scoped account-level target",
                         "the >=10%-of-requirement build filter")],
            docs=["293"],
            closed_on="2026-07-12",
        ),
        dict(
            family="Market-neutral 3-sigma residual-reversion spreads on liquids",
            claim="Residual reversion on liquid names is tradeable market-neutral.",
            closure=Closure.ABANDONED,
            rationale="Rejected before build as a closed-family variant (per-name "
                      "directional prediction, price-feature, hedged). Re-opening "
                      "requires doc-289/290-class novelty plus Pierce's sign-off. Filed "
                      "as abandoned rather than refuted because it was never measured.",
            docs=["293"],
            closed_on="2026-07-12",
        ),
        dict(
            family="Adversary NULL-CERT blanket certificate",
            claim="The program can issue a blanket no-edge certificate over the universe.",
            closure=Closure.ABANDONED,
            rationale="Rejected as premature — its shape-(b) bound missed the event-vol "
                      "channel. The Stage-3 dated accept/kill payload was salvaged.",
            docs=["293"],
            closed_on="2026-07-12",
        ),
        dict(
            family="SEVP scheduled-event vol carry (trial T00029)",
            claim="Shorting an ATM straddle from T-1 to T+1 around single-name earnings carries a positive net.",
            closure=Closure.INSTRUMENT_LIMITED,
            rationale="Closed on Pierce's disposition (doc 305) after its frozen death date "
                      "(2026-09-01) passed at 205/300 two-leg events. The IV collector feeding it was "
                      "never scheduled and last ran 2026-07-13. No gate was ever computed, so blindness "
                      "is intact and NOTHING is known about the effect. The frozen structure is a naked "
                      "short straddle, which Alpaca cannot express at any options level; the runner "
                      "still conditioned G2 on realized outcomes. Deadline NOT slid, nothing re-tuned.",
            keystones=[K("executable_prereg_fixture",
                         "a runner that implements the frozen prose, with a fixture",
                         "the conformed runner still conditions G2 on realized outcomes"),
                       K("historical_option_nbbo",
                         "event-window option quotes to cost the trade",
                         "option cost measured on 15 of 205 covered events' names, one non-event day"),
                       K("clean_iv_surface",
                         "synchronous, parity-checked IV legs at T-1 and T+1",
                         "legs were month-start strikes and non-synchronous EOD closes")],
            docs=["293", "294", "296", "304", "305"],
            closed_on="2026-09-22",
        ),
        dict(
            family="Kalshi zero-capital LLM shadow (trial T00028)",
            claim="An LLM's probability forecasts beat Kalshi market prices net of fees.",
            closure=Closure.REFUTED_BY_NATURE,
            rationale="Its own frozen gate (doc 284:30: >=200 resolved; Brier(LLM) < Brier(market) AND "
                      "fee-adjusted day-blocked P&L CI > 0) FAILED at n=237 and at every re-score to n=793. "
                      "Brier LLM 0.2451 vs market 0.1101; the gap is +0.1350 (LLM worse) with a "
                      "forecast-day-blocked 95% CI [+0.1142, +0.1575] over 32 days (doc 305, seed 305). "
                      "The divergence rule lost $22.58 over 631 one-contract trades. The collector then "
                      "went dark on 2026-08-10 (parse failures) and was decommissioned in doc 305.",
            effect=0.1350,
            ci=(0.1142, 0.1575),
            n_obs=793,
            docs=["284", "298", "305"],
            closed_on="2026-09-22",
        ),
        dict(
            family="Rocket-gate conditional exit posture (trial T00027)",
            claim="For gapper entries with entry-time rvol>100 in hour 9 ET, holding to the close beats the BAR-1 exit.",
            closure=Closure.REFUTED_BY_NATURE,
            rationale="Its frozen acceptance test (7f003f6: day-blocked bootstrap of the per-session delta, "
                      "CI lower bound > 0 AND both chronological halves > 0, at n >= 30 gated forward "
                      "sessions) returned NOT PASSED at n=36: mean -$2,032/session, 95% CI "
                      "[-$11,084, +$7,582]; halves +$60,053 / -$133,205; about -69 bps per filled ticket. "
                      "The forward interval EXCLUDES the retrospectively mined +$21,912/session the gate "
                      "was built on. Replicated from the frozen prose, and 5 sessions re-priced from raw "
                      "bars to the cent (doc 305). Frozen status was FAILING-SO-FAR (kill horizon n=60); "
                      "closed early as a futility stop on Pierce's written direction. A pass would have "
                      "needed the next 24 sessions to average about +$21K each.",
            effect=-2031.9967,
            ci=(-11084.34, 7582.18),
            n_obs=36,
            docs=["280", "284", "305"],
            closed_on="2026-09-22",
        ),
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="validate and report without writing")
    ap.add_argument("--path", default=None, help="archive path override")
    ap.add_argument("--force", action="store_true",
                    help="APPEND to a non-empty archive. Almost never what you want: "
                         "re-seeding after editing a record needs --replace. Appending a "
                         "second copy of the corpus leaves EVERY published ratio unchanged "
                         "(market_evidence_frac, mean_evidential_weight) and doubles only "
                         "n, so the summary output looks correct on a corrupt archive.")
    ap.add_argument("--replace", action="store_true",
                    help="truncate the archive and re-seed from source. This is the right "
                         "flag after editing a stone.")
    args = ap.parse_args()

    arc = Archive(args.path)
    existing = arc.load()
    if existing and not args.check:
        if args.replace:
            arc.path.write_text("", encoding="utf-8")
            print(f"--replace: truncated {len(existing)} existing stones at {arc.path}")
        elif args.force:
            print("")
            print(f"  !!  --force APPENDS. The archive already holds {len(existing)} stones,")
            print(f"  !!  so this run will leave {len(existing) + len(_stones())} and every ratio")
            print("  !!  will look unchanged. Use --replace if you edited a record.")
            print("")
        else:
            print(f"archive already holds {len(existing)} stones at {arc.path}")
            print("refusing to duplicate; pass --replace to re-seed (or --force to append)")
            return 1

    rows = _stones()
    strict_ok, non_strict, unknown_keys = 0, [], []

    for i, r in enumerate(rows, 1):
        rec = ClosureRecord(
            family=r["family"],
            closure=r["closure"],
            rationale=r["rationale"],
            keystones=r.get("keystones", []),
            effect=r.get("effect"),
            ci=r.get("ci"),
            n_obs=r.get("n_obs"),
            docs=r.get("docs", []),
        )
        problems = rec.validate()
        if problems:
            non_strict.append((r["family"], problems))
        else:
            strict_ok += 1
        unknown_keys.extend(rec.unknown_keystones)

        if not args.check:
            stone = SteppingStone(
                stone_id=f"SS{i:04d}",
                closure=rec,
                claim=r["claim"],
                closed_on=r["closed_on"],
                revival_predicate=r["closure"].revives_on,
                anomalies=r.get("anomalies", []),
            )
            arc.append(stone, strict=False)

    print(f"{'validated' if args.check else 'wrote'} {len(rows)} stones"
          f"{'' if args.check else ' to ' + str(arc.path)}")
    print(f"  strictly admissible: {strict_ok}/{len(rows)}")
    if non_strict:
        print(f"  written non-strict:  {len(non_strict)}")
        for fam, probs in non_strict:
            print(f"    - {fam[:62]}")
            for p in probs:
                print(f"        {p}")
    if unknown_keys:
        print(f"  UNREGISTERED keystones (likely typos): {sorted(set(unknown_keys))}")

    if not args.check:
        print()
        print(json.dumps(arc.discrimination(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
