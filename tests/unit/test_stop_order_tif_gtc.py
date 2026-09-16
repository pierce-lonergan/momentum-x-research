"""
Tue 2026-04-21 Fix 1: GTC stops on every position.

Background. The Mon 2026-04-21 session bought ELSE @ $7.65 with a
$6.50 stop, then died at 10:18 ET (watchdog kill, see Fix 2 + Fix 3).
The D100-converted stop order was submitted with `time_in_force="day"`
— Alpaca's DAY orders expire at 16:00 ET unfilled. So at 16:00 the
stop-loss expired and ELSE rode out the next 14 hours of overnight
exposure with ZERO broker-side protection. The post-pump-day-2
gap-down literature says this is modally a -1.5%-or-worse outcome.
We were lucky it ended -0.52%.

Root cause. Every protective stop in the production tree (10 call
sites: main.py × 8, stop_resubmitter.py × 2) called
`submit_stop_order` without an explicit `time_in_force` kwarg, so
they all picked up the function default of `"day"`. The OTO bracket's
*entry* leg correctly uses DAY (entries that don't fill should expire);
but the *protective stop* leg should use GTC because the strategy's
risk model assumes the stop is continuously active.

Fix. Two parts:
  1. Default of `submit_stop_order(time_in_force=...)` flipped from
     "day" to "gtc" — single-source fix that catches all 10 call sites.
  2. Defensive: explicit `time_in_force="gtc"` added at every call site
     so a future reader cannot miss the intent and a future "fix the
     default back to day" PR fails review.

Tests below cover:
  T1. Default contract — `submit_stop_order` defaults to "gtc".
  T2. AST sweep — no production call site passes `time_in_force="day"`
       to `submit_stop_order` (entries via OTO are exempt — they're
       allowed to be DAY).
  T3. Behavioral round-trip — calling submit_stop_order with no TIF
       results in `"gtc"` being sent to Alpaca's HTTP endpoint.
  T4. Behavioral round-trip — explicit GTC also sends GTC.
  T5. Rule (e) positive case — payload sent to Alpaca always
       contains a non-empty `time_in_force` field. Catches the case
       where a future refactor accidentally drops the field entirely.
  T6. Defensive — every call site in main.py + stop_resubmitter.py
       passes `time_in_force="gtc"` *explicitly*. Triple-redundant
       with T2 but enforces the documented coding convention.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.data.alpaca_client import AlpacaDataClient


REPO_ROOT = Path(__file__).resolve().parents[2]


# ── T1: Default contract ──────────────────────────────────────────────


class TestDefaultContract:
    """The single-source fix. If a future PR flips the default back
    to "day", this test fires immediately."""

    def test_submit_stop_order_default_is_gtc(self):
        sig = inspect.signature(AlpacaDataClient.submit_stop_order)
        assert "time_in_force" in sig.parameters, (
            "time_in_force kwarg removed from submit_stop_order — "
            "this is a breaking-change protective failure"
        )
        param = sig.parameters["time_in_force"]
        assert param.default == "gtc", (
            f"submit_stop_order default time_in_force is {param.default!r}, "
            f"expected 'gtc'. DAY-TIF stops expire at 16:00 ET and leave "
            f"positions naked overnight. See "
            f"tests/unit/test_stop_order_tif_gtc.py docstring + "
            f"docs/research-log/16 (silent-fallback audit)."
        )


# ── T2: AST sweep across production tree ─────────────────────────────


def _iter_python_files() -> list[Path]:
    """All .py files in src/, scripts/, and main.py.

    Scopes the walk to those roots rather than walking REPO_ROOT and filtering
    afterwards: rglob descends into a directory before any filter can reject it, so
    the previous form traversed data/ (a multi-GB market-data warehouse) and
    .hypothesis/ (thousands of files) on every run, and eventually hung the suite.
    """
    skip_parts = {".venv", "venv", "node_modules", "__pycache__"}
    roots = [REPO_ROOT / "src", REPO_ROOT / "scripts"]
    out: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            if set(path.parts) & skip_parts:
                continue
            out.append(path)
    main_py = REPO_ROOT / "main.py"
    if main_py.exists():
        out.append(main_py)
    return sorted(out)


def _find_submit_stop_order_calls(tree: ast.AST) -> list[tuple[int, dict]]:
    """Return (lineno, kwargs_dict) for every `submit_stop_order(...)` call.
    kwargs_dict maps each keyword name to its constant value (or None)."""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match either `submit_stop_order(...)` or `<x>.submit_stop_order(...)`
        if isinstance(node.func, ast.Name) and node.func.id == "submit_stop_order":
            target = True
        elif isinstance(node.func, ast.Attribute) and node.func.attr == "submit_stop_order":
            target = True
        else:
            target = False
        if not target:
            continue

        kwargs = {}
        for kw in node.keywords:
            if kw.arg is None:
                continue
            if isinstance(kw.value, ast.Constant):
                kwargs[kw.arg] = kw.value.value
            else:
                kwargs[kw.arg] = "<non-constant>"
        found.append((node.lineno, kwargs))
    return found


class TestNoDayTifInProductionCalls:
    """T2: No production call to submit_stop_order may pass
    time_in_force='day'. The default flip in T1 catches the no-kwarg
    case; this catches anyone explicitly setting day."""

    def test_no_caller_passes_day_tif(self):
        offenders: list[tuple[Path, int]] = []
        for py_file in _iter_python_files():
            try:
                tree = ast.parse(py_file.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for lineno, kwargs in _find_submit_stop_order_calls(tree):
                if kwargs.get("time_in_force") == "day":
                    offenders.append((py_file.relative_to(REPO_ROOT), lineno))

        assert not offenders, (
            f"Found {len(offenders)} call(s) to submit_stop_order with "
            f"time_in_force='day' — protective stops MUST be GTC.\n"
            + "\n".join(f"  {p}:{ln}" for p, ln in offenders)
        )


# ── T3 + T4: Behavioral round-trip via mocked HTTP ───────────────────


@pytest.fixture
def mock_client():
    """An AlpacaDataClient with `_trading_post` mocked. Captures the
    payload sent to /v2/orders so we can assert on time_in_force."""
    fake_config = MagicMock()
    fake_config.api_key = "test"
    fake_config.secret_key = "test"
    fake_config.base_url = "https://paper-api.alpaca.markets"
    fake_config.data_url = "https://data.alpaca.markets"
    c = AlpacaDataClient(fake_config)
    captured = {}

    async def fake_post(url, payload):
        captured["url"] = url
        captured["payload"] = payload
        return {"id": "stub-id", "status": "accepted"}

    c._trading_post = fake_post  # type: ignore[assignment]
    c._captured = captured  # type: ignore[attr-defined]
    return c


class TestBehavioralRoundTrip:

    @pytest.mark.asyncio
    async def test_default_call_sends_gtc(self, mock_client):
        """Calling without an explicit TIF should send GTC over the wire."""
        await mock_client.submit_stop_order(
            symbol="ELSE", qty=2478, side="sell", stop_price=6.50,
        )
        payload = mock_client._captured["payload"]
        assert payload["time_in_force"] == "gtc", (
            f"submit_stop_order default did not produce gtc payload — "
            f"got {payload.get('time_in_force')!r}"
        )

    @pytest.mark.asyncio
    async def test_explicit_gtc_sends_gtc(self, mock_client):
        await mock_client.submit_stop_order(
            symbol="ELSE", qty=2478, side="sell", stop_price=6.50,
            time_in_force="gtc",
        )
        assert mock_client._captured["payload"]["time_in_force"] == "gtc"

    @pytest.mark.asyncio
    async def test_payload_contains_required_fields(self, mock_client):
        """T5 rule-(e) positive case: payload always has the contract
        fields populated. Catches a refactor that accidentally drops
        TIF entirely."""
        await mock_client.submit_stop_order(
            symbol="ELSE", qty=2478, side="sell", stop_price=6.50,
        )
        payload = mock_client._captured["payload"]
        for field in ("symbol", "qty", "side", "type", "stop_price", "time_in_force"):
            assert field in payload, f"payload missing required field: {field}"
            assert payload[field], f"payload field {field!r} is empty: {payload[field]!r}"
        assert payload["type"] == "stop"
        assert payload["symbol"] == "ELSE"


# ── T6: Coding convention — every site uses explicit GTC kwarg ───────


class TestExplicitGTCAtEverySite:
    """Defensive convention: even though the default is now GTC, every
    call site in main.py + stop_resubmitter.py must pass
    `time_in_force="gtc"` *explicitly*. This ensures a future grep for
    "where do we set TIF on stops" finds every site, and that a future
    PR-reviewer reading any single call site sees the intent without
    chasing a default."""

    PRODUCTION_FILES = [
        REPO_ROOT / "main.py",
        REPO_ROOT / "src" / "execution" / "stop_resubmitter.py",
        # Tue 2026-04-21 Fix 4: cancel_stop_and_submit_exit_ladder
        # centralizes the post-OTO stop submission. main.py's 4
        # inline sites were refactored to call this helper, so the
        # AST scan must include the helper module to keep the GTC
        # contract enforced.
        REPO_ROOT / "src" / "execution" / "exit_ladder.py",
    ]

    def test_all_callers_pass_explicit_gtc(self):
        offenders = []
        for py_file in self.PRODUCTION_FILES:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
            for lineno, kwargs in _find_submit_stop_order_calls(tree):
                if "time_in_force" not in kwargs:
                    offenders.append((py_file.relative_to(REPO_ROOT), lineno, "missing"))
                elif kwargs["time_in_force"] != "gtc":
                    offenders.append(
                        (py_file.relative_to(REPO_ROOT), lineno,
                         f"set to {kwargs['time_in_force']!r}"),
                    )
        assert not offenders, (
            f"{len(offenders)} call site(s) to submit_stop_order must "
            f"pass explicit time_in_force='gtc':\n"
            + "\n".join(f"  {p}:{ln} ({reason})" for p, ln, reason in offenders)
        )

    def test_all_expected_sites_present(self):
        """Sanity: assert we still find AT LEAST the expected count
        of call sites. If a future refactor reduces this count
        without an explicit update to this test, fail loudly — we
        may have lost a stop path entirely.

        History:
          - Initial Fix 1 (Tue 2026-04-21 evening): 8 in main.py +
            2 in stop_resubmitter.py = 10 sites.
          - Fix 4 (same evening): RESCAN site refactored to call
            cancel_stop_and_submit_exit_ladder which encapsulates
            the 2 inline submit_stop_order calls. Removed 2 sites
            from main.py, intentionally — net 6 + 2 = 8.
          - Followup Fix 4b: as the other 3 main.py sites (Phase 2,
            FastPath, VWAP) get refactored to use the helper, this
            count will drop further (each removes 2 calls). Update
            this threshold accordingly each time.

        The threshold is the FLOOR (>=). The assertion only fires
        if calls are removed BELOW the floor, so the test catches
        unintended removals while permitting documented refactors
        to lower the floor by one update each."""
        total = 0
        per_file = {}
        for py_file in self.PRODUCTION_FILES:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
            count = len(_find_submit_stop_order_calls(tree))
            per_file[py_file.name] = count
            total += count
        # As of Fix 4 full refactor (all 4 main.py sites moved to
        # cancel_stop_and_submit_exit_ladder helper):
        #   main.py:           0 (was 8 -> all centralized in helper)
        #   stop_resubmitter:  2 (ratchet + emergency, unchanged)
        #   exit_ladder.py:    1 (the centralized residual-stop submit)
        #   total: 3
        # Floor is 3. If a refactor drops main.py back to direct calls
        # (regressing the centralization), main.py would jump above 0
        # and the per-file _ test_no_caller_passes_day_tif would catch
        # any new DAY-TIF call. This count test ensures the centralized
        # path itself isn't accidentally removed.
        assert total >= 3, (
            f"Expected >=3 submit_stop_order call sites across "
            f"main.py + stop_resubmitter.py + exit_ladder.py, "
            f"found {total}: {per_file}. The exit_ladder.py call is "
            f"now load-bearing — losing it removes broker-side stop "
            f"submission entirely from the post-fill path."
        )


# ── T7: Parity — OTO entry stays DAY (entries should expire) ─────────


class TestOTOEntryStillUsesDay:
    """Belt-and-suspenders parity check: the OTO ENTRY leg correctly
    stays DAY. We changed only `submit_stop_order`, not OTO. If someone
    accidentally also flips OTO TIF to GTC, unfilled entry orders would
    persist overnight — a different bug, equally bad."""

    def test_oto_order_default_is_still_day(self):
        sig = inspect.signature(AlpacaDataClient.submit_oto_order)
        assert sig.parameters["time_in_force"].default == "day", (
            "submit_oto_order default time_in_force changed from 'day'. "
            "OTO is for ENTRY orders that should expire if unfilled "
            "today; protective stops are handled by submit_stop_order "
            "(default GTC). Flipping OTO to GTC would create stale "
            "entry orders persisting overnight."
        )

    def test_oto_short_order_default_is_still_day(self):
        sig = inspect.signature(AlpacaDataClient.submit_oto_short_order)
        assert sig.parameters["time_in_force"].default == "day"
