# 95 — Dynamic Systems Brainstorm: Twelve Wild Architectures

**Date:** 2026-05-02
**Mood:** the drop-of-water-on-cracked-rock metaphor. Substrate, substance,
strategy all evolve together. Nothing static.
**Constraint dropped:** feasibility. We're collecting madness on the table.
Genius reads as madness until the community catches up.

---

## §0 — Framing: what a "dynamic" system has that a static one doesn't

A static system runs the same pipeline every day, scores with frozen
weights, gates on hard thresholds, exits on fixed targets. Tunable but
brittle. When the market regime shifts, the system either keeps trading
into a wall or has to be hand-redeployed.

A dynamic system has at least four properties:

1. **State that persists across episodes.** Memory of past outcomes alters
   the next decision without code changes.
2. **Topology that grows or prunes.** New nodes/connections form when
   stimulus arrives; unused ones decay.
3. **Reactive timing.** It speeds up when the market is interesting and
   sleeps when it isn't, instead of running on a fixed cron.
4. **Online learning.** Every fill is a training signal that updates
   weights/probabilities/structure within seconds, not weekly retrains.

The arena infrastructure we have (replay engine, signal corpus, decision
audit) is the perfect substrate to test these — every idea below sketches
how it slots in.

---

## §1 — The four seeds you planted

### IDEA 1 — Quantum tunneling: probabilistic gate traversal

**Core insight.** Classical physics: ball rolling at energy E < V cannot
cross a barrier of height V. Quantum: there's a finite probability the ball
appears on the OTHER side without ever having E ≥ V. Tunneling probability
falls exponentially with barrier width.

**MOMENTUM-X application.** Right now our gates are binary: `mcap < $1B` →
PASS, `mcap > $1B` → FAIL. A ticker at $1.05B is rejected exactly as hard
as one at $50B. But there's a continuous probability surface: barely-
failing tickers OCCASIONALLY merit trading, and our hard gates throw away
the tail.

**Tunneling rule.** Replace each gate with `P_pass = exp(-((value - threshold) / kT)²)`
where `kT` is a "temperature" that controls how forgiving we are. At
`kT = 0`, classical hard gate. At `kT = thresh × 0.2`, ~20% pass-through
on borderline candidates.

The ticker still BUYS (or doesn't) deterministically — we sample once per
candidate per session. Position size is proportional to P_pass.

**Arena test.** Replay every historical signal against (a) hard gates and
(b) tunneling gates with `kT ∈ {0, 0.05, 0.10, 0.20, 0.50}`. Compare
P&L distribution. The hypothesis: a small `kT` (5-10%) increases capture
of edge cases without much added noise. Larger `kT` decays into pure
randomness.

**What "working" looks like:** the average P&L across replay strictly
improves at some kT > 0, AND the variance doesn't blow up. Inverted-U
sweet spot is the signal.

### IDEA 2 — Mycelial networks: distributed signal processing

**Core insight.** Fungal mycelia have no central nervous system. Each hypha
relays nutrients/signals to neighbors via electrical and chemical gradients.
The whole organism "computes" routes to food without a brain.

**MOMENTUM-X application.** Our pipeline today is linear: scanner → filter
→ score → execute. One signal at a time, one path. A mycelial architecture
would have NODES (one per signal source: gap%, RVOL, news, options-IV,
short-interest, sector-cohort) that exchange "nutrient" messages on the
fly. When a signal node detects unusual activity, it broadcasts and other
nodes amplify or inhibit. The decision EMERGES from the network state, not
from a single forward pass.

**Concrete sketch:**
```
node_gap        ← gap_pct of ticker T
node_rvol       ← (current_vol / avg_vol_at_time)
node_news       ← max sentiment over last 4h articles
node_short_int  ← short_interest / float
node_sector     ← mean (gap%) across same-sector tickers TODAY
node_options    ← IV change vs prior day

each node:
  1. computes its own activation a_i ∈ [-1, +1]
  2. sends to all neighbors: signal = w_ij × a_i
  3. receives sum from neighbors, updates: a_i' = tanh(a_i + sum)
  4. iterate 5 rounds — let activations settle
  5. final decision = sigmoid(sum of all a_i × output_weights)

weights w_ij START random, UPDATE via Hebbian rule:
  Δw_ij = η × (a_i × a_j) × (P&L_outcome > 0)
  i.e., nodes that fired together on profitable trades strengthen their link
```

**Arena test.** Build the 6-node network. Run 88-day arena replay. Measure
(a) accuracy vs current linear pipeline, (b) how the weight matrix
self-organizes — do gap+rvol amplify each other? Does news dampen sector?

**What "working" looks like:** the weight matrix stabilizes into an
interpretable structure (we see "gap" + "rvol" with high mutual weight
when both fire correctly) AND aggregate P&L beats the linear baseline.

### IDEA 3 — Wave-based learning: recurrent refinement

**Core insight.** A transformer outputs the answer in one forward pass
and commits. A recurrent network re-processes its own output, refining
each pass. Diffusion models go further: start from noise, denoise step
by step, each step sharper.

**MOMENTUM-X application.** Today's scoring agents (CatalystNews,
Technical, Risk, etc.) each commit to a verdict in one shot. What if they
each iterated 3-5 times, with later passes seeing the earlier-pass
verdicts of OTHER agents? Convergence = consensus; divergence after N
passes = "this signal is genuinely ambiguous, scale down."

