"""Secrets and profanity never reach a memory: Blockether/vis#346.

The fake secrets are built from parts, so this file holds no value that a secret
scanner would report.
"""

import random
from pathlib import Path

import pytest
from conftest import fill

from vis_optmem import UnsafeMemory
from vis_optmem import memo as memo_module
from vis_optmem.guard import has_profanity, secret_kind
from vis_optmem.memo import TEAM, Memo, load_store

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "sqlite_store.py"
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def fake(prefix, size, alphabet=LETTERS + "0123456789"):
    """A random-looking value after ``prefix``, the same on every run."""
    chance = random.Random(f"{prefix}{size}")
    return prefix + "".join(chance.choice(alphabet) for _ in range(size))


SECRETS = {
    "a private key": "-----BEGIN RSA " + "PRIVATE KEY----- MIIEow",
    "a JSON Web Token": "session "
    + ".".join([fake("eyJ", 20), fake("eyJ", 30), fake("", 25)]),
    "an AWS access key": "aws id " + fake("AK" + "IA", 16, LETTERS[:26] + "0123456789"),
    "a GitHub token": "push with " + fake("gh" + "p_", 36),
    "a GitLab token": fake("gl" + "pat-", 20),
    "a Slack token": fake("xo" + "xb-", 30),
    "a Google API key": fake("AI" + "za", 35),
    "a Google OAuth secret": fake("ya" + "29.", 40),
    "an OpenAI key": fake("sk-" + "proj-", 48),
    "an Anthropic key": fake("sk-" + "ant-api03-", 80),
    "a Stripe key": fake("sk_" + "live_", 24),
    "an npm token": fake("np" + "m_", 36),
    "a Hugging Face token": fake("h" + "f_", 34, LETTERS),
    "a Bearer authorization value": "curl -H 'Authorization: Bearer "
    + fake("", 32)
    + "'",
    "a Basic authorization value": "Authorization: Basic " + "dXNlcjpYazlwTG0yUXpS",
    "a token in a URL": "git clone https://"
    + fake("", 32)
    + "@github.com/org/repo.git",
    "a password in a URL": "DATABASE_URL=postgres://app:"
    + "Xk9pLm2Q@db.internal:5432/app",
    "a password or token value": "password=" + "Sup3rS3cr3t!",
}

MORE_SECRETS = [
    "key: -----BEGIN OPENSSH " + "PRIVATE KEY-----",
    "github " + fake("github" + "_pat_", 60),
    "https://hooks.slack.com/services/"
    + "/".join([fake("T", 8), fake("B", 8), fake("", 24)]),
    "old key " + fake("sk-", 48),
    "aws_secret_access_key=" + fake("", 40),
    "GH_TOKEN: " + fake("", 24),
    # Invisible characters must not hide a token.
    "deploy with gh\u200b" + fake("p_", 36),
    '{"password": "' + "Xk9pLm2QzR" + '"}',
    "export API_KEY='" + fake("", 32) + "'",
]

FACTS = [
    "VIS-346 vis staging @7abdac4: the deploy works again after the cache fix.",
    "PROJ-123: prod gateway at gateway.example.com uses port 8443.",
    "Use Bearer tokens in the Authorization header for the HTTP API.",
    "The deploy token is in the DEPLOY_TOKEN variable, read as ${DEPLOY_TOKEN}.",
    "password=${DB_PASSWORD} in compose.yml, set from the vault.",
    "api_key=os.environ['OPENAI_API_KEY'] in settings.py.",
    "Connect with postgres://localhost:5432/app, no password locally.",
    "postgres://user:<password>@host/db is the URL format.",
    "scikit-learn is imported as sklearn, see the sk-learn docs.",
    "max_tokens: 4096 is the default output limit.",
    "Commit 9b39d851f81af9d5d126265a530e6974a871416a added the cache.",
    "Session 81fce447-fc35-42ab-90c7-d3cd0dd0b1fd ran the end-to-end checks.",
    "AWS keys start with AKIA; rotate them every 90 days.",
    "token: required in the request body.",
    "Passwords live in 1Password; never paste them into a chat.",
    "Scunthorpe, the Wankel engine, Hujiang and a classic assumption are fine.",
    "Use only AES-GCM cipher suites; skurcz means cramp in Polish.",
    "Basic auth is off on staging; clone with https://git@github.com/org/repo.git.",
    "The bot pushes as ssh://deploy-bot-account-01@host.",
]

