# LLM Arena — Experiment Engine (Component 4)

The Experiment Engine is the A/B testing framework for the LLM Performance Arena.
It runs controlled comparisons between agent configurations, scores them against the
same labeled scenarios, and determines whether observed differences are statistically
significant via bootstrap testing.

---

## Architecture

```
ExperimentConfig         defines the test (baseline, variants, filters)
     |
ExperimentEngine.run_experiment()
     |
     +-- _select_scenarios()      filter dataset, optional train/test split
     |
     +-- _run_config()            harness.run_batch() + scorer.score_results()
     |         (once for baseline, once per variant)
     |
     +-- scorer.compare_scorecards()   compute metric deltas
     |
     +-- _compute_significance()  bootstrap test per variant
     |
     +-- _generate_recommendation()
     |
ExperimentResult         scorecards + comparisons + significance + recommendation
```

Files:
- `src/llm_arena/experiment.py` — ExperimentEngine, ExperimentConfig, ExperimentResult, SignificanceResult
- `src/llm_arena/experiments_library.py` — factory functions for 6 pre-defined experiments
- `scripts/run_llm_arena.py` — CLI commands: `experiment`, `experiments`, `experiment-result`

---

## How to Define an Experiment (ExperimentConfig)

```python
from src.llm_arena.experiment import ExperimentConfig
from src.llm_arena.harness import AgentConfig

config = ExperimentConfig(
    name="my_experiment",
    description="Short description of what we're testing",

    # The agent configuration to use as the control
    baseline=AgentConfig(
        agent_type="news",
        model_id="mixtral-8x7b",
        temperature=0.0,
    ),

    # One or more variants to compare against baseline
    variants=[
        AgentConfig(
            agent_type="news",
            model_id="claude-haiku-4-5-20251001",
            temperature=0.0,
        ),
    ],

    # Scenario selection: keys match DatasetManager.filter() kwargs
    # Accepts: outcome, catalyst_type, label_confidence, min_gap_pct,
    #          date_range, was_traded
    scenario_filters={"outcome": "runner"},  # or {} for all scenarios

    # Hold out this fraction as a validation set (0.0 = use all)
    test_split_pct=0.20,

    # Statistical parameters
    bootstrap_iterations=1000,
    confidence_level=0.95,
    min_scenarios=20,     # raise ValueError if fewer scenarios match

    # Execution mode
    mode="replay",        # "replay" (free, instant) or "live" (API calls)
    max_concurrent=5,
)
```

### Scenario filters

| Key               | Type / Example             | Description                          |
|-------------------|----------------------------|--------------------------------------|
| `outcome`         | `"runner"` / `StockOutcome`| Filter by RUNNER, FADER, MIXED, FLAT |
| `catalyst_type`   | `"earnings"` / `CatalystType` | Filter by catalyst classification |
| `label_confidence`| `"auto_high"` / `LabelConfidence` | Filter by label quality      |
| `min_gap_pct`     | `0.20`                     | Keep only gap >= 20%                 |
| `date_range`      | `(date(2024,1,1), date(2024,12,31))` | Date window             |
| `was_traded`      | `True`                     | Only scenarios the system traded     |

String values are auto-converted to enums by the engine.

---

## How Significance Testing Works (Bootstrap Method)

The engine uses a **one-sided bootstrap permutation test** on `direction_accuracy`
(percentage of scenarios where the agent called the correct direction).

### Algorithm

1. **Identify common scenarios**: Find scenario IDs that have results from both
   baseline and variant.

2. **Compute observed delta**:
   `delta = variant_accuracy - baseline_accuracy`

3. **Bootstrap loop** (default 1000 iterations):
   - Resample `n` scenario indices *with replacement*
   - Compute `baseline_accuracy` on the resampled set
   - Compute `variant_accuracy` on the resampled set
   - Record `boot_delta = variant - baseline`

4. **p-value** (one-sided, H1: variant > baseline):
   `p = fraction of boot_deltas <= 0`

   A small p means the variant consistently outperforms baseline even when
   accounting for sampling variability.

