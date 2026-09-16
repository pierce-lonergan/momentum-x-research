"""doc 282: loser-re-entry ban (env-gated, default OFF) — regression tests.
The gate must: (1) be a no-op when unset/0/invalid; (2) ban a ticker with a recent realized
loss inside the window; (3) NOT ban outside the window, on wins, or on other tickers;
(4) never raise on a missing/corrupt trade_results file (fail-open = trade)."""
import json
import os
from datetime import date, timedelta

import pytest

from src.data.alpaca_client import _check_reentry_ban


@pytest.fixture
def trade_results(tmp_path, monkeypatch):
    """Point the ban's repo-relative data path at a temp tree."""
    import src.data.alpaca_client as mod
    root = tmp_path / "repo"
    (root / "data").mkdir(parents=True)
    (root / "src" / "data").mkdir(parents=True)
    fake_file = root / "src" / "data" / "alpaca_client.py"
    fake_file.write_text("# stub")
    monkeypatch.setattr(mod, "__file__", str(fake_file))
    path = root / "data" / "trade_results.jsonl"

    def write(rows):
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return write


def _row(ticker, pnl, days_ago):
    d = (date.today() - timedelta(days=days_ago)).isoformat()
    return {"ticker": ticker, "pnl": pnl, "session_date": d,
            "entry_time": f"{d}T14:00:00+00:00", "exit_time": f"{d}T15:00:00+00:00"}


def test_off_by_default(monkeypatch, trade_results):
    trade_results([_row("XXXX", -500, 1)])
    monkeypatch.delenv("MOMENTUM_REENTRY_BAN_DAYS", raising=False)
    assert _check_reentry_ban("XXXX") is None


def test_invalid_env_is_off(monkeypatch, trade_results):
    trade_results([_row("XXXX", -500, 1)])
    monkeypatch.setenv("MOMENTUM_REENTRY_BAN_DAYS", "banana")
    assert _check_reentry_ban("XXXX") is None
    monkeypatch.setenv("MOMENTUM_REENTRY_BAN_DAYS", "0")
    assert _check_reentry_ban("XXXX") is None


def test_bans_recent_loss(monkeypatch, trade_results):
    trade_results([_row("XXXX", -500, 3)])
    monkeypatch.setenv("MOMENTUM_REENTRY_BAN_DAYS", "10")
    resp = _check_reentry_ban("XXXX")
    assert resp is not None
    assert resp["status"] == "halted_by_operator"
    assert "REENTRY_BAN" in resp["halt_reason"] or "MOMENTUM_REENTRY_BAN" in resp["halt_reason"]
    assert resp["symbol"] == "XXXX"
    assert resp["id"].startswith("reentry-ban-")


def test_no_ban_outside_window(monkeypatch, trade_results):
    trade_results([_row("XXXX", -500, 15)])
    monkeypatch.setenv("MOMENTUM_REENTRY_BAN_DAYS", "10")
    assert _check_reentry_ban("XXXX") is None


def test_no_ban_on_win_or_scratch(monkeypatch, trade_results):
    trade_results([_row("XXXX", +500, 2), _row("XXXX", -0.5, 2)])
    monkeypatch.setenv("MOMENTUM_REENTRY_BAN_DAYS", "10")
    assert _check_reentry_ban("XXXX") is None


def test_other_ticker_unaffected(monkeypatch, trade_results):
    trade_results([_row("XXXX", -500, 2)])
    monkeypatch.setenv("MOMENTUM_REENTRY_BAN_DAYS", "10")
    assert _check_reentry_ban("YYYY") is None


def test_missing_file_fails_open(monkeypatch, tmp_path):
    import src.data.alpaca_client as mod
    root = tmp_path / "repo2"
    (root / "src" / "data").mkdir(parents=True)
    fake = root / "src" / "data" / "alpaca_client.py"
    fake.write_text("# stub")
    monkeypatch.setattr(mod, "__file__", str(fake))
    monkeypatch.setenv("MOMENTUM_REENTRY_BAN_DAYS", "10")
    assert _check_reentry_ban("XXXX") is None  # no data/ dir at all


def test_corrupt_lines_fail_open(monkeypatch, trade_results, tmp_path):
    import src.data.alpaca_client as mod
    # corrupt file: garbage lines + one valid loss
    root_file = mod.__file__
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(root_file))), "data", "trade_results.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{not json}\n")
        f.write(json.dumps(_row("XXXX", -500, 2)) + "\n")
    monkeypatch.setenv("MOMENTUM_REENTRY_BAN_DAYS", "10")
    resp = _check_reentry_ban("XXXX")
    assert resp is not None  # valid line still honored, garbage skipped
