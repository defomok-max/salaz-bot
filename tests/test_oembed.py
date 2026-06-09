"""Tests for the oEmbed backstop client."""

from __future__ import annotations

import httpx
import pytest

from instagram_dork_bot.oembed import InstagramOEmbedClient


def _client_with(handler):  # type: ignore[no-untyped-def]
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport)


@pytest.mark.asyncio
async def test_oembed_returns_title_and_author() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "title": "Selling iPhone 14 Pro, DM for price",
                "author_name": "shop_berlin",
                "author_url": "https://www.instagram.com/shop_berlin",
            },
        )

    transport = httpx.MockTransport(handler)
    async with (
        httpx.AsyncClient(transport=transport) as http_client,
        InstagramOEmbedClient(client=http_client) as client,
    ):
        result = await client.fetch("https://www.instagram.com/p/abc/")
    assert result is not None
    assert "iPhone 14" in result.title
    assert result.author_name == "shop_berlin"


@pytest.mark.asyncio
async def test_oembed_returns_none_for_404() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Not Found")

    async with (
        _client_with(handler) as http_client,
        InstagramOEmbedClient(client=http_client) as client,
    ):
        assert await client.fetch("https://www.instagram.com/p/gone/") is None


@pytest.mark.asyncio
async def test_oembed_returns_none_for_500() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="oops")

    async with (
        _client_with(handler) as http_client,
        InstagramOEmbedClient(client=http_client) as client,
    ):
        assert await client.fetch("https://www.instagram.com/p/x/") is None


@pytest.mark.asyncio
async def test_oembed_handles_network_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network down")

    async with (
        _client_with(handler) as http_client,
        InstagramOEmbedClient(client=http_client) as client,
    ):
        assert await client.fetch("https://www.instagram.com/p/x/") is None
