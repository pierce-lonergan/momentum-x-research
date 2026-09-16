"""Scenario loader for the production arena.

Reads `data/backfill/candidates.jsonl` (5,862 historical pre-market candidates),
joins each row with its minute bars from `data/bar_recordings/<date>/<ticker>.json`,
and attaches multi-horizon outcome labels from BOTH of:
  - `data/backfill/features_labeled.jsonl` (canonical, pre-D221 curated file)
  - `data/backfill/labels_shards/labels_<YYYY-MM-DD>.jsonl` (D221 backfill daemon)

Emits immutable `Scenario` instances matching the contract in
`src/production_arena/types.py`.

Design notes:
- stdlib only (json, dataclasses, datetime, logging, pathlib).
- No global mutable state outside an LRU-style label cache keyed on file mtime.
- All paths anchored to `_PROJECT_ROOT` (computed from `__file__`) so the
  static-analysis suite at `tests/static_analysis/test_relative_paths.py`
  is satisfied — no cwd-dependent literals.
- Malformed JSON lines and missing bar files are logged and skipped, never
  silently swallowed (no `except: pass`).
- The 9:31 ET open is the canonical entry price (matches
  `scripts/backfill_features.py`); 9:30 ET == 13:30 UTC == "T13:30".
- Label dedup policy: **base wins on conflict.** When a `(date, ticker)` is
  present in both `features_labeled.jsonl` and a shard, the canonical file's
  row is authoritative. This preserves the invariant "v0's training rows are
  exactly v0's training rows" — any v0↔vN CV-AUC delta is attributable to
  NEW data, not to silently-rewritten old rows. Conflicts are logged at
  DEBUG level so they can be audited post-hoc if the delta surprises.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

from src.production_arena.types import LabeledOutcome, MinuteBar, Scenario

logger = logging.getLogger(__name__)

# ── Repo anchors (NEVER use relative literals — see test_relative_paths.py) ──

_PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]
_CANDIDATES_PATH: Path = _PROJECT_ROOT / "data" / "backfill" / "candidates.jsonl"
_LABELS_PATH: Path = _PROJECT_ROOT / "data" / "backfill" / "features_labeled.jsonl"
_SHARDS_DIR: Path = _PROJECT_ROOT / "data" / "backfill" / "labels_shards"
_BAR_RECORDINGS_DIR: Path = _PROJECT_ROOT / "data" / "bar_recordings"

# 9:30 ET == 13:30 UTC during DST, 14:30 UTC during EST. The bar files use
# UTC ISO timestamps, so we match on either prefix and let the chronological
# ordering carry the rest.
_MARKET_OPEN_UTC_PREFIXES: tuple[str, ...] = ("T13:30", "T14:30")


# ── Helpers ─────────────────────────────────────────────────────────────


def _parse_session_date(s: str) -> date | None:
    """Parse a 'YYYY-MM-DD' string. Returns None on malformed input."""
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError) as e:
        logger.warning("Could not parse session_date %r: %s", s, e)
        return None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL file, logging and skipping malformed lines."""
    if not path.exists():
        logger.warning("JSONL not found: %s", path)
        return []
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                rows.append(json.loads(raw))
            except json.JSONDecodeError as e:
                logger.warning("Skipping malformed JSON in %s:%d (%s)", path, lineno, e)
    return rows


def _load_bar_file(session_date: str, ticker: str) -> tuple[MinuteBar, ...] | None:
    """Load minute bars for `(session_date, ticker)`. Returns None when missing."""
    bar_path = _BAR_RECORDINGS_DIR / session_date / f"{ticker}.json"
    if not bar_path.exists():
        return None
    try:
        with open(bar_path, encoding="utf-8") as f:
            payload = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Could not read bar file %s: %s", bar_path, e)
        return None

    raw_bars = payload.get("bars") or []
    parsed: list[MinuteBar] = []
    for b in raw_bars:
        try:
            parsed.append(
                MinuteBar(
                    timestamp=str(b["timestamp"]),
                    open=float(b["open"]),
                    high=float(b["high"]),
                    low=float(b["low"]),
                    close=float(b["close"]),
                    volume=int(b.get("volume", 0)),
                    vwap=float(b["vwap"]) if b.get("vwap") is not None else None,
                )
            )
        except (KeyError, TypeError, ValueError) as e:
            logger.warning("Skipping malformed bar in %s: %s", bar_path, e)
    parsed.sort(key=lambda mb: mb.timestamp)
    return tuple(parsed)


