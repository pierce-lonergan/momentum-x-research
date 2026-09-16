"""
D221 Phase F: Historical distribution cache for Tier 3 decision-learning messages.

Loads `data/journals/journal_*.jsonl` (per-evaluation agent signals + MFCS)
and `data/backfill/features_labeled.jsonl` + `data/backfill/labels_shards/`
(per-(date, ticker) realized outcomes), joins them on (session_date, ticker),
and exposes percentile / historical-winrate lookups so every Discord
message can be annotated with statistical context against the training
substrate.

### Why a separate module
Decision-learning messages need to ground every numeric claim in a
distribution. Doing this inline in alerts.py would couple the message
formatter to the analytics layer; doing it in a fresh module keeps the
analytics testable and the message-formatter side-effects isolated.

### What's in scope (per the Sunday Tier 3 design)
- Percentile rank of MFCS, composite scores, agent confidences against
  the historical journal distribution.
- Historical conversion rate at each metric level (binned), using the
  joined labels_shards close_win field as the outcome.
- Catalyst-type base rates (FDA, M&A, etc.) win rates.
- Per-agent historical signal-vs-outcome accuracy (BULL→win match rate).
- Consensus strength distribution across agents.

### Out of scope (deferred, called out in module docstring of caller)
- Regime-conditional distributions (waits for 1A/1B Monday).
- Cross-agent interaction terms.
- Time-of-day percentiles.

### Invariants
1. NEVER raises into the caller — degraded distribution is better than
   broken Discord. Empty/missing data yields a sentinel result, not an
   exception.
2. All percentile/winrate queries handle empty distributions, single-
   element distributions, NaN values, and out-of-range queries.
3. Cache is loaded lazily on first query, then memoized. Re-load by
   instantiating a fresh `DistributionCache()`.
4. Pure functions everywhere except the constructor's disk reads.
"""

from __future__ import annotations

import json
import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_JOURNALS_DIR = _PROJECT_ROOT / "data" / "journals"
_LABELS_BASE = _PROJECT_ROOT / "data" / "backfill" / "features_labeled.jsonl"
_LABELS_SHARDS = _PROJECT_ROOT / "data" / "backfill" / "labels_shards"


# ── Public sentinel for missing-data queries ─────────────────────────


@dataclass(frozen=True)
class StatLookup:
    """Result of a percentile/winrate query.

    `value` is the requested point estimate (or None if undefined).
    `n` is the sample size the estimate is based on.
    `note` is a short human-readable status: 'ok', 'empty_dist',
    'single_elem', 'out_of_range', 'no_overlap'.

    Callers are responsible for rendering 'value=None' as 'n/a' or
    falling back to a neutral message.
    """

    value: float | None
    n: int
    note: str = "ok"

    @property
    def ok(self) -> bool:
        return self.value is not None and self.note == "ok"


# ── Helpers (pure) ───────────────────────────────────────────────────


def _safe_float(v: Any) -> float | None:
    """Coerce to float; return None on failure or NaN."""
    if v is None:
        return None
    try:
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (ValueError, TypeError):
        return None


def _percentile_rank(sorted_values: list[float], query: float) -> float | None:
    """Return the percentile rank (0..1) of `query` against the sorted list.

    Uses the "definition 6" empirical CDF: rank / (n + 1). For n == 0
    returns None. For n == 1 returns 0.5 (single-elem fallback).
    `query=NaN` returns None.
    """
    if query != query:  # NaN
        return None
    n = len(sorted_values)
    if n == 0:
        return None
    if n == 1:
        return 0.5
    # Binary-search insertion index
    lo, hi = 0, n
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_values[mid] < query:
            lo = mid + 1
        else:
            hi = mid
    rank = lo + 1  # 1-indexed
    return rank / (n + 1)


def _bin_winrate(
    samples: list[tuple[float, bool]],
    query: float,
    bin_half_width: float,
) -> StatLookup:
    """Return (winrate, count) for samples within +/- bin_half_width of query.

    samples: list of (metric_value, won_bool).
    """
    if query != query:
        return StatLookup(None, 0, "out_of_range")
    if not samples:
        return StatLookup(None, 0, "empty_dist")
    in_bin = [w for v, w in samples if abs(v - query) <= bin_half_width]
    if not in_bin:
        return StatLookup(None, 0, "no_overlap")
    return StatLookup(sum(in_bin) / len(in_bin), len(in_bin), "ok")


# ── Cache ────────────────────────────────────────────────────────────


