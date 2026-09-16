# D218 Item 1: Silent Exception Handler Sweep

**Date:** 2026-04-11
**Scope:** Targeted sweep of TRADE_CORRECTNESS silent handlers in main.py

## Handler Census (before sweep)

```
Total silent handlers (except Exception: + pass):
  main.py:                81
  src/core/orchestrator.py: 0
  src/execution/bridge.py:  1
  src/execution/position_manager.py: 1
  src/analysis/trade_journal.py:     0
  TOTAL:                  83
```

## Classification

| Category | Count | Action |
|----------|-------|--------|
| TRADE_CORRECTNESS | 24 | **Converted in this sweep** |
| OBSERVABILITY | ~45 | Deferred to full sweep |
| NON_CRITICAL | ~14 | Deferred to full sweep |

## Conversions (24 handlers)

All converted from `except Exception: pass` to `except Exception as e: logger.warning("D218: <context> failed: %s", e)`.

Categories of converted handlers:
- **state_persistence** (15): `state_mgr.save()` calls throughout Phase 2, Phase 3, Phase 4
- **stopped_ticker_tracking** (3): `state_mgr.add_stopped_out_ticker()` + save
- **pnl_recording** (2): `record_realized_pnl()` + daily P&L updates
- **close_attribution** (2): `close_with_attribution()` result processing
- **position_removal** (1): `state_mgr.remove_position()` + save
- **execution_recording** (1): `_exec_recorder.record_execution()` or `record_close()`

## Not converted (deferred)

57 remaining silent handlers in main.py:
- Metrics gauge updates (Prometheus counters/gauges)
- Dashboard UI refresh
- Velocity/premarket snapshots
- Optional enrichment fields
- Cosmetic log formatting
- Agent cache operations

2 remaining in bridge.py + position_manager.py:
- Metrics gauge sync in `add_position()` and close fallback path

All deferred handlers are OBSERVABILITY or NON_CRITICAL — their failure affects monitoring but not trading correctness.

## Verification

- Compile: clean
- 38 regression tests pass
- Dry-run: zero "D218:.*failed" warnings during normal startup (converted handlers don't fire under normal conditions)
- All conversions use lazy `%s` formatting (not f-strings) per engineering standard
