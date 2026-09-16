"""Experiment Engine — A/B testing framework for LLM agent configurations.

Runs controlled comparisons between agent configurations using bootstrap
significance testing. Supports replay mode (free, instant) and live mode
(actual API calls, not yet implemented).

Usage::

    from src.llm_arena.experiment import ExperimentEngine, ExperimentConfig
    from src.llm_arena.experiments_library import create_replay_validation_experiment

    config = create_replay_validation_experiment()
    engine = ExperimentEngine(dataset, harness, scorer, data_dir)
    result = engine.run_experiment(config)
    engine.save_experiment(result)
    print(result.recommendation)
"""

from __future__ import annotations

import json
import os
import random
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .dataset import DatasetManager
from .harness import AgentConfig, AgentHarness, AgentRunResult
from .models import (
    CatalystType,
    CorrectSignal,
    LabelConfidence,
    LabeledScenario,
    StockOutcome,
)
from .scoring import AgentScorecard, MetricsCalculator


# ---------------------------------------------------------------------------
# Direction correctness helper (mirrors scoring.py private logic)
# ---------------------------------------------------------------------------

_BULL_DIRECTIONS = {"BULL", "STRONG_BULL"}
_BEAR_DIRECTIONS = {"BEAR", "STRONG_BEAR"}
_NEUTRAL_DIRECTIONS = {"NEUTRAL"}
_BULL_SIGNALS = {CorrectSignal.BULL, CorrectSignal.STRONG_BULL}
_BEAR_SIGNALS = {CorrectSignal.BEAR, CorrectSignal.STRONG_BEAR}
_NEUTRAL_SIGNALS = {CorrectSignal.NEUTRAL}


def _direction_correct(result: AgentRunResult, scenario: LabeledScenario) -> bool:
    direction = (result.signal_direction or "").upper()
    correct = scenario.correct_signal
    if direction in _BULL_DIRECTIONS and correct in _BULL_SIGNALS:
        return True
    if direction in _BEAR_DIRECTIONS and correct in _BEAR_SIGNALS:
        return True
    if direction in _NEUTRAL_DIRECTIONS and correct in _NEUTRAL_SIGNALS:
        return True
    return False


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class ExperimentConfig:
    """Defines an A/B experiment."""

    name: str
    description: str

    # What to test
    baseline: AgentConfig
    variants: list[AgentConfig] = field(default_factory=list)

    # What scenarios to use
    scenario_filters: dict = field(default_factory=dict)
    test_split_pct: float = 0.0

    # Statistical settings
    bootstrap_iterations: int = 1000
    confidence_level: float = 0.95
    min_scenarios: int = 20

    # Execution settings
    mode: str = "replay"
    max_concurrent: int = 5

    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "baseline": self.baseline.to_dict(),
            "variants": [v.to_dict() for v in self.variants],
            "scenario_filters": {
                k: (v.value if hasattr(v, "value") else v)
                for k, v in self.scenario_filters.items()
            },
            "test_split_pct": self.test_split_pct,
            "bootstrap_iterations": self.bootstrap_iterations,
            "confidence_level": self.confidence_level,
            "min_scenarios": self.min_scenarios,
            "mode": self.mode,
            "max_concurrent": self.max_concurrent,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ExperimentConfig":
        return cls(
            name=d["name"],
            description=d["description"],
            baseline=AgentConfig.from_dict(d["baseline"]),
            variants=[AgentConfig.from_dict(v) for v in d.get("variants", [])],
            scenario_filters=d.get("scenario_filters", {}),
            test_split_pct=d.get("test_split_pct", 0.0),
            bootstrap_iterations=d.get("bootstrap_iterations", 1000),
            confidence_level=d.get("confidence_level", 0.95),
            min_scenarios=d.get("min_scenarios", 20),
            mode=d.get("mode", "replay"),
            max_concurrent=d.get("max_concurrent", 5),
            created_at=(
                datetime.fromisoformat(d["created_at"])
                if d.get("created_at")
                else None
            ),
        )


