"""Tests for the epistemic machinery (doc 299).

The properties pinned here are the ones that make the architecture worth having
rather than decorative. Each maps to a failure this program actually committed:

* A closure that is a statement about us must name a keystone, or it is an
  abandonment (doc 296 was filed as a result when it was a spec mismatch).
* REFUTED_BY_NATURE requires a measurement, because that is the only class that
  is evidence the edge does not exist (doc 275: per-experiment p<0.05 is dead as
  a promotion criterion, and so is an unmeasured null).
* Retro-validation must not resurrect a wrong-signed, tightly-measured effect
  just because a data feed arrived (the discrimination problem).
* The planner's bar must agree with the gate's bar to the digit, or the program
  plans against one threshold and is judged against another.
"""

from __future__ import annotations

import json
import math

import pytest

from src.epistemics.archive import Archive, SteppingStone
from src.epistemics.closure import KEYSTONES, Closure, ClosureRecord, Keystone
from src.epistemics.eig import (
    Design,
    appraise,
    diversity_bonus,
    effective_n,
    operative_bar,
    rank,
    shannon_entropy,
)
from src.epistemics.retro import find_revivals, first_principles_gate, triage


# ── The taxonomy ─────────────────────────────────────────────────────────


class TestClosureTaxonomy:
    def test_only_three_classes_are_evidence_about_the_market(self):
        market = {c for c in Closure if c.is_evidence_about_market}
        assert market == {
            Closure.REFUTED_BY_NATURE,
            Closure.REFUTED_BY_COST,
            Closure.STRUCTURALLY_UNAVAILABLE,
        }

    def test_instrument_and_defect_closures_carry_no_evidential_weight(self):
        """These two classes say nothing about the hypothesis, in either
        direction. A family closed this way is exactly as open as before."""
        assert Closure.INSTRUMENT_LIMITED.evidential_weight == 0.0
        assert Closure.VOIDED_BY_DEFECT.evidential_weight == 0.0

    def test_nature_is_the_heaviest_class(self):
        assert Closure.REFUTED_BY_NATURE.evidential_weight == max(
            c.evidential_weight for c in Closure
        )

    def test_the_full_evidential_weight_table_is_pinned(self):
        """Five of the eight weights were unpinned, and they feed a published number.

        Doc 299 publishes "mean evidential weight 0.736", computed from all eight
        weights across the 25 records — so two of them could be changed
        arbitrarily with the suite green and the published figure would move.
        Caught by the doc-300 audit (test-adequacy/closure-01).
        """
        assert {c: c.evidential_weight for c in Closure} == {
            Closure.REFUTED_BY_NATURE: 1.00,
            Closure.REFUTED_BY_COST: 0.80,
            Closure.STRUCTURALLY_UNAVAILABLE: 0.70,
            Closure.REFUTED_BY_ARITHMETIC: 0.40,
            Closure.UNDERPOWERED: 0.15,
            Closure.INSTRUMENT_LIMITED: 0.00,
            Closure.VOIDED_BY_DEFECT: 0.00,
            Closure.ABANDONED: 0.05,
        }

    def test_every_class_has_a_revival_predicate(self):
        for c in Closure:
            assert c.revives_on, f"{c} has no revival predicate"

    def test_nature_requires_a_measurement(self):
        """The strongest claim carries the strongest burden of proof."""
        rec = ClosureRecord(
            family="f", closure=Closure.REFUTED_BY_NATURE, rationale="no edge"
        )
        problems = rec.validate()
        assert problems
        assert any("measured effect" in p for p in problems)
        assert any("UNDERPOWERED" in p for p in problems)

    def test_nature_with_a_measurement_is_admissible(self):
        rec = ClosureRecord(
            family="f",
            closure=Closure.REFUTED_BY_NATURE,
            rationale="measured negative",
            effect=-2.041,
            ci=(-2.823, -1.226),
            n_obs=4851,
        )
        assert rec.validate() == []

    def test_nature_needs_effect_AND_interval_not_either(self):
        """The conjunction must not weaken to a disjunction.

        `if self.effect is None or self.ci is None` complains when EITHER is
        missing. Mutated to `and`, it complains only when BOTH are — so a record
        with a point estimate and no interval would pass as REFUTED_BY_NATURE.
        That is precisely the state the exit-timing record is in (effect −1.13
        from doc 235, and no stated n for that subset), and precisely the state
        the rule exists to refuse. Verified by mutation: without this test the
        or→and mutant survives.
        """
        effect_only = ClosureRecord(
            family="f", closure=Closure.REFUTED_BY_NATURE,
            rationale="measured, but no interval", effect=-1.13, n_obs=500,
        )
        assert any("measured effect" in p for p in effect_only.validate())

        ci_only = ClosureRecord(
            family="f", closure=Closure.REFUTED_BY_NATURE,
            rationale="interval, but no point estimate", ci=(-1.7, -0.56),
            n_obs=500,
        )
        assert any("measured effect" in p for p in ci_only.validate())

        both = ClosureRecord(
            family="f", closure=Closure.REFUTED_BY_NATURE, rationale="complete",
            effect=-1.13, ci=(-1.7, -0.56), n_obs=500,
        )
        assert both.validate() == []

    def test_non_market_closure_must_name_a_keystone(self):
        rec = ClosureRecord(
            family="f", closure=Closure.INSTRUMENT_LIMITED, rationale="no data"
        )
        problems = rec.validate()
        assert any("keystone" in p for p in problems)

    def test_abandoned_is_exempt_from_the_keystone_requirement(self):
        """ABANDONED is precisely the no-keystone bucket. Requiring one there
        would push honest abandonments back into dressed-up refutations."""
        rec = ClosureRecord(
            family="f", closure=Closure.ABANDONED, rationale="not worth the attention"
        )
        assert rec.validate() == []

    def test_unknown_keystones_are_flagged(self):
        """A typo silently makes a record unrevivable — the exact failure this
        module exists to prevent — so the vocabulary is checked."""
        rec = ClosureRecord(
            family="f",
            closure=Closure.INSTRUMENT_LIMITED,
            rationale="blocked",
            keystones=[Keystone("histrical_option_nbbo", "typo")],
        )
        assert rec.unknown_keystones == ["histrical_option_nbbo"]

    def test_registered_keystones_are_not_flagged(self):
        rec = ClosureRecord(
            family="f",
            closure=Closure.INSTRUMENT_LIMITED,
            rationale="blocked",
            keystones=[Keystone(k, KEYSTONES[k]) for k in list(KEYSTONES)[:3]],
        )
        assert rec.unknown_keystones == []

    def test_round_trips_through_dict(self):
        rec = ClosureRecord(
            family="f",
            closure=Closure.REFUTED_BY_COST,
            rationale="borrow",
            keystones=[Keystone("short_locate_access", "borrow", "the door")],
            effect=-0.6,
            ci=(-1.5, -0.1),
            n_obs=94,
            docs=["283-284"],
        )
        again = ClosureRecord.from_dict(json.loads(json.dumps(rec.to_dict())))
        assert again.family == rec.family
        assert again.closure is rec.closure
        assert again.ci == rec.ci
        assert [k.key for k in again.keystones] == ["short_locate_access"]


