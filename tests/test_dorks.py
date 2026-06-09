"""Tests for Google dork query construction."""

from __future__ import annotations

import pytest

from instagram_dork_bot.dorks import build_dorks


def test_build_dorks_includes_site_and_keyword() -> None:
    dorks = build_dorks("iphone 14")
    assert dorks
    for d in dorks:
        assert "site:instagram.com" in d.query
        assert '"iphone 14"' in d.query


def test_build_dorks_per_host_wa_me_dorks_present() -> None:
    """One dork per email host forces ``wa.me`` next to the keyword."""
    dorks = build_dorks("sofa")
    labels = {d.label for d in dorks}
    assert any(label.startswith("wa.me+") for label in labels)
    # Each email host produces a dork.
    for host in ("gmail.com", "yahoo.com", "hotmail.com", "outlook.com"):
        assert any(host in label for label in labels)


def test_build_dorks_includes_wide_net_dork() -> None:
    """A single wide-net dork ORs every contact token for high recall."""
    dorks = build_dorks("camera")
    labels = {d.label for d in dorks}
    assert "wide+contact" in labels


def test_build_dorks_includes_phone_dork() -> None:
    """Plain-phone dork covers listings that don't mention WhatsApp."""
    dorks = build_dorks("iphone")
    labels = {d.label for d in dorks}
    assert "phone+broad" in labels


def test_build_dorks_country_variant_present_when_countries_default() -> None:
    dorks = build_dorks("camera")
    assert any(d.label.startswith("country") for d in dorks)


def test_build_dorks_no_country_variant_when_empty() -> None:
    dorks = build_dorks("camera", countries=[])
    assert not any(d.label.startswith("country") for d in dorks)


def test_build_dorks_rejects_blank_keyword() -> None:
    with pytest.raises(ValueError):
        build_dorks("   ")


def test_build_dorks_query_has_whatsapp_token() -> None:
    dorks = build_dorks("watch")
    assert any("wa.me" in d.query.lower() for d in dorks)


def test_build_dorks_user_country_codes_override_default() -> None:
    """The user-supplied country list is used in the country dork."""
    dorks = build_dorks("iphone", countries=("Germany", "Berlin"))
    country_dorks = [d for d in dorks if d.label.startswith("country")]
    assert country_dorks
    assert any("Germany" in d.query for d in country_dorks)
