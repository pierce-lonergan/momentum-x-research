"""The closure taxonomy — separating what nature refused from what we asked badly.

Motivation
----------
The program's `ATTEMPTS_LEDGER.md` records 33 closed hypothesis families under a
single column called "verdict". Read closely, that column conflates two kinds of
statement that have nothing to do with each other:

    "Short door — net negative after borrow"
        A measurement of the world. Borrow costs more than the effect is worth.
        Nothing we build changes this. It is a law of the current market.

    "Single-name RV-vs-IV @30d — unblinded pass VOID, runner evaluated h=1
     where the prereg froze h=21"
        A measurement of *us*. The hypothesis was never actually tested.

Both were filed as closures. Only the first is evidence about markets.

This module makes the distinction explicit and machine-checkable, because the
program's headline claim — "35 families closed, zero certified edges" — is only
as strong as the fraction of those closures that are the first kind. If most of
the graveyard died of instrument limits, underpowered designs, or spec defects,
then the honest summary is not "there is no edge here" but "we have not yet
asked most of these questions properly", which licenses completely different
next actions.

The taxonomy deliberately refuses a "failed" category. Every closure must name
*which kind* of failure, and every non-nature closure must name the keystone it
was missing. A closure that cannot name its keystone is not a closure; it is an
abandonment, and it is filed as one.

Reference: docs/research-log/299_the_epistemic_architecture.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Closure(str, Enum):
    """Why a hypothesis stopped being pursued.

    Ordered from "strongest evidence about the world" to "no evidence about the
    world at all". The ordering is load-bearing: `Closure.evidential_weight`
    depends on it, and the retro-validation engine walks the archive in reverse
    order because the weakest closures are the most likely to be revivable.
    """

    # ── Evidence about the market ────────────────────────────────────────
    REFUTED_BY_NATURE = "refuted_by_nature"
    """Measured with adequate power; the effect is absent or wrong-signed.

    This is the only class that is evidence the edge does not exist. Revival
    requires a structural change in the market itself, not a better instrument.
    Example: attention-coupling, 0/32 pre-declared regime cells (doc 289-290).
    """

    REFUTED_BY_COST = "refuted_by_cost"
    """The gross effect may well be real; frictions exceed it.

    A law of the *current cost structure*, not of nature. Revivable if and only
    if the cost structure changes: a different venue, instrument, broker, or
    size regime. Distinguishing this from REFUTED_BY_NATURE matters because the
    revival predicate is completely different.
    Example: the short door — net negative after borrow (doc 283-284, 288).
    """

    STRUCTURALLY_UNAVAILABLE = "structurally_unavailable"
    """The action cannot be taken at all from this account.

    Not a statement about profitability. Revivable only if access changes.
    Example: passive liquidity rebates are paid to the broker, not the customer;
    100% of flow is internalised to wholesalers (doc 298).
    """

    # ── Evidence about us ────────────────────────────────────────────────
    REFUTED_BY_ARITHMETIC = "refuted_by_arithmetic"
    """Never empirically tested; the derived ceiling sits below the requirement.

    Evidence about the *target*, not the market. If the requirement falls or the
    ceiling estimate was wrong, the family returns. Note that under the doc-297
    identity the requirement cannot be lowered by leverage, so "the requirement
    fell" almost always means the target itself was re-scoped.
    Example: LETF close-window harvest — needs annualised Sharpe 22.2 (doc 298).
    """

    UNDERPOWERED = "underpowered"
    """The test could not have detected the effect it was looking for.

    Absence of evidence, filed as such. This is the class the program is most at
    risk of mis-filing as REFUTED_BY_NATURE, which is why doc 275 made
    pre-registered acceptance-test power mandatory. Revivable with more data.
    """

    INSTRUMENT_LIMITED = "instrument_limited"
    """A data or capability gap prevented the test from running at all.

    Carries zero information about the hypothesis. Revives exactly when the
    named keystone lands. The program has already done this twice by hand: the
    day_aggs Q1 rebuild (doc 278) and the LETF point-in-time shares-outstanding
    unblock (doc 295 -> 298).
    """

    VOIDED_BY_DEFECT = "voided_by_defect"
    """The result is invalid because the implementation did not match the spec.

    The purest trash-can case: a hypothesis that was reported on without ever
    having been asked. Carries zero information in either direction.
    Example: doc 296 — runner at h=1, frozen spec at h=21.
    """

    ABANDONED = "abandoned"
    """Stopped for reasons outside the epistemics: budget, attention, fashion.

    Filed honestly rather than dressed up as a refutation. Every closure that
    cannot name a keystone or a measurement lands here. A graveyard with many of
    these is a graveyard that has not been read.
    """

    @property
    def is_evidence_about_market(self) -> bool:
        """Does this closure tell us something about the world, or about us?"""
        return self in _MARKET_EVIDENCE

    @property
    def evidential_weight(self) -> float:
        """How much this closure should move a prior against the hypothesis.

        In [0, 1]. Used by the retro-validation engine to rank revival
        candidates: a hypothesis whose closure carries no evidential weight is
        exactly as open as it was before anyone looked at it.
        """
        return _WEIGHTS[self]

    @property
    def revives_on(self) -> str:
        """What kind of change would license re-opening this family."""
        return _REVIVES_ON[self]


_MARKET_EVIDENCE = frozenset({
    Closure.REFUTED_BY_NATURE,
    Closure.REFUTED_BY_COST,
    Closure.STRUCTURALLY_UNAVAILABLE,
})

# Deliberately not uniform, and deliberately not zero for the cost/access
# classes: "the fee is the fade" is a real and durable finding about this
# market even though it is not a finding about the underlying signal.
_WEIGHTS: dict[Closure, float] = {
    Closure.REFUTED_BY_NATURE: 1.00,
    Closure.REFUTED_BY_COST: 0.80,
    Closure.STRUCTURALLY_UNAVAILABLE: 0.70,
    Closure.REFUTED_BY_ARITHMETIC: 0.40,
    Closure.UNDERPOWERED: 0.15,
    Closure.INSTRUMENT_LIMITED: 0.00,
    Closure.VOIDED_BY_DEFECT: 0.00,
    Closure.ABANDONED: 0.05,
}

_REVIVES_ON: dict[Closure, str] = {
    Closure.REFUTED_BY_NATURE: "a structural regime change in the market",
    Closure.REFUTED_BY_COST: "a cheaper venue, instrument, or size regime",
    Closure.STRUCTURALLY_UNAVAILABLE: "a change in account or venue access",
    Closure.REFUTED_BY_ARITHMETIC: "a re-scoped requirement or a corrected ceiling",
    Closure.UNDERPOWERED: "more observations, or a higher-frequency observable",
    Closure.INSTRUMENT_LIMITED: "the named keystone landing",
    Closure.VOIDED_BY_DEFECT: "re-running the frozen spec as written",
    Closure.ABANDONED: "someone deciding it is worth the attention",
}


@dataclass(frozen=True)
class Keystone:
    """The specific missing piece that prevented a hypothesis from being settled.

    This is the load-bearing field of the whole architecture. A closure without
    a keystone cannot be retro-validated, because there is no predicate to
    evaluate when a new capability arrives. Writing it down at burial time is
    the only moment at which the information is cheap — six months later nobody
    remembers whether the family died of the data or of the idea.

    Attributes
    ----------
    key:
        Stable slug naming the capability. Retro-validation matches on this, so
        it must be reused verbatim across records. See `KEYSTONES` for the
        registered vocabulary.
    description:
        What the capability is, in one line.
    blocks:
        What specifically could not be computed without it.
    """

    key: str
    description: str
    blocks: str = ""

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.key}: {self.description}"


# The registered keystone vocabulary. Free-text keys are accepted but warned
# about, because a typo silently makes a record unrevivable — the failure mode
# this whole module exists to prevent.
KEYSTONES: dict[str, str] = {
    "historical_option_nbbo": "quote-level option data before the collector started",
    "intraday_tape_pre_2020": "trades+quotes for the pre-2020 sample",
    "point_in_time_shares_outstanding": "ETF/LETF share counts as of the close",
    "borrow_fee_timeseries": "per-name, per-day borrow cost and availability",
    "short_locate_access": "the ability to borrow and short at this broker",
    "lit_venue_routing": "order routing that reaches a rebate-paying exchange",
    "n_obs_sufficient": "enough non-overlapping observations to power the test",
    "clean_iv_surface": "an IV panel free of the doc-296 look-ahead",
    "executable_prereg_fixture": "a runnable fixture the runner must reproduce",
    "third_party_ground_truth": "an independent source to validate the primary feed",
    "realised_fill_distribution": "our own fills, at size, in the target regime",
    "regime_coverage": "a sample spanning more than one volatility regime",
    "lower_requirement": "a re-scoped account-level target (Pierce's call, not a build)",
    "capacity_headroom": "an account size at which the effect is not impact-dominated",
}


@dataclass
class ClosureRecord:
    """A closure, with its discrimination made explicit.

    `validate()` enforces the rule that gives the taxonomy its teeth: a
    non-market closure must name at least one keystone. Without that rule the
    taxonomy degrades into relabelled "failed".
    """

    family: str
    closure: Closure
    rationale: str
    keystones: list[Keystone] = field(default_factory=list)
    effect: float | None = None
    ci: tuple[float, float] | None = None
    n_obs: int | None = None
    docs: list[str] = field(default_factory=list)

    def validate(self) -> list[str]:
        """Return a list of problems; empty means the record is admissible."""
        problems: list[str] = []

        if not self.family.strip():
            problems.append("family is empty")
        if not self.rationale.strip():
            problems.append("rationale is empty")

        # ABANDONED is exactly the "no keystone" bucket, so exempt it: demanding
        # one there would push honest abandonments back into dressed-up
        # refutations, which is the failure this taxonomy exists to prevent.
        if (
            not self.closure.is_evidence_about_market
            and self.closure is not Closure.ABANDONED
            and not self.keystones
        ):
            problems.append(
                f"{self.closure.value} is a statement about us, not the market, "
                "so it must name at least one keystone — otherwise nothing can "
                "ever revive it and it should be filed as ABANDONED"
            )

        if self.closure is Closure.REFUTED_BY_NATURE:
            # The strongest claim in the taxonomy carries the strongest burden:
            # you may only say nature refused it if you can show the measurement
            # that had the power to see it.
            if self.effect is None or self.ci is None:
                problems.append(
                    "REFUTED_BY_NATURE requires a measured effect and interval; "
                    "without them the honest class is UNDERPOWERED"
                )
            if self.n_obs is None:
                problems.append("REFUTED_BY_NATURE requires n_obs")

        for k in self.keystones:
            if not k.key:
                problems.append("keystone with empty key")

        return problems

    @property
    def unknown_keystones(self) -> list[str]:
        """Keystone keys outside the registered vocabulary (likely typos)."""
        return [k.key for k in self.keystones if k.key not in KEYSTONES]

    def to_dict(self) -> dict:
        return {
            "family": self.family,
            "closure": self.closure.value,
            "rationale": self.rationale,
            "keystones": [
                {"key": k.key, "description": k.description, "blocks": k.blocks}
                for k in self.keystones
            ],
            "effect": self.effect,
            "ci": list(self.ci) if self.ci else None,
            "n_obs": self.n_obs,
            "docs": list(self.docs),
            "is_evidence_about_market": self.closure.is_evidence_about_market,
            "evidential_weight": self.closure.evidential_weight,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ClosureRecord":
        ci = d.get("ci")
        return cls(
            family=d["family"],
            closure=Closure(d["closure"]),
            rationale=d.get("rationale", ""),
            keystones=[
                Keystone(k["key"], k.get("description", ""), k.get("blocks", ""))
                for k in d.get("keystones", [])
            ],
            effect=d.get("effect"),
            ci=(ci[0], ci[1]) if ci else None,
            n_obs=d.get("n_obs"),
            docs=list(d.get("docs", [])),
        )
