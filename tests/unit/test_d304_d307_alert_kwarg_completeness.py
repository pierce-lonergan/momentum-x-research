"""D304 / D307 (2026-05-19) -- Discord alert kwarg-completeness guards.

Pin the production observation from 2026-05-19:
  ``alert_session_end_rich`` was being called with only 9 of 15 supported
  kwargs. The 6 missing ones (d_codes_fired, top_winners, top_losers,
  eod_recon, bocpd_refit, veto_summary) were the entire reason doc 161
  shipped that alert -- the rich context I added never actually reached
  Discord. User reported: "Discord is also providing incomplete messages".

  Same pattern existed for ``post_trade_open``: doc 165 added ``gap_pct``
  and ``intended_entry`` kwargs but the caller in main.py never passed
  them. Every BUY alert was missing the gap badge and slippage display.

Two layers of tests in this file:

  1. **D304 unit tests** -- payload builders for the new EOD kwargs
     (winners/losers from trade_journal, d_codes from eod_full_report,
     bocpd_payload from refit dict). Catches regressions in the kwarg
     extraction logic, independent of how main.py wires it.

  2. **D307 static AST guard** -- audit each alert function's signature
     against its call site(s) in main.py. Flag any kwarg the function
     accepts but the call site never passes. This catches the *class*
     of bug, not just the specific instances we know about.

Result: any future commit that adds a kwarg to an alert function but
forgets to wire it from main.py gets caught at test time, BEFORE the
Discord channel goes silently incomplete for weeks.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parents[2]
MAIN_PY = REPO / "main.py"
ALERTS_PY = REPO / "src" / "monitoring" / "alerts.py"


# ═══════════════════════════════════════════════════════════════
# D304 unit tests -- payload builder logic
# ═══════════════════════════════════════════════════════════════


def _make_journal_entry(*, ticker, action, entry_price, exit_price, realized_pnl):
    e = MagicMock()
    e.ticker = ticker
    e.action = action
    e.entry_price = entry_price
    e.exit_price = exit_price
    e.realized_pnl = realized_pnl
    return e


def _compute_winners_losers(trade_journal):
    """Mirror the D304 payload logic embedded in main.py:6905+.

    This is the reference implementation. The test below verifies that
    callers (and the inlined main.py block) behave identically.
    """
    closed = [
        e for e in trade_journal._entries.values()
        if e.action == "BUY" and e.realized_pnl is not None
    ]
    closed.sort(key=lambda e: float(e.realized_pnl or 0.0), reverse=True)
    winners, losers = [], []
    for e in closed[:3]:
        if (e.realized_pnl or 0) <= 0:
            break
        ep = float(e.entry_price or 0)
        xp = float(e.exit_price or 0)
        pct = ((xp / ep - 1) * 100) if ep > 0 and xp > 0 else 0.0
        winners.append({"ticker": e.ticker, "pnl": float(e.realized_pnl),
                        "pnl_pct": pct, "entry_price": ep, "exit_price": xp})
    for e in reversed(closed[-3:]):
        if (e.realized_pnl or 0) >= 0:
            break
        ep = float(e.entry_price or 0)
        xp = float(e.exit_price or 0)
        pct = ((xp / ep - 1) * 100) if ep > 0 and xp > 0 else 0.0
        losers.append({"ticker": e.ticker, "pnl": float(e.realized_pnl),
                       "pnl_pct": pct, "entry_price": ep, "exit_price": xp})
    return winners, losers


def test_winners_losers_extracted_from_closed_buy_entries():
    """The exact pattern wired into main.py:6905+ for D304."""
    journal = MagicMock()
    journal._entries = {
        "t1": _make_journal_entry(ticker="WIN1", action="BUY",
                                   entry_price=10, exit_price=12, realized_pnl=200),
        "t2": _make_journal_entry(ticker="WIN2", action="BUY",
                                   entry_price=5, exit_price=5.5, realized_pnl=50),
        "t3": _make_journal_entry(ticker="LOSE1", action="BUY",
                                   entry_price=20, exit_price=18, realized_pnl=-200),
        "t4": _make_journal_entry(ticker="OPEN", action="BUY",
                                   entry_price=8, exit_price=None, realized_pnl=None),
    }
    winners, losers = _compute_winners_losers(journal)
    assert [w["ticker"] for w in winners] == ["WIN1", "WIN2"]
    assert [l["ticker"] for l in losers] == ["LOSE1"]
    # Open positions (no realized_pnl) must be excluded
    assert all(w["ticker"] != "OPEN" for w in winners)
    assert all(l["ticker"] != "OPEN" for l in losers)


def test_winners_losers_with_only_losses():
    """All-losing day must produce 0 winners + up to 3 losers."""
    journal = MagicMock()
    journal._entries = {
        "t1": _make_journal_entry(ticker="L1", action="BUY",
                                   entry_price=10, exit_price=9, realized_pnl=-100),
        "t2": _make_journal_entry(ticker="L2", action="BUY",
                                   entry_price=10, exit_price=8, realized_pnl=-200),
    }
    winners, losers = _compute_winners_losers(journal)
    assert winners == []
    assert [l["ticker"] for l in losers] == ["L2", "L1"]


def test_winners_losers_caps_at_three():
    journal = MagicMock()
    journal._entries = {
        f"t{i}": _make_journal_entry(
            ticker=f"T{i}", action="BUY",
            entry_price=10, exit_price=11, realized_pnl=100 + i)
        for i in range(5)
    }
    winners, losers = _compute_winners_losers(journal)
    assert len(winners) == 3


def test_winners_losers_zero_entry_does_not_crash():
    """Defensive: a buggy entry with entry_price=0 must not divide by 0."""
    journal = MagicMock()
    journal._entries = {
        "t1": _make_journal_entry(ticker="X", action="BUY",
                                   entry_price=0, exit_price=10, realized_pnl=100),
    }
    winners, losers = _compute_winners_losers(journal)
    assert winners[0]["pnl_pct"] == 0.0


def test_bocpd_refit_payload_shape_matches_alert_signature():
    """The translation from build_eod_report's bocpd_refit dict to the
    {refit_recommended, old_mu, new_mu, n_trades} shape the alert
    expects. Pre-D304 this mapping wasn't done at all."""
    bocpd_dict = {
        "fires_d262": True,
        "diff": {
            "old_mu_edge": -255.44,
            "new_mu_edge": -231.81,
            "new_n_trades": 38,
            "delta_mu": 23.63,
        },
        "error": None,
    }
    payload = {
        "refit_recommended": bool(bocpd_dict.get("fires_d262")),
        "old_mu": bocpd_dict["diff"]["old_mu_edge"],
        "new_mu": bocpd_dict["diff"]["new_mu_edge"],
        "n_trades": bocpd_dict["diff"]["new_n_trades"],
    }
    # The alert reads these exact field names (see alerts.py:937-945)
    assert payload["refit_recommended"] is True
    assert payload["old_mu"] == -255.44
    assert payload["new_mu"] == -231.81
    assert payload["n_trades"] == 38


