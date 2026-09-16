# 16 — Silent-Fallback Retrospective Audit

Date: 2026-04-21 (Tue, pre-market, on the first session after the PreMarketCache fix landed at 07:18 ET)
Author: opus-4.7 (audit run)
Scope: `src/**/*.py`, `main.py`, `config/**/*.py`, `scripts/**/*.py`
Excludes: `tests/**`, `mx-arena/**`, `docs/**`, `data/**`, `logs/**`, `models/**`, `__pycache__`, `.venv`, `node_modules`, `.claude/`
Tool: AST-based scanner (`scripts/silent_fallback_audit.py` + `scripts/silent_fallback_triage.py`).
Files scanned: 202. Raw catches matched: 433.

---

## 1. Executive summary

We swept the production tree for the structural signature shared by the three recent "8-month dormancy" bugs (pandas_ta, D158 SEC dilution classifier, PreMarketCache.tickers): a `try/except` whose handler swallows the failure and returns an empty/default value with no observable effect on the rest of the system.

**433 silent-fallback catches** across 202 files. Distribution by class:

| class | meaning | count |
| --- | --- | --- |
| S1 | silent `return {}/[]/None/False/0/""` (no log) | 18 |
| S2 | `pass` / `continue` / `...` | 177 |
| S3 | log-only fall-through (logger then implicit `return None`) | 208 |
| S4 | log + return empty/default — the exact pandas_ta + PreMarketCache pattern | 30 |

After triage (hot/warm/cold path, narrow-vs-broad exception, presence of positive-case tests):

| severity | count |
| --- | --- |
| HIGH | **7** |
| MEDIUM | **20** |
| LOW | 406 |

The 7 HIGH findings (all `S4` on a HOT path catching broad `Exception`):

1. `src/agents/ensemble.py:158` — `EnsembleWrapper._safe_call`
2. `src/core/orchestrator.py:1981` — `Orchestrator._fetch_options_summary`
3. `src/core/scan_loop.py:167` — `ScanLoop._compute_live_gex`
4. `src/execution/archetype_exit.py:168` — `ArchetypeClassifier._fit_hdbscan`
5. `src/execution/archetype_exit.py:199` — `ArchetypeClassifier._fit_gmm`
6. `src/execution/exit_intelligence.py:82` — `SignalHistoryLogger._ensure_file`
7. `src/execution/session_state.py:302` — `SessionStateManager._load_backup`

**Punchline.** 13.4% of the production tree is "log-and-swallow" code (61 of 202 files contain at least one S3/S4 broad-`Exception` catch). The 3 known dormant bugs were not exotic — they are representative of a default coding style. A heartbeat-embedded catch counter is the only structural fix that scales.

---

## 2. Methodology

- Built `scripts/silent_fallback_audit.py` — walks every included `.py` file, parses with `ast`, finds every `ast.Try`, and inspects each `ExceptHandler.body` against four shape templates (S1–S4 in the brief). Handlers containing a `Raise` are skipped (re-raising is not silent).
- Triage in `scripts/silent_fallback_triage.py`:
  - **Hot/warm/cold** by file-path heuristic. HOT = called per-evaluation in the live trading loop (`src/scanners/`, `src/composite/`, `src/execution/`, `src/core/`, `src/data/feeds|intraday|realtime|quote|fundamentals|cache|pre_market|tickers|short_avail`, `src/agents/`). WARM = startup/EOD/scheduler-tick (`src/scheduling/`, `src/monitoring/`, `src/analysis/`, `src/utils/cache`, `main.py`, `config/`). COLD = research harnesses + `scripts/` + `src/{production,llm,model,selection}_arena/` + `src/backfill_agent/` + `src/experiments/`.
  - **Severity**: HOT × {S1, S4} = HIGH. WARM × {S1, S4} or HOT × {S2, S3} = MEDIUM. Else LOW.
  - **Drop one level** if the catch is narrow (`ValueError`, `TypeError`, `JSONDecodeError`, `OSError`, `ImportError`, `KeyError`, `AttributeError`, `statistics.StatisticsError`, `sqlite3.IntegrityError`) — these document the contract.
  - **Drop one level** if a positive-case test exists for the function name in `tests/`.
- The 3 known-fixed instances (pandas_ta, SEC dilution classifier, PreMarketCache) are filtered out of the table by name + log-text matching.

---

## 3. Findings table

Sorted HIGH → MEDIUM → LOW (LOW summarised, not enumerated).

### 3.1 HIGH severity (7)

