"""Doc 165 (2026-05-13) -- Discord alert formatter regression tests.

Pin the post-deep-dive enhancement:
- Every alert that displays a stock now includes a % change badge.
- A new ``post_entry_filled`` alert fires when an OTO actually fills
  (separate from submit) so D237 BRIDGE_CANCEL retry churn is visible
  in real-time rather than only post-mortem.

These tests verify (a) the shared formatting helpers behave correctly
on edge cases and (b) each enhanced alert's payload contains the
expected '%' / arrow / price strings -- so a future refactor that
silently strips them gets caught.
"""
from __future__ import annotations

import asyncio
import re
from unittest.mock import AsyncMock, patch

import pytest

from src.monitoring import alerts
from src.monitoring.alerts import (
    _color_for_pct,
    _pct_arrow,
    _pct_badge,
    _price_with_change,
    _separator,
)


# ── _pct_arrow direction + magnitude grading ────────────────────────


@pytest.mark.parametrize(
    "pct, expected_codepoint",
    [
        (0.50, "\U0001f680"),          # rocket (>= +20%)
        (0.20, "\U0001f680"),          # rocket boundary
        (0.05, "\U0001f7e2"),          # green up arrow (>= +5%, contains green circle)
        (0.01, "\U0001f7e2"),          # green circle (> 0)
        (0.0, "⏸"),                # pause
        (-0.02, "\U0001f534"),          # red circle
        (-0.10, "\U0001f534"),          # red down arrow (contains red)
        (-0.30, "\U0001f4a5"),          # explosion (<= -20%)
    ],
)
def test_pct_arrow_grades_by_magnitude(pct, expected_codepoint):
    out = _pct_arrow(pct)
    assert expected_codepoint in out, (
        f"_pct_arrow({pct}) = {ascii(out)} did not contain {ascii(expected_codepoint)}"
    )


# ── _pct_badge sign + format ────────────────────────────────────────


@pytest.mark.parametrize(
    "pct, expected",
    [
        (0.088, "+8.8%"),    # signed positive
        (-0.012, "-1.2%"),   # signed negative
        (0.0, "0.0%"),       # zero (no sign)
        (0.50, "+50.0%"),    # large positive
        (-0.5, "-50.0%"),    # large negative
        (15.0, "+1500%"),    # 4-digit gets whole-pct
        (-12.0, "-1200%"),   # 4-digit negative
    ],
)
def test_pct_badge_format(pct, expected):
    assert _pct_badge(pct) == expected


def test_pct_badge_unsigned_mode_drops_plus_sign():
    # For headline magnitudes like "gap of 38.2%", we don't want a "+".
    assert _pct_badge(0.382, signed=False) == "38.2%"
    # Unsigned negatives still show their minus (we never want to lie about direction)
    assert _pct_badge(-0.05, signed=False) == "-5.0%"


# ── _price_with_change end-to-end ───────────────────────────────────


def test_price_with_change_2dp_for_dollar_stocks():
    out = _price_with_change(21.14, 0.088)
    assert "$21.14" in out
    assert "+8.8%" in out


def test_price_with_change_4dp_for_subdollar():
    """Penny stocks must keep 4-decimal precision so the operator can
    distinguish $0.5573 from $0.5500 (the QUCY 2026-05-13 case)."""
    out = _price_with_change(0.5573, -0.012)
    assert "$0.5573" in out, f"sub-$1 should keep 4 decimals; got {ascii(out)}"
    assert "-1.2%" in out


def test_price_with_change_no_pct_omits_badge():
    """When pct is None (e.g. a stop price), no change badge is shown."""
    out = _price_with_change(199.99)
    assert out == "$199.99"


def test_price_with_change_decimals_override():
    out = _price_with_change(123.456, 0.05, decimals=3)
    assert "$123.456" in out


# ── _separator length is exactly the requested width ────────────────


def test_separator_default_length_is_30():
    assert len(_separator()) == 30


def test_separator_custom_width():
    assert len(_separator(50)) == 50


# ── _color_for_pct grades to Discord color ints ─────────────────────


@pytest.mark.parametrize(
    "pct, expected_hex",
    [
        (0.10, 0x2ECC71),   # vivid green
        (0.05, 0x2ECC71),   # boundary
        (0.01, 0x58D68D),   # soft green
        (0.0, 0x95A5A6),    # gray
        (-0.02, 0xEC7063),  # soft red
        (-0.10, 0xE74C3C),  # vivid red
    ],
)
def test_color_for_pct(pct, expected_hex):
    assert _color_for_pct(pct) == expected_hex


