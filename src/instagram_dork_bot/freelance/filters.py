"""Filters specific to the freelance-platform pipeline.

Rules:

* The URL must belong to one of the supported platforms (a simple
  substring check is enough — we already constrained the Google dork
  with ``site:host``).
* The URL must look like a listing (``/freelance-jobs/`` / ``/gig/`` /
  etc.) or, for some platforms, a user profile (``/u/`` / ``/users/``).
* At least one contact signal must be present (email, WhatsApp, or
  Telegram).
* For the contact signal we also check the snippet contains the
  keyword tokens — same cheap gate as the Instagram side. Freelance
  listings are usually more text-heavy so the snippet often contains
  the keyword; if not, we mark the listing for oEmbed verification
  (the freelancer.py caller decides whether to verify).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlparse

from ..extractors import Contacts
from ..text_match import keyword_in_text
from .platforms import PLATFORMS, FreelancePlatform, get_platform


@dataclass(frozen=True)
class FreelanceFilterDecision:
    """Outcome of running filters against a freelance hit."""

    accepted: bool
    reasons: tuple[str, ...] = ()
    platform: str | None = None
    listing_type: str = ""
    budget: str = ""
    needs_verification: bool = False


def evaluate_freelance_result(
    *,
    title: str,
    snippet: str,
    link: str,
    contacts: Contacts,
    keyword: str = "",
    allowed_platforms: Iterable[str] | None = None,
) -> FreelanceFilterDecision:
    """Apply all platform-specific filters and return a decision."""
    if not link:
        return FreelanceFilterDecision(False, ("empty link",))

    parsed = urlparse(link)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]

    matched: FreelancePlatform | None = None
    for plat in PLATFORMS:
        if plat.host in host:
            matched = plat
            break
    if matched is None:
        return FreelanceFilterDecision(False, ("unknown platform",))

    if allowed_platforms is not None and matched.code not in {c.lower() for c in allowed_platforms}:
        return FreelanceFilterDecision(False, (f"platform not allowed: {matched.code}",))

    if not _looks_like_listing(matched, link):
        return FreelanceFilterDecision(False, (f"not a {matched.type} URL",))

    if not contacts.has_any_contact:
        return FreelanceFilterDecision(
            False,
            ("no contact (email, whatsapp, telegram) in snippet",),
            platform=matched.code,
        )

    budget = _extract_budget(snippet, title)
    listing_type = _classify_listing_type(matched, link)

    needs_verification = False
    if keyword:
        text = " ".join(t for t in (title, snippet) if t)
        if not keyword_in_text(keyword, text):
            needs_verification = True

    return FreelanceFilterDecision(
        accepted=True,
        platform=matched.code,
        listing_type=listing_type,
        budget=budget,
        needs_verification=needs_verification,
    )


def _looks_like_listing(platform: FreelancePlatform, link: str) -> bool:
    """Return True if ``link`` matches one of the platform's listing paths."""
    lowered = link.lower()
    return any(path in lowered for path in platform.search_paths)


def _extract_budget(snippet: str, title: str) -> str:
    """Try to extract a price/budget string from the snippet.

    Very cheap heuristic — looks for a dollar amount next to "budget" /
    "fixed" / "hourly" / "from" / "starting at" / "$". Returns the empty
    string when nothing matches.
    """
    import re

    if not snippet and not title:
        return ""
    text = f"{title}\n{snippet}"
    patterns = [
        r"\$\s?(\d{1,5}(?:[.,]\d{2,3})?)",
        r"(?:budget|price|rate|cost)\s*[:\-]?\s*\$?\s?(\d{1,5}(?:[.,]\d{2,3})?)",
        r"(?:fixed|hourly)\s*[:\-]?\s*\$?\s?(\d{1,5}(?:[.,]\d{2,3})?)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return f"${m.group(1)}"
    return ""


def _classify_listing_type(platform: FreelancePlatform, link: str) -> str:
    """Map a URL path to a short listing-type string for the UI."""
    lowered = link.lower()
    if platform.type == "gigs":
        if "/gig" in lowered:
            return "gig"
        if "/user" in lowered or "/freelancer" in lowered or "/u/" in lowered:
            return "profile"
        return "gig"
    if "/job" in lowered or "/project" in lowered or "/contest" in lowered:
        return "job"
    if "/u/" in lowered or "/user" in lowered or "/freelancer" in lowered:
        return "profile"
    return "job"


def _platform(code: str) -> FreelancePlatform | None:
    return get_platform(code)