5. **Confidence interval**: `[alpha/2, 1-alpha/2]` percentiles of the bootstrap
   delta distribution. At 95% confidence: `[2.5th, 97.5th]` percentile.

6. **Effect size** (Cohen's d):
   `d = delta / pooled_std`
   where `pooled_std = sqrt((std_baseline^2 + std_variant^2) / 2)`.

### Significance threshold

`significant = (p_value < 1 - confidence_level)`

Default: `confidence_level=0.95` means `p < 0.05` is significant.

### Why bootstrap instead of a t-test?

- Direction accuracy is a binary proportion (0 or 1 per scenario), not Gaussian.
- Sample sizes are small (20-509 scenarios), making parametric assumptions fragile.
- Bootstrap makes no distributional assumptions and correctly handles the
  discrete nature of the metric.

---

## How Recommendations Are Generated

Priority order: `direction_accuracy` > `false_positive_rate` > `catalyst_accuracy` > `latency`

**A variant wins if:**
1. Its bootstrap p-value < alpha (significant improvement on primary metric), AND
2. Its false positive count is not >1.5x baseline FP count (no precision regression).

**If multiple variants win:** the one with the largest `delta` on direction_accuracy
is selected as the winner.

**If no variant wins:** baseline is recommended (conservative default). The
recommendation string says "Baseline '...' recommended (conservative default)."

---

## Pre-Defined Experiments

### replay_validation (mode: replay)

Validates the full pipeline end-to-end without any API calls.

- **Baseline**: `news_agent` — news headline analysis
- **Variant 1**: `fundamental_agent` — SEC filing analysis
- **Variant 2**: `manipulation_classifier` — manipulation risk signals
- **Scenarios**: All 509 labeled scenarios
- **Purpose**: Confirm pipeline works and establish agent baseline benchmarks

**First run result (2026-04-02, 509 scenarios):**
```
Baseline (news_agent):         direction_accuracy = 30.6%
Variant (fundamental_agent):   direction_accuracy = 55.2%  (+24.6%, p=0.000)  WINNER
Variant (manipulation):        direction_accuracy = 11.4%  (-19.3%, p=1.000)
```

### model_comparison (mode: live)

Compares Mixtral-8x7B vs Haiku 4.5 vs Qwen3-235B for the news agent.
Hypothesis: newer/larger models produce better direction accuracy.
Requires live LLM API access.

### prompt_engineering (mode: live)

Tests default prompt vs concise_v2 vs chain_of_thought prompt templates.
Hypothesis: structured prompts improve catalyst classification.
Requires live LLM API access.

### two_pass_classifier (mode: live)

Fast binary filter (pass 1, cheap model) + full analysis (pass 2, expensive model).
Hypothesis: two-pass reduces cost and latency while improving precision.
Requires live LLM API access.

### ensemble_voting (mode: live)

3-model majority vote (Mixtral + Haiku + Qwen3) vs single model.
Hypothesis: ensemble reduces false positives.
Requires live LLM API access.

### sec_data_integration (mode: live)

News headlines + pre-fetched SEC filing data vs headlines only.
Hypothesis: SEC data reduces false positives on promotional/squeeze plays.
Filtered to `catalyst_type=sec_filing` scenarios.
Requires live LLM API + SEC data pipeline.

---

## CLI Usage

### Run a pre-defined experiment

```bash
# Run replay_validation (no API calls required)
python scripts/run_llm_arena.py experiment --name replay_validation

# Filter to runner scenarios only
python scripts/run_llm_arena.py experiment --name replay_validation --outcome runner

# Filter by catalyst type
python scripts/run_llm_arena.py experiment --name replay_validation --catalyst earnings

# Override bootstrap iterations (faster for development)
python scripts/run_llm_arena.py experiment --name replay_validation --bootstrap 200

# Don't save results to disk
python scripts/run_llm_arena.py experiment --name replay_validation --no-save

# Print raw JSON result
python scripts/run_llm_arena.py experiment --name replay_validation --json
```

### List experiments

```bash
# Show pre-defined experiments + saved experiment summaries
python scripts/run_llm_arena.py experiments
```

### Display a saved result

```bash
# Show summary
python scripts/run_llm_arena.py experiment-result --name replay_validation

# Show full scorecards and comparison tables
python scripts/run_llm_arena.py experiment-result --name replay_validation --detail

# Print raw JSON
python scripts/run_llm_arena.py experiment-result --name replay_validation --json
```

Saved experiment files live at `data/llm_arena/experiments/{name}.json`.

---

## How to Create Custom Experiments

### Inline config

```python
from src.llm_arena.experiment import ExperimentConfig, ExperimentEngine
from src.llm_arena.harness import AgentConfig, AgentHarness
from src.llm_arena.dataset import DatasetManager
from src.llm_arena.scoring import MetricsCalculator

# Load dataset
dataset = DatasetManager("data/llm_arena")
dataset.load()

# Build engine
harness = AgentHarness("data/llm_arena")
scorer = MetricsCalculator()
engine = ExperimentEngine(dataset, harness, scorer, "data/llm_arena/experiments")

# Define experiment
config = ExperimentConfig(
    name="my_test",
    description="Testing a new prompt template",
    baseline=AgentConfig(agent_type="news", model_id="replay"),
    variants=[AgentConfig(agent_type="fundamental", model_id="replay")],
    scenario_filters={"outcome": "fader"},
    bootstrap_iterations=500,
    mode="replay",
)

# Run
result = engine.run_experiment(config)
engine.save_experiment(result)
print(result.recommendation)
```

### Adding to the library

Add a factory function to `src/llm_arena/experiments_library.py` and register
it in `_EXPERIMENT_REGISTRY`:

```python
def create_my_experiment() -> ExperimentConfig:
    return ExperimentConfig(
        name="my_experiment",
        description="...",
        baseline=AgentConfig(...),
        variants=[AgentConfig(...)],
        mode="live",
    )

_EXPERIMENT_REGISTRY["my_experiment"] = create_my_experiment
```

It will then appear in `experiments` list and be runnable via the CLI.

---

## Reading Results Programmatically

```python
from src.llm_arena.experiment import ExperimentEngine, format_experiment_result
from src.llm_arena.harness import AgentHarness
from src.llm_arena.dataset import DatasetManager
from src.llm_arena.scoring import MetricsCalculator

engine = ExperimentEngine(
    DatasetManager("data/llm_arena"),
    AgentHarness("data/llm_arena"),
    MetricsCalculator(),
    "data/llm_arena/experiments",
)

result = engine.load_experiment("replay_validation")

# Top-level result
print(result.winner)               # "fundamental"
print(result.recommendation)       # multi-line string

# Significance for first variant
sig = result.significance[0]
print(f"p={sig.p_value:.4f}  d={sig.effect_size:.3f}  significant={sig.significant}")
print(f"CI: [{sig.confidence_interval[0]:+.1%}, {sig.confidence_interval[1]:+.1%}]")

# Scorecard for baseline
bc = result.baseline_scorecard
print(f"Baseline direction_accuracy: {bc.classification.direction_accuracy:.1%}")

# List all saved experiments
for exp in engine.list_experiments():
    print(exp["name"], exp["winner"], exp["primary_p_value"])
```

---

## Component Map

| Component | File | Status |
|-----------|------|--------|
| 1 — Labeled Dataset | `src/llm_arena/models.py`, `dataset.py`, `auto_labeler.py` | Done |
| 2 — Agent Harness | `src/llm_arena/harness.py` | Done (replay), live stubbed |
| 3 — Scoring Engine | `src/llm_arena/scoring.py` | Done |
| 4 — Experiment Engine | `src/llm_arena/experiment.py`, `experiments_library.py` | Done |
| 5 — Dashboard | TBD | Pending |
