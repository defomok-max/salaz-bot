"""Tests for the freelance-platform dork builder and filter."""

from __future__ import annotations

import pytest

from instagram_dork_bot.extractors import Contacts
from instagram_dork_bot.freelance.dorks import build_all_dorks, build_dorks
from instagram_dork_bot.freelance.filters import (
    _classify_listing_type,
    _extract_budget,
    evaluate_freelance_result,
)
from instagram_dork_bot.freelance.platforms import (
    PLATFORMS,
    all_platform_codes,
    get_platform,
)


def test_all_platform_codes_lists_every_platform() -> None:
    codes = set(all_platform_codes())
    assert {"upwork", "fiverr", "freelancer", "kwork"} <= codes


def test_get_platform_unknown_returns_none() -> None:
    assert get_platform("ghost") is None


def test_build_dorks_for_known_platform() -> None:
    dorks = build_dorks("python bot", platform="upwork")
    assert dorks
    for d in dorks:
        assert d.platform == "upwork"
        assert "site:upwork.com" in d.query
        assert '"python bot"' in d.query


def test_build_dorks_rejects_unknown_platform() -> None:
    with pytest.raises(ValueError):
        build_dorks("kw", platform="nope")


def test_build_dorks_rejects_blank_keyword() -> None:
    with pytest.raises(ValueError):
        build_dorks("   ", platform="upwork")


def test_build_all_dorks_returns_dorks_for_every_platform() -> None:
    out = build_all_dorks("web scraping")
    platforms_seen = {d.platform for d in out}
    assert platforms_seen == {p.code for p in PLATFORMS}


def test_evaluate_accepts_listing_with_email() -> None:
    contacts = Contacts(emails=("hire@brand.io",))
    decision = evaluate_freelance_result(
        title="Looking for python dev",
        snippet="Email hire@brand.io for details",
        link="https://www.upwork.com/freelance-jobs/abc/",
        contacts=contacts,
        keyword="python",
    )
    assert decision.accepted is True
    assert decision.platform == "upwork"
    assert decision.listing_type == "job"


def test_evaluate_accepts_listing_with_telegram() -> None:
    contacts = Contacts(
        emails=(),
        telegram_handles=("hire_me",),
    )
    decision = evaluate_freelance_result(
        title="figma designer wanted",
        snippet="Contact us at t.me/hire_me for portfolio",
        link="https://www.fiverr.com/gig/abc123",
        contacts=contacts,
        keyword="figma",
    )
    assert decision.accepted is True
    assert decision.platform == "fiverr"


def test_evaluate_rejects_unknown_platform() -> None:
    contacts = Contacts(emails=("a@b.com",))
    decision = evaluate_freelance_result(
        title="x",
        snippet="a@b.com",
        link="https://example.com/job/123/",
        contacts=contacts,
    )
    assert decision.accepted is False
    assert "unknown platform" in decision.reasons


def test_evaluate_rejects_when_url_is_not_a_listing() -> None:
    contacts = Contacts(emails=("a@b.com",))
    decision = evaluate_freelance_result(
        title="x",
        snippet="a@b.com",
        link="https://www.upwork.com/about/",
        contacts=contacts,
    )
    assert decision.accepted is False
    assert "not a" in decision.reasons[0]


def test_evaluate_rejects_when_no_contact() -> None:
    decision = evaluate_freelance_result(
        title="Looking for dev",
        snippet="No contact info here",
        link="https://www.upwork.com/freelance-jobs/abc/",
        contacts=Contacts(),
        keyword="dev",
    )
    assert decision.accepted is False
    assert "no contact" in decision.reasons[0]


def test_evaluate_marks_needs_verification_when_keyword_missing() -> None:
    contacts = Contacts(emails=("a@b.com",))
    decision = evaluate_freelance_result(
        title="Senior dev wanted",
        snippet="Send your CV to a@b.com",
        link="https://www.upwork.com/freelance-jobs/abc/",
        contacts=contacts,
        keyword="python",
    )
    assert decision.accepted is True
    assert decision.needs_verification is True


def test_evaluate_filters_out_disallowed_platform() -> None:
    contacts = Contacts(emails=("a@b.com",))
    decision = evaluate_freelance_result(
        title="x",
        snippet="a@b.com",
        link="https://www.upwork.com/freelance-jobs/abc/",
        contacts=contacts,
        allowed_platforms=("fiverr",),
    )
    assert decision.accepted is False
    assert "platform not allowed" in decision.reasons[0]


def test_extract_budget_picks_dollar_amount() -> None:
    assert _extract_budget("Looking for python dev, $500 fixed", "Title") == "$500"


def test_extract_budget_picks_budget_token() -> None:
    assert _extract_budget("Budget: 1500", "Title") == "$1500"


def test_extract_budget_handles_no_match() -> None:
    assert _extract_budget("no price here", "title") == ""


def test_classify_listing_type_for_upwork() -> None:
    assert (
        _classify_listing_type(
            get_platform("upwork"),
            "https://upwork.com/freelance-jobs/abc/",
        )
        == "job"
    )
    assert _classify_listing_type(get_platform("upwork"), "https://upwork.com/u/abc/") == "profile"


def test_classify_listing_type_for_fiverr() -> None:
    assert _classify_listing_type(get_platform("fiverr"), "https://fiverr.com/gig/abc/") == "gig"
    assert (
        _classify_listing_type(get_platform("fiverr"), "https://fiverr.com/users/abc/") == "profile"
    )
