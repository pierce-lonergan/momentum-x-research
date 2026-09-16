"""Counterfactual aggregator + report builder.

Tier 4 #15 final layer: takes a corpus of captured decisions + a
proposed rule change → quantifies how many verdicts flip + estimates
the synthetic impact.

Use cases:
  1. Bug AK validation: replay today's session against v1 vs v2 D124
     rules, confirm the +47% pass-through prediction.
  2. Future calibration A/B: "if I lower the MFCS threshold to 0.20,
     how many more BUYs do I get?"
  3. Regression detection: any code change that touches the verdict
     pipeline can be replayed against a captured corpus to verify it
     doesn't silently flip outcomes.
"""
from __future__ import annotations

import collections
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from src.analysis.decision_replay.rules import replay_decision_d124


@dataclass
class CounterfactualReport:
    """Result of replaying a corpus through a proposed rule change."""
    n_decisions_total: int = 0
    n_verdicts_unchanged: int = 0
    n_verdicts_flipped: int = 0
    flip_directions: dict[str, int] = field(default_factory=dict)
    by_ticker: dict[str, dict] = field(default_factory=dict)
    by_tier: dict[str, int] = field(default_factory=dict)  # post-replay rejection tiers

    # Specific to BUY-creating flips (the most operationally interesting)
    new_buys: list[dict] = field(default_factory=list)
    blocked_buys: list[dict] = field(default_factory=list)

    @property
    def flip_rate_pct(self) -> float:
        if self.n_decisions_total == 0:
            return 0.0
        return self.n_verdicts_flipped / self.n_decisions_total * 100.0

    @property
    def n_new_buys(self) -> int:
        return len(self.new_buys)

    @property
    def n_blocked_buys(self) -> int:
        return len(self.blocked_buys)

    def summary_text(self) -> str:
        lines = [
            "=" * 70,
            "DECISION-REPLAY COUNTERFACTUAL REPORT",
            "=" * 70,
            f"  total decisions replayed:  {self.n_decisions_total}",
            f"  verdicts unchanged:        {self.n_verdicts_unchanged}",
            f"  verdicts flipped:          {self.n_verdicts_flipped} "
            f"({self.flip_rate_pct:.1f}%)",
            "",
            "  flip directions:",
        ]
        for direction, count in sorted(self.flip_directions.items(), key=lambda x: -x[1]):
            pct = count / self.n_decisions_total * 100.0 if self.n_decisions_total else 0
            lines.append(f"    {count:4d} ({pct:5.1f}%)  {direction}")
        lines.append("")
        lines.append(f"  NEW BUYs (replay added):     {self.n_new_buys}")
        for nb in self.new_buys[:10]:
            lines.append(
                f"    {nb['ticker']} @ MFCS={nb['mfcs_score']:.3f} "
                f"(was {nb['captured_action']})"
            )
        if self.n_new_buys > 10:
            lines.append(f"    ... and {self.n_new_buys - 10} more")
        lines.append(f"  BLOCKED BUYs (replay removed): {self.n_blocked_buys}")
        for bb in self.blocked_buys[:10]:
            lines.append(
                f"    {bb['ticker']} @ MFCS={bb['mfcs_score']:.3f} "
                f"(was BUY, replayed {bb['replayed_action']})"
            )
        if self.n_blocked_buys > 10:
            lines.append(f"    ... and {self.n_blocked_buys - 10} more")
        lines.append("")
        lines.append("  post-replay rejection tier breakdown:")
        for tier, count in sorted(self.by_tier.items(), key=lambda x: -x[1]):
            lines.append(f"    {count:4d}  tier={tier}")
        lines.append("=" * 70)
        return "\n".join(lines)


def replay_corpus(
    corpus: Iterator[dict],
    *,
    rule_kind: str = "d124",
    rule_version: str = "v2_bug_ak",
) -> CounterfactualReport:
    """Replay a corpus of captured decisions through an alternate rule.

    Args:
        corpus: iterable of DecisionRow-shaped dicts.
        rule_kind: which rule layer to mutate (currently only 'd124').
        rule_version: rule variant — for d124, 'v1_pre_bug_ak' or
                      'v2_bug_ak'.

    Returns:
        CounterfactualReport with aggregate flip stats + per-ticker
        breakdown + the list of NEW BUYs and BLOCKED BUYs.
    """
    if rule_kind != "d124":
        raise NotImplementedError(f"rule_kind {rule_kind!r} not yet supported")

    report = CounterfactualReport()
    flip_counter: collections.Counter = collections.Counter()
    tier_counter: collections.Counter = collections.Counter()
    by_ticker: dict[str, dict] = collections.defaultdict(
        lambda: {"total": 0, "flipped": 0, "new_buys": 0, "blocked_buys": 0}
    )

    for row in corpus:
        report.n_decisions_total += 1
        captured = row.get("verdict_action", "NO_TRADE")
        replayed, meta = replay_decision_d124(row, rule_version=rule_version)
        ticker = row.get("ticker", "?")
        by_ticker[ticker]["total"] += 1

        if meta["verdict_changed"]:
            report.n_verdicts_flipped += 1
            by_ticker[ticker]["flipped"] += 1
            flip_counter[meta["flip_direction"]] += 1
            if captured != "BUY" and replayed == "BUY":
                by_ticker[ticker]["new_buys"] += 1
                report.new_buys.append({
                    "ticker": ticker,
                    "mfcs_score": row.get("mfcs_score", 0.0),
                    "captured_action": captured,
                    "replayed_action": replayed,
                    "decision_id": row.get("decision_id", "?"),
                })
            elif captured == "BUY" and replayed != "BUY":
                by_ticker[ticker]["blocked_buys"] += 1
                report.blocked_buys.append({
                    "ticker": ticker,
                    "mfcs_score": row.get("mfcs_score", 0.0),
                    "captured_action": captured,
                    "replayed_action": replayed,
                    "decision_id": row.get("decision_id", "?"),
                })
        else:
            report.n_verdicts_unchanged += 1

        if meta["d124_rejected_under_replay_rule"]:
            tier_counter[meta["d124_rejection_tier_replay"] or "unknown"] += 1
        else:
            tier_counter["pass-through"] += 1

    report.flip_directions = dict(flip_counter)
    report.by_tier = dict(tier_counter)
    report.by_ticker = dict(by_ticker)
    return report


def replay_jsonl(
    jsonl_path: str | Path,
    *,
    rule_kind: str = "d124",
    rule_version: str = "v2_bug_ak",
) -> CounterfactualReport:
    """Convenience: replay a JSONL corpus file."""
    jsonl_path = Path(jsonl_path)

    def _gen():
        with jsonl_path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                yield json.loads(line)

    return replay_corpus(_gen(), rule_kind=rule_kind, rule_version=rule_version)
