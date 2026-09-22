# 302 — LETF close-window re-test: PRE-REGISTRATION (DRAFT, NOT FROZEN)

**Status: DRAFT. Not frozen, not hashed, no trial registered.** Freezing consumes
registered trial 34 and raises the operative multiplicity bar for every other
hypothesis in the program, so it is Pierce's decision and not a side effect of
drafting. Review the arithmetic in section 8 first — a full pass still leaves this
family at 0.63–0.91 bps/day against a 5 bps/day target.

---

I have everything I need. Writing the document as my return value.

---

# PREREG-T00034 — LETF CLOSE-WINDOW REBALANCE-FLOW RE-TEST

**Class:** PRE-REGISTRATION, DRAFT — **NOT YET FROZEN, NOT YET REGISTERED.** No trial is consumed until §0 clears and the SHA-256 of the frozen text is written to `data/research/trial_registry.jsonl`.
**Drafted:** 2026-09-21 | **Author:** Claude (Opus 5) | **Repo:** `C:/Users/Pierce Lonergan/Documents/GitHub/momentum-x-research` (read-only this session)
**Trial slot:** T00034 (last registered id is T00033, verified in `data/research/trial_registry.jsonl`)
**Design row:** `letf_close_window_retest_if_minute_extended` in `data/research/design_queue.json` — rank 1 of 9, score 0.3294, verdict ADMISSIBLE (reproduced this session: `python scripts/epistemics.py plan --spec data/research/design_queue.json`)
**Parents:** doc 293 §triage row 4 · doc 295 §4 · doc 298 (CLOSED verdict; **no document file exists** — see §9) · doc 299 §2 · doc 300 · `docs/ATTEMPTS_LEDGER.md:30,52,61,119` · `docs/TARGET.md`
**Binding process rules:** 275, 276, 278, 290, 295, 296, 297, 299, 300
**Freeze hash:** `TO BE COMPUTED AT FREEZE` (sha256 of this file, byte-exact, recorded with `trial_registry.py`)

---

## 1. WHAT IS BEING CLAIMED

**The claim, in one falsifiable sentence:**

> Over the 2,690 ET sessions from 2016-01-05 to 2026-09-17, the cross-index-family long/short portfolio formed at 15:44:59 ET from the point-in-time LETF rebalance-flow-to-liquidity imbalance, **orthogonalised against the same-session partial-day return of the same underlying ETFs**, earns a positive mean return over the 15:45:00–15:59:59 ET window net of the frozen NBBO-plus-regulatory-fee cost model, with a stationary-block-bootstrap one-sided 95% lower bound on its annualised Sharpe strictly above **1.153711**.

**This test cannot establish that the family reaches the program target, and a full pass does not put it within reach.** The validated ceiling is 6.0–8.6% of the 21.07 bps requirement, i.e. **1.2642–1.8120 bps per window**, which at the frozen 50% gross deployment is **0.6321–0.9060 bps/day** against a standing target of **5 bps/day** — **12.6% to 18.1% of the bar, at the ceiling, gross of cost.** The family clears the ≥10%-of-requirement **build filter** (12.0–17.2%), which is the threshold for being worth *testing*. It does not clear the *target*, and no result of this test can make it clear the target. What passing would buy is one thing only: evidence that LETF rebalance flow carries close-window information **beyond same-day momentum** — a mechanism claim, in a mechanism class the program has closed three times (`market_structure_flow`, 3 of 25 archived closures).

**What passing would establish**

1. That the K-channel (the point-in-time AUM scale of the LETF complex) carries incremental content over the strong baseline of raw same-day return. This is the claim doc 293 demanded and doc 295 found unsatisfiable.
2. That the effect exceeds the 34-trial multiplicity bar on 2,690 sessions, which is a stronger statement than `p < 0.05` and is the only statement doc 275 still accepts.

**What passing would NOT establish**

1. **Not a certificate.** Doc 275: per-experiment significance is dead as a promotion criterion, and `TARGET.md §0b` gives the certification invariant `n = (2.8016·√252 / S)²` — certifying an annualised Sharpe of 1.91 against any bar at 80% power needs 2,152 sessions of *forward* data. This is a retrospective test on 2,690 historical sessions; it is a screen, not a certification.
2. **Not final.** Doc 276: any pass is **PROVISIONAL** until an independent adversarial fleet clears it. Doc 296 is the precedent — the first result in program history to pass every frozen gate was voided by the fleet because the runner used `h=1` where the prose said `h=21`.
3. **Not a deployment license.** Doc 297: `return ≡ deployment × turns × net-per-ticket`; deployment and turns are on the requirement side only and are sign-preserving. `TARGET.md §4.2` forbids any deployment or breadth increase until a per-ticket net edge has a CI excluding zero.
4. **Not a ceiling upgrade.** A ceiling is an upper bound on the effect, not a central estimate. Doc 293 recorded the **central** estimate for this family as **<5% of requirement**. The realised mean carries its own sampling error and can land anywhere below the ceiling, including below zero.

### 1b. The arithmetic, stated once, correctly

A reader recently got this backwards. **21.07 bps is the REQUIREMENT, not the edge.**

| quantity | value | what it is |
|---|---|---|
| **Requirement**, 0.1%/day era, 50% gross | **21.07 bps/window** | the mean the family must **produce**. Requirement-implied annualised Sharpe at sd 15.06 = **22.21**. Not an edge, not an estimate, not a measurement of anything |
| Requirement, current 5 bps/day target | **10.535 bps/window** | 21.07 / 2 |
| **Validated ceiling** | **6.0–8.6% of 21.07 = 1.2642–1.8120 bps/window** | an **UPPER BOUND** on the effect. Ceiling-implied annualised Sharpe **1.3326–1.9100** |
| Ceiling as % of the 5 bps/day requirement | **12.0% – 17.2%** | clears the ≥10% **build filter**; this is permission to test |
| Ceiling as % of the 0.1%/day requirement | 6.0% – 8.6% | fails the same filter — the verdict doc 298 recorded |
| **Ceiling in account terms at 50% gross** | **0.6321 – 0.9060 bps/day** | **12.6% – 18.1% of the 5 bps/day bar** |
| Buy-and-hold SPY, 2016–2026 (`TARGET.md §0`) | **+6.13 bps/day** | **123% of the bar.** The do-nothing baseline clears the target by itself; this family at its ceiling delivers 10–15% of what SPY delivered over the same span |

Every figure above reproduces exactly from the stated inputs. Verified this session: `21.07 × 0.060 = 1.2642`; `21.07 × 0.086 = 1.8120`; `1.2642 / 10.535 = 12.00%`; `1.8120 / 10.535 = 17.20%`; `(1.2642/15.06)·√252 = 1.3326`; `(1.8120/15.06)·√252 = 1.9100`; `(21.07/15.06)·√252 = 22.21`.

**One correction to the halving, disclosed because this document will be hashed and checked.** `21.07 = 20.00 + 1.07`, where `20.00 = 10 bps/day ÷ 0.50 gross`. The residual 1.07 bps is consistent with a round-trip cost allowance inside the requirement, but **no surviving source states the decomposition** (§9, P-6). If the requirement is `target/deployment + cost`, then halving the *target* does not halve the *cost*: the 5 bps/day requirement is `10.00 + 1.07 = 11.07 bps`, not 10.535, and the ceiling is **11.4% – 16.4%** of it rather than 12.0% – 17.2%. **No sign changes and the build filter is still cleared at both ends.** The frozen spec uses 10.535 (the halved figure, matching doc 299) as primary and reports 11.07 alongside it.

