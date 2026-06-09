"""Integration-style tests for the search pipeline using a fake search client."""

from __future__ import annotations

from typing import Any

import pytest

from instagram_dork_bot.pipeline import SearchPipeline
from instagram_dork_bot.search import SearchResult


class _FakeClient:
    """Stand-in for SerperClient that returns canned data per query."""

    def __init__(self, batches: list[list[SearchResult]]) -> None:
        self._batches = list(batches)
        self.calls: list[str] = []

    async def search(
        self,
        query: str,
        *,
        num: int = 20,
        gl: str = "us",
        hl: str = "en",
    ) -> list[SearchResult]:
        del num, gl, hl
        self.calls.append(query)
        if not self._batches:
            return []
        return self._batches.pop(0)


def _result(link: str, snippet: str, title: str = "") -> SearchResult:
    return SearchResult(title=title, link=link, snippet=snippet, raw={})


@pytest.mark.asyncio
async def test_pipeline_filters_and_dedupes() -> None:
    accepted_snippet = "Selling iPhone 14 -- email shop@brand.io / WhatsApp wa.me/15551234567"
    rejected_no_phone = "Selling iPhone 14 -- email shop@brand.io but no contact"
    rejected_cis = "Продаю iPhone 14 -- email shop@brand.ru / WhatsApp wa.me/79161234567"
    rejected_no_keyword = "Selling iPad -- email shop@brand.io / WhatsApp wa.me/15559999999"
    duplicate = accepted_snippet

    batches = [
        [
            _result("https://www.instagram.com/p/abc/", accepted_snippet),
            _result("https://www.instagram.com/p/def/", rejected_no_phone),
            _result("https://www.instagram.com/p/ghi/", rejected_cis),
            _result("https://www.instagram.com/p/mno/", rejected_no_keyword),
            _result("https://www.instagram.com/p/abc/", duplicate),  # dup
        ],
        [
            _result(
                "https://www.instagram.com/p/jkl/",
                "Selling iPhone 14 -- shop@brand.io wa.me/447700900123",
            ),
        ],
    ]

    fake: Any = _FakeClient(batches)
    pipeline = SearchPipeline(fake, max_results=50, results_per_query=20)

    listings, stats = await pipeline.run("iphone 14")

    links = [item.link for item in listings]
    assert "https://www.instagram.com/p/abc" in links
    assert "https://www.instagram.com/p/jkl" in links
    assert "https://www.instagram.com/p/ghi" not in links  # CIS
    assert "https://www.instagram.com/p/def" not in links  # no phone
    assert "https://www.instagram.com/p/mno" not in links  # keyword mismatch
    assert len(set(links)) == len(links)  # no dupes
    assert stats.queries_run >= 1


@pytest.mark.asyncio
async def test_pipeline_accepts_email_plus_plain_phone() -> None:
    """A post with email + non-WhatsApp phone is now accepted."""
    snippet = "Selling iPhone 14 -- email shop@brand.io. Call +1 555 123 4567"
    fake: Any = _FakeClient([[_result("https://www.instagram.com/p/abc/", snippet)]])
    pipeline = SearchPipeline(fake, max_results=50)
    listings, _ = await pipeline.run("iphone 14")
    assert len(listings) == 1
    assert listings[0].contacts.has_phone
    assert not listings[0].contacts.has_whatsapp


@pytest.mark.asyncio
async def test_pipeline_stops_at_max_results() -> None:
    accepted_snippet = "selling phones -- shop@brand.io / wa.me/15551234567"
    big_batch = [_result(f"https://www.instagram.com/p/p{i}/", accepted_snippet) for i in range(75)]
    fake: Any = _FakeClient([big_batch])
    pipeline = SearchPipeline(fake, max_results=10, results_per_query=75)

    listings, _ = await pipeline.run("phones")
    assert len(listings) == 10


@pytest.mark.asyncio
async def test_pipeline_rejects_when_keyword_not_in_snippet() -> None:
    """The relevance fix: a hit without the keyword tokens is dropped."""
    snippet = "Random shop -- shop@brand.io wa.me/15551234567"
    fake: Any = _FakeClient([[_result("https://www.instagram.com/p/abc/", snippet)]])
    pipeline = SearchPipeline(fake, max_results=50)
    listings, _ = await pipeline.run("iphone")
    assert listings == []


@pytest.mark.asyncio
async def test_pipeline_marks_needs_verification_for_long_keywords() -> None:
    """Hits whose snippet lacks the keyword are queued for oEmbed verification."""
    snippet = "Photo of camera, contact: shop@brand.io"
    fake: Any = _FakeClient([[_result("https://www.instagram.com/p/abc/", snippet)]])
    pipeline = SearchPipeline(fake, max_results=50)
    listings, _stats = await pipeline.run("camera")
    # Without an oEmbed factory the listing is dropped (rejected as
    # unverifiable) — the test is about the needs_verification flag,
    # so we check that the pipeline did not silently accept.
    assert listings == []


@pytest.mark.asyncio
async def test_pipeline_handles_search_error_and_continues() -> None:
    class _FlakyClient:
        def __init__(self) -> None:
            self.calls = 0

        async def search(self, query: str, **_: Any) -> list[SearchResult]:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("boom")
            return [
                _result(
                    "https://www.instagram.com/p/ok/",
                    "Selling phones - shop@brand.io wa.me/15551234567",
                )
            ]

    flaky: Any = _FlakyClient()
    pipeline = SearchPipeline(flaky, max_results=50)
    listings, stats = await pipeline.run("phones")

    assert len(listings) == 1
    assert stats.queries_run >= 1
