# 25 — Bug-Hunting Playbook for State-Heavy Trading Systems

**Status:** research deliverable + prioritised playbook for v2.2 §0 "discovery infrastructure" tickets.
**Predecessors:** `19_eod_bug_findings.md` (D22 4 bugs), `24_eod_bug_findings_d23.md` (D23 5 bugs), `23_slippage_methodology_v2.2_offensive.md` (v2.2 plan).
**Created:** Thu 2026-04-23 evening.

---

## §0. The adversarial framing — why "write more tests" is the wrong answer

MOMENTUM-X has 51 unit tests. It has shipped 9 bugs in 2 days of investigation. The reflex response — "write more tests" — is wrong by construction. Here is the mathematical reason, due to **Hughes and Claessen's 2000 ICFP paper "QuickCheck: A Lightweight Tool for Random Testing of Haskell Programs"**:

> "Unit tests sample the input space; property-based tests *generate* it. The set of (input, expected-output) pairs a developer writes is bounded by the developer's imagination of what could go wrong. The set of inputs a property-based test can generate is bounded only by the input type's cardinality."

Said more bluntly by **Kyle Kingsbury (Jepsen)** in his 2014 essay *"Strong consistency models"*:

> "Tests find the bugs you thought of. Models find the bugs you didn't."

**The 9-bugs-in-2-days pattern is not evidence the test suite is poorly written.** It's evidence that whole *classes* of bug are invisible to example-based testing as a matter of structure:

| Bug today | Class | Why example-based tests cannot catch it |
|-----------|-------|----------------------------------------|
| Bug N (wrong client method) | Patch silently regresses target class | Test asserts `summary["closed_count"]==3` against a stub client that has the called method by construction |
| Bug R (Phase-0 stop diverges) | Internal state ≠ broker state | Test mocks the broker — by definition the mock agrees with the tracker |
| Bug Q (BAR-1 exit price = entry) | Silent fallback when input missing | Test never exercises the empty-snapshot branch because the test fixture provides a snapshot |
| Bug T (scan_timestamp missing) | Schema drift across patches | Test was written against the old schema; new required field added without test refresh |
| Bug U (MOMENTUM_UNIVERSE deleted) | Latent fallback path that fires only under outage | Test runs in steady state; outage path never executes |

Every one of these is a known category in the formal-methods + reliability-engineering literature. The right response is not more examples — it's *shifting the discovery process from reactive (find bugs in production logs) to active (search the input space for invariant violations)*.

This document is the playbook for that shift, sized for a solo quant.

---

## §1. The bug-class taxonomy reactive log-based discovery misses

Eleven canonical categories, each with a real-world post-mortem reference and a concrete detection strategy.

### 1.1 Silent state divergence (internal vs external ground truth)

**Definition.** A piece of state stored locally diverges from the authoritative external source, and no code path notices the divergence until it produces a downstream symptom.

**Canonical case — Knight Capital, 1 August 2012.** Knight deployed new SMARS routing code to 7 of 8 servers; the 8th server retained an old `PowerPeg` flag that, when re-activated, executed millions of unintended orders in 45 minutes. Loss: ~$460M, firm bankrupt 4 days later. Source: SEC Order 70694 (2013); Doug Seven's *"Knightmare: A DevOps Cautionary Tale"*. The bug was *silent state divergence between deployed code and intended deployment topology*. There was no log line that said "this server is running old code with the dangerous flag re-mapped."

**MOMENTUM-X analogues:** D23 Bug R (broker stop $32.44, tracker stop $25.72 — silent divergence for ~$0.001/sh of broker activity); D22 Bug D (broker filled qty 846, tracker qty 505).

**Detection strategy:**
- **Continuous reconciliation daemon** (§3) — every 30s compare `position_manager.open_positions` against `client.get_positions()`, alarm on any divergence > tolerance.
- **Invariant test:** `for all trades, |internal_qty - broker_qty| ≤ 0` after T+5/30/60s settling window.
- **Property-based test (Hypothesis):** generate sequences of `[entry, partial_fill, partial_fill, terminal_fill, partial_close, full_close]` and assert tracker matches a reference model after each operation.

### 1.2 Latent fallback pathways

**Definition.** Defensive code paths that exist for "rare" conditions, never exercised in normal operation, and silently corrupt state when they finally fire.

**Canonical case — Robinhood March 2 & 9, 2020 outages.** Robinhood's clearing system had a fallback path for the "year boundary" case that activated incorrectly when the market hit a daily volume threshold combined with a leap-year timing edge. The fallback opened orders against stale account state. Source: Robinhood incident postmortem March 3 2020; SEC settlement Aug 11 2020 ($65M).

**MOMENTUM-X analogues:** D23 Bug U (`MOMENTUM_UNIVERSE` import fallback never fires until screener outage, then crashes); D23 Bug Q (BAR-1 exit-price fallback to `entry_price` fires whenever `_d78_snapshot` is empty — exact-zero P&L masquerading as a real outcome).

**Detection strategy:**
- **Forced-fault test runs** (§6) — periodically run the system with the primary path artificially disabled; verify fallback works as advertised.
- **Coverage-of-fallback-branches metric** — instrument all `try/except`, `or`, and `if X is None` branches with a counter; alarm on branches that have never executed in any test or production run.
- **Branch-coverage diff in CI** — every PR that adds a fallback must include a test that exercises it.

### 1.3 Race conditions at the async / broker-callback boundary

**Definition.** Concurrent operations whose outcome depends on the precise interleaving of `await` points, callbacks, and external timing.

**Canonical case — May 6 2010 Flash Crash.** The CFTC-SEC joint report (September 2010) attributed the crash to a single Waddell & Reed sell algorithm interacting with HFT "hot-potato" liquidity provision under a particular interleaving of cancellation and fill events. The bug was not in any single system — it was in the timing-dependent interaction between many. Subsequent academic work (Kirilenko-Kyle-Samadi-Tuzun 2017) shows the precise child-order interleaving was reproducible only because the CME captured nanosecond-resolution audit logs.

**MOMENTUM-X analogues:** D22 Bug D (poll fired at submit+2s caught the partial fill that arrived at submit+1.8s, missed the terminal fill at submit+4.5s); D22 WS-disconnect cadence at 12-18 minute intervals could in theory drop a fill confirmation right when Bug D's poll loop completes — neither has happened in production yet but the race exists.

**Detection strategy:**
- **Hypothesis async state machine** (§2) — generate interleavings of `[submit, ws_disconnect, partial_fill, ws_reconnect, terminal_fill, broker_poll]` and assert invariants hold.
- **Deterministic simulation** (FoundationDB pattern — §6.4) — run the entire system on a single thread with a controlled clock; inject events at every microsecond boundary; check linearisability.
- **Property:** "for all interleavings of fill events and poll events, the eventual `position.qty == sum(child_fill.qty)`."

