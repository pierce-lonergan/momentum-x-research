"""MOMENTUM-X Tests: doc 297 — the trial registry (machine-readable multiplicity counter).

Node ID: tests.unit.test_doc297_trial_registry

The registry exists because a self-feeding hypothesis loop is a multiple-testing machine. These tests
pin the four properties that make it worth having:

  1. the promotion bar RISES with the number of trials (and falls with sample length);
  2. a result cannot be recorded against a trial that was never registered;
  3. the ledger is hash-chained, so tampering is detectable;
  4. promote() is refused to any automated caller (doc 276: self-audit has no fixed point).

Reference values are cross-checked against the Bailey/Lopez de Prado figures quoted independently by
the doc-297 adversary agent: E[max Sharpe] = 1.07 at N=35 and 1.63 at N=1000, on 4 years of daily data.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
_SPEC = importlib.util.spec_from_file_location("trial_registry", _ROOT / "scripts" / "trial_registry.py")
TR = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(TR)


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    """Point the registry at a throwaway ledger so tests never touch the real one."""
    p = tmp_path / "trial_registry.jsonl"
    monkeypatch.setattr(TR, "LEDGER", p)
    return p


class TestTheBarRisesWithLooking:
    def test_reproduces_published_expected_max_sharpe(self):
        """Cross-check against the independently-quoted Bailey/LdP reference figures."""
        assert TR.expected_max_sharpe(35, n_obs=1008) == pytest.approx(1.07, abs=0.02)
        assert TR.expected_max_sharpe(1000, n_obs=1008) == pytest.approx(1.63, abs=0.02)

    def test_bar_is_monotone_increasing_in_trials(self):
        bars = [TR.expected_max_sharpe(n, n_obs=252) for n in (1, 10, 35, 100, 1000)]
        assert bars == sorted(bars), f"bar must rise with trial count; got {bars}"
        assert bars[-1] > bars[0] * 2, "1000 trials should demand a materially higher Sharpe than 1"

    def test_bar_falls_with_sample_length(self):
        """More data lowers the bar — this is the only honest way to earn a lower threshold."""
        short = TR.expected_max_sharpe(35, n_obs=252)
        long_ = TR.expected_max_sharpe(35, n_obs=2520)
        assert long_ < short / 2, f"10x data should roughly cut the bar by sqrt(10); {short} -> {long_}"

    def test_a_good_looking_sharpe_fails_after_many_trials(self):
        """The program's actual situation: ~60 trials, ~1 year of data, an observed Sharpe of 1.2."""
        res = TR.promotion_threshold(sharpe=1.2, n_obs=252, n_trials=60)
        assert res["expected_max_sharpe_under_null"] > 1.2
        assert res["clears"] is False, (
            "Sharpe 1.2 after 60 trials on one year of data must NOT clear — it is "
            "indistinguishable from the best of 60 coin flips"
        )

    def test_a_strong_result_on_long_data_can_clear(self):
        res = TR.promotion_threshold(sharpe=3.0, n_obs=2520, n_trials=60)
        assert res["clears"] is True, f"a genuinely strong long-sample result should clear; {res}"


class TestRegistrationDiscipline:
    def test_result_on_unregistered_trial_is_refused(self, ledger):
        with pytest.raises(SystemExit) as e:
            TR.record_result("T99999", sharpe=2.0, n_obs=500)
        assert "never registered" in str(e.value)

    def test_registration_assigns_increasing_ordinals(self, ledger):
        a = TR.register("fam-a", "hypothesis one")
        b = TR.register("fam-b", "hypothesis two")
        assert a["trial_ordinal"] == 1 and b["trial_ordinal"] == 2
        assert TR.trial_count() == 2

    def test_registering_raises_the_bar_for_everyone(self, ledger):
        """The point of the whole module: looking more makes the next claim harder."""
        before = TR.promotion_threshold(sharpe=1.5, n_obs=504)["expected_max_sharpe_under_null"]
        for i in range(30):
            TR.register("noise-family", f"speculative hypothesis {i}")
        after = TR.promotion_threshold(sharpe=1.5, n_obs=504)["expected_max_sharpe_under_null"]
        assert after > before, (
            "registering 30 more trials must raise the bar; otherwise the counter is decorative"
        )


class TestTamperEvidence:
    def test_clean_chain_verifies(self, ledger):
        TR.register("fam", "h1")
        TR.register("fam", "h2")
        assert TR.verify_chain()["ok"] is True

    def test_edited_row_is_detected(self, ledger):
        TR.register("fam", "h1")
        TR.register("fam", "h2")
        rows = [json.loads(l) for l in ledger.read_text(encoding="utf-8").splitlines() if l.strip()]
        rows[0]["hypothesis"] = "quietly reworded after the fact"
        ledger.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        chk = TR.verify_chain()
        assert chk["ok"] is False and chk["broken_at_index"] == 0


class TestNoSelfPromotion:
    def test_promote_is_refused(self, ledger):
        TR.register("fam", "h1")
        with pytest.raises(SystemExit) as e:
            TR.promote("T00001", adjudication_ref="i-promise-a-fleet-reviewed-it")
        msg = str(e.value)
        assert "not available to an automated caller" in msg
        assert "skeptic fleet" in msg
