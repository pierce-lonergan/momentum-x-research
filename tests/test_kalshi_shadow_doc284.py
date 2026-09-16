"""Tests for scripts/kalshi_shadow_doc284.py (doc-283 Step 4 shadow rig).

Pure-function coverage (price parse, LLM-JSON defense, fee math, day-blocked CI)
plus a monkeypatched end-to-end `score` run proving the settlement P&L/Brier math
without touching the network.
"""
import argparse
import importlib.util
import json
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "kalshi_shadow_doc284",
    Path(__file__).resolve().parents[1] / "scripts" / "kalshi_shadow_doc284.py",
)
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)


# ---------------------------------------------------------------- price parsing
def test_to_cents_legacy_int():
    assert mod.to_cents({"yes_bid": 42}, "yes_bid") == 42.0


def test_to_cents_dollars_string():
    assert mod.to_cents({"yes_bid_dollars": "0.1200"}, "yes_bid") == 12.0


def test_to_cents_missing_and_garbage():
    assert mod.to_cents({}, "yes_bid") is None
    assert mod.to_cents({"yes_bid_dollars": "abc"}, "yes_bid") is None


def test_to_float_fp_string():
    assert mod.to_float({"volume_fp": "110357.82"}, "volume") == pytest.approx(110357.82)
    assert mod.to_float({"volume": 7}, "volume") == 7.0
    assert mod.to_float({}, "volume") is None


# ---------------------------------------------------------------- LLM JSON defense
def test_parse_llm_json_clean():
    out = mod.parse_llm_json('{"p_yes": 0.62, "confidence": "med", "reasoning": "ok"}')
    assert out == {"p_yes": 0.62, "confidence": "med", "reasoning": "ok"}


def test_parse_llm_json_fenced_and_prose():
    out = mod.parse_llm_json('Sure!\n```json\n{"p_yes": 0.9, "confidence": "high", "reasoning": "x"}\n```')
    assert out is not None and out["p_yes"] == 0.9


def test_parse_llm_json_clamps_and_rejects():
    assert mod.parse_llm_json('{"p_yes": 1.7}')["p_yes"] == 1.0
    assert mod.parse_llm_json('{"p_yes": -0.2}')["p_yes"] == 0.0
    assert mod.parse_llm_json('{"p_yes": "maybe"}') is None
    assert mod.parse_llm_json("no json here") is None
    assert mod.parse_llm_json("") is None
    assert mod.parse_llm_json('{"probability": 0.5}') is None


# ---------------------------------------------------------------- fee math
def test_taker_fee_midpoint():
    # 0.07 * 0.5 * 0.5 dollars = 1.75 cents at a 50c fill
    assert mod.taker_fee_cents(50.0) == pytest.approx(2.0)  # ceil(0.07*0.5*0.5*100=1.75)->2 per published Kalshi schedule (fees round UP to next cent)


def test_taker_fee_skewed():
    assert mod.taker_fee_cents(12.0) == pytest.approx(1.0)  # ceil(0.7392)->1 cent (published schedule rounds up)


# ---------------------------------------------------------------- day-blocked CI
def test_day_blocked_ci_needs_five_days():
    assert mod.day_blocked_ci({"d1": [1.0], "d2": [2.0]}) is None


def test_day_blocked_ci_all_positive_days():
    pnl = {f"2026-07-{i:02d}": [10.0, 5.0] for i in range(1, 11)}
    lo, hi = mod.day_blocked_ci(pnl)
    assert lo > 0 and hi >= lo
    assert lo == pytest.approx(15.0) and hi == pytest.approx(15.0)  # constant days


def test_day_blocked_ci_mixed_sign_contains_zero():
    pnl = {f"2026-07-{i:02d}": [(-1) ** i * 50.0] for i in range(1, 13)}
    lo, hi = mod.day_blocked_ci(pnl)
    assert lo < 0 < hi