**Concrete sketch:**
```
pass_0: each of 5 agents independently scores ticker T
pass_1: each agent re-scores, given the OTHER 4 agents' pass_0 scores in context
pass_2: re-score given pass_1 of others
... up to pass_K (K=4)

if pass_K spread (max - min) ≤ ε: confident, full position
if pass_K spread > 2ε: scale to 1/2 position
if still diverging at pass_K: skip
```

This is a wave because information ripples through the agent population
multiple times before output. Each agent's view is informed by everyone
else's view of itself in the previous pass.

**Arena test.** Pick 100 historical decisions. Re-run them with K=1
(current behavior) vs K=5 (wave). Measure (a) score volatility per
ticker, (b) realized P&L correlation with score-confidence band.

**What "working" looks like:** the wave's "high confidence" calls
outperform their "low confidence" calls by a clean margin, AND the
classification is BETTER than the single-pass equivalent (more signal in
the residual).

### IDEA 4 — Diffusion-style decisions: denoise into orders

**Core insight.** Image diffusion: T=1000 random noise → T=999 slightly
less random → ... → T=0 a coherent image. Each step is a small denoising.
The model never "decides" to draw an image; it iteratively reduces
uncertainty.

**MOMENTUM-X application.** Instead of "predict the next action" (buy at
$X, sell at $Y), the model produces a NOISY exit plan and refines it
across 20 inference steps. Each step shows the plan getting more confident
about target, stop, sizing.

**Concrete sketch:**
```
T=20: exit_plan = {target_pct: random uniform [0, 1], stop_pct: random uniform [-1, 0],
                    size_mult: random [0, 2], hold_hours: random [0.1, 7]}

at each step T → T-1:
  - input: current exit_plan + market state vector (price path so far,
            RVOL, time, similar past trades)
  - output: small refinement δ to each plan parameter
  - apply δ (with decreasing noise — schedule like image diffusion)

T=0: exit_plan is the model's converged answer.
```

The MODEL can be tiny (an MLP). What matters is the iterative refinement:
each step "sees" what step before chose and adjusts. The market state
acts as the conditioning signal.

**Arena test.** Train on 88-day arena outcomes (input: state at entry,
target: actual best exit derived from MFE). At inference, run 20-step
denoise. Compare to a one-shot regression model trained on the same data.

**What "working" looks like:** the diffusion model's exit plans
hierarchically capture uncertainty (noisy at T=20, confident at T=0), AND
the converged plan beats a one-shot baseline on holdout.

---

## §2 — Eight more wild ones

### IDEA 5 — Stigmergic agents (ant pheromone trails)

**Core insight.** Ants don't plan paths — they leave pheromone on the
ground. Future ants smell stronger trails and reinforce them; trails to
empty food sources evaporate. Path-finding emerges without a planner.

**MOMENTUM-X application.** Every decision the bot makes leaves a
"pheromone" trail in a DECISION GRAPH: nodes are (ticker, time-of-day,
signal-set), edges are "decision X led to decision Y." Future decisions
prefer paths with strong pheromone (good past outcomes) and avoid
evaporated paths (bad).

