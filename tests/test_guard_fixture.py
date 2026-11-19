"""The Worker refuses the same lines as Python, with the same messages: Blockether/vis#368.

``tests/fixtures/guard.json`` holds each case below with the result of ``guard.py``.
``cloudflare/test/guard.test.js`` checks the same file against ``guard.js``. After a
change to ``guard.py`` or to the cases, write the file again with
``vis-agent python tests/test_guard_fixture.py``.

A text is a string or a list of parts. The fake secrets are lists, so the file holds
no value that a secret scanner would report. A case with ``limit`` first goes
through ``check_line``, like a line that ``remote.answer`` gets.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from vis_optmem.guard import SECRETS, check_line, problem

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "guard.json"
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def fake(prefix, size, alphabet=LETTERS + "0123456789"):
    """``prefix`` and a random-looking value as two parts, the same on every run."""
    chance = random.Random(f"{prefix}{size}")
    return [prefix, "".join(chance.choice(alphabet) for _ in range(size))]


def case(text, what="memory", language="", limit=None):
    data = {"text": text, "what": what, "language": language}
    if limit is not None:
        data["limit"] = limit
    return data


SECRET_TEXTS = [
    ["key: -----BEGIN RSA ", "PRIVATE KEY----- MIIEow"],
    ["-----BEGIN OPENSSH ", "PRIVATE KEY-----"],
    ["session ", *fake("eyJ", 20), ".", *fake("eyJ", 30), ".", *fake("", 25)],
    ["aws id ", *fake("AK" + "IA", 16, LETTERS[:26] + "0123456789")],
    ["push with ", *fake("gh" + "p_", 36)],
    ["github ", *fake("github" + "_pat_", 60)],
    fake("gl" + "pat-", 20),
    fake("xo" + "xb-", 30),
    ["https://hooks.slack", ".com/services/", *fake("T", 8), "/", *fake("B", 24)],
    fake("AI" + "za", 35),
    fake("ya" + "29.", 40),
    fake("GOC" + "SPX-", 28),
    fake("sk-" + "ant-api03-", 80),
    fake("sk-" + "proj-", 48),
    ["old key ", *fake("sk-", 48)],
    fake("sk" + "_live_", 24),
    fake("rk" + "_test_", 24),
    fake("np" + "m_", 36),
    fake("h" + "f_", 34, LETTERS),
    ["curl -H 'Authorization: Bearer ", *fake("", 32), "'"],
    ["Authorization: Basic ", "dXNlcjpYazlwTG0yUXpS"],
    ["AUTHORIZATION: BASIC ", "dXNlcjpYazlwTG0yUXpS"],
    ["git clone https://", *fake("", 32), "@github.com/org/repo.git"],
    ["DATABASE_URL=postgres://app:", "Xk9pLm2Q@db.internal:5432/app"],
    ["password=", "Sup3rS3cr3t!"],
    ["aws_secret_access_key=", *fake("", 40)],
    ["GH_TOKEN: ", *fake("", 24)],
    ['{"password": "', "Xk9pLm2QzR", '"}'],
    ["export API_KEY='", *fake("", 32), "'"],
    ["client_secret => ", *fake("", 20)],
    # Invisible characters and other forms must not hide a token.
    ["deploy with gh\u200b", *fake("p_", 36)],
    ["deploy with ", *fake("\uff47\uff48\uff50_", 36)],
]

SAFE_TEXTS = [
    "VIS-346 vis staging @7abdac4: the deploy works again after the cache fix.",
    "PROJ-123: prod gateway at gateway.example.com uses port 8443.",
    "Use Bearer tokens in the Authorization header for the HTTP API.",
    "Bearer abc and Basic auth are words here.",
    "The deploy token is in the DEPLOY_TOKEN variable, read as ${DEPLOY_TOKEN}.",
    "password=${DB_PASSWORD} in compose.yml, set from the vault.",
    "password=<your password> and token={{ secret }} are templates.",
    "password=changeme and token=redacted are placeholders.",
    "password=******** is masked in the log.",
    "api_key=os.environ['OPENAI_API_KEY'] in settings.py.",
    "Connect with postgres://localhost:5432/app, no password locally.",
    "postgres://user:<password>@host/db is the URL format.",
    "postgres://user:password@host/db and redis://u:$REDIS_PASS@host are examples.",
    "scikit-learn is imported as sklearn, see the sk-learn docs.",
    "max_tokens: 4096 is the default output limit.",
    "Commit 9b39d851f81af9d5d126265a530e6974a871416a added the cache.",
    "Session 81fce447-fc35-42ab-90c7-d3cd0dd0b1fd ran the end-to-end checks.",
    "AWS keys start with AKIA; rotate them every 90 days.",
    "token: required in the request body.",
    "Passwords live in 1Password; never paste them into a chat.",
    "Basic auth is off on staging; clone with https://git@github.com/org/repo.git.",
    "The bot pushes as ssh://deploy-bot-account-01@host.",
    "Zażółć gęślą jaźń: hasło=Wiosna jest w sejfie.",
]

PROFANE_TEXTS = [
    "what the fuck is this build",
    "MOTHERFUCKER of a bug",
    "the f*cking test fails",
    "this is bullshit",
    "oh sh!t, the cache",
    "the bitches of the old API",
    "a wanker wrote this",
    "kurwa, the cache is broken",
    "spierdalaj from main",
    "zajebisty wynik",
    "to jest chujowe",
    "huj z tym",
    "kutasy z działu",
    "ta cipa",
    "dziwka",
    "ku\u00adrwa with a soft hyphen",
    "\uff46\uff55\uff43\uff4b in full width",
    "b\u0131tch with a dotless i",
]

CLEAN_TEXTS = [
    "Scunthorpe, the Wankel engine, Hujiang and a classic assumption are fine.",
    "Use only AES-GCM cipher suites; skurcz means cramp in Polish.",
    "The cockpit, the Dickens novel and the Assam tea are fine.",
    "Cipher, cipka-free code and the city of Hujiang.",
    "Sussex, Essex and Middlesex are counties.",
]

LANGUAGE_CASES = [
    # (text, memory language)
    ("To jest bardzo ważna informacja o tym, jak działa nasz system.", "en"),
    ("Deploy idzie przez staging, potem prod.", "en"),
    ("Wdrożenie działa po naprawie pamięci podręcznej.", "en"),
    ("The deploy works again after the cache fix on staging.", "pl"),
    ("The deploy works again after the cache fix on staging.", "en"),
    ("Wdrożenie działa ponownie po naprawie cache na stagingu.", "pl"),
    ("Das ist der neue Server, und er läuft nicht mit der alten Version.", "en"),
    ("Das ist der neue Server, und er läuft nicht mit der alten Version.", "de"),
    ("The server runs with the old version of the API.", "de"),
    ("Le serveur est dans la salle avec les autres machines.", "en"),
    ("Le serveur est dans la salle avec les autres machines.", "fr"),
    ("El servidor está en la sala con las otras máquinas también.", "en"),
    ("El servidor está en la sala con las otras máquinas también.", "es"),
    ("¿Dónde está el servidor de producción ahora?", "es"),
    ("Il server è nella sala con le altre macchine, non qui.", "en"),
    ("O servidor não está na sala com as outras máquinas.", "en"),
    ("Het is een server van de klant en niet van ons.", "en"),
    # Names, technical words and short lines say nothing about the language.
    ("Jan Kowalski, Łódź, Gdańsk, Kraków.", "en"),
    ("Paweł Żółkiewski owns the Łódź gateway.", "en"),
    ("PROJ-12 ąćęłńśźż-cache v2.1 @7abdac4.", "en"),
    ("łódź gdańsk kraków", "en"),
    ("Würzburg München Köln Düsseldorf Straße.", "en"),
    ("deploy\u00a0staging\u00a0prod\u00a0cache works", "en"),
    ("To jest\u00a0test\u00a0który\u00a0działa.", "en"),
    ("The deploy works.", "PL"),
    (" en ", "  en "),
]


def all_cases():
    """Every case, in the order of the fixture."""
    cases = [
        case(text) for text in SECRET_TEXTS + SAFE_TEXTS + PROFANE_TEXTS + CLEAN_TEXTS
    ]
    cases += [case(text, "summary") for text in (SECRET_TEXTS[4], PROFANE_TEXTS[0])]
    cases += [case(text, language=language) for text, language in LANGUAGE_CASES]
    cases += [
        case("Zostaw cache, on działa po restarcie serwera.", "summary", "en"),
        # A search text is checked only for its language.
        case("gdzie jest ten serwer i jak go uruchomić", "search text", "en"),
        case("where is the staging server", "search text", "en"),
        case(SECRET_TEXTS[4], "search text", "en"),
        case("kurwa cache", "search text"),
        case("The deploy works.", language="xx"),
        case("The deploy works.", language="Klingon"),
        case("It's fine.", language="it's"),
        # Lines that remote.answer gets go through check_line first.
        case("  The deploy works.  ", limit=40),
        case("Zażółć gęślą jaźń", limit=17),
        case("Zażółć gęślą jaźń", limit=26),
        case("x" * 41, "summary", limit=40),
        case("   ", limit=40),
        case("one\ntwo\r\nthree\rfour", limit=40),
        case("one\u2028two", limit=40),
        case("one\x1ctwo\x85three", limit=40),
        case("one\vtwo", limit=40),
        case(["kurwa", " cache"], limit=40),
        case(SECRET_TEXTS[4], limit=340),
        case("a\udc80b", limit=40),
    ]
    return cases


def joined(text):
    return text if isinstance(text, str) else "".join(text)


def verdict(data):
    """The refusal of ``remote.answer`` for one case, or None."""
    text = joined(data["text"])
    try:
        if "limit" in data:
            text = check_line(text, data["limit"], what=data["what"])
        return problem(text, data["what"], data["language"])
    except ValueError as error:
        return str(error)


def write():
    """Write the fixture with the Python results."""
    cases = [{**data, "problem": verdict(data)} for data in all_cases()]
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(cases, ensure_ascii=True, indent=1) + "\n"
    FIXTURE.write_text(text, encoding="utf-8")


def load():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


CASES = all_cases()


@pytest.mark.parametrize(
    "index", range(len(CASES)), ids=lambda i: joined(CASES[i]["text"])[:40]
)
def test_each_fixture_case_has_the_python_result(index):
    assert verdict(CASES[index]) == load()[index]["problem"]


def test_the_fixture_has_every_case():
    assert [
        {k: v for k, v in data.items() if k != "problem"} for data in load()
    ] == CASES


def test_the_fixture_names_every_kind_of_secret():
    found = "\n".join(data["problem"] or "" for data in load())
    for kind, _ in SECRETS:
        assert f"seems to hold {kind}." in found, kind


if __name__ == "__main__":
    write()
