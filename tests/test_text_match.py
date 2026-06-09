"""Tests for the keyword-in-text relevance helpers."""

from __future__ import annotations

from instagram_dork_bot.text_match import keyword_in_text, tokens_of


def test_tokens_of_basic() -> None:
    assert tokens_of("iphone 14 pro") == ["iphone", "14", "pro"]


def test_tokens_of_strips_punctuation() -> None:
    assert tokens_of("iPhone-14,Pro!") == ["iphone", "14", "pro"]


def test_tokens_of_drops_short_tokens() -> None:
    """Single-character tokens are noise; skip them."""
    assert tokens_of("a 14 pro") == ["14", "pro"]


def test_keyword_in_text_case_insensitive() -> None:
    assert keyword_in_text("iphone", "iPhone 14 for sale") is True


def test_keyword_in_text_substring_match() -> None:
    assert keyword_in_text("iphone 14", "Selling iPhone 14 Pro Max") is True


def test_keyword_in_text_partial_token_still_works() -> None:
    """Substring containment means 'pro' matches 'process' too — that's
    intentional (we want recall), the rest of the keyword tokens do the
    narrowing."""
    assert keyword_in_text("macbook", "MacBook Air M2") is True


def test_keyword_in_text_missing_token_rejects() -> None:
    assert keyword_in_text("iphone 14", "iPhone 13 in Berlin") is False


def test_keyword_in_text_unrelated_rejects() -> None:
    assert keyword_in_text("iphone", "Renting a tractor in Munich") is False


def test_keyword_in_text_empty_keyword_returns_true() -> None:
    """An empty keyword never blocks anything."""
    assert keyword_in_text("", "anything goes here") is True


def test_keyword_in_text_russian() -> None:
    assert keyword_in_text("айфон", "Продаю Айфон 14 в Москве") is True
    assert keyword_in_text("айфон", "Продаю Самсунг") is False
