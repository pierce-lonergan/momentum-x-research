# Runtime Agent Inventory — what's LLM-backed in production (D221 Phase F)

**Generated:** 2026-04-19 (Sunday, during the Phase F bug sweep)
**Purpose:** Single referenceable artifact for Tuesday's architecture decisions
and for every future "is this agent still using the LLM?" question. Historically
this lived as oral tradition; today's surprise ("technical_agent was already
deterministic") demonstrated the cost of that.

## Live orchestrator instantiations

Source: `src/core/orchestrator.py:217-237`.

| slot | class instantiated | LLM path? | ensemble? | notes |
|---|---|---|---|---|
| `_risk_agent` | **DeterministicRiskAgent** | NO | no | Since D101 (commit `2b11f53`, months ago). Ruleset + spread/dilution/bankruptcy checks. Never fails invariant. |
| `_technical_agent` | **DeterministicTechnicalAgent** | NO | no | Since D101. pandas_ta indicators + rule-based pattern detection. Never fails invariant. |
| `_news_agent` | `NewsAgent` (+`EnsembleWrapper`) | YES | yes (n_calls per T2 settings) | D221 Phase F: emits 12-dim `news_features` vector alongside legacy verdict. |
| `_fundamental_agent` | `FundamentalAgent` (+`EnsembleWrapper`) | YES | yes | Unconverted. Target of Monday afternoon distillation. |
| `_institutional_agent` | `InstitutionalAgent` (+`EnsembleWrapper`) | YES | yes | Unconverted. Deep dive. |
| `_deep_search_agent` | `DeepSearchAgent` (+`EnsembleWrapper`) | YES | yes | Unconverted. |

## Other agent-like components

| component | file | LLM path? | runs when? |
|---|---|---|---|
| `ManipulationClassifier` | `src/agents/manipulation_classifier.py` | NO | Rare (71/13,053 agent-records on disk); gate-time heuristic. |
| `DebateEngine` | `src/agents/debate_engine.py` | YES | Only when composite > `debate_threshold` (currently disabled since D100: `max_debate_attempts=0`). |

## Features-distilled status (D221 Phase F)

Pattern (a) from the v2 thesis — "agents distill to dense feature vectors,
meta-model decides." Current status:

| agent | verdict still emitted? | dense features emitted? | consumers migrated? |
|---|---|---|---|
| news_agent | yes (shared base class) | **YES** (12-dim `news_features`) | orchestrator.py:1260, faller_detection.py:704+723 |
| fundamental_agent | yes | no | target for Monday PM |
| institutional_agent | yes | no | next |
| deep_search_agent | yes | no | next |
| technical_agent | yes (deterministic-computed) | n/a (full result set is already structured) | n/a |
| risk_agent | yes (deterministic-computed) | n/a (full breakdown is already structured) | n/a |

## What this inventory DOES NOT cover (and why)

- **Per-agent LLM cost/latency profile.** That's a separate investigation
  surface; needs production telemetry from the journals to be meaningful.
- **Which consumers still depend on `signal` vs `news_features`.** Partially
  inventoried in the D221 Phase F news_agent commit; a full cross-agent pass
  will land as part of fundamental_agent's distillation.
- **EnsembleWrapper behavior deltas.** The EnsembleWrapper (n-call majority
  vote) sits between every LLM agent and its output; worth its own audit
  when distillation scope expands past news_agent.

## When to update this doc

Re-run the `grep -nE "self\._.*_agent\s*=" src/core/orchestrator.py` and the
per-class LLM-backed check any time an agent is added, removed, or switched
between LLM and deterministic. The orchestrator lines 217-237 are the
single source of truth.
