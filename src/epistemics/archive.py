"""The stepping-stone archive — a graveyard you can query.

`ATTEMPTS_LEDGER.md` is prose. It is excellent prose, and it is the reason this
program does not re-propose closed families, but it cannot answer the question
that matters most six months after a burial:

    "We just built X. Which of the things we gave up on died *because* we
     didn't have X?"

The program has answered that question twice, both times by hand, and both
times it paid off. The day_aggs Q1 rebuild (doc 278) unblocked a whole class of
inference and triggered the 235-273 contamination sweep in doc 279. The
point-in-time shares-outstanding feed (doc 295 -> 298) exhumed the LETF family,
which then died honestly on arithmetic instead of sitting in limbo. Neither
revival was found by searching; both were remembered.

This module is the memory. Each record keeps the closure's discrimination (see
`closure.py`), the keystone vector that was missing, and enough of the
measurement to re-score the hypothesis later without re-reading the document.

Design constraints, learned the hard way by this program:

* Append-only JSONL. Editing history is how a ledger stops being a ledger.
* Nothing is pruned. A failed experiment is a stepping stone, not waste.
* The archive stores no verdicts of its own. It records what was decided and
  why; the judgement stays in the documents and in Pierce's ratification.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .closure import Closure, ClosureRecord, Keystone

_REPO = Path(__file__).resolve().parents[2]
ARCHIVE_PATH = Path(
    os.environ.get("MOMENTUM_STEPPING_STONES")
    or _REPO / "data" / "research" / "stepping_stones.jsonl"
)


@dataclass
class SteppingStone:
    """One archived hypothesis, with everything needed to re-open it.

    Attributes
    ----------
    stone_id:
        Stable identifier, e.g. ``SS0007``.
    closure:
        The discrimination record: why it died and what was missing.
    claim:
        The hypothesis as it was actually asked, in one sentence. Written in the
        past tense on purpose — this is what *was* claimed, not what is true.
    closed_on:
        ISO date of the burial.
    revival_predicate:
        Human-readable condition under which this should be re-opened. Paired
        with the keystones, which are the machine-checkable half.
    anomalies:
        Unexplained observations noticed *while* the family was being killed.
        These are the highest-value field in the archive and the one most often
        lost: a family dies on its primary endpoint while leaving behind a
        result nobody asked for. Doc 290's "predictable volatility scale" is
        exactly this — a durable positive found inside a refutation.
    """

    stone_id: str
    closure: ClosureRecord
    claim: str
    closed_on: str
    revival_predicate: str = ""
    anomalies: list[str] = field(default_factory=list)
    commit: str = ""

    def to_dict(self) -> dict:
        return {
            "stone_id": self.stone_id,
            "claim": self.claim,
            "closed_on": self.closed_on,
            "revival_predicate": self.revival_predicate,
            "anomalies": list(self.anomalies),
            "commit": self.commit,
            **self.closure.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SteppingStone":
        return cls(
            stone_id=d["stone_id"],
            closure=ClosureRecord.from_dict(d),
            claim=d.get("claim", ""),
            closed_on=d.get("closed_on", ""),
            revival_predicate=d.get("revival_predicate", ""),
            anomalies=list(d.get("anomalies", [])),
            commit=d.get("commit", ""),
        )

    @property
    def keystone_keys(self) -> set[str]:
        return {k.key for k in self.closure.keystones}


class Archive:
    """Append-only collection of stepping stones."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else ARCHIVE_PATH

    # ── I/O ──────────────────────────────────────────────────────────────

    def load(self) -> list[SteppingStone]:
        if not self.path.exists():
            return []
        stones: list[SteppingStone] = []
        with self.path.open(encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    stones.append(SteppingStone.from_dict(json.loads(line)))
                except (json.JSONDecodeError, KeyError, ValueError) as exc:
                    raise ValueError(
                        f"{self.path}:{lineno} is not a valid stepping stone: {exc}"
                    ) from exc
        return stones

    def append(self, stone: SteppingStone, *, strict: bool = True) -> SteppingStone:
        """Append one stone. Raises if the closure record is inadmissible.

        `strict=False` is provided for backfilling historical closures whose
        measurements were never recorded in a recoverable form; it downgrades
        the admissibility check to a returned warning rather than an exception.
        """
        problems = stone.closure.validate()
        if problems and strict:
            raise ValueError(
                f"{stone.stone_id} ({stone.closure.family}) is not admissible:\n  - "
                + "\n  - ".join(problems)
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(stone.to_dict(), sort_keys=True) + "\n")
        return stone

    def next_id(self) -> str:
        existing = self.load()
        n = 0
        for s in existing:
            if s.stone_id.startswith("SS") and s.stone_id[2:].isdigit():
                n = max(n, int(s.stone_id[2:]))
        return f"SS{n + 1:04d}"

    def bury(
        self,
        family: str,
        claim: str,
        closure: Closure,
        rationale: str,
        *,
        keystones: list[Keystone] | None = None,
        effect: float | None = None,
        ci: tuple[float, float] | None = None,
        n_obs: int | None = None,
        docs: list[str] | None = None,
        revival_predicate: str = "",
        anomalies: list[str] | None = None,
        commit: str = "",
        closed_on: str | None = None,
        strict: bool = True,
    ) -> SteppingStone:
        """Record a closure. The only sanctioned way to close a family."""
        stone = SteppingStone(
            stone_id=self.next_id(),
            closure=ClosureRecord(
                family=family,
                closure=closure,
                rationale=rationale,
                keystones=keystones or [],
                effect=effect,
                ci=ci,
                n_obs=n_obs,
                docs=docs or [],
            ),
            claim=claim,
            closed_on=closed_on or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            revival_predicate=revival_predicate,
            anomalies=anomalies or [],
            commit=commit,
        )
        return self.append(stone, strict=strict)

    # ── Queries ──────────────────────────────────────────────────────────

    def by_keystone(self, key: str) -> list[SteppingStone]:
        """Every stone that named `key` as a missing piece."""
        return [s for s in self.load() if key in s.keystone_keys]

    def by_closure(self, closure: Closure) -> list[SteppingStone]:
        return [s for s in self.load() if s.closure.closure is closure]

    def anomalies(self) -> list[tuple[str, str, str]]:
        """Every unexplained observation left behind by a closure.

        Returns (stone_id, family, anomaly). This is the list to read before
        proposing anything new: the program's own refutations are the least
        crowded source of hypotheses it has access to.
        """
        out: list[tuple[str, str, str]] = []
        for s in self.load():
            for a in s.anomalies:
                out.append((s.stone_id, s.closure.family, a))
        return out

    def discrimination(self) -> dict:
        """The headline number: how much of the graveyard is about the market?

        This is the question `closure.py` exists to answer. A program that has
        closed 33 families has either learned 33 things about markets or filed
        33 things it never managed to ask, and the difference determines what it
        should do next.
        """
        stones = self.load()
        if not stones:
            return {
                "n": 0,
                "market_evidence": 0,
                "about_us": 0,
                "market_evidence_frac": None,
                "by_closure": {},
                "revivable": 0,
                "mean_evidential_weight": None,
            }

        by_closure: dict[str, int] = {}
        for s in stones:
            k = s.closure.closure.value
            by_closure[k] = by_closure.get(k, 0) + 1

        market = [s for s in stones if s.closure.closure.is_evidence_about_market]
        # "Revivable" means a named keystone exists, so an arriving capability
        # can mechanically re-open it. Cost/access closures can also revive, but
        # only on a market-structure change, which no amount of building causes.
        revivable = [
            s for s in stones
            if s.closure.keystones and not s.closure.closure.is_evidence_about_market
        ]
        weights = [s.closure.closure.evidential_weight for s in stones]

        return {
            "n": len(stones),
            "market_evidence": len(market),
            "about_us": len(stones) - len(market),
            "market_evidence_frac": len(market) / len(stones),
            "by_closure": dict(sorted(by_closure.items(), key=lambda kv: -kv[1])),
            "revivable": len(revivable),
            "mean_evidential_weight": sum(weights) / len(weights),
        }

    def keystone_census(self) -> list[tuple[str, int, list[str]]]:
        """Which missing capabilities block the most families.

        Returns (keystone, n_blocked, families), most-blocking first. This is a
        procurement and build priority list derived from the graveyard rather
        than from enthusiasm: the keystone at the top of this list unlocks the
        most re-askable questions per unit of work.
        """
        counts: dict[str, list[str]] = {}
        for s in self.load():
            for k in s.keystone_keys:
                counts.setdefault(k, []).append(s.closure.family)
        return sorted(
            ((k, len(v), v) for k, v in counts.items()),
            key=lambda t: (-t[1], t[0]),
        )
