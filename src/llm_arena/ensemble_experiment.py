"""Ensemble experiment: multi-model majority voting vs single-model baselines.

Runs 3 different LLMs in parallel on the same stock scenarios, takes the
majority vote, and measures whether the ensemble outperforms any single model.

Usage::

    from src.llm_arena.ensemble_experiment import EnsembleExperiment

    experiment = EnsembleExperiment(data_dir="data/llm_arena")
    result = experiment.run(
        scenarios=scenarios[:50],
        models=["qwen3-235b", "llama-3.3-70b", "mixtral-8x7b"],
        name="ensemble_v1",
    )
    path = experiment.save(result)
    print(format_ensemble_result(result))
"""

from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .ensemble import EnsembleConfig, EnsembleResult, EnsembleVoter, _normalize_signal
from .harness import AgentConfig, AgentRunResult
from .models import CorrectSignal, LabeledScenario
from .scoring import AgentScorecard, MetricsCalculator


# ---------------------------------------------------------------------------
# Direction helpers (mirrors scoring.py private logic)
# ---------------------------------------------------------------------------

_BULL_DIRECTIONS = {"BULL", "STRONG_BULL"}
_BEAR_DIRECTIONS = {"BEAR", "STRONG_BEAR"}
_NEUTRAL_DIRECTIONS = {"NEUTRAL"}
_BULL_SIGNALS = {CorrectSignal.BULL, CorrectSignal.STRONG_BULL}
_BEAR_SIGNALS = {CorrectSignal.BEAR, CorrectSignal.STRONG_BEAR}
_NEUTRAL_SIGNALS = {CorrectSignal.NEUTRAL}


def _dir_correct_ensemble(result: EnsembleResult, scenario: LabeledScenario) -> bool:
    direction = (result.voted_signal or "").upper()
    correct = scenario.correct_signal
    if direction in _BULL_DIRECTIONS and correct in _BULL_SIGNALS:
        return True
    if direction in _BEAR_DIRECTIONS and correct in _BEAR_SIGNALS:
        return True
    if direction in _NEUTRAL_DIRECTIONS and correct in _NEUTRAL_SIGNALS:
        return True
    return False


def _dir_correct_run(result: AgentRunResult, scenario: LabeledScenario) -> bool:
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
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class EnsembleExperimentResult:
    """Full output of an ensemble experiment run."""

    name: str
    models: list[str]
    scenario_count: int

    # Per-model scorecards (from individual signals)
    individual_scorecards: dict[str, AgentScorecard]  # model_id -> scorecard

    # Ensemble scorecard (majority vote)
    ensemble_scorecard: AgentScorecard

    # Comparison: ensemble vs each individual model
    comparisons: dict[str, dict]  # model_id -> compare_scorecards dict

    # Ensemble-specific metrics
    mean_agreement_ratio: float
    unanimous_agreement_pct: float  # Fraction where all N models agreed

    # Raw ensemble results (for downstream analysis)
    ensemble_results: list[EnsembleResult]

    # Statistical significance (ensemble vs best individual model)
    best_individual_model: str
    ensemble_vs_best_delta: float
    ensemble_vs_best_p_value: float
    ensemble_vs_best_significant: bool

    # Cost and timing
    run_duration_seconds: float
    total_cost_usd: float
    completed_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "models": self.models,
            "scenario_count": self.scenario_count,
            "individual_scorecards": {
                k: v.to_dict() for k, v in self.individual_scorecards.items()
            },
            "ensemble_scorecard": self.ensemble_scorecard.to_dict(),
            "comparisons": self.comparisons,
            "mean_agreement_ratio": self.mean_agreement_ratio,
            "unanimous_agreement_pct": self.unanimous_agreement_pct,
            "ensemble_results": [r.to_dict() for r in self.ensemble_results],
            "best_individual_model": self.best_individual_model,
            "ensemble_vs_best_delta": self.ensemble_vs_best_delta,
            "ensemble_vs_best_p_value": self.ensemble_vs_best_p_value,
            "ensemble_vs_best_significant": self.ensemble_vs_best_significant,
            "run_duration_seconds": self.run_duration_seconds,
            "total_cost_usd": self.total_cost_usd,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EnsembleExperimentResult":
        return cls(
            name=d["name"],
            models=d["models"],
            scenario_count=d["scenario_count"],
            individual_scorecards={
                k: AgentScorecard.from_dict(v)
                for k, v in d.get("individual_scorecards", {}).items()
            },
            ensemble_scorecard=AgentScorecard.from_dict(d["ensemble_scorecard"]),
            comparisons=d.get("comparisons", {}),
            mean_agreement_ratio=d["mean_agreement_ratio"],
            unanimous_agreement_pct=d["unanimous_agreement_pct"],
            ensemble_results=[
                EnsembleResult.from_dict(r) for r in d.get("ensemble_results", [])
            ],
            best_individual_model=d["best_individual_model"],
            ensemble_vs_best_delta=d["ensemble_vs_best_delta"],
            ensemble_vs_best_p_value=d["ensemble_vs_best_p_value"],
            ensemble_vs_best_significant=d["ensemble_vs_best_significant"],
            run_duration_seconds=d["run_duration_seconds"],
            total_cost_usd=d["total_cost_usd"],
            completed_at=(
                datetime.fromisoformat(d["completed_at"])
                if d.get("completed_at")
                else None
            ),
        )