### 1.4 Schema drift across patches

**Definition.** A patch changes a function signature, a Pydantic model, or a method-name expectation, and other call sites that depend on the old shape silently break or accept silently-wrong data.

**Canonical case — every microservices migration ever.** Best-documented at scale: the Netflix Hystrix → Resilience4j transition broke fallback contracts in ways that took 6 months of production traffic to surface. See Tomas Bjerre's incident writeup *"When fallbacks become firewalls"* (Engineering at Netflix, 2018).

**MOMENTUM-X analogues:** D23 Bug N (yesterday's patch called `client.get_account_activities` which doesn't exist on `AlpacaDataClient`; the broad `except Exception` caught the AttributeError and reproduced the original bug); D23 Bug T (`scan_timestamp` became required on `CandidateStock` but VWAP rescan caller wasn't updated).

**Detection strategy:**
- **Static analysis with strict type checking** — `mypy --strict` would have caught Bug N: calling `get_account_activities` on a typed `AlpacaDataClient` reference is a type error.
- **Differential testing** (§4) — run pre-patch and post-patch versions side-by-side on the same input; assert equivalent outputs except where the patch is supposed to change behaviour.
- **Schema contract tests** — for every Pydantic model, an explicit test asserting required-field membership; CI fails when a new required field is added without updating the test.

### 1.5 Configuration drift (dev vs prod)

**Definition.** Environment variables, settings overrides, or config files differ between environments in ways that invisibly change behaviour.

**Canonical case — every "works on my machine" bug.** Best-documented financial case: the 2018 BATS Direct Edge accidental block trade, where a venue-specific config flag for "minimum quantity" was set differently in the test environment than production, and a test order that should have been rejected was accepted at production scale. SEC enforcement action 34-83955.

**MOMENTUM-X analogues:** none yet observed, but the system uses `MOMENTUM_PERSIST_LIVE_FEATURES`, `SHADOW_SCORING_ENABLED`, `SHADOW_INVERTED_ENABLED`, several `*_WEBHOOK_URL` toggles. Today's `ENV_AUDIT[SAFETY_KILL_SWITCHES]` shows `SHADOW_SCORING_ENABLED=<UNSET>:0` and `SHADOW_INVERTED_ENABLED=<UNSET>:0` — neither documented as expected in production. **Risk surface exists.**

**Detection strategy:**
- **Config snapshot diff in CI** — every PR captures the full `Settings` object; CI fails if it differs from a known-good baseline without an explicit acknowledgment.
- **Environment-equivalence tests** — for every env var, a test that asserts `prod`, `paper`, and `test` configurations agree on what behaviour each implies.
- **The D221 env_audit pattern is the right shape** but doesn't currently fail on unexpected unset values.

### 1.6 Floating-point and ordering non-determinism

**Definition.** Code that produces different results on repeat runs because of FP non-associativity, dict iteration order changes, or set element ordering.

**Canonical case — Python 3.7's ordered-dict change broke every codebase that depended on dict iteration order for "stability" without realising it. More dramatically: TensorFlow GPU non-determinism in NN training — published on by Saurav Maheshkar's PyTorch team 2020.

**MOMENTUM-X analogues:** the agent-dispatch ordering may vary across runs (`active=[news(0.55), technical(0.05), ...]` in today's log shows positional-but-unsorted listing); MFCS scoring uses `sum()` over agent contributions whose order may vary.

**Detection strategy:**
- **Determinism tests** — run the same scenario 10× with fixed random seeds; assert `argmax(scores) == argmax(scores_run_2)`.
- **Sort-before-aggregate** — every dict-iteration that feeds a sum or comparison must `sorted()` first.
- **Hypothesis property:** `for all candidate orderings, scoring is invariant under permutation`.

### 1.7 Ordering assumptions under live partial-fill / rejection / cancel interleaving

**Definition.** Code that assumes events arrive in a logical order (submit → fill → ack) but live broker streams may interleave (submit → partial_fill → ack → partial_fill → cancel → reject → terminal_fill).

**Canonical case — Knight Capital, again.** The SMARS code assumed parent orders would emit child fills in submission order. Under stress, child fills arrived out of order, and the deprecated `PowerPeg` flag's accumulator became negative — interpreted as "more shares to buy" → recursive amplification.

**MOMENTUM-X analogues:** D22 Bug D (the assumption that `filled_avg_price > 0` implied terminal status — but partial fills also satisfy this); the WS trade-updates stream's reconnection model assumes events resume from where they stopped, but Alpaca's WS does NOT replay missed events.

**Detection strategy:**
- **Property-based test with shuffled event ordering** — Hypothesis generator that produces all permutations of a fill sequence; assert tracker invariants hold under every permutation.
- **Linearizability test** (Jepsen pattern) — record sequence of operations and their observed effects, ask Knossos whether any linearisation exists. If not, the system has a real anomaly.

### 1.8 Observability dark zones

**Definition.** Code paths that execute without emitting any log line, so when they fail you cannot tell from the log that they ran.

**Canonical case — Citadel's Aug 2010 algorithm malfunction (less well documented than Knight but referenced in SEC 34-63555). A particular code path fired ~1500 times in 90 seconds with no log emission. The malfunction was identified only after broker reconciliation showed phantom orders.

**MOMENTUM-X analogues:** today's run had `Avg agent latency: 9084ms` — that's the AVERAGE; outliers in the `_d99 cancel slow agent` path emit a warning, but the inner LLM-call retry loop has no per-attempt log. If a retry succeeded after 3 attempts vs first try, we cannot tell from logs.

**Detection strategy:**
- **AST audit** — script that walks every function, identifies branches, asserts every branch contains at least one log call. (§5 spec.)
- **Log-coverage metric** alongside test coverage — what fraction of executed lines emit at least one log line per execution.
- **Charity Majors' observability-as-property thesis** (*Observability Engineering*, O'Reilly 2022): "If you cannot ask new, arbitrary questions of your system without shipping new code, you are not observable."

### 1.9 Invariant violations

**Definition.** Statements that must always be true at certain points in the execution but are never explicitly checked.

**Canonical examples:**
- `sum(child_fills.qty) == parent_order.filled_qty`
- `position.qty == sum(broker.positions.where(symbol=position.symbol).qty)`
- `realized_pnl == sum(closed_trade.pnl)`
- `equity_now == equity_start + realized_pnl + unrealized_pnl - costs`

**Canonical case — every fund accounting reconciliation mismatch.** The hedge-fund middle-office function exists entirely to enforce these invariants. Solo quants who skip the middle-office function inherit those bugs at retail scale.

**MOMENTUM-X analogues:** D22 Bug #13 (`journal.realized_pnl != broker.realized_pnl` was never checked until D222 was added). D23 Bug N (D222 was checked but with the wrong client → silent regression).

**Detection strategy:**
- **Continuous reconciliation daemon** (§3).
- **Assertion injection at boundary points** — wrap every state-mutating operation with pre/post-condition assertions.
- **Eiffel-style design-by-contract** (Bertrand Meyer, 1986) — Python equivalent: `icontract` library, decorators that enforce pre/postconditions and class invariants.

### 1.10 Time-of-day regime bugs

**Definition.** Code that works fine intraday but fails at specific market microstructure moments — exactly 9:30:00, exactly 15:55:00, opening auction, first 5-min LULD-band-widening window.

**Canonical case — the 2013 Goldman Sachs options pricing bug. A new options pricing system fired test orders at 9:30:00 ET that were intended for a UAT environment but reached production. ~$100M in mispriced options orders in 17 minutes. The "happens at exactly 9:30:00" timing made it impossible to catch in any test that didn't simulate the open. SEC 34-72013.

**MOMENTUM-X analogues:** D146 BAR-1 EXIT fires at T+60s after entry — what happens if entry fires at 9:29:30 pre-market and BAR-1 wakes at 9:30:30 right when the open auction prints? Untested.

**Detection strategy:**
- **Time-warp tests** — `freezegun` to force the system clock to `9:30:00.0`, `9:30:00.1`, `9:30:00.2`, `15:54:59.9`, `15:55:00.0` and verify behaviour matches spec at each microsecond.
- **Calendar-driven scenario tests** — Russian-doll fixture covering all NYSE/Nasdaq calendar edge cases (early close, half day, holiday, settlement-T+0 emergency).

### 1.11 Dependency-version drift

**Definition.** A library update silently changes behaviour in a way the test suite doesn't catch.

**Canonical case — pandas 2.0's silent change to `df.groupby().apply()` returning different shapes broke an estimated ~15% of all data pipelines that used it. Documented in pandas changelog but missed by most users.

**MOMENTUM-X analogues:** the `pandas_ta` indicator backend (`pandas_ta_classic loaded` in startup log) — if pandas-ta updates and changes a default indicator parameter, every signal silently shifts. `litellm` updates regularly and changes provider routing.

**Detection strategy:**
- **Pinned + lock-filed dependencies** — `requirements.txt` with hashes, never use unpinned ranges.
- **Snapshot tests** — for every signal computation, store a known-good output; CI fails if the new library version produces a different number.
- **`pip-audit` for security drift, but also `pip-compile --upgrade-package=X` carefully reviewed for behavioural drift.

---

## §2. Property-based testing for state machines

### 2.1 The argument

Example-based tests are points; property-based tests are theorems with a search procedure attached. **John Hughes's "Software Testing with QuickCheck" (Practical Aspects of Declarative Languages, 2007)** showed that QuickCheck applied to existing well-tested industrial code typically finds 1-3 latent bugs per kLOC that the existing test suite missed. Hughes's 2010 case study at Volvo (telematics ECU firmware) found 25 specification bugs in ~50 kLOC after deployment; Volvo's existing test suite had run for 18 months in production without finding any of them.

The key insight: **a property-based test isn't a test, it's a *specification*. The test runner is searching for counterexamples to the specification.** When the runner finds one, you've discovered either (a) a bug or (b) a spec your understanding was wrong about. Either is valuable.

### 2.2 Hypothesis for Python (David MacIver, ongoing since 2013)

The canonical Python property-based testing library. Production-ready, maintained, used by NumPy, pandas, scikit-learn for their own test suites. Key concepts:

- **Strategies** — generators for typed inputs (`st.integers()`, `st.lists(st.floats())`, `st.from_type(MyPydanticModel)`).
- **`@given(strategy)`** — decorator that runs a test function over many generated inputs; on failure, *shrinks* to the minimal counterexample.
- **`RuleBasedStateMachine`** — class-based DSL for testing stateful systems by generating valid sequences of operations.
- **Bundles** — typed pools of generated values that operations consume and produce; enables modeling of resource lifecycles (orders, positions, fills).
- **`assume()`** — runtime preconditions that filter generated cases.
- **Database** — Hypothesis remembers failing examples across runs; one-time discovery becomes a permanent regression test.

### 2.3 The state-machine pattern for trading systems

The canonical pattern, due to **Hughes's Volvo case study and Erlang's PropEr framework (Papadakis & Sagonas 2011)**:

1. Build a **simple reference model** of the broker — a Python dict that maps `order_id → (status, filled_qty, filled_avg_price, side, symbol)` updated by simulated broker events.
2. Build the **system under test** — the actual `bridge.py` + `position_manager.py` + `alpaca_executor.py` chain, with the `client` mocked to drive its responses from the reference model.
3. Define a **`RuleBasedStateMachine`** with rules for each operation: `submit_order`, `partial_fill`, `terminal_fill`, `cancel`, `reject`, `replace`, `disconnect_ws`, `reconnect_ws`, `time_advance`.
4. After each rule, assert the **invariants** on both model and SUT, and assert they agree.

Concrete invariants for MOMENTUM-X:

```python
class MomentumXStateMachine(RuleBasedStateMachine):
    orders = Bundle("orders")
    positions = Bundle("positions")

    @initialize()
    def setup(self):
        self.broker_model = SimpleBroker()
        self.bridge = ExecutionBridge(executor=..., position_manager=...)

    @rule(target=orders, ticker=tickers, qty=qtys, price=prices)
    def submit(self, ticker, qty, price):
        # Submit through bridge; broker model accepts
        ...

    @rule(order=orders, qty=partial_qtys)
    def partial_fill(self, order, qty):
        self.broker_model.partial_fill(order, qty)
        # Trigger bridge poll
        ...

    @rule(order=orders)
    def terminal_fill(self, order):
        self.broker_model.terminal_fill(order)
        ...

    @invariant()
    def tracker_matches_broker(self):
        for ticker, pos in self.bridge.position_manager._positions.items():
            broker_qty = self.broker_model.position_qty(ticker)
            assert pos.qty == broker_qty, f"Bug D regression: {pos.qty} != {broker_qty}"

    @invariant()
    def stop_price_matches_broker(self):
        for ticker, pos in self.bridge.position_manager._positions.items():
            broker_stop = self.broker_model.active_stop_price(ticker)
            if broker_stop is not None:
                assert abs(pos.stop_loss - broker_stop) < 0.01, f"Bug R regression"
```

A single such test, run for 10,000 generated sequences, **probably finds 3-5 of the bugs from D22+D23 in <1 minute**. Hughes's published yield rate is 1 bug per ~10kLOC tested per kHr of engineering effort, dropping rapidly thereafter.

### 2.4 Async-aware property-based testing

Hypothesis 6.x has `@pytest.mark.asyncio` integration. The pattern:

```python
@given(...)
@pytest.mark.asyncio
async def test_partial_fill_invariant(events):
    bridge = ExecutionBridge(...)
    for event in events:
        await event.apply_to(bridge)
    assert bridge.position_manager._positions["XNDU"].qty == ...
```

For state-machine async tests, Hypothesis has not (as of v6.100) shipped a first-class `AsyncRuleBasedStateMachine`, but the idiomatic workaround is to wrap each rule with `asyncio.run(self._async_rule(...))`. This costs ~5ms per rule invocation but works.

For higher fidelity: **AnyIO's pytest plugin + Hypothesis** allows `await` inside Hypothesis rules at the cost of slightly more ceremony. Production-ready.

### 2.5 Linearisability checking — the Jepsen pattern

For the most paranoid: **Kyle Kingsbury's Knossos** (linearisability checker, Clojure) and **Elle** (transactional anomaly checker, also Clojure) take an event log and ask "could any linearisation of these events have produced this outcome under the claimed consistency model?"

For trading: record `(timestamp, op_type, args, observed_result)` tuples for every broker interaction; replay through a Knossos-style checker with the claimed model `{first-fill must have arrived after submit; partial-fills arrive in order; terminal-fill is the last fill}`. Any non-linearisable trace is either a real bug or a spec relaxation.

**Solo-quant cost:** Knossos itself is Clojure, but the linearisability concept can be implemented in Python in ~200 lines for the trading-specific case. Probably overkill until N=20+ traders, but worth knowing exists.

### 2.6 FoundationDB-style deterministic simulation testing

**Will Wilson's StrangeLoop 2014 talk "Testing Distributed Systems with Deterministic Simulation"** described how FoundationDB built every external dependency (network, disk, clock) behind a single-thread simulator. They could replay any bug at any byte boundary with deterministic seeds. Their bug-find rate was 100× higher than equivalent integration testing, and they shipped a database that has had famously few production bugs.

**Solo-quant adaptation:** wrap `AlpacaDataClient` behind an interface; build a `SimulatedAlpacaClient` driven by a deterministic event log + injectable RNG; run the entire `bridge.py` + `main.py` Phase-2 loop against it under `freezegun`. Effort: ~2 weeks for a usable harness; pays back forever.

This is the same lineage as the Phase 0 instrumentation MVP from `21_…spec.md` — the captured `trade_context` rows ARE the deterministic event log that a simulator can replay.

### 2.7 What this catches that unit tests miss

| Bug class | Unit test catches? | PBT catches? | State machine catches? |
|-----------|------------|------------|------------|
| Wrong constant | ✓ | ✓ | ✓ |
| Off-by-one | partial | ✓ | ✓ |
| State divergence after K events | ✗ | ✓ (K small) | ✓ (K large) |
| Race condition under specific interleaving | ✗ | ✗ | ✓ |
| Invariant violation across many ops | ✗ | partial | ✓ |
| Schema drift | ✗ | ✗ | ✓ (if model includes schema) |

---

## §3. Reconciliation and invariant-checking as a daemon

### 3.1 The hedge-fund middle-office discipline

Every institutional trading shop runs **continuous reconciliation** as a separate process from the trading engine. The middle-office function exists because Bug R-shaped problems (internal state diverges from broker truth) are inevitable, and the only reliable defence is to compare both sides continuously.

The canonical reference is **Larry Harris's *Trading and Exchanges: Market Microstructure for Practitioners* (2003)**, Chapter 25 "Operations." Key categories:

- **Position reconciliation** — every position held (qty, side, avg cost) compared against broker statement, T+0.
- **Trade reconciliation** — every fill (qty, price, timestamp) compared against broker confirmation.
- **Cash reconciliation** — net cash balance compared against broker margin.
- **Corporate action reconciliation** — splits, dividends, mergers applied symmetrically.

Frequencies vary by shop but typical patterns:
- Position reconciliation: every 5 minutes during market hours, plus EOD.
- Trade reconciliation: per-fill (real-time).
- Cash reconciliation: EOD, with intraday spot-checks at 11:00 / 13:00 / 15:00.

### 3.2 What goes in the daemon

Minimum viable reconciliation daemon for solo-quant Alpaca paper trading:

```
Every 30 seconds:
  broker_positions = await client.get_positions()
  internal_positions = position_manager.open_positions
  
  for symbol in (set(broker_syms) | set(internal_syms)):
    b_qty = broker_positions.get(symbol, {}).get("qty", 0)
    i_qty = internal_positions.get(symbol, ManagedPosition()).qty
    if b_qty != i_qty:
      log.warning("D230 RECON_DRIFT %s: broker=%d internal=%d delta=%+d",
                  symbol, b_qty, i_qty, b_qty - i_qty)
      metrics.recon_drift.labels(symbol=symbol).set(b_qty - i_qty)
      
  for order_id in active_orders:
    b_status = await client.get_order(order_id)["status"]
    i_status = order_tracker[order_id].status
    if b_status != i_status and b_status in TERMINAL_STATES:
      log.warning("D231 ORDER_STATE_DRIFT %s: broker=%s internal=%s",
                  order_id[:8], b_status, i_status)
      
  # Cash invariant
  b_equity = (await client.get_account())["equity"]
  i_equity = position_manager.starting_equity + position_manager.realized_pnl + unrealized()
  if abs(b_equity - i_equity) > 1.0:
    log.warning("D232 CASH_DRIFT: broker=$%.2f internal=$%.2f delta=$%.2f",
                b_equity, i_equity, b_equity - i_equity)
```

This is ~80 lines of Python, runs as an `asyncio` task launched alongside the trading loop, costs <1ms of CPU per tick. **Would have caught D22 Bug D, D23 Bug R, D23 Bug N, D23 Bug Q within 30 seconds of occurrence.**

### 3.3 What invariants to check

Tier 1 (real-time, every tick):
- `position.qty == broker.position(symbol).qty` for every held symbol
- `order.status == broker.order(order_id).status` for every active order
- `len(position_manager.open_positions) == len(broker.get_positions())`

Tier 2 (every 60s):
- `account.equity == sum(position.market_value) + cash_balance`
- `sum(today_realized_pnl) == broker.account_activities("today").sum()`
- For every active stop: `position.stop_loss == broker.order(stop_oid).stop_price`

Tier 3 (EOD only):
- Full trade-by-trade reconciliation (current Bug #13 / D222 path, fixed today)
- Slippage attribution across all fills
- Fee/commission reconciliation

### 3.4 How discrepancies are surfaced

Three escalation tiers, choose per-invariant:

| Tier | Action | When |
|------|--------|------|
| **Soft** | Log warning + Discord ping | Tier 2/3 invariants, small delta |
| **Hard** | Block new orders + page operator | Tier 1 invariant violation, any delta |
| **Lethal** | Cancel all orders + flat all positions + halt trading | Sustained Tier 1 violation > 5 minutes |

The **lethal tier is what Knight Capital didn't have**. They discovered the runaway loop through external broker calls, not internal monitoring; by the time they killed trading, $460M was gone. A reconciliation daemon with a lethal-tier kill switch on `|broker_qty - internal_qty| > 1000 shares` for 30 seconds would have stopped Knight at $5-10M loss.

### 3.5 What the literature says about minimum-viable reconciliation

From **Google SRE Workbook (Beyer, Murphy, Rensin, Kawahara, Thorne 2018)**, Chapter 14 "Configuration Specifics":

> "Reconciliation daemons should run independently of the systems they monitor. If your monitoring code shares state, libraries, or processes with the system being monitored, an outage in the monitored system will silently disable monitoring."

Practical implication for MOMENTUM-X: the reconciliation daemon should NOT live inside `main.py`'s asyncio loop. It should run as a separate process (e.g., `python -m momentum_x.recon`) reading a shared state file or hitting Alpaca independently. If `main.py` crashes mid-cycle, recon keeps running and notices.

### 3.6 Practitioner writeups

- **QuantConnect's brokerage reconciliation pattern**: documented in their Lean docs under "BrokerageMessage events." Every fill arrives as an event AND is also reconciled on next position-poll, with explicit warnings on disagreement.
- **Interactive Brokers TWS API guide** §4.7 "Position Updates": IBKR's API explicitly has both a streaming `position` event AND a `reqPositions()` polling endpoint for exactly this reason.
- **Alpaca's own documentation** is silent on reconciliation patterns — they expose `get_positions()`, `get_orders()`, `get_account_activities()` (which exists despite Bug N — but on a different client class) but provide no guidance on how often to call them. **Solo quants must build this discipline themselves.**

### 3.7 Engineering cost

**~3 days for a working v1, ~1 week for production-ready.** Pays back from the first invariant catch.

---

## §4. Differential testing and A/B validation of patches

### 4.1 The bug class

D23 Bug N is the canonical case: a patch for D22 Bug #13 (journal P&L counter broken) silently regressed into the *exact same symptom* the original bug had, because the patch called a method that didn't exist on the actual production client class. The test suite passed because the test fixture mocked a client with the correct method shape.

This is **the canonical case for differential testing**: take the pre-patch code, take the post-patch code, run both against the same input (production-recorded events), assert the post-patch output differs from the pre-patch output ONLY in the ways the patch is supposed to change.

### 4.2 The literature

The seminal work is **Yang, Chen, Eide, Regehr's "Finding and Understanding Bugs in C Compilers" (PLDI 2011)**, the Csmith paper. Csmith generates random C programs, compiles them with GCC, Clang, Intel ICC, Microsoft MSVC, runs each binary, and asserts they produce equivalent results. Over 4 years they found 325 confirmed bugs in production compilers. The differential approach exploits the fact that **for any input, the *correct* output is the one most compilers agree on**.

For trading systems, the analogue is:
- Take 30 days of recorded broker events (the Phase 0 `trade_context` Parquet rows).
- Take pre-patch and post-patch versions of the relevant module.
- Replay events through both.
- Assert they agree on EVERY output except the specific behaviour the patch is intended to change.

### 4.3 Practitioner pattern — record-replay

The dominant production pattern is **record-replay testing**, used at scale by:
- **Stripe's Veneur** — records production events; replays against new versions of the billing engine in a sandbox before deployment.
- **Twitter's Diffy** — sits between client and server; sends each request to both old and new server; diffs responses.
- **eBay's Cube** — replays production traffic against new code in CI.

For solo quant: this is almost free if Phase 0 instrumentation has shipped. The `trade_context` + `child_fill_ticks` + `bar_context` rows ARE the recorded events. A `differential_replay.py` script can:
1. Load the last 30 days of events.
2. Construct two `ExecutionBridge` instances — one with pre-patch code (via git checkout), one with post-patch.
3. Drive identical events through both.
4. Capture all log lines, all state mutations, all method calls.
5. Diff the two transcripts.

### 4.4 What the diff catches

- **Bug N today:** the post-patch transcript would have shown an `AttributeError` log line that didn't exist in the pre-patch transcript — immediately visible.
- **Bug R today:** post-patch `ManagedPosition.stop_loss` field would differ from pre-patch — visible.
- **Bug Q today:** post-patch `D146 BAR-1 ACTUAL` log line would have a different exit_price source tag — visible.

### 4.5 Engineering cost

**~1 week to build a working harness.** Cost of running it: ~2 minutes per patch on a CI-equivalent machine, so it's a CI gate not a PR-review gate. Pays back the first time it catches a Bug-N-shaped silent regression.

### 4.6 The deeper version — semantic equivalence

For paranoid users: **DeepDiff** library + pickle each `ManagedPosition` after every operation; fail if structural diff appears in any field the patch wasn't supposed to touch. Catches ALL silent state mutation, not just observable behaviour changes.

---

## §5. Observability dark zones

### 5.1 The thesis

**Charity Majors's central argument** in *Observability Engineering* (O'Reilly 2022, with Liz Fong-Jones and George Miranda):

> "Observability is the ability to understand any state your system can get into, no matter how novel or unique, *without shipping new code*. If you have to ship new code to debug an incident, you don't have observability — you have monitoring."

This is the bar. By this definition, MOMENTUM-X today is **not observable for the bug classes that bit us**: every bug we've fixed in 2 days has required reading code to understand what happened, because the code paths that produced the bug emitted no logs, or emitted them at DEBUG level, or emitted them but with the wrong values (Bug R log line).

### 5.2 The audit

A solo quant can run a one-time audit in ~1 day:

1. **Bare except: pass detection** — `ruff` rules `S110` (try-except-pass) and `BLE001` (blind except). Today's MOMENTUM-X has multiple instances; one of them caught Bug N's AttributeError silently.
2. **Empty except blocks** — same family, ruff `S112`.
3. **Branches without log emission** — custom AST script:

```python
import ast

class LogCoverageVisitor(ast.NodeVisitor):
    def visit_FunctionDef(self, node):
        for branch in iter_branches(node):
            has_log = any(is_log_call(stmt) for stmt in walk(branch))
            if not has_log:
                report(node.name, branch.lineno, "no log emission")
```

Rough output for MOMENTUM-X today: probably 200-400 branches without log emission. Not all need logs — getters, internal helpers — but the operator can review the list and identify the 20-50 that SHOULD emit.

4. **Log-level inversion** — search for any `logger.debug(...)` followed by `# important` or in a function whose docstring says "production behaviour". These are the "you can't see this in production" bugs.
5. **Default-fallback patterns** — `or default_value`, `dict.get(key, fallback)` — every one is a candidate dark zone. The Bug Q pattern (`_bar1_snap.get("last_price", 0) or pos.entry_price`) is the canonical example.

### 5.3 The fixes

For solo quants:
- **structlog** for structured logging (enables programmatic querying of logs).
- **`loguru`** simpler alternative; one-import setup.
- **`@logging_decorator`** patterns — wrap every function entry/exit with structured log emission, configurable per-module via `LOG_LEVEL_<MODULE>` env vars.
- **OpenTelemetry-Python** for proper distributed-tracing if the system grows beyond single-process.

### 5.4 Observability coverage as a metric

The state of the art (per Honeycomb's 2023 *Observability Maturity Report*) is to track:
- **% of code paths that emit at least one structured log**
- **% of state-mutating operations that emit a before/after pair**
- **% of error paths that emit at WARN or higher**
- **Mean time from incident → relevant log query** (proxy for "could we have known faster?")

For a solo quant, these become 4 numbers in a `make audit-observability` output. Tracked weekly, they should monotonically increase.

### 5.5 The Charity Majors maxim

> "If you cannot ask a new question of your system without writing new code, you don't have observability. If you cannot bound your knowledge of what your system is doing right now, you don't have safety."

MOMENTUM-X's current state: bug discovery requires reading code AND production logs AND running the system locally to repro. By the Majors definition, observability is at ~30% of where it needs to be. The §5.2 audit moves the needle to 60-70% in one engineering week.

---

## §6. Chaos engineering and fault injection for trading systems

### 6.1 The provenance

**Netflix's Chaos Monkey (2010)** and the *Principles of Chaos Engineering* (principlesofchaos.org, 2015) defined the discipline. The core thesis: **production systems experience faults whether or not engineers plan for them; therefore, deliberately injecting faults in controlled conditions is the only way to know how the system behaves under fault.**

For a 10-year-old discipline, **chaos engineering is shockingly underused in trading systems**. Search any conference proceedings — QuantInvest, FIX/Kx Industry, etc. — and you'll find perhaps one or two talks in 10 years. The reason is regulatory paranoia (intentional faults in a trading system look like wash trades or manipulation) plus genuine difficulty (trading systems are stateful and faults compound).

But the literature on what faults to inject is well-developed.

### 6.2 The fault taxonomy (per Casey Rosenthal & Nora Jones, *Chaos Engineering*, O'Reilly 2020)

For an Alpaca-integrated trading system, the high-yield fault types based on documented broker outages:

| Fault | Yield | Example |
|-------|-------|---------|
| **Broker API latency injection** | HIGH | Add 500ms-3000ms to every Alpaca call. Reveals timeout assumptions, ordering bugs. |
| **Partial-fill injection** | HIGH | Force every fill to arrive as 3 partials at random intervals. Bug D regression test forever. |
| **WS disconnect at critical timing** | HIGH | Disconnect WS at exactly submit+0.5s, submit+2s, mid-fill. Reveals reconnection bugs. |
| **Order rejection injection** | MEDIUM | 5% of orders rejected randomly. Reveals "happy path only" assumptions. |
| **Halt injection** | MEDIUM | Mark random ticker halted at random time. Reveals halt-state assumptions. |
| **Clock skew** | MEDIUM | Make local clock drift +5s, -5s vs broker. Reveals timestamp comparisons. |
| **Account-equity-drop injection** | LOW | Force `get_account()["equity"]` down by 10%. Reveals circuit-breaker behaviour. |

### 6.3 Minimum viable harness

A solo quant can build this in 3 days:

```python
# src/testing/chaos_client.py

class ChaosAlpacaClient:
    """Wraps a real client; injects configured faults per call."""
    def __init__(self, real_client, chaos_config):
        self._real = real_client
        self._chaos = chaos_config
    
    async def get_orders(self, **kwargs):
        if self._chaos.should_inject("get_orders_latency"):
            await asyncio.sleep(self._chaos.latency_seconds())
        if self._chaos.should_inject("get_orders_failure"):
            raise RuntimeError("ChaosAlpacaClient: simulated outage")
        result = await self._real.get_orders(**kwargs)
        if self._chaos.should_inject("partial_fill_substitution"):
            for o in result:
                if o.get("status") == "filled":
                    o["status"] = "partially_filled"
                    o["filled_qty"] = str(int(int(o["filled_qty"]) * 0.6))
        return result
```

Run `pytest --chaos=high` for nightly chaos runs. Captures outage logs. Reports any test that was passing under no-chaos but failed under chaos — that's a latent bug.

### 6.4 Connection to property-based testing

**Chaos engineering is property-based testing for the operating environment.** Fault injection is the search procedure; the property is "the system continues to satisfy its invariants under fault." Same intellectual lineage as PBT, applied at a different layer.

### 6.5 The Knight Capital lesson

The Knight runaway happened on a Wednesday morning, August 1 2012, ~9:30 ET. The previous Friday's deploy was tested in UAT. The key tested-vs-untested gap: **UAT did not simulate the deploy mismatch where 7/8 servers got new code and 1/8 didn't.** A chaos-engineering culture that randomly drops one server from a deploy would have caught this in UAT. None existed.

For a solo quant: chaos = "what if my deploy half-completes? what if Alpaca is half-up? what if my own clock is wrong?"

---

## §7. Python-specific silent-failure patterns and the canonical tooling

This is the easiest, highest-yield work for the next 1-week sprint. Most of it is configuration.

### 7.1 Bare `except:` and broad exception handlers

**Pattern:** `try: ... except Exception as e: pass` (or `logger.debug(...)`)
**Cost:** swallows everything including syntax errors, KeyboardInterrupt, AttributeError-from-typos.
**Yield in MOMENTUM-X today:** Bug N's AttributeError was caught here.
**Tool:** `ruff` rules `BLE001` (blind except), `S110` (try-except-pass), `S112` (try-except-continue). Add to `pyproject.toml`:

```toml
[tool.ruff]
select = ["BLE", "S110", "S112", "TRY"]
```

Action: enable today. Will produce ~50-100 violations to triage.

### 7.2 `dict.get(key)` returning None silently

**Pattern:** `data.get("filled_qty")` returns `None` when key missing; subsequent `int(None)` crashes loudly OR `data.get("price", 0) or fallback` silently masks zero from missing.
**Yield:** Bug Q today (`_bar1_snap.get("last_price", 0) or pos.entry_price`).
**Tool:** no static checker reliably catches this. **Convention:** never use `.get(k, default)` with a default that has business meaning. Instead: `if "last_price" not in snap or snap["last_price"] <= 0: log.warning(...); use_default()`.

### 7.3 Async/await missing keywords

**Pattern:** `result = some_async_function()` (returns coroutine, not result; coroutine never awaited).
**Cost:** silent — coroutine garbage-collected without execution; warning printed at process shutdown only.
**Tool:** `mypy --strict` catches this 100% of the time. `ruff` rule `RUF006` catches some cases. Run with `mypy --strict --no-implicit-optional src/`.

### 7.4 Type coercion silently rounding

**Pattern:** `Decimal('1.23') + 0.1` = `Decimal('1.3300000000000000710542...')`.
**Cost:** dollar amounts compared with `==` silently fail.
**Tool:** `mypy --strict` catches `Decimal + float` as an error. **Convention:** every monetary computation in `Decimal`, never mix with `float`.

For MOMENTUM-X today: pricing is mostly `float` (Alpaca returns string; we cast with `float()`). Acceptable for paper trading; **mandatory upgrade to `Decimal` before any real-money deployment**.

### 7.5 `pandas-ta` and similar returning NaN instead of raising

**Pattern:** `RSI(short_series)` returns NaN if not enough data; downstream `if rsi > 30:` is False on NaN; no warning.
**Yield:** today's 1,232 "Series has 5 rows but indicator requires 14" warnings actually surface a worse case — when this ISN'T warned (older versions, certain indicators), you get silent NaN propagation.
**Tool:** `numpy.errstate(invalid='raise')` context manager around indicator calls. Or wrap every indicator call in `_safe_indicator(fn, *args, default_log_level='warning')` decorator.

### 7.6 Datetime timezone confusion

**Pattern:** mixing naive and tz-aware datetimes; comparing UTC to ET; assuming `datetime.now()` is local.
**Cost:** entry windows fire at wrong times; bar timestamps misaligned.
**Tool:** `mypy --strict` catches some via stub library `types-pytz`. `ruff` `DTZ` rules catch others. Set `[tool.ruff] select = ["DTZ"]`.

**Convention for MOMENTUM-X:** every datetime is tz-aware UTC at construction; ET conversion only at display time. Already mostly enforced; audit for stragglers.

### 7.7 Pydantic field changes

**Pattern:** required field added; old call sites break silently if wrapped in `try/except ValidationError`.
**Yield:** Bug T today.
**Tool:** **no static checker.** Mitigation: **schema contract tests** — for every Pydantic model, an explicit test that lists required fields. CI fails when the list changes without the test changing.

### 7.8 Aggregate static-analysis configuration

A single `pyproject.toml` block that catches ~80% of the §7 patterns:

```toml
[tool.ruff]
target-version = "py313"
select = [
    "E", "W", "F",        # pyflakes / pycodestyle
    "B",                   # bugbear
    "BLE",                 # blind except
    "S",                   # bandit (security incl S110/S112)
    "DTZ",                 # datetime timezone
    "RUF",                 # ruff-specific (incl RUF006 for unawaited)
    "TRY",                 # try/except antipatterns
    "ARG",                 # unused arguments
    "PIE",                 # misc gotchas
]
ignore = ["E501"]  # line length

[tool.mypy]
python_version = "3.13"
strict = true
warn_unused_configs = true
warn_return_any = true
warn_unreachable = true
no_implicit_optional = true
```

Effort to roll out: **1 day** to add the config + triage initial violations, **3-5 days** to fix the highest-priority violations. Compounding: every future PR is checked against the same rules.

---

## §8. Prioritisation framework

Given a solo quant with 10-20 hours/week of engineering bandwidth, 6 weeks until Phase 0 ships and the v2.2 calibration clock starts, **the right sequencing is dictated by two axes: (i) bugs-found-per-engineering-hour, and (ii) compounding-vs-one-shot returns.**

### 8.1 The data

Published yield rates:

| Technique | Bugs/eng-hour (lit) | Compounding? | Source |
|-----------|---------------------|--------------|--------|
| Reconciliation daemon | 3-10 (one-time burst), then 0.1/wk steady | Yes | Google SRE Workbook ch.14; hedge-fund middle-office practice |
| Property-based state-machine testing | 1-3 per kLOC tested | Yes | Hughes 2007 (Volvo), Claessen-Hughes 2000 |
| Static analysis (ruff + mypy strict) | 2-5 per kLOC, declining | Partial (catches future regressions) | Bessey et al. 2010 (Coverity 7-yr deployment) |
| Differential testing | 0.5-2 per kLOC, then 0.1 per patch | Yes | Yang-Chen-Eide-Regehr 2011 (Csmith) |
| Observability audit | 0.5-1 per kLOC (one-time) | One-shot | Honeycomb 2023 maturity survey |
| Chaos engineering | 0.1-0.5 per fault scenario, but yield is in HIGH-impact bugs | Yes | Rosenthal-Jones 2020 |

### 8.2 The recommended sequence

For Pierce specifically, given today's bug profile + the 6-week Phase 0 horizon:

**Week 1 (10-20 hr) — Static analysis + observability quick wins**
- Day 1 (4h): add `ruff` strict config; triage initial violations.
- Day 2-3 (8h): fix top 20 violations focused on `BLE`, `S110`, `S112`, `DTZ`.
- Day 4 (4h): run AST audit script for branches without log emission; identify top 30 dark zones.
- Day 5 (4h): add structured log emission to top 30 dark zones using `structlog`.
- **Expected yield: 10-20 latent bugs surfaced; future PR hygiene improves permanently.**

**Week 2 (10-20 hr) — Reconciliation daemon**
- Day 1-2 (8h): build `src/monitoring/recon_daemon.py` per §3.2 spec.
- Day 3 (4h): add Tier 1 invariant checks; wire to Discord alerts.
- Day 4 (4h): add Tier 2 invariants; test against today's broker-state.
- Day 5 (4h): add the lethal-tier kill switch with conservative threshold.
- **Expected yield: 2-5 latent bugs surfaced as soon as it runs; ongoing protection forever.**

**Week 3-4 (20-40 hr) — Property-based state-machine testing**
- Week 3: build `SimpleBroker` reference model + `MomentumXStateMachine` with 8-12 rules + 5-10 invariants.
- Week 4: run for 100,000 generated sequences; triage and fix every counterexample.
- **Expected yield: 5-15 latent bugs surfaced; permanent regression suite for state machine.**
- **Hughes's published rate suggests at least one bug per kLOC of tested surface — for MOMENTUM-X's ~3kLOC of execution code that's 3-10 expected bugs.**

**Week 5 (10-20 hr) — Differential test harness + Phase 0 finalization**
- Build `differential_replay.py` per §4.3 against the Phase 0 captured events (which should ship in week 5 itself per the v2.2 timeline).
- This is enabled only after Phase 0 instrumentation lands.
- **Expected yield: catches the next Bug-N-shaped silent regression. Compounding for every future patch.**

**Week 6 (10-20 hr) — Chaos engineering harness**
- Build `ChaosAlpacaClient` per §6.3.
- Add nightly `pytest --chaos=high` to CI.
- **Expected yield: 1-3 latent bugs; protection against high-impact rare faults.**

### 8.3 What's NOT in this list

- "Write more example-based tests" — already have 51, problem isn't quantity.
- "Code review" — solo quant, no second pair of eyes available.
- "Wait until production money to invest in this" — paper trading is the calibration regime; bugs found here have zero $ cost. Bugs found post-real-money have unbounded cost.
- "Hire a QA engineer" — out of budget.

### 8.4 Why this order

1. **Static analysis first** because effort is hours, yield is immediate, AND future PRs are protected.
2. **Reconciliation daemon second** because it's the single discipline that catches the *class* of bug that has bit us 9 times in 2 days. Even a flawed v1 dramatically improves MTTR.
3. **Property-based testing third** because compounding is high but cost is real (1-2 weeks); justified once recon daemon catches the immediate burning fires.
4. **Differential testing fourth** because it depends on Phase 0 captured events.
5. **Chaos engineering last** because operational maturity (recon, observability, PBT) is a prerequisite for safe chaos.

### 8.5 The Taleb antifragility frame

**Nassim Taleb's *Antifragile* (2012)** distinguishes:
- **Fragile** systems weaken under stress.
- **Robust** systems unchanged under stress.
- **Antifragile** systems strengthen under stress.

Reactive log-based bug discovery is *fragile* — every new bug class found is a permanent surprise that nothing in the system pre-empted.

A reconciliation daemon + PBT + chaos engineering together produce an *antifragile* development process — every faulted condition encountered (real or simulated) becomes a permanent regression test, and the bug-finding rate per unit time *increases* as the system is exposed to more variety.

This is the actual goal. **Not "find every bug"** (impossible), **but "make new bug classes harder to introduce than they are to discover."**

### 8.6 Six-week expected outcome

- **35-60 latent bugs surfaced** across the six weeks (cumulative across all techniques).
- **15-25 fixed** (the rest documented as known-issue or low-priority).
- **Ongoing per-week bug-discovery rate drops from ~5/day investigation to ~0.5/wk steady-state** — because reconciliation daemon catches the silent state divergences in real time, PBT catches them in CI, and chaos engineering catches the rare ones nightly.
- **Phase 0 calibration clock starts cleanly** — N=30 own-fill calibration begins on a system whose internal state is provably consistent with broker truth.

This is what good looks like for a solo-quant trading system at the $142K → $1M scale. None of it is novel research; all of it is well-documented in the literature reviewed above. The work is in the doing.

---

## Citations and further reading

**Trading-system post-mortems:**
- SEC Order 70694 (2013) — Knight Capital
- CFTC-SEC Joint Report (Sept 30 2010) — Findings Regarding the Market Events of May 6, 2010
- Kirilenko, Kyle, Samadi, Tuzun (2017) "The Flash Crash: High-Frequency Trading in an Electronic Market", *Journal of Finance* 72(3)
- SEC 34-72013 (2014) — Goldman Sachs options pricing incident
- SEC 34-83955 (2018) — BATS Direct Edge
- Robinhood Outage Postmortem, March 3 2020 (blog)
- Doug Seven, "Knightmare: A DevOps Cautionary Tale" (2014)

**Property-based testing & formal methods:**
- Claessen, Hughes (2000) "QuickCheck: A Lightweight Tool for Random Testing of Haskell Programs", ICFP
- Hughes (2007) "Software Testing with QuickCheck" (case study), PADL
- Papadakis, Sagonas (2011) "PropEr Testing of Concurrent Programs"
- David MacIver, Hypothesis documentation: hypothesis.readthedocs.io
- Lamport (2002) *Specifying Systems* (TLA+)
- Newcombe, Rath, Zhang, Munteanu, Brooker, Deardeuff (2015) "How AWS Uses Formal Methods", *CACM* 58(4)

**Distributed systems testing:**
- Kingsbury, Jepsen reports: jepsen.io/analyses
- Kingsbury (2014) "Knossos: an open-source linearizability checker"
- Wilson, "Testing Distributed Systems with Deterministic Simulation" StrangeLoop 2014 talk (FoundationDB)

**Reconciliation & SRE:**
- Harris (2003) *Trading and Exchanges: Market Microstructure for Practitioners*, ch. 25
- Beyer, Murphy, Rensin, Kawahara, Thorne (2018) *Site Reliability Workbook*, ch. 14

**Differential testing:**
- Yang, Chen, Eide, Regehr (2011) "Finding and Understanding Bugs in C Compilers", PLDI
- Bessey et al. (2010) "A Few Billion Lines of Code Later: Using Static Analysis to Find Bugs in the Real World" (Coverity), *CACM*

**Observability:**
- Majors, Fong-Jones, Miranda (2022) *Observability Engineering*, O'Reilly
- Charity Majors, "Observability — A 3-Year Retrospective" (Honeycomb blog, 2020)

**Chaos engineering:**
- Rosenthal, Jones (2020) *Chaos Engineering*, O'Reilly
- *Principles of Chaos Engineering*, principlesofchaos.org

**Antifragility:**
- Taleb (2012) *Antifragile: Things That Gain From Disorder*

---

*End of playbook. The user translates §8 into v2.2 §0 "discovery infrastructure" tickets — recon daemon (week 2) and PBT state machines (weeks 3-4) are the highest-leverage entries.*
