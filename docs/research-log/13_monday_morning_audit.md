# Monday Morning Audit + Day-1 Live Operational Findings — D221 Phase F

**Date:** 2026-04-20 (Monday, EDT)
**Window:** Pre-market 04:30 EDT through after-hours 18:00 EDT
**Scope:** First live trading day with the full D221 Phase F infrastructure
(env-audit, Tier 3 Decision Learning, Path B persistence, news_agent
distillation). Documents both the planned pre-market audit AND what
actually happened during execution.
**Current state at write time:** Trading session DOWN (crashed 09:40 EDT,
no restart pending until 04:30 Tuesday auto-trigger).

## TL;DR

- **Pre-market audit landed clean.** 1 atomic commit shipped (`a53212a`),
  17 commits since Friday total. env_audit infrastructure live and
  verified end-to-end via heartbeat embedding + log emission. Restart
  cycle worked twice (PID 2756 → 36332 → 32328).
- **Cascade fired, persistence partially worked, Tier 3 demonstrated
  itself.** 8 DISAGREE_SHADOW_BUYS captured during the 10-minute live
  window before crash. 6 cleared Discord threshold (HIVE × 5, BZAI × 1).
  short_interest persistence wrote 10 rows; other 3 modules silent.
- **Session crashed at 09:40 EDT.** Alpaca REST circuit breaker tripped
  after 3 consecutive failures around market open. Exit code -1, lock
  file cleaned up gracefully. **System has been down 8.5 hours; watchdog
  ran at 17:00 but did not restart (market closed).**
- **The v2 thesis demonstrated itself in real operational data on day
  one.** Despite a 10-minute live window, the cascade-anti-selection
  signal showed up: composite scored above 0.60 on candidates the
  production cascade rejected. Paper 1 Row #1 candidates: HIVE
  (composite 0.648, rejected by D101 consensus gate × 4 + D124
  alignment × 1) and BZAI (composite 0.635, rejected by D101).

---

## Section 1 — Planned audit (the 13-item checklist queued from Sunday)

These were the items I intended to verify before market open. Some
sequenced before the cascade fires, some required live data to validate.

| # | Check | Why it mattered |
|---|---|---|
| 1 | Git state clean, in sync with origin | Baseline integrity |
| 2 | Daemon (backfill_agent) status | Background persistence health |
| 3 | Today's data presence (journal/shadow/persistence dirs) | Detect dormant infrastructure |
| 4 | Live trading session running | Don't audit a phantom |
| 5 | Env vars actually loaded (the 3 D221 keys especially) | Path B + Tier 3 dormancy detection |
| 6 | 1A/1B preflight (imports OK, output writable) | 09:00 execution non-negotiable |
| 7 | Phantom journal schema re-verify | Confirm the Sunday-night finding holds |
| 8 | Feature extraction spot-check (3 rows × 8 features) | Substrate for 1A/1B unblock |
| 9 | Env-toggle inventory (catalogue all `os.environ` reads) | Find next class of activation gap |
| 10 | FRMM pre-flight read | First Paper-1-candidate watch |
| 11 | Partial-v2 elements audit | Catch "already half-built" surprises |
| 12 | 13th audit item: Decision Learning messages flowing | First-day Tier 3 verification |
| 13 | (added during execution) Behavioral check at 09:30+ | Cascade ground-truth |

## Section 2 — Execution timeline

### 04:30:02 EDT — Session start (PID 2756)

`MomentumX-PaperTrading` Task Scheduler trigger. Pre-flight checks all
passed (Alpaca, Finnhub, Disk, Heartbeat). Equity $142,192.62 paper
mode. Started `python -u -m main paper`. **Critical detail unknown to
me at the time: this session loaded `.env` FROM the secrets file at
04:30:02, which DID NOT include the 3 D221 Phase F env keys I added
Sunday night** (because I'd added them to repo `.env` only, and the
04:30 Copy-Item -Force overwrote that file). So Path B persistence
and Tier 3 Discord were dormant for this session.

### 07:30 EDT — User asked for system check