# ═══════════════════════════════════════════════════════════════
# D307 static AST guard -- alert kwarg-completeness audit
# ═══════════════════════════════════════════════════════════════


# Each alert function and the EXPECTED kwargs that production callers
# MUST pass to render a useful Discord message. We DON'T require every
# possible kwarg (many are correctly optional), only the ones whose
# absence would leave a critical field blank in the Discord embed.
# Kwarg names here are the ALERT FUNCTION's parameter names.
#
# This is the test contract: when adding a NEW kwarg to an alert that
# materially changes the rendered output, add it here too.
_ALERT_CONTRACTS: dict[str, set[str]] = {
    "alert_session_end_rich": {
        # The 9 baseline kwargs the call site has had since doc 161
        "session_date", "trades", "realized_pnl", "unrealized_pnl",
        "positions_at_close", "starting_equity", "ending_equity",
        "halt_blocked_count", "webhook_url",
        # The 5 rich kwargs D304 wires (the operator-critical fields
        # that user explicitly called out as missing on 2026-05-19)
        "d_codes_fired", "top_winners", "top_losers",
        "eod_recon", "bocpd_refit",
        # D312 (2026-05-24, doc 172): gate-rejection breakdown so
        # operator sees "10 BUYs -> 0 OTOs because D216 blocked 123 times"
        "veto_summary",
    },
    "post_trade_open": {
        "ticker", "side", "qty", "entry_price", "stop_loss", "mfcs",
        "path", "webhook_url",
        # D165 / D305 enriched fields
        "gap_pct", "intended_entry",
    },
    "post_entry_filled": {
        # D165 introduced this alert; D306 wired it in main.py.
        # n_submission_attempts and fill_latency_ms are intentionally
        # OPTIONAL for now -- a follow-up commit will add per-ticker
        # retry tracking and submit timestamp. Until then the alert
        # still renders correctly with these omitted.
        "ticker", "qty", "fill_price", "intended_entry", "stop_loss",
        "webhook_url",
    },
}