---

## 2. THE FROZEN SPEC

Anything not frozen here is a degree of freedom someone will exploit. Every row is an assertion in the tripwire test (§5); changing one is an automatic kill (§6).

### 2.1 Instruments

| parameter | frozen value | why |
|---|---|---|
| **What is TRADED** | the **underlying index/sector ETF**, never the LETF | The LETF is the *source* of the flow, not the vehicle. Measured this session: naked 15:45→15:59 ET return sd for 3× LETFs is **24.60–132.35 bps** (2026-08) and **43.20–96.08 bps** (2016-01), versus SPY at **8.52 / 21.84 bps**. The doc-298 window sd of **15.06 bps** is arithmetically incompatible with a naked LETF position and is consistent only with an index-ETF or hedged construction |
| **Index families (J = 9)** | SPX, NDX, RUT, DJIA, SEMI, FIN, BIO, GOLDMINERS, TREAS20 | one family per LETF complex with a liquid cash-index underlying |
| **LETF complex per family** | SPX: UPRO, SPXU, SPXL, SPXS, SSO, SDS · NDX: TQQQ, SQQQ, QLD, QID · RUT: TNA, TZA · DJIA: UDOW, SDOW · SEMI: SOXL, SOXS · FIN: FAS, FAZ · BIO: LABU, LABD · GOLDMINERS: NUGT, DUST · TREAS20: TMF, TMV | all 24 verified present in `minute_aggs` 2016-01 with complete close-window coverage (§2.7) |
| **Traded underlying per family** | SPY, QQQ, IWM, DIA, SOXX, XLF, XBI, GDX, TLT | SPY/QQQ/IWM/DIA are the only tier with a measured cost line (`TARGET.md §2b`). SOXX/XLF/XBI/GDX/TLT are **cost-unmeasured** — see the drop rule, §2.5 |
| **EXCLUDED, by name** | **UVXY, SVXY** | VIX-futures ETPs. No cash-index rebalance mechanism (their hedge is futures), and SVXY's stated leverage changed from −1x to −0.5x in Feb 2018. Excluding them is frozen so nobody adds them later to widen the cross-section |
| **EXCLUDED, by rule** | any LETF whose underlying is not one of the nine listed ETFs; any single-stock LETF; any LETF launched after 2016-01-04 is included **only from its own first session in `minute_aggs`**, with the family's J counted per session | survivorship and universe-membership audit (doc 273) |

### 2.2 The window, in ET, exactly

| parameter | frozen value |
|---|---|
| Signal information set closes | **15:44:59.999999 ET** — only `minute_aggs` rows with `ts_et <= 15:44:59` of session t, plus reference data dated `<= t−1` |
| Entry price | `open` of the minute bar whose `ts_et` minute-of-day is **945** (15:45:00–15:45:59 ET) |
| Exit price | `close` of the minute bar whose minute-of-day is **959** (15:59:00–15:59:59 ET) |
| Window return | `p_end / p_start − 1`, in bps, **15 bars, 15 minutes** |
| Minimum bars in window | **>= 10 of 15** per (ticker, session), else that ticker is **UNMEASURED** for that session and the family is dropped from that session's cross-section; the session stays in the sample with its reduced J |
| Auction-inclusive variant | the minute-960 (16:00 ET) bar is **SECONDARY, reported, no acceptance role.** Measured this session: 16:00-bar coverage in 2016-01 is 19/19 sessions for SPY/QQQ/IWM/DIA/GDX but **4/19 SMH, 6/19 SOXX, 14/19 XBI, 17/19 XLF, 17/19 TLT** — not uniformly measurable pre-2020, so it cannot carry an acceptance test |
| Timezone source | the `ts_et` column (`TIMESTAMP WITH TIME ZONE`) of `minute_aggs` **only**. `trades_v1.ts_et` is poisoned (doc 288, CI-guarded) and is not used anywhere in this design |
| Sessions per year for annualisation | **252** |

### 2.3 The flow construction from point-in-time shares outstanding

For LETF `i` in family `j`, with point-in-time stated leverage `L` and AUM `A`:

```
A_{i,t-1}     = SharesOutstanding_{i,t-1} × Close_{i,t-1}
K_{j,t-1}     = SUM over i in j of  L_{i,t-1} · (L_{i,t-1} - 1) · A_{i,t-1}
r_{j,t}^part  = UnderlyingETF_j close at 15:44:59 ET on t  /  its close on t-1  -  1
F_{j,t}       = K_{j,t-1} × r_{j,t}^part                      [dollars to be traded at the close]
ADV$_{j,t-1}  = trailing 20-session MEDIAN of (SUM of volume × close) over minutes 945..959
                for UnderlyingETF_j, computed on sessions <= t-1
S_{j,t}       = F_{j,t} / ADV$_{j,t-1}                        [the CHALLENGER signal, dimensionless]
```

`L(L−1)` is the standard result: a fund holding `L·A` of exposure must trade `L(L−1)·A·r` at the close, and **both bull and bear funds trade in the same direction as the index** (for L=+3, `L(L−1)=6`; for L=−3, `L(L−1)=12`).

| parameter | frozen value |
|---|---|
| Shares-outstanding field | **`share_class_shares_outstanding`**, never `weighted_shares_outstanding` |
| Reason this is frozen by name | Measured this session in `data/polygon_warehouse/reference/ticker_details.parquet`: for `type = 'ETF'`, `share_class_shares_outstanding` is populated **183 / 192 (95.3%)** while `weighted_shares_outstanding` is populated **0 / 192**. A runner reading the wrong field gets an all-null AUM panel, which silently degenerates to constant AUM — **which is exactly the doc-295 blocker, arriving silently**. Fixture assertion F-5 exists for this |
| Point-in-time rule | shares outstanding **as of session t−1**, never t. Price leg also t−1 close. `K` must be strictly in the information set before session t's return exists |
| Staleness rule | if the point-in-time source returns the same value for > **10** consecutive sessions for a given LETF, that LETF's AUM is flagged STALE for those sessions; if flagged AUM exceeds **20%** of the family's `K` on a session, the family is dropped from that session. Frozen because a monthly-stamped feed masquerading as daily would re-introduce constant AUM over short windows |
| Point-in-time leverage `L` | per (ticker, session), from issuer prospectus history | **`TO BE MEASURED BEFORE FREEZING`** — see §0, P-2. `L` enters as `L(L−1)`, so a wrong `L` is a squared scale error. Known changes to resolve: SOXL/SOXS, LABU/LABD, NUGT/DUST leverage-factor changes around 2020. This prerequisite appears in **no keystone list in `design_queue.json`** and is a gap this draft is recording |
| Signal sign convention | `S` is signed. Positive `S` = the complex must **buy** the underlying at the close |
| Missing inputs | the (family, session) cell is **dropped, never imputed, never zero-filled** |

### 2.4 Portfolio construction and the horizon

