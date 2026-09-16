# 2026-04-30 — broker-truth initial pull + Bug AU discovery

**Status:** parallel work in PROMPT_07. Per §6.7 stop condition: "if broker-truth pull surfaces drift not just in `prod_entry_avg_px` but in `prod_entry_qty` or `prod_pnl` → file Bug AU finding doc. The corpus has been wrong on more than the price. Investigate before committing the regeneration."

**That stop condition triggered.** Source-0 integration into `extract_prod_qty_truth.py` was DEFERRED to next session pending policy decisions on side-flip + qty-mismatch handling.

---

## §0 — TL;DR

`scripts/pull_broker_truth.py` already existed (commit `a2ae7a0`, 2026-04-29 morning). Refreshed for 2026-04-22 → 2026-04-29; now 113 FILL events covering all 8 sessions in the window.

Comparing the 11-row truth corpus against the broker tape revealed **systemic discrepancies far beyond the original Bug AT-1 scope**:

| Severity | n | Examples |
|---|---|---|
| **EXACT match** | 2 | ONMD (17173 @ $1.15), ATER (3122 @ $1.29) |
| **Close** (qty Δ ≤ 10%) | 3 | AGPU (+53), SCNI (-546), SBLX (perfect) |
| **Significant qty drift** (>10%) | 3 | MAAS (-548), SEGG (price -$0.02), LIDR (-2349 = 45% drift) |
| **Catastrophic** | 2 | XTLB (1397 of 3013 = 46% missing), OPFI (2171 of 2182 = 99.5% missing) |
| **Side flip + price impossible** | 1 | **OGN** (journal: BUY 999 @ $11.25; broker: sell_short 666 @ $13.18) |

**The corpus is wrong on more dimensions than just price.** Fixing this honestly requires:
1. A policy for qty mismatches (trust broker always? merge multi-leg fills?)
2. Side-aware schema (current corpus assumes long-only; OGN was a short)
3. Re-running every downstream calibration (slippage v3, adverse-selection, exit counterfactuals) on corrected values

---

## §1 — The 11-row comparison table

| ticker | date | journal qty | journal $ | broker qty | broker $ (vwap of buys) | side | delta |
|---|---|---|---|---|---|---|---|
| AGPU | 04-22 | 505 | 9.5900 | 558 | 9.5900 | buy | qty +53 |
| MAAS | 04-22 | 1652 | 11.7300 | 1104 | 11.7300 | buy | qty -548 (-33%) |
| SCNI | 04-24 | 9293 | 0.8561 | 8747 | 0.8562 | buy | qty -546 |
| LIDR | 04-24 | 5264 | 2.4200 | 2915 | 2.4200 | buy | qty -2349 (-45%) |
| ONMD | 04-24 | 17173 | 1.1500 | 17173 | 1.1500 | buy | EXACT |
| **OGN** | **04-27** | **999** | **11.2500** | **666** | **13.2000** | **sell_short** | **SIDE FLIP + price wrong** |
| SEGG | 04-28 | 9281 | 1.1396 | 9281 | 1.1200 | buy | px -$0.0196 |
| SBLX | 04-28 | 3490 | 3.0303 | 3490 | 3.0300 | buy | EXACT (rounding) |
| ATER | 04-28 | 3122 | 1.2900 | 3122 | 1.2900 | buy | EXACT |
| XTLB | 04-29 | 3013 | 3.5200 | 1616 | 3.4900 | buy | qty -1397 (-46%) px -$0.03 |
| OPFI | 04-29 | 2182 | 9.6600 | 11 | 9.6200 | buy | qty -2171 (-99.5%) px -$0.04 |

---

## §2 — Bug AU diagnosis hypotheses

### §2.1 Side flip (OGN)

OGN 4/27 broker tape:
```
2026-04-27T13:32:04 sell_short 117 @ $13.20  partially_filled
2026-04-27T13:33:25 sell_short 333 @ $13.17  filled
2026-04-27T13:34:06 sell_short  40 @ $13.16  partially_filled
2026-04-27T13:35:21 sell_short 176 @ $13.16  filled
2026-04-27T14:00:46 buy        666 @ $13.20  filled  (cover)
```

Total: 666 shares shorted at avg $13.18, covered at $13.20 → P&L ≈ -$13.

The bot's journal recorded a LONG entry at $11.25 with qty 999 — wrong on **direction, qty, and price**. The fast-path FAST_PATH execution recorder logged a fictitious long at the LIMIT price, while the actual order was a short on a different code path (D161_FALLER_SHORT or D207_SHORT — both still drifted per doc 82 §0).

**Hypothesis:** the OGN trade was a FALLER short (D161 path), but FAST_PATH's logging routine mis-attributed it as a long. The journal carries the FAST_PATH side; the broker carries the actual short. This makes OGN a "two-bug" case: AT-1 (limit-as-fill) + side mis-attribution (likely a Bug AT-bypass variant in D161_SHORT path).

### §2.2 Catastrophic qty mismatches (XTLB, OPFI)