**Concrete sketch:**
```
nodes: (signal_signature_hash, decision)
edges: (node_a, node_b) annotated with cumulative_pnl, n_visits
pheromone(edge) = sum(pnl) × exp(-decay × age)

decision logic:
  1. compute current signal signature
  2. find all OUTBOUND edges from this node in graph
  3. weight each by pheromone strength
  4. softmax-sample one (NOT argmax — explore)
  5. record outcome, update pheromone after fill
```

**Evolution.** Old paths evaporate with `exp(-decay × t)`. New profitable
paths get reinforced with each successful trade. The graph LITERALLY
embodies the bot's learned strategy and evolves without retraining.

**Arena test.** Build the graph from 88 days of historical decisions.
Replay last 30 days as test, watching how the path distribution changes.
Measure: does the bot's "preferred path" through signal-space differ on
day 30 vs day 1, in a way that correlates with profitability?

**What "working" looks like:** decision entropy reduces over time
(consolidation onto profitable paths) AND the bot beats a fixed-policy
baseline.

### IDEA 6 — Slime mold pathfinding (Physarum)

**Core insight.** *Physarum polycephalum* — a single-celled organism —
can solve mazes by exploring all paths simultaneously, then reinforcing
the shortest food-bearing path while abandoning others. It famously
recreated the Tokyo subway map within a Petri dish.

**MOMENTUM-X application.** Treat the day's 30-ticker watchlist as nodes
in a graph. Each node has a "food value" = the realized intraday MFE
(observable post-hoc) or a predicted proxy (current). The slime mold
allocates capital across paths (sequences of trades) and reinforces the
ones that bring back the most P&L.

**Concrete sketch:**
- Each day, 30 watchlist tickers are nodes.
- Edges: every pair (i, j) such that we could enter i then later j
  within the session.
- "Mass" flows through edges proportional to (current pheromone × predicted
  food at j).
- After session, edges that delivered P&L thicken; edges that delivered
  losses thin.

The graph topology IS the strategy. Some weeks the mold is "find one
big winner and concentrate." Other weeks it's "diversify across 10
small wins." The substrate adapts.

**Arena test.** Run on 88-day arena. Measure how the topology changes
across the regime shift around 2026-02-25 (we know there was a regime
shift from doc 89). Hypothesis: the slime mold REORGANIZES, in a way
the linear pipeline can't.

**What "working" looks like:** topology metrics (clustering coefficient,
betweenness) shift on regime-change dates, AND P&L holds up across
regimes better than fixed-policy baseline.

### IDEA 7 — Cellular automata (Game of Life on tickers)

**Core insight.** Conway's Game of Life: each cell has state {dead, alive}.
Each tick, cell's next state is a function of its 8 neighbors via 4 rules.
Complex global patterns (gliders, spaceships, oscillators) emerge from
trivial local rules.

**MOMENTUM-X application.** Watchlist tickers form a 2D grid (sorted by
sector × market-cap). Each ticker's "state" is {dormant, building,
ripping, fading}. State transitions depend on (a) ticker's own price
action and (b) neighboring tickers' states.

**Local rules:**
- Dormant + 2-3 ripping neighbors → Building (sympathy ramp-up)
- Building + 1 ripping neighbor → Ripping
- Ripping + 5 neighbors fading → Fading (sector exhaustion)
- Fading + no ripping neighbors → Dormant

**Why it might work.** Real markets show sympathy moves (one biotech rips
→ others follow) and sector exhaustion (whole biotech complex dumps
together). These are LOCAL spatial dynamics, exactly what CA models well.

**Arena test.** Map historical watchlist tickers onto a sector × cap grid.
Compute states at 5-min intervals. Run the CA forward. Measure: do the
emergent "ripping clusters" predict the day's biggest movers earlier than
single-ticker momentum does?

**What "working" looks like:** the CA's "Ripping" state has positive
predictive value for next-30-min price; "Fading" state has negative
predictive value. Beats a per-ticker baseline.

### IDEA 8 — Reservoir computing (echo state network)

