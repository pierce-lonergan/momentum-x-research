"""Static analysis tests — catch process bug classes (D217-D219).

This package contains AST-driven and regex-based tests that scan production
source code for recurring bug patterns. Tests use a per-file baseline
(`_baselines.json`) to allow existing tech debt to coexist while blocking
NEW instances of each bug class.

See `_ast_helpers.py` for the shared infrastructure and the plan at
`.claude/plans/elegant-stirring-penguin.md` for design rationale.
"""
