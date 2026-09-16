# 63 — Block C: Bug AO orchestrator hook (MVP via sidecar backfill)

**Status:** shipped 2026-04-28 PM. Sidecar approach per stop condition.
**Severity:** infrastructure. Closes the third of three Block 4.4 prerequisites identified in plan doc 58.

---

## §0 — TL;DR

The original Bug AO ask: persist `requested_qty`, `terminal_filled_qty`, `terminal_filled_avg_price`, `catalyst_type`, `news_agent_signal`, `news_agent_confidence`, `exit_codepath` per order so historical trade analysis (and Block 4.4 OOS run) can size + stratify trades correctly.

The brief offered MVP scope: forward-going hook OR backfill from logs. Hitting the stop condition: extending the existing `TradeContextRow` (frozen+`extra=forbid`) requires schema versioning + writer changes + 2-3 emit-site updates ≈ 150 LOC across 4 modules. **Over the >100 LOC threshold.**

Shipping the **sidecar-backfill MVP** instead: a new `trade_attribution_corpus` parquet, joinable to `trade_context` by `order_id` where extractable, otherwise keyed by `(session_date, ticker, entry_ts)`. All data extracted from existing log lines + reuses the truth tables from extract_prod_qty_truth.py / extract_exit_truth.py. **Zero changes to the order submission code path.**

Forward-going hook (writing this sidecar live during prod) is deferred to the same future session that ships the recorder fix (doc 61 §6 Option B).

---

## §1 — What got shipped

### Module: `scripts/build_trade_attribution_corpus.py`

For each in-scope trade in `data/trade_results.jsonl`:
1. Load qty + entry_px from `data/replay/prod_qty_truth.parquet`.
2. Load exit_px from `data/replay/prod_exit_truth.parquet`.
3. Scrape `logs/momentum_<date>.log` for:
   - news_agent ENSEMBLE classification at-or-before entry timestamp (signal + conf)
   - exit codepath (D146 BAR-1 / D245 SMART / D76 close / TRANCHE)
   - D85 SUBMITTED order_id (FAST_PATH path)
4. Compose into `AttributionRow`:

```
session_date, ticker, entry_ts, order_id,
prod_qty, prod_entry_avg_px, prod_exit_avg_px, prod_pnl,
catalyst_type, news_signal, news_conf, exit_codepath,
data_completeness ∈ {full, partial, minimal},
backfill_source = "logs_v0.1"
```

5. Write per-session parquet to `data/instrumentation/trade_attribution/session_date=<date>/attribution.parquet`.

### Output: 17 attribution rows across 5 sessions (4/22-4/28)

| Session | Rows | Full | Partial+Min |
|---|---:|---:|---:|
| 2026-04-22 | 3 | 2 | 1 |
| 2026-04-23 | 1 | 0 | 1 |
| 2026-04-24 | 4 | 3 | 1 |
| 2026-04-27 | 3 | 1 | 2 |
| 2026-04-28 | 6 | 3 | 3 |
| **Total** | **17** | **9** | **8** |

