# 102 — ML & Sophistication Brainstorm: What Else Could We Ask the Data?

**Date:** 2026-05-02
**Premise:** logistic + XGBoost on 20K rows is the *baseline* — I'd start
there and stop there in a normal project. The user asked me to entertain
**more sophisticated** ideas. This doc is the brainstorm: the questions
worth asking, the approaches worth trying, and the honest filter on
which give us a real edge vs. which look impressive but don't.

---

## §0 — How to read this doc

I'm organizing the ideas into three categories:

1. **The questions worth asking** — framings that change what we measure
2. **The architectures worth trying** — model classes beyond plain GBM
3. **The infrastructure worth building** — uncertainty, calibration,
   monitoring, drift detection

Each idea gets a **rank (★ to ★★★★★)** and a one-line "what would tell us
it's working." The bottom of the doc has the **deployment cookbook**:
the top 5 to actually ship this session, the next 5 for the following
session, and the 5 marked "research bet" for the indefinite-future
exploration.

Notation:
- ★★★★★ — high EV, low cost, ships this session
- ★★★★  — high EV, medium cost, next session
- ★★★   — promising but uncertain payoff, research bet
- ★★    — long shot, mostly intellectual interest
- ★     — fun to think about, not deploying

---

## §1 — The questions worth asking

### Q1 ★★★★★ — What's the predicted CONTINUER PROBABILITY per row, with calibrated uncertainty?

The current per-ticker prior gives a single `smoothed_continuer_rate`
estimate. It doesn't tell us:
- How CONFIDENT we are in that estimate
- How prediction varies with the OTHER features (dvol, intra, oc, day-of-week)
- Whether to take a position or pass (no notion of expected utility)

**What an ML model gives us**: `P(continuer | features)` per row, calibrated,
with conformal-prediction intervals. From this we get:
- **Position sizing via Kelly** (size proportional to expected edge)
- **Selection threshold** (only enter if P(continuer) > X%)
- **Confidence-aware position cap** (if uncertainty is high, scale down)

**Test**: Brier score and calibration plot on walk-forward holdout.

### Q2 ★★★★★ — What's the EXPECTED RETURN E[ret_t5 | features], not just classifier?

Classification (continuer y/n) is binary; regression on ret_t5 captures
magnitude. The information is "this candidate has E[ret_t5] = +8%" not
just "this is a continuer." Sizing follows directly.

**Test**: out-of-sample R² on ret_t5; compare to baseline (mean of
training set).

### Q3 ★★★★★ — Which FEATURE INTERACTIONS matter most?

The single-feature analysis (doc 99 §2) showed continuer rate is FLAT.
The 3D contingency (doc 99 §2.3) showed combinations matter (e.g.,
dvol × intra × prior_7d). A tree-based model auto-discovers these.

**Test**: SHAP values per feature; identify the top 5 interaction pairs.

### Q4 ★★★★ — Does the SAME-day intraday PATH predict T+5? (sequence model)

Currently we use only end-of-day OHLC + volume. But the SHAPE of the
intraday path matters: a stock that gapped, faded by 11:00, then ramped
into close has different forward dynamics than one that ramped all day.

**Test**: predict ret_t5 from sequence of (1-min returns, volumes) over
RTH; compare to flat-feature baseline.

### Q5 ★★★★ — Does TICKER-LEVEL SECTOR / SIC predict continuer differential?

Biotechs fade differently from SPACs which fade differently from
recently-IPO'd techs. Sector dummies might capture systematic patterns.

**Test**: 2D contingency continuer-rate by sector; if any sector deviates
from baseline by >5pp, include.

### Q6 ★★★★ — What's the CAUSAL effect of a ≥30% intraday move on T+5 return?

We've measured CORRELATION (median T+5 = -7%). But what's the
counterfactual: what would the SAME ticker have done WITHOUT the +30% move?
If we control for "Pierce's ticker would have moved -2% anyway based on
its sector + market direction + recent vol," is the +30% gap a +5% bonus
or a -5% penalty?

**Test**: synthetic control / matching estimator; compare gappers to
non-gapper twins from same sector/cap/recent-vol bucket.

### Q7 ★★★★ — Does the WATCHLIST CONSTRUCTION itself contain signal?

