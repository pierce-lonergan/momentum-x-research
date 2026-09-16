# models.md — per-layer ML/scoring components

For each scoring/prediction component: model type, training data, calibration
data points, current status (production / shadow / research), and pointer to
recent enhancements.

**For pipeline layer numbering, see [`architecture.md`](architecture.md).**

---

## Layer 3 — Agent ensemble (6 parallel signals)

The MFCS input layer. Each agent emits one `AgentSignal` per candidate.

| Agent | Type | Weight | Latency | Failure mode |
|---|---|---|---|---|
| **news_agent** | LLM (Claude 3.5 Sonnet) | **0.30** | ~5-10s | Empty response → D211 raises → D91 fallback |
| **technical_agent** | Deterministic rule-based | **0.20** | <100ms | Always returns (pattern detection: BULL_FLAG, CUP_HANDLE, ASC_TRIANGLE, etc.) |
| **risk_agent** | Deterministic Bayesian-penalty | **0.20** | <100ms | VETO is unconditional (INV-008) |
| **institutional_agent** | LLM (Claude 3.5 Sonnet) | **0.10** | ~5-10s | Same fallback chain as news |
| **fundamental_agent** | LLM (Claude 3.5 Sonnet) | **0.15** | ~5-10s | Pulls SEC EDGAR + insider data |
| **deep_search_agent** | LLM (lightweight Tier-1) | **0.05** | ~3-5s | Supplementary; fires when others lack confidence |

**Weights are HAND-TUNED.** Not data-driven. This is a known gap — filed
in [`backlog.md`](backlog.md) as Tier-2 "Continuous calibration tests per
agent" (Brier / log-loss → auto weight adjustment).

**Resilience:** D91 three-tier fallback chain wraps every LLM call:
- Primary → fallback (different model family) → emergency (Llama 70B)
- D92 emergency tier has defensive `<think>` block parsing
- 15s/10s Tier-1/Tier-2 timeouts
- D279 forwards actual exception to circuit-breaker trip log

---

## Layer 4 — MFCS (Multi-Factor Composite Score)

```
MFCS = Σ w_k · σ_k(S,t) - λ · RISK(S,t)
```

where:
- `w_k` ∈ {0.30, 0.20, 0.20, 0.15, 0.10, 0.05} per agent (above)
- `σ_k` ∈ {-1.0, -0.5, 0.0, +0.5, +1.0} via D101 bipolar mapping
- `λ = 0.3` (default risk penalty)
- MFCS ∈ [-1.0, +1.0]

**Production thresholds:**
- `mfcs ≥ 0.60` → qualifies_for_debate (triggers Layer 5)
- `mfcs ≥ 0.25` → entry candidate (passes MFCS gate)
- `confidence ≥ 0.20` per agent (D116 floor)

**Calibration**: per debate divergence > 0.6, target Sharpe 8.21 (vs single-agent 2-3) per REF-001. This is the doc 162 E1 finding: top-decile +8.94% return.

---

## Layer 5 — Debate engine

| Role | Model | Purpose |
|---|---|---|
| Bull | DeepSeek R1-32B | Strongest BUY case from agent signals + context |
| Bear | DeepSeek R1-32B | Strongest BEAR case |
| Judge | DeepSeek R1-32B | Synthesizes both sides → DebateResult |

**Output:**
- `divergence ∈ [0, 1]` (semantic distance between bull/bear)
- DIV > 0.6 → full position sizing (INV-001)
- DIV ∈ [0.3, 0.6] → half position
- DIV < 0.3 → NO_TRADE (insufficient edge)

**Latency budget:** 30s per candidate (parallel bull/bear, sequential judge).
D217 fallback to neutral on timeout. D218 efficiency extracted gather_timeout
from hardcoded value.

---

## Layer 6 — Meta-Scorer ★ PRODUCTION DECISION PATH ★

