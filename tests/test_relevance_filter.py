"""Tests for the keyword-relevance filter and the oEmbed re-check."""

from __future__ import annotations

from instagram_dork_bot.extractors import Contacts
from instagram_dork_bot.filters import evaluate_result, recheck_with_title


def _result(
    *,
    snippet: str = "Selling iPhone 14, email shop@brand.io WhatsApp wa.me/15551234567",
    title: str = "Brand Shop",
    link: str = "https://www.instagram.com/p/abc/",
    contacts: Contacts | None = None,
    keyword: str = "iphone 14",
):
    return evaluate_result(
        title=title,
        snippet=snippet,
        link=link,
        contacts=contacts
        or Contacts(
            emails=("shop@brand.io",),
            whatsapp_numbers=("+15551234567",),
        ),
        keyword=keyword,
    )


def test_filter_accepts_when_keyword_in_snippet() -> None:
    decision = _result()
    assert decision.accepted is True
    assert decision.needs_verification is False


def test_filter_marks_verification_when_keyword_missing() -> None:
    decision = _result(snippet="Tractor rental, contact shop@brand.io wa.me/15551234567")
    assert decision.accepted is True
    assert decision.needs_verification is True


def test_recheck_promotes_when_oembed_title_has_keyword() -> None:
    decision = _result(snippet="Tractor rental, contact shop@brand.io wa.me/15551234567")
    assert decision.needs_verification is True
    rechecked = recheck_with_title(
        decision=decision,
        title_from_oembed="Selling iPhone 14 Pro — DM to order",
        keyword="iphone 14",
    )
    assert rechecked.accepted is True
    assert rechecked.needs_verification is False


def test_recheck_rejects_when_oembed_title_lacks_keyword() -> None:
    decision = _result(snippet="Tractor rental, contact shop@brand.io wa.me/15551234567")
    rechecked = recheck_with_title(
        decision=decision,
        title_from_oembed="Renting tractors in Munich",
        keyword="iphone 14",
    )
    assert rechecked.accepted is False
    assert "keyword not in oembed title" in rechecked.reasons


def test_recheck_noop_when_not_needing_verification() -> None:
    decision = _result()
    rechecked = recheck_with_title(
        decision=decision,
        title_from_oembed="anything",
        keyword="iphone 14",
    )
    # Decision was already accepted; the recheck is a no-op.
    assert rechecked is decision