| sev | file:line | function | class | catches | returns | hot/warm/cold | has positive test? | note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| HIGH | `src/agents/ensemble.py:158` | `EnsembleWrapper._safe_call` | S4 | `Exception` | `return None` | HOT | no | Per-call inside ensemble agent. Failure is observable downstream (D202_ENSEMBLE_ALL_FAILED) but a partial silent failure (1-of-3 calls dies silently) is not. |
| HIGH | `src/core/orchestrator.py:1981` | `Orchestrator._fetch_options_summary` | S4 | `Exception` | `return {}` | HOT | no | Per-candidate options chain pull for institutional agent. Provider being misconfigured looks identical to "no options activity". |
| HIGH | `src/core/scan_loop.py:167` | `ScanLoop._compute_live_gex` | S4 | `Exception` | `return None` | HOT | no | Per-candidate live GEX. Returning `None` quietly disables the GEX filter — exactly the failure mode of pandas_ta dormancy. |
| HIGH | `src/execution/archetype_exit.py:168` | `ArchetypeClassifier._fit_hdbscan` | S4 | `ImportError` + `Exception` | `return None` | HOT | no | Two adjacent handlers. The `ImportError` is the *literal* pandas_ta pattern. If `hdbscan` is absent, GMM fallback fires silently and changes the entire exit-classifier output distribution. |
| HIGH | `src/execution/archetype_exit.py:199` | `ArchetypeClassifier._fit_gmm` | S4 | `Exception` | `return None` | HOT | no | If GMM also fails, every position lands in the "fallback" archetype with default exit policy — silent, indistinguishable from "no candidates fit". |
| HIGH | `src/execution/exit_intelligence.py:82` | `SignalHistoryLogger._ensure_file` | S4 | `Exception` | `return False` | HOT | no | Called once per exit-cycle. If file open fails, every exit cycle produces 0 log rows for that day — D107 signal history quietly empty. |
| HIGH | `src/execution/session_state.py:302` | `SessionStateManager._load_backup` | S4 | `Exception` | `return None` | HOT | no | D108 backup-recovery path. If the primary state file corrupts AND backup recovery silently fails, we boot with empty state — every position becomes "phantom-orphaned" without any startup error. |

### 3.2 MEDIUM severity (20)

| sev | file:line | function | class | catches | returns | hot/warm | note |
| --- | --- | --- | --- | --- | --- | --- | --- |
| MEDIUM | `main.py:3672` | `_d164_early_profit_task` | S4 | `Exception` | implicit `None` | WARM | Async early-profit checker; failure here disables the early-profit policy silently. |
| MEDIUM | `main.py:6936` | `cmd_build_scenarios` | S4 | `Exception` | implicit `None` | WARM | EOD scenario builder. |
| MEDIUM | `main.py:7020` | `cmd_record_scenarios` | S4 | `Exception` | implicit `None` | WARM | Bar recorder. |
| MEDIUM | `src/agents/deterministic_risk.py:70` | `_safe_int` | S1 | `(ValueError, TypeError)` | `default` (var) | HOT | Narrow coercion, but silent (S1). Risk of silently returning the *wrong* default in deterministic risk model. |
| MEDIUM | `src/analysis/post_trade.py:362` | `PostTradeAnalyzer._find_opponent` | S1 | `Exception` | `return None` | WARM | Silent return — affects post-trade attribution. |
| MEDIUM | `src/core/orchestrator.py:341` | `Orchestrator.__init__` | S3 | `Exception` | fall-through | HOT | Constructor catches and continues — risks half-initialized orchestrator. |
| MEDIUM | `src/core/orchestrator.py:1787` | `Orchestrator._build_filing_summary` | S3 | `Exception` | fall-through | HOT | Used by fundamental agent; silent skip. |
| MEDIUM | `src/core/orchestrator.py:1824` | `Orchestrator._fetch_sec_filings` | S4 | `Exception` | `return {}` | HOT | The H-004 live SEC pipeline. Silent empty filings looks identical to "no filings exist". |
| MEDIUM | `src/core/orchestrator.py:1877` | `Orchestrator._fetch_vix` | S3 | `Exception` | fall-through | HOT | VIX fetch failure silently disables regime gate. |
| MEDIUM | `src/core/orchestrator.py:1930` | `Orchestrator._fetch_spy_return` | S3 | `Exception` | fall-through | HOT | SPY return failure silently disables SPY gate. |
| MEDIUM | `src/core/orchestrator.py:1948` | `Orchestrator._fetch_spy_return` (daily fallback path) | S3 | `Exception` | fall-through | HOT | Same risk; nested. |
| MEDIUM | `src/execution/alpaca_executor.py:294` | `AlpacaExecutor.execute` | S2 | `Exception` | `pass` | HOT | Inner counter bump (`orders_rejected.inc()`); failure suppresses metrics, not orders. |
| MEDIUM | `src/execution/alpaca_executor.py:308` | `AlpacaExecutor.execute` | S2 | `Exception` | `pass` | HOT | Same kind. |
| MEDIUM | `src/execution/archetype_exit.py:168` | `_fit_hdbscan` ImportError handler | S4 | `ImportError` | `return None` | HOT | Already counted above (the broad-Exception handler is HIGH). |
| MEDIUM | `src/execution/bridge.py:194` | `ExecutionBridge.execute_verdict` | S4 | `Exception` | `return None` | HOT | Logs at `error` level — observable, but trade is silently dropped after entry-pipeline succeeded. Worth a positive-case path test. |
| MEDIUM | `src/execution/exit_intelligence.py:70` | `SignalHistoryLogger.__init__` | S3 | `Exception` | fall-through | HOT | Mkdir failure silently leaves logger broken. |
| MEDIUM | `src/execution/fast_path.py:734` | `FastPathReconciler._cancel_entry` | S3 | `Exception` | fall-through | HOT | Cancel failure silently leaves an unwanted entry pending. |
| MEDIUM | `src/execution/session_state.py:79` | `PositionState._safe_int` | S1 | `(ValueError, TypeError)` | `default` (var) | HOT | Narrow but silent. |
| MEDIUM | `src/execution/session_state.py:275` | `SessionStateManager._save_inner` | S3 | `Exception` | fall-through | HOT | Save failure silently does nothing — state on disk lags reality. |
| MEDIUM | `src/execution/session_state.py:293` | `SessionStateManager._save_inner` | S2 | `Exception` | `pass` | HOT | Tmp-file cleanup failure — low blast radius but still silent. |