# ── The archive ──────────────────────────────────────────────────────────


@pytest.fixture
def archive(tmp_path):
    return Archive(tmp_path / "stones.jsonl")


class TestArchive:
    def test_empty_archive_reports_zero_without_dividing(self, archive):
        d = archive.discrimination()
        assert d["n"] == 0
        assert d["market_evidence_frac"] is None

    def test_bury_then_read_back(self, archive):
        archive.bury(
            family="short door",
            claim="shorting gappers is profitable net of borrow",
            closure=Closure.REFUTED_BY_COST,
            rationale="net negative after borrow",
            keystones=[Keystone("short_locate_access", "borrow")],
            docs=["283"],
        )
        stones = archive.load()
        assert len(stones) == 1
        assert stones[0].stone_id == "SS0001"
        assert stones[0].closure.closure is Closure.REFUTED_BY_COST

    def test_ids_increment(self, archive):
        for i in range(3):
            archive.bury(
                family=f"f{i}", claim="c", closure=Closure.ABANDONED, rationale="r"
            )
        assert [s.stone_id for s in archive.load()] == ["SS0001", "SS0002", "SS0003"]

    def test_strict_bury_rejects_an_inadmissible_closure(self, archive):
        with pytest.raises(ValueError, match="not admissible"):
            archive.bury(
                family="f",
                claim="c",
                closure=Closure.REFUTED_BY_NATURE,
                rationale="no edge",
            )

    def test_non_strict_bury_allows_backfill(self, archive):
        archive.bury(
            family="f",
            claim="c",
            closure=Closure.REFUTED_BY_NATURE,
            rationale="no edge",
            strict=False,
        )
        assert len(archive.load()) == 1

    def test_discrimination_counts_the_split(self, archive):
        archive.bury(family="a", claim="c", closure=Closure.REFUTED_BY_NATURE,
                     rationale="r", effect=-1.0, ci=(-2.0, -0.5), n_obs=100)
        archive.bury(family="b", claim="c", closure=Closure.VOIDED_BY_DEFECT,
                     rationale="r",
                     keystones=[Keystone("executable_prereg_fixture", "fixture")])
        d = archive.discrimination()
        assert d["n"] == 2
        assert d["market_evidence"] == 1
        assert d["about_us"] == 1
        assert d["market_evidence_frac"] == 0.5
        assert d["revivable"] == 1

    def test_keystone_census_ranks_by_families_blocked(self, archive):
        archive.bury(family="a", claim="c", closure=Closure.INSTRUMENT_LIMITED,
                     rationale="r", keystones=[Keystone("lower_requirement", "x")])
        archive.bury(family="b", claim="c", closure=Closure.INSTRUMENT_LIMITED,
                     rationale="r", keystones=[Keystone("lower_requirement", "x")])
        archive.bury(family="c", claim="c", closure=Closure.INSTRUMENT_LIMITED,
                     rationale="r", keystones=[Keystone("clean_iv_surface", "y")])
        census = archive.keystone_census()
        assert census[0][0] == "lower_requirement"
        assert census[0][1] == 2

    def test_anomalies_are_preserved(self, archive):
        """The highest-value field and the one most often lost: a family dies on
        its primary endpoint leaving behind a result nobody asked for."""
        archive.bury(
            family="H-LOCAL", claim="c", closure=Closure.REFUTED_BY_NATURE,
            rationale="r", effect=0.0, ci=(-0.1, 0.1), n_obs=32,
            anomalies=["volatility scale is predictable while direction is not"],
        )
        assert archive.anomalies() == [
            ("SS0001", "H-LOCAL",
             "volatility scale is predictable while direction is not")
        ]

    def test_corrupt_line_names_the_line_number(self, archive):
        archive.path.parent.mkdir(parents=True, exist_ok=True)
        good = json.dumps({"stone_id": "SS0001", "family": "f",
                           "closure": "abandoned", "rationale": "r"})
        archive.path.write_text(good + "\nnot json\n", encoding="utf-8")
        with pytest.raises(ValueError, match=":2"):
            archive.load()


# ── Retro-validation ─────────────────────────────────────────────────────