@dataclass
class SignificanceResult:
    """Statistical significance of a variant vs baseline comparison."""

    variant_name: str
    metric: str
    baseline_mean: float
    variant_mean: float
    delta: float
    delta_pct: float
    p_value: float
    significant: bool
    confidence_interval: tuple  # (lo, hi) 95% CI of the delta
    effect_size: float  # Cohen's d

    def to_dict(self) -> dict:
        return {
            "variant_name": self.variant_name,
            "metric": self.metric,
            "baseline_mean": self.baseline_mean,
            "variant_mean": self.variant_mean,
            "delta": self.delta,
            "delta_pct": self.delta_pct,
            "p_value": self.p_value,
            "significant": self.significant,
            "confidence_interval": list(self.confidence_interval),
            "effect_size": self.effect_size,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SignificanceResult":
        return cls(
            variant_name=d["variant_name"],
            metric=d["metric"],
            baseline_mean=d["baseline_mean"],
            variant_mean=d["variant_mean"],
            delta=d["delta"],
            delta_pct=d["delta_pct"],
            p_value=d["p_value"],
            significant=d["significant"],
            confidence_interval=tuple(d["confidence_interval"]),
            effect_size=d["effect_size"],
        )


@dataclass
class ExperimentResult:
    """Results from running an experiment."""

    config: ExperimentConfig
    baseline_scorecard: AgentScorecard
    variant_scorecards: list[AgentScorecard]
    comparisons: list[dict]
    significance: list[SignificanceResult]
    winner: str
    recommendation: str
    scenario_count: int
    run_duration_seconds: float
    completed_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "config": self.config.to_dict(),
            "baseline_scorecard": self.baseline_scorecard.to_dict(),
            "variant_scorecards": [s.to_dict() for s in self.variant_scorecards],
            "comparisons": self.comparisons,
            "significance": [s.to_dict() for s in self.significance],
            "winner": self.winner,
            "recommendation": self.recommendation,
            "scenario_count": self.scenario_count,
            "run_duration_seconds": self.run_duration_seconds,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ExperimentResult":
        return cls(
            config=ExperimentConfig.from_dict(d["config"]),
            baseline_scorecard=AgentScorecard.from_dict(d["baseline_scorecard"]),
            variant_scorecards=[
                AgentScorecard.from_dict(s) for s in d.get("variant_scorecards", [])
            ],
            comparisons=d.get("comparisons", []),
            significance=[
                SignificanceResult.from_dict(s) for s in d.get("significance", [])
            ],
            winner=d["winner"],
            recommendation=d["recommendation"],
            scenario_count=d["scenario_count"],
            run_duration_seconds=d["run_duration_seconds"],
            completed_at=(
                datetime.fromisoformat(d["completed_at"])
                if d.get("completed_at")
                else None
            ),
        )


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class ExperimentEngine:
    """Runs A/B experiments comparing agent configurations.

    Args:
        dataset:  DatasetManager loaded with scenarios.
        harness:  AgentHarness for running agents.
        scorer:   MetricsCalculator for scoring results.
        data_dir: Directory for saving/loading experiment JSON files.
                  (e.g. data/llm_arena/experiments/)
    """

    def __init__(
        self,
        dataset: DatasetManager,
        harness: AgentHarness,
        scorer: MetricsCalculator,
        data_dir: str,
    ):
        self._dataset = dataset
        self._harness = harness
        self._scorer = scorer
        self._data_dir = data_dir

    # ------------------------------------------------------------------
    # Public: run
    # ------------------------------------------------------------------

    def run_experiment(self, config: ExperimentConfig) -> ExperimentResult:
        """Run a complete A/B experiment.

        Steps:
          1. Load and filter scenarios (with optional train/test split).
          2. Run baseline against all scenarios.
          3. Run each variant against the same scenarios.
          4. Score all results into AgentScorecards.
          5. Compute scorecard comparisons (deltas).
          6. Bootstrap statistical significance for each variant.
          7. Generate winner + human-readable recommendation.
        """
        start = time.time()

        scenarios = self._select_scenarios(config)
        scenario_map = {s.scenario_id: s for s in scenarios}

        # Run baseline
        baseline_results, baseline_scorecard = self._run_config(
            scenarios, config.baseline, config.mode
        )

        # Run variants
        variant_results_list: list[list[AgentRunResult]] = []
        variant_scorecards: list[AgentScorecard] = []
        for variant_cfg in config.variants:
            vresults, vscorecard = self._run_config(scenarios, variant_cfg, config.mode)
            variant_results_list.append(vresults)
            variant_scorecards.append(vscorecard)

        # Comparisons (delta dicts)
        comparisons = [
            self._scorer.compare_scorecards(baseline_scorecard, vc)
            for vc in variant_scorecards
        ]

        # Statistical significance
        significance = [
            self._compute_significance(
                baseline_results,
                vresults,
                scenario_map,
                bootstrap_iterations=config.bootstrap_iterations,
                confidence_level=config.confidence_level,
            )
            for vresults in variant_results_list
        ]

        winner, recommendation = self._generate_recommendation(
            config, baseline_scorecard, variant_scorecards, significance
        )

        return ExperimentResult(
            config=config,
            baseline_scorecard=baseline_scorecard,
            variant_scorecards=variant_scorecards,
            comparisons=comparisons,
            significance=significance,
            winner=winner,
            recommendation=recommendation,
            scenario_count=len(scenarios),
            run_duration_seconds=time.time() - start,
            completed_at=datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------
    # Scenario selection
    # ------------------------------------------------------------------

    def _select_scenarios(self, config: ExperimentConfig) -> list[LabeledScenario]:
        """Filter dataset and optionally hold out a test split."""
        filters = self._parse_filters(config.scenario_filters)
        # _limit is a CLI-only flag — remove before passing to DatasetManager
        limit = filters.pop("_limit", None)
        scenarios = self._dataset.filter(**filters)

        # Apply --limit cap (for cost-controlled live tests)
        if limit is not None:
            scenarios = scenarios[:int(limit)]

        if len(scenarios) < config.min_scenarios:
            raise ValueError(
                f"Insufficient scenarios after filtering: "
                f"{len(scenarios)} < min_scenarios={config.min_scenarios}. "
                f"Relax scenario_filters or lower min_scenarios."
            )

        if config.test_split_pct > 0.0:
            rng = random.Random(42)
            shuffled = list(scenarios)
            rng.shuffle(shuffled)
            n_test = max(1, int(len(shuffled) * config.test_split_pct))
            # Return training set (hold out test set for independent validation)
            scenarios = shuffled[n_test:]

        return scenarios

    def _parse_filters(self, raw: dict) -> dict:
        """Convert a raw filter dict (possibly with string values) to enum kwargs."""
        filters: dict = {}
        for key, val in raw.items():
            if key == "outcome":
                filters["outcome"] = (
                    StockOutcome(val) if isinstance(val, str) else val
                )
            elif key == "catalyst_type":
                filters["catalyst_type"] = (
                    CatalystType(val) if isinstance(val, str) else val
                )
            elif key == "label_confidence":
                filters["label_confidence"] = (
                    LabelConfidence(val) if isinstance(val, str) else val
                )
            else:
                filters[key] = val
        return filters

    # ------------------------------------------------------------------
    # Run one config
    # ------------------------------------------------------------------

    def _run_config(
        self,
        scenarios: list[LabeledScenario],
        agent_config: AgentConfig,
        mode: str,
    ) -> tuple[list[AgentRunResult], AgentScorecard]:
        """Run one AgentConfig against all scenarios, return (results, scorecard)."""
        harness = AgentHarness(self._data_dir)
        results = harness.run_batch(scenarios, agent_config, mode=mode)
        scorecard = self._scorer.score_results(results, scenarios)
        return results, scorecard

    # ------------------------------------------------------------------
    # Bootstrap significance
    # ------------------------------------------------------------------

    def _compute_significance(
        self,
        baseline_results: list[AgentRunResult],
        variant_results: list[AgentRunResult],
        scenarios: dict[str, LabeledScenario],
        metric: str = "direction_accuracy",
        bootstrap_iterations: int = 1000,
        confidence_level: float = 0.95,
        seed: int = 42,
    ) -> SignificanceResult:
        """Bootstrap significance test on direction_accuracy (or another metric).

        Algorithm:
          1. Identify common scenario IDs with results from both configs.
          2. Compute observed delta (variant_mean - baseline_mean).
          3. For each bootstrap iteration:
               - Resample common scenario IDs with replacement.
               - Compute metric for baseline on resampled set.
               - Compute metric for variant on resampled set.
               - Record the delta.
          4. p_value = fraction of bootstrap deltas <= 0.
             (Testing H1: variant > baseline, one-sided.)
          5. Confidence interval = [alpha/2, 1-alpha/2] percentiles of deltas.
          6. Effect size = Cohen's d.
        """
        baseline_map = {r.scenario_id: r for r in baseline_results}
        variant_map = {r.scenario_id: r for r in variant_results}

        # Determine the variant name from results or fallback
        variant_name = "variant"
        if variant_results:
            variant_name = variant_results[0].agent_config.agent_type

        # Common scenario IDs with a result from both
        common_ids = [
            sid
            for sid in scenarios
            if sid in baseline_map and sid in variant_map
        ]

        if len(common_ids) < 2:
            return SignificanceResult(
                variant_name=variant_name,
                metric=metric,
                baseline_mean=0.0,
                variant_mean=0.0,
                delta=0.0,
                delta_pct=0.0,
                p_value=1.0,
                significant=False,
                confidence_interval=(0.0, 0.0),
                effect_size=0.0,
            )

        # Observed accuracy for each scenario (binary for direction_accuracy)
        def _per_scenario_correct(results_map: dict, sids: list) -> list[float]:
            values = []
            for sid in sids:
                r = results_map.get(sid)
                s = scenarios.get(sid)
                if r is not None and s is not None:
                    values.append(1.0 if _direction_correct(r, s) else 0.0)
                else:
                    values.append(0.0)
            return values

        baseline_vals = _per_scenario_correct(baseline_map, common_ids)
        variant_vals = _per_scenario_correct(variant_map, common_ids)

        baseline_mean = sum(baseline_vals) / len(baseline_vals)
        variant_mean = sum(variant_vals) / len(variant_vals)
        delta = variant_mean - baseline_mean
        delta_pct = (delta / baseline_mean) if baseline_mean > 0 else 0.0

        # Bootstrap
        rng = random.Random(seed)
        n = len(common_ids)
        boot_deltas: list[float] = []

        for _ in range(bootstrap_iterations):
            indices = [rng.randint(0, n - 1) for _ in range(n)]
            b_mean = sum(baseline_vals[i] for i in indices) / n
            v_mean = sum(variant_vals[i] for i in indices) / n
            boot_deltas.append(v_mean - b_mean)

        # p-value: one-sided (how often does the bootstrap delta go against us?)
        p_value = sum(1 for d in boot_deltas if d <= 0) / len(boot_deltas)
        alpha = 1.0 - confidence_level
        significant = p_value < alpha

        # Confidence interval of the delta
        sorted_deltas = sorted(boot_deltas)
        lo_idx = max(0, int(len(sorted_deltas) * (alpha / 2)))
        hi_idx = min(len(sorted_deltas) - 1, int(len(sorted_deltas) * (1.0 - alpha / 2)))
        ci = (sorted_deltas[lo_idx], sorted_deltas[hi_idx])

        # Effect size: Cohen's d
        b_std = statistics.stdev(baseline_vals) if len(baseline_vals) > 1 else 0.0
        v_std = statistics.stdev(variant_vals) if len(variant_vals) > 1 else 0.0
        pooled_std = ((b_std ** 2 + v_std ** 2) / 2) ** 0.5
        effect_size = delta / pooled_std if pooled_std > 0 else 0.0

        return SignificanceResult(
            variant_name=variant_name,
            metric=metric,
            baseline_mean=round(baseline_mean, 6),
            variant_mean=round(variant_mean, 6),
            delta=round(delta, 6),
            delta_pct=round(delta_pct, 6),
            p_value=round(p_value, 6),
            significant=significant,
            confidence_interval=(round(ci[0], 6), round(ci[1], 6)),
            effect_size=round(effect_size, 6),
        )

    # ------------------------------------------------------------------
    # Recommendation
    # ------------------------------------------------------------------

    def _generate_recommendation(
        self,
        config: ExperimentConfig,
        baseline_scorecard: AgentScorecard,
        variant_scorecards: list[AgentScorecard],
        significance: list[SignificanceResult],
    ) -> tuple[str, str]:
        """Determine winner and write a human-readable recommendation.

        Priority: direction_accuracy (primary) > false_positive_rate > catalyst_accuracy > latency.
        A variant wins if:
          1. Significantly better on direction_accuracy (p < alpha), AND
          2. Not significantly worse on any critical metric (checked via FP rate).
        If multiple variants win, the one with the largest delta wins.
        If no variant wins, baseline is recommended (conservative default).
        """
        alpha = 1.0 - config.confidence_level
        baseline_name = config.baseline.agent_type

        # Identify winning variants
        winning: list[tuple[int, SignificanceResult]] = []
        for i, sig in enumerate(significance):
            if sig.significant and sig.delta > 0:
                # Also check FP rate didn't get significantly worse
                vc = variant_scorecards[i]
                bc = baseline_scorecard
                fp_worse = (
                    vc.classification.false_positives
                    > bc.classification.false_positives * 1.5
                )
                if not fp_worse:
                    winning.append((i, sig))

        if not winning:
            return "baseline", (
                f"No variant achieved statistical significance at "
                f"{config.confidence_level:.0%} confidence level "
                f"(alpha={alpha:.2f}). "
                f"Baseline '{baseline_name}' recommended (conservative default)."
            )

        # Pick best by delta on primary metric
        best_i, best_sig = max(winning, key=lambda x: x[1].delta)
        variant_cfg = config.variants[best_i]
        variant_name = variant_cfg.agent_type

        parts = [
            f"Variant '{variant_name}' wins over baseline '{baseline_name}':",
            f"  direction_accuracy: {best_sig.baseline_mean:.1%} -> {best_sig.variant_mean:.1%} "
            f"(+{best_sig.delta:.1%}, p={best_sig.p_value:.3f})",
            f"  95% CI of delta: [{best_sig.confidence_interval[0]:+.1%}, "
            f"{best_sig.confidence_interval[1]:+.1%}]",
            f"  Effect size (Cohen's d): {best_sig.effect_size:.3f}",
        ]

        # Note any other winning variants
        other_winners = [
            config.variants[i].agent_type for i, _ in winning if i != best_i
        ]
        if other_winners:
            parts.append(
                f"  Also significant: {', '.join(other_winners)}"
            )

        return variant_name, "\n".join(parts)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_experiment(self, result: ExperimentResult) -> str:
        """Save experiment result to data_dir/{name}.json. Returns path."""
        os.makedirs(self._data_dir, exist_ok=True)
        path = os.path.join(self._data_dir, f"{result.config.name}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(result.to_dict(), fh, indent=2, default=str)
        return path

    def load_experiment(self, name: str) -> ExperimentResult:
        """Load a saved experiment from data_dir/{name}.json."""
        path = os.path.join(self._data_dir, f"{name}.json")
        if not os.path.exists(path):
            raise FileNotFoundError(f"No experiment found: {path}")
        with open(path, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return ExperimentResult.from_dict(d)

    def list_experiments(self) -> list[dict]:
        """List all saved experiments with summary stats."""
        if not os.path.isdir(self._data_dir):
            return []
        results = []
        for fname in sorted(os.listdir(self._data_dir)):
            if not fname.endswith(".json"):
                continue
            path = os.path.join(self._data_dir, fname)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    d = json.load(fh)
                cfg = d.get("config", {})
                sig_list = d.get("significance", [])
                first_sig = sig_list[0] if sig_list else {}
                results.append(
                    {
                        "name": cfg.get("name", fname[:-5]),
                        "description": cfg.get("description", ""),
                        "winner": d.get("winner", "?"),
                        "scenario_count": d.get("scenario_count", 0),
                        "run_duration_seconds": d.get("run_duration_seconds", 0),
                        "completed_at": d.get("completed_at"),
                        "recommendation": d.get("recommendation", ""),
                        "primary_delta": first_sig.get("delta"),
                        "primary_p_value": first_sig.get("p_value"),
                        "significant": first_sig.get("significant"),
                    }
                )
            except Exception:
                pass
        return results


# ---------------------------------------------------------------------------
# Formatting helpers (used by CLI)
# ---------------------------------------------------------------------------


def format_experiment_result(result: ExperimentResult) -> str:
    """Return a formatted multi-line summary of an experiment result."""
    width = 60
    sep = "=" * width
    config = result.config

    lines = [
        sep,
        f"  EXPERIMENT: {config.name}",
        f"  {config.description}",
        sep,
        "",
        f"  Mode              : {config.mode}",
        f"  Scenarios         : {result.scenario_count}",
        f"  Baseline          : {config.baseline.agent_type} / {config.baseline.model_id}",
        f"  Variants          : {len(config.variants)}",
        f"  Confidence level  : {config.confidence_level:.0%}",
        f"  Bootstrap iters   : {config.bootstrap_iterations}",
        f"  Duration          : {result.run_duration_seconds:.1f}s",
        f"  Completed         : {result.completed_at}",
        "",
        f"  WINNER: {result.winner.upper()}",
        "",
        "RECOMMENDATION",
    ]
    for line in result.recommendation.split("\n"):
        lines.append(f"  {line}")

    for i, sig in enumerate(result.significance):
        variant_name = (
            config.variants[i].agent_type if i < len(config.variants) else f"variant_{i}"
        )
        lines += [
            "",
            f"SIGNIFICANCE: baseline vs {variant_name}",
            f"  Metric          : {sig.metric}",
            f"  Baseline mean   : {sig.baseline_mean:.3%}",
            f"  Variant mean    : {sig.variant_mean:.3%}",
            f"  Delta           : {sig.delta:+.3%}  ({sig.delta_pct:+.1%} relative)",
            f"  p-value         : {sig.p_value:.4f}",
            f"  Significant     : {'YES' if sig.significant else 'NO'}",
            f"  95% CI          : [{sig.confidence_interval[0]:+.3%}, {sig.confidence_interval[1]:+.3%}]",
            f"  Effect size (d) : {sig.effect_size:.3f}",
        ]

    lines.append(sep)
    return "\n".join(lines)
