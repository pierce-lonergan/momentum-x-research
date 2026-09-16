"""D200: Decision Quality Arena — measures the full pipeline, not just scanner filters.

### PROBLEM
The existing Selection Arena tests "did we SEE the right stocks?" (scanner filter
optimization). It doesn't test whether the LLM agents, MFCS scoring, faller gate,
and execution layer make correct decisions. This module fills that gap.

### WHAT IT MEASURES
1. Per-agent accuracy: when news_agent says BULL, does the stock actually run?
2. MFCS calibration: when MFCS=0.7, does the stock win ~70% of the time?
3. Agent value-add: does each agent beat random selection?
4. Catalyst quality: win rate broken down by catalyst_type
5. Debate impact: does the debate engine improve or degrade verdicts?

### DATA SOURCES
- data/journals/*.jsonl: 4548 entries with agent signals, MFCS, verdicts
- data/trade_results.jsonl: 14 realized trades with P&L
- data/scenarios/gap_scenarios.json: 196 labeled scenarios with outcomes
"""

from __future__ import annotations

import json
import glob
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Data directory (relative to project root) ─────────────────────────────

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass
class DecisionRecord:
    """A single evaluated candidate with its actual outcome."""

    ticker: str
    date: str
    mfcs: float
    action: str                              # BUY, STRONG_BUY, NO_TRADE, HOLD
    agent_signals: dict[str, dict[str, Any]]  # agent_id → {signal, confidence, catalyst_type, ...}
    catalyst_type: str | None = None
    gap_pct: float = 0.0
    rvol: float = 0.0
    # Outcome — from trade results or scenario data
    actual_win: bool | None = None           # True/False/None if unknown
    actual_pnl: float | None = None          # Dollar P&L if available
    actual_return_pct: float | None = None   # Percentage return
    qualifies_for_debate: bool = False
    faller_score: float | None = None
    direction: str = "long"


@dataclass
class AgentAccuracy:
    """Accuracy metrics for a single agent."""

    agent_id: str
    total_signals: int = 0
    bullish_signals: int = 0
    bearish_signals: int = 0
    neutral_signals: int = 0
    # When agent said BULL/STRONG_BULL, how often did stock actually win?
    bullish_correct: int = 0
    bullish_total_with_outcome: int = 0
    # When agent said BEAR/STRONG_BEAR, how often did stock actually lose?
    bearish_correct: int = 0
    bearish_total_with_outcome: int = 0

    @property
    def bullish_accuracy(self) -> float:
        if self.bullish_total_with_outcome == 0:
            return 0.0
        return self.bullish_correct / self.bullish_total_with_outcome

    @property
    def bearish_accuracy(self) -> float:
        if self.bearish_total_with_outcome == 0:
            return 0.0
        return self.bearish_correct / self.bearish_total_with_outcome

    @property
    def overall_accuracy(self) -> float:
        total = self.bullish_total_with_outcome + self.bearish_total_with_outcome
        if total == 0:
            return 0.0
        correct = self.bullish_correct + self.bearish_correct
        return correct / total


@dataclass
class MFCSBucket:
    """Win rate for a specific MFCS range."""

    mfcs_min: float
    mfcs_max: float
    total: int = 0
    wins: int = 0
    total_pnl: float = 0.0

    @property
    def win_rate(self) -> float:
        return self.wins / self.total if self.total > 0 else 0.0

    @property
    def avg_pnl(self) -> float:
        return self.total_pnl / self.total if self.total > 0 else 0.0


@dataclass
class CatalystBreakdown:
    """Win rate breakdown by catalyst type."""

    catalyst_type: str
    total: int = 0
    wins: int = 0
    total_pnl: float = 0.0
    buy_count: int = 0  # How many BUY verdicts for this catalyst

    @property
    def win_rate(self) -> float:
        return self.wins / self.total if self.total > 0 else 0.0


@dataclass
class AgentMarginalValue:
    """D201: Marginal contribution of each agent — does adding it improve decisions?"""

    agent_id: str
    # Win rate when this agent is BULLISH vs when it's not
    bullish_win_rate: float = 0.0
    non_bullish_win_rate: float = 0.0
    marginal_lift: float = 0.0  # bullish_wr - non_bullish_wr (positive = valuable)
    n_bullish: int = 0
    n_non_bullish: int = 0