class TestRetroValidation:
    def test_keystone_arrival_revives_an_instrument_closure(self, archive):
        archive.bury(
            family="LETF close-window", claim="c",
            closure=Closure.REFUTED_BY_ARITHMETIC, rationale="ceiling below filter",
            keystones=[Keystone("lower_requirement", "re-scoped target")],
        )
        revivals = find_revivals({"lower_requirement"}, archive=archive)
        assert len(revivals) == 1
        assert revivals[0].fully_unblocked
        assert revivals[0].satisfied == ["lower_requirement"]

    def test_market_evidence_does_not_revive_on_capability(self, archive):
        """The discrimination-problem guard. A keystone arriving cannot undo a
        measurement; only an argued market-structure change can."""
        archive.bury(
            family="short door", claim="c", closure=Closure.REFUTED_BY_COST,
            rationale="net negative after borrow",
            keystones=[Keystone("short_locate_access", "borrow")],
        )
        assert find_revivals({"short_locate_access"}, archive=archive) == []
        forced = find_revivals(
            {"short_locate_access"}, archive=archive, allow_market_evidence=True
        )
        assert len(forced) == 1

    def test_partial_unblock_reports_what_is_still_missing(self, archive):
        archive.bury(
            family="Stage 3", claim="c", closure=Closure.VOIDED_BY_DEFECT,
            rationale="h=1 vs h=21",
            keystones=[Keystone("executable_prereg_fixture", "fixture"),
                       Keystone("clean_iv_surface", "iv")],
        )
        r = find_revivals({"executable_prereg_fixture"}, archive=archive)[0]
        assert not r.fully_unblocked
        assert r.still_missing == ["clean_iv_surface"]

    def test_a_point_estimate_without_an_interval_is_still_read(self, archive):
        """`_plausibility` used to ignore `effect` entirely.

        A family whose measured point estimate pointed the WRONG way scored the
        same 0.5 as one that had never been measured — the maximum "genuine
        ignorance" value. On the real archive this is the branch that actually
        fires: all six reachable records have `ci=None`, so before this fix
        `_plausibility` was a constant function and contributed nothing to the
        ranking. Caught by the doc-300 audit.
        """
        from src.epistemics.retro import _plausibility

        def stone(effect):
            arc = Archive(archive.path.parent / f"p{effect}.jsonl")
            return arc.bury(
                family="f", claim="c", closure=Closure.VOIDED_BY_DEFECT,
                rationale="r", effect=effect,
                keystones=[Keystone("executable_prereg_fixture", "fixture")],
            )

        wrong_signed = _plausibility(stone(-4.43))
        favourable = _plausibility(stone(1.03))
        unmeasured = _plausibility(stone(None))

        assert wrong_signed < unmeasured < favourable
        assert unmeasured == 0.5
        # Compressed toward 0.5: a point estimate carries no precision, so it
        # cannot justify the decisive scores the interval branch can reach.
        assert 0.0 < wrong_signed < 0.5 < favourable < 1.0

    def test_the_wrong_signed_interval_branch_is_currently_unreachable(self):
        """Doc 299 cited this guard as what protects the archive. It does not.

        Every record carrying a wrong-signed interval is ALSO a market-evidence
        closure, so the market-evidence guard excludes it first; and both such
        records name no keystone, so they would be skipped regardless. Two
        independent exclusions fire before `_plausibility` is consulted.

        This test documents that state rather than asserting it is desirable. If
        it starts failing, a record has appeared that genuinely reaches the
        branch, and doc 299's claim becomes true — at which point update the
        prose rather than the test.
        """
        stones = Archive().load()
        if not stones:
            pytest.skip("real archive not present")

        reachable = [
            s for s in stones
            if not s.closure.closure.is_evidence_about_market and s.closure.keystones
        ]
        assert reachable, "expected some reachable records"
        wrong_signed_and_reachable = [
            s for s in reachable if s.closure.ci is not None and s.closure.ci[1] <= 0
        ]
        assert wrong_signed_and_reachable == [], (
            "a wrong-signed record now reaches _plausibility; doc 299's claim "
            "about this guard is no longer vacuous, so update the document"
        )

    def test_a_tight_wrong_signed_interval_scores_near_zero_as_a_unit_test(self, archive):
        """A unit test of `_plausibility`, NOT evidence about the real archive.

        It has to file the record as UNDERPOWERED to reach the branch at all,
        because a REFUTED_BY_NATURE record — which is what every wrong-signed
        family in the real archive actually is — is excluded by the
        market-evidence guard before `_plausibility` runs. See
        test_the_wrong_signed_interval_branch_is_currently_unreachable. Doc 299
        cited this passing test as though it established something about the
        archive; it does not.
        """
        archive.bury(
            family="gapper universe", claim="c", closure=Closure.UNDERPOWERED,
            rationale="measured negative",
            keystones=[Keystone("n_obs_sufficient", "more data")],
            effect=-2.041, ci=(-2.823, -1.226), n_obs=4851,
        )
        revivals = find_revivals({"n_obs_sufficient"}, archive=archive)
        assert revivals == [] or revivals[0].score < 0.05

    def test_an_interval_straddling_zero_scores_higher(self, archive):
        archive.bury(
            family="inconclusive", claim="c", closure=Closure.UNDERPOWERED,
            rationale="interval spans zero",
            keystones=[Keystone("n_obs_sufficient", "more data")],
            effect=0.5, ci=(-1.0, 3.0), n_obs=30,
        )
        revivals = find_revivals({"n_obs_sufficient"}, archive=archive)
        assert revivals and revivals[0].score > 0.05

    def test_unrelated_keystone_revives_nothing(self, archive):
        archive.bury(
            family="f", claim="c", closure=Closure.INSTRUMENT_LIMITED, rationale="r",
            keystones=[Keystone("clean_iv_surface", "iv")],
        )
        assert find_revivals({"lit_venue_routing"}, archive=archive) == []


class TestFirstPrinciplesGate:
    def test_cost_inadmissible_edge_fails(self):
        g = first_principles_gate(
            gross_edge_bps_per_ticket=5.0, round_trip_cost_bps=61.3
        )
        assert not g.passed
        assert any("cost-inadmissible" in f for f in g.failures)

    def test_inexecutable_action_fails_regardless_of_edge(self):
        """The short door did not fail on signal quality."""
        g = first_principles_gate(
            gross_edge_bps_per_ticket=500.0,
            round_trip_cost_bps=1.0,
            executable_at_broker=False,
        )
        assert not g.passed
        assert any("not executable" in f for f in g.failures)

    def test_capacity_violation_fails(self):
        g = first_principles_gate(
            gross_edge_bps_per_ticket=50.0,
            round_trip_cost_bps=1.0,
            required_size_usd=1_000_000,
            adv_usd=2_000_000,
        )
        assert not g.passed
        assert any("capacity" in f for f in g.failures)

    def test_ceiling_below_the_ten_percent_filter_fails(self):
        g = first_principles_gate(
            gross_edge_bps_per_ticket=2.0,
            round_trip_cost_bps=0.5,
            requirement_bps_per_ticket=100.0,
        )
        assert not g.passed
        assert any("build filter" in f for f in g.failures)

    def test_unchecked_arguments_are_reported_not_silently_passed(self):
        g = first_principles_gate()
        assert g.passed
        assert any("UNCHECKED" in n for n in g.notes)
        assert len([n for n in g.notes if "UNCHECKED" in n]) == 3

    def test_an_admissible_design_passes(self):
        g = first_principles_gate(
            gross_edge_bps_per_ticket=30.0,
            round_trip_cost_bps=2.0,
            required_size_usd=10_000,
            adv_usd=5_000_000,
            requirement_bps_per_ticket=50.0,
        )
        assert g.passed, g.failures


# ── Information gain and the multiplicity toll ───────────────────────────