### 3.3 LOW severity (406)

By bucket:

| bucket | class | severity | count |
| --- | --- | --- | --- |
| scripts | S1 | LOW | 7 |
| scripts | S2 | LOW | 52 |
| scripts | S3 | LOW | 2 |
| scripts | S4 | LOW | 1 |
| src | S1 | LOW | 8 |
| src | S2 | LOW | 122 |
| src | S3 | LOW | 198 |
| src | S4 | LOW | 16 |

Full per-row LOW data is in `scripts/_silent_fallback_triaged.json`. The bulk of the LOW count is `main.py` `cmd_paper`/`cmd_scan` outer loops (intentional resilience) and research harnesses (production_arena, llm_arena, model_arena, selection_arena, backfill_agent, experiments) — all of which are CLI-driven and observable by exit code.

---

## 4. HIGH severity drill-down

### 4.1 `EnsembleWrapper._safe_call` — `src/agents/ensemble.py:158`

What dormancy looks like in production: ensemble launches N parallel calls, one fails silently and returns `None`. The aggregator only logs `D202_ENSEMBLE_ALL_FAILED` when *every* call fails. So a steady-state "1-of-3 always fails" condition (auth bug for one model, rate-limit on one provider) is invisible — Elo and quorum stats degrade but no alarm fires.

Rule-(e) test should assert: with a working agent stub, `_safe_call` returns a non-`None` `AgentSignal` whose `agent_id` matches. A second test should mock the agent to raise `RuntimeError` and assert the `logger.debug` is emitted with a `D202_ENSEMBLE` substring. Heartbeat counter justified — this is the highest-frequency call site in the system; a per-minute "_safe_call returned None" rate exposes the dormancy in <1 hour rather than weeks.

### 4.2 `Orchestrator._fetch_options_summary` — `src/core/orchestrator.py:1981`

Dormancy: the institutional agent's prompt is built from this dict. Empty dict → agent operates on a hollow context, but no upstream sees it. A misconfigured options provider, an SDK breaking change, or a Polygon outage all collapse to "agent silently runs with degraded inputs" — exactly how D158 dilution went dark for 8 months.

Rule-(e) test: with an `OptionsProvider` stub returning a chain object, assert `_fetch_options_summary("AAPL")` returns `{"has_options": True, ...}` with non-empty `chain_summary`. Heartbeat counter justified — emit a per-scan ratio of `non_empty_options / candidates_with_options_provider` and alert on regression below 0.95.

### 4.3 `ScanLoop._compute_live_gex` — `src/core/scan_loop.py:167`

Dormancy: this is the *exact* pandas_ta pattern moved to a different filter. If the GEX calc fails for a class of tickers (small caps, no options market), the GEX filter is silently bypassed for them. The scan logs "%d after GEX filter" but if GEX never fires, the count just equals input — a pattern that's invisible without a baseline.

