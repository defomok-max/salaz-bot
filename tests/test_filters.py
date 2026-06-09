"""Tests for the country-aware result filter."""

from __future__ import annotations

from instagram_dork_bot.countries import (
    all_country_codes,
    default_country_codes,
)
from instagram_dork_bot.extractors import Contacts
from instagram_dork_bot.filters import evaluate_result


def test_evaluate_result_accepts_with_whatsapp() -> None:
    contacts = Contacts(
        emails=("shop@brand.io",),
        whatsapp_numbers=("+15551234567",),
    )
    decision = evaluate_result(
        title="Brand Shop",
        snippet="Selling iPhone, email shop@brand.io / WhatsApp wa.me/15551234567",
        link="https://www.instagram.com/p/abcdef/",
        contacts=contacts,
    )
    assert decision.accepted is True
    assert decision.reasons == ()


def test_evaluate_result_accepts_with_plain_phone_only() -> None:
    """Email + plain phone (no WhatsApp) is now an accepted combination."""
    contacts = Contacts(
        emails=("shop@brand.io",),
        whatsapp_numbers=(),
        phones=("+15551234567",),
    )
    decision = evaluate_result(
        title="Brand Shop",
        snippet="Selling iPhone, email shop@brand.io / call +1 555 123 4567",
        link="https://www.instagram.com/p/abcdef/",
        contacts=contacts,
    )
    assert decision.accepted is True
    assert decision.reasons == ()


def test_evaluate_result_rejects_when_missing_email() -> None:
    contacts = Contacts(
        emails=(),
        whatsapp_numbers=("+15551234567",),
    )
    decision = evaluate_result(
        title="Brand",
        snippet="WhatsApp wa.me/15551234567",
        link="https://www.instagram.com/p/abcdef/",
        contacts=contacts,
    )
    assert decision.accepted is False
    assert "no email in snippet" in decision.reasons


def test_evaluate_result_rejects_when_no_phone_at_all() -> None:
    contacts = Contacts(
        emails=("shop@brand.io",),
        whatsapp_numbers=(),
        phones=(),
    )
    decision = evaluate_result(
        title="Brand",
        snippet="Email shop@brand.io for inquiries",
        link="https://www.instagram.com/p/abcdef/",
        contacts=contacts,
    )
    assert decision.accepted is False
    assert "no phone or whatsapp in snippet" in decision.reasons


def test_evaluate_result_rejects_cis_phone_with_default_whitelist() -> None:
    """CIS phone is hard-blocked under the default whitelist."""
    contacts = Contacts(
        emails=("shop@brand.ru",),
        whatsapp_numbers=("+79161234567",),
    )
    decision = evaluate_result(
        title="Магазин",
        snippet="Продаём iPhone, wa.me/79161234567",
        link="https://www.instagram.com/p/abcdef/",
        contacts=contacts,
    )
    assert decision.accepted is False
    assert any("country blocked" in r for r in decision.reasons)
    assert decision.country == "RU"


def test_evaluate_result_blocks_cis_even_if_explicitly_whitelisted() -> None:
    """CIS countries are hard-blocked regardless of the user's whitelist."""
    contacts = Contacts(
        emails=("shop@brand.ru",),
        whatsapp_numbers=("+79161234567",),
    )
    decision = evaluate_result(
        title="Магазин",
        snippet="Продаём iPhone, wa.me/79161234567",
        link="https://www.instagram.com/p/abcdef/",
        contacts=contacts,
        allowed_countries=("RU", "BY", "KZ", "US", "AE"),
    )
    assert decision.accepted is False
    assert any("country blocked" in r for r in decision.reasons)
    assert decision.country == "RU"


def test_evaluate_result_blocks_cis_phone_with_all_codes_whitelisted() -> None:
    """Even passing every selectable code as the whitelist must not accept CIS."""
    contacts = Contacts(
        emails=("shop@brand.ru",),
        whatsapp_numbers=("+79161234567",),
    )
    decision = evaluate_result(
        title="Магазин",
        snippet="Продаём iPhone, wa.me/79161234567",
        link="https://www.instagram.com/p/abcdef/",
        contacts=contacts,
        allowed_countries=all_country_codes(),
    )
    assert decision.accepted is False
    assert decision.country == "RU"


def test_evaluate_result_blocks_cyrillic_only_low_confidence_hit() -> None:
    """Cyrillic-dominant snippet without a high-confidence country is rejected.

    The filter requires email + phone, but here the only phone is fed as a
    raw E.164 string without classifier-friendly metadata: extractors keep
    the number while the classifier still falls back to the cyrillic-only
    branch (because the snippet body itself stays cyrillic-dominant).
    """
    contacts = Contacts(
        emails=("shop@example.io",),
        whatsapp_numbers=(),
        # A satellite-style number that parses as +870 with no region.
        phones=("+870773000000",),
    )
    decision = evaluate_result(
        title="продаю iphone недорого",
        snippet="пишите в директ почту shop@example.io вотсап",
        link="https://www.instagram.com/p/abcdef/",
        contacts=contacts,
    )
    assert decision.accepted is False
    assert any("blocked country" in r or "country not allowed" in r for r in decision.reasons)


def test_evaluate_result_rejects_non_instagram_link() -> None:
    contacts = Contacts(
        emails=("shop@brand.io",),
        whatsapp_numbers=("+15551234567",),
    )
    decision = evaluate_result(
        title="Brand",
        snippet="snippet",
        link="https://example.com/abc",
        contacts=contacts,
    )
    assert decision.accepted is False
    assert "not an instagram.com link" in decision.reasons


def test_evaluate_result_rejects_when_country_not_in_custom_whitelist() -> None:
    """A US phone is rejected when only AE / SA are allowed."""
    contacts = Contacts(
        emails=("shop@brand.io",),
        whatsapp_numbers=("+15551234567",),
    )
    decision = evaluate_result(
        title="Brand",
        snippet="WhatsApp wa.me/15551234567 email shop@brand.io",
        link="https://www.instagram.com/p/abcdef/",
        contacts=contacts,
        allowed_countries=("AE", "SA"),
    )
    assert decision.accepted is False
    assert decision.country == "US"


def test_evaluate_result_us_phone_accepted_in_default_whitelist() -> None:
    """+1 phones map to US which is in the default whitelist."""
    contacts = Contacts(
        emails=("shop@brand.io",),
        whatsapp_numbers=(),
        phones=("+15551234567",),
    )
    decision = evaluate_result(
        title="brand",
        snippet="email shop@brand.io / call +1 555 123 4567",
        link="https://www.instagram.com/p/abcdef/",
        contacts=contacts,
    )
    assert decision.accepted is True
    assert decision.country == "US"


def test_default_whitelist_excludes_cis_block() -> None:
    """Sanity: the default whitelist must not contain Russia / CIS."""
    codes = set(default_country_codes())
    assert "RU" not in codes
    assert "BY" not in codes
    assert "KZ" not in codes
    assert "US" in codes
    assert "AE" in codes