class TestBarAgreement:
    def test_planner_bar_matches_the_gate_to_the_digit(self):
        """If these drift, the program plans against one threshold and is judged
        against another. The registry's own docstring pins the 33-trial /
        2.08-year case at 2.606."""
        import importlib.util
        import pathlib

        spec = importlib.util.spec_from_file_location(
            "_tr",
            pathlib.Path(__file__).resolve().parents[2] / "scripts" / "trial_registry.py",
        )
        tr = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tr)

        for n_trials, n_obs in [(33, 524), (34, 2772), (2, 252), (100, 1008)]:
            gate = tr.promotion_threshold(0.0, n_obs=n_obs, n_trials=n_trials)
            assert operative_bar(n_trials, n_obs) == pytest.approx(
                gate["OPERATIVE_BAR"], abs=1e-3
            )

    def test_planner_and_gate_agree_at_every_periods_per_year(self):
        """The agreement test above only ever exercised ppy=252.

        `operative_bar()` forwarded a caller-supplied `periods_per_year` into both
        of its terms while `promotion_threshold()` had no such parameter and
        hardcoded 252 in both of its own — so the planner's bar equalled
        sqrt(ppy/252) times the bar the gate enforces. At ppy=12 that is 4.58x,
        in the dangerous direction, and `periods_per_year` is settable straight
        from `data/research/design_queue.json` with no guard. A verifier
        demonstrated a one-key edit flipping a design from CEREMONIAL to
        ADMISSIBLE, across doc 299's own rule that a ceremonial design must not
        be registered. Caught by the doc-300 audit (bar-agreement/eig-02).
        """
        import importlib.util
        import pathlib

        spec = importlib.util.spec_from_file_location(
            "_tr",
            pathlib.Path(__file__).resolve().parents[2] / "scripts" / "trial_registry.py",
        )
        tr = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tr)

        for ppy in (12, 52, 252, 1638):
            for n_obs in (252, 1008):
                gate = tr.promotion_threshold(
                    0.0, n_obs=n_obs, n_trials=33, periods_per_year=ppy
                )["OPERATIVE_BAR"]
                # abs=5e-5, not a relative tolerance: promotion_threshold()
                # rounds every value it reports to 4 decimals, so half of the
                # last reported decimal is the tightest agreement observable
                # through its public return. The underlying arithmetic is
                # identical - both sides call the same expected_max_sharpe and
                # the same sqrt(ppy/n_obs) - and a relative tolerance fails on
                # the smallest bars purely from that rounding.
                assert operative_bar(33, n_obs, ppy) == pytest.approx(gate, abs=5e-5), (
                    f"planner and gate disagree at ppy={ppy}, n_obs={n_obs}"
                )

    def test_bar_falls_with_sample_length(self):
        """The scale term is the SE of an annualised Sharpe, which shrinks with
        sample length. Omitting it returns a z-score, not a Sharpe."""
        assert operative_bar(34, 252) > operative_bar(34, 1008) > operative_bar(34, 2772)

    def test_bar_rises_with_trial_count(self):
        assert operative_bar(2, 1008) < operative_bar(34, 1008) < operative_bar(400, 1008)


class TestEffectiveN:
    def test_independence_is_the_identity(self):
        assert effective_n(1000, cluster_size=1, icc=0.0) == 1000

    def test_the_design_effect_arithmetic_is_exact(self):
        """`(m-1)*rho` could become `m*rho` with the suite green.

        The correction is a design-stage requirement (doc 278: rho 0.09-0.35,
        ~2.5x SE inflation) and five of the nine live designs set cluster_size
        and icc, so it reaches published output. Caught by the doc-300 audit
        (test-adequacy/eig-07).

        n_eff = n / (1 + (m-1)*rho) = 1000 / (1 + 19*0.3) = 1000 / 6.7
        """
        assert effective_n(1000, cluster_size=20, icc=0.3) == pytest.approx(
            1000 / 6.7, abs=1e-9
        )
        # m*rho instead of (m-1)*rho would give 1000/7.0 = 142.86, not 149.25.
        assert effective_n(1000, cluster_size=20, icc=0.3) != pytest.approx(
            1000 / 7.0, abs=0.5
        )
        # And Design must actually propagate it rather than ignoring clustering.
        d = Design(name="d", family="f", n_obs=1000, cluster_size=20, icc=0.3)
        assert d.n_eff == pytest.approx(1000 / 6.7, abs=1e-9)

    def test_clustering_shrinks_the_sample(self):
        """rho 0.09-0.35 within-day was measured to inflate SE ~2.5x. The
        correction belongs at the design stage, where it is free."""
        assert effective_n(1000, cluster_size=20, icc=0.3) < 200

    def test_never_returns_less_than_two(self):
        assert effective_n(3, cluster_size=1000, icc=0.9) >= 2


