# LLM Arena Report Generator (Component 5)

The Report Generator turns raw `AgentScorecard` and `ExperimentResult` objects into
human-readable ASCII text. All output is plain ASCII — no unicode box-drawing characters
— so reports display correctly on Windows terminals, in log files, and in Markdown.

---

## Quick Start

```python
from src.llm_arena import ReportGenerator, MetricsCalculator
from src.llm_arena.experiment import ExperimentEngine, ExperimentConfig

gen = ReportGenerator()

# --- Single-agent scorecard ---
calc = MetricsCalculator()
scorecard = calc.score_results(results, scenarios)
print(gen.generate_scorecard_report(scorecard))

# --- Full A/B experiment ---
engine = ExperimentEngine(dataset, harness, calc, "data/llm_arena/experiments")
result = engine.run_experiment(config)
report = gen.generate_experiment_report(result)
print(report)
gen.save_markdown(report, "data/llm_arena/reports/my_experiment.md")
```

---

## Methods

### `generate_scorecard_report(scorecard: AgentScorecard) -> str`

Formats a single agent scorecard into a 72-column text block. Sections:

1. **Header** — agent type, model, scenario count
2. **Classification Performance** — direction accuracy, catalyst accuracy, confusion matrix, precision/recall/F1, runner/fader accuracy
3. **Confidence Calibration** — calibration error, overconfidence rate, quality label
4. **Operational Performance** — parse success, timeout rate, median/P95/P99 latency, tokens per call
5. **Financial Impact** — captured gains, avoided losses, signal value, cost per signal, ROI
6. **Breakdown by Catalyst Type** — per-type accuracy with ASCII bar chart (if data present)
7. **Breakdown by Outcome** — per-outcome accuracy with ASCII bar chart (if data present)

**Example output (abbreviated):**

```
========================================================================
  AGENT SCORECARD: fundamental
  Model: sonnet  |  Scenarios: 26
========================================================================

CLASSIFICATION PERFORMANCE
------------------------------------------------------------------------
  Direction Accuracy : 55.2%
  Catalyst Accuracy  : 40.0%

  Confusion Matrix:
                                   Pred BULL      Pred BEAR/NEUTRAL
  Actual RUNNER (opportunity)         TP=10                   FN=3
  Actual FADER  (avoid)               FP=5                   TN=8

  Precision : 0.667   Recall : 0.769   F1 : 0.714

  Accuracy on RUNNERS : 76.9%
  Accuracy on FADERS  : 61.5%

CONFIDENCE CALIBRATION
------------------------------------------------------------------------
  Calibration Error    : 0.080  (slightly miscalibrated)
  Overconfidence Rate  : 35.0%  (underconfident)
  Interpretation: Agent confidence vs actual accuracy gap = 0.080
    0.00-0.05 = well-calibrated  |  0.05-0.10 = slight issue
    0.10-0.20 = moderate issue   |  >0.20     = poorly calibrated

OPERATIONAL PERFORMANCE
------------------------------------------------------------------------
  Parse Success Rate : 98.0%
  Timeout Rate       : 2.0%
  Median Latency     : 1,200 ms
  P95 Latency        : 2,500 ms
  P99 Latency        : 4,000 ms
  Avg Tokens/Call    : 600

FINANCIAL IMPACT
------------------------------------------------------------------------
  Captured Gains  :    $ 8,500
  Avoided Losses  :    $ 4,000
  Signal Value    :   $12,500
  Cost per Signal :      $0.0012
  ROI             :        42.5x

BREAKDOWN BY CATALYST TYPE
------------------------------------------------------------------------
  earnings                  [#########...........]  45.0%
  fda                       [##############......]  70.0%
  pharma_deal               [######..............]  30.0%
========================================================================
```

---

### `generate_experiment_report(result: ExperimentResult) -> str`

Generates a complete A/B experiment report. Sections:

1. **Header** — experiment name, description, date, scenario count, bootstrap settings
2. **Executive Summary** — winner, delta, p-value, effect size in one paragraph
3. **Experiment Setup** — baseline and variant configs, applied filters
4. **Head-to-Head Comparison Table** — one table per variant (calls `generate_comparison_table`)
5. **Statistical Significance** — p-value, CI, Cohen's d for each variant
6. **Detailed Scorecards** — full scorecard for baseline and each variant
7. **Recommendation** — actionable text with impact label (HIGH / MEDIUM / LOW)

```python
report = gen.generate_experiment_report(result)
print(report)
# Save as markdown
gen.save_markdown(report, "data/llm_arena/reports/replay_validation.md")
```

---

### `generate_comparison_table(baseline, variant, significance=None) -> str`

Generates a side-by-side table comparing baseline and variant scorecards. Columns:
`Metric | Baseline | Variant | Delta | Sig?`

