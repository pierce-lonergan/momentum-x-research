# LLM Performance Arena — System Overview

The LLM Performance Arena is a framework for evaluating and comparing LLM-based
trading agents against a ground-truth labeled dataset of premarket gap scenarios.
It answers the question: **does this agent configuration make money, and is that
better than what we had before?**

---

## Why it exists

The Momentum-X system uses multiple LLM agents (news, fundamental, technical, risk,
manipulation) to make premarket trading decisions. Different models, prompts, and
agent configurations produce different results — but without a rigorous comparison
framework, it is impossible to know which change is genuinely better and which is
noise.

The Arena provides:

- A labeled dataset of 500+ real scenarios with ground-truth outcomes
- A scoring engine that computes classification, calibration, operational, and financial metrics
- An A/B experiment engine with bootstrap statistical significance testing
- A report generator that makes results readable and actionable

---

## Components

```
Component 1: Labeled Dataset     src/llm_arena/models.py
                                 src/llm_arena/auto_labeler.py
                                 src/llm_arena/dataset.py

Component 2: Agent Harness       src/llm_arena/harness.py

Component 3: Scoring Engine      src/llm_arena/scoring.py

Component 4: Experiment Engine   src/llm_arena/experiment.py
                                 src/llm_arena/experiments_library.py

Component 5: Report Generator    src/llm_arena/report.py
```

---

## Component 1: Labeled Dataset

**Files:** `models.py`, `auto_labeler.py`, `dataset.py`

Stores ground-truth scenarios. Each `LabeledScenario` represents one stock on one
premarket session and contains:

- Premarket inputs: gap %, RVOL, dollar volume, headlines, SEC filings
- Ground truth labels: catalyst type (FDA, earnings, pharma deal, etc.) and price outcome (RUNNER/FADER/MIXED/FLAT)
- Correct signal: what a perfect agent should have said (STRONG_BULL through STRONG_BEAR)
- Actual system output: what our agents said, MFCS, trade decision, P&L
- Label confidence: AUTO_HIGH / AUTO_MEDIUM / AUTO_LOW / UNLABELED / VERIFIED

`AutoLabeler` builds scenarios from existing Momentum-X data sources (journals,
trade results, session reports, selection arena universe files). `DatasetManager`
handles persistence (one JSON file per date + an index file) and filtering.

**Docs:** `docs/LLM_Arena_Dataset.md`

---

## Component 2: Agent Harness

**File:** `harness.py`

Runs agent configurations against labeled scenarios in two modes:

| Mode | API Calls | Cost | Use For |
|------|-----------|------|---------|
| Replay | None | Free | Testing scoring pipeline with stored signals |
| Live | Yes | Real cost | Comparing new models against historical scenarios |

`AgentConfig` specifies the agent type, model ID, prompt template, and operational
parameters (timeout, temperature, max tokens). `AgentHarness.run_batch()` runs a
list of scenarios and returns `AgentRunResult` objects with the signal direction,
confidence, parse success, latency, and token counts.

**Docs:** `docs/LLM_Arena_Harness.md`

---

## Component 3: Scoring Engine

**File:** `scoring.py`

Turns `AgentRunResult` lists into structured `AgentScorecard` objects. Computes
four metric groups:

| Group | Key Metrics |
|-------|------------|
| Classification | direction_accuracy, precision, recall, F1, TP/FP/TN/FN |
| Calibration | calibration_error, overconfidence_rate |
| Operational | parse_success_rate, timeout_rate, latency percentiles |
| Financial | captured_gains, avoided_losses, signal_value, ROI |

`MetricsCalculator.compare_scorecards()` computes metric deltas between a baseline
and a variant, returning a summary dict for the experiment engine.

**Docs:** `docs/LLM_Arena_Scoring.md`

---

## Component 4: Experiment Engine

**File:** `experiment.py`, `experiments_library.py`

Runs controlled A/B comparisons between agent configurations.

```
ExperimentConfig  (baseline AgentConfig + list of variant AgentConfigs)
        |
ExperimentEngine.run_experiment()
        |
        +-- filter dataset (scenario_filters, optional train/test split)
        +-- run baseline + each variant through harness
        +-- score all results into AgentScorecards
        +-- compute scorecard deltas (compare_scorecards)
        +-- bootstrap significance test per variant (1000 iterations, 95% CI)
        +-- pick winner + generate recommendation text
        |
ExperimentResult  (scorecards + comparisons + significance + winner + recommendation)
```

