"""Experiment selection by expected information gain, priced against multiplicity.

The gap this closes
-------------------
`scripts/trial_registry.py` prices a trial *after* it runs: it counts how many
times the program has looked and raises the bar accordingly. Nothing prices a
trial *before* it runs. Experiments have been chosen by narrative interest —
which is how a program ends up having spent 33 trials and raised its own bar
beyond what any of them could clear.

Two quantities, computed here, change that.

**1. Expected information gain (EIG).** How much uncertainty about the effect
would this experiment actually resolve? A test with n_obs so small that the
posterior equals the prior teaches nothing regardless of its outcome. Doc 275
made pre-registered acceptance-test *power* mandatory; this is that rule moved
to the design stage, where it is free.

**2. The multiplicity toll.** This is the part standard Bayesian experimental
design does not model, and it is the dominant cost in a program like this one.
Under a Bailey/Lopez de Prado gate the bar rises with the number of trials, so
running an experiment does not merely spend time — it *permanently raises the
threshold every future experiment must clear*. At 33 registered trials on two
years of daily data the operative bar is already 2.6 annualised Sharpe. Each
additional trial pushes it further out of reach. A trial with negligible EIG is
therefore not free and not harmless: it is a tax levied on every hypothesis
still in the queue.

Put together, the decision rule is no longer "is this interesting?" but:

    Does what this experiment teaches exceed what it costs the rest of the
    program in certification headroom?

That question has an answer, it is computable before any data is touched, and
for most of what a trading program wants to try the answer is no.

Robustness to misspecification
------------------------------
Classical EIG assumes the likelihood is right. For market data it is not: the
data-generating process is non-stationary, the program has already seen one
"pass" void on a horizon mismatch (doc 296) and one headline refuted by its own
retest (doc 277). Following generalised Bayesian experimental design, the
likelihood is tempered by an epistemic learning rate `omega` in (0, 1], which
inflates the effective observation variance by 1/omega. `omega=1` recovers the
classical case and should be regarded as the optimistic bound, not the default.

All formulae below are closed-form under a Gaussian conjugate model. That model
is a simplification, stated rather than hidden: effects are treated as normal
and estimators as unbiased. It is adequate for ranking designs, which is the
only claim made for it here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

# The bar arithmetic is imported rather than re-derived so the planner and the
# gate cannot drift apart. If trial_registry's convention changes, this changes
# with it.
try:  # pragma: no cover - import plumbing
    from scripts.trial_registry import expected_max_sharpe
except ImportError:  # pragma: no cover
    import importlib.util
    import pathlib

    _spec = importlib.util.spec_from_file_location(
        "_trial_registry",
        pathlib.Path(__file__).resolve().parents[2] / "scripts" / "trial_registry.py",
    )
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)  # type: ignore[union-attr]
    expected_max_sharpe = _mod.expected_max_sharpe

_Z_ONE_SIDED_95 = 1.6449
"""The margin trial_registry.clears() actually enforces on top of E[max]."""


def _phi(x: float) -> float:
    """Standard normal CDF."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def sharpe_se(n_obs: int, periods_per_year: int = 252) -> float:
    """Standard error of an annualised Sharpe estimate.

    Matches `trial_registry`'s convention exactly: sqrt(ppy / n_obs).
    """
    return math.sqrt(periods_per_year / max(int(n_obs), 2))


def effective_n(n_obs: int, cluster_size: int = 1, icc: float = 0.0) -> float:
    """Sample size after the design-effect correction for clustering.

    The program's standing denominator-honesty rule (doc 278, and the 2.5x SE
    inflation measured at intraclass rho 0.09-0.35) belongs at the *design*
    stage, not only in the post-hoc bootstrap. A design that looks powered on
    nominal n and is unpowered on effective n should never be run, and that is
    knowable before any data is touched.

    n_eff = n_obs / (1 + (m - 1) * rho)
    """
    m = max(int(cluster_size), 1)
    rho = max(float(icc), 0.0)
    design_effect = 1.0 + (m - 1) * rho
    return max(n_obs / design_effect, 2.0)


def operative_bar(n_trials: int, n_obs: int, periods_per_year: int = 252) -> float:
    """The threshold `trial_registry.clears()` enforces at this trial count."""
    emax = expected_max_sharpe(
        max(int(n_trials), 2), n_obs=n_obs, periods_per_year=periods_per_year
    )
    return emax + _Z_ONE_SIDED_95 * sharpe_se(n_obs, periods_per_year)


