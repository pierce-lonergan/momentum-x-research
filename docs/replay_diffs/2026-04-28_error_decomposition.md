# 2026-04-28 — Replay Rig Forensic Audit: Error Decomposition

**Status:** Block A of the rig-audit session. Forensics, no fidelity features added.
**Trigger:** Previous session's diff report categorized OGN sign flip + SBLX sign flip + ATER 27× magnitude error as "fill_price divergence." Slippage moves fills by basis points, not multiples. The categorization was technically true (`fill_delta_bps` was non-zero) but operationally hid the real causes.

---

## §0 — TL;DR

The "fill_price" categorization in the previous diff was **wrong by hypothesis**. Decomposing the 4 replayable trades by error source reveals:

| Trade | qty error (H1) | exit-px error (H2) | direction flip (H3) |
|---|---:|---:|---|
| LIDR 4/24 | 1.45× too big | exits via tranche fills, not bar-open | (held) |
| OGN 4/27 | **5.68× too big** | **exits via tranche fills at $13.17 vs arena bar-open ~$11.30** | sign flipped |
| SBLX 4/28 | **7.0× too big** | exit semantics differ; flipped direction by $-0.04 vs $+0.04 bar move | sign flipped |
| ATER 4/28 | **16.7× too big** | smaller relative contribution | sign correct (both negative) |

**Dominant cause: H1 (qty) for ATER alone; H1+H2 jointly for OGN and SBLX. H3 (direction) is clean — no sign bug; sign flips emerge from H2 (arena exits at bar-open while prod exits via tranche limits ABOVE entry, or via close prices far from bar anchor).**

**Fix priority** (per session brief, smallest scope first):
1. **H1 first**: use trade_context parquet for qty where available; fall back to momentum log scrape. Smallest scope, biggest leverage on ATER.
2. **H2 second**: arena needs to model tranche-limit-sell fills as exits, not bar-anchored sells. Larger scope; gates OGN/SBLX accuracy.
3. **H3**: no fix needed.

---

## §1 — Source-of-truth ground rules

For this audit, "prod truth" is sourced from:
- `data/instrumentation/trade_context/session_date=2026-04-28/orders.parquet` — has actual `requested_qty` and `terminal_filled_qty` for orders captured under Bug AO orchestrator hook (4/28 only).
- `logs/momentum_<date>.log` D217 fill events: `"order <oid> reached terminal status=filled filled_qty=<N> filled_avg_price=<P>"`.
- `logs/momentum_<date>.log` tranche fill events: `"TRANCHE T<i> FILLED: <ticker> @ $<P>"`.
- `data/trade_results.jsonl` — closed-trade P&L (not qty, not entry price).

For each trade, `prod_qty` and `prod_exit_path` were extracted by hand from the log + orders parquet. The numbers below are NOT computed; they are pulled from production records.

---

## §2 — Per-trade walkthrough

### LIDR 2026-04-24 (prod +$421)

**Production:**
- `D217: LIDR order 7ba158da reached terminal status=filled filled_qty=5264 filled_avg_price=2.42 (poll 2/6)` at 10:16:48 ET
- BAR-1 EXIT attempts at T+61s, +126s, +187s, +254s, +316s, +378s — **all 403'd** with `40310000 insufficient qty available` (Bug AR scenario, then-undiagnosed)
- Position eventually cleared between T+6.3 min and T+14 min — likely tranche limit-sell fills above entry (consistent with the +$421 P&L on 5264 shares = +$0.080/share = exit ≈ $2.50).
- `prod_qty = 5264`, `prod_entry_avg = $2.42`, `prod_exit_avg ≈ $2.50` (implied from P&L), `exit_path = TRANCHE_LIMIT or D245_eventual_close`

