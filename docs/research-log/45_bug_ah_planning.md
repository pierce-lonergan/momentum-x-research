# 45 — Bug AH planning: bridge.py UnboundLocalVariable cleanup

**Status:** scheduled for **tonight 2026-04-27, post-close (after 16:00 ET)**.
**Severity:** **CRITICAL-PATH** — same module as Bug AF.
**Source:** Pyright baseline `_pyright_baseline.json` lists 4 `reportPossiblyUnboundVariable` errors in `src/execution/bridge.py`, the highest-priority entry in the baseline-prioritization table.
**Predecessors:** `44_bug_ag_track_b_unbound.md` §7 (the pyright defense layer surfaced 24 latent issues; this is the top critical-path cleanup).

---

## §0 — TL;DR

Bridge has 4 `reportPossiblyUnboundVariable` findings, all on the post-trade-close enriched path:

| Line | Variable | Context |
|------|----------|---------|
| 1022 | `_now` | `traded_entry_ts=_now` in `find_cohort_peers` call |
| 1030 | `_now` | `traded_entry_ts=_now` in `build_cohort_rows` call |
| 1221 | `_close_aio` | `_close_aio.ensure_future(post_trade_close_enriched(...))` |
| 1226 | `_hold_min` | `hold_minutes=_hold_min` in `post_trade_close_enriched` call |

All four follow the Bug AF/AG pattern: defined inside a conditional branch, referenced later unconditionally. Production has not crashed because the surrounding `try:` block catches the `UnboundLocalError` and logs at WARNING — meaning the post-trade enrichment SILENTLY DROPS for some fills, losing Phase 0 attribution data.

Fix scope: 4 hoist + None-init operations, mirroring the Bug AF fix shape exactly (`_cand = None` before the `if`-branch).

---

## §1 — Test-first protocol per CONTRIBUTING.md §"Test-first protocol"

Each of the 4 fixes follows the 4-defense protocol. For Bug AH overall:

1. **Behavioral injection test** — extend `tests/integration/test_phase0_production_lifecycle.py` to call the post-trade-close enriched path with the input shapes that hit each conditional branch off-path (forces `_now` / `_close_aio` / `_hold_min` to be unset). Assert that `post_trade_close_enriched` is called with concrete values, not `UnboundLocalError`.
2. **Search canary** — N/A (these are not PBT rule mutations; the integration test is the appropriate behavioral coverage).
3. **Static-analysis rule** — already in place: `pyright reportPossiblyUnboundVariable` from Bug AG. Decrementing the bridge.py count in `_pyright_baseline.json` from 4 → 0 IS the regression gate.
4. **Finding doc** — `docs/research-log/46_bug_ah_bridge_unbound.md` (§0–§8 per the convention).

---

## §2 — Sequence of atomic operations (one commit per fix)

Following the discipline that scales because each fix is small:

| Step | Action | Verification |
|------|--------|--------------|
| 1 | Read each of lines 1022, 1030, 1221, 1226 in context | Confirm the 4 instances are independent (no shared variable) |
| 2 | Write the 4 behavioral test cases first (one per variable) | All 4 fail against current bytecode |
| 3 | Apply the 4 fixes (None-init + hoist) | All 4 tests pass |
| 4 | Run `pyright src/execution/bridge.py` → confirm 0 findings | bridge.py count drops 4 → 0 in baseline |
| 5 | Update `_pyright_baseline.json`: remove `src/execution/bridge.py` from baseline (or set to 0) | Pyright gate still passes |
| 6 | Run `pre-commit run --all-files` | All gates green |
| 7 | Commit with message: `Bug AH: bridge.py UnboundLocalVariable cleanup (4/24 from pyright baseline)` | |
| 8 | Push to origin | |

---

## §3 — Why tonight, not now

- **Market is open.** Touching `bridge.py` while trading is live risks introducing a regression on the active execution path. The Phase 0 integration test would catch it, but losing a session to a regression-induced kill-switch is the exact "patches introduced regressions" outcome we measure against.
- **No active impact.** The 4 findings only surface UnboundLocalError under specific input shapes; production has been running with them for weeks without symptom. Tonight's quiet window is when we touch this code.
- **Cleanup discipline.** Per CONTRIBUTING.md: "The discovery rate must exceed the introduction rate." Bug AG's pyright defense surfaced 24 issues; we should fix them in priority order, one atomic commit per file, instead of a big-bang sweep.

---

## §4 — Acceptance criteria for tonight's session

- [ ] All 4 behavioral test cases written first (and failing initially)
- [ ] All 4 fixes applied
- [ ] `pyright src/execution/bridge.py` reports 0 `reportPossiblyUnboundVariable`
- [ ] `_pyright_baseline.json` decremented appropriately
- [ ] `_total_at_baseline` updated: 24 → 20
- [ ] All static_analysis tests pass
- [ ] Phase 0 integration test still passes
- [ ] CI green on the push
- [ ] Bug AH finding doc written (`46_bug_ah_bridge_unbound.md`)
- [ ] Discovery rate metric updated: 19/0 → 20/0

---

## §5 — After Bug AH (the queue)

| Bug | File | Count | Trigger |
|-----|------|-------|---------|
| AI | `src/core/orchestrator.py` | 4 | Tomorrow morning |
| AJ | `src/execution/exit_intelligence.py` | 3 | Tuesday evening |
| AK | `src/execution/position_manager.py` | 1 | Quick fix |
| AL | `src/agents/deterministic_technical.py` | 2 | Quick fix |
| AM | `src/data/alpaca_client.py` | 2 | Quick fix |
| AN | `src/data/short_interest.py` | 1 | Quick fix |
| (research) | `scripts/run_meta_simulation.py` | 7 | Convenient |

Cleared sequentially, the baseline drops 24 → 20 → 16 → 13 → 12 → 10 → 8 → 7 → 0.
At cadence of 1 per day, the entire baseline clears by **2026-05-04**.

---

## §6 — Connection to the operational discipline

Bug AH is not just a code fix. It's the first practical test of:

- The **pyright baseline + ratchet workflow** — does the discipline of "decrement the count in `_pyright_baseline.json` after each fix" actually work in practice? Bug AH is the first decrement.
- The **CONTRIBUTING.md test-first protocol applied to a static-analysis-surfaced bug** — Bugs A–AF were each surfaced by a specific test or invariant; Bug AH is the first surfaced by a static analyzer (pyright). The test-first defense translates 1:1 (behavioral test + static rule + finding doc).
- The **"discovery rate exceeds introduction rate" guarantee** — pyright surfaced 24 issues in one shot. Our discipline is to clear them in priority order without introducing a regression. Bug AH's atomic commit + Phase 0 integration test verification is the proof.

A clean Bug AH ship demonstrates the complete loop: *static analyzer surfaces issue → test-first protocol catches the regression class → atomic commit ratchets the baseline → discovery rate marker advances*.
