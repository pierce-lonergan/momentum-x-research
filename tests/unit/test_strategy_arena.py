"""
MOMENTUM-X Tests: D119 Strategy Simulation Arena

Tests for StrategyProfile, entry re-evaluation, Elo tournament,
and counterfactual analysis.
"""

import pytest
import math
from pathlib import Path

from src.arena.strategy_arena import (
    StrategyProfile,
    StrategyVariant,
    StrategyArena,
    ArenaDataLoader,
    ArenaSession,
    ProfileSessionResult,
    CounterfactualOutcome,
    TradeCounterfactual,
    build_counterfactuals,
    recompute_entry,
    simulate_profile_session,
    DEFAULT_ELO,
)
from src.agents.prompt_arena import compute_expected_score, update_elo


# ── StrategyProfile Tests ────────────────────────────────────────


class TestStrategyProfile:
    """Verify profile construction and properties."""

    def test_baseline_no_overrides(self):
        """Baseline profile has no entry overrides."""
        p = StrategyProfile(name="baseline")
        assert not p.has_entry_overrides

    def test_entry_overrides_detected(self):
        """Profile with agent_weights has entry overrides."""
        p = StrategyProfile(
            name="news_heavy",
            agent_weights={"catalyst_news": 0.45, "technical": 0.15},
        )
        assert p.has_entry_overrides

    def test_mfcs_threshold_is_entry_override(self):
        p = StrategyProfile(name="high_bar", mfcs_buy_threshold=0.35)
        assert p.has_entry_overrides

    def test_exit_only_not_entry_override(self):
        """Exit-only overrides don't trigger entry re-eval."""
        p = StrategyProfile(
            name="wide_exits",
            exit_threshold=0.70,
            tranche_targets=(0.10, 0.20, 0.40),
            runner_pct=0.25,
        )
        assert not p.has_entry_overrides

    def test_to_dict_only_non_none(self):
        """to_dict only includes non-None fields."""
        p = StrategyProfile(name="test", exit_threshold=0.5)
        d = p.to_dict()
        assert d["name"] == "test"
        assert d["exit_threshold"] == 0.5
        assert "agent_weights" not in d
        assert "runner_pct" not in d

    def test_to_sim_config(self):
        """to_sim_config produces a valid SimConfig with overrides."""
        p = StrategyProfile(
            name="custom",
            exit_threshold=0.55,
            tranche_targets=(0.08, 0.15, 0.30),
        )
        sim = p.to_sim_config()
        assert sim.name == "custom"
        assert sim.exit_threshold == 0.55
        assert sim.tranche_targets == (0.08, 0.15, 0.30)
        # Defaults inherited from "current" profile
        assert sim.risk_per_trade_pct == 0.01


# ── Entry Re-evaluation Tests ────────────────────────────────────


