# Tomorrow Morning Packet — April 17, 2026

**Read order:** `07_execution_log.md` first (the day's narrative). Then
`08_morning_monitoring_plan.md` (the three checkpoints). Then this packet
(the "what to type" reference).

This document is the literal copy-paste source. Keep it open in a tab.

---

## The four kill-switch env vars

| Env var | Default | What it disables | When to flip |
|---------|---------|------------------|--------------|
| `SHADOW_SCORING_ENABLED` | `true` | Composite shadow logging | If composite shadow throws exceptions on >10% of evaluations |
| `SHADOW_INVERTED_ENABLED` | `true` | Inverted shadow logging (when wired) | If ORB detection produces obviously wrong values |
| `ALPACA_API_KEY` | (in .env) | All trading | If something is fundamentally broken — pause new entries via `/pause` |
| `FINNHUB_API_KEY` | (in .env) | News + float enrichment | Don't flip; news degraded ≠ system broken |

To flip a shadow switch mid-session:

```powershell
# In the running Task Scheduler context, edit .env then send /pause + /resume
# OR set in shell and re-trigger:
$env:SHADOW_SCORING_ENABLED = "false"
schtasks /Run /TN "MomentumX-PaperTrading"
```

The orchestrator checks these env vars on EVERY shadow call. No restart needed.

---

## Config sanity at startup (run this 7:25 AM)

```powershell
cd "<repo-root>"
python -c "from config.settings import Settings; s = Settings(); print(f'max_float={s.router.instant_reject_max_float:,}, min_price={s.router.instant_reject_min_price}, gap_max={s.thresholds.gap_pct_max}, mfcs_buy={s.scoring.mfcs_buy_threshold}')"
```

**Expected output:**
```
max_float=2,000,000,000, min_price=0.5, gap_max=1.0, mfcs_buy=0.25
```

If `max_float=200,000,000`, the D220 branch did not deploy — abort and re-merge.

---

## Three checkpoints (literal commands)

### 7:30 AM ET — confirm process started

```powershell
cd "<repo-root>"
git log --oneline -8                          # should show ~10 D220 commits at top
type data\heartbeat.json                      # phase, pid, branch, commit
curl -s http://localhost:9091/health          # should return status: ok
```

**Expected:** heartbeat shows `branch: develop` (or `d220-full-throttle`),
commit hash matches the deployed branch HEAD, `phase: PHASE_1`.

**Fail signals:**
- No heartbeat → process didn't start. Check Task Scheduler `LastTaskResult`.
- `branch` does not match what you merged → wrong code is running.
- `phase: PHASE_0` after 7:30 → still in pre-market preflight, give it 2 minutes.

### 9:25 AM ET — check evaluation activity

```powershell
cd "<repo-root>"
python -c "
import json, sys, glob, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
from collections import Counter
files = sorted(glob.glob('data/journals/journal_2026-04-17_*.jsonl'))
if not files: print('no journal yet'); sys.exit()
data = open(files[-1], 'rb').read()
text = data[2:].decode('utf-16-le','replace') if data[:2]==b'\xff\xfe' else data.decode('utf-8','replace')
actions = Counter(); gates = Counter()
for line in text.split('\n'):
    if not line.strip(): continue
    try: e = json.loads(line)
    except: continue
    actions[e.get('action','?')] += 1
    if e.get('rejection_reason'): gates[e['rejection_reason'][:60]] += 1
print(f'Actions: {dict(actions)}')
print(f'Top gates:')
for k,v in gates.most_common(5): print(f'  {v:3d}  {k}')
"
```

**Expected:** Evals > 0. Top rejection reasons NOT dominated by D112 router.
At least 1 BUY action.

### 9:36 AM ET — check shadow data (THE NEW THING)

```powershell
cd "<repo-root>"
type data\shadow\shadow_2026-04-17.jsonl | measure | select Count
python -c "
import json
from collections import Counter
rows = [json.loads(l) for l in open('data/shadow/shadow_2026-04-17.jsonl')]
print(f'Total shadow entries: {len(rows)}')
agreement = Counter(r['agreement'] for r in rows)
print(f'Agreement distribution: {dict(agreement)}')
scores = [r['composite_score_full'] for r in rows if r.get('composite_score_full') is not None]
if scores:
    print(f'composite_full: min={min(scores):.3f} mean={sum(scores)/len(scores):.3f} max={max(scores):.3f}')
disagree = [r for r in rows if r['agreement'] == 'DISAGREE_SHADOW_BUYS']
print(f'DISAGREE_SHADOW_BUYS count: {len(disagree)}')
for r in disagree[:5]:
    print(f'  {r[\"ticker\"]}  prod={r[\"production_decision\"]}({r[\"production_gate_rejected\"][:30] if r[\"production_gate_rejected\"] else \"\"})  composite={r[\"composite_score_full\"]:.3f}')
"
```

**Expected:** File exists; 10–50 entries; composite scores in [0,1] with mean ~0.2-0.4;
some DISAGREE entries (tickers the cascade rejected but composite would buy).

---

## Expected composite score range (based on Phase 4 arena analysis)

The composite was trained on synthesized agent signals. Live LLM output will be noisier.
Per Phase 4 arena validation:

- **Mean composite_full on cascade-BUYs:** ~0.225 (model is appropriately pessimistic)
- **Mean composite_full on cascade-rejects (non-ORB):** ~0.65 (high — the model knows
  the cascade anti-selects)
- **Range expected in live data:** [0.05, 0.95], mean somewhere in [0.2, 0.5]

**Warning sign 1:** scores cluster suspiciously high or low (>70% above 0.7 OR
>70% below 0.2) → model miscalibrated for real-LLM signals. Run histogram diagnostic
on Day 6.

**Warning sign 2:** scores are independent of `agreement` (uniform distribution)
→ composite isn't capturing the cascade signal. Run cross-tabulation Day 6.

---

## What to do if shadow log is empty at 9:35

Probable causes (in order of likelihood):
1. `SHADOW_SCORING_ENABLED=false` set somewhere → check env: `echo $env:SHADOW_SCORING_ENABLED`
2. Composite model files missing → `ls models/composite_v0_*.pkl`
3. Orchestrator silently failing on shadow call → grep logs for `composite shadow failed`
4. No evaluations happened → check 9:25 checkpoint

Recovery: if (1), re-set env and re-trigger task.
If (2)-(4), check journal for evals. If evals happened but no shadow entries,
file is a code bug — capture the warning logs and ping me.

---

## What to do if shadow log shows obvious errors

Look for these patterns in `data/shadow/shadow_2026-04-17.jsonl`:

```python
# All scores 0.5 → model failed safely; load_model is broken
[r for r in rows if r['composite_score_full'] == 0.5]

# All scores identical → model is broken differently
len(set(r['composite_score_full'] for r in rows))  # should be > 5

# Schema fields missing → logger ran but data is incomplete
[r for r in rows if 'agreement' not in r or 'production_decision' not in r]
```

If anything looks wrong: flip `SHADOW_SCORING_ENABLED=false` and continue
the trading session. Composite shadow being broken does NOT affect trades —
production decisions don't read shadow data (AST guard enforces this).

---

## Pager / escalation criteria

Page me (text or call) for:
- Process did not start at 4:30 AM (NumberOfMissedRuns incremented)
- 0 evals through 9:30 AM
- 0 BUY verdicts through 9:35 AM (D220 unblock failed)
- Uncaught exception traceback in `logs/paper_2026-04-17.log`
- Equity drops > 3% intraday
- A trade fills > 8% worse than the simulated entry price

Do NOT page me for:
- Shadow log empty / errored (flip switch, continue trading)
- 1-2 trades / day below expected — D220 was a binary unblock; volume is calibration
- Composite scores looking weird (collect data, analyze Day 6)

---

## File index — what to read in order

```
docs/research-log/07_execution_log.md           ← READ FIRST: the day's story
docs/research-log/08_morning_monitoring_plan.md ← READ SECOND: monitoring details
docs/research-log/11_tomorrow_morning_packet.md ← THIS FILE: copy-paste reference
docs/research-log/06_threshold_calibration.md   ← REFERENCE: the corrected calibration
docs/research-log/09_bug_sweep_followups.md     ← REFERENCE: P1/P2 known-issues list
docs/research-log/10_model_arena_smoke.md       ← REFERENCE: Phase 7 model results
                                              (independent of tomorrow's session)
```

---

## Final reminder

**The hard rule:** no live promotion of the inverted strategy until 5+ shadow
sessions confirm the +8-10% per-trade range survives real-LLM noise + realistic
fills. Today is session 0 of that 5. Tomorrow is session 1. Don't rush.
