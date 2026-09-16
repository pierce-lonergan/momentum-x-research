"""DifferentialHarness — Csmith-pattern differential testing for trading code.

Per `docs/research-log/25_bug_hunting_playbook.md` §4 + the user's items 27-28
(2026-04-25 next-actions list).

Design principle: replay the same input sequence through TWO code variants
(typically pre-patch and post-patch versions of the same module), record
each operation's result + exception, and assert the two recordings are
identical except at allowlisted call sites the patch documents as
intentionally changed.

Catches the **Bug-N-class** regression: a patch changes a method call
site (e.g., switches from `get_account_activities` to `get_orders`)
that the patch description didn't enumerate. Without differential
testing, the change ships silently and only surfaces when the missing
method gets called in production.

## Architecture

  ReplayInput  → ordered list of OperationCall(name, args, kwargs)
  variant_a    → callable that takes (operation_name, *args, **kwargs)
  variant_b    → callable with the same signature
  allowlist    → set of operation_names where divergence is expected

  DifferentialHarness(variant_a, variant_b, allowlist)
    .replay(input)         → (records_a, records_b)
    .compare(records_a, records_b) → DivergenceReport
    .assert_equivalent(input)      → raises DifferentialHarnessError on
                                     out-of-allowlist divergence

## Production wrapper (separate script)

`scripts/check_differential_diff.py` — git-aware wrapper:
  1. checkout pre-patch SHA, run replay against a recorded session
  2. checkout post-patch SHA, run replay against the same input
  3. compare via DifferentialHarness, fail if out-of-allowlist divergence

Wired into `.git/hooks/pre-push` for any patch touching:
  bridge.py, alpaca_executor.py, trade_journal.py, main.py

## Equivalence semantics

Two operation results are equivalent iff:
  - both raised the same exception class with the same str representation, OR
  - both returned a value AND `repr(a) == repr(b)`

Floating-point comparison uses an explicit `equality_fn` parameter
(default: exact equality). For trade-replay use cases pass
`equality_fn=approx_floats(rtol=1e-9)` to tolerate tiny FP drift from
non-deterministic operation order in the underlying numpy / pandas calls.

## Read this before extending

This harness deliberately does NOT execute the variants in subprocess
isolation. Each variant must be a pure callable that accepts (op_name,
args, kwargs) and returns a value or raises. For real pre-patch /
post-patch comparison the wrapper script handles the git checkout +
process boundary; the harness itself stays pure-Python and unit-testable.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)


# ── Data classes ────────────────────────────────────────────────────


@dataclass(frozen=True)
class OperationCall:
    """One call to a code-under-test surface.

    Attributes:
      name:   The operation name. Must match what each variant accepts.
      args:   Positional arguments. Tuple for hashability.
      kwargs: Keyword arguments. Frozen via tuple of sorted items.
    """
    name: str
    args: tuple = field(default_factory=tuple)
    kwargs: tuple = field(default_factory=tuple)  # sorted ((k, v), ...)

    @classmethod
    def of(cls, name: str, *args: Any, **kwargs: Any) -> "OperationCall":
        """Constructor accepting *args/**kwargs ergonomics."""
        return cls(name=name, args=tuple(args), kwargs=tuple(sorted(kwargs.items())))

    def to_kwargs(self) -> dict:
        return dict(self.kwargs)


@dataclass(frozen=True)
class OperationRecord:
    """One operation's result from one variant.

    Exactly one of `result` or `exception` is set.
    """
    call: OperationCall
    result: Any = None
    exception: tuple[str, str] | None = None  # (exception_class_name, str(exc))

    @property
    def raised(self) -> bool:
        return self.exception is not None


@dataclass(frozen=True)
class ReplayInput:
    """An ordered sequence of operations to replay through both variants."""
    operations: tuple[OperationCall, ...]

    @classmethod
    def from_iterable(cls, ops: Iterable[OperationCall]) -> "ReplayInput":
        return cls(operations=tuple(ops))


@dataclass(frozen=True)
class Divergence:
    """A single divergence between variant_a and variant_b on one operation."""
    op_index: int
    operation: OperationCall
    record_a: OperationRecord
    record_b: OperationRecord
    reason: str


@dataclass(frozen=True)
class DivergenceReport:
    """The full diff between two replay recordings.

    Attributes:
      divergences:           ALL divergences seen during replay
      allowlisted:           Subset that match the allowlist
      out_of_allowlist:      Subset that DO NOT match the allowlist (the failures)
      total_ops:             Total operations replayed
    """
    divergences: tuple[Divergence, ...]
    allowlisted: tuple[Divergence, ...]
    out_of_allowlist: tuple[Divergence, ...]
    total_ops: int

    @property
    def passed(self) -> bool:
        return len(self.out_of_allowlist) == 0


# ── Errors ──────────────────────────────────────────────────────────


class DifferentialHarnessError(AssertionError):
    """Raised by `assert_equivalent` when out-of-allowlist divergence detected."""

    def __init__(self, report: DivergenceReport) -> None:
        self.report = report
        msg_lines = [
            f"DifferentialHarness: {len(report.out_of_allowlist)} out-of-allowlist "
            f"divergence(s) across {report.total_ops} operations.",
        ]
        for d in report.out_of_allowlist[:5]:
            msg_lines.append(
                f"  op[{d.op_index}] {d.operation.name}: {d.reason}",
            )
        if len(report.out_of_allowlist) > 5:
            msg_lines.append(f"  ... and {len(report.out_of_allowlist) - 5} more")
        super().__init__("\n".join(msg_lines))


# ── Harness ─────────────────────────────────────────────────────────


VariantCallable = Callable[..., Any]
"""A variant is a callable taking (op_name: str, *args, **kwargs) and
returning the operation's result (or raising)."""


def approx_floats(rtol: float = 1e-9, atol: float = 1e-12) -> Callable[[Any, Any], bool]:
    """Return an equality function that compares floats with tolerance.

    Non-float values fall back to `repr(a) == repr(b)`.
    """
    def _eq(a: Any, b: Any) -> bool:
        if isinstance(a, float) and isinstance(b, float):
            return abs(a - b) <= max(atol, rtol * max(abs(a), abs(b)))
        return repr(a) == repr(b)
    return _eq


class DifferentialHarness:
    """Replay the same input sequence through two variants; report divergence.

    Args:
      variant_a:   Callable signature: (op_name, *args, **kwargs) -> result
      variant_b:   Same signature.
      allowlist:   Set of op_names where divergence is expected (and tolerated).
                   Each entry is the operation name as stored on OperationCall.name.
                   Empty allowlist == strict equivalence.
      equality_fn: Function (a, b) -> bool used to compare non-exception results.
                   Default: `repr(a) == repr(b)`. Pass `approx_floats(...)` for
                   FP-tolerant comparison.
    """

    def __init__(
        self,
        variant_a: VariantCallable,
        variant_b: VariantCallable,
        allowlist: set[str] | None = None,
        equality_fn: Callable[[Any, Any], bool] | None = None,
    ) -> None:
        self.variant_a = variant_a
        self.variant_b = variant_b
        self.allowlist: set[str] = set(allowlist or ())
        self.equality_fn: Callable[[Any, Any], bool] = (
            equality_fn or (lambda a, b: repr(a) == repr(b))
        )

    # ── Replay ─────────────────────────────────────────────────

    def _run_one(self, variant: VariantCallable, op: OperationCall) -> OperationRecord:
        try:
            result = variant(op.name, *op.args, **op.to_kwargs())
            return OperationRecord(call=op, result=result, exception=None)
        except BaseException as e:
            return OperationRecord(
                call=op, result=None,
                exception=(type(e).__name__, str(e)),
            )

    def replay(
        self, replay_input: ReplayInput,
    ) -> tuple[tuple[OperationRecord, ...], tuple[OperationRecord, ...]]:
        """Replay the input through both variants. Returns (records_a, records_b)."""
        records_a: list[OperationRecord] = []
        records_b: list[OperationRecord] = []
        for op in replay_input.operations:
            records_a.append(self._run_one(self.variant_a, op))
            records_b.append(self._run_one(self.variant_b, op))
        return tuple(records_a), tuple(records_b)

    # ── Compare ────────────────────────────────────────────────

    def compare(
        self,
        records_a: tuple[OperationRecord, ...],
        records_b: tuple[OperationRecord, ...],
    ) -> DivergenceReport:
        """Build a DivergenceReport from paired recordings."""
        if len(records_a) != len(records_b):
            raise ValueError(
                f"Recording length mismatch: a={len(records_a)} b={len(records_b)}"
            )
        divergences: list[Divergence] = []
        for i, (ra, rb) in enumerate(zip(records_a, records_b)):
            if ra.call != rb.call:
                divergences.append(Divergence(
                    op_index=i, operation=ra.call,
                    record_a=ra, record_b=rb,
                    reason=f"call mismatch (a={ra.call} b={rb.call})",
                ))
                continue
            d = self._diff_records(i, ra, rb)
            if d is not None:
                divergences.append(d)

        allowlisted = tuple(d for d in divergences if d.operation.name in self.allowlist)
        out_of_allowlist = tuple(d for d in divergences if d.operation.name not in self.allowlist)
        return DivergenceReport(
            divergences=tuple(divergences),
            allowlisted=allowlisted,
            out_of_allowlist=out_of_allowlist,
            total_ops=len(records_a),
        )

    def _diff_records(
        self, i: int, ra: OperationRecord, rb: OperationRecord,
    ) -> Divergence | None:
        # Both raised
        if ra.raised and rb.raised:
            if ra.exception != rb.exception:
                return Divergence(
                    op_index=i, operation=ra.call,
                    record_a=ra, record_b=rb,
                    reason=f"different exception (a={ra.exception} b={rb.exception})",
                )
            return None  # same exception → equivalent
        # One raised, the other didn't
        if ra.raised != rb.raised:
            return Divergence(
                op_index=i, operation=ra.call,
                record_a=ra, record_b=rb,
                reason=f"raise mismatch (a.raised={ra.raised} b.raised={rb.raised})",
            )
        # Neither raised → compare results
        if not self.equality_fn(ra.result, rb.result):
            return Divergence(
                op_index=i, operation=ra.call,
                record_a=ra, record_b=rb,
                reason=f"result mismatch (a={ra.result!r} b={rb.result!r})",
            )
        return None

    # ── Convenience ────────────────────────────────────────────

    def assert_equivalent(self, replay_input: ReplayInput) -> DivergenceReport:
        """Run replay + compare; raise DifferentialHarnessError on out-of-allowlist."""
        records_a, records_b = self.replay(replay_input)
        report = self.compare(records_a, records_b)
        if not report.passed:
            raise DifferentialHarnessError(report)
        return report
