"""BOCPD pre-training + online detection tests.

12 tests across 4 categories:
  Empirical-Bayes pre-training (4): clean corpus, contamination filter,
                                    empty corpus, custom hazard
  Persistence round-trip       (2): write+read, schema-version guard
  Online detection             (4): stable regime, regime break,
                                    kill-switch trigger, observation count
  Edge cases                   (2): single observation, prior path loader
"""
from __future__ import annotations

import random
from pathlib import Path

import pytest

from src.analysis.bocpd import (
    BOCPDObservation,
    BOCPDPrior,
    BOCPDState,
    DEFAULT_HAZARD_RATE,
    DEFAULT_KILL_SWITCH_THRESHOLD,
    PRIOR_SCHEMA_VERSION,
)


# ── 1. Empirical-Bayes pre-training ─────────────────────────────


class TestEmpiricalBayesPretraining:

    def test_fits_clean_corpus(self) -> None:
        rows = [
            {"pnl": 100.0, "session_date": "2026-04-22", "infrastructure_contaminated": False},
            {"pnl": -50.0, "session_date": "2026-04-22", "infrastructure_contaminated": False},
            {"pnl": 75.0, "session_date": "2026-04-23", "infrastructure_contaminated": False},
            {"pnl": -25.0, "session_date": "2026-04-23", "infrastructure_contaminated": False},
        ]
        prior = BOCPDPrior.from_corpus(rows)
        assert prior.n_trades == 4
        assert prior.filtered_count == 0
        assert prior.mu_edge == pytest.approx(25.0, abs=0.001)
        # Sample std with n=4: variance = sum((x-mean)^2)/3
        # values: [100, -50, 75, -25]; mean=25
        # deviations: [75, -75, 50, -50]; squared: [5625, 5625, 2500, 2500]
        # sum_sq = 16250; variance = 16250/3 = 5416.67; std ≈ 73.60
        assert prior.sigma_edge == pytest.approx(73.60, abs=0.5)
        assert prior.corpus_dates == ("2026-04-22", "2026-04-23")
        assert prior.schema_version == PRIOR_SCHEMA_VERSION

    def test_filters_infrastructure_contaminated(self) -> None:
        """LIDR Bug Z-class trades excluded from training per spec."""
        rows = [
            {"pnl": 100.0, "session_date": "2026-04-22", "infrastructure_contaminated": False},
            {"pnl": 421.12, "session_date": "2026-04-24",
             "infrastructure_contaminated": True},  # the LIDR fake-positive
            {"pnl": -50.0, "session_date": "2026-04-22", "infrastructure_contaminated": False},
        ]
        prior = BOCPDPrior.from_corpus(rows)
        assert prior.n_trades == 2
        assert prior.filtered_count == 1
        assert prior.mu_edge == pytest.approx(25.0, abs=0.001)  # (100 + -50) / 2
        # The contaminated $421.12 didn't poison the training mean

    def test_empty_corpus_returns_safe_default(self) -> None:
        prior = BOCPDPrior.from_corpus([])
        assert prior.n_trades == 0
        assert prior.mu_edge == 0.0
        assert prior.sigma_edge == 1.0  # safe default
        assert prior.hazard_rate == DEFAULT_HAZARD_RATE
        assert prior.corpus_dates == ()

    def test_custom_hazard_rate_overrides_default(self) -> None:
        rows = [{"pnl": 1.0, "session_date": "x", "infrastructure_contaminated": False}]
        prior = BOCPDPrior.from_corpus(rows, hazard_rate=1.0 / 100.0)
        assert prior.hazard_rate == pytest.approx(0.01)


# ── 2. Persistence round-trip ───────────────────────────────────