XTLB 4/29: journal qty=3013, broker buy qty=1616, broker sells totaling 3013.
OPFI 4/29: journal qty=2182, broker buy qty=11, broker sells totaling 2182.

**Hypothesis A (most likely):** the journal recorded the *intended* qty from the OTO submission, but the broker only partially filled. The unfilled portion was stopped at submission time (Bug V protection) or expired silently. The journal didn't update to reflect the actual filled qty — it carried the limit-order qty forward.

**Hypothesis B:** the bot held a small filled position, then tried to exit a larger qty than it actually owned. Alpaca's "sell_short" may have kicked in to make up the difference, or sells came from a different broker-internal lot. (OPFI: 11 buy, 2182 sells → 2171 phantom-shorted-then-covered.)

**Hypothesis C:** the journal's qty INCLUDES paper account state from before the broker's view (e.g., shares acquired in a previous session that we didn't pull broker_truth for).

The `pull_broker_truth.py` script reconstructed 91 closed trades with **net P&L = -$2,008.33** vs the journal's -$494.23 (just from yesterday). The discrepancy is in scope.

### §2.3 Significant qty drift (MAAS, LIDR, SEGG)

MAAS 4/22: -548 of 1652 (33%). LIDR 4/24: -2349 of 5264 (45%).

These are large enough to materially affect P&L computations. Same hypothesis as §2.2 (partial fills + journal recording intent rather than reality), but smaller in proportion.

---

## §3 — Why corpus regeneration was DEFERRED (PROMPT_07 §6.7)

If I had simply integrated broker_truth as Source 0 in `extract_prod_qty_truth.py`, the regenerated corpus would have:

1. **Silently flipped OGN's side** from "buy" to "sell_short" — but the schema doesn't carry side, so the resolution would just match the new fill at $13.20 without flagging the change. Downstream replay code that assumes long-only entries would mis-compute.
2. **Updated XTLB qty from 3013 → 1616** and **OPFI qty from 2182 → 11** — drastically changing replay positions and arena-reconstructed P&L without any acknowledgment that the journal had the wrong numbers all along.
3. **Updated MAAS/LIDR qtys** by 30-45% silently.

Per §6.7's stop condition: "investigate before committing the regeneration." This finding doc IS the investigation; the regeneration is queued for next session with explicit policy decisions.

---

## §4 — Decisions needed (operator) before regenerating the corpus

1. **Qty mismatch policy**: when journal says 3013 but broker says 1616 buy, which wins for replay?
   - (a) Always trust broker (truth corpus = broker realities)
   - (b) Always trust journal (truth corpus = bot's recorded intent)
   - (c) Use broker for buys < journal (partial fills), use journal otherwise
2. **Side-aware schema**: should the corpus have a `side` column to support shorts? Current schema is implicitly long-only.
3. **OGN handling**: 
   - (a) Drop OGN from the corpus entirely (it's a short, not in the EP-pivot scope)
   - (b) Add side column + treat OGN as a separate short subset
   - (c) Replace the OGN row with the broker-truth short data and document the side mismatch
4. **Multi-leg buys**: when a single OTO order results in 3 partial fills, should the corpus show one row (vwap-aggregated) or multiple rows? Current corpus is one-row-per-trade.
5. **Cross-session lots**: the OPFI 11-buy-vs-2182-sells suggests we may have been managing a position that the broker doesn't think we own. Investigation needed: was OPFI bought in a prior session, or is the journal phantom?

---

## §5 — What WAS shipped this session (not deferred)

- `scripts/pull_broker_truth.py` ran end-to-end successfully
- `data/broker_truth/activities.parquet` refreshed: 113 fills, 4/22-4/29 coverage
- `data/broker_truth/closed_trades.parquet` reconstructed: 91 closed trades, ΣP&L = -$2,008.33
- This finding doc

What was NOT shipped:
- `extract_prod_qty_truth.py` Source-0 integration
- `prod_qty_truth.parquet` regeneration
- Updated replay/calibration on broker_truth-corrected corpus

---

## §6 — Forward sequence update

PROMPT_07 originally projected:
- N (this session): FAST_PATH + broker_truth pull script + initial run + corpus regen → γ-audit at N+2
- N+1: broker_truth backfill 02-04 + journal reconciliation
- N+2: γ-audit on CRCA

Updated projection (Bug AU adds a session):
- N (this session — DONE): FAST_PATH + broker_truth pull script + initial run; corpus regen DEFERRED
- N+1: Bug AU resolution (operator decisions in §4 above + corpus regeneration with side-aware schema if approved)
- N+2: broker_truth backfill 02-04 + journal reconciliation
- N+3: γ-audit on CRCA

**γ-audit slipped one session.** The framework caught the bug; better one extra session than fictional analysis on corrupt data.

---

## §7 — Discovery rate

35 → **36** (Bug AU is new; isolated to truth corpus; no production fix attempted).

This is exactly what the framework is for. PROMPT_07 §6.7 explicitly anticipated this scenario as a stop condition. The agent honored it.
