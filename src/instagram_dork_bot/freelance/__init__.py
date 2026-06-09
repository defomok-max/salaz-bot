"""Freelance-platform search — separate pipeline from the Instagram dork bot.

The bot has a second mode that scrapes public freelance listings (jobs
posted by clients) for the user's keyword. We look for listings that
mention an **email or contact outside the platform** — most platforms
hide contact info, but a subset of clients still post their email or
Telegram/WhatsApp directly in the listing body so they can be reached
without going through the platform.

Supported platforms
-------------------
* Upwork
* Fiverr
* Freelancer.com
* Kwork

We fetch listings via Google dorks (``site:upwork.com ...``) through
the same Serper backend. Each platform has a tailored set of dorks to
maximise recall.
"""

from __future__ import annotations

from .dorks import FreelanceDork, build_all_dorks, build_dorks
from .filters import FreelanceFilterDecision, evaluate_freelance_result
from .pipeline import (
    FreelanceListing,
    FreelancePipeline,
    FreelancePipelineStats,
    FreelanceProgressEvent,
)
from .platforms import PLATFORMS, FreelancePlatform, all_platform_codes, get_platform

__all__ = [
    "FreelanceDork",
    "FreelanceFilterDecision",
    "FreelanceListing",
    "FreelancePipeline",
    "FreelancePipelineStats",
    "FreelancePlatform",
    "FreelanceProgressEvent",
    "PLATFORMS",
    "all_platform_codes",
    "build_all_dorks",
    "build_dorks",
    "evaluate_freelance_result",
    "get_platform",
]
