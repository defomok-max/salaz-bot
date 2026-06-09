"""Pipeline that orchestrates dork building, search, extraction and filtering.

Compared to the previous version, the pipeline now:

* feeds the keyword into the relevance filter so a "iphone" search cannot
  return a tractor-rental post,
* optionally fetches the Instagram oEmbed title for every hit that
  passed the cheap gate and uses it to *re-verify* hits whose snippet
  didn't contain the keyword tokens (with a small concurrency cap so we
  don't hammer Instagram with parallel calls),
* uses the caller's selected countries for the country-biased dork
  instead of a hard-coded default list.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .dorks import Dork, build_dorks, country_keyword_terms
from .extractors import Contacts, extract_contacts
from .filters import FilterDecision, evaluate_result, recheck_with_title
from .oembed import InstagramOEmbedClient, OEmbedResult
from .search import SearchResult, SerperClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Listing:
    """A search result that passed all filters."""

    title: str
    link: str
    snippet: str
    contacts: Contacts


@dataclass
class PipelineStats:
    queries_run: int = 0
    raw_results: int = 0
    accepted: int = 0
    rejected_reasons: dict[str, int] = field(default_factory=dict)
    oembed_verified: int = 0
    oembed_fetched: int = 0
    oembed_failed: int = 0


@dataclass(frozen=True)
class ProgressEvent:
    """Structured progress signal emitted during pipeline execution."""

    stage: str  # "init" | "dork_start" | "dork_done" | "dork_failed" | "completed"
    queries_done: int = 0
    queries_total: int = 0
    accepted: int = 0
    max_results: int = 0
    label: str = ""
    detail: str = ""


ProgressCallback = Callable[[ProgressEvent], Awaitable[None]]


async def _noop_progress(_event: ProgressEvent) -> None:
    return None


OEmbedFactory = Callable[[], Awaitable[InstagramOEmbedClient]]


class SearchPipeline:
    """Run a series of dork queries and collect filtered Instagram listings."""

    def __init__(
        self,
        client: SerperClient,
        *,
        max_results: int = 50,
        results_per_query: int = 20,
        max_search_calls: int = 12,
        allowed_countries: tuple[str, ...] | None = None,
        oembed_factory: OEmbedFactory | None = None,
        oembed_concurrency: int = 8,
    ) -> None:
        self._client = client
        self._max_results = max_results
        self._results_per_query = results_per_query
        self._max_search_calls = max_search_calls
        self._allowed_countries = (
            tuple(c.upper() for c in allowed_countries) if allowed_countries is not None else None
        )
        self._oembed_factory = oembed_factory
        self._oembed_concurrency = max(1, oembed_concurrency)

    async def run(
        self,
        keyword: str,
        *,
        progress: ProgressCallback | None = None,
    ) -> tuple[list[Listing], PipelineStats]:
        progress = progress or _noop_progress
        stats = PipelineStats()
        seen_links: set[str] = set()
        accepted: list[Listing] = []
        pending_verification: list[tuple[Listing, FilterDecision]] = []

        countries_for_dork = country_keyword_terms(self._allowed_countries)
        dorks = build_dorks(keyword, countries=countries_for_dork)
        queries_total = min(len(dorks), self._max_search_calls)
        await progress(
            ProgressEvent(
                stage="init",
                queries_done=0,
                queries_total=queries_total,
                accepted=0,
                max_results=self._max_results,
            )
        )

        for dork in dorks:
            if len(accepted) >= self._max_results:
                break
            if stats.queries_run >= self._max_search_calls:
                break

            await progress(
                ProgressEvent(
                    stage="dork_start",
                    queries_done=stats.queries_run,
                    queries_total=queries_total,
                    accepted=len(accepted),
                    max_results=self._max_results,
                    label=dork.label,
                )
            )
            try:
                results = await self._client.search(dork.query, num=self._results_per_query)
            except Exception as exc:
                logger.exception("dork %s failed", dork.label)
                await progress(
                    ProgressEvent(
                        stage="dork_failed",
                        queries_done=stats.queries_run,
                        queries_total=queries_total,
                        accepted=len(accepted),
                        max_results=self._max_results,
                        label=dork.label,
                        detail=str(exc),
                    )
                )
                continue

            stats.queries_run += 1
            stats.raw_results += len(results)

            new_in_this_query = self._consume_results(
                results,
                keyword,
                seen_links,
                accepted,
                pending_verification,
                stats,
            )
            await progress(
                ProgressEvent(
                    stage="dork_done",
                    queries_done=stats.queries_run,
                    queries_total=queries_total,
                    accepted=len(accepted),
                    max_results=self._max_results,
                    label=dork.label,
                    detail=f"{len(results)} hits, {new_in_this_query} accepted",
                )
            )

            if len(accepted) >= self._max_results:
                break

        # Re-verify the hits that need an oEmbed check (snippet was bad but
        # we kept them in case the oEmbed title contains the keyword).
        if pending_verification and self._oembed_factory is not None:
            await self._verify_with_oembed(
                keyword=keyword,
                pending=pending_verification,
                accepted=accepted,
                stats=stats,
            )

        await progress(
            ProgressEvent(
                stage="completed",
                queries_done=stats.queries_run,
                queries_total=queries_total,
                accepted=len(accepted),
                max_results=self._max_results,
            )
        )

        return accepted[: self._max_results], stats

    def _consume_results(
        self,
        results: list[SearchResult],
        keyword: str,
        seen_links: set[str],
        accepted: list[Listing],
        pending_verification: list[tuple[Listing, FilterDecision]],
        stats: PipelineStats,
    ) -> int:
        added = 0
        for result in results:
            if len(accepted) >= self._max_results:
                break
            link = _canonical_instagram_link(result.link)
            if not link or link in seen_links:
                continue

            contacts = extract_contacts(result.title, result.snippet)
            decision: FilterDecision = evaluate_result(
                title=result.title,
                snippet=result.snippet,
                link=link,
                contacts=contacts,
                keyword=keyword,
                allowed_countries=self._allowed_countries,
            )
            if not decision.accepted:
                for reason in decision.reasons:
                    stats.rejected_reasons[reason] = stats.rejected_reasons.get(reason, 0) + 1
                continue

            seen_links.add(link)
            listing = Listing(
                title=result.title,
                link=link,
                snippet=result.snippet,
                contacts=contacts,
            )
            if decision.needs_verification:
                pending_verification.append((listing, decision))
            else:
                accepted.append(listing)
                stats.accepted += 1
                added += 1
        return added

    async def _verify_with_oembed(
        self,
        *,
        keyword: str,
        pending: list[tuple[Listing, FilterDecision]],
        accepted: list[Listing],
        stats: PipelineStats,
    ) -> None:
        assert self._oembed_factory is not None
        urls = [lst.link for lst, _dec in pending]
        client = await self._oembed_factory()
        await client.__aenter__()
        try:
            sem = asyncio.Semaphore(self._oembed_concurrency)
            titles: dict[str, OEmbedResult] = {}

            async def _one(url: str) -> None:
                async with sem:
                    data = await client.fetch(url)
                if data is not None:
                    titles[url] = data

            await asyncio.gather(*(_one(u) for u in urls))
        finally:
            await client.__aexit__(None, None, None)

        stats.oembed_fetched = len(titles)
        stats.oembed_failed = len(urls) - len(titles)

        for listing, decision in pending:
            title_data = titles.get(listing.link)
            if title_data is None:
                # oEmbed couldn't resolve (deleted/private post). Be
                # conservative: drop the hit, since we couldn't confirm
                # relevance.
                stats.rejected_reasons["oembed unavailable"] = (
                    stats.rejected_reasons.get("oembed unavailable", 0) + 1
                )
                continue
            rechecked = recheck_with_title(
                decision=decision,
                title_from_oembed=title_data.title,
                keyword=keyword,
            )
            if rechecked.accepted:
                accepted.append(listing)
                stats.accepted += 1
                stats.oembed_verified += 1
            else:
                for reason in rechecked.reasons:
                    stats.rejected_reasons[reason] = stats.rejected_reasons.get(reason, 0) + 1


def _canonical_instagram_link(link: str) -> str | None:
    """Normalise an Instagram URL by stripping query strings and fragments."""
    if not link:
        return None
    cleaned = link.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    if "instagram.com" not in cleaned.lower():
        return None
    return cleaned


def build_dorks_for(keyword: str) -> list[Dork]:
    """Convenience re-export so callers don't need to import dorks."""
    return build_dorks(keyword)
