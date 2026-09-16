# glossary.md — acronyms + jargon used in momentum-x

Single source-of-truth for terminology. If a term appears in a doc, ship
log, or D-code description and isn't here, add it.

---

## Core trading terms

| Term | Meaning |
|---|---|
| **OTO** | One-Triggers-Other order. Alpaca order type: parent BUY limit + stop child that activates only after parent fills. Atomic; eliminates the "fill without stop" window. |
| **bracket** | OTO with two children: stop + take-profit. Used by `submit_bracket_order`. |
| **standalone STOP** | Plain stop order NOT attached to a parent OTO. L1 wide-arm path uses this to bypass TrailingStopManager (D310). |
| **gap_pct** | Premarket gap as fraction of prior close: `(premarket_price - prior_close) / prior_close`. Positive = gap up. |
| **RVOL** | Relative volume vs 20-day average at same time of day. Microcap gaps are typically RVOL > 3x. |
| **dvol_d0** | Day-zero dollar volume = `volume × price`. Used for the 5% participation cap (D285). |
| **MFE** | Maximum Favorable Excursion: peak unrealized gain during the trade. |
| **MAE** | Maximum Adverse Excursion: trough unrealized loss during the trade. |
| **TIF** | Time-in-force: `day` (expires 16:00 ET), `gtc` (good-til-canceled), `gtd` (good-til-date). Protective stops MUST be `gtc`. |
| **D0 / D1** | "Day zero" = trade entry day. "Day one" = next session. `ret_t5` = T+5-day forward return. |

---

## momentum-x specific

| Term | Meaning |
|---|---|
| **MFCS** | Multi-Factor Composite Score. Weighted-linear of 6 AgentSignals, ∈ [-1, +1] (D101). |
| **AgentSignal** | Output of one of the 6 agents in Layer 3. Signal ∈ {STRONG_BULL, BULL, NEUTRAL, BEAR, STRONG_BEAR}, plus confidence ∈ [0,1]. |
| **TradeVerdict** | Final pipeline output. Action ∈ {STRONG_BUY, BUY, HOLD, NO_TRADE}, plus entry_price, stop_loss, target_prices, kelly_tier, execution_arm (D310). |
| **execution_arm** | T2 A/B label. `wide_stop` = L1 standalone-stop + halved qty. `tight_stop` = legacy D142 Phase 1 OTO. Set in orchestrator via `assigned_arm_for_verdict`. |
| **tier** | Meta-scorer output ∈ {ELITE, HIGH, VETOED, BROAD, SKIP}. Determines Kelly cap. |
| **v3 / v3t** | The current production XGBoost meta-scorer ensemble. `v3t` = "tuned" 16-fold variant. Produces `meta_score = P(ret_t5 > 0)`. |
| **debate divergence** | Semantic distance between bull and bear LLM advocacies. DIV > 0.6 → full size; < 0.3 → NO_TRADE. |
| **conformal width** | Uncertainty quantile from meta-scorer; used to widen/tighten sizing on uncertain candidates. |
| **kelly_frac** | Position size as fraction of bankroll. Capped per tier; modified by `qty_multiplier` (T2 wide arm = 0.5x). |
| **aftermath_strat** | The training-data parquet: `data/polygon_warehouse/derived/aftermath_strat.parquet`. 20k+ rows of (ticker, d0, features, ret_t5). |

---

## Defense / safety / observability

| Term | Meaning |
|---|---|
| **L1** | T2 standalone-stop path. Wide arm submits plain BUY limit + separate stop, bypasses TrailingStopManager. |
| **L2** | HedgeIntegrityWatcher. Polls broker every 20s, enforces `qty != 0 → has_protective_order`. |
| **L2_WATCHDOG** | The watcher-of-watchers. Pages CRITICAL if L2 heartbeat > 90s stale. |
| **L3** | The proper TrailingStopManager FSM rewrite OR deletion (Move 2 decision Thursday 5/28). |
| **Move 2** | Migrate tight arm to L1 pattern, delete TrailingStopManager. Pierce's "deletion not rewrite" framing. |
| **HEDGE_VIOLATION** | D313 alert: position has been unhedged > 60s tolerance. Auto-submits emergency stop. |
| **EMERGENCY_STOP_FAILED** | L2 attempted emergency stop, broker refused. Operator must intervene manually. |
| **wide-arm** | T2 execution_arm = "wide_stop": ATR-based stop, halved qty, L1 standalone-stop path. |
| **tight-arm** | T2 execution_arm = "tight_stop": legacy D142 Phase 1 1.5% stop in OTO order class. |
| **dead callback** | The TrailingStopManager's missing executor.on_submit_trailing implementation. Lived as comment-only from 2026-02-02 to 2026-05-24 (4 months). |
| **D314 spool** | Durable Discord webhook: every alert written to `data/alerts/YYYY-MM-DD/*.json` BEFORE live POST. Retry loop redelivers. |
| **phantom journal** | `src/data/phantom_journal.py` — records every BUY verdict BEFORE gates can block it, with gate attribution. D312 surfaces aggregate counts. |

