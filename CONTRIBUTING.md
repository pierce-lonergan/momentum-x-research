# Contributing to Momentum-X

Thank you for your interest in contributing! This guide will help you get started.

## Quick Start

```bash
# 1. Fork and clone
git clone https://github.com/YOUR_USERNAME/momentum-x.git
cd momentum-x

# 2. Install dev dependencies
pip install -e ".[dev]"

# 3. Run tests (no API keys needed!)
make test

# 4. Create a branch
git checkout -b feature/your-feature-name
```

## Development Workflow

### The TOR-P Protocol

This project follows the **TITAN-OMNE Recursive Protocol (TOR-P)**. Every code change must:

1. **Have a graph node** — Update `docs/memory/graph_state.json` with new modules
2. **Be research-justified** — Link to papers in `docs/research/` or decisions in `docs/decisions/`
3. **Be test-first** — Write failing tests before implementation
4. **Update the black box** — `docs/memory/black_box.json` tracks session state

Don't worry about being perfect with the protocol — maintainers will help you align contributions.

### Running Tests

```bash
make test          # Full test suite (~245 fast tests, ~30s)
make test-fast     # Skip property tests (faster)
make lint          # Check code quality
make format        # Auto-format code

# Discovery infrastructure (six layers — all gated on every commit):
python scripts/preflight.py                                 # 6-check morning health
python scripts/audit_d_codes.py                             # D-code registry orphan check
HYP_MAX_EXAMPLES=200 python -m pytest tests/property/ -q   # PBT fast variant
HYP_MAX_EXAMPLES=10000 python -m pytest tests/property/test_bridge_state_machine.py -q --hypothesis-seed=0  # nightly deep
```

### Discovery Infrastructure Discipline (since Saturday 2026-04-26 sprint)

The project ships **six discovery infrastructure layers** (see [`docs/research-log/40_discovery_infrastructure_summary.md`](docs/research-log/40_discovery_infrastructure_summary.md) for the definitive overview). Every patch is automatically gated on every commit by:

| Layer | Tag | What it catches |
|-------|-----|-----------------|
| Static analysis | `v2.2-static-analysis-active` | frozen-mutate, async-leak, silent-handler, relative-path, ps-datetime-utc, ps-pipe-deadlock, D-code orphans, **pyright reportPossiblyUnboundVariable (Bug AF + AG class)** |
| PBT state machine | `v2.2-pbt-phase2-active` | 17 invariants × 11 rules; Bug D/V/W/Z classes + generalizations |
| Phase 0 instrumentation | `v2.2-phase-0-active` | per-trade microstructure capture; D261 schema validation |
| Differential testing | `v2.2-difftest-foundation` | Bug-N-class regressions (silent call-site changes) |
| BOCPD changepoint | `v2.2-bocpd-armed` | regime breaks; D223 BOCPD_BREAK + D224 KELLY_HALVED |
| Bayesian (η, γ) estimator | `v2.2-bayesian-estimator-armed` | capacity calibration drift; D256-D259 four-trigger gates |

