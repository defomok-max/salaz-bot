"""Async oEmbed client used as a relevance backstop.

When a Google snippet is short or doesn't contain the user's keyword,
the bot still has a way to confirm the linked post is about the
keyword: fetch the Instagram oEmbed JSON. The response includes a
``title`` field (the first ~200 chars of the caption) and an
``author_name``. If the keyword is in the title, the hit is real; if
not, we drop it.

Why oEmbed (and not scraping the post HTML)
-------------------------------------------
- No auth, no API key, no rate limit beyond the usual HTTP one.
- Tiny JSON payload (~1 KB) so the cost is negligible.
- Works for public posts only — which is exactly the population we
  can index via Google anyway.
- Doesn't trigger Instagram's aggressive scraping defences.

Limitations
-----------
- Only Instagram URLs that look like ``/p/{shortcode}`` or
  ``/reel/{shortcode}`` resolve. Profile URLs (``/user/...``) do not.
- Returns HTTP 404 for deleted/private posts; we treat that as
  "indeterminate" (caller decides what to do with no data).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

OEMBED_ENDPOINT = "https://api.instagram.com/oembed/"


@dataclass(frozen=True)
class OEmbedResult:
    """Outcome of an oEmbed lookup."""

    title: str
    author_name: str
    raw: dict[str, object]


class OEmbedError(RuntimeError):
    """Raised when the oEmbed request fails for non-recoverable reasons."""


class InstagramOEmbedClient:
    """Tiny async wrapper around the Instagram oEmbed endpoint.

    The client is cheap to instantiate per search — it shares an
    underlying ``httpx.AsyncClient`` and enforces a per-call timeout.
    Use as an async context manager.
    """

    def __init__(
        self,
        *,
        timeout: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> InstagramOEmbedClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
            self._owns_client = True
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch(self, url: str) -> OEmbedResult | None:
        """Return oEmbed data for ``url`` or ``None`` if the post is gone.

        Network errors are logged and returned as ``None`` so the caller
        can treat the snippet as the only signal.
        """
        if self._client is None:
            raise RuntimeError("InstagramOEmbedClient must be used as an async context manager")
        try:
            resp = await self._client.get(
                OEMBED_ENDPOINT,
                params={"url": url},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            logger.debug("oEmbed network error for %s: %s", url, exc)
            return None

        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            logger.debug("oEmbed HTTP %s for %s: %s", resp.status_code, url, resp.text[:200])
            return None

        try:
            data = resp.json()
        except ValueError:
            return None

        title = str(data.get("title") or "").strip()
        author = str(data.get("author_name") or "").strip()
        if not title:
            return None
        raw_obj = data if isinstance(data, dict) else {}
        return OEmbedResult(
            title=title,
            author_name=author,
            raw=raw_obj,
        )


async def gather_oembed_titles(
    urls: list[str],
    *,
    client_factory: Callable[[], Awaitable[InstagramOEmbedClient]],
    concurrency: int = 8,
) -> dict[str, OEmbedResult]:
    """Fetch oEmbed titles for many URLs in parallel with a concurrency cap.

    Returns a mapping ``url -> OEmbedResult`` for every URL that
    resolved; missing URLs are simply absent from the dict.
    """
    if not urls:
        return {}

    sem = asyncio.Semaphore(max(1, concurrency))
    client = await client_factory()
    results: dict[str, OEmbedResult] = {}

    async def _one(url: str) -> None:
        async with sem:
            data = await client.fetch(url)
        if data is not None:
            results[url] = data

    try:
        await asyncio.gather(*(_one(u) for u in urls))
    finally:
        await client.__aexit__(None, None, None)
    return results
