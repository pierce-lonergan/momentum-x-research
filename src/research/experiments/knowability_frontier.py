"""doc 273 — KnowabilityFrontier: place each day's production decisions on the I(t)/A(t) curve.

The offline stage (scripts/knowability_frontier_doc273.py) measured, on the certified-null
gapper universe, how much information about the cost-cleared remainder-of-day outcome the
tape contains at each decision time t — and whether acting on it could pay. This experiment
is the live half: every night it reads the day's verdict_trace events off the bus and reports
WHERE on that curve production acted (SUBMITTED) and declined (BLOCKED_*). Under the doc-273
verdict the placement is interpretation-free here — the curve artifact carries the numbers;
the nightly metric is descriptive placement (e.g. "100% of today's entries happened at t
where measured tape-information was statistically zero").

DORMANT-C: read-only, never imported by production, never-raises by substrate contract.
Artifact: data/research/doc273_frontier.parquet (written by the offline stage; if absent
the metrics say so and nothing breaks).
"""
from __future__ import annotations

from pathlib import Path

from src.research.event_bus import Event, default_data_root
from src.research.experiment import Experiment, register

# doc-273 decision grid (ET minute-of-day -> label), frozen in the prereg.
_GRID = [(575, "9:35"), (580, "9:40"), (585, "9:45"), (590, "9:50"), (600, "10:00"),
         (615, "10:15"), (630, "10:30"), (660, "11:00"), (690, "11:30"), (720, "12:00"),
         (780, "13:00"), (840, "14:00"), (900, "15:00")]


def _bucket(ts) -> str:
    """Map a decision timestamp to the at-or-before grid bucket ('pre'/'post' off-grid)."""
    if ts is None:
        return "unknown"
    try:
        if ts.tzinfo is not None:
            import zoneinfo
            ts = ts.astimezone(zoneinfo.ZoneInfo("America/New_York"))
        mod = ts.hour * 60 + ts.minute
    except Exception:
        return "unknown"
    if mod < 575:
        return "pre"
    label = "post"
    for m, lbl in _GRID:
        if mod >= m:
            label = lbl
    return label if mod <= 960 else "post"


@register
class KnowabilityFrontier(Experiment):
    name = "knowability_frontier"
    consumes = {"verdict_trace"}

    def __init__(self) -> None:
        self.submitted: dict[str, int] = {}
        self.blocked: dict[str, int] = {}
        self.n_sub = 0
        self.n_blk = 0

    def on_event(self, e: Event) -> None:
        stage = str((e.payload or {}).get("stage", "")).upper()
        if not stage:
            return
        b = _bucket(e.ts)
        if stage == "SUBMITTED":
            self.n_sub += 1
            self.submitted[b] = self.submitted.get(b, 0) + 1
        elif stage.startswith("BLOCKED_") or stage == "DOWNGRADED_ABSENCE":
            self.n_blk += 1
            self.blocked[b] = self.blocked.get(b, 0) + 1

    def _frontier(self):
        try:
            import pandas as pd
            path = Path(default_data_root()) / "research" / "doc273_frontier.parquet"
            if not path.exists():
                return None
            return pd.read_parquet(path).set_index("t_lbl")
        except Exception:
            return None

    def metrics(self) -> dict:
        out: dict = {"n_submitted": self.n_sub, "n_blocked": self.n_blk,
                     "submitted_by_t": self.submitted, "blocked_by_t": self.blocked}
        fr = self._frontier()
        if fr is None:
            out["frontier"] = "artifact-missing (run the doc-273 offline stage)"
            return out
        try:
            on_grid = [(b, n) for b, n in self.submitted.items() if b in fr.index]
            tot = sum(n for _b, n in on_grid)
            if tot:
                out["sub_wtd_I"] = round(float(sum(
                    max(fr.loc[b, "I_gbm"], fr.loc[b, "I_logit"]) * n for b, n in on_grid) / tot), 5)
                out["sub_wtd_A"] = round(float(sum(
                    fr.loc[b, "A_mean"] * n for b, n in on_grid) / tot), 5)
                out["sub_wtd_oracle"] = round(float(sum(
                    fr.loc[b, "oracle_mean"] * n for b, n in on_grid) / tot), 5)
            out["off_grid_submits"] = self.n_sub - tot
        except Exception as exc:
            out["frontier"] = f"placement-error: {exc!r}"
        return out

    def promote_gate(self) -> str | None:
        return ("descriptive experiment - no promotion path; value = the nightly placement "
                "of production decisions on the doc-273 knowability curve")

    def kill_gate(self) -> str | None:
        return None
