# Sunday Bug Sweep — D221 Phase F

**Date:** 2026-04-19 (Sunday evening, post-B-distillation ship)
**Scope:** Targeted adversarial testing of tonight's three commits
(A: dead-code cleanup, C: experiment scripts, B: news_agent distillation),
plus three Monday-morning audit questions answered tonight rather than
deferred.
**Outcome:** 9 silent-failure bugs found and fixed in deterministic code.
2 separate strategic findings that reshape the v2 experiment scope.

## Why this sweep happened

A's rewrite of `test_risk_agent.py` caught 2 silent-failure bugs that
had been in production for months. That outcome was evidence -- not an
isolated finding -- that deterministic agents ship with silent failure
modes that only surface under rigorous testing. Sunday night was the
window to check whether tonight's three commits introduced new instances
of the same class.

The directive: "Better to lose a Sunday afternoon than ship a wash."
Following that, the sweep ran for ~3 hours on the four areas at highest
risk for silent failure: the news_agent distillation, the migrated
caller paths, and the two deterministic agents (technical + risk) that
A had not yet audited adversarially.

## Bugs found and fixed (all shipped tonight)

| # | Module | Bug | How discovered | Severity |
|---|---|---|---|---|
| 1 | `news_agent.compute_news_features` | `float(sentiment_score)` crashes on string-typed bad inputs ("not a number") | adversarial test | Medium -- LLM could emit non-numeric sentiment if hallucinated |
| 2 | `orchestrator.py:1260` migration | Empty `news_features={}` (degraded path) silently treated BULL signals as not-bullish; old code returned True | adversarial test | High -- D91 degraded-mode gate behavior changed without intent |
| 3 | `DeterministicRiskAgent` | `float_shares="3.7M"` (string) crashes downstream `if float_shares > 100_000_000` | bug-sweep test | High -- LLM upstream can produce string-formatted floats |
| 4 | `DeterministicRiskAgent` | filing.form as dict/list/int crashes `.upper()` | bug-sweep test | Medium -- malformed filing JSON could trigger |
| 5 | `DeterministicRiskAgent` | filing.description as non-string crashes `.lower()` and `[:100]` | bug-sweep test | Medium |
| 6 | `DeterministicRiskAgent` | `candidate_signals=None` (explicit None bypasses kwargs default) crashes iteration | bug-sweep test | High -- common upstream pattern |
| 7 | `DeterministicRiskAgent` | `candidate_signals=[{...}, ...]` (dicts vs AgentSignal) crashes `s.signal` attribute access | bug-sweep test | Medium -- partial-deserialization path |
| 8 | `DeterministicTechnicalAgent` | `price_data=None` (explicit) crashes `tf in price_data` | bug-sweep test | High -- same kwargs-default-bypass pattern |
| 9 | `DeterministicTechnicalAgent` | `indicators=[...]` (wrong type) crashes `.items()` | bug-sweep test | Medium |

Plus the earlier 2 bugs caught during A (already shipped in `44aa39d`):
- `float(market_data.get('X') or 0)` crash on string inputs
- Type guard validated outer `sec_filings` dict but not inner list

**Grand total weekend bug count in deterministic code: 11.**

All 11 were silent-failure bugs that would have manifested only under
degraded upstream data conditions -- precisely when the "Never fails"
invariant matters most. The class of bug is uniform: **`kwargs.get('x',
default)` patterns where the default applies only on missing keys, not
on explicit None or wrong-type values from upstream.** The fix pattern
is uniform too: explicit `isinstance` guards or `_safe_float`/`_safe_int`
coercion at every external-input boundary.

## Strategic findings (not bugs, but reshape v2 work)

### Finding A: `MOMENTUM_PERSIST_LIVE_FEATURES` was not set in `.env`

Path B (forward-only persistence for the four LIVE-ONLY feature modules)
shipped in commit `dc3eac9` on Saturday afternoon. The persistence layer
is gated behind the `MOMENTUM_PERSIST_LIVE_FEATURES` environment variable,
which was never added to `.env`. The persistence has been **shipped but
dormant for 36+ hours** -- two days of forward-accumulating training data
for v3 lost.

**Resolved tonight:** appended `MOMENTUM_PERSIST_LIVE_FEATURES=1` to
`.env` with a comment block explaining purpose and the kill-switch
semantics. Next live trading session via `daily_paper_trade.ps1`
activates persistence. v3 starts accumulating immediately.