class TestAppraisal:
    def test_eig_is_zero_when_the_experiment_cannot_move_the_prior(self):
        tiny = Design(name="t", family="f", n_obs=2, prior_sd=1e-6)
        a = appraise(tiny, n_trials_registered=34)
        assert a.eig_nats < 1e-6

    def test_eig_rises_with_sample_size(self):
        small = Design(name="s", family="f", n_obs=252)
        big = Design(name="b", family="f", n_obs=6300)
        assert (appraise(big, n_trials_registered=34).eig_nats
                > appraise(small, n_trials_registered=34).eig_nats)

    def test_the_eig_closed_form_has_the_right_MAGNITUDE(self):
        """Every other EIG test checked only ordering, so the magnitude was free.

        The leading 0.5 and the scale of the variance ratio could both be wrong
        and the whole suite stayed green — while doc 299 publishes absolute EIG
        values (0.344, 0.144, 0.135, ...) that rank the live queue and justify
        the top procurement item, and `rank()` denominates its EIG-versus-toll
        trade-off in these units. Caught by the doc-300 audit
        (test-adequacy/eig-03).

        Closed form with sigma_0 = 1.0, n_obs = 1008, omega = 1.0:
          se    = sqrt(252/1008) = 0.5
          ratio = (sigma_0/se)^2 = 4.0
          EIG   = 0.5 * ln(1 + 4) = 0.5 * ln 5 = 0.80472 nats

        sigma_0 is deliberately NOT 0.5. At sigma_0 = se the ratio is 1.0 and
        1.0**2 == 1.0, so squaring is a no-op and a mutation dropping the square
        survives. The first version of this test used 0.5 and did exactly that —
        it asserted a true value at the one point where the thing it meant to
        pin is invisible. Verified by mutation: at 0.5 the dropped-square
        mutant survives; at 1.0 it dies.
        """
        a = appraise(
            Design(name="d", family="f", n_obs=1008, prior_sd=1.0),
            n_trials_registered=34,
            omega=1.0,
        )
        assert a.eig_nats == pytest.approx(0.5 * math.log(5), abs=1e-9)
        # Posterior sd: 1/sqrt(1/1.0 + 1/0.25) = 1/sqrt(5) = 0.44721
        assert a.posterior_sd == pytest.approx(1.0 / math.sqrt(5), abs=1e-9)
        # And the ratio must scale QUADRATICALLY: doubling sigma_0 from 1.0 to
        # 2.0 takes the ratio from 4 to 16, not from 2 to 4.
        b = appraise(
            Design(name="d", family="f", n_obs=1008, prior_sd=2.0),
            n_trials_registered=34,
            omega=1.0,
        )
        assert b.eig_nats == pytest.approx(0.5 * math.log(17), abs=1e-9)

    def test_posterior_is_never_wider_than_the_prior(self):
        d = Design(name="d", family="f", n_obs=1008, prior_sd=0.5)
        a = appraise(d, n_trials_registered=34)
        assert a.posterior_sd <= d.prior_sd

    def test_tempering_reduces_credited_information(self):
        """Under misspecification the design is credited with less learning per
        observation. omega=1 is the optimistic bound, not the default."""
        d = Design(name="d", family="f", n_obs=1008)
        strong = appraise(d, n_trials_registered=34, omega=1.0)
        weak = appraise(d, n_trials_registered=34, omega=0.1)
        assert weak.eig_nats < strong.eig_nats

    def test_registering_a_trial_always_costs_something(self):
        d = Design(name="d", family="f", n_obs=1008)
        a = appraise(d, n_trials_registered=34)
        assert a.toll_sharpe > 0
        assert a.bar_after > a.bar_before

    def test_a_null_prior_gives_a_low_certification_probability(self):
        """prior_mean=0 is the program's own measured base rate."""
        d = Design(name="d", family="f", n_obs=1008, prior_mean=0.0, prior_sd=0.3)
        assert appraise(d, n_trials_registered=34).p_cert < 0.05

    def test_ceremonial_tests_are_flagged(self):
        """A test that can neither teach nor discriminate.

        A near-point prior at zero means nothing can be learned, and a pass is
        then exactly as likely under the prior as under the null.
        """
        d = Design(name="d", family="f", n_obs=1008, prior_mean=0.0, prior_sd=0.001)
        a = appraise(d, n_trials_registered=34)
        assert a.eig_nats < 0.05
        assert a.informativeness < 1.5
        assert a.is_ceremonial
        assert "CEREMONIAL" in a.verdict

    def test_p_cert_is_invariant_ONLY_under_a_point_mass_prior(self):
        """The corrected form of a claim this file previously got wrong.

        The original test asserted p_cert was invariant in sample size, and
        passed — but only because it used prior_sd=0.001, which is 500x below
        this module's own default of 0.5 and appears nowhere in the live queue.
        That is exactly the weak-baseline failure doc 290 forbids, committed
        against the program's own machinery, and the green test is what licensed
        publishing the unqualified claim in doc 299. Corrected by the doc-300
        audit; see test_p_false_positive_is_the_invariant_quantity for where the
        invariance actually lives.
        """
        NS = (252, 1008, 2772, 6300)

        def spread(prior_sd):
            ps = [
                appraise(
                    Design(name="d", family="f", n_obs=n,
                           prior_mean=0.0, prior_sd=prior_sd),
                    n_trials_registered=34,
                ).p_cert
                for n in NS
            ]
            return max(ps) - min(ps)

        # A point-mass prior: invariant, as originally claimed.
        assert spread(0.001) < 1e-4

        # The module's OWN default prior: emphatically not invariant. 0.0855 on
        # this grid, which is 8.5x the 0.01 tolerance the original test used to
        # certify "invariance". Widening the grid to 25,200 takes it to 0.208.
        assert spread(0.5) > 0.05, (
            "p_cert must be shown to vary at the default prior, or the "
            "corrected claim is not actually pinned"
        )
        # And monotone increasing in n, which is the substantive point: a
        # longer sample lowers the bar faster than it sharpens the estimate.
        ps = [
            appraise(Design(name="d", family="f", n_obs=n, prior_mean=0.0, prior_sd=0.5),
                     n_trials_registered=34).p_cert
            for n in NS
        ]
        assert ps == sorted(ps), ps

    def test_p_false_positive_is_the_invariant_quantity(self):
        """This is the claim worth locking, and the one doc 299 should have made.

        bar/sigma_e = sqrt(omega) * (E[max]/se + 1.6449), in which n cancels
        exactly, so the rate at which a design certifies NOTHING is identical at
        every sample length — and independent of the prior, which never enters
        it. 2.9342% at omega=0.25 and 34 trials.
        """
        vals = set()
        for n in (252, 1008, 2772, 6300, 25200):
            for prior_sd in (0.001, 0.3, 0.5, 0.9):
                for prior_mean in (0.0, 1.0, 2.0):
                    vals.add(round(appraise(
                        Design(name="d", family="f", n_obs=n,
                               prior_mean=prior_mean, prior_sd=prior_sd),
                        n_trials_registered=33, omega=0.25,
                    ).p_false_positive, 9))
        assert len(vals) == 1, f"p_false_positive should be invariant, got {sorted(vals)}"
        # 0.029735 at 33 REGISTERED trials - the count the registry and the live
        # queue actually hold, and the value all nine shipped designs emit. The
        # earlier 0.029342 was the 34-registered value and matched nothing the
        # planner produces. Caught by the doc-300 completeness critic.
        assert vals.pop() == pytest.approx(0.0297350005, abs=1e-9)

    def test_the_bar_is_computed_on_the_effective_sample(self):
        """The bar must see the same sampling noise the estimator does.

        Computing it on nominal n while computing sigma_e on n_eff understated
        the bar for every clustered design — three of the nine in the live queue
        — and broke the cancellation that makes p_false_positive invariant.
        Caught by the doc-300 audit; nothing in the suite failed when it was
        wrong, which is why this test exists.
        """
        clustered = Design(name="c", family="f", n_obs=2772, cluster_size=12, icc=0.2)
        a = appraise(clustered, n_trials_registered=34)

        # n_eff is far below n_obs, so the bar must be far ABOVE the naive one.
        assert clustered.n_eff < clustered.n_obs / 3
        assert a.bar_after > operative_bar(35, clustered.n_obs) + 0.5
        assert a.bar_after == pytest.approx(
            operative_bar(35, int(clustered.n_eff)), abs=1e-9
        )

        # And with the bar on the same footing, the invariance is restored even
        # under clustering — which is the independent check that the fix is right.
        fps = {
            round(appraise(
                Design(name="c", family="f", n_obs=n, cluster_size=12, icc=0.2),
                n_trials_registered=34,
            ).p_false_positive, 9)
            for n in (1008, 2772, 6300)
        }
        assert len(fps) == 1, f"clustered p_fp should also be invariant, got {sorted(fps)}"


    def test_informativeness_is_an_absolute_p_cert_floor_including_clustering(self):
        """The retraction, pinned so it cannot drift back.

        `eig.py` once claimed the floor equivalence held only for unclustered
        designs. Putting the bar on the effective sample (doc 300 §3) made n_eff
        cancel exactly as n_obs does, so p_false_positive is invariant under
        clustering too — and `informativeness < 1.5` is therefore an absolute
        floor on p_cert at 1.5 × p_fp, with no exception.
        """
        vals = set()
        for n in (252, 1008, 2772):
            for cs, icc in ((1, 0.0), (4, 0.25), (12, 0.2)):
                for psd in (0.25, 0.5, 0.7):
                    vals.add(round(appraise(
                        Design(name="d", family="f", n_obs=n, prior_sd=psd,
                               cluster_size=cs, icc=icc),
                        n_trials_registered=33,
                    ).p_false_positive, 10))
        assert len(vals) == 1, f"clustering must not move p_fp, got {sorted(vals)}"
        assert 1.5 * vals.pop() == pytest.approx(0.0446025, abs=1e-6)

    def test_a_well_powered_design_discriminates(self):
        """The contrast case: a prior mean well above the bar makes a pass far
        more likely than under the null."""
        d = Design(name="d", family="f", n_obs=2772, prior_mean=2.0, prior_sd=0.5)
        a = appraise(d, n_trials_registered=34)
        assert a.informativeness > 1.5
        assert not a.is_ceremonial

    def test_severity_is_high_when_the_bar_is_far_above_noise(self):
        """Popper via Mayo: a test only corroborates if it would probably have
        failed had the hypothesis been false."""
        d = Design(name="d", family="f", n_obs=6300)
        assert appraise(d, n_trials_registered=34).severity > 0.9

    def test_mde_exceeds_the_bar(self):
        d = Design(name="d", family="f", n_obs=1008)
        a = appraise(d, n_trials_registered=34)
        assert a.mde > a.bar_after

    def test_infeasible_when_a_required_keystone_is_missing(self):
        d = Design(name="d", family="f", n_obs=1008,
                   keystones_required=("historical_option_nbbo",))
        a = appraise(d, n_trials_registered=34,
                     available_keystones={"clean_iv_surface"})
        assert not a.feasible
        assert a.blocked_by == ["historical_option_nbbo"]
        assert a.verdict == "INFEASIBLE"

    def test_queue_toll_is_reported(self):
        queue = [Design(name=f"q{i}", family="f", n_obs=1008, prior_mean=2.0)
                 for i in range(5)]
        a = appraise(queue[0], n_trials_registered=34, queue=queue)
        assert a.toll_p_cert > 0