@dataclass
class WalkForwardSplit:
    """D201: Walk-forward validation on decision records."""

    train_size: int = 0
    test_size: int = 0
    train_win_rate: float = 0.0
    test_win_rate: float = 0.0
    overfit_ratio: float = 0.0  # train_wr / test_wr (>1.5 = overfitting)
    train_avg_mfcs: float = 0.0
    test_avg_mfcs: float = 0.0


@dataclass
class DecisionQualityReport:
    """Complete arena output."""

    total_decisions: int = 0
    decisions_with_outcomes: int = 0
    agent_accuracy: dict[str, AgentAccuracy] = field(default_factory=dict)
    mfcs_calibration: list[MFCSBucket] = field(default_factory=list)
    catalyst_breakdown: dict[str, CatalystBreakdown] = field(default_factory=dict)
    # D201: New 9+ elevation metrics
    agent_marginal_value: dict[str, AgentMarginalValue] = field(default_factory=dict)
    walk_forward: WalkForwardSplit | None = None
    # Debate impact
    debate_trades_win_rate: float | None = None
    no_debate_trades_win_rate: float | None = None
    debate_trades_count: int = 0
    no_debate_trades_count: int = 0
    # Overall
    buy_decisions: int = 0
    buy_win_rate: float = 0.0
    avg_mfcs_winners: float = 0.0
    avg_mfcs_losers: float = 0.0