| parameter | frozen value |
|---|---|
| Unit of observation | **one session**, one row. Not one (family, session) row. See §4 — this is what makes nominal n equal effective n |
| Cross-sectional weights, challenger | each session, rank the available families by `S_{j,t}`; **long the top 3, short the bottom 3**, equal dollar weight `1/6` per name, gross 1.00, net 0.00. Sessions with fewer than **6** measurable families contribute **0.0 bps** and **stay in the sample** |
| Cross-sectional weights, baseline | identical machinery on `B_{j,t} = r_{j,t}^part` (§3) |
| **Primary endpoint** | the **orthogonalised** sleeve return, §3.2 |
| Horizon | **one window, same session.** Entry 15:45, exit 15:59, no overnight, no multi-day, **h = 1 window and the number 1 appears nowhere else in this spec** |
| Turns | **1 round trip per session** |
| Deployment | **50% of equity gross** (10 families × 5% cap, `TARGET.md §4.1`). Requirement-side only, doc 297 |
| Rebalance | daily, from scratch; no position carried between sessions |

### 2.5 The cost model — true NBBO plus regulatory fees, NOT Roll

| parameter | frozen value |
|---|---|
| Estimator | **true NBBO quoted spread** in the 15:45–16:00 ET window, per traded ETF, plus the three regulatory fees. `scripts/true_nbbo_cost.py` is the instrument; its `SAMPLE_TIMES_ET` already contains `"15:50"` and its `TIERS["index_etf"]` is `["SPY","QQQ","IWM","DIA"]` |
| **Roll is banned** | Roll is measured at **0.56× truth on thin names, 3.5× truth on SPY**, and returns a non-positive (undefined) estimate on **29.5% of 463,183 liquid ticker-days**; one doc-298 generator coded a failed estimate as 0.0 bps and carried the zeros into a median (`TARGET.md §2b`). No Roll figure may appear in this test |
| Round trip | **one full quoted spread.** There is **no midpoint or peg order type on this broker** (`TARGET.md §2b`) — no "trade at mid" assumption is admissible |
| Regulatory fees | SEC `$0.0000206 × value` on sells; TAF `$0.000195/share` on sells; CAT `$0.000003/share` both sides. Index-ETF tier adds **+0.21 bps** to the round trip |
| Paper-fill correction | **not applied** — the cost comes from quotes, not from paper fills. (The paper account understates regulatory fees by 1.44×; that correction applies only to paper-activity-derived costs) |
| Sleeve cost per session | `SUM over the 6 traded names of (weight_i × all_in_round_trip_bps_i)`. At equal `1/6` weights and a common `c`, the sleeve cost is exactly `c` bps/session |
| **The cost constant `c`** | **`TO BE MEASURED BEFORE FREEZING`** — see §0, P-4. This is the most decisive unmeasured number in the document; §8 is largely about it |
| Pre-2020 cost | the forward-measured close-window constant is applied uniformly and is **optimistic for 2016–2019**, when spreads were wider. Declared now: a PASS driven by the pre-2020 subsample is inadmissible, which the both-halves-positive clause (§6) enforces |
| Is the ceiling gross or net? | **no surviving source states it.** Pre-declared conservative default: the 1.2642–1.8120 bps ceiling is treated as **GROSS**, so the net ceiling is `1.2642 − c` to `1.8120 − c` |

### 2.6 Sample span and denominators

| parameter | frozen value |
|---|---|
| Warehouse | `data/polygon_warehouse/minute_aggs/year=YYYY/month=MM/data.parquet` in the sibling repo `Documents/GitHub/momentum-x`. Verified this session: 11 year partitions 2016..2026; columns `ticker, volume, open, close, high, low, window_start, ts_utc, ts_et, year, month, day` |
| Stated warehouse contents | **2,691 distinct ET sessions, 2016-01-04 to 2026-09-17, 3,943,485,895 minute bars**, verified by DuckDB on `ts_et`, landed 2026-09-21 |
| **Frozen n** | **2,690 sessions, 2016-01-05 .. 2026-09-17.** The first session (2016-01-04) carries no prior-session close and therefore no `r^part` or `K_{t−1}`. `design_queue.json` registers `n_obs = 2690` without stating the reason; this reconciliation is to be confirmed at freeze (§0, P-5). One session moves the bar from 1.153711 to 1.153500 — immaterial |
| Coverage-integrity clause | if UNMEASURED (family, session) cells exceed **10%** of `9 × 2,690 = 24,210`, the test **cannot PASS** (verdict `BLOCKED-COVERAGE`) until the holes are documented. Holes must be **counted and reported**, never booked as 0 bps (doc 278) |
| Trailing-window warm-up | the 20-session `ADV$` median means the first 20 sessions of the span have no `ADV$`; those sessions are **excluded and counted**, so the acceptance sample is **at most 2,670** and the exact figure is reported before the statistic |

### 2.7 Coverage verified this session (not assumed)

Measured directly from the warehouse, second moments and coverage only:

- **Close-window session coverage, all 24 LETFs:** 19/19 sessions in 2016-01, 20/20 in 2026-08.
- **Close-window minute fill, 2016-01:** 285/285 (100%) for 18 of 24 LETFs; lowest **SOXS 195/285 (68.4%)**, then TMF 250, SOXL 279, LABD 281, UDOW 281, TMV 282.
- **Close-window minute fill, 2026-08:** 300/300 for 11 of 24; lowest LABU 254, FAS 263.
- **Traded underlyings:** 285/285 in 2016-01 for SPY, QQQ, IWM, DIA, GDX, XLF, XBI, TLT, SMH; **SOXX 280/285**. 300/300 for all ten in 2026-08.

The 15-minute close window is **measurable at both ends of the eleven-year span.** The `intraday_tape_pre_2020` keystone is genuinely satisfied for this design's price inputs. It is **not** satisfied for this design's AUM inputs (§0).

### 2.8 Every parameter that could be varied, and its frozen value

Enumerated so that variation is detectable: `J=9`; nine named families; nine named underlyings; UVXY/SVXY excluded; window `945..959`; signal cut-off `15:44:59`; entry `open(945)`; exit `close(959)`; min 10 of 15 bars; `L(L−1)` weighting; `share_class_shares_outstanding`; `t−1` for shares and price; 10-session staleness limit; 20% flagged-AUM family-drop; `ADV$` = 20-session median of minutes 945..959 dollar volume; top-3 long / bottom-3 short; `1/6` equal weights; gross 1.00 / net 0.00; min 6 families per session; orthogonalisation against the baseline weight vector; `w_perp` gross floor 0.10; 0.0 bps for degenerate sessions with the session retained; cost = full quoted NBBO + SEC/TAF/CAT, one spread per round trip; 50% deployment; 1 turn/session; span 2016-01-05..2026-09-17; n=2,690 pre-warm-up; 20-session warm-up excluded; annualisation 252; bootstrap = stationary Politis–Romano, expected block length **5**, `B = 10,000`, seed **34**, percentile method, one-sided 95%; bar **1.153711**; rank-calibration abort threshold Spearman **0.98**; coverage-integrity threshold **10%**.

---

## 3. THE STRONG BASELINE AND THE RANK-CALIBRATION

Doc 290's rule: rank-calibrate against the **strong** baseline; a weak baseline manufactures lift. Doc 290's own kNN "lift" was a weak-baseline artifact, and doc 293 named this family's specific hazard: **it is intraday momentum in disguise otherwise.**

### 3.1 The strong baseline, named explicitly

> **The strong baseline is the raw same-session partial-day return of the same nine underlying ETFs, `B_{j,t} = r_{j,t}^part` (prior close → 15:44:59 ET), run through the identical ranking machinery, the identical window, the identical cost model, on the identical sessions.**