@dataclass(frozen=True)
class Design:
    """A candidate experiment, specified before any data is touched.

    Attributes
    ----------
    name:
        Identifier for the candidate.
    family:
        Mechanism class, used by the diversity term. Candidates sharing a family
        are near-substitutes for the purpose of exploration.
    n_obs:
        Nominal observation count.
    cluster_size, icc:
        Clustering structure; see `effective_n`. Defaults assume independence,
        which for intraday equity data is almost always wrong.
    prior_mean, prior_sd:
        Prior over the *true* annualised Sharpe of the effect. `prior_mean = 0`
        is the program's own measured base rate and is the honest default;
        anything else is a claim that needs its own justification.
    cost_days:
        Analyst/compute days to run it. Used for EIG-per-day ranking.
    keystones_required:
        Capabilities this design needs. A design requiring an unavailable
        keystone is infeasible regardless of how well it scores.
    """

    name: str
    family: str
    n_obs: int
    prior_mean: float = 0.0
    prior_sd: float = 0.5
    cluster_size: int = 1
    icc: float = 0.0
    periods_per_year: int = 252
    cost_days: float = 1.0
    keystones_required: tuple[str, ...] = ()

    @property
    def n_eff(self) -> float:
        return effective_n(self.n_obs, self.cluster_size, self.icc)

    def obs_sd(self, omega: float = 1.0) -> float:
        """Effective sd of the Sharpe estimate, tempered by `omega`.

        The Gibbs posterior with learning rate omega behaves, in the Gaussian
        case, like a likelihood whose variance is inflated by 1/omega. Lower
        omega means the design is credited with less learning per observation.
        """
        w = min(max(float(omega), 1e-6), 1.0)
        return sharpe_se(int(self.n_eff), self.periods_per_year) / math.sqrt(w)


@dataclass
class Appraisal:
    """What a design is worth, and what it costs the program."""

    design: Design
    eig_nats: float
    posterior_sd: float
    severity: float
    p_cert: float
    p_false_positive: float
    bar_before: float
    bar_after: float
    toll_sharpe: float
    toll_p_cert: float
    mde: float
    feasible: bool = True
    blocked_by: list[str] = field(default_factory=list)

    @property
    def eig_per_day(self) -> float:
        return self.eig_nats / max(self.design.cost_days, 1e-9)

    @property
    def informativeness(self) -> float:
        """How much more likely a pass is under the prior than under the null.

        `p_cert / p_false_positive`. A ratio near 1 means a passing result is
        indistinguishable from noise: the design would certify a true effect at
        almost exactly the rate at which it certifies nothing at all, so its
        verdict carries no information whichever way it lands.

        This is doc 290's standing rule — always rank-calibrate against the
        strong baseline, because a weak baseline manufactures lift — applied to
        the *design* rather than to a model. The baseline here is the null.
        """
        if self.p_false_positive <= 0.0:
            return float("inf") if self.p_cert > 0 else 1.0
        return self.p_cert / self.p_false_positive

    @property
    def is_ceremonial(self) -> bool:
        """A test that can neither teach nor discriminate.

        The invariance that motivates this belongs to `p_false_positive`, not to
        `p_cert`. Because the bar and the estimator's spread carry the same
        standard error, bar/sigma_e = sqrt(omega) * (E[max]/se + 1.6449) with n
        cancelling exactly, so p_false_positive is identical at every sample
        length: 2.9342% at omega=0.25 and 34 trials, from one year to a hundred.

        `p_cert` is NOT invariant. It also carries the prior, through
        sqrt(sigma_0^2 + sigma_e^2), and at this module's own default
        prior_sd=0.5 it runs from 3.3% at one year to 24.1% at a hundred. An
        earlier version of this docstring, and doc 299, asserted the invariance
        of p_cert. That was wrong; corrected by the doc-300 audit.

        The consequence for this flag is unchanged, and it is still why a ratio
        beats an absolute floor: a design whose pass is no more likely under the
        prior than under the null has proved nothing, whatever its sample size.

        One honest caveat. For an UNCLUSTERED design p_false_positive depends
        only on (n_trials, omega), so within a single planning run
        `informativeness < 1.5` reduces to an absolute floor on p_cert at
        1.5 * p_fp. It is not a floor across runs, and not one for clustered
        designs, but the difference from an absolute threshold is smaller than
        doc 299 originally implied.
        """
        return self.eig_nats < 0.05 and self.informativeness < 1.5

    @property
    def verdict(self) -> str:
        if not self.feasible:
            return "INFEASIBLE"
        if self.is_ceremonial:
            return "CEREMONIAL — a pass would be indistinguishable from noise"
        if self.informativeness < 1.5:
            return "UNDISCRIMINATING — informative to run, but a pass proves nothing"
        if self.p_cert < 0.01 <= self.eig_nats:
            return "INFORMATIVE BUT UNCERTIFIABLE — run only to learn, never to promote"
        if self.toll_p_cert > self.p_cert:
            return "NEGATIVE-SUM — costs the queue more certification than it can win"
        return "ADMISSIBLE"

    def to_dict(self) -> dict:
        return {
            "name": self.design.name,
            "family": self.design.family,
            "n_obs": self.design.n_obs,
            "n_eff": round(self.design.n_eff, 1),
            "eig_nats": round(self.eig_nats, 4),
            "eig_per_day": round(self.eig_per_day, 4),
            "posterior_sd": round(self.posterior_sd, 4),
            "prior_sd": round(self.design.prior_sd, 4),
            "severity": round(self.severity, 4),
            "p_cert": round(self.p_cert, 6),
            "p_false_positive": round(self.p_false_positive, 6),
            "informativeness": round(self.informativeness, 3),
            "mde": round(self.mde, 4),
            "bar_before": round(self.bar_before, 4),
            "bar_after": round(self.bar_after, 4),
            "toll_sharpe": round(self.toll_sharpe, 4),
            "toll_p_cert": round(self.toll_p_cert, 4),
            "feasible": self.feasible,
            "blocked_by": list(self.blocked_by),
            "verdict": self.verdict,
        }


