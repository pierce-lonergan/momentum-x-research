"""Block A: Audit every truth corpus row against Polygon ticks.

For each row in data/replay/prod_qty_truth.parquet (and prod_exit_truth.parquet),
load Polygon NBBO + trade ticks for that (ticker, session_date) and check:

  * Is the recorded prod_entry_avg_px (or prod_exit_avg_px) within the
    full-day price range that ACTUALLY traded?
  * Is there a trade at that price within ±5s of the recorded entry_ts?
  * What is the NBBO mid at the recorded entry_ts?
  * Slippage vs NBBO mid (bps)
  * 5s VWAP of nearby trades (sanity reference)

Output:
  data/audits/truth_corpus_audit_2026-04-29.parquet
  data/audits/truth_corpus_exit_audit_2026-04-29.parquet  (for exit truth)

Severity classification:
  HIGH:   recorded price is OUTSIDE the full-day price range (impossible)
  MEDIUM: recorded price is in-range but no nearby trade ±5s and >500 bps from NBBO mid
  LOW:    recorded price is in-range, within 500 bps of NBBO mid
  CLEAN:  recorded price within 500 bps AND has a nearby trade ±$0.005 within ±5s
"""
from __future__ import annotations

import sys
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
TICK_ROOT = REPO_ROOT / "data" / "polygon_backfill" / "tick_data"
QTY_TRUTH = REPO_ROOT / "data" / "replay" / "prod_qty_truth.parquet"
EXIT_TRUTH = REPO_ROOT / "data" / "replay" / "prod_exit_truth.parquet"
OUT_QTY = REPO_ROOT / "data" / "audits" / "truth_corpus_audit_2026-04-29.parquet"
OUT_EXIT = REPO_ROOT / "data" / "audits" / "truth_corpus_exit_audit_2026-04-29.parquet"
OUT_QTY.parent.mkdir(parents=True, exist_ok=True)


def _classify_severity(
    recorded_px: float, day_min: float, day_max: float,
    nbbo_mid: float, has_nearby_trade_at_px: bool, bps_vs_mid: float,
) -> str:
    if recorded_px < day_min - 1e-9 or recorded_px > day_max + 1e-9:
        return "HIGH"
    if not has_nearby_trade_at_px and abs(bps_vs_mid) > 500:
        return "MEDIUM"
    if has_nearby_trade_at_px and abs(bps_vs_mid) <= 500:
        return "CLEAN"
    return "LOW"


