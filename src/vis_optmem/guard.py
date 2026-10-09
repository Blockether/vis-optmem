"""Checks that keep secrets and profanity out of a memory.

A memory never changes and nothing removes it, so ``Memo`` checks every line
before a store gets it, for every store and both scopes. The secret rules are
written for this module in the style of gitleaks: known token formats, keys,
passwords in URLs and random values after a word like ``password=``. A refusal
names the kind of problem and never repeats the value.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

SECRETS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (kind, re.compile(pattern))
    for kind, pattern in (
        ("a private key", r"-----BEGIN[A-Z ]*PRIVATE KEY"),
        ("a JSON Web Token", r"\beyJ[\w-]{8,}\.eyJ[\w-]{8,}\.[\w-]{8,}"),
        ("an AWS access key", r"\b(?:AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{16}\b"),
        ("a GitHub token", r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_\w{22,})"),
        ("a GitLab token", r"\bgl(?:pat|dt|ptt|rt|cbt|soat)-[\w-]{20,}"),
        (
            "a Slack token",
            r"\bxox[abposre]-[A-Za-z0-9-]{10,}"
            r"|hooks\.slack\.com/(?:services|workflows)/[\w/-]{20,}",
        ),
        ("a Google API key", r"\bAIza[\w-]{35}"),
        ("a Google OAuth secret", r"\bya29\.[\w-]{20,}|\bGOCSPX-[\w-]{20,}"),
        ("an Anthropic key", r"\bsk-ant-[a-z]+\d*-[\w-]{20,}"),
        ("an OpenAI key", r"\bsk-(?:proj-|svcacct-|admin-)?[\w-]*?[A-Za-z0-9]{20,}"),
        ("a Stripe key", r"\b[rs]k_(?:live|test)_[A-Za-z0-9]{16,}"),
        ("an npm token", r"\bnpm_[A-Za-z0-9]{36}\b"),
        ("a Hugging Face token", r"\bhf_[A-Za-z]{34}\b"),
    )
)
"""Known secret formats, as (kind, pattern). The first match names the kind."""

BEARER = re.compile(r"(?i)\b(bearer|basic)\s+([\w.~+/=-]+)")
URL_PASSWORD = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@]+:([^\s/@]+)@", re.IGNORECASE)
URL_USER = re.compile(r"\b[a-z][a-z0-9+.-]*://([^\s/:@]{20,})@", re.IGNORECASE)
ASSIGNED = re.compile(
    r"(?i)\b[\w.-]*?(?:password|passwd|pwd|passphrase|secret|token|api[_-]?key"
    r"|access[_-]?key|private[_-]?key|credentials?)"
    r"[\"']?\s*(?::|=>?)\s*[\"']?([^\s\"'`,;]+)"
)
"""A value after a word like ``password=`` or ``token:``."""

PLACEHOLDERS = frozenset(
    "password passwd pass secret token key apikey changeme example redacted "
    "hidden none null empty xxx".split()
)

PROFANITY = re.compile(
    r"(?i)\w*fuck\w*|\bf[*#@]+ck\w*|\b(?:bull|dip|horse)?shit\w*|\bsh[*#@!]+t\b"
    r"|\bcunts?\b|\bbitch\w*|\bassholes?\b|\bdickheads?\b|\bwank(?:er|ers|ing)\b"
    r"|\btwats?\b|\bbastards?\b"
    r"|\w*(?:kurw|pierdol|pierdal|jeba[cćlnł]|jebi|jebn|zjeb|pizd)\w*"
    r"|\b(?:ch|h)uj(?!i)\w*|\bkutas\w*|\bcip(?:a|e|y|ą|ę|ie)\b|\bdziwk\w*"
)
"""English and Polish swear words. Stems that only swear words use match inside
a word, because Polish adds prefixes. The others match at the start of a word or
as whole words, so names like Scunthorpe, Wankel and Hujiang stay allowed."""


class UnsafeMemory(ValueError):
    """A line holds a secret or profanity. The message never repeats it."""


def entropy(value: str) -> float:
    """Shannon entropy of ``value`` in bits for each character."""
    counts = Counter(value)
    return -sum(n / len(value) * math.log2(n / len(value)) for n in counts.values())


def is_random(value: str) -> bool:
    """True when ``value`` looks like a generated secret, not a word or a reference."""
    value = value.strip(".")
    if len(value) < 8 or value.lower() in PLACEHOLDERS:
        return False
    # $VAR, ${VAR}, <token>, {{ secret }}, os.environ["X"], %s and *** name a value.
    if re.search(r"[$<>{}()\[\]%]", value) or re.fullmatch(r"[*x.]+", value):
        return False
    classes = sum(
        bool(re.search(pattern, value))
        for pattern in ("[a-z]", "[A-Z]", "[0-9]", r"[^\w]|_")
    )
    # A short value cannot reach a high entropy, so three kinds of characters also count.
    return classes >= 3 or (classes >= 2 and entropy(value) >= 3.0)


def visible(text: str) -> str:
    """``text`` in NFKC form without invisible characters, so they cannot hide a word."""
    text = unicodedata.normalize("NFKC", text)
    return "".join(char for char in text if unicodedata.category(char) != "Cf")


def secret_kind(text: str) -> str | None:
    """The kind of secret that ``text`` seems to hold, like "a GitHub token", or None."""
    text = visible(text)
    for kind, pattern in SECRETS:
        if pattern.search(text):
            return kind
    for match in URL_PASSWORD.finditer(text):
        value = match.group(1)
        if value.lower() not in PLACEHOLDERS and not re.search(r"[$<>{}%*]", value):
            return "a password in a URL"
    for match in URL_USER.finditer(text):
        # A user name like deploy-bot-01 is not random enough to be a token.
        if is_random(match.group(1)) and entropy(match.group(1)) >= 3.8:
            return "a token in a URL"
    for match in BEARER.finditer(text):
        if is_random(match.group(2)):
            return f"a {match.group(1).capitalize()} authorization value"
    if any(is_random(match.group(1)) for match in ASSIGNED.finditer(text)):
        return "a password or token value"
    return None


def has_profanity(text: str) -> bool:
    """True when ``text`` has a swear word."""
    return PROFANITY.search(visible(text)) is not None


def problem(text: str, what: str) -> str | None:
    """Why ``text`` must not be saved, without the unsafe part, or None when it is safe."""
    kind = secret_kind(text)
    if kind is not None:
        return (
            f"Not saved: the {what} seems to hold {kind}. Memories never change, so a "
            "secret would stay there for ever. Save the fact without the value, like "
            '"the deploy token is in the DEPLOY_TOKEN variable".'
        )
    if has_profanity(text):
        return f"Not saved: the {what} has profanity. Write it again in professional language."
    return None


def check_safe(text: str, *, what: str) -> str:
    """Return ``text``, or raise UnsafeMemory if it holds a secret or profanity."""
    reason = problem(text, what)
    if reason is not None:
        raise UnsafeMemory(reason)
    return text
