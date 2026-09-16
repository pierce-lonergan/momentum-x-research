# 167 — 2026-05-19 HOTFIX — D301/D302/D303 forward-reference crash

**Session date:** 2026-05-19 evening
**Branch:** develop
**Predecessor:** [doc 166 D297/D298](166_v6_2026_05_19_d297_d298_oto_stopfill_and_exception_logging.md)
**Severity:** P0. Doc 166 commit `7df1c16` shipped this morning crashed the bot at 04:30 ET startup. **0 trades fired, 0 Discord alerts, no D91 close of yesterday's NXXT carry-overnight.**

---

## 0. What happened today

User reported "+10% today" (account equity $134k → $147k, +$12,912). But the bot's log was only 7.4 KB and stopped at 04:30 ET — the exact moment the scheduled task started it. The +$12.9k was **pure luck** — NXXT (Monday's carry-overnight position) was never closed by D91 because the bot crashed, and the price happened to rally +74% intraday.

```
04:30:34 | momentum_x | CRITICAL | D95 FATAL CRASH:
  UnboundLocalError: cannot access local variable 'trade_journal'
  where it is not associated with a value
File "main.py", line 729, in cmd_paper
    trade_journal=trade_journal,
                  ^^^^^^^^^^^^^
04:30:34 | momentum_x | CRITICAL | D221 Bug #3: ... exiting with code 90
```

The doc 166 D297 fix added `trade_journal=trade_journal` as a kwarg to `FillStreamBridge(...)` at main.py:729. But `trade_journal = TradeJournal()` was at line 1004 — **275 lines after** the use. Python's function scope made this a forward reference → `UnboundLocalError` at the first call.

D297's 16 unit tests passed because they constructed `FillStreamBridge` directly with mocks. They never exercised the full `main.cmd_paper()` import-order. **My test discipline failed.**

---

## 1. What this commit fixes

### D301 — Move `trade_journal` initialization above its use

Moved the `TradeJournal` ctor block (3 lines) from main.py:1004 to main.py:728, immediately before the `FillStreamBridge` construction. Original site replaced with a stub comment.

### D302 — Static AST guard against forward references

New test `tests/unit/test_d301_main_forward_reference_guard.py` (11 tests). AST walks `cmd_paper`, builds (name → first_assignment_line) and (name → first_use_line) maps, fails if any use precedes its assignment. Handles:
- Function parameters (bound at entry)
- For-loop / with-statement targets
- Except handler `as name`
- Imports
- **Comprehension scopes** (closed in Python 3 — don't leak names)

**This test would have caught D301 the moment doc 166 was committed.** The headline `test_trade_journal_assigned_before_first_use_in_cmd_paper` is a direct regression test for the exact 2026-05-19 crash.

### D303 — Two dormant forward-references the new guard surfaced

While building the guard, it found **2 pre-existing latent bugs** in main.py from before doc 166. Both were silenced by enclosing `try/except: pass` blocks but were real bugs:

#### D303a — `_d217_hb_commit` used at line 1253, assigned at line 1583

`alert_session_start(commit=_d217_hb_commit, ...)` at line 1253 was inside `try/except Exception: pass`. The exception was silently swallowed, meaning **the Discord session-start ping has been a no-op for an indeterminate period**. Fix: moved the git rev-parse block (1581-1591) to before line 1248.

#### D303b — `_d78_snapshots` used at line 4451, assigned at line 4724

Inside the BAR-1 exit branch (`if _bar1_pct >= 1.0`). Currently dormant under D278 `t1_next_open` (BAR-1 exits gated off). Would crash on first BAR-1 exit if anyone reverted to `bar1_legacy`. Fix: pre-initialize `_d78_snapshots: dict = {}` at the top of the Phase 3 loop.

---

## 2. Test inventory

`tests/unit/test_d301_main_forward_reference_guard.py` (11 tests):

| Category | Tests |
|---|---|
| Sanity | main.py parses, cmd_paper exists |
| Headline | trade_journal assign-before-use (the exact 2026-05-19 crash) |
| General guard | every locally-assigned-and-used name has assign ≤ first use |
| D166 kwarg specifics | 4 parametric tests: position_manager, trade_journal, fill_bridge, tranche_monitor |
| Singleton guard | trade_journal initialized exactly once (no duplicate ctor) |
| Import sanity | `import main` doesn't crash |
| Contract guard | FillStreamBridge ctor in main.py passes both D297 kwargs |

**11/11 PASS.** Broader regression: **230/230** across D301, D297, D298, D294, D295, D296, doc-163, doc-165, D91, D279, D277, D24 EOD failsafes, alpaca_client, tabpfn_shadow, D121 stream.

---

## 3. Why D297's tests didn't catch this

D297 tests constructed `FillStreamBridge` directly:
```python
bridge = FillStreamBridge(
    tranche_monitor=_make_tranche_monitor(),
    position_manager=pm,
    trade_journal=journal,
)
```

These verified the **bridge's internal logic** when given the kwargs. They could not see that `main.py:cmd_paper`, the actual caller, would fail to provide them due to forward reference.

The fix (D302) is a static analysis test that operates at the AST level on `main.py` itself — it can catch any forward reference without needing to execute the function. This is the right layer of testing for this class of bug.

---

## 4. Files in this commit

| File | Change |
|---|---|
| `main.py` | Move `trade_journal` ctor (D301), `_d217_hb_commit` block (D303a), pre-init `_d78_snapshots` (D303b) |
| `tests/unit/test_d301_main_forward_reference_guard.py` | NEW, 11 tests, AST-based guard |
| `docs/research-log/167_v6_2026_05_19_hotfix_forward_reference_crash.md` | THIS doc |

---

## 5. Verdict

**Status:** **HOTFIX SHIPPED 2026-05-19.**

Tomorrow morning (Wed 5/20, 04:30 ET):
1. **Bot will start cleanly** (no UnboundLocalError on the D297 kwargs)
2. **NXXT (still open from Monday, currently +$8,880 unrealized) will close via D91 + D295 journal recording** — first real production test of the doc 164 D295 path
3. **Any new OTO stop-fill will journal cleanly via D297**
4. **alert_session_start Discord ping will fire** (D303a fix) — operator gets equity + commit notification at 04:30
5. **The D301 guard test will catch any future forward-reference** in `cmd_paper` before it lands

### What the user saw today

| Observation | Cause | Status |
|---|---|---|
| Zero Discord reporting | Bot crashed at 04:30 before any alert path initialized | **FIXED** (D301) |
| Could have cashed out at 18% but didn't | Bot wasn't running to manage NXXT exit | **FIXED** (D301) — tomorrow bot will close it via D91 |
| +10% (+$12.9k) realized | Pure luck (NXXT rally on unmanaged carry-overnight) | n/a — but tomorrow's exit will lock in actual realized gain |

### Filed for follow-up (not in this commit)

- **The guard should run in CI** on every PR to main, not just locally. Filed.
- The general `test_no_forward_references_in_cmd_paper` could be extended to `_dispatch_agents` and other 100+ LOC functions in `src/`. Filed.
- D300 (lottery 0-pick research) still open.
