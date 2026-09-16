"""Bayesian Online Change Point Detection (BOCPD) — Adams-MacKay 2007.

Per `docs/research-log/25_bug_hunting_playbook.md` §3.1 + the user's items 21-23
(2026-04-25 next-actions list).

Detects regime changes in a sequence of trade outcomes (P&L, R-multiple, or
any standardized residual). On each observation, maintains a posterior
distribution over run length r_t — "trades since the last changepoint."
When P(r_t = 0 | x_{1:t}) > kill_switch_threshold, the system has seen
strong evidence of a regime break and the trading loop should react
(D223 BOCPD_BREAK) — typically by reverting to paper trading or halving
Kelly sizing.

This module implements the simplified-Gaussian variant:
  - Likelihood:  N(x_t | μ_run, σ_known)
  - Prior:       Empirical-Bayes (μ_edge, σ_edge, hazard) from journal corpus
  - Hazard:      Constant H = 1 / expected_run_length

The full Normal-Inverse-Gamma conjugate model (unknown σ per run) is a
v2.1 enhancement; the simplified-Gaussian version captures the regime-
break signal with one fewer hyperparameter to tune from a small corpus.

Prior persistence: `data/priors/s1_bocpd_prior.parquet` — versioned,
dated, reproducible. Reload at session startup so BOCPD is operational
from trade #1, not trade #50.

Wire-in (deferred to follow-up commit):
  main.py session-start: load prior + construct BOCPDState
  bridge.execute_verdict on terminal: state.observe(pnl) → if break: D223
"""
from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

logger = logging.getLogger(__name__)


# ── Schema constants ────────────────────────────────────────────────


PRIOR_SCHEMA_VERSION: int = 1
"""Bumped when BOCPDPrior shape changes. Single source of truth for the
persisted Parquet file. Loaders verify on read."""

DEFAULT_KILL_SWITCH_THRESHOLD: float = 0.85
"""Posterior P(r_t = 0 | x_{1:t}) above this → D223 BOCPD_BREAK fires."""

DEFAULT_HAZARD_RATE: float = 1.0 / 60.0
"""Constant hazard rate. Expected run length = 60 trades (~1-2 weeks of
typical trading). Empirical-Bayes overrides this if the corpus has enough
data to estimate it."""

DEFAULT_CP_WIDENING_FACTOR: float = 3.0
"""The changepoint arm uses N(x | μ_edge, σ_edge * cp_widening_factor)
as its predictive — wider than the growth-arm predictive — to model the
Bayesian "we don't know where the new regime mean will land" uncertainty.
Without this widening (i.e. = 1.0), the cp arm and the long-run-growth
arms get equal predictive density at any x ≠ μ_edge, and P(r_t=0) never
spikes above the hazard rate even during a 20σ regime break.

This is the simplified-Gaussian analogue of the Student-t predictive
that comes out of the full Normal-Inverse-Gamma conjugate model. With
cp_widening_factor = 3, the cp arm tolerates jumps up to ~3σ of σ_edge
without losing predictive density relative to a tight growth arm."""


# ── Data classes ────────────────────────────────────────────────────


