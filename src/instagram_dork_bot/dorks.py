"""Build Google dork queries that target Instagram posts with contact info.

Design notes
------------
The previous version emitted one dork per email host × two WhatsApp
variants (12 dorks) and a few extra ones. With ``MAX_SEARCH_CALLS=15``
and a per-query yield of ~10 raw results, that gave 130 raw hits
maximum — but the *snippet* the user actually sees in the result message
was not guaranteed to contain the keyword. A tractor-rental post that
mentions an iPhone in the caption would slip through and surface to the
user.

The new strategy is wider but cheaper (fewer calls, broader queries) and
relies on a downstream :func:`~instagram_dork_bot.filters.evaluate_result`
that **requires the keyword to appear in the snippet** (with
``allintext:`` style enforcement baked in). The dorks here only need to
force a contact signal (``wa.me`` / ``whatsapp`` / email host) alongside
the keyword — no need to over-narrow.

The country hint dork uses the caller's *actual* selected countries, not
the hard-coded default list, so that searching "iphone" with only Germany
selected doesn't pull in US tractor listings.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

# Country names the dork can mention. Excludes the CIS list. We do NOT
# enumerate every country in the world -- a representative whitelist of
# high-volume Western markets is enough for Google's bias hint. The
# post-search filter enforces the "no CIS" rule on actual extracted data.
DEFAULT_TARGET_COUNTRIES: tuple[str, ...] = (
    "USA",
    "United States",
    "Canada",
    "United Kingdom",
    "Australia",
    "Germany",
    "France",
    "Spain",
    "Italy",
    "Netherlands",
)

# Sale-style phrases used to bias toward commercial posts.
SALE_HINTS: tuple[str, ...] = (
    "for sale",
    "DM to order",
    "DM for price",
    "available",
)

# WhatsApp-related tokens. Either a direct wa.me link OR an explicit
# "WhatsApp" mention. Phone-only posts (call only) are excluded by the
# downstream contact extractor.
WHATSAPP_HINTS: tuple[str, ...] = (
    "wa.me",
    "whatsapp",
)

# Email-host tokens. Each per-host dork is a separate Google query so we
# rotate through these to maximise recall (Google forces all bracketed
# tokens to appear, so combining all hosts in one OR-clause caps recall).
EMAIL_HOSTS: tuple[str, ...] = (
    "@gmail.com",
    "@yahoo.com",
    "@hotmail.com",
    "@outlook.com",
    "@icloud.com",
    "@proton.me",
)


@dataclass(frozen=True)
class Dork:
    """A single Google dork query string with a human-readable label."""

    label: str
    query: str


def _join_or(values: Iterable[str]) -> str:
    quoted = [f'"{v}"' for v in values]
    return "(" + " OR ".join(quoted) + ")"


def build_dorks(
    keyword: str,
    countries: Iterable[str] | None = None,
) -> list[Dork]:
    """Build a list of complementary Google dork queries.

    Each query forces ``site:instagram.com`` plus the keyword. The
    contact signals are combined in OR-groups so each dork is wide enough
    to actually return results; the downstream keyword-in-snippet filter
    prevents the keyword from drifting away.

    The country dork uses ``countries`` if provided, otherwise falls back
    to a representative Western-market whitelist.
    """
    keyword = keyword.strip()
    if not keyword:
        raise ValueError("keyword must be non-empty")

    countries = tuple(countries) if countries is not None else DEFAULT_TARGET_COUNTRIES
    base = f'site:instagram.com "{keyword}"'

    dorks: list[Dork] = []

    # 1. Per-host strong dorks: post must contain THIS host AND a wa.me link.
    #    One query per host — Google handles OR-groups of email hosts poorly
    #    because of how it tokenises them, so we keep the per-host strategy.
    for host in EMAIL_HOSTS:
        dorks.append(
            Dork(
                label=f"wa.me+{host.lstrip('@')}",
                query=f'{base} "{host}" "wa.me"',
            )
        )

    # 2. Single broad dork that covers the wa.me OR whatsapp fallback for
    #    every email host in one shot. This is the new "wide-net" dork that
    #    pulls in posts that mention *any* consumer email alongside *any*
    #    WhatsApp signal — gives us a much higher hit rate for the same
    #    number of API calls.
    dorks.append(
        Dork(
            label="wide+contact",
            query=f"{base} {_join_or(EMAIL_HOSTS)} {_join_or(WHATSAPP_HINTS)}",
        )
    )

    # 3. Sale-biased dork. Catches accounts that explicitly use commerce
    #    vocabulary alongside a contact signal.
    dorks.append(
        Dork(
            label="sale+contact",
            query=(
                f"{base} {_join_or(SALE_HINTS)} {_join_or(WHATSAPP_HINTS)} {_join_or(EMAIL_HOSTS)}"
            ),
        )
    )

    # 4. Plain-phone dork — many listings advertise a phone number without
    #    saying "WhatsApp" or linking wa.me. We require the keyword AND a
    #    phone-shaped token (broad: "phone"/"call"/"tel").
    dorks.append(
        Dork(
            label="phone+broad",
            query=f'{base} ("phone" OR "call" OR "tel" OR "mobile")',
        )
    )

    # 5. Country-biased dork. Names the caller's selected countries to
    #    nudge Google toward the right locale.
    if countries:
        dorks.append(
            Dork(
                label="country+contact",
                query=(
                    f"{base} {_join_or(countries)} "
                    f"{_join_or(EMAIL_HOSTS)} "
                    f"{_join_or(WHATSAPP_HINTS)}"
                ),
            )
        )

    return dorks


def country_keyword_terms(
    countries: Sequence[str] | None,
) -> tuple[str, ...]:
    """Map ISO codes to the human-friendly country names used in dorks.

    The country dork only helps Google if we pass readable names
    ("Germany", "Berlin") rather than ISO codes ("DE"). This helper
    looks up the right names for the caller's whitelist; codes we
    don't know about are returned verbatim (so e.g. custom codes still
    surface in the dork).
    """
    if not countries:
        return DEFAULT_TARGET_COUNTRIES
    out: list[str] = []
    from .countries import get_country  # local import to avoid cycle

    for code in countries:
        c = get_country(code)
        if c is None:
            out.append(code)
            continue
        # First keyword (which is usually the country name in English) is
        # what Google's locale-bias likes best.
        if c.keywords:
            out.append(c.keywords[0])
        else:
            out.append(c.name_ru)
    return tuple(out) or DEFAULT_TARGET_COUNTRIES