Rule-(e) test: with an options provider stub returning a chain with non-zero gamma, assert `_compute_live_gex` returns a `float` in `(-100, 100)` (or whatever the normalized range is). Heartbeat counter justified — track per-scan `gex_returned_none_count`. If it climbs to 100% (the pandas_ta failure mode), page on it.

### 4.4 `ArchetypeClassifier._fit_hdbscan` (ImportError + Exception) — `src/execution/archetype_exit.py:168`

Dormancy: the `ImportError: hdbscan` branch is **structurally identical to the pandas_ta bug**. If `hdbscan` ever leaves `requirements.txt`, every clustering call falls back to GMM with zero startup signal — exit policy silently changes. The broader `Exception` branch hides numerical failures.

Rule-(e) test: with synthetic 2-component Gaussian-mixture data, assert `_fit_hdbscan` returns a `numpy.ndarray` of length `len(X)` with at least one cluster (i.e. labels != all-noise). Heartbeat counter justified at WARM frequency (per-fit) — emit `archetype_classifier.fallback_to_gmm_count` and page on first non-zero.

### 4.5 `ArchetypeClassifier._fit_gmm` — `src/execution/archetype_exit.py:199`

Dormancy: defence-in-depth fallback below `_fit_hdbscan`. If both fail, every position is "unknown archetype" and gets the default exit policy. Indistinguishable from "no positions". Risk vector: a sklearn version bump that changes a kwarg.

Rule-(e) test: same fixture as 4.4; assert `_fit_gmm` returns labels with `len(set(labels)) >= 2`. Heartbeat justified — same counter as 4.4.

### 4.6 `SignalHistoryLogger._ensure_file` — `src/execution/exit_intelligence.py:82`

Dormancy: open() failure (disk full, permission change, path moved) silently disables D107 signal history — every later `log_cycle` is a no-op. Today this is invisible until someone tries to read the JSONL and finds it empty.

Rule-(e) test: in a `tmp_path`, assert `_ensure_file()` returns `True` and that the daily JSONL file actually exists on disk. A separate test should mock `open` to raise `OSError` and assert `False`. Heartbeat counter — count successful `log_cycle` writes per session and emit at EOD; alarm on `0`.

### 4.7 `SessionStateManager._load_backup` — `src/execution/session_state.py:302`

Dormancy: this is the D108 last-line-of-defence. Primary state corrupt → load backup. If backup is *also* corrupt and the catch swallows, we boot with `None` state and the session manager creates a fresh empty state. Every position becomes "phantom-orphaned" relative to the broker without any startup error.

Rule-(e) test: write a known-good `state.bak` JSON to tmp; assert `_load_backup()` returns a `SessionState` with the expected `session_date`. A second test writes corrupt JSON and asserts `None` plus the warning log. Heartbeat counter — emit `session_state.backup_recovery_attempts` and `session_state.backup_recovery_failures` (1 each at startup max). Loud on any non-zero `failures`.

---

## 5. Recommended action plan

### Tue 2026-04-21 pre-market diagnostic — DONE, all clean

Per this section's recommendation, the read-only diagnostic was run
against the new session log (lines 10195+ — the post-restart window
starting 07:18:41 EDT). Result: **0 hits** across all 7 HIGH-finding
log signatures (`Live GEX computation`, `Options fetch.*failed`,
`D202.*ENSEMBLE`, `_fit_hdbscan`/`hdbscan.*ImportError`,
`_ensure_file.*failed`, `_load_backup.*failed`). The only WARNING in
the new session is the known-benign `websocket_client | Trade updates
disconnected` (auto-reconnects in <2s).

Conclusion: none of the 7 HIGH catches are *currently* firing in
production. The findings remain forward-looking risk (these *will*
fire when the underlying API/lib drifts) rather than "happening now."
No emergency code change before market open is justified.

The structural fix (HeartbeatCatchCounter) remains the correct
Tuesday architecture call deliverable. Original recommendation
preserved below for context.

> Original (pre-diagnostic) text:
> The live trading session is running. Do **not** restart it. The HIGH findings on the live hot path are:
> - `_compute_live_gex` (4.3)
> - `_fetch_options_summary` (4.2)
> - `_safe_call` (4.1)
> For tonight, add **read-only diagnostics**: tail the running log for any `logger.debug("Live GEX computation failed")`, `logger.debug("Options fetch for")`, `logger.debug("D202 ENSEMBLE")` lines and count occurrences. If these are firing at >5% rate, today's session is running with the pandas_ta-class dormancy *right now*. No code changes — just confirm the heartbeat reflects what you think it reflects.

### Tuesday architecture call

