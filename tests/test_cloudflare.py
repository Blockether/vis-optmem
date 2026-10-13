"""The Cloudflare Worker in cloudflare/ keeps a memory like the files do.

Start the Worker with a token and every memory name, then give its URL and token:

    cd cloudflare
    printf 'MEMORY_STORE_TOKEN=<token>\nMEMORIES=*\n' > .dev.vars
    npx wrangler dev
    MEMORY_SERVICE_URL=http://localhost:8787/ MEMORY_STORE_TOKEN=<token> \
      vis-agent python -m pytest tests/test_cloudflare.py -q

Without MEMORY_SERVICE_URL, the tests are skipped. The Worker embeds each memory
with Workers AI, so a run makes a few hundred small Workers AI calls.
"""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_custom_store import run

from vis_optmem.memo import TEAM, DuplicateMemory, Memo
from vis_optmem.remote import RemoteStore

# Read before conftest removes the memory settings of the process.
URL = os.environ.get("MEMORY_SERVICE_URL", "")
TOKEN = os.environ.get("MEMORY_STORE_TOKEN", "")

pytestmark = pytest.mark.skipif(not URL, reason="Set MEMORY_SERVICE_URL to a Worker.")


def store(name=None, token=TOKEN):
    """A new memory in the Worker, so that each test starts empty."""
    return RemoteStore(URL, name or f"test-{uuid.uuid4().hex}", token=token)


def test_the_worker_gives_the_same_memory_as_the_files(tmp_path):
    source = tmp_path / "old.txt"
    source.write_text("2999-01-01 first imported\n2999-01-02 second imported\n")
    files = run(Memo(tmp_path / "memory"), source)
    assert run(Memo(store=store()), source) == files


def test_the_worker_finds_repeats_by_meaning_and_keeps_near_misses(tmp_path):
    tool = Memo(tmp_path / "mine", team_store=store())
    tool.init()
    assert tool.init(scope=TEAM).is_new
    assert tool.note("Deploy with the blue-green script.", scope=TEAM).id == 0
    with pytest.raises(DuplicateMemory):
        tool.note("Always deploy using the blue green script.", scope=TEAM)
    assert tool.note("The staging database listens on port 5432.", scope=TEAM).id == 1
    assert tool.note("The staging database listens on port 5433.", scope=TEAM).id == 2
    assert (
        tool.note("The production database listens on port 5432.", scope=TEAM).id == 3
    )
    assert "#0 " in tool.recall(about="how do we ship a release", scope=TEAM).text
    assert "[team] #3 " in tool.recall(about="production database", scope="all").text


def test_concurrent_appends_get_dense_ids():
    memory = store()
    memory.create()

    def add(index):
        return memory.append(
            [("2026-10-12", f"Parallel fact {index} on host-{index}.")] * 3
        )

    with ThreadPoolExecutor(8) as pool:
        firsts = sorted(pool.map(add, range(8)))
    assert firsts == list(range(0, 24, 3))
    assert [entry.id for entry in memory.entries(0, 24)] == list(range(24))


def test_the_worker_refuses_a_wrong_token_and_a_bad_pattern():
    with pytest.raises(PermissionError, match=r"\$MEMORY_STORE_TOKEN"):
        store(token="wrong").exists()
    memory = store()
    memory.create()
    with pytest.raises(ValueError, match="not a valid regular expression"):
        memory.matches("(unclosed", 5)
