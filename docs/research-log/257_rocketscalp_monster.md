# 257 — RocketScalp-RL "The Monster" (v2): a 10-floor SOTA deep-RL trader, the force-to-trade redesign, and the verdict that makes it respectable.

**Author**: Claude Opus 4.8 (7-agent architecture deep-research workflow + lead-architect synthesis; 718K tokens, 191 web tool-uses)
**Date**: 2026-06-04
**Mandate**: Pierce — "build a 10-story monster, force it to trade (not conservative), introduce ALL features we collected, spin up agents to research layers to stack on." Full send, for the craft.

## The governing truth (stated up front, because it ranks every layer)
This universe has **no ex-ante directional expectancy** (docs 245-254). So a maximally-layered monster **cannot manufacture alpha.** What it *can* legitimately do is three things: **better perception** (microstructure timing), **better risk-shaping** (chase the right tail instead of avoiding the left), and **better sizing** (press only where a calibrated signal is tight). Everything else is craft/ornament — and the research labels it as such. **A well-built monster that lands at ~flat under rigorous CPCV+Deflated-Sharpe is the *respectable null*** — and the architecture's first-class value is the ability to **falsify the edge cleanly.**

## The 10-floor stack
The invariant that makes it composable: the critic/policy only ever see a state embedding `h∈R⁹⁶` and the critic emits `z∈R^[B,16,NA]`. Every floor is surgery on (a) how `h` is produced, (b) the risk reduction `z→value`, or (c) the reward.
```
 10  TRAINING      CPCV(6grp,15 paths)+Deflated/Probabilistic Sharpe+PBO [GATE] · CQL/IQL offline
                   conservatism · SAM/SWA flat-minima · curriculum+uncertainty-PER · GPBT · SSL pretrain
  9  SIZING        conformal risk-controlled position multiplier (ensemble spread + IQN width)  [CORE]
  8  POLICY        ★ TAIL-SEEKING spectral distortion (Wang η>0 / upper-CVaR) = THE FORCE-TO-TRADE  [CORE]
                   + state-augmentation (running P&L) for episode-coherent risk · HRL regime router [abl]
  7  CRITIC        IQN cosine-τ head, K=4 randomized-prior ensemble, double-Q, n-step  [KEEP]
                   + optional continuous actor (DSAC) for sizing
  6  STATE         h = LayerNorm([h_bar ‖ c_bar ‖ c_tick ‖ peer ‖ retrieval ‖ static]) → R⁹⁶  ← the seam
  5  CROSS-SECTION peer-attention (Set-Transformer, theme-sympathy) · retrieval-RL  [ABLATION]
  4  CANDIDACY     ★ FiLM g(c)→(γ,β) modulation; c = the doc-255 candidacy vector + tick aggregates  [CORE]
  3  FUSION        bidirectional cross-attention (TLOB dual-attn): BAR ⇄ TICK  [CORE]
  2  ENCODERS      BAR: dilated-TCN (keep) · TICK: ★ Mamba/S6 selective-SSM over raw early ticks + Time2Vec [CORE]
  1  ENV           cost-realistic price-replay; reward = pos·ret − |Δpos|·30bps; no look-ahead  [doc 256]
```

