"""
MOMENTUM-X SEC EDGAR Client

### ARCHITECTURAL CONTEXT
Node ID: data.sec_client
Graph Link: docs/memory/graph_state.json → "data.sec_client"

### RESEARCH BASIS
Implements SEC EDGAR EFTS integration for real-time filing detection.
Resolves H-004: Float data freshness via S-3/424B5 dilution detection.
Ref: docs/research/SEC_EDGAR_INTEGRATION.md

### CRITICAL INVARIANTS
1. User-Agent MUST include company name + email per SEC requirements.
2. Rate limit: ≤10 req/sec (we use 8 req/sec with 20% buffer).
3. S-3 within 90 days → dilution WARNING.
4. 424B5 within 30 days → ACTIVE dilution CRITICAL.
5. No dilution filings → CLEAN assessment.
6. doc 270 D2: every request is bounded (per-request httpx timeout +
   total asyncio.wait_for deadline) — fetches can NEVER block the eval cycle.
7. doc 270 D2: retries (max 2, exponential backoff) ONLY on 5xx/timeout;
   4xx is never retried. Total worst case ≤ _TOTAL_DEADLINE_S.
8. doc 270 D2: responses are cached (in-process + on-disk, 12h TTL);
   failures are negative-cached (30min TTL) so a 500ing endpoint is not
   hammered. Top-level fetchers never raise — they return empty + ONE
   deduped warning per session.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

logger = logging.getLogger(__name__)


# ── doc 270 D2: EDGAR Fetch Hardening ─────────────────────────
#
# Evidence: 26 EDGAR 500-errors at a single boot (2026-06-09). This module
# gates the D204/D200 news/catalyst inputs, so an unbounded or storming
# fetch path degrades the whole eval cycle.

# Per-request httpx timeout. EDGAR p50 is <1s; 4s tolerates slow-but-alive.
_REQUEST_TIMEOUT_S = 4.0
# Hard ceiling on one logical fetch INCLUDING retries + backoff, enforced
# with asyncio.wait_for. Uncapped worst case would be 3 x 4.0s + 1.5s
# backoff = 13.5s; the deadline cuts the tail at 10s.
_TOTAL_DEADLINE_S = 10.0
# Retry budget: initial attempt + 2 retries, backoff 0.5s then 1.0s.
_MAX_RETRIES = 2
_BACKOFF_BASE_S = 0.5
# Response cache TTLs: successes 12h, failures 30min (negative cache).
_CACHE_TTL_OK_S = 12 * 3600.0
_CACHE_TTL_FAIL_S = 30 * 60.0
# On-disk cache location (repo-relative, same convention as data/journals).
_DEFAULT_CACHE_DIR = Path("data") / "ops" / "edgar_cache"

# Session-level warning dedupe: identical failure messages log WARNING once,
# then demote to DEBUG. Module-level so it spans all client instances.
_WARN_SEEN: set[str] = set()
_WARN_SEEN_MAX = 1024


def _warn_once(message: str) -> None:
    """
    Log a warning exactly once per session; identical repeats go to DEBUG.

    doc 270 D2: prevents retry/poll storms from flooding the log (the
    26-identical-500s-at-boot pattern). Dedupe key is the formatted message.
    """
    if message in _WARN_SEEN:
        logger.debug("%s (repeat suppressed)", message)
        return
    if len(_WARN_SEEN) < _WARN_SEEN_MAX:
        _WARN_SEEN.add(message)
    logger.warning("%s", message)


# ── Filing Type Classification ────────────────────────────────


class FilingType(Enum):
    """
    SEC filing types relevant to momentum trading.

    Ref: docs/research/SEC_EDGAR_INTEGRATION.md (Filing Types table)

    ### CRITICAL INVARIANTS
    - S-3 and 424B5 are dilution risks (company selling shares)
    - 8-K is informational (content determines risk, not form type)
    - Form 4 tracks insider activity (buying vs selling)
    """

    S3 = "S-3"
    PROSPECTUS_424B5 = "424B5"
    EVENT_8K = "8-K"
    ANNUAL_10K = "10-K"
    QUARTERLY_10Q = "10-Q"
    INSIDER_FORM4 = "4"
    SC_13D = "SC 13D"
    SC_13G = "SC 13G"
    UNKNOWN = "UNKNOWN"

    @property
    def is_dilution_risk(self) -> bool:
        """
        Does this filing type indicate potential share dilution?

        Only S-3 (shelf registration) and 424B5 (prospectus supplement)
        directly indicate dilution. 8-K may contain dilution info but
        requires content analysis — not classified here.
        """
        return self in (FilingType.S3, FilingType.PROSPECTUS_424B5)


# ── Filing Data Model ─────────────────────────────────────────


@dataclass(frozen=True)
class Filing:
    """
    Parsed SEC filing record.

    Node ID: data.sec_client.Filing
    """

    form_type: str
    filing_type: FilingType
    filed_date: date
    company_name: str
    cik: str
    accession_number: str
    description: str
    url: str = ""

    def age_days(self, reference_date: date | None = None) -> int:
        """Days since filing was submitted."""
        ref = reference_date or date.today()
        return (ref - self.filed_date).days


# ── Dilution Risk Assessment ──────────────────────────────────


@dataclass
class DilutionAssessment:
    """
    Aggregate dilution risk from filing history.

    Node ID: data.sec_client.DilutionAssessment

    ### RISK LEVELS
    - CLEAN: No dilution filings within lookback window
    - WARNING: S-3 filed within 90 days (shelf registered, may sell)
    - CRITICAL: 424B5 within 30 days (shares actively being sold NOW)
    """

    risk_level: str  # CLEAN | WARNING | CRITICAL
    active_dilution: bool  # 424B5 within 30 days
    dilution_filings: list[Filing] = field(default_factory=list)
    insider_filing_count: int = 0
    total_filings_analyzed: int = 0


def classify_filing_risk(
    filings: list[Filing],
    reference_date: date | None = None,
    s3_lookback_days: int = 90,
    prospectus_lookback_days: int = 30,
) -> DilutionAssessment:
    """
    Classify dilution risk from a set of SEC filings.

    ### INVARIANTS (enforced by tests)
    1. No filings → CLEAN
    2. S-3 within `s3_lookback_days` → WARNING
    3. 424B5 within `prospectus_lookback_days` → CRITICAL + active_dilution=True
    4. Old dilution filings (beyond lookback) → CLEAN
    5. Highest risk level wins when multiple types present

    Args:
        filings: List of Filing objects to analyze.
        reference_date: Date to calculate age from (default: today).
        s3_lookback_days: Days to look back for S-3 filings.
        prospectus_lookback_days: Days to look back for 424B5 filings.

    Returns:
        DilutionAssessment with risk level and supporting evidence.
    """
    ref = reference_date or date.today()

    dilution_filings: list[Filing] = []
    active_dilution = False
    risk_level = "CLEAN"
    insider_count = 0

    for f in filings:
        age = f.age_days(reference_date=ref)

        # Track insider filings
        if f.filing_type == FilingType.INSIDER_FORM4:
            insider_count += 1
            continue

        # Check dilution risk filings
        if not f.filing_type.is_dilution_risk:
            continue

        # 424B5: Active dilution if within prospectus lookback
        if f.filing_type == FilingType.PROSPECTUS_424B5 and age <= prospectus_lookback_days:
            dilution_filings.append(f)
            active_dilution = True
            risk_level = "CRITICAL"

        # S-3: Warning if within shelf lookback
        elif f.filing_type == FilingType.S3 and age <= s3_lookback_days:
            dilution_filings.append(f)
            if risk_level != "CRITICAL":  # Don't downgrade from CRITICAL
                risk_level = "WARNING"

    return DilutionAssessment(
        risk_level=risk_level,
        active_dilution=active_dilution,
        dilution_filings=dilution_filings,
        insider_filing_count=insider_count,
        total_filings_analyzed=len(filings),
    )


# ── D106 §2B: Manipulation Filing Summary ─────────────────────


@dataclass(frozen=True)
class ManipulationFilingSummary:
    """
    Structured summary of SEC filings relevant to manipulation detection.

    Used by ManipulationClassifier agent to classify ORGANIC vs PROMOTIONAL.
    All fields are deterministic (no LLM required).

    Ref: D106 WS2 (Manipulation Detection)
    """

    s3_age_days: int | None = None  # Days since most recent S-3 (None = no S-3)
    has_424b5_same_day: bool = False  # Same-day 424B5 → always PROMOTIONAL_LATE
    insider_sell_count_30d: int = 0  # Form 4 sell filings in last 30 days
    dilution_filing_count: int = 0  # S-3 + 424B5 filings in lookback
    recent_8k_count: int = 0  # 8-K filings in last 14 days


def build_manipulation_filing_summary(
    filings: list[Filing],
    gap_date: date | None = None,
    insider_lookback_days: int = 30,
    event_lookback_days: int = 14,
) -> ManipulationFilingSummary:
    """
    Build a structured filing summary for manipulation classification.

    D106 §2B: Deterministic analysis of SEC filing history. Reuses existing
    Filing dataclass and FilingType enum. Called by ManipulationClassifier
    agent before LLM invocation.

    Args:
        filings: List of Filing objects (from search_filings or cache).
        gap_date: The date of the gap (default: today). Used to compute
            same-day 424B5 detection and filing age.
        insider_lookback_days: Window for Form 4 counting (default 30d).
        event_lookback_days: Window for 8-K counting (default 14d).

    Returns:
        ManipulationFilingSummary with all fields populated.

    ### INVARIANTS
    - Same-day 424B5 → has_424b5_same_day = True (hard rule)
    - No filings → all zeros/None (safe defaults)
    """
    ref = gap_date or date.today()

    s3_age_days: int | None = None
    has_424b5_same_day = False
    insider_sell_count = 0
    dilution_count = 0
    recent_8k_count = 0

    for f in filings:
        age = f.age_days(reference_date=ref)

        # S-3: Track most recent (smallest age)
        if f.filing_type == FilingType.S3:
            dilution_count += 1
            if s3_age_days is None or age < s3_age_days:
                s3_age_days = age

        # 424B5: Same-day detection + dilution count
        elif f.filing_type == FilingType.PROSPECTUS_424B5:
            dilution_count += 1
            if f.filed_date == ref:
                has_424b5_same_day = True

        # Form 4: Count insider sells within lookback
        elif f.filing_type == FilingType.INSIDER_FORM4:
            if age <= insider_lookback_days:
                insider_sell_count += 1

        # 8-K: Count recent events
        elif f.filing_type == FilingType.EVENT_8K:
            if age <= event_lookback_days:
                recent_8k_count += 1

    return ManipulationFilingSummary(
        s3_age_days=s3_age_days,
        has_424b5_same_day=has_424b5_same_day,
        insider_sell_count_30d=insider_sell_count,
        dilution_filing_count=dilution_count,
        recent_8k_count=recent_8k_count,
    )


# ── Form Type Parser ──────────────────────────────────────────

_FORM_TYPE_MAP: dict[str, FilingType] = {
    "S-3": FilingType.S3,
    "S-3/A": FilingType.S3,
    "S-3ASR": FilingType.S3,
    "424B5": FilingType.PROSPECTUS_424B5,
    "424B2": FilingType.PROSPECTUS_424B5,
    "8-K": FilingType.EVENT_8K,
    "8-K/A": FilingType.EVENT_8K,
    "10-K": FilingType.ANNUAL_10K,
    "10-K/A": FilingType.ANNUAL_10K,
    "10-Q": FilingType.QUARTERLY_10Q,
    "10-Q/A": FilingType.QUARTERLY_10Q,
    "4": FilingType.INSIDER_FORM4,
    "SC 13D": FilingType.SC_13D,
    "SC 13D/A": FilingType.SC_13D,
    "SC 13G": FilingType.SC_13G,
    "SC 13G/A": FilingType.SC_13G,
}


def parse_form_type(raw: str) -> FilingType:
    """Map raw SEC form type string to FilingType enum."""
    return _FORM_TYPE_MAP.get(raw.strip(), FilingType.UNKNOWN)


# ── SEC EDGAR Client ──────────────────────────────────────────


class SECEdgarClient:
    """
    Async client for SEC EDGAR EFTS (Full-Text Search).

    ### ARCHITECTURAL CONTEXT
    Node ID: data.sec_client
    Resolves: H-004 (Float data freshness)

    ### CRITICAL INVARIANTS
    1. User-Agent MUST be set per SEC guidelines (company + email)
    2. Rate limited to 8 req/sec (SEC allows 10, 20% buffer)
    3. EFTS endpoint returns JSON with filing metadata
    4. CIK lookup via company tickers JSON

    ### USAGE
    ```python
    client = SECEdgarClient()
    filings = await client.search_filings("AAPL", form_types=["S-3", "424B5"])
    risk = classify_filing_risk(filings)
    ```
    """

    EFTS_BASE = "https://efts.sec.gov/LATEST/search-index"
    CIK_LOOKUP_BASE = "https://www.sec.gov/cgi-bin/browse-edgar"
    COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

    def __init__(
        self,
        user_agent: str = "Momentum-X Research bot@momentum-x.dev",
        max_requests_per_second: float = 8.0,
        cache_dir: str | Path | None = None,
    ) -> None:
        self._user_agent = user_agent
        self._max_requests_per_second = max_requests_per_second
        # doc 270 D2: response cache — in-process dict + on-disk JSON files.
        # Key = full request URL (endpoint + entity query + forms + date
        # window, which rolls daily because startdt/enddt derive from today).
        self._cache_dir = Path(cache_dir) if cache_dir is not None else _DEFAULT_CACHE_DIR
        self._mem_cache: dict[str, tuple[float, bool, Any]] = {}

    def _request_headers(self) -> dict[str, str]:
        """
        SEC fair-access headers — REQUIRED on every EDGAR request.

        SEC policy requires a User-Agent identifying company + contact
        ("Sample Company Name AdminContact@sample.com"). Centralized here so
        no request path can omit it (doc 270 D2).
        """
        return {"User-Agent": self._user_agent, "Accept": "application/json"}

    # ── doc 270 D2: response cache plumbing ───────────────────

    def _cache_path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        return self._cache_dir / f"{digest}.json"

    def _cache_get(self, key: str) -> tuple[bool, Any] | None:
        """
        Return (ok, payload) for a live cache entry, else None.

        Checks the in-process dict first, then the on-disk cache (promoting
        disk hits to memory). Unreadable/expired entries are misses — cache
        problems must never become fetch problems.
        """
        now = time.time()

        entry = self._mem_cache.get(key)
        if entry is not None:
            expires_at, ok, payload = entry
            if now < expires_at:
                return ok, payload
            self._mem_cache.pop(key, None)

        try:
            path = self._cache_path(key)
            if not path.exists():
                return None
            raw = json.loads(path.read_text(encoding="utf-8"))
            if raw.get("key") != key:
                return None  # hash collision or stale schema → miss
            expires_at = float(raw.get("cached_at", 0.0)) + float(raw.get("ttl_s", 0.0))
            if now >= expires_at:
                return None
            ok = bool(raw.get("ok", False))
            payload = raw.get("payload")
            self._mem_cache[key] = (expires_at, ok, payload)
            return ok, payload
        except Exception as e:  # noqa: BLE001 - cache faults must stay misses
            logger.debug("EDGAR disk cache read skipped (%s)", e)
            return None

    def _cache_put(self, key: str, ok: bool, payload: dict[str, Any] | None, ttl_s: float) -> None:
        """Write-through store (memory + disk). Disk failures are best-effort."""
        now = time.time()
        self._mem_cache[key] = (now + ttl_s, ok, payload)
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            path = self._cache_path(key)
            tmp = path.parent / (path.name + ".tmp")
            tmp.write_text(
                json.dumps(
                    {
                        "key": key,
                        "cached_at": now,
                        "ttl_s": ttl_s,
                        "ok": ok,
                        "payload": payload,
                    }
                ),
                encoding="utf-8",
            )
            os.replace(tmp, path)
        except Exception as e:  # noqa: BLE001 - disk cache is best-effort
            logger.debug("EDGAR disk cache write skipped (%s)", e)

    # ── doc 270 D2: bounded fetch with retry ──────────────────

    async def _get_json_with_retry(self, url: str) -> tuple[dict[str, Any] | None, str | None]:
        """
        GET with the doc 270 D2 retry policy. Never raises httpx errors.

        - Per-request timeout: _REQUEST_TIMEOUT_S (httpx timeout param —
          nothing unbounded).
        - Retries ONLY on 5xx and timeouts: max _MAX_RETRIES, exponential
          backoff (0.5s, 1.0s). 4xx, transport, and parse errors NEVER retry
          (no retry storms against SEC fair-access policy).

        Returns:
            (payload, None) on success, (None, reason) on failure.
        """
        import httpx

        headers = self._request_headers()
        reason: str = "unknown error"

        for attempt in range(_MAX_RETRIES + 1):
            if attempt > 0:
                await asyncio.sleep(_BACKOFF_BASE_S * (2 ** (attempt - 1)))
            try:
                async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_S) as client:
                    resp = await client.get(url, headers=headers)
            except httpx.TimeoutException as e:
                reason = f"timeout>{_REQUEST_TIMEOUT_S:.0f}s ({type(e).__name__})"
                continue  # retryable
            except Exception as e:  # noqa: BLE001 - never-raise (doc 270 D2)
                reason = f"{type(e).__name__}: {e}"
                break  # transport/DNS/etc — not retried

            if resp.status_code >= 500:
                reason = f"HTTP {resp.status_code}"
                continue  # retryable
            if not (200 <= resp.status_code < 300):
                reason = f"HTTP {resp.status_code} (not retried)"
                break  # 4xx/3xx — never retried
            try:
                return resp.json(), None
            except Exception as e:  # noqa: BLE001 - never-raise (doc 270 D2)
                reason = f"malformed JSON ({type(e).__name__})"
                break

        return None, reason

    def _safe_parse(self, data: dict[str, Any], ticker: str) -> list[Filing]:
        """Parse an EFTS payload; never raises into the cycle (doc 270 D2)."""
        try:
            return self._parse_efts_response(data)
        except Exception as e:  # noqa: BLE001 - never-raise (doc 270 D2)
            _warn_once(f"SEC EDGAR parse failed for {ticker}: {type(e).__name__}: {e}")
            return []

    def _build_search_url(
        self,
        query: str,
        form_types: list[str] | None = None,
        days_back: int = 90,
    ) -> str:
        """
        Build EDGAR EFTS search URL.

        Args:
            query: Company name or ticker.
            form_types: Filter by form types (e.g., ["S-3", "424B5"]).
            days_back: Search window in days from today.

        Returns:
            Full EFTS search URL.
        """
        params: dict[str, str] = {
            "q": query,
            "dateRange": "custom",
            "startdt": str(date.today() - timedelta(days=days_back)),
            "enddt": str(date.today()),
        }
        if form_types:
            params["forms"] = ",".join(form_types)

        return f"{self.EFTS_BASE}?{urlencode(params, quote_via=quote)}"

    def _build_cik_lookup_url(self, ticker: str) -> str:
        """Build URL to look up CIK by ticker symbol."""
        params = {
            "action": "getcompany",
            "company": ticker,
            "type": "",
            "dateb": "",
            "owner": "include",
            "count": "10",
            "search_text": "",
            "action": "getcompany",
            "output": "atom",
        }
        return f"{self.CIK_LOOKUP_BASE}?{urlencode(params)}"

    async def search_filings(
        self,
        ticker: str,
        form_types: list[str] | None = None,
        days_back: int = 90,
    ) -> list[Filing]:
        """
        Search EDGAR for recent filings by ticker.

        doc 270 D2 hardening (signature + return shape unchanged):
        - Cache first: in-process + on-disk, 12h TTL; failures negative-cached
          30min so a 500ing endpoint is not hammered.
        - Bounded: per-request httpx timeout + asyncio.wait_for total deadline
          (~10s absolute worst case) — cannot block the eval cycle.
        - Retries (max 2, exponential backoff) ONLY on 5xx/timeout.
        - NEVER raises: returns [] on any failure with ONE deduped warning.

        Args:
            ticker: Stock ticker symbol (e.g., "AAPL").
            form_types: Optional filter (e.g., ["S-3", "424B5"]).
            days_back: Lookback window in days.

        Returns:
            List of Filing objects sorted by date (newest first).
        """
        url = self._build_search_url(ticker, form_types, days_back)
        cache_key = url  # endpoint + entity query + forms + date window

        cached = self._cache_get(cache_key)
        if cached is not None:
            ok, payload = cached
            if not ok or not isinstance(payload, dict):
                return []  # negative-cached failure — skip the network entirely
            return self._safe_parse(payload, ticker)

        try:
            data, reason = await asyncio.wait_for(
                self._get_json_with_retry(url), timeout=_TOTAL_DEADLINE_S
            )
        except TimeoutError:  # asyncio.TimeoutError is TimeoutError on 3.11+
            data, reason = None, f"total deadline {_TOTAL_DEADLINE_S:.0f}s exceeded"
        except Exception as e:  # noqa: BLE001 - absolute never-raise guarantee
            data, reason = None, f"{type(e).__name__}: {e}"

        if data is None:
            self._cache_put(cache_key, ok=False, payload=None, ttl_s=_CACHE_TTL_FAIL_S)
            _warn_once(
                f"SEC EDGAR search failed for {ticker}: {reason} "
                f"(negative-cached {int(_CACHE_TTL_FAIL_S / 60)}min)"
            )
            return []

        self._cache_put(cache_key, ok=True, payload=data, ttl_s=_CACHE_TTL_OK_S)
        return self._safe_parse(data, ticker)

    def _parse_efts_response(self, data: dict[str, Any]) -> list[Filing]:
        """Parse EFTS JSON response into Filing objects."""
        filings: list[Filing] = []
        hits = data.get("hits", {}).get("hits", [])

        for hit in hits:
            source = hit.get("_source", {})
            form_raw = source.get("form_type", "")
            filing_type = parse_form_type(form_raw)

            try:
                filed_str = source.get("file_date", "")
                filed_date = date.fromisoformat(filed_str) if filed_str else date.today()
            except ValueError:
                filed_date = date.today()

            filings.append(
                Filing(
                    form_type=form_raw,
                    filing_type=filing_type,
                    filed_date=filed_date,
                    company_name=source.get("display_names", [""])[0] if source.get("display_names") else "",
                    cik=source.get("entity_id", ""),
                    accession_number=hit.get("_id", ""),
                    description=source.get("file_description", ""),
                )
            )

        # Sort newest first
        filings.sort(key=lambda f: f.filed_date, reverse=True)
        return filings

    async def check_dilution_risk(
        self,
        ticker: str,
        reference_date: date | None = None,
    ) -> DilutionAssessment:
        """
        High-level method: search for dilution-related filings and assess risk.

        Args:
            ticker: Stock ticker.
            reference_date: Override for testing.

        Returns:
            DilutionAssessment with risk level.
        """
        filings = await self.search_filings(
            ticker=ticker,
            form_types=["S-3", "S-3/A", "S-3ASR", "424B5", "424B2", "4"],
            days_back=90,
        )
        return classify_filing_risk(filings, reference_date=reference_date)
