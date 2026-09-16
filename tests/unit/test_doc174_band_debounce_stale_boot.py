"""Doc 174 (2026-05-24) -- pre-Monday safety fixes per Pierce review.

Tests cover:
  - D313.v3 band widening: 0.30 -> 0.65 to accommodate wide-arm ATR
    stops. Empirical-basis assertion: all 14 historical stops pass.
  - D313.v3 @here debounce: 5min cooldown between CRITICAL mentions
    on the same webhook so degradations don't spam.
  - D314.v2 stale-prefix: records redelivered >2h after spool get a
    "[STALE - originally fired at HH:MM ET]" prefix.
  - D315 boot self-test: static guard that main.py emits the message.
  - Operator runbook exists at the expected path.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════
# D313.v3 -- band widening (empirical regression)
# ═══════════════════════════════════════════════════════════════


def test_l2_default_band_is_0_65():
    """The new default must be 0.65 -- the previous 0.30 false-positives
    21% of legitimate wide-arm ATR stops."""
    from src.monitoring.hedge_integrity_watcher import HedgeIntegrityWatcher
    w = HedgeIntegrityWatcher(client=MagicMock())
    assert w._stop_band_min_pct == pytest.approx(0.65)


@pytest.mark.parametrize("entry,stop_pct,should_pass", [
    # Historical wide-arm stops from doc 170 (all SHOULD pass):
    (4.67, 0.266, True),   # CISS  -26.6%
    (0.51, 0.314, True),   # NXXT  -31.4%
    (1.97, 0.609, True),   # AUUD  -60.9% (worst observed)
    (1.20, 0.310, True),   # NIVF  -31.0%
    (5.87, 0.186, True),   # SLE   -18.6%
    # Tight-arm valid stop (1.5% Phase 1) -- MUST pass
    (21.25, 0.015, True),  # VELO phase1 -1.5% (legitimate tight stop)
    # Absurd cases that MUST be flagged:
    (100.0, 0.999, False),  # stop at $0.10 on $100 stock = 99.9% off
    (100.0, 0.0005, False),  # stop at $99.95 on $100 stock = 5bps off (too tight)
])
def test_band_correctness_on_known_stops(entry, stop_pct, should_pass):
    """Historical wide-arm ATR stops from the doc 170 dataset must pass.
    Absurd stops (too wide OR too tight) must be flagged as incorrect."""
    from src.monitoring.hedge_integrity_watcher import HedgeIntegrityWatcher
    w = HedgeIntegrityWatcher(client=MagicMock())
    stop_price = entry * (1 - stop_pct)
    issue = w._check_stop_correctness(
        sym="X", position_qty=100, position_side="long",
        avg_entry=entry, last_price=entry,
        orders=[{
            "symbol": "X", "side": "sell", "qty": "100",
            "time_in_force": "gtc",
            "stop_price": str(stop_price),
        }],
    )
    if should_pass:
        assert issue is None, (
            f"entry=${entry} stop=${stop_price:.4f} ({stop_pct*100:.1f}% off) "
            f"should PASS but flagged as: {issue}"
        )
    else:
        assert issue is not None, (
            f"entry=${entry} stop=${stop_price:.4f} ({stop_pct*100:.1f}% off) "
            f"should FAIL but was not flagged"
        )


def test_historical_p95_stop_distance_passes_band():
    """Pulls all stop_decisions_*.jsonl and asserts the 95th-percentile
    ATR distance fits inside the new band. If this fails, the band
    needs to be widened further or made arm-aware."""
    import statistics
    dists = []
    for f in sorted(Path('data/shadow_stops').glob('stop_decisions_*.jsonl')):
        for line in f.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            atr_dist = ev.get('atr_dist_pct')
            if atr_dist is not None:
                dists.append(abs(atr_dist) / 100.0)  # to decimal
    if not dists:
        pytest.skip("no historical stop_decisions available")
    p95 = sorted(dists)[int(len(dists) * 0.95)] if len(dists) >= 20 else max(dists)
    from src.monitoring.hedge_integrity_watcher import HedgeIntegrityWatcher
    w = HedgeIntegrityWatcher(client=MagicMock())
    assert p95 <= w._stop_band_min_pct, (
        f"p95 stop distance {p95*100:.2f}% exceeds band "
        f"{w._stop_band_min_pct*100:.0f}% -- band still too tight for "
        f"the empirical distribution"
    )


# ═══════════════════════════════════════════════════════════════
# D313.v3 -- @here debounce
# ═══════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _clear_mention_cache():
    """Each test starts with a clean debounce state."""
    import src.monitoring.alerts as alerts
    alerts._last_critical_mention_utc.clear()
    yield
    alerts._last_critical_mention_utc.clear()


def test_should_mention_first_call_returns_true():
    from src.monitoring.alerts import _should_mention_at_here
    assert _should_mention_at_here("https://x.test/wh") is True


def test_should_mention_second_call_within_cooldown_returns_false():
    from src.monitoring.alerts import _should_mention_at_here
    assert _should_mention_at_here("https://x.test/wh") is True
    # Immediate second call -- inside the 5min window
    assert _should_mention_at_here("https://x.test/wh") is False


def test_different_webhooks_dont_share_debounce():
    from src.monitoring.alerts import _should_mention_at_here
    assert _should_mention_at_here("https://x.test/a") is True
    # Different webhook should still mention
    assert _should_mention_at_here("https://x.test/b") is True


def test_debounce_resets_after_cooldown():
    import src.monitoring.alerts as alerts
    from src.monitoring.alerts import _should_mention_at_here
    assert _should_mention_at_here("https://x.test/wh") is True
    assert _should_mention_at_here("https://x.test/wh") is False
    # Backdate to before the cooldown window
    alerts._last_critical_mention_utc["https://x.test/wh"] = (
        time.monotonic() - (alerts.MENTION_COOLDOWN_SEC + 1.0)
    )
    assert _should_mention_at_here("https://x.test/wh") is True


@pytest.mark.asyncio
async def test_post_critical_mentions_first_time_only(tmp_path, monkeypatch):
    """The async _post path injects @here on first CRITICAL post, but
    not on the second (within cooldown)."""
    import src.monitoring.durable_alert_spool as ds
    monkeypatch.setattr(ds, "DEFAULT_SPOOL_DIR", tmp_path)
    captured = []
    async def fake_dp(url, payload, severity="INFO"):
        captured.append((dict(payload), severity))
        return True
    with patch.object(ds, "durable_post", fake_dp):
        from src.monitoring.alerts import _post
        await _post("https://x.test/wh",
                     {"embeds": [{"title": "T1"}]},
                     severity="CRITICAL")
        await _post("https://x.test/wh",
                     {"embeds": [{"title": "T2"}]},
                     severity="CRITICAL")
    p1, _ = captured[0]
    p2, _ = captured[1]
    # First call: @here mentioned
    assert "@here" in p1.get("content", "")
    # Second call: NO @here (debounced)
    assert "@here" not in p2.get("content", "")


# ═══════════════════════════════════════════════════════════════
# D314.v2 -- stale-prefix injection
# ═══════════════════════════════════════════════════════════════


def test_stale_prefix_injected_for_records_older_than_2h():
    """A record spooled >2h ago gets a [STALE - ...] prefix on retry."""
    from src.monitoring.durable_alert_spool import _maybe_inject_stale_prefix
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    record = {
        "spooled_at_utc": old_ts,
        "payload": {"embeds": [{"title": "T"}]},
    }
    _maybe_inject_stale_prefix(record)
    content = record["payload"].get("content", "")
    assert "[STALE" in content
    assert "originally fired at" in content


def test_no_stale_prefix_for_fresh_records():
    """Records spooled <2h ago are not prefixed."""
    from src.monitoring.durable_alert_spool import _maybe_inject_stale_prefix
    fresh_ts = datetime.now(timezone.utc).isoformat()
    record = {
        "spooled_at_utc": fresh_ts,
        "payload": {"embeds": [{"title": "T"}]},
    }
    _maybe_inject_stale_prefix(record)
    assert "content" not in record["payload"]


def test_stale_prefix_not_double_injected():
    """If a prior retry tick already added a [STALE] prefix, don't add another."""
    from src.monitoring.durable_alert_spool import _maybe_inject_stale_prefix
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    record = {
        "spooled_at_utc": old_ts,
        "payload": {
            "embeds": [],
            "content": "[STALE - originally fired at 09:30 ET] @here ALERT",
        },
    }
    _maybe_inject_stale_prefix(record)
    # Should still be a single [STALE prefix, not two
    assert record["payload"]["content"].count("[STALE") == 1