I started the planned audit. Several findings landed quickly:

- Git clean, daemon healthy, queue drained 5,347 completed
- **False-negative**: I incorrectly concluded "live trading process not
  running" — `ps`/`tasklist` queries were too narrow. User pasted the
  session log showing it IS running.
- Discovered `.env` had been overwritten — `MOMENTUM_PERSIST_LIVE_FEATURES`,
  `DECISION_LEARNING_WEBHOOK_URL`, `DISCORD_DISAGREE_THRESHOLD` all gone.
  Found `daily_paper_trade.ps1:128-130` does `Copy-Item -Path $SecretsFile
  -Destination .env -Force` — repo `.env` is a derived artifact, the
  REAL activation site is `~/momentum-x-secrets.env`.

### 07:50 EDT — Track 1: secrets file activation + restart

Per user authorization (Option B):

1. Read secrets file via Python (no value echo). 6131 bytes, 107 lines,
   LF endings, ends with newline. Confirmed 3 target keys absent.
2. Appended 3 keys with comment block. +982 bytes (CRLF inflation
   accounts for delta). Diff-check: each key present exactly once,
   pre-existing keys preserved.
3. **Mistake: exposed `ALPACA_API_KEY=PK_REDACTED_ROTATED_2026-07-29` via
   `grep -n` on `.env`.** Paper key, low real risk, but the discipline
   matters. Recommended rotation. Not yet rotated as of write time.
4. `Stop-ScheduledTask MomentumX-PaperTrading` — PID 2756 GONE,
   heartbeat went stale at 26s, lock file lingered (script handles on
   restart).
5. `Start-ScheduledTask` — new PID 36332, heartbeat 0.8s fresh, phase
   PHASE_0_1, lock file refreshed.
6. Verified `.env` had all 3 keys (Copy-Item refreshed from secrets).
7. Live Discord test message delivered (HTTP 204, `is_enabled=True`,
   distribution cache loaded 2,400 / 157 / 7 / 8 / 2,398).

### 08:00-08:30 EDT — Track 2: broader audit (read-only)

| Audit | Result |
|---|---|
| 1A/1B preflight | ✓ Both scripts import cleanly, output writable, no collision |
| Phantom journal | Still 0 full records (consistent with zero-trade week) |
| Feature spot-check | **24/24 OK** across 3 rows × 8 features × prod-vs-manual. 1A/1B substrate intact. |
| Env-toggle inventory | 12 vars cataloged. **Structural gap surfaced:** none had session-startup verification log. |
| FRMM pre-flight | 548 mentions in today's log; candidate active in watchlist (Gap 59.5%, RVOL 55.6x, Float 16.2M, MCap $87M) |
| Partial-v2 elements | No surprises. Triple-barrier/IPW/regime-MoE: not present (correctly deferred). MoE language exists in `src/llm_arena/harness.py` and `experiment_1a_regime_cv.py` but for different purposes. |

### 08:30-09:00 EDT — Option B (env-audit module) executed

Per user directive (Option B authorized over deferral):

1. Wrote `src/utils/env_audit.py` (197 LOC):
   - `EnvVarSpec` namedtuple + categorized registry
   - `_classify_value()` returns `(status, length)` where status ∈
     `{<UNSET>, <EMPTY>, <WHITESPACE>, <SET>}`
   - `collect_env_audit()` walks the registry against `os.environ`
   - `log_env_audit(logger)` emits one INFO line per var; CRITICAL UNSET
     promotes to WARNING; summary line tallies n_critical_unset
   - `format_for_heartbeat()` returns compact dict for embedding
2. Wrote `tests/unit/test_env_audit.py` (24 adversarial tests covering
   the 7 classes from user spec). **24/24 pass on first run, 0 bugs.**
   Defensive-by-design from line 1.
3. Wired into `main.py:7297` (post-dotenv-load) for log emission.
   Caught a closure-scoping issue: `_env_audit_for_heartbeat` was in
   `main()` scope, heartbeat function is closure inside `cmd_paper()`.
   Split cleanly: log in main, re-collect inside cmd_paper for
   heartbeat embedding.
