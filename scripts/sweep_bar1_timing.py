"""Sweep BAR-1 exit timing across hold-time alternatives, arena-driven.

Block E of tonight's plan. The first sweep that ACTUALLY exercises
arena (the catalyst gate sweep was rig-independent — used prod_pnl
directly). This sweep uses arena's bar data + FillModel to compute
hypothetical exit prices at different hold times.

CRITICAL DISCLAIMER (per brief discipline rule 2):
  This sweep uses MODELED exits, NOT prod-mirror. For each trade
  and each candidate hold time T+N, arena exits at the bar-open of
  the minute (entry_minute + N seconds → ceiling to next bar).
  This is mechanistically modeled, NOT what prod actually did at
  that hold time (because prod only exited once, at its actual exit
  time — there's no prod fill at T+90s for a trade that exited
  at T+60s).

  Therefore the EV numbers below are "what would arena have
  produced" — they are not "what prod would have produced."
  Calibration of arena's modeled exits against prod is the
  unfinished work (Block 2.1 spread calibration was reverted on
  stop condition; this sweep exposes the same uncertainty).

PROVISIONAL labeling: every output gets the disclaimer at the top.

Range: T+30s, T+60s, T+90s, T+120s, T+300s, T+15min, T+EOD

Output: docs/sweeps/bar1_timing_sweep.md
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from random import Random

logger = logging.getLogger("sweep_bar1_timing")

REPO_ROOT = Path(__file__).resolve().parent.parent
QTY_TRUTH = REPO_ROOT / "data" / "replay" / "prod_qty_truth.parquet"
ATTRIBUTION_DIR = REPO_ROOT / "data" / "instrumentation" / "trade_attribution"
ARENA_HISTORICAL = REPO_ROOT / "mx-arena" / "data" / "historical"
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "sweeps" / "bar1_timing_sweep.md"

sys.path.insert(0, str(REPO_ROOT / "mx-arena"))


HOLD_TIMES_SEC = [30, 60, 90, 120, 300, 900, 23400]  # 23400s = full session ≈ EOD


@dataclass
class SweepResult:
    ticker: str
    session_date: str
    entry_ts: str
    qty: int
    entry_px: float
    prod_pnl: float
    news_signal: str
    # One field per hold time
    pnl_t30s: float = 0.0
    pnl_t60s: float = 0.0
    pnl_t90s: float = 0.0
    pnl_t120s: float = 0.0
    pnl_t300s: float = 0.0
    pnl_t900s: float = 0.0
    pnl_eod: float = 0.0


# ── Load attribution corpus ───────────────────────────────────────


def load_attribution_corpus() -> list[dict]:
    if not ATTRIBUTION_DIR.exists():
        return []
    try:
        import pandas as pd
    except ImportError:
        return []
    rows = []
    for sd_dir in sorted(ATTRIBUTION_DIR.iterdir()):
        if not sd_dir.is_dir():
            continue
        target = sd_dir / "attribution.parquet"
        if not target.exists():
            continue
        df = pd.read_parquet(target)
        for _, r in df.iterrows():
            rows.append(dict(r))
    return rows


# ── Bar machinery (reused from arena_replay_session) ──────────────


def _load_bars_for(ticker: str, session_date: str):
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
    engine = DataEngine(clock=clock, historical_dir=str(ARENA_HISTORICAL))
    return engine._load_parquet_bars(ticker, session_date)


def _bar_at_or_after(bars_map: dict, ts: datetime):
    """Return the FIRST bar at-or-after ts (the next bar that would
    fill an exit submitted at ts)."""
    target_iso = ts.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    for _idx, bar in bars_map.items():
        if bar.timestamp >= target_iso:
            return bar
    # past end-of-bars: return the last bar (EOD)
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


def sweep_one(*, row: dict) -> SweepResult | None:
    """For one trade, simulate exits at each hold time."""
    from arena.fill_model import AlpacaFillModel
    from arena.spread_model import SpreadModel

    if int(row["prod_qty"]) <= 0 or float(row["prod_entry_avg_px"]) <= 0:
        return None

    bars = _load_bars_for(row["ticker"], row["session_date"])
    if bars is None:
        return None

    entry_ts = datetime.fromisoformat(row["entry_ts"])
    qty = int(row["prod_qty"])
    entry_px = float(row["prod_entry_avg_px"])

    fill_model = AlpacaFillModel()
    spread_model = SpreadModel()
    rng = Random(hash((row["ticker"], row["entry_ts"])) & 0xFFFFFFFF)

    result = SweepResult(
        ticker=row["ticker"], session_date=row["session_date"],
        entry_ts=row["entry_ts"], qty=qty, entry_px=entry_px,
        prod_pnl=float(row.get("prod_pnl", 0)),
        news_signal=row.get("news_signal", "NO_SIGNAL"),
    )

    for hold_sec in HOLD_TIMES_SEC:
        exit_ts = entry_ts + timedelta(seconds=hold_sec)
        exit_bar = _bar_at_or_after(bars, exit_ts)
        if exit_bar is None:
            continue
        exit_px = _arena_sell_fill(
            qty=qty, bar=exit_bar, ts=exit_ts,
            fill_model=fill_model, spread_model=spread_model, rng=rng,
        )
        pnl = (exit_px - entry_px) * qty
        attr_name = f"pnl_t{hold_sec}s" if hold_sec < 23400 else "pnl_eod"
        # Map odd second values to the attribute set
        attr_map = {
            30: "pnl_t30s", 60: "pnl_t60s", 90: "pnl_t90s",
            120: "pnl_t120s", 300: "pnl_t300s", 900: "pnl_t900s",
            23400: "pnl_eod",
        }
        attr_name = attr_map.get(hold_sec, attr_name)
        setattr(result, attr_name, round(pnl, 2))

    return result


def render_report(*, results: list[SweepResult]) -> str:
    lines = []
    lines.append("# BAR-1 Exit Timing Sweep — Arena-Driven (Block E)")
    lines.append("")
    lines.append("**Generated:** by `scripts/sweep_bar1_timing.py` against trade attribution corpus + bar parquets.")
    lines.append("**Trade window:** 4/22-4/28 deduped, intraday, with full attribution data.")
    lines.append("")
    lines.append("> **PROVISIONAL — MODELED EXITS, NOT PROD-MIRROR.** This sweep uses arena's `AlpacaFillModel` to compute hypothetical exit prices at each candidate hold time. There is NO prod fill at T+90s for a trade that exited at T+60s — these are arena-modeled what-ifs. The relative ranking across hold times is informative; the absolute P&L numbers are bounded by arena's slippage modeling fidelity (Block 2.1 calibration was reverted on stop condition; that uncertainty propagates here).")
    lines.append("")
    lines.append("> The CATALYST GATE SWEEP (`docs/sweeps/catalyst_gate_pareto.md`) was rig-independent. THIS sweep is rig-dependent. They cannot be combined into a single conclusion until arena fidelity is calibrated.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## §1 — Per-trade hold-time matrix")
    lines.append("")
    lines.append("Modeled arena P&L at each hold time. Compare to `prod_pnl` (actual realized) for context.")
    lines.append("")
    lines.append("| Ticker | Date | Signal | qty | entry | prod $ | T+30s | T+60s | T+90s | T+120s | T+300s | T+15min | EOD |")
    lines.append("|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in sorted(results, key=lambda x: (x.session_date, x.ticker)):
        lines.append(
            f"| {r.ticker} | {r.session_date} | {r.news_signal} | {r.qty} | "
            f"${r.entry_px:.2f} | ${r.prod_pnl:+,.2f} | "
            f"${r.pnl_t30s:+,.0f} | ${r.pnl_t60s:+,.0f} | "
            f"${r.pnl_t90s:+,.0f} | ${r.pnl_t120s:+,.0f} | "
            f"${r.pnl_t300s:+,.0f} | ${r.pnl_t900s:+,.0f} | "
            f"${r.pnl_eod:+,.0f} |"
        )
    lines.append("")

    # Aggregate per hold-time
    lines.append("## §2 — Aggregate Σ P&L per hold time")
    lines.append("")
    lines.append("| Hold time | Σ arena P&L | vs prod baseline |")
    lines.append("|---|---:|---:|")
    prod_total = sum(r.prod_pnl for r in results)
    lines.append(f"| **prod actual** | n/a | **${prod_total:+,.2f}** |")
    for hold_sec, label in [
        (30, "T+30s"), (60, "T+60s"), (90, "T+90s"), (120, "T+120s"),
        (300, "T+5min"), (900, "T+15min"), (23400, "EOD"),
    ]:
        attr = {30: "pnl_t30s", 60: "pnl_t60s", 90: "pnl_t90s",
                120: "pnl_t120s", 300: "pnl_t300s", 900: "pnl_t900s",
                23400: "pnl_eod"}[hold_sec]
        total = sum(getattr(r, attr) for r in results)
        delta = total - prod_total
        lines.append(f"| {label} | ${total:+,.2f} | ${delta:+,.2f} |")
    lines.append("")

    # Slice by news_signal
    lines.append("## §3 — Sliced by news_signal (small-sample caveat)")
    lines.append("")
    by_sig: dict[str, list[SweepResult]] = {}
    for r in results:
        by_sig.setdefault(r.news_signal, []).append(r)
    for sig in sorted(by_sig.keys()):
        rs = by_sig[sig]
        lines.append(f"### {sig} (n={len(rs)})")
        lines.append("")
        lines.append("| Hold time | Σ arena P&L | mean per trade |")
        lines.append("|---|---:|---:|")
        for hold_sec, label in [
            (60, "T+60s (BAR-1 default)"),
            (300, "T+5min"),
            (900, "T+15min"),
            (23400, "EOD"),
        ]:
            attr = {60: "pnl_t60s", 300: "pnl_t300s", 900: "pnl_t900s", 23400: "pnl_eod"}[hold_sec]
            total = sum(getattr(r, attr) for r in rs)
            mean = total / len(rs) if rs else 0
            lines.append(f"| {label} | ${total:+,.2f} | ${mean:+,.2f} |")
        lines.append("")

    lines.append("## §4 — Honest reading")
    lines.append("")
    lines.append("**What this sweep CAN say** (pattern-level, hold-time relative):")
    lines.append("- Whether longer holds capture more upside on BULL signals (or whether momentum decays past T+60s)")
    lines.append("- Whether NEUTRAL signals get worse with longer holds (consistent with the catalyst gate finding)")
    lines.append("- Whether STRONG_BULL benefits from holding past the BAR-1 default")
    lines.append("")
    lines.append("**What this sweep CANNOT say** (absolute, strategy-level):")
    lines.append("- Whether the strategy with hold-time X would profit at any specific number — arena's slippage uncertainty makes the absolute number untrustworthy")
    lines.append("- Whether the prod-actual exits were near-optimal — can't compare modeled-exit-at-T+N to a prod fill that didn't happen at T+N")
    lines.append("- Anything about the LIDR carry-class trades (filtered out of this sweep — only intraday trades with full attribution)")
    lines.append("")
    lines.append("**Stop conditions checked:**")
    eod_total = sum(r.pnl_eod for r in results)
    if eod_total > prod_total * 5:
        lines.append("- ⚠️ EOD-hold Σ is >5× prod baseline. Investigate before drawing any conclusion — likely simulator artifact.")
    elif eod_total < prod_total - 5000:
        lines.append("- ⚠️ EOD-hold Σ is significantly worse than prod. May reflect real signal decay OR slippage modeling underestimating the cost of long holds.")
    else:
        lines.append("- ✅ EOD-hold Σ within reasonable range vs prod baseline.")
    lines.append("")
    lines.append("**No promotion to prod from this sweep.** Halt switch stays on.")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    corpus = load_attribution_corpus()
    full_only = [r for r in corpus if r.get("data_completeness") == "full"]
    logger.info("loaded %d attribution rows; %d with full data", len(corpus), len(full_only))

    results = []
    for r in full_only:
        sw = sweep_one(row=r)
        if sw is not None:
            results.append(sw)
            logger.info(
                "  %s %s prod=$%+.2f T+60s=$%+.2f EOD=$%+.2f",
                sw.ticker, sw.session_date, sw.prod_pnl, sw.pnl_t60s, sw.pnl_eod,
            )

    if not results:
        logger.error("zero sweep results — no full-data trades with bars")
        return 1

    md = render_report(results=results)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(md, encoding="utf-8")
    logger.info("wrote %s", args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
