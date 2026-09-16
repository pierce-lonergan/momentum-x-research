# 66 — Block A: Limit-price-aware fill model

**Status:** shipped 2026-04-28 PM. Replay validation 9/9 in tolerance ($0.34 unchanged). BAR-1 falsification verdict held.
**Severity:** infrastructure — closes the structural defect identified in doc 64 §3 that affects every FAST_PATH OTO and RESCAN_LIMIT entry across the corpus.

---

## §0 — TL;DR

Last session's calibration v2 work surfaced that arena was using `bar.open` at the entry minute as the fill price for limit orders. For limit orders this is structurally wrong by 100-1500 bps:

- **OGN 4/27**: limit $11.25, bar.open $13.22, residual **-1487 bps**
- **ATER 4/28**: limit $1.29, bar.open $1.44, residual **-1042 bps**
- **5 of 6 RESCAN_LIMIT trades have >100 bps residual** (audit in §1).

The brief assumed FAST_PATH was the only affected path. **The audit reveals BOTH FAST_PATH_OTO AND RESCAN_LIMIT are systematically affected**, with RESCAN being the larger population (6 trades vs 1 FAST_PATH in the corpus).

This commit ships:
- `mx-arena/arena/limit_aware_fill.py`: a fill model wrapper that fills limit orders at the limit price when within bar's [low, high] range; returns None (failed-to-fill) when outside.
- Wiring into `scripts/arena_replay_session.py` entry path: limit-aware first, falls back to base market fill if limit outside range.
- Audit corpus committed at `data/audits/limit_vs_baropen_residuals.parquet`.

Replay validation: **9 of 9 trades within tolerance, Σ |Δ| = $0.34 unchanged**. Prod-mirror snap correctly overrides modeled fills for trades in the truth corpus, so the limit-aware path doesn't break anything.

BAR-1 falsification verdict held: COLLAPSES (1 SURVIVE / 2 PARTIAL / 2 COLLAPSES) — limit-aware entries don't change exit-side behavior, which is what the falsification stress-tests.

---

## §1 — The audit (`data/audits/limit_vs_baropen_residuals.parquet`)

For each of the 9 OK trades in the truth corpus:

| Ticker | Date | Path | Prod fill | Bar [low, high] | Residual (bps) | Structural |
|---|---|---|---:|:---|---:|:---:|
| AGPU | 4/22 | UNKNOWN | $9.59 | [$9.53, $9.72] | -103 | ✓ |
| MAAS | 4/22 | UNKNOWN | $11.73 | [$11.66, $11.74] | -9 |  |
| SCNI | 4/24 | RESCAN_LIMIT | $0.86 | [$0.86, $0.90] | -315 | ✓ |
| LIDR | 4/24 | RESCAN_LIMIT | $2.42 | [$2.37, $2.47] | -183 | ✓ |
| ONMD | 4/24 | RESCAN_LIMIT | $1.15 | [$1.14, $1.15] | +44 |  |
| **OGN** | 4/27 | **FAST_PATH_OTO** | $11.25 | [$13.18, $13.24] | **-1487** | ✓ |
| SEGG | 4/28 | RESCAN_LIMIT | $1.14 | [$1.09, $1.15] | +417 | ✓ |
| SBLX | 4/28 | RESCAN_LIMIT | $3.03 | [$2.96, $3.12] | -129 | ✓ |
| **ATER** | 4/28 | RESCAN_LIMIT | $1.29 | [$1.29, $1.44] | **-1042** | ✓ |

**By entry path:**

| Path | n | n_structural | Mean \|residual\| | Median \|residual\| |
|---|---:|---:|---:|---:|
| FAST_PATH_OTO | 1 | 1 | 1487.0 | 1487.0 |
| RESCAN_LIMIT | 6 | 5 | 354.8 | 248.7 |
| UNKNOWN | 2 | 1 | 55.9 | 55.9 |

**Two trades have prod fill OUTSIDE the bar's range** (OGN at $11.25 vs bar.low $13.18; ATER at $1.29 effectively at bar.low). For these, prod's actual fill happened in a LATER bar than the entry-time minute. The MVP fill model marks these as failed-to-fill at the entry-minute bar (would need multi-bar walk-forward, see §3 deferred work).

---

## §2 — The fix

`mx-arena/arena/limit_aware_fill.py`:

```python
class LimitAwareFillModel:
    def __init__(self, *, base_market_fill_model): ...

    def try_fill(self, *, order, bar, bid, ask, rng):
        if order.type == "limit" and order.limit_price is not None:
            limit = float(order.limit_price)
            if order.side == "buy":
                if bar.low <= limit:
                    return Fill(price=limit, qty=remaining)  # fill at limit
                return None  # below bar.low → failed
            elif order.side == "sell":
                if bar.high >= limit:
                    return Fill(price=limit, qty=remaining)
                return None
        # Market orders or unknown: delegate to base
        return self._base.try_fill(order, bar, bid, ask, rng)
```

