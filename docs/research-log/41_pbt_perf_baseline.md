# 41 — PBT Performance Baseline + Optimization Decision

**Status:** profile + decision shipped 2026-04-26 evening. **Conclusion: do not over-optimize.** Further work has diminishing returns; the current envelope is acceptable for all three operating modes.
**Predecessors:** `40_discovery_infrastructure_summary.md` §4 (test surface table).
**Tool:** `scripts/profile_pbt.py` (cProfile wrapper, reusable).

---

## §0 — TL;DR

```
HYP_MAX_EXAMPLES=200    pre-commit fast feedback   ~3s
HYP_MAX_EXAMPLES=2000   dev default               ~20s
HYP_MAX_EXAMPLES=10000  nightly deep search       ~100s
```

Profile run at 2k examples (with cProfile overhead, baseline ~3× actual): 62s total, of which **~3s (5%) is our code**. The remaining 95% is Hypothesis's example-generation + state-machine machinery — out of our control without forking Hypothesis.

**Decision: ship the profile script as a reusable tool, document the characteristics, do NOT pursue further optimization.** When the time budget exceeds tolerance (e.g., nightly 10k crosses 5 minutes), revisit.

---

## §1 — The profile (HYP_MAX_EXAMPLES=2000, cProfile-instrumented)

| Tier | Cumulative time | Notes |
|------|----------------:|-------|
| **Total runtime** | **61.6s** | cProfile overhead ~3× — actual is ~20s |
| Hypothesis engine + draw + settings | ~52s | `generate_mutations_from`, `cached_test_function`, `do_draw`, `_settings.__gt__` |
| Hypothesis check_invariants dispatch | 22.1s | per-invariant call overhead (96k checks × 17 invariants = 1.6M dispatches) |
| **Our 17 invariants combined** | **0.85s** (cumtime) | 0.05s mean per invariant per check_invariants pass |
| **Our 11 rules combined** | **~1.5s** (cumtime) | Dominated by `r1_submit_entry` (`asyncio.Runner.run` overhead) |
| SimpleBroker submit_order | 0.14s | Includes legs construction + buying-power check |
| SimpleBroker to_dict | 0.03s | Per-call serialization |

### Per-invariant breakdown (94k calls each, 2k examples)

| Invariant | tottime | per-call (µs) |
|-----------|--------:|---------------:|
| I10 no_opposite_side_orders | 95ms | ~1.0 |
| I12 position_count_matches_broker | 84ms | ~0.9 |
| I2 stop_matches_broker | 75ms | ~0.8 |
| I7 tranche_restructure_preserves_tightened_stop | 65ms | ~0.7 |
| I1 tracker_matches_broker | 59ms | ~0.6 |
| I13 no_zero_qty_open_position | 55ms | ~0.6 |
| I17 terminal_status_consistent | 54ms | ~0.6 |
| I3 no_orders_during_halt | 54ms | ~0.6 |
| I15 entry_qty_matches_request | 51ms | ~0.5 |
| I14 stop_oid_uniqueness | 50ms | ~0.5 |
| I5 cumulative_fill_bounded | 39ms | ~0.4 |
| I16 fill_price_within_quote_band | 39ms | ~0.4 |
| I11 equity_drift_bounded_per_session | 36ms | ~0.4 |
| I9 close_attempt_only_marks_closed_on_broker_2xx | 32ms | ~0.3 |
| I4 no_negative_position | 29ms | ~0.3 |
| I8 equity_conservation | 27ms | ~0.3 |
| I6 rejected_orders_canceled_at_broker | 20ms | ~0.2 |

All invariants complete in <1µs/call. The relative ordering reflects iteration scope:
- I10/I12 iterate `broker.orders.items()` (largest collection)
- I2/I7/I1/I13/I14/I15 iterate `tracker_positions` (medium)
- I16/I17 iterate `broker.orders` with extra filter cost
- I9/I4/I6 iterate the smallest sets (close_attempts, positions, bridge_rejected)

---

## §2 — What we can NOT optimize

Hypothesis's example generation + draw machinery is irreducible at the framework level. The 22s in `check_invariants` includes per-invariant function-call overhead (1.6M calls × ~14µs framework dispatch each), which we cannot reduce without rewriting Hypothesis's invariant model.

The 13.5s in `_settings._int_value` (3.2M calls) is Hypothesis comparing settings on every test function call — a known internal characteristic.

---

## §3 — What we CAN optimize, ranked by ROI

| Optimization | Estimated win | Complexity | ROI |
|--------------|--------------:|-----------:|----:|
| Switch SimpleBroker async → sync | 10-15% | Medium (touches all method signatures, breaks production-shape mimicry) | Medium |
| Combine I5+I16+I17 into single broker.orders walk | 1-2% | Low | Low |
| Combine I1+I2+I12+I14+I15 into single tracker walk | 1-2% | Low | Low |
| Cache `non_terminal_orders` view per check_invariants | 2-3% | Medium (requires Hypothesis hook for "check started") | Low |
| Skip invariant checks when state didn't change | 5-10% | High (requires per-rule state-tracking; risks missing bugs) | Low (risk > reward) |

**Conclusion:** the maximum realistic speedup we can extract from the OUR code surface is ~15%, and it costs us either production-shape mimicry (async signature) or invariant readability (combined walks). At current runtime tiers (3s/20s/100s) the speedup doesn't justify the cost.

---

## §4 — Decision matrix: when to revisit

Re-open this decision if ANY of:
- Pre-commit gate exceeds 10s on the fast variant (currently ~3s)
- Dev default exceeds 60s on 2k examples (currently ~20s)
- Nightly deep search exceeds 5min on 10k examples (currently ~100s)
- New invariants push the total over 30 (currently 17 — adding more would scale per-invariant cost linearly)
- Hypothesis 7.x or later releases meaningful state-machine optimizations

Until then: ship the profile tool, document the characteristics, focus engineering time on the substantive work (Phase 0 capture, Bayesian calibration, capital-side fundraise).

---

## §5 — Tool: scripts/profile_pbt.py

Run a profile any time:

```bash
HYP_MAX_EXAMPLES=2000 python scripts/profile_pbt.py
# Profile dumped to pbt_profile.prof
# Top 30 functions printed to stdout
```

Inspect interactively:

```bash
pip install snakeviz
snakeviz pbt_profile.prof
```

Or:

```bash
python -c "import pstats; p=pstats.Stats('pbt_profile.prof'); p.sort_stats('tottime').print_stats(30)"
```

The script is committed in this PR alongside this baseline doc. Reuse it whenever the §4 trigger conditions fire.

---

## §6 — Closing note

The Track C state machine is architecturally complete + performance-acceptable. The discovery rate (17 bugs surfaced this week, 5 of them PBT-discovered) more than justifies the 100s nightly cost. The marginal hour spent on per-invariant micro-optimization would be better spent on Phase 0 wire-in polish, Bayesian estimator productionization once Phase 0 captures real data, or capital-side fundraise prep.

**Antifragility ratio (17/0 = ∞) > performance ratio every time.**
