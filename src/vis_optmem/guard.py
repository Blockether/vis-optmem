"""Checks that keep secrets, profanity and other languages out of a memory.

A memory never changes and nothing removes it, so ``Memo`` checks every line
before a store gets it, for every store and both scopes. ``remote.answer`` does
the same checks in a service, so a client cannot go around them. The secret rules are
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


LANGUAGES: dict[str, tuple[str, frozenset[str], str]] = {
    code: (name, frozenset(words.split()), letters)
    for code, name, words, letters in (
        (
            "en",
            "English",
            "the of and to in is are was were be been for on with as by at from that this "
            "these those it its not but or if then than which who what when where how there "
            "their they we you he she will would can could should must has have had do does "
            "did an into about after before only also just more most other some such each",
            "",
        ),
        (
            "pl",
            "Polish",
            "się nie jest są być był była było że na w z ze do od po przez przy przed dla jak "
            "ale lub albo oraz czy gdy jeśli już tylko też jego jej ich ten ta te tego tej "
            "tym który która które może można trzeba ma mają będzie bo więc pod nad między "
            "tak bez jako",
            "ąćęłńśźż",
        ),
        (
            "de",
            "German",
            "der die das und ist sind nicht mit von zu den dem des ein eine einen auf für "
            "sich auch als wie aber oder wenn dass nur noch bei nach aus wird werden kann "
            "haben hat",
            "äöüß",
        ),
        (
            "fr",
            "French",
            "le la les des une est sont pas pour avec dans sur que qui par ce cette mais ou "
            "ne plus aux du au être avoir fait sans",
            "çèêàùâîôûë",
        ),
        (
            "es",
            "Spanish",
            "el los las del una es son por para con que pero como más sin sobre también "
            "este esta muy hay ser",
            "ñ¿¡",
        ),
        (
            "it",
            "Italian",
            "il gli della delle sono non con che una più anche questo questa come nel alla dei",
            "",
        ),
        (
            "pt",
            "Portuguese",
            "os das dos uma não para com que mais mas como também este esta são pelo pela",
            "ãõ",
        ),
        (
            "nl",
            "Dutch",
            "het een en van zijn niet met voor op dat maar ook als bij naar wordt",
            "ĳ",
        ),
    )
}
"""Each language: (name, common short words, letters that only it uses here)."""

LANGUAGE_MIN_WORDS = 4
"""Fewer small words than this say too little about the language: the line passes."""


class UnsafeMemory(ValueError):
    """A line holds a secret or profanity, or is in another language. The message never repeats it."""


def check_language(language: str) -> str:
    """Return the language code ``language`` in lowercase, "" for none, or raise ValueError."""
    code = language.strip().lower()
    if code and code not in LANGUAGES:
        raise ValueError(
            f"Unknown memory language {language!r}. Use one of: {', '.join(LANGUAGES)}."
        )
    return code


def language_scores(text: str) -> dict[str, int]:
    """For each language, how many small words of ``text`` are its common words or letters.

    Only words in small letters count: a capitalized word is a name, and a word with
    digits or signs is technical.
    """
    words = [
        token
        for token in (raw.strip(".,;:!?()[]{}\"'`") for raw in visible(text).split())
        if token.isalpha() and token.islower()
    ]
    scores = dict.fromkeys(LANGUAGES, 0)
    if len(words) < LANGUAGE_MIN_WORDS:
        return scores
    for word in words:
        for code, (_, common, letters) in LANGUAGES.items():
            if word in common or (letters and any(c in letters for c in word)):
                scores[code] += 1
    return scores


def other_language(text: str, language: str) -> str | None:
    """The name of the language of ``text`` when it is clearly not ``language``, else None."""
    code = check_language(language)
    if not code:
        return None
    scores = language_scores(text)
    best = max((c for c in LANGUAGES if c != code), key=lambda c: scores[c])
    # Most lines of a language have its small words, so none of them is a sign too.
    if scores[best] > scores[code] and (scores[best] >= 2 or scores[code] == 0):
        return LANGUAGES[best][0]
    return None


def check_line(text: str, limit: int, *, what: str) -> str:
    """Return ``text`` without outer whitespace, or raise ValueError if it is not one short line."""
    if not isinstance(text, str):
        raise TypeError(f"The {what} must be a string.")
    line = text.strip()
    if not line:
        raise ValueError(f"The {what} is empty. Write one line of text.")
    if "\n" in line or "\r" in line:
        lines = len(line.splitlines())
        raise ValueError(
            f"The {what} has {lines} lines. Write one line: merge them, or save them one by one."
        )
    size = len(line.encode("utf-8"))
    if size > limit:
        raise ValueError(
            f"The {what} is too long: {size} bytes, and the limit is {limit}. "
            "Letters with accents use 2 bytes. Make it shorter."
        )
    return line


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


def problem(text: str, what: str, language: str = "") -> str | None:
    """Why ``text`` must not be saved, without the unsafe part, or None when it is safe.

    ``language`` is the code of the only language that the memory keeps, like "en",
    or "" for any language. A search text is checked only for its language.
    """
    other = other_language(text, language)
    if other is not None:
        wanted = LANGUAGES[check_language(language)][0]
        verb = "searched" if what == "search text" else "saved"
        return (
            f"Not {verb}: the {what} seems to be in {other}, and this memory keeps "
            f"{wanted} only. Write it again in {wanted}."
        )
    if what == "search text":
        return None
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


def check_safe(text: str, *, what: str, language: str = "") -> str:
    """Return ``text``, or raise UnsafeMemory if it holds a secret or profanity or is not in ``language``."""
    reason = problem(text, what, language)
    if reason is not None:
        raise UnsafeMemory(reason)
    return text
