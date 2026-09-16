# Paper 1 — Data Inventory

**Date:** 2026-04-19 (Sunday)
**Audit script:** none — direct walk of `data/journals/` + schema check
**Result:** OUTCOME 1 from Sunday morning's parity-data audit. Per-agent
reasoning is preserved. Paper 1 is unblocked, writeable from data
already on disk.

---

## TL;DR

Per-agent reasoning text is preserved in `data/journals/journal_*.jsonl`
across 18 sessions spanning Feb 10 – Apr 17 2026. **2,400 candidate
evaluations × 13,053 agent-signal records** with `reasoning`, `signal`,
`confidence`, `key_data`, `sources_used`, `risk_verdict`, and
`veto_reason` fields per agent. No schema evolution gaps — every journal
file has the `agent_signals` field with consistent structure.

This is well past the threshold for Paper 1's consensus-strength analysis
with bootstrap CIs. The "draft this week" estimate from Saturday's deep
dive is confirmed.

---

## Data shape

### Per-session totals

| File pattern | Files | Date range | Total candidate rows |
|---|---|---|---|
| `journal_2026-02-*.jsonl` | 14 | Feb 10 – Feb 24 | ~1,900 |
| `journal_2026-04-*.jsonl` | 4 | Apr 14 – Apr 17 | ~500 |
| **Total** | **18** | **Feb 10 – Apr 17** | **2,400** |

Gap in March (Feb 24 → Apr 14) is the system-rebuild period; no live
sessions ran. Doesn't affect Paper 1 — the surviving data still spans
two regime windows (Feb winter regime + April spring regime).

### Per-agent record counts

| agent_id | n records | coverage |
|---|---|---|
| technical_agent | 2,397 | ~100% of evaluations |
| fundamental_agent | 2,374 | ~99% |
| news_agent | 2,292 | ~96% |
| risk_agent | 2,119 | ~88% |
| institutional_agent | 1,900 | ~79% |
| deep_search_agent | 1,900 | ~79% |
| manipulation_classifier | 71 | ~3% (rarely fires) |

The 5-agent core (technical / fundamental / news / risk / institutional)
covers the vast majority of evaluations. `deep_search_agent` runs less
often (probably gated). `manipulation_classifier` is rare by design.

### Per-agent signal record schema

```python
{
    "agent_id":             str,         # e.g. "news_agent"
    "model_id":             str,         # which LLM produced this
    "prompt_variant_id":    str,
    "signal":               str,         # STRONG_BULL/BULL/NEUTRAL/BEAR/STRONG_BEAR
    "confidence":           float,       # 0.0 - 1.0
    "reasoning":            str,         # full text, ~50-500 chars typical
    "key_data":             dict,        # agent-specific structured fields
    "sources_used":         list[str],
    "latency_ms":           float,

    # Risk-agent-specific (None for others):
    "risk_verdict":         str | None,  # APPROVE / CAUTION / VETO
    "risk_score":           float | None,
    "veto_reason":          str | None,

    # News-agent-specific (None for others):
    "catalyst_type":        str | None,
    "catalyst_specificity": str | None,
    "sentiment_score":      float | None,

    # Technical-agent-specific (None for others):
    "pattern_identified":   str | None,
    "breakout_confirmed":   bool | None,
}
```

---

## What this enables for Paper 1

### Section 3: Adversarial Consensus Effect Size

**Computable directly from existing data:**

1. **Consensus-strength metric** per (date, ticker):
   - Map each agent's signal to {-2, -1, 0, +1, +2} numeric scale.
   - Compute std-dev across agents for that candidate (low std = high
     consensus).
   - Or: pairwise cosine distance of agent verdict vectors.
   - Or: information-theoretic entropy over the verdict distribution.

2. **Realized return** per (date, ticker) at multiple horizons (T+5, T+15,
   T+60, close): joinable from `data/backfill/labels_shards/` plus
   `data/backfill/features_labeled.jsonl`. Already on disk.

3. **Spearman correlation** of consensus_strength × realized_return
   at each horizon. Bootstrap CIs at 5,000 resamples.

