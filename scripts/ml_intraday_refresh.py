"""Intraday VETOED-tier refresh — second-pass scoring at 10:00 ET.

WHY: The meta-scorer's VETOED tier (v3t>=0.30 + MID-mag + TCN<0.30) returns
+12.74% / 51% win on n=73 in the WF (session 109). It cannot fire pre-market
because the TCN needs the first 30 RTH minutes of bars (9:30-10:00 ET).

This module:
  1. At 10:00 ET (or whenever 30+ RTH bars have accumulated), pull live
     minute bars for each currently-tracked candidate.
  2. Build the (6, 30) intraday-path tensor matching build_intraday_paths.py.
  3. Re-score each candidate via MetaScorer.score_candidate(intraday_path=path).
  4. Surface upgraded tier (especially BROAD -> VETOED transitions).

USAGE:
    # Standalone smoke test against historical data
    python scripts/ml_intraday_refresh.py --d0 2026-04-25 --tickers AAPL TSLA

    # As a library (called by lottery_runner during the trading day):
    from ml_intraday_refresh import build_path_from_bars, refresh_decisions

    bars = polygon_client.fetch_minute_bars(ticker, d0, end="10:00")
    path = build_path_from_bars(bars)  # (6, 30) numpy array
    decision = scorer.score_candidate(features, intraday_path=path, bankroll=bankroll)

OUTPUTS (smoke test mode):
    Console: tier transitions for each ticker
"""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DERIVED = REPO / "data" / "polygon_warehouse" / "derived"
MINUTE_GLOB = (REPO / "data" / "polygon_warehouse" / "minute_aggs"
                / "**" / "*.parquet").as_posix()
ET = ZoneInfo("America/New_York")

sys.path.insert(0, str(REPO / "scripts"))


def section(t: str) -> None:
    print(f"\n{'='*72}\n{t}\n{'='*72}")


def fetch_intraday_bars_from_warehouse(ticker: str, d0: str,
                                          start_min: int = 0,
                                          n_bars: int = 30) -> pd.DataFrame:
    """Pull first N RTH minute bars (9:30 + start_min onward) from local warehouse.

    For LIVE deployment, replace this with a Polygon REST/WS call. For
    backtest / smoke test, use the local minute_aggs warehouse.
    """
    con = duckdb.connect()
    con.sql("SET memory_limit='2GB'")
    rows = con.sql(f"""
        WITH raw AS (
            SELECT ts_et, open, high, low, close, volume, transactions,
                   ROW_NUMBER() OVER (ORDER BY ts_et) - 1 AS bar_idx
            FROM read_parquet('{MINUTE_GLOB}', hive_partitioning=true)
            WHERE ticker = '{ticker}'
              AND CAST(ts_et AS DATE) = DATE '{d0}'
              AND ((EXTRACT(hour FROM ts_et) = 9 AND EXTRACT(minute FROM ts_et) >= 30)
                   OR (EXTRACT(hour FROM ts_et) = 10 AND EXTRACT(minute FROM ts_et) < 30))
        )
        SELECT * FROM raw
        WHERE bar_idx >= {start_min} AND bar_idx < {start_min + n_bars}
        ORDER BY ts_et
    """).df()
    return rows


def build_path_from_alpaca_bars(alpaca_bars: list[dict], n_bars: int = 30) -> Optional[np.ndarray]:
    """Convert Alpaca /v2/stocks/{ticker}/bars JSON into (6, 30) tensor.

    Alpaca bar dict format:
      {"t": "2026-04-29T13:30:00Z", "o": 1.50, "h": 1.55, "l": 1.48,
       "c": 1.52, "v": 100000, "n": 432, "vw": 1.51}

    Mirrors build_path_from_bars (warehouse format) but accepts the LIVE
    broker output. Returns (6, 30) np.float32 array, or None if < 5 bars.
    """
    if not alpaca_bars or len(alpaca_bars) < 5:
        return None
    df = pd.DataFrame(alpaca_bars)
    df = df.rename(columns={"o": "open", "h": "high", "l": "low",
                              "c": "close", "v": "volume", "n": "transactions"})
    return build_path_from_bars(df, n_bars=n_bars)


def build_path_from_bars(bars: pd.DataFrame, n_bars: int = 30) -> Optional[np.ndarray]:
    """Convert minute bars into (6, 30) tensor matching the TCN's input format.

    Channels (mirror scripts/build_intraday_paths.py):
      0: open_rel    = (bar.open - rth_open) / rth_open
      1: high_rel
      2: low_rel
      3: close_rel
      4: vol_z       = z-score of bar.volume within day
      5: log_trans   = ln(transactions + 1)

    Returns (6, 30) np.float32 array, or None if insufficient bars (< 5 bars).
    Pads with zeros if fewer than n_bars present.
    """
    if len(bars) < 5:
        return None
    rth_open = float(bars["open"].iloc[0])
    if rth_open <= 0:
        return None
    mean_vol = float(bars["volume"].mean()) or 1.0
    std_vol = float(bars["volume"].std()) or 1.0

    X = np.zeros((6, n_bars), dtype=np.float32)
    for i, row in enumerate(bars.itertuples()):
        if i >= n_bars:
            break
        X[0, i] = (row.open - rth_open) / rth_open
        X[1, i] = (row.high - rth_open) / rth_open
        X[2, i] = (row.low - rth_open) / rth_open
        X[3, i] = (row.close - rth_open) / rth_open
        X[4, i] = (row.volume - mean_vol) / std_vol if std_vol > 0 else 0.0
        X[5, i] = float(np.log(getattr(row, "transactions", 0) + 1))
    # Replace inf/nan
    X = np.nan_to_num(X, nan=0.0, posinf=10.0, neginf=-10.0)
    return X


