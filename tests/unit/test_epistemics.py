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
from src.epistemics.retro import find_revivals, first_principles_gate


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

    def test_a_tight_wrong_signed_interval_scores_near_zero(self, archive):
        """No instrument upgrade makes a decisively negative measurement a
        candidate again. This is the guard against collapsing into wishful
        thinking when mining the discard pile."""
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

    def test_bar_falls_with_sample_length(self):
        """The scale term is the SE of an annualised Sharpe, which shrinks with
        sample length. Omitting it returns a z-score, not a Sharpe."""
        assert operative_bar(34, 252) > operative_bar(34, 1008) > operative_bar(34, 2772)

    def test_bar_rises_with_trial_count(self):
        assert operative_bar(2, 1008) < operative_bar(34, 1008) < operative_bar(400, 1008)


class TestEffectiveN:
    def test_independence_is_the_identity(self):
        assert effective_n(1000, cluster_size=1, icc=0.0) == 1000

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

    def test_p_cert_under_a_null_prior_is_near_invariant_in_sample_size(self):
        """Both the bar and the estimator's spread scale with the same standard
        error, so lengthening the sample does not reduce the rate at which a
        null design certifies. This is why `is_ceremonial` cannot use an
        absolute floor on p_cert, and it restates the deflated-Sharpe point:
        more data does not buy protection from multiplicity, fewer looks do.
        """
        ps = [
            appraise(
                Design(name="d", family="f", n_obs=n, prior_mean=0.0, prior_sd=0.001),
                n_trials_registered=34,
            ).p_cert
            for n in (252, 1008, 2772, 6300)
        ]
        assert max(ps) - min(ps) < 0.01

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
