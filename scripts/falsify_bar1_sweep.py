"""Five orthogonal falsification tests for last session's BAR-1
timing sweep finding (T+15min Σ ≈ +$4,786 vs prod $719 = 6.6× lift).

Block A of the rig-falsification session. Each test is designed to
COLLAPSE the 6.6× if it was a simulator artifact. Survival across
all 5 = candidate finding worth chasing in Block 4.4. Collapse on
any 2+ = artifact, document and move on.

Tests:
  A.1 — Multi-seed variance (10 seeds; bands instead of point est)
  A.2 — Slippage-multiplier stress (1×, 2×, 3×, 5×)
  A.3 — Per-trade decomposition (which trades drive the lift?)
  A.4 — Out-of-sample bar test (synthesized entries, Dec 2025 - Feb 2026)
  A.5 — Adversarial gap-fade exit modeling

Output: docs/sweeps/bar1_timing_falsification.md with per-test result
+ aggregate verdict ∈ {SURVIVES, PARTIALLY SURVIVES, COLLAPSES}.

Determinism: every test seeds explicitly and reports the seed used.
Re-running this script must produce identical numbers.
"""
from __future__ import annotations

import argparse
import json
import logging
import statistics
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from random import Random

logger = logging.getLogger("falsify_bar1")

REPO_ROOT = Path(__file__).resolve().parent.parent
ATTRIBUTION_DIR = REPO_ROOT / "data" / "instrumentation" / "trade_attribution"
ARENA_HISTORICAL = REPO_ROOT / "mx-arena" / "data" / "historical"
BAR_RECORDINGS = REPO_ROOT / "data" / "bar_recordings"
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "sweeps" / "bar1_timing_falsification.md"

sys.path.insert(0, str(REPO_ROOT / "mx-arena"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))


HOLD_TIMES_SEC = [60, 300, 900, 23400]  # T+60s (BAR-1 default), T+5min, T+15min, EOD
HOLD_LABELS = {60: "T+60s", 300: "T+5min", 900: "T+15min", 23400: "EOD"}

SEEDS_FOR_A1 = list(range(10))
MULTIPLIERS_FOR_A2 = [1.0, 2.0, 3.0, 5.0]


# ── Common machinery ──────────────────────────────────────────────


def _load_attribution_corpus() -> list[dict]:
    """Pull all 'full' completeness rows from the attribution corpus."""
    import pandas as pd
    rows = []
    if not ATTRIBUTION_DIR.exists():
        return rows
    for sd_dir in sorted(ATTRIBUTION_DIR.iterdir()):
        if not sd_dir.is_dir():
            continue
        target = sd_dir / "attribution.parquet"
        if not target.exists():
            continue
        df = pd.read_parquet(target)
        for _, r in df.iterrows():
            rows.append(dict(r))
    return [r for r in rows if r.get("data_completeness") == "full"]


def _load_bars(ticker: str, session_date: str):
    from arena.data_engine import DataEngine
    from arena.clock import SimClock, ClockMode
    target = ARENA_HISTORICAL / ticker / f"{session_date}.parquet"
    if not target.exists():
        return None
    clock = SimClock(
        start=datetime.fromisoformat(f"{session_date}T13:30:00+00:00"),
        end=datetime.fromisoformat(f"{session_date}T20:00:00+00:00"),
        mode=ClockMode.REPLAY,
    )
    return DataEngine(clock=clock, historical_dir=str(ARENA_HISTORICAL))._load_parquet_bars(ticker, session_date)


def _bar_at_or_after(bars_map: dict, ts: datetime):
    target_iso = ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    for _idx, bar in bars_map.items():
        if bar.timestamp >= target_iso:
            return bar
    return list(bars_map.values())[-1]


