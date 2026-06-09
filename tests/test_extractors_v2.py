"""Tests for the new extractors behaviour: telegram handles + tighter phone regex."""

from __future__ import annotations

from instagram_dork_bot.extractors import (
    extract_contacts,
    extract_phones,
    extract_telegram_handles,
    extract_whatsapp_numbers,
)


def test_extract_telegram_with_at_prefix() -> None:
    assert extract_telegram_handles("DM me @brand_official for details") == ["brand_official"]


def test_extract_telegram_with_t_me_link() -> None:
    out = extract_telegram_handles("Ping us at t.me/sell_iphone for a quote")
    assert out == ["sell_iphone"]


def test_extract_telegram_dedup() -> None:
    text = "DM @brand or visit t.me/brand"
    out = extract_telegram_handles(text)
    assert out == ["brand"]


def test_extract_telegram_ignores_short_handles() -> None:
    """Handles < 4 chars are not real Telegram usernames."""
    out = extract_telegram_handles("ping @ab — too short")
    assert out == []


def test_extract_telegram_ignores_long_random_words() -> None:
    """Handles > 32 chars are not real Telegram usernames."""
    out = extract_telegram_handles("DM @abcdefghijklmnopqrstuvwxyzabcdefghi for the deal")
    assert out == []


def test_phones_ignore_dates() -> None:
    """A short date in the middle of text should not be parsed as a phone."""
    text = "Posted 2024-01-15 about my listing"
    assert extract_phones(text) == []


def test_phones_ignore_tracking_numbers() -> None:
    text = "Tracking 1Z999AA10123456784 should arrive tomorrow"
    assert extract_phones(text) == []


def test_phones_ignore_ibans() -> None:
    text = "Wire to GB29NWBK60161331926819 and we'll ship"
    assert extract_phones(text) == []


def test_phones_still_catch_e164() -> None:
    text = "Call +1 555 123 4567 for offers"
    assert extract_phones(text) == ["+15551234567"]


def test_phones_catch_parenthesised_format() -> None:
    text = "Phone (555) 123-4567 for orders"
    out = extract_phones(text)
    # We don't try to guess the country code from a bare parenthesised
    # area code; the result is +<digits> as a string in E.164 form.
    assert out and out[0].startswith("+")
    assert out[0].lstrip("+").isdigit()


def test_extract_contacts_includes_telegram() -> None:
    text = "Email me at a@b.com or @brand — also +1 555 123 4567"
    c = extract_contacts(text)
    assert c.emails == ("a@b.com",)
    assert c.has_telegram
    assert "brand" in c.telegram_handles
    assert c.has_any_phone


def test_extract_whatsapp_inline_ignores_unrelated_inline_keywords() -> None:
    """``whatsapp`` keyword must be present for an inline match."""
    text = "Call 1-555-123-4567 for details"
    assert extract_whatsapp_numbers(text) == []
