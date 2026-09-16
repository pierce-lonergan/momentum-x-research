"""Quantify what % of the prize Friday's lottery captured.

For each of the 8 positions opened on 2026-05-01:
  1. Pull 1-min bars from entry through EOD via Alpaca Market Data v2.
  2. Compute MFE (max favorable excursion) from entry to high.
  3. Compute MAE (max adverse excursion) from entry to low.
  4. Compute "actual capture" = (final_close - entry) / entry.
  5. Simulate trail-15% from entry: what would the trail have captured?
  6. Simulate trail-12% (the backtest's optimal width) hypothetical.
  7. Simulate scaled-exit ladder: 1/3 at +15%, 1/3 at +30%, 1/3 trail-15.
  8. Simulate VWAP-anchored stop: exit when price < VWAP for 3 consecutive bars.

Aggregate across all 8: how much $ of the available prize did we capture?

Output: data/lottery/friday_capture_analysis.json + console table
"""
from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

REPO = Path(__file__).resolve().parents[1]
OUT_DIR = REPO / "data" / "lottery"
OUT_DIR.mkdir(parents=True, exist_ok=True)
ET = ZoneInfo("America/New_York")

ALPACA_API_KEY = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_DATA_URL = os.environ.get("ALPACA_DATA_URL",
                                    "https://data.alpaca.markets").rstrip("/")

# From Friday's actual fills (Alpaca paper account state)
FRIDAY = "2026-05-01"
POSITIONS = [
    # (symbol, qty, avg_entry_price)
    ("HCAI", 22, 10.066364),
    ("MRAM", 13, 18.880000),
    ("RPGL", 128, 1.913906),
    ("RYOJ", 73, 3.310000),
    ("SKLZ", 31, 7.711291),
    ("VLN", 115, 2.060000),
    ("WNW", 58, 3.825861),
    ("XRX", 111, 2.265946),
]


async def fetch_bars(client: httpx.AsyncClient, symbol: str,
                       start_iso: str, end_iso: str) -> list[dict]:
    """Fetch 1-min bars between start and end. Handles pagination."""
    bars: list[dict] = []
    page_token = None
    while True:
        params = {
            "timeframe": "1Min", "start": start_iso, "end": end_iso,
            "adjustment": "raw", "feed": "iex", "limit": 10000,
        }
        if page_token:
            params["page_token"] = page_token
        r = await client.get(f"{ALPACA_DATA_URL}/v2/stocks/{symbol}/bars",
                              params=params)
        if r.status_code == 403:
            # IEX feed not entitled? try sip
            params["feed"] = "sip"
            r = await client.get(f"{ALPACA_DATA_URL}/v2/stocks/{symbol}/bars",
                                  params=params)
        r.raise_for_status()
        body = r.json()
        for b in body.get("bars", []) or []:
            bars.append(b)
        page_token = body.get("next_page_token")
        if not page_token:
            break
    return bars


def simulate_trail(bars: list[dict], entry_idx: int,
                    entry_px: float, trail_pct: float) -> dict:
    """Simulate trailing-stop exit from entry_idx forward.
    Returns {exit_px, exit_pct, exit_min_from_entry, exit_reason}."""
    running_high = entry_px
    for i, b in enumerate(bars[entry_idx:]):
        hi = float(b["h"]); lo = float(b["l"]); cl = float(b["c"])
        running_high = max(running_high, hi)
        trail_stop = running_high * (1 - trail_pct / 100)
        if lo <= trail_stop:
            return {
                "exit_px": trail_stop,
                "exit_pct": (trail_stop - entry_px) / entry_px,
                "exit_min_from_entry": i,
                "exit_reason": "trail",
            }
    last_close = float(bars[-1]["c"])
    return {
        "exit_px": last_close,
        "exit_pct": (last_close - entry_px) / entry_px,
        "exit_min_from_entry": len(bars) - entry_idx,
        "exit_reason": "eod",
    }


def simulate_ladder(bars: list[dict], entry_idx: int, entry_px: float) -> dict:
    """Sell 1/3 at +15%, 1/3 at +30%, 1/3 trails @ 15%."""
    running_high = entry_px
    legs = {"target_15": None, "target_30": None, "trail_15": None}
    for i, b in enumerate(bars[entry_idx:]):
        hi = float(b["h"]); lo = float(b["l"]); cl = float(b["c"])
        running_high = max(running_high, hi)
        if legs["target_15"] is None and hi >= entry_px * 1.15:
            legs["target_15"] = {"px": entry_px * 1.15, "pct": 0.15, "min": i}
        if legs["target_30"] is None and hi >= entry_px * 1.30:
            legs["target_30"] = {"px": entry_px * 1.30, "pct": 0.30, "min": i}
        if legs["trail_15"] is None:
            trail_stop = running_high * 0.85
            if lo <= trail_stop:
                legs["trail_15"] = {"px": trail_stop,
                                     "pct": (trail_stop - entry_px) / entry_px,
                                     "min": i}
    last_close = float(bars[-1]["c"])
    for k in legs:
        if legs[k] is None:
            legs[k] = {"px": last_close,
                        "pct": (last_close - entry_px) / entry_px,
                        "min": len(bars) - entry_idx, "fallback": "eod"}
    blended = sum(leg["pct"] for leg in legs.values()) / 3
    return {"legs": legs, "blended_pct": blended}