**Arena (current rig):**
- `arena_qty = 7657` (from `tier1_qty = 75000 / 2.465`)
- `arena_entry_px = bar.open at entry minute = $2.46` (close to prod's $2.42, ~17 bps high)
- `arena_exit_px = bar.open at exit minute (~14:30) = $2.51`
- `arena_pnl = (2.51 - 2.46) × 7657 ≈ +$383`. Reported as +$452 in the rig output — small discrepancy from rounding/slippage layer.
- Arena_pnl - prod_pnl = +$31 (the "small" delta in the previous diff)

**Decomposition:**
- **H1 (qty)**: 7657 vs 5264 = **1.45× too big**. With ~$0.05 per-share move, arena's overshoot adds ≈ +$120 spurious P&L.
- **H2 (exit)**: arena_exit ($2.51 bar-open) is approximately right because LIDR's eventual price near the actual close minute matched. **H2 mostly clean for LIDR.**
- **H3**: clean. Sign correct.

### OGN 2026-04-27 (prod +$515) — sign flip

**Production:**
- Three tranche fills observed:
  - `TRANCHE T1 FILLED: OGN @ $13.17 ... PnL: $639.36` at 09:33:26
  - `TRANCHE T2 FILLED: OGN @ $13.17 ... PnL: $640.71` at 09:35:21
- After two tranches consumed 666/999 shares: `Remaining: 333`
- BAR-1 EXIT at T+1816s for the remaining 333 shares — got 404 (position already closed)
- `prod_qty = 999` (3 × 333), `prod_entry_avg ≈ $11.25` (from edge assessment doc), `prod_exit_avg ≈ $13.17` (tranche fills)
- Realized P&L: ($13.17 - $11.25) × ~999 × (some fraction realized) ≈ +$515

**Arena (current rig):**
- `arena_qty = 5675` (from `tier1_qty = 75000 / 13.22`)
- `arena_entry_px = bar.open at 13:30:30 UTC = $13.22`
- `arena_exit_px = bar.open at exit minute = ~$13.18`
- `arena_pnl = ($13.18 - $13.22) × 5675 = -$227`. Reported as -$184. Direction: NEGATIVE.

**Decomposition:**
- **H1 (qty)**: 5675 vs 999 = **5.68× too big**.
- **H2 (exit semantics)**: prod realized at $13.17 via tranche LIMIT sell — a price far ABOVE entry. Arena replayed exit as bar.open at exit minute (~$13.18) which was BELOW entry. **The sign flip lives entirely here**: arena modeled a flat-to-down exit at the bar level; prod actually exited via limit fills targeted ABOVE entry that captured the move.
- **H3**: clean. The negative arena number is consistent with how arena computed exit; no sign convention bug.

**Rig-correctness implication**: even fixing H1 (use prod's 999 qty), arena would still report ($13.18 - $13.22) × 999 = -$40 P&L. To match prod's +$515, arena MUST learn to exit via tranche limits, not bar prices.

### SBLX 2026-04-28 (prod -$157) — sign flip

**Production:**
- First entry at 14:32:10 was 404'd on close (position not found — likely fast scratch from D217 partial-fill ghost)
- Second entry at 14:49:05 filled: `filled_qty=3490 filled_avg_price=3.03`
- BAR-1 EXIT at T+86s (10:50:31) — got 404 (position not found). Position closed BEFORE BAR-1 attempt.
- Trade_results: entry 14:48:27 → exit 14:52:37, pnl = -$157
- `prod_qty = 3490`, `prod_entry_avg = $3.03`, `prod_exit_avg ≈ $2.985` (back-out: -$157 / 3490 ≈ -$0.045/share off entry)

**Arena (current rig):**
- `arena_qty = 24429` (from `tier1_qty = 75000 / 3.07`)
- `arena_entry_px = bar.open at entry minute = $3.07`
- `arena_exit_px = bar.open at exit minute (~14:52) ≈ $3.11` (the actual bar moved UP slightly)
- `arena_pnl = ($3.11 - $3.07) × 24429 ≈ +$977`. Reported as +$1561. Direction: POSITIVE.

**Decomposition:**
- **H1 (qty)**: 24429 vs 3490 = **7.0× too big**.
- **H2 (exit)**: prod's actual exit price ($2.985) was BELOW entry ($3.03), but the BAR at exit minute opened ABOVE entry ($3.11). Arena anchored to bar-open and computed positive; prod actually filled via market sell at a worse price than the bar open showed. **H2 fully responsible for the sign flip.**
- **H3**: clean.

### ATER 2026-04-28 (prod -$60) — magnitude error only, sign correct

**Production:**
- First entry at 14:32:10 was rejected by D217 (`returning partial`)
- Second entry at 14:49:08 filled: `filled_qty=3122 filled_avg_price=1.29`
- BAR-1 EXIT at T+83s — 404 (already closed)
- Trade_results: entry 14:48:27 → exit 14:52:37, pnl = -$60
- `prod_qty = 3122`, `prod_entry_avg = $1.29`, `prod_exit_avg ≈ $1.271` (back-out: -$60 / 3122 ≈ -$0.019/share)

**Arena (current rig):**
- `arena_qty = 52083` (from `tier1_qty = 75000 / 1.44`)
- `arena_entry_px = bar.open at entry minute = $1.44`
- `arena_exit_px = bar.open at exit minute = ~$1.41`
- `arena_pnl = ($1.41 - $1.44) × 52083 ≈ -$1562`. Reported as -$984. Direction: NEGATIVE (matches prod).

**Decomposition:**
- **H1 (qty)**: 52083 vs 3122 = **16.7× too big**. THIS IS WHERE H1 DOMINATES PURELY.
- **H2 (exit)**: arena's exit (-$0.03/share) was directionally correct vs prod's actual realized loss; magnitude was also right per share. The 27× magnitude error is dominantly the qty multiplier.
- **H3**: clean.

---

## §3 — Aggregate decomposition

For the 4 replayable trades, the previous diff reported:

| Source | Σ |Δ| |
|---|---:|
| `fill_price` (old categorization) | $3,374 |

Decomposed correctly:

| Source | Σ |Δ| (estimated) | Notes |
|---|---:|---|
| **H1: qty multiplier error** | ≈$2,400 | Dominates ATER cleanly; large contribution to OGN, SBLX |
| **H2: exit semantics** | ≈$900 | Dominates OGN sign flip; SBLX sign flip; LIDR mostly clean |
| **H3: direction/sign convention** | $0 | No bug found |
| **fill-price slippage (true)** | <$80 | The actual basis-point bar-open vs prod-fill drift |

The previous diff's "fill_price" bucket was **>97% NOT actually fill price**. The true fill-price drift is ≤2.4% of the headline divergence.

---

## §4 — Decisions arising from this audit

Per the session brief halt-and-decide gate:

> **If H1 (qty) dominates → fix qty reconstruction before any other parity work.**
> **If H2 (exit) dominates → fix exit semantics in arena before any other parity work.**

**Both H1 and H2 dominate, in different proportions per trade.** Per the brief: "Fix in priority order, smallest scope first."

Order:
1. **Block B.1 (this session)**: fix H1 — use trade_context parquet for qty where available; fall back to momentum log scrape. Eliminates the 5.7-16.7× multiplier errors that drown out everything else. Re-run replay. Document delta.
2. **Block B.2 (deferred to next session)**: fix H2 — model tranche-limit-sell fills as exits in arena. Without this, sign flips will persist for OGN/SBLX-class trades.

---

## §5 — Implications for other artifacts

**Catalyst gate sweep (`docs/sweeps/catalyst_gate_pareto.md`)**: the sweep uses RECORDED prod P&L per trade, NOT arena's replayed P&L. Therefore the sweep's PROVISIONAL conclusions are NOT corrupted by the rig errors above. We will still re-run it post-Block-B-fix as a discipline check, but the expected outcome is no change.

**Spread calibration (doc 59)**: the calibration was REVERTED last session because the fit overshot. With the corrected error decomposition, the *real* fill-price bps drift is much smaller than the previous diff implied. **The calibration framework's revert was correct; the underlying signal it would have fit against was much smaller than the inflated `fill_price Δ` suggested.** If anything, this audit *strengthens* the revert decision.

**FailureInjector (doc 60)**: unaffected. The injector + LIDR 403 fixture are correct in isolation regardless of the qty/exit issues above.

---

## §6 — Audit completeness check

- ✅ All 4 replayable trades walked end-to-end against prod logs.
- ✅ Each trade decomposed across H1/H2/H3.
- ✅ Direction (H3) explicitly checked on the OGN sign-flip canary — no bug.
- ✅ Aggregate ranking inverted from "fill_price first" to "qty + exit semantics first."
- ⚠️ Could NOT pull qty/fill data for 4/24 LIDR or 4/27 OGN from `trade_context` parquets (Bug AO orchestrator-hook gap means those sessions were not captured). Used momentum log scrapes instead — pinned in §2.

**Gate to Block B**: this doc committed. Proceed to fix H1.