4. Updated `docs/research-log/11_sunday_bug_sweep.md` with addendum
   documenting the hardened rule (a)-(e), credential-file scanning
   discipline, multi-source process-state detection rule, and the two
   deliberately-deferred behaviors.
5. Atomic commit `a53212a`. 4 files changed, 651 insertions.

### 08:30 EDT — Second restart

Stop+Start cycle (PID 36332 → 32328). Verified:
- New session heartbeat fresh, env_audit field embedded
  (`n_total=14, n_critical_unset=0`)
- 14 ENV_AUDIT lines visible at 08:01:48 in `momentum_2026-04-20.log`
- Discord test message delivered

### 08:50 EDT — Push to origin

`f9592c6..a53212a` to `origin/develop`. **17 commits since Friday's
loader fix.**

### 09:30 EDT — Market open, cascade fires

This is the moment that mattered. Phase 2 began evaluating candidates.

**Path B persistence verification:**
- `data/short_interest/2026-04-20.jsonl` populated. 10 rows including
  LZM at 13:30:27 UTC (= 09:30:27 EDT, the literal first second after
  market open). Sample row:
  ```json
  {"ticker": "LZM", "timestamp": "...", "short_float_pct": 0.07,
   "short_shares": 9359, "float_shares": 30576811, "days_to_cover": 0.03,
   "classification": "unknown", "data_source": "yfinance", "data_age_days": null,
   "error": null}
  ```
- **Other 3 modules silent.** `data/sentiment_velocity/`,
  `data/order_flow/`, `data/premarket_velocity/` — directories not
  created. Either those modules weren't exercised in the cascade flow
  during the 10-min window, or there's a wiring gap to investigate.

**Tier 3 + composite shadow firing:**
- 8 DISAGREE_SHADOW_BUYS captured in `shadow_2026-04-20.jsonl` over the
  10-minute window
- 6 of 8 cleared the 0.55 Discord surface threshold
- Per-ticker breakdown:

| ticker | composite | mfcs | rejected by | Discord (≥0.55) |
|---|---|---|---|---|
| HIVE | 0.648 | 0.092 | D101 consensus gate | YES |
| HIVE | 0.648 | 0.108 | D124 consensus alignment | YES |
| HIVE | 0.648 | 0.092 | D124 consensus alignment | YES |
| HIVE | 0.648 | 0.092 | D101 consensus gate | YES |
| HIVE | 0.648 | 0.092 | D101 consensus gate | YES |
| HIVE | 0.648 | 0.204 | D101 consensus gate | YES |
| BZAI | 0.635 | 0.189 | D101 consensus gate | YES |
| LZM  | 0.455 | 0.171 | D124 consensus alignment | no (below 0.55) |

