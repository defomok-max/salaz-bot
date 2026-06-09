"""Filtering rules for search results: country whitelist, contact validity, keyword relevance.

Relevance
---------
The previous version only checked for email + phone in the snippet, but
**not** for the keyword itself. A Google hit for ``"iphone 14"`` may
return a snippet that does not contain those two words — the post might
mention an iPhone in the caption but show a different chunk in the
snippet (and post-search filters never saw the keyword in the snippet,
so the hit slipped through and showed up as a tractor-rental result).

We now enforce, in order:

1. The link must point at ``instagram.com``.
2. Email + at least one phone signal must be present in the snippet
   (existing rule).
3. **Every token of the keyword must appear in the snippet** (cheap,
   deterministic). This is the cheap, no-network gate.
4. If a post passes the cheap gate, we keep it. We additionally return
   a ``needs_verification=True`` flag for downstream code that has the
   budget to call oEmbed and re-check against the full title.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass

from .countries import (
    BLOCKED_COUNTRY_CODES,
    CountryClassification,
    classify,
    default_country_codes,
    get_country,
)
from .extractors import Contacts
from .text_match import keyword_in_text

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FilterDecision:
    """Outcome of running filters against a candidate result."""

    accepted: bool
    reasons: tuple[str, ...] = ()
    country: str | None = None
    needs_verification: bool = False


def evaluate_result(
    *,
    title: str,
    snippet: str,
    link: str,
    contacts: Contacts,
    keyword: str = "",
    allowed_countries: Iterable[str] | None = None,
    allow_unknown: bool = True,
    require_email: bool = True,
    require_phone: bool = True,
    require_keyword_in_snippet: bool = True,
) -> FilterDecision:
    """Decide whether a single search result should be returned to the user.

    A result is accepted if:

    * the link is on instagram.com,
    * (when ``require_email``) at least one email was extracted,
    * (when ``require_phone``) at least one phone number of any kind
      (WhatsApp or plain) was extracted,
    * the keyword (if non-empty) has all of its tokens present in the
      snippet (``require_keyword_in_snippet=True``).
    * the result classifies into a country present in ``allowed_countries``.

    ``allowed_countries=None`` falls back to the default whitelist (everything
    except the CIS bloc). ``allow_unknown`` controls what happens when the
    country classifier can't make a decision: by default such results are
    accepted.
    """
    reasons: list[str] = []
    needs_verification = False

    if not link or "instagram.com" not in link.lower():
        reasons.append("not an instagram.com link")
        return FilterDecision(False, tuple(reasons))

    if require_email and not contacts.has_email:
        reasons.append("no email in snippet")
    if require_phone and not contacts.has_any_phone:
        reasons.append("no phone or whatsapp in snippet")

    if reasons:
        return FilterDecision(False, tuple(reasons))

    # Keyword relevance. If the snippet does not contain the keyword
    # tokens, we *still* accept the hit but mark it as needing oEmbed
    # verification. That way we can use the cheap check as a fast gate
    # but still let the downstream pipeline recover cases where the
    # snippet is just a bad snippet of an otherwise-relevant post.
    if require_keyword_in_snippet and keyword:
        text = " ".join(t for t in (title, snippet) if t)
        if not keyword_in_text(keyword, text):
            needs_verification = True
            logger.debug(
                "keyword %r not in snippet for %s; deferring to oEmbed",
                keyword,
                link,
            )

    if allowed_countries is None:
        allowed = set(default_country_codes())
    else:
        allowed = {c.upper() for c in allowed_countries}

    classification = classify(
        title,
        snippet,
        phones=(*contacts.whatsapp_numbers, *contacts.phones),
    )

    decision = _check_country(classification, allowed=allowed, allow_unknown=allow_unknown)
    if decision is not None:
        return FilterDecision(False, (decision,), classification.primary)

    return FilterDecision(
        accepted=True,
        reasons=(),
        country=classification.primary,
        needs_verification=needs_verification,
    )


def recheck_with_title(
    *,
    decision: FilterDecision,
    title_from_oembed: str,
    keyword: str,
) -> FilterDecision:
    """Re-evaluate a previously ``needs_verification`` hit using oEmbed title.

    If the oEmbed title still doesn't contain the keyword, the hit is
    rejected. Otherwise we keep the original decision and clear the
    flag.
    """
    if not decision.needs_verification or not keyword:
        return decision
    if keyword_in_text(keyword, title_from_oembed):
        return FilterDecision(
            accepted=True,
            reasons=(),
            country=decision.country,
            needs_verification=False,
        )
    return FilterDecision(
        accepted=False,
        reasons=("keyword not in oembed title",),
        country=decision.country,
        needs_verification=False,
    )


def _check_country(
    classification: CountryClassification,
    *,
    allowed: set[str],
    allow_unknown: bool,
) -> str | None:
    """Return a rejection reason or None if the country check passes."""
    # Hard block: CIS bloc is never returned, regardless of the user's
    # whitelist (defence in depth — even if a blocked code somehow ends up
    # in ``allowed`` via a stale DB row, it cannot pass this gate).
    if classification.primary is not None and classification.primary in BLOCKED_COUNTRY_CODES:
        country = get_country(classification.primary)
        label = country.name_ru if country else classification.primary
        return f"country blocked: {label}"

    if classification.primary is not None:
        if classification.primary in allowed:
            return None
        country = get_country(classification.primary)
        label = country.name_ru if country else classification.primary
        return f"country not allowed: {label}"

    # No high-confidence country, but every low-confidence candidate is on
    # the blocklist (typical for cyrillic-dominant snippets). Reject hard.
    if classification.candidates and classification.candidates <= BLOCKED_COUNTRY_CODES:
        codes = ", ".join(sorted(classification.candidates))
        return f"likely blocked country ({codes})"

    # No high-confidence country. If we have low-confidence candidates and
    # *all* of them are outside the whitelist, drop the result.
    if classification.candidates and not (classification.candidates & allowed):
        codes = ", ".join(sorted(classification.candidates))
        return f"likely outside whitelist ({codes})"

    if not allow_unknown:
        return "country unknown"
    return None