def simulate_vwap_stop(bars: list[dict], entry_idx: int,
                        entry_px: float, n_consec: int = 3) -> dict:
    """Exit when price closes below cumulative-VWAP for n_consec bars."""
    # Anchor VWAP at entry
    pv = 0.0; vol = 0
    consec_below = 0
    for i, b in enumerate(bars[entry_idx:]):
        typical = (float(b["h"]) + float(b["l"]) + float(b["c"])) / 3
        v = int(b["v"])
        pv += typical * v; vol += v
        vwap = pv / vol if vol else float(b["c"])
        cl = float(b["c"])
        if cl < vwap:
            consec_below += 1
        else:
            consec_below = 0
        if consec_below >= n_consec:
            return {"exit_px": cl, "exit_pct": (cl - entry_px) / entry_px,
                    "exit_min_from_entry": i, "exit_reason": "vwap_break"}
    last_close = float(bars[-1]["c"])
    return {"exit_px": last_close, "exit_pct": (last_close - entry_px) / entry_px,
            "exit_min_from_entry": len(bars) - entry_idx, "exit_reason": "eod"}


async def analyze_position(client: httpx.AsyncClient,
                            symbol: str, qty: int, entry_px: float) -> dict:
    """Full capture analysis for one position."""
    # Friday RTH: 09:30 to 16:00 ET (in UTC: 13:30 to 20:00)
    start_iso = f"{FRIDAY}T13:30:00Z"
    end_iso = f"{FRIDAY}T20:00:00Z"
    bars = await fetch_bars(client, symbol, start_iso, end_iso)
    if not bars:
        return {"symbol": symbol, "error": "no_bars"}

    # Find entry bar: our buys went out 09:30:01-09:30:21 ET, which means the
    # FIRST RTH bar (13:30 UTC) is our entry. We use the bar's close price
    # vs our actual avg fill, but for path simulation the bar timing is what
    # matters.
    entry_idx = 0

    # Stats
    rth_high = max(float(b["h"]) for b in bars)
    rth_low = min(float(b["l"]) for b in bars)
    rth_close = float(bars[-1]["c"])
    rth_total_vol = sum(int(b["v"]) for b in bars)

    # Time of high/low (minutes from open)
    high_idx = max(range(len(bars)), key=lambda i: float(bars[i]["h"]))
    low_idx = min(range(len(bars)), key=lambda i: float(bars[i]["l"]))

    mfe_pct = (rth_high - entry_px) / entry_px
    mae_pct = (rth_low - entry_px) / entry_px
    eod_pct = (rth_close - entry_px) / entry_px

    # Simulations from entry
    sim_trail_15 = simulate_trail(bars, entry_idx, entry_px, 15.0)
    sim_trail_12 = simulate_trail(bars, entry_idx, entry_px, 12.0)
    sim_trail_10 = simulate_trail(bars, entry_idx, entry_px, 10.0)
    sim_trail_25 = simulate_trail(bars, entry_idx, entry_px, 25.0)
    sim_ladder = simulate_ladder(bars, entry_idx, entry_px)
    sim_vwap = simulate_vwap_stop(bars, entry_idx, entry_px, n_consec=3)

    return {
        "symbol": symbol, "qty": qty, "entry_px": entry_px,
        "n_bars": len(bars),
        "rth_open_bar_open": float(bars[0]["o"]),
        "rth_open_bar_close": float(bars[0]["c"]),
        "rth_high": rth_high, "rth_low": rth_low, "rth_close": rth_close,
        "rth_total_vol": rth_total_vol,
        "mfe_pct": mfe_pct,
        "mae_pct": mae_pct,
        "eod_pct": eod_pct,
        "high_minute": high_idx,
        "low_minute": low_idx,
        # Dollar P&L per simulation
        "sim_eod_pnl": (rth_close - entry_px) * qty,
        "sim_mfe_pnl": (rth_high - entry_px) * qty,
        "sim_mae_pnl": (rth_low - entry_px) * qty,
        "sim_trail_15": {**sim_trail_15,
                          "pnl_usd": sim_trail_15["exit_pct"] * entry_px * qty},
        "sim_trail_12": {**sim_trail_12,
                          "pnl_usd": sim_trail_12["exit_pct"] * entry_px * qty},
        "sim_trail_10": {**sim_trail_10,
                          "pnl_usd": sim_trail_10["exit_pct"] * entry_px * qty},
        "sim_trail_25": {**sim_trail_25,
                          "pnl_usd": sim_trail_25["exit_pct"] * entry_px * qty},
        "sim_ladder": {**sim_ladder,
                        "pnl_usd": sim_ladder["blended_pct"] * entry_px * qty},
        "sim_vwap_stop": {**sim_vwap,
                           "pnl_usd": sim_vwap["exit_pct"] * entry_px * qty},
    }


