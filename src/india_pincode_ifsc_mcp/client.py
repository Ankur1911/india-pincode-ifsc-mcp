"""Small async client for two free public APIs: India Post PIN data (postalpincode.in) and Razorpay IFSC."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any
from urllib.parse import quote

import httpx

log = logging.getLogger("india_pincode_ifsc_mcp")

PINCODE_BASE_URL = "https://api.postalpincode.in"
IFSC_BASE_URL = "https://ifsc.razorpay.com"
RETRY_STATUSES = {429, 502, 503, 504}


class ApiError(Exception):
    """An API or network failure with a message that is safe to show a model."""


def enc(value: str) -> str:
    """Percent-encode one path segment."""
    return quote(str(value), safe="")


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


class ApiClient:
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._sem: asyncio.Semaphore | None = None

    @property
    def pincode_base(self) -> str:
        return os.environ.get("PINCODE_BASE_URL", PINCODE_BASE_URL).rstrip("/")

    @property
    def ifsc_base(self) -> str:
        return os.environ.get("IFSC_BASE_URL", IFSC_BASE_URL).rstrip("/")

    def _http(self) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        if self._client is None or self._client.is_closed or self._loop is not loop:
            self._loop = loop
            self._sem = asyncio.Semaphore(int(_float_env("INDIA_LOOKUP_CONCURRENCY", 5)))
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(_float_env("INDIA_LOOKUP_TIMEOUT", 20.0), connect=10.0),
                headers={"Accept": "application/json", "User-Agent": "india-pincode-ifsc-mcp/0.1"},
                follow_redirects=True,
            )
        return self._client

    async def get(self, url: str, params: Any = None, *, source: str) -> Any:
        """GET a URL and return decoded JSON, or None on HTTP 404. `source` names the upstream in error messages."""
        retries = int(_float_env("INDIA_LOOKUP_RETRIES", 2))
        delay = _float_env("INDIA_LOOKUP_RETRY_DELAY", 1.0)
        http = self._http()
        resp: httpx.Response | None = None
        assert self._sem is not None
        async with self._sem:
            for attempt in range(retries + 1):
                try:
                    resp = await http.get(url, params=params)
                except httpx.TimeoutException as exc:
                    if attempt < retries:
                        await asyncio.sleep(delay * 2**attempt)
                        continue
                    raise ApiError(f"{source} timed out ({type(exc).__name__}). The free public service may be slow; retry later.") from exc
                except httpx.HTTPError as exc:
                    raise ApiError(f"{source} request failed: {type(exc).__name__}: {exc}") from exc
                if resp.status_code in RETRY_STATUSES and attempt < retries:
                    wait = delay * 2**attempt
                    retry_after = resp.headers.get("retry-after")
                    if retry_after and retry_after.isdigit():
                        wait = min(float(retry_after), 10.0)
                    await asyncio.sleep(wait)
                    continue
                break
        assert resp is not None
        if resp.status_code == 404:
            return None
        if resp.status_code == 429:
            raise ApiError(f"{source} rate limited the request. Wait a moment and retry with fewer lookups.")
        if resp.status_code >= 400:
            raise ApiError(f"{source} returned HTTP {resp.status_code}. {resp.text[:200].strip()}".strip())
        try:
            return resp.json()
        except ValueError as exc:
            raise ApiError(f"{source} returned a non-JSON response.") from exc

    async def pincode(self, path: str) -> Any:
        return await self.get(self.pincode_base + path, source="India Post PIN service")

    async def ifsc(self, path: str, params: Any = None) -> Any:
        return await self.get(self.ifsc_base + path, params, source="IFSC service")