def _find_market_open_index(bars: tuple[MinuteBar, ...]) -> int | None:
    """Return the index of the 9:30 ET bar, or None if absent."""
    for i, bar in enumerate(bars):
        if any(prefix in bar.timestamp for prefix in _MARKET_OPEN_UTC_PREFIXES):
            return i
    return None


def _premarket_volume_from_bars(bars: tuple[MinuteBar, ...]) -> int:
    """Sum volume across bars before market open (9:30 ET == 13:30/14:30 UTC)."""
    open_idx = _find_market_open_index(bars)
    if open_idx is None:
        return 0
    return sum(b.volume for b in bars[:open_idx])


def _entry_price_from_bars(bars: tuple[MinuteBar, ...]) -> float | None:
    """The 9:31 open — first bar after market open. Matches backfill_features.py."""
    open_idx = _find_market_open_index(bars)
    if open_idx is None or open_idx + 1 >= len(bars):
        return None
    return bars[open_idx + 1].open


def _label_to_outcome(label: dict[str, Any] | None, fallback_entry: float | None) -> LabeledOutcome:
    """Build a LabeledOutcome from a labeled-features row. All-None when unlabeled."""
    if not label:
        return LabeledOutcome(entry_price=fallback_entry)

    def _get_float(key: str) -> float | None:
        v = label.get(key)
        return float(v) if isinstance(v, (int, float)) else None

    def _win(horizon_key: str) -> bool | None:
        v = label.get(horizon_key)
        if not isinstance(v, (int, float)):
            return None
        return v > 0

    return LabeledOutcome(
        close_return=_get_float("close"),
        mfe_pct=_get_float("mfe_pct"),
        mae_pct=_get_float("mae_pct"),
        time_to_mfe_min=int(label["time_to_mfe_min"])
        if isinstance(label.get("time_to_mfe_min"), (int, float))
        else None,
        win_t1=_win("t1"),
        win_t5=_win("t5"),
        win_t15=_win("t15"),
        win_t30=_win("t30"),
        win_t60=_win("t60"),
        win_close=_win("close"),
        entry_price=_get_float("entry_price") or fallback_entry,
        orb_high=_get_float("orb_high"),
        orb_low=_get_float("orb_low"),
        orb_broken=bool(label["orb_broken"]) if "orb_broken" in label else None,
    )


def _build_premarket_features(
    candidate: dict[str, Any],
    bars: tuple[MinuteBar, ...] | None,
    label: dict[str, Any] | None,
) -> dict[str, Any]:
    """Assemble the premarket feature dict required by the Scenario contract."""
    pm_volume = _premarket_volume_from_bars(bars) if bars else 0
    features: dict[str, Any] = {
        "gap_pct": candidate.get("gap_pct"),
        "premarket_volume": pm_volume,
        "dollar_volume": candidate.get("dollar_volume"),
        "price": candidate.get("open"),
        "prior_close": candidate.get("prior_close"),
        "vwap": candidate.get("vwap"),
        "day_volume": candidate.get("volume"),
    }
    if label:
        # Surface labeled-only features (e.g. pre_market_high) so downstream
        # code can read them without re-loading features_labeled.jsonl.
        for k in ("pre_market_high", "orb_range_pct", "day_high", "day_low", "day_close"):
            if k in label:
                features[k] = label[k]
    return features


