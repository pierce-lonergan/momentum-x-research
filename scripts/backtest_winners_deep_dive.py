"""Deep-dive on the two winners from the 5-hypothesis run.

WINNERS (from h_5_summary.json):
  H1_entry_1300_top3:        avg +0.45%/trade, win 47%, compound +49.79%
  H3_short_top3_min30:       avg +1.87%/trade, win 46%, compound +77.31%

Goals:
  A) Parameter-sweep H1 entry times (12:30, 13:00, 13:30, 14:00, 14:30) × top-K (1,2,3,5).
  B) Parameter-sweep H3 (top-K × min_first_30 × stop/target).
  C) Test COMBINED long-late + short-ripper as a market-neutral strategy.
  D) Sample-bias / overfitting checks: bootstrap, time-split (first half vs second half).
  E) Re-test H5 with relaxed filters.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
import random
import sys

import pandas as pd

# Reuse helpers from the unified harness
sys.path.insert(0, str(Path(__file__).parent))
from backtest_5_hypotheses import (  # type: ignore
    load_all_dates, first_30_max_return, simulate_long_trade,
    simulate_short_trade, summarize_trades, OUT_DIR, ET,
    cumulative_vwap,
)

OUT_PATH = OUT_DIR / "winners_deep_dive_summary.json"


# ─── Sweep H1 ─────────────────────────────────────────────────────

def sweep_h1(by_date: dict) -> list[dict]:
    """Long top-K of first-30-min momentum, entry at H:M, +20%/-10%/EOD."""
    rows = []
    for entry_hour, entry_min in [(12, 30), (13, 0), (13, 30), (14, 0), (14, 30)]:
        for k in [1, 2, 3, 5]:
            trades = []
            for date_str, tickers in by_date.items():
                scored = []
                for t in tickers:
                    f30, _, _ = first_30_max_return(t["rth"])
                    if f30 is None: continue
                    scored.append((t, f30))
                scored.sort(key=lambda x: -x[1])
                picks = scored[:k]
                for t, f30 in picks:
                    rth = t["rth"]
                    entry_ts_target = rth[0][0].replace(hour=entry_hour, minute=entry_min, second=0)
                    entry_bar = next((b for ts, b in rth if ts >= entry_ts_target), None)
                    if entry_bar is None: continue
                    actual_entry_ts = next(ts for ts, b in rth if ts >= entry_ts_target)
                    entry_px = float(entry_bar["open"])
                    sim = simulate_long_trade(rth, actual_entry_ts, entry_px, 0.20, -0.10)
                    trades.append({
                        "date": date_str, "ticker": t["ticker"], "side": "long",
                        "entry_ts": actual_entry_ts.isoformat(), "entry_px": entry_px,
                        "first_30_max": f30,
                        "exit_reason": sim["exit_reason"], "pnl_pct": sim["exit_pct"],
                        "hold_min": sim["hold_min"],
                    })
            label = f"H1_entry_{entry_hour:02d}{entry_min:02d}_top{k}"
            s = summarize_trades(trades, label)
            s["trades"] = trades
            rows.append(s)
    return rows


# ─── Sweep H3 ─────────────────────────────────────────────────────

def sweep_h3(by_date: dict) -> list[dict]:
    """Short top-K of first-30-min, with various min_threshold and target/stop."""
    rows = []
    for k in [1, 2, 3, 5]:
        for min_pct in [0.20, 0.30, 0.50, 0.75]:
            for tgt, stop in [(0.20, 0.10), (0.10, 0.05), (0.30, 0.15)]:
                trades = []
                for date_str, tickers in by_date.items():
                    scored = []
                    for t in tickers:
                        f30, end_ts, _ = first_30_max_return(t["rth"])
                        if f30 is None or f30 < min_pct: continue
                        scored.append((t, f30, end_ts))
                    scored.sort(key=lambda x: -x[1])
                    picks = scored[:k]
                    for t, f30, end_ts in picks:
                        rth = t["rth"]
                        entry_bar = next((b for ts, b in rth if ts >= end_ts), None)
                        if entry_bar is None: continue
                        actual_entry_ts = next(ts for ts, b in rth if ts >= end_ts)
                        entry_px = float(entry_bar["open"])
                        sim = simulate_short_trade(rth, actual_entry_ts, entry_px, tgt, stop)
                        trades.append({
                            "date": date_str, "ticker": t["ticker"], "side": "short",
                            "entry_ts": actual_entry_ts.isoformat(), "entry_px": entry_px,
                            "first_30_max": f30,
                            "exit_reason": sim["exit_reason"], "pnl_pct": sim["exit_pct"],
                            "hold_min": sim["hold_min"],
                        })
                label = f"H3_short_top{k}_min{int(min_pct*100)}_tgt{int(tgt*100)}_stp{int(stop*100)}"
                s = summarize_trades(trades, label)
                s["trades"] = trades
                rows.append(s)
    return rows


# ─── Combined long-late + short-ripper ─────────────────────────────

def run_combined_market_neutral(by_date: dict, h1_entry=(13, 0), h1_k=3,
                                  h3_k=3, h3_min=0.30) -> dict:
    """Daily portfolio: equal-weight K longs at 13:00 + equal-weight K shorts
    entered at 10:00. Daily return = avg(longs) + avg(shorts).
    """
    daily_returns = []
    n_long_trades = 0
    n_short_trades = 0
    for date_str, tickers in by_date.items():
        scored = []
        for t in tickers:
            f30, end_ts, _ = first_30_max_return(t["rth"])
            if f30 is None: continue
            scored.append((t, f30, end_ts))
        scored.sort(key=lambda x: -x[1])

        # Longs: top-h1_k entered at 13:00
        long_picks = scored[:h1_k]
        long_pnls = []
        for t, f30, _ in long_picks:
            rth = t["rth"]
            entry_ts_target = rth[0][0].replace(hour=h1_entry[0], minute=h1_entry[1], second=0)
            entry_bar = next((b for ts, b in rth if ts >= entry_ts_target), None)
            if entry_bar is None: continue
            ats = next(ts for ts, b in rth if ts >= entry_ts_target)
            entry_px = float(entry_bar["open"])
            sim = simulate_long_trade(rth, ats, entry_px, 0.20, -0.10)
            long_pnls.append(sim["exit_pct"])
            n_long_trades += 1

        # Shorts: top-h3_k of first_30 ≥ h3_min entered at 10:00
        short_eligible = [(t, f30, end_ts) for t, f30, end_ts in scored if f30 >= h3_min]
        short_picks = short_eligible[:h3_k]
        short_pnls = []
        for t, f30, end_ts in short_picks:
            rth = t["rth"]
            entry_bar = next((b for ts, b in rth if ts >= end_ts), None)
            if entry_bar is None: continue
            ats = next(ts for ts, b in rth if ts >= end_ts)
            entry_px = float(entry_bar["open"])
            sim = simulate_short_trade(rth, ats, entry_px, 0.20, 0.10)
            short_pnls.append(sim["exit_pct"])
            n_short_trades += 1

        daily = 0.0
        n_legs = 0
        if long_pnls:
            daily += sum(long_pnls) / len(long_pnls); n_legs += 1
        if short_pnls:
            daily += sum(short_pnls) / len(short_pnls); n_legs += 1
        if n_legs > 0:
            daily = daily / n_legs  # average of long-leg-avg and short-leg-avg
            daily_returns.append({"date": date_str, "daily_pnl": daily,
                                  "n_long": len(long_pnls), "n_short": len(short_pnls)})

    df = pd.DataFrame(daily_returns)
    if df.empty:
        return {"strategy": "combined_market_neutral", "n_days": 0}
    df = df.sort_values("date")
    compound = float((1 + df["daily_pnl"]).prod() - 1)
    mean = float(df["daily_pnl"].mean())
    std = float(df["daily_pnl"].std())
    sharpe = mean / std if std > 0 else None
    win_days = float((df["daily_pnl"] > 0).mean())
    return {
        "strategy": "combined_market_neutral",
        "n_days": len(df),
        "n_long_trades": n_long_trades,
        "n_short_trades": n_short_trades,
        "daily_mean": mean,
        "daily_std": std,
        "daily_sharpe_unannualized": sharpe,
        "daily_sharpe_annualized": sharpe * (252 ** 0.5) if sharpe else None,
        "compound_seq": compound,
        "win_day_rate": win_days,
        "max_dd": float((df["daily_pnl"].cumsum() - df["daily_pnl"].cumsum().cummax()).min()),
    }


# ─── Robustness checks ─────────────────────────────────────────────

def time_split_check(trades: list[dict], label: str) -> dict:
    """Compare first-half vs second-half performance."""
    if not trades: return {"label": label, "n": 0}
    df = pd.DataFrame(trades).sort_values("date")
    cutoff = df["date"].iloc[len(df) // 2]
    first = df[df["date"] < cutoff]
    second = df[df["date"] >= cutoff]
    return {
        "label": label,
        "first_half": {
            "n": len(first), "avg": float(first["pnl_pct"].mean()),
            "win": float((first["pnl_pct"] > 0).mean()) if len(first) else None,
            "compound": float((1 + first["pnl_pct"]).prod() - 1) if len(first) else None,
        },
        "second_half": {
            "n": len(second), "avg": float(second["pnl_pct"].mean()),
            "win": float((second["pnl_pct"] > 0).mean()) if len(second) else None,
            "compound": float((1 + second["pnl_pct"]).prod() - 1) if len(second) else None,
        },
    }


def bootstrap_check(trades: list[dict], n_iter: int = 1000) -> dict:
    """Bootstrap CI for mean PnL."""
    if not trades: return {"n": 0}
    pnls = [t["pnl_pct"] for t in trades]
    n = len(pnls)
    means = []
    rng = random.Random(42)
    for _ in range(n_iter):
        sample = [pnls[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    return {
        "n_trades": n,
        "mean_obs": sum(pnls) / n,
        "boot_p05": means[int(n_iter * 0.05)],
        "boot_p50": means[int(n_iter * 0.50)],
        "boot_p95": means[int(n_iter * 0.95)],
        "share_positive_iters": sum(1 for m in means if m > 0) / n_iter,
    }


# ─── Main ─────────────────────────────────────────────────────────

def main():
    by_date = load_all_dates()

    print("\n=== A) H1 sweep (entry time × top-K) ===")
    h1_rows = sweep_h1(by_date)
    h1_for_print = sorted(h1_rows, key=lambda r: r["compound_seq"] or -1, reverse=True)
    print(f"{'strategy':40s}  {'n':>4s}  {'avg':>7s}  {'win':>5s}  {'cmp':>9s}")
    for r in h1_for_print[:15]:
        print(f"{r['strategy'][:40]:40s}  {r['n_trades']:>4d}  "
              f"{r['avg_pnl']*100:>+6.2f}%  {r['win_rate']*100:>4.1f}%  "
              f"{r['compound_seq']*100:>+8.2f}%")

    print("\n=== B) H3 sweep (top-K × min_first_30 × tgt/stp) ===")
    h3_rows = sweep_h3(by_date)
    h3_for_print = sorted(h3_rows, key=lambda r: r["compound_seq"] or -1, reverse=True)
    print(f"{'strategy':50s}  {'n':>4s}  {'avg':>7s}  {'win':>5s}  {'cmp':>9s}")
    for r in h3_for_print[:15]:
        print(f"{r['strategy'][:50]:50s}  {r['n_trades']:>4d}  "
              f"{r['avg_pnl']*100:>+6.2f}%  {r['win_rate']*100:>4.1f}%  "
              f"{r['compound_seq']*100:>+8.2f}%")

    # Pick top H1 + top H3 and run combined
    print("\n=== C) Combined long-late (best H1) + short-ripper (best H3) ===")
    best_h1 = h1_for_print[0]
    best_h3 = h3_for_print[0]
    print(f"  Using H1: {best_h1['strategy']}")
    print(f"  Using H3: {best_h3['strategy']}")
    # Parse h1 entry from label
    label_h1 = best_h1['strategy']
    # H1_entry_HHMM_topK
    parts = label_h1.split("_")
    hhmm = parts[2]; h1_k = int(parts[3].replace("top", ""))
    h1_entry = (int(hhmm[:2]), int(hhmm[2:]))
    label_h3 = best_h3['strategy']
    h3_parts = label_h3.split("_")
    h3_k = int(h3_parts[2].replace("top", ""))
    h3_min = int(h3_parts[3].replace("min", "")) / 100
    combined = run_combined_market_neutral(by_date, h1_entry, h1_k, h3_k, h3_min)
    print(f"  Combined: n_days={combined['n_days']}  "
          f"daily_mean={combined['daily_mean']*100:+.3f}%  "
          f"daily_std={combined['daily_std']*100:.3f}%  "
          f"sharpe_annual={combined['daily_sharpe_annualized']:.2f}  "
          f"compound={combined['compound_seq']*100:+.2f}%  "
          f"win_days={combined['win_day_rate']*100:.1f}%  "
          f"max_dd={combined['max_dd']*100:.2f}%")

    # D) Robustness: time-split + bootstrap on best H1 and best H3
    print("\n=== D) Robustness checks ===")
    h1_best_split = time_split_check(best_h1.get("trades", []), best_h1["strategy"])
    h3_best_split = time_split_check(best_h3.get("trades", []), best_h3["strategy"])
    h1_boot = bootstrap_check(best_h1.get("trades", []))
    h3_boot = bootstrap_check(best_h3.get("trades", []))

    def fmt_split(s):
        f = s["first_half"]; sec = s["second_half"]
        return (f"  first n={f['n']} avg={f['avg']*100:+.2f}% cmp={f['compound']*100:+.2f}%  "
                f"second n={sec['n']} avg={sec['avg']*100:+.2f}% cmp={sec['compound']*100:+.2f}%")

    print(f"\n  {best_h1['strategy']} (H1 time-split):"); print(fmt_split(h1_best_split))
    print(f"  Bootstrap mean CI [p05={h1_boot['boot_p05']*100:+.3f}%, p50={h1_boot['boot_p50']*100:+.3f}%, p95={h1_boot['boot_p95']*100:+.3f}%]")
    print(f"  Share of bootstrap iterations with positive mean: {h1_boot['share_positive_iters']*100:.1f}%")

    print(f"\n  {best_h3['strategy']} (H3 time-split):"); print(fmt_split(h3_best_split))
    print(f"  Bootstrap mean CI [p05={h3_boot['boot_p05']*100:+.3f}%, p50={h3_boot['boot_p50']*100:+.3f}%, p95={h3_boot['boot_p95']*100:+.3f}%]")
    print(f"  Share of bootstrap iterations with positive mean: {h3_boot['share_positive_iters']*100:.1f}%")

    # Persist
    out = {
        "h1_sweep": [{k: v for k, v in r.items() if k != "trades"} for r in h1_rows],
        "h3_sweep": [{k: v for k, v in r.items() if k != "trades"} for r in h3_rows],
        "combined_market_neutral": combined,
        "robustness": {
            "h1_best_time_split": h1_best_split,
            "h3_best_time_split": h3_best_split,
            "h1_bootstrap": h1_boot,
            "h3_bootstrap": h3_boot,
        },
    }
    OUT_PATH.write_text(json.dumps(out, indent=2, default=str))
    print(f"\nWrote {OUT_PATH.relative_to(Path.cwd())}")


if __name__ == "__main__":
    main()