The bootstrap test resamples scenario-level binary outcomes (correct/incorrect)
with replacement and computes the fraction of bootstrap deltas that were <= 0.
Cohen's d is computed from the pooled standard deviation of the per-scenario values.

Results are persisted to `data/llm_arena/experiments/{name}.json` and can be
reloaded via `ExperimentEngine.load_experiment()`.

**Docs:** `docs/LLM_Arena_Experiments.md`

---

## Component 5: Report Generator

**File:** `report.py`

Formats `AgentScorecard` and `ExperimentResult` objects into human-readable ASCII
text. All output uses only printable ASCII (no unicode box-drawing characters) for
Windows terminal and log file compatibility.

Key methods:

| Method | Output |
|--------|--------|
| `generate_scorecard_report(scorecard)` | Full scorecard: classification, calibration, operational, financial, breakdowns |
| `generate_experiment_report(result)` | Complete A/B report: executive summary, comparison table, significance, scorecards, recommendation |
| `generate_comparison_table(baseline, variant, sig)` | Side-by-side metric delta table with significance stars |
| `generate_confusion_matrix_display(metrics)` | TP/FP/TN/FN in ASCII grid |
| `generate_calibration_chart(scorecard)` | Calibration error with quality label |
| `generate_recommendations(result)` | Standalone recommendation section |
| `generate_dataset_quality_report(stats)` | Dataset coverage audit with bar charts |
| `save_markdown(content, filepath)` | Saves any report to a .md file |

**Docs:** `docs/LLM_Arena_Reports.md`

---

## Data flow (end-to-end)

```
Historical data (journals, trade results, premarket)
        |
        v
AutoLabeler.build_from_*()
        |
        v
DatasetManager (index.json + scenarios_YYYY-MM-DD.json)
        |
        v
DatasetManager.filter(outcome=RUNNER, label_confidence=AUTO_HIGH, ...)
        |
        v
AgentHarness.run_batch(scenarios, AgentConfig, mode="replay")
        |
        v
MetricsCalculator.score_results(results, scenarios) -> AgentScorecard
        |
        v
ExperimentEngine.run_experiment(ExperimentConfig) -> ExperimentResult
        |
        v
ReportGenerator.generate_experiment_report(result) -> str
        |
        v
save_markdown(report, "data/llm_arena/reports/experiment.md")
```

---

## Directory layout

```
src/llm_arena/
    __init__.py                 Public API exports
    models.py                   LabeledScenario, enums
    auto_labeler.py             AutoLabeler
    dataset.py                  DatasetManager
    harness.py                  AgentConfig, AgentRunResult, AgentHarness
    scoring.py                  ClassificationMetrics, AgentScorecard, MetricsCalculator
    experiment.py               ExperimentConfig, ExperimentResult, ExperimentEngine
    experiments_library.py      Pre-built experiment configs
    report.py                   ReportGenerator

data/llm_arena/
    scenarios/                  Raw labeled scenarios by date
    results/                    Saved AgentRunResult batches
    experiments/                Saved ExperimentResult JSON files
    reports/                    Generated .md reports

tests/unit/
    test_llm_arena_dataset.py
    test_llm_arena_harness.py
    test_llm_arena_scoring.py
    test_llm_arena_experiment.py
    test_llm_arena_report.py

docs/
    LLM_Arena_Dataset.md
    LLM_Arena_Harness.md
    LLM_Arena_Scoring.md
    LLM_Arena_Experiments.md
    LLM_Arena_Reports.md
    LLM_Arena_Overview.md       (this file)
```

---

## Interpreting results

**Direction accuracy** is the primary metric. The baseline (random-ish news agent
on replay data) tends to run ~30-35% on hard scenarios. Anything above 55% with
p < 0.05 is worth investigating for live deployment.

**False positive rate** matters more than recall. A false positive on a 9:31am
premarket gap trade can lose 20-30% before the stop fires. The experiment engine
rejects variants where FP count is more than 1.5x the baseline even if direction
accuracy improved.

**ROI** above 10x means the LLM cost is negligible compared to the trading P&L it
influences. ROI below 1x means the agent costs more to run than the value it adds.

**Cohen's d** thresholds for effect size:

| d | Label |
|---|-------|
| >= 0.8 | large |
| >= 0.5 | medium |
| >= 0.2 | small |
| < 0.2 | negligible |

A statistically significant result with a negligible effect size is not worth
deploying. Target d >= 0.5 (medium) for production changes.