**Discovery rate operational guarantee:** **27 bugs surfaced + fixed / 0 patches introduced regressions = ∞.** Maintained at the operational level via the conventions below. **NINE bugs surfaced + shipped on Mon 2026-04-27** (highest single-day rate): AG, AH, AI, AJ, AK, AL, AM, AN (the 8 production-defect ships), and **AO (decision-replay infrastructure — Tier 4 #15 closes the "rigorous answer to does this make money" architectural gap; validates Bug AK's +47% prediction against today's actual log → measured 48.6%)**. All nine live-surfaced via operator log-reading + strategic assessment. See `docs/research-log/44_bug_ag_*` through `docs/research-log/54_bug_ao_*`.

#### Test-first protocol

For every shipped bug:

1. **Behavioral injection test** — proves the invariant catches the state directly (`tests/property/test_invariant_injection.py`).
2. **Search canary** when the bug class maps to a rule mutation — proves the PBT search reaches the state (`tests/property/test_mutation_canaries.py`).
3. **Static-analysis rule** when the bug is AST-detectable (`tests/static_analysis/test_*.py`).
4. **Finding doc** in `docs/research-log/<seq>_<short-name>.md` — root cause, reproduction, fix, downstream effects.

#### Bug-letter alphabet

Bug numbering continues alphabetically. Current alphabet position: **AO** (last shipped). The next surface is **AP**.

- A through Z = production-code bugs
- AA through AE = SimpleBroker oracle bugs (PBT-surfaced)
- AF onward = production-code bugs + architectural infrastructure surfaced post-sprint (AF caught by integration test; AG → AN caught **live** in Monday 2026-04-27 session log; AO is the Tier 4 decision-replay infrastructure — 9 ships in one trading day)

When you ship a new bug, claim the next letter in `docs/research-log/26_d_code_registry.md` and write the finding doc.

#### D-code reservation discipline

Every D-code referenced in production code or tests must be **reserved in `docs/research-log/26_d_code_registry.md`** before the patch lands. The registry's regression gate (`tests/static_analysis/test_d_code_audit.py`) ensures **0 orphans** — codes reserved but never wired surface as test failures.

To reserve a code:

1. Add a row to the appropriate section of `26_d_code_registry.md`.
2. Update the "Next available code" sentinel.
3. Reference the code in your patch (production source or test).
4. Commit registry update + patch in the same commit.

#### Finding doc convention

Every shipped bug gets `docs/research-log/<seq>_<short-name>.md` with these sections:

- §0 TL;DR (one-paragraph summary)
- §1 Root cause (file path + line numbers + code reference)
- §2 Why production allows the trigger condition (the real-world manifestation)
- §3 How the test caught it (the specific test + assertion)
- §4 Fix (the minimal patch, in-line)
- §5 How this would have manifested in production (worst-case operational impact)
- §6 Discovery rate impact (X/0 → Y/0)
- §7 Downstream effects
- §8 The discipline this preserves

See [`43_bug_af_uncond_cand_reference.md`](docs/research-log/43_bug_af_uncond_cand_reference.md) for a worked example.

### One-command setup for new contributors

```bash
python scripts/install_hooks.py    # Installs pre-commit hooks; verifies the discovery gate
python scripts/preflight.py        # Verifies the local dev environment is green
```

### Code Style

- **Python 3.11+** with full type hints
- **Pydantic v2** for all data models
- **ruff** for formatting and linting
- **Docstrings** with architectural context (see existing code for examples)

## What to Contribute

### Good First Issues

Look for issues labeled `good-first-issue` on GitHub. Common areas:

- **Tests** — Adding test coverage for edge cases
- **Documentation** — Improving docstrings, README, or research docs
- **Agent improvements** — Better prompts for existing agents
- **Bug fixes** — Anything in the issue tracker

### Architecture Areas

| Area | Difficulty | Description |
|------|-----------|-------------|
| `src/agents/` | Medium | LLM agent prompts and parsing |
| `src/data/` | Medium | Data clients and WebSocket streaming |
| `src/core/` | Hard | Orchestrator, scoring, backtester |
| `src/execution/` | Hard | Order execution and position management |
| `docs/research/` | Easy | Paper summaries and references |

### Adding a New Agent

1. Create `src/agents/your_agent.py` inheriting from `BaseAgent`
2. Define `agent_id`, `system_prompt`, `build_user_prompt()`, `parse_response()`
3. Add invariant enforcement in `parse_response()`
4. Register in `src/core/orchestrator.py`
5. Add weight in `config/settings.py` `ScoringWeights`
6. Write tests in `tests/unit/test_your_agent.py`

## Pull Request Process

1. **Branch** from `develop` (engineering work) or `main` (release-only)
2. **Write tests first** per the §"Test-first protocol" above
3. **Run `make test && make lint`** locally — must pass
4. **Run `python scripts/preflight.py`** — must show ALL GREEN
5. **Update docs** if you changed behavior; write a finding doc if you fixed a bug
6. **Open PR** with a clear description of what and why
7. **CI gate** must pass — `discovery_infrastructure` job runs all 6 layers' fast variants on every PR (see `.github/workflows/ci.yml`). Per-PR replay activates once Phase 0 captures real session data.
8. **One approval** required from a maintainer

## Architecture Decision Records (ADRs)

If your change involves a trade-off (e.g., choosing one library over another), create an ADR:

```
docs/decisions/ADR_XXX_YOUR_DECISION.md
```

See existing ADRs in `docs/decisions/` for the format.

## Code of Conduct

Be kind. Be helpful. Assume good intent. We're all here to build something great.

## Questions?

Open an issue with the `question` label or start a discussion on GitHub Discussions.
