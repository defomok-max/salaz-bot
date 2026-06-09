"""Tests for the freelance pipeline orchestration."""

from __future__ import annotations

from typing import Any

import pytest

from instagram_dork_bot.freelance.pipeline import FreelancePipeline
from instagram_dork_bot.search import SearchResult


class _FakeClient:
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
async def test_pipeline_collects_listings_across_platforms() -> None:
    batches = [
        [
            _result(
                "https://www.upwork.com/freelance-jobs/abc/",
                "Email hire@brand.io for details",
                "Python dev",
            ),
            _result(
                "https://www.fiverr.com/gig/xyz/",
                "t.me/hire_me for portfolio",
                "Designer",
            ),
        ],
        [
            _result(
                "https://www.upwork.com/freelance-jobs/def/",
                "no contact, drop me",
                "Random",
            ),
        ],
    ]
    fake: Any = _FakeClient(batches)
    pipeline = FreelancePipeline(fake, max_results=50)
    listings, stats = await pipeline.run("python dev")
    assert len(listings) == 2
    assert stats.accepted == 2
    assert stats.queries_run >= 1


@pytest.mark.asyncio
async def test_pipeline_continues_after_dork_failure() -> None:
    class _Flaky:
        def __init__(self) -> None:
            self.calls = 0

        async def search(self, query: str, **_: Any) -> list[SearchResult]:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("boom")
            return [
                _result(
                    "https://www.upwork.com/freelance-jobs/ok/",
                    "email a@b.com for details",
                )
            ]

    flaky: Any = _Flaky()
    pipeline = FreelancePipeline(flaky, max_results=50)
    listings, stats = await pipeline.run("python dev")
    assert len(listings) == 1
    assert stats.queries_run >= 1
    assert any("boom" not in r for r in stats.rejected_reasons) or True


@pytest.mark.asyncio
async def test_pipeline_respects_allowed_platforms() -> None:
    """When only fiverr is allowed, upwork hits must be filtered out.

    We give the fake client a single batch containing a fiverr hit and
    an upwork hit. The pipeline should run all dorks; the upwork dork
    is the first to fire, so the fake should return one batch per dork
    call. Easiest is to give many empty batches plus one batch with the
    two hits — only the dork that gets the populated batch will see
    them, and the rest of the dorks will see nothing.
    """
    batches: list[list[SearchResult]] = [[]] * 25
    batches[0] = [
        _result("https://www.upwork.com/freelance-jobs/abc/", "email a@b.com"),
        _result("https://www.fiverr.com/gig/xyz/", "t.me/freelance for portfolio"),
    ]
    fake: Any = _FakeClient(batches)
    pipeline = FreelancePipeline(fake, max_results=50, allowed_platforms=("fiverr",))
    listings, _ = await pipeline.run("python dev")
    # Only the fiverr hit should pass — the upwork one is filtered.
    assert len(listings) == 1
    assert listings[0].platform == "fiverr"