async def main():
    if not ALPACA_API_KEY:
        raise RuntimeError("ALPACA_API_KEY not set")
    headers = {
        "APCA-API-KEY-ID": ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY,
    }
    print(f"=== Friday {FRIDAY} capture analysis ===\n")
    print(f"{'sym':6s}  {'entry':>7s}  {'high':>7s}  {'low':>7s}  {'close':>7s}  "
          f"{'MFE%':>7s}  {'MAE%':>7s}  {'EOD%':>7s}  {'trail15%':>8s}  "
          f"{'trail12%':>8s}  {'ladder%':>7s}  {'vwap%':>7s}  {'h_min':>5s}")
    async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
        results = []
        for sym, qty, entry_px in POSITIONS:
            r = await analyze_position(client, sym, qty, entry_px)
            results.append(r)
            if "error" in r:
                print(f"{sym:6s}  ERROR: {r['error']}")
                continue
            print(f"{r['symbol']:6s}  ${r['entry_px']:>6.2f}  "
                  f"${r['rth_high']:>6.2f}  ${r['rth_low']:>6.2f}  "
                  f"${r['rth_close']:>6.2f}  "
                  f"{r['mfe_pct']*100:>+6.2f}%  {r['mae_pct']*100:>+6.2f}%  "
                  f"{r['eod_pct']*100:>+6.2f}%  "
                  f"{r['sim_trail_15']['exit_pct']*100:>+7.2f}%  "
                  f"{r['sim_trail_12']['exit_pct']*100:>+7.2f}%  "
                  f"{r['sim_ladder']['blended_pct']*100:>+6.2f}%  "
                  f"{r['sim_vwap_stop']['exit_pct']*100:>+6.2f}%  "
                  f"{r['high_minute']:>5d}")

    # Aggregate dollars
    print("\n=== AGGREGATE $ P&L by exit strategy ===")
    n = len([r for r in results if "error" not in r])
    aggs = {
        "actual_eod": sum(r["sim_eod_pnl"] for r in results if "error" not in r),
        "perfect_mfe": sum(r["sim_mfe_pnl"] for r in results if "error" not in r),
        "trail_15": sum(r["sim_trail_15"]["pnl_usd"] for r in results if "error" not in r),
        "trail_12": sum(r["sim_trail_12"]["pnl_usd"] for r in results if "error" not in r),
        "trail_10": sum(r["sim_trail_10"]["pnl_usd"] for r in results if "error" not in r),
        "trail_25": sum(r["sim_trail_25"]["pnl_usd"] for r in results if "error" not in r),
        "ladder": sum(r["sim_ladder"]["pnl_usd"] for r in results if "error" not in r),
        "vwap_stop": sum(r["sim_vwap_stop"]["pnl_usd"] for r in results if "error" not in r),
    }
    deployed = sum(qty * px for _, qty, px in POSITIONS)
    print(f"Deployed: ${deployed:.2f} across {n} positions")
    for k, v in aggs.items():
        print(f"  {k:18s}: ${v:>+9.2f}  ({v/deployed*100:>+6.2f}% of deployed)")

    # Capture ratio
    print("\n=== CAPTURE RATIO (vs. perfect MFE = 100%) ===")
    mfe_total = aggs["perfect_mfe"]
    if mfe_total > 0:
        for k, v in aggs.items():
            if k == "perfect_mfe": continue
            pct = v / mfe_total * 100 if mfe_total > 0 else 0
            print(f"  {k:18s}: {pct:>+6.1f}% of perfect MFE")

    # Per-ticker capture detail
    print("\n=== PER-TICKER: actual EOD vs MFE prize ===")
    print(f"{'sym':6s}  {'mfe$':>9s}  {'actual$':>9s}  {'capture%':>9s}  "
          f"{'h_min':>5s}  {'note':40s}")
    for r in results:
        if "error" in r: continue
        mfe = r["sim_mfe_pnl"]; actual = r["sim_eod_pnl"]
        cap = (actual / mfe * 100) if mfe > 0 else (-100 if actual < 0 else 0)
        note = ""
        if r["mfe_pct"] > 0.30 and r["eod_pct"] < 0.10:
            note = "huge spike then faded"
        elif r["mfe_pct"] < 0.05 and r["eod_pct"] < 0:
            note = "never had a prize"
        elif r["mfe_pct"] > 0.20 and r["eod_pct"] > 0.10:
            note = "captured most"
        print(f"{r['symbol']:6s}  ${mfe:>+8.2f}  ${actual:>+8.2f}  "
              f"{cap:>+8.1f}%  {r['high_minute']:>5d}  {note}")

    out = {
        "friday": FRIDAY,
        "deployed_usd": deployed,
        "n_positions": n,
        "per_position": results,
        "aggregate_pnl_usd": aggs,
        "capture_ratio_vs_mfe": {k: (v/aggs["perfect_mfe"]*100) if aggs["perfect_mfe"]>0 else None
                                   for k, v in aggs.items() if k != "perfect_mfe"},
    }
    out_path = OUT_DIR / "friday_capture_analysis.json"
    out_path.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
