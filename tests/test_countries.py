"""Tests for the country registry and the classifier."""

from __future__ import annotations

from instagram_dork_bot.countries import (
    BLOCKED_COUNTRY_CODES,
    COUNTRIES,
    GROUPS,
    all_country_codes,
    classify,
    default_country_codes,
    get_country,
    normalise_codes,
    selectable_countries,
)


def test_registry_has_unique_iso_codes() -> None:
    codes = [c.code for c in COUNTRIES]
    assert len(codes) == len(set(codes)), "duplicate country codes in registry"


def test_default_excludes_cis() -> None:
    default = set(default_country_codes())
    for code in BLOCKED_COUNTRY_CODES:
        assert code not in default
    # Some non-CIS codes must still be present.
    assert "US" in default
    assert "AE" in default
    assert "DE" in default


def test_groups_no_longer_expose_cis() -> None:
    """The CIS preset has been removed from the picker UI."""
    assert all(g.code != "cis" for g in GROUPS)
    for group in GROUPS:
        for member in group.members:
            assert member not in BLOCKED_COUNTRY_CODES


def test_selectable_countries_excludes_blocked() -> None:
    codes = {c.code for c in selectable_countries()}
    assert codes.isdisjoint(BLOCKED_COUNTRY_CODES)
    # Sanity: a few well-known non-CIS countries are still present.
    assert {"US", "DE", "AE"}.issubset(codes)


def test_all_country_codes_matches_selectable_countries() -> None:
    assert set(all_country_codes()) == {c.code for c in selectable_countries()}
    assert set(all_country_codes()).isdisjoint(BLOCKED_COUNTRY_CODES)


def test_classifier_still_recognises_cis_codes() -> None:
    """CIS codes stay in :data:`COUNTRIES` so the classifier can detect them."""
    registry = {c.code for c in COUNTRIES}
    assert BLOCKED_COUNTRY_CODES.issubset(registry)


def test_get_country_case_insensitive() -> None:
    assert get_country("us") is not None
    assert get_country("US").code == "US"
    assert get_country("zz") is None


def test_normalise_codes_drops_unknown_and_dedupes() -> None:
    out = normalise_codes(("us", "US", "ZZ", "ae", "AE", "de"))
    assert out == tuple(
        c
        for c in (
            "US",
            "DE",
            "AE",
        )
        if c in (out)
    )
    # Order preserved by registry order: DE comes before AE.
    assert "US" in out and "DE" in out and "AE" in out
    assert "ZZ" not in out
    assert len(out) == 3


def test_normalise_codes_strips_blocked_countries() -> None:
    """Even if the DB contains a blocked code, normalise_codes drops it."""
    out = normalise_codes(("US", "RU", "BY", "DE"))
    assert set(out) == {"US", "DE"}
    assert all(code not in BLOCKED_COUNTRY_CODES for code in out)


def test_classify_by_phone_us() -> None:
    res = classify("Some english copy", phones=("+15551234567",))
    assert res.primary == "US"
    assert res.confidence == "high"


def test_classify_by_phone_russia() -> None:
    res = classify("English copy", phones=("+79161234567",))
    assert res.primary == "RU"


def test_classify_by_phone_uk() -> None:
    res = classify("brand store", phones=("+447700900123",))
    assert res.primary == "GB"


def test_classify_by_phone_uae() -> None:
    res = classify("contact us", phones=("+971501234567",))
    assert res.primary == "AE"


def test_classify_by_keyword_dubai() -> None:
    res = classify("Premium boutique in Dubai for luxury watches", phones=())
    assert res.primary == "AE"
    assert res.confidence == "high"


def test_classify_by_keyword_moscow() -> None:
    res = classify("Brand new shop in Moscow", phones=())
    assert res.primary == "RU"


def test_classify_cyrillic_only_yields_low_confidence_cis() -> None:
    """Cyrillic-dominant text without a city/phone yields CIS candidates."""
    res = classify("продаю iphone недорого пишите в директ", phones=())
    assert res.primary is None
    assert res.confidence == "low"
    assert "RU" in res.candidates
    assert "BY" in res.candidates


def test_classify_unknown_returns_none() -> None:
    res = classify("anonymous brand", phones=())
    assert res.primary is None
    assert res.confidence == "none"
    assert res.candidates == frozenset()
