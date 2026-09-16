# 182 — The Rejection-Outcome Shadow (keystone): grade the gates before loosening them

**Author**: Claude Opus 4.8
**Date**: 2026-05-29 (Friday, ~11:20 ET, LIVE)
**Mandate**: Pierce greenlit all 4 next-gen fixes (wire rejection-grading / fix
D170 / marketable limits / Continuer shadow). This ships #1 — the keystone that
makes the other 3 SAFE — and stages the rest.

---

## 0. Why this is first (and why it gates the other 3)

Doc 181 proved today's gates (faller D160, entry-delay D170) **correctly avoided
fades** — CGTL −14%, UMAC −13.8%, CODX −11.3%. The dangerous corollary: **if we
loosen the entry machinery (D170, marketable limits) WITHOUT a continuation edge,
the bot buys exactly those fades.** So before changing entry behavior we must
answer, on data, "when the gates block a name, are they right?" Today that's a
manual price-check (doc 180 §6.2). This module makes it automatic and continuous.

**Sequencing principle (do not violate):** instrument → calibrate → only then
loosen. The two entry-loosening fixes (D170, marketable limits) stay flag-gated OFF
until this shadow shows the gates are wrong often enough to justify it.

---

## 1. What shipped (keystone — SHADOW, write-only, safe to deploy)

`src/shadow/rejection_outcome_shadow.py` — `log_rejection_for_grading(...)`: at each
gate rejection, logs decision-time context {ticker, gate, decision_price,
decision_ts_utc, mfcs, score, reason, gap, rvol, outcome=null}. Write-only, never
raises, env kill-switch `SHADOW_REJECTION_GRADING_ENABLED` (default true).

**Wired at the two binding gates:**
- **D160 faller block** (`main.py`, after `D160_FALLER_REJECT`): logs gate
  `D160_FALLER` with the faller score + MFCS + candidate price/gap/rvol.
- **D170 entry-delay reject** (`entry_delay.py`, at the REJECTED log): logs gate
  `D170_ENTRY_DELAY` with the rejection reason + price.
Both guarded by try/except — zero risk to the trade loop.

`scripts/finalize_rejection_outcomes.py` — post-close batch: reads the day's
`shadow_<date>.jsonl`, fetches each rejected name's **forward** 1-min bars (strictly
after decision_ts → EOD), computes return at +15m/+60m/EOD + MFE/MAE, and grades:
- `block_CORRECT_faded` (EOD ≤ −5%) — the gate was right,
- `block_WRONG_ran` (MFE ≥ +10% and EOD ≥ 0) — the gate cost us a runner,
- `block_neutral`.
Prints a **per-gate scorecard**: count, %correct, %wrong, mean EOD return, and
names every MISSED RUNNER. That scorecard is the calibration signal for the faller
0.60 threshold and the D170 10%-drawdown limit — and the go/no-go for loosening.

**Verification**: 4 files compile; module unit-tested (4/4 pass — logs well-formed,
never raises, respects the kill-switch, swallows logger failures); all 19 D170
tests still pass (wiring is non-intrusive). No lookahead: `outcome` is null at log
time; only the post-close batch fills it from strictly-later bars.

**Deploys on the next restart** (it's in the running-process gap with doc 179/180).
Until then it logs nothing live, but it's safe and ready.

---

## 2. Operational: the restart (needs Pierce)

The live bot (PID 32120) runs as the Task Scheduler's elevated user; my session got
`Access is denied` trying to stop it, and the launcher won't relaunch while the old
PID holds the lock. **The restart needs an elevated shell.** Recommended:
```
# elevated PowerShell
Stop-ScheduledTask -TaskName 'MomentumX-PaperTrading'
Get-Process python | Where-Object {$_.Id -eq 32120} | Stop-Process -Force   # if still alive
Start-ScheduledTask  -TaskName 'MomentumX-PaperTrading'
# verify: the new momentum_<date>.log D217 STARTUP line shows commit != b33b853
```
One restart deploys EVERYTHING on disk: doc 179 (M2 real-time data) + doc 180 (D249
re-arm fix) + doc 182 (this shadow). It also triggers a fresh APPS overnight-close
*with* the D249 fix (may finally flatten the +24% accidental hold — fine, it's paper).

---

## 3. The other 3 fixes — staged, with the safety gate

Built/queued but **deliberately not deployed-active** until §1's scorecard returns:

| Fix | Status | Why staged |
|---|---|---|
| **D170 redesign** (distinguish healthy-pullback from fade; drop the impossible ≥4-bullish-agents early-entry rule that even MFCS 0.856 failed) | designed | It's the dominant 0-fills cause AND has a clear bug (the ≥4-agent rule is unsatisfiable under the blind funnel). But loosening it lets the bot buy fades — gate on the §1 scorecard + flag (default OFF). |
| **Marketable limits** on the WIDE_STOP entry (so thin-name orders fill; CMND was 0/2) | designed | Same: filling more orders on pumps = losses without the edge. Flag-gated OFF; enable once selection is trusted. |
| **Continuer_v2 shadow on momentum picks** (the squeeze-vs-pump edge, currently wired only to the lottery) | designed | The real Phase-C unlock. Write-only shadow (safe), but a larger build (model load + feature plumbing). Pairs with §1: A/B the ML continuer vs the heuristic faller gate on the same names. |

**The plan**: deploy §1 (+179/180) on restart → collect ~1-2 weeks of graded
rejections + Continuer shadow scores → if the scorecard shows the gates block
genuine runners (and the Continuer predicts them), THEN flip the D170/limits flags
to capture them. This is how we bridge to 5% without first bleeding on the fades
the gates are currently (correctly) avoiding.

## Appendix — files
- `src/shadow/rejection_outcome_shadow.py` (new), `scripts/finalize_rejection_outcomes.py` (new)
- `main.py` (faller-block wiring), `src/execution/entry_delay.py` (D170 wiring)
- `tests/unit/test_rejection_outcome_shadow.py` (new, 4 tests)
- This doc + `docs/SYSTEM_MAP/changelog.md`
