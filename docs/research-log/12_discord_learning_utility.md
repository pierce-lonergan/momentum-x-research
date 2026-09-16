# Discord Decision Learning channel — D221 Phase F (Sunday-night Tier 3)

**Date:** 2026-04-19 (Sunday → Monday morning)
**Modules:** `src/monitoring/distribution_cache.py`, `src/monitoring/decision_learning.py`
**Channel:** `DECISION_LEARNING_WEBHOOK_URL` (separate from existing
ALERTS / WATCHLIST / HEARTBEAT channels — clean separation per design)
**Threshold env:** `DISCORD_DISAGREE_THRESHOLD` (default 0.55)

## Why this exists

Notifications-as-notifications produced no learning value. The system
emitted "BOUGHT TICKER for $X with MFCS Y" with zero attribution, zero
context, zero ability to debug *why*. Tier 3 transforms each message
into a Paper-1-ingestible data row carrying decision attribution,
statistical context, and the cascade-anti-selection annotation made
visible in real time.

Every message has the same anatomy:
1. **Mechanical outcome** (what happened)
2. **Decision attribution** (who said what, why)
3. **Statistical context** (where this sits historically)
4. **Cascade annotation** (when high consensus, the -0.86 finding flagged)
5. **Paper 1 JSON footer** (structured numerics for downstream analysis)

## The 4 message types

### 1. `post_trade_open_enriched` — fired when a BUY verdict executes
- Per-agent verdicts table (signal + confidence)
- MFCS at entry + percentile rank + historical conversion rate at this level
- Catalyst type + historical win rate for that catalyst
- Path (FAST_PATH / PHASE2_BUY / DEGRADED_MODE) + Kelly tier
- Cascade-consensus annotation if consensus ≥ 0.7 (high agreement = contra)
- Paper 1 JSON footer: ticker, qty, prices, mfcs, percentiles, consensus, catalyst

### 2. `post_trade_close_enriched` — fired at every position close
- Mechanical outcome (qty, prices, PnL, exit reason, hold time)
- **Shapley attribution table:** per-agent-category contribution to PnL,
  sorted by absolute magnitude
- Calibration check: was this outcome expected at this MFCS level?
- Flags: debate_triggered, high_risk_at_entry
- Paper 1 JSON footer: shapley dict, calibration boolean, all numerics

### 3. `post_disagree_shadow_buy` — fires only when interesting
Surfaces cases where composite_shadow score ≥ `DISCORD_DISAGREE_THRESHOLD`
(default 0.55) AND production blocked. The exact slice the v2
cascade-anti-selection thesis predicts is alpha-positive.
- Filtered by allowlist (HARD_VETO_DILUTION, D198_REGIME_HALT, etc.
  suppress message — those are correct-by-design rejections)
- Includes per-agent verdicts, catalyst context, the cascade warning
- Paper 1 JSON footer: composite_score, rejection_code, agent_verdicts,
  consensus, kind=DISAGREE_SHADOW_BUYS

### 4. `post_session_summary_enriched` — end of trading day
- Trades, W/L, win rate, net PnL
- Distribution cache snapshot (n historical observations)
- Cascade stats for the day (agree_buy / disagree_shadow_buys / agree_no_trade)
- Paper 1 JSON footer with the day's aggregates

## Anatomy of the Paper 1 JSON footer

Every message ends with a fenced JSON block containing the structured
numerics behind every human-readable claim. Example from a real
trade-open message:

```json
{
  "catalyst_type": "FDA_APPROVAL",
  "catalyst_winrate": 0.75,
  "catalyst_winrate_n": 4,
  "consensus_strength": 1.0,
  "entry_price": 5.50,
  "kelly_tier": 3,
  "mfcs": 0.62,
  "mfcs_percentile": 0.85,
  "mfcs_winrate_at_level": 0.38,
  "mfcs_winrate_n": 22,
  "path": "PHASE2_BUY",
  "qty": 200,
  "stop_loss": 5.20,
  "ticker": "ACME"
}
```

This makes the Discord channel a Paper 1 dataset, not just a
notification feed. Every message is a row; the JSON block is the
structured-fields portion. Each annotation carries enough context that
post-hoc aggregation requires no replay.

## The cascade-anti-selection annotation

Per the D221 retrain finding (`docs/research-log/composite_retrain_log.md`),
`arena_buy_verdict` has a -0.86 coefficient in v0 composite — meaning
high cascade consensus is *contra-predictive* in this universe.