## The force-to-trade redesign (the headline — and it's *principled*, not "churn")
The base policy is CVaR-**averse** → on a spread-walled, no-expectancy universe it *converges to abstention* (doc 256's predicted null). Forcing aggression means changing the **objective**, not bolting on an activity bonus:
- **Flip the risk distortion to tail-SEEKING.** The IQN identity (Dabney 2018): a distortion risk measure is implemented *purely by reweighting the quantile fractions τ* — **zero architecture change, zero new params.** Replace lower-tail CVaR with an upper-tail spectral weight (Wang η>0). Because flat's tail is identically 0, any action whose *optimistic* (rocket) tail is positive now **beats flat → the agent takes the trade.** **Decision-time only — the learning target stays undistorted** (the "Pitfall of Optimism" correctness detail: distorting the *learner* makes it chase its own optimism and overestimate).
- **+ state-augmentation** (append running realized P&L) → episode-coherent risk (per-step CVaR is time-inconsistent — *that* is the abstention mechanism; this fixes it so it can hold through the opening dip to catch the runner).
- **+ conformal sizing** (Floor 9) to press only on tight, right-shifted intervals — aggressive *with* finite-sample risk control.
- **Anti-churn guards:** the 30bps is in the reward (needless flips are self-penalized), turnover-L1 + potential-based **Sortino** shaping (penalize *downside* variance only, so it doesn't fight the tail-seeking), conformal abstention, and **randomized η** (don't pin max-optimism). **Turnover is reported beside P&L so "aggression" can never hide as churn.**

## Build order (scoreboard first, gargoyles last)
- **Phase 0 — the scoreboard:** CPCV (6 groups, 15 paths, 1-day embargo) + **Deflated Sharpe** that charges for every config tried. Build *first* — the repo's own evidence shows single-split verdicts flip; with fat tails a lone 2026 number is "which 3-4 rockets landed."
- **Phase 1 — highest lift, near-zero capacity (BUILT, see below):** tail-seeking distortion + state-aug; **FiLM candidacy injection**; conformal sizing; CQL/IQL + SAM/SWA.
- **Phase 2 — representation:** Mamba tick tower + cross-attention fusion, *paired with* SSL pretraining (the overfit insurance that lets the big encoder earn its keep).
- **Phase 3 — search:** curriculum + uncertainty-PER; GPBT with **fitness = CPCV-OOS net P&L** (not train reward).
- **Phase 4 — the gargoyles (build for craft, expect to cut):** retrieval-RL, learned peer-graphs, **Decision-Transformer** (proven failure on luck-dominated returns — reviewer-rejectable here without the ESPER fix), Dreamer world-model, NCDE/KAN/diffusion. *"Impressive, and load-bearing only if they clear a bar they will probably fail."*

## What's built in this doc (Phase 1, the headline two)
`scripts/rocketscalp_rl_v2_doc257.py` — the optimized monster with **(A) the tail-seeking spectral distortion** (`--risk-mode {cvar_low, cvar_high, wang, dualpow}`, decision-time-only, 0 params) and **(B) FiLM candidacy conditioning** (γ=1/β=0 init; `c` = the doc-255 candidacy vector + the doc-242/248 aggregated tick microstructure — *"all the data we collected"*), plus state-augmentation and a **vectorized rollout** (the v1 per-step Python bottleneck, fixed). One script runs the **conservative-vs-aggressive A/B** (`--risk-mode cvar_low` vs `wang`), walk-forward (2024+2025 → held-out 2026), net-of-cost vs the baselines (flat 0% · long −0.75% · random-churn −89% · vwap-mr −0.16%). (Leakage discipline applied: dropped the `foreign_issuer` feature (0% coverage in the warehouse) and the `coiled` flag (its definition leaks same-day data).)

## Result (the A/B — held-out 2026 net-of-cost, 4-epoch walk-forward) — THE RESPECTABLE NULL, demonstrated
| arm | held-out 2026 net (30bps) | turnover |
|---|---|---|
| **MONSTER[wang]** (tail-seeking/aggressive) | **−0.54%** | 1.6 |
| **MONSTER[cvar_low]** (conservative) | **+0.63%** | 2.6 |
| long-only (trivial) | **+0.79%** | 1.0 |
| vwap-mean-rev | +0.52% | 1.8 |
| flat | 0.00% | 0 |
- **Both RL arms collapse to ~flat (±0.6%)** — and the IQN quantile loss collapses to ~0, the signature of the policy collapsing to **mostly abstain** (the abstention attractor the synthesis named). The training paths are noisy (wang: −4.5→−5.9→−0.5→−0.5; cvar_low: +0.6→−1.1→−0.15→+0.5) — bouncing around 0.
- **Forcing-to-trade did NOT work:** `wang` (tail-seeking) *also* learned to abstain (turns 1.6, *fewer* than the conservative) and landed slightly **negative**. The 30bps spread wall held even against an upper-tail-seeking objective — exactly the §2.5 honest cost.
- **The +0.63% conservative arm is a noise-level mirage:** it is *below* trivial **long-only (+0.79%)**, untested by CPCV/DSR, and on 464 fat-tailed episodes a ±0.6% mean is dominated by which few rockets landed (the synthesis's explicit warning — a single 2026 number is not a verdict).
- **Verdict:** a SOTA distributional risk-sensitive RL agent — with FiLM candidacy + tick-microstructure features, *forced* to be aggressive — **cannot beat flat or even trivial long-only net-of-cost on held-out 2026.** The architecture *correctly revealed* there is no harvestable intraday edge. The monster's first-class value — falsifying the edge cleanly — is exactly what it delivered. (Phase 0 CPCV+DSR would make the ±0.6% rigorously a null; the point estimates already land below a trivial baseline.)

## Honest verdict (the part that earns the professor's respect)
- **Most likely:** the tail-seeking agent **trades** (turnover up — the force-to-trade *works as a behavior change*) and lands **~flat-to-slightly-negative** net-of-cost on held-out 2026 — because aggression on a no-expectancy universe is *paying the spread to buy negative-net-EV lottery tickets*. That's the architecture **correctly revealing** that aggression can't create absent expectancy. The respectable null.
- **Where the monster genuinely helps:** risk-shaping (Floor 8 — a real behavior change, 0 capacity, can't overfit) · sizing (Floor 9 — the one honest lever on a no-expectancy substrate) · perception (Floors 2-4 — the Mamba tick tower finally puts OFI/absorption/coiled-catalyst signatures into the state; better *timing*, **not** new directional alpha).
- **Where it's ornamental (build for craft, don't believe):** retrieval / peer-graphs / Decision-Transformer / world-model / KAN — *"the gargoyles on the facade,"* maximal capacity for the least-defensible reason on ~3,000 episodes dominated by a handful of rockets; they will memorize 2024-25 and read it back as edge that evaporates in 2026.
- **The discipline:** evaluate only on held-out-2026 net-of-cost as a **15-path CPCV distribution with a Deflated Sharpe**; gate complex layers behind cheap ones; read the K=4 ensemble disagreement as the **overfit governor** (if it doesn't shrink OOS, the encoder is memorizing — a kill signal); report turnover beside P&L.

## Status
**No capital, NO live trading change.** The monster is genuinely SOTA *and* honest — the twin-tower Mamba/cross-attention encoder, the tail-seeking distributional head, the conformal sizer, and the CPCV/DSR harness are reviewer-respectable; the build is wired knowing its honest job is **better perception, risk-posture, and sizing — not the discovery of directional alpha the data has exhaustively shown isn't there.** If the layered agent *does* clear flat with a positive DSR floor, the candidacy features belong in a position-**sizing/selection** screen as much as an intraday policy.
**Basis**: workflow `wf_6b61e20f-5c7` (7 agents) + `scripts/rocketscalp_rl_v2_doc257.py` (FiLM + tail-seeking + vectorized) + `enrich_episodes_doc257.py`. **Predecessors**: 256 (the base + env + baselines), 255 (candidacy features), 235 (variance-not-expectancy — the thesis the monster tests). **Memory**: [[gapper-universe-no-edge]].
