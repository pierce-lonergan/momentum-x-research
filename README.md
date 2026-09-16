# Momentum-X

**A falsification-driven research program in systematic equity trading.**
It ran for 311 documented experiments and did not find a tradeable edge — and the interesting part
is how thoroughly it established that.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Paper trading only](https://img.shields.io/badge/trading-paper%20only-lightgrey.svg)](DISCLAIMER.md)
[![Certified edges](https://img.shields.io/badge/certified%20edges-0-red.svg)](docs/ATTEMPTS_LEDGER.md)

---

## What this is

Most trading repositories show a backtest that worked. This one is the opposite: a sustained attempt
to find an edge in US equities, run against itself with pre-registration, adversarial review, and
explicit multiple-testing control — plus the complete written record of every hypothesis it killed,
including several the author believed in.

The headline result, stated the way the repository's own evidence supports it:

> Across 39 hypothesis families and 33 registered trials, **zero** produced a positive, cost-net,
> denominator-honest effect whose confidence interval excludes zero. Over the same 2016–2026 window,
> **buy-and-hold SPY returned +14.91%/yr** — more than the program's own target. The active strategy
> underperformed a passive index position.

That is a negative result. It is also the point: the machinery built to *reach* that conclusion, and
to stop the author from talking himself out of it, is what this repository is actually for.

## The method

The rules hardened over time, usually right after something went wrong. The current set:

| Rule | Why it exists |
|---|---|
| **Pre-registration with a hashed, frozen spec** | A hypothesis is committed and hashed *before* data is touched. No post-hoc target changes. |
| **Executable prereg fixtures** | Prose specs are not enough — a frozen prereg ships a synthetic fixture the runner must reproduce. Added after a spec that said `h=21` in words was executed by code using `h=1`, and passed every gate. |
| **Mandatory adversarial review** | Any apparent pass is `PROVISIONAL` until an independent skeptic fleet tries to kill it. The first result that ever passed all gates was voided this way. |
| **Machine-readable multiplicity counter** | [`scripts/trial_registry.py`](scripts/trial_registry.py) — hash-chained and tamper-evident. The promotion bar is *computed from* the trial count (Bailey–López de Prado deflated Sharpe), not chosen after seeing the result. `promote()` refuses to run: a loop may propose, freeze, collect and report — never promote. |
| **Denominator honesty** | Day-clustered or block bootstrap everywhere. Within-day intraclass correlation in these universes is 0.09–0.35; treating stock-days as independent inflates significance by roughly 2.5x. |
| **Death dates** | Every hypothesis gets an expiry. Bar-lowering and deadline-sliding are not remedies. |
| **Re-tune = kill** | Any change to a live gate's code, target, universe or horizon after arming voids the arm. |

The counter is not decorative. At 33 registered trials, the Sharpe a result must clear to be
distinguishable from the best of N coin flips:

| Sample length | Operative bar (annualised Sharpe) |
|---|---|
| 1 year | 3.76 |
| 2.5 years | 2.38 |
| 10.5 years | 1.16 |

For scale, the best audited 12-year retail systematic track the program could find runs Sharpe 0.80.
This is why nothing here is certified — and why reporting a "Sharpe 1.4 strategy" without stating
*N* is close to meaningless.

## What was tested, and what it cost to find out

A selection from [`docs/ATTEMPTS_LEDGER.md`](docs/ATTEMPTS_LEDGER.md) (39 families, all closed):

| Family | Verdict |
|---|---|
| Per-name directional prediction (all horizons, all universes) | no edge |
| The low-float gapper long universe | **−1% to −2%/ticket**, day-blocked CI excludes zero, negative in 2024, 2025 and 2026 separately |
| Short door (borrow-priced, full census) | net negative after borrow costs |
| Volatility forecasting vs implied (Stage 1–3) | gate pass **voided** — the runner's horizon disagreed with the frozen spec |
| Overnight/intraday ETF decomposition | replicates as a *gross* effect, but is beta plus T-bill interest; buy-and-hold wins on Sharpe 4/4 |
| Index short-volatility (SPY/IWM) | killed on 3 of 5 pre-registered conditions, including a corrupt denominator inherited into the prereg |
| Passive liquidity provision (~10 hypotheses) | structurally unavailable — 100% of retail order flow internalised, rebates paid to the broker |
| Micro-regime kNN retrieval | refuted; the apparent lift was a weak-baseline artifact |

**Cost structure explained more than any signal did.** Measured all-in round-trip cost from true
NBBO: index ETFs 0.72 bps, mega caps 1.76, large caps 2.27 — against roughly 61 bps in the low-float
universe the system originally traded. A large share of the null results turn out to be one
cost-structure result rather than sixty independent failures of imagination.

## The self-correction record

The repository treats its own errors as first-class findings, because catching them was the hardest
engineering in the project:

- A hash-frozen prereg specifying a 21-day horizon was executed by a runner coded at 1 day. Every
  gate passed. A three-agent skeptic fleet caught it; at the frozen horizon the result was negative.
  Produced the executable-fixture rule.
- 44% of an implied-volatility panel was built from contracts whose strike was chosen using *future*
  spot. Produced the selection-provenance rule.
- A cost figure with no artifact behind it was propagated into the binding requirements document and
  used to gate roughly 30 hypotheses. Replaced with true-NBBO measurement.
- A "the book is flat" claim was window-selected; over a longer window the same book was
  significantly negative. All performance claims now carry their window and denominator.
- A live safety defect: stops computed as `price - 1.5*ATR` with no floor went *negative* on volatile
  names, producing unplaceable stop orders and silently unprotected positions. Found by audit rather
  than by loss.

## Engineering

74.5k lines across 218 modules and 272 test files, with a discovery stack that gates every commit:
property-based state machines over the execution bridge, differential testing, static analysis for
project-specific bug classes, Bayesian changepoint detection on the equity curve, and a nightly
config-truth reconciliation comparing intended configuration against what the broker actually did.
That last one fired nine genuine breaches on the day it was first enabled.

## Repository map

```
docs/ATTEMPTS_LEDGER.md    every hypothesis family, its verdict, and the doc that closed it
docs/TARGET.md             the requirements ledger - what a result must clear, and why
docs/research-log/         305 ship documents, 00-297, in chronological order
docs/research-log/README.md  a curated reading path through them
scripts/trial_registry.py  the multiplicity counter
src/                       the trading system: agents, execution, risk, monitoring
tests/                     272 test files including property-based and differential suites
```

**If you read only three documents:** [`ATTEMPTS_LEDGER.md`](docs/ATTEMPTS_LEDGER.md) (what was
tried), [`TARGET.md`](docs/TARGET.md) (what the bar is, and why almost nothing clears it), and
[`docs/research-log/297_the_cost_result.md`](docs/research-log/297_the_cost_result.md) (the cost finding that
reframed the program).

## Running it

Honest status: **this is a research repository, not a deployable product.** It was built for one
operator, one broker account, and one machine.

```bash
python -m venv .venv && . .venv/bin/activate     # .venv\Scripts\activate on Windows
pip install -e ".[dev]"
python -m pytest -m "not slow" -q                # the fast suite
python scripts/trial_registry.py --status        # the multiplicity counter
```

Anything touching market data needs your own API credentials, exported as environment variables.
**No key is embedded anywhere in this repository, and none should be added.** Several research
scripts also expect a local market-data warehouse that is not distributed here.

## Disclaimer

Research code. Paper trading only. Not investment advice, not a performance claim, no warranty.
See [DISCLAIMER.md](DISCLAIMER.md) before drawing any conclusion about returns.
