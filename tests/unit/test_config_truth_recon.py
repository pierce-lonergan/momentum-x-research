"""doc 285 gaps #5/#6/#12 — regression tests for the CONFIG-TRUTH recon sentinel
(scripts/config_truth_recon.py) and the LLM primary-dead fail-loud (src/agents/base.py).

The sizing tests encode the REAL 2026-07-06 drift to the share: RIVN 1552 @ $19.31
on ~$192.5K equity = 15.57% of equity against a 5% declared intent (doc-285 gap #3,
"the doc-282 sizing knobs were dead letters"). If the breach math ever loosens to
where that fill passes, these tests fail.

The base.py tests FAIL on the pre-doc-285 behavior: a dead primary model
(Together de-serverless reject on every call) with a healthy fallback chain
previously produced ZERO incident-bus alarms all session.
"""
from __future__ import annotations

import asyncio
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import config_truth_recon as ctr  # noqa: E402


# ── fixtures: the real 2026-07-06 session shapes ─────────────────────────────

_ENV_FILE_TEXT = """\
# doc-285 corrected package
LLM_TIER1_MODEL=Qwen/Qwen3.5-397B-A17B
MOMENTUM_T2_WIDE_PCT=0.75            # doc 266 prereg promotion FIRED
EXEC_TIER1_POSITION_PCT=0.05         # D150 tier1 was 50% (!) -> 5%
EXEC_TIER2_POSITION_PCT=0.05         # was 30% -> 5%
EXEC_TIER3_POSITION_PCT=0.05         # was 15% -> 5% (the cap RIVN actually hit)
KELLY_TIER1_RISK_PCT=0.0067          # was 2.0% -> 0.67%
KELLY_TIER2_RISK_PCT=0.0067
KELLY_TIER3_RISK_PCT=0.0067
KELLY_TIER4_RISK_PCT=0.0067
"""

_LOG_TEXT = """\
09:30:47 | src.execution.t2_arm_assignment | INFO  | D310.T2 ARM ASSIGNED CWD: arm=tight_stop qty_mult=1.00 (MOMENTUM_T2_ENABLED=1)
09:30:48 | src.execution.t2_arm_assignment | INFO  | D310.T2 ARM ASSIGNED DSY: arm=wide_stop qty_mult=0.50 (MOMENTUM_T2_ENABLED=1)
09:30:50 | src.execution.t2_arm_assignment | INFO  | D310.T2 ARM ASSIGNED YRD: arm=wide_stop qty_mult=0.50 (MOMENTUM_T2_ENABLED=1)
09:30:50 | src.execution.t2_arm_assignment | INFO  | D310.T2 ARM ASSIGNED RIVN: arm=tight_stop qty_mult=1.00 (MOMENTUM_T2_ENABLED=1)
09:34:58 | src.execution.t2_arm_assignment | INFO  | D310.T2 ARM ASSIGNED CWD: arm=tight_stop qty_mult=1.00 (MOMENTUM_T2_ENABLED=1)
10:01:35 | momentum_x           | INFO  | D278 BAR-1 SKIPPED Phase3 RIVN: exit_policy=t1_next_open age=61s (carrying overnight via D86/D91 next-open path)
10:15:43 | src.execution.post_fill_handler | INFO  | D278 BAR-1 SKIPPED RESCAN LUCY: exit_policy=t1_next_open (position will carry; close-at-next-open via D91)
"""

_REG_TEXT = """\

HKEY_CURRENT_USER\\Environment
    Path    REG_EXPAND_SZ    <local-path>
    TEMP    REG_EXPAND_SZ    %USERPROFILE%\\AppData\\Local\\Temp
    OneDrive    REG_EXPAND_SZ    <local-path>
    MOMENTUM_T2_ENABLED    REG_SZ    1
    OPS_OPERATOR_ENABLED    REG_SZ    0
    EXEC_FAKE_SECRET_KEY    REG_SZ    hunter2
"""


# ── intent parsing ────────────────────────────────────────────────────────────