@dataclass(frozen=True)
class BOCPDPrior:
    """Empirical-Bayes-trained prior — μ_edge, σ_edge, hazard, plus
    metadata for reproducibility.

    Attributes:
      mu_edge:        Prior mean of trade outcome under stable regime.
      sigma_edge:     Prior std (assumed-known likelihood scale).
      hazard_rate:    Constant per-step changepoint probability.
      n_trades:       Number of trades used to fit the prior.
      filtered_count: Number of trades EXCLUDED (e.g. infrastructure_contaminated).
      corpus_dates:   Tuple of session_dates contributing to the prior.
      generated_at:   ISO timestamp of pre-training.
      schema_version: Persistent schema version.
    """
    mu_edge: float
    sigma_edge: float
    hazard_rate: float
    n_trades: int
    filtered_count: int
    corpus_dates: tuple[str, ...]
    generated_at: str
    schema_version: int = PRIOR_SCHEMA_VERSION

    # ── Persistence ──────────────────────────────────────────

    def to_parquet(self, path: Path | str) -> None:
        """Atomic-write the prior as a one-row Parquet file."""
        import os
        import pandas as pd

        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        row = asdict(self)
        # corpus_dates → JSON string (Parquet doesn't store tuples cleanly across versions)
        row["corpus_dates"] = ",".join(self.corpus_dates)
        pd.DataFrame([row]).to_parquet(tmp, index=False)
        os.replace(tmp, path)

    @classmethod
    def from_parquet(cls, path: Path | str) -> "BOCPDPrior":
        """Load a previously-persisted prior. Verifies schema version."""
        import pandas as pd
        df = pd.read_parquet(path)
        if df.empty:
            raise ValueError(f"Empty prior file: {path}")
        row = df.iloc[0].to_dict()
        sv = int(row.get("schema_version", 0))
        if sv != PRIOR_SCHEMA_VERSION:
            raise ValueError(
                f"Prior schema version mismatch: file={sv}, expected={PRIOR_SCHEMA_VERSION}"
            )
        dates_raw = row.get("corpus_dates", "") or ""
        dates = tuple(d for d in dates_raw.split(",") if d)
        return cls(
            mu_edge=float(row["mu_edge"]),
            sigma_edge=float(row["sigma_edge"]),
            hazard_rate=float(row["hazard_rate"]),
            n_trades=int(row["n_trades"]),
            filtered_count=int(row["filtered_count"]),
            corpus_dates=dates,
            generated_at=str(row["generated_at"]),
            schema_version=sv,
        )

    # ── Empirical-Bayes pre-training ─────────────────────────

    @classmethod
    def from_corpus(
        cls,
        rows: Iterable[dict],
        *,
        outcome_field: str = "pnl",
        contaminated_field: str = "infrastructure_contaminated",
        date_field: str = "session_date",
        hazard_rate: float | None = None,
    ) -> "BOCPDPrior":
        """Fit an empirical-Bayes prior from a journal corpus.

        Filters rows where `contaminated_field` is truthy. Computes:
          μ_edge       = sample mean of outcomes
          σ_edge       = sample std of outcomes (Bessel-corrected; floor 0.01)
          hazard_rate  = caller-provided OR DEFAULT_HAZARD_RATE
          corpus_dates = sorted unique dates in the filtered set

        Empty post-filter corpus → returns a default prior with metadata
        flagging zero training data; the caller decides whether to fall
        back to a hand-tuned prior or skip BOCPD entirely for the session.
        """
        clean: list[float] = []
        filtered = 0
        dates: set[str] = set()

        for row in rows:
            if row.get(contaminated_field):
                filtered += 1
                continue
            try:
                v = float(row[outcome_field])
            except (KeyError, TypeError, ValueError) as _e:
                logger.debug("BOCPD pretrain: skipping row, missing/bad outcome: %s", _e)
                continue
            clean.append(v)
            d = row.get(date_field)
            if d:
                dates.add(str(d))

        if not clean:
            return cls(
                mu_edge=0.0,
                sigma_edge=1.0,
                hazard_rate=hazard_rate or DEFAULT_HAZARD_RATE,
                n_trades=0,
                filtered_count=filtered,
                corpus_dates=tuple(),
                generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            )

        n = len(clean)
        mu = sum(clean) / n
        if n > 1:
            var = sum((x - mu) ** 2 for x in clean) / (n - 1)
            sigma = max(math.sqrt(var), 0.01)
        else:
            sigma = max(abs(mu), 1.0)  # single observation — can't estimate scale
        return cls(
            mu_edge=mu,
            sigma_edge=sigma,
            hazard_rate=hazard_rate or DEFAULT_HAZARD_RATE,
            n_trades=n,
            filtered_count=filtered,
            corpus_dates=tuple(sorted(dates)),
            generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )


# ── Online state ────────────────────────────────────────────────────


def _gaussian_logpdf(x: float, mu: float, sigma: float) -> float:
    """Log-density of N(x | mu, sigma). Numerically stable."""
    z = (x - mu) / sigma
    return -0.5 * z * z - math.log(sigma) - 0.5 * math.log(2.0 * math.pi)


@dataclass
class BOCPDObservation:
    """One observe() call's return value."""
    posterior_changepoint: float       # P(r_t = 0 | x_{1:t})
    most_likely_run_length: int        # argmax_r P(r_t = r | x_{1:t})
    expected_run_length: float         # E[r_t | x_{1:t}]
    n_observations: int
    kill_switch_triggered: bool