When agent consensus strength ≥ 0.7, every message includes:

> ⚠ CASCADE CONSENSUS = X.XX — per D221 retrain finding
> (arena_buy_verdict coefficient = -0.86 in v0 composite), high agent
> agreement is *contra-predictive* in this universe. The more agents
> agree, the more this resembles the slice the cascade loses on.

The publishable claim is surfaced in real time. Every "5/5 BULL"
message becomes part of Paper 1's effect-size evidence.

## Distribution cache

`src/monitoring/distribution_cache.py` joins
`data/journals/journal_*.jsonl` (per-evaluation MFCS + agent signals)
with `data/backfill/labels_shards/` + `features_labeled.jsonl`
(per-(date, ticker) outcomes via close_win) on (session_date, ticker).

Currently caches:
- **MFCS distribution** — 2,400 historical observations
- **MFCS-with-outcome** — 157 (overlap of journals × labels)
- **Per-agent confidence + signal histories** — 7 agents
- **Catalyst-type win rates** — 8 catalyst types observed
- **Consensus strength distribution** — 2,398 measurements

Lazy-loaded on first query, memoized for the process lifetime.
Re-instantiate `DistributionCache()` to refresh.

Out of scope (deferred):
- Regime-conditional distributions (waits for 1A/1B Monday)
- Cross-agent interaction terms
- Time-of-day percentiles

## Trust-the-gate allowlist

DISAGREE messages suppress these rejection codes (the system caught a
real risk; not signal disagreement worth surfacing):

```
HARD_VETO_DILUTION       — real S-3/424B5 catch
HARD_VETO_BANKRUPTCY     — real Chapter 11/7 catch
HARD_VETO_SPREAD         — real liquidity catch
D198_REGIME_HALT         — system-correct trading halt
D150_MAX_POSITIONS       — capacity hit
D150_CIRCUIT_BREAKER     — risk control engaged
D56_DUPLICATE            — already in position
D85_FAST_PATH            — already executed via fast_path
EXECUTED                 — trade actually went through
EXECUTED_VIA_FAST_PATH
EXECUTED_VIA_PHASE2
```

To add: edit `_TRUST_THE_GATE_REJECTIONS` in `decision_learning.py`.

## Failure mode

NEVER raises into the caller. Same contract as `alerts.py`. Verified by
adversarial test sweep covering:
- Missing webhook URL → all functions are no-ops
- Malformed agent_signals (None entries, dicts, wrong types) → message ships with degraded info
- Empty distribution cache → annotations fall back to "n/a"-style omission
- Distribution cache load failure → cache returns empty sentinels, no crash

## How to verify activation

```bash
# 1. Confirm env vars are set (without echoing the URL)
grep -c DECISION_LEARNING_WEBHOOK_URL .env
# Expected: 1

# 2. Confirm tests pass
python -m pytest tests/unit/test_distribution_cache.py \
                  tests/unit/test_decision_learning.py -v

# 3. Send a live test message to confirm the wire works
python -c "
import asyncio, os
from pathlib import Path
for line in open('.env', encoding='utf-8'):
    line = line.strip()
    if not line or line.startswith('#') or '=' not in line: continue
    k, _, v = line.partition('='); os.environ.setdefault(k.strip(), v.strip())
from src.monitoring.decision_learning import post_session_summary_enriched
asyncio.run(post_session_summary_enriched(
    trades_count=0, pnl=0.0, wins=0, losses=0,
))
"
# Expected: message appears in #decision-learning channel
```

Re-run any time the integration is in doubt. **First Monday session
must include this verification per the 13th audit item in the Monday
morning checklist.**

## Pacing note for future-Pierce

This shipped past midnight Sunday. Per the Sunday-night discipline:
the 3 AM principled-partial trigger was not pulled because the
distribution-cache bug-sweep produced ZERO bugs (32/32 tests pass on
first run, designed defensively from the start), and the
decision_learning module produced zero functional bugs (one
self-induced test-design issue caught and fixed in 5 minutes).

The 9 silent-failure bugs caught in the prior bug sweep informed the
defensive design here. **The discipline compounds.** This module would
have shipped with at least 3-5 silent-failure surfaces a week ago;
shipping clean tonight is the bug-sweep template paying off in advance.
