# 110 — Live-bot wiring of meta-scorer + TCN focal-loss negative finding

**Session date:** 2026-05-02
**Branch:** develop → main (merged + pushed)
**Predecessor:** [109 v3 + meta-scorer breakthrough](109_v3_meta_scorer_breakthrough.md)

---

## TL;DR

1. **Meta-scorer wired into `lottery_runner.py`** behind `LOTTERY_USE_META_SCORER=1`. New per-pick fields on `Pick` dataclass (`meta_tier`, `meta_notional_usd`, `meta_score`). Tier-based notional overrides global `LOTTERY_NOTIONAL_USD`. **All 13 existing lottery tests still pass.**

2. **`scripts/ml_meta_scorer_inference.py` ships with 24/24 unit tests passing.** Reusable `MetaScorer` class loads v3-tuned ensemble + TCN model + Ising regime once, exposes `score_candidate()` for live decisions, returns `MetaDecision` dataclass.

3. **TCN v2 with focal loss did not beat BCE.** With `alpha=0.75, gamma=2.0` the model collapsed to predicting positive on essentially all rows (P≥0.30 for n=12,057 of 12,057 OOS rows; broad avg unchanged at -3.49%). Honest negative finding — use the original BCE-trained TCN for the inverse-veto signal in production.

---

## 1. Meta-scorer inference module

`scripts/ml_meta_scorer_inference.py` (NEW, ~250 LOC).

### API
```python
from ml_meta_scorer_inference import MetaScorer, MetaDecision

scorer = MetaScorer.load_default()       # loads v3-tuned + TCN + Ising mag once
decision = scorer.score_candidate(
    candidate={"ticker": "X", "log_open": 1.5, ...},  # feature dict
    intraday_path=None,                  # (6, 30) np array if available
    bankroll=10_000.0,
)
# decision = MetaDecision(
#   tier="ELITE", meta_score=0.638, kelly_frac=0.05,
#   notional_usd=500.0, conformal_width=0.42,
#   v3t_proba=0.638, tcn_proba=None, mag_label="MID",
#   reason="v3t>=0.60 AND mag IN (HI, MID)"
# )
```

### Tier waterfall (mirrors session 109 ml_meta_scorer.py)
```
ELITE  : v3t ≥ 0.60  AND mag ∈ {HI, MID}             → kelly cap 5%
HIGH   : v3t ≥ 0.50  AND mag ∈ {HI, MID}             → kelly cap 3%
VETOED : v3t ≥ 0.30  AND mag = MID  AND tcn < 0.30   → kelly cap 2%
BROAD  : v3t ≥ 0.30  AND mag ∈ {HI, MID}             → kelly cap 1%
SKIP   : everything else                              → kelly = 0
```

### Conformal-modulated Kelly
```
base_kelly = (b * p - q) / b           # b = win/loss ratio (per-tier from WF)
width_mod  = exp(-2 * conformal_width)
kelly_frac = clip(base_kelly * width_mod, 0, KELLY_CAPS[tier])
notional   = bankroll * kelly_frac
```

### Test coverage (`scripts/test_meta_scorer_inference.py`)

24 tests, all passing:
- `TestClassifyMag` (3): HI/MID/LO bucketing at ±0.05 thresholds
- `TestAssignTier` (10): every tier triggered + every tier blocked when conditions fail (LO-mag blocks ELITE, HI-mag blocks VETOED, etc.)
- `TestComputeKelly` (5): zero at SKIP, capped at tier cap, zero at breakeven probability, monotonic in conformal width
- `TestScoreCandidate` (4): end-to-end MetaDecision shape, ELITE notional == 5% × bankroll, VETOED requires low TCN, returns proper dataclass
- `TestKellyCapsConsistency` (2): caps are monotonic ELITE > HIGH > VETOED > BROAD > SKIP

---

## 2. Lottery_runner integration

### What changed
- `Pick` dataclass extended with optional `meta_tier`, `meta_notional_usd`, `meta_score` fields
- New env vars: `LOTTERY_USE_META_SCORER` (default 0), `LOTTERY_META_BANKROLL_USD` (default 10000)
- New gate block (~80 LOC) inserted BEFORE legacy ML+Ising gates
- When `USE_META_SCORER=1`:
  - Loads `MetaScorer` once (v3-tuned + TCN + latest mag_5d from ising_daily.parquet)
  - For each candidate: builds full feature dict (incl. priors lookup), calls `score_candidate(intraday_path=None)`, attaches tier + notional to Pick
  - SKIP candidates dropped; rest pass through with per-tier notional
  - Suppresses legacy `USE_ML_MODEL` and `USE_ISING_GATE` blocks for the run
- `open_lottery_positions` reads `p.meta_notional_usd` if set, else falls back to global `NOTIONAL_USD`

### Pre-market behavior

At 9:30 ET when entries fire, no intraday bars exist yet. So:
- `intraday_path=None` is passed → TCN proba is `None`
- `VETOED` tier (which requires `tcn < 0.30`) cannot fire pre-market
- Available tiers at the open: BROAD / HIGH / ELITE
- VETOED tier could fire on a 10:00 ET refresh pass (when 30 min of bars available)

### Test results
**13/13 existing lottery tests still pass** (test_lottery_runner.py). Meta-scorer is gated behind a flag; default behavior unchanged.

### Rollout

```powershell
# Paper Monday: enable meta-scorer, $10k bankroll
$env:LOTTERY_USE_META_SCORER = "1"
$env:LOTTERY_META_BANKROLL_USD = "10000"

# Optional: keep legacy ML / Ising flags off for clean separation
$env:LOTTERY_USE_ML_MODEL = "0"
$env:LOTTERY_USE_ISING_GATE = "0"

# Optional: dry-run first to see what would fire
$env:LOTTERY_DRY_RUN = "1"
python scripts/lottery_runner.py
```

