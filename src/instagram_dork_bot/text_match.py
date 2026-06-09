"""Keyword-in-text matching helpers used by the relevance filter.

The bot searches via Google and then must decide whether a given search
hit is actually about the user's keyword. The Google dork already forces
the keyword to appear on the indexed page (``"iphone 14"``), but the
*snippet* the user sees may quote a different region of the page — so we
end up showing a tractor-rental post in response to an iPhone query.

These helpers provide cheap, deterministic checks:

* :func:`keyword_in_text` — every token in the keyword appears in the text
  (case-insensitive, after stripping non-word characters). Allows token
  length >= 2 to skip noise like single characters.
* :func:`tokens_of` — split a keyword into normalised tokens (used by
  tests and by the filter for debug output).
"""

from __future__ import annotations

import re

_TOKEN_SPLIT_RE = re.compile(r"[\W_]+", re.UNICODE)


def tokens_of(keyword: str) -> list[str]:
    """Return the normalised tokens of ``keyword`` (lowercased, len >= 2)."""
    return [tok for tok in _TOKEN_SPLIT_RE.split(keyword.lower()) if len(tok) >= 2]


def keyword_in_text(keyword: str, text: str) -> bool:
    """True iff every token in ``keyword`` is found (substring) in ``text``.

    The match is case-insensitive and operates on substrings, so an
    ``"iphone 14"`` query matches a snippet containing ``"iPhone 14 pro"``
    but rejects a snippet about ``"iPhone 13"`` (the ``14`` token is
    missing) and about ``"Tractor"`` (no token in common).
    """
    toks = tokens_of(keyword)
    if not toks:
        return True
    haystack = text.lower()
    return all(tok in haystack for tok in toks)
