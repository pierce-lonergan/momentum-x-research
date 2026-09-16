# 272 — The Diamond Pass: the flawless layer shipped, the concurrent-experiment foundation seeded

**Author**: Claude (Fable 5 era) | **Date**: 2026-06-10 (evening) | **HEAD: `2891a67`** — deploys at the next boot (04:30, files-on-disk; the 16:00-exited bot's next session runs this tree; a forced near-midnight restart would only idle until 04:00 premarket and risk same-day session-state edges — no-drawer intent met). Final gauntlet on the merged tree: **compileall OK · 116/116 consolidated gate · 297-module import graph clean**. Residual (standing protocol): full suite + tomorrow's 16:01 Adversary run post-deploy on the record.

## The merge train (six commits, all hostile-reviewed, risk-tagged, one-line rollbacks)
| sha | build | class |
|---|---|---|
| `1ad617f` | **B1 spine**: `trace_lineage.py` — the HWH forensic is now one command (proven) + A6 smalls (operator_pulse exit-mask; watchlist pre-registered median verdict) + **B3 drift forensics**: 6/4 ledger-broker delta decoded = avg-entry-vs-FIFO + cross-midnight day-attribution, **not integrity failures** (NEXR matches the broker exactly) — the cutover case is now explained, not just counted | DORMANT-C |
| `a0b8811` | **C2 diamond seed**: `src/research/event_bus.py` (read-only typed view over the durable streams, spine-keyed) + `Experiment` interface/registry/isolated fan-out runner + reference experiment whose **first real run independently re-derived the HWH phantom (drift −1039.49)** + nightly hook @19:30. A new experiment is now a registration, not a pipeline | DORMANT-C |
| `744d843` | **D2+D3 levers, priced and OFF**: `MOMENTUM_EMPTY_NOT_BEAR` (absence-fail-open at 0.5× via doc-178 machinery at D200-E4 + D204×3; conservative classification — infra-fault=block, bearish-always-wins, weak-BULL=data; `DOWNGRADED_ABSENCE` VLL emits) + `FAST_PATH_DIP_OVERRIDE_PCT` (all-class dip override, garbage-safe). **No-op when OFF pinned by a 17-case both-ways grid** (50 tests) | FLAG-B |
| `e13e543` | **A1 VLL exhaustive**: 52 emit sites; 26+ gates wired; three truth-over-plan finds (**MAIN-D204 was never wired by doc 268** — a live killer-class hole; the SUBMITTED emit orphaned every instant fill; VWAP/RESCAN had 10 replicated unwired gates). Zero production control-flow changes (verified). Coverage pin test guards the map | DORMANT-C |
| `b8eef0a` | pin-map sync (the semantic-merge check caught the levers/VLL count drift) | INERT-D |
| `2891a67` | **A2 paging**: `incident_pager.py` (cursor+dedup+rate-cap, **DRY unless `INCIDENT_PAGER_ARMED=1`**, 6/6 self-test against the real bus — self-proving from birth) + **6 detectors now emit** (RECON_LETHAL±SHADOW, QTY_GHOST, HEDGE_VIOLATION, EMERGENCY_STOP_FAILED, CB_TRIP, HEDGE_WATCHER_DOWN×2) through never-raises once-per-session wrappers (verified) + `src/ops/synthetics.py` shared namespace + `tests/conftest.py` (pytest was leaking 16 fake CRITICALs into the live bus — quarantined). **The EPSM 700-silent-ticks class ends the moment Pierce arms it** | LIVE-A-adjacent |

## Chip register (net direction: DOWN)
**Closed today (9)**: VLL coverage 3/26→exhaustive · paging gap (built+self-proving) · operator_pulse exit-mask · watchlist verdict line · synthetic-namespace · test-bus pollution (new find, fixed same hour) · MAIN-D204 emit hole (new find, fixed) · SUBMITTED instant-fill orphan (new find, fixed) · spine/trace_lineage. **Carried with owners**: rejection-grading heartbeat (A3 — partially eased, gate_graded_n woke 6/10), logs retention (A4), fill-capture completeness assert (A5), stop_widening disposition, registry self-verify, Hypothesis I12 oracle (counterexample preserved), raw_fills post-bell backfill, B2 unified-model build (spec captured; the matrix + calibration-line design done — the module is next session's first block).

## Scorecard delta (re-graded where touched)
VLL **B− → A−** (exhaustive + pinned) · incident_bus **C → B+** (consumer built, armable, self-proving; A on arm) · recon_daemon **B → B+** (voice connected to the siren) · health_check **B+ → A−** (shared namespace; self-verify still chipped) · ShadowGrader **B+ → A−** (sys-fix + ledger + experiments + pager riding one slot) · the substrate enters ungraded (day 0).

## PIERCE'S CHECKLIST (everything is one line each)
1. **Arm the pager**: `INCIDENT_PAGER_ARMED=1` (+ optionally register the documented 5-min loop for true ~60s latency; the 19:30 ride-along alone = up-to-24h worst case).
2. **Levers** (priced, OFF): `MOMENTUM_EMPTY_NOT_BEAR=1` — the no-news rockets get 0.5×-size tickets (VLL `DOWNGRADED_ABSENCE` grades it nightly); `FAST_PATH_DIP_OVERRIDE_PCT=0.0` — decision-price entries (doc-265: 94% vs 26% fill).
3. Standing: the three doc-266 flip lines · isolation keys · D200/D204 dollar brief accumulating nightly · ledger-cutover go/no-go after the ≥5-session shadow (3 exact-zero days + drift mechanisms now named) · rot dispositions.

**Not one number bent.** The Focus got its diamond pass; the experiment substrate means the next hundred ideas are registrations, not pipelines.