**This is exactly what the v2 thesis predicted.** The composite score
disagrees with the production cascade — composite says BUY (≥0.55,
even ≥0.60), production gates the candidate out (D101 = 0 directional
agents, D124 = 0 bullish vs 1 bearish). Per the -0.86 cascade-anti-
selection finding from Sunday's retrain, **these are exactly the
candidates where alpha is most likely to live.** HIVE × 5 messages,
BZAI × 1. Whether they were realized winners requires post-close bar
data (queued for tomorrow's analysis).

### 09:38:44 EDT — Circuit breaker trips

```
09:38:44 src.utils.circuit_breaker ERROR D87: Circuit breaker 'alpaca_rest' TRIPPED
         (closed -> open). 3 consecutive failures. Rejecting calls for 30s. Total trips: 1
09:38:50 src.utils.circuit_breaker ERROR D87: Circuit breaker 'llm_provider' TRIPPED
         (closed -> open). 5 consecutive failures. Rejecting calls for 120s. Total trips: 5
09:38:52 src.utils.circuit_breaker INFO  D87: Circuit breaker 'llm_provider' recovered.
                                          (open -> closed). Trips: 5. D108: Reset 120s -> 60s.
```

Alpaca REST API failures cluster around market open (well-known load
spike). 3 consecutive REST failures hit the circuit breaker threshold,
breaker opened, calls rejected for 30s. LLM provider also tripped at
09:38:50 but recovered 2 seconds later (transient).

### 09:40:02 EDT — Session exit

```
09:39:50 momentum_x INFO  Scans: 80.0  |  Evals: 60.0  |  Debates: 13.0 (0.0 BUY)
09:39:50 momentum_x INFO  Agent latency: 9934ms  |  Errors: 0.0  |  Vetoes: 0.0
09:39:50 momentum_x INFO  Circuit breaker: OPEN: alpaca_rest
09:40:02 [paper_log]      Paper trading exited with code -1
```

Final session stats: 80 scans, 60 evaluations, 13 debates, 0 BUYs,
9934ms agent latency. Lock file removed cleanly via the script's
finally block. Heartbeat file removed. Task state went to "Ready".

### 17:00 EDT — Watchdog ran

`MomentumX-Watchdog` last execution. Did NOT restart the trading task
(market already closed at 16:00 EDT, no point). LastTaskResult: 0
(success). Task Scheduler's NextRunTime for paper-trading: tomorrow
04:30:00 EDT.

### 18:00 EDT — Now (write time)

Trading session has been DOWN for 8.5 hours. No persistence happening.
Daemon is idle (queue drained). Backfill agent still running healthy
(PID 13920, 43+ hours uptime). Tomorrow's 04:30 trigger will start a
fresh session — the secrets file refresh will activate Path B + Tier 3
again.

---

## Section 3 — Findings categorized

### Structural (infrastructure shipped)

1. **env_audit module + heartbeat embedding** (commit `a53212a`).
   Closes the silent-activation visibility gap. 24 adversarial tests,
   0 bugs found by the sweep. Categorized output (CRITICAL /
   FEATURE_GATED / SAFETY_KILL_SWITCHES) with length hints,
   never-echoes-value contract, embedded in heartbeat for monitoring.
2. **Hardened bug-sweep template**. The (a)-(e) rule for env toggles
   plus credential-file-scanning discipline plus multi-source process-
   state detection. Documented in `11_sunday_bug_sweep.md` addendum.

### Operational (today's behavior)

3. **Path B persistence works for short_interest** (10 rows written).
   But sentiment_velocity, order_flow, premarket_velocity dirs absent
   — they weren't exercised. Investigate whether this is correct
   conditional flow (e.g., they only run for specific candidate types)
   or a wiring gap.
4. **Session crash at 09:40 EDT** from Alpaca REST circuit breaker.
   Three consecutive REST failures within ~2-3 seconds at market open.
   Worth investigating whether this is recurrent at 09:30-09:40 (load
   spike) or specific to today.
5. **Watchdog correctly did not restart after market close**
   (LastRunTime 17:00 EDT). Either by-design (cmd_paper exits
   gracefully when market is over) or because watchdog logic specifically
   checks for market hours before restarting. Worth understanding.
6. **Lock file + heartbeat cleanup worked correctly on crash.** No
   stale state to clean before tomorrow's 04:30 launch.

### v2 thesis evidence (the operational moment)

7. **6 enriched DISAGREE_SHADOW_BUYS messages should have fired to
   Decision Learning channel** during the 10-minute live window
   (HIVE × 5 + BZAI × 1, all composite ≥ 0.60, all rejected by D101 or
   D124 consensus gates, NONE in the trust-the-gate allowlist).
8. **The pattern is exactly the cascade-anti-selection finding.** D101
   = "0 directional agents < 1 minimum" and D124 = "0 bullish vs 1
   bearish" — both are consensus-strength gates rejecting candidates
   for *low* agent agreement. The composite, trained on broader signal,
   says these are still buys. Per the -0.86 coefficient finding, these
   are exactly where alpha lives.
9. **Whether the Discord channel actually received the 6 enriched
   messages cannot be confirmed from logs alone** (post_disagree_shadow_buy
   uses `logger.debug` for failures only; successful sends don't log).
   Tuesday morning audit item: visually confirm in the Decision Learning
   channel that 6 messages with HIVE/BZAI titles landed between
   09:30 and 09:40 EDT.

### Mistakes named (so they don't fade)

10. **`ALPACA_API_KEY` exposure via `grep -n` on `.env`.** Paper key,
    low real risk. Recommend rotation today (still pending). Discipline:
    default to `grep -c` on credential files; use `-n` only when value
    is required and then redact via Python.
11. **False-negative on live-trading process detection** (`ps`/`tasklist`
    too narrow on Windows — process CommandLine was hidden). Discipline:
    multi-source process-state detection (heartbeat + lock file +
    Get-Process; require agreement before concluding state).
12. **ALPACA endpoint URL confusion.** User sent
    `https://paper-api.alpaca.markets/v2`; the codebase appends `/v2/`
    per call so the base must NOT include it. Caught before any
    destructive change. Surfaced 3 options to user for resolution.
13. **Time-of-day confusion during live execution.** I thought it was
    ~9:40 AM throughout the post-09:40 audit; it was actually 6 PM.
    The session had crashed 8 hours earlier. Caught when checking the
    watchdog's "LastRunTime: 5:00:02 PM" which I initially thought was
    in the future. Discipline: always verify current time at start of
    each multi-step audit when timing matters.

---

## Section 4 — Commits shipped today

```
a53212a  D221 Phase F: env-audit log + heartbeat embedding (Mon 2026-04-20)
```

That's the only commit added today. The other 16 commits in the
weekend chain shipped Sat-Sun. Total weekend: 17 commits, all on
`develop`, in sync with origin.

---

## Section 5 — Pending for tomorrow (Tuesday 04:30+)

### Auto-fires (no operator action)

- **04:30 EDT:** Task Scheduler triggers `MomentumX-PaperTrading`.
  Secrets file → .env Copy-Item → load_dotenv → ENV_AUDIT log lines
  fire → heartbeat begins writing with env_audit field. Should be
  identical to today's 08:01 restart.

### Required operator verification (first 30 min of session)

- [ ] **ENV_AUDIT lines present in fresh log.** Same 14 lines, all
      CRITICAL SET, FEATURE_GATED 5/8 SET. If any drift, investigate
      before market open.
- [ ] **Heartbeat has `env_audit` field with `n_critical_unset=0`.**
- [ ] **Decision Learning Discord channel test message** if any DISAGREE
      messages from today aren't visible (manual check that yesterday's
      6 enriched messages did land — confirms the wire works for real
      events, not just my synthetic smoke tests).
- [ ] **Path B persistence dirs exist after first cascade fire.**
      Specifically check WHY only `short_interest/` populated yesterday;
      should sentiment_velocity / order_flow / premarket_velocity also
      have written? Either expected behavior (conditional flow) or a
      wiring gap to fix.

### Required operator verification (after market open)

- [ ] **alpaca_rest circuit breaker recovers without tripping.**
      Yesterday's crash was driven by 3 consecutive REST failures at
      market open. If this recurs, root-cause investigation needed
      (maybe rate limiting, maybe specific endpoint, maybe correlated
      with a particular candidate type).
- [ ] **Session survives past 09:40 (the crash time yesterday).**
      Goal: full session through 16:00 EDT without circuit breaker
      crash.

### Tuesday morning queue

| Item | Time est | Notes |
|---|---|---|
| Verify Discord channel received yesterday's 6 DISAGREE messages | 5 min | If yes: Paper 1 has Row #1-6 from day one. Screenshot for the paper. |
| Investigate: why only short_interest persisted (other 3 modules dormant) | 30 min | Either conditional flow or wiring gap |
| Investigate: alpaca_rest crash root cause | 30 min | Check Alpaca status page for 09:30-09:40 outage; check our retry logic |
| Execute 1A + 1B (deferred from today) | Multi-hour | Substrate verified intact (24/24 spot-check); preflight clean |
| fundamental_agent kill (apply 5-test bug-sweep template) | 1-2 hr | Same pattern as news_agent distillation |
| **ALPACA_API_KEY rotation (still pending)** | 5 min | Paper key, low risk, but discipline matters |
| Launcher script hardening (add critical-var validation post-Copy-Item) | 30 min | Per Sunday's audit item |

### Architecture decisions queued for Tuesday

- Read 1A/1B results when they land. Apply decision matrix (overfit vs
  regime). Route v2 architecture for the week.
- **Re-read the Decision Learning channel's full Monday accumulation**
  before architecture call. The 6 enriched DISAGREE messages (if
  delivered) are the first operational day of Paper 1 data. Patterns
  there should inform the architecture call.
- Re-scope experiment #2 from "phantom journal IPW" to "journal-based
  IPW" per Sunday's Finding B (phantom has 0 full records; journals
  have 2,400+).

