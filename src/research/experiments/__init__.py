"""doc 272 C2 — builtin research experiments. Importing this package registers them.

Each builtin import is individually guarded: one broken experiment module must
not prevent the rest of the registry from loading.
"""
from __future__ import annotations

try:
    from src.research.experiments import ledger_journal_drift  # noqa: F401
except Exception:
    pass

try:
    from src.research.experiments import knowability_frontier  # noqa: F401  (doc 273)
except Exception:
    pass

try:
    from src.research.experiments import engine_immune_system  # noqa: F401  (doc 274)
except Exception:
    pass