It is not zero, not a random signal, not an unconditional long, and not a shuffled control. It is intraday momentum, priced exactly as the challenger is priced. All three are reported: baseline, challenger, and the orthogonalised residual.

### 3.2 Why the calibration was unsatisfiable, and what breaks the degeneracy

Doc 295 §4 ruled this family BLOCKED-AT-$0 because under a **constant-AUM** proxy `K_j` is a constant `k_j`, so

```
S_{j,t} = k_j · r_{j,t}^part / ADV$_{j,t-1}
```

is a strictly increasing transform of `r_{j,t}^part` for every family. Ranks coincide identically. The mandatory flow-vs-raw-return separation is **not merely weak — it is arithmetically unsatisfiable**, and no sample size fixes it.

**What point-in-time shares outstanding changes.** `K_{j,t−1}` becomes a per-family, per-session quantity, so

```
S_{j,t} = [K_{j,t-1} / ADV$_{j,t-1}] × r_{j,t}^part
```

and the **cross-sectional ranking** of `S` across the nine families is no longer a function of any single return. Two families with identical `r^part` receive different ranks when their complexes carry different AUM relative to their underlying's close-window liquidity, and the ordering can invert between families as `K` moves — TQQQ's complex and TNA's complex have differed by more than an order of magnitude in AUM over this span. **The degeneracy is broken in the cross-section, not in the time series.**

**Stated plainly, because it limits the claim:** in a pure *time-series* design the degeneracy would survive. `K_{j,t−1} > 0` always, so `sign(S_{j,t}) = sign(r_{j,t}^part)` on every date for every family; only the magnitude differs. A single-family directional test could therefore only ever test magnitude-conditioning. **This is why the primary endpoint is cross-sectional.** A cross-sectional rank among nine families depends on the joint configuration of `K/ADV$` and `r^part`, which is not a monotone transform of any one return.

### 3.3 The frozen rank-calibration procedure

1. **Degeneracy abort, computed before any return statistic.** Pool all `(family, session)` cells and compute Spearman `rho(rank(S), rank(B))` within sessions, then average across sessions. **If the pooled within-session Spearman exceeds 0.98, the test is declared `BLOCKED-DEGENERATE`, no return statistic is computed, no trial is consumed, and the doc-295 ruling stands unchanged.** The 0.98 threshold is frozen here.
2. **Dispersion floor, also pre-return.** Report the cross-sectional coefficient of variation of `K_{j,t−1}/ADV$_{j,t−1}`, pooled and by calendar year. If its median across sessions is below **0.10**, the K-channel has no usable variation and the verdict is `BLOCKED-DEGENERATE`.
3. **Orthogonalisation — the primary endpoint.** Per session, with challenger weights `w_c` and baseline weights `w_b`:

   ```
   w_perp = w_c - (w_c . w_b / w_b . w_b) · w_b        [w_b . w_b > 0 by construction]
   if gross(w_perp) < 0.10:  session contributes 0.0 bps and STAYS in the sample
   else:                     rescale w_perp to gross 1.00
   primary return_t = w_perp . (close-window returns)_t  -  cost_t
   ```

   `w_perp` is the K-channel with the raw-return channel projected out. If this clears the bar, the flow carries content beyond same-day momentum. If it does not, **the family is intraday momentum in disguise and dies on doc 293's own stated condition.**
4. **Reported, no acceptance role:** the baseline sleeve, the raw challenger sleeve, the challenger-minus-baseline difference, the count and fraction of zero-contribution degenerate sessions, and every one of the above by calendar year.
5. **Ban.** The acceptance metric may not be switched from the orthogonalised sleeve to the raw challenger after any return statistic is seen. Doc 296's precedent — a metric substituted after unblinding is a void, not a finding.

---

## 4. POWER

Doc 275: **pre-registered acceptance-test power is mandatory.** Doc 278: nominal n is not effective n.

### 4.1 The acceptance test

Primary statistic: the **annualised Sharpe** of the primary per-session net return series. It is in Sharpe units because the multiplicity bar is in Sharpe units. The mean in bps/window, the implied bps/day at 50% gross, and the implied % of the 5 bps/day bar are reported alongside, every time, with no exceptions.

### 4.2 Effective n, with the clustering correction shown

**Number of independent session-day clusters: 2,690** (at most 2,670 after the 20-session `ADV$` warm-up; the exact figure is reported before the statistic). **Not** the 24,210 `(family, session)` rows.

This is a design decision, not an accident. The row-level alternative is rejected here so nobody adopts it later:

| unit | nominal n | m | rho | design effect `1+(m−1)rho` | n_eff | SE inflation |
|---|---|---|---|---|---|---|
| (family, session) row | 24,210 | 9 | 0.09 | 1.720 | 14,076 | 1.31× |
| (family, session) row | 24,210 | 9 | 0.20 | 2.600 | 9,312 | 1.61× |
| (family, session) row | 24,210 | 9 | 0.35 | 3.800 | 6,371 | 1.95× |
| **session sleeve (FROZEN)** | **2,690** | **1** | **0.00** | **1.000** | **2,690** | **1.00×** |

Doc 278's measured within-day intraclass rho of 0.09–0.35 inflates SE by roughly 2.5×; at `m = 9` a 2.5× inflation corresponds to `rho = 0.656`. **By aggregating the cross-section into one portfolio return per session before any statistic is computed, nominal n equals effective n by construction and the 2.5× inflation cannot bite.** This is also the only reading under which `design_queue.json`'s registered `cluster_size = 1, icc = 0.0` — and therefore the planner's `n_eff = 2690.0` and `MDE = 1.6689` — are honest. Had the design been specified at row level, the queue row would be wrong and the design should not be run on its numbers.

**Residual serial dependence** is handled by the interval, not by a correction factor: primary interval = **stationary (Politis–Romano) block bootstrap, expected block length 5 sessions, B = 10,000, seed 34, percentile method, one-sided 95%**. Pre-declared: the ratio of block-bootstrap SE to i.i.d.-resample SE is **reported as a measured design effect**, and the implied cluster count `2,690 / ratio²` is reported with it. The block bootstrap is primary whatever that ratio turns out to be — it is not selected after seeing it.

### 4.3 MDE and power, at the registered parameters

```
Sharpe SE:  se        = sqrt(252 / 2690)            = 0.30607
tempered:   sigma_e   = se / sqrt(omega), omega=0.25 = 0.61214
bar:        bar(34 trials, n=2690)                   = 1.153711
MDE(80%):   bar + 0.8416 * sigma_e                   = 1.6689  annualised Sharpe
            = 1.6689 * 15.06 / sqrt(252)             = 1.5833  bps/window
untempered (omega=1.0): MDE = 1.4113 Sharpe          = 1.3389  bps/window
```

Reproduced this session against `scripts/epistemics.py plan`, which reports `mde: 1.6689`, `bar_before: 1.15`, `bar_after: 1.1537` for this exact design row.

**The finding that matters, and it is not comfortable:**

| true effect | annualised Sharpe | power vs the bar, omega=0.25 | power vs the bar, omega=1.0 |
|---|---|---|---|
| ceiling, pessimistic end (1.2642 bps) | 1.3326 | **61.5%** | 72.1% |
| **tempered MDE (1.5833 bps)** | **1.6689** | **80.0%** | 95.4% |
| ceiling, optimistic end (1.8120 bps) | 1.9100 | **89.2%** | 99.3% |
| the registered prior mean | 1.0000 | 40.1% | 30.8% |

