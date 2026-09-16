"""doc 272 A2 (the doc-270 'synthetic namespace constant' elevation).

Single source of truth for which incident rows are SYNTHETIC — produced by the
nightly Adversary battle (ticker "X"), pager self-tests (ticker "TEST"), or unit
fixtures (AAPL/TSLA) — and must NEVER page a human or redline a health check.

Import this everywhere a consumer filters incidents (incident_pager.py,
pipeline_health_check.py, future Operator). Do NOT re-declare local tuples;
the doc-269/270 census found drift between ad-hoc exclusion lists.
"""
from __future__ import annotations

# Tickers used exclusively by synthetic/battle/test emitters. A real position
# in one of these names will never exist in this bot's small-cap universe
# (AAPL/TSLA are mega-caps outside every scanner gate; X and TEST are markers).
SYNTHETIC_TICKERS: frozenset[str] = frozenset({"X", "TEST", "AAPL", "TSLA"})


def is_synthetic_incident(row: dict) -> bool:
    """True if an incident-bus row is synthetic and must not page.

    Checks (any one suffices):
      - ticker in SYNTHETIC_TICKERS
      - top-level ``synthetic: true`` field (direct-append emitters)
      - ``context.synthetic: true`` (emit_incident emitters)
    Never raises: malformed rows return False (fail-open to PAGE, because a
    mangled real CRITICAL must not be silently classified synthetic).
    """
    try:
        ticker = str(row.get("ticker") or "").upper()
        if ticker in SYNTHETIC_TICKERS:
            return True
        if row.get("synthetic") is True:
            return True
        ctx = row.get("context")
        if isinstance(ctx, dict) and ctx.get("synthetic") is True:
            return True
    except Exception:
        return False
    return False
