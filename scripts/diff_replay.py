"""Diff arena replay output against production trade results.

Block 1 (B1.2) of the arena↔prod parity program — see
docs/research-log/58_arena_prod_parity_plan.md.

Inputs:
    data/replay/arena_session_<since>_<until>.parquet
    data/trade_results.jsonl

Output:
    docs/replay_diffs/<since>_to_<until>_diff_report.md (committed)

Tolerance bands (per session brief, op question 2):
    fill_delta ≤ 5 bps
    pnl_delta_per_trade ≤ $5 (or $20 for outliers)
    total_pnl_delta ≤ $20

Categorizes divergence by source (fill_price, exit_timing,
exit_codepath, missing_bars, carry_skipped) and ranks the top
contributors by aggregate $ impact. The actual fidelity number does
not matter for Block 1 — surfacing the gaps does.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

logger = logging.getLogger("diff_replay")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REPLAY_DIR = REPO_ROOT / "data" / "replay"
DEFAULT_REPORT_DIR = REPO_ROOT / "docs" / "replay_diffs"


# ── Tolerance bands ─────────────────────────────────────────────────


FILL_TOLERANCE_BPS = 5.0
PNL_TOLERANCE_USD = 5.0
PNL_OUTLIER_TOLERANCE_USD = 20.0
TOTAL_PNL_TOLERANCE_USD = 20.0


# ── Diff computation ────────────────────────────────────────────────


def categorize_divergence(row) -> str:
    """Classify each row's divergence source for the breakdown table.

    Refined post-Block-A audit (docs/replay_diffs/
    2026-04-28_error_decomposition.md): the prior categorization
    bucketed sign flips and 27× magnitude errors as `fill_price`,
    which was technically true (fill_delta_bps != 0) but
    operationally misleading. New ranked categories:

      direction_flip   — sign(prod_pnl) != sign(arena_pnl) AND both
                          are non-trivial. Almost always a Block B.2
                          exit-semantics issue (arena anchored to
                          bar-open, prod exited via tranche limits or
                          actual close fills well off bar anchor).
      exit_semantics   — large $-delta with SMALL fill_delta_bps. The
                          per-share fills look right; the dollar
                          divergence comes from arena's exit price
                          source disagreeing with prod's actual exit.
      qty_residual     — large $-delta tracks roughly with qty
                          multiplier residual (legacy: qty was wrong
                          pre-Block-B.1; keep this category as a
                          regression flag for any future recurrence).
      fill_price       — fill_delta_bps > tolerance AND pnl_delta is
                          consistent with bps-level slippage.
      within_tolerance — both fill bps and $-delta within bands.
    """
    status = row["replay_status"]
    if status == "missing_bars":
        return "missing_bars"
    if status == "carry_skipped":
        return "carry_skipped"
    if status == "bar_gap":
        return "bar_gap"
    if status not in ("ok",):
        return f"replay_error:{status}"
    fill_drift = max(
        abs(row["fill_delta_entry_bps"]),
        abs(row["fill_delta_exit_bps"]),
    )
    pnl_drift_usd = abs(row["pnl_delta_usd"])
    prod_pnl = row["prod_pnl"]
    arena_pnl = row["arena_pnl"]

    # Direction flip: prod and arena disagree on the trade's sign,
    # AND both are non-trivial in magnitude.
    if (
        abs(prod_pnl) > 10.0
        and abs(arena_pnl) > 10.0
        and (prod_pnl > 0) != (arena_pnl > 0)
    ):
        return "direction_flip"

    # Within tolerance — both gates pass
    if fill_drift <= FILL_TOLERANCE_BPS and pnl_drift_usd <= PNL_OUTLIER_TOLERANCE_USD:
        return "within_tolerance"

    # Decide between fill_price vs exit_semantics by ratio of bps to $
    # impact. Implied $-impact-from-fill ≈ qty × prod_entry_px × bps
    # / 1e4 / 2 (averaging entry+exit). If actual pnl_drift greatly
    # exceeds this, the source is exit semantics (or some other non-
    # fill effect), not slippage.
    qty = float(row.get("qty", 0))
    prod_entry_px = float(row.get("prod_entry_px", 0))
    implied_dollar_from_fill = qty * prod_entry_px * fill_drift / 1e4
    if implied_dollar_from_fill > 0 and pnl_drift_usd > 3 * implied_dollar_from_fill:
        return "exit_semantics"
    if fill_drift > FILL_TOLERANCE_BPS:
        return "fill_price"
    # Large pnl_drift but no clear bps signal — exit semantics is the
    # most likely cause given Block A audit.
    return "exit_semantics"


def is_within_tolerance(row) -> bool:
    """A row is in-tolerance if status==ok AND both delta bands satisfied.
    Outlier band ($20) is the relaxed per-trade threshold from the
    session brief (specifically anticipates SEGG-class entries)."""
    if row["replay_status"] != "ok":
        return False
    fill_drift = max(
        abs(row["fill_delta_entry_bps"]),
        abs(row["fill_delta_exit_bps"]),
    )
    if fill_drift > FILL_TOLERANCE_BPS:
        return False
    if abs(row["pnl_delta_usd"]) > PNL_OUTLIER_TOLERANCE_USD:
        return False
    return True


def render_report(*, df, since: str, until: str) -> str:
    """Build the markdown diff report."""
    n_total = len(df)
    n_ok = (df["replay_status"] == "ok").sum()
    n_missing_bars = (df["replay_status"] == "missing_bars").sum()
    n_carry = (df["replay_status"] == "carry_skipped").sum()
    n_bar_gap = (df["replay_status"] == "bar_gap").sum()
    n_other = n_total - n_ok - n_missing_bars - n_carry - n_bar_gap

    df["divergence_category"] = df.apply(categorize_divergence, axis=1)
    df["within_tolerance"] = df.apply(is_within_tolerance, axis=1)

    n_in_tolerance = int(df["within_tolerance"].sum())
    n_replayable = int(n_ok)  # ok rows are the only ones with computed deltas

    # Aggregate $-impact per category (for ranking top divergence sources)
    impact = (
        df[df["replay_status"] == "ok"]
        .groupby("divergence_category")["pnl_delta_usd"]
        .agg(lambda s: s.abs().sum())
        .sort_values(ascending=False)
    )

    # Total deltas
    total_prod_pnl = df[df["replay_status"] == "ok"]["prod_pnl"].sum()
    total_arena_pnl = df[df["replay_status"] == "ok"]["arena_pnl"].sum()
    total_pnl_delta = total_arena_pnl - total_prod_pnl

    # Top 3 divergence sources by $-impact
    top3 = list(impact.items())[:3]

    # ── Markdown ────
    lines = []
    lines.append(f"# Arena Replay Diff Report — {since} to {until}")
    lines.append("")
    lines.append(f"**Status:** Block 1 (B1.3) of the arena↔prod parity program.")
    lines.append(f"**Generated:** by `scripts/diff_replay.py` against `data/replay/arena_session_{since}_{until}.parquet`.")
    lines.append("")
    lines.append("> **PROVISIONAL** — this is the FIRST replay run before any Block 2 parity fixes. Divergences are expected and are the point. The aggregate fidelity number is not yet meaningful; the divergence ranking IS.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## §1 — Headline numbers")
    lines.append("")
    lines.append(f"- Trades in window: **{n_total}**")
    lines.append(f"- Replayed (status=ok): **{n_ok}**")
    lines.append(f"- Skipped — carry/snapshot: **{n_carry}**")
    lines.append(f"- Skipped — missing bars: **{n_missing_bars}**")
    lines.append(f"- Skipped — bar gap (timestamp outside bar range): **{n_bar_gap}**")
    lines.append(f"- Other replay errors: **{n_other}**")
    lines.append("")
    lines.append(f"- Within tolerance (≤{FILL_TOLERANCE_BPS} bps fill, ≤${PNL_OUTLIER_TOLERANCE_USD} $-delta): **{n_in_tolerance} / {n_replayable}**")
    lines.append("")
    lines.append(f"- Σ prod P&L (replayed only): **${total_prod_pnl:+,.2f}**")
    lines.append(f"- Σ arena P&L (replayed only): **${total_arena_pnl:+,.2f}**")
    lines.append(f"- Σ delta: **${total_pnl_delta:+,.2f}** (tolerance band: ±${TOTAL_PNL_TOLERANCE_USD})")
    lines.append("")
    lines.append("## §2 — Top divergence sources by aggregate $-impact")
    lines.append("")
    if top3:
        lines.append("| Rank | Source | Σ |Δ| ($) |")
        lines.append("|---:|---|---:|")
        for i, (cat, val) in enumerate(top3, start=1):
            lines.append(f"| {i} | `{cat}` | ${val:,.2f} |")
        lines.append("")
    else:
        lines.append("(no replayed trades — coverage gap dominates)")
        lines.append("")

    lines.append("## §3 — Per-trade detail")
    lines.append("")
    lines.append("| Ticker | Date | Status | Cat | Prod P&L | Arena P&L | Δ ($) | Δ entry (bps) | Δ exit (bps) | Qty |")
    lines.append("|---|---|---|---|---:|---:|---:|---:|---:|---:|")
    for _, r in df.iterrows():
        lines.append(
            f"| {r['ticker']} | {r['session_date']} | `{r['replay_status']}` | "
            f"`{r['divergence_category']}` | "
            f"${r['prod_pnl']:+,.2f} | ${r['arena_pnl']:+,.2f} | "
            f"${r['pnl_delta_usd']:+,.2f} | "
            f"{r['fill_delta_entry_bps']:+.1f} | {r['fill_delta_exit_bps']:+.1f} | "
            f"{int(r['qty'])} |"
        )
    lines.append("")

    lines.append("## §4 — What these numbers mean (and what they don't)")
    lines.append("")
    lines.append("**SIZE-INDEPENDENT signals (interpret these first):**")
    lines.append("- `Δ entry (bps)` and `Δ exit (bps)` measure arena's fill-price fidelity vs the bar-open at the same minute. These are the cleanest signal of whether arena's slippage model matches what the broker actually delivered.")
    lines.append("")
    lines.append("**SIZE-DEPENDENT signals (interpret with caution):**")
    lines.append("- `Δ ($)` and `Σ delta` are computed using a tier-1 sizing default (50% × $150K equity / entry_price), because `data/trade_results.jsonl` does NOT carry per-trade quantity (Bug AO orchestrator-hook gap). Production may have used tier-2 or tier-3 sizing for some trades, in which case the $-delta over-states what arena would have produced at the actual prod size.")
    lines.append("")
    lines.append("**Coverage gaps surfaced:**")
    lines.append(f"- {n_missing_bars} trades have no bar recordings (production traded tickers we weren't capturing). This is a Block 2 finding: bar-recording coverage must match the candidate universe.")
    lines.append(f"- {n_carry} rows are LIDR carry snapshots — will be replayed properly in Block 4 (cross-session state).")
    lines.append(f"- {n_bar_gap} trades had entry/exit outside the bar range (typically pre-market entries at 09:29 ET when bars start at 09:30).")
    lines.append("")

    lines.append("## §5 — Top 3 parity gaps to address in Block 2")
    lines.append("")
    if top3:
        for i, (cat, val) in enumerate(top3, start=1):
            lines.append(f"{i}. **`{cat}`** (Σ |Δ| = ${val:,.2f}) — see Block 2 plan")
    lines.append("")
    lines.append("Note: even if some categories are 'within_tolerance', they only test arena's pricing on a tiny sample. Larger-sample sweeps will surface new divergences.")
    lines.append("")

    lines.append("## §6 — Discipline status check")
    lines.append("")
    perfect_match = (n_in_tolerance == n_replayable and n_replayable > 0)
    if perfect_match and n_replayable >= 5:
        lines.append("⚠️ **STOP CONDITION TRIPPED:** all replayed trades match within tolerance on the first run. Per the Block 1 brief: arena currently has constant slippage and never 403s. Perfect match suggests the replay is echoing prod outputs rather than running arena machinery. Investigate before proceeding to Block 2.")
    else:
        lines.append("✅ Diff report generated; proceed to Block 2 with the ranked divergence sources above.")
    lines.append("")

    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--since", default="2026-04-22")
    p.add_argument("--until", default="2026-04-28")
    p.add_argument("--replay-dir", type=Path, default=DEFAULT_REPLAY_DIR)
    p.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s: %(message)s",
    )

    try:
        import pandas as pd
    except ImportError:
        logger.error("pandas required")
        return 1

    replay_path = args.replay_dir / f"arena_session_{args.since}_{args.until}.parquet"
    if not replay_path.exists():
        logger.error("replay file not found: %s — run scripts/arena_replay_session.py first", replay_path)
        return 1
    df = pd.read_parquet(replay_path)
    logger.info("loaded %d rows from %s", len(df), replay_path)

    md = render_report(df=df, since=args.since, until=args.until)

    args.report_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.report_dir / f"{args.since}_to_{args.until}_diff_report.md"
    out_path.write_text(md, encoding="utf-8")
    logger.info("wrote %s", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
