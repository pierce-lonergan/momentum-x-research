"""FailureInjector — controlled broker-error injection for arena replays.

Block 2.2 of the arena↔prod parity program. See
docs/research-log/60_failure_injector.md.

Production fails in specific shapes that arena's SimExchange does not
naturally model:
  - HTTP 403 with Alpaca code 40310000 ("insufficient qty available")
    when a competing protective stop reserves the position's shares.
    Today's LIDR 10:00:38 ET deadlock that motivated Bug AI / Bug AR.
  - HTTP 403 for trading-account locked / market closed / etc.
  - HTTP 422 for validation failures (qty=0, bad prices, etc.).
  - HTTP 5xx transient broker outages.

Without injecting these in arena, Bug AR / Bug AI / Bug Z code paths
are untested in simulation — exactly the gap that let Bug AR ship as
dead code for 24 hours in production.

Design:
  - Rule-based: each rule binds a TRIGGER (ticker, endpoint, action,
    optional time window) to a RESPONSE (status code, JSON body).
  - Deterministic: order of rule registration is order of evaluation.
    First match wins. Empty rule list = no injection.
  - Composable: rules are dataclasses, easy to seed from fixtures or
    JSON files for parameterized tests.
  - Honest API: returns either an injected response (caller raises) or
    None (caller proceeds normally). NO silent bypass — explicit
    `evaluate()` call site.

Seed fixture: the LIDR 2026-04-28 10:00:38 ET 403 body, byte-for-byte,
reproduces the AR scenario for regression tests.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)


# ── Rule + response data classes ────────────────────────────────────


@dataclass(frozen=True)
class InjectedFailure:
    """A simulated broker error response."""
    status_code: int
    body: str               # JSON string, opaque to the injector
    reason: str = ""        # Human-readable, for logs/debugging

    @property
    def body_json(self) -> dict:
        try:
            return json.loads(self.body)
        except (json.JSONDecodeError, TypeError):
            return {}


@dataclass
class FailureRule:
    """One trigger → response mapping.

    Triggers (None = wildcard / always match):
      - ticker: symbol must equal this (case-insensitive)
      - action: must equal this (e.g., "close_position", "submit_order")
      - after_iso / before_iso: ISO-format UTC timestamps; trigger
        only fires INSIDE the [after, before] window (inclusive).
      - max_fires: stop firing after this many matches (None = unlimited)
    """
    response: InjectedFailure
    ticker: Optional[str] = None
    action: Optional[str] = None
    after_iso: Optional[str] = None
    before_iso: Optional[str] = None
    max_fires: Optional[int] = None
    fires: int = 0

    def matches(
        self, *, ticker: str, action: str, ts_utc: datetime,
    ) -> bool:
        if self.ticker is not None and ticker.upper() != self.ticker.upper():
            return False
        if self.action is not None and action != self.action:
            return False
        if self.after_iso is not None:
            try:
                after = datetime.fromisoformat(self.after_iso.replace("Z", "+00:00"))
                if ts_utc < after:
                    return False
            except ValueError:
                return False
        if self.before_iso is not None:
            try:
                before = datetime.fromisoformat(self.before_iso.replace("Z", "+00:00"))
                if ts_utc > before:
                    return False
            except ValueError:
                return False
        if self.max_fires is not None and self.fires >= self.max_fires:
            return False
        return True


# ── Injector ────────────────────────────────────────────────────────


class FailureInjector:
    """Holds a list of rules; queried by the arena exchange before
    each action that could fail at the broker."""

    def __init__(self, rules: Optional[Iterable[FailureRule]] = None) -> None:
        self._rules: list[FailureRule] = list(rules) if rules else []

    def add_rule(self, rule: FailureRule) -> None:
        self._rules.append(rule)

    def clear(self) -> None:
        self._rules.clear()

    @property
    def rules(self) -> list[FailureRule]:
        return list(self._rules)

    def evaluate(
        self,
        *,
        ticker: str,
        action: str,
        ts_utc: datetime,
    ) -> Optional[InjectedFailure]:
        """Return an injected failure if any rule matches; else None.

        First-match-wins ordering by registration. Successful matches
        increment the rule's `fires` counter (used by max_fires gating).
        """
        for rule in self._rules:
            if rule.matches(ticker=ticker, action=action, ts_utc=ts_utc):
                rule.fires += 1
                logger.debug(
                    "FailureInjector fired: ticker=%s action=%s status=%d (rule fire #%d)",
                    ticker, action, rule.response.status_code, rule.fires,
                )
                return rule.response
        return None


# ── Fixture: today's LIDR Bug AR scenario ──────────────────────────


# Reconstructed from the production transcript at 2026-04-28T14:00:38Z
# (10:00:38 ET). The actual Alpaca body is in `e.response.text` per the
# Bug AR finding (docs/research-log/56_bug_ar_alpaca_body_extraction.md).
LIDR_TUE_403_BODY = (
    '{"available":"0","code":40310000,"existing_qty":"5264",'
    '"held_for_orders":"5264","message":"insufficient qty available '
    'for order (requested: 5264, available: 0)","symbol":"LIDR"}'
)


def lidr_tue_2026_04_28_qty_conflict() -> FailureRule:
    """The production LIDR Bug AR fixture as a one-shot rule.

    Wire this into a FailureInjector, then run a close on LIDR within
    the time window — arena will return the same shape 403 prod did.
    Bug AI / Bug AR cancel-and-coordinate logic should fire in
    response.

    Used by tests/unit/test_failure_injector.py as the canonical
    regression fixture.
    """
    return FailureRule(
        response=InjectedFailure(
            status_code=403,
            body=LIDR_TUE_403_BODY,
            reason="LIDR Tue 10:00:38 ET qty conflict (Bug AR fixture)",
        ),
        ticker="LIDR",
        action="close_position",
        # Allow 60s window around the actual incident
        after_iso="2026-04-28T14:00:00Z",
        before_iso="2026-04-28T14:01:30Z",
        max_fires=1,  # Production saw it once
    )
