"""Four radical experiments:

  R1: BESSEMBINDER LOTTERY. Buy 1 unit of every watchlist ticker at RTH open.
      Exit at maximum favorable excursion (PERFECT TIMING — upper bound of
      extractable prize). Then re-test with REALISTIC trailing stop (give
      back X% from peak). Accept 90%+ losers in the realistic version.

  R2: COHORT — FIRST APPEARANCE vs RECURRING. For each (date, ticker), is
      this the first time the ticker has appeared in the watchlist over our
      sample window? Compare intraday dynamics, max-mover hit rate.

  R3: LEVERAGED ETF UNIVERSE. Find every leveraged ETF (type ETS/ETF with
      "Ultra", "2x", "3x", "Bear", "Direxion", "ProShares" in the name) in
      the watchlist history. Aggregate their P&L. Compare to CRCA's pattern.

  R4: HARVEST THE RUNNER. No fixed target. Buy at 10:00 ET (top-K of first-30
      momentum). Trailing stop at X% from highest-high-since-entry. Compare
      different trail widths.

Outputs: data/audits/radical_4_summary.json + 4 trade parquets.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from backtest_5_hypotheses import (  # type: ignore
    load_all_dates, first_30_max_return, simulate_long_trade, OUT_DIR,
    load_fund,
)


# ─── R1: Bessembinder Lottery ─────────────────────────────────────

def r1_bessembinder(by_date: dict) -> dict:
    """A) Perfect-timing upper bound: max favorable excursion on every ticker.
       B) Realistic trail: trailing stop at X% from highest-high.
       C) Adaptive trail: tighter stops as P&L grows.
    """
    perfect = []
    trails: dict[str, list] = {f"trail_{int(p*100)}": [] for p in [0.10, 0.20, 0.30, 0.50]}

    for date_str, tickers in by_date.items():
        for t in tickers:
            rth = t["rth"]
            if not rth: continue
            rth_open = float(rth[0][1]["open"])
            if rth_open <= 0: continue

            # Perfect MFE
            running_high = rth_open
            for _, b in rth:
                running_high = max(running_high, float(b["high"]))
            mfe_pct = (running_high - rth_open) / rth_open
            perfect.append({"date": date_str, "ticker": t["ticker"],
                            "rth_open": rth_open, "running_high": running_high,
                            "pnl_pct": mfe_pct})

            # Trailing stop variants
            for trail_pct in [0.10, 0.20, 0.30, 0.50]:
                key = f"trail_{int(trail_pct*100)}"
                running_high = rth_open
                stopped = False
                exit_pct = 0.0
                cl = rth_open  # default if rth is empty (shouldn't happen, but bind for type-checker)
                for _, b in rth:
                    hi = float(b["high"]); lo = float(b["low"]); cl = float(b["close"])
                    running_high = max(running_high, hi)
                    trail_stop = running_high * (1 - trail_pct)
                    if lo <= trail_stop:
                        # Filled at trail_stop
                        exit_pct = (trail_stop - rth_open) / rth_open
                        stopped = True
                        break
                if not stopped:
                    exit_pct = (cl - rth_open) / rth_open
                trails[key].append({"date": date_str, "ticker": t["ticker"],
                                     "pnl_pct": exit_pct,
                                     "running_high": running_high})

    out = {}
    df_perfect = pd.DataFrame(perfect)
    out["perfect_mfe"] = {
        "n_trades": len(df_perfect),
        "avg_pnl": float(df_perfect["pnl_pct"].mean()),
        "median": float(df_perfect["pnl_pct"].median()),
        "win_rate": float((df_perfect["pnl_pct"] > 0).mean()),
        "compound_per_day": "N/A (per-trade ceiling)",
        "max": float(df_perfect["pnl_pct"].max()),
        "p99": float(df_perfect["pnl_pct"].quantile(0.99)),
        "p95": float(df_perfect["pnl_pct"].quantile(0.95)),
        "p75": float(df_perfect["pnl_pct"].quantile(0.75)),
    }
    df_perfect.to_parquet(OUT_DIR / "r1_perfect_mfe.parquet", index=False)

    for trail_label, trades in trails.items():
        df = pd.DataFrame(trades)
        # Per-day equal-weight portfolio return
        per_day = df.groupby("date")["pnl_pct"].mean()
        compound = float((1 + per_day).prod() - 1)
        out[trail_label] = {
            "n_trades": len(df),
            "avg_pnl": float(df["pnl_pct"].mean()),
            "median": float(df["pnl_pct"].median()),
            "win_rate": float((df["pnl_pct"] > 0).mean()),
            "compound_eq_weight_daily": compound,
            "max_win": float(df["pnl_pct"].max()),
            "max_loss": float(df["pnl_pct"].min()),
            "n_winners_gte_50pct": int((df["pnl_pct"] >= 0.50).sum()),
            "n_winners_gte_100pct": int((df["pnl_pct"] >= 1.00).sum()),
        }
        df.to_parquet(OUT_DIR / f"r1_{trail_label}.parquet", index=False)
    return out


# ─── R2: Cohort — first appearance vs recurring ────────────────────

def r2_cohort(by_date: dict) -> dict:
    # Build a "first seen" date per ticker
    first_seen: dict[str, str] = {}
    for date_str in sorted(by_date):
        for t in by_date[date_str]:
            tk = t["ticker"]
            if tk not in first_seen:
                first_seen[tk] = date_str

    # Track appearance count per ticker across the sample
    appearance_count: dict[str, int] = defaultdict(int)
    cohort_rows = []
    for date_str in sorted(by_date):
        for t in by_date[date_str]:
            tk = t["ticker"]
            appearance_count[tk] += 1
            n_prior = appearance_count[tk] - 1
            rth = t["rth"]
            if not rth: continue
            rth_open = float(rth[0][1]["open"])
            if rth_open <= 0: continue
            high = max(float(b["high"]) for _, b in rth)
            close = float(rth[-1][1]["close"])
            mfe_pct = (high - rth_open) / rth_open
            close_pct = (close - rth_open) / rth_open
            f30, _, _ = first_30_max_return(rth)
            cohort_rows.append({
                "date": date_str, "ticker": tk,
                "is_first_appearance": (n_prior == 0),
                "n_prior_appearances": n_prior,
                "max_return": mfe_pct, "close_return": close_pct,
                "first_30_max": f30,
                "rth_open": rth_open,
            })
    df = pd.DataFrame(cohort_rows)
    df.to_parquet(OUT_DIR / "r2_cohort.parquet", index=False)

    out = {"n_unique_tickers": int(df["ticker"].nunique()),
            "n_total_appearances": int(len(df))}
    # Compare first vs recurring
    first = df[df["is_first_appearance"]]
    recurring = df[~df["is_first_appearance"]]
    out["first_appearance"] = {
        "n": len(first),
        "avg_max_return": float(first["max_return"].mean()),
        "median_max_return": float(first["max_return"].median()),
        "p75_max_return": float(first["max_return"].quantile(0.75)),
        "p95_max_return": float(first["max_return"].quantile(0.95)),
        "share_with_max_gte_30pct": float((first["max_return"] >= 0.30).mean()),
        "share_with_max_gte_100pct": float((first["max_return"] >= 1.00).mean()),
        "avg_close_return": float(first["close_return"].mean()),
    }
    out["recurring"] = {
        "n": len(recurring),
        "avg_max_return": float(recurring["max_return"].mean()),
        "median_max_return": float(recurring["max_return"].median()),
        "p75_max_return": float(recurring["max_return"].quantile(0.75)),
        "p95_max_return": float(recurring["max_return"].quantile(0.95)),
        "share_with_max_gte_30pct": float((recurring["max_return"] >= 0.30).mean()),
        "share_with_max_gte_100pct": float((recurring["max_return"] >= 1.00).mean()),
        "avg_close_return": float(recurring["close_return"].mean()),
    }
    # By recurrence bucket
    df["recur_bucket"] = pd.cut(df["n_prior_appearances"],
                                  bins=[-0.5, 0, 1, 3, 7, 15, 100],
                                  labels=["first", "second", "3-4th", "5-8th", "9-16th", "17+"])
    g = df.groupby("recur_bucket", observed=True).agg(
        n=("max_return", "size"),
        avg_max_return=("max_return", "mean"),
        share_huge=("max_return", lambda s: (s >= 0.30).mean()),
        avg_close=("close_return", "mean"),
    )
    out["by_recurrence_bucket"] = g.reset_index().to_dict(orient="records")
    return out


# ─── R3: Leveraged ETF universe ────────────────────────────────────

LEVERAGED_KEYWORDS = ("Ultra", "2x", "3x", "Bear", "Bull", "Daily",
                       "Direxion", "ProShares", "Inverse", "Leveraged",
                       "MicroSectors", "GraniteShares")
LEVERAGED_TYPES = ("ETS", "ETV", "ETN")  # ETS=ETF Single, ETV=ETF Volatility


def r3_leveraged_etfs(by_date: dict) -> dict:
    found = []
    seen_tickers = set()
    for date_str in sorted(by_date):
        for t in by_date[date_str]:
            tk = t["ticker"]
            if tk in seen_tickers: continue
            fund = load_fund(tk)
            if not fund: continue
            details = fund.get("details") or {}
            ttype = details.get("type", "?")
            tname = (details.get("name") or "").lower()
            is_lev = (ttype in LEVERAGED_TYPES) or any(
                kw.lower() in tname for kw in LEVERAGED_KEYWORDS
            )
            if is_lev:
                found.append({"ticker": tk, "type": ttype, "name": details.get("name"),
                              "first_date": date_str})
                seen_tickers.add(tk)
            else:
                seen_tickers.add(tk)

    # Per-instance returns for leveraged ETF appearances
    lev_set = {f["ticker"] for f in found}
    instances = []
    for date_str in sorted(by_date):
        for t in by_date[date_str]:
            if t["ticker"] not in lev_set: continue
            rth = t["rth"]
            if not rth: continue
            rth_open = float(rth[0][1]["open"])
            if rth_open <= 0: continue
            high = max(float(b["high"]) for _, b in rth)
            close = float(rth[-1][1]["close"])
            instances.append({
                "date": date_str, "ticker": t["ticker"],
                "rth_open": rth_open,
                "max_return": (high - rth_open) / rth_open,
                "close_return": (close - rth_open) / rth_open,
            })
    df = pd.DataFrame(instances) if instances else pd.DataFrame()
    if not df.empty:
        df.to_parquet(OUT_DIR / "r3_leveraged_etf_instances.parquet", index=False)

    out = {
        "n_unique_leveraged_etfs": len(found),
        "leveraged_etfs": found[:50],
        "n_appearances_total": len(df),
        "stats": ({
            "avg_max_return": float(df["max_return"].mean()),
            "p95_max_return": float(df["max_return"].quantile(0.95)),
            "max_max_return": float(df["max_return"].max()),
            "share_max_gte_50pct": float((df["max_return"] >= 0.50).mean()),
            "share_max_gte_100pct": float((df["max_return"] >= 1.00).mean()),
            "share_max_gte_200pct": float((df["max_return"] >= 2.00).mean()),
        } if not df.empty else {}),
        "top_10_lev_etf_days": (df.sort_values("max_return", ascending=False).head(10).to_dict("records")
                                  if not df.empty else []),
    }
    return out


# ─── R4: Harvest the runner (trailing only) ────────────────────────

def r4_harvest_runner(by_date: dict, top_k: int = 3,
                       trails: list = None) -> dict:
    if trails is None:
        trails = [0.15, 0.25, 0.35, 0.50]
    out = {}
    for trail_pct in trails:
        trades = []
        for date_str, tickers in by_date.items():
            scored = []
            for t in tickers:
                f30, end_ts, _ = first_30_max_return(t["rth"])
                if f30 is None: continue
                scored.append((t, f30, end_ts))
            scored.sort(key=lambda x: -x[1])
            for t, f30, end_ts in scored[:top_k]:
                rth = t["rth"]
                entry_bar = next((b for ts, b in rth if ts >= end_ts), None)
                if entry_bar is None: continue
                ats = next(ts for ts, b in rth if ts >= end_ts)
                entry_px = float(entry_bar["open"])
                if entry_px <= 0: continue
                running_high = entry_px
                exit_px = entry_px
                exit_reason = "eod"
                for ts, b in rth:
                    if ts < ats: continue
                    hi = float(b["high"]); lo = float(b["low"]); cl = float(b["close"])
                    running_high = max(running_high, hi)
                    trail_stop = running_high * (1 - trail_pct)
                    if lo <= trail_stop and trail_stop < entry_px * (1 - trail_pct + 0.001):
                        # Initial trail-stop fill (loss case)
                        exit_px = trail_stop
                        exit_reason = "trail_initial"
                        break
                    if lo <= trail_stop:
                        exit_px = trail_stop
                        exit_reason = "trail_after_run"
                        break
                    exit_px = cl
                pnl = (exit_px - entry_px) / entry_px
                trades.append({"date": date_str, "ticker": t["ticker"],
                               "entry_px": entry_px, "exit_px": exit_px,
                               "running_high": running_high,
                               "pnl_pct": pnl, "exit_reason": exit_reason,
                               "first_30": f30})
        df = pd.DataFrame(trades)
        per_day = df.groupby("date")["pnl_pct"].mean()
        compound = float((1 + per_day).prod() - 1)
        out[f"trail_{int(trail_pct*100)}"] = {
            "n_trades": len(df),
            "avg_pnl": float(df["pnl_pct"].mean()),
            "median_pnl": float(df["pnl_pct"].median()),
            "win_rate": float((df["pnl_pct"] > 0).mean()),
            "compound": compound,
            "max_win": float(df["pnl_pct"].max()),
            "max_loss": float(df["pnl_pct"].min()),
            "n_winners_gte_50pct": int((df["pnl_pct"] >= 0.50).sum()),
            "n_winners_gte_100pct": int((df["pnl_pct"] >= 1.00).sum()),
            "p95_pnl": float(df["pnl_pct"].quantile(0.95)),
        }
        df.to_parquet(OUT_DIR / f"r4_harvest_trail{int(trail_pct*100)}.parquet", index=False)
    return out


# ─── Main ──────────────────────────────────────────────────────────

def main():
    by_date = load_all_dates()

    print("\n=== R1: BESSEMBINDER LOTTERY ===")
    r1 = r1_bessembinder(by_date)
    print("Perfect MFE (upper bound of extractable, per-trade):")
    pm = r1["perfect_mfe"]
    print(f"  n={pm['n_trades']}  avg={pm['avg_pnl']*100:+.2f}%  "
          f"median={pm['median']*100:+.2f}%  p75={pm['p75']*100:+.2f}%  "
          f"p95={pm['p95']*100:+.2f}%  p99={pm['p99']*100:+.2f}%  max={pm['max']*100:+.2f}%")
    for k in ["trail_10", "trail_20", "trail_30", "trail_50"]:
        s = r1[k]
        print(f"  {k}: n={s['n_trades']}  avg={s['avg_pnl']*100:+.2f}%  "
              f"win={s['win_rate']*100:.1f}%  daily-compound={s['compound_eq_weight_daily']*100:+.1f}%  "
              f"winners_>=50%={s['n_winners_gte_50pct']}  >=100%={s['n_winners_gte_100pct']}")

    print("\n=== R2: COHORT (first vs recurring) ===")
    r2 = r2_cohort(by_date)
    print(f"Unique tickers: {r2['n_unique_tickers']}, total appearances: {r2['n_total_appearances']}")
    f = r2["first_appearance"]; rec = r2["recurring"]
    print(f"  FIRST appearance:  n={f['n']:5d}  avg_max={f['avg_max_return']*100:+.2f}%  "
          f"share>=30%={f['share_with_max_gte_30pct']*100:.1f}%  "
          f"share>=100%={f['share_with_max_gte_100pct']*100:.2f}%  "
          f"close={f['avg_close_return']*100:+.2f}%")
    print(f"  RECURRING:         n={rec['n']:5d}  avg_max={rec['avg_max_return']*100:+.2f}%  "
          f"share>=30%={rec['share_with_max_gte_30pct']*100:.1f}%  "
          f"share>=100%={rec['share_with_max_gte_100pct']*100:.2f}%  "
          f"close={rec['avg_close_return']*100:+.2f}%")
    print("\n  By recurrence bucket:")
    for row in r2["by_recurrence_bucket"]:
        print(f"    {str(row['recur_bucket']):8s}: n={row['n']:4d}  "
              f"avg_max={row['avg_max_return']*100:+.2f}%  "
              f"share_huge={row['share_huge']*100:5.1f}%  "
              f"avg_close={row['avg_close']*100:+.2f}%")

    print("\n=== R3: LEVERAGED ETF universe ===")
    r3 = r3_leveraged_etfs(by_date)
    print(f"  unique leveraged ETFs in watchlist history: {r3['n_unique_leveraged_etfs']}")
    print(f"  total appearances: {r3['n_appearances_total']}")
    if r3["stats"]:
        s = r3["stats"]
        print(f"  avg_max={s['avg_max_return']*100:+.2f}%  "
              f"p95={s['p95_max_return']*100:+.2f}%  max={s['max_max_return']*100:+.2f}%")
        print(f"  share_max>=50%={s['share_max_gte_50pct']*100:.1f}%  "
              f">=100%={s['share_max_gte_100pct']*100:.1f}%  "
              f">=200%={s['share_max_gte_200pct']*100:.1f}%")
    print("\n  Top 10 leveraged-ETF days by max_return:")
    for d in r3["top_10_lev_etf_days"]:
        print(f"    {d['date']}  {d['ticker']:6s}  open=${d['rth_open']:.2f}  "
              f"max={d['max_return']*100:+.2f}%  close={d['close_return']*100:+.2f}%")

    print("\n=== R4: HARVEST THE RUNNER (trailing stop only) ===")
    r4 = r4_harvest_runner(by_date)
    for k, s in r4.items():
        print(f"  {k}: n={s['n_trades']}  avg={s['avg_pnl']*100:+.2f}%  "
              f"win={s['win_rate']*100:.1f}%  compound={s['compound']*100:+.2f}%  "
              f"max_win={s['max_win']*100:+.2f}%  >=50%={s['n_winners_gte_50pct']}  "
              f">=100%={s['n_winners_gte_100pct']}")

    out = {"R1": r1, "R2": r2, "R3": r3, "R4": r4}
    (OUT_DIR / "radical_4_summary.json").write_text(json.dumps(out, indent=2, default=str))
    print(f"\nWrote {OUT_DIR / 'radical_4_summary.json'}")


if __name__ == "__main__":
    main()