**Core insight.** Train a giant random recurrent network ONCE (just
fix the random connections). Only train a small linear readout layer.
Surprisingly: works as well as fully-trained RNNs for many tasks, with
1000× less compute.

**MOMENTUM-X application.** A 500-neuron sparsely-connected recurrent
network whose weights are FIXED RANDOM. At each minute bar, feed
(ticker, return, vol, spread) into the reservoir. The reservoir "echoes"
the input — its high-dimensional state contains all the recent history
in compressed form.

A simple linear readout `W_out @ reservoir_state` predicts: target,
stop, hold-time, conviction. Train W_out via online ridge regression on
arena outcomes.

**Why it's dynamic.** The reservoir is fixed but the readout retrains
WEEKLY (or daily) on accumulated arena outcomes. The reservoir's high
dimensionality means the readout can find new patterns without
restructuring.

**Arena test.** Build the reservoir, feed 88 days of arena. Train W_out
on first 60 days, test on last 28. Compare to (a) linear baseline on
hand-engineered features, (b) full RNN.

**What "working" looks like:** the reservoir matches or beats the full
RNN on holdout while requiring minutes-not-hours to retrain.

### IDEA 9 — Ising model / spin glass (regime detection)

**Core insight.** Statistical physics: each "spin" can be ±1 (up or
down). Spins influence neighbors via couplings. At high temperature,
spins flip randomly (no order). At low temperature, spins align in
domains (ferromagnetic). Phase transitions: tiny temperature change →
sudden global re-organization.

**MOMENTUM-X application.** Each ticker is a spin: +1 (rallying), −1
(declining). Couplings J_ij between tickers measure historical
correlation. The system's "magnetization" = mean spin = market breadth.

**Phase transition signal.** When the system flips from disorder
(magnetization ≈ 0) to order (|magnetization| > threshold), that's a
**regime change** — exactly what doc 89 §6 identified as the killer for
H3 short. We never had a real-time detector. Now we do.

**Arena test.** Compute the daily magnetization across 88 days. Mark
the dates we identified as regime shifts. Does magnetization show a
clear discontinuity on those dates? If yes, the metric is a forward-
useful regime detector.

**What "working" looks like:** running magnetization correlates with
H3-short profitability per doc 90 — and crosses thresholds CHANGE
strategy parameters automatically (e.g., if magnetization > 0.5,
disable shorts, increase trail width).

### IDEA 10 — Genetic / evolutionary strategy population

**Core insight.** Maintain a POPULATION of N strategies. Each gets a tiny
slice of capital. Each session, allocate next-session capital
proportional to fitness (P&L). Worst die; best mutate and breed.

**MOMENTUM-X application.** Define a strategy as a tuple of parameters:
`(trail_pct, notional_usd, entry_delay_min, max_tickers, gap_min,
sentiment_threshold, ...)`. Spawn 50 strategies, randomly initialized.
Each gets $50 of paper capital. After session, rank by P&L:
- Top 25%: survive unchanged + clone with mutation
- Middle 50%: survive
- Bottom 25%: die, replaced by mutated clones of top performers

After 30 days, the surviving population represents what the market
actually rewarded — without any human tuning. Cross-over: take params
from two parents.

**Why it's dynamic.** The strategy MUTATES. If trail_pct=12 was killing
Friday but trail_pct=18 worked, the population converges away from 12.
If markets shift, the population follows.

**Arena test.** Spawn population on 88-day arena. Track diversity
(parameter spread) over time. Compare top performer at day 30 vs day 88.
Is convergence happening? Does the converged config beat the manually-
chosen one?

**What "working" looks like:** parameter distributions narrow toward
some optimum, AND the optimum from the GA matches or beats our
hand-tuned defaults.

### IDEA 11 — Free-energy principle / active inference (Friston)

**Core insight.** Karl Friston's framework: an organism minimizes
"surprise" (negative log-probability of its sensory input given its
generative model). Action choice = the action that, if taken, would
LEAST SURPRISE the model in the next moment.

**MOMENTUM-X application.** The bot maintains a generative model
`P(market state | bot model)`. Surprise = how unexpected the actual
price path is. The bot's actions (which stock to buy, when to exit)
are chosen to minimize EXPECTED surprise — i.e., it acts to confirm
its predictions.

