# 53 — Bug AN: Equity estimator typo (`starting_equity` vs `_starting_equity`)

**Status:** patched 2026-04-27 evening (Tier 3 #11).
**Severity:** **MEDIUM** — D230 RECON_WARN EQUITY fired every 30s of every session for an unknown duration. No P&L impact directly, but constant warning noise + masked any LEGITIMATE equity drift signal.
**Surface:** Tier 3 audit (strategic assessment flagged "internal_estimate=$0.00 delta=$+140470.37" pattern in today's log).
**Bug class:** typo / missing public attribute — `eod_recon` did `getattr(pm, "starting_equity", 0)` but `PositionManager` only had `_starting_equity` (underscore-private). Default 0 always returned. Hidden because the test suite used MagicMock with explicit attribute setters.

## §0 — TL;DR

Today's log, every 30 seconds:

```
D230 RECON_WARN EQUITY: broker_equity=$140470.37 internal_estimate=$0.00
delta=$+140470.37 (tolerance $1.00). May reflect unrealised P&L not
marked-to-market in the internal estimate, OR a real drift requiring
investigation.
```

`internal_estimate=$0.00` is the smoking gun. The code:

```python
internal_equity_estimate = (
    float(getattr(position_manager, "starting_equity", 0) or 0)
    + float(getattr(position_manager, "_daily_realized_pnl", 0) or 0)
)
```

`PositionManager.__init__` sets `self._starting_equity = starting_equity` (with underscore). No public `starting_equity` attribute existed. `getattr(pm, "starting_equity", 0)` returned the default 0. So `internal_estimate = 0 + _daily_realized_pnl ≈ 0`.

`delta = broker_equity - 0 = broker_equity` always exceeded the $1 tolerance → D230 RECON_WARN EQUITY every 30s.

Fix: add a public `starting_equity` property to `PositionManager` (delegates to `_starting_equity`). The eod_recon caller now reads the correct value.

## §1 — Root cause

Two contributing factors:

1. **Naming-convention mismatch**: PositionManager followed Python private-by-convention (`_starting_equity` with underscore), but eod_recon's caller assumed a public attribute. The typo was a silent failure mode — no error, just a 0 default.

2. **Test fixture masked the bug**: `tests/unit/test_d230_eod_recon_invariants.py` uses `pm = MagicMock()` and explicitly sets `pm.starting_equity = 142520.59`. MagicMock auto-creates attributes, so `getattr(pm, "starting_equity", 0)` returns the explicit value — making the test pass under the broken code. **The test verified the bug, not the correct behavior.** A test against a REAL PositionManager would have failed immediately.

## §2 — Fix

Two halves:

**`src/execution/position_manager.py`** — add public read-only property:

```python
@property
def starting_equity(self) -> float:
    """Bug AN (Tier 3 #11, 2026-04-27): public accessor for the
    session's starting equity. Closes the typo gap in eod_recon."""
    return self._starting_equity
```

**`src/monitoring/eod_recon.py`** — comment-block clarifying the typo class:

```python
# Bug AN fix (2026-04-27): eod_recon used getattr(pm, "starting_equity", 0)
# but PositionManager only had _starting_equity. Always returned 0.
# Fix: PositionManager now exposes the public property.
internal_equity_estimate = (
    float(getattr(position_manager, "starting_equity", 0) or 0)  # now reads the property
    + float(getattr(position_manager, "_daily_realized_pnl", 0) or 0)
)
```

(The eod_recon code itself is unchanged — adding the property to PositionManager makes the existing code WORK as intended.)

## §3 — Aggressive testing (the "test the real thing" lesson)

`tests/unit/test_bug_an_equity_estimator.py` — 7 tests, all using **REAL** PositionManager (not MagicMock):

- `test_position_manager_exposes_public_starting_equity` — pin the public contract
- `test_starting_equity_reflects_init_value` — parametrized over 4 equity values
- `test_starting_equity_is_read_only_property` — preserves the underscore-private intent (no setter)
- `test_real_pm_in_eod_recon_does_not_trigger_d230_on_clean_state` — end-to-end: real pm + clean broker state → NO D230 (the pre-Bug-AN code WOULD have fired it)
- `test_real_pm_with_realized_pnl_added_correctly` — realized PnL adjusts the estimate
- `test_real_pm_with_actual_drift_correctly_fires_d230` — the OPPOSITE direction: real $1000 drift SHOULD fire D230 (Bug AN must not silence the legitimate signal)
- `test_pre_bug_an_typo_pattern_does_not_match_pm_anymore` — explicitly exercises the broken access pattern with the new property; should now return the correct value

Plus the existing `tests/unit/test_d230_eod_recon_invariants.py` (8 tests) still passes because the MagicMock fixtures' `pm.starting_equity = X` continues to be reachable (the property exists; the explicit setter on the mock still wins for MagicMock instances).

**Key methodological note:** the lesson here is to **exercise real classes in unit tests where possible**, not just MagicMock fixtures. The MagicMock pattern has a place (mocking expensive collaborators) but masks attribute-name typos. For Bug AN, real PositionManager is cheap and the typo-detection win is worth it.

## §4 — Discovery rate impact

| Pre-Bug AN | After Bug AN |
|---|---|
| 25 production / oracle / harness bugs surfaced + fixed | **26** |
| 0 patches introduced regressions | **0** |
| Discovery rate ratio | **26/0 = ∞** |

## §5 — Downstream effects

- **D230 RECON_WARN EQUITY noise drops to zero** for clean-state sessions
- **Real equity drift detection works again** — when broker equity actually diverges from internal, D230 fires legitimately and operator can investigate
- **The public `starting_equity` property** is now the canonical accessor; future code (analytics, EOD reports, Phase 0 instrumentation) can read it without underscore-private gymnastics
- **No D-code registry impact** (D230 was already registered)

## §6 — The pattern

Three Tier 3 bugs, three different fix shapes, but a common pattern:

| Bug | Type | Cost today | Fix LOC |
|-----|------|-----------|---------|
| **AL** | Missing input validation (data quality) | 1 lost trade (highest-RVOL setup) | ~70 + 22 tests |
| **AM** | Missing API + missing auto-discovery | 900 D230 noise lines + Bug AI deadlock context | ~150 + 12 tests |
| **AN** | Typo / naming mismatch | 900 D230 noise lines | ~10 + 7 tests |

All three are SCAFFOLDING-COMPLETE failures: the system had every piece needed to do the right thing (Pydantic validators, broker.get_orders, the equity attribute), but the wiring was missing. **The discovery infrastructure caught all three via direct operator observation, NOT via any test layer.** The PBT/static-analysis/integration-test layers are necessary but not sufficient — operator vigilance remains the seventh discovery layer.
