# 79 — Arena infrastructure sweep + Polygon integration strategy

**Status:** shipped 2026-04-29 PM. Sequel to docs 77 (Polygon REST integration) and 78 (tick-level slippage v3).

---

## §0 — TL;DR

Polygon supersedes our existing bar data sources. New abstraction `mx-arena/data_providers/unified_bar_source.py` returns 1-minute bars from (in priority order): polygon_cache → bar_recordings → alpaca_backfill. Existing strategy code can use it as a drop-in. The $0.34 firewall holds **by construction** for in-corpus trades (truth-table snap takes precedence). The EP pivot's data dependencies (doc 71) are mostly covered by Polygon — except analyst count and institutional ownership, which still require Benzinga or similar.

---

## §1 — Inventory of arena's data sources

| Source | Used by | Schema | Coverage | Status |
|---|---|---|---|---|
| `data/polygon_backfill/equity_minute_bars/` | NEW (this session) | parquet: ts_ms, ohlcv, vwap, n_trades | 1y of project tickers (--max=100 capped tonight) | growing in background |
| `data/polygon_backfill/etf_minute_bars/` | NEW (this session) | same | 10y of 40 ETFs (12+/40 done at session end) | growing in background |
| `data/polygon_backfill/tick_data/{date}/{ticker}_quotes.parquet` | Block C calibration | parquet: NBBO ticks ns precision | 9 prod-truth (ticker, date) pairs, full sessions | complete |
| `data/polygon_backfill/tick_data/{date}/{ticker}_trades.parquet` | Block C calibration | parquet: trade ticks | same 9 pairs | complete |
| `data/polygon_backfill/fundamentals/{ticker}.json` | NEW (EP pivot) | JSON: ticker_details + 8 quarters financials | 2,370 tickers | complete |
| `data/bar_recordings/{date}/{ticker}.json` | session_data_collector, falsification suite, replay | JSON: regular-session 1-min OHLCV+VWAP | 86 days × 12-30 tickers | LEGACY — superseded by Polygon for newer dates |
| `data/replay/prod_qty_truth.parquet` | replay rig truth-table snap | 9 rows × 7 cols | static reference (4/22-4/28) | **Bug AS pending** (OGN row impossible price) |
| `data/replay/prod_exit_truth.parquet` | replay rig | 9 rows × 10 cols | same | check during Bug AS investigation |
| `data/calibration/historical_earnings_*.json` | (Finnhub-derived, deferred) | JSON: earnings calendar | Dec 2025 – Apr 2026 | LEGACY |
| `data/calibration/slippage_residuals.parquet` | doc 64 v2 (contaminated) | parquet | static | superseded by `tick_level_slippage_residuals.parquet` |
| `mx-arena/arena/calibration/spread_v2.json` | arena fill model | identity multipliers | static | superseded by spread_v3.json |
| `mx-arena/arena/calibration/spread_v3.json` | arena fill model (forthcoming wire-up) | per-tier multipliers | session-current | NEW |

---

## §2 — Where Polygon supersedes existing

### §2.1 Bar data
**Polygon wins.** Bit-perfect OHLC vs bar_recordings (verified doc 77 §4 reconciliation), extended hours included, full SIP volume (~0.1% delta vs IEX-only on AKAN 4/29), unlimited historical depth.

### §2.2 Tick data
**Net new.** No existing source had NBBO ticks or trade ticks at any historical depth. This is the single biggest unlock from the $199 subscription — closes doc 64's contamination finding (per doc 78).

### §2.3 Fundamentals
**Polygon partial.** Polygon's `/vX/reference/financials` provides:
- ✅ revenues, net_income, operating_income, diluted_eps, fiscal period dates

But MAGNA-N (doc 71 §3.1) also wants:
- ✅ market cap, shares outstanding (via Polygon `/v3/reference/tickers/{t}`)
- ❌ analyst count / earnings estimates (need Benzinga, FMP, or paid Finnhub)
- ❌ institutional ownership (need Form 13F parser via edgartools, or Benzinga)
- ❌ short interest (need FinViz/Alpaca shorts — already integrated via `src/data/short_interest.py`)

### §2.4 Universe enumeration
**Polygon wins.** `/v3/reference/tickers` is canonical and current. Project's hand-curated lists become obsolete; replace with a Polygon-driven universe filter.

---

## §3 — Integration strategy

### §3.1 Unified bar source

Shipped: `mx-arena/data_providers/unified_bar_source.py`.

```python
from data_providers.unified_bar_source import get_bars, get_source
bars = get_bars("AKAN", "2026-04-29")  # returns list[dict]
src = get_source("AKAN", "2026-04-29")  # 'polygon' | 'bar_recordings' | 'alpaca_backfill' | None
```

**Priority order**:
1. Polygon (`equity_minute_bars/`, then `etf_minute_bars/`)
2. bar_recordings JSON
3. alpaca_backfill (legacy, may not exist)

**Schema** (uniform across all sources):
```python
{
    "ts_ms": int,         # UTC millis at bar start
    "open": float, "high": float, "low": float, "close": float,
    "volume": float,
    "vwap": float | None,
    "source": str,        # 'polygon' | 'bar_recordings' | 'alpaca_backfill'
}
```

Bars are sorted ascending by `ts_ms`. Returns `[]` if no source has data.

### §3.2 No migration backfill

Existing `bar_recordings/` stays as historical record. The unified source picks Polygon first when both exist; bar_recordings is a fallback for tickers/dates not yet in the Polygon backfill.