4. **Regime stratification** by date or by VIX (after Sunday's VIX
   backfill, if needed).

### Section 4: Mechanism Discussion

The `reasoning` field is the substrate for the three falsifiable
mechanism statements:

- **Narrative density / retail saturation:** count distinct narrative
  threads per candidate via TF-IDF clustering on `reasoning` text.
  Predict: high narrative-density candidates have lower returns.
- **Corpus-induced bias:** measure semantic similarity of agent reasoning
  to known-pump-language baselines. Predict: high-similarity candidates
  have lower returns.
- **Agreement-as-killer:** the consensus-strength analysis from Section 3
  itself. Predict: high consensus correlates with low returns.

All three are computable from `reasoning` text + label data, both
already on disk.

### Section 5: Three-Distribution Effect Size Table

The v0/v99/v1 nested training distributions from yesterday's retrain log
(`docs/research-log/composite_retrain_log.md`) provide the headline figure.
Effect size for `arena_buy_verdict` coefficient:

| Distribution | n_rows | coef | CV AUC | Stratified gap (NO_TRADE - BUY) |
|---|---|---|---|---|
| v0 (narrow regime) | 407 | -0.86 | 0.7564 | +38.0pp |
| v99 (narrow + EST fix) | 497 | -0.76 | 0.7339 | +33.0pp |
| v1 (broad) | 5,774 | (negative, smaller) | 0.5729 | +9.9pp |

**Effect compresses with broader distribution but never inverts** across
three nested training sets. That's Figure 1 of Paper 1, available now.

---

## Data NOT in journals

Worth surfacing what isn't here so the methodology section is honest:

- **Per-candidate market context.** VIX level, SPY return, sector
  performance — not stored per row in journals. Backfillable from
  yfinance or Alpaca for paper revisions that want regime stratification
  on these covariates.
- **Realized fills.** All journals are paper-trading; no real fill
  prices, slippage, or partial-fill data exist. Paper 1 must caveat
  results as "predictive validity, not execution validity." Honest.
- **Cascade-rejected candidates.** Journals only contain evaluations
  that reached the agent layer. Candidates rejected upstream by the
  scanner (~67% of universe per Selection Arena) are absent. This is
  the same survivorship bias problem v2's phantom-IPW work addresses,
  and it's worth flagging in Paper 1's limitations section.

---

## Paper 1 timeline (revised from Saturday's estimate)

Saturday's deep dive said: *"Paper 1 is real and writeable in a month
on existing data."* This audit confirms the data is here. Concrete
6-week path to submission:

| Week | Deliverable |
|---|---|
| Week 1 (this week) | Section 3 analysis: consensus_strength × realized_return Spearman correlations with bootstrap CIs, regime-stratified |
| Week 2 | Section 4 mechanism analysis: TF-IDF narrative-density, semantic similarity to pump-language, agreement-as-killer correlations |
| Week 3 | Section 5: three-distribution effect-size table with CIs (the v0/v99/v1 figure) |
| Week 4 | Draft introduction, methods, related work |
| Week 5 | Internal review, edit, re-run any analyses that surface gaps |
| Week 6 | Submit to NeurIPS Workshop on AI in Finance OR arxiv as preprint + Algorithmic Finance |

Paper 1 runs in parallel to v2 experiments. Pierce-the-bottleneck splits
time: experiments weekdays (1A/1B Monday, distillation-prep through the
week), Paper 1 evenings/weekends. The empirical anchor is the
publishable contribution; the v2 architecture is the deployable
contribution. They don't compete for the same machinery.

---

## Re-runnable check

```bash
python -c "
import json
from pathlib import Path
journals = sorted(Path('data/journals').glob('journal_*.jsonl'))
total = sum(1 for j in journals for _ in open(j) if _.strip())
print(f'{len(journals)} files, {total} rows')
"
```

Expect: at least 18 files, at least 2,400 rows. Re-run after any
session to confirm new sessions add rows (the journal pipeline is
producing data).
