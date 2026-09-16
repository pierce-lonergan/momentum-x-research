# 237 — Phantom-P&L audit: recorded vs realized. The leak IS the qty-drift class (already fixed).

**Author**: Claude Opus 4.8
**Date**: 2026-06-02 (Tuesday, late)
**Mandate**: Pierce — "Reconcile recorded vs realized P&L on every live (paper) trade YTD. Quantify the
leak. Rank fixes by $-impact. If leak > 20% of recorded edge, this is the largest concrete improvement
available regardless of any predictive-model work."

**Outcome:** the leak is real, it is **+33% of realized magnitude** (above the 20% bar), it is **81%
concentrated on qty-drift days** — i.e. it is precisely the **phantom-position / qty-drift class**, and it
is **already the target of the docs 226-229 fixes shipped this session.** The audit also hit a Stage-1
data-integrity wall that is itself a finding (a reporting-field bug + absent per-trade fill records).

---

## Stage 1 — data integrity (gates the audit; two findings before any number)
1. **The `broker_total_pnl` field is inconsistent pre- vs post-5/12.** In 26 EOD reports, sessions
   **4/27–5/11** carry a *static ~+$41,256* `broker_total_pnl` repeated identically across days (with
   journal=0) — daily realized P&L cannot be identical to the dollar for a week. That field is a
   **cumulative/account-level (or cached) value, a reporting bug**, not daily realized. Only **post-5/12**
   reports carry genuine daily realized P&L. *→ Full-YTD reconciliation is impossible from this field; the
   clean window is 5/12–6/02 (15 sessions). The reporting bug is fix #2 below.*
2. **No per-trade fill records exist on disk** (`trade_journal_*.json` = 0, `executed_*.jsonl` = 0). The
   per-trade slippage/fee/partial-fill decomposition the mandate envisioned is **not assemblable from flat
   files** — and on **paper** trading it is largely moot (Alpaca paper fills are an idealized model, not
   real market impact). *→ per-trade decomposition deferred to live cutover (fix #3).*
3. **Realized side is broker-anchored:** `broker_truth_recon.broker_total_pnl` is the bot's own EOD Alpaca
   pull, so the recorded-vs-realized delta below is a genuine recorded-vs-broker reconciliation (no fresh
   API call needed).

## Stage 2-3 — the clean-window reconciliation (5/12–6/02, 15 sessions)
Leak = `journal_total_pnl` (recorded) − `broker_total_pnl` (realized). Positive = recorded **overstates**.

| metric | value |
|---|---|
| total leak (recorded − realized) | **+$2,631** (recorded overstated) |
| per-session | mean +$175, median +$20 |
| total realized (clean window) | −$7,977 |
| **leak as % of \|realized\|** | **+33%** (> the 20% bar) |
| leak on **qty-drift** days (8) | **+$2,143 (81% of total)**, mean +$268/day |
| leak on clean days (7) | +$488 (19%), mean +$70/day |
| biggest single sessions | 5/29 +$673 (drift=2), 6/02 +$493 (drift=2), 5/18 +$571 (drift=1) |

**Attribution:** the leak is overwhelmingly the **phantom / qty-drift class** — recorded books ghost/
unrealized MTM that the broker never realized (the APPS/LFS/LASE pattern: a close 403s on reserved qty, or
a multi-buy mis-tracks, and the journal books P&L the broker didn't). Real slippage/fees are ~0 on paper
and are *not* the leak here. The 81%-on-drift-days concentration is the signature.

**Annualized (caveated extrapolation):** +$175/session × ~252 ≈ **~+$44K/yr** of phantom overstatement if
the pre-fix rate continued — which **dwarfs the bot's actual realized P&L** (≈ −$8K over the window). So the
phantom leak was the single largest distortion of the system's perceived edge, *and it poisoned the
BOCPD/Kelly corpus* (mu_edge read off overstated journal P&L). This **quantitatively confirms** that the
docs 226-229 phantom work was the highest-ROI lever available — exactly the mandate's hypothesis.

## Stage 4 — ranked fix list ($-impact × feasibility)
| # | fix | status | est. impact |
|---|---|---|---|
| **1** | **Phantom/qty-drift guards** — add_position MERGE (D227), the 2 CRITICAL EOD-close phantom guards (D229), qty-drift reconcile (D218) | **SHIPPED this session (226-229)** | eliminates the 81%-of-leak drift component → forward drift-day leak should fall toward $0 |
| 2 | **Fix the `broker_total_pnl` reporting field** (daily, not cumulative) | **ALREADY SHIPPED — D238 (2026-05-12)**, `eod_failsafes.py:314`: now filters `get_orders(after=today_00:00_UTC)` to TODAY's orders only; the comment literally cites the old "false-positive $41k+ deltas." This *is* why the clean window starts 5/12. (The spawned fix chip is redundant.) | done |
| 3 | **Persist per-trade fill records** (`executed_*.jsonl` with submitted vs filled price, qty, fees) | open, small eng | unlocks true per-trade decomposition + the b2 overlay live-tape backtest (doc 236) |
| 4 | **Market-impact fill model** keyed to per-name $-volume | open — **only needed at live-money cutover** | the *real-money* slippage leak (≈0 on paper) — not a paper concern yet |

## Verdict
- **The leak is +33% of realized (> 20% bar) and is the phantom/qty-drift class** — the mandate's
  "largest concrete improvement" was correctly identified, and it was **already shipped this session
  (226-229)**. The audit's value is **confirmation + quantification**, not a new target.
- **The forward test (the real proof):** future sessions' EOD reconciliation should show the **drift-day
  leak collapse toward $0** now that 226-229 are deployed. If a session still books a journal-vs-broker
  gap on a qty-drift day post-deploy, a phantom guard was missed — that is the live KPI to watch.
- **Honest limits:** per-trade slippage/fee decomposition is not assemblable (no flat files) and not
  meaningful on paper; the real-money slippage leak awaits the market-impact model at live cutover. The
  pre-5/12 broker-field bug means a clean *full-YTD* number isn't recoverable — the 15-session clean window
  is the honest basis.

**No live change** (the fixes that matter, 226-229, already shipped; #2/#3/#4 are follow-ons).

**Initiator**: Claude Opus 4.8 (1M ctx). **Basis**: 26 EOD `broker_truth_recon` reports (broker-anchored).
**Predecessors**: 226-229 (the phantom/qty-drift fixes this audit confirms were the right target), 230
(execution integrity = the lever), 209 (honest EOD reporting). **Feeds**: doc 236 (the b2 overlay needs the
per-trade tape that fix #3 would persist).