This is wild because instead of "predict and trade," the bot "trades
to verify." If model says HCAI will rip from $10 → $12, buying HCAI is
the action that resolves the prediction. If price doesn't move, surprise
is high → model updates.

**Arena test.** Build a tiny generative model (Gaussian process over
intraday paths). Run on arena. Measure: does the bot consistently
choose entries that — given outcome — were "predicted by its model"?
Is the model improving over time (surprise decreasing)?

**What "working" looks like:** the bot's average surprise per session
decreases monotonically AND the predictions become tighter (lower
entropy) as the model accumulates evidence.

### IDEA 12 — Attractor dynamics (Hopfield)

**Core insight.** Hopfield networks store memories as energy-minimum
"attractors." Given a partial/noisy input, the network DESCENDS to the
nearest attractor (the closest stored memory). Pattern completion.

**MOMENTUM-X application.** Store every PROFITABLE historical setup
as an attractor in a high-dimensional feature space (gap, RVOL, time-
of-day, sector, IV, news-sentiment, ...). When today's candidate
appears, project its features into that space and find the nearest
profitable attractor. The DISTANCE to the attractor = conviction.

**Why it's dynamic.** Every new profitable trade ADDS an attractor.
Over time the bot accumulates more memories and pattern-completes
better. Memories that haven't been re-visited in N days slowly decay
(forgetting).

**Arena test.** Embed all 88 days × winning setups into a 32-dim
feature space. For each new candidate, find nearest neighbor distance
and outcome. Hypothesis: candidates closer to a winning attractor
outperform random picks.

**What "working" looks like:** average P&L of "near a winning attractor"
candidates > average P&L of "far from any attractor" candidates by a
clean margin. AND the attractor map evolves (regions appear/disappear
over time).

---

## §3 — Common evolutionary substrate

If we're building several of these, they share infrastructure:

```
SIGNAL CORPUS         → arena/data/signals/...
DECISION CORPUS       → arena/data/decisions/...
OUTCOME CORPUS        → arena/data/outcomes/...
WEIGHT/STATE STORE    → arena/data/state/{strategy_name}/...
ARENA REPLAY ENGINE   → mx-arena/replay/...
```

We already have most of this (the arena was built for it). What's needed:

- A `evolve_step()` interface every dynamic strategy implements:
  ```python
  class DynamicStrategy(Protocol):
      def predict(self, state: dict) -> Decision: ...
      def evolve_step(self, decision: Decision, outcome: Outcome) -> None: ...
      def serialize_state(self) -> dict: ...
      def load_state(self, blob: dict) -> None: ...
  ```
- A nightly arena loop that replays today's decisions through every
  registered dynamic strategy and updates their internal state.
- A leaderboard of strategy fitnesses, displayed alongside the bot's
  live P&L.

This means EVERY wild idea above is implementable as one new file
inheriting from `DynamicStrategy`. Pick one a week. Run them all in the
arena in parallel. The ones that survive get promoted to live paper
trading.

---

## §4 — How to run a "shots on goal" lab

This is the meta-recommendation — the discipline that makes 12 wild
ideas tractable:

1. **One idea per week.** Implement, plug into arena, run 30 days of
   replay, write a 2-page brief. 12 ideas = 3 months.

2. **No tuning before measurement.** First run: defaults from the brief.
   Measure raw signal. Only THEN consider tuning.

3. **Survival rule.** A dynamic strategy survives iff: (a) it beats
   buy-and-hold, (b) it beats current bot strategy, (c) it shows
   meaningful change in internal state over time (it's actually
   evolving, not just running).

4. **Composability.** The most valuable outcome isn't any single
   strategy winning — it's discovering that, e.g., MYCELIAL routing
   + DIFFUSION exits gives a better P&L than either alone. Combinatoric
   space is huge; the survival rule keeps it manageable.

5. **The bot's actual capital is sacred.** No idea touches live capital
   until 60+ days of forward arena replay. The lottery (doc 90) IS our
   "outer arena" already — the dynamic strategies are inner-arena
   experiments.

---

## §5 — Three immediate experiments (start this week)

If we want momentum on this NOW, these three are the cheapest:

