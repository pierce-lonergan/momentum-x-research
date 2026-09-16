"""Sweep the catalyst-gate threshold across recorded trades.

Block 3.2 of the arena↔prod parity program. See plan doc 58.

The cheapest-highest-information sweep per the session brief: the
catalyst gate is the next live-to-prod change being considered (per
evaluation/2026-04-28-catalyst-stratification.md §6). This sweep
asks "if we'd applied threshold X, which trades would have been
rejected, and what's the resulting P&L?"

PROVISIONAL labeling discipline:
  Block 1 + 2.1 + 2.2 produced 0/4 within-tolerance replays. Arena
  fidelity is NOT yet earned for full sweeps over arena-replayed P&L.
  This script therefore uses the SHADOW approach: take the recorded
  prod P&L per trade and ask which trades would have been REJECTED
  under each threshold. We are NOT claiming arena's per-trade P&L
  numbers under the sweep — we're claiming "the population of trades
  the gate keeps" and the "summed prod P&L of that population."

  This is a defensible analysis at the policy-comparison level even
  while arena fidelity catches up. The sweep results inform a
  candidate config; they do NOT promote one to prod (halt switch
  stays on regardless).

Output: docs/sweeps/catalyst_gate_pareto.md (committed).

Usage:
    python scripts/sweep_catalyst_gate.py
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("sweep_catalyst_gate")

REPO_ROOT = Path(__file__).resolve().parent.parent
TRADE_RESULTS = REPO_ROOT / "data" / "trade_results.jsonl"
LOGS_DIR = REPO_ROOT / "logs"
OUTPUT_DIR = REPO_ROOT / "docs" / "sweeps"

# Provisional fidelity label — pulled from Block 2.1's reverted state.
ARENA_FIDELITY_LABEL = "0% (Block 2.1 calibration reverted; framework only)"


@dataclass
class TaggedTrade:
    """A deduped prod trade with news_agent classification at entry."""
    ticker: str
    session_date: str
    entry_time: datetime
    pnl: float
    news_signal: str       # STRONG_BULL | BULL | NEUTRAL | NO_SIGNAL | UNKNOWN
    news_conf: float       # 0.0 – 1.0
    is_carry: bool


# ── Trade loading + deduping (mirrors arena_replay_session.py) ──────


def load_trades() -> list[TaggedTrade]:
    """Same dedupe as the replay script + news_agent classification
    enrichment from log scraping."""
    raw = []
    with TRADE_RESULTS.open("r", encoding="utf-8") as f:
        for line in f:
            j = json.loads(line)
            entry = datetime.fromisoformat(j["entry_time"])
            exit_ = datetime.fromisoformat(j["exit_time"])
            raw.append({
                "ticker": j["ticker"],
                "session_date": j.get("session_date", ""),
                "entry_time": entry,
                "exit_time": exit_,
                "pnl": float(j["pnl"]),
                "is_carry": entry.date() < exit_.date(),
            })

    # Dedupe by (ticker, entry_time) — for carries keep earliest exit
    seen: dict[tuple[str, str], dict] = {}
    for t in raw:
        key = (t["ticker"], t["entry_time"].isoformat())
        if t["is_carry"]:
            existing = seen.get(key)
            if existing is None or t["exit_time"] < existing["exit_time"]:
                seen[key] = t
        else:
            seen[key] = t
    deduped = sorted(seen.values(), key=lambda r: r["entry_time"])

    # Pre-market / carry filter: same cutoff as the replay script
    filtered = []
    for t in deduped:
        et_cutoff = datetime.fromisoformat("2000-01-01T13:00:00+00:00").time()
        if t["entry_time"].astimezone(timezone.utc).time() < et_cutoff:
            continue
        if t["is_carry"]:
            continue
        filtered.append(t)

    # Tag with news_agent classification at entry
    out = []
    for t in filtered:
        sig, conf = _scrape_news_signal(t["ticker"], t["session_date"], t["entry_time"])
        out.append(TaggedTrade(
            ticker=t["ticker"], session_date=t["session_date"],
            entry_time=t["entry_time"], pnl=t["pnl"],
            news_signal=sig, news_conf=conf,
            is_carry=t["is_carry"],
        ))
    return out


# ── News-signal scraper from momentum_*.log ─────────────────────────


_RE_ENS = re.compile(
    r'(\d{2}:\d{2}:\d{2}).*?D202 ENSEMBLE news_agent (\w+):.*?(NEUTRAL|STRONG_BULL|STRONG_BEAR|BULL|BEAR).*?conf=([\d\.]+)',
)


def _scrape_news_signal(ticker: str, date: str, entry_time: datetime) -> tuple[str, float]:
    """Return (signal, conf) for the most-recent ensemble call BEFORE
    entry_time on the given session. Falls back to 'NO_SIGNAL', 0.0
    if no signal found.

    Bug-fix detail: log timestamps are LOCAL ET time (e.g. "09:20:55");
    entry_time is UTC. Convert entry_time to ET (UTC-4 during EDT) for
    comparison. Without this conversion, every log timestamp appears
    'before' the entry clock and the scraper returns the LAST log
    signal of the day — wrong (e.g. OGN ended up tagged NEUTRAL/0.00
    instead of STRONG_BULL/0.61)."""
    from zoneinfo import ZoneInfo
    log = LOGS_DIR / f"momentum_{date}.log"
    if not log.exists():
        return ("NO_SIGNAL", 0.0)
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ("NO_SIGNAL", 0.0)
    entry_et = entry_time.astimezone(ZoneInfo("America/New_York"))
    entry_clock = entry_et.strftime("%H:%M:%S")
    sigs: list[tuple[str, str, float]] = []
    for line in text.split("\n"):
        if "news_agent" not in line or ticker not in line:
            continue
        m = _RE_ENS.search(line)
        if m and m.group(2) == ticker:
            sigs.append((m.group(1), m.group(3), float(m.group(4))))
    at_or_before = [s for s in sigs if s[0] <= entry_clock]
    if at_or_before:
        return (at_or_before[-1][1], at_or_before[-1][2])
    if sigs:
        return (sigs[0][1], sigs[0][2])
    return ("NO_SIGNAL", 0.0)


# ── Sweep ───────────────────────────────────────────────────────────


@dataclass
class GateResult:
    threshold: float
    require_non_neutral: bool
    n_total: int
    n_kept: int
    n_rejected: int
    sum_kept_pnl: float
    sum_rejected_pnl: float
    win_rate_kept: float
    rejected_tickers: list[str]


def sweep(trades: list[TaggedTrade]) -> list[GateResult]:
    results: list[GateResult] = []
    bullish = {"BULL", "STRONG_BULL"}
    for threshold in [0.0, 0.2, 0.3, 0.4, 0.5, 0.6]:
        for require_non_neutral in [False, True]:
            kept = []
            rejected = []
            for t in trades:
                if t.news_conf < threshold:
                    rejected.append(t)
                    continue
                if require_non_neutral and t.news_signal not in bullish:
                    rejected.append(t)
                    continue
                kept.append(t)
            wins = sum(1 for t in kept if t.pnl > 0)
            results.append(GateResult(
                threshold=threshold,
                require_non_neutral=require_non_neutral,
                n_total=len(trades),
                n_kept=len(kept), n_rejected=len(rejected),
                sum_kept_pnl=sum(t.pnl for t in kept),
                sum_rejected_pnl=sum(t.pnl for t in rejected),
                win_rate_kept=(wins / len(kept) if kept else 0.0),
                rejected_tickers=[f"{t.ticker} ({t.session_date})" for t in rejected],
            ))
    return results


def render_report(*, trades: list[TaggedTrade], results: list[GateResult]) -> str:
    lines = []
    lines.append("# Catalyst Gate Sweep — Pareto Frontier")
    lines.append("")
    lines.append(f"**Generated:** by `scripts/sweep_catalyst_gate.py` against `data/trade_results.jsonl`.")
    lines.append(f"**Trade window:** 2026-04-22 to 2026-04-28, deduped, intraday only.")
    lines.append(f"**Arena fidelity at this sweep:** {ARENA_FIDELITY_LABEL}")
    lines.append("")
    lines.append("> **PROVISIONAL** — this sweep uses RECORDED prod P&L per trade and asks which trades each candidate gate would have KEPT vs REJECTED. It does NOT use arena-replayed P&L (arena fidelity not yet earned). The result is a population-level policy comparison, not a strategy-level backtest.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## §1 — Trade tagging (news_agent classification at entry)")
    lines.append("")
    lines.append("| Ticker | Date | P&L | news_signal | news_conf |")
    lines.append("|---|---|---:|---|---:|")
    for t in trades:
        lines.append(
            f"| {t.ticker} | {t.session_date} | ${t.pnl:+,.2f} | "
            f"{t.news_signal} | {t.news_conf:.2f} |"
        )
    lines.append("")
    lines.append(f"**Total trades:** {len(trades)} | **Σ P&L:** ${sum(t.pnl for t in trades):+,.2f}")
    lines.append("")

    lines.append("## §2 — Sweep results")
    lines.append("")
    lines.append("Two gate variants per threshold:")
    lines.append("- **conf-only**: keep trade if news_conf ≥ threshold (any signal direction).")
    lines.append("- **conf + non-NEUTRAL**: keep only if conf ≥ threshold AND signal ∈ {BULL, STRONG_BULL}.")
    lines.append("")
    lines.append("| Threshold | Require BULL+ | Kept | Rej | Σ kept P&L | Σ rej P&L | Win-rate kept |")
    lines.append("|---:|:---:|---:|---:|---:|---:|---:|")
    for r in results:
        bull_mark = "✓" if r.require_non_neutral else " "
        lines.append(
            f"| {r.threshold:.2f} | {bull_mark} | {r.n_kept} | {r.n_rejected} | "
            f"${r.sum_kept_pnl:+,.2f} | ${r.sum_rejected_pnl:+,.2f} | "
            f"{r.win_rate_kept:.0%} |"
        )
    lines.append("")

    # Pareto frontier (max kept_pnl for each kept count)
    lines.append("## §3 — Pareto frontier (max Σ kept P&L per trade-count)")
    lines.append("")
    by_count: dict[int, GateResult] = {}
    for r in results:
        existing = by_count.get(r.n_kept)
        if existing is None or r.sum_kept_pnl > existing.sum_kept_pnl:
            by_count[r.n_kept] = r
    pareto = sorted(by_count.values(), key=lambda r: r.n_kept)
    lines.append("| Trades kept | Best gate | Σ kept P&L | Win-rate |")
    lines.append("|---:|---|---:|---:|")
    for r in pareto:
        gate = f"conf≥{r.threshold:.2f}" + (" + non-NEUTRAL" if r.require_non_neutral else "")
        lines.append(
            f"| {r.n_kept} | {gate} | ${r.sum_kept_pnl:+,.2f} | {r.win_rate_kept:.0%} |"
        )
    lines.append("")

    lines.append("## §4 — Honest reading")
    lines.append("")
    lines.append(f"**Sample size:** {len(trades)} trades is well below any threshold for statistical claims. Win-rate columns at small N's are not reliable.")
    lines.append("")
    lines.append("**Pattern observable:**")
    if pareto:
        worst = min(pareto, key=lambda r: r.sum_kept_pnl)
        best = max(pareto, key=lambda r: r.sum_kept_pnl)
        lines.append(f"- Worst-case gate kept Σ P&L: **${worst.sum_kept_pnl:+,.2f}** ({worst.n_kept} trades, conf≥{worst.threshold:.2f}{', non-NEUTRAL' if worst.require_non_neutral else ''}).")
        lines.append(f"- Best-case gate kept Σ P&L: **${best.sum_kept_pnl:+,.2f}** ({best.n_kept} trades, conf≥{best.threshold:.2f}{', non-NEUTRAL' if best.require_non_neutral else ''}).")
    lines.append("")
    lines.append("**Stop conditions checked:**")
    zero_kept = [r for r in results if r.n_kept == 0]
    if zero_kept:
        lines.append(f"- ⚠️ {len(zero_kept)} configs reject ALL trades. This is consistent with small-sample sparsity (e.g., few STRONG_BULL signals exist) but should be treated as 'no edge demonstrable at this threshold' rather than 'best gate.'")
    else:
        lines.append(f"- ✅ No configuration rejects all trades.")
    lines.append("")
    lines.append("**No promotion to prod from this sweep.** Halt switch stays on. Sweep results inform candidate configs only.")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    trades = load_trades()
    logger.info("loaded %d intraday non-carry trades", len(trades))

    results = sweep(trades)
    logger.info("ran %d gate variants", len(results))

    md = render_report(trades=trades, results=results)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    out = args.output_dir / "catalyst_gate_pareto.md"
    out.write_text(md, encoding="utf-8")
    logger.info("wrote %s", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
