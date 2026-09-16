"""Bug AR tests: extract the Alpaca response body so Bug AI's
substring match can fire.

Pins today's (Tuesday 2026-04-28) finding as a regression guard:

  - Mon 2026-04-27 LIDR sell-down: 5+ failed close attempts. The
    operator-submitted protective stop reserved all 5264 shares;
    bridge close_position calls returned 403 with body containing
    "insufficient qty available" + code 40310000.

  - Bug AI shipped Mon 2026-04-27 evening: cancel-blocking-stops-then-
    close coordination, gated on substring detection in the error
    string.

  - Tue 2026-04-28 LIDR manual sell at 10:00:38 ET: another 403,
    same shape. D248 BLOCKING_STOPS log line was ABSENT from the
    transcript. Bug AI did not fire.

  - Phase 0 audit found the gap: production exception is httpx
    HTTPStatusError, whose `__str__` renders only:
      "Client error '403 Forbidden' for url 'https://...'"
    The body containing "insufficient qty available" / "40310000" /
    "available: 0" lives in `e.response.text` and was never read.
    The substring match in attempt_close_with_status_check therefore
    NEVER matched in production.

  - The existing Bug AI tests (test_bug_ai_force_close.py) used a
    custom `_Block403Error` whose `__str__` returned the body — so
    they passed while production code was dead. False green.

Bug AR fix (src/execution/bridge.py:attempt_close_with_status_check):
  - Read `e.response.text` (when present) and append it to err_str.
  - Match case-insensitively against the COMBINED string.
  - Add "qty available" as a tolerant fallback substring.

Aggressive testing per the discipline framework:
  1. Realistic-shape error (str(e) is short, body in .response.text)
     — old code path does NOT fire Bug AI; new code path DOES.
  2. Fixture replay: today's actual 403 body, byte-for-byte.
  3. Variant bodies: casing changes, key-order changes, missing keys,
     extra keys, alternative error code paths.
  4. Invariant: the substring match must remain robust to Alpaca
     casing drift (uppercase / lowercase / mixed).

See docs/research-log/56_bug_ar_alpaca_body_extraction.md (TBD) and
audit/2026-04-28-infrastructure-audit.md §0.3.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.execution.bridge import attempt_close_with_status_check


# ── Realistic httpx-shaped error doubles ─────────────────────────────


class _FakeHTTPXResponse:
    """Mimics httpx.Response.text — the body Alpaca returns inside a
    403. Production httpx.HTTPStatusError carries this on .response."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.status_code = 403


class _RealisticHTTPStatusError(Exception):
    """Mimics what httpx actually raises on a 403 from Alpaca:
    - str(e) renders the truncated 'Client error \\'403 Forbidden\\' for
      url ...' message — NO BODY.
    - e.response.text holds the JSON body with the qty diagnostics.

    This is the shape that production sees. The existing Bug AI tests
    used a doctored `__str__` that included the body, masking Bug AR.
    """

    def __init__(self, body: str, url: str = "https://paper-api.alpaca.markets/v2/positions/LIDR") -> None:
        super().__init__(
            f"Client error '403 Forbidden' for url '{url}'\n"
            f"For more information check: https://developer.mozilla.org/.../403"
        )
        self.response = _FakeHTTPXResponse(text=body)


# Today's actual LIDR 10:00:38 ET 403 body shape (reconstructed from
# transcript). Pin byte-for-byte so future Alpaca payload drift is
# surfaced as a test diff, not a silent regression.
LIDR_403_BODY_TUESDAY = (
    '{"available":"0","code":40310000,"existing_qty":"5264",'
    '"held_for_orders":"5264","message":"insufficient qty available '
    'for order (requested: 5264, available: 0)","symbol":"LIDR"}'
)


