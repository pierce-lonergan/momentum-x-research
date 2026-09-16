# 42 — Monday Operator Runbook

**Status:** operational. Bridges "discovery infrastructure complete" → "operator knows what to do."
**Predecessors:** `40_discovery_infrastructure_summary.md` (definitive overview), `architecture_call_deck.html` (slide brief).
**Companion script:** `scripts/preflight.py` (one-command morning health check).
**Audience:** Pierce on Monday morning + every trading day after, until automation evolves further.

---

## §0 — TL;DR (the 90-second version)

Monday morning, in order:

```
1. python scripts/preflight.py                              # health check
2. Review previous session's eod_*.json (if any)            # triage
3. Confirm BOCPD prior is current (data/priors/...parquet)  # calibration
4. Start the trading process                                # session
5. Open dashboard / live tail logs                          # monitoring
```

Wednesday evening, after Monday + Tuesday both ship clean:
```
6. Arm Track B daemon (D231 hard-block) per N=2 criteria     # operator decision
```

Throughout the week:
```
7. If D262 fires (BOCPD refit recommended): run pretrain_bocpd_prior.py
8. If D247 fires (SMART_EXIT escalate): operator intervention required
9. If D224 fires (Kelly halved): observe — system is self-protecting
```

EOD daily:
```
10. cat data/reports/eod_$(date +%Y-%m-%d).json | jq .       # consolidated report
11. Review fires[] list — investigate any unexpected codes
12. Update notes if a new bug class surfaces (continue alphabet: BF, BG, ...)
```

---

## §1 — Pre-session (before 04:30 ET launcher fires)

### Health check (one command)

```bash
python scripts/preflight.py
```

Verifies, in order:

1. **Test suite green** — runs `pytest -m "not slow" -q` on the relevant scope. Expects ~225/225 fast tests in ~30s. Any failure here means **DO NOT START THE SESSION** until investigated.
2. **D-code registry: 0 orphans** — runs `scripts/audit_d_codes.py --strict`. Catches "we reserved a code but never wired it" silent debt.
3. **BOCPD prior present** — verifies `data/priors/s1_bocpd_prior.parquet` exists and the schema_version matches expected. If missing, runs `pretrain_bocpd_prior.py --dry-run` and warns.
4. **Phase 0 directory writable** — confirms `data/instrumentation/` exists + writable. Warns if a stale `*.tmp` file from a previous crash needs cleanup.
5. **Git working copy clean** — warns if uncommitted changes exist (D239 HEARTBEAT_DIRTY_WORKTREE will fire downstream).
6. **Disk space** — warns if `<1GB` free (Phase 0 captures + reports accumulate).

### Manual checks (under 60 seconds each)

- **Equity** — verify Alpaca paper account equity matches expected (~$140k baseline, adjust for prior session realized P&L).
- **Open positions** — confirm `0` open at session start. Anything non-zero is a leftover ghost requiring Bug Z-class investigation FIRST.
- **Working orders** — confirm `0` working at start. Same risk class as open positions.

---

## §2 — Session start (04:30 - 09:30 ET)

The launcher autostart fires `main.py`. Watch for these in the early log:

| Log marker | What it means | Action |
|---|---|---|
| `Phase 0 instrumentation writer initialized` | Phase 0 capturing | Expected ✓ |
| `BOCPD prior loaded: mu_edge=... sigma_edge=... n_trades=...` | Pre-trained prior loaded | Expected ✓ — note the values match what the file contains |
| `Kelly governor armed: halve to 0.50 for 5 trades on D223 BOCPD_BREAK` | Kelly governance armed | Expected ✓ |
| `D239 HEARTBEAT_DIRTY_WORKTREE` | Git worktree had uncommitted changes at startup | Investigate — running code may not match committed code |
| `D240 PREOPEN_GHOST_DETECTED` | Pre-open broker check found unknown positions/orders | **HALT** — investigate before any new entries |

If D239 or D240 fire, do not let the session enter market hours until resolved.

---

## §3 — Mid-session (09:30 - 16:00 ET)

The system runs autonomously. Monitor signals via the live dashboard or tail logs for:

### Routine (no action required)

- `D101 SIZING ...` — every entry. Check the `[D115:Tier{N}]` suffix matches expected Kelly tier.
- `D215 EXECUTION RECORDED ...` — every fill. Per-trade P&L feeds `trade_results.jsonl`.
- `D146 BAR-1 EXIT ... ACTUAL=$...` — every BAR-1 exit. Compare to the Arena_expected line one row above.
- `D217 poll terminal status reached ...` — every order's terminal poll completion.

### Attention (review but routine)

- **`D224 KELLY_HALVED ACTIVE — sizing TICKER at multiplier=0.50`** — BOCPD detected a regime break; Kelly halved for next 5 trades. **No action needed** — the system is self-protecting. Note for the EOD review whether the regime break was real (cluster of losses) or false-positive (single outlier).
- **`D218 QTY_DRIFT WARN`** — broker qty diverged from internal tracker; auto-reconciled. Note in EOD review.
- **`D230 RECON_WARN ...`** — reconciliation tier-2 alert; below Tier-1 hard threshold.