class TestIntentParsing:
    def test_env_file_inline_comments_stripped(self):
        env = ctr.parse_env_file(_ENV_FILE_TEXT)
        assert env["MOMENTUM_T2_WIDE_PCT"] == "0.75"
        assert env["EXEC_TIER3_POSITION_PCT"] == "0.05"

    def test_intent_is_min_of_declared_tiers(self):
        env = ctr.parse_env_file(_ENV_FILE_TEXT)
        assert ctr.intent_position_pct(env) == pytest.approx(0.05)
        assert ctr.intent_risk_pct(env) == pytest.approx(0.0067)
        assert ctr.intent_t2_wide(env) == pytest.approx(0.75)

    def test_intent_defaults_when_env_silent(self):
        assert ctr.intent_position_pct({}) == pytest.approx(0.05)
        assert ctr.intent_risk_pct({}) == pytest.approx(0.0067)
        assert ctr.intent_t2_wide({}) == pytest.approx(0.50)
        assert ctr.intent_exit_policy({}) == "bar1_legacy"

    def test_intent_min_beats_a_loose_tier(self):
        env = {"EXEC_TIER1_POSITION_PCT": "0.50", "EXEC_TIER3_POSITION_PCT": "0.05"}
        assert ctr.intent_position_pct(env) == pytest.approx(0.05)


# ── sizing breach math: the 2026-07-06 proof ─────────────────────────────────

class TestSizingBreach:
    def test_rivn_15pct_fill_vs_5pct_intent_breaches(self):
        """The real drift: RIVN 1552sh @ $19.31, OTO stop leg $18.22, equity
        $192,539 -> 15.57% notional vs 5% intent AND 0.879% risk vs 0.67%."""
        ev = ctr.evaluate_fill(
            {"symbol": "RIVN", "qty": 1552, "price": 19.31,
             "equity": 192_539.20, "stop_price": 18.22},
            intent_pos=0.05, intent_risk=0.0067)
        assert ev["notional_pct"] == pytest.approx(0.15565, abs=1e-4)
        kinds = " ".join(ev["breaches"])
        assert "POSITION_PCT RIVN" in kinds
        assert "RISK_PCT RIVN" in kinds

    def test_compliant_fill_passes(self):
        ev = ctr.evaluate_fill(
            {"symbol": "OK", "qty": 960, "price": 10.0,
             "equity": 200_000.0, "stop_price": 9.875},
            intent_pos=0.05, intent_risk=0.0067)
        assert ev["breaches"] == []

    def test_chase_fill_within_tolerance_passes(self):
        # doc-285 gap #9: a 5% cap can legitimately fill ~5.4% (stale anchor).
        ev = ctr.evaluate_fill(
            {"symbol": "CHASE", "qty": 1000, "price": 10.8,
             "equity": 200_000.0, "stop_price": 10.65},
            intent_pos=0.05, intent_risk=0.0067)
        assert ev["breaches"] == []

    def test_missing_stop_is_a_breach(self):
        # the "internal_stop but no stop_order_id" class must be loud, not silent
        ev = ctr.evaluate_fill(
            {"symbol": "GHOST", "qty": 100, "price": 5.0,
             "equity": 200_000.0, "stop_price": None},
            intent_pos=0.05, intent_risk=0.0067)
        assert any(b.startswith("NO_STOP_FOUND GHOST") for b in ev["breaches"])


# ── T2 wide-arm fraction ─────────────────────────────────────────────────────

class TestT2WideFraction:
    def test_assignments_dedupe_per_ticker(self):
        arms = ctr.parse_arm_assignments(_LOG_TEXT)
        assert arms == {"CWD": "tight_stop", "DSY": "wide_stop",
                        "YRD": "wide_stop", "RIVN": "tight_stop"}

    def test_session_noise_within_band_passes(self):
        arms = ctr.parse_arm_assignments(_LOG_TEXT)  # 2/4 wide vs 0.75 intent
        assert ctr.check_wide_fraction(arms, intent=0.75)["breaches"] == []

    def test_wide_arm_silently_dead_breaches(self):
        arms = {t: "tight_stop" for t in "ABCDEF"}  # 0/6 wide vs 0.75 intent
        out = ctr.check_wide_fraction(arms, intent=0.75)
        assert out["breaches"] and "T2_WIDE_FRACTION" in out["breaches"][0]

    def test_small_n_never_alarms(self):
        arms = {"A": "tight_stop", "B": "tight_stop"}
        assert ctr.check_wide_fraction(arms, intent=0.75)["breaches"] == []


