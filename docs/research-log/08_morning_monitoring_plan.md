# Tomorrow Morning — Pre-Market Monitoring Plan

**Session:** April 17, 2026 (Friday)
**Trigger:** Task Scheduler @ 4:30 AM ET kicks off the daily paper trade
**Branch deployed:** `d220-full-throttle` (assumes you merged tonight)
**Your time at the keyboard:** ~15 min between 9:25 and 9:45 ET

This is the first session running D220 code — the first session where the
gate cascade SHOULD produce trades. It's also the first session collecting
shadow data. Both things matter; they require different attention.

---

## What success looks like at three checkpoints

### Checkpoint 1 — 7:30 AM ET (pre-market scan kickoff)

**Look at:** `data/heartbeat.json` and `logs/paper_2026-04-17.log` (decoded with
`python -c "import sys; sys.stdout.reconfigure(encoding='utf-8'); print(open('logs/paper_2026-04-17.log','rb').read()[2:].decode('utf-16-le','replace'))"`).

**Expected:**
- Process started cleanly (no preflight failure, no worktree-path errors)
- Watchlist: 15-25 candidates per scan (similar to yesterday's 24)
- Float enrichment populating most candidates with float_shares
- D219 enrichment dropping any implausible Finnhub values with WARNING

**Fail signals:**
- 0 candidates → scanner broke; check `src/scanners/premarket.py` recent changes
- More than 50 candidates → threshold drift; check config
- "implausible shareOutstanding" warnings on >2 tickers per scan → Finnhub feed degraded

### Checkpoint 2 — 9:25 AM ET (evaluation phase)

**Look at:** the live journal `data/journals/journal_2026-04-17_*.jsonl` and
the dashboard's "Evals" counter.

**Expected:**
- Evals > 0 (the bug-of-the-week was 0 evals due to D219 frozen-Pydantic crash)
- D112 router rejection count: <5 (vs 65 yesterday). The Phase 1 fix should
  have flipped this.
- VWAP rejection count: <5 (vs 14 yesterday). The 2% threshold + 10-min skip.
- D101 + D124 consensus rejections: still ~0 (D219 Phase 2 already fixed them)
- **At least 1 BUY verdict.** This is the headline metric. If 0 BUYs,
  trades are still blocked somewhere.

**Fail signals:**
- Evals = 0 → another orchestrator crash. Check today's first 100 log lines
  for "Traceback" or "ValidationError".
- D112 rejections > 20 → my Phase 1 escape hatch isn't firing. Verify
  `src/core/adaptive_router.py:148-167` matches commit `36c5fbb`.
- 0 BUY verdicts → we're at the next bottleneck. Check the journal's
  rejection-reason column for the new top reason. Likely candidates: MFCS
  threshold, ORB observation, position-sizing.

### Checkpoint 3 — 9:36 AM ET (the SHADOW DATA window)

This is the most important checkpoint of the day, and the one nobody has
done before.

**Look at:** `data/shadow/shadow_2026-04-17.jsonl` should now contain
~20-50 entries from the morning's evaluations.

**Expected:**
- File exists and has lines (composite shadow is logging)
- Each line has `kind: "composite_shadow"` with non-null
  `composite_score_full` and `composite_score_prescore`
- Field `agreement` is one of AGREE_BUY / AGREE_NO_TRADE / DISAGREE_SHADOW_BUYS
  / DISAGREE_PROD_BUYS
- DISAGREE_SHADOW_BUYS count: any > 0 is interesting. These are candidates
  the cascade rejected but the composite would have bought. Tomorrow's
  per-day count, accumulated over 5 days, is the inverted-strategy
  shadow signal.

**Fail signals:**
- File doesn't exist → `SHADOW_SCORING_ENABLED` is set to false somewhere
  (check Task Scheduler env, check launcher .env)
- File exists but is empty → orchestrator is silently failing to call
  `maybe_score_composite`. Check warning logs for "composite shadow failed".
- All entries have `composite_score_full = null` → the model file
  (`models/composite_v0_full.pkl`) didn't deploy with the branch. Verify
  the file is on disk.

---

## Kill-switch flip criteria

**Flip `SHADOW_SCORING_ENABLED=false` if:**
- Composite shadow is throwing exceptions on >10% of evaluations
- Composite scores show a degenerate distribution (all 0.5, all 0.0, all 1.0)
- The shadow file is growing faster than 1 KB per evaluation (data quality bug)

**Flip `SHADOW_INVERTED_ENABLED=false` if:**
- Inverted shadow batch (when you wire it Day 6) crashes or times out
- ORB detection produces obviously wrong values
- Decision timestamps are showing up before 9:36

**Both flips are environment-variable changes.** No code edits, no restart.
The orchestrator checks the env var on every shadow call. If you flip mid-session,
the next eval respects the new value.

---

## Recognizing artifacts in live data

The Phase 4 lessons apply to live data too. Watch for these warning signs in
the first 5 sessions of shadow data — if you see them, RUN diagnostics before
trusting the live numbers:

### Warning sign 1 — composite scores cluster suspiciously high or low

If the composite_score_full distribution on the first 100 candidates is
consistently above 0.7 or below 0.2, the model is mis-calibrated for
real-LLM signals. The training data used synthesized signals (deterministic
functions of pre-market features); real LLM noise should produce a more
spread distribution.

**Diagnostic:** plot the histogram on Day 6 evening. Compare to the
training-set distribution stored in `models/composite_v0_metadata.json`.
If the live distribution shifts >0.15 in mean, retrain.

### Warning sign 2 — composite scores are independent of cascade verdict

If `agreement` is uniformly distributed (25% in each of the 4 buckets), the
composite isn't capturing the cascade's signal at all. Either the model is
broken or the live signals are completely different from the training
signals.

**Diagnostic:** compute the cross-tabulation on Day 6.

### Warning sign 3 — the inverted shadow's "would_have_bought" count is huge

If, when you wire the inverted shadow on Day 6 and run it post-9:36, you find
that 60+ candidates per day get marked `would_have_inverted_bought=True`,
something is wrong. The Phase 4 honest result was ~32 trades over 79 days
= ~0.4 per day. Anything that produces 60 per day is either:
- ORB break detection is too loose (firing on noise)
- The cascade's NO_TRADE pool grew dramatically (universe shifted)
- The 9:36 timing is being violated (paranoid schema would catch this; check
  for schema-violation warnings in the log)

### Warning sign 4 — first live trade fills 5%+ worse than the simulated entry

The arena uses labeled bar-derived prices. Real fills have spread + slippage.
If the first inverted-strategy trade (whenever you allow them) fills at
$5.20 against a simulated $5.00 entry (that's 4% slippage), the +9.86%
arena finding shrinks dramatically.

**This is the test the diagnostics couldn't run.** Live fills are the only way
to measure execution-quality cost. Tomorrow morning, even though we're not
auto-trading the inverted strategy, you can manually check: pick one BUY that
the cascade made, compare the actual Alpaca fill price to the arena's
`would_be_entry_price` on the same ticker (need to map by hand for now).

---

## What you should NOT do tomorrow morning

- **Do NOT enable any auto-promotion of the composite score.** The AST guard
  in `tests/static_analysis/test_shadow_isolation.py` blocks it at code
  review, but you could in principle merge a PR that bypasses it. Don't.
- **Do NOT manually add inverted-strategy trades to the live system.** The
  honest +8-10% per-trade range needs 5 shadow sessions before any live
  position is sized.
- **Do NOT panic if the day produces zero trades.** Phase 1 should unblock
  but might not — there's always a next bottleneck. If 0 trades, run
  `python scripts/d220_phase4_diagnostics.py` against today's journal to
  identify the new top rejection reason.
- **Do NOT delete the +16.19% claim from `06_threshold_calibration.md`** if
  someone asks you to "clean up the docs." The visible correction is part of
  the audit trail.

---

## Morning checklist (literal)

```bash
# 7:30 AM ET — confirm process started cleanly
cd "<repo-root>"
cat data/heartbeat.json
curl -s http://localhost:9091/health | head -20

# Confirm the new D220 commits are deployed
git log --oneline -6  # should show de94f22 at top

# 9:25 AM ET — check evaluation activity
ls -la data/journals/journal_2026-04-17_*.jsonl
python -c "
import json, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from collections import Counter
data = open('data/journals/journal_2026-04-17_$(echo *.jsonl).jsonl','rb').read()
text = data[2:].decode('utf-16-le','replace') if data[:2]==b'\xff\xfe' else data.decode('utf-8','replace')
actions = Counter()
gates = Counter()
for line in text.split('\n'):
    if not line.strip(): continue
    try:
        e = json.loads(line)
    except: continue
    actions[e.get('action','?')] += 1
    if e.get('rejection_reason'): gates[e['rejection_reason'][:60]] += 1
print('Actions:', dict(actions))
print('Top 5 gates:')
for k,v in gates.most_common(5): print(f'  {v:3d}  {k}')
"

# 9:36 AM ET — check shadow data
ls -la data/shadow/shadow_2026-04-17.jsonl
wc -l data/shadow/shadow_2026-04-17.jsonl
python -c "
import json
from collections import Counter
rows = [json.loads(l) for l in open('data/shadow/shadow_2026-04-17.jsonl')]
print(f'Total shadow entries: {len(rows)}')
agreement = Counter(r['agreement'] for r in rows)
print('Agreement distribution:', dict(agreement))
scores = [r['composite_score_full'] for r in rows if r.get('composite_score_full') is not None]
if scores:
    print(f'composite_full: min={min(scores):.3f} mean={sum(scores)/len(scores):.3f} max={max(scores):.3f}')
"

# 4:00 PM ET — close-of-session review (or run later in evening)
# Confirm we have at least 1 trade
ls data/trade_results.jsonl 2>/dev/null
wc -l data/trade_results.jsonl 2>/dev/null
```

---

## When to escalate (i.e. ping me)

- Process didn't start at 4:30 AM (Task Scheduler missed-runs counter incremented)
- 0 evals through 9:30 AM (orchestrator broken)
- 0 BUY verdicts through 9:35 AM (gates still cascading)
- Shadow file empty at 9:35 AM (composite shadow broken)
- Any uncaught exception traceback in `logs/paper_2026-04-17.log`
- Equity drops > 3% intraday (drawdown alarm)
- A trade fills > 8% worse than the simulated entry price for that ticker

For each escalation: capture the relevant log lines + journal entries +
shadow entries before paging me. Diagnostic discipline matters more
when you're surprised than when you're not.
