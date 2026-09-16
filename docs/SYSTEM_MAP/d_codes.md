# d_codes.md — canonical D-code registry

One TOML block per D-code. Machine-queryable via `tomllib`. Human-readable
on GitHub.

**Population status:** schema locked 2026-05-24. 25 worked examples
populated 2026-05-24 evening (the operationally-critical codes — the
ones referenced 20+ times in src/ OR that gated major shipping
decisions). Remaining ~290 codes backfilled Wed-Fri 2026-05-27→29.

---

## Schema

```toml
[DXXX_EXAMPLE]                          # key = D-code identifier (use real D-code in entries)
label = "SHORT_LABEL / human-readable name"
status = "ACTIVE"                       # ACTIVE | SHADOW | DEPRECATED | FILED | REVERTED
shipped_date = "YYYY-MM-DD"
docs = ["169", "171"]                   # ship doc #s in narrative
supersedes = []                         # D-codes this replaces
superseded_by = []                      # D-codes that replace this (DEPRECATED only)
depends_on = ["D142", "D294"]           # other D-codes OR "module:path"
gates = ["MOMENTUM_T2_ENABLED"]         # env vars or feature flags
category = "alpha"                      # alpha | safety | observability | data
one_line = "Brief one-line description for grepping."
```

## Validation invariants (enforced by `_linter.py`)

