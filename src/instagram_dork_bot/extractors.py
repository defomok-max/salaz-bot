"""Extract email addresses, WhatsApp numbers and regular phone numbers."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

import phonenumbers

# Email regex. Intentionally pragmatic, not RFC-perfect.
EMAIL_RE = re.compile(
    # The lookbehind/lookahead exclude email-glue characters (alphanumerics,
    # underscore, plus, minus). We deliberately don't include ``.`` so that
    # sentence-ending punctuation ("contact me@x.com.") and period-separated
    # emails ("a@x.com.b@x.com") still match cleanly.
    r"(?<![A-Za-z0-9_+-])"
    r"[A-Za-z0-9._%+\-]{1,64}"
    r"@"
    r"[A-Za-z0-9.\-]{1,253}"
    r"\.[A-Za-z]{2,24}"
    r"(?![A-Za-z0-9_+-])"
)

# wa.me / api.whatsapp.com / whatsapp.com/send links.
WA_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?"
    r"(?:wa\.me/|api\.whatsapp\.com/send\?phone=|whatsapp\.com/send\?phone=)"
    r"(\+?\d[\d\-\s]{6,})",
    re.IGNORECASE,
)

# Heuristic match for "WhatsApp" / "WA" / "WA:" near a phone number on the
# same line. Matches forms like:
#   WhatsApp: +1 (555) 123-4567
#   WA +44 7700 900123
#   📱 WhatsApp 1-555-123-4567
WA_INLINE_RE = re.compile(
    r"(?:whats[\s\-]*app|\bwa\b)\s*[:\-]?\s*"
    r"(\+?\d[\d\s().\-]{6,}\d)",
    re.IGNORECASE,
)

# Standalone phone-shaped substring. Used to extract regular (non-WA) phones.
# We require a leading ``+`` OR at least one separator (space, hyphen, dot,
# parens) — this stops the regex from eating bare long digit runs such as
# tracking numbers ("1Z999AA10123456784") and dates ("20240115143000").
# The post-filter below additionally enforces a digit count in [10, 15]
# (E.164 length range) so ISBNs, IBANs, and stray numeric IDs are dropped.
ANY_PHONE_RE = re.compile(
    r"(?:\+\d[\d\s().\-]{7,}\d)"
    r"|"
    r"(?:\(\d{2,4}\)[\s.\-]?\d[\d\s().\-]{5,}\d)"
    r"|"
    r"(?:\d{1,4}[\s.\-]\d[\d\s().\-]{5,}\d)"
)

# Telegram contact handles. Useful as an additional contact signal — the
# bot stores them as plain text but they are not phones. Telegram
# usernames are 5-32 chars; the regex below matches 5-32 chars. The
# trailing negative lookahead enforces an alphanumeric boundary so we
# don't match a 32-char prefix of a longer token.
TELEGRAM_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:t\.me/|@)([A-Za-z][A-Za-z0-9_]{3,30}[A-Za-z0-9])(?![A-Za-z0-9_])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Contacts:
    """Contacts extracted from a piece of text."""

    emails: tuple[str, ...] = field(default_factory=tuple)
    whatsapp_numbers: tuple[str, ...] = field(default_factory=tuple)
    phones: tuple[str, ...] = field(default_factory=tuple)
    telegram_handles: tuple[str, ...] = field(default_factory=tuple)

    @property
    def has_email(self) -> bool:
        return bool(self.emails)

    @property
    def has_whatsapp(self) -> bool:
        return bool(self.whatsapp_numbers)

    @property
    def has_phone(self) -> bool:
        return bool(self.phones)

    @property
    def has_telegram(self) -> bool:
        return bool(self.telegram_handles)

    @property
    def has_any_phone(self) -> bool:
        return self.has_whatsapp or self.has_phone

    @property
    def has_any_contact(self) -> bool:
        return self.has_email or self.has_any_phone or self.has_telegram


def _normalise_phone(raw: str) -> str | None:
    """Try to normalise a raw phone string to E.164.

    Returns ``None`` if the number can't be parsed at all, or if the raw
    string's digit count is outside the E.164 length window (so we don't
    accept tracking numbers / IBANs / dates that happen to look phone-like).
    """
    cleaned = raw.strip().lstrip(".").rstrip(".")
    cleaned = re.sub(r"\.{2,}$", "", cleaned)

    # Cheap gate: count digits and reject if outside plausible range.
    digit_count = sum(c.isdigit() for c in cleaned)
    if digit_count < 7 or digit_count > 15:
        return None

    candidates: list[str] = [cleaned]
    if not cleaned.startswith("+"):
        candidates.append("+" + re.sub(r"\D", "", cleaned))

    for candidate in candidates:
        try:
            parsed = phonenumbers.parse(candidate, None)
        except phonenumbers.NumberParseException:
            continue
        # Use ``is_possible_number`` only (not ``is_valid_number``) so that
        # less-common but well-formed numbers and reserved test prefixes
        # (e.g. US 555, UK 7700) are still accepted.
        if phonenumbers.is_possible_number(parsed):
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    return None


def extract_emails(text: str) -> list[str]:
    """Return de-duplicated, lower-cased emails found in ``text``."""
    seen: list[str] = []
    for match in EMAIL_RE.findall(text or ""):
        email = match.lower()
        if email not in seen:
            seen.append(email)
    return seen


def extract_whatsapp_numbers(text: str) -> list[str]:
    """Return de-duplicated WhatsApp-capable phone numbers in E.164 format.

    A number is considered WhatsApp-capable if it appears either inside a
    ``wa.me`` / ``api.whatsapp.com`` URL, or on the same line as the words
    "WhatsApp" / "WA".
    """
    if not text:
        return []

    found: list[str] = []
    seen: set[str] = set()

    for match in WA_URL_RE.findall(text):
        normalised = _normalise_phone(match)
        if normalised and normalised not in seen:
            seen.add(normalised)
            found.append(normalised)

    for match in WA_INLINE_RE.findall(text):
        normalised = _normalise_phone(match)
        if normalised and normalised not in seen:
            seen.add(normalised)
            found.append(normalised)

    return found


def extract_phones(text: str, *, exclude: Iterable[str] = ()) -> list[str]:
    """Return all phone-number-shaped substrings normalised to E.164.

    ``exclude`` lets the caller drop already-known numbers (e.g. WhatsApp
    numbers we've already classified) so that the same number doesn't
    appear in both the WhatsApp and phone buckets.
    """
    if not text:
        return []
    excluded = set(exclude)
    seen: set[str] = set()
    out: list[str] = []
    for match in ANY_PHONE_RE.findall(text):
        normalised = _normalise_phone(match)
        if not normalised or normalised in seen or normalised in excluded:
            continue
        seen.add(normalised)
        out.append(normalised)
    return out


def extract_telegram_handles(text: str) -> list[str]:
    """Return de-duplicated Telegram handles (``@name``) found in ``text``.

    Both ``@handle`` and ``t.me/handle`` forms are recognised. Handles are
    returned without the leading ``@`` / domain, lowercased. Telegram
    usernames are 5-32 chars, so anything outside that window is dropped.
    """
    if not text:
        return []
    seen: list[str] = []
    for match in TELEGRAM_RE.findall(text):
        handle = match.lower()
        if len(handle) < 5 or len(handle) > 32:
            continue
        if handle not in seen:
            seen.append(handle)
    return seen


def extract_contacts(*texts: str) -> Contacts:
    """Extract emails + WhatsApp numbers + plain phones + Telegram from text fragments."""
    combined = "\n".join(t for t in texts if t)
    emails = extract_emails(combined)
    whatsapp = extract_whatsapp_numbers(combined)
    phones = extract_phones(combined, exclude=whatsapp)
    telegram = extract_telegram_handles(combined)
    return Contacts(
        emails=tuple(emails),
        whatsapp_numbers=tuple(whatsapp),
        phones=tuple(phones),
        telegram_handles=tuple(telegram),
    )


def merge_contacts(contacts: Iterable[Contacts]) -> Contacts:
    """Union the fields of multiple ``Contacts`` instances."""
    emails: list[str] = []
    whatsapp: list[str] = []
    phones: list[str] = []
    telegram: list[str] = []
    for c in contacts:
        for e in c.emails:
            if e not in emails:
                emails.append(e)
        for w in c.whatsapp_numbers:
            if w not in whatsapp:
                whatsapp.append(w)
        for p in c.phones:
            if p not in phones and p not in whatsapp:
                phones.append(p)
        for h in c.telegram_handles:
            if h not in telegram:
                telegram.append(h)
    return Contacts(
        emails=tuple(emails),
        whatsapp_numbers=tuple(whatsapp),
        phones=tuple(phones),
        telegram_handles=tuple(telegram),
    )