@dataclass
class _RawData:
    """Internal: parsed journal + labels join."""

    # All MFCS values observed across journal entries (any action).
    all_mfcs: list[float] = field(default_factory=list)
    # (mfcs, won) pairs where outcome is known via labels join.
    mfcs_outcome_pairs: list[tuple[float, bool]] = field(default_factory=list)
    # Per-agent: list of (confidence, signal_str, won_bool_or_None).
    per_agent: dict[str, list[tuple[float, str, bool | None]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    # Catalyst type → list of won_bool (subset where outcome known).
    catalyst_outcomes: dict[str, list[bool]] = field(
        default_factory=lambda: defaultdict(list)
    )
    # Consensus strength values: standard deviation of agent signal numerics
    # across each evaluation. Lower std = higher consensus.
    consensus_strength_values: list[float] = field(default_factory=list)


_SIGNAL_TO_NUMERIC: dict[str, float] = {
    "STRONG_BEAR": -2.0, "BEAR": -1.0, "NEUTRAL": 0.0,
    "BULL": 1.0, "STRONG_BULL": 2.0,
}


class DistributionCache:
    """Lazy-loaded historical distribution cache.

    Construct once per session; first query triggers disk read; subsequent
    queries are in-memory. Disk read is failure-tolerant: missing files /
    malformed rows are logged and skipped, never raised.
    """

    def __init__(self) -> None:
        self._raw: _RawData | None = None
        self._sorted_mfcs: list[float] | None = None
        self._loaded = False

    # ── Loading ──────────────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        try:
            self._raw = self._load_raw()
            self._sorted_mfcs = sorted(self._raw.all_mfcs)
        except Exception as e:
            # NEVER let cache failure crash the caller.
            logger.warning(
                "DistributionCache load failed: %s. All queries will return "
                "empty_dist sentinels until next process restart.",
                e,
            )
            self._raw = _RawData()
            self._sorted_mfcs = []
        finally:
            self._loaded = True

    def _load_raw(self) -> _RawData:
        raw = _RawData()

        # Build labels index: (date, ticker) -> close_win bool.
        labels: dict[tuple[str, str], bool] = {}
        for path in self._iter_label_files():
            for row in self._iter_jsonl(path):
                d = row.get("date")
                t = row.get("ticker")
                w = _safe_float(row.get("close"))
                if isinstance(d, str) and isinstance(t, str) and w is not None:
                    labels[(d, t)] = w > 0

        # Walk journals.
        for jpath in sorted(_JOURNALS_DIR.glob("journal_*.jsonl")):
            for row in self._iter_jsonl(jpath):
                date = row.get("session_date")
                ticker = row.get("ticker")
                mfcs = _safe_float(row.get("mfcs"))
                if mfcs is not None:
                    raw.all_mfcs.append(mfcs)
                    label = labels.get((date, ticker)) if (
                        isinstance(date, str) and isinstance(ticker, str)
                    ) else None
                    if label is not None:
                        raw.mfcs_outcome_pairs.append((mfcs, label))

                # Per-agent
                sigs = row.get("agent_signals") or []
                signal_numerics: list[float] = []
                catalyst_for_row: str | None = None
                for sig in sigs:
                    if not isinstance(sig, dict):
                        continue
                    agent_id = sig.get("agent_id")
                    if not isinstance(agent_id, str):
                        continue
                    conf = _safe_float(sig.get("confidence"))
                    sig_str = sig.get("signal", "NEUTRAL")
                    if not isinstance(sig_str, str):
                        sig_str = "NEUTRAL"
                    won = labels.get((date, ticker)) if (
                        isinstance(date, str) and isinstance(ticker, str)
                    ) else None
                    if conf is not None:
                        raw.per_agent[agent_id].append((conf, sig_str, won))
                    if sig_str in _SIGNAL_TO_NUMERIC:
                        signal_numerics.append(_SIGNAL_TO_NUMERIC[sig_str])
                    if agent_id == "news_agent":
                        ct = sig.get("catalyst_type")
                        if isinstance(ct, str):
                            catalyst_for_row = ct

                # Consensus strength = inverse-std (lower std = higher
                # consensus). We store stdev directly; consumers map to
                # consensus by 1 - normalized_std.
                if len(signal_numerics) >= 2:
                    mean = sum(signal_numerics) / len(signal_numerics)
                    var = sum((x - mean) ** 2 for x in signal_numerics) / len(signal_numerics)
                    raw.consensus_strength_values.append(math.sqrt(var))

                # Catalyst-type win rates
                if catalyst_for_row:
                    won = labels.get((date, ticker)) if (
                        isinstance(date, str) and isinstance(ticker, str)
                    ) else None
                    if won is not None:
                        raw.catalyst_outcomes[catalyst_for_row].append(won)

        logger.info(
            "DistributionCache loaded: %d journal MFCS values, %d with outcomes, "
            "%d agents, %d catalyst types, %d consensus measurements",
            len(raw.all_mfcs), len(raw.mfcs_outcome_pairs),
            len(raw.per_agent), len(raw.catalyst_outcomes),
            len(raw.consensus_strength_values),
        )
        return raw

    @staticmethod
    def _iter_label_files() -> Iterable[Path]:
        if _LABELS_BASE.exists():
            yield _LABELS_BASE
        if _LABELS_SHARDS.exists() and _LABELS_SHARDS.is_dir():
            for p in sorted(_LABELS_SHARDS.glob("labels_*.jsonl")):
                yield p

    @staticmethod
    def _iter_jsonl(path: Path) -> Iterable[dict]:
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                        if isinstance(row, dict):
                            yield row
                    except json.JSONDecodeError:  # noqa: silent-handler
                        # By-design: streaming reader of historical JSONL.
                        # A single malformed line in a 5K-row file should
                        # not abort the read; downstream queries against
                        # the partial cache still work. Counts not logged
                        # to keep the cache load silent in normal operation.
                        continue
        except OSError as e:
            logger.debug("DistributionCache: skip unreadable %s (%s)", path, e)

    # ── Query API ────────────────────────────────────────────────────

    def mfcs_percentile(self, mfcs_value: float | None) -> StatLookup:
        """Where does this MFCS rank against historical evaluations?"""
        self._ensure_loaded()
        v = _safe_float(mfcs_value)
        if v is None:
            return StatLookup(None, 0, "out_of_range")
        if not self._sorted_mfcs:
            return StatLookup(None, 0, "empty_dist")
        rank = _percentile_rank(self._sorted_mfcs, v)
        return StatLookup(rank, len(self._sorted_mfcs), "ok" if rank is not None else "out_of_range")

    def mfcs_historical_winrate(
        self,
        mfcs_value: float | None,
        bin_half_width: float = 0.05,
    ) -> StatLookup:
        """Historical close-win rate among candidates with MFCS within
        +/- bin_half_width of the query."""
        self._ensure_loaded()
        v = _safe_float(mfcs_value)
        if v is None:
            return StatLookup(None, 0, "out_of_range")
        return _bin_winrate(self._raw.mfcs_outcome_pairs, v, bin_half_width)

    def catalyst_winrate(self, catalyst_type: str | None) -> StatLookup:
        """Historical close-win rate for this catalyst type."""
        self._ensure_loaded()
        if not isinstance(catalyst_type, str) or not catalyst_type:
            return StatLookup(None, 0, "out_of_range")
        outcomes = self._raw.catalyst_outcomes.get(catalyst_type, [])
        if not outcomes:
            return StatLookup(None, 0, "empty_dist")
        return StatLookup(sum(outcomes) / len(outcomes), len(outcomes), "ok")

    def agent_historical_accuracy(self, agent_id: str | None) -> StatLookup:
        """Fraction of journal entries where this agent's signal was
        directionally aligned with the realized outcome.

        BULL/STRONG_BULL aligned with WIN; BEAR/STRONG_BEAR/NEUTRAL aligned
        with LOSS (NEUTRAL counts as a correct "low conviction" call on a
        loss). Same alignment definition as `post_trade.is_signal_aligned`.
        """
        self._ensure_loaded()
        if not isinstance(agent_id, str) or not agent_id:
            return StatLookup(None, 0, "out_of_range")
        records = self._raw.per_agent.get(agent_id, [])
        records_with_outcome = [
            (sig, won) for _conf, sig, won in records if won is not None
        ]
        if not records_with_outcome:
            return StatLookup(None, 0, "empty_dist")
        aligned = 0
        for sig, won in records_with_outcome:
            if won and sig in ("BULL", "STRONG_BULL"):
                aligned += 1
            elif (not won) and sig in ("BEAR", "STRONG_BEAR", "NEUTRAL"):
                aligned += 1
        return StatLookup(
            aligned / len(records_with_outcome),
            len(records_with_outcome),
            "ok",
        )

    def consensus_strength_percentile(self, consensus_std: float | None) -> StatLookup:
        """Percentile rank of this evaluation's consensus_std against
        historical. LOWER std = HIGHER consensus (callers should invert
        the percentile if displaying as "consensus strength" rather than
        "signal disagreement")."""
        self._ensure_loaded()
        v = _safe_float(consensus_std)
        if v is None:
            return StatLookup(None, 0, "out_of_range")
        sorted_vals = sorted(self._raw.consensus_strength_values)
        if not sorted_vals:
            return StatLookup(None, 0, "empty_dist")
        rank = _percentile_rank(sorted_vals, v)
        return StatLookup(rank, len(sorted_vals), "ok" if rank is not None else "out_of_range")

    def agent_confidence_percentile(
        self, agent_id: str | None, confidence: float | None,
    ) -> StatLookup:
        """Where does this agent's stated confidence rank against its own
        historical confidence distribution?"""
        self._ensure_loaded()
        if not isinstance(agent_id, str):
            return StatLookup(None, 0, "out_of_range")
        v = _safe_float(confidence)
        if v is None:
            return StatLookup(None, 0, "out_of_range")
        records = self._raw.per_agent.get(agent_id, [])
        if not records:
            return StatLookup(None, 0, "empty_dist")
        sorted_confs = sorted(c for c, _, _ in records)
        rank = _percentile_rank(sorted_confs, v)
        return StatLookup(rank, len(sorted_confs), "ok" if rank is not None else "out_of_range")

    # ── Diagnostic ──────────────────────────────────────────────────

    def stats(self) -> dict[str, int]:
        """Return summary counts for /status diagnostics."""
        self._ensure_loaded()
        return {
            "n_mfcs_observations": len(self._raw.all_mfcs),
            "n_mfcs_with_outcome": len(self._raw.mfcs_outcome_pairs),
            "n_agents": len(self._raw.per_agent),
            "n_catalyst_types": len(self._raw.catalyst_outcomes),
            "n_consensus_observations": len(self._raw.consensus_strength_values),
        }
