"""Track B: full MAGNA-N classification of long carry trades 2/19-4/29.

Per PROMPT_10 §5. Distinguishes:
- CS (common stock) — Bonde MAGNA-N applies directly
- ETF/ETS/leveraged ETF — Bonde MAGNA-N applies to UNDERLYING (resolution
  via Polygon ticker_details may not give an underlying field; use the name)
- ADR / other types — flag separately

For each common-stock carry trade, applies MAGNA-N where data is available:
  M: net_income YoY trajectory >100% over the prior 4 quarters (proxy)
  A: revenues YoY trajectory >39% (looser proxy: latest QoQ growth)
  G: bot's recorded entry was on a >10% gap day (use bar_recordings if available)
  N: market_cap <= $1B (the project's neglect proxy, since analyst counts and
     institutional ownership are not in our Polygon fundamentals dump)

Score 0/4 to 4/4. Tag:
  Real EP if score >= 3
  Story EP if 1-2 with G (gap-up confirmed)
  Noise otherwise
"""
from __future__ import annotations

import json
from pathlib import Path
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
ACTIVITIES = REPO / "data" / "broker_truth" / "activities.parquet"
CLOSED = REPO / "data" / "broker_truth" / "closed_trades.parquet"
FUND_DIR = REPO / "data" / "polygon_backfill" / "fundamentals"
BAR_DIR = REPO / "data" / "bar_recordings"
OUT_PARQUET = REPO / "data" / "audits" / "carry_trades_full_magna_n.parquet"
OUT_PARQUET.parent.mkdir(parents=True, exist_ok=True)


def load_fundamentals(ticker: str) -> dict | None:
    p = FUND_DIR / f"{ticker}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def magna_m_score(fins: list) -> tuple[bool, str]:
    """M: profit growth proxy. Compare latest quarter net_income to oldest in
    our data. We have at most 4 quarters per ticker.
    """
    if not fins or len(fins) < 2:
        return False, "insufficient_data"
    latest = fins[0]  # Polygon returns most-recent first
    oldest = fins[-1]
    li = latest.get("net_income_loss")
    oi = oldest.get("net_income_loss")
    if li is None or oi is None or oi == 0:
        return False, "missing_net_income"
    growth = (li - oi) / abs(oi) * 100
    return growth >= 100.0, f"growth={growth:+.0f}%"


def magna_a_score(fins: list) -> tuple[bool, str]:
    """A: sales/revenue acceleration. Look at latest revenue vs Q-1, and
    the prior step (Q-1 vs Q-2). Both should be >=10% (relaxed from Bonde's
    39% YoY since we only have intra-year data)."""
    if not fins or len(fins) < 3:
        return False, "insufficient_data"
    rev = [f.get("revenues") for f in fins[:3]]
    if any(r is None or r == 0 for r in rev):
        return False, "missing_revenue"
    # rev[0] = latest, rev[1] = previous, rev[2] = prior
    qoq1 = (rev[0] - rev[1]) / rev[1] * 100
    qoq2 = (rev[1] - rev[2]) / rev[2] * 100
    return (qoq1 >= 10.0 and qoq2 >= 10.0), f"qoq1={qoq1:+.0f}% qoq2={qoq2:+.0f}%"


def magna_g_score(ticker: str, session_date: str) -> tuple[bool, str]:
    """G: gap-up >=10% on entry day. Use bar_recordings if available;
    compare first bar's open to (assumed) prior close from previous day's
    last bar."""
    p = BAR_DIR / session_date / f"{ticker}.json"
    if not p.exists():
        return False, "no_bar_recording"
    try:
        obj = json.loads(p.read_text())
        bars = obj.get("bars", [])
        if not bars:
            return False, "empty_bars"
        # First bar's open
        first_open = float(bars[0]["open"])
        # Try previous trading day's close
        from datetime import datetime, timedelta
        d = datetime.strptime(session_date, "%Y-%m-%d")
        for back in range(1, 5):
            prev = (d - timedelta(days=back)).strftime("%Y-%m-%d")
            prev_p = BAR_DIR / prev / f"{ticker}.json"
            if prev_p.exists():
                try:
                    pobj = json.loads(prev_p.read_text())
                    pbars = pobj.get("bars", [])
                    if pbars:
                        prev_close = float(pbars[-1]["close"])
                        gap_pct = (first_open - prev_close) / prev_close * 100
                        return gap_pct >= 10.0, f"gap={gap_pct:+.1f}%"
                except Exception as e:
                    print(f"WARN: prev-bar gap parse failed for {pf}: {e}")
                    continue
        return False, "no_prev_bars"
    except Exception as e:
        return False, f"bar_read_error: {e}"


def magna_n_score(details: dict | None) -> tuple[bool, str]:
    """N: neglected proxy. market_cap <= $1B."""
    if not details:
        return False, "no_details"
    mc = details.get("market_cap")
    if mc is None:
        return False, "no_market_cap"
    return mc <= 1e9, f"mcap=${mc/1e6:.0f}M"


def classify(score: int, has_g: bool) -> str:
    if score >= 3:
        return "Real_EP"
    if score >= 1 and has_g:
        return "Story_EP"
    return "Noise"