def appraise(
    design: Design,
    *,
    n_trials_registered: int,
    omega: float = 0.25,
    queue: list[Design] | None = None,
    available_keystones: set[str] | None = None,
) -> Appraisal:
    """Price one candidate experiment.

    Parameters
    ----------
    n_trials_registered:
        The program's current trial count. The bar depends on it, so the value
        of an experiment depends on the program's own history — which is the
        whole point.
    omega:
        Epistemic learning rate in (0, 1]. The default of 0.25 is a governance
        choice, not a measurement: it says one market observation is worth about
        a quarter of an observation from a correctly specified model. It is set
        pessimistically on the program's own record of results that did not
        survive retest (docs 277, 296). Pass `omega=1.0` to see the optimistic
        bound; the gap between the two is the honest uncertainty band.
    queue:
        Other designs still hoping to certify. The toll is the certification
        probability this experiment destroys across that queue by raising the
        bar. Without a queue the toll is reported in Sharpe units only.
    available_keystones:
        Capabilities the program currently has. If omitted, feasibility is not
        checked.

    Returns
    -------
    Appraisal
    """
    sigma_0 = max(float(design.prior_sd), 1e-9)
    sigma_e = design.obs_sd(omega)

    # ── Expected information gain (exact, Gaussian conjugate) ────────────
    # theta ~ N(mu0, sigma_0^2), y | theta ~ N(theta, sigma_e^2).
    # The posterior variance does not depend on the realised y, so the
    # expectation over y is unnecessary and the KL divergence is closed-form:
    #   EIG = 0.5 * ln(1 + sigma_0^2 / sigma_e^2)   nats
    ratio = (sigma_0 / sigma_e) ** 2
    eig = 0.5 * math.log1p(ratio)
    posterior_sd = math.sqrt(1.0 / (1.0 / sigma_0**2 + 1.0 / sigma_e**2))

    # ── The bar, before and after this trial is registered ───────────────
    # On the EFFECTIVE sample, not the nominal one. The bar answers "what is the
    # best Sharpe the null would produce given this much sampling noise", so it
    # must see the same noise the estimator does. Computing it on nominal n while
    # computing sigma_e on n_eff made every clustered design's severity, p_cert
    # and mde wrong, and broke the n-cancellation that makes p_false_positive
    # invariant. Caught by the doc-300 audit.
    n_bar = int(design.n_eff)
    bar_before = operative_bar(n_trials_registered, n_bar, design.periods_per_year)
    bar_after = operative_bar(n_trials_registered + 1, n_bar, design.periods_per_year)
    toll_sharpe = bar_after - bar_before

    # ── Severity (Popper via Mayo): would this test have failed if the
    #    hypothesis were false? Computed at the bar this trial must clear.
    severity = _phi(bar_after / sigma_e)

    # ── Certification probability under the prior ────────────────────────
    # Predictive distribution of the estimate is N(mu0, sigma_0^2 + sigma_e^2).
    s_pred = math.sqrt(sigma_0**2 + sigma_e**2)
    p_cert = 1.0 - _phi((bar_after - design.prior_mean) / s_pred)

    # The rate at which this design certifies nothing at all. `severity` is its
    # complement: the probability the test would have failed had the hypothesis
    # been false, which is what makes a pass mean anything (Popper via Mayo).
    p_false_positive = 1.0 - severity

    # Minimum detectable effect at 80% power against the bar.
    mde = bar_after + 0.8416 * sigma_e

    # ── The toll the rest of the queue pays ──────────────────────────────
    toll_p_cert = 0.0
    for other in queue or []:
        if other.name == design.name:
            continue
        s_o = other.obs_sd(omega)
        s_pred_o = math.sqrt(max(other.prior_sd, 1e-9) ** 2 + s_o**2)
        n_bar_o = int(other.n_eff)
        b_before = operative_bar(n_trials_registered, n_bar_o, other.periods_per_year)
        b_after = operative_bar(n_trials_registered + 1, n_bar_o, other.periods_per_year)
        before = 1.0 - _phi((b_before - other.prior_mean) / s_pred_o)
        after = 1.0 - _phi((b_after - other.prior_mean) / s_pred_o)
        toll_p_cert += max(before - after, 0.0)

    feasible = True
    blocked: list[str] = []
    if available_keystones is not None:
        blocked = [k for k in design.keystones_required if k not in available_keystones]
        feasible = not blocked

    return Appraisal(
        design=design,
        eig_nats=eig,
        posterior_sd=posterior_sd,
        severity=severity,
        p_cert=p_cert,
        p_false_positive=p_false_positive,
        bar_before=bar_before,
        bar_after=bar_after,
        toll_sharpe=toll_sharpe,
        toll_p_cert=toll_p_cert,
        mde=mde,
        feasible=feasible,
        blocked_by=blocked,
    )