class TestEntryReeval:
    """Verify entry re-scoring with custom weights."""

    @pytest.fixture
    def bull_entry(self) -> dict:
        """A BUY entry with bullish agent signals."""
        return {
            "action": "BUY",
            "ticker": "TEST",
            "mfcs": 0.30,
            "agent_signals": [
                {"agent_id": "news_agent", "signal": "STRONG_BULL", "confidence": 0.8},
                {"agent_id": "technical_agent", "signal": "BULL", "confidence": 0.7},
                {"agent_id": "fundamental_agent", "signal": "BULL", "confidence": 0.6},
                {"agent_id": "risk_agent", "signal": "NEUTRAL", "confidence": 0.5,
                 "risk_score": 0.2, "risk_verdict": "APPROVE"},
            ],
        }

    def test_baseline_accepts_bull(self, bull_entry):
        """Baseline profile accepts a bullish entry."""
        profile = StrategyProfile(name="baseline")
        would_enter, mfcs, dir_count = recompute_entry(bull_entry, profile)
        assert would_enter
        assert mfcs > 0.25
        assert dir_count == 3  # news=STRONG_BULL, tech=BULL, fund=BULL

    def test_high_bar_may_reject(self, bull_entry):
        """High bar profile may reject moderate-conviction entry."""
        profile = StrategyProfile(
            name="high_bar",
            mfcs_buy_threshold=0.50,
            min_directional_agents=4,
        )
        would_enter, mfcs, dir_count = recompute_entry(bull_entry, profile)
        # Only 3 directional agents, need 4 → reject
        assert not would_enter

    def test_news_heavy_changes_mfcs(self, bull_entry):
        """News-heavy weighting changes MFCS score."""
        baseline = StrategyProfile(name="baseline")
        news_heavy = StrategyProfile(
            name="news_heavy",
            agent_weights={
                "catalyst_news": 0.60, "technical": 0.10,
                "volume_rvol": 0.15, "float_structure": 0.10,
                "institutional": 0.05, "deep_search": 0.00,
            },
        )
        _, mfcs_base, _ = recompute_entry(bull_entry, baseline)
        _, mfcs_news, _ = recompute_entry(bull_entry, news_heavy)
        # STRONG_BULL on news agent with higher weight → higher MFCS
        assert mfcs_news > mfcs_base

    def test_empty_signals_rejected(self):
        """Entry with no agent signals is rejected."""
        entry = {"action": "BUY", "ticker": "TEST", "agent_signals": []}
        would_enter, mfcs, _ = recompute_entry(entry, StrategyProfile(name="test"))
        assert not would_enter
        assert mfcs == 0.0

    def test_confidence_floor_applied(self):
        """D116 confidence floor of 0.20 is applied."""
        entry = {
            "action": "BUY", "ticker": "TEST",
            "agent_signals": [
                {"agent_id": "news_agent", "signal": "BULL", "confidence": 0.0},
                {"agent_id": "technical_agent", "signal": "BULL", "confidence": 0.0},
            ],
        }
        _, mfcs, _ = recompute_entry(entry, StrategyProfile(name="test"))
        # With floor 0.20, BULL * 0.20 = 0.10 per agent (not 0.0)
        assert mfcs > 0


# ── Elo Tournament Tests ─────────────────────────────────────────


class TestEloTournament:
    """Verify Elo math and tournament mechanics."""

    def test_expected_score_symmetric(self):
        """E_A + E_B = 1.0 for any pair."""
        ea = compute_expected_score(1200, 1400)
        eb = compute_expected_score(1400, 1200)
        assert abs(ea + eb - 1.0) < 1e-10

    def test_expected_score_equal_ratings(self):
        """Equal ratings → 0.5 expected."""
        ea = compute_expected_score(1200, 1200)
        assert abs(ea - 0.5) < 1e-10

    def test_elo_update_winner(self):
        """Winner's rating increases."""
        new_rating = update_elo(1200.0, 0.5, 1.0, k=32.0)
        assert new_rating > 1200.0

    def test_elo_update_loser(self):
        """Loser's rating decreases."""
        new_rating = update_elo(1200.0, 0.5, 0.0, k=32.0)
        assert new_rating < 1200.0

    def test_variant_stddev(self):
        """Stddev computation from per-session PnL."""
        v = StrategyVariant(
            profile_name="test",
            per_session_pnl={"d1": 100.0, "d2": -50.0, "d3": 200.0},
        )
        v.compute_stddev()
        assert v.pnl_stddev > 0

    def test_variant_serialization(self):
        """StrategyVariant round-trips through dict."""
        v = StrategyVariant(
            profile_name="test",
            elo_rating=1350.0,
            match_count=10,
            win_count=7,
            total_pnl=500.0,
            per_session_pnl={"d1": 200, "d2": 300},
        )
        d = v.to_dict()
        v2 = StrategyVariant.from_dict(d)
        assert v2.profile_name == "test"
        assert v2.elo_rating == 1350.0
        assert v2.match_count == 10
        assert v2.total_pnl == 500.0

    def test_arena_register_and_rank(self):
        """Arena registers profiles and produces rankings."""
        arena = StrategyArena()
        arena.register_profile(StrategyProfile(name="a"))
        arena.register_profile(StrategyProfile(name="b"))
        rankings = arena.get_rankings()
        assert len(rankings) == 2
        assert all(r.elo_rating == DEFAULT_ELO for r in rankings)

    def test_arena_matchup_updates_elo(self):
        """Recording a matchup changes Elo ratings."""
        arena = StrategyArena()
        arena.register_profile(StrategyProfile(name="winner"))
        arena.register_profile(StrategyProfile(name="loser"))
        arena._record_matchup("winner", "loser", pnl_a=500.0, pnl_b=-200.0)
        rankings = arena.get_rankings()
        winner = [r for r in rankings if r.profile_name == "winner"][0]
        loser = [r for r in rankings if r.profile_name == "loser"][0]
        assert winner.elo_rating > DEFAULT_ELO
        assert loser.elo_rating < DEFAULT_ELO
        assert winner.win_count == 1
        assert loser.loss_count == 1

    def test_arena_draw(self):
        """Small P&L difference = draw."""
        arena = StrategyArena()
        arena.register_profile(StrategyProfile(name="a"))
        arena.register_profile(StrategyProfile(name="b"))
        # Both within 0.1% of 138k equity = $138
        arena._record_matchup("a", "b", pnl_a=50.0, pnl_b=60.0)
        rankings = arena.get_rankings()
        a = [r for r in rankings if r.profile_name == "a"][0]
        b = [r for r in rankings if r.profile_name == "b"][0]
        assert a.draw_count == 1
        assert b.draw_count == 1

    def test_arena_save_load(self, tmp_path):
        """Arena state persists to JSON."""
        arena = StrategyArena()
        arena.register_profile(StrategyProfile(name="test"))
        arena._variants["test"].elo_rating = 1350.0
        arena._variants["test"].win_count = 5

        path = tmp_path / "arena_state.json"
        arena.save(path)

        arena2 = StrategyArena.load(path)
        assert "test" in arena2._variants
        assert arena2._variants["test"].elo_rating == 1350.0
        assert arena2._variants["test"].win_count == 5


