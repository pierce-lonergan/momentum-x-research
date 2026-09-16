"""doc 272 C2 — Experiment interface + registry + offline runner (DORMANT-C).

The concurrent-experiment substrate: many small read-only experiments ride ONE
pass over the unified event view (src.research.event_bus) per session date.
Never imported by production. Run nightly (rides the shadow-grader slot):

    python -m src.research.experiment --date YYYY-MM-DD [--data-root D]
                                      [--out-root D] [--no-write]

Contract:
  * Each registered experiment is constructed fresh per run, fed only the
    event kinds it `consumes` ("*" = everything), and is wrapped never-raises:
    one experiment's exception (init, on_event, metrics, gates) cannot affect
    another, and the runner itself always exits 0 from __main__.
  * After the pass, one record per experiment is appended to
    <out_root>/research/experiments/<name>/metrics_<date>.jsonl
    (jsonl append: re-runs add lines; readers take the last line per date).
    out_root defaults to the LOCAL repo data dir — overriding data_root to
    read another tree's streams never writes into that tree.
  * Gates are HUMAN-READABLE status strings, not actions: promote_gate()
    describes what promotion would require / its current status; a non-None
    kill_gate() is an auto-retire recommendation for the operator. Nothing
    here touches capital or production config.
  * Capital isolation (future): `paper_account_id` exists on the interface so
    a capital-touching experiment would bind to its OWN per-experiment paper
    account. It is an interface field only — nothing reads it yet.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable

from src.research.event_bus import Event, default_data_root, events

SCHEMA_VERSION = 1


class Experiment(ABC):
    """One concurrent read-only experiment. Subclass, set name/consumes, register."""

    name: str = "unnamed"
    consumes: set[str] = set()          # event kinds to receive; {"*"} = all
    paper_account_id: str | None = None  # capital isolation hook — unused for now

    @abstractmethod
    def on_event(self, e: Event) -> None:
        """Accumulate state from one event (called in time order)."""

    @abstractmethod
    def metrics(self) -> dict:
        """Nightly emit — a small JSON-serializable dict."""

    def promote_gate(self) -> str | None:
        """Human-readable promotion criterion/status, e.g. 'n>=30 and winsor_mean>0'."""
        return None

    def kill_gate(self) -> str | None:
        """If non-None, the kill criterion fired — auto-retire recommendation."""
        return None


#: Registered experiment factories (zero-arg callables, typically the class).
REGISTRY: list[Callable[[], Experiment]] = []


def register(factory: Callable[[], Experiment]) -> Callable[[], Experiment]:
    """Decorator/function: add an Experiment factory to REGISTRY."""
    REGISTRY.append(factory)
    return factory


def _load_builtin_experiments() -> None:
    """Import the builtin experiments package so its @register decorators run."""
    try:
        import src.research.experiments  # noqa: F401  (registration side effect)
    except Exception:
        pass


def _safe_gate(fn: Callable[[], str | None]) -> str | None:
    try:
        return fn()
    except Exception as exc:
        return f"gate-error: {exc!r}"


def _write_record(record: dict, out_root: Path) -> None:
    """Append one metrics line; never raises (write failure must not kill the run)."""
    try:
        target = out_root / "research" / "experiments" / record["name"]
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"metrics_{record['date']}.jsonl"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
    except Exception:
        pass


def run_experiments(date: str, data_root: str | Path | None = None,
                    out_root: str | Path | None = None, write: bool = True,
                    registry: list[Callable[[], Experiment]] | None = None,
                    ) -> dict[str, dict]:
    """Fan ONE pass of event_bus.events(date) to every registered experiment.

    Returns {name: record} where record = {"schema_version", "date", "name",
    "metrics", "promote_gate", "kill_gate", "n_events_seen", "generated_at"}.
    A failed experiment's record carries metrics={"error": ...}; failures are
    fully isolated per experiment. Optionally appends each record to
    <out_root>/research/experiments/<name>/metrics_<date>.jsonl.
    """
    if registry is None:
        _load_builtin_experiments()
        factories = list(REGISTRY)
    else:
        factories = list(registry)

    live: list[Experiment] = []
    failed: dict[int, str] = {}          # id(exp) -> error string
    init_failed: list[tuple[str, str]] = []
    seen: dict[int, int] = {}            # id(exp) -> events delivered

    for factory in factories:
        try:
            exp = factory()
            live.append(exp)
            seen[id(exp)] = 0
        except Exception as exc:
            name = getattr(factory, "name", None) or getattr(factory, "__name__", repr(factory))
            init_failed.append((str(name), f"init-error: {exc!r}"))

    for event in events(date, data_root=data_root):
        for exp in live:
            if id(exp) in failed:
                continue
            try:
                if "*" in exp.consumes or event.kind in exp.consumes:
                    seen[id(exp)] += 1
                    exp.on_event(event)
            except Exception as exc:
                failed[id(exp)] = f"on_event-error: {exc!r}"

    generated_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    results: dict[str, dict] = {}

    def _record(name: str, metrics: dict, promote: str | None, kill: str | None,
                n_seen: int) -> dict:
        return {"schema_version": SCHEMA_VERSION, "date": str(date), "name": name,
                "metrics": metrics, "promote_gate": promote, "kill_gate": kill,
                "n_events_seen": n_seen, "generated_at": generated_at}

    for name, err in init_failed:
        results[name] = _record(name, {"error": err}, None, None, 0)

    for exp in live:
        name = getattr(exp, "name", exp.__class__.__name__)
        if id(exp) in failed:
            metrics: dict = {"error": failed[id(exp)]}
        else:
            try:
                metrics = exp.metrics()
            except Exception as exc:
                metrics = {"error": f"metrics-error: {exc!r}"}
        results[name] = _record(name, metrics, _safe_gate(exp.promote_gate),
                                _safe_gate(exp.kill_gate), seen.get(id(exp), 0))

    if write:
        root = Path(out_root) if out_root is not None else default_data_root()
        for record in results.values():
            _write_record(record, root)
    return results


def _format_line(record: dict) -> str:
    metrics = record.get("metrics", {})
    body = " ".join(f"{k}={json.dumps(v, default=str)}" for k, v in metrics.items())
    promote = record.get("promote_gate") or "-"
    kill = record.get("kill_gate") or "-"
    return (f"EXPERIMENT {record.get('name')} {record.get('date')}: {body or 'no-metrics'} "
            f"| events={record.get('n_events_seen', 0)} | promote: {promote} | kill: {kill}")


def main(argv: list[str] | None = None) -> int:
    """Nightly entrypoint — one line per experiment; ALWAYS returns 0."""
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--date", default=dt.date.today().isoformat())
        parser.add_argument("--data-root", default=None,
                            help="read streams from this data dir (default: local repo data/)")
        parser.add_argument("--out-root", default=None,
                            help="write metrics under this data dir (default: local repo data/)")
        parser.add_argument("--no-write", action="store_true",
                            help="skip the metrics_<date>.jsonl append")
        args = parser.parse_args(argv)
        results = run_experiments(args.date, data_root=args.data_root,
                                  out_root=args.out_root, write=not args.no_write)
        if not results:
            print(f"RESEARCH-EXPERIMENTS {args.date}: registry empty")
        for record in results.values():
            print(_format_line(record))
    except Exception as exc:  # never-raises: this rides the grader cron log
        try:
            print(f"RESEARCH-EXPERIMENTS error (swallowed, exit 0): {exc!r}")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    # `python -m src.research.experiment` runs this file as the `__main__` module,
    # which is a DIFFERENT module instance from `src.research.experiment` — and the
    # builtin experiments register into the latter. Delegate to the canonical
    # instance so REGISTRY is the one the @register decorators populated.
    try:
        from src.research.experiment import main as _canonical_main
        sys.exit(_canonical_main())
    except Exception as exc:  # same never-raises contract as main()
        print(f"RESEARCH-EXPERIMENTS error (swallowed, exit 0): {exc!r}")
        sys.exit(0)