### Attention (action may be required)

- **`D245 SMART_EXIT_REJECTED ... attempt N/3`** — broker rejected close attempt; retry pending. Wait for D246 (retry succeeded) or D247 (escalate).
- **`D246 SMART_EXIT_RETRY ...`** — recovered after retry. No action.
- **`D247 SMART_EXIT_ESCALATE ... OPERATOR INTERVENTION REQUIRED`** — close failed N=3 times. **Operator action**: manually verify position state at broker, decide whether to manual-close OR add ticker to a "do not retry" list.
- **`D231 RECON_HARD_BLOCK ...`** — Tier-1 reconciliation alert (qty / stop drift). New entries blocked by daemon. Investigate before allowing further trades.

### Halt signals

- **`D232 RECON_LETHAL ...`** — only fires when Track B daemon's lethal tier is armed (N=5+ post-D231 sessions). Force-flat-and-halt. **Operator action**: confirm broker truth, file an incident report.
- **Any uncaught exception in main loop** — investigate; the system may be in undefined state.

---

## §4 — End-of-session (16:00 ET)

The system runs the EOD pipeline automatically. Monitor for:

- `EOD RECON SUMMARY: broker_reachable=True qty_drift=0 stop_drift=0 ...` (clean) or `... qty_drift=N` (investigate)
- `Phase 0 EOD flush: trade_context=N, bar_context=N, child_fill_ticks=N, cohort_registry=N` (capture summary)
- `D261 PHASE0_SCHEMA_VALIDATION_FAILED` — any non-zero count means production code is producing malformed payloads; investigate the offending emit-site
- `D262 BOCPD_REFIT_RECOMMENDED` — when fired, run `python scripts/pretrain_bocpd_prior.py` to refresh the persisted prior
- `Bayesian EOD fit: posterior eta=... gamma=... n=N → data/reports/...json` (when N≥30)
- `EOD failsafes: D241_canceled=N D242_force_closed=N D238_recon_delta=N`
- `EOD CONSOLIDATED REPORT: data/reports/eod_<date>.json` ← **the one file that summarizes the day**

### EOD review workflow (5 minutes)

```bash
# 1. Open the consolidated report
cat data/reports/eod_$(date +%Y-%m-%d).json | jq .

# 2. Inspect the fires[] list at the top
#    Empty []     → clean session, no action
#    Non-empty    → triage each code per §5 decision tree

# 3. If Bayesian fit ran (n_observations >= 30), inspect the posterior
cat data/reports/bayesian_eta_gamma_$(date +%Y-%m-%d).json | jq '.posterior, .gates'

# 4. Note anything for the daily journal entry — bug surface, surprise behavior, etc.
```

---

## §5 — D-code triage decision tree

When `fires[]` is non-empty in the EOD report:

```
fires contains DXXX?
├─ D224 KELLY_HALVED          → No action. System self-protected after BOCPD fired.
│                                Note in journal whether the regime break was real.
├─ D262 BOCPD_REFIT            → Run `python scripts/pretrain_bocpd_prior.py`
│                                Re-deploy by next session start.
├─ D261 PHASE0_VALIDATION      → Investigate the offending emit-site (check
│                                _d261_failures dict in the writer health snapshot).
│                                Failed payloads are LOST — they don't write to Parquet.
├─ D260 SIGNING_DISAGREEMENT   → Trade flagged for manual review. Investigate
│                                whether QMP vs midpoint baseline disagreement reflects
│                                a real regime change or a data quality issue.
├─ D256 ETA_PERM_HIGH          → Capacity reduces. Halt high-tier sizing until
│                                investigated. The Bayesian estimator says we're
│                                eating our edge via slippage.
├─ D257 GAMMA_DRIFT            → Methodology paper draft halts. Cohort screen
│                                may be mis-specified.
├─ D258 HALT_RATE_HIGH         → Cohort screen too loose. Tighten + re-fit.
├─ D259 MARTINGALE_RESIDUAL    → Decomposition inadequate. Halt Paper 1 drafting.
│                                Re-examine cohort matching algorithm.
├─ D231-qty / D231-stop        → Tier-1 reconciliation drift. Investigate per
│                                the D231 hard-block discipline. Does NOT auto-arm
│                                until N=2 clean shadow sessions complete.
├─ D230-equity                 → Soft equity drift. Check unrealized vs realized math.
├─ D247 SMART_EXIT_ESCALATE    → Operator intervention required (see §3).
└─ Any new D-code              → New bug class. Continue alphabet (next is BF).
                                  Add to 26_d_code_registry.md + write a finding doc.
```

---

## §6 — Wednesday-eve Track B arming decision

After Monday + Tuesday both ship clean:

```bash
# Verify Track B arming criteria per 26_d_code_registry.md
python scripts/audit_d_codes.py | grep "D231"      # must be LIVE
ls data/reports/eod_*.json | tail -2               # last 2 sessions present
ls data/instrumentation/trade_context/             # Phase 0 captures present
```