# ── Counterfactual Tests ──────────────────────────────────────────


class TestCounterfactual:
    """Verify counterfactual data structures."""

    def test_counterfactual_outcome_entered(self):
        """CounterfactualOutcome for an entered trade."""
        co = CounterfactualOutcome(
            would_enter=True,
            exit_price=5.50,
            exit_reason="t1_fill",
            pnl=100.0,
            pnl_pct=2.5,
            hold_minutes=15.0,
            tranches_filled=1,
        )
        assert co.would_enter
        assert co.pnl == 100.0

    def test_counterfactual_outcome_rejected(self):
        """CounterfactualOutcome for a rejected entry."""
        co = CounterfactualOutcome(
            would_enter=False,
            recomputed_mfcs=0.18,
        )
        assert not co.would_enter
        assert co.pnl is None

    def test_trade_counterfactual_structure(self):
        """TradeCounterfactual holds multiple profile outcomes."""
        cf = TradeCounterfactual(
            ticker="ANNA",
            session_date="2026-03-20",
            entry_price=3.25,
            actual_pnl=261.0,
        )
        cf.profile_outcomes["baseline"] = CounterfactualOutcome(
            would_enter=True, pnl=261.0,
        )
        cf.profile_outcomes["tight_exits"] = CounterfactualOutcome(
            would_enter=True, pnl=120.0,
        )
        assert len(cf.profile_outcomes) == 2
        assert cf.profile_outcomes["baseline"].pnl > cf.profile_outcomes["tight_exits"].pnl


# ── Data Loader Tests ─────────────────────────────────────────────


class TestArenaDataLoader:
    """Verify session discovery and loading."""

    def test_discover_empty_dir(self, tmp_path):
        """Empty data dir returns no sessions."""
        (tmp_path / "data" / "journals").mkdir(parents=True)
        (tmp_path / "data" / "bars").mkdir(parents=True)
        loader = ArenaDataLoader(tmp_path)
        assert loader.discover_sessions() == []

    def test_discover_requires_both(self, tmp_path):
        """Session needs both journal AND bars."""
        journals = tmp_path / "data" / "journals"
        bars = tmp_path / "data" / "bars"
        journals.mkdir(parents=True)
        bars.mkdir(parents=True)

        # Journal but no bars
        (journals / "journal_2026-03-20_140000.jsonl").write_text("")
        assert ArenaDataLoader(tmp_path).discover_sessions() == []

        # Add bars for same date
        (bars / "bars_TEST_2026-03-20.json").write_text("[]")
        assert ArenaDataLoader(tmp_path).discover_sessions() == ["2026-03-20"]