def _arena_sell_fill(*, qty: int, bar, ts: datetime, fill_model, spread_model, rng):
    from arena.exchange import OrderState
    mid = (bar.open + bar.close) / 2.0
    half = spread_model.get_spread(price=mid, volume=bar.volume, timestamp=ts)
    bid = round(mid - half, 4)
    ask = round(mid + half, 4)
    order = OrderState(
        id=str(uuid.uuid4()), client_order_id=str(uuid.uuid4()),
        symbol="X", side="sell", type="market", time_in_force="day",
        qty=qty, status="new",
    )
    fill = fill_model.try_fill(order=order, bar=bar, bid=bid, ask=ask, rng=rng)
    return fill.price if fill else bid


# ── Adversarial fill model for A.5 ────────────────────────────────


class AdversarialFadeFillModel:
    """Models the "gap fades after T+5min" worst-case: each minute past
    T+5min the price drifts 50 bps adversely (against the long side).

    Wraps AlpacaFillModel; applies the fade only when the bar timestamp
    is more than `fade_after_seconds` past `entry_ts`."""

    def __init__(self, *, base_fill_model, entry_ts: datetime,
                 fade_after_seconds: int = 300, fade_bps_per_min: float = 50.0):
        self._base = base_fill_model
        self._entry_ts = entry_ts
        self._fade_after = fade_after_seconds
        self._fade_per_min = fade_bps_per_min

    def try_fill(self, *, order, bar, bid, ask, rng):
        # Compute fade adjustment based on time since entry
        try:
            bar_ts = datetime.fromisoformat(bar.timestamp.replace("Z", "+00:00"))
        except Exception:
            bar_ts = self._entry_ts
        elapsed = (bar_ts - self._entry_ts).total_seconds()
        if elapsed > self._fade_after:
            extra_min = (elapsed - self._fade_after) / 60.0
            fade_pct = self._fade_per_min * extra_min / 1e4  # bps → fraction
            # Adverse for sell-side longs: lower the bid
            adverse_bid = bid * (1.0 - fade_pct)
            adverse_ask = ask * (1.0 - fade_pct)
        else:
            adverse_bid, adverse_ask = bid, ask
        return self._base.try_fill(order, bar, adverse_bid, adverse_ask, rng)


# ── Single-trade sweep (parameterized) ────────────────────────────


def _sweep_one_trade(
    *, ticker: str, session_date: str, entry_ts: datetime, qty: int,
    entry_px: float, seed: int, calibration: dict | None = None,
    adversarial: bool = False,
) -> dict[str, float]:
    """Returns {hold_label: pnl} for one trade across all hold times."""
    from arena.fill_model import AlpacaFillModel
    from arena.spread_model import SpreadModel

    bars = _load_bars(ticker, session_date)
    if bars is None:
        return {label: 0.0 for label in HOLD_LABELS.values()}

    fill_model = AlpacaFillModel()
    spread_model = SpreadModel(calibration=calibration)
    rng = Random(seed)

    out = {}
    for hold_sec, label in HOLD_LABELS.items():
        exit_ts = entry_ts + timedelta(seconds=hold_sec)
        exit_bar = _bar_at_or_after(bars, exit_ts)
        if exit_bar is None:
            out[label] = 0.0
            continue
        if adversarial:
            adv_fm = AdversarialFadeFillModel(
                base_fill_model=fill_model, entry_ts=entry_ts,
            )
            exit_px = _arena_sell_fill(
                qty=qty, bar=exit_bar, ts=exit_ts,
                fill_model=adv_fm, spread_model=spread_model, rng=rng,
            )
        else:
            exit_px = _arena_sell_fill(
                qty=qty, bar=exit_bar, ts=exit_ts,
                fill_model=fill_model, spread_model=spread_model, rng=rng,
            )
        out[label] = round((exit_px - entry_px) * qty, 2)
    return out


def _sum_per_hold(per_trade: list[dict], hold_label: str) -> float:
    return sum(t.get(hold_label, 0.0) for t in per_trade)


# ── A.1 multi-seed ────────────────────────────────────────────────


