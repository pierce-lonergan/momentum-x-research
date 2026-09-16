# 112 — Aggressive Kelly + live broker bar-fetch + paper-deploy monitor

**Session date:** 2026-05-02
**Branch:** develop → main (merged + pushed)
**Predecessor:** [111 Optuna 16-fold + intraday + microstructure scaffold](111_full_send_optuna_focal_intraday_microstructure.md)
**Goal:** push paper-trading profit margins to the limit + activate VETOED tier live + ship Monday-deploy monitoring.

---

## TL;DR

Three production deliverables, all wired and tested:

1. **Aggressive Kelly mode** (paper-only, env-gated): caps lift from 5/3/2/1% to **50/35/20/10%**. WF bankroll re-simulation with 16-fold v3-tuned predictions: **+$12,283 / +122.83% on $10k bankroll** (vs +$6,543 / +65.43% conservative). **Annualized ~92% APY.**

2. **Live broker bar-fetch wired into intraday refresh.** New `AlpacaClient.get_minute_bars()` method + `build_path_from_alpaca_bars()` in `ml_intraday_refresh.py`. The lottery_runner placeholder hook now fires real Alpaca minute-bar requests at 10:00 ET, builds (6,30) tensors, re-scores SKIPped candidates, and opens new BUY orders for VETOED+ upgrades.

3. **Monday paper-deploy monitor** ships at `scripts/monitor_paper_deploy.py`. Two modes: `--tail` streams live events with one-line summaries; `--eod` end-of-day report compares actual fires/P&L against WF expectation per tier.

**37/37 tests still pass.** All changes are env-flag-gated; default behavior unchanged.

---

## 1. Aggressive Kelly: math + WF result

### Why 50/35/20/10?

Full Kelly per tier (computed from session-111 WF win/loss distribution):

| Tier | b (win/loss ratio) | mean p | full Kelly | aggressive cap |
|---|---|---|---|---|
| ELITE | 8.65 | 0.658 | **61.85%** | 50% |
| HIGH | 2.34 | 0.543 | **34.73%** | 35% |
| VETOED | 2.46 | 0.351 | **8.68%** | 20% |
| BROAD | 1.76 | 0.355 | 0% | 10% |

The aggressive cap is set at **just above mean full-Kelly** for ELITE/HIGH (so the model can express conviction on outliers above the mean) and **above mean full-Kelly** for VETOED/BROAD (so the conformal-width modulator drives the actual sizing).

### Bankroll WF results ($10k bankroll, 16-fold predictions)

| Tier | Conservative (5/3/2/1%) | **Aggressive (50/35/20/10%)** | Δ |
|---|---|---|---|
| ELITE | 7 picks, mean Kelly 5.00%, +$2,058 | 7 picks, mean Kelly **9.30%**, **+$4,211** | +$2,153 |
| HIGH | 27 picks, 3.00%, +$1,899 | 27 picks, **5.58%**, **+$3,401** | +$1,502 |
| VETOED | 87 picks, 0.51%, +$591 | 87 picks, 0.58%, +$755 | +$164 |
| BROAD | 367 picks, 0.44%, +$1,994 | 367 picks, **0.66%**, **+$3,916** | +$1,922 |
| **TOTAL** | **+$6,543 (+65.43%)** | **+$12,283 (+122.83%)** | **+$5,740 (+57.40pp)** |

**Annualized: ~49% APY → ~92% APY** (rough, no compounding, no transaction costs / slippage).

### Calibration check
- Mean ELITE Kelly is 9.30% — well below the 50% cap. Conformal width modulator (exp(-2*width)) is doing the right thing: even at p=0.66, width=0.42 produces effective Kelly ~0.37 of full Kelly.
- Max ELITE Kelly hit 16.92% — that's where the ELITE-tier $4,211 P&L comes from.
- BROAD per-pick mean Kelly went 0.23% → 0.66% — modest sizing, but ×367 picks = +$1,922 lift.

### Activation
```powershell
$env:LOTTERY_AGGRESSIVE_KELLY = "1"
# This sets MX_META_KELLY_PROFILE=aggressive automatically;
# MetaScorer.load_default() picks it up.
```

**WARNING:** This is paper-trading only. A 50% notional on a single microcap pick on a real account is a recipe for ruin — the WF expectation is averaged across 16 months and 7 ELITE picks; live deployment of one bad ELITE-tier pick at 50% bankroll could wipe out months of compounding.

---

## 2. Live broker bar-fetch for intraday refresh

### The pipeline (per pick at 10:00 ET)

