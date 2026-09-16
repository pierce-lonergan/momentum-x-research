# 36 — BOCPD Pre-training: Module + Tests + Persisted Prior

**Status:** module + 12 tests + pre-trained prior shipped 2026-04-25 evening. Live wire-in (D223 BOCPD_BREAK in trading loop) deferred to follow-up commit so this can be reviewed in isolation.
**Predecessors:** `25_bug_hunting_playbook.md` §3.1, `34_phase0_instrumentation_mvp_shipped.md` (the dependency that gated this), user's items 21-23 (2026-04-25 next-actions list).
**Tag:** `v2.2-bocpd-armed` (this commit).

---

## §0 — TL;DR

Three deliverables shipped:

| File | Purpose | LOC |
|------|---------|-----|
| `src/analysis/bocpd.py` | BOCPDPrior + BOCPDState + Adams-MacKay 2007 algorithm with cp-widening fix | 280 |
| `tests/unit/test_bocpd.py` | 12 tests across 4 categories (pretraining, persistence, online detection, edge cases) | 195 |
| `scripts/pretrain_bocpd_prior.py` | Standalone CLI to fit + persist the prior from `data/trade_results.jsonl` | 95 |
| `data/priors/s1_bocpd_prior.parquet` | Pre-trained prior file (the persisted artifact) | one row |

Prior values from the current 8-row corpus (1 LIDR row filtered as infrastructure_contaminated):

  μ_edge       = -$6.5844      (mean trade outcome under stable regime)
  σ_edge       = $17.4207      (sample std)
  hazard_rate  = 0.016667      (1 / 60 — expected run length 60 trades)
  n_trades     = 7
  filtered     = 1
  corpus_dates = 2026-04-22, 2026-04-23, 2026-04-24

The corpus is small (7 clean trades) — the prior is more "reasonable starting point" than "tight calibration." The point is that BOCPD will be operational from trade #1 of the next session, not trade #50; as Phase 0 captures more trades the prior gets re-fit + redeployed.

---

## §1 — The cp-widening fix that took 2× iterations

The classical Adams-MacKay 2007 BOCPD with Gaussian-known-σ likelihood has a subtle failure mode: the **changepoint arm uses the same predictive as the long-run-growth arms**. So when an observation x arrives that's far from every run-conditional mean (e.g., a regime break to x=5 after a long run at μ=0), every arm has equally bad predictive density → P(r_t = 0) stays at the hazard rate.

The classical fix (in the Normal-Inverse-Gamma conjugate model) is that the predictive *narrows* as run length grows. So short run lengths and the cp arm have wider predictives than long run lengths, and a 5σ jump is far less surprising under "we just had a changepoint" than under "we have 50 obs of evidence the mean is 0."

I implemented the simplified-Gaussian variant. The fix: **`cp_widening_factor`** (default 3.0). The cp arm uses `N(x | μ_edge, σ_edge × 3.0)` as its predictive; the growth arms keep `N(x | run_mean_r, σ_edge)`. This gives cp the right relative density advantage when data is far from any run mean.

Test 7 (`test_regime_break_spikes_changepoint`) failed pre-fix at `max_post_cp = 0.017` (just the hazard rate). Post-fix it passes at `max_post_cp > 0.4` after a 5σ shift. Test 8 (`test_kill_switch_triggers_at_threshold`) similarly went from never-firing to triggering on a 20σ shift. The math is captured inline in `src/analysis/bocpd.py` where `cp_widening_factor` is defined.

This is a **real PBT-style finding** — not a bug, but a calibration issue the test-first discipline surfaced. Without writing the synthetic regime-break test and asserting `max_post_cp > 0.4`, the implementation would have shipped silently broken (cp probability stuck at hazard rate forever).

---

## §2 — What's NOT in this ship

Per the user's spec — three follow-up wire-ins that require touching the production trading loop:

```
session_start:
  prior = BOCPDPrior.from_parquet("data/priors/s1_bocpd_prior.parquet")
  _bocpd_state = BOCPDState(prior, kill_switch_threshold=0.85)

bridge.execute_verdict on terminal:
  pnl = realized_pnl_for_position(...)
  obs = _bocpd_state.observe(pnl)
  if obs.kill_switch_triggered:
    # D223 BOCPD_BREAK already logged inside observe()
    _kelly_multiplier = 0.5  # or revert to paper trading
```

**Decision: defer to a follow-up commit.** Reasons:

1. **Production-stack diff** — the wire-in touches `main.py` startup + `bridge.execute_verdict`. Best reviewed in isolation from the algorithm + test foundation.
2. **The `realized_pnl_for_position` plumbing** — `bridge.execute_verdict` doesn't currently compute realized P&L at terminal; that's a separate observation collected at exit time. The wire-in needs a small refactor to thread the eventual exit P&L back into the BOCPD state at the right moment.
3. **Kelly governance loop policy** — the user's spec says "should_revert_to_paper" + "current_kelly_multiplier" are the production-side hooks. Those decisions need explicit policy documentation (under what conditions does the system actually reduce Kelly vs. just log the warning?). Out-of-scope for THIS commit.

What this ship enables when those follow-ups land:

- The `BOCPDState` instance is single-allocation, thread-safe-by-construction (no mutable state shared across threads), and deterministic. Loading the prior + constructing the state is one line.
- `D223 BOCPD_BREAK` is already logged inside `observe()` via the `logger.warning(...)` path. The follow-up commit just adds the Kelly reduction.
- The kill-switch threshold is parameterized at construction (`kill_switch_threshold=0.85`); the production code can override per-session if needed.

