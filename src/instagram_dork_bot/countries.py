"""Country registry and per-result country classifier.

The bot supports a fixed set of countries that the user can include / exclude
on a per-search basis. Each :class:`Country` knows its phone-region codes
(consumed by ``phonenumbers``), short list of city / region keywords, and
optional script hint (Cyrillic / Arabic / etc.).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

import phonenumbers

# A single character class — matches one Cyrillic codepoint.
_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF\u0500-\u052F]")


class Script(StrEnum):
    """Writing system hints that strongly correlate with a country group."""

    CYRILLIC = "cyrillic"


@dataclass(frozen=True)
class Country:
    """Static metadata about a supported country."""

    code: str  # ISO 3166-1 alpha-2
    name_ru: str
    flag: str
    phone_regions: tuple[str, ...]  # ISO codes accepted by phonenumbers
    keywords: tuple[str, ...] = ()
    script: Script | None = None


@dataclass(frozen=True)
class CountryGroup:
    """A named preset that picks a subset of countries with one click."""

    code: str
    name_ru: str
    members: tuple[str, ...]


# Note: keep the list small enough that the picker keyboard is usable
# inside a Telegram message. The CIS bloc is kept inside the registry only
# so that the classifier can still recognise CIS-attributed search hits and
# *reject* them — those countries are NOT exposed to the user via the picker
# and are explicitly listed in :data:`BLOCKED_COUNTRY_CODES`.
COUNTRIES: tuple[Country, ...] = (
    # CIS bloc — recognised by the classifier, blocked from search results.
    Country(
        "RU",
        "Россия",
        "🇷🇺",
        ("RU",),
        (
            "russia",
            "moscow",
            "saint petersburg",
            "st. petersburg",
            "st petersburg",
            "russian federation",
        ),
        Script.CYRILLIC,
    ),
    Country("BY", "Беларусь", "🇧🇾", ("BY",), ("belarus", "minsk"), Script.CYRILLIC),
    Country(
        "KZ",
        "Казахстан",
        "🇰🇿",
        ("KZ",),
        ("kazakhstan", "almaty", "astana", "nur-sultan"),
        Script.CYRILLIC,
    ),
    Country(
        "UA",
        "Украина",
        "🇺🇦",
        ("UA",),
        ("ukraine", "kyiv", "kiev", "lviv", "odesa", "odessa"),
        Script.CYRILLIC,
    ),
    Country("UZ", "Узбекистан", "🇺🇿", ("UZ",), ("uzbekistan", "tashkent")),
    Country("AM", "Армения", "🇦🇲", ("AM",), ("armenia", "yerevan")),
    Country("AZ", "Азербайджан", "🇦🇿", ("AZ",), ("azerbaijan", "baku")),
    Country("GE", "Грузия", "🇬🇪", ("GE",), ("tbilisi", "georgia tbilisi", "batumi")),
    Country("MD", "Молдова", "🇲🇩", ("MD",), ("moldova", "chisinau")),
    Country("KG", "Кыргызстан", "🇰🇬", ("KG",), ("kyrgyzstan", "bishkek")),
    Country("TJ", "Таджикистан", "🇹🇯", ("TJ",), ("tajikistan", "dushanbe")),
    Country("TM", "Туркменистан", "🇹🇲", ("TM",), ("turkmenistan", "ashgabat")),
    # Anglosphere.
    Country(
        "US",
        "США",
        "🇺🇸",
        ("US",),
        ("usa", "united states", "new york", "los angeles", "miami", "california"),
    ),
    Country("CA", "Канада", "🇨🇦", ("CA",), ("canada", "toronto", "vancouver", "montreal")),
    Country(
        "GB", "Великобритания", "🇬🇧", ("GB",), ("united kingdom", "uk ", "london", "manchester")
    ),
    Country("AU", "Австралия", "🇦🇺", ("AU",), ("australia", "sydney", "melbourne")),
    Country("NZ", "Новая Зеландия", "🇳🇿", ("NZ",), ("new zealand", "auckland")),
    Country("IE", "Ирландия", "🇮🇪", ("IE",), ("ireland", "dublin")),
    # Continental Europe.
    Country("DE", "Германия", "🇩🇪", ("DE",), ("germany", "berlin", "munich", "hamburg")),
    Country("FR", "Франция", "🇫🇷", ("FR",), ("france", "paris", "marseille", "lyon")),
    Country("IT", "Италия", "🇮🇹", ("IT",), ("italy", "rome", "milan", "naples")),
    Country("ES", "Испания", "🇪🇸", ("ES",), ("spain", "madrid", "barcelona")),
    Country("NL", "Нидерланды", "🇳🇱", ("NL",), ("netherlands", "amsterdam", "rotterdam")),
    Country("PL", "Польша", "🇵🇱", ("PL",), ("poland", "warsaw", "krakow")),
    Country("PT", "Португалия", "🇵🇹", ("PT",), ("portugal", "lisbon", "porto")),
    Country("CH", "Швейцария", "🇨🇭", ("CH",), ("switzerland", "zurich", "geneva")),
    Country("AT", "Австрия", "🇦🇹", ("AT",), ("austria", "vienna")),
    Country("SE", "Швеция", "🇸🇪", ("SE",), ("sweden", "stockholm")),
    Country("CZ", "Чехия", "🇨🇿", ("CZ",), ("czech", "prague")),
    # Middle East.
    Country(
        "AE", "ОАЭ", "🇦🇪", ("AE",), ("united arab emirates", "uae", "dubai", "abu dhabi", "sharjah")
    ),
    Country("SA", "Саудовская Аравия", "🇸🇦", ("SA",), ("saudi arabia", "riyadh", "jeddah")),
    Country("QA", "Катар", "🇶🇦", ("QA",), ("qatar", "doha")),
    Country("KW", "Кувейт", "🇰🇼", ("KW",), ("kuwait",)),
    Country("BH", "Бахрейн", "🇧🇭", ("BH",), ("bahrain", "manama")),
    Country("OM", "Оман", "🇴🇲", ("OM",), ("oman", "muscat")),
    Country("TR", "Турция", "🇹🇷", ("TR",), ("turkey", "istanbul", "ankara", "antalya")),
    Country("IL", "Израиль", "🇮🇱", ("IL",), ("israel", "tel aviv", "jerusalem")),
    # Asia / South-East Asia.
    Country("IN", "Индия", "🇮🇳", ("IN",), ("india", "mumbai", "delhi", "bangalore")),
    Country("PK", "Пакистан", "🇵🇰", ("PK",), ("pakistan", "karachi", "lahore")),
    Country("ID", "Индонезия", "🇮🇩", ("ID",), ("indonesia", "jakarta", "bali")),
    Country("TH", "Таиланд", "🇹🇭", ("TH",), ("thailand", "bangkok", "phuket")),
    Country("MY", "Малайзия", "🇲🇾", ("MY",), ("malaysia", "kuala lumpur")),
    Country("SG", "Сингапур", "🇸🇬", ("SG",), ("singapore",)),
    Country("PH", "Филиппины", "🇵🇭", ("PH",), ("philippines", "manila", "cebu")),
    # Latin America.
    Country("BR", "Бразилия", "🇧🇷", ("BR",), ("brazil", "sao paulo", "rio de janeiro")),
    Country("MX", "Мексика", "🇲🇽", ("MX",), ("mexico", "mexico city", "guadalajara")),
    Country("AR", "Аргентина", "🇦🇷", ("AR",), ("argentina", "buenos aires")),
    Country("CL", "Чили", "🇨🇱", ("CL",), ("chile", "santiago")),
    Country("CO", "Колумбия", "🇨🇴", ("CO",), ("colombia", "bogota", "medellin")),
    # Africa.
    Country("ZA", "ЮАР", "🇿🇦", ("ZA",), ("south africa", "johannesburg", "cape town")),
    Country("NG", "Нигерия", "🇳🇬", ("NG",), ("nigeria", "lagos", "abuja")),
    Country("EG", "Египет", "🇪🇬", ("EG",), ("egypt", "cairo")),
    Country("MA", "Марокко", "🇲🇦", ("MA",), ("morocco", "casablanca", "rabat")),
)


# ISO codes that are NEVER returned by the bot, regardless of any user
# whitelist. The classifier still recognises these so that we can detect
# and reject CIS-attributed search hits.
BLOCKED_COUNTRY_CODES: frozenset[str] = frozenset(
    {
        "RU",
        "BY",
        "KZ",
        "UA",
        "UZ",
        "AM",
        "AZ",
        "GE",
        "MD",
        "KG",
        "TJ",
        "TM",
    }
)


# Built-in presets the user can apply with one click. ``ALL`` (everything)
# and ``NONE`` (clear) are handled inline by the keyboard, not as group
# objects. The CIS preset has been removed: those countries cannot be
# selected, period.
GROUPS: tuple[CountryGroup, ...] = (
    CountryGroup(
        "west",
        "🌍 Запад",
        (
            "US",
            "CA",
            "GB",
            "AU",
            "NZ",
            "IE",
            "DE",
            "FR",
            "IT",
            "ES",
            "NL",
            "PL",
            "PT",
            "CH",
            "AT",
            "SE",
            "CZ",
        ),
    ),
    CountryGroup("gulf", "🛢 Залив", ("AE", "SA", "QA", "KW", "BH", "OM")),
    CountryGroup(
        "asia",
        "🌏 Азия / SEA",
        (
            "IN",
            "PK",
            "ID",
            "TH",
            "MY",
            "SG",
            "PH",
            "TR",
            "IL",
        ),
    ),
    CountryGroup("latam", "🌎 LatAm", ("BR", "MX", "AR", "CL", "CO")),
    CountryGroup("africa", "🌍 Африка", ("ZA", "NG", "EG", "MA")),
)


def selectable_countries() -> tuple[Country, ...]:
    """Countries that the user is allowed to pick in the settings UI.

    The CIS bloc is hidden from the picker (and from every other
    user-facing list) — it can never appear in search results.
    """
    return tuple(c for c in COUNTRIES if c.code not in BLOCKED_COUNTRY_CODES)


# The default whitelist used when a user has not configured one yet:
# every selectable country (CIS is excluded by definition).
def default_country_codes() -> tuple[str, ...]:
    return tuple(c.code for c in selectable_countries())


def all_country_codes() -> tuple[str, ...]:
    """All user-selectable country codes (CIS bloc excluded)."""
    return tuple(c.code for c in selectable_countries())


def get_country(code: str) -> Country | None:
    """Lookup a country by ISO code (case-insensitive)."""
    upper = code.upper()
    for c in COUNTRIES:
        if c.code == upper:
            return c
    return None


def normalise_codes(codes: Iterable[str]) -> tuple[str, ...]:
    """Filter input down to selectable countries, deduplicated, in registry order.

    Codes belonging to :data:`BLOCKED_COUNTRY_CODES` are silently dropped so
    that the CIS bloc can never end up in a user's whitelist, even via a
    direct DB write.
    """
    wanted = {code.upper() for code in codes}
    return tuple(
        c.code for c in COUNTRIES if c.code in wanted and c.code not in BLOCKED_COUNTRY_CODES
    )


def _group(code: str) -> CountryGroup:
    for g in GROUPS:
        if g.code == code:
            return g
    raise KeyError(code)


@dataclass(frozen=True)
class CountryClassification:
    """Result of trying to assign a country to a search hit."""

    primary: str | None
    candidates: frozenset[str] = field(default_factory=frozenset)
    confidence: str = "none"  # 'none' | 'low' | 'high'

    @property
    def known(self) -> bool:
        return self.primary is not None


def classify(
    *texts: str,
    phones: Sequence[str] = (),
) -> CountryClassification:
    """Best-effort country classification for a search hit.

    Heuristics, in priority order:

    1. Phone E.164 → ``phonenumbers`` region (high confidence).
    2. City / country keyword in the text (high confidence).
    3. Cyrillic-dominant text → CIS candidates without a definite country
       (low confidence; ``primary`` stays None but candidates set is non-empty).

    A return with ``primary is None`` means we don't know — the caller can
    decide whether to accept "unknown" hits or filter them out.
    """
    candidates: set[str] = set()
    primary: str | None = None

    # 1. Phone-derived.
    for phone in phones:
        for region in _phone_regions(phone):
            if get_country(region):
                candidates.add(region)
                primary = primary or region

    # 2. Keyword-derived.
    haystack = " ".join(t.lower() for t in texts if t)
    if haystack:
        for country in COUNTRIES:
            for kw in country.keywords:
                if kw and kw in haystack:
                    candidates.add(country.code)
                    primary = primary or country.code
                    break

    # 3. Script-derived (only used as a candidate-set, not a primary pick).
    # Cyrillic-dominant text strongly correlates with the CIS bloc — we
    # mark every CIS code as a candidate so the filter can drop the hit.
    if primary is None:
        for t in texts:
            if _is_cyrillic_dominant(t):
                candidates.update(BLOCKED_COUNTRY_CODES)
                break

    confidence = "high" if primary else ("low" if candidates else "none")
    return CountryClassification(primary, frozenset(candidates), confidence)


def _phone_regions(e164: str) -> tuple[str, ...]:
    try:
        parsed = phonenumbers.parse(e164, None)
    except phonenumbers.NumberParseException:
        return ()
    primary = phonenumbers.region_code_for_number(parsed)
    if primary:
        return (primary,)
    cc = parsed.country_code
    if cc is None:
        return ()
    fallback = phonenumbers.region_codes_for_country_code(cc) or ()
    return tuple(r for r in fallback if r and r != "ZZ")


def _is_cyrillic_dominant(text: str, *, threshold: float = 0.3) -> bool:
    if not text:
        return False
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 10:
        return False
    cyr = sum(1 for c in letters if _CYRILLIC_RE.match(c))
    return (cyr / len(letters)) >= threshold
