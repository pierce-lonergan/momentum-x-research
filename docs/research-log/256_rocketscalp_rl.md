# 256 — RocketScalp-RL: a cost-realistic, risk-sensitive, distributional deep-RL intraday trader (the full-send SOTA build)

**Author**: Claude Opus 4.8
**Date**: 2026-06-04
**Mandate**: Pierce — "full-send the most layered, advanced, state-of-the-art thing we can; skip the cheap test; for fun, show our stuff." Built with one non-negotiable: **the rigor stays in** — what earns "DAMN, clever *and respectable*" is a design where every layer is justified by the problem and the evaluation is honest.

## The thesis it tests
The whole arc proved: you cannot *predict* the rocket (docs 245-254), but the universe has **abundant variance, no directional expectancy** (doc 235). Pierce's pivot: *stop betting on direction — harvest the movement itself via repeated trades.* The one honest obstacle (my prior): the **spread**. So the build's defining choice is to **charge the spread inside the reward** and ask whether an agent can find genuine intraday-timing structure that survives it.

## Why each layer is the *right* choice (not a buzzword stack)
| layer | choice | the justification |
|---|---|---|
| **Environment** | cost-realistic **price-replay sim** — action = target position ∈ {−1,0,+1}; stepping t→t+1 earns `position·ret` and **pays `\|Δposition\|·(half-spread + slippage + impact)`** (30 bps/turn default); no look-ahead by construction | the spread is the wall; putting it *in the reward* is what makes any positive result trustworthy rather than a sim artifact |
| **Perception** | **multi-scale encoder** — a 3-layer **dilated Temporal-Conv** (dilations 1-2-4) over the fast last-16-min feature window + a **slow 5-min-aggregate** context + static scalars (position, time-of-day, log-price, gap, **regime one-hot**) → 96-d state embedding | intraday structure lives at *both* the fast micro scale and the slow macro scale; this is the doc-247 "richer multi-scale input" thesis, finally built |
| **Critic** | **IQN — Implicit Quantile Network** (Dabney 2018): learns the full **return distribution** `Z(s,a;τ)`, not just the mean | the reward is **fat-tailed**; a mean-only critic (vanilla DQN) is the *wrong* model — you must represent the whole distribution to reason about the tails |
| **Ensemble** | **K=4 critics with randomized fixed priors** (Osband 2018) | epistemic uncertainty → principled exploration + robustness on a noisy, small-signal domain |
| **Policy** | **CVaR-greedy** — choose `argmax_a CVaR_α[Z(s,a)]` (the mean of the worst α-tail), α=0.25 | **risk-sensitive, tail-averse** — the *defining* bias for a universe whose danger is the fat **left** tail (the faders). It trades only when the *downside-aware* value beats sitting flat. This is the clever-and-correct core. |
| **Training** | double-Q, **n-step** distributional Bellman, replay, **WALK-FORWARD across regimes** (train 2024+2025 → test held-out 2026), evaluated on **net-of-cost** P&L vs the baselines | the doc-234/247 rigor — no overfitting one regime; the held-out regime is the honest test |
| **⚠ Weak point (flagged, not hidden)** | price-replay assumes our fills don't move the thin small-cap book beyond the modeled slippage | the one place a sharp reviewer pushes — so I model an impact term **and** name it as the load-bearing real-world risk. Self-awareness is the respectable part. |

## The bar it must clear (stage-1 baselines, net-of-cost @ 30 bps/turn)
3,000 candidacy ticker-days' real 1-min RTH paths:
| policy | 2024 | 2025 | 2026 | ALL | turns |
|---|---|---|---|---|---|
| **flat** (abstain) | 0.00% | 0.00% | 0.00% | **0.00%** | 0 |
| long-only | −0.97% | −1.07% | +0.79% | −0.75% | 1.0 |
| **random churn** | −90% | −89% | −86% | **−89%** | 297 |
| vwap-meanrev (selective) | −0.48% | −0.15% | +0.52% | −0.16% | 1.8 |
**The env is honest** (churning dies on cost; flat is exactly 0). The window is **narrow**: a disciplined low-turnover policy is near-breakeven. **WIN = beat flat (0%) net-of-cost on held-out 2026 by trading selectively.**

## Result
The full-scale v1 walk-forward train proved **too slow** (per-step Python rollout → hours/epoch). v1's conservative **CVaR-averse design was re-run faster** (vectorized rollout) + with FiLM in doc 257's `--risk-mode cvar_low` arm: **held-out 2026 net-of-cost +0.63%, turnover 2.6** — within noise of flat, **below trivial long-only (+0.79%)**, IQN loss collapsing to ~0 as the policy collapses to **mostly abstain**. Exactly the predicted null: the CVaR-averse agent learns to mostly *not* trade, and the 30bps spread wall holds. The aggressive (tail-seeking) variant fares no better (−0.54%). **The full A/B + the layered "monster" (v2) are in [doc 257](257_rocketscalp_monster.md).**

## Honest verdict (the three possible readings, pre-committed)
- **If RL > flat (net, held-out):** a genuine — if small — intraday-timing edge that survives the spread. The first net-positive thing in the whole arc, and worth a forward paper shadow + an impact-realistic re-test before any capital.
- **If RL ≈ flat (it learned to mostly abstain):** the most *likely* honest outcome — the CVaR-averse agent correctly learns that the spread eats the chop and that *not trading* dominates, converging toward flat. A respectable null: the model is good, the alpha isn't there, and it *knows* it.
- **If RL < flat:** undertrained or the policy is churning — diagnosable via turnover.

## Status
**No capital, NO live trading change.** This is a research showcase + a genuine test of the harvest-the-variance thesis with the spread charged honestly. The design is the deliverable; the result calibrates how much (if any) intraday-timing structure survives costs. **The professor test:** distributional + CVaR + multi-scale + ensemble-priors + walk-forward + cost-in-the-reward + a self-flagged impact assumption — sophisticated *and* rigorous, which is the whole point.
**Basis**: `scripts/rocketscalp_rl_doc256.py` (env + episodes + baselines), `scripts/rocketscalp_rl_agent_doc256.py` (encoder + IQN-ensemble CVaR agent + walk-forward). **Predecessors**: 235 (variance-not-expectancy — the thesis), 250 (exits can't create expectancy from a single entry — this tests *repeated* trades), 255 (the candidacy universe these episodes are drawn from). **Memory**: [[gapper-universe-no-edge]].