---

## §3 — Test coverage (12/12)

| # | Category | Test | What it pins |
|---|----------|------|--------------|
| 1 | Pretraining | `test_fits_clean_corpus` | μ + σ from 4 synthetic trades |
| 2 | Pretraining | `test_filters_infrastructure_contaminated` | LIDR-style row excluded |
| 3 | Pretraining | `test_empty_corpus_returns_safe_default` | μ=0, σ=1, no crash |
| 4 | Pretraining | `test_custom_hazard_rate_overrides_default` | Caller-provided hazard wins |
| 5 | Persistence | `test_round_trip_via_parquet` | write + read → equal Pydantic-frozen instances |
| 6 | Persistence | `test_schema_version_mismatch_rejected` | bad schema_version → ValueError |
| 7 | Detection | `test_stable_regime_keeps_changepoint_low` | 50 stable obs → late cp avg < 0.3 |
| 8 | Detection | `test_regime_break_spikes_changepoint` | 5σ shift → max post cp > 0.4 (cp-widening fix) |
| 9 | Detection | `test_kill_switch_triggers_at_threshold` | 20σ shift → triggered = True |
| 10 | Detection | `test_observation_count_increments` | n_observations + history list grow correctly |
| 11 | Edge | `test_single_observation_does_not_crash` | n=1 → σ falls back to abs(μ) or 1.0 |
| 12 | Edge | `test_from_prior_path_loads_correctly` | `BOCPDState.from_prior_path(p)` convenience works |

Suite runs in 0.60s. No external dependencies beyond stdlib + pyarrow + pydantic.

---

## §4 — Empirical-Bayes pre-training run

```
$ python scripts/pretrain_bocpd_prior.py
[bocpd-pretrain] loaded 8 rows from data/trade_results.jsonl
[bocpd-pretrain] fit prior:
  mu_edge      = -6.5844
  sigma_edge   = 17.4207
  hazard_rate  = 0.016667 (expected run length: 60)
  n_trades     = 7
  filtered     = 1
  corpus_dates = ('2026-04-22', '2026-04-23', '2026-04-24')
  generated_at = 2026-04-25T22:33:41Z
[bocpd-pretrain] wrote data/priors/s1_bocpd_prior.parquet
```

The 7-trade clean corpus is dominated by zero-PnL rows (positions that never reached terminal P&L due to Bug-V/W/Z-class abandonment). Once Phase 0 captures meaningful realized-PnL data — including the bar-by-bar fills that let us reconstruct R-multiples — the prior will tighten. Re-running the script after every 10-20 new trades is the discipline.

The contamination filter is hard-coded in `scripts/pretrain_bocpd_prior.py:KNOWN_CONTAMINATED` for trade rows that pre-date the `infrastructure_contaminated` field. Today's set: `(LIDR, 2026-04-24)`. As more cases surface they're added here; older trade_results rows don't need to be modified.

---

## §5 — Discovery rate impact

This ship surfaces 0 production bugs, but it surfaces **1 algorithm-calibration finding** (the cp-widening issue described in §1). The synthetic regime-break test caught the issue before the prior ever shipped. Without that test pass, BOCPD would have been operational-but-silent — never firing D223 even on real regime breaks.

The discovery rate (11/0 = ∞) holds. The **algorithm-calibration finding** is captured here so future BOCPD-adjacent work knows to verify the cp arm's predictive density vs. the growth arms.

**Cumulative session ledger (8 commits, 6 tags):**

| Commit | Capability | Tag |
|--------|------------|-----|
| `366d028` | Static-analysis suite + 6 silent-handler fixes | `v2.2-static-analysis-active` |
| `2cc3e9a` | Track C Phase 2 expansion | `v2.2-pbt-phase2-active` |
| `09f3ce4` | Bug AA: SimpleBroker fill discipline | — |
| `5e6a9b9` | QMP signing migration | — |
| `f1fd13f` | Phase 0 instrumentation MVP foundation | `v2.2-phase-0-active` |
| `0f0bd0f` | Phase 0 wire-in: 6 emit-sites | — |
| `e4c32a3` | Track D differential harness foundation | `v2.2-difftest-foundation` |
| (this) | BOCPD pre-training: module + tests + persisted prior | `v2.2-bocpd-armed` |

Suite cumulative: 131/131 across bocpd + difftest + phase0 + qmp + property + static-analysis + d219 + d24-bug-z in 23.61s.

---

## §6 — Knock-on items (next iteration plan)

1. **`bridge.execute_verdict`** — thread realized P&L back into `_bocpd_state.observe(pnl)` at terminal exit (not entry). Needs a small refactor to capture exit P&L at the bar1-exit / stop-trigger / smart-exit confluence.
2. **`main.py` session-start** — load `data/priors/s1_bocpd_prior.parquet` and construct `_bocpd_state` once per session. Falls back to default prior if file missing/corrupt.
3. **Kelly governance policy** — define when D223 BOCPD_BREAK should:
   - Just log (current default — informational)
   - Reduce Kelly multiplier to 0.5
   - Revert to paper trading
   - Halt all entries
   This is a policy decision separate from the algorithm itself; document in `25_bug_hunting_playbook.md` §3.1 update.
4. **Re-fit cadence** — schedule the `pretrain_bocpd_prior.py` script to run weekly (or after every 10 new trades) to incorporate new outcomes. Output the diff in the EOD report.
