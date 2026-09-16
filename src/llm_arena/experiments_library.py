"""Pre-defined experiments for the LLM Performance Arena.

Factory functions that return ready-to-run ExperimentConfig objects for
the 5 priority experiments from the design doc, plus a replay validation
experiment that works immediately without any API calls.

Quick start (replay validation)::

    from src.llm_arena.experiments_library import create_replay_validation_experiment
    config = create_replay_validation_experiment()

Live-mode experiments (require LLM API)::

    from src.llm_arena.experiments_library import create_model_comparison_experiment
    config = create_model_comparison_experiment()
    # Will raise NotImplementedError until live mode is implemented in the harness
"""

from __future__ import annotations

from datetime import datetime, timezone

from .experiment import ExperimentConfig
from .harness import AgentConfig


# ---------------------------------------------------------------------------
# Experiment 0: Replay validation
# ---------------------------------------------------------------------------


def create_replay_validation_experiment() -> ExperimentConfig:
    """Quick A/B test using replay data to validate the full pipeline.

    Compares news_agent signals vs fundamental_agent signals on all 509
    scenarios.  Both use stored (replay) data — no API calls required.
    This validates: dataset → harness → scoring → experiment → result.

    Baseline:  news_agent (news catalyst analysis)
    Variant 1: fundamental_agent (filing-based analysis)
    Variant 2: manipulation_classifier (manipulation risk)
    """
    return ExperimentConfig(
        name="replay_validation",
        description=(
            "Replay validation: news_agent vs fundamental_agent vs "
            "manipulation_classifier on all 509 scenarios (no API calls)"
        ),
        baseline=AgentConfig(
            agent_type="news",
            model_id="replay",
        ),
        variants=[
            AgentConfig(
                agent_type="fundamental",
                model_id="replay",
            ),
            AgentConfig(
                agent_type="manipulation",
                model_id="replay",
            ),
        ],
        scenario_filters={},  # All scenarios
        test_split_pct=0.0,
        bootstrap_iterations=1000,
        confidence_level=0.95,
        min_scenarios=20,
        mode="replay",
        created_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Experiment 1: Model comparison
# ---------------------------------------------------------------------------


def create_model_comparison_experiment() -> ExperimentConfig:
    """Compare model providers for the news_agent.

    Baseline: Current Mixtral-8x7B (production default).
    Variants:
      - claude-haiku-4-5  (fast, cheap)
      - qwen3-235b        (strong reasoning)

    Requires live mode.  Run after implementing AgentHarness.run_batch live.
    """
    return ExperimentConfig(
        name="model_comparison",
        description=(
            "Compare Mixtral-8x7B (baseline) vs Haiku 4.5 and Qwen3-235B "
            "for the news_agent on direction accuracy"
        ),
        baseline=AgentConfig(
            agent_type="news",
            model_id="mixtral-8x7b",
            temperature=0.0,
            max_tokens=1024,
        ),
        variants=[
            AgentConfig(
                agent_type="news",
                model_id="claude-haiku-4-5-20251001",
                temperature=0.0,
                max_tokens=1024,
            ),
            AgentConfig(
                agent_type="news",
                model_id="qwen3-235b",
                temperature=0.0,
                max_tokens=1024,
            ),
        ],
        scenario_filters={},
        test_split_pct=0.20,
        bootstrap_iterations=1000,
        confidence_level=0.95,
        min_scenarios=50,
        mode="live",
        created_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Experiment 2: Prompt engineering
# ---------------------------------------------------------------------------


def create_prompt_engineering_experiment() -> ExperimentConfig:
    """Test different prompt templates for the news_agent.

    Baseline: Default production prompt.
    Variants:
      - concise_v2: Shorter prompt focused on catalyst classification only.
      - chain_of_thought: Added explicit reasoning chain before verdict.

    Requires live mode.
    """
    return ExperimentConfig(
        name="prompt_engineering",
        description=(
            "Compare prompt variants for news_agent: default vs concise_v2 "
            "vs chain_of_thought on direction accuracy and latency"
        ),
        baseline=AgentConfig(
            agent_type="news",
            model_id="mixtral-8x7b",
            prompt_template=None,  # Default production prompt
            temperature=0.0,
        ),
        variants=[
            AgentConfig(
                agent_type="news",
                model_id="mixtral-8x7b",
                prompt_template="concise_v2",
                temperature=0.0,
            ),
            AgentConfig(
                agent_type="news",
                model_id="mixtral-8x7b",
                prompt_template="chain_of_thought",
                temperature=0.0,
            ),
        ],
        scenario_filters={},
        test_split_pct=0.20,
        bootstrap_iterations=1000,
        confidence_level=0.95,
        min_scenarios=50,
        mode="live",
        created_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Experiment 3: Two-pass classifier
# ---------------------------------------------------------------------------


def create_two_pass_experiment() -> ExperimentConfig:
    """Compare single-pass vs two-pass classification.

    Baseline: Single model call → direction + confidence.
    Variant:  Pass 1 = fast binary "tradeable/skip" filter (cheap model),
              Pass 2 = full analysis only on pass-1 positives (expensive model).

    Hypothesis: Two-pass reduces cost and latency while improving precision
    by filtering out noise before the expensive analysis.

    Requires live mode.
    """
    return ExperimentConfig(
        name="two_pass_classifier",
        description=(
            "Two-pass classifier: fast binary filter (pass 1) + detailed "
            "analysis (pass 2) vs single-pass Mixtral-8x7B"
        ),
        baseline=AgentConfig(
            agent_type="news",
            model_id="mixtral-8x7b",
            temperature=0.0,
        ),
        variants=[
            AgentConfig(
                agent_type="news",
                model_id="mixtral-8x7b",
                extra_params={
                    "mode": "two_pass",
                    "pass1_model": "claude-haiku-4-5-20251001",
                    "pass2_model": "mixtral-8x7b",
                },
                temperature=0.0,
            ),
        ],
        scenario_filters={},
        test_split_pct=0.20,
        bootstrap_iterations=1000,
        confidence_level=0.95,
        min_scenarios=50,
        mode="live",
        created_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Experiment 4: Multi-model ensemble
# ---------------------------------------------------------------------------


def create_ensemble_experiment() -> ExperimentConfig:
    """Multi-model ensemble voting vs single model.

    Baseline: Single news_agent (Mixtral-8x7B).
    Variant:  Majority vote across 3 models (Mixtral, Haiku, Qwen3).
              Final signal = direction chosen by ≥2 of 3 models;
              confidence = fraction of models in agreement.

    Hypothesis: Ensemble reduces false positives without hurting recall.

    Requires live mode.
    """
    return ExperimentConfig(
        name="ensemble_voting",
        description=(
            "3-model majority-vote ensemble (Mixtral + Haiku + Qwen3) "
            "vs single Mixtral-8x7B for news_agent"
        ),
        baseline=AgentConfig(
            agent_type="news",
            model_id="mixtral-8x7b",
            temperature=0.0,
        ),
        variants=[
            AgentConfig(
                agent_type="news",
                model_id="ensemble",
                extra_params={
                    "ensemble_models": [
                        "mixtral-8x7b",
                        "claude-haiku-4-5-20251001",
                        "qwen3-235b",
                    ],
                    "voting_strategy": "majority",
                },
                temperature=0.0,
            ),
        ],
        scenario_filters={},
        test_split_pct=0.20,
        bootstrap_iterations=1000,
        confidence_level=0.95,
        min_scenarios=50,
        mode="live",
        created_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Experiment 5: SEC filing integration
# ---------------------------------------------------------------------------


def create_sec_integration_experiment() -> ExperimentConfig:
    """Test pre-fetched SEC filing data augmentation.

    Baseline: news_agent with headlines only.
    Variant:  news_agent + pre-fetched SEC filing summaries injected into
              the prompt (S-3, 424B5, 8-K filings from prior 30 days).

    Hypothesis: Dilution/offering signals from SEC data reduce false
    positives on promotional/squeeze plays.

    Requires live mode + SEC data pipeline.
    """
    return ExperimentConfig(
        name="sec_data_integration",
        description=(
            "news_agent + pre-fetched SEC filing data vs headlines-only "
            "baseline; targets false positive reduction on promo/squeeze"
        ),
        baseline=AgentConfig(
            agent_type="news",
            model_id="mixtral-8x7b",
            temperature=0.0,
        ),
        variants=[
            AgentConfig(
                agent_type="news",
                model_id="mixtral-8x7b",
                extra_params={
                    "use_sec_data": True,
                    "sec_lookback_days": 30,
                    "sec_forms": ["S-3", "424B5", "8-K"],
                },
                temperature=0.0,
            ),
        ],
        # Focus on catalyst types where SEC data matters most
        scenario_filters={"catalyst_type": "sec_filing"},
        test_split_pct=0.20,
        bootstrap_iterations=1000,
        confidence_level=0.95,
        min_scenarios=20,
        mode="live",
        created_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Registry: name → factory
# ---------------------------------------------------------------------------

_EXPERIMENT_REGISTRY: dict[str, callable] = {
    "replay_validation": create_replay_validation_experiment,
    "model_comparison": create_model_comparison_experiment,
    "prompt_engineering": create_prompt_engineering_experiment,
    "two_pass_classifier": create_two_pass_experiment,
    "ensemble_voting": create_ensemble_experiment,
    "sec_data_integration": create_sec_integration_experiment,
}


def get_experiment(name: str) -> ExperimentConfig:
    """Retrieve a pre-defined experiment config by name.

    Raises:
        KeyError: If the name is not in the registry.
    """
    if name not in _EXPERIMENT_REGISTRY:
        available = ", ".join(sorted(_EXPERIMENT_REGISTRY))
        raise KeyError(
            f"Unknown experiment '{name}'. Available: {available}"
        )
    return _EXPERIMENT_REGISTRY[name]()


def list_available_experiments() -> list[dict]:
    """Return metadata for all pre-defined experiments."""
    configs = [factory() for factory in _EXPERIMENT_REGISTRY.values()]
    return [
        {
            "name": c.name,
            "description": c.description,
            "mode": c.mode,
            "baseline": c.baseline.agent_type,
            "variants": [v.agent_type for v in c.variants],
        }
        for c in configs
    ]