class TestPersistence:

    def test_round_trip_via_parquet(self, tmp_path: Path) -> None:
        original = BOCPDPrior(
            mu_edge=25.0, sigma_edge=76.38, hazard_rate=1.0 / 60.0,
            n_trades=4, filtered_count=1,
            corpus_dates=("2026-04-22", "2026-04-23"),
            generated_at="2026-04-25T17:00:00Z",
        )
        path = tmp_path / "bocpd_prior.parquet"
        original.to_parquet(path)
        assert path.exists()
        loaded = BOCPDPrior.from_parquet(path)
        assert loaded == original

    def test_schema_version_mismatch_rejected(self, tmp_path: Path) -> None:
        """Loader must refuse a prior file with wrong schema version."""
        import pandas as pd
        path = tmp_path / "bad_prior.parquet"
        pd.DataFrame([{
            "mu_edge": 0.0, "sigma_edge": 1.0, "hazard_rate": 0.01,
            "n_trades": 0, "filtered_count": 0,
            "corpus_dates": "", "generated_at": "x",
            "schema_version": 99,  # WRONG
        }]).to_parquet(path, index=False)
        with pytest.raises(ValueError, match="schema version mismatch"):
            BOCPDPrior.from_parquet(path)


# ── 3. Online detection ────────────────────────────────────────


class TestOnlineDetection:

    def test_stable_regime_keeps_changepoint_low(self) -> None:
        """50 obs from same Gaussian → posterior_cp stays low after settle."""
        random.seed(42)
        prior = BOCPDPrior(
            mu_edge=0.0, sigma_edge=1.0, hazard_rate=1.0 / 60.0,
            n_trades=0, filtered_count=0, corpus_dates=(),
            generated_at="x",
        )
        state = BOCPDState(prior)
        obs_list = state.observe_sequence([random.gauss(0.0, 1.0) for _ in range(50)])
        # After enough observations the posterior changepoint should be low
        # (the data MATCHES the prior). Look at the last 20 observations.
        late_cps = [o.posterior_changepoint for o in obs_list[-20:]]
        avg_late_cp = sum(late_cps) / len(late_cps)
        assert avg_late_cp < 0.3, f"Stable regime cp avg too high: {avg_late_cp:.3f}"

    def test_regime_break_spikes_changepoint(self) -> None:
        """50 obs ~ N(0,1) then 30 obs ~ N(5,1) → cp spike near transition."""
        random.seed(7)
        prior = BOCPDPrior(
            mu_edge=0.0, sigma_edge=1.0, hazard_rate=1.0 / 60.0,
            n_trades=0, filtered_count=0, corpus_dates=(),
            generated_at="x",
        )
        state = BOCPDState(prior)
        # First regime
        for _ in range(50):
            state.observe(random.gauss(0.0, 1.0))
        cp_pre = state.history[-1].posterior_changepoint
        # Second regime — 5σ shift
        post_break_cps: list[float] = []
        for _ in range(10):
            obs = state.observe(random.gauss(5.0, 1.0))
            post_break_cps.append(obs.posterior_changepoint)
        max_post_cp = max(post_break_cps)
        # The break MUST drive cp higher than pre-break baseline
        assert max_post_cp > cp_pre, (
            f"Regime break didn't elevate cp: pre={cp_pre:.3f} post_max={max_post_cp:.3f}"
        )
        # And cp should reach a meaningful level (>0.4 for 5σ shift)
        assert max_post_cp > 0.4, (
            f"5σ regime shift produced weak cp signal: {max_post_cp:.3f}"
        )

    def test_kill_switch_triggers_at_threshold(self) -> None:
        """Strong regime break crosses kill_switch_threshold → flag set."""
        prior = BOCPDPrior(
            mu_edge=0.0, sigma_edge=1.0, hazard_rate=1.0 / 60.0,
            n_trades=0, filtered_count=0, corpus_dates=(),
            generated_at="x",
        )
        state = BOCPDState(prior, kill_switch_threshold=0.5)
        # Build run-length history
        for _ in range(20):
            state.observe(0.0)
        # Massive shift — 20σ
        obs = state.observe(20.0)
        assert obs.kill_switch_triggered is True, (
            f"20σ shift didn't trigger kill switch (cp={obs.posterior_changepoint:.3f})"
        )

    def test_observation_count_increments(self) -> None:
        prior = BOCPDPrior(
            mu_edge=0.0, sigma_edge=1.0, hazard_rate=1.0 / 60.0,
            n_trades=0, filtered_count=0, corpus_dates=(),
            generated_at="x",
        )
        state = BOCPDState(prior)
        assert state.n_observations == 0
        state.observe(0.5)
        assert state.n_observations == 1
        state.observe_sequence([0.1, -0.2, 0.3])
        assert state.n_observations == 4
        assert len(state.history) == 4