# ── Profile Session Result Tests ──────────────────────────────────


class TestProfileSessionResult:
    def test_win_rate(self):
        psr = ProfileSessionResult(
            profile_name="test", session_date="2026-01-01",
            trade_results=[], win_count=3, loss_count=2,
        )
        assert abs(psr.win_rate - 0.6) < 1e-10

    def test_win_rate_no_trades(self):
        psr = ProfileSessionResult(
            profile_name="test", session_date="2026-01-01",
            trade_results=[],
        )
        assert psr.win_rate == 0.0


# ── D120: New Dimension Tests ────────────────────────────────────


class TestD120TrailingDecoupling:
    """Verify trailing_trail_distance_pct is independent of activation."""

    def test_profile_has_trail_distance_field(self):
        """StrategyProfile accepts trailing_trail_distance_pct."""
        p = StrategyProfile(
            name="test",
            trailing_activation_pct=0.03,
            trailing_trail_distance_pct=0.08,
        )
        assert p.trailing_activation_pct == 0.03
        assert p.trailing_trail_distance_pct == 0.08

    def test_none_trail_distance_not_in_dict(self):
        """to_dict omits trailing_trail_distance_pct when None."""
        p = StrategyProfile(name="test", trailing_activation_pct=0.03)
        d = p.to_dict()
        assert "trailing_trail_distance_pct" not in d

    def test_sim_config_trail_distance_wired(self):
        """to_sim_config passes trailing_trail_distance_pct through."""
        p = StrategyProfile(
            name="test",
            trailing_activation_pct=0.03,
            trailing_trail_distance_pct=0.08,
        )
        sim = p.to_sim_config()
        assert sim.trailing_activation_pct == 0.03
        assert sim.trailing_trail_distance_pct == 0.08

    def test_sim_config_none_preserves_default(self):
        """trailing_trail_distance_pct=None in SimConfig (backward compat)."""
        p = StrategyProfile(name="test")
        sim = p.to_sim_config()
        assert sim.trailing_trail_distance_pct is None


class TestD120ExitSignalWeightOverrides:
    """Verify exit signal weight override merging."""

    def test_profile_accepts_overrides(self):
        p = StrategyProfile(
            name="test",
            exit_signal_weight_overrides={"time_decay": 0.0, "volume_fade": 0.05},
        )
        assert p.exit_signal_weight_overrides["time_decay"] == 0.0
        assert p.exit_signal_weight_overrides["volume_fade"] == 0.05

    def test_sim_config_overrides_wired(self):
        p = StrategyProfile(
            name="test",
            exit_signal_weight_overrides={"time_decay": 0.0},
        )
        sim = p.to_sim_config()
        assert sim.exit_signal_weight_overrides == {"time_decay": 0.0}

    def test_exit_engine_merge_semantics(self):
        """ExitSignalEngine merges partial overrides onto defaults."""
        from src.execution.exit_intelligence import ExitSignalEngine, EXIT_SIGNAL_WEIGHTS
        engine = ExitSignalEngine(weights={"time_decay": 0.0, "volume_fade": 0.05})
        # Overridden values
        assert engine._weights["time_decay"] == 0.0
        assert engine._weights["volume_fade"] == 0.05
        # Non-overridden values preserved from defaults
        assert engine._weights["vwap_deterioration"] == EXIT_SIGNAL_WEIGHTS["vwap_deterioration"]
        assert engine._weights["distribution_detector"] == EXIT_SIGNAL_WEIGHTS["distribution_detector"]

    def test_exit_engine_none_uses_defaults(self):
        """ExitSignalEngine with None weights uses all defaults."""
        from src.execution.exit_intelligence import ExitSignalEngine, EXIT_SIGNAL_WEIGHTS
        engine = ExitSignalEngine(weights=None)
        assert engine._weights == EXIT_SIGNAL_WEIGHTS


