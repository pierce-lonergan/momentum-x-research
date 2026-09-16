# 267 — New Together serverless models benched, debate infrastructure assessed, final sweep + the dead-defaults fix

**Author**: Claude (Fable 5 era)
**Date**: 2026-06-09 (wrap)
**Mandate**: Pierce — bench the new Together serverless models vs ours, assess the debate infrastructure, final bug sweep, call it a day.

## 1. Model bench (`scripts/model_bench_doc267.py`) — drop-in compatibility test
3× tier-2-style bare-JSON extraction (signal/confidence/catalyst on an FDA+offering trap) + 1 judgment case (the doc-253 ATM-dilution red-flag trap). Bare-JSON prompting = exactly how prod's agents call models, so this measures **drop-in** fitness (a model that needs json_mode/system-prompt coaxing isn't a drop-in).
| model | price in/out | p50 | JSON | judge |
|---|---|---|---|---|
| **deepseek-ai/DeepSeek-V4-Pro** | $1.74/$3.48 | **0.8s** | **3/3** | **PASS** |
| zai-org/GLM-5.1 | $1.40/$4.40 | 3.1s | 0/3 | miss |
| nvidia/nemotron-3-ultra-550b | $0.60/$3.60 | 0.7s | 0/3 | miss |
| moonshotai/Kimi-K2.6 | $1.20/$4.50 | 1.3s | 0/3 | miss |
| MiniMaxAI/MiniMax-M2.7 | $0.30/$1.20 | 6.3s | 0/3 | miss |
| Qwen/Qwen3.7-Max | $1.25/$3.75 | 400 model-not-available (ID/serverless) | — | — |
| *(live T2)* Qwen3-235B-2507-tput | — | **0.7s** | 3/3 | PASS |
| *(emergency)* Llama-3.3-70B | $0.88 | 1.5s | 3/3 | PASS |
| *(t1 fallback)* Qwen2.5-7B | $0.30 | 0.4s | 3/3 | PASS |
**Verdict: keep the current primaries.** Only **DeepSeek-V4-Pro** is production-grade drop-in among the six — and it's slower-or-equal and pricier than the live Tier2 (0.7s, 3/3, PASS). The K2.6/GLM/Nemotron failures are format non-compliance on bare-JSON (the same reason D92 dropped Kimi-thinking); they may comply with json_mode, but that's a migration, not a swap. **V4-Pro is the bench's noted candidate if a Tier-1 reasoning gap ever appears** (e.g., a future judge role); no gap is currently evidenced — Tier1 Qwen3.5-397B probed healthy (1.0s).

## 2. Live-model audit + the REAL bug found and FIXED (dead defaults)
Prod's env pins are current and healthy (6/8 startup log: Tier1=Qwen3.5-397B, Tier2=Qwen3-235B-2507-tput). **But the settings.py DEFAULTS were dead**: tier1/tier3 defaulted to `DeepSeek-V3.1` and tier2 to `Qwen3-Coder-480B` — **both no longer Together-serverless (verified 400 tonight)** — and worst, the **tier2 FALLBACK was also V3.1**: a broken middle link (primary fails → fallback 400s instantly → emergency). If the env ever fails to load (the exact D93 incident class), the bot would boot onto models that cannot respond. **Fixed**: tier1/tier3 → Qwen3.5-397B, tier2 → Qwen3-235B-2507-tput (the live pins), tier2-fallback → Llama-3.3-70B (alive, 3/3 bench, dense architecture = diverse from MoE primary). Settings load verified; 241 config-adjacent unit tests pass; the 7 failures present are **pre-existing default-drift tests** (identical with the edit stashed — D104/D105/D122/D207/debate-timeout tests asserting stale defaults; same disease as the pipeline-guard MagicMock chip).

## 3. Debate infrastructure assessment
**State**: intentionally OFF — `max_debate_attempts` default **0** ("D100: Debate engine killed. 0% conversion rate over 7 debates, 20-45s latency each. Speed of execution > quality of deliberation for momentum."). The "0/0 attempts" logs are by design.
**Code health**: good — judge model is the CURRENT Tier1 (`debate_engine.py:89` = Qwen3.5-397B, not stale); the D94 cascade bugs (confidence-default guard, HOLD→NO_TRADE) were fixed; thresholds tuned (MFCS gate 0.35, divergence 0.08/0.6 pinned).
**Verdict: keep it OFF, and not because the code is bad — because the JOB is impossible.** Debate is a *selection-quality* layer, and docs 245-254 proved selection has no extractable signal in this universe (the backtesting insight: debate rejected 10/12 profitable candidates; its gatekeeping was anti-selective, like MFCS AUC 0.548). A smarter judge (V4-Pro included) cannot mine signal that isn't there. The only future role worth considering is a *specific adjudication task* with a measurable target (e.g., arbitrating red-flag-vs-momentum conflicts on the watchlist, scored by the nightly grader) — if that role ever materializes, bench V4-Pro as judge then. Until then: dormant engine, current models, zero maintenance burden. No change.

## 4. Final sweep (all green, one fix, one chip)
- **Compile**: all 10 files touched this arc (main.py D163 fix, grader, forensics, replay, arm-truth, watchlist engine, lottery/fader isolation, t2 knob, bench) — OK.
- **Tests**: 34/34 close-path (bridge/wiring/invariant), t2-arm test, 241 config-adjacent — pass. Pre-existing failures: 10 pipeline-guard (MagicMock chip, filed) + 7 default-drift (same class; test expectations need updating to current defaults — follow-up chip).
- **Tonight's automation verified**: MomentumX-ShadowGrader ran 19:30 exit-0 (the doc-263 ship is alive in production).
- **Settings defaults fix** (§2) — the one real bug this sweep found, repaired, test-verified.
- **Still pending Pierce's hand** (doc 266): the three flip lines — `MOMENTUM_T2_WIDE_PCT=0.75` (User env) + `EXEC_MAX_POSITIONS=16` + `EXEC_MAX_POSITION_PCT=0.05` (secrets + .env). Agent remains permission-blocked at live config, by design.
- **Tomorrow's #1 (unchanged)**: trace the silent execution-blocker that ate 14 BUY verdicts (7 rockets) on 6/8 with no order IDs and no log line — and make it log loudly.

**Basis**: model_bench_doc267.py (live API), momentum_2026-06-08.log ENV_AUDIT/D95 lines, settings.py field audit, stash-compare test attribution. **Predecessors**: 266 (flip lines pending), 92/150/190 (model history), 100 (debate kill).
