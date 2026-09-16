# 84 — Bug AT-1 fix (FAST_PATH migration) + broker-truth pull + Bug AU surfaced

**Status:** shipped 2026-04-30. Per PROMPT_07.

**Severity:** Bug AT-1 closed for FAST_PATH (the canonical AT-1 path). **Bug AU surfaced** in parallel: journal-vs-broker disagreements span far beyond what AT-1 alone explains. Bug AU resolution deferred to next session per PROMPT_07 §6.7 stop condition.

**Halt switch:** ON at session start AND end (verified).

---

## §0 — TL;DR

**Block 2 (FAST_PATH migration) — SHIPPED:**
- `src/execution/fast_path.py` gains `prepare_queue()` (pre-checks without OTO submission) + `fast_path_entry_to_verdict()` + `fast_path_entry_to_scored()` adapters
- Legacy `execute_queue()` preserved for backward compat (3 existing tests still pass)
- `main.py` FAST_PATH call site replaced (~157 LOC → ~70 LOC) with: `prepare_queue` → `bridge.execute_verdict` (which polls for broker confirmation via `_poll_for_terminal_fill`) → `post_fill_bookkeeping` (the unified helper from PROMPT_06)
- xfail `test_main_py_fast_path_records_actual_fill_not_limit` flipped XFAIL → PASS, marker removed
- D278 source-grep: **16 passed + 0 xfail** (was 15 + 1; previously 13 + 3 before PROMPT_06)
- 31 fast_path unit tests still pass
- Firewall holds bit-perfect at $0.3400

**Block 3 (broker-truth pull) — PARTIALLY SHIPPED:**
- `scripts/pull_broker_truth.py` already existed (commit `a2ae7a0`, 4/29). Refreshed with 4/29 data: 113 fills, 4/22-4/29 coverage
- Source-0 integration into `extract_prod_qty_truth.py` was **DEFERRED** because Block 3's audit surfaced **Bug AU**: journal-vs-broker disagrees on more than just price (see `docs/audits/2026-04-30_broker_truth_initial_pull.md`).

**Result vs PROMPT_07 targets:**
- ✅ FAST_PATH xfail flips to PASS (target met)
- ✅ Firewall $0.3400 bit-perfect (target met)
- ✅ 16 D278 tests + 31 fast_path tests + 27 static analysis pass
- ✅ Halt confirmed ON both ends
- ⏸ Corpus regeneration deferred (PROMPT_07 §6.7 stop condition triggered correctly)
- 🚨 **Discovery rate: 35 → 36** (Bug AU surfaced, isolated, documented)

---

## §1 — FAST_PATH migration design (Block 2)

### §1.1 Audit verdict (Block 1)

Per `docs/audits/2026-04-30_fast_path_flow_audit.md`:

> **Recommendation: Option A** (route through `bridge.execute_verdict`)
> Bridge already does broker confirmation via the SAME `_poll_for_terminal_fill` that hardens the 4 migrated paths. Bridge already constructs `ManagedPosition` with broker-truth fill price (bridge.py:1137). Bridge already emits Phase 0 ChildFillRow + TradeContextRow per OTO leg.

The audit identified that FAST_PATH bypassed bridge for **latency reasons** (FAST_PATH fires at 9:30:01 ET, before the ~100s LLM eval pipeline produces a TradeVerdict). The fix: synthesize a verdict from the FastPathEntry's pre-market scoring data and feed it to bridge — bridge handles the rest.

### §1.2 Implementation (`src/execution/fast_path.py`)

Three additions:

1. **`prepare_queue(entries)`** — new method on `FastPathExecutor`. Same pre-flight checks as legacy `execute_queue` (circuit breaker, position exists, portfolio risk, qty sizing) but **does not submit any OTO order**. After pre-checks pass, sets `entry.status = "READY"`. The caller (main.py) then routes through `bridge.execute_verdict`.

2. **`fast_path_entry_to_verdict(fpe)`** — module-level adapter. Builds a synthetic `TradeVerdict` carrying the partial-MFCS signal that FAST_PATH produced at 9:20 ET pre-market scoring. Field mapping per the audit's §8 table:
   - ticker / entry_price / stop_loss / target_prices: direct from fpe
   - action: "BUY" (FAST_PATH is long-only by construction)
   - confidence: `clip(abs(fpe.partial_mfcs), 0, 1)`
   - mfcs: fpe.partial_mfcs
   - kelly_tier: 1
   - gap_pct/rvol/float_shares: from fpe.candidate
   - direction: "long"

3. **`fast_path_entry_to_scored(fpe)`** — module-level adapter. Builds a synthetic `ScoredCandidate` wrapping fpe.candidate with the partial MFCS.

Plus: extended `FastPathStatus` literal with `"READY"`. Extended `get_fast_path_tickers` to recognize READY as an active state.

### §1.3 Implementation (`main.py:2255-2426`)

OLD code (~157 LOC):
```python
fast_path_queue = await fp_executor.execute_queue(fast_path_queue)  # OTO submission
fast_path_fired = True
for fpe in fast_path_queue:
    # ... 145 LOC of inline registration, D215 with limit-as-fill,
    #     exit_ladder, stop_resubmitter, state_mgr, BUG-P3/P7/P10 guards ...
```