def shannon_entropy(counts: dict[str, int] | list[str]) -> float:
    """Shannon entropy of a family histogram, in nats."""
    if isinstance(counts, list):
        hist: dict[str, int] = {}
        for c in counts:
            hist[c] = hist.get(c, 0) + 1
        counts = hist
    total = sum(counts.values())
    if total <= 0:
        return 0.0
    h = 0.0
    for n in counts.values():
        if n > 0:
            p = n / total
            h -= p * math.log(p)
    return h


def diversity_bonus(
    design: Design, history: dict[str, int] | list[str], *, lam: float = 0.5
) -> float:
    """How much adding this design would raise the program's family entropy.

    Guards against the failure mode the framework calls mode collapse: a search
    that keeps re-testing minor variants of one mechanism while leaving the rest
    of the space uncharted.

    Measured on this program, that has NOT happened at the level of mechanism
    class. The 25 archived closures spread over 10 classes with entropy 2.068
    nats against a uniform maximum of 2.303 — 89.8% of maximum, with the largest
    class (per-name price/momentum) at only 8 of 25. The term is a guard for
    future selection, not a diagnosis of past selection, and saying otherwise
    would be the easy and wrong reading.

    What doc 290 and doc 291 found is a different thing that is easy to conflate
    with it: within the families that were tested, *generic* features carried
    whatever signal was present and momentum-x's own vocabulary contributed
    about nothing. That is concentration in feature space, not in hypothesis
    space, and this function does not measure it.

    Note also that the entropy depends on how finely classes are cut, and the
    assignment in `data/research/design_queue.json` is a hand-made judgement. A
    coarser taxonomy would show more concentration. The number is reported with
    its histogram for that reason.

    Returns lam * (H_after - H_before), which is positive for an
    under-represented family and slightly negative for an over-represented one.
    """
    hist: dict[str, int] = {}
    if isinstance(history, list):
        for c in history:
            hist[c] = hist.get(c, 0) + 1
    else:
        hist = dict(history)

    h_before = shannon_entropy(hist)
    hist[design.family] = hist.get(design.family, 0) + 1
    h_after = shannon_entropy(hist)
    return lam * (h_after - h_before)


def rank(
    designs: list[Design],
    *,
    n_trials_registered: int,
    omega: float = 0.25,
    history: dict[str, int] | list[str] | None = None,
    lam: float = 0.5,
    available_keystones: set[str] | None = None,
) -> list[tuple[Appraisal, float]]:
    """Rank candidate experiments by entropy-regularised net value.

    score = EIG_omega + lam * dH  -  penalty(toll)

    The toll enters as a penalty in *nats* by converting the certification
    probability it destroys into the equivalent information the program forgoes.
    The conversion is deliberately crude — one unit of destroyed certification
    probability across the queue is charged at one nat — because the point is to
    make the trade-off visible and arguable, not to pretend it is calibrated.
    Anyone who disagrees with the exchange rate can change `lam` and re-run; the
    ranking is reported alongside its inputs for exactly that reason.

    Infeasible designs are ranked last regardless of score.
    """
    hist = history or {}
    out: list[tuple[Appraisal, float]] = []
    for d in designs:
        a = appraise(
            d,
            n_trials_registered=n_trials_registered,
            omega=omega,
            queue=designs,
            available_keystones=available_keystones,
        )
        score = a.eig_nats + diversity_bonus(d, hist, lam=lam) - a.toll_p_cert
        if not a.feasible:
            score = float("-inf")
        out.append((a, score))
    return sorted(out, key=lambda t: -t[1])