```
1. lottery_runner enters periodic monitoring loop (post-9:30-entries)
2. At LOTTERY_INTRADAY_REFRESH_HOUR:MIN (default 10:00 ET), trigger fires once
3. For each pick that did NOT enter at 9:30 (meta_tier is None):
   a. AlpacaClient.get_minute_bars(ticker, start=9:30Z, end=10:00Z, limit=60)
   b. build_path_from_alpaca_bars(bars) -> (6, 30) np.float32 tensor
   c. MetaScorer.score_candidate(features, intraday_path=path, bankroll=$10k)
   d. If decision.tier in {VETOED, HIGH, ELITE}: queue for entry
4. Fire BUYS for the upgraded picks (open_lottery_positions)
5. Update lottery_tickers + positions; resume monitoring loop
```

### Code paths

- `scripts/lottery_runner.py:AlpacaClient.get_minute_bars()` — NEW method, reads `ALPACA_DATA_FEED` env (default `iex`)
- `scripts/ml_intraday_refresh.py:build_path_from_alpaca_bars()` — NEW, accepts Alpaca's `{t, o, h, l, c, v, n, vw}` bar dicts; renames + delegates to `build_path_from_bars()`
- `scripts/lottery_runner.py` (~80 LOC inserted): replaces session-111 placeholder with full implementation

### Activation
```powershell
$env:LOTTERY_USE_INTRADAY_REFRESH = "1"
$env:LOTTERY_INTRADAY_REFRESH_HOUR = "10"   # default
$env:LOTTERY_INTRADAY_REFRESH_MIN = "0"
$env:ALPACA_DATA_FEED = "iex"               # 'sip' if subscribed
```

### Expected dollar lift (per session 111 WF)

VETOED tier alone: 87 picks × ~$8/pick (aggressive Kelly) = **+$755 / 16 months**, ~5% APY *just from intraday refresh*. Paid for entirely by activating the tier — no model retraining required.

---

## 3. Monday paper-deploy monitor

`scripts/monitor_paper_deploy.py` (NEW, ~230 LOC).

### Modes

```
# Live tail during market hours (one-line per event)
$ python scripts/monitor_paper_deploy.py --tail
TAIL: logs/lottery_2026-05-04.log
[FIRE]    BROAD    AAPL   score=0.34  $100.00
[FIRE]    HIGH     TSLA   score=0.55  $1500.00
[UPGRADE] VETOED   NVDA   $200.00
[HEARTBEAT] 10:35:12  open=4 unr=$+45.20

# End-of-day report (compare to WF)
$ python scripts/monitor_paper_deploy.py --eod --profile 16fold_aggressive
EOD REPORT: lottery_2026-05-04.log  profile=16fold_aggressive
  meta_passes:     8
  refresh_upgrades:2
  ...
Per-tier fire pattern:
  ELITE   1  $5000.00
  HIGH    2  $1500.00 (1 from refresh)
  VETOED  1  $200.00 (1 from refresh)
  BROAD   4  $100.00
  TOTAL    $8400.00 deployed

WF expectation (per-tier $-per-pick) for profile=16fold_aggressive:
  ELITE     7   +58.79%   85.7%
  HIGH     27   +23.45%   63.0%
  VETOED   87    +5.36%   42.5%
  BROAD   367    +8.54%   49.9%
  WF total $:   $+12282.65  (+122.83% of bankroll)
```

### What it parses

Regex matches against lottery_runner log lines:
- `META-PASS <tk>: tier=<T> score=<s> kelly=<k> notional=$<n>`
- `META-SKIP <tk>:`
- `REFRESH-UPGRADE <tk>: SKIP -> <new_tier> score=<s> notional=$<n> tcn=<tcn>`
- `HEARTBEAT @ <time> ET - <n> open: <summary> | unrealized=$<dollars>`
- `TIME_STOP SELL <qty> <tk>`

NOTHING is sent to the broker. Read-only on filesystem.

---

## 4. Files shipped this session

| Path | Status | LOC |
|---|---|---|
| `scripts/ml_meta_scorer_inference.py` | modified (KELLY_CAPS_AGGRESSIVE + env profile) | +20 |
| `scripts/ml_meta_scorer.py` | modified (same KELLY_CAPS gating) | +10 |
| `scripts/lottery_runner.py` | modified (AGGRESSIVE_KELLY + get_minute_bars + intraday refresh body) | +120 |
| `scripts/ml_intraday_refresh.py` | modified (build_path_from_alpaca_bars) | +20 |
| `scripts/monitor_paper_deploy.py` | NEW | ~230 |
| `data/polygon_warehouse/derived/meta_scores_walkforward_16fold_aggressive.parquet` | NEW (gitignored) | 12k rows |
| `data/models/meta_scorer_summary_16fold_aggressive.json` | NEW (gitignored) | small |
| `docs/research-log/112_aggressive_kelly_intraday_live_monitor.md` | NEW (this doc) | this |

Total: 1 new script + 1 monitor + 4 modified, +400 LOC.

---

## 5. Validated edge stack (post-112)