class TestD120TranchRatchetRatio:
    """Verify tranche_ratchet_ratio field."""

    def test_profile_accepts_ratchet(self):
        p = StrategyProfile(name="test", tranche_ratchet_ratio=0.40)
        assert p.tranche_ratchet_ratio == 0.40

    def test_sim_config_ratchet_wired(self):
        p = StrategyProfile(name="test", tranche_ratchet_ratio=0.50)
        sim = p.to_sim_config()
        assert sim.tranche_ratchet_ratio == 0.50

    def test_sim_config_default_ratchet(self):
        """Default ratchet ratio is 0.25 (backward compat)."""
        p = StrategyProfile(name="test")
        sim = p.to_sim_config()
        assert sim.tranche_ratchet_ratio == 0.25


class TestD120MfcsScalingDenom:
    """Verify mfcs_scaling_denom exposure through StrategyProfile."""

    def test_profile_accepts_denom(self):
        p = StrategyProfile(name="test", mfcs_scaling_denom=0.35)
        assert p.mfcs_scaling_denom == 0.35

    def test_sim_config_denom_wired(self):
        p = StrategyProfile(name="test", mfcs_scaling_denom=0.35)
        sim = p.to_sim_config()
        assert sim.mfcs_scaling_denom == 0.35

    def test_sim_config_default_denom(self):
        """Default denom is 0.5."""
        p = StrategyProfile(name="test")
        sim = p.to_sim_config()
        assert sim.mfcs_scaling_denom == 0.5


class TestD120EvolvedProfiles:
    """Verify evolved and ablation profiles are properly constructed."""

    def test_evolved_hunter_has_decoupled_trailing(self):
        """evolved_hunter has different activation and trail distance."""
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
        from scripts.run_arena import PROFILES
        p = PROFILES["evolved_hunter"]
        assert p.trailing_activation_pct == 0.03
        assert p.trailing_trail_distance_pct == 0.08
        assert p.trailing_activation_pct != p.trailing_trail_distance_pct

    def test_evolved_holder_suppresses_time_decay(self):
        from scripts.run_arena import PROFILES
        p = PROFILES["evolved_holder"]
        assert p.exit_signal_weight_overrides["time_decay"] == 0.0

    def test_evolved_holder_moderate_partial_suppression(self):
        from scripts.run_arena import PROFILES
        p = PROFILES["evolved_holder_moderate"]
        assert p.exit_signal_weight_overrides["time_decay"] == 0.03

    def test_evolved_sniper_has_ratchet_and_denom(self):
        from scripts.run_arena import PROFILES
        p = PROFILES["evolved_sniper"]
        assert p.tranche_ratchet_ratio == 0.40
        assert p.mfcs_scaling_denom == 0.35

    def test_ablation_profiles_isolate_one_dimension(self):
        """Each ablation profile changes exactly ONE new D120 dimension."""
        from scripts.run_arena import PROFILES
        # ablation_trailing: only trail decoupling
        at = PROFILES["ablation_trailing"]
        assert at.trailing_trail_distance_pct == 0.08
        assert at.exit_signal_weight_overrides is None
        assert at.mfcs_scaling_denom is None
        assert at.tranche_ratchet_ratio is None

        # ablation_signals: only exit weight overrides
        asig = PROFILES["ablation_signals"]
        assert asig.trailing_trail_distance_pct is None
        assert asig.exit_signal_weight_overrides is not None
        assert asig.mfcs_scaling_denom is None
        assert asig.tranche_ratchet_ratio is None

        # ablation_sizing: only mfcs_scaling_denom
        asz = PROFILES["ablation_sizing"]
        assert asz.trailing_trail_distance_pct is None
        assert asz.exit_signal_weight_overrides is None
        assert asz.mfcs_scaling_denom == 0.35
        assert asz.tranche_ratchet_ratio is None

        # ablation_ratchet: only tranche_ratchet_ratio
        ar = PROFILES["ablation_ratchet"]
        assert ar.trailing_trail_distance_pct is None
        assert ar.exit_signal_weight_overrides is None
        assert ar.mfcs_scaling_denom is None
        assert ar.tranche_ratchet_ratio == 0.40

    def test_all_22_profiles_exist(self):
        from scripts.run_arena import PROFILES
        assert len(PROFILES) == 22