The lesson: shipping a code toggle is not the same as shipping the
configuration that activates it. The Path B commit should have included
the `.env` change in the same commit OR explicitly noted the activation
step in the commit body. Future shipping discipline: configuration
activation co-ships with code.

### Finding B: Phantom journal has 0 full records, reframes experiment #2

The v2 deep dive (Saturday) assumed `data/phantom/` contained ~10x the
training rows for inverse-propensity-weighted (IPW) retraining. Empirical
check: **1 phantom file, 0 full `record()` rows, 1 `update_gate()` row.**
The `record()` method is only called when `verdict.action in ("BUY",
"STRONG_BUY")` (main.py:2589); the system has been in zero-trade mode all
week, so 0 records exist.

**Reframing experiment #2 (phantom IPW):** the right substrate is
`data/journals/journal_*.jsonl` (2,400 candidate evaluations including
NO_TRADE rejections) -- which IS the data needed for propensity-weighted
training. The phantom journal would be the right substrate IF the system
were producing trades and getting them blocked; it isn't, so the
journals are the actual data layer.

This is a meaningful scope correction for Tuesday's architecture
discussion. Previously: "phantom IPW expands training distribution 10x."
Now: "journal-based IPW gives propensity-weighted training across the
2,400 evaluated candidates, including the ~5/6 of arena-rejected ones."
Different claim, same diagnostic value.

## What this sweep did NOT cover (intentional)

- **Full regression sweep across the codebase.** Out of scope per the
  directive ("ceremony at 11 PM Sunday"). The 64 pre-existing
  test-suite failures from this morning are unchanged and not regressions
  of tonight's commits.
- **Adversarial sweep of the LLM-backed agents (news/fundamental/
  institutional/deep_search).** Their LLM call surface is by definition
  unpredictable; defensive coercion belongs in the parser layer, which
  for news_agent is now done. The other three are deferred to their own
  distillation work.
- **Synthetic LLM-output fuzzing.** Would catch more bugs but is its
  own multi-day project. The structured-bug class identified tonight
  (kwargs default bypass) is the highest-leverage one to fix first.

## How to re-run this sweep

The bug-sweep tests live under their respective test files with
`TestDeterministicRiskBugSweep` / `TestDeterministicTechnicalBugSweep`
classes. Run:

```bash
python -m pytest tests/unit/test_risk_agent.py::TestDeterministicRiskBugSweep \
                  tests/unit/test_technical_agent.py::TestDeterministicTechnicalBugSweep \
                  tests/unit/test_news_distillation_adversarial.py -v
```

Plus the parity validation:

```bash
python scripts/parity_news_features.py
```

If any test fails, the corresponding migration or coercion is broken.
If parity reports mismatches, a downstream caller diverges from prior
behavior.

## Posture note for future-Pierce

Tonight surfaced 9 bugs in 3 hours of targeted testing. That's a
3-bug-per-hour discovery rate, all silent-failure surfaces in
deterministic code that's been live for months. The class of bug is
narrow (kwargs defaults bypass + missing isinstance guards on
LLM-derived inputs); the discovery method is mechanical (write a test
that passes None, a string, or a wrong-type dict for every kwarg and
every nested field).

**Apply the same lens to fundamental_agent, institutional_agent, and
deep_search_agent during their respective distillation work.** The
parser layer of every LLM-backed agent has the same structural risk.
Each agent's distillation is also an opportunity to harden it.

Recommended discipline going forward: when shipping any change to
deterministic agent code OR any new agent parser, the test file should
include a `Test*BugSweep` class with at minimum these cases:

1. Required kwarg = None (explicit, bypasses default)
2. Required kwarg = wrong type (string / dict / list as appropriate)
3. Nested dict field = non-string where string operations follow
4. Iterable kwarg = single-item iterable with malformed entry
5. Numeric kwarg = string representation ("3.7M", "high", etc.)

Five tests per agent. ~20 minutes per agent to write. Catches the class
of bug. Worth the time.

---

## Addendum: Mon 2026-04-20 — silent-activation failures (3 in 48 hrs)

Three failures in 48 hours of the **same class** as the deterministic-agent
bugs above, but at the configuration/activation layer rather than the code
layer:

