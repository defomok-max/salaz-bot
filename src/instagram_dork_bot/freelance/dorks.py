"""Dork builders for the freelance platforms.

For each platform we emit a small set of complementary Google dorks:

* a per-host dork with the keyword AND a contact token (email, telegram,
  whatsapp) — pulls in listings that *actually* expose a contact channel,
* a wide-net dork that OR's all contact tokens — broader recall, fewer
  total queries,
* a "project"/"job" biased dork when the platform is job-shaped, or a
  "gig" biased dork when the platform is gig-shaped.

The downstream filter (:mod:`filters`) enforces that the URL contains
one of the platform's ``search_paths`` and that at least one email or
Telegram/WhatsApp handle was extracted.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .platforms import PLATFORMS, FreelancePlatform, get_platform

# A small set of generic "this is a job" or "this is a gig" anchors we
# tack onto dorks to nudge Google toward the right listing type.
JOB_ANCHORS: tuple[str, ...] = (
    "looking for",
    "need a",
    "hiring",
    "job",
    "project",
)

GIG_ANCHORS: tuple[str, ...] = (
    "i will",
    "for sale",
    "service",
    "package",
    "starting at",
    "from $",
)


@dataclass(frozen=True)
class FreelanceDork:
    """A single dork plus the platform it targets."""

    platform: str
    label: str
    query: str


def _join_or(values: Iterable[str]) -> str:
    quoted = [f'"{v}"' for v in values]
    return "(" + " OR ".join(quoted) + ")"


def _contact_query(platform: FreelancePlatform) -> str:
    """Return a quoted OR-group of contact tokens for the platform."""
    return _join_or(platform.contact_hints)


def _anchor_query(platform: FreelancePlatform) -> str:
    if platform.type == "gigs":
        return _join_or(GIG_ANCHORS)
    return _join_or(JOB_ANCHORS)


def build_dorks(
    keyword: str,
    *,
    platform: str,
) -> list[FreelanceDork]:
    """Build a list of dorks for the given platform + keyword."""
    plat = get_platform(platform)
    if plat is None:
        raise ValueError(f"unknown platform: {platform!r}")

    keyword = keyword.strip()
    if not keyword:
        raise ValueError("keyword must be non-empty")

    base = f'site:{plat.host} "{keyword}"'
    contact_q = _contact_query(plat)
    anchor_q = _anchor_query(plat)
    dorks: list[FreelanceDork] = []

    # 1. The wide-net dork: keyword AND any contact token.
    dorks.append(
        FreelanceDork(
            platform=plat.code,
            label=f"{plat.code}+contact",
            query=f"{base} {contact_q}",
        )
    )

    # 2. Anchor-biased dork for the listing type (job vs gig).
    dorks.append(
        FreelanceDork(
            platform=plat.code,
            label=f"{plat.code}+anchor",
            query=f"{base} {anchor_q} {contact_q}",
        )
    )

    # 3. Per-host email dorks: one per top email host, so the most
    #    common case (gmail) doesn't drown the rest out in a single
    #    OR-group.
    email_hosts = [h for h in plat.contact_hints if h.startswith("@")]
    for host in email_hosts[:4]:
        dorks.append(
            FreelanceDork(
                platform=plat.code,
                label=f"{plat.code}+{host.lstrip('@')}",
                query=f'{base} "{host}"',
            )
        )

    return dorks


def build_all_dorks(keyword: str) -> list[FreelanceDork]:
    """Build dorks for every supported platform."""
    out: list[FreelanceDork] = []
    for plat in PLATFORMS:
        out.extend(build_dorks(keyword, platform=plat.code))
    return out
