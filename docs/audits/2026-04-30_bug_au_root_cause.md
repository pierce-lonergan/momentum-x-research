# 2026-04-30 — Bug AU Block 2 root-cause audit (PROMPT_08 §5)

**Status:** AUDIT ONLY (no code edits). Repo HEAD `3fb9f9a`.

---

## §1 — OGN side flip → **H1 CONFIRMED**

`data/journals/journal_2026-04-27_122958.jsonl` carries two FAST_PATH entries for OGN with `direction="long"`, `fill_qty=999`, `fill_price=11.25`. **No journal entry has `direction="short"` for OGN.**

Broker tape rows 344-348: two distinct OTO order_ids at 13:32:04 and 13:33:25 ET (`2ebb4039…`, `659f47a8…`) — `sell_short` totaling 666 @ avg $13.18, covered 14:00:46. **No buy fill at $11.25 exists.** FAST_PATH limit never filled.

**H2 refuted**: `JournalEntry.direction` defaults `"long"` (`trade_journal.py:219`), but D161 mutates `_j_ent.direction = "short"` at `main.py:3421-3424`. If D161 had run, mutation would persist.
**H3 refuted**: `alpaca_executor.py:178/304-318` routes `_is_short=True` → `submit_oto_short_order` → `side="sell"` → broker auto-derives `sell_short`.

**Mechanism**: FAST_PATH (pre-AT-1) wrote a phantom long at limit (Bug AT-1). The actual short at 13:32 came from a path that did **not** touch the journal — neither D161 nor D207 (D207 writes its own `phase="D207_AGGRESSIVE_SHORT"` entry per `main.py:2715-2734`; not present). Likely a state-recovery/external trigger.

**Post-AT-1 status**: `prepare_queue` no longer writes D215 at submit, closing OGN's specific phantom-long sub-cause. **Structural collision still latent**: `execution_recorder.py:119-123` keys journal lookup on `ticker + action ∈ {BUY,STRONG_BUY,SHORT}` (direction-blind). A later short on a ticker with an existing long entry mutates that entry. Combined with `post_fill_handler.py:145` hardcoding `side="buy"`, D215 remains vulnerable to side mis-attribution from any short path routed through the unified helper.

---

## §2 — XTLB qty mismatch → **H-A; H-C REFUTED**

Journal: phase=RESCAN, `fill_qty=3013, fill_px=$3.49, status=filled`. Broker: 1 buy `1616 @ $3.49`, 2 sells totaling 3013. **Price matches** — not Bug AT-1; pure qty drift.

XTLB only appears on 4/29 across the entire 2/19-4/29 tape → **H-C refuted**.

OTO requested 3013, broker partial-filled 1616. Bug V auto-cancel killed the residual. The bridge's terminal poll at `bridge.py:962-1031` is supposed to overwrite `order_result.qty = terminal_filled_qty`, but the journal recorded 3013 — meaning the terminal status reported `filled` with `filled_qty=3013` (or the partial-fill reconciliation skipped). Downstream tranche taker then issued sells against 3013, the broker covered the diff. **H-A.**

---

## §3 — OPFI qty mismatch → **H-B; H-C REFUTED**

Journal: `fill_qty=2182, fill_px=$9.62, status=filled`. Broker: buy `11 @ $9.62`, sells totaling 2182. OPFI only appears on 4/29 → **H-C refuted**.

99.5% qty drift makes H-A implausible (no broker would partial-fill 0.5%). Broker accepting 11 then later supplying 2182 sells implies **phantom inventory** — Alpaca labels them `sell` (not `sell_short`) but functionally net into a naked short the broker covered internally. Likely cause: stale `ManagedPosition` from a state-recovery file claiming 2182 OPFI shares the broker never delivered. **H-B confirmed.**

---

## §4 — Bug AU upper-bound count

Broker tape 2/19-4/29: **5 distinct (date, symbol) `sell_short` trades**: IOVA, NVTS, VIR (2/25), EEIQ (3/30), OGN (4/27). Of these:
- **OGN (4/27)**: journal `direction="long"` — **CONFIRMED mismatch**.
- **IOVA, NVTS, VIR, EEIQ**: no journal files exist for those dates — untestable.

**Upper bound: 5. Confirmed: 1. Untestable: 4.** True count likely 1-5.

---

## §5 — D161_FALLER_SHORT migration scope → **<100 LOC**

D161 inline post-fill block: `main.py:3419-3504` ≈ 85 LOC (D215, journal mutate, state_mgr, stop_resubmitter). Migration:
- Remove ~85 inline → ~10 LOC at call site.
- **Required helper change**: `post_fill_handler.py:145` hardcodes `side="buy"`; line 177 keys `action == "BUY"`. Must derive side from `verdict.direction` and broaden lookup (~20-30 LOC).

**Net delta: -50 to -55 LOC.** Under 100.

---

## §6 — Recommendation: **DEFER to N+2**

Eligible by LOC, but three blockers argue defer:

1. **Helper side-mis-attribution fix is prerequisite, not the migration.** Hardcoded `side="buy"` at `post_fill_handler.py:145` propagates the OGN-class defect to the unified path if D161 migrates first.
2. **Journal-lookup at `execution_recorder.py:119-123` is direction-blind.** Two-path same-ticker collisions silently mutate each other's records. Migration without this fix relocates the OGN failure mode rather than closing it.
3. **PROMPT_08 §5 already carries corpus regen + side-aware schema decisions.** Migrating D161 before those decisions risks rewriting twice.

**Recommended N+2 sequence**: (a) harden `post_fill_handler` for direction-aware side; (b) fix `execution_recorder` direction-aware lookup; (c) add unit test reproducing OGN two-path collision; (d) migrate D161 + D207 + D170 in one sweep (~150 LOC delta combined, unified fix).

---

## §7 — Discovery rate

36 → **37**. New: latent side-attribution landmine in the unified helper (`post_fill_handler.py:145` hardcoded `"buy"`; `execution_recorder.py:119` direction-blind lookup). Blocks safe D161 migration.