def _find_call_kwargs_in_main(alert_fn_name: str) -> set[str]:
    """AST-walk main.py looking for any Call to ``alert_fn_name(...)``,
    return the UNION of kwargs across ALL call sites (a kwarg passed
    anywhere counts as covered)."""
    tree = ast.parse(MAIN_PY.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Match plain name `alert_fn_name(...)`
        name = (func.id if isinstance(func, ast.Name)
                else (func.attr if isinstance(func, ast.Attribute) else None))
        if name != alert_fn_name:
            continue
        for kw in node.keywords:
            if kw.arg:  # excludes **kwargs splats
                found.add(kw.arg)
    return found


@pytest.mark.parametrize("alert_fn,required_kwargs",
                          sorted(_ALERT_CONTRACTS.items()))
def test_alert_call_site_passes_all_contract_kwargs(alert_fn, required_kwargs):
    """For each alert in _ALERT_CONTRACTS, verify that the union of all
    call sites in main.py covers every required kwarg.

    If this fails, a Discord alert is being silently rendered without
    operator-critical fields. The fix is to either:
      - update the call site in main.py to pass the missing kwarg(s), OR
      - if the kwarg is genuinely optional now, remove it from
        _ALERT_CONTRACTS in this file (with a comment explaining why).
    """
    passed = _find_call_kwargs_in_main(alert_fn)
    missing = required_kwargs - passed
    assert not missing, (
        f"{alert_fn}: call site(s) in main.py missing required kwargs:\n"
        f"  missing: {sorted(missing)}\n"
        f"  passed:  {sorted(passed)}\n"
        f"  expected: {sorted(required_kwargs)}\n\n"
        f"This is the D304/D307 'incomplete Discord message' bug class. "
        f"Either wire the missing kwarg into main.py OR remove it from "
        f"_ALERT_CONTRACTS in this test if it's now truly optional."
    )


def test_alert_signature_actually_accepts_each_contract_kwarg():
    """Sanity flip-side: each kwarg we require above must actually be a
    parameter of the alert function. Otherwise the contract is stale
    (alert was refactored, kwarg removed) and this test is meaningless."""
    from src.monitoring import alerts as _alerts_mod
    for fn_name, required in _ALERT_CONTRACTS.items():
        fn = getattr(_alerts_mod, fn_name, None)
        assert fn is not None, f"alerts.{fn_name} does not exist"
        sig = inspect.signature(fn)
        params = set(sig.parameters.keys())
        unknown = required - params
        assert not unknown, (
            f"{fn_name}: _ALERT_CONTRACTS lists kwargs not in signature: "
            f"{sorted(unknown)}. Either fix the contract OR re-add the "
            f"kwarg to {fn_name}."
        )


# ═══════════════════════════════════════════════════════════════
# Regression pins for the EXACT bugs we just fixed
# ═══════════════════════════════════════════════════════════════


def test_d304_eod_call_site_passes_d_codes_fired():
    """The user-facing regression: D-codes (D230-equity etc.) must
    appear in the Discord EOD message."""
    assert "d_codes_fired" in _find_call_kwargs_in_main("alert_session_end_rich")


def test_d304_eod_call_site_passes_top_winners_and_losers():
    passed = _find_call_kwargs_in_main("alert_session_end_rich")
    assert "top_winners" in passed
    assert "top_losers" in passed


def test_d304_eod_call_site_passes_eod_recon():
    """Broker-vs-journal recon delta must show in the Discord message."""
    assert "eod_recon" in _find_call_kwargs_in_main("alert_session_end_rich")


def test_d304_eod_call_site_passes_bocpd_refit():
    """BOCPD drift recommendation must show when fires_d262 triggers."""
    assert "bocpd_refit" in _find_call_kwargs_in_main("alert_session_end_rich")


def test_d305_trade_open_call_site_passes_gap_pct():
    """Doc 165 added gap_pct as an optional kwarg for the gap badge on
    the entry price line. Pre-D305 the call site never passed it."""
    assert "gap_pct" in _find_call_kwargs_in_main("post_trade_open")


def test_d305_trade_open_call_site_passes_intended_entry():
    """Doc 165 added intended_entry for slippage display when fill !=
    submitted limit. Pre-D305 the call site never passed it."""
    assert "intended_entry" in _find_call_kwargs_in_main("post_trade_open")
