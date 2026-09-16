"""MOMENTUM-X Tests: doc 298 — the SEVP runner must implement its own frozen prereg.

Node ID: tests.unit.test_doc298_sevp_prereg_conformity

This is the doc-296 rule ("a prereg target must be EXECUTABLE — every armed gate ships a synthetic
fixture the runner must reproduce before the gate may be consulted") applied for the first time.

Doc 296 caught a hash-frozen prereg specifying h=21 in PROSE being executed by a runner coded at h=1;
the hash covered the words, every gate passed, and only a skeptic fleet caught it. The doc-298 cull
found the SEVP runner about to repeat that failure in four places at once. SEVP had not yet unblinded
(coverage 205/300), so the defects were fixed while blind — but nothing prevents them recurring, and
prose cannot pin code. These tests can.

Each test names the prereg line it enforces (`scripts/_doc294_PREREG.md`, sha256 ae62ea59..., frozen
at commit 5c2b0e7):

  L27  "G1 ... mean net/event > 0 with event-blocked bootstrap (B=5000) CI95 excluding 0"
  L29  "... both calendar halves of the event sample"
  L30  "G2 CONDITIONER SUB-GATE (does OUR signal add?)"
  L34  "... exceeds 50% of cumulative net in magnitude, the best available verdict is FRAGILE-PASS"

The fixture is built so that the CORRECT answer differs from the answer the old code gave, which is
what makes it a regression test rather than a restatement.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
_SPEC = importlib.util.spec_from_file_location(
    "sevp", _ROOT / "scripts" / "_doc294_sevp_pipeline.py")
SEVP = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(SEVP)

PREREG = (_ROOT / "scripts" / "_doc294_PREREG.md").read_text(encoding="utf-8")


def _contract(tk: str, exp: str, cp: str, strike: float) -> str:
    y, m, d = exp.split("-")
    return f"O:{tk}{y[2:]}{m}{d}{cp}{int(round(strike * 1000)):08d}"


def _fixture(tmp_path, n_dates=8, per_date=8, fat_tail=False):
    """Events whose CALENDAR order is the reverse of their ALPHABETICAL order.

    Early dates get late-alphabet tickers and vice versa, so a runner that splits on file order
    (which `pull_events` writes sorted by ticker) produces the halves backwards. The two halves are
    given deliberately different gross returns so the error is visible in the number, not just in
    the ordering.
    """
    from datetime import date as _date, timedelta as _td
    events, prices = [], []
    base = _date(2026, 3, 3)
    dates = [(base + _td(days=7 * i)).isoformat() for i in range(n_dates)]
    for di, d in enumerate(dates):
        early = di < n_dates // 2
        # early calendar half -> tickers starting with Z; late half -> tickers starting with A
        prefix = "Z" if early else "A"
        for k in range(per_date):
            tk = f"{prefix}{chr(ord('A') + k)}{di}"
            # prereg contract rule: 5 <= DTE <= 40, nearest 21 — the fixture must satisfy it
            exp = (_date.fromisoformat(d) + _td(days=21)).isoformat()
            strike = 100.0
            # premium falls a lot in the early half (profitable short straddle), a little in the late
            p0, p1 = (10.0, 4.0) if early else (10.0, 9.0)
            if fat_tail and di == 0 and k == 0:
                p1 = 200.0          # one catastrophic event dominating the cumulative net
            t0 = (_date.fromisoformat(d) - _td(days=1)).isoformat()
            t1 = (_date.fromisoformat(d) + _td(days=1)).isoformat()
            events.append({"ticker": tk, "ts": f"{d}T12:00:00", "date": d, "source": "fixture"})
            for cp, frac in (("C", 0.5), ("P", 0.5)):
                c = _contract(tk, exp, cp, strike)
                for dd, px in ((t0, p0 * frac), (t1, p1 * frac)):
                    prices.append({"date": dd, "ticker": tk, "contract": c, "cp": cp,
                                   "K": strike, "exp": exp, "close": px, "S": 100.0})
    ev_path = tmp_path / "earnings_events.jsonl"
    px_path = tmp_path / "option_eod.jsonl"
    # deliberately written in ALPHABETICAL order, exactly as pull_events() does
    ev_path.write_text("\n".join(json.dumps(r) for r in
                                 sorted(events, key=lambda r: (r["ticker"], r["ts"]))) + "\n",
                       encoding="utf-8")
    px_path.write_text("\n".join(json.dumps(r) for r in prices) + "\n", encoding="utf-8")
    return ev_path, px_path


@pytest.fixture
def armed(tmp_path, monkeypatch):
    ev, px = _fixture(tmp_path)
    monkeypatch.setattr(SEVP, "EVENTS", ev)
    monkeypatch.setattr(SEVP, "PRICES", px)
    monkeypatch.setattr(SEVP, "OUT", tmp_path)
    monkeypatch.setattr(SEVP, "N_UNBLIND", 1)      # fixture-only; production stays at 300
    return SEVP.run_gates()


class TestPreregTextIsUnchanged:
    """If the frozen prereg is edited, these tests are testing the wrong contract."""

    def test_the_four_clauses_are_still_in_the_prereg(self):
        assert "event-blocked bootstrap" in PREREG
        assert "calendar halves" in PREREG
        assert "G2 CONDITIONER SUB-GATE" in PREREG
        assert "FRAGILE-PASS" in PREREG


class TestCalendarHalves:
    """prereg L29 — halves are CALENDAR halves, not file/alphabetical order."""

    def test_halves_follow_the_calendar_not_the_alphabet(self, armed):
        r = armed["cost_5pct"]
        h1, h2 = r["half1_mean_calendar"], r["half2_mean_calendar"]
        assert h1 is not None and h2 is not None
        # fixture: early CALENDAR half is the profitable one (premium 10 -> 4).
        assert h1 > h2, (
            f"calendar half1 ({h1}) must be the profitable early half, not the alphabetical one; "
            "getting h1 < h2 means the runner split on file order again"
        )

    def test_both_halves_are_populated(self, armed):
        r = armed["cost_5pct"]
        assert r["n_half1"] > 0 and r["n_half2"] > 0


class TestEventBlockedBootstrap:
    """prereg L27 — the bootstrap resamples event DATES, not individual events."""

    def test_ci_is_reported_as_event_blocked(self, armed):
        assert "ci95_event_blocked" in armed["cost_5pct"]
        assert "ci95" not in armed["cost_5pct"], "the old IID key must be gone, not shadowed"

    def test_blocked_ci_is_wider_than_iid_on_clustered_data(self, armed, tmp_path, monkeypatch):
        """With events clustered on few dates, ignoring the clustering understates the CI.

        This is the doc-297 law-4 defect (design effect 6.06, SE inflated 2.46x) in the SEVP gate.
        """
        import numpy as np
        lo, hi = armed["cost_5pct"]["ci95_event_blocked"]
        blocked_width = hi - lo
        # reconstruct the same nets and bootstrap them IID, as the old code did
        prices = SEVP._load_prices()
        rows = [json.loads(l) for l in SEVP.EVENTS.read_text(encoding="utf-8").splitlines() if l.strip()]
        nets = []
        for ev in rows:
            legs = SEVP._event_legs(ev, prices)
            if not legs:
                continue
            p0 = legs["legs"]["C"][0] + legs["legs"]["P"][0]
            p1 = legs["legs"]["C"][1] + legs["legs"]["P"][1]
            if p0 > 0:
                nets.append((p0 - p1) / p0 - 0.05)
        v = np.array(nets)
        rng = np.random.default_rng(SEVP.SEED)
        boots = [rng.choice(v, len(v), replace=True).mean() for _ in range(2000)]
        iid_width = float(np.percentile(boots, 97.5) - np.percentile(boots, 2.5))
        assert blocked_width > iid_width, (
            f"event-blocked CI ({blocked_width:.5f}) must be wider than IID ({iid_width:.5f}) when "
            "events cluster on dates; if it is not, the blocking is not happening"
        )


class TestFatTailRule:
    """prereg L34 — a single event over 50% of cumulative net caps the verdict at FRAGILE-PASS."""

    def test_flag_is_present_and_false_on_a_balanced_sample(self, armed):
        r = armed["cost_5pct"]
        assert "fat_tail_flag" in r and "largest_single_event_share_of_cum_net" in r
        assert r["fat_tail_flag"] is False

    def test_flag_fires_when_one_event_dominates(self, tmp_path, monkeypatch):
        ev, px = _fixture(tmp_path, fat_tail=True)
        monkeypatch.setattr(SEVP, "EVENTS", ev)
        monkeypatch.setattr(SEVP, "PRICES", px)
        monkeypatch.setattr(SEVP, "OUT", tmp_path)
        monkeypatch.setattr(SEVP, "N_UNBLIND", 1)
        r = SEVP.run_gates()["cost_5pct"]
        assert r["fat_tail_flag"] is True, (
            f"one event at {r['largest_single_event_share_of_cum_net']:.1%} of cumulative net must "
            "raise the fat-tail flag"
        )


class TestG2Exists:
    """prereg L30-32 — G2 must be evaluated, and must never be silently absent."""

    def test_g2_is_reported(self, armed):
        assert "G2" in armed, "G2 was absent from the runner entirely before doc 298"

    def test_g2_refuses_rather_than_passing_when_it_cannot_be_computed(self, armed):
        """A conditioner that cannot be evaluated must REFUSE, never read as a pass."""
        g2 = armed["G2"]
        if "REFUSED" in g2:
            assert "not evaluated" in g2["REFUSED"] or "below n=30" in g2["REFUSED"] \
                or "missing" in g2["REFUSED"]
        else:
            # if it did evaluate, it must carry an explicit pass/fail per cost line
            assert any(k.startswith("cost_") for k in g2)