NEW code (~70 LOC):
```python
fast_path_queue = await fp_executor.prepare_queue(fast_path_queue)
fast_path_fired = True
from src.execution.fast_path import (
    fast_path_entry_to_verdict, fast_path_entry_to_scored,
)
for fpe in fast_path_queue:
    if fpe.ticker in _stopped_out_tickers: continue
    if fpe.status != "READY" or fpe.qty <= 0: continue
    if position_manager.has_position(fpe.ticker): continue
    _fp_verdict = fast_path_entry_to_verdict(fpe)
    _fp_scored = fast_path_entry_to_scored(fpe)
    try:
        order = await bridge.execute_verdict(_fp_verdict, scored=_fp_scored)
    except Exception as _fp_be:
        # fpe.status = "ERROR"; continue
    if order is None:
        # bridge returned None (rejected/expired/timeout); fpe.status = "EXPIRED"; continue
    fpe.order_id = order.order_id
    fpe.status = "FILLED"
    await post_fill_bookkeeping(
        order=order, verdict=_fp_verdict, scored=_fp_scored,
        candidate=fpe.candidate,
        path="FAST_PATH",
        ctx=_post_fill_ctx,
        log_prefix="FastPath",
    )
```

The `post_fill_bookkeeping` helper (from PROMPT_06) handles D215, journal record_fill, exit_ladder, stop register (Bug AS hardened), state_mgr update, BAR-1+D278 gate. Same canonical path as PHASE2_BUY/RESCAN/VWAP.

### §1.4 What this closes

- **Bug AT-1 (FAST_PATH limit-as-fill)** — D215's fill_price now comes from `order.filled_avg_price` (broker-confirmed), not `fpe.entry_price` (the limit). Verified by `test_main_py_fast_path_records_actual_fill_not_limit`.
- **Bug AT-bypass for FAST_PATH** — FAST_PATH was the 4th of 7 drifted entry paths. Now uses the unified helper. **3 long-side paths (PHASE2_BUY, RESCAN, VWAP_BREAKOUT) + FAST_PATH = 4/7 closed.**
- **Latent bug from audit §10**: FAST_PATH's old `execute_queue` only caught raised exceptions, not `status=rejected` returned non-exceptionally. Bridge handles both — Option A closes this for free.

### §1.5 What this does NOT close

Still drifted (deferred per PROMPT_07 §0):
- **D207_SHORT** (main.py ~line 2729)
- **D161_FALLER_SHORT** (main.py ~line 3481)
- **D170_OBSERVATION** (main.py ~line 5118)

All three are short-side paths or special-purpose paths; long-only EP pivot doesn't need them yet. They will continue to use inline post-fill logic until a future session migrates them.

### §1.6 Risk assessment (forward)

Per audit §10:

1. **Opening-dip latency vs 12s poll budget**: FAST_PATH's limits are dip-from-premarket prices that may take >12s to walk down. Bridge auto-cancels on partial fill (Bug V). Mitigation deferred until production exercises this; could parametrize `max_polls`/`poll_interval_s` per call. Low-priority because halt is ON.
2. **D101 spread filter**: FAST_PATH operates on high-RVOL gaps with potentially wide 9:30:01 spreads. Bridge's D101 might reject. D125 conviction widening helps when MFCS ≥ buy threshold. Risk also gated by halt.
3. **Double portfolio-risk check**: FastPathExecutor.prepare_queue calls portfolio_risk; bridge calls it again. Idempotent today; if it ever becomes non-idempotent, this is a known callout.

Per the audit: "Safety net: the xfail at test_d278_exit_policy.py:253 auto-validates the D215 fill-price contract on landing per doc 83 §3.5." That xfail flipped to PASS this session.

---

## §2 — Broker-truth pull (Block 3)

### §2.1 Surprise discovery: script already exists

`scripts/pull_broker_truth.py` was shipped in commit `a2ae7a0` (2026-04-29 morning) during the starting-equity correction work. Output already had 371 fills covering 2026-02-19 → 2026-04-28.

PROMPT_07 §4.1 specified a slightly different schema; the existing script's schema is sufficient for tonight's needs (covers `ticker`, `side`, `qty`, `price`, `transaction_time`, `order_id`, `order_status`, `type`). Extra fields PROMPT_07 wanted (`fill_id`, `leaves_qty`, `commission`) are deferred — not blocking.

### §2.2 Refresh through 4/29

Ran `python scripts/pull_broker_truth.py --since 2026-04-22`. New activities.parquet: **113 fills**, coverage 4/22-4/29:

| date | fills |
|---|---|
| 2026-04-22 | 16 |
| 2026-04-23 | 48 |
| 2026-04-24 | 12 |
| 2026-04-27 | 5 |
| 2026-04-28 | 22 |
| 2026-04-29 | 10 |

Reconstructed 91 closed trades (FIFO pairing) with ΣP&L = -$2,008.33.

### §2.3 Bug AU discovery (CRITICAL)

