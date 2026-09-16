"""doc 272 LEVER-1 — EMPTY != BEAR gate classifier (FLAG-B, default OFF).

Env flag: ``MOMENTUM_EMPTY_NOT_BEAR`` (unset/falsy => this module is a pure
no-op: ``absence_fail_open()`` returns "" without inspecting anything, so the
D200-E4 / D204 block sites behave byte-identically to before doc 272).

WHY: the D200-E4 catalyst gate and the D204 news-confidence gates block on
data ABSENCE (catalyst_type=NONE / no BULL news signal). Absence is not
bearishness: the no-news micro-cap rockets (BYAH/NPT class, docs 268/270) are
exactly the cohort these gates ate. When the flag is ON, a gate-block is
classified before it fires:

  ABSENCE  -> fail-open: admit at reduced size via the doc-178 downgrade
              mechanism (verdict.qty_multiplier clamped to
              settings.universe.catalyst_gate_downgrade_qty_mult, default 0.5)
              and vll_emit("DOWNGRADED_ABSENCE", ...) instead of the BLOCKED
              emit. The call sites in main.py own that mechanism; this module
              only answers "is this absence?".
  BEARISH  -> block exactly as today (an actual BEAR/STRONG_BEAR news signal,
              any-agent STRONG_BEAR, or a risk-agent VETO).
  anything
  else     -> block exactly as today (weak-but-present data is NOT absence).

CONSERVATIVE CLASSIFICATION RULES (hostile-review notes, doc 272):
  1. news agent MISSING from the signal set (timed out / never returned) =
     ABSENCE — *only if* at least one other PARSEABLE agent signal exists
     (non-empty agent_id or signal direction). An entirely empty / garbage
     signal list is an infrastructure fault, not "no news": NOT absence
     (-> NO_SIGNALS -> block as today). Rationale: doc-178 already documented
     NCPL/AMSS blocked on news-agent TIMEOUT while the rest of the eval ran.
  2. news agent present with signal NEUTRAL and catalyst_type NONE-ish =
     ABSENCE — the news agent's prompt FORCES "NEUTRAL, NONE" when no
     company-specific catalyst exists, so this is the canonical no-news shape.
  3. news agent NEUTRAL but with a REAL catalyst_type (e.g. FDA_APPROVAL) =
     NOT absence — the agent saw real news and judged it not bullish. Block.
  4. news agent BULL/STRONG_BULL below the D204 confidence bar = NOT absence
     at the D204 sites (the agent saw the news and was unconvinced — that is
     data). At the D200-E4 site the gate trigger is catalyst_type=NONE, so a
     non-bearish news read there IS "catalyst NONE with no bearish signal"
     (pass ``catalyst_none_trigger=True``).
  5. BEARISH always wins: a news BEAR/STRONG_BEAR, a STRONG_BEAR from ANY
     agent, or a risk_verdict=VETO anywhere in the signal set forces BEARISH
     regardless of rules 1-4.

Failure philosophy: ``absence_fail_open`` NEVER raises — any internal error
returns "" (fail-closed to today's block behavior).
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

ENV_FLAG = "MOMENTUM_EMPTY_NOT_BEAR"

# Classifications
ABSENCE = "ABSENCE"
BEARISH = "BEARISH"
WEAK_DATA = "WEAK_DATA"
NO_SIGNALS = "NO_SIGNALS"

# catalyst_type values that mean "no catalyst" (mirrors the D200-E4 gate's
# own NONE-set at main.py ~3429).
_NONEISH_CATALYSTS = (None, "", "NONE", "None", "null", "unknown")

_TRUTHY = ("1", "true", "yes", "on")


def enabled() -> bool:
    """True iff env MOMENTUM_EMPTY_NOT_BEAR is set to a truthy value.

    Read at call time (not import time) so tests and ops can flip the flag
    without a process restart of the importing module.
    """
    return os.environ.get(ENV_FLAG, "").strip().lower() in _TRUTHY


def _field(sig: Any, name: str, default: Any = None) -> Any:
    """Read a field from a dict- or object-shaped agent signal."""
    if isinstance(sig, dict):
        return sig.get(name, default)
    return getattr(sig, name, default)


def classify_block(
    signals: Any,
    *,
    catalyst_none_trigger: bool = False,
) -> tuple[str, str]:
    """Classify a gate-block situation from the ticker's agent-signal set.

    Args:
        signals: list of AgentSignal objects and/or dicts (the
            ``orchestrator._signals_by_ticker`` entry for the ticker).
        catalyst_none_trigger: True at the D200-E4 site, where the gate
            trigger is catalyst_type=NONE — there, ANY non-bearish news state
            (including BULL) counts as "catalyst NONE with no bearish signal"
            = ABSENCE. False at the D204 sites, where only EMPTY/NEUTRAL-
            no-data counts (a weak BULL is data, not absence).

    Returns:
        (classification, detail) — classification in
        {ABSENCE, BEARISH, WEAK_DATA, NO_SIGNALS}; detail is a short
        log-friendly explanation.
    """
    if not isinstance(signals, list) or not signals:
        # Whole-eval data void: infrastructure fault, not "no news". Block.
        return NO_SIGNALS, "no agent signals at all (infra fault?)"

    news_found = False
    news_sig = ""
    news_conf = 0.0
    news_catalyst: Any = None
    parsed_any = False

    for s in signals:
        agent_id = str(_field(s, "agent_id", "") or "")
        direction = str(_field(s, "signal", "") or "")
        if agent_id or direction:
            parsed_any = True

        # Rule 5a: risk-agent VETO anywhere -> BEARISH (covers ADVISORY
        # veto mode, where a VETO signal can survive into a BUY verdict).
        if str(_field(s, "risk_verdict", "") or "") == "VETO":
            return BEARISH, f"risk_veto({agent_id or 'risk_agent'})"

        # Rule 5b: STRONG_BEAR from ANY agent -> BEARISH (conservative).
        if direction == "STRONG_BEAR":
            return BEARISH, f"strong_bear({agent_id or 'unknown_agent'})"

        if "news" in agent_id.lower():
            news_found = True
            news_sig = direction
            try:
                news_conf = float(_field(s, "confidence", 0.0) or 0.0)
            except (TypeError, ValueError):
                news_conf = 0.0
            news_catalyst = _field(s, "catalyst_type", None)
            # Rule 5c: explicit bearish news -> BEARISH.
            if direction in ("BEAR", "STRONG_BEAR"):
                return BEARISH, f"news=BEAR/{news_conf:.2f}"

    if not parsed_any:
        # Entries exist but none look like agent signals (no agent_id and no
        # direction on any of them). Treat as an infra fault: block as today.
        return NO_SIGNALS, "no parseable agent signals (infra fault?)"

    if not news_found:
        # Rule 1: eval ran (>=1 parseable signal) but the news read never
        # arrived (timeout / dispatch failure). The NCPL/AMSS shape.
        return ABSENCE, "news=EMPTY(timeout/missing)"

    if news_sig == "NEUTRAL":
        if news_catalyst in _NONEISH_CATALYSTS:
            # Rule 2: canonical no-news shape (prompt forces NEUTRAL/NONE).
            return ABSENCE, "news=NEUTRAL(no-catalyst)"
        # Rule 3: agent saw a real catalyst and judged it neutral. Data.
        return WEAK_DATA, f"news=NEUTRAL(catalyst={news_catalyst})"

    if catalyst_none_trigger:
        # Rule 4 (E4 site): non-bearish news + catalyst NONE trigger.
        return ABSENCE, f"news={news_sig}/{news_conf:.2f}(catalyst=NONE)"

    # Rule 4 (D204 sites): the agent produced a sub-threshold directional
    # read — that is data, not absence. Block as today.
    return WEAK_DATA, f"news={news_sig}/{news_conf:.2f}(below gate)"


def absence_fail_open(
    signals: Any,
    *,
    catalyst_none_trigger: bool = False,
) -> str:
    """Gate-site entry point. Returns a truthy detail string ONLY when the
    flag is ON and the block classifies as ABSENCE; otherwise "" (block as
    today). Never raises.

    The env check is FIRST: with MOMENTUM_EMPTY_NOT_BEAR unset, this returns
    "" before touching ``signals`` — the no-op-when-OFF guarantee.
    """
    try:
        if not enabled():
            return ""
        classification, detail = classify_block(
            signals, catalyst_none_trigger=catalyst_none_trigger,
        )
        if classification == ABSENCE:
            return detail
        return ""
    except Exception as exc:  # noqa: BLE001 — fail closed to legacy block
        try:
            logger.debug("d272 absence_fail_open swallowed: %s", str(exc)[:120])
        except Exception:
            pass
        return ""