class DecisionQualityArena:
    """
    Loads journal data, joins with outcomes, and computes decision quality metrics.

    Usage:
        arena = DecisionQualityArena()
        arena.load_journals()
        arena.load_outcomes()
        report = arena.analyze()
    """

    def __init__(self, data_dir: Path | None = None):
        self._data_dir = data_dir or (_PROJECT_ROOT / "data")
        self._records: list[DecisionRecord] = []
        self._outcomes: dict[tuple[str, str], dict] = {}  # (ticker, date) → outcome

    def load_journals(self, journals_dir: Path | None = None) -> int:
        """Load journal entries and extract decision records."""
        jdir = journals_dir or (self._data_dir / "journals")
        loaded = 0
        for jf in sorted(glob.glob(str(jdir / "journal_*.jsonl"))):
            with open(jf, encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        record = self._parse_journal_entry(entry)
                        if record is not None:
                            self._records.append(record)
                            loaded += 1
                    except (json.JSONDecodeError, KeyError):
                        continue
        logger.info("D200: Loaded %d decision records from journals", loaded)
        return loaded

    def load_outcomes(self) -> int:
        """Load outcome data from trade results and scenario database."""
        joined = 0

        # Source 1: Realized trade results
        tr_path = self._data_dir / "trade_results.jsonl"
        if tr_path.exists():
            with open(tr_path, encoding="utf-8") as f:
                for line in f:
                    try:
                        tr = json.loads(line)
                        key = (tr["ticker"], tr["session_date"])
                        self._outcomes[key] = {
                            "win": tr["is_win"],
                            "pnl": tr["pnl"],
                            "source": "live_trade",
                        }
                    except (json.JSONDecodeError, KeyError):
                        continue

        # Source 2: Historical scenario database (196 labeled scenarios)
        sc_path = self._data_dir / "scenarios" / "gap_scenarios.json"
        if sc_path.exists():
            with open(sc_path, encoding="utf-8") as f:
                data = json.load(f)
                scenarios = data if isinstance(data, list) else data.get("scenarios", [])
                for s in scenarios:
                    key = (s["ticker"], s["date"])
                    self._outcomes[key] = {
                        "win": s["outcome"] == "WIN",
                        "pnl": None,
                        "return_pct": s.get("intraday_return", 0.0),
                        "high_from_open": s.get("high_from_open_pct", 0.0),
                        "source": "scenario",
                    }

        # Join outcomes to records
        for rec in self._records:
            outcome = self._outcomes.get((rec.ticker, rec.date))
            if outcome is not None:
                rec.actual_win = outcome["win"]
                rec.actual_pnl = outcome.get("pnl")
                rec.actual_return_pct = outcome.get("return_pct")
                joined += 1

        logger.info("D200: Joined %d/%d records with outcomes", joined, len(self._records))
        return joined

    def analyze(self) -> DecisionQualityReport:
        """Run full decision quality analysis."""
        report = DecisionQualityReport()
        report.total_decisions = len(self._records)

        records_with_outcome = [r for r in self._records if r.actual_win is not None]
        report.decisions_with_outcomes = len(records_with_outcome)

        # ── Per-agent accuracy ──────────────────────────────────────
        agent_stats: dict[str, AgentAccuracy] = {}
        for rec in self._records:
            for agent_id, sig_data in rec.agent_signals.items():
                if agent_id not in agent_stats:
                    agent_stats[agent_id] = AgentAccuracy(agent_id=agent_id)
                aa = agent_stats[agent_id]
                signal = sig_data.get("signal", "NEUTRAL")
                aa.total_signals += 1
                if signal in ("BULL", "STRONG_BULL"):
                    aa.bullish_signals += 1
                    if rec.actual_win is not None:
                        aa.bullish_total_with_outcome += 1
                        if rec.actual_win:
                            aa.bullish_correct += 1
                elif signal in ("BEAR", "STRONG_BEAR"):
                    aa.bearish_signals += 1
                    if rec.actual_win is not None:
                        aa.bearish_total_with_outcome += 1
                        if not rec.actual_win:
                            aa.bearish_correct += 1
                else:
                    aa.neutral_signals += 1
        report.agent_accuracy = agent_stats

        # ── MFCS calibration ────────────────────────────────────────
        buckets = [
            MFCSBucket(-1.0, 0.0),
            MFCSBucket(0.0, 0.2),
            MFCSBucket(0.2, 0.4),
            MFCSBucket(0.4, 0.6),
            MFCSBucket(0.6, 0.8),
            MFCSBucket(0.8, 1.0),
        ]
        for rec in records_with_outcome:
            for bucket in buckets:
                if bucket.mfcs_min <= rec.mfcs < bucket.mfcs_max or (
                    bucket.mfcs_max == 1.0 and rec.mfcs == 1.0
                ):
                    bucket.total += 1
                    if rec.actual_win:
                        bucket.wins += 1
                    if rec.actual_pnl is not None:
                        bucket.total_pnl += rec.actual_pnl
                    break
        report.mfcs_calibration = buckets

        # ── Catalyst breakdown ──────────────────────────────────────
        catalyst_stats: dict[str, CatalystBreakdown] = {}
        for rec in self._records:
            ct = rec.catalyst_type or "unknown"
            if ct not in catalyst_stats:
                catalyst_stats[ct] = CatalystBreakdown(catalyst_type=ct)
            cb = catalyst_stats[ct]
            if rec.action in ("BUY", "STRONG_BUY"):
                cb.buy_count += 1
            if rec.actual_win is not None:
                cb.total += 1
                if rec.actual_win:
                    cb.wins += 1
                if rec.actual_pnl is not None:
                    cb.total_pnl += rec.actual_pnl
        report.catalyst_breakdown = catalyst_stats

        # ── Debate impact ───────────────────────────────────────────
        debate_wins, debate_total = 0, 0
        no_debate_wins, no_debate_total = 0, 0
        for rec in records_with_outcome:
            if rec.action not in ("BUY", "STRONG_BUY"):
                continue
            if rec.qualifies_for_debate:
                debate_total += 1
                if rec.actual_win:
                    debate_wins += 1
            else:
                no_debate_total += 1
                if rec.actual_win:
                    no_debate_wins += 1
        report.debate_trades_count = debate_total
        report.no_debate_trades_count = no_debate_total
        report.debate_trades_win_rate = (
            debate_wins / debate_total if debate_total > 0 else None
        )
        report.no_debate_trades_win_rate = (
            no_debate_wins / no_debate_total if no_debate_total > 0 else None
        )

        # ── BUY decisions summary ───────────────────────────────────
        buy_records = [
            r for r in records_with_outcome
            if r.action in ("BUY", "STRONG_BUY")
        ]
        report.buy_decisions = len(buy_records)
        buy_wins = sum(1 for r in buy_records if r.actual_win)
        report.buy_win_rate = buy_wins / len(buy_records) if buy_records else 0.0

        winners = [r for r in buy_records if r.actual_win]
        losers = [r for r in buy_records if not r.actual_win]
        report.avg_mfcs_winners = (
            sum(r.mfcs for r in winners) / len(winners) if winners else 0.0
        )
        report.avg_mfcs_losers = (
            sum(r.mfcs for r in losers) / len(losers) if losers else 0.0
        )

        # ── D201: Per-agent marginal value ──────────────────────────
        # For each agent: measure win rate when agent is BULL vs not-BULL.
        # Positive marginal lift = agent adds real signal.
        for agent_id in agent_stats:
            bull_wins, bull_total = 0, 0
            non_bull_wins, non_bull_total = 0, 0
            for rec in records_with_outcome:
                sig_data = rec.agent_signals.get(agent_id, {})
                sig = sig_data.get("signal", "NEUTRAL")
                if sig in ("BULL", "STRONG_BULL"):
                    bull_total += 1
                    if rec.actual_win:
                        bull_wins += 1
                else:
                    non_bull_total += 1
                    if rec.actual_win:
                        non_bull_wins += 1
            bull_wr = bull_wins / bull_total if bull_total > 0 else 0.0
            non_bull_wr = non_bull_wins / non_bull_total if non_bull_total > 0 else 0.0
            report.agent_marginal_value[agent_id] = AgentMarginalValue(
                agent_id=agent_id,
                bullish_win_rate=bull_wr,
                non_bullish_win_rate=non_bull_wr,
                marginal_lift=bull_wr - non_bull_wr,
                n_bullish=bull_total,
                n_non_bullish=non_bull_total,
            )

        # ── D201: Walk-forward validation ───────────────────────────
        # Sort by date, train on first 75%, test on last 25%.
        dated_records = sorted(records_with_outcome, key=lambda r: r.date)
        if len(dated_records) >= 20:
            split_idx = int(len(dated_records) * 0.75)
            train = dated_records[:split_idx]
            test = dated_records[split_idx:]
            train_buy = [r for r in train if r.action in ("BUY", "STRONG_BUY")]
            test_buy = [r for r in test if r.action in ("BUY", "STRONG_BUY")]
            train_wr = (
                sum(1 for r in train_buy if r.actual_win) / len(train_buy)
                if train_buy else 0.0
            )
            test_wr = (
                sum(1 for r in test_buy if r.actual_win) / len(test_buy)
                if test_buy else 0.0
            )
            report.walk_forward = WalkForwardSplit(
                train_size=len(train_buy),
                test_size=len(test_buy),
                train_win_rate=train_wr,
                test_win_rate=test_wr,
                overfit_ratio=train_wr / test_wr if test_wr > 0 else float("inf"),
                train_avg_mfcs=sum(r.mfcs for r in train_buy) / len(train_buy) if train_buy else 0.0,
                test_avg_mfcs=sum(r.mfcs for r in test_buy) / len(test_buy) if test_buy else 0.0,
            )

        return report

    def format_report(self, report: DecisionQualityReport) -> str:
        """Format report as human-readable text."""
        lines = [
            "=" * 70,
            "D200 DECISION QUALITY ARENA REPORT",
            "=" * 70,
            f"Total decisions analyzed: {report.total_decisions}",
            f"Decisions with outcomes:  {report.decisions_with_outcomes}",
            f"BUY decisions with outcomes: {report.buy_decisions}",
            f"BUY win rate: {report.buy_win_rate:.1%}",
            "",
            f"Avg MFCS (winners): {report.avg_mfcs_winners:.3f}",
            f"Avg MFCS (losers):  {report.avg_mfcs_losers:.3f}",
            "",
            "--- PER-AGENT ACCURACY ---",
        ]

        for agent_id in sorted(report.agent_accuracy.keys()):
            aa = report.agent_accuracy[agent_id]
            lines.append(
                f"  {agent_id:25s} | "
                f"bull_acc={aa.bullish_accuracy:.1%} ({aa.bullish_correct}/{aa.bullish_total_with_outcome}) | "
                f"bear_acc={aa.bearish_accuracy:.1%} ({aa.bearish_correct}/{aa.bearish_total_with_outcome}) | "
                f"overall={aa.overall_accuracy:.1%} | "
                f"signals: {aa.bullish_signals}B/{aa.bearish_signals}b/{aa.neutral_signals}N"
            )

        lines.append("")
        lines.append("--- MFCS CALIBRATION ---")
        for b in report.mfcs_calibration:
            bar = "#" * int(b.win_rate * 20) if b.total > 0 else ""
            lines.append(
                f"  MFCS [{b.mfcs_min:+.1f}, {b.mfcs_max:+.1f}): "
                f"n={b.total:4d}  win_rate={b.win_rate:.1%}  "
                f"avg_pnl=${b.avg_pnl:+.0f}  {bar}"
            )

        lines.append("")
        lines.append("--- CATALYST BREAKDOWN ---")
        for ct in sorted(report.catalyst_breakdown.keys()):
            cb = report.catalyst_breakdown[ct]
            if cb.total > 0:
                lines.append(
                    f"  {ct:25s} | "
                    f"win_rate={cb.win_rate:.1%} ({cb.wins}/{cb.total}) | "
                    f"BUY verdicts={cb.buy_count} | "
                    f"total_pnl=${cb.total_pnl:+,.0f}"
                )

        lines.append("")
        lines.append("--- DEBATE IMPACT ---")
        if report.debate_trades_count > 0:
            lines.append(
                f"  With debate:    win_rate={report.debate_trades_win_rate:.1%} "
                f"(n={report.debate_trades_count})"
            )
        if report.no_debate_trades_count > 0:
            lines.append(
                f"  Without debate: win_rate={report.no_debate_trades_win_rate:.1%} "
                f"(n={report.no_debate_trades_count})"
            )

        # D201: Arena 9+ metrics
        if report.agent_marginal_value:
            lines.append("")
            lines.append("--- AGENT MARGINAL VALUE (D201) ---")
            for agent_id in sorted(report.agent_marginal_value.keys()):
                mv = report.agent_marginal_value[agent_id]
                tag = "+" if mv.marginal_lift > 0 else "-" if mv.marginal_lift < 0 else "="
                lines.append(
                    f"  {agent_id:25s} | "
                    f"BULL_wr={mv.bullish_win_rate:.1%} (n={mv.n_bullish}) | "
                    f"other_wr={mv.non_bullish_win_rate:.1%} (n={mv.n_non_bullish}) | "
                    f"lift={mv.marginal_lift:+.1%} [{tag}]"
                )

        if report.walk_forward is not None:
            wf = report.walk_forward
            lines.append("")
            lines.append("--- WALK-FORWARD VALIDATION (D201) ---")
            lines.append(f"  Train: n={wf.train_size}  win_rate={wf.train_win_rate:.1%}  avg_mfcs={wf.train_avg_mfcs:.3f}")
            lines.append(f"  Test:  n={wf.test_size}  win_rate={wf.test_win_rate:.1%}  avg_mfcs={wf.test_avg_mfcs:.3f}")
            overfit_tag = "OK" if wf.overfit_ratio < 1.5 else "OVERFITTING"
            lines.append(f"  Overfit ratio: {wf.overfit_ratio:.2f} ({overfit_tag})")

        lines.append("=" * 70)
        return "\n".join(lines)

    # ── Internal helpers ────────────────────────────────────────────────

    def _parse_journal_entry(self, entry: dict) -> DecisionRecord | None:
        """Parse a journal JSONL entry into a DecisionRecord."""
        ticker = entry.get("ticker")
        if not ticker:
            return None

        # Extract date from session_date or timestamp
        date = entry.get("session_date", "")
        if not date and entry.get("timestamp"):
            date = entry["timestamp"][:10]
        if not date:
            return None

        # Parse agent signals into dict
        agent_signals = {}
        catalyst_type = None
        for sig in (entry.get("agent_signals") or []):
            # BUG-FIX: Guard against None or non-dict entries in agent_signals
            if not isinstance(sig, dict):
                continue
            agent_id = sig.get("agent_id", "unknown")
            agent_signals[agent_id] = {
                "signal": sig.get("signal", "NEUTRAL"),
                "confidence": sig.get("confidence", 0.0),
                "catalyst_type": sig.get("catalyst_type"),
                "catalyst_specificity": sig.get("catalyst_specificity"),
                "risk_verdict": sig.get("risk_verdict"),
                "risk_score": sig.get("risk_score"),
            }
            # Extract catalyst from news_agent
            if agent_id == "news_agent":
                ct = sig.get("catalyst_type")
                if ct and ct not in ("NONE", "None", "null"):
                    catalyst_type = ct
                else:
                    # Also check key_data
                    kd = sig.get("key_data", {})
                    ct2 = kd.get("catalyst_type") if isinstance(kd, dict) else None
                    if ct2 and ct2 not in ("NONE", "None", "null"):
                        catalyst_type = ct2

        return DecisionRecord(
            ticker=ticker,
            date=date,
            mfcs=entry.get("mfcs", 0.0),
            action=entry.get("action", "UNKNOWN"),
            agent_signals=agent_signals,
            catalyst_type=catalyst_type,
            gap_pct=entry.get("gap_pct", 0.0),
            rvol=entry.get("rvol", 0.0),
            qualifies_for_debate=entry.get("qualifies_for_debate", False),
            direction=entry.get("direction", "long"),
        )

    @property
    def records(self) -> list[DecisionRecord]:
        return self._records

    @property
    def outcomes(self) -> dict:
        return self._outcomes
