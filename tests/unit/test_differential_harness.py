"""Differential harness tests — proves the harness correctly identifies
divergence between two code variants AND respects the allowlist.

10 tests across 4 categories:
  Equivalence detection:    same code → no divergence (3 tests)
  Divergence detection:     different code → reported (3 tests)
  Allowlist semantics:      documented divergence → tolerated (2 tests)
  Edge cases:               exceptions, FP equality, length mismatch (2 tests)
"""
from __future__ import annotations

import pytest

from src.testing import (
    DifferentialHarness,
    DifferentialHarnessError,
    OperationCall,
    ReplayInput,
)
from src.testing.differential_harness import approx_floats


# ── Helpers ─────────────────────────────────────────────────────


def _make_input(*calls) -> ReplayInput:
    """Construct a ReplayInput from raw (name, args, kwargs) tuples or OperationCall objects."""
    ops = []
    for c in calls:
        if isinstance(c, OperationCall):
            ops.append(c)
        else:
            name, args, kwargs = c
            ops.append(OperationCall.of(name, *args, **kwargs))
    return ReplayInput.from_iterable(ops)


def _identity_variant(op_name: str, *args, **kwargs):
    """Reference variant — returns args[0] for any op."""
    if not args:
        return None
    return args[0]


# ── 1. Equivalence detection ────────────────────────────────────


class TestEquivalenceDetection:

    def test_identical_variants_produce_zero_divergence(self) -> None:
        """Two pointers to the same callable → zero divergence."""
        h = DifferentialHarness(_identity_variant, _identity_variant)
        report = h.assert_equivalent(_make_input(("get", (5,), {}), ("get", (10,), {})))
        assert report.passed
        assert len(report.divergences) == 0
        assert report.total_ops == 2

    def test_semantically_equivalent_variants_pass(self) -> None:
        """Two distinct functions with the same input → same output → no divergence."""
        def variant_a(name: str, x: int) -> int:
            return x * 2
        def variant_b(name: str, x: int) -> int:
            return x + x  # same result, different mechanism
        h = DifferentialHarness(variant_a, variant_b)
        report = h.assert_equivalent(_make_input(
            ("double", (3,), {}), ("double", (10,), {}),
        ))
        assert report.passed

    def test_zero_operations_passes_trivially(self) -> None:
        h = DifferentialHarness(_identity_variant, _identity_variant)
        report = h.assert_equivalent(ReplayInput.from_iterable([]))
        assert report.total_ops == 0
        assert report.passed


# ── 2. Divergence detection ─────────────────────────────────────


class TestDivergenceDetection:

    def test_different_returns_flagged_with_op_index(self) -> None:
        def variant_a(name: str, x: int) -> int:
            return x * 2
        def variant_b(name: str, x: int) -> int:
            return x * 3  # off by one mechanism
        h = DifferentialHarness(variant_a, variant_b)
        with pytest.raises(DifferentialHarnessError) as exc_info:
            h.assert_equivalent(_make_input(("scale", (5,), {})))
        report = exc_info.value.report
        assert len(report.out_of_allowlist) == 1
        d = report.out_of_allowlist[0]
        assert d.op_index == 0
        assert d.operation.name == "scale"
        assert "a=10" in d.reason and "b=15" in d.reason

    def test_one_raises_other_returns_is_divergence(self) -> None:
        """The Bug-N regression: post-patch crashes, pre-patch returns."""
        def variant_a(name: str, x: int) -> int:
            return x  # works
        def variant_b(name: str, x: int) -> int:
            raise AttributeError("get_account_activities")  # patch broke this
        h = DifferentialHarness(variant_a, variant_b)
        with pytest.raises(DifferentialHarnessError) as exc_info:
            h.assert_equivalent(_make_input(("call_method", (1,), {})))
        report = exc_info.value.report
        assert len(report.out_of_allowlist) == 1
        assert "raise mismatch" in report.out_of_allowlist[0].reason

    def test_different_exception_classes_is_divergence(self) -> None:
        """Both raise but different exception types → divergence."""
        def variant_a(name: str, x: int):
            raise ValueError("bad value")
        def variant_b(name: str, x: int):
            raise TypeError("bad type")
        h = DifferentialHarness(variant_a, variant_b)
        report = h.compare(*h.replay(_make_input(("err", (1,), {}))))
        assert len(report.divergences) == 1
        assert "different exception" in report.divergences[0].reason


# ── 3. Allowlist semantics ──────────────────────────────────────


class TestAllowlistSemantics:

    def test_allowlisted_op_divergence_does_not_fail(self) -> None:
        """Documented intentional divergence → report says passed=True."""
        def variant_a(name: str, x: int) -> int:
            return x
        def variant_b(name: str, x: int) -> int:
            return x + 1  # the documented patch surface
        h = DifferentialHarness(variant_a, variant_b, allowlist={"refactored_op"})
        report = h.assert_equivalent(_make_input(("refactored_op", (5,), {})))
        # Divergence WAS recorded but didn't fail the assertion
        assert len(report.divergences) == 1
        assert len(report.allowlisted) == 1
        assert len(report.out_of_allowlist) == 0
        assert report.passed

    def test_mixed_allowlist_only_blocks_unlisted(self) -> None:
        """Replay with both allowlisted AND non-allowlisted divergences;
        only the unlisted one fails."""
        def variant_a(name: str, x: int) -> int:
            return x
        def variant_b(name: str, x: int) -> int:
            return x + 1
        h = DifferentialHarness(variant_a, variant_b, allowlist={"intentional"})
        with pytest.raises(DifferentialHarnessError) as exc_info:
            h.assert_equivalent(_make_input(
                ("intentional", (5,), {}),    # allowed to differ
                ("regression", (10,), {}),    # NOT allowed to differ
            ))
        report = exc_info.value.report
        assert len(report.divergences) == 2
        assert len(report.allowlisted) == 1
        assert len(report.out_of_allowlist) == 1
        assert report.out_of_allowlist[0].operation.name == "regression"


# ── 4. Edge cases ───────────────────────────────────────────────


class TestEdgeCases:

    def test_approx_floats_tolerates_tiny_drift(self) -> None:
        """Non-deterministic FP order in numpy → tiny drift → still equivalent."""
        def variant_a(name: str) -> float:
            return 0.1 + 0.2          # = 0.30000000000000004
        def variant_b(name: str) -> float:
            return 0.3
        # With default exact equality → divergence
        h_exact = DifferentialHarness(variant_a, variant_b)
        with pytest.raises(DifferentialHarnessError):
            h_exact.assert_equivalent(_make_input(("sum", (), {})))
        # With approx_floats → no divergence
        h_approx = DifferentialHarness(variant_a, variant_b, equality_fn=approx_floats(rtol=1e-9))
        report = h_approx.assert_equivalent(_make_input(("sum", (), {})))
        assert report.passed

    def test_compare_raises_on_length_mismatch(self) -> None:
        """If recordings have different lengths the harness has been misused."""
        h = DifferentialHarness(_identity_variant, _identity_variant)
        records_a, _ = h.replay(_make_input(("op", (1,), {})))
        records_b, _ = h.replay(_make_input(
            ("op", (1,), {}), ("op", (2,), {}),
        ))
        with pytest.raises(ValueError, match="length mismatch"):
            h.compare(records_a, records_b)