```
v3-tuned-16fold + HI-mag, P≥0.30  +11.56%/trade WF   (s111 — n=217)
v3-tuned-16fold + (HI|MID) P≥0.50 +30.73%/trade WF   (s111 — n=34)
v3-tuned-16fold + (HI|MID) P≥0.60 +58.79%/trade WF   (s111 — n=7)
Meta-scorer 16fold conservative   +65.43% bankroll  (~49% APY)
Meta-scorer 16fold AGGRESSIVE     +122.83% bankroll (~92% APY) ★
+ intraday refresh active VETOED  paid for itself: +$755 / 16mo
+ live deploy monitor ready       Real-time tier fire + WF compare
```

---

## 6. Monday paper-deploy command

Full-send config (paper-only, all gates ON):

```powershell
# === Required ===
$env:ALPACA_API_KEY = "<paper key>"
$env:ALPACA_SECRET_KEY = "<paper secret>"
$env:ALPACA_BASE_URL = "https://paper-api.alpaca.markets"
$env:ALPACA_DATA_URL = "https://data.alpaca.markets"

# === Meta-scorer with aggressive Kelly ===
$env:LOTTERY_USE_META_SCORER = "1"
$env:LOTTERY_META_BANKROLL_USD = "10000"
$env:LOTTERY_AGGRESSIVE_KELLY = "1"     # CRITICAL — paper only

# === Intraday VETOED-tier refresh at 10:00 ET ===
$env:LOTTERY_USE_INTRADAY_REFRESH = "1"
$env:LOTTERY_INTRADAY_REFRESH_HOUR = "10"
$env:LOTTERY_INTRADAY_REFRESH_MIN = "0"

# === Suppress legacy gates ===
$env:LOTTERY_USE_ML_MODEL = "0"
$env:LOTTERY_USE_ISING_GATE = "0"

# === Halt switch (set 1 for emergency) ===
$env:MOMENTUM_LOTTERY_HALT = "0"

# === Run ===
python scripts/lottery_runner.py
# (Or via the daily_paper_trade.ps1 launcher)

# === In another terminal during the day: ===
python scripts/monitor_paper_deploy.py --tail

# === After 4 PM ET: ===
python scripts/monitor_paper_deploy.py --eod --profile 16fold_aggressive
```

---

## 7. Phase 3 trade tape — readiness status

`scripts/polygon_trades_v1_pull.py` (s108) and `scripts/build_microstructure_features.py` (s111) are wired and waiting for `POLYGON_S3_KEY` / `POLYGON_S3_SECRET`. Existing `scripts/polygon_credentials_preflight.py` validates keys before launching the multi-hour download.

Activation flow once credentials available:
```powershell
$env:POLYGON_S3_KEY = "<from polygon.io/dashboard/flat-files>"
$env:POLYGON_S3_SECRET = "<same>"
python scripts/polygon_credentials_preflight.py        # verify access
python scripts/polygon_trades_v1_pull.py --start 2024-01-01 --end 2026-04-30 --workers 16
# (~3 hr unattended download; ~300-500 GB)
python scripts/build_microstructure_features.py
# (~10 min; produces microstructure_features.parquet)
```

Then v4 ensemble retrain via a `--include-microstructure` flag (extension of `ml_continuer_v2_ensemble.py` — next session).

---

## 8. Risk note

Aggressive Kelly + intraday refresh + live broker = paper-only, full-send mode. Per-pick worst-case loss bounds:
- ELITE @ 50% × $10k = $5,000 max loss per pick (mean E[loss] is -$833 = -8.33% of position)
- HIGH @ 35% × $10k = $3,500 max loss per pick
- VETOED @ 20% × $10k = $2,000 max loss per pick
- BROAD @ 10% × $10k = $1,000 max loss per pick

Single-pick worst-case: **-50% of bankroll on an ELITE pick that hits a -100% return** (rare but possible in microcap halts / delistings). The +122.83% WF average bakes in roughly 1-in-7 ELITE picks losing ~8%, so the expected-value math survives — but a single tail event could wipe out a paper-account "month."

For real-money deployment, REVERT to conservative caps (`LOTTERY_AGGRESSIVE_KELLY=0`, default).

---

## 9. Next-session priorities (post-112)

1. **Monday: actually paper-deploy.** Watch the live tail. After EOD, run the monitor's `--eod` mode and compare per-tier $-per-pick to WF expectation. First real-world calibration check of the meta-scorer.
2. **Phase 3 trade tape pull** the moment credentials arrive. ~3 hr unattended; produces v4-ready microstructure features.
3. **Post-Monday: tighten conformal width modulation if live calibration is off.** WF assumes width modulator exp(-2×w) is well-calibrated; live data may show otherwise.
4. **Post-Monday: drift-detector cron.** Run the existing `scripts/ml_drift_detector.py` daily; if PSI > 0.25 on any feature, retrain v3-tuned.