# ── 4. Edge cases ──────────────────────────────────────────────


class TestEdgeCases:

    def test_single_observation_does_not_crash(self) -> None:
        """Single-obs corpus should NOT crash — sigma falls back to abs(mu) or 1.0."""
        prior = BOCPDPrior.from_corpus([
            {"pnl": 50.0, "session_date": "x", "infrastructure_contaminated": False},
        ])
        assert prior.n_trades == 1
        assert prior.mu_edge == 50.0
        # n=1 → sigma = max(|mu|, 1.0) = 50.0
        assert prior.sigma_edge == 50.0

    def test_from_prior_path_loads_correctly(self, tmp_path: Path) -> None:
        """Convenience: BOCPDState.from_prior_path(p) reads file + constructs."""
        original = BOCPDPrior(
            mu_edge=10.0, sigma_edge=2.0, hazard_rate=1.0 / 30.0,
            n_trades=10, filtered_count=2, corpus_dates=("2026-04-22",),
            generated_at="x",
        )
        p = tmp_path / "prior.parquet"
        original.to_parquet(p)
        state = BOCPDState.from_prior_path(p)
        assert state.prior == original
        assert state.kill_switch_threshold == DEFAULT_KILL_SWITCH_THRESHOLD
        assert state.n_observations == 0


# ── 5. Re-fit diff (D262 wire-in support) ──────────────────────────