class TestDiversity:
    def test_entropy_of_a_single_family_is_zero(self):
        assert shannon_entropy({"a": 10}) == 0.0

    def test_entropy_is_maximal_when_uniform(self):
        assert shannon_entropy({"a": 5, "b": 5, "c": 5}) == pytest.approx(math.log(3))

    def test_entropy_handles_an_empty_histogram(self):
        assert shannon_entropy({}) == 0.0

    def test_a_new_family_is_rewarded_over_a_crowded_one(self):
        """Guards against the mode collapse the program already shows: doc 290
        and 291 both found generic features carrying the signal, which is the
        signature of a search that never left its neighbourhood."""
        history = {"price_momentum": 20, "volatility": 2}
        novel = Design(name="n", family="market_structure", n_obs=1008)
        crowded = Design(name="c", family="price_momentum", n_obs=1008)
        assert diversity_bonus(novel, history) > diversity_bonus(crowded, history)

    def test_crowding_an_already_dominant_family_is_penalised(self):
        history = {"price_momentum": 30, "other": 1}
        crowded = Design(name="c", family="price_momentum", n_obs=1008)
        assert diversity_bonus(crowded, history) < 0


class TestRanking:
    def test_infeasible_designs_rank_last(self):
        good = Design(name="good", family="a", n_obs=1008)
        blocked = Design(name="blocked", family="b", n_obs=6300,
                         keystones_required=("historical_option_nbbo",))
        ranked = rank([blocked, good], n_trials_registered=34,
                      available_keystones=set())
        assert ranked[-1][0].design.name == "blocked"
        assert ranked[-1][1] == float("-inf")

    def test_ranking_is_stable_and_complete(self):
        designs = [Design(name=f"d{i}", family="f", n_obs=252 * (i + 1))
                   for i in range(4)]
        ranked = rank(designs, n_trials_registered=34)
        assert len(ranked) == 4
        scores = [s for _, s in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_longer_samples_win_all_else_equal(self):
        short = Design(name="short", family="f", n_obs=252)
        long_ = Design(name="long", family="f", n_obs=6300)
        ranked = rank([short, long_], n_trials_registered=34)
        assert ranked[0][0].design.name == "long"


class TestSteppingStoneSerialisation:
    def test_round_trips(self):
        stone = SteppingStone(
            stone_id="SS0001",
            closure=ClosureRecord(
                family="f", closure=Closure.INSTRUMENT_LIMITED, rationale="r",
                keystones=[Keystone("clean_iv_surface", "iv", "blocked")],
            ),
            claim="c",
            closed_on="2026-09-20",
            revival_predicate="the named keystone landing",
            anomalies=["something odd"],
        )
        again = SteppingStone.from_dict(json.loads(json.dumps(stone.to_dict())))
        assert again.stone_id == "SS0001"
        assert again.anomalies == ["something odd"]
        assert again.keystone_keys == {"clean_iv_surface"}

class TestVerdictBranches:
    """Every branch of `Appraisal.verdict`, pinned and proved reachable.

    The doc-300 completeness critic reported that `UNDISCRIMINATING` had never
    been emitted by any execution path in this repository, and that deleting
    three of the six verdict strings left the suite green. Probing every branch
    for reachability turned up something worse: **INFORMATIVE BUT UNCERTIFIABLE
    was unreachable by construction.** Its guard is `p_cert < 0.01 <= eig`, but
    `p_cert < 0.01` implies `p_cert < 1.5 * p_fp = 0.0446`, which implies
    `informativeness < 1.5` — so the more general `UNDISCRIMINATING` branch
    always returned first and swallowed it. Reordered so the specific test runs
    first; every branch now has a witness below.
    """

    def test_infeasible(self):
        d = Design(name="d", family="f", n_obs=1008,
                   keystones_required=("historical_option_nbbo",))
        assert appraise(d, n_trials_registered=33,
                        available_keystones=set()).verdict == "INFEASIBLE"

    def test_ceremonial(self):
        d = Design(name="d", family="f", n_obs=252, prior_mean=0.0, prior_sd=0.05)
        assert appraise(d, n_trials_registered=33).verdict.startswith("CEREMONIAL")

    def test_informative_but_uncertifiable_is_reachable_at_all(self):
        """The branch that was dead. It needs a NEGATIVE prior mean.

        `p_cert` is bounded below by `p_false_positive` whenever prior_mean >= 0,
        because s_pred = sqrt(s0^2 + se^2) >= se — so p_cert can only fall below
        1% if the analyst believes the effect is wrong-signed. That is a legal
        and meaningful design ("I expect this to fail; I want to know how
        badly"), and this verdict exists to say: run it to learn, never to
        promote.
        """
        d = Design(name="d", family="f", n_obs=1008, prior_mean=-1.0, prior_sd=0.6)
        a = appraise(d, n_trials_registered=33)
        assert a.p_cert < 0.01 <= a.eig_nats
        assert a.verdict.startswith("INFORMATIVE BUT UNCERTIFIABLE")

    def test_undiscriminating(self):
        d = Design(name="d", family="f", n_obs=252, prior_mean=0.0, prior_sd=0.9)
        a = appraise(d, n_trials_registered=33)
        assert a.eig_nats >= 0.05 and a.informativeness < 1.5
        assert a.verdict.startswith("UNDISCRIMINATING")

    def test_negative_sum_needs_a_large_queue(self):
        """Reachable, but only when the queue it taxes is big enough.

        The design must first survive CEREMONIAL and UNDISCRIMINATING — so
        p_cert >= ~0.0446 — and only then can the summed toll exceed it. At 200
        queued high-prior designs it does; at 50 it does not.
        """
        me = Design(name="me", family="f", n_obs=1008, prior_mean=0.5, prior_sd=0.6)
        small = [me] + [Design(name=f"q{i}", family="g", n_obs=1008,
                               prior_mean=2.0, prior_sd=0.5) for i in range(50)]
        big = [me] + [Design(name=f"q{i}", family="g", n_obs=1008,
                             prior_mean=2.0, prior_sd=0.5) for i in range(200)]
        assert appraise(me, n_trials_registered=33, queue=small).verdict == "ADMISSIBLE"
        assert appraise(me, n_trials_registered=33,
                        queue=big).verdict.startswith("NEGATIVE-SUM")

    def test_admissible(self):
        d = Design(name="d", family="f", n_obs=252, prior_mean=0.2, prior_sd=0.9)
        assert appraise(d, n_trials_registered=33).verdict == "ADMISSIBLE"

    def test_all_six_verdicts_are_distinct_and_witnessed(self):
        """Guards against a mutation that collapses two labels into one."""
        seen = set()
        seen.add(appraise(Design(name="d", family="f", n_obs=1008,
                                 keystones_required=("x",)),
                          n_trials_registered=33,
                          available_keystones=set()).verdict.split(" ")[0])
        for pm, ps, n in [(0.0, 0.05, 252), (-1.0, 0.6, 1008),
                          (0.0, 0.9, 252), (0.2, 0.9, 252)]:
            seen.add(appraise(Design(name="d", family="f", n_obs=n, prior_mean=pm,
                                     prior_sd=ps),
                              n_trials_registered=33).verdict.split(" ")[0])
        me = Design(name="me", family="f", n_obs=1008, prior_mean=0.5, prior_sd=0.6)
        q = [me] + [Design(name=f"q{i}", family="g", n_obs=1008, prior_mean=2.0,
                           prior_sd=0.5) for i in range(200)]
        seen.add(appraise(me, n_trials_registered=33, queue=q).verdict.split(" ")[0])
        assert seen == {"INFEASIBLE", "CEREMONIAL", "INFORMATIVE",
                        "UNDISCRIMINATING", "NEGATIVE-SUM", "ADMISSIBLE"}, seen

    def test_is_ceremonial_requires_BOTH_conditions(self):
        """`and` must not become `or`. A design can be uninformative without
        being undiscriminating, and vice versa; only the conjunction is
        worthless."""
        a = appraise(Design(name="d", family="f", n_obs=252, prior_mean=0.0,
                            prior_sd=0.9), n_trials_registered=33)
        assert a.eig_nats >= 0.05 and a.informativeness < 1.5
        assert not a.is_ceremonial
        b = appraise(Design(name="d", family="f", n_obs=6300, prior_mean=3.0,
                            prior_sd=0.02), n_trials_registered=33)
        assert b.eig_nats < 0.05 and b.informativeness >= 1.5
        assert not b.is_ceremonial


class TestGateBoundaries:
    """`first_principles_gate`'s comparisons, at the boundary.

    All three boundary mutations survived the critic's run: `net <= 0` to
    `net < 0`, `participation > limit` to `>=`, and `frac < floor` to
    `frac < floor*0.5`. Each constraint had a failing-case test and no boundary
    test.
    """

    def test_exactly_break_even_is_not_admissible(self):
        g = first_principles_gate(gross_edge_bps_per_ticket=10.0,
                                  round_trip_cost_bps=10.0)
        assert not g.passed, "net == 0 must fail, not pass"

    def test_a_hair_above_break_even_clears_the_cost_check(self):
        g = first_principles_gate(gross_edge_bps_per_ticket=10.01,
                                  round_trip_cost_bps=10.0)
        assert not any("cost-inadmissible" in f for f in g.failures)

    def test_participation_exactly_at_the_limit_passes(self):
        g = first_principles_gate(gross_edge_bps_per_ticket=50.0,
                                  round_trip_cost_bps=1.0,
                                  required_size_usd=10_000, adv_usd=1_000_000,
                                  max_adv_participation=0.01)
        assert not any("capacity" in f for f in g.failures), g.failures

    def test_participation_a_hair_over_the_limit_fails(self):
        g = first_principles_gate(gross_edge_bps_per_ticket=50.0,
                                  round_trip_cost_bps=1.0,
                                  required_size_usd=10_100, adv_usd=1_000_000,
                                  max_adv_participation=0.01)
        assert any("capacity" in f for f in g.failures)

    def test_ceiling_exactly_at_the_ten_percent_floor_passes(self):
        g = first_principles_gate(gross_edge_bps_per_ticket=11.0,
                                  round_trip_cost_bps=1.0,
                                  requirement_bps_per_ticket=100.0)
        assert not any("build filter" in f for f in g.failures), g.failures

    def test_ceiling_a_hair_under_the_floor_fails(self):
        g = first_principles_gate(gross_edge_bps_per_ticket=10.9,
                                  round_trip_cost_bps=1.0,
                                  requirement_bps_per_ticket=100.0)
        assert any("build filter" in f for f in g.failures)

    def test_the_filter_refuses_to_run_on_gross(self):
        """The fix for code-bugs/retro-01, pinned.

        With no cost supplied the requirement filter must report UNCHECKED, not
        compute a ceiling on gross edge and pass it.
        """
        g = first_principles_gate(gross_edge_bps_per_ticket=2.0,
                                  requirement_bps_per_ticket=100.0)
        assert not any("build filter" in f for f in g.failures)
        assert any("no round-trip cost supplied" in n for n in g.notes), g.notes


class TestRankTerms:
    """`rank()`'s score has three terms and the critic killed none of them.

    Flipping the toll sign, deleting the toll, and deleting the diversity term
    all survived. Each now has its own directional assertion.
    """

    def test_the_toll_is_SUBTRACTED_not_added(self):
        """A design that taxes a queue must score BELOW the same design alone.
        If the sign flips, taxing the queue would look like a benefit."""
        a = Design(name="a", family="f", n_obs=1008, prior_mean=2.0, prior_sd=0.5)
        padding = [Design(name=f"p{i}", family="f", n_obs=1008, prior_mean=2.0,
                          prior_sd=0.5) for i in range(60)]
        alone = rank([a], n_trials_registered=33)[0][1]
        taxed = [sc for ap, sc in rank([a] + padding, n_trials_registered=33)
                 if ap.design.name == "a"][0]
        assert taxed < alone, f"alone={alone}, taxed={taxed}"

    def test_the_diversity_term_actually_moves_the_score(self):
        novel = Design(name="n", family="brand_new_class", n_obs=1008)
        crowded = Design(name="c", family="price_momentum", n_obs=1008)
        history = {"price_momentum": 30, "other": 1}
        with_lam = dict((ap.design.name, sc) for ap, sc in
                        rank([novel, crowded], n_trials_registered=33,
                             history=history, lam=0.5))
        assert with_lam["n"] > with_lam["c"], with_lam
        # At lam=0 the two must tie, which proves the gap came from the term.
        flat = dict((ap.design.name, sc) for ap, sc in
                    rank([novel, crowded], n_trials_registered=33,
                         history=history, lam=0.0))
        assert flat["n"] == pytest.approx(flat["c"], abs=1e-12)

    def test_rank_does_not_mutate_the_history_it_is_given(self):
        """`diversity_bonus` is called once per design with the SAME history
        object. If it mutated rather than copied, later designs would see
        earlier ones already counted and two identical calls would disagree."""
        history = {"price_momentum": 8, "volatility": 3}
        snapshot = dict(history)
        designs = [Design(name=f"d{i}", family="price_momentum", n_obs=1008)
                   for i in range(3)]
        first = [sc for _, sc in rank(designs, n_trials_registered=33,
                                      history=history)]
        second = [sc for _, sc in rank(designs, n_trials_registered=33,
                                       history=history)]
        assert history == snapshot, "rank() mutated the caller's history"
        assert first == second


class TestTriage:
    """`triage()` had ZERO coverage — the function doc 299 tells you to run
    after any capability lands, and a CLI subcommand."""

    def test_triage_returns_every_documented_key(self, archive):
        archive.bury(family="f", claim="c", closure=Closure.INSTRUMENT_LIMITED,
                     rationale="r",
                     keystones=[Keystone("clean_iv_surface", "iv")],
                     anomalies=["something odd"])
        t = triage({"clean_iv_surface"}, archive=archive)
        assert set(t) == {"available_keystones", "discrimination", "revivals",
                          "fully_unblocked", "keystone_census", "anomalies"}
        assert t["revivals"] and t["revivals"][0]["fully_unblocked"]
        assert t["fully_unblocked"] == ["f"]
        assert t["anomalies"][0]["anomaly"] == "something odd"
        assert t["discrimination"]["n"] == 1

    def test_triage_on_an_empty_archive_does_not_crash(self, archive):
        t = triage({"anything"}, archive=archive)
        assert t["revivals"] == [] and t["discrimination"]["n"] == 0


class TestPublishedJsonShape:
    """Every `to_dict()` was uncovered, so the published JSON shape was
    untested — and it is exactly what `scripts/epistemics.py` prints."""

    def test_appraisal_to_dict_carries_every_published_column(self):
        a = appraise(Design(name="d", family="f", n_obs=1008),
                     n_trials_registered=33)
        d = a.to_dict()
        for k in ("name", "family", "n_obs", "n_eff", "eig_nats", "eig_per_day",
                  "posterior_sd", "prior_sd", "severity", "p_cert",
                  "p_false_positive", "informativeness", "mde", "bar_before",
                  "bar_after", "toll_sharpe", "toll_p_cert", "feasible",
                  "blocked_by", "verdict"):
            assert k in d, f"published column {k} missing from to_dict()"
        assert json.loads(json.dumps(d))["verdict"] == a.verdict

    def test_revival_to_dict_is_json_round_trippable(self, archive):
        archive.bury(family="f", claim="c", closure=Closure.VOIDED_BY_DEFECT,
                     rationale="r",
                     keystones=[Keystone("executable_prereg_fixture", "fx")])
        r = find_revivals({"executable_prereg_fixture"}, archive=archive)[0]
        d = json.loads(json.dumps(r.to_dict()))
        assert d["fully_unblocked"] is True
        assert d["closure"] == "voided_by_defect"
