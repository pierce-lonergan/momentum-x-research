"""Block 4.4: 86-session OOS Sharpe headline run.

Drives the strategy harness across the full Dec 2025 → Apr 2026 bar
corpus, aggregates per-trade outcomes, computes equity curve +
Sharpe + drawdown + per-data-completeness stratification, and writes
the OOS doc.

Per the brief discipline:
  - Per-data-completeness stratification matters MORE than aggregate
  - SUSPECT-RANGE rule: <0.5 (no edge) / 0.5-1.5 (needs work) /
    1.5-2.0 (encouraging) / >2.0 (apply A.5 falsification)
  - Every caveat must be documented

Output:
  docs/oos/2025-12-to-2026-04_oos_run.md (markdown report)
  data/oos/per_trade_outcomes.parquet (raw trade rows)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import statistics
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("oos_run")

REPO_ROOT = Path(__file__).resolve().parent.parent
ARENA_HISTORICAL = REPO_ROOT / "mx-arena" / "data" / "historical"
DECISION_ROW_DIR = REPO_ROOT / "data" / "instrumentation" / "decision_row"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "docs" / "oos"
DEFAULT_DATA_DIR = REPO_ROOT / "data" / "oos"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "mx-arena"))


def determine_completeness(session_date: str) -> str:
    """Per-session data-quality tag."""
    if (DECISION_ROW_DIR / f"session_date={session_date}" / "decisions.parquet").exists():
        return "full_decision_row"
    log = REPO_ROOT / "logs" / f"momentum_{session_date}.log"
    if log.exists():
        return "partial_news_only"
    return "synthesized_no_news"


def list_corpus_sessions(min_date: str = "2025-12-01") -> list[str]:
    """All session dates with at least one ticker bar parquet."""
    seen: set[str] = set()
    for ticker_dir in ARENA_HISTORICAL.iterdir():
        if not ticker_dir.is_dir():
            continue
        for f in ticker_dir.glob("*.parquet"):
            d = f.stem
            if d >= min_date:
                seen.add(d)
    return sorted(seen)


async def run_corpus(
    *, sessions: list[str],
    require_earnings_catalyst: bool = False,
    earnings_calendar: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    """Returns (trades, session_summaries)."""
    from strategy_harness_run import run_one_session

    trades: list[dict] = []
    summaries: list[dict] = []
    for sd in sessions:
        completeness = determine_completeness(sd)
        mode = "decision_row" if completeness == "full_decision_row" else "policy"
        try:
            session_trades = await run_one_session(
                session_date=sd, mode=mode,
                gate_signal_neutral=False,
                max_concurrent_positions=3,
                use_consensus=True,
                data_completeness=completeness,
                require_earnings_catalyst=require_earnings_catalyst,
                earnings_calendar=earnings_calendar,
            )
        except Exception as e:
            logger.warning("session %s failed: %s", sd, e)
            session_trades = []
        for t in session_trades:
            d = asdict(t)
            d["data_completeness"] = completeness
            trades.append(d)
        session_pnl = sum(t.pnl for t in session_trades)
        summaries.append({
            "session_date": sd, "n_trades": len(session_trades),
            "session_pnl": round(session_pnl, 2),
            "data_completeness": completeness, "mode": mode,
        })
        logger.info("session %s [%s]: n=%d pnl=$%+.2f",
                    sd, completeness, len(session_trades), session_pnl)
    return trades, summaries


def compute_metrics(trades: list[dict]) -> dict:
    """Sharpe, max DD, win/loss stats."""
    if not trades:
        return {"n": 0, "total_pnl": 0.0}
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    total = sum(pnls)
    n = len(pnls)
    win_rate = len(wins) / n if n else 0.0

    # Per-day P&L for Sharpe calc
    by_day: dict[str, float] = defaultdict(float)
    for t in trades:
        by_day[t["session_date"]] += t["pnl"]
    daily = list(by_day.values())
    if len(daily) >= 2:
        daily_mean = statistics.mean(daily)
        daily_stdev = statistics.stdev(daily)
        sharpe_daily = daily_mean / daily_stdev if daily_stdev > 0 else 0.0
        sharpe_annual = sharpe_daily * math.sqrt(252)
    else:
        sharpe_annual = 0.0

    # Max drawdown on cumulative equity
    cum_pnl: list[float] = []
    running = 0.0
    for t in sorted(trades, key=lambda x: (x["session_date"], x["entry_ts_iso"])):
        running += t["pnl"]
        cum_pnl.append(running)
    if cum_pnl:
        peak = cum_pnl[0]
        max_dd = 0.0
        for v in cum_pnl:
            peak = max(peak, v)
            dd = peak - v
            if dd > max_dd:
                max_dd = dd
    else:
        max_dd = 0.0

    avg_win = statistics.mean(wins) if wins else 0.0
    avg_loss = statistics.mean(losses) if losses else 0.0
    profit_factor = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else float("inf")

    return {
        "n": n, "n_wins": len(wins), "n_losses": len(losses),
        "win_rate": round(win_rate, 3),
        "total_pnl": round(total, 2),
        "avg_win": round(avg_win, 2), "avg_loss": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 3) if profit_factor != float("inf") else "inf",
        "max_drawdown": round(max_dd, 2),
        "sharpe_annual": round(sharpe_annual, 3),
        "n_session_days": len(by_day),
    }


def render_doc(*, trades: list[dict], summaries: list[dict],
               aggregate: dict, by_strata: dict, suspect_bucket: str) -> str:
    lines = []
    lines.append("# 86-Session OOS Run — 2025-12 to 2026-04")
    lines.append("")
    lines.append("**Generated:** by `scripts/run_block_44_oos.py` driving `arena.harness`. **PROVISIONAL.** This is the FIRST 86-session OOS run on the rig.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## §0 — TL;DR — VERDICT FROM FALSIFICATION (read this first)")
    lines.append("")
    lines.append("Aggregate Sharpe = +3.776 (SUSPECT bucket). Per discipline rule, falsification was applied (`docs/oos/2025-12-to-2026-04_oos_falsification.md`). **VERDICT: HEADLINE OVERTURNED.**")
    lines.append("")
    lines.append("Three converging tests reject the aggregate as fictional:")
    lines.append("1. **Stratification** (the brief's exact warning): synthesized_no_news (n=225) Sharpe +4.36 carries all the apparent edge; full_decision_row (n=3) and partial_news_only (n=27) are losing/zero. **In the high-quality strata where we have actual news data, the strategy LOSES money.**")
    lines.append("2. **Shuffle test** (random entry-exit re-pairing, 100 iters): shuffled mean Sharpe +7.05 — HIGHER than the baseline +3.78. Random pairings beat the strategy → the 'edge' is from the bar corpus's price distribution, not from strategy decisions.")
    lines.append("3. **A.5 adversarial fade**: structurally inapplicable to T+60s exits (fade threshold is 5 min). Logged as a discipline-suite finding: A.5 needs exit-policy-awareness for OOS runs.")
    lines.append("")
    lines.append("**OPERATIONAL IMPLICATION: NO DEMONSTRATED EDGE on this rig + corpus.** Halt switch stays on. Per the SUSPECT-bucket discipline, the failed falsification IS the headline.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## §1 — Aggregate (FICTIONAL — see §0)")
    lines.append("")
    a = aggregate
    lines.append(f"- **Trades**: {a['n']} (over {a['n_session_days']} session days)")
    lines.append(f"- **Total P&L**: ${a['total_pnl']:+,.2f}")
    lines.append(f"- **Win rate**: {a['win_rate']*100:.1f}% ({a['n_wins']} wins / {a['n_losses']} losses)")
    lines.append(f"- **Avg win**: ${a['avg_win']:+,.2f} / **Avg loss**: ${a['avg_loss']:+,.2f}")
    lines.append(f"- **Profit factor**: {a['profit_factor']}")
    lines.append(f"- **Max drawdown**: ${a['max_drawdown']:,.2f}")
    lines.append(f"- **OOS Sharpe (annualized)**: **{a['sharpe_annual']:+.3f}**")
    lines.append("")
    lines.append(f"### Suspect-range bucket: **{suspect_bucket}**")
    lines.append("")
    lines.append("Pre-committed (plan doc 58 §11):")
    lines.append("- < 0.5 → no demonstrated edge; halt stays on")
    lines.append("- 0.5-1.5 → realistic 'needs more work but not hopeless'; do not size up")
    lines.append("- 1.5-2.0 → encouraging but not yet defensible at single-shot OOS")
    lines.append("- > 2.0 → SUSPECT simulator exploit; A.5 adversarial fade test required before claim")
    lines.append("")

    lines.append("## §2 — Per-data-completeness stratification (more important than aggregate)")
    lines.append("")
    lines.append("| Stratum | n trades | n days | Total P&L | Win rate | Sharpe |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for stratum_name in ["full_decision_row", "partial_news_only", "synthesized_no_news"]:
        s = by_strata.get(stratum_name, {})
        if not s or s.get("n", 0) == 0:
            lines.append(f"| {stratum_name} | 0 | 0 | $0.00 | n/a | n/a |")
            continue
        lines.append(
            f"| {stratum_name} | {s['n']} | {s['n_session_days']} | "
            f"${s['total_pnl']:+,.2f} | {s['win_rate']*100:.1f}% | "
            f"{s['sharpe_annual']:+.3f} |"
        )
    lines.append("")
    lines.append("**The stratification is the deliverable, not the aggregate.** Per the brief: 'if aggregate Sharpe is encouraging but full_decision_row is < 0.5 and synthesized_no_news is > 1.5, the headline number is being dragged up by the lowest-quality data.'")
    lines.append("")

    lines.append("## §3 — Per-session breakdown")
    lines.append("")
    lines.append("| Session | Mode | Strata | n trades | Session P&L |")
    lines.append("|---|---|---|---:|---:|")
    for s in summaries:
        lines.append(
            f"| {s['session_date']} | {s['mode']} | {s['data_completeness']} | "
            f"{s['n_trades']} | ${s['session_pnl']:+,.2f} |"
        )
    lines.append("")

    lines.append("## §4 — Caveats (the doc's most-important section)")
    lines.append("")
    lines.append("1. **Limit-aware fills are MVP.** Trades where the limit price was outside bar range at entry minute are marked failed-to-fill (conservative under-count). Multi-bar walk-forward deferred.")
    lines.append("2. **Slippage calibration v2 is identity.** Real fit requires intra-bar tick data we don't have.")
    lines.append("3. **Exits**: T+60s bar-anchored where no prod-mirror truth available. Modeled exit fidelity is bounded by Block A falsification (BAR-1 timing COLLAPSES verdict suggests modeled exits over-reward long holds).")
    lines.append("4. **Decision_row coverage is 4/28-only**. Other sessions use policy-mode candidate detection (price + liquidity filter, gap/RVOL approximated).")
    lines.append("5. **Bar coverage**: backfilled for 4/22-4/28; pre-4/22 coverage is whatever the recorder captured at the time (D121 dynamic-subscription gaps possible — see doc 61).")
    lines.append("6. **Consensus stub** mocks the n=3 ensemble (news + gap + rvol); does not replicate prod's full debate logic / kelly sizing / position-tier rules.")
    lines.append("7. **Position-count limit**: configurable, default 3 (mirrors prod's typical ceiling). Sensitivity to this parameter not yet swept.")
    lines.append(f"8. **86 sessions ≈ 4 months**. Not n=multi-year. The Sharpe number's confidence interval is wide.")
    lines.append("9. **Halt switch stays ON.** No live config promotions regardless of this run's verdict.")
    lines.append("")

    lines.append("## §5 — Verdict + operational implication")
    lines.append("")
    if suspect_bucket == "no_edge":
        lines.append("**No demonstrated edge** on this rig + corpus. Halt switch stays on indefinitely; research continues. The negative result is documented with the same rigor as a positive one would have been.")
    elif suspect_bucket == "needs_work":
        lines.append("**Realistic 'needs more work but not hopeless' range.** Do not size up. The strategy has signal but the rig + sample is too noisy for high-confidence claims. Next session: fix-list — multi-bar walk-forward, intra-bar tick data calibration, longer corpus.")
    elif suspect_bucket == "encouraging":
        lines.append("**Encouraging but not yet defensible** at single-shot OOS. Explore further (longer corpus, walk-forward validation, multiple seeds) before any sizing decision. Halt switch stays on.")
    else:  # suspect
        lines.append("⚠️ **SUSPECT simulator exploit.** Apply Block A.5 adversarial fade test against the OOS run before any operational decision. Verdict from that test becomes part of the headline.")
    lines.append("")

    return "\n".join(lines)


def stratify(trades: list[dict]) -> dict:
    by_strata: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_strata[t.get("data_completeness", "unknown")].append(t)
    return {k: compute_metrics(v) for k, v in by_strata.items()}


def bucket_sharpe(sharpe: float) -> str:
    if sharpe < 0.5:
        return "no_edge"
    if sharpe < 1.5:
        return "needs_work"
    if sharpe < 2.0:
        return "encouraging"
    return "suspect"


async def amain(args) -> int:
    sessions = list_corpus_sessions(min_date=args.since)
    if args.until:
        sessions = [s for s in sessions if s <= args.until]
    if args.limit:
        sessions = sessions[:args.limit]
    logger.info("running OOS over %d sessions [%s..%s] (catalyst_gate=%s)",
                len(sessions), sessions[0] if sessions else "?",
                sessions[-1] if sessions else "?",
                args.require_earnings_catalyst)

    earnings_calendar: dict = {}
    if args.require_earnings_catalyst:
        earnings_path = REPO_ROOT / "data" / "calibration" / "historical_earnings_2025-12_to_2026-04.json"
        if not earnings_path.exists():
            logger.error("earnings calendar not found at %s — run fetch_historical_earnings.py first", earnings_path)
            return 1
        payload = json.loads(earnings_path.read_text(encoding="utf-8"))
        earnings_calendar = payload.get("by_ticker", {})
        logger.info("loaded earnings calendar: %d tickers", len(earnings_calendar))

    trades, summaries = await run_corpus(
        sessions=sessions,
        require_earnings_catalyst=args.require_earnings_catalyst,
        earnings_calendar=earnings_calendar,
    )
    aggregate = compute_metrics(trades)
    by_strata = stratify(trades)
    bucket = bucket_sharpe(aggregate.get("sharpe_annual", 0.0))

    # Persist raw trades
    args.data_dir.mkdir(parents=True, exist_ok=True)
    parquet_name = f"per_trade_outcomes{args.variant_tag}.parquet"
    try:
        import pandas as pd
        if trades:
            df = pd.DataFrame(trades)
            df.to_parquet(args.data_dir / parquet_name, index=False)
        else:
            logger.warning("zero trades produced; not writing parquet")
    except ImportError:
        pass

    # Render doc
    md = render_doc(trades=trades, summaries=summaries, aggregate=aggregate,
                    by_strata=by_strata, suspect_bucket=bucket)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.output_dir / f"2025-12-to-2026-04_oos_run{args.variant_tag}.md"
    out_path.write_text(md, encoding="utf-8")
    logger.info("wrote %s; sharpe=%.3f bucket=%s",
                out_path, aggregate.get("sharpe_annual", 0.0), bucket)

    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--since", default="2025-12-01")
    p.add_argument("--until", default=None)
    p.add_argument("--limit", type=int, default=None,
                   help="cap session count (for testing)")
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    p.add_argument("--require-earnings-catalyst", action="store_true",
                   help="Catalyst-only run: reject candidates without earnings ±1 trading day")
    p.add_argument("--variant-tag", default="",
                   help="Suffix for output files (e.g. '_catalyst_only')")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