**The tempered MDE of 1.6689 falls INSIDE the ceiling band [1.3326, 1.9100].** Only the top **41.8%** of the family's own validated ceiling band is detectable at 80% power against the multiplicity bar; untempered, the top 86.4%. At the pessimistic end of its own ceiling the design runs at **61.5% power** — under-powered, by the program's standard, against the bar it must clear. And the ceiling is an upper bound, so the true effect is more likely below the band than inside it.

**Why doc 298 called this design "well-powered" (3.3–15.1× MDE) and was measuring something else.** Power against **zero** is a different quantity. At 80% power, one-sided 5%, sd 15.06 bps, the sessions required to detect a mean of 1.2642 bps is **878**, and for 1.8120 bps it is **428**. Against the 2,690 available that is **3.06×** and **6.28×** oversampled. **That is what "well-powered" means, and doc 275 retired it as a promotion criterion.** Against the bar the same design is 61.5%–89.2%. Both statements are true; only the second is admissible.

### 4.4 What must be measured before the power section is final

The entire power calculation above scales with the window sd of the **primary orthogonalised sleeve**, and the 15.06 bps figure is neither that sleeve's sd nor artifacted anywhere (§9, P-1). Measured this session: SPY's own close-window sd moves **2.56×** between 2016-01 (21.84 bps) and 2026-08 (8.52 bps); QQQ's moves 2.01×. Doc 299 already flagged that the 15.06 figure was measured on 2024–2026 and that LETF rebalance mechanics are not stationary over 2016–2026. **`TO BE MEASURED BEFORE FREEZING`** (§0, P-3): the primary sleeve's pooled sd and its by-year sd, on the full 2,690 sessions, with the K-channel replaced by a **sign-randomised placebo** so the variance is calibrated without any effect statistic being computed.

---

## 5. THE EXECUTABLE FIXTURE

Doc 296: **a prereg in prose is not a prereg.** The first result in program history to pass every frozen gate was voided because the runner evaluated `h = 1` where the spec said `h = 21` in words. The runner must reproduce a planted answer on synthetic data before it is permitted to open a single warehouse parquet.

### 5.1 Fixture shape

Written by `scripts/_t34_fixture.py --build` to a temp directory, in the **exact schemas of the real inputs**, so a runner cannot pass the fixture and then fail on real column names.

**`fixture_minute_aggs.parquet`** — columns `ticker, volume, open, close, high, low, window_start, ts_utc, ts_et, year, month, day` (byte-identical schema to `minute_aggs`):

- **3 synthetic families** A, B, C; each with 1 underlying (`UA`, `UB`, `UC`) and 2 LETFs (`XA3`/`XA3S`, etc.) = **9 tickers**.
- **400 synthetic sessions**, consecutive weekdays from 2020-01-01.
- **45 minute bars per (ticker, session)**: minutes 930..944 (the 15:30–15:44 **decoy block**), 945..959 (the real window), and 840..854 (14:00–14:14, the **zero-effect decoy block**).
- Total rows: `9 × 400 × 45 = 162,000`.

**`fixture_shares_outstanding.parquet`** — columns `ticker, asof_date, share_class_shares_outstanding, weighted_shares_outstanding`:

- 6 LETF tickers × 400 dates = **2,400 rows**.
- `share_class_shares_outstanding` populated on every row.
- **`weighted_shares_outstanding` is NULL on every row** — deliberately, to reproduce the on-disk reality (0/192 ETFs populated).

### 5.2 The planted answer

- `r_{j,t}^part ~ N(0, 80 bps)`, i.i.d. across families and sessions.
- `K_{j,t-1}` lognormal with `sd(log K) = 0.60`, generated **independently of `r`**, so the cross-sectional rank of `S` is not a monotone transform of any single return.
- The close-window return per family is `w_{j,t} = beta · (S_{j,t} − median_t S) + eps`, `eps ~ N(0, 15 bps)`.
- **The generator then runs the frozen §2/§3 pipeline once**, obtains the realised 400-session primary sleeve series, and — because the sleeve return is linear in the family close-window returns at fixed weights — **solves in closed form for the affine transform `(a, b)` on `w` such that the sleeve's SAMPLE mean is exactly 4.0000 bps and its SAMPLE sd is exactly 15.0600 bps.** The fixture is therefore fully deterministic and the assertions can be exact rather than statistical.

**Planted answer, variant PASS:**

```
sleeve sample mean   = 4.0000 bps/window      (exact)
sleeve sample sd     = 15.0600 bps            (exact)
annualised Sharpe    = (4.0000/15.0600)*sqrt(252) = 4.21543
n sessions           = 400
expected block-bootstrap one-sided 95% LB  ~= 4.21543 - 1.6449*sqrt(252/400) = 2.9098
                        [PINNED to the seeded value at fixture build, then asserted to 1e-6]
required verdict     = PASS   (LB 2.9098 > bar 1.153711)
```

**Planted answer, variant KILL:** identical generator, sleeve sample mean rescaled to exactly **0.5000 bps** → Sharpe **0.52693**, LB ≈ **−0.7787**, **required verdict KILL.** This variant exists because doc 300's rule applies to the fixture too: *a test can assert a true value and still pin nothing.* A fixture that only ever exercises the PASS branch does not pin that the acceptance rule is wired in the right direction.

### 5.3 The assertions, each with the specific defect it catches

The runner exits non-zero and **refuses to open any file under `data/polygon_warehouse/`** unless all nine pass.

| # | assertion | if the runner has this defect, it returns | catches |
|---|---|---|---|
| **F-1** | reported mean `== 4.0000 bps ± 1e-4`, sd `== 15.0600 ± 1e-4`, `n_sessions == 400`, `verdict == PASS` | — | the endpoint is not the one the spec defines |
| **F-2** | the reported window is exactly minutes `945..959`. The fixture plants mean **−4.0000 bps** in minutes 930..944 and **0.0000 bps** in minutes 840..854 | **−4.0000** → used 15:30–15:44; **0.0000** → used the 14:00 decoy | **the doc-296 defect, made impossible.** A window stated only in words cannot be silently swapped |
| **F-3** | on a variant with `K_{j,t}` **constant** across all j,t, the runner must emit `BLOCKED-DEGENERATE` with the pooled within-session Spearman `>= 0.98`, and must emit **no mean at all** | emits a mean → the §3.3 degeneracy guard is absent | the doc-295 blocker returning silently under a constant-AUM proxy |
| **F-4** | on a variant where the planted effect is driven by `K` as of **session t** rather than t−1, the runner must return `0.0000 bps ± 1e-4` | returns **4.0000** → it read same-session shares outstanding | look-ahead in the AUM leg |
| **F-5** | the runner must return 4.0000 bps despite `weighted_shares_outstanding` being NULL on all 2,400 rows | `BLOCKED-DEGENERATE` or an all-null AUM panel → it read the wrong field | the **measured** on-disk trap: `weighted_shares_outstanding` populated 0/192 for ETFs, `share_class_shares_outstanding` 183/192 |
| **F-6** | with the cost model switched on at exactly **2.0000 bps** round trip, the reported NET mean must be `2.0000 ± 1e-4` | **3.0000** → charged one leg; **0.0000** → charged the spread twice | cost applied per leg instead of per round trip, or double-counted |
| **F-7** | on a variant whose 400 sessions are each of 40 distinct sessions repeated 10×, the runner must report `n_clusters == 40` and a CI at least **3.16×** (`sqrt(10)`) wider than the i.i.d.-resample CI | `n_clusters == 400` or an unchanged CI → nominal n is being used as effective n | **doc 278 denominator honesty**, asserted rather than promised |
| **F-8** | the KILL variant (mean 0.5000 bps) must return `verdict == KILL` | returns PASS → the acceptance comparison is inverted or the bar is not applied | an acceptance rule that cannot fail (doc 299: `severity` = 0.9703 only if the test can fail) |
| **F-9** | the runner must echo back, and the fixture must assert byte-equality of, the SHA-256 of the frozen spec file and every constant in §2.8 | any mismatch | a re-tuned constant reaching real data (§6) |