The bot's scanner produces a daily watchlist from premarket data. If we
log what FEATURES it used to select each ticker, we can train a
meta-model on "watchlist features" → ret_t5. The watchlist might be
discarding good signals.

**Test**: collect bot's per-ticker scan output; ML on (scan features) → outcome.

### Q8 ★★★ — Do MICROCAP DEALERS have predictable inventory cycles?

PFOF wholesalers (Citadel, Virtu, etc.) internalize ~60-70% of retail
flow. Their inventory positions are unpublished but inferrable from
order-flow imbalance proxies. If we can detect dealer-long positioning
(persistent buying at the offer), expect a fade as they unwind.

**Test**: order-flow imbalance from tick data → ret_t5 regression. Needs
Phase 3 trade tape.

### Q9 ★★★ — Does the LANGUAGE of news headlines carry directional signal beyond Polygon's Insights label?

Polygon gives us a 3-class sentiment per article. The actual headline TEXT
contains structure (e.g., "FDA approves" vs "FDA rejects" vs "FDA delays").
A small LLM (or even regex grammar) could extract cleaner features.

**Test**: prompt Claude with each headline → catalyst_type / direction;
compare against Polygon Insights for predictive power.

### Q10 ★★★ — Are there REGIMES the per-ticker prior doesn't capture?

Per-ticker prior is global. But ADTX in Q1 2024 (post-pandemic biotech
rip) is different from ADTX in Q4 2025 (post-AI mania exhaustion). A
regime-conditioned prior might be sharper.

**Test**: train HMM with 3-4 states on market features (VIX, breadth,
dollar volume, n_huge_up); compute per-ticker rate within each state.

### Q11 ★★ — Does the OPTIONS MARKET know something the equity market doesn't?

For tickers with active option chains (~600 most liquid), unusual options
activity (volume spikes, IV surges) sometimes precedes equity moves.
Polygon Stocks Advanced doesn't include options data — we'd need the
$199/mo Options Advanced add-on.

**Test (if we had data)**: regression: 24-hour options volume change → ret_t5.

### Q12 ★★ — Are there COORDINATED PUMPS detectable via cross-ticker patterns?

When tickers in the same sector all gap together within minutes of each
other, that's coordinated. Detection: temporal clustering of catalog
appearances by sector.

**Test**: cluster (date, sector) → mean continuer rate; compare to
non-clustered sector days.

### Q13 ★ — Could a TRANSFORMER architect attention over (ticker × date) yield emergent patterns?

This is the "use the modern toy" answer. Mamba / FT-Transformer over a
panel of (ticker, date, features) might learn what we can't manually
engineer.

**Test**: train tiny FT-Transformer; compare to XGBoost. If it doesn't
crush XGBoost on tabular data, drop it (DLinear paper applies).

---

## §2 — The architectures worth trying

### A1 ★★★★★ — Gradient Boosting (XGBoost / LightGBM / CatBoost) — THE BASELINE

Industry-standard for tabular data. Auto-discovers feature interactions
via tree splits. Fast, robust, interpretable via SHAP. Scales to our
20K rows trivially.

**Why first**: every more-sophisticated approach should be benchmarked
against GBM. If you can't beat GBM on tabular, your approach is wrong
for tabular.

**Implementation**: ~150 LOC. Walk-forward CV. SHAP feature importance.
Log loss + Brier score on holdout. Sklearn-compatible.

### A2 ★★★★★ — Conformal prediction (calibrated intervals on top of A1)

Wraps any classifier/regressor to produce **distribution-free prediction
intervals at any confidence level** (e.g., 90% CI on P(continuer)).

**Why it matters for trading**: we need to size positions by edge size.
Conformal gives us width = uncertainty. If interval is [0.10, 0.55]
(wide), don't size up; if [0.42, 0.48] (tight), size to confidence.

**Implementation**: ~50 LOC on top of A1. `mapie` library or hand-rolled.

### A3 ★★★★ — Stacked ensemble (logistic + XGBoost + LightGBM + CatBoost + LSTM)

Stack a meta-learner over multiple base learners. Different model classes
make different errors; stacking averages them.

**Risk**: overfitting if validated wrong. Use nested CV.

### A4 ★★★★ — Bayesian regression with shrinkage prior (PyMC / numpyro)