1. Status-supersession consistency: `superseded_by != []` ⇒ `status ∈ {DEPRECATED, REVERTED}`
2. Bidirectional pointer agreement: `A.supersedes ∋ B ⇔ B.superseded_by ∋ A`
3. Dependency resolution: every `depends_on` entry resolves to another D-code OR `module:path` reference
4. `category` mandatory for `ACTIVE`
5. Doc references must exist (`docs/research-log/{N}_*.md`)
6. No orphan ACTIVE codes (must appear in `src/`, `tests/`, another code's `depends_on`, or a runbook)

## Highest D-code currently in production: **D315**

## Smoke test verification (2026-05-24)

Pre-commit hook verified end-to-end:
- **Negative case**: commit adding `docs/research-log/175_SMOKE_TEST.md` WITHOUT changelog update → hook REJECTED with format-example error message
- **Positive case**: same commit WITH `docs/SYSTEM_MAP/changelog.md` staged → hook ACCEPTED

Discipline is **enforced structurally**, not by memory.

---

## ACTIVE codes (operationally critical, populated 2026-05-24)

```toml
# ── LLM Agent layer ────────────────────────────────────────────────

[D91]
label = "LLM_FALLBACK_CHAIN"
status = "ACTIVE"
shipped_date = "2026-03-04"
docs = ["167", "171"]
supersedes = []
superseded_by = []
depends_on = ["D92", "src/agents/base.py"]
gates = []
category = "safety"
one_line = "Three-tier model resilience: primary → fallback (different family) → emergency. Activated on timeout / API error / circuit breaker."

[D92]
label = "EMERGENCY_MODEL_TIER"
status = "ACTIVE"
shipped_date = "2026-03-04"
docs = ["167"]
supersedes = []
superseded_by = []
depends_on = ["D91", "src/agents/base.py"]
gates = []
category = "safety"
one_line = "Last-resort model (Llama 70B) when primary + fallback fail. Defensive <think> block parsing."

[D101]
label = "BIPOLAR_MFCS"
status = "ACTIVE"
shipped_date = "2026-03-10"
docs = ["100"]
supersedes = ["D26"]
superseded_by = []
depends_on = ["src/core/scoring.py"]
gates = []
category = "alpha"
one_line = "Bipolar [-1, +1] MFCS range. Enables negative consensus expression and STRONG_BEAR signals to lower composite."

[D116]
label = "CONFIDENCE_FLOOR_020"
status = "ACTIVE"
shipped_date = "2026-04"
docs = ["116"]
supersedes = []
superseded_by = []
depends_on = ["D101", "src/agents/base.py"]
gates = []
category = "alpha"
one_line = "Confidence floor 0.20 min for directional signals. Stabilizes MFCS under low-confidence agents."

# ── Stop / position / exit ─────────────────────────────────────────

[D142]
label = "PHASE1_STOP_TIGHTENING"
status = "ACTIVE"
shipped_date = "2026-04"
docs = ["170", "171"]
supersedes = []
superseded_by = []
depends_on = ["src/execution/alpaca_executor.py"]
gates = []
category = "safety"
one_line = "Time-phased stop override: Phase 0 ultra-tight first 30s, Phase 1 1.5% static. LONGS only. Wide arm bypasses via D310."

[D146]
label = "NON_SHORTABLE_FILTERING"
status = "ACTIVE"
shipped_date = "2026-04-08"
docs = ["146"]
supersedes = []
superseded_by = []
depends_on = ["D147", "src/scanners/premarket.py"]
gates = []
category = "safety"
one_line = "Filters non-shortable stocks before short entry. Enables D147 fix."

[D147]
label = "SHORTABILITY_CHECK"
status = "ACTIVE"
shipped_date = "2026-04-08"
docs = ["147"]
supersedes = []
superseded_by = []
depends_on = ["D146", "D165", "src/execution/alpaca_executor.py"]
gates = []
category = "safety"
one_line = "Skips stop protection for non-shortables (Alpaca 422 on partial sells); D165 compensates."

[D163]
label = "TRAILING_STOP_SOFTWARE"
status = "ACTIVE"
shipped_date = "2026-04"
docs = ["163"]
supersedes = []
superseded_by = []
depends_on = ["src/execution/bridge.py"]
gates = []
category = "alpha"
one_line = "Per-cycle software trailing stop (fires at +2% gain); trail level = midpoint(entry, peak)."

[D165]
label = "TRANCHE_PROFIT_TAKING"
status = "ACTIVE"
shipped_date = "2026-04"
docs = ["165"]
supersedes = ["D164"]
superseded_by = []
depends_on = ["D142", "D147", "src/execution/bridge.py"]
gates = []
category = "alpha"
one_line = "Software tranche exits at +3/+6/+10% (longs) or -3/-6/-10% (shorts). Bypasses Alpaca 422 on non-shortables."

# ── Carry overnight + Kelly sizing ─────────────────────────────────

[D278]
label = "T1_NEXT_OPEN_EXIT_POLICY"
status = "ACTIVE"
shipped_date = "2026-04-29"
docs = ["171", "172"]
supersedes = []
superseded_by = []
depends_on = ["D91", "D295", "src/core/orchestrator.py"]
gates = ["MOMENTUM_EXIT_POLICY"]
category = "alpha"
one_line = "Carry-overnight policy. Skips BAR-1 EXIT + TIME_EXIT, closes positions at next session market open via D91."

[D281]
label = "KELLY_BASELINE_N25"
status = "ACTIVE"
shipped_date = "2026-05-12"
docs = ["162"]
supersedes = []
superseded_by = []
depends_on = ["scripts/ml_meta_scorer_inference.py"]
gates = []
category = "alpha"
one_line = "Per-tier Kelly baseline established at n=25+. Control measurement for D291 series ratchets."

[D290]
label = "ADAPTIVE_KELLY_BY_AUM"
status = "ACTIVE"
shipped_date = "2026-05-11"
docs = ["149", "162"]
supersedes = []
superseded_by = []
depends_on = ["D281", "scripts/ml_meta_scorer_inference.py"]
gates = []
category = "alpha"
one_line = "AUM-bracketed Kelly schedule. +60% $PnL at $500k bracket vs flat."

[D291_5]
label = "HIGH_TIER_KELLY_RAISE_50"
status = "ACTIVE"
shipped_date = "2026-05-12"
docs = ["162"]
supersedes = []
superseded_by = []
depends_on = ["D290", "D281"]
gates = []
category = "alpha"
one_line = "HIGH tier Kelly cap 0.35 → 0.50. Trigger met: n_HIGH=56, mean return +4.13%."

# ── Defense / observability cluster ────────────────────────────────

[D277]
label = "HALT_NEW_ENTRIES"
status = "ACTIVE"
shipped_date = "2026-04-28"
docs = ["156", "157", "160"]
supersedes = []
superseded_by = []
depends_on = ["src/data/alpaca_client.py"]
gates = ["MOMENTUM_HALT_NEW_ENTRIES"]
category = "safety"
one_line = "Operator kill switch. Refuses OTO + short submissions mid-session. Alert per refusal (post-D156 silent-block incident)."

[D293]
label = "TABPFN_ENSEMBLE"
status = "REVERTED"
shipped_date = "2026-05-12"
docs = ["152", "153", "154", "155"]
supersedes = []
superseded_by = ["D293a"]
depends_on = ["scripts/tabpfn_shadow_runner.py"]
gates = []
one_line = "Multi-seed ensemble REVERTED 2026-05-12: bootstrap CI gate failed at n=2-7 dates. D293.8 filed for n>=200 retest."

[D294]
label = "ALPACA_STOP_FLOOR_CLAMP"
status = "ACTIVE"
shipped_date = "2026-05-13"
docs = ["164"]
supersedes = []
superseded_by = []
depends_on = ["src/data/alpaca_client.py"]
gates = []
category = "safety"
one_line = "Stop-loss price clamp: enforces stop ≥ entry × (1 - max_pct) AND stop ≤ entry - $0.01 to avoid Alpaca rejection on sub-$1 ticks."

[D295]
label = "D91_NEXT_OPEN_JOURNAL_CLOSE"
status = "ACTIVE"
shipped_date = "2026-05-13"
docs = ["164"]
supersedes = []
superseded_by = []
depends_on = ["D91", "D278", "src/analysis/trade_journal.py"]
gates = []
category = "safety"
one_line = "Records explicit journal close when D91 fires next-open exit. Prevents D222 EOD recon flagging carry-overnight closes as MISSING."

[D297]
label = "OTO_STOP_FILL_JOURNAL"
status = "ACTIVE"
shipped_date = "2026-05-13"
docs = ["164"]
supersedes = []
superseded_by = []
depends_on = ["D295", "src/data/alpaca_client.py", "src/analysis/trade_journal.py"]
gates = []
category = "safety"
one_line = "OTO stop-leg fill records journal close. Closes the CISS/GCTS-class silent journal gap."

[D304]
label = "ALERT_KWARG_COMPLETENESS"
status = "ACTIVE"
shipped_date = "2026-05-19"
docs = ["168"]
supersedes = []
superseded_by = []
depends_on = ["src/monitoring/alerts.py"]
gates = []
category = "observability"
one_line = "Standardized Discord alert schema validation. All alerts must pass complete kwarg set (color, timestamp, footer) to prevent KeyError on post."

[D308]
label = "STOP_DECISION_LOG"
status = "ACTIVE"
shipped_date = "2026-05-19"
docs = ["169", "170"]
supersedes = []
superseded_by = []
depends_on = ["D142", "D310", "src/analysis/stop_decision_log.py"]
gates = []
category = "observability"
one_line = "JSONL shadow log of (atr_stop, phase1_stop, submitted) at every OTO submission. Best-effort; production unaffected on write failure."

[D310]
label = "STOP_WIDENING_T2_ARM_ASSIGN"
status = "ACTIVE"
shipped_date = "2026-05-19"
docs = ["169", "171", "174"]
supersedes = []
superseded_by = []
depends_on = ["D142", "D294", "D308", "D313", "src/execution/t2_arm_assignment.py"]
gates = ["MOMENTUM_T2_ENABLED"]
category = "alpha"
one_line = "Hash-keyed 50/50 A/B: wide arm uses ATR stop + halved qty + L1 standalone STOP submission; tight arm preserves D142 OTO."

[D311]
label = "PNL_RECON_AFTER_FILTER"
status = "ACTIVE"
shipped_date = "2026-05-24"
docs = ["170"]
supersedes = []
superseded_by = []
depends_on = ["src/analysis/trade_journal.py"]
gates = []
category = "observability"
one_line = "Trade journal pulls broker fills with after=<today UTC midnight> filter. Prevents phantom $49k delta on EOD recon."

[D312]
label = "GATE_SUMMARY_TO_EOD"
status = "ACTIVE"
shipped_date = "2026-05-24"
docs = ["172"]
supersedes = []
superseded_by = []
depends_on = ["D304", "src/data/phantom_journal.py", "src/monitoring/alerts.py"]
gates = []
category = "observability"
one_line = "Per-session gate-rejection counter (D216, D85, etc.) surfaced via veto_summary on EOD Discord. Closes Friday silent-BUY visibility."

[D313]
label = "HEDGE_INTEGRITY_WATCHER"
status = "ACTIVE"
shipped_date = "2026-05-24"
docs = ["171", "173", "174"]
supersedes = []
superseded_by = []
depends_on = ["D310", "src/monitoring/hedge_integrity_watcher.py", "main.py"]
gates = []
category = "safety"
one_line = "L2 invariant watcher: polls broker every 20s, enforces qty != 0 → has_protective_order. Emergency stop after 60s tolerance + L2_WATCHDOG."

[D314]
label = "DURABLE_ALERT_SPOOL"
status = "ACTIVE"
shipped_date = "2026-05-24"
docs = ["173", "174"]
supersedes = []
superseded_by = []
depends_on = ["src/monitoring/durable_alert_spool.py", "src/monitoring/alerts.py"]
gates = []
category = "observability"
one_line = "Every Discord alert spooled to data/alerts/ BEFORE live POST. Retry loop redelivers on failure. [STALE] prefix on >2h redeliveries."

[D315]
label = "BOOT_SELF_TEST"
status = "ACTIVE"
shipped_date = "2026-05-24"
docs = ["174"]
supersedes = []
superseded_by = []
depends_on = ["D313", "D314", "main.py"]
gates = []
category = "observability"
one_line = "Single Discord embed at startup confirming T2/L2/L2_WATCHDOG/D314_RETRY status. Doubles as E2E test of durable_post pipeline."
```

---

## DEPRECATED / REVERTED (lessons learned)

```toml
[D26]
label = "WEIGHT_REDISTRIBUTION_LEGACY"
status = "DEPRECATED"
shipped_date = "early-2026"
docs = []
supersedes = []
superseded_by = ["D101"]
depends_on = []
gates = []
one_line = "Pre-D101 weight redistribution when agents returned empty signals. Replaced by bipolar [-1,+1] MFCS."

[D164]
label = "EARLY_PROFIT_TAKE_DEPRECATED"
status = "DEPRECATED"
shipped_date = "2026-04"
docs = ["164"]
supersedes = []
superseded_by = ["D165"]
depends_on = []
gates = []
one_line = "Aggressive early exit trigger. Superseded by D165 software tranches."

[D293a]
label = "TABPFN_SINGLE_SEED_REVERT"
status = "ACTIVE"
shipped_date = "2026-05-12"
docs = ["155"]
supersedes = ["D293"]
superseded_by = []
depends_on = ["scripts/tabpfn_shadow_runner.py"]
gates = []
category = "alpha"
one_line = "Single-seed revert after D293 ensemble failed bootstrap CI gate. n_est=1, single random_state."
```

---

## FILED (specced, not yet shipped)

```toml
[D293_8]
label = "TABPFN_ENSEMBLE_RETEST"
status = "FILED"
shipped_date = ""
docs = ["155", "162", "167"]
depends_on = ["D293", "scripts/tabpfn_shadow_runner.py"]
gates = []
one_line = "Re-test multi-seed ensemble at n >= 200 labeled picks. Targets ~2026-07-12 after T2 accumulates picks."

[D316]
label = "EXTERNAL_PROCESS_WATCHDOG"
status = "FILED"
shipped_date = ""
docs = ["173", "174"]
depends_on = []
gates = []
one_line = "PowerShell scheduled task polls heartbeat.json freshness; restarts bot + Discord webhook if >3 min stale. Closes last in-process SPOF."

[D317]
label = "EVENT_SOURCED_TRADE_JOURNAL"
status = "FILED"
shipped_date = ""
docs = []                                  # will be set when doc 175 lands
depends_on = ["src/analysis/trade_journal.py"]
gates = []
one_line = "SQLite/DuckDB append-only events. Crash recovery one-line replay. Target: 2026-06-15."
```

---

## Reverse indexes (regenerated by `_linter.py` Mon PM)

- `_by_category.md` — alpha / safety / observability / data
- `_by_status.md` — DEPRECATED + REVERTED (delete candidates + lessons)
- `_dependency_graph.dot` + `.svg` — Graphviz visualization

Derived artifacts; do not edit by hand.