`scripts/_t34_fixture.py --verify` returns `PASS` / `FAIL:<assertion-id>` and is the gate the runner calls on startup. The fixture, the runner, and this document are frozen in the same commit and hashed together.

---

## 6. ACCEPT / KILL

Both numeric, both stated in advance, with a death date.

### 6.1 PASS requires ALL FIVE

1. **Primary bar.** Stationary block-bootstrap (L=5, B=10,000, seed 34, percentile) **one-sided 95% lower bound on the annualised Sharpe of the primary orthogonalised net sleeve > 1.153711.**
2. **Return-space floor.** Point-estimate mean **>= 1.0000 bps/window net** — i.e. `>= 9.5%` of the 10.535 bps requirement, the ≥10% build filter re-applied to the realised mean rather than to the ceiling, with the rounding stated. A pass on Sharpe with a mean below 1.0000 bps is reported as `PASS-STATISTICAL / FAIL-ARITHMETIC` and **does not** advance the family.
3. **Both chronological halves positive.** Split the acceptance sessions in date order into the first ⌈n/2⌉ and the rest; the mean must be **> 0** in each. This is also the guard against a PASS carried by the cost-optimistic pre-2020 subsample (§2.5).
4. **Calibration satisfied.** Pooled within-session Spearman `rho(rank(S), rank(B)) < 0.98` **and** median cross-sectional CV of `K/ADV$` `>= 0.10` — both computed and reported before any return statistic.
5. **Coverage integrity.** UNMEASURED `(family, session)` cells `<= 10%` of 24,210, with the count reported.

Any PASS is **PROVISIONAL** until an independent adversarial fleet clears it (doc 276, doc 296). The word "certified" may not be used.

### 6.2 KILL — any one of these, and the family returns to CLOSED

1. Bootstrap lower bound `<= 1.153711`. **This is the expected outcome; the program is 0-for-60+ on gated hypotheses and 0-for-34 on registered trials.**
2. Point-estimate net mean `<= 0.0000 bps/window`.
3. Either chronological half negative.
4. `BLOCKED-DEGENERATE` on the §3.3 calibration → doc 295's ruling stands, verbatim and unchanged.
5. `BLOCKED-COVERAGE` at freeze + 30 days.
6. Not REPORTED by the death date.

### 6.3 Death date

**2026-10-31.** If T00034 is not in state REPORTED by 2026-10-31, the trial is recorded `ABANDONED` (doc-299 closure class, second group — evidence about us, not about the market) with the keystone it was missing named. **No extension, no re-tune, no "one more month."**

Interlock, so the toll is not paid for nothing: if the §0 prerequisites are not all satisfied by **2026-10-05**, this draft is **VOID and never registered**, the trial slot is not consumed, the bar does not move, and the family stays CLOSED on doc 298's arithmetic.

### 6.4 RE-TUNE EQUALS KILL

Changing **any** value in §2, §3, §4 or §6 after freeze is an **automatic kill of this gate**, not an amendment. A different window, a different J, a different shares-outstanding field, a different cost constant, a different bar, a different bootstrap, a different endpoint, a different span — each is a **different pre-registration**, needing its own freeze, its own hash, and its own trial number, which raises the bar again for everyone. Specifically prohibited: swapping the primary orthogonalised endpoint for the raw challenger after seeing a return statistic; extending the span past 2026-09-17; re-deriving the 0.98 or 0.10 calibration thresholds; substituting Roll for NBBO anywhere; adding UVXY, SVXY, or any tenth family.

---

## 7. WHAT THIS COSTS THE PROGRAM

Registering this consumes **trial 34**, and the promotion bar is computed **from** the trial count. Reproduced this session from `scripts/epistemics.py plan` and `src/epistemics/eig.py`:

| quantity | value |
|---|---|
| `bar_before` — operative bar at 33 trials, n=2,690 | **1.150006** |
| `bar_after` — operative bar at 34 trials, n=2,690 | **1.153711** |
| **`toll_sharpe`** — the bar increment levied on every other hypothesis in the queue | **+0.003705 annualised Sharpe** |
| `toll_p_cert` — the certification-probability toll | **0.0058** |
| `p_false_positive` at omega=0.25, 34 trials | **0.029735** (invariant in n — doc 299, as corrected by doc 300) |
| EIG | 0.3367 nats (0.0281 nats/day over 12 cost-days) |
| severity | 0.9703 — the test can fail, so it can corroborate |
| `p_cert` | 0.428841 |
| informativeness | 14.422 |
| verdict | ADMISSIBLE, rank 1 of 9 |

**The toll, stated plainly.** Every other hypothesis in the queue must now clear **1.153711** instead of **1.150006**. On the same 2,690-session sample the toll costs 0.0037 Sharpe; on the shorter samples the other queue rows actually have, the increment is larger — `anomaly_vol_scale_monetisation` at n_eff 866 pays **+0.0065** (2.0268 → 2.0334) and `sevp_event_vol_carry` at n_eff 686 pays a comparable amount. **Registering this trial makes `sevp_event_vol_carry`, the ledger's standing BUILD-NEXT, measurably harder to promote.** That is the real price, and it is paid at registration, not at reporting.

**The toll is also the reason for the §0 / §6.3 interlock.** Doc 299's rule is that a `CEREMONIAL` or `UNDISCRIMINATING` design must not be registered, because it cannot teach and it raises the bar anyway. The prerequisite phase (§0) computes coverage, variance and cost only — no effect statistic — and therefore consumes **no trial**. If the prerequisites fail, the family dies at **zero multiplicity cost**. Registration happens at freeze, after the prerequisites, and not before.

**And one item the program should charge to itself.** This is the 34th registered trial against a base rate of **0 certified edges in 60+ gated hypotheses**, in a mechanism class already carrying 3 of 25 archived closures. `p_cert = 0.4288` is computed against a **declared prior** of mean Sharpe 1.0, sd 0.6 — a judgement, not a measurement, and `design_queue.json` says so in its own header. Against the program's measured base-rate prior of 0.0, `p_cert` would be materially lower.

---

## 8. WHAT WOULD MAKE THIS NOT WORTH RUNNING

The honest case against, in descending order of force.