Per-feature coefficients with a prior centered at 0 (e.g., Horseshoe).
The model itself decides which features deserve non-zero weight. Handles
small-sample features (e.g., a sector with only 50 observations) gracefully.

**Why over GBM**: gives proper uncertainty around each coefficient. We
can audit "what is the model actually doing." GBM via SHAP gives
attribution but not principled uncertainty.

**Cost**: 2-5x slower. Worth it if we need scientific confidence about
WHICH features drive predictions (e.g., for explaining why we entered a
trade to ourselves).

### A5 ★★★★ — Temporal Convolutional Network (TCN) on intraday paths

For Q4 above. Take the 390 1-min bars → conv layers with dilations →
predict ret_t5. Faster than LSTM, often more accurate on short sequences.

**Cost**: ~200 LOC + GPU helpful (CPU works for 20K sequences). Need to
set up the input pipeline (390 bars × 5 features per ticker-day).

### A6 ★★★ — Mamba / S4 state-space model on sequences

Per Compass §B.1: linear-time complexity in sequence length. Recent work
(CryptoMamba, FinMamba) shows promise on financial sequences but **not
yet validated on microcap gap-up specifically**. Treat as research bet.

### A7 ★★★ — Graph Neural Network (GNN) over ticker × time

Tickers as nodes; edges = co-occurrence in catalog (same day) OR same
sector. GNN learns node embeddings; downstream MLP predicts ret_t5.

**Use case**: SYMPATHY MOVES — if NVDA pumps, AMD usually follows.
A GNN can encode that.

### A8 ★★★ — Diffusion model for trajectory generation

Per doc 95 IDEA 4. Start with noise → denoise into a coherent
(target, stop, sizing, hold-time) plan. The model never "predicts" —
it iteratively refines.

**Reality check**: every published diffusion-RL trading paper trains on
synthetic data. Real-data sample efficiency is brutal. **Research bet
ranked low because we lack the data volume to make it work.**

### A9 ★★★ — Online learning (River / Vowpal Wabbit)

Streaming model that updates with each new arena outcome. No retraining
cron. The model is always current.

**Why interesting**: continuous learning = less drift. **Why hard**:
walk-forward validation becomes more complex; need to think carefully
about positive feedback loops.

### A10 ★★ — TabPFN (foundation model for tabular)

Pre-trained transformer for tabular classification. Zero-shot — feed
your training set + a query, get a prediction. Performs surprisingly
well on small tables.

**Limitation**: max ~1000 training samples and ~100 features per
inference. We have 20K rows; would need to chunk or use as ensemble
member.

### A11 ★★ — Symbolic regression (PySR)

Discovers algebraic formulas (e.g., `ret_t5 ≈ -0.05 * intra + 0.03 * sqrt(dvol_M)`)
that fit the data. Interpretable, sometimes finds unexpected patterns.

**Cost**: slow (genetic algorithm). Usually returns formulas that look
like overfit polynomials. Worth one experiment.

### A12 ★ — Reinforcement learning (PPO / SAC over portfolio actions)

Frame the lottery as a sequential decision problem; learn a policy that
maps daily state → portfolio allocation.

**Reality**: RL on financial data is a graveyard of failed papers.
Training-distribution shift, reward sparsity, and non-stationarity are
all severe. **Don't do this until everything else is exhausted.**

---

## §3 — The infrastructure worth building

### I1 ★★★★★ — Walk-forward validation framework

Every model we ship MUST pass walk-forward. Build a reusable harness:

```python
# pseudo
def walk_forward_cv(df, model_factory, cutoff_dates):
    results = []
    for cutoff in cutoff_dates:
        train = df[df.d0 < cutoff]
        test = df[(df.d0 >= cutoff) & (df.d0 < cutoff + 30d)]
        model = model_factory().fit(train)
        results.append(evaluate(model, test))
    return results
```

This generalizes the doc 100 §3 logic for any model.

### I2 ★★★★★ — Drift detection + retraining cadence

Log (rolling 30-day model accuracy, calibration error). When either
exceeds threshold, trigger retraining. Don't retrain blindly weekly —
retrain when the model drifts.

### I3 ★★★★★ — Model versioning + serving

Each trained model has a version hash + training-period metadata. The
runner specifies which version. We can rollback if a new version
underperforms.

