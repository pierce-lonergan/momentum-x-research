"""doc 208: Self-contained Discord notifier for the Operator (never-raises).

Reuses the bot's existing OPS_ALERT_WEBHOOK_URL. Deliberately dependency-free (stdlib
urllib) and isolated from src/monitoring/alerts.py so an Operator post can never be broken
by unrelated alerting code. Pierce wants the Operator Discord-heavy; this is the channel.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request

logger = logging.getLogger(__name__)
_WEBHOOK_ENV = "OPS_ALERT_WEBHOOK_URL"
_MAX = 1900  # Discord hard limit is 2000


def is_configured() -> bool:
    return bool(os.environ.get(_WEBHOOK_ENV, "").strip())


def post(text: str, *, prefix: str = "🤖 Operator") -> bool:
    """Post a message to the Operator Discord channel. Returns True on 2xx. NEVER raises."""
    try:
        url = os.environ.get(_WEBHOOK_ENV, "").strip()
        if not url:
            logger.info("discord_notify: no %s set — skipping post", _WEBHOOK_ENV)
            return False
        body = f"**{prefix}**\n{text}"
        if len(body) > _MAX:
            body = body[:_MAX] + "\n…(truncated)"
        data = json.dumps({"content": body}).encode("utf-8")
        # doc 211: Discord sits behind Cloudflare, which BLOCKS the default urllib
        # User-Agent ("Python-urllib/x.y") with error 1010 -> 403. A browser-like UA is
        # required (the bot's httpx-based alerts work because httpx sends one). Without
        # this, every Operator Discord heartbeat silently 403s.
        req = urllib.request.Request(url, data=data, headers={
            "Content-Type": "application/json",
            "User-Agent": "MomentumX-Operator/1.0 (+https://momentum-x.local)",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            return 200 <= resp.status < 300
    except Exception as e:  # noqa: BLE001 — a failed Discord post must never break anything
        logger.warning("discord_notify.post failed: %s", e)
        return False