# ── Public collection ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScenarioCollection:
    """Iterable of Scenario, with __len__ for tqdm progress and filter_by()
    for in-memory sweep dimensions.

    Stored as a tuple so the collection itself is hashable / picklable for
    the pipeline_runner's process pool.
    """

    scenarios: tuple[Scenario, ...]

    def __len__(self) -> int:
        return len(self.scenarios)

    def __iter__(self) -> Iterator[Scenario]:
        return iter(self.scenarios)

    def filter_by(
        self,
        min_gap: float | None = None,
        max_float: float | None = None,
        has_label: bool | None = None,
        tickers: list[str] | None = None,
    ) -> "ScenarioCollection":
        ticker_set = {t.upper() for t in tickers} if tickers else None
        kept: list[Scenario] = []
        for s in self.scenarios:
            if ticker_set is not None and s.ticker.upper() not in ticker_set:
                continue
            gap = s.premarket_features.get("gap_pct")
            if min_gap is not None and (gap is None or gap < min_gap):
                continue
            float_shares = s.premarket_features.get("float_shares")
            if max_float is not None and float_shares is not None and float_shares > max_float:
                continue
            if has_label is not None:
                labeled = s.labeled_outcome.close_return is not None
                if labeled is not has_label:
                    continue
            kept.append(s)
        return ScenarioCollection(tuple(kept))

    @property
    def stats(self) -> dict[str, Any]:
        n_total = len(self.scenarios)
        n_with_bars = sum(1 for s in self.scenarios if s.minute_bars)
        n_labeled = sum(1 for s in self.scenarios if s.labeled_outcome.close_return is not None)
        dates = sorted({s.session_date for s in self.scenarios})
        unique_tickers = {s.ticker for s in self.scenarios}
        return {
            "n_total": n_total,
            "n_with_bars": n_with_bars,
            "n_labeled": n_labeled,
            "n_unique_tickers": len(unique_tickers),
            "date_range": (dates[0], dates[-1]) if dates else (None, None),
            "n_dates": len(dates),
        }


# ── Loader ─────────────────────────────────────────────────────────────────


def _index_labels(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """Build a (date, ticker) -> label dict from a list of label rows.

    Kept for backward compatibility; prefer `_build_label_index` which
    handles both the canonical file and shards plus conflict logging.
    """
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        d = row.get("date")
        t = row.get("ticker")
        if isinstance(d, str) and isinstance(t, str):
            out[(d, t)] = row
    return out


def _build_label_index(
    base_path: Path,
    shards_dir: Path,
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, int]]:
    """Build a unified (date, ticker) -> label index from the canonical file
    and any per-day shards.

    Dedup policy: **base wins on conflict.** A `(date, ticker)` present in
    both `base_path` (features_labeled.jsonl) and a shard under `shards_dir`
    keeps the base row — the canonical file is treated as authoritative so
    v0's training data remains immutable under v1 training. Conflicts are
    emitted at DEBUG level, one log line per conflict, for post-hoc audit.

    Returns:
        (index, counts) where counts = {"base": N, "shards": M, "conflicts": K}.
        - `base`: rows contributed by the canonical file.
        - `shards`: rows contributed by shards AFTER dedup (non-conflicting).
        - `conflicts`: shard rows whose (date, ticker) already appeared in base.
    """
    index: dict[tuple[str, str], dict[str, Any]] = {}
    counts = {"base": 0, "shards": 0, "conflicts": 0}

    # Canonical file first so it wins on conflict.
    for row in _read_jsonl(base_path):
        d = row.get("date")
        t = row.get("ticker")
        if isinstance(d, str) and isinstance(t, str):
            index[(d, t)] = row
            counts["base"] += 1

    # Shards: sorted filename order for determinism across runs.
    if shards_dir.exists() and shards_dir.is_dir():
        for shard_path in sorted(shards_dir.glob("labels_*.jsonl")):
            for row in _read_jsonl(shard_path):
                d = row.get("date")
                t = row.get("ticker")
                if not (isinstance(d, str) and isinstance(t, str)):
                    continue
                key = (d, t)
                if key in index:
                    # Base-wins: canonical row is authoritative. Log so the
                    # next investigator can trace delta surprises.
                    logger.debug(
                        "label dedup: (%s, %s) present in base and %s — base wins",
                        d, t, shard_path.name,
                    )
                    counts["conflicts"] += 1
                    continue
                index[key] = row
                counts["shards"] += 1

    return index, counts