# ---------------------------------------------------------------- end-to-end score (no network)
def test_score_end_to_end_settlement_math(tmp_path, monkeypatch, capsys):
    fcast = tmp_path / "f.jsonl"
    score = tmp_path / "s.jsonl"
    rows = [
        {  # YES divergence trade, resolves yes -> win
            "ts": "2026-07-05T00:00:00Z", "market_ticker": "T-YESWIN",
            "p_llm": 0.80, "p_market_mid": 0.60, "edge": 0.20,
            "yes_bid": 58.0, "yes_ask": 62.0, "close_time": "2026-07-06T00:00:00Z",
            "title": "t1", "category": "Politics",
        },
        {  # NO divergence trade, resolves no -> win
            "ts": "2026-07-05T00:00:00Z", "market_ticker": "T-NOWIN",
            "p_llm": 0.10, "p_market_mid": 0.30, "edge": -0.20,
            "yes_bid": 28.0, "yes_ask": 32.0, "close_time": "2026-07-06T00:00:00Z",
            "title": "t2", "category": "Economics",
        },
        {  # inside threshold -> no trade, Brier only
            "ts": "2026-07-05T00:00:00Z", "market_ticker": "T-NOTRADE",
            "p_llm": 0.52, "p_market_mid": 0.50, "edge": 0.02,
            "yes_bid": 48.0, "yes_ask": 52.0, "close_time": "2026-07-06T00:00:00Z",
            "title": "t3", "category": "World",
        },
        {  # still open -> must stay pending
            "ts": "2026-07-05T00:00:00Z", "market_ticker": "T-OPEN",
            "p_llm": 0.5, "p_market_mid": 0.5, "edge": 0.0,
            "yes_bid": 49.0, "yes_ask": 51.0, "close_time": "2026-09-01T00:00:00Z",
            "title": "t4", "category": "World",
        },
        {  # voided settlement -> unscoreable
            "ts": "2026-07-05T00:00:00Z", "market_ticker": "T-VOID",
            "p_llm": 0.5, "p_market_mid": 0.5, "edge": 0.0,
            "yes_bid": 49.0, "yes_ask": 51.0, "close_time": "2026-07-06T00:00:00Z",
            "title": "t5", "category": "World",
        },
    ]
    fcast.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    monkeypatch.setattr(mod, "FCAST_PATH", fcast)
    monkeypatch.setattr(mod, "SCORE_PATH", score)

    fake_api = {
        "T-YESWIN": {"ticker": "T-YESWIN", "status": "finalized", "result": "yes"},
        "T-NOWIN": {"ticker": "T-NOWIN", "status": "finalized", "result": "no"},
        "T-NOTRADE": {"ticker": "T-NOTRADE", "status": "finalized", "result": "no"},
        "T-OPEN": {"ticker": "T-OPEN", "status": "active", "result": ""},
        "T-VOID": {"ticker": "T-VOID", "status": "finalized", "result": "void"},
    }

    def fake_get(client, path, params=None, retries=3):
        tickers = params["tickers"].split(",")
        return {"markets": [fake_api[t] for t in tickers if t in fake_api]}

    monkeypatch.setattr(mod, "kalshi_get", fake_get)
    assert mod.cmd_score(argparse.Namespace()) == 0

    out = {r["market_ticker"]: r for r in mod.read_jsonl(score)}
    assert set(out) == {"T-YESWIN", "T-NOWIN", "T-NOTRADE", "T-VOID"}  # T-OPEN pending

    w = out["T-YESWIN"]
    assert w["traded"] and w["side"] == "yes" and w["fill_price_cents"] == 62.0
    assert w["fee_cents"] == pytest.approx(2.0)  # ceil(1.6492)->2 cents (published schedule rounds up)
    assert w["pnl_cents"] == pytest.approx(100 - 62 - w["fee_cents"], abs=1e-3)
    assert w["brier_llm"] == pytest.approx(0.04) and w["brier_market"] == pytest.approx(0.16)

    nw = out["T-NOWIN"]
    assert nw["traded"] and nw["side"] == "no" and nw["fill_price_cents"] == 72.0
    assert nw["pnl_cents"] == pytest.approx(100 - 72 - nw["fee_cents"], abs=1e-3)

    nt = out["T-NOTRADE"]
    assert nt["traded"] is False and nt["pnl_cents"] is None
    assert nt["brier_llm"] == pytest.approx(0.52 ** 2)

    assert out["T-VOID"]["scoreable"] is False

    text = capsys.readouterr().out
    assert "PENDING-COLLECTION" in text  # n=3 < 200

    # idempotence: second run scores nothing new (only T-OPEN re-checked)
    assert mod.cmd_score(argparse.Namespace()) == 0
    assert len(mod.read_jsonl(score)) == 4
