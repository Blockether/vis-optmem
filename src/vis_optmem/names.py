"""The names in a memory: what two memories must share to say the same fact.

Embeddings cannot separate "port 5432" from "port 5433", or staging from
production. So a repeat check also compares names. ``cloudflare/src/protocol.js``
has the same rules; ``tests/fixtures/names.json`` keeps both the same.
"""

from __future__ import annotations

import re
import unicodedata

ENVIRONMENTS = frozenset(
    "dev development staging stage prod production preprod qa uat sandbox".split()
)
"""Environment words. Two facts about different environments are different facts."""

PROPER_STEM = 5
"""Letters of a proper name that must match, without accents: Kraków, Krakow and Krakowie are one name."""

_LEADING = re.compile(r"^[(\"'`\[{]+")
_TRAILING = re.compile(r"[.,;:!?)\"'`\]}]+$")
_TECHNICAL = re.compile(r"\d|[\\/_.:@=$~#]|^-")
_SENTENCE_END = re.compile(r"[.!?][\"')\]]*$")


def _is_technical(token: str) -> bool:
    """Digits, paths, file names, flags, camelCase or ALL-CAPS."""
    if _TECHNICAL.search(token):
        return True
    if any(a.islower() and b.isupper() for a, b in zip(token, token[1:])):
        return True
    return len(token) >= 2 and all(c.isalpha() and c.isupper() for c in token)


def _is_proper(token: str) -> bool:
    """A capitalized word: one capital letter, then only small letters."""
    return (
        len(token) >= 2
        and token[0].isupper()
        and all(c.isalpha() and c.islower() for c in token[1:])
    )


def _tokens(text: str) -> list[tuple[str, bool]]:
    """The words of ``text`` without outer punctuation, each with True at a sentence start."""
    found = []
    sentence_start = True
    for raw in text.split():
        token = _TRAILING.sub("", _LEADING.sub("", raw))
        if token:
            found.append((token, sentence_start))
        sentence_start = _SENTENCE_END.search(raw) is not None
    return found


def _stem(token: str) -> str:
    """``name:`` and the first PROPER_STEM letters of ``token``, in small letters, without accents."""
    plain = unicodedata.normalize("NFKD", token.lower())
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    return f"name:{plain[:PROPER_STEM]}"


def names(text: str) -> set[str]:
    """The names in ``text``: technical tokens, environment words and proper names.

    A technical token is kept in lowercase, an environment word as ``env:<word>``
    and a capitalized word that does not start a sentence as ``name:<first 5 letters>``.
    """
    found = set()
    for token, sentence_start in _tokens(text):
        lower = token.lower()
        if lower in ENVIRONMENTS:
            found.add(f"env:{lower}")
        elif _is_technical(token):
            found.add(lower)
        elif not sentence_start and _is_proper(token):
            found.add(_stem(token))
    return found


def _stems(text: str) -> set[str]:
    return {_stem(token) for token, _ in _tokens(text)}


def same_names(a: str, b: str) -> bool:
    """True when ``a`` and ``b`` have the same names.

    Technical tokens and environment words must be the same. A proper name of one
    text must be a word of the other, in any case: "Poetry" matches "poetry".
    """
    left, right = names(a), names(b)
    if {n for n in left if not n.startswith("name:")} != {
        n for n in right if not n.startswith("name:")
    }:
        return False
    proper = {n for n in left | right if n.startswith("name:")}
    return proper <= _stems(a) and proper <= _stems(b)
