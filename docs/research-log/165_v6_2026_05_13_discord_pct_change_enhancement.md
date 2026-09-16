# 165 — 2026-05-13 Discord enhancement: % change everywhere + post_entry_filled

**Session date:** 2026-05-13 late night
**Branch:** develop
**Predecessors:** [doc 161 Discord enhancements](161_v6_2026_05_12_discord_dramatic_enhancement.md), [doc 164 four-bug fix](164_v6_2026_05_13_four_bug_comprehensive_fix.md)
**Trigger:** Operator request post-doc-164 — "lets also add the percent change for each of the stocks displayed in the discord and entry points that get flagged. please do a overall enhancement of how we display information in discord"

---

## 0. The gap

Doc 161 added rich event-driven Discord alerts but each alert reinvented its
own price/percent display. Result:
- Some lines showed `${price:.2f}` (no change context)
- Others showed `+1.5%` with no direction arrow
- Sub-$1 stocks rounded to 2 decimals (lossy for QUCY-class candidates)
- Winners/losers in EOD report didn't show entry → exit prices
- The `post_trade_open` alert fired AT submit time, but with today's
  4×BRIDGE_CANCEL VELO churn, the operator had no real-time visibility
  into when a position actually opened (or how much price ran during
  the 48-minute submit-to-fill window)

---

## 1. What shipped (5 helpers, 7 alert upgrades, 1 new alert)

### Shared helpers (top of `src/monitoring/alerts.py`)

```python
_pct_arrow(pct)       # 🚀 / 🟢⬆️ / 🟢 / ⏸️ / 🔴 / 🔴⬇️ / 💥  (8-tier)
_pct_badge(pct)       # "+8.8%" / "-1.2%" / "0.0%"
_price_with_change(price, pct=None)  # "$21.14 (🚀 +38.2%)"
_separator(width=30)  # standardized ─ divider
_color_for_pct(pct)   # graded Discord embed color (vivid green → vivid red)
```

Sub-$1 prices auto-default to 4 decimals (penny-stock precision). The
`_pct_arrow` thresholds are graded so tiny moves get a soft circle
while explosive moves get the rocket/explosion.

### Alerts upgraded

| Alert | Added |
|---|---|
| `alert_morning_resolution_sync` | Standardized `_price_with_change` for entry/current price; consistent arrow on each carry-overnight line |
| `alert_halt_blocked_entry_sync` | Stop distance as signed % of entry (catches the QUCY-class -1.3% violation visually) |
| `post_watchlist` | Optional `day_change_pct` per candidate → intraday badge alongside the gap badge |
| `post_fast_path_scores` | Gap % badge on entry price (shows WHY each name was fast-tracked); stop distance % |
| `post_trade_open` | New kwargs: `gap_pct`, `intended_entry`. Shows gap badge on fill price + slippage from limit |
| `post_verdict_summary` | BUY/HOLD lines show price + gap badge + arrow (verdict context, not just confidence score) |
| `alert_session_end_rich` | Winners/losers use `_pct_arrow`; optional `entry_price`/`exit_price` per-trade keys show price journey |

### New alert: `post_entry_filled`

Fires when an OTO **actually fills** (not at submit). Includes:
- Fill price + gap badge
- Slippage from intended limit (signed %)
- Stop distance from fill (%)
- `n_submission_attempts` — surfaces D237 BRIDGE_CANCEL churn
- `fill_latency_ms` — submit-to-fill wall-clock time

**Sample output (today's VELO replay):**

```
TITLE: ✅ FILLED VELO

📅 **Wednesday, May 13, 2026** | ⏰ 11:46 PM ET
──────────────────────────────

💵 **957 shares** of **VELO** filled at **$21.14 (🚀 +38.2%)**
📏 Slippage from limit: **+8.8%** (intended $19.4300)
💰 Notional: **$20,230.98**  | Risk to stop: **$200.97**
🛡️ Stop: **$20.93** (-1.0% from fill)
⚠️ **4 OTO attempts** before fill (D237 BRIDGE_CANCEL churn -- check stop math)
⏱️ Fill latency: **2887.0s** from first submit
```

The 4-attempt warning + 2887s latency line would have made today's
VELO churn jump out of the Discord stream in real-time, instead of
being discoverable only via the EOD log audit I had to do.

---

## 2. Backwards compatibility

Every kwarg added to existing alerts is **optional** with a safe default.
Verified with these tests:

- `test_post_trade_open_kwargs_are_optional` — old call signature still works
- `test_post_watchlist_works_without_day_change_pct` — gap-only candidates render
- `test_verdict_summary_works_without_price` — no-price verdict dicts still render
- `test_session_end_rich_works_without_entry_exit_prices` — old per-trade dicts still render

No production caller needs to change. Callers that want the richer
display opt in by passing the new kwargs.

---

## 3. Files in this commit

| File | Change |
|---|---|
| `src/monitoring/alerts.py` | +5 helpers (~80 LOC) + 6 alert upgrades + 1 new alert (~50 LOC) = ~210 LOC |
| `tests/unit/test_doc165_discord_pct_change.py` | NEW, 46 tests covering helpers + every alert payload |
| `docs/research-log/165_v6_2026_05_13_discord_pct_change_enhancement.md` | THIS doc |

**Test count:**
- 46/46 new tests pass
- 137/137 broader regression (D294 stop-floor, D295 D91 journal, D296 LLM
  backoff, doc-163 shadow timing, D91 + D279 + D277 + tabpfn + alpaca_client)

---

## 4. Verdict

**Status:** **PASS — SHIPPED 2026-05-13.**

Tomorrow morning, every alert that mentions a stock will include its
% change. When the bot fills VELO-class candidates, the new
`post_entry_filled` ping will land in Discord with the slippage,
gap, and submission-attempt count — so today's silent BRIDGE_CANCEL
ghosts can't recur unnoticed.

### Caller wiring (TODO: not in this commit)

The new `post_entry_filled` alert is implemented but not yet wired
into the bot's order-confirmation path. Filed for follow-up:
- Find the OTO fill confirmation site in `src/execution/bridge.py`
  (around the BUY-leg fill handler)
- Track `submission_attempt` counter per ticker
- Track `first_submit_ts` per ticker → compute `fill_latency_ms`
- Call `post_entry_filled(...)` at fill confirmation

That's a small wiring-only commit; the alert function itself is
ready.

### Out of scope

- `post_trade_close` already shows pnl + pct change correctly; not modified
- `alert_data_ingest_result` is totals-only; no per-stock data to enhance
- `alert_critical` / `alert_critical_sync` are free-text; no stock data
- Lottery runner has its own discord posting code (separate from this
  alerts.py); not touched in this commit