`scripts/ml_meta_scorer_inference.py` — the tier classifier and Kelly cap producer.

**Loaded at session startup:**
- `v3-tuned-16-fold` XGBoost ensemble (WF-trained, frozen)
- TCN (Temporal Convolutional Network) for intraday path continuation
- Ising regime detector (macro regime from market state)
- Kelly tier thresholds + conformal calibration widths

**Output: MetaDecision**
```python
{
  "tier": "ELITE" | "HIGH" | "VETOED" | "BROAD" | "SKIP",
  "meta_score": float,         # v3t probability: P(ret_t5 > 0)
  "kelly_frac": float,         # capped per tier (D290 adaptive schedule)
  "conformal_width": float,    # uncertainty quantile
  "reason": str                # audit trail
}
```

**Tier classification cascade (top-down):**

| Tier | Rule | Kelly cap (current) | Win rate (recent WF) |
|---|---|---|---|
| ELITE | `v3t ≥ 0.60 AND mag ∈ {HI, MID}` | 0.50 | ~72% |
| HIGH | `v3t ≥ 0.50 AND mag ∈ {HI, MID}` | **0.50 (D291.5 raised from 0.35)** | ~41% |
| VETOED | `v3t ≥ 0.30 AND mag == MID AND intra_pct < p25` | 0.20 | n/a |
| BROAD | `v3t ≥ 0.30 AND mag ∈ {HI, MID}` | 0.10 | n/a |
| SKIP | below thresholds | 0 | — |

**Bouchaud-optimal (theoretical):** ELITE ~62%, HIGH ~98% (doc 162). Currently
capped conservatively at 0.50 across both.

**Recent enhancements (last 30d):** D281 baseline, D290 adaptive Kelly,
D291.5 HIGH raise, D293.6-7 unlabeled data path (doc 163), D116 confidence
floor, D101 bipolar MFCS.

---

## Layer 7 — Continuer v2 ensemble (SHADOW)

`scripts/ml_continuer_v2_ensemble.py`. **Decision date 2026-06-25.**

**Architecture:** Stacked ensemble.
- Base learners: XGBoost + LightGBM + CatBoost + Logistic + RandomForest
- Meta-learner: Logistic Regression on OOF predictions
- Per-fold conformal calibration (quantile-based width)

**Features (24 total):**
- Base: `gap_pct`, `price`, `dollar_volume`, `premarket_volume`, `volume_ratio`
- Technical: `first_5min_max_close`, `orb_range_pct`, `day_volume`
- Cross-sectional: rank percentile, rolling ticker win-count (`prior_n`, `prior_cont`, `prior_fade`)
- Temporal: time-of-month features
- Microstructure (optional): `sweep_burst_rate`, `dark_pool_pct`, `large_print_pct`, `true_vwap`
- News (optional): `n_articles_24h`, `weighted_sentiment`, `insights_coverage_pct`

**Performance (WF):** +4.05%/trade on T+5 forward return vs v1 baseline.

**Disposition** (per [`experiments.md`](experiments.md) Continuer_v2):
- PASS → ship as Kelly multiplier on tier output
- FAIL → DELETE on 2026-07-15
- Gating metric: Spearman > 0.15 over 30d of T2 live picks

---

## Layer 8 — BOCPD (SHADOW)

`src/analysis/bocpd*.py`. **Decision date 2026-06-15.**

**Algorithm:** Adams-MacKay (2007) simplified-Gaussian variant
- Likelihood: `N(x_t | μ_run, σ_known)`
- Prior: Empirical-Bayes (`μ_edge`, `σ_edge`, hazard) from journal corpus
- Hazard: constant `H = 1/60` (run length ~60 trades)
- CP widening: 3.0× at changepoint

**Output:** Posterior `P(r_t=0 | x_{1:t})` — probability run-length is zero (regime break).

**Planned trigger:** `P(break) > 0.75` → halve Kelly (SOFT kill, not full halt).