def load_scenarios(
    date_range: tuple[date, date] | None = None,
    tickers: list[str] | None = None,
    require_bars: bool = True,
    require_label: bool = False,
) -> ScenarioCollection:
    """Load scenarios matching filters. Returns a ScenarioCollection.

    Args:
        date_range: (start, end) inclusive `date` tuple. None = all dates.
        tickers: optional whitelist — only these symbols (case-insensitive).
        require_bars: skip scenarios without a minute-bar recording on disk.
        require_label: skip scenarios without a labeled-features row.

    Disk-IO failures (missing files, malformed JSON) are logged via
    `logging` and the offending row is dropped — this loader is not fatal
    on partial data, by design.
    """
    candidates = _read_jsonl(_CANDIDATES_PATH)
    label_index, label_counts = _build_label_index(_LABELS_PATH, _SHARDS_DIR)

    ticker_filter = {t.upper() for t in tickers} if tickers else None

    def _coerce_to_date(v):
        if v is None or isinstance(v, date):
            return v
        if isinstance(v, str):
            try:
                return datetime.strptime(v, "%Y-%m-%d").date()
            except ValueError as e:
                raise ValueError(f"date_range strings must be YYYY-MM-DD (got '{v}': {e})")
        raise TypeError(f"date_range entries must be `date` or 'YYYY-MM-DD' string (got {type(v).__name__})")

    start: date | None = _coerce_to_date(date_range[0]) if date_range else None
    end: date | None = _coerce_to_date(date_range[1]) if date_range else None

    scenarios: list[Scenario] = []
    n_skipped_date = 0
    n_skipped_ticker = 0
    n_skipped_no_bars = 0
    n_skipped_no_label = 0
    n_skipped_malformed = 0

    for cand in candidates:
        ticker = cand.get("ticker")
        session_date = cand.get("date")
        if not isinstance(ticker, str) or not isinstance(session_date, str):
            n_skipped_malformed += 1
            continue

        if ticker_filter is not None and ticker.upper() not in ticker_filter:
            n_skipped_ticker += 1
            continue

        sd = _parse_session_date(session_date)
        if sd is None:
            n_skipped_malformed += 1
            continue
        if start is not None and sd < start:
            n_skipped_date += 1
            continue
        if end is not None and sd > end:
            n_skipped_date += 1
            continue

        bars = _load_bar_file(session_date, ticker)
        if bars is None:
            if require_bars:
                n_skipped_no_bars += 1
                continue
            bars = ()

        label = label_index.get((session_date, ticker))
        if label is None and require_label:
            n_skipped_no_label += 1
            continue

        # Entry price preference: labeled 9:31 open -> bar-derived 9:31 open ->
        # daily open from candidates.jsonl (last-resort fallback).
        bar_entry = _entry_price_from_bars(bars) if bars else None
        fallback_entry = bar_entry if bar_entry is not None else cand.get("open")
        labeled_outcome = _label_to_outcome(label, fallback_entry)

        premarket_features = _build_premarket_features(cand, bars, label)

        scenarios.append(
            Scenario(
                ticker=ticker,
                session_date=session_date,
                premarket_features=premarket_features,
                minute_bars=bars,
                labeled_outcome=labeled_outcome,
            )
        )

    logger.info(
        "load_scenarios: %d kept | labels: base=%d shards=%d conflicts=%d | "
        "skipped: date=%d ticker=%d no_bars=%d no_label=%d malformed=%d",
        len(scenarios),
        label_counts["base"],
        label_counts["shards"],
        label_counts["conflicts"],
        n_skipped_date,
        n_skipped_ticker,
        n_skipped_no_bars,
        n_skipped_no_label,
        n_skipped_malformed,
    )
    return ScenarioCollection(tuple(scenarios))