SWEARING = [
    "what the fuck is this build",
    "the f*cking test fails",
    "this is bullshit",
    "kurwa, the cache is broken",
    "spierdalaj from main",
    "zajebisty wynik",
    "to jest chujowe",
    "ku\u00adrwa with a soft hyphen",
]


@pytest.mark.parametrize("kind", SECRETS)
def test_each_kind_of_secret_is_named(kind):
    assert secret_kind(SECRETS[kind]) == kind


@pytest.mark.parametrize("line", MORE_SECRETS)
def test_more_secret_formats_are_found(line):
    assert secret_kind(line) is not None


@pytest.mark.parametrize("line", FACTS)
def test_ordinary_facts_are_neither_secrets_nor_swearing(line, memo):
    assert secret_kind(line) is None
    assert not has_profanity(line)
    assert memo.note(line).memory == line


@pytest.mark.parametrize("line", SWEARING)
def test_swear_words_in_english_and_polish_are_found(line):
    assert has_profanity(line)


def test_a_note_with_a_token_is_refused(memo):
    # Blockether/vis#346: the personal memory saved any secret that fit one line.
    token = fake("gh" + "p_", 36)
    with pytest.raises(UnsafeMemory, match="seems to hold a GitHub token") as refused:
        memo.note(f"Deploy with {token}", force=True)
    assert token not in str(refused.value)
    assert memo.wake().at == 0


def test_profanity_is_refused_without_repeating_it(memo):
    with pytest.raises(UnsafeMemory, match="professional language") as refused:
        memo.note("kurwa, the cache is broken")
    assert "kurwa" not in str(refused.value)
    assert memo.wake().at == 0


def test_a_summary_with_a_secret_or_profanity_is_refused(memo):
    fill(memo, 15)
    memo.note("the last memory of the block")
    request = memo.nap().request
    for summary in (SECRETS["a password in a URL"], "fuck the old summaries"):
        with pytest.raises(UnsafeMemory):
            memo.nap(request.block, summary)
    assert memo.nap().request.block == request.block
    assert memo.nap(request.block, "the first sixteen memories").saved == request.block


def test_the_team_memory_and_a_custom_store_refuse_them_too(tmp_path, monkeypatch):
    monkeypatch.setattr(memo_module, "_loaded", {})
    store = load_store(f"{EXAMPLE}:SqliteStore", {"path": str(tmp_path / "m.db")})
    tool = Memo(store=store, team_directory=tmp_path / "team")
    tool.init()
    tool.init(scope=TEAM)
    for scope in ("personal", TEAM):
        with pytest.raises(UnsafeMemory):
            tool.note(SECRETS["an OpenAI key"], scope=scope)
        with pytest.raises(UnsafeMemory):
            tool.note("the deploy is bullshit", scope=scope)
        assert tool.recall(".", scope=scope).total == 0


def test_an_import_with_one_secret_imports_nothing(memo, tmp_path):
    source = tmp_path / "old.txt"
    source.write_text(
        "2026-01-01 a normal fact\n2026-01-02 " + SECRETS["a Slack token"] + "\n",
        encoding="utf-8",
    )
    with pytest.raises(UnsafeMemory, match=r"^Line 2: .*a Slack token") as refused:
        memo.import_memories(str(source))
    assert SECRETS["a Slack token"] not in str(refused.value)
    assert memo.wake().at == 0
