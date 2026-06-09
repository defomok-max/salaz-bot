"""Pipeline that runs the freelance-platform dorks and filters the hits."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ..extractors import Contacts, extract_contacts
from ..search import SearchResult, SerperClient
from ..text_match import keyword_in_text
from .dorks import FreelanceDork, build_all_dorks
from .filters import FreelanceFilterDecision, evaluate_freelance_result

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FreelanceListing:
    """A freelance search hit that passed all filters."""

    title: str
    link: str
    snippet: str
    contacts: Contacts
    platform: str
    listing_type: str
    budget: str


@dataclass
class FreelancePipelineStats:
    queries_run: int = 0
    raw_results: int = 0
    accepted: int = 0
    rejected_reasons: dict[str, int] = field(default_factory=dict)
    per_platform: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class FreelanceProgressEvent:
    """Progress signal emitted during pipeline execution."""

    stage: str  # "init" | "dork_start" | "dork_done" | "dork_failed" | "completed"
    queries_done: int = 0
    queries_total: int = 0
    accepted: int = 0
    max_results: int = 0
    label: str = ""
    detail: str = ""


FreelanceProgressCallback = Callable[[FreelanceProgressEvent], Awaitable[None]]


async def _noop_freelance_progress(_event: FreelanceProgressEvent) -> None:
    return None


class FreelancePipeline:
    """Run a series of dork queries across the supported platforms."""

    def __init__(
        self,
        client: SerperClient,
        *,
        max_results: int = 50,
        results_per_query: int = 10,
        max_search_calls: int = 20,
        allowed_platforms: tuple[str, ...] | None = None,
    ) -> None:
        self._client = client
        self._max_results = max_results
        self._results_per_query = results_per_query
        self._max_search_calls = max_search_calls
        self._allowed_platforms = (
            tuple(p.lower() for p in allowed_platforms) if allowed_platforms is not None else None
        )

    async def run(
        self,
        keyword: str,
        *,
        progress: FreelanceProgressCallback | None = None,
    ) -> tuple[list[FreelanceListing], FreelancePipelineStats]:
        progress = progress or _noop_freelance_progress
        stats = FreelancePipelineStats()
        seen_links: set[str] = set()
        accepted: list[FreelanceListing] = []

        dorks = build_all_dorks(keyword)
        queries_total = min(len(dorks), self._max_search_calls)
        await progress(
            FreelanceProgressEvent(
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
                FreelanceProgressEvent(
                    stage="dork_start",
                    queries_done=stats.queries_run,
                    queries_total=queries_total,
                    accepted=len(accepted),
                    max_results=self._max_results,
                    label=f"{dork.platform}/{dork.label}",
                )
            )
            try:
                results = await self._client.search(dork.query, num=self._results_per_query)
            except Exception as exc:
                logger.exception("freelance dork %s failed", dork.label)
                await progress(
                    FreelanceProgressEvent(
                        stage="dork_failed",
                        queries_done=stats.queries_run,
                        queries_total=queries_total,
                        accepted=len(accepted),
                        max_results=self._max_results,
                        label=f"{dork.platform}/{dork.label}",
                        detail=str(exc),
                    )
                )
                continue

            stats.queries_run += 1
            stats.raw_results += len(results)

            added = self._consume(
                results,
                keyword,
                dork,
                seen_links,
                accepted,
                stats,
            )
            await progress(
                FreelanceProgressEvent(
                    stage="dork_done",
                    queries_done=stats.queries_run,
                    queries_total=queries_total,
                    accepted=len(accepted),
                    max_results=self._max_results,
                    label=f"{dork.platform}/{dork.label}",
                    detail=f"{len(results)} hits, {added} accepted",
                )
            )
            if len(accepted) >= self._max_results:
                break

        await progress(
            FreelanceProgressEvent(
                stage="completed",
                queries_done=stats.queries_run,
                queries_total=queries_total,
                accepted=len(accepted),
                max_results=self._max_results,
            )
        )
        return accepted[: self._max_results], stats

    def _consume(
        self,
        results: list[SearchResult],
        keyword: str,
        dork: FreelanceDork,
        seen_links: set[str],
        accepted: list[FreelanceListing],
        stats: FreelancePipelineStats,
    ) -> int:
        added = 0
        for result in results:
            if len(accepted) >= self._max_results:
                break
            link = _canonical_freelance_link(result.link)
            if not link or link in seen_links:
                continue
            contacts = extract_contacts(result.title, result.snippet)
            decision: FreelanceFilterDecision = evaluate_freelance_result(
                title=result.title,
                snippet=result.snippet,
                link=link,
                contacts=contacts,
                keyword=keyword,
                allowed_platforms=self._allowed_platforms,
            )
            if not decision.accepted:
                for reason in decision.reasons:
                    stats.rejected_reasons[reason] = stats.rejected_reasons.get(reason, 0) + 1
                continue

            seen_links.add(link)
            accepted.append(
                FreelanceListing(
                    title=result.title,
                    link=link,
                    snippet=result.snippet,
                    contacts=contacts,
                    platform=decision.platform or dork.platform,
                    listing_type=decision.listing_type,
                    budget=decision.budget,
                )
            )
            stats.accepted += 1
            stats.per_platform[decision.platform or dork.platform] = (
                stats.per_platform.get(decision.platform or dork.platform, 0) + 1
            )
            added += 1
        return added


def _canonical_freelance_link(link: str) -> str | None:
    """Strip query strings and fragments from a URL."""
    if not link:
        return None
    cleaned = link.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    return cleaned or None


def keyword_for_storage(keyword: str) -> str:
    """Trim keyword and return a clean string for SQLite storage."""
    return keyword.strip()


def matches_keyword(keyword: str, title: str, snippet: str) -> bool:
    """Public helper used by tests for the keyword relevance check."""
    return keyword_in_text(keyword, " ".join(t for t in (title, snippet) if t))