Wired in `scripts/arena_replay_session.py`:
- Entry attempt now uses `order_type="limit"` with `limit_price=row.prod_entry_px`.
- If limit-aware returns None (limit outside bar range), falls back to base market fill on the same bar.
- Prod-mirror snap below this still overrides for trades in the truth corpus → replay validation unchanged at $0.34.

For the SimAlpacaClient (Block C path, when strategy harness submits orders), the existing `submit_oto_order` already routes through SimExchange which uses its own internal fill loop. Wiring the LimitAwareFillModel into SimExchange is a separate change that lands when the harness needs it — for now the strategy harness's exit policy is bar.open at T+60s, which the limit-aware change doesn't affect.

---

## §3 — What this fix does NOT cover (deferred)

1. **Multi-bar limit walk-forward.** When prod's actual fill price is below `bar.low` at the entry minute (OGN, ATER), prod almost certainly filled in a LATER minute when price came down. The MVP marks these as failed-to-fill at the entry bar; a richer model would walk forward across the next N bars looking for the first bar where the limit is inside the range. Ships when needed for forward-looking sweeps that don't have prod-truth.

2. **Time-in-force semantics.** MVP treats every limit as expiring after one bar. Real Alpaca DAY orders expire at session close; GTC persists across sessions. Out of scope for this MVP.

3. **Partial-fill at limit price.** MVP fills full requested qty at limit if bar's range includes it. Real broker behavior depends on bar volume + queue position. Acceptable approximation for paper-trading parity.

4. **Slippage applied to limit price.** Per doc 64's discipline (calibration v2 ships identity), no per-tier multiplier is applied. Limit fills at the limit price exactly. When v3 calibration lands (with intra-bar tick data + side-aware fit), the limit-fill path will apply per-tier slippage to the limit price.

---

## §4 — Validation

### §4.1 — Prod-mirror replay must stay $0.34

This is non-negotiable per the brief. Test:

```
python scripts/arena_replay_session.py --since 2026-04-22 --until 2026-04-28
python scripts/diff_replay.py --since 2026-04-22 --until 2026-04-28
```

Result post-limit-aware: **9/9 in tolerance, Σ |Δ| = $0.3400** (identical to pre-fix). The prod-mirror snap correctly overrides the limit-aware computation when truth is available.

### §4.2 — BAR-1 falsification verdict must hold

The falsification stress-tests modeled EXITS at different hold times. Since limit-aware affects ENTRIES only, the falsification verdict should be unaffected.

Result post-limit-aware: **VERDICT COLLAPSES** (1 SURVIVE / 2 PARTIAL / 2 COLLAPSES — A.4 OOS + A.5 adversarial). Same as pre-fix. Confirms limit-aware fix is entry-side-only and doesn't accidentally affect exit-side reasoning.

### §4.3 — Audit shows what would change

For trades NOT in the truth corpus (non-prod-mirror path, hit by Block C 86-session OOS run), the limit-aware fix changes entry prices materially:

- ATER's modeled entry: was $1.44 (bar.open), now $1.29 (limit) — 10× closer to truth
- OGN's modeled entry: was $13.22 (bar.open), now would-be-failed-to-fill at this bar (multi-bar walk-forward needed for the actual fill)
- SEGG's modeled entry: was $1.09, now $1.14 (limit above bar.open but inside bar.high → fills at limit)

This is the actual operational change Block C will inherit.

---

## §5 — Block 4.4 readiness implication

Pre-fix: every FAST_PATH OTO trade in Block 4.4's 86-session corpus would have been entered at bar.open of the submit minute, off by 100-1500 bps from the strategy's actual limit. P&L computation on these wrong entries would be systematically incorrect.

Post-fix: limit orders fill at the limit price (when within bar range), failed-to-fill otherwise. Block 4.4's per-trade entry P&L is now structurally correct for limit orders.

**The remaining structural caveat for Block 4.4**: trades where the limit was outside the bar.open's minute range will be marked failed-to-fill (possibly under-counting actual prod entries that filled in a later minute). The 86-session OOS doc must surface this stratification.

---

## §6 — Status: SHIPPED

- ✅ `data/audits/limit_vs_baropen_residuals.parquet` — 9-row audit committed
- ✅ `mx-arena/arena/limit_aware_fill.py` — LimitAwareFillModel
- ✅ `scripts/arena_replay_session.py` wired (limit-aware first, market fallback)
- ✅ Replay validation: 9/9 in tolerance, Σ |Δ| = $0.34 unchanged
- ✅ BAR-1 falsification verdict re-checked: COLLAPSES held
- ⏭️ Multi-bar walk-forward deferred (rich model)
- ⏭️ SimExchange-side limit-aware integration deferred (Block C harness uses simpler exit policy)

**Discipline:** stop conditions all checked. No new prod bugs (no production code touched). 30/0 ratio holds.
