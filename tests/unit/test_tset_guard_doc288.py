"""doc 288: CI guard against RE-INTRODUCING the trades_v1.ts_et UTC-mislabel trap (the audit's #1 systemic
finding — it broke doc-242/244, was fixed in doc-245, then re-appeared in v6_microstructure_pack.py). The
allowlist freezes the KNOWN occurrences (grandfathered tech debt, impact confined/known per doc-288); a NEW
ts_et wall-clock extraction in a file that neither uses sip_timestamp nor tz_convert will FAIL this test.
When you legitimately add one, you must (a) derive ET from sip_timestamp instead, or (b) add it here with a
justification comment — forcing the reviewer to confront the trap."""
import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("tset_guard", str(_ROOT / "scripts" / "_doc288_tset_guard.py"))
G = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(G)

# frozen 2026-07-11: the known suspect ts_et wall-clock sites (most are confined/dormant per the doc-288 audit;
# several read a CORRECTLY-labeled warehouse — the guard is deliberately conservative). NEW sites must not appear.
ALLOWLIST = {
    "scripts/analyze_watchlist_movers.py:50", "scripts/analyze_watchlist_movers.py:62",
    "scripts/backtest_5_hypotheses.py:52", "scripts/backtest_first_30min_signal.py:47",
    "scripts/backtest_h1_full_universe.py:43", "scripts/backtest_h1_full_universe.py:44",
    "scripts/backtest_h1_full_universe.py:99", "scripts/backtest_h1_full_universe.py:100",
    "scripts/backtest_h1_full_universe.py:101", "scripts/backtest_h3_full_universe.py:42",
    "scripts/backtest_h3_full_universe.py:43", "scripts/build_intraday_paths.py:91",
    "scripts/build_intraday_paths.py:92", "scripts/build_intraday_paths.py:93",
    "scripts/build_intraday_paths.py:94", "scripts/build_microstructure_features_v2.py:72",
    "scripts/build_microstructure_features_v2.py:73", "scripts/build_microstructure_features_v2.py:90",
    "scripts/build_microstructure_features_v2.py:91", "scripts/build_tick_features_doc242.py:51",
    "scripts/ml_intraday_refresh.py:71", "scripts/ml_intraday_refresh.py:72",
    "scripts/polygon_ising_magnetization.py:136", "scripts/polygon_ising_magnetization.py:137",
    "scripts/polygon_news_catalyst.py:87",
}


def test_no_new_tset_wallclock_sites():
    suspect, _safe = G.scan()
    new = sorted(set(suspect) - ALLOWLIST)
    assert not new, (
        "NEW ts_et wall-clock extraction without sip_timestamp/tz_convert (the doc-288 UTC-mislabel trap). "
        "Derive ET from sip_timestamp, or add to ALLOWLIST with justification:\n  " + "\n  ".join(new))