**Disposition** (per [`experiments.md`](experiments.md) BOCPD_kill_switch):
- PASS → wire as soft kill-switch
- FAIL → DELETE
- Gating metric: hit rate on detected breaks ≥ 60%

---

## Layer 9 — Kelly Tier Classifier (rule-based cascade)

`src/core/kelly_tier.py`. Top-down evaluation: check Tier 4 first, fall through.

| Tier | Name | Risk % | Max position % |
|---|---|---|---|
| 1 | STANDARD | 1.0% | 25% |
| 2 | HIGH_CONVICTION | 2.0% | 50% |
| 3 | EXCEPTIONAL | 3.5% | 75% (capped by D290) |
| 4 | STATISTICAL_OUTLIER | 5.0% | 100% (capped by D290) |

**Daily loss limit:** 10% (default).

**Recent:** D290 adaptive Kelly by AUM bracket (+60% $PnL at $500k), D291.5 HIGH cap raise.

---

## Layer 10 — Composite v0 (SHADOW)

`src/shadow/composite_shadow.py`. **Decision date 2026-06-30.**

**Architecture:** sklearn LogisticRegression (StandardScaler + L2)
- `composite_v0_full.pkl`: includes `arena_buy_verdict` as feature
- `composite_v0_prescore.pkl`: same WITHOUT arena_buy_verdict (early-filter use)

**Training set:** 407 labeled scenario rows.

**Calibration:**
- Train AUC ~0.68, CV-mean AUC ~0.63 (overfit gap 0.05, below 0.10 threshold)
- Threshold: 0.40 (Phase 3 IS finding, caveats apply)

**Agreement classification:**
- `AGREE_BUY` / `AGREE_NO_TRADE` / `DISAGREE_SHADOW_BUYS` / `DISAGREE_PROD_BUYS`

**Disposition:**
- PASS → graduate to Tier-7 agent input in MFCS (weight 0.05)
- FAIL → DELETE
- Gating metric: agreement rate ≥ 65% over 30d

---

## Cross-layer dependencies

| Layer | Consumes from | Produces for |
|---|---|---|
| 1 Scanner | market ticks | CandidateStock |
| 2 Enrichment | scanner + SEC + ticker_details | enriched CandidateStock |
| 3 Agents | enriched candidate + LLM API | List[AgentSignal] |
| 4 MFCS | List[AgentSignal] | ScoredCandidate |
| 5 Debate | ScoredCandidate (mfcs ≥ 0.60) | DebateResult |
| 6 Meta-Scorer | features + MFCS | MetaDecision (tier, kelly) |
| 7 Continuer v2 | features (unlabeled) | P(ret_t5 > 0) (SHADOW) |
| 8 BOCPD | PnL stream | P(regime break) (SHADOW) |
| 9 Kelly Tier | MFCS, RVOL, VIX, portfolio state | KellyTierResult |
| 10 Composite | features + arena verdict | P(win) (SHADOW) |
| 11 Execution | TradeVerdict + Kelly tier | Alpaca order |

---

## Filed for research (no near-term ship date)

Per [`backlog.md`](backlog.md) Tier-3:

- **Single tool-using LLM** replacing the 6-agent ensemble (Q3 2026)
- **Vision LLM on intraday chart** as Tier-7 agent
- **Embedding-based news retrieval** (BGE-M3 / E5-mistral)
- **SEC EDGAR vector index** (DuckDB + VSS)
- **Historical similar-setup KB** (case-based reasoning, blocked on event-sourced journal)
- **Multi-regime sizing matrix** (Ising regime × Kelly tier)
- **D293.8 ensemble retest at n ≥ 200**

---

## See also

- [`experiments.md`](experiments.md) — decision-dated dispositions for all shadow models
- [`backlog.md`](backlog.md) — Tier-3 research items
- [`d_codes.md`](d_codes.md) — D-code provenance for each enhancement
