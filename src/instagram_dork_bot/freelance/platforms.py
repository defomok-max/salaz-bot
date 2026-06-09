"""Static metadata about the freelance platforms we support.

Each platform has:

* a ``host`` — the FQDN used in the ``site:`` Google operator,
* a ``label`` — human-friendly name for the UI,
* a ``type`` — ``"jobs"`` for client-posted work (we look for jobs) or
  ``"gigs"`` for seller-posted offers (we look for gigs),
* a tuple of ``search_paths`` — substrings we look for in the URL to
  decide whether a Google hit is actually a listing (e.g. ``/freelance-jobs/``).
* a tuple of ``contact_hints`` — search tokens we use in dorks to bias
  toward listings that mention a contact channel *outside* the
  platform. These are the same signals the Instagram side uses
  (email hosts, ``wa.me``, ``whatsapp``, Telegram).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FreelancePlatform:
    """One supported freelance platform."""

    code: str
    label: str
    host: str
    type: str  # "jobs" or "gigs"
    search_paths: tuple[str, ...] = ()
    contact_hints: tuple[str, ...] = ()
    country_hints: tuple[str, ...] = ()


PLATFORMS: tuple[FreelancePlatform, ...] = (
    FreelancePlatform(
        code="upwork",
        label="Upwork",
        host="upwork.com",
        type="jobs",
        search_paths=("/freelance-jobs/", "/jobs/", "/nx/find-work/", "/freelancers/"),
        contact_hints=(
            "@gmail.com",
            "@yahoo.com",
            "@hotmail.com",
            "@outlook.com",
            "t.me/",
            "telegram",
            "wa.me/",
            "whatsapp",
            "telegram.me/",
        ),
    ),
    FreelancePlatform(
        code="fiverr",
        label="Fiverr",
        host="fiverr.com",
        type="gigs",
        search_paths=("/gig/", "/gigs/", "/users/"),
        contact_hints=(
            "@gmail.com",
            "@yahoo.com",
            "@hotmail.com",
            "@outlook.com",
            "t.me/",
            "telegram",
            "wa.me/",
            "whatsapp",
        ),
    ),
    FreelancePlatform(
        code="freelancer",
        label="Freelancer.com",
        host="freelancer.com",
        type="jobs",
        search_paths=("/projects/", "/job/", "/contest/", "/u/"),
        contact_hints=(
            "@gmail.com",
            "@yahoo.com",
            "@hotmail.com",
            "@outlook.com",
            "t.me/",
            "telegram",
            "wa.me/",
            "whatsapp",
        ),
    ),
    FreelancePlatform(
        code="kwork",
        label="Kwork",
        host="kwork.ru",
        type="gigs",
        search_paths=("/project/", "/projects/", "/freelancer/"),
        contact_hints=(
            "@gmail.com",
            "@yahoo.com",
            "@mail.ru",
            "@yandex.ru",
            "t.me/",
            "telegram",
            "wa.me/",
            "whatsapp",
        ),
    ),
)


def get_platform(code: str) -> FreelancePlatform | None:
    upper = code.lower()
    for p in PLATFORMS:
        if p.code == upper:
            return p
    return None


def all_platform_codes() -> tuple[str, ...]:
    return tuple(p.code for p in PLATFORMS)