def refresh_decision(scorer, features: dict, ticker: str, d0: str,
                      bankroll: float = 10_000.0) -> dict:
    """Re-score a candidate after 30 RTH min using its intraday path.

    Returns a dict with both pre-bar and post-bar decisions for comparison.
    """
    from ml_meta_scorer_inference import MetaDecision  # noqa
    # Pre-bar decision (same as morning)
    pre = scorer.score_candidate(features, intraday_path=None, bankroll=bankroll)

    # Build path
    bars = fetch_intraday_bars_from_warehouse(ticker, d0)
    path = build_path_from_bars(bars)
    if path is None:
        return {
            "ticker": ticker, "d0": d0,
            "pre_tier": pre.tier, "post_tier": pre.tier,
            "tier_changed": False,
            "pre_score": pre.meta_score,
            "post_score": pre.meta_score,
            "tcn_proba": None, "n_bars": len(bars),
            "note": "insufficient bars; no refresh",
        }

    # Post-bar decision
    post = scorer.score_candidate(features, intraday_path=path, bankroll=bankroll)
    return {
        "ticker": ticker, "d0": d0,
        "pre_tier": pre.tier, "post_tier": post.tier,
        "tier_changed": pre.tier != post.tier,
        "pre_score": pre.meta_score,
        "post_score": post.meta_score,
        "pre_kelly": pre.kelly_frac, "post_kelly": post.kelly_frac,
        "pre_notional": pre.notional_usd, "post_notional": post.notional_usd,
        "tcn_proba": post.tcn_proba, "n_bars": len(bars),
        "reason": post.reason,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--d0", required=True, help="Trading date (YYYY-MM-DD)")
    parser.add_argument("--tickers", nargs="+", required=True,
                        help="Ticker symbols to refresh")
    parser.add_argument("--bankroll", type=float, default=10_000.0)
    args = parser.parse_args()

    from ml_meta_scorer_inference import MetaScorer

    section(f"INTRADAY REFRESH: d0={args.d0}, n_tickers={len(args.tickers)}")
    scorer = MetaScorer.load_default()
    print(f"  scorer loaded: features={len(scorer.feature_columns)}, "
          f"mag_5d={scorer.mag_5d:+.4f} ({scorer.mag_label})")

    # Stub features: in production, lottery_runner has full feature dicts.
    # Here we use a generic plausible set per ticker for smoke testing.
    section("Per-ticker refresh decisions")
    print(f"  {'ticker':<8} {'pre_tier':<8} {'post_tier':<8} "
          f"{'tcn':>6} {'pre_$':>8} {'post_$':>8} {'change':<8}")
    upgrades = 0
    downgrades = 0
    transitions: dict = {}
    for ticker in args.tickers:
        # Stub features (caller provides real ones in production)
        features = {
            "ticker": ticker, "log_open": 1.5, "log_dvol_d0": 16.0,
            "intraday_pct": 0.30, "ret_open_close_d0": 0.20,
            "rank_intra": 5, "rank_intra_log": 1.79,
            "prior_n": 5, "prior_cont_rate": 30.0,
        }
        try:
            r = refresh_decision(scorer, features, ticker, args.d0,
                                  bankroll=args.bankroll)
        except Exception as e:
            print(f"  {ticker:<8} ERROR: {e}")
            continue

        change = ""
        if r["tier_changed"]:
            tiers = ["SKIP", "BROAD", "VETOED", "HIGH", "ELITE"]
            try:
                pre_idx = tiers.index(r["pre_tier"])
                post_idx = tiers.index(r["post_tier"])
                if post_idx > pre_idx:
                    change = "UPGRADE"
                    upgrades += 1
                else:
                    change = "DOWNGRADE"
                    downgrades += 1
            except ValueError:
                change = "CHANGE"
            key = f"{r['pre_tier']}->{r['post_tier']}"
            transitions[key] = transitions.get(key, 0) + 1
        tcn_str = f"{r.get('tcn_proba', 0) or 0:.2f}" if r.get('tcn_proba') is not None else "  --"
        print(f"  {ticker:<8} {r['pre_tier']:<8} {r['post_tier']:<8} "
              f"{tcn_str:>6} ${r.get('pre_notional', 0):>7.2f} "
              f"${r.get('post_notional', 0):>7.2f} {change:<8}")

    section("Summary")
    print(f"  upgrades:   {upgrades}")
    print(f"  downgrades: {downgrades}")
    print(f"  transitions: {transitions}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
