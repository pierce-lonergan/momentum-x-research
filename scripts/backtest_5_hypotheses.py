"""Unified backtest harness for the five §7 hypotheses + bonus fresh ones.

H1: Late-day entry sweep (11:30 / 12:00 / 13:00 ET) — same top-K first-30-min
    ranking but defer entry.
H2: VWAP reclaim — buy on cross back above anchored VWAP after a pullback
    from morning high.
H3: Short the morning ripper — top-K first-30-min ranking, but SHORT entry
    at 10:00 with stop +10% (against), target -20% (with).
H4: MAGNA-N intersection — same first-30-min top-K but restrict to tickers
    passing N (mcap≤$1B) and at least one of M / A.
H5: Consolidation breakout (fresh-perspective hypothesis) — morning push,
    then base-build, then break above morning high.

All five share a single bar-load pass for efficiency.

Outputs:
  data/audits/h{1-5}_backtest.parquet        — per-trade results
  data/audits/h_5_summary.json               — comparative table

Each hypothesis's `run_*` function returns: list[dict] of trades.
A trade dict has: date, ticker, entry_ts, entry_px, exit_ts, exit_px,
                  exit_reason, pnl_pct, hold_min, side ("long"/"short"),
                  hypothesis, strategy_label.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
BAR_DIR = REPO / "data" / "bar_recordings"
FUND_DIR = REPO / "data" / "polygon_backfill" / "fundamentals"
OUT_DIR = REPO / "data" / "audits"
OUT_DIR.mkdir(parents=True, exist_ok=True)

ET = ZoneInfo("America/New_York")


# ───────────────────────────── helpers ─────────────────────────────

def parse_ts(s: str) -> datetime:
    if s.endswith("Z"):
        return datetime.fromisoformat(s[:-1]).replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(s)


def is_rth(ts_et: datetime) -> bool:
    h, m = ts_et.hour, ts_et.minute
    if h < 9 or h > 16: return False
    if h == 9 and m < 30: return False
    if h == 16: return False
    return True


def load_bars(path: Path) -> list[tuple[datetime, dict]]:
    obj = json.loads(path.read_text())
    bars = obj.get("bars") or []
    out = []
    for b in bars:
        try:
            ts = parse_ts(b["timestamp"]).astimezone(ET)
        except Exception as e:
            print(f"WARN: load_bars: bad timestamp in {path}: {e}")
            continue
        if is_rth(ts):
            out.append((ts, b))
    out.sort(key=lambda x: x[0])
    return out


def cumulative_vwap(rth_bars: list) -> list[float]:
    """Anchored VWAP from RTH open."""
    vwap = []
    pv_sum = 0.0
    v_sum = 0
    for _, b in rth_bars:
        typical = (float(b["high"]) + float(b["low"]) + float(b["close"])) / 3.0
        v = int(b["volume"])
        pv_sum += typical * v
        v_sum += v
        vwap.append(pv_sum / v_sum if v_sum else float(b["close"]))
    return vwap


def first_30_max_return(rth_bars: list) -> tuple[float | None, datetime, float]:
    if not rth_bars: return None, None, None
    open_ts = rth_bars[0][0]
    rth_open = float(rth_bars[0][1]["open"])
    end_ts = open_ts + pd.Timedelta(minutes=30)
    sl = [(ts, b) for ts, b in rth_bars if ts < end_ts]
    if not sl or rth_open <= 0: return None, end_ts, rth_open
    sl_high = max(float(b["high"]) for _, b in sl)
    return (sl_high - rth_open) / rth_open, end_ts, rth_open


def simulate_long_trade(rth_bars: list, entry_ts: datetime, entry_px: float,
                        target_pct: float | None, stop_pct: float | None,
                        time_stop_minute: int = 55, time_stop_hour: int = 15) -> dict:
    if entry_px <= 0:
        return {"exit_reason": "bad_entry", "exit_pct": 0.0, "hold_min": 0, "exit_px": entry_px, "exit_ts": entry_ts}
    target_px = entry_px * (1 + target_pct) if target_pct is not None else None
    stop_px = entry_px * (1 + stop_pct) if stop_pct is not None else None
    time_stop_ts = entry_ts.replace(hour=time_stop_hour, minute=time_stop_minute, second=0)
    last_close = entry_px
    last_ts = entry_ts
    hold_min = 0
    for ts, b in rth_bars:
        if ts < entry_ts: continue
        hi, lo, cl = float(b["high"]), float(b["low"]), float(b["close"])
        last_close = cl; last_ts = ts
        hold_min = int((ts - entry_ts).total_seconds() // 60)
        # Conservative: stop fills before target if both touched in same bar
        if stop_px is not None and lo <= stop_px:
            return {"exit_reason": "stop", "exit_pct": stop_pct, "hold_min": hold_min,
                    "exit_px": stop_px, "exit_ts": ts}
        if target_px is not None and hi >= target_px:
            return {"exit_reason": "target", "exit_pct": target_pct, "hold_min": hold_min,
                    "exit_px": target_px, "exit_ts": ts}
        if ts >= time_stop_ts:
            return {"exit_reason": "time_stop", "exit_pct": (cl - entry_px) / entry_px,
                    "hold_min": hold_min, "exit_px": cl, "exit_ts": ts}
    return {"exit_reason": "eod", "exit_pct": (last_close - entry_px) / entry_px,
            "hold_min": hold_min, "exit_px": last_close, "exit_ts": last_ts}


def simulate_short_trade(rth_bars: list, entry_ts: datetime, entry_px: float,
                         target_drop_pct: float, stop_rise_pct: float,
                         time_stop_minute: int = 55, time_stop_hour: int = 15) -> dict:
    """For shorts: target_drop_pct = how far DOWN price needs to go (e.g. 0.20 = -20%)
       stop_rise_pct = how far UP price can go before we cover (e.g. 0.10 = +10% against us).
       PnL is reported as gain to the SHORT position."""
    if entry_px <= 0:
        return {"exit_reason": "bad_entry", "exit_pct": 0.0, "hold_min": 0, "exit_px": entry_px, "exit_ts": entry_ts}
    target_px = entry_px * (1 - target_drop_pct)
    stop_px = entry_px * (1 + stop_rise_pct)
    time_stop_ts = entry_ts.replace(hour=time_stop_hour, minute=time_stop_minute, second=0)
    last_close = entry_px; last_ts = entry_ts; hold_min = 0
    for ts, b in rth_bars:
        if ts < entry_ts: continue
        hi, lo, cl = float(b["high"]), float(b["low"]), float(b["close"])
        last_close = cl; last_ts = ts
        hold_min = int((ts - entry_ts).total_seconds() // 60)
        # Stop fills before target if both touched
        if hi >= stop_px:
            return {"exit_reason": "stop", "exit_pct": -stop_rise_pct, "hold_min": hold_min,
                    "exit_px": stop_px, "exit_ts": ts}
        if lo <= target_px:
            return {"exit_reason": "target", "exit_pct": target_drop_pct, "hold_min": hold_min,
                    "exit_px": target_px, "exit_ts": ts}
        if ts >= time_stop_ts:
            return {"exit_reason": "time_stop", "exit_pct": (entry_px - cl) / entry_px,
                    "hold_min": hold_min, "exit_px": cl, "exit_ts": ts}
    return {"exit_reason": "eod", "exit_pct": (entry_px - last_close) / entry_px,
            "hold_min": hold_min, "exit_px": last_close, "exit_ts": last_ts}


# ───────────────────────────── data load ─────────────────────────────

def load_all_dates() -> dict:
    """Returns {date_str: [{ticker, rth, fundamentals}, ...]}."""
    print("Loading bar recordings ...")
    by_date: dict = {}
    n_loaded = 0
    for d in sorted(BAR_DIR.iterdir()):
        if not d.is_dir(): continue
        date_str = d.name
        per_day = []
        for f in d.glob("*.json"):
            ticker = f.stem
            try:
                rth = load_bars(f)
            except Exception as e:
                print(f"WARN: load_bars failed for {ticker}: {e}")
                continue
            if not rth or len(rth) < 30: continue
            per_day.append({"ticker": ticker, "rth": rth})
            n_loaded += 1
        if per_day: by_date[date_str] = per_day
    print(f"  Loaded {n_loaded} (date,ticker) bar files across {len(by_date)} dates.")
    return by_date


def load_fund(ticker: str) -> dict | None:
    p = FUND_DIR / f"{ticker}.json"
    if not p.exists(): return None
    try: return json.loads(p.read_text())
    except Exception: return None


def magna_score(ticker: str, gap_pct: float | None) -> tuple[int, dict]:
    """Returns (score, components)."""
    fund = load_fund(ticker)
    components = {"M": False, "A": False, "G": False, "N": False, "type": None}
    if not fund: return 0, components
    details = fund.get("details") or {}
    components["type"] = details.get("type", "?")
    fins = fund.get("financials") or []

    # M: net_income growth across 4 quarters
    if len(fins) >= 2:
        try:
            li = fins[0].get("net_income_loss")
            oi = fins[-1].get("net_income_loss")
            if li is not None and oi is not None and oi != 0:
                growth = (li - oi) / abs(oi) * 100
                components["M"] = growth >= 100.0
        except Exception as e:
            print(f"WARN: M-component (net_income growth) parse failed: {e}")

    # A: revenue acceleration QoQ ≥10% for 2 consecutive
    if len(fins) >= 3:
        try:
            rev = [f.get("revenues") for f in fins[:3]]
            if all(r is not None and r != 0 for r in rev):
                qoq1 = (rev[0] - rev[1]) / rev[1] * 100
                qoq2 = (rev[1] - rev[2]) / rev[2] * 100
                components["A"] = (qoq1 >= 10.0 and qoq2 >= 10.0)
        except Exception as e:
            print(f"WARN: A-component (revenue accel) parse failed: {e}")

    # G: gap-up ≥10% on entry day
    if gap_pct is not None and gap_pct >= 0.10:
        components["G"] = True

    # N: mcap ≤ $1B
    mc = details.get("market_cap")
    if mc is not None and mc <= 1e9:
        components["N"] = True

    score = sum([components["M"], components["A"], components["G"], components["N"]])
    return score, components


# ───────────────────────────── H1: late-day entry ─────────────────────────────

def run_h1_late_day(by_date: dict, top_k: int = 3) -> list[dict]:
    """Top-K of first-30-min momentum, but defer entry to 11:30 / 12:00 / 13:00 ET.
    Same +20%/-10%/EOD exit logic.
    """
    trades = []
    for entry_hour, entry_min in [(11, 30), (12, 0), (13, 0)]:
        label = f"H1_entry_{entry_hour:02d}{entry_min:02d}_top{top_k}"
        for date_str, tickers in by_date.items():
            scored = []
            for t in tickers:
                f30, _, _ = first_30_max_return(t["rth"])
                if f30 is None: continue
                scored.append((t, f30))
            scored.sort(key=lambda x: -x[1])
            picks = scored[:top_k]
            for t, f30 in picks:
                rth = t["rth"]
                # Find entry bar at/after entry_hour:entry_min ET
                entry_ts_target = rth[0][0].replace(hour=entry_hour, minute=entry_min, second=0)
                entry_bar = next((b for ts, b in rth if ts >= entry_ts_target), None)
                if entry_bar is None: continue
                entry_px = float(entry_bar["open"])
                actual_entry_ts = next(ts for ts, b in rth if ts >= entry_ts_target)
                sim = simulate_long_trade(rth, actual_entry_ts, entry_px, 0.20, -0.10)
                trades.append({
                    "hypothesis": "H1", "strategy_label": label,
                    "date": date_str, "ticker": t["ticker"], "side": "long",
                    "entry_ts": actual_entry_ts.isoformat(), "entry_px": entry_px,
                    "first_30_max": f30,
                    "exit_reason": sim["exit_reason"], "pnl_pct": sim["exit_pct"],
                    "hold_min": sim["hold_min"], "exit_px": sim["exit_px"],
                    "exit_ts": sim["exit_ts"].isoformat() if hasattr(sim["exit_ts"], "isoformat") else sim["exit_ts"],
                })
    return trades


# ───────────────────────────── H2: VWAP reclaim ─────────────────────────────

def run_h2_vwap_reclaim(by_date: dict, min_morning_high_pct: float = 0.20,
                         min_pullback_pct: float = 0.10,
                         require_vwap_break: bool = True) -> list[dict]:
    """Pattern:
       1) Stock reaches morning_high ≥ +20% from open at some point t1.
       2) Price drops to ≤ VWAP after t1 (the pullback).
       3) Price closes back above VWAP — that's the entry.
       4) Exit: +20% / -10% / EOD time-stop.
    """
    trades = []
    label = f"H2_vwap_reclaim_morn{int(min_morning_high_pct*100)}_pull{int(min_pullback_pct*100)}"
    for date_str, tickers in by_date.items():
        for t in tickers:
            rth = t["rth"]
            if len(rth) < 60: continue
            rth_open = float(rth[0][1]["open"])
            if rth_open <= 0: continue
            vwap_arr = cumulative_vwap(rth)
            morning_high = rth_open
            morning_high_idx = None
            pulled_back = False
            entry_done = False
            for i, (ts, b) in enumerate(rth):
                hi = float(b["high"])
                lo = float(b["low"])
                cl = float(b["close"])
                vwap = vwap_arr[i]
                if morning_high_idx is None or hi > morning_high:
                    morning_high = hi
                    morning_high_idx = i
                # Need to first hit morning push
                if morning_high < rth_open * (1 + min_morning_high_pct):
                    continue
                # Wait for pullback below VWAP (i must be after morning_high_idx)
                if not pulled_back and i > morning_high_idx and lo <= vwap and (morning_high - cl) / morning_high >= min_pullback_pct:
                    pulled_back = True
                    continue
                # Entry: close back above VWAP
                if pulled_back and not entry_done and cl > vwap:
                    entry_px = cl
                    entry_ts = ts
                    sim = simulate_long_trade(rth, entry_ts, entry_px, 0.20, -0.10)
                    trades.append({
                        "hypothesis": "H2", "strategy_label": label,
                        "date": date_str, "ticker": t["ticker"], "side": "long",
                        "entry_ts": entry_ts.isoformat(), "entry_px": entry_px,
                        "morning_high_pct": (morning_high - rth_open) / rth_open,
                        "exit_reason": sim["exit_reason"], "pnl_pct": sim["exit_pct"],
                        "hold_min": sim["hold_min"], "exit_px": sim["exit_px"],
                        "exit_ts": sim["exit_ts"].isoformat() if hasattr(sim["exit_ts"], "isoformat") else sim["exit_ts"],
                    })
                    entry_done = True
                    break
    return trades


# ───────────────────────────── H3: short the ripper ─────────────────────────────

def run_h3_short_ripper(by_date: dict, top_k: int = 3,
                         min_first_30_pct: float = 0.30) -> list[dict]:
    """Top-K by first-30-min momentum (filtered to first_30 ≥ 30%).
    Short entry at 10:00 ET with target -20% / stop +10%.
    """
    trades = []
    label = f"H3_short_top{top_k}_min{int(min_first_30_pct*100)}"
    for date_str, tickers in by_date.items():
        scored = []
        for t in tickers:
            f30, end_ts, _ = first_30_max_return(t["rth"])
            if f30 is None or f30 < min_first_30_pct: continue
            scored.append((t, f30, end_ts))
        scored.sort(key=lambda x: -x[1])
        picks = scored[:top_k]
        for t, f30, end_ts in picks:
            rth = t["rth"]
            entry_bar = next((b for ts, b in rth if ts >= end_ts), None)
            if entry_bar is None: continue
            entry_px = float(entry_bar["open"])
            actual_entry_ts = next(ts for ts, b in rth if ts >= end_ts)
            sim = simulate_short_trade(rth, actual_entry_ts, entry_px, 0.20, 0.10)
            trades.append({
                "hypothesis": "H3", "strategy_label": label,
                "date": date_str, "ticker": t["ticker"], "side": "short",
                "entry_ts": actual_entry_ts.isoformat(), "entry_px": entry_px,
                "first_30_max": f30,
                "exit_reason": sim["exit_reason"], "pnl_pct": sim["exit_pct"],
                "hold_min": sim["hold_min"], "exit_px": sim["exit_px"],
                "exit_ts": sim["exit_ts"].isoformat() if hasattr(sim["exit_ts"], "isoformat") else sim["exit_ts"],
            })
    return trades


# ───────────────────────────── H4: MAGNA-N intersection ─────────────────────────────

def estimate_gap_pct(t: dict) -> float | None:
    """We don't have prior-day close in RTH-only files. Use first 5-min
    return as a proxy for 'gap evidence'. (For older files with premarket,
    this is a separate analysis.) Return None to abstain when unknown."""
    rth = t["rth"]
    if len(rth) < 5: return None
    rth_open = float(rth[0][1]["open"])
    if rth_open <= 0: return None
    five_min_high = max(float(b["high"]) for _, b in rth[:5])
    return (five_min_high - rth_open) / rth_open


def run_h4_magna_intersection(by_date: dict, top_k: int = 5,
                                min_magna_score: int = 2) -> list[dict]:
    """First-30-min top-K AMONG candidates passing MAGNA-N >= min_score.
    Same +20%/-10%/EOD exit at 10:00 ET entry.
    """
    trades = []
    label = f"H4_magna{min_magna_score}_top{top_k}"
    for date_str, tickers in by_date.items():
        scored = []
        for t in tickers:
            f30, end_ts, rth_open = first_30_max_return(t["rth"])
            if f30 is None: continue
            gap = estimate_gap_pct(t)
            magna_pts, comps = magna_score(t["ticker"], gap)
            if magna_pts < min_magna_score: continue
            # Skip ETFs (they need underlying-based scoring)
            if comps.get("type") in ("ETF", "ETS", "ETV"): continue
            scored.append((t, f30, end_ts, magna_pts, comps))
        scored.sort(key=lambda x: -x[1])
        picks = scored[:top_k]
        for t, f30, end_ts, magna_pts, comps in picks:
            rth = t["rth"]
            entry_bar = next((b for ts, b in rth if ts >= end_ts), None)
            if entry_bar is None: continue
            entry_px = float(entry_bar["open"])
            actual_entry_ts = next(ts for ts, b in rth if ts >= end_ts)
            sim = simulate_long_trade(rth, actual_entry_ts, entry_px, 0.20, -0.10)
            trades.append({
                "hypothesis": "H4", "strategy_label": label,
                "date": date_str, "ticker": t["ticker"], "side": "long",
                "entry_ts": actual_entry_ts.isoformat(), "entry_px": entry_px,
                "first_30_max": f30, "magna_score": magna_pts,
                "magna_M": comps["M"], "magna_A": comps["A"],
                "magna_G": comps["G"], "magna_N": comps["N"],
                "exit_reason": sim["exit_reason"], "pnl_pct": sim["exit_pct"],
                "hold_min": sim["hold_min"], "exit_px": sim["exit_px"],
                "exit_ts": sim["exit_ts"].isoformat() if hasattr(sim["exit_ts"], "isoformat") else sim["exit_ts"],
            })
    return trades


# ───────────────────────────── H5: consolidation breakout ─────────────────────────────

def run_h5_consolidation_breakout(by_date: dict,
                                    min_morning_push: float = 0.20,
                                    consolidation_window_min: int = 60,
                                    max_consolidation_range: float = 0.10,
                                    breakout_buffer: float = 0.005) -> list[dict]:
    """Pattern (Kullamägi-style):
       1) First 60 min: stock pushes ≥ +20% from open. Capture morning_high (mh)
          and the bar idx i_mh.
       2) From i_mh through i_mh+consolidation_window_min minutes, the bar-by-bar
          range (max_high - min_low) is ≤ max_consolidation_range * mh.
       3) After the consolidation, the first bar that closes ≥ mh*(1+breakout_buffer)
          is the entry.
       4) Exit: +20% / -10% / EOD time-stop.
    """
    trades = []
    label = (f"H5_breakout_push{int(min_morning_push*100)}_"
             f"win{consolidation_window_min}_range{int(max_consolidation_range*100)}")
    for date_str, tickers in by_date.items():
        for t in tickers:
            rth = t["rth"]
            if len(rth) < 90: continue
            rth_open = float(rth[0][1]["open"])
            if rth_open <= 0: continue

            # Step 1: morning push within first 60 min
            first_60 = rth[:60]
            morning_high = max(float(b["high"]) for _, b in first_60)
            push_pct = (morning_high - rth_open) / rth_open
            if push_pct < min_morning_push: continue
            # i_mh = idx of bar containing morning_high
            i_mh = max(range(len(first_60)),
                       key=lambda i: float(first_60[i][1]["high"]))

            # Step 2: consolidation window check from i_mh
            cons_end = min(i_mh + consolidation_window_min, len(rth))
            cons = rth[i_mh:cons_end]
            if len(cons) < 15: continue  # need some bars
            cons_high = max(float(b["high"]) for _, b in cons)
            cons_low = min(float(b["low"]) for _, b in cons)
            cons_range = (cons_high - cons_low) / morning_high
            if cons_range > max_consolidation_range: continue

            # Step 3: breakout — first bar AFTER consolidation closing > mh*(1+buf)
            breakout_threshold = morning_high * (1 + breakout_buffer)
            entry_idx = None
            for i in range(cons_end, len(rth)):
                ts, b = rth[i]
                if float(b["close"]) > breakout_threshold:
                    entry_idx = i
                    break
            if entry_idx is None: continue
            entry_ts, entry_bar = rth[entry_idx]
            entry_px = float(entry_bar["close"])
            sim = simulate_long_trade(rth, entry_ts, entry_px, 0.20, -0.10)
            trades.append({
                "hypothesis": "H5", "strategy_label": label,
                "date": date_str, "ticker": t["ticker"], "side": "long",
                "entry_ts": entry_ts.isoformat(), "entry_px": entry_px,
                "morning_push_pct": push_pct, "cons_range_pct": cons_range,
                "morning_high": morning_high,
                "exit_reason": sim["exit_reason"], "pnl_pct": sim["exit_pct"],
                "hold_min": sim["hold_min"], "exit_px": sim["exit_px"],
                "exit_ts": sim["exit_ts"].isoformat() if hasattr(sim["exit_ts"], "isoformat") else sim["exit_ts"],
            })
    return trades


# ───────────────────────────── reporting ─────────────────────────────

def summarize_trades(trades: list[dict], label: str) -> dict:
    if not trades:
        return {"strategy": label, "n_trades": 0, "n_dates": 0,
                "avg_pnl": None, "win_rate": None, "compound": None}
    df = pd.DataFrame(trades)
    n = len(df)
    n_dates = df["date"].nunique()
    avg = float(df["pnl_pct"].mean())
    win = float((df["pnl_pct"] > 0).mean())
    compound = float((1 + df["pnl_pct"]).prod() - 1)
    median = float(df["pnl_pct"].median())
    pos_n = int((df["pnl_pct"] > 0).sum())
    neg_n = int((df["pnl_pct"] < 0).sum())
    avg_win = float(df.loc[df["pnl_pct"] > 0, "pnl_pct"].mean()) if pos_n else None
    avg_loss = float(df.loc[df["pnl_pct"] < 0, "pnl_pct"].mean()) if neg_n else None
    expectancy = (win * (avg_win or 0)) + ((1 - win) * (avg_loss or 0))
    target_rate = float((df["exit_reason"] == "target").mean())
    stop_rate = float((df["exit_reason"] == "stop").mean())
    return {
        "strategy": label, "n_trades": n, "n_dates": n_dates,
        "avg_pnl": avg, "median_pnl": median,
        "win_rate": win, "target_rate": target_rate, "stop_rate": stop_rate,
        "avg_win": avg_win, "avg_loss": avg_loss, "expectancy_per_trade": expectancy,
        "compound_seq": compound,
        "max_win": float(df["pnl_pct"].max()), "max_loss": float(df["pnl_pct"].min()),
    }


def main():
    by_date = load_all_dates()

    print("\n=== Running 5 hypotheses ===")
    all_results: list[dict] = []
    all_trades: dict[str, list[dict]] = {}

    print("H1: late-day entry sweep ...")
    h1 = run_h1_late_day(by_date, top_k=3)
    all_trades["H1"] = h1
    pd.DataFrame(h1).to_parquet(OUT_DIR / "h1_late_day_trades.parquet", index=False)

    print("H2: VWAP reclaim ...")
    h2 = run_h2_vwap_reclaim(by_date, 0.20, 0.10)
    all_trades["H2"] = h2
    pd.DataFrame(h2).to_parquet(OUT_DIR / "h2_vwap_reclaim_trades.parquet", index=False)

    print("H3: short the ripper ...")
    h3 = run_h3_short_ripper(by_date, top_k=3, min_first_30_pct=0.30)
    all_trades["H3"] = h3
    pd.DataFrame(h3).to_parquet(OUT_DIR / "h3_short_ripper_trades.parquet", index=False)

    print("H4: MAGNA-N intersection ...")
    h4 = run_h4_magna_intersection(by_date, top_k=5, min_magna_score=2)
    all_trades["H4"] = h4
    pd.DataFrame(h4).to_parquet(OUT_DIR / "h4_magna_intersection_trades.parquet", index=False)

    print("H5: consolidation breakout ...")
    h5 = run_h5_consolidation_breakout(by_date, 0.20, 60, 0.10)
    all_trades["H5"] = h5
    pd.DataFrame(h5).to_parquet(OUT_DIR / "h5_consolidation_breakout_trades.parquet", index=False)

    # Per-strategy summary across each hypothesis
    summaries = {}
    for hyp_name, trades in all_trades.items():
        if not trades:
            summaries[hyp_name] = [{"strategy": hyp_name, "n_trades": 0}]
            continue
        df = pd.DataFrame(trades)
        per = []
        for label, sub in df.groupby("strategy_label"):
            per.append(summarize_trades(sub.to_dict("records"), label))
        summaries[hyp_name] = per

    OUT_DIR.joinpath("h_5_summary.json").write_text(json.dumps(summaries, indent=2, default=str))
    print(f"\nWrote {OUT_DIR / 'h_5_summary.json'}")

    # Console preview — comparative table
    print("\n" + "=" * 100)
    print(f"{'strategy':50s}  {'n':>4s}  {'avg':>7s}  {'win':>5s}  {'tgt':>5s}  {'stp':>5s}  {'compound':>10s}")
    print("=" * 100)
    for hyp_name in ["H1", "H2", "H3", "H4", "H5"]:
        for s in summaries[hyp_name]:
            n = s.get("n_trades", 0)
            if n == 0:
                print(f"{hyp_name:50s}  {0:>4d}  {'—':>7s}  {'—':>5s}  {'—':>5s}  {'—':>5s}  {'—':>10s}")
                continue
            print(f"{s['strategy'][:50]:50s}  {n:>4d}  "
                  f"{s['avg_pnl']*100:>+6.2f}%  {s['win_rate']*100:>4.1f}%  "
                  f"{s['target_rate']*100:>4.1f}%  {s['stop_rate']*100:>4.1f}%  "
                  f"{s['compound_seq']*100:>+9.2f}%")
    print("=" * 100)

    # Headline: any strategy with positive expectancy?
    print("\n=== Strategies with positive avg_pnl ===")
    found_any = False
    for hyp_name, per in summaries.items():
        for s in per:
            if s.get("avg_pnl") is not None and s["avg_pnl"] > 0:
                print(f"  [WIN] {s['strategy']}: avg={s['avg_pnl']*100:+.2f}%  "
                      f"n={s['n_trades']}  win={s['win_rate']*100:.1f}%  "
                      f"compound={s['compound_seq']*100:+.2f}%  "
                      f"avg_win={(s['avg_win'] or 0)*100:+.2f}%  "
                      f"avg_loss={(s['avg_loss'] or 0)*100:+.2f}%")
                found_any = True
    if not found_any:
        print("  (none)")


if __name__ == "__main__":
    main()