| Idea | Why first | Time to first signal |
|------|-----------|---------------------|
| #9 Ising magnetization | Pure stat — just compute over historical data, see if regime-shifts pop out | 2 hours |
| #5 Stigmergic decision graph | We have decision logs; just need to add pheromone bookkeeping | 1 day |
| #2 Mycelial 6-node | Simple Hebbian update; arena replay framework exists | 2 days |

The Ising one in particular is BARELY a strategy — it's a diagnostic.
Compute daily magnetization from doc 90's 88-day data, plot it, and we
either see regime-shift signal at the right dates or we don't. **2 hours
of work either validates the entire phase-transition framing or kills it.**

---

## §6 — Bias toward designs that evolve

Per your brief: prefer architectures that grow/adapt/mutate over things
that get tuned and frozen. Online learning > batch retraining. Reactive >
scheduled.

Rank of the 12 by "evolvability score" (purely subjective, but useful):

| Rank | Idea | Why it evolves well |
|------|------|---------------------|
| 1 | #5 Stigmergic agents | Pheromone literally evolves with every trade |
| 2 | #10 Genetic strategy population | Explicit evolution as the mechanism |
| 3 | #6 Slime mold | Topology rebuilds session-by-session |
| 4 | #2 Mycelial network | Hebbian weights update online |
| 5 | #11 Friston active inference | Generative model updates per surprise |
| 6 | #12 Hopfield attractors | New memories added per profitable trade |
| 7 | #8 Reservoir computing | Readout retrains; reservoir doesn't (semi-static) |
| 8 | #7 Cellular automata | Local rules fixed but global pattern evolves |
| 9 | #1 Quantum tunneling | Static gate-shape, only `kT` tunable |
| 10 | #9 Ising | Pure measurement; doesn't itself evolve |
| 11 | #3 Wave-based | Architecture fixed; weights frozen |
| 12 | #4 Diffusion | Trained model, batch-retrained |

Top of this list is where to spend cycles for "alive" systems.

---

## §7 — The drop-of-water test

You used the metaphor of water on a cracked rock. The questions that
test whether something is "alive":

1. **Does it explore?** When given a new substrate, does it try multiple
   facets before committing? (Stigmergic: yes via softmax sampling.
   Mycelial: yes via parallel-node activation. Genetic: yes via
   population diversity.)

2. **Does it exploit?** Once it finds a profitable facet, does it pour
   more resources there? (Pheromone reinforcement: yes. Genetic fitness
   weighting: yes. Slime mold mass redistribution: yes.)

3. **Does it react to environmental change?** When the rock cracks
   differently (regime shift), does it find new paths? (Ising magnetization
   detects, slime mold re-routes, genetic mutations explore new
   parameter regions.)

4. **Does it leave traces that future passes can use?** (Stigmergic by
   design. Hopfield by design. Friston model accumulates evidence over
   sessions.)

The strongest candidates by all four tests are **#5 Stigmergic** and **#10
Genetic Population**. They are the most "alive."

---

## §8 — Files

| Path | Purpose |
|------|---------|
| `docs/research-log/95_dynamic_systems_brainstorm.md` | This document |
| (future) `mx-arena/dynamic/__init__.py` | DynamicStrategy protocol + registry |
| (future) `mx-arena/dynamic/ising_magnetization.py` | Idea #9 — start here |
| (future) `mx-arena/dynamic/stigmergic.py` | Idea #5 |
| (future) `mx-arena/dynamic/mycelial.py` | Idea #2 |

---

## §9 — One-paragraph summary

> "The four seeds you planted (quantum tunneling, mycelial networks,
> wave learning, diffusion) all map onto ideas where the SYSTEM ITSELF
> evolves, not just its parameters. Eight more are sketched here, ranked
> by evolvability. The cheapest first experiment — Ising magnetization
> as a regime detector — is two hours of code and either validates or
> kills the whole phase-transition framing. The most alive — stigmergic
> pheromone trails and genetic strategy populations — should be the
> primary architectural bets if we commit to this direction. The arena
> infrastructure you have ALREADY supports running 12 of these in
> parallel as inner-arena experiments. Pick one a week. Let the rock
> teach the water how to flow."
