# LLM Arena — Agent Harness (Component 2)

The Agent Harness is a sandboxed execution environment that runs individual LLM agents against labeled scenarios and captures their full output.

## What It Does

The harness feeds an agent the same inputs it would receive in production, captures the response (signal direction, confidence, reasoning, catalyst_type), and measures operational metrics (latency, token usage, timeout rate, parse success).

It supports two modes:

| Mode | API Calls | Cost | Speed | Use For |
|------|-----------|------|-------|---------|
| **Replay** | None | Free | Instant | Testing scoring/metrics pipeline |
| **Live** | Yes | Real cost | 1-25s/call | Comparing models (Component 3) |

---

## Replay Mode vs Live Mode

### Replay Mode (Priority)

Replay mode uses the `actual_agent_signals` already stored in each `LabeledScenario`. These come from the production pipeline journals and contain the real signal the agent produced on that day.

- **509 scenarios** available immediately
- **Zero API calls** — free and deterministic
- **Perfect** for testing the scoring/metrics pipeline end-to-end
- **Latency is reported as 0ms** (no real call happens)

### Live Mode (Component 3)

Live mode actually calls the LLM API with the scenario's inputs. This is needed for Experiment 1 (model comparison) where you want to test a *new* model against the same historical scenarios.

Live mode currently raises `NotImplementedError("Live mode will be implemented in Component 3")`.

---

## Input Preparation

The `prepare_input(scenario, agent_type)` method converts a `LabeledScenario` into exactly what the production agent's `build_user_prompt` method expects.

### Mapping by Agent Type

| Agent Type | Key Inputs Extracted |
|------------|---------------------|
| `news` / `news_agent` | ticker, premarket_headlines → news_items, dollar_volume as market_cap |
| `fundamental` / `fundamental_agent` | ticker, sec_filings → recent_filings (float/SI not in scenario) |
| `technical` / `technical_agent` | ticker, open_price, rvol (OHLCV bars not in LabeledScenario) |
| `manipulation` / `manipulation_classifier` | ticker, gap_pct, rvol, headlines, sec_filings, filing_summary |
| `risk` / `risk_agent` | ticker, gap_pct, rvol, open_price, sec_filings |
| `institutional` / `institutional_agent` | ticker, rvol (options/dark pool data not in scenario) |

Both short names (`"news"`) and full agent IDs (`"news_agent"`) are accepted.

---

## Result Schema (`AgentRunResult`)

```python
@dataclass
class AgentRunResult:
    # Identity
    scenario_id: str          # e.g. "BRLS_2026-02-10"
    agent_config: AgentConfig
    run_id: str               # UUID, unique per run

    # Agent output
    signal_direction: str | None    # "BULL", "BEAR", "NEUTRAL", "STRONG_BULL", "STRONG_BEAR"
    signal_confidence: float | None # 0.0–1.0
    reasoning: str | None
    catalyst_type: str | None       # News agent only, e.g. "FDA_APPROVAL"
    raw_output: dict | None         # Full original signal dict

    # Operational metrics
    latency_ms: float         # 0.0 in replay mode
    tokens_input: int         # 0 in replay mode
    tokens_output: int        # 0 in replay mode
    timed_out: bool
    parse_success: bool       # True if signal_direction was extracted
    error: str | None

    # Accuracy (filled by scorer, not harness)
    direction_correct: bool | None
    catalyst_correct: bool | None

    timestamp: datetime | None
```

---

## CLI Usage

```bash
# Replay all scenarios through all stored agents
python scripts/run_llm_arena.py replay

# Replay fader scenarios through the news agent only
python scripts/run_llm_arena.py replay --agent news --outcome fader

# Replay FDA catalyst scenarios and save results
python scripts/run_llm_arena.py replay --agent news --catalyst fda --save baseline_news_fda

# Check what was saved
python scripts/run_llm_arena.py stats
```

Saved results land in `data/llm_arena/results/{NAME}.json`.

---

## Python API

```python
from src.llm_arena import AgentConfig, AgentHarness
from src.llm_arena import DatasetManager

# Load dataset
mgr = DatasetManager("data/llm_arena")
mgr.load()
scenarios = mgr.all()

# Create harness
harness = AgentHarness("data/llm_arena")

# Replay all agents stored in every scenario
results = harness.run_replay(scenarios)

# Replay one specific agent type
cfg = AgentConfig(agent_type="news", model_id="mixtral-8x7b")
results = harness.run_batch(scenarios, cfg, mode="replay")

# Replay a single scenario
result = harness.run_single(scenarios[0], cfg, mode="replay")

# Save for later
harness.save_results("baseline_news")

# Load saved results
harness2 = AgentHarness("data/llm_arena")
results = harness2.load_results("baseline_news")
```

---

## Adding New Agent Types

1. Add an input preparer function in `harness.py`:
   ```python
   def _prepare_myagent_input(scenario: LabeledScenario) -> dict:
       return {
           "ticker": scenario.ticker,
           # ... map scenario fields to agent kwargs
       }
   ```

2. Register it in `_INPUT_PREPARERS`:
   ```python
   _INPUT_PREPARERS["my_agent"] = _prepare_myagent_input
   _INPUT_PREPARERS["myagent_id"] = _prepare_myagent_input
   ```

3. Add to `_AGENT_TYPE_TO_ID` if using a short name:
   ```python
   _AGENT_TYPE_TO_ID["myagent"] = "myagent_id"
   ```

4. Add `--agent myagent` to the CLI choices in `scripts/run_llm_arena.py`.

---

## Storage Format

Results are stored as JSON:

```json
{
  "experiment_name": "baseline_news",
  "saved_at": "2026-04-02T09:00:00+00:00",
  "scenario_count": 509,
  "result_count": 509,
  "results": [
    {
      "scenario_id": "BRLS_2026-02-10",
      "agent_config": { "agent_type": "news", "model_id": "mixtral-8x7b", ... },
      "run_id": "uuid-...",
      "signal_direction": "BULL",
      "signal_confidence": 0.72,
      "reasoning": "...",
      "catalyst_type": "FDA_APPROVAL",
      "latency_ms": 0.0,
      "parse_success": true,
      ...
    }
  ]
}
```

---

## Component Roadmap

| Component | Status | Description |
|-----------|--------|-------------|
| 1 — Labeled Dataset | Complete | 509 scenarios, models.py, dataset.py, auto_labeler.py |
| **2 — Agent Harness** | **Complete** | Replay + Live stub, input prep, result storage |
| 3 — Live Mode | Planned | LLM API calls, rate limiting, async batching |
| 4 — Scorer | Planned | direction_correct, catalyst_correct, Elo ratings |
| 5 — Experiment Runner | Planned | Model A vs B comparisons, statistical significance |
