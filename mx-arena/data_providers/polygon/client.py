"""Polygon REST HTTP client.

Responsibilities:
- httpx async wrapper with retry on 429/5xx
- file-based cache keyed on (endpoint, sorted-params-hash)
- pagination via `next_url` (Polygon's standard cursor)
- rate limit budget — Stocks Advanced exposes no headers, observed
  ~8 req/s sustained safely; we cap at 10 concurrent in flight to be
  polite (see RATE_LIMITS.md)

Auth: query-param `apiKey=...` (Polygon's only auth method on REST).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Optional
from urllib.parse import urlencode, urlparse, parse_qsl

import httpx

logger = logging.getLogger(__name__)

POLYGON_BASE_URL = "https://api.polygon.io"

# Cache TTLs (seconds; -1 = forever)
TTL_FOREVER = -1
TTL_ONE_DAY = 86_400
TTL_ZERO = 0  # always re-fetch

# Default cache TTL per endpoint pattern (longest match wins)
DEFAULT_TTL: dict[str, int] = {
    "/v2/aggs/ticker/": TTL_FOREVER,         # historical bars don't change
    "/v3/quotes/": TTL_FOREVER,              # historical NBBO doesn't change
    "/v3/trades/": TTL_FOREVER,              # historical trades don't change
    "/v3/reference/tickers": TTL_ONE_DAY,    # universe shifts slowly
    "/vX/reference/financials": TTL_FOREVER, # filed financials don't change
    "/v2/snapshot/": TTL_ZERO,               # snapshots always fresh
}


class PolygonAPIError(Exception):
    """Non-recoverable HTTP error from Polygon."""


class PolygonRateLimitError(PolygonAPIError):
    """HTTP 429 — caller should back off."""


@dataclass(frozen=True, slots=True)
class PolygonResponse:
    """Wrapper around a parsed Polygon JSON response.

    `next_url` is present for paginated endpoints; iterate via
    `PolygonClient.paginate()`.

    Note: some Polygon endpoints (e.g. /v1/marketstatus/upcoming) return a
    bare JSON array. We normalize: when the payload is a list, `data` is
    `{"results": <list>}` so `results`, `data.get(...)` etc all work.
    """
    data: dict
    next_url: Optional[str]
    cached: bool

    @property
    def results(self) -> list[dict]:
        """The `results` array, or empty list."""
        r = self.data.get("results")
        return list(r) if isinstance(r, list) else []


class PolygonClient:
    """Async REST client for Polygon.io.

    Usage:
        async with PolygonClient.from_env() as c:
            resp = await c.get("/v2/aggs/ticker/AAPL/range/1/day/2024-01-01/2024-01-02")
            for row in resp.results:
                ...
    """

    def __init__(
        self,
        api_key: str,
        cache_dir: Path | str = "data/polygon_cache",
        max_concurrent: int = 10,
        max_retries: int = 5,
        timeout_s: float = 30.0,
        base_url: str = POLYGON_BASE_URL,
    ) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        self._api_key = api_key
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._sem = asyncio.Semaphore(max_concurrent)
        self._max_retries = max_retries
        self._timeout_s = timeout_s
        self._base_url = base_url.rstrip("/")
        self._client: Optional[httpx.AsyncClient] = None

    @classmethod
    def from_env(cls, **kwargs: Any) -> "PolygonClient":
        """Build a client from POLYGON_API_KEY env var."""
        key = os.environ.get("POLYGON_API_KEY", "").strip()
        if not key:
            raise RuntimeError(
                "POLYGON_API_KEY not set in environment. "
                "Set it via .env or export."
            )
        return cls(api_key=key, **kwargs)

    async def __aenter__(self) -> "PolygonClient":
        self._client = httpx.AsyncClient(timeout=self._timeout_s)
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ── Cache layer ──────────────────────────────────────────────

    def _cache_key(self, endpoint: str, params: dict) -> Path:
        # Strip apiKey from cache key (security + key-rotation tolerance).
        params_no_key = {k: v for k, v in params.items() if k != "apiKey"}
        canonical = json.dumps(params_no_key, sort_keys=True, separators=(",", ":"))
        h = hashlib.sha256(f"{endpoint}|{canonical}".encode()).hexdigest()[:16]
        # Mirror endpoint hierarchy in directory tree for human inspection.
        ep_clean = endpoint.strip("/").replace("/", "_")[:80]
        return self._cache_dir / ep_clean / f"{h}.json"

    def _ttl_for(self, endpoint: str) -> int:
        # Longest-prefix match.
        best = TTL_ZERO
        best_len = -1
        for pat, ttl in DEFAULT_TTL.items():
            if endpoint.startswith(pat) and len(pat) > best_len:
                best = ttl
                best_len = len(pat)
        return best

    def _read_cache(self, key: Path, ttl: int) -> Optional[dict]:
        if not key.exists():
            return None
        if ttl == TTL_ZERO:
            return None
        if ttl != TTL_FOREVER:
            age = time.time() - key.stat().st_mtime
            if age > ttl:
                return None
        try:
            return json.loads(key.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def _write_cache(self, key: Path, payload: dict) -> None:
        key.parent.mkdir(parents=True, exist_ok=True)
        tmp = key.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, key)
        except OSError as e:
            logger.warning("polygon cache write failed for %s: %s", key, e)
            tmp.unlink(missing_ok=True)

    # ── HTTP layer ────────────────────────────────────────────────

    async def get(
        self,
        endpoint: str,
        params: Optional[dict] = None,
        *,
        bypass_cache: bool = False,
    ) -> PolygonResponse:
        """GET /endpoint with optional query params.

        endpoint: path including leading slash, e.g. "/v2/aggs/ticker/..."
        params:   query params (apiKey added automatically)
        """
        params = dict(params or {})
        # Cache lookup BEFORE adding apiKey so the key is rotation-safe.
        cache_key = self._cache_key(endpoint, params)
        ttl = self._ttl_for(endpoint)
        if not bypass_cache:
            cached = self._read_cache(cache_key, ttl)
            if cached is not None:
                return PolygonResponse(
                    data=cached, next_url=cached.get("next_url"), cached=True,
                )

        params["apiKey"] = self._api_key
        url = f"{self._base_url}{endpoint}"
        payload = await self._request_with_retry(url, params)

        # Normalize bare-array responses (e.g. /v1/marketstatus/upcoming)
        if isinstance(payload, list):
            payload = {"results": payload}

        if ttl != TTL_ZERO and not bypass_cache:
            # Strip apiKey from cached payload too (defense in depth — Polygon
            # never echoes it but be safe).
            cache_payload = {k: v for k, v in payload.items() if k != "apiKey"}
            self._write_cache(cache_key, cache_payload)

        return PolygonResponse(
            data=payload, next_url=payload.get("next_url"), cached=False,
        )

    async def get_url(self, full_url: str, *, bypass_cache: bool = False) -> PolygonResponse:
        """GET an absolute URL (used for `next_url` pagination cursor).

        Polygon's `next_url` already includes the apiKey in some plans but
        NOT in others — we re-add it defensively.
        """
        parsed = urlparse(full_url)
        endpoint = parsed.path
        params = dict(parse_qsl(parsed.query))
        return await self.get(endpoint, params, bypass_cache=bypass_cache)

    async def paginate(
        self,
        endpoint: str,
        params: Optional[dict] = None,
        *,
        max_pages: int = 1000,
    ) -> AsyncIterator[PolygonResponse]:
        """Iterate all pages via `next_url` cursor.

        Stops at max_pages as a safety cap (Polygon should always terminate,
        but defensive).
        """
        page = 0
        resp = await self.get(endpoint, params)
        yield resp
        page += 1
        while resp.next_url and page < max_pages:
            resp = await self.get_url(resp.next_url)
            yield resp
            page += 1
        if page >= max_pages:
            logger.warning(
                "polygon paginate hit max_pages=%d for %s — possible runaway",
                max_pages, endpoint,
            )

    async def _request_with_retry(self, url: str, params: dict) -> dict:
        """HTTP GET with exponential-backoff retry on 429/5xx.

        Polygon Stocks Advanced has no documented hard cap, but we still
        retry defensively.
        """
        if self._client is None:
            raise RuntimeError("PolygonClient must be used as async context manager")

        async with self._sem:
            for attempt in range(self._max_retries):
                try:
                    r = await self._client.get(url, params=params)
                except (httpx.TimeoutException, httpx.NetworkError) as e:
                    if attempt + 1 == self._max_retries:
                        raise PolygonAPIError(f"network error after {attempt+1} tries: {e}") from e
                    delay = 2 ** attempt
                    logger.warning("polygon network err: %s — retry in %ds", e, delay)
                    await asyncio.sleep(delay)
                    continue

                if r.status_code == 200:
                    try:
                        return r.json()
                    except json.JSONDecodeError as e:
                        raise PolygonAPIError(
                            f"non-JSON 200 from {url}: {r.text[:200]}"
                        ) from e

                if r.status_code == 429:
                    if attempt + 1 == self._max_retries:
                        raise PolygonRateLimitError(
                            f"429 after {self._max_retries} retries: {r.text[:200]}"
                        )
                    delay = 2 ** (attempt + 1)
                    logger.warning(
                        "polygon 429 — retry in %ds (attempt %d/%d)",
                        delay, attempt + 1, self._max_retries,
                    )
                    await asyncio.sleep(delay)
                    continue

                if 500 <= r.status_code < 600:
                    if attempt + 1 == self._max_retries:
                        raise PolygonAPIError(
                            f"5xx after {self._max_retries} retries: "
                            f"{r.status_code} {r.text[:200]}"
                        )
                    delay = 2 ** attempt
                    logger.warning(
                        "polygon %d — retry in %ds (attempt %d/%d)",
                        r.status_code, delay, attempt + 1, self._max_retries,
                    )
                    await asyncio.sleep(delay)
                    continue

                # 4xx other than 429 — don't retry, raise.
                raise PolygonAPIError(
                    f"{r.status_code} from {url}: {r.text[:200]}"
                )

            # Should be unreachable.
            raise PolygonAPIError(f"exhausted retries for {url}")
