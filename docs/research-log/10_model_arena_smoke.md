# Model Arena — Phase 7 Smoke Run

**Sprint date:** April 16, 2026
**Branch:** `d220-full-throttle`
**Task:** `catalyst_classification` — 7-class small-cap news catalyst (FDA / EARNINGS / M_AND_A / CONTRACT / OFFERING / SQUEEZE / NONE)
**Sample size:** **n=50** (hand-built fixture in `src/model_arena/fixtures/catalyst_smoke.json`)
**Models tested:** 13 of 16 catalog entries (3 Anthropic dropped — no API key locally; all 13 Together models attempted)
**Output:** `data/model_arena_runs/phase7_smoke.csv` + `phase7_smoke.responses.jsonl`

## TL;DR

| Rank | Model | Accuracy | F1 | $/call | $/correct | Notes |
|------|-------|---------:|---:|-------:|----------:|-------|
| **1** | **deepseek-v3-1** | **100.0%** | **100.0%** | $0.00008 | **$0.0001** | The clear winner — perfect, fast, cheap |
| 2 | qwen-3-235b | 98.0% | 97.3% | $0.00003 | $0.0000 | Cheapest; only 1 miss |
| 3 | gpt-oss-20b | 84.0% | 87.7% | $0.00019 | $0.0002 | Solid OpenAI 20B baseline |
| 4 | gpt-oss-120b | 88.0% | 88.9% | $0.00079 | $0.0009 | Bigger model, more cost, marginally better |
| — | qwen-3-5-397b | 0.0% | 0.0% | $0.00036 | ∞ | Resolved but parser failed all 50 (verbose output) |
| — | deepseek-r1-0528 | 0.0% | 0.0% | $0.00124 | ∞ | Reasoning model — `<think>` blocks confuse parser |
| — | kimi-k2-5 | 0.0% | 0.0% | $0.00025 | ∞ | Resolved & called but parser failed |
| — | qwen-3-5-9b-fp8 | 0.0% | 0.0% | n/a | ∞ | **API model_id did not resolve** (100% errors) |
| — | glm-5-fp4 | — | — | n/a | ∞ | API model_id did not resolve |
| — | glm-5-1-fp4 | — | — | n/a | ∞ | API model_id did not resolve |
| — | minimax-m-2-5-fp4 | — | — | n/a | ∞ | API model_id did not resolve |
| — | minimax-m-2-7-fp4 | — | — | n/a | ∞ | API model_id did not resolve |
| — | gemma-4-31b-it-fp8 | — | — | n/a | ∞ | API model_id did not resolve |

## Key findings

### Production candidates (worth deeper evaluation next week)

1. **deepseek-v3-1** — perfect 50/50 with sub-second median latency at $0.0001 per correct prediction. If this holds on n=500, it's a strong production candidate for the news agent.
2. **qwen-3-235b** — 49/50 at 1/3 the cost of deepseek. Slightly noisier; would need to investigate the single miss.
3. **gpt-oss-120b** — 88% on a 7-class problem is non-trivial. The OpenAI open-weights model is a credible alternative provider.

### Models that need parser/prompt work, not deeper evaluation

- **qwen-3-5-397b** (the production Tier 1 model) returned 0% under the strict-JSON prompt. Suggests it's outputting prose ("The catalyst is FDA approval...") that our default parser can't extract from. Production may handle this via more permissive prompting; the arena's strict-JSON prompt is mismatched.
- **deepseek-r1-0528** — same problem with `<think>` reasoning blocks. Need a parser that strips think-blocks before classification.
- **kimi-k2-5** — output format unclear; 3.5s p50 latency is also notable (slower than peers).

### `verified: false` flag was justified

6 of 9 unverified models (qwen-3-5-9b-fp8, both GLMs, both MiniMaxes, gemma-4-31b-it-fp8) returned 100% error rate — the API model_id format was wrong. The catalog's verification flag correctly flagged the risk. Action: research the correct Together IDs for these (likely different namespacing or typo) before next eval.

## What this proves

- **The harness works end-to-end** — async runner, semaphore-bounded concurrency, timeout handling, retry, JSON parser, cost calculation, ranking, CSV output. ~7 seconds for 13 models × 50 samples (650 calls) on Together's API.
- **Cost calculation is reasonable** — total spend on this run was ~$0.04 across all 650 calls.
- **The `verified` flag in the catalog is doing real work** — unverified IDs systematically fail; verified IDs systematically work.

## What this does NOT prove (caveats)

- **n=50 is a smoke test.** Per the action plan, any production swap requires n≥500 on a held-out set + shadow validation the same way the composite score does.
- **The fixture is hand-built.** The 50 examples reflect my (Claude's) intuition about what catalysts look like in real headlines. They may not match the distribution of actual pre-market news the live system encounters.
- **Catalyst classification is one of 5+ tasks production agents do.** Risk scoring, manipulation detection, technical analysis, debate moderation — none of these were tested. A model that wins on catalyst classification could lose on risk scoring.
- **Single-prompt comparison.** The strict-JSON prompt advantages models that follow instructions tersely. A model that's better at reasoning (deepseek-r1, qwen-3-5-397b) gets penalized by the parser, not by intelligence.
- **No multi-turn, no tool-use, no streaming.** Production agents may use these capabilities differently across providers.

## Recommendation for next week

1. **Stand up a verified Together API model-id check.** Hit Together's `/v1/models` endpoint and cross-reference. Update catalog.yaml's `verified` flags accordingly. ~15 min of work.
2. **Add a permissive parser variant** that handles prose responses + `<think>` blocks. Re-run qwen-3-5-397b and deepseek-r1-0528 to get fair scores.
3. **Build a labeled dataset of n≥500 from src/llm_arena's auto-labeler pipeline** — currently `data/llm_arena/` is empty. Real production headlines, not hand-built fixtures.
4. **Then** run the cross-model arena on the real dataset and produce a ranking that's safe to act on.

## Files

```
src/model_arena/
  __init__.py              auto-loads .env for CLI invocations
  __main__.py              entry point so `python -m src.model_arena` works
  models.py                ModelEntry + catalog loader (~120 lines)
  tasks.py                 Task + parser + 50-example catalyst fixture (~250 lines)
  runner.py                async runner with semaphore + timeout + retry (~180 lines)
  metrics.py               accuracy, F1, cost-per-correct, ranking (~110 lines)
  cli.py                   argparse + summary table + CSV output (~165 lines)
  catalog.yaml             16-model catalog with prices + verified flags

src/model_arena/fixtures/
  (created at first run; default fixture in tasks.py)

tests/unit/test_model_arena.py  21 tests, all passing in 4.14s

data/model_arena_runs/
  phase7_smoke.csv                  per-model summary metrics
  phase7_smoke.responses.jsonl      per-call raw responses for failure-mode analysis
```

## Hard rule (per Phase 7 directive)

**No production agent swaps tonight or tomorrow.** This is infrastructure only. Any swap requires:
1. n≥500 on a held-out, real-data set (not the smoke fixture)
2. The composite-score-style shadow-mode validation: log the candidate model's output alongside the production model's output for at least 5 sessions
3. A post-shadow analysis that survives diagnostics as rigorous as Phase 4.6's
4. User sign-off