Lightweight: store models as pickled artifacts in
`data/models/{strategy}/{version}.pkl` + manifest JSON.

### I4 ★★★★ — Feature store (canonical, point-in-time-correct features)

Each row in `aftermath_strat.parquet` should have ALL features pre-computed
at d0 time. This guarantees no future-leak.

What goes in: dvol, intra, oc, prior_n, prior_cont (WF), prior_7d,
sector, mcap, days_since_ipo, sector_breadth_today, vix_today,
last_split_days_ago, etc.

### I5 ★★★★ — Per-prediction logging (for live model audit)

Every prediction the live model makes goes into `data/predictions/{date}.parquet`
with: features, predicted_proba, predicted_return, conformal_interval,
actual_outcome (filled in T+5 days). We can re-evaluate model
performance at any time.

### I6 ★★★★ — Backtested-vs-live drift monitor

Daily compare: (live predictions on today's catalog) vs (model's
backtest distribution). If live is meaningfully different, the
deployment env is drifting from training env.

### I7 ★★★★ — Position sizer (Kelly + conformal-width-aware)

Given P(continuer), conformal interval width, and edge size, size the
position. Kelly-optimal but capped at fractional Kelly (e.g., 25%) for
safety. Conformal width modulates: wider → smaller size.

```python
def size(p_continuer, ci_width, edge_pct, cap_pct=0.05):
    kelly = (p_continuer * win_pct - (1 - p_continuer) * loss_pct) / win_pct
    kelly_frac = kelly * 0.25  # fractional Kelly
    width_modifier = max(0.2, 1 - ci_width / 0.3)  # narrow CI → full size
    return min(cap_pct, kelly_frac * width_modifier)
```

### I8 ★★★ — Hyperparameter tuning (Optuna / Ray Tune)

For GBM. Cross-validated. Runs overnight. Don't hand-tune.

### I9 ★★★ — Counterfactual analysis: "what if we'd had this model 6 months ago"

Take the current model, replay it on every (date, ticker) pair from 12
months ago. Plot what trades it would have made vs what we actually made.
Quantifies the lift from the new model.

### I10 ★★ — Distributional reinforcement (predict the WHOLE distribution of ret_t5, not just mean)

Quantile regression forests / quantile loss XGBoost. Output: `[q10, q50, q90]`
of ret_t5 per row. Gives us tail risk explicitly.

---

## §4 — The deployment cookbook

### §4.1 — Ship THIS session (top 5)

1. **A1: Gradient Boosting baseline** (XGBoost) on engineered features
2. **A2: Conformal prediction** for calibrated intervals
3. **I1: Walk-forward validation harness**
4. **I7: Position sizer (Kelly + width-aware)**
5. **I3: Model versioning** (filesystem-based)

These give us an end-to-end ML pipeline with calibration and sizing.

### §4.2 — Ship next session (top 5)

1. **A3: Stacked ensemble** (XGB + LGBM + CatBoost + logistic)
2. **I2: Drift detection** + retraining cadence
3. **I5: Per-prediction logging** for live audit
4. **Q5: Sector dummies** (need ticker_details enrichment first)
5. **A4: Bayesian baseline** (PyMC) for principled uncertainty

### §4.3 — Research bets (no commitment)

1. **A5: TCN on intraday paths** — needs minute-bar feature pipeline
2. **A7: Graph NN for sympathy moves** — needs sector graph
3. **Q9: LLM-extracted catalyst features** — needs Claude API + cost
4. **Q10: Regime-conditioned per-ticker prior** — extends our HMM
5. **A6: Mamba on sequences** — promising but unvalidated for our domain

### §4.4 — Don't bother (bottom 5)

1. **A12: Reinforcement learning** — graveyard
2. **A10: TabPFN** — too small for our data
3. **A8: Diffusion** — sample efficiency
4. **A11: Symbolic regression** — usually overfits
5. **Q11: Options data** — costs $199/mo extra for limited microcap coverage

---

## §5 — The integration question (how does this fit into the bot?)

### §5.1 — Lottery long integration

Today: `lottery_runner.py` has a binary continuer-prior gate (rate < 3% →
GATE OUT). With ML:
1. At 09:30 ET, for each candidate, compute features (dvol, intra, oc,
   prior_n, prior_cont, prior_7d, sector, ...)