---

## 3. TCN v2 with focal loss — negative finding

### Hypothesis
Focal loss (Lin et al. 2017) down-weights easy examples, focusing gradient on hard-to-classify ones. With our 20.74% positive rate, this should improve calibration of the minority class — useful both for the veto signal and potentially as a standalone tier.

### Setup
```python
class FocalLoss(nn.Module):
    """L = -alpha_t * (1-p_t)^gamma * log(p_t)"""
    def __init__(self, alpha=0.75, gamma=2.0):
        ...
```

Configuration: `alpha=0.75` (up-weight positives), `gamma=2.0` (standard focusing), `epochs=10` (2× the BCE training).

### Result vs BCE baseline (16-fold WF)

| Metric | BCE (5 ep) | Focal (10 ep, α=0.75, γ=2.0) |
|---|---|---|
| Final training loss | 0.51 | 0.06 |
| TCN P≥0.30 n | 8,761 | **12,057** (= every row) |
| TCN P≥0.30 avg | -3.46% | -3.49% |
| TCN P≥0.50 n | 38 | 999 |
| TCN P≥0.50 avg | -6.28% | -3.16% |
| TCN P≥0.60 n | 8 | 0 |
| BOTH (TCN ∩ v2 P≥0.30) | n=539, +4.21% | **n=783, +4.95%** |
| TCN-only (long) | n=8,222, -3.96% | n=11,274, -4.08% |

### Diagnosis

**The model collapsed to predicting positive on essentially all rows.** Final training loss went 0.51 → 0.06, but the per-fold P≥0.30 count went from ~50% of test set to 100% of test set. Focal loss with `alpha=0.75` over-corrected: the model learned to output high probabilities for everything to minimize the up-weighted positive-class loss.

The minor improvement in BOTH-agree (+4.21% → +4.95%) is just because the wider TCN gate now includes more of v2's picks — not because TCN learned anything new about hard cases.

### What to try (deferred)

- **alpha=0.50, gamma=1.0** (no class re-weighting, mild focusing) — likely the sweet spot for our imbalance
- **alpha=0.25** (DOWN-weight positives, force model to find hard negatives) — counterintuitive but matches the original Lin et al. defaults for object detection
- **Temperature scaling post-hoc** — train on BCE then calibrate
- **Longer training (50+ epochs) with early stopping on val AUC** — current 10 epochs may have undertrained even before collapse

### Production decision

**Use the BCE-trained TCN (`tcn_intraday.pt`) for live deployment.** The focal variant is saved as `tcn_intraday_focal.pt` for future experiments but should NOT be wired into the meta-scorer until calibration improves.

---

## 4. Files shipped this session

| Path | Status | LOC |
|---|---|---|
| `scripts/ml_meta_scorer_inference.py` | NEW (live-bot inference module) | ~250 |
| `scripts/test_meta_scorer_inference.py` | NEW (24 unit tests) | ~210 |
| `scripts/lottery_runner.py` | modified (meta-scorer gate + Pick fields) | +95 |
| `scripts/ml_tcn_intraday.py` | modified (FocalLoss class + CLI flags + .pt save) | +75 |
| `data/models/tcn_intraday.pt` | NEW (gitignored, BCE-trained, production-ready) | binary |
| `data/models/tcn_intraday_focal.pt` | NEW (gitignored, focal-trained, deferred) | binary |
| `docs/research-log/110_live_bot_wiring_focal_negative.md` | NEW (this doc) | this |

Total: 2 new scripts, 2 modified, +630 LOC.

---

## 5. Validated edge stack (post-110, unchanged from 109)

```
v2 P≥0.30 baseline                +4.94%/trade WF   (broad, session 106)
v2 + MID-mag overlay              +9.30%/trade WF   (high-EV broad, session 106)
v2 + HI-mag overlay              +10.00%/trade WF   (session 106 milestone)
v2-tuned + HI-mag, P≥0.50        +37.48%/trade WF   (session 107 elite tier)
v3-tuned + (HI|MID), P≥0.60      +59.33%/trade WF   (session 109 — n=11)
Meta-scorer Kelly-sized           +54.76% bankroll  (session 109 — ~41% APY)
Meta-scorer wired into lottery    READY-TO-DEPLOY   (session 110)
```

This session is **deployment-ready, not edge-extending**. The +54.76% bankroll WF result from session 109 is the validated number; session 110 makes it executable.

---

## 6. Branch state at end of session

- `develop` HEAD: 109 commit + 110 commit (this session's work)
- `main` HEAD: merged develop including all session 106-110 work, pushed to origin

---

## 7. Next-session priorities (post-110)

1. **Monday paper-deploy** with `LOTTERY_USE_META_SCORER=1`. Watch for: (a) is `MetaScorer.load_default()` succeeding under the runner's env, (b) which tiers fire, (c) actual P&L vs WF expectation, (d) any ImportError or model-load surprises.
2. **Phase 3 trade tape pull** when `POLYGON_S3_KEY` available. Microstructure features feed v4. Per Compass artifact 2, this is the highest-impact data unlock left.
3. **TCN v2 calibration retry** with `alpha=0.50, gamma=1.0` and early stopping on val AUC. The collapse-to-positive failure mode is solvable.
4. **Drift detector cron**. Already built (session 106); needs to be scheduled. Critical guardrail for production deployment.
5. **VETOED tier intraday refresh**. Add a 10:00 ET re-evaluation pass that runs `intraday_path` from the first 30 min of bars, enabling VETOED-tier picks (the +12.74% slice from session 109).
6. **Re-run Optuna across full 16 folds** (session 107 used only 6); tighter regularization may surface different params.