**8.1 A full pass leaves the family at 0.63–0.91 bps/day, and the do-nothing baseline is 6.13.** The arithmetic is §1b and it is not in dispute: at the ceiling, at 50% gross, this family delivers **0.6321–0.9060 bps/day = 12.6%–18.1% of the 5 bps/day bar**. Buy-and-hold SPY returned **+6.13 bps/day** over the same 2016–2026 span — **123% of the bar**, from an allocation decision requiring no research at all. **A perfect result here is worth one-seventh to one-tenth of doing nothing.** Doc 297's identity is the reason no amount of sizing repairs this: deployment and turns are sign-preserving multipliers on the requirement side and cannot manufacture the remaining 82%–87%.

**8.2 The cost constant can kill it before the test runs, and it is not yet measured.** Under the frozen conservative default that the ceiling is gross (§2.5), the net ceiling is `ceiling − c`:

| all-in close-window round trip `c` | net ceiling, bps/window | net, bps/day at 50% gross | verdict |
|---|---|---|---|
| 0.72 bps — the measured index-ETF **pooled-clock** figure | +0.5442 to +1.0920 | +0.272 to +0.546 | survives; **5.4%–10.9% of the bar** |
| 1.26 bps | +0.0042 to +0.5520 | +0.002 to +0.276 | pessimistic end is **zero** |
| 1.81 bps | **−0.5458** to +0.0020 | negative to zero | **dead at the pessimistic end** |
| 2.23 bps — 0.51 × the ~3.96× close-window clock multiplier, + 0.21 fees | **−0.9658 to −0.4180** | negative at both ends | **dead before the test runs** |

`TARGET.md §2b` records that **trading near the close costs about four times trading mid-afternoon**: median quoted spread **1.62 bps at 14:30–15:30 versus 6.42 bps at 15:50**, a ratio of 3.96×. If anything close to that multiplier applies to the index-ETF tier's 0.51 bps, the all-in close-window round trip lands near **2.23 bps — above the optimistic end of the family's entire validated ceiling.** The multiplier was measured pooled across all five tiers and is plausibly dominated by thin names, so it is not transferable; that is exactly why P-4 is a blocking prerequisite and not a footnote. **This design trades the single most expensive fifteen minutes of the session, to harvest an effect bounded above by 1.81 bps.**

**8.3 The AUM keystone may not be satisfied for these instruments.** Doc 298 recorded point-in-time shares outstanding as "served and moves"; `design_queue.json` records **"DEPTH UNVERIFIED"**. Measured this session: nothing on disk is point-in-time. `reference/ticker_details.parquet` is a single 2,808-row snapshot; `scripts/polygon_ticker_details_v2.py` passes no `date=` parameter and never did; the fundamentals cache is quarterly and carries `weighted_shares_outstanding: null` for TQQQ, SOXL and TNA; and only **3 of 22** probed LETF tickers appear in the reference table at all. LETF shares outstanding change **daily** through creation and redemption — a quarterly or monthly-stamped source does not merely add noise, it **restores the doc-295 degeneracy over any short window** and the whole test collapses back to `BLOCKED`. Assertion F-3 exists so that collapse is loud rather than silent, but a loud collapse after registration still costs trial 34.

**8.4 Point-in-time leverage is a second unsatisfied data prerequisite that no keystone list mentions.** The signal scales as `L(L−1)`, so a stale `L` is a squared error. SOXL/SOXS, LABU/LABD and NUGT/DUST all changed stated leverage factors around 2020, and SVXY changed in 2018. `design_queue.json` lists `point_in_time_shares_outstanding` as the family's keystone and **does not list leverage at all.** A family whose closure record was already found wrong once in exactly this way — doc 299 records that the LETF stepping-stone originally omitted the intraday-data keystone, "wrong in exactly the way the module exists to prevent" — is a family whose keystone list should be assumed incomplete until checked.

**8.5 Two of the three load-bearing numbers have no artifact.** There is **no doc 298 file in either repository**, the changelog stops at doc 297, and `data/research/doc298/` does not exist. The **15.06 bps window sd**, the **3.3–15.1× MDE** and the **6.0–8.6% ceiling** survive only as prose in `ATTEMPTS_LEDGER.md:30` and in stepping-stone `SS0019` — whose `effect`, `ci` and `n_obs` fields are all **null**. Doc 300's rule is that no field is filled unless the source states it, and doc 301 records that a day earlier the author of that archive had been caught fabricating measurements to satisfy a validator. **The entire power section of this document rests on an sd that cannot be traced to an artifact, and that measurement showed 2.56× regime variation in the one adjacent quantity that could be checked.**

**8.6 The mechanism class is crowded and the prior is a judgement.** `market_structure_flow` carries 3 of 25 archived closures. The `prior_mean = 1.0` that produces `p_cert = 0.4288` is a declared judgement set deliberately **below** the ceiling-implied 1.33–1.91, and `design_queue.json`'s own header says these priors "are DECLARED JUDGEMENTS, NOT MEASUREMENTS." The program's measured base rate across 35 families is **0.0**.

**8.7 Under-powered where it matters.** §4.3: at the pessimistic end of its own ceiling the design runs **61.5%** power against the bar, and only the top **41.8%** of the ceiling band is detectable at 80%. Doc 275's standing lesson from the fleet certification was precisely to **pre-register acceptance-test POWER** — and honest pre-registration here says the most likely region of the effect space is the region this design cannot resolve.

**8.8 The one real argument for running it anyway.** It is the **top-ranked admissible design in the queue** (score 0.3294, informativeness 14.422, severity 0.9703 — roughly 6× the informativeness of the next row at 2.312), it is **$0 in spend**, it is the only family the retro-validation engine revived on its own rather than by someone remembering, and it is the **first design in the queue whose blocking keystone actually landed**. Doc 300's governance stop rule is explicit: the harness is frozen, **"the bottleneck is the alpha pipeline, and it gets the attention."** A queue whose top-ranked admissible design is never run is not a queue. The counter-argument to 8.1 through 8.7 is not that they are wrong — they are all correct — but that **12 days and one trial slot to close the last mechanically revivable family in the archive, cleanly and on a frozen contract, is cheap relative to leaving it open forever as a maybe.** That is the decision, and §0 is where it gets made on measurements rather than on this paragraph.

---

## 0. PRE-FREEZE PREREQUISITES (blocking; no trial consumed)

Each returns a number or a `BLOCKED`. None computes an effect statistic. Deadline **2026-10-05**; any failure voids the draft unregistered (§6.3).