Check each EOD report:
- `eod_recon.qty_drift_count == 0`
- `eod_recon.stop_drift_count == 0`
- No `D231-*` code in `fires[]`

If ALL pass for 2 consecutive sessions:

```bash
# Edit main.py: change ReconDaemon's `shadow_mode=True` → `shadow_mode=False`
# Update 26_d_code_registry.md with the arming date + commit hash
# Commit + push
```

Then resume normal sessions. D231 RECON_HARD_BLOCK is now LIVE — it actually blocks new entries on Tier-1 drift instead of just logging.

If ANY criterion fails: do NOT arm. Investigate the fail. Restart the 2-session count from zero.

---

## §7 — Common operator scenarios

### Scenario: D247 fires on a real position

System logs `D247 SMART_EXIT_ESCALATE TICKER: 3 retries exhausted; broker close FAILED. OPERATOR INTERVENTION REQUIRED`.

Steps:
1. Open Alpaca dashboard, confirm the position exists at broker.
2. If position EXISTS at broker: manual market-close via Alpaca UI OR via:
   ```bash
   python -c "
   from src.execution.alpaca_executor import _make_paper_client
   c = _make_paper_client()
   import asyncio
   asyncio.run(c.close_position('TICKER'))
   "
   ```
3. If position DOES NOT exist at broker (broker says closed but tracker thinks open): the bridge tracker has a phantom. Restart the trading process — it'll reload positions from broker truth.
4. File a manual_intervention_log.jsonl entry per the LIDR convention (see `data/journals/manual_intervention_log.jsonl`).

### Scenario: D224 Kelly halved repeatedly across sessions

System fires D223 → D224 every other session. Either the BOCPD prior is mis-calibrated OR the strategy has shifted regimes systematically.

Steps:
1. Check D262 status — has BOCPD been recommending refit?
2. If yes: run `python scripts/pretrain_bocpd_prior.py` to refresh.
3. If no: the underlying P&L distribution genuinely has changed. Investigate (parameter drift, market regime, calibration window too short).
4. Lower bound: D224 is a SAFETY mechanism. Halving Kelly is a feature, not a bug. The system is doing exactly what it was designed to do.

### Scenario: Phase 0 writer fails repeatedly (D261 firing)

System logs `D261 PHASE0_SCHEMA_VALIDATION_FAILED schema=trade_context payload_keys=[...]` repeatedly.

Steps:
1. Check writer health snapshot: `_phase0_health.summary.d261_failures`
2. Identify which schema is failing (trade_context, bar_context, child_fill_ticks, cohort_registry).
3. Inspect the offending emit-site in `src/execution/bridge.py` or `src/execution/alpaca_executor.py`.
4. The most common cause: a new field was added to the production code path that wasn't reflected in the Pydantic schema. Add the field to `src/analysis/instrumentation/schemas.py` + bump SCHEMA_VERSION (per the migrator pattern).
5. Re-deploy. Existing partitions remain readable via `read_with_migration`.

---

## §8 — When to write a new finding doc

Trigger conditions (any one):
- A new bug class surfaces (continue alphabet: BF, BG, ...)
- An invariant fires unexpectedly in a clean run
- A D-code is added to the registry (commit the finding doc with the registry update)
- A capability is shipped that affects operator behavior (this runbook §3-5)

Template path: `docs/research-log/<seq>_<short-name>.md` (next sequence: 43).

---

## §9 — Quick-reference command appendix

```bash
# Health check
python scripts/preflight.py

# Run full fast suite
python -m pytest -m "not slow" -q

# Run Track C state machine at deep search
HYP_MAX_EXAMPLES=10000 python -m pytest tests/property/test_bridge_state_machine.py -q --hypothesis-seed=0

# Audit D-code registry
python scripts/audit_d_codes.py

# Re-train BOCPD prior
python scripts/pretrain_bocpd_prior.py

# Profile PBT
HYP_MAX_EXAMPLES=2000 python scripts/profile_pbt.py

# Differential check (run before pushing)
python scripts/check_differential_diff.py origin/develop

# Inspect today's EOD report
cat data/reports/eod_$(date +%Y-%m-%d).json | jq .

# Inspect today's Bayesian fit
cat data/reports/bayesian_eta_gamma_$(date +%Y-%m-%d).json | jq '.posterior, .gates'

# Open the architecture deck
xdg-open docs/research-log/architecture_call_deck.html  # Linux/macOS
start docs/research-log/architecture_call_deck.html      # Windows
```

---

## §10 — When in doubt: the discipline

**The discovery rate must exceed the introduction rate.** If a session surfaces a regression caused by a patch, the next-week budget shifts from new features to discovery infrastructure (tests, invariants, canaries) until the rate recovers.

The 17/0 ratio is the operational guarantee. Maintaining it is the architecture-call vote. Until otherwise resolved, every patch ships test-first per the established discipline; every shipped bug gets a behavioral injection test + a search canary + (when AST-detectable) a static-analysis rule + a finding doc.

**The number arrives because the discipline holds.**
