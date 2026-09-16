"""doc 272 (A1) — Verdict Lifecycle Ledger emit-map coverage pin.

Source-pin test (cf. the test_stop_order_tif_gtc pattern): greps the four
wired production files for every expected ``vll_emit("<STAGE>", ...)`` call
so the doc-272 emit map cannot silently rot. If a gate is refactored away,
update BOTH the gate and this map in the same change (truth over plan).

The doc-272 invariant: every BUY/SHORT verdict that dies must leave exactly
one terminal in data/ops/verdict_trace_<date>.jsonl, so
``count(journal verdicts) == count(trace terminals)`` with ZERO orphans.

Map (stage -> emit-site count per file):

main.py
    BLOCKED_CATALYST_GATE   x1  (doc 268, MAIN)
    BLOCKED_NEWS_GATE       x3  (MAIN [doc 272 gap-fix] + VWAP + RESCAN [doc 268])
    BLOCKED_CIRCUIT         x1  (MAIN, daily-drawdown circuit halt-all)
    BLOCKED_MAX_POSITIONS   x1  (MAIN)
    BLOCKED_FP_DEDUP        x1  (MAIN)
    BLOCKED_ALREADY_HELD    x3  (MAIN + VWAP + RESCAN)
    BLOCKED_STOP_COOLDOWN   x3  (MAIN + VWAP + RESCAN)
    BLOCKED_RECENTLY_CLOSED x1  (MAIN)
    BLOCKED_PORTFOLIO_RISK  x3  (MAIN + VWAP + RESCAN)
    BLOCKED_FALLER          x3  (MAIN [post-D191-exemption] + VWAP + RESCAN)
    BLOCKED_REGIME          x3  (MAIN + VWAP + RESCAN)
    ROUTED_SHORT            x1  (MAIN, D161 long->short routing, informational)
    ROUTED_FADE_SHORT       x1  (MAIN, D202 flag-gated routing, informational)
    DEFERRED_OBSERVATION    x1  (MAIN, D170 — NON-terminal marker)
    EXPIRED_OBSERVATION     x9  (2 canonical REJECTED/EXPIRED polls + 7
                                 superseded-by-position-close defensive pops)
    ERROR_EXECUTOR          x1  (MAIN, D170 approved-exec raised after pop)

src/core/orchestrator.py
    BLOCKED_ARM_ASSIGN      x1  (D310.T2.v2 HARD_ABORT BUY->HOLD conversion)

src/execution/bridge.py
    SKIPPED_HOLD            x1
    BLOCKED_ZERO_SIZE       x1
    BLOCKED_SPREAD          x1  (D101)
    BLOCKED_PM_RECHECK      x1  (D150 — backstop for VWAP/RESCAN circuit/max-pos)
    ERROR_EXECUTOR          x1
    EXPIRED_NOT_VISIBLE     x1  (D216)
    REJECTED_BROKER         x1  (D216 canceled/expired/rejected)
    REJECTED_PARTIAL_ZERO   x1  (D217)
    SUBMITTED               x2  (doc-268 poll path + doc-272 instant-fill else)

src/execution/alpaca_executor.py
    BLOCKED_INVALID_ENTRY     x1
    BLOCKED_MAX_POSITIONS_EXEC x1
    BLOCKED_ZERO_EQUITY       x1
    BLOCKED_QTY_ZERO          x2  (sizing-zero + doc-286 worst-fill cap recap)
    REJECTED_BROKER_OTO       x1
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

MAIN = REPO_ROOT / "main.py"
ORCH = REPO_ROOT / "src" / "core" / "orchestrator.py"
BRIDGE = REPO_ROOT / "src" / "execution" / "bridge.py"
EXECUTOR = REPO_ROOT / "src" / "execution" / "alpaca_executor.py"

# (file, stage, expected emit-site count). Counts are EXACT — both a missing
# emit (gate deleted / refactored without re-wiring) and a surprise extra
# (new emit added without updating the map) fail loudly.
EXPECTED_EMITS: list[tuple[Path, str, int]] = [
    # ── main.py ──────────────────────────────────────────────────────
    (MAIN, "BLOCKED_CATALYST_GATE", 1),
    (MAIN, "BLOCKED_NEWS_GATE", 3),
    # doc 272 D2 (merged after the A1 map was built): the EMPTY!=BEAR lever's
    # absence-fail-open terminal at D200-E4 + D204 MAIN/VWAP/RESCAN.
    (MAIN, "DOWNGRADED_ABSENCE", 4),
    (MAIN, "BLOCKED_CIRCUIT", 1),
    (MAIN, "BLOCKED_MAX_POSITIONS", 1),
    (MAIN, "BLOCKED_FP_DEDUP", 1),
    (MAIN, "BLOCKED_ALREADY_HELD", 3),
    (MAIN, "BLOCKED_STOP_COOLDOWN", 3),
    (MAIN, "BLOCKED_RECENTLY_CLOSED", 1),
    (MAIN, "BLOCKED_PORTFOLIO_RISK", 3),
    (MAIN, "BLOCKED_FALLER", 3),
    (MAIN, "BLOCKED_REGIME", 3),
    (MAIN, "ROUTED_SHORT", 1),
    (MAIN, "ROUTED_FADE_SHORT", 1),
    (MAIN, "DEFERRED_OBSERVATION", 1),
    (MAIN, "EXPIRED_OBSERVATION", 9),
    (MAIN, "ERROR_EXECUTOR", 1),
    # ── src/core/orchestrator.py ─────────────────────────────────────
    (ORCH, "BLOCKED_ARM_ASSIGN", 1),
    # ── src/execution/bridge.py ──────────────────────────────────────
    (BRIDGE, "SKIPPED_HOLD", 1),
    (BRIDGE, "BLOCKED_ZERO_SIZE", 1),
    (BRIDGE, "BLOCKED_SPREAD", 1),
    (BRIDGE, "BLOCKED_PM_RECHECK", 1),
    (BRIDGE, "ERROR_EXECUTOR", 1),
    (BRIDGE, "EXPIRED_NOT_VISIBLE", 1),
    (BRIDGE, "REJECTED_BROKER", 1),
    (BRIDGE, "REJECTED_PARTIAL_ZERO", 1),
    (BRIDGE, "SUBMITTED", 2),
    # ── src/execution/alpaca_executor.py ─────────────────────────────
    (EXECUTOR, "BLOCKED_INVALID_ENTRY", 1),
    (EXECUTOR, "BLOCKED_MAX_POSITIONS_EXEC", 1),
    (EXECUTOR, "BLOCKED_ZERO_EQUITY", 1),
    # doc 286: x2 — the original sizing-computed-zero terminal + the
    # worst-fill cap recap (qty shrunk to 0 when one share would breach
    # the cap at max(eval, marketable limit)).
    (EXECUTOR, "BLOCKED_QTY_ZERO", 2),
    (EXECUTOR, "REJECTED_BROKER_OTO", 1),
]

WIRED_FILES = (MAIN, ORCH, BRIDGE, EXECUTOR)


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _count_stage(source: str, stage: str) -> int:
    # `vll_emit("STAGE",` — trailing `",` disambiguates prefix collisions
    # (BLOCKED_MAX_POSITIONS vs BLOCKED_MAX_POSITIONS_EXEC, REJECTED_BROKER
    # vs REJECTED_BROKER_OTO).
    return source.count(f'vll_emit("{stage}",')


class TestVllEmitMap:
    """Every doc-268/272 gate keeps its vll_emit terminal — exact counts."""

    def test_wired_files_exist(self):
        for path in WIRED_FILES:
            assert path.is_file(), f"wired file missing: {path}"

    def test_every_expected_stage_is_wired(self):
        failures = []
        for path, stage, expected in EXPECTED_EMITS:
            got = _count_stage(_src(path), stage)
            if got != expected:
                failures.append(
                    f"{path.name}: vll_emit(\"{stage}\", ...) x{got} (expected x{expected})"
                )
        assert not failures, (
            "doc 272 VLL emit map drifted — a verdict-drop gate lost (or grew) "
            "its ledger terminal. Re-wire the gate or update EXPECTED_EMITS "
            "with the ship doc:\n  " + "\n  ".join(failures)
        )

    def test_no_unmapped_emits(self):
        """Total emit calls per file == sum of the mapped stages.

        Catches a brand-new vll_emit added without extending the map (the
        map is the documentation of record for the grader's terminal set).
        """
        for path in WIRED_FILES:
            source = _src(path)
            total = source.count('vll_emit("')
            mapped = sum(n for p, _s, n in EXPECTED_EMITS if p == path)
            assert total == mapped, (
                f"{path.name}: {total} vll_emit call(s) but {mapped} mapped in "
                f"EXPECTED_EMITS — update the map for the new/removed stage"
            )

    def test_every_emit_site_is_guarded(self):
        """Never-raises discipline: each emit site does its own local
        `from src.ops.verdict_ledger import vll_emit` inside a try/except
        (1 import per call). An unguarded module-level usage would break
        the 1:1 ratio and fail here.
        """
        for path in WIRED_FILES:
            source = _src(path)
            calls = source.count('vll_emit("')
            local_imports = source.count(
                "from src.ops.verdict_ledger import vll_emit"
            )
            assert calls == local_imports, (
                f"{path.name}: {calls} vll_emit call(s) vs {local_imports} "
                f"guarded local import(s) — every emit must sit in its own "
                f"try/except with a local import (never-raises mandate)"
            )

    def test_stage_names_fit_ledger_truncation(self):
        """vll_emit truncates stage to 40 chars — names must survive intact
        so the nightly grader's joins stay exact."""
        for _path, stage, _n in EXPECTED_EMITS:
            assert len(stage) <= 40, f"stage too long for ledger: {stage}"