| # | prerequisite | pass condition | status |
|---|---|---|---|
| **P-1** | Recover or discard doc 298's **15.06 bps** sd, **3.3–15.1×** MDE, and **6.0–8.6%** ceiling. No doc-298 file, no changelog entry, no `data/research/doc298/` artifact exists | each figure traced to an artifact, or replaced by a re-measurement, or **struck** | **`TO BE MEASURED BEFORE FREEZING`** |
| **P-2** | Point-in-time **leverage factors** `L_{i,t}` for all 24 LETFs, 2016-01-04..2026-09-17, from issuer prospectus history | complete panel, no gaps, no imputation | **`TO BE MEASURED BEFORE FREEZING`** |
| **P-3** | Primary-sleeve **window sd**, pooled and by year, on all 2,690 sessions, with the K-channel **sign-randomised** so no effect statistic is produced | sd measured; §4 power table recomputed on it | **`TO BE MEASURED BEFORE FREEZING`** |
| **P-4** | **Index-ETF × 15:45–16:00 ET all-in round trip.** Run `scripts/true_nbbo_cost.py` on the `index_etf` tier and read the `15:50` cell; extend to SOXX/XLF/XBI/GDX/TLT | `c` measured per name. **Drop rule: any family whose `c` exceeds 1.2642 bps is removed from the traded set before freezing, and the drop list is inside the hash.** If SPY/QQQ/IWM/DIA all exceed 1.8120 bps, the family is **dead on cost** and this draft is voided | **`TO BE MEASURED BEFORE FREEZING`** |
| **P-5** | Point-in-time **shares outstanding** for all 24 LETFs back to 2016-01-04, at daily granularity, via `share_class_shares_outstanding` with an explicit `date=` parameter | non-stale daily panel, >= 90% coverage, staleness runs <= 10 sessions. **If only quarterly or monthly granularity is available, emit `BLOCKED-DEGENERATE` and void the draft** | **`TO BE MEASURED BEFORE FREEZING`** — measured this session: **nothing point-in-time exists on disk** |
| **P-6** | Reconcile `n_obs = 2690` against 2,691 warehouse sessions; recover the `21.07 = 20.00 + 1.07` decomposition and whether the ceiling is gross or net | both stated, or the conservative defaults of §2.5/§2.6 adopted in writing | **`TO BE MEASURED BEFORE FREEZING`** |
| **P-7** | Build and run the §5 fixture; all nine assertions pass on both PASS and KILL variants | `_t34_fixture.py --verify` returns `PASS` | not started |

**PRE-FREEZE DISCLOSURE.** To calibrate the §4 denominator and verify §2.7 coverage, this session queried `minute_aggs` for two months (2016-01, 2026-08 — **39 sessions, 1.45% of the sample**) and, alongside the second moments it was after, **also returned first moments of a NON-primary endpoint**: the naked per-ticker 15:45→15:59 window mean for 24 LETFs and 10 ETFs. Those means are **not reported in this document** and carry no acceptance role. The primary endpoint of this prereg — the orthogonalised cross-family flow residual — **was not computed and is not computable from what was queried**, because it requires the point-in-time `K` panel, which P-5 establishes does not exist on disk. **No peek at the primary endpoint has occurred, and none was possible.** The 39 sessions remain in the sample; they are not excluded, and this paragraph is the mitigation.

---

## 9. PROVENANCE (doc 300: no field is filled unless the source states it)

| figure | artifact status |
|---|---|
| 21.07 bps requirement; 6.0–8.6% ceiling; 15.06 bps sd; Sharpe 22.2; 3.3–15.1× MDE | **PROSE ONLY** — `ATTEMPTS_LEDGER.md:30` and `SS0019`, whose `effect`/`ci`/`n_obs` are all null. **No doc-298 file exists in either repo; the changelog stops at doc 297.** P-1 |
| 12.5% ceiling / central <5% (post-2014 decay) | `docs/research-log/293_the_half_percent_bar.md:47` |
| Closure belongs to the 0.1%/day era | **RESOLVED from commit timestamps**, not from the `21.07 ≈ 2 × 10` inference: `2af6f12` 2026-07-28 20:16 set 0.1%/day; `f411a28` 2026-07-29 carried the CLOSED verdict; `f4b743c` 2026-08-02 15:38 set 5 bps/day. doc 299 RESOLVED / doc 300 item 3 |
| 5 bps/day target; SPY +6.13 bps/day; 0.72/1.76/2.27/7.06 bps cost tiers; no midpoint order type; 1.62 vs 6.42 bps clock; SEC/TAF/CAT schedules; Roll 0.56×/3.5×/29.5% | `docs/TARGET.md` §0, §2b. **`data/research/doc298/` does not exist** — these are prose-only too |
| Bar 1.153711 / 1.150006, toll +0.003705, MDE 1.6689, severity 0.9703, p_cert 0.428841, p_false_positive 0.029735, EIG 0.3367, informativeness 14.422, score 0.3294, rank 1/9 | **REPRODUCED THIS SESSION** — `scripts/epistemics.py plan --spec data/research/design_queue.json` and `src/epistemics/eig.py` |
| n_obs 2,690; 2,691 warehouse sessions; 3,943,485,895 bars; 2016-01-04..2026-09-17 | `design_queue.json` `_keystone_notes` and the 2026-09-21 landing record. The 2,690-vs-2,691 reason is **not stated** — P-6 |
| Last registered trial = T00033 | **VERIFIED THIS SESSION** — `data/research/trial_registry.jsonl` |
| `minute_aggs` schema and partitioning; all 24 LETFs present in 2016-01; close-window coverage 19/19 and 20/20; minute fill 285/285 (min SOXS 195/285); underlying fill 300/300 and 285/285 (SOXX 280/285); 16:00-bar coverage 19/19 SPY-QQQ-IWM-DIA-GDX vs 4/19 SMH, 6/19 SOXX; window sd 24.60–132.35 bps LETF vs 8.52/21.84 bps SPY | **MEASURED THIS SESSION** by DuckDB on `ts_et` |
| `share_class_shares_outstanding` 183/192 ETFs; `weighted_shares_outstanding` 0/192; TQQQ 511,450,000, SOXL 140,400,060, TNA 24,650,000; `ticker_details.parquet` = 2,808-row snapshot; no `date=` in the puller; no PIT/AUM/quote panel on disk; `trades_v1_parquet` = 2025–2026 only | **MEASURED THIS SESSION** |
| Within-day rho 0.09–0.35, ~2.5× SE inflation | doc 278, via `src/epistemics/eig.py::effective_n` docstring |
| `prior_mean = 1.0`, `prior_sd = 0.6`, `omega = 0.25`, `lam = 0.5` | **DECLARED JUDGEMENTS**, not measurements — `design_queue.json` says so in its own header |

---

**FREEZE BLOCK** — completed only when §0 P-1..P-7 all pass.

```
spec_sha256   : <sha256 of this file, byte-exact>
fixture_sha256: <sha256 of scripts/_t34_fixture.py>
runner_sha256 : <sha256 of the runner>
trial_id      : T00034
family        : market_structure_flow / letf_close_window_retest_if_minute_extended
universe      : 9 index families, 24 LETFs, 9 traded ETFs (post-P-4 drop list applied)
horizon       : one 15-minute window, 15:45:00-15:59:59 ET, same session
n_sessions    : 2,690  (acceptance sample <= 2,670 after the 20-session ADV$ warm-up)
n_clusters    : 2,690  (session-aggregated; nominal n == effective n by construction)
bar           : 1.153711   (34 trials, n=2,690, omega=0.25)
MDE(80%)      : 1.6689 annualised Sharpe = 1.5833 bps/window at sd 15.06
death_date    : 2026-10-31
registered_at : <ISO-8601 UTC at freeze>
state         : PROPOSED
```

**Bottom line, so it cannot be mistaken on a second reading:** 21.07 bps is the requirement, not the edge. The edge is bounded above at 1.26–1.81 bps per window, which is why this family clears a **build filter** and not a **target**. Even a full pass leaves it at **0.63–0.91 bps/day against a 5 bps/day bar that buy-and-hold SPY cleared by itself at 6.13.** The test is worth running only because it is $0, it is the top-ranked admissible design in the queue, and it closes the last mechanically revivable family in the archive on a frozen contract instead of leaving it open as a maybe. Four of its inputs are not yet measured, one of them — the close-window cost — can kill it on arithmetic before a single session is scored, and §0 exists so that death costs the program nothing.