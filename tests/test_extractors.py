"""Tests for the contact extractors."""

from __future__ import annotations

from instagram_dork_bot.extractors import (
    Contacts,
    extract_contacts,
    extract_emails,
    extract_phones,
    extract_whatsapp_numbers,
    merge_contacts,
)


def test_extract_emails_basic() -> None:
    text = "Contact us at sales@example.com or sales@example.com again"
    assert extract_emails(text) == ["sales@example.com"]


def test_extract_emails_multiple_distinct() -> None:
    text = "DM sales@brand.io / orders@brand.io / SUPPORT@Brand.IO"
    out = extract_emails(text)
    assert "sales@brand.io" in out
    assert "orders@brand.io" in out
    assert "support@brand.io" in out


def test_extract_emails_ignores_punctuation_neighbours() -> None:
    text = "(hello@there.com)"
    assert extract_emails(text) == ["hello@there.com"]


def test_extract_whatsapp_via_wa_me() -> None:
    text = "Order via https://wa.me/15551234567 today"
    assert extract_whatsapp_numbers(text) == ["+15551234567"]


def test_extract_whatsapp_via_api_url() -> None:
    text = "https://api.whatsapp.com/send?phone=447700900123"
    assert extract_whatsapp_numbers(text) == ["+447700900123"]


def test_extract_whatsapp_inline() -> None:
    text = "WhatsApp: +1 (555) 123-4567 or call us"
    assert extract_whatsapp_numbers(text) == ["+15551234567"]


def test_extract_whatsapp_ignores_bare_phone() -> None:
    """A phone number with NO whatsapp signal must NOT appear in WA bucket."""
    text = "Call now at +1 555 123 4567 for offers"
    assert extract_whatsapp_numbers(text) == []


def test_extract_phones_picks_up_bare_phone() -> None:
    """The new ``phones`` bucket includes plain (non-WhatsApp) numbers."""
    text = "Call now at +1 555 123 4567 for offers"
    assert extract_phones(text) == ["+15551234567"]


def test_extract_phones_excludes_whatsapp_numbers() -> None:
    """Numbers already classified as WhatsApp must not appear in ``phones``."""
    text = "WhatsApp wa.me/15551234567 or call +44 7700 900123"
    wa = extract_whatsapp_numbers(text)
    phones = extract_phones(text, exclude=wa)
    assert "+15551234567" not in phones
    assert "+447700900123" in phones


def test_extract_contacts_combined() -> None:
    text = "Selling iPhones! Email: shop@example.com. WhatsApp +442079460000 -- order today."
    contacts = extract_contacts(text)
    assert contacts.emails == ("shop@example.com",)
    assert contacts.whatsapp_numbers == ("+442079460000",)
    assert contacts.has_email and contacts.has_whatsapp


def test_extract_contacts_email_plus_plain_phone() -> None:
    text = "Email orders@brand.io. Phone: +1 (555) 123 4567"
    contacts = extract_contacts(text)
    assert contacts.has_email is True
    assert contacts.has_whatsapp is False
    assert contacts.has_phone is True
    assert contacts.has_any_phone is True
    assert "+15551234567" in contacts.phones


def test_extract_contacts_does_not_double_count_phone() -> None:
    text = "WhatsApp wa.me/15551234567 -- email shop@brand.io"
    contacts = extract_contacts(text)
    assert "+15551234567" in contacts.whatsapp_numbers
    assert "+15551234567" not in contacts.phones


def test_extract_contacts_multiple_text_fragments() -> None:
    title = "Shop @ DM us shop@brand.io"
    snippet = "wa.me/15551234567 - free shipping"
    contacts = extract_contacts(title, snippet)
    assert contacts.has_email
    assert contacts.has_whatsapp


def test_merge_contacts_unions_unique() -> None:
    a = Contacts(emails=("a@x.com",), whatsapp_numbers=("+15551234567",))
    b = Contacts(emails=("a@x.com", "b@x.com"), whatsapp_numbers=("+447700900123",))
    merged = merge_contacts([a, b])
    assert set(merged.emails) == {"a@x.com", "b@x.com"}
    assert set(merged.whatsapp_numbers) == {"+15551234567", "+447700900123"}


def test_merge_contacts_phones_dedup_against_whatsapp() -> None:
    a = Contacts(
        emails=("a@x.com",),
        whatsapp_numbers=("+15551234567",),
        phones=("+15551234567",),
    )
    b = Contacts(
        emails=("a@x.com",),
        whatsapp_numbers=(),
        phones=("+447700900123",),
    )
    merged = merge_contacts([a, b])
    assert "+15551234567" not in merged.phones
    assert "+447700900123" in merged.phones


def test_email_excludes_neighbouring_punctuation() -> None:
    """Adjacent punctuation must not be glued onto the captured email."""
    text = "Email: <shop@brand.io>, more text"
    out = extract_emails(text)
    assert "shop@brand.io" in out
    for e in out:
        assert not e.endswith(",")
        assert not e.endswith(">")