# ---------------------------------------------------------------------------
# Experiment engine
# ---------------------------------------------------------------------------


class EnsembleExperiment:
    """Runs the full ensemble experiment.

    1. Call all N models in parallel for each scenario (via EnsembleVoter).
    2. Score each model individually using its own signals.
    3. Score the ensemble using the majority-voted signal.
    4. Compare ensemble vs each individual model.
    5. Bootstrap significance test: ensemble vs best individual model.
    """

    def __init__(self, data_dir: str):
        self._data_dir = data_dir
        self._scorer = MetricsCalculator()

    # ------------------------------------------------------------------
    # Public: run
    # ------------------------------------------------------------------

    def run(
        self,
        scenarios: list[LabeledScenario],
        models: list[str],
        name: str = "ensemble_v1",
        agent_type: str = "news",
        rate_limit_delay: float = 0.3,
        progress_callback=None,
    ) -> EnsembleExperimentResult:
        """Run the full ensemble experiment.

        Args:
            scenarios:         Labeled scenarios to evaluate.
            models:            List of model IDs from MODEL_REGISTRY.
            name:              Experiment name (used for saving).
            agent_type:        Agent type to evaluate ("news", etc.).
            rate_limit_delay:  Seconds between scenario calls (rate limit buffer).
            progress_callback: Optional callable(done, total).

        Returns:
            EnsembleExperimentResult with full per-model and ensemble metrics.
        """
        start = time.time()
        scenario_map = {s.scenario_id: s for s in scenarios}

        print(
            f"[ensemble] {name}: {len(models)} models × {len(scenarios)} scenarios"
        )
        print(f"[ensemble] Models: {', '.join(models)}")

        # Step 1: Run ensemble (fires all models in parallel per scenario)
        ensemble_config = EnsembleConfig(
            models=models,
            agent_type=agent_type,
        )
        voter = EnsembleVoter(ensemble_config, self._data_dir)
        ensemble_results = voter.evaluate_batch(
            scenarios,
            rate_limit_delay=rate_limit_delay,
            progress_callback=progress_callback,
        )

        # Step 2: Extract per-model AgentRunResults from ensemble responses
        individual_results: dict[str, list[AgentRunResult]] = {m: [] for m in models}
        for ens_result, scenario in zip(ensemble_results, scenarios):
            for resp in ens_result.responses:
                cfg = AgentConfig(
                    agent_type=agent_type, model_id=resp.model_id
                )
                arr = AgentRunResult(
                    scenario_id=scenario.scenario_id,
                    agent_config=cfg,
                    signal_direction=resp.signal,
                    signal_confidence=resp.confidence,
                    reasoning=resp.reasoning,
                    catalyst_type=resp.catalyst_type,
                    latency_ms=resp.latency_ms,
                    parse_success=resp.success,
                    error=resp.error if resp.error else None,
                )
                individual_results[resp.model_id].append(arr)

        # Step 3: Score each individual model
        individual_scorecards: dict[str, AgentScorecard] = {}
        for model_id, results in individual_results.items():
            if results:
                individual_scorecards[model_id] = self._scorer.score_results(
                    results, scenarios
                )

        # Step 4: Score the ensemble (using voted_signal)
        ensemble_scorecard = self._score_ensemble(ensemble_results, scenarios)

        # Step 5: Find best individual model by direction_accuracy
        if individual_scorecards:
            best_model = max(
                individual_scorecards.keys(),
                key=lambda m: individual_scorecards[m].classification.direction_accuracy,
            )
        else:
            best_model = models[0] if models else "unknown"

        # Step 6: Comparisons (ensemble vs each model)
        comparisons: dict[str, dict] = {}
        for model_id, scorecard in individual_scorecards.items():
            comparisons[model_id] = self._scorer.compare_scorecards(
                scorecard, ensemble_scorecard
            )

        # Step 7: Bootstrap significance — ensemble vs best single model
        best_individual_results = individual_results.get(best_model, [])
        delta, p_value, significant = self._bootstrap_significance(
            best_individual_results, ensemble_results, scenario_map
        )

        # Step 8: Ensemble-specific aggregate metrics
        responded = [r for r in ensemble_results if r.models_responded >= 2]
        mean_agreement = (
            sum(r.agreement_ratio for r in responded) / len(responded)
            if responded
            else 0.0
        )
        unanimous_count = sum(1 for r in responded if r.agreement_ratio >= 0.999)
        unanimous_pct = unanimous_count / len(responded) if responded else 0.0

        total_cost = sum(r.total_cost for r in ensemble_results)

        return EnsembleExperimentResult(
            name=name,
            models=models,
            scenario_count=len(scenarios),
            individual_scorecards=individual_scorecards,
            ensemble_scorecard=ensemble_scorecard,
            comparisons=comparisons,
            mean_agreement_ratio=round(mean_agreement, 4),
            unanimous_agreement_pct=round(unanimous_pct, 4),
            ensemble_results=ensemble_results,
            best_individual_model=best_model,
            ensemble_vs_best_delta=round(delta, 6),
            ensemble_vs_best_p_value=round(p_value, 6),
            ensemble_vs_best_significant=significant,
            run_duration_seconds=round(time.time() - start, 1),
            total_cost_usd=round(total_cost, 6),
            completed_at=datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------
    # Internal: score ensemble as AgentScorecard
    # ------------------------------------------------------------------

    def _score_ensemble(
        self,
        ensemble_results: list[EnsembleResult],
        scenarios: list[LabeledScenario],
    ) -> AgentScorecard:
        """Convert EnsembleResults to AgentRunResults using voted_signal, then score."""
        run_results: list[AgentRunResult] = []
        for er in ensemble_results:
            cfg = AgentConfig(agent_type="ensemble", model_id="ensemble")
            arr = AgentRunResult(
                scenario_id=er.scenario_id,
                agent_config=cfg,
                signal_direction=er.voted_signal,
                signal_confidence=er.voted_confidence,
                catalyst_type=er.voted_catalyst_type,
                latency_ms=er.total_latency_ms,
                parse_success=er.models_responded >= 1,
                cost_usd=er.total_cost,
            )
            run_results.append(arr)

        return self._scorer.score_results(run_results, scenarios)

    # ------------------------------------------------------------------
    # Internal: bootstrap significance
    # ------------------------------------------------------------------

    def _bootstrap_significance(
        self,
        baseline_results: list[AgentRunResult],
        ensemble_results: list[EnsembleResult],
        scenario_map: dict[str, LabeledScenario],
        n_iter: int = 1000,
        seed: int = 42,
    ) -> tuple[float, float, bool]:
        """Bootstrap significance: ensemble direction_accuracy vs best single model.

        Returns:
            (delta, p_value, significant)
            delta = ensemble_mean - baseline_mean
            p_value: one-sided (H1: ensemble > baseline)
            significant: p_value < 0.05
        """
        baseline_map = {r.scenario_id: r for r in baseline_results}
        ensemble_map = {r.scenario_id: r for r in ensemble_results}

        common_ids = [
            sid
            for sid in scenario_map
            if sid in baseline_map and sid in ensemble_map
        ]

        if len(common_ids) < 2:
            return 0.0, 1.0, False

        def _base_correct(sid: str) -> float:
            r = baseline_map.get(sid)
            s = scenario_map.get(sid)
            return (
                1.0
                if r is not None and s is not None and _dir_correct_run(r, s)
                else 0.0
            )

        def _ens_correct(sid: str) -> float:
            r = ensemble_map.get(sid)
            s = scenario_map.get(sid)
            return (
                1.0
                if r is not None and s is not None and _dir_correct_ensemble(r, s)
                else 0.0
            )

        base_vals = [_base_correct(sid) for sid in common_ids]
        ens_vals = [_ens_correct(sid) for sid in common_ids]

        base_mean = sum(base_vals) / len(base_vals)
        ens_mean = sum(ens_vals) / len(ens_vals)
        delta = ens_mean - base_mean

        rng = random.Random(seed)
        n = len(common_ids)
        boot_deltas: list[float] = []
        for _ in range(n_iter):
            indices = [rng.randint(0, n - 1) for _ in range(n)]
            b = sum(base_vals[i] for i in indices) / n
            e = sum(ens_vals[i] for i in indices) / n
            boot_deltas.append(e - b)

        # One-sided p-value: how often does bootstrap delta go against the hypothesis?
        p_value = sum(1 for d in boot_deltas if d <= 0) / len(boot_deltas)
        significant = p_value < 0.05

        return delta, p_value, significant

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(
        self, result: EnsembleExperimentResult, name: Optional[str] = None
    ) -> str:
        """Save to data/llm_arena/ensemble/{name}.json. Returns path."""
        save_name = name or result.name
        ensemble_dir = os.path.join(self._data_dir, "ensemble")
        os.makedirs(ensemble_dir, exist_ok=True)
        path = os.path.join(ensemble_dir, f"{save_name}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(result.to_dict(), fh, indent=2, default=str)
        return path

    def load(self, name: str) -> EnsembleExperimentResult:
        """Load a saved ensemble experiment from data/llm_arena/ensemble/{name}.json."""
        ensemble_dir = os.path.join(self._data_dir, "ensemble")
        path = os.path.join(ensemble_dir, f"{name}.json")
        if not os.path.exists(path):
            raise FileNotFoundError(f"No ensemble result found: {path}")
        with open(path, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return EnsembleExperimentResult.from_dict(d)

    def list_saved(self) -> list[str]:
        """List saved ensemble experiment names."""
        ensemble_dir = os.path.join(self._data_dir, "ensemble")
        if not os.path.isdir(ensemble_dir):
            return []
        return [
            f[:-5]
            for f in sorted(os.listdir(ensemble_dir))
            if f.endswith(".json")
        ]


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def format_ensemble_result(result: EnsembleExperimentResult) -> str:
    """Return a formatted multi-line summary of an ensemble experiment result."""
    width = 64
    sep = "=" * width
    thin = "-" * width

    lines = [
        sep,
        f"  ENSEMBLE EXPERIMENT: {result.name}",
        f"  Models: {', '.join(result.models)}",
        sep,
        "",
        f"  Scenarios      : {result.scenario_count}",
        f"  Duration       : {result.run_duration_seconds:.1f}s",
        f"  Total Cost     : ${result.total_cost_usd:.4f}",
        f"  Completed      : {result.completed_at}",
        "",
        "ENSEMBLE VOTING STATS",
        thin,
        f"  Mean Agreement Ratio    : {result.mean_agreement_ratio:.1%}",
        f"  Unanimous Agreement     : {result.unanimous_agreement_pct:.1%}",
        "",
    ]

    # --- Individual model scorecards ---
    lines += ["INDIVIDUAL MODEL PERFORMANCE", thin]
    lines.append(
        f"  {'Model':<28} {'Dir Acc':>8} {'Prec':>7} {'Recall':>7} "
        f"{'F1':>7} {'FP':>5} {'FN':>5} {'$/sig':>8}"
    )
    lines.append(f"  {'-'*28} {'-'*8} {'-'*7} {'-'*7} {'-'*7} {'-'*5} {'-'*5} {'-'*8}")

    for model_id in result.models:
        sc = result.individual_scorecards.get(model_id)
        if sc is None:
            lines.append(f"  {model_id:<28} (no data)")
            continue
        c = sc.classification
        f = sc.financial
        marker = " <-- best" if model_id == result.best_individual_model else ""
        lines.append(
            f"  {model_id:<28} {c.direction_accuracy:>7.1%} {c.precision:>7.3f} "
            f"{c.recall:>7.3f} {c.f1_score:>7.3f} {c.false_positives:>5} "
            f"{c.false_negatives:>5} ${f.cost_per_signal:>7.4f}{marker}"
        )

    # --- Ensemble scorecard ---
    esc = result.ensemble_scorecard
    ec = esc.classification
    ef = esc.financial
    lines += [
        "",
        "ENSEMBLE (MAJORITY VOTE)",
        thin,
        f"  {'[ensemble]':<28} {ec.direction_accuracy:>7.1%} {ec.precision:>7.3f} "
        f"{ec.recall:>7.3f} {ec.f1_score:>7.3f} {ec.false_positives:>5} "
        f"{ec.false_negatives:>5} ${ef.cost_per_signal:>7.4f}",
        "",
    ]

    # --- Statistical significance ---
    lines += [
        "STATISTICAL SIGNIFICANCE (ensemble vs best individual)",
        thin,
        f"  Best individual model  : {result.best_individual_model}",
        f"  Ensemble delta         : {result.ensemble_vs_best_delta:+.1%}",
        f"  p-value (bootstrap)    : {result.ensemble_vs_best_p_value:.4f}",
        f"  Significant (p<0.05)   : {'YES' if result.ensemble_vs_best_significant else 'NO'}",
        "",
    ]

    # --- Per-model comparison vs ensemble ---
    lines += ["COMPARISON: each model vs ensemble", thin]
    for model_id, cmp in result.comparisons.items():
        delta = cmp.get("direction_accuracy_delta", 0.0)
        arrow = "^" if delta > 0 else ("v" if delta < 0 else "--")
        lines.append(
            f"  {model_id:<28} dir_acc delta: {arrow} {delta:+.1%}  "
            f"F1: {cmp.get('f1_delta', 0.0):+.3f}"
        )

    lines.append(sep)
    return "\n".join(lines)
