"""D280 (2026-05-05) — Dynamic bankroll for full-capital Kelly sizing.

Pin Tuesday 2026-05-05 production observation: every order in
``logs/lottery_2026-05-01.log`` (the last day picks fired) was sized to
``~$240–$249 notional`` — the legacy ``LOTTERY_NOTIONAL_USD=$250`` fixed
cap, with ZERO Kelly tier modulation. Even if the meta-scorer had fired
a META-PASS at full ELITE conviction (Kelly cap = 0.50), the production
bankroll constant ``LOTTERY_META_BANKROLL_USD = $10,000`` would have
sized the position to $5k = **3.5% of the $140k paper account**.

Post-D280: bankroll = ``account_equity * LOTTERY_BANKROLL_PCT`` (default
1.0 = 100% of equity). Aggressive Kelly's 50/35/20/10 tier caps now map
onto the **full account**:

    ELITE  on $140k @ Kelly cap 0.50 → $70,000 per pick
    HIGH   on $140k @ Kelly cap 0.35 → $49,000 per pick
    VETOED on $140k @ Kelly cap 0.20 → $28,000 per pick
    BROAD  on $140k @ Kelly cap 0.10 → $14,000 per pick

Three opt-out paths preserved for safety:
  - ``LOTTERY_BANKROLL_PCT=0.5`` for half-account ramp-up
  - ``LOTTERY_BANKROLL_PCT=0`` for legacy fixed-$10k mode (uses
    ``LOTTERY_META_BANKROLL_USD``)
  - Zero/missing equity → falls back to legacy fixed bankroll

This test suite is the keystone regression guard for tomorrow's clean run.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))


def _reload_runner(monkeypatch, **env):
    """Reload lottery_runner with a clean env so module-level constants pick
    up the desired LOTTERY_BANKROLL_PCT / LOTTERY_META_BANKROLL_USD values."""
    for k in ("LOTTERY_BANKROLL_PCT", "LOTTERY_META_BANKROLL_USD"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    # D262 added a $5k SHARED-account cap that clamps the bankroll before these
    # assertions ever see it. These tests exercise D280 bankroll RESOLUTION, so
    # isolate the account the documented way to lift that cap.
    monkeypatch.setenv("LOTTERY_ALPACA_API_KEY", "test-lottery-key-26char-aaaa")
    monkeypatch.setenv("ALPACA_API_KEY", "test-key-26char-aaaaaaaaaaaa")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret-44char-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
    import lottery_runner
    importlib.reload(lottery_runner)
    return lottery_runner


# ── 1. Default behavior: dynamic bankroll = 100% of equity ────────────


def test_default_bankroll_is_100pct_of_equity(monkeypatch):
    """Production default: LOTTERY_BANKROLL_PCT unset → 1.0 → use full equity."""
    runner = _reload_runner(monkeypatch)
    assert runner.LOTTERY_BANKROLL_PCT == 1.0
    bankroll, label = runner.compute_sizing_bankroll(account_equity=140_316.0)
    assert bankroll == pytest.approx(140_316.0), (
        f"Default LOTTERY_BANKROLL_PCT=1.0 should yield full equity; got ${bankroll}"
    )
    assert "dynamic" in label
    assert "100%" in label
    assert "140,316" in label or "140316" in label


def test_explicit_pct_1_0_matches_full_equity(monkeypatch):
    """Explicit LOTTERY_BANKROLL_PCT=1.0 → full equity (defensive duplicate)."""
    runner = _reload_runner(monkeypatch, LOTTERY_BANKROLL_PCT="1.0")
    bankroll, _ = runner.compute_sizing_bankroll(account_equity=200_000.0)
    assert bankroll == pytest.approx(200_000.0)


def test_half_account_ramp_up_at_pct_0_5(monkeypatch):
    """LOTTERY_BANKROLL_PCT=0.5 → half-account ramp-up mode."""
    runner = _reload_runner(monkeypatch, LOTTERY_BANKROLL_PCT="0.5")
    bankroll, label = runner.compute_sizing_bankroll(account_equity=140_316.0)
    assert bankroll == pytest.approx(70_158.0)
    assert "50%" in label


def test_quarter_account_at_pct_0_25(monkeypatch):
    """Configurable to any ramp fraction."""
    runner = _reload_runner(monkeypatch, LOTTERY_BANKROLL_PCT="0.25")
    bankroll, _ = runner.compute_sizing_bankroll(account_equity=100_000.0)
    assert bankroll == pytest.approx(25_000.0)


# ── 2. Opt-out: legacy fixed-$10k mode ───────────────────────────────


def test_pct_zero_falls_back_to_legacy_fixed_bankroll(monkeypatch):
    """LOTTERY_BANKROLL_PCT=0 → use the legacy LOTTERY_META_BANKROLL_USD."""
    runner = _reload_runner(
        monkeypatch,
        LOTTERY_BANKROLL_PCT="0",
        LOTTERY_META_BANKROLL_USD="10000",
    )
    bankroll, label = runner.compute_sizing_bankroll(account_equity=140_316.0)
    assert bankroll == pytest.approx(10_000.0)
    assert "legacy fixed" in label


def test_zero_equity_falls_back_to_legacy_even_with_pct_set(monkeypatch):
    """If broker returns equity=0 (auth issue, etc.), don't size to $0 —
    fall back to the legacy fixed bankroll. Defends against the alternative
    failure mode where dynamic sizing accidentally kills all picks."""
    runner = _reload_runner(monkeypatch, LOTTERY_BANKROLL_PCT="1.0")
    bankroll, label = runner.compute_sizing_bankroll(account_equity=0.0)
    assert bankroll == pytest.approx(10_000.0), (
        "zero equity must NOT cause $0 bankroll — fall back to legacy fixed"
    )
    assert "legacy" in label


def test_negative_equity_falls_back_to_legacy(monkeypatch):
    """Defensive: bizarre negative equity values must not break sizing."""
    runner = _reload_runner(monkeypatch, LOTTERY_BANKROLL_PCT="1.0")
    bankroll, _ = runner.compute_sizing_bankroll(account_equity=-50_000.0)
    assert bankroll == pytest.approx(10_000.0)


# ── 3. Custom legacy floor still works ────────────────────────────────


def test_pct_zero_respects_custom_legacy_bankroll(monkeypatch):
    """LOTTERY_META_BANKROLL_USD=50000 + LOTTERY_BANKROLL_PCT=0 → $50k floor."""
    runner = _reload_runner(
        monkeypatch,
        LOTTERY_BANKROLL_PCT="0",
        LOTTERY_META_BANKROLL_USD="50000",
    )
    bankroll, _ = runner.compute_sizing_bankroll(account_equity=140_316.0)
    assert bankroll == pytest.approx(50_000.0)


# ── 4. End-to-end: bankroll × Kelly cap = expected $-amount ──────────


def test_elite_kelly_cap_x_full_equity_yields_70k_on_140k_account(monkeypatch):
    """The headline case: ELITE pick on a $140k account at Aggressive Kelly
    sizes to $70k notional (50% × $140k). Pre-D280 it was $5k (50% × $10k)."""
    runner = _reload_runner(monkeypatch, LOTTERY_BANKROLL_PCT="1.0")
    bankroll, _ = runner.compute_sizing_bankroll(account_equity=140_316.0)
    elite_kelly_cap = 0.50  # KELLY_CAPS_AGGRESSIVE['ELITE']
    elite_max_notional = bankroll * elite_kelly_cap
    assert elite_max_notional == pytest.approx(70_158.0), (
        f"ELITE on $140k at Kelly cap 0.50 should be ~$70k; got ${elite_max_notional:,.0f}"
    )
    # And the pre-D280 baseline for contrast — assert the regression cleared
    pre_d280_legacy = 10_000.0 * elite_kelly_cap  # $5k
    assert elite_max_notional > pre_d280_legacy * 10, (
        f"sizing must be >10x the pre-D280 legacy ceiling of ${pre_d280_legacy}; "
        f"got ${elite_max_notional}"
    )


def test_all_tiers_size_proportionally_on_full_account(monkeypatch):
    """Sanity: each tier's Kelly cap yields the expected fraction of equity.
    Aggressive Kelly: ELITE 50%, HIGH 50% (D291.5 raise), VETOED 20%, BROAD 10%.

    2026-05-12: HIGH bumped 0.35 -> 0.50 per doc 162 §7 (D291.5 trigger met
    at n_HIGH=56, mean ret +4.13%). Original target was Bouchaud-optimal 98%
    (doc 150 sub-exp B); shipped conservatively at 0.50.
    """
    runner = _reload_runner(monkeypatch, LOTTERY_BANKROLL_PCT="1.0")
    equity = 140_000.0
    bankroll, _ = runner.compute_sizing_bankroll(account_equity=equity)
    expected = {
        "ELITE": 0.50 * equity,   # $70,000
        "HIGH": 0.50 * equity,    # $70,000 (D291.5 raise from 0.35)
        "VETOED": 0.20 * equity,  # $28,000
        "BROAD": 0.10 * equity,   # $14,000
    }
    for tier, expected_max in expected.items():
        # Use the inference module's KELLY_CAPS_AGGRESSIVE
        from ml_meta_scorer_inference import KELLY_CAPS_AGGRESSIVE
        cap = KELLY_CAPS_AGGRESSIVE[tier]
        actual_max = bankroll * cap
        assert actual_max == pytest.approx(expected_max), (
            f"{tier}: cap={cap} × bankroll=${bankroll:,.0f} = "
            f"${actual_max:,.0f}, expected ${expected_max:,.0f}"
        )


# ── 5. Pre-D280 regression check: documents what was broken ──────────


def test_pre_d280_legacy_behavior_was_microscopic_on_real_account(monkeypatch):
    """Documents the pre-D280 microscopic-sizing failure mode for posterity.
    The legacy LOTTERY_META_BANKROLL_USD=$10k on a $140k paper account
    produced max ELITE pick = $5k = 3.5% of available capital. This test
    demonstrates that opting back into legacy mode reproduces the bug —
    NOT a fix-regression, just a documentation of pre-D280 behavior."""
    runner = _reload_runner(
        monkeypatch,
        LOTTERY_BANKROLL_PCT="0",
        LOTTERY_META_BANKROLL_USD="10000",
    )
    real_account = 140_316.0
    bankroll, _ = runner.compute_sizing_bankroll(account_equity=real_account)
    elite_max = bankroll * 0.50
    pct_of_account = elite_max / real_account * 100
    assert bankroll == 10_000.0
    assert elite_max == 5_000.0
    assert pct_of_account < 5.0, (
        f"pre-D280 ELITE on real account = {pct_of_account:.1f}% of capital "
        f"(this microscopic behavior is what D280 fixes when PCT > 0)"
    )