def _audit_row(
    ticker: str, sess_date: str, recorded_ts: pd.Timestamp,
    recorded_px: float, kind: str = "entry",
) -> dict:
    out = {
        "ticker": ticker,
        "session_date": sess_date,
        "recorded_ts": recorded_ts.isoformat(),
        "recorded_px": recorded_px,
        "kind": kind,
        "tick_data_present": False,
        "polygon_day_min_px": None,
        "polygon_day_max_px": None,
        "polygon_n_trades_total": 0,
        "in_day_range": None,
        "nbbo_bid_at_ts": None, "nbbo_ask_at_ts": None,
        "nbbo_mid_at_ts": None, "nbbo_lag_ms": None,
        "trades_in_5s_window_n": 0,
        "trades_in_5s_window_min_px": None,
        "trades_in_5s_window_max_px": None,
        "trades_in_5s_window_vwap": None,
        "has_nearby_trade_at_recorded_px": None,
        "slippage_bps_vs_mid": None,
        "slippage_bps_vs_5s_vwap": None,
        "severity": "UNAUDITED",
        "notes": "",
    }

    quotes_p = TICK_ROOT / sess_date / f"{ticker}_quotes.parquet"
    trades_p = TICK_ROOT / sess_date / f"{ticker}_trades.parquet"
    if not quotes_p.exists() and not trades_p.exists():
        out["severity"] = "NO_TICK_DATA"
        out["notes"] = "no Polygon tick data for this (ticker,date)"
        return out
    out["tick_data_present"] = True

    target_ns = int(recorded_ts.timestamp() * 1e9)

    # NBBO at target ts
    if quotes_p.exists():
        q = pd.read_parquet(quotes_p)
        if not q["sip_ts_ns"].is_monotonic_increasing:
            q = q.sort_values("sip_ts_ns").reset_index(drop=True)
        idx = q["sip_ts_ns"].searchsorted(target_ns, side="right") - 1
        if idx >= 0:
            row = q.iloc[idx]
            bid = float(row["bid_price"]); ask = float(row["ask_price"])
            mid = (bid + ask) / 2 if bid > 0 and ask > 0 else 0.0
            out["nbbo_bid_at_ts"] = bid
            out["nbbo_ask_at_ts"] = ask
            out["nbbo_mid_at_ts"] = mid if mid > 0 else None
            out["nbbo_lag_ms"] = (target_ns - int(row["sip_ts_ns"])) / 1e6
            if mid > 0:
                out["slippage_bps_vs_mid"] = (recorded_px - mid) / mid * 1e4

    # Trades full-day range
    if trades_p.exists():
        t = pd.read_parquet(trades_p)
        if not t.empty:
            out["polygon_day_min_px"] = float(t["price"].min())
            out["polygon_day_max_px"] = float(t["price"].max())
            out["polygon_n_trades_total"] = int(len(t))
            out["in_day_range"] = (
                out["polygon_day_min_px"] - 1e-9 <= recorded_px
                <= out["polygon_day_max_px"] + 1e-9
            )

            window_ns = 5 * 10**9
            tw = t[
                (t["sip_ts_ns"] >= target_ns - window_ns) &
                (t["sip_ts_ns"] <= target_ns + window_ns)
            ]
            out["trades_in_5s_window_n"] = int(len(tw))
            if not tw.empty:
                out["trades_in_5s_window_min_px"] = float(tw["price"].min())
                out["trades_in_5s_window_max_px"] = float(tw["price"].max())
                vwap = float((tw["price"] * tw["size"]).sum() / tw["size"].sum())
                out["trades_in_5s_window_vwap"] = vwap
                out["slippage_bps_vs_5s_vwap"] = (recorded_px - vwap) / vwap * 1e4
                # Match within ±$0.005 (≈ half-tick on $1 stocks)
                tol = max(0.005, recorded_px * 0.001)
                out["has_nearby_trade_at_recorded_px"] = bool(
                    ((tw["price"] - recorded_px).abs() <= tol).any()
                )

    # Severity
    if out["polygon_day_min_px"] is None:
        out["severity"] = "NO_TRADE_DATA"
    else:
        out["severity"] = _classify_severity(
            recorded_px,
            out["polygon_day_min_px"], out["polygon_day_max_px"],
            out["nbbo_mid_at_ts"] or 0.0,
            bool(out["has_nearby_trade_at_recorded_px"]),
            out["slippage_bps_vs_mid"] or 0.0,
        )

    # Notes
    notes_parts = []
    if out["severity"] == "HIGH":
        notes_parts.append(
            f"recorded ${recorded_px} OUTSIDE day range "
            f"[${out['polygon_day_min_px']:.4f}, ${out['polygon_day_max_px']:.4f}]"
        )
    if out["nbbo_lag_ms"] and out["nbbo_lag_ms"] > 1000:
        notes_parts.append(f"nbbo_lag={out['nbbo_lag_ms']:.0f}ms (stale quote)")
    if out["trades_in_5s_window_n"] == 0:
        notes_parts.append("no trades in ±5s window")
    if not out["has_nearby_trade_at_recorded_px"] and out["trades_in_5s_window_n"] > 0:
        notes_parts.append(
            f"no trade matched recorded px (window range "
            f"${out['trades_in_5s_window_min_px']:.4f}-${out['trades_in_5s_window_max_px']:.4f})"
        )
    out["notes"] = " | ".join(notes_parts)
    return out


def _audit_corpus(corpus_path: Path, ts_col: str, px_col: str, kind: str) -> pd.DataFrame:
    if not corpus_path.exists():
        print(f"!! corpus missing: {corpus_path}")
        return pd.DataFrame()
    df = pd.read_parquet(corpus_path)
    print(f"\n=== Auditing {corpus_path.name} ({len(df)} rows, kind={kind}) ===")
    rows = []
    for _, t in df.iterrows():
        ticker = str(t["ticker"]).upper()
        sess = str(t["session_date"])
        ts = pd.Timestamp(t[ts_col]).tz_convert("UTC") if pd.Timestamp(t[ts_col]).tzinfo else pd.Timestamp(t[ts_col]).tz_localize("UTC")
        px = float(t[px_col])
        r = _audit_row(ticker, sess, ts, px, kind=kind)
        rows.append(r)
        print(
            f"  [{r['severity']:<13}] {ticker:<5} {sess} px=${px:<8.4f} "
            f"day_range=[{r['polygon_day_min_px']}, {r['polygon_day_max_px']}] "
            f"mid=${r['nbbo_mid_at_ts']} bps_vs_mid={r['slippage_bps_vs_mid']}"
        )
        if r["notes"]:
            print(f"      notes: {r['notes']}")
    return pd.DataFrame(rows)


def main() -> None:
    qty_audit = _audit_corpus(QTY_TRUTH, ts_col="entry_ts", px_col="prod_entry_avg_px", kind="entry")
    if not qty_audit.empty:
        qty_audit.to_parquet(OUT_QTY, index=False)
        print(f"\nWrote {OUT_QTY.relative_to(REPO_ROOT)} ({len(qty_audit)} rows)")

    exit_audit = _audit_corpus(EXIT_TRUTH, ts_col="entry_ts", px_col="prod_exit_avg_px", kind="exit")
    if not exit_audit.empty:
        exit_audit.to_parquet(OUT_EXIT, index=False)
        print(f"Wrote {OUT_EXIT.relative_to(REPO_ROOT)} ({len(exit_audit)} rows)")

    # Summary
    print("\n=== SEVERITY SUMMARY ===")
    for label, df in [("entry", qty_audit), ("exit", exit_audit)]:
        if df.empty:
            continue
        c = df["severity"].value_counts().to_dict()
        print(f"  {label}: {c}")


if __name__ == "__main__":
    main()
