# LLM Arena Labeled Dataset

A ground-truth dataset of premarket gap scenarios with auto-labeled catalysts, price outcomes, and correct agent signals. Used to evaluate and benchmark LLM-based trading agents.

---

## Overview

Each record is a **LabeledScenario** — one stock on one session date. It captures:

| Layer | What it stores |
|---|---|
| **Premarket inputs** | Gap %, RVOL, dollar volume, headlines, SEC filings |
| **Ground truth labels** | Catalyst type, price outcome (RUNNER/FADER/MIXED/FLAT) |
| **Correct agent signal** | What a perfect agent should have said (STRONG_BULL → STRONG_BEAR) |
| **Actual system output** | What our agents said, MFCS score, trade decision, P&L |
| **Label confidence** | AUTO_HIGH / AUTO_MEDIUM / AUTO_LOW / UNLABELED / VERIFIED |

---

## Source Data

The auto-labeler reads from four sources:

```
data/
  journals/journal_YYYY-MM-DD_*.jsonl   — agent signals, MFCS, trade decisions
  trade_results.jsonl                   — realized P&L per trade
  scenarios/gap_scenarios.json          — historical gap scenarios with price outcomes
  selection_arena/universe_*.json       — per-session universe with open/high/low/close
```

All sources are merged by `{ticker}_{session_date}` key.

---

## Data Models

### Enums

**CatalystType**
- `fda` — FDA approval, phase trial, NDA/BLA
- `earnings` — Quarterly results, EPS, revenue
- `contract` — Contract award, partnership, deal
- `merger` — M&A, acquisition, takeover
- `pharma_deal` — General pharma/biotech news
- `sec_filing` — Dilution filings (424B5, S-1, S-3)
- `promotional` — No verifiable catalyst, high RVOL
- `squeeze` — Short squeeze dynamics
- `technical_breakout` — Pure technical catalyst
- `unknown` — Not classified

**StockOutcome**
- `runner` — Max gain ≥ 20%, closed above open
- `fader` — Max drawdown ≥ 20%, closed below open
- `mixed` — Ran ≥ 10% then drew down ≥ 15%
- `flat` — Minimal movement

**CorrectSignal**
- `strong_bull` — Real catalyst + RUNNER (confidence 0.70–0.90)
- `bull` — Runner but weaker catalyst (confidence 0.50–0.75)
- `neutral` — MIXED or FLAT (confidence 0.20–0.50)
- `bear` — FADER with ambiguous catalyst (confidence 0.45–0.70)
- `strong_bear` — PROMOTIONAL/SEC_FILING fader (confidence 0.70–0.90)

**LabelConfidence**
- `verified` — Human-reviewed
- `auto_high` — 3+ of: price data, headlines, trade P&L, RVOL
- `auto_medium` — 2 of the above
- `auto_low` — 1 of the above
- `unlabeled` — No usable data

---

## Auto-Labeling Rules

### Catalyst Classification (priority order)

1. SEC filings containing `424B5`, `S-1`, `S-3`, `424B3`, `424B4` → `sec_filing`
2. Headlines matching FDA/approval/phase/NDA/BLA → `fda`
3. Headlines matching earnings/revenue/EPS/quarterly → `earnings`
4. Headlines matching merger/acquisition/buyout/takeover → `merger`
5. Headlines matching contract/awarded/agreement/deal → `contract`
6. Headlines matching squeeze/short squeeze → `squeeze`
7. Headlines matching pharma/biotech/therapeutics/drug → `pharma_deal`
8. No headlines + RVOL > 10× → `promotional`
9. news_agent signal `M_AND_A` → `merger`
10. Fallback → `unknown`

### Outcome Classification

| Condition | Outcome |
|---|---|
| max_gain ≥ 20% AND close > open | RUNNER |
| max_drawdown ≥ 20% AND close < open | FADER |
| max_gain ≥ 10% AND max_drawdown ≥ 15% | MIXED |
| close ≤ −5% from open | FADER |
| close ≥ +5% from open | RUNNER |
| Otherwise | FLAT |

### Correct Signal Assignment

| Outcome | Catalyst | Signal | Confidence |
|---|---|---|---|
| RUNNER | FDA, MERGER, CONTRACT, EARNINGS, PHARMA_DEAL | STRONG_BULL | 0.70–0.90 |
| RUNNER | Other | BULL | 0.50–0.75 |
| FADER | PROMOTIONAL, SEC_FILING | STRONG_BEAR | 0.70–0.90 |
| FADER | Other | BEAR | 0.45–0.70 |
| MIXED | Any | NEUTRAL | 0.30–0.50 |
| FLAT | Any | NEUTRAL | 0.20–0.50 |

---

## Storage Layout

```
data/llm_arena/
  index.json                  — {scenario_id: {date, ticker, outcome, catalyst_type, confidence}}
  scenarios_YYYY-MM-DD.json   — list of full LabeledScenario dicts for that date
```

---

## CLI

```bash
# Seed: run full auto-labeling pipeline
python scripts/run_llm_arena.py seed

# Merge into existing dataset (don't overwrite)
python scripts/run_llm_arena.py seed --merge

# Stats
python scripts/run_llm_arena.py stats
python scripts/run_llm_arena.py stats --json

# List with filters
python scripts/run_llm_arena.py list
python scripts/run_llm_arena.py list --outcome runner --catalyst fda
python scripts/run_llm_arena.py list --traded true --min-gap 0.5 --limit 20

# Show one scenario
python scripts/run_llm_arena.py show BRLS_2026-02-10
python scripts/run_llm_arena.py show BRLS_2026-02-10 --agent-view

# Validate integrity
python scripts/run_llm_arena.py validate
python scripts/run_llm_arena.py validate --quiet   # errors only
```

All commands accept `--data-dir PATH` to point at a non-default dataset directory.

---

## Python API

```python
from src.llm_arena import AutoLabeler, DatasetManager, CatalystType, StockOutcome

# Seed
labeler = AutoLabeler("data/")
scenarios = labeler.run_full_pipeline()

mgr = DatasetManager("data/llm_arena/")
for s in scenarios:
    mgr.add_scenario(s)
mgr.save()

# Query
mgr.load()
runners = mgr.filter(outcome=StockOutcome.RUNNER, catalyst_type=CatalystType.FDA)
train, test = mgr.split(test_pct=0.20, seed=42)
stats = mgr.stats()

# Export for agent evaluation (ground truth stripped)
agent_input = mgr.export_for_agent("BRLS_2026-02-10")
```

---

## Evaluation Use Case

The dataset supports blind agent evaluation:

1. `export_for_agent(scenario_id)` returns only premarket inputs (no labels).
2. Feed to the agent under test → collect its signal and confidence.
3. Compare `agent_signal` to `scenario.correct_signal`.
4. Score: signal matches + confidence in `[correct_confidence_min, correct_confidence_max]` = correct.

This enables walk-forward evaluation of prompt variants, model swaps, and architecture changes against a fixed ground-truth set.