def test_a1_multi_seed(corpus: list[dict]) -> dict:
    logger.info("A.1: multi-seed variance (n=%d trades × %d seeds)", len(corpus), len(SEEDS_FOR_A1))
    per_seed_totals: dict[str, list[float]] = {label: [] for label in HOLD_LABELS.values()}
    for seed in SEEDS_FOR_A1:
        per_trade = [
            _sweep_one_trade(
                ticker=r["ticker"], session_date=r["session_date"],
                entry_ts=datetime.fromisoformat(r["entry_ts"]),
                qty=int(r["prod_qty"]), entry_px=float(r["prod_entry_avg_px"]),
                seed=seed,
            )
            for r in corpus
        ]
        for label in HOLD_LABELS.values():
            per_seed_totals[label].append(_sum_per_hold(per_trade, label))

    # mean + stdev per hold
    summary = {}
    for label, totals in per_seed_totals.items():
        summary[label] = {
            "mean": round(statistics.mean(totals), 2),
            "stdev": round(statistics.stdev(totals) if len(totals) > 1 else 0.0, 2),
            "min": round(min(totals), 2),
            "max": round(max(totals), 2),
        }
    return {"per_seed_totals": per_seed_totals, "summary": summary, "n_seeds": len(SEEDS_FOR_A1)}


# ── A.2 slippage multiplier ───────────────────────────────────────


def test_a2_multiplier(corpus: list[dict], baseline_seed: int = 0) -> dict:
    logger.info("A.2: slippage-multiplier stress (multipliers=%s)", MULTIPLIERS_FOR_A2)
    out = {}
    for mult in MULTIPLIERS_FOR_A2:
        cal = {"sub_1": mult, "sub_3": mult, "sub_10": mult, "sub_50": mult, "above_50": mult}
        per_trade = [
            _sweep_one_trade(
                ticker=r["ticker"], session_date=r["session_date"],
                entry_ts=datetime.fromisoformat(r["entry_ts"]),
                qty=int(r["prod_qty"]), entry_px=float(r["prod_entry_avg_px"]),
                seed=baseline_seed, calibration=cal,
            )
            for r in corpus
        ]
        per_hold = {label: _sum_per_hold(per_trade, label) for label in HOLD_LABELS.values()}
        # Lift ratio: T+15min / T+60s (the canonical 6.6× formulation)
        t60 = per_hold["T+60s"]
        t15 = per_hold["T+15min"]
        lift = (t15 / t60) if abs(t60) > 1.0 else float("inf")
        out[f"{mult}x"] = {
            "totals": {k: round(v, 2) for k, v in per_hold.items()},
            "lift_t15_over_t60": round(lift, 2) if lift != float("inf") else None,
        }
    return out


# ── A.3 per-trade decomposition ───────────────────────────────────


def test_a3_decomposition(corpus: list[dict], seed: int = 0) -> dict:
    logger.info("A.3: per-trade decomposition")
    per_trade_curves = []
    for r in corpus:
        curve = _sweep_one_trade(
            ticker=r["ticker"], session_date=r["session_date"],
            entry_ts=datetime.fromisoformat(r["entry_ts"]),
            qty=int(r["prod_qty"]), entry_px=float(r["prod_entry_avg_px"]),
            seed=seed,
        )
        per_trade_curves.append({
            "ticker": r["ticker"],
            "session_date": r["session_date"],
            "news_signal": r.get("news_signal", "?"),
            "prod_pnl": round(float(r.get("prod_pnl", 0)), 2),
            **curve,
            "lift_contribution_t15": round(curve.get("T+15min", 0) - curve.get("T+60s", 0), 2),
        })
    # Sort by lift contribution descending
    per_trade_curves.sort(key=lambda x: -x["lift_contribution_t15"])
    return {"per_trade": per_trade_curves}


# ── A.4 OOS bar test ──────────────────────────────────────────────