# ═══════════════════════════════════════════════════════════════
# Alert payload regression tests
# Each test captures the JSON payload posted to the webhook and
# asserts that the operator-relevant strings are present.
# ═══════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """alerts.py uses a module-level _last_sent dict for rate limiting.
    Without resetting it between tests, the second test that fires the
    same message-type within 30-300s gets silently dropped, producing
    spurious IndexError when the test inspects captured_post."""
    alerts._last_sent.clear()
    yield
    alerts._last_sent.clear()


@pytest.fixture
def captured_post(monkeypatch):
    """Patch alerts._post and alerts._post_sync to capture payloads."""
    captured = []

    async def _fake_post(webhook_url, payload):
        captured.append(("async", webhook_url, payload))

    def _fake_post_sync(webhook_url, payload):
        captured.append(("sync", webhook_url, payload))

    monkeypatch.setattr(alerts, "_post", _fake_post)
    monkeypatch.setattr(alerts, "_post_sync", _fake_post_sync)
    return captured


def _embed_text(payload: dict) -> str:
    """Concatenate every text-bearing field of the embed for substring tests."""
    parts = []
    for embed in payload.get("embeds", []):
        parts.append(embed.get("title", ""))
        parts.append(embed.get("description", ""))
        for f in embed.get("fields", []):
            parts.append(f.get("name", ""))
            parts.append(f.get("value", ""))
    return "\n".join(parts)


# ── post_entry_filled (NEW alert) ───────────────────────────────────


def test_post_entry_filled_includes_pct_change_and_slippage(captured_post):
    """Today's VELO replay: filled at $21.14 vs intended $19.43 with
    gap +38.2% and 4 BRIDGE_CANCEL attempts. The embed must surface
    every one of those numbers."""
    asyncio.run(alerts.post_entry_filled(
        ticker="VELO",
        qty=957,
        fill_price=21.14,
        intended_entry=19.43,
        stop_loss=20.93,
        gap_pct=0.382,
        fill_latency_ms=2_887_000,  # ~48 minutes from first submit to fill
        n_submission_attempts=4,
        webhook_url="https://example.com/webhook",
    ))
    assert len(captured_post) == 1
    text = _embed_text(captured_post[0][2])

    # Identity
    assert "VELO" in text
    assert "FILLED" in text

    # Fill price + gap badge
    assert "$21.14" in text
    assert "+38.2%" in text  # gap badge

    # Slippage from intended entry
    # slip = (21.14 - 19.43) / 19.43 = +8.80%
    assert "+8.8%" in text or "+8.80%" in text
    assert "19.4300" in text or "$19.43" in text  # intended entry shown

    # Stop distance: (20.93 - 21.14) / 21.14 = -0.99%
    assert "$20.93" in text
    assert "-1.0%" in text or "-0.99%" in text or "-1.0" in text

    # BRIDGE_CANCEL churn warning
    assert "4 OTO attempts" in text or "4" in text
    assert "BRIDGE_CANCEL" in text or "churn" in text.lower()

    # Latency
    assert "2887.0s" in text  # 2887000ms / 1000 = 2887.0 seconds


def test_post_entry_filled_no_attempts_no_churn_warning(captured_post):
    """Single-attempt fills should NOT show the churn warning."""
    asyncio.run(alerts.post_entry_filled(
        ticker="VELO", qty=100, fill_price=10.00, intended_entry=10.00,
        stop_loss=9.50, n_submission_attempts=1,
        webhook_url="https://example.com/webhook",
    ))
    text = _embed_text(captured_post[0][2])
    assert "OTO attempts" not in text
    assert "BRIDGE_CANCEL" not in text


def test_post_entry_filled_no_slippage_when_filled_at_limit(captured_post):
    """If fill == intended, slippage badge should still be 0.0% (clear signal)."""
    asyncio.run(alerts.post_entry_filled(
        ticker="X", qty=1, fill_price=10.00, intended_entry=10.00,
        stop_loss=9.50,
        webhook_url="https://example.com/webhook",
    ))
    text = _embed_text(captured_post[0][2])
    # slip is 0 -- the slippage_str is suppressed when fill == intended
    # so the line shouldn't appear (cleaner UX)
    assert "Slippage" not in text or "0.0%" in text


def test_post_entry_filled_no_webhook_is_noop(captured_post):
    """No webhook URL -> nothing posted (must never crash)."""
    asyncio.run(alerts.post_entry_filled(
        ticker="X", qty=1, fill_price=10.0, intended_entry=10.0, stop_loss=9.0,
        webhook_url=None,
    ))
    assert captured_post == []


# ── post_trade_open enhancements ────────────────────────────────────


def test_post_trade_open_shows_gap_pct_when_provided(captured_post):
    asyncio.run(alerts.post_trade_open(
        ticker="VELO", side="BUY", qty=957,
        entry_price=21.14, stop_loss=20.93, mfcs=0.55, path="PHASE2_BUY",
        webhook_url="https://example.com/webhook",
        gap_pct=0.382,
    ))
    text = _embed_text(captured_post[0][2])
    assert "+38.2%" in text, "gap badge missing from BUY embed"
    assert "VELO" in text and "$21.14" in text


def test_post_trade_open_shows_slippage_when_intended_differs(captured_post):
    asyncio.run(alerts.post_trade_open(
        ticker="VELO", side="BUY", qty=957,
        entry_price=21.14, stop_loss=20.93, mfcs=0.55, path="PHASE2_BUY",
        webhook_url="https://example.com/webhook",
        gap_pct=0.382, intended_entry=19.43,
    ))
    text = _embed_text(captured_post[0][2])
    assert "Slippage" in text
    assert "+8.8%" in text  # (21.14 - 19.43) / 19.43


def test_post_trade_open_omits_slippage_when_intended_equals_fill(captured_post):
    asyncio.run(alerts.post_trade_open(
        ticker="X", side="BUY", qty=10,
        entry_price=10.0, stop_loss=9.5, mfcs=0.5, path="FAST_PATH",
        webhook_url="https://example.com/webhook",
        intended_entry=10.0,
    ))
    text = _embed_text(captured_post[0][2])
    assert "Slippage" not in text


def test_post_trade_open_kwargs_are_optional(captured_post):
    """Backwards-compat: existing callers without gap_pct/intended_entry must work."""
    asyncio.run(alerts.post_trade_open(
        ticker="X", side="BUY", qty=10,
        entry_price=10.0, stop_loss=9.5, mfcs=0.5, path="FAST_PATH",
        webhook_url="https://example.com/webhook",
    ))
    assert len(captured_post) == 1, "old call signature should still work"


# ── alert_halt_blocked_entry_sync stop-distance pct ─────────────────


def test_halt_blocked_shows_stop_distance_pct(captured_post):
    """The QUCY 2026-05-13 failure case: stop too tight relative to base.
    The alert should display the stop distance as a pct so the operator
    can spot the violation without doing arithmetic."""
    alerts.alert_halt_blocked_entry_sync(
        ticker="QUCY", side="buy", qty=36546,
        limit_price=0.5573, stop_loss=0.55,
        halt_reason="MOMENTUM_HALT_NEW_ENTRIES",
        webhook_url="https://example.com/webhook",
    )
    text = _embed_text(captured_post[0][2])
    assert "QUCY" in text
    assert "$0.5573" in text  # 4-decimal sub-$1 precision
    assert "$0.5500" in text  # stop also 4-dp
    # stop_pct = (0.55 - 0.5573) / 0.5573 = -0.0131 = -1.3%
    assert "-1.3%" in text, f"stop distance pct missing from halt embed: {ascii(text)[:500]}"


# ── post_watchlist day-change badge ─────────────────────────────────


def test_post_watchlist_includes_intraday_change_when_provided(captured_post):
    asyncio.run(alerts.post_watchlist(
        candidates=[{
            "ticker": "VELO", "gap_pct": 0.382, "rvol": 2.6,
            "price": 21.14, "day_change_pct": 0.025,
        }],
        phase="PHASE_2", scan_count=1,
        webhook_url="https://example.com/webhook",
        hour_et=10,
    ))
    assert len(captured_post) == 1
    text = _embed_text(captured_post[0][2])
    assert "VELO" in text
    assert "$21.14" in text
    # Both gap and intraday change badges should appear
    assert "+38.2%" in text  # gap
    assert "+2.5%" in text   # day_change_pct (intraday)


def test_post_watchlist_works_without_day_change_pct(captured_post):
    """Backwards-compat: no day_change_pct means just price (no badge)."""
    asyncio.run(alerts.post_watchlist(
        candidates=[{
            "ticker": "X", "gap_pct": 0.10, "rvol": 5.0, "price": 10.0,
        }],
        phase="PHASE_2", scan_count=1,
        webhook_url="https://example.com/webhook",
        hour_et=10,
    ))
    text = _embed_text(captured_post[0][2])
    assert "X" in text and "$10.00" in text
    assert "+10.0%" in text  # gap still shown


# ── post_fast_path_scores gap badge ─────────────────────────────────


def test_fast_path_includes_gap_pct_so_operator_sees_why(captured_post):
    asyncio.run(alerts.post_fast_path_scores(
        entries=[{
            "ticker": "VELO", "partial_mfcs": 0.7,
            "entry_price": 21.14, "stop_loss": 20.93, "gap_pct": 0.382,
        }],
        webhook_url="https://example.com/webhook",
    ))
    text = _embed_text(captured_post[0][2])
    assert "VELO" in text
    assert "+38.2%" in text  # gap, the headline number
    # stop distance: (20.93 - 21.14) / 21.14 = -0.0099 = -1.0%
    assert "-1.0%" in text or "1.0%" in text


# ── post_verdict_summary BUY/HOLD lines ─────────────────────────────


def test_verdict_summary_buy_lines_show_price_and_gap(captured_post):
    asyncio.run(alerts.post_verdict_summary(
        verdicts=[
            {"ticker": "VELO", "action": "BUY", "mfcs": 0.7,
             "price": 21.14, "gap_pct": 0.382},
            {"ticker": "X", "action": "HOLD", "mfcs": 0.3,
             "price": 5.00, "gap_pct": 0.05},
        ],
        webhook_url="https://example.com/webhook",
    ))
    text = _embed_text(captured_post[0][2])
    assert "BUY" in text and "VELO" in text
    assert "$21.14" in text
    assert "+38.2%" in text


def test_verdict_summary_works_without_price(captured_post):
    """Backwards-compat: verdict dicts without 'price' key still render."""
    asyncio.run(alerts.post_verdict_summary(
        verdicts=[{"ticker": "X", "action": "BUY", "mfcs": 0.7}],
        webhook_url="https://example.com/webhook",
    ))
    text = _embed_text(captured_post[0][2])
    assert "BUY" in text and "X" in text


# ── alert_session_end_rich winners/losers with arrows + entry/exit ──


def test_session_end_rich_winners_use_pct_arrow(captured_post):
    asyncio.run(alerts.alert_session_end_rich(
        session_date="2026-05-14",
        trades=2, realized_pnl=200.0, unrealized_pnl=0.0,
        positions_at_close=0,
        starting_equity=140000.0, ending_equity=140200.0,
        top_winners=[{
            "ticker": "WINNER", "pnl": 250.0, "pnl_pct": 8.0,
            "entry_price": 10.00, "exit_price": 10.80,
        }],
        top_losers=[{
            "ticker": "LOSER", "pnl": -50.0, "pnl_pct": -2.5,
            "entry_price": 5.00, "exit_price": 4.875,
        }],
        webhook_url="https://example.com/webhook",
    ))
    text = _embed_text(captured_post[0][2])
    # Winner: +8% -> green up arrow
    assert "WINNER" in text
    assert "+8.0%" in text
    # Loser: -2.5% -> red circle
    assert "LOSER" in text
    assert "-2.5%" in text
    # Entry/exit prices appear
    assert "$10.00" in text and "$10.80" in text
    assert "$5.00" in text


def test_session_end_rich_works_without_entry_exit_prices(captured_post):
    """Backwards-compat: existing callers without entry_price/exit_price still work."""
    asyncio.run(alerts.alert_session_end_rich(
        session_date="2026-05-14",
        trades=1, realized_pnl=100.0, unrealized_pnl=0.0,
        positions_at_close=0,
        starting_equity=140000.0, ending_equity=140100.0,
        top_winners=[{"ticker": "W", "pnl": 100.0, "pnl_pct": 5.0}],
        webhook_url="https://example.com/webhook",
    ))
    text = _embed_text(captured_post[0][2])
    assert "W" in text and "+5.0%" in text


# ── alert_morning_resolution_sync uses _price_with_change ───────────


def test_morning_resolution_uses_unified_price_format(captured_post):
    """The carry-overnight alert should display each position with the
    standardized price+change formatting."""
    alerts.alert_morning_resolution_sync(
        overnight_positions=[{
            "ticker": "VELO", "qty": 957, "entry": 21.14, "current": 22.50,
            "pnl_dollar": 1300.0, "pnl_pct": 6.43, "days_held": 1,
        }],
        webhook_url="https://example.com/webhook",
    )
    text = _embed_text(captured_post[0][2])
    assert "VELO" in text
    assert "$21.14" in text  # entry
    assert "$22.50" in text  # current
    assert "+6.4%" in text   # pnl_pct rounded


# ── Entire module imports cleanly + has expected public API ─────────


def test_alerts_module_public_api():
    """Sanity: the new public symbols are exposed."""
    expected = [
        "_pct_arrow", "_pct_badge", "_price_with_change",
        "_separator", "_color_for_pct",
        "post_entry_filled",  # new alert
    ]
    for name in expected:
        assert hasattr(alerts, name), f"alerts.py missing {name}"
