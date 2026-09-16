# LLM Arena Scoring & Metrics Engine

Component 3 of the LLM Performance Arena. Turns raw `AgentRunResult` lists into structured scorecards for agent comparison and experimentation.

## Why these metrics?

The goal is to answer one question: **does this agent configuration make money?** Every metric traces back to that.

- **Classification metrics** — does the agent predict direction correctly?
- **Calibration metrics** — does the agent's stated confidence reflect its actual accuracy?
- **Operational metrics** — is the agent reliable and fast enough to run in production?
- **Financial metrics** — do the correct calls outweigh the cost of the wrong ones?

---

## Classification Metrics

| Metric | Formula | Interpretation |
|--------|---------|----------------|
| `direction_accuracy` | correct directions / total | Fraction of calls where the agent's BULL/BEAR/NEUTRAL matched the ground truth |
| `catalyst_accuracy` | correct catalysts / paired | Fraction of calls where the agent identified the right catalyst type |
| `true_positives` | BULL call + RUNNER outcome | Correct buy signals — the ones that made money |
| `false_positives` | BULL call + FADER outcome | Worst mistake: buying a stock that reversed |
| `true_negatives` | BEAR/NEUTRAL + FADER | Correctly avoided losers |
| `false_negatives` | BEAR/NEUTRAL + RUNNER | Missed winners — opportunity cost |
| `precision` | TP / (TP + FP) | Of all BULL calls, how many were real runners? |
| `recall` | TP / (TP + FN) | Of all real runners, how many did we catch? |
| `f1_score` | 2·P·R / (P+R) | Harmonic mean — balances precision and recall |
| `accuracy_on_runners` | correct / runners | How well does the agent identify stocks that will run? |
| `accuracy_on_faders` | correct / faders | How well does the agent identify stocks that will fade? |

**Why False Positives are the priority:** A false positive on a $2 NASDAQ stock at 9:31am can lose 20-30% before the stop fires. FPs are weighted 1.5x in financial scoring.

---

## Calibration Metrics

Good calibration means that when an agent says 80% confidence, it is actually correct 80% of the time.

**Binning approach:** Results are split into 5 confidence bins: [0–20%), [20–40%), [40–60%), [60–80%), [80–100%]. For each non-empty bin:

```
bin_error = |mean_confidence_in_bin - actual_accuracy_in_bin|
```

| Metric | Formula | Interpretation |
|--------|---------|----------------|
| `calibration_error` | mean bin_error across non-empty bins | Closer to 0 is better; 0.15+ indicates miscalibration |
| `overconfidence_rate` | predictions where conf > bin accuracy / total | High values (>50%) suggest the agent is systematically overconfident |

**Typical values for LLMs:** Most instruction-tuned models are overconfident on zero-shot classification tasks. A calibration error below 0.10 is excellent; above 0.20 is a red flag.

---

## Operational Metrics

| Metric | Interpretation |
|--------|----------------|
| `timeout_rate` | Fraction of calls that exceeded the timeout. >5% is a production risk |
| `parse_success_rate` | Fraction of calls that returned a parseable signal. Should be >95% |
| `median_latency_ms` | Typical per-call latency. Target: <2,000ms for premarket use |
| `p95_latency_ms` | 95th percentile — what the slow tail looks like |
| `p99_latency_ms` | Extreme outliers. High P99 suggests occasional server-side issues |
| `total_tokens_input/output` | Aggregate token usage for cost estimation |
| `avg_tokens_per_call` | Per-call token burn; drives cost projection |

---

## Financial Metrics

Financial metrics require `trade_pnl` or price data (`max_gain_pct`, `max_drawdown_pct`, `open_price`) to be populated in scenarios.

| Metric | How it is computed |
|--------|-------------------|
| `captured_gains` | Sum of `trade_pnl` (or proxy) for all TP calls |
| `avoided_losses` | Sum of `abs(loss proxy)` for all TN calls |
| `signal_value` | `captured_gains + avoided_losses - 1.5×FP_losses - FN_missed_gains` |
| `cost_per_signal` | Average USD cost per call based on token counts and model pricing |
| `roi` | `signal_value / (cost_per_signal × scenario_count)` |

**Token pricing used:**

| Model pattern | Input $/M | Output $/M |
|---------------|-----------|------------|
| opus | $3.00 | $15.00 |
| sonnet | $1.00 | $5.00 |
| haiku | $0.80 | $4.00 |

---

## CLI Usage

### Score a saved experiment

```bash
# Basic scorecard
python scripts/run_llm_arena.py score --results baseline_news

# With per-catalyst and per-outcome breakdown
python scripts/run_llm_arena.py score --results baseline_news --detail

# With raw JSON output for further processing
python scripts/run_llm_arena.py score --results baseline_news --json
```

Results are loaded from `data/llm_arena/results/{NAME}.json`. Create them first with:

```bash
python scripts/run_llm_arena.py replay --agent news --save baseline_news
```

### Compare two experiments

```bash
python scripts/run_llm_arena.py compare --baseline baseline_news --variant improved_news
```

Prints both scorecards then a delta comparison showing which metrics improved.

---

## How to interpret a scorecard

```
======================================================
  AGENT SCORECARD: news_agent (baseline_news)
======================================================

CLASSIFICATION METRICS
  Direction Accuracy:  73.2%       ← Above 65% is useful
  Catalyst Accuracy:   61.5%       ← Bonus signal; 50%+ is ok

  Confusion Matrix:
    TP (Bull on Runner):  142  │  FP (Bull on Fader):   52
    FN (Miss on Runner):   38  │  TN (Bear on Fader):  277

  Precision: 0.732  Recall: 0.789  F1: 0.759

CALIBRATION
  Calibration Error:   0.142       ← <0.10 ideal, >0.20 recalibrate
  Overconfidence Rate: 68.2%       ← >50% means filter low-confidence calls

OPERATIONAL METRICS
  Parse Success Rate:  98.4%       ← Should be >95%
  Timeout Rate:         1.2%       ← Should be <5%
  Median Latency:     1,240ms
  P95 Latency:        3,820ms

FINANCIAL METRICS
  Captured Gains:    $12,450       ← TP value
  Avoided Losses:     $8,320       ← TN value
  Signal Value:      $11,230       ← Net after FP/FN penalties
  Cost per Signal:      $0.28      ← Per-call LLM cost
  ROI:               40.1x         ← >1x means it pays for itself
```

**Rules of thumb:**

- ROI > 10x — the agent is profitable even at modest position sizes
- F1 > 0.70 — the agent finds most runners without too many false alarms
- Overconfidence rate > 70% — consider adding a confidence filter (e.g. only act on calls ≥ 0.75)
- FP count > FN count — the agent is too trigger-happy; tighten the bull threshold

---

## Programmatic usage

```python
from src.llm_arena.scoring import MetricsCalculator, format_scorecard

calc = MetricsCalculator()
scorecard = calc.score_results(results, scenarios)

print(f"Direction accuracy: {scorecard.classification.direction_accuracy:.1%}")
print(f"Signal value: ${scorecard.financial.signal_value:,.0f}")
print(format_scorecard(scorecard, experiment_name="my_run", detail=True))

# Compare two runs
comparison = calc.compare_scorecards(baseline_scorecard, variant_scorecard)
print(comparison["summary"])

# Persist
import json
with open("scorecard.json", "w") as f:
    json.dump(scorecard.to_dict(), f, indent=2)

# Restore
from src.llm_arena.scoring import AgentScorecard
with open("scorecard.json") as f:
    restored = AgentScorecard.from_dict(json.load(f))
```
