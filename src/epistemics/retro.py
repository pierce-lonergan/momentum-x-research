"""Retro-validation — re-asking the questions that died of our own limitations.

When a new capability lands, most of what it unlocks is invisible, because the
hypotheses it would have rescued were closed months earlier and the reason they
closed is buried in prose. This module makes that retrieval mechanical: given
the set of capabilities the program now has, it returns the archived families
whose stated blocker no longer holds.

The program has done this twice by hand and both times it mattered:

* Doc 278 rebuilt the day_aggs Q1 hole. That keystone had killed V-RACE back in
  doc 273. The rebuild triggered the 235-273 contamination sweep in doc 279,
  which is what makes the program's negative results trustworthy today.
* Doc 295 recorded the LETF family as BLOCKED-AT-$0 on point-in-time shares
  outstanding. When that feed turned out to be served, doc 298 exhumed the
  family, found it *well-powered* against expectation, and killed it honestly on
  arithmetic instead of leaving it in limbo.

Neither was found by searching. Both were remembered, by luck.

The discrimination problem
--------------------------
The framework this is adapted from warns that a discard pile is overwhelmingly
noise with a thin seam of signal, and that a system which indiscriminately
revives its rejects collapses into wishful thinking. That warning applies here
with force: a trading program that re-opens closed families whenever a new data
feed arrives will re-derive the same losses with more decimal places.

Two guards, both hard:

1. **Closures that are evidence about the market do not revive on capability.**
   A keystone arriving cannot undo a measurement. `REFUTED_BY_NATURE`,
   `REFUTED_BY_COST` and `STRUCTURALLY_UNAVAILABLE` are excluded from the
   revival queue and can only be re-opened by an explicit, argued
   market-structure claim (`allow_market_evidence=True`), which is a decision
   for Pierce and not for this module.

2. **Revival is a licence to re-ask, not a licence to believe.** A revived
   family re-enters at the *front of the pre-registration process*, not at the
   front of the promotion queue.

A caveat on guard (2) that the doc-300 audit forced, and that doc 299 stated too
confidently. On the *current* archive the wrong-signed branch of
`_plausibility` is **unreachable**: every record carrying a wrong-signed
interval is also a market-evidence closure, so guard (1) excludes it first, and
both such records additionally name no keystone, so they are skipped anyway.
Two independent exclusions fire before `_plausibility` is consulted. The branch
is defence-in-depth against a record that does not yet exist — worth keeping,
but it is not what is protecting the archive today, and citing it as though it
were was wrong. It must pass the first-principles gate below,
   then be registered as a fresh trial — which raises the bar for everything
   else, exactly as it should.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .archive import Archive, SteppingStone
from .closure import Closure


@dataclass
class Revival:
    """An archived family whose stated blocker may no longer hold."""

    stone: SteppingStone
    satisfied: list[str]
    still_missing: list[str]
    score: float
    reason: str

    @property
    def fully_unblocked(self) -> bool:
        return not self.still_missing

    def to_dict(self) -> dict:
        return {
            "stone_id": self.stone.stone_id,
            "family": self.stone.closure.family,
            "closure": self.stone.closure.closure.value,
            "closed_on": self.stone.closed_on,
            "satisfied_keystones": list(self.satisfied),
            "still_missing": list(self.still_missing),
            "fully_unblocked": self.fully_unblocked,
            "score": round(self.score, 4),
            "reason": self.reason,
            "revival_predicate": self.stone.revival_predicate,
            "docs": list(self.stone.closure.docs),
        }


def _plausibility(stone: SteppingStone) -> float:
    """How much room the death measurement left for the effect to be real.

    Returns a value in [0, 1]. The logic is deliberately conservative:

    * No measurement at all -> 0.5. Genuine ignorance, not optimism.
    * A point estimate with NO interval -> 0.3 if wrong-signed, 0.7 if
      favourable. The sign is real information and was previously discarded:
      this function did not read `effect` at all, so a family whose measured
      point estimate pointed the wrong way scored the same 0.5 as one never
      measured. Both values are compressed toward 0.5 rather than reaching the
      extremes the interval branch can, because a point estimate carries no
      precision and cannot justify a decisive score. Added after the doc-300
      audit; on the current archive this is the branch that actually fires.
    * An interval whose upper bound is below zero -> low. The measurement saw
      the effect pointing the wrong way, and a new data feed does not change the
      sign of something already measured on adequate data.
    * An interval straddling zero -> higher, scaled by how much of it is
      favourable. This is the "absence of evidence" case.

    Note what this deliberately does *not* do: it never returns a high value for
    a tight, wrong-signed interval, no matter which keystone has arrived. The
    low-float gapper universe measured -2.041%/ticket with a day-blocked CI of
    [-2.823, -1.226] across three separate years; no instrument upgrade makes
    that a candidate again.
    """
    effect, ci = stone.closure.effect, stone.closure.ci

    if ci is None:
        if effect is None:
            return 0.5
        # Sign only. See the docstring: compressed toward the ignorance value.
        return 0.3 if effect < 0 else 0.7

    lo, hi = ci
    if hi <= 0:
        # Wholly unfavourable interval. Scale by how close it came to zero, so
        # a marginal negative keeps a little more room than a decisive one.
        width = max(hi - lo, 1e-9)
        return max(0.0, min(0.25, 0.25 * (1.0 - min(abs(hi) / width, 1.0))))
    if lo >= 0:
        return 1.0
    # Straddles zero: the fraction of the interval that is favourable.
    return max(0.0, min(1.0, hi / (hi - lo)))


def find_revivals(
    available_keystones: set[str] | list[str],
    *,
    archive: Archive | None = None,
    allow_market_evidence: bool = False,
    min_score: float = 0.0,
) -> list[Revival]:
    """Return archived families whose blockers the given capabilities address.

    Parameters
    ----------
    available_keystones:
        Capabilities the program now has. Matched verbatim against the keys
        recorded at burial time, which is why `closure.KEYSTONES` exists as a
        controlled vocabulary.
    allow_market_evidence:
        Include closures that are measurements of the market. Off by default;
        see the discrimination-problem guard in this module's docstring.
    min_score:
        Drop revivals scoring at or below this. The default of 0.0 keeps
        everything with any residual openness at all.

    Returns
    -------
    list[Revival], highest score first.
    """
    have = set(available_keystones)
    arc = archive or Archive()
    out: list[Revival] = []

    for stone in arc.load():
        rec = stone.closure
        if rec.closure.is_evidence_about_market and not allow_market_evidence:
            continue

        keys = stone.keystone_keys
        if not keys:
            continue
        satisfied = sorted(keys & have)
        if not satisfied:
            continue
        missing = sorted(keys - have)

        openness = 1.0 - rec.closure.evidential_weight
        coverage = len(satisfied) / len(keys)
        plaus = _plausibility(stone)
        score = openness * coverage * plaus

        if score <= min_score:
            continue

        bits = [
            f"closed as {rec.closure.value} "
            f"(evidential weight {rec.closure.evidential_weight:.2f})",
            f"{len(satisfied)}/{len(keys)} keystones now available",
        ]
        if rec.ci:
            bits.append(f"death interval [{rec.ci[0]:.3f}, {rec.ci[1]:.3f}]")
        if missing:
            bits.append("still blocked on " + ", ".join(missing))
        reason = "; ".join(bits)

        out.append(
            Revival(
                stone=stone,
                satisfied=satisfied,
                still_missing=missing,
                score=score,
                reason=reason,
            )
        )

    return sorted(out, key=lambda r: (-r.score, r.stone.stone_id))


# ──────────────────────────────────────────────────────────────────────────
# The first-principles gate
# ──────────────────────────────────────────────────────────────────────────


@dataclass
class GateResult:
    """Outcome of the immutable-constraint check."""

    passed: bool
    failures: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "failures": list(self.failures),
            "notes": list(self.notes),
        }


def first_principles_gate(
    *,
    gross_edge_bps_per_ticket: float | None = None,
    round_trip_cost_bps: float | None = None,
    executable_at_broker: bool = True,
    required_size_usd: float | None = None,
    adv_usd: float | None = None,
    max_adv_participation: float = 0.01,
    requirement_bps_per_ticket: float | None = None,
    ceiling_fraction_floor: float = 0.10,
) -> GateResult:
    """Constraints no amount of modelling can argue around.

    The analogue of a thermodynamic feasibility check. Its purpose is to stop a
    revived hypothesis consuming a registered trial — and therefore raising the
    bar for everything else — when it is already dead on arithmetic. Every
    threshold here is one the program derived the hard way:

    1. **Cost admissibility.** A gross edge below the measured round-trip cost
       is not an edge. Doc 297's synthesis is that roughly sixty null results
       were one cost-structure result; this is that lesson as a precondition.
       Costs must be true NBBO plus regulatory fees, not a Roll estimate — the
       Roll estimator was measured at 0.56x-3.5x off and non-positive on 29.5%
       of ticker-days.

    2. **Executability.** The short door did not fail on signal quality; it
       failed because the borrow is not there. An inexecutable action scores
       zero regardless of its backtest.

    3. **Capacity.** An edge that requires more than `max_adv_participation` of
       average daily volume does not survive its own market impact.

    4. **The >=10% filter.** Doc 293's rule: a family whose validated ceiling
       is under ten percent of the standing requirement is not built. Under the
       doc-297 identity the requirement cannot be reduced by leverage —
       deployment and turns sit on the requirement side only and are
       sign-preserving, so they cannot rescue a shortfall.

    Any argument named `None` is skipped and recorded as unchecked, so a partial
    gate never silently passes as a full one.
    """
    failures: list[str] = []
    notes: list[str] = []

    if gross_edge_bps_per_ticket is not None and round_trip_cost_bps is not None:
        net = gross_edge_bps_per_ticket - round_trip_cost_bps
        if net <= 0:
            failures.append(
                f"cost-inadmissible: gross {gross_edge_bps_per_ticket:.2f} bps "
                f"- cost {round_trip_cost_bps:.2f} bps = {net:.2f} bps/ticket"
            )
        else:
            notes.append(f"net edge {net:.2f} bps/ticket before capacity")
    else:
        notes.append("cost admissibility UNCHECKED (no cost or edge supplied)")

    if not executable_at_broker:
        failures.append("not executable at this broker (access, not profitability)")

    if required_size_usd is not None and adv_usd is not None and adv_usd > 0:
        participation = required_size_usd / adv_usd
        if participation > max_adv_participation:
            failures.append(
                f"capacity: needs {participation:.1%} of ADV, limit "
                f"{max_adv_participation:.1%}"
            )
        else:
            notes.append(f"participation {participation:.2%} of ADV")
    else:
        notes.append("capacity UNCHECKED (no size or ADV supplied)")

    if (
        requirement_bps_per_ticket is not None
        and gross_edge_bps_per_ticket is not None
        and requirement_bps_per_ticket > 0
    ):
        net = gross_edge_bps_per_ticket - (round_trip_cost_bps or 0.0)
        frac = net / requirement_bps_per_ticket
        if frac < ceiling_fraction_floor:
            failures.append(
                f"ceiling {frac:.1%} of requirement, below the "
                f"{ceiling_fraction_floor:.0%} build filter (doc 293)"
            )
        else:
            notes.append(f"ceiling {frac:.1%} of requirement")
    else:
        notes.append("requirement filter UNCHECKED (no requirement supplied)")

    return GateResult(passed=not failures, failures=failures, notes=notes)


def triage(
    available_keystones: set[str] | list[str], *, archive: Archive | None = None
) -> dict:
    """A summary suitable for printing after any capability lands.

    Reports the revival queue, the keystones that would unlock the most if
    acquired next, and the anomalies the graveyard is holding. The last of these
    is the cheapest source of genuinely uncrowded hypotheses the program has:
    observations it recorded while killing something else, which by construction
    nobody was looking for.
    """
    arc = archive or Archive()
    revivals = find_revivals(available_keystones, archive=arc)
    return {
        "available_keystones": sorted(set(available_keystones)),
        "discrimination": arc.discrimination(),
        "revivals": [r.to_dict() for r in revivals],
        "fully_unblocked": [
            r.stone.closure.family for r in revivals if r.fully_unblocked
        ],
        "keystone_census": [
            {"keystone": k, "families_blocked": n, "families": fams}
            for k, n, fams in arc.keystone_census()
        ],
        "anomalies": [
            {"stone_id": sid, "family": fam, "anomaly": a}
            for sid, fam, a in arc.anomalies()
        ],
    }