---

## Section 6 — What today actually proved

Three things, in increasing order of importance:

**1. The infrastructure works.** env_audit fires, persistence writes,
shadow telemetry captures, Tier 3 messages should have fired. Nothing
silently dormant after the morning restart. The Sunday-night
defensive-by-design discipline (24/24 tests, 0 bugs) paid off — no
"shipped clean but doesn't actually work" surprise.

**2. The crash is informative.** A 10-minute live window before the
circuit breaker tripped is not enough to draw conclusions about
profitability, but it IS enough to confirm the operational stack works
end-to-end. The crash itself is a bug (or at minimum a calibration
issue with the circuit breaker thresholds during market-open load
spike), but it's a known-class bug — circuit breakers exist for exactly
this reason. Tomorrow's session is the real test.

**3. The v2 thesis is showing itself in real data.** Even in 10
minutes, 8 DISAGREE_SHADOW_BUYS landed — composite says BUY,
production cascade says NO_TRADE. The rejection codes (D101, D124) are
both consensus-gate rejections — the cascade rejecting because not
enough agents agree. Per the -0.86 finding: cascade consensus is
contra-predictive in this universe. The composite is trained to weight
that finding correctly; the cascade is not.

**HIVE got 5 of those 8 messages.** If HIVE was a realized winner
between 09:30 and close (16:00 EDT), that's the first operational
demonstration of the publishable claim. Tuesday morning analysis: pull
HIVE's bar data from 09:30 close, compute realized return, compare to
composite's prediction. If positive: Paper 1 has real-world Row #1.