# ── exit-policy applied vs intended ──────────────────────────────────────────

class TestExitPolicy:
    def test_observed_t1_next_open_vs_declared_bar1_breaches(self):
        """THE 2026-07-06 drift: log shows t1_next_open, .env declares nothing
        -> intended bar1_legacy -> loud drift. A user-scope override must not
        be able to bless itself."""
        observed = ctr.parse_exit_policies(_LOG_TEXT)
        assert observed == ["t1_next_open"]
        out = ctr.check_exit_policy(observed, intended="bar1_legacy")
        assert out["breaches"] and "EXIT_POLICY" in out["breaches"][0]

    def test_matching_policy_passes(self):
        out = ctr.check_exit_policy(["t1_next_open"], intended="t1_next_open")
        assert out["breaches"] == []

    def test_no_observations_never_alarms(self):
        out = ctr.check_exit_policy([], intended="bar1_legacy")
        assert out["breaches"] == []


# ── the invisible user-scope surface ─────────────────────────────────────────

class TestUserScopeEnv:
    def test_reg_parse_filters_to_momentum_prefixes(self):
        vars_ = ctr.parse_reg_query(_REG_TEXT)
        assert "MOMENTUM_T2_ENABLED" in vars_
        assert "EXEC_FAKE_SECRET_KEY" in vars_
        assert "Path" not in vars_ and "TEMP" not in vars_
        assert "OPS_OPERATOR_ENABLED" not in vars_  # OPS_ not a sized-surface prefix

    def test_unmirrored_user_scope_var_breaches(self):
        out = ctr.check_user_scope({"MOMENTUM_T2_ENABLED": "1"}, env_file={})
        assert out["breaches"] and "USER_SCOPE_ENV MOMENTUM_T2_ENABLED" in out["breaches"][0]

    def test_verbatim_mirrored_var_passes(self):
        out = ctr.check_user_scope({"MOMENTUM_T2_ENABLED": "1"},
                                   env_file={"MOMENTUM_T2_ENABLED": "1"})
        assert out["breaches"] == []

    def test_mirrored_but_different_value_breaches(self):
        out = ctr.check_user_scope({"MOMENTUM_T2_WIDE_PCT": "1.0"},
                                   env_file={"MOMENTUM_T2_WIDE_PCT": "0.75"})
        assert len(out["breaches"]) == 1

    def test_secrety_names_are_redacted(self):
        out = ctr.check_user_scope({"EXEC_FAKE_SECRET_KEY": "hunter2"}, env_file={})
        assert "hunter2" not in out["breaches"][0]
        assert out["vars"]["EXEC_FAKE_SECRET_KEY"]["value"] == "<redacted>"


# ── instrument freshness (gap #12) ───────────────────────────────────────────