When comparing the 11-row `prod_qty_truth.parquet` corpus against the broker tape, found **systemic disagreements far beyond Bug AT-1**:

| pattern | n / 11 | examples |
|---|---|---|
| EXACT match | 2 | ONMD, ATER |
| Close (qty Δ ≤ 10%) | 3 | AGPU, SCNI, SBLX |
| Significant qty drift (>10%) | 3 | MAAS (-33%), LIDR (-45%), SEGG |
| Catastrophic qty drift | 2 | XTLB (-46%), **OPFI (-99.5%)** |
| Side flip | 1 | **OGN** (journal: BUY 999 @ $11.25; broker: sell_short 666 @ $13.18) |

**OGN is a 2-bug case**: AT-1 (limit-as-fill) AND side mis-attribution (likely from D161_FALLER_SHORT path that's still drifted).

**OPFI is the worst**: journal recorded buy qty=2182 but broker has buy qty=11 (and sells totaling 2182). Either the journal recorded INTENT not REALITY, OR the position was acquired across multiple sessions and we're missing earlier broker_truth.

Per PROMPT_07 §6.7 stop condition, **Source-0 integration into `extract_prod_qty_truth.py` was DEFERRED**. Regenerating the corpus right now would silently flip OGN's side, change XTLB qty by 46%, change OPFI qty by 99.5%, etc. That would corrupt every downstream calibration without any acknowledgment.

Full Bug AU finding doc: `docs/audits/2026-04-30_broker_truth_initial_pull.md`.

### §2.4 Forward sequence update

PROMPT_07 projected γ-audit at session N+2. Bug AU adds one session:

| Session | Scope | Status |
|---|---|---|
| N (this) | FAST_PATH (AT-1 fix) + broker_truth refresh + Bug AU discovery | ✅ shipped |
| N+1 | Bug AU resolution (operator §4 decisions + corpus regen with side-aware schema if approved) | pending |
| N+2 | broker_truth backfill 02-04 + journal reconciliation | pending |
| N+3 | γ-audit on CRCA | pending |

**γ-audit slipped one session.** Framework working as designed: better one extra session than fictional analysis on the disagreed-corpus.

---

## §3 — Validation summary

| Check | Result | Status |
|---|---|---|
| `test_d278_exit_policy.py` | **16 passed + 0 xfailed** (was 15 + 1) | ✅ FAST_PATH xfail flipped + marker removed |
| `test_fast_path.py` | 31 passed | ✅ legacy execute_queue preserved |
| Static analysis (full) | 27 passed + 0 fail | ✅ no new silent-handlers (refactor REMOVED ~6 from main.py) |
| Replay firewall | Σ\|Δ\| = $0.3400 bit-perfect | ✅ |
| main.py LOC | 7788 → 7633 (-155) | ✅ |
| Halt switch | ON at start AND end | ✅ verified |

---

## §4 — Operator decisions queued

Carried + new:

1. **POLYGON_API_KEY rotation** (still pending from 4/29)
2. **Bug AU resolution policy** (NEW, blocks N+1):
   - Qty mismatch policy: trust broker always, trust journal always, or hybrid?
   - Side-aware corpus schema: add `side` column?
   - OGN handling: drop, separate-shorts subset, or replace with broker truth?
   - Multi-leg buys: aggregate to one row or expand?
   - Cross-session lots (OPFI): investigate phantom-position-or-not
3. **Broker-truth backfill 2026-02 to 2026-04** (still queued for N+2)
4. **Databento ~$50** (still queued for N+3)
5. **D207_SHORT, D161_FALLER_SHORT, D170_OBSERVATION migrations** (still deferred)

---

## §5 — Discovery rate

35 → **36**.

Bug AU is the kind of finding PROMPT_07 §6.7 explicitly anticipated as a stop condition. The session honored it: pulled the data, surfaced the disagreement, refused to commit corpus regeneration on the surface comparison alone, documented the finding for next-session resolution.

Both stop-condition discipline and the Option A audit verdict from Block 1 ARE the framework working. The aggressive scope (PROMPT_07's "ship Block 2 + Block 3 in one session") flexed correctly: Block 2 shipped clean; Block 3's discovery scoped down its own deliverable rather than ship over a known disagreement.

---

## §6 — Status checklist

- ✅ Pre-session §1 verification (halt, HEAD, firewall, LOC counts) all passed
- ✅ Block 1: FAST_PATH audit doc shipped (`docs/audits/2026-04-30_fast_path_flow_audit.md`)
- ✅ Block 2.1: `prepare_queue` + adapters in `fast_path.py`
- ✅ Block 2.2: main.py FAST_PATH call site migrated (-155 LOC)
- ✅ Block 2.3: D278 xfail flipped + marker removed
- ✅ Block 3.1: `pull_broker_truth.py` refresh executed
- ✅ Block 3.2: Bug AU finding doc shipped (`docs/audits/2026-04-30_broker_truth_initial_pull.md`)
- ⏸ Block 3.3 (Source-0 integration): DEFERRED per PROMPT_07 §6.7
- ✅ Block F: this doc + commit + push
- ✅ Halt switch: ON at end (verified)
