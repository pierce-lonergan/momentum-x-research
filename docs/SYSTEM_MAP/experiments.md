# experiments.md — momentum-x experiment ledger

One TOML block per experiment. `decision_date` is MANDATORY for
`status = "SHADOW"` and `status = "LIVE"` entries. The weekly hygiene
script (`_linter.py`) will fail if any SHADOW entry has a past
`decision_date` without a recorded verdict.

**Format:**

```toml
[Example_Experiment_Schema]
status = "SHADOW"             # PASS | FAIL | REVERT | SHADOW | LIVE | DELETED
decision_date = "2099-12-31"  # required for SHADOW/LIVE (far-future to avoid linter false-positive)
disposition_if_pass = "..."
disposition_if_fail = "..."
docs = ["N", "M"]             # ship doc numbers
one_line = "..."
```

---

## Currently active / shadow experiments

```toml
[T1_stop_widening_shadow]
status = "PASS"
decision_date = "2026-05-19"
hypothesis = "Wide ATR stops outperform tight Phase 1 stops on real live trades"
method = "D308 emitter + D309 replayer; 14 events backfill + live"
key_finding = "+$5,955 wide-stop delta, 86% wide-arm win rate, 6/6 wins live week"
docs = ["169", "170"]
graduated_to = "T2"
one_line = "Observational shadow log + offline replay against minute_aggs."

[T2_stop_widening_ab]
status = "LIVE"
decision_date = "2026-05-28"          # Move 2 gate (Thursday)
hypothesis = "Hash-keyed 50/50 wide/tight A/B isolates stop-width edge"
method = "MD5(date:symbol) % 100, wide_stop gets 0.5x sizing + standalone STOP via L1"
gates = ["MOMENTUM_T2_ENABLED"]
disposition_if_pass = "T3 cutover: delete TrailingStopManager, migrate tight arm to L1"
disposition_if_fail = "Revert MOMENTUM_T2_ENABLED=0; re-examine selection layer (D293.8 path)"
abort_criteria = [
    "Wide-arm cumulative P&L < -$2,000 in first 3 days",
    "Any D313 EMERGENCY_STOP_FAILED",
    "3+ D313 HEDGE_VIOLATION in single session",
]
ratchet_criteria = [
    "Start qty_multiplier = 0.5 (current)",
    "Bump to 0.75 after 5 sessions AND cum > +$3k AND 0 HEDGE_VIOLATION",
    "Full 1.0 after 10 sessions AND cum > +$8k AND 0 HEDGE_VIOLATION",
]
docs = ["171", "172", "173", "174"]
one_line = "Live A/B with L1 standalone-stop + L2 hedge invariant + L2 watchdog + D314 spool."

[Continuer_v2]
status = "SHADOW"
decision_date = "2026-06-25"          # 30 days after T2 stabilization assumed
hypothesis = "Stacked ensemble (XGB+LGBM+Cat+LR+RF -> LR meta) on T+5 forward return adds alpha"
method = "Trained on 407-row labeled set; +4.05%/trade WF reported"
disposition_if_pass = "ship as Kelly multiplier on tier output (e.g. ELITE × pred_uplift)"
disposition_if_fail = "DELETE scripts/continuer_v2_*.py + tests on 2026-07-15"
gating_metric = "Spearman correlation > 0.15 over 30d of T2 live picks"
docs = ["155", "162", "163"]
one_line = "Continuation predictor on aftermath_strat features; perpetual shadow since March."

[BOCPD_kill_switch]
status = "SHADOW"
decision_date = "2026-06-15"
hypothesis = "Bayesian online change-point on rolling P&L detects regime breaks before drawdown"
method = "Adams-MacKay 2007 simplified-Gaussian; prior from journal corpus; hazard 1/60"
disposition_if_pass = "Wire as SOFT kill-switch (halve Kelly when P(break) > 0.75) -- NOT full halt"
disposition_if_fail = "DELETE src/analysis/bocpd*.py + tests"
gating_metric = "Hit rate on detected regime breaks >= 60% (true positives / total triggers)"
docs = ["bocpd.py docstring", "D262 prior pretraining"]
one_line = "Online regime-break detector; wired to logging but never to action."

[Composite_v0_shadow]
status = "SHADOW"
decision_date = "2026-06-30"
hypothesis = "Sklearn LR on (gap_pct, price, dollar_volume, premarket_volume, arena_buy_verdict) predicts close_return > 0"
method = "5-fold stratified CV on 407 labeled scenario rows; train AUC ~0.68, CV AUC ~0.63"
disposition_if_pass = "Graduate to a Tier-7 agent input in MFCS (weight 0.05)"
disposition_if_fail = "DELETE src/shadow/composite_shadow.py + composite_v0_*.pkl artifacts"
gating_metric = "Agreement rate with production on PASS+FAIL cases >= 65% over 30d"
docs = ["D211", "D221"]
one_line = "Logistic-regression composite scorer running write-only since training."

[D293_ensemble]
status = "REVERT"
decision_date = "2026-05-12"
hypothesis = "TabPFN multi-seed ensemble beats single-seed on shadow data"
method = "n_est sweep {1, 2, 4, 8}"
key_finding = "n=2-7 dates insufficient for bootstrap CI gate; per-config Spearman near-identical (best +0.0035)"
disposition = "Reverted to single-seed n_est=2; D293.8 filed for re-test at n >= 200 labeled picks"
docs = ["152", "153", "154", "155", "162"]
one_line = "Multi-seed ensemble reverted; D293.7 (n_est sweep) closed as TIE."

[D293_8_ensemble_retest]
status = "FILED"
target_date = "2026-07-12"             # ~8 weeks of T2 to accumulate 200 picks
hypothesis = "At n >= 200 labeled picks, multi-seed ensemble shows statistically significant improvement"
trigger = "shadow log accumulates >= 200 labeled picks from T2 production"
docs = ["155", "162", "167"]
one_line = "Re-test the D293 ensemble at proper sample size."

[D291_5_HIGH_kelly_raise]
status = "PASS"
decision_date = "2026-05-12"
hypothesis = "n_HIGH >= 50 enables raising HIGH Kelly cap from 0.35 -> 0.50"
method = "Last-60d count + mean return calculation; D162 E3 trigger check"
key_finding = "n_HIGH = 56, mean return +4.13% -- trigger met"
disposition = "SHIPPED 2026-05-12; live monitoring for drawdown"
docs = ["149", "162"]
one_line = "Kelly tier sizing bump; first of a planned D291 series for other tiers."

[D290_adaptive_kelly]
status = "PASS"
decision_date = "2026-05-11"
hypothesis = "AUM-bracketed Kelly schedule beats one-size-fits-all"
method = "Brackets $100k-$2M get higher caps than $5M+"
key_finding = "+60% $PnL at $500k bracket vs flat schedule"
disposition = "SHIPPED"
docs = ["149"]
one_line = "Adaptive Kelly schedule, AUM-dependent."

[D162_E1_cascade_refresh]
status = "MARGINAL"
decision_date = "2026-05-12"
hypothesis = "Cascade alpha holds on fresh May 4-11 data"
key_finding = "Last-30d Spearman +0.1472 (gate >= +0.18 for PASS); mean ret_t5 -2.16%; top-decile still +8.94%"
disposition = "Bot trades with current sizing; flagged for post-EOD review"
docs = ["162"]
one_line = "Cascade alpha degraded but top-tier still strong."

[D281_kelly_baseline]
status = "PASS"
decision_date = "2026-05-12"
hypothesis = "Establish per-tier Kelly baseline at n=25+"
key_finding = "n=25+ per tier validated against empirical win rates"
disposition = "Baseline for D291 series ratchets"
docs = ["pre-148"]
one_line = "Control measurement for Kelly raises."
```