class TestInstrumentFreshness:
    def test_all_dark_when_nothing_exists(self, tmp_path):
        out = ctr.check_instruments(tmp_path, "2026-07-06", time.time())
        assert len(out["breaches"]) == 5   # doc 292 adds rv_forward_ledger
        assert all("INSTRUMENT_DARK" in b for b in out["breaches"])

    def test_all_fresh_passes(self, tmp_path):
        import json as _json
        (tmp_path / "data" / "reports").mkdir(parents=True)
        (tmp_path / "data" / "research").mkdir(parents=True)
        (tmp_path / "logs").mkdir()
        # doc 287: a genuinely-fresh rocket-gate row must have actually MEASURED tickets
        # (n_filled>0, no_bar_data absent) -- a present-but-empty row is DARK, not fresh.
        (tmp_path / "data" / "reports" / "rocket_gate_ledger.jsonl").write_text(
            _json.dumps({"date": "2026-07-06", "n_gated": 12, "n_filled": 12,
                         "n_unmeasured": 0, "no_bar_data": False, "delta_hold_vs_bar1_usd": 3.2})
            + "\n", encoding="utf-8")
        (tmp_path / "data" / "reports" / "posture_delta_trend.jsonl").write_text(
            _json.dumps({"date": "2026-07-06"}) + "\n", encoding="utf-8")
        (tmp_path / "logs" / "kalshi_shadow_doc284.log").write_text("ok\n", encoding="utf-8")
        (tmp_path / "data" / "research" / "rocket_watchlist_log.jsonl").write_text(
            "{}\n", encoding="utf-8")
        # doc 292: rv forward ledger — a MEASURED row (n_names>=30) for the date
        (tmp_path / "data" / "reports" / "rv_forward_ledger.jsonl").write_text(
            _json.dumps({"date": "2026-07-06", "n_names": 140, "qlike_har": 0.2,
                         "qlike_challenger": 0.19}) + "\n", encoding="utf-8")
        out = ctr.check_instruments(tmp_path, "2026-07-06", time.time())
        assert out["breaches"] == []

    def test_present_but_unmeasured_rocket_row_is_dark(self, tmp_path):
        """doc 287: the exact 7/6-7/9 failure -- gated candidates but 0 measured (no_bar_data). The
        OLD check reported this 'fresh'; it must now fail-loud as DARK."""
        import json as _json
        (tmp_path / "data" / "reports").mkdir(parents=True)
        (tmp_path / "data" / "reports" / "rocket_gate_ledger.jsonl").write_text(
            _json.dumps({"date": "2026-07-06", "n_gated": 23, "n_filled": 0,
                         "n_unmeasured": 23, "no_bar_data": True}) + "\n", encoding="utf-8")
        out = ctr.check_instruments(tmp_path, "2026-07-06", time.time())
        assert any("rocket_gate_ledger" in b and "DARK" in b for b in out["breaches"])

    def test_ledger_missing_the_session_row_is_dark(self, tmp_path):
        import json as _json
        (tmp_path / "data" / "reports").mkdir(parents=True)
        (tmp_path / "data" / "reports" / "rocket_gate_ledger.jsonl").write_text(
            _json.dumps({"date": "2026-07-02"}) + "\n", encoding="utf-8")
        out = ctr.check_instruments(tmp_path, "2026-07-06", time.time())
        assert any("rocket_gate_ledger" in b for b in out["breaches"])


# ── equity join helper ───────────────────────────────────────────────────────

class TestEquityAt:
    def test_last_point_at_or_before_fill(self):
        ts = [100, 200, 300]
        eq = [1000.0, 1100.0, 1200.0]
        assert ctr.equity_at(250, ts, eq) == 1100.0
        assert ctr.equity_at(300, ts, eq) == 1200.0


# ── doc 285 gap #5: LLM primary-dead fail-loud (src/agents/base.py) ──────────

def _make_stub_agent(**kw):
    """Minimal concrete BaseAgent (built lazily to keep collection light)."""
    from src.agents import base as _base
    from src.core.models import AgentSignal

    class Stub(_base.BaseAgent):
        @property
        def agent_id(self):
            return "stub_agent"

        @property
        def system_prompt(self):
            return "sys"

        def build_user_prompt(self, **kwargs):
            return "user"

        def parse_response(self, raw, ticker):
            return AgentSignal(
                agent_id="stub_agent", ticker=ticker,
                timestamp=datetime.now(timezone.utc),
                signal="NEUTRAL", confidence=0.5, reasoning="stub")

    return Stub(**kw)


