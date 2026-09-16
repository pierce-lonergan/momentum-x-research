"""Bug AJ tests: morning-resolution Discord alert for overnight positions.

Pins the Tier 1 #2 fix from today's strategic assessment: stale
positions like LIDR (carried since 2026-04-25 Bug Z) need explicit
operator surfacing at startup, not just a buried WARNING line in the
log.

See docs/research-log/49_bug_aj_morning_resolution.md.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.monitoring.alerts import alert_morning_resolution_sync


def test_no_webhook_url_silently_returns():
    """Defensive: if OPS_ALERT_WEBHOOK_URL is unset, function is a no-op."""
    with patch("src.monitoring.alerts._post_sync") as mock_post:
        alert_morning_resolution_sync(
            overnight_positions=[
                {"ticker": "LIDR", "qty": 5264, "entry": 2.42, "current": 2.17,
                 "pnl_dollar": -1316, "pnl_pct": -10.3, "days_held": 4},
            ],
            webhook_url=None,
        )
        mock_post.assert_not_called()


def test_empty_positions_silently_returns():
    """Defensive: no overnight positions = no alert posted."""
    with patch("src.monitoring.alerts._post_sync") as mock_post:
        alert_morning_resolution_sync(
            overnight_positions=[],
            webhook_url="https://discord.com/api/webhooks/x/y",
        )
        mock_post.assert_not_called()


def test_single_position_payload_shape():
    """One overnight position → Discord embed with the canonical
    ticker + P&L + days held format."""
    with patch("src.monitoring.alerts._post_sync") as mock_post:
        alert_morning_resolution_sync(
            overnight_positions=[
                {"ticker": "LIDR", "qty": 5264, "entry": 2.42, "current": 2.17,
                 "pnl_dollar": -1316, "pnl_pct": -10.3, "days_held": 4},
            ],
            webhook_url="https://discord.com/api/webhooks/x/y",
        )
        mock_post.assert_called_once()
        url, payload = mock_post.call_args[0]
        assert url == "https://discord.com/api/webhooks/x/y"
        assert "embeds" in payload
        embed = payload["embeds"][0]
        assert "Morning Resolution Required" in embed["title"]
        assert "LIDR" in embed["description"]
        assert "5264" in embed["description"]
        assert "$2.42" in embed["description"]
        assert "$2.17" in embed["description"]
        assert "4d" in embed["description"]
        assert "MOMENTUM_HOLD_OVERNIGHT" in embed["description"]
        # Total P&L includes the $1316 loss
        assert "-1,316" in embed["description"] or "-$1,316" in embed["description"]


def test_multi_position_aggregates_total_pnl():
    """Multiple positions → total P&L is sum of per-position P&L."""
    with patch("src.monitoring.alerts._post_sync") as mock_post:
        alert_morning_resolution_sync(
            overnight_positions=[
                {"ticker": "LIDR", "qty": 5264, "entry": 2.42, "current": 2.17,
                 "pnl_dollar": -1316, "pnl_pct": -10.3, "days_held": 4},
                {"ticker": "OGN", "qty": 999, "entry": 13.20, "current": 13.50,
                 "pnl_dollar": 300, "pnl_pct": 2.3, "days_held": 1},
            ],
            webhook_url="https://discord.com/api/webhooks/x/y",
        )
        mock_post.assert_called_once()
        embed = mock_post.call_args[0][1]["embeds"][0]
        assert "LIDR" in embed["description"]
        assert "OGN" in embed["description"]
        # Net: -1316 + 300 = -1016
        assert "-1,016" in embed["description"] or "-$1,016" in embed["description"]


def test_alert_color_is_warning_amber_not_error_red():
    """Morning resolution is a warning, not a critical error — uses
    amber (0xF39C12), not red (0xE74C3C). Distinguishable from
    actual critical alerts in the Discord channel."""
    with patch("src.monitoring.alerts._post_sync") as mock_post:
        alert_morning_resolution_sync(
            overnight_positions=[
                {"ticker": "X", "qty": 1, "entry": 1.0, "current": 1.0,
                 "pnl_dollar": 0, "pnl_pct": 0, "days_held": 1},
            ],
            webhook_url="https://discord.com/api/webhooks/x/y",
        )
        embed = mock_post.call_args[0][1]["embeds"][0]
        assert embed["color"] == 0xF39C12  # amber, not 0xE74C3C critical red


def test_post_sync_failure_doesnt_raise():
    """Defensive: if Discord post itself fails, function must not raise
    (would crash the startup path which has no try/except around it)."""
    with patch("src.monitoring.alerts._post_sync", side_effect=RuntimeError("Discord down")):
        # Must not raise
        try:
            alert_morning_resolution_sync(
                overnight_positions=[
                    {"ticker": "X", "qty": 1, "entry": 1.0, "current": 1.0,
                     "pnl_dollar": 0, "pnl_pct": 0, "days_held": 1},
                ],
                webhook_url="https://discord.com/api/webhooks/x/y",
            )
        except RuntimeError:
            pytest.fail(
                "alert_morning_resolution_sync raised on Discord failure — "
                "must be defensive (would crash startup path)"
            )