2. Model predicts P(continuer | features) + conformal interval
3. Sizer outputs notional based on Kelly × width modifier
4. Submit market buy at that notional with trail-15

**Latency budget**: model inference + Kelly + sizer must complete in <2s
so we can submit by 09:30:02.

### §5.2 — Fader short integration

Today: `fader_short_runner.py` has hard-coded S2/S3 gates. With ML:
1. At 15:50 ET, for each candidate, compute features
2. Model predicts P(fader | features) + conformal interval
3. Sizer outputs notional
4. Submit short with bracket

**Same latency budget** since 15:50 is well before the 16:00 close.

### §5.3 — H3 integration (when helper variant ships)

Today: H3 picks top-1 per day by first-30-min momentum, no model.
With ML: predict P(target_hit) per H3 candidate; rank by P(target_hit) ×
edge_size; only enter top-1 if P × edge > threshold.

### §5.4 — Capital allocator integration

Today: per-strategy notional based on Ising regime alone.
With ML:
1. ML predicts per-strategy expected daily P&L from current regime
2. Allocator uses that to weight: more capital to whichever strategy
   the model expects to perform best today
3. Conformal width on each prediction modulates the size

---

## §6 — The honest tradeoffs

### Where ML helps a lot
- **Calibration**: P(continuer) instead of binary gate
- **Multi-feature interactions**: SHAP-discovered combinations
- **Sizing**: Kelly with proper edge estimates
- **Drift detection**: knowing when to retrain

### Where ML over-promises
- **Sample size**: 20K rows × ~10 features ≈ 200K data points. Modern
  deep nets need 100M+. Tabular GBM is the right scale for our data.
- **Non-stationarity**: model trained on 2024 may be wrong for 2026.
  Walk-forward is the only honest test.
- **Survivorship bias**: per-ticker prior we already saw decay
  87% out-of-sample (V4). ML adds *more* features that can mine the
  past in misleading ways unless we're rigorous about WF.

### Where to be skeptical
- Any reported in-sample lift > 5% above WF baseline
- Any model that requires GPU training
- Any model that doesn't beat the per-ticker prior + simple gate
- Any model whose top feature in SHAP is the target leak (verify!)

---

## §7 — Three things that would HONESTLY change my opinion

If A1+A2 (XGBoost + conformal) on walk-forward beats the current
chronic-fader gate by ≥1pp average, **the ML pipeline gets shipped to
production**.

If A1+A2 only matches the current gate, **the ML stays in research**;
the gate is simpler and easier to reason about.

If A1+A2 underperforms the current gate, **we trust the manual feature
engineering** and don't deploy more ML until we have more data
(2026-Q4+).

The question is whether ML adds enough lift to justify the operational
complexity (model versioning, drift monitoring, retraining cadence).
Doc 103 will answer that with the actual model results.

---

## §8 — Files to ship this session

| Path | Purpose |
|---|---|
| `scripts/ml_continuer_model.py` | A1 + A2: XGBoost + conformal, with walk-forward |
| `scripts/ml_walkforward_harness.py` | I1: reusable WF harness |
| `scripts/ml_position_sizer.py` | I7: Kelly + width-aware sizer |
| `data/models/continuer_v1.pkl` | I3: trained model artifact |
| `data/models/continuer_v1_manifest.json` | Versioning + metadata |
| `docs/research-log/103_ml_results.md` | Empirical results doc |

The integration into `lottery_runner.py` and `fader_short_runner.py`
will follow once the model is validated.

---

## §9 — One paragraph summary

> "More sophisticated than logistic + XGBoost is a long ladder. The first
> rung that matters is **conformal prediction** — calibrated uncertainty
> intervals on top of any model — because position sizing requires
> uncertainty, not just point estimates. Beyond that: stacked ensembles
> (free lift), Bayesian regression (interpretable uncertainty), TCNs on
> intraday paths (Q4), and graph nets for sympathy moves (Q5). The
> research-bet tier (Mamba, diffusion, TabPFN) is intellectually
> interesting but our 20K rows are too small to validate them honestly.
> The most underrated infrastructure piece is **walk-forward validation
> as a reusable harness** — without it every model is in-sample fiction.
> Doc 103 will report whether XGBoost + conformal actually beats the
> manual chronic-fader gate; if not, we keep the gate."
