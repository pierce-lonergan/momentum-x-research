"""Differential testing infrastructure.

Per `25_bug_hunting_playbook.md` §4 — Csmith-pattern testing for trading
systems: compare two code variants against the same input sequence,
report any divergence beyond a documented allowlist.

The canonical use case: run the pre-patch and post-patch versions of a
critical-path module against the same recorded session; assert they
produce identical outputs except where the patch is documented to
change behaviour. Catches Bug-N-class regressions (a patch changes a
method call site that the patch description didn't mention).
"""
from src.testing.differential_harness import (
    DifferentialHarness,
    DivergenceReport,
    OperationCall,
    OperationRecord,
    ReplayInput,
    DifferentialHarnessError,
)

__all__ = [
    "DifferentialHarness",
    "DivergenceReport",
    "OperationCall",
    "OperationRecord",
    "ReplayInput",
    "DifferentialHarnessError",
]