### §3.3 Strategy code remains unchanged

The strategy (in `src/`) doesn't need changes. Only arena/replay code that currently reads bar_recordings JSON should switch to `unified_bar_source.get_bars()`. That migration is a follow-up doc (likely doc 80).

---

## §4 — Firewall validation

Per Block D.4: "Σ |Δ| must remain ≤ $0.34 across 4/22-4/28".

**By construction:** the prod-mirror replay's truth-table snap takes priority over any computed bar source for the 9 corpus trades. So the in-corpus 9 trades will replay at their truth values regardless of which bar source is selected. `Σ |Δ|` for the corpus = **0** by snap.

For non-corpus trades in the 4/22-4/28 window (cancelled orders, rejected fills, partial fills), the unified bar source's choice may differ. Empirical validation is deferred — when arena is wired to use unified_bar_source, re-run replay and confirm Σ|Δ| ≤ $0.34. Filed for next session.

**Hand-checked sanity (this session)**:
- AKAN 2026-04-29: Polygon vs bar_recordings = bit-perfect OHLC (doc 77 §4)
- 12+ ETFs and 100+ equities pulled cleanly with 0 errors

No basis to expect a firewall break, but a real run will confirm.

---

## §5 — EP pivot data dependency audit

Per doc 71 Block 6 (deterministic catalyst classifier) + Block 7 (5-min ORB on stocks-in-play):

| MAGNA-N component | Polygon provides? | Alternative |
|---|---|---|
| **M**: Massive accel in profit growth ≥100% | YES via financials.net_income_loss QoQ % | — |
| **A**: Sales accel ≥39% two consec quarters | YES via financials.revenues QoQ % | — |
| **G**: Gap-up ≥10% | YES via aggregates (compare prior_day_close vs today_open) | — |
| **N**: Neglected (low coverage) | PARTIAL — Polygon has shares_outstanding; "coverage" needs analyst count | **Benzinga or paid Finnhub** |
| Market cap | YES via reference/tickers | — |
| Float | PARTIAL — Polygon has shares_outstanding; float requires insider/restricted-share data | **edgartools (Form 4 + 10-K parsing) or Benzinga** |
| Institutional ownership % | NO | **edgartools (13F) or Benzinga** |
| Short interest % of float | NO | already wired via `src/data/short_interest.py` (FinViz/Alpaca) |
| News catalyst (earnings/M&A/FDA) | NO | already wired via `src/data/news_client.py` (Finnhub + Alpaca News); also **edgartools for 8-K** |
| 5-min ORB bars | YES via aggregates(timespan='minute', multiplier=5) | — |

**Verdict:** Polygon covers ~70% of EP-pivot data needs. Critical gaps:
- analyst count (needed for "Neglected" filter)
- institutional ownership / 13F (needed for the "smart money pre-positioning" check from research report §1.4)
- 8-K Item parsing for catalyst tagging (edgartools handles this, free)

**Recommended additional procurement:**
1. **edgartools** (free) — wire up 8-K Item 2.02 / 1.01 / 8.01 parsing. Doc 71 §3.1 already calls this out.
2. **Benzinga Pro Essential ($99/mo)** — covers analyst count, real-time news, AI tagging. Best ROI of remaining gaps.
3. Defer 13F ownership (the most expensive gap) until the EP classifier proves out a base-rate edge.

Total recommended monthly spend: **$199 (Polygon) + $99 (Benzinga) = $298/mo**, ~0.2% of equity, full EP-pivot data coverage minus 13F.

---

## §6 — What's NOT integrated tonight

- **Polygon WebSocket streaming** — REST is sufficient for backtesting / calibration. Streaming touches live data paths and reconnection semantics; deferred to a dedicated session.
- **Polygon Flat Files (S3)** — for true bulk pulls (every ticker × every minute × all history), S3 is cheaper than REST. We have ~10y × 40 ETFs + 1y × 100 equities + 9 tick-validation pairs from REST tonight, ~6 GB. S3 would be appropriate if we want, e.g., the entire NYSE universe at minute resolution.
- **Polygon options data** — included in subscription, not relevant to EP pivot today.
- **Adverse-selection on modeled exits** (doc 78 §6) — deferred. The data is in `tick_data/`; the analysis just hasn't been written.

---

## §7 — Followups for next session

1. **Bug AS** (HIGH) — fix `prod_qty_truth.parquet` OGN row; re-run Block C calibration
2. **Wire arena fill model to spread_v3** — replace spread_v2 reference; re-run prod-mirror replay; confirm $0.34 firewall holds empirically
3. **Migrate replay/falsification code to `unified_bar_source.get_bars()`** — drop direct bar_recordings JSON reads
4. **Adverse-selection analysis** on the 60min post-fill windows (doc 78 §6)
5. **Bug AT** (still open from doc 71 §6.3) — Phase 2 fill-handler bypass investigation
6. **edgartools wiring** for 8-K catalyst classification per doc 71 §3.1

---

## §8 — Status

- ✅ Inventory of arena data sources (§1)
- ✅ Polygon supersession map (§2)
- ✅ `unified_bar_source.py` shipped + smoke-tested (§3)
- ✅ Firewall validation by construction (§4); empirical replay deferred
- ✅ EP pivot data audit (§5)
- ✅ This doc

**Discovery rate: 33/0 holds** (Bug AS surfaced in doc 78; no new bugs introduced).
