"""Google search backend (Serper.dev) with multi-key rotation."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

SERPER_ENDPOINT = "https://google.serper.dev/search"

# HTTP statuses that imply "this key is exhausted / banned, try the next one".
# 401 = key invalid / revoked
# 402 = payment required (out of credits)
# 403 = forbidden (key disabled)
# 429 = rate-limit / quota
_KEY_EXHAUSTED_STATUSES = frozenset({401, 402, 403, 429})


@dataclass(frozen=True)
class SearchResult:
    """A single organic Google search hit."""

    title: str
    link: str
    snippet: str
    raw: dict[str, Any]


class SearchError(RuntimeError):
    """Raised when the search backend fails (after all keys are exhausted)."""


class _KeyExhaustedError(Exception):
    """Internal signal: rotate to the next API key."""

    def __init__(self, status_code: int, body: str) -> None:
        super().__init__(f"Serper key exhausted (HTTP {status_code}): {body[:200]!r}")
        self.status_code = status_code
        self.body = body


class SerperClient:
    """Thin async client around Serper.dev with automatic API-key rotation.

    Pass either a single ``api_key`` or a list via ``api_keys``. When a request
    fails with a status that signals the key is dead (401/402/403/429), the
    client moves to the next key and retries. ``SearchError`` is only raised
    once *every* key has been exhausted for the current request.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        api_keys: Iterable[str] | None = None,
        timeout: float = 20.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        keys: list[str] = []
        seen: set[str] = set()
        for k in list(api_keys or []) + ([api_key] if api_key else []):
            if not k:
                continue
            stripped = k.strip()
            if stripped and stripped not in seen:
                seen.add(stripped)
                keys.append(stripped)
        if not keys:
            raise ValueError("at least one Serper API key must be provided")

        self._keys: list[str] = keys
        self._exhausted: set[str] = set()
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None

    @property
    def keys(self) -> Sequence[str]:
        return tuple(self._keys)

    @property
    def active_keys(self) -> tuple[str, ...]:
        """Keys that have not yet been marked exhausted."""
        return tuple(k for k in self._keys if k not in self._exhausted)

    async def __aenter__(self) -> SerperClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
            self._owns_client = True
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def search(
        self,
        query: str,
        *,
        num: int = 20,
        gl: str = "us",
        hl: str = "en",
    ) -> list[SearchResult]:
        """Execute a single Google search via Serper.dev, rotating keys as needed."""
        if self._client is None:
            raise RuntimeError("SerperClient must be used as an async context manager")

        candidates = [k for k in self._keys if k not in self._exhausted]
        if not candidates:
            raise SearchError(
                "All Serper API keys are exhausted. "
                "Add fresh keys to SERPER_API_KEYS and restart the bot."
            )

        payload = {"q": query, "num": min(max(num, 1), 100), "gl": gl, "hl": hl}
        last_error: _KeyExhaustedError | None = None

        for key in candidates:
            try:
                return await self._search_once(key, payload)
            except _KeyExhaustedError as exc:
                # Log only length + last 4 chars (no prefix) to avoid leaking
                # the secret into a logfile on persistent deployments.
                logger.warning(
                    "serper key_len=%d key_suffix=%s exhausted (HTTP %s); rotating",
                    len(key),
                    key[-4:] if len(key) >= 4 else "****",
                    exc.status_code,
                )
                self._exhausted.add(key)
                last_error = exc
                continue

        msg = "All Serper API keys are exhausted"
        if last_error is not None:
            msg += f" (last status: HTTP {last_error.status_code})"
        raise SearchError(msg + ". Add fresh keys to SERPER_API_KEYS and restart the bot.")

    async def _search_once(self, api_key: str, payload: dict[str, Any]) -> list[SearchResult]:
        assert self._client is not None  # narrowed by caller
        headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}

        # The Serper API key is sensitive — log only its length and a
        # short fingerprint suffix to aid debugging without leaking the
        # secret into a logfile.
        logger.debug(
            "serper request key_len=%d key_suffix=%s payload=%s",
            len(api_key),
            api_key[-4:] if len(api_key) >= 4 else "****",
            payload,
        )
        try:
            resp = await self._client.post(
                SERPER_ENDPOINT, headers=headers, json=payload, timeout=self._timeout
            )
        except httpx.HTTPError as exc:
            # Network errors are not key-specific - raise immediately.
            raise SearchError(f"network error contacting Serper: {exc}") from exc

        if resp.status_code in _KEY_EXHAUSTED_STATUSES:
            raise _KeyExhaustedError(resp.status_code, resp.text)
        if resp.status_code >= 400:
            raise SearchError(f"Serper returned HTTP {resp.status_code}: {resp.text[:200]!r}")

        data = resp.json()
        organic = data.get("organic") or []
        results: list[SearchResult] = []
        for item in organic:
            link = item.get("link") or ""
            if not link:
                continue
            results.append(
                SearchResult(
                    title=str(item.get("title") or ""),
                    link=str(link),
                    snippet=str(item.get("snippet") or ""),
                    raw=item,
                )
            )
        return results