---

## Deferred / filed (not yet started)

```toml
[Move_2_tight_arm_migration]
status = "FILED"
target_date = "2026-05-28"             # Thursday gate per doc 172
hypothesis = "Both arms can use L1 standalone-stop pattern; TrailingStopManager can be deleted"
trigger = "0 D313 HEDGE_VIOLATION across 3 T2 sessions (Mon/Tue/Wed)"
docs = ["171", "172", "174"]
one_line = "Convert L3 from FSM rewrite to deletion."

[Replay_regression_seed]
status = "FILED"
target_date = "2026-05-26"             # Tuesday PM per doc 174
scope = ["NXXT 5/18-5/20 (66h unhedge)", "Friday 5/22 silent-BUY (D216 ate 123)"]
purpose = "CI bedrock + first 2 of 15 historical multi-day unhedges"
docs = ["172", "174"]
one_line = "2-test seed for replay harness."

[Single_LLM_tool_using_agent]
status = "FILED"
target_date = "post-T2-stabilization (~Q3 2026)"
hypothesis = "Single agentic LLM with tool access replaces 6-agent ensemble at lower cost + better synthesis"
risk = "Single point of failure for selection; current ensemble has D91/D92 fallback chain"
gating = "Do not run in parallel with T2 -- conflates experiments"
docs = ["175 deep-dive Part 4 #1"]
one_line = "SOTA selection architecture; deferred until T2 wraps."
```
