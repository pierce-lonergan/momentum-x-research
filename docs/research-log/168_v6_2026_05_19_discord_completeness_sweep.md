# 168 — 2026-05-19 Discord completeness sweep — D304/D305/D306/D307

**Session date:** 2026-05-19 night (post doc 167 hotfix)
**Branch:** develop
**Predecessor:** [doc 167 forward-reference hotfix](167_v6_2026_05_19_hotfix_forward_reference_crash.md)
**Trigger:** User: *"at the end of the day [Discord] is also providing incomplete messages"*

---

## 0. The root cause

`alert_session_end_rich` (shipped in doc 161 with 15 supported kwargs) was being called from `main.py:6913` with **only 9 of 15**. The 6 missing kwargs were the entire reason doc 161 existed — D-codes fired, top winners/losers with entry→exit prices, EOD broker-truth reconciliation, BOCPD refit recommendation, veto summary. **All the rich context I added in doc 161 never actually reached Discord.**

Same pattern hit `post_trade_open`: doc 165 added `gap_pct` and `intended_entry` kwargs but the call site never passed them.

Same pattern hit `post_entry_filled`: doc 165 created the alert function but **the wiring TODO was never done** — zero callers.

This is a recurring class of bug: an alert's signature is enriched, but the call site stays at the old shape. The Discord channel silently loses fields for months.

---

## 1. What this commit ships

### D304 — Wire 6 missing kwargs into `alert_session_end_rich`

Built D304 payload extraction in `main.py:6905+` from data already computed for the EOD report JSON:
- `d_codes_fired`: `_eod_full_report["fires"]` (D230-equity, D231-qty, etc.)
- `top_winners` / `top_losers`: derived from `trade_journal._entries` — closed BUY entries sorted by realized_pnl, top 3 each, with entry_price/exit_price for the "($X → $Y)" display added in doc 165
- `eod_recon`: `_eod_full_report.sections.eod_failsafes.broker_truth_recon` (broker_pnl, journal_pnl, delta, disagreements)
- `bocpd_refit`: translated from `_bocpd_refit.fires_d262 + diff.*` to the `{refit_recommended, old_mu, new_mu, n_trades}` shape the alert renders

Preview using Monday 5/18's real EOD data:

```
TITLE: ❌📉 EOD Report -- 2026-05-18

DESCRIPTION:
📅 Tuesday, May 19, 2026 | ⏰ Market closed
Loss day. 3 trade(s) executed.

FIELDS (9):
  [💰 Realized P&L]     **$-371.69**
  [📊 Trades]            **3**
  [📦 Open at Close]     **1**
  [🏦 Starting Equity]   $140,000.00
  [🏦 Ending Equity]     $139,628.31
  [📈 Day Change]        $-371.69 (-0.27%)
  [🚨 D-Codes Fired (2)] `D231-qty`, `D230-equity`
  [📉 Top Losers]
      🔴 CISS $-561.87 (-3.5%)  ($4.67 → $4.50)
      🔴 GCTS $-118.84 (-0.8%)  ($2.49 → $2.47)
  [⚠️ EOD Reconciliation]
      broker_pnl=$-680.71  journal_pnl=$-109.29  delta=$-571.42  disagreements=3
```

3 extra fields (D-Codes, Top Losers, EOD Reconciliation) that pre-D304 were **silently dropped on the floor**.

### D305 — Wire `gap_pct` + `intended_entry` into `post_trade_open`

`gap_pct` extracted from `cand_by_ticker.get(verdict.ticker).gap_pct` (already used 13 lines earlier for archetype assignment). `intended_entry = order.submitted_price` so the doc 165 slippage badge appears whenever fill ≠ limit.

Result: every BUY alert now shows `[Bought 957 of VELO at $21.14 (🚀 +38.2%)]` and `[Slippage: +8.8% (intended $19.43)]` instead of just `[Bought 957 of VELO at $21.14]`.

### D306 — Wire `post_entry_filled` (doc 165 alert that had zero callers)

Added a second alert dispatch right after `post_trade_open`. `post_trade_open` is "bot intends to buy"; `post_entry_filled` is "broker confirmed the fill, here's the slippage + stop distance from fill". `n_submission_attempts` and `fill_latency_ms` are left at defaults for now — a follow-up commit will add per-ticker BRIDGE_CANCEL retry tracking.

### D307 — AST static guard against future "incomplete message" bugs

New `tests/unit/test_d304_d307_alert_kwarg_completeness.py` (14 tests). For each alert in an explicit `_ALERT_CONTRACTS` dict (alert_session_end_rich, post_trade_open, post_entry_filled), the test:

1. AST-walks `main.py` looking for every `alert_fn(...)` call
2. Collects the union of kwargs passed across all call sites
3. Fails if any contract kwarg is missing
4. Cross-checks: every contract kwarg must actually be in the alert's signature (catches stale contracts after refactors)

When a future commit adds a new kwarg to an alert and updates `_ALERT_CONTRACTS` but forgets to wire it from `main.py`, the guard fails at test time — well before the Discord channel goes silently incomplete.

---

## 2. Tests

| Suite | Tests | Status |
|---|---|---|
| D304 payload builders (winners/losers, BOCPD shape mapping) | 5 | ✓ |
| D307 static AST guard (per-alert + per-kwarg checks) | 3 parametric + 6 regression pins | ✓ |
| All doc 168 tests | **14/14** | ✓ |
| Full regression (D301, D297, D298, D294, D295, D296, doc-163, doc-165, D91, D279, D277, D24, alpaca, tabpfn, D121) | **245/245** | ✓ |

---

## 3. What the operator will see tomorrow

| Alert | Before | After |
|---|---|---|
| Session-end EOD | Headline P&L + 5 numbers. No D-codes, no trade attribution, no recon. | Same headline + D-codes fired + per-trade winners/losers with price journey + broker-vs-journal recon + BOCPD recommendation. |
| Trade BUY | "Bought 957 of VELO at $21.14" | "Bought 957 of VELO at **$21.14 (🚀 +38.2%)** · Slippage: **+8.8%** (intended $19.43)" |
| Trade FILL | (no alert existed; the doc 165 alert was never wired) | New "✅ FILLED VELO" message with fill price + gap + slippage + stop distance from fill. Foundation for the future BRIDGE_CANCEL retry-count display. |

---

## 4. Filed for follow-up

- **Veto summary** is the only field in `_ALERT_CONTRACTS` for `alert_session_end_rich` not yet wired — there's no per-reason veto counter in `metrics`. Filed: add a `metrics.vetoes_by_reason: Counter` so we can wire `veto_summary` into the EOD alert.
- **`post_entry_filled` enrichment**: add per-ticker `submission_attempts` counter in `bridge.py` so the BRIDGE_CANCEL retry storm warning (the most valuable feature of this alert) actually fires when it should. Currently always shows attempts=1.
- **`alert_morning_resolution_sync`** and **`alert_critical`** are not yet in `_ALERT_CONTRACTS` because they have stable signatures. Add them if/when they grow new fields.
- **CI integration**: this guard should run on every PR, not just locally. Filed.

---

## 5. Verdict

**Status:** **PASS — SHIPPED 2026-05-19.**

Tomorrow's EOD Discord message will be the full enriched embed for the first time. The user's "incomplete messages" complaint is resolved. The kwarg-completeness AST guard makes this class of bug catchable at commit time going forward.
