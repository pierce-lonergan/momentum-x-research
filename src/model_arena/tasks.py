"""Task definitions for cross-model arena.

A Task supplies:
  - the system + user prompt template (string with {variables})
  - a labeled dataset (list of {input dict, expected output})
  - a parser that extracts the model's prediction
  - a comparator that decides correctness

Phase 7 ships ONE task: catalyst_classification. The dataset is a small
hand-built fixture (`data/model_arena_runs/catalyst_smoke.json`) since the
auto-labeled llm_arena dataset isn't populated yet.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_FIXTURE_DIR = _PROJECT_ROOT / "src" / "model_arena" / "fixtures"


@dataclass(frozen=True)
class TaskExample:
    """One labeled example."""
    example_id: str
    inputs: dict[str, Any]                     # values plugged into prompt template
    expected_output: str                       # ground truth label / classification


@dataclass(frozen=True)
class Task:
    """A cross-model evaluation task."""
    name: str
    system_prompt: str
    user_prompt_template: str                  # use {placeholder} for example fields
    examples: list[TaskExample]
    valid_outputs: tuple[str, ...]             # the closed set of valid labels
    parse_fn_name: str = "default_classification_parse"

    def render_user_prompt(self, ex: TaskExample) -> str:
        try:
            return self.user_prompt_template.format(**ex.inputs)
        except KeyError as e:
            raise ValueError(f"task {self.name!r}: example {ex.example_id} missing {e}")


# ── Parser ───────────────────────────────────────────────────────────────


def default_classification_parse(raw_response: str, valid_outputs: tuple[str, ...]) -> str | None:
    """Extract the model's classification from a free-text response.

    Strategy:
      1. Strip whitespace, lowercase, remove punctuation
      2. Look for any valid_output as a substring (longest match wins)
      3. Look for JSON-style {"label": "X"} or {"classification": "X"}
      4. Return None if no match
    """
    if not raw_response:
        return None
    text = raw_response.strip()

    # Try JSON parse
    json_match = re.search(r'\{[^}]*"(?:label|classification|catalyst)"\s*:\s*"([^"]+)"', text, re.IGNORECASE)
    if json_match:
        candidate = json_match.group(1).strip().upper()
        for vo in valid_outputs:
            if vo.upper() == candidate:
                return vo

    # Substring match (longest wins to avoid "FDA" matching "FDA_APPROVAL")
    text_upper = text.upper()
    matches = [vo for vo in valid_outputs if vo.upper() in text_upper]
    if matches:
        return max(matches, key=len)
    return None


# Registry of parsers
_PARSERS: dict[str, Callable] = {
    "default_classification_parse": default_classification_parse,
}


def get_parser(name: str) -> Callable:
    if name not in _PARSERS:
        raise ValueError(f"no parser registered as {name!r}; known: {list(_PARSERS)}")
    return _PARSERS[name]


# ── Catalyst Classification Task ────────────────────────────────────────


class CatalystClassificationTask:
    """Catalyst type classification on small-cap gap-up news headlines.

    Categories (matches src/llm_arena/models.py CatalystType where possible):
      FDA           — drug approval, clinical trial result, regulatory action
      EARNINGS      — quarterly results, guidance update
      M_AND_A       — merger, acquisition, takeover
      CONTRACT      — major customer win, partnership announcement
      OFFERING      — secondary offering, ATM, dilutive event (BEARISH catalyst)
      SQUEEZE       — short squeeze, gamma squeeze, social momentum
      NONE          — pure technical move, no clear catalyst
    """

    NAME = "catalyst_classification"
    VALID = ("FDA", "EARNINGS", "M_AND_A", "CONTRACT", "OFFERING", "SQUEEZE", "NONE")

    SYSTEM_PROMPT = (
        "You are an expert small-cap trader analyzing pre-market news catalysts. "
        "Given a stock symbol and its top news headline, classify the catalyst into "
        "EXACTLY ONE of: FDA, EARNINGS, M_AND_A, CONTRACT, OFFERING, SQUEEZE, NONE. "
        "Respond with a single JSON object: {\"label\": \"<category>\"}. No prose."
    )

    USER_TEMPLATE = (
        "Ticker: {ticker}\n"
        "Gap: {gap_pct}%\n"
        "Headline: {headline}\n"
        "\n"
        "Classify the catalyst. JSON only."
    )

    @classmethod
    def build(cls) -> Task:
        examples = _load_or_build_catalyst_examples()
        return Task(
            name=cls.NAME,
            system_prompt=cls.SYSTEM_PROMPT,
            user_prompt_template=cls.USER_TEMPLATE,
            examples=examples,
            valid_outputs=cls.VALID,
            parse_fn_name="default_classification_parse",
        )


def _load_or_build_catalyst_examples() -> list[TaskExample]:
    """Load catalyst examples from disk, or build defaults on first run."""
    fixture = _FIXTURE_DIR / "catalyst_smoke.json"
    if fixture.exists():
        try:
            with open(fixture, encoding="utf-8") as f:
                raw = json.load(f)
            return [
                TaskExample(
                    example_id=r["example_id"],
                    inputs=r["inputs"],
                    expected_output=r["expected_output"],
                )
                for r in raw
            ]
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("model_arena: catalyst_smoke.json malformed (%s); using built-in defaults", e)

    # Hand-built default fixture — 50 examples spanning all 7 categories
    return _DEFAULT_CATALYST_EXAMPLES


def _save_default_fixture() -> None:
    """Write the default fixture to disk so it can be edited."""
    _FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    fixture = _FIXTURE_DIR / "catalyst_smoke.json"
    with open(fixture, "w", encoding="utf-8") as f:
        json.dump([
            {
                "example_id": ex.example_id,
                "inputs": ex.inputs,
                "expected_output": ex.expected_output,
            }
            for ex in _DEFAULT_CATALYST_EXAMPLES
        ], f, indent=2)


# ── Default 50-example fixture ──────────────────────────────────────────
# Each example: realistic small-cap gap-up scenario. Diverse across catalysts.

_DEFAULT_CATALYST_EXAMPLES: list[TaskExample] = [
    # FDA (n=10)
    TaskExample("fda_001", {"ticker": "BCTX", "gap_pct": 56.5, "headline": "BriaCell receives FDA Fast Track designation for breast cancer immunotherapy"}, "FDA"),
    TaskExample("fda_002", {"ticker": "OCG", "gap_pct": 247, "headline": "Oncology firm announces positive Phase 2 trial results for lead drug candidate"}, "FDA"),
    TaskExample("fda_003", {"ticker": "BJDX", "gap_pct": 330, "headline": "Bone Biologics secures FDA 510(k) clearance for spinal implant device"}, "FDA"),
    TaskExample("fda_004", {"ticker": "MDAI", "gap_pct": 78, "headline": "Spectral AI gets FDA Breakthrough Device for burn diagnosis platform"}, "FDA"),
    TaskExample("fda_005", {"ticker": "TLSA", "gap_pct": 45, "headline": "Tiziana announces successful Phase 1 results for nasal MS treatment"}, "FDA"),
    TaskExample("fda_006", {"ticker": "ICCM", "gap_pct": 62, "headline": "Icarus Care receives PDUFA action date for migraine drug"}, "FDA"),
    TaskExample("fda_007", {"ticker": "PSTV", "gap_pct": 35, "headline": "Plus Therapeutics reports favorable interim data from glioblastoma trial"}, "FDA"),
    TaskExample("fda_008", {"ticker": "INMB", "gap_pct": 28, "headline": "INmune Bio's CORDStrom receives orphan drug designation from EMA and FDA"}, "FDA"),
    TaskExample("fda_009", {"ticker": "CBIO", "gap_pct": 89, "headline": "Catalyst Biosciences MarzAA Phase 3 study meets primary endpoint"}, "FDA"),
    TaskExample("fda_010", {"ticker": "ALDX", "gap_pct": 124, "headline": "Aldeyra reports positive topline results from RASP modulator clinical trial"}, "FDA"),

    # EARNINGS (n=8)
    TaskExample("earn_001", {"ticker": "AVNS", "gap_pct": 69, "headline": "Avanos Medical Q4 EPS beats by $0.18, raises FY guidance"}, "EARNINGS"),
    TaskExample("earn_002", {"ticker": "TVTX", "gap_pct": 52, "headline": "Travere Therapeutics Q3 revenue $89M vs $61M est, FILSPARI launch ahead of plan"}, "EARNINGS"),
    TaskExample("earn_003", {"ticker": "RBLX", "gap_pct": 18, "headline": "Roblox reports DAU growth 30% YoY, bookings beat consensus"}, "EARNINGS"),
    TaskExample("earn_004", {"ticker": "IONS", "gap_pct": 22, "headline": "Ionis Pharmaceuticals Q2 EPS $0.15 vs ($0.08) est, raises 2026 guidance"}, "EARNINGS"),
    TaskExample("earn_005", {"ticker": "SMCI", "gap_pct": 14, "headline": "Super Micro Q1 revenue $5.9B vs $5.3B est on AI server demand"}, "EARNINGS"),
    TaskExample("earn_006", {"ticker": "AMD", "gap_pct": 8, "headline": "AMD Q4 data center revenue grows 38% YoY, EPS beat"}, "EARNINGS"),
    TaskExample("earn_007", {"ticker": "SHOP", "gap_pct": 11, "headline": "Shopify GMV $80.7B Q4, gross margin expansion 240bps"}, "EARNINGS"),
    TaskExample("earn_008", {"ticker": "MARA", "gap_pct": 25, "headline": "Marathon Digital Q3 revenue $97.8M vs $91M est, BTC production up 18%"}, "EARNINGS"),

    # M_AND_A (n=8)
    TaskExample("ma_001", {"ticker": "SANA", "gap_pct": 44, "headline": "Sana Biotechnology to be acquired by major pharma at 65% premium"}, "M_AND_A"),
    TaskExample("ma_002", {"ticker": "CRWG", "gap_pct": 38, "headline": "Crown Holdings agrees to all-cash merger with packaging giant"}, "M_AND_A"),
    TaskExample("ma_003", {"ticker": "BMNU", "gap_pct": 55, "headline": "Bmnu Industries enters definitive agreement to acquire competitor for $1.2B"}, "M_AND_A"),
    TaskExample("ma_004", {"ticker": "XYZ", "gap_pct": 31, "headline": "XYZ Corp confirms exploring strategic alternatives including potential sale"}, "M_AND_A"),
    TaskExample("ma_005", {"ticker": "SQQQ", "gap_pct": 42, "headline": "Sequans Communications receives takeover bid from Renesas at $5.65/share"}, "M_AND_A"),
    TaskExample("ma_006", {"ticker": "KELYB", "gap_pct": 455, "headline": "Kelly Services class B announces merger with Adecco Group, special dividend"}, "M_AND_A"),
    TaskExample("ma_007", {"ticker": "PAYC", "gap_pct": 12, "headline": "Paycom in advanced acquisition talks with Workday, deal could close Q1"}, "M_AND_A"),
    TaskExample("ma_008", {"ticker": "ENPH", "gap_pct": 19, "headline": "Enphase Energy completes Sunpower assets acquisition for $45M cash"}, "M_AND_A"),

    # CONTRACT (n=7)
    TaskExample("ctrl_001", {"ticker": "MRNA", "gap_pct": 28, "headline": "Moderna wins $1.2B BARDA contract for next-gen pandemic preparedness vaccines"}, "CONTRACT"),
    TaskExample("ctrl_002", {"ticker": "PLTR", "gap_pct": 16, "headline": "Palantir awarded $480M Army contract for AI battlefield systems through 2030"}, "CONTRACT"),
    TaskExample("ctrl_003", {"ticker": "RKLB", "gap_pct": 22, "headline": "Rocket Lab signs multi-launch agreement with Synspective for 11 missions"}, "CONTRACT"),
    TaskExample("ctrl_004", {"ticker": "CRDU", "gap_pct": 71, "headline": "CRDX announces strategic partnership with Microsoft for cloud AI integration"}, "CONTRACT"),
    TaskExample("ctrl_005", {"ticker": "AI", "gap_pct": 15, "headline": "C3.ai expands partnership with US Air Force, $50M expansion award"}, "CONTRACT"),
    TaskExample("ctrl_006", {"ticker": "NVDA", "gap_pct": 6, "headline": "Nvidia and Saudi Arabia announce $5B AI infrastructure partnership"}, "CONTRACT"),
    TaskExample("ctrl_007", {"ticker": "GE", "gap_pct": 9, "headline": "GE Aerospace receives $1.7B order for LEAP engines from major airline"}, "CONTRACT"),

    # OFFERING (BEARISH) (n=8)
    TaskExample("off_001", {"ticker": "DILU", "gap_pct": -22, "headline": "DILU Inc files for 12M share secondary offering, pricing TBD"}, "OFFERING"),
    TaskExample("off_002", {"ticker": "ATM", "gap_pct": -15, "headline": "Company announces $50M at-the-market equity program, dilutive financing"}, "OFFERING"),
    TaskExample("off_003", {"ticker": "MULN", "gap_pct": -28, "headline": "Mullen Automotive files S-3 shelf registration for up to $200M securities"}, "OFFERING"),
    TaskExample("off_004", {"ticker": "EVTL", "gap_pct": -19, "headline": "Vertical Aerospace announces $130M PIPE financing at 12% discount"}, "OFFERING"),
    TaskExample("off_005", {"ticker": "TUP", "gap_pct": -11, "headline": "Tupperware Brands prices $40M follow-on offering at $0.45/share"}, "OFFERING"),
    TaskExample("off_006", {"ticker": "AMC", "gap_pct": -8, "headline": "AMC Entertainment to issue 50M new shares to repay debt"}, "OFFERING"),
    TaskExample("off_007", {"ticker": "SOS", "gap_pct": -25, "headline": "SOS Limited announces 10M ADS registered direct offering"}, "OFFERING"),
    TaskExample("off_008", {"ticker": "GME", "gap_pct": -9, "headline": "GameStop files prospectus supplement for 75M share ATM program"}, "OFFERING"),

    # SQUEEZE (n=5)
    TaskExample("sqz_001", {"ticker": "SQZD", "gap_pct": 110, "headline": "Stock surges 100%+ on Reddit-driven retail buying, short interest 35%"}, "SQUEEZE"),
    TaskExample("sqz_002", {"ticker": "BBBY", "gap_pct": 65, "headline": "Bed Bath & Beyond rallies as retail traders coordinate on social platforms"}, "SQUEEZE"),
    TaskExample("sqz_003", {"ticker": "ATER", "gap_pct": 88, "headline": "Aterian short interest hits 41%, gamma exposure builds at $5 strike"}, "SQUEEZE"),
    TaskExample("sqz_004", {"ticker": "GME", "gap_pct": 73, "headline": "GameStop short squeeze accelerates, options volume 12x average"}, "SQUEEZE"),
    TaskExample("sqz_005", {"ticker": "RDBX", "gap_pct": 124, "headline": "Redbox stock spikes on extreme retail interest, unusual options activity"}, "SQUEEZE"),

    # NONE (technical/no clear catalyst) (n=4)
    TaskExample("none_001", {"ticker": "TECH", "gap_pct": 12, "headline": "Stock breaks out of multi-month consolidation pattern on heavy volume"}, "NONE"),
    TaskExample("none_002", {"ticker": "CHRT", "gap_pct": 9, "headline": "Shares cross 50-day moving average, RSI shows momentum reversal"}, "NONE"),
    TaskExample("none_003", {"ticker": "NEUT", "gap_pct": 8, "headline": "Stock continues recent uptrend amid sector rotation"}, "NONE"),
    TaskExample("none_004", {"ticker": "FLAT", "gap_pct": 7, "headline": "Trading volume spikes with no apparent news catalyst"}, "NONE"),
]


# ── Public registry ──────────────────────────────────────────────────────


_TASK_BUILDERS: dict[str, Callable[[], Task]] = {
    "catalyst_classification": CatalystClassificationTask.build,
}


def load_task(name: str) -> Task:
    if name not in _TASK_BUILDERS:
        raise ValueError(f"no task registered as {name!r}; known: {list(_TASK_BUILDERS)}")
    return _TASK_BUILDERS[name]()