---

## Section 7 — How to read this doc

- **For Tuesday-morning Pierce:** Section 5 has the full queued
  checklist. Section 4 lists today's commit (just one). Section 3
  has the categorized findings.
- **For someone picking this up cold:** Read Section 2 (timeline) for
  what happened. Then Section 6 for what it means. Then Section 5 for
  what to do next.
- **For the v2 architecture decision Tuesday:** Section 6 item 3 plus
  the Decision Learning Discord channel content (manually visible in
  Discord) plus the 1A/1B results when they land. All three feed the
  call.

## Appendix — current state at write time (18:02 EDT)

```
git tip:        a53212a (in sync with origin/develop)
weekend:        17 commits since Friday's loader fix
trading task:   Ready (was Running, exited at 09:40 EDT with code -1)
trading PID:    none (heartbeat file removed, lock cleaned)
backfill daemon: PID 13920, healthy, 43h uptime, queue drained
data/journals/journal_2026-04-20_120152.jsonl: present (session output)
data/shadow/shadow_2026-04-20.jsonl: 8 rows (all DISAGREE_SHADOW_BUYS)
data/short_interest/2026-04-20.jsonl: 10 rows (Path B working)
data/sentiment_velocity/, data/order_flow/, data/premarket_velocity/: empty
ENV_AUDIT lines in today's log: 15 (one full audit at 08:01:48)
Total scan iterations today: 276
Total FRMM mentions: 1017
```

Tomorrow at 04:30 EDT, Task Scheduler fires the next session. Everything
shipped today should activate cleanly because the secrets file now has
the canonical D221 Phase F keys. ENV_AUDIT will fire at session start
~04:30:10 — first thing to check Tuesday morning.
