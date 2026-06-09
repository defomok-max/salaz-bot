"""Tests for the SerperClient and its multi-key rotation behaviour."""

from __future__ import annotations

import httpx
import pytest

from instagram_dork_bot.search import SearchError, SerperClient


def _make_handler(scripted: list[httpx.Response]):
    """Return an httpx.MockTransport handler that returns scripted responses in order.

    The handler also records the request headers so we can inspect which API key
    was used for each call.
    """
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.headers))
        if not scripted:
            return httpx.Response(500, json={})
        return scripted.pop(0)

    return handler, calls


@pytest.mark.asyncio
async def test_init_rejects_empty_keys() -> None:
    with pytest.raises(ValueError):
        SerperClient(api_keys=[])


@pytest.mark.asyncio
async def test_single_key_happy_path() -> None:
    handler, calls = _make_handler(
        [
            httpx.Response(
                200,
                json={
                    "organic": [
                        {
                            "title": "Brand",
                            "link": "https://www.instagram.com/p/abc/",
                            "snippet": "shop@brand.io wa.me/15551234567",
                        }
                    ]
                },
            )
        ]
    )
    transport = httpx.MockTransport(handler)
    async with (
        httpx.AsyncClient(transport=transport) as http_client,
        SerperClient(api_key="key-a", client=http_client) as serper,
    ):
        results = await serper.search("iphone")
    assert len(results) == 1
    assert calls[0]["x-api-key"] == "key-a"


@pytest.mark.asyncio
async def test_rotation_on_quota_exhausted() -> None:
    """402 / 429 on key A must transparently retry with key B."""
    handler, calls = _make_handler(
        [
            httpx.Response(402, text="Insufficient credits"),
            httpx.Response(
                200,
                json={
                    "organic": [
                        {
                            "title": "Brand",
                            "link": "https://www.instagram.com/p/abc/",
                            "snippet": "shop@brand.io wa.me/15551234567",
                        }
                    ]
                },
            ),
        ]
    )
    transport = httpx.MockTransport(handler)
    async with (
        httpx.AsyncClient(transport=transport) as http_client,
        SerperClient(api_keys=["dead-key", "good-key"], client=http_client) as serper,
    ):
        results = await serper.search("iphone")
        assert serper.active_keys == ("good-key",)
    assert [c["x-api-key"] for c in calls] == ["dead-key", "good-key"]
    assert len(results) == 1


@pytest.mark.asyncio
async def test_dead_key_marked_exhausted_for_subsequent_calls() -> None:
    """Once a key is dead, the client must not try it again on later searches."""
    handler, calls = _make_handler(
        [
            httpx.Response(401, text="Unauthorized"),  # call 1, key A: dies
            httpx.Response(200, json={"organic": []}),  # call 1, key B: ok
            httpx.Response(200, json={"organic": []}),  # call 2: should go straight to B
        ]
    )
    transport = httpx.MockTransport(handler)
    async with (
        httpx.AsyncClient(transport=transport) as http_client,
        SerperClient(api_keys=["bad-key", "good-key"], client=http_client) as serper,
    ):
        await serper.search("a")
        await serper.search("b")
    sent_keys = [c["x-api-key"] for c in calls]
    assert sent_keys == ["bad-key", "good-key", "good-key"]


@pytest.mark.asyncio
async def test_all_keys_exhausted_raises() -> None:
    handler, _ = _make_handler(
        [
            httpx.Response(429, text="Rate limited"),
            httpx.Response(429, text="Rate limited"),
        ]
    )
    transport = httpx.MockTransport(handler)
    async with (
        httpx.AsyncClient(transport=transport) as http_client,
        SerperClient(api_keys=["k1", "k2"], client=http_client) as serper,
    ):
        with pytest.raises(SearchError, match="exhausted"):
            await serper.search("anything")


@pytest.mark.asyncio
async def test_non_key_error_does_not_burn_key() -> None:
    """A 500 from Serper is not a key issue - keys must remain usable."""
    handler, _ = _make_handler([httpx.Response(500, text="oops")])
    transport = httpx.MockTransport(handler)
    async with (
        httpx.AsyncClient(transport=transport) as http_client,
        SerperClient(api_keys=["k1"], client=http_client) as serper,
    ):
        with pytest.raises(SearchError, match="HTTP 500"):
            await serper.search("anything")
        assert serper.active_keys == ("k1",)


@pytest.mark.asyncio
async def test_duplicate_keys_are_deduplicated() -> None:
    handler, _ = _make_handler([httpx.Response(200, json={"organic": []})])
    transport = httpx.MockTransport(handler)
    async with (
        httpx.AsyncClient(transport=transport) as http_client,
        SerperClient(api_keys=["k1", "k1", "  k1  "], client=http_client) as serper,
    ):
        assert serper.keys == ("k1",)
        await serper.search("ok")