def _build_blocking_client(
    *,
    body: str,
    fail_forever: bool = False,
):
    """Minimal mock client that mimics today's deadlock with a
    realistic httpx-shaped 403."""
    client = MagicMock()
    client.get_orders = AsyncMock(return_value=[
        {
            "id": "stop-oid-tuesday",
            "symbol": "LIDR",
            "side": "sell",
            "qty": "5264",
            "type": "stop",
            "stop_price": "2.00",
            "limit_price": None,
            "client_order_id": "manual_protect_tuesday",
            "status": "new",
        },
    ])
    client.cancel_order = AsyncMock(return_value={"status": "canceled"})
    client.submit_order = AsyncMock(return_value={
        "id": "rearm-oid", "status": "accepted",
        "stop_price": "2.00", "qty": "5264",
    })
    call_count = {"n": 0}

    async def close_position(symbol):
        call_count["n"] += 1
        if call_count["n"] == 1 or fail_forever:
            raise _RealisticHTTPStatusError(body=body)
        # Second attempt (post-cancel) succeeds
        return {"id": "close-oid", "filled_avg_price": "1.95", "filled_qty": "5264"}

    client.close_position = AsyncMock(side_effect=close_position)
    return client, call_count


# ── Core regression: Bug AR fix triggers Bug AI on realistic body ───


@pytest.mark.asyncio
async def test_bug_ar_realistic_403_triggers_bug_ai_cancel_and_retry():
    """The defect: with a realistic httpx HTTPStatusError where the body
    is in .response.text (NOT str(e)), the old code path failed to
    detect 'insufficient qty available' and Bug AI never fired. The
    fix: extract e.response.text. Verify cancel-and-coordinate now
    fires for today's exact 403 body."""
    client, call_count = _build_blocking_client(body=LIDR_403_BODY_TUESDAY)
    result = await attempt_close_with_status_check(
        client=client, ticker="LIDR", qty=5264,
        cancel_blocking_stops_first=True,
        retry_backoff_s=0.001,
    )
    # Bug AI fired: stop was cancelled and close retried
    assert result["succeeded"] is True, (
        "Bug AR regression: realistic httpx 403 should trigger cancel-"
        "and-coordinate, but close did not succeed. Check that the "
        "exception handler reads e.response.text into err_str."
    )
    assert len(result["cancelled_stops"]) == 1
    assert result["cancelled_stops"][0]["stop_price"] == 2.00
    assert call_count["n"] == 2  # blocked + retry-after-cancel
    client.cancel_order.assert_awaited_once_with("stop-oid-tuesday")


@pytest.mark.asyncio
async def test_bug_ar_old_codepath_would_have_missed_this():
    """SANITY CHECK on the regression: confirm that the str-only path
    on a realistic httpx error does NOT contain the substrings Bug AI
    looks for. Pin this so a future refactor that drops the body
    extraction immediately surfaces in CI."""
    err = _RealisticHTTPStatusError(body=LIDR_403_BODY_TUESDAY)
    str_only = str(err)
    # If any of these substrings appear in str(e) alone, the test below
    # is meaningless — the regression couldn't happen the way Phase 0
    # said it did. Force a hard fail with a clear message.
    for needle in ("insufficient qty available", "40310000",
                   "available: 0", "qty available"):
        assert needle.lower() not in str_only.lower(), (
            f"BUG AR PREMISE VIOLATED: substring {needle!r} found in "
            f"str(e) alone. The fix may be unnecessary OR the test "
            f"double drifted from real httpx behavior. Either way, "
            f"this test no longer guards what it claims to guard."
        )
    # And confirm the body itself contains the key signal
    assert "40310000" in err.response.text
    assert "insufficient qty available" in err.response.text


# ── Variant bodies: tolerance to Alpaca payload drift ────────────────


@pytest.mark.parametrize("variant_body", [
    # Today's exact body
    LIDR_403_BODY_TUESDAY,
    # Casing drift on the message
    '{"code":40310000,"message":"INSUFFICIENT QTY AVAILABLE FOR ORDER",'
    '"symbol":"LIDR"}',
    # Mixed casing
    '{"code":40310000,"message":"Insufficient Qty Available for order",'
    '"symbol":"LIDR"}',
    # Different key order
    '{"symbol":"LIDR","message":"insufficient qty available","code":40310000}',
    # Code-only (message localized away)
    '{"code":40310000,"symbol":"LIDR","detail":"position is locked"}',
    # Only "available: 0" phrasing (older Alpaca format)
    '{"message":"order rejected; available: 0","code":42210000}',
    # Tolerant fallback: just "qty available"
    '{"message":"reduced qty available","detail":"shares held"}',
])
@pytest.mark.asyncio
async def test_bug_ar_matches_variant_403_bodies(variant_body):
    """Robust matching: every realistic 403-body variant we've observed
    or could observe from Alpaca must trigger Bug AI. Casing, key
    order, message localization, and code-only payloads all count."""
    client, call_count = _build_blocking_client(body=variant_body)
    result = await attempt_close_with_status_check(
        client=client, ticker="LIDR", qty=5264,
        cancel_blocking_stops_first=True,
        retry_backoff_s=0.001,
    )
    assert result["succeeded"] is True, (
        f"Bug AR variant failed to trigger cancel-and-coordinate.\n"
        f"Body: {variant_body!r}\n"
        f"Result: {result!r}"
    )
    assert len(result["cancelled_stops"]) == 1


