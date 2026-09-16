"""D308/D309 (2026-05-19) — Stop-widening shadow replayer.

PURPOSE — T1 of the validation plan (doc 169):
  Reads stop_decision events emitted by ``src/analysis/stop_decision_log.py``
  and simulates what each trade would have done under the wide ATR/gap-floor
  stop instead of the D142 Phase 1 tight stop. Output is a rolling parquet
  ledger comparing actual-vs-hypothetical P&L per event.

CORE QUESTION:
  If we had used the orchestrator's wide stop (verdict.stop_loss) instead
  of the Phase 1 tightened stop on every trade in the last N days, what
  would the hit rate and aggregate P&L have been?

SIMULATION RULES (per event):
  1. Pull minute bars for the symbol from polygon_warehouse minute_aggs,
     starting at the event timestamp through the end of the session.
  2. Walk forward minute-by-minute. At each bar check (in priority order):
       a. Stop hit?   ``low <= atr_stop``   -> exit @ atr_stop  (stopped)
       b. T1 hit?     ``high >= +3%``       -> exit 33% @ +3%   (tranche 1)
       c. T2 hit?     ``high >= +6%``       -> exit 33% @ +6%   (tranche 2)
       d. T3 hit?     ``high >= +10%``      -> exit 34% @ +10%  (tranche 3)
  3. If no exit triggered, close at end-of-session at the last close
     (matches D278 t1_next_open behavior — except we don't carry overnight
     in the shadow simulation; defer that to a follow-up if/when D278 is
     reverted).
  4. Net P&L = sum(exit_price * fraction * qty) - entry * qty.

USAGE:
  python scripts/stop_widening_replay.py
    [--shadow-dir PATH]   default data/shadow_stops/
    [--out PATH]          default data/shadow_stops/replay_results.parquet
    [--symbol TICKER]     filter to one ticker (debugging)
    [--dry-run]           print results, don't write parquet

The launcher (daily_data_ingest.ps1) calls this every night after the
ingest, so the ledger grows by 5-8 rows per trading day. After ~3 days
we have ~15-25 simulated trades to compare against actuals.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DEFAULT_SHADOW_DIR = REPO / "data" / "shadow_stops"
DEFAULT_OUT = DEFAULT_SHADOW_DIR / "replay_results.parquet"
WAREHOUSE_MIN = REPO / "data" / "polygon_warehouse" / "minute_aggs"

# Target ladder: matches D106 PROMOTIONAL_EARLY targets [+3, +6, +10]%.
# Fractions sum to 1.0 (last tranche absorbs rounding).
TARGET_PCTS = [0.03, 0.06, 0.10]
TARGET_FRACTIONS = [0.33, 0.33, 0.34]
# End-of-session anchor in ET — minute bars after this close the simulation.
EOS_HOUR_ET = 15
EOS_MIN_ET = 55


def _section(s: str) -> None:
    print(f"\n{'=' * 72}\n{s}\n{'=' * 72}", flush=True)


def load_stop_decisions(shadow_dir: Path) -> pd.DataFrame:
    """Read every stop_decisions_*.jsonl file in shadow_dir into one frame."""
    rows: list[dict] = []
    for f in sorted(shadow_dir.glob("stop_decisions_*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if ev.get("event") != "stop_decision":
                continue
            ev["_source_file"] = f.name
            rows.append(ev)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    # 2026-05-24 fix: live emission writes ".123456+00:00", backfill writes
    # "-04:00" or "+00:00" (no microseconds). pandas can't auto-infer
    # mixed -- need format='ISO8601' to accept both.
    df["ts"] = pd.to_datetime(df["ts"], utc=True, format="ISO8601")
    df["session_date"] = df["ts"].dt.tz_convert("America/New_York").dt.date
    return df


def fetch_minute_bars(symbol: str, session_date: pd.Timestamp,
                        from_ts_et: pd.Timestamp) -> pd.DataFrame:
    """Pull minute bars for ``symbol`` between ``from_ts_et`` and end of
    session. Returns DataFrame with ts_et (ET, tz-aware), high, low, close.
    Empty DataFrame if no warehouse partition or no rows found.
    """
    yr = session_date.year
    mo = f"{session_date.month:02d}"
    part = WAREHOUSE_MIN / f"year={yr}" / f"month={mo}" / "data.parquet"
    if not part.exists():
        return pd.DataFrame()
    eos = from_ts_et.replace(hour=EOS_HOUR_ET, minute=EOS_MIN_ET,
                              second=0, microsecond=0)
    con = duckdb.connect()
    try:
        df = con.sql(f"""
            SELECT ts_et, open, high, low, close, volume
            FROM read_parquet('{part.as_posix()}')
            WHERE ticker = '{symbol}'
              AND ts_et >= TIMESTAMPTZ '{from_ts_et.isoformat()}'
              AND ts_et <= TIMESTAMPTZ '{eos.isoformat()}'
            ORDER BY ts_et
        """).df()
    except Exception:
        return pd.DataFrame()
    return df


def simulate(entry_price: float, atr_stop: float, bars: pd.DataFrame
             ) -> dict:
    """Walk forward through bars; return per-tranche exit details.

    Returns dict with:
      - exit_strategy: "stopped" | "targets_partial" | "targets_full" | "eos"
      - tranches_hit: 0..3
      - effective_exit_pct: weighted average exit pct
      - stop_hit_minute: int (minute offset from entry, None if not hit)
      - first_target_minute: int (offset to first +3% touch, None if never)
      - max_high_pct: max intraday excursion (entry-relative)
    """
    if bars.empty:
        return {"exit_strategy": "no_data", "tranches_hit": 0,
                "effective_exit_pct": 0.0, "stop_hit_minute": None,
                "first_target_minute": None, "max_high_pct": 0.0}

    targets_price = [entry_price * (1 + p) for p in TARGET_PCTS]
    tranches_hit = 0
    weighted_exits: list[tuple[float, float]] = []  # (frac, exit_px)
    stop_hit_minute: int | None = None
    first_target_minute: int | None = None
    max_high_pct = 0.0

    for i, row in enumerate(bars.itertuples(index=False)):
        hi, lo = float(row.high), float(row.low)
        max_high_pct = max(max_high_pct, (hi / entry_price - 1.0) * 100)
        # Stop hit?
        if lo <= atr_stop:
            # Remainder closes at stop
            remaining_frac = 1.0 - sum(f for f, _ in weighted_exits)
            if remaining_frac > 1e-9:
                weighted_exits.append((remaining_frac, atr_stop))
            stop_hit_minute = i
            break
        # Targets — tranches fire in priority order (lowest first)
        for ti in range(tranches_hit, len(TARGET_PCTS)):
            if hi >= targets_price[ti]:
                weighted_exits.append((TARGET_FRACTIONS[ti], targets_price[ti]))
                tranches_hit += 1
                if first_target_minute is None:
                    first_target_minute = i
            else:
                break
        if tranches_hit == len(TARGET_PCTS):
            # All targets hit — done
            break
    else:
        # Loop completed without break = no stop and not full targets — EOS
        remaining_frac = 1.0 - sum(f for f, _ in weighted_exits)
        if remaining_frac > 1e-9:
            eos_price = float(bars.iloc[-1].close)
            weighted_exits.append((remaining_frac, eos_price))

    # Compute weighted exit pct
    if weighted_exits:
        effective_exit_px = sum(f * px for f, px in weighted_exits)
        effective_exit_pct = (effective_exit_px / entry_price - 1.0) * 100
    else:
        effective_exit_pct = 0.0
        effective_exit_px = entry_price

    # Strategy label
    if stop_hit_minute is not None and tranches_hit == 0:
        strat = "stopped"
    elif tranches_hit == len(TARGET_PCTS):
        strat = "targets_full"
    elif tranches_hit > 0:
        strat = "targets_partial"
    else:
        strat = "eos"

    return {
        "exit_strategy": strat,
        "tranches_hit": tranches_hit,
        "effective_exit_pct": round(effective_exit_pct, 3),
        "effective_exit_price": round(effective_exit_px, 4),
        "stop_hit_minute": stop_hit_minute,
        "first_target_minute": first_target_minute,
        "max_high_pct": round(max_high_pct, 3),
    }


def replay(events: pd.DataFrame, symbol_filter: str | None = None) -> pd.DataFrame:
    """Run simulate() for every event and return a DataFrame of results."""
    if events.empty:
        return pd.DataFrame()
    if symbol_filter:
        events = events[events["symbol"] == symbol_filter.upper()]

    out_rows: list[dict] = []
    for ev in events.to_dict("records"):
        symbol = ev["symbol"]
        entry = float(ev["entry"])
        atr_stop = float(ev["atr_stop"])
        phase1_stop = ev.get("phase1_stop") or atr_stop
        qty = int(ev["qty"])
        qty_halved = int(ev["qty_halved_hypothetical"])
        sd = ev["session_date"]
        from_ts_et = (pd.Timestamp(ev["ts"]).tz_convert("America/New_York"))
        bars = fetch_minute_bars(symbol, pd.Timestamp(sd), from_ts_et)
        # Run BOTH simulations -- the atr-stop arm AND the phase1-stop
        # arm (sanity check: phase1 should usually match actual outcome).
        sim_atr = simulate(entry, float(atr_stop), bars)
        sim_p1 = simulate(entry, float(phase1_stop), bars)

        # Dollar P&L per arm using respective sizing
        full_qty_pnl_atr = (sim_atr["effective_exit_price"] - entry) * qty
        half_qty_pnl_atr = (sim_atr["effective_exit_price"] - entry) * qty_halved
        full_qty_pnl_p1 = (sim_p1["effective_exit_price"] - entry) * qty
        delta_pnl = full_qty_pnl_atr - full_qty_pnl_p1

        out_rows.append({
            "session_date": sd,
            "symbol": symbol,
            "ts_et": from_ts_et,
            "entry": entry,
            "atr_stop": float(atr_stop),
            "atr_dist_pct": ev.get("atr_dist_pct"),
            "phase1_stop": float(phase1_stop),
            "phase1_dist_pct": ev.get("phase1_dist_pct"),
            "submitted_stop": float(ev["submitted_stop"]),
            "submitted_strategy": ev["submitted_strategy"],
            "qty_actual": qty,
            "qty_halved": qty_halved,
            "gap_pct": ev.get("gap_pct"),
            "mfcs": ev.get("mfcs"),
            "n_bars_available": 0 if bars.empty else len(bars),
            # ATR-stop arm
            "atr_exit_pct": sim_atr["effective_exit_pct"],
            "atr_exit_strategy": sim_atr["exit_strategy"],
            "atr_tranches_hit": sim_atr["tranches_hit"],
            "atr_max_high_pct": sim_atr["max_high_pct"],
            "atr_stop_hit_minute": sim_atr["stop_hit_minute"],
            "atr_first_target_minute": sim_atr["first_target_minute"],
            "atr_pnl_fullsize": round(full_qty_pnl_atr, 2),
            "atr_pnl_halfsize": round(half_qty_pnl_atr, 2),
            # Phase1-stop arm (the actual production behavior)
            "phase1_exit_pct": sim_p1["effective_exit_pct"],
            "phase1_exit_strategy": sim_p1["exit_strategy"],
            "phase1_pnl_fullsize": round(full_qty_pnl_p1, 2),
            # Delta: positive = wide stop would have done better
            "delta_pnl_atr_vs_phase1": round(delta_pnl, 2),
        })
    return pd.DataFrame(out_rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shadow-dir", type=Path, default=DEFAULT_SHADOW_DIR)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--symbol", type=str, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    _section("STEP 1 -- load stop_decision events")
    events = load_stop_decisions(args.shadow_dir)
    if events.empty:
        print(f"  no stop_decisions found in {args.shadow_dir}")
        return 0
    print(f"  loaded {len(events)} events across {events['session_date'].nunique()} "
          f"trading day(s)")

    _section("STEP 2 -- replay each event vs minute bars")
    results = replay(events, symbol_filter=args.symbol)
    if results.empty:
        print("  no results (replay returned empty)")
        return 0

    _section("STEP 3 -- aggregate")
    n_with_bars = (results["n_bars_available"] > 0).sum()
    print(f"  events:                {len(results)}")
    print(f"  with minute-bar data:  {n_with_bars} / {len(results)}")
    if n_with_bars > 0:
        replayable = results[results["n_bars_available"] > 0]
        atr_mean = replayable["atr_exit_pct"].mean()
        p1_mean = replayable["phase1_exit_pct"].mean()
        atr_wins = (replayable["atr_exit_pct"] > 0).sum()
        atr_full_pnl = replayable["atr_pnl_fullsize"].sum()
        atr_half_pnl = replayable["atr_pnl_halfsize"].sum()
        p1_full_pnl = replayable["phase1_pnl_fullsize"].sum()
        delta = atr_full_pnl - p1_full_pnl
        print(f"  ATR-stop mean exit:    {atr_mean:+.2f}%   "
              f"win_rate: {atr_wins}/{n_with_bars} = {atr_wins/n_with_bars*100:.0f}%")
        print(f"  Phase1-stop mean exit: {p1_mean:+.2f}%   "
              f"(actual production behavior approximation)")
        print(f"  ATR-stop arm dollar P&L (full qty): ${atr_full_pnl:+,.2f}")
        print(f"  ATR-stop arm dollar P&L (half qty): ${atr_half_pnl:+,.2f}  "
              "<-- T2 A/B arm sizing")
        print(f"  Phase1 dollar P&L (full qty):       ${p1_full_pnl:+,.2f}")
        print(f"  DELTA (ATR - Phase1) full qty:      ${delta:+,.2f}  "
              "<-- positive = wide stop wins")

    _section("STEP 4 -- write results")
    if args.dry_run:
        print(f"  --dry-run: skipping write")
        print(results.tail(10).to_string())
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        results.to_parquet(args.out, compression="zstd")
        print(f"  wrote {len(results)} rows -> {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
