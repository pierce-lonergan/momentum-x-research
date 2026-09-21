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
            docs=["248", "256-257"],
            closed_on="<=2026-07-12",
        ),
        dict(
            family="Catalyst/news amplification (PEAD Stage A/B, filing text)",
            claim="Catalyst text amplifies an otherwise weak directional signal.",
            closure=Closure.REFUTED_BY_NATURE,
            rationale="Gate fails.",
            docs=["259-260"],
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
            closure=Closure.REFUTED_BY_NATURE,
            rationale="Refuted at the gate and on effect size. The apparent kNN lift was "
                      "a weak-baseline artifact; roughly four neighbourhoods exist and "
                      "they lie on one smooth surface.",
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
                      "it was underpowered. It fails on arithmetic: at the maximum the "
                      "standing constraints permit (10 families x 5% cap = 50% gross) it "
                      "needs a mean of 21.07 bps in a 15-minute window whose measured sd "
                      "is 15.06 bps — annualised Sharpe 22.2. Ceiling 6.0-8.6% of "
                      "requirement against the >=10% build filter.",
            keystones=[K("lower_requirement",
                         "a re-scoped account-level target",
                         "the 21.07 bps requirement that makes Sharpe 22.2 necessary"),
                       K("intraday_tape_pre_2020",
                         "minute bars for the pre-2024 sample",
                         "a 15-minute close window can only be measured on minute "
                         "bars, and minute_aggs covers 2024-2026 only (~650 "
                         "sessions) where the daily warehouse covers 2016-2026")],
            docs=["295", "298"],
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
            keystones=[K("lower_requirement",
                         "a re-scoped account-level target",
                         "the 10 bps/day line that is unreachable at any sample size")],
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
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true",
                    help="validate and report without writing")
    ap.add_argument("--path", default=None, help="archive path override")
    ap.add_argument("--force", action="store_true",
                    help="append even if the archive is non-empty")
    args = ap.parse_args()

    arc = Archive(args.path)
    existing = arc.load()
    if existing and not args.check and not args.force:
        print(f"archive already holds {len(existing)} stones at {arc.path}")
        print("refusing to duplicate; pass --force if that is genuinely intended")
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