Design **HeartbeatCatchCounter** — a singleton that all silent fallbacks call into:

```python
HEARTBEAT_COUNTERS.bump("ensemble._safe_call.exception")
```

The heartbeat emit (every N seconds in `main.py`) prints non-zero counters with delta-since-last-emit. This converts every S2/S3/S4 catch from "invisible" to "visible at heartbeat granularity". Migration plan: tag each catch site with a stable string ID (one-line change), wire through the existing `metrics` module which already has `prometheus_client.Counter`. Avoids the per-handler test problem by making *all* catches observable.

Cost estimate: 1 day to design the API, 2 days to migrate the 30 S4 + 8 MEDIUM S3 sites. The 122+198 LOW S2/S3 sites can migrate opportunistically.

### Tuesday afternoon — ship rule-(e) tests for the top 7

In `tests/unit/`, add one new file per HIGH finding:

- `test_ensemble_safe_call_positive.py`
- `test_orchestrator_fetch_options_positive.py`
- `test_scan_loop_compute_live_gex_positive.py`
- `test_archetype_classifier_fit_positive.py`
- `test_signal_history_logger_ensure_file_positive.py`
- `test_session_state_load_backup_positive.py`

Each asserts non-empty output under nominal inputs (per the rule-(e) text in section 4 above). Estimate 30-60 minutes per test.

### Wednesday — MEDIUM tier rule-(e)

Bridge the 20 MEDIUM findings:

- 4 SEC/VIX/SPY/options orchestrator catches (`_fetch_sec_filings`, `_fetch_vix`, `_fetch_spy_return`, `_build_filing_summary`) — these silently disable regime/fundamentals gates. Same template as 4.2.
- `ExecutionBridge.execute_verdict` — assert that with a working executor stub, `execute_verdict` returns a non-`None` result and the broker stub was invoked.
- `SessionStateManager._save_inner` — assert that after `save()`, the state file on disk parses to the saved object.
- The two `alpaca_executor.execute` `pass` blocks — convert to `metrics.metrics_failure.inc()` (low blast radius but free observability).

### Backlog (LOW)

The 406 LOW catches do not need rule-(e) tests, but they should all participate in the heartbeat counter once it ships. Specifically, the 16 LOW S4 in `src/` and the 122 LOW S2 in `src/` are the cheap-to-tag pool that turns into "free dormancy detection" once the HeartbeatCatchCounter is live.

---

## 6. What we are NOT changing

These catches are appropriate by design and should remain silent. Document them in the code with a `# noqa: silent-catch (intentional)` comment so future audits don't re-flag them:

- **Narrow type-coercion utilities** catching `(ValueError, TypeError)`: `_safe_int`, `_safe_float`, `_safe_judge_float`, `_as_float_or_none`, `_as_bool_or_none`, etc. The catch IS the contract.
- **JSONL line-by-line readers** catching `json.JSONDecodeError`: `production_arena/scenarios.py:_read_jsonl`, `production_arena/scenarios.py:_load_bar_file`. Skipping a malformed line is correct; the surrounding loop logs each skip.
- **CLI-driven research harnesses**: every catch in `src/{production,llm,model,selection}_arena/`, `src/backfill_agent/`, `src/experiments/`, and `scripts/`. These are run interactively; failures show up immediately in the operator's terminal.
- **`main.py` outer-loop resilience** in `cmd_paper`, `cmd_scan`: dozens of `try / except Exception: logger.warning(...)` around the per-cycle loop. These are *intentional* keep-alive code; restarting the daemon on a transient ticker error would be worse than logging it.
- **Optional-feature gates**: e.g. `try: from src.monitoring.metrics import get_metrics / except: pass` for counter increments. The system runs fine without metrics; the alternative (crash on missing optional dep) is wrong.
- **Third-party SDK resilience layers**: e.g. `try: response = sdk.call() / except SDKError as e: backoff_and_retry(e)`. These are not in the audit (they re-raise after retry) but bear noting.
- **`_safe_call` ensemble inner**: even though it's HIGH, the *contract* is "return None, ensemble aggregates around the failure" — we keep the catch but add the rule-(e) test and heartbeat counter so partial dormancy is visible.

---

## Appendix — artefacts produced

- `scripts/silent_fallback_audit.py` — AST scanner (read-only; safe to re-run)
- `scripts/silent_fallback_triage.py` — severity classifier (read-only)
- `scripts/_silent_fallback_findings.json` — raw 433-row dump
- `scripts/_silent_fallback_triaged.json` — triaged 433-row dump with severity, hot/warm, has_test
- `docs/research-log/16_silent_fallback_audit.md` — this report
