"""Bug AU resolution: side-aware corpus reconciliation logic.

Per PROMPT_08 §6 and the Bug AU finding doc
(docs/audits/2026-04-30_broker_truth_initial_pull.md).

**Discipline rule** (PROMPT_08 §13.1): broker is canonical, always.
When broker and journal disagree on side or qty, broker wins. Journal
becomes a forensic field; never the other way around.

This module is pulled out as a separate file so it can be unit-tested
in isolation (independent of the much larger extract_prod_qty_truth.py
script). The reconciliation function is a pure function of:
    - the existing journal-derived row (ticker/date/qty/price/source)
    - the broker_truth fills for that (ticker, session_date)
and returns an enriched dict with the new schema columns.

New columns added (per PROMPT_08 §6.1):
    side: "long" | "short" | "long_legacy" (when broker_truth missing)
    broker_qty: int (canonical; 0 if broker_truth missing for this ticker_date)
    journal_qty: int (preserved forensic — what the journal recorded)
    qty_drift_pct: float (broker-vs-journal drift, NaN if broker missing)
    n_legs: int (number of broker fill events on the matched side)
    data_integrity_flag: str | None (one of: side_mismatch_AU,
        qty_mismatch_AU, missing_broker_truth, qty_mismatch_AU_lookback_capped)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import pandas as pd


# Threshold above which qty drift is flagged. Smaller drifts are
# typically broker rounding / fee accounting / partial-fill noise.
QTY_DRIFT_FLAG_THRESHOLD_PCT = 5.0


@dataclass(frozen=True)
class ReconciledRow:
    """The output of reconciling one journal row against broker_truth."""
    side: str
    broker_qty: int
    journal_qty: int
    qty_drift_pct: float
    n_legs: int
    data_integrity_flag: Optional[str]
    # Canonical price/qty for downstream consumers (broker wins when
    # available; journal otherwise).
    canonical_qty: int
    canonical_entry_avg_px: float


def reconcile_one_row(
    *,
    ticker: str,
    session_date: str,        # "YYYY-MM-DD"
    journal_qty: int,
    journal_entry_avg_px: float,
    broker_fills: pd.DataFrame,  # filtered to (ticker, session_date) already
) -> ReconciledRow:
    """Reconcile a single journal row against broker_truth fills.

    `broker_fills` may be empty (broker_truth missing for this date) — in
    that case we preserve the journal values, mark side as "long_legacy"
    (the existing implicit assumption) and flag the row.

    `broker_fills` should contain rows with at least: side, qty, price.
    Sides recognized: "buy", "sell", "sell_short", "buy_to_cover".
    """
    if broker_fills.empty:
        return ReconciledRow(
            side="long_legacy",
            broker_qty=0,
            journal_qty=journal_qty,
            qty_drift_pct=float("nan"),
            n_legs=0,
            data_integrity_flag="missing_broker_truth",
            canonical_qty=journal_qty,
            canonical_entry_avg_px=journal_entry_avg_px,
        )

    buys = broker_fills[broker_fills["side"] == "buy"]
    short_sells = broker_fills[broker_fills["side"] == "sell_short"]

    # Determine canonical side.
    # If both buys and short_sells exist: this is two trades on the same
    # ticker the same day (or a long entry + a separate short entry).
    # We pick the side that matches journal's implicit "long" by default,
    # because most journal entries are long. If only one side exists, use it.
    if not buys.empty and short_sells.empty:
        side = "long"
        side_fills = buys
    elif buys.empty and not short_sells.empty:
        side = "short"
        side_fills = short_sells
    elif not buys.empty and not short_sells.empty:
        # Both sides present. Default to long (journal's assumption) but
        # record the ambiguity in the flag. Downstream can investigate.
        side = "long"
        side_fills = buys
    else:
        # No buys, no shorts. Could be all-sells (closing-only) or weird.
        # Preserve journal as the canonical fallback.
        return ReconciledRow(
            side="long_legacy",
            broker_qty=0,
            journal_qty=journal_qty,
            qty_drift_pct=float("nan"),
            n_legs=0,
            data_integrity_flag="missing_broker_truth",
            canonical_qty=journal_qty,
            canonical_entry_avg_px=journal_entry_avg_px,
        )

    broker_qty = int(side_fills["qty"].sum())
    n_legs = len(side_fills)
    # VWAP from broker fills — this is the canonical entry price.
    if broker_qty > 0:
        broker_entry_vwap = float(
            (side_fills["price"] * side_fills["qty"]).sum() / broker_qty
        )
    else:
        broker_entry_vwap = journal_entry_avg_px  # fallback

    # Compute qty drift.
    if journal_qty > 0:
        qty_drift_pct = (broker_qty - journal_qty) / journal_qty * 100.0
    else:
        qty_drift_pct = float("nan")

    # Determine flag.
    flag: Optional[str] = None

    # Side mismatch: journal implicit-long, broker shows short OR
    # both sides present (ambiguous).
    if side == "short":
        # Journal's implicit "long" is wrong — broker shows short.
        flag = "side_mismatch_AU"
    elif (not buys.empty) and (not short_sells.empty):
        # Both sides present — flag for investigation.
        flag = "side_ambiguous_AU"
    # Qty mismatch (only if no side mismatch — side mismatch is more severe).
    elif (not math.isnan(qty_drift_pct)) and abs(qty_drift_pct) > QTY_DRIFT_FLAG_THRESHOLD_PCT:
        flag = "qty_mismatch_AU"

    # Canonical values: broker wins for qty + side.
    canonical_qty = broker_qty
    canonical_entry_avg_px = broker_entry_vwap

    return ReconciledRow(
        side=side,
        broker_qty=broker_qty,
        journal_qty=journal_qty,
        qty_drift_pct=qty_drift_pct,
        n_legs=n_legs,
        data_integrity_flag=flag,
        canonical_qty=canonical_qty,
        canonical_entry_avg_px=canonical_entry_avg_px,
    )


def load_broker_fills_for(
    activities_df: pd.DataFrame,
    ticker: str,
    session_date: str,
) -> pd.DataFrame:
    """Filter the full broker activities DataFrame to one (ticker, date).

    activities_df: the parquet from `data/broker_truth/activities.parquet`
    session_date: ET date string "YYYY-MM-DD"

    Returns a filtered DataFrame with at minimum: side, qty, price.
    """
    if activities_df.empty:
        return activities_df.iloc[0:0]
    df = activities_df.copy()
    if "ts" not in df.columns:
        df["ts"] = pd.to_datetime(df["transaction_time"])
    if "et_date" not in df.columns:
        df["et_date"] = df["ts"].dt.tz_convert("America/New_York").dt.date.astype(str)
    sub = df[(df["symbol"] == ticker) & (df["et_date"] == session_date)]
    return sub