class BOCPDState:
    """Online BOCPD state. One per session.

    Maintains:
      run_lengths[r]    — P(r_t = r | x_{1:t})
      run_means[r]      — running mean of obs assigned to each run length
                          (used for Gaussian-known-σ likelihood)
      run_counts[r]     — observation count per run length

    Args:
      prior:                  BOCPDPrior with mu_edge, sigma_edge, hazard_rate
      kill_switch_threshold:  Posterior changepoint above this fires D223
      max_run_length:         Truncate beyond this for memory bound (default 500)
    """

    def __init__(
        self,
        prior: BOCPDPrior,
        *,
        kill_switch_threshold: float = DEFAULT_KILL_SWITCH_THRESHOLD,
        max_run_length: int = 500,
        cp_widening_factor: float = DEFAULT_CP_WIDENING_FACTOR,
        kelly_governor: object | None = None,
    ) -> None:
        self.prior = prior
        self.kill_switch_threshold = float(kill_switch_threshold)
        self.max_run_length = int(max_run_length)
        self.cp_widening_factor = float(cp_widening_factor)
        # Optional KellyGovernor — if provided, on_break_detected is
        # called when the kill switch triggers. None = log-only behavior.
        self.kelly_governor = kelly_governor
        # Initial state: run length 0 (we're starting fresh)
        self.run_lengths: list[float] = [1.0]
        self.run_means: list[float] = [prior.mu_edge]
        self.run_counts: list[int] = [0]
        self.n_observations: int = 0
        self.history: list[BOCPDObservation] = []

    # ── Observe ────────────────────────────────────────────────

    def observe(self, x: float) -> BOCPDObservation:
        """Update state on a new observation. Returns posterior summary."""
        sigma = self.prior.sigma_edge
        H = self.prior.hazard_rate

        # 1a. Growth predictive: per-run-length mean, narrow sigma (we know
        #     this run's mean from the running average).
        log_pred_growth = [
            _gaussian_logpdf(x, self.run_means[r], sigma)
            for r in range(len(self.run_lengths))
        ]
        # 1b. Changepoint predictive: prior mean, WIDENED sigma. This is
        #     the Bayesian "we don't know where the new regime mean will
        #     land" uncertainty. Without widening, the cp arm gets equal
        #     density to the growth arms at any x ≠ μ_edge and P(r_t=0)
        #     never spikes above the hazard rate.
        log_pred_cp = _gaussian_logpdf(
            x, self.prior.mu_edge, sigma * self.cp_widening_factor,
        )

        # 2. Update run-length distribution (Adams-MacKay growth + changepoint)
        # Growth: P_new[r+1] ∝ P_old[r] * π_growth_r * (1 - H)
        # Changepoint: P_new[0] ∝ Σ P_old[r] * π_cp * H
        # Stabilize by subtracting the max log-pred before exp.
        max_lp = max([log_pred_cp] + log_pred_growth)
        growth = [
            self.run_lengths[r] * math.exp(log_pred_growth[r] - max_lp) * (1.0 - H)
            for r in range(len(self.run_lengths))
        ]
        cp_mass = sum(self.run_lengths) * math.exp(log_pred_cp - max_lp) * H

        # New distribution: [P(r=0), P(r=1), P(r=2), ...]
        new_dist = [cp_mass] + growth
        # Truncate
        if len(new_dist) > self.max_run_length:
            new_dist = new_dist[: self.max_run_length]
        # Normalize
        total = sum(new_dist) or 1.0
        new_dist = [p / total for p in new_dist]

        # 3. Update sufficient statistics for each run length
        # Run length r at step t corresponds to having seen x_{t-r+1}, ..., x_t
        # in this regime. Running mean:
        # new_run_means[r+1] = old_run_means[r] + (x - old_run_means[r]) / (count + 1)
        new_means = [self.prior.mu_edge]  # r=0 reset to prior mean
        new_counts = [0]
        for r in range(len(self.run_means)):
            old_count = self.run_counts[r]
            old_mean = self.run_means[r]
            new_count = old_count + 1
            new_mean = old_mean + (x - old_mean) / new_count
            new_means.append(new_mean)
            new_counts.append(new_count)
        # Truncate to match new_dist
        new_means = new_means[: len(new_dist)]
        new_counts = new_counts[: len(new_dist)]

        self.run_lengths = new_dist
        self.run_means = new_means
        self.run_counts = new_counts
        self.n_observations += 1

        # 4. Compute summary statistics
        post_cp = new_dist[0]
        most_likely = max(range(len(new_dist)), key=lambda r: new_dist[r])
        expected_rl = sum(r * p for r, p in enumerate(new_dist))
        triggered = post_cp > self.kill_switch_threshold

        obs = BOCPDObservation(
            posterior_changepoint=post_cp,
            most_likely_run_length=most_likely,
            expected_run_length=expected_rl,
            n_observations=self.n_observations,
            kill_switch_triggered=triggered,
        )
        self.history.append(obs)
        if triggered:
            logger.warning(
                "D223 BOCPD_BREAK posterior_cp=%.3f > threshold=%.3f after %d obs",
                post_cp, self.kill_switch_threshold, self.n_observations,
            )
            # Fire the Kelly governor if wired (D224 KELLY_HALVED logged inside)
            if self.kelly_governor is not None:
                try:
                    self.kelly_governor.on_break_detected(post_cp)
                except Exception as _ke:
                    logger.warning("Kelly governor failed on D223: %s", _ke)
        return obs

    # ── Convenience ────────────────────────────────────────────

    def observe_sequence(self, xs: Sequence[float]) -> list[BOCPDObservation]:
        """Observe a batch of values; return per-step observation list."""
        return [self.observe(x) for x in xs]

    @classmethod
    def from_prior_path(
        cls,
        path: Path | str,
        *,
        kill_switch_threshold: float = DEFAULT_KILL_SWITCH_THRESHOLD,
    ) -> "BOCPDState":
        """Load prior from Parquet and construct fresh state."""
        prior = BOCPDPrior.from_parquet(path)
        return cls(prior=prior, kill_switch_threshold=kill_switch_threshold)


