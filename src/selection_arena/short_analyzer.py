"""
D161: Short Selling Backtester for the Selection Arena

Answers the question: "What would have happened if we shorted the stocks
that the faller gate rejected (score > 0.65)?"

For each high-faller-score stock in the historical universe:
1. Check D161 qualification filters (RVOL >= 3x, dolvol >= $500K, gap >= 20%)
2. Simulate entry at open_price with OTO buy-stop above and targets below
3. Compute outcome: stop hit, target hit(s), or EOD close
4. Aggregate metrics: win rate, avg P&L, MAE, MFE

Key test cases (from session notes):
  - ARTL: dropped 55% from entry → should be a big short winner
  - EEIQ: generated ~$19K as accidental short → should be profitable
  - SST: ~$200K dolvol → correctly filtered out by $500K dolvol minimum

Usage:
    from src.selection_arena.short_analyzer import ShortAnalyzer, ShortAnalyzerConfig
    from src.selection_arena.market_movers import MarketMoversDB

    db = MarketMoversDB()
    analyzer = ShortAnalyzer()
    stats = analyzer.run(dates=["2026-03-30", "2026-03-31"], db=db)
    print(analyzer.format_report(stats))
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field

from src.selection_arena.models import MoverRecord, ShortBacktestStats, ShortSimResult

logger = logging.getLogger(__name__)


@dataclass
class ShortAnalyzerConfig:
    """Configuration parameters for the short simulation.

    Mirrors the live ShortSellingConfig values from config/settings.py.
    Kept separate so the arena can backtest different parameter sets.
    """
    # Faller score threshold — stocks above this are short candidates
    min_faller_score: float = 0.65
    full_size_faller_score: float = 0.80

    # D161 qualification gates
    rvol_min: float = 3.0           # Minimum RVOL to short
    dollar_volume_min: float = 500_000   # Minimum dollar volume to short
    gap_min_pct: float = 0.20       # Minimum gap % to short (20%)

    # Stop / target structure
    stop_pct_above_entry: float = 0.35   # Buy-stop at entry × 1.35
    target_pcts: list[float] = field(
        default_factory=lambda: [-0.03, -0.06, -0.10]
    )  # T1/T2/T3 targets below entry

    # Faller score estimation: when real faller scores aren't available,
    # estimate based on observable signals.
    # A stock is treated as a high-faller candidate if:
    #   - gap >= gap_min_pct (overextended)
    #   - rvol >= rvol_min (liquid enough to cover)
    #   - dollar_volume >= dollar_volume_min (enough to exit)
    # plus we look at how the day played out (close vs open) to classify
    # stocks that "actually faded" as having high implied faller scores.
    #
    # implied_fader_threshold: stocks that close this far below open are
    # considered to have had an implied high faller score (they actually faded).
    implied_fader_close_pct: float = -0.10   # -10% from open = probable fader

    # For arena replay: assume all gap-up stocks with no news are high-faller
    # candidates (conservative — overshoots slightly vs live system).
    assume_no_news_is_fader: bool = False


class ShortAnalyzer:
    """
    Simulate the D161 short selling path against historical MoverRecords.

    The analyzer treats each MoverRecord as a potential short candidate if
    it would have been flagged as a high-faller by the faller gate. Since
    we don't have stored faller scores for all historical stocks, we use a
    heuristic: stocks that actually faded (closed well below open) with
    high RVOL and overextended gaps are the prime short candidates.

    For a cleaner signal, callers can pre-filter the universe to only stocks
    they know were faller-rejected by the live system (e.g. from journal logs).
    """

    def __init__(self, config: ShortAnalyzerConfig | None = None) -> None:
        self.config = config or ShortAnalyzerConfig()

    # ── Public API ──────────────────────────────────────────────────

    def simulate_one(self, record: MoverRecord) -> ShortSimResult | None:
        """
        Simulate a short trade on one MoverRecord.

        Returns None if outcome data (high, low, close) is missing.
        """
        cfg = self.config

        # Require price and outcome data
        entry = record.open_price
        if not entry or entry <= 0:
            return None
        high = record.high
        low = record.low
        close = record.close
        if high is None or low is None or close is None:
            return None

        gap = record.gap_pct or 0.0
        rvol = record.rvol_at_open or 0.0
        dolvol = record.dollar_volume or 0.0

        # D161 qualification filters
        passed_dolvol = dolvol >= cfg.dollar_volume_min
        passed_rvol = rvol >= cfg.rvol_min
        passed_gap = gap >= cfg.gap_min_pct
        would_short = passed_dolvol and passed_rvol and passed_gap

        # Stop and target prices
        stop_price = round(entry * (1 + cfg.stop_pct_above_entry), 4)
        target_prices = [round(entry * (1 + t), 4) for t in cfg.target_pcts]

        # Simulate outcome
        hit_stop = high >= stop_price
        hits = [low <= t for t in target_prices]
        hit_t1 = hits[0] if len(hits) > 0 else False
        hit_t2 = hits[1] if len(hits) > 1 else False
        hit_t3 = hits[2] if len(hits) > 2 else False

        # Determine exit price
        # Priority: stop > T3 > T2 > T1 > EOD close
        # (In a real D161 short, the OTO buy-stop fires first if price spikes;
        # targets are limit buys that fill as price falls.)
        # For simulation purposes, if stop is hit assume that's the exit.
        # Otherwise use the deepest target hit, else EOD close.
        if hit_stop:
            exit_price = stop_price
        elif hit_t3:
            exit_price = target_prices[2]
        elif hit_t2:
            exit_price = target_prices[1]
        elif hit_t1:
            exit_price = target_prices[0]
        else:
            exit_price = close

        # P&L per share (short: entry - exit)
        pnl_pct = (entry - exit_price) / entry if entry > 0 else 0.0

        # Adverse / favorable excursion
        max_adverse = (high - entry) / entry if entry > 0 else 0.0   # Up move against short
        max_favorable = (entry - low) / entry if entry > 0 else 0.0  # Down move for short

        # Notes
        notes_parts = []
        if hit_stop:
            notes_parts.append(f"STOPPED OUT @ ${stop_price:.2f} (+{cfg.stop_pct_above_entry:.0%})")
        if hit_t3:
            notes_parts.append("hit T3 (-10%)")
        elif hit_t2:
            notes_parts.append("hit T2 (-6%)")
        elif hit_t1:
            notes_parts.append("hit T1 (-3%)")
        if not would_short:
            reasons = []
            if not passed_dolvol:
                reasons.append(f"dolvol ${dolvol/1000:.0f}K < ${cfg.dollar_volume_min/1000:.0f}K")
            if not passed_rvol:
                reasons.append(f"rvol {rvol:.1f}x < {cfg.rvol_min:.1f}x")
            if not passed_gap:
                reasons.append(f"gap {gap:.0%} < {cfg.gap_min_pct:.0%}")
            notes_parts.append("FILTERED: " + ", ".join(reasons))

        return ShortSimResult(
            ticker=record.ticker,
            date=record.date,
            entry_price=round(entry, 4),
            stop_price=stop_price,
            target_prices=target_prices,
            close_price=round(close, 4),
            day_high=round(high, 4),
            day_low=round(low, 4),
            gap_pct=round(gap, 4),
            rvol=round(rvol, 2),
            dollar_volume=round(dolvol),
            passed_dolvol_filter=passed_dolvol,
            passed_rvol_filter=passed_rvol,
            passed_gap_filter=passed_gap,
            would_have_shorted=would_short,
            hit_stop=hit_stop,
            hit_t1=hit_t1,
            hit_t2=hit_t2,
            hit_t3=hit_t3,
            exit_price=round(exit_price, 4),
            short_pnl_pct=round(pnl_pct, 4),
            max_adverse_excursion_pct=round(max_adverse, 4),
            max_favorable_excursion_pct=round(max_favorable, 4),
            notes=" | ".join(notes_parts),
        )

    def run(
        self,
        universe: list[MoverRecord],
        faller_candidates: list[str] | None = None,
    ) -> ShortBacktestStats:
        """
        Run short simulation across a universe of MoverRecords.

        Args:
            universe: All stocks for the dates of interest.
            faller_candidates: Optional list of tickers known to have been
                faller-rejected by the live system. If None, all stocks that
                pass the overextension heuristic are included as candidates.

        Returns:
            ShortBacktestStats with full breakdown.
        """
        cfg = self.config
        results: list[ShortSimResult] = []
        n_candidates = 0

        for record in universe:
            # Filter to faller candidates
            if faller_candidates is not None:
                if record.ticker not in faller_candidates:
                    continue
            else:
                # Heuristic: treat overextended gap-ups as faller candidates
                gap = record.gap_pct or 0.0
                rvol = record.rvol_at_open or 0.0
                close = record.close
                open_p = record.open_price
                if gap < cfg.gap_min_pct or rvol < 2.0:
                    continue  # Not a gap-up or insufficient RVOL — skip
                # Require the stock to have actually faded or be a known fader
                if close and open_p and close > 0 and open_p > 0:
                    close_pct_from_open = (close - open_p) / open_p
                    if close_pct_from_open > 0:
                        # Stock went UP on the day — not a fader, skip
                        # unless assume_no_news_is_fader and no confirmed news
                        if cfg.assume_no_news_is_fader and not record.has_news:
                            pass  # Include anyway
                        else:
                            continue

            n_candidates += 1
            result = self.simulate_one(record)
            if result is not None:
                results.append(result)

        return self._aggregate(results, n_candidates)

    def run_on_tickers(
        self,
        tickers: list[str],
        universe: list[MoverRecord],
    ) -> ShortBacktestStats:
        """
        Run short simulation on a specific set of tickers (e.g. from live session logs).

        This is the most accurate mode — use when you have the actual list of
        faller-rejected tickers from journal/log files.
        """
        ticker_set = set(tickers)
        filtered = [r for r in universe if r.ticker in ticker_set]
        return self.run(filtered, faller_candidates=tickers)

    # ── Formatting ──────────────────────────────────────────────────

    def format_report(self, stats: ShortBacktestStats) -> str:
        """Generate a human-readable report of short simulation results."""
        lines = [
            "═══ D161 SHORT SIMULATION REPORT ═══",
            f"Candidates (faller-rejected):  {stats.n_candidates}",
            f"Qualified (pass RVOL/dolvol/gap): {stats.n_qualified}",
            f"Would have shorted:            {stats.n_would_short}",
            "",
            "── Performance ──────────────────────",
            f"Win rate:           {stats.win_rate:.1%}  ({stats.n_wins}W / {stats.n_losses}L)",
            f"Stop-out rate:      {stats.stop_rate:.1%}  ({stats.n_stops} stops)",
            f"T1 hits (-3%):      {stats.n_t1_hits}",
            f"T2 hits (-6%):      {stats.n_t2_hits}",
            f"T3 hits (-10%):     {stats.n_t3_hits}",
            "",
            "── P&L Summary ──────────────────────",
            f"Avg P&L per short:  {stats.avg_pnl_pct:+.2%}",
            f"Median P&L:         {stats.median_pnl_pct:+.2%}",
            f"Total P&L (equal $): {stats.total_pnl_pct:+.2%}",
            f"Avg EOD close P&L:  {stats.avg_pnl_at_close_pct:+.2%}",
            "",
            "── Risk ─────────────────────────────",
            f"Avg adverse excursion (MAE): {stats.avg_max_adverse_excursion_pct:+.2%}",
            f"Avg favorable excursion (MFE): {stats.avg_max_favorable_excursion_pct:+.2%}",
        ]

        if stats.results:
            lines.append("")
            lines.append("── Individual Trades ────────────────")
            header = f"{'Ticker':<8} {'Date':<12} {'Entry':>8} {'Exit':>8} {'P&L%':>7} {'Stop':>5} {'T1':>3} {'T2':>3} {'T3':>3}  Notes"
            lines.append(header)
            lines.append("-" * 90)
            for r in sorted(stats.results, key=lambda x: x.short_pnl_pct, reverse=True):
                if not r.would_have_shorted:
                    continue
                flag = "W" if r.is_win else "L"
                row = (
                    f"{r.ticker:<8} {r.date:<12} ${r.entry_price:>6.2f} ${r.exit_price:>6.2f} "
                    f"{r.short_pnl_pct:>+7.2%} {'Y' if r.hit_stop else 'N':>5} "
                    f"{'Y' if r.hit_t1 else 'N':>3} {'Y' if r.hit_t2 else 'N':>3} "
                    f"{'Y' if r.hit_t3 else 'N':>3} [{flag}] {r.notes[:40]}"
                )
                lines.append(row)

        # Filtered stocks (would not have shorted)
        filtered = [r for r in stats.results if not r.would_have_shorted]
        if filtered:
            lines.append("")
            lines.append("── Filtered Out (failed D161 gates) ──")
            for r in filtered:
                lines.append(f"  {r.ticker} ({r.date}): {r.notes}")

        if stats.biggest_winner:
            bw = stats.biggest_winner
            lines.append("")
            lines.append(f"Best short:  {bw.ticker} {bw.date} | entry=${bw.entry_price:.2f} close=${bw.close_price:.2f} | P&L={bw.pnl_at_close_pct:+.1%}")
        if stats.biggest_loser:
            bl = stats.biggest_loser
            lines.append(f"Worst short: {bl.ticker} {bl.date} | entry=${bl.entry_price:.2f} exit=${bl.exit_price:.2f} | P&L={bl.short_pnl_pct:+.1%}")
        if stats.biggest_stop_out:
            bs = stats.biggest_stop_out
            lines.append(f"Worst MAE:   {bs.ticker} {bs.date} | adverse={bs.max_adverse_excursion_pct:+.1%} (stop {'hit' if bs.hit_stop else 'NOT hit'})")

        return "\n".join(lines)

    # ── Internals ───────────────────────────────────────────────────

    def _aggregate(
        self,
        results: list[ShortSimResult],
        n_candidates: int,
    ) -> ShortBacktestStats:
        """Build ShortBacktestStats from a list of ShortSimResults."""
        n_qualified = sum(1 for r in results if r.would_have_shorted)
        n_would_short = n_qualified
        shorted = [r for r in results if r.would_have_shorted]

        n_wins = sum(1 for r in shorted if r.is_win)
        n_losses = len(shorted) - n_wins
        n_stops = sum(1 for r in shorted if r.hit_stop)
        n_t1 = sum(1 for r in shorted if r.hit_t1)
        n_t2 = sum(1 for r in shorted if r.hit_t2)
        n_t3 = sum(1 for r in shorted if r.hit_t3)

        pnl_values = [r.short_pnl_pct for r in shorted]
        close_pnl_values = [r.pnl_at_close_pct for r in shorted]
        mae_values = [r.max_adverse_excursion_pct for r in shorted]
        mfe_values = [r.max_favorable_excursion_pct for r in shorted]

        avg_pnl = statistics.mean(pnl_values) if pnl_values else 0.0
        avg_close_pnl = statistics.mean(close_pnl_values) if close_pnl_values else 0.0
        median_pnl = statistics.median(pnl_values) if pnl_values else 0.0
        total_pnl = sum(pnl_values)
        avg_mae = statistics.mean(mae_values) if mae_values else 0.0
        avg_mfe = statistics.mean(mfe_values) if mfe_values else 0.0

        biggest_winner = max(shorted, key=lambda r: r.short_pnl_pct, default=None)
        biggest_loser = min(shorted, key=lambda r: r.short_pnl_pct, default=None)
        biggest_stop_out = max(shorted, key=lambda r: r.max_adverse_excursion_pct, default=None)

        return ShortBacktestStats(
            n_candidates=n_candidates,
            n_qualified=n_qualified,
            n_would_short=n_would_short,
            n_wins=n_wins,
            n_losses=n_losses,
            n_stops=n_stops,
            n_t1_hits=n_t1,
            n_t2_hits=n_t2,
            n_t3_hits=n_t3,
            avg_pnl_pct=avg_pnl,
            avg_pnl_at_close_pct=avg_close_pnl,
            median_pnl_pct=median_pnl,
            total_pnl_pct=total_pnl,
            avg_max_adverse_excursion_pct=avg_mae,
            avg_max_favorable_excursion_pct=avg_mfe,
            results=results,
            biggest_winner=biggest_winner if biggest_winner and biggest_winner.is_win else None,
            biggest_loser=biggest_loser if biggest_loser and not biggest_loser.is_win else None,
            biggest_stop_out=biggest_stop_out,
        )
