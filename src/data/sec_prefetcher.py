"""D191: SEC EDGAR Real-Time Pre-Fetcher.

### ARCHITECTURAL CONTEXT
Node ID: data.sec_prefetcher
Graph Link: docs/memory/graph_state.json → "data.sec_prefetcher"

Deterministic SEC filing analysis during the premarket window (4:30–9:30 AM ET).
No LLM needed — pure pattern matching on filing types and dates.

### DESIGN RATIONALE
The existing sec_client.py uses the EFTS full-text search API (efts.sec.gov),
which is optimised for keyword search across document bodies.
This module uses the EDGAR submissions REST API (data.sec.gov/submissions/),
which returns structured filing metadata directly keyed by CIK — faster,
more reliable, and purpose-built for "what did this company file recently?"

The two clients are complementary:
  - sec_client.py  → keyword search, manipulation filing summary, content queries
  - sec_prefetcher.py → premarket batch prefetch, ATM detection, faller integration

### CRITICAL INVARIANTS
1. User-Agent MUST include company name + email per SEC requirements.
2. Rate limit: ≤10 req/sec (semaphore set to 8 with 20% safety buffer).
3. 424B5 filed today or yesterday → DILUTION_ACTIVE (hard BEAR).
4. ATM language in 424B5 text → DILUTION_ATM (hardest possible BEAR).
5. 8-K filed today → MATERIAL_EVENT (potential real catalyst).
6. CIK cache is populated from company_tickers.json at first lookup.

### PREMARKET TIMING
  - 4:30 AM ET: Prefetch triggered for initial scanner candidates
  - 6:00 AM ET: EDGAR opens and accepts new filings
  - 9:30 AM ET: Market open — all analysis must be complete

Ref: docs/decisions/D191_sec_prefetcher.md
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


# ── Signal Classification ─────────────────────────────────────────────────────


class FilingSignal(str, Enum):
    """
    Deterministic signal derived from SEC filing analysis.

    Signals are ordered from most bearish to most bullish. The faller detector
    uses these to apply score adjustments defined in FallerDetectionConfig.

    ### INVARIANTS
    - DILUTION_ATM is always worse than DILUTION_ACTIVE
    - Multiple signals coexist: a 424B5 may also trigger MATERIAL_EVENT if 8-K filed
    - UNKNOWN means the fetch failed — treated conservatively (no score change)
    """

    DILUTION_ATM = "dilution_atm"        # 424B5 + ATM language → hardest BEAR
    DILUTION_ACTIVE = "dilution_active"  # 424B5 filed today/yesterday → hard BEAR
    SHELF_REGISTERED = "shelf_registered"  # S-3 filed recently → caution
    MATERIAL_EVENT = "material_event"    # 8-K filed today → potential catalyst
    EARNINGS_FILED = "earnings_filed"    # 10-Q/10-K recent
    CLEAN = "clean"                      # No concerning filings
    UNKNOWN = "unknown"                  # Fetch failed — no data available


# ── Result Model ──────────────────────────────────────────────────────────────


@dataclass
class SECFilingResult:
    """
    Output of SECPrefetcher.analyze_ticker() for one ticker.

    Passed to FallerRiskDetector.score() as the optional `sec_result` argument.
    All fields default to neutral/unknown values so callers can safely destructure
    without checking for None.

    Node ID: data.sec_prefetcher.SECFilingResult
    """

    ticker: str
    cik: Optional[str] = None
    signal: FilingSignal = FilingSignal.UNKNOWN

    # Evidence
    filings_found: list[dict] = field(default_factory=list)  # {form, date, accession}
    dilution_detected: bool = False    # Any 424B5/S-3 within lookback
    atm_detected: bool = False         # ATM language in 424B5 text
    material_event_detected: bool = False  # 8-K filed today

    # 8-K item codes extracted from the filing (e.g. ["1.01", "8.01"])
    eight_k_items: list[str] = field(default_factory=list)

    # Diagnostics
    confidence: float = 0.0        # 0-1: how confident is this signal
    fetch_latency_ms: float = 0.0  # Wall time for this ticker
    error: Optional[str] = None    # Non-None if fetch failed

    def is_hard_bear(self) -> bool:
        """True if this result should block long entry."""
        return self.signal in (FilingSignal.DILUTION_ACTIVE, FilingSignal.DILUTION_ATM)

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "cik": self.cik,
            "signal": self.signal.value,
            "dilution_detected": self.dilution_detected,
            "atm_detected": self.atm_detected,
            "material_event_detected": self.material_event_detected,
            "eight_k_items": self.eight_k_items,
            "confidence": round(self.confidence, 3),
            "fetch_latency_ms": round(self.fetch_latency_ms, 1),
            "error": self.error,
        }


# ── ATM Detection Patterns ────────────────────────────────────────────────────

# "at-the-market" offerings allow continuous dilution at prevailing market prices.
# These are the most damaging dilution structure for retail longs: the company
# sells shares into every rally, suppressing any upward move.
ATM_PATTERNS: list[re.Pattern] = [
    re.compile(r"at[- ]the[- ]market\s+offering", re.IGNORECASE),
    re.compile(r"at[- ]the[- ]market", re.IGNORECASE),
    re.compile(r"Rule\s+415\s*\(\s*a\s*\)\s*\(\s*4\s*\)", re.IGNORECASE),
    re.compile(r"sales\s+agent", re.IGNORECASE),
    re.compile(r"distribution\s+agreement", re.IGNORECASE),
    re.compile(r"at\s+prices\s+related\s+to\s+(?:such\s+)?prevailing\s+market\s+prices", re.IGNORECASE),
]

# Forms that indicate dilution risk (company selling shares)
DILUTION_FORMS: frozenset[str] = frozenset({"424B5", "424B2", "S-3", "S-3/A", "S-3ASR", "S-1", "S-1/A"})

# Forms that indicate prospectus supplement (active dilution — most critical)
PROSPECTUS_FORMS: frozenset[str] = frozenset({"424B5", "424B2"})

# Material event forms (may indicate real catalyst)
MATERIAL_FORMS: frozenset[str] = frozenset({"8-K", "8-K/A"})

# Earnings forms
EARNINGS_FORMS: frozenset[str] = frozenset({"10-K", "10-K/A", "10-Q", "10-Q/A"})

# Lookback windows
# 3-day windows accommodate weekend/holiday gaps:
#   Friday filing + weekend = Monday check at 3 days → still caught.
DILUTION_LOOKBACK_DAYS = 3    # 424B5: today/yesterday/2d ago = CRITICAL (covers weekends)
SHELF_LOOKBACK_DAYS = 90      # S-3: within 90 days = SHELF_REGISTERED
MATERIAL_LOOKBACK_DAYS = 3    # 8-K: within 3 days = MATERIAL_EVENT (covers weekends)
EARNINGS_LOOKBACK_DAYS = 7    # 10-K/Q: within a week = EARNINGS_FILED

# Date formats SEC uses (primary + fallback variants)
_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%Y%m%d", "%d-%m-%Y")


# ── Prefetcher ────────────────────────────────────────────────────────────────


class SECPrefetcher:
    """
    Fetches and analyses SEC filings during the premarket window.

    Uses the EDGAR submissions REST API (data.sec.gov/submissions/) rather than
    the EFTS search API. This endpoint returns structured metadata for all recent
    filings for a given company, keyed by CIK.

    ### USAGE
    ```python
    prefetcher = SECPrefetcher()
    async with prefetcher:
        results = await prefetcher.analyze_batch(["NVDA", "TSLA", "GME"])
    for ticker, result in results.items():
        if result.is_hard_bear():
            print(f"{ticker}: DILUTION DETECTED — block long")
    ```

    ### RATE LIMITING
    The SEC allows 10 requests/second. We cap at 8 (20% buffer) using asyncio.Semaphore.
    Each HTTP request is wrapped in a semaphore acquire/release cycle with a 125ms
    minimum gap between releases (8 req/sec = 125ms/req).

    Node ID: data.sec_prefetcher.SECPrefetcher
    """

    SEC_SUBMISSIONS_BASE = "https://data.sec.gov/submissions"
    COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
    SEC_FILING_BASE = "https://www.sec.gov/Archives/edgar/data"

    USER_AGENT = "MomentumX Trading Research admin@momentum-x.dev"
    MAX_REQUESTS_PER_SEC = 8  # SEC allows 10; 20% safety buffer

    # Fetch up to 2KB of filing text for ATM detection — sufficient for the cover page
    ATM_FETCH_CHARS = 8_000

    def __init__(self) -> None:
        self._cik_cache: dict[str, str] = {}         # ticker.upper() → zero-padded CIK
        self._results_cache: dict[str, SECFilingResult] = {}  # ticker → result
        self._semaphore = asyncio.Semaphore(self.MAX_REQUESTS_PER_SEC)
        self._last_request_time: float = 0.0
        self._session: Optional[object] = None       # aiohttp.ClientSession
        self._cik_load_lock = asyncio.Lock()         # prevents duplicate concurrent CIK index loads

    # ── Context manager ───────────────────────────────────────────────────────

    async def __aenter__(self) -> "SECPrefetcher":
        await self._get_session()
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()

    # ── Session management ────────────────────────────────────────────────────

    async def _get_session(self):
        """Return (or create) the shared aiohttp.ClientSession."""
        import aiohttp

        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(
                limit=20,                   # Max total connections
                limit_per_host=5,           # Max per host (SEC.gov) — avoids pool exhaustion
                ttl_dns_cache=300,          # Cache DNS for 5 min — handles DNS failure gracefully
                enable_cleanup_closed=True, # Reclaim closed sockets promptly
            )
            timeout = aiohttp.ClientTimeout(total=15, connect=5)
            self._session = aiohttp.ClientSession(
                headers={
                    "User-Agent": self.USER_AGENT,
                    "Accept-Encoding": "gzip, deflate",
                    "Accept": "application/json",
                },
                connector=connector,
                timeout=timeout,
            )
        return self._session

    # ── Rate-limited HTTP fetch with retry ───────────────────────────────────

    async def _rate_limit(self) -> None:
        """Enforce the inter-request minimum gap for SEC rate limiting."""
        min_gap = 1.0 / self.MAX_REQUESTS_PER_SEC
        elapsed = time.monotonic() - self._last_request_time
        if elapsed < min_gap:
            await asyncio.sleep(min_gap - elapsed)
        self._last_request_time = time.monotonic()

    async def _fetch_json(self, url: str, timeout: float = 15.0) -> Optional[dict]:
        """
        Fetch JSON from URL with rate limiting, retry, and exponential backoff.

        Handles 429 rate limiting, transient network errors, and partial responses.
        Returns None on unrecoverable failure — never raises.

        Retry schedule: attempt 0 immediate, 1 → ~1s, 2 → ~2s, 3 → ~4s (with jitter).
        """
        import aiohttp

        max_retries = 3
        for attempt in range(max_retries + 1):
            try:
                async with self._semaphore:
                    await self._rate_limit()
                    session = await self._get_session()
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=timeout)
                    ) as resp:
                        if resp.status == 200:
                            try:
                                return await resp.json(content_type=None)
                            except (json.JSONDecodeError, ValueError) as exc:
                                logger.warning(
                                    "SEC EDGAR truncated/invalid JSON %s: %s", url, exc
                                )
                                return None
                        elif resp.status == 429:
                            wait = (2 ** attempt) + random.uniform(0, 1)
                            logger.warning(
                                "SEC rate limited (429) attempt %d/%d, backing off %.1fs",
                                attempt + 1, max_retries + 1, wait,
                            )
                            await asyncio.sleep(wait)
                            continue
                        elif resp.status == 403:
                            logger.error("SEC access denied (403) for %s", url)
                            return None
                        else:
                            logger.debug("SEC EDGAR %s → HTTP %d", url, resp.status)
                            if attempt < max_retries:
                                await asyncio.sleep(2 ** attempt + random.uniform(0, 0.5))
                                continue
                            return None
            except (OSError, asyncio.TimeoutError) as exc:
                # Covers ConnectionError, DNS failure, SSL errors, TimeoutError
                logger.warning(
                    "SEC EDGAR fetch error attempt %d/%d %s: %s",
                    attempt + 1, max_retries + 1, url, exc,
                )
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt + random.uniform(0, 0.5))
                continue
            except Exception as exc:
                logger.warning("SEC EDGAR unexpected error %s: %s", url, exc)
                return None
        return None

    async def _fetch_text(self, url: str, timeout: float = 15.0) -> Optional[str]:
        """
        Fetch raw text (for filing body / ATM detection).

        Reads only ATM_FETCH_CHARS bytes — the cover page is sufficient.
        Handles encoding issues (ASCII/UTF-8/Latin-1) with errors='replace'.
        """
        import aiohttp

        max_retries = 3
        for attempt in range(max_retries + 1):
            try:
                async with self._semaphore:
                    await self._rate_limit()
                    session = await self._get_session()
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=timeout)
                    ) as resp:
                        if resp.status == 429:
                            wait = (2 ** attempt) + random.uniform(0, 1)
                            logger.warning("SEC rate limited (429) on text fetch, %.1fs", wait)
                            await asyncio.sleep(wait)
                            continue
                        if resp.status != 200:
                            return None
                        # Safety cap: never fetch more than ATM_FETCH_CHARS bytes
                        content = await resp.content.read(self.ATM_FETCH_CHARS)
                        # Try UTF-8 first, fall back with replacement for Latin-1/ASCII
                        return content.decode("utf-8", errors="replace")
            except (OSError, asyncio.TimeoutError) as exc:
                logger.warning(
                    "SEC EDGAR text fetch error attempt %d/%d %s: %s",
                    attempt + 1, max_retries + 1, url, exc,
                )
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt + random.uniform(0, 0.5))
                continue
            except Exception as exc:
                logger.debug("SEC EDGAR text fetch failed %s: %s", url, exc)
                return None
        return None

    # ── CIK Lookup ────────────────────────────────────────────────────────────

    async def _load_cik_index(self) -> None:
        """
        Bulk-load the company_tickers.json index into memory.

        SEC provides a single JSON file mapping all ~10K public company tickers
        to their CIKs. We load this once and cache the result. This is faster
        and more reliable than per-ticker CIK API calls.

        Thread-safe via asyncio.Lock: two concurrent callers share the same load
        rather than firing duplicate HTTP requests.
        """
        if self._cik_cache:
            return  # Already loaded (fast path, no lock needed)

        async with self._cik_load_lock:
            if self._cik_cache:
                return  # Loaded by another coroutine while we waited for the lock

            data = await self._fetch_json(self.COMPANY_TICKERS_URL, timeout=30.0)
            if not data:
                logger.warning("Failed to load SEC company tickers index")
                return

            # Format: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
            for entry in data.values():
                try:
                    ticker = str(entry.get("ticker", "")).upper().strip()
                    cik_raw = entry.get("cik_str", "")
                    if ticker and cik_raw:
                        # Zero-pad CIK to 10 digits as required by submissions API
                        cik_padded = str(int(cik_raw)).zfill(10)
                        self._cik_cache[ticker] = cik_padded
                except (TypeError, ValueError):
                    continue  # Skip malformed entries

            logger.info("SEC CIK index loaded: %d tickers", len(self._cik_cache))

    async def lookup_cik(self, ticker: str) -> Optional[str]:
        """
        Map a ticker symbol to its 10-digit SEC CIK.

        Returns None if the ticker is not found (e.g. non-US, OTC-only, or
        the bulk index hasn't loaded yet due to network failure).
        """
        await self._load_cik_index()
        return self._cik_cache.get(ticker.upper())

    # ── Filing Fetcher ────────────────────────────────────────────────────────

    @staticmethod
    def _parse_filing_date(date_str: str) -> Optional[date]:
        """
        Parse a filing date string tolerating multiple formats.

        SEC uses YYYY-MM-DD in structured JSON but legacy and alternative
        endpoints occasionally vary. Tries all formats in _DATE_FORMATS.
        Returns None if all formats fail — callers skip filings without dates.
        """
        if not date_str:
            return None
        for fmt in _DATE_FORMATS:
            try:
                return datetime.strptime(str(date_str).strip(), fmt).date()
            except (ValueError, TypeError):
                continue
        return None

    def _safe_parse_filings(self, data: dict, max_filings: int) -> list[dict]:
        """
        Defensively extract filings from a submissions JSON blob.

        Uses .get() at every level — the SEC schema has changed before and may
        change again. Missing or malformed fields produce empty/None values that
        downstream checks skip, rather than crashing.
        """
        try:
            recent = data.get("filings", {}).get("recent", {})
            if not isinstance(recent, dict):
                return []

            forms = recent.get("form", []) or []
            dates = recent.get("filingDate", []) or []
            accessions = recent.get("accessionNumber", []) or []
            primary_docs = recent.get("primaryDocument", []) or []
            descriptions = recent.get("description", []) or []

            if not forms:
                return []

            results = []
            for i in range(min(max_filings, len(forms))):
                try:
                    results.append({
                        "form": forms[i] if i < len(forms) else "",
                        "filingDate": self._parse_filing_date(dates[i] if i < len(dates) else ""),
                        "accessionNumber": accessions[i] if i < len(accessions) else "",
                        "primaryDocument": primary_docs[i] if i < len(primary_docs) else "",
                        "description": descriptions[i] if i < len(descriptions) else "",
                    })
                except (IndexError, TypeError):
                    continue
            return results
        except (KeyError, TypeError, AttributeError) as exc:
            logger.error("Failed to parse SEC filings JSON: %s", exc)
            return []

    async def fetch_recent_filings(self, cik: str, max_filings: int = 40) -> list[dict]:
        """
        Fetch recent filings from data.sec.gov/submissions/CIK##########.json.

        The submissions endpoint returns filing metadata without requiring
        any search query. It covers all forms filed by the company ordered
        newest-first. We only look at the first `max_filings` rows.

        Returns a list of dicts with keys: form, filingDate, accessionNumber.
        Returns [] on any error or empty filing history.
        """
        url = f"{self.SEC_SUBMISSIONS_BASE}/CIK{cik}.json"
        data = await self._fetch_json(url)
        if not data:
            return []

        return self._safe_parse_filings(data, max_filings)

    # ── Dilution Detection ────────────────────────────────────────────────────

    async def check_dilution(
        self,
        cik: str,
        filings: list[dict],
        reference_date: Optional[date] = None,
    ) -> tuple[bool, bool, list[dict]]:
        """
        Detect 424B5/S-3 dilution filings within the lookback window.

        Checks for prospectus supplements (424B5/424B2) filed within
        DILUTION_LOOKBACK_DAYS (today/yesterday). If found, fetches the
        filing text and runs ATM pattern detection.

        Args:
            cik: 10-digit company CIK.
            filings: Recent filings list from fetch_recent_filings().
            reference_date: Override for testing (default: today).

        Returns:
            (dilution_found, atm_found, matching_filing_dicts)
        """
        ref = reference_date or date.today()
        dilution_found = False
        atm_found = False
        matching: list[dict] = []

        for f in filings:
            form = f.get("form", "")
            filed = f.get("filingDate")
            if not filed or form not in DILUTION_FORMS:
                continue

            age_days = (ref - filed).days

            # Prospectus supplement: hard bear if today or yesterday
            if form in PROSPECTUS_FORMS and age_days <= DILUTION_LOOKBACK_DAYS:
                dilution_found = True
                matching.append(f)
                # Fetch filing text to check for ATM language
                atm_found = atm_found or await self._detect_atm(cik, f)

        return dilution_found, atm_found, matching

    async def _detect_atm(self, cik: str, filing: dict) -> bool:
        """
        Fetch the filing document and scan for at-the-market offering language.

        ATM offerings (Rule 415(a)(4)) allow continuous share sales at prevailing
        market prices. The company can sell into every rally indefinitely, making
        sustained upward moves nearly impossible. This is the hardest possible
        BEAR signal for momentum longs.
        """
        accession = filing.get("accessionNumber", "")
        if not accession:
            return False

        # Build the filing index URL: accession number without dashes
        acc_nodash = accession.replace("-", "")
        url = f"{self.SEC_FILING_BASE}/{int(cik)}/{acc_nodash}/{accession}-index.json"
        index_data = await self._fetch_json(url)
        if not index_data:
            # Try without leading zeros in CIK path
            return False

        # Find the primary document in the filing index
        doc_url = self._find_primary_document_url(cik, acc_nodash, index_data)
        if not doc_url:
            return False

        text = await self._fetch_text(doc_url)
        if not text:
            return False

        for pattern in ATM_PATTERNS:
            if pattern.search(text):
                logger.info(
                    "ATM language detected: pattern=%r accession=%s",
                    pattern.pattern[:40], accession
                )
                return True

        return False

    def _find_primary_document_url(
        self, cik: str, acc_nodash: str, index_data: dict
    ) -> Optional[str]:
        """Extract URL of the primary filing document from the index JSON."""
        documents = index_data.get("documents", [])
        primary_doc = None
        for doc in documents:
            doc_type = doc.get("type", "")
            if doc_type in ("424B5", "424B2", "S-3", "S-3/A"):
                primary_doc = doc.get("documentUrl") or doc.get("name", "")
                break
        if not primary_doc and documents:
            # Fall back to first document
            primary_doc = documents[0].get("documentUrl") or documents[0].get("name", "")

        if not primary_doc:
            return None

        # If it's already a full URL, return it
        if primary_doc.startswith("http"):
            return primary_doc

        # Otherwise build from components
        cik_int = int(cik)
        return f"{self.SEC_FILING_BASE}/{cik_int}/{acc_nodash}/{primary_doc}"

    # ── Material Event Detection ──────────────────────────────────────────────

    def check_material_events(
        self,
        filings: list[dict],
        reference_date: Optional[date] = None,
    ) -> tuple[bool, list[str]]:
        """
        Detect 8-K material event filings from today.

        An 8-K filed today suggests the gap-up has a real catalyst behind it.
        This is the signal the LLM news agents frequently miss when SEC metadata
        is not available to them.

        Returns (material_event_found, item_codes_list).
        Item codes are extracted from the description if present.
        """
        ref = reference_date or date.today()
        found = False
        items: list[str] = []

        for f in filings:
            form = f.get("form", "")
            filed = f.get("filingDate")
            if not filed or form not in MATERIAL_FORMS:
                continue

            age_days = (ref - filed).days
            if age_days <= MATERIAL_LOOKBACK_DAYS:
                found = True
                # Extract item codes from description (e.g. "Item 1.01, 8.01")
                desc = f.get("description", "") or ""
                extracted = re.findall(r"\d+\.\d+", desc)
                items.extend(extracted)

        return found, sorted(set(items))

    def check_shelf_registration(
        self,
        filings: list[dict],
        reference_date: Optional[date] = None,
    ) -> bool:
        """Detect S-3 shelf registration filed within SHELF_LOOKBACK_DAYS."""
        ref = reference_date or date.today()
        for f in filings:
            form = f.get("form", "")
            filed = f.get("filingDate")
            if not filed:
                continue
            if form in ("S-3", "S-3/A", "S-3ASR"):
                age_days = (ref - filed).days
                if 0 <= age_days <= SHELF_LOOKBACK_DAYS:
                    return True
        return False

    def check_earnings(
        self,
        filings: list[dict],
        reference_date: Optional[date] = None,
    ) -> bool:
        """Detect 10-K/10-Q filed within EARNINGS_LOOKBACK_DAYS."""
        ref = reference_date or date.today()
        for f in filings:
            form = f.get("form", "")
            filed = f.get("filingDate")
            if not filed or form not in EARNINGS_FORMS:
                continue
            age_days = (ref - filed).days
            if 0 <= age_days <= EARNINGS_LOOKBACK_DAYS:
                return True
        return False

    # ── Full Ticker Analysis ──────────────────────────────────────────────────

    async def analyze_ticker(
        self,
        ticker: str,
        reference_date: Optional[date] = None,
    ) -> SECFilingResult:
        """
        Full SEC analysis for one ticker.

        Execution order (fastest to slowest):
          1. Cache check → return immediately if already analysed
          2. CIK lookup (from pre-loaded bulk index, no extra HTTP request)
          3. Submissions API fetch (1 HTTP request → all recent filings)
          4. Pattern matching on form types and dates (no HTTP requests)
          5. ATM text fetch (1 HTTP request, only if 424B5 found today/yesterday)

        The happy path (no 424B5 recent) requires exactly 2 HTTP requests total.

        Args:
            ticker: Stock ticker symbol, case-insensitive.
            reference_date: Override for testing.

        Returns:
            SECFilingResult with signal, evidence, and diagnostics.
        """
        ticker_upper = ticker.upper()

        # Cache check
        if ticker_upper in self._results_cache:
            return self._results_cache[ticker_upper]

        t0 = time.monotonic()
        result = SECFilingResult(ticker=ticker_upper)

        try:
            # Step 1: CIK lookup
            cik = await self.lookup_cik(ticker_upper)
            if not cik:
                result.signal = FilingSignal.UNKNOWN
                result.error = f"CIK not found for {ticker_upper}"
                result.fetch_latency_ms = (time.monotonic() - t0) * 1000
                self._results_cache[ticker_upper] = result
                return result

            result.cik = cik

            # Step 2: Fetch recent filings
            filings = await self.fetch_recent_filings(cik)
            result.filings_found = [
                {"form": f["form"], "date": str(f["filingDate"]), "accession": f["accessionNumber"]}
                for f in filings
                if f.get("filingDate")
            ]

            # Step 3: Pattern matching — order matters (most severe first)
            ref = reference_date or date.today()

            dilution_found, atm_found, dilution_filings = await self.check_dilution(
                cik, filings, ref
            )
            material_found, item_codes = self.check_material_events(filings, ref)
            shelf_found = self.check_shelf_registration(filings, ref)
            earnings_found = self.check_earnings(filings, ref)

            result.dilution_detected = dilution_found
            result.atm_detected = atm_found
            result.material_event_detected = material_found
            result.eight_k_items = item_codes

            # Step 4: Assign primary signal (most severe wins)
            if atm_found:
                result.signal = FilingSignal.DILUTION_ATM
                result.confidence = 0.95
            elif dilution_found:
                result.signal = FilingSignal.DILUTION_ACTIVE
                result.confidence = 0.90
            elif shelf_found:
                result.signal = FilingSignal.SHELF_REGISTERED
                result.confidence = 0.70
            elif material_found:
                result.signal = FilingSignal.MATERIAL_EVENT
                result.confidence = 0.75
            elif earnings_found:
                result.signal = FilingSignal.EARNINGS_FILED
                result.confidence = 0.60
            else:
                result.signal = FilingSignal.CLEAN
                result.confidence = 0.80

        except Exception as exc:
            logger.exception("SEC prefetch failed for %s: %s", ticker_upper, exc)
            result.signal = FilingSignal.UNKNOWN
            result.error = str(exc)

        result.fetch_latency_ms = (time.monotonic() - t0) * 1000
        logger.info(
            "D191 SEC %s → %s (%.0fms) dilution=%s atm=%s event=%s",
            ticker_upper, result.signal.value, result.fetch_latency_ms,
            result.dilution_detected, result.atm_detected, result.material_event_detected,
        )

        self._results_cache[ticker_upper] = result
        return result

    # ── Batch Analysis ────────────────────────────────────────────────────────

    async def analyze_batch(
        self,
        tickers: list[str],
        reference_date: Optional[date] = None,
    ) -> dict[str, SECFilingResult]:
        """
        Analyse multiple tickers concurrently with rate limiting.

        Pre-loads the CIK index once, then fires all ticker analyses
        concurrently (bounded by the semaphore to ≤8 req/sec).

        Returns:
            Dict mapping ticker → SECFilingResult.
        """
        # Pre-load CIK index once for the whole batch
        await self._load_cik_index()

        tasks = [self.analyze_ticker(t, reference_date) for t in tickers]
        results_list = await asyncio.gather(*tasks, return_exceptions=True)

        output: dict[str, SECFilingResult] = {}
        for ticker, result in zip(tickers, results_list):
            ticker_upper = ticker.upper()
            if isinstance(result, Exception):
                output[ticker_upper] = SECFilingResult(
                    ticker=ticker_upper,
                    signal=FilingSignal.UNKNOWN,
                    error=str(result),
                )
            else:
                output[ticker_upper] = result

        return output

    # ── Cache Management ──────────────────────────────────────────────────────

    def clear_cache(self) -> None:
        """Clear result cache (call between premarket sessions)."""
        self._results_cache.clear()

    def cache_stats(self) -> dict:
        """Return cache hit statistics for diagnostics."""
        return {
            "cik_cache_size": len(self._cik_cache),
            "results_cache_size": len(self._results_cache),
        }

    # ── Health Check ──────────────────────────────────────────────────────────

    async def health_check(self) -> dict:
        """
        Verify the SEC EDGAR API is reachable and responsive.

        Fetches Apple's well-known CIK record as a canary. Returns a status dict
        suitable for logging, alerting, or a `/health` HTTP endpoint.
        """
        start = time.monotonic()
        try:
            # Apple Inc. CIK — stable, always present in EDGAR
            data = await self._fetch_json(
                f"{self.SEC_SUBMISSIONS_BASE}/CIK0000320193.json", timeout=10.0
            )
            latency_ms = (time.monotonic() - start) * 1000
            status = "healthy" if data else "degraded"
            return {
                "status": status,
                "latency_ms": round(latency_ms, 1),
                "cik_cache_size": len(self._cik_cache),
                "results_cache_size": len(self._results_cache),
            }
        except Exception as exc:
            return {
                "status": "unhealthy",
                "error": str(exc),
                "latency_ms": round((time.monotonic() - start) * 1000, 1),
                "cik_cache_size": len(self._cik_cache),
                "results_cache_size": len(self._results_cache),
            }

    # ── Cleanup ───────────────────────────────────────────────────────────────

    async def close(self) -> None:
        """Close the HTTP session. Call when done with premarket analysis."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