1. **Path B persistence dormant 36 hours.** Shipped Saturday afternoon
   gated on `MOMENTUM_PERSIST_LIVE_FEATURES`. The env var was added to
   the repo `.env` only. No live trading session ran with it set until
   Monday morning. Caught by Sunday-night audit, but the discovery
   pattern was "operator notices and asks," not "system reports."

2. **Tier 3 Decision Learning channel dormant from Sunday-night ship to
   Monday pre-market.** Same root cause as #1. Same env-var-only
   activation that failed to survive operational lifecycle.

3. **Both env vars wiped at Monday 04:30** by
   `daily_paper_trade.ps1:128-130` doing `Copy-Item -Path $SecretsFile
   -Destination .env -Force`. The actual activation site is the user's
   secrets file at `~/momentum-x-secrets.env`, which gets copied OVER
   the repo `.env` at every session start. Editing `.env` directly is
   ephemeral by design.

The hardened rule from this addendum: **every env toggle ships with**

  (a) the code that reads it (with safe default behavior on UNSET),
  (b) the canonical activation site (the file from which secrets are
      loaded — for this codebase, `~/momentum-x-secrets.env` not
      repo `.env`),
  (c) a session-startup verification log that enumerates every tracked
      env var with status (UNSET/EMPTY/WHITESPACE/SET) and a length hint,
  (d) embedding into the heartbeat file so monitoring can read activation
      state without parsing logs,
  (e) a test that would fail if any of (a)-(d) were broken.

The (c) and (d) pieces are now implemented in `src/utils/env_audit.py`
and called from `main.py` post-dotenv-load + heartbeat write. 24 unit
tests + integration. Generic — every future env toggle just adds an
`EnvVarSpec` to the registry and gets the audit + heartbeat embedding
for free.

### Adjacent operational discipline (from the same audit cycle)

**Credential-file scanning.** While auditing the secrets file, an
`ALPACA_API_KEY=...` value got echoed via `grep -n` (line numbers + content).
Paper-trading key, low real risk, but the discipline matters. Habit
update: when reading or grepping any file matching `secrets`, `.env`,
`credentials`, `*-key*`, default to `grep -c` (count only). Use `-n`
or content output ONLY when the value itself is required for the audit,
and then redact via Python rather than shell.

**Multi-source process-state detection.** A `ps`/`tasklist` query for
the live trading session returned empty (Windows process-listing quirks
hid the CommandLine), leading to a false "trading session not running"
conclusion. Lock file + heartbeat + scheduled-task state were the
correct ground truth. Habit update: **when checking process state, use
at least two independent sources and require agreement before
concluding state.** Heartbeat staleness + lock file + Get-Process. Any
one says "alive" → alive. Only unanimous silence → dead.

### What the env_audit module surfaces

A representative startup log line series:

```
ENV_AUDIT[CRITICAL] ALPACA_API_KEY=<SET>:32  (Alpaca brokerage API key)
ENV_AUDIT[CRITICAL] FINNHUB_API_KEY=<SET>:19  (Finnhub API key)
ENV_AUDIT[FEATURE_GATED] MOMENTUM_PERSIST_LIVE_FEATURES=<SET>:1  (Path B persistence)
ENV_AUDIT[FEATURE_GATED] DECISION_LEARNING_WEBHOOK_URL=<SET>:124  (Tier 3 Discord)
ENV_AUDIT[SAFETY_KILL_SWITCHES] SHADOW_SCORING_ENABLED=<UNSET>:0  (default ON)
ENV_AUDIT summary: 14 vars audited, all CRITICAL set
```

Categories let an operator distinguish "scream-worthy UNSET" (CRITICAL)
from "feature dormant" (FEATURE_GATED) from "safe default in effect"
(SAFETY_KILL_SWITCHES). Length hints catch the class of bug where a
var is set to a truncated paste or whitespace.

The `n_critical_unset` counter in the heartbeat file is what
monitoring/dashboards should read — single integer, easy to alert on.

### NOT yet shipped (deliberately deferred)

- **Preflight abort on CRITICAL UNSET.** The audit logs WARNING lines
  but does not abort startup. That's a behavior change with its own
  failure modes (e.g., aborting because a TOGETHER_AI_API_KEY rotation
  is mid-flight). Ships in its own commit with explicit testing.
- **Watchdog alert on missing audit line.** "Session started but no
  ENV_AUDIT line in first 10 seconds" → something wrong with dotenv
  loading. Worth shipping but needs the watchdog-monitor.ps1 surface.