def test_a4_oos(seed: int = 0) -> dict:
    logger.info("A.4: out-of-sample bar test")
    # Use the deterministic selection from the survey
    oos_targets = [
        ("2025-12-16", "AMCI"),
        ("2026-01-06", "AEVA"),
        ("2026-01-28", "ABOS"),
        ("2026-02-02", "APPX"),
        ("2026-02-06", "AAOI"),
    ]

    # Convert these to parquet first if not already done
    import subprocess
    for date, ticker in oos_targets:
        target = ARENA_HISTORICAL / ticker / f"{date}.parquet"
        if not target.exists():
            # Convert just this one date
            cmd = [sys.executable, "scripts/convert_bars_to_arena_parquet.py", "--date", date]
            try:
                subprocess.run(cmd, capture_output=True, check=True)
            except Exception as e:
                logger.warning("convert failed for %s: %s", date, e)

    per_trade_curves = []
    for date, ticker in oos_targets:
        bars = _load_bars(ticker, date)
        if bars is None:
            logger.warning("OOS skip %s/%s: no bars", date, ticker)
            continue
        # Synthesize entry: first regular-hours bar (13:30 UTC = 09:30 ET)
        entry_ts = datetime.fromisoformat(f"{date}T13:30:00+00:00")
        first_bar = _bar_at_or_after(bars, entry_ts)
        if first_bar is None:
            continue
        entry_px = float(first_bar.open)
        # Synthesize qty: tier-1 default (50% × $150K equity / entry_px)
        qty = max(1, int(75_000.0 / max(entry_px, 0.01)))
        curve = _sweep_one_trade(
            ticker=ticker, session_date=date,
            entry_ts=entry_ts, qty=qty, entry_px=entry_px,
            seed=seed,
        )
        per_trade_curves.append({
            "ticker": ticker, "session_date": date,
            "synth_qty": qty, "synth_entry_px": entry_px,
            **curve,
            "lift_t15_over_t60_dollars": round(curve.get("T+15min", 0) - curve.get("T+60s", 0), 2),
        })

    if not per_trade_curves:
        return {"per_trade": [], "totals": {}, "lift_t15_over_t60_ratio": None}

    totals = {label: round(sum(t.get(label, 0) for t in per_trade_curves), 2) for label in HOLD_LABELS.values()}
    t60 = totals["T+60s"]
    t15 = totals["T+15min"]
    lift = (t15 / t60) if abs(t60) > 1.0 else float("inf")
    return {
        "per_trade": per_trade_curves,
        "totals": totals,
        "lift_t15_over_t60_ratio": round(lift, 2) if lift != float("inf") else None,
    }


# ── A.5 adversarial gap-fade ──────────────────────────────────────


def test_a5_adversarial(corpus: list[dict], seed: int = 0) -> dict:
    logger.info("A.5: adversarial gap-fade exit (50 bps/min after T+5min)")
    per_trade = [
        _sweep_one_trade(
            ticker=r["ticker"], session_date=r["session_date"],
            entry_ts=datetime.fromisoformat(r["entry_ts"]),
            qty=int(r["prod_qty"]), entry_px=float(r["prod_entry_avg_px"]),
            seed=seed, adversarial=True,
        )
        for r in corpus
    ]
    totals = {label: round(_sum_per_hold(per_trade, label), 2) for label in HOLD_LABELS.values()}
    t60 = totals["T+60s"]
    t15 = totals["T+15min"]
    lift = (t15 / t60) if abs(t60) > 1.0 else float("inf")
    return {
        "totals": totals,
        "lift_t15_over_t60_ratio": round(lift, 2) if lift != float("inf") else None,
    }


# ── Verdict ───────────────────────────────────────────────────────