def main():
    closed = pd.read_parquet(CLOSED)
    print(f"Total closed trades: {len(closed)}")

    # Carry trades only, long-side (entry_qty > 0; sells of long lots)
    carry = closed[closed["is_carry"] == True].copy()
    print(f"Carry trades (held overnight or longer): {len(carry)}")
    print(f"Unique tickers in carry trades: {carry['symbol'].nunique()}")

    # Aggregate per (symbol, entry_session_date) to dedupe FIFO splits
    # Use the entry_ts as the key
    carry["entry_dt"] = pd.to_datetime(carry["entry_ts"])
    carry["entry_session"] = carry["entry_dt"].dt.tz_convert("America/New_York").dt.date.astype(str)
    grouped = carry.groupby(["symbol", "entry_session"]).agg(
        n_legs=("symbol", "count"),
        total_pnl=("realized_pnl", "sum"),
        total_qty=("entry_qty", "sum"),
        avg_entry_px=("entry_price", "mean"),
        avg_exit_px=("exit_price", "mean"),
        avg_hold_h=("hold_seconds", lambda x: x.mean() / 3600),
    ).reset_index()
    print(f"Deduped (ticker, entry_session) carry trades: {len(grouped)}")

    rows = []
    for _, t in grouped.iterrows():
        ticker = t["symbol"]
        entry_session = t["entry_session"]
        fund = load_fundamentals(ticker)
        if fund:
            details = fund.get("details") or {}
            ticker_type = details.get("type", "?")
            ticker_name = details.get("name", "?")
        else:
            details = {}
            ticker_type = "no_fundamentals"
            ticker_name = "?"

        # Scoring
        is_etf = ticker_type in ("ETF", "ETS", "ETV")
        if is_etf:
            row = {
                "ticker": ticker, "entry_session": entry_session,
                "ticker_type": ticker_type, "ticker_name": ticker_name,
                "n_legs": t["n_legs"], "total_qty": int(t["total_qty"]),
                "total_pnl": float(t["total_pnl"]),
                "avg_entry_px": float(t["avg_entry_px"]),
                "avg_exit_px": float(t["avg_exit_px"]),
                "avg_hold_h": float(t["avg_hold_h"]),
                "m_pass": False, "m_note": "ETF_NA",
                "a_pass": False, "a_note": "ETF_NA",
                "g_pass": False, "g_note": "ETF_NA_underlying",
                "n_pass": False, "n_note": "ETF_NA",
                "score": -1, "ep_class": "ETF_amplifier",
                "rationale": f"Type={ticker_type}; MAGNA-N N/A; classify via underlying",
            }
        else:
            fins = (fund or {}).get("financials", [])
            m_pass, m_note = magna_m_score(fins)
            a_pass, a_note = magna_a_score(fins)
            g_pass, g_note = magna_g_score(ticker, entry_session)
            n_pass, n_note = magna_n_score(details)
            score = sum([m_pass, a_pass, g_pass, n_pass])
            ep_class = classify(score, g_pass)
            row = {
                "ticker": ticker, "entry_session": entry_session,
                "ticker_type": ticker_type, "ticker_name": ticker_name,
                "n_legs": t["n_legs"], "total_qty": int(t["total_qty"]),
                "total_pnl": float(t["total_pnl"]),
                "avg_entry_px": float(t["avg_entry_px"]),
                "avg_exit_px": float(t["avg_exit_px"]),
                "avg_hold_h": float(t["avg_hold_h"]),
                "m_pass": m_pass, "m_note": m_note,
                "a_pass": a_pass, "a_note": a_note,
                "g_pass": g_pass, "g_note": g_note,
                "n_pass": n_pass, "n_note": n_note,
                "score": int(score), "ep_class": ep_class,
                "rationale": f"score={score}/4 (M={m_pass} A={a_pass} G={g_pass} N={n_pass})",
            }
        rows.append(row)

    df = pd.DataFrame(rows)
    df = df.sort_values("total_pnl", ascending=False)
    df.to_parquet(OUT_PARQUET, index=False)
    print(f"\nWrote {OUT_PARQUET.relative_to(REPO)} ({len(df)} rows)")

    # Summary
    print("\n=== Class breakdown ===")
    print(df["ep_class"].value_counts().to_string())
    print("\n=== Dollar-weighted P&L by class ===")
    pnl_by_class = df.groupby("ep_class")["total_pnl"].agg(["sum", "count", "mean"])
    print(pnl_by_class.to_string())
    print()
    total = df["total_pnl"].sum()
    print(f"\nTotal carry P&L: ${total:+,.2f}")
    for cls in df["ep_class"].unique():
        cls_pnl = df[df["ep_class"] == cls]["total_pnl"].sum()
        pct = cls_pnl / total * 100 if total else 0
        print(f"  {cls:20s}: ${cls_pnl:+,.2f} ({pct:+.1f}% of total)")

    # Real EP details
    real = df[df["ep_class"] == "Real_EP"]
    if not real.empty:
        print("\n=== Real EP details ===")
        print(real[["ticker", "entry_session", "ticker_name", "total_pnl", "avg_hold_h", "rationale"]].to_string(index=False))

    print("\n=== Per-trade table ===")
    print(df[["ticker", "entry_session", "ticker_type", "n_legs", "total_qty", "total_pnl", "avg_hold_h", "score", "ep_class"]].to_string(index=False))


if __name__ == "__main__":
    main()