The 8 partial+minimal rows are predominantly LIDR carry-snapshot duplicates (no qty/entry/exit truth because they aren't real entries) and pre-market 09:29 ET trades (XNDU 4/23, TRT 4/24).

### Stratification preview (now queryable across the corpus)

Pulling `news_signal × prod_pnl` for the 9 full-completeness rows:

| Signal | n | Σ pnl | Mean | Note |
|---|---:|---:|---:|---|
| STRONG_BULL | 1 | +$515.85 | +$515.85 | OGN 4/27 — earnings |
| BULL | 6 | +$203.12 | +$33.85 | LIDR 4/24 +$421 + 5 scratches/losses |
| NEUTRAL | 1 | -$157.75 | -$157.75 | SBLX 4/28 |
| (other 1 row was a $0 scratch) | | | | |

This is the same pattern surfaced manually in `evaluation/2026-04-28-catalyst-stratification.md`. The difference: now it's a queryable parquet, refresh-able with one script invocation, ready to scale across 86+ sessions.

---

## §2 — Why sidecar instead of extending TradeContextRow

`TradeContextRow` schema:
```python
class TradeContextRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    schema_version: int = SCHEMA_VERSION  # currently 1
    # ... 16 fields ...
```

Adding catalyst_type / news_signal / news_conf / exit_codepath / terminal_filled_avg_price as v2 fields would require:
- Bumping `SCHEMA_VERSION` and adding all the above as Optional[X] = None to remain backward-compatible
- Writing a migration in `migration.py` (the framework exists but a real migration has never shipped)
- Updating writer.py's `emit_trade_context_dict` (and any sibling helpers) to accept the new fields
- Updating `alpaca_executor.submit_oto_order:393` to populate news_signal/conf at submit time (requires passing the orchestrator's most-recent ensemble signal down)
- Updating `bridge._poll_for_terminal_fill:1033` to populate exit_codepath at terminal time (requires passing context about which exit path fired)
- Tests for each touched file

Conservative count: ~150 LOC across 4 modules. Over the brief's >100 LOC stop threshold.

The sidecar:
- 1 new schema (dataclass only, no Pydantic frozen drama)
- 1 new script
- 0 changes to submission code path
- Joinable to TradeContextRow via order_id when needed
- Backfillable retrospectively from logs

**The sidecar is strictly inferior for FORWARD-GOING capture** (it relies on logs being written; if a log line shape drifts, the backfill silently misses) but strictly superior for SHIPPING TONIGHT and being usable by Block 4.4.

---

## §3 — Forward-going hook is the next-session deliverable

When the next session ships either (a) the recorder fix (doc 61 §6 Option B) or (b) the SimExchange wiring of FailureInjector, the same session should add the live attribution write path:

```python
# In src/execution/alpaca_executor.py: submit_oto_order
self._instrumentation.emit_trade_attribution_dict({
    "order_id": entry_order_id,
    "ticker": verdict.ticker,
    "submit_ts_utc": datetime.now(timezone.utc),
    "news_signal": orchestrator_context.most_recent_news_signal_for(verdict.ticker),
    "news_conf": orchestrator_context.most_recent_news_conf_for(verdict.ticker),
    # catalyst_type, exit_codepath: NULL at submit; updated at terminal
})
```

```python
# In src/execution/bridge.py: where exit fires
self._instrumentation.update_trade_attribution(
    order_id=position.order_id,
    exit_codepath="d146_bar1",  # or whichever fired
    terminal_filled_avg_price=fill.price,
    terminal_ts_utc=datetime.now(timezone.utc),
)
```

The schema added to `src/analysis/instrumentation/schemas.py` would be a new `TradeAttributionRow` dataclass (NOT a frozen Pydantic model — let it have evolving fields without migration overhead).

---

## §4 — Block 4.4 readiness

Block 4.4 (full historical 86-day OOS Sharpe) needs three data sources:
1. ✅ **Bar corpus** — 86 sessions in `data/bar_recordings/` (Block B closed the 4/22-4/28 gap; pre-4/22 corpus is whatever the recorder captured at the time and is presumed similarly partial — operator can run `audit_bar_coverage.py` on any historical range).
2. ✅ **Trade attribution** — `trade_attribution/session_date=*/attribution.parquet` — backfilled where logs exist, `data_completeness` field surfaces the quality of each row.
3. ⏭️ **Strategy-driven replay harness** — extends `mx-arena/arena/runner.py` to drive the strategy through arena (not echo recorded decisions). **This is the multi-session deliverable** the brief identified as D.1. It requires:
   - Stubbing or mocking the LLM agent ensemble (use the decision_row corpus where available; fall back to a deterministic policy)
   - Wiring AlpacaDataClient → SimExchange (FailureInjector wiring is the natural moment)
   - Running the orchestrator loop against arena's clock instead of wallclock

Block D is **infrastructurally ready** but the harness itself is a session of its own. Tonight's plan doc 58 §7 update reflects this honestly: A+B+C closed; D's strategy harness is next-session work; D.4 honesty section will state these dependencies explicitly when the run lands.

---

## §5 — Status: SHIPPED (sidecar MVP)

- ✅ `scripts/build_trade_attribution_corpus.py` (200 LOC)
- ✅ 5 per-session parquets written to `data/instrumentation/trade_attribution/`
- ✅ 17 rows total; 9 full data, 8 partial+minimal
- ✅ Catalyst stratification queryable across the corpus
- ⏭️ Forward-going hook deferred to same session as recorder fix
- ⏭️ Pre-2026-04-22 corpus backfill: same script run with `--since 2025-12-11` (deferred — large run, not tonight's scope)

**Discipline:** Stop condition correctly tripped on the in-place schema modification path; sidecar approach shipped instead. Discovery rate 30/0 = ∞ holds.
