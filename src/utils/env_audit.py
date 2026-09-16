"""
D221 Phase F (Mon 2026-04-20): startup env-toggle audit.

Single source of truth for the env vars the trading session depends on.
Called once at session startup (post-dotenv-load) to:
  1. Log a human-readable audit line per var (status + length, never value)
  2. Return a compact dict suitable for heartbeat-file embedding

The audit categorizes every tracked var into one of three buckets:

  CRITICAL              — session cannot run safely without these (API keys).
                          UNSET should scream; eventually a preflight abort
                          (not in this commit; behavior change deferred).
  FEATURE_GATED         — session runs fine but features are dormant if
                          UNSET. UNSET is operator notice, not failure.
  SAFETY_KILL_SWITCHES  — default behavior is correct (usually default-on);
                          UNSET means the safe default is in effect.

Why this exists: three silent-activation failures in 48 hours
(Path B 36h dormancy; Tier 3 dormant Sun-night to Mon-morning;
ALPACA_API_KEY exposure during the audit itself). The pattern is uniform:
no observability into which env vars actually loaded means we discover
gaps via incident, not via startup log. This module makes the activation
state observable from session second 1.

Length hints catch the class of bug where a var is set to an empty
string, whitespace, or a truncated paste — all "<SET>"-ish but useless
downstream. Length surfaces those without echoing the value.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── Specs ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EnvVarSpec:
    """Describes one tracked env var. Frozen so the registry is immutable."""
    name: str
    category: str  # CRITICAL | FEATURE_GATED | SAFETY_KILL_SWITCHES
    description: str
    default_safe_unset: bool = True  # CRITICAL vars set this False


# ── Registry ─────────────────────────────────────────────────────────


# CRITICAL: session cannot run safely if these are unset.
_CRITICAL: tuple[EnvVarSpec, ...] = (
    EnvVarSpec("ALPACA_API_KEY", "CRITICAL",
               "Alpaca brokerage API key (paper or live)",
               default_safe_unset=False),
    EnvVarSpec("ALPACA_SECRET_KEY", "CRITICAL",
               "Alpaca brokerage API secret",
               default_safe_unset=False),
    EnvVarSpec("FINNHUB_API_KEY", "CRITICAL",
               "Finnhub API key (earnings calendar, news enrichment)",
               default_safe_unset=False),
    EnvVarSpec("TOGETHER_AI_API_KEY", "CRITICAL",
               "Together.ai LLM API key (news/fundamental/institutional/deep_search)",
               default_safe_unset=False),
)

# FEATURE_GATED: session runs fine but the feature is dormant if unset.
_FEATURE_GATED: tuple[EnvVarSpec, ...] = (
    EnvVarSpec("MOMENTUM_PERSIST_LIVE_FEATURES", "FEATURE_GATED",
               "Path B forward-only persistence for short_interest et al."),
    EnvVarSpec("DECISION_LEARNING_WEBHOOK_URL", "FEATURE_GATED",
               "Tier 3 enriched Discord channel"),
    EnvVarSpec("DISCORD_DISAGREE_THRESHOLD", "FEATURE_GATED",
               "DISAGREE_SHADOW_BUYS surface threshold (default 0.55)"),
    EnvVarSpec("OPS_ALERT_WEBHOOK_URL", "FEATURE_GATED",
               "Discord critical-alerts channel"),
    EnvVarSpec("OPS_WATCHLIST_WEBHOOK_URL", "FEATURE_GATED",
               "Discord watchlist/legacy-trade-alerts channel"),
    EnvVarSpec("HEARTBEAT_WEBHOOK_URL", "FEATURE_GATED",
               "Discord heartbeat channel"),
    EnvVarSpec("SESSION_NOTIFICATION_URL", "FEATURE_GATED",
               "Post-session JSON summary endpoint"),
    EnvVarSpec("MOMENTUM_HEALTH_TOKEN", "FEATURE_GATED",
               "Health-server auth token (optional)"),
)

# SAFETY_KILL_SWITCHES: default-on; UNSET means safe default is in effect.
_SAFETY_KILL_SWITCHES: tuple[EnvVarSpec, ...] = (
    EnvVarSpec("SHADOW_SCORING_ENABLED", "SAFETY_KILL_SWITCHES",
               "Composite shadow telemetry (default ON)"),
    EnvVarSpec("SHADOW_INVERTED_ENABLED", "SAFETY_KILL_SWITCHES",
               "Inverted-strategy shadow telemetry (default ON)"),
    EnvVarSpec("MOMENTUM_HALT_NEW_ENTRIES", "SAFETY_KILL_SWITCHES",
               "D277 halt switch: when set to 1, blocks all new OTO order "
               "submissions. Re-read per submit_oto_order call (cached at "
               "process start; restart required to lift)."),
    EnvVarSpec("MOMENTUM_EXIT_POLICY", "SAFETY_KILL_SWITCHES",
               "D278 EXIT_POLICY: which intraday-exit rules fire. 'bar1_legacy' "
               "(default) keeps BAR-1 EXIT (T+60s) + D101 §3.5 TIME_EXIT "
               "(T+20min) active; 't1_next_open' gates both off so positions "
               "carry overnight."),
)

_ALL_SPECS: tuple[EnvVarSpec, ...] = (
    *_CRITICAL, *_FEATURE_GATED, *_SAFETY_KILL_SWITCHES,
)


# ── Public API ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class AuditEntry:
    """One row of the audit. status is the displayable token; length is
    the raw byte length of the value (0 if UNSET/EMPTY). Never carries
    the value itself."""
    name: str
    category: str
    status: str  # <SET> | <UNSET> | <EMPTY> | <WHITESPACE>
    length: int  # 0 unless <SET>
    description: str
    default_safe_unset: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "category": self.category,
            "status": self.status, "length": self.length,
            "description": self.description,
            "default_safe_unset": self.default_safe_unset,
        }


def _classify_value(raw: str | None) -> tuple[str, int]:
    """Map raw env value -> (status, length).

    None or missing -> <UNSET>, length 0
    Empty string    -> <EMPTY>, length 0
    Whitespace-only -> <WHITESPACE>, length len(raw)
    Anything else   -> <SET>, length len(raw)

    Length is RAW byte length (no stripping), so a "set but with leading
    whitespace" gets caught: status=<SET> but the operator can compare
    length vs expected to spot it.
    """
    if raw is None:
        return ("<UNSET>", 0)
    if raw == "":
        return ("<EMPTY>", 0)
    if not raw.strip():
        return ("<WHITESPACE>", len(raw))
    return ("<SET>", len(raw))


def collect_env_audit(specs: tuple[EnvVarSpec, ...] = _ALL_SPECS) -> list[AuditEntry]:
    """Return the audit as a list of AuditEntry, ordered by spec
    declaration (CRITICAL first, then FEATURE_GATED, then SAFETY_*).

    Pure function: reads os.environ once per spec, never raises."""
    out: list[AuditEntry] = []
    for spec in specs:
        raw = os.environ.get(spec.name)
        status, length = _classify_value(raw)
        out.append(AuditEntry(
            name=spec.name, category=spec.category,
            status=status, length=length,
            description=spec.description,
            default_safe_unset=spec.default_safe_unset,
        ))
    return out


def log_env_audit(target_logger: logging.Logger | None = None) -> list[AuditEntry]:
    """Log the audit at INFO level. Returns the audit list for
    embedding into heartbeat / further inspection.

    Output format (one line per var):
      ENV_AUDIT[CATEGORY] NAME=<STATUS:LENGTH>  (description)

    If a CRITICAL var is UNSET, the line is logged at WARNING (still
    not aborting -- behavior change deferred per the deliberate scope
    constraint -- but visually distinguishable in the log).
    """
    target_logger = target_logger or logger
    audit = collect_env_audit()
    n_critical_unset = 0
    for entry in audit:
        line = (
            f"ENV_AUDIT[{entry.category}] {entry.name}={entry.status}:{entry.length}  "
            f"({entry.description})"
        )
        if entry.category == "CRITICAL" and entry.status != "<SET>":
            target_logger.warning(line + "  <-- CRITICAL UNSET; session may fail")
            n_critical_unset += 1
        else:
            target_logger.info(line)
    if n_critical_unset:
        target_logger.warning(
            "ENV_AUDIT summary: %d CRITICAL var(s) unset. "
            "Preflight abort behavior is NOT yet enabled per Phase F scope; "
            "watch for downstream API failures.",
            n_critical_unset,
        )
    else:
        target_logger.info(
            "ENV_AUDIT summary: %d vars audited, all CRITICAL set",
            len(audit),
        )
    return audit


def format_for_heartbeat(audit: list[AuditEntry] | None = None) -> dict[str, Any]:
    """Compact dict for embedding into the heartbeat JSON.

    Shape:
      {
        "captured_at": ISO8601,
        "n_total": int,
        "n_critical_unset": int,
        "by_category": {
          "CRITICAL": {"NAME1": {"status": "<SET>", "length": 20}, ...},
          "FEATURE_GATED": {...},
          "SAFETY_KILL_SWITCHES": {...},
        }
      }

    Designed to add a few hundred bytes to the heartbeat, not multiple KB.
    Caller can persist the same dict every heartbeat write -- the audit
    is captured once and reused.
    """
    from datetime import datetime, timezone
    audit = audit if audit is not None else collect_env_audit()
    by_cat: dict[str, dict[str, dict[str, Any]]] = {}
    n_critical_unset = 0
    for entry in audit:
        by_cat.setdefault(entry.category, {})
        by_cat[entry.category][entry.name] = {
            "status": entry.status, "length": entry.length,
        }
        if entry.category == "CRITICAL" and entry.status != "<SET>":
            n_critical_unset += 1
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "n_total": len(audit),
        "n_critical_unset": n_critical_unset,
        "by_category": by_cat,
    }