def render_verdict_doc(*, baseline_lift: float, results: dict) -> str:
    """Aggregate the 5 test results into a verdict markdown."""
    lines = []
    lines.append("# BAR-1 Timing Sweep Falsification Report")
    lines.append("")
    lines.append("**Generated:** by `scripts/falsify_bar1_sweep.py` against `data/instrumentation/trade_attribution/`.")
    lines.append("**Trigger:** last session's BAR-1 timing sweep produced a 6.6× lift at T+15min over prod baseline. This report runs 5 orthogonal stress tests designed to COLLAPSE the lift if it was a simulator artifact.")
    lines.append("")
    lines.append(f"**Baseline 6.6× lift reference:** T+15min Σ ≈ +$4,786 / prod baseline +$719 = 6.65×.")
    lines.append("")
    lines.append("---")
    lines.append("")

    # ── A.1 ──
    a1 = results["a1"]
    lines.append("## A.1 — Multi-seed variance (n=10 seeds, single-trade rng)")
    lines.append("")
    lines.append("| Hold time | Mean Σ | Stdev | Min | Max | Stdev / Mean |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for label, stats in a1["summary"].items():
        ratio = (stats["stdev"] / abs(stats["mean"])) if abs(stats["mean"]) > 1.0 else float("inf")
        ratio_s = f"{ratio:.2f}" if ratio != float("inf") else "n/a"
        lines.append(f"| {label} | ${stats['mean']:+,.2f} | ${stats['stdev']:,.2f} | ${stats['min']:+,.2f} | ${stats['max']:+,.2f} | {ratio_s} |")
    a1_t15 = a1["summary"]["T+15min"]
    a1_t60 = a1["summary"]["T+60s"]
    a1_lift = a1_t15["mean"] / a1_t60["mean"] if abs(a1_t60["mean"]) > 1.0 else float("inf")
    lines.append("")
    lines.append(f"**A.1 verdict:** T+15min mean ${a1_t15['mean']:+,.2f} ± ${a1_t15['stdev']:,.2f}, T+60s mean ${a1_t60['mean']:+,.2f} ± ${a1_t60['stdev']:,.2f}. Lift mean / mean = **{a1_lift:.2f}×**.")
    if abs(a1_t15["stdev"]) < 0.01 and abs(a1_t60["stdev"]) < 0.01:
        # Methodological finding: the fill model is essentially deterministic
        # for the sweep's price computation. rng affects partial-fill qty
        # only, and the sweep computes pnl with full prod_qty regardless.
        # So multi-seed test cannot detect variance even if it existed.
        lines.append("- ⚠️ **TEST DEGENERATE:** stdev = 0 across all seeds. The arena fill model is deterministic in PRICE for market orders (rng only affects partial-fill qty, which the sweep ignores). Multi-seed variance test cannot exercise the simulator's stochasticity as currently wired. **This is itself a rig finding** — see §X. The test does NOT validate the lift; it surfaces that the simulator has no stochastic exit pricing under this codepath.")
        a1_collapse = "partial"
    elif abs(a1_t15["stdev"]) >= abs(a1_t15["mean"]):
        lines.append("- ❌ **COLLAPSE:** stdev ≥ mean magnitude → noise dominates.")
        a1_collapse = True
    elif abs(a1_t15["stdev"]) > abs(a1_t15["mean"]) * 0.3:
        lines.append("- ⚠️ **HIGH VARIANCE:** stdev > 30% of mean → claim is fragile.")
        a1_collapse = "partial"
    else:
        lines.append("- ✅ **STABLE:** stdev < 30% of mean → multi-seed result is consistent.")
        a1_collapse = False
    lines.append("")

    # ── A.2 ──
    a2 = results["a2"]
    lines.append("## A.2 — Slippage-multiplier stress")
    lines.append("")
    lines.append("Same trades, scale arena's spread (and thus per-trade fill cost) by N×. If the 6.6× was driven by under-modeled slippage, larger multipliers should compress it.")
    lines.append("")
    lines.append("| Multiplier | Σ T+60s | Σ T+5min | Σ T+15min | Σ EOD | Lift (T+15/T+60) |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for key, vals in a2.items():
        t = vals["totals"]
        lift_s = f"{vals['lift_t15_over_t60']:.2f}×" if vals['lift_t15_over_t60'] is not None else "n/a"
        lines.append(f"| {key} | ${t['T+60s']:+,.2f} | ${t['T+5min']:+,.2f} | ${t['T+15min']:+,.2f} | ${t['EOD']:+,.2f} | {lift_s} |")
    lift_5x = a2.get("5.0x", {}).get("lift_t15_over_t60")
    lift_3x = a2.get("3.0x", {}).get("lift_t15_over_t60")
    lines.append("")
    # Compare absolute totals at 5× vs 1× — if all four hold-times scale
    # roughly proportionally, the test isn't differentiating hold times.
    if lift_5x is not None:
        t15_1x = a2.get("1.0x", {}).get("totals", {}).get("T+15min", 0)
        t15_5x = a2.get("5.0x", {}).get("totals", {}).get("T+15min", 0)
        ratio_5x_to_1x = (t15_5x / t15_1x) if abs(t15_1x) > 1.0 else float("inf")
        lines.append(f"- T+15min Σ at 5× / 1× = {ratio_5x_to_1x:.3f} (uniform spread scaling preserves ratios; differential hold-time penalty would be needed to falsify properly)")
    if lift_5x is not None and lift_5x < 1.5:
        lines.append(f"- ❌ **COLLAPSE:** 5× multiplier brings lift to {lift_5x:.2f}× (< 1.5×). The 6.6× was arena under-modeling slippage.")
        a2_collapse = True
    elif lift_3x is not None and lift_3x < 2.0:
        lines.append(f"- ⚠️ **PARTIAL:** 3× multiplier brings lift to {lift_3x:.2f}× (< 2.0×). Lift is calibration-sensitive.")
        a2_collapse = "partial"
    else:
        # Methodological caveat: a UNIFORM multiplier scales both T+60s
        # and T+15min equally, so the LIFT RATIO is invariant by
        # construction. A proper test would scale slippage MORE on
        # longer holds (the 50-bps/min gap-fade in A.5 does this).
        lines.append("- ⚠️ **TEST METHODOLOGICALLY LIMITED:** uniform spread-multiplier scales T+60s and T+15min Σ proportionally, so the ratio is preserved by construction. This test cannot falsify the lift via this mechanism; see A.5 for the differential-fade approach that CAN.")
        a2_collapse = "partial"
    lines.append("")

    # ── A.3 ──
    a3 = results["a3"]
    lines.append("## A.3 — Per-trade decomposition (which trades drive the lift?)")
    lines.append("")
    lines.append("Sorted by `lift_contribution_t15` (T+15min P&L − T+60s P&L). Top contributors carry the aggregate lift.")
    lines.append("")
    lines.append("| Ticker | Date | Signal | T+60s | T+15min | Δ (lift contrib) |")
    lines.append("|---|---|---|---:|---:|---:|")
    for r in a3["per_trade"]:
        lines.append(
            f"| {r['ticker']} | {r['session_date']} | {r['news_signal']} | "
            f"${r['T+60s']:+,.2f} | ${r['T+15min']:+,.2f} | ${r['lift_contribution_t15']:+,.2f} |"
        )
    total_contrib = sum(r["lift_contribution_t15"] for r in a3["per_trade"])
    if total_contrib != 0 and a3["per_trade"]:
        top = a3["per_trade"][0]
        top_share = abs(top["lift_contribution_t15"]) / abs(total_contrib) * 100 if abs(total_contrib) > 1.0 else 0
        top2 = sum(r["lift_contribution_t15"] for r in a3["per_trade"][:2])
        top2_share = abs(top2) / abs(total_contrib) * 100 if abs(total_contrib) > 1.0 else 0
    else:
        top_share = 0
        top2_share = 0
    lines.append("")
    lines.append(f"- Top single contributor: **{a3['per_trade'][0]['ticker'] if a3['per_trade'] else 'n/a'}** = {top_share:.0f}% of aggregate lift")
    lines.append(f"- Top 2 contributors: {top2_share:.0f}% of aggregate lift")
    if top2_share > 90:
        lines.append("- ❌ **COLLAPSE-AS-STRATEGY:** lift is concentrated in 1-2 trades — this is a stratification finding (catalyst-driven), not a hold-time finding.")
        a3_collapse = True
    elif top_share > 60:
        lines.append("- ⚠️ **PARTIAL:** single trade dominates → lift is heavily catalyst-driven.")
        a3_collapse = "partial"
    else:
        lines.append("- ✅ **DISTRIBUTED:** lift spread across multiple trades → genuine cross-trade signal.")
        a3_collapse = False
    lines.append("")

    # ── A.4 ──
    a4 = results["a4"]
    lines.append("## A.4 — Out-of-sample bar test (Dec 2025 - Feb 2026 synthesized entries)")
    lines.append("")
    if a4["per_trade"]:
        lines.append("Synthesized entry: first regular-hours bar (09:30 ET) of each session at tier-1 default sizing ($75K notional). Hold-time sweep on the same bar corpus arena uses for the in-sample sweep.")
        lines.append("")
        lines.append("| Ticker | Date | qty | entry | T+60s | T+15min | EOD |")
        lines.append("|---|---|---:|---:|---:|---:|---:|")
        for r in a4["per_trade"]:
            lines.append(
                f"| {r['ticker']} | {r['session_date']} | {r['synth_qty']} | "
                f"${r['synth_entry_px']:.2f} | ${r['T+60s']:+,.2f} | "
                f"${r['T+15min']:+,.2f} | ${r['EOD']:+,.2f} |"
            )
        t = a4["totals"]
        lines.append("")
        lines.append(f"- Σ T+60s: ${t['T+60s']:+,.2f}")
        lines.append(f"- Σ T+15min: ${t['T+15min']:+,.2f}")
        lines.append(f"- Σ EOD: ${t['EOD']:+,.2f}")
        if a4["lift_t15_over_t60_ratio"] is not None:
            lift = a4["lift_t15_over_t60_ratio"]
            lines.append(f"- OOS lift T+15/T+60: **{lift:.2f}×** (in-sample reference: 6.65×)")
            if lift > 4.0:
                lines.append("- ✅ **REPLICATES:** OOS lift is comparable to in-sample → signal is not specific to 4/22-4/28.")
                a4_collapse = False
            elif lift > 1.5:
                lines.append("- ⚠️ **PARTIAL:** OOS lift is positive but smaller → in-sample lift partially overfit.")
                a4_collapse = "partial"
            else:
                lines.append("- ❌ **COLLAPSES OOS:** lift is specific to in-sample window → overfit to 4/22-4/28.")
                a4_collapse = True
        else:
            a4_collapse = "partial"
    else:
        lines.append("- ⚠️ **TEST INCONCLUSIVE:** no OOS bars loaded successfully.")
        a4_collapse = "partial"
    lines.append("")

    # ── A.5 ──
    a5 = results["a5"]
    lines.append("## A.5 — Adversarial gap-fade exit (50 bps/min after T+5min)")
    lines.append("")
    lines.append("Replace exit fills with a worst-case model: every minute past T+5min the price drifts 50 bps adversely (against long position). Tests whether the lift survives if real-world adverse selection bites long holds.")
    lines.append("")
    t = a5["totals"]
    lines.append("| Hold time | Σ adversarial | vs uncalibrated baseline |")
    lines.append("|---|---:|---|")
    baseline_a3 = {label: sum(r[label] for r in a3["per_trade"]) for label in HOLD_LABELS.values()}
    for label in HOLD_LABELS.values():
        delta = t[label] - baseline_a3[label]
        lines.append(f"| {label} | ${t[label]:+,.2f} | Δ ${delta:+,.2f} |")
    lift = a5["lift_t15_over_t60_ratio"]
    lines.append("")
    if lift is not None:
        lines.append(f"- Adversarial T+15/T+60 lift: **{lift:.2f}×**")
        if lift < 1.5:
            lines.append("- ❌ **COLLAPSE:** adversarial fade brings lift below 1.5× → real-world adverse selection erases the apparent edge.")
            a5_collapse = True
        elif lift < 3.0:
            lines.append("- ⚠️ **PARTIAL:** adversarial fade compresses lift but doesn't erase it.")
            a5_collapse = "partial"
        else:
            lines.append("- ✅ **SURVIVES:** lift persists even under adversarial exit modeling.")
            a5_collapse = False
    else:
        a5_collapse = "partial"
    lines.append("")

    # ── Aggregate verdict ──
    lines.append("---")
    lines.append("")
    lines.append("## Aggregate verdict")
    lines.append("")
    test_outcomes = {
        "A.1 multi-seed variance": a1_collapse,
        "A.2 slippage-multiplier stress": a2_collapse,
        "A.3 per-trade decomposition": a3_collapse,
        "A.4 out-of-sample test": a4_collapse,
        "A.5 adversarial gap-fade": a5_collapse,
    }
    lines.append("| Test | Outcome |")
    lines.append("|---|---|")
    for test, outcome in test_outcomes.items():
        if outcome is True:
            sym = "❌ COLLAPSES"
        elif outcome == "partial":
            sym = "⚠️ PARTIAL"
        elif outcome is False:
            sym = "✅ SURVIVES"
        else:
            sym = "? inconclusive"
        lines.append(f"| {test} | {sym} |")

    n_collapse = sum(1 for v in test_outcomes.values() if v is True)
    n_partial = sum(1 for v in test_outcomes.values() if v == "partial")
    n_survive = sum(1 for v in test_outcomes.values() if v is False)
    lines.append("")
    lines.append(f"**Tally: {n_survive} SURVIVE / {n_partial} PARTIAL / {n_collapse} COLLAPSE.**")
    lines.append("")
    if n_collapse >= 2:
        verdict = "COLLAPSES"
        lines.append(f"### Verdict: **COLLAPSES** ({n_collapse} hard collapses ≥ 2 threshold)")
        lines.append("")
        lines.append("The 6.6× BAR-1 timing lift was a simulator artifact. The specific failure modes that produced the apparent edge are documented above. Operational implication: the BAR-1 EXIT default at T+60s is **not** clearly suboptimal based on this rig; the apparent T+15min edge does not survive skeptical testing. Move on; do not chase BAR-1 timing as a candidate config.")
    elif n_survive >= 3 and n_collapse == 0:
        verdict = "SURVIVES"
        lines.append(f"### Verdict: **SURVIVES** ({n_survive} survives, 0 collapses)")
        lines.append("")
        lines.append("The 6.6× lift survives all hostile tests. It is a candidate finding worth pursuing in Block 4.4's full 86-session run. Operational implication: track as candidate config; do not promote until an arena-driven OOS run on the broader corpus corroborates.")
    else:
        verdict = "PARTIALLY SURVIVES"
        lines.append(f"### Verdict: **PARTIALLY SURVIVES** ({n_survive} survives / {n_partial} partial / {n_collapse} collapses)")
        lines.append("")
        lines.append("The 6.6× lift survives some tests but fails or weakens on others. The specific failure modes above identify which conditions invalidate the apparent edge. Operational implication: too fragile to act on; the conditions under which it might be real are not yet meeting the bar for further investment.")
    lines.append("")
    lines.append(f"_Verdict marker (machine-readable): {verdict}_")
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    corpus = _load_attribution_corpus()
    if not corpus:
        logger.error("no full-completeness rows in attribution corpus")
        return 1
    logger.info("Falsifying with %d full-completeness trades", len(corpus))

    baseline_lift = 6.65  # from last session's headline
    results = {
        "a1": test_a1_multi_seed(corpus),
        "a2": test_a2_multiplier(corpus),
        "a3": test_a3_decomposition(corpus),
        "a4": test_a4_oos(),
        "a5": test_a5_adversarial(corpus),
    }

    md = render_verdict_doc(baseline_lift=baseline_lift, results=results)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(md, encoding="utf-8")
    logger.info("wrote %s", args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