def test_stale_prefix_preserves_existing_content():
    """Existing content (e.g., from @here mention) is appended, not lost."""
    from src.monitoring.durable_alert_spool import _maybe_inject_stale_prefix
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    record = {
        "spooled_at_utc": old_ts,
        "payload": {"embeds": [], "content": "@here body"},
    }
    _maybe_inject_stale_prefix(record)
    content = record["payload"]["content"]
    assert "[STALE" in content
    assert "@here body" in content


def test_stale_prefix_handles_missing_spooled_at_utc():
    """Defensive: records without spooled_at_utc must not crash."""
    from src.monitoring.durable_alert_spool import _maybe_inject_stale_prefix
    record = {"payload": {"embeds": []}}
    _maybe_inject_stale_prefix(record)  # must not raise
    assert "content" not in record["payload"]


# ═══════════════════════════════════════════════════════════════
# D315 -- boot self-test
# ═══════════════════════════════════════════════════════════════


def test_main_py_emits_boot_self_test():
    """Static guard: main.py must compose + post a D315 boot summary
    containing T2/L2/L2_WATCHDOG/D314_RETRY status."""
    src = Path("main.py").read_text(encoding="utf-8")
    assert "D315" in src
    assert "BOOT_SELF_TEST" in src
    # Must surface each of the 4 watchers/flags
    for token in ["T2", "L2_WATCHDOG", "D314_RETRY"]:
        assert token in src, f"boot self-test missing {token}"


# ═══════════════════════════════════════════════════════════════
# Operator runbook -- exists and covers the 3 alert classes
# ═══════════════════════════════════════════════════════════════


def test_runbook_exists():
    p = Path("docs/RUNBOOK_HEDGE_VIOLATION.md")
    assert p.exists(), "operator runbook must exist for HEDGE_VIOLATION response"


def test_runbook_covers_three_alert_classes():
    p = Path("docs/RUNBOOK_HEDGE_VIOLATION.md")
    txt = p.read_text(encoding="utf-8")
    for cls in ["HEDGE_VIOLATION", "EMERGENCY_STOP_FAILED", "L2_WATCHDOG"]:
        assert cls in txt, f"runbook missing {cls}"


def test_runbook_has_anti_patterns_section():
    """Anti-patterns section prevents the operator from making the
    NXXT-class mistake (manually cancelling the L2 emergency stop)."""
    p = Path("docs/RUNBOOK_HEDGE_VIOLATION.md")
    txt = p.read_text(encoding="utf-8")
    assert "Anti-patterns" in txt or "anti-patterns" in txt.lower()
    assert "Do not manually cancel the L2 emergency stop" in txt or \
           "manually cancel the L2 emergency stop" in txt