# ── Negative case: unrelated 403s must NOT trigger cancel ────────────


@pytest.mark.asyncio
async def test_bug_ar_unrelated_403_does_NOT_cancel_stops():
    """Defensive: a 403 for a different reason (e.g., account locked,
    market closed, permission denied) must NOT trigger the cancel-
    and-coordinate dance. Otherwise we'd cancel protective stops on
    every transient broker failure."""
    unrelated_body = (
        '{"code":40010001,"message":"account is locked for trading",'
        '"symbol":"LIDR"}'
    )
    client, call_count = _build_blocking_client(
        body=unrelated_body,
        fail_forever=True,  # close keeps failing — no recovery available
    )
    result = await attempt_close_with_status_check(
        client=client, ticker="LIDR", qty=5264,
        max_retries=2,
        cancel_blocking_stops_first=True,
        retry_backoff_s=0.001,
    )
    assert result["succeeded"] is False
    # CRITICAL: Bug AI must NOT have fired — protective stop must
    # remain at the broker untouched
    assert result["cancelled_stops"] == [], (
        "Bug AI fired on an unrelated 403. This would cancel "
        "protective stops on every account-lock / market-closed / "
        "permission error — the exact opposite of safety."
    )
    client.cancel_order.assert_not_called()


# ── Stress: deterministic success across many replays ───────────────


@pytest.mark.asyncio
async def test_bug_ar_deterministic_across_100_replays():
    """The fix must be deterministic — no race conditions, no flakes.
    Replay the LIDR 403 fixture 100 times, expect 100 successes."""
    successes = 0
    for _ in range(100):
        client, _ = _build_blocking_client(body=LIDR_403_BODY_TUESDAY)
        result = await attempt_close_with_status_check(
            client=client, ticker="LIDR", qty=5264,
            cancel_blocking_stops_first=True,
            retry_backoff_s=0.0,  # zero backoff for stress
        )
        if result["succeeded"]:
            successes += 1
    assert successes == 100, (
        f"Bug AR fix is non-deterministic: {successes}/100 succeeded. "
        f"Expected 100/100."
    )


# ── Wiring: last_error now carries the body for downstream forensics ─


@pytest.mark.asyncio
async def test_bug_ar_last_error_contains_response_body_for_forensics():
    """The Phase 0 audit found that without the body, post-mortem
    forensics on broker rejections were impossible (transcript only
    had 'Client error 403'). The fix must surface the body in
    result['last_error'] so D245 SMART_EXIT_REJECTED logs and any
    downstream attribution can see WHY the close failed."""
    client, _ = _build_blocking_client(
        body=LIDR_403_BODY_TUESDAY,
        fail_forever=True,
    )
    result = await attempt_close_with_status_check(
        client=client, ticker="LIDR", qty=5264,
        max_retries=1,  # one shot, fail
        cancel_blocking_stops_first=False,  # bypass Bug AI to inspect raw error
        retry_backoff_s=0.0,
    )
    assert result["succeeded"] is False
    last_err = result["last_error"] or ""
    assert "40310000" in last_err, (
        f"last_error missing Alpaca code: {last_err!r}. "
        f"Forensics on broker rejections require the body."
    )
    assert "body=" in last_err, (
        f"last_error missing body= prefix: {last_err!r}. "
        f"This breaks the forensics format documented in the Phase 0 "
        f"audit §0.3."
    )