@pytest.fixture()
def _llm_alarm_env(monkeypatch):
    """Isolate the module-level reject counter + breaker + capture emissions."""
    from src.agents import base
    from src.ops import incident_bus
    from src.utils.circuit_breaker import llm_breaker

    base._PRIMARY_REJECTS.clear()
    llm_breaker._state = llm_breaker.CLOSED
    llm_breaker._fail_count = 0
    llm_breaker._current_reset_timeout = llm_breaker._base_reset_timeout

    emitted: list[dict] = []

    def _capture(kind, severity="WARN", **kw):
        emitted.append({"kind": kind, "severity": severity, **kw})
        return True

    monkeypatch.setattr(incident_bus, "emit_incident", _capture)
    # no real sleeps between fallback attempts
    monkeypatch.setattr(base, "_llm_fallback_backoff_ms", lambda e: 0)
    yield emitted
    base._PRIMARY_REJECTS.clear()
    llm_breaker._state = llm_breaker.CLOSED
    llm_breaker._fail_count = 0


def _dead_primary(model_arg_ok: str | None = None):
    """_call_llm stub: primary model always 400s; optional fallback succeeds."""
    async def call(model, user_prompt):
        if model_arg_ok is not None and model == model_arg_ok:
            return '{"signal": "NEUTRAL", "confidence": 0.5, "reasoning": "ok"}', 5.0
        raise ValueError(
            "litellm.BadRequestError: Together_aiException - Unable to access "
            "non-serverless model Qwen/Qwen3.5-397B-A17B")
    return call


class TestLLMPrimaryDeadFailLoud:
    def test_five_consecutive_rejects_emit_critical(self, _llm_alarm_env):
        """FAILS on pre-doc-285 base.py: dead primary, no fallback -> the old
        code returned NEUTRAL signals forever with zero incident emissions."""
        agent = _make_stub_agent(model="Qwen/Qwen3.5-397B-A17B", provider="together_ai")
        agent._call_llm = _dead_primary()
        for _ in range(5):
            asyncio.run(agent.analyze("RIVN"))
        dead = [e for e in _llm_alarm_env if e["kind"] == "LLM_PRIMARY_MODEL_DEAD"]
        assert len(dead) == 1
        assert dead[0]["severity"] == "CRITICAL"
        assert dead[0]["context"]["consecutive_primary_rejects"] == 5
        assert "Qwen3.5-397B" in dead[0]["context"]["model"]

    def test_fallback_success_does_not_silence_dead_primary(self, _llm_alarm_env):
        """THE 2026-07-06 mode: every primary call rejected, every fallback
        succeeded, breaker kept resetting -> zero alarms all session."""
        agent = _make_stub_agent(model="Qwen/Qwen3.5-397B-A17B", provider="together_ai",
                           fallback_model="Qwen/Qwen2.5-7B-Instruct-Turbo",
                           fallback_provider="together_ai")
        agent._call_llm = _dead_primary(model_arg_ok=agent.fallback_model)
        for _ in range(5):
            sig = asyncio.run(agent.analyze("RIVN"))
            assert "AGENT_ERROR" not in sig.flags  # fallback genuinely served
        dead = [e for e in _llm_alarm_env if e["kind"] == "LLM_PRIMARY_MODEL_DEAD"]
        assert len(dead) == 1 and dead[0]["severity"] == "CRITICAL"

    def test_primary_success_resets_the_counter(self, _llm_alarm_env):
        agent = _make_stub_agent(model="Qwen/Qwen3.5-397B-A17B", provider="together_ai")
        dead_call = _dead_primary()

        async def ok_call(model, user_prompt):
            return '{"signal": "NEUTRAL", "confidence": 0.5, "reasoning": "ok"}', 5.0

        for _ in range(4):
            agent._call_llm = dead_call
            asyncio.run(agent.analyze("RIVN"))
        agent._call_llm = ok_call
        asyncio.run(agent.analyze("RIVN"))  # heals -> counter resets
        for _ in range(4):
            agent._call_llm = dead_call
            asyncio.run(agent.analyze("RIVN"))
        dead = [e for e in _llm_alarm_env if e["kind"] == "LLM_PRIMARY_MODEL_DEAD"]
        assert dead == []