# ── Re-fit diff helper (EOD scheduling) ────────────────────────────


@dataclass(frozen=True)
class RefitDiff:
    """Comparison between an existing prior and a fresh re-fit on the
    current corpus. Drives EOD reporting + the operator-facing refit
    recommendation.

    Attributes:
      old_prior:        BOCPDPrior loaded from disk (or None if missing)
      new_prior:        BOCPDPrior fit from current corpus
      delta_mu:         |new_mu - old_mu| (0 if old missing)
      delta_sigma:      |new_sigma - old_sigma| (0 if old missing)
      delta_n_trades:   new.n_trades - old.n_trades
      recommend_refit:  Boolean — based on drift OR sample-size threshold
      reason:           Short string explaining recommend_refit
    """
    old_prior: BOCPDPrior | None
    new_prior: BOCPDPrior
    delta_mu: float
    delta_sigma: float
    delta_n_trades: int
    recommend_refit: bool
    reason: str


def bocpd_refit_diff(
    corpus_rows: list[dict],
    *,
    prior_path: Path | str | None = None,
    drift_mu_threshold: float | None = None,
    drift_sigma_rel_threshold: float = 0.20,
    new_trades_threshold: int = 10,
    hazard_rate: float | None = None,
) -> RefitDiff:
    """Compute the diff between the persisted prior (if any) and a fresh
    re-fit on `corpus_rows`. Returns a RefitDiff with `recommend_refit`
    set based on:

      - delta_n_trades >= new_trades_threshold  (we have more data), OR
      - delta_mu > drift_mu_threshold           (mean shifted), OR
      - delta_sigma / old_sigma > drift_sigma_rel_threshold  (scale shifted)

    Defaults:
      drift_mu_threshold = old_prior.sigma_edge / 4 (mu shift > 0.25σ)
      drift_sigma_rel_threshold = 0.20 (sigma changed by ≥20%)
      new_trades_threshold = 10

    If `prior_path` is None or the file doesn't exist, treats it as
    "no prior on disk" — recommend_refit=True, reason='no_prior_file'.
    """
    new_prior = BOCPDPrior.from_corpus(corpus_rows, hazard_rate=hazard_rate)

    old_prior: BOCPDPrior | None = None
    if prior_path is not None:
        try:
            p = Path(prior_path)
            if p.exists():
                old_prior = BOCPDPrior.from_parquet(p)
        except Exception as e:
            logger.warning("BOCPD refit: failed to load existing prior at %s: %s", prior_path, e)

    if old_prior is None:
        return RefitDiff(
            old_prior=None,
            new_prior=new_prior,
            delta_mu=0.0,
            delta_sigma=0.0,
            delta_n_trades=new_prior.n_trades,
            recommend_refit=True,
            reason="no_prior_file",
        )

    delta_mu = abs(new_prior.mu_edge - old_prior.mu_edge)
    delta_sigma = abs(new_prior.sigma_edge - old_prior.sigma_edge)
    delta_n = new_prior.n_trades - old_prior.n_trades
    eff_mu_thresh = (
        drift_mu_threshold if drift_mu_threshold is not None
        else max(old_prior.sigma_edge / 4.0, 0.01)
    )
    eff_sigma_rel = (
        delta_sigma / max(old_prior.sigma_edge, 1e-6)
    )

    reasons: list[str] = []
    if delta_n >= new_trades_threshold:
        reasons.append(f"n_trades grew by {delta_n} (>={new_trades_threshold})")
    if delta_mu > eff_mu_thresh:
        reasons.append(f"mu drift {delta_mu:.4f} > {eff_mu_thresh:.4f}")
    if eff_sigma_rel > drift_sigma_rel_threshold:
        reasons.append(
            f"sigma drift {eff_sigma_rel*100:.1f}% > {drift_sigma_rel_threshold*100:.1f}%"
        )

    return RefitDiff(
        old_prior=old_prior,
        new_prior=new_prior,
        delta_mu=delta_mu,
        delta_sigma=delta_sigma,
        delta_n_trades=delta_n,
        recommend_refit=bool(reasons),
        reason="; ".join(reasons) or "stable_no_refit_needed",
    )