---

## Pipeline + infrastructure

| Term | Meaning |
|---|---|
| **Phase 0-4** | Bot session phases. P0=pre-startup; P1=pre-market scan; P2=evaluation; P3=position management (60s loop); P4=EOD close. |
| **D278 t1_next_open** | Carry-overnight exit policy. Skip same-day close, exit at next session's market open via D91. |
| **D91 next-open path** | The close routine that fires when a carry-overnight position needs to exit at next-day market open. |
| **D87 circuit breaker** | LLM-call circuit breaker. Trips after consecutive failures, fails-fast for ~60s before retry. |
| **D91 fallback chain** | Three-tier LLM resilience: primary → fallback (different family) → emergency (Llama 70B). |
| **Polygon flat-files** | S3-hosted CSV/parquet of OHLCV by day/minute. Source for `polygon_warehouse/` lake. |
| **aftermath catalog** | The pipeline: polygon_flatfile_pull → polygon_parquet_warehouse → polygon_high_mover_catalog → polygon_aftermath_catalog → aftermath_strat.parquet. |
| **shadow log** | Write-only JSONL output of a shadow system (e.g. `data/shadow_stops/stop_decisions_*.jsonl` for D308). |

---

## Files & locations

| Path | Contents |
|---|---|
| `main.py` | Bot entry point. Phase 0-4 loop. Wires all subsystems. |
| `src/core/orchestrator.py` | The cycle engine. Hosts the candidate-evaluation pipeline (Layers 1-10). |
| `src/core/scoring.py` | MFCS implementation (Layer 4). |
| `src/core/kelly_tier.py` | Kelly tier classifier (Layer 9). |
| `src/agents/` | 6 agents (Layer 3). |
| `src/execution/bridge.py` | Order-state lifecycle, D91 close routine. |
| `src/execution/alpaca_executor.py` | OTO submission, D142 Phase 1 override, T2 wide-arm L1 path. |
| `src/execution/t2_arm_assignment.py` | T2 A/B arm hash logic (D310). |
| `src/monitoring/alerts.py` | Discord alert composers + `_post` / `_post_sync` durable routing. |
| `src/monitoring/durable_alert_spool.py` | D314 spool + retry loop. |
| `src/monitoring/hedge_integrity_watcher.py` | D313 L2 watcher. |
| `src/data/phantom_journal.py` | D211 phantom verdict log (D312 source). |
| `src/analysis/trade_journal.py` | The journal of records (will become event-sourced per `backlog.md` D317). |
| `scripts/ml_meta_scorer_inference.py` | Layer 6 production decision engine. |
| `scripts/ml_continuer_v2_ensemble.py` | Layer 7 shadow. |
| `scripts/tabpfn_shadow_runner.py` | TabPFN shadow predictor. |
| `data/polygon_warehouse/derived/aftermath_strat.parquet` | Training data lake. |
| `data/shadow_stops/` | D308 stop_decisions.jsonl + replay_results.parquet. |
| `data/alerts/YYYY-MM-DD/*.json` | D314 spool. |
| `docs/research-log/N.md` | Ship docs (append-only history). |
| `docs/SYSTEM_MAP/` | Source of truth (current state). |
| `docs/RUNBOOK_HEDGE_VIOLATION.md` | Operator runbook for 60s response. |
| `.githooks/pre-commit` | Discipline enforcer: ship doc add ⇒ changelog update required. |

---

## Acronym index

| Acronym | Expansion |
|---|---|
| **ADR** | Architecture Decision Record |
| **ATR** | Average True Range (volatility measure) |
| **BOCPD** | Bayesian Online Change-Point Detection |
| **CRCA** | Stock ticker (appeared in 90-day audit) |
| **DFL** | Decision-Focused Learning |
| **EOD** | End of Day |
| **ET** | Eastern Time |
| **GTC** | Good-Til-Canceled (TIF) |
| **GTD** | Good-Til-Date (TIF) |
| **HTB** | Hard-to-Borrow (non-shortable indicator) |
| **INV** | Invariant (assertion that must hold) |
| **JZXN, RPGL, XWEL, etc.** | Stock tickers from historical audit |
| **MAE / MFE** | Maximum Adverse / Favorable Excursion |
| **MCC** | Matthews Correlation Coefficient |
| **NSS** | Naked Short Selling (non-shortable indicator) |
| **OOF** | Out-of-Fold (cross-validation predictions) |
| **OTO** | One-Triggers-Other (Alpaca order type) |
| **REF** | Reference (cited result, e.g., REF-001) |
| **RVOL** | Relative Volume |
| **SOTA** | State of the Art |
| **SPOF** | Single Point of Failure |
| **TCN** | Temporal Convolutional Network |
| **TIF** | Time-In-Force |
| **UOA** | Unusual Options Activity |
| **VSS** | Vector Similarity Search (DuckDB extension) |
| **WF** | Walk-Forward (validation methodology) |
