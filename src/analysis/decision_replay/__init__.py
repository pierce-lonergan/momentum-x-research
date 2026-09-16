"""Decision-replay infrastructure (Tier 4 #15, Bug AO 2026-04-27).

The MISSING architectural layer for "rigorous answer to does this make money."

Per `feedback_arena_assessment.md`: "Decision replay covers 1/50 signal types."
This module closes that gap: capture every orchestrator verdict + replay
through alternate rule sets to compute counterfactual outcomes.

Modules:
  - schemas.py (in src/analysis/instrumentation/) — DecisionRow Pydantic
  - log_parser.py — extract DecisionRow from production session logs
  - rules.py — pluggable rule library (D124 v1/v2, MFCS thresholds, etc.)
  - replay.py — apply alternate rules to a captured DecisionRow
  - counterfactual.py — aggregate replay outputs into a verdict-flip + ΔP&L report
  - cli.py — operator entry point: "given corpus, what would change X do?"

Validation case: Bug AK (D124 three-tier) predicts ~47% reduction in
D124 rejections from today's session. The decision_replay tool validates
this prediction quantitatively against today's actual log data.
"""