class TestRefitDiff:
    """Cover the bocpd_refit_diff helper that drives D262 EOD reporting."""

    def test_no_existing_prior_recommends_refit(self, tmp_path: Path) -> None:
        """No prior file on disk → recommend_refit=True, reason=no_prior_file."""
        from src.analysis.bocpd import bocpd_refit_diff
        rows = [
            {"pnl": 10.0, "session_date": "2026-04-25", "infrastructure_contaminated": False},
        ]
        diff = bocpd_refit_diff(rows, prior_path=tmp_path / "missing.parquet")
        assert diff.old_prior is None
        assert diff.recommend_refit is True
        assert diff.reason == "no_prior_file"
        assert diff.delta_n_trades == 1

    def test_unchanged_corpus_no_refit_recommended(self, tmp_path: Path) -> None:
        """Same corpus → diffs at zero → recommend_refit=False."""
        from src.analysis.bocpd import bocpd_refit_diff
        rows = [
            {"pnl": 10.0, "session_date": "x", "infrastructure_contaminated": False},
            {"pnl": 20.0, "session_date": "x", "infrastructure_contaminated": False},
            {"pnl": 30.0, "session_date": "x", "infrastructure_contaminated": False},
        ]
        # Persist a prior fit on `rows`
        prior = BOCPDPrior.from_corpus(rows)
        prior_path = tmp_path / "prior.parquet"
        prior.to_parquet(prior_path)
        # Re-fit on the same rows → identical diff
        diff = bocpd_refit_diff(rows, prior_path=prior_path)
        assert diff.recommend_refit is False
        assert diff.delta_mu == pytest.approx(0.0, abs=1e-9)
        assert diff.delta_sigma == pytest.approx(0.0, abs=1e-9)
        assert diff.delta_n_trades == 0

    def test_n_trades_growth_above_threshold_triggers(self, tmp_path: Path) -> None:
        """Adding ≥10 new trades → recommend_refit=True."""
        from src.analysis.bocpd import bocpd_refit_diff
        old_rows = [
            {"pnl": 10.0, "session_date": "x", "infrastructure_contaminated": False},
        ]
        prior = BOCPDPrior.from_corpus(old_rows)
        prior_path = tmp_path / "prior.parquet"
        prior.to_parquet(prior_path)
        new_rows = old_rows + [
            {"pnl": 5.0 + i, "session_date": "y", "infrastructure_contaminated": False}
            for i in range(15)
        ]
        diff = bocpd_refit_diff(new_rows, prior_path=prior_path, new_trades_threshold=10)
        assert diff.recommend_refit is True
        assert diff.delta_n_trades == 15
        assert "n_trades grew by 15" in diff.reason

    def test_mu_drift_above_threshold_triggers(self, tmp_path: Path) -> None:
        """Mu drift > drift_mu_threshold → recommend_refit=True."""
        from src.analysis.bocpd import bocpd_refit_diff
        old_rows = [
            {"pnl": 0.0, "session_date": "x", "infrastructure_contaminated": False},
            {"pnl": 0.0, "session_date": "x", "infrastructure_contaminated": False},
        ]
        prior = BOCPDPrior.from_corpus(old_rows)  # mu=0, sigma=...
        prior_path = tmp_path / "prior.parquet"
        prior.to_parquet(prior_path)
        # Replace corpus with new mean far from 0
        new_rows = [
            {"pnl": 50.0, "session_date": "y", "infrastructure_contaminated": False},
            {"pnl": 50.0, "session_date": "y", "infrastructure_contaminated": False},
        ]
        # Pass an explicit small mu threshold so the drift is detected
        diff = bocpd_refit_diff(
            new_rows, prior_path=prior_path,
            new_trades_threshold=999,
            drift_mu_threshold=1.0,
        )
        assert diff.recommend_refit is True
        assert diff.delta_mu == pytest.approx(50.0, abs=0.01)
        assert "mu drift" in diff.reason


class TestEodRefitCheck:
    """Cover the EOD wrapper that drives the D262 surface."""

    def test_run_eod_bocpd_refit_check_logs_d262_when_recommended(
        self, tmp_path: Path, caplog,
    ) -> None:
        import json
        import logging
        from src.monitoring.eod_recon import run_eod_bocpd_refit_check

        # Create a corpus with new trades
        corpus_path = tmp_path / "trade_results.jsonl"
        with open(corpus_path, "w", encoding="utf-8") as f:
            for i in range(15):
                f.write(json.dumps({
                    "pnl": 10.0 + i,
                    "session_date": "2026-04-25",
                }) + "\n")
        # Create a stale prior with n_trades=0
        stale_prior = BOCPDPrior(
            mu_edge=0.0, sigma_edge=1.0, hazard_rate=1.0 / 60.0,
            n_trades=0, filtered_count=0, corpus_dates=(), generated_at="x",
        )
        prior_path = tmp_path / "prior.parquet"
        stale_prior.to_parquet(prior_path)

        with caplog.at_level(logging.WARNING, logger="src.monitoring.eod_recon"):
            result = run_eod_bocpd_refit_check(
                corpus_path=corpus_path, prior_path=prior_path,
                new_trades_threshold=10,
            )
        assert result["fires_d262"] is True
        assert result["error"] is None
        assert any("D262" in r.message for r in caplog.records)
        assert result["diff"]["delta_n_trades"] == 15

    def test_missing_corpus_returns_no_corpus_error_gracefully(
        self, tmp_path: Path,
    ) -> None:
        from src.monitoring.eod_recon import run_eod_bocpd_refit_check
        result = run_eod_bocpd_refit_check(
            corpus_path=tmp_path / "missing.jsonl",
            prior_path=tmp_path / "missing_prior.parquet",
        )
        assert result["fires_d262"] is False
        assert result["error"] == "no_corpus"
        assert result["diff"] is None