The `Sig?` column shows `***` / `**` / `*` / `ns` for the primary metric
(direction_accuracy) when a `SignificanceResult` is provided.

Rows covered:

| Group | Metrics |
|-------|---------|
| Classification | Direction Accuracy, Catalyst Accuracy, F1 Score, Precision, Recall, False Positive Rate, Calibration Error |
| Operational | Parse Success Rate, Timeout Rate, Median Latency (ms) |
| Financial | Signal Value ($), ROI |

**Example output (abbreviated):**

```
  Metric                       | Baseline | Variant  | Delta    | Sig?
  ----------------------------+-+----------+-+----------+-+----------+-+----
  Direction Accuracy           |   30.6%  |   55.2%  |  +24.6%  | ***
  Catalyst Accuracy            |   40.0%  |   40.0%  |   +0.0%  |
  F1 Score                     |    0.714 |    0.714 |   +0.000 |
  ...
```

---

### `generate_confusion_matrix_display(metrics: ClassificationMetrics) -> str`

Renders the four confusion matrix cells in ASCII table format.

```
  Confusion Matrix:
                                   Pred BULL      Pred BEAR/NEUTRAL
  Actual RUNNER (opportunity)         TP=10                   FN=3
  Actual FADER  (avoid)               FP=5                   TN=8
```

Can be called with a `ClassificationMetrics` object directly (not the full scorecard),
which is useful for comparisons within test code.

---

### `generate_calibration_chart(scorecard: AgentScorecard) -> str`

Displays calibration error and overconfidence rate with a plain-text quality label.

Quality thresholds:

| Calibration Error | Label |
|-------------------|-------|
| < 0.05 | well-calibrated |
| 0.05 - 0.10 | slightly miscalibrated |
| 0.10 - 0.20 | moderately miscalibrated |
| > 0.20 | poorly calibrated |

---

### `generate_recommendations(result: ExperimentResult) -> str`

Returns the RECOMMENDATION section as a standalone string. Useful for emailing
or logging just the conclusion without the full report.

For each winning variant (significant + delta > 0) it adds an impact label:
- **HIGH IMPACT** — Cohen's d >= 0.5
- **MEDIUM IMPACT** — Cohen's d >= 0.2
- **LOW IMPACT** — Cohen's d < 0.2

---

### `generate_dataset_quality_report(dataset_stats: dict) -> str`

Generates a quality audit report from a stats dict. Expected keys:

| Key | Type | Description |
|-----|------|-------------|
| `total` | int | Total scenario count |
| `traded` | int | Scenarios that triggered a trade |
| `trade_wins` | int | Winning trades |
| `trade_win_rate` | float | Win rate (0.0 - 1.0) |
| `by_outcome` | dict[str, int] | Counts per StockOutcome |
| `by_catalyst_type` | dict[str, int] | Counts per CatalystType |
| `by_label_confidence` | dict[str, int] | Counts per LabelConfidence |

Reports:
- Outcome distribution with ASCII bar chart
- Catalyst type distribution with ASCII bar chart
- Label confidence distribution with ASCII bar chart
- Gaps (catalyst types or outcomes with < 10 scenarios)
- Recommendations for improving dataset coverage

---

### `save_markdown(content: str, filepath: str) -> None`

Saves any report string to a Markdown file. The content is wrapped in a
triple-backtick code block to preserve fixed-width formatting in GitHub
and other Markdown renderers.

```python
gen.save_markdown(report, "data/llm_arena/reports/experiment_2026-04-02.md")
```

The method creates missing parent directories automatically.

---

### Formatting helpers

| Method | Description |
|--------|-------------|
| `format_pct(value)` | Returns `+24.6%` or `-14.0%` (always with sign) |
| `format_significance(p_value)` | Returns `***`, `** `, `*  `, or `ns ` |

Significance thresholds:

| p-value | Stars |
|---------|-------|
| < 0.001 | `***` |
| < 0.01 | `** ` |
| < 0.05 | `*  ` |
| >= 0.05 | `ns ` |

---

## ASCII-only constraint

All output from `ReportGenerator` uses only printable ASCII characters (codes 32-126).
This means:
- No unicode box-drawing characters (`|`, `-`, `+` used instead of `+--+`)
- No arrows or checkmarks
- No non-breaking spaces

This ensures compatibility with Windows terminals, Windows log files, and `.md`
files viewed on any platform.

---

## File layout

```
src/llm_arena/report.py         ReportGenerator class
tests/unit/test_llm_arena_report.py   17 unit tests
data/llm_arena/reports/         default output directory for saved reports
```

---

## Running the tests

```bash
python -m pytest tests/unit/test_llm_arena_report.py -v
```

Expected: 17 tests, all passing.
