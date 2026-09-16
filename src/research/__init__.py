"""doc 272 C2 — concurrent-experiment research substrate (DORMANT-C).

Read-only over the durable streams production already writes. NEVER imported by
production code paths; consumed only by the nightly offline runner
(`python -m src.research.experiment`) and tests. Keep this package free of
import-time side effects.
"""
