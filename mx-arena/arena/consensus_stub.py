"""Consensus stub — minimal-viable mock of prod's MFCS / D124 ensemble.

Block A.3 of the harness fix session. Per the brief: "lightweight
classifier rules; require ≥2 of 3 to agree on direction before
passing the candidate." This is a PLACEHOLDER for prod's real
multi-agent debate logic, not a re-implementation.

Three deterministic rules, each producing BULL / BEAR / NEUTRAL:
  R1 — news_signal direction (from candidate's news_signal field if present)
  R2 — gap-up magnitude (gap >= 10% from previous close → BULL; gap < 0% → BEAR)
  R3 — RVOL threshold (RVOL >= 2.0 → BULL; < 0.5 → BEAR)

Aggregation: count BULL votes vs BEAR votes. Need ≥2 BULL with
no BEAR overrides to PASS (mirrors prod's "consensus alignment"
approach without the full debate).

Usage in harness:
    from arena.consensus_stub import ConsensusStub
    cs = ConsensusStub()
    decision = cs.evaluate(news_signal="BULL", gap_pct=0.15, rvol=3.5)
    if decision == "PASS":
        # submit entry
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ConsensusResult:
    decision: Literal["PASS", "REJECT"]
    bull_votes: int
    bear_votes: int
    rule_outputs: dict[str, str]  # rule_name -> BULL/BEAR/NEUTRAL
    reason: str


class ConsensusStub:
    """3-rule consensus mock. Deterministic, no state."""

    @staticmethod
    def evaluate(
        *,
        news_signal: str = "NO_SIGNAL",
        gap_pct: float = 0.0,
        rvol: float = 1.0,
    ) -> ConsensusResult:
        # Rule 1: news direction
        sig = (news_signal or "NO_SIGNAL").upper()
        if sig in ("STRONG_BULL", "BULL"):
            r1 = "BULL"
        elif sig in ("STRONG_BEAR", "BEAR"):
            r1 = "BEAR"
        else:
            r1 = "NEUTRAL"

        # Rule 2: gap magnitude (gap is signed fraction; 0.10 = +10%)
        if gap_pct >= 0.10:
            r2 = "BULL"
        elif gap_pct < 0.0:
            r2 = "BEAR"
        else:
            r2 = "NEUTRAL"

        # Rule 3: RVOL
        if rvol >= 2.0:
            r3 = "BULL"
        elif rvol < 0.5:
            r3 = "BEAR"
        else:
            r3 = "NEUTRAL"

        rule_outputs = {"news": r1, "gap": r2, "rvol": r3}
        bull = sum(1 for v in rule_outputs.values() if v == "BULL")
        bear = sum(1 for v in rule_outputs.values() if v == "BEAR")

        # PASS requires 2+ BULL with NO BEAR override
        if bull >= 2 and bear == 0:
            return ConsensusResult(
                decision="PASS", bull_votes=bull, bear_votes=bear,
                rule_outputs=rule_outputs,
                reason=f"{bull} bull / {bear} bear → consensus",
            )
        return ConsensusResult(
            decision="REJECT", bull_votes=bull, bear_votes=bear,
            rule_outputs=rule_outputs,
            reason=f"{bull} bull / {bear} bear → no consensus",
        )
